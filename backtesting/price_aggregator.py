"""Price aggregation for cross-pair calculation."""

from typing import Optional

import pandas as pd

from .price_feed import PriceFeed


class PriceAggregator:
    """
    Aggregates two trading pairs to create a synthetic pair.

    Example: LTC/DOGE from LTC/USD and DOGE/USD

    LTC/DOGE = (LTC/USD) / (DOGE/USD)
    """

    COMMON_QUOTES = ["USD", "BTC", "ETH", "USDT"]

    def __init__(
        self,
        price_feed: Optional[PriceFeed] = None,
        interval: str = "1h",
        start_date: str = "",
        end_date: str = "",
    ):
        self.price_feed = price_feed or PriceFeed()
        self.interval = interval
        self.start_date = start_date
        self.end_date = end_date

    def get_synthetic_pair(
        self,
        base: str,
        quote: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """
        Calculate synthetic price: base/quote

        If direct pair exists (e.g., BTC/USD): use it
        If not: calculate from common denominators:
            - base/USD and quote/USD
            - base/BTC and quote/BTC
            - etc.
        """
        direct_symbol = f"{base}-{quote}"
        if self._pair_exists(direct_symbol):
            return self._fetch_direct(direct_symbol, start, end)

        inverse_symbol = f"{quote}-{base}"
        if self._pair_exists(inverse_symbol):
            data = self._fetch_direct(inverse_symbol, start, end)
            return self._invert_data(data)

        for common_quote in self.COMMON_QUOTES:
            try:
                result = self._try_aggregate_via_common(
                    base, quote, common_quote, start, end
                )
                if result is not None:
                    return result
            except Exception:
                continue

        raise ValueError(
            f"Could not find price data for {base}/{quote}. "
            f"Tried direct, inverse, and common quotes: {self.COMMON_QUOTES}"
        )

    def _pair_exists(self, symbol: str) -> bool:
        return self.price_feed.pair_exists(
            symbol, self.start_date, self.end_date, self.interval
        )

    def _fetch_direct(
        self,
        symbol: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        return self.price_feed.fetch_prices(symbol, start, end, self.interval)

    def _invert_data(self, data: pd.DataFrame) -> pd.DataFrame:
        inverted = data.copy()

        for col in ["Open", "High", "Low", "Close"]:
            if col in inverted.columns:
                inverted[col] = 1.0 / inverted[col]

        if "Volume" in inverted.columns:
            inverted["Volume"] = data["Volume"]

        return inverted

    def _try_aggregate_via_common(
        self,
        base: str,
        quote: str,
        common_quote: str,
        start: str,
        end: str,
    ) -> Optional[pd.DataFrame]:
        base_symbol = f"{base}-{common_quote}"
        quote_symbol = f"{quote}-{common_quote}"

        if not self._pair_exists(base_symbol) or not self._pair_exists(quote_symbol):
            return None

        base_data = self._fetch_direct(base_symbol, start, end)
        quote_data = self._fetch_direct(quote_symbol, start, end)

        if base_data.empty or quote_data.empty:
            return None

        base_close = self._get_close_column(base_data).squeeze()
        quote_close = self._get_close_column(quote_data).squeeze()

        synthetic_close = (base_close / quote_close).squeeze()

        base_open = (
            base_data["Open"].squeeze() if "Open" in base_data.columns else base_close
        )
        quote_open = (
            quote_data["Open"].squeeze()
            if "Open" in quote_data.columns
            else quote_close
        )

        base_high = (
            base_data["High"].squeeze() if "High" in base_data.columns else base_close
        )
        quote_high = (
            quote_data["High"].squeeze()
            if "High" in quote_data.columns
            else quote_close
        )
        base_low = (
            base_data["Low"].squeeze() if "Low" in base_data.columns else base_close
        )
        quote_low = (
            quote_data["Low"].squeeze() if "Low" in quote_data.columns else quote_close
        )

        result = pd.DataFrame(
            {
                "Open": base_open / quote_open,
                "High": base_high / quote_low,
                "Low": base_low / quote_high,
                "Close": synthetic_close,
                "Volume": (
                    base_data["Volume"].squeeze() + quote_data["Volume"].squeeze()
                )
                / 2
                if "Volume" in base_data.columns and "Volume" in quote_data.columns
                else 0,
            },
            index=base_data.index,
        )

        return result

    def _get_close_column(self, data: pd.DataFrame) -> pd.Series:
        if "Close" in data.columns:
            return data["Close"]
        elif "Adj Close" in data.columns:
            return data["Adj Close"]
        else:
            raise ValueError("Could not find close price in data")
