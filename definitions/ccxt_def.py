import socket
import sys
import time

import definitions.bcolors as bcolors
from definitions.xbridge_def import rpc_call
from definitions.yaml_mix import YamlToObject

config = YamlToObject('config/config_ccxt.yaml')


def debug_display(func, params, result, debug=config.debug_level, timer=None):
    if debug >= 2:
        if timer is None:
            timer = ''
        else:
            timer = " exec_timer: " + str(round(timer, 2))

        msg = "ccxt_rpc_call( " + str(func[10::]) + ' ' + str(params) + " )" + timer
        print(f"{bcolors.mycolor.OKCYAN}{msg}{bcolors.mycolor.ENDC}")
        if debug >= 3:
            print(str(result))


def ccxt_manage_error(error, err_count=1):
    from definitions.classes import general_log
    err_type = type(error).__name__
    msg = f"parent: {str(sys._getframe(1).f_code.co_name)},error: {str(type(error))}, {str(error)}, {str(err_type)}"
    # print('parent:', sys._getframe(1).f_code.co_name, type(error), error, err_type)
    if general_log:
        general_log.error(msg)
    else:
        print(msg)
    if (err_type == "NetworkError" or
        err_type == "DDoSProtection" or
        err_type == "RateLimitExceeded" or
        err_type == "InvalidNonce" or
        err_type == "RequestTimeout" or
        err_type == "ExchangeNotAvailable" or
        err_type == "Errno -3" or
        err_type == "AuthenticationError" or
        err_type == "Temporary failure in name resolution" or
        err_type == "ExchangeError" or
        err_type == "BadResponse") or \
            err_type == "KeyError":
        time.sleep(err_count * 1)
    else:
        time.sleep(err_count * 1)


def ccxt_call_fetch_order_book(ccxt_o, symbol, limit=25):
    err_count = 0
    while True:
        try:
            result = ccxt_o.fetch_order_book(symbol, limit)
        except Exception as error:
            err_count += 1
            ccxt_manage_error(error, err_count)
        else:
            debug_display('ccxt_call_fetch_order_book', [symbol, limit], result)
            return result


def ccxt_call_fetch_free_balance(ccxt_o):
    err_count = 0
    while True:
        try:
            result = ccxt_o.fetch_free_balance()
        except Exception as error:
            err_count += 1
            ccxt_manage_error(error, err_count)
        else:
            debug_display('ccxt_call_fetch_free_balance', [], result)
            return result


def isportopen(ip, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((ip, int(port)))
        s.shutdown(2)
        return True
    except:
        return False


def ccxt_call_fetch_tickers(ccxt_o, symbols_list, proxy=True):
    start = time.time()
    err_count = 0
    result = None
    while not result:
        try:
            used_proxy = False
            if isportopen("127.0.0.1", 2233) and proxy:  # CCXT PROXY
                # print('aaa',tuple(symbols_list))
                result = rpc_call("ccxt_call_fetch_tickers", tuple(symbols_list), rpc_port=2233,
                                  debug=config.debug_level, display=False)
                used_proxy = True
            else:
                result = ccxt_o.fetchTickers(symbols_list)
        except Exception as error:
            err_count += 1
            ccxt_manage_error(error, err_count)
        else:
            stop = time.time()
            debug_display('ccxt_call_fetch_tickers', str(symbols_list) + ' used_proxy? ' + str(used_proxy), result,
                          timer=stop - start)
            return result


def ccxt_call_fetch_ticker(ccxt_o, symbol):
    err_count = 0
    while True:
        try:
            result = ccxt_o.fetch_ticker(symbol)
        except Exception as error:
            err_count += 1
            ccxt_manage_error(error, err_count)
        else:
            debug_display('ccxt_call_fetch_ticker', [symbol], result)
            return result
