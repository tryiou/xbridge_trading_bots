import argparse
import asyncio
import os

from definitions.config_manager import ConfigManager
from starter import run_async_main


def start():
    """Parse CLI args, initialize ConfigManager, and run the arbitrage main loop."""
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = argparse.ArgumentParser(
        prog="main_arbitrage",
        description="A cross-exchange arbitrage bot that identifies and evaluates opportunities between\n"
                    "the XBridge DEX and Thorchain. It operates by comparing order books and quotes\n"
                    "to find profitable trades.",
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False  # Disable default help to add our own custom help flags
    )

    # Custom help argument to include '-help'
    parser.add_argument(
        "-h", "-help", "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="Show this help message and exit."
    )

    parser.add_argument("--live", action="store_true",
                        help="Run in live mode, which executes real trades.\nIf this flag is not present, the bot runs in 'dry mode',\nidentifying and logging opportunities without placing orders.\nDefault: dry-run.")
    parser.add_argument("--min-profit", type=float, default=0.01,
                        help="The minimum profit margin required to consider an arbitrage\nopportunity valid for execution.\nFormat: a float representing the ratio (e.g., 0.01 for 1%%).\nDefault: 0.01 (1%%).")

    args = parser.parse_args()

    config_manager = ConfigManager(strategy="arbitrage")
    # Pass CLI args to be stored in strategy_config
    config_manager.initialize(
        dry_mode=not args.live,
        min_profit_margin=args.min_profit
    )

    # run_async_main will handle the event loop creation and management.
    run_async_main(config_manager)

if __name__ == '__main__':
    start()