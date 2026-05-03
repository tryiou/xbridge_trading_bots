import asyncio
import time
from typing import TYPE_CHECKING, Any

import aiohttp
import ccxt

from definitions.balance_manager import BalanceManager
from definitions.pair import Pair
from definitions.price_handler import PriceHandler
from definitions.price_update_handler import PriceUpdateHandler
from definitions.token import Token
from definitions.trading_processor import TradingProcessor

if TYPE_CHECKING:
    from definitions.config_manager import ConfigManager
    from definitions.xbridge_manager import XBridgeManager


class MainController:
    """Main controller class for coordinating trading operations."""

    def __init__(
            self, config_manager: "ConfigManager", loop: asyncio.AbstractEventLoop
    ) -> None:
        """
        Initialize MainController.

        Args:
            config_manager: Application configuration manager
            loop: Asyncio event loop
        """
        self.config_manager: ConfigManager = config_manager
        self.pairs_dict: dict[str, Pair] = config_manager.pairs
        self.tokens_dict: dict[str, Token] = config_manager.tokens
        self.ccxt_i: ccxt.Exchange = config_manager.ccxt_manager.my_ccxt
        self.config_coins: Any = config_manager.config_coins
        self.disabled_coins: list[str] = []
        self.http_session: aiohttp.ClientSession | None = None
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self._http_session_owner: bool = False
        self.loop: asyncio.AbstractEventLoop = loop

        # Subcomponents
        price_update_handler = PriceUpdateHandler(
            config_manager.ccxt_manager, config_manager
        )
        self.price_handler: PriceHandler = PriceHandler(
            self, loop, price_update_handler
        )
        self.balance_manager: BalanceManager = BalanceManager(
            self.tokens_dict, self.config_manager, loop
        )
        self.processor: TradingProcessor = TradingProcessor(self)
        # Pass controller reference to strategy
        self.config_manager.strategy_instance.controller = self

    async def main_init_loop(self) -> None:
        """Initialization loop executed before main trading starts."""
        try:
            # Read addresses for enabled tokens
            token_init_futures: list[asyncio.Task] = [
                token.dex.read_address()
                for token in self.tokens_dict.values()
                if token.dex.enabled and not self.shutdown_event.is_set()
            ]
            if token_init_futures:
                await asyncio.gather(*token_init_futures)

            # Load XBridge config if needed
            if self.config_manager.load_xbridge_conf_on_startup:
                xbm: XBridgeManager = self.config_manager.xbridge_manager
                await xbm.dxloadxbridgeconf()

            # Initialize balances and prices
            await self.balance_manager.update_balances()
            await self.price_handler.update_ccxt_prices()

            # Update pair pricing
            strategy = self.config_manager.strategy_instance
            if strategy.should_update_cex_prices():
                price_futures: list[asyncio.Task] = [
                    pair.cex.update_pricing()
                    for pair in self.pairs_dict.values()
                    if not self.shutdown_event.is_set()
                ]
                if price_futures:
                    await asyncio.gather(*price_futures)

            await self.processor.process_pairs(strategy.thread_init_async_action)
        except Exception as e:
            if self.config_manager:
                await self.config_manager.error_handler.handle_async(
                    e, context={"component": "main_init_loop"}
                )
            raise

    async def main_loop(self) -> None:
        """Main trading loop executed repeatedly at configured intervals."""
        try:
            start_time: float = time.perf_counter()
            await self.balance_manager.update_balances()
            await self.price_handler.update_ccxt_prices()

            price_futures: list[asyncio.Task] = []
            strategy = self.config_manager.strategy_instance
            for pair in self.pairs_dict.values():
                if self.shutdown_event.is_set():
                    return
                if strategy.should_update_cex_prices():
                    price_futures.append(pair.cex.update_pricing())
            if price_futures:
                await asyncio.gather(*price_futures)

            await self.processor.process_pairs(strategy.safe_thread_loop)
            self._report_time(start_time)
        except Exception as e:
            context: dict = {"component": "main_loop"}
            if self.config_manager:
                await self.config_manager.error_handler.handle_async(e, context=context)

    def _report_time(self, start_time: float) -> None:
        """
        Log operation execution time.

        Args:
            start_time: Timestamp before operation started
        """
        end_time: float = time.perf_counter()
        duration: float = end_time - start_time
        self.config_manager.general_log.info(
            f"Operation took {duration:0.2f} second(s) to complete."
        )

    async def close_http_session(self) -> None:
        """Close the HTTP session if controller owns it."""
        if (
                self.http_session
                and not self.http_session.closed
                and self._http_session_owner
        ):
            try:
                await self.http_session.close()
            except (aiohttp.ClientError, asyncio.CancelledError) as e:
                self.config_manager.general_log.warning(
                    f"Session closure warning: {e!s}"
                )
            self.config_manager.general_log.debug("HTTP session closed")
        self.http_session = None
        self._http_session_owner = False
