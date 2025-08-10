import asyncio
import time
import traceback

import aiohttp

from definitions.ccxt_manager import CCXTManager
from definitions.errors import OperationalError
from definitions.errors import RPCConfigError
from definitions.shutdown import ShutdownCoordinator

debug_level = 2

CCXT_PRICE_REFRESH = 2
UPDATE_BALANCES_DELAY = 0.5
FLUSH_DELAY = 15 * 60
MAX_THREADS = 5
SLEEP_INTERVAL = 1  # Shorter sleep interval (in seconds)


class TradingProcessor:
    def __init__(self, controller):
        self.controller = controller
        self.pairs_dict = controller.pairs_dict

    async def process_pairs(self, target_function):
        """Processes all pairs using the target function without concurrency limit."""
        tasks = []
        for pair in self.pairs_dict.values():
            if pair.disabled:
                continue
            if self.controller.shutdown_event.is_set():
                return
            if asyncio.iscoroutinefunction(target_function):
                tasks.append(target_function(pair))
            else:
                loop = asyncio.get_running_loop()
                tasks.append(loop.run_in_executor(None, target_function, pair))
        if tasks:
            await asyncio.gather(*tasks)


class BalanceManager:
    def __init__(self, tokens_dict, config_manager, loop):
        self.tokens_dict = tokens_dict
        self.config_manager = config_manager
        self.timer_main_dx_update_bals = None
        self.loop = loop

    async def update_balances(self):
        if self._should_update_bals():
            try:
                xb_tokens = await self.config_manager.xbridge_manager.getlocaltokens()
            except Exception as e:
                self.config_manager.error_handler.handle(
                    OperationalError(f"Error getting local tokens: {e}"),
                    context={"stage": "update_balances"},
                    exc_info=True
                )
                return

            futures = []
            for token_data in self.tokens_dict.values():
                futures.append(self._update_token_balance(token_data, xb_tokens))

            if futures:
                try:
                    await asyncio.gather(*futures)  # Wait for all balance updates to complete
                except Exception as e:
                    self.config_manager.error_handler.handle(
                        OperationalError(f"Error in balance updates: {e}"),
                        context={"stage": "update_balances"},
                        exc_info=True
                    )

            self.timer_main_dx_update_bals = time.time()

    def _should_update_bals(self):
        return self.timer_main_dx_update_bals is None or time.time() - self.timer_main_dx_update_bals > UPDATE_BALANCES_DELAY

    async def _update_token_balance(self, token_data, xb_tokens):
        with self.config_manager.resource_lock:
            try:
                if xb_tokens and token_data.symbol in xb_tokens:
                    utxos = await self.config_manager.xbridge_manager.gettokenutxo(token_data.symbol, used=True)
                    bal, bal_free = self._calculate_balances(utxos)
                    token_data.dex.total_balance = bal
                    token_data.dex.free_balance = bal_free
                else:
                    token_data.dex.total_balance = None
                    token_data.dex.free_balance = None
            except Exception as e:
                self.config_manager.error_handler.handle(
                    OperationalError(f"Error updating {token_data.symbol} balance: {e}"),
                    context={"token": token_data.symbol},
                    exc_info=True
                )

    def _calculate_balances(self, utxos):
        bal = bal_free = 0
        if isinstance(utxos, list):
            for utxo in utxos:
                amount = float(utxo.get('amount', 0))
                bal += amount
                if not utxo.get('orderid'):
                    bal_free += amount
        return bal, bal_free


class PriceHandler:
    def __init__(self, main_controller, loop):
        self.tokens_dict = main_controller.tokens_dict
        self.ccxt_i = main_controller.ccxt_i
        self.config_manager = main_controller.config_manager
        self.main_controller = main_controller
        self.loop = loop
        self.ccxt_price_timer = None
        self.shutdown_event = main_controller.shutdown_event

    async def update_ccxt_prices(self):
        if not self.config_manager.strategy_instance.should_update_cex_prices():
            self.config_manager.general_log.debug("Strategy does not require CEX price updates.")
            return

        if self.ccxt_price_timer is None or time.time() - self.ccxt_price_timer > CCXT_PRICE_REFRESH:
            try:
                await self._fetch_and_update_prices()  # Await the async call
                self.ccxt_price_timer = time.time()
            except Exception as e:
                self.config_manager.error_handler.handle(
                    OperationalError(f"Error updating CEX prices: {e}"),
                    context={"stage": "price_update"}
                )

    async def _fetch_and_update_prices(self):
        custom_coins = vars(self.config_manager.config_coins.usd_ticker_custom).keys()
        keys = [self._construct_key(token) for token in self.tokens_dict if token not in custom_coins]

        try:
            tickers = await self.config_manager.ccxt_manager.ccxt_call_fetch_tickers(self.ccxt_i, keys)
            await self._update_token_prices(tickers)
        except Exception as e:
            self.config_manager.error_handler.handle(
                OperationalError(f"Error fetching CEX tickers: {e}"),
                context={"stage": "price_update"}
            )

    def _construct_key(self, token):
        return f"{token}/USDT" if token == 'BTC' else f"{token}/BTC"

    async def _update_token_prices(self, tickers):
        lastprice_string = self._get_last_price_string()
        symbols_to_update = sorted(
            self.tokens_dict.items(),
            key=lambda item: (item[0] != 'BTC', item[0])
        )

        for token_symbol, token_data in symbols_to_update:
            if self.shutdown_event.is_set():
                return
            symbol = f"{token_data.symbol}/USDT" if token_data.symbol == 'BTC' else f"{token_data.symbol}/BTC"
            if not hasattr(self.config_manager.config_coins.usd_ticker_custom,
                           token_symbol) and symbol in self.ccxt_i.symbols:
                try:
                    await self._update_token_price(tickers, symbol, lastprice_string, token_data)
                except Exception as e:
                    self.config_manager.error_handler.handle(
                        OperationalError(f"Error updating {token_symbol} price: {e}"),
                        context={"token": token_symbol, "symbol": symbol},
                        exc_info=True
                    )

        for token in vars(self.config_manager.config_coins.usd_ticker_custom):
            if self.main_controller.shutdown_event.is_set():
                return
            if token in self.tokens_dict:
                try:
                    await self.tokens_dict[token].cex.update_price()
                except Exception as e:
                    self.config_manager.error_handler.handle(
                        OperationalError(f"Error updating custom {token} price: {e}"),
                        context={"token": token},
                        exc_info=True
                    )

    def _get_last_price_string(self):
        return {
            "kucoin": "last",
            "binance": "lastPrice"
        }.get(self.config_manager.my_ccxt.id, "lastTradeRate")

    async def _update_token_price(self, tickers, symbol, lastprice_string, token_data):
        if symbol in tickers:
            last_price = float(tickers[symbol]['info'][lastprice_string])  # This is a blocking call
            if token_data.symbol == 'BTC':
                token_data.cex.usd_price = last_price
                token_data.cex.cex_price = 1
            else:
                token_data.cex.cex_price = last_price
                token_data.cex.usd_price = last_price * self.tokens_dict['BTC'].cex.usd_price
        else:
            self.config_manager.general_log.warning(f"Missing symbol in tickers: {symbol}")
            token_data.cex.cex_price = None
            token_data.cex.usd_price = None


class MainController:
    def __init__(self, config_manager, loop):
        self.config_manager = config_manager
        self.pairs_dict = config_manager.pairs
        self.tokens_dict = config_manager.tokens
        self.ccxt_i = config_manager.my_ccxt
        self.config_coins = config_manager.config_coins
        self.disabled_coins = []
        self.http_session = None  # Initialize http_session
        self.shutdown_event = asyncio.Event()
        self.loop = loop

        self.price_handler = PriceHandler(self, self.loop)
        self.balance_manager = BalanceManager(self.tokens_dict, self.config_manager, self.loop)
        self.processor = TradingProcessor(self)
        self.config_manager.strategy_instance.controller = self  # Pass controller to strategy

    async def main_init_loop(self):
        try:
            # Initialize token addresses, which was previously done in a sync constructor
            token_init_futures = []
            for token in self.tokens_dict.values():
                if token.dex.enabled:
                    # Only queue token initialization if not shutting down
                    if not self.config_manager.controller.shutdown_event.is_set():
                        token_init_futures.append(token.dex.read_address())
            if token_init_futures:
                await asyncio.gather(*token_init_futures)

            # Load XBridge configuration asynchronously if enabled
            if self.config_manager.load_xbridge_conf_on_startup:
                await self.config_manager.xbridge_manager.dxloadxbridgeconf()

            await self.balance_manager.update_balances()  # Await the async call
            await self.price_handler.update_ccxt_prices()  # Await the async call

            futures = []
            for pair in self.pairs_dict.values():
                if self.shutdown_event.is_set():
                    return
                if self.config_manager.strategy_instance.should_update_cex_prices():
                    futures.append(pair.cex.update_pricing())

            if futures:
                await asyncio.gather(*futures)

            await self.processor.process_pairs(self.config_manager.strategy_instance.thread_init_async_action)
        except Exception as e:
            self.config_manager.error_handler.handle(
                OperationalError(f"Initialization loop error: {e}"),
                context={"stage": "main_init_loop"}
            )
            raise

    async def main_loop(self):
        try:
            start_time = time.perf_counter()
            await self.balance_manager.update_balances()  # Await the async call
            await self.price_handler.update_ccxt_prices()  # Await the async call

            futures = []

            for pair in self.pairs_dict.values():
                if self.shutdown_event.is_set():
                    return
                if self.config_manager.strategy_instance.should_update_cex_prices():
                    futures.append(pair.cex.update_pricing())
            if futures:
                await asyncio.gather(*futures)

            await self.processor.process_pairs(self.config_manager.strategy_instance.thread_loop_async_action)
            self._report_time(start_time)
        except Exception as e:
            self.config_manager.error_handler.handle(
                OperationalError(f"Main loop error: {e}"),
                context={"stage": "main_loop"}
            )

    def _report_time(self, start_time):
        end_time = time.perf_counter()
        self.config_manager.general_log.info(f'Operation took {end_time - start_time:0.2f} second(s) to complete.')

    def thread_init_blocking(self, pair):
        try:
            self.config_manager.strategy_instance.thread_loop_blocking_action(pair)
        except Exception as e:
            self.config_manager.error_handler.handle(
                OperationalError(f"Thread blocking action error: {e}"),
                context={"pair": pair.symbol},
                exc_info=True
            )


def run_async_main(config_manager, startup_tasks=None):
    """Runs the main application loop with proper signal handling."""

    async def main_wrapper():
        CCXTManager.register_strategy()
        try:
            controller = MainController(config_manager, asyncio.get_running_loop())
            config_manager.controller = controller
            await main(config_manager, asyncio.get_running_loop(), startup_tasks)
        except (SystemExit, KeyboardInterrupt, asyncio.CancelledError):
            config_manager.general_log.info("Received stop signal. Initiating coordinated shutdown...")
            if controller and not controller.shutdown_event.is_set():
                controller.shutdown_event.set()
                await ShutdownCoordinator.unified_shutdown(config_manager)
        except RPCConfigError as e:
            config_manager.general_log.critical(f"Fatal RPC configuration error: {e}")
            raise
        finally:
            # Unregister strategy after cleanup
            CCXTManager.unregister_strategy()
            # Give time for proxy cleanup
            await asyncio.sleep(0.5)

    try:
        asyncio.run(main_wrapper())
    except Exception as e:
        config_manager.general_log.error(f"Unhandled exception: {e}")
        traceback.print_exc()


async def main(config_manager, loop, startup_tasks=None):
    """Generic main loop that works with any strategy. Handles graceful cancellation."""
    try:
        if startup_tasks:
            config_manager.general_log.info("Running startup tasks...")
            await asyncio.gather(*startup_tasks)
            config_manager.general_log.info("Startup tasks finished.")

        # Create HTTP session
        async with aiohttp.ClientSession() as session:
            # Pass the session to the controller and strategy if needed
            config_manager.controller.http_session = session
            if hasattr(config_manager.strategy_instance, 'http_session'):
                config_manager.strategy_instance.http_session = session

            config_manager.general_log.info("Performing initial operation (creating or resuming orders)...")
            await config_manager.controller.main_init_loop()

            if config_manager.controller.shutdown_event.is_set():
                config_manager.general_log.info(
                    "Shutdown requested during initial operation. Exiting without starting main loop.")
                return

            operation_interval = config_manager.strategy_instance.get_operation_interval()
            config_manager.general_log.info(
                f"Using operation interval of {operation_interval} seconds for {config_manager.strategy} strategy.")

            flush_timer = time.time()                                                                                                                                                           
            # Immediately run the main loop once at the start                                                                                                                                   
            await config_manager.controller.main_loop()                                                                                                                                         
            operation_timer = time.time()  # Reset the operation timer after the first run     

            while not config_manager.controller.shutdown_event.is_set():
                current_time = time.time()

                if current_time - flush_timer > FLUSH_DELAY:
                    await config_manager.xbridge_manager.dxflushcancelledorders()
                    flush_timer = current_time

                if current_time - operation_timer > operation_interval:
                    await config_manager.controller.main_loop()
                    operation_timer = current_time

                try:
                    # Use shorter timeout to check shutdown event more frequently
                    await asyncio.wait_for(
                        config_manager.controller.shutdown_event.wait(),
                        timeout=SLEEP_INTERVAL
                    )
                except asyncio.TimeoutError:
                    # Normal behavior when timeout occurs
                    pass
    except (asyncio.CancelledError, KeyboardInterrupt):
        config_manager.general_log.info("Main task cancelled. Preparing for shutdown...")
        raise
