import logging
import os

from .bcolors import bcolors

formatter = logging.Formatter('[%(asctime)s] [%(module)s] %(levelname)s - %(message)s')

_GUI_MODE_ACTIVE = False


def set_gui_mode(active=True):
    """Sets a global flag to indicate if the application is running in GUI mode."""
    global _GUI_MODE_ACTIVE
    _GUI_MODE_ACTIVE = active


class ColoredFormatter(logging.Formatter):
    """A custom formatter to add colors to log levels for console output."""

    def format(self, record):
        # Store the original levelname to restore it later, preventing side effects
        original_levelname = record.levelname

        level_colors = {
            logging.DEBUG: bcolors.OKCYAN,
            logging.INFO: bcolors.OKGREEN,
            logging.WARNING: bcolors.WARNING,
            logging.ERROR: bcolors.FAIL,
            logging.CRITICAL: bcolors.FAIL + bcolors.BOLD,
        }
        color = level_colors.get(record.levelno, bcolors.ENDC)
        record.levelname = f"{color}{original_levelname:<7s}{bcolors.ENDC}"  # Pad to 7 chars for alignment
        formatted_message = super().format(record)
        record.levelname = original_levelname  # Restore original levelname
        return formatted_message


class FlushStreamHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()


def setup_logging(name, log_file=None, level=logging.INFO, console=False, force=False):
    """To set up as many loggers as you want, with console flushing"""
    log_handle = logging.getLogger(name)
    log_handle.setLevel(level)

    # If the GUI is active, it will handle all handler configuration.
    # We just return the logger instance to prevent duplicate handlers.
    if _GUI_MODE_ACTIVE and not force:
        return log_handle

    if log_handle.handlers:
        log_handle.handlers.clear()

    if log_file:
        handler = logging.FileHandler(log_file)
        handler.setFormatter(formatter)
        handler.setLevel(level)
        log_handle.addHandler(handler)

    if console:
        ch = FlushStreamHandler()
        # Use the colored formatter for console output, and pad the name for alignment
        ch.setFormatter(ColoredFormatter('[%(asctime)s] [%(name)-20s] %(levelname)s - %(message)s'))
        ch.setLevel(level)
        log_handle.addHandler(ch)

    return log_handle


def setup_logger(strategy=None, ROOT_DIR=None):
    if strategy:
        general_log = setup_logging(name=f"{strategy}.general",
                                    log_file=ROOT_DIR + '/logs/' + strategy + '_general.log',
                                    level=logging.DEBUG,  # Changed to DEBUG for comprehensive logging
                                    console=True)
        general_log.propagate = True
        trade_log = setup_logging(name=f"{strategy}.trade",
                                  log_file=ROOT_DIR + '/logs/' + strategy + '_trade.log',
                                  level=logging.INFO,
                                  console=False)
        ccxt_log = setup_logging(name=f"{strategy}.ccxt",
                                 log_file=ROOT_DIR + '/logs/' + strategy + '_ccxt.log',
                                 level=logging.INFO,
                                 console=True)
        return general_log, trade_log, ccxt_log

    else:
        print("setup_logger(strategy=None)")
        os._exit(1)
