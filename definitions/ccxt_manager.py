import asyncio
import atexit  # Add for exit handler
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import ccxt

from definitions.error_handler import ErrorHandler, TransientError, OperationalError, CriticalError
from definitions.errors import RPCConfigError
from definitions.rpc import rpc_call, is_port_open


class CCXTManager:
    # Class-level variables for shared proxy state
    _proxy_process = None
    _proxy_port = 2233
    _proxy_lock = threading.Lock()
    _proxy_ref_count = 0  # Track active strategies using proxy

    @classmethod
    def register_strategy(cls):
        """Call whenever a strategy starts"""
        with cls._proxy_lock:
            cls._proxy_ref_count += 1

    @classmethod
    def unregister_strategy(cls):
        """Call when strategy stops"""
        with cls._proxy_lock:
            if cls._proxy_ref_count > 0:
                cls._proxy_ref_count -= 1
                if cls._proxy_ref_count == 0 and cls._proxy_process:
                    # Use a thread for termination since it might block
                    if cls._proxy_process.poll() is None:
                        threading.Thread(target=cls._cleanup_proxy, name="ProxyCleanup").start()
                    else:
                        cls._proxy_process = None
                # Ensure we clear ref count if proxy already dead
                elif cls._proxy_ref_count == 0:
                    cls._proxy_process = None
            # Safety: if ref count hits negative (shouldn't happen), reset to 0
            if cls._proxy_ref_count < 0:
                cls._proxy_ref_count = 0

    def __init__(self, config_manager):
        self.config_manager = config_manager  # Store ConfigManager reference
        # Instance doesn't need its own proxy_process reference
        self.error_handler = ErrorHandler(config_manager, logger=self.config_manager.ccxt_log)

    @classmethod
    def _cleanup_proxy(cls):
        """Terminate proxy process with robust cleanup handling."""
        # Skip cleanup if strategies are still running
        if cls._proxy_ref_count > 0:
            logging.getLogger('ccxt_manager').info(
                f"[PROXY.MAINTENANCE] Skipping cleanup - {cls._proxy_ref_count} running strategies"
            )
            return

        with cls._proxy_lock:
            if cls._proxy_process is None:
                logging.getLogger('ccxt_manager').info(
                    "[PROXY.MAINTENANCE] No active proxy process to terminate"
                )
                return

            try:
                if cls._proxy_process.poll() is None:
                    logging.getLogger('ccxt_manager').info(
                        "[PROXY.MAINTENANCE] Terminating proxy process..."
                    )
                    # Try graceful termination first
                    cls._proxy_process.terminate()

                    # Give it time to terminate
                    try:
                        cls._proxy_process.wait(timeout=5.0)
                        logging.getLogger('ccxt_manager').info(
                            "[PROXY.MAINTENANCE] Proxy terminated gracefully"
                        )
                    except (subprocess.TimeoutExpired, TimeoutError):
                        # Force kill if it didn't terminate
                        logging.getLogger('ccxt_manager').info(
                            "[PROXY.MAINTENANCE] Forcing proxy termination after 5s timeout"
                        )
                        cls._proxy_process.kill()
                        try:
                            cls._proxy_process.wait(timeout=2.0)
                            logging.getLogger('ccxt_manager').info(
                                "[PROXY.MAINTENANCE] Proxy killed successfully"
                            )
                        except (subprocess.TimeoutExpired, TimeoutError):
                            logging.getLogger('ccxt_manager').info(
                                "[PROXY.MAINTENANCE] Proxy could not be killed - proceeding anyway"
                            )
            except Exception as e:
                logging.getLogger('ccxt_manager').error(
                    f"[PROXY.MAINTENANCE] Error during termination: {str(e)}"
                )
            finally:
                cls._proxy_process = None
                logging.getLogger('ccxt_manager').info(
                    "[PROXY.MAINTENANCE] Proxy cleared from state"
                )

    def init_ccxt_instance(self, exchange, hostname=None, private_api=False, debug_level=1):
        # CCXT instance
        api_key = None
        api_secret = None
        if private_api:
            try:
                with open(self.config_manager.ROOT_DIR + '/config/api_keys.local.json') as json_file:
                    data_json = json.load(json_file)
                    for data in data_json['api_info']:
                        if exchange in data['exchange']:
                            api_key = data['api_key']
                            api_secret = data['api_secret']
            except Exception as e:
                self.error_handler.handle(
                    OperationalError(f"API keys load failed: {str(e)}",
                                     {"exchange": exchange, "file": "api_keys.local.json"}),
                    context={"method": "init_ccxt_instance"}
                )
                return None

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
                    self.error_handler.handle(
                        TransientError(f"Exchange initialization failed: {str(e)}",
                                       {"exchange": exchange}),
                        context={"method": "init_ccxt_instance"}
                    )
                    # Continue retrying unless it's a critical error
                    if isinstance(e, CriticalError):
                        raise RPCConfigError("No valid Blocknet Core Config path found.", context={"context": str(e)})
                else:
                    done = True
            return instance
        else:
            self.config_manager.ccxt_log.error(f"Unsupported exchange: {exchange}")
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
                context = {
                    "method": "_fetch_order_book",
                    "symbol": symbol,
                    "limit": limit,
                    "err_count": err_count
                }
                if not self.error_handler.handle(
                        TransientError(str(error), {"type": type(error).__name__}),
                        context=context
                ):
                    return None  # Abort on critical error
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
                context = {
                    "method": "ccxt_call_fetch_free_balance",
                    "err_count": err_count
                }
                if not self.error_handler.handle(
                        TransientError(str(error), {"type": type(error).__name__}),
                        context=context
                ):
                    return None
            else:
                self._debug_display('ccxt_call_fetch_free_balance', [], result)
                return result

    async def ccxt_call_fetch_tickers(self, ccxt_o, symbols_list, proxy=True):
        start = time.time()
        err_count = 0

        # Start proxy if needed before first attempt
        if proxy and not is_port_open("127.0.0.1", 2233) and not hasattr(self, 'proxy_started'):
            self._start_proxy()

        while True:
            try:
                used_proxy = False
                if is_port_open("127.0.0.1", 2233) and proxy:  # CCXT PROXY
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
                context = {
                    "method": "ccxt_call_fetch_tickers",
                    "symbols": symbols_list,
                    "proxy_used": proxy,
                    "err_count": err_count
                }
                if not self.error_handler.handle(
                        TransientError(str(error), {"type": type(error).__name__}),
                        context=context
                ):
                    return None

    async def ccxt_call_fetch_ticker(self, ccxt_o, symbol):
        err_count = 0
        loop = asyncio.get_running_loop()
        while True:
            try:
                result = await loop.run_in_executor(None, ccxt_o.fetch_ticker, symbol)
            except Exception as error:
                err_count += 1
                context = {
                    "method": "ccxt_call_fetch_ticker",
                    "symbol": symbol,
                    "err_count": err_count
                }
                if not self.error_handler.handle(
                        TransientError(str(error), {"type": type(error).__name__}),
                        context=context
                ):
                    return None
            else:
                self._debug_display('ccxt_call_fetch_ticker', [symbol], result)
                return result

    def _start_proxy(self):
        """Start shared CCXT proxy with process coordination"""
        logger = logging.getLogger('ccxt_manager')
        strategy_log = self.config_manager.ccxt_log  # Strategy-specific logger

        with CCXTManager._proxy_lock:
            if is_port_open("127.0.0.1", CCXTManager._proxy_port):
                log_msg = (f"[PROXY.STARTUP] Proxy port occupied already - "
                           f"pid: {CCXTManager._proxy_process.pid if CCXTManager._proxy_process else 'unknown'}")
                strategy_log.info(log_msg)
                logger.info(log_msg)
                return

            proxy_path = Path(__file__).parent.parent / "proxy_ccxt.py"

            startup_msg = f"[PROXY.STARTUP] Initiating proxy on port {CCXTManager._proxy_port}"
            strategy_log.info(startup_msg)
            logger.info(startup_msg)

            try:
                start_cmd = [sys.executable, str(proxy_path)]
                CCXTManager._proxy_process = subprocess.Popen(
                    start_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
                )
                logger.info(f"[PROXY.STARTUP] Command: {' '.join(start_cmd)} PID={CCXTManager._proxy_process.pid}")

                # Verify proxy started properly
                proxy_started = False
                for attempt in range(3):
                    wait_sec = 2 * (attempt + 1)
                    time.sleep(wait_sec)
                    port_status = is_port_open("127.0.0.1", CCXTManager._proxy_port)
                    logger.info(
                        f"[PROXY.STARTUP] Port check attempt {attempt + 1}/3: {'open' if port_status else 'closed'}")

                    if port_status:
                        ready_msg = f"Proxy operational (PID {CCXTManager._proxy_process.pid})"
                        strategy_log.info(ready_msg)
                        logger.info(f"[PROXY.STARTUP] {ready_msg}")
                        proxy_started = True
                        break

                if not proxy_started:
                    failure_msg = f"Proxy failed to start after 3 checks"
                    strategy_log.error(failure_msg)
                    logger.error(f"[PROXY.STARTUP] {failure_msg}")
                    if CCXTManager._proxy_process.poll() is None:
                        logger.info("[PROXY.STARTUP] Killing stalled proxy process")
                        CCXTManager._proxy_process.kill()
                    CCXTManager._proxy_process = None
            except Exception as e:
                error_detail = f"Startup error: {str(e)}"
                strategy_log.error(error_detail)
                logger.exception(f"[PROXY.STARTUP] {error_detail}")
                if CCXTManager._proxy_process and CCXTManager._proxy_process.poll() is None:
                    logger.info("[PROXY.STARTUP] Killing process after startup error")
                    CCXTManager._proxy_process.kill()
                CCXTManager._proxy_process = None
                # Propagate error to handler
                self.error_handler.handle(
                    CriticalError(f"Proxy startup failed: {str(e)}"),
                    context={"stage": "proxy_startup"}
                )

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


# Register proxy cleanup function to be called on exit
atexit.register(CCXTManager._cleanup_proxy)
