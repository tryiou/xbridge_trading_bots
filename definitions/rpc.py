import asyncio
import aiohttp
from aiohttp import BasicAuth, ClientError
import async_timeout

from definitions.error_handler import ErrorHandler, TransientError, OperationalError

class RpcTimeoutError(Exception):
    """Custom exception for RPC timeout handling"""
    pass

async def rpc_call(method, params=None, url="http://127.0.0.1", rpc_user=None, rpc_password=None,
                   rpc_port=None, debug=2, timeout=30, display=True, prefix='xbridge', max_err_count=5,
                   logger=None, session=None, error_handler=None):
    """
    Make an async JSON-RPC call with centralized error handling.

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
    :param session: Optional aiohttp.ClientSession instance.
    :param error_handler: ErrorHandler instance for centralized error handling.
    :return: Result of the RPC call, or None if failed after max attempts.
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
            response_text = None
            try:
                async with async_timeout.timeout(timeout):
                    async with s.post(url, json=payload, headers=headers, auth=auth, timeout=client_timeout) as response:
                        response_text = await response.text()
                        response.raise_for_status()

                        try:
                            json_response = await response.json()
                        except aiohttp.ContentTypeError:
                            raise OperationalError(
                                "RPC response is not valid JSON",
                                context={"content": response_text}
                            )

                        if 'error' in json_response and json_response['error'] is not None:
                            error_msg = json_response['error'].get('message', 'Unknown RPC error')
                            error_code = json_response['error'].get('code', -1)
                            if error_handler:
                                error_handler.handle(
                                    OperationalError(
                                        f"RPC error {error_code}: {error_msg}",
                                        {"method": method, "params": params}
                                    ),
                                    context={"prefix": prefix, "err_count": err_count}
                                )
                            elif logger:
                                logger.warning(f"{prefix}_rpc_call: RPC error {error_code} - {error_msg}")
                            return json_response

                        result = json_response.get('result')
                        if result is not None:
                            if display and debug >= 2:
                                log_func = logger.info if logger else print
                                if debug >= 3:
                                    log_func(f"{prefix}_rpc_call({method}, {params})")
                                elif debug == 2:
                                    log_func(f"{prefix}_rpc_call({method})")
                            return result
                        else:
                            if logger:
                                logger.warning(f"{prefix}_rpc_call: Missing result in response")
                            return None
            except (ClientError, asyncio.TimeoutError, OperationalError) as e:
                context = {
                    "method": method,
                    "params": params,
                    "prefix": prefix,
                    "err_count": err_count,
                    "response_text": response_text
                }
                
                if error_handler:
                    error_class = TransientError if isinstance(e, asyncio.TimeoutError) else OperationalError
                    if not error_handler.handle(error_class(str(e)), context=context):
                        return None
                elif logger:
                    logger.warning(f"{prefix}_rpc_call error: {type(e).__name__} - {e}")
                
                await asyncio.sleep(err_count + 1)
            except Exception as e:
                context = {
                    "method": method,
                    "params": params,
                    "prefix": prefix,
                    "err_count": err_count,
                    "response_text": response_text
                }
                
                if error_handler:
                    if not error_handler.handle(
                        OperationalError(f"Unexpected error: {str(e)}"),
                        context=context
                    ):
                        return None
                elif logger:
                    logger.error(f"{prefix}_rpc_call unexpected error: {type(e).__name__} - {e}", exc_info=True)
                
                await asyncio.sleep(err_count + 1)
        return None

    if session:
        return await _rpc_call_internal(session)
    else:
        async with aiohttp.ClientSession() as new_session:
            return await _rpc_call_internal(new_session)
