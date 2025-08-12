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
        super().__init__(message)
        self.context = context or {}

    def __str__(self):
        return f"{self.__class__.__name__}: {super().__str__()} | Context: {self.context}"


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


def convert_exception(e: Exception) -> 'AppError':
    """Convert third-party exceptions to native application error types"""
    # Handle CCXT Errors
    from .errors import AppError, InsufficientFundsError, NetworkTimeoutError, OrderError, RPCConfigError, \
        OperationalError

    # Handle CCXT Errors
    if hasattr(e, 'name'):
        if e.name == 'InsufficientFunds':
            exc = InsufficientFundsError(str(e))
            exc.__cause__ = e
            return exc
        if e.name == 'NetworkError':
            exc = NetworkTimeoutError(str(e))
            exc.__cause__ = e
            return exc
        if 'Order' in e.name:
            exc = OrderError(str(e))
            exc.__cause__ = e
            return exc

    # Handle HTTP/AIO Errors
    try:
        import aiohttp
        if isinstance(e, aiohttp.ClientError):
            exc = NetworkTimeoutError(str(e))
            exc.__cause__ = e
            return exc
    except ImportError:
        pass  # Gracefully fallback if aiohttp unavailable

    # Handle Python Built-ins
    if isinstance(e, (ConnectionError, TimeoutError)):
        exc = NetworkTimeoutError(str(e))
        exc.__cause__ = e
        return exc
    if isinstance(e, ValueError):
        exc = RPCConfigError(str(e))
        exc.__cause__ = e
        return exc

    # Preserve existing AppErrors
    if isinstance(e, AppError):
        return e

    # Default to OperationalError
    exc = OperationalError(str(e))
    exc.__cause__ = e
    return exc


class NetworkTimeoutError(TransientError):
    """Network timeouts and connection issues"""
    pass


class ProtocolError(OperationalError):
    """Errors in RPC/API protocol handling"""
    pass
