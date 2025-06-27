# LOGIC:
# 1/BOT SELL T1 ON DEX AT {CEX MARKETPRICE * (1 + SPREAD)}
# 2/BOT BUY T1 ON DEX AT (min(live_price),max(SOLD PRICE * (1 - SPREAD)))
# 3/LOOP
#
# ONLY ONE AT A TIME, BOT RECORD THE LAST SELL ORDER ON A FILE, LOAD AT START

import asyncio

from definitions.config_manager import ConfigManager
from starter import run_async_main  # Import run_async_main


def start():
    """Initialize ConfigManager and run the centralized main loop."""
    config_manager = ConfigManager(strategy="pingpong")
    config_manager.initialize()

    config_manager.xbridge_manager.cancelallorders()
    config_manager.xbridge_manager.dxflushcancelledorders()

    loop = asyncio.get_event_loop()  # Get the current event loop
    run_async_main(config_manager, loop)  # Pass the loop to run_async_main


if __name__ == '__main__':
    start()
