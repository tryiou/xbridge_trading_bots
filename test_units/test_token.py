import os
import sys
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
import yaml

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from definitions.token import Token


@pytest.fixture
def mock_config_manager():
    """Fixture to create a mock ConfigManager."""
    cm = MagicMock()
    cm.strategy_instance = MagicMock()
    cm.general_log = MagicMock()
    cm.xbridge_manager = MagicMock()
    cm.ccxt_manager = MagicMock()
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
        token.config_manager.general_log.error.assert_called()


@pytest.mark.asyncio
async def test_dex_token_request_addr_failure(token):
    """Test DexToken.request_addr on XBridge failure."""
    token.config_manager.xbridge_manager.getnewtokenadress.side_effect = Exception("RPC error")
    with patch.object(token.dex, 'write_address', new_callable=AsyncMock) as mock_write:
        await token.dex.request_addr()
        assert token.dex.address is None
        mock_write.assert_not_awaited()
        token.config_manager.general_log.error.assert_called_with(
            "TEST Error requesting XB address: Exception: RPC error"
        )


# CexToken Tests
@pytest.mark.asyncio
async def test_cex_token_update_price_no_btc_price(token):
    """Test CexToken.update_price when BTC price is unavailable."""
    token.config_manager.tokens['BTC'].cex.usd_price = None
    await token.cex.update_price()
    assert token.cex.usd_price is None
    assert token.cex.cex_price is None
    token.config_manager.general_log.error.assert_called_with(
        "BTC price is None or zero, cannot compute custom price for TEST"
    )


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
            patch('aiohttp.ClientSession.get') as mock_get:
        # Mock aiohttp response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {'BTC': 0.00016}
        async_context_manager = AsyncMock()
        async_context_manager.__aenter__.return_value = mock_response
        mock_get.return_value = async_context_manager

        with patch.object(token.config_manager.ccxt_manager, 'isportopen_sync', return_value=False):
            result = await token.cex.update_block_ticker()
            assert result == 0.00016
            mock_rpc.assert_not_awaited()
            mock_get.assert_called_once()
