"""
Refactored Range Maker Backtester

Key improvements:
1. Proper integration with refactored strategy
2. DRY and SOC principles applied
3. Modular architecture with dedicated classes
4. Cleaner metrics reporting
5. Better error handling and logging
6. Simplified animation system
"""

import asyncio
import copy
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

import matplotlib
matplotlib.use('Agg')
import matplotlib.animation as animation
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from definitions.logger import setup_logging
from strategies.range_maker_strategy import RangeMakerStrategy, RangeConfig


class BacktestMode(Enum):
    """Backtesting execution modes."""
    OHLC = "ohlc"  # Use OHLC data for more realistic fills
    CLOSE_ONLY = "close_only"  # Use only close prices


@dataclass
class BacktestConfig:
    """Configuration for backtesting parameters."""
    period: str = "3mo"
    timeframe: str = "1d"
    mode: BacktestMode = BacktestMode.OHLC
    animate: bool = False
    log_level: int = logging.INFO
    log_trades: bool = True


@dataclass 
class TradeRecord:
    """Record of a single trade execution."""
    timestamp: datetime
    pair: str
    side: str  # 'buy' or 'sell'
    price: float
    base_amount: float
    quote_amount: float
    order_id: str = ""


@dataclass
class PortfolioSnapshot:
    """Snapshot of portfolio state at a point in time."""
    timestamp: datetime
    price: float
    balances: Dict[str, float]
    portfolio_value: float
    buy_hold_value: float
    impermanent_loss: float


@dataclass
class BacktestMetrics:
    """Comprehensive backtesting performance metrics."""
    # Basic metrics
    initial_portfolio_value: float = 0.0
    final_portfolio_value: float = 0.0
    total_return_pct: float = 0.0
    
    # Dual view metrics
    initial_portfolio_value_asset1: float = 0.0
    final_portfolio_value_asset1: float = 0.0
    total_return_pct_asset1: float = 0.0
    initial_portfolio_value_asset2: float = 0.0
    final_portfolio_value_asset2: float = 0.0
    total_return_pct_asset2: float = 0.0
    
    # Price movement
    initial_price: float = 0.0
    final_price: float = 0.0
    price_return_pct: float = 0.0
    
    # Trading metrics
    num_trades: int = 0
    total_fees: float = 0.0
    executed_orders: List[TradeRecord] = field(default_factory=list)
    
    # Risk metrics
    max_drawdown: float = 0.0
    volatility: float = 0.0
    sharpe_ratio: float = 0.0
    
    # IL metrics
    final_impermanent_loss: float = 0.0
    max_impermanent_loss: float = 0.0
    avg_impermanent_loss: float = 0.0
    
    # Comparison metrics
    buy_hold_return_pct: float = 0.0
    outperformance_pct: float = 0.0
    
    # Store snapshots for detailed reporting
    initial_snapshot: Optional[PortfolioSnapshot] = None
    final_snapshot: Optional[PortfolioSnapshot] = None


class DataManager:
    """Handles historical data loading and downloading."""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
    
    def get_data_file_path(self, pair: str, period: str, timeframe: str) -> Path:
        """Generate data file path."""
        safe_pair = pair.replace('/', '_')
        return Path(__file__).parent / f"{safe_pair}_historical_data_{period}_{timeframe}.csv"
    
    async def load_or_download_data(self, pair: str, config: BacktestConfig) -> pd.DataFrame:
        """Load data from file or download if missing."""
        file_path = self.get_data_file_path(pair, config.period, config.timeframe)
        
        if file_path.exists():
            self.logger.info(f"Loading data from {file_path}")
            return self._load_csv_data(file_path)
        else:
            self.logger.info(f"Data file not found, downloading for {pair}")
            await self._download_data(pair, file_path, config)
            return self._load_csv_data(file_path)
    
    def _load_csv_data(self, file_path: Path) -> pd.DataFrame:
        """Load and process CSV data."""
        try:
            data = pd.read_csv(file_path, parse_dates=['date'])
            data.rename(columns={'date': 'timestamp'}, inplace=True)
            
            if 'close' not in data.columns:
                raise ValueError("Data file must include 'close' column")
            
            # Ensure OHLC columns exist
            for col in ['open', 'high', 'low']:
                if col not in data.columns:
                    data[col] = data['close']
            
            # Resample and clean
            data = data.set_index('timestamp').resample('1D').bfill()
            data = data.bfill().ffill()
            data = data.reset_index()
            
            self.logger.info(f"Loaded {len(data)} data points")
            return data
            
        except Exception as e:
            self.logger.error(f"Error loading data: {e}")
            raise
    
    async def _download_data(self, pair: str, file_path: Path, config: BacktestConfig):
        """Download historical data using yfinance."""
        try:
            import yfinance as yf
        except ImportError:
            raise ImportError("yfinance required for data download: pip install yfinance")
        
        if "/" not in pair:
            raise ValueError(f"Invalid pair format: {pair}. Must be BASE/QUOTE")
        
        base_token, quote_token = pair.split("/")
        base_sym = f"{base_token}-USD"
        quote_sym = f"{quote_token}-USD"
        
        try:
            self.logger.info(f"Downloading {base_sym} and {quote_sym}")
            base_data = yf.download(base_sym, period=config.period, interval=config.timeframe, progress=False, auto_adjust=True)
            quote_data = yf.download(quote_sym, period=config.period, interval=config.timeframe, progress=False, auto_adjust=True)
            
            # Create synthetic pair data
            df = self._create_synthetic_pair_data(base_data, quote_data)
            df.to_csv(file_path, index=False)
            self.logger.info(f"Data saved to {file_path}")
            
        except Exception as e:
            self.logger.error(f"Download failed: {e}")
            raise
    
    def _create_synthetic_pair_data(self, base_data: pd.DataFrame, quote_data: pd.DataFrame) -> pd.DataFrame:
        """Create synthetic pair OHLCV data."""
        # Align data
        start_date = min(base_data.index.min(), quote_data.index.min())
        end_date = max(base_data.index.max(), quote_data.index.max())
        date_range = pd.date_range(start_date, end_date)
        
        # Reindex and fill missing values using proper methods
        base_data = base_data.reindex(date_range)
        quote_data = quote_data.reindex(date_range)
        
        # Forward fill then backfill
        base_data = base_data.ffill().bfill()
        quote_data = quote_data.ffill().bfill()
        
        # Calculate pair prices
        df = pd.DataFrame(index=date_range)
        
        # Convert to numpy arrays for safe division
        base_open = base_data['Open'].to_numpy()
        base_high = base_data['High'].to_numpy()
        base_low = base_data['Low'].to_numpy()
        base_close = base_data['Close'].to_numpy()
        
        quote_open = quote_data['Open'].to_numpy()
        quote_high = quote_data['High'].to_numpy()
        quote_low = quote_data['Low'].to_numpy()
        quote_close = quote_data['Close'].to_numpy()
        
        # Calculate pair prices using numpy arrays
        df['open'] = base_open / quote_open
        df['high'] = base_high / quote_low  # high of base / low of quote
        df['low'] = base_low / quote_high   # low of base / high of quote
        df['close'] = base_close / quote_close
        df['volume'] = 0
        
        df.reset_index(inplace=True)
        df.rename(columns={'index': 'date'}, inplace=True)
        return df


class PortfolioTracker:
    """Tracks portfolio state and calculates metrics."""
    
    def __init__(self, initial_balances: Dict[str, float], pair: str):
        self.initial_balances = initial_balances.copy()
        self.current_balances = initial_balances.copy()
        self.pair = pair
        self.base_token, self.quote_token = pair.split('/')
        
        self.snapshots: List[PortfolioSnapshot] = []
        self.trades: List[TradeRecord] = []
    
    def record_snapshot(self, timestamp: datetime, price: float):
        """Record portfolio state snapshot."""
        portfolio_value = self._calculate_portfolio_value(price)
        buy_hold_value = self._calculate_buy_hold_value(price)
        
        snapshot = PortfolioSnapshot(
            timestamp=timestamp,
            price=price,
            balances=self.current_balances.copy(),
            portfolio_value=portfolio_value,
            buy_hold_value=buy_hold_value,
            impermanent_loss=buy_hold_value - portfolio_value
        )
        self.snapshots.append(snapshot)
    
    def execute_trade(self, order: Dict[str, Any], timestamp: datetime) -> Optional[TradeRecord]:
        """Execute trade and update balances."""
        # Check sufficient balance
        maker_token = order['maker']
        maker_size = order['maker_size']
        
        if self.current_balances.get(maker_token, 0) < maker_size:
            return None
        
        # Update balances
        self.current_balances[order['maker']] -= order['maker_size']
        self.current_balances[order['taker']] += order['taker_size']
        
        # Create trade record
        if order['type'] == 'buy':
            base_amount = order['taker_size']
            quote_amount = order['maker_size']
        else:
            base_amount = order['maker_size']
            quote_amount = order['taker_size']
        
        trade = TradeRecord(
            timestamp=timestamp,
            pair=self.pair,
            side=order['type'],
            price=order['price'],
            base_amount=base_amount,
            quote_amount=quote_amount,
            order_id=order.get('id', 'sim')
        )
        
        self.trades.append(trade)
        return trade
    
    def _calculate_portfolio_value(self, price: float) -> float:
        """Calculate total portfolio value in quote token terms."""
        base_balance = self.current_balances.get(self.base_token, 0)
        quote_balance = self.current_balances.get(self.quote_token, 0)
        return base_balance * price + quote_balance
    
    def _calculate_buy_hold_value(self, price: float) -> float:
        """Calculate buy-and-hold portfolio value."""
        initial_base = self.initial_balances.get(self.base_token, 0)
        initial_quote = self.initial_balances.get(self.quote_token, 0)
        return initial_base * price + initial_quote


class MetricsCalculator:
    """Calculates comprehensive backtesting metrics."""
    
    def __init__(self, portfolio: PortfolioTracker):
        self.portfolio = portfolio
    
    def calculate_metrics(self) -> BacktestMetrics:
        """Calculate all performance metrics."""
        if not self.portfolio.snapshots:
            return BacktestMetrics()
        
        initial_snapshot = self.portfolio.snapshots[0]
        final_snapshot = self.portfolio.snapshots[-1]
        
        metrics = BacktestMetrics()
        
        # Store snapshots for balance access
        metrics.initial_snapshot = initial_snapshot
        metrics.final_snapshot = final_snapshot
        
        # Basic metrics
        metrics.initial_portfolio_value = initial_snapshot.portfolio_value
        metrics.final_portfolio_value = final_snapshot.portfolio_value
        metrics.total_return_pct = self._calculate_return_pct(
            initial_snapshot.portfolio_value, final_snapshot.portfolio_value
        )
        
        # Dual view metrics - calculate values in terms of each asset
        base_token, quote_token = self.portfolio.pair.split('/')
        
        # Asset1 (base token) view
        initial_asset1 = (initial_snapshot.balances.get(base_token, 0) + 
                         initial_snapshot.balances.get(quote_token, 0) / initial_snapshot.price)
        final_asset1 = (final_snapshot.balances.get(base_token, 0) + 
                       final_snapshot.balances.get(quote_token, 0) / final_snapshot.price)
        metrics.initial_portfolio_value_asset1 = initial_asset1
        metrics.final_portfolio_value_asset1 = final_asset1
        metrics.total_return_pct_asset1 = self._calculate_return_pct(initial_asset1, final_asset1)
        
        # Asset2 (quote token) view
        initial_asset2 = (initial_snapshot.balances.get(base_token, 0) * initial_snapshot.price + 
                         initial_snapshot.balances.get(quote_token, 0))
        final_asset2 = (final_snapshot.balances.get(base_token, 0) * final_snapshot.price + 
                       final_snapshot.balances.get(quote_token, 0))
        metrics.initial_portfolio_value_asset2 = initial_asset2
        metrics.final_portfolio_value_asset2 = final_asset2
        metrics.total_return_pct_asset2 = self._calculate_return_pct(initial_asset2, final_asset2)
        
        # Price metrics
        metrics.initial_price = initial_snapshot.price
        metrics.final_price = final_snapshot.price
        metrics.price_return_pct = self._calculate_return_pct(
            initial_snapshot.price, final_snapshot.price
        )
        
        # Trading metrics
        metrics.num_trades = len(self.portfolio.trades)
        metrics.executed_orders = self.portfolio.trades.copy()
        
        # Risk metrics
        metrics.max_drawdown = self._calculate_max_drawdown()
        metrics.volatility = self._calculate_volatility()
        metrics.sharpe_ratio = self._calculate_sharpe_ratio()
        
        # IL metrics
        il_values = [s.impermanent_loss for s in self.portfolio.snapshots]
        metrics.final_impermanent_loss = il_values[-1] if il_values else 0
        metrics.max_impermanent_loss = max(il_values) if il_values else 0
        metrics.avg_impermanent_loss = np.mean(il_values) if il_values else 0
        
        # Comparison metrics
        metrics.buy_hold_return_pct = self._calculate_return_pct(
            initial_snapshot.buy_hold_value, final_snapshot.buy_hold_value
        )
        metrics.outperformance_pct = metrics.total_return_pct - metrics.buy_hold_return_pct
        
        return metrics
    
    def _calculate_return_pct(self, initial: float, final: float) -> float:
        """Calculate percentage return."""
        if initial == 0:
            return 0.0
        return ((final - initial) / initial) * 100
    
    def _calculate_max_drawdown(self) -> float:
        """Calculate maximum drawdown."""
        values = [s.portfolio_value for s in self.portfolio.snapshots]
        if len(values) < 2:
            return 0.0
        
        peak = np.maximum.accumulate(values)
        drawdown = (values - peak) / peak
        return np.min(drawdown) * 100
    
    def _calculate_volatility(self) -> float:
        """Calculate annualized volatility."""
        values = [s.portfolio_value for s in self.portfolio.snapshots]
        if len(values) < 2:
            return 0.0
        
        returns = np.diff(values) / values[:-1]
        return np.std(returns) * np.sqrt(365) * 100
    
    def _calculate_sharpe_ratio(self, risk_free_rate: float = 0.0) -> float:
        """Calculate Sharpe ratio."""
        values = [s.portfolio_value for s in self.portfolio.snapshots]
        if len(values) < 2:
            return 0.0
        
        returns = np.diff(values) / values[:-1]
        excess_returns = returns - risk_free_rate / 365
        
        if np.std(excess_returns) == 0:
            return 0.0
        
        return (np.mean(excess_returns) / np.std(excess_returns)) * np.sqrt(365)


class FillSimulator:
    """Simulates order fills based on market data."""
    
    def __init__(self, mode: BacktestMode = BacktestMode.OHLC):
        self.mode = mode
    
    def get_fills(self, orders: List[Dict[str, Any]], market_data: Dict[str, float]) -> List[Dict[str, Any]]:
        """Determine which orders would be filled."""
        if self.mode == BacktestMode.OHLC:
            return self._get_ohlc_fills(orders, market_data)
        else:
            return self._get_close_fills(orders, market_data)
    
    def _get_ohlc_fills(self, orders: List[Dict[str, Any]], data: Dict[str, float]) -> List[Dict[str, Any]]:
        """Simulate fills using only high and low prices from historical data."""
        filled = []
        low, high = data['low'], data['high']
        
        for order in orders:
            if order.get('status') == 'filled':
                continue
                
            # For buy orders: fill if the low price is <= our order price
            # This means the price reached our buy limit or below during this period
            if order['type'] == 'buy' and low <= order['price']:
                # Fill at our limit price (since we're placing limit orders)
                # This is more realistic - we get filled at our specified price, not better
                fill_price = order['price']
                filled_order = order.copy()
                filled_order['price'] = fill_price
                filled_order['status'] = 'filled'
                filled.append(filled_order)
            # For sell orders: fill if the high price is >= our order price
            # This means the price reached our sell limit or above during this period
            elif order['type'] == 'sell' and high >= order['price']:
                # Fill at our limit price
                fill_price = order['price']
                filled_order = order.copy()
                filled_order['price'] = fill_price
                filled_order['status'] = 'filled'
                filled.append(filled_order)
        
        return filled
    
    def _get_close_fills(self, orders: List[Dict[str, Any]], data: Dict[str, float]) -> List[Dict[str, Any]]:
        """Simulate fills using only high and low prices (not close)."""
        # Since we're only using high and low, delegate to ohlc method
        # This ensures consistency in fill logic
        return self._get_ohlc_fills(orders, data)


class ReportGenerator:
    """Generates formatted performance reports."""
    
    def __init__(self, metrics: BacktestMetrics, pair: str, config: BacktestConfig):
        self.metrics = metrics
        self.pair = pair
        self.config = config
        self.base_token, self.quote_token = pair.split('/')
    
    def generate_report(self) -> str:
        """Generate comprehensive performance report."""
        # Get initial and final balances
        if self.metrics.executed_orders:
            initial_snapshot = self.metrics.executed_orders[0]
            final_snapshot = self.metrics.executed_orders[-1]
        else:
            # Fallback to portfolio snapshots if no trades
            initial_snapshot = self.metrics.executed_orders[0] if self.metrics.executed_orders else None
            final_snapshot = self.metrics.executed_orders[-1] if self.metrics.executed_orders else None
        
        # Get initial and final balances from portfolio snapshots
        if self.metrics.executed_orders and hasattr(self.metrics.executed_orders[0], 'balances'):
            initial_balances = self.metrics.executed_orders[0].balances
            final_balances = self.metrics.executed_orders[-1].balances
        else:
            # Use the first and last portfolio snapshots
            initial_balances = self.metrics.executed_orders[0].balances if self.metrics.executed_orders else {}
            final_balances = self.metrics.executed_orders[-1].balances if self.metrics.executed_orders else {}
        
        # Balance comparison section
        balance_comparison = self._generate_balance_comparison(initial_balances, final_balances)
        
        # Main report
        report = f"""
╔════════════════════════════════════════════════════════════════════════════════╗
║                     RANGE MAKER BACKTEST RESULTS - {self.pair:<12}                ║
╠════════════════════════════════════════════════════════════════════════════════╣
║ SIMULATION PARAMETERS                                                          ║
║   Period: {self.config.period:<10} │ Timeframe: {self.config.timeframe:<6} │ Mode: {self.config.mode.value:<12}    ║
║   Trades Executed: {self.metrics.num_trades:<6}                                                  ║
║                                                                                ║
{balance_comparison}
║ PORTFOLIO PERFORMANCE                                                          ║
║   Initial Value: {self.metrics.initial_portfolio_value:>12.6f} {self.quote_token}                           ║
║   Final Value:   {self.metrics.final_portfolio_value:>12.6f} {self.quote_token}                           ║
║   Total Return:  {self.metrics.total_return_pct:>12.2f}%                                    ║
║                                                                                ║
║ PRICE MOVEMENT                                                                 ║
║   Initial Price: {self.metrics.initial_price:>12.6f} {self.quote_token}/{self.base_token}                        ║
║   Final Price:   {self.metrics.final_price:>12.6f} {self.quote_token}/{self.base_token}                        ║
║   Price Return:  {self.metrics.price_return_pct:>12.2f}%                                    ║
║                                                                                ║
║ RISK METRICS                                                                   ║
║   Max Drawdown:  {self.metrics.max_drawdown:>12.2f}% (Largest peak-to-trough decline)          ║
║   Volatility:    {self.metrics.volatility:>12.2f}% (Annualized price fluctuation)              ║
║   Sharpe Ratio:  {self.metrics.sharpe_ratio:>12.4f} (Risk-adjusted return)                     ║
║                                                                                ║
║ IMPERMANENT LOSS ANALYSIS                                                      ║
║   Final IL:      {self.metrics.final_impermanent_loss:>12.6f} {self.quote_token} (vs buy & hold)           ║
║   Max IL:        {self.metrics.max_impermanent_loss:>12.6f} {self.quote_token} (worst case)               ║
║   Average IL:    {self.metrics.avg_impermanent_loss:>12.6f} {self.quote_token}                           ║
║   IL Explained:  Loss from providing liquidity vs holding assets               ║
║                                                                                ║
║ STRATEGY VS BUY & HOLD                                                         ║
║   Buy & Hold Return: {self.metrics.buy_hold_return_pct:>8.2f}%                                    ║
║   Strategy Return:   {self.metrics.total_return_pct:>8.2f}%                                    ║
║   Outperformance:    {self.metrics.outperformance_pct:>8.2f}%                                    ║
║                                                                                ║
║ METRICS EXPLANATION:                                                            ║
║   • Max Drawdown: Maximum loss from a peak to a trough                         ║
║   • Volatility: How much portfolio value fluctuates over time                  ║
║   • Sharpe Ratio: Return per unit of risk (higher is better)                   ║
║   • Impermanent Loss: Difference between providing liquidity vs holding        ║
║                                                                                ║
╚════════════════════════════════════════════════════════════════════════════════╝
"""
        return report
    
    def _generate_balance_comparison(self, initial_balances: Dict[str, float], 
                                   final_balances: Dict[str, float]) -> str:
        """Generate balance comparison section."""
        section = "║ BALANCE COMPARISON                                                              ║\n"
        
        # Add base token
        initial_base = initial_balances.get(self.base_token, 0)
        final_base = final_balances.get(self.base_token, 0)
        base_change = final_base - initial_base
        base_change_pct = (base_change / initial_base * 100) if initial_base != 0 else 0
        
        section += f"║   {self.base_token}: {initial_base:>12.6f} → {final_base:>12.6f} ({base_change:>+12.6f}, {base_change_pct:>+6.2f}%) ║\n"
        
        # Add quote token
        initial_quote = initial_balances.get(self.quote_token, 0)
        final_quote = final_balances.get(self.quote_token, 0)
        quote_change = final_quote - initial_quote
        quote_change_pct = (quote_change / initial_quote * 100) if initial_quote != 0 else 0
        
        section += f"║   {self.quote_token}: {initial_quote:>12.6f} → {final_quote:>12.6f} ({quote_change:>+12.6f}, {quote_change_pct:>+6.2f}%) ║\n"
        section += "║                                                                                ║"
        
        return section
        
    def generate_report(self) -> str:
        """Generate comprehensive performance report."""
        # Get initial and final balances from snapshots
        if self.metrics.initial_snapshot and self.metrics.final_snapshot:
            initial_balances = self.metrics.initial_snapshot.balances
            final_balances = self.metrics.final_snapshot.balances
        else:
            initial_balances = {}
            final_balances = {}
        
        # Balance comparison section
        balance_comparison = self._generate_balance_comparison(initial_balances, final_balances)
        
        # Main report
        report = f"""
╔════════════════════════════════════════════════════════════════════════════════╗
║                     RANGE MAKER BACKTEST RESULTS - {self.pair:<12}                ║
╠════════════════════════════════════════════════════════════════════════════════╣
║ SIMULATION PARAMETERS                                                          ║
║   Period: {self.config.period:<10} │ Timeframe: {self.config.timeframe:<6} │ Mode: {self.config.mode.value:<12}    ║
║   Trades Executed: {self.metrics.num_trades:<6}                                                  ║
║                                                                                ║
{balance_comparison}
║ PORTFOLIO PERFORMANCE                                                          ║
║   Initial Value: {self.metrics.initial_portfolio_value:>12.6f} {self.quote_token}                           ║
║   Final Value:   {self.metrics.final_portfolio_value:>12.6f} {self.quote_token}                           ║
║   Total Return:  {self.metrics.total_return_pct:>12.2f}%                                    ║
║                                                                                ║
║ PRICE MOVEMENT                                                                 ║
║   Initial Price: {self.metrics.initial_price:>12.6f} {self.quote_token}/{self.base_token}                        ║
║   Final Price:   {self.metrics.final_price:>12.6f} {self.quote_token}/{self.base_token}                        ║
║   Price Return:  {self.metrics.price_return_pct:>12.2f}%                                    ║
║                                                                                ║
║ RISK METRICS                                                                   ║
║   Max Drawdown:  {self.metrics.max_drawdown:>12.2f}% (Largest peak-to-trough decline)          ║
║   Volatility:    {self.metrics.volatility:>12.2f}% (Annualized price fluctuation)              ║
║   Sharpe Ratio:  {self.metrics.sharpe_ratio:>12.4f} (Risk-adjusted return)                     ║
║                                                                                ║
║ IMPERMANENT LOSS ANALYSIS                                                      ║
║   Final IL:      {self.metrics.final_impermanent_loss:>12.6f} {self.quote_token} (vs buy & hold)           ║
║   Max IL:        {self.metrics.max_impermanent_loss:>12.6f} {self.quote_token} (worst case)               ║
║   Average IL:    {self.metrics.avg_impermanent_loss:>12.6f} {self.quote_token}                           ║
║   IL Explained:  Loss from providing liquidity vs holding assets               ║
║                                                                                ║
║ STRATEGY VS BUY & HOLD                                                         ║
║   Buy & Hold Return: {self.metrics.buy_hold_return_pct:>8.2f}%                                    ║
║   Strategy Return:   {self.metrics.total_return_pct:>8.2f}%                                    ║
║   Outperformance:    {self.metrics.outperformance_pct:>8.2f}%                                    ║
║                                                                                ║
║ METRICS EXPLANATION:                                                            ║
║   • Max Drawdown: Maximum loss from a peak to a trough                         ║
║   • Volatility: How much portfolio value fluctuates over time                  ║
║   • Sharpe Ratio: Return per unit of risk (higher is better)                   ║
║   • Impermanent Loss: Difference between providing liquidity vs holding        ║
║                                                                                ║
╚════════════════════════════════════════════════════════════════════════════════╝
"""
        return report


class AnimationGenerator:
    """Generates animated visualizations of the backtesting process."""
    
    def __init__(self, portfolio: PortfolioTracker, strategy_data: List[Dict]):
        self.portfolio = portfolio
        self.strategy_data = strategy_data
        self.pair = portfolio.pair
        self.base_token, self.quote_token = self.pair.split('/')
        # Store initial balances to always show them
        self.initial_balances = portfolio.initial_balances.copy()
    
    def create_animation(self, save_path: str):
        """Create animated order book visualization."""
        if not self.strategy_data:
            return
        
        # Create figure with two y-axes
        fig, ax1 = plt.subplots(figsize=(14, 8))  # Reduced height to 8 inches
        # Reduce bottom margin to minimize wasted space
        fig.subplots_adjust(top=0.9, bottom=0.1)
        ax2 = ax1.twinx()
        
        # Animation function
        def animate_frame(frame_data):
            return self._update_frame(ax1, ax2, frame_data)
        
        # Create animation
        anim = animation.FuncAnimation(
            fig, animate_frame, frames=self.strategy_data,
            interval=1000, blit=False, repeat=False
        )
        
        # Save animation
        try:
            # Use ffmpeg writer for MP4 format
            ffmpeg_writer = animation.FFMpegWriter(fps=1)
            anim.save(save_path, writer=ffmpeg_writer)
        except Exception as e:
            print(f"Animation save failed: {e}")
            print("Please install ffmpeg: sudo apt install ffmpeg")
    
    def _setup_plot(self, ax1, ax2):
        """Setup initial plot elements."""
        ax1.set_xlabel(f"Price ({self.base_token}/{self.quote_token})")
        ax1.grid(True)
        
        # Remove default Y axis labels
        ax1.set_ylabel("")
        ax2.set_ylabel("")
        
        # Add Y axis legends using custom legend approach
        # Left Y axis legend
        left_legend_text = f"BASE {self.base_token} Amount"
        ax1.text(-0.05, 0.5, left_legend_text, transform=ax1.transAxes, 
                rotation=90, verticalalignment='center', fontsize=10)

        # Right Y axis legend
        right_legend_text = f"QUOTE {self.quote_token} Amount"
        ax2.text(1.05, 0.5, right_legend_text, transform=ax2.transAxes, 
                rotation=90, verticalalignment='center', fontsize=10)
      
    def _update_frame(self, ax1, ax2, frame_data):
        """Update frame with current data."""
        ax1.clear()
        ax2.clear()
        self._setup_plot(ax1, ax2)
        
        # Plot current market price
        current_price = frame_data['price']
        ax1.axvline(x=current_price, color='blue', linestyle='--', label='Current Price')
        
        # Plot buy orders
        buy_prices = []
        buy_base_sizes = []
        buy_quote_sizes = []
        for order in frame_data['buy_orders'].values():
            # For buy orders:
            #   base token amount = taker_size
            #   quote token amount = maker_size
            buy_prices.append(order['price'])
            buy_base_sizes.append(order['taker_size'])
            buy_quote_sizes.append(order['maker_size'])
        
        # Plot sell orders
        sell_prices = []
        sell_base_sizes = []
        sell_quote_sizes = []
        for order in frame_data['sell_orders'].values():
            # For sell orders:
            #   base token amount = maker_size
            #   quote token amount = taker_size
            sell_prices.append(order['price'])
            sell_base_sizes.append(order['maker_size'])
            sell_quote_sizes.append(order['taker_size'])
        
        # Plot base token amounts on ax1 (left axis)
        ax1.scatter(buy_prices, buy_base_sizes, color='green', marker='^', label='Buy Base', alpha=0.7)
        ax1.scatter(sell_prices, sell_base_sizes, color='red', marker='v', label='Sell Base', alpha=0.7)
        
        # Plot quote token amounts on ax2 (right axis)
        ax2.scatter(buy_prices, buy_quote_sizes, color='blue', marker='>', label='Buy Quote', alpha=0.7)
        ax2.scatter(sell_prices, sell_quote_sizes, color='orange', marker='<', label='Sell Quote', alpha=0.7)
        
        # Combined title with strategy and date
        ax1.set_title(f"Range Maker Strategy - {self.pair} at {frame_data['timestamp'].strftime('%Y-%m-%d')}", fontsize=14)
        
        # Get handles and labels for legend
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        
        # Get the figure
        fig = ax1.figure
        
        # Remove any existing legend and balance text
        if hasattr(self, '_legend'):
            try:
                self._legend.remove()
            except:
                pass
        if hasattr(self, '_balance_text'):
            try:
                self._balance_text.remove()
            except:
                pass
        
        # Place legend in bottom right corner
        self._legend = fig.legend(
            lines1 + lines2, labels1 + labels2,
            loc='lower right',
            bbox_to_anchor=(0.98, 0.02),
            ncol=2,
            fancybox=True,
            shadow=True,
            framealpha=0.7,
            fontsize=10
        )
        
        # Show current balances
        current_base = frame_data['balances'].get(self.base_token, 0)
        current_quote = frame_data['balances'].get(self.quote_token, 0)
        balance_text = f"Current Balances: {self.base_token}: {current_base:.6f}, {self.quote_token}: {current_quote:.6f}"
        
        self._balance_text = fig.text(
            0.02, 0.02, balance_text,
            ha='left', va='bottom', fontsize=10,
            bbox=dict(facecolor='white', alpha=0.7, edgecolor='none', boxstyle='round,pad=0.3')
        )
        
        # Adjust layout to make space for the legend and balance text
        fig.tight_layout(rect=[0, 0.1, 1, 0.95])


class RangeMakerBacktester:
    """
    Refactored backtester for Range Maker strategy with improved architecture.
    
    Key improvements:
    - Modular design with dedicated classes for different responsibilities
    - Proper integration with refactored strategy
    - Comprehensive metrics calculation
    - Better error handling and logging
    """
    
    def __init__(self, strategy: RangeMakerStrategy, config: BacktestConfig):
        self.strategy = strategy
        self.config = config
        
        # Use the root logger's level
        root_level = logging.getLogger().getEffectiveLevel()
        self.logger = setup_logging(
            name="range_maker_backtester",
            level=root_level,
            console=True
        )
        
        # Initialize components
        self.data_manager = DataManager(self.logger)
        self.fill_simulator = FillSimulator(config.mode)
        
        # State
        self.portfolio: Optional[PortfolioTracker] = None
        self.historical_data: Optional[pd.DataFrame] = None
        self.animation_data: List[Dict] = []
        
        self.logger.info("RangeMakerBacktester initialized")
    
    async def run_backtest(self, pair_cfg: Dict[str, Any], initial_balances: Dict[str, float]) -> BacktestMetrics:
        """Run complete backtest simulation."""
        pair = pair_cfg['pair']
        self.logger.info(f"Starting backtest for {pair}")
        
        try:
            # Load data
            self.historical_data = await self.data_manager.load_or_download_data(pair, self.config)
            
            if self.historical_data.empty:
                self.logger.error("Historical data is empty, cannot set initial midpoint")
                return BacktestMetrics()
            
            # Get initial mid price from first row of historical data
            first_row = self.historical_data.iloc[0]
            initial_mid_price = first_row['close']
            self.logger.info(f"Setting initial midpoint from historical data: {initial_mid_price:.6f}")
            
            # Update pair configuration with actual initial middle price
            pair_cfg = pair_cfg.copy()
            pair_cfg['initial_middle_price'] = initial_mid_price
            
            # Initialize strategy with updated configuration
            self.strategy.initialize_strategy_specifics(**pair_cfg)
            
            # Initialize portfolio
            self.portfolio = PortfolioTracker(initial_balances, pair)
            # Log initial balances to verify they're correct
            self.logger.info(f"Portfolio initialized with balances: {self.portfolio.initial_balances}")
            self.logger.info(f"Current balances: {self.portfolio.current_balances}")
            
            # Setup strategy mock environment
            self._setup_strategy_environment(pair, initial_balances)
            
            # Set current mid price in strategy position
            if pair in self.strategy.positions:
                self.strategy.positions[pair].current_mid_price = initial_mid_price
            else:
                self.logger.error(f"No position configuration for {pair} to set initial midpoint")
            
            # Run simulation
            await self._run_simulation()
            
            # Calculate metrics
            calculator = MetricsCalculator(self.portfolio)
            metrics = calculator.calculate_metrics()
            
            # Generate report
            self._generate_and_log_report(metrics)
            
            return metrics
            
        except Exception as e:
            self.logger.error(f"Backtest failed: {e}", exc_info=True)
            raise
    
    def _setup_strategy_environment(self, pair: str, initial_balances: Dict[str, float]):
        """Setup mock trading environment for strategy."""
        from definitions.pair import Pair
        from definitions.token import Token
        
        # Create mock tokens
        base_token, quote_token = pair.split('/')
        
        # Mock token objects with balance tracking
        token1 = Token(base_token, self.strategy.config_manager)
        token2 = Token(quote_token, self.strategy.config_manager)
        
        # Mock DEX interfaces
        class MockDex:
            def __init__(self, balance: float):
                self.free_balance = balance
        
        # Set initial balances from the provided initial_balances
        token1.dex = MockDex(initial_balances.get(base_token, 0))
        token2.dex = MockDex(initial_balances.get(quote_token, 0))
        self.logger.info(f"Mock DEX balances set: {base_token}={token1.dex.free_balance}, {quote_token}={token2.dex.free_balance}")
        
        # Create pair
        pair_config = {
            'name': pair,
            'min_price': 0,  # Will be set by strategy config
            'max_price': 1000,
            'grid_density': 20
        }
        
        pair_instance = Pair(
            token1=token1,
            token2=token2,
            cfg=pair_config,
            strategy="range_maker",
            config_manager=self.strategy.config_manager
        )
        
        self.strategy.pairs[pair] = pair_instance
    
    async def _run_simulation(self):
        """Run the main simulation loop."""
        pair = self.portfolio.pair
        pair_instance = self.strategy.pairs[pair]
        config = self.strategy.positions.get(pair)
        
        if not config:
            raise ValueError(f"No strategy configuration for pair {pair}")
        
        self.logger.info(f"Running simulation with {len(self.historical_data)} data points")
        
        # Generate initial orders before recording the first frame
        # Process strategy to create initial orders
        self.logger.debug("Processing strategy to generate initial orders...")
        await self.strategy.process_pair_async(pair_instance)
        self.logger.debug(f"Initial orders generated. Active orders: {len(self.strategy.order_grids[pair].active_orders)}")
        
        # Record initial state with orders
        if self.config.animate:
            first_row = self.historical_data.iloc[0]
            initial_timestamp = first_row['timestamp']
            initial_market_data = {
                'open': first_row['open'],
                'high': first_row['high'], 
                'low': first_row['low'],
                'close': first_row['close']
            }
            # Record initial frame with initial balances AND orders
            initial_frame_data = {
                'timestamp': initial_timestamp,
                'price': initial_market_data['close'],
                'buy_orders': copy.deepcopy(self.strategy.order_grids[pair].buy_orders),
                'sell_orders': copy.deepcopy(self.strategy.order_grids[pair].sell_orders),
                'balances': self.portfolio.initial_balances.copy(),
                'trades': 0,
                'base_token': pair_instance.t1.symbol,
                'quote_token': pair_instance.t2.symbol
            }
            self.animation_data.append(initial_frame_data)
            self.logger.info(f"Recorded initial frame with orders and balances: {initial_frame_data['balances']}")
            self.logger.info(f"Buy orders: {len(initial_frame_data['buy_orders'])}, Sell orders: {len(initial_frame_data['sell_orders'])}")
        
        for idx, row in self.historical_data.iterrows():
            timestamp = row['timestamp']
            # Only use high and low prices for fill simulation
            market_data = {
                'high': row['high'], 
                'low': row['low']
            }
            
            # For mid price updates, we need to decide what to use
            # Since we're only supposed to use high and low, let's use their average
            # This is better than using close which we're not supposed to use
            mid_price = (row['high'] + row['low']) / 2
            
            # Simulate fills
            active_orders = self.strategy.order_grids[pair].active_orders
            filled_orders = self.fill_simulator.get_fills(active_orders, market_data)
            
            if filled_orders:
                self.logger.info(f"Found {len(filled_orders)} filled orders at market price range: "
                               f"low={market_data['low']:.6f}, high={market_data['high']:.6f}")
            
            # Log data point processing concisely
            if filled_orders or (idx + 1) % 5 == 0 or idx == 0 or idx == len(self.historical_data) - 1:
                self.logger.debug(f"Data #{idx+1}/{len(self.historical_data)}: {timestamp.date()} | "
                                f"Range: {market_data['low']:.1f}-{market_data['high']:.1f} | "
                                f"Mid: {mid_price:.1f} | "
                                f"Strategy mid: {config.current_mid_price:.1f}")
            
            # Record portfolio snapshot using the mid price
            self.portfolio.record_snapshot(timestamp, mid_price)
            # Only log portfolio when it changes significantly or periodically
            if filled_orders or (idx + 1) % 10 == 0 or idx == len(self.historical_data) - 1:
                snapshot = self.portfolio.snapshots[-1]
                self.logger.debug(f"Portfolio: {snapshot.portfolio_value:.1f} {self.portfolio.quote_token} | "
                                f"Balances: {dict((k, round(v, 2)) for k, v in snapshot.balances.items())}")
            

            
            # Simulate fills
            active_orders = self.strategy.order_grids[pair].active_orders
            filled_orders = self.fill_simulator.get_fills(active_orders, market_data)
            
            if filled_orders:
                self.logger.info(f"Found {len(filled_orders)} filled orders at market price range: "
                               f"low={market_data['low']:.6f}, high={market_data['high']:.6f}")
            
            # Process each filled order sequentially
            for order_idx, order in enumerate(filled_orders):
                # Execute the trade
                trade = self.portfolio.execute_trade(order, timestamp)
                if trade:
                    self.logger.info(f"Trade #{order_idx+1}: {trade.side.upper()} {trade.base_amount:.6f} @ {trade.price:.6f}")
                    # Log order details to verify prices are correct
                    self.logger.debug(f"Order was originally priced at {order.get('original_price', 'N/A')}")
                
                # Mark order as filled
                order['status'] = 'filled'
                
                # Update mock balances
                self._update_mock_balances(pair_instance)
                self.logger.debug(f"Updated mock balances after trade")
                
                # Update mid price to this fill price - CRITICAL: This must be done before regridding
                old_mid_price = config.current_mid_price
                config.current_mid_price = order['price']
                # Also update the strategy's position mid price
                if hasattr(self.strategy, 'positions') and pair in self.strategy.positions:
                    self.strategy.positions[pair].current_mid_price = order['price']
                self.logger.info(f"Updated mid price to fill price: {old_mid_price:.6f} -> {config.current_mid_price:.6f}")
                
                # Regrid after each fill - this will use the updated mid price
                self.logger.debug("Regridding after fill...")
                # Clear existing orders to force regeneration with new mid price
                if pair in self.strategy.order_grids:
                    self.strategy.order_grids[pair].clear()
                await self.strategy.process_pair_async(pair_instance)
                # Don't log here - the strategy already logs the regrid completion
                
                # Update animation data after each fill
                if self.config.animate:
                    # Include token symbols for balance labeling in animation
                    frame_data = {
                        'timestamp': timestamp,
                        'price': config.current_mid_price,
                        'buy_orders': copy.deepcopy(self.strategy.order_grids[pair].buy_orders),
                        'sell_orders': copy.deepcopy(self.strategy.order_grids[pair].sell_orders),
                        'balances': self.portfolio.current_balances.copy(),
                        'trades': 1,
                        'base_token': pair_instance.t1.symbol,
                        'quote_token': pair_instance.t2.symbol
                    }
                    self.animation_data.append(frame_data)
            
            # If no fills occurred, still record a frame to show the current state
            if self.config.animate and not filled_orders:
                frame_data = {
                    'timestamp': timestamp,
                    'price': mid_price,
                    'buy_orders': copy.deepcopy(self.strategy.order_grids[pair].buy_orders),
                    'sell_orders': copy.deepcopy(self.strategy.order_grids[pair].sell_orders),
                    'balances': self.portfolio.current_balances.copy(),
                    'trades': 0,
                    'base_token': pair_instance.t1.symbol,
                    'quote_token': pair_instance.t2.symbol
                }
                self.animation_data.append(frame_data)
        
        self.logger.info("Simulation completed")
    
    def _update_mock_balances(self, pair_instance):
        """Update mock DEX balances to match portfolio state."""
        base_token, quote_token = self.portfolio.pair.split('/')
        pair_instance.t1.dex.free_balance = self.portfolio.current_balances.get(base_token, 0)
        pair_instance.t2.dex.free_balance = self.portfolio.current_balances.get(quote_token, 0)
    
    def _record_animation_frame(self, timestamp: datetime, market_data: Dict, trades: List):
        """Record data for animation frame."""
        if not self.portfolio:
            return
        
        pair = self.portfolio.pair
        grid = self.strategy.order_grids.get(pair)
        
        if grid:
            frame_data = {
                'timestamp': timestamp,
                'price': market_data['close'],
                'buy_orders': copy.deepcopy(grid.buy_orders),
                'sell_orders': copy.deepcopy(grid.sell_orders),
                'balances': self.portfolio.current_balances.copy(),
                'trades': len(trades)
            }
            self.animation_data.append(frame_data)
    
    def _generate_and_log_report(self, metrics: BacktestMetrics):
        """Generate and log the performance report."""
        # Log executed orders first
        if metrics.executed_orders:
            order_log = "\n╔════════════════════════════════════════════════════════════════════════════════╗\n"
            order_log += "║                              EXECUTED ORDERS (CHRONOLOGICAL)                      ║\n"
            order_log += "╠════════════════════════════════════════════════════════════════════════════════╣\n"
            
            for i, trade in enumerate(metrics.executed_orders):
                order_log += f"║ {i+1:3d}. {trade.timestamp.strftime('%Y-%m-%d %H:%M:%S')} | {trade.side.upper():4} | "
                order_log += f"{trade.base_amount:>10.6f} {self.portfolio.base_token} @ {trade.price:>10.6f} {self.portfolio.quote_token}/{self.portfolio.base_token} | "
                order_log += f"Value: {trade.quote_amount:>10.6f} {self.portfolio.quote_token}\n"
            
            order_log += "╚════════════════════════════════════════════════════════════════════════════════╝\n"
            self.logger.info(order_log)
        else:
            self.logger.info("No orders were executed during the backtest")
        
        # Generate and log the main performance report
        generator = ReportGenerator(metrics, self.portfolio.pair, self.config)
        report = generator.generate_report()
        self.logger.info(report)
    
    def save_animation(self, save_path: str):
        """Save animation if data is available."""
        if not self.animation_data or not self.portfolio:
            self.logger.warning("No animation data available")
            return
        
        generator = AnimationGenerator(self.portfolio, self.animation_data)
        generator.create_animation(save_path)
        self.logger.info(f"Animation saved to {save_path}")


# Example usage
async def main():
    """Example backtesting execution."""
    from definitions.config_manager import ConfigManager
    from range_maker_strategy import CurveType, PriceStepType
    
    # Setup strategy
    config_manager = ConfigManager(strategy="range_maker")
    config_manager.initialize(loadxbridgeconf=False)
    
    strategy = RangeMakerStrategy(config_manager)
    
    # Configure strategy position
    strategy.initialize_strategy_specifics(
        pair="LTC/DOGE",
        min_price=400,
        max_price=600,
        grid_density=20,
        curve="linear",
        curve_strength=10.0,
        percent_min_size=0.001,
        initial_middle_price=500.0
    )
    
    # Setup backtest configuration
    backtest_config = BacktestConfig(
        period="3mo",
        timeframe="1d",
        mode=BacktestMode.OHLC,
        animate=True,
        log_level=logging.INFO
    )
    
    # Create and run backtester
    backtester = RangeMakerBacktester(strategy, backtest_config)
    
    pair = "LTC/DOGE"
    initial_balances = {"LTC": 100.0, "DOGE": 100000.0}
    metrics = await backtester.run_backtest(pair, initial_balances)
    
    # Save animation if enabled
    if backtest_config.animate:
        animation_path = f"backtest_animation_{pair.replace('/', '_')}.mp4"
        backtester.save_animation(animation_path)
    
    print(f"Backtest completed. Total return: {metrics.total_return_pct:.2f}%")
    return metrics


if __name__ == "__main__":
    asyncio.run(main())
