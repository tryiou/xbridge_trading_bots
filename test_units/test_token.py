import os
import sys
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
import yaml

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from definitions.errors import OperationalError
from definitions.token import Token


@pytest.fixture
def mock_config_manager():
    """Fixture to create a mock ConfigManager."""
    cm = MagicMock()
    cm.strategy_instance = MagicMock()
    cm.general_log = MagicMock()
    cm.xbridge_manager = MagicMock()
    cm.ccxt_manager = MagicMock()
    cm.error_handler = MagicMock()
    cm.error_handler.handle_async = AsyncMock()
    cm.tokens = {'BTC': MagicMock(cex=MagicMock(usd_price=50000.0))}
    cm.config_coins = MagicMock()
    # spec=object prevents hasattr from returning True for arbitrary attributes
    cm.config_coins.usd_ticker_custom = MagicMock(spec=object)
    return cm


@pytest.fixture
def token(mock_config_manager):
    """Fixture to create a Token instance."""
    return Token('TEST', 'test_strategy', config_manager=mock_config_manager)


# DexToken Tests
@pytest.mark.asyncio
async def test_dex_token_read_address_file_not_found(token):
    """Test DexToken.read_address when address file is missing."""
    with patch('builtins.open', side_effect=FileNotFoundError):
        token.dex.request_addr = AsyncMock()
        await token.dex.read_address()
        token.dex.request_addr.assert_awaited_once()
        token.config_manager.general_log.info.assert_called()


@pytest.mark.asyncio
async def test_dex_token_read_address_malformed_yaml(token):
    """Test DexToken.read_address when address file is malformed."""
    with patch('builtins.open'), patch('yaml.safe_load', side_effect=yaml.YAMLError):
        token.dex.request_addr = AsyncMock()
        await token.dex.read_address()
        token.dex.request_addr.assert_awaited_once()
        token.config_manager.error_handler.handle_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_dex_token_request_addr_failure(token):
    """Test DexToken.request_addr on XBridge failure."""
    token.config_manager.xbridge_manager.getnewtokenadress.side_effect = Exception("RPC error")
    with patch.object(token.dex, 'write_address', new_callable=AsyncMock) as mock_write:
        await token.dex.request_addr()
        assert token.dex.address is None
        mock_write.assert_not_awaited()
        token.config_manager.error_handler.handle_async.assert_awaited_once()


# Tests for DexToken.write_address
@pytest.mark.asyncio
async def test_dex_token_write_address_success(token):
    """Test DexToken.write_address success case."""
    token.dex.address = "new_address"
    with patch('builtins.open', create=True), patch('yaml.safe_dump') as mock_dump:
        await token.dex.write_address()
        mock_dump.assert_called_once()
        token.config_manager.error_handler.handle_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_dex_token_write_address_failure(token):
    """Test DexToken.write_address failure case."""
    token.dex.address = "new_address"
    with patch('builtins.open', side_effect=IOError("Disk error")):
        await token.dex.write_address()
        token.config_manager.error_handler.handle_async.assert_awaited_once()


# CexToken Tests
@pytest.mark.asyncio
async def test_cex_token_update_price_no_btc_price(token):
    """Test CexToken.update_price when BTC price is unavailable."""
    token.config_manager.tokens['BTC'].cex.usd_price = None
    await token.cex.update_price()
    assert token.cex.usd_price is None
    assert token.cex.cex_price is None
    # Check that the error handler was called with OperationalError
    token.config_manager.error_handler.handle_async.assert_awaited_once()
    # Get the call arguments
    args, kwargs = token.config_manager.error_handler.handle_async.call_args
    # Check OperationalError message
    assert isinstance(args[0], OperationalError)
    assert args[0].args[0] == "BTC price unavailable for TEST price calculation"
    # Verify context was set
    assert kwargs == {'context': {'token': 'TEST'}}


@pytest.mark.asyncio
async def test_cex_token_update_price_custom_ticker(token):
    """Test CexToken.update_price using a custom ticker from config."""
    token.config_manager.config_coins.usd_ticker_custom.TEST = 0.5
    await token.cex.update_price()

    assert token.cex.usd_price == 0.5
    assert token.cex.cex_price == pytest.approx(0.5 / 50000.0)


@pytest.mark.asyncio
async def test_cex_token_update_price_api_failure(token):
    """Test CexToken.update_price when API call fails repeatedly."""
    token.config_manager.my_ccxt.symbols = ['TEST/BTC']
    # Mock `ccxt_call_fetch_ticker` to simulate a persistent failure.
    token.config_manager.ccxt_manager.ccxt_call_fetch_ticker = AsyncMock(side_effect=Exception("API Error"))

    # Invalidate timer to ensure fetch is attempted
    token.cex.cex_price_timer = None

    await token.cex.update_price()

    assert token.cex.usd_price is None
    assert token.cex.cex_price is None


@pytest.mark.asyncio
async def test_cex_token_update_block_ticker(token):
    """Test CexToken.update_block_ticker with proxy and fallback."""
    token.symbol = 'BLOCK'  # Override for this test

    # Test 1: Proxy is available and returns a value
    with patch('definitions.token.rpc_call', new_callable=AsyncMock) as mock_rpc, \
            patch('aiohttp.ClientSession.get') as mock_get:
        mock_rpc.return_value = 0.00015
        with patch.object(token.config_manager.ccxt_manager, 'isportopen_sync', return_value=True):
            result = await token.cex.update_block_ticker()
            assert result == 0.00015
            mock_rpc.assert_awaited_once()
            mock_get.assert_not_called()

    # Test 2: Proxy is not available, fallback to cryptocompare
    with patch('definitions.token.rpc_call', new_callable=AsyncMock) as mock_rpc, \
            patch('aiohttp.ClientSession.get', new_callable=MagicMock) as mock_get:
        # Mock aiohttp response with AsyncMock for json()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={'BTC': 0.00016})
        mock_get.return_value.__aenter__.return_value = mock_response

        with patch.object(token.config_manager.ccxt_manager, 'isportopen_sync', return_value=False):
            result = await token.cex.update_block_ticker()
            assert result == 0.00016
            mock_rpc.assert_not_awaited()
            mock_get.assert_called_once()

    # Test 3: Both proxy and cryptocompare fail
    with patch('definitions.token.rpc_call', new_callable=AsyncMock) as mock_rpc, \
            patch('aiohttp.ClientSession.get', new_callable=MagicMock) as mock_get:
        mock_rpc.side_effect = Exception("Proxy error")
        mock_get.side_effect = Exception("Network fail")
        with patch.object(token.config_manager.ccxt_manager, 'isportopen_sync', return_value=False):
            result = await token.cex.update_block_ticker()
            assert result is None

    # Test 4: Proxy available but returns invalid value
    with patch('definitions.token.rpc_call', new_callable=AsyncMock) as mock_rpc, \
            patch('aiohttp.ClientSession.get') as mock_get:
        mock_rpc.return_value = "invalid_price"
        with patch.object(token.config_manager.ccxt_manager, 'isportopen_sync', return_value=True):
            result = await token.cex.update_block_ticker()
            assert result is None


@pytest.mark.asyncio
async def test_cex_token_update_price_exchange_success(token):
    """Test CexToken.update_price for non-BTC token with exchange ticker returning valid price."""
    token.symbol = 'TEST'
    token.config_manager.my_ccxt.symbols = ['TEST/BTC']
    token.config_manager.tokens['BTC'].cex.usd_price = 50000.0

    # Mock the ccxt_manager's fetch_ticker method to return a valid ticker
    mock_ticker = {
        'info': {
            'lastTradeRate': '0.0002'
        }
    }
    token.config_manager.ccxt_manager.ccxt_call_fetch_ticker = AsyncMock(return_value=mock_ticker)

    await token.cex.update_price()

    assert token.cex.cex_price == 0.0002
    assert token.cex.usd_price == 0.0002 * 50000.0


@pytest.mark.asyncio
async def test_cex_token_update_price_btc(token):
    """Test CexToken.update_price when updating the token is BTC."""
    token.symbol = 'BTC'
    # Set the BTC token's USD price to a non-None value
    token.config_manager.tokens['BTC'].cex.usd_price = 50000.0

    # We don't expect any external calls for BTC since it's handled as a special case
    token.config_manager.ccxt_manager.ccxt_call_fetch_ticker = AsyncMock()

    await token.cex.update_price()

    # For BTC, cex_price should be 1 and usd_price should be the BTC token's USD price (50000.0)
    assert token.cex.cex_price == 1.0
    assert token.cex.usd_price == 50000.0
    token.config_manager.ccxt_manager.ccxt_call_fetch_ticker.assert_not_awaited()


# Token class property tests
def test_token_properties(token):
    """Test Token class property getters."""
    # Set values on DexToken and CexToken
    token.dex.free_balance = 10.0
    token.dex.total_balance = 15.0
    token.cex.usd_price = 25.5

    assert token.dex_free_balance == 10.0
    assert token.dex_total_balance == 15.0
    assert token.cex_usd_price == 25.5
