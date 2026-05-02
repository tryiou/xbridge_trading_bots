import os
import tempfile

import pytest

from strategies.autonomous_state import (
    StateManager,
    StrategyState,
    TradeRecord,
    TradeRecorder,
)


class TestTradeRecord:
    """Tests for TradeRecord dataclass."""

    def test_creation(self):
        record = TradeRecord(
            id=1,
            side="buy",
            amount=0.1,
            price=100.0,
            balance_a_after=1.1,
            balance_b_after=90.0,
        )
        assert record.id == 1
        assert record.side == "buy"
        assert record.amount == 0.1
        assert record.price == 100.0
        assert record.balance_a_after == 1.1
        assert record.balance_b_after == 90.0

    def test_default_values(self):
        record = TradeRecord(
            id=1,
            side="sell",
            amount=0.1,
            price=100.0,
        )
        assert record.balance_a_after == 0.0
        assert record.balance_b_after == 0.0
        assert record.timestamp is not None


class TestStrategyState:
    """Tests for StrategyState dataclass."""

    def test_creation(self):
        state = StrategyState(
            mid_price=100.0,
            trade_count=10,
            last_updated="2024-01-01T00:00:00Z",
            initial_mid_price=95.0,
            config_hash="abc123",
        )
        assert state.mid_price == 100.0
        assert state.trade_count == 10
        assert state.last_updated == "2024-01-01T00:00:00Z"
        assert state.initial_mid_price == 95.0
        assert state.config_hash == "abc123"


class TestTradeRecorder:
    """Tests for TradeRecorder class."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def recorder(self, temp_dir):
        return TradeRecorder("BTC_LTC", temp_dir)

    def test_initialization(self, recorder, temp_dir):
        assert recorder.pair_name == "BTC_LTC"
        assert recorder.data_dir == temp_dir
        assert len(recorder.trades) == 0
        assert recorder.next_id == 1

    def test_record_trade(self, recorder):
        trade = recorder.record_trade(
            side="buy",
            amount=0.1,
            price=100.0,
            balance_a_after=1.1,
            balance_b_after=90.0,
        )
        assert trade.id == 1
        assert trade.side == "buy"
        assert len(recorder.trades) == 1
        assert recorder.next_id == 2

    def test_record_multiple_trades(self, recorder):
        recorder.record_trade(
            side="buy",
            amount=0.1,
            price=100.0,
            balance_a_after=1.1,
            balance_b_after=90.0,
        )
        recorder.record_trade(
            side="sell",
            amount=0.15,
            price=101.0,
            balance_a_after=1.25,
            balance_b_after=78.5,
        )
        assert len(recorder.trades) == 2
        assert recorder.next_id == 3

    def test_get_trades(self, recorder):
        recorder.record_trade(
            side="buy",
            amount=0.1,
            price=100.0,
            balance_a_after=1.1,
            balance_b_after=90.0,
        )
        trades = recorder.get_trades()
        assert len(trades) == 1

    def test_persistence(self, temp_dir):
        recorder1 = TradeRecorder("BTC_LTC", temp_dir)
        recorder1.record_trade(
            side="buy",
            amount=0.1,
            price=100.0,
            balance_a_after=1.1,
            balance_b_after=90.0,
        )
        recorder2 = TradeRecorder("BTC_LTC", temp_dir)
        assert len(recorder2.trades) == 1
        assert recorder2.next_id == 2

    def test_pair_name_slash_replacement(self, temp_dir):
        recorder = TradeRecorder("LTC/DOGE", temp_dir)
        assert recorder.pair_name == "LTC_DOGE"


class TestStateManager:
    """Tests for StateManager class."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def manager(self, temp_dir):
        return StateManager("BTC_LTC", temp_dir)

    def test_initialization(self, manager, temp_dir):
        assert manager.pair_name == "BTC_LTC"
        assert manager.data_dir == temp_dir
        assert manager.state_file.endswith("autonomous_BTC_LTC_state.yaml")
        assert manager.orders_file.endswith("autonomous_BTC_LTC_orders.yaml")

    def test_load_state_nonexistent(self, manager):
        state = manager.load_state()
        assert state is None

    def test_save_and_load_state(self, manager):
        manager.save_state(
            mid_price=100.0,
            trade_count=10,
            initial_mid_price=95.0,
            config_hash="abc123",
        )
        state = manager.load_state()
        assert state is not None
        assert state.mid_price == 100.0
        assert state.trade_count == 10
        assert state.initial_mid_price == 95.0
        assert state.config_hash == "abc123"

    def test_save_orders(self, manager):
        orders_data = {"open_orders": [{"id": "order1", "side": "buy", "price": 99.0}]}
        manager.save_orders(orders_data)
        loaded = manager.load_orders()
        assert "open_orders" in loaded
        assert len(loaded["open_orders"]) == 1

    def test_load_orders_nonexistent(self, manager):
        loaded = manager.load_orders()
        assert "open_orders" in loaded
        assert len(loaded["open_orders"]) == 0

    def test_delete_state(self, manager):
        manager.save_state(
            mid_price=100.0,
            trade_count=10,
            initial_mid_price=95.0,
        )
        manager.delete_state()
        assert not os.path.exists(manager.state_file)
        assert not os.path.exists(manager.orders_file)

    def test_pair_name_slash_replacement(self, temp_dir):
        manager = StateManager("LTC/DOGE", temp_dir)
        assert manager.pair_name == "LTC_DOGE"


class TestStateManagerEdgeCases:
    """Tests for edge cases in StateManager."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_save_state_creates_directory(self, temp_dir):
        nested_dir = os.path.join(temp_dir, "nested", "path")
        manager = StateManager("BTC_LTC", nested_dir)
        manager.save_state(
            mid_price=100.0,
            trade_count=0,
            initial_mid_price=100.0,
        )
        assert os.path.exists(manager.state_file)

    def test_load_corrupted_state_file(self, temp_dir):
        manager = StateManager("BTC_LTC", temp_dir)
        with open(manager.state_file, "w") as f:
            f.write("invalid: yaml: content [[[")
        with pytest.raises(RuntimeError):
            manager.load_state()

    def test_load_corrupted_orders_file(self, temp_dir):
        manager = StateManager("BTC_LTC", temp_dir)
        with open(manager.orders_file, "w") as f:
            f.write("invalid: yaml: content [[[")
        with pytest.raises(RuntimeError):
            manager.load_orders()
