# from utils import dxbottools
import logging
import pickle
import time

import config.config_pingpong as config_pp
import definitions.bcolors as bcolors
import definitions.ccxt_def as ccxt_def
import definitions.init as init
import definitions.logger as logger
import definitions.xbridge_def as xb

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
        # Loggers are already set up, no need to do it again
        return

    if strategy:
        general_log = logger.setup_logger(name="GENERAL_LOG",
                                          log_file=init.ROOT_DIR + '/logs/' + strategy + '_general.log',
                                          level=logging.INFO, console=True)
        general_log.propagate = False
        trade_log = logger.setup_logger(name="TRADE_LOG", log_file=init.ROOT_DIR + '/logs/' + strategy + '_trade.log',
                                        level=logging.INFO,
                                        console=False)
    else:
        print("setup_logger(strategy=None)")
        exit()


# logging.basicConfig(level=logging.INFO)

star_counter = 0


class Token:
    def __init__(self, symbol, strategy, dex_enabled=True):
        self.strategy = strategy  # arbtaker, pingpong
        self.symbol = symbol
        self.ccxt_price = None
        self.ccxt_price_timer = None
        self.xb_address = None
        self.usd_price = None
        self.dex_total_balance = None
        self.dex_free_balance = None
        self.dex_enabled = dex_enabled
        self.cex_total_balance = None
        self.cex_free_balance = None
        self.read_xb_address()

    def read_xb_address(self):
        if self.dex_enabled:
            try:
                with open(init.ROOT_DIR + '/data/' + self.strategy + '_' + self.symbol + '_addr.pic', 'rb') as fp:
                    self.xb_address = pickle.load(fp)
            except FileNotFoundError:
                general_log.info(f"File not found: {self.strategy}_{self.symbol}_addr.pic")
                self.dx_request_addr()
            except (pickle.PickleError, Exception) as e:
                general_log.error(
                    f"Error reading XB address from file: {self.strategy}_{self.symbol}_addr.pic - {type(e).__name__}: {e}")
                self.dx_request_addr()

    def write_xb_address(self):
        if self.dex_enabled:
            try:
                with open(init.ROOT_DIR + '/data/' + self.strategy + '_' + self.symbol + '_addr.pic', 'wb') as fp:
                    pickle.dump(self.xb_address, fp)
            except (pickle.PickleError, Exception) as e:
                general_log.error(
                    f"Error writing XB address to file: {self.strategy}_{self.symbol}_addr.pic - {type(e).__name__}: {e}")

    def dx_request_addr(self):
        try:
            self.xb_address = xb.getnewtokenadress(self.symbol)[0]
            general_log.info(f"dx_request_addr: {self.symbol}, {self.xb_address}")
            self.write_xb_address()
        except Exception as e:
            general_log.error(f"Error requesting XB address for {self.symbol}: {type(e).__name__}: {e}")
            exit()

    def update_ccxt_price(self, display=False):
        update_ccxt_price_delay = 2

        if self.ccxt_price_timer is None or time.time() - self.ccxt_price_timer > update_ccxt_price_delay:
            done = False
            count = 0

            cex_symbol = "BTC/USD" if self.symbol == "BTC" else f"{self.symbol}/BTC"

            if cex_symbol in init.my_ccxt.symbols:
                while not done:
                    count += 1
                    try:
                        result = float(
                            ccxt_def.ccxt_call_fetch_ticker(init.my_ccxt, cex_symbol)['info']['lastTradeRate'])
                    except Exception as e:
                        general_log.error(f"update_ccxt_price: error({count}): {type(e).__name__}: {e}")
                        time.sleep(count)
                    else:
                        if result:
                            self.ccxt_price = 1 if self.symbol == "BTC" else result
                            self.usd_price = result if self.symbol != "BTC" else result * init.t['BTC'].usd_price
                            self.ccxt_price_timer = time.time()
                            done = True
            else:
                general_log.info(f"{cex_symbol} not in cex {str(init.my_ccxt)}")
                self.usd_price = None
                self.ccxt_price = None
        elif display:
            print('Token.update_ccxt_price()', 'too fast call?', self.symbol)


class Pair:
    def __init__(self, token1, token2, amount_token_to_sell=None, min_sell_price_usd=None, ccxt_sell_price_upscale=None,
                 strategy=None, dex_enabled=True):
        self.strategy = strategy  # arbtaker, pingpong
        self.t1 = token1
        self.t2 = token2
        self.symbol = self.t1.symbol + '/' + self.t2.symbol
        self.price = None
        self.order_history = None

        # virtual order >
        self.current_order = None
        # 'real' order >
        self.dex_order = None

        self.dex_orderbook = None
        self.have_dex_orderbook = None
        self.cex_orderbook = None
        self.cex_orderbook_timer = None
        self.cex_pair_1 = None
        self.cex_pair_2 = None
        self.dex_enabled = dex_enabled
        self.read_pair_dex_last_order_history()
        self.disabled = False
        self.variation = None
        self.amount_token_to_sell = amount_token_to_sell
        self.min_sell_price_usd = min_sell_price_usd
        self.ccxt_sell_price_upscale = ccxt_sell_price_upscale

    def update_cex_orderbook(self, limit=25, ignore_timer=False):
        update_cex_orderbook_timer_delay = 2
        if ignore_timer or self.cex_orderbook_timer is None or \
                time.time() - self.cex_orderbook_timer > update_cex_orderbook_timer_delay:
            #            ccxt_def.ccxt_call_fetch_order_book(init.my_ccxt,self.symbol,self.symbol)
            self.cex_orderbook = ccxt_def.ccxt_call_fetch_order_book(init.my_ccxt, self.symbol, self.symbol)
            # init.my_ccxt.fetch_order_book(self.symbol, limit)
            self.cex_orderbook_timer = time.time()

    def update_dex_orderbook(self):
        self.dex_orderbook = xb.dxgetorderbook(detail=3, maker=self.t1.symbol, taker=self.t2.symbol)
        del self.dex_orderbook['detail']

    def update_pricing(self, display=False):
        while self.t1.ccxt_price is None:
            self.t1.update_ccxt_price()
        while self.t2.ccxt_price is None:
            self.t2.update_ccxt_price()
        self.price = self.t1.ccxt_price / self.t2.ccxt_price
        if display:
            general_log.info("update_pricing: %s btc_p: %s, %s btc_p: %s, %s/%s price: %s" % (
                self.t1.symbol, self.t1.ccxt_price,
                self.t2.symbol, self.t2.ccxt_price,
                self.t1.symbol, self.t2.symbol, self.price
            ))

    def read_pair_dex_last_order_history(self):
        # print(self.dex_enabled)
        if self.dex_enabled:
            try:
                with open(
                        init.ROOT_DIR + '/data/' + self.strategy + '_' + self.t1.symbol + '_' + self.t2.symbol + '_last_order.pic',
                        'rb') as fp:
                    self.order_history = pickle.load(fp)
            except FileNotFoundError:
                general_log.info(f"File not found: {self.strategy}_{self.t1.symbol}_{self.t2.symbol}_last_order.pic")
            except Exception as e:
                general_log.error(f"read_pair_last_order_history: {type(e)}, {e}")
                self.order_history = None
                # pass

    def write_pair_dex_last_order_history(self):
        try:
            with open(
                    init.ROOT_DIR + '/data/' + self.strategy + '_' + self.t1.symbol + '_' + self.t2.symbol + '_last_order.pic',
                    'wb') as fp:
                pickle.dump(self.order_history, fp)
        except Exception as e:
            print('error write_pair_last_order_history:', type(e), e)
            # pass

    def create_dex_virtual_sell_order(self, display=True, manual_dex_price=None):
        # MADE FOR PINGPONG STRAT
        # SELL BLOCK BUY LTC
        #       T1       T2
        self.current_order = None
        try:
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

            if manual_dex_price:
                price = manual_dex_price
            else:
                price = self.min_sell_price_usd / self.t2.usd_price if self.min_sell_price_usd and self.t1.usd_price < self.min_sell_price_usd else self.price

            amount = self.amount_token_to_sell if self.strategy == 'basic_seller' else config_pp.usd_amount_custom.get(
                self.symbol, config_pp.usd_amount_default) / (self.t1.ccxt_price * init.t['BTC'].usd_price)
            spread = config_pp.arb_team_spread if config_pp.arb_team and self.symbol in config_pp.arb_team_pairs else config_pp.sell_price_offset

            self.current_order.update({
                'maker_size': amount,
                'taker_size': amount * (price * (1 + spread)),
                'dex_price': (amount * (price * (1 + spread))) / amount,
                'org_pprice': price,
                'org_t1price': self.t1.ccxt_price,
                'org_t2price': self.t2.ccxt_price,
            })

        except Exception as e:
            general_log.error(f"Error in create_virtual_sell_order: {type(e).__name__}, {e}")
            exit()

    def create_dex_virtual_buy_order(self, display=True, manual_dex_price=False):
        # MADE FOR PINGPONG STRAT
        # BUY ALWAYS COME AFTER A SELL FIRST, USE SELL ORDER AMOUNT (manual_dex_price).
        # SELL LTC BUY BLOCK
        #       T2       T1
        self.current_order = None
        if self.strategy == 'pingpong':
            try:
                self.current_order = {}
                self.current_order['symbol'] = self.symbol

                if manual_dex_price:
                    price = self.price if self.price < self.order_history['dex_price'] and not \
                        (config_pp.arb_team and self.symbol in config_pp.arb_team_pairs) else \
                        self.order_history['dex_price']
                else:
                    price = self.price

                self.current_order['manual_dex_price'] = manual_dex_price

                amount = float(self.order_history['maker_size'])
                spread = config_pp.spread_custom.get(self.symbol, config_pp.spread_default)

                if config_pp.arb_team and self.symbol in config_pp.arb_team_pairs:
                    spread = config_pp.arb_team_spread
                    print('arbteam')

                self.current_order['side'] = 'BUY'
                self.current_order['maker'] = self.t2.symbol
                self.current_order['maker_size'] = amount * price * (1 - spread)
                self.current_order['maker_address'] = self.t2.xb_address
                self.current_order['taker'] = self.t1.symbol
                self.current_order['taker_size'] = amount
                self.current_order['taker_address'] = self.t1.xb_address
                self.current_order['type'] = 'exact'
                # self.current_order['dryrun'] = True
                self.current_order['dex_price'] = self.current_order['maker_size'] / self.current_order['taker_size']
                self.current_order['org_pprice'] = price
                self.current_order['org_t1price'] = self.t1.ccxt_price
                self.current_order['org_t2price'] = self.t2.ccxt_price

                # Rest of the function...

            except Exception as e:
                general_log.error(f"Error in create_virtual_buy_order: {type(e).__name__}, {e}")
                exit()
        else:
            general_log.error(
                f"Bot strategy is {self.strategy}, no rule for this strat on create_dex_virtual_buy_order")

    def check_price_in_range(self, display=False):
        self.variation = None

        # Debug log: Log entering the function
        general_log.debug("Entering check_price_in_range_ancient")

        # Set the default tolerance
        if self.strategy == 'pingpong':
            price_variation_tolerance = config_pp.price_variation_tolerance
        elif self.strategy == 'basic_seller':
            price_variation_tolerance = 0.01
            # TODO: ADD PARAMETERS INPUT for 'basic_seller' if needed

        # Debug log: Log the strategy and price_variation_tolerance
        general_log.debug(f"Strategy: {self.strategy}, Price Variation Tolerance: {price_variation_tolerance}")

        if 'side' in self.current_order and self.current_order['manual_dex_price'] is True:
            # Debug log: Log manual_dex_price is True and 'side' is present
            general_log.debug("Manual DEX price is True and 'side' is present")

            # Calculate var based on the side
            if self.current_order['side'] == 'BUY' and (
                    not (self.strategy == 'pingpong' and self.symbol in config_pp.arb_team_pairs and config_pp.arb_team)
                    and self.price < self.order_history['org_pprice']):
                var = float(self.price / self.current_order['org_pprice'])
                # Debug log: Log that var is calculated based on 'BUY' side
                general_log.debug("Var calculated based on 'BUY' side")
            elif self.current_order['side'] == 'SELL' and self.price > self.order_history['org_pprice']:
                var = float(self.price / self.current_order['org_pprice'])
                # Debug log: Log that var is calculated based on 'SELL' side
                general_log.debug("Var calculated based on 'SELL' side")
            else:
                var = 1
                # Debug log: Log that var is set to 1
                general_log.debug("Var set to 1")
        else:
            # Debug log: Log manual_dex_price is False or 'side' is not present
            general_log.debug("Manual DEX price is False or 'side' is not present")

            # Calculate var based on strategy and conditions
            if self.strategy == 'basic_seller' and self.t1.usd_price < self.min_sell_price_usd:
                var = (self.min_sell_price_usd / self.t2.usd_price) / self.current_order['org_pprice']
                # Debug log: Log that var is calculated based on 'basic_seller' strategy and condition
                general_log.debug("Var calculated based on 'basic_seller' strategy and condition")
            else:
                var = float(self.price / self.current_order['org_pprice'])
                # Debug log: Log that var is calculated based on default strategy
                general_log.debug("Var calculated based on default strategy")

        # Debug log: Log the calculated var
        general_log.debug(f"Calculated Var: {var}")
        if isinstance(var, float):
            self.variation = float("{:.3f}".format(var))
        else:
            self.variation = [float("{:.3f}".format(self.price / self.current_order['org_pprice']))]
        # Debug log: Log the variation
        general_log.debug(f"Variation: {self.variation}")

        if display:
            general_log.info("%s_%s %s %s %s" % (
                self.symbol, str(self.variation),
                self.price, self.current_order['org_pprice'],
                self.price / self.current_order['org_pprice']
            ))

        # Check if the price is in range
        if 1 - price_variation_tolerance < var < 1 + price_variation_tolerance:
            # Debug log: Log that the price is in range
            general_log.debug("Price in range")
            return True
        else:
            # Debug log: Log that the price is not in range
            general_log.debug("Price not in range")
            return False

    def init_virtual_order(self, disabled_coins=None, display=True):
        if disabled_coins and (self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins):
            self.disabled = True
            general_log.info(self.symbol + ' disabled due to cc checks: ' + str(disabled_coins))
        if not self.disabled:
            if self.order_history is None or ("pingpong" in self.strategy and
                                              'side' in self.order_history and self.order_history['side'] == 'BUY'):
                self.create_dex_virtual_sell_order()
            elif 'side' in self.order_history and self.order_history['side'] == 'SELL':
                self.create_dex_virtual_buy_order(manual_dex_price=True)
            else:
                general_log.error('error during init_order\n' + str(self.order_history))
                exit()
            if display:
                general_log.info("init_virtual_order, Prices: %s %s %s" % (
                    self.symbol + str(["{:.8f}".format(self.price)]),
                    self.t1.symbol + '/USD' + str(["{:.2f}".format(self.t1.usd_price)]),
                    self.t2.symbol + '/USD' + str(["{:.2f}".format(self.t2.usd_price)])
                ))
                general_log.info(f"current_order: {self.current_order}")

    def dex_cancel_myorder(self):
        if self.dex_order and 'id' in self.dex_order:
            xb.cancelorder(self.dex_order['id'])
            self.dex_order = None
            self.current_order = None

    def dex_create_order(self, dry_mode=False):
        self.dex_order = None

        if not self.disabled:
            maker = self.current_order['maker']
            maker_size = "{:.6f}".format(self.current_order['maker_size'])

            bal = self.t2.dex_free_balance if self.current_order['side'] == "BUY" else self.t1.dex_free_balance
            valid = bal is not None and maker_size.replace('.', '').isdigit()

            general_log.debug(f"dex_create_order, maker: {maker}, maker_size: {maker_size}, bal: {bal}, valid: {valid}")

            if valid:
                if float(bal) > float(maker_size):
                    maker_address = self.current_order['maker_address']
                    taker = self.current_order['taker']
                    taker_size = "{:.6f}".format(self.current_order['taker_size'])
                    taker_address = self.current_order['taker_address']

                    general_log.info(
                        f"dex_create_order, Creating order. maker: {maker}, maker_size: {maker_size}, bal: {bal}")

                    if not dry_mode:
                        self.dex_order = xb.makeorder(maker, maker_size, maker_address, taker, taker_size,
                                                      taker_address)
                    else:
                        msg = f"xb.makeorder({maker}, {maker_size}, {maker_address}, {taker}, {taker_size}, {taker_address})"
                        general_log.info(f"dex_create_order, Dry mode enabled. {msg}")
                        print(f"{bcolors.mycolor.OKBLUE}{msg}{bcolors.mycolor.ENDC}")
                else:
                    general_log.error(f"dex_create_order, balance too low: {bal}, need: {maker_size} {maker}")
            else:
                general_log.error(f"dex_create_order, valid=False, bal={bal}, maker_size={maker_size}")

    def dex_check_order_status(self) -> int:
        """
        Return:
            2: INPROGRESS,
            1: FINISHED,
            0: OPEN,
            -1: ERROR,
            -2: CANCELLED WITHOUT CALL.
        """
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
                    general_log.error("Error in dex_check_order_status: 'status' not in order.")
                    general_log.error(f"Symbol: {self.symbol}, Error: {local_dex_order.get('error')}")
                    general_log.error(f"Current Order: {self.current_order}")
                    general_log.error(f"Dex Order: {self.dex_order}")
                    if self.strategy in ['pingpong', 'basic_seller']:
                        self.dex_order = None
                        return -2  # LOST TRACK, CONSIDER IT CANCELLED
                else:
                    general_log.warning("dex_check_order_status, 'status' not in order, counter: " + str(counter))
                    time.sleep(counter)

        self.dex_order = local_dex_order
        status_mapping = {
            "open": 0,
            "new": 0,
            "created": 2,
            "initialized": 2,
            "committed": 2,
            "finished": 1,
            "expired": -1,
            "offline": -1,
            "canceled": -2,
            "invalid": -1,
            "rolled back": -1,
            "rollback failed": -1
        }
        return status_mapping.get(self.dex_order.get('status'), 0)

    def check_price_variation(self, disabled_coins, display=False):
        global star_counter
        if 'side' in self.current_order and self.check_price_in_range(display=display) is False:
            msg = "check_price_variation, " + self.symbol + ", variation: " + "{:.3f}".format(self.variation) + \
                  ', ' + self.dex_order['status'] + ", live_price: " + "{:.8f}".format(self.price) + \
                  ", order_price: " + "{:.8f}".format(self.current_order['dex_price'])
            print(f"{bcolors.mycolor.WARNING}{msg}{bcolors.mycolor.ENDC}")
            if self.dex_order:
                msg = "check_price_variation, dex cancel: " + self.dex_order['id']
                print(f"{bcolors.mycolor.WARNING}{msg}{bcolors.mycolor.ENDC}")
                # general_log.info()
                self.dex_cancel_myorder()
            if self.strategy == 'pingpong':
                self.init_virtual_order(disabled_coins)
                if not self.dex_order:
                    self.dex_create_order()

            elif self.strategy == 'basic_seller':
                self.create_dex_virtual_sell_order()
                if self.dex_order is None:
                    self.dex_create_order(dry_mode=False)

    def status_check(self, disabled_coins=None, display=False):
        self.update_pricing()
        status = None

        if self.disabled and not (
                disabled_coins and (self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins)):
            self.disabled = False

        if self.dex_order and 'id' in self.dex_order:
            status = self.dex_check_order_status()
        else:
            if not self.disabled:
                # general_log.error(f"Order Missing: {self.dex_order}, {self.current_order}")
                self.init_virtual_order(disabled_coins)  # Renamed from create_virtual_order
                if self.dex_order and "id" in self.dex_order:
                    status = self.dex_check_order_status()

        if status == STATUS_OPEN:
            if disabled_coins and (self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins):
                if self.dex_order:
                    general_log.info(f'Disabled pairs due to cc_height_check {self.symbol}, {disabled_coins}')
                    general_log.info(f"status_check, dex cancel {self.dex_order['id']}")
                    self.dex_cancel_myorder()
            else:
                self.check_price_variation(disabled_coins, display=display)
        elif status == STATUS_FINISHED:
            self.dex_order_finished(disabled_coins)
        elif status == STATUS_OTHERS:
            self.check_price_in_range(display=display)
        elif status == STATUS_ERROR_SWAP:
            general_log.error('Order Error:\n' + str(self.current_order))
            general_log.error(self.dex_order)
            if self.strategy == 'pingpong':
                xb.cancelallorders()
                exit()
        elif status == STATUS_CANCELLED_WITHOUT_CALL:
            if self.dex_order and 'id' in self.dex_order:
                order_id = self.dex_order['id']
            else:
                order_id = None
            general_log.error(f'Order Error: {order_id} CANCELLED WITHOUT CALL')
            general_log.error(self.dex_order)
            self.dex_order = None
        else:
            if not self.disabled:
                general_log.error(
                    f"status_check, no valid status: {self.symbol}, {self.current_order}, {self.dex_order}")
                self.dex_create_order()

    def dex_order_finished(self, disabled_coins):
        msg = f"order FINISHED: {self.dex_order['id']}"
        general_log.info(msg)
        # general_log.info(self.current_order)
        # general_log.info(self.dex_order)
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
