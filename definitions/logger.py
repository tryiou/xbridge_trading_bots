import logging

formatter = logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s')  # '%(asctime)s %(levelname)s %(message)s')


def setup_logger(name, log_file, level=logging.INFO, console=False):
    """To setup as many loggers as you want"""
    handler = logging.FileHandler(log_file)
    handler.setFormatter(formatter)
    # handler.setFormatter()
    log_handle = logging.getLogger(name)
    log_handle.setLevel(level)
    log_handle.addHandler(handler)
    if console:
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        log_handle.addHandler(ch)
    return log_handle
