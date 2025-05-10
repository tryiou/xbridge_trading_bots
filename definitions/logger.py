import logging
import os

formatter = logging.Formatter('[%(asctime)s] [%(module)s] %(levelname)s - %(message)s')


def setup_logging(name, log_file=None, level=logging.INFO, console=False):
    """To set up as many loggers as you want"""
    # Define formatter

    log_handle = logging.getLogger(name)
    log_handle.setLevel(level)

    # Clear existing handlers to avoid duplicates if called multiple times
    if log_handle.handlers:
        log_handle.handlers.clear()

    # Add FileHandler if log_file is provided
    if log_file:
        handler = logging.FileHandler(log_file)
        handler.setFormatter(formatter)
        handler.setLevel(level)
        log_handle.addHandler(handler)

    # Add StreamHandler for console logging
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)  # Also apply formatter to console handler
        ch.setLevel(level)
        log_handle.addHandler(ch)

    return log_handle


def setup_logger(strategy=None):
    from definitions.bot_init import context
    if strategy:
        general_log = setup_logging(name="GENERAL_LOG",
                                    log_file=context.ROOT_DIR + '/logs/' + strategy + '_general.log',
                                    level=logging.INFO,
                                    console=True)
        general_log.propagate = False
        trade_log = setup_logging(name="TRADE_LOG",
                                  log_file=context.ROOT_DIR + '/logs/' + strategy + '_trade.log',
                                  level=logging.INFO,
                                  console=False)
        ccxt_log = setup_logging(name="CCXT_LOG",
                                 log_file=context.ROOT_DIR + '/logs/' + strategy + '_ccxt.log',
                                 level=logging.INFO,
                                 console=True)
        return general_log, trade_log, ccxt_log

    else:
        print("setup_logger(strategy=None)")
        os._exit(1)
