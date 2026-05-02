"""Price data fetching using yfinance."""

import logging
import os

import pandas as pd

logger = logging.getLogger(__name__)


class PriceFeed:
    """Fetches historical price data from yfinance."""

    CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

    def __init__(self):
        self._cache: dict[str, pd.DataFrame] = {}
        os.makedirs(self.CACHE_DIR, exist_ok=True)

    def _get_cache_path(self, symbol: str, start: str, end: str, interval: str) -> str:
        safe_symbol = symbol.replace('-', '_').replace('/', '_')
        filename = f"{safe_symbol}_{start}_{end}_{interval}.parquet"
        return os.path.join(self.CACHE_DIR, filename)

    def _load_from_disk(self, cache_path: str) -> pd.DataFrame | None:
        if os.path.exists(cache_path):
            logger.info("Loading from cache: %s", cache_path)
            return pd.read_parquet(cache_path)
        return None

    def _save_to_disk(self, data: pd.DataFrame, cache_path: str) -> None:
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = [col[0] for col in data.columns]
        data.to_parquet(cache_path)

    def fetch_prices(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str = "1h",
    ) -> pd.DataFrame:
        """
        Fetch historical price data.

        Args:
            symbol: The ticker symbol (e.g., "BTC-USD", "LTC-USD")
            start: Start date in YYYY-MM-DD format
            end: End date in YYYY-MM-DD format
            interval: Data interval (default: "1h")

        Returns:
            DataFrame with columns: Open, High, Low, Close, Volume, Adj Close
        """
        cache_key = f"{symbol}_{start}_{end}_{interval}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        cache_path = self._get_cache_path(symbol, start, end, interval)
        disk_data = self._load_from_disk(cache_path)
        if disk_data is not None:
            self._cache[cache_key] = disk_data
            return disk_data

        try:
            import yfinance as yf

            data = yf.download(
                symbol,
                start=start,
                end=end,
                interval=interval,
                progress=False,
            )

            if data.empty:
                raise ValueError(f"No data returned for {symbol}")

            self._save_to_disk(data, cache_path)
            self._cache[cache_key] = data
            return data

        except ImportError:
            raise ImportError(
                "yfinance is required for backtesting. Install with: pip install yfinance"
            )

    def get_ohlc(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str = "1h",
    ) -> pd.DataFrame:
        """Get OHLCV data (Open, High, Low, Close, Volume)."""
        return self.fetch_prices(symbol, start, end, interval)

    def get_close_prices(
        self,
        symbol: str,
        start: str,
        end: str,
        interval: str = "1h",
    ) -> pd.Series:
        """Get only close prices as a Series."""
        data = self.fetch_prices(symbol, start, end, interval)
        if "Close" in data.columns:
            return data["Close"]
        elif "Adj Close" in data.columns:
            return data["Adj Close"]
        else:
            raise ValueError(f"Could not find close price in data for {symbol}")

    def pair_exists(self, symbol: str, start: str, end: str, interval: str = "1h") -> bool:
        """Check if a trading pair exists on yfinance."""
        try:
            data = self.fetch_prices(symbol, start, end, interval)
            return not data.empty
        except Exception:
            return False
