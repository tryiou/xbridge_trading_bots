import argparse
import signal
import sys
import time
import traceback
from threading import Thread

import definitions.xbridge_def as xb
import definitions.init as init
import definitions.ccxt_def as ccxt_def


class ValidatePercentArg(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if not 0.001 <= values < 1:
            raise argparse.ArgumentError(self, "Value must be between 0.001 (inclusive) and 1 (exclusive).")
        setattr(namespace, self.dest, values)


def cancel_my_order():
    p = init.p
    for pair in p:
        if p[pair].dex_order:
            p[pair].dex_cancel_myorder()


def signal_handler(signal, frame):
    print('You pressed Ctrl+C - or killed me with -2')
    cancel_my_order()
    sys.exit(0)


def update_ccxt_prices(tokens_dict, ccxt_i):
    global ccxt_price_timer
    ccxt_price_refresh = 2

    if ccxt_price_timer is None or time.time() - ccxt_price_timer > ccxt_price_refresh:
        try:
            keys = [
                f"{token}/USDT" if token == 'BTC' else f"{token}/BTC"
                for token in tokens_dict.keys() if token != 'BLOCK'
            ]
            keys.insert(0, keys.pop(keys.index('BTC/USDT')))

            tickers = ccxt_def.ccxt_call_fetch_tickers(ccxt_i, keys)
            lastprice_string = (
                "last" if ccxt_i.id == "kucoin" else
                "lastPrice" if ccxt_i.id == "binance" else
                "lastTradeRate"
            )

            for token, token_data in tokens_dict.items():
                if token == 'BLOCK':
                    continue

                symbol = (
                    f"{token_data.symbol}/USDT"
                    if token_data.symbol == 'BTC' else
                    f"{token_data.symbol}/BTC"
                )

                if tickers and symbol in tickers:
                    ccxt_price = float(tickers[symbol]['info'][lastprice_string])
                    token_data.ccxt_price = ccxt_price if token_data.symbol != 'BTC' else 1
                    token_data.usd_price = (
                        ccxt_price * tokens_dict['BTC'].usd_price
                        if token_data.symbol != 'BTC' else ccxt_price
                    )
                else:
                    print("update_ccxt_prices, missing symbol in tickers:", [symbol], tickers)
                    token_data.ccxt_price = None
                    token_data.usd_price = None

            if "BLOCK" in tokens_dict:
                tokens_dict["BLOCK"].update_ccxt_price()

            ccxt_price_timer = time.time()

        except Exception as e:
            print("update_ccxt_prices error:", type(e), str(e))


def main_dx_update_bals(tokens_dict):
    xb_tokens = xb.getlocaltokens()

    for token, token_data in tokens_dict.items():
        if xb_tokens and token_data.symbol in xb_tokens:
            utxos = xb.gettokenutxo(token, used=True)
            bal = sum(float(utxo.get('amount', 0)) for utxo in utxos)
            bal_free = sum(
                float(utxo['amount']) for utxo in utxos
                if 'orderid' in utxo and not utxo['orderid']
            )
            token_data.dex_total_balance = bal
            token_data.dex_free_balance = bal_free
        else:
            token_data.dex_total_balance = None
            token_data.dex_free_balance = None


def thread_init(p):
    p.create_dex_virtual_sell_order()
    p.dex_create_order(dry_mode=False)


def main_init_loop(pairs_dict, tokens_dict, my_ccxt):
    enable_threading = False
    max_threads = 10

    for key, p in pairs_dict.items():
        update_ccxt_prices(tokens_dict, my_ccxt)
        p.update_pricing()

        if enable_threading:
            threads = []
            for i in range(0, len(pairs_dict), max_threads):
                for p in list(pairs_dict.values())[i:i + max_threads]:
                    t = Thread(target=thread_init, args=(p,))
                    threads.append(t)
                    t.start()
                for t in threads:
                    t.join()
                time.sleep(0.1)
        else:
            thread_init(p)


def thread_loop(p):
    p.status_check(display=True)


def main_loop(pairs_dict, tokens_dict, my_ccxt):
    enable_threading = False
    max_threads = 10

    start_time = time.perf_counter()
    main_dx_update_bals(tokens_dict)

    for key, p in pairs_dict.items():
        update_ccxt_prices(tokens_dict, my_ccxt)

        if enable_threading:
            threads = []
            for i in range(0, len(pairs_dict), max_threads):
                for p in list(pairs_dict.values())[i:i + max_threads]:
                    t = Thread(target=thread_loop, args=(p,))
                    threads.append(t)
                    t.start()
                for t in threads:
                    t.join()
                time.sleep(0.1)
        else:
            thread_loop(p)

    end_time = time.perf_counter()
    print(f'loop took {end_time - start_time: 0.2f} second(s) to complete.')


def start(pair, tokens):
    flush_delay = 15 * 60
    flush_timer = None

    main_dx_update_bals(tokens)
    main_init_loop(pair, tokens, init.my_ccxt)

    while True:
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
            sys.exit(1)


def main():
    global ccxt_price_timer
    signal.signal(signal.SIGINT, signal_handler)

    parser = argparse.ArgumentParser(
        prog="basic_seller",
        usage='%(prog)s [options]',
        description=(
            "Sell a specified amount of one token to buy another token using CCXT price tickers.\n\n"
            "Examples:\n"
            "python3 basic_seller.py -tts BLOCK -ttb LTC -atts 50 -mup 0.2 -spu 0.02 --partial 0.5\n"
            "python3 basic_seller.py -tts LTC -ttb BTC -atts 200 -mup 70"
        ),
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument("-tts", "--TokenToSell", required=True, help="Token to sell (e.g., BLOCK). Required. string")
    parser.add_argument("-ttb", "--TokenToBuy", required=True, help="Token to buy (e.g., LTC). Required. string")
    parser.add_argument("-atts", "--AmountTokenToSell", required=True, type=float,
                        help="Amount of 'TokenToSell' to sell. Required. float")
    parser.add_argument("-mup", "--MinUsdPrice", required=True, type=float,
                        help="Minimum USD sell price for 'TokenToSell'. Required. float")

    parser.add_argument("-spu", "--SellPriceUpscale", default=0.015, type=float,
                        help="Percentage upscale on CCXT ticker price (e.g 0.015 for 1.5%% upscale), default is 0.015. Optional. float")

    parser.add_argument("-p", "--partial", type=float, default=None, action=ValidatePercentArg,
                        help="Partial minimum size as a percentage of total size (between 0.001 (inclusive) and 1 (exclusive)).\n"
                             "For example, '--partial 0.5' means sell 50%% of the specified amount, default is None. Optional. float")

    args = parser.parse_args()

    token_to_sell = args.TokenToSell
    token_to_buy = args.TokenToBuy
    amount_token_to_sell = args.AmountTokenToSell
    min_sell_price_usd = args.MinUsdPrice
    ccxt_sell_price_upscale = args.SellPriceUpscale
    partial_value = args.partial

    print("Sell", amount_token_to_sell, token_to_sell, "to", token_to_buy,
          "// min_sell_price_usd:", min_sell_price_usd,
          "// ccxt_sell_price_upscale", ccxt_sell_price_upscale,
          "// partial value:", partial_value)

    pair_symbol = f"{token_to_sell}/{token_to_buy}"
    ccxt_price_timer = None

    init.init_basic_seller(
        [token_to_sell, token_to_buy],
        amount_token_to_sell=amount_token_to_sell,
        min_sell_price_usd=min_sell_price_usd,
        ccxt_sell_price_upscale=ccxt_sell_price_upscale,
        partial_percent=partial_value
    )

    tokens = init.t
    pair = init.p

    start(pair, tokens)


if __name__ == '__main__':
    main()
