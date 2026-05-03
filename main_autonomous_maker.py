import argparse

from definitions.cli_runner import add_custom_help, run_cli
from definitions.config_manager import ConfigManager
from definitions.run import run_async_main


def start():
    parser = argparse.ArgumentParser(
        prog="main_autonomous_maker",
        description="An autonomous market-making bot for XBridge DEX that operates without external price feeds.",
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False,
    )

    add_custom_help(parser)

    parser.parse_args()

    config_manager = ConfigManager(strategy="autonomous_maker")
    config_manager.initialize()

    startup_tasks = config_manager.strategy_instance.get_startup_tasks()

    run_async_main(config_manager, startup_tasks=startup_tasks)


if __name__ == "__main__":
    run_cli(start)
