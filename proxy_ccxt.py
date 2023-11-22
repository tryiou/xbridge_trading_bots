import time
from jsonrpclib.SimpleJSONRPCServer import SimpleJSONRPCServer

import config.ccxt_cfg as ccxt_cfg
import definitions.bcolors as bcolors
import definitions.ccxt_def as ccxt_def


class CCXTServer:
    def __init__(self):
        self.symbols_list = []
        self.tickers = {}
        self.ccxt_call_count = 0
        self.ccxt_cache_hit = 0
        self.print_delay = 5
        self.print_timer = None
        self.total_exec_time = time.time()
        self.ccxt_call_fetch_tickers_timer = time.time()
        self.ccxt_i = ccxt_def.init_ccxt_instance(ccxt_cfg.ccxt_exchange, ccxt_cfg.ccxt_hostname)
        self.inprogress = False

    def ccxt_call_fetch_tickers(self, *args):
        refresh_delay = 5
        for symbol in args:
            if symbol not in self.symbols_list:
                self.symbols_list.append(symbol)
        trigger = False
        while self.inprogress == True:
            time.sleep(0.1)
        for symbol in self.symbols_list:
            if symbol not in self.tickers:
                trigger = True
        if time.time() - self.ccxt_call_fetch_tickers_timer > refresh_delay:
            trigger = True
        if trigger:
            self.inprogress = True
            self.ccxt_call_count += 1
            temp_tickers = ccxt_def.ccxt_call_fetch_tickers(self.ccxt_i, self.symbols_list, proxy=False)
            self.tickers = temp_tickers
            self.ccxt_call_fetch_tickers_timer = time.time()
            self.print_metrics()
            self.inprogress = False
        else:
            self.ccxt_cache_hit += 1
        return self.tickers

    def print_metrics(self):
        exec_sec = time.time() - self.total_exec_time
        ccxt_cps = self.ccxt_call_count / exec_sec
        msg = f"exec_sec: {round(exec_sec, 2)} ccxt_cps: {round(ccxt_cps, 2)} ccxt_call_count: {self.ccxt_call_count} ccxt_cache_hit: {self.ccxt_cache_hit}"
        print(f"{bcolors.mycolor.OKGREEN}{msg}{bcolors.mycolor.ENDC}")


def main():
    server = SimpleJSONRPCServer(('localhost', 2233))
    ccxt_server = CCXTServer()
    server.register_function(ccxt_server.ccxt_call_fetch_tickers)
    print("Start server")
    server.serve_forever()


if __name__ == '__main__':
    main()
