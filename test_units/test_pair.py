import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest
import yaml

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Remove custom event loop fixtures
# Pytest-asyncio already handles event management

from definitions.pair import DexPair, Pair, CexPair
from definitions.token import Token, CexToken, DexToken


@pytest.fixture
def mock_pair():
    """Fixture to create a mock Pair instance for DexPair testing."""
    token1 = MagicMock(spec=Token)
    token1.symbol = 'T1'
    token1.cex = MagicMock(spec=CexToken)
    token1.cex.update_price = AsyncMock()
    token1.cex.update_block_ticker = AsyncMock()
    token1.dex = MagicMock(spec=DexToken)
    token1.dex.request_addr = AsyncMock()
    token1.dex.read_address = AsyncMock()
    token1.dex.write_address = AsyncMock()
    token1.cex.cex_price = 1.0
    token1.dex.address = 't1_addr'

    token2 = MagicMock(spec=Token)
    token2.symbol = 'T2'
    token2.cex = MagicMock(spec=CexToken)
    token2.cex.update_price = AsyncMock()
    token2.cex.update_block_ticker = AsyncMock()
    token2.dex = MagicMock(spec=DexToken)
    token2.dex.request_addr = AsyncMock()
    token2.dex.read_address = AsyncMock()
    token2.dex.write_address = AsyncMock()
    token2.cex.cex_price = 0.1
    token2.dex.address = 't2_addr'

    config_manager = MagicMock()
    config_manager.strategy_instance = MagicMock()
    config_manager.strategy_instance.get_dex_history_file_path.return_value = 'mock_history.yaml'
    config_manager.general_log = MagicMock()
    config_manager.error_handler = MagicMock()
    config_manager.error_handler.handle_async = AsyncMock()
    config_manager.controller = None

    pair = MagicMock(spec=Pair)
    pair.t1 = token1
    pair.t2 = token2
    pair.name = 'T1_T2_pair'
    pair.symbol = 'T1/T2'
    pair.strategy = 'pingpong'  # Set strategy to avoid error in tests
    pair.config_manager = config_manager
    pair.cex = MagicMock(spec=CexPair)
    pair.cex.update_pricing = AsyncMock()
    pair.cex.price = 10.0  # t1/t2 price
    pair.dex_enabled = True
    pair.cfg = {
        'name': 'T1_T2_pair',
        'sell_price_offset': 0.01,
        'spread': 0.02
    }
    return pair


@pytest.fixture
def dex_pair(mock_pair):
    """Fixture to create a DexPair instance with a mock parent Pair."""
    return DexPair(mock_pair, partial_percent=None)


def test_truncate():
    """Tests the static truncate method."""
    assert DexPair.truncate(1.23456789123, 8) == 1.23456789
    assert DexPair.truncate(1.99999999999, 8) == 1.99999999
    assert DexPair.truncate(5, 4) == 5.0
    assert DexPair.truncate(0.123, 10) == 0.123


def test_map_order_status(dex_pair):
    """Tests the mapping of raw order status strings to internal constants."""
    status_map = {
        "open": dex_pair.STATUS_OPEN,
        "new": dex_pair.STATUS_OPEN,
        "created": dex_pair.STATUS_OTHERS,
        "initialized": dex_pair.STATUS_OTHERS,
        "committed": dex_pair.STATUS_OTHERS,
        "finished": dex_pair.STATUS_FINISHED,
        "expired": dex_pair.STATUS_CANCELLED_WITHOUT_CALL,
        "offline": dex_pair.STATUS_ERROR_SWAP,
        "canceled": dex_pair.STATUS_CANCELLED_WITHOUT_CALL,
        "invalid": dex_pair.STATUS_ERROR_SWAP,
        "rolled back": dex_pair.STATUS_ERROR_SWAP,
        "rollback failed": dex_pair.STATUS_ERROR_SWAP,
        "unknown_status": dex_pair.STATUS_OPEN,  # Default case
    }
    for raw_status, expected in status_map.items():
        dex_pair.order = {'status': raw_status}
        assert dex_pair._map_order_status() == expected


def test_check_price_in_range(dex_pair):
    """Tests the logic for checking if the current price is within tolerance."""
    strategy_mock = dex_pair.pair.config_manager.strategy_instance
    strategy_mock.get_price_variation_tolerance.return_value = 0.02  # 2% tolerance

    # Mock the strategy's variation calculation
    # Simulate a normal SELL order check
    strategy_mock.calculate_variation_based_on_side.return_value = 1.01  # 1% variation
    dex_pair.current_order = {'side': 'SELL', 'org_pprice': 10.0}
    dex_pair.pair.cex.price = 10.1
    assert dex_pair.check_price_in_range() is True

    # Price outside tolerance (high)
    strategy_mock.calculate_variation_based_on_side.return_value = 1.03
    dex_pair.pair.cex.price = 10.3
    assert dex_pair.check_price_in_range() is False

    # Price outside tolerance (low)
    strategy_mock.calculate_variation_based_on_side.return_value = 0.97
    dex_pair.pair.cex.price = 9.7
    assert dex_pair.check_price_in_range() is False

    # Price at edge of tolerance
    strategy_mock.calculate_variation_based_on_side.return_value = 0.9801
    dex_pair.pair.cex.price = 9.801
    assert dex_pair.check_price_in_range() is True

    # Locked order (signaled by list return)
    strategy_mock.calculate_variation_based_on_side.return_value = [1.05]  # 5% variation, but locked
    dex_pair.current_order = {'side': 'BUY', 'org_pprice': 10.0}
    dex_pair.pair.cex.price = 10.5
    assert dex_pair.check_price_in_range() is True
    # Verify variation is stored as a list
    assert isinstance(dex_pair.variation, list)


def test_create_virtual_sell_order(dex_pair):
    """Tests the creation of a virtual sell order."""
    strategy_mock = dex_pair.pair.config_manager.strategy_instance
    strategy_mock.calculate_sell_price.return_value = 10.0
    strategy_mock.build_sell_order_details.return_value = (1.5, 0.01)  # amount, offset
    dex_pair.t1.dex.free_balance = 2.0  # Sufficient balance

    dex_pair.create_virtual_sell_order()

    order = dex_pair.current_order
    assert order is not None
    assert order['side'] == 'SELL'
    assert order['maker'] == 'T1'
    assert order['taker'] == 'T2'
    assert order['maker_size'] == pytest.approx(1.5)
    assert order['taker_size'] == pytest.approx(1.5 * 10.0 * (1 + 0.01))
    assert order['dex_price'] == pytest.approx(10.0 * (1 + 0.01))
    assert order['taker_size'] == pytest.approx(1.5 * 10.0 * (1 + 0.01))
    assert order['dex_price'] == pytest.approx(10.0 * (1 + 0.01))


def test_create_virtual_buy_order(dex_pair):
    """Tests the creation of a virtual buy order."""
    strategy_mock = dex_pair.pair.config_manager.strategy_instance
    strategy_mock.determine_buy_price.return_value = 9.0
    strategy_mock.build_buy_order_details.return_value = (1.5, 0.02)  # amount, spread

    dex_pair.create_virtual_buy_order()

    order = dex_pair.current_order
    assert order is not None
    assert order['side'] == 'BUY'
    assert order['maker'] == 'T2'
    assert order['taker'] == 'T1'
    assert order['taker_size'] == pytest.approx(1.5)  # taker_size is amount for buy
    assert order['maker_size'] == pytest.approx(1.5 * 9.0 * (1 - 0.02))
    assert order['dex_price'] == pytest.approx(9.0 * (1 - 0.02))


def test_is_shutting_down(dex_pair):
    """Tests the _is_shutting_down check."""
    controller_mock = MagicMock()
    dex_pair.pair.config_manager.controller = controller_mock

    # Test case 1: Shutdown event is not set
    controller_mock.shutdown_event.is_set.return_value = False
    assert dex_pair._is_shutting_down() is False

    # Test case 2: Shutdown event is set
    controller_mock.shutdown_event.is_set.return_value = True
    assert dex_pair._is_shutting_down() is True

    # Test case 3: Controller does not exist
    dex_pair.pair.config_manager.controller = None
    assert dex_pair._is_shutting_down() is False


@pytest.mark.asyncio
async def test_dex_create_order_insufficient_balance(dex_pair):
    """Tests that create_order does not call makeorder if balance is too low."""
    # Mock strategy calls to prevent TypeErrors inside create_virtual_sell_order
    strategy_mock = dex_pair.pair.config_manager.strategy_instance
    strategy_mock.calculate_sell_price.return_value = 10.0
    strategy_mock.build_sell_order_details.return_value = (1.0, 0.01)  # amount = 1.0

    dex_pair.create_virtual_sell_order()  # This will set maker_size to 1.0
    # Mock balance to be lower than required maker_size
    dex_pair.t1.dex.free_balance = 0.1

    mock_makeorder = AsyncMock()
    dex_pair.pair.config_manager.xbridge_manager.makeorder = mock_makeorder

    await dex_pair.create_order()

    mock_makeorder.assert_not_awaited()
    dex_pair.pair.config_manager.general_log.error.assert_called()
    assert "balance too low" in dex_pair.pair.config_manager.general_log.error.call_args[0][0]


@pytest.mark.asyncio
async def test_dex_create_order_xb_error(dex_pair):
    """Tests that _handle_order_error is called when makeorder returns an error."""
    # Create an AsyncMock that returns an error response
    mock_makeorder = AsyncMock(return_value={'error': 'Failed to make order', 'code': 1001})

    # Use AsyncMock to avoid coroutine flag issues
    async_notify_user_mock = AsyncMock()

    strategy_mock = dex_pair.pair.config_manager.strategy_instance
    strategy_mock.calculate_sell_price.return_value = 10.0
    strategy_mock.build_sell_order_details.return_value = (1.0, 0.01)

    dex_pair.create_virtual_sell_order()
    dex_pair.t1.dex.free_balance = 2.0  # Sufficient balance

    dex_pair.pair.config_manager.xbridge_manager.makeorder = mock_makeorder

    # Fix mock config_manager.async_notify_user
    dex_pair.pair.config_manager.async_notify_user = async_notify_user_mock

    # Mock the async strategy handler
    strategy_handle_error_mock = AsyncMock()
    strategy_handle_error_mock.side_effect = lambda dex_pair_arg: setattr(dex_pair_arg, 'order', None)
    dex_pair.pair.config_manager.strategy_instance.handle_order_status_error = strategy_handle_error_mock

    # Mock the config manager to return True from error handler
    dex_pair.pair.config_manager.error_handler.handle_async = AsyncMock(return_value=True)

    await dex_pair.create_order()

    # For pingpong strategy (which this test uses), the order should be cleared by handle_order_status_error
    assert dex_pair.order is None
    assert dex_pair.disabled is True
    strategy_handle_error_mock.assert_called_once_with(dex_pair)
    dex_pair.pair.config_manager.general_log.error.assert_called()


def test_map_order_status_invalid(dex_pair):
    """Tests that invalid statuses default to STATUS_OPEN."""
    dex_pair.order = {'status': 'made_up_status'}
    assert dex_pair._map_order_status() == dex_pair.STATUS_OPEN


def test_init_virtual_order_no_disabled_coins(dex_pair):
    """Tests that init_virtual_order works when no coins are disabled."""
    # Should not mark pair as disabled
    dex_pair.init_virtual_order(disabled_coins=None, display=False)
    assert not dex_pair.disabled


def test_write_last_order_history_failure(dex_pair):
    """Tests error handling in write_last_order_history."""
    file_path = dex_pair._get_history_file_path()
    with patch("builtins.open", mock_open()) as mock_file:
        mock_file.side_effect = IOError("Disk full")
        dex_pair.write_last_order_history()
        # Verify error handler was called with expected exception type
        handle_call_args = dex_pair.pair.config_manager.error_handler.handle.call_args
        assert handle_call_args is not None
        # Unpack the call arguments: (args, kwargs)
        args, kwargs = handle_call_args
        # The error is the first positional argument
        error = args[0]
        # Context is passed as keyword argument
        context = kwargs['context']
        assert isinstance(error, IOError)
        assert context["pair"] == dex_pair.pair.name
        assert context["stage"] == "write_last_order_history"
        assert context["file_path"] == file_path


@pytest.mark.asyncio
async def test_update_taker_address_mismatch(dex_pair):
    """Tests that taker address doesn't update for mismatched tokens."""
    dex_pair.order = {'taker': 'OTHER'}
    dex_pair.t1.symbol = 'T1'
    dex_pair.t2.symbol = 'T2'
    # Should not attempt to update address
    await dex_pair._update_taker_address()
    dex_pair.t1.dex.request_addr.assert_not_awaited()
    dex_pair.t2.dex.request_addr.assert_not_awaited()


@pytest.mark.asyncio
async def test_dex_at_order_finished(dex_pair):
    """Tests order completion workflow."""
    # Setup
    dex_pair.order = {'id': 'test_order_id', 'status': 'finished', 'taker': 'T2', 'taker_address': 'test_address'}
    dex_pair.current_order = {
        'symbol': 'T1/T2',
        'maker': 'T1',
        'maker_address': 'maker_addr',
        'taker': 'T2',
        'taker_address': 'taker_addr',
        'maker_size': 1.0,
        'taker_size': 10.0,
        'dex_price': 10.0
    }
    dex_pair.t2.symbol = 'T2'  # Ensure consistent symbol

    # Setup mocks to return completed futures
    with patch.object(dex_pair.t2.dex, 'request_addr', new_callable=AsyncMock) as addr_mock, \
            patch.object(dex_pair.pair.config_manager.strategy_instance, 'handle_finished_order',
                         new_callable=AsyncMock) as handle_mock, \
            patch.object(dex_pair, 'write_last_order_history') as write_mock:
        # Execute
        await dex_pair.at_order_finished(disabled_coins=[])

        # Verify async calls were awaited
        addr_mock.assert_awaited_once()
        handle_mock.assert_awaited_once_with(dex_pair, [])
        write_mock.assert_called_once()

    # Verify order history update
    assert dex_pair.order_history == dex_pair.current_order


def test_dex_pair_read_last_order_history(dex_pair):
    """Tests reading order history from file."""
    # Test 1: Successful read
    mock_history = {'side': 'SELL', 'maker_size': 1.0}
    with patch('builtins.open', mock_open(read_data=yaml.dump(mock_history))), \
            patch('yaml.safe_load', return_value=mock_history):
        dex_pair.read_last_order_history()
        assert dex_pair.order_history == mock_history

    # Test 2: File not found
    dex_pair.order_history = None  # Reset
    with patch('builtins.open', side_effect=FileNotFoundError):
        dex_pair.read_last_order_history()
        assert dex_pair.order_history is None
        dex_pair.pair.config_manager.general_log.info.assert_called()

    # Test 3: Corrupted YAML file
    dex_pair.order_history = None  # Reset
    with patch('builtins.open', mock_open(read_data="- {")), \
            patch('yaml.safe_load', side_effect=yaml.YAMLError):
        dex_pair.read_last_order_history()
        assert dex_pair.order_history is None
        dex_pair.pair.config_manager.error_handler.handle.assert_called_once()

    # Test 4: Empty file
    dex_pair.order_history = None
    with patch('builtins.open', mock_open(read_data="")) as mock_file:
        mock_file.return_value.read.return_value = ""
        dex_pair.read_last_order_history()
        assert dex_pair.order_history is None
        dex_pair.pair.config_manager.general_log.info.assert_called()


def test_pair_initialization():
    """Tests Pair class initialization with different configurations."""
    token1 = MagicMock(spec=Token)
    token1.symbol = 'TEST1'
    token1.cex = MagicMock(spec=CexToken)
    token1.dex = MagicMock(spec=DexToken, address='test1_addr')

    token2 = MagicMock(spec=Token)
    token2.symbol = 'TEST2'
    token2.cex = MagicMock(spec=CexToken)
    token2.dex = MagicMock(spec=DexToken, address='test2_addr')

    config_manager = MagicMock()
    config_manager.strategy_instance = None

    # Test with minimal config
    cfg = {'name': 'TEST_PAIR'}
    pair = Pair(token1, token2, config_manager, cfg)
    assert pair.name == 'TEST_PAIR'
    assert pair.symbol == 'TEST1/TEST2'
    assert pair.t1 == token1
    assert pair.t2 == token2
    assert pair.dex_enabled is True
    assert pair.min_sell_price_usd is None
    assert pair.sell_price_offset is None
    assert isinstance(pair.dex, DexPair)
    assert isinstance(pair.cex, CexPair)
    # Test dex addresses
    assert pair.dex.t1.dex.address == token1.dex.address
    assert pair.dex.t2.dex.address == token2.dex.address

    # Test with full config
    cfg = {
        'name': 'TRADE_PAIR',
        'sell_price_offset': 0.02,
        'spread': 0.01
    }
    pair = Pair(token1, token2, config_manager, cfg, min_sell_price_usd=100.0,
                strategy='pingpong', partial_percent=0.5)
    assert pair.name == 'TRADE_PAIR'
    assert pair.sell_price_offset == 0.02
    assert pair.min_sell_price_usd == 100.0
    assert pair.strategy == 'pingpong'
    assert pair.dex.partial_percent == 0.5


def test_pair_disabled_orders():
    """Tests dex_enabled=False prevents order creation and clears existing orders."""
    token1 = MagicMock(spec=Token)
    token1.symbol = 'TEST1'
    token1.cex = MagicMock(spec=CexToken)
    token1.cex.cex_price = 1.0
    token1.dex = MagicMock(spec=DexToken)
    token1.dex.address = 'test1_addr'

    token2 = MagicMock(spec=Token)
    token2.symbol = 'TEST2'
    token2.cex = MagicMock(spec=CexToken)
    token2.cex.cex_price = 0.1
    token2.dex = MagicMock(spec=DexToken)
    token2.dex.address = 'test2_addr'

    config_manager = MagicMock()
    config_manager.strategy_instance = MagicMock()
    config_manager.strategy_instance.calculate_sell_price.return_value = 10.0
    config_manager.strategy_instance.build_sell_order_details.return_value = (1.0, 0.01)

    cfg = {'name': 'DISABLED_PAIR'}
    pair = Pair(token1, token2, config_manager, cfg, dex_enabled=False)

    # Should block order creation
    pair.dex.create_virtual_sell_order()
    assert pair.dex.current_order is None

    # Enable and create order, then disable and verify cleanup
    pair.dex_enabled = True
    pair.dex.create_virtual_sell_order()
    assert pair.dex.current_order is not None
    pair.dex_enabled = False
    pair.dex.create_virtual_sell_order()
    assert pair.dex.current_order is None


@pytest.mark.asyncio
async def test_dex_create_order_disabled(dex_pair):
    """Tests that create_order doesn't proceed when pair is disabled."""
    strategy_mock = dex_pair.pair.config_manager.strategy_instance
    strategy_mock.calculate_sell_price.return_value = 10.0
    strategy_mock.build_sell_order_details.return_value = (1.0, 0.01)

    # Setup virtual order
    dex_pair.create_virtual_sell_order()
    dex_pair.disabled = True

    mock_makeorder = AsyncMock()
    dex_pair.pair.config_manager.xbridge_manager.makeorder = mock_makeorder

    # Run create_order
    await dex_pair.create_order()

    mock_makeorder.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_order_error_with_ignored_code(dex_pair):
    """Tests that ignored error codes don't disable the pair."""
    # Set up the strategy to not handle errors so we test the base behavior
    dex_pair.pair.config_manager.strategy_instance.handle_order_status_error = None

    dex_pair.order = {'error': 'Test error', 'code': 1019}
    dex_pair.disabled = False

    await dex_pair._handle_order_error()

    assert not dex_pair.disabled
    mock_log = dex_pair.pair.config_manager.general_log.error
    mock_log.assert_called_once()
    log_message = mock_log.call_args[0][0]
    assert "Test error" in log_message


@pytest.mark.asyncio
async def test_update_dex_orderbook(dex_pair):
    """Tests updating DEX orderbook."""
    initial_orderbook = dex_pair.orderbook  # Should be None

    # Mock the dxgetorderbook call
    mock_dxgetorderbook = AsyncMock(return_value={'asks': [], 'bids': [], 'detail': 'ignored'})
    dex_pair.pair.config_manager.xbridge_manager.dxgetorderbook = mock_dxgetorderbook

    await dex_pair.update_dex_orderbook()

    mock_dxgetorderbook.assert_awaited_once_with(detail=3, maker='T1', taker='T2')
    assert dex_pair.orderbook == {'asks': [], 'bids': []}
    assert dex_pair.orderbook != initial_orderbook
    assert 'detail' not in dex_pair.orderbook


@pytest.mark.asyncio
async def test_check_price_variation_cancellation(dex_pair):
    """Tests cancellation and reinit when prices go out of range."""
    # Setup virtual order
    dex_pair.current_order = {'side': 'SELL', 'org_pprice': 10.0, 'dex_price': 10.0}
    dex_pair.order = {'id': 'test_order', 'status': 'open'}

    # Always report price out of range
    dex_pair.pair.config_manager.strategy_instance.calculate_variation_based_on_side.return_value = 1.03  # 3% variation
    dex_pair.pair.config_manager.strategy_instance.get_price_variation_tolerance.return_value = 0.01  # 1% tolerance

    # Mock methods
    mock_cancel = AsyncMock()
    mock_reinit = AsyncMock()
    dex_pair.cancel_myorder_async = mock_cancel
    dex_pair._reinit_virtual_order = mock_reinit

    # Test with disabled_coins
    disabled_coins = ['T3']
    await dex_pair.check_price_variation(disabled_coins, display=True)

    # Verify cancellation and reinit
    mock_cancel.assert_awaited_once()
    mock_reinit.assert_awaited_once_with(disabled_coins)


@pytest.mark.asyncio
async def test_status_open_flow(dex_pair):
    """Tests order status workflow for STATUS_OPEN."""
    # Setup
    dex_pair.order = {'id': 'open_order', 'status': 'open'}
    dex_pair.disabled = False

    # Mock the update_pricing method to avoid await issue
    dex_pair.pair.cex.update_pricing = AsyncMock()

    # Mock methods
    mock_check_status = AsyncMock(return_value=dex_pair.STATUS_OPEN)
    mock_handle_open = AsyncMock()
    dex_pair.check_order_status = mock_check_status
    dex_pair.handle_status_open = mock_handle_open

    # Execute status check
    await dex_pair.status_check(disabled_coins=['T3'], display=True)

    # Verify
    mock_check_status.assert_awaited_once_with()
    mock_handle_open.assert_awaited_once_with(['T3'], True)


@pytest.mark.asyncio
async def test_handle_status_open_disabled_coins(dex_pair):
    """Tests cancellation when coins are disabled during open status."""
    # Setup
    dex_pair.order = {'id': 'open_order', 'status': 'open'}
    disabled_coins = ['T1', 'T3']  # T1 is in the pair

    # Mock cancellation method
    dex_pair.cancel_myorder_async = AsyncMock()

    # Execute
    await dex_pair.handle_status_open(disabled_coins, display=True)

    # Verify cancellation occurred
    dex_pair.cancel_myorder_async.assert_awaited_once()


def test_partial_order_creation(dex_pair):
    """Tests partial order creation logic."""
    # Setup
    dex_pair.partial_percent = 0.5  # 50% partial order

    # Mock strategy methods
    strategy_mock = dex_pair.pair.config_manager.strategy_instance
    strategy_mock.calculate_sell_price.return_value = 10.0
    strategy_mock.build_sell_order_details.return_value = (1.5, 0.01)  # amount, offset

    # Create sell order
    dex_pair.create_virtual_sell_order()
    order = dex_pair.current_order

    # Verify order details
    assert order['type'] == 'partial'
    assert 'minimum_size' in order
    assert order['minimum_size'] == pytest.approx(1.5 * 0.5)


def test_zero_partial_order(dex_pair):
    """Tests 0% partial falls back to exact order."""
    dex_pair.partial_percent = 0.0
    strategy_mock = dex_pair.pair.config_manager.strategy_instance
    strategy_mock.calculate_sell_price.return_value = 10.0
    strategy_mock.build_sell_order_details.return_value = (1.5, 0.01)

    dex_pair.create_virtual_sell_order()
    order = dex_pair.current_order

    assert order['type'] == 'exact'
    assert 'minimum_size' not in order


@pytest.mark.asyncio
async def test_complex_status_workflow(dex_pair):
    """Tests behavior for unsupported order statuses."""
    # Setup order with unexpected status
    dex_pair.order = {'id': 'weird_order', 'status': 'unknown_status'}

    # Mock the update_pricing method to avoid await issue
    dex_pair.pair.cex.update_pricing = AsyncMock()

    # Mock to trigger STATUS_OTHERS path - use AsyncMock for async methods
    mock_check_status = AsyncMock(return_value=dex_pair.STATUS_OTHERS)
    mock_cvar = MagicMock(return_value=True)
    dex_pair.check_order_status = mock_check_status
    dex_pair.check_price_in_range = mock_cvar

    # Test
    await dex_pair.status_check(disabled_coins=None, display=False)

    # Verify status check and price validation
    mock_check_status.assert_called_once()
    mock_cvar.assert_called_once_with(display=False)


@pytest.mark.asyncio
async def test_dex_handle_shutdown_event(dex_pair):
    """Tests graceful exit during shutdown event."""
    # Set shutdown flag
    controller_mock = MagicMock()
    controller_mock.shutdown_event.is_set.return_value = True
    dex_pair.pair.config_manager.controller = controller_mock

    # Create and complete the async task
    task = asyncio.create_task(dex_pair.create_order())
    await task

    # Verify no orders were created
    dex_pair.pair.config_manager.general_log.warning.assert_called_with(
        "Skipping order creation for T1/T2 - shutdown in progress"
    )
    assert dex_pair.order is None


def test_dex_handle_order_status_error(dex_pair):
    """Tests error handling in dex_order_status when no status present."""
    # Setup
    dex_pair.pair.strategy = 'pingpong'  # Explicitly set strategy
    dex_pair.order = {'id': 'bad_order', 'error': 'test error'}

    # Mock the logger error method
    logger_error = MagicMock()
    dex_pair.pair.config_manager.general_log.error = logger_error

    # Execute
    dex_pair._handle_order_status_error()

    # Verify
    logger_error.assert_called_once()
    assert dex_pair.order is None


@pytest.mark.parametrize("strategy", ['pingpong', 'basic_seller', 'arbitrage'])
def test_dex_handle_order_status_error_with_strategies(mock_pair, strategy):
    """Tests order clearing behavior for different strategies."""
    # Arrange
    mock_pair.strategy = strategy
    dex_pair = DexPair(mock_pair, partial_percent=None)
    dex_pair.order = {'id': 'test_order', 'error': 'test'}

    # Act
    dex_pair._handle_order_status_error()

    # Assert
    if strategy in ['pingpong', 'basic_seller']:
        assert dex_pair.order is None
    else:
        assert dex_pair.order is not None


class TestCexPair:
    @pytest.fixture
    def mock_cex_pair(self, mock_pair):
        """Fixture to create a CexPair instance."""
        return CexPair(mock_pair)

    def test_cex_pair_init(self, mock_cex_pair):
        """Tests CexPair construction and token initialization."""
        assert mock_cex_pair.pair is not None
        assert mock_cex_pair.t1.symbol == 'T1'
        assert mock_cex_pair.t2.symbol == 'T2'
        assert mock_cex_pair.symbol == 'T1/T2'
        assert mock_cex_pair.price is None
        assert mock_cex_pair.cex_orderbook is None
        assert mock_cex_pair.cex_orderbook_timer is None

    @pytest.mark.asyncio
    async def test_update_pricing_success(self, mock_cex_pair):
        """Tests successful price calculation."""
        # Arrange
        mock_cex_pair.t1.cex.cex_price = 1.5  # e.g., T1/BTC price
        mock_cex_pair.t2.cex.cex_price = 0.5  # e.g., T2/BTC price

        # Act
        await mock_cex_pair.update_pricing()

        # Assert
        assert mock_cex_pair.price == 3.0  # 1.5 / 0.5

    @pytest.mark.asyncio
    async def test_update_pricing_missing_price(self, mock_cex_pair):
        """Tests that price is None if a token price is missing."""
        # Arrange
        mock_cex_pair.t1.cex.cex_price = 1.5
        mock_cex_pair.t2.cex.cex_price = None

        # Act
        await mock_cex_pair.update_pricing()

        # Assert
        assert mock_cex_pair.price is None

    @pytest.mark.asyncio
    async def test_update_pricing_division_by_zero(self, mock_cex_pair):
        """Tests that price is None if the denominator is zero."""
        # Arrange
        mock_cex_pair.t1.cex.cex_price = 1.5
        mock_cex_pair.t2.cex.cex_price = 0

        # Act
        await mock_cex_pair.update_pricing()

        # Assert
        assert mock_cex_pair.price is None

    @pytest.mark.asyncio
    @patch.object(time, 'time', return_value=1234567890)
    async def test_update_pricing_logging(self, mock_time, mock_cex_pair):
        """Tests logging during price updates."""
        mock_cex_pair.t1.cex.cex_price = 2.0
        mock_cex_pair.t2.cex.cex_price = 0.4
        mock_cex_pair.pair.config_manager.general_log.info = MagicMock()

        await mock_cex_pair.update_pricing(display=True)

        # Verify logging occurred
        log_args = mock_cex_pair.pair.config_manager.general_log.info.call_args[0][0]
        assert "T1 btc_p: 2.0" in log_args
        assert "T2 btc_p: 0.4" in log_args
        assert "T1/T2 price: 5.0" in log_args

    @pytest.mark.asyncio
    async def test_update_orderbook_timer_conditions(self, mock_cex_pair):
        """Tests CexPair.update_orderbook with timer conditions."""
        # Mock the fetch method
        mock_fetch = AsyncMock(return_value={'bids': [[100, 1]], 'asks': [[101, 1]]})
        mock_cex_pair.pair.config_manager.ccxt_manager.ccxt_call_fetch_order_book = mock_fetch

        # Test with timer reset
        mock_cex_pair.cex_orderbook_timer = None
        await mock_cex_pair.update_orderbook(limit=10, ignore_timer=False)
        mock_fetch.assert_awaited_once()
        assert mock_cex_pair.cex_orderbook == {'bids': [[100, 1]], 'asks': [[101, 1]]}
        assert abs(time.time() - mock_cex_pair.cex_orderbook_timer) < 1

        # Reset for timer not expired test
        mock_fetch.reset_mock()
        mock_cex_pair.cex_orderbook_timer = time.time()
        await mock_cex_pair.update_orderbook(ignore_timer=False)
        mock_fetch.assert_not_awaited()

        # Test with ignore_timer flag
        await mock_cex_pair.update_orderbook(ignore_timer=True)
        mock_fetch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_orderbook_exception(self, mock_cex_pair):
        """Tests exception handling in CexPair.update_orderbook."""
        # Mock to raise exception
        mock_fetch = AsyncMock(side_effect=Exception("API failure"))
        mock_cex_pair.pair.config_manager.ccxt_manager.ccxt_call_fetch_order_book = mock_fetch

        # Mock the error handler handle_async as an AsyncMock
        mock_cex_pair.pair.config_manager.error_handler.handle_async = AsyncMock()

        # Should not propagate exception
        try:
            await mock_cex_pair.update_orderbook(ignore_timer=True)
        except Exception:
            pytest.fail("Unexpected exception propagation")

        # Verify exception was handled
        mock_cex_pair.pair.config_manager.error_handler.handle_async.assert_awaited()
        assert mock_cex_pair.cex_orderbook_timer is None  # Should be reset
