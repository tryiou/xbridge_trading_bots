import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import ccxt

from definitions.error_handler import ErrorHandler, TransientError, OperationalError
from definitions.errors import RPCConfigError, CriticalError
from definitions.rpc import rpc_call, is_port_open


class CCXTManager:
    # Class-level variables for shared proxy state
    _proxy_process = None
    _proxy_port = 2233
    _proxy_lock = threading.Lock()
    _proxy_ref_count = 0  # Track active strategies using proxy

    # Class-level logger for proxy events
    _proxy_logger = logging.getLogger('ccxt_manager.proxy')

    @classmethod
    def register_strategy(cls):
        """Call whenever a strategy starts"""
        with cls._proxy_lock:
            cls._proxy_ref_count += 1
            cls._proxy_logger.debug(
                f"Strategy registered. New refcount: {cls._proxy_ref_count} "
                f"(Process: {getattr(cls._proxy_process, 'pid', 'None')})"
            )

    @classmethod
    def unregister_strategy(cls):
        """Call when strategy stops"""
        with cls._proxy_lock:
            if cls._proxy_ref_count > 0:
                cls._proxy_ref_count -= 1
                new_refcount = cls._proxy_ref_count
                cls._proxy_logger.debug(
                    f"Strategy unregistered. New refcount: {new_refcount} "
                    f"(Process: {getattr(cls._proxy_process, 'pid', 'None')})"
                )
            else:
                cls._proxy_logger.warning(f"unregister_strategy called with refcount <= 0")
                new_refcount = 0
            
            # Trigger cleanup when refcount reaches zero
            if new_refcount == 0 and cls._proxy_process:
                if cls._proxy_process.poll() is None:
                    cls._proxy_logger.debug("Scheduling proxy cleanup")
                    threading.Thread(target=cls._cleanup_proxy, name="ProxyCleanup").start()
                else:
                    cls._proxy_logger.debug("Proxy already dead - clearing state")
                    cls._proxy_process = None

    def __init__(self, config_manager):
        self.config_manager = config_manager  # Store ConfigManager reference
        # Instance doesn't need its own proxy_process reference
        self.error_handler = ErrorHandler(config_manager, logger=self.config_manager.ccxt_log)
        self.logger = self.config_manager.ccxt_log  
        
    @classmethod
    def _cleanup_proxy(cls):
        """Coordinate proxy termination only when no strategies are running"""
        current_refcount = None
        with cls._proxy_lock:
            current_refcount = cls._proxy_ref_count
            # Double-check refcount under lock
            if cls._proxy_ref_count > 0:
                cls._proxy_logger.info( 
                    f"[PROXY.MAINTENANCE] Aborting cleanup - strategies still running: refcount={cls._proxy_ref_count}"
                )
                return
                
            if cls._proxy_process is None:
                cls._proxy_logger.info( 
                    "[PROXY.MAINTENANCE] Proxy process already terminated"
                )
                return
                
            try:
                if cls._proxy_process.poll() is None:
                    cls._proxy_logger.info( 
                        f"[PROXY.MAINTENANCE] Terminating proxy process (refcount={current_refcount})..."
                    )
                    cls._proxy_process.terminate()
                    try:
                        cls._proxy_process.wait(timeout=5.0)
                    except (subprocess.TimeoutExpired, TimeoutError):
                        cls._proxy_logger.info( 
                            "[PROXY.MAINTENANCE] Forcing proxy termination after 5s timeout"
                        )
                        cls._proxy_process.kill()
                        try:
                            cls._proxy_process.wait(timeout=1.0)
                        except (subprocess.TimeoutExpired, TimeoutError):
                            pass
                            
                    if cls._proxy_process.poll() is None:
                        cls._proxy_logger.warning( 
                            "[PROXY.MAINTENANCE] Proxy failed to terminate after kill"
                        )
                    else:
                        cls._proxy_logger.info( 
                            "[PROXY.MAINTENANCE] Proxy terminated successfully"
                        )
                else:
                    cls._proxy_logger.info( 
                        "[PROXY.MAINTENANCE] Proxy process already dead"
                    )
            except Exception as e:
                cls._proxy_logger.error( 
                    f"[PROXY.MAINTENANCE] Error during termination: {str(e)}"
                )
            finally:
                cls._proxy_process = None
                cls._proxy_logger.info( 
                    "[PROXY.MAINTENANCE] Proxy state cleared"
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
            self.logger.error(f"Unsupported exchange: {exchange}")
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
                                            debug=self.config_manager.config_ccxt.debug_level,
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
        with CCXTManager._proxy_lock:
            if is_port_open("127.0.0.1", CCXTManager._proxy_port):
                log_msg = (f"[PROXY.STARTUP] Proxy port occupied already - "
                           f"pid: {CCXTManager._proxy_process.pid if CCXTManager._proxy_process else 'unknown'}")
                CCXTManager._proxy_logger.info(log_msg)
                return

            proxy_path = Path(__file__).parent.parent / "proxy_ccxt.py"

            startup_msg = f"[PROXY.STARTUP] Initiating proxy on port {CCXTManager._proxy_port}"
            CCXTManager._proxy_logger.info(startup_msg)

            try:
                start_cmd = [sys.executable, str(proxy_path)]
                CCXTManager._proxy_process = subprocess.Popen(
                    start_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
                )
                CCXTManager._proxy_logger.info(f"[PROXY.STARTUP] Command: {' '.join(start_cmd)} PID={CCXTManager._proxy_process.pid}")

                # Verify proxy started properly
                proxy_started = False
                for attempt in range(3):
                    wait_sec = 2 * (attempt + 1)
                    time.sleep(wait_sec)
                    port_status = is_port_open("127.0.0.1", CCXTManager._proxy_port)
                    CCXTManager._proxy_logger.info(
                        f"[PROXY.STARTUP] Port check attempt {attempt + 1}/3: {'open' if port_status else 'closed'}")

                    if port_status:
                        ready_msg = f"Proxy operational (PID {CCXTManager._proxy_process.pid})"
                        CCXTManager._proxy_logger.info(ready_msg)
                        proxy_started = True
                        break

                if not proxy_started:
                    failure_msg = f"Proxy failed to start after 3 checks"
                    CCXTManager._proxy_logger.error(failure_msg)
                    if CCXTManager._proxy_process.poll() is None:
                        CCXTManager._proxy_logger.info("[PROXY.STARTUP] Killing stalled proxy process")
                        CCXTManager._proxy_process.kill()
                    CCXTManager._proxy_process = None
            except Exception as e:
                error_detail = f"Startup error: {str(e)}"
                CCXTManager._proxy_logger.error(error_detail)
                CCXTManager._proxy_logger.exception(f"[PROXY.STARTUP] {error_detail}")
                if CCXTManager._proxy_process and CCXTManager._proxy_process.poll() is None:
                    CCXTManager._proxy_logger.info("[PROXY.STARTUP] Killing process after startup error")
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
            self.logger.info(msg)
        # Level 3: Log method and parameters
        elif debug_level >= 3:
            msg = f"ccxt_rpc_call( {func[10::]} {params} ){timer}"
            self.logger.info(msg)

        # Level 4: Also log the full result
        if debug_level >= 4:
            self.logger.debug(str(result))
