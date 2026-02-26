from enum import Enum
import math


class OrderSizingMode(Enum):
    EQUAL = "equal"
    GROWING_OUTWARD = "growing_outward"
    GROWING_INWARD = "growing_inward"
    MANUAL = "manual"


class OrderSizingEngine:
    def __init__(
        self,
        mode: str = "equal",
        base_percent: float = 0.05,
        buy_amounts: list[float] = None,
        sell_amounts: list[float] = None,
    ):
        self.mode = OrderSizingMode(mode)
        self.base_percent = base_percent
        self.buy_amounts = buy_amounts or []
        self.sell_amounts = sell_amounts or []
        self.skew_intensity: float = 0.5
        self.price_skew: float = 0.002

    def configure_skew(self, intensity: float, price_skew: float = 0.002):
        self.skew_intensity = intensity
        self.price_skew = price_skew

    def calculate_amounts(
        self,
        side: str,
        level: int,
        total_balance: float,
        remaining_levels: int,
        skew: float = 0.0,
    ) -> float:
        if remaining_levels <= 0:
            return 0.0

        base_amount = self._calculate_base_amount(
            side, level, total_balance, remaining_levels
        )

        adjusted = self._apply_skew(side, base_amount, skew)
        return max(0.0, adjusted)

    def _calculate_base_amount(
        self,
        side: str,
        level: int,
        total_balance: float,
        remaining_levels: int,
    ) -> float:
        if self.mode == OrderSizingMode.MANUAL:
            amounts = self.buy_amounts if side == "buy" else self.sell_amounts
            if 0 < level <= len(amounts):
                return amounts[level - 1]
            return 0.0

        base_amount = total_balance * self.base_percent

        if self.mode == OrderSizingMode.EQUAL:
            return base_amount

        if self.mode == OrderSizingMode.GROWING_OUTWARD:
            multiplier = 1.0 + (level - 1) * 0.5
            return base_amount * multiplier

        if self.mode == OrderSizingMode.GROWING_INWARD:
            multiplier = 1.5 - (level - 1) * 0.25
            return base_amount * max(multiplier, 0.25)

        return base_amount

    def _apply_skew(self, side: str, base_amount: float, skew: float) -> float:
        if abs(skew) < 0.001:
            return base_amount

        if side == "buy":
            multiplier = 1 - skew * self.skew_intensity
        else:
            multiplier = 1 + skew * self.skew_intensity

        multiplier = max(0.1, min(2.0, multiplier))
        return base_amount * multiplier

    def calculate_buy_price_with_skew(
        self, mid_price: float, level: int, base_spread: float, skew: float
    ) -> float:
        spread = base_spread + (skew * self.price_skew)
        return mid_price * (1 - spread)

    def calculate_sell_price_with_skew(
        self, mid_price: float, level: int, base_spread: float, skew: float
    ) -> float:
        spread = base_spread - (skew * self.price_skew)
        return mid_price * (1 + spread)
