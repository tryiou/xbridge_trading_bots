# LOGIC:
# 1/BOT SELL T1 ON DEX AT {CEX MARKETPRICE * (1 + SPREAD)}
# 2/BOT BUY T1 ON DEX AT (min(live_price),max(SOLD PRICE * (1 - SPREAD)))
# 3/LOOP
#
# ONLY ONE AT A TIME, BOT RECORD THE LAST SELL ORDER ON A FILE, LOAD AT START
import time
from threading import Thread

import definitions.xlite_endpoint_check as xlite_endpoint_check
import definitions.ccxt_def as ccxt_def
import definitions.init as init
import definitions.xbridge_def as xb
from config.config_pingpong import cc_coins
import concurrent.futures

# from pycallgraph import PyCallGraph
# from pycallgraph.output import GraphvizOutput

class General:
    def __init__(self, pairs_dict, tokens_dict, ccxt_i):
        self.pairs_dict = pairs_dict
        self.tokens_dict = tokens_dict
        self.ccxt_i = ccxt_i
        self.timer_main_dx_update_bals = None
        self.delay_main_dx_update_bals = 0.5
        self.ccxt_price_refresh = 2
        self.disabled_coins = []
        # print(self.pairs_dict)

    def main_init_loop(self):
        self.main_dx_update_bals()
        enable_threading = False
        threads = []
        start_time = time.perf_counter()
        max_threads = 10
        counter = 0
        for key, pair in self.pairs_dict.items():
            self.update_ccxt_prices()
            self.main_dx_update_bals()
            pair.update_pricing()
            if enable_threading:
                t = Thread(target=self.thread_init, args=(pair,))
                threads.append(t)
                t.start()
                counter += 1
                if counter == max_threads:
                    for t in threads:
                        t.join()
                    threads = []
                    counter = 0
                time.sleep(0.1)
            else:
                self.thread_init(pair)
        end_time = time.perf_counter()
        print(f'init took{end_time - start_time: 0.2f} second(s) to complete.')

    def main_loop(self):
        enable_threading = False
        threads = []
        start_time = time.perf_counter()
        max_threads = 10
        counter = 0
        self.main_dx_update_bals()
        for key, pair in self.pairs_dict.items():
            self.update_ccxt_prices()
            if enable_threading:
                t = Thread(target=self.thread_loop, args=(pair,))
                threads.append(t)
                t.start()
                counter += 1
                if counter == max_threads:
                    for t in threads:
                        t.join()
                    threads = []
                    counter = 0
                time.sleep(0.1)
            else:
                self.thread_loop(pair)
        end_time = time.perf_counter()
        print(f'loop took{end_time - start_time: 0.2f} second(s) to complete.')

    def update_ccxt_prices(self):
        global ccxt_price_timer
        if ccxt_price_timer is None or time.time() - ccxt_price_timer > self.ccxt_price_refresh:
            keys = list(self.tokens_dict.keys())
            for x, token in enumerate(keys):
                if token == 'BTC':
                    keys[x] = token + '/USDT'
                else:
                    keys[x] = token + '/BTC'
            keys.insert(0, keys.pop(keys.index('BTC/USDT')))
            done = False
            while not done:
                try:
                    tickers = ccxt_def.ccxt_call_fetch_tickers(self.ccxt_i, keys)
                    for token in self.tokens_dict:
                        if self.tokens_dict[token].symbol == 'BTC':
                            symbol = self.tokens_dict[token].symbol + '/USDT'
                            if tickers and symbol in tickers:
                                self.tokens_dict[token].usd_price = float(tickers[symbol]['info']['lastTradeRate'])
                                self.tokens_dict[token].ccxt_price = 1
                                done = True
                            else:
                                print("update_ccxt_prices, missing symbol in tickers:", [symbol], tickers)
                                self.tokens_dict[token].usd_price = None
                                self.tokens_dict[token].ccxt_price = None
                        else:
                            symbol = self.tokens_dict[token].symbol + '/BTC'
                            if tickers and symbol in tickers:
                                self.tokens_dict[token].ccxt_price = float(tickers[symbol]['info']['lastTradeRate'])
                                self.tokens_dict[token].usd_price = float(
                                    self.tokens_dict[token].ccxt_price * self.tokens_dict['BTC'].usd_price)
                                done = True
                            else:
                                print("update_ccxt_prices, missing symbol in tickers:", [symbol], tickers)
                                self.tokens_dict[token].ccxt_price = None
                                self.tokens_dict[token].usd_price = None
                except Exception as e:
                    print("general.update_ccxt_prices error:", type(e), str(e))
            ccxt_price_timer = time.time()

    def main_dx_update_bals(self):
        if self.timer_main_dx_update_bals is None or time.time() - self.timer_main_dx_update_bals > self.delay_main_dx_update_bals:
            xb_tokens = xb.getlocaltokens()

            def update_token_bal(token):
                if xb_tokens and self.tokens_dict[token].symbol in xb_tokens:
                    utxos = xb.gettokenutxo(token, used=True)
                    bal = 0
                    bal_free = 0
                    for utxo in utxos:
                        if 'amount' in utxo:
                            bal += float(utxo['amount'])
                            if 'orderid' in utxo:
                                if utxo['orderid'] == '':
                                    bal_free += float(utxo['amount'])
                                #else:
                                #    print('no orderid in utxo:\n', utxo)
                            else:
                                print(token, 'no amount in utxo:\n', utxo, '**', utxos)
                                # cc_check.cc_height_check(cc_coins)
                    self.tokens_dict[token].dex_total_balance = bal
                    self.tokens_dict[token].dex_free_balance = bal_free
                else:
                    self.tokens_dict[token].dex_total_balance = None
                    self.tokens_dict[token].dex_free_balance = None

            with concurrent.futures.ThreadPoolExecutor() as executor:
                executor.map(update_token_bal, self.tokens_dict)

            self.timer_main_dx_update_bals = time.time()

    def thread_init(self, p):
        p.init_virtual_order(self.disabled_coins)
        p.dex_create_order()

    def thread_loop(self, p):
        p.status_check(self.disabled_coins)


def main():
    global ccxt_price_timer, disabled_coins

    # graphviz = GraphvizOutput()
    # graphviz.output_file = 'basic.png'
    #
    # with PyCallGraph(output=graphviz):
    ccxt_price_timer = None
    # pairs_dict = init.p
    # tokens_dict = init.t
    general = General(pairs_dict=init.p, tokens_dict=init.t, ccxt_i=init.my_ccxt)
    xb.cancelallorders()
    xb.dxflushcancelledorders()
    # exit()
    flush_timer = time.time()
    cc_check_timer = time.time()
    flush_delay = 15 * 60
    cc_timer = 5 * 60
    general.disabled_coins = xlite_endpoint_check.xlite_endpoint_height_check(cc_coins, display=True)
    general.main_init_loop()
    # test_counter=0
    while 1:
        # test_counter+=1
        # if test_counter >= 3:
        #     break
        if time.time() - cc_check_timer > cc_timer:
            general.disabled_coins = xlite_endpoint_check.xlite_endpoint_height_check(cc_coins)
            cc_check_timer = time.time()
        if time.time() - flush_timer > flush_delay:
            xb.dxflushcancelledorders()
            flush_timer = time.time()
        general.main_loop()
        time.sleep(10)


def start():
    init.init_pingpong()
    main()


if __name__ == '__main__':
    start()
