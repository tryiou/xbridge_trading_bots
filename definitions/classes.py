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

general_log = None
trade_log = None


# from config.config_pingpong import usd_amount_default, usd_amount_custom, spread_default, spread_custom, \
#     price_variation_tolerance
def setup_logger(strategy=None):
    global general_log, trade_log
    if strategy:
        general_log = logger.setup_logger(name="GENERAL_LOG",
                                          log_file=init.ROOT_DIR + '/logs/' + strategy + '_general.log',
                                          level=logging.INFO, console=True)
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
            except Exception as e:
                print(self.strategy, self.symbol)
                general_log.info('data/' + self.strategy + '_' + self.symbol + '_addr.pic ' + str(type(e)) + ', ' + str(e))
                # print('data/' + self.strategy + '_' + self.symbol + '_addr.pic', type(e), e)
                self.dx_request_addr()

    def write_xb_address(self):
        if self.dex_enabled:
            try:
                with open(init.ROOT_DIR + '/data/' + self.strategy + '_' + self.symbol + '_addr.pic', 'wb') as fp:
                    pickle.dump(self.xb_address, fp)
            except Exception as e:
                general_log.info('data/' + self.strategy + '_' + self.symbol + '_addr.pic ' + str(type(e)) + ', ' + str(e))
                # print('data/' + self.strategy + '_' + self.symbol + '_addr.pic', type(e), e)
                # pass

    def dx_request_addr(self):
        self.xb_address = xb.getnewtokenadress(self.symbol)[0]
        general_log.info('dx_request_addr: ' + self.symbol + ', ' + self.xb_address)
        self.write_xb_address()

    def update_ccxt_price(self, display=False):
        update_ccxt_price_delay = 2
        if self.ccxt_price_timer is None or \
                time.time() - self.ccxt_price_timer > update_ccxt_price_delay:
            done = False
            count = 0
            if self.symbol == "BTC":
                cex_symbol = "BTC/USD"
            else:
                # init.t['BTC'].update_ccxt_price()
                cex_symbol = self.symbol + '/BTC'
            if cex_symbol in init.my_ccxt.symbols:
                while not done:
                    count += 1
                    try:
                        result = float(
                            ccxt_def.ccxt_call_fetch_ticker(init.my_ccxt, cex_symbol)['info']['lastTradeRate'])
                    except Exception as e:
                        general_log.error(
                            "update_ccxt_price: error(" + str(count) + "): " + str(type(e)) + ', ' + str(e))
                        # print("update_ccxt_price: error(" + str(count) + "):", type(e), e)
                        time.sleep(count)
                        # pass
                    else:
                        if result:
                            if self.symbol == "BTC":
                                self.usd_price = result
                                self.ccxt_price = 1
                            else:
                                self.ccxt_price = result
                                self.usd_price = self.ccxt_price * init.t['BTC'].usd_price
                            self.ccxt_price_timer = time.time()
                            done = True
            else:
                general_log.info(cex_symbol + " not in cex " + str(init.my_ccxt))
                # print(cex_symbol, "not in cex", init.my_ccxt)
                self.usd_price = None
                self.ccxt_price = None
        else:
            if display:
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
        self.current_order = None
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
        self.var = None
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
            print(self.t1.symbol, 'btc_p:', self.t1.ccxt_price, ',', self.t2.symbol, 'btc_p:', self.t2.ccxt_price, ',',
                  self.t1.symbol + '/' + self.t2.symbol, 'price:', self.price)

    def read_pair_dex_last_order_history(self):
        # print(self.dex_enabled)
        if self.dex_enabled:
            try:
                with open(
                        init.ROOT_DIR + '/data/' + self.strategy + '_' + self.t1.symbol + '_' + self.t2.symbol + '_last_order.pic',
                        'rb') as fp:
                    self.order_history = pickle.load(fp)
            except Exception as e:
                print('error read_pair_last_order_history:', type(e), e)
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
        if self.strategy == 'pingpong':
            try:
                self.current_order = {}
                self.current_order['symbol'] = self.symbol
                if manual_dex_price:
                    price = manual_dex_price
                    self.current_order['manual_dex_price'] = True
                else:
                    price = self.price
                    self.current_order['manual_dex_price'] = False
                # CALC AMOUNT FROM USD AMOUNT
                # init.t['BTC'].update_ccxt_price()
                if self.symbol in config_pp.usd_amount_custom:
                    usd_amount = config_pp.usd_amount_custom[self.symbol]
                else:
                    usd_amount = config_pp.usd_amount_default
                amount = usd_amount / (self.t1.ccxt_price * init.t['BTC'].usd_price)
                self.current_order['side'] = 'SELL'
                self.current_order['maker'] = self.t1.symbol
                self.current_order['maker_size'] = amount
                self.current_order['maker_address'] = self.t1.xb_address
                self.current_order['taker'] = self.t2.symbol
                # if self.symbol in config.spread_custom:
                #     spread = config.spread_custom[self.symbol]
                # else:
                spread = config_pp.sell_price_offset

                if config_pp.arb_team and self.symbol in config_pp.arb_team_pairs:
                    print('arbteam')
                    self.current_order['taker_size'] = amount * price * (1 - config_pp.arb_team_spread)
                else:
                    self.current_order['taker_size'] = amount * (price * (1 + spread))
                self.current_order['taker_address'] = self.t2.xb_address
                self.current_order['type'] = 'exact'
                # self.current_order['dryrun'] = True
                self.current_order['dex_price'] = self.current_order['taker_size'] / self.current_order['maker_size']
                self.current_order['org_pprice'] = price
                self.current_order['org_t1price'] = self.t1.ccxt_price
                self.current_order['org_t2price'] = self.t2.ccxt_price
            except Exception as e:
                general_log.error("error create_virtual_sell_order: " + str(type(e)) + ', ' + str(e))
                exit()
        elif self.strategy == 'basic_seller':
            try:
                self.current_order = {}
                self.current_order['symbol'] = self.symbol
                if manual_dex_price:
                    price = manual_dex_price
                    self.current_order['manual_dex_price'] = True
                else:
                    if self.min_sell_price_usd and self.t1.usd_price < self.min_sell_price_usd:
                        price = self.min_sell_price_usd / self.t2.usd_price
                    else:
                        price = self.price
                    self.current_order['manual_dex_price'] = False
                amount = self.amount_token_to_sell
                spread = self.ccxt_sell_price_upscale
                self.current_order['side'] = 'SELL'
                self.current_order['maker'] = self.t1.symbol
                self.current_order['maker_size'] = amount
                self.current_order['maker_address'] = self.t1.xb_address
                self.current_order['taker'] = self.t2.symbol
                self.current_order['taker_size'] = amount * (price * (1 + spread))
                self.current_order['taker_address'] = self.t2.xb_address
                self.current_order['dex_price'] = self.current_order['taker_size'] / self.current_order['maker_size']
                self.current_order['org_pprice'] = price
                self.current_order['org_t1price'] = self.t1.ccxt_price
                self.current_order['org_t2price'] = self.t2.ccxt_price
            except Exception as e:
                general_log.error("error create_virtual_sell_order: " + str(type(e)) + ', ' + str(e))
                exit()
        else:
            print('bot strategy is', self.strategy, 'no rule for this strat on create_dex_virtual_sell_order')

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
                    if self.price < self.order_history['dex_price'] and not \
                            (config_pp.arb_team and self.symbol in config_pp.arb_team_pairs):
                        price = self.price
                    else:
                        if config_pp.arb_team and self.symbol in config_pp.arb_team_pairs:
                            print('arbteam')
                        price = self.order_history['dex_price']
                else:
                    price = self.price
                self.current_order['manual_dex_price'] = manual_dex_price
                amount = float(self.order_history['maker_size'])
                if self.symbol in config_pp.spread_custom:
                    spread = config_pp.spread_custom[self.symbol]
                elif config_pp.arb_team and self.symbol in config_pp.arb_team_pairs:
                    spread = config_pp.arb_team_spread
                else:
                    spread = config_pp.spread_default
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

                # print(self.order_history['maker_size'], self.order_history['taker_size'],
                #       self.current_order['maker_size'],
                #       self.current_order['taker_size'])
            except Exception as e:
                general_log.error("error create_virtual_buy_order: " + str(type(e)) + ', ' + str(e))
                exit()
        else:
            print('bot strategy is', self.strategy, 'no rule for this strat on create_dex_virtual_buy_order')

    def check_price_in_range(self, display=False):
        self.var = None
        if self.strategy == 'pingpong':
            price_variation_tolerance = config_pp.price_variation_tolerance
        elif self.strategy == 'basic_seller':
            price_variation_tolerance = 0.01  # TODO ADD PARAMETERS INPUT
        if 'side' in self.current_order:
            if self.current_order['manual_dex_price'] is True:
                if self.current_order['side'] == 'BUY':
                    if (
                            self.strategy == 'pingpong' and self.symbol in config_pp.arb_team_pairs and config_pp.arb_team) is False and self.price < \
                            self.order_history['org_pprice']:
                        var = float(self.price / self.current_order['org_pprice'])
                    else:
                        var = 1
                elif self.current_order['side'] == 'SELL':
                    if self.price > self.order_history['org_pprice']:
                        var = float(self.price / self.current_order['org_pprice'])
                    else:
                        var = 1
            else:
                if self.strategy == 'basic_seller' and self.t1.usd_price < self.min_sell_price_usd:
                    var = (self.min_sell_price_usd / self.t2.usd_price) / self.current_order['org_pprice']
                else:
                    var = float(self.price / self.current_order['org_pprice'])
        if isinstance(var, float):
            self.var = float("{:.3f}".format(var))
        else:
            self.var = [float("{:.3f}".format(self.price / self.current_order['org_pprice']))]
        if display:
            print(self.symbol + '_' + str(self.var), self.price, self.current_order['org_pprice'],
                  self.price / self.current_order['org_pprice'])  # , end='*')
        if 1 - price_variation_tolerance < var < 1 + price_variation_tolerance:
            # Price in range
            return True
        else:

            return False

    def init_virtual_order(self, disabled_coins=None, display=True):
        if disabled_coins and (self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins):
            self.disabled = True
            general_log.info(self.symbol + ' disabled due to cc checks: ' + str(disabled_coins))
        if not self.disabled:
            if self.order_history is None or ('side' in self.order_history and self.order_history['side'] == 'BUY'):
                self.create_dex_virtual_sell_order()
            elif 'side' in self.order_history and self.order_history['side'] == 'SELL':
                self.create_dex_virtual_buy_order(manual_dex_price=True)
            else:
                general_log.error('error during init_order\n' + str(self.order_history))
                exit()
            if display:
                print("init_virtual_order, Prices:", self.symbol + str(["{:.8f}".format(self.price)]),
                      self.t1.symbol + '/USD' +
                      str(["{:.2f}".format(self.t1.usd_price)]),
                      self.t2.symbol + '/USD' + str(["{:.2f}".format(self.t2.usd_price)]))
                print(self.current_order)

    def dex_cancel_myorder(self):
        if self.dex_order and 'id' in self.dex_order:
            xb.cancelorder(self.dex_order['id'])
            self.dex_order = None
            self.current_order = None

    def dex_create_order(self, dry_mode=False):
        self.dex_order = None
        if not self.disabled:
            # my_dex_bals = xb.gettokenbalances()
            # print(my_dex_bals)
            maker = self.current_order['maker']
            maker_size = "{:.6f}".format(self.current_order['maker_size'])
            # if maker in my_dex_bals:
            if self.current_order['side'] == "BUY":
                bal = self.t2.dex_free_balance
            else:
                bal = self.t1.dex_free_balance
            print(bal, maker_size)
            valid = True if bal and maker_size else False
            if valid:
                if float(bal) > float(maker_size):
                    maker_address = self.current_order['maker_address']
                    taker = self.current_order['taker']
                    taker_size = "{:.6f}".format(self.current_order['taker_size'])
                    taker_address = self.current_order['taker_address']
                    if not dry_mode:
                        self.dex_order = xb.makeorder(maker, maker_size, maker_address, taker, taker_size,
                                                      taker_address)
                    else:
                        msg = "xb.makeorder( " + maker + ', ' + maker_size + ', ' + maker_address + ', ' + taker + ', ' + taker_size + ', ' + taker_address + " )"
                        print(f"{bcolors.mycolor.OKBLUE}{msg}{bcolors.mycolor.ENDC}")
                else:
                    general_log.error(
                        "dex_create_order, balance too low: " + str(bal) + ", need: " + str(maker_size))
            else:
                general_log.error("dex_create_order, valid=False, bal=" + str(bal) + ", maker_size=" + str(maker_size))

    def dex_check_order_status(self):
        # RETURN 1 if FINISHED, 0 if OPEN, -1 if ERROR, -2 if CANCELLED WITHOUT CALL , 2 if INPROGRESS
        done = False
        counter = 0
        max_count = 3
        while not done:
            # self.dex_order \
            try:
                local_dex_order = xb.getorderstatus(self.dex_order['id'])
            except Exception as e:
                print("dex_check_order_status", type(e), e, '\n' + str(self.dex_order))
                # pass
            if 'status' in local_dex_order:
                done = True
            else:
                counter += 1
                if counter == max_count:
                    general_log.error("dex_check_order_status, 'status' not in order, ")
                    general_log.error(self.symbol + ': ' + str(local_dex_order['error']))
                    general_log.error(self.current_order)
                    general_log.error(self.dex_order)
                    # if self.strategy == 'pingpong':
                    #     xb.cancelallorders()

                    if self.strategy == 'pingpong':
                        self.dex_order = None
                        return -2  # LOST TRACK,CONSIDER IT CANCELLED
                    elif self.strategy == 'basic_seller':
                        self.dex_order = None
                        return -2  # LOST TRACK,CONSIDER IT CANCELLED
                else:
                    general_log.error("dex_check_order_status, 'status' not in order, counter: " + str(counter))
                    time.sleep(counter)
        self.dex_order = local_dex_order
        if self.dex_order['status'] == "open":
            return 0
        elif self.dex_order['status'] == "new":
            return 0
        elif self.dex_order['status'] == "created":
            return 2
        elif self.dex_order['status'] == "initialized":
            return 2
        elif self.dex_order['status'] == "commited":
            return 2
        elif self.dex_order['status'] == "finished":
            return 1
        elif self.dex_order['status'] == "expired":
            return -1
        elif self.dex_order['status'] == "offline":
            return -1
        elif self.dex_order['status'] == "canceled":
            return -2
        elif self.dex_order['status'] == "invalid":
            return -1
        elif self.dex_order['status'] == "rolled back":
            return -1
        elif self.dex_order['status'] == "rollback failed":
            return -1

    def check_price_variation(self, disabled_coins, display=False):
        global star_counter
        if 'side' in self.current_order and self.check_price_in_range(display=display) is False:
            msg = "check_price_variation, " + self.symbol + ", variation: " + "{:.3f}".format(self.var) + \
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
                if self.dex_order is None:
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
                general_log.error("Order Missing:" + str(self.dex_order) + ', ' + str(self.current_order))
                print(self.t1.symbol, self.t1.dex_free_balance, self.t2.symbol, self.t2.dex_free_balance)
                if self.strategy == 'pingpong':
                    self.init_virtual_order(disabled_coins)
                    self.dex_create_order()
                    if self.dex_order and "id" in self.dex_order:
                        status = self.dex_check_order_status()
                elif self.strategy == 'basic_seller':
                    self.create_dex_virtual_sell_order()
                    self.dex_create_order(dry_mode=False)
        if status == 0:  # OPEN
            if disabled_coins and (self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins):
                if self.dex_order:
                    general_log.info(
                        'disabled pairs due to cc_height_check ' + self.symbol + ', ' + str(disabled_coins))
                    general_log.info("status_check, dex cancel " + self.dex_order['id'])
                    self.dex_cancel_myorder()
            else:
                self.check_price_variation(disabled_coins, display=display)  # CANCEL IF PRICE VARIATION IS PAST SET %
        elif status == 1:  # FINISHED
            self.dex_order_finished(disabled_coins)  # LOG AND PREPARE REVERSE ORDER
        elif status == 2:  # OTHERS
            self.check_price_in_range(display=display)  # NO ACTION
        elif status == -1:  # ERROR DURING SWAP, WAIT FOR REFUND/EXIT
            print()
            general_log.error('order ERROR:\n' + str(self.current_order))
            general_log.error(self.dex_order)
            if self.strategy == 'pingpong':
                xb.cancelallorders()
            exit()
        elif status == -2:  # CANCELLED WITHOUT CALL
            if self.dex_order and 'id' in self.dex_order:
                orderid = self.dex_order['id']
            else:
                orderid = None
            general_log.error('order ERROR: ' + str(orderid) + " CANCELLED WITHOUT CALL")
            general_log.error(self.dex_order)
            if self.strategy == 'pingpong':
                self.init_virtual_order(disabled_coins)
                self.dex_create_order()
            elif self.strategy == 'basic_seller':
                self.create_dex_virtual_sell_order()
                self.dex_create_order(dry_mode=False)
        else:
            if not self.disabled:
                general_log.error(
                    "status_check, no valid status: " + self.symbol + ', ' + self.current_order['side'] + ', ' + str(
                        self.dex_order))

    def dex_order_finished(self, disabled_coins):
        msg = 'order FINISHED: ' + self.dex_order['id']
        # general_log.info(msg)
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
