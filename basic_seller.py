import asyncio
from starter import main

import argparse
import signal
import logging
import sys
import definitions.init as init
import definitions.xbridge_def as xb

def signal_handler(signal, frame):
    logging.warning("Signal received: %s. Exiting...", signal)
    xb.cancelallorders()
    sys.exit(0)


class ValidatePercentArg(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if not 0.001 <= values < 1:
            logging.error("Invalid argument: %s must be between 0.001 (inclusive) and 1 (exclusive).", self.dest)
            raise argparse.ArgumentError(self, "Value must be between 0.001 (inclusive) and 1 (exclusive).")
        setattr(namespace, self.dest, values)

def run_async_main():
    """Runs the main asynchronous function using a new event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())

def start():
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

    logging.info("Sell %f %s to %s // min_sell_price_usd: %f // ccxt_sell_price_upscale: %f // partial value: %s",
                 amount_token_to_sell, token_to_sell, token_to_buy, min_sell_price_usd, ccxt_sell_price_upscale,
                 partial_value)

    pair_symbol = f"{token_to_sell}/{token_to_buy}"
    ccxt_price_timer = None

    init.init_basic_seller(
        [token_to_sell, token_to_buy],
        amount_token_to_sell=amount_token_to_sell,
        min_sell_price_usd=min_sell_price_usd,
        ccxt_sell_price_upscale=ccxt_sell_price_upscale,
        partial_percent=partial_value
    )
    run_async_main()



if __name__ == '__main__':
    start()


