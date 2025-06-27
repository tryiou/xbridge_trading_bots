import asyncio

import aiohttp
from aiohttp import BasicAuth, ClientError


async def handle_error(e, err_count, method, params, prefix, logger=None, response_text=None):
    msg = f"{prefix}_rpc_call( {method}, {params} )"
    log_func = logger.warning if logger else print
    log_func(f"{msg} - {type(e)}, {e}")
    if response_text:
        log_func(f"Raw RPC response content: {response_text}")

    await asyncio.sleep(err_count + 1)


async def rpc_call(method, params=None, url="http://127.0.0.1", rpc_user=None, rpc_password=None,
                   rpc_port=None, debug=2, timeout=120, display=True, prefix='xbridge', max_err_count=3,
                   logger=None, session=None):
    """
    Make an async JSON-RPC call.

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
    :param logger: Optional logger instance to use for messages.
    :param session: Optional aiohttp.ClientSession instance. If not provided, a new one is created.
    :return: Result of the RPC call, or None if no result is obtained after max attempts.
    """
    if params is None:
        params = []
    url = f"{url}:{rpc_port}" if rpc_port not in {80, 443} else url
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 0}
    headers = {'Content-type': 'application/json'}
    auth = BasicAuth(rpc_user, rpc_password) if rpc_user and rpc_password else None
    client_timeout = aiohttp.ClientTimeout(total=timeout)

    async def _rpc_call_internal(s):
        for err_count in range(max_err_count):
            try:
                response_text = None
                async with s.post(url, json=payload, headers=headers, auth=auth, timeout=client_timeout) as response:
                    # Read response text first for better error logging
                    response_text = await response.text()
                    response.raise_for_status() # Raises an exception for 4xx/5xx responses

                    try:
                        json_response = await response.json()
                    except aiohttp.ContentTypeError:
                        # If response is not JSON, log raw text and raise an error
                        raise ValueError(f"RPC response is not valid JSON. Content: {response_text}")
                    
                    # Check for JSON-RPC level error first
                    if 'error' in json_response and json_response['error'] is not None:
                        log_func = logger.warning if logger else print
                        log_func(f"{prefix}_rpc_call( {method}, {params} ) - Received JSON-RPC error: {json_response['error']}")
                        return json_response  # Return the full error object
                    
                    result = json_response.get('result')
                    if result is not None:
                        if debug >= 2 and display:
                            log_func = logger.info if logger else print
                            msg = f"{prefix}_rpc_call( {method}, {params} )"
                            log_func(msg)
                            if debug >= 3:  # Log full JSON response only if debug level is 3 or higher
                                log_func(f"Full JSON response: {json_response}")
                        return result
                    else:
                        # This case is for when 'result' is null or missing, but 'error' is also null/missing.
                        # This is an ambiguous response. Log it and return None to signal failure.
                        log_func = logger.warning if logger else print
                        log_func(f"{prefix}_rpc_call( {method}, {params} ) - 'result' field is None or missing. Full response: {json_response}")
                        return None
            except (ClientError, Exception) as e:
                await handle_error(e, err_count, method, params, prefix, logger, response_text=response_text)
        return None

    if session:
        return await _rpc_call_internal(session)
    else:
        async with aiohttp.ClientSession() as new_session:
            return await _rpc_call_internal(new_session)
