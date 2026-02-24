"""
Centralized constants for the XBridge Trading Bots application.

This module consolidates all magic numbers, configuration defaults, and
enumerated values to improve maintainability and reduce code smells.
"""

from enum import Enum
from typing import Final


class OrderStatus(Enum):
    """DEX order status codes."""

    OPEN = 0
    FINISHED = 1
    OTHERS = 2
    ERROR_SWAP = -1
    CANCELLED_WITHOUT_CALL = -2


class PriceFieldName(Enum):
    """Exchange-specific field names for last price."""

    KUCOIN = "last"
    BINANCE = "lastPrice"
    DEFAULT = "lastTradeRate"


class LogLevel(Enum):
    """Application log levels."""

    DEBUG = 1
    INFO = 2
    WARNING = 3
    ERROR = 4
    CRITICAL = 5


# =============================================================================
# Timing Constants (in seconds)
# =============================================================================

CCXT_PRICE_REFRESH_INTERVAL: Final[float] = 2.0
UPDATE_BALANCES_DELAY: Final[float] = 0.5
FLUSH_DELAY: Final[int] = 15 * 60  # 15 minutes
SLEEP_INTERVAL: Final[int] = 1
CACHE_DURATION: Final[float] = 3.0
PROXY_STARTUP_TIMEOUT: Final[int] = 10
SHUTDOWN_TIMEOUT: Final[float] = 45.0

# =============================================================================
# Network & Connection Constants
# =============================================================================

DEFAULT_RPC_TIMEOUT: Final[int] = 30
DEFAULT_RPC_PORT: Final[int] = 41412
DEFAULT_RPC_PORT_TESTNET: Final[int] = 41413
DEFAULT_PROXY_PORT: Final[int] = 2233
PORT_CHECK_TIMEOUT: Final[float] = 2.0
MAX_CONCURRENT_RPC_TASKS: Final[int] = 5

# =============================================================================
# Rate Limiting
# =============================================================================

CCXT_RATE_LIMIT: Final[int] = 1000  # milliseconds
MAX_RPC_RETRIES: Final[int] = 5
RETRY_DELAYS: Final[list[int]] = [1, 3, 5]

# =============================================================================
# Order & Trading Constants
# =============================================================================

DEFAULT_TRADE_SIZE_DIGITS: Final[int] = 8
PRICE_VARIATION_TOLERANCE_DEFAULT: Final[float] = 0.01
TYPICAL_TX_SIZE_ESTIMATE: Final[int] = 500  # bytes
ORDERBOOK_UPDATE_DELAY: Final[float] = 2.0

# =============================================================================
# Debug Levels
# =============================================================================

DEBUG_LEVEL_QUIET: Final[int] = 0
DEBUG_LEVEL_ERRORS: Final[int] = 1
DEBUG_LEVEL_INFO: Final[int] = 2
DEBUG_LEVEL_PARAMS: Final[int] = 3
DEBUG_LEVEL_VERBOSE: Final[int] = 4

# =============================================================================
# File & Directory Paths
# =============================================================================

CONFIG_DIR: Final[str] = "config"
CONFIG_TEMPLATES_DIR: Final[str] = "config/templates"
DATA_DIR: Final[str] = "data"
LOGS_DIR: Final[str] = "logs"

CONFIG_CCXT: Final[str] = "config_ccxt.yaml"
CONFIG_COINS: Final[str] = "config_coins.yaml"
CONFIG_XBRIDGE: Final[str] = "config_xbridge.yaml"
CONFIG_PINGPONG: Final[str] = "config_pingpong.yaml"
CONFIG_BASIC_SELLER: Final[str] = "config_basic_seller.yaml"

# =============================================================================
# Strategy Configuration
# =============================================================================

SUPPORTED_STRATEGIES: Final[list[str]] = [
    "pingpong",
    "basic_seller",
    "gui",
]

DEFAULT_OPERATION_INTERVALS: Final[dict[str, int]] = {
    "pingpong": 15,
    "basic_seller": 15,
}

# =============================================================================
# Error Handling
# =============================================================================

ERROR_CONTEXT_MAX_DEPTH: Final[int] = 5
MAX_ERROR_LOG_SIZE: Final[int] = 1000

# =============================================================================
# GUI Constants
# =============================================================================

GUI_UPDATE_INTERVAL: Final[float] = 1.0
GUI_LOG_BUFFER_SIZE: Final[int] = 1000
GUI_REFRESH_RATE: Final[float] = 0.5
