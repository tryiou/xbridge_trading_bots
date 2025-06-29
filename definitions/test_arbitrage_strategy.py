import asyncio
import os
import uuid
import json
from unittest.mock import patch, AsyncMock

from strategies.arbitrage_strategy import TradeState

from contextlib import contextmanager

class ArbitrageStrategyTester:
    """
    A dedicated class to test the state management and recovery logic
    of the ArbitrageStrategy.
    """

    def __init__(self, strategy_instance):
        self.strategy = strategy_instance
        self.config_manager = strategy_instance.config_manager
        self.test_results = []

    @contextmanager
    def _patch_dependencies(self):
        """A context manager to patch all external dependencies for tests."""
        with patch.object(self.strategy, '_monitor_xbridge_order', return_value=True) as mock_monitor_xb, \
             patch.object(self.strategy, '_monitor_thorchain_swap', return_value=True) as mock_monitor_thor, \
             patch('definitions.thorchain_def.execute_thorchain_swap', return_value="mock_thor_txid") as mock_exec_thor, \
             patch('definitions.thorchain_def.get_thorchain_quote') as mock_get_quote, \
             patch('asyncio.sleep', return_value=None):  # Patch sleep to make tests run instantly
            yield {
                'monitor_xb': mock_monitor_xb,
                'monitor_thor': mock_monitor_thor,
                'exec_thor': mock_exec_thor,
                'get_quote': mock_get_quote
            }



    async def _get_mock_leg_result(self, profitable=True):
        """
        Generates a mock leg_result dictionary, similar to what
        _check_arbitrage_leg would produce. This decouples the tests from
        the implementation details of the checking logic.
        """
        pair_symbol = next(iter(self.config_manager.pairs))
        pair_instance = self.config_manager.pairs[pair_symbol]

        # Ensure tokens have addresses for the test
        if not pair_instance.t1.dex.address: await pair_instance.t1.dex.read_address()
        if not pair_instance.t2.dex.address: await pair_instance.t2.dex.read_address()

        # Mock a profitable or unprofitable quote
        if profitable:
            # Returns 0.0515 LTC for 75 DOGE, which is profitable vs the 0.05 LTC cost
            mock_quote = {
                'expected_amount_out': str(int(0.0515 * 10 ** 8)),
                'fees': {'outbound': str(int(0.0001 * 10 ** 8))},
                'memo': f'SWAP:{pair_instance.t1.symbol}.{pair_instance.t1.symbol}:{pair_instance.t1.dex.address}',
                'inbound_address': 'mock_thor_inbound_address_for_' + pair_instance.t2.symbol
            }
        else:
            # Returns 0.04 LTC for 75 DOGE, which is unprofitable vs the 0.05 LTC cost
            mock_quote = {
                'expected_amount_out': str(int(0.04 * 10 ** 8)),
                'fees': {'outbound': str(int(0.0001 * 10 ** 8))},
                'memo': f'SWAP:{pair_instance.t1.symbol}.{pair_instance.t1.symbol}:{pair_instance.t1.dex.address}',
                'inbound_address': 'mock_thor_inbound_address_for_' + pair_instance.t2.symbol
            }

        execution_data = {
            'leg': 1,
            'xbridge_from_amount': 0.05,
            'pair_symbol': pair_symbol,
            'xbridge_fee': 0.00005, # Add the fee for consistent re-evaluation
            'xbridge_order_id': f'mock_xb_order_{uuid.uuid4()}',
            'xbridge_from_token': pair_instance.t1.symbol,
            'xbridge_to_token': pair_instance.t2.symbol,
            'thorchain_memo': mock_quote['memo'],
            'thorchain_inbound_address': mock_quote['inbound_address'],
            'thorchain_from_token': pair_instance.t2.symbol,
            'thorchain_to_token': pair_instance.t1.symbol,
            'thorchain_swap_amount': 75.0,
            'thorchain_quote': mock_quote,
        }

        return {
            'profitable': profitable,
            'opportunity_details': 'Mocked opportunity',
            'execution_data': execution_data,
            'report': 'Mocked report'
        }

    async def run_arbitrage_test(self, leg_to_test: int):
        """
        Runs a one-off test of the arbitrage execution logic for a specific leg.
        This method constructs mock data, calls the internal _check_arbitrage_leg
        to generate execution data, and then calls the execute_arbitrage method in test mode.
        This ensures the test uses the actual calculation logic from the strategy.
        """
        if not self.strategy.test_mode:
            self.config_manager.general_log.error("run_arbitrage_test can only be run if test_mode is enabled.")
            return

        # Use the first configured pair for the test
        pair_symbol = next(iter(self.config_manager.pairs))
        pair_instance = self.config_manager.pairs[pair_symbol]
        check_id = "test-run-leg"

        self.config_manager.general_log.info(f"Using pair {pair_symbol} for the test.")

        # Ensure tokens have addresses for the test
        if not pair_instance.t1.dex.address: await pair_instance.t1.dex.read_address()
        if not pair_instance.t2.dex.address: await pair_instance.t2.dex.read_address()

        # Common mock data
        mock_order_id = f'mock_xb_order_{uuid.uuid4()}'
        mock_xb_price = 1500.0
        mock_order_amount_t1 = 0.05
        leg_result = None

        if leg_to_test == 1:
            self.config_manager.general_log.info("Testing Leg 1: Sell XBridge, Buy Thorchain")
            with patch('definitions.thorchain_def.get_thorchain_quote') as mock_get_quote:
                mock_get_quote.return_value = {
                    'expected_amount_out': str(int(0.0515 * 10 ** 8)),  # e.g., 0.0515 t1
                    'fees': {'outbound': str(int(0.0001 * 10 ** 8))},  # e.g., 0.0001 t1 fee
                    'memo': f'SWAP:{pair_instance.t1.symbol}.{pair_instance.t1.symbol}:{pair_instance.t1.dex.address}',
                    'inbound_address': 'mock_thor_inbound_address_for_' + pair_instance.t2.symbol
                }
                mock_bids = [[str(mock_xb_price), str(mock_order_amount_t1), mock_order_id]]
                leg_result = await self.strategy._check_arbitrage_leg(pair_instance, mock_bids, check_id, 'bid')

        elif leg_to_test == 2:
            self.config_manager.general_log.info("Testing Leg 2: Buy XBridge, Sell Thorchain")
            with patch('definitions.thorchain_def.get_thorchain_quote') as mock_get_quote:
                mock_get_quote.return_value = {
                    'expected_amount_out': str(int(80 * 10 ** 8)),  # e.g., 80 t2
                    'fees': {'outbound': str(int(0.1 * 10 ** 8))},  # e.g., 0.1 t2 fee
                    'memo': f'SWAP:{pair_instance.t2.symbol}.{pair_instance.t2.symbol}:{pair_instance.t2.dex.address}',
                    'inbound_address': 'mock_thor_inbound_address_for_' + pair_instance.t1.symbol
                }
                mock_asks = [[str(mock_xb_price), str(mock_order_amount_t1), mock_order_id]]
                leg_result = await self.strategy._check_arbitrage_leg(pair_instance, mock_asks, check_id, 'ask')

        else:
            self.config_manager.general_log.error(f"Invalid leg_to_test: {leg_to_test}. Must be 1 or 2.")
            return

        if leg_result and leg_result.get('profitable'):
            self.config_manager.general_log.info(f"--- [TEST] Profitability Report ---\n{leg_result['report']}\n--- [TEST] End of Report ---")
            self.config_manager.general_log.info(f"Leg {leg_to_test} Test: Profitable arbitrage found: {leg_result['opportunity_details']}")
            await self.strategy.execute_arbitrage(leg_result, check_id)
        else:
            self.config_manager.general_log.warning(f"Leg {leg_to_test} Test: No profitable arbitrage found with mock data.")
            if leg_result: self.config_manager.general_log.info(f"--- [TEST] Non-Profitable Report ---\n{leg_result['report']}\n--- [TEST] End of Report ---")

    async def run_all_tests(self):
        """Runs the full suite of state management and recovery tests."""
        if not self.strategy.test_mode:
            self.config_manager.general_log.error("run_all_tests can only be run if test_mode is enabled.")
            return

        self.config_manager.general_log.info("--- Starting State Management Test Suite ---")
        await self._test_full_trade_success()
        await self._test_resume_from_xb_initiated()
        await self._test_resume_from_xb_confirmed_profitable()
        await self._test_resume_from_xb_confirmed_unprofitable()
        await self._test_resume_from_thor_initiated()
        await self._test_resume_with_thor_refund()
        await self._test_execute_with_xb_monitor_failure()
        await self._test_check_leg_profit_margin_edge_case()
        await self._test_check_leg_insufficient_balance()
        await self._test_execute_with_xb_take_order_failure()
        await self._test_execute_with_thor_swap_failure()
        await self._test_insufficient_block_fee_balance()
        self.config_manager.general_log.info("\n--- State Management Test Suite Finished ---")
        self._print_summary()

    def _print_summary(self):
        """Prints a formatted summary of the test suite results."""
        summary_lines = [
            "\n" + "="*60,
            "--- Test Suite Summary ---".center(60),
            "="*60
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

        # Use the general logger to print the summary clearly
        self.config_manager.general_log.info("\n".join(summary_lines))

    async def _test_full_trade_success(self):
        """Test a full, uninterrupted trade execution, ensuring state file is cleaned up."""
        test_name = "Full Trade Success"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.config_manager)
        passed = False
        check_id = "test-full-success"

        leg_result = await self._get_mock_leg_result(profitable=True)

        with patch.object(self.strategy, '_monitor_xbridge_order', return_value=True), \
             patch.object(self.strategy, '_monitor_thorchain_swap', return_value=True):
            await self.strategy.execute_arbitrage(leg_result, check_id)

        # Verification
        state_file = os.path.join(self.config_manager.ROOT_DIR, "data", "arbitrage_states", f"{check_id}.json")
        if os.path.exists(state_file):
            self.config_manager.general_log.error(f"[TEST FAILED] State file {state_file} was not deleted after successful trade.")
            passed = False
        else:
            self.config_manager.general_log.info("[TEST PASSED] State file was correctly deleted.")
            passed = True
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_resume_from_xb_initiated(self):
        """Test resuming from a state where XBridge trade was initiated but not confirmed."""
        test_name = "Resume from XBRIDGE_INITIATED"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.config_manager)
        passed = False
        check_id = "test-resume-xb-init"
        state = TradeState(self.config_manager, check_id)

        with self._patch_dependencies() as mocks:
            # 1. Manually create the 'interrupted' state file
            leg_result = await self._get_mock_leg_result(profitable=True)
            state.save('XBRIDGE_INITIATED', {
                'execution_data': leg_result['execution_data'],
                'xbridge_trade_id': 'mock_xb_trade_id'
            })
            mocks['get_quote'].return_value = leg_result['execution_data']['thorchain_quote']

            # 2. Run the resumption logic
            await self.strategy.resume_interrupted_trades()

        # 3. Verification
        mocks['monitor_xb'].assert_called_once()
        mocks['exec_thor'].assert_called_once()
        mocks['monitor_thor'].assert_called_once()
        state_file = os.path.join(self.config_manager.ROOT_DIR, "data", "arbitrage_states", f"{check_id}.json")
        if os.path.exists(state_file):
            self.config_manager.general_log.error(f"[TEST FAILED] State file {state_file} was not deleted after successful resumption.")
            passed = False
        else:
            self.config_manager.general_log.info("[TEST PASSED] Resumption from XBRIDGE_INITIATED completed successfully.")
            passed = True
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_resume_from_xb_confirmed_profitable(self):
        """Test resuming from XBRIDGE_CONFIRMED where the trade is still profitable."""
        test_name = "Resume from XBRIDGE_CONFIRMED (Profitable)"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.config_manager)
        passed = False
        check_id = "test-resume-xb-profit"
        state = TradeState(self.config_manager, check_id)

        with self._patch_dependencies() as mocks:
            # 1. Create the state file
            leg_result = await self._get_mock_leg_result(profitable=True)
            state.save('XBRIDGE_CONFIRMED', {
                'execution_data': leg_result['execution_data'],
                'xbridge_trade_id': 'mock_xb_trade_id'
            })
            mocks['get_quote'].return_value = leg_result['execution_data']['thorchain_quote']

            # 2. Run resumption
            await self.strategy.resume_interrupted_trades()

        # 3. Verification
        mocks['get_quote'].assert_called_once()
        mocks['exec_thor'].assert_called_once()
        mocks['monitor_thor'].assert_called_once()
        state_file = os.path.join(self.config_manager.ROOT_DIR, "data", "arbitrage_states", f"{check_id}.json")
        if os.path.exists(state_file):
            self.config_manager.general_log.error(f"[TEST FAILED] State file {state_file} was not deleted after successful resumption.")
            passed = False
        else:
            self.config_manager.general_log.info("[TEST PASSED] Resumption from XBRIDGE_CONFIRMED (Profitable) completed successfully.")
            passed = True
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_resume_from_xb_confirmed_unprofitable(self):
        """Test resuming from XBRIDGE_CONFIRMED where the trade is now unprofitable, forcing an abort."""
        test_name = "Resume from XBRIDGE_CONFIRMED (Unprofitable)"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.config_manager)
        passed = False
        check_id = "test-resume-xb-loss"
        state = TradeState(self.config_manager, check_id)

        with self._patch_dependencies() as mocks:
            # 1. Create the state file
            leg_result = await self._get_mock_leg_result(profitable=True) # Start with a profitable scenario
            state.save('XBRIDGE_CONFIRMED', {
                'execution_data': leg_result['execution_data'],
                'xbridge_trade_id': 'mock_xb_trade_id'
            })
            # 2. Now, create an unprofitable quote for the re-evaluation
            unprofitable_quote = (await self._get_mock_leg_result(profitable=False))['execution_data']['thorchain_quote']
            mocks['get_quote'].return_value = unprofitable_quote

            # 3. Run resumption
            await self.strategy.resume_interrupted_trades()

        # 4. Verification
        mocks['get_quote'].assert_called_once()
        mocks['exec_thor'].assert_not_called() # Crucially, a new swap should NOT be executed
        archive_file_found = any(f.startswith(check_id) for f in os.listdir(os.path.join(state.state_dir, "archive")))
        if archive_file_found:
            self.config_manager.general_log.info("[TEST PASSED] Unprofitable trade was correctly aborted and state archived.")
            passed = True
        else:
            self.config_manager.general_log.error("[TEST FAILED] State file was not archived for unprofitable trade.")
            passed = False
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_resume_from_thor_initiated(self):
        """Test resuming from a state where Thorchain swap was initiated but not confirmed."""
        test_name = "Resume from THORCHAIN_INITIATED"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.config_manager)
        passed = False
        check_id = "test-resume-thor-init"
        state = TradeState(self.config_manager, check_id)

        with self._patch_dependencies() as mocks:
            # 1. Create the state file
            leg_result = await self._get_mock_leg_result(profitable=True)
            state.save('THORCHAIN_INITIATED', {
                'execution_data': leg_result['execution_data'],
                'xbridge_trade_id': 'mock_xb_trade_id',
                'thorchain_txid': 'mock_thor_txid'
            })
            # 2. Run resumption
            await self.strategy.resume_interrupted_trades()

        # 3. Verification
        mocks['monitor_thor'].assert_called_once()
        state_file = os.path.join(self.config_manager.ROOT_DIR, "data", "arbitrage_states", f"{check_id}.json")
        if os.path.exists(state_file):
            self.config_manager.general_log.error(f"[TEST FAILED] State file {state_file} was not deleted after successful resumption.")
            passed = False
        else:
            self.config_manager.general_log.info("[TEST PASSED] Resumption from THORCHAIN_INITIATED completed successfully.")
            passed = True
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_resume_with_thor_refund(self):
        """Test resuming from THORCHAIN_INITIATED where the swap is refunded, pausing all trading."""
        test_name = "Resume from THORCHAIN_INITIATED (Refunded) and Pause"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.config_manager)
        # Clean up pause file from previous runs
        if os.path.exists(self.strategy.pause_file_path):
            os.remove(self.strategy.pause_file_path)
        passed = False
        check_id = "test-resume-thor-refund"
        state = TradeState(self.config_manager, check_id)

        # --- Part 1: Trigger the refund and verify pause ---
        with self._patch_dependencies() as mocks:
            mocks['monitor_thor'].return_value = False

            leg_result = await self._get_mock_leg_result(profitable=True)
            state.save('THORCHAIN_INITIATED', {
                'execution_data': leg_result['execution_data'],
                'xbridge_trade_id': 'mock_xb_trade_id', 'thorchain_txid': 'mock_thor_txid'
            })
            await self.strategy.resume_interrupted_trades()

        pause_file_found = os.path.exists(self.strategy.pause_file_path)
        state_is_awaiting_refund = False
        current_state_status = "NOT_FOUND"
        if os.path.exists(state.state_file_path):
            with open(state.state_file_path, 'r') as f:
                current_state = json.load(f)
            current_state_status = current_state.get('status')
            state_is_awaiting_refund = current_state_status == 'AWAITING_REFUND'

        if pause_file_found and state_is_awaiting_refund:
            self.config_manager.general_log.info("[TEST PASSED] Refunded trade correctly created pause file and set state to AWAITING_REFUND.")
            passed = True
        else:
            self.config_manager.general_log.error(f"[TEST FAILED] Refunded trade state not handled correctly (pause: {pause_file_found}, state: {current_state_status}).")
            passed = False
            self.test_results.append({'name': test_name, 'passed': passed})
            if os.path.exists(self.strategy.pause_file_path): os.remove(self.strategy.pause_file_path)
            return

        # --- Part 2: Verify that trading is paused ---
        with patch.object(self.strategy, '_check_arbitrage_leg') as mock_check_leg:
            pair_instance = self.config_manager.pairs[next(iter(self.config_manager.pairs))]
            await self.strategy.thread_loop_async_action(pair_instance)
            if mock_check_leg.called:
                self.config_manager.general_log.error("[TEST FAILED] Bot continued to check for trades despite pause file.")
                passed = False
            else:
                self.config_manager.general_log.info("[TEST PASSED] Bot correctly paused trading operations.")

        # --- Part 3: Simulate refund confirmation and verify resumption ---
        with patch.object(self.strategy, '_verify_refund_received', return_value=True) as mock_verify_refund:
            await self.strategy.resume_interrupted_trades()

        mock_verify_refund.assert_called_once()
        pause_file_gone = not os.path.exists(self.strategy.pause_file_path)
        archive_file_found = any(f.startswith(check_id) for f in os.listdir(os.path.join(state.state_dir, "archive")))
        if pause_file_gone and archive_file_found:
            self.config_manager.general_log.info("[TEST PASSED] Bot correctly resumed trading after confirming refund.")
            passed = True
        else:
            self.config_manager.general_log.error(f"[TEST FAILED] Bot did not resume correctly (pause_gone: {pause_file_gone}, archived: {archive_file_found}).")
            passed = False

        if os.path.exists(self.strategy.pause_file_path):
            os.remove(self.strategy.pause_file_path)

        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_execute_with_xb_monitor_failure(self):
        """Test execution aborts correctly if the XBridge order monitoring fails."""
        test_name = "Execute with XBridge Monitor Failure"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.config_manager)
        passed = False
        check_id = "test-xb-monitor-fail"
        state = TradeState(self.config_manager, check_id)

        leg_result = await self._get_mock_leg_result(profitable=True)

        # Mock _monitor_xbridge_order to return False, simulating a timeout or error
        with patch.object(self.strategy, '_monitor_xbridge_order', return_value=False) as mock_monitor_xb, \
             patch.object(self.strategy, '_monitor_thorchain_swap') as mock_monitor_thor:
            await self.strategy.execute_arbitrage(leg_result, check_id)

        # Verification
        mock_monitor_xb.assert_called_once()
        mock_monitor_thor.assert_not_called()  # Thorchain part should never be reached
        archive_file_found = any(f.startswith(check_id) for f in os.listdir(os.path.join(state.state_dir, "archive")))
        if archive_file_found:
            self.config_manager.general_log.info("[TEST PASSED] Trade was correctly aborted and state archived on XBridge monitor failure.")
            passed = True
        else:
            self.config_manager.general_log.error("[TEST FAILED] State file was not archived after XBridge monitor failure.")
            passed = False
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_check_leg_profit_margin_edge_case(self):
        """Tests that a trade with profit > 0 but < min_profit_margin is not marked as profitable."""
        test_name = "Profit Margin Edge Case"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        
        # Temporarily set a higher profit margin for this test
        original_margin = self.strategy.min_profit_margin
        self.strategy.min_profit_margin = 0.02  # 2%

        pair_instance = self.config_manager.pairs[next(iter(self.config_manager.pairs))]
        check_id = "test-profit-edge"

        # Mock data that will result in a ~1.7% profit, which is below the 2% threshold
        mock_bids = [[str(1500.0), str(0.05), f'mock_xb_order_{uuid.uuid4()}']]

        with patch('definitions.thorchain_def.get_thorchain_quote') as mock_get_quote:
            # This quote results in a net profit of 0.00085 LTC (1.7%), which is below the 2% threshold.
            # Calculation: (0.0509_gross - 0.0001_thor_fee) - 0.05_cost - 0.00005_xb_fee = 0.00075 profit
            # (0.00075 / 0.05) * 100 = 1.5%
            mock_get_quote.return_value = {
                'expected_amount_out': str(int(0.0509 * 10 ** 8)),
                'fees': {'outbound': str(int(0.0001 * 10 ** 8))},
                'memo': 'mock_memo', 'inbound_address': 'mock_inbound_address'
            }

            leg_result = await self.strategy._check_arbitrage_leg(pair_instance, mock_bids, check_id, 'bid')

        # Verification
        if leg_result and not leg_result.get('profitable'):
            self.config_manager.general_log.info("[TEST PASSED] Trade with profit below min_profit_margin was correctly identified as not profitable.")
            passed = True
        else:
            self.config_manager.general_log.error("[TEST FAILED] Profit margin edge case test failed.")
            passed = False

        # Restore original margin
        self.strategy.min_profit_margin = original_margin
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_check_leg_insufficient_balance(self):
        """Tests that the bot correctly skips an unaffordable order and evaluates the next one."""
        test_name = "Insufficient Balance Skips Order"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        pair_instance = self.config_manager.pairs[next(iter(self.config_manager.pairs))]
        check_id = "test-balance-skip"

        # Simulate having a balance of only 0.04 LTC
        original_balance = pair_instance.t1.dex.free_balance
        pair_instance.t1.dex.free_balance = 0.04

        # The first order (0.05 LTC) is unaffordable. The second (0.03 LTC) is affordable.
        mock_bids = [
            [str(1500.0), str(0.05), f'mock_xb_order_unaffordable'],
            [str(1510.0), str(0.03), f'mock_xb_order_affordable']
        ]

        # Set dry_mode to False for this test to ensure the balance check is triggered
        original_dry_mode = self.strategy.dry_mode
        self.strategy.dry_mode = False

        with patch('definitions.thorchain_def.get_thorchain_quote') as mock_get_quote:
            mock_get_quote.return_value = {
                'expected_amount_out': str(int(0.031 * 10 ** 8)), # Profitable for the 0.03 order
                'fees': {'outbound': str(int(0.0001 * 10 ** 8))},
                'memo': 'mock_memo', 'inbound_address': 'mock_inbound_address'
            }

            leg_result = await self.strategy._check_arbitrage_leg(pair_instance, mock_bids, check_id, 'bid')

        # Verification
        if leg_result and leg_result['execution_data']['xbridge_order_id'] == 'mock_xb_order_affordable':
            self.config_manager.general_log.info("[TEST PASSED] Bot correctly skipped unaffordable order and found the next profitable one.")
            passed = True
        else:
            self.config_manager.general_log.error("[TEST FAILED] Insufficient balance test failed.")
            passed = False

        # Restore original values
        pair_instance.t1.dex.free_balance = original_balance
        self.strategy.dry_mode = original_dry_mode
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_execute_with_xb_take_order_failure(self):
        """Test execution aborts correctly if the initial XBridge take_order call fails."""
        test_name = "Execute with XBridge take_order Failure"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.config_manager)
        passed = False
        check_id = "test-xb-take-order-fail"
        state = TradeState(self.config_manager, check_id)

        leg_result = await self._get_mock_leg_result(profitable=True)

        # Mock take_order to return None, simulating a failure (e.g., order already taken)
        with patch.object(self.config_manager.xbridge_manager, 'take_order', return_value=None) as mock_take_order:
            await self.strategy.execute_arbitrage(leg_result, check_id)

        # Verification
        mock_take_order.assert_called_once()
        # The state file should be archived, not left in the main directory
        state_file_exists = os.path.exists(state.state_file_path)
        archive_file_found = any(f.startswith(check_id) for f in os.listdir(os.path.join(state.state_dir, "archive")))
        if not state_file_exists and archive_file_found:
            self.config_manager.general_log.info("[TEST PASSED] Trade was correctly aborted and state archived on take_order failure.")
            passed = True
        else:
            self.config_manager.general_log.error("[TEST FAILED] State file was not handled correctly on take_order failure.")
            passed = False
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_execute_with_thor_swap_failure(self):
        """Test that a failed Thorchain swap initiation leaves the state as XBRIDGE_CONFIRMED for resumption."""
        test_name = "Execute with Thorchain Swap Failure"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.config_manager)
        passed = False
        check_id = "test-thor-init-fail"
        state = TradeState(self.config_manager, check_id)

        leg_result = await self._get_mock_leg_result(profitable=True)

        with self._patch_dependencies() as mocks:
            # Simulate the Thorchain swap execution failing to return a TXID
            mocks['exec_thor'].return_value = None
            await self.strategy.execute_arbitrage(leg_result, check_id)

        # Verification
        mocks['exec_thor'].assert_called_once()
        # The state file should still exist with status XBRIDGE_CONFIRMED, ready for the next resumption attempt.
        if os.path.exists(state.state_file_path):
            with open(state.state_file_path, 'r') as f:
                final_state = json.load(f)
            if final_state.get('status') == 'XBRIDGE_CONFIRMED':
                self.config_manager.general_log.info("[TEST PASSED] State correctly left as XBRIDGE_CONFIRMED after Thorchain init failure.")
                passed = True
            else:
                self.config_manager.general_log.error(f"[TEST FAILED] State had incorrect status '{final_state.get('status')}' after Thorchain init failure.")
                passed = False
        else:
            self.config_manager.general_log.error("[TEST FAILED] State file was incorrectly deleted or archived after Thorchain init failure.")
            passed = False

        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_insufficient_block_fee_balance(self):
        """Tests that the main loop skips checks if BLOCK balance is too low for the taker fee."""
        test_name = "Insufficient BLOCK Fee Balance"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        pair_instance = self.config_manager.pairs[next(iter(self.config_manager.pairs))]

        # Simulate having an insufficient BLOCK balance
        block_token = self.config_manager.tokens.get('BLOCK')
        original_balance = block_token.dex.free_balance
        block_token.dex.free_balance = 0.001  # Less than the 0.015 fee

        # Set dry_mode to False to ensure the balance check is triggered
        original_dry_mode = self.strategy.dry_mode
        self.strategy.dry_mode = False

        with patch.object(self, 'strategy', wraps=self.strategy) as spy_strategy:
            await spy_strategy.thread_loop_async_action(pair_instance)
            # Verification: The core logic to check for arbitrage should not have been called.
            if not any(call.name == '_check_arbitrage_leg' for call in spy_strategy.method_calls):
                self.config_manager.general_log.info("[TEST PASSED] Arbitrage check was correctly skipped due to low BLOCK balance.")
                passed = True
            else:
                self.config_manager.general_log.error("[TEST FAILED] Arbitrage check was not skipped despite low BLOCK balance.")
                passed = False

        # Restore original values
        block_token.dex.free_balance = original_balance
        self.strategy.dry_mode = original_dry_mode
        self.test_results.append({'name': test_name, 'passed': passed})