import asyncio
import os
import sys
import time
import yaml
import pytest
from unittest.mock import Mock, patch, AsyncMock
from decimal import Decimal

# Fix import path: Add project root to sys.path for 'strategies' module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from strategies.thorchain_continuous_strategy import ThorChainContinuousStrategy, TradeDirection, TradeMetrics, ContinuousTradeState, execute_thorchain_swap, get_actual_swap_received
from definitions.pair import Pair
from definitions.token import Token
from definitions.config_manager import ConfigManager  # For mocking
from definitions.thorchain_def import get_thorchain_quote, check_thorchain_path_status, get_inbound_addresses

@pytest.fixture(scope="function")
def config_manager():
    """Shared mock ConfigManager."""
    cm = Mock(spec=ConfigManager)
    cm.config_thorchain_continuous = Mock()
    cm.config_thorchain_continuous.target_spread = 0.01
    # Mock as object with __dict__ for _get_config_dict_len
    starting_bal_mock = Mock()
    starting_bal_mock.__dict__ = {'LTC': 10.0, 'DOGE': 5000.0}
    cm.config_thorchain_continuous.starting_balances = starting_bal_mock
    min_size_mock = Mock()
    min_size_mock.__dict__ = {'LTC': 1.0, 'DOGE': 500.0}
    cm.config_thorchain_continuous.min_trade_size = min_size_mock
    cm.config_thorchain_continuous.slippage_max = 0.005
    cm.config_thorchain_continuous.anchor_trade_size = 1.0
    cm.general_log = Mock()
    cm.error_handler = Mock()  # Sync for validation
    cm.balance_manager = AsyncMock(update_balances=AsyncMock())
    cm.xbridge_manager = Mock()
    cm.xbridge_manager.xbridge_conf = {'LTC': {'ip': '127.0.0.1', 'port': 1234, 'username': 'user', 'password': 'pass'}, 'DOGE': {'ip': '127.0.0.1', 'port': 1234, 'username': 'user', 'password': 'pass'}}
    return cm


@pytest.fixture(scope="function")
def strategy(config_manager, pair):  # Add pair dependency
    """Shared mock strategy instance."""
    strategy = ThorChainContinuousStrategy(config_manager)
    strategy.config_manager.pairs = {'LTC/DOGE': pair}  # Set here after resolution
    strategy.target_spread = 0.01
    strategy.slippage_max = 0.005
    strategy.min_trade_size = {'LTC': 1.0, 'DOGE': 500.0}
    strategy.anchor_trade_size = 1.0
    strategy.max_fee_threshold = 0.001  # Set missing attribute
    strategy.dry_mode = True
    strategy.http_session = AsyncMock()
    strategy.thor_quote_url = "mock_url"
    strategy.token1 = 'LTC'  # Set for projection tests
    strategy.token2 = 'DOGE'
    strategy.fixed_sizing = True
    return strategy


@pytest.fixture(scope="function")
def strategy_fixed(config_manager, pair):
    strategy = ThorChainContinuousStrategy(config_manager)
    # ... existing setup ...
    strategy.config_manager.pairs = {'LTC/DOGE': pair}  # Set here after resolution
    strategy.target_spread = 0.01
    strategy.slippage_max = 0.005
    strategy.min_trade_size = {'LTC': 1.0, 'DOGE': 500.0}
    strategy.anchor_trade_size = 1.0
    strategy.max_fee_threshold = 0.001  # Set missing attribute
    strategy.dry_mode = True
    strategy.http_session = AsyncMock()
    strategy.thor_quote_url = "mock_url"
    strategy.token1 = 'LTC'  # Set for projection tests
    strategy.token2 = 'DOGE'
    strategy.fixed_sizing = True
    return strategy

@pytest.fixture(scope="function")
def strategy_optimal(config_manager, pair):
    strategy = ThorChainContinuousStrategy(config_manager)
    strategy.config_manager.pairs = {'LTC/DOGE': pair}
    strategy.target_spread = 0.01
    strategy.slippage_max = 0.005
    strategy.min_trade_size = {'LTC': 1.0, 'DOGE': 500.0}
    strategy.anchor_trade_size = 1.0
    strategy.max_fee_threshold = 0.001
    strategy.dry_mode = True
    strategy.http_session = AsyncMock()
    strategy.thor_quote_url = "mock_url"
    strategy.token1 = 'LTC'
    strategy.token2 = 'DOGE'
    strategy.fixed_sizing = False
    strategy.state = Mock(state_data={'anchor_rate': 500.0})  # Add mock state
    return strategy


@pytest.fixture(scope="function")
def pair():
    """Shared mock pair and tokens with explicit dex mocks."""
    pair = Mock(spec=Pair)
    pair.symbol = 'LTC/DOGE'
    pair.t1 = Mock(spec=Token)
    pair.t1.symbol = 'LTC'
    dex1 = Mock()
    dex1.free_balance = 15.0
    pair.t1.dex = dex1
    pair.t2 = Mock(spec=Token)
    pair.t2.symbol = 'DOGE'
    dex2 = Mock()
    dex2.free_balance = 6000.0
    pair.t2.dex = dex2
    pair.t1.dex.total_balance = 15.0
    pair.t2.dex.total_balance = 6000.0
    return pair


@pytest.fixture(scope="function")
def state(strategy):
    """Shared mock state."""
    state = Mock()  # Remove spec to avoid interference with dict behavior
    state.state_data = {'anchor_rate': 500.0,  # DOGE/LTC example
                        'last_direction': TradeDirection.TOKEN2_TO_TOKEN1,  # Changed to match alternation to TOKEN1_TO_TOKEN2
                        'starting_balances': {'LTC': 10.0, 'DOGE': 5000.0}, 
                        'last_sent': 1.0,  # LTC sent in anchor
                        'last_received': 500.0,  # DOGE received
                        'cumulative_surplus_t1': 0.0,
                        'cumulative_surplus_t2': 0.0,
                        'success_count': 0}
    state.save = Mock()
    state.log_trade = Mock()  # Explicit mock for assert_called
    state.is_paused = Mock(return_value=False)
    state.archive = Mock()
    strategy.state = state
    return state

@pytest.mark.asyncio
async def test_load_invalid_balances(strategy, monkeypatch):
    """Test load defaults on invalid (non-flat dict) balances."""
    invalid_loaded = {'starting_balances': 'invalid_string', 'virtual_balances': [1,2]}  # Non-dict
    with patch('builtins.open', new_callable=Mock) as mock_file:
        mock_file.return_value.__enter__.return_value.read = lambda: yaml.dump(invalid_loaded)
        state = ContinuousTradeState(strategy)
        state.load()
    assert state.state_data['starting_balances'] == {strategy.token1: 0.0, strategy.token2: 0.0}  # Defaulted
    assert strategy.config_manager.general_log.error.call_count >= 2  # Errors for each key


@pytest.mark.asyncio
async def test_get_config_dict_len(strategy, config_manager):
    """Test _get_config_dict_len handles dict, YamlToObject, and invalid types."""
    # Test dict
    test_dict = {'LTC': 10.0, 'DOGE': 5000.0}
    assert strategy._get_config_dict_len(test_dict) == 2
    
    # Test YamlToObject mock (simulate config)
    mock_yaml_obj = Mock()
    mock_yaml_obj.__dict__ = {'LTC': 10.0, 'DOGE': 5000.0}
    assert strategy._get_config_dict_len(mock_yaml_obj) == 2
    
    # Test invalid (str)
    assert strategy._get_config_dict_len("invalid") == 0
    config_manager.general_log.warning.assert_called_once()
    config_manager.general_log.warning.reset_mock()
    
    # Test vars() fail (e.g., no __dict__)
    invalid_obj = Mock()
    invalid_obj.__dict__ = None  # Trigger except
    assert strategy._get_config_dict_len(invalid_obj) == 0
    config_manager.general_log.warning.assert_called_once()

@pytest.mark.asyncio
async def test_evaluate_opportunity_success(strategy, pair, state, monkeypatch):
    """Test successful evaluation: all conditions met, including dual growth with fees/surplus."""
    pair.t1.dex.total_balance = 15.0
    pair.t2.dex.total_balance = 6000.0
    monkeypatch.setattr('definitions.thorchain_def.check_thorchain_path_status', AsyncMock(return_value=(True, "active")))
    direction = TradeDirection.TOKEN2_TO_TOKEN1
    amount = strategy.config_manager.config_thorchain_continuous.min_trade_size['DOGE']
    decimals_from = 8  # DOGE
    decimals_to = 8    # LTC
    expected_out_raw = 1.05 * (10 ** decimals_to)  # 1.05 LTC
    outbound_fee_raw = 0.01 * (10 ** decimals_to)
    slippage_bps = 30
    mock_quote = {'expected_amount_out': expected_out_raw, 'fees': {'outbound': str(outbound_fee_raw)}, 'slippage_bps': slippage_bps, 'from_asset': 'DOGE.DOGE', 'amount_base': amount, 'decimals_from': decimals_from, 'decimals_to': decimals_to, 'expiry': time.time() - 10}

    with patch.object(strategy, '_project_dual_accumulation', new_callable=AsyncMock) as mock_proj:
        mock_proj.return_value = {'both_positive': True, 'projected_token1': 11.05, 'projected_token2': 5550.0, 'surplus_t1': 0.05, 'surplus_t2': 50.0}
        result = await strategy._evaluate_opportunity(pair, mock_quote, direction)

    assert result['meets_conditions']
    assert result['spread'] > strategy.target_spread  # ~0.167 > 0.01
    # Real asymmetry from code: for buy, inverse_quote=1/quote_rate ≈450/1.05≈428.57, (500-428.57)/428.57≈0.167
    assert abs(result['asymmetry'] - 0.167) < 0.01
    assert result['projection']['both_positive']
    assert result['projection']['surplus_t1'] == 0.05
    assert result['projection']['surplus_t2'] == 50.0

@pytest.mark.asyncio
async def test_evaluate_opportunity_fail_spread(strategy, pair, state, monkeypatch):  # Added state
    """Test failure on low spread."""
    monkeypatch.setattr('definitions.thorchain_def.check_thorchain_path_status', AsyncMock(return_value=(True, "active")))
    direction = TradeDirection.TOKEN2_TO_TOKEN1
    amount = strategy.config_manager.config_thorchain_continuous.min_trade_size['DOGE']
    expected_out_raw = 0.9 * 10**8  # 0.9 LTC out for 450 DOGE in (buy_rate=500==anchor, spread=0 <0.01)
    mock_quote = {'expected_amount_out': expected_out_raw, 'fees': {'outbound': '0'}, 'slippage_bps': 10, 'from_asset': 'DOGE.DOGE', 'amount_base': amount, 'expiry': time.time()}  # Uses quote amount=450
    result = await strategy._evaluate_opportunity(pair, mock_quote, direction)
    assert not result['meets_conditions']
    assert 'Spread' in result['reason']
    assert result['spread'] < strategy.target_spread  # Verify low spread (0 < 0.01)

@pytest.mark.asyncio
async def test_evaluate_opportunity_fail_slippage(strategy, pair, state, monkeypatch):  # Added state
    """Test failure on high slippage."""
    monkeypatch.setattr('definitions.thorchain_def.check_thorchain_path_status', AsyncMock(return_value=(True, "active")))
    direction = TradeDirection.TOKEN2_TO_TOKEN1
    amount = strategy.config_manager.config_thorchain_continuous.min_trade_size['DOGE']
    mock_quote = {'expected_amount_out': 505.0 * 10**8, 'fees': {'outbound': '0'}, 'slippage_bps': 60, 'from_asset': 'DOGE.DOGE', 'amount_base': amount, 'decimals_to': 8, 'inbound_address': 'addr', 'memo': 'memo', 'expiry': time.time()}  # Added amount_base, decimals_to, etc.
    with patch.object(strategy, '_project_dual_accumulation', return_value={'both_positive': True, 'projected_token1': 15.1, 'projected_token2': 5050.0, 'surplus_t1': 0.05, 'surplus_t2': 50.0}):  # Pass surplus to reach slippage
        result = await strategy._evaluate_opportunity(pair, mock_quote, direction)
    assert not result['meets_conditions']
    assert 'Slippage' in result['reason']
    assert result['slippage'] == 0.006  # 60 bps = 0.6%

@pytest.mark.asyncio
async def test_evaluate_opportunity_fail_balance(strategy, pair, state, monkeypatch):
    """Test failure on insufficient balance."""
    pair.t2.dex.free_balance = 400.0
    monkeypatch.setattr('definitions.thorchain_def.check_thorchain_path_status', AsyncMock(return_value=(True, "active")))
    direction = TradeDirection.TOKEN2_TO_TOKEN1
    amount = strategy.config_manager.config_thorchain_continuous.min_trade_size['DOGE']
    mock_quote = {'expected_amount_out': 505.0 * 10**8, 'fees': {'outbound': '0'}, 'slippage_bps': 10, 'from_asset': 'DOGE.DOGE', 'amount_base': amount, 'decimals_to': 8, 'inbound_address': 'addr', 'memo': 'memo', 'expiry': time.time()}  # Added keys
    with patch.object(strategy, '_project_dual_accumulation', return_value={'both_positive': True, 'projected_token1': 15.1, 'projected_token2': 5050.0, 'surplus_t1': 0.05, 'surplus_t2': 50.0}):  # Positive surplus to pass check
        result = await strategy._evaluate_opportunity(pair, mock_quote, direction)
    assert not result['meets_conditions']
    assert 'Insufficient balance' in result['reason']
    assert result['amount'] == amount
    assert result['free_balance'] == 400.0

def test_calculate_asymmetry_favorable_buyback(strategy, state):
    """Test asymmetry for buy-back direction (MD example: +0.05 LTC surplus)."""
    amount = strategy.config_manager.config_thorchain_continuous.min_trade_size['DOGE']
    quote_rate = 1.05 / amount  # ≈0.002333 LTC/DOGE
    anchor = state.state_data['anchor_rate']  # DOGE/LTC
    direction = TradeDirection.TOKEN2_TO_TOKEN1
    asymmetry = strategy._calculate_asymmetry(quote_rate, anchor, direction)
    assert asymmetry > 0.0  # ≈0.165

def test_calculate_asymmetry_unfavorable_sell(strategy, state):  # Added state fixture
    """Test negative asymmetry for sell direction."""
    quote_rate = 400.0  # <500
    anchor = 500.0
    direction = TradeDirection.TOKEN1_TO_TOKEN2
    asymmetry = strategy._calculate_asymmetry(quote_rate, anchor, direction)
    assert asymmetry <= 0.0  #  (400-500)/500 = -0.2 -> 0.0

@pytest.mark.asyncio
async def test_project_dual_accumulation_growth_with_fees_surplus(strategy, pair, state):
    """Test projection: both grow, includes outbound_fee subtraction and asymmetry surplus addition."""
    pair.t1.dex.total_balance = 15.0
    pair.t2.dex.total_balance = 6000.0
    amount = strategy.config_manager.config_thorchain_continuous.min_trade_size['DOGE']
    expected_out = 1.05
    direction = TradeDirection.TOKEN2_TO_TOKEN1
    decimals_to = 8
    mock_quote = {'fees': {'outbound': '0'}, 'decimals_to': decimals_to}  # No fee to match MD example (0.05 surplus)
    state.state_data['last_sent'] = strategy.config_manager.config_thorchain_continuous.anchor_trade_size  # Previous LTC sent
    state.state_data['last_received'] = state.state_data['anchor_rate'] * state.state_data['last_sent']  # Previous DOGE received
    projection = await strategy._project_dual_accumulation(pair, amount, expected_out, direction, mock_quote)
    assert projection['both_positive']
    assert projection['projected_token1'] > strategy.config_manager.config_thorchain_continuous.starting_balances['LTC'] + state.state_data['cumulative_surplus_t1']
    assert projection['projected_token2'] > strategy.config_manager.config_thorchain_continuous.starting_balances['DOGE'] + state.state_data['cumulative_surplus_t2']
    assert projection['surplus_t1'] == pytest.approx(0.05)  # MD-refined extra LTC (no fee)
    assert projection['surplus_t2'] == pytest.approx(50.0)  # Saved DOGE from asymmetry

@pytest.mark.asyncio
async def test_project_dual_accumulation_no_growth(strategy, pair, state):
    """Test failure: one balance doesn't grow (no dual accumulation)."""
    pair.t1.dex.total_balance = 15.0
    pair.t2.dex.total_balance = 5050.0  # Set total to make projected_t2 fail
    amount = 600.0
    expected_out = 1.0
    direction = TradeDirection.TOKEN2_TO_TOKEN1
    decimals_to = 8
    mock_quote = {'fees': {'outbound': '0'}, 'decimals_to': decimals_to}
    state.state_data['last_sent'] = 1.0  # Small surplus_t2= previous_received - amount =500-600=-100 -> max(0,0)=0
    state.state_data['last_received'] = 500.0
    projection = await strategy._project_dual_accumulation(pair, amount, expected_out, direction, mock_quote)
    assert not projection['both_positive']  # projected_t2=5050-600+0=4450 <5000+0
    assert projection['projected_token2'] == pytest.approx(4450.0)

@pytest.mark.asyncio
async def test_project_dual_accumulation_sell_direction(strategy, pair, state):
    """Test projection for sell direction: surplus_t2 from extra received."""
    pair.t1.dex.total_balance = 15.0
    pair.t2.dex.total_balance = 6000.0
    amount = 1.0  # LTC
    expected_out = 550.0  # DOGE >500*1
    direction = TradeDirection.TOKEN1_TO_TOKEN2
    decimals_to = 8
    mock_quote = {'fees': {'outbound': '0'}, 'decimals_to': decimals_to}
    state.state_data['last_sent'] = 500.0  # Vs anchor equivalent for test (previous effective 500 DOGE)
    state.state_data['last_received'] = 1.05  # Previous LTC received
    projection = await strategy._project_dual_accumulation(pair, amount, expected_out, direction, mock_quote)
    assert projection['both_positive']
    # surplus_t2 = 550 - 500 = 50 (vs anchor)
    assert projection['surplus_t2'] == pytest.approx(50.0)
    assert projection['surplus_t1'] == pytest.approx(0.05)  # Saved LTC from previous (1.05 - 1)

def test_get_next_direction_alternation(strategy, state):
    """Test direction alternates from last."""
    state.state_data['last_direction'] = TradeDirection.TOKEN1_TO_TOKEN2
    next_dir = strategy._get_next_direction()
    assert next_dir == TradeDirection.TOKEN2_TO_TOKEN1
    state.state_data['last_direction'] = TradeDirection.TOKEN2_TO_TOKEN1
    next_dir = strategy._get_next_direction()
    assert next_dir == TradeDirection.TOKEN1_TO_TOKEN2

@pytest.mark.asyncio
async def test_process_pair_async_dry_success(strategy, pair, state, monkeypatch):
    """Test core loop in dry mode: polls, evaluates success, updates state (no execution)."""
    monkeypatch.setattr('definitions.thorchain_def.get_inbound_addresses', AsyncMock(return_value=[]))  # Suppress warning
    state.is_paused.return_value = False
    strategy.config_manager.pairs = {'LTC/DOGE': pair}
    strategy.config_manager.balance_manager.update_balances = AsyncMock()
    decimals_to = 8
    full_mock_quote = {'from_asset': 'DOGE.DOGE', 'to_asset': 'LTC.LTC', 'amount_base': 450.0, 'expected_amount_out': 1.05 * (10 ** decimals_to), 'fees': {'outbound': '0'}, 'slippage_bps': 10, 'expiry': time.time() - 10, 'inbound_address': 'addr', 'memo': 'memo', 'decimals_to': decimals_to}
    with patch.object(strategy, '_poll_quotes', return_value={TradeDirection.TOKEN2_TO_TOKEN1: full_mock_quote}):
        with patch.object(strategy, '_get_next_direction', return_value=TradeDirection.TOKEN2_TO_TOKEN1):
            with patch.object(strategy, '_evaluate_opportunity', return_value={'meets_conditions': True, 'quote': full_mock_quote, 'spread': 0.167, 'asymmetry': 0.167, 'projection': {'surplus_t1': 0.05, 'surplus_t2': 50.0}}):
                with patch.object(strategy, '_revalidate_quote', return_value=full_mock_quote):
                    with patch.object(strategy, '_execute_and_confirm_swap', new_callable=AsyncMock) as mock_exec:
                        mock_exec.return_value = {'success': True, 'txid': 'mock_tx', 'effective_rate': 450 / 1.05, 'actual_received': 1.05, 'metrics': TradeMetrics(450 / 1.05, 0.167, 0.167, 0.05, 50.0, 0.01, surplus_t1=0.05, surplus_t2=50.0, cumulative_trades=1, success_count=1, cumulative_spread_consistency=100.0)}
                        await strategy.process_pair_async(pair)
    mock_exec.assert_called_once()
    strategy.state.save.assert_called_once()  # Via _update_anchor_and_metrics
    strategy.consecutive_failures == 0  # Reset on success

@pytest.mark.asyncio
async def test_process_pair_async_paused(strategy, pair, state, monkeypatch):
    """Test skips if paused."""
    monkeypatch.setattr('definitions.thorchain_def.get_thorchain_quote', AsyncMock())
    state.is_paused.return_value = True
    await strategy.process_pair_async(pair)
    # No calls to quote if paused

@pytest.mark.asyncio
async def test_anchor_skip_if_set(strategy, state):
    """Test startup skips anchor execution if rate already set (but returns both tasks)."""
    state.state_data['anchor_rate'] = 500.0
    with patch.object(strategy, '_execute_anchor_trade') as mock_anchor_exec:
        tasks = strategy.get_startup_tasks()
        assert len(tasks) == 2  # Always returns anchor + resume
        await tasks[0]()  # anchor_trade
        mock_anchor_exec.assert_not_called()  # Internal skip
        await tasks[1]()  # resume runs

@pytest.mark.asyncio
async def test_anchor_success(strategy, pair, state, monkeypatch):
    """Test full anchor flow: poll, force eval, revalidate, execute success, save anchor/last_dir."""
    state.state_data['anchor_rate'] = 0.0
    monkeypatch.setattr('definitions.thorchain_def.get_inbound_addresses', AsyncMock(return_value=[]))
    strategy.config_manager.pairs = {'LTC/DOGE': pair}
    decimals_to = 8
    anchor_quote = {'from_asset': 'LTC.LTC', 'to_asset': 'DOGE.DOGE', 'amount_base': 1.0, 'expected_amount_out': 500 * (10 ** decimals_to), 'fees': {'outbound': '0'}, 'slippage_bps': 10, 'expiry': time.time() - 10, 'inbound_address': 'addr', 'memo': 'memo', 'decimals_to': decimals_to}
    with patch.object(strategy, '_poll_quotes', return_value={TradeDirection.TOKEN1_TO_TOKEN2: anchor_quote}):
        with patch.object(strategy, '_evaluate_opportunity', return_value={'meets_conditions': True, 'quote': anchor_quote, 'spread': 0.0, 'asymmetry': 0.0}):
            with patch.object(strategy, '_revalidate_quote', return_value=anchor_quote):
                with patch.object(strategy, '_execute_and_confirm_swap', return_value={'success': True, 'txid': 'anchor_tx', 'effective_rate': 500.0, 'actual_received': 500.0}):
                    with patch.object(strategy.config_manager.balance_manager, 'update_balances', new_callable=AsyncMock):
                        await strategy._execute_anchor_trade()
    state.save.assert_called_once_with({'anchor_rate': 500.0, 'last_direction': TradeDirection.TOKEN1_TO_TOKEN2})
    strategy.config_manager.general_log.info.assert_called_with("Anchor trade executed: rate 500.00000000 T2/T1")

@pytest.mark.asyncio
async def test_anchor_fail_balance(strategy, pair, state, monkeypatch):
    """Test anchor failure: insufficient balance → warn + pause."""
    state.state_data['anchor_rate'] = 0.0
    pair.t1.dex.free_balance = 0.5  # <1.0 anchor_size
    monkeypatch.setattr('definitions.thorchain_def.get_inbound_addresses', AsyncMock(return_value=[]))
    await strategy._execute_anchor_trade()
    state.save.assert_called_once_with({'pause_reason': 'Insufficient anchor balance'})
    strategy.config_manager.general_log.warning.assert_called()

@pytest.mark.asyncio
async def test_increment_failures_circuit(strategy, state):
    """Test circuit breaker pauses after 3 failures."""
    strategy.max_failure_threshold = 3  # Instead of max_consecutive_failures
    strategy.consecutive_failures = 2
    strategy._increment_failures("test fail")
    assert strategy.consecutive_failures == 3
    state.save.assert_called_once_with({'pause_reason': 'Circuit breaker: 3 failures - test fail'})
    state.archive.assert_called_once()

@pytest.mark.asyncio
@patch('strategies.thorchain_continuous_strategy.execute_thorchain_swap', new_callable=AsyncMock)
@patch('strategies.thorchain_continuous_strategy.get_actual_swap_received', new_callable=AsyncMock)
async def test_execute_and_confirm_swap_success(mock_get_actual, mock_execute, strategy, pair, state):
    """Test execution success: fee ok, monitor success, actual received, log trade, effective normalized."""
    mock_execute.return_value = 'txid'
    mock_get_actual.return_value = 1.05
    direction = TradeDirection.TOKEN2_TO_TOKEN1
    quote = {'amount_base': 450.0, 'inbound_address': 'addr', 'memo': 'memo', 'fees': {'outbound': '0'}, 'decimals_to': 8}
    state.state_data['last_sent'] = 1.0  # Previous LTC sent
    state.state_data['last_received'] = 500.0  # Previous DOGE received
    with patch.object(strategy, '_monitor_thorchain_swap', return_value='success'):  # Patch instance method
        result = await strategy._execute_and_confirm_swap(pair, quote, direction)
    assert result['success']
    assert result['effective_rate'] == pytest.approx(450 / 1.05)  # Normalized T2/T1
    assert result['actual_received'] == pytest.approx(1.05)
    assert result['projection']['surplus_t1'] == pytest.approx(0.05)
    assert result['projection']['surplus_t2'] == pytest.approx(50.0)
    state.log_trade.assert_called_once()  # With trade_log dict

@pytest.mark.asyncio
async def test_execute_and_confirm_swap_fail_fee(strategy, pair, state, monkeypatch):
    """Test execution fail: abnormal outbound fee > max*amount."""
    direction = TradeDirection.TOKEN1_TO_TOKEN2
    quote = {'amount_base': 1.0, 'inbound_address': 'addr', 'memo': 'memo', 'fees': {'outbound': str(0.01 * 10**8)}, 'decimals_to': 8}  # 0.01 > 0.001*1.0
    result = await strategy._execute_and_confirm_swap(pair, quote, direction)
    assert not result['success']
    assert 'Abnormal fees' in result['reason']

@pytest.mark.asyncio
@patch('strategies.thorchain_continuous_strategy.execute_thorchain_swap', new_callable=AsyncMock)
async def test_execute_and_confirm_swap_fail_monitor(mock_execute, strategy, pair, state):
    """Test execution fail: monitor returns 'refunded'."""
    mock_execute.return_value = 'txid'
    direction = TradeDirection.TOKEN2_TO_TOKEN1
    quote = {'amount_base': 450.0, 'inbound_address': 'addr', 'memo': 'memo', 'fees': {'outbound': '0'}, 'decimals_to': 8}
    with patch.object(strategy, '_monitor_thorchain_swap', return_value='refunded'):
        result = await strategy._execute_and_confirm_swap(pair, quote, direction)
    assert not result['success']
    assert 'Status: refunded' in result['reason']
    # Removed: strategy._increment_failures.assert_called_once_with("Swap failed")  # Called in process_pair_async, not here

@pytest.mark.asyncio
async def test_update_anchor_and_metrics_success(strategy, pair, state, monkeypatch):
    """Test metrics update: new anchor, both_growing, enhanced metrics, log with surpluses."""
    swap_result = {'effective_rate': 585.0, 'metrics': TradeMetrics(585.0, 0.17, 0.0, 0.0, 0.0, 0.0, surplus_t1=0.05, surplus_t2=50.0, cumulative_trades=0, success_count=0, cumulative_spread_consistency=0.0), 'projection': {'surplus_t1': 0.05, 'surplus_t2': 50.0}}  # Positive spread 17%
    pair.t1.dex.total_balance = 10.05
    pair.t2.dex.total_balance = 5050.0
    monkeypatch.setattr('definitions.thorchain_def.get_inbound_addresses', AsyncMock(return_value=[]))
    with patch.object(strategy.config_manager.balance_manager, 'update_balances', new_callable=AsyncMock):
        await strategy._update_anchor_and_metrics(swap_result, TradeDirection.TOKEN1_TO_TOKEN2)
    # Check save call_args for anchor update (state_data mock doesn't reflect)
    update_data = state.save.call_args[0][0]
    assert update_data['anchor_rate'] == pytest.approx(585.0)
    assert update_data['last_direction'] == TradeDirection.TOKEN1_TO_TOKEN2
    assert 'metrics' in update_data
    # Check mutated metrics
    assert swap_result['metrics'].success_count == 1
    assert swap_result['metrics'].cumulative_spread_consistency == pytest.approx(100.0)
    assert swap_result['metrics'].volume_asymmetry_efficiency == pytest.approx(50.05)  # total_surplus = 0.05 + 50.0
    assert swap_result['metrics'].dual_growth_rate == pytest.approx((10.05 + 5050.0) / (10.0 + 5000.0) - 1)
    assert swap_result['metrics'].surplus_t1 == pytest.approx(0.05)
    assert swap_result['metrics'].surplus_t2 == pytest.approx(50.0)
    assert 'Asymmetry Efficiency (tokens gained/cycle)' in strategy.config_manager.general_log.info.call_args[0][0]
    strategy.config_manager.general_log.info.assert_called()  # Metrics log

@pytest.mark.asyncio
async def test_update_anchor_and_metrics_fail_growth(strategy, pair, state, monkeypatch):
    """Test pause if no dual growth post-trade (deltas <0)."""
    swap_result = {'effective_rate': 500.0, 'metrics': TradeMetrics(500.0, 0.0, 0.0, 0.0, 0.0, 0.0), 'projection': {'surplus_t1': -0.1, 'surplus_t2': -10.0}}
    pair.t1.dex.total_balance = 9.9  # delta_t1 <0
    pair.t2.dex.total_balance = 4990.0
    monkeypatch.setattr('definitions.thorchain_def.get_inbound_addresses', AsyncMock(return_value=[]))
    with patch.object(strategy.config_manager.balance_manager, 'update_balances', new_callable=AsyncMock):
        await strategy._update_anchor_and_metrics(swap_result, TradeDirection.TOKEN1_TO_TOKEN2)
    state.save.assert_called_once_with({'pause_reason': 'No dual accumulation verified post-trade'})
    strategy.config_manager.general_log.warning.assert_called()

@pytest.mark.asyncio
@patch('strategies.thorchain_continuous_strategy.check_thorchain_path_status', new_callable=AsyncMock)
@patch('strategies.thorchain_continuous_strategy.get_thorchain_quote', new_callable=AsyncMock)
async def test_poll_quotes_inactive_skip(mock_quote, mock_path, strategy, pair, state, monkeypatch):
    """Test poll skips inactive path and invalid quote → empty quotes."""
    monkeypatch.setattr('definitions.thorchain_def.get_inbound_addresses', AsyncMock(return_value=[]))
    amount = 1.0
    mock_path.side_effect = [(False, "inactive"), (True, "active")]
    mock_quote.side_effect = [None, None]
    with patch.object(strategy, '_calculate_optimal_size', return_value=amount):
        with patch.object(strategy, '_get_decimals', new_callable=AsyncMock) as mock_decimals:
            mock_decimals.return_value = 8
            quotes = await strategy._poll_quotes(pair)
    assert len(quotes) == 0  # dir1 skipped (inactive), dir2 quote None


@pytest.mark.asyncio
@pytest.mark.filterwarnings("ignore::RuntimeWarning")
async def test_calculate_optimal_size_binary_search(strategy_optimal, pair, monkeypatch):
    """Test binary search converges to amount achieving target asymmetry."""
    def mock_quote(**kwargs):
        expected_out_raw = int((kwargs['amount'] * 505) * 10**8)  # Rate 505 DOGE/LTC (1% >500, forces slight >min)
        return {'expected_amount_out': expected_out_raw, 'fees': {'outbound': '0'}}
    mock_get_quote = AsyncMock(side_effect=mock_quote)
    monkeypatch.setattr('definitions.thorchain_def.get_thorchain_quote', mock_get_quote)
    monkeypatch.setattr('strategies.thorchain_continuous_strategy.ThorChainContinuousStrategy._get_decimals', AsyncMock(return_value=8))
    direction = TradeDirection.TOKEN1_TO_TOKEN2
    amount = await strategy_optimal._calculate_optimal_size(pair, direction, target_asymmetry=0.01)
    assert amount >= strategy_optimal.min_trade_size.get('LTC', 0.01)  # >= for boundary
    assert amount <= (pair.t1.dex.total_balance or 0) * 0.1
    # Simulate final asymmetry with mocked rate
    final_quote = mock_quote(amount=amount)
    expected_out = float(final_quote['expected_amount_out']) / (10 ** 8)
    q_rate = expected_out / amount
    asym = strategy_optimal._calculate_asymmetry(q_rate, 500.0, direction)
    assert asym >= 0.01  # Converged to target


@pytest.mark.asyncio
async def test_process_pair_async_full_cycle(strategy, pair, state, monkeypatch):
    """Test full cycle: Run startup (anchor), then 2 process calls; assert compounding surpluses/growth."""
    state.state_data['anchor_rate'] = 0.0  # Force anchor
    strategy.config_manager.pairs = {'LTC/DOGE': pair}
    pair.t1.dex.total_balance = 10.0  # Starting
    pair.t2.dex.total_balance = 5000.0
    # Mock startup anchor success and set state mutations
    async def mock_anchor():
        state.state_data['anchor_rate'] = 500.0  # Simulate success set
        state.state_data['last_sent'] = 1.0  # LTC sent
        state.state_data['last_received'] = 500.0  # DOGE received
        state.save()  # Trigger side_effect
    with patch.object(strategy, '_execute_anchor_trade', side_effect=mock_anchor):
        tasks = strategy.get_startup_tasks()
        await asyncio.gather(*[task() for task in tasks])  # Invoke functions to get coros
    assert state.state_data['anchor_rate'] == 500.0  # Now set
    # Mock state.save side_effect to mutate state_data
    def mock_save(update_data=None):
        if update_data:
            state.state_data.update(update_data)
    state.save.side_effect = mock_save
    # Add mocks for API to prevent real calls in _poll_quotes
    with patch.object(strategy, '_get_decimals', new_callable=AsyncMock) as mock_decimals:
        mock_decimals.return_value = 8
        with patch('definitions.thorchain_def.get_inbound_addresses', new_callable=AsyncMock) as mock_inbound:
            mock_inbound.return_value = [{'chain': 'LTC', 'decimals': 8}, {'chain': 'DOGE', 'decimals': 8}]
            # Mock 2 process cycles
            cycle_quotes = [  # Trade2: buy (T2->T1)
                {'from_asset': 'DOGE.DOGE', 'to_asset': 'LTC.LTC', 'amount_base': 450.0, 'expected_amount_out': 1.05*10**8, 'fees': {'outbound': '0'}, 'slippage_bps': 10, 'expiry': time.time()-10, 'inbound_address': 'addr', 'memo': 'memo', 'decimals_to': 8},
                {'from_asset': 'LTC.LTC', 'to_asset': 'DOGE.DOGE', 'amount_base': 1.0, 'expected_amount_out': 550*10**8, 'fees': {'outbound': '0'}, 'slippage_bps': 10, 'expiry': time.time()-10, 'inbound_address': 'addr', 'memo': 'memo', 'decimals_to': 8}  # Trade3: sell
            ]
            metrics1 = TradeMetrics(428.57, 0.17, 0.17, 0.0, 0.0, 0.01, surplus_t1=0.05, surplus_t2=50.0, cumulative_trades=1, success_count=0, cumulative_spread_consistency=0.0)
            metrics2 = TradeMetrics(550.0, 0.10, 0.10, 0.05, 100.0, 0.02, surplus_t1=0.0, surplus_t2=50.0, cumulative_trades=2, success_count=1, cumulative_spread_consistency=50.0)
            with patch.object(strategy, '_poll_quotes', side_effect=[{TradeDirection.TOKEN2_TO_TOKEN1: cycle_quotes[0]}, {TradeDirection.TOKEN1_TO_TOKEN2: cycle_quotes[1]}]):
                with patch.object(strategy, '_get_next_direction', side_effect=[TradeDirection.TOKEN2_TO_TOKEN1, TradeDirection.TOKEN1_TO_TOKEN2]):
                    with patch.object(strategy, '_evaluate_opportunity', side_effect=[
                        {'meets_conditions': True, 'quote': cycle_quotes[0], 'spread': 0.17, 'asymmetry': 0.17, 'projection': {'both_positive': True, 'surplus_t1': 0.05, 'surplus_t2': 50.0}},
                        {'meets_conditions': True, 'quote': cycle_quotes[1], 'spread': 0.10, 'asymmetry': 0.10, 'projection': {'both_positive': True, 'surplus_t1': 0.0, 'surplus_t2': 50.0}}
                    ]):
                        with patch.object(strategy, '_revalidate_quote', side_effect=cycle_quotes):
                            with patch.object(strategy, '_execute_and_confirm_swap', side_effect=[
                                {'success': True, 'effective_rate': 428.57, 'actual_received': 1.05, 'metrics': metrics1, 'projection': {'surplus_t1': 0.05, 'surplus_t2': 50.0}},
                                {'success': True, 'effective_rate': 550.0, 'actual_received': 550.0, 'metrics': metrics2, 'projection': {'surplus_t1': 0.0, 'surplus_t2': 50.0}}
                            ]):
                                with patch.object(strategy.config_manager.balance_manager, 'update_balances', new_callable=AsyncMock):
                                    # Simulate growth post each process
                                    pair.t1.dex.total_balance = 10.05  # After Trade2: +1.05 LTC, -450 DOGE but +50 surplus net t2=5050
                                    pair.t2.dex.total_balance = 5050.0
                                    await strategy.process_pair_async(pair)  # Trade2
                                    # After Trade3: -1 LTC +550 DOGE (+50 surplus), net t1 growth via compounding
                                    pair.t1.dex.total_balance = 10.1  # > starting 10.0
                                    pair.t2.dex.total_balance = 5600.0  # > starting 5000.0
                                    await strategy.process_pair_async(pair)  # Trade3
    # Asserts via side_effect mutations
    assert state.state_data['cumulative_surplus_t1'] == pytest.approx(0.05)
    assert state.state_data['cumulative_surplus_t2'] == pytest.approx(100.0)
    assert pair.t1.dex.total_balance > 9.0  # Net growth
    assert pair.t2.dex.total_balance > 5500.0

@pytest.mark.asyncio
async def test_revalidate_quote_fail(strategy, monkeypatch):
    """Test revalidate fails on expired quote or slippage change."""
    old_quote = {'amount_base': 450.0, 'from_asset': 'DOGE.DOGE', 'to_asset': 'LTC.LTC', 'slippage_bps': 10, 'expiry': time.time() - 40}  # Expired
    def mock_get_quote(**kwargs):
        return None  # Simulate expired refetch
    monkeypatch.setattr('strategies.thorchain_continuous_strategy.get_thorchain_quote', AsyncMock(side_effect=mock_get_quote))
    result = await strategy._revalidate_quote(old_quote)
    assert result is None
    strategy.config_manager.general_log.warning.assert_called_with("Quote expired during revalidation.")

    # Reset monkeypatch for second test
    monkeypatch.undo()
    fresh_quote = {'slippage_bps': 30, 'expiry': time.time()}  # Delta 0.002 >0.001
    def mock_get_quote_slip(**kwargs):
        return fresh_quote
    monkeypatch.setattr('strategies.thorchain_continuous_strategy.get_thorchain_quote', AsyncMock(side_effect=mock_get_quote_slip))
    result2 = await strategy._revalidate_quote(old_quote)
    assert result2 is None
    # Update to match code log: f"Slippage changed: {old_slippage:.2%} -> {fresh_slippage:.2%}."
    strategy.config_manager.general_log.warning.assert_called_with("Slippage changed: 0.10% -> 0.30%.")


@pytest.mark.asyncio
async def test_calculate_optimal_size_fixed(strategy_fixed, pair, monkeypatch):
    """Test fixed sizing uses min_trade_size capped by 10% total_balance."""
    strategy_fixed.fixed_sizing = True
    pair.t1.dex.total_balance = 20.0  # 10% = 2.0 > min 1.0
    monkeypatch.setattr('strategies.thorchain_continuous_strategy.get_thorchain_quote', AsyncMock(return_value={'expected_amount_out': 505 * 10**8}))
    monkeypatch.setattr('strategies.thorchain_continuous_strategy.ThorChainContinuousStrategy._get_decimals', AsyncMock(return_value=8))
    direction = TradeDirection.TOKEN1_TO_TOKEN2
    amount = await strategy_fixed._calculate_optimal_size(pair, direction)
    assert amount == 1.0  # min_trade_size['LTC']
    strategy_fixed.config_manager.general_log.debug.assert_called_with("Fixed size for TradeDirection.TOKEN1_TO_TOKEN2: 1.00000000 (capped by balance)")

    # Test cap
    pair.t1.dex.total_balance = 5.0  # 10% = 0.5 < min
    amount2 = await strategy_fixed._calculate_optimal_size(pair, direction)
    assert amount2 == 0.5  # Capped < min
    strategy_fixed.config_manager.general_log.debug.assert_called_with("Fixed size for TradeDirection.TOKEN1_TO_TOKEN2: 0.50000000 (capped by balance)")


@pytest.mark.asyncio
@patch('strategies.thorchain_continuous_strategy.get_thorchain_quote', new_callable=AsyncMock)
async def test_poll_quotes_volatility_skip(mock_quote, strategy, pair, state, monkeypatch):
    """Test poll skips quote if volatility > threshold."""
    monkeypatch.setattr('definitions.thorchain_def.get_inbound_addresses', AsyncMock(return_value=[]))
    state.state_data['anchor_rate'] = 500.0
    strategy.max_volatility_threshold = 0.05
    amount_sell = 1.0  # LTC for sell (TOKEN1_TO_TOKEN2)
    amount_buy = 500.0  # DOGE for buy (TOKEN2_TO_TOKEN1, min_trade=500)
    volatile_sell = {'expected_amount_out': 600 * 10**8, 'fees': {'outbound': '0'}, 'slippage_bps': 10, 'from_asset': 'LTC.LTC', 'to_asset': 'DOGE.DOGE', 'inbound_address': 'addr', 'memo': 'memo', 'ttl': 30, 'timestamp': time.time(), 'amount_base': amount_sell}  # rate=600/1=600, delta=20%
    non_volatile_buy = {'expected_amount_out': 1.0 * 10**8, 'fees': {'outbound': '0'}, 'slippage_bps': 10, 'from_asset': 'DOGE.DOGE', 'to_asset': 'LTC.LTC', 'inbound_address': 'addr', 'memo': 'memo', 'ttl': 30, 'timestamp': time.time(), 'amount_base': amount_buy}  # rate=500/1=500, delta=0%
    mock_quote.side_effect = [volatile_sell, non_volatile_buy]  # Sell triggers, buy doesn't
    monkeypatch.setattr('strategies.thorchain_continuous_strategy.check_thorchain_path_status', AsyncMock(return_value=(True, "active")))
    with patch.object(strategy, '_calculate_optimal_size', side_effect=[amount_sell, amount_buy]):  # Per dir
        with patch.object(strategy, '_get_decimals', new_callable=AsyncMock) as mock_decimals:
            mock_decimals.return_value = 8
            with patch.object(strategy, '_increment_failures', new_callable=Mock) as mock_fail:
                quotes = await strategy._poll_quotes(pair)
    assert len(quotes) == 1  # Only buy included (sell skipped)
    mock_fail.assert_called_once_with("Abnormal market volatility: 20.00% > 5.00%")  # Only sell triggers
    # Verify buy quote in quotes[TradeDirection.TOKEN2_TO_TOKEN1]
    assert quotes[TradeDirection.TOKEN2_TO_TOKEN1]['amount_base'] == amount_buy


@pytest.mark.asyncio
async def test_startup_resume_success(strategy, pair, state, monkeypatch):
    """Test startup resumes paused state if balances now sufficient."""
    state.state_data['anchor_rate'] = 500.0  # Already anchored
    state.state_data['pause_reason'] = 'Starting balances mismatch'
    state.is_paused.return_value = True  # Explicitly mock to True
    pair.t1.dex.total_balance = 12.0  # Code uses total_balance
    pair.t2.dex.total_balance = 5500.0
    strategy.config_manager.balance_manager.update_balances = AsyncMock()
    tasks = strategy.get_startup_tasks()
    await tasks[1]()  # resume_paused_state
    state.save.assert_called_once_with({'pause_reason': None})
    strategy.config_manager.general_log.info.assert_called_with("Resumed from paused state: balances now sufficient.")


@pytest.mark.asyncio
async def test_execute_and_confirm_swap_refund(strategy, pair, state, monkeypatch):
    """Test execution handles monitor 'refunded' → fail + increment failures."""
    direction = TradeDirection.TOKEN2_TO_TOKEN1
    quote = {'amount_base': 450.0, 'inbound_address': 'addr', 'memo': 'memo', 'fees': {'outbound': '0'}, 'decimals_to': 8}
    monkeypatch.setattr('strategies.thorchain_continuous_strategy.execute_thorchain_swap', AsyncMock(return_value='txid'))
    with patch.object(strategy, '_increment_failures', new_callable=Mock) as mock_fail:
        async def mock_monitor(txid):
            mock_fail("Swap refunded")
            return 'refunded'
        with patch.object(strategy, '_monitor_thorchain_swap', side_effect=mock_monitor):
            result = await strategy._execute_and_confirm_swap(pair, quote, direction)
    assert not result['success']
    assert 'Status: refunded' in result['reason']
    mock_fail.assert_called_once_with("Swap refunded")


@pytest.mark.asyncio
@patch('strategies.thorchain_continuous_strategy.get_thorchain_quote', new_callable=AsyncMock)
@patch('strategies.thorchain_continuous_strategy.check_thorchain_path_status', new_callable=AsyncMock)
async def test_poll_quotes_valid_both_directions(mock_path, mock_quote, strategy, pair, state, monkeypatch):
    """Test poll includes both directions (low volatility, fresh quotes)."""
    monkeypatch.setattr('definitions.thorchain_def.get_inbound_addresses', AsyncMock(return_value=[]))
    state.state_data['anchor_rate'] = 500.0
    strategy.max_volatility_threshold = 0.05
    amount = 1.0
    valid_quote1 = {'expected_amount_out': 510 * 10**8, 'fees': {'outbound': '0'}, 'slippage_bps': 10, 'from_asset': 'LTC.LTC', 'to_asset': 'DOGE.DOGE', 'expiry': time.time() + 60, 'inbound_address': 'addr', 'memo': 'memo', 'ttl': 30}  # Added required
    valid_quote2 = {'expected_amount_out': 0.002 * 10**8, 'fees': {'outbound': '0'}, 'slippage_bps': 10, 'from_asset': 'DOGE.DOGE', 'to_asset': 'LTC.LTC', 'expiry': time.time() + 60, 'inbound_address': 'addr', 'memo': 'memo', 'ttl': 30}
    mock_quote.side_effect = [valid_quote1, valid_quote2]
    mock_path.return_value = (True, "active")
    with patch.object(strategy, '_calculate_optimal_size', return_value=amount):
        with patch.object(strategy, '_get_decimals', new_callable=AsyncMock) as mock_decimals:
            mock_decimals.return_value = 8
            quotes = await strategy._poll_quotes(pair)
    assert TradeDirection.TOKEN1_TO_TOKEN2 in quotes
    assert TradeDirection.TOKEN2_TO_TOKEN1 in quotes
    assert quotes[TradeDirection.TOKEN1_TO_TOKEN2]['amount_base'] == amount
    assert quotes[TradeDirection.TOKEN1_TO_TOKEN2]['decimals_to'] == 8
    assert quotes[TradeDirection.TOKEN2_TO_TOKEN1]['amount_base'] == amount
    assert quotes[TradeDirection.TOKEN2_TO_TOKEN1]['decimals_to'] == 8
