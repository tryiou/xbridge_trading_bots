from __future__ import annotations

import logging
import math
import re
import time
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from definitions.constants import XBridgeErrorCode
from definitions.price_update_handler import PriceUpdateHandler
from definitions.yaml_utils import load_yaml, save_yaml

if TYPE_CHECKING:
    from definitions.config_manager import ConfigManager
    from definitions.token import Token


class RecoverState(Enum):
    """Return state for bad-address recovery attempt."""

    NORMAL = auto()
    RECOVERED = auto()
    ALREADY_DISABLED = auto()


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
        self.disable_reason: str | None = None
        self.variation: float | list | None = None
        self.partial_percent = partial_percent
        self.orderbook: dict[str, Any] | None = None
        self.orderbook_timer: float | None = None
        self.order: dict[str, Any] | None = None
        self._bad_address_recovered = False
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
            self.order_history = load_yaml(file_path)
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
            if self.order_history is not None:
                save_yaml(file_path, self.order_history)
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
        stepper = 10.0**digits
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

    def init_virtual_order(self, display=True):
        disabled_coins = self.pair.config_manager.disabled_coins
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
            await self._do_make_order(dry_mode)

            if self.order and "error" in self.order:
                await self._handle_order_error()

        except Exception as e:
            self.pair.logger.debug(
                "[RECOVERY] Pair %s - order creation failed: %s: %s",
                self.symbol,
                type(e).__name__,
                e,
            )
            recovered = await self._try_recover_bad_address(e)
            if recovered is RecoverState.ALREADY_DISABLED:
                self.pair.logger.debug(
                    "[RECOVERY] Pair %s - ALREADY_DISABLED, aborting", self.symbol
                )
                return
            if recovered is RecoverState.RECOVERED:
                self.pair.logger.info(
                    "[RECOVERY] Pair %s - address regenerated, retrying order...",
                    self.symbol,
                )
                try:
                    await self._do_make_order(dry_mode)
                    if self.order and "error" in self.order:
                        await self._handle_order_error()
                except Exception as retry_exc:
                    self.pair.logger.debug(
                        "[RECOVERY] Pair %s - retry failed: %s: %s",
                        self.symbol,
                        type(retry_exc).__name__,
                        retry_exc,
                    )
                    recovered = await self._try_recover_bad_address(retry_exc)
                    if recovered is RecoverState.NORMAL:
                        context = {
                            "pair": self.pair.symbol,
                            "stage": "order_creation_retry",
                            "error": str(retry_exc),
                        }
                        should_continue = await self.pair.error_handler.handle_async(
                            retry_exc, context
                        )
                        if not should_continue:
                            self.disabled = True
                            self.disable_reason = str(retry_exc)
            else:
                self.pair.logger.debug(
                    "[RECOVERY] Pair %s - not a bad-address error, passing to error handler",
                    self.symbol,
                )
                context = {
                    "pair": self.pair.symbol,
                    "stage": "order_creation",
                    "error": str(e),
                }
                retry_or_continue = await self.pair.error_handler.handle_async(
                    e, context
                )
                if not retry_or_continue:
                    self.disabled = True
                    self.disable_reason = str(e)

    async def _do_make_order(self, dry_mode):
        """Execute the actual XBridge makeorder/makepartialorder RPC call."""
        maker, maker_address = (
            self.current_order["maker"],
            self.current_order["maker_address"],
        )
        taker, taker_address = (
            self.current_order["taker"],
            self.current_order["taker_address"],
        )
        maker_size = f"{self.current_order['maker_size']:.6f}"
        taker_size = f"{self.current_order['taker_size']:.6f}"

        self.pair.logger.debug(
            "[RECOVERY] Pair %s - making order: maker=%s (%s), taker=%s (%s)",
            self.symbol,
            maker,
            maker_address,
            taker,
            taker_address,
        )

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
                f"{self.current_order['maker_size']:.6f}",
                maker_address,
                taker,
                taker_size,
                taker_address,
                min_size,
            )
        else:
            self.order = await self.pair.xbridge_manager.makeorder(
                maker,
                f"{self.current_order['maker_size']:.6f}",
                maker_address,
                taker,
                taker_size,
                taker_address,
            )

    async def _try_recover_bad_address(self, exc: Exception) -> RecoverState:
        """Detect bad-address errors and regenerate the affected token's address.

        Returns RecoverState.NORMAL if not a bad-address error (caller handles normally).
        Returns RecoverState.RECOVERED if address was regenerated (caller should retry).
        Returns RecoverState.ALREADY_DISABLED if pair is now disabled (caller returns).
        """
        error_str = str(exc)
        is_bad_addr = "bad address" in error_str.lower()
        self.pair.logger.debug(
            "[RECOVERY] Pair %s - checking exception %s: is_bad_address=%s",
            self.symbol,
            type(exc).__name__,
            is_bad_addr,
        )
        if not is_bad_addr:
            return RecoverState.NORMAL

        if self._bad_address_recovered:
            self.disabled = True
            self.disable_reason = f"Bad address persists after regeneration: {exc}"
            self.pair.logger.warning(
                "[RECOVERY] Pair %s - already recovered once, disabling (second bad address)",
                self.symbol,
            )
            return RecoverState.ALREADY_DISABLED

        self.pair.logger.info(
            "[RECOVERY] Pair %s - regenerating bad address...", self.symbol
        )
        try:
            await self._regenerate_bad_address(error_str)
        except RuntimeError as regen_err:
            # Recovery is impossible (unextractable/unmatched address):
            # terminate loudly instead of escaping and looping forever.
            self.disabled = True
            self.disable_reason = str(regen_err)
            return RecoverState.ALREADY_DISABLED
        return RecoverState.RECOVERED

    @staticmethod
    def _extract_bad_address(error_str):
        """Extract the wallet address from a 'Bad address <addr>' error string.

        The capture stops at the first non-word character, which naturally
        excludes trailing punctuation from repr/str() of Python dicts.
        """
        match = re.search(r"bad\s+address\s+([\w]+)", error_str, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    async def _regenerate_bad_address(self, error_str: str) -> None:
        """Regenerate the token whose address appears in the bad-address error.

        Updates the current_order slot(s) that held the bad address regardless
        of order side (BUY maps maker->t2 / taker->t1, SELL the reverse).
        Raises RuntimeError if the extracted address matches neither token.
        """
        bad_addr = self._extract_bad_address(error_str)
        if not bad_addr:
            self.pair.logger.critical(
                "[RECOVERY] Pair %s - FAILED to extract bad address from: %s",
                self.symbol,
                error_str[:300],
            )
            raise RuntimeError(f"Could not extract bad address from: {error_str}")

        token = None
        if bad_addr == self.t1.dex.address:
            token = self.t1
        elif bad_addr == self.t2.dex.address:
            token = self.t2
        else:
            self.pair.logger.critical(
                "[RECOVERY] Pair %s - bad address %s matched neither token",
                self.symbol,
                bad_addr,
            )
            raise RuntimeError(
                f"Bad address {bad_addr} matched neither token for {self.symbol}"
            )

        await token.dex.request_addr()
        for slot in ("maker_address", "taker_address"):
            if self.current_order[slot] == bad_addr:
                self.current_order[slot] = token.dex.address
        self._bad_address_recovered = True

    async def _handle_order_error(self):
        original_error = self.order
        error_code = original_error.get("code")

        if error_code == XBridgeErrorCode.BAD_ADDRESS:
            recovered = await self._try_recover_bad_address_from_response(
                original_error
            )
            if recovered is RecoverState.RECOVERED:
                # Drop the id-less error dict so the next cycle places a fresh order.
                self.order = None
            return

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

    async def _try_recover_bad_address_from_response(
        self, order_error: dict
    ) -> RecoverState:
        """Recovery path for response-based bad-address errors (code 1026)."""
        if self._bad_address_recovered:
            self.disabled = True
            self.disable_reason = (
                f"Bad address persists after regeneration: {order_error}"
            )
            self.pair.logger.warning(
                "[RECOVERY] Pair %s - already recovered once, disabling (second bad address, response path)",
                self.symbol,
            )
            return RecoverState.ALREADY_DISABLED

        error_msg = str(order_error)
        self.pair.logger.info(
            "[RECOVERY] Pair %s - regenerating bad address (response path)...",
            self.symbol,
        )
        try:
            await self._regenerate_bad_address(error_msg)
        except RuntimeError as regen_err:
            self.disabled = True
            self.disable_reason = str(regen_err)
            return RecoverState.ALREADY_DISABLED
        return RecoverState.RECOVERED

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

    async def check_price_variation(self, display=False):
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
                self
            )

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
        if not hasattr(self.pair, "_price_update_handler"):
            self.pair._price_update_handler = PriceUpdateHandler(
                self.pair.ccxt_manager, self.pair.config_manager
            )
        price_handler = self.pair._price_update_handler
        if self.t1.cex.cex_price is None:
            await price_handler.update(self.t1.cex)
        if self.t2.cex.cex_price is None:
            await price_handler.update(self.t2.cex)
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
                        self.pair.config_manager.ccxt_manager.my_ccxt,
                        self.symbol,
                        limit,
                    )
                )
                self.cex_orderbook_timer = time.time()
            except Exception as e:
                await self.pair.error_handler.handle_async(
                    e, context={"pair": self.symbol, "stage": "update_orderbook"}
                )
                self.cex_orderbook_timer = None
