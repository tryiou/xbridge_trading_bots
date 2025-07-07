import argparse
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
                        help='JSON array of pair configs. Example: [{"pair":"LTC/DOGE","min_price":400,"max_price":600,"grid_density":20}]')

    parser.add_argument("--backtest", action='store_true',
                        help='Run in backtest mode with historical data')
    parser.add_argument("--animate-graph", action='store_true',
                        help='Display an animated Matplotlib graph during backtesting')

    args = parser.parse_args()

    config_manager = ConfigManager(strategy="range_maker")

    config_manager.initialize(
        loadxbridgeconf=not args.backtest
    )

    # Manually initialize strategy specifics for each pair
    for pair_cfg in args.pairs:
        config_manager.strategy_instance.initialize_strategy_specifics(**pair_cfg)

    if args.backtest:
        import asyncio
        from test_units.test_range_maker_strategy import RangeMakerBacktester

        pair_symbol = args.pairs[0]['pair'].replace('/', '_')
        period = "1y"
        interval = "1h"

        backtester = RangeMakerBacktester(config_manager.strategy_instance)
        initial_balances = args.pairs[0].get('initial_balances', {})
        print(f"Initial balances: {initial_balances}")
        asyncio.run(
            backtester.execute_fullbacktest(initial_balances=initial_balances, animate_graph=args.animate_graph))

        if args.animate_graph and backtester.animation_data:
            # Generate a unique filename for the animation based on strategy parameters
            pair_cfg = args.pairs[0]
            pair_symbol = pair_cfg['pair'].replace('/', '_')
            min_p = str(pair_cfg['min_price']).replace('.', '_')
            max_p = str(pair_cfg['max_price']).replace('.', '_')
            grid_d = pair_cfg['grid_density']
            curve_type = pair_cfg.get('curve', 'linear')
            curve_s = pair_cfg.get('curve_strength', 10)

            animation_filename = f"animation_{pair_symbol}_min{min_p}_max{max_p}_grid{grid_d}_curve{curve_type}_strength{curve_s}.mp4"

            # Save the animation in the current script's directory
            from pathlib import Path
            script_dir = Path(__file__).parent
            save_path = script_dir / animation_filename

            backtester.plot_animated_order_book(save_path=str(save_path))

            if save_path.exists():
                backtester.logger.info(f"Confirmed: Animation file created at {save_path}")
            else:
                backtester.logger.error(f"Error: Animation file was NOT created at {save_path}")
                backtester.logger.warning(
                    "Please ensure you have 'ImageMagick' installed for GIF support or 'ffmpeg' for MP4 support.")
                backtester.logger.warning(
                    "For Debian/Ubuntu: `sudo apt-get install imagemagick` or `sudo apt-get install ffmpeg`")
                backtester.logger.warning(
                    "For macOS (with Homebrew): `brew install imagemagick` or `brew install ffmpeg`")
                backtester.logger.warning(
                    "For Windows (with Chocolatey): `choco install imagemagick` or `choco install ffmpeg`")
    else:
        startup_tasks = config_manager.strategy_instance.get_startup_tasks()
        run_async_main(config_manager, startup_tasks=startup_tasks)


if __name__ == '__main__':
    start()
