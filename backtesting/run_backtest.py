"""Run backtest for autonomous maker strategy from CLI arguments."""

import argparse
import logging
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ruamel.yaml import YAML

from definitions.logger import set_backtest_mode
from backtesting.engine import BacktestEngine
from backtesting.reporter import Reporter


def configure_backtest_logging(log_file: str):
    """Configure root logger to write all output to single backtest file."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    handler = logging.FileHandler(log_file, mode="w")
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(name)-20s] %(levelname)-8s - %(message)s")
    )
    root.addHandler(handler)
    root.propagate = False


def load_config_from_yaml(config_path: str):
    """Load configuration from YAML file."""
    yaml = YAML()
    with open(config_path) as f:
        config_data = yaml.load(f)

    pair_configs = config_data.get("pair_configs", [])

    enabled = [pc for pc in pair_configs if pc.get("enabled", False)]
    if not enabled:
        raise ValueError("No enabled pair configs found")
    return enabled[0]


def run_backtest(
    config_path: str,
    start_date: str,
    end_date: str,
    interval: str,
    output_name: str,
):
    """Run backtest with given parameters."""
    set_backtest_mode(True)

    pair_cfg = load_config_from_yaml(config_path)

    pair = pair_cfg["pair"]
    base, quote = pair.split("/")

    backtest_name = output_name

    engine = BacktestEngine(
        pair=pair,
        start_date=start_date,
        end_date=end_date,
        interval=interval,
        pair_config=pair_cfg,
        backtest_name=backtest_name,
    )

    log_file = os.path.join(engine.output_dir, "backtest.log")
    configure_backtest_logging(log_file)

    logger = logging.getLogger(__name__)
    logger.info("Running backtest...")
    logger.info("Config: %s", config_path)
    logger.info("Pair: %s", pair)
    logger.info("Period: %s to %s", start_date, end_date)
    logger.info("Interval: %s", interval)
    logger.info(
        "Initial: %s %s, %s %s",
        pair_cfg.get("initial_balance_a"),
        base,
        pair_cfg.get("initial_balance_b"),
        quote,
    )
    logger.info("Mid-Price: %s", pair_cfg.get("initial_mid_price"))
    logger.info(
        "Sizing: %s, Spread: %s",
        pair_cfg.get("order_sizing_mode"),
        pair_cfg.get("spread_mode"),
    )

    results = engine.run()

    reporter = Reporter()
    report = reporter.generate_report(results)
    logger.info("\n%s", report)

    if results.results["trades"]:
        logger.info("Total trades: %d", len(results.results["trades"]))
        for trade in results.results["trades"]:
            logger.debug(
                "Trade: %s | %s @ %s",
                trade.side,
                trade.amount,
                trade.price,
            )

    print(f"BACKTEST_OUTPUT_DIR:{engine.output_dir}")
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Run backtest for autonomous maker strategy"
    )
    parser.add_argument("config", help="Path to autonomous maker config YAML file")
    parser.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("interval", help="Price interval (1m, 5m, 15m, 1h, 4h, 1d)")
    parser.add_argument("output_name", help="Output name suffix (e.g., 3month_equal)")

    args = parser.parse_args()

    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("aiodns").setLevel(logging.ERROR)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.ERROR)
    logging.getLogger("c-ares").setLevel(logging.ERROR)

    warnings.filterwarnings("ignore", message=".*DNS resolver.*inotify.*")
    warnings.filterwarnings("ignore", category=FutureWarning)

    run_backtest(
        args.config, args.start_date, args.end_date, args.interval, args.output_name
    )


if __name__ == "__main__":
    main()
