import asyncio
import os
import time
import traceback

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
        # Get concurrency limit from config, with a safe default of 5.
        # This prevents overwhelming the XBridge daemon with too many simultaneous requests.
        concurrency_limit = getattr(self.controller.config_manager.config_xbridge, 'max_concurrent_tasks', 5)
        self.semaphore = asyncio.Semaphore(concurrency_limit)
        self.controller.config_manager.general_log.info(
            f"XBridge concurrency limit set to {concurrency_limit} tasks."
        )

    async def process_pairs(self, target_function):
        """Processes all pairs using the target function, but limits concurrency with a semaphore."""

        async def sem_task(pair):
            async with self.semaphore:
                if self.controller.shutdown_event.is_set():
                    return
                # If target_function is async, await it directly.
                if asyncio.iscoroutinefunction(target_function):
                    await target_function(pair)
                else:  # If blocking, run in executor.
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, target_function, pair)

        tasks = [sem_task(pair) for pair in self.pairs_dict.values() if not pair.disabled]
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
            xb_tokens = await self.config_manager.xbridge_manager.getlocaltokens()

            futures = []
            for token_data in self.tokens_dict.values():
                futures.append(self._update_token_balance(token_data, xb_tokens))

            if futures:
                await asyncio.gather(*futures)  # Wait for all balance updates to complete

            self.timer_main_dx_update_bals = time.time()

    def _should_update_bals(self):
        return self.timer_main_dx_update_bals is None or time.time() - self.timer_main_dx_update_bals > UPDATE_BALANCES_DELAY

    async def _update_token_balance(self, token_data, xb_tokens):
        with self.config_manager.resource_lock:
            if xb_tokens and token_data.symbol in xb_tokens:
                utxos = await self.config_manager.xbridge_manager.gettokenutxo(token_data.symbol, used=True)
                bal, bal_free = self._calculate_balances(utxos)
                token_data.dex.total_balance = bal
                token_data.dex.free_balance = bal_free
            else:
                token_data.dex.total_balance = None
                token_data.dex.free_balance = None

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

    async def update_ccxt_prices(self):
        if not self.config_manager.strategy_instance.should_update_cex_prices():
            self.config_manager.general_log.debug("Strategy does not require CEX price updates.")
            return

        if self.ccxt_price_timer is None or time.time() - self.ccxt_price_timer > CCXT_PRICE_REFRESH:
            try:
                await self._fetch_and_update_prices()  # Await the async call
                self.ccxt_price_timer = time.time()
            except Exception as e:
                self.config_manager.general_log.error(f"Error in update_ccxt_prices: {e}", exc_info=True)

    async def _fetch_and_update_prices(self):
        custom_coins = vars(self.config_manager.config_coins.usd_ticker_custom).keys()
        keys = [self._construct_key(token) for token in self.tokens_dict if token not in custom_coins]

        tickers = await self.config_manager.ccxt_manager.ccxt_call_fetch_tickers(self.ccxt_i, keys)
        await self._update_token_prices(tickers)

    def _construct_key(self, token):
        return f"{token}/USDT" if token == 'BTC' else f"{token}/BTC"

    async def _update_token_prices(self, tickers):
        lastprice_string = self._get_last_price_string()
        for token, token_data in sorted(self.tokens_dict.items(), key=lambda item: (item[0] != 'BTC', item[0])):
            if self.main_controller.shutdown_event.is_set():
                return
            symbol = f"{token_data.symbol}/USDT" if token_data.symbol == 'BTC' else f"{token_data.symbol}/BTC"
            if not hasattr(self.config_manager.config_coins.usd_ticker_custom, token) and symbol in self.ccxt_i.symbols:
                # This is a blocking call
                await self._update_token_price(tickers, symbol, lastprice_string, token_data)

        for token in vars(self.config_manager.config_coins.usd_ticker_custom):
            if self.main_controller.shutdown_event.is_set():
                return
            if token in self.tokens_dict:
                await self.tokens_dict[token].cex.update_price()

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
        # Initialize token addresses, which was previously done in a sync constructor
        token_init_futures = []
        for token in self.tokens_dict.values():
            if token.dex.enabled:
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

    async def main_loop(self):
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

    def _report_time(self, start_time):
        end_time = time.perf_counter()
        self.config_manager.general_log.info(f'Operation took {end_time - start_time:0.2f} second(s) to complete.')

    def thread_init_blocking(self, pair):
        try:
            self.config_manager.strategy_instance.thread_loop_blocking_action(pair)
        except Exception as e:
            self.config_manager.general_log.error(f"Error in thread_loop: {e}", exc_info=True)


def run_async_main(config_manager, loop=None, startup_tasks=None):
    """Runs the main application loop, with graceful shutdown handling."""
    # Centralize the event loop policy for Windows
    if os.name == 'nt':
        # This is where the logic was moved to.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    if loop is None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    startup_tasks = startup_tasks or []
    controller = MainController(config_manager, loop)
    config_manager.controller = controller

    main_task = loop.create_task(main(config_manager, loop, startup_tasks))

    try:
        # Run the event loop until the main task is complete.
        loop.run_until_complete(main_task)
    except (SystemExit, KeyboardInterrupt):
        from definitions.shutdown import ShutdownCoordinator
        config_manager.general_log.info("Received stop signal. Initiating coordinated shutdown...")
        if controller and not controller.shutdown_event.is_set():
            controller.shutdown_event.set()
            # Use the proper async shutdown method for CLI
            loop.run_until_complete(ShutdownCoordinator.shutdown_async(config_manager))
    except Exception as e:
        config_manager.general_log.error(f"Exception in run_async_main: {e}")
        traceback.print_exc()
        raise
    finally:
        # This block will run for clean exit, SystemExit, and KeyboardInterrupt

        # Cancel the main task if it's still running (e.g., from KeyboardInterrupt)
        if not main_task.done():
            main_task.cancel()
            try:
                # Give the task a chance to finish its cancellation
                loop.run_until_complete(main_task)
            except asyncio.CancelledError:
                pass  # This is expected


        # Force close all async generators and the event loop
        if loop and not loop.is_closed():
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()
            except Exception as e:
                config_manager.general_log.error(f"Error closing loop: {str(e)}")


async def main(config_manager, loop, startup_tasks=None):
    import aiohttp  # Import aiohttp
    """Generic main loop that works with any strategy. Handles graceful cancellation."""
    try:
        async with aiohttp.ClientSession() as session:
            # Pass the session to the controller and strategy if needed
            config_manager.controller.http_session = session
            if hasattr(config_manager.strategy_instance, 'http_session'):
                config_manager.strategy_instance.http_session = session

            if startup_tasks:
                config_manager.general_log.info("Running startup tasks...")
                await asyncio.gather(*startup_tasks)
                config_manager.general_log.info("Startup tasks finished.")

            # Perform the first operation immediately on startup
            config_manager.general_log.info("Performing initial operation (creating or resuming orders)...")
            await config_manager.controller.main_init_loop()

            config_manager.general_log.info("Entering main monitoring loop...")
            await config_manager.controller.main_loop()
            # Get the operation interval from the strategy
            operation_interval = config_manager.strategy_instance.get_operation_interval()
            config_manager.general_log.info(
                f"Using operation interval of {operation_interval} seconds for {config_manager.strategy} strategy.")

            flush_timer = time.time()
            operation_timer = time.time()

            while not config_manager.controller.shutdown_event.is_set():
                current_time = time.time()

                if current_time - flush_timer > FLUSH_DELAY:
                    await config_manager.xbridge_manager.dxflushcancelledorders()
                    flush_timer = current_time

                if current_time - operation_timer > operation_interval:
                    await config_manager.controller.main_loop()
                    operation_timer = current_time

                try:
                    # Wait for the shutdown event or until the sleep interval times out
                    await asyncio.wait_for(config_manager.controller.shutdown_event.wait(), timeout=SLEEP_INTERVAL)
                except asyncio.TimeoutError:
                    # This is the normal path, the timeout occurred, so we continue the loop.
                    pass
    except asyncio.CancelledError:
        config_manager.general_log.info("Main loop was cancelled.")
