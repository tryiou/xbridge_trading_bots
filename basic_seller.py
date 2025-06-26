import argparse
import asyncio
import logging

import definitions.xbridge_def as xb
from definitions.config_manager import ConfigManager
from starter import run_async_main


class ValidatePercentArg(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if not 0.001 <= values < 1:
            logging.error("Invalid argument: %s must be between 0.001 (inclusive) and 1 (exclusive).", self.dest)
            raise argparse.ArgumentError(self, "Value must be between 0.001 (inclusive) and 1 (exclusive).")
        setattr(namespace, self.dest, values)


def start():
    """Parse CLI args, initialize ConfigManager, and run the centralized main loop."""
    parser = argparse.ArgumentParser(
        prog="basic_seller",
        usage='%(prog)s [options]',
        description="Sell a specified amount of one token to buy another token using CCXT price tickers."
    )
    parser.add_argument("-tts", "--TokenToSell", required=True, help="Token to sell (e.g., BLOCK). Required.")
    parser.add_argument("-ttb", "--TokenToBuy", required=True, help="Token to buy (e.g., LTC). Required.")
    parser.add_argument("-atts", "--AmountTokenToSell", required=True, type=float,
                        help="Amount of TokenToSell to sell.")
    parser.add_argument("-mup", "--MinUsdPrice", required=True, type=float, help="Minimum USD sell price.")
    parser.add_argument("-spu", "--SellPriceUpscale", default=0.015, type=float, help="Sell price upscale.")
    parser.add_argument("-p", "--partial", type=float, default=None, action=ValidatePercentArg,
                        help="Partial minimum size.")

    args = parser.parse_args()

    config_manager = ConfigManager(strategy="basic_seller")
    config_manager.initialize(
        token_to_sell=args.TokenToSell,
        token_to_buy=args.TokenToBuy,
        amount_token_to_sell=args.AmountTokenToSell,
        min_sell_price_usd=args.MinUsdPrice,
        sell_price_offset=args.SellPriceUpscale,
        partial_percent=args.partial
    )

    xb.cancelallorders()
    xb.dxflushcancelledorders()

    loop = asyncio.get_event_loop()  # Get the current event loop
    run_async_main(config_manager, loop)  # Pass the loop to run_async_main


if __name__ == '__main__':
    start()
