"""
Standardized error classes for the trading application
"""

import asyncio
from typing import Any


class AppError(Exception):
    """Base class for all application errors"""

    context: dict[str, Any]

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.context = context or {}
        # Preserve original cause for better debugging
        self.__cause__ = (
            self.context.get("__cause__") if isinstance(self.context, dict) else None
        )

    def __str__(self) -> str:
        return f"{self.__class__.__name__}: {super().__str__()}"


class TransientError(AppError):
    """Temporary errors (network issues, timeouts) that might resolve with retries"""

    pass


class BlockingError(AppError):
    """Non-recoverable errors (bad addresses, invalid config) - fail immediately, no retries"""

    pass


class OperationalError(AppError):
    """Recoverable errors (validation, input issues) that don't require shutdown"""

    pass


class CriticalError(AppError):
    """Unrecoverable errors (system failures, data corruption) requiring shutdown"""

    pass


class ConfigurationError(OperationalError):
    """Errors related to configuration issues"""

    pass


class RPCConfigError(ConfigurationError):
    """Critical failure in RPC configuration during application initialization

    Indicates unrecoverable errors in Blocknet RPC setup that prevent trading operations.
    Typically caused by missing credentials, unresponsive ports, or inaccessible config files.

    Attributes:
        context: Technical details about failure context. May include:
            - path: Location of configuration file
            - port: RPC service port number
            - keys: Missing configuration keys
    """

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, context)


class ExchangeError(TransientError):
    """Errors from exchange APIs"""

    pass


class BlockchainError(TransientError):
    """Errors from blockchain interactions"""

    pass


class StrategyError(OperationalError):
    """Errors specific to trading strategies"""

    pass


class GUIRenderingError(OperationalError):
    """Errors in GUI components"""

    pass


class OrderError(OperationalError):
    """Errors related to order creation/management"""

    pass


class InsufficientFundsError(OperationalError):
    """Insufficient funds error - now recoverable"""

    pass


class NetworkTimeoutError(TransientError):
    """Network timeouts and connection issues"""

    pass


class RpcHttpError(OperationalError):
    """HTTP status error from the RPC endpoint (e.g. 401 auth, 404 method).

    Distinct from NetworkTimeoutError: the server was reachable and answered,
    but rejected the request. Carries the HTTP status so logs do not
    misreport a 404/401 as a timeout.
    """

    def __init__(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message, context)
        self.status = status


class ProtocolError(OperationalError):
    """Errors in RPC/API protocol handling"""

    pass


def _wrap_exception(e: Exception, app_error_cls: type[AppError]) -> AppError:
    """Wraps an exception in an AppError subclass, preserving the cause."""
    exc = app_error_cls(str(e))
    exc.__cause__ = e
    return exc


def convert_exception(e: Exception) -> AppError:
    """Convert third-party exceptions to native application error types"""
    # Preserve existing AppErrors
    if isinstance(e, AppError):
        return e

    # Handle CCXT Errors
    if getattr(e, "name", None):
        if e.name == "InsufficientFunds":
            return _wrap_exception(e, InsufficientFundsError)
        if e.name == "NetworkError":
            return _wrap_exception(e, NetworkTimeoutError)
        if "Order" in e.name:
            return _wrap_exception(e, OrderError)

    # Handle HTTP/AIO Errors
    try:
        import aiohttp

        if isinstance(e, aiohttp.ClientResponseError):
            status = getattr(e, "status", None)
            url = getattr(e, "request_info", None)
            url_str = str(getattr(url, "url", "")) if url is not None else ""
            detail = f" ({url_str})" if url_str else ""
            exc = RpcHttpError(
                f"HTTP {status}: {e.message}{detail}",
                context={"http_status": status, "url": url_str},
                status=status,
            )
            exc.__cause__ = e
            return exc
        if isinstance(e, aiohttp.ClientError):
            return _wrap_exception(e, NetworkTimeoutError)
    except ImportError:
        pass  # Gracefully fallback if aiohttp unavailable

    # Handle Python Built-ins
    if isinstance(e, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return _wrap_exception(e, NetworkTimeoutError)
    # Default to OperationalError
    return _wrap_exception(e, OperationalError)
