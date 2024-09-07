# LOGIC:
# 1/BOT SELL T1 ON DEX AT {CEX MARKETPRICE * (1 + SPREAD)}
# 2/BOT BUY T1 ON DEX AT (min(live_price),max(SOLD PRICE * (1 - SPREAD)))
# 3/LOOP
#
# ONLY ONE AT A TIME, BOT RECORD THE LAST SELL ORDER ON A FILE, LOAD AT START

import asyncio
import concurrent.futures
import logging
import time
import traceback
from threading import Thread

import definitions.ccxt_def as ccxt_def
import definitions.init as init
import definitions.xbridge_def as xb
from definitions.yaml_mix import YamlToObject

# Constants
CCXT_PRICE_REFRESH = 2
UPDATE_BALANCES_DELAY = 0.5
FLUSH_DELAY = 15 * 60
MAX_THREADS = 5
OPERATION_INTERVAL = 10  # Main loop operations interval (in seconds)
SLEEP_INTERVAL = 1  # Shorter sleep interval (in seconds)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class General:
    def __init__(self, pairs_dict, tokens_dict, ccxt_i):
        self.pairs_dict = pairs_dict
        self.tokens_dict = tokens_dict
        self.ccxt_i = ccxt_i
        self.config_coins = YamlToObject('config/config_coins.yaml')
        self.timer_main_dx_update_bals = None
        self.ccxt_price_timer = None
        self.disabled_coins = []

    def main_init_loop(self):
        """Initial loop to update balances and initialize trading pairs."""
        self.main_dx_update_bals()
        self._process_pairs(self.thread_init)

    def main_loop(self):
        """Main loop that continuously updates balances and processes trading pairs."""
        start_time = time.perf_counter()
        self.main_dx_update_bals()
        self._process_pairs(self.thread_loop)
        self._report_time(start_time)

    def _process_pairs(self, target_function):
        """Processes trading pairs concurrently using threads."""
        threads = []

        for counter, pair in enumerate(self.pairs_dict.values(), start=1):
            self.update_ccxt_prices()
            pair.update_pricing()

            thread = Thread(target=target_function, args=(pair,))
            threads.append(thread)
            thread.start()

            if counter % MAX_THREADS == 0:
                self._join_threads(threads)
                threads = []

            # time.sleep(0.1)  # Yield control

        self._join_threads(threads)

    def _join_threads(self, threads):
        """Joins all active threads."""
        for thread in threads:
            thread.join()

    def _report_time(self, start_time):
        """Reports the time taken to complete an operation."""
        end_time = time.perf_counter()
        logging.info(f'Operation took {end_time - start_time:0.2f} second(s) to complete.')

    def update_ccxt_prices(self):
        """Updates CCXT prices if the refresh interval has passed."""
        if self.ccxt_price_timer is None or time.time() - self.ccxt_price_timer > CCXT_PRICE_REFRESH:
            try:
                self._fetch_and_update_prices()
                self.ccxt_price_timer = time.time()
            except Exception as e:
                logging.error(f"Error in update_ccxt_prices: {e}", exc_info=True)

    def _fetch_and_update_prices(self):
        """Fetches and updates token prices from CCXT."""
        custom_coins = self.config_coins.usd_ticker_custom.keys()
        keys = [self._construct_key(token) for token in self.tokens_dict if token not in custom_coins]

        try:
            tickers = ccxt_def.ccxt_call_fetch_tickers(self.ccxt_i, keys)
            self._update_token_prices(tickers)
        except Exception as e:
            logging.error(f"Error fetching tickers: {e}", exc_info=True)

    def _construct_key(self, token):
        """Constructs the ticker key for a given token."""
        return f"{token}/USDT" if token == 'BTC' else f"{token}/BTC"

    def _update_token_prices(self, tickers):
        """Updates the prices of tokens based on fetched tickers."""
        lastprice_string = self._get_last_price_string()
        for token, token_data in self.tokens_dict.items():
            if token not in self.config_coins.usd_ticker_custom:
                symbol = f"{token_data.symbol}/USDT" if token_data.symbol == 'BTC' else f"{token_data.symbol}/BTC"
                self._update_token_price(tickers, symbol, lastprice_string, token_data)

        for token in self.config_coins.usd_ticker_custom:
            if token in self.tokens_dict:
                self.tokens_dict[token].update_ccxt_price()

    def _get_last_price_string(self):
        """Determines the appropriate last price string based on the exchange."""
        return {
            "kucoin": "last",
            "binance": "lastPrice"
        }.get(init.my_ccxt.id, "lastTradeRate")

    def _update_token_price(self, tickers, symbol, lastprice_string, token_data):
        """Updates the price of a specific token."""
        if symbol in tickers:
            last_price = float(tickers[symbol]['info'][lastprice_string])
            if token_data.symbol == 'BTC':
                token_data.usd_price = last_price
                token_data.ccxt_price = 1
            else:
                token_data.ccxt_price = last_price
                token_data.usd_price = last_price * self.tokens_dict['BTC'].usd_price
        else:
            logging.warning(f"Missing symbol in tickers: {symbol}")
            token_data.ccxt_price = None
            token_data.usd_price = None

    def main_dx_update_bals(self):
        """Main method for updating DEX balances."""
        if self._should_update_bals():
            xb_tokens = xb.getlocaltokens()
            with concurrent.futures.ThreadPoolExecutor() as executor:
                executor.map(lambda token: self._update_token_balance(token, xb_tokens), self.tokens_dict.values())

            self.timer_main_dx_update_bals = time.time()

    def _should_update_bals(self):
        """Determines whether it's time to update balances."""
        return self.timer_main_dx_update_bals is None or time.time() - self.timer_main_dx_update_bals > UPDATE_BALANCES_DELAY

    def _update_token_balance(self, token_data, xb_tokens):
        """Updates the balance for a specific token."""
        if xb_tokens and token_data.symbol in xb_tokens:
            utxos = xb.gettokenutxo(token_data.symbol, used=True)
            bal, bal_free = self._calculate_balances(utxos)
            token_data.dex_total_balance = bal
            token_data.dex_free_balance = bal_free
        else:
            token_data.dex_total_balance = None
            token_data.dex_free_balance = None

    def _calculate_balances(self, utxos):
        """Calculates the total and free balances from UTXOs."""
        bal = bal_free = 0
        for utxo in utxos:
            amount = float(utxo.get('amount', 0))
            bal += amount
            if not utxo.get('orderid'):
                bal_free += amount
        return bal, bal_free

    def thread_init(self, pair):
        """Thread function for initializing orders."""
        try:
            pair.init_virtual_order(self.disabled_coins)
            pair.dex_create_order()
        except Exception as e:
            logging.error(f"Error in thread_init: {e}", exc_info=True)

    def thread_loop(self, pair):
        """Thread function for checking order status."""
        try:
            pair.status_check(self.disabled_coins)
        except Exception as e:
            logging.error(f"Error in thread_loop: {e}", exc_info=True)


async def main():
    """Main asynchronous function to start the trading operations."""
    try:
        general = General(pairs_dict=init.p, tokens_dict=init.t, ccxt_i=init.my_ccxt)
        xb.cancelallorders()
        xb.dxflushcancelledorders()

        flush_timer = time.time()
        operation_timer = time.time()

        general.main_init_loop()

        while True:
            current_time = time.time()

            if current_time - flush_timer > FLUSH_DELAY:
                xb.dxflushcancelledorders()
                flush_timer = current_time

            if current_time - operation_timer > OPERATION_INTERVAL:
                general.main_loop()
                operation_timer = current_time

            await asyncio.sleep(SLEEP_INTERVAL)

    except (SystemExit, KeyboardInterrupt):
        logging.info("Received Stop order. Cleaning up...")
        xb.cancelallorders()
        exit()

    except Exception as e:
        logging.error(f"Exception in main loop: {e}", exc_info=True)
        traceback.print_exc()
        xb.cancelallorders()
        exit()


def run_async_main():
    """Runs the main asynchronous function using a new event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())


def start():
    """Initializes the application and starts the main process."""
    init.init_pingpong()
    run_async_main()


if __name__ == '__main__':
    start()
