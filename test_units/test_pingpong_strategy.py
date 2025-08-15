import asyncio
import os
import sys
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from definitions.config_manager import ConfigManager

if TYPE_CHECKING:
    from strategies.pingpong_strategy import PingPongStrategy

from contextlib import contextmanager

from definitions.starter import TradingProcessor, MainController


class PingPongStrategyTester:
    """
    A dedicated class to test the logic and various scenarios
    of the PingPongStrategy.
    """

    def __init__(self, strategy_instance: 'PingPongStrategy'):
        self.strategy = strategy_instance
        self.config_manager = strategy_instance.config_manager
        if not self.config_manager.pairs:
            raise RuntimeError("Cannot run tests: No pairs were initialized. "
                               "Check config/config_pingpong.yaml for enabled pairs.")
        # Use the first *initialized* pair for testing, making the test independent of config file order.
        self.pair_name = next(iter(self.config_manager.pairs))
        self.pair = self.config_manager.pairs[self.pair_name]

    def reset(self):
        """Resets the state of the pair for test isolation."""
        self.pair.dex.order_history = None
        self.pair.dex.current_order = None
        self.pair.dex.disabled = False
        self.pair.dex.variation = None
        self.pair.dex.order = None

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
            mock_make_order.return_value = {'id': 'mock_order_id_123', 'status': 'created'}
            mock_get_status.return_value = {'id': 'mock_order_id_123', 'status': 'open'}

            mocks = {
                'make_order': mock_make_order,
                'cancel_order': mock_cancel_order,
                'get_status': mock_get_status,
                'open': mock_open,
                'yaml_load': mock_yaml_load,
                'yaml_dump': mock_yaml_dump,
            }
            yield mocks

    def _set_mock_cex_price(self, price: float):
        """Helper to set the CEX price for the test pair."""
        self.pair.cex.price = price
        # Mock underlying token prices for realistic calculations
        self.pair.t2.cex.usd_price = 0.125
        self.pair.t1.cex.usd_price = price * self.pair.t2.cex.usd_price
        self.config_manager.tokens['BTC'].cex.usd_price = 100000.0
        self.pair.t1.cex.cex_price = self.pair.t1.cex.usd_price / 100000.0
        self.pair.t2.cex.cex_price = self.pair.t2.cex.usd_price / 100000.0

    async def _test_initial_sell_order_creation(self):
        """
        Tests that a SELL order is created when no order history exists.
        """
        test_name = "Initial SELL Order Creation"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange: No order history
            mocks['yaml_load'].return_value = None
            self.pair.dex.read_last_order_history()  # Reread with mock
            self._set_mock_cex_price(0.3)

            # Act: Initialize the virtual order and attempt to create it
            self.pair.dex.init_virtual_order()
            await self.pair.dex.create_order()

            # Assert
            mocks['make_order'].assert_called_once()
            call_args = mocks['make_order'].call_args[0]
            # For a SELL on t1/t2, the maker is t1
            assert call_args[0] == self.pair.t1.symbol, \
                f"Incorrect order side. Expected SELL (maker={self.pair.t1.symbol}), got maker={call_args[0]}"
            self.config_manager.general_log.info("[TEST PASSED] Correctly created a SELL order.")

    async def _test_buy_order_creation_after_sell(self):
        """
        Tests that a BUY order is created after a SELL order has finished.
        """
        test_name = "BUY Order Creation After SELL"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange: History of a finished SELL order
            mocks['yaml_load'].return_value = {'side': 'SELL', 'maker_size': '1.0', 'dex_price': 0.3}
            self.pair.dex.read_last_order_history()
            self._set_mock_cex_price(0.29)  # Price is stable/lower

            # Act
            self.pair.dex.init_virtual_order()
            await self.pair.dex.create_order()

            # Assert
            mocks['make_order'].assert_called_once()
            call_args = mocks['make_order'].call_args[0]
            # For a BUY on t1/t2, the maker is t2
            assert call_args[0] == self.pair.t2.symbol, \
                f"Incorrect order side. Expected BUY (maker={self.pair.t2.symbol}), got maker={call_args[0]}"
            self.config_manager.general_log.info("[TEST PASSED] Correctly created a BUY order.")

    async def _test_sell_order_creation_after_buy(self):
        """
        Tests that a SELL order is created after a BUY order has finished.
        """
        test_name = "SELL Order Creation After BUY"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange: History of a finished BUY order
            mocks['yaml_load'].return_value = {'side': 'BUY'}
            self.pair.dex.read_last_order_history()
            self._set_mock_cex_price(0.3)

            # Act
            self.pair.dex.init_virtual_order()
            await self.pair.dex.create_order()

            # Assert
            mocks['make_order'].assert_called_once()
            call_args = mocks['make_order'].call_args[0]
            assert call_args[0] == self.pair.t1.symbol, \
                f"Incorrect order side. Expected SELL (maker={self.pair.t1.symbol}), got maker={call_args[0]}"
            self.config_manager.general_log.info("[TEST PASSED] Correctly created a SELL order.")

    async def _test_price_variation_cancel_and_recreate_sell(self):
        """
        Tests that a SELL order is cancelled and recreated if price drops too much.
        """
        test_name = "Price Variation Cancel & Recreate (SELL)"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange: No history, so we are in a SELL state.
            mocks['yaml_load'].return_value = None
            self.pair.dex.read_last_order_history()
            self._set_mock_cex_price(0.3)
            self.pair.dex.init_virtual_order()
            # Manually set the open order
            self.pair.dex.order = {'id': 'mock_order_id_123', 'status': 'open'}

            # Act: Price drops significantly (tolerance is 0.02, or 2%)
            self._set_mock_cex_price(0.2)  # >2% drop from 0.3
            await self.pair.dex.status_check()

            # Assert
            mocks['cancel_order'].assert_called_once_with('mock_order_id_123')
            # make_order should be called again to create the new order
            assert mocks['make_order'].call_count == 1, \
                f"make_order was called {mocks['make_order'].call_count} times, expected 1 for recreate."
            self.config_manager.general_log.info(
                "[TEST PASSED] Correctly cancelled and recreated SELL order on price variation.")

    async def _test_price_variation_no_cancel_buy(self):
        """
        Tests that a BUY order is NOT cancelled if price variation is within tolerance.
        """
        test_name = "Price Variation Within Tolerance (BUY)"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange: History of a finished SELL order, so we are in a BUY state.
            mocks['yaml_load'].return_value = {'side': 'SELL', 'maker_size': '1.0', 'dex_price': 0.3,
                                               'org_pprice': 0.3}
            self.pair.dex.read_last_order_history()
            self._set_mock_cex_price(0.3)
            self.pair.dex.init_virtual_order()
            # Manually set the open order
            self.pair.dex.order = {'id': 'mock_order_id_123', 'status': 'open'}

            # Act: Price moves slightly, but within the 2% tolerance
            self._set_mock_cex_price(0.305)
            await self.pair.dex.status_check()

            # Assert
            mocks['cancel_order'].assert_not_called()
            self.config_manager.general_log.info(
                "[TEST PASSED] Correctly kept BUY order open as price variation was within tolerance.")

    async def _test_order_completion_flow(self):
        """
        Tests that a finished order is written to history and the next order is created.
        """
        test_name = "Order Completion Flow (SELL -> BUY)"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange: An open SELL order exists
            mocks['yaml_load'].return_value = None
            self.pair.dex.read_last_order_history()
            self._set_mock_cex_price(0.3)
            self.pair.dex.init_virtual_order()
            self.pair.dex.order = {'id': 'mock_order_id_123', 'status': 'open'}

            # Act: The order status check now returns 'finished'
            mocks['get_status'].return_value = {'id': 'mock_order_id_123', 'status': 'finished'}
            await self.pair.dex.status_check()

            # Assert
            mocks['yaml_dump'].assert_called_once()  # History was written
            # A new order should have been created
            mocks['make_order'].assert_called_once()
            call_args = mocks['make_order'].call_args[0]
            # The new order should be a BUY order
            assert call_args[0] == self.pair.t2.symbol, \
                f"Incorrect next order side. Expected BUY (maker={self.pair.t2.symbol}), got maker={call_args[0]}"
            self.config_manager.general_log.info(
                "[TEST PASSED] Correctly wrote history and created next (BUY) order.")

    async def _test_order_expiration_recreates_order(self):
        """
        Tests that if an open order expires, the bot recreates it based on the
        same side, rather than moving to the next step in the cycle.
        """
        test_name = "Order Expiration Recreates Order"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange: An open SELL order exists (no history).
            mocks['yaml_load'].return_value = None
            self.pair.dex.read_last_order_history()
            self._set_mock_cex_price(0.3)
            self.pair.dex.init_virtual_order()
            self.pair.dex.order = {'id': 'mock_order_id_123', 'status': 'open'}

            # Act: The order status check now returns 'expired'.
            mocks['get_status'].return_value = {'id': 'mock_order_id_123', 'status': 'expired'}
            await self.pair.dex.status_check()

            # Assert:
            # 1. The bot should not have written a new history file, as the trade didn't finish.
            mocks['yaml_dump'].assert_not_called()

            # 2. The bot should have tried to create a new order, and it should be another SELL.
            mocks['make_order'].assert_called_once()
            call_args = mocks['make_order'].call_args[0]
            assert call_args[0] == self.pair.t1.symbol, \
                f"Incorrect order side after expiration. Expected SELL (maker={self.pair.t1.symbol}), got maker={call_args[0]}"
            self.config_manager.general_log.info("[TEST PASSED] Correctly recreated a SELL order after expiration.")

    async def _test_buy_price_logic_on_market_moves(self):
        """
        Tests the core profit-locking algorithm for BUY orders.
        - Verifies that the bot extends the profit spread on favorable market moves.
        - Verifies that the bot locks the buy price on unfavorable market moves.
        """
        test_name = "Buy Price Logic (Profit Algorithm Resilience)"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        # Use a single patch for both sub-tests
        with self._patch_dependencies() as mocks:
            # --- Sub-test 1: Favorable move (price drops) ---
            self.config_manager.general_log.info("  - Testing favorable market move (price drops)...")

            # Arrange: History of a finished SELL order at 0.3
            last_sell_price = 0.3
            mocks['yaml_load'].return_value = {'side': 'SELL', 'maker_size': '1.0', 'dex_price': last_sell_price}
            self.pair.dex.read_last_order_history()

            # Act: Live price drops to 0.28, which is lower than the last sell price
            favorable_live_price = 0.28
            self._set_mock_cex_price(favorable_live_price)
            self.pair.dex.init_virtual_order()

            # Assert: The bot should use the new, lower price as its base
            favorable_base_price = self.pair.dex.current_order['org_pprice']
            assert favorable_base_price == favorable_live_price, \
                f"Expected base price {favorable_live_price}, but got {favorable_base_price}."
            self.config_manager.general_log.info(
                f"    [SUB-TEST PASSED] Bot correctly used the lower live price ({favorable_live_price}) as the new base.")

            # --- Sub-test 2: Unfavorable move (price rises) ---
            self.config_manager.general_log.info("\n  - Testing unfavorable market move (price rises)...")

            # Act: Live price rises to 0.32, which is higher than the last sell price
            self._set_mock_cex_price(0.32)
            self.pair.dex.init_virtual_order()

            # Assert: The bot should lock its price to the last sell price, ignoring the higher live price
            unfavorable_base_price = self.pair.dex.current_order['org_pprice']
            assert unfavorable_base_price == last_sell_price, \
                f"Expected locked base price {last_sell_price}, but got {unfavorable_base_price}."
            self.config_manager.general_log.info(
                f"    [SUB-TEST PASSED] Bot correctly locked the base price to the last sell price ({last_sell_price}).")

    async def _test_insufficient_balance(self):
        """
        Tests that an order is not created if the wallet balance is insufficient.
        """
        test_name = "Insufficient Balance for SELL Order"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange: No order history, attempting to create a SELL order.
            mocks['yaml_load'].return_value = None
            self.pair.dex.read_last_order_history()
            self._set_mock_cex_price(0.3)
            self.pair.dex.init_virtual_order()

            # Mock the balance of the token to be sold (t1) to be less than the required amount.
            original_balance = self.pair.t1.dex.free_balance
            self.pair.t1.dex.free_balance = 0.1  # Virtual order will need more than this

            # Act
            await self.pair.dex.create_order()

            # Assert
            mocks['make_order'].assert_not_called()
            self.config_manager.general_log.info(
                "[TEST PASSED] Correctly prevented order creation due to insufficient balance.")

            # Cleanup
            self.pair.t1.dex.free_balance = original_balance


    async def _test_error_swap_status_disables_pair(self):
        """
        Tests that a pair is disabled when an 'error swap' status is encountered.
        """
        test_name = "Error Swap Status Disables Pair"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange: An open SELL order exists
            self.pair.dex.order = {'id': 'mock_order_id_123', 'status': 'open'}
            assert self.pair.dex.disabled is False, "Pair should not be disabled initially."

            # Act: The order status check now returns a status that maps to STATUS_ERROR_SWAP
            mocks['get_status'].return_value = {'id': 'mock_order_id_123', 'status': 'offline'}
            await self.pair.dex.status_check()

            # Assert
            assert self.pair.dex.disabled is True, "Pair was not disabled after 'error swap' status."
            self.config_manager.general_log.info(
                "[TEST PASSED] Correctly disabled pair after encountering 'error swap' status.")

    async def _test_cancel_own_orders(self):
        """
        Tests that cancel_own_orders only cancels orders for the specific strategy instance.
        """
        test_name = "Cancel Own Orders"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies() as mocks:
            # Arrange
            # Mock a controller and attach it to the strategy
            mock_controller = MagicMock()
            mock_controller.pairs_dict = self.config_manager.pairs
            self.strategy.controller = mock_controller

            # Give the pair an active order to be cancelled
            self.pair.dex.order = {'id': 'order_to_cancel_123'}

            # Act
            await self.strategy.cancel_own_orders()

            # Assert
            mocks['cancel_order'].assert_called_once_with('order_to_cancel_123')
            self.config_manager.general_log.info("[TEST PASSED] Correctly cancelled its own order.")

            # Arrange for no orders
            mocks['cancel_order'].reset_mock()
            self.pair.dex.order = None

            # Act
            await self.strategy.cancel_own_orders()

            # Assert
            mocks['cancel_order'].assert_not_called()
            self.config_manager.general_log.info("[TEST PASSED] Did not attempt to cancel when no order exists.")

    async def _test_build_sell_order_details_calculation(self):
        """
        Tests the specific calculation logic within build_sell_order_details,
        including edge cases like missing prices.
        """
        test_name = "Sell Order Details Calculation"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies():
            # Arrange: Set known prices and USD amount
            self.pair.cfg['usd_amount'] = 50.0  # $50
            self.config_manager.tokens['BTC'].cex.usd_price = 100000.0  # $100k
            # This makes T1 worth $100
            self.pair.t1.cex.cex_price = 0.001  # T1/BTC price

            # Act
            amount, offset = self.strategy.build_sell_order_details(self.pair.dex)

            # Assert
            # Expected amount = (50 / 100000) / 0.001 = 0.5 T1
            assert amount == pytest.approx(0.5)
            self.config_manager.general_log.info(
                "[SUB-TEST PASSED] Correctly calculated sell amount with valid prices.")

            # --- Test edge case: missing BTC price ---
            self.config_manager.tokens['BTC'].cex.usd_price = 0  # Missing/zero price
            with patch.object(self.config_manager.general_log, 'warning') as mock_log_warning:
                amount_zero, _ = self.strategy.build_sell_order_details(self.pair.dex)
                assert amount_zero == 0
                mock_log_warning.assert_called_once()
                self.config_manager.general_log.info(
                    "[SUB-TEST PASSED] Correctly returned 0 amount with missing BTC price.")

    async def _test_buy_order_price_lock(self):
        """
        Tests the price lock mechanism for BUY orders, ensuring is_locked is True
        when the live price exceeds the last sell price.
        """
        test_name = "BUY Order Price Lock Mechanism"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        with self._patch_dependencies():
            # Arrange: History of a SELL at 0.3
            last_sell_price = 0.3
            self.pair.dex.order_history = {'side': 'SELL', 'dex_price': last_sell_price}
            original_price = 0.29  # The price of the current BUY order

            # Act 1: Live price is HIGHER than last sell price
            live_price_high = 0.31
            variation_high, is_locked_high = self.strategy.calculate_variation_based_on_side(
                self.pair.dex, 'BUY', live_price_high, original_price
            )

            # Assert 1: is_locked should be True, indicating a lock
            assert is_locked_high is True
            self.config_manager.general_log.info(
                "[SUB-TEST PASSED] Correctly signaled to lock BUY order on price rise.")

            # Act 2: Live price is LOWER than last sell price
            live_price_low = 0.28
            variation_low, is_locked_low = self.strategy.calculate_variation_based_on_side(
                self.pair.dex, 'BUY', live_price_low, original_price
            )

            # Assert 2: is_locked should be False
            assert is_locked_low is False
            self.config_manager.general_log.info(
                "[SUB-TEST PASSED] Correctly did not signal lock when price is below last sell.")


@pytest.fixture(scope="module")
def mock_strategy():
    """Fixture to create a mock strategy instance for testing."""
    config_manager = ConfigManager(strategy="pingpong")
    config_manager.initialize()
    return config_manager.strategy_instance


@pytest.fixture(scope="module")
def pingpong_tester(mock_strategy):
    """Fixture to create a PingPongStrategyTester instance."""
    return PingPongStrategyTester(mock_strategy)


@pytest.mark.asyncio
async def test_initial_sell_order_creation(pingpong_tester):
    await pingpong_tester._test_initial_sell_order_creation()


@pytest.mark.asyncio
async def test_buy_order_creation_after_sell(pingpong_tester):
    await pingpong_tester._test_buy_order_creation_after_sell()


@pytest.mark.asyncio
async def test_sell_order_creation_after_buy(pingpong_tester):
    await pingpong_tester._test_sell_order_creation_after_buy()


@pytest.mark.asyncio
async def test_price_variation_cancel_and_recreate_sell(pingpong_tester):
    await pingpong_tester._test_price_variation_cancel_and_recreate_sell()


@pytest.mark.asyncio
async def test_price_variation_no_cancel_buy(pingpong_tester):
    await pingpong_tester._test_price_variation_no_cancel_buy()


@pytest.mark.asyncio
async def test_order_completion_flow(pingpong_tester):
    await pingpong_tester._test_order_completion_flow()


@pytest.mark.asyncio
async def test_buy_price_logic_on_market_moves(pingpong_tester):
    await pingpong_tester._test_buy_price_logic_on_market_moves()


@pytest.mark.asyncio
async def test_order_expiration_recreates_order(pingpong_tester):
    await pingpong_tester._test_order_expiration_recreates_order()


@pytest.mark.asyncio
async def test_insufficient_balance(pingpong_tester):
    await pingpong_tester._test_insufficient_balance()




@pytest.mark.asyncio
async def test_error_swap_status_disables_pair(pingpong_tester):
    await pingpong_tester._test_error_swap_status_disables_pair()


@pytest.mark.asyncio
async def test_cancel_own_orders(pingpong_tester):
    await pingpong_tester._test_cancel_own_orders()


@pytest.mark.asyncio
async def test_build_sell_order_details_calculation(pingpong_tester):
    await pingpong_tester._test_build_sell_order_details_calculation()


@pytest.mark.asyncio
async def test_buy_order_price_lock(pingpong_tester):
    await pingpong_tester._test_buy_order_price_lock()


def test_get_price_variation_tolerance(pingpong_tester):
    """Tests that get_price_variation_tolerance returns the correct value from config."""
    tester = pingpong_tester
    # Set a value in the mock config
    tester.pair.cfg['price_variation_tolerance'] = 0.05
    tolerance = tester.strategy.get_price_variation_tolerance(tester.pair.dex)
    assert tolerance == 0.05


def test_strategy_static_values(pingpong_tester):
    """Tests methods that should return static values."""
    tester = pingpong_tester
    assert tester.strategy.should_update_cex_prices() is True
    assert tester.strategy.get_operation_interval() == 15
