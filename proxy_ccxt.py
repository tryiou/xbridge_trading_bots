import time
from datetime import datetime, timedelta
# logging.basicConfig(level=logging.INFO)
from functools import lru_cache, wraps

from jsonrpclib.SimpleJSONRPCServer import SimpleJSONRPCServer

import config.ccxt_cfg as ccxt_cfg
import definitions.bcolors as bcolors
import definitions.ccxt_def as ccxt_def


def ccxt_call_fetch_tickers(*args):
    global symbols_list, tickers, ccxt_call_fetch_tickers_timer, ccxt_call_count, ccxt_cache_hit, print_timer
    refresh_delay = 4
    for symbol in args:
        if symbol not in symbols_list:
            symbols_list.append(symbol)
    trigger = False
    for symbol in symbols_list:
        if symbol not in tickers:
            trigger = True
    if time.time() - ccxt_call_fetch_tickers_timer > refresh_delay:
        trigger = True
    if trigger:
        ccxt_call_count += 1
        # temp_tickers = fetch_tickers_xcloud(symbols_list).json()
        temp_tickers = ccxt_def.ccxt_call_fetch_tickers(ccxt_i, symbols_list, proxy=False)
        tickers = temp_tickers
        ccxt_call_fetch_tickers_timer = time.time()
        exec_sec = time.time() - total_exec_time
        ccxt_cps = ccxt_call_count / exec_sec
        msg = "exec_sec: " + str(round(exec_sec, 2)) + " ccxt_cps:" + str(round(ccxt_cps, 2)) + " ccxt_call_count: " + \
              str(ccxt_call_count) + " ccxt_cache_hit: " + str(ccxt_cache_hit)
        print(f"{bcolors.mycolor.OKGREEN}{msg}{bcolors.mycolor.ENDC}")
    else:
        ccxt_cache_hit += 1
    # if print_timer is None or time.time() - print_timer > print_delay:
    #     exec_sec = time.time() - total_exec_time
    #     ccxt_cps = ccxt_call_count / exec_sec
    #
    #
    #     print_timer = time.time()
    return tickers


def main():
    server = SimpleJSONRPCServer(('localhost', 2233))
    server.register_function(ccxt_call_fetch_tickers)
    print("Start server")
    server.serve_forever()


if __name__ == '__main__':
    symbols_list = []
    tickers = {}
    ccxt_call_count = 0
    ccxt_cache_hit = 0
    print_delay = 5
    print_timer = None
    # in_progress = False
    total_exec_time = time.time()
    ccxt_call_fetch_tickers_timer = time.time()
    ccxt_i = ccxt_def.init_ccxt_instance(ccxt_cfg.ccxt_exchange, ccxt_cfg.ccxt_hostname)
    main()
