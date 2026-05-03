import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from definitions.config_manager import ConfigManager
    from definitions.xbridge_manager import XBridgeManager


class OrderStatusProcessor:
    """Deep module for processing DEX order status checks.

    Handles the status check workflow for DEX pairs, including
    price updates, order status verification, and order lifecycle management.

    Interface:
        process(dex_pair, disabled_coins, display) -> None
    """

    def __init__(
        self,
        config_manager: "ConfigManager",
        xbridge_manager: "XBridgeManager",
        error_handler: Any,
        strategy_instance: Any,
        shutdown_event: asyncio.Event,
    ) -> None:
        self.config_manager = config_manager
        self.xbridge_manager = xbridge_manager
        self.error_handler = error_handler
        self.strategy_instance = strategy_instance
        self.shutdown_event = shutdown_event

    async def process(self, dex_pair, disabled_coins=None, display=False) -> None:
        """Process order status for a DEX pair.

        Args:
            dex_pair: The DexPair instance to process.
            disabled_coins: List of disabled coin symbols.
            display: Whether to display price information.
        """
        await dex_pair.pair.cex.update_pricing(display=display)
        if dex_pair.disabled:
            dex_pair.pair.logger.info(
                "Pair %s Disabled, reason: %s", dex_pair.symbol, dex_pair.disable_reason
            )
            return

        status = None
        if dex_pair.order and dex_pair.order.get("id"):
            status = await self._check_order_status(dex_pair)
        elif not dex_pair.disabled and dex_pair.current_order:
            dex_pair.init_virtual_order(disabled_coins, display=False)
            if dex_pair.order and "id" in dex_pair.order:
                status = await self._check_order_status(dex_pair)

        if status == dex_pair.STATUS_OPEN:
            if disabled_coins and (
                dex_pair.t1.symbol in disabled_coins
                or dex_pair.t2.symbol in disabled_coins
            ):
                if dex_pair.order:
                    dex_pair.pair.logger.info(
                        "Disabled pairs due to cc_height_check %s",
                        dex_pair.symbol,
                    )
                    await dex_pair.cancel_myorder_async()
            else:
                await self._check_price_variation(dex_pair, disabled_coins, display)
        elif status == dex_pair.STATUS_FINISHED:
            await self._handle_finished_order(dex_pair, disabled_coins)
        elif status == dex_pair.STATUS_OTHERS:
            dex_pair.check_price_in_range(display=display)
        elif status == dex_pair.STATUS_ERROR_SWAP:
            await self._handle_error_swap_status(dex_pair)
        elif status is not None and not dex_pair.disabled:
            dex_pair.pair.logger.info(
                "Order %s is %s. Re-initializing order for %s.",
                dex_pair.order.get("id"),
                dex_pair.order.get("status"),
                dex_pair.symbol,
            )
            dex_pair.order = None
            dex_pair.init_virtual_order(disabled_coins, display=False)
            await dex_pair.create_order()

    async def _check_order_status(self, dex_pair) -> int | None:
        """Check the status of the current order."""
        return await dex_pair.check_order_status()

    async def _check_price_variation(self, dex_pair, disabled_coins, display) -> None:
        """Check price variation and take appropriate action."""
        await dex_pair.check_price_variation(disabled_coins, display=display)

    async def _handle_finished_order(self, dex_pair, disabled_coins) -> None:
        """Handle a finished order."""
        dex_pair.pair.logger.info(
            "order FINISHED: {'name': '%s', 'pair': '%s', 'side': '%s', 'orderid': '%s'}",
            dex_pair.pair.cfg["name"],
            dex_pair.symbol,
            dex_pair.current_order["side"],
            dex_pair.order["id"],
        )
        dex_pair.order_history = dex_pair.current_order
        dex_pair.write_last_order_history()
        if not self.shutdown_event.is_set():
            taker_sym = dex_pair.order.get("taker")
            if taker_sym == dex_pair.t1.symbol:
                await dex_pair.t1.dex.request_addr()
            elif taker_sym == dex_pair.t2.symbol:
                await dex_pair.t2.dex.request_addr()
        await self.strategy_instance.handle_finished_order(dex_pair, disabled_coins)

    async def _handle_error_swap_status(self, dex_pair) -> None:
        """Handle error swap status."""
        await self.strategy_instance.handle_error_swap_status(dex_pair)
