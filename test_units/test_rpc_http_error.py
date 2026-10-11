"""HTTP status errors must not be misreported as timeouts (regression test)."""

import os
import sys

import aiohttp

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from definitions.errors import (
    NetworkTimeoutError,
    OperationalError,
    RpcHttpError,
    convert_exception,
)


def _response_error(
    status: int, message: str = "Not Found"
) -> aiohttp.ClientResponseError:
    req_info = aiohttp.RequestInfo(
        url=aiohttp.client_reqrep.URL("http://127.0.0.1:41414"),
        method="POST",
        headers={},
        real_url=aiohttp.client_reqrep.URL("http://127.0.0.1:41414"),
    )
    return aiohttp.ClientResponseError(
        request_info=req_info,
        history=(),
        status=status,
        message=message,
    )


def test_http_404_maps_to_rpc_http_error_not_timeout():
    err = convert_exception(_response_error(404))
    assert isinstance(err, RpcHttpError)
    assert isinstance(err, OperationalError)
    assert not isinstance(err, NetworkTimeoutError)
    assert err.status == 404
    assert "404" in str(err)


def test_http_401_maps_to_rpc_http_error():
    err = convert_exception(_response_error(401, "Unauthorized"))
    assert isinstance(err, RpcHttpError)
    assert err.status == 401


def test_generic_client_error_still_timeout():
    err = convert_exception(aiohttp.ClientError("boom"))
    assert isinstance(err, NetworkTimeoutError)
