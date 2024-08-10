## NOT FUNCTIONAL
def main_dx_get_markets(tokens_dict=None, preferred_token2=None):
    # Prioritize this preferred_token2  list as coin2 if possible
    if preferred_token2 is None:
        preferred_token2 = ["BTC", "LTC"]
    markets_list = []
    # print("Listing DX markets and retrieving orderbook:")
    for o, token1 in tokens_dict.items():
        for p, token2 in tokens_dict.items():
            if token1.symbol != token2.symbol and token1.dex_enabled and token2.dex_enabled:
                pairing_exist = any(x for x in markets_list if (x[0] == token1.symbol and x[1] == token2.symbol) or (
                        x[0] == token2.symbol and x[1] == token1.symbol))
                if not pairing_exist:
                    if token1.symbol in preferred_token2:
                        if token1.symbol in preferred_token2 and token2.symbol in preferred_token2:
                            index1 = preferred_token2.index(token1.symbol)
                            index2 = preferred_token2.index(token2.symbol)
                            if index1 > index2:
                                t1 = token1.symbol
                                t2 = token2.symbol
                            else:
                                t1 = token2.symbol
                                t2 = token1.symbol
                        else:
                            t1 = token2.symbol
                            t2 = token1.symbol
                    else:
                        t1 = token1.symbol
                        t2 = token2.symbol
                    markets_list.append([t1, t2])
    return markets_list


def main_dx_update_bals(display=False):
    import definitions.xbridge_def as xb
    import definitions.init as init
    xb_tokens = xb.getlocaltokens()
    tokens_dict = init.t
    for token in tokens_dict:
        if xb_tokens and tokens_dict[token].symbol in xb_tokens:
            utxos = xb.gettokenutxo(token, used=True)
            bal = 0
            bal_free = 0
            for utxo in utxos:
                if 'amount' in utxo:
                    bal += float(utxo['amount'])
                    if 'orderid' in utxo:
                        if utxo['orderid'] == '':
                            bal_free += float(utxo['amount'])
                    else:
                        print('no orderid in utxo:\n', utxo)
                else:
                    print('no amount in utxo:\n', utxo)
            tokens_dict[token].dex_total_balance = bal
            tokens_dict[token].dex_free_balance = bal_free
        else:
            tokens_dict[token].dex_total_balance = 0
            tokens_dict[token].dex_free_balance = 0
        if display:
            print(token, tokens_dict[token].dex_total_balance, tokens_dict[token].dex_free_balance)


def main_dx_update_orderbooks(pair_dict):
    from config import config_arbtaker as config
    for x, pair in pair_dict.items():
        if pair.t1.symbol not in config.dex_coins_disabled or pair.t2.symbol not in config.dex_coins_disabled:
            pair.update_dex_orderbook()
            if pair.dex_orderbook['asks'] or pair.dex_orderbook['bids']:
                pair.have_dex_orderbook = True
            else:
                pair.have_dex_orderbook = False


def symbols_check(pair, tokens_dict, pairs_dict):
    import definitions.init as init
    from definitions.classes import Pair
    my_ccxt = init.my_ccxt
    cex_symbol1 = None
    cex_symbol2 = None
    if pair.t1.symbol != 'BTC':
        cex_symbol1 = pair.t1.symbol + '/BTC'
    if cex_symbol1 and cex_symbol1 in my_ccxt.markets and 'ONLINE' in my_ccxt.markets[cex_symbol1]['info']['status']:
        if not (cex_symbol1 in pairs_dict):
            pairs_dict[cex_symbol1] = Pair(tokens_dict[pair.t1.symbol], tokens_dict['BTC'], strategy='arbtaker',
                                           dex_enabled=False)
        pair.cex_pair_1 = pairs_dict[cex_symbol1]
    else:
        pair.cex_pair_1 = None
    if pair.t2.symbol != 'BTC':
        cex_symbol2 = pair.t2.symbol + '/BTC'
    if cex_symbol2 and cex_symbol2 in my_ccxt.markets and 'ONLINE' in my_ccxt.markets[cex_symbol2]['info']['status']:
        if not (cex_symbol2 in pairs_dict):
            pairs_dict[cex_symbol2] = Pair(tokens_dict[pair.t2.symbol], tokens_dict['BTC'], strategy='arbtaker',
                                           dex_enabled=False)
        pair.cex_pair_2 = pairs_dict[cex_symbol2]
    else:
        pair.cex_pair_2 = None


def check_min_size(token, size, display=False):
    import config.config_arbtaker as config
    if token in config.min_size:
        if size > config.min_size[token]:
            result = True
        else:
            result = False
        if display:
            print('check_min_size', token, size, config.min_size[token], result)
    else:
        result = True
        if display:
            print('check_min_size, no minsize for', token, result)
    return result


def check_max_size(token, size, display=False):
    import config.config_arbtaker as config
    if token in config.max_size:
        if size < config.max_size[token]:
            result = True
        else:
            result = False
        if display:
            print('check_max_size', token, size, config.max_size[token], result)
    else:
        result = True
        if display:
            print('check_max_size, no maxsize for', token, result)
    return result


def check_size(order_data, tokens_dict):
    maker = order_data['maker']
    taker = order_data['taker']
    maker_size = float(order_data['maker_size'])
    taker_size = float(order_data['taker_size'])
    t1_tobtc = taker_size * tokens_dict[taker].ccxt_price
    t2_tobtc = maker_size * tokens_dict[maker].ccxt_price
    if check_min_size('BTC', t1_tobtc) and check_min_size('BTC', t2_tobtc) and \
            check_min_size(maker, maker_size) and check_min_size(taker, taker_size) and \
            check_max_size(maker, maker_size) and check_max_size(taker, taker_size):
        return True
    else:
        return False


def dex_select_order(side, pair, tokens_dict):
    # asks = sell maker to taker # bids = buy maker with taker
    import definitions.xbridge_def as xb
    if side == 'asks':
        orderslist = reversed(pair.dex_orderbook['asks'])
    elif side == 'bids':
        orderslist = pair.dex_orderbook['bids']
    selected_order = None
    for order in orderslist:
        order_data = xb.getorderstatus(order[2])
        if check_size(order_data, tokens_dict):
            selected_order = order_data
            break
    return selected_order


def calc_cex_depth_price(side, pair, qty):
    # COIN1/BTC
    # side: 'bids' ( i sell into buy book )  or 'asks' ( i buy into sell book )
    # input coin1 qty
    import definitions.init as init
    pair.update_cex_orderbook()
    count = 0
    done = False
    while not done:
        final_price_cex_book = 0
        quantity = qty
        executed_tobtc = 0
        count += 1
        if count == 2:
            message = "calc_cex_depth_price(" + pair.symbol + ", " + init.my_ccxt.name + ", " + str(qty) + \
                      " ) " + side + " error, count = " + str(count) + ", " + str(final_price_cex_book)
            print(message)
            pair.update_cex_orderbook(limit=500, ignore_timer=True)
        elif count == 3:
            print("calc_cex_coin1_depth_price, not enough depth on orderbook")
            return None, None
        count = 0
        for order in pair.cex_orderbook[side]:
            count += 1
            if order[1] > quantity:
                executed_quantity = quantity
                quantity = 0
                executed_tobtc += executed_quantity * order[0]
            elif order[1] <= quantity:
                executed_quantity = order[1]
                quantity -= executed_quantity
                executed_tobtc += executed_quantity * order[0]
            if quantity == 0:
                final_price_cex_book = order[0]
                done = True
                break
    if quantity == 0 and final_price_cex_book > 0:
        print('calc_cex_depth_price:', pair.symbol, side, qty, executed_tobtc, final_price_cex_book)
        return executed_tobtc, final_price_cex_book


def print_arb_info(xb_side, dex_order, pair):
    import definitions.init as init
    if xb_side == 'ask':
        dex_ask_price = float(dex_order['taker_size']) / float(dex_order['maker_size'])
        dex_ask_price_print = "{:.8f}".format(dex_ask_price)
        print('selected dex_ask_order:', dex_order)
        if pair.cex_pair_1:  # 1 HOP CEX
            cex_executed_tobtc, cex_final_price_book = calc_cex_depth_price(side='bids',
                                                                            pair=pair.cex_pair_1,
                                                                            qty=float(
                                                                                dex_order[
                                                                                    'maker_size']))
            tobtc_print = "{:.8f}".format(cex_executed_tobtc)
            toprice_print = "{:.8f}".format(cex_final_price_book)
            avg_price = cex_executed_tobtc / float(dex_order['maker_size'])
            avg_price_print = "{:.8f}".format(avg_price)
            msg_dx = f"{' ' * 10}{'Xbridge(' + pair.symbol + '):':<19} {'BUY':<5}{dex_order['maker_size']:<9} {dex_order['maker']:<6}{'SELL':<5}{dex_order['taker_size']:<10} {dex_order['taker']:<6}{'DEX_PRICE:':<11}{dex_ask_price_print}"
            msg_s1 = f"{' ' * 10}{init.my_ccxt.name + '(' + pair.cex_pair_1.symbol + '):':<19} {'SELL':<5}{dex_order['maker_size']:<9} {dex_order['maker']:<6}{'BUY':<5}{tobtc_print:<10} {'BTC':<6}{'AVG_PRICE:':<11}{avg_price_print} {'FINAL_PRICE:':<13}{toprice_print}"

            # print('XB(', pair.symbol, '), i buy:', dex_order['maker_size'], dex_order['maker'], 'i sell:',
            #       dex_order['taker_size'],
            #       dex_order['taker'], 'dex_ask_price:', dex_ask_price_print)
            # print('CEX_HOP1(', pair.cex_pair_1.symbol, '), i sell', dex_order['maker_size'], dex_order['maker'],
            #       'i buy', tobtc_print, 'BTC', 'final_cex_price:', toprice_print)
        if pair.cex_pair_2:  # 2 HOP CEX
            cex_executed_tobtc2, cex_final_price_book2 = calc_cex_depth_price(side='asks',
                                                                              pair=pair.cex_pair_2,
                                                                              qty=float(
                                                                                  dex_order[
                                                                                      'taker_size']))
            avg_price2 = cex_executed_tobtc2 / float(dex_order['taker_size'])
            avg_price2_print = "{:.8f}".format(avg_price2)
            tobtc2_print = "{:.8f}".format(cex_executed_tobtc2)
            toprice2_print = "{:.8f}".format(cex_final_price_book2)
            msg_s2 = f"{' ' * 10}{init.my_ccxt.name + '(' + pair.cex_pair_1.symbol + '):':<19} {'BUY':<5}{dex_order['taker_size']:<9} {dex_order['taker']:<6}{'SELL':<5}{tobtc2_print:<10} {'BTC':<6}{'AVG_PRICE:':<11}{avg_price2_print} {'FINAL_PRICE:':<13}{toprice2_print}"
            # print('CEX_HOP2(', pair.cex_pair_2.symbol, '), i buy', dex_order['taker_size'], dex_order['taker'],
            #       'i sell', tobtc2_print, 'BTC', 'final_cex_price:', toprice2_print)
            print(msg_dx)
            print(msg_s1)
            print(msg_s2)
            exit()
    elif xb_side == 'bid':
        print('selected dex_bid_order:', dex_order)
        dex_bid_price = float(dex_order['taker_size']) / float(dex_order['maker_size'])
        dex_ask_price_print = "{:.8f}".format(dex_bid_price)
        if pair.cex_pair_1:  # 1 HOP CEX
            cex_executed_tobtc, cex_final_price_book = calc_cex_depth_price(side='asks',
                                                                            pair=pair.cex_pair_1,
                                                                            qty=float(
                                                                                dex_order[
                                                                                    'taker_size']))
            tobtc_print = "{:.8f}".format(cex_executed_tobtc)
            toprice_print = "{:.8f}".format(cex_final_price_book)
            print('XB(', pair.symbol, '), i sell:', dex_order['taker_size'], dex_order['taker'], 'i buy:',
                  dex_order['maker_size'], dex_order['maker'], 'dex_bid_price:', dex_ask_price_print)
            print('CEX_HOP1(', pair.cex_pair_1.symbol, '), i buy', dex_order['taker_size'], dex_order['taker'],
                  'i sell', tobtc_print, 'BTC', 'final_cex_price:', toprice_print)
        if pair.cex_pair_2:  # 2 HOP CEX
            cex_executed_tobtc2, cex_final_price_book2 = calc_cex_depth_price(side='bids',
                                                                              pair=pair.cex_pair_2,
                                                                              qty=float(
                                                                                  dex_order[
                                                                                      'maker_size']))
            tobtc2_print = "{:.8f}".format(cex_executed_tobtc2)
            toprice2_print = "{:.8f}".format(cex_final_price_book2)
            print('CEX_HOP2(', pair.cex_pair_2.symbol, '), i sell', dex_order['maker_size'], dex_order['maker'],
                  'i buy', tobtc2_print, 'BTC', 'final_cex_price:', toprice2_print)


def main_cex_update_bals():
    import definitions.init as init
    import definitions.ccxt_def as ccxt_def
    my_ccxt = init.my_ccxt
    bals = ccxt_def.ccxt_call_fetch_free_balance(my_ccxt)
    tokens_dict = init.t
    for x, token in tokens_dict.items():
        if token.symbol in bals:
            token.cex_free_balance = float(bals[token.symbol])
        else:
            token.cex_free_balance = 0


def main():
    import definitions.init as init
    pairs_dict = init.p
    tokens_dict = init.t
    print('main_arbtaker')
    main_dx_update_orderbooks(pairs_dict)
    main_dx_update_bals()
    main_cex_update_bals()
    for x, token in tokens_dict.items():
        print(token.symbol, token.dex_free_balance)
    for x, pair in list(pairs_dict.items()):
        if pair.have_dex_orderbook:
            print([pair.symbol])  # ,pair.t1.xb_address,pair.t2.xb_address)
            pair.update_pricing()
            dex_ask_order = dex_select_order('asks', pair, tokens_dict)
            dex_bid_order = dex_select_order('bids', pair, tokens_dict)
            symbols_check(pair, tokens_dict, pairs_dict)
            if dex_ask_order:
                print_arb_info(xb_side='ask', dex_order=dex_ask_order, pair=pair)
            if dex_bid_order:
                print_arb_info(xb_side='bid', dex_order=dex_bid_order, pair=pair)


def start():
    import definitions.init as init
    init.init_arbtaker()
    main()


if __name__ == '__main__':
    print("Not ready for usage")
    # start()
