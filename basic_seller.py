import argparse
import signal
import sys
import time
import traceback
import definitions.xbridge_def as xb
import definitions.init as init


class ValidatePercentArg(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        # Check if the provided value is within the valid range
        if not 0.001 <= values < 1:
            raise argparse.ArgumentError(self, "Value must be between 0.001 (inclusive) and 1 (exclusive).")
        setattr(namespace, self.dest, values)


def cancel_my_order():
    import definitions.init as init
    p = init.p
    print(p)
    for pair in p:
        if p[pair].dex_order:
            p[pair].dex_cancel_myorder()


def signal_handler(signal, frame):
    print('You pressed Ctrl+C - or killed me with -2')
    # .... Put your logic here .....
    cancel_my_order()
    sys.exit(0)


def update_ccxt_prices(tokens_dict, ccxt_i):
    ccxt_price_refresh = 2
    import definitions.ccxt_def as ccxt_def
    global ccxt_price_timer
    if ccxt_price_timer is None or time.time() - ccxt_price_timer > ccxt_price_refresh:
        keys = [f"{token}/USDT" if token == 'BTC' else f"{token}/BTC" for token in tokens_dict.keys() if
                token != 'BLOCK']
        keys.insert(0, keys.pop(keys.index('BTC/USDT')))
        done = False
        while not done:
            try:
                tickers = ccxt_def.ccxt_call_fetch_tickers(ccxt_i, keys)
                lastprice_string = "lastPrice" if ccxt_i.id == "binance" else "lastTradeRate"
                for token in [token for token in tokens_dict if token != 'BLOCK']:
                    symbol = f"{tokens_dict[token].symbol}/USDT" if (tokens_dict[token].symbol == 'BTC') \
                        else f"{tokens_dict[token].symbol}/BTC"
                    if tickers and symbol in tickers:
                        ccxt_price = float(tickers[symbol]['info'][lastprice_string])
                        tokens_dict[token].ccxt_price = ccxt_price if tokens_dict[token].symbol != 'BTC' else 1
                        tokens_dict[token].usd_price = ccxt_price * tokens_dict['BTC'].usd_price if tokens_dict[
                                                                                                        token].symbol != 'BTC' else ccxt_price
                    else:
                        print("update_ccxt_prices, missing symbol in tickers:", [symbol], tickers)
                        tokens_dict[token].ccxt_price = None
                        tokens_dict[token].usd_price = None
                done = True
            except Exception as e:
                print("general.update_ccxt_prices error:", type(e), str(e))

        if "BLOCK" in tokens_dict.keys():
            tokens_dict["BLOCK"].update_ccxt_price()
        ccxt_price_timer = time.time()


# def update_ccxt_prices(tokens_dict, ccxt_i):
#     ccxt_price_refresh = 2
#     import definitions.ccxt_def as ccxt_def
#     global ccxt_price_timer
#     if ccxt_price_timer is None or time.time() - ccxt_price_timer > ccxt_price_refresh:
#         keys = list(tokens_dict.keys())
#         for x, token in enumerate(keys):
#             if token == 'BTC':
#                 keys[x] = token + '/USDT'
#             else:
#                 keys[x] = token + '/BTC'
#         keys.insert(0, keys.pop(keys.index('BTC/USDT')))
#         done = False
#         while not done:
#             try:
#                 tickers = ccxt_def.ccxt_call_fetch_tickers(ccxt_i, keys)
#                 if ccxt_i.id == "binance":
#                     lastprice_string = "lastPrice"
#                 elif ccxt_i.id == "bittrex":
#                     lastprice_string = "lastTradeRate"
#                 for token in tokens_dict:
#                     if tokens_dict[token].symbol == 'BTC':
#                         symbol = tokens_dict[token].symbol + '/USDT'
#                         if tickers and symbol in tickers:
#                             tokens_dict[token].usd_price = float(tickers[symbol]['info'][lastprice_string])
#                             tokens_dict[token].ccxt_price = 1
#                             done = True
#                         else:
#                             print("update_ccxt_prices, missing symbol in tickers:", [symbol], tickers)
#                             tokens_dict[token].usd_price = None
#                             tokens_dict[token].ccxt_price = None
#                     else:
#                         symbol = tokens_dict[token].symbol + '/BTC'
#                         if tickers and symbol in tickers:
#                             tokens_dict[token].ccxt_price = float(tickers[symbol]['info'][lastprice_string])
#                             tokens_dict[token].usd_price = float(
#                                 tokens_dict[token].ccxt_price * tokens_dict['BTC'].usd_price)
#                             done = True
#                         else:
#                             print("update_ccxt_prices, missing symbol in tickers:", [symbol], tickers)
#                             tokens_dict[token].ccxt_price = None
#                             tokens_dict[token].usd_price = None
#             except Exception as e:
#                 print("general.update_ccxt_prices error:", type(e), str(e))
#         ccxt_price_timer = time.time()


def main_dx_update_bals(tokens_dict):
    xb_tokens = xb.getlocaltokens()
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
                    # else:
                    #     print('no orderid in utxo:\n', utxo)
                # else:
                #     print(token, 'no amount in utxo:\n', utxo, '**', utxos)
            tokens_dict[token].dex_total_balance = bal
            tokens_dict[token].dex_free_balance = bal_free
        else:
            tokens_dict[token].dex_total_balance = None
            tokens_dict[token].dex_free_balance = None


def thread_init(p):
    p.create_dex_virtual_sell_order()
    p.dex_create_order(dry_mode=False)


def main_init_loop(pairs_dict, tokens_dict, my_ccxt):
    from threading import Thread
    enable_threading = False
    threads = []
    start_time = time.perf_counter()
    max_threads = 10
    counter = 0
    for key, p in pairs_dict.items():
        update_ccxt_prices(tokens_dict, my_ccxt)
        p.update_pricing()
        if enable_threading:
            t = Thread(target=thread_init, args=(p,))
            threads.append(t)
            t.start()
            counter += 1
            if counter == max_threads:
                for t in threads:
                    t.join()
                threads = []
                counter = 0
            time.sleep(0.1)
        else:
            thread_init(p)


def thread_loop(p):
    p.status_check(display=True)


def main_loop(pairs_dict, tokens_dict, my_ccxt):
    from threading import Thread
    enable_threading = False
    threads = []
    max_threads = 10
    start_time = time.perf_counter()
    main_dx_update_bals(tokens_dict)
    for key, p in pairs_dict.items():
        update_ccxt_prices(tokens_dict, my_ccxt)
        if enable_threading:
            t = Thread(target=thread_loop, args=(p,))
            threads.append(t)
            t.start()
            counter += 1
            if counter == max_threads:
                for t in threads:
                    t.join()
                threads = []
                counter = 0
            time.sleep(0.1)
        else:
            thread_loop(p)
    end_time = time.perf_counter()
    print(f'loop took{end_time - start_time: 0.2f} second(s) to complete.')


def start(pair, tokens):
    flush_delay = 15 * 60
    flush_timer = None
    main_dx_update_bals(tokens)
    main_init_loop(pair, tokens, init.my_ccxt)
    while 1:
        try:
            if flush_timer is None or time.time() - flush_timer > flush_delay:
                xb.dxflushcancelledorders()
                flush_timer = time.time()
            main_loop(pair, tokens, init.my_ccxt)
            time.sleep(10)
        except Exception as e:
            print(type(e), str(e), e.args)
            traceback.print_exc()
            cancel_my_order()
            exit()


def main():
    global ccxt_price_timer
    signal.signal(signal.SIGINT, signal_handler)
    # Create the main argument parser
    parser = argparse.ArgumentParser(prog="basic_seller",
                                     usage='%(prog)s [options]',
                                     description="Sell a specified amount of one token to buy another token using CCXT price tickers.\n\n"
                                                 "Examples:\n"
                                                 "python3 basic_seller.py -tts BLOCK -ttb LTC -atts 50 -mup 0.2 -spu 0.02 --partial 0.5\n"
                                                 "python3 basic_seller.py -tts LTC -ttb BTC -atts 200 -mup 70",

                                     formatter_class=argparse.RawTextHelpFormatter)
    # Adding required arguments
    parser.add_argument("-tts", "--TokenToSell", required=True, help="Token to sell (e.g., BLOCK). Required. string")
    parser.add_argument("-ttb", "--TokenToBuy", required=True, help="Token to buy (e.g., LTC). Required. string")
    parser.add_argument("-atts", "--AmountTokenToSell", required=True, type=float,
                        help="Amount of 'TokenToSell' to sell. Required. float")
    parser.add_argument("-mup", "--MinUsdPrice", required=True, type=float,
                        help="Minimum USD sell price for 'TokenToSell'. Required. float")

    # Adding optional arguments
    parser.add_argument("-spu", "--SellPriceUpscale", default=0.015, type=float,
                        help="Percentage upscale on CCXT ticker price (e.g 0.015 for 1.5%% upscale), default is 0.015. Optional. float")

    parser.add_argument("-p", "--partial", type=float, default=None, action=ValidatePercentArg,
                        help="Partial minimum size as a percentage of total size (between 0.001 (inclusive) and 1 (exclusive)).\n"
                             "For example, '--partial 0.5' means sell 50%% of the specified amount, default is None. Optional. float")

    # Parse the command-line arguments
    args = parser.parse_args()

    token_to_sell = str(args.TokenToSell)
    token_to_buy = str(args.TokenToBuy)
    amount_token_to_sell = float(args.AmountTokenToSell)
    min_sell_price_usd = float(args.MinUsdPrice)
    ccxt_sell_price_upscale = float(args.SellPriceUpscale)
    partial_value = args.partial
    print("Sell", amount_token_to_sell, token_to_sell, "to", token_to_buy,
          "// min_sell_price_usd:", min_sell_price_usd,
          "// ccxt_sell_price_upscale", ccxt_sell_price_upscale,
          "// partial value:", partial_value)

    # LOAD FROM CONF FILE
    # token_to_sell = config.token_to_sell
    # token_to_buy = config.token_to_buy
    # amount_token_to_sell = config.amount_token_to_sell
    # min_sell_price_usd = config.min_sell_price_usd
    # ccxt_sell_price_upscale = config.ccxt_sell_price_upscale

    from definitions import init
    pair_symbol = token_to_sell + '/' + token_to_buy
    ccxt_price_timer = None
    init.init_basic_seller([token_to_sell, token_to_buy], amount_token_to_sell=amount_token_to_sell,
                           min_sell_price_usd=min_sell_price_usd,
                           ccxt_sell_price_upscale=ccxt_sell_price_upscale, partial_percent=partial_value)  # exit()
    tokens = init.t
    pair = init.p

    start(pair, tokens)


if __name__ == '__main__':
    main()
    # Initialize parser
