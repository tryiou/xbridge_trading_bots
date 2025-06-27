import asyncio
import concurrent.futures
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

    async def process_pairs(self, target_function):
        futures = []
        loop = asyncio.get_running_loop()
        for pair in self.pairs_dict.values():
            if self.controller.stop_order:
                break
            # If target_function is async, await it directly. If blocking, run in executor.
            future = target_function(pair) if asyncio.iscoroutinefunction(target_function) else loop.run_in_executor(
                None, target_function, pair)
            futures.append(future)
        # Wait for all tasks to complete
        if futures:
            await asyncio.gather(*futures)


class BalanceManager:
    def __init__(self, tokens_dict, config_manager, loop):
        self.tokens_dict = tokens_dict
        self.config_manager = config_manager
        self.timer_main_dx_update_bals = None
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS)  # Use a dedicated executor
        self.loop = loop

    async def update_balances(self):
        if self._should_update_bals():
            # Offload blocking xb.getlocaltokens to the thread pool
            xb_tokens = await self.loop.run_in_executor(self.executor, self.config_manager.xbridge_manager.getlocaltokens)

            futures = []
            for token_data in self.tokens_dict.values():
                # Offload blocking _update_token_balance_blocking to the thread pool
                futures.append(self.loop.run_in_executor(self.executor, self._update_token_balance_blocking, token_data,
                                                         xb_tokens))

            if futures:
                await asyncio.gather(*futures)  # Wait for all balance updates to complete

            self.timer_main_dx_update_bals = time.time()

    def _should_update_bals(self):
        return self.timer_main_dx_update_bals is None or time.time() - self.timer_main_dx_update_bals > UPDATE_BALANCES_DELAY

    def _update_token_balance_blocking(self, token_data, xb_tokens):
        if xb_tokens and token_data.symbol in xb_tokens:
            utxos = self.config_manager.xbridge_manager.gettokenutxo(token_data.symbol, used=True)  # This is a blocking call
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
        custom_coins = self.config_manager.config_coins.usd_ticker_custom.keys()
        keys = [self._construct_key(token) for token in self.tokens_dict if token not in custom_coins]

        try:
            # Offload blocking CCXT call to the thread pool
            tickers = await self.loop.run_in_executor(None, self.config_manager.ccxt_manager.ccxt_call_fetch_tickers,
                                                      self.ccxt_i, keys)
            # _update_token_prices is a synchronous method, but it contains blocking calls
            # It needs to be run in an executor if token_data.cex.update_price() is blocking.
            await self.loop.run_in_executor(None, self._update_token_prices_blocking, tickers)
        except Exception as e:
            self.config_manager.general_log.error(f"Error fetching tickers: {e}", exc_info=True)

    def _construct_key(self, token):
        return f"{token}/USDT" if token == 'BTC' else f"{token}/BTC"

    def _update_token_prices_blocking(self, tickers):
        # This function remains blocking, but is now explicitly run in an executor thread
        lastprice_string = self._get_last_price_string()
        for token, token_data in sorted(self.tokens_dict.items(), key=lambda item: (item[0] != 'BTC', item[0])):
            if self.main_controller.stop_order:
                return
            if token not in self.config_manager.config_coins.usd_ticker_custom:
                symbol = f"{token_data.symbol}/USDT" if token_data.symbol == 'BTC' else f"{token_data.symbol}/BTC"  # This is a blocking call
                self._update_token_price_blocking(tickers, symbol, lastprice_string, token_data)

        for token in self.config_manager.config_coins.usd_ticker_custom:
            if self.main_controller.stop_order:
                return
            if token in self.tokens_dict:
                self.tokens_dict[token].cex.update_price()  # This is a blocking call

    def _get_last_price_string(self):
        return {
            "kucoin": "last",
            "binance": "lastPrice"
        }.get(self.config_manager.my_ccxt.id, "lastTradeRate")

    # def _update_token_price(self, tickers, symbol, lastprice_string, token_data):
    def _update_token_price_blocking(self, tickers, symbol, lastprice_string, token_data):
        # This function remains blocking, but is now explicitly run in an executor thread
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
        self.stop_order = False
        self.loop = loop

        self.price_handler = PriceHandler(self, self.loop)
        self.balance_manager = BalanceManager(self.tokens_dict, self.config_manager, self.loop)
        self.processor = TradingProcessor(self)
        self.config_manager.strategy_instance.controller = self  # Pass controller to strategy

    async def main_init_loop(self):
        await self.balance_manager.update_balances()  # Await the async call
        await self.price_handler.update_ccxt_prices()  # Await the async call

        futures = []
        for pair in self.pairs_dict.values():
            if self.stop_order:
                return
            if self.config_manager.strategy_instance.should_update_cex_prices():
                futures.append(self.loop.run_in_executor(None, pair.cex.update_pricing))

        if futures:
            await asyncio.gather(*futures)

        await self.processor.process_pairs(self.config_manager.strategy_instance.thread_init_blocking_action)

    async def main_loop(self):
        start_time = time.perf_counter()
        await self.balance_manager.update_balances()  # Await the async call
        await self.price_handler.update_ccxt_prices()  # Await the async call

        futures = []

        for pair in self.pairs_dict.values():
            if self.stop_order:
                return
            if self.config_manager.strategy_instance.should_update_cex_prices():
                futures.append(self.loop.run_in_executor(None, pair.cex.update_pricing))
        if futures:
            await asyncio.gather(*futures)

        await self.processor.process_pairs(self.config_manager.strategy_instance.thread_loop_blocking_action)
        self._report_time(start_time)

    def _report_time(self, start_time):
        end_time = time.perf_counter()
        self.config_manager.general_log.info(f'Operation took {end_time - start_time:0.2f} second(s) to complete.')

    def thread_init_blocking(self, pair):
        try:
            self.config_manager.strategy_instance.thread_loop_blocking_action(pair)
        except Exception as e:
            self.config_manager.general_log.error(f"Error in thread_loop: {e}", exc_info=True)


def run_async_main(config_manager, loop=None):
    """Runs the main loop using a new event loop."""
    if loop is None:  # Create a new loop if one isn't provided (e.g., for console execution)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    controller = None  # Initialize controller to None for finally block
    try:
        controller = MainController(config_manager, loop)
        config_manager.controller = controller
        loop.run_until_complete(main(config_manager, loop))
    except (SystemExit, KeyboardInterrupt):
        config_manager.general_log.info("Received Stop order. Cleaning up...")
        config_manager.xbridge_manager.cancelallorders()
        # Ensure executors are shut down
        if controller:
            if hasattr(controller.processor, 'executor'):
                controller.processor.executor.shutdown(wait=True)
            if hasattr(controller.balance_manager, 'executor'):
                controller.balance_manager.executor.shutdown(wait=True)
            if hasattr(controller.price_handler, 'executor'):
                controller.price_handler.executor.shutdown(wait=True)
            if hasattr(controller, 'executor'):
                controller.executor.shutdown(wait=True)
    except Exception as e:
        config_manager.general_log.error(f"Exception in run_async_main: {e}")
        traceback.print_exc()
        config_manager.xbridge_manager.cancelallorders()
        # Ensure executors are shut down
        if controller:
            if hasattr(controller.processor, 'executor'):
                controller.processor.executor.shutdown(wait=True)
            if hasattr(controller.balance_manager, 'executor'):
                controller.balance_manager.executor.shutdown(wait=True)
            if hasattr(controller.price_handler, 'executor'):
                controller.price_handler.executor.shutdown(wait=True)
            if hasattr(controller, 'executor'):
                controller.executor.shutdown(wait=True)
        raise  # Re-raise to allow graceful exit from main_pingpong.py
    finally:
        # Ensure the loop is closed
        if loop and loop.is_running():  # Check if loop is still running before closing
            loop.close()


async def main(config_manager, loop):
    import aiohttp  # Import aiohttp
    """Generic main loop that works with any strategy."""
    async with aiohttp.ClientSession() as session:
        # Pass the session to the controller and strategy if needed
        config_manager.controller.http_session = session
        if hasattr(config_manager.strategy_instance, 'http_session'):
            config_manager.strategy_instance.http_session = session

        await config_manager.controller.main_init_loop()

        # Perform the first operation immediately on startup
        config_manager.general_log.info("Performing initial operation...")
        await config_manager.controller.main_loop()

        # Get the operation interval from the strategy
        operation_interval = config_manager.strategy_instance.get_operation_interval()
        config_manager.general_log.info(
            f"Using operation interval of {operation_interval} seconds for {config_manager.strategy} strategy.")

        flush_timer = time.time()
        operation_timer = time.time()

        while True:
            current_time = time.time()

            if config_manager.controller and config_manager.controller.stop_order:
                config_manager.general_log.info("Received stop_order")
                break

            # Offload blocking xb.dxflushcancelledorders to the thread pool
            if current_time - flush_timer > FLUSH_DELAY:
                await loop.run_in_executor(None, config_manager.xbridge_manager.dxflushcancelledorders)
                flush_timer = current_time

            if current_time - operation_timer > operation_interval:
                await config_manager.controller.main_loop()
                operation_timer = current_time

            await asyncio.sleep(SLEEP_INTERVAL)
