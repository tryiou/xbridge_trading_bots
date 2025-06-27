from definitions.detect_rpc import detect_rpc
from definitions.rpc import rpc_call

class XBridgeManager:
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.logger = config_manager.general_log
        self.user_rpc, self.port_rpc, self.password_rpc = detect_rpc()

        # Optional: Test RPC connection during initialization
        if not self.test_rpc():
            self.logger.error(f'Blocknet core rpc server not responding or credentials incorrect.')
            # Depending on desired behavior, you might want to raise an exception or exit here.
            # For now, just log the error.

    def rpc_wrapper(self, method, params=None):
        if params is None:
            params = []
        result = rpc_call(method=method,
                          params=params,
                          rpc_user=self.user_rpc,
                          rpc_port=self.port_rpc,
                          rpc_password=self.password_rpc,
                          logger=self.logger)  # Pass the instance logger
        return result

    def test_rpc(self):
        result = self.rpc_wrapper("getwalletinfo")
        if result:
            self.logger.info(f'XBridge RPC connection successful: getwalletinfo returned {result}')
            return True
        else:
            self.logger.error(f'XBridge RPC connection failed: getwalletinfo returned {result}')
            return False

    def getnewtokenadress(self, token):
        return self.rpc_wrapper("dxGetNewTokenAddress", [token])

    def getmyordersbymarket(self, maker, taker):
        myorders = self.rpc_wrapper("dxGetMyOrders")
        return [zz for zz in myorders if (zz['maker'] == maker) and (zz['taker'] == taker)]

    def cancelorder(self, order_id):
        return self.rpc_wrapper("dxCancelOrder", [order_id])

    def cancelallorders(self):
        myorders = self.rpc_wrapper("dxGetMyOrders")
        for z in myorders:
            if z['status'] == "open" or z['status'] == "new":
                self.cancelorder(z['id'])

    def dxloadxbridgeconf(self):
        self.rpc_wrapper("dxloadxbridgeconf")

    def dxflushcancelledorders(self):
        return self.rpc_wrapper("dxflushcancelledorders")

    def gettokenbalances(self):
        return self.rpc_wrapper("dxgettokenbalances")

    def gettokenutxo(self, token, used=False):
        return self.rpc_wrapper("dxgetutxos", [token, used])

    def getlocaltokens(self):
        return self.rpc_wrapper("dxgetlocaltokens")

    def makeorder(self, maker, makeramount, makeraddress, taker, takeramount, takeraddress, dryrun=None):
        if dryrun:
            result = self.rpc_wrapper("dxMakeOrder",
                                      [maker, makeramount, makeraddress, taker, takeramount, takeraddress, 'exact', 'dryrun'])
        else:
            result = self.rpc_wrapper("dxMakeOrder",
                                      [maker, makeramount, makeraddress, taker, takeramount, takeraddress, 'exact'])
        return result

    def makepartialorder(self, maker, makeramount, makeraddress, taker, takeramount, takeraddress, min_size, repost=False,
                         dryrun=None):
        if dryrun:
            result = self.rpc_wrapper("dxMakePartialOrder",
                                      [maker, makeramount, makeraddress, taker, takeramount, takeraddress, min_size, repost,
                                       'dryrun'])
        else:
            result = self.rpc_wrapper("dxMakePartialOrder",
                                      [maker, makeramount, makeraddress, taker, takeramount, takeraddress, min_size, repost])
        return result

    def getorderstatus(self, oid):
        return self.rpc_wrapper("dxGetOrder", [oid])

    def dxgetorderbook(self, detail, maker, taker):
        return self.rpc_wrapper("dxgetorderbook", [detail, maker, taker])
