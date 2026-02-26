from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4


@dataclass
class ManagedOrder:
    id: str
    level: int
    side: str
    price: float
    amount: float
    created_at: datetime = field(default_factory=datetime.utcnow)
    finished_amount: float = 0.0
    status: str = "open"

    def is_finished(self) -> bool:
        return self.finished_amount >= self.amount

    def remaining_amount(self) -> float:
        return self.amount - self.finished_amount


class OrderLadder:
    def __init__(self, max_orders: int = 10, partial_percent: float = 0.1):
        self.max_orders = max_orders
        self.partial_percent = partial_percent
        self.orders: dict[str, ManagedOrder] = {}

    def add_order(
        self,
        level: int,
        side: str,
        price: float,
        amount: float,
        order_id: str,
    ) -> ManagedOrder:
        order = ManagedOrder(
            id=order_id,
            level=level,
            side=side,
            price=price,
            amount=amount,
        )
        self.orders[order.id] = order
        return order

    def remove_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            del self.orders[order_id]
            return True
        return False

    def get_order(self, order_id: str) -> ManagedOrder | None:
        return self.orders.get(order_id)

    def get_orders_by_side(self, side: str) -> list[ManagedOrder]:
        return [o for o in self.orders.values() if o.side == side]

    def get_buy_orders(self) -> list[ManagedOrder]:
        return self.get_orders_by_side("buy")

    def get_sell_orders(self) -> list[ManagedOrder]:
        return self.get_orders_by_side("sell")

    def get_open_orders(self) -> list[ManagedOrder]:
        return [o for o in self.orders.values() if o.status == "open"]

    @property
    def num_buy_orders(self) -> int:
        return len(
            [o for o in self.orders.values() if o.side == "buy" and o.status == "open"]
        )

    @property
    def num_sell_orders(self) -> int:
        return len(
            [o for o in self.orders.values() if o.side == "sell" and o.status == "open"]
        )

    @property
    def num_open_orders(self) -> int:
        return len(self.get_open_orders())

    def get_next_buy_level(self) -> int:
        buy_orders = [
            o for o in self.orders.values() if o.side == "buy" and o.status == "open"
        ]
        if not buy_orders:
            return 1
        return max(o.level for o in buy_orders) + 1

    def get_next_sell_level(self) -> int:
        sell_orders = [
            o for o in self.orders.values() if o.side == "sell" and o.status == "open"
        ]
        if not sell_orders:
            return 1
        return max(o.level for o in sell_orders) + 1

    def can_add_buy_order(self) -> bool:
        return self.num_buy_orders < (self.max_orders // 2)

    def can_add_sell_order(self) -> bool:
        return self.num_sell_orders < ((self.max_orders + 1) // 2)

    def can_add_order(self, side: str) -> bool:
        if side == "buy":
            return self.can_add_buy_order()
        return self.can_add_sell_order()

    def get_lowest_buy_price(self) -> float | None:
        buy_orders = self.get_buy_orders()
        if not buy_orders:
            return None
        return min(o.price for o in buy_orders)

    def get_highest_sell_price(self) -> float | None:
        sell_orders = self.get_sell_orders()
        if not sell_orders:
            return None
        return max(o.price for o in sell_orders)

    def update_order_status(self, order_id: str, status: str) -> bool:
        order = self.get_order(order_id)
        if order:
            order.status = status
            return True
        return False

    def record_finish(self, order_id: str, finish_amount: float):
        order = self.get_order(order_id)
        if order:
            order.finished_amount += finish_amount
            if order.is_finished():
                order.status = "finished"

    def clear_all(self):
        self.orders.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "open_orders": [
                {
                    "id": o.id,
                    "level": o.level,
                    "side": o.side,
                    "price": o.price,
                    "amount": o.amount,
                    "created_at": o.created_at.isoformat(),
                    "finished_amount": o.finished_amount,
                    "status": o.status,
                }
                for o in self.orders.values()
            ]
        }

    @classmethod
    def from_dict(
        cls, data: dict, max_orders: int = 10, partial_percent: float = 0.1
    ) -> "OrderLadder":
        ladder = cls(max_orders=max_orders, partial_percent=partial_percent)
        for order_data in data.get("open_orders", []):
            order = ManagedOrder(
                id=order_data["id"],
                level=order_data["level"],
                side=order_data["side"],
                price=order_data["price"],
                amount=order_data["amount"],
                created_at=datetime.fromisoformat(order_data["created_at"]),
                finished_amount=order_data.get("finished_amount", 0.0),
                status=order_data.get("status", "open"),
            )
            ladder.orders[order.id] = order
        return ladder
