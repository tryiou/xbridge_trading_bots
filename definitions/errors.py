"""
Standardized error classes for the trading application
"""


class AppError(Exception):
    """Base class for all application errors"""

    def __init__(self, message, context=None):
        super().__init__(message)
        self.context = context or {}

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
