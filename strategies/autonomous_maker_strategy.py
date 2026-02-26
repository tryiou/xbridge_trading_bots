import asyncio
import hashlib
import logging
from typing import Any

from definitions.errors import ConfigurationError

from .autonomous_inventory import InventoryManager
from .autonomous_order_ladder import OrderLadder
from .autonomous_order_sizing import OrderSizingEngine
from .autonomous_pricing_engine import PricingEngine
from .autonomous_state import StateManager, TradeRecorder
from .maker_strategy import MakerStrategy


class AutonomousMakerStrategy(MakerStrategy):
    def __init__(self, config_manager, controller=None):
        super().__init__(config_manager, controller)
        self.config_autonomous = config_manager.config_autonomous_maker
        self.pair_configs = self.config_autonomous.pair_configs
        self.pair_config: dict[str, Any] = {}
        self.token_a = ""
        self.token_b = ""
        self.pricing_engine: PricingEngine | None = None
        self.inventory_manager: InventoryManager | None = None
        self.order_ladder: OrderLadder | None = None
        self.sizing_engine: OrderSizingEngine | None = None
        self.state_manager: StateManager | None = None
        self.trade_recorder: TradeRecorder | None = None
        self.trade_count = 0
        self.mid_price = 0.0
        self.initial_mid_price = 0.0

    def initialize_strategy_specifics(self, **kwargs):
        self.config_manager.general_log.info(
            "--- Autonomous Maker Strategy Parameters ---"
        )

        enabled_pairs = [p for p in self.pair_configs if p.get("enabled", True)]
        if not enabled_pairs:
            self.config_manager.general_log.info(
                "  - No enabled pairs found in config."
            )
        else:
            self.config_manager.general_log.info(
                "  - Found %d enabled pair(s):", len(enabled_pairs)
            )
            for pair_cfg in enabled_pairs:
                self.config_manager.general_log.info(
                    "    - %s (%s): Initial A=%.4f, Initial B=%.4f, Mid-Price=%.6f",
                    pair_cfg["name"],
                    pair_cfg["pair"],
                    pair_cfg.get("initial_balance_a", 0),
                    pair_cfg.get("initial_balance_b", 0),
                    pair_cfg.get("initial_mid_price", 0),
                )

        self.config_manager.general_log.info(
            "------------------------------------------"
        )

    def get_tokens_for_initialization(self, **kwargs) -> list:
        tokens = set()
        for cfg in self.pair_configs:
            if cfg.get("enabled", True):
                t1, t2 = cfg["pair"].split("/")
                tokens.add(t1)
                tokens.add(t2)
        return list(tokens)

    def get_pairs_for_initialization(self, tokens_dict, **kwargs) -> dict:
        from definitions.pair import Pair

        pairs = {}
        enabled_configs = [c for c in self.pair_configs if c.get("enabled", True)]

        for cfg in enabled_configs:
            t1, t2 = cfg["pair"].split("/")
            pair_name = cfg["name"]

            pairs[pair_name] = Pair(
                token1=tokens_dict[t1],
                token2=tokens_dict[t2],
                cfg=cfg,
                strategy="autonomous_maker",
                dex_enabled=True,
                config_manager=self.config_manager,
            )

        return pairs

    def _initialize_for_pair(self, pair_cfg: dict[str, Any]):
        self.pair_config = pair_cfg
        pair = pair_cfg["pair"]
        self.token_a, self.token_b = pair.split("/")

        self.mid_price = pair_cfg.get("initial_mid_price", 0.0)
        self.initial_mid_price = self.mid_price

        self.pricing_engine = PricingEngine(
            mid_price=self.mid_price,
            max_open_orders=pair_cfg.get("max_open_orders", 10),
            spread_mode=pair_cfg.get("spread_mode", "exponential"),
            base_spread_percent=pair_cfg.get("base_spread_percent", 1.0),
            spread_multiplier=pair_cfg.get("spread_multiplier", 2.0),
            spread_increment=pair_cfg.get("spread_increment", 0.5),
            max_price_range_percent=pair_cfg.get("max_price_range_percent", 50.0),
        )

        self.inventory_manager = InventoryManager(
            initial_balance_a=pair_cfg.get("initial_balance_a", 0.0),
            initial_balance_b=pair_cfg.get("initial_balance_b", 0.0),
            token_a_symbol=self.token_a,
            token_b_symbol=self.token_b,
            mid_price=self.mid_price,
        )

        self.order_ladder = OrderLadder(
            max_orders=pair_cfg.get("max_open_orders", 10),
            partial_percent=pair_cfg.get("partial_percent", 0.1),
        )

        self.sizing_engine = OrderSizingEngine(
            mode=pair_cfg.get("order_sizing_mode", "equal"),
            base_percent=pair_cfg.get("base_percent", 0.05),
            buy_amounts=pair_cfg.get("buy_order_amounts", []),
            sell_amounts=pair_cfg.get("sell_order_amounts", []),
        )

        inventory_config = pair_cfg.get("inventory", {})
        position_config = pair_cfg.get("position", {})
        spread_config = pair_cfg.get("spread", {})
        risk_config = pair_cfg.get("risk", {})

        self.min_reserve_percent = inventory_config.get("min_reserve_percent", 0.10)
        self.concentration_sensitivity = inventory_config.get(
            "concentration_sensitivity", 2.0
        )

        self.inventory_manager.configure_risk(
            {
                "target_ratio_a": position_config.get("target_ratio_a", 0.50),
                "skew_sensitivity": position_config.get("skew_sensitivity", 0.15),
            }
        )

        self.pricing_engine.configure_spread(
            {
                "volatility_adjustment": spread_config.get(
                    "volatility_adjustment", True
                ),
                "volatility_multiplier": spread_config.get(
                    "volatility_multiplier", 1.0
                ),
                "price_skew": position_config.get("price_skew", 0.002),
            }
        )

        self.sizing_engine.configure_skew(
            intensity=position_config.get("skew_intensity", 0.5),
            price_skew=position_config.get("price_skew", 0.002),
        )

        self.risk_config = risk_config
        self.position_config = position_config

        pair_name_safe = pair_cfg["name"].replace("/", "_")
        self.state_manager = StateManager(
            pair_name_safe, self.config_manager.ROOT_DIR + "/data"
        )
        self.trade_recorder = TradeRecorder(
            pair_name_safe, self.config_manager.ROOT_DIR + "/data"
        )

        saved_state = self.state_manager.load_state()
        if saved_state:
            config_hash = hashlib.sha256(str(pair_cfg).encode()).hexdigest()
            if saved_state.config_hash and saved_state.config_hash != config_hash:
                raise ConfigurationError(
                    f"Config hash mismatch! Saved: {saved_state.config_hash[:8]}, Current: {config_hash[:8]}. "
                    f"Cannot restore state - configuration has changed since last run. "
                    f"Either restore the original config or delete the state files manually.",
                    context={
                        "saved_hash": saved_state.config_hash,
                        "current_hash": config_hash,
                    },
                )

            self.mid_price = saved_state.mid_price
            self.trade_count = saved_state.trade_count
            self.initial_mid_price = saved_state.initial_mid_price
            self.pricing_engine.update_mid_price(self.mid_price)
            self.inventory_manager.update_mid_price(self.mid_price)

            orders_data = self.state_manager.load_orders()
            if (
                orders_data
                and "open_orders" in orders_data
                and orders_data.get("open_orders")
            ):
                self.order_ladder = OrderLadder.from_dict(
                    orders_data,
                    max_orders=pair_cfg.get("max_open_orders", 10),
                    partial_percent=pair_cfg.get("partial_percent", 0.1),
                )

    async def _fetch_current_balances(self) -> tuple[float, float]:
        balances = await self.config_manager.xbridge_manager.gettokenbalances()
        if not balances or self.token_a not in balances or self.token_b not in balances:
            self.config_manager.general_log.error(
                "Failed to fetch balances from xbridge"
            )
            return None, None
        return float(balances[self.token_a]), float(balances[self.token_b])

    async def _check_order_statuses(self):
        if not self.order_ladder:
            return

        for order in self.order_ladder.get_open_orders():
            try:
                status_result = (
                    await self.config_manager.xbridge_manager.getorderstatus(order.id)
                )

                if status_result and "status" in status_result:
                    status_data = status_result

                    if status_data.get("status") == "finished":
                        self.config_manager.general_log.info(
                            "DETECTED FINISHED ORDER: %s status=%s",
                            order.id,
                            status_data.get("status"),
                        )

                    if status_data.get("status") in ["finished"]:
                        self.order_ladder.update_order_status(order.id, "finished")

                        finished_taker_amount = float(
                            status_data.get("finished", order.amount)
                        )
                        self.order_ladder.record_finish(order.id, finished_taker_amount)

                        executed_price = float(status_data.get("price", order.price))

                        if order.side == "sell":
                            maker_token = self.token_a
                            taker_token = self.token_b
                            execution_amount = finished_taker_amount / executed_price
                            maker_filled = execution_amount
                            taker_filled = finished_taker_amount
                        else:
                            maker_token = self.token_b
                            taker_token = self.token_a
                            execution_amount = finished_taker_amount
                            maker_filled = finished_taker_amount * executed_price
                            taker_filled = finished_taker_amount

                        self._process_execution(
                            side=order.side,
                            amount=execution_amount,
                            price=executed_price,
                        )

                        self.config_manager.general_log.info(
                            "Filled order: %s | SOLD %.4f %s → RECEIVED %.4f %s @ %.4f",
                            order.side.upper(),
                            maker_filled,
                            maker_token,
                            taker_filled,
                            taker_token,
                            executed_price,
                        )

                        self.config_manager.trade_log.info(
                            "TRADE: side=%s amount=%.8f price=%.8f",
                            order.side,
                            taker_filled,
                            executed_price,
                        )

                    elif status_data.get("status") in ["canceled", "expired"]:
                        self.order_ladder.update_order_status(order.id, "canceled")
                        self.config_manager.general_log.info(
                            "Order canceled/expired: %s", order.id
                        )

            except Exception as e:
                self.config_manager.general_log.warning(
                    "Error checking order status for %s: %s", order.id, e
                )

    def _cleanup_finished_orders(self):
        """Remove finished and canceled orders from the ladder."""
        if not self.order_ladder:
            return

        finished_orders = [
            order_id
            for order_id, order in self.order_ladder.orders.items()
            if order.status in ["finished", "canceled", "expired"]
        ]

        for order_id in finished_orders:
            self.order_ladder.remove_order(order_id)
            self.config_manager.general_log.debug(
                "Cleaned up order %s from ladder", order_id[:8]
            )

    def _process_execution(self, side: str, amount: float, price: float):
        self.mid_price = price

        self.pricing_engine.update_mid_price(self.mid_price)
        self.inventory_manager.update_mid_price(self.mid_price)

        self.trade_count += 1

        self.trade_recorder.record_trade(
            side=side,
            amount=amount,
            price=price,
            balance_a_after=self.inventory_manager.current_balance_a,
            balance_b_after=self.inventory_manager.current_balance_b,
        )

        self.config_manager.general_log.info(
            "Order finished: %s %.4f @ %.2f | Balance: %s=%.4f %s=%.4f",
            side,
            amount,
            price,
            self.token_a,
            self.inventory_manager.current_balance_a,
            self.token_b,
            self.inventory_manager.current_balance_b,
        )

        config_hash = hashlib.sha256(str(self.pair_config).encode()).hexdigest()
        self.state_manager.save_state(
            mid_price=self.mid_price,
            trade_count=self.trade_count,
            initial_mid_price=self.initial_mid_price,
            config_hash=config_hash,
        )

    async def _create_orders(self):
        if not all(
            [
                self.pricing_engine,
                self.inventory_manager,
                self.order_ladder,
                self.sizing_engine,
            ]
        ):
            return

        risk_config = getattr(self, "risk_config", {})
        position_config = getattr(self, "position_config", {})

        skew = self.inventory_manager.calculate_skew()

        balance_a = self.inventory_manager.current_balance_a
        balance_b = self.inventory_manager.current_balance_b

        if balance_a <= 0 and balance_b <= 0:
            return

        open_sell_orders = [
            o
            for o in self.order_ladder.orders.values()
            if o.side == "sell" and o.status == "open"
        ]
        open_buy_orders = [
            o
            for o in self.order_ladder.orders.values()
            if o.side == "buy" and o.status == "open"
        ]

        allocated_a = sum(o.amount for o in open_sell_orders)
        allocated_b = sum(o.amount for o in open_buy_orders)

        dyn_available_a, dyn_available_b = (
            self.inventory_manager.get_available_for_trading(
                self.min_reserve_percent, self.concentration_sensitivity
            )
        )
        available_a = max(0, dyn_available_a - allocated_a)
        available_b = max(0, dyn_available_b - allocated_b)

        max_buy_levels = self.order_ladder.max_orders // 2
        max_sell_levels = (self.order_ladder.max_orders + 1) // 2

        occupied_buy_levels = {o.level for o in open_buy_orders}
        occupied_sell_levels = {o.level for o in open_sell_orders}

        missing_buy_levels = {
            lvl for lvl in range(1, max_buy_levels + 1)
        } - occupied_buy_levels
        missing_sell_levels = {
            lvl for lvl in range(1, max_sell_levels + 1)
        } - occupied_sell_levels

        buy_levels_needed = len(missing_buy_levels)
        sell_levels_needed = len(missing_sell_levels)

        current_ratio_a = self.inventory_manager.get_ratio_a()

        if buy_levels_needed > 0 and available_b > 0:
            remaining_b = available_b
            sorted_buy_levels = sorted(missing_buy_levels)
            for i, level in enumerate(sorted_buy_levels):
                if remaining_b <= 0:
                    break
                remaining_levels = len(sorted_buy_levels) - i
                amount = self.sizing_engine.calculate_amounts(
                    side="buy",
                    level=level,
                    total_balance=available_b,
                    remaining_levels=remaining_levels,
                    skew=skew,
                )

                amount = min(amount, remaining_b)
                if amount <= 0:
                    break

                price = self.pricing_engine.calculate_buy_price(level, skew)
                if self.pricing_engine.validate_price(price, "buy"):
                    await self._place_order("buy", level, price, amount)
                    remaining_b -= amount

        if sell_levels_needed > 0 and available_a > 0:
            remaining_a = available_a
            sorted_sell_levels = sorted(missing_sell_levels)
            for i, level in enumerate(sorted_sell_levels):
                if remaining_a <= 0:
                    break
                remaining_levels = len(sorted_sell_levels) - i
                amount = self.sizing_engine.calculate_amounts(
                    side="sell",
                    level=level,
                    total_balance=available_a,
                    remaining_levels=remaining_levels,
                    skew=skew,
                )

                amount = min(amount, remaining_a)
                if amount <= 0:
                    break

                price = self.pricing_engine.calculate_sell_price(level, skew)
                if self.pricing_engine.validate_price(price, "sell"):
                    await self._place_order("sell", level, price, amount)
                    remaining_a -= amount

    async def _place_order(self, side: str, level: int, price: float, amount: float):
        if amount <= 0 or price <= 0:
            return

        if side == "sell":
            maker = self.token_a
            makeramount = amount
            taker = self.token_b
            takeramount = amount * price
            side_desc = f"SELL {self.token_a} (get {self.token_b})"
        else:
            maker = self.token_b
            makeramount = amount
            taker = self.token_a
            takeramount = amount / price
            side_desc = f"BUY {self.token_a} (spend {self.token_b})"

        min_size = amount * self.order_ladder.partial_percent

        try:
            result = await self.config_manager.xbridge_manager.makepartialorder(
                maker=maker,
                makeramount=makeramount,
                makeraddress=self.pair_config.get("address_a", ""),
                taker=taker,
                takeramount=takeramount,
                takeraddress=self.pair_config.get("address_b", ""),
                min_size=min_size,
                repost=False,
            )

            if result and "id" in result:
                order_id = result.get("id")
                if order_id:
                    self.order_ladder.add_order(level, side, price, amount, order_id)
                    self._save_orders()

                    self.config_manager.general_log.info(
                        "Created order: %s | SELL %.4f %s → BUY %.4f %s @ %.4f",
                        side_desc,
                        makeramount,
                        maker,
                        takeramount,
                        taker,
                        price,
                    )
                    return

            self.config_manager.general_log.warning(
                "Failed to create %s order: %s", side, result
            )

        except Exception as e:
            self.config_manager.general_log.error(
                "Error creating %s order: %s", side, e
            )

    def _save_orders(self):
        if self.state_manager and self.order_ladder:
            self.state_manager.save_orders(self.order_ladder.to_dict())

    async def _cancel_stale_orders(self, force_cancel: bool = False):
        if not self.order_ladder:
            return

        if self.trade_count > 0:
            self.config_manager.general_log.info(
                "Restored from state with %d open orders - preserving orders",
                self.order_ladder.num_open_orders,
            )
            return

        orders_to_cancel = self.order_ladder.get_open_orders()
        if not orders_to_cancel:
            return

        for order in orders_to_cancel:
            try:
                await self.config_manager.xbridge_manager.cancelorder(order.id)
                self.order_ladder.remove_order(order.id)
                self.config_manager.general_log.info(
                    "Canceled stale order: %s", order.id
                )
            except Exception as e:
                self.config_manager.general_log.warning(
                    "Error canceling order %s: %s", order.id, e
                )

    def _get_status_summary(self) -> str:
        if not self.inventory_manager:
            return "Not initialized"

        profit_a = (
            self.inventory_manager.current_balance_a
            - self.inventory_manager.initial_balance_a
        )
        profit_b = (
            self.inventory_manager.current_balance_b
            - self.inventory_manager.initial_balance_b
        )
        return (
            f"Mid-Price: {self.mid_price:.6f} | "
            f"Orders: {self.order_ladder.num_open_orders if self.order_ladder else 0} | "
            f"Trades: {self.trade_count} | "
            f"A: {self.inventory_manager.current_balance_a:.4f} ({profit_a:+.4f}) | "
            f"B: {self.inventory_manager.current_balance_b:.4f} ({profit_b:+.4f})"
        )

    def build_sell_order_details(self, dex_pair, manual_dex_price=None) -> tuple:
        return 0.0, 0.0

    def calculate_sell_price(self, dex_pair, manual_dex_price=None) -> float:
        return 0.0

    def build_buy_order_details(self, dex_pair, manual_dex_price=None) -> tuple:
        return 0.0, 0.0

    def determine_buy_price(self, dex_pair, manual_dex_price=None) -> float:
        return 0.0

    def get_price_variation_tolerance(self, dex_pair) -> float:
        return self.pair_config.get("price_variation_tolerance", 5.0)

    def calculate_variation_based_on_side(
        self,
        dex_pair,
        current_order_side: str,
        cex_price: float,
        original_price: float,
    ) -> tuple[float, bool]:
        return 1.0, False

    def init_virtual_order_logic(self, dex_pair, order_history: dict):
        pass

    async def handle_order_status_error(self, dex_pair):
        pass

    async def reinit_virtual_order_after_price_variation(
        self, dex_pair, disabled_coins: list
    ):
        pass

    async def handle_finished_order(self, dex_pair, disabled_coins: list):
        pass

    async def handle_error_swap_status(self, dex_pair):
        pass

    def should_update_cex_prices(self) -> bool:
        return False

    async def thread_init_async_action(self, pair_instance):
        pair_cfg = pair_instance.cfg
        self._initialize_for_pair(pair_cfg)

        self.config_manager.general_log.info(
            "Autonomous Maker initialized for %s", pair_cfg["pair"]
        )

        await self._cancel_stale_orders()

    async def process_pair_async(self, pair_instance):
        if not self.inventory_manager:
            return

        balance_a, balance_b = await self._fetch_current_balances()
        self.inventory_manager.update_balance_a(balance_a)
        self.inventory_manager.update_balance_b(balance_b)

        await self._check_order_statuses()

        self._cleanup_finished_orders()

        await self._create_orders()

        self._save_orders()

        summary = self._get_status_summary()
        self.config_manager.general_log.debug("%s", summary)

    def get_operation_interval(self) -> int:
        return self.pair_config.get("check_interval", 15)

    def get_startup_tasks(self) -> list:
        return [
            self.config_manager.xbridge_manager.cancelallorders,
            self.config_manager.xbridge_manager.dxflushcancelledorders,
        ]
