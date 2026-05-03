import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import ccxt
import pytest

# Add parent directory to path to resolve the 'definitions' module
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from definitions.ccxt_manager import CCXTManager  # noqa: E402
from definitions.proxy_manager import ProxyManager  # noqa: E402


@pytest.fixture
def mock_config_manager():
    manager = MagicMock()
    manager.ROOT_DIR = "/fake/project/root"
    manager.ccxt_log = MagicMock(spec=logging.Logger)
    manager.general_log = MagicMock(spec=logging.Logger)
    manager.config_ccxt = MagicMock(debug_level=1)
    manager.config_xbridge = MagicMock(taker_fee_block=0.01)
    manager.config_loader.secrets_manager = MagicMock()
    manager.config_loader.secrets_manager.get_api_keys.return_value = {"api_info": []}
    return manager


class TestCCXTManager:
    @pytest.fixture(autouse=True)
    def setup(self, mock_config_manager):
        self.mock_cm = mock_config_manager
        self.manager = CCXTManager(self.mock_cm)

    def test_proxy_manager_register_unregister(self):
        proxy = ProxyManager()
        assert proxy._ref_count == 0
        proxy.register()
        assert proxy._ref_count == 1
        proxy.unregister()
        assert proxy._ref_count == 0

    def test_proxy_manager_cleanup_after_last_unregister(self):
        proxy = ProxyManager()
        proxy.register()
        # Create a fake proxy service instance and thread
        fake_service = MagicMock()
        fake_thread = MagicMock()
        # is_alive is checked before stop and after join.
        # First check should be True to proceed. After join, it should be False for success.
        fake_thread.is_alive.side_effect = [True, False]
        proxy._service_instance = fake_service
        proxy._service_thread = fake_thread

        proxy.unregister()

        fake_service.stop.assert_called_once()
        fake_thread.join.assert_called_once_with(timeout=10.0)

    def test_proxy_manager_logs_warning_on_timeout(self):
        proxy = ProxyManager()
        proxy.register()
        fake_service = MagicMock()
        fake_thread = MagicMock()
        # is_alive is always True, simulating a stuck thread
        fake_thread.is_alive.return_value = True
        proxy._service_instance = fake_service
        proxy._service_thread = fake_thread

        proxy.unregister()

        fake_service.stop.assert_called_once()
        fake_thread.join.assert_called_once_with(timeout=10.0)

    @patch("definitions.ccxt_manager.is_port_open")
    @patch.object(ccxt, "binance")
    def test_init_ccxt_with_private_api(self, mock_binance, mock_is_port_open):
        mock_is_port_open.return_value = True
        mock_exchange = MagicMock()
        mock_binance.return_value = mock_exchange
        self.mock_cm.config_loader.secrets_manager.get_api_keys.return_value = {
            "api_info": [
                {"exchange": "binance", "api_key": "key1", "api_secret": "sec1"}
            ]
        }
        self.manager.init_ccxt_instance("binance", private_api=True)

        mock_binance.assert_called_once_with(
            {
                "apiKey": "key1",
                "secret": "sec1",
                "enableRateLimit": True,
                "rateLimit": 1000,
            }
        )
        self.manager.logger.error.assert_not_called()
        self.mock_cm.ccxt_log.error.assert_not_called()

    @patch("definitions.ccxt_manager.is_port_open")
    @patch.object(ccxt, "binance")
    def test_init_ccxt_with_hostname(self, mock_binance, mock_is_port_open):
        mock_is_port_open.return_value = True
        mock_exchange = MagicMock()
        mock_binance.return_value = mock_exchange

        self.manager.init_ccxt_instance("binance", hostname="global.binance.com")

        self.manager.logger.error.assert_not_called()
        self.mock_cm.ccxt_log.error.assert_not_called()
        # Check binance was initialized correctly with hostname in the config
        mock_binance.assert_called_once_with(
            {
                "apiKey": None,
                "secret": None,
                "enableRateLimit": True,
                "rateLimit": 1000,
                "hostname": "global.binance.com",
            }
        )

    @patch("definitions.ccxt_manager.is_port_open")
    @patch("definitions.ccxt_manager.getattr")
    def test_init_ccxt_failure(self, mock_getattr, mock_is_port_open):
        # Test handling of unsupported exchange
        mock_getattr.side_effect = AttributeError
        instance = self.manager.init_ccxt_instance("fake_exchange")
        assert instance is None
        self.mock_cm.ccxt_log.error.assert_called_once()

    @pytest.mark.asyncio
    @patch(
        "definitions.ccxt_manager.CCXTManager._ccxt_blocking_call_with_retry",
        new_callable=AsyncMock,
    )
    async def test_fetch_order_book_with_rate_limit(self, mock_retry):
        mock_retry.return_value = {"bids": [], "asks": []}
        mock_ccxt = MagicMock()
        # Timer is None initially, so a fetch should be triggered.
        result = await self.manager.ccxt_call_fetch_order_book(mock_ccxt, "BTC/USDT", 5)
        assert result == mock_retry.return_value

    @pytest.mark.asyncio
    @patch(
        "definitions.ccxt_manager.CCXTManager._ccxt_blocking_call_with_retry",
        new_callable=AsyncMock,
    )
    async def test_fetch_free_balance(self, mock_retry):
        mock_retry.return_value = {"BTC": 1.5}
        mock_ccxt = MagicMock()
        result = await self.manager.ccxt_call_fetch_free_balance(mock_ccxt)
        assert result == mock_retry.return_value

    @pytest.mark.asyncio
    @patch("definitions.ccxt_manager.rpc_call", new_callable=AsyncMock)
    async def test_fetch_tickers_with_proxy(self, mock_rpc_call):
        mock_rpc_call.return_value = {}
        mock_ccxt = MagicMock()
        # Mock proxy_manager.ensure_running to avoid actually starting proxy
        with patch.object(self.manager.proxy_manager, "ensure_running"), \
             patch("definitions.ccxt_manager.is_port_open", return_value=True):
            await self.manager.ccxt_call_fetch_tickers(mock_ccxt, ["BTC/USDT"])
            mock_rpc_call.assert_awaited_once()

    def test_start_proxy_handles_process_creation_failure(self):
        proxy = ProxyManager()
        with patch(
                "definitions.proxy_manager.AsyncPriceService",
                side_effect=OSError("Process error"),
        ):
            proxy.ensure_running()
            # Verify the proxy process is set to None after failure
            assert proxy._service_instance is None
            assert proxy._service_thread is None

    @patch("definitions.ccxt_manager.logging.Formatter")
    @patch("definitions.ccxt_manager.logging.StreamHandler")
    def test_debug_display_logging_levels(self, mock_stream, mock_formatter):
        test_cases = [
            (1, [], 0, 0),  # Level 1: no logging
            (2, ["function_name", "params"], 1, 0),  # Level 2: info log
            (3, ["function_name", "params"], 1, 0),  # Level 3: info with params
            (4, ["function_name", "params"], 1, 1),  # Level 4: info + debug
        ]

        for level, params, expected_info_calls, expected_debug_calls in test_cases:
            self.mock_cm.config_ccxt.debug_level = level
            self.manager._debug_display("test_function", params, "result")
            assert self.mock_cm.ccxt_log.info.call_count == expected_info_calls
            assert self.mock_cm.ccxt_log.debug.call_count == expected_debug_calls
            self.mock_cm.ccxt_log.reset_mock()

    @pytest.mark.asyncio
    @patch("asyncio.get_running_loop")
    async def test_ccxt_blocking_retry(self, mock_loop):
        # Setup a mock function that fails then succeeds
        future1 = asyncio.Future()
        future1.set_exception(Exception("Transient"))
        future2 = asyncio.Future()
        future2.set_result("Success")
        mock_loop.return_value.run_in_executor.side_effect = [future1, future2]
        # Setup error handler to allow one retry
        self.manager.error_handler.handle_async = AsyncMock(return_value=True)

        result = await self.manager._ccxt_blocking_call_with_retry(
            lambda: None, {}, "param"
        )
        assert result == "Success"
        self.manager.error_handler.handle_async.assert_awaited_once()
