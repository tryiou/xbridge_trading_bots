import asyncio
import copy
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import matplotlib

matplotlib.use('Agg')  # Use non-interactive backend for animation saving
import matplotlib.animation as animation
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import matplotlib.ticker as mticker

from definitions.logger import setup_logging

timeframe = '1d'
period = "3mo"

# Define a standard formatter for consistent log output
STANDARD_FORMATTER = logging.Formatter(
    fmt='[%(asctime)s] [%(name)-20s] %(levelname)-8s - %(message)s',
    datefmt='%H:%M:%S'
)


class RangeMakerBacktester:
    """Backtesting engine for RangeMaker strategy simulations"""

    def __init__(self, strategy_instance):
        self.strategy = strategy_instance
        self.logger = setup_logging(name="range_maker_backtester", level=logging.DEBUG, console=True)
        # Apply the standard formatter to the backtester's logger
        for handler in self.logger.handlers:
            handler.setFormatter(STANDARD_FORMATTER)
        self.historical_data = None
        self.simulation_results = []
        self.metrics = {}
        self.logger.info("TRADEMAKER: Preparing backtest environment using %s strategy",
                         strategy_instance.__class__.__name__)
        # Dynamically construct data_file_path using the script's directory and global variables
        self.data_file_path = Path(
            __file__).parent / f"{self.get_pair_symbol()}_historical_data_{period}_{timeframe}.csv"

        # Explicitly set strategy's logger to DEBUG for comprehensive backtest logging
        self.strategy.logger.setLevel(logging.DEBUG)
        # Explicitly set strategy's logger to DEBUG for comprehensive backtest logging
        # for handler in self.strategy.logger.handlers:
        #     handler.setFormatter(STANDARD_FORMATTER)
        self.logger.info("RangeMakerBacktester initialized.")
        # Simulation state
        self.current_balances = {}
        self.order_history = []
        self.price_history = []
        self.inventory_history = []
        self.fee_history = []
        self.initial_balances = {}
        self.final_balances = {}
        self.initial_price = 0
        self.final_price = 0
        self.animation_data = []  # Store data for animation frames
        self.impermanent_loss_history = []  # New: Store Impermanent Loss over time
        self.initial_hold_base_amount = 0.0  # New: For IL calculation
        self.initial_hold_quote_amount = 0.0  # New: For IL calculation
        self._order_stem_lines = []  # Stores references to dynamic lines (for blitting or clearing)
        self._order_value_texts = [] # Stores references to dynamic text labels for order values
        self.logger.debug(f"Backtester initialized. Data will be loaded/saved from: {self.data_file_path}")

    def get_pair_symbol(self) -> str:
        """Helper to get the pair symbol from active positions, or a default."""
        if self.strategy.active_positions:
            return next(iter(self.strategy.active_positions.keys())).replace('/', '_')
        return "UNKNOWN_PAIR"

    async def load_historical_data(self):
        """
        Load historical price data from CSV file and prepare it for backtesting.
        """
        self.logger.debug(f"Loading historical data from: {self.data_file_path}")
        file_path = self.data_file_path
        if not file_path.exists():
            self.logger.error("FAIL: Historical data file not found at %s", file_path.absolute())
            self.logger.info("INFO: Create this file manually - or program will attempt download if implemented")
            raise FileNotFoundError(f"Historical data file not found at {file_path.absolute()}")

        try:
            raw_data = pd.read_csv(file_path, parse_dates=['date'])
            raw_data.rename(columns={'date': 'timestamp'}, inplace=True)
            self.logger.debug(f"Raw data loaded ({len(raw_data)} rows). Columns: {raw_data.columns.tolist()}")

            if 'close' not in raw_data.columns:
                self.logger.error("Data file must include 'close' column for pair price")
                raise ValueError("Data file missing 'close' column")

            self.historical_data = raw_data.set_index('timestamp').resample(timeframe).ffill()
            self.historical_data.ffill(inplace=True)
            self.historical_data.bfill(inplace=True)
            self.historical_data.reset_index(inplace=True)
            self.logger.info(
                f"Successfully loaded and processed {len(self.historical_data)} historical data points with close prices.")

        except Exception as e:
            self.logger.error(f"Error loading or processing historical data: {e}", exc_info=True)
            raise

    async def download_sample_data(self, file_path: str, pair: str, interval: str):
        """Download sample historical price data using yfinance and ensure date alignment, including all OHLCV fields"""
        self.logger.info(
            f"Attempting to download sample data for {pair} with period={period}, interval={interval} to {file_path}")
        try:
            import yfinance as yf
            self.logger.debug("yfinance package imported successfully.")
        except ImportError:
            self.logger.error("yfinance package required for downloading historical data")
            self.logger.error("Install with: pip install yfinance")
            raise

        if "/" in pair:
            base_token, quote_token = pair.split("/")
            base_sym, quote_sym = f"{base_token}-USD", f"{quote_token}-USD"
            self.logger.debug(f"Parsed pair: Base={base_token} ({base_sym}), Quote={quote_token} ({quote_sym})")
        else:
            self.logger.error(f"Invalid pair format: {pair}. Must be in BASE/QUOTE format")
            raise ValueError(f"Invalid pair format: {pair}. Must be in BASE/QUOTE format")

        try:
            self.logger.info(f"Downloading historical data for {base_sym} and {quote_sym}...")
            base_data = yf.download(tickers=base_sym, period=period, interval=interval, progress=False)
            quote_data = yf.download(tickers=quote_sym, period=period, interval=interval, progress=False)
            self.logger.info("Historical data download complete.")

            # Create aligned date range covering both series
            start_date = min(base_data.index.min(), quote_data.index.min())
            end_date = max(base_data.index.max(), quote_data.index.max())
            date_range = pd.date_range(start_date, end_date)

            # Reindex base_data and quote_data to the full date_range with forward/back fill
            base_data = base_data.reindex(date_range)
            base_data[['Open', 'High', 'Low', 'Close', 'Volume']] = base_data[
                ['Open', 'High', 'Low', 'Close', 'Volume']].ffill().bfill()

            quote_data = quote_data.reindex(date_range)
            quote_data[['Open', 'High', 'Low', 'Close', 'Volume']] = quote_data[
                ['Open', 'High', 'Low', 'Close', 'Volume']].ffill().bfill()

            self.logger.debug("Computing synthetic pair OHLCV data")
            # Extract necessary columns as Series
            base_open = base_data['Open'].squeeze()
            base_high = base_data['High'].squeeze()
            base_low = base_data['Low'].squeeze()
            base_close = base_data['Close'].squeeze()
            quote_open = quote_data['Open'].squeeze()
            quote_high = quote_data['High'].squeeze()
            quote_low = quote_data['Low'].squeeze()
            quote_close = quote_data['Close'].squeeze()

            # Compute pair prices
            df = pd.DataFrame(index=date_range)
            df['open'] = base_open / quote_open
            df['high'] = base_high / quote_low  # High when base high and quote low
            df['low'] = base_low / quote_high  # Low when base low and quote high
            df['close'] = base_close / quote_close
            df['volume'] = 0  # No meaningful volume for synthetic pairs

            self.logger.debug("Synthetic pair DataFrame created. Shape: %s", df.shape)
            # Reset index so we have a date column
            df.reset_index(inplace=True)
            df.rename(columns={'index': 'date'}, inplace=True)
            # Save as CSV with date and OHLCV columns
            df.to_csv(file_path, index=False)
            self.logger.info(f"Successfully downloaded and saved synthetic pair data to {file_path}")
            return

        except Exception as e:
            self.logger.error(f"Failed to download data using yfinance: {str(e)}", exc_info=True)
            self.logger.error("Please check the token symbols and your internet connection")
            raise ValueError("Historical data download failed")

    def _setup_mock_pairs(self):
        """Create mock Pair objects for backtesting"""
        self.logger.info("Setting up mock Pair objects for backtesting.")
        from definitions.pair import Pair
        from definitions.token import Token

        if not self.strategy.active_positions:
            self.logger.warning("No active positions found in strategy. Skipping mock pair setup.")
            return

        for pair_name, position in self.strategy.active_positions.items():
            self.logger.debug(f"Setting up mock pair for {pair_name}")
            t1_sym, t2_sym = pair_name.split('/')
            token1 = Token(t1_sym, self.strategy.config_manager)
            token2 = Token(t2_sym, self.strategy.config_manager)
            token1.dex = lambda: None
            token2.dex = lambda: None
            setattr(token1.dex, 'free_balance', self.current_balances.get(t1_sym, 0))
            setattr(token2.dex, 'free_balance', self.current_balances.get(t2_sym, 0))
            pair_cfg = {
                'name': pair_name,
                'min_price': position.min_price,
                'max_price': position.max_price,
                'grid_density': position.grid_density
            }
            self.logger.debug(
                f"Mock pair {pair_name} setup: Balances {t1_sym}={token1.dex.free_balance}, {t2_sym}={token2.dex.free_balance}. Config: {pair_cfg}")

            self.strategy.pairs[pair_name] = Pair(
                token1=token1,
                token2=token2,
                cfg=pair_cfg,
                strategy="range_maker",
                config_manager=self.strategy.config_manager
            )
        self.logger.info("Mock Pair objects setup complete.")

    async def execute_fullbacktest(self, initial_balances: Dict[str, float], period: str = "1y", interval: str = "1h",
                                   animate_graph: bool = False):
        """
        Run full historical simulation with initial token balances.
        Updates strategy state through simulated time periods.

        Note: The 'period' and 'interval' arguments to this method are currently
        overridden by the global 'period' and 'timeframe' variables defined
        at the top of this backtesting script (e.g., "3mo" and "1d").
        """
        self.logger.info("Starting full backtest execution.")
        self.logger.debug(f"Backtest parameters: period={period}, interval={interval}")

        try:
            # Auto-download data if missing
            if not self.data_file_path.exists():
                if not self.strategy.active_positions:
                    self.logger.error("Strategy active_positions is empty. Cannot download sample data.")
                    raise ValueError("Strategy not initialized with active positions for data download.")
                pair = next(iter(self.strategy.active_positions.values())).token_pair
                self.logger.warning(
                    f"Data file {self.data_file_path} not found - downloading sample data for {pair} with period={period}, interval={timeframe}")
                await self.download_sample_data(self.data_file_path, pair,
                                                interval=timeframe)  # Use global timeframe here
            else:
                self.logger.info(f"Data file {self.data_file_path} found, skipping download.")

            self.logger.info(f"Loading historical data from file: {self.data_file_path}")
            await self.load_historical_data()

            if self.historical_data is None or self.historical_data.empty:
                self.logger.error("No historical data loaded or data is empty - aborting backtest.")
                raise ValueError("Historical data not loaded successfully or is empty.")
            self.logger.info(f"Historical data loaded successfully with {len(self.historical_data)} records.")
            self.logger.info(f"Backtesting with {len(self.historical_data)} data points.")
            min_price = self.historical_data['close'].min()
            max_price = self.historical_data['close'].max()
            self.logger.info(f"Historical price range: {min_price:.6f} to {max_price:.6f} "
                             f"({max_price - min_price:.6f} spread)")

            # Store initial price
            self.initial_price = self.historical_data['close'].iloc[0]
            self.logger.info(f"Initial price: {self.initial_price:.4f}")

            self.initial_balances = (initial_balances or {}).copy()
            self.current_balances = self.initial_balances.copy()

            # For Impermanent Loss calculation
            if self.strategy.active_positions:
                pair_name = next(iter(self.strategy.active_positions.keys()))
                base_token, quote_token = pair_name.split('/')
                self.initial_hold_base_amount = self.initial_balances.get(base_token, 0)
                self.initial_hold_quote_amount = self.initial_balances.get(quote_token, 0)
                self.logger.debug(
                    f"Initial hold amounts for IL: {base_token}={self.initial_hold_base_amount}, {quote_token}={self.initial_hold_quote_amount}")

            if not self.current_balances:
                self.logger.info("No initial balances provided - generating defaults.")
                if not self.strategy.active_positions:
                    self.logger.error("Cannot generate default balances: strategy active_positions is empty.")
                    raise ValueError("Cannot generate default balances without active positions.")
                tokens = list(self.strategy.active_positions.keys())[0].split('/')
                self.initial_balances = {token: 1000.0 for token in tokens}
                self.current_balances = self.initial_balances.copy()
                self.logger.info(f"Generated initial balances: {self.initial_balances}")
            else:
                self.logger.debug("Using provided initial balances.")

            self._setup_mock_pairs()
            if not self.strategy.pairs:
                self.logger.error("Mock pairs not set up. Aborting backtest.")
                raise ValueError("Mock pairs not set up.")
            # Initialize the order grid before starting the simulation
            if not self.strategy.active_positions:
                self.logger.error("Strategy active_positions is empty. Cannot initialize order grid.")
                raise ValueError("Strategy not initialized with active positions for order grid.")
            pair_name = next(iter(self.strategy.active_positions.keys()))
            pair_instance = self.strategy.pairs.get(pair_name)
            if not pair_instance:
                self.logger.error(f"Pair instance for {pair_name} not found in strategy.pairs. Aborting.")
                raise ValueError(f"Pair instance for {pair_name} not found.")
            position = self.strategy.active_positions.get(pair_name)
            if not position:
                self.logger.error(f"Position for {pair_name} not found in strategy.active_positions. Aborting.")
                raise ValueError(f"Position for {pair_name} not found.")

            # Set the initial_mid_price in the strategy's position based on the first historical price
            position.current_mid_price = self.initial_price
            self.logger.info(
                f"Backtest: Setting initial mid-price for {pair_name} to first historical close price: {self.initial_price:.6f}")
            self.strategy._log_position_initialization(position) # Log the effective initial mid-price for backtesting

            await self.strategy.initialize_order_grid(pair_instance, position)

            self.logger.info("Resetting simulation state.")
            self._reset_simulation_state()
            self.logger.debug("Simulation state reset.")

            # Record initial state (time zero) before simulation starts
            self.price_history.append(self.initial_price)
            initial_timestamp = self.historical_data['timestamp'].iloc[0]
            self._record_simulation_state(initial_timestamp) # This appends to inventory_history

            # Capture the very first frame for animation to show initial setup
            if animate_graph:
                pair_name = next(iter(self.strategy.active_positions.keys()))
                if pair_name in self.strategy.order_grids:
                    initial_frame_data_entry = {
                        'timestamp': initial_timestamp,
                        'current_price': self.initial_price, # Use the explicit initial price
                        'active_buy_orders': copy.deepcopy(self.strategy.order_grids[pair_name].get('buy', {})),
                        'active_sell_orders': copy.deepcopy(
                            self.strategy.order_grids[pair_name].get('sell', {})),
                        'balances': self.current_balances.copy(),
                        'min_price': position.min_price,
                        'max_price': position.max_price,
                        'position_config': copy.deepcopy(position),
                        'trade_message': "" # No trade message for the initial frame
                    }
                    self.animation_data.append(initial_frame_data_entry)
                    self.logger.debug("Added initial state as the first animation frame.")

            self.logger.info(f"Beginning simulation of {len(self.historical_data)} time periods.")
            self.logger.debug(f"Historical data head:\n{self.historical_data.head()}")

            try:
                self.logger.info(f"Starting simulation of {len(self.historical_data)} periods")
                for idx, row in self.historical_data.iterrows():
                    current_price = row['close']
                    timestamp = row['timestamp']
                    self.price_history.append(current_price)

                    # Log every 5 periods and first/last
                    # Log summary at start, end, and every 10 periods
                    # Log period summary at INFO level (first/last and every ~500 periods)
                    # This dramatically reduces log spam for routine steps
                    print_info_every = max(1, len(self.historical_data) // 500)
                    if idx == 0 or (idx + 1) % print_info_every == 0 or idx == len(self.historical_data) - 1:
                        self.logger.info(
                            f"Period {idx + 1}/{len(self.historical_data)}: Price={current_price:.6f} | Balances: {self.current_balances.get(base_token, 0):.6f} {base_token}, {self.current_balances.get(quote_token, 0):.6f} {quote_token}")

                    filled_orders = await self.simulate_time_step(row)
                    self._record_simulation_state(timestamp)

                    trade_message = ""  # Initialize trade message for the current frame
                    if filled_orders:
                        for order in filled_orders:
                            # _process_simulated_fill now returns a formatted trade message
                            fill_msg = self._process_simulated_fill(order, timestamp)
                            if fill_msg:
                                trade_message += f"{fill_msg}\n"
                        # Remove trailing newline if any
                        trade_message = trade_message.strip()

                        self.logger.info(
                            f"{timestamp.strftime('%Y-%m-%d')}: {len(filled_orders)} orders filled at {current_price:.6f}."
                        )
                        # Process strategy behavior after fills
                        await self.strategy.process_pair_async(pair_instance)

                    if animate_graph:
                        pair_name = next(iter(self.strategy.active_positions.keys()))
                        if pair_name in self.strategy.order_grids:
                            position = self.strategy.active_positions[pair_name]
                            frame_data_entry = { # Store the dict in a variable
                                'timestamp': timestamp,
                                'current_price': current_price,
                                'active_buy_orders': copy.deepcopy(self.strategy.order_grids[pair_name].get('buy', {})),
                                'active_sell_orders': copy.deepcopy(
                                    self.strategy.order_grids[pair_name].get('sell', {})),
                                'balances': self.current_balances.copy(),
                                'min_price': position.min_price,
                                'max_price': position.max_price,
                                'position_config': copy.deepcopy(position),  # Include the position object
                                'trade_message': trade_message  # Include trade message for this frame
                            }
                            self.animation_data.append(frame_data_entry) # Append the variable

            except Exception as e:
                self.logger.error(f"Error during simulation loop: {str(e)}", exc_info=True)
                raise

            # Store final price
            self.final_price = self.historical_data['close'].iloc[-1]
            self.logger.info(f"Final price: {self.final_price:.4f}")

            # Calculate final metrics
            self.final_balances = self.current_balances.copy()
            self.logger.info("Calculating final performance metrics.")
            self._calculate_performance_metrics()

            self._log_executed_trades()
            
            self._generate_analysis_report()

            self.logger.info("Historical simulation completed successfully.")


        except Exception as e:
            self.logger.critical(f"Critical error during backtest: {str(e)}", exc_info=True)
            self.logger.critical("Backtest aborted due to critical error.")
            return  # Exit gracefully instead of re-raising

    def _log_executed_trades(self):
        # Log all executed trades before generating report
        self.logger.info("Logging all executed trades:")
        self.logger.info("=" * 110)
        # Get token symbols from active positions or use defaults
        base_token = "BASE"
        quote_token = "QUOTE"
        if self.strategy.active_positions:
            pair_name = next(iter(self.strategy.active_positions.keys()))
            base_token, quote_token = pair_name.split('/')

        # Log header
        header = f"{'#':<4} {'Timestamp':<15}   {'Side':<6} {'Price':<14} {base_token + ' Amount':<20} {quote_token + ' Amount':<20}"
        self.logger.info(header)
        self.logger.info("-" * 110)

        for i, trade in enumerate(self.order_history):
            if trade['side'] == 'buy':
                base_amount = trade['taker_size']
                quote_amount = trade['maker_size']
            else:  # 'sell'
                base_amount = trade['maker_size']
                quote_amount = trade['taker_size']

            # Calculate trade value in quote currency
            trade_value = base_amount * trade['price'] if trade['side'] == 'buy' else quote_amount

            self.logger.info(
                f"{i + 1:<4} {trade['timestamp'].strftime('%Y-%m-%d'):<15}   {trade['side'].upper():<6} {trade['price']:14.8f} "
                f"{base_amount:20.8f} {quote_amount:20.8f}"
            )
        self.logger.info("=" * 110)

    async def simulate_time_step(self, row):
        """Simulate strategy behavior for a single time step using OHLC data"""
        timestamp = row['timestamp']
        high = row['high']
        low = row['low']
        open_price = row['open']
        close = row['close']

        pair_name = next(iter(self.strategy.active_positions.keys()))
        pair_instance = self.strategy.pairs[pair_name]

        # First, get filled orders based on OHLC movement
        # Use scalar values from current row
        low_val = low
        high_val = high
        active_orders = self.strategy.order_grids.get(pair_name, {}).get('active_orders', [])
        filled_orders = self._get_backtest_fills(active_orders, low_val, high_val)

        if filled_orders:
            for order in filled_orders:
                self._process_simulated_fill(order, timestamp)
                await self.strategy.process_pair_async(pair_instance)

        # This function only processes the fills, strategy behavior and regridding is handled by process_pair_async after all fills for a step are processed.

    def _update_mock_balances(self):
        """Update the mock free_balance on the pair's tokens."""
        if not self.strategy.active_positions:
            self.logger.warning("_update_mock_balances: No active positions in strategy.")
            return

        pair_name = next(iter(self.strategy.active_positions.keys()))
        pair_instance = self.strategy.pairs.get(pair_name)
        if not pair_instance:
            self.logger.warning(f"_update_mock_balances: Pair instance for {pair_name} not found.")
            return

        t1_sym, t2_sym = pair_instance.symbol.split('/')
        pair_instance.t1.dex.free_balance = self.current_balances.get(t1_sym, 0)
        pair_instance.t2.dex.free_balance = self.current_balances.get(t2_sym, 0)
        self.logger.debug(
            f"_update_mock_balances: {pair_instance.t1.symbol}={pair_instance.t1.dex.free_balance:.6f}, {pair_instance.t2.symbol}={pair_instance.t2.dex.free_balance:.6f}")

    def _process_simulated_fill(self, order: dict, timestamp: datetime) -> str:
        """
        Processes a simulated order fill and returns a formatted trade message.
        EXACT LIMIT PRICE EXECUTION as per strategy design.
        """
        trade_message = ""
        pair_name = next(iter(self.strategy.active_positions.keys())) # Assuming single pair for now
        base_token, quote_token = pair_name.split('/')

        # Check balance before filling and skip if insufficient
        maker_token = order['maker']
        maker_size = order['maker_size']
        if self.current_balances.get(maker_token, 0) < maker_size:
            self.logger.warning(
                f"SKIPPING {order['type']} order {order.get('id', '')} due to insufficient {maker_token} balance. "
                f"Needed: {maker_size:.6f}, Available: {self.current_balances.get(maker_token, 0):.6f}"
            )
            return "" # Return empty message if trade skipped

        # Update balances based on trade type
        if order['type'] == 'buy':
            # Buy order: Maker sells quote, Taker buys base
            # We are the maker, so we pay quote, receive base
            self.current_balances[order['taker']] += order['taker_size']  # Receive base token
            self.current_balances[order['maker']] -= order['maker_size']  # Pay quote token
            trade_info = f"BUY {order['taker_size']:.4f} {base_token} @ {order['price']:.4f} (Cost: {order['maker_size']:.4f} {quote_token})"

        elif order['type'] == 'sell':
            # Sell order: Maker sells base, Taker buys quote
            # We are the maker, so we sell base, receive quote
            self.current_balances[order['taker']] += order['taker_size']  # Receive quote token
            self.current_balances[order['maker']] -= order['maker_size']  # Pay base token
            trade_info = f"SELL {order['maker_size']:.4f} {base_token} @ {order['price']:.4f} (Recv: {order['taker_size']:.4f} {quote_token})"
        else:
            self.logger.error(f"Unknown order type encountered: {order['type']}")
            return ""

        self._update_mock_balances() # Update the mock token balances after a fill

        # Update strategy's current price to THIS ORDER'S LIMIT PRICE
        pair_key = next(iter(self.strategy.active_positions.keys()))
        position = self.strategy.active_positions.get(pair_key)
        if position:
            position.current_mid_price = order['price']

        # Record trade with LIMIT PRICE
        trade = {
            'timestamp': timestamp,
            'pair': pair_key,
            'side': order['type'],
            'price': order['price'],
            'limit_price': order['price'],
            'execution_price': order['price'],
            'maker_size': order['maker_size'],
            'taker_size': order['taker_size'],
            'order_id': order.get('id', 'simulated')
        }
        self.order_history.append(trade)
        order['status'] = 'filled'

        # Use full timestamp for trade logging to include date
        self.logger.info(f"  Trade: {timestamp.strftime('%Y-%m-%d')} | {trade_info}")
        trade_message = f"{timestamp.strftime('%Y-%m-%d')}\n{trade_info}"
        return trade_message  # Return the message

    def _reset_simulation_state(self):
        """Initialize fresh simulation tracking"""
        self.logger.info("Resetting simulation state variables.")
        self.order_history = []
        self.price_history = []
        self.inventory_history = []
        self.fee_history = []
        self.metrics = {}
        self.counter_pnl_history = []  # Initialize counter P&L history
        self.logger.debug("Simulation state variables cleared.")

    def _record_simulation_state(self, timestamp: datetime):
        """Capture snapshot of current state"""
        portfolio_value = self._calculate_portfolio_value()
        current_price = self.price_history[-1]
        buy_and_hold_value = (self.initial_hold_base_amount * current_price) + self.initial_hold_quote_amount
        self.inventory_history.append({
            'timestamp': timestamp,
            'balances': self.current_balances.copy(),
            'portfolio_value': portfolio_value,
            'buy_and_hold_value': buy_and_hold_value,  # Store for more detailed analysis
            'impermanent_loss': buy_and_hold_value - portfolio_value  # Calculate and store IL
        })
        self.impermanent_loss_history.append(buy_and_hold_value - portfolio_value)  # Append IL to history
        # Removed per-step debug logging for simulation state to reduce verbosity.

    def _calculate_portfolio_value(self) -> float:
        """Calculate portfolio value ONLY in quote token terms."""
        if not self.strategy.active_positions or not self.price_history:
            return sum(self.current_balances.values())

        pair_name = next(iter(self.strategy.active_positions.keys()))
        base_token, quote_token = pair_name.split('/')
        current_price = self.price_history[-1]

        # Convert all token balances to quote token value
        portfolio_value = 0
        portfolio_value += self.current_balances.get(base_token, 0) * current_price
        portfolio_value += self.current_balances.get(quote_token, 0)

        # Log detailed debug information only when needed
        debug = False
        if debug:
            self.logger.debug(f"Calculating portfolio value for {pair_name}")
            base_balance = self.current_balances.get(base_token, 0)
            quote_balance = self.current_balances.get(quote_token, 0)
            base_value = base_balance * current_price
            self.logger.debug(
                f" - Base ({base_token}): {base_balance:.6f} * {current_price:.6f} = {base_value:.6f} {quote_token}")
            self.logger.debug(f" - Quote ({quote_token}): {quote_balance:.6f} {quote_token}")
            # Log balance changes as percentages
            base_pct = (base_balance - self.initial_balances.get(base_token, 0)) / self.initial_balances.get(base_token,
                                                                                                             1) * 100
            quote_pct = (quote_balance - self.initial_balances.get(quote_token, 0)) / self.initial_balances.get(
                quote_token, 1) * 100
            self.logger.debug(f"   {base_token} change: {base_pct:.2f}% | {quote_token} change: {quote_pct:.2f}%")
            self.logger.debug(f"Total portfolio value: {portfolio_value:.6f} {quote_token}")

        return portfolio_value

    def _get_backtest_fills(self, active_orders: List[Dict[str, Any]], low: float, high: float) -> List[Dict[str, Any]]:
        """
        Get filled orders in backtest mode using price range simulation.
        Orders are filled if price moves within their price range during the period.
        
        Args:
            active_orders: List of active orders
            low: Lowest price of the period
            high: Highest price of the period
            
        Returns:
            List of orders that would be filled during the period
        """
        filled = []
        for order in active_orders:
            if order['type'] == 'buy' and low <= order['price']:
                filled.append(order)
            elif order['type'] == 'sell' and high >= order['price']:
                filled.append(order)
        return filled

    def _get_current_price_for_asset(self, base: str, quote: str) -> float:
        """Get current price of base/quote pair"""
        if base == quote:
            self.logger.debug(f"Base and quote tokens are the same ({base}), returning 1.0.")
            return 1.0
        # For simplicity, assume direct conversion
        pair_name = next(iter(self.strategy.active_positions))
        if base + '/' + quote == pair_name:
            price = self.price_history[-1]
            self.logger.debug(f"Returning current price for {pair_name}: {price:.6f}")
            return price
        else:
            price = 1 / self.price_history[-1]
            self.logger.debug(f"Returning inverse current price for {pair_name}: {price:.6f}")
            return price

    def _calculate_performance_metrics(self):
        """Calculate key performance indicators"""
        self.logger.info("Calculating performance metrics.")
        if not self.inventory_history:
            self.logger.warning("No inventory history to calculate metrics from.")
            return

        # Get token symbols
        if not self.strategy.active_positions:
            self.logger.error("Cannot calculate metrics: Strategy has no active positions.")
            return

        pair_name = next(iter(self.strategy.active_positions.keys()))
        base_token, quote_token = pair_name.split('/')
        self.logger.debug(f"Calculating metrics for pair: {base_token}/{quote_token}")

        # Calculate token-based metrics
        initial_base = self.initial_balances.get(base_token, 0)
        initial_quote = self.initial_balances.get(quote_token, 0)

        self.logger.info(f"Initial balances: {base_token}={initial_base:.6f}, {quote_token}={initial_quote:.6f}")
        self.logger.info(f"Initial price: {self.initial_price:.6f}")

        final_base = self.final_balances.get(base_token, 0)
        final_quote = self.final_balances.get(quote_token, 0)

        price_return_base = ((
                                     self.final_price - self.initial_price) / self.initial_price * 100) if self.initial_price != 0 else 0

        # Get actual (high-precision) initial and final portfolio values from history
        initial_portfolio_value_exact = self.inventory_history[0]['portfolio_value']
        final_portfolio_value_exact = self.inventory_history[-1]['portfolio_value']

        # Calculate profit and return using exact values
        profit = final_portfolio_value_exact - initial_portfolio_value_exact
        return_pct = (profit / initial_portfolio_value_exact * 100) if initial_portfolio_value_exact != 0 else 0

        # Calculate value change percentages
        base_change_pct = ((final_base - initial_base) / initial_base * 100) if initial_base != 0 else 0
        quote_change_pct = ((final_quote - initial_quote) / initial_quote * 100) if initial_quote != 0 else 0

        # Number of trades
        num_trades = len(self.order_history)

        # Impermanent Loss Metrics
        total_impermanent_loss = 0.0
        max_impermanent_loss = 0.0 # Will store the max positive value (worst case for strategy)
        avg_impermanent_loss = 0.0
        if self.impermanent_loss_history:
            il_series = pd.Series(self.impermanent_loss_history)
            total_impermanent_loss = il_series.iloc[-1]
            # Max impermanent loss is the highest positive value (worst for the strategy, as IL reduces value)
            # Or the largest absolute value if it goes both positive and negative
            max_impermanent_loss = il_series.max() # Use max as IL is defined as (B&H - Strategy) so positive is bad.
            avg_impermanent_loss = il_series.mean()

        # Other financial metrics
        sharpe_ratio = self._calculate_sharpe_ratio()
        max_drawdown = self._calculate_max_drawdown()
        annualized_volatility = self._calculate_volatility()


        # Prepare final report lines for self.metrics
        self.metrics = {
            'pair': pair_name,
            'base_token': base_token,
            'quote_token': quote_token,
            'initial_base': initial_base,
            'initial_quote': initial_quote,
            'final_base': final_base,
            'final_quote': final_quote,
            'initial_portfolio_value': initial_portfolio_value_exact,
            'final_portfolio_value': final_portfolio_value_exact,
            'profit': profit,
            'return_pct': return_pct,
            'base_change_pct': base_change_pct,
            'quote_change_pct': quote_change_pct,
            'price_return_base': price_return_base,
            'initial_price': self.initial_price,
            'final_price': self.final_price,
            'num_trades': num_trades,
            'total_impermanent_loss': total_impermanent_loss,
            'max_impermanent_loss': max_impermanent_loss,
            'avg_impermanent_loss': avg_impermanent_loss,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'annualized_volatility': annualized_volatility,
        }
        self.logger.info("Performance metrics calculated")

    def _calculate_sharpe_ratio(self, risk_free_rate: float = 0.0) -> float:
        """Calculate annualized Sharpe ratio"""
        # Ensure there's enough data for meaningful return calculations
        if len(self.inventory_history) < 2:
            self.logger.debug("Not enough inventory history to calculate Sharpe ratio.")
            return 0.0
        
        returns = pd.Series([iv['portfolio_value'] for iv in self.inventory_history]).pct_change().dropna()
        if len(returns) < 2: # After dropping NaNs, might have too few points
            self.logger.debug("Not enough valid returns after dropping NaNs to calculate Sharpe ratio.")
            return 0.0

        # Adjust risk-free rate to match the interval of historical data (e.g., daily)
        # Assuming `timeframe` (e.g., '1d') determines the interval length.
        # This is a simplification; a more robust solution would dynamically infer interval.
        annualization_factor = 365 # Default for daily. Needs to be more dynamic.
        if timeframe == '1d':
            annualization_factor = 365
        elif timeframe == '1h':
            annualization_factor = 365 * 24
        # ... add more timeframes if necessary

        # Calculate excess returns and Sharpe Ratio
        excess_returns = returns - risk_free_rate / annualization_factor
        if np.std(excess_returns) == 0:
            self.logger.warning("Standard deviation of excess returns is zero, Sharpe ratio is undefined.")
            return 0.0
        
        sharpe = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(annualization_factor)
        self.logger.debug(f"Calculated Sharpe Ratio: {sharpe:.4f}")
        return sharpe

    def _calculate_max_drawdown(self) -> float:
        """Calculate maximum drawdown during simulation"""
        values = pd.Series([iv['portfolio_value'] for iv in self.inventory_history])
        if values.empty:
            self.logger.debug("No values to calculate max drawdown.")
            return 0.0
        peak = values.expanding(min_periods=1).max()
        drawdown = (values - peak) / peak
        max_dd = drawdown.min()
        self.logger.debug(f"Calculated Max Drawdown: {max_dd:.2f}")
        return max_dd

    def _calculate_volatility(self) -> float:
        """Calculate annualized portfolio volatility"""
        returns = pd.Series([iv['portfolio_value'] for iv in self.inventory_history]).pct_change().dropna()
        if len(returns) < 2:
            self.logger.debug("Not enough returns to calculate volatility.")
            return 0.0
        volatility = returns.std() * np.sqrt(365 * 24)  # Annualized from hourly
        self.logger.debug(f"Calculated Annualized Volatility: {volatility:.2f}")
        return volatility

    def _generate_analysis_report(self):
        """Generate clear double-sided performance report"""
        self.logger.info("Generating analysis report.")
        base_token = self.metrics.get('base_token', 'BASE')
        quote_token = self.metrics.get('quote_token', 'QUOTE')
        initial_price = self.metrics['initial_price']
        final_price = self.metrics['final_price']

        # Calculate inverse prices
        start_inv_price = 1 / initial_price if initial_price > 0 else float('inf')
        end_inv_price = 1 / final_price if final_price > 0 else float('inf')

        # Calculate portfolio values in base token terms
        initial_portfolio_base = self.metrics['initial_portfolio_value'] / initial_price if initial_price > 0 else 0
        final_portfolio_base = self.metrics['final_portfolio_value'] / final_price if final_price > 0 else 0

        # Calculate buy-and-hold values
        bh_final_doge = (self.metrics['initial_base'] * final_price) + self.metrics['initial_quote']
        bh_final_ltc = (self.metrics['initial_quote'] * end_inv_price) + self.metrics['initial_base']

        # For display arithmetic consistency, use formatted metrics directly in calculation strings
        # instead of the exact, internal, high-precision values for display components.
        initial_base_disp = self.metrics['initial_base']
        initial_quote_disp = self.metrics['initial_quote']
        final_base_disp = self.metrics['final_base']
        final_quote_disp = self.metrics['final_quote']
        initial_price_disp = self.metrics['initial_price']
        final_price_disp = self.metrics['final_price']
        start_inv_price_disp = 1 / initial_price_disp if initial_price_disp != 0 else float('inf')
        end_inv_price_disp = 1 / final_price_disp if final_price_disp != 0 else float('inf')

        # Recalculate portfolio values for display consistency
        initial_portfolio_value_disp_quote = (initial_base_disp * initial_price_disp) + initial_quote_disp
        final_portfolio_value_disp_quote = (final_base_disp * final_price_disp) + final_quote_disp
        initial_portfolio_value_disp_base = (initial_quote_disp * start_inv_price_disp) + initial_base_disp
        final_portfolio_value_disp_base = (final_quote_disp * end_inv_price_disp) + final_base_disp

        # Recalculate B&H values for display consistency
        bh_final_quote_disp = (initial_base_disp * final_price_disp) + initial_quote_disp
        bh_final_base_disp = (initial_quote_disp * end_inv_price_disp) + initial_base_disp

        report = f"""
+#=====================================================================================+
#             RANGE MAKER PERFORMANCE REPORT - {self.metrics.get('pair', 'UNKNOWN_PAIR')}         
+#=====================================================================================+
|                                                                                     |
| Simulation Period: {self.historical_data['timestamp'].iloc[0]} to {self.historical_data['timestamp'].iloc[-1]}
| Number of Trades: {self.metrics.get('num_trades', 0)}
|                                                                                     |
| TOKEN BALANCES:                                                                     |
|   Initial:                                                                          |
|     {base_token}: {initial_base_disp:.6f}                                                 
|     {quote_token}: {initial_quote_disp:.6f}                                                
|   Final:                                                                            |
|     {base_token}: {final_base_disp:.6f} ({self.metrics['base_change_pct']:.2f}%) 
|     {quote_token}: {final_quote_disp:.6f} ({self.metrics['quote_change_pct']:.2f}%) 
|                                                                                     |
| PORTFOLIO VALUE:                                                                    |
|   Valuation in {quote_token} terms:                                                             |
|     Initial: ({initial_base_disp:.6f} × {initial_price_disp:.6f}) + {initial_quote_disp:.6f} = {initial_portfolio_value_disp_quote:.6f} {quote_token}
|     Final:   ({final_base_disp:.6f} × {final_price_disp:.6f}) + {final_quote_disp:.6f} = {final_portfolio_value_disp_quote:.6f} {quote_token}
|     Profit: {self.metrics['profit']:.6f} {quote_token}                               
|     Return: {self.metrics['return_pct']:.2f}%                                       
|                                                                                     |
|   Valuation in {base_token} terms:                                                              |
|     Initial: ({initial_quote_disp:.6f} × {start_inv_price_disp:.6f}) + {initial_base_disp:.6f} = {initial_portfolio_value_disp_base:.6f} {base_token}
|     Final:   ({final_quote_disp:.6f} × {end_inv_price_disp:.6f}) + {final_base_disp:.6f} = {final_portfolio_value_disp_base:.6f} {base_token}
|     Profit: {(final_portfolio_value_disp_base - initial_portfolio_value_disp_base):.6f} {base_token}            
|     Return: {(final_portfolio_value_disp_base - initial_portfolio_value_disp_base) / initial_portfolio_value_disp_base * 100 if initial_portfolio_value_disp_base != 0 else 0:.2f}%                                         
|                                                                                     |
| PRICE MOVEMENT:                                                                     |
|   {base_token}/{quote_token}: {initial_price_disp:.6f} → {final_price_disp:.6f} ({self.metrics['price_return_base']:.2f}%) 
|   {quote_token}/{base_token}: {start_inv_price_disp:.6f} → {end_inv_price_disp:.6f} ({(end_inv_price_disp - start_inv_price_disp) / start_inv_price_disp * 100 if start_inv_price_disp != 0 else 0:.2f}%) 
|                                                                                     |
| IMPERMANENT LOSS: (relative to Buy-and-Hold)                                        |
|   Total IL (Final): {self.metrics['total_impermanent_loss']:.6f} {quote_token}
|   Max IL (Worst Case): {self.metrics['max_impermanent_loss']:.6f} {quote_token}
|   Avg IL: {self.metrics['avg_impermanent_loss']:.6f} {quote_token}
|                                                                                     |
| RISK METRICS:                                                                       |
|   Sharpe Ratio (Annualized): {self.metrics['sharpe_ratio']:.4f}
|   Max Drawdown: {self.metrics['max_drawdown']:.4f}%
|   Annualized Volatility: {self.metrics['annualized_volatility']:.4f}
|                                                                                     |
| PERFORMANCE VS BUY-AND-HOLD:                                                        |
|   Strategy Final Value ({quote_token}): {self.metrics['final_portfolio_value']:.6f} 
|   B&H Final Value ({quote_token}): ({initial_base_disp:.6f} × {final_price_disp:.6f}) + {initial_quote_disp:.6f} = {bh_final_quote_disp:.6f} 
|   Outperformance: {self.metrics['final_portfolio_value'] - bh_final_quote_disp:.6f} {quote_token} ({(self.metrics['final_portfolio_value'] - bh_final_quote_disp) / bh_final_quote_disp * 100 if bh_final_quote_disp != 0 else 0:.2f}%) 
|                                                                                     |
|   Strategy Final Value ({base_token}): {self.metrics['final_portfolio_value'] / final_price_disp if final_price_disp != 0 else float('inf'):.6f} 
|   B&H Final Value ({base_token}): ({initial_quote_disp:.6f} × {end_inv_price_disp:.6f}) + {initial_base_disp:.6f} = {bh_final_base_disp:.6f} 
|   Outperformance: {(self.metrics['final_portfolio_value'] / final_price_disp if final_price_disp != 0 else 0) - bh_final_base_disp:.6f} {base_token} ({(((self.metrics['final_portfolio_value'] / final_price_disp if final_price_disp != 0 else 0) - bh_final_base_disp) / bh_final_base_disp) * 100 if bh_final_base_disp != 0 else 0:.2f}%) 
|                                                                                     |
+#=====================================================================================+
        """
        self.logger.info(report)

    def plot_pnl(self):
        """Plot portfolio value over time"""
        self.logger.info("Generating P&L plot.")
        if not self.inventory_history:
            self.logger.warning("No inventory history to plot P&L.")
            return
        values = [iv['portfolio_value'] for iv in self.inventory_history]
        timestamps = [iv['timestamp'] for iv in self.inventory_history]

        plt.figure(figsize=(12, 6))
        plt.plot(timestamps, values)
        plt.title("Portfolio Value Over Time")
        plt.xlabel("Time")
        plt.ylabel("Portfolio Value (Quote)")
        plt.grid(True)
        plt.show()
        self.logger.info("P&L plot generated.")

    def plot_price_with_orders(self):
        """Plot price history with order entry points"""
        self.logger.info("Generating price with orders plot.")
        if not self.price_history or self.historical_data.empty:  # Corrected condition
            self.logger.warning("No price history or historical data to plot price with orders.")
            return
        prices = self.price_history
        timestamps = self.historical_data['timestamp'].tolist()

        buy_times = [t['timestamp'] for t in self.order_history if t['side'] == 'buy']
        sell_times = [t['timestamp'] for t in self.order_history if t['side'] == 'sell']

        plt.figure(figsize=(12, 6))
        plt.plot(timestamps, prices, label='Price')
        plt.scatter(buy_times, [prices[timestamps.index(t)] for t in buy_times],
                    color='green', label='Buys', marker='^')
        plt.scatter(sell_times, [prices[timestamps.index(t)] for t in sell_times],
                    color='red', label='Sells', marker='v')
        plt.title("Price Action with Order Execution")
        plt.xlabel("Time")
        plt.ylabel("Price")
        plt.legend()
        plt.grid(True)
        plt.show()
        self.logger.info("Price with orders plot generated.")

    def plot_animated_order_book(self, save_path: str = None):
        """
        Generates an animated plot of the order book, current price, and balances.
        If save_path is provided, saves the animation to the specified file.
        """
        self.logger.info("Generating animated order book plot.")
        if not self.animation_data:
            self.logger.warning("No animation data available to plot.")
            return

        fig, ax = plt.subplots(figsize=(14, 8))
        fig.suptitle("Range Maker Strategy Simulation", fontsize=16)

        # Initial plot elements
        # Mid-price indicator
        mid_price_line, = ax.plot([], [], color='purple', linestyle='-', label='Shifting Mid Price')

        # Buy and Sell orders
        buy_scatter = ax.scatter([], [], color='green', marker='^', s=50, label='Buy Orders (BID)')
        sell_scatter = ax.scatter([], [], color='red', marker='v', s=50, label='Sell Orders (ASK)')

        # Balance and Timestamp text
        balance_text = ax.text(0.02, 0.98, '', transform=ax.transAxes, verticalalignment='top', fontsize=8,
                               bbox=dict(boxstyle='round,pad=0.3', fc='yellow', alpha=0.5))
        timestamp_text = ax.text(0.02, 0.94, '', transform=ax.transAxes, verticalalignment='top', fontsize=8,
                                 bbox=dict(boxstyle='round,pad=0.3', fc='lightblue', alpha=0.5))

        # Get pair symbols for dynamic axis labels
        pair_name_for_labels = next(iter(self.strategy.active_positions.keys()))
        base_token_label, quote_token_label = pair_name_for_labels.split('/')

        ax.set_xlabel(f"Price ({base_token_label}/{quote_token_label})")
        ax.set_ylabel(f"Amount ({quote_token_label})") # Maker size for buy orders is in quote currency
        ax.grid(True, which='major', linestyle='-', linewidth=0.7)
        ax.grid(True, which='minor', linestyle=':', linewidth=0.5, alpha=0.7)
        ax.xaxis.set_minor_locator(mticker.AutoMinorLocator())
        ax.yaxis.set_minor_locator(mticker.AutoMinorLocator())
        ax.legend(loc='upper right')

        # Add minor ticks and grid
        ax.xaxis.set_major_locator(mticker.MaxNLocator(nbins=8))
        ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=8))

        # Get pair symbols for balance display
        pair_name = next(iter(self.strategy.active_positions.keys()))
        base_token, quote_token = pair_name.split('/')

        # Initialize current_max_y_limit for smoothing
        self.current_max_y_limit = 0.1

        # Extract static strategy parameters from the first frame's position_config
        # Assuming parameters are consistent across the simulation for a given position
        first_position_config = self.animation_data[0]['position_config']

        # Strategy parameters text
        param_text = ax.text(0.02, 0.88, '', transform=ax.transAxes, verticalalignment='top', fontsize=8,
                             bbox=dict(boxstyle='round,pad=0.3', fc='lightgreen', alpha=0.5))
        param_str = (
            f"Strategy: RangeMaker\n"
            f"Pair: {first_position_config.token_pair}\n"
            f"Range: {first_position_config.min_price:.2f}-{first_position_config.max_price:.2f}\n"
            f"Density: {first_position_config.grid_density}\n"
            f"Curve: {first_position_config.curve} (Str: {first_position_config.curve_strength:.1f})\n"
            f"Price Steps: {first_position_config.price_steps}"
        )
        param_text.set_text(param_str)

        # Trade message text (initially hidden, top right)
        trade_message_text = ax.text(0.98, 0.98, '', transform=ax.transAxes, verticalalignment='top',
                                     horizontalalignment='right', fontsize=9, color='blue', fontweight='bold',
                                     bbox=dict(boxstyle='round,pad=0.5', fc='white', alpha=0.8), zorder=5)

        # Trade message at bottom (visible during trade pause)
        trade_message_bottom = ax.text(0.5, 0.02, '', transform=ax.transAxes, verticalalignment='bottom',
                                       horizontalalignment='center', fontsize=12, color='darkgreen',
                                       bbox=dict(boxstyle='round,pad=0.7', fc='lightyellow', alpha=0.9), zorder=10)


        def update_frame(frame_data):
            timestamp = frame_data['timestamp']
            current_price = frame_data['current_price']
            buy_orders_dict = frame_data['active_buy_orders']
            sell_orders_dict = frame_data['active_sell_orders']
            balances = frame_data['balances']
            min_price = frame_data['min_price']
            max_price = frame_data['max_price']
            trade_message = frame_data['trade_message']  # Get trade message for this frame

            # Clear previous stem lines and value texts
            for line in self._order_stem_lines:
                line.remove()
            self._order_stem_lines.clear()
            for text_artist in self._order_value_texts:
                text_artist.remove()
            self._order_value_texts.clear()

            # The current_price from frame_data is the "mid price" for this step
            current_mid_price = current_price

            # Buy orders (X=price, Y=amount) - plot all active buy orders
            buy_orders_list = list(buy_orders_dict.values())
            buy_prices = [o['price'] for o in buy_orders_list]
            buy_amounts = [o['maker_size'] for o in buy_orders_list]  # Already in quote currency
            buy_scatter.set_offsets(np.c_[buy_prices, buy_amounts])

            # Sell orders (X=price, Y=amount) - plot all active sell orders
            sell_orders_list = list(sell_orders_dict.values())
            sell_prices = [o['price'] for o in sell_orders_list]
            sell_amounts = [o['maker_size'] * o['price'] for o in sell_orders_list]  # Convert base amount to quote amount
            sell_scatter.set_offsets(np.c_[sell_prices, sell_amounts])

            # Create value texts for buy orders
            for price, amount in zip(buy_prices, buy_amounts):
                text_label = ax.text(price, amount * 1.05, f"P:{price:.2f}\nA:{amount:.2f}",
                                     fontsize=6, color='darkgreen', ha='center', va='bottom',
                                     bbox=dict(boxstyle='square,pad=0.1', fc='white', ec='none', alpha=0.7))
                self._order_value_texts.append(text_label)


            # Create value texts for sell orders
            for price, amount in zip(sell_prices, sell_amounts):
                text_label = ax.text(price, amount * 1.05, f"P:{price:.2f}\nA:{amount:.2f}",
                                     fontsize=6, color='darkred', ha='center', va='bottom',
                                     bbox=dict(boxstyle='square,pad=0.1', fc='white', ec='none', alpha=0.7))
                self._order_value_texts.append(text_label)


            # Update balances text
            balance_str = f"Balances: {base_token}: {balances.get(base_token, 0):.4f}, {quote_token}: {balances.get(quote_token, 0):.4f}"
            balance_text.set_text(balance_str)

            # Update timestamp text
            timestamp_text.set_text(f"Time: {timestamp.strftime('%Y-%m-%d %H:%M')}")

            # Update trade message (top right)
            trade_message_text.set_text(trade_message)
            trade_message_text.set_visible(bool(trade_message))  # Hide if empty

            # Update trade message at bottom (more prominent during trade)
            trade_message_bottom.set_text(trade_message)
            trade_message_bottom.set_visible(bool(trade_message))

            # Adjust x-axis limits to be static based on min/max price of the strategy
            padding_x = (max_price - min_price) * 0.1
            ax.set_xlim(min_price - padding_x, max_price + padding_x)

            # Adjust y-axis limits dynamically with smoother behavior
            all_amounts = buy_amounts + sell_amounts
            current_frame_max_amount = max(all_amounts) if all_amounts else 0.01

            # The ideal Y-limit for this frame, allowing a buffer
            ideal_y_limit = current_frame_max_amount * 1.20  # 20% buffer above current max amount

            # Smoothing factor for EMA. Smaller values = more smoothing (slower adaptation).
            smoothing_alpha = 0.08  # Adjust this value for desired smoothness vs. responsiveness

            # Initialize current_max_y_limit if it's the first run or close to zero
            if self.current_max_y_limit <= 0.01:
                self.current_max_y_limit = ideal_y_limit
            else:
                # Apply exponential moving average. If ideal_y_limit is much higher, it will pull up quickly.
                # If ideal_y_limit is lower, it will decay slowly towards it.
                self.current_max_y_limit = (1 - smoothing_alpha) * self.current_max_y_limit + smoothing_alpha * ideal_y_limit

            # Ensure the limit never goes below the current highest point + a minimum buffer, or a global floor.
            # This prevents the axis from cutting off data or collapsing too much.
            self.current_max_y_limit = max(self.current_max_y_limit, current_frame_max_amount * 1.10, 0.05)


            ax.set_ylim(0, self.current_max_y_limit)

            # Update mid-price line (vertical line at current_mid_price on the X-axis)
            mid_price_line.set_data([current_mid_price, current_mid_price], ax.get_ylim())

            # Return all artists that have been modified or newly created
            return (*[buy_scatter, sell_scatter, mid_price_line, balance_text, timestamp_text, param_text, trade_message_text, trade_message_bottom], *self._order_stem_lines, *self._order_value_texts)

        ani = animation.FuncAnimation(
            fig,
            update_frame,
            frames=self.animation_data,
            interval=1000,  # Milliseconds per frame (1 second per frame)
            blit=False, # Changed to False for easier management of dynamic number of artists
            repeat=False
        )
        plt.tight_layout(rect=[0, 0.03, 1, 0.95])  # Adjust layout to prevent title overlap
        if save_path:
            self.logger.info(f"Saving animation to {save_path}...")
            try:
                import shutil
                if shutil.which("ffmpeg"):
                    self.logger.info("Using ffmpeg writer for animation.")
                    # Reduced fps for slower video output
                    writer = animation.FFMpegWriter(fps=2, metadata=dict(artist='Me'), codec='libx264')
                    ani.save(save_path, writer=writer, dpi=150)  # Explicitly set DPI
                else:
                    self.logger.warning(
                        "ffmpeg not found. Falling back to pillow writer for GIF. For MP4, install ffmpeg.")
                    # Reduced fps for slower GIF output
                    ani.save(save_path, writer='pillow', fps=1, dpi=150)  # Use pillow writer for GIF
                self.logger.info(f"Animation saved successfully to {save_path}")
            except Exception as e:
                self.logger.error(f"Error saving animation to {save_path}: {e}", exc_info=True)
                self.logger.warning("Animation could not be saved. Check ffmpeg installation and dependencies.")
                return  # Exit to prevent further hanging
        else:
            self.logger.info(
                "Animated order book plot generated (not saved or displayed interactively as no save_path was provided).")

    async def optimize_parameters(self, param_grid: Dict[str, list], cv=5):
        """Perform grid search optimization of strategy parameters"""
        self.logger.info("Starting parameter optimization (not implemented in this version).")
        # Implementation for parameter optimization would go here
        pass


if __name__ == '__main__':
    # Example usage
    from definitions.config_manager import ConfigManager

    # Setup strategy
    config_manager = ConfigManager(strategy="range_maker")
    config_manager.initialize(
        loadxbridgeconf=False
    )
    # Manually initialize strategy specifics for each pair in the example usage
    example_pairs = [
        {"pair": "LTC/DOGE", "min_price": 400, "max_price": 600, "grid_density": 20, "curve": "linear",
         "curve_strength": 25, "percent_min_size": 0.0001, "initial_middle_price": 500.0,
         "initial_balances": {"LTC": 100, "DOGE": 100000}}]  # Updated example_pairs with initial_middle_price
    for pair_cfg in example_pairs:
        config_manager.strategy_instance.initialize_strategy_specifics(**pair_cfg)

    # Setup backtester
    # No data_path argument needed, as it's constructed internally using globals
    backtester = RangeMakerBacktester(config_manager.strategy_instance)

    # Run backtest
    # Define animation flag and pass to backtester
    animate_graph = True
    asyncio.run(backtester.execute_fullbacktest(initial_balances={"LTC": 100, "DOGE": 100000}, period=period,
                                                interval=timeframe, animate_graph=animate_graph))
    # backtester.plot_pnl()
    # backtester.plot_price_with_orders()
    if backtester.animation_data and animate_graph:
        backtester.logger.info(f"Animation data collected: {len(backtester.animation_data)} frames.")
        backtester.logger.info(f"Animate graph flag is: {animate_graph}")

        # Generate a unique filename for the animation based on strategy parameters
        pair_cfg = example_pairs[0]
        pair_symbol = pair_cfg['pair'].replace('/', '_')
        min_p = str(pair_cfg['min_price']).replace('.', '_')
        max_p = str(pair_cfg['max_price']).replace('.', '_')
        grid_d = pair_cfg['grid_density']
        curve_type = pair_cfg['curve']
        curve_s = pair_cfg['curve_strength']
        percent_min_s = pair_cfg.get('percent_min_size', 0.0001)  # New line

        animation_filename = f"animation_{pair_symbol}_min{min_p}_max{max_p}_grid{grid_d}_curve{curve_type}_strength{curve_s}_min_size{str(percent_min_s).replace('.', '_')}.gif"

        # Save the animation in the current script's directory
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
            backtester.logger.warning("For macOS (with Homebrew): `brew install imagemagick` or `brew install ffmpeg`")
            backtester.logger.warning(
                "For Windows (with Chocolatey): `choco install imagemagick` or `choco install ffmpeg`")
