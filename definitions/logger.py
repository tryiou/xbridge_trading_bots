import logging
import os

formatter = logging.Formatter('[%(asctime)s] [%(module)s] %(levelname)s - %(message)s')


class FlushStreamHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()


def setup_logging(name, log_file=None, level=logging.INFO, console=False):
    """To set up as many loggers as you want, with console flushing"""
    log_handle = logging.getLogger(name)
    log_handle.setLevel(level)

    if log_handle.handlers:
        log_handle.handlers.clear()

    if log_file:
        handler = logging.FileHandler(log_file)
        handler.setFormatter(formatter)
        handler.setLevel(level)
        log_handle.addHandler(handler)

    if console:
        ch = FlushStreamHandler()
        ch.setFormatter(formatter)
        ch.setLevel(level)
        log_handle.addHandler(ch)

    return log_handle


def setup_logger(strategy=None, ROOT_DIR=None):
    if strategy:
        general_log = setup_logging(name="GENERAL_LOG",
                                    log_file=ROOT_DIR + '/logs/' + strategy + '_general.log',
                                    level=logging.DEBUG,  # Changed to DEBUG for comprehensive logging
                                    console=True)
        general_log.propagate = False
        trade_log = setup_logging(name="TRADE_LOG",
                                  log_file=ROOT_DIR + '/logs/' + strategy + '_trade.log',
                                  level=logging.INFO,
                                  console=False)
        ccxt_log = setup_logging(name="CCXT_LOG",
                                 log_file=ROOT_DIR + '/logs/' + strategy + '_ccxt.log',
                                 level=logging.INFO,
                                 console=True)
        return general_log, trade_log, ccxt_log

    else:
        print("setup_logger(strategy=None)")
        os._exit(1)
