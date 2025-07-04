import asyncio
import os
import time
from typing import Any, Dict, List, TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

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
        self.test_results: List[Dict[str, Any]] = []
        if not self.config_manager.pairs:
            raise RuntimeError("Cannot run tests: No pairs were initialized. "
                               "Check config/config_pingpong.yaml for enabled pairs.")
        # Use the first *initialized* pair for testing, making the test independent of config file order.
        self.pair_name = next(iter(self.config_manager.pairs))
        self.pair = self.config_manager.pairs[self.pair_name]

    @contextmanager
    def _patch_dependencies(self):
        """A context manager to patch all external dependencies for tests."""
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

    async def run_all_tests(self):
        """Runs the full suite of PingPong strategy tests."""
        self.config_manager.general_log.info("--- Starting PingPong Strategy Test Suite ---")
        await self._test_initial_sell_order_creation()
        await self._test_buy_order_creation_after_sell()
        await self._test_sell_order_creation_after_buy()
        await self._test_price_variation_cancel_and_recreate_sell()
        await self._test_price_variation_no_cancel_buy()
        await self._test_order_completion_flow()
        await self._test_buy_price_logic_on_market_moves()
        await self._test_order_expiration_recreates_order()
        await self._test_insufficient_balance()
        await self._test_concurrency_throttling()
        self.config_manager.general_log.info("\n--- PingPong Strategy Test Suite Finished ---")
        self._print_summary()

    def _print_summary(self):
        """Prints a formatted summary of the test suite results."""
        summary_lines = [
            "\n" + "=" * 60,
            "--- Test Suite Summary ---".center(60),
            "=" * 60
        ]
        passed_count = 0
        failed_count = 0

        for result in self.test_results:
            status = "PASSED" if result['passed'] else "FAILED"
            summary_lines.append(f"  - [{status}] {result['name']}")
            if result['passed']:
                passed_count += 1
            else:
                failed_count += 1

        summary_lines.append("-" * 60)
        summary_lines.append(f"Total Tests: {len(self.test_results)} | Passed: {passed_count} | Failed: {failed_count}")
        summary_lines.append("=" * 60)
        self.config_manager.general_log.info("\n".join(summary_lines))

    async def _test_initial_sell_order_creation(self):
        """
        Tests that a SELL order is created when no order history exists.
        """
        test_name = "Initial SELL Order Creation"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False

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
            if call_args[0] == self.pair.t1.symbol:
                self.config_manager.general_log.info("[TEST PASSED] Correctly created a SELL order.")
                passed = True
            else:
                self.config_manager.general_log.error(
                    f"[TEST FAILED] Incorrect order side. Expected SELL (maker={self.pair.t1.symbol}), got maker={call_args[0]}")
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_buy_order_creation_after_sell(self):
        """
        Tests that a BUY order is created after a SELL order has finished.
        """
        test_name = "BUY Order Creation After SELL"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False

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
            if call_args[0] == self.pair.t2.symbol:
                self.config_manager.general_log.info("[TEST PASSED] Correctly created a BUY order.")
                passed = True
            else:
                self.config_manager.general_log.error(
                    f"[TEST FAILED] Incorrect order side. Expected BUY (maker={self.pair.t2.symbol}), got maker={call_args[0]}")
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_sell_order_creation_after_buy(self):
        """
        Tests that a SELL order is created after a BUY order has finished.
        """
        test_name = "SELL Order Creation After BUY"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False

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
            if call_args[0] == self.pair.t1.symbol:
                self.config_manager.general_log.info("[TEST PASSED] Correctly created a SELL order.")
                passed = True
            else:
                self.config_manager.general_log.error(
                    f"[TEST FAILED] Incorrect order side. Expected SELL (maker={self.pair.t1.symbol}), got maker={call_args[0]}")
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_price_variation_cancel_and_recreate_sell(self):
        """
        Tests that a SELL order is cancelled and recreated if price drops too much.
        """
        test_name = "Price Variation Cancel & Recreate (SELL)"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False

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
            if mocks['make_order'].call_count == 1:
                self.config_manager.general_log.info(
                    "[TEST PASSED] Correctly cancelled and recreated SELL order on price variation.")
                passed = True
            else:
                self.config_manager.general_log.error(
                    f"[TEST FAILED] make_order was called {mocks['make_order'].call_count} times, expected 1 for recreate.")
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_price_variation_no_cancel_buy(self):
        """
        Tests that a BUY order is NOT cancelled if price variation is within tolerance.
        """
        test_name = "Price Variation Within Tolerance (BUY)"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False

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
            passed = True
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_order_completion_flow(self):
        """
        Tests that a finished order is written to history and the next order is created.
        """
        test_name = "Order Completion Flow (SELL -> BUY)"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False

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
            if call_args[0] == self.pair.t2.symbol:
                self.config_manager.general_log.info("[TEST PASSED] Correctly wrote history and created next (BUY) order.")
                passed = True
            else:
                self.config_manager.general_log.error(
                    f"[TEST FAILED] Incorrect next order side. Expected BUY (maker={self.pair.t2.symbol}), got maker={call_args[0]}")
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_order_expiration_recreates_order(self):
        """
        Tests that if an open order expires, the bot recreates it based on the
        same side, rather than moving to the next step in the cycle.
        """
        test_name = "Order Expiration Recreates Order"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False

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
            if call_args[0] == self.pair.t1.symbol:
                self.config_manager.general_log.info("[TEST PASSED] Correctly recreated a SELL order after expiration.")
                passed = True
            else:
                self.config_manager.general_log.error(
                    f"[TEST FAILED] Incorrect order side after expiration. Expected SELL (maker={self.pair.t1.symbol}), got maker={call_args[0]}")
        self.test_results.append({'name': test_name, 'passed': passed})

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
            passed_favorable = favorable_base_price == favorable_live_price
            if passed_favorable:
                self.config_manager.general_log.info(
                    f"    [SUB-TEST PASSED] Bot correctly used the lower live price ({favorable_live_price}) as the new base.")
            else:
                self.config_manager.general_log.error(
                    f"    [SUB-TEST FAILED] Expected base price {favorable_live_price}, but got {favorable_base_price}.")

            # --- Sub-test 2: Unfavorable move (price rises) ---
            self.config_manager.general_log.info("\n  - Testing unfavorable market move (price rises)...")

            # Act: Live price rises to 0.32, which is higher than the last sell price
            self._set_mock_cex_price(0.32)
            self.pair.dex.init_virtual_order()

            # Assert: The bot should lock its price to the last sell price, ignoring the higher live price
            unfavorable_base_price = self.pair.dex.current_order['org_pprice']
            passed_unfavorable = unfavorable_base_price == last_sell_price
            if passed_unfavorable:
                self.config_manager.general_log.info(
                    f"    [SUB-TEST PASSED] Bot correctly locked the base price to the last sell price ({last_sell_price}).")
            else:
                self.config_manager.general_log.error(
                    f"    [SUB-TEST FAILED] Expected locked base price {last_sell_price}, but got {unfavorable_base_price}.")

        # Final result for the whole test case
        final_passed = passed_favorable and passed_unfavorable
        self.test_results.append({'name': test_name, 'passed': final_passed})

    async def _test_insufficient_balance(self):
        """
        Tests that an order is not created if the wallet balance is insufficient.
        """
        test_name = "Insufficient Balance for SELL Order"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False

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
            passed = True

            # Cleanup
            self.pair.t1.dex.free_balance = original_balance
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_concurrency_throttling(self):
        """
        Tests that the semaphore correctly limits concurrent task execution.
        """
        test_name = "Concurrency Throttling with Semaphore"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False

        # Mock a controller and processor with a semaphore limit of 1
        mock_loop = asyncio.get_event_loop()
        mock_controller = MainController(self.config_manager, mock_loop)
        self.config_manager.controller = mock_controller

        # Temporarily override the config for the test to set a low concurrency limit
        original_concurrency = getattr(self.config_manager.config_xbridge, 'max_concurrent_tasks', 5)
        self.config_manager.config_xbridge.max_concurrent_tasks = 1

        # We need to re-initialize the processor to pick up the new semaphore limit
        processor = TradingProcessor(mock_controller)

        # Arrange
        # Use events to control the flow of tasks instead of relying on time.sleep
        task1_started_running = asyncio.Event()
        task1_can_finish = asyncio.Event()
        execution_log = []

        async def controlled_task_1(pair_mock):
            # This task will acquire the semaphore and then pause
            execution_log.append('task1_started')
            task1_started_running.set()  # Signal that we are inside the task and holding the semaphore
            await task1_can_finish.wait()  # Block until the test lets us continue
            execution_log.append('task1_finished')

        async def controlled_task_2(pair_mock):
            # This task will start and finish quickly
            execution_log.append('task2_started')
            execution_log.append('task2_finished')

        task_map = {'pair1': controlled_task_1, 'pair2': controlled_task_2}

        async def task_runner(pair):
            await task_map[pair.name](pair)

        mock_pair1 = MagicMock(disabled=False)
        mock_pair1.name = 'pair1'
        mock_pair2 = MagicMock(disabled=False)
        mock_pair2.name = 'pair2'
        processor.pairs_dict = {'pair1': mock_pair1, 'pair2': mock_pair2}

        # Act
        processing_task = asyncio.create_task(processor.process_pairs(task_runner))
        await asyncio.wait_for(task1_started_running.wait(), timeout=1)

        # Assert
        if 'task2_started' not in execution_log:
            self.config_manager.general_log.info("[SUB-TEST PASSED] Task 2 correctly blocked by semaphore.")
            passed = True
        else:
            self.config_manager.general_log.error("[SUB-TEST FAILED] Task 2 started while Task 1 held the semaphore.")
            passed = False

        # Let the first task finish and wait for the whole process to complete
        task1_can_finish.set()
        await processing_task

        if passed:
            self.config_manager.general_log.info(f"[TEST PASSED] Semaphore correctly throttled concurrent tasks.")

        # Cleanup
        self.config_manager.config_xbridge.max_concurrent_tasks = original_concurrency
        self.test_results.append({'name': test_name, 'passed': passed})