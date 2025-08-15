import asyncio
import os
import sys
from unittest.mock import MagicMock, AsyncMock, PropertyMock, patch

import pytest

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import threading

from definitions.shutdown import wait_for_pending_rpcs, ShutdownCoordinator
from strategies.maker_strategy import MakerStrategy


@pytest.mark.asyncio
async def test_wait_for_pending_rpcs_completes():
    """Tests that wait_for_pending_rpcs exits when the counter reaches zero."""
    config_manager = MagicMock()
    config_manager.general_log = MagicMock()
    # Simulate counter dropping to 0 after two checks
    p = PropertyMock(side_effect=[2, 1, 0])
    type(config_manager.xbridge_manager).active_rpc_counter = p

    await wait_for_pending_rpcs(config_manager, timeout=5)

    assert p.call_count == 3


@pytest.mark.asyncio
async def test_wait_for_pending_rpcs_times_out():
    """Tests that wait_for_pending_rpcs times out if the counter never reaches zero."""
    config_manager = MagicMock()
    config_manager.general_log = MagicMock()
    # Counter never reaches zero
    p = PropertyMock(return_value=1)
    type(config_manager.xbridge_manager).active_rpc_counter = p

    # Use a short timeout for the test
    await wait_for_pending_rpcs(config_manager, timeout=1.5)

    # The warning should be logged on timeout
    config_manager.general_log.warning.assert_called_once()


@pytest.mark.asyncio
async def test_unified_shutdown_sequence():
    """Tests the sequence of operations in ShutdownCoordinator.unified_shutdown."""
    # Arrange
    mock_cm = MagicMock()
    mock_cm.resource_lock = threading.RLock()
    mock_cm.general_log = MagicMock()
    mock_cm.error_handler.handle_async = AsyncMock()

    # Mock controller and its shutdown event
    mock_cm.controller = MagicMock()
    mock_cm.controller.shutdown_event = asyncio.Event()

    # Mock strategy instance as a MakerStrategy to test order cancellation path
    mock_cm.strategy_instance = MagicMock(spec=MakerStrategy)
    mock_cm.strategy_instance.cancel_own_orders = AsyncMock(return_value=1)

    # Mock http_session
    mock_cm.http_session = AsyncMock()
    mock_cm.http_session.close = AsyncMock()

    # Patch wait_for pending RPCs
    with patch('definitions.shutdown.wait_for_pending_rpcs', new_callable=AsyncMock) as mock_wait_rpc:
        # Act
        await ShutdownCoordinator.unified_shutdown(mock_cm)

        # Assert
        # 1. Waited for pending RPCs
        mock_wait_rpc.assert_awaited_once_with(mock_cm, timeout=30)

        # 2. Cancelled strategy orders
        mock_cm.strategy_instance.cancel_own_orders.assert_awaited_once()
