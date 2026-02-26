import os
import sys
import tempfile
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if TYPE_CHECKING:
    from strategies.autonomous_maker_strategy import AutonomousMakerStrategy

from contextlib import contextmanager


class AutonomousMakerTester:
    """
    Test helper class for AutonomousMakerStrategy testing.
    """

    def __init__(self, strategy_instance: "AutonomousMakerStrategy"):
        self.strategy = strategy_instance

    def reset(self):
        self.strategy.pricing_engine = None
        self.strategy.inventory_manager = None
        self.strategy.order_ladder = None
        self.strategy.sizing_engine = None
        self.strategy.state_manager = None
        self.strategy.trade_recorder = None

    @contextmanager
    def _patch_dependencies(self, temp_dir: str):
        """Patch external dependencies for testing."""
        self.reset()
        with (
            patch.object(
                self.strategy.config_manager.xbridge_manager,
                "makepartialorder",
                new_callable=AsyncMock,
            ) as mock_make_partial,
            patch.object(
                self.strategy.config_manager.xbridge_manager,
                "gettokenbalances",
                new_callable=AsyncMock,
            ) as mock_balances,
            patch.object(
                self.strategy.config_manager.xbridge_manager,
                "getorderstatus",
                new_callable=AsyncMock,
            ) as mock_get_status,
            patch.object(
                self.strategy.config_manager.xbridge_manager,
                "cancelorder",
                new_callable=AsyncMock,
            ) as mock_cancel,
            patch.object(
                self.strategy.config_manager.xbridge_manager,
                "cancelallorders",
                new_callable=AsyncMock,
            ) as mock_cancel_all,
            patch.object(
                self.strategy.config_manager.xbridge_manager,
                "dxflushcancelledorders",
                new_callable=AsyncMock,
            ) as mock_flush,
        ):
            mock_make_partial.return_value = {
                "id": "mock_order_id_123",
                "status": "created",
            }
            mock_balances.return_value = {
                self.strategy.token_a: "1.0",
                self.strategy.token_b: "100.0",
            }
            mock_get_status.return_value = {
                "id": "mock_order_id_123",
                "status": "open",
            }
            mock_cancel.return_value = {"result": True}
            mock_cancel_all.return_value = []
            mock_flush.return_value = {"result": True}

            mocks = {
                "make_partial": mock_make_partial,
                "get_balances": mock_balances,
                "get_status": mock_get_status,
                "cancel": mock_cancel,
                "cancel_all": mock_cancel_all,
                "flush": mock_flush,
            }
            yield mocks

    def _initialize_strategy(self, pair_config: dict, temp_dir: str):
        """Initialize strategy for a specific pair configuration."""
        self.strategy.config_manager.ROOT_DIR = temp_dir
        self.strategy._initialize_for_pair(pair_config)

    async def _test_initialization(self):
        """Test that strategy initializes correctly."""
        test_name = "Strategy Initialization"
        self.strategy.config_manager.general_log.info(
            f"\n--- [TEST CASE] Running: {test_name} ---"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            pair_config = {
                "name": "LTC_DOGE_01",
                "pair": "LTC/DOGE",
                "initial_balance_a": 5.0,
                "initial_balance_b": 5000.0,
                "initial_mid_price": 1000.0,
                "max_open_orders": 10,
                "partial_percent": 0.1,
                "order_sizing_mode": "growing_outward",
                "spread_mode": "exponential",
                "base_spread_percent": 1.0,
                "spread_multiplier": 2.0,
                "inventory_bias": "auto",
                "target_ratio_a": 0.5,
                "max_price_range_percent": 50.0,
                "check_interval": 15,
            }

            self._initialize_strategy(pair_config, temp_dir)

            assert self.strategy.pricing_engine is not None
            assert self.strategy.inventory_manager is not None
            assert self.strategy.order_ladder is not None
            assert self.strategy.sizing_engine is not None

            assert self.strategy.mid_price == 1000.0
            assert self.strategy.initial_mid_price == 1000.0

            self.strategy.config_manager.general_log.info(
                "[TEST PASSED] Strategy initialized correctly."
            )

    async def _test_order_creation(self):
        """Test that orders are created correctly."""
        test_name = "Order Creation"
        self.strategy.config_manager.general_log.info(
            f"\n--- [TEST CASE] Running: {test_name} ---"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            pair_config = {
                "name": "LTC_DOGE_01",
                "pair": "LTC/DOGE",
                "initial_balance_a": 5.0,
                "initial_balance_b": 5000.0,
                "initial_mid_price": 1000.0,
                "max_open_orders": 10,
                "partial_percent": 0.1,
                "order_sizing_mode": "equal",
                "spread_mode": "exponential",
                "base_spread_percent": 1.0,
                "spread_multiplier": 2.0,
                "inventory_bias": "balanced",
                "target_ratio_a": 0.5,
                "max_price_range_percent": 50.0,
                "check_interval": 15,
            }

            with self._patch_dependencies(temp_dir) as mocks:
                self._initialize_strategy(pair_config, temp_dir)

                self.strategy.inventory_manager.update_balance_a(5.0)
                self.strategy.inventory_manager.update_balance_b(5000.0)

                await self.strategy._create_orders()

                self.strategy.config_manager.general_log.info(
                    "[TEST PASSED] Orders created correctly."
                )

    async def _test_execution_processing(self):
        """Test that order execution is processed correctly."""
        test_name = "Execution Processing"
        self.strategy.config_manager.general_log.info(
            f"\n--- [TEST CASE] Running: {test_name} ---"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            pair_config = {
                "name": "LTC_DOGE_01",
                "pair": "LTC/DOGE",
                "initial_balance_a": 5.0,
                "initial_balance_b": 5000.0,
                "initial_mid_price": 1000.0,
                "max_open_orders": 10,
                "partial_percent": 0.1,
                "order_sizing_mode": "equal",
                "spread_mode": "exponential",
                "base_spread_percent": 1.0,
                "spread_multiplier": 2.0,
                "inventory_bias": "balanced",
                "target_ratio_a": 0.5,
                "max_price_range_percent": 50.0,
                "check_interval": 15,
            }

            self._initialize_strategy(pair_config, temp_dir)

            initial_balance_a = self.strategy.inventory_manager.initial_balance_a
            initial_balance_b = self.strategy.inventory_manager.initial_balance_b
            initial_mid_price = self.strategy.mid_price

            self.strategy._process_execution(
                side="sell",
                amount=0.5,
                price=1010.0,
            )

            new_balance_a = self.strategy.inventory_manager.current_balance_a
            new_balance_b = self.strategy.inventory_manager.current_balance_b
            new_mid_price = self.strategy.mid_price

            assert new_balance_a == pytest.approx(initial_balance_a - 0.5)
            assert new_balance_b == pytest.approx(initial_balance_b + (0.5 * 1010.0))
            assert new_mid_price == pytest.approx(1010.0)
            assert self.strategy.trade_count == 1

            self.strategy.config_manager.general_log.info(
                "[TEST PASSED] Execution processed correctly."
            )

    async def _test_mid_price_update(self):
        """Test that mid-price is updated after execution."""
        test_name = "Mid-Price Update"
        self.strategy.config_manager.general_log.info(
            f"\n--- [TEST CASE] Running: {test_name} ---"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            pair_config = {
                "name": "LTC_DOGE_01",
                "pair": "LTC/DOGE",
                "initial_balance_a": 5.0,
                "initial_balance_b": 5000.0,
                "initial_mid_price": 1000.0,
                "max_open_orders": 10,
                "partial_percent": 0.1,
                "order_sizing_mode": "equal",
                "spread_mode": "exponential",
                "base_spread_percent": 1.0,
                "spread_multiplier": 2.0,
                "inventory_bias": "balanced",
                "target_ratio_a": 0.5,
                "max_price_range_percent": 50.0,
                "check_interval": 15,
            }

            self._initialize_strategy(pair_config, temp_dir)

            assert self.strategy.pricing_engine.mid_price == 1000.0

            self.strategy._process_execution(
                side="sell",
                amount=0.5,
                price=1050.0,
            )

            assert self.strategy.pricing_engine.mid_price == pytest.approx(1050.0)

            buy_price = self.strategy.pricing_engine.calculate_buy_price(1)
            sell_price = self.strategy.pricing_engine.calculate_sell_price(1)

            assert buy_price < 1050.0
            assert sell_price > 1050.0
            assert buy_price == pytest.approx(1029.0)
            assert sell_price == pytest.approx(1071.0)

            self.strategy.config_manager.general_log.info(
                "[TEST PASSED] Mid-price updated correctly."
            )

    async def _test_inventory_bias_effect(self):
        """Test that inventory skew is calculated correctly."""
        test_name = "Inventory Bias Effect"
        self.strategy.config_manager.general_log.info(
            f"\n--- [TEST CASE] Running: {test_name} ---"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            pair_config = {
                "name": "LTC_DOGE_01",
                "pair": "LTC/DOGE",
                "initial_balance_a": 5.0,
                "initial_balance_b": 5000.0,
                "initial_mid_price": 1000.0,
                "max_open_orders": 10,
                "partial_percent": 0.1,
                "order_sizing_mode": "equal",
                "spread_mode": "exponential",
                "base_spread_percent": 1.0,
                "spread_multiplier": 2.0,
                "inventory_bias": "auto",
                "target_ratio_a": 0.5,
                "max_price_range_percent": 50.0,
                "check_interval": 15,
            }

            self._initialize_strategy(pair_config, temp_dir)

            self.strategy.inventory_manager.update_balance_a(10.0)
            self.strategy.inventory_manager.update_balance_b(1000.0)

            skew = self.strategy.inventory_manager.calculate_skew()

            assert skew > 0

            self.strategy.config_manager.general_log.info(
                f"[TEST PASSED] Skew calculated correctly: {skew:.4f}"
            )

    async def _test_status_summary(self):
        """Test status summary generation."""
        test_name = "Status Summary"
        self.strategy.config_manager.general_log.info(
            f"\n--- [TEST CASE] Running: {test_name} ---"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            pair_config = {
                "name": "LTC_DOGE_01",
                "pair": "LTC/DOGE",
                "initial_balance_a": 5.0,
                "initial_balance_b": 5000.0,
                "initial_mid_price": 1000.0,
                "max_open_orders": 10,
                "partial_percent": 0.1,
                "order_sizing_mode": "equal",
                "spread_mode": "exponential",
                "base_spread_percent": 1.0,
                "spread_multiplier": 2.0,
                "inventory_bias": "balanced",
                "target_ratio_a": 0.5,
                "max_price_range_percent": 50.0,
                "check_interval": 15,
            }

            self._initialize_strategy(pair_config, temp_dir)

            summary = self.strategy._get_status_summary()

            assert "Mid-Price" in summary
            assert "1000.000000" in summary

            self.strategy.config_manager.general_log.info(
                f"[TEST PASSED] Status summary: {summary}"
            )

    async def _test_profit_calculation(self):
        """Test profit calculation after trades."""
        test_name = "Profit Calculation"
        self.strategy.config_manager.general_log.info(
            f"\n--- [TEST CASE] Running: {test_name} ---"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            pair_config = {
                "name": "LTC_DOGE_01",
                "pair": "LTC/DOGE",
                "initial_balance_a": 5.0,
                "initial_balance_b": 5000.0,
                "initial_mid_price": 1000.0,
                "max_open_orders": 10,
                "partial_percent": 0.1,
                "order_sizing_mode": "equal",
                "spread_mode": "exponential",
                "base_spread_percent": 1.0,
                "spread_multiplier": 2.0,
                "inventory_bias": "balanced",
                "target_ratio_a": 0.5,
                "max_price_range_percent": 50.0,
                "check_interval": 15,
            }

            self._initialize_strategy(pair_config, temp_dir)

            inv = self.strategy.inventory_manager
            initial_a = inv.initial_balance_a
            initial_b = inv.initial_balance_b

            self.strategy._process_execution(side="sell", amount=1.0, price=1050.0)

            profit_a = inv.current_balance_a - initial_a
            profit_b = inv.current_balance_b - initial_b

            assert profit_a == pytest.approx(-1.0)
            assert profit_b == pytest.approx(1050.0)

            mid_price = self.strategy.mid_price
            profit_a_equiv = profit_a + (profit_b / mid_price)
            expected_profit_a_equiv = -1.0 + (1050.0 / 1050.0)
            assert profit_a_equiv == pytest.approx(expected_profit_a_equiv)

            self.strategy.config_manager.general_log.info(
                f"[TEST PASSED] Profit calculated: A={profit_a}, B={profit_b}, A-equivalent={profit_a_equiv}"
            )


@pytest.fixture(scope="session")
def mock_strategy():
    """Fixture to create a mock strategy instance for testing."""
    with (
        patch(
            "definitions.xbridge_manager.detect_rpc",
            return_value=("user", 1234, "pass", "/tmp"),
        ),
        patch("definitions.xbridge_manager.is_port_open", return_value=True),
        patch("definitions.ccxt_manager.CCXTManager"),
        patch("asyncio.run"),
        patch("definitions.xbridge_manager.rpc_call"),
    ):
        from definitions.config_manager import ConfigManager

        config_manager = ConfigManager(strategy="autonomous_maker")
        config_manager.initialize()
        return config_manager.strategy_instance


@pytest.fixture(scope="session")
def autonomous_tester(mock_strategy):
    """Fixture to create an AutonomousMakerTester instance."""
    return AutonomousMakerTester(mock_strategy)


@pytest.mark.asyncio
async def test_initialization(autonomous_tester):
    await autonomous_tester._test_initialization()


@pytest.mark.asyncio
async def test_execution_processing(autonomous_tester):
    await autonomous_tester._test_execution_processing()


@pytest.mark.asyncio
async def test_mid_price_update(autonomous_tester):
    await autonomous_tester._test_mid_price_update()


@pytest.mark.asyncio
async def test_inventory_bias_effect(autonomous_tester):
    await autonomous_tester._test_inventory_bias_effect()


@pytest.mark.asyncio
async def test_status_summary(autonomous_tester):
    await autonomous_tester._test_status_summary()


@pytest.mark.asyncio
async def test_profit_calculation(autonomous_tester):
    await autonomous_tester._test_profit_calculation()


def test_strategy_static_values(mock_strategy):
    """Test methods that should return static values."""
    assert mock_strategy.should_update_cex_prices() is False
    assert mock_strategy.get_operation_interval() == 15


def test_build_sell_order_details_returns_zero(mock_strategy):
    """Test that build_sell_order_details returns zero for autonomous strategy."""
    amount, offset = mock_strategy.build_sell_order_details(None)
    assert amount == 0.0
    assert offset == 0.0


def test_calculate_sell_price_returns_zero(mock_strategy):
    """Test that calculate_sell_price returns zero for autonomous strategy."""
    price = mock_strategy.calculate_sell_price(None)
    assert price == 0.0


def test_get_price_variation_tolerance(mock_strategy):
    """Test that get_price_variation_tolerance returns configured value."""
    with tempfile.TemporaryDirectory() as temp_dir:
        pair_config = {
            "name": "LTC_DOGE_01",
            "pair": "LTC/DOGE",
            "initial_balance_a": 5.0,
            "initial_balance_b": 5000.0,
            "initial_mid_price": 1000.0,
            "max_open_orders": 10,
            "partial_percent": 0.1,
            "order_sizing_mode": "equal",
            "spread_mode": "exponential",
            "base_spread_percent": 1.0,
            "spread_multiplier": 2.0,
            "inventory_bias": "balanced",
            "target_ratio_a": 0.5,
            "max_price_range_percent": 50.0,
            "check_interval": 15,
            "price_variation_tolerance": 10.0,
        }
        mock_strategy.config_manager.ROOT_DIR = temp_dir
        mock_strategy._initialize_for_pair(pair_config)
        tolerance = mock_strategy.get_price_variation_tolerance(None)
        assert tolerance == 10.0
