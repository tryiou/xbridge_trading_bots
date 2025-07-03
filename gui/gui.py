# gui/gui.py
import logging
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk

from ttkbootstrap import Style

from definitions.config_manager import ConfigManager
from definitions.logger import ColoredFormatter, setup_logging as setup_file_logging
from gui.frames import (ArbitrageFrame, BasicSellerFrame, LogFrame, PingPongFrame,
                        StdoutRedirector, TextLogHandler)


class GUI_Main:
    """Main GUI application class that hosts different strategy frames."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("XBridge Trading Bots")
        self.style = Style(theme="darkly")
        self.status_var = tk.StringVar(value="Idle")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(pady=10, padx=10)

        # Create the log frame and set up logging *before* creating other frames
        # that might use logging during their initialization.
        self.log_frame = LogFrame(self.notebook)
        self.setup_logging()  # Take control of logging BEFORE anything else.

        # Create a master ConfigManager to hold shared resources
        # This must be done *before* setup_logging to ensure all backend loggers exist.
        self.master_config_manager = ConfigManager(strategy="gui")

        # Create and add frames for each strategy
        self.strategy_frames = {
            'PingPong': PingPongFrame(self.notebook, self, self.master_config_manager),
            'Basic Seller': BasicSellerFrame(self.notebook, self, self.master_config_manager),
            'Arbitrage': ArbitrageFrame(self.notebook, self, self.master_config_manager),
        }
        for text, frame in self.strategy_frames.items():
            self.notebook.add(frame, text=text)

        # Add the log frame to the notebook at the end
        self.notebook.add(self.log_frame, text='Logs')

        self.create_status_bar()

        # Start the refresh loop for the initially selected tab
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        self.on_tab_changed()  # Manually trigger for the first tab

    def setup_logging(self):
        """Configures logging to display in the GUI and redirects stdout/stderr."""
        original_stdout = sys.stdout
        original_stderr = sys.stderr

        # Configure the root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)  # Set to DEBUG to see all logs

        # Since the backend loggers no longer add handlers in GUI mode,
        # we only need to clear the root logger to ensure a clean setup.
        root_logger.handlers.clear()  # Also clear root logger's handlers

        # Use the existing setup_logging function to add the file handler to the root logger.
        # This is forced to run even in GUI mode to restore file logging.
        log_dir = os.path.join(os.path.abspath(os.curdir), "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "gui_debug.log")  # Changed name for clarity
        setup_file_logging(name=None, log_file=log_file, level=logging.DEBUG, force=True)

        # Add the custom handler for the GUI log panel
        gui_handler = TextLogHandler(self.log_frame)
        # Use a standard, uncolored formatter for the GUI panel
        gui_formatter = logging.Formatter('%(asctime)s [%(name)-18s] - %(levelname)-7s - %(message)s',
                                          datefmt='%H:%M:%S')
        gui_handler.setFormatter(gui_formatter)
        root_logger.addHandler(gui_handler)

        # Add a handler to also print logs to the console (stdout)
        # Use the same ColoredFormatter as the standalone scripts
        console_formatter = ColoredFormatter('[%(asctime)s] [%(name)-18s] %(levelname)s - %(message)s')
        console_handler = logging.StreamHandler(original_stdout)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)

        # Redirect raw stdout and stderr for non-logging output (e.g., print() statements)
        sys.stdout = StdoutRedirector(self.log_frame, "INFO", original_stdout)
        sys.stderr = StdoutRedirector(self.log_frame, "ERROR", original_stderr)

        logging.info("Logging initialized. GUI is ready.")

    def create_status_bar(self) -> None:
        """Creates the status bar at the bottom of the main window."""
        status_frame = ttk.Frame(self.root)
        status_frame.pack(side="bottom", fill="x", padx=5, pady=5)
        status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        status_label.pack(fill="x")

    def on_tab_changed(self, event=None):
        """Handle tab changes to start/stop the appropriate refresh loops."""
        # Stop all refresh loops first
        for frame in self.strategy_frames.values():
            frame.stop_refresh()

        # Start the refresh loop for the selected tab
        selected_widget = self.root.nametowidget(self.notebook.select())
        if hasattr(selected_widget, 'start_refresh'):
            selected_widget.start_refresh()

    def on_closing(self) -> None:
        """Handles the application closing event by stopping bots in a background thread."""
        # Prevent multiple shutdown attempts
        if getattr(self, "_is_closing", False):
            return
        self._is_closing = True

        logging.info("Shutdown initiated. Stopping all running bots...")
        self.status_var.set("Shutting down... Please wait.")
        # Disable the close button to prevent multiple clicks
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        # Start the shutdown process in a separate thread to avoid freezing the GUI
        shutdown_thread = threading.Thread(target=self._shutdown_worker, daemon=True)
        shutdown_thread.start()

    def _shutdown_worker(self):
        """Worker thread to gracefully stop all bot threads."""
        running_frames = [
            frame for frame in self.strategy_frames.values() if frame.started
        ]

        # Stop each bot. The `stop` method will block this worker thread, which is what we want.
        for frame in running_frames:
            logging.info(f"Stopping {frame.strategy_name} bot...")
            # Wait indefinitely for each bot to stop
            frame.stop(blocking=True, reload_config=False)

        logging.info("All bots stopped. Closing application.")
        self.root.after(0, self.root.destroy)
