import argparse
import sys

from definitions.cli_runner import run_cli, add_custom_help
from definitions.config_manager import ConfigManager
from definitions.starter import run_async_main


def start():
    """Parse CLI args, initialize ConfigManager, and run the continuous trading main loop."""

    parser = argparse.ArgumentParser(
        prog="main_thorchain_continuous",
        description="A THORChain continuous-chain trading bot that alternates swaps on a single\n"
                    "TOKEN1/TOKEN2 pool to achieve dual-token accumulation via volume asymmetry\n"
                    "and spread capture.",
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False  # Disable default help to add our own custom help flags
    )

    # Custom help argument to include '-help'
    add_custom_help(parser)

    parser.add_argument("--token1", type=str, default=None,
                        help="TOKEN1 symbol (e.g., LTC). If not provided, the 'token1' setting from config_thorchain_continuous.yaml is used.")
    parser.add_argument("--token2", type=str, default=None,
                        help="TOKEN2 symbol (e.g., DOGE). If not provided, the 'token2' setting from config_thorchain_continuous.yaml is used.")
    parser.add_argument("--target-spread", type=float, default=None,
                        help="The minimum target spread required to execute a trade (e.g., 0.01 for 1%%).\n"
                             "If not provided, the 'target_spread' setting from config_thorchain_continuous.yaml is used.")
    parser.add_argument("--dry-mode", action="store_true", default=None,
                        help="Dry run mode (simulate trades without execution).\n"
                             "If not provided, the 'dry_mode' setting from config_thorchain_continuous.yaml is used.")

    args = parser.parse_args()

    # Prevent running in live mode (still in testing)
    if not args.dry_mode:
        print("\n" + "=" * 60)
        print("ERROR: Continuous-chain trading strategy is still in testing phase")
        print("=" * 60)
        print("The continuous-chain trading strategy is not yet ready for production use.")
        print("Please run with '--dry-mode' flag for testing purposes only.")
        print("=" * 60 + "\n")
        sys.exit(1)

    config_manager = ConfigManager(strategy="thorchain_continuous")

    # Force dry_mode to True regardless of config
    final_dry_mode = True

    # Pass CLI args to be stored in strategy_config
    config_manager.initialize(
        token1=args.token1,
        token2=args.token2,
        target_spread=args.target_spread,
        dry_mode=final_dry_mode
    )

    if config_manager.strategy_instance:
        # The strategy will now automatically check for interrupted trades upon initialization.
        startup_tasks = config_manager.strategy_instance.get_startup_tasks()
        # run_async_main will handle the event loop creation and management.
        run_async_main(config_manager, startup_tasks=startup_tasks)
    else:
        config_manager.general_log.error("Failed to initialize strategy instance.")


if __name__ == '__main__':
    run_cli(start)
