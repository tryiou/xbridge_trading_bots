from definitions.detect_rpc import detect_rpc
from definitions.rpc import rpc_call

user_rpc, port_rpc, password_rpc = detect_rpc()


def rpc_wrapper(method, params=None):
    if params is None:
        params = []
    result = rpc_call(method=method,
                      params=params,
                      rpc_user=user_rpc,
                      rpc_port=port_rpc,
                      rpc_password=password_rpc)
    return result


def test_rpc(rpc_user, rpc_port, rpc_password):
    result = rpc_call("getwalletinfo", rpc_user=rpc_user, rpc_port=rpc_port, rpc_password=rpc_password)
    print(f'rpc call getwalletinfo: {result}')
    if result:
        return True
    else:
        return False


def getnewtokenadress(token):
    return rpc_wrapper("dxGetNewTokenAddress", [token])


def getmyordersbymarket(maker, taker):
    myorders = rpc_wrapper("dxGetMyOrders")
    return [zz for zz in myorders if (zz['maker'] == maker) and (zz['taker'] == taker)]


def cancelorder(order_id):
    return rpc_wrapper("dxCancelOrder", [order_id])


def cancelallorders():
    myorders = rpc_wrapper("dxGetMyOrders")
    for z in myorders:
        if z['status'] == "open" or z['status'] == "new":
            cancelorder(z['id'])


def dxloadxbridgeconf():
    rpc_wrapper("dxloadxbridgeconf")


def dxflushcancelledorders():
    return rpc_wrapper("dxflushcancelledorders")


def gettokenbalances():
    # return proxy_gettokenbalances()
    return rpc_wrapper("dxgettokenbalances")


def gettokenutxo(token, used=False):
    return rpc_wrapper("dxgetutxos", [token, used])


def getlocaltokens():
    return rpc_wrapper("dxgetlocaltokens")


def makeorder(maker, makeramount, makeraddress, taker, takeramount, takeraddress, dryrun=None):
    if dryrun:
        result = rpc_wrapper("dxMakeOrder",
                             [maker, makeramount, makeraddress, taker, takeramount, takeraddress, 'exact', 'dryrun'])
    else:
        result = rpc_wrapper("dxMakeOrder",
                             [maker, makeramount, makeraddress, taker, takeramount, takeraddress, 'exact'])
    return result


def makepartialorder(maker, makeramount, makeraddress, taker, takeramount, takeraddress, min_size, repost=False,
                     dryrun=None):
    if dryrun:
        result = rpc_wrapper("dxMakePartialOrder",
                             [maker, makeramount, makeraddress, taker, takeramount, takeraddress, min_size, repost,
                              'dryrun'])
    else:
        result = rpc_wrapper("dxMakePartialOrder",
                             [maker, makeramount, makeraddress, taker, takeramount, takeraddress, min_size, repost])
    return result


def getorderstatus(oid):
    return rpc_wrapper("dxGetOrder", [oid])


def dxgetorderbook(detail, maker, taker):
    return rpc_wrapper("dxgetorderbook", [detail, maker, taker])


if not test_rpc(user_rpc, port_rpc, password_rpc):
    print(f'Blocknet core rpc server not responding ?')
    exit()
