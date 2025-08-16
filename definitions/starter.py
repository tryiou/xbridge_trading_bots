import asyncio
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TYPE_CHECKING, Union

import aiohttp
import ccxt

from definitions.ccxt_manager import CCXTManager
from definitions.errors import OperationalError, RPCConfigError, CriticalError
from definitions.pair import Pair
from definitions.shutdown import ShutdownCoordinator
from definitions.token import Token

if TYPE_CHECKING:
    from definitions.config_manager import ConfigManager
    from strategies.base_strategy import BaseStrategy
    from definitions.xbridge_manager import XBridgeManager

debug_level = 2

CCXT_PRICE_REFRESH: int = 2
UPDATE_BALANCES_DELAY: float = 0.5
FLUSH_DELAY: int = 15 * 60
MAX_THREADS: int = 5
SLEEP_INTERVAL: int = 1  # Shorter sleep interval (in seconds)


class TradingProcessor:
    """Processes trading pairs using target functions asynchronously."""

    def __init__(self, controller: 'MainController') -> None:
        """
        Initialize TradingProcessor.
        
        Args:
            controller: Reference to main controller instance
        """
        self.controller: 'MainController' = controller
        self.pairs_dict: Dict[str, Pair] = controller.pairs_dict

    async def process_pairs(
            self,
            target_function: Callable[[Pair], Union[None, Any]]
    ) -> None:
        """
        Processes all trading pairs using the target function.
        
        Handles both async and sync functions. Processes only enabled pairs.
        
        Args:
            target_function: Function to execute for each pair. Can be async or sync.
        """
        tasks = []
        for pair in self.pairs_dict.values():
            if pair.disabled:
                continue
            if self.controller.shutdown_event.is_set():
                return
            if asyncio.iscoroutinefunction(target_function):
                tasks.append(target_function(pair))
            else:
                tasks.append(self.controller.loop.run_in_executor(None, target_function, pair))
        if tasks:
            await asyncio.gather(*tasks)


class BalanceManager:
    """Manages token balance updates for the trading system."""

    def __init__(
            self,
            tokens_dict: Dict[str, Token],
            config_manager: 'ConfigManager',
            loop: asyncio.AbstractEventLoop
    ) -> None:
        """
        Initialize BalanceManager.
        
        Args:
            tokens_dict: Dictionary of token symbols to Token instances
            config_manager: Reference to application config manager
            loop: Asyncio event loop instance
        """
        self.tokens_dict: Dict[str, Token] = tokens_dict
        self.config_manager: 'ConfigManager' = config_manager
        self.timer_main_dx_update_bals: Optional[float] = None
        self.loop: asyncio.AbstractEventLoop = loop

    async def update_balances(self) -> None:
        """
        Update token balances if update interval has elapsed.
        
        Retrieves token UTXOs and calculates free/total balances.
        Handles errors through the application's error handler.
        """
        if self._should_update_bals():
            try:
                xb_tokens: List[str] = await self.config_manager.xbridge_manager.getlocaltokens()
            except Exception as e:
                err = OperationalError(f"Error getting local tokens: {e}")
                self.config_manager.error_handler.handle(
                    err,
                    context={"stage": "update_balances"},
                    exc_info=True
                )
                return

            futures = []
            for token_data in self.tokens_dict.values():
                futures.append(self._update_token_balance(token_data, xb_tokens))

            if futures:
                try:
                    await asyncio.gather(*futures)
                except Exception as e:
                    err = OperationalError(f"Error in balance updates: {e}")
                    self.config_manager.error_handler.handle(
                        err,
                        context={"stage": "update_balances"},
                        exc_info=True
                    )

            self.timer_main_dx_update_bals = time.time()

    def _should_update_bals(self) -> bool:
        """Determine if balance update interval has elapsed since last update."""
        return (self.timer_main_dx_update_bals is None or
                time.time() - self.timer_main_dx_update_bals > UPDATE_BALANCES_DELAY)

    async def _update_token_balance(
            self,
            token_data: Token,
            xb_tokens: List[str]
    ) -> None:
        """
        Update balance for a single token.
        
        Args:
            token_data: Token instance to update
            xb_tokens: List of tokens to get balances for
        """
        with self.config_manager.resource_lock:
            try:
                if token_data.symbol not in xb_tokens:
                    token_data.dex.total_balance = None
                    token_data.dex.free_balance = None
                    return

                utxos = await self.config_manager.xbridge_manager.gettokenutxo(token_data.symbol, used=True)
                bal, bal_free = self._calculate_balances(utxos)
                token_data.dex.total_balance = bal
                token_data.dex.free_balance = bal_free
            except Exception as e:
                err = OperationalError(f"Error updating {token_data.symbol} balance: {e}")
                self.config_manager.error_handler.handle(
                    err,
                    context={"token": token_data.symbol},
                    exc_info=True
                )

    def _calculate_balances(self, utxos: List[Dict[str, Any]]) -> Tuple[float, float]:
        """
        Calculate total and free balances from UTXO list.
        
        Args:
            utxos: List of UTXO dictionaries
            
        Returns:
            Tuple of (total_balance, free_balance)
        """
        if not isinstance(utxos, list):
            return (0.0, 0.0)

        bal: float = 0.0
        bal_free: float = 0.0

        for utxo in utxos:
            amount = float(utxo.get('amount', 0))
            bal += amount
            # UTXOs without order IDs are free (not locked in orders)
            if not utxo.get('orderid'):
                bal_free += amount

        return (bal, bal_free)


class PriceHandler:
    """Handles updating token prices from CEX sources."""

    def __init__(self, main_controller: 'MainController', loop: asyncio.AbstractEventLoop) -> None:
        """
        Initialize PriceHandler.
        
        Args:
            main_controller: Reference to MainController
            loop: Asyncio event loop
        """
        self.tokens_dict: Dict[str, Token] = main_controller.tokens_dict
        self.ccxt_i: ccxt.Exchange = main_controller.ccxt_i
        self.config_manager: 'ConfigManager' = main_controller.config_manager
        self.main_controller: 'MainController' = main_controller
        self.loop: asyncio.AbstractEventLoop = loop
        self.ccxt_price_timer: Optional[float] = None
        self.shutdown_event: asyncio.Event = main_controller.shutdown_event

    async def update_ccxt_prices(self) -> None:
        """
        Update CEX prices if strategy requires it and refresh interval elapsed.
        """
        strategy_instance: 'BaseStrategy' = self.config_manager.strategy_instance
        if not strategy_instance.should_update_cex_prices():
            self.config_manager.general_log.debug("Strategy does not require CEX price updates.")
            return

        now: float = time.time()
        if self.ccxt_price_timer is None or now - self.ccxt_price_timer > CCXT_PRICE_REFRESH:
            try:
                await self._fetch_and_update_prices()
                self.ccxt_price_timer = now
            except Exception as e:
                err = OperationalError(f"Error updating CEX prices: {e}")
                self.config_manager.error_handler.handle(
                    err,
                    context={"stage": "price_update"}
                )

    async def _fetch_and_update_prices(self) -> None:
        """Fetch ticker data from CEX and update token prices."""
        custom_coins: Set[str] = set(vars(self.config_manager.config_coins.usd_ticker_custom).keys())
        keys: List[str] = [
            self._construct_key(token)
            for token in self.tokens_dict
            if token not in custom_coins
        ]

        try:
            tickers: Dict = await self.config_manager.ccxt_manager.ccxt_call_fetch_tickers(
                self.ccxt_i, keys
            )
            await self._update_token_prices(tickers)
        except Exception as e:
            err = OperationalError(f"Error fetching CEX tickers: {e}")
            self.config_manager.error_handler.handle(
                err,
                context={"stage": "price_update"}
            )

    def _construct_key(self, token: str) -> str:
        """Construct symbol string for CEX API."""
        return f"{token}/USDT" if token == 'BTC' else f"{token}/BTC"

    async def _update_token_prices(self, tickers: Dict) -> None:
        """
        Update token prices from ticker data and custom coin configurations.
        
        Args:
            tickers: Dictionary of symbol to ticker data
        """
        lastprice_string: str = self._get_last_price_string()
        # BTC first, then others
        symbols_to_update: List[Tuple[str, Token]] = sorted(
            self.tokens_dict.items(),
            key=lambda item: (item[0] != 'BTC', item[0])
        )

        for token_symbol, token_data in symbols_to_update:
            if self.shutdown_event.is_set():
                return
            symbol: str = (f"{token_data.symbol}/USDT" if token_data.symbol == 'BTC'
                           else f"{token_data.symbol}/BTC")
            is_custom_coin: bool = hasattr(
                self.config_manager.config_coins.usd_ticker_custom,
                token_symbol
            )
            if not is_custom_coin and symbol in self.ccxt_i.symbols:
                try:
                    self._update_token_price(tickers, symbol, lastprice_string, token_data)
                except Exception as e:
                    exc = OperationalError(f"Error updating {token_symbol} price: {e}")
                    self.config_manager.error_handler.handle(
                        exc,
                        context={"token": token_symbol, "symbol": symbol},
                        exc_info=True
                    )

        # Process custom coins
        custom_tokens: Set[str] = set(vars(self.config_manager.config_coins.usd_ticker_custom))
        for token in custom_tokens:
            if self.main_controller.shutdown_event.is_set():
                return
            if token in self.tokens_dict:
                try:
                    await self.tokens_dict[token].cex.update_price()
                except Exception as e:
                    exc = OperationalError(f"Error updating custom {token} price: {e}")
                    self.config_manager.error_handler.handle(
                        exc,
                        context={"token": token},
                        exc_info=True
                    )

    def _get_last_price_string(self) -> str:
        """Get exchange-specific field name for last price."""
        exchange_map: Dict[str, str] = {
            "kucoin": "last",
            "binance": "lastPrice"
        }
        return exchange_map.get(self.config_manager.my_ccxt.id, "lastTradeRate")

    def _update_token_price(
            self,
            tickers: Dict,
            symbol: str,
            price_key: str,
            token_data: Token
    ) -> None:
        """
        Update a token's price from ticker data.
        
        Args:
            tickers: Dictionary of symbol to ticker data
            symbol: Trading symbol to look up
            price_key: Exchange-specific field containing last price
            token_data: Token instance to update
        """
        if symbol in tickers:
            last_price: float = float(tickers[symbol]['info'][price_key])
            if token_data.symbol == 'BTC':
                token_data.cex.usd_price = last_price
                token_data.cex.cex_price = 1.0
            else:
                token_data.cex.cex_price = last_price
                btc_usd: Optional[float] = self.tokens_dict['BTC'].cex.usd_price
                token_data.cex.usd_price = last_price * btc_usd if btc_usd else None
        else:
            self.config_manager.general_log.warning(f"Missing symbol in tickers: {symbol}")
            token_data.cex.cex_price = None
            token_data.cex.usd_price = None


class MainController:
    """Main controller class for coordinating trading operations."""

    def __init__(self, config_manager: 'ConfigManager', loop: asyncio.AbstractEventLoop) -> None:
        """
        Initialize MainController.
        
        Args:
            config_manager: Application configuration manager
            loop: Asyncio event loop
        """
        self.config_manager: 'ConfigManager' = config_manager
        self.pairs_dict: Dict[str, Pair] = config_manager.pairs
        self.tokens_dict: Dict[str, Token] = config_manager.tokens
        self.ccxt_i: ccxt.Exchange = config_manager.my_ccxt
        self.config_coins: Any = config_manager.config_coins
        self.disabled_coins: List[str] = []
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self._http_session_owner: bool = False
        self.loop: asyncio.AbstractEventLoop = loop

        # Subcomponents
        self.price_handler: PriceHandler = PriceHandler(self, loop)
        self.balance_manager: BalanceManager = BalanceManager(
            self.tokens_dict, self.config_manager, loop
        )
        self.processor: TradingProcessor = TradingProcessor(self)
        # Pass controller reference to strategy
        self.config_manager.strategy_instance.controller = self

    async def main_init_loop(self) -> None:
        """Initialization loop executed before main trading starts."""
        try:
            # Read addresses for enabled tokens
            token_init_futures: List[asyncio.Task] = [
                token.dex.read_address()
                for token in self.tokens_dict.values()
                if token.dex.enabled and not self.shutdown_event.is_set()
            ]
            if token_init_futures:
                await asyncio.gather(*token_init_futures)

            # Load XBridge config if needed
            if self.config_manager.load_xbridge_conf_on_startup:
                xbm: 'XBridgeManager' = self.config_manager.xbridge_manager
                await xbm.dxloadxbridgeconf()

            # Initialize balances and prices
            await self.balance_manager.update_balances()
            await self.price_handler.update_ccxt_prices()

            # Update pair pricing
            strategy = self.config_manager.strategy_instance
            if strategy.should_update_cex_prices():
                price_futures: List[asyncio.Task] = [
                    pair.cex.update_pricing()
                    for pair in self.pairs_dict.values()
                    if not self.shutdown_event.is_set()
                ]
                if price_futures:
                    await asyncio.gather(*price_futures)

            await self.processor.process_pairs(strategy.thread_init_async_action)
        except Exception as e:
            error = CriticalError(f"Initialization loop error: {e}", context={"component": "main_init_loop"})
            if self.config_manager:
                self.config_manager.error_handler.handle(error)
            raise

    async def main_loop(self) -> None:
        """Main trading loop executed repeatedly at configured intervals."""
        try:
            start_time: float = time.perf_counter()
            await self.balance_manager.update_balances()
            await self.price_handler.update_ccxt_prices()

            price_futures: List[asyncio.Task] = []
            strategy = self.config_manager.strategy_instance
            for pair in self.pairs_dict.values():
                if self.shutdown_event.is_set():
                    return
                if strategy.should_update_cex_prices():
                    price_futures.append(pair.cex.update_pricing())
            if price_futures:
                await asyncio.gather(*price_futures)

            await self.processor.process_pairs(strategy.safe_thread_loop)
            self._report_time(start_time)
        except Exception as e:
            err_msg: str = f"Main loop error: {e}"
            context: Dict = {"component": "main_loop"}
            error = CriticalError(err_msg, context=context)
            if self.config_manager:
                await self.config_manager.error_handler.handle_async(error)

    def _report_time(self, start_time: float) -> None:
        """
        Log operation execution time.
        
        Args:
            start_time: Timestamp before operation started
        """
        end_time: float = time.perf_counter()
        duration: float = end_time - start_time
        self.config_manager.general_log.info(f'Operation took {duration:0.2f} second(s) to complete.')

    def thread_init_blocking(self, pair: Pair) -> None:
        """
        Execute blocking initialization task for a trading pair.
        
        Args:
            pair: Pair instance to initialize
        """
        try:
            self.config_manager.strategy_instance.thread_loop_blocking_action(pair)
        except Exception as e:
            err = OperationalError(f"Thread blocking action error: {e}")
            self.config_manager.error_handler.handle(
                err,
                context={"pair": pair.symbol},
                exc_info=True
            )

    async def close_http_session(self) -> None:
        """Close the HTTP session if controller owns it."""
        if self.http_session and not self.http_session.closed and self._http_session_owner:
            try:
                await self.http_session.close()
            except (aiohttp.ClientError, asyncio.CancelledError) as e:
                self.config_manager.general_log.warning(f"Session closure warning: {str(e)}")
            self.config_manager.general_log.debug("HTTP session closed")
        self.http_session = None
        self._http_session_owner = False


def run_async_main(config_manager: 'ConfigManager', startup_tasks: Optional[List[Callable]] = None) -> None:
    """
    Run main application loop with proper signal handling and cleanup.
    
    Args:
        config_manager: Application configuration manager
        startup_tasks: Optional list of async tasks to run at startup
    """

    async def main_wrapper() -> None:
        """Async wrapper for main application logic."""
        CCXTManager.register_strategy()
        controller: Optional[MainController] = None
        try:
            loop = asyncio.get_running_loop()
            controller = MainController(config_manager, loop)
            config_manager.controller = controller
            config_manager.strategy_instance.is_running = True
            await main(config_manager, loop, startup_tasks)
        except (SystemExit, asyncio.CancelledError):
            config_manager.general_log.info("Received stop signal. Initiating coordinated shutdown...")
            if config_manager.strategy_instance:
                config_manager.strategy_instance.stop()
        except RPCConfigError as e:
            config_manager.general_log.critical(f"Fatal RPC configuration error: {e}")
            raise
        finally:
            if controller:
                await ShutdownCoordinator.unified_shutdown(config_manager)

            CCXTManager.unregister_strategy()
            await asyncio.sleep(0.5)  # Allow proxy cleanup

    try:
        asyncio.run(main_wrapper())
    except RPCConfigError:
        raise
    except Exception as e:
        config_manager.general_log.error(f"Unhandled exception: {e}")
        traceback.print_exc()


async def main(
        config_manager: 'ConfigManager',
        loop: asyncio.AbstractEventLoop,
        startup_tasks: Optional[List[Callable]] = None
) -> None:
    """
    Execute the main trading loop.
    
    Args:
        config_manager: Application configuration manager
        loop: Asyncio event loop
        startup_tasks: Optional list of async startup tasks
    """
    try:
        # Run startup tasks if provided
        if startup_tasks:
            config_manager.general_log.info("Running startup tasks...")
            coros = [task() for task in startup_tasks]
            await asyncio.gather(*coros)
            config_manager.general_log.info("Startup tasks finished.")

        try:
            # Create and manage HTTP session
            session = aiohttp.ClientSession()
            controller = config_manager.controller
            controller.http_session = session
            controller._http_session_owner = True
            strategy = config_manager.strategy_instance
            if hasattr(strategy, 'http_session'):
                strategy.http_session = session

            config_manager.general_log.info(
                "Performing initial operation (creating or resuming orders)..."
            )
            await controller.main_init_loop()

            if controller.shutdown_event.is_set():
                config_manager.general_log.info(
                    "Shutdown requested during initial operation. Exiting without starting main loop."
                )
                return

            # Configure main loop interval
            operation_interval: int = strategy.get_operation_interval()
            config_manager.general_log.info(
                f"Using operation interval of {operation_interval} seconds "
                f"for {config_manager.strategy} strategy."
            )

            flush_timer: float = time.time()
            await controller.main_loop()  # Initial run
            operation_timer: float = time.time()

            while not controller.shutdown_event.is_set():
                current_time: float = time.time()

                # Flush cancelled orders periodically
                if current_time - flush_timer > FLUSH_DELAY:
                    xbm = config_manager.xbridge_manager
                    await xbm.dxflushcancelledorders()
                    flush_timer = current_time

                sleep_needed: float = operation_interval - (current_time - operation_timer)
                if sleep_needed <= 0:
                    await controller.main_loop()
                    operation_timer = current_time

                # Short sleep while checking for shutdown
                try:
                    await asyncio.wait_for(
                        controller.shutdown_event.wait(),
                        timeout=SLEEP_INTERVAL
                    )
                except asyncio.TimeoutError:
                    pass  # Normal timeout between operations
        finally:
            await controller.close_http_session()
    except (asyncio.CancelledError, KeyboardInterrupt):
        config_manager.general_log.info("Main task cancelled. Preparing for shutdown...")
        raise
