import argparse
import asyncio
import logging
import signal
import sys
import time

import definitions.xbridge_def as xb
from definitions.config_manager import ConfigManager  # Import ConfigManager


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


def run_async_main(config_manager):
    """Runs the main asynchronous function using a new event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main(config_manager))


async def main(config_manager):
    """Main asynchronous function to start the trading operations."""
    controller = None  # Initialize controller to avoid reference before assignment warning
    try:

        xb.cancelallorders()
        xb.dxflushcancelledorders()

        from starter import MainController
        controller = MainController(config_manager)

        controller.main_init_loop()

        flush_timer = time.time()
        operation_timer = time.time()

        while True:
            current_time = time.time()

            if controller and controller.stop_order:
                print("Received stop_order")
                break

            if current_time - flush_timer > 15 * 60:
                xb.dxflushcancelledorders()
                flush_timer = current_time

            if current_time - operation_timer > 15:  # Main loop operations interval (in seconds)
                controller.main_loop()
                operation_timer = current_time

            await asyncio.sleep(1)  # Shorter sleep interval (in seconds)

    except (SystemExit, KeyboardInterrupt):
        print("Received Stop order. Cleaning up...")
        if controller:
            controller.stop_order = True
        xb.cancelallorders()
        exit()

    except Exception as e:
        logging.error(f"Exception in main loop: {e}")
        import traceback
        traceback.print_exc()
        if controller:
            controller.stop_order = True
        xb.cancelallorders()
        exit()


def start():
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
    sell_price_offset = args.SellPriceUpscale
    partial_value = args.partial
    msg = f"Sell {amount_token_to_sell} {token_to_sell} to {token_to_buy} // min_sell_price_usd: {min_sell_price_usd} // sell_price_offset: {sell_price_offset} // partial value: {partial_value}"
    logging.info(msg)
    config_manager = ConfigManager(strategy="basic_seller")
    config_manager.initialize(token_to_sell=token_to_sell,
                              token_to_buy=token_to_buy,
                              amount_token_to_sell=amount_token_to_sell,
                              min_sell_price_usd=min_sell_price_usd,
                              sell_price_offset=sell_price_offset,
                              partial_percent=partial_value)
    run_async_main(config_manager)


if __name__ == '__main__':
    start()
