# from utils import dxbottools
import logging
import os
import pickle
import time

import requests
import yaml

import definitions.bcolors as bcolors
import definitions.ccxt_def as ccxt_def
import definitions.init as init
import definitions.xbridge_def as xb
from definitions.logger import setup_logging
from definitions.yaml_mix import YamlToObject

general_log = None
trade_log = None


def setup_logger(strategy=None):
    global general_log, trade_log

    if general_log and trade_log:
        # Loggers are already set up, no need to do it again
        return

    if strategy:
        general_log = setup_logging(name="GENERAL_LOG",
                                    log_file=init.ROOT_DIR + '/logs/' + strategy + '_general.log',
                                    level=logging.INFO, console=True)
        general_log.propagate = False
        trade_log = setup_logging(name="TRADE_LOG", log_file=init.ROOT_DIR + '/logs/' + strategy + '_trade.log',
                                  level=logging.INFO,
                                  console=False)
    else:
        print("setup_logger(strategy=None)")
        os._exit(1)


# logging.basicConfig(level=logging.INFO)

star_counter = 0

config_coins = YamlToObject('config/config_coins.yaml')


class ConfigPP:
    def __init__(self, config_data=None):
        # Initialize with None values
        self.debug_level = None
        self.ttk_theme = None
        self.user_pairs = None
        self.price_variation_tolerance = None
        self.sell_price_offset = None
        self.usd_amount_default = None
        self.usd_amount_custom = None
        self.spread_default = None
        self.spread_custom = None

        # Override with provided config_data
        if config_data:
            self.update_config(config_data)
        else:
            # Set default values if no config_data provided
            self.set_defaults()

    def set_defaults(self):
        self.debug_level = 2
        self.ttk_theme = "darkly"
        self.user_pairs = ["BLOCK/LTC", "LTC/BLOCK"]
        self.price_variation_tolerance = 0.02
        self.sell_price_offset = 0.05
        self.usd_amount_default = 1
        self.usd_amount_custom = {
            "DASH/BLOCK": 21,
            "BLOCK/DASH": 19
        }
        self.spread_default = 0.05
        self.spread_custom = {
            "BLOCK/LTC": 0.04,
            "LTC/BLOCK": 0.04
        }

    def update_config(self, config_data):
        # Update only with the provided values
        if 'debug_level' in config_data:
            self.debug_level = config_data['debug_level']
        if 'ttk_theme' in config_data:
            self.ttk_theme = config_data['ttk_theme']
        if 'user_pairs' in config_data:
            self.user_pairs = config_data['user_pairs']
        if 'price_variation_tolerance' in config_data:
            self.price_variation_tolerance = config_data['price_variation_tolerance']
        if 'sell_price_offset' in config_data:
            self.sell_price_offset = config_data['sell_price_offset']
        if 'usd_amount_default' in config_data:
            self.usd_amount_default = config_data['usd_amount_default']
        if 'usd_amount_custom' in config_data:
            self.usd_amount_custom = config_data['usd_amount_custom']
        if 'spread_default' in config_data:
            self.spread_default = config_data['spread_default']
        if 'spread_custom' in config_data:
            self.spread_custom = config_data['spread_custom']

    def get(self, key, default=None):
        """Return the value for the given key or a default value if the key does not exist."""
        return getattr(self, key, default)

    @staticmethod
    def load_config(filename):
        if not os.path.exists(filename):
            # File does not exist, save default configuration
            default_config = ConfigPP()
            ConfigPP.save_config(default_config, filename)

        with open(filename, 'r') as file:
            config_data = yaml.safe_load(file)
        return ConfigPP(config_data)

    @staticmethod
    def save_config(config, filename):
        with open(filename, 'w') as file:
            yaml.dump(config.__dict__, file)  # Save the instance's dictionary to file


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


class Pair:
    def __init__(self, token1: Token, token2: Token, amount_token_to_sell=None, min_sell_price_usd=None,
                 ccxt_sell_price_upscale=None,
                 strategy=None, dex_enabled=True, partial_percent=None):
        self.strategy = strategy  # e.g., arbtaker, pingpong, basic_seller
        self.t1 = token1
        self.t2 = token2
        self.symbol = f'{self.t1.symbol}/{self.t2.symbol}'
        self.disabled = False
        self.variation = None
        self.dex_enabled = dex_enabled
        self.amount_token_to_sell = amount_token_to_sell
        self.min_sell_price_usd = min_sell_price_usd
        self.ccxt_sell_price_upscale = ccxt_sell_price_upscale
        self.dex = DexPair(self, partial_percent)
        self.cex = CexPair(self)


class DexPair:
    # Constants for status codes
    STATUS_OPEN = 0
    STATUS_FINISHED = 1
    STATUS_OTHERS = 2
    STATUS_ERROR_SWAP = -1
    STATUS_CANCELLED_WITHOUT_CALL = -2

    PRICE_VARIATION_TOLERANCE_DEFAULT = 0.01

    def __init__(self, pair, partial_percent):
        self.pair = pair
        self.t1 = pair.t1
        self.t2 = pair.t2
        self.symbol = pair.symbol
        self.order_history = None
        self.current_order = None  # Virtual order
        self.disabled = False
        self.variation = None
        self.partial_percent = partial_percent
        self.orderbook = None
        self.orderbook_timer = None
        self.order = None
        self.read_last_order_history()

    def update_dex_orderbook(self):
        self.orderbook = xb.dxgetorderbook(detail=3, maker=self.t1.symbol, taker=self.t2.symbol)
        self.orderbook.pop('detail', None)

    def read_last_order_history(self):

        if not self.pair.dex_enabled:
            return
        file_path = f"{init.ROOT_DIR}/data/{self.pair.strategy}_{self.t1.symbol}_{self.t2.symbol}_last_order.pic"
        try:
            with open(file_path, 'rb') as fp:
                self.order_history = pickle.load(fp)
        except FileNotFoundError:
            general_log.info(f"File not found: {file_path}")
        except Exception as e:
            general_log.error(f"read_pair_last_order_history: {type(e)}, {e}")
            self.order_history = None

    def write_last_order_history(self):
        file_path = f"{init.ROOT_DIR}/data/{self.pair.strategy}_{self.t1.symbol}_{self.t2.symbol}_last_order.pic"
        try:
            with open(file_path, 'wb') as fp:
                pickle.dump(self.order_history, fp)
        except Exception as e:
            general_log.error(f"error write_pair_last_order_history: {type(e)}, {e}")

    def create_virtual_sell_order(self, display=True, manual_dex_price=None):
        self.current_order = self._build_sell_order(manual_dex_price)
        if display:
            general_log.info(f"Created virtual sell order: {self.current_order}")

    def _build_sell_order(self, manual_dex_price):
        # try:
        price = self._calculate_sell_price(manual_dex_price)
        amount, spread = self._determine_amount_and_spread()
        general_log.info(f"_build_sell_order: {price} {amount} {spread}")
        order = {
            'symbol': self.symbol,
            'manual_dex_price': bool(manual_dex_price),
            'side': 'SELL',
            'maker': self.t1.symbol,
            'maker_address': self.t1.dex.address,
            'taker': self.t2.symbol,
            'taker_address': self.t2.dex.address,
            'type': 'partial' if self.partial_percent else 'exact',
            'maker_size': amount,
            'taker_size': amount * (price * (1 + spread)),
            'dex_price': (amount * (price * (1 + spread))) / amount,
            'org_pprice': price,
            'org_t1price': self.t1.cex.cex_price,
            'org_t2price': self.t2.cex.cex_price,
        }
        if self.partial_percent:
            order['minimum_size'] = amount * self.partial_percent
        return order
        # except Exception as e:
        #     general_log.error(f"Error in create_virtual_sell_order: {type(e).__name__}, {e}")
        #     exit()

    def _calculate_sell_price(self, manual_dex_price):
        if manual_dex_price:
            return manual_dex_price
        if self.pair.strategy == 'basic_seller':
            if self.pair.min_sell_price_usd and self.t1.cex.usd_price < self.pair.min_sell_price_usd:
                return self.pair.min_sell_price_usd / self.t2.cex.usd_price
        return self.pair.cex.price

    def _determine_amount_and_spread(self):
        if self.pair.strategy == 'basic_seller':
            return self.pair.amount_token_to_sell, self.pair.ccxt_sell_price_upscale

        usd_amount = init.config_pp.usd_amount_custom.get(self.symbol, init.config_pp.usd_amount_default)
        amount = usd_amount / (self.t1.cex.cex_price * init.t['BTC'].cex.usd_price)
        spread = init.config_pp.sell_price_offset
        return amount, spread

    def create_virtual_buy_order(self, display=True, manual_dex_price=False):
        if self.pair.strategy != 'pingpong':
            general_log.error(
                f"Bot strategy is {self.pair.strategy}, no rule for this strat on create_dex_virtual_buy_order")
            return

        try:
            self.current_order = self._build_buy_order(manual_dex_price)
            if display:
                general_log.info(f"Created virtual buy order: {self.current_order}")
        except Exception as e:
            general_log.error(f"Error in create_virtual_buy_order: {type(e).__name__}, {e}")
            os._exit(1)

    def _build_buy_order(self, manual_dex_price):
        price = self._determine_buy_price(manual_dex_price)
        amount = float(self.order_history['maker_size'])
        spread = init.config_pp.spread_custom.get(self.symbol, init.config_pp.spread_default)
        return {
            'symbol': self.symbol,
            'manual_dex_price': manual_dex_price,
            'side': 'BUY',
            'maker': self.t2.symbol,
            'maker_size': amount * price * (1 - spread),
            'maker_address': self.t2.dex.address,
            'taker': self.t1.symbol,
            'taker_size': amount,
            'taker_address': self.t1.dex.address,
            'type': 'exact',
            'dex_price': (amount * price * (1 - spread)) / amount,
            'org_pprice': price,
            'org_t1price': self.t1.cex.cex_price,
            'org_t2price': self.t2.cex.cex_price,
        }

    def _determine_buy_price(self, manual_dex_price):
        if manual_dex_price:
            return min(self.pair.cex.price, self.order_history['dex_price'])
        return self.pair.cex.price

    def check_price_in_range(self, display=False):
        self.variation = None
        price_variation_tolerance = self._get_price_variation_tolerance()

        if self.current_order.get('side') and self.current_order['manual_dex_price']:
            var = self._calculate_variation_based_on_side()
        else:
            var = self._calculate_default_variation(price_variation_tolerance)

        self._set_variation(var)
        if display:
            self._log_price_check(var)

        return self._is_price_in_range(var, price_variation_tolerance)

    def _get_price_variation_tolerance(self):
        if self.pair.strategy == 'pingpong':
            return init.config_pp.price_variation_tolerance
        if self.pair.strategy == 'basic_seller':
            return self.PRICE_VARIATION_TOLERANCE_DEFAULT
        return None

    def _calculate_variation_based_on_side(self):
        # LOCK PRICE TO POSITIVE ACTION ONLY, PINGPONG DO NOT REBUY UNDER SELL PRICE
        if self.current_order['side'] == 'BUY' and self.pair.cex.price < self.order_history['org_pprice']:
            return float(self.pair.cex.price / self.current_order['org_pprice'])
        # SELL SIDE FLOAT ON CURRENT PRICE
        if self.current_order['side'] == 'SELL':  # and self.price > self.order_history['org_pprice'] TO PRUNE ? DEBUG ?
            return float(self.pair.cex.price / self.current_order['org_pprice'])
        else:
            return 1

    def _calculate_default_variation(self, price_variation_tolerance):
        if self.pair.strategy == 'basic_seller' and self.t1.cex.usd_price < self.pair.min_sell_price_usd:
            return (self.pair.min_sell_price_usd / self.t2.cex.usd_price) / self.current_order['org_pprice']
        return float(self.pair.cex.price / self.current_order['org_pprice'])

    def _set_variation(self, var):
        self.variation = float(f"{var:.3f}") if isinstance(var, float) else [
            float(f"{self.pair.cex.price / self.current_order['org_pprice']:.3f}")]

    def _log_price_check(self, var):
        general_log.info(
            f"check_price_in_range - {self.symbol} - var: {var:.4f}, "
            f"s.variation: {self.variation:.4f}, Price: {self.pair.cex.price:.4f}, "
            f"Org PPrice: {self.current_order['org_pprice']:.4f}, "
            f"Ratio: {self.pair.cex.price / self.current_order['org_pprice']:.4f}"
        )

    def _is_price_in_range(self, var, price_variation_tolerance):
        return 1 - price_variation_tolerance < var < 1 + price_variation_tolerance

    def init_virtual_order(self, disabled_coins=None, display=True):
        if self._is_pair_disabled(disabled_coins):
            self.disabled = True
            general_log.info(f"{self.symbol} disabled due to cc checks: {disabled_coins}")
            return

        if not self.disabled:
            self._initialize_order()
            if display:
                self._log_virtual_order()

    def _is_pair_disabled(self, disabled_coins):
        return disabled_coins and (self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins)

    def _initialize_order(self):
        if not self.order_history or "basic_seller" in self.pair.strategy or (
                'side' in self.order_history and self.order_history['side'] == 'BUY'):
            self.create_virtual_sell_order()
        elif 'side' in self.order_history and self.order_history['side'] == 'SELL':
            self.create_virtual_buy_order(manual_dex_price=True)
        else:
            general_log.error(f"error during init_order\n{self.order_history}")
            os._exit(1)

    def _log_virtual_order(self):
        general_log.info(
            f"init_virtual_order, Prices: {self.symbol}{['{:.8f}'.format(self.pair.cex.price)]}, "
            f"{self.t1.symbol}/USD{['{:.2f}'.format(self.t1.cex.usd_price)]}, "
            f"{self.t2.symbol}/USD{['{:.2f}'.format(self.t2.cex.usd_price)]}"
        )
        general_log.info(f"current_order: {self.current_order}")

    def cancel_myorder(self):
        if self.order and 'id' in self.order:
            xb.cancelorder(self.order['id'])
            self.order = None

    def create_order(self, dry_mode=False):
        self.order = None
        if self.disabled:
            return

        maker_size = f"{self.current_order['maker_size']:.6f}"
        bal = self._get_balance()

        if self._is_balance_valid(bal, maker_size):
            self._create_order(dry_mode, maker_size)
        else:
            general_log.error(
                f"dex_create_order, balance too low: {bal}, need: {maker_size} {self.current_order['maker']}")

    def _get_balance(self):
        return self.t2.dex.free_balance if self.current_order['side'] == "BUY" else self.t1.dex.free_balance

    def _is_balance_valid(self, bal, maker_size):
        return bal is not None and maker_size.replace('.', '').isdigit()

    def _create_order(self, dry_mode, maker_size):
        if float(self._get_balance()) > float(maker_size):
            order = self._generate_order(dry_mode)
            if not dry_mode:
                self.order = order
                if self.order and 'error' in self.order:
                    self._handle_order_error()
            else:
                self._log_dry_mode_order(order)
        else:
            general_log.error(
                f"dex_create_order, balance too low: {self._get_balance()}, need: {maker_size} {self.current_order['maker']}")

    def _generate_order(self, dry_mode):
        maker = self.current_order['maker']
        maker_size = f"{self.current_order['maker_size']:.6f}"
        maker_address = self.current_order['maker_address']
        taker = self.current_order['taker']
        taker_size = f"{self.current_order['taker_size']:.6f}"
        taker_address = self.current_order['taker_address']

        if self.partial_percent:
            minimum_size = f"{self.current_order['minimum_size']:.6f}"
            return xb.makepartialorder(maker, maker_size, maker_address, taker, taker_size, taker_address, minimum_size)
        return xb.makeorder(maker, maker_size, maker_address, taker, taker_size, taker_address)

    def _handle_order_error(self):
        if 'code' in self.order and self.order['code'] not in {1019, 1018, 1026, 1032}:
            self.disabled = True
        general_log.error(f"Error making order on Pair {self.symbol}, disabled: {self.disabled}, {self.order}")

    def _log_dry_mode_order(self, order):
        msg = f"xb.makeorder({self.current_order['maker']}, {self.current_order['maker_size']:.6f}, {self.current_order['maker_address']}, {self.current_order['taker']}, {self.current_order['taker_size']:.6f}, {self.current_order['taker_address']})"
        general_log.info(f"dex_create_order, Dry mode enabled. {msg}")
        print(f"{bcolors.mycolor.OKBLUE}{msg}{bcolors.mycolor.ENDC}")

    def check_order_status(self) -> int:
        counter = 0
        max_count = 3

        while counter < max_count:
            try:
                local_dex_order = xb.getorderstatus(self.order['id'])
                if 'status' in local_dex_order:
                    self.order = local_dex_order
                    return self._map_order_status()
            except Exception as e:
                general_log.error(f"Error in dex_check_order_status: {type(e).__name__}, {e}\n{self.order}")
            counter += 1
            time.sleep(counter)

        self._handle_order_status_error()
        return self.STATUS_CANCELLED_WITHOUT_CALL

    def _map_order_status(self):
        status_mapping = {
            "open": self.STATUS_OPEN,
            "new": self.STATUS_OPEN,
            "created": self.STATUS_OTHERS,
            "initialized": self.STATUS_OTHERS,
            "committed": self.STATUS_OTHERS,
            "finished": self.STATUS_FINISHED,
            "expired": self.STATUS_ERROR_SWAP,
            "offline": self.STATUS_ERROR_SWAP,
            "canceled": self.STATUS_CANCELLED_WITHOUT_CALL,
            "invalid": self.STATUS_ERROR_SWAP,
            "rolled back": self.STATUS_ERROR_SWAP,
            "rollback failed": self.STATUS_ERROR_SWAP
        }
        return status_mapping.get(self.order.get('status'), self.STATUS_OPEN)

    def _handle_order_status_error(self):
        general_log.error(f"Error in dex_check_order_status: 'status' not in order. {self.order}")
        if self.pair.strategy in ['pingpong', 'basic_seller']:
            self.order = None

    def check_price_variation(self, disabled_coins, display=False):
        if 'side' in self.current_order and not self.check_price_in_range(display=display):
            self._log_price_variation()
            if self.order:
                self.cancel_myorder()
            self._reinit_virtual_order(disabled_coins)

    def _log_price_variation(self):
        msg = (f"check_price_variation, {self.symbol}, variation: {self.variation}, {self.order['status']}, "
               f"live_price: {self.pair.cex.price:.8f}, order_price: {self.current_order['dex_price']:.8f}")
        print(f"{bcolors.mycolor.WARNING}{msg}{bcolors.mycolor.ENDC}")
        if self.order:
            msg = f"check_price_variation, dex cancel: {self.order['id']}"
            print(f"{bcolors.mycolor.WARNING}{msg}{bcolors.mycolor.ENDC}")

    def _reinit_virtual_order(self, disabled_coins):
        if self.pair.strategy == 'pingpong':
            self.init_virtual_order(disabled_coins)
            if not self.order:
                self.create_order()
        elif self.pair.strategy == 'basic_seller':
            self.create_virtual_sell_order()
            if self.order is None:
                self.create_order(dry_mode=False)

    def status_check(self, disabled_coins=None, display=False, partial_percent=None):
        self.pair.cex.update_pricing(display)
        if self.disabled:
            general_log.info(f"Pair {self.symbol} Disabled, error: {self.order}")
            return

        status = self._check_order_status(disabled_coins)
        self._handle_status(status, disabled_coins, display)

    def _check_order_status(self, disabled_coins):
        if self.order and 'id' in self.order:
            return self.check_order_status()
        if not self.disabled:
            self.init_virtual_order(disabled_coins)
            if self.order and "id" in self.order:
                return self.check_order_status()
        return None

    def _handle_status(self, status, disabled_coins, display):
        status_handlers = {
            self.STATUS_OPEN: lambda: self.handle_status_open(disabled_coins, display),
            self.STATUS_FINISHED: lambda: self.at_order_finished(disabled_coins),
            self.STATUS_OTHERS: lambda: self.check_price_in_range(display=display),
            self.STATUS_ERROR_SWAP: self.handle_status_error_swap,
        }
        status_handlers.get(status, self.handle_status_default)()

    def handle_status_open(self, disabled_coins, display):
        if self._is_pair_disabled(disabled_coins):
            self._cancel_order_due_to_disabled_coins(disabled_coins)
        else:
            self.check_price_variation(disabled_coins, display=display)

    def _cancel_order_due_to_disabled_coins(self, disabled_coins):
        if self.order:
            general_log.info(f"Disabled pairs due to cc_height_check {self.symbol}, {disabled_coins}")
            general_log.info(f"status_check, dex cancel {self.order['id']}")
            self.cancel_myorder()

    def handle_status_error_swap(self):
        general_log.error(f"Order Error:\n{self.current_order}\n{self.order}")
        if self.pair.strategy == 'pingpong':
            self.disabled = True
            # xb.cancelallorders()
            # os._exit(1)

    def handle_status_default(self):
        if not self.disabled:
            general_log.error(f"status_check, no valid status: {self.symbol}, {self.order}")
            self.create_order()

    def at_order_finished(self, disabled_coins):
        msg = f"order FINISHED: {self.order['id']}"
        general_log.info(msg)
        trade_log.info(msg)
        trade_log.info(self.current_order)
        trade_log.info(self.order)
        self.order_history = self.current_order
        self.write_last_order_history()

        if self.order['taker'] == self.t1.symbol:
            self.t1.dex.request_addr()
        elif self.order['taker'] == self.t2.symbol:
            self.t2.dex.request_addr()

        if self.pair.strategy == 'pingpong':
            self.init_virtual_order(disabled_coins)
            self.create_order()
        elif self.pair.strategy == 'basic_seller':
            general_log.info('order sold, terminate!')
            os._exit(1)


class CexPair:
    def __init__(self, pair):
        self.pair = pair
        self.t1 = pair.t1
        self.t2 = pair.t2
        self.symbol = pair.symbol
        self.price = None
        self.cex_orderbook = None
        self.cex_orderbook_timer = None

    def update_pricing(self, display=False):
        self._update_token_prices()
        self.price = self.t1.cex.cex_price / self.t2.cex.cex_price
        if display:
            general_log.info(
                f"update_pricing: {self.t1.symbol} btc_p: {self.t1.cex.cex_price}, "
                f"{self.t2.symbol} btc_p: {self.t2.cex.cex_price}, "
                f"{self.symbol} price: {self.price}"
            )

    def _update_token_prices(self):
        if self.t1.cex.cex_price is None:
            self.t1.cex.update_price()
        if self.t2.cex.cex_price is None:
            self.t2.cex.update_price()

    def update_orderbook(self, limit=25, ignore_timer=False):
        update_cex_orderbook_timer_delay = 2
        if ignore_timer or not self.cex_orderbook_timer or time.time() - self.cex_orderbook_timer > update_cex_orderbook_timer_delay:
            self.cex_orderbook = ccxt_def.ccxt_call_fetch_order_book(init.my_ccxt, self.symbol, self.symbol)
            self.cex_orderbook_timer = time.time()
