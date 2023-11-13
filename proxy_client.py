import socket

import config.ccxt_cfg as ccxt_cfg
import definitions.ccxt_def as ccxt_def
from definitions.xbridge_def import rpc_call


def isOpen(ip, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((ip, int(port)))
        s.shutdown(2)
        return True
    except:
        return False


# ['BTC/USDT', 'PIVX/BTC', 'BLOCK/BTC', 'SYS/BTC', 'DASH/BTC', 'DOGE/BTC', 'LTC/BTC', 'RVN/BTC']
symbols_list = ['BTC/USDT', 'PIVX/BTC', 'ETH/BTC', 'SYS/BTC', 'DASH/BTC', 'DOGE/BTC', 'LTC/BTC', 'RVN/BTC']
ccxt_i = ccxt_def.init_ccxt_instance(ccxt_cfg.ccxt_exchange, ccxt_cfg.ccxt_hostname)
result2 = ccxt_def.ccxt_call_fetch_tickers(ccxt_i, symbols_list, proxy=False)
print('direct', type(result2), result2)
result = rpc_call("ccxt_call_fetch_tickers", [symbols_list], rpc_port=2233, debug=0)
print('proxy', type(result), result)
print(isOpen("127.0.0.1", 2233))
