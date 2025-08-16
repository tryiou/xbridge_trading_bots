import os
import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from definitions.config_manager import ConfigManager
from strategies.basicseller_strategy import BasicSellerStrategy


class BasicSellerStrategyTester:
    """
    A dedicated class to test the logic and various scenarios
    of the BasicSellerStrategy.
    """

    def __init__(self, strategy_instance: 'BasicSellerStrategy'):
        self.strategy = strategy_instance
        self.config_manager = strategy_instance.config_manager
        if not self.config_manager.pairs:
            raise RuntimeError("Cannot run tests: No pairs were initialized. "
                               "Check test setup or config_basicseller.yaml.")
        # Use the first initialized pair for testing
        self.pair_name = next(iter(self.config_manager.pairs))
        self.pair = self.config_manager.pairs[self.pair_name]
        self.initial_min_sell_price_usd = self.pair.min_sell_price_usd

    def reset(self):
        """Resets the state of the pair for test isolation."""
        self.pair.dex.order_history = None
        self.pair.dex.current_order = None
        self.pair.dex.disabled = False
        self.pair.dex.order = None
        self.pair.min_sell_price_usd = self.initial_min_sell_price_usd

    @contextmanager
    def _patch_dependencies(self):
        """A context manager to patch all external dependencies for tests."""
        self.reset()
        with patch.object(self.config_manager.xbridge_manager, 'makeorder',
                          new_callable=AsyncMock) as mock_make_order, \
                patch.object(self.config_manager.xbridge_manager, 'cancelorder',
                             new_callable=AsyncMock) as mock_cancel_order, \
                patch.object(self.config_manager.xbridge_manager, 'getorderstatus',
                             new_callable=AsyncMock) as mock_get_status, \
                patch('builtins.open', new_callable=MagicMock) as mock_open, \
                patch('yaml.safe_load') as mock_yaml_load, \
                patch('yaml.safe_dump') as mock_yaml_dump, \
                patch('asyncio.sleep', return_value=None), \
                patch.object(self.pair.t1.dex, 'free_balance', 1000.0), \
                patch.object(self.pair.t2.dex, 'free_balance', 1000.0):
            # Default mock behaviors
            mock_make_order.return_value = {'id': 'mock_order_id_456', 'status': 'created'}
            mock_get_status.return_value = {'id': 'mock_order_id_456', 'status': 'open'}

            mocks = {
                'make_order': mock_make_order,
                'cancel_order': mock_cancel_order,
                'get_status': mock_get_status,
                'open': mock_open,
                'yaml_load': mock_yaml_load,
                'yaml_dump': mock_yaml_dump,
            }
            yield mocks

    def _set_mock_prices(self, t1_usd_price: float, t2_usd_price: float):
        """Helper to set the mock token prices for the test pair."""
        self.pair.t1.cex.usd_price = t1_usd_price
        self.pair.t2.cex.usd_price = t2_usd_price
        if t2_usd_price > 0:
            self.pair.cex.price = t1_usd_price / t2_usd_price
        else:
            self.pair.cex.price = 0
        # Mock underlying BTC price for realistic calculations
        self.config_manager.tokens['BTC'].cex.usd_price = 100000.0
        self.pair.t1.cex.cex_price = t1_usd_price / 100000.0
        self.pair.t2.cex.cex_price = t2_usd_price / 100000.0

    async def _test_sell_order_creation(self):
        """Tests that a SELL order is always created."""
        test_name = "Sell Order Creation"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange
            mocks['yaml_load'].return_value = None  # No history
            self.pair.dex.read_last_order_history()
            self._set_mock_prices(t1_usd_price=1.0, t2_usd_price=0.1)

            # Act
            self.pair.dex.init_virtual_order()
            await self.pair.dex.create_order()

            # Assert
            mocks['make_order'].assert_called_once()
            call_args = mocks['make_order'].call_args[0]
            assert call_args[0] == self.pair.t1.symbol, "Expected a SELL order"
            self.config_manager.general_log.info("[TEST PASSED] Correctly created a SELL order.")

    async def _test_sell_price_above_minimum(self):
        """Tests sell price logic when CEX price is above the minimum."""
        test_name = "Sell Price Above Minimum"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies():
            # Arrange
            self.pair.min_sell_price_usd = 0.9  # Set a minimum price
            self._set_mock_prices(t1_usd_price=1.0, t2_usd_price=0.1)  # Live price is $1.0
            cex_price = self.pair.cex.price

            # Act
            sell_price = self.strategy.calculate_sell_price(self.pair.dex)

            # Assert
            assert sell_price == cex_price, "Should use CEX price when it's higher than the minimum"
            self.config_manager.general_log.info("[TEST PASSED] Correctly used CEX price.")

    async def _test_sell_price_below_minimum(self):
        """Tests sell price logic when CEX price is below the minimum."""
        test_name = "Sell Price Below Minimum"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies():
            # Arrange
            self.pair.min_sell_price_usd = 1.1
            self._set_mock_prices(t1_usd_price=1.0, t2_usd_price=0.1)  # Live price is $1.0
            expected_price = self.pair.min_sell_price_usd / self.pair.t2.cex.usd_price

            # Act
            sell_price = self.strategy.calculate_sell_price(self.pair.dex)

            # Assert
            assert sell_price == expected_price, "Should use minimum USD price converted to pair price"
            self.config_manager.general_log.info("[TEST PASSED] Correctly used minimum price.")

    async def _test_order_completion_disables_pair(self):
        """Tests that a finished order disables the pair."""
        test_name = "Order Completion Disables Pair"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange
            self.pair.dex.disabled = False
            self.pair.dex.order = {'id': 'mock_order_id_456', 'status': 'open'}
            self.pair.dex.current_order = {'maker': self.pair.t1.symbol}
            mocks['get_status'].return_value = {'id': 'mock_order_id_456', 'status': 'finished'}

            # Act
            await self.pair.dex.status_check()

            # Assert
            assert self.pair.dex.disabled is True, "Pair was not disabled after order finished."
            self.config_manager.general_log.info("[TEST PASSED] Correctly disabled pair on order completion.")

    async def _test_price_variation_recreates_order(self):
        """Tests that a SELL order is recreated on significant price variation."""
        test_name = "Price Variation Recreates SELL Order"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange
            self._set_mock_prices(t1_usd_price=1.0, t2_usd_price=0.1)
            self.pair.dex.init_virtual_order()
            self.pair.dex.order = {'id': 'mock_order_id_456', 'status': 'open'}

            # Act: Price moves significantly (default tolerance is 1%)
            self._set_mock_prices(t1_usd_price=1.0, t2_usd_price=0.12)  # ~20% change in pair price
            await self.pair.dex.status_check()

            # Assert
            mocks['cancel_order'].assert_called_once_with('mock_order_id_456')
            assert mocks['make_order'].call_count == 1
            self.config_manager.general_log.info("[TEST PASSED] Correctly recreated order on price variation.")

    async def _test_insufficient_balance(self):
        """Tests that an order is not created if the wallet balance is insufficient."""
        test_name = "Insufficient Balance for SELL Order"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange
            self._set_mock_prices(t1_usd_price=1.0, t2_usd_price=0.1)
            # In CLI mode, amount_token_to_sell is set on the pair instance from initialize().
            # The fixture sets it to 100.0.
            self.pair.dex.init_virtual_order()

            # Mock balance to be less than required
            with patch.object(self.pair.t1.dex, 'free_balance', 50.0):
                # Act
                await self.pair.dex.create_order()

                # Assert
                mocks['make_order'].assert_not_called()
                self.config_manager.general_log.info(
                    "[TEST PASSED] Correctly prevented order creation due to insufficient balance.")

    async def _test_error_swap_status_disables_pair(self):
        """Tests that a pair is disabled when an 'error swap' status is encountered."""
        test_name = "Error Swap Status Disables Pair"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange
            self.pair.dex.order = {'id': 'mock_order_id_456', 'status': 'open'}
            self.pair.dex.disabled = False
            mocks['get_status'].return_value = {'id': 'mock_order_id_456', 'status': 'invalid'}  # Maps to error

            # Act
            await self.pair.dex.status_check()

            # Assert
            assert self.pair.dex.disabled is True, "Pair was not disabled after 'error swap' status."
            self.config_manager.general_log.info("[TEST PASSED] Correctly disabled pair on error status.")

    async def _test_buy_methods_log_errors(self):
        """Tests that buy-related methods log errors as they are not implemented."""
        test_name = "Buy Methods Log Errors"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies(), \
                patch.object(self.config_manager.general_log, 'error') as mock_log_error:
            # Act
            self.strategy.build_buy_order_details(self.pair.dex)
            self.strategy.determine_buy_price(self.pair.dex)

            # Assert
            assert mock_log_error.call_count == 2
            self.config_manager.general_log.info("[TEST PASSED] Buy methods correctly logged errors.")

    def _test_strategy_static_values(self):
        """Tests methods that should return static values."""
        test_name = "Strategy Static Values"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        assert self.strategy.get_price_variation_tolerance(
            self.pair.dex) == self.pair.dex.PRICE_VARIATION_TOLERANCE_DEFAULT
        assert self.strategy.should_update_cex_prices() is True
        assert self.strategy.get_operation_interval() == 15
        self.config_manager.general_log.info("[TEST PASSED] Static value methods returned correct values.")


@pytest.fixture(scope="module")
def mock_strategy_cli():
    """Fixture to create a mock strategy instance for testing in CLI mode."""
    config_manager = ConfigManager(strategy="basic_seller")
    # Simulate CLI arguments for initialization
    config_manager.initialize(
        token_to_sell="BLOCK",
        token_to_buy="LTC",
        amount_token_to_sell=100.0,
        min_sell_price_usd=0.5,
        sell_price_offset=0.01
    )
    return config_manager.strategy_instance


@pytest.fixture(scope="module")
def basicseller_tester(mock_strategy_cli):
    """Fixture to create a BasicSellerStrategyTester instance."""
    return BasicSellerStrategyTester(mock_strategy_cli)


@pytest.mark.asyncio
async def test_sell_order_creation(basicseller_tester):
    await basicseller_tester._test_sell_order_creation()


@pytest.mark.asyncio
async def test_sell_price_above_minimum(basicseller_tester):
    await basicseller_tester._test_sell_price_above_minimum()


@pytest.mark.asyncio
async def test_sell_price_below_minimum(basicseller_tester):
    await basicseller_tester._test_sell_price_below_minimum()


@pytest.mark.asyncio
async def test_order_completion_disables_pair(basicseller_tester):
    await basicseller_tester._test_order_completion_disables_pair()


@pytest.mark.asyncio
async def test_price_variation_recreates_order(basicseller_tester):
    await basicseller_tester._test_price_variation_recreates_order()


@pytest.mark.asyncio
async def test_insufficient_balance(basicseller_tester):
    await basicseller_tester._test_insufficient_balance()


@pytest.mark.asyncio
async def test_error_swap_status_disables_pair(basicseller_tester):
    await basicseller_tester._test_error_swap_status_disables_pair()


@pytest.mark.asyncio
async def test_buy_methods_log_errors(basicseller_tester):
    await basicseller_tester._test_buy_methods_log_errors()


def test_strategy_static_values(basicseller_tester):
    basicseller_tester._test_strategy_static_values()
