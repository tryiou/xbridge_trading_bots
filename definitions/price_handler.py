import asyncio
import time
from typing import TYPE_CHECKING

import ccxt

from definitions.constants import CCXT_PRICE_REFRESH_INTERVAL
from definitions.price_update_handler import PriceUpdateHandler
from definitions.token import Token

if TYPE_CHECKING:
    from definitions.config_manager import ConfigManager
    from definitions.main_controller import MainController
    from strategies.base_strategy import BaseStrategy


class PriceHandler:
    """Handles updating token prices from CEX sources."""

    def __init__(
            self,
            main_controller: "MainController",
            loop: asyncio.AbstractEventLoop,
            price_update_handler: PriceUpdateHandler | None = None,
    ) -> None:
        """
        Initialize PriceHandler.

        Args:
            main_controller: Reference to MainController
            loop: Asyncio event loop
            price_update_handler: Handler for updating individual token prices
        """
        self.tokens_dict: dict[str, Token] = main_controller.tokens_dict
        self.ccxt_i: ccxt.Exchange = main_controller.ccxt_i
        self.config_manager: ConfigManager = main_controller.config_manager
        self.main_controller: MainController = main_controller
        self.loop: asyncio.AbstractEventLoop = loop
        self.ccxt_price_timer: float | None = None
        self.shutdown_event: asyncio.Event = main_controller.shutdown_event
        self.price_update_handler = price_update_handler or PriceUpdateHandler(
            self.config_manager.ccxt_manager, self.config_manager
        )

    async def update_ccxt_prices(self) -> None:
        """
        Update CEX prices if strategy requires it and refresh interval elapsed.
        """
        strategy_instance: BaseStrategy = self.config_manager.strategy_instance
        if not strategy_instance.should_update_cex_prices():
            self.config_manager.general_log.debug(
                "Strategy does not require CEX price updates."
            )
            return

        now: float = time.time()
        if (
                self.ccxt_price_timer is None
                or now - self.ccxt_price_timer > CCXT_PRICE_REFRESH_INTERVAL
        ):
            try:
                await self._fetch_and_update_prices()
                self.ccxt_price_timer = now
            except Exception as e:
                await self.config_manager.error_handler.handle_async(
                    e, context={"stage": "price_update"}
                )

    async def _fetch_and_update_prices(self) -> None:
        """Fetch ticker data from CEX and update token prices."""
        custom_coins: set[str] = set(
            vars(self.config_manager.config_coins.usd_ticker_custom).keys()
        )
        keys: list[str] = [
            self._construct_key(token)
            for token in self.tokens_dict
            if token not in custom_coins
        ]

        try:
            tickers: dict = (
                await self.config_manager.ccxt_manager.ccxt_call_fetch_tickers(
                    self.ccxt_i, keys
                )
            )
            await self._update_token_prices(tickers)
        except Exception as e:
            await self.config_manager.error_handler.handle_async(
                e, context={"stage": "fetch_cex_tickers"}
            )

    def _construct_key(self, token: str) -> str:
        """Construct symbol string for CEX API."""
        return f"{token}/USDT" if token == "BTC" else f"{token}/BTC"

    async def _update_token_prices(self, tickers: dict) -> None:
        """
        Update token prices from ticker data and custom coin configurations.

        Args:
            tickers: Dictionary of symbol to ticker data
        """
        lastprice_string: str = self._get_last_price_string()
        # BTC first, then others
        symbols_to_update: list[tuple[str, Token]] = sorted(
            self.tokens_dict.items(), key=lambda item: (item[0] != "BTC", item[0])
        )

        for token_symbol, token_data in symbols_to_update:
            if self.shutdown_event.is_set():
                return
            symbol: str = (
                f"{token_data.symbol}/USDT"
                if token_data.symbol == "BTC"
                else f"{token_data.symbol}/BTC"
            )
            is_custom_coin: bool = hasattr(
                self.config_manager.config_coins.usd_ticker_custom, token_symbol
            )
            if not is_custom_coin and symbol in self.ccxt_i.symbols:
                try:
                    self._update_token_price(
                        tickers, symbol, lastprice_string, token_data
                    )
                except Exception as e:
                    await self.config_manager.error_handler.handle_async(
                        e, context={"token": token_symbol, "symbol": symbol}
                    )

        # Process custom coins
        custom_tokens: set[str] = set(
            vars(self.config_manager.config_coins.usd_ticker_custom)
        )
        for token in custom_tokens:
            if self.main_controller.shutdown_event.is_set():
                return
            if token in self.tokens_dict:
                try:
                    await self.price_update_handler.update(self.tokens_dict[token].cex)
                except Exception as e:
                    await self.config_manager.error_handler.handle_async(
                        e, context={"token": token, "type": "custom"}
                    )

    def _get_last_price_string(self) -> str:
        """Get exchange-specific field name for last price."""
        exchange_map: dict[str, str] = {"kucoin": "last", "binance": "lastPrice"}
        return exchange_map.get(self.config_manager.ccxt_manager.my_ccxt.id, "lastTradeRate")

    def _update_token_price(
            self, tickers: dict, symbol: str, price_key: str, token_data: Token
    ) -> None:
        """
        Update a token's price from ticker data.

        Args:
            tickers: Dictionary of symbol to ticker data
            symbol: Trading symbol to look up
            price_key: Exchange-specific field containing last price
            token_data: Token instance to update
        """
        if symbol in tickers:
            last_price: float = float(tickers[symbol]["info"][price_key])
            if token_data.symbol == "BTC":
                token_data.cex.usd_price = last_price
                token_data.cex.cex_price = 1.0
            else:
                token_data.cex.cex_price = last_price
                btc_usd: float | None = self.tokens_dict["BTC"].cex.usd_price
                token_data.cex.usd_price = last_price * btc_usd if btc_usd else None
        else:
            self.config_manager.general_log.warning(
                f"Missing symbol in tickers: {symbol}"
            )
            token_data.cex.cex_price = None
            token_data.cex.usd_price = None
