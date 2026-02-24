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
from definitions.detect_rpc import detect_rpc
from definitions.errors import RPCConfigError
from definitions.logger import setup_logging
from definitions.rpc import AsyncThreadingSemaphore, is_port_open, rpc_call


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
    _xbridge_fees_cache: dict[str, dict[str, Any] | None] = {}
    _xbridge_conf_lock = threading.Lock()

    @property
    def active_rpc_counter(self) -> int:
        return XBridgeManager._active_rpc_counter

    def __init__(
        self,
        config_manager: Any,
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
        self.xbridge_fees_estimate: dict[str, dict[str, Any] | None] = {}

        try:
            if XBridgeManager._rpc_config is None:
                with XBridgeManager._rpc_config_lock:
                    if XBridgeManager._rpc_config is None:
                        self.logger.info("Detecting RPC configuration.")
                        XBridgeManager._rpc_config = detect_rpc()

            (
                self.blocknet_user_rpc,
                self.blocknet_port_rpc,
                self.blocknet_password_rpc,
                self.blocknet_datadir_path,
            ) = XBridgeManager._rpc_config
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
                    return await rpc_call(
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

                    raise convert_exception(e) from e
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
        if final_shutdown_event and final_shutdown_event.is_set() and method not in [
            "dxCancelOrder",
            "dxGetMyOrders",
            "dxflushcancelledorders",
        ]:
            self.logger.debug(
                f"RPC call to {method} cancelled due to shutdown signal."
            )
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

    def calculate_xbridge_fees(self) -> None:
        """Calculate and store estimated XBridge transaction fees for each coin."""
        if not self.xbridge_conf:
            self.logger.error("Cannot calculate fees: xbridge.conf not loaded")
            return

        self.xbridge_fees_estimate = {}

        for coin, config in self.xbridge_conf.items():
            try:
                # Get the fee per byte and minimum transaction fee
                fee_per_byte = config.get("feeperbyte", 0)
                min_tx_fee = config.get("mintxfee", 0)

                # Estimate the fee for a typical transaction
                # We'll assume a typical transaction size of 500 bytes (this might need adjustment)
                typical_tx_size = 500
                estimated_fee = fee_per_byte * typical_tx_size

                # Ensure the fee doesn't go below the minimum
                estimated_fee = max(estimated_fee, min_tx_fee)

                # Convert to absolute value (in satoshis)
                estimated_fee_satoshis = estimated_fee

                # Convert to coin units (BTC, LTC, etc.)
                coin_units = config.get("coin", 100000000)
                estimated_fee_coin = estimated_fee_satoshis / coin_units

                self.xbridge_fees_estimate[coin] = {
                    "fee_per_byte": fee_per_byte,
                    "min_tx_fee": min_tx_fee,
                    "estimated_fee_satoshis": estimated_fee_satoshis,
                    "estimated_fee_coin": estimated_fee_coin,
                    "typical_tx_size": typical_tx_size,
                }
            except Exception as e:
                self.logger.error(
                    f"Error calculating fee estimate for {coin}: {e!s}"
                )
                self.xbridge_fees_estimate[coin] = None

        self.logger.info(
            f"XBridge fee estimates calculated for {len(self.xbridge_fees_estimate)} coins"
        )
        # self.logger.info(f"XBridge fee estimates: {self.xbridge_fees_estimate}")

    async def getnewtokenadress(self, token: str) -> Any:
        return await self.rpc_wrapper("dxGetNewTokenAddress", [token])

    async def getmyordersbymarket(self, maker: str, taker: str) -> list[dict[str, Any]]:
        myorders = await self.rpc_wrapper("dxGetMyOrders")
        return [
            zz for zz in myorders if (zz["maker"] == maker) and (zz["taker"] == taker)
        ]

    async def cancelorder(self, order_id: str, use_shutdown_event: bool = True) -> Any:
        return await self.rpc_wrapper(
            "dxCancelOrder", [order_id], use_shutdown_event=use_shutdown_event
        )

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

    async def gettokenbalances(self) -> Any:
        return await self.rpc_wrapper("dxgettokenbalances")

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
        takeramount: float,
        takeraddress: str,
        dryrun: bool | None = None,
    ) -> Any:
        if dryrun:
            result = await self.rpc_wrapper(
                "dxMakeOrder",
                [
                    maker,
                    makeramount,
                    makeraddress,
                    taker,
                    takeramount,
                    takeraddress,
                    "exact",
                    "dryrun",
                ],
            )
        else:
            result = await self.rpc_wrapper(
                "dxMakeOrder",
                [
                    maker,
                    makeramount,
                    makeraddress,
                    taker,
                    takeramount,
                    takeraddress,
                    "exact",
                ],
            )
        return result

    async def makepartialorder(
        self,
        maker: str,
        makeramount: float,
        makeraddress: str,
        taker: str,
        takeramount: float,
        takeraddress: str,
        min_size: float,
        repost: bool = False,
        dryrun: bool | None = None,
    ) -> Any:
        if dryrun:
            result = await self.rpc_wrapper(
                "dxMakePartialOrder",
                [
                    maker,
                    makeramount,
                    makeraddress,
                    taker,
                    takeramount,
                    takeraddress,
                    min_size,
                    repost,
                    "dryrun",
                ],
            )
        else:
            result = await self.rpc_wrapper(
                "dxMakePartialOrder",
                [
                    maker,
                    makeramount,
                    makeraddress,
                    taker,
                    takeramount,
                    takeraddress,
                    min_size,
                    repost,
                ],
            )
        return result

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
