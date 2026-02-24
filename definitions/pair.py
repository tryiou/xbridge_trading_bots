from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from definitions.config_manager import ConfigManager
    from definitions.token import Token


class Pair:
    def __init__(
            self,
            token1: Token,
            token2: Token,
            config_manager: ConfigManager | None,
            cfg: dict[str, Any],
            amount_token_to_sell: float | None = None,
            min_sell_price_usd: float | None = None,
            sell_price_offset: float | None = None,
            strategy: str | None = None,
            dex_enabled: bool = True,
            partial_percent: float | None = None,
            xbridge_manager: Any | None = None,
            ccxt_manager: Any | None = None,
            error_handler: Any | None = None,
            logger: logging.Logger | None = None,
    ) -> None:
        self.cfg = cfg
        self.name = cfg["name"]
        self.strategy = strategy
        self.t1 = token1
        self.t2 = token2
        self.symbol = f"{self.t1.symbol}/{self.t2.symbol}"
        self.disabled = False
        self.variation: float | list | None = None
        self.dex_enabled = dex_enabled
        self.amount_token_to_sell = amount_token_to_sell
        self.min_sell_price_usd = min_sell_price_usd
        self.sell_price_offset = self.cfg.get("sell_price_offset", sell_price_offset)

        self._config_manager = config_manager
        self.xbridge_manager = xbridge_manager or getattr(
            config_manager, "xbridge_manager", None
        )
        self.ccxt_manager = ccxt_manager or getattr(
            config_manager, "ccxt_manager", None
        )
        self.error_handler = error_handler or getattr(
            config_manager, "error_handler", None
        )
        self.logger = logger or getattr(
            config_manager, "general_log", logging.getLogger(__name__)
        )

        self.dex = DexPair(self, partial_percent)
        self.cex = CexPair(self)

    @property
    def config_manager(self):
        """Deprecated property for backward compatibility."""
        return self._config_manager


class DexPair:
    STATUS_OPEN = 0
    STATUS_FINISHED = 1
    STATUS_OTHERS = 2
    STATUS_ERROR_SWAP = -1
    STATUS_CANCELLED_WITHOUT_CALL = -2
    PRICE_VARIATION_TOLERANCE_DEFAULT = 0.01

    def __init__(self, pair: Pair, partial_percent: float | None) -> None:
        self.pair = pair
        self.t1 = pair.t1
        self.t2 = pair.t2
        self.symbol = pair.symbol
        self.order_history: dict[str, Any] | None = None
        self.current_order: dict[str, Any] | None = None
        self.disabled = False
        self.variation: float | list | None = None
        self.partial_percent = partial_percent
        self.orderbook: dict[str, Any] | None = None
        self.orderbook_timer: float | None = None
        self.order: dict[str, Any] | None = None
        self.read_last_order_history()

    async def update_dex_orderbook(self):
        self.orderbook = await self.pair.xbridge_manager.dxgetorderbook(
            detail=3, maker=self.t1.symbol, taker=self.t2.symbol
        )
        self.orderbook.pop("detail", None)

    def _get_history_file_path(self):
        return self.pair.config_manager.strategy_instance.get_dex_history_file_path(
            self.pair.name
        )

    def read_last_order_history(self):
        if not self.pair.dex_enabled or not self.pair.config_manager.strategy_instance:
            return
        file_path = self._get_history_file_path()
        try:
            with open(file_path) as fp:
                self.order_history = yaml.safe_load(fp)
        except FileNotFoundError:
            self.pair.logger.info("File not found: %s", file_path)
        except Exception as e:
            # Re-added file_path to context for better observability
            self.pair.error_handler.handle(
                e,
                context={
                    "pair": self.pair.name,
                    "stage": "read_last_order_history",
                    "file_path": file_path,
                },
            )
            self.order_history = None

    def write_last_order_history(self):
        file_path = self._get_history_file_path()
        try:
            with open(file_path, "w") as fp:
                yaml.safe_dump(self.order_history, fp)
        except Exception as e:
            # Re-added file_path to context to satisfy the test and improve debugging
            self.pair.error_handler.handle(
                e,
                context={
                    "pair": self.pair.name,
                    "stage": "write_last_order_history",
                    "file_path": file_path,
                },
            )

    def _log_virtual_order(self, side: str, maker_symbol: str, taker_symbol: str):
        self.pair.logger.info(
            "Virtual %s order created for %s | Symbol: %s | Maker: %s | Taker: %s | Maker size: %.6f | Taker size: %.6f | Price: %.8f",
            side,
            self.pair.name,
            self.symbol,
            maker_symbol,
            taker_symbol,
            self.current_order["maker_size"],
            self.current_order["taker_size"],
            self.current_order["dex_price"],
        )

    def create_virtual_sell_order(self):
        if not self.pair.dex_enabled:
            self.current_order = None
            return
        self.current_order = self._build_sell_order()
        self._log_virtual_order("sell", self.t1.symbol, self.t2.symbol)

    def create_virtual_buy_order(self):
        if not self.pair.dex_enabled:
            self.current_order = None
            return
        self.current_order = self._build_buy_order()
        self._log_virtual_order("buy", self.t2.symbol, self.t1.symbol)

    @staticmethod
    def truncate(value: float, digits: int = 8) -> float:
        if not isinstance(value, (int, float)):
            return value
        stepper = 10.0 ** digits
        return math.trunc(stepper * value) / stepper

    def _construct_order_dict(
            self,
            side,
            maker_token,
            taker_token,
            maker_size,
            taker_size,
            original_price,
            final_price,
    ):
        order_type = "exact"
        if (
                side == "SELL"
                and isinstance(self.partial_percent, (int, float))
                and 0 < self.partial_percent < 1
        ):
            order_type = "partial"
        order = {
            "symbol": self.symbol,
            "side": side,
            "maker": maker_token.symbol,
            "maker_address": maker_token.dex.address,
            "taker": taker_token.symbol,
            "taker_address": taker_token.dex.address,
            "type": order_type,
            "maker_size": DexPair.truncate(maker_size),
            "taker_size": DexPair.truncate(taker_size),
            "dex_price": DexPair.truncate(final_price),
            "org_pprice": DexPair.truncate(original_price),
            "org_t1price": DexPair.truncate(self.t1.cex.cex_price),
            "org_t2price": DexPair.truncate(self.t2.cex.cex_price),
        }
        if self.partial_percent and side == "SELL":
            order["minimum_size"] = maker_size * self.partial_percent
        return order

    def _build_sell_order(self):
        original_price = (
            self.pair.config_manager.strategy_instance.calculate_sell_price(self)
        )
        maker_size, offset = (
            self.pair.config_manager.strategy_instance.build_sell_order_details(self)
        )
        final_price = original_price * (1 + offset)
        taker_size = maker_size * final_price
        return self._construct_order_dict(
            "SELL",
            self.t1,
            self.t2,
            maker_size,
            taker_size,
            original_price,
            final_price,
        )

    def _build_buy_order(self):
        original_price = self.pair.config_manager.strategy_instance.determine_buy_price(
            self
        )
        taker_size, spread = (
            self.pair.config_manager.strategy_instance.build_buy_order_details(self)
        )
        final_price = original_price * (1 - spread)
        maker_size = taker_size * final_price
        return self._construct_order_dict(
            "BUY", self.t2, self.t1, maker_size, taker_size, original_price, final_price
        )

    def check_price_in_range(self, display=False):
        price_variation_tolerance = (
            self.pair.config_manager.strategy_instance.get_price_variation_tolerance(
                self
            )
        )
        var_result = self.pair.config_manager.strategy_instance.calculate_variation_based_on_side(
            self,
            self.current_order.get("side"),
            self.pair.cex.price,
            self.current_order["org_pprice"],
        )

        if isinstance(var_result, tuple):
            variation, is_locked = var_result
        else:
            variation = var_result[0] if isinstance(var_result, list) else var_result
            is_locked = isinstance(var_result, list)

        self._set_variation(variation, is_locked)
        if display:
            self._log_price_check(variation)
        if is_locked:
            return True
        return 1 - price_variation_tolerance < variation < 1 + price_variation_tolerance

    def _set_variation(self, variation_value, is_locked):
        truncated_var = self.truncate(variation_value, 3)
        self.variation = [truncated_var] if is_locked else truncated_var

    def _log_price_check(self, var):
        self.pair.logger.info(
            "Price variation check for %s: Variation: %.4f, Stored: %.4f, Live price: %.8f, Original price: %.8f, Ratio: %.4f",
            self.symbol,
            var,
            self.variation[0] if isinstance(self.variation, list) else self.variation,
            self.pair.cex.price,
            self.current_order["org_pprice"],
            self.pair.cex.price / self.current_order["org_pprice"],
        )

    def init_virtual_order(self, disabled_coins=None, display=True):
        if disabled_coins and (
                self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins
        ):
            self.disabled = True
            self.pair.logger.info(
                "%s disabled due to cc checks: %s", self.symbol, disabled_coins
            )
            return

        if not self.disabled:
            self.pair.config_manager.strategy_instance.init_virtual_order_logic(
                self, self.order_history
            )
            if display:
                self.pair.logger.info(
                    "live pair prices : %s %s | %s/USD: %s | %s/USD: %s",
                    DexPair.truncate(self.pair.cex.price),
                    self.symbol,
                    self.t1.symbol,
                    DexPair.truncate(self.t1.cex.usd_price, 3),
                    self.t2.symbol,
                    DexPair.truncate(self.t2.cex.usd_price, 3),
                )

    async def cancel_myorder_async(self):
        if self.order and self.order.get("id"):
            await self.pair.xbridge_manager.cancelorder(self.order["id"])
        self.order = None

    async def create_order(self, dry_mode=False):
        if self.disabled:
            return

        if self._is_shutting_down():
            self.pair.logger.warning(
                "Skipping order creation for %s - shutdown in progress", self.symbol
            )
            return

        self.order = None
        maker_size = f"{self.current_order['maker_size']:.6f}"
        bal = (
            self.t2.dex.free_balance
            if self.current_order["side"] == "BUY"
            else self.t1.dex.free_balance
        )

        if (
                bal is not None
                and maker_size.replace(".", "").isdigit()
                and float(bal) >= float(maker_size)
        ):
            await self._create_order(dry_mode, maker_size)
        else:
            self.pair.logger.error(
                "dex_create_order, balance too low: %s, need: %s %s",
                bal,
                maker_size,
                self.current_order["maker"],
            )

    async def _create_order(self, dry_mode, maker_size):
        try:
            maker, maker_address = (
                self.current_order["maker"],
                self.current_order["maker_address"],
            )
            taker, taker_address = (
                self.current_order["taker"],
                self.current_order["taker_address"],
            )
            taker_size = f"{self.current_order['taker_size']:.6f}"

            if dry_mode:
                self.pair.logger.info(
                    "dex_create_order, Dry mode. xb.makeorder(%s, %s, %s, %s, %s, %s)",
                    maker,
                    maker_size,
                    maker_address,
                    taker,
                    taker_size,
                    taker_address,
                )
                return

            if self.partial_percent:
                min_size = f"{self.current_order['minimum_size']:.6f}"
                self.order = await self.pair.xbridge_manager.makepartialorder(
                    maker,
                    maker_size,
                    maker_address,
                    taker,
                    taker_size,
                    taker_address,
                    min_size,
                )
            else:
                self.order = await self.pair.xbridge_manager.makeorder(
                    maker, maker_size, maker_address, taker, taker_size, taker_address
                )

            if self.order and "error" in self.order:
                await self._handle_order_error()

        except Exception as e:
            context = {
                "pair": self.pair.symbol,
                "stage": "order_creation",
                "error": str(e),
            }
            retry_or_continue = await self.pair.error_handler.handle_async(e, context)
            if not retry_or_continue:
                self.disabled = True

    async def _handle_order_error(self):
        original_error = self.order
        if original_error.get("code") not in {1019, 1018, 1026, 1032}:
            self.disabled = True

        strategy_handler = getattr(
            self.pair.config_manager.strategy_instance,
            "handle_order_status_error",
            None,
        )
        if strategy_handler:
            await strategy_handler(self)

        self.pair.logger.error(
            "Error making order on Pair: %s | Symbol: %s | Details: %s",
            self.pair.name,
            self.symbol,
            original_error,
        )

    async def check_order_status(self) -> int:
        try:
            local_dex_order = await self.pair.xbridge_manager.getorderstatus(
                self.order["id"]
            )
            if local_dex_order and "status" in local_dex_order:
                self.order = local_dex_order
                return self._map_order_status()
            self.pair.logger.warning(
                "Could not get valid status for order %s. Response: %s",
                self.order.get("id"),
                local_dex_order,
            )
        except Exception as e:
            await self.pair.error_handler.handle_async(
                e,
                context={
                    "pair": self.pair.name,
                    "stage": "check_order_status",
                    "order": self.order,
                },
            )

        self._handle_order_status_error()
        return self.STATUS_CANCELLED_WITHOUT_CALL

    def _map_order_status(self):
        status_mapping = {
            "open": self.STATUS_OPEN,
            "new": self.STATUS_OPEN,
            "created": self.STATUS_OTHERS,
            "initialized": self.STATUS_OTHERS,
            "committed": self.STATUS_OTHERS,
            "finished": self.STATUS_FINISHED,
            "expired": self.STATUS_CANCELLED_WITHOUT_CALL,
            "offline": self.STATUS_ERROR_SWAP,
            "canceled": self.STATUS_CANCELLED_WITHOUT_CALL,
            "invalid": self.STATUS_ERROR_SWAP,
            "rolled back": self.STATUS_ERROR_SWAP,
            "rollback failed": self.STATUS_ERROR_SWAP,
        }
        return status_mapping.get(self.order.get("status"), self.STATUS_OPEN)

    def _handle_order_status_error(self):
        self.pair.logger.error(
            "Error in dex_check_order_status: 'status' not in order. %s", self.order
        )
        if self.pair.strategy in ["pingpong", "basic_seller"]:
            self.order = None

    async def check_price_variation(self, disabled_coins, display=False):
        if "side" in self.current_order and not self.check_price_in_range(
                display=display
        ):
            self.pair.logger.warning(
                "check_price_variation, %s, variation: %s, %s, live_price: %.8f, order_price: %.8f",
                self.symbol,
                self.variation,
                self.order["status"],
                self.pair.cex.price,
                self.current_order["dex_price"],
            )
            if self.order and self.order.get("id"):
                self.pair.logger.warning(
                    "check_price_variation, dex cancel: %s", self.order["id"]
                )
                await self.cancel_myorder_async()
            await self.pair.config_manager.strategy_instance.reinit_virtual_order_after_price_variation(
                self, disabled_coins
            )

    async def status_check(
            self, disabled_coins=None, display=False, partial_percent=None
    ):
        await self.pair.cex.update_pricing(display)
        if self.disabled:
            self.pair.logger.info(
                "Pair %s Disabled, error: %s", self.symbol, self.order
            )
            return

        status = None
        if self.order and self.order.get("id"):
            status = await self.check_order_status()
        elif not self.disabled and self.current_order:
            self.init_virtual_order(disabled_coins, display=False)
            if self.order and "id" in self.order:
                status = await self.check_order_status()

        if status == self.STATUS_OPEN:
            if disabled_coins and (
                    self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins
            ):
                if self.order:
                    self.pair.logger.info(
                        "Disabled pairs due to cc_height_check %s", self.symbol
                    )
                    await self.cancel_myorder_async()
            else:
                await self.check_price_variation(disabled_coins, display=display)
        elif status == self.STATUS_FINISHED:
            self.pair.logger.info(
                "order FINISHED: {'name': '%s', 'pair': '%s', 'side': '%s', 'orderid': '%s'}",
                self.pair.cfg["name"],
                self.symbol,
                self.current_order["side"],
                self.order["id"],
            )
            self.order_history = self.current_order
            self.write_last_order_history()
            if not self._is_shutting_down():
                taker_sym = self.order.get("taker")
                if taker_sym == self.t1.symbol:
                    await self.t1.dex.request_addr()
                elif taker_sym == self.t2.symbol:
                    await self.t2.dex.request_addr()
            await self.pair.config_manager.strategy_instance.handle_finished_order(
                self, disabled_coins
            )
        elif status == self.STATUS_OTHERS:
            self.check_price_in_range(display=display)
        elif status == self.STATUS_ERROR_SWAP:
            await self.pair.config_manager.strategy_instance.handle_error_swap_status(
                self
            )
        elif status is not None and not self.disabled:
            self.pair.logger.info(
                "Order %s is %s. Re-initializing order for %s.",
                self.order.get("id"),
                self.order.get("status"),
                self.symbol,
            )
            self.order = None
            self.init_virtual_order(disabled_coins, display=False)
            await self.create_order()

    def _is_shutting_down(self) -> bool:
        try:
            return bool(
                self.pair.config_manager
                and self.pair.config_manager.controller
                and self.pair.config_manager.controller.shutdown_event.is_set()
            )
        except Exception:
            return False


class CexPair:
    def __init__(self, pair: Pair) -> None:
        self.pair = pair
        self.t1, self.t2, self.symbol = pair.t1, pair.t2, pair.symbol
        self.price: float | None = None
        self.cex_orderbook: dict[str, Any] | None = None
        self.cex_orderbook_timer: float | None = None

    async def update_pricing(self, display=False):
        if self.t1.cex.cex_price is None:
            await self.t1.cex.update_price()
        if self.t2.cex.cex_price is None:
            await self.t2.cex.update_price()
        if self.t1.cex.cex_price is not None and self.t2.cex.cex_price:
            self.price = self.t1.cex.cex_price / self.t2.cex.cex_price
        else:
            self.price = None
        if display:
            self.pair.logger.info(
                "update_pricing: %s btc_p: %s, %s btc_p: %s, %s price: %s",
                self.t1.symbol,
                self.t1.cex.cex_price,
                self.t2.symbol,
                self.t2.cex.cex_price,
                self.symbol,
                self.price,
            )

    async def update_orderbook(self, limit=25, ignore_timer=False):
        if (
                ignore_timer
                or not self.cex_orderbook_timer
                or time.time() - self.cex_orderbook_timer > 2
        ):
            try:
                self.cex_orderbook = (
                    await self.pair.ccxt_manager.ccxt_call_fetch_order_book(
                        self.pair.config_manager.my_ccxt, self.symbol, limit
                    )
                )
                self.cex_orderbook_timer = time.time()
            except Exception as e:
                await self.pair.error_handler.handle_async(
                    e, context={"pair": self.symbol, "stage": "update_orderbook"}
                )
                self.cex_orderbook_timer = None
