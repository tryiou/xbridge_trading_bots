import argparse
import asyncio

from definitions.config_manager import ConfigManager
from definitions.starter import run_async_main


def start():
    """Parse CLI args, initialize ConfigManager, and run the arbitrage main loop."""

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

    parser.add_argument("--mode", choices=['live', 'dry'], default=None,
                        help="Specify the execution mode. 'live' executes real trades, 'dry' only logs them.\n"
                             "If not provided, the 'dry_mode' setting from config_arbitrage.yaml is used.")
    parser.add_argument("--min-profit", type=float, default=None,
                        help="The minimum profit margin required to execute a trade (e.g., 0.01 for 1%%).\n"
                             "If not provided, the 'min_profit_margin' setting from config_arbitrage.yaml is used.")

    args = parser.parse_args()

    config_manager = ConfigManager(strategy="arbitrage")

    # Determine final values. CLI arguments take precedence.
    # The strategy itself will handle loading from config if args are None.
    final_dry_mode = (args.mode == 'dry') if args.mode == 'dry' else (False if args.mode == 'live' else None)

    # Pass CLI args to be stored in strategy_config
    config_manager.initialize(
        dry_mode=final_dry_mode,
        min_profit_margin=args.min_profit  # Pass None if not provided, strategy will use its default
    )

    if config_manager.strategy_instance:
        # The strategy will now automatically check for interrupted trades upon initialization.
        startup_tasks = config_manager.strategy_instance.get_startup_tasks()
        # run_async_main will handle the event loop creation and management.
        run_async_main(config_manager, startup_tasks=startup_tasks)
    else:
        config_manager.general_log.error("Failed to initialize strategy instance.")


if __name__ == '__main__':
    start()
