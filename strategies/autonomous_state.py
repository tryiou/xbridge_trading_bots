import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ruamel.yaml import YAML


@dataclass
class TradeRecord:
    id: int
    side: str
    amount: float
    price: float
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    balance_a_after: float = 0.0
    balance_b_after: float = 0.0


class TradeRecorder:
    def __init__(self, pair_name: str, data_dir: str):
        self.pair_name = pair_name.replace("/", "_")
        self.data_dir = data_dir
        self.history_file = os.path.join(
            data_dir, f"autonomous_{self.pair_name}_history.yaml"
        )
        self.trades: list[TradeRecord] = []
        self.next_id = 1
        self._load_history()

    def _load_history(self):
        if not os.path.exists(self.history_file):
            return

        yaml = YAML()
        yaml.preserve_quotes = True

        try:
            with open(self.history_file, "r") as f:
                data = yaml.load(f)

            if data and "trades" in data:
                for trade_data in data["trades"]:
                    trade = TradeRecord(
                        id=trade_data.get("id", 0),
                        side=trade_data.get("side", ""),
                        amount=trade_data.get("amount", 0.0),
                        price=trade_data.get("price", 0.0),
                        timestamp=trade_data.get("timestamp", ""),
                        balance_a_after=trade_data.get("balance_a_after", 0.0),
                        balance_b_after=trade_data.get("balance_b_after", 0.0),
                    )
                    self.trades.append(trade)
                    if trade.id >= self.next_id:
                        self.next_id = trade.id + 1
        except Exception as e:
            raise RuntimeError(
                f"Failed to load trade history from {self.history_file}: {e}"
            )

    def record_trade(
        self,
        side: str,
        amount: float,
        price: float,
        balance_a_after: float,
        balance_b_after: float,
    ) -> TradeRecord:
        trade = TradeRecord(
            id=self.next_id,
            side=side,
            amount=amount,
            price=price,
            balance_a_after=balance_a_after,
            balance_b_after=balance_b_after,
        )
        self.next_id += 1
        self.trades.append(trade)
        self._save_history()
        return trade

    def _save_history(self):
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.default_flow_style = False

        data = {
            "trades": [
                {
                    "id": t.id,
                    "side": t.side,
                    "amount": t.amount,
                    "price": t.price,
                    "timestamp": t.timestamp,
                    "balance_a_after": t.balance_a_after,
                    "balance_b_after": t.balance_b_after,
                }
                for t in self.trades
            ]
        }

        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
        with open(self.history_file, "w") as f:
            yaml.dump(data, f)

    def get_trades(self) -> list[TradeRecord]:
        return self.trades


@dataclass
class StrategyState:
    mid_price: float
    trade_count: int
    last_updated: str
    initial_mid_price: float
    config_hash: str = ""


class StateManager:
    def __init__(self, pair_name: str, data_dir: str):
        self.pair_name = pair_name.replace("/", "_")
        self.data_dir = data_dir
        self.state_file = os.path.join(
            data_dir, f"autonomous_{self.pair_name}_state.yaml"
        )
        self.orders_file = os.path.join(
            data_dir, f"autonomous_{self.pair_name}_orders.yaml"
        )
        self.state: StrategyState | None = None

    def load_state(self) -> StrategyState | None:
        if not os.path.exists(self.state_file):
            return None

        yaml = YAML()
        yaml.preserve_quotes = True

        try:
            with open(self.state_file, "r") as f:
                data = yaml.load(f)

            if data:
                self.state = StrategyState(
                    mid_price=data.get("mid_price", 0.0),
                    trade_count=data.get("trade_count", 0),
                    last_updated=data.get("last_updated", ""),
                    initial_mid_price=data.get("initial_mid_price", 0.0),
                    config_hash=data.get("config_hash", ""),
                )
                return self.state
        except Exception as e:
            raise RuntimeError(f"Failed to load state from {self.state_file}: {e}")

        return None

    def save_state(
        self,
        mid_price: float,
        trade_count: int,
        initial_mid_price: float,
        config_hash: str = "",
    ):
        self.state = StrategyState(
            mid_price=mid_price,
            trade_count=trade_count,
            last_updated=datetime.utcnow().isoformat(),
            initial_mid_price=initial_mid_price,
            config_hash=config_hash,
        )

        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.default_flow_style = False

        data = {
            "mid_price": self.state.mid_price,
            "trade_count": self.state.trade_count,
            "last_updated": self.state.last_updated,
            "initial_mid_price": self.state.initial_mid_price,
            "config_hash": self.state.config_hash,
        }

        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        with open(self.state_file, "w") as f:
            yaml.dump(data, f)

    def save_orders(self, orders_data: dict[str, Any]):
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.default_flow_style = False

        os.makedirs(os.path.dirname(self.orders_file), exist_ok=True)
        with open(self.orders_file, "w") as f:
            yaml.dump(orders_data, f)

    def load_orders(self) -> dict[str, Any]:
        if not os.path.exists(self.orders_file):
            return {"open_orders": []}

        yaml = YAML()
        yaml.preserve_quotes = True

        try:
            with open(self.orders_file, "r") as f:
                return yaml.load(f) or {"open_orders": []}
        except Exception as e:
            raise RuntimeError(f"Failed to load orders from {self.orders_file}: {e}")

    def delete_state(self):
        for f in [self.state_file, self.orders_file]:
            if os.path.exists(f):
                os.remove(f)
