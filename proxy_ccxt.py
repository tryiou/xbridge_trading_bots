import asyncio
import logging
import os
import signal
from functools import wraps

import aiohttp
import ccxt.async_support as ccxt
from aiohttp import ClientSession, web

from definitions.yaml_mix import YamlToObject

if os.name == 'nt':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# --- Retry Decorator ---
def async_retry(max_retries=5, delay=1, backoff=2):
    """
    A decorator for retrying an async function with exponential backoff.
    """

    def decorator(f):
        @wraps(f)
        async def wrapper(*args, **kwargs):
            _delay = delay
            for i in range(max_retries):
                try:
                    return await f(*args, **kwargs)
                except Exception as e:
                    logging.error(
                        f"Attempt {i + 1}/{max_retries} for {f.__name__} failed: {e}",
                        exc_info=True
                    )
                    if i == max_retries - 1:
                        raise
                    await asyncio.sleep(_delay)
                    _delay *= backoff

        return wrapper

    return decorator


# --- Price Fetcher ---
class PriceFetcher:
    def __init__(self, config, session: ClientSession):
        self.config = config
        self.session = session
        self.ccxt_i = None
        self.tickers = {}
        self.custom_tickers = {}
        self.symbols_list = []
        self.active_custom_tickers = set()
        self.fetch_timeout = 10  # Timeout for fetchTickers in seconds

        # For stats
        self.ccxt_call_count = 0
        self.ccxt_cache_hit = 0
        self.custom_ticker_call_count = 0
        self.custom_ticker_cache_hit = 0

        self._refresh_lock = asyncio.Lock()

    async def initialize(self):
        """Initializes the CCXT instance and loads markets."""
        exchange_name = self.config.ccxt_exchange
        hostname = self.config.ccxt_hostname

        if exchange_name not in ccxt.exchanges:
            raise ValueError(f"Exchange {exchange_name} not supported by ccxt")

        exchange_class = getattr(ccxt, exchange_name)

        config = {'enableRateLimit': True}
        if hostname:
            config['hostname'] = hostname

        self.ccxt_i = exchange_class(config)

        await self._load_markets_with_retry()

    @async_retry()
    async def _load_markets_with_retry(self):
        logging.info(f"Loading markets for exchange: {self.ccxt_i.id}")
        await self.ccxt_i.load_markets()
        logging.info(f"Markets loaded successfully for exchange: {self.ccxt_i.id}")

    async def close(self):
        if self.ccxt_i:
            await self.ccxt_i.close()
            logging.info("CCXT instance closed.")

    def _needs_refresh(self, symbols=None):
        """Check if a refresh is needed for the given symbols."""
        if symbols is None:
            symbols = self.symbols_list
        return any(s not in self.tickers for s in symbols)

    @async_retry(max_retries=3, delay=2)
    async def refresh_ccxt_tickers(self):
        """Refreshes tickers from CCXT for all registered symbols."""
        if not self.symbols_list:
            return

        logging.info(f"Refreshing CCXT tickers for: {self.symbols_list}")
        self.ccxt_call_count += 1
        self.tickers = await asyncio.wait_for(
            self.ccxt_i.fetchTickers(self.symbols_list),
            timeout=self.fetch_timeout
        )
        logging.info("Successfully refreshed CCXT tickers.")

    @async_retry(max_retries=3, delay=2)
    async def update_custom_ticker_block(self):
        """Updates the BLOCK ticker from CryptoCompare."""
        logging.info("Fetching BLOCK ticker from external API...")
        self.custom_ticker_call_count += 1
        url = 'https://min-api.cryptocompare.com/data/price?fsym=BLOCK&tsyms=BTC'
        async with self.session.get(url, timeout=10) as response:
            response.raise_for_status()
            data = await response.json()
            price = data.get('BTC')
            if price and isinstance(price, float):
                self.custom_tickers['BLOCK'] = price
                logging.info(f"Updated BLOCK ticker: {price} BTC")
            else:
                logging.error(f"Invalid data for BLOCK ticker: {data}")

    async def refresh_all_tickers(self):
        """Refreshes all configured tickers."""
        tasks = [self.refresh_ccxt_tickers()]
        if 'BLOCK' in self.active_custom_tickers:
            tasks.append(self.update_custom_ticker_block())

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logging.error(f"Error during periodic refresh of task {i}: {result}")

        self.print_metrics()

    async def get_ccxt_tickers(self, *symbols: str) -> dict:
        """Get tickers from CCXT, fetching if necessary."""
        new_symbols = [s for s in symbols if s not in self.symbols_list]
        if new_symbols:
            self.symbols_list.extend(new_symbols)

        if self._needs_refresh(symbols):
            async with self._refresh_lock:
                # Double-check inside the lock
                if self._needs_refresh(symbols):
                    logging.info("Triggering on-demand refresh for CCXT tickers.")
                    await self.refresh_ccxt_tickers()
                    self.print_metrics()
        else:
            self.ccxt_cache_hit += 1
            logging.info("Returning cached CCXT tickers.")

        return self.tickers

    async def get_block_ticker(self) -> float:
        """Get BLOCK ticker, fetching if necessary."""
        self.active_custom_tickers.add('BLOCK')
        if self.custom_tickers.get('BLOCK') is None:
            async with self._refresh_lock:
                if self.custom_tickers.get('BLOCK') is None:
                    logging.info("Triggering on-demand refresh for BLOCK ticker.")
                    await self.update_custom_ticker_block()
                    self.print_metrics()
        else:
            self.custom_ticker_cache_hit += 1
            logging.info("Returning cached BLOCK ticker.")

        return self.custom_tickers['BLOCK']

    def print_metrics(self):
        msg_parts = [
            f"ccxt_call_count: {self.ccxt_call_count}",
            f"ccxt_cache_hit: {self.ccxt_cache_hit}",
        ]
        if 'BLOCK' in self.active_custom_tickers:
            msg_parts.extend([
                f"BLOCK_call_count: {self.custom_ticker_call_count}",
                f"BLOCK_cache_hit: {self.custom_ticker_cache_hit}",
            ])
        logging.info(f"Metrics: {', '.join(msg_parts)}")


# --- Web Server ---
class WebServer:
    def __init__(self, fetcher: PriceFetcher, host: str, port: int):
        self.fetcher = fetcher
        self.host = host
        self.port = port
        self.app = web.Application()
        self.app.router.add_post("/", self.handle_request)
        self.runner = None
        self.periodic_task = None
        self.refresh_interval = 15

    async def handle_request(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            method = data.get('method')
            params = data.get('params', [])

            logging.info(f"Received request for method: {method}")

            if method == 'ccxt_call_fetch_tickers':
                response_data = await self.fetcher.get_ccxt_tickers(*params)
            elif method == 'fetch_ticker_block':
                response_data = await self.fetcher.get_block_ticker()
            else:
                raise ValueError(f"Unsupported method: {method}")

            return web.json_response({
                "jsonrpc": "2.0",
                "result": response_data,
                "id": data.get("id")
            })
        except Exception as e:
            logging.error(f"Error handling request: {e}", exc_info=True)
            error_response = {
                "jsonrpc": "2.0",
                "error": {"code": 500, "message": str(e)},
                "id": data.get("id")
            }
            return web.json_response(error_response, status=500)

    async def _run_periodically(self):
        """Periodically refreshes all tickers."""
        while True:
            try:
                await self.fetcher.refresh_all_tickers()
                await asyncio.sleep(self.refresh_interval)
            except asyncio.CancelledError:
                logging.info("Periodic refresh task cancelled.")
                break
            except Exception as e:
                logging.error(f"Error in periodic refresh: {e}", exc_info=True)
                # Wait before retrying to avoid spamming logs on persistent errors
                await asyncio.sleep(self.refresh_interval)

    async def start(self):
        """Starts the web server and the periodic refresh task."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        logging.info(f"Web server running on http://{self.host}:{self.port}")

        self.periodic_task = asyncio.create_task(self._run_periodically())

    async def stop(self):
        """Stops the web server and associated tasks gracefully."""
        logging.info("Shutting down server...")

        if self.periodic_task and not self.periodic_task.done():
            self.periodic_task.cancel()
            await self.periodic_task

        if self.runner:
            await self.runner.cleanup()
            logging.info("Web server stopped.")


# --- Main execution ---
async def main():
    config = YamlToObject("./config/config_ccxt.yaml")

    async with aiohttp.ClientSession() as session:
        fetcher = PriceFetcher(config, session)
        try:
            await fetcher.initialize()

            server = WebServer(fetcher, "localhost", 2233)
            loop = asyncio.get_running_loop()
            stop_event = asyncio.Event()

            def _signal_handler():
                logging.info("Shutdown signal received.")
                stop_event.set()

            if os.name != 'nt':
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, _signal_handler)

            await server.start()
            await stop_event.wait()
        except Exception as e:
            logging.error(f"Critical error in main: {e}", exc_info=True)
        finally:
            if server:
                await server.stop()
            if fetcher:
                await fetcher.close()
            logging.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received, shutting down.")
