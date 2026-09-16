import time

from definitions.constants import CCXT_PRICE_REFRESH_INTERVAL
from definitions.errors import OperationalError
from definitions.price_helpers import get_price_field_for_exchange


class PriceUpdateHandler:
    """Deep module for updating CEX token prices.

    Handles fetching ticker data, applying custom price overrides,
    and calculating USD prices based on BTC price.

    Interface:
        update(cex_token) -> None: Update the price of a CexToken.
    """

    def __init__(self, ccxt_manager, config_manager) -> None:
        self.ccxt_manager = ccxt_manager
        self.config_manager = config_manager

    async def update(self, cex_token) -> None:
        """Update the price of a CexToken.

        Args:
            cex_token: The CexToken instance to update.
        """
        if (
            cex_token.cex_price_timer is not None
            and time.time() - cex_token.cex_price_timer <= CCXT_PRICE_REFRESH_INTERVAL
        ):
            return

        token = cex_token.token
        cex_symbol = "BTC/USDT" if token.symbol == "BTC" else f"{token.symbol}/BTC"
        my_ccxt = self.ccxt_manager.my_ccxt if self.config_manager else None
        exchange_id = getattr(my_ccxt, "id", "default") if my_ccxt else "default"
        lastprice_string = get_price_field_for_exchange(exchange_id)

        async def fetch_ticker_async(symbol: str) -> float | None:
            try:
                ticker = await self.ccxt_manager.ccxt_call_fetch_ticker(my_ccxt, symbol)
            except Exception as e:
                await cex_token.token.error_handler.handle_async(
                    e,
                    context={
                        "token": token.symbol,
                        "cex_symbol": symbol,
                        "stage": "fetch_ticker",
                    },
                )
                return None

            if not ticker:
                return None
            try:
                return float(ticker["info"][lastprice_string])
            except (KeyError, TypeError, ValueError) as e:
                await cex_token.token.error_handler.handle_async(
                    e,
                    context={
                        "token": token.symbol,
                        "cex_symbol": symbol,
                        "ticker_response": ticker,
                    },
                )
                return None

        btc_price = self.config_manager.tokens["BTC"].cex.usd_price
        if btc_price is None or btc_price == 0:
            if cex_token.token.error_handler:
                await cex_token.token.error_handler.handle_async(
                    OperationalError(
                        f"BTC price unavailable for {token.symbol} price calculation"
                    ),
                    context={"token": token.symbol},
                )
            cex_token.usd_price, cex_token.cex_price = None, None
            return

        if token.symbol == "BTC":
            cex_token.cex_price, cex_token.usd_price = 1.0, btc_price
            cex_token.cex_price_timer = time.time()
            return

        result = None
        if hasattr(self.config_manager.config_coins, "usd_ticker_custom"):
            custom_tickers = self.config_manager.config_coins.usd_ticker_custom
            if hasattr(custom_tickers, token.symbol):
                custom_price = getattr(custom_tickers, token.symbol)
                try:
                    result = float(custom_price) / btc_price
                except (TypeError, ValueError) as e:
                    await cex_token.token.error_handler.handle_async(
                        e,
                        context={
                            "token": token.symbol,
                            "stage": "update_price",
                            "custom_price": custom_price,
                        },
                    )

        if result is None:
            if hasattr(my_ccxt, "symbols") and cex_symbol in my_ccxt.symbols:
                result = await fetch_ticker_async(cex_symbol)
            else:
                cex_token.usd_price, cex_token.cex_price = None, None
                return

        if result is not None:
            cex_token.cex_price = result
            cex_token.usd_price = result * btc_price
            cex_token.cex_price_timer = time.time()
            btc_price_fmt = format(float(btc_price), ".8f").rstrip("0").rstrip(".")
            cex_token.token.logger.debug(
                "fetch_ticker %s, BTC_PRICE: %s, USD_PRICE: %s, BTC_USD_PRICE: %s",
                token.symbol,
                format(float(cex_token.cex_price), ".8f").rstrip("0").rstrip("."),
                format(float(cex_token.usd_price), ".8f").rstrip("0").rstrip("."),
                btc_price_fmt,
            )
        else:
            cex_token.usd_price, cex_token.cex_price = None, None
