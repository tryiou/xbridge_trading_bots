# gui/gui.py
import logging
import os
import sys
import tkinter as tk
from tkinter import ttk

# Get module-specific logger
logger = logging.getLogger(__name__)

from ttkbootstrap import Style

from definitions.config_manager import ConfigManager
from definitions.logger import ColoredFormatter, setup_logging as setup_file_logging
from gui.frames import (ArbitrageFrame, BasicSellerFrame, LogFrame, PingPongFrame,
                        StdoutRedirector, TextLogHandler)
from gui.components.data_panels import BalancesPanel


class GUI_Main:
    """Main GUI application class that hosts different strategy frames."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("XBridge Trading Bots")

        # Handle Ctrl+C/KeyboardInterrupt signals for clean shutdown
        import signal
        def handle_signal(signum, frame):
            self.root.after(0, self.on_closing)

        if hasattr(signal, 'SIGINT'):
            signal.signal(signal.SIGINT, handle_signal)

        self.style = Style(theme="darkly")
        self.status_var = tk.StringVar(value="Idle")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(pady=10, padx=10)

        # Create a master ConfigManager to hold shared resources
        self.master_config_manager = ConfigManager(strategy="gui")

        # Create main panels
        main_panel = ttk.Frame(self.root)
        main_panel.pack(fill='both', expand=True, padx=10, pady=10)

        # Create notebook first
        self.notebook = ttk.Notebook(main_panel)
        self.notebook.pack(fill='both', expand=True, pady=10)

        # Create shared balances panel below notebook
        balances_frame = ttk.LabelFrame(main_panel, text="Balances")
        balances_frame.pack(fill='x', padx=5, pady=(0, 5))
        self.balances_panel = BalancesPanel(balances_frame)
        self.balances_panel.pack(fill='both', expand=True)

        # Create and add frames for each strategy
        self.strategy_frames = {
            'PingPong': PingPongFrame(self.notebook, self, self.master_config_manager),
            'Basic Seller': BasicSellerFrame(self.notebook, self, self.master_config_manager),
            'Arbitrage': ArbitrageFrame(self.notebook, self, self.master_config_manager),
        }
        for text, frame in self.strategy_frames.items():
            logger.debug(f"Initializing {text} strategy frame")
            self.notebook.add(frame, text=text)

        # Create and add the log frame as the last tab
        logger.debug("Initializing log frame")
        self.log_frame = LogFrame(self.notebook)
        self.notebook.add(self.log_frame, text='Logs')
        self.setup_logging()  # Setup logging AFTER GUI structure is finalized

        # Start periodic task to update shared balances panel
        self.update_shared_balances()

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
        log_level = logging.DEBUG
        console_handler.setLevel(logging.DEBUG)  # Show INFO+ to console
        root_logger.addHandler(console_handler)
        logger.info(f"Console logging initialized at {str(log_level)} level")

        # Redirect raw stdout and stderr for non-logging output (e.g., print() statements)
        sys.stdout = StdoutRedirector(self.log_frame, "INFO", original_stdout)
        sys.stderr = StdoutRedirector(self.log_frame, "ERROR", original_stderr)

        logger.info("Logging initialized. GUI is ready.")

    def create_status_bar(self) -> None:
        """Creates the status bar at the bottom of the main window."""
        status_frame = ttk.Frame(self.root)
        status_frame.pack(side="bottom", fill="x", padx=5, pady=5)
        status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        status_label.pack(fill="x")

    def on_tab_changed(self, event=None):
        """Handle tab changes to start/stop the appropriate refresh loops."""
        tab_name = self.notebook.tab('current')['text']
        logger.info(f"Tab changed to: {tab_name}")

        # Stop all refresh loops first
        logger.debug("Stopping all refresh loops")
        for frame in self.strategy_frames.values():
            frame.stop_refresh()

        # Start the refresh loop for the selected tab
        selected_widget = self.root.nametowidget(self.notebook.select())
        if hasattr(selected_widget, 'start_refresh'):
            logger.debug(f"Starting refresh loop for {tab_name}")
            selected_widget.start_refresh()

    def on_closing(self) -> None:
        """Handles application closing event using coordinated shutdown sequence"""
        from definitions.shutdown import ShutdownCoordinator

        ShutdownCoordinator.initiate_shutdown(
            config_manager=self.master_config_manager,
            strategies=self.strategy_frames,
            gui_root=self.root
        )
        self.status_var.set("Shutting down... Please wait.")

    def _shutdown_worker(self):
        """Worker thread to gracefully stop all bot threads."""
        logger.info("Executing shutdown worker thread")
        running_frames = [
            frame for frame in self.strategy_frames.values()
            if frame.started and not frame.stopping
        ]
        logger.debug(f"Processing {len(running_frames)} running frames")

        # First stop all frames and wait for them
        for frame in running_frames:
            logger.debug(f"Stopping {frame.strategy_name} bot")
            frame.stop(blocking=True, reload_config=False)

        # Then cancel all orders
        for frame in running_frames:
            logger.debug(f"Canceling orders for {frame.strategy_name}")
            frame.cancel_all()

        # Direct destruction instead of using after()
        self._final_shutdown()

    def _final_shutdown(self):
        """Final cleanup and window destruction."""
        # Destroy all strategy frames first
        for frame in self.strategy_frames.values():
            frame.cleanup()

        # Then destroy root window
        if self.root and self.root.winfo_exists():
            try:
                # Destroy any child windows first
                for child in self.root.winfo_children():
                    child.destroy()

                # Now destroy root
                self.root.quit()
                self.root.destroy()
            except tk.TclError:
                pass
        # Ensure process exits
        os._exit(0)

    def update_shared_balances(self):
        """Centralized balance refresh using tokens from strategy frames"""
        # logger.debug("Updating shared balances panel")
        data = []
        tokens_seen = set()

        # Collect tokens from all strategy frames
        for frame in self.strategy_frames.values():
            if getattr(frame, 'config_manager', None) and hasattr(frame.config_manager, 'tokens'):
                tokens = frame.config_manager.tokens
                for token_symbol, token_obj in tokens.items():
                    if token_obj.cex and token_obj.dex and token_symbol not in tokens_seen:
                        usd_price = token_obj.cex.usd_price or 0.0
                        total = token_obj.dex.total_balance or 0.0
                        free = token_obj.dex.free_balance or 0.0
                        data.append({
                            "symbol": token_symbol,
                            "usd_price": usd_price,
                            "total": total,
                            "free": free
                        })
                        tokens_seen.add(token_symbol)

        # Update the single shared balances panel
        self.balances_panel.update_data(data)

        self._balances_update_id = self.root.after(2000, self.update_shared_balances)
