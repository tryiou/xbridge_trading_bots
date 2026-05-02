from dataclasses import dataclass
from enum import Enum


class SpreadMode(Enum):
    EXPONENTIAL = "exponential"
    LINEAR = "linear"


@dataclass
class PriceLevel:
    level: int
    side: str
    price: float
    amount: float


class PricingEngine:
    def __init__(
        self,
        mid_price: float,
        max_open_orders: int = 10,
        spread_mode: str = "exponential",
        base_spread_percent: float = 1.0,
        spread_multiplier: float = 2.0,
        spread_increment: float = 0.5,
        max_price_range_percent: float = 50.0,
        volatility_adjustment: bool = True,
        volatility_multiplier: float = 1.0,
        min_spread: float = 0.5,
    ):
        self.mid_price = mid_price
        self.max_open_orders = max_open_orders
        self.spread_mode = SpreadMode(spread_mode)
        self.base_spread_percent = base_spread_percent / 100.0
        self.spread_multiplier = spread_multiplier
        self.spread_increment = spread_increment / 100.0
        self.max_price_range_percent = max_price_range_percent / 100.0
        self.volatility_adjustment = volatility_adjustment
        self.volatility_multiplier = volatility_multiplier
        self.min_spread = min_spread / 100.0
        self.price_skew: float = 0.002
        self.price_history: list[float] = []
        self.atr_percent: float = 0.0

    def configure_spread(self, config: dict):
        self.volatility_adjustment = config.get("volatility_adjustment", True)
        self.volatility_multiplier = config.get("volatility_multiplier", 1.0)
        self.price_skew = config.get("price_skew", 0.002)

    def update_mid_price(self, new_price: float):
        self.price_history.append(new_price)
        if len(self.price_history) > 100:
            self.price_history.pop(0)
        self._calculate_atr_percent()
        self.mid_price = new_price

    def _calculate_atr_percent(self):
        if len(self.price_history) < 2:
            self.atr_percent = 0.0
            return

        changes = [
            abs(self.price_history[i] - self.price_history[i - 1])
            for i in range(1, len(self.price_history))
        ]
        avg_change = sum(changes) / len(changes)

        if self.mid_price > 0:
            self.atr_percent = avg_change / self.mid_price

    def get_effective_spread_percent(self) -> float:
        if not self.volatility_adjustment:
            return self.base_spread_percent

        effective = self.base_spread_percent * self.volatility_multiplier

        if self.atr_percent > 0:
            volatility_spread = self.atr_percent * self.volatility_multiplier
            effective = max(effective, volatility_spread)

        effective = max(effective, self.min_spread)
        return effective

    def calculate_buy_price(self, level: int, skew: float = 0.0) -> float:
        spread = self._calculate_spread(level)
        effective_spread = self.get_effective_spread_percent()
        adjusted_spread = spread + effective_spread + (skew * self.price_skew)
        return self.mid_price * (1 - adjusted_spread)

    def calculate_sell_price(self, level: int, skew: float = 0.0) -> float:
        spread = self._calculate_spread(level)
        effective_spread = self.get_effective_spread_percent()
        adjusted_spread = spread + effective_spread - (skew * self.price_skew)
        return self.mid_price * (1 + adjusted_spread)

    def _calculate_spread(self, level: int) -> float:
        level = max(level, 1)

        if self.spread_mode == SpreadMode.EXPONENTIAL:
            return self.base_spread_percent * (self.spread_multiplier ** (level - 1))
        else:
            return self.base_spread_percent + (self.spread_increment * (level - 1))

    def get_price_levels(self, num_levels: int | None = None) -> list[PriceLevel]:
        if num_levels is None:
            num_levels = self.max_open_orders // 2

        levels = []
        buy_levels = min(num_levels, self.max_open_orders // 2)
        sell_levels = min(num_levels, (self.max_open_orders + 1) // 2)

        for level in range(1, buy_levels + 1):
            if level == 1:
                spread = self.base_spread_percent
            else:
                spread = self._calculate_spread(level)

            max_spread = self.max_price_range_percent
            spread = min(spread, max_spread)

            price = self.mid_price * (1 - spread)
            levels.append(PriceLevel(level=level, side="buy", price=price, amount=0.0))

        for level in range(1, sell_levels + 1):
            if level == 1:
                spread = self.base_spread_percent
            else:
                spread = self._calculate_spread(level)

            max_spread = self.max_price_range_percent
            spread = min(spread, max_spread)

            price = self.mid_price * (1 + spread)
            levels.append(PriceLevel(level=level, side="sell", price=price, amount=0.0))

        return levels

    def validate_price(self, price: float, side: str) -> bool:
        if side == "buy":
            min_price = self.mid_price * (1 - self.max_price_range_percent)
            return price >= min_price and price <= self.mid_price
        else:
            max_price = self.mid_price * (1 + self.max_price_range_percent)
            return price >= self.mid_price and price <= max_price
