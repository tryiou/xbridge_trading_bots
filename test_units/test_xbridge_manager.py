import asyncio
import os
import sys
import time
from unittest.mock import MagicMock, AsyncMock, patch, mock_open

import pytest

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from definitions.xbridge_manager import XBridgeManager


@pytest.fixture(autouse=True)
def reset_xbridge_manager_class_vars():
    XBridgeManager._active_rpc_counter = 0
    XBridgeManager._rpc_semaphore = None
    yield


@pytest.fixture
def mock_config_manager():
    """Fixture for a mock ConfigManager."""
    cm = MagicMock()
    cm.strategy = "test_strat"
    cm.config_xbridge.debug_level = 0
    cm.config_xbridge.max_concurrent_tasks = 2  # Lower for testing
    cm.general_log = MagicMock()
    cm.controller = None
    return cm


@pytest.fixture
def xbridge_manager(mock_config_manager):
    """Fixture to create an XBridgeManager instance with mocked dependencies."""
    with patch('definitions.xbridge_manager.detect_rpc', return_value=("user", 1234, "pass", "/tmp")), \
         patch('definitions.xbridge_manager.is_port_open', return_value=True), \
         patch('asyncio.run'), \
         patch('definitions.xbridge_manager.rpc_call', new_callable=AsyncMock) as mock_rpc_call:
        manager = XBridgeManager(mock_config_manager)
        manager.mock_rpc_call = mock_rpc_call  # Attach mock for easy access in tests
        yield manager


@pytest.mark.asyncio
async def test_gettokenutxo_caching(xbridge_manager):
    """Tests the caching logic of gettokenutxo."""
    token = "BLOCK"
    mock_utxos = [{"txid": "123", "amount": 100}]
    xbridge_manager.mock_rpc_call.return_value = mock_utxos

    # 1. First call (cache miss)
    result1 = await xbridge_manager.gettokenutxo(token)
    assert result1 == mock_utxos
    xbridge_manager.mock_rpc_call.assert_called_once()
    assert xbridge_manager.mock_rpc_call.call_args.kwargs['method'] == 'dxgetutxos'

    # 2. Second call immediately after (cache hit)
    xbridge_manager.mock_rpc_call.reset_mock()
    result2 = await xbridge_manager.gettokenutxo(token)
    assert result2 == mock_utxos
    xbridge_manager.mock_rpc_call.assert_not_called()

    # 3. Third call after cache duration (cache miss)
    xbridge_manager.mock_rpc_call.reset_mock()
    # Manually expire cache for test reliability
    with patch('time.time', return_value=time.time() + xbridge_manager.UTXO_CACHE_DURATION + 1):
        result3 = await xbridge_manager.gettokenutxo(token)
        assert result3 == mock_utxos
        xbridge_manager.mock_rpc_call.assert_called_once()
        assert xbridge_manager.mock_rpc_call.call_args.kwargs['method'] == 'dxgetutxos'


@pytest.mark.asyncio
async def test_rpc_wrapper_concurrency_and_counter(xbridge_manager):
    """Tests that rpc_wrapper respects concurrency and updates the active RPC counter."""
    manager = xbridge_manager
    method = "testmethod"
    params = []
    concurrency_limit = manager.config_manager.config_xbridge.max_concurrent_tasks
    active_calls = 0
    max_active_calls = 0

    async def delayed_rpc(*args, **kwargs):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        assert manager.active_rpc_counter > 0
        await asyncio.sleep(0.05)  # Simulate network latency
        active_calls -= 1
        return "success"

    manager.mock_rpc_call.side_effect = delayed_rpc

    # Start more tasks than the concurrency limit
    tasks = [manager.rpc_wrapper(method, params) for _ in range(concurrency_limit * 2)]
    results = await asyncio.gather(*tasks)

    assert all(r == "success" for r in results)
    assert max_active_calls == concurrency_limit
    assert manager.active_rpc_counter == 0


@pytest.mark.asyncio
async def test_makeorder_dryrun(xbridge_manager):
    """Tests that makeorder calls rpc_wrapper with the correct 'dryrun' parameter."""
    manager = xbridge_manager
    params = ["MAKER", "1.0", "m_addr", "TAKER", "10.0", "t_addr"]

    # Test with dryrun=True
    await manager.makeorder(*params, dryrun=True)
    manager.mock_rpc_call.assert_called_once()
    call_kwargs = manager.mock_rpc_call.call_args.kwargs
    assert call_kwargs['method'] == 'dxMakeOrder'
    assert call_kwargs['params'][-1] == 'dryrun'

    # Test with dryrun=False (or None)
    manager.mock_rpc_call.reset_mock()
    await manager.makeorder(*params, dryrun=False)
    manager.mock_rpc_call.assert_called_once()
    call_kwargs = manager.mock_rpc_call.call_args.kwargs
    assert call_kwargs['method'] == 'dxMakeOrder'
    assert call_kwargs['params'][-1] != 'dryrun'


MOCK_XBRIDGE_CONF = """
[Main]
ExchangeWallets=BLOCK,LTC

[BLOCK]
Title=Blocknet
Address=
Ip=127.0.0.1
Port=41474
Username=testuser
Password=testpass
coin=100000000
feeperbyte=20
mintxfee=10000

[LTC]
Title=Litecoin
Address=
Ip=127.0.0.1
Port=9332
Username=testuser
Password=testpass
coin=100000000
feeperbyte=10
mintxfee=20000
"""


def test_parse_xbridge_conf_success(xbridge_manager):
    """Tests successful parsing of a mock xbridge.conf file."""
    manager = xbridge_manager
    # Set a mock datadir path
    manager.blocknet_datadir_path = "/mock/datadir"

    with patch('builtins.open', mock_open(read_data=MOCK_XBRIDGE_CONF)), \
         patch('os.path.exists', return_value=True):
        manager.parse_xbridge_conf()

        assert manager.xbridge_conf is not None
        assert "BLOCK" in manager.xbridge_conf
        assert "LTC" in manager.xbridge_conf
        assert "Main" not in manager.xbridge_conf  # Should be skipped

        # Verify type conversion
        assert isinstance(manager.xbridge_conf['BLOCK']['feeperbyte'], int)
        assert manager.xbridge_conf['BLOCK']['feeperbyte'] == 20
        assert manager.xbridge_conf['LTC']['mintxfee'] == 20000


def test_parse_xbridge_conf_file_not_found(xbridge_manager):
    """Tests behavior when xbridge.conf is not found."""
    manager = xbridge_manager
    manager.blocknet_datadir_path = "/mock/datadir"
    # Replace the real logger with a mock to assert calls
    manager.logger = MagicMock()

    with patch('os.path.exists', return_value=False):
        manager.parse_xbridge_conf()
        assert manager.xbridge_conf is None
        manager.logger.error.assert_called_with("xbridge.conf not found at /mock/datadir/xbridge.conf")


def test_calculate_xbridge_fees(xbridge_manager):
    """Tests the fee estimation logic."""
    manager = xbridge_manager
    # Manually set the parsed conf
    manager.xbridge_conf = {
        'BLOCK': {'feeperbyte': 20, 'mintxfee': 10000, 'coin': 100000000},
        'LTC': {'feeperbyte': 10, 'mintxfee': 20000, 'coin': 100000000}
    }

    manager.calculate_xbridge_fees()

    assert "BLOCK" in manager.xbridge_fees_estimate
    assert "LTC" in manager.xbridge_fees_estimate

    # BLOCK fee: feeperbyte * 500 = 10000. This is equal to mintxfee.
    assert manager.xbridge_fees_estimate['BLOCK']['estimated_fee_satoshis'] == 10000
    assert manager.xbridge_fees_estimate['BLOCK']['estimated_fee_coin'] == 0.0001

    # LTC fee: feeperbyte * 500 = 5000. This is less than mintxfee.
    assert manager.xbridge_fees_estimate['LTC']['estimated_fee_satoshis'] == 20000
    assert manager.xbridge_fees_estimate['LTC']['estimated_fee_coin'] == 0.0002
