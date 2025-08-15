import argparse

from definitions.config_manager import ConfigManager
from definitions.starter import run_async_main  # Import run_async_main
from definitions.cli_runner import run_cli


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

    parser.parse_args()

    config_manager = ConfigManager(strategy="pingpong")
    config_manager.initialize()

    # Get strategy-specific startup tasks
    startup_tasks = config_manager.strategy_instance.get_startup_tasks()

    # Run the main bot logic, which will create and manage its own event loop.
    run_async_main(config_manager, startup_tasks=startup_tasks)


if __name__ == '__main__':
    run_cli(start)
