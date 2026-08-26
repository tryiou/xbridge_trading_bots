import asyncio
import configparser
import logging
import os
import threading
import time
import uuid
import weakref
from typing import Any

from definitions.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerError,
)
from definitions.constants import POOL_FILE_TEMPLATE
from definitions.errors import RPCConfigError
from definitions.logger import setup_logging
from definitions.order_id_pool import OrderIdPool
from definitions.rpc import (
    AsyncThreadingSemaphore,
    is_port_open,
    rpc_call as _rpc_call_module,
)


class XBridgeManager:
    _loops: weakref.WeakSet = weakref.WeakSet()
    _active_rpc_counter: int = 0
    _rpc_counter_lock = threading.Lock()
    _rpc_semaphore: AsyncThreadingSemaphore | None = None
    _utxo_cache: dict[str, tuple[float, Any]] = {}
    _utxo_cache_lock = threading.Lock()
    UTXO_CACHE_DURATION: float = 3.0
    _rpc_config: tuple[str, int, str, str] | None = None
    _rpc_config_lock = threading.Lock()
    _xbridge_conf_cache: dict[str, dict[str, Any]] | None = None
    _xbridge_conf_lock = threading.Lock()

    @property
    def active_rpc_counter(self) -> int:
        return XBridgeManager._active_rpc_counter

    def __init__(
        self,
        config_manager: Any,
        rpc_config: tuple[str, int, str, str],
        logger: logging.Logger | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        rpc_semaphore: AsyncThreadingSemaphore | None = None,
    ) -> None:
        self.config_manager: Any = config_manager
        strategy = (
            self.config_manager.strategy
            if hasattr(config_manager, "strategy")
            else "no_strat"
        )
        self.logger: logging.Logger = logger or setup_logging(
            name=f"{strategy}.xbridge_manager", level=logging.DEBUG, console=True
        )
        self._provided_logger = logger is not None

        self.blocknet_user_rpc: str
        self.blocknet_port_rpc: int
        self.blocknet_password_rpc: str
        self.blocknet_datadir_path: str
        self.xbridge_conf: dict[str, dict[str, Any]] | None = None

        # Per-session historic notebook: only THIS instance's posted ids live
        # here, so a checkup can never touch another session's orders.
        self.order_pool = OrderIdPool(
            file_path=POOL_FILE_TEMPLATE.format(strategy=strategy)
        )

        try:
            (
                self.blocknet_user_rpc,
                self.blocknet_port_rpc,
                self.blocknet_password_rpc,
                self.blocknet_datadir_path,
            ) = rpc_config
        except RPCConfigError as e:
            self.logger.critical(f"Failed to initialize RPC: {e!s}")
            raise

        if rpc_semaphore is not None:
            self._rpc_semaphore = rpc_semaphore
        elif XBridgeManager._rpc_semaphore is None:
            max_tasks = 5
            try:
                max_tasks = self.config_manager.config_xbridge.max_concurrent_tasks
            except AttributeError:
                self.logger.info(
                    f"Falling back to default max_concurrent_tasks ({max_tasks})"
                )
            XBridgeManager._rpc_semaphore = AsyncThreadingSemaphore(max_tasks)

        self._rpc_semaphore = (
            rpc_semaphore
            if rpc_semaphore is not None
            else XBridgeManager._rpc_semaphore
        )

        # Check if RPC port is open (synchronous check)
        if not is_port_open("127.0.0.1", self.blocknet_port_rpc):
            self.logger.error(
                f"Blocknet RPC port {self.blocknet_port_rpc} is not open. Will not be able to connect to Blocknet Core."
            )
        else:
            self.logger.info(f"Blocknet RPC port {self.blocknet_port_rpc} is open.")

        # Only run test if port is actually open and we're not in main thread
        if threading.current_thread() is not threading.main_thread() and is_port_open(
            "127.0.0.1", self.blocknet_port_rpc
        ):
            asyncio.run(self.async_test_rpc())

        # Initialize circuit breaker for RPC calls
        cb_config = CircuitBreakerConfig(
            failure_threshold=5, success_threshold=2, timeout=30.0
        )
        self.circuit_breaker = CircuitBreaker(
            name=f"xbridge_rpc_{strategy}", config=cb_config, logger=self.logger
        )
        self._rpc_call = _rpc_call_module

    async def _execute_rpc_call(
        self,
        method: str,
        params: list[Any],
        final_shutdown_event: asyncio.Event | None,
    ) -> Any:
        """Execute the actual RPC call. Used by circuit breaker."""
        async with XBridgeManager._rpc_semaphore:
            with XBridgeManager._rpc_counter_lock:
                XBridgeManager._active_rpc_counter += 1

            try:
                try:
                    return await self._rpc_call(
                        method=method,
                        params=params,
                        rpc_user=self.blocknet_user_rpc,
                        rpc_password=self.blocknet_password_rpc,
                        rpc_port=self.blocknet_port_rpc,
                        debug=self.config_manager.config_xbridge.debug_level,
                        logger=self.logger,
                        session=None,
                        shutdown_event=final_shutdown_event,
                        error_handler=getattr(
                            self.config_manager, "error_handler", None
                        ),
                    )
                except Exception as e:
                    from definitions.errors import convert_exception

                    converted = convert_exception(e)
                    if hasattr(converted, "context"):
                        converted.context = {
                            **converted.context,
                            "xbridge_method": method,
                            "xbridge_params": params,
                        }
                    raise converted from e
            finally:
                with XBridgeManager._rpc_counter_lock:
                    XBridgeManager._active_rpc_counter -= 1

    async def rpc_wrapper(
        self,
        method: str,
        params: list[Any] | None = None,
        shutdown_event: asyncio.Event | None = None,
        use_shutdown_event: bool = True,
    ) -> Any:
        """Execute RPC call with context tracking and optional shutdown event"""
        final_shutdown_event = shutdown_event
        if use_shutdown_event and shutdown_event is None:
            if self.config_manager and self.config_manager.controller:
                final_shutdown_event = self.config_manager.controller.shutdown_event
        elif not use_shutdown_event:
            final_shutdown_event = None

        # Early bailout if shutdown is signaled, with exceptions for cleanup RPCs
        if (
            final_shutdown_event
            and final_shutdown_event.is_set()
            and method
            not in [
                "dxCancelOrder",
                "dxGetMyOrders",
                "dxflushcancelledorders",
            ]
        ):
            self.logger.debug(f"RPC call to {method} cancelled due to shutdown signal.")
            return None

        # Execute with circuit breaker protection
        try:
            return await self.circuit_breaker.call(
                self._execute_rpc_call, method, params or [], final_shutdown_event
            )
        except CircuitBreakerError as e:
            self.logger.warning(f"Circuit breaker open for {method}: {e}")
            return None

    async def async_test_rpc(self) -> bool:
        """Perform RPC connection test asynchronously with cancellation handling"""
        try:
            result = await self.rpc_wrapper("getwalletinfo")
            if result:
                self.logger.info(
                    f"XBridge RPC connection successful: getwalletinfo returned {result}"
                )
                return True
            return False
        except asyncio.CancelledError:
            self.logger.warning("RPC test cancelled during shutdown")
            return False
        except Exception as e:
            self.logger.error(f"XBridge RPC connection failed: {e}")
            return False

    def parse_xbridge_conf(self) -> None:
        """Parse the xbridge.conf file and store the configuration in self.xbridge_conf."""
        if not self.blocknet_datadir_path:
            self.logger.error(
                "No Blocknet datadir path found, cannot parse xbridge.conf"
            )
            return

        conf_path = os.path.join(self.blocknet_datadir_path, "xbridge.conf")
        if not os.path.exists(conf_path):
            self.logger.error(f"xbridge.conf not found at {conf_path}")
            return

        config = configparser.ConfigParser()
        try:
            config.read(conf_path)
            self.xbridge_conf = {}

            # Get all supported coins (sections after [Main])
            for section in config.sections():
                if section == "Main":
                    continue

                # Skip if the section is not a coin (unlikely, but just in case)
                if not section.isupper() or not section.isalpha():
                    continue

                coin = section
                self.xbridge_conf[coin] = {}

                # Get all key-value pairs in the section
                for key, value in config.items(section):
                    # Convert numeric values to appropriate types
                    # self.logger.info(f"key: {key}, value: {value}")
                    if key in [
                        "coin",
                        "minimumamount",
                        "dustamount",
                        "txversion",
                        "blocktime",
                        "feeperbyte",
                        "mintxfee",
                        "confirmations",
                        "addressprefix",
                        "scriptprefix",
                        "secretprefix",
                    ]:
                        self.xbridge_conf[coin][key] = int(value)
                    elif key in [
                        "getnewkeysupported",
                        "importwithnoscansupported",
                        "lockcoinssupported",
                        "txwithtimefield",
                    ]:
                        self.xbridge_conf[coin][key] = bool(value)
                    else:
                        self.xbridge_conf[coin][key] = value

            self.logger.info(
                f"Successfully parsed xbridge.conf with {len(self.xbridge_conf)} coins"
            )
            # self.logger.info(f"Parsed xbridge.conf {self.xbridge_conf}")
        except Exception as e:
            self.logger.error(f"Error parsing xbridge.conf: {e!s}")
            self.xbridge_conf = None

    async def getnewtokenadress(self, token: str) -> Any:
        return await self.rpc_wrapper("dxGetNewTokenAddress", [token])

    async def cancelorder(self, order_id: str, use_shutdown_event: bool = True) -> Any:
        return await self.rpc_wrapper(
            "dxCancelOrder", [order_id], use_shutdown_event=use_shutdown_event
        )

    async def getmyorders(self) -> Any:
        """All own orders of the current daemon session (dxGetMyOrders)."""
        return await self.rpc_wrapper("dxGetMyOrders")

    async def cancelallorders(self, use_shutdown_event: bool = True) -> list[str]:
        myorders = await self.rpc_wrapper(
            "dxGetMyOrders", use_shutdown_event=use_shutdown_event
        )
        successful = []
        failed = []

        if not myorders:
            self.logger.info("No orders to cancel or failed to retrieve orders.")
            return successful

        tasks = []
        orders_to_cancel = []
        for order in myorders:
            if order["status"] in ("open", "new"):
                orders_to_cancel.append(order["id"])
                tasks.append(
                    self.cancelorder(order["id"], use_shutdown_event=use_shutdown_event)
                )

        if not tasks:
            self.logger.info("No open orders found to cancel.")
            return successful

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for order_id, res in zip(orders_to_cancel, results, strict=False):
            if isinstance(res, Exception):
                failed.append(order_id)
                self.logger.error(f"Failed to cancel order {order_id}: {res}")
            else:
                successful.append(order_id)

        if failed:
            self.logger.critical(
                f"Partially canceled: {len(successful)} ok, {len(failed)} failed: {failed}"
            )

        return successful

    async def dxloadxbridgeconf(self) -> None:
        await self.rpc_wrapper("dxloadxbridgeconf")

    async def dxflushcancelledorders(self) -> Any:
        return await self.rpc_wrapper("dxflushcancelledorders")

    async def gettokenutxo(self, token: str, used: bool = False) -> Any:
        cache_key = f"{token}_{used}"
        current_time = time.time()

        # Check cache under class lock (thread-safe)
        with XBridgeManager._utxo_cache_lock:
            cached_data = XBridgeManager._utxo_cache.get(cache_key)
            if (
                cached_data
                and (current_time - cached_data[0]) < XBridgeManager.UTXO_CACHE_DURATION
            ):
                self.logger.debug(
                    f"Returning class-level cached UTXO data for {token} (used={used})"
                )
                return cached_data[1]

        # If not cached or expired, make the RPC call
        result = await self.rpc_wrapper("dxgetutxos", [token, used])

        with XBridgeManager._utxo_cache_lock:
            XBridgeManager._utxo_cache[cache_key] = (time.time(), result)
            # self.logger.debug(f"Cached new class-level UTXO data for {token} (used={used})")
        return result

    async def getlocaltokens(self) -> Any:
        return await self.rpc_wrapper("dxgetlocaltokens")

    async def makeorder(
        self,
        maker: str,
        makeramount: float,
        makeraddress: str,
        taker: str,
        takeramount: str | float,
        takeraddress: str,
    ) -> Any:
        result = await self.rpc_wrapper(
            "dxMakeOrder",
            [
                str(maker),
                str(makeramount),
                str(makeraddress),
                str(taker),
                str(takeramount),
                str(takeraddress),
                "exact",
            ],
        )
        self._record_posted_order(result, maker, taker)
        return result

    async def makepartialorder(
        self,
        maker: str,
        makeramount: float,
        makeraddress: str,
        taker: str,
        takeramount: str | float,
        takeraddress: str,
        min_size: float,
        repost: bool = False,
    ) -> Any:
        result = await self.rpc_wrapper(
            "dxMakePartialOrder",
            [
                str(maker),
                str(makeramount),
                str(makeraddress),
                str(taker),
                str(takeramount),
                str(takeraddress),
                str(min_size),
                repost,
            ],
        )
        self._record_posted_order(result, maker, taker)
        return result

    def _record_posted_order(self, result: Any, maker: str, taker: str) -> None:
        """Remember a successfully posted order id in the historic pool."""
        if isinstance(result, dict) and result.get("id"):
            try:
                self.order_pool.add(str(result["id"]), f"{maker}/{taker}")
                self.order_pool.flush()
            except Exception as e:  # pool must never break order flow
                self.logger.warning("Could not record posted order id: %s", e)

    async def getorderstatus(self, oid: str) -> Any:
        return await self.rpc_wrapper("dxGetOrder", [oid])

    async def dxgetorderbook(self, detail: int, maker: str, taker: str) -> Any:
        return await self.rpc_wrapper("dxgetorderbook", [detail, maker, taker])

    async def take_order(
        self, order_id: str, from_address: str, to_address: str, test_mode: bool = False
    ) -> dict[str, str] | None:
        """Takes an XBridge order using dxTakeOrder."""
        self.logger.info(
            f"Attempting to take XBridge order {order_id} from {from_address} to {to_address}"
        )

        if test_mode:
            mock_result = {
                "id": f"mock_xbridge_txid_{uuid.uuid4()}",
                "status": "created",
            }
            self.logger.info("[TEST MODE] Would execute BLOCKNET WALLET RPC CALL:")
            self.logger.info(f"    - RPC Port: {self.blocknet_port_rpc}")
            self.logger.info("    - Method: dxTakeOrder")
            self.logger.info(
                f"    - Params: ['{order_id}', '{from_address}', '{to_address}']"
            )
            self.logger.info(f"    - Returning mock result: {mock_result}")
            return mock_result

        try:
            result = await self.rpc_wrapper(
                "dxTakeOrder", [order_id, from_address, to_address]
            )
            self.logger.info(
                f"Successfully took XBridge order {order_id}. Result: {result}"
            )
            return result
        except Exception as e:
            self.logger.error(
                f"Failed to take XBridge order {order_id}. Error: {e}", exc_info=True
            )
            return None
