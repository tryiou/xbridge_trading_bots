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
        self.symbols_list = []
        self.tickers = {}
        self.ccxt_call_count = 0
        self.ccxt_cache_hit = 0
        self.print_delay = 5
        self.print_timer = None
        self.total_exec_time = time.time()
        self.ccxt_call_fetch_tickers_timer = time.time()
        self.ccxt_i = None
        self.task = None  # Initialize task to None
        self.custom_ticker = {}
        self.custom_ticker_call_count = 0
        self.custom_ticker_cache_count = 0

    async def run_periodically(self, interval):
        while True:
            await asyncio.sleep(interval)
            await self.refresh_tickers()

    async def init_task(self):
        self.task = asyncio.create_task(self.run_periodically(refresh_interval))
        self.ccxt_i = await init_ccxt_instance(ccxt_cfg.ccxt_exchange, ccxt_cfg.ccxt_hostname)

    async def refresh_tickers(self):
        done = False
        counter = 0
        while not done:
            counter += 1
            try:
                if self.symbols_list:
                    self.ccxt_call_count += 1
                    msg = f"{now()} refresh_tickers: {self.symbols_list}"
                    print(f"{bcolors.mycolor.OKGREEN}{msg}{bcolors.mycolor.OKGREEN}")
                    temp_tickers = await self.ccxt_i.fetchTickers(self.symbols_list)
                    self.tickers = temp_tickers
                    self.print_metrics()
            except Exception as e:
                msg = f"{now()} refresh_tickers error: {e} {type(e)}"
                print(f"{bcolors.mycolor.FAIL}{msg}{bcolors.mycolor.FAIL}")
                time.sleep(counter)
            else:
                done = True
        if 'BLOCK' in self.custom_ticker:
            await self.update_ticker_block()

    async def ccxt_call_fetch_tickers(self, *args):
        for symbol in args:
            if symbol not in self.symbols_list:
                self.symbols_list.append(symbol)
        trigger = False
        for symbol in self.symbols_list:
            if symbol not in self.tickers:
                trigger = True
        if trigger:
            await self.refresh_tickers()  # Await the refresh_tickers method
        else:
            self.ccxt_cache_hit += 1
        return self.tickers

    async def update_ticker_block(self):
        result = None
        done = False
        count = 0
        while not done:
            count += 1
            try:
                self.custom_ticker_call_count += 1
                ticker = requests.get(url=f"https://market.southxchange.com/api/price/{'BLOCK/BTC'}")
                if ticker.status_code == 200:
                    json = ticker.json()
                    result = json['Bid'] + ((json['Ask'] - json['Bid']) / 2)
            except Exception as e:
                msg = f"update_ccxt_price: BLOCK error({count}): {type(e).__name__}: {e}"
                print(f"{bcolors.mycolor.FAIL}{msg}{bcolors.mycolor.ENDC}")
                time.sleep(count)
            else:
                if result and isinstance(result, float):
                    done = True
                    msg = f"{now()} Updated BLOCK ticker: {result} BTC, call_count: {self.custom_ticker_call_count}, cache_hit: {self.custom_ticker_cache_count}"
                    print(f"{bcolors.mycolor.OKGREEN}{msg}{bcolors.mycolor.ENDC}")
                    self.custom_ticker['BLOCK'] = result
                else:
                    time.sleep(count)

    async def fetch_ticker_block(self):
        if 'BLOCK' not in self.custom_ticker:
            await self.update_ticker_block()
        else:
            self.custom_ticker_cache_count += 1
        return self.custom_ticker['BLOCK']

    def print_metrics(self):
        msg = f"{now()} ccxt_call_count: {self.ccxt_call_count} ccxt_cache_hit: {self.ccxt_cache_hit}"
        print(f"{bcolors.mycolor.OKGREEN}{msg}{bcolors.mycolor.ENDC}")

    async def handle(self, request):
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


def now():
    current_time = datetime.now()
    formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
    return formatted_time


def main():
    ccxt_server = CCXTServer()

    async def async_main():
        await ccxt_server.init_task()

        app = web.Application()
        app.router.add_post("/", ccxt_server.handle)
        web_task = web._run_app(app, host="localhost", port=2233)  # Use web._run_app instead of web.run_app
        await asyncio.gather(ccxt_server.task, web_task)

    asyncio.run(async_main())


async def init_ccxt_instance(exchange, hostname=None, private_api=False):
    # CCXT instance
    import ccxt.async_support as ccxt
    api_key = None
    api_secret = None
    if exchange in ccxt.exchanges:
        exchange_class = getattr(ccxt, exchange)
        if hostname:
            instance = exchange_class({
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True,
                'hostname': hostname,  # 'global.bittrex.com',
            })
        else:
            instance = exchange_class({
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True,
            })
        done = False
        while not done:
            try:
                await instance.load_markets()
            except Exception as e:
                msg = f"{now()} proxy_ccxt_rpc_call init_ccxt_instance error: {e} {type(e)} "
                print(f"{bcolors.mycolor.WARNING}{msg}{bcolors.mycolor.WARNING}")
            else:
                done = True
        return instance
    else:
        return None


if __name__ == "__main__":
    main()
