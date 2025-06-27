# LOGIC:
# 1/BOT SELL T1 ON DEX AT {CEX MARKETPRICE * (1 + SPREAD)}
# 2/BOT BUY T1 ON DEX AT (min(live_price),max(SOLD PRICE * (1 - SPREAD)))
# 3/LOOP
#
# ONLY ONE AT A TIME, BOT RECORD THE LAST SELL ORDER ON A FILE, LOAD AT START

import asyncio
import os

from definitions.config_manager import ConfigManager
from starter import run_async_main  # Import run_async_main


def start():
    """Initialize ConfigManager and run the centralized main loop."""
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    config_manager = ConfigManager(strategy="pingpong")
    config_manager.initialize()

    async def run_startup_tasks():
        """Helper coroutine to run async startup tasks."""
        await config_manager.xbridge_manager.cancelallorders()
        await config_manager.xbridge_manager.dxflushcancelledorders()

    # Run the async startup tasks in a temporary event loop
    asyncio.run(run_startup_tasks())

    # Run the main bot logic, which will create and manage its own event loop.
    run_async_main(config_manager)

if __name__ == '__main__':
    start()
