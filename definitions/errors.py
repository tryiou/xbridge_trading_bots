"""
Standardized error classes for the trading application
"""


class AppError(Exception):
    """Base class for all application errors"""

    def __init__(self, message, context=None):
        super().__init__(message)
        self.context = context or {}
        # Preserve original cause for better debugging
        self.__cause__ = context.get('__cause__') if isinstance(context, dict) else None

    def __str__(self):
        return f"{self.__class__.__name__}: {super().__str__()} | Context: {self.context}"


class TransientError(AppError):
    """Temporary errors (network issues, timeouts) that might resolve with retries"""


class OperationalError(AppError):
    """Recoverable errors (validation, input issues) that don't require shutdown"""


class CriticalError(AppError):
    """Unrecoverable errors (system failures, data corruption) requiring shutdown"""


class ConfigurationError(OperationalError):
    """Errors related to configuration issues"""


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

    def __init__(self, message, context=None):
        super().__init__(message, context)


class ExchangeError(TransientError):
    """Errors from exchange APIs"""


class BlockchainError(TransientError):
    """Errors from blockchain interactions"""


class StrategyError(OperationalError):
    """Errors specific to trading strategies"""


class GUIRenderingError(OperationalError):
    """Errors in GUI components"""


class OrderError(OperationalError):
    """Errors related to order creation/management"""
    pass


class InsufficientFundsError(OperationalError):
    """Insufficient funds error - now recoverable"""
    pass


def _wrap_exception(e: Exception, app_error_cls: type) -> 'AppError':
    """Wraps an exception in an AppError subclass, preserving the cause."""
    exc = app_error_cls(str(e))
    exc.__cause__ = e
    return exc


def convert_exception(e: Exception) -> 'AppError':
    """Convert third-party exceptions to native application error types"""
    # Preserve existing AppErrors
    if isinstance(e, AppError):
        return e

    # Handle CCXT Errors
    if hasattr(e, 'name'):
        if e.name == 'InsufficientFunds':
            return _wrap_exception(e, InsufficientFundsError)
        if e.name == 'NetworkError':
            return _wrap_exception(e, NetworkTimeoutError)
        if 'Order' in e.name:
            return _wrap_exception(e, OrderError)

    # Handle HTTP/AIO Errors
    try:
        import aiohttp
        if isinstance(e, aiohttp.ClientError):
            return _wrap_exception(e, NetworkTimeoutError)
    except ImportError:
        pass  # Gracefully fallback if aiohttp unavailable

    # Handle Python Built-ins
    if isinstance(e, (ConnectionError, TimeoutError)):
        return _wrap_exception(e, NetworkTimeoutError)
    if isinstance(e, ValueError):
        return _wrap_exception(e, RPCConfigError)

    # Default to OperationalError
    return _wrap_exception(e, OperationalError)


class NetworkTimeoutError(TransientError):
    """Network timeouts and connection issues"""
    pass


class ProtocolError(OperationalError):
    """Errors in RPC/API protocol handling"""
    pass
