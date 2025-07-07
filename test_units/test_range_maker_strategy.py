import logging
import asyncio
import io
import pandas as pd
import time
import yfinance as yf
from aiohttp import ClientSession, TCPConnector, ClientTimeout
from datetime import datetime
from pathlib import Path
from typing import List, Dict

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from definitions.logger import setup_logging


class RangeMakerBacktester:
    """Backtesting engine for RangeMaker strategy simulations"""

    def __init__(self, strategy_instance, data_path: str):
        self.strategy = strategy_instance
        self.data_path = data_path
        self.logger = strategy_instance.logger
        self.historical_data = None
        self.simulation_results = []
        self.metrics = {}

        self.logger = setup_logging(name="range_maker_backtester", level=logging.DEBUG, console=True)
        # Simulation state
        self.current_balances = {}
        self.order_history = []
        self.price_history = []
        self.inventory_history = []
        self.fee_history = []

    async def load_historical_data(self, time_frame: str = '1h'):
        """
        Load historical price data from CSV/JSON file.
        Expected columns: timestamp, open, high, low, close, volume
        """
        self.logger.info(f"Loading historical data from: {self.data_path}")
        self.logger.debug(f"Requested time frame: {time_frame}")

        path = Path(self.data_path)
        self.logger.debug(f"Resolved path: {path.absolute()}")
        try:
            if path.suffix == '.csv':
                self.logger.debug("Loading CSV file")
                self.logger.debug(f"Attempting to read CSV from: {path.absolute()}")
                
                # First read just the columns to debug structure
                try:
                    cols = pd.read_csv(path, nrows=0).columns.tolist()
                    self.logger.debug(f"CSV columns detected: {cols}")
                    self.logger.debug(f"Checking for timestamp column in: {cols}")
                    
                    # Check if timestamp exists, fallback to date
                    if 'timestamp' not in cols and 'date' in cols:
                        self.logger.warning("'timestamp' column not found but 'date' exists - using 'date' as timestamp")
                        self.historical_data = pd.read_csv(path, parse_dates=['date'])
                        self.historical_data.rename(columns={'date': 'timestamp'}, inplace=True)
                    else:
                        self.historical_data = pd.read_csv(path, parse_dates=['timestamp'])
                        
                    self.logger.debug(f"CSV loaded successfully. Columns: {self.historical_data.columns.tolist()}")
                    self.logger.debug(f"First 3 rows:\n{self.historical_data.head(3).to_string()}")
                    self.logger.debug(f"Date range: {self.historical_data['timestamp'].min()} to {self.historical_data['timestamp'].max()}")
                    
                except Exception as read_error:
                    self.logger.error("CSV structure debug - reading first 5 rows as plain text:")
                    with open(path, 'r') as f:
                        for i, line in enumerate(f):
                            if i < 5:
                                self.logger.error(f"Row {i}: {line.strip()}")
                            else:
                                break
                    raise read_error
            else:
                error_msg = f"Unsupported file format: {path.suffix}. Use CSV or JSON"
                self.logger.error(error_msg)
                raise ValueError(error_msg)

            # Get actual column names from the data
            price_columns = [col for col in self.historical_data.columns if col != 'timestamp']
            
            self.historical_data = self.historical_data.resample(time_frame, on='timestamp').agg({
                col: 'mean' for col in price_columns  # Use mean for price columns when resampling
            }).reset_index()
            
            # Create single price column from average of both assets
            self.historical_data['close'] = self.historical_data[price_columns].mean(axis=1)
            self.historical_data['open'] = self.historical_data[price_columns].mean(axis=1)
            self.historical_data['high'] = self.historical_data[price_columns].mean(axis=1)
            self.historical_data['low'] = self.historical_data[price_columns].mean(axis=1)
            self.historical_data['volume'] = 0  # Volume not available in sample data

            # Verify we have required timestamp column
            if 'timestamp' not in self.historical_data.columns:
                raise ValueError("Missing required 'timestamp' column in historical data")
            self.logger.debug(f"Data types:\n{self.historical_data.dtypes}")
            self.logger.debug(f"Final columns after processing: {self.historical_data.columns.tolist()}")

        except FileNotFoundError as e:
            self.logger.error(f"Data file not found: {path}")
            self.logger.error(f"Absolute path: {path.absolute()}")
            self.logger.error("Please check: ")
            self.logger.error("- File exists at this path")
            self.logger.error("- File permissions (read access)")
            self.logger.error("- Disk space availability")
            raise
        except pd.errors.EmptyDataError as e:
            self.logger.error(f"Empty data file: {path}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error loading data: {str(e)}")
            self.logger.error("Data loading failed", exc_info=True)
            raise

        # Get actual column names from the data
        price_columns = [col for col in self.historical_data.columns if col != 'timestamp']
        
        self.historical_data = self.historical_data.resample(time_frame, on='timestamp').agg({
            col: 'mean' for col in price_columns  # Use mean for price columns when resampling
        }).reset_index()
        
        # Create single price column from average of both assets
        self.historical_data['close'] = self.historical_data[price_columns].mean(axis=1)
        self.historical_data['open'] = self.historical_data[price_columns].mean(axis=1)
        self.historical_data['high'] = self.historical_data[price_columns].mean(axis=1)
        self.historical_data['low'] = self.historical_data[price_columns].mean(axis=1)
        self.historical_data['volume'] = 0  # Volume not available in sample data

        self.logger.info(f"Loaded {len(self.historical_data)} historical data points")

    async def download_sample_data(self, file_path: str, pair: str):
        """Download sample historical price data using yfinance"""
        try:
            import yfinance as yf
        except ImportError:
            self.logger.error("yfinance package required for downloading historical data")
            self.logger.error("Install with: pip install yfinance")
            raise

        if "/" in pair:
            base_token, quote_token = pair.split("/")
            yahoo_symbols = [f"{base_token}-USD", f"{quote_token}-USD"]
        else:
            raise ValueError(f"Invalid pair format: {pair}. Must be in BASE/QUOTE format")

        try:
            # Download historical data for both tokens
            data = yf.download(
                tickers=yahoo_symbols,
                period="1y",
                interval="1d",
                group_by='ticker',
                progress=False
            )

            # Process and merge the data
            dfs = []
            for symbol in yahoo_symbols:
                df = data[symbol][['Open', 'High', 'Low', 'Close', 'Volume']]
                df = df.reset_index().rename(columns={'Date': 'date'})
                df['symbol'] = symbol
                dfs.append(df)

            merged_df = pd.concat(dfs)
            # Forward fill and take last value when resampling
            merged_df = merged_df.pivot_table(
                index='date', 
                columns='symbol',
                values=['Open', 'High', 'Low', 'Close', 'Volume'],
                aggfunc='last'
            )
            merged_df.ffill(inplace=True)
            merged_df.to_csv(file_path)
            
            self.logger.info(f"Successfully downloaded and saved data to {file_path}")
            return

        except Exception as e:
            self.logger.error(f"Failed to download data using yfinance: {str(e)}")
            self.logger.error("Please check the token symbols and your internet connection")
            raise ValueError("Historical data download failed")

    async def execute_fullbacktest(self, initial_balances: Dict[str, float] = None):
        """
        Run full historical simulation with initial token balances.
        Updates strategy state through simulated time periods.
        """
        self.logger.info("Starting full backtest execution")

        try:
            # Auto-download data if missing
            data_path = Path(self.data_path)
            if not data_path.exists():
                pair = next(iter(self.strategy.active_positions.values())).token_pair
                self.logger.warning(f"Data file {self.data_path} not found - downloading sample data for {pair}")
                await self.download_sample_data(self.data_path, pair)

            self.logger.debug("Loading historical data")
            await self.load_historical_data()

            if self.historical_data is None:
                self.logger.error("No historical data loaded - aborting backtest")
                raise ValueError("Historical data not loaded successfully")

            self.logger.debug(f"Historical data contains {len(self.historical_data)} records")
            self.logger.debug("Sample data:\n" + str(self.historical_data.head(3)))

            self.current_balances = (initial_balances or {}).copy()

            if not self.current_balances:
                self.logger.info("No initial balances provided - generating defaults")
                tokens = list(self.strategy.active_positions.keys())[0].split('/')
                self.current_balances = {token: 1000.0 for token in tokens}
                self.logger.debug(f"Generated initial balances: {self.current_balances}")

            self.logger.info("Resetting simulation state")
            self._reset_simulation_state()

            self.logger.info(f"Beginning simulation of {len(self.historical_data)} time periods")

            try:
                for idx, row in self.historical_data.iterrows():
                    self.logger.debug(f"\n=== Processing period {idx + 1}/{len(self.historical_data)} ===")
                    self.logger.debug(f"Row data: {row.to_dict()}")

                    current_price = row['close']
                    self.logger.debug(f"Current price: {current_price}")

                    timestamp = row['timestamp']
                    self.logger.debug(f"Timestamp: {timestamp}")

                    self.price_history.append(current_price)
                    self.logger.debug("Price history updated")

                    self.logger.debug("Executing strategy logic for time period")
                    await self.simulate_time_step(current_price, timestamp)

                    self.logger.debug("Recording simulation state")
                    self._record_simulation_state(timestamp)

                    if idx % 100 == 0:
                        self.logger.info(f"Progress: {idx + 1}/{len(self.historical_data)} periods completed")
                        self.logger.debug(f"Current balances: {self.current_balances}")

            except Exception as e:
                self.logger.error(f"Error during simulation loop: {str(e)}", exc_info=True)
                raise

            # Calculate final metrics
            self._calculate_performance_metrics()
            self._generate_analysis_report()

            self.logger.info("Historical simulation completed")

        except Exception as e:
            self.logger.error(f"Critical error during backtest: {str(e)}", exc_info=True)
            self.logger.error("Backtest aborted - could not download required historical data")
            return  # Exit gracefully instead of re-raising

    async def simulate_time_step(self, current_price: float, timestamp: datetime):
        """Simulate strategy behavior for a single time step"""
        # Get active pair instance (assuming single pair for simplicity)
        pair = next(iter(self.strategy.active_positions.values()))

        # Run strategy's order processing
        await self.strategy.process_order_updates(None, current_price)

        # Check and simulate order fills
        filled_orders = self.simulate_order_fills(pair, current_price)

        # Handle filled orders
        for order in filled_orders:
            self._process_simulated_fill(order, timestamp)

        # Check for rebalancing needs
        if self.strategy.needs_rebalance(pair):
            await self.strategy.rebalance_position(pair, self.strategy.active_positions[pair.token_pair])

    def simulate_order_fills(self, pair, current_price: float) -> List[dict]:
        """Determine which orders would be filled at current market price"""
        filled = []
        symbol = pair.token_pair

        if symbol not in self.strategy.order_grids:
            return filled

        for order in self.strategy.order_grids[symbol]['active_orders']:
            if order['type'] == 'buy' and current_price <= order['price']:
                filled.append(order)
            elif order['type'] == 'sell' and current_price >= order['price']:
                filled.append(order)

        return filled

    def _process_simulated_fill(self, order: dict, timestamp: datetime):
        """Update balances and track metrics for filled orders"""
        # Extract order details
        maker_amt = order['maker_size']
        taker_amt = order['taker_size']
        maker_token = order['maker']
        taker_token = order['taker']
        fee = order.get('fee', 0)

        # Update balances
        self.current_balances[maker_token] -= maker_amt
        self.current_balances[taker_token] += taker_amt - fee

        # Record trade
        trade = {
            'timestamp': timestamp,
            'pair': f"{maker_token}/{taker_token}",
            'side': 'sell' if order['type'] == 'sell' else 'buy',
            'price': order['price'],
            'size': maker_amt,
            'fee': fee,
            'taker_received': taker_amt - fee
        }
        self.order_history.append(trade)
        self.fee_history.append(fee)

    def _reset_simulation_state(self):
        """Initialize fresh simulation tracking"""
        self.order_history = []
        self.price_history = []
        self.inventory_history = []
        self.fee_history = []
        self.metrics = {}

    def _record_simulation_state(self, timestamp: datetime):
        """Capture snapshot of current state"""
        self.inventory_history.append({
            'timestamp': timestamp,
            'balances': self.current_balances.copy(),
            'portfolio_value': self._calculate_portfolio_value()
        })

    def _calculate_portfolio_value(self) -> float:
        """Calculate total portfolio value in quote currency"""
        # For simplicity, assume first pair's quote currency
        if not self.strategy.active_positions:
            return 0

        pair = next(iter(self.strategy.active_positions.values()))
        quote_token = pair.token_pair.split('/')[1]
        return sum(
            amt * self._get_current_price_for_asset(token, quote_token)
            for token, amt in self.current_balances.items()
        )

    def _get_current_price_for_asset(self, base: str, quote: str) -> float:
        """Get current price of base/quote pair"""
        if base == quote:
            return 1.0
        # For simplicity, assume direct conversion
        return self.price_history[-1] if base + '/' + quote == next(iter(self.strategy.active_positions)) else 1 / \
                                                                                                               self.price_history[
                                                                                                                   -1]

    def _calculate_performance_metrics(self):
        """Calculate key performance indicators"""
        if not self.inventory_history:
            return

        # Calculate P&L
        initial_value = self.inventory_history[0]['portfolio_value']
        final_value = self.inventory_history[-1]['portfolio_value']

        self.metrics = {
            'total_return_pct': ((final_value - initial_value) / initial_value) * 100,
            'sharpe_ratio': self._calculate_sharpe_ratio(),
            'max_drawdown': self._calculate_max_drawdown(),
            'total_fees': sum(self.fee_history),
            'num_trades': len(self.order_history),
            'volatility': self._calculate_volatility(),
            'win_rate': self._calculate_win_rate()
        }

    def _calculate_sharpe_ratio(self, risk_free_rate: float = 0.0) -> float:
        """Calculate annualized Sharpe ratio"""
        returns = np.diff([iv['portfolio_value'] for iv in self.inventory_history])
        if len(returns) < 2:
            return 0.0
        excess_returns = returns - risk_free_rate
        return np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(365 * 24)

    def _calculate_max_drawdown(self) -> float:
        """Calculate maximum drawdown during simulation"""
        values = [iv['portfolio_value'] for iv in self.inventory_history]
        peak = values[0]
        max_dd = 0.0

        for value in values:
            if value > peak:
                peak = value
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd

        return max_dd

    def _calculate_volatility(self) -> float:
        """Calculate annualized portfolio volatility"""
        returns = np.diff([iv['portfolio_value'] for iv in self.inventory_history])
        if len(returns) < 2:
            return 0.0
        return np.std(returns) * np.sqrt(365 * 24)

    def _calculate_win_rate(self) -> float:
        """Calculate percentage of profitable trades"""
        if not self.order_history:
            return 0.0
        profitable = sum(1 for t in self.order_history if t['taker_received'] > t['size'] * t['price'])
        return profitable / len(self.order_history)

    def _generate_analysis_report(self):
        """Generate summary report of simulation results"""
        report = f"""
        Range Maker Backtest Report
        ---------------------------
        Simulation Period: {self.historical_data['timestamp'].iloc[0]} to {self.historical_data['timestamp'].iloc[-1]}
        Total Return: {self.metrics['total_return_pct']:.2f}%
        Sharpe Ratio: {self.metrics['sharpe_ratio']:.2f}
        Max Drawdown: {self.metrics['max_drawdown'] * 100:.2f}%
        Volatility: {self.metrics['volatility'] * 100:.2f}%
        Total Fees Paid: {self.metrics['total_fees']:.6f}
        Trades Executed: {self.metrics['num_trades']}
        Win Rate: {self.metrics['win_rate'] * 100:.2f}%
        """
        self.logger.info(report)

    def plot_pnl(self):
        """Plot portfolio value over time"""
        values = [iv['portfolio_value'] for iv in self.inventory_history]
        timestamps = [iv['timestamp'] for iv in self.inventory_history]

        plt.figure(figsize=(12, 6))
        plt.plot(timestamps, values)
        plt.title("Portfolio Value Over Time")
        plt.xlabel("Time")
        plt.ylabel("Portfolio Value (Quote)")
        plt.grid(True)
        plt.show()

    def plot_price_with_orders(self):
        """Plot price history with order entry points"""
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

    async def optimize_parameters(self, param_grid: Dict[str, list], cv=5):
        """Perform grid search optimization of strategy parameters"""
        # Implementation for parameter optimization would go here
        pass
