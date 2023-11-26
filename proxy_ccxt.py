import asyncio
from aiohttp import web
import config.ccxt_cfg as ccxt_cfg
import definitions.bcolors as bcolors
import definitions.ccxt_def as ccxt_def
import time

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
        self.ccxt_i = ccxt_def.init_ccxt_instance(ccxt_cfg.ccxt_exchange, ccxt_cfg.ccxt_hostname)
        self.task = None  # Initialize task to None

    async def run_periodically(self, interval):
        while True:
            await asyncio.sleep(interval)
            await self.refresh_tickers()

    async def init_task(self):
        self.task = asyncio.create_task(self.run_periodically(refresh_interval))

    async def refresh_tickers(self):
        print(f"symbols_list: {self.symbols_list}")
        if self.symbols_list:
            self.ccxt_call_count += 1
            temp_tickers = ccxt_def.ccxt_call_fetch_tickers(self.ccxt_i, self.symbols_list, proxy=False)
            self.tickers = temp_tickers
            self.print_metrics()

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

    def print_metrics(self):
        exec_sec = time.time() - self.total_exec_time
        ccxt_cps = self.ccxt_call_count / exec_sec
        msg = f"exec_sec: {round(exec_sec, 2)} ccxt_cps: {round(ccxt_cps, 2)} ccxt_call_count: {self.ccxt_call_count} ccxt_cache_hit: {self.ccxt_cache_hit}"
        print(f"{bcolors.mycolor.OKGREEN}{msg}{bcolors.mycolor.ENDC}")

    async def handle(self, request):
        try:
            data = await request.json()
            response = await self.ccxt_call_fetch_tickers(*data['params'])
            return web.json_response({"jsonrpc": "2.0", "result": response, "id": data.get("id")})
        except Exception as e:
            error_response = {"jsonrpc": "2.0", "error": {"code": 500, "message": str(e)}, "id": None}
            return web.json_response(error_response, status=500)


def main():
    ccxt_server = CCXTServer()

    async def async_main():
        await ccxt_server.init_task()

        app = web.Application()
        app.router.add_post("/", ccxt_server.handle)
        web_task = web._run_app(app, host="localhost", port=2233)  # Use web._run_app instead of web.run_app
        await asyncio.gather(ccxt_server.task, web_task)

    asyncio.run(async_main())


if __name__ == "__main__":
    main()
