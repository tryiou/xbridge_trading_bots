# Imports
import logging
import pickle
import time
import requests
import yaml
import os

import config.config_coins as config_coins
import definitions.bcolors as bcolors
import definitions.ccxt_def as ccxt_def
import definitions.init as init
import definitions.logger as logger
import definitions.xbridge_def as xb

# Constants
STATUS_OPEN = 0
STATUS_FINISHED = 1
STATUS_OTHERS = 2
STATUS_ERROR_SWAP = -1
STATUS_CANCELLED_WITHOUT_CALL = -2

general_log = None
trade_log = None


# Utility function to set up loggers
def setup_logger(strategy=None):
    global general_log, trade_log

    if general_log and trade_log:
        return

    if strategy:
        log_dir = f"{init.ROOT_DIR}/logs/{strategy}_"
        general_log = logger.setup_logger("GENERAL_LOG", f"{log_dir}general.log", logging.INFO, console=True)
        trade_log = logger.setup_logger("TRADE_LOG", f"{log_dir}trade.log", logging.INFO, console=False)
        general_log.propagate = False
    else:
        print("setup_logger(strategy=None)")
        exit()


# Configuration class
class Config:
    def __init__(self, config_data=None):
        # Default configuration values
        self.debug_level = 2
        self.ttk_theme = "darkly"
        self.user_pairs = ["BLOCK/LTC", "LTC/BLOCK"]
        self.price_variation_tolerance = 0.02
        self.sell_price_offset = 0.05
        self.usd_amount_default = 1
        self.usd_amount_custom = {"DASH/BLOCK": 21, "BLOCK/DASH": 19}
        self.spread_default = 0.05
        self.spread_custom = {"BLOCK/LTC": 0.04, "LTC/BLOCK": 0.04}

        # Update config with provided data
        if config_data:
            self.update_config(config_data)

    def update_config(self, config_data):
        for key, value in config_data.items():
            setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)

    @staticmethod
    def load_config(filename):
        if not os.path.exists(filename):
            default_config = Config()
            Config.save_config(default_config, filename)

        with open(filename, 'r') as file:
            config_data = yaml.safe_load(file)
        return Config(config_data)

    @staticmethod
    def save_config(config, filename):
        with open(filename, 'w') as file:
            yaml.dump(config.__dict__, file)


# Token class to handle individual token data
class Token:
    def __init__(self, symbol, strategy, dex_enabled=True):
        self.symbol = symbol
        self.strategy = strategy
        self.dex_enabled = dex_enabled

        self.ccxt_price = None
        self.ccxt_price_timer = None
        self.xb_address = None
        self.usd_price = None
        self.dex_total_balance = None
        self.dex_free_balance = None
        self.cex_total_balance = None
        self.cex_free_balance = None

        self.read_xb_address()

    def read_xb_address(self):
        if not self.dex_enabled:
            return

        filename = f"{init.ROOT_DIR}/data/{self.strategy}_{self.symbol}_addr.pic"
        try:
            with open(filename, 'rb') as fp:
                self.xb_address = pickle.load(fp)
        except FileNotFoundError:
            general_log.info(f"File not found: {filename}")
            self.dx_request_addr()
        except (pickle.PickleError, Exception) as e:
            general_log.error(f"Error reading XB address: {e}")
            self.dx_request_addr()

    def write_xb_address(self):
        if not self.dex_enabled:
            return

        filename = f"{init.ROOT_DIR}/data/{self.strategy}_{self.symbol}_addr.pic"
        try:
            with open(filename, 'wb') as fp:
                pickle.dump(self.xb_address, fp)
        except (pickle.PickleError, Exception) as e:
            general_log.error(f"Error writing XB address: {e}")

    def dx_request_addr(self):
        try:
            self.xb_address = xb.getnewtokenadress(self.symbol)[0]
            general_log.info(f"dx_request_addr: {self.symbol}, {self.xb_address}")
            self.write_xb_address()
        except Exception as e:
            general_log.error(f"Error requesting XB address: {e}")

    def update_ccxt_price(self, display=False):
        update_ccxt_price_delay = 2

        if self.ccxt_price_timer and time.time() - self.ccxt_price_timer <= update_ccxt_price_delay:
            if display:
                print(f"Token.update_ccxt_price() too fast call? {self.symbol}")
            return

        cex_symbol = "BTC/USD" if self.symbol == "BTC" else f"{self.symbol}/BTC"
        lastprice_key = self.get_lastprice_key(init.my_ccxt.id)
        result = None

        if self.symbol in config_coins.usd_ticker_custom:
            result = config_coins.usd_ticker_custom[self.symbol] / init.t['BTC'].usd_price
        elif cex_symbol in init.my_ccxt.symbols:
            result = self.fetch_ccxt_price(cex_symbol, lastprice_key)
        elif self.symbol == "BLOCK":
            result = self.update_block_ticker()

        if result:
            self.ccxt_price = 1 if self.symbol == "BTC" else result
            self.usd_price = result if self.symbol == "BTC" else result * init.t['BTC'].usd_price
            self.ccxt_price_timer = time.time()
            general_log.debug(
                f"New pricing {self.symbol} {self.ccxt_price} {self.usd_price} USD PRICE {init.t['BTC'].usd_price}")

    def fetch_ccxt_price(self, cex_symbol, lastprice_key):
        count = 0
        while True:
            count += 1
            try:
                return float(ccxt_def.ccxt_call_fetch_ticker(init.my_ccxt, cex_symbol)['info'][lastprice_key])
            except Exception as e:
                general_log.error(f"Error fetching CCXT price ({count}): {e}")
                time.sleep(count)

    def update_block_ticker(self):
        count = 0
        while True:
            count += 1
            try:
                if ccxt_def.isportopen("127.0.0.1", 2233):
                    result = xb.rpc_call("fetch_ticker_block", rpc_port=2233, debug=2, display=False)
                else:
                    ticker = requests.get('https://min-api.cryptocompare.com/data/price?fsym=BLOCK&tsyms=BTC')
                    if ticker.status_code == 200:
                        result = ticker.json().get('BTC')
            except Exception as e:
                general_log.error(f"Error updating BLOCK ticker ({count}): {e}")
                time.sleep(count)
            else:
                if result:
                    general_log.info(f"Updated BLOCK ticker: {result} BTC")
                    return result

    @staticmethod
    def get_lastprice_key(exchange_id):
        if exchange_id == "kucoin":
            return "last"
        elif exchange_id == "binance":
            return "lastPrice"
        else:
            return "lastTradeRate"


# Pair class to manage token pairs and trading logic
class Pair:
    def __init__(self, token1, token2, amount_token_to_sell=None, min_sell_price_usd=None, ccxt_sell_price_upscale=None,
                 strategy=None, dex_enabled=True, partial_percent=None):
        self.strategy = strategy
        self.t1 = token1
        self.t2 = token2
        self.symbol = f"{self.t1.symbol}/{self.t2.symbol}"
        self.price = None
        self.order_history = None
        self.current_order = None
        self.dex_order = None
        self.dex_orderbook = None
        self.cex_orderbook = None
        self.cex_orderbook_timer = None
        self.dex_enabled = dex_enabled
        self.disabled = False
        self.variation = None
        self.amount_token_to_sell = amount_token_to_sell
        self.min_sell_price_usd = min_sell_price_usd
        self.ccxt_sell_price_upscale = ccxt_sell_price_upscale
        self.partial_percent = partial_percent

        self.read_pair_dex_last_order_history()

    def update_cex_orderbook(self, limit=25, ignore_timer=False):
        update_cex_orderbook_timer_delay = 2
        if ignore_timer or not self.cex_orderbook_timer or time.time() - self.cex_orderbook_timer > update_cex_orderbook_timer_delay:
            self.cex_orderbook = ccxt_def.ccxt_call_fetch_order_book(init.my_ccxt, self.symbol, self.symbol)
            self.cex_orderbook_timer = time.time()

    def update_dex_orderbook(self):
        self.dex_orderbook = xb.dxgetorderbook(detail=3, maker=self.t1.symbol, taker=self.t2.symbol)
        del self.dex_orderbook['detail']

    def update_pricing(self, display=False):
        self.t1.update_ccxt_price()
        self.t2.update_ccxt_price()
        self.price = self.t1.ccxt_price / self.t2.ccxt_price
        if display:
            general_log.info(f"update_pricing: {self.symbol} - {self.price}")

    def read_pair_dex_last_order_history(self):
        if self.dex_enabled:
            filename = f"{init.ROOT_DIR}/data/{self.strategy}_{self.t1.symbol}_{self.t2.symbol}_last_order.pic"
            try:
                with open(filename, 'rb') as fp:
                    self.order_history = pickle.load(fp)
            except FileNotFoundError:
                general_log.info(f"File not found: {filename}")
            except Exception as e:
                general_log.error(f"Error reading last order history: {e}")

    def write_pair_dex_last_order_history(self):
        if self.dex_enabled:
            filename = f"{init.ROOT_DIR}/data/{self.strategy}_{self.t1.symbol}_{self.t2.symbol}_last_order.pic"
            try:
                with open(filename, 'wb') as fp:
                    pickle.dump(self.order_history, fp)
            except Exception as e:
                general_log.error(f"Error writing last order history: {e}")

    def create_dex_virtual_sell_order(self, display=True, manual_dex_price=None):
        self.current_order = {}
        price = manual_dex_price if manual_dex_price else self.calculate_sell_price()

        amount = self.amount_token_to_sell if self.strategy == 'basic_seller' else self.get_order_amount()
        spread = self.ccxt_sell_price_upscale if self.strategy == 'basic_seller' else init.config_pp.sell_price_offset

        self.current_order = {
            'symbol': self.symbol,
            'manual_dex_price': bool(manual_dex_price),
            'side': 'SELL',
            'maker': self.t1.symbol,
            'maker_address': self.t1.xb_address,
            'taker': self.t2.symbol,
            'taker_address': self.t2.xb_address,
            'type': 'partial' if self.partial_percent else 'exact',
            'maker_size': amount,
            'taker_size': amount * (price * (1 + spread)),
            'dex_price': (amount * (price * (1 + spread))) / amount,
            'org_pprice': price,
            'org_t1price': self.t1.ccxt_price,
            'org_t2price': self.t2.ccxt_price,
        }

        if self.partial_percent:
            self.current_order['minimum_size'] = amount * self.partial_percent

    def calculate_sell_price(self):
        return self.min_sell_price_usd / self.t2.usd_price if self.min_sell_price_usd and self.t1.usd_price < self.min_sell_price_usd else self.price

    def get_order_amount(self):
        return init.config_pp.usd_amount_custom.get(self.symbol, init.config_pp.usd_amount_default) / (
                self.t1.ccxt_price * init.t['BTC'].usd_price)

    def create_dex_virtual_buy_order(self, display=True, manual_dex_price=False):
        if self.strategy != 'pingpong':
            general_log.error(f"No rule for strategy {self.strategy} in create_dex_virtual_buy_order")
            return

        price = self.price if not manual_dex_price or self.price < self.order_history['dex_price'] else \
        self.order_history[
            'dex_price']
        amount = float(self.order_history['maker_size'])
        spread = init.config_pp.spread_custom.get(self.symbol, init.config_pp.spread_default)

        self.current_order = {
            'symbol': self.symbol,
            'manual_dex_price': manual_dex_price,
            'side': 'BUY',
            'maker': self.t2.symbol,
            'maker_size': amount * price * (1 - spread),
            'maker_address': self.t2.xb_address,
            'taker': self.t1.symbol,
            'taker_size': amount,
            'taker_address': self.t1.xb_address,
            'type': 'exact',
            'dex_price': amount * price * (1 - spread) / amount,
            'org_pprice': price,
            'org_t1price': self.t1.ccxt_price,
            'org_t2price': self.t2.ccxt_price,
        }

    def check_price_in_range(self, display=False):
        self.variation = None
        tolerance = init.config_pp.price_variation_tolerance if self.strategy == 'pingpong' else 0.01

        var = self.calculate_variation()
        self.variation = round(var, 3) if isinstance(var, float) else round(
            self.price / self.current_order['org_pprice'], 3)

        if display:
            general_log.info(
                f"check_price_in_range - {self.symbol} - var: {var}, s.variation: {self.variation}, Price: {self.price}, Org PPrice: {self.current_order['org_pprice']}, Ratio: {self.price / self.current_order['org_pprice']}")

        return 1 - tolerance < var < 1 + tolerance

    def calculate_variation(self):
        if self.current_order.get('manual_dex_price'):
            if self.current_order['side'] == 'BUY' and self.price < self.order_history['org_pprice']:
                return self.price / self.current_order['org_pprice']
            elif self.current_order['side'] == 'SELL' and self.price > self.order_history['org_pprice']:
                return self.price / self.current_order['org_pprice']
            return 1
        elif self.strategy == 'basic_seller' and self.t1.usd_price < self.min_sell_price_usd:
            return self.min_sell_price_usd / self.t2.usd_price / self.current_order['org_pprice']
        return self.price / self.current_order['org_pprice']

    def init_virtual_order(self, disabled_coins=None, display=True):
        if disabled_coins and (self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins):
            self.disabled = True
            general_log.info(f"{self.symbol} disabled due to cc checks: {disabled_coins}")
            return

        if not self.disabled:
            if not self.order_history or "basic_seller" in self.strategy or self.order_history.get('side') == 'BUY':
                self.create_dex_virtual_sell_order()
            elif self.order_history.get('side') == 'SELL':
                self.create_dex_virtual_buy_order(manual_dex_price=True)
            else:
                general_log.error(f"Error during init_order: {self.order_history}")
                exit()

            if display:
                general_log.info(
                    f"init_virtual_order, Prices: {self.symbol} [{self.price:.8f}], {self.t1.symbol}/USD [{self.t1.usd_price:.2f}], {self.t2.symbol}/USD [{self.t2.usd_price:.2f}]")
            general_log.info(f"current_order: {self.current_order}")

    def dex_cancel_myorder(self):
        if self.dex_order and 'id' in self.dex_order:
            xb.cancelorder(self.dex_order['id'])
            self.dex_order = None
            self.current_order = None

    def dex_create_order(self, dry_mode=False):
        if self.disabled:
            return

        maker = self.current_order['maker']
        maker_size = f"{self.current_order['maker_size']:.6f}"
        bal = self.t2.dex_free_balance if self.current_order['side'] == "BUY" else self.t1.dex_free_balance

        if bal is None or not maker_size.replace('.', '').isdigit() or float(bal) <= float(maker_size):
            general_log.error(f"dex_create_order, balance too low: {bal}, need: {maker_size} {maker}")
            return

        maker_address = self.current_order['maker_address']
        taker = self.current_order['taker']
        taker_size = f"{self.current_order['taker_size']:.6f}"
        taker_address = self.current_order['taker_address']
        minimum_size = f"{self.current_order['minimum_size']:.6f}" if self.partial_percent else None

        general_log.info(f"dex_create_order, Creating order. maker: {maker}, maker_size: {maker_size}, bal: {bal}")

        if not dry_mode:
            self.dex_order = xb.makepartialorder(maker, maker_size, maker_address, taker, taker_size, taker_address,
                                                 minimum_size) if self.partial_percent else xb.makeorder(maker,
                                                                                                         maker_size,
                                                                                                         maker_address,
                                                                                                         taker,
                                                                                                         taker_size,
                                                                                                         taker_address)

            if self.dex_order and 'error' in self.dex_order:
                self.handle_dex_order_error()
        else:
            msg = f"xb.makeorder({maker}, {maker_size}, {maker_address}, {taker}, {taker_size}, {taker_address})"
            general_log.info(f"dex_create_order, Dry mode enabled. {msg}")
            print(f"{bcolors.mycolor.OKBLUE}{msg}{bcolors.mycolor.ENDC}")

    def handle_dex_order_error(self):
        if self.dex_order.get('code') not in {1019, 1018, 1026, 1032}:
            self.disabled = True
        general_log.error(f"Error making order on Pair {self.symbol}, disabled: {self.disabled}, {self.dex_order}")

    def dex_check_order_status(self) -> int:
        max_count = 3
        for _ in range(max_count):
            try:
                self.dex_order = xb.getorderstatus(self.dex_order['id'])
                if 'status' in self.dex_order:
                    break
            except Exception as e:
                general_log.error(f"Error in dex_check_order_status: {e}, {self.dex_order}")
        else:
            general_log.error(f"Error in dex_check_order_status: 'status' not in order, {self.dex_order}")
            if self.strategy in ['pingpong', 'basic_seller']:
                self.dex_order = None
                return -2

        status_mapping = {
            "open": STATUS_OPEN,
            "new": STATUS_OPEN,
            "created": STATUS_OTHERS,
            "initialized": STATUS_OTHERS,
            "committed": STATUS_OTHERS,
            "finished": STATUS_FINISHED,
            "expired": STATUS_ERROR_SWAP,
            "offline": STATUS_ERROR_SWAP,
            "canceled": STATUS_CANCELLED_WITHOUT_CALL,
            "invalid": STATUS_ERROR_SWAP,
            "rolled back": STATUS_ERROR_SWAP,
            "rollback failed": STATUS_ERROR_SWAP
        }
        return status_mapping.get(self.dex_order.get('status'), STATUS_OPEN)

    def check_price_variation(self, disabled_coins, display=False):
        if self.current_order and not self.check_price_in_range(display):
            msg = (f"check_price_variation, {self.symbol}, variation: {self.variation:.3f}, "
                   f"{self.dex_order['status']}, live_price: {self.price:.8f}, order_price: {self.current_order['dex_price']:.8f}")
            print(f"{bcolors.mycolor.WARNING}{msg}{bcolors.mycolor.ENDC}")

            if self.dex_order:
                general_log.info(f"dex_cancel_myorder: {self.dex_order['id']}")
                self.dex_cancel_myorder()

            if self.strategy == 'pingpong':
                self.init_virtual_order(disabled_coins)
                if not self.dex_order:
                    self.dex_create_order()

            elif self.strategy == 'basic_seller':
                self.create_dex_virtual_sell_order()
                if not self.dex_order:
                    self.dex_create_order()

    def status_check(self, disabled_coins=None, display=False, partial_percent=None):
        self.update_pricing()
        status = self.dex_check_order_status() if self.dex_order and 'id' in self.dex_order else None

        if self.disabled:
            general_log.info(f"Pair {self.symbol} Disabled, error: {self.dex_order}")
            return

        if status is None and not self.disabled:
            self.init_virtual_order(disabled_coins)
            if self.dex_order and "id" in self.dex_order:
                status = self.dex_check_order_status()

        handlers = {
            STATUS_OPEN: lambda: self.handle_status_open(disabled_coins, display),
            STATUS_FINISHED: lambda: self.dex_order_finished(disabled_coins),
            STATUS_OTHERS: lambda: self.check_price_in_range(display=display),
            STATUS_ERROR_SWAP: self.handle_status_error_swap,
            STATUS_CANCELLED_WITHOUT_CALL: self.handle_status_cancelled_without_call,
        }

        handlers.get(status, self.handle_status_default)()

    def handle_status_open(self, disabled_coins, display):
        if disabled_coins and (self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins):
            general_log.info(f"Disabled pairs due to cc_height_check {self.symbol}, {disabled_coins}")
            self.dex_cancel_myorder()
        else:
            self.check_price_variation(disabled_coins, display=display)

    def handle_status_error_swap(self):
        general_log.error(f"Order Error: {self.current_order}")
        general_log.error(self.dex_order)
        if self.strategy == 'pingpong':
            xb.cancelallorders()
            exit()

    def handle_status_cancelled_without_call(self):
        general_log.error(f"Order Error: {self.dex_order['id']} CANCELLED WITHOUT CALL")
        general_log.error(self.dex_order)
        self.dex_order = None

    def handle_status_default(self):
        if not self.disabled:
            general_log.error(f"No valid status for Pair {self.symbol}, {self.dex_order}")
            self.dex_create_order()

    def dex_order_finished(self, disabled_coins):
        msg = f"Order FINISHED: {self.dex_order['id']}"
        general_log.info(msg)
        trade_log.info(msg)
        trade_log.info(self.current_order)
        trade_log.info(self.dex_order)

        self.order_history = self.current_order
        self.write_pair_dex_last_order_history()

        if self.dex_order['taker'] == self.t1.symbol:
            self.t1.dx_request_addr()
        elif self.dex_order['taker'] == self.t2.symbol:
            self.t2.dx_request_addr()

        if self.strategy == 'pingpong':
            self.init_virtual_order(disabled_coins)
            self.dex_create_order()
        elif self.strategy == 'basic_seller':
            print("Order sold, terminate!")
            exit()
