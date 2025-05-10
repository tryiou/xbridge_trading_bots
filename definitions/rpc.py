import time

from requests import Session, HTTPError
from requests.auth import HTTPBasicAuth

import definitions.bcolors as bcolors


def handle_error(e, err_count, method, params, prefix):
    msg = f"{prefix}_rpc_call( {method}, {params} )"
    print(f"{bcolors.mycolor.WARNING}{msg}{bcolors.mycolor.ENDC}")
    print(f"{bcolors.mycolor.WARNING}{type(e)}, {e}{bcolors.mycolor.ENDC}")
    time.sleep(err_count + 1)


def rpc_call(method, params=None, url="http://127.0.0.1", rpc_user=None, rpc_password=None,
             rpc_port=None, debug=2, timeout=120, display=True, prefix='xbridge', max_err_count=3):
    """
    Make a JSON-RPC call.

    :param method: RPC method to call.
    :param params: Parameters for the RPC call.
    :param url: URL for the RPC server.
    :param rpc_user: RPC server username.
    :param rpc_password: RPC server password.
    :param rpc_port: RPC port.
    :param debug: Debug level.
    :param timeout: Timeout for the HTTP request.
    :param display: Whether to display debug information.
    :param prefix: Prefix for debug messages.
    :param max_err_count: Maximum number of retries in case of errors.
    :return: Result of the RPC call, or None if no result is obtained after max attempts.
    """
    if params is None:
        params = []
    url = f"{url}:{rpc_port}" if rpc_port not in {80, 443} else url
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 0}
    headers = {'Content-type': 'application/json'}
    auth = HTTPBasicAuth(rpc_user, rpc_password) if rpc_user and rpc_password else None

    for err_count in range(max_err_count):
        try:
            with Session() as session:
                response = session.post(url, json=payload, headers=headers, auth=auth, timeout=timeout)
                response.raise_for_status()
                result = response.json().get('result')
                if result is not None:
                    if debug >= 2 and display:
                        msg = f"{prefix}_rpc_call( {method}, {params} )"
                        print(f"{bcolors.mycolor.OKGREEN}{msg}{bcolors.mycolor.ENDC}")
                        if debug >= 3:
                            print(response.json())
                    return result
        except (HTTPError, Exception) as e:
            handle_error(e, err_count, method, params, prefix)

    return None
