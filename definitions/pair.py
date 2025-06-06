import os
import time

import yaml

import definitions.xbridge_def as xb
from definitions.token import Token


class Pair:
    def __init__(self, token1: Token, token2: Token, config_manager, cfg: dict, amount_token_to_sell=None,
                 min_sell_price_usd=None,
                 sell_price_offset=None,
                 strategy=None, dex_enabled=True, partial_percent=None):
        self.cfg = cfg
        self.name = cfg['name']
        self.strategy = strategy  # e.g., arbtaker, pingpong, basic_seller
        self.t1 = token1
        self.t2 = token2
        self.symbol = f'{self.t1.symbol}/{self.t2.symbol}'
        self.disabled = False
        self.variation = None
        self.dex_enabled = dex_enabled
        self.amount_token_to_sell = amount_token_to_sell
        self.min_sell_price_usd = min_sell_price_usd
        if 'sell_price_offset' in self.cfg:
            offset = self.cfg['sell_price_offset']
        else:
            offset = sell_price_offset
        self.sell_price_offset = offset
        self.config_manager = config_manager
        self.dex = DexPair(self, partial_percent)
        self.cex = CexPair(self)
        self.config_manager.general_log.info(
            f"Pair initialized: {self.name} | "
            f"Symbol: {self.symbol} | "
            f"Strategy: {self.strategy} | "
            f"Amount to sell: {self.amount_token_to_sell} | "
            f"Min sell price (USD): {self.min_sell_price_usd} | "
            f"Sell price offset: {self.sell_price_offset}"
        )


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

    def _get_history_file_path(self):
        unique_id = self.pair.name.replace("/", "_")
        return f"{self.pair.config_manager.ROOT_DIR}/data/{self.pair.strategy}_{unique_id}_last_order.yaml"

    def read_last_order_history(self):
        if not self.pair.dex_enabled:
            return
        file_path = self._get_history_file_path()
        try:
            with open(file_path, 'r') as fp:
                self.order_history = yaml.safe_load(fp)
        except FileNotFoundError:
            self.pair.config_manager.general_log.info(f"File not found: {file_path}")
        except Exception as e:
            self.pair.config_manager.general_log.error(f"read_pair_last_order_history: {type(e)}, {e}")
            self.order_history = None

    def write_last_order_history(self):
        # Get exact USD amount from our specific config entry

        file_path = self._get_history_file_path()
        try:
            with open(file_path, 'w') as fp:
                yaml.safe_dump(self.order_history, fp)
        except Exception as e:
            self.pair.config_manager.general_log.error(f"error write_pair_last_order_history: {type(e)}, {e}")

    def create_virtual_sell_order(self, manual_dex_price=None):
        self.current_order = self._build_sell_order(manual_dex_price)
        self.pair.config_manager.general_log.info(
            f"Virtual sell order created for {self.pair.name} | "
            f"Symbol: {self.symbol} | "
            f"Maker: {self.t1.symbol} | "
            f"Taker: {self.t2.symbol} | "
            f"Maker size: {self.current_order['maker_size']:.6f} | "
            f"Taker size: {self.current_order['taker_size']:.6f} | "
            f"Price: {self.current_order['dex_price']:.8f}"
        )

    def truncate(self, value, digits=8):
        format_str = f"{{:.{digits}f}}"
        truncated_float = float(format_str.format(value))
        return truncated_float

    def _build_sell_order(self, manual_dex_price):
        price = self._calculate_sell_price(manual_dex_price)
        amount, offset = self._determine_amount_and_spread_sell_side()
        order = {
            'symbol': self.symbol,
            'manual_dex_price': bool(manual_dex_price),
            'side': 'SELL',
            'maker': self.t1.symbol,
            'maker_address': self.t1.dex.address,
            'taker': self.t2.symbol,
            'taker_address': self.t2.dex.address,
            'type': 'partial' if self.partial_percent else 'exact',
            'maker_size': self.truncate(amount),
            'taker_size': self.truncate(amount * (price * (1 + offset))),
            'dex_price': self.truncate((amount * (price * (1 + offset))) / amount),
            'org_pprice': self.truncate(price),
            'org_t1price': self.truncate(self.t1.cex.cex_price),
            'org_t2price': self.truncate(self.t2.cex.cex_price),
        }
        if self.partial_percent:
            order['minimum_size'] = amount * self.partial_percent
        return order

    def _calculate_sell_price(self, manual_dex_price):
        if manual_dex_price:
            return manual_dex_price
        if self.pair.strategy == 'basic_seller':
            if self.pair.min_sell_price_usd and self.t1.cex.usd_price < self.pair.min_sell_price_usd:
                return self.pair.min_sell_price_usd / self.t2.cex.usd_price
        return self.pair.cex.price

    def _determine_amount_and_spread_sell_side(self):
        if self.pair.strategy == 'basic_seller':
            return self.pair.amount_token_to_sell, self.pair.sell_price_offset

        usd_amount = self.pair.cfg['usd_amount']

        # Calculate how many tokens needed for this USD amount using current BTC price
        btc_usd_price = self.pair.config_manager.tokens['BTC'].cex.usd_price
        if self.t1.cex.cex_price and btc_usd_price:
            amount = (usd_amount / btc_usd_price) / self.t1.cex.cex_price
        else:
            amount = 0  # Can't calculate without prices
        offset = self.pair.sell_price_offset
        return amount, offset

    def create_virtual_buy_order(self, manual_dex_price=False):
        if self.pair.strategy != 'pingpong':
            self.pair.config_manager.general_log.error(
                f"Bot strategy is {self.pair.strategy}, no rule for this strat on create_dex_virtual_buy_order")
            return
        self.current_order = self._build_buy_order(manual_dex_price)
        self.pair.config_manager.general_log.info(
            f"Virtual buy order created for {self.pair.name} | "
            f"Symbol: {self.symbol} | "
            f"Maker: {self.t2.symbol} | "
            f"Taker: {self.t1.symbol} | "
            f"Maker size: {self.current_order['maker_size']:.6f} | "
            f"Taker size: {self.current_order['taker_size']:.6f} | "
            f"Price: {self.current_order['dex_price']:.8f}"
        )

    def _build_buy_order(self, manual_dex_price):
        price = self._determine_buy_price(manual_dex_price)
        amount = float(self.order_history['maker_size'])
        # Get spread from pair config
        spread = self.pair.cfg.get('spread')
        order = {
            'symbol': self.symbol,
            'manual_dex_price': manual_dex_price,
            'side': 'BUY',
            'maker': self.t2.symbol,
            'maker_address': self.t2.dex.address,
            'taker': self.t1.symbol,
            'taker_address': self.t1.dex.address,
            'type': 'exact',
            'maker_size': self.truncate(amount * price * (1 - spread)),
            'taker_size': self.truncate(amount),
            'dex_price': self.truncate((amount * price * (1 - spread)) / amount),
            'org_pprice': self.truncate(price),
            'org_t1price': self.truncate(self.t1.cex.cex_price),
            'org_t2price': self.truncate(self.t2.cex.cex_price),
        }
        return order

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
            return self.pair.cfg.get('price_variation_tolerance')
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
        self.variation = self.truncate(var, 3) if isinstance(var, float) else [
            self.truncate(self.pair.cex.price / self.current_order['org_pprice'], 3)]

    def _log_price_check(self, var):
        self.pair.config_manager.general_log.info(
            f"Price variation check for {self.symbol}: "
            f"Variation: {var:.4f}, Stored variation: {self.variation:.4f}, "
            f"Live price: {self.pair.cex.price:.8f}, Original price: {self.current_order['org_pprice']:.8f}, "
            f"Price ratio: {self.pair.cex.price / self.current_order['org_pprice']:.4f}"
        )

    def _is_price_in_range(self, var, price_variation_tolerance):
        return 1 - price_variation_tolerance < var < 1 + price_variation_tolerance

    def init_virtual_order(self, disabled_coins=None, display=True):
        if self._is_pair_disabled(disabled_coins):
            self.disabled = True
            self.pair.config_manager.general_log.info(f"{self.symbol} disabled due to cc checks: {disabled_coins}")
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
            self.pair.config_manager.general_log.error(f"error during init_order\n{self.order_history}")
            os._exit(1)

    def _log_virtual_order(self):
        self.pair.config_manager.general_log.info(
            f"live pair prices : {self.truncate(self.pair.cex.price)} {self.symbol} | "
            f"{self.t1.symbol}/USD: {self.truncate(self.t1.cex.usd_price, 3)} | "
            f"{self.t2.symbol}/USD: {self.truncate(self.t2.cex.usd_price, 3)}"
        )
        self.pair.config_manager.general_log.info(f"Current virtual order details: {self.current_order}")

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
            self.pair.config_manager.general_log.error(
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
            self.pair.config_manager.general_log.error(
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
        self.pair.config_manager.general_log.error(
            f"Error making order on Pair: {self.pair.name} | "
            f"Symbol: {self.symbol} | "
            f"disabled: {self.disabled} | "
            f"{self.order}")

    def _log_dry_mode_order(self, order):
        msg = (f"xb.makeorder({self.current_order['maker']}, {self.current_order['maker_size']:.6f}, "
               f"{self.current_order['maker_address']}, {self.current_order['taker']}, "
               f"{self.current_order['taker_size']:.6f}, {self.current_order['taker_address']})")
        self.pair.config_manager.general_log.info(f"dex_create_order, Dry mode enabled. {msg}")

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
                self.pair.config_manager.general_log.error(
                    f"Error in dex_check_order_status: {type(e).__name__}, {e}\n{self.order}")
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
        self.pair.config_manager.general_log.error(
            f"Error in dex_check_order_status: 'status' not in order. {self.order}")
        if self.pair.strategy in ['pingpong', 'basic_seller']:
            self.order = None

    def check_price_variation(self, disabled_coins, display=False):
        if 'side' in self.current_order and not self.check_price_in_range(display=display):
            self._log_price_variation()
            if self.order:
                self.cancel_myorder()
            self._reinit_virtual_order(disabled_coins)

    def _log_price_variation(self):
        msg = (f"check_price_variation, {self.symbol}, variation: {self.variation}, "
               f"{self.order['status']}, live_price: {self.pair.cex.price:.8f}, "
               f"order_price: {self.current_order['dex_price']:.8f}")
        self.pair.config_manager.general_log.warning(msg)
        if self.order:
            msg = f"check_price_variation, dex cancel: {self.order['id']}"
            self.pair.config_manager.general_log.warning(msg)

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
            self.pair.config_manager.general_log.info(f"Pair {self.symbol} Disabled, error: {self.order}")
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
            self.pair.config_manager.general_log.info(
                f"Disabled pairs due to cc_height_check {self.symbol}, {disabled_coins}")
            self.pair.config_manager.general_log.info(f"status_check, dex cancel {self.order['id']}")
            self.cancel_myorder()

    def handle_status_error_swap(self):
        self.pair.config_manager.general_log.error(f"Order Error:\n{self.current_order}\n{self.order}")
        if self.pair.strategy == 'pingpong':
            self.disabled = True
            # xb.cancelallorders()
            # os._exit(1)

    def handle_status_default(self):
        if not self.disabled:
            self.pair.config_manager.general_log.error(f"status_check, no valid status: {self.symbol}, {self.order}")
            self.create_order()

    def at_order_finished(self, disabled_coins):
        if self.current_order['maker'] == self.pair.t1.symbol:
            side = 'SELL'
        else:
            side = 'BUY'
        dict = {
            "name": self.pair.cfg['name'],
            "pair": self.pair.symbol,
            "side": side,
            "orderid": self.order['id']
        }
        msg = f"order FINISHED: {dict}"
        self.pair.config_manager.general_log.info(msg)
        self.pair.config_manager.trade_log.info(msg)
        msg = f"virtual order: {self.current_order}"
        self.pair.config_manager.trade_log.info(msg)
        msg = f"xbridge order: {self.order}"
        self.pair.config_manager.trade_log.info(msg)
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
            self.pair.config_manager.general_log.info('order sold, terminate!')
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
            self.pair.config_manager.general_log.info(
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
            self.cex_orderbook = self.pair.config_manager.ccxt_manager.ccxt_call_fetch_order_book(
                self.pair.config_manager.my_ccxt, self.symbol, self.symbol)
            self.cex_orderbook_timer = time.time()
