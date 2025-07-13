# gui/main_app.py
import logging
import os
import sys
import tkinter as tk
from tkinter import ttk
import signal
import asyncio 
import queue 
import threading 
from ttkbootstrap import Style

from definitions.config_manager import ConfigManager
from gui.frames.strategy_frames import ArbitrageFrame, BasicSellerFrame, PingPongFrame
from gui.components.data_panels import BalancesPanel
from gui.components.logging_components import LogFrame
from gui.utils.logging_setup import setup_console_logging, setup_gui_logging
from gui.shutdown.gui_shutdown_coordinator import GUIShutdownCoordinator

logger = logging.getLogger(__name__)


class MainApplication:
    """Main GUI application class that hosts different strategy frames."""

    def __init__(self):
        # Initialize console logging FIRST
        setup_console_logging()
        
        title = "XBridge Trading Bots"
        self.root = tk.Tk(className=title) 

        # Get screen dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        # Desired default size
        default_width = 1300
        default_height = 600

        # Fallback to 90% of screen if too small
        window_width = min(default_width, int(screen_width * 0.9))
        window_height = min(default_height, int(screen_height * 0.9))

        # Set window size and minsize
        self.root.geometry(f"{window_width}x{window_height}")
        self.root.minsize(min(1000, screen_width), min(400, screen_height))

        logger.info(f"Screen size detected: {screen_width}x{screen_height}")
        logger.info(f"Window initialized with size: {window_width}x{window_height}")

        self.root.title(title)
        self._watchdog_count = 0
        self.running_strategies = set() 


        logger.info("Initializing GUI application")

        # Handle Ctrl+C/KeyboardInterrupt signals for clean shutdown
        def handle_signal(signum, frame):
            self.root.after(0, self.on_closing)

        if hasattr(signal, 'SIGINT'):
            signal.signal(signal.SIGINT, handle_signal)
        self.style = Style(theme="darkly")
        self.style.theme_use("darkly")
        # NEW LINE ADDED BELOW:
        self.root.configure(background=self.style.lookup("TFrame", "background")) 

        self.status_var = tk.StringVar(value="Idle")

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

        # Finalize logging setup AFTER GUI components are ready
        setup_gui_logging(self.log_frame)
        self.start_watchdog()

        # Start asynchronous task to update shared balances panel (using threading like original gui)
        self.balance_update_queue = queue.Queue()
        self.balance_updater_thread = threading.Thread(target=self._run_balance_updater, daemon=True)
        self.balance_updater_thread.start()
        self.root.after(100, self._process_balance_updates)


        self.create_status_bar()

        # Start refresh loops for all strategy frames
        for frame in self.strategy_frames.values():
            frame.start_refresh()
    def start_watchdog(self):
        """Periodic check to maintain GUI responsiveness"""

        def watchdog():
            if self._watchdog_count > 5:  # 25 seconds no response
                self.root.update_idletasks()
                self._watchdog_count = 0
            else:
                self._watchdog_count += 1
            self.root.after(5000, watchdog)

        self.root.after(5000, watchdog)

    def create_status_bar(self) -> None:
        """Creates the status bar at the bottom of the main window."""
        status_frame = ttk.Frame(self.root)
        status_frame.pack(side="bottom", fill="x", padx=5, pady=5)
        status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        status_label.pack(fill="x")

    def on_closing(self) -> None:
        """Handles application closing event by signaling shutdown coordinator"""
        self.status_var.set("Shutting down... Please wait.")
        
        # Signal the balance updater thread to stop
        if hasattr(self, 'balance_updater_thread') and self.balance_updater_thread.is_alive():
            self.balance_update_queue.put(None) # Signal to stop

        shutdown_coordinator = GUIShutdownCoordinator(
            config_manager=self.master_config_manager,
            strategies=self.strategy_frames,
            gui_root=self.root
        )
        shutdown_coordinator.initiate_shutdown()

    def _run_balance_updater(self):
        """Runs in a separate thread to collect balance data."""
        while True:
            try:
                # Collect balance data
                balances = {}  # Use a dictionary to aggregate balances

                # Use lock to safely access tokens_dict
                with self.master_config_manager.resource_lock:
                    for frame in self.strategy_frames.values():
                        if getattr(frame, 'config_manager', None) and hasattr(frame.config_manager, 'tokens'):
                            tokens = frame.config_manager.tokens
                            for token_symbol, token_obj in tokens.items():
                                # Only process tokens that have both CEX and DEX components
                                if getattr(token_obj, 'cex', None) and getattr(token_obj, 'dex', None):
                                    balance_total = token_obj.dex_total_balance or 0.0
                                    balance_free = token_obj.dex_free_balance or 0.0
                                    
                                    # Ensure usd_price is not None before using it, default to 0.0
                                    usd_price = token_obj.cex_usd_price if token_obj.cex_usd_price is not None else 0.0

                                    if token_symbol not in balances:
                                        # Add new token balance
                                        balances[token_symbol] = {
                                            "symbol": token_symbol,
                                            "usd_price": usd_price,
                                            "total": balance_total,
                                            "free": balance_free
                                        }
                                    else:
                                        # Update existing token balance, prioritizing positive values
                                        existing_balance = balances[token_symbol]

                                        # Prioritize positive or non-zero values
                                        existing_balance["total"] = max(existing_balance["total"], balance_total)
                                        existing_balance["free"] = max(existing_balance["free"], balance_free)
                                        # Prioritize non-zero usd_price
                                        existing_balance["usd_price"] = usd_price if usd_price > 0 else existing_balance["usd_price"]
                
                # Convert the dictionary to a list for the GUI
                data = list(balances.values())

                # Only update if there are running strategies, else display initial state
                if not self.running_strategies:
                    # logger.debug("No strategies running, displaying initial balances.")
                    data = self._get_initial_balances_data()
                
                self.balance_update_queue.put(data)
                
                # Check for stop signal
                if not self.balance_update_queue.empty() and self.balance_update_queue.queue[0] is None:
                    self.balance_update_queue.get() # Consume the None
                    break

                threading.Event().wait(2) # Wait for 2 seconds

            except Exception as e:
                logger.error(f"Error in balance updater thread: {e}", exc_info=True)
                threading.Event().wait(5) # Wait longer on error

    def _process_balance_updates(self):
        """Processes queued balance updates in the main Tkinter thread (mimicking gui/gui.py)."""
        if not self.root.winfo_exists():
            return

        try:
            while not self.balance_update_queue.empty():
                data = self.balance_update_queue.get_nowait()
                if data is None: # Stop signal
                    return
                self.balances_panel.update_data(data)
        except queue.Empty:
            pass # No updates yet
        except Exception as e:
            logger.error(f"Error processing balance updates in main thread: {e}", exc_info=True)
        finally:
            if self.root.winfo_exists(): # Only reschedule if still running
                self.root.after(250, self._process_balance_updates) # Schedule next check

    def notify_strategy_started(self, strategy_name: str):
        """Notifies MainApplication that a strategy has started."""
        self.running_strategies.add(strategy_name)
        logger.info(f"Strategy '{strategy_name}' started. Active strategies: {len(self.running_strategies)}")

    def notify_strategy_stopped(self, strategy_name: str):
        """Notifies MainApplication that a strategy has stopped."""
        if strategy_name in self.running_strategies:
            self.running_strategies.remove(strategy_name)
            logger.info(f"Strategy '{strategy_name}' stopped. Active strategies: {len(self.running_strategies)}")
            # If no strategies are running, clear the balances display
            if not self.running_strategies:
                self.balances_panel.update_data(self._get_initial_balances_data())

    def _get_initial_balances_data(self) -> list:
        """Returns the initial state of aggregate coins with 0.00 value for each field."""
        initial_balances = []
        # Collect all unique tokens from all strategy frames to get the full list of aggregate coins
        all_tokens = {}
        for frame in self.strategy_frames.values():
            if getattr(frame, 'config_manager', None) and hasattr(frame.config_manager, 'tokens'):
                for token_symbol, token_obj in frame.config_manager.tokens.items():
                    all_tokens[token_symbol] = token_obj

        for token_symbol in all_tokens.keys():
            initial_balances.append({
                "symbol": token_symbol,
                "usd_price": 0.00,
                "total": 0.00,
                "free": 0.00
            })
        return initial_balances
