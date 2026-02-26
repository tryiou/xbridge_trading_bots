import pytest

from strategies.autonomous_inventory import InventoryManager


class TestInventoryManager:
    """Tests for InventoryManager balance tracking and skew logic."""

    @pytest.fixture
    def manager(self):
        return InventoryManager(
            initial_balance_a=1.0,
            initial_balance_b=100.0,
            token_a_symbol="BTC",
            token_b_symbol="LTC",
            mid_price=50.0,
        )

    def test_initialization(self, manager):
        assert manager.initial_balance_a == 1.0
        assert manager.initial_balance_b == 100.0
        assert manager.current_balance_a == 1.0
        assert manager.current_balance_b == 100.0
        assert manager.mid_price == 50.0
        assert manager.target_ratio_a == 0.50
        assert manager.skew_sensitivity == 0.15

    def test_update_balance_a(self, manager):
        manager.update_balance_a(1.5)
        assert manager.current_balance_a == 1.5

    def test_update_balance_b(self, manager):
        manager.update_balance_b(150.0)
        assert manager.current_balance_b == 150.0

    def test_update_mid_price(self, manager):
        manager.update_mid_price(60.0)
        assert manager.mid_price == 60.0

    def test_apply_trade_sell(self, manager):
        manager.apply_trade("sell", 0.1, 50.0)
        assert manager.current_balance_a == pytest.approx(0.9)
        assert manager.current_balance_b == pytest.approx(105.0)

    def test_apply_trade_buy(self, manager):
        manager.apply_trade("buy", 0.1, 50.0)
        assert manager.current_balance_a == pytest.approx(1.1)
        assert manager.current_balance_b == pytest.approx(95.0)


class TestInventoryManagerValueCalculations:
    """Tests for value and ratio calculations."""

    @pytest.fixture
    def manager(self):
        return InventoryManager(
            initial_balance_a=1.0,
            initial_balance_b=100.0,
            token_a_symbol="BTC",
            token_b_symbol="LTC",
            mid_price=50.0,
        )

    def test_get_total_value_b(self, manager):
        value = manager.get_total_value_b()
        expected = 1.0 * 50.0 + 100.0
        assert value == pytest.approx(expected)

    def test_get_total_value_b_zero_mid_price(self, manager):
        manager.mid_price = 0.0
        value = manager.get_total_value_b()
        assert value == pytest.approx(100.0)

    def test_get_ratio_a(self, manager):
        ratio = manager.get_ratio_a()
        expected = (1.0 * 50.0) / (1.0 * 50.0 + 100.0)
        assert ratio == pytest.approx(expected)

    def test_get_ratio_a_zero_mid_price(self, manager):
        manager.mid_price = 0.0
        ratio = manager.get_ratio_a()
        assert ratio == 0.0

    def test_get_ratio_a_zero_total(self, manager):
        manager.current_balance_a = 0.0
        manager.current_balance_b = 0.0
        ratio = manager.get_ratio_a()
        assert ratio == 0.50


class TestInventorySkew:
    """Tests for skew calculation."""

    @pytest.fixture
    def manager(self):
        m = InventoryManager(
            initial_balance_a=1.0,
            initial_balance_b=100.0,
            token_a_symbol="BTC",
            token_b_symbol="LTC",
            mid_price=50.0,
        )
        m.configure_risk({"target_ratio_a": 0.50, "skew_sensitivity": 0.15})
        return m

    def test_skew_balanced(self, manager):
        manager.current_balance_a = 2.0
        manager.current_balance_b = 100.0
        skew = manager.calculate_skew()
        assert skew == pytest.approx(0.0)

    def test_skew_more_a_than_target(self, manager):
        manager.current_balance_a = 5.0
        manager.current_balance_b = 100.0
        skew = manager.calculate_skew()
        assert skew > 0.0

    def test_skew_less_a_than_target(self, manager):
        manager.current_balance_a = 0.5
        manager.current_balance_b = 100.0
        skew = manager.calculate_skew()
        assert skew < 0.0

    def test_skew_bounded(self, manager):
        manager.current_balance_a = 100.0
        skew = manager.calculate_skew()
        assert -1.0 <= skew <= 1.0


class TestGetAvailableForTrading:
    """Tests for available trading amount calculation."""

    @pytest.fixture
    def manager(self):
        return InventoryManager(
            initial_balance_a=10.0,
            initial_balance_b=1000.0,
            token_a_symbol="BTC",
            token_b_symbol="LTC",
            mid_price=50.0,
        )

    def test_available_with_reserve(self, manager):
        available_a, available_b = manager.get_available_for_trading(0.10, 2.0)
        assert available_a > 0
        assert available_b > 0

    def test_available_zero_balances(self, manager):
        manager.current_balance_a = 0.0
        manager.current_balance_b = 0.0
        available_a, available_b = manager.get_available_for_trading(0.10, 2.0)
        assert available_a == 0.0
        assert available_b == 0.0


class TestInventoryDrawdown:
    """Tests for drawdown calculation."""

    @pytest.fixture
    def manager(self):
        return InventoryManager(
            initial_balance_a=1.0,
            initial_balance_b=100.0,
            token_a_symbol="BTC",
            token_b_symbol="LTC",
            mid_price=50.0,
        )

    def test_drawdown_positive_profit(self, manager):
        manager.current_balance_a = 1.2
        drawdown = manager.get_drawdown()
        assert drawdown < 0.0

    def test_drawdown_negative_loss(self, manager):
        manager.current_balance_a = 0.8
        drawdown = manager.get_drawdown()
        assert drawdown > 0.0


class TestInventoryManagerSerialization:
    """Tests for InventoryManager serialization."""

    def test_to_dict(self):
        manager = InventoryManager(
            initial_balance_a=1.0,
            initial_balance_b=100.0,
            token_a_symbol="BTC",
            token_b_symbol="LTC",
            mid_price=50.0,
        )
        data = manager.to_dict()
        assert data["initial_balance_a"] == 1.0
        assert data["initial_balance_b"] == 100.0
        assert data["token_a_symbol"] == "BTC"
        assert data["token_b_symbol"] == "LTC"
        assert data["mid_price"] == 50.0

    def test_from_dict(self):
        data = {
            "initial_balance_a": 1.0,
            "initial_balance_b": 100.0,
            "token_a_symbol": "BTC",
            "token_b_symbol": "LTC",
            "mid_price": 50.0,
        }
        manager = InventoryManager.from_dict(data)
        assert manager.initial_balance_a == 1.0
        assert manager.initial_balance_b == 100.0
        assert manager.token_a_symbol == "BTC"

    def test_from_dict_with_defaults(self):
        data = {}
        manager = InventoryManager.from_dict(data)
        assert manager.initial_balance_a == 0.0
        assert manager.token_a_symbol == "TOKEN_A"
        assert manager.target_ratio_a == 0.50
