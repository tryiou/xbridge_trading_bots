# LOGIC:
# 1/BOT SELL T1 ON DEX AT {CEX MARKETPRICE * (1 + SPREAD)}
# 2/BOT BUY T1 ON DEX AT (min(live_price),max(SOLD PRICE * (1 - SPREAD)))
# 3/LOOP
#
# ONLY ONE AT A TIME, BOT RECORD THE LAST SELL ORDER ON A FILE, LOAD AT START

import definitions.xbridge_def as xb
from definitions.config_manager import ConfigManager
from starter import run_async_main


def start():
    """Initialize ConfigManager and run the centralized main loop."""
    config_manager = ConfigManager(strategy="pingpong")
    config_manager.initialize()
    xb.cancelallorders()
    xb.dxflushcancelledorders()

    run_async_main(config_manager)


if __name__ == '__main__':
    start()
