import argparse
import asyncio

from definitions.config_manager import ConfigManager
from starter import run_async_main


def start():
    """Parse CLI args, initialize ConfigManager, and run the arbitrage main loop."""
    parser = argparse.ArgumentParser(
        prog="main_arbitrage",
        description="Cross-exchange arbitrage bot between XBridge and Thorchain."
    )
    parser.add_argument("--live", action="store_true",
                        help="Run in live mode, executing real trades. Default is dry-run.")
    parser.add_argument("--min-profit", type=float, default=0.01,
                        help="Minimum profit margin to execute a trade (e.g., 0.01 for 1%).")

    args = parser.parse_args()

    config_manager = ConfigManager(strategy="arbitrage")
    # Pass CLI args to be stored in strategy_config
    config_manager.initialize(
        dry_mode=not args.live,
        min_profit_margin=args.min_profit
    )

    loop = asyncio.get_event_loop()
    run_async_main(config_manager, loop)


if __name__ == '__main__':
    start()
