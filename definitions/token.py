import pickle
import time

import requests

import definitions.xbridge_def as xb
from definitions import init, ccxt_def
from definitions.yaml_mix import YamlToObject

config_coins = YamlToObject('config/config_coins.yaml')


class Token:
    def __init__(self, symbol, strategy, dex_enabled=True):
        self.symbol = symbol
        self.strategy = strategy
        self.dex = DexToken(self, dex_enabled)
        self.cex = CexToken(self)


class DexToken:
    def __init__(self, parent_token, dex_enabled=True):
        self.token = parent_token
        self.enabled = dex_enabled
        self.address = None
        self.total_balance = None
        self.free_balance = None
        self.read_address()

    def _get_file_path(self):
        return f"{init.ROOT_DIR}/data/{self.token.strategy}_{self.token.symbol}_addr.pic"

    def read_address(self):
        if not self.enabled:
            return

        file_path = self._get_file_path()
        try:
            with open(file_path, 'rb') as fp:
                self.address = pickle.load(fp)
        except FileNotFoundError:
            general_log.info(f"File not found: {file_path}")
            self.request_addr()
        except (pickle.PickleError, Exception) as e:
            general_log.error(f"Error reading XB address from file: {file_path} - {type(e).__name__}: {e}")
            self.request_addr()

    def write_address(self):
        if not self.enabled:
            return

        file_path = self._get_file_path()
        try:
            with open(file_path, 'wb') as fp:
                pickle.dump(self.address, fp)
        except (pickle.PickleError, Exception) as e:
            general_log.error(f"Error writing XB address to file: {file_path} - {type(e).__name__}: {e}")

    def request_addr(self):
        try:
            address = xb.getnewtokenadress(self.token.symbol)[0]
            self.address = address
            general_log.info(f"dx_request_addr: {self.token.symbol}, {address}")
            self.write_address()
        except Exception as e:
            general_log.error(f"Error requesting XB address for {self.token.symbol}: {type(e).__name__}: {e}")


class CexToken:
    def __init__(self, parent_token):
        self.token = parent_token
        self.cex_price = None
        self.usd_price = None
        self.cex_price_timer = None
        self.cex_total_balance = None
        self.cex_free_balance = None

    def update_price(self, display=False):
        if (self.cex_price_timer is not None and
                time.time() - self.cex_price_timer <= 2):
            if display:
                print('Token.update_ccxt_price()', 'too fast call?', self.token.symbol)
            return

        cex_symbol = "BTC/USD" if self.token.symbol == "BTC" else f"{self.token.symbol}/BTC"
        lastprice_string = {
            'kucoin': 'last',
            'binance': 'lastPrice'
        }.get(init.my_ccxt.id, 'lastTradeRate')

        def fetch_ticker(cex_symbol):
            for _ in range(3):  # Attempt to fetch the ticker up to 3 times
                try:
                    result = float(ccxt_def.ccxt_call_fetch_ticker(init.my_ccxt, cex_symbol)['info'][lastprice_string])
                    return result
                except Exception as e:
                    general_log.error(f"fetch_ticker: {cex_symbol} error: {type(e).__name__}: {e}")
                    time.sleep(1)  # Sleep for a second before retrying
            return None

        if self.token.symbol in config_coins.usd_ticker_custom:
            result = config_coins.usd_ticker_custom[self.token.symbol] / init.t['BTC'].cex.usd_price
        elif cex_symbol in init.my_ccxt.symbols:
            result = fetch_ticker(cex_symbol)
        else:
            general_log.info(f"{cex_symbol} not in cex {str(init.my_ccxt)}")
            self.usd_price = None
            self.cex_price = None
            return

        if result is not None:
            self.cex_price = 1 if self.token.symbol == "BTC" else result
            self.usd_price = result if self.token.symbol == "BTC" else result * init.t['BTC'].cex.usd_price
            self.cex_price_timer = time.time()
            general_log.debug(
                f"new pricing {self.token.symbol} {self.cex_price} {self.usd_price} USD PRICE {init.t['BTC'].cex.usd_price}")
        else:
            self.usd_price = None
            self.cex_price = None

    def update_block_ticker(self):
        count = 0
        done = False
        used_proxy = False
        while not done:
            count += 1
            try:
                if ccxt_def.isportopen("127.0.0.1", 2233):
                    result = xb.rpc_call("fetch_ticker_block", rpc_port=2233, debug=2, display=False)
                    used_proxy = True
                else:
                    response = requests.get('https://min-api.cryptocompare.com/data/price?fsym=BLOCK&tsyms=BTC')
                    if response.status_code == 200:
                        result = response.json().get('BTC')
            except Exception as e:
                general_log.error(f"update_ccxt_price: BLOCK error({count}): {type(e).__name__}: {e}")
                time.sleep(count)
            else:
                if isinstance(result, float):
                    general_log.info(f"Updated BLOCK ticker: {result} BTC proxy: {used_proxy}")
                    return result
                time.sleep(count)
