import time

# import requests
from requests.auth import HTTPBasicAuth
from requests import Session
# import config.blocknet_rpc_cfg as config
import definitions.bcolors as bcolors
from definitions.detect_rpc import detect_rpc

user_rpc, port_rpc, password_rpc = detect_rpc()


def test_rpc(rpc_user, rpc_port, rpc_password):
    result = rpc_call("getwalletinfo", rpc_user=rpc_user, rpc_port=rpc_port, rpc_password=rpc_password)
    print(f'rpc call getwalletinfo: {result}')
    if result:
        return True
    else:
        return False


def rpc_call(method, params=[], url="http://127.0.0.1", rpc_user=user_rpc, rpc_password=password_rpc, rpc_port=port_rpc,
             debug=2, timeout=120, display=True, prefix='xbridge', max_err_count=None):
    if rpc_port not in {80, 443}:
        url = f"{url}:{rpc_port}"

    payload = {"jsonrpc": "2.0",
               "method": method,
               "params": params,
               "id": 0}

    headers = {'Content-type': 'application/json'}
    auth = HTTPBasicAuth(rpc_user, rpc_password) if rpc_user and rpc_password else None

    done = False
    error = False
    err_count = 0

    while not done:
        try:
            with Session() as session:
                response = session.post(url, json=payload, headers=headers, auth=auth, timeout=timeout)
                responsejson = response.json()
                result = responsejson.get('result')
        except Exception as e:
            err_count += 1
            msg = f"{prefix}_rpc_call( {method}, {params} )"
            print(f"{bcolors.mycolor.WARNING}{msg}{bcolors.mycolor.ENDC}")
            print(f"{bcolors.mycolor.WARNING}{type(e)}, {e}{bcolors.mycolor.ENDC}")
            result = None
            error = True
            if max_err_count and err_count >= max_err_count:
                break
            time.sleep(err_count)
        else:
            done = True
            error = False

    if debug >= 2 and display and not error:
        msg = f"{prefix}_rpc_call( {method}, {params} )"
        print(f"{bcolors.mycolor.OKGREEN}{msg}{bcolors.mycolor.ENDC}")
        if debug >= 3:
            print(str(responsejson))

    return result


if not test_rpc(user_rpc, port_rpc, password_rpc):
    print.error(f'Blocknet core rpc server not responding ?')
    exit()


def xrgetblockcount(token, nodecount=1, timeout=120, max_err_count=None):
    return rpc_call("xrGetBlockCount", [token, nodecount], timeout=timeout, max_err_count=max_err_count)


def xrgetnetworkservices(timeout=120):
    return rpc_call("xrGetNetworkServices", timeout=timeout)


def xrgetreply(uuid, timeout=120):
    return rpc_call("xrGetReply", [uuid], timeout=timeout)


def getnewtokenadress(token):
    return rpc_call("dxGetNewTokenAddress", [token])


def getmyordersbymarket(maker, taker):
    myorders = rpc_call("dxGetMyOrders")
    return [zz for zz in myorders if (zz['maker'] == maker) and (zz['taker'] == taker)]


def cancelorder(order_id):
    return rpc_call("dxCancelOrder", [order_id])


def cancelallorders():
    myorders = rpc_call("dxGetMyOrders")
    for z in myorders:
        if z['status'] == "open" or z['status'] == "new":
            cancelorder(z['id'])


def dxloadxbridgeconf():
    rpc_call("dxloadxbridgeconf")


def dxflushcancelledorders():
    return rpc_call("dxflushcancelledorders")


def gettokenbalances():
    # return proxy_gettokenbalances()
    return rpc_call("dxgettokenbalances")


def gettokenutxo(token, used=False):
    return rpc_call("dxgetutxos", [token, used])


def getlocaltokens():
    return rpc_call("dxgetlocaltokens")


def makeorder(maker, makeramount, makeraddress, taker, takeramount, takeraddress, dryrun=None):
    if dryrun:
        result = rpc_call("dxMakeOrder",
                          [maker, makeramount, makeraddress, taker, takeramount, takeraddress, 'exact', 'dryrun'])
    else:
        result = rpc_call("dxMakeOrder", [maker, makeramount, makeraddress, taker, takeramount, takeraddress, 'exact'])
    return result


def getorderstatus(oid):
    return rpc_call("dxGetOrder", [oid])


def dxgetorderbook(detail, maker, taker):
    return rpc_call("dxgetorderbook", [detail, maker, taker])
