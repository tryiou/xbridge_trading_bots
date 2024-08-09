import logging

formatter = logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s')  # '%(asctime)s %(levelname)s %(message)s')


def setup_logging(name, log_file=None, level=logging.INFO, console=False):
    """To set up as many loggers as you want"""
    log_handle = logging.getLogger(name)
    log_handle.setLevel(level)

    # Add FileHandler if log_file is provided
    if log_file:
        handler = logging.FileHandler(log_file)
        handler.setFormatter(formatter)
        handler.setLevel(level)  # Set handler level to match logger level
        log_handle.addHandler(handler)

    # Add StreamHandler for console logging
    if console:
        ch = logging.StreamHandler()
        ch.setLevel(level)  # Set console handler level to match logger level
        log_handle.addHandler(ch)

    return log_handle
