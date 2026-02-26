"""
Fake XBridge RPC server for backtesting - HTTP JSON-RPC server.

IMPORTANT: This is a REPLICA of the real XBridge server (http://127.0.0.1:41414). use it if you want to control real server response.
 Example:
curl --location 'http://127.0.0.1:41414' \
--header 'Content-Type: application/json' \
--header 'Authorization: Basic QnBZeGpVTmV5OU03UmhlV0VPUnF2QzN2a0lxcXZNbjk6OUF3YmFzWVRDZXFhcU1EUW9zWm5rS3c0YmtTa3R2S0E=' \
--data '{
    "method": "dxgettokenbalances"
}'
This file MUST exactly match the real server's behavior and response format,
including any inconsistencies. The real server is the source of truth.

Real server format verification:
- Success: {"result": {...}, "error": null, "id": ...}
- Known errors: {"result": {"error": "...", "code": ..., "name": "MethodName"}, "error": null, "id": ...}
- Unknown method: {"result": null, "error": {"code": -32601, "message": "Method not found"}, "id": ...}
"""

import logging
from datetime import datetime
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)


class FakeXBridgeRPCServer:
    """
    HTTP JSON-RPC server that mimics XBridge protocol.

    The bot connects to this server thinking it's the real XBridge.
    This replicates ONLY the XBridge server behavior - no backtesting logic.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8332):
        self.host = host
        self.port = port

        self.app = None
        self.runner = None
        self.site = None
        self.orders: dict[str, dict] = {}
        self.balances: dict[str, str] = {}
        self.base_token: str = ""
        self.quote_token: str = ""

        self._order_counter = 0
        self._running = False
        self._token_addresses: dict[str, str] = {}
        self._methods: dict = {}

        self._register_methods()

    def _register_methods(self):
        self._methods = {
            "dxgettokenbalances": self._gettokenbalances,
            "dxGetOrder": self._getorderstatus,
            "dxMakeOrder": self._makeorder,
            "dxMakePartialOrder": self._makepartialorder,
            "dxCancelOrder": self._cancelorder,
            "dxGetMyOrders": self._getorders,
            "dxflushcancelledorders": self._dxflushcancelledorders,
            "dxloadxbridgeconf": self._dxloadxbridgeconf,
            "dxGetNewTokenAddress": self._getnewtokenaddress,
        }

    def set_tokens(self, base_token: str, quote_token: str):
        self.base_token = base_token
        self.quote_token = quote_token

    def set_balances(self, base_balance: float, quote_balance: float):
        self.balances = {
            self.base_token: str(base_balance),
            self.quote_token: str(quote_balance),
        }
        self.available_balances = {
            self.base_token: float(base_balance),
            self.quote_token: float(quote_balance),
        }

    def _generate_order_id(self) -> str:
        self._order_counter += 1
        # Use UUID-like format like real server
        import uuid

        return uuid.uuid4().hex

    async def _gettokenbalances(self, params: Any) -> dict:
        return dict(self.balances)

    async def _makeorder(self, params: list) -> dict:
        """dxMakeOrder - non-partial order."""
        if len(params) < 7:
            return {
                "error": "Invalid parameters",
                "code": -1,
                "name": "dxMakeOrder",
            }

        maker = params[0]
        makeramount = params[1]
        makeraddress = params[2]
        taker = params[3]
        takeramount = params[4]
        takeraddress = params[5]
        order_type = params[6] if len(params) > 6 else "exact"

        try:
            makeramount = float(makeramount)
            takeramount = float(takeramount)
        except (ValueError, TypeError):
            return {"error": "Invalid amount", "code": -1, "name": "dxMakeOrder"}

        if makeramount <= 0 or takeramount <= 0:
            return {"error": "Invalid amount", "code": -1, "name": "dxMakeOrder"}

        if not makeraddress or not takeraddress:
            return {
                "error": f"Bad address {makeraddress or takeraddress}",
                "code": 1026,
                "name": "dxMakeOrder",
            }

        if makeraddress == takeraddress:
            return {
                "error": f"Invalid parameters: The maker_address and the taker_address cannot be the same: {makeraddress}",
                "code": 1025,
                "name": "dxMakeOrder",
            }

        available = self.available_balances.get(maker, 0)
        if available < makeramount:
            return {
                "error": f"Insufficient balance for {maker}: have {available}, need {makeramount}",
                "code": 1026,
                "name": "dxMakeOrder",
            }

        self.available_balances[maker] = available - makeramount

        order_id = self._generate_order_id()
        now = datetime.utcnow().isoformat() + "Z"

        self.orders[order_id] = {
            "id": order_id,
            "maker_address": makeraddress,
            "maker": maker,
            "maker_size": str(makeramount),
            "taker_address": takeraddress,
            "taker": taker,
            "taker_size": str(takeramount),
            "created_at": now,
            "updated_at": now,
            "block_id": f"fake_block_{order_id}",
            "order_type": order_type,
            "partial_minimum": "0",
            "partial_orig_maker_size": str(makeramount),
            "partial_orig_taker_size": str(takeramount),
            "partial_repost": False,
            "partial_parent_id": "",
            "status": "open",
        }

        return self.orders[order_id]

    async def _makepartialorder(self, params: list) -> dict:
        if len(params) < 8:
            return {
                "error": "Invalid parameters",
                "code": -1,
                "name": "dxMakePartialOrder",
            }

        maker = params[0]
        makeramount = float(params[1])
        makeraddress = params[2]
        taker = params[3]
        takeramount = float(params[4])
        takeraddress = params[5]
        min_size = float(params[6])
        repost = params[7] if len(params) > 7 else False

        if makeramount <= 0 or takeramount <= 0:
            return {"error": "Invalid amount", "code": -1, "name": "dxMakePartialOrder"}

        if not makeraddress or not takeraddress:
            return {
                "error": f"Bad address {makeraddress or takeraddress}",
                "code": 1026,
                "name": "dxMakePartialOrder",
            }

        if makeraddress == takeraddress:
            return {
                "error": f"Invalid parameters: The maker_address and taker_address cannot be the same: {makeraddress}",
                "code": 1025,
                "name": "dxMakePartialOrder",
            }

        available = self.available_balances.get(maker, 0)
        if available < makeramount:
            return {
                "error": f"Insufficient balance for {maker}: have {available}, need {makeramount}",
                "code": 1026,
                "name": "dxMakePartialOrder",
            }

        self.available_balances[maker] = available - makeramount

        order_id = self._generate_order_id()

        self.orders[order_id] = {
            "id": order_id,
            "maker": maker,
            "maker_size": str(makeramount),
            "maker_address": makeraddress,
            "taker": taker,
            "taker_size": str(takeramount),
            "taker_address": takeraddress,
            "partial_minimum": str(min_size),
            "partial_repost": repost,
            "status": "open",
            "order_type": "partial",
            "partial_orig_maker_size": str(makeramount),
            "partial_orig_taker_size": str(takeramount),
            "partial_parent_id": "",
            "block_id": f"fake_block_{order_id}",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }

        return {"id": order_id}

    async def _getorderstatus(self, params: list) -> dict:
        order_id = params[0] if params else ""

        order = self.orders.get(order_id)
        if not order:
            return {
                "error": "Transaction 0000000000000000000000000000000000000000000000000000000000000000 not found",
                "code": 1021,
                "name": "dxGetOrder",
            }

        return order

    async def _cancelorder(self, params: list) -> dict:
        order_id = params[0] if params else ""

        order = self.orders.get(order_id)
        if not order:
            if len(order_id) == 64 and all(
                c in "0123456789abcdefABCDEF" for c in order_id
            ):
                return {
                    "error": f"Transaction {order_id[:56]} not found",
                    "code": 1021,
                    "name": "dxCancelOrder",
                }
            return {
                "error": f"Invalid parameters: Invalid order id [{order_id}]",
                "code": 1025,
                "name": "dxCancelOrder",
            }

        now = datetime.utcnow().isoformat() + "Z"
        order["status"] = "canceled"
        order["refund_tx"] = ""
        order["updated_at"] = now

        maker = order.get("maker")
        maker_size = float(order.get("maker_size", 0))
        if maker and maker_size > 0:
            current_available = self.available_balances.get(maker, 0)
            self.available_balances[maker] = current_available + maker_size
            logger.debug(
                "Restored %f %s to available balance on cancel", maker_size, maker
            )

        return order

    async def _cancelallorders(self, params: Any) -> dict:
        count = len(self.orders)
        self.orders.clear()
        return {"canceled": count}

    async def _dxflushcancelledorders(self, params: Any) -> dict:
        return {}

    async def _dxloadxbridgeconf(self, params: Any) -> dict:
        if params:
            return {
                "error": "Invalid parameters: This function does not accept any parameter.",
                "code": 1025,
                "name": "dxLoadXBridgeConf",
            }
        return True

    async def _getorders(self, params: Any) -> list:
        if params:
            return {
                "error": "Invalid parameters: This function does not accept any parameters.",
                "code": 1025,
                "name": "dxGetMyOrders",
            }
        return list(self.orders.values())

    async def _getnewtokenaddress(self, params: list) -> list:
        if not params or not params[0]:
            return {
                "error": "Invalid parameters: (ticker)",
                "code": 1025,
                "name": "dxGetNewTokenAddress",
            }
        token = params[0]
        if token not in self._token_addresses:
            self._token_addresses[token] = f"FakeAddr{token}{self._order_counter}"
        return [self._token_addresses[token]]

    async def _handle_http_request(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()

            method = data.get("method", "")
            params = data.get("params", [])
            request_id = data.get("id")

            if method in self._methods:
                result = await self._methods[method](params)
                if isinstance(result, dict) and "error" in result and "code" in result:
                    response = {
                        "result": result,
                        "error": None,
                        "id": request_id,
                    }
                else:
                    response = {
                        "result": result,
                        "error": None,
                        "id": request_id,
                    }
            else:
                response = {
                    "result": None,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                    "id": request_id,
                }

            return web.json_response(response)

        except Exception as e:
            logger.error("Error handling RPC request: %s", e)
            return web.json_response(
                {
                    "result": None,
                    "error": {"code": -32603, "message": str(e)},
                    "id": 1,
                }
            )

    async def start(self):
        self.app = web.Application()
        self.app.router.add_post("/", self._handle_http_request)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        self._running = True
        logger.info(
            "Fake XBridge HTTP RPC server started on %s:%d", self.host, self.port
        )

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
        self._running = False
        logger.info("Fake XBridge HTTP RPC server stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def get_state(self) -> dict:
        """Get current server state for debugging."""
        return {
            "balances": dict(self.balances),
            "orders": dict(self.orders),
            "order_count": len(self.orders),
        }

    def apply_fill(self, order_id: str, filled_maker: float, filled_taker: float):
        """
        Apply a fill to an order and update balances accordingly.

        This should be called by the backtest engine when price crosses an order.

        Args:
            order_id: The order ID that was filled
            filled_maker: Amount of maker token that was filled
            filled_taker: Amount of taker token that was filled
        """
        order = self.orders.get(order_id)
        if not order:
            logger.warning("Cannot apply fill: order %s not found", order_id)
            return

        maker = order.get("maker")
        taker = order.get("taker")

        if maker == self.base_token:
            self.balances[self.base_token] = str(
                float(self.balances.get(self.base_token, 0)) - filled_maker
            )
            self.balances[self.quote_token] = str(
                float(self.balances.get(self.quote_token, 0)) + filled_taker
            )
        else:
            self.balances[self.base_token] = str(
                float(self.balances.get(self.base_token, 0)) + filled_maker
            )
            self.balances[self.quote_token] = str(
                float(self.balances.get(self.quote_token, 0)) - filled_taker
            )

        logger.debug(
            "Applied fill: order=%s, maker=%s filled=%s, taker=%s filled=%s",
            order_id,
            maker,
            filled_maker,
            taker,
            filled_taker,
        )
