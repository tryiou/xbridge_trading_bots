import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import ccxt

from definitions.rpc import rpc_call


class CCXTManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager  # Store ConfigManager reference
        self.proxy_process = None  # Initialize even if not used
        self.proxy_port = 2233  # Defined here for shutdown cleanup

    def init_ccxt_instance(self, exchange, hostname=None, private_api=False, debug_level=1):
        # CCXT instance
        api_key = None
        api_secret = None
        if private_api:
            with open(self.config_manager.ROOT_DIR + '/config/api_keys.local.json') as json_file:
                data_json = json.load(json_file)
                for data in data_json['api_info']:
                    if exchange in data['exchange']:
                        api_key = data['api_key']
                        api_secret = data['api_secret']
        if exchange in ccxt.exchanges:
            exchange_class = getattr(ccxt, exchange)
            if hostname:
                instance = exchange_class({
                    'apiKey': api_key,
                    'secret': api_secret,
                    'enableRateLimit': True,
                    'rateLimit': 1000,
                    'hostname': hostname,  # 'global.bittrex.com',
                })
            else:
                instance = exchange_class({
                    'apiKey': api_key,
                    'secret': api_secret,
                    'enableRateLimit': True,
                    'rateLimit': 1000,
                })
            done = False
            while not done:
                try:
                    # Run blocking load_markets in a thread pool executor
                    instance.load_markets()  # Directly call the blocking method
                except Exception as e:
                    self._manage_error(e)
                    exit()
                else:
                    done = True
            return instance
        else:
            return None

    async def ccxt_call_fetch_order_book(self, ccxt_o, symbol, limit=25, ignore_timer=False):
        update_cex_orderbook_timer_delay = 2
        if ignore_timer or not ccxt_o.cex_orderbook_timer or time.time() - ccxt_o.cex_orderbook_timer > update_cex_orderbook_timer_delay:
            self.cex_orderbook = await self._fetch_order_book(ccxt_o, symbol, limit)
            self.cex_orderbook_timer = time.time()
        return self.cex_orderbook

    async def _fetch_order_book(self, ccxt_o, symbol, limit):
        err_count = 0
        loop = asyncio.get_running_loop()
        while True:
            try:
                # Run blocking fetch_order_book in a thread pool executor
                result = await loop.run_in_executor(None, ccxt_o.fetch_order_book, symbol, limit)
            except Exception as error:
                err_count += 1
                self._manage_error(error, err_count)
            else:
                self._debug_display('ccxt_call_fetch_order_book', [symbol, limit], result)
                return result

    async def ccxt_call_fetch_free_balance(self, ccxt_o):
        err_count = 0
        loop = asyncio.get_running_loop()
        while True:
            try:
                # Run blocking fetch_free_balance in a thread pool executor
                result = await loop.run_in_executor(None, ccxt_o.fetch_free_balance)
            except Exception as error:
                err_count += 1
                self._manage_error(error, err_count)
            else:
                self._debug_display('ccxt_call_fetch_free_balance', [], result)
                return result

    async def ccxt_call_fetch_tickers(self, ccxt_o, symbols_list, proxy=True):
        start = time.time()
        err_count = 0

        # Start proxy if needed before first attempt
        if proxy and not self.isportopen("127.0.0.1", 2233) and not hasattr(self, 'proxy_started'):
            self._start_proxy()

        while True:
            try:
                used_proxy = False
                if self.isportopen("127.0.0.1", 2233) and proxy:  # CCXT PROXY
                    result = await rpc_call("ccxt_call_fetch_tickers", tuple(symbols_list), rpc_port=2233,
                                            debug=self.config_manager.config_ccxt.debug_level, display=False,
                                            logger=self.config_manager.general_log, timeout=60)
                    used_proxy = True
                else:
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(None, ccxt_o.fetchTickers, symbols_list)

                if result:
                    stop = time.time()
                    self._debug_display('ccxt_call_fetch_tickers',
                                        str(symbols_list) + ' used_proxy? ' + str(used_proxy),
                                        result,
                                        timer=stop - start)
                    return result
            except Exception as error:
                err_count += 1
                self._manage_error(error, err_count)

    async def ccxt_call_fetch_ticker(self, ccxt_o, symbol):
        err_count = 0
        loop = asyncio.get_running_loop()
        while True:
            try:
                result = await loop.run_in_executor(None, ccxt_o.fetch_ticker, symbol)
            except Exception as error:
                err_count += 1
                self._manage_error(error, err_count)
            else:
                self._debug_display('ccxt_call_fetch_ticker', [symbol], result)
                return result

    def isportopen(self, ip, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.connect((ip, int(port)))
            s.shutdown(2)
            return True
        except:
            return False

    def _start_proxy(self):
        """Start CCXT proxy - ONLY redirect outputs to void"""
        proxy_path = Path(__file__).parent.parent / "proxy_ccxt.py"
        self.proxy_port = 2233

        self.config_manager.ccxt_log.info(f"🚀 Starting CCXT proxy server on port {self.proxy_port}")
        try:
            self.proxy_process = subprocess.Popen(
                [sys.executable, str(proxy_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )

            max_retries = 5
            for attempt in range(1, max_retries + 1):
                time.sleep(attempt * 1)
                if self.isportopen("127.0.0.1", self.proxy_port):
                    self.proxy_started = True
                    self.config_manager.ccxt_log.info(
                        f"✅ Proxy started successfully (PID: {self.proxy_process.pid}, port: {self.proxy_port})"
                    )
                    time.sleep(0.5)  # Allow server initialization
                    return
                self.config_manager.ccxt_log.debug(f"Port check attempt {attempt}/{max_retries} failed")

            self.config_manager.ccxt_log.error(
                f"❌ Proxy failed to start - port {self.proxy_port} not responding after {max_retries} attempts")
            if self.proxy_process.stderr:
                stderr_output = self.proxy_process.stderr.read().decode().strip()
                if stderr_output:
                    self.config_manager.ccxt_log.error(f"Proxy error output:\n{stderr_output}")
        except Exception as e:
            self.config_manager.ccxt_log.error(f"Proxy startup failed: {str(e)}")
            self.proxy_process = None

    def _manage_error(self, error, err_count=1):
        err_type = type(error).__name__
        msg = f"parent: {str(sys._getframe(1).f_code.co_name)}, error: {str(type(error))}, {str(error)}, {str(err_type)}"
        self.config_manager.ccxt_log.error(msg)
        if err_type == "TimeoutError":  #
            sleep_time = min(err_count * 2, 10)
            self.config_manager.ccxt_log.warning(f"Timeout detected, retrying in {sleep_time}s")
            time.sleep(sleep_time)
        elif err_type in ["NetworkError", "DDoSProtection", "RateLimitExceeded", "InvalidNonce",
                          "RequestTimeout", "ExchangeNotAvailable", "Errno -3", "AuthenticationError",
                          "Temporary failure in name resolution", "ExchangeError", "BadResponse", "KeyError", "BadRequest"]:
            time.sleep(err_count * 1)
        else:
            time.sleep(err_count * 1)

    def _debug_display(self, func, params, result, timer=None):
        debug_level = self.config_manager.config_ccxt.debug_level
        if debug_level < 2:
            return

        if timer is None:
            timer = ''
        else:
            timer = " exec_timer: " + str(round(timer, 2))

        # Level 2: Log method name only
        if debug_level == 2:
            msg = f"ccxt_rpc_call( {func[10::]} ){timer}"
            self.config_manager.ccxt_log.info(msg)
        # Level 3: Log method and parameters
        elif debug_level >= 3:
            msg = f"ccxt_rpc_call( {func[10::]} {params} ){timer}"
            self.config_manager.ccxt_log.info(msg)

        # Level 4: Also log the full result
        if debug_level >= 4:
            self.config_manager.ccxt_log.debug(str(result))
