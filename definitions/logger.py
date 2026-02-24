import logging
import os
import contextvars
from typing import Optional, Tuple

from .bcolors import bcolors

# Correlation ID for tracking async flows across modules
correlation_id = contextvars.ContextVar('correlation_id', default='SYS')

class CorrelationIdFilter(logging.Filter):
    """Injects the current async correlation ID into the log record."""
    def filter(self, record):
        record.correlation_id = correlation_id.get()
        return True

formatter = logging.Formatter('[%(asctime)s] [%(correlation_id)s] [%(name)-20s] %(levelname)-8s - %(message)s')

_GUI_MODE_ACTIVE = False

def set_gui_mode(active: bool = True) -> None:
    """Sets a global flag to indicate if the application is running in GUI mode."""
    global _GUI_MODE_ACTIVE
    _GUI_MODE_ACTIVE = active

class ColoredFormatter(logging.Formatter):
    """A custom formatter to add colors to log levels for console output."""
    def format(self, record: logging.LogRecord) -> str:
        original_levelname = record.levelname
        level_colors = {
            logging.DEBUG: bcolors.OKCYAN,
            logging.INFO: bcolors.OKGREEN,
            logging.WARNING: bcolors.WARNING,
            logging.ERROR: bcolors.FAIL,
            logging.CRITICAL: bcolors.FAIL + bcolors.BOLD,
        }
        color = level_colors.get(record.levelno, bcolors.ENDC)
        record.levelname = f"{color}{original_levelname:<7s}{bcolors.ENDC}"
        formatted_message = super().format(record)
        record.levelname = original_levelname
        return formatted_message

class FlushStreamHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()

def setup_logging(name: str, log_file: Optional[str] = None,
                 level: int = logging.INFO, console: bool = False, force: bool = False) -> logging.Logger:
    """To set up as many loggers as you want, with console flushing"""
    log_handle = logging.getLogger(name)
    log_handle.setLevel(level)
    log_handle.addFilter(CorrelationIdFilter())

    if _GUI_MODE_ACTIVE and not force:
        return log_handle

    if log_handle.handlers:
        log_handle.handlers.clear()

    if log_file:
        handler = logging.FileHandler(log_file)
        handler.setFormatter(formatter)
        handler.setLevel(level)
        handler.addFilter(CorrelationIdFilter())
        log_handle.addHandler(handler)

    if console:
        ch = FlushStreamHandler()
        ch.setFormatter(ColoredFormatter('[%(asctime)s] [%(correlation_id)s] [%(name)-20s] %(levelname)s - %(message)s'))
        ch.setLevel(level)
        ch.addFilter(CorrelationIdFilter())
        log_handle.addHandler(ch)

    return log_handle

def silence_noisy_loggers() -> None:
    """Sets the logging level for noisy third-party libraries to INFO."""
    logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)
    logging.getLogger("ccxt.base.exchange").setLevel(logging.INFO)
    logging.getLogger("asyncio").setLevel(logging.INFO)

def setup_logger(strategy: str, ROOT_DIR: str) -> Tuple[logging.Logger, logging.Logger, logging.Logger]:
    """Setup logging for a trading strategy."""
    logs_dir = os.path.join(ROOT_DIR, 'logs')
    os.makedirs(logs_dir, exist_ok=True)

    general_log = setup_logging(name=f"{strategy}.general",
                                log_file=os.path.join(logs_dir, strategy + '_general.log'),
                                level=logging.DEBUG,
                                console=True)
    general_log.propagate = True

    trade_log = setup_logging(name=f"{strategy}.trade",
                              log_file=os.path.join(logs_dir, strategy + '_trade.log'),
                              level=logging.INFO,
                              console=False)

    ccxt_log = setup_logging(name=f"{strategy}.ccxt",
                             log_file=os.path.join(logs_dir, strategy + '_ccxt.log'),
                             level=logging.INFO,
                             console=True)

    silence_noisy_loggers()

    return general_log, trade_log, ccxt_log