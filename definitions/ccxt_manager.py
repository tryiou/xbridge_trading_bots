import asyncio
import json
import logging
import threading
import time

import ccxt

from definitions.error_handler import ErrorHandler
from definitions.errors import RPCConfigError, CriticalError
from definitions.rpc import rpc_call, is_port_open
from proxy_ccxt import AsyncPriceService


class CCXTManager:
    # Class-level variables for shared proxy state
    _proxy_service_instance = None
    _proxy_service_thread = None
    _proxy_port = 2233
    _proxy_lock = threading.Lock()
    _proxy_ref_count = 0  # Track active strategies using proxy

    # Class-level logger for proxy events
    _proxy_logger = logging.getLogger('ccxt_manager.proxy')

    @classmethod
    def register_strategy(cls):
        """Call whenever a strategy starts"""
        with cls._proxy_lock:
            cls._proxy_ref_count += 1
            cls._proxy_logger.debug(
                f"Strategy registered. New refcount: {cls._proxy_ref_count} "
                f"(Thread: {getattr(cls._proxy_service_thread, 'name', 'None')})"
            )

    @classmethod
    def unregister_strategy(cls):
        """Call when strategy stops"""
        with cls._proxy_lock:
            if cls._proxy_ref_count > 0:
                cls._proxy_ref_count -= 1
                new_refcount = cls._proxy_ref_count
                cls._proxy_logger.debug(
                    f"Strategy unregistered. New refcount: {new_refcount} "
                    f"(Thread: {getattr(cls._proxy_service_thread, 'name', 'None')})"
                )
            else:
                cls._proxy_logger.warning(f"unregister_strategy called with refcount <= 0")
                new_refcount = 0

            # Trigger cleanup when refcount reaches zero
            if new_refcount == 0 and cls._proxy_service_thread:
                if cls._proxy_service_thread.is_alive():
                    cls._proxy_logger.debug("Scheduling proxy cleanup")
                    threading.Thread(target=cls._cleanup_proxy, name="ProxyCleanup").start()
                else:
                    cls._proxy_logger.debug("Proxy thread already dead - clearing state")
                    cls._proxy_service_thread = None
                    cls._proxy_service_instance = None

    def __init__(self, config_manager):
        self.cex_orderbook = None
        self.cex_orderbook_timer = None
        self.config_manager = config_manager  # Store ConfigManager reference
        # Instance doesn't need its own proxy_process reference
        self.error_handler = ErrorHandler(config_manager, logger=self.config_manager.ccxt_log)
        self.logger = self.config_manager.ccxt_log

    @classmethod
    def _cleanup_proxy(cls):
        """Coordinate proxy termination only when no strategies are running"""
        with cls._proxy_lock:
            # Double-check refcount under lock
            if cls._proxy_ref_count > 0:
                cls._proxy_logger.info(
                    f"[PROXY.MAINTENANCE] Aborting cleanup - strategies still running: refcount={cls._proxy_ref_count}"
                )
                return

            if not cls._proxy_service_instance or not cls._proxy_service_thread.is_alive():
                cls._proxy_logger.info("[PROXY.MAINTENANCE] Proxy service already terminated")
                cls._proxy_service_instance = None
                cls._proxy_service_thread = None
                return

            try:
                cls._proxy_logger.info("[PROXY.MAINTENANCE] Stopping proxy service...")
                cls._proxy_service_instance.stop()
                cls._proxy_service_thread.join(timeout=10.0)

                if cls._proxy_service_thread.is_alive():
                    cls._proxy_logger.warning("[PROXY.MAINTENANCE] Proxy service thread failed to stop after 10s")
                else:
                    cls._proxy_logger.info("[PROXY.MAINTENANCE] Proxy service stopped successfully")

            except Exception as e:
                cls._proxy_logger.error(f"[PROXY.MAINTENANCE] Error during proxy service stop: {str(e)}")
            finally:
                cls._proxy_service_instance = None
                cls._proxy_service_thread = None
                cls._proxy_logger.info("[PROXY.MAINTENANCE] Proxy state cleared")

    def init_ccxt_instance(self, exchange, hostname=None, private_api=False, debug_level=1):
        # CCXT instance
        api_key = None
        api_secret = None
        if private_api:
            try:
                with open(self.config_manager.ROOT_DIR + '/config/api_keys.local.json') as json_file:
                    data_json = json.load(json_file)
                    for data in data_json['api_info']:
                        if exchange in data['exchange']:
                            api_key = data['api_key']
                            api_secret = data['api_secret']
            except Exception as e:
                self.error_handler.handle(
                    e,
                    context={"method": "init_ccxt_instance",
                             "exchange": exchange,
                             "file": "api_keys.local.json"}
                )
                return None

        if exchange in ccxt.exchanges:
            exchange_class = getattr(ccxt, exchange)
            if hostname:
                instance = exchange_class({
                    'apiKey': api_key,
                    'secret': api_secret,
                    'enableRateLimit': True,
                    'rateLimit': 1000,
                    'hostname': hostname,  # 'global.bittrex.com',
                })
            else:
                instance = exchange_class({
                    'apiKey': api_key,
                    'secret': api_secret,
                    'enableRateLimit': True,
                    'rateLimit': 1000,
                })
            done = False
            while not done:
                try:
                    # Run blocking load_markets in a thread pool executor
                    instance.load_markets()  # Directly call the blocking method
                except Exception as e:
                    self.error_handler.handle(
                        e,
                        context={"method": "init_ccxt_instance", "exchange": exchange}
                    )
                    # Continue retrying unless it's a critical error
                    if isinstance(e, CriticalError):
                        raise RPCConfigError("No valid Blocknet Core Config path found.", context={"context": str(e)})
                else:
                    done = True
            return instance
        else:
            self.logger.error(f"Unsupported exchange: {exchange}")
            return None

    async def _ccxt_blocking_call_with_retry(self, func, context, *args):
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

    async def ccxt_call_fetch_order_book(self, ccxt_o, symbol, limit=25, ignore_timer=False):
        update_cex_orderbook_timer_delay = 2
        if ignore_timer or self.cex_orderbook_timer is None or \
                time.time() - self.cex_orderbook_timer > update_cex_orderbook_timer_delay:
            self.cex_orderbook = await self._fetch_order_book(ccxt_o, symbol, limit)
            self.cex_orderbook_timer = time.time()
        return self.cex_orderbook

    async def _fetch_order_book(self, ccxt_o, symbol, limit):
        context = {
            "method": "_fetch_order_book",
            "symbol": symbol,
            "limit": limit
        }
        result = await self._ccxt_blocking_call_with_retry(
            ccxt_o.fetch_order_book,
            context,
            symbol, limit
        )
        if result is not None:
            self._debug_display('ccxt_call_fetch_order_book', [symbol, limit], result)
        return result

    async def ccxt_call_fetch_free_balance(self, ccxt_o):
        context = {
            "method": "ccxt_call_fetch_free_balance"
        }
        result = await self._ccxt_blocking_call_with_retry(
            ccxt_o.fetch_free_balance,
            context
        )
        if result is not None:
            self._debug_display('ccxt_call_fetch_free_balance', [], result)
        return result

    async def ccxt_call_fetch_tickers(self, ccxt_o, symbols_list, proxy=True):
        start = time.time()
        err_count = 0

        # Start proxy if needed before first attempt
        if proxy and not is_port_open("127.0.0.1", 2233):
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._start_proxy)

        while True:
            try:
                used_proxy = False
                if is_port_open("127.0.0.1", 2233) and proxy:  # CCXT PROXY
                    result = await rpc_call("ccxt_call_fetch_tickers", tuple(symbols_list), rpc_port=2233,
                                            debug=self.config_manager.config_ccxt.debug_level,
                                            logger=self.config_manager.general_log, timeout=60)
                    used_proxy = True
                else:
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(None, ccxt_o.fetchTickers, symbols_list)

                if result is not None:
                    stop = time.time()
                    self._debug_display('ccxt_call_fetch_tickers',
                                        str(symbols_list) + ' used_proxy? ' + str(used_proxy),
                                        result,
                                        timer=stop - start)
                    return result
            except Exception as error:
                err_count += 1
                context = {
                    "method": "ccxt_call_fetch_tickers",
                    "symbols": symbols_list,
                    "proxy_used": proxy,
                    "err_count": err_count
                }
                if not await self.error_handler.handle_async(
                        error, context=context
                ):
                    return None

    async def ccxt_call_fetch_ticker(self, ccxt_o, symbol):
        context = {
            "method": "ccxt_call_fetch_ticker",
            "symbol": symbol
        }
        result = await self._ccxt_blocking_call_with_retry(
            ccxt_o.fetch_ticker,
            context,
            symbol
        )
        if result is not None:
            self._debug_display('ccxt_call_fetch_ticker', [symbol], result)
        return result

    def _start_proxy(self):
        """Start shared CCXT proxy service in a thread. This is a blocking call."""
        with CCXTManager._proxy_lock:
            if CCXTManager._proxy_service_thread and CCXTManager._proxy_service_thread.is_alive():
                CCXTManager._proxy_logger.info("[PROXY.STARTUP] Proxy service thread is already running.")
                return

            if is_port_open("127.0.0.1", CCXTManager._proxy_port):
                CCXTManager._proxy_logger.warning(
                    f"[PROXY.STARTUP] Proxy port {CCXTManager._proxy_port} already in use. Aborting start."
                )
                return

            CCXTManager._proxy_logger.info(
                f"[PROXY.STARTUP] Initializing proxy service on port {CCXTManager._proxy_port}")

            try:
                CCXTManager._proxy_service_instance = AsyncPriceService()

                def service_runner():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(CCXTManager._proxy_service_instance.run())
                    except Exception as e:
                        CCXTManager._proxy_logger.error(f"Error in proxy service event loop: {e}", exc_info=True)
                    finally:
                        loop.close()

                CCXTManager._proxy_service_thread = threading.Thread(target=service_runner, name="CCXTProxyService")
                CCXTManager._proxy_service_thread.daemon = True
                CCXTManager._proxy_service_thread.start()

                CCXTManager._proxy_logger.info("[PROXY.STARTUP] Proxy service thread started.")

                # Verify proxy started properly
                proxy_started = False
                for attempt in range(10):  # Wait up to 10 seconds
                    time.sleep(1)
                    if is_port_open("127.0.0.1", CCXTManager._proxy_port):
                        ready_msg = f"Proxy operational (Thread: {CCXTManager._proxy_service_thread.name})"
                        CCXTManager._proxy_logger.info(ready_msg)
                        proxy_started = True
                        break

                if not proxy_started:
                    failure_msg = "Proxy failed to start and open port after 10 seconds."
                    CCXTManager._proxy_logger.error(failure_msg)
                    if CCXTManager._proxy_service_instance:
                        CCXTManager._proxy_service_instance.stop()
                    CCXTManager._proxy_service_instance = None
                    CCXTManager._proxy_service_thread = None
            except Exception as e:
                error_detail = f"Startup error: {str(e)}"
                CCXTManager._proxy_logger.error(error_detail, exc_info=True)
                CCXTManager._proxy_service_instance = None
                CCXTManager._proxy_service_thread = None
                self.error_handler.handle(
                    e,
                    context={"stage": "proxy_startup"}
                )

    def _debug_display(self, func, params, result, timer=None):
        debug_level = self.config_manager.config_ccxt.debug_level
        if debug_level < 2:
            return

        if timer is None:
            timer = ''
        else:
            timer = " exec_timer: " + str(round(timer, 2))

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
