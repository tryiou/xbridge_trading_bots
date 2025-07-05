### ADD TEST HERE
import asyncio
import os
import sys
import traceback
import unittest.mock
from typing import List, Dict, Any
from unittest.mock import patch, AsyncMock, MagicMock

import aiohttp
import ccxt
import pytest

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from definitions.bcolors import bcolors
from proxy_ccxt import PriceFetcher, WebServer, async_retry


import unittest

class ProxyCCXTTester:
    """
    A dedicated class to test the CCXT proxy server v2 components,
    following the project's custom tester pattern.
    """

    def __init__(self):
        self.test_results: List[Dict[str, Any]] = []

    async def run_all_tests(self):
        """Runs the full suite of tests."""
        print("--- Starting Proxy CCXT Test Suite ---")
        try:
            await self.test_price_fetcher_initialization()
            await self.test_ccxt_ticker_fetching()
            await self.test_block_ticker_fetching()
            await self.test_webserver_request_handling()
            await self.test_async_retry_decorator()
            await self.test_periodic_refreshing()
            await self.test_invalid_symbol_handling()
            await self.test_ticker_refresh_lock()
            await self.test_block_ticker_error_handling()
            await self.test_grouped_ticker_refresh()
            await self.test_rate_limiting_exception()
            await self.test_invalid_api_response_handling()
            await self.test_network_failure_scenarios()
            await self.test_authentication_error_handling()
            await self.test_invalid_exchange_configuration()
        except Exception as e:
            print(f"A critical error occurred during the test suite run: {e}")
            traceback.print_exc()
        finally:
            print("\n--- Proxy CCXT Test Suite Finished ---")
            self._print_summary()

    def _print_summary(self):
        """Prints a formatted summary of the test suite results."""
        summary_lines = [
            "\n" + "=" * 60,
            "--- Test Suite Summary ---".center(60),
            "=" * 60
        ]
        passed_count = 0
        failed_count = 0

        for result in self.test_results:
            status = f"{bcolors.OKGREEN}PASSED{bcolors.ENDC}" if result[
                'passed'] else f"{bcolors.FAIL}FAILED{bcolors.ENDC}"
            summary_lines.append(f"  - [{status}] {result['name']}")
            if result['passed']:
                passed_count += 1
            else:
                failed_count += 1

        summary_lines.append("-" * 60)
        summary_lines.append(f"Total Tests: {len(self.test_results)} | Passed: {passed_count} | Failed: {failed_count}")
        summary_lines.append("=" * 60)
        print("\n".join(summary_lines))

    async def test_price_fetcher_initialization(self):
        """Test PriceFetcher initialization and market loading."""
        test_name = "PriceFetcher Initialization"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            with patch('proxy_ccxt_v2.ccxt.kraken', new_callable=unittest.mock.MagicMock) as mock_exchange:
                config = unittest.mock.MagicMock()
                config.ccxt_exchange = "kraken"
                config.ccxt_hostname = None

                async with aiohttp.ClientSession() as session:
                    mock_exchange_instance = mock_exchange.return_value
                    mock_exchange_instance.load_markets = AsyncMock()
                    # Need to clear existing instances as ccxt maintains class-level state
                    if hasattr(ccxt.kraken, "_async_supported"):
                        delattr(ccxt.kraken, "_async_supported")
                    
                    fetcher = PriceFetcher(config, session)
                    await fetcher.initialize()

                    # Verify initialization steps
                    mock_exchange.assert_called_once_with({
                        "enableRateLimit": True,
                    })
                    mock_exchange_instance.load_markets.assert_awaited()
                    assert isinstance(fetcher.ccxt_i, unittest.mock.MagicMock), "CCXT instance not properly initialized"
                    print("[TEST PASSED] PriceFetcher initialized correctly.")
                    passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})

    async def test_ccxt_ticker_fetching(self):
        """Test fetching CCXT tickers and caching behavior."""
        test_name = "CCXT Ticker Fetching"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            config = unittest.mock.MagicMock()

            async with aiohttp.ClientSession() as session:
                fetcher = PriceFetcher(config, session)

                # Set up mock CCXT instance
                mock_ccxt = MagicMock()
                mock_ccxt.fetchTickers = AsyncMock(
                    return_value={}  # Return valid dict structure
                )
                mock_ccxt.fetchTickers = AsyncMock(return_value={"BTC/USD": {"last": 50000}})
                # Set valid markets
                mock_ccxt.markets = {
                    "BTC/USD": {"type": "spot"},
                    "ETH/USD": {"type": "spot"}
                }
                fetcher.ccxt_i = mock_ccxt

                # First fetch (API call expected)
                result = await fetcher.get_ccxt_tickers("BTC/USD")
                assert "BTC/USD" in result
                assert mock_ccxt.fetchTickers.await_count == 1

                # Second fetch (cache hit)
                result = await fetcher.get_ccxt_tickers("BTC/USD")
                assert "BTC/USD" in result
                assert mock_ccxt.fetchTickers.await_count == 1

                # New symbols (trigger API call)
                await fetcher.get_ccxt_tickers("ETH/USD")
                assert mock_ccxt.fetchTickers.await_count == 2

                # Needs refresh for new symbols
                fetcher.tickers = {}  # Clear cache
                all_tickers = await fetcher.get_ccxt_tickers("BTC/USD", "XMR/USD")
                assert mock_ccxt.fetchTickers.await_count == 3
                
                print("[TEST PASSED] CCXT ticker fetching behavior correct.")
                passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})

    async def test_block_ticker_fetching(self):
        """Test custom BLOCK ticker fetching via external API."""
        test_name = "BLOCK Ticker Fetching"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            config = unittest.mock.MagicMock()

            # Create a mock for the response
            response_mock = AsyncMock()
            response_mock.raise_for_status = unittest.mock.MagicMock()
            response_mock.json.return_value = {"BTC": 0.0015}

            # Mock session
            session = AsyncMock(spec=aiohttp.ClientSession)
            session.get.return_value.__aenter__.return_value = response_mock

            fetcher = PriceFetcher(config, session)
            fetcher.ccxt_i = AsyncMock()  # Empty mock

            # First fetch (API call)
            price = await fetcher.get_block_ticker()
            assert price == 0.0015
            session.get.assert_called_with(
                "https://min-api.cryptocompare.com/data/price?fsym=BLOCK&tsyms=BTC",
                timeout=10
            )

            # Second fetch (cache hit)
            session.get.reset_mock()
            price = await fetcher.get_block_ticker()
            assert price == 0.0015
            session.get.assert_not_called()
            
            print("[TEST PASSED] BLOCK ticker fetching behavior correct.")
            passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})

    async def test_webserver_request_handling(self):
        """Test WebServer request handling."""
        test_name = "WebServer Request Handling"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            fetcher = unittest.mock.MagicMock(spec=PriceFetcher)
            fetcher.get_ccxt_tickers = AsyncMock(return_value={"BTC/USD": 50000})
            fetcher.get_block_ticker = AsyncMock(return_value=0.0015)

            server = WebServer(fetcher, "localhost", 2233)

            # Test valid ccxt_call_fetch_tickers
            request = unittest.mock.MagicMock()
            request.json = AsyncMock(
                return_value={
                    "method": "ccxt_call_fetch_tickers",
                    "params": ["BTC/USD"],
                    "id": 1
                }
            )
            response = await server.handle_request(request)
            assert response.status == 200
            assert b"result" in response.body and b"BTC/USD" in response.body

            # Test valid BLOCK ticker
            request.json = AsyncMock(
                return_value={"method": "fetch_ticker_block", "id": 2}
            )
            response = await server.handle_request(request)
            assert response.status == 200
            assert b"result" in response.body and b"0.0015" in response.body

            # Test unsupported method
            request.json = AsyncMock(
                return_value={"method": "unknown_method", "id": 3}
            )
            response = await server.handle_request(request)
            assert response.status == 500
            assert b"Unsupported method" in response.body

            # Test malformed request
            request.json = AsyncMock(side_effect=Exception("Bad JSON"))
            response = await server.handle_request(request)
            assert response.status == 500
            # Check for either possible error message
            assert b"Error parsing request" in response.body or b"Bad JSON" in response.body
            
            print("[TEST PASSED] WebServer request handling correct.")
            passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})

    async def test_async_retry_decorator(self):
        """Test async retry decorator functionality."""

        class Service:
            def __init__(self):
                self.call_count = 0

            @async_retry(max_retries=3, delay=0.01)
            async def unreliable_method(self):
                self.call_count += 1
                if self.call_count < 3:
                    raise ValueError("Temporary failure")
                return "success"

        service = Service()
        result = await service.unreliable_method()
        assert result == "success"
        assert service.call_count == 3

        # Test failure beyond retry limit
        class FailingService:
            def __init__(self):
                self.call_count = 0

            @async_retry(max_retries=3, delay=0.01)
            async def unreliable_method(self):
                self.call_count += 1
                raise ValueError("Temporary failure")

        failing_service = FailingService()
        with pytest.raises(ValueError) as excinfo:
            await failing_service.unreliable_method()
        assert str(excinfo.value) == "Temporary failure"
        assert failing_service.call_count == 3

    async def test_periodic_refreshing(self):
        """Test periodic refresh task."""
        fetcher = unittest.mock.MagicMock(spec=PriceFetcher)
        fetcher.refresh_all_tickers = AsyncMock()

        server = WebServer(fetcher, "localhost", 2233)
        server.refresh_interval = 0.1  # Faster refresh for testing

        task = asyncio.create_task(server._run_periodically())
        await asyncio.sleep(0.25)  # Let it run 2-3 cycles

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Exception is expected but caught by production code

        assert fetcher.refresh_all_tickers.await_count >= 2

    async def test_invalid_symbol_handling(self):
        """Test handling of invalid symbols in get_ccxt_tickers."""
        config = unittest.mock.MagicMock()

        async with aiohttp.ClientSession() as session:
            fetcher = PriceFetcher(config, session)

            # Set up mock CCXT instance
            mock_ccxt = AsyncMock()
            mock_ccxt.fetchTickers = AsyncMock(return_value={"BTC/USD": {"last": 50000}})
            
            # Mock markets - only BTC/USD is valid
            mock_ccxt.markets = {
                "BTC/USD": {"type": "spot"},
            }
            fetcher.ccxt_i = mock_ccxt

            # Fetch with mix of valid and invalid symbols
            result = await fetcher.get_ccxt_tickers("BTC/USD", "ETH/USD", "INVALID")
            
            # Verify results
            assert "BTC/USD" in result
            assert result["BTC/USD"] == {"last": 50000}
            assert "ETH/USD" in result
            assert "error" in result["ETH/USD"]
            assert "INVALID" in result
            assert "error" in result["INVALID"]

            # First call should include only valid symbols in refresh
            symbols_in_refresh = mock_ccxt.fetchTickers.call_args[0][0]
            assert ["BTC/USD"] == symbols_in_refresh

    async def test_ticker_refresh_lock(self):
        """Test that refresh lock prevents multiple simultaneous refreshes."""
        test_name = "Ticker Refresh Lock"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            config = unittest.mock.MagicMock()

            async with aiohttp.ClientSession() as session:
                fetcher = PriceFetcher(config, session)
                mock_ccxt = AsyncMock()
                mock_ccxt.fetchTickers = AsyncMock(return_value={"BTC/USD": {"last": 50000}})
                mock_ccxt.markets = {"BTC/USD": {"type": "spot"}}
                fetcher.ccxt_i = mock_ccxt

                # Clear cache to force refresh
                fetcher.tickers = {}
                
                # Setup lock to delay mock fetch
                # Setup mock to return a fixed value after a delay
                return_value = {"BTC/USD": {"last": 50000}}
                async def delayed_fetch(*args, **kwargs):
                    await asyncio.sleep(0.01)
                    return return_value
                mock_ccxt.fetchTickers = AsyncMock(side_effect=delayed_fetch)

                # Start 5 concurrent requests
                tasks = [fetcher.get_ccxt_tickers("BTC/USD") for _ in range(5)]
                results = await asyncio.gather(*tasks)

                # Should only have made 1 API call due to lock
                assert mock_ccxt.fetchTickers.call_count == 1
                # All results should be valid
                for res in results:
                    assert "BTC/USD" in res
                
                print("[TEST PASSED] Refresh lock prevents duplicate API calls.")
                passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})

    async def test_block_ticker_error_handling(self):
        """Test BLOCK ticker returns error on API failure."""
        test_name = "BLOCK Ticker Error Handling"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            config = unittest.mock.MagicMock()

            # Create a mock session that returns an error
            session = AsyncMock(spec=aiohttp.ClientSession)
            session.get.return_value.__aenter__.side_effect = aiohttp.ClientError("API timeout")

            fetcher = PriceFetcher(config, session)
            fetcher.ccxt_i = AsyncMock()

            # First fetch (API call fails)
            try:
                await fetcher.get_block_ticker()
                passed = False  # Should have raised exception
            except aiohttp.ClientError as e:
                assert str(e) == "API timeout"
                passed = True
                print("[TEST PASSED] Properly handled API error for BLOCK ticker.")
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})

    async def test_grouped_ticker_refresh(self):
        """Test symbols are grouped by market type during refresh."""
        test_name = "Grouped Ticker Refresh"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            config = unittest.mock.MagicMock()

            async with aiohttp.ClientSession() as session:
                fetcher = PriceFetcher(config, session)

                # Set up mock CCXT instance with mixed market types
                mock_ccxt = AsyncMock()
                # Mock markets with proper structure including id and symbol
                mock_ccxt.markets = {
                    "BTC/USD": {"type": "spot", "id": "BTCUSD", "symbol": "BTC/USD"},
                    "ETH/USD": {"type": "spot", "id": "ETHUSD", "symbol": "ETH/USD"},
                    "XRP/USD:SWAP": {"type": "swap", "id": "XRPUSD:SWAP", "symbol": "XRP/USD:SWAP"}
                }
                # Set up mock return values for fetchTickers
                mock_ccxt.fetchTickers = AsyncMock(
                    side_effect=[
                        {"BTC/USD": {}, "ETH/USD": {}},
                        {"XRP/USD:SWAP": {}}
                    ]
                )
                fetcher.ccxt_i = mock_ccxt
                
                # Register symbols
                fetcher.symbols_list = ["BTC/USD", "ETH/USD", "XRP/USD:SWAP"]
                
                # Trigger refresh
                await fetcher.refresh_ccxt_tickers()

                # Verify three fetch calls: one for spot (both symbols), one for swap
                assert mock_ccxt.fetchTickers.call_count == 2
                spot_call_args = mock_ccxt.fetchTickers.call_args_list[0]
                swap_call_args = mock_ccxt.fetchTickers.call_args_list[1]
                
                # Spot group should have both spot symbols
                assert sorted(spot_call_args[0][0]) == ["BTC/USD", "ETH/USD"]
                # Swap group should have the swap symbol
                assert swap_call_args[0][0] == ["XRP/USD:SWAP"]
                
                print("[TEST PASSED] Symbols grouped by market type during refresh.")
                passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})

    async def test_rate_limiting_exception(self):
        """Test handling of CCXT rate limit exceptions."""
        test_name = "Rate Limiting Exception"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            config = unittest.mock.MagicMock()

            async with aiohttp.ClientSession() as session:
                fetcher = PriceFetcher(config, session)

                # Set up mock CCXT instance
                mock_ccxt = AsyncMock()
                mock_ccxt.fetchTickers = AsyncMock(
                    side_effect=ccxt.RateLimitExceeded("Rate limit exceeded")
                )
                mock_ccxt.markets = {"BTC/USD": {"type": "spot"}}
                fetcher.ccxt_i = mock_ccxt

                # This should trigger retries and eventually fail
                with pytest.raises(ccxt.RateLimitExceeded):
                    await fetcher.get_ccxt_tickers("BTC/USD")
                
                # Should have retried 3 times (default retry count)
                assert mock_ccxt.fetchTickers.await_count == 3
                print("[TEST PASSED] Properly handled rate limit exception.")
                passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})

    async def test_invalid_api_response_handling(self):
        """Test handling of invalid/malformed API responses."""
        test_name = "Invalid API Response Handling"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            config = unittest.mock.MagicMock()

            # Use a MagicMock for session to avoid real aiohttp.ClientSession
            session = MagicMock(spec=aiohttp.ClientSession)
            fetcher = PriceFetcher(config, session)
            
            # Mock CCXT to return unexpected data
            mock_ccxt = AsyncMock()
            mock_ccxt.fetchTickers = AsyncMock(return_value="INVALID_STRING_RESPONSE")
            mock_ccxt.markets = {"BTC/USD": {"type": "spot"}}
            fetcher.ccxt_i = mock_ccxt

            # Build proper mock for async context manager
            response_mock = MagicMock()
            response_mock.raise_for_status = MagicMock()

            # For async call to response.json(), use AsyncMock
            response_mock.json = AsyncMock(return_value={"unexpected": "format"})

            # Create a context manager mock for session.get
            context_manager = MagicMock()
            context_manager.__aenter__ = AsyncMock(return_value=response_mock)
            session.get.return_value = context_manager
            
            # Should not crash but log an error
            await fetcher.get_ccxt_tickers("BTC/USD")
            
            # Should raise KeyError for BLOCK ticker
            with pytest.raises(KeyError):
                await fetcher.get_block_ticker()
            
            print("[TEST PASSED] Properly handled invalid API responses.")
            passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})

    async def test_network_failure_scenarios(self):
        """Test handling of network failures and timeouts."""
        test_name = "Network Failure Handling"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            config = unittest.mock.MagicMock()

            # Mock session that simulates network failures
            session = AsyncMock(spec=aiohttp.ClientSession)
            session.get.side_effect = aiohttp.ClientError("Simulated network failure")
            
            fetcher = PriceFetcher(config, session)
            mock_ccxt = AsyncMock()
            mock_ccxt.fetchTickers = AsyncMock(side_effect=aiohttp.ClientConnectionError)
            mock_ccxt.markets = {"BTC/USD": {"type": "spot"}}
            fetcher.ccxt_i = mock_ccxt

            # Test CCXT network failure
            with pytest.raises(aiohttp.ClientConnectionError):
                await fetcher.get_ccxt_tickers("BTC/USD")
            
            # Test BLOCK API network failure
            with pytest.raises(aiohttp.ClientError):
                await fetcher.get_block_ticker()
            
            print("[TEST PASSED] Properly handled network failures.")
            passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})

    async def test_authentication_error_handling(self):
        """Test handling of authentication errors from CCXT."""
        test_name = "Authentication Error Handling"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            config = unittest.mock.MagicMock()

            async with aiohttp.ClientSession() as session:
                fetcher = PriceFetcher(config, session)
                
                # Mock CCXT authentication error
                mock_ccxt = AsyncMock()
                mock_ccxt.fetchTickers = AsyncMock(
                    side_effect=ccxt.AuthenticationError("Invalid API key")
                )
                mock_ccxt.markets = {"BTC/USD": {"type": "spot"}}
                fetcher.ccxt_i = mock_ccxt

                with pytest.raises(ccxt.AuthenticationError):
                    await fetcher.get_ccxt_tickers("BTC/USD")
                
                print("[TEST PASSED] Properly handled authentication errors.")
                passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})

    async def test_invalid_exchange_configuration(self):
        """Test handling of invalid exchange configuration."""
        test_name = "Invalid Exchange Configuration"
        print(f"\n--- [TEST CASE] Running: {test_name} ---")
        passed = False
        try:
            config = unittest.mock.MagicMock()
            config.ccxt_exchange = "INVALID_EXCHANGE"

            async with aiohttp.ClientSession() as session:
                fetcher = PriceFetcher(config, session)
                
                with pytest.raises(ValueError) as excinfo:
                    await fetcher.initialize()
                
                assert "not supported by ccxt" in str(excinfo.value)
                print("[TEST PASSED] Properly handled invalid exchange config.")
                passed = True
        except Exception as e:
            print(f"[TEST FAILED] {e}")
            traceback.print_exc()
        self.test_results.append({'name': test_name, 'passed': passed})


if __name__ == "__main__":
    tester = ProxyCCXTTester()
    asyncio.run(tester.run_all_tests())
