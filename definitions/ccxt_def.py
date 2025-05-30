import json
import socket
import sys
import time

import ccxt

import definitions.bcolors as bcolors
from definitions.rpc import rpc_call


class CCXTManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager  # Store ConfigManager reference

    def init_ccxt_instance(self, exchange, hostname=None, private_api=False, debug_level=1):
        # CCXT instance
        api_key = None
        api_secret = None
        if private_api:
            with open(self.config_manager.ROOT_DIR + '/config/api_keys.local.json') as json_file:
                data_json = json.load(json_file)
                for data in data_json['api_info']:
                    if exchange in data['exchange']:
                        api_key = data['api_key']
                        api_secret = data['api_secret']
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
                    instance.load_markets()
                except Exception as e:
                    self._manage_error(e)
                    exit()
                else:
                    done = True
            return instance
        else:
            return None

    def ccxt_call_fetch_order_book(self, ccxt_o, symbol, limit=25, ignore_timer=False):
        update_cex_orderbook_timer_delay = 2
        if ignore_timer or not ccxt_o.cex_orderbook_timer or time.time() - ccxt_o.cex_orderbook_timer > update_cex_orderbook_timer_delay:
            self.cex_orderbook = self._fetch_order_book(ccxt_o, symbol, limit)
            self.cex_orderbook_timer = time.time()
        return self.cex_orderbook

    def _fetch_order_book(self, ccxt_o, symbol, limit):
        err_count = 0
        while True:
            try:
                result = ccxt_o.fetch_order_book(symbol, limit)
            except Exception as error:
                err_count += 1
                self._manage_error(error, err_count)
            else:
                self._debug_display('ccxt_call_fetch_order_book', [symbol, limit], result)
                return result

    def ccxt_call_fetch_free_balance(self, ccxt_o):
        err_count = 0
        while True:
            try:
                result = ccxt_o.fetch_free_balance()
            except Exception as error:
                err_count += 1
                self._manage_error(error, err_count)
            else:
                self._debug_display('ccxt_call_fetch_free_balance', [], result)
                return result

    def ccxt_call_fetch_tickers(self, ccxt_o, symbols_list, proxy=True):
        start = time.time()
        err_count = 0
        result = None
        while not result:
            try:
                used_proxy = False
                if self.isportopen("127.0.0.1", 2233) and proxy:  # CCXT PROXY
                    result = rpc_call("ccxt_call_fetch_tickers", tuple(symbols_list), rpc_port=2233,
                                      debug=self.config_manager.config_ccxt.debug_level, display=False)
                    used_proxy = True
                else:
                    result = ccxt_o.fetchTickers(symbols_list)
            except Exception as error:
                err_count += 1
                self._manage_error(error, err_count)
            else:
                stop = time.time()
                self._debug_display('ccxt_call_fetch_tickers', str(symbols_list) + ' used_proxy? ' + str(used_proxy),
                                    result,
                                    timer=stop - start)
                return result

    def ccxt_call_fetch_ticker(self, ccxt_o, symbol):
        err_count = 0
        while True:
            try:
                result = ccxt_o.fetch_ticker(symbol)
            except Exception as error:
                err_count += 1
                self._manage_error(error, err_count)
            else:
                self._debug_display('ccxt_call_fetch_ticker', [symbol], result)
                return result

    def isportopen(self, ip, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect((ip, int(port)))
            s.shutdown(2)
            return True
        except:
            return False

    def _manage_error(self, error, err_count=1):
        err_type = type(error).__name__
        msg = f"parent: {str(sys._getframe(1).f_code.co_name)}, error: {str(type(error))}, {str(error)}, {str(err_type)}"
        if self.config_manager.ccxt_log:
            self.config_manager.ccxt_log.error(msg)
        else:
            print(msg)
        if err_type in ["NetworkError", "DDoSProtection", "RateLimitExceeded", "InvalidNonce",
                        "RequestTimeout", "ExchangeNotAvailable", "Errno -3", "AuthenticationError",
                        "Temporary failure in name resolution", "ExchangeError", "BadResponse", "KeyError"]:
            time.sleep(err_count * 1)
        else:
            time.sleep(err_count * 1)

    def _debug_display(self, func, params, result, timer=None):
        if timer is None:
            timer = ''
        else:
            timer = " exec_timer: " + str(round(timer, 2))

        msg = "ccxt_rpc_call( " + str(func[10::]) + ' ' + str(params) + " )" + timer
        print(f"{bcolors.mycolor.OKCYAN}{msg}{bcolors.mycolor.ENDC}")
        if self.config_manager.config_ccxt.debug_level >= 3:
            print(str(result))
