import asyncio

from definitions.detect_rpc import detect_rpc
from definitions.rpc import rpc_call


class XBridgeManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.logger = config_manager.general_log
        self.blocknet_user_rpc, self.blocknet_port_rpc, self.blocknet_password_rpc , self.blocknet_datadir_path = detect_rpc()

        # Optional: Test RPC connection during initialization
        if not self.test_rpc():
            self.logger.error(f'Blocknet core rpc server not responding or credentials incorrect.')
            # Depending on desired behavior, you might want to raise an exception or exit here.
            # For now, just log the error.

    async def rpc_wrapper(self, method, params=None):
        if params is None:
            params = []
        result = await rpc_call(method=method,
                                params=params,
                                rpc_user=self.blocknet_user_rpc,
                                rpc_port=self.blocknet_port_rpc,
                                rpc_password=self.blocknet_password_rpc,
                                logger=self.logger)  # Pass the instance logger
        return result

    def test_rpc(self):
        # This method is called from constructor, so it cannot be async.
        # We will use asyncio.run to run the async rpc_wrapper.
        # This is acceptable for a one-off test at startup.
        result = asyncio.run(self.rpc_wrapper("getwalletinfo"))
        if result:
            self.logger.info(f'XBridge RPC connection successful: getwalletinfo returned {result}')
            return True
        else:
            self.logger.error(f'XBridge RPC connection failed: getwalletinfo returned {result}')
            return False

    async def getnewtokenadress(self, token):
        return await self.rpc_wrapper("dxGetNewTokenAddress", [token])

    async def getmyordersbymarket(self, maker, taker):
        myorders = await self.rpc_wrapper("dxGetMyOrders")
        return [zz for zz in myorders if (zz['maker'] == maker) and (zz['taker'] == taker)]

    async def cancelorder(self, order_id):
        return await self.rpc_wrapper("dxCancelOrder", [order_id])

    async def cancelallorders(self):
        myorders = await self.rpc_wrapper("dxGetMyOrders")
        for z in myorders:
            if z['status'] == "open" or z['status'] == "new":
                await self.cancelorder(z['id'])

    async def dxloadxbridgeconf(self):
        await self.rpc_wrapper("dxloadxbridgeconf")

    async def dxflushcancelledorders(self):
        return await self.rpc_wrapper("dxflushcancelledorders")

    async def gettokenbalances(self):
        return await self.rpc_wrapper("dxgettokenbalances")

    async def gettokenutxo(self, token, used=False):
        return await self.rpc_wrapper("dxgetutxos", [token, used])

    async def getlocaltokens(self):
        return await self.rpc_wrapper("dxgetlocaltokens")

    async def makeorder(self, maker, makeramount, makeraddress, taker, takeramount, takeraddress, dryrun=None):
        if dryrun:
            result = await self.rpc_wrapper("dxMakeOrder",
                                            [maker, makeramount, makeraddress, taker, takeramount, takeraddress,
                                             'exact', 'dryrun'])
        else:
            result = await self.rpc_wrapper("dxMakeOrder",
                                            [maker, makeramount, makeraddress, taker, takeramount, takeraddress,
                                             'exact'])
        return result

    async def makepartialorder(self, maker, makeramount, makeraddress, taker, takeramount, takeraddress, min_size,
                               repost=False, dryrun=None):
        if dryrun:
            result = await self.rpc_wrapper("dxMakePartialOrder",
                                            [maker, makeramount, makeraddress, taker, takeramount, takeraddress,
                                             min_size, repost, 'dryrun'])
        else:
            result = await self.rpc_wrapper("dxMakePartialOrder",
                                            [maker, makeramount, makeraddress, taker, takeramount, takeraddress,
                                             min_size, repost])
        return result

    async def getorderstatus(self, oid):
        return await self.rpc_wrapper("dxGetOrder", [oid])

    async def dxgetorderbook(self, detail, maker, taker):
        return await self.rpc_wrapper("dxgetorderbook", [detail, maker, taker])
