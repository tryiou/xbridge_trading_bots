import time

import requests

import config.blocknet_rpc_cfg as config
import definitions.bcolors as bcolors


def rpc_call(method, params=[], url="http://127.0.0.1", port=config.rpc_port, debug=config.debug_level, timeout=120,
             rpc_user=config.rpc_user, rpc_password=config.rpc_password, display=True, prefix='xbridge',
             max_err_count=None):
    if port != 80 and port != 443:
        url = url + ':' + str(port)
    payload = {"jsonrpc": "2.0",
               "method": method,
               "params": params,
               "id": 0}
    headers = {'Content-type': 'application/json'}
    if rpc_user and rpc_password:
        auth = (config.rpc_user, config.rpc_password)
    else:
        auth = None
    done = False
    error = False
    err_count = 0
    while not done:
        try:
            response = requests.Session().post(url, json=payload, headers=headers, auth=auth, timeout=timeout)
            responsejson = response.json()
            # if method == "ccxt_call_fetch_tickers":
            #     print(response, '\n', responsejson)
            result = responsejson['result']
        except Exception as e:
            err_count += 1
            msg = prefix + "_rpc_call( " + str(method) + ', ' + str(params) + " )"
            print(f"{bcolors.mycolor.WARNING}{msg}{bcolors.mycolor.ENDC}")
            print(f"{bcolors.mycolor.WARNING}{str(type(e)) + ', ' + str(e)}{bcolors.mycolor.ENDC}")
            result = None
            error = True
            if max_err_count and err_count >= max_err_count:
                break
            time.sleep(err_count)
        else:
            done = True
            error = False
    if debug >= 2 and display and not error:
        msg = prefix + "_rpc_call( " + str(method) + ', ' + str(params) + " )"
        print(f"{bcolors.mycolor.OKGREEN}{msg}{bcolors.mycolor.ENDC}")
        if debug >= 3:
            print(str(responsejson))
    return result


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
