import asyncio
import os
import sys
import threading
from unittest.mock import MagicMock, AsyncMock, patch, create_autospec, call

import pytest

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from definitions.token import CexToken, DexToken, Token
from definitions.starter import (
    TradingProcessor,
    BalanceManager,
    PriceHandler,
    MainController,
    run_async_main,
)
from definitions.errors import RPCConfigError


@pytest.fixture
def mock_config_manager():
    """Fixture for a mock ConfigManager with necessary attributes."""
    cm = MagicMock()

    # Create more structured token mocks
    t1_mock = MagicMock(symbol='T1')
    t1_mock.dex = create_autospec(DexToken, instance=True, total_balance=None, free_balance=None)
    t1_mock.dex.enabled = True
    t1_mock.dex.read_address = AsyncMock()
    t2_mock = MagicMock(symbol='T2')
    t2_mock.dex = create_autospec(DexToken, instance=True, total_balance=None, free_balance=None)
    t2_mock.dex.enabled = True
    t2_mock.dex.read_address = AsyncMock()

    # Create structured pair mocks
    pair1_mock = MagicMock(disabled=False)
    pair1_mock.cex = MagicMock()
    pair1_mock.cex.update_pricing = AsyncMock()
    pair2_mock = MagicMock(disabled=True)
    pair2_mock.cex = MagicMock()
    pair2_mock.cex.update_pricing = AsyncMock()

    cm.pairs = {'pair1': pair1_mock, 'pair2': pair2_mock}
    cm.tokens = {'T1': t1_mock, 'T2': t2_mock}
    cm.config_xbridge.max_concurrent_tasks = 2
    cm.strategy_instance = MagicMock()
    cm.strategy_instance.should_update_cex_prices.return_value = True
    cm.error_handler = MagicMock()
    cm.general_log = MagicMock()
    cm.xbridge_manager = AsyncMock()
    cm.ccxt_manager = AsyncMock()
    cm.resource_lock = threading.RLock()  # Mock lock for async context
    cm.controller = None
    return cm


@pytest.fixture
def mock_main_controller(mock_config_manager):
    """Fixture for a mock MainController."""
    loop = asyncio.get_event_loop()
    controller = MainController(mock_config_manager, loop)
    # Patch the sub-managers to prevent real operations
    controller.price_handler = AsyncMock()
    controller.balance_manager = AsyncMock()
    controller.processor = AsyncMock()
    mock_config_manager.controller = controller
    return controller




@pytest.mark.asyncio
async def test_balance_manager_update(mock_config_manager):
    """Tests BalanceManager balance update logic."""
    mock_config_manager.xbridge_manager.getlocaltokens.return_value = {'T1': {}}
    mock_config_manager.xbridge_manager.gettokenutxo.return_value = [
        {'amount': '10.0', 'orderid': ''},
        {'amount': '5.0', 'orderid': 'some_id'}
    ]
    balance_manager = BalanceManager(mock_config_manager.tokens, mock_config_manager, asyncio.get_event_loop())

    await balance_manager.update_balances()

    token_t1 = mock_config_manager.tokens['T1']
    assert token_t1.dex.total_balance == 15.0
    assert token_t1.dex.free_balance == 10.0


@pytest.mark.asyncio
async def test_price_handler_update(mock_config_manager):
    """Tests PriceHandler price update logic."""
    mock_main_controller = MagicMock()
    btc_mock = MagicMock(symbol='BTC')
    btc_mock.cex = create_autospec(CexToken, instance=True, usd_price=None, cex_price=None)
    ltc_mock = MagicMock(symbol='LTC')
    ltc_mock.cex = create_autospec(CexToken, instance=True, usd_price=None, cex_price=None)
    mock_main_controller.tokens_dict = {
        'BTC': btc_mock,
        'LTC': ltc_mock,
    }
    mock_main_controller.config_manager = mock_config_manager
    mock_main_controller.ccxt_i = mock_config_manager.my_ccxt
    mock_main_controller.shutdown_event = asyncio.Event()

    mock_config_manager.ccxt_manager.ccxt_call_fetch_tickers.return_value = {
        'BTC/USDT': {'info': {'lastPrice': '50000'}},
        'LTC/BTC': {'info': {'lastPrice': '0.003'}}
    }
    mock_config_manager.config_coins.usd_ticker_custom = MagicMock(spec=object)
    mock_config_manager.my_ccxt.id = 'binance'
    mock_config_manager.my_ccxt.symbols = ['BTC/USDT', 'LTC/BTC']

    price_handler = PriceHandler(mock_main_controller, asyncio.get_event_loop())

    await price_handler.update_ccxt_prices()

    btc_token = mock_main_controller.tokens_dict['BTC']
    ltc_token = mock_main_controller.tokens_dict['LTC']
    assert btc_token.cex.usd_price == 50000.0
    assert btc_token.cex.cex_price == 1
    assert ltc_token.cex.cex_price == 0.003
    assert ltc_token.cex.usd_price == 0.003 * 50000.0


@pytest.mark.asyncio
async def test_main_controller_loops(mock_main_controller):
    """Tests that MainController's init and main loops call sub-components."""
    await mock_main_controller.main_init_loop()
    mock_main_controller.balance_manager.update_balances.assert_awaited_once()
    mock_main_controller.price_handler.update_ccxt_prices.assert_awaited_once()
    mock_main_controller.processor.process_pairs.assert_awaited_once()

    mock_main_controller.balance_manager.update_balances.reset_mock()
    mock_main_controller.price_handler.update_ccxt_prices.reset_mock()
    mock_main_controller.processor.process_pairs.reset_mock()

    await mock_main_controller.main_loop()
    mock_main_controller.balance_manager.update_balances.assert_awaited_once()
    mock_main_controller.price_handler.update_ccxt_prices.assert_awaited_once()
    mock_main_controller.processor.process_pairs.assert_awaited_once()


@patch('definitions.starter.main', new_callable=AsyncMock)
@patch('definitions.starter.MainController')
def test_run_async_main_rpc_error(MockController, mock_main, mock_config_manager):
    """Tests that RPCConfigError during init is caught and logged."""
    MockController.side_effect = RPCConfigError("Test RPC Error")

    with pytest.raises(RPCConfigError):
        run_async_main(mock_config_manager)

    mock_config_manager.general_log.critical.assert_called_once()
    assert "Fatal RPC configuration error" in mock_config_manager.general_log.critical.call_args[0][0]


@pytest.mark.asyncio
async def test_trading_processor_async(mock_config_manager):
    """Test TradingProcessor handles async callbacks correctly."""
    mock_controller = MagicMock()
    mock_controller.pairs_dict = {
        'pair1': MagicMock(disabled=False),
        'pair2': MagicMock(disabled=False)
    }
    mock_controller.shutdown_event = asyncio.Event()
    mock_controller.loop = asyncio.get_running_loop()
    processor = TradingProcessor(mock_controller)
    
    async_mock = AsyncMock()
    await processor.process_pairs(async_mock)
    
    # Check call counts and arguments
    assert async_mock.await_count == 2
    async_mock.assert_has_calls([
        call(mock_controller.pairs_dict['pair1']),
        call(mock_controller.pairs_dict['pair2'])
    ], any_order=True)

@pytest.mark.asyncio
async def test_trading_processor_sync(mock_config_manager):
    """Test TradingProcessor handles sync callbacks correctly."""
    mock_controller = MagicMock()
    mock_controller.pairs_dict = {
        'pair1': MagicMock(disabled=False)
    }
    mock_controller.shutdown_event = asyncio.Event()
    # Create a completed future to return
    future = asyncio.Future()
    future.set_result(None)
    mock_controller.loop = MagicMock()
    mock_controller.loop.run_in_executor = MagicMock(return_value=future)
    processor = TradingProcessor(mock_controller)
    
    sync_mock = MagicMock()
    await processor.process_pairs(sync_mock)
    
    # Verify thread pool executor was used
    assert mock_controller.loop.run_in_executor.call_count == 1
    # Verify call arguments
    mock_controller.loop.run_in_executor.assert_called_once_with(None, sync_mock, mock_controller.pairs_dict['pair1'])

@pytest.mark.asyncio
async def test_price_handler_custom_coin(mock_config_manager):
    """Test PriceHandler handles custom coins correctly."""
    # Create mock objects with necessary structure
    usd_ticker_custom = type('', (), {})()  # Create an empty object
    setattr(usd_ticker_custom, 'TEST', {})  # Add TEST attribute
    mock_config_manager.config_coins.usd_ticker_custom = usd_ticker_custom
    # Ensure strategy requires price updates
    mock_config_manager.strategy_instance.should_update_cex_prices.return_value = True
        
    mock_controller = MagicMock()
    mock_controller.config_manager = mock_config_manager
        
    # Create token with async update_price
    token_mock = MagicMock()
    token_mock.cex.update_price = AsyncMock()
    mock_controller.tokens_dict = {'TEST': token_mock}
    mock_controller.shutdown_event = asyncio.Event()  # Add shutdown_event
        
    price_handler = PriceHandler(mock_controller, asyncio.get_event_loop())
    price_handler.ccxt_price_timer = 0  # Force update
        
    await price_handler.update_ccxt_prices()
    token_mock.cex.update_price.assert_awaited_once()

@pytest.mark.asyncio
async def test_balance_manager_token_not_in_xb_tokens(mock_config_manager):
    """Test BalanceManager resets balances when token isn't in xb_tokens."""
    mock_config_manager.xbridge_manager.getlocaltokens.return_value = ['BTC']
    token = Token('ETH', 'test') 
    token.dex.total_balance = 10
    token.dex.free_balance = 5
    
    balance_manager = BalanceManager(
        {'ETH': token}, mock_config_manager, asyncio.get_event_loop()
    )
    await balance_manager.update_balances()
    
    assert token.dex.total_balance is None
    assert token.dex.free_balance is None

@pytest.mark.asyncio
async def test_main_controller_thread_init_blocking(mock_config_manager):
    """Test blocking initialization in MainController."""
    controller = MainController(mock_config_manager, asyncio.get_event_loop())
    mock_config_manager.strategy_instance.thread_loop_blocking_action = MagicMock()
    pair = MagicMock()
    controller.pairs_dict = {'pair1': pair}
    
    controller.thread_init_blocking(pair)
    mock_config_manager.strategy_instance.thread_loop_blocking_action.assert_called_once_with(pair)

@pytest.mark.asyncio
async def test_main_controller_close_session(mock_config_manager):
    """Test HTTP session closure logic."""
    controller = MainController(mock_config_manager, asyncio.get_event_loop())
    # Make sure to store session before closing
    session = AsyncMock()
    session.closed = False  # Mark session as open
    controller.http_session = session
    controller._http_session_owner = True
    
    await controller.close_http_session()
    session.close.assert_awaited_once()
    assert controller.http_session is None
