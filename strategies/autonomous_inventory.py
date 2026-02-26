import math


class InventoryManager:
    def __init__(
        self,
        initial_balance_a: float,
        initial_balance_b: float,
        token_a_symbol: str,
        token_b_symbol: str,
        mid_price: float = 0.0,
    ):
        self.initial_balance_a = initial_balance_a
        self.initial_balance_b = initial_balance_b
        self.current_balance_a = initial_balance_a
        self.current_balance_b = initial_balance_b
        self.token_a_symbol = token_a_symbol
        self.token_b_symbol = token_b_symbol
        self.mid_price = mid_price

        self.target_ratio_a: float = 0.50
        self.skew_sensitivity: float = 0.15

    def configure_risk(self, config: dict):
        self.target_ratio_a = config.get("target_ratio_a", 0.50)
        self.skew_sensitivity = config.get("skew_sensitivity", 0.15)

    def update_balance_a(self, amount: float):
        self.current_balance_a = amount

    def update_balance_b(self, amount: float):
        self.current_balance_b = amount

    def update_mid_price(self, price: float):
        self.mid_price = price

    def apply_trade(self, side: str, amount: float, price: float):
        if side == "sell":
            self.current_balance_a -= amount
            self.current_balance_b += amount * price
        else:
            self.current_balance_a += amount
            self.current_balance_b -= amount * price

    def get_total_value_b(self) -> float:
        if self.mid_price <= 0:
            return self.current_balance_b
        return self.current_balance_a * self.mid_price + self.current_balance_b

    def get_ratio_a(self) -> float:
        total = self.get_total_value_b()
        if total <= 0:
            return 0.50
        value_a = self.current_balance_a * self.mid_price
        return value_a / total

    def calculate_skew(self) -> float:
        current_ratio = self.get_ratio_a()
        deviation = current_ratio - self.target_ratio_a
        return math.tanh(deviation / self.skew_sensitivity)

    def get_total_initial_value_b(self) -> float:
        if self.mid_price <= 0:
            return self.initial_balance_b
        return self.initial_balance_a * self.mid_price + self.initial_balance_b

    def get_drawdown(self) -> float:
        initial_value = self.get_total_initial_value_b()
        if initial_value <= 0:
            return 0.0
        current_value = self.current_balance_a * self.mid_price + self.current_balance_b
        return (initial_value - current_value) / initial_value

    def to_dict(self) -> dict:
        return {
            "initial_balance_a": self.initial_balance_a,
            "initial_balance_b": self.initial_balance_b,
            "current_balance_a": self.current_balance_a,
            "current_balance_b": self.current_balance_b,
            "token_a_symbol": self.token_a_symbol,
            "token_b_symbol": self.token_b_symbol,
            "mid_price": self.mid_price,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InventoryManager":
        return cls(
            initial_balance_a=data.get("initial_balance_a", 0.0),
            initial_balance_b=data.get("initial_balance_b", 0.0),
            token_a_symbol=data.get("token_a_symbol", "TOKEN_A"),
            token_b_symbol=data.get("token_b_symbol", "TOKEN_B"),
            mid_price=data.get("mid_price", 0.0),
        )

    def get_available_for_trading(
        self, min_reserve_pct: float, sensitivity: float
    ) -> tuple[float, float]:
        if self.mid_price <= 0:
            return 0.0, 0.0

        value_a = self.current_balance_a * self.mid_price
        value_b = self.current_balance_b
        total_value = value_a + value_b

        if total_value <= 0:
            return 0.0, 0.0

        ratio_a = value_a / total_value
        deviation = abs(ratio_a - 0.5) * 2
        reduction = deviation**sensitivity
        available_pct = max(min_reserve_pct, 1.0 - reduction)

        return (
            self.current_balance_a * available_pct,
            self.current_balance_b * available_pct,
        )
