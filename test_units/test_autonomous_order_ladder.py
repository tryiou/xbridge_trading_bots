import pytest

from strategies.autonomous_order_ladder import ManagedOrder, OrderLadder


class TestManagedOrder:
    """Tests for ManagedOrder dataclass."""

    def test_creation(self):
        order = ManagedOrder(
            id="test-id",
            level=1,
            side="buy",
            price=100.0,
            amount=0.5,
        )
        assert order.id == "test-id"
        assert order.level == 1
        assert order.side == "buy"
        assert order.price == 100.0
        assert order.amount == 0.5
        assert order.status == "open"

    def test_creation_with_status(self):
        order = ManagedOrder(
            id="test-id",
            level=1,
            side="buy",
            price=100.0,
            amount=0.5,
            status="finished",
        )
        assert order.status == "finished"


class TestOrderLadder:
    """Tests for OrderLadder order management."""

    @pytest.fixture
    def ladder(self):
        return OrderLadder(max_orders=10)

    def test_add_order(self, ladder):
        order = ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        assert order is not None
        assert "order-1" in ladder.orders

    def test_remove_order_existing(self, ladder):
        ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        result = ladder.remove_order("order-1")
        assert result is True
        assert "order-1" not in ladder.orders

    def test_remove_order_nonexistent(self, ladder):
        result = ladder.remove_order("nonexistent")
        assert result is False

    def test_get_order_existing(self, ladder):
        ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        order = ladder.get_order("order-1")
        assert order is not None
        assert order.level == 1

    def test_get_order_nonexistent(self, ladder):
        order = ladder.get_order("nonexistent")
        assert order is None

    def test_get_orders_by_side(self, ladder):
        ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        ladder.add_order(
            level=1, side="sell", price=101.0, amount=0.1, order_id="order-2"
        )
        buy_orders = ladder.get_orders_by_side("buy")
        assert len(buy_orders) == 1

    def test_get_buy_orders(self, ladder):
        ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        orders = ladder.get_buy_orders()
        assert len(orders) == 1
        assert orders[0].side == "buy"

    def test_get_sell_orders(self, ladder):
        ladder.add_order(
            level=1, side="sell", price=101.0, amount=0.1, order_id="order-1"
        )
        orders = ladder.get_sell_orders()
        assert len(orders) == 1
        assert orders[0].side == "sell"

    def test_get_open_orders(self, ladder):
        ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        ladder.add_order(
            level=2, side="buy", price=98.0, amount=0.1, order_id="order-2"
        )
        orders = ladder.get_open_orders()
        assert len(orders) == 2

    def test_num_buy_orders(self, ladder):
        ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        ladder.add_order(
            level=2, side="buy", price=98.0, amount=0.1, order_id="order-2"
        )
        assert ladder.num_buy_orders == 2

    def test_num_sell_orders(self, ladder):
        ladder.add_order(
            level=1, side="sell", price=101.0, amount=0.1, order_id="order-1"
        )
        ladder.add_order(
            level=2, side="sell", price=102.0, amount=0.1, order_id="order-2"
        )
        assert ladder.num_sell_orders == 2

    def test_num_open_orders(self, ladder):
        ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        ladder.add_order(
            level=1, side="sell", price=101.0, amount=0.1, order_id="order-2"
        )
        assert ladder.num_open_orders == 2

    def test_get_next_buy_level_no_orders(self, ladder):
        level = ladder.get_next_buy_level()
        assert level == 1

    def test_get_next_buy_level_existing(self, ladder):
        ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        ladder.add_order(
            level=2, side="buy", price=98.0, amount=0.1, order_id="order-2"
        )
        level = ladder.get_next_buy_level()
        assert level == 3

    def test_get_next_sell_level_no_orders(self, ladder):
        level = ladder.get_next_sell_level()
        assert level == 1

    def test_get_next_sell_level_existing(self, ladder):
        ladder.add_order(
            level=1, side="sell", price=101.0, amount=0.1, order_id="order-1"
        )
        ladder.add_order(
            level=2, side="sell", price=102.0, amount=0.1, order_id="order-2"
        )
        level = ladder.get_next_sell_level()
        assert level == 3

    def test_can_add_buy_order_true(self, ladder):
        assert ladder.can_add_buy_order() is True

    def test_can_add_buy_order_false(self, ladder):
        for i in range(5):
            ladder.add_order(
                level=i + 1,
                side="buy",
                price=100.0 - i,
                amount=0.1,
                order_id=f"order-buy-{i}",
            )
        assert ladder.can_add_buy_order() is False

    def test_can_add_sell_order_true(self, ladder):
        assert ladder.can_add_sell_order() is True

    def test_can_add_sell_order_false(self, ladder):
        for i in range(5):
            ladder.add_order(
                level=i + 1,
                side="sell",
                price=100.0 + i,
                amount=0.1,
                order_id=f"order-sell-{i}",
            )
        assert ladder.can_add_sell_order() is False

    def test_get_lowest_buy_price(self, ladder):
        ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        ladder.add_order(
            level=2, side="buy", price=98.0, amount=0.1, order_id="order-2"
        )
        price = ladder.get_lowest_buy_price()
        assert price == pytest.approx(98.0)

    def test_get_highest_sell_price(self, ladder):
        ladder.add_order(
            level=1, side="sell", price=101.0, amount=0.1, order_id="order-1"
        )
        ladder.add_order(
            level=2, side="sell", price=102.0, amount=0.1, order_id="order-2"
        )
        price = ladder.get_highest_sell_price()
        assert price == pytest.approx(102.0)

    def test_update_order_status(self, ladder):
        ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        ladder.update_order_status("order-1", "finished")
        order = ladder.get_order("order-1")
        assert order.status == "finished"

    def test_clear_all(self, ladder):
        ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        ladder.add_order(
            level=1, side="sell", price=101.0, amount=0.1, order_id="order-2"
        )
        ladder.clear_all()
        assert len(ladder.orders) == 0


class TestOrderLadderEdgeCases:
    """Edge case tests for OrderLadder."""

    def test_max_orders_even(self):
        ladder = OrderLadder(max_orders=10)
        for i in range(5):
            ladder.add_order(
                level=i + 1,
                side="buy",
                price=100.0 - i,
                amount=0.1,
                order_id=f"order-buy-{i}",
            )
        assert ladder.can_add_buy_order() is False

    def test_max_orders_odd(self):
        ladder = OrderLadder(max_orders=9)
        for i in range(5):
            ladder.add_order(
                level=i + 1,
                side="buy",
                price=100.0 - i,
                amount=0.1,
                order_id=f"order-buy-{i}",
            )
        assert ladder.can_add_buy_order() is False

    def test_get_lowest_buy_returns_minimum(self):
        ladder = OrderLadder()
        ladder.add_order(level=3, side="buy", price=97.0, amount=0.1, order_id="o3")
        ladder.add_order(level=1, side="buy", price=99.0, amount=0.1, order_id="o1")
        ladder.add_order(level=2, side="buy", price=98.0, amount=0.1, order_id="o2")
        assert ladder.get_lowest_buy_price() == pytest.approx(97.0)

    def test_get_highest_sell_returns_maximum(self):
        ladder = OrderLadder()
        ladder.add_order(level=3, side="sell", price=103.0, amount=0.1, order_id="o3")
        ladder.add_order(level=1, side="sell", price=101.0, amount=0.1, order_id="o1")
        ladder.add_order(level=2, side="sell", price=102.0, amount=0.1, order_id="o2")
        assert ladder.get_highest_sell_price() == pytest.approx(103.0)


class TestOrderLadderSerialization:
    """Tests for OrderLadder serialization."""

    def test_to_dict(self):
        ladder = OrderLadder(max_orders=10)
        ladder.add_order(
            level=1, side="buy", price=99.0, amount=0.1, order_id="order-1"
        )
        ladder.add_order(
            level=1, side="sell", price=101.0, amount=0.1, order_id="order-2"
        )
        data = ladder.to_dict()
        assert "open_orders" in data
        assert len(data["open_orders"]) == 2

    def test_from_dict(self):
        data = {
            "open_orders": [
                {
                    "id": "order-1",
                    "level": 1,
                    "side": "buy",
                    "price": 99.0,
                    "amount": 0.1,
                    "created_at": "2024-01-01T00:00:00",
                    "finished_amount": 0.0,
                    "status": "open",
                }
            ]
        }
        ladder = OrderLadder.from_dict(data, max_orders=10)
        assert len(ladder.orders) == 1

    def test_from_dict_empty(self):
        data = {"open_orders": []}
        ladder = OrderLadder.from_dict(data, max_orders=10)
        assert len(ladder.orders) == 0
