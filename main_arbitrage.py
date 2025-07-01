import argparse
import asyncio

from definitions.config_manager import ConfigManager
from test_units.test_arbitrage_strategy import ArbitrageStrategyTester
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
    parser.add_argument("--test-leg", type=int, choices=[1, 2],
                        help="Run a specific arbitrage leg in test mode to verify the execution flow.\nLeg 1: Sell on XBridge, Buy on Thorchain.\nLeg 2: Buy on XBridge, Sell on Thorchain.")
    parser.add_argument("--run-tests", action="store_true",
                        help="Run the internal test suite for state management and recovery logic.")

    args = parser.parse_args()

    config_manager = ConfigManager(strategy="arbitrage")

    # Load defaults from config file
    config = config_manager.config_arbitrage
    config_dry_mode = getattr(config, 'dry_mode', True)
    config_min_profit = getattr(config, 'min_profit_margin', 0.01)

    # Determine final values. CLI arguments take precedence over the config file.
    final_dry_mode = (args.mode == 'dry') if args.mode else config_dry_mode
    final_min_profit = args.min_profit if args.min_profit is not None else config_min_profit

    # Pass CLI args to be stored in strategy_config
    config_manager.initialize(
        # dry_mode is True if mode is 'dry', False if 'live'.
        dry_mode=final_dry_mode,
        min_profit_margin=final_min_profit,
        test_mode=(args.test_leg is not None or args.run_tests)
    )

    if config_manager.strategy_instance:
        # The strategy will now automatically check for interrupted trades upon initialization.
        # No explicit call is needed here if the logic is in initialize_strategy_specifics.

        if args.test_leg:
            tester = ArbitrageStrategyTester(config_manager.strategy_instance)
            config_manager.general_log.info(f"--- Running Test for Arbitrage Leg {args.test_leg} ---")
            asyncio.run(tester.run_arbitrage_test(args.test_leg))
            config_manager.general_log.info(f"--- Test for Arbitrage Leg {args.test_leg} Finished ---")
        elif args.run_tests:
            tester = ArbitrageStrategyTester(config_manager.strategy_instance)
            asyncio.run(tester.run_all_tests())
        else:
            startup_tasks = config_manager.strategy_instance.get_startup_tasks()
            # run_async_main will handle the event loop creation and management.
            run_async_main(config_manager, startup_tasks=startup_tasks)
    else:
        config_manager.general_log.error("Failed to initialize strategy instance.")


if __name__ == '__main__':
    start()
