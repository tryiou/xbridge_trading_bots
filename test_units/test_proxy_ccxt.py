import asyncio
import logging
import os
import sys
from unittest.mock import patch, AsyncMock, MagicMock

import aiohttp
import ccxt
import pytest
import pytest_asyncio

# Add parent directory to path for module imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from proxy_ccxt import PriceFetcher, WebServer, async_retry


@pytest_asyncio.fixture
async def mock_price_fetcher():
    """Fixture to create a mock PriceFetcher instance for testing."""
    config = MagicMock()
    config.ccxt_exchange = "kraken"
    config.ccxt_hostname = None

    async with aiohttp.ClientSession() as session:
        fetcher = PriceFetcher(config, session)
        # Mock the ccxt instance to avoid real network calls during most tests
        fetcher.ccxt_i = AsyncMock()
        fetcher.ccxt_i.markets = {
            "BTC/USD": {"type": "spot"},
            "ETH/USD": {"type": "spot"},
            "XRP/USD:SWAP": {"type": "swap"}
        }
        yield fetcher


@pytest.mark.asyncio
async def test_price_fetcher_initialization():
    """Test PriceFetcher initialization and market loading."""
    with patch('proxy_ccxt.ccxt.kraken', new_callable=MagicMock) as mock_exchange:
        config = MagicMock()
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
            assert isinstance(fetcher.ccxt_i, MagicMock), "CCXT instance not properly initialized"


@pytest.mark.asyncio
async def test_ccxt_ticker_fetching(mock_price_fetcher):
    """Test fetching CCXT tickers and caching behavior."""
    fetcher = mock_price_fetcher
    mock_ccxt = fetcher.ccxt_i
    all_tickers = {"BTC/USD": {"last": 50000}, "ETH/USD": {"last": 3000}}

    async def mock_fetch_tickers(symbols, params={}):
        return {s: all_tickers[s] for s in symbols if s in all_tickers}

    mock_ccxt.fetchTickers.side_effect = mock_fetch_tickers

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
    await fetcher.get_ccxt_tickers("BTC/USD", "XMR/USD")
    assert mock_ccxt.fetchTickers.await_count == 3


@pytest.mark.asyncio
async def test_block_ticker_fetching():
    """Test custom BLOCK ticker fetching via external API."""
    config = MagicMock()

    # Create a mock for the response
    response_mock = AsyncMock()
    response_mock.raise_for_status = MagicMock()
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


@pytest.mark.asyncio
async def test_webserver_request_handling():
    """Test WebServer request handling."""
    fetcher = MagicMock(spec=PriceFetcher)
    fetcher.get_ccxt_tickers = AsyncMock(return_value={"BTC/USD": 50000})
    fetcher.get_block_ticker = AsyncMock(return_value=0.0015)

    server = WebServer(fetcher, "localhost", 2233)

    # Test valid ccxt_call_fetch_tickers
    request = MagicMock()
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
    assert b"Bad JSON" in response.body


@pytest.mark.asyncio
async def test_async_retry_decorator(caplog):
    """Test async retry decorator functionality and error logging."""
    caplog.set_level(logging.ERROR)

    # Test error log on final failure
    class FailingService:
        def __init__(self):
            self.call_count = 0

        @async_retry(max_retries=3, delay=0.01)
        async def unreliable_method(self):
            self.call_count += 1
            raise ValueError("Temporary failure")

    failing_service = FailingService()
    with pytest.raises(ValueError):
        await failing_service.unreliable_method()

    assert failing_service.call_count == 3
    assert "All retries failed for unreliable_method" in caplog.text

    # Test successful call doesn't log critical
    caplog.clear()

    class SuccessfulService:
        def __init__(self):
            self.call_count = 0

        @async_retry(max_retries=3, delay=0.01)
        async def reliable_method(self):
            self.call_count += 1
            if self.call_count < 2:
                raise ValueError("Temporary failure")
            return "success"

    successful_service = SuccessfulService()
    result = await successful_service.reliable_method()
    assert result == "success"
    assert "All retries failed for reliable_method" not in caplog.text


@pytest.mark.asyncio
async def test_periodic_refreshing():
    """Test periodic refresh task under normal conditions."""
    fetcher = MagicMock(spec=PriceFetcher)
    fetcher.refresh_all_tickers = AsyncMock()

    server = WebServer(fetcher, "localhost", 2233)
    server.refresh_interval = 0.01  # Faster refresh for testing

    task = asyncio.create_task(server._run_periodically())
    await asyncio.sleep(0.02)  # Let it run 2-3 cycles

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass  # Exception is expected but caught by production code

    assert fetcher.refresh_all_tickers.await_count >= 2


@pytest.mark.asyncio
async def test_periodic_refreshing_resilience(caplog):
    """Test that errors in the refresh loop are logged but do not break the loop."""
    caplog.set_level(logging.ERROR)
    fetcher = MagicMock(spec=PriceFetcher)
    # Fail on the first call, succeed on subsequent calls
    fetcher.refresh_all_tickers = AsyncMock(
        side_effect=[Exception("Ticker fetch error"), None, None]
    )

    server = WebServer(fetcher, "localhost", 2233)
    server.refresh_interval = 0.01

    task = asyncio.create_task(server._run_periodically())
    # Let it run for a few cycles to ensure it retries
    await asyncio.sleep(0.05)

    # Cancel the task to stop it
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass  # Expected on cancellation

    # Check that error was logged
    assert "Error during ticker refresh" in caplog.text
    assert "Ticker fetch error" in caplog.text
    # Check that it attempted to refresh multiple times despite the error
    assert fetcher.refresh_all_tickers.await_count >= 2


@pytest.mark.asyncio
async def test_invalid_symbol_handling(mock_price_fetcher):
    """Test handling of invalid symbols in get_ccxt_tickers."""
    fetcher = mock_price_fetcher
    mock_ccxt = fetcher.ccxt_i
    mock_ccxt.fetchTickers.return_value = {"BTC/USD": {"last": 50000}, "ETH/USD": {"last": 3000}}

    # Fetch with mix of valid and invalid symbols
    result = await fetcher.get_ccxt_tickers("BTC/USD", "ETH/USD", "INVALID")

    # Verify results
    assert "BTC/USD" in result
    assert result["BTC/USD"] == {"last": 50000}
    assert "ETH/USD" in result
    assert "error" not in result["ETH/USD"]
    assert "INVALID" in result
    assert "error" in result["INVALID"]

    # First call should include only valid symbols in refresh
    symbols_in_refresh = mock_ccxt.fetchTickers.call_args[0][0]
    assert "BTC/USD" in symbols_in_refresh
    assert "ETH/USD" in symbols_in_refresh
    assert "INVALID" not in symbols_in_refresh


@pytest.mark.asyncio
async def test_ticker_refresh_lock(mock_price_fetcher):
    """Test that refresh lock prevents multiple simultaneous refreshes."""
    fetcher = mock_price_fetcher
    mock_ccxt = fetcher.ccxt_i

    # Clear cache to force refresh
    fetcher.tickers = {}

    # Setup mock to return a fixed value after a delay
    return_value = {"BTC/USD": {"last": 50000}}

    async def delayed_fetch(*args, **kwargs):
        await asyncio.sleep(0.01)
        return return_value

    mock_ccxt.fetchTickers.side_effect = delayed_fetch

    # Start 5 concurrent requests
    tasks = [fetcher.get_ccxt_tickers("BTC/USD") for _ in range(5)]
    results = await asyncio.gather(*tasks)

    # Should only have made 1 API call due to lock
    assert mock_ccxt.fetchTickers.call_count == 1
    # All results should be valid
    for res in results:
        assert "BTC/USD" in res


@pytest.mark.asyncio
async def test_block_ticker_error_handling():
    """Test BLOCK ticker returns error on API failure."""
    config = MagicMock()

    # Create a mock session that returns an error
    session = AsyncMock(spec=aiohttp.ClientSession)
    session.get.return_value.__aenter__.side_effect = aiohttp.ClientError("API timeout")

    fetcher = PriceFetcher(config, session)
    fetcher.ccxt_i = AsyncMock()

    # First fetch (API call fails)
    with pytest.raises(aiohttp.ClientError, match="API timeout"):
        await fetcher.get_block_ticker()


@pytest.mark.asyncio
async def test_grouped_ticker_refresh(mock_price_fetcher):
    """Test symbols are grouped by market type during refresh."""
    fetcher = mock_price_fetcher
    mock_ccxt = fetcher.ccxt_i

    # Set up mock return values for fetchTickers
    mock_ccxt.fetchTickers.side_effect = [
        {"BTC/USD": {}, "ETH/USD": {}},
        {"XRP/USD:SWAP": {}}
    ]

    # Register symbols
    fetcher.symbols_list = ["BTC/USD", "ETH/USD", "XRP/USD:SWAP"]

    # Trigger refresh
    await fetcher.refresh_ccxt_tickers()

    # Verify two fetch calls: one for spot, one for swap
    assert mock_ccxt.fetchTickers.call_count == 2
    spot_call_args = mock_ccxt.fetchTickers.call_args_list[0]
    swap_call_args = mock_ccxt.fetchTickers.call_args_list[1]

    # Spot group should have both spot symbols
    assert sorted(spot_call_args[0][0]) == ["BTC/USD", "ETH/USD"]
    # Swap group should have the swap symbol
    assert swap_call_args[0][0] == ["XRP/USD:SWAP"]


@pytest.mark.asyncio
async def test_rate_limiting_exception(mock_price_fetcher):
    """Test handling of CCXT rate limit exceptions."""
    fetcher = mock_price_fetcher
    mock_ccxt = fetcher.ccxt_i
    mock_ccxt.fetchTickers.side_effect = ccxt.RateLimitExceeded("Rate limit exceeded")

    # This should trigger retries and eventually fail
    with pytest.raises(ccxt.RateLimitExceeded):
        await fetcher.get_ccxt_tickers("BTC/USD")

    # Should have retried 3 times (default retry count)
    assert mock_ccxt.fetchTickers.await_count == 3


@pytest.mark.asyncio
async def test_invalid_api_response_handling():
    """Test handling of invalid/malformed API responses."""
    config = MagicMock()
    session = MagicMock(spec=aiohttp.ClientSession)
    fetcher = PriceFetcher(config, session)

    # Mock CCXT to return unexpected data
    mock_ccxt = AsyncMock()
    mock_ccxt.fetchTickers.return_value = "INVALID_STRING_RESPONSE"
    fetcher.ccxt_i = mock_ccxt

    # Build proper mock for async context manager
    response_mock = MagicMock()
    response_mock.raise_for_status = MagicMock()
    response_mock.json = AsyncMock(return_value={"unexpected": "format"})
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=response_mock)
    session.get.return_value = context_manager

    # Should not crash but log an error
    await fetcher.get_ccxt_tickers("BTC/USD")

    # Should raise KeyError for BLOCK ticker due to unexpected format
    with pytest.raises(KeyError):
        await fetcher.get_block_ticker()


@pytest.mark.asyncio
async def test_network_failure_scenarios(mock_price_fetcher):
    """Test handling of network failures and timeouts."""
    fetcher = mock_price_fetcher
    mock_ccxt = fetcher.ccxt_i
    mock_ccxt.fetchTickers.side_effect = aiohttp.ClientConnectionError

    # Test CCXT network failure
    with pytest.raises(aiohttp.ClientConnectionError):
        await fetcher.get_ccxt_tickers("BTC/USD")

    # Test BLOCK API network failure
    with patch('aiohttp.ClientSession.get', side_effect=aiohttp.ClientError("Simulated network failure")):
        with pytest.raises(aiohttp.ClientError):
            await fetcher.get_block_ticker()


@pytest.mark.asyncio
async def test_authentication_error_handling(mock_price_fetcher):
    """Test handling of authentication errors from CCXT."""
    fetcher = mock_price_fetcher
    mock_ccxt = fetcher.ccxt_i
    mock_ccxt.fetchTickers.side_effect = ccxt.AuthenticationError("Invalid API key")

    with pytest.raises(ccxt.AuthenticationError):
        await fetcher.get_ccxt_tickers("BTC/USD")


@pytest.mark.asyncio
async def test_invalid_exchange_configuration():
    """Test handling of invalid exchange configuration."""
    config = MagicMock()
    config.ccxt_exchange = "INVALID_EXCHANGE"

    async with aiohttp.ClientSession() as session:
        fetcher = PriceFetcher(config, session)
        with pytest.raises(ValueError, match="not supported by ccxt"):
            await fetcher.initialize()
