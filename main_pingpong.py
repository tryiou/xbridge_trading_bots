# LOGIC:
# 1/BOT SELL T1 ON DEX AT {CEX MARKETPRICE * (1 + SPREAD)}
# 2/BOT BUY T1 ON DEX AT (min(live_price),max(SOLD PRICE * (1 - SPREAD)))
# 3/LOOP
#
# ONLY ONE AT A TIME, BOT RECORD THE LAST SELL ORDER ON A FILE, LOAD AT START

import asyncio
import logging
import time
import traceback

import definitions.xbridge_def as xb
from definitions.config_manager import ConfigManager
from starter import MainController


def run_async_main():
    """Runs the main asynchronous function using a new event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())

    except (SystemExit, KeyboardInterrupt):
        print("Received Stop order. Cleaning up...")
        xb.cancelallorders()
        exit()

    except Exception as e:
        logging.error(f"Exception in main loop: {e}")
        traceback.print_exc()
        xb.cancelallorders()
        exit()


async def main():
    """Main asynchronous function to start the trading operations."""

    config_manager = ConfigManager(strategy="pingpong")
    config_manager.initialize()
    xb.cancelallorders()
    xb.dxflushcancelledorders()

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


def start():
    """Initializes the application and starts the main process."""
    run_async_main()


if __name__ == '__main__':
    start()
