import asyncio
import logging
import time
from typing import Any

import ccxt

from definitions.constants import DEFAULT_RPC_TIMEOUT
from definitions.error_handler import ErrorHandler
from definitions.errors import CriticalError, RPCConfigError
from definitions.proxy_manager import ProxyManager
from definitions.rpc import is_port_open, rpc_call


class CCXTManager:
    def __init__(
        self,
        config_manager: Any,
        logger: logging.Logger | None = None,
        error_handler: ErrorHandler | None = None,
        proxy_manager: ProxyManager | None = None,
    ) -> None:
        self.cex_orderbook: dict[str, Any] | None = None
        self.cex_orderbook_timer: float | None = None
        self.config_manager = config_manager
        self.logger = logger or (
            self.config_manager.ccxt_log
            if hasattr(self.config_manager, "ccxt_log")
            else logging.getLogger("ccxt_manager")
        )
        self.error_handler = error_handler or ErrorHandler(
            config_manager, logger=self.logger
        )
        self.proxy_manager = proxy_manager or ProxyManager()

    def init_ccxt_instance(
        self,
        exchange: str,
        hostname: str | None = None,
        private_api: bool = False,
        debug_level: int = 1,
    ) -> Any:
        api_key: str | None = None
        api_secret: str | None = None
        if private_api:
            api_keys_data = (
                self.config_manager.config_loader.secrets_manager.get_api_keys()
            )
            for data in api_keys_data.get("api_info", []):
                if exchange.lower() in data.get("exchange", "").lower():
                    api_key = data.get("api_key")
                    api_secret = data.get("api_secret")
                    break
            if not api_key:
                self.logger.warning(
                    f"No API credentials found for {exchange} in environment variables"
                )

        if exchange in ccxt.exchanges:
            exchange_class = getattr(ccxt, exchange)
            if hostname:
                instance = exchange_class(
                    {
                        "apiKey": api_key,
                        "secret": api_secret,
                        "enableRateLimit": True,
                        "rateLimit": 1000,
                        "hostname": hostname,  # 'global.bittrex.com',
                    }
                )
            else:
                instance = exchange_class(
                    {
                        "apiKey": api_key,
                        "secret": api_secret,
                        "enableRateLimit": True,
                        "rateLimit": 1000,
                    }
                )
            done = False
            while not done:
                try:
                    # Run blocking load_markets in a thread pool executor
                    instance.load_markets()  # Directly call the blocking method
                except Exception as e:
                    self.error_handler.handle(
                        e,
                        context={"method": "init_ccxt_instance", "exchange": exchange},
                    )
                    # Continue retrying unless it's a critical error
                    if isinstance(e, CriticalError):
                        raise RPCConfigError(
                            "No valid Blocknet Core Config path found.",
                            context={"context": str(e)},
                        )
                else:
                    done = True
            return instance
        else:
            self.logger.error(f"Unsupported exchange: {exchange}")
            return None

    async def _ccxt_blocking_call_with_retry(
        self, func: Any, context: dict[str, Any], *args: Any
    ) -> Any:
        """Helper method to run a blocking CCXT function with retry and error handling.

        Args:
            func: The blocking function to call in a thread pool.
            context: The context for the error handler (dict). Will be updated with err_count.
            *args: Arguments to pass to the function.

        Returns:
            The result of the function, or None on unrecoverable failure.

        The loop will continue on transient errors until either the function succeeds or
        the error handler returns False.
        """
        err_count = 0
        loop = asyncio.get_running_loop()
        while True:
            try:
                # Run the blocking function in a thread pool and return the result
                return await loop.run_in_executor(None, func, *args)
            except Exception as error:
                err_count += 1
                context_with_err_count = {**context, "err_count": err_count}
                # The handler will convert the exception type appropriately.
                if not await self.error_handler.handle_async(
                    error, context=context_with_err_count
                ):
                    return None

    async def ccxt_call_fetch_order_book(
        self, ccxt_o: Any, symbol: str, limit: int = 25, ignore_timer: bool = False
    ) -> dict[str, Any] | None:
        update_cex_orderbook_timer_delay = 2
        if (
            ignore_timer
            or self.cex_orderbook_timer is None
            or time.time() - self.cex_orderbook_timer > update_cex_orderbook_timer_delay
        ):
            self.cex_orderbook = await self._fetch_order_book(ccxt_o, symbol, limit)
            self.cex_orderbook_timer = time.time()
        return self.cex_orderbook

    async def _fetch_order_book(
        self, ccxt_o: Any, symbol: str, limit: int
    ) -> dict[str, Any] | None:
        context = {"method": "_fetch_order_book", "symbol": symbol, "limit": limit}
        result = await self._ccxt_blocking_call_with_retry(
            ccxt_o.fetch_order_book, context, symbol, limit
        )
        if result is not None:
            self._debug_display("ccxt_call_fetch_order_book", [symbol, limit], result)
        return result

    async def ccxt_call_fetch_free_balance(self, ccxt_o: Any) -> dict[str, Any] | None:
        context = {"method": "ccxt_call_fetch_free_balance"}
        result = await self._ccxt_blocking_call_with_retry(
            ccxt_o.fetch_free_balance, context
        )
        if result is not None:
            self._debug_display("ccxt_call_fetch_free_balance", [], result)
        return result

    def _get_rpc_timeout(self, fallback: int = 60) -> int:
        try:
            cfg = getattr(self.config_manager, "config_xbridge", None)
            cfg_timeout = getattr(cfg, "rpc_timeout", None) if cfg else None
            if isinstance(cfg_timeout, (int, float)) and cfg_timeout > 0:
                return int(cfg_timeout)
        except Exception:
            pass
        return fallback if fallback else DEFAULT_RPC_TIMEOUT

    async def ccxt_call_fetch_tickers(
        self, ccxt_o: Any, symbols_list: list[str], proxy: bool = True
    ) -> dict[str, Any] | None:
        start = time.time()
        err_count = 0

        # Start proxy if needed before first attempt
        if proxy:
            await asyncio.get_running_loop().run_in_executor(
                None, self.proxy_manager.ensure_running
            )

        while True:
            try:
                used_proxy = False
                if proxy and is_port_open("127.0.0.1", self.proxy_manager.get_port()):
                    result = await rpc_call(
                        "ccxt_call_fetch_tickers",
                        tuple(symbols_list),
                        rpc_port=self.proxy_manager.get_port(),
                        debug=self.config_manager.config_ccxt.debug_level,
                        logger=self.config_manager.general_log,
                        timeout=self._get_rpc_timeout(fallback=60),
                    )
                    used_proxy = True
                else:
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None, ccxt_o.fetchTickers, symbols_list
                    )

                if result is not None:
                    stop = time.time()
                    self._debug_display(
                        "ccxt_call_fetch_tickers",
                        str(symbols_list) + " used_proxy? " + str(used_proxy),
                        result,
                        timer=stop - start,
                    )
                    return result
            except Exception as error:
                err_count += 1
                context = {
                    "method": "ccxt_call_fetch_tickers",
                    "symbols": symbols_list,
                    "proxy_used": proxy,
                    "err_count": err_count,
                }
                if not await self.error_handler.handle_async(error, context=context):
                    return None

    async def ccxt_call_fetch_ticker(
        self, ccxt_o: Any, symbol: str
    ) -> dict[str, Any] | None:
        context = {"method": "ccxt_call_fetch_ticker", "symbol": symbol}
        result = await self._ccxt_blocking_call_with_retry(
            ccxt_o.fetch_ticker, context, symbol
        )
        if result is not None:
            self._debug_display("ccxt_call_fetch_ticker", [symbol], result)
        return result

    def _debug_display(
        self, func: str, params: Any, result: Any, timer: float | None = None
    ) -> None:
        debug_level = self.config_manager.config_ccxt.debug_level
        if debug_level < 2:
            return

        timer = "" if timer is None else " exec_timer: " + str(round(timer, 2))

        # Level 2: Log method name only
        if debug_level == 2:
            msg = f"ccxt_rpc_call( {func[10::]} ){timer}"
            self.logger.info(msg)
        # Level 3: Log method and parameters
        elif debug_level >= 3:
            msg = f"ccxt_rpc_call( {func[10::]} {params} ){timer}"
            self.logger.info(msg)

        # Level 4: Also log the full result
        if debug_level >= 4:
            self.logger.debug(str(result))
