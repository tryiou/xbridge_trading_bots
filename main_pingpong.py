import argparse
import asyncio

from definitions.config_manager import ConfigManager
from definitions.starter import run_async_main  # Import run_async_main
from test_units.test_pingpong_strategy import PingPongStrategyTester


def start():
    """Initialize ConfigManager and run the centralized main loop."""

    parser = argparse.ArgumentParser(
        prog="main_pingpong",
        description="A market-making bot that places buy and sell orders around a CEX price feed.",
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False
    )

    parser.add_argument(
        "-h", "-help", "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="Show this help message and exit."
    )

    parser.add_argument("--run-tests", action="store_true",
                        help="Run the internal test suite for the PingPong strategy logic.")

    args = parser.parse_args()

    config_manager = ConfigManager(strategy="pingpong")
    config_manager.initialize(test_mode=args.run_tests)

    if args.run_tests:
        if config_manager.strategy_instance:
            tester = PingPongStrategyTester(config_manager.strategy_instance)
            asyncio.run(tester.run_all_tests())
        else:
            config_manager.general_log.error("Failed to initialize strategy instance for testing.")
        return

    # Get strategy-specific startup tasks
    startup_tasks = config_manager.strategy_instance.get_startup_tasks()

    # Run the main bot logic, which will create and manage its own event loop.
    run_async_main(config_manager, startup_tasks=startup_tasks)


if __name__ == '__main__':
    start()