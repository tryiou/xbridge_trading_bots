"""Test fake XBridge RPC server against real server - every call, good and bad paths."""

import asyncio
import aiohttp
from backtesting.fake_xbridge_rpc import FakeXBridgeRPCServer

REAL_SERVER = "http://127.0.0.1:41414"
AUTH_HEADER = {
    "Authorization": "Basic QnBZeGpVTmV5OU03UmhlV0VPUnF2QzN2a0lxcXZNbjk6OUF3YmFzWVRDZXFhcU1EUW9zWm5rS3c0YmtTa3R2S0E="
}


async def rpc_call(url: str, method: str, params: list = None, auth: dict = None):
    """Make RPC call and return JSON response."""
    if params is None:
        params = []
    headers = {"Content-Type": "application/json"}
    if auth:
        headers.update(auth)
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json={"method": method, "params": params, "id": 1},
            headers=headers,
        ) as resp:
            return resp.status, await resp.json()


async def test_compare_all_calls():
    """Compare fake vs real server for all RPC calls."""
    print("=" * 80)
    print("COMPARING FAKE VS REAL SERVER - EVERY CALL, GOOD AND BAD PATH")
    print("=" * 80)

    # Start fake server
    fake_server = FakeXBridgeRPCServer(port=18338)
    fake_server.set_tokens("LTC", "DOGE")
    fake_server.set_balances(1.0, 100.0)
    await fake_server.start()
    await asyncio.sleep(0.5)

    results = []

    async def test_method(method: str, params_good: list, params_bad: list, desc: str):
        print(f"\n[{desc}] {method}")
        # Good path
        _, fake_good = await rpc_call("http://127.0.0.1:18338", method, params_good)
        _, real_good = await rpc_call(REAL_SERVER, method, params_good, AUTH_HEADER)
        print(f"  GOOD - Fake: {fake_good}")
        print(f"  GOOD - Real: {real_good}")
        # Bad path
        _, fake_bad = await rpc_call("http://127.0.0.1:18338", method, params_bad)
        _, real_bad = await rpc_call(REAL_SERVER, method, params_bad, AUTH_HEADER)
        print(f"  BAD  - Fake: {fake_bad}")
        print(f"  BAD  - Real: {real_bad}")

    # 1. dxgettokenbalances - no params
    await test_method(
        "dxgettokenbalances",
        [],
        [],  # No bad path known
        "1",
    )

    # 2. dxGetOrder - good: valid order id, bad: fake order id
    await test_method(
        "dxGetOrder",
        ["fake_order_1"],  # will be created but for comparison
        ["0000000000000000000000000000000000000000000000000000000000000000"],
        "2",
    )

    # 3. dxMakePartialOrder - create small order, get status, then cancel
    print("\n[3] dxMakePartialOrder + dxGetOrder + dxCancelOrder - FULL SEQUENCE")
    # Good path - create order on fake
    _, fake_create = await rpc_call(
        "http://127.0.0.1:18338",
        "dxMakePartialOrder",
        ["PIVX", "0.001", "testaddr", "BLOCK", "0.001", "testaddr2", "0.0001", False],
    )
    print(f"  CREATE Fake: {fake_create}")

    # Get the order ID
    fake_order_id = fake_create.get("result", {}).get("id")

    if fake_order_id:
        # Get status
        _, fake_status = await rpc_call(
            "http://127.0.0.1:18338", "dxGetOrder", [fake_order_id]
        )
        print(f"  GET STATUS Fake: {fake_status}")

        # Cancel
        _, fake_cancel = await rpc_call(
            "http://127.0.0.1:18338", "dxCancelOrder", [fake_order_id]
        )
        print(f"  CANCEL Fake: {fake_cancel}")

    # Real server - create order
    _, real_create = await rpc_call(
        REAL_SERVER,
        "dxMakePartialOrder",
        ["PIVX", "0.001", "testaddr", "BLOCK", "0.001", "testaddr2", "0.0001", False],
        AUTH_HEADER,
    )
    print(f"  CREATE Real: {real_create}")

    real_order_id = real_create.get("result", {}).get("id")

    if real_order_id and "error" not in real_create.get("result", {}):
        # Get status
        _, real_status = await rpc_call(
            REAL_SERVER, "dxGetOrder", [real_order_id], AUTH_HEADER
        )
        print(f"  GET STATUS Real: {real_status}")

        # Cancel
        _, real_cancel = await rpc_call(
            REAL_SERVER, "dxCancelOrder", [real_order_id], AUTH_HEADER
        )
        print(f"  CANCEL Real: {real_cancel}")

    # Bad path - empty addresses
    _, fake_bad = await rpc_call(
        "http://127.0.0.1:18338",
        "dxMakePartialOrder",
        ["PIVX", "0.001", "", "BLOCK", "0.001", "", "0.0001", False],
    )
    print(f"  BAD - Fake: {fake_bad}")

    _, real_bad = await rpc_call(
        REAL_SERVER,
        "dxMakePartialOrder",
        ["PIVX", "0.001", "", "BLOCK", "0.001", "", "0.0001", False],
        AUTH_HEADER,
    )
    print(f"  BAD - Real: {real_bad}")

    # 4. dxCancelOrder - good: valid order, bad: fake order
    await test_method(
        "dxCancelOrder",
        ["fake_order_1"],
        ["0000000000000000000000000000000000000000000000000000000000000000"],
        "4",
    )

    # 5. dxGetMyOrders - good: no params, bad: with params
    await test_method("dxGetMyOrders", [], ["invalid_param"], "5")

    # 6. dxflushcancelledorders - good: no params, bad: with params
    await test_method("dxflushcancelledorders", [], ["invalid_param"], "6")

    # 7. dxloadxbridgeconf - good: no params, bad: with params
    await test_method("dxloadxbridgeconf", [], ["invalid_param"], "7")

    # 8. dxGetNewTokenAddress - good: with token, bad: no token
    await test_method("dxGetNewTokenAddress", ["LTC"], [], "8")

    # 9. Unknown method - good path only
    print("\n[9] unknown_method")
    _, fake_unknown = await rpc_call("http://127.0.0.1:18338", "unknown_method", [])
    _, real_unknown = await rpc_call(REAL_SERVER, "unknown_method", [], AUTH_HEADER)
    print(f"  GOOD - Fake: {fake_unknown}")
    print(f"  GOOD - Real: {real_unknown}")

    print("\n" + "=" * 80)
    print("COMPARISON COMPLETE")
    print("=" * 80)

    await fake_server.stop()


if __name__ == "__main__":
    asyncio.run(test_compare_all_calls())
