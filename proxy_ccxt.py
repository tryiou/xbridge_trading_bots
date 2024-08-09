import asyncio
from aiohttp import web
import config.ccxt_cfg as ccxt_cfg
import definitions.bcolors as bcolors
import time
from datetime import datetime
import requests

refresh_interval = 15


class CCXTServer:
    def __init__(self):
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

    async def run_periodically(self, interval: int):
        while True:
            await asyncio.sleep(interval)
            await self.refresh_tickers()

    async def init_task(self):
        self.task = asyncio.create_task(self.run_periodically(refresh_interval))
        self.ccxt_i = await init_ccxt_instance(ccxt_cfg.ccxt_exchange, ccxt_cfg.ccxt_hostname)

    async def refresh_tickers(self):
        done = False
        retry_count = 0
        while not done:
            retry_count += 1
            try:
                if self.symbols_list:
                    self.ccxt_call_count += 1
                    self._log_info(f"refresh_tickers: {self.symbols_list}")
                    temp_tickers = await self.ccxt_i.fetchTickers(self.symbols_list)
                    self.tickers = temp_tickers
                    done = True
            except Exception as e:
                self._log_error(f"refresh_tickers error: {e} {type(e)}", retry_count)
                await asyncio.sleep(retry_count)
        if 'BLOCK' in self.custom_ticker:
            await self.update_ticker_block()
        self.print_metrics()

    async def ccxt_call_fetch_tickers(self, *symbols: str) -> dict:
        self.symbols_list.extend([symbol for symbol in symbols if symbol not in self.symbols_list])
        if any(symbol not in self.tickers for symbol in self.symbols_list):
            await self.refresh_tickers()
        else:
            self.ccxt_cache_hit += 1
        return self.tickers

    async def update_ticker_block(self):
        result = None
        done = False
        retry_count = 0
        while not done:
            retry_count += 1
            try:
                self.custom_ticker_call_count += 1
                ticker = requests.get(url='https://min-api.cryptocompare.com/data/price?fsym=BLOCK&tsyms=BTC')
                if ticker.status_code == 200:
                    result = ticker.json().get('BTC')
                    if result and isinstance(result, float):
                        done = True
                        self.custom_ticker['BLOCK'] = result
                        self._log_info(f"Updated BLOCK ticker: {result} BTC")
            except Exception as e:
                self._log_error(f"update_ccxt_price: BLOCK error: {type(e).__name__}: {e}", retry_count)
                await asyncio.sleep(retry_count)

    async def fetch_ticker_block(self) -> float:
        if 'BLOCK' not in self.custom_ticker:
            await self.update_ticker_block()
        else:
            self.custom_ticker_cache_count += 1
        return self.custom_ticker['BLOCK']

    def print_metrics(self):
        if 'BLOCK' in self.custom_ticker:
            msg = f"ccxt_call_count: {self.ccxt_call_count} ccxt_cache_hit: {self.ccxt_cache_hit} " \
                  f"BLOCK_call_count: {self.custom_ticker_call_count}, BLOCK_cache_hit: {self.custom_ticker_cache_count}"
        else:
            msg = f"ccxt_call_count: {self.ccxt_call_count} ccxt_cache_hit: {self.ccxt_cache_hit}"
        self._log_info(msg)

    async def handle(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
            method = data.get('method')
            if method == 'ccxt_call_fetch_tickers':
                response = await self.ccxt_call_fetch_tickers(*data['params'])
            elif method == 'fetch_ticker_block':
                response = await self.fetch_ticker_block()
            else:
                raise ValueError(f"Unsupported method: {method}")
            return web.json_response({"jsonrpc": "2.0", "result": response, "id": data.get("id")})
        except Exception as e:
            error_response = {"jsonrpc": "2.0", "error": {"code": 500, "message": str(e)}, "id": None}
            return web.json_response(error_response, status=500)

    def _log_info(self, message: str):
        print(f"{bcolors.mycolor.OKGREEN}{now()} {message}{bcolors.mycolor.ENDC}")

    def _log_error(self, message: str, retry_count: int = 0):
        retry_msg = f" (Retrying in {retry_count} seconds...)" if retry_count else ""
        print(f"{bcolors.mycolor.FAIL}{now()} {message}{retry_msg}{bcolors.mycolor.ENDC}")


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    ccxt_server = CCXTServer()

    async def async_main():
        await ccxt_server.init_task()

        app = web.Application()
        app.router.add_post("/", ccxt_server.handle)
        web_task = web._run_app(app, host="localhost", port=2233)  # Use web._run_app instead of web.run_app
        await asyncio.gather(ccxt_server.task, web_task)

    asyncio.run(async_main())


async def init_ccxt_instance(exchange: str, hostname: str = None, private_api: bool = False):
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
                await instance.load_markets()
                done = True
            except Exception as e:
                print(f"{bcolors.mycolor.WARNING}{now()} proxy_ccxt_rpc_call init_ccxt_instance error: {e} {type(e)} {bcolors.mycolor.WARNING}")
                await asyncio.sleep(5)
        return instance
    return None


if __name__ == "__main__":
    main()
