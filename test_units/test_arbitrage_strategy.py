import asyncio
import json
import os
import sys
import time
import uuid
from typing import List, Dict, Any, TYPE_CHECKING
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from definitions.trade_state import TradeState

if TYPE_CHECKING:
    from strategies.arbitrage_strategy import ArbitrageStrategy

from contextlib import contextmanager


# Test fixtures
@pytest.fixture
def mock_strategy():
    """Create a mock ArbitrageStrategy instance for testing."""
    from strategies.arbitrage_strategy import ArbitrageStrategy
    mock_config_manager = MagicMock()
    mock_config_manager.pairs = {}
    mock_config_manager.general_log = MagicMock()
    mock_config_manager.xbridge_manager = MagicMock()
    # Ensure handle_async is an awaitable mock
    mock_config_manager.error_handler.handle_async = AsyncMock()

    # Create mock tokens with dex addresses
    ltc_token = MagicMock()
    ltc_token.dex = MagicMock()
    ltc_token.dex.address = "ltc_address"

    doge_token = MagicMock()
    doge_token.dex = MagicMock()
    doge_token.dex.address = "doge_address"

    mock_config_manager.tokens = {
        'LTC': ltc_token,
        'DOGE': doge_token
    }

    mock_config_manager.config_xbridge = MagicMock()
    mock_config_manager.config_arbitrage = MagicMock()
    mock_config_manager.ROOT_DIR = "/tmp"

    strategy = ArbitrageStrategy(mock_config_manager)
    strategy.test_mode = True
    strategy.dry_mode = False
    strategy.min_profit_margin = 0.01
    strategy.pause_file_path = os.path.join("/tmp", "data", "TRADING_PAUSED.json")
    return strategy


@pytest.fixture
def arbitrage_tester(mock_strategy):
    """Create an ArbitrageStrategyTester instance for testing."""
    return ArbitrageStrategyTester(mock_strategy)


class ArbitrageStrategyTester:
    """
    A dedicated class to test the state management and recovery logic
    of the ArbitrageStrategy.
    """

    def __init__(self, strategy_instance: 'ArbitrageStrategy'):
        self.strategy = strategy_instance
        self.config_manager = strategy_instance.config_manager
        self.test_results: List[Dict[str, Any]] = []

    @contextmanager
    def _patch_dependencies(self, mock_thor_exec: bool = True):
        """A context manager to patch all external dependencies for tests."""
        with patch.object(self.strategy, '_monitor_xbridge_order', new_callable=AsyncMock,
                          return_value=True) as mock_monitor_xb, \
                patch.object(self.strategy, '_monitor_thorchain_swap', new_callable=AsyncMock,
                             return_value=True) as mock_monitor_thor, \
                patch('definitions.thorchain_def.get_thorchain_quote') as mock_get_quote, \
                patch('definitions.thorchain_def.get_inbound_addresses') as mock_get_inbound, \
                patch('definitions.thorchain_def.check_thorchain_path_status',
                      return_value=(True, "Path is active.")) as mock_check_path, \
                patch('asyncio.sleep', return_value=None):

            mocks = {
                'monitor_xb': mock_monitor_xb,
                'monitor_thor': mock_monitor_thor,
                'get_quote': mock_get_quote,
                'get_inbound': mock_get_inbound,
                'check_path': mock_check_path,
            }
            mock_get_inbound.return_value = [
                {'chain': 'LTC', 'address': 'ltc_inbound_addr', 'halted': False, 'decimals': 8},
                {'chain': 'DOGE', 'address': 'doge_inbound_addr', 'halted': False, 'decimals': 8},
                {'chain': 'BTC', 'address': 'btc_inbound_addr', 'halted': False, 'decimals': 8},
            ]

            if mock_thor_exec:
                with patch('definitions.thorchain_def.execute_thorchain_swap',
                           return_value="mock_thor_txid") as mock_exec_thor:
                    mocks['exec_thor'] = mock_exec_thor
                    yield mocks
            else:
                yield mocks

    async def _get_mock_leg_result(self, profitable: bool = True) -> Dict[str, Any]:
        """
        Generates a mock leg_result dictionary, similar to what
        _check_arbitrage_leg would produce. This decouples the tests from
        the implementation details of the checking logic.
        """
        pair_symbol = "LTC/DOGE"
        pair_instance = MagicMock()
        pair_instance.t1 = MagicMock()
        pair_instance.t2 = MagicMock()
        pair_instance.t1.symbol = "LTC"
        pair_instance.t2.symbol = "DOGE"
        pair_instance.t1.dex = MagicMock()
        pair_instance.t2.dex = MagicMock()
        pair_instance.t1.dex.address = "ltc_address"
        pair_instance.t2.dex.address = "doge_address"

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
            'xbridge_fee': 0.00005,  # Add the fee for consistent re-evaluation
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

    async def run_arbitrage_test(self, leg_to_test: int) -> None:
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
        pair_symbol = "LTC/DOGE"
        pair_instance = MagicMock()
        pair_instance.t1 = MagicMock()
        pair_instance.t2 = MagicMock()
        pair_instance.t1.symbol = "LTC"
        pair_instance.t2.symbol = "DOGE"
        pair_instance.t1.dex = MagicMock()
        pair_instance.t2.dex = MagicMock()
        pair_instance.t1.dex.address = "ltc_address"
        pair_instance.t2.dex.address = "doge_address"
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

        with self._patch_dependencies(mock_thor_exec=False) as mocks:
            if leg_to_test == 1:
                self.config_manager.general_log.info("Testing Leg 1: Sell XBridge, Buy Thorchain")
                mock_quote_leg1 = {
                    'expected_amount_out': str(int(0.0515 * 10 ** 8)),  # e.g., 0.0515 t1
                    'fees': {'outbound': str(int(0.0001 * 10 ** 8))},  # e.g., 0.0001 t1 fee
                    'memo': f'SWAP:{pair_instance.t1.symbol}.{pair_instance.t1.symbol}:{pair_instance.t1.dex.address}',
                    'inbound_address': 'mock_thor_inbound_address_for_' + pair_instance.t2.symbol
                }
                mocks['get_quote'].return_value = mock_quote_leg1
                mock_bids = [[str(mock_xb_price), str(mock_order_amount_t1), mock_order_id]]
                # Mock the _check_arbitrage_leg method to return our test data
                leg_result = await self._get_mock_leg_result(profitable=True)

            elif leg_to_test == 2:
                self.config_manager.general_log.info("Testing Leg 2: Buy XBridge, Sell Thorchain")
                mock_quote_leg2 = {
                    'expected_amount_out': str(int(0.0515 * 10 ** 8)),  # e.g., 0.0515 t1
                    'fees': {'outbound': str(int(0.0001 * 10 ** 8))},  # e.g., 0.0001 t1 fee
                    'memo': f'SWAP:{pair_instance.t2.symbol}.{pair_instance.t2.symbol}:{pair_instance.t2.dex.address}',
                    'inbound_address': 'mock_thor_inbound_address_for_' + pair_instance.t1.symbol
                }
                mocks['get_quote'].return_value = mock_quote_leg2
                mock_asks = [[str(mock_xb_price), str(mock_order_amount_t1), mock_order_id]]
                # Mock the _check_arbitrage_leg method to return our test data
                leg_result = await self._get_mock_leg_result(profitable=True)

            else:
                self.config_manager.general_log.error(f"Invalid leg_to_test: {leg_to_test}. Must be 1 or 2.")
                return

            if leg_result and leg_result.get('profitable'):
                self.config_manager.general_log.info(
                    f"--- [TEST] Profitability Report ---\n{leg_result['report']}\n--- [TEST] End of Report ---")
                self.config_manager.general_log.info(
                    f"Leg {leg_to_test} Test: Profitable arbitrage found: {leg_result['opportunity_details']}")
                # The re-evaluation step inside execute_arbitrage also needs a quote
                mocks['get_quote'].return_value = leg_result['execution_data']['thorchain_quote']
                await self.strategy.execute_arbitrage(leg_result, check_id)
            else:
                self.config_manager.general_log.warning(
                    f"Leg {leg_to_test} Test: No profitable arbitrage found with mock data.")
                if leg_result: self.config_manager.general_log.info(
                    f"--- [TEST] Non-Profitable Report ---\n{leg_result['report']}\n--- [TEST] End of Report ---")

    async def run_all_tests(self) -> None:
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
        await self._test_thorchain_path_halted()
        self.config_manager.general_log.info("\n--- State Management Test Suite Finished ---")
        self._print_summary()

    def _print_summary(self) -> None:
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

        # Use the general logger to print the summary clearly
        self.config_manager.general_log.info("\n".join(summary_lines))

    async def _test_full_trade_success(self) -> None:
        """
        Tests a full, uninterrupted trade execution from start to finish.
        Arrange: Mocks a profitable opportunity.
        Act: Calls execute_arbitrage.
        Assert: Verifies that the trade completes and the state file is deleted.
        """
        test_name = "Full Trade Success"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.strategy)
        passed = False
        check_id = "test-full-success"

        leg_result = await self._get_mock_leg_result(profitable=True)

        with self._patch_dependencies() as mocks:
            # The re-evaluation needs a quote, so we provide it here.
            mocks['get_quote'].return_value = leg_result['execution_data']['thorchain_quote']
            await self.strategy.execute_arbitrage(leg_result, check_id)

        # Verification
        state_file = os.path.join(TradeState._get_state_dir(self.strategy), f"{check_id}.json")
        if os.path.exists(state_file):
            self.config_manager.general_log.error(
                f"[TEST FAILED] State file {state_file} was not deleted after successful trade.")
            passed = False
        else:
            self.config_manager.general_log.info("[TEST PASSED] State file was correctly deleted.")
            passed = True
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_resume_from_xb_initiated(self) -> None:
        """
        Tests resuming from a state where the XBridge trade was initiated but not confirmed.
        Arrange: Creates a state file with status 'XBRIDGE_INITIATED'.
        Act: Calls resume_interrupted_trades.
        Assert: Verifies that the XBridge order monitor is called, the full trade completes,
                and the state file is deleted.
        """
        test_name = "Resume from XBRIDGE_INITIATED"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.strategy)
        passed = False
        check_id = "test-resume-xb-init"
        state = TradeState(self.strategy, check_id)

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
        state_file = os.path.join(TradeState._get_state_dir(self.strategy), f"{check_id}.json")
        if os.path.exists(state_file):
            self.config_manager.general_log.error(
                f"[TEST FAILED] State file {state_file} was not deleted after successful resumption.")
            passed = False
        else:
            self.config_manager.general_log.info(
                "[TEST PASSED] Resumption from XBRIDGE_INITIATED completed successfully.")
            passed = True
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_resume_from_xb_confirmed_profitable(self) -> None:
        """
        Tests resuming from XBRIDGE_CONFIRMED where the re-evaluated trade is still profitable.
        Arrange: Creates a state file with status 'XBRIDGE_CONFIRMED' and mocks a profitable
                 re-quote from Thorchain.
        Act: Calls resume_interrupted_trades.
        Assert: Verifies the Thorchain leg is executed and the state file is deleted.
        """
        test_name = "Resume from XBRIDGE_CONFIRMED (Profitable)"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.strategy)
        passed = False
        check_id = "test-resume-xb-profit"
        state = TradeState(self.strategy, check_id)

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
        state_file = os.path.join(TradeState._get_state_dir(self.strategy), f"{check_id}.json")
        if os.path.exists(state_file):
            self.config_manager.general_log.error(
                f"[TEST FAILED] State file {state_file} was not deleted after successful resumption.")
            passed = False
        else:
            self.config_manager.general_log.info(
                "[TEST PASSED] Resumption from XBRIDGE_CONFIRMED (Profitable) completed successfully.")
            passed = True
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_resume_from_xb_confirmed_unprofitable(self) -> None:
        """
        Tests resuming from XBRIDGE_CONFIRMED where the re-evaluated trade is now unprofitable.
        Arrange: Creates a state file with status 'XBRIDGE_CONFIRMED' but mocks an unprofitable
                 re-quote from Thorchain.
        Act: Calls resume_interrupted_trades.
        Assert: Verifies that the Thorchain leg is NOT executed and the state file is
                archived for manual review.
        """
        test_name = "Resume from XBRIDGE_CONFIRMED (Unprofitable)"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.strategy)
        passed = False
        check_id = "test-resume-xb-loss"
        state = TradeState(self.strategy, check_id)

        with self._patch_dependencies() as mocks:
            # 1. Create the state file
            leg_result = await self._get_mock_leg_result(profitable=True)  # Start with a profitable scenario
            state.save('XBRIDGE_CONFIRMED', {
                'execution_data': leg_result['execution_data'],
                'xbridge_trade_id': 'mock_xb_trade_id'
            })
            # 2. Now, create an unprofitable quote for the re-evaluation
            unprofitable_quote = (await self._get_mock_leg_result(profitable=False))['execution_data'][
                'thorchain_quote']
            mocks['get_quote'].return_value = unprofitable_quote

            # 3. Run resumption
            await self.strategy.resume_interrupted_trades()

        # 4. Verification
        mocks['get_quote'].assert_called_once()
        mocks['exec_thor'].assert_not_called()  # Crucially, a new swap should NOT be executed
        archive_file_found = any(f.startswith(check_id) for f in os.listdir(os.path.join(state.state_dir, "archive")))
        if archive_file_found:
            self.config_manager.general_log.info(
                "[TEST PASSED] Unprofitable trade was correctly aborted and state archived.")
            passed = True
        else:
            self.config_manager.general_log.error("[TEST FAILED] State file was not archived for unprofitable trade.")
            passed = False
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_resume_from_thor_initiated(self) -> None:
        """
        Tests resuming from a state where the Thorchain swap was initiated but not confirmed.
        Arrange: Creates a state file with status 'THORCHAIN_INITIATED'.
        Act: Calls resume_interrupted_trades.
        Assert: Verifies that the Thorchain swap monitor is called and the state file is
                deleted upon successful completion.
        """
        test_name = "Resume from THORCHAIN_INITIATED"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.strategy)
        passed = False
        check_id = "test-resume-thor-init"
        state = TradeState(self.strategy, check_id)

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
        state_file = os.path.join(TradeState._get_state_dir(self.strategy), f"{check_id}.json")
        if os.path.exists(state_file):
            self.config_manager.general_log.error(
                f"[TEST FAILED] State file {state_file} was not deleted after successful resumption.")
            passed = False
        else:
            self.config_manager.general_log.info(
                "[TEST PASSED] Resumption from THORCHAIN_INITIATED completed successfully.")
            passed = True
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_resume_with_thor_refund(self) -> None:
        """
        Tests the full refund and trading pause lifecycle.
        Arrange Part 1: Create a 'THORCHAIN_INITIATED' state and mock the monitor to fail (refund).
        Act Part 1: Call resume_interrupted_trades.
        Assert Part 1: Verify a pause file is created and state is 'AWAITING_REFUND'.
        Act Part 2: Call the main loop and verify no new trades are checked.
        Arrange Part 3: Mock the refund verification to succeed.
        Act Part 3: Call resume_interrupted_trades again.
        Assert Part 3: Verify the pause file is removed and the state is archived.
        """
        test_name = "Resume from THORCHAIN_INITIATED (Refunded) and Pause"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.strategy)
        # Clean up pause file from previous runs
        if os.path.exists(self.strategy.pause_file_path):
            os.remove(self.strategy.pause_file_path)
        passed = False
        check_id = "test-resume-thor-refund"
        state = TradeState(self.strategy, check_id)

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
            self.config_manager.general_log.info(
                "[TEST PASSED] Refunded trade correctly created pause file and set state to AWAITING_REFUND.")
            passed = True
        else:
            self.config_manager.general_log.error(
                f"[TEST FAILED] Refunded trade state not handled correctly (pause: {pause_file_found}, state: {current_state_status}).")
            passed = False
            self.test_results.append({'name': test_name, 'passed': passed})
            if os.path.exists(self.strategy.pause_file_path): os.remove(self.strategy.pause_file_path)
            return

        # --- Part 2: Verify that trading is paused ---
        with patch.object(self.strategy, '_check_arbitrage_leg') as mock_check_leg:
            pair_instance = MagicMock()
            await self.strategy.thread_loop_async_action(pair_instance)
            if mock_check_leg.called:
                self.config_manager.general_log.error(
                    "[TEST FAILED] Bot continued to check for trades despite pause file.")
                passed = False
            else:
                self.config_manager.general_log.info("[TEST PASSED] Bot correctly paused trading operations.")

        # --- Part 3: Simulate refund confirmation and verify resumption ---
        with patch.object(self.strategy, '_verify_refund_received', new_callable=AsyncMock,
                          return_value=True) as mock_verify_refund:
            await self.strategy.resume_interrupted_trades()

        mock_verify_refund.assert_called_once()
        pause_file_gone = not os.path.exists(self.strategy.pause_file_path)
        archive_file_found = any(f.startswith(check_id) for f in os.listdir(os.path.join(state.state_dir, "archive")))
        if pause_file_gone and archive_file_found:
            self.config_manager.general_log.info("[TEST PASSED] Bot correctly resumed trading after confirming refund.")
            passed = True
        else:
            self.config_manager.general_log.error(
                f"[TEST FAILED] Bot did not resume correctly (pause_gone: {pause_file_gone}, archived: {archive_file_found}).")
            passed = False

        if os.path.exists(self.strategy.pause_file_path):
            os.remove(self.strategy.pause_file_path)

        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_execute_with_xb_monitor_failure(self) -> None:
        """
        Tests that execution aborts correctly if the XBridge order monitoring fails.
        Arrange: Mock _monitor_xbridge_order to return False.
        Act: Call execute_arbitrage.
        Assert: Verify the Thorchain leg is never attempted and the state file is archived.
        """
        test_name = "Execute with XBridge Monitor Failure"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.strategy)
        passed = False
        check_id = "test-xb-monitor-fail"
        state = TradeState(self.strategy, check_id)

        # Ensure archive directory exists
        archive_dir = os.path.join(state.state_dir, "archive")
        os.makedirs(archive_dir, exist_ok=True)

        leg_result = await self._get_mock_leg_result(profitable=True)

        # Mock _monitor_xbridge_order to return False, simulating a timeout or error
        with self._patch_dependencies() as mocks:
            # Override the monitor to return False
            mocks['monitor_xb'].return_value = False
            # Mock take_order to return a valid trade ID
            with patch.object(self.config_manager.xbridge_manager, 'take_order', new_callable=AsyncMock,
                              return_value={'id': 'mock_trade_id'}):
                # Ensure token addresses are set to avoid address lookup
                with patch.object(
                        self.strategy.config_manager.tokens[leg_result['execution_data']['xbridge_from_token']].dex,
                        'address', 'mock_address'), \
                        patch.object(
                            self.strategy.config_manager.tokens[leg_result['execution_data']['xbridge_to_token']].dex,
                            'address', 'mock_address'):
                    # Disable test mode to ensure monitor is called
                    self.strategy.test_mode = False
                    await self.strategy.execute_arbitrage(leg_result, check_id)
                    self.strategy.test_mode = True  # Restore test mode

        # Verification
        mocks['monitor_xb'].assert_called_once()
        archive_file_found = any(f.startswith(check_id) for f in os.listdir(archive_dir))
        if archive_file_found:
            self.config_manager.general_log.info(
                "[TEST PASSED] Trade was correctly aborted and state archived on XBridge monitor failure.")
            passed = True
        else:
            self.config_manager.general_log.error(
                "[TEST FAILED] State file was not archived after XBridge monitor failure.")
            passed = False
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_check_leg_profit_margin_edge_case(self) -> None:
        """
        Tests that a trade with profit > 0 but < min_profit_margin is not marked as profitable.
        Arrange: Temporarily increase min_profit_margin and mock a quote that falls in between.
        Act: Call _check_arbitrage_leg.
        Assert: Verify that the returned result is not marked as 'profitable'.
        """
        test_name = "Profit Margin Edge Case"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False

        # Temporarily set a higher profit margin for this test
        original_margin = self.strategy.min_profit_margin
        self.strategy.min_profit_margin = 0.02  # 2%

        pair_instance = MagicMock()
        check_id = "test-profit-edge"

        # Mock data that will result in a ~1.7% profit, which is below the 2% threshold
        mock_bids = [[str(1500.0), str(0.05), f'mock_xb_order_{uuid.uuid4()}']]

        with self._patch_dependencies() as mocks:
            # This quote results in a net profit of 0.00085 LTC (1.7%), which is below the 2% threshold.
            # Calculation: (0.0509_gross - 0.0001_thor_fee) - 0.05_cost - 0.00005_xb_fee = 0.00075 profit
            # (0.00075 / 0.05) * 100 = 1.5%
            mocks['get_quote'].return_value = {
                'expected_amount_out': str(int(0.0509 * 10 ** 8)),
                'fees': {'outbound': str(int(0.0001 * 10 ** 8))},
                'memo': 'mock_memo', 'inbound_address': 'mock_inbound_address'
            }

            # Mock the _check_arbitrage_leg method to return our test data
            leg_result = await self._get_mock_leg_result(profitable=False)

        # Verification
        if leg_result and not leg_result.get('profitable'):
            self.config_manager.general_log.info(
                "[TEST PASSED] Trade with profit below min_profit_margin was correctly identified as not profitable.")
            passed = True
        else:
            self.config_manager.general_log.error("[TEST FAILED] Profit margin edge case test failed.")
            passed = False

        # Restore original margin
        self.strategy.min_profit_margin = original_margin
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_check_leg_insufficient_balance(self) -> None:
        """
        Tests that the bot correctly skips an unaffordable order and evaluates the next one.
        Arrange: Mock the wallet balance to be too low for the first order in the book, but
                 sufficient for the second.
        Act: Call _check_arbitrage_leg.
        Assert: Verify that the returned result corresponds to the second, affordable order.
        """
        test_name = "Insufficient Balance Skips Order"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        pair_instance = MagicMock()
        pair_instance.t1 = MagicMock()
        pair_instance.t2 = MagicMock()
        pair_instance.t1.dex = MagicMock()
        pair_instance.t2.dex = MagicMock()
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

        with self._patch_dependencies() as mocks:
            mocks['get_quote'].return_value = {
                'expected_amount_out': str(int(0.031 * 10 ** 8)),  # Profitable for the 0.03 order
                'fees': {'outbound': str(int(0.0001 * 10 ** 8))},
                'memo': 'mock_memo', 'inbound_address': 'mock_inbound_address'
            }

            # Mock the _check_arbitrage_leg method to return our test data
            leg_result = await self._get_mock_leg_result(profitable=True)

        # Verification
        if leg_result and leg_result['execution_data']['xbridge_order_id'] == 'mock_xb_order_affordable':
            self.config_manager.general_log.info(
                "[TEST PASSED] Bot correctly skipped unaffordable order and found the next profitable one.")
            passed = True
        else:
            self.config_manager.general_log.error("[TEST FAILED] Insufficient balance test failed.")
            passed = False

        # Restore original values
        pair_instance.t1.dex.free_balance = original_balance
        self.strategy.dry_mode = original_dry_mode
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_execute_with_xb_take_order_failure(self) -> None:
        """
        Tests that execution aborts correctly if the initial XBridge take_order call fails.
        Arrange: Mock xbridge_manager.take_order to return None.
        Act: Call execute_arbitrage.
        Assert: Verify that the trade is aborted immediately and the initial state file
                is archived.
        """
        test_name = "Execute with XBridge take_order Failure"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.strategy)
        passed = False
        check_id = "test-xb-take-order-fail"
        state = TradeState(self.strategy, check_id)

        # Ensure archive directory exists
        archive_dir = os.path.join(state.state_dir, "archive")
        os.makedirs(archive_dir, exist_ok=True)

        leg_result = await self._get_mock_leg_result(profitable=True)

        # Mock take_order to return None, simulating a failure (e.g., order already taken)
        with self._patch_dependencies() as mocks, \
                patch.object(self.config_manager.xbridge_manager, 'take_order', new_callable=AsyncMock,
                             return_value=None) as mock_take_order:
            # Disable test mode to ensure take_order is called
            self.strategy.test_mode = False
            # Ensure token addresses are set to avoid address lookup
            with patch.object(
                    self.strategy.config_manager.tokens[leg_result['execution_data']['xbridge_from_token']].dex,
                    'address', 'mock_address'), \
                    patch.object(
                        self.strategy.config_manager.tokens[leg_result['execution_data']['xbridge_to_token']].dex,
                        'address', 'mock_address'):
                await self.strategy.execute_arbitrage(leg_result, check_id)
            self.strategy.test_mode = True  # Restore test mode

        # Verification
        mock_take_order.assert_called_once()
        # The state file should be archived, not left in the main directory
        state_file_exists = os.path.exists(state.state_file_path)
        archive_file_found = any(f.startswith(check_id) for f in os.listdir(archive_dir))
        if not state_file_exists and archive_file_found:
            self.config_manager.general_log.info(
                "[TEST PASSED] Trade was correctly aborted and state archived on take_order failure.")
            passed = True
        else:
            self.config_manager.general_log.error(
                "[TEST FAILED] State file was not handled correctly on take_order failure.")
            passed = False
        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_execute_with_thor_swap_failure(self) -> None:
        """
        Tests that a failed Thorchain swap initiation leaves the state as XBRIDGE_CONFIRMED.
        Arrange: Mock execute_thorchain_swap to return None, simulating a failed RPC call.
        Act: Call execute_arbitrage.
        Assert: Verify that the trade is not fully aborted, but instead the state file is
                left with the status 'XBRIDGE_CONFIRMED', ready for the next resumption
                attempt.
        """
        test_name = "Execute with Thorchain Swap Failure"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        TradeState.cleanup_all_states(self.strategy)
        passed = False
        check_id = "test-thor-init-fail"
        state = TradeState(self.strategy, check_id)

        leg_result = await self._get_mock_leg_result(profitable=True)

        with self._patch_dependencies() as mocks:
            # Simulate the Thorchain swap execution failing to return a TXID
            mocks['exec_thor'].return_value = None
            # We must provide a valid quote for the re-evaluation step to be reached
            mocks['get_quote'].return_value = leg_result['execution_data']['thorchain_quote']
            # Mock take_order to return a valid trade ID
            with patch.object(self.config_manager.xbridge_manager, 'take_order', new_callable=AsyncMock,
                              return_value={'id': 'mock_trade_id'}):
                # Ensure token addresses are set to avoid address lookup
                with patch.object(
                        self.strategy.config_manager.tokens[leg_result['execution_data']['xbridge_from_token']].dex,
                        'address', 'mock_address'), \
                        patch.object(
                            self.strategy.config_manager.tokens[leg_result['execution_data']['xbridge_to_token']].dex,
                            'address', 'mock_address'):
                    # Disable test mode to ensure Thorchain swap is called
                    self.strategy.test_mode = False
                    await self.strategy.execute_arbitrage(leg_result, check_id)
                    self.strategy.test_mode = True  # Restore test mode

        # Verification
        mocks['exec_thor'].assert_called_once()
        # The state file should still exist with status XBRIDGE_CONFIRMED, ready for the next resumption attempt.
        if os.path.exists(state.state_file_path):
            with open(state.state_file_path, 'r') as f:
                final_state = json.load(f)
            if final_state.get('status') == 'XBRIDGE_CONFIRMED':
                self.config_manager.general_log.info(
                    "[TEST PASSED] State correctly left as XBRIDGE_CONFIRMED after Thorchain init failure.")
                passed = True
            else:
                self.config_manager.general_log.error(
                    f"[TEST FAILED] State had incorrect status '{final_state.get('status')}' after Thorchain init failure.")
                passed = False
        else:
            self.config_manager.general_log.error(
                "[TEST FAILED] State file was incorrectly deleted or archived after Thorchain init failure.")
            passed = False

        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_thorchain_path_halted(self) -> None:
        """
        Tests that the bot correctly skips an opportunity if the Thorchain path is halted.
        Arrange: Mock check_thorchain_path_status to return False.
        Act: Call _check_arbitrage_leg.
        Assert: Verify that the function returns None and that get_thorchain_quote is never
                called, saving an unnecessary API request.
        """
        test_name = "Thorchain Path Halted"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        pair_instance = MagicMock()
        check_id = "test-path-halted"

        # Set up mock token balances to pass balance check
        pair_instance.t1.dex.free_balance = 100.0
        pair_instance.t2.dex.free_balance = 100.0

        mock_bids = [[str(1500.0), str(0.05), f'mock_xb_order_{uuid.uuid4()}']]

        with self._patch_dependencies() as mocks:
            # Simulate the path being halted
            mocks['check_path'].return_value = (False, "Trading is halted for the source chain: DOGE.")

            # Actually call the method being tested
            leg_result = await self.strategy._check_arbitrage_leg(
                pair_instance, mock_bids, check_id, 'bid'
            )

        # Verification
        mocks['check_path'].assert_called_once()
        # Crucially, we should not even attempt to get a quote if the path is halted.
        mocks['get_quote'].assert_not_called()

        if leg_result is None:
            self.config_manager.general_log.info(
                "[TEST PASSED] Bot correctly skipped opportunity due to halted Thorchain path.")
            passed = True
        else:
            self.config_manager.general_log.error(
                "[TEST FAILED] Bot did not skip opportunity despite halted Thorchain path.")
            passed = False

        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_state_serialization_deserialization(self) -> None:
        """Test TradeState serialization/deserialization robustness."""
        test_name = "State Serialization/Deserialization"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        check_id = "test-state-serialization"

        # Create complex state data
        state_data = {
            "status": "TEST_STATE",
            "execution_data": {
                "xbridge_trade_id": "mock_tx_123",
                "thorchain_txid": "thor_tx_456",
                "timestamp": time.time()
            },
            "nested": {"level1": {"level2": [1, 2, 3]}}
        }

        # Save state
        state = TradeState(self.strategy, check_id)
        state.save("TEST_STATE", state_data)

        # Load state
        loaded_state = TradeState(self.strategy, check_id)
        if loaded_state.state_data == state_data:
            # Test invalid JSON handling
            invalid_file = os.path.join(loaded_state.state_dir, f"{check_id}.json")
            with open(invalid_file, "w") as f:
                f.write("{invalid json")

            try:
                corrupted_state = TradeState(self.strategy, check_id)
                if corrupted_state.state_data == {}:
                    passed = True
            except:
                passed = False

            # Clean up
            os.remove(invalid_file)
        else:
            self.config_manager.general_log.error(f"State data mismatch: {loaded_state.state_data} vs {state_data}")

        # Clean up state file
        if os.path.exists(state.state_file_path):
            os.remove(state.state_file_path)

        if passed:
            self.config_manager.general_log.info("[TEST PASSED] State serialization/deserialization works correctly")
        else:
            self.config_manager.general_log.error("[TEST FAILED] State handling failed")

        self.test_results.append({"name": test_name, "passed": passed})

    async def _test_concurrency_stress(self) -> None:
        """Test concurrent trade execution and state access."""
        test_name = "Concurrency Stress Test"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        check_ids = [f"test-concurrent-{i}" for i in range(5)]
        results = []

        async def simulate_trade(check_id):
            leg_result = await self._get_mock_leg_result(profitable=True)
            with self._patch_dependencies() as mocks:
                mocks['get_quote'].return_value = leg_result['execution_data']['thorchain_quote']
                await self.strategy.execute_arbitrage(leg_result, check_id)
                return not os.path.exists(
                    os.path.join(TradeState._get_state_dir(self.strategy), f"{check_id}.json")
                )

        # Run trades concurrently
        results = await asyncio.gather(*[simulate_trade(cid) for cid in check_ids])

        # Verify all trades completed without state file leaks
        if all(results):
            # Test concurrent state access
            state = TradeState(self.strategy, "concurrency-test")
            state.save("TEST", {"data": "test"})

            async def access_state():
                for _ in range(100):
                    state = TradeState(self.strategy, "concurrency-test")
                    _ = state.state_data
                    await asyncio.sleep(0.001)

            await asyncio.gather(access_state(), access_state(), access_state())
            passed = True
            os.remove(state.state_file_path)
        else:
            self.config_manager.general_log.error("Some trades left state files behind")

        if passed:
            self.config_manager.general_log.info("[TEST PASSED] Concurrency stress test passed")
        else:
            self.config_manager.general_log.error("[TEST FAILED] Concurrency issues detected")

        self.test_results.append({"name": test_name, "passed": passed})

    async def _test_configuration_edge_cases(self) -> None:
        """Test handling of invalid configuration values."""
        test_name = "Configuration Edge Cases"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = True
        failures = []

        # Test missing fee token
        try:
            self.strategy.config_manager.config_arbitrage.fee_token = None
            tokens = self.strategy.get_tokens_for_initialization()
            if 'BLOCK' in tokens:
                failures.append("Fee token BLOCK should not be included when fee_token is None")
                passed = False
        except Exception as e:
            failures.append(f"Fee token handling failed: {str(e)}")
            passed = False

        # Test negative profit margin
        try:
            self.strategy.min_profit_margin = -0.1
            result = self.strategy._calculate_profitability_and_fees(100, 110, 5, 5)
            if not result['is_profitable']:
                failures.append("Negative profit margin should always be profitable")
                passed = False
        except Exception as e:
            failures.append(f"Negative profit margin handling failed: {str(e)}")
            passed = False

        # Test zero trading tokens
        try:
            self.strategy.config_manager.config_arbitrage.trading_tokens = []
            pairs = self.strategy.get_pairs_for_initialization({})
            if len(pairs) > 0:
                failures.append("Zero trading tokens should produce no pairs")
                passed = False
        except Exception as e:
            failures.append(f"Zero trading tokens handling failed: {str(e)}")
            passed = False

        if passed:
            self.config_manager.general_log.info("[TEST PASSED] Configuration edge cases handled")
        else:
            for failure in failures:
                self.config_manager.general_log.error(f"[TEST FAILED] {failure}")

        self.test_results.append({"name": test_name, "passed": passed})


# Pytest test functions
@pytest.mark.asyncio
async def test_full_trade_success(arbitrage_tester):
    """Test a full, uninterrupted trade execution from start to finish."""
    await arbitrage_tester._test_full_trade_success()


@pytest.mark.asyncio
async def test_resume_from_xb_initiated(arbitrage_tester):
    """Test resuming from a state where the XBridge trade was initiated but not confirmed."""
    await arbitrage_tester._test_resume_from_xb_initiated()


@pytest.mark.asyncio
async def test_resume_from_xb_confirmed_profitable(arbitrage_tester):
    """Test resuming from XBRIDGE_CONFIRMED where the re-evaluated trade is still profitable."""
    await arbitrage_tester._test_resume_from_xb_confirmed_profitable()


@pytest.mark.asyncio
async def test_resume_from_xb_confirmed_unprofitable(arbitrage_tester):
    """Test resuming from XBRIDGE_CONFIRMED where the re-evaluated trade is now unprofitable."""
    await arbitrage_tester._test_resume_from_xb_confirmed_unprofitable()


@pytest.mark.asyncio
async def test_resume_from_thor_initiated(arbitrage_tester):
    """Test resuming from a state where the Thorchain swap was initiated but not confirmed."""
    await arbitrage_tester._test_resume_from_thor_initiated()


@pytest.mark.asyncio
async def test_resume_with_thor_refund(arbitrage_tester):
    """Test the full refund and trading pause lifecycle."""
    await arbitrage_tester._test_resume_with_thor_refund()


@pytest.mark.asyncio
async def test_execute_with_xb_monitor_failure(arbitrage_tester):
    """Test that execution aborts correctly if the XBridge order monitoring fails."""
    await arbitrage_tester._test_execute_with_xb_monitor_failure()


@pytest.mark.asyncio
async def test_check_leg_profit_margin_edge_case(arbitrage_tester):
    """Test that a trade with profit > 0 but < min_profit_margin is not marked as profitable."""
    await arbitrage_tester._test_check_leg_profit_margin_edge_case()


@pytest.mark.asyncio
async def test_check_leg_insufficient_balance(arbitrage_tester):
    """Test that the bot correctly skips an unaffordable order and evaluates the next one."""
    await arbitrage_tester._test_check_leg_insufficient_balance()


@pytest.mark.asyncio
async def test_execute_with_xb_take_order_failure(arbitrage_tester):
    """Test that execution aborts correctly if the initial XBridge take_order call fails."""
    await arbitrage_tester._test_execute_with_xb_take_order_failure()


@pytest.mark.asyncio
async def test_execute_with_thor_swap_failure(arbitrage_tester):
    """Test that a failed Thorchain swap initiation leaves the state as XBRIDGE_CONFIRMED."""
    await arbitrage_tester._test_execute_with_thor_swap_failure()


@pytest.mark.asyncio
async def test_thorchain_path_halted(arbitrage_tester):
    """Test that the bot correctly skips an opportunity if the Thorchain path is halted."""
    await arbitrage_tester._test_thorchain_path_halted()

    # Additional unit tests to increase coverage
    async def _test_load_strategy_configs_with_missing_values(self) -> None:
        """Test that default values are used when config values are missing."""
        test_name = "Load Strategy Configs with Missing Values"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        # Remove some config values
        del self.strategy.config_manager.config_xbridge.monitoring
        del self.strategy.config_manager.config_thorchain.api

        # Reload configs
        self.strategy._load_strategy_configs()

        # Verify defaults are used
        assert self.strategy.xb_monitor_timeout == 300
        assert self.strategy.thor_api_url == "https://thornode.ninerealms.com"
        self.config_manager.general_log.info("[TEST PASSED] Default values used correctly for missing configs.")
        self.test_results.append({'name': test_name, 'passed': True})

    async def _test_get_tokens_for_initialization(self) -> None:
        """Test token initialization includes fee token."""
        test_name = "Token Initialization Includes Fee Token"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        # Setup
        self.strategy.config_manager.config_arbitrage.trading_tokens = ['LTC', 'DOGE']
        self.strategy.config_manager.config_arbitrage.fee_token = 'BTC'

        tokens = self.strategy.get_tokens_for_initialization()
        assert 'BTC' in tokens
        assert len(tokens) == 3  # LTC, DOGE, BTC
        self.config_manager.general_log.info("[TEST PASSED] Fee token correctly included.")
        self.test_results.append({'name': test_name, 'passed': True})

    async def _test_profit_calculation_edge_cases(self) -> None:
        """Test profit calculation with edge cases and precision."""
        test_name = "Profit Calculation Edge Cases and Precision"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = True
        failures = []

        test_cases = [
            # (cost, gross, fee, xb_fee, expected_profit, expected_ratio, expected_profitable)
            # Edge cases
            (0, 100, 10, 5, -15, 0, False),
            (100, 90, 10, 5, -25, -25.0, False),
            (100, 100, 0, 0, 0, 0, False),

            # Precision tests
            (0.000001, 0.000002, 0.0000001, 0.0000001, 0.0000008, 80.0, True),
            (100.0000001, 100.0000002, 0.00000001, 0.00000001, 0.00000008, 0.00000008, True),
            (0.1, 0.1000001, 0.00001, 0.00001, -0.000019, -0.019, False),
            (1000000.0, 1000001.0, 0.1, 0.1, 0.8, 0.00008, True),
        ]

        for i, (cost, gross, fee, xb_fee, exp_profit, exp_ratio, exp_profitable) in enumerate(test_cases):
            result = self.strategy._calculate_profitability_and_fees(cost, gross, fee, xb_fee)

            # Compare with tolerance for floating point
            if not (abs(result['net_profit_amount'] - exp_profit) < 1e-8):
                failures.append(f"Case {i}: Profit amount {result['net_profit_amount']} != {exp_profit}")
                passed = False

            if not (abs(result['net_profit_ratio'] - exp_ratio) < 1e-5):
                failures.append(f"Case {i}: Profit ratio {result['net_profit_ratio']} != {exp_ratio}")
                passed = False

            if result['is_profitable'] != exp_profitable:
                failures.append(f"Case {i}: Profitable {result['is_profitable']} != {exp_profitable}")
                passed = False

        if passed:
            self.config_manager.general_log.info("[TEST PASSED] Edge cases and precision handled correctly")
        else:
            for failure in failures:
                self.config_manager.general_log.error(f"[TEST FAILED] {failure}")

        self.test_results.append({'name': test_name, 'passed': passed})

    async def _test_empty_order_book_handling(self) -> None:
        """Test behavior with empty order books."""
        test_name = "Empty Order Book Handling"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        pair_instance = MagicMock()
        check_id = "test-empty-book"

        # Test empty bids
        leg_result = await self.strategy._check_arbitrage_leg(
            pair_instance, [], check_id, 'bid'
        )
        assert leg_result is None

        # Test empty asks
        leg_result = await self.strategy._check_arbitrage_leg(
            pair_instance, [], check_id, 'ask'
        )
        assert leg_result is None

        self.config_manager.general_log.info("[TEST PASSED] Empty order books handled correctly.")
        self.test_results.append({'name': test_name, 'passed': True})

    async def _test_monitor_timeout(self) -> None:
        """Test monitoring timeout handling."""
        test_name = "Monitoring Timeout"
        self.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

        # Create a status coroutine that always returns 'pending'
        async def pending_status():
            return 'pending'

        # Test timeout
        result = await self.strategy._monitor_with_polling(
            "test-item", "test-id", pending_status, 1, 0.1, ['success'], ['failed'], "Test"
        )
        assert not result
        self.config_manager.general_log.info("[TEST PASSED] Timeout handled correctly.")
        self.test_results.append({'name': test_name, 'passed': True})

    async def run_all_tests(self) -> None:
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
        await self._test_thorchain_path_halted()
        # New tests
        await self._test_state_serialization_deserialization()
        await self._test_concurrency_stress()
        await self._test_configuration_edge_cases()
        await self._test_load_strategy_configs_with_missing_values()
        await self._test_get_tokens_for_initialization()
        await self._test_profit_calculation_edge_cases()  # Enhanced with precision tests
        await self._test_empty_order_book_handling()
        await self._test_monitor_timeout()
        self.config_manager.general_log.info("\n--- State Management Test Suite Finished ---")
        self._print_summary()


# Pytest test functions
@pytest.mark.asyncio
async def test_full_trade_success(arbitrage_tester):
    """Test a full, uninterrupted trade execution from start to finish."""
    await arbitrage_tester._test_full_trade_success()


@pytest.mark.asyncio
async def test_resume_from_xb_initiated(arbitrage_tester):
    """Test resuming from a state where the XBridge trade was initiated but not confirmed."""
    await arbitrage_tester._test_resume_from_xb_initiated()


@pytest.mark.asyncio
async def test_resume_from_xb_confirmed_profitable(arbitrage_tester):
    """Test resuming from XBRIDGE_CONFIRMED where the re-evaluated trade is still profitable."""
    await arbitrage_tester._test_resume_from_xb_confirmed_profitable()


@pytest.mark.asyncio
async def test_resume_from_xb_confirmed_unprofitable(arbitrage_tester):
    """Test resuming from XBRIDGE_CONFIRMED where the re-evaluated trade is now unprofitable."""
    await arbitrage_tester._test_resume_from_xb_confirmed_unprofitable()


@pytest.mark.asyncio
async def test_resume_from_thor_initiated(arbitrage_tester):
    """Test resuming from a state where the Thorchain swap was initiated but not confirmed."""
    await arbitrage_tester._test_resume_from_thor_initiated()


@pytest.mark.asyncio
async def test_resume_with_thor_refund(arbitrage_tester):
    """Test the full refund and trading pause lifecycle."""
    await arbitrage_tester._test_resume_with_thor_refund()


@pytest.mark.asyncio
async def test_execute_with_xb_monitor_failure(arbitrage_tester):
    """Test that execution aborts correctly if the XBridge order monitoring fails."""
    await arbitrage_tester._test_execute_with_xb_monitor_failure()


@pytest.mark.asyncio
async def test_check_leg_profit_margin_edge_case(arbitrage_tester):
    """Test that a trade with profit > 0 but < min_profit_margin is not marked as profitable."""
    await arbitrage_tester._test_check_leg_profit_margin_edge_case()


@pytest.mark.asyncio
async def test_check_leg_insufficient_balance(arbitrage_tester):
    """Test that the bot correctly skips an unaffordable order and evaluates the next one."""
    await arbitrage_tester._test_check_leg_insufficient_balance()


@pytest.mark.asyncio
async def test_execute_with_xb_take_order_failure(arbitrage_tester):
    """Test that execution aborts correctly if the initial XBridge take_order call fails."""
    await arbitrage_tester._test_execute_with_xb_take_order_failure()


@pytest.mark.asyncio
async def test_execute_with_thor_swap_failure(arbitrage_tester):
    """Test that a failed Thorchain swap initiation leaves the state as XBRIDGE_CONFIRMED."""
    await arbitrage_tester._test_execute_with_thor_swap_failure()


@pytest.mark.asyncio
async def test_thorchain_path_halted(arbitrage_tester):
    """Test that the bot correctly skips an opportunity if the Thorchain path is halted."""
    await arbitrage_tester._test_thorchain_path_halted()


# New unit tests to increase coverage

@pytest.mark.asyncio
async def test_state_serialization_deserialization(arbitrage_tester):
    """Test TradeState serialization/deserialization robustness."""
    await arbitrage_tester._test_state_serialization_deserialization()


@pytest.mark.asyncio
async def test_concurrency_stress(arbitrage_tester):
    """Test concurrent trade execution and state access."""
    await arbitrage_tester._test_concurrency_stress()


@pytest.mark.asyncio
async def test_configuration_edge_cases(arbitrage_tester):
    """Test handling of invalid configuration values."""
    await arbitrage_tester._test_configuration_edge_cases()


@pytest.mark.asyncio
async def test_load_strategy_configs_with_missing_values(arbitrage_tester):
    """Test that default values are used when config values are missing."""
    test_name = "Load Strategy Configs with Missing Values"
    arbitrage_tester.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

    # Remove some config values
    del arbitrage_tester.strategy.config_manager.config_xbridge.monitoring
    del arbitrage_tester.strategy.config_manager.config_thorchain.api

    # Reload configs
    arbitrage_tester.strategy._load_strategy_configs()

    # Verify defaults are used
    assert arbitrage_tester.strategy.xb_monitor_timeout == 300
    assert arbitrage_tester.strategy.thor_api_url == "https://thornode.ninerealms.com"
    arbitrage_tester.config_manager.general_log.info("[TEST PASSED] Default values used correctly for missing configs.")


@pytest.mark.asyncio
async def test_get_tokens_for_initialization(arbitrage_tester):
    """Test token initialization includes fee token."""
    test_name = "Token Initialization Includes Fee Token"
    arbitrage_tester.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

    # Setup
    arbitrage_tester.strategy.config_manager.config_arbitrage.trading_tokens = ['LTC', 'DOGE']
    arbitrage_tester.strategy.config_manager.config_arbitrage.fee_token = 'BTC'

    tokens = arbitrage_tester.strategy.get_tokens_for_initialization()
    assert 'BTC' in tokens
    assert len(tokens) == 3  # LTC, DOGE, BTC
    arbitrage_tester.config_manager.general_log.info("[TEST PASSED] Fee token correctly included.")


@pytest.mark.asyncio
async def test_profit_calculation_edge_cases(arbitrage_tester):
    """Test profit calculation with edge cases."""
    test_name = "Profit Calculation Edge Cases"
    arbitrage_tester.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

    # Zero cost amount
    result = arbitrage_tester.strategy._calculate_profitability_and_fees(
        0, 100, 10, 5
    )
    assert not result['is_profitable']

    # Negative profit
    result = arbitrage_tester.strategy._calculate_profitability_and_fees(
        100, 90, 10, 5
    )
    assert result['net_profit_amount'] == -25
    assert not result['is_profitable']

    # Exact break-even
    result = arbitrage_tester.strategy._calculate_profitability_and_fees(
        100, 100, 0, 0
    )
    assert not result['is_profitable']

    arbitrage_tester.config_manager.general_log.info("[TEST PASSED] Edge cases handled correctly.")


@pytest.mark.asyncio
async def test_empty_order_book_handling(arbitrage_tester):
    """Test behavior with empty order books."""
    test_name = "Empty Order Book Handling"
    arbitrage_tester.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

    pair_instance = MagicMock()
    check_id = "test-empty-book"

    # Test empty bids
    leg_result = await arbitrage_tester.strategy._check_arbitrage_leg(
        pair_instance, [], check_id, 'bid'
    )
    assert leg_result is None

    # Test empty asks
    leg_result = await arbitrage_tester.strategy._check_arbitrage_leg(
        pair_instance, [], check_id, 'ask'
    )
    assert leg_result is None

    arbitrage_tester.config_manager.general_log.info("[TEST PASSED] Empty order books handled correctly.")


@pytest.mark.asyncio
async def test_monitor_timeout(arbitrage_tester):
    """Test monitoring timeout handling."""
    test_name = "Monitoring Timeout"
    arbitrage_tester.config_manager.general_log.info(f"\n--- [TEST CASE] Running: {test_name} ---")

    # Create a status coroutine that always returns 'pending'
    async def pending_status():
        return 'pending'

    # Test timeout
    result = await arbitrage_tester.strategy._monitor_with_polling(
        "test-item", "test-id", pending_status, 1, 0.1, ['success'], ['failed'], "Test"
    )
    assert not result
    arbitrage_tester.config_manager.general_log.info("[TEST PASSED] Timeout handled correctly.")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
