# gui/utils/logging_setup.py
import logging
import os
import sys

from definitions.logger import ColoredFormatter, setup_logging as setup_file_logging
from gui.components.logging_components import TextLogHandler, StdoutRedirector

def setup_console_logging():
    """Initializes console logging before GUI components are ready"""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Clear existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Setup file logging first
    log_dir = os.path.join(os.path.abspath(os.curdir), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "gui_debug.log")
    setup_file_logging(name=None, log_file=log_file, level=logging.DEBUG, force=True)

    # Add colored console handler
    console_formatter = ColoredFormatter('[%(asctime)s] [%(name)-20s] %(levelname)-8s - %(message)s')
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)

def setup_gui_logging(log_frame):
    """Finalizes logging setup after GUI components are initialized"""
    root_logger = logging.getLogger()

    # Add GUI panel handler
    gui_handler = TextLogHandler(log_frame)
    gui_formatter = logging.Formatter('[%(asctime)s] [%(name)-20s] %(levelname)-8s - %(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S')
    gui_handler.setFormatter(gui_formatter)
    root_logger.addHandler(gui_handler)

    # Redirect stdout/stderr after GUI is ready
    sys.stdout = StdoutRedirector(log_frame, "INFO", sys.__stdout__)
    sys.stderr = StdoutRedirector(log_frame, "ERROR", sys.__stderr__)

    logging.getLogger(__name__).info("GUI logging initialized")