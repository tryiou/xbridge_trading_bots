# LOGIC:
# 1/BOT SELL T1 ON DEX AT {CEX MARKETPRICE * (1 + SPREAD)}
# 2/BOT BUY T1 ON DEX AT (min(live_price),max(SOLD PRICE * (1 - SPREAD)))
# 3/LOOP
#
# ONLY ONE AT A TIME, BOT RECORD THE LAST SELL ORDER ON A FILE, LOAD AT START


import time
from threading import Thread
import config.config_coins as config_coins
import definitions.ccxt_def as ccxt_def
import definitions.init as init
import definitions.xbridge_def as xb
import concurrent.futures
import asyncio
import traceback

# Constants
CCXT_PRICE_REFRESH = 2
UPDATE_BALANCES_DELAY = 0.5
FLUSH_DELAY = 15 * 60
MAX_THREADS = 10
OPERATION_INTERVAL = 10  # Main loop operations interval (in seconds)
SLEEP_INTERVAL = 1  # Shorter sleep interval (in seconds)


class General:
    def __init__(self, pairs_dict, tokens_dict, ccxt_i):
        self.pairs_dict = pairs_dict
        self.tokens_dict = tokens_dict
        self.ccxt_i = ccxt_i
        self.timer_main_dx_update_bals = None
        self.ccxt_price_refresh = CCXT_PRICE_REFRESH
        self.delay_main_dx_update_bals = UPDATE_BALANCES_DELAY
        self.disabled_coins = []
        self.ccxt_price_timer = None

    def main_init_loop(self):
        self.main_dx_update_bals()
        self._process_pairs(self.thread_init)

    def main_loop(self):
        self.main_dx_update_bals()
        self._process_pairs(self.thread_loop)

    def _process_pairs(self, target_function):
        threads = []
        start_time = time.perf_counter()

        for counter, (key, pair) in enumerate(self.pairs_dict.items(), start=1):
            self.update_ccxt_prices()
            pair.update_pricing()

            if counter % MAX_THREADS == 0:
                self._join_threads(threads)
                threads = []

            thread = Thread(target=target_function, args=(pair,))
            threads.append(thread)
            thread.start()

            time.sleep(0.1)  # Yield control

        self._join_threads(threads)
        self._report_time(start_time)

    def _join_threads(self, threads):
        for thread in threads:
            thread.join()

    def _report_time(self, start_time):
        end_time = time.perf_counter()
        print(f'Operation took {end_time - start_time:0.2f} second(s) to complete.')

    def update_ccxt_prices(self):
        if self.ccxt_price_timer is None or time.time() - self.ccxt_price_timer > self.ccxt_price_refresh:
            try:
                self._fetch_and_update_prices()
                self.ccxt_price_timer = time.time()
            except Exception as e:
                print(f"Error in update_ccxt_prices: {e}")

    def _fetch_and_update_prices(self):
        custom_coins = config_coins.usd_ticker_custom.keys()
        keys = [self._construct_key(token) for token in self.tokens_dict if token not in custom_coins]
        keys.insert(0, keys.pop(keys.index('BTC/USDT')))

        try:
            tickers = ccxt_def.ccxt_call_fetch_tickers(self.ccxt_i, keys)
            self._update_token_prices(tickers)
        except Exception as e:
            print(f"Error fetching tickers: {e}")

    def _construct_key(self, token):
        return f"{token}/USDT" if token == 'BTC' else f"{token}/BTC"

    def _update_token_prices(self, tickers):
        lastprice_string = self._get_last_price_string()
        for token in [t for t in self.tokens_dict if t not in config_coins.usd_ticker_custom]:
            symbol = f"{self.tokens_dict[token].symbol}/USDT" if self.tokens_dict[token].symbol == 'BTC' else f"{self.tokens_dict[token].symbol}/BTC"
            self._update_token_price(tickers, symbol, lastprice_string, token)

        for token in config_coins.usd_ticker_custom:
            if token in self.tokens_dict:
                self.tokens_dict[token].update_ccxt_price()

    def _get_last_price_string(self):
        ccxt_id = init.my_ccxt.id
        return {
            "kucoin": "last",
            "binance": "lastPrice"
        }.get(ccxt_id, "lastTradeRate")

    def _update_token_price(self, tickers, symbol, lastprice_string, token):
        if symbol in tickers:
            last_price = float(tickers[symbol]['info'][lastprice_string])
            if self.tokens_dict[token].symbol == 'BTC':
                self.tokens_dict[token].usd_price = last_price
                self.tokens_dict[token].ccxt_price = 1
            else:
                self.tokens_dict[token].ccxt_price = last_price
                self.tokens_dict[token].usd_price = float(last_price * self.tokens_dict['BTC'].usd_price)
        else:
            print(f"Missing symbol in tickers: {symbol}")
            self.tokens_dict[token].ccxt_price = None
            self.tokens_dict[token].usd_price = None

    def main_dx_update_bals(self):
        if self._should_update_bals():
            xb_tokens = xb.getlocaltokens()
            with concurrent.futures.ThreadPoolExecutor() as executor:
                executor.map(lambda token: self._update_token_balance(token, xb_tokens), self.tokens_dict)

            self.timer_main_dx_update_bals = time.time()

    def _should_update_bals(self):
        return self.timer_main_dx_update_bals is None or time.time() - self.timer_main_dx_update_bals > self.delay_main_dx_update_bals

    def _update_token_balance(self, token, xb_tokens):
        if xb_tokens and self.tokens_dict[token].symbol in xb_tokens:
            utxos = xb.gettokenutxo(token, used=True)
            bal, bal_free = self._calculate_balances(utxos)
            self.tokens_dict[token].dex_total_balance = bal
            self.tokens_dict[token].dex_free_balance = bal_free
        else:
            self.tokens_dict[token].dex_total_balance = None
            self.tokens_dict[token].dex_free_balance = None

    def _calculate_balances(self, utxos):
        bal = bal_free = 0
        for utxo in utxos:
            if 'amount' in utxo:
                amount = float(utxo['amount'])
                bal += amount
                if 'orderid' in utxo and utxo['orderid'] == '':
                    bal_free += amount
        return bal, bal_free

    def thread_init(self, p):
        try:
            p.init_virtual_order(self.disabled_coins)
            p.dex_create_order()
        except Exception as e:
            print(f"Error in thread_init: {e}")

    def thread_loop(self, p):
        try:
            p.status_check(self.disabled_coins)
        except Exception as e:
            print(f"Error in thread_loop: {e}")


async def main():
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
        print("Received Stop order. Cleaning up...")
        xb.cancelallorders()
        exit()

    except Exception as e:
        print(f"Exception in main loop: {e}")
        traceback.print_exc()
        xb.cancelallorders()
        exit()


def run_async_main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())


def start():
    init.init_pingpong()
    run_async_main()


if __name__ == '__main__':
    start()
