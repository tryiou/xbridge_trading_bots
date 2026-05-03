from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from definitions.order_status_processor import OrderStatusProcessor
from definitions.pair import DexPair

from .base_strategy import BaseStrategy

if TYPE_CHECKING:
    from typing import Any


class MakerStrategy(BaseStrategy):
    """
    Abstract base class for "maker" strategies that create and manage
    orders on the DEX order book (e.g., PingPong, BasicSeller).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._order_status_processor: OrderStatusProcessor | None = None

    @abstractmethod
    def build_sell_order_details(
            self, dex_pair: DexPair, manual_dex_price: float | None = None
    ) -> tuple[float, float]:
        """
        Strategy-specific logic to determine amount and offset for a sell order.
        Returns (amount, offset).
        """
        pass

    @abstractmethod
    def calculate_sell_price(self, dex_pair: DexPair, manual_dex_price: float | None = None) -> float:
        """
        Strategy-specific logic to calculate the sell price.
        """
        pass

    @abstractmethod
    def build_buy_order_details(
            self, dex_pair: DexPair, manual_dex_price: float | None = None
    ) -> tuple[float, float]:
        """
        Strategy-specific logic to determine amount and spread for a buy order.
        Returns (amount, spread).
        """
        pass

    @abstractmethod
    def determine_buy_price(self, dex_pair: DexPair, manual_dex_price=None) -> float:
        """
        Strategy-specific logic to determine the buy price.
        """
        pass

    @abstractmethod
    def get_price_variation_tolerance(self, dex_pair: DexPair) -> float:
        """
        Returns the price variation tolerance for the strategy.
        """
        pass

    @abstractmethod
    def calculate_variation_based_on_side(
            self,
            dex_pair: DexPair,
            current_order_side: str,
            cex_price: float,
            original_price: float,
    ) -> float:
        """
        Strategy-specific logic to calculate price variation based on order side.
        """
        pass

    @abstractmethod
    def init_virtual_order_logic(self, dex_pair: DexPair, order_history: dict):
        pass

    @abstractmethod
    async def handle_order_status_error(self, dex_pair: DexPair):
        pass

    @abstractmethod
    def reinit_virtual_order_after_price_variation(
            self, dex_pair: DexPair, disabled_coins: list
    ):
        pass

    @abstractmethod
    def handle_finished_order(self, dex_pair: DexPair, disabled_coins: list):
        pass

    @abstractmethod
    async def handle_error_swap_status(self, dex_pair: DexPair):
        pass

    def get_startup_tasks(self) -> list:
        """
        For maker strategies, it's often useful to clear out any old,
        stale orders before starting fresh.
        """
        return [
            self.config_manager.xbridge_manager.cancelallorders,
            self.config_manager.xbridge_manager.dxflushcancelledorders,
        ]

    def should_update_cex_prices(self) -> bool:
        return True

    def get_operation_interval(self) -> int:
        return 15

    async def thread_init_async_action(self, pair_instance):
        pair_instance.dex.init_virtual_order(self.controller.disabled_coins)
        await pair_instance.dex.create_order()

    async def process_pair_async(self, pair_instance):
        if self._order_status_processor is None:
            self._order_status_processor = OrderStatusProcessor(
                self.config_manager,
                self.config_manager.xbridge_manager,
                self.config_manager.error_handler,
                self.config_manager.strategy_instance,
                self.controller.shutdown_event,
            )
        await self._order_status_processor.process(
            pair_instance.dex, self.controller.disabled_coins
        )

    async def cancel_own_orders(self):
        """Cancel only orders belonging to this strategy"""
        if not hasattr(self, "controller") or not self.controller:
            return

        self.config_manager.general_log.info(
            f"Canceling {self.__class__.__name__} orders..."
        )
        count = 0
        for pair_name, pair in self.controller.pairs_dict.items():
            if not pair.dex_enabled:
                continue

            dex = pair.dex
            if not dex.order or "id" not in dex.order:
                continue

            order_id = dex.order["id"]
            try:
                self.config_manager.general_log.info(
                    f"Canceling order {order_id} for {pair_name}"
                )
                await pair.dex.cancel_myorder_async()
                count += 1
                # await self.config_manager.xbridge_manager.cancelorder(order_id)
            except Exception as e:
                self.config_manager.general_log.error(
                    f"Error canceling order {order_id}: {e}"
                )
            finally:
                pass
                # self.config_manager.general_log.info(f"Cancelled order {order_id} for {pair_name}")
        return count
