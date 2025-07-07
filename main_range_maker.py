import argparse
import io
import json

from definitions.config_manager import ConfigManager
from definitions.starter import run_async_main


def start():
    """Parse CLI args and initialize RangeMaker strategy"""
    parser = argparse.ArgumentParser(
        prog="main_range_maker",
        description="Autonomous range-based market maker with concentrated liquidity bands",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument("--pairs", type=json.loads, required=True,
                        help='JSON array of pair configs. Example: [{"pair":"LTC/DOGE","min_price":0.0001,"max_price":0.001,"grid_density":20}]')
    parser.add_argument("--curve-type", choices=['linear', 'exp_decay', 'sigmoid'], default='linear',
                        help='Capital allocation curve type')
    parser.add_argument("--backtest", action='store_true',
                        help='Run in backtest mode with historical data')
    parser.add_argument("--data",
                        help="Path to historical data CSV file (optional, auto-generates from pair if not specified)")
    parser.add_argument("--download", action='store_true',
                        help="Download sample historical data from Yahoo Finance (uses base token from first pair)")

    args = parser.parse_args()

    config_manager = ConfigManager(strategy="range_maker")
    config_manager.initialize(
        pairs=args.pairs,
        curve_type=args.curve_type,
        loadxbridgeconf=not args.backtest
    )

    if args.backtest:
        import asyncio
        from test_units.test_range_maker_strategy import RangeMakerBacktester

        # Auto-generate data path if not specified
        pair_symbol = args.pairs[0]['pair'].replace('/', '_')
        data_path = args.data or f"{pair_symbol}_historical_data.csv"

        backtester = RangeMakerBacktester(
            config_manager.strategy_instance,
            data_path=data_path
        )

        asyncio.run(backtester.execute_fullbacktest())
    else:
        startup_tasks = config_manager.strategy_instance.get_startup_tasks()
        run_async_main(config_manager, startup_tasks=startup_tasks)



if __name__ == '__main__':
    start()
