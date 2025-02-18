import asyncio
import time
from datetime import datetime
import traceback
import signal
import requests
from aiohttp import web

import definitions.bcolors as bcolors
from definitions.yaml_mix import YamlToObject


class CCXTServer:
    def __init__(self):
        self.must_refresh_tickers = None
        self.refresh_interval = 15
        self.config_ccxt = None
        self.symbols_list: list[str] = []
        self.tickers: dict = {}
        self.ccxt_call_count: int = 0
        self.ccxt_cache_hit: int = 0
        self.print_delay: int = 5
        self.print_timer: float = time.time()
        self.total_exec_time: float = time.time()
        self.ccxt_call_fetch_tickers_timer: float = time.time()
        self.ccxt_i = None
        self.task = None  # Initialize task to None
        self.custom_ticker: dict[str, float] = {}
        self.custom_ticker_call_count: int = 0
        self.custom_ticker_cache_count: int = 0
        self.fetch_timeout = 10  # Timeout for fetchTickers in seconds

    def now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async def run_periodically(self, interval: int):
        last_refresh = 0
        while True:
            try:
                now = time.time()
                if self.must_refresh_tickers:
                    self._log_info("Must refresh tickers")
                    await self.refresh_tickers()
                    last_refresh = now
                    self.must_refresh_tickers = False  # Reset the flag after refreshing

                if (now - last_refresh) >= interval:
                    self._log_info("Periodic refresh tickers")
                    await self.refresh_tickers()
                    last_refresh = now

                await asyncio.sleep(0.1)
            except Exception as e:
                self._log_error(f"Error in periodic task: {e}")

    async def init_task(self):
        self._log_info("Initializing CCXT task...")
        try:
            self.ccxt_i = await self.init_ccxt_instance(self.config_ccxt.ccxt_exchange, self.config_ccxt.ccxt_hostname)
            self._log_info("CCXT instance initialized.")
            self.task = asyncio.create_task(self.run_periodically(self.refresh_interval))

        except asyncio.CancelledError:
            self._log_info("Periodic task cancelled. Exiting run_periodically.")
            await self.shutdown()
            exit()
        except Exception as e:
            self._log_error(f"Error during init_task: {e}")
            traceback.print_exc()

    async def refresh_tickers(self):
        self._log_info("Starting refresh_tickers task...")
        if not self.symbols_list:
            return
        done = False
        retry_count = 0
        while not done:
            retry_count += 1
            if self.symbols_list:
                self.ccxt_call_count += 1
                self._log_info(f"Attempting to refresh tickers for: {self.symbols_list}, retry: {retry_count}")
                try:
                    temp_tickers = await asyncio.wait_for(self.ccxt_i.fetchTickers(self.symbols_list),
                                                          timeout=self.fetch_timeout)
                    self.tickers = temp_tickers
                    done = True
                    self._log_info("Successfully refreshed tickers.")
                except asyncio.TimeoutError:
                    self._log_error(f"Timeout fetching tickers after {self.fetch_timeout} seconds, retrying...")
                    await asyncio.sleep(retry_count)  # Delay before retrying
                except Exception as e:
                    self._log_error(f"refresh_tickers error: {e} {type(e)}, retrying...")
                    traceback.print_exc()
                    await asyncio.sleep(retry_count)

        if 'BLOCK' in self.custom_ticker:
            await self.update_ticker_block()
        self.print_metrics()

    async def ccxt_call_fetch_tickers(self, *symbols: str) -> dict:
        self._log_info(f"ccxt_call_fetch_tickers called with symbols: {symbols}")
        self.symbols_list.extend([symbol for symbol in symbols if symbol not in self.symbols_list])
        if any(symbol not in self.tickers for symbol in self.symbols_list):
            self._log_info("Fetching tickers from ccxt...")
            self.must_refresh_tickers = True
            while self.must_refresh_tickers:
                await(asyncio.sleep(0.1))
            # await self.refresh_tickers()
        else:
            self.ccxt_cache_hit += 1
            self._log_info("Returning cached tickers.")
        return self.tickers

    async def update_ticker_block(self):
        self._log_info("Starting update_ticker_block task...")
        result = None
        done = False
        retry_count = 0
        while not done:
            retry_count += 1
            try:
                self.custom_ticker_call_count += 1
                self._log_info("Fetching BLOCK ticker from external API...")
                ticker = requests.get(url='https://min-api.cryptocompare.com/data/price?fsym=BLOCK&tsyms=BTC')
                if ticker.status_code == 200:
                    result = ticker.json().get('BTC')
                    if result and isinstance(result, float):
                        done = True
                        self.custom_ticker['BLOCK'] = result
                        self._log_info(f"Updated BLOCK ticker: {result} BTC")
                else:
                    self._log_error(f"Error fetching BLOCK ticker, status code: {ticker.status_code}")
            except Exception as e:
                self._log_error(f"update_ticker_block error: {e} {type(e)}, retrying...")
                traceback.print_exc()
                await asyncio.sleep(retry_count)

    async def fetch_ticker_block(self) -> float:
        self._log_info("fetch_ticker_block called.")
        if 'BLOCK' not in self.custom_ticker:
            self._log_info("BLOCK ticker not in cache, updating...")
            await self.update_ticker_block()
        else:
            self.custom_ticker_cache_count += 1
            self._log_info("Returning cached BLOCK ticker.")
        return self.custom_ticker['BLOCK']

    def print_metrics(self):
        if 'BLOCK' in self.custom_ticker:
            msg = f"ccxt_call_count: {self.ccxt_call_count}, ccxt_cache_hit: {self.ccxt_cache_hit}, " \
                  f"BLOCK_call_count: {self.custom_ticker_call_count}, BLOCK_cache_hit: {self.custom_ticker_cache_count}"
        else:
            msg = f"ccxt_call_count: {self.ccxt_call_count}, ccxt_cache_hit: {self.ccxt_cache_hit}"
        self._log_info(f"Metrics: {msg}")

    async def handle(self, request: web.Request) -> web.Response:
        self._log_info("Received a request.")
        try:
            data = await request.json()
            self._log_info(f"Request data: {data}")
            method = data.get('method')
            if method == 'ccxt_call_fetch_tickers':
                response = await self.ccxt_call_fetch_tickers(*data['params'])
            elif method == 'fetch_ticker_block':
                response = await self.fetch_ticker_block()
            else:
                raise ValueError(f"Unsupported method: {method}")
            self._log_info(f"Request processed successfully, method: {method}")
            return web.json_response({"jsonrpc": "2.0", "result": response, "id": data.get("id")})
        except Exception as e:
            self._log_error(f"Error handling request: {e}")
            traceback.print_exc()
            error_response = {"jsonrpc": "2.0", "error": {"code": 500, "message": str(e)}, "id": None}
            return web.json_response(error_response, status=500)

    def _log_info(self, message: str):
        print(f"{bcolors.mycolor.OKGREEN}{self.now()} [INFO] {message}{bcolors.mycolor.ENDC}")

    def _log_error(self, message: str):
        print(f"{bcolors.mycolor.FAIL}{self.now()} [ERROR] {message}{bcolors.mycolor.ENDC}")

    async def shutdown(self):
        self._log_info("Shutting down server...")
        if self.task:
            self._log_info("Cancelling periodic task...")
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                self._log_info("Periodic task cancelled successfully.")

    async def init_ccxt_instance(self, exchange: str, hostname: str = None) -> object:
        import ccxt.async_support as ccxt
        api_key = None
        api_secret = None
        if exchange in ccxt.exchanges:
            exchange_class = getattr(ccxt, exchange)
            instance = exchange_class({
                                          'apiKey': api_key,
                                          'secret': api_secret,
                                          'enableRateLimit': True,
                                          'hostname': hostname
                                      } if hostname else {
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True
            })

            done = False
            while not done:
                try:
                    print(f"{self.now()} [INFO] Loading markets for exchange: {exchange}")
                    await instance.load_markets()
                    done = True
                    print(f"{self.now()} [INFO] Markets loaded successfully for exchange: {exchange}")
                except Exception as e:
                    print(f"{self.now()} [ERROR] init_ccxt_instance error: {e} {type(e)}")
                    traceback.print_exc()
                    await asyncio.sleep(5)
            return instance
        return None

    async def main(self):
        self.config_ccxt = YamlToObject("./config/config_ccxt.yaml")

        try:
            await self.init_task()

            app = web.Application()
            app.router.add_post("/", self.handle)
            self._log_info("Starting web server...")

            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "localhost", 2233)
            await site.start()

            # Register signal handlers for graceful shutdown
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(self.shutdown()))
            loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(self.shutdown()))

            self._log_info("Web server is running.")

            await self.task  # Keep running the periodic task
        except Exception as e:
            self._log_error(f"Error in main method: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    ccxt_server = CCXTServer()
    asyncio.run(ccxt_server.main())
