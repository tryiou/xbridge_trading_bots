import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict

import matplotlib
matplotlib.use('Agg') # Use non-interactive backend for animation saving
import matplotlib.animation as animation
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from definitions.logger import setup_logging

timeframe = '1h'
period = "1d"

formatter = logging.Formatter(
    fmt='[%(asctime)s] [%(name)-20s] %(levelname)-8s - %(message)s',
    datefmt='%H:%M:%S'
)

def override_all_formatters():
    # Start with root logger
    loggers = [logging.getLogger()]
    # Add all other loggers registered so far
    loggers += [
        logging.getLogger(name)
        for name in logging.root.manager.loggerDict.keys()
        if isinstance(logging.getLogger(name), logging.Logger)
    ]

    for logger in loggers:
        for handler in logger.handlers:
            handler.setFormatter(formatter)

class RangeMakerBacktester:
    """Backtesting engine for RangeMaker strategy simulations"""

    def __init__(self, strategy_instance):
        self.strategy = strategy_instance
        self.logger = strategy_instance.logger
        self.historical_data = None
        self.simulation_results = []
        self.metrics = {}
        # Dynamically construct data_file_path using the script's directory and global variables
        self.data_file_path = Path(
            __file__).parent / f"{self.get_pair_symbol()}_historical_data_{period}_{timeframe}.csv"

        self.logger = setup_logging(name="range_maker_backtester", level=logging.INFO, console=True)

        override_all_formatters()

                    
        self.strategy.logger.setLevel(logging.DEBUG)  # Increase verbosity for troubleshooting
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
        self.impermanent_loss_history = [] # New: Store Impermanent Loss over time
        self.initial_hold_base_amount = 0.0 # New: For IL calculation
        self.initial_hold_quote_amount = 0.0 # New: For IL calculation
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
        self.logger.info(f"Attempting to load historical data from: {self.data_file_path}")
        file_path = self.data_file_path
        if not file_path.exists():
            self.logger.error(f"Historical data file not found at {file_path.absolute()}")
            raise FileNotFoundError(f"Historical data file not found at {file_path.absolute()}")
        self.logger.debug(f"Found historical data file at: {file_path.absolute()}")

        try:
            # Load the raw data
            self.logger.debug(f"Reading CSV from {file_path}")
            raw_data = pd.read_csv(file_path, parse_dates=['date'])
            raw_data.rename(columns={'date': 'timestamp'}, inplace=True)
            self.logger.debug(f"Raw data loaded. Columns: {raw_data.columns.tolist()}, Rows: {len(raw_data)}")

            # Determine pair and required columns
            if not self.strategy.active_positions:
                self.logger.error(
                    "Strategy active_positions is empty. Cannot determine pair name for historical data loading.")
                raise ValueError("Strategy not initialized with active positions.")
            pair_name = next(iter(self.strategy.active_positions.keys()))
            self.logger.info(f"Determined pair name for historical data: {pair_name}")
            base, quote = pair_name.split('/')
            base_col, quote_col = f"{base}-USD", f"{quote}-USD"
            self.logger.debug(f"Base column: {base_col}, Quote column: {quote_col}")

            if base_col not in raw_data.columns or quote_col not in raw_data.columns:
                self.logger.error(
                    f"Missing required price columns for {pair_name} in {self.data_file_path}. Expected '{base_col}' and '{quote_col}'. Available columns: {raw_data.columns.tolist()}")
                raise ValueError(f"Missing required price columns for {pair_name} in {self.data_file_path}")
            self.logger.debug("Required price columns found in raw data.")

            # Set timestamp as index and resample to the desired timeframe, forward-filling gaps
            self.logger.debug(f"Resampling data to {timeframe} and forward-filling gaps.")
            self.historical_data = raw_data.set_index('timestamp').resample(timeframe).ffill()
            self.logger.debug(f"Data resampled. New shape: {self.historical_data.shape}")

            # Calculate the correct pair price (Base/Quote)
            self.logger.debug(f"Calculating '{pair_name}' close price from '{base_col}' and '{quote_col}'.")
            self.historical_data['close'] = self.historical_data[base_col] / self.historical_data[quote_col]
            self.historical_data.loc[self.historical_data[quote_col] == 0, 'close'] = np.nan
            self.logger.debug("Pair price calculated.")

            # Fill any remaining NaNs after calculation
            self.logger.debug("Filling any remaining NaNs (forward and backward fill).")
            self.historical_data.ffill(inplace=True)
            self.historical_data.bfill(inplace=True)
            self.logger.debug("NaNs filled.")

            # Simplify OHLC data for this backtest
            self.logger.debug("Simplifying OHLC data.")
            for col in ['open', 'high', 'low']:
                self.historical_data[col] = self.historical_data['close']
            self.historical_data['volume'] = 0
            self.logger.debug("OHLC data simplified.")

            self.historical_data.reset_index(inplace=True)
            self.logger.info(f"Successfully loaded and processed {len(self.historical_data)} historical data points.")

        except Exception as e:
            self.logger.error(f"Error loading or processing historical data: {e}", exc_info=True)
            raise

    async def download_sample_data(self, file_path: str, pair: str, interval: str):
        """Download sample historical price data using yfinance"""
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

            self.logger.debug("Combining 'Close' prices into a single DataFrame.")
            df = pd.DataFrame(index=base_data.index)
            df.index.name = 'date'
            df[base_sym] = base_data['Close']
            df[quote_sym] = quote_data['Close']
            self.logger.debug(f"Combined DataFrame created. Shape: {df.shape}")

            self.logger.debug("Forward-filling any missing values.")
            df.ffill(inplace=True)
            df.bfill(inplace=True)
            self.logger.debug("Missing values filled.")

            df.to_csv(file_path)
            self.logger.info(f"Successfully downloaded and saved data to {file_path}")
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
            self.logger.debug(
                f"Mock balances set for {t1_sym}: {self.current_balances.get(t1_sym, 0)} and {t2_sym}: {self.current_balances.get(t2_sym, 0)}")

            pair_cfg = {
                'name': pair_name,
                'min_price': position.min_price,
                'max_price': position.max_price,
                'grid_density': position.grid_density
            }
            self.logger.debug(f"Pair config for {pair_name}: {pair_cfg}")

            self.strategy.pairs[pair_name] = Pair(
                token1=token1,
                token2=token2,
                cfg=pair_cfg,
                strategy="range_maker",
                config_manager=self.strategy.config_manager
            )
            self.logger.debug(f"Mock Pair object created for {pair_name}.")
        self.logger.info("Mock Pair objects setup complete.")

    async def execute_fullbacktest(self, initial_balances: Dict[str, float], period: str = "1y", interval: str = "1h",
                                   animate_graph: bool = False):
        """
        Run full historical simulation with initial token balances.
        Updates strategy state through simulated time periods.
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

            self.logger.debug("Loading historical data.")
            self.logger.info(f"Loading historical data from file: {self.data_file_path}")
            await self.load_historical_data()

            if self.historical_data is None or self.historical_data.empty:
                self.logger.error("No historical data loaded or data is empty - aborting backtest.")
                raise ValueError("Historical data not loaded successfully or is empty.")
            self.logger.info(f"Historical data loaded successfully with {len(self.historical_data)} records.")

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
                self.logger.debug(f"Initial hold amounts for IL: {base_token}={self.initial_hold_base_amount}, {quote_token}={self.initial_hold_quote_amount}")

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
            self.logger.info("Mock pairs setup complete.")

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

            self.logger.info(f"Initializing order grid for {pair_name}.")
            await self.strategy.initialize_order_grid(pair_instance, position)
            self.logger.info(f"Order grid initialized for {pair_name}.")

            self.logger.info("Resetting simulation state.")
            self._reset_simulation_state()
            self.logger.debug("Simulation state reset.")

            self.logger.info(f"Beginning simulation of {len(self.historical_data)} time periods.")
            self.logger.debug(f"Historical data head:\n{self.historical_data.head()}")

            try:
                for idx, row in self.historical_data.iterrows():
                    current_price = row['close']
                    timestamp = row['timestamp']
                    self.price_history.append(current_price)

                    # Log key events, not every single step
                    if idx % 100 == 0 or idx == len(self.historical_data) - 1:  # Log every 100 periods or at the end
                        self.logger.debug(
                            f"Simulating period {idx + 1}/{len(self.historical_data)} at price: {current_price:.6f} (Timestamp: {timestamp})")

                    await self.simulate_time_step(current_price, timestamp)
                    self._record_simulation_state(timestamp)

                    if animate_graph:
                        # Capture data for animation
                        pair_name = next(iter(self.strategy.active_positions.keys()))
                        position = self.strategy.active_positions[pair_name]

                        self.animation_data.append({
                            'timestamp': timestamp,
                            'current_price': current_price,
                            'active_buy_orders': [o for o in self.strategy.order_grids[pair_name]['active_orders'] if
                                                  o['type'] == 'buy'],
                            'active_sell_orders': [o for o in self.strategy.order_grids[pair_name]['active_orders'] if
                                                   o['type'] == 'sell'],
                            'balances': self.current_balances.copy(),
                            'min_price': position.min_price,
                            'max_price': position.max_price
                        })

                    if idx > 0 and idx % 100 == 0:  # Changed to 100 for more frequent updates
                        self.logger.info(
                            f"Progress: {idx + 1}/{len(self.historical_data)} periods completed. Current price: {current_price:.6f}")
                        self.logger.info(f"Current balances: {self.current_balances}")

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

            self.logger.info("Generating analysis report.")
            self._generate_analysis_report()

            self.logger.info("Historical simulation completed successfully.")

        except Exception as e:
            self.logger.critical(f"Critical error during backtest: {str(e)}", exc_info=True)
            self.logger.critical("Backtest aborted due to critical error.")
            return  # Exit gracefully instead of re-raising

    async def simulate_time_step(self, current_price: float, timestamp: datetime):
        """Simulate strategy behavior for a single time step"""
        # Only log this if it's a significant step (e.g., every 50 periods, or if fills occur)
        # self.logger.debug(f"Simulating time step at {timestamp} with price {current_price:.6f}. Balances: {self.current_balances}")
        pair_name = next(iter(self.strategy.active_positions.keys()))
        pair_instance = self.strategy.pairs[pair_name]

        filled_orders = await self.strategy.thread_loop_async_action(pair_instance, current_price=current_price)

        if filled_orders:
            self.logger.debug(f"Simulated {len(filled_orders)} fills at {timestamp} with price {current_price:.6f}.")
            for order in filled_orders:
                self._process_simulated_fill(order, timestamp)

        self._update_mock_balances(pair_instance)

    def _update_mock_balances(self, pair_instance):
        """Update the mock free_balance on the pair's tokens."""
        t1_sym, t2_sym = pair_instance.symbol.split('/')
        # self.logger.debug(f"Updating mock balances for {t1_sym} and {t2_sym}.") # Too spammy
        pair_instance.t1.dex.free_balance = self.current_balances.get(t1_sym, 0)
        pair_instance.t2.dex.free_balance = self.current_balances.get(t2_sym, 0)
        # self.logger.debug(f"Mock balances set: {t1_sym}={pair_instance.t1.dex.free_balance}, {t2_sym}={pair_instance.t2.dex.free_balance}") # Too spammy

    def _process_simulated_fill(self, order: dict, timestamp: datetime):
        """Update balances and track metrics for filled orders"""
        fee = 0.0  # XBridge fees are negligible and should be ignored for now

        # Update balances based on the fill
        self.current_balances[order['maker']] -= order['maker_size']
        self.current_balances[order['taker']] += order['taker_size'] - fee

        trade = {
            'timestamp': timestamp,
            'pair': f"{order['maker']}/{order['taker']}",
            'side': order['type'],
            'price': order['price'],
            'size': order['maker_size'],
            'fee': fee,
            'taker_received': order['taker_size'] - fee,
            'order_id': order.get('id', 'simulated') # Include order ID for better traceability
        }
        self.order_history.append(trade)
        self.fee_history.append(fee)

        # Calculate P&L for counter orders (if applicable)
        profit = 0.0
        if order.get('is_counter'):
            if order['type'] == 'sell':  # Original was a buy, counter is a sell
                # Profit = (sell_price - original_buy_price) * base_amount_sold
                profit = (order['price'] - order['original_price']) * order['maker_size']
            elif order['type'] == 'buy':  # Original was a sell, counter is a buy
                # Profit = (original_sell_price - buy_price) * base_amount_bought
                # For a buy counter-order, maker_size is quote (amount spent), taker_size is base (amount received).
                # Profit should be calculated on the base amount received.
                profit = (order['original_price'] - order['price']) * order['taker_size'] # Corrected P&L calculation
            self.counter_pnl_history.append(profit)
            self.logger.info(
                f"  Counter-order filled: ID={trade['order_id']} | Type={trade['side']} | Price={trade['price']:.6f} | Size={trade['size']:.6f} | P&L={profit:.6f}")
        else:
            self.logger.info(
                f"  Initial order filled: ID={trade['order_id']} | Type={trade['side']} | Price={trade['price']:.6f} | Size={trade['size']:.6f}")

        self.logger.info(f"  Balances after fill: {self.current_balances}") # Log balances after every trade

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
       # self.logger.debug(f"Recorded simulation state at {timestamp}. Portfolio value: {portfolio_value:.6f}, IL: {buy_and_hold_value - portfolio_value:.6f}")

    def _calculate_portfolio_value(self) -> float:
        """Calculate total portfolio value in the quote currency."""
        if not self.strategy.active_positions:
            self.logger.debug("No active positions in strategy, portfolio value is 0.")
            return 0
        if not self.price_history:
            self.logger.debug("No price history available, portfolio value is 0.")
            return 0

        pair_name = next(iter(self.strategy.active_positions.keys()))
        base_token, quote_token = pair_name.split('/')
        current_price = self.price_history[-1]

        # Calculate buy and hold portfolio value for Impermanent Loss tracking
        buy_and_hold_value = (self.initial_hold_base_amount * current_price) + self.initial_hold_quote_amount

        base_value = self.current_balances.get(base_token, 0) * current_price
        quote_value = self.current_balances.get(quote_token, 0)
        total_value = base_value + quote_value
        return total_value

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

        # Calculate P&L
        initial_value = self.inventory_history[0]['portfolio_value']
        self.logger.debug(f"Initial portfolio value: {initial_value:.6f}")

        # Calculate final portfolio value using the average price of the last few trades
        num_last_trades = min(10, len(self.order_history))
        if num_last_trades > 0:
            last_trades = self.order_history[-num_last_trades:]
            avg_price = sum(t['price'] for t in last_trades) / num_last_trades
            self.logger.debug(f"Calculated average price from last {num_last_trades} trades: {avg_price:.6f}")
        else:
            avg_price = self.price_history[-1] if self.price_history else self.initial_price
            self.logger.debug(
                f"Using last price ({avg_price:.6f}) or initial price ({self.initial_price:.6f}) for final value calculation.")

        base_token, quote_token = next(iter(self.strategy.active_positions.keys())).split('/')
        final_base_value = self.final_balances.get(base_token, 0) * avg_price
        final_quote_value = self.final_balances.get(quote_token, 0)
        final_value = final_base_value + final_quote_value
        self.logger.debug(f"Final portfolio value: {final_value:.6f}")

        # Calculate Win Rate only on counter-orders based on P&L
        wins = []  # Initialize wins to an empty list
        if self.counter_pnl_history:
            wins = [pnl for pnl in self.counter_pnl_history if pnl > 0]
            win_rate = (len(wins) / len(self.counter_pnl_history)) * 100
        else:
            win_rate = 0
        self.logger.debug(
            f"Total counter orders (P&L entries): {len(self.counter_pnl_history)}, Wins: {len(wins)}, Win Rate: {win_rate:.2f}%")

        self.metrics = {
            'total_return_pct': ((final_value - initial_value) / initial_value) * 100 if initial_value != 0 else 0.0,
            'sharpe_ratio': self._calculate_sharpe_ratio(),
            'max_drawdown_pct': self._calculate_max_drawdown() * 100,
            'annualized_volatility_pct': self._calculate_volatility() * 100,
            'total_fees': sum(self.fee_history),
            'num_trades': len(self.order_history),
            'win_rate_pct': win_rate,
            'initial_portfolio_value': initial_value,
            'final_portfolio_value': final_value,
            'initial_price': self.initial_price,  # Add initial_price to metrics
            'final_price': self.final_price,  # Add final_price to metrics
            'final_impermanent_loss': self.impermanent_loss_history[-1] if self.impermanent_loss_history else 0.0, # Final IL
            'average_impermanent_loss': np.mean(self.impermanent_loss_history) if self.impermanent_loss_history else 0.0, # Average IL
            'max_impermanent_loss': np.max(self.impermanent_loss_history) if self.impermanent_loss_history else 0.0, # Max IL
            'min_impermanent_loss': np.min(self.impermanent_loss_history) if self.impermanent_loss_history else 0.0, # Min IL
        }
        self.logger.info(f"Performance metrics calculated: {self.metrics}")

    def _calculate_sharpe_ratio(self, risk_free_rate: float = 0.0) -> float:
        """Calculate annualized Sharpe ratio"""
        returns = pd.Series([iv['portfolio_value'] for iv in self.inventory_history]).pct_change().dropna()
        if len(returns) < 2:
            self.logger.debug("Not enough returns to calculate Sharpe ratio.")
            return 0.0
        excess_returns = returns - risk_free_rate / (365 * 24)  # Adjust for hourly data
        sharpe = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(365 * 24)
        self.logger.debug(f"Calculated Sharpe Ratio: {sharpe:.2f}")
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
        """Generate summary report of simulation results"""
        self.logger.info("Generating analysis report.")
        report = f"""
        Range Maker Backtest Report
        ---------------------------
        Simulation Period: {self.historical_data['timestamp'].iloc[0]} to {self.historical_data['timestamp'].iloc[-1]}
        Total Return: {self.metrics.get('total_return_pct', 0.0):.2f}%
        Sharpe Ratio: {self.metrics.get('sharpe_ratio', 0.0):.2f}
        Max Drawdown: {self.metrics.get('max_drawdown_pct', 0.0):.2f}%
        Annualized Volatility: {self.metrics.get('annualized_volatility_pct', 0.0):.2f}%
        Total Fees Paid: {self.metrics.get('total_fees', 0.0):.6f}
        Trades Executed: {self.metrics.get('num_trades', 0)}
        Win Rate (Counter Orders): {self.metrics.get('win_rate_pct', 0.0):.2f}%
        Initial Portfolio Value: {self.metrics.get('initial_portfolio_value', 0.0):.6f}
        Final Portfolio Value: {self.metrics.get('final_portfolio_value', 0.0):.6f}
        Initial Balances: {self.initial_balances}
        Final Balances: {self.final_balances}
        Initial Price: {self.metrics.get('initial_price', 0.0):.6f}
        Final Price: {self.metrics.get('final_price', 0.0):.6f}
        LTC Balance Change: {self.final_balances.get('LTC', 0) - self.initial_balances.get('LTC', 0):.6f}
        DOGE Balance Change: {self.final_balances.get('DOGE', 0) - self.initial_balances.get('DOGE', 0):.6f}
        Impermanent Loss (Final): {self.metrics.get('final_impermanent_loss', 0.0):.6f}
        Impermanent Loss (Average): {self.metrics.get('average_impermanent_loss', 0.0):.6f}
        Impermanent Loss (Max): {self.metrics.get('max_impermanent_loss', 0.0):.6f}
        Impermanent Loss (Min): {self.metrics.get('min_impermanent_loss', 0.0):.6f}
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

        ax.set_xlabel("Price")
        ax.set_ylabel("Amount")
        ax.grid(True)
        ax.legend(loc='upper right')

        # Get pair symbols for balance display
        pair_name = next(iter(self.strategy.active_positions.keys()))
        base_token, quote_token = pair_name.split('/')

        def update_frame(frame_data):
            timestamp = frame_data['timestamp']
            current_price = frame_data['current_price']
            buy_orders = frame_data['active_buy_orders']
            sell_orders = frame_data['active_sell_orders']
            balances = frame_data['balances']
            min_price = frame_data['min_price']
            max_price = frame_data['max_price']

            # The current_price from frame_data is the "mid price" for this step
            current_mid_price = current_price

            # Buy orders (X=price, Y=amount) - plot all active buy orders
            buy_prices = [o['price'] for o in buy_orders]
            buy_amounts = [o['maker_size'] for o in buy_orders] # Already in quote currency
            buy_scatter.set_offsets(np.c_[buy_prices, buy_amounts])

            # Sell orders (X=price, Y=amount) - plot all active sell orders
            sell_prices = [o['price'] for o in sell_orders]
            sell_amounts = [o['maker_size'] * o['price'] for o in sell_orders] # Convert base amount to quote amount
            sell_scatter.set_offsets(np.c_[sell_prices, sell_amounts])

            # Update balances text
            balance_str = f"Balances: {base_token}: {balances.get(base_token, 0):.4f}, {quote_token}: {balances.get(quote_token, 0):.4f}"
            balance_text.set_text(balance_str)

            # Update timestamp text
            timestamp_text.set_text(f"Time: {timestamp.strftime('%Y-%m-%d %H:%M')}")

            # Adjust x-axis limits to be static based on min/max price of the strategy
            padding_x = (max_price - min_price) * 0.1
            ax.set_xlim(min_price - padding_x, max_price + padding_x)

            # Adjust y-axis limits dynamically based on max amount in current orders
            all_amounts = buy_amounts + sell_amounts
            max_amount = max(all_amounts) if all_amounts else 0.1 # Prevent division by zero
            padding_y = max_amount * 0.1
            ax.set_ylim(0, max_amount + padding_y)

            # Update mid-price line (vertical line at current_mid_price on the X-axis)
            # This must be done AFTER ax.set_ylim to ensure it spans the current dynamic Y-axis limits
            mid_price_line.set_data([current_mid_price, current_mid_price], ax.get_ylim())

            return buy_scatter, sell_scatter, mid_price_line, balance_text, timestamp_text

        ani = animation.FuncAnimation(
            fig,
            update_frame,
            frames=self.animation_data,
            interval=100,  # Milliseconds per frame
            blit=True,
            repeat=False
        )
        plt.tight_layout(rect=[0, 0.03, 1, 0.95])  # Adjust layout to prevent title overlap
        if save_path:
            self.logger.info(f"Saving animation to {save_path}...")
            try:
                import shutil
                if shutil.which("ffmpeg"):
                    self.logger.info("Using ffmpeg writer for animation.")
                    writer = animation.FFMpegWriter(fps=10, metadata=dict(artist='Me'), codec='libx264')
                    ani.save(save_path, writer=writer, dpi=150) # Explicitly set DPI
                else:
                    self.logger.warning("ffmpeg not found. Falling back to pillow writer for GIF. For MP4, install ffmpeg.")
                    ani.save(save_path, writer='pillow', fps=10, dpi=150) # Use pillow writer for GIF
                self.logger.info(f"Animation saved successfully to {save_path}")
            except Exception as e:
                self.logger.error(f"Error saving animation to {save_path}: {e}", exc_info=True)
                self.logger.warning("Animation could not be saved. Check ffmpeg installation and dependencies.")
                return # Exit to prevent further hanging
        else:
            self.logger.info("Animated order book plot generated (not saved or displayed interactively as no save_path was provided).")

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
         "curve_strength": 25, "percent_min_size": 0.0001}] # Updated example_pairs
    for pair_cfg in example_pairs:
        config_manager.strategy_instance.initialize_strategy_specifics(**pair_cfg)

    strategy = config_manager.strategy_instance

    # Setup backtester
    # No data_path argument needed, as it's constructed internally using globals
    backtester = RangeMakerBacktester(strategy)

    # Run backtest
    # Pass global period and timeframe to execute_fullbacktest
    asyncio.run(backtester.execute_fullbacktest(initial_balances={"LTC": 100, "DOGE": 100000}, period=period,
                                                interval=timeframe, animate_graph=True))
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
        percent_min_s = pair_cfg.get('percent_min_size', 0.0001) # New line

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
