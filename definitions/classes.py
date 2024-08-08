# from utils import dxbottools
import logging
import pickle
import time
import requests
import yaml
import os

# import config.config_pingpong as config_pp
import config.config_coins as config_coins
import definitions.bcolors as bcolors
import definitions.ccxt_def as ccxt_def
import definitions.init as init
import definitions.logger as logger
import definitions.xbridge_def as xb

# Status constants
STATUS_OPEN = 0
STATUS_FINISHED = 1
STATUS_OTHERS = 2
STATUS_ERROR_SWAP = -1
STATUS_CANCELLED_WITHOUT_CALL = -2

general_log = None
trade_log = None


def setup_logger(strategy=None):
    global general_log, trade_log

    if general_log and trade_log:
        return

    if strategy:
        log_dir = f'{init.ROOT_DIR}/logs/{strategy}_'
        general_log = logger.setup_logger(name="GENERAL_LOG",
                                          log_file=f'{log_dir}general.log',
                                          level=logging.INFO, console=True)
        trade_log = logger.setup_logger(name="TRADE_LOG",
                                        log_file=f'{log_dir}trade.log',
                                        level=logging.INFO, console=False)
    else:
        print("setup_logger(strategy=None)")
        exit()


class Config:
    def __init__(self, config_data=None):
        self.set_defaults()

        if config_data:
            self.update_config(config_data)

    def set_defaults(self):
        self.debug_level = 2
        self.ttk_theme = "darkly"
        self.user_pairs = ["BLOCK/LTC", "LTC/BLOCK"]
        self.price_variation_tolerance = 0.02
        self.sell_price_offset = 0.05
        self.usd_amount_default = 1
        self.usd_amount_custom = {"DASH/BLOCK": 21, "BLOCK/DASH": 19}
        self.spread_default = 0.05
        self.spread_custom = {"BLOCK/LTC": 0.04, "LTC/BLOCK": 0.04}

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
        if self.dex_enabled:
            filename = f'{init.ROOT_DIR}/data/{self.strategy}_{self.symbol}_addr.pic'
            try:
                with open(filename, 'rb') as fp:
                    self.xb_address = pickle.load(fp)
            except FileNotFoundError:
                general_log.info(f"File not found: {filename}")
                self.dx_request_addr()
            except Exception as e:
                general_log.error(f"Error reading XB address: {filename} - {type(e).__name__}: {e}")
                self.dx_request_addr()

    def write_xb_address(self):
        if self.dex_enabled:
            filename = f'{init.ROOT_DIR}/data/{self.strategy}_{self.symbol}_addr.pic'
            try:
                with open(filename, 'wb') as fp:
                    pickle.dump(self.xb_address, fp)
            except Exception as e:
                general_log.error(f"Error writing XB address: {filename} - {type(e).__name__}: {e}")

    def dx_request_addr(self):
        try:
            self.xb_address = xb.getnewtokenadress(self.symbol)[0]
            general_log.info(f"dx_request_addr: {self.symbol}, {self.xb_address}")
            self.write_xb_address()
        except Exception as e:
            general_log.error(f"Error requesting XB address for {self.symbol}: {type(e).__name__}: {e}")

    def update_ccxt_price(self, display=False):
        update_ccxt_price_delay = 2

        if self.ccxt_price_timer is None or time.time() - self.ccxt_price_timer > update_ccxt_price_delay:
            cex_symbol = "BTC/USD" if self.symbol == "BTC" else f"{self.symbol}/BTC"
            lastprice_string = "last" if init.my_ccxt.id == "kucoin" else (
                "lastPrice" if init.my_ccxt.id == "binance" else "lastTradeRate")

            if self.symbol in config_coins.usd_ticker_custom:
                result = config_coins.usd_ticker_custom[self.symbol] / init.t['BTC'].usd_price
            elif cex_symbol in init.my_ccxt.symbols:
                result = self._fetch_cex_price(cex_symbol, lastprice_string)
            elif self.symbol == "BLOCK":
                result = self.update_block_ticker()
            else:
                general_log.info(f"{cex_symbol} not in cex {str(init.my_ccxt)}")
                self.usd_price = None
                self.ccxt_price = None
                return

            if result:
                self.ccxt_price = 1 if self.symbol == "BTC" else result
                self.usd_price = result if self.symbol == "BTC" else result * init.t['BTC'].usd_price
                self.ccxt_price_timer = time.time()
                general_log.debug(
                    f"new pricing {self.symbol} {self.ccxt_price} {self.usd_price} USD PRICE {init.t['BTC'].usd_price}")

        elif display:
            print('Token.update_ccxt_price()', 'too fast call?', self.symbol)

    def _fetch_cex_price(self, cex_symbol, lastprice_string):
        count = 0
        done = False
        while not done:
            count += 1
            try:
                result = float(ccxt_def.ccxt_call_fetch_ticker(init.my_ccxt, cex_symbol)['info'][lastprice_string])
                done = True
            except Exception as e:
                general_log.error(f"update_ccxt_price: {cex_symbol} error({count}): {type(e).__name__}: {e}")
                time.sleep(count)
        return result

    def update_block_ticker(self):
        count = 0
        done = False
        used_proxy = False

        while not done:
            count += 1
            result = None
            try:
                if ccxt_def.isportopen("127.0.0.1", 2233):
                    result = xb.rpc_call("fetch_ticker_block", rpc_port=2233, debug=2, display=False)
                    used_proxy = True
                else:
                    ticker = requests.get(url='https://min-api.cryptocompare.com/data/price?fsym=BLOCK&tsyms=BTC')
                    if ticker.status_code == 200:
                        result = ticker.json()['BTC']
            except Exception as e:
                general_log.error(f"update_ccxt_price: BLOCK error({count}): {type(e).__name__}: {e}")
                time.sleep(count)
            else:
                if result and isinstance(result, float):
                    general_log.info(f"Updated BLOCK ticker: {result} BTC proxy: {used_proxy}")
                    return result
                else:
                    time.sleep(count)


class Pair:
    def __init__(self, token1, token2, amount_token_to_sell=None, min_sell_price_usd=None,
                 ccxt_sell_price_upscale=None, strategy=None, dex_enabled=True, partial_percent=None):
        self.t1 = token1
        self.t2 = token2
        self.strategy = strategy
        self.dex_enabled = dex_enabled
        self.symbol = f'{self.t1.symbol}/{self.t2.symbol}'
        self.price = None
        self.order_history = None
        self.current_order = None
        self.dex_order = None
        self.dex_orderbook = None
        self.have_dex_orderbook = None
        self.cex_orderbook = None
        self.cex_orderbook_timer = None
        self.cex_pair_1 = None
        self.cex_pair_2 = None
        self.disabled = False
        self.variation = None
        self.amount_token_to_sell = amount_token_to_sell
        self.min_sell_price_usd = min_sell_price_usd
        self.ccxt_sell_price_upscale = ccxt_sell_price_upscale
        self.partial_percent = partial_percent
        self.read_pair_dex_last_order_history()

    def update_cex_orderbook(self, limit=25, ignore_timer=False):
        update_cex_orderbook_timer_delay = 2
        if ignore_timer or self.cex_orderbook_timer is None or time.time() - self.cex_orderbook_timer > update_cex_orderbook_timer_delay:
            self.cex_orderbook = ccxt_def.ccxt_call_fetch_order_book(init.my_ccxt, self.symbol, self.symbol)
            self.cex_orderbook_timer = time.time()

    def update_dex_orderbook(self):
        self.dex_orderbook = xb.dxgetorderbook(detail=3, maker=self.t1.symbol, taker=self.t2.symbol)
        self.dex_orderbook.pop('detail', None)

    def update_pricing(self, display=False):
        self._ensure_token_prices()

        self.price = self.t1.ccxt_price / self.t2.ccxt_price
        if display:
            general_log.info("update_pricing: %s btc_p: %s, %s btc_p: %s, %s/%s price: %s" % (
                self.t1.symbol, self.t1.ccxt_price,
                self.t2.symbol, self.t2.ccxt_price,
                self.t1.symbol, self.t2.symbol, self.price
            ))

    def _ensure_token_prices(self):
        while self.t1.ccxt_price is None:
            self.t1.update_ccxt_price()
        while self.t2.ccxt_price is None:
            self.t2.update_ccxt_price()

    def read_pair_dex_last_order_history(self):
        if self.dex_enabled:
            filename = f'{init.ROOT_DIR}/data/{self.strategy}_{self.t1.symbol}_{self.t2.symbol}_last_order.pic'
            try:
                with open(filename, 'rb') as fp:
                    self.order_history = pickle.load(fp)
            except FileNotFoundError:
                general_log.info(f"File not found: {filename}")
            except Exception as e:
                general_log.error(f"read_pair_last_order_history: {type(e).__name__}, {e}")
                self.order_history = None

    def write_pair_dex_last_order_history(self):
        filename = f'{init.ROOT_DIR}/data/{self.strategy}_{self.t1.symbol}_{self.t2.symbol}_last_order.pic'
        try:
            with open(filename, 'wb') as fp:
                pickle.dump(self.order_history, fp)
        except Exception as e:
            general_log.error(f'Error writing pair last order history: {type(e)}, {e}')

    def create_dex_virtual_sell_order(self, display=True, manual_dex_price=None):
        self.current_order = {
            'symbol': self.symbol,
            'manual_dex_price': bool(manual_dex_price),
            'side': 'SELL',
            'maker': self.t1.symbol,
            'maker_address': self.t1.xb_address,
            'taker': self.t2.symbol,
            'taker_address': self.t2.xb_address,
            'type': 'exact',
        }

        price = manual_dex_price or self._calculate_sell_price()
        amount = self._calculate_sell_amount()

        if self.partial_percent:
            minimum_size = amount * self.partial_percent
            self.current_order.update({
                'type': 'partial',
                'minimum_size': minimum_size,
            })

        self.current_order.update({
            'maker_size': amount,
            'taker_size': amount * (price * (1 + self._calculate_spread())),
            'dex_price': (amount * (price * (1 + self._calculate_spread()))) / amount,
            'org_pprice': price,
            'org_t1price': self.t1.ccxt_price,
            'org_t2price': self.t2.ccxt_price,
        })

    def _calculate_sell_price(self):
        if self.strategy == 'basic_seller' and self.t1.usd_price < self.min_sell_price_usd:
            return self.min_sell_price_usd / self.t2.usd_price
        return self.price

    def _calculate_sell_amount(self):
        if self.strategy == 'basic_seller':
            return self.amount_token_to_sell
        return init.config_pp.usd_amount_custom.get(self.symbol, init.config_pp.usd_amount_default) / (
                    self.t1.ccxt_price * init.t['BTC'].usd_price)

    def _calculate_spread(self):
        if self.strategy == 'basic_seller':
            return self.ccxt_sell_price_upscale
        return init.config_pp.sell_price_offset

    def create_dex_virtual_buy_order(self, display=True, manual_dex_price=False):
        if self.strategy != 'pingpong':
            general_log.error(
                f"Bot strategy is {self.strategy}, no rule for this strat on create_dex_virtual_buy_order")
            return

        self.current_order = {
            'symbol': self.symbol,
            'side': 'BUY',
            'manual_dex_price': manual_dex_price,
            'maker': self.t2.symbol,
            'maker_address': self.t2.xb_address,
            'taker': self.t1.symbol,
            'taker_address': self.t1.xb_address,
            'type': 'exact',
        }

        price = self._calculate_buy_price(manual_dex_price)
        amount = float(self.order_history['maker_size'])
        spread = init.config_pp.spread_custom.get(self.symbol, init.config_pp.spread_default)

        self.current_order.update({
            'maker_size': amount * price * (1 - spread),
            'taker_size': amount,
            'dex_price': (amount * price * (1 - spread)) / amount,
            'org_pprice': price,
            'org_t1price': self.t1.ccxt_price,
            'org_t2price': self.t2.ccxt_price,
        })

    def _calculate_buy_price(self, manual_dex_price):
        if manual_dex_price:
            return min(self.price, self.order_history['dex_price'])
        return self.price

    def check_price_in_range(self, display=False):
        self.variation = None
        general_log.debug("Entering check_price_in_range_ancient")

        price_variation_tolerance = self._get_price_variation_tolerance()

        if 'side' in self.current_order and self.current_order['manual_dex_price'] is True:
            var = self._calculate_manual_price_variation()
        else:
            var = self._calculate_auto_price_variation()

        self._set_variation(var)

        if display:
            general_log.info(
                f"check_price_in_range - {self.symbol} - "
                f"var: {var}, "
                f"s.variation: {self.variation}, "
                f"Price: {self.price}, "
                f"Org PPrice: {self.current_order['org_pprice']}, "
                f"Ratio: {self.price / self.current_order['org_pprice']}"
            )

        return 1 - price_variation_tolerance < var < 1 + price_variation_tolerance

    def _get_price_variation_tolerance(self):
        if self.strategy == 'pingpong':
            return init.config_pp.price_variation_tolerance
        if self.strategy == 'basic_seller':
            return 0.01

    def _calculate_manual_price_variation(self):
        if self.current_order['side'] == 'BUY' and self.price < self.order_history['org_pprice']:
            return float(self.price / self.current_order['org_pprice'])
        if self.current_order['side'] == 'SELL' and self.price > self.order_history['org_pprice']:
            return float(self.price / self.current_order['org_pprice'])
        return 1

    def _calculate_auto_price_variation(self):
        if self.strategy == 'basic_seller' and self.t1.usd_price < self.min_sell_price_usd:
            return (self.min_sell_price_usd / self.t2.usd_price) / self.current_order['org_pprice']
        return float(self.price / self.current_order['org_pprice'])

    def _set_variation(self, var):
        if isinstance(var, float):
            self.variation = float(f"{var:.3f}")
        else:
            self.variation = [float(f"{self.price / self.current_order['org_pprice']:.3f}")]

    def init_virtual_order(self, disabled_coins=None, display=True):
        if disabled_coins and (self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins):
            self.disabled = True
            general_log.info(f'{self.symbol} disabled due to cc checks: {disabled_coins}')
            return

        if not self.disabled:
            if self._is_initial_order():
                self.create_dex_virtual_sell_order()
            elif self._is_buy_side_order():
                self.create_dex_virtual_buy_order(manual_dex_price=True)
            else:
                general_log.error(f'Error during init_order\n{self.order_history}')
                exit()

            if display:
                general_log.info(f"init_virtual_order, Prices: {self.symbol} {self._format_prices()}")

            general_log.info(f"current_order: {self.current_order}")

    def _is_initial_order(self):
        return self.order_history is None or "basic_seller" in self.strategy or (self._is_buy_side_order())

    def _is_buy_side_order(self):
        return 'side' in self.order_history and self.order_history['side'] == 'BUY'

    def _format_prices(self):
        return f"{self.symbol} [{self.price:.8f}], {self.t1.symbol}/USD [{self.t1.usd_price:.2f}], {self.t2.symbol}/USD [{self.t2.usd_price:.2f}]"

    def dex_cancel_myorder(self):
        if self.dex_order and 'id' in self.dex_order:
            xb.cancelorder(self.dex_order['id'])
            self.dex_order = None
            self.current_order = None

    def dex_create_order(self, dry_mode=False):
        self.dex_order = None

        if not self.disabled:
            maker = self.current_order['maker']
            maker_size = f"{self.current_order['maker_size']:.6f}"

            bal = self._get_balance()
            valid = self._is_valid_balance(bal, maker_size)

            general_log.debug(f"dex_create_order, maker: {maker}, maker_size: {maker_size}, bal: {bal}, valid: {valid}")

            if valid and float(bal) > float(maker_size):
                order_func = self._get_order_func()
                order_args = (maker, maker_size, self.current_order['maker_address'],
                              self.current_order['taker'], f"{self.current_order['taker_size']:.6f}",
                              self.current_order['taker_address'])

                if not dry_mode:
                    self.dex_order = order_func(*order_args)
                    self._handle_order_error()
                else:
                    general_log.info(f"dex_create_order, Dry mode enabled. xb.makeorder{order_args}")
                    print(f"{bcolors.mycolor.OKBLUE}xb.makeorder{order_args}{bcolors.mycolor.ENDC}")
            else:
                general_log.error(f'dex_create_order, balance too low: {bal}, need: {maker_size} {maker}')

    def _get_balance(self):
        return self.t2.dex_free_balance if self.current_order['side'] == "BUY" else self.t1.dex_free_balance

    def _is_valid_balance(self, bal, maker_size):
        return bal is not None and maker_size.replace('.', '').isdigit()

    def _get_order_func(self):
        return xb.makepartialorder if self.partial_percent else xb.makeorder

    def _handle_order_error(self):
        if self.dex_order and 'error' in self.dex_order:
            if self.dex_order.get('code') not in {1019, 1018, 1026, 1032}:
                self.disabled = True
            general_log.error(f"Error making order on Pair {self.symbol}, disabled: {self.disabled}, {self.dex_order}")

    def dex_check_order_status(self) -> int:
        done = False
        counter = 0
        max_count = 3

        while not done:
            try:
                local_dex_order = xb.getorderstatus(self.dex_order['id'])
            except Exception as e:
                general_log.error("Error in dex_check_order_status: %s %s\n%s", type(e), e, self.dex_order)

            if 'status' in local_dex_order:
                done = True
            else:
                counter += 1
                if counter == max_count:
                    return self._handle_max_count_exceeded(local_dex_order)
                else:
                    general_log.warning(f"dex_check_order_status, 'status' not in order, counter: {counter}")
                    time.sleep(counter)

        self.dex_order = local_dex_order
        return self._map_status_to_code()

    def _handle_max_count_exceeded(self, local_dex_order):
        general_log.error(f"Error in dex_check_order_status: 'status' not in order.\n{local_dex_order.get('error')}")
        general_log.error(f"Symbol: {self.symbol}, Current Order: {self.current_order}, Dex Order: {self.dex_order}")
        if self.strategy in ['pingpong', 'basic_seller']:
            self.dex_order = None
            return STATUS_CANCELLED_WITHOUT_CALL
        return STATUS_ERROR_SWAP

    def _map_status_to_code(self):
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
        if 'side' in self.current_order and not self.check_price_in_range(display=display):
            msg = (f"check_price_variation, {self.symbol}, variation: {self.variation:.3f}, "
                   f"{self.dex_order['status']}, live_price: {self.price:.8f}, "
                   f"order_price: {self.current_order['dex_price']:.8f}")
            print(f"{bcolors.mycolor.WARNING}{msg}{bcolors.mycolor.ENDC}")
            self.dex_cancel_myorder()

            if self.strategy == 'pingpong':
                self.init_virtual_order(disabled_coins)
                if not self.dex_order:
                    self.dex_create_order()
            elif self.strategy == 'basic_seller':
                self.create_dex_virtual_sell_order()
                if not self.dex_order:
                    self.dex_create_order(dry_mode=False)

    def status_check(self, disabled_coins=None, display=False, partial_percent=None):
        self.update_pricing()

        status = None
        if self.disabled:
            general_log.info(f"Pair {self.symbol} Disabled, error: {self.dex_order}")
            return

        if self.dex_order and 'id' in self.dex_order:
            status = self.dex_check_order_status()
        elif not self.disabled:
            self.init_virtual_order(disabled_coins)
            if self.dex_order and "id" in self.dex_order:
                status = self.dex_check_order_status()

        status_handlers = {
            STATUS_OPEN: lambda: self.handle_status_open(disabled_coins, display),
            STATUS_FINISHED: lambda: self.dex_order_finished(disabled_coins),
            STATUS_OTHERS: lambda: self.check_price_in_range(display=display),
            STATUS_ERROR_SWAP: self.handle_status_error_swap,
            STATUS_CANCELLED_WITHOUT_CALL: self.handle_status_cancelled_without_call,
        }

        status_handlers.get(status, self.handle_status_default)()

    def handle_status_open(self, disabled_coins, display):
        if disabled_coins and (self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins):
            if self.dex_order:
                general_log.info(f'Disabled pairs due to cc_height_check {self.symbol}, {disabled_coins}')
                general_log.info(f"status_check, dex cancel {self.dex_order['id']}")
                self.dex_cancel_myorder()
        else:
            self.check_price_variation(disabled_coins, display=display)

    def handle_status_error_swap(self):
        general_log.error('Order Error:\n' + str(self.current_order))
        general_log.error(self.dex_order)
        if self.strategy == 'pingpong':
            xb.cancelallorders()
            exit()

    def handle_status_cancelled_without_call(self):
        order_id = self.dex_order['id'] if self.dex_order and 'id' in self.dex_order else None
        general_log.error(f'Order Error: {order_id} CANCELLED WITHOUT CALL')
        general_log.error(self.dex_order)
        self.dex_order = None

    def handle_status_default(self):
        if not self.disabled:
            general_log.error(f"status_check, no valid status: {self.symbol}, {self.dex_order}")
            self.dex_create_order()

    def dex_order_finished(self, disabled_coins):
        msg = f"order FINISHED: {self.dex_order['id']}"
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
            print('order sold, terminate!')
            exit()
