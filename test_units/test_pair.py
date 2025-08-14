import os
import sys
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch, mock_open

import pytest
import yaml

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from definitions.pair import DexPair, Pair, CexPair
from definitions.token import Token, CexToken, DexToken


@pytest.fixture
def mock_pair():
    """Fixture to create a mock Pair instance for DexPair testing."""
    token1 = create_autospec(Token, instance=True)
    token1.symbol = 'T1'
    token1.cex = create_autospec(CexToken, instance=True)
    token1.dex = create_autospec(DexToken, instance=True)
    token1.cex.cex_price = 1.0
    token1.dex.address = 't1_addr'

    token2 = create_autospec(Token, instance=True)
    token2.symbol = 'T2'
    token2.cex = create_autospec(CexToken, instance=True)
    token2.dex = create_autospec(DexToken, instance=True)
    token2.cex.cex_price = 0.1
    token2.dex.address = 't2_addr'

    config_manager = MagicMock()
    config_manager.strategy_instance = MagicMock()
    config_manager.general_log = MagicMock()
    config_manager.error_handler = AsyncMock()
    config_manager.controller = None

    pair = MagicMock(spec=Pair)
    pair.t1 = token1
    pair.t2 = token2
    pair.name = 'T1_T2_pair'
    pair.symbol = 'T1/T2'
    pair.config_manager = config_manager
    pair.cex = MagicMock()
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


def test_dex_create_order_insufficient_balance(dex_pair):
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

    # Use explicit event loop to avoid pytest-asyncio teardown issues
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(dex_pair.create_order())
    finally:
        loop.close()

    mock_makeorder.assert_not_called()
    dex_pair.pair.config_manager.general_log.error.assert_called()
    assert "balance too low" in dex_pair.pair.config_manager.general_log.error.call_args[0][0]


def test_dex_create_order_xb_error(dex_pair):
    """Tests that _handle_order_error is called when makeorder returns an error."""
    # Create an AsyncMock that returns an error response
    mock_makeorder = AsyncMock(return_value={'error': 'Failed to make order', 'code': 1001})

    # Create a coroutine function that immediately returns None for async_notify_user
    async def async_notify_user_mock(*args, **kwargs):
        return None

    strategy_mock = dex_pair.pair.config_manager.strategy_instance
    strategy_mock.calculate_sell_price.return_value = 10.0
    strategy_mock.build_sell_order_details.return_value = (1.0, 0.01)

    dex_pair.create_virtual_sell_order()
    dex_pair.t1.dex.free_balance = 2.0  # Sufficient balance

    dex_pair.pair.config_manager.xbridge_manager.makeorder = mock_makeorder
    
    # Fix mock config_manager.async_notify_user
    dex_pair.pair.config_manager.async_notify_user = async_notify_user_mock

    strategy_handle_error_mock = MagicMock()
    dex_pair.pair.config_manager.strategy_instance.handle_order_status_error = strategy_handle_error_mock

    # Use explicit event loop to avoid pytest-asyncio teardown issues
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(dex_pair.create_order())
    finally:
        loop.close()

    # Verify order was created with error
    assert dex_pair.order == {'error': 'Failed to make order', 'code': 1001}
    assert dex_pair.disabled is True
    strategy_handle_error_mock.assert_called_once_with(dex_pair)
    dex_pair.pair.config_manager.general_log.error.assert_called()


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
        dex_pair.pair.config_manager.general_log.error.assert_called()


class TestCexPair:
    @pytest.fixture
    def mock_cex_pair(self, mock_pair):
        """Fixture to create a CexPair instance."""
        return CexPair(mock_pair)

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
