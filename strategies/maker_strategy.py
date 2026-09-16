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
        self._checkup_running = False

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
    def calculate_sell_price(
        self, dex_pair: DexPair, manual_dex_price: float | None = None
    ) -> float:
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
    def reinit_virtual_order_after_price_variation(self, dex_pair: DexPair):
        pass

    @abstractmethod
    def handle_finished_order(self, dex_pair: DexPair):
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
        pair_instance.dex.init_virtual_order()
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
        await self._order_status_processor.process(pair_instance.dex)

    async def run_periodic_checkup(self) -> None:
        """Graveyard reconciliation: one dxGetMyOrders, zero per-id calls.

        Walks OUR historic notebook against the daemon's own-orders listing:

        * absent from listing  -> no order exists right now -> dormant, kept;
        * canceled/expired     -> legit resting state of a past id -> kept
          (purging here would blind us to its later resurrection);
        * open/new, not tracked-> RESURRECTED ghost -> ERROR log + cancel,
          id STAYS pooled so repeat offenders keep being caught;
        * finished             -> filled while unmonitored -> CRITICAL, pruned;
        * failed-swap stages   -> WARN (funds roll back) -> pruned;
        * other in-progress    -> INFO once per state change, kept.

        Only ids from our own notebook are ever examined: manual orders and
        sibling sessions are structurally out of reach. Never raises.
        """
        if self._checkup_running:
            return
        self._checkup_running = True
        try:
            xbm = self.config_manager.xbridge_manager
            pool = xbm.order_pool
            try:
                listing = await xbm.getmyorders()
            except Exception as e:
                self.config_manager.general_log.debug(
                    "Checkup: could not list own orders (%s)", e
                )
                return
            if not isinstance(listing, list):
                return
            index = {o["id"]: o for o in listing if isinstance(o, dict) and o.get("id")}
            tracked = self._collect_tracked_ids()
            pool.enforce_ttl()

            for oid in list(pool.ids):
                entry = index.get(oid)
                if entry is None:
                    continue  # no order exists right now -> dormant
                status = str(entry.get("status", "")).lower()
                if oid in tracked:
                    continue  # our actively managed order, normal life
                await self._apply_pool_verdict(pool, oid, entry, status)

            pool.flush()
        finally:
            self._checkup_running = False

    def _iter_pairs(self):
        """Yield the pairs owned by this strategy instance."""
        if not getattr(self, "controller", None) or not self.controller.pairs_dict:
            return
        yield from self.controller.pairs_dict.values()

    def _collect_tracked_ids(self) -> set[str]:
        """Ids this strategy is currently managing."""
        tracked: set[str] = set()
        for pair in self._iter_pairs():
            order = pair.dex.order
            if isinstance(order, dict) and order.get("id"):
                tracked.add(str(order["id"]))
        return tracked

    async def _apply_pool_verdict(
        self, pool, oid: str, entry: dict, status: str
    ) -> None:
        """Flag/handle one pooled id per the v9 verdict table. Never raises."""
        log = self.config_manager.general_log
        previous = pool.last_status(oid)

        def flag(level: str, message: str) -> None:
            # One log per observed state change, never per round.
            if previous != status:
                getattr(log, level)(message)

        if status == "finished":
            flag(
                "critical",
                f"Checkup: pooled order {oid} FILLED while unmonitored! "
                f"Snapshot: {entry}",
            )
            pool.purge(oid)  # final state observed -> mystery resolved
        elif status in ("offline", "invalid", "rolled back", "rollback failed"):
            flag(
                "warning",
                f"Checkup: pooled order {oid} ended '{status}' (funds roll "
                f"back automatically), purged",
            )
            pool.purge(oid)  # final state observed -> mystery resolved
        elif status in ("open", "new"):
            flag(
                "error",
                f"Checkup: RESURRECTED order {oid} is {status} - cancelling",
            )
            try:
                result = await self.config_manager.xbridge_manager.cancelorder(oid)
                if result:
                    # Deliberate: stays pooled. A cancelled ghost remains a
                    # candidate; if it resurrects again it gets caught again.
                    pass
                else:
                    log.warning(
                        "Checkup: cancel of %s got no confirmation; "
                        "stays pooled for retry",
                        oid,
                    )
            except Exception as e:
                log.warning(
                    "Checkup: cancelling resurrected %s failed, stays pooled: %s",
                    oid,
                    e,
                )
            pool.set_last_status(oid, status)
        elif status in ("canceled", "expired"):
            # Legit resting state: keep vigilant without logging every round.
            pool.set_last_status(oid, status)
        else:
            # Exotic in-progress stage on an unmanaged id: flag it.
            flag(
                "info",
                f"Checkup: pooled order {oid} in unexpected stage '{status}'",
            )
            pool.set_last_status(oid, status)

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
