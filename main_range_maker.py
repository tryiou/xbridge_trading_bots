"""
main_range_maker.py

This script serves as the command-line interface for initializing and running the
RangeMaker trading strategy. It parses arguments for pair configurations,
backtesting, and animation, then sets up the strategy and executes it either
in live mode or backtesting mode.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from definitions.cli_runner import run_cli
from definitions.config_manager import ConfigManager
from definitions.logger import setup_logging
from definitions.starter import run_async_main

if TYPE_CHECKING:
    from backtesting.backtest_range_maker_strategy import RangeMakerBacktester


def _setup_argument_parser() -> argparse.ArgumentParser:
    """
    Sets up and returns the argument parser for the RangeMaker strategy.

    Returns:
        argparse.ArgumentParser: The configured argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="main_range_maker",
        description="Autonomous range-based market maker with concentrated liquidity bands",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument("--pairs", type=json.loads, required=True,
                        help=(
                            'JSON array of pair configurations. All configurations MUST include the required core parameters.\n\n'
                            'REQUIRED PARAMETERS FOR EACH PAIR:\n'
                            '  "pair": Trading pair symbol in "BASE/QUOTE" format (e.g., "LTC/DOGE")\n'
                            '  "min_price": Minimum price boundary for liquidity range (float)\n'
                            '  "max_price": Maximum price boundary for liquidity range (float)\n'
                            '  "grid_density": Number of orders to place within price range (int)\n\n'

                            'ADVANCED OPTIONAL PARAMETERS:\n'
                            '  "initial_balances": Initial token balances in dict format (e.g., {"LTC":10, "DOGE":5000})\n'
                            '  "initial_middle_price": Starting mid-price for grid (default: (min_price + max_price)/2, or first historical price in backtest mode)\n'
                            '  "percent_min_size": Minimum order size as percentage of balance (float, default: 0.0001)\n\n'

                            'CONCENTRATION CONTROLS (Non-Linear Order Patterns):\n\n'

                            '[CURVE - Capital Allocation Method]\n'
                            '  Controls how trading capital is distributed between orders:\n\n'
                            '  Options (default="linear"):\n'
                            '    • linear:      Equal funds at each price point\n'
                            '    • exp_decay:   More funds near mid-price (exponential decay)\n'
                            '    • sigmoid:     Heavy concentration at mid-price (S-curve)\n'
                            '    • constant_product: Constant product (x*y=k) similar to Uniswap V2\n\n'
                            '  Use "curve_strength" (float) to control intensity:\n'
                            '    • Higher values = sharper concentration\n'
                            '    • Typical range: 5-50 (default=10)\n\n'

                            '[PRICE_STEPS - Price Distribution]\n'
                            '  Controls where orders are placed along price axis:\n\n'
                            '  Options (default="linear"):\n'
                            '    • linear:      Uniformly spaced prices (equal steps)\n'
                            '    • sigmoid:     Orders clustered near mid-price (S-curve distribution)\n'
                            '    • exponential: Tight grouping near mid-price (aggressive clustering)\n'
                            '    • power:       Smooth curve with adjustable concentration\n\n'
                            '  "curve_strength" also adjusts concentration for these methods\n\n'
                            '  RECOMMENDED COMBINATIONS:\n'
                            '    • Balanced:   linear price_steps + sigmoid curve\n'
                            '    • Aggressive: sigmoid price_steps + exp_decay curve\n\n'

                            'FULL EXAMPLE CONFIG:\n'
                            "  [{\n"
                            '    "pair": "LTC/DOGE",\n'
                            '    "min_price": 100,\n'
                            '    "max_price": 1000,\n'
                            '    "grid_density": 40,\n'
                            '    "percent_min_size": 0.001,\n'
                            '    "initial_balances": {"LTC": 10, "DOGE": 5000},\n'
                            '    "initial_middle_price": 500,\n'
                            '    "curve": "sigmoid",\n'
                            '    "curve_strength": 25,\n'
                            '    "price_steps": "exponential"\n'
                            "  }]"))
    parser.add_argument("--backtest", action='store_true',
                        help='Run in backtesting mode (ignores live exchanges, requires initial_balances)')
    parser.add_argument("--animate-graph", action='store_true',
                        help='Generate animated order book visualization when backtesting (requires matplotlib/ffmpeg)')
    return parser


def _log_parsed_arguments(logger: logging.Logger, args: argparse.Namespace) -> None:
    """
    Logs a summary of the parsed command-line arguments.

    Args:
        logger (logging.Logger): The logger instance.
        args (argparse.Namespace): The parsed arguments.
    """
    param_report = "Starting Range Maker with parameters:\n"
    param_report += f"  Pairs: {len(args.pairs)} configuration(s)\n"
    for i, pair_cfg in enumerate(args.pairs, 1):
        midpoint = pair_cfg.get('initial_middle_price', (pair_cfg['min_price'] + pair_cfg['max_price']) / 2)
        param_report += (
            f"    Pair #{i}: {pair_cfg['pair']} | "
            f"Price range: [{pair_cfg['min_price']:.2f} - {pair_cfg['max_price']:.2f}] | "
            f"Grid density: {pair_cfg['grid_density']} orders | "
            f"Min size: {pair_cfg.get('percent_min_size', 0.0001) * 100:.6f}% | "
            f"Initial midpoint: {midpoint:.2f}\n"
        )
    param_report += f"  Backtest mode: {args.backtest}\n"
    param_report += f"  Animated graph: {args.animate_graph}"
    logger.info(param_report)


def _log_strategy_configuration(logger: logging.Logger, pair_configs: List[Dict[str, Any]]) -> None:
    """
    Logs the detailed configuration for each trading pair.

    Args:
        logger (logging.Logger): The logger instance.
        pair_configs (List[Dict[str, Any]]): List of pair configuration dictionaries.
    """
    logger.info("Starting RangeMaker strategy with the following pair configurations:")
    for pair_cfg in pair_configs:
        midpoint_info = (f"initial_middle_price: {pair_cfg['initial_middle_price']:.6f}"
                         if 'initial_middle_price' in pair_cfg
                         else "(computed as average of min and max)")
        balances_info = (f", initial_balances: {pair_cfg['initial_balances']}"
                         if 'initial_balances' in pair_cfg
                         else "")

        logger.info(
            f"  Pair: {pair_cfg['pair']} | Price range: [{pair_cfg['min_price']:.2f}-{pair_cfg['max_price']:.2f}] | "
            f"Grid: {pair_cfg['grid_density']} orders | Curve: {pair_cfg.get('curve', 'linear')} (strength={pair_cfg.get('curve_strength', 10)}) | "
            f"Price steps: {pair_cfg.get('price_steps', 'linear')} | "
            f"Min size: {pair_cfg.get('percent_min_size', 0.0001) * 100:.4f}% | {midpoint_info}{balances_info}"
        )


def _handle_backtest_animation(
    logger: logging.Logger,
    args: argparse.Namespace,
    backtester: RangeMakerBacktester,
    pair_cfg: Dict[str, Any]
) -> None:
    """
    Handles the generation and saving of the backtest animation.

    Args:
        logger (logging.Logger): The logger instance.
        args (argparse.Namespace): Parsed command-line arguments.
        backtester (RangeMakerBacktester): The backtesting instance.
        pair_cfg (Dict[str, Any]): The configuration for the primary trading pair.
    """
    if args.animate_graph and backtester.animation_data:
        # Generate a unique filename for the animation based on strategy parameters
        pair_symbol = pair_cfg['pair'].replace('/', '_')
        min_p = str(pair_cfg['min_price']).replace('.', '_')
        max_p = str(pair_cfg['max_price']).replace('.', '_')
        grid_d = pair_cfg['grid_density']
        curve_type = pair_cfg.get('curve', 'linear')
        curve_s = pair_cfg.get('curve_strength', 10)
        percent_min_s = pair_cfg.get('percent_min_size', 0.0001)

        animation_filename = (
            f"animation_{pair_symbol}_min{min_p}_max{max_p}_grid{grid_d}_"
            f"curve{curve_type}_strength{curve_s}_min_size{str(percent_min_s).replace('.', '_')}.mp4"
        )

        script_dir = Path(__file__).parent
        save_path = script_dir / animation_filename

        backtester.plot_animated_order_book(save_path=str(save_path))

        if save_path.exists():
            logger.info(f"Confirmed: Animation file created at {save_path}")
        else:
            logger.error(f"Error: Animation file was NOT created at {save_path}")
            logger.warning(
                "Please ensure you have 'ImageMagick' installed for GIF support or 'ffmpeg' for MP4 support.")
            logger.warning(
                "For Debian/Ubuntu: `sudo apt-get install imagemagick` or `sudo apt-get install ffmpeg`")
            logger.warning(
                "For macOS (with Homebrew): `brew install imagemagick` or `brew install ffmpeg`")
            logger.warning(
                "For Windows (with Chocolatey): `choco install imagemagick` or `choco install ffmpeg`")


def start() -> None:
    """
    Parses CLI arguments, initializes the RangeMaker strategy, and runs it
    either in live trading or backtesting mode.
    """
    logger = setup_logging(name="main_range_maker", level=logging.DEBUG, console=True)

    parser = _setup_argument_parser()
    args = parser.parse_args()

    _log_parsed_arguments(logger, args)

    if args.backtest:
        logger.info("Running in BACKTEST mode")
        logger.info("Detailed strategy configuration will be logged after backtester sets initial price.")
        if args.animate_graph:
            logger.info("Animated graph enabled")
    else:
        logger.info("Running in LIVE mode")
        _log_strategy_configuration(logger, args.pairs)

    config_manager = ConfigManager(strategy="range_maker")
    # Determine whether to load XBridge configuration based on backtest mode
    config_manager.initialize(loadxbridgeconf=not args.backtest)

    # Prepare pair configurations for strategy initialization
    processed_pair_configs = []
    for pair_cfg in args.pairs:
        current_pair_cfg = pair_cfg.copy()
        if args.backtest:
            # For backtesting, initial_middle_price will be determined from historical data.
            # Remove it from the config passed to the strategy, so the backtester can override it later.
            if 'initial_middle_price' in current_pair_cfg:
                logger.info(
                    f"Backtesting mode: 'initial_middle_price' ({current_pair_cfg['initial_middle_price']:.6f}) "
                    f"for pair {current_pair_cfg['pair']} will be ignored and set automatically from historical data."
                )
                del current_pair_cfg['initial_middle_price']
        processed_pair_configs.append(current_pair_cfg)

    # Manually initialize strategy specifics for each pair with the processed configurations
    for pair_cfg in processed_pair_configs:
        config_manager.strategy_instance.initialize_strategy_specifics(**pair_cfg)

    if args.backtest:
        from backtesting.backtest_range_maker_strategy import RangeMakerBacktester

        # Assume the first pair in the list is the primary one for backtesting context
        # (This is a simplification; a multi-pair backtest would need a more complex approach)
        primary_pair_cfg = args.pairs[0]
        initial_balances = primary_pair_cfg.get('initial_balances')

        if not initial_balances:
            logger.error("Initial balances are required for backtesting. Please provide them in the --pairs argument.")
            return

        backtester = RangeMakerBacktester(config_manager.strategy_instance)
        backtester.logger.info(f"Initial balances: {initial_balances}")
        asyncio.run(
            backtester.execute_fullbacktest(initial_balances=initial_balances, animate_graph=args.animate_graph))

        _handle_backtest_animation(logger, args, backtester, primary_pair_cfg)

    else:
        startup_tasks = config_manager.strategy_instance.get_startup_tasks()
        run_async_main(config_manager, startup_tasks=startup_tasks)


if __name__ == '__main__':
    run_cli(start)
