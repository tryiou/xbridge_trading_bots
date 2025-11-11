import asyncio
import logging
import socket
import threading
from typing import Any, Dict, List, Optional, Union

import aiohttp
import async_timeout
from aiohttp import BasicAuth, ClientSession

from definitions.error_handler import OperationalError


class AsyncThreadingSemaphore:
    """A wrapper to use a threading.BoundedSemaphore in an async context."""

    def __init__(self, value: int = 1) -> None:
        self._semaphore = threading.BoundedSemaphore(value)

    async def __aenter__(self) -> 'AsyncThreadingSemaphore':
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._semaphore.acquire)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._semaphore.release()


class RpcTimeoutError(Exception):
    """Custom exception for RPC timeout handling"""
    pass


async def rpc_call(method: str,
                   params: Optional[Union[List, Dict[str, Any]]] = None,
                   url: str = "http://127.0.0.1",
                   rpc_user: Optional[str] = None,
                   rpc_password: Optional[str] = None,
                   rpc_port: Optional[int] = None,
                   debug: int = 2,
                   timeout: int = 30,
                   prefix: str = 'xbridge',
                   max_err_count: int = 5,
                   logger: Optional[logging.Logger] = None,
                   session: Optional[ClientSession] = None,
                   error_handler: Optional[Any] = None,
                   shutdown_event: Optional[asyncio.Event] = None) -> Any:
    """
    Make an async JSON-RPC call with centralized error handling.

    Args:
        method: RPC method to call.
        params: Parameters for the RPC call.
        url: URL for the RPC server.
        rpc_user: RPC server username.
        rpc_password: RPC server password.
        rpc_port: RPC port.
        debug: Debug level.
        timeout: Timeout for the HTTP request.
        display: Whether to display debug information.
        prefix: Prefix for debug messages.
        max_err_count: Maximum number of retries in case of errors.
        logger: Optional logger instance to use for messages.
        session: Optional aiohttp.ClientSession instance.
        error_handler: ErrorHandler instance for centralized error handling.
    Returns:
        Result of the RPC call, or None if failed after max attempts.
    """
    if params is None:
        params = []
    url = f"{url}:{rpc_port}" if rpc_port not in {80, 443} else url
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 0}
    headers = {'Content-type': 'application/json'}
    auth = BasicAuth(rpc_user, rpc_password) if rpc_user and rpc_password else None
    client_timeout = aiohttp.ClientTimeout(total=timeout)

    async def _rpc_call_internal(s: ClientSession) -> Any:
        for err_count in range(max_err_count):
            response_text = None
            try:
                async with async_timeout.timeout(timeout):
                    async with s.post(url,
                                      json=payload,
                                      headers=headers,
                                      auth=auth,
                                      timeout=client_timeout) as response:
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
                            if logger and debug >= 2:
                                if debug >= 3:
                                    logger.info(f"{prefix}_rpc_call({method}, {params})")
                                else:
                                    logger.info(f"{prefix}_rpc_call({method})")
                            return result
                        else:
                            if logger:
                                logger.warning(f"{prefix}_rpc_call: Missing result in response")
                            return None
            except Exception as e:
                context = {
                    "method": method,
                    "params": params,
                    "prefix": prefix,
                    "err_count": err_count,
                    "response_text": response_text
                }
                if error_handler:
                    if not await error_handler.handle_async(e, context=context):
                        return None  # Abort if handler says so (e.g., max retries)
                elif logger:
                    # Fallback logging if no handler is provided
                    logger.warning(f"{prefix}_rpc_call encountered an error: {e}", exc_info=True)

                if shutdown_event:
                    try:
                        # Wait for the shutdown event or timeout
                        await asyncio.wait_for(shutdown_event.wait(), timeout=err_count + 1)
                        # If wait() completes, it means the event was set.
                        if logger:
                            logger.debug(f"Shutdown signaled during RPC backoff for {method}. Aborting.")
                        return None
                    except asyncio.TimeoutError:
                        # This is the normal case, sleep finished.
                        pass
                else:
                    await asyncio.sleep(err_count + 1)
        raise RpcTimeoutError(f"{prefix}_rpc_call failed after {max_err_count} attempts for method '{method}'")

    if session:
        return await _rpc_call_internal(session)
    else:
        async with aiohttp.ClientSession() as new_session:
            return await _rpc_call_internal(new_session)


def is_port_open(ip: str, port: int, timeout: float = 2.0) -> bool:
    """Check if TCP port is open synchronously."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((ip, port))
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False
        except Exception as e:
            # We don't log here to keep it simple. Callers should handle logging.
            return False
