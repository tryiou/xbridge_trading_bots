# gui/main_app.py
import logging
import queue
import signal
import threading
import time
import tkinter as tk
from tkinter import ttk

from ttkbootstrap import Style

from definitions.config_manager import ConfigManager
from definitions.error_handler import OperationalError
from gui.components.data_panels import BalancesPanel
from gui.components.logging_components import LogFrame
from gui.frames.strategy_frames import ArbitrageFrame, BasicSellerFrame, PingPongFrame
from gui.shutdown.gui_shutdown_coordinator import GUIShutdownCoordinator
from gui.utils.logging_setup import setup_console_logging, setup_gui_logging

logger = logging.getLogger(__name__)


class MainApplication:
    """Main GUI application class that hosts different strategy frames with centralized error handling."""

    def __init__(self, root=None):
        try:
            # Initialize console logging
            setup_console_logging()

            # Initialize the root window
            self._init_root_window(root)

            # Create master config manager
            self.master_config_manager = ConfigManager(strategy="gui")
            self.running_strategies = set()

            # Create main application structure
            self._create_main_structure()

            # Initialize strategy frames
            self._init_strategy_frames()

            # Setup logging components
            self._setup_logging()

            # Set up watchdog for UI responsiveness
            self.start_watchdog()

            # Initialize and start balance updater
            self._init_balance_updater()

            # Create status bar
            self.create_status_bar()

            # Start refresh loops for all strategy frames
            for frame in self.strategy_frames.values():
                frame.start_refresh()
                
            # Unify shutdown handlers for both GUI close and console signals
            self.root.protocol("WM_DELETE_WINDOW", self.initiate_shutdown_procedure)
            if hasattr(signal, 'SIGINT'):
                signal.signal(signal.SIGINT, self._handle_signal_interrupt)

        except Exception as e:
            error_msg = f"Critical error during application initialization: {str(e)}"
            logger.critical(error_msg, exc_info=True)
            # Use error handler if available
            if hasattr(self, 'master_config_manager') and self.master_config_manager:
                self.master_config_manager.error_handler.handle(
                    OperationalError(error_msg),
                    context={"stage": "application_init"}
                )
            # Show error in UI if root exists
            if hasattr(self, 'root') and self.root.winfo_exists():
                self.status_var.set(error_msg)
            else:
                print(error_msg)
                
    def _handle_signal_interrupt(self, signum, frame):
        """Handles POSIX signals by scheduling shutdown on main thread"""
        self.root.after(0, self.initiate_shutdown_procedure)

    def initiate_shutdown_procedure(self):
        """Unified shutdown procedure for all exit paths"""
        self.status_var.set("Shutting down... Please wait.")

        # Signal the balance updater thread to stop
        if hasattr(self, 'balance_stop_event'):
            self.balance_stop_event.set()
        if hasattr(self, 'balance_updater_thread') and self.balance_updater_thread.is_alive():
            self.balance_updater_thread.join(2.0)  # Give thread 2 seconds to exit

        shutdown_coordinator = GUIShutdownCoordinator(
            config_manager=self.master_config_manager,
            strategies=self.strategy_frames,
            gui_root=self.root
        )
        shutdown_coordinator.initiate_shutdown()

    def _init_root_window(self, root):
        """Initialize root window properties and signals."""
        title = "XBridge Trading Bots"
        if root is None:
            self.root = tk.Tk(className=title)
        else:
            self.root = root

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

        # Handle Ctrl+C/KeyboardInterrupt signals for clean shutdown
        def handle_signal(signum, frame):
            # Instead of scheduling on_closing, create shutdown coordinator immediately                                                                                                            
            shutdown_coordinator = GUIShutdownCoordinator(
                config_manager=self.master_config_manager,
                strategies=self.strategy_frames,
                gui_root=self.root
            )
            shutdown_coordinator.initiate_shutdown()

        if hasattr(signal, 'SIGINT'):
            signal.signal(signal.SIGINT, handle_signal)

        # Setup UI theme
        self.style = Style(theme="darkly")
        self.style.theme_use("darkly")
        self.root.configure(background=self.style.lookup("TFrame", "background"))
        self.status_var = tk.StringVar(value="Idle")

    def _create_main_structure(self):
        """Create main panels and layout structure."""
        main_panel = ttk.Frame(self.root)
        main_panel.pack(fill='both', expand=True, padx=10, pady=10)

        # Create notebook for tabs
        self.notebook = ttk.Notebook(main_panel)
        self.notebook.pack(fill='both', expand=True, pady=10)

        # Create shared balances panel below notebook
        balances_frame = ttk.LabelFrame(main_panel, text="Balances")
        balances_frame.pack(fill='x', padx=5, pady=(0, 5))
        self.balances_panel = BalancesPanel(balances_frame)
        self.balances_panel.pack(fill='both', expand=True)

    def _init_strategy_frames(self):
        """Initialize and add all strategy frames to the notebook."""
        self.strategy_frames = {
            'PingPong': PingPongFrame(self.notebook, self, self.master_config_manager),
            'Basic Seller': BasicSellerFrame(self.notebook, self, self.master_config_manager),
            'Arbitrage': ArbitrageFrame(self.notebook, self, self.master_config_manager),
        }
        for text, frame in self.strategy_frames.items():
            logger.debug(f"Initializing {text} strategy frame")
            self.notebook.add(frame, text=text)

    def _setup_logging(self):
        """Finalize logging setup after UI components are ready."""
        # Create and add the log frame as the last tab
        logger.debug("Initializing log frame")
        self.log_frame = LogFrame(self.notebook)
        self.notebook.add(self.log_frame, text='Logs')
        setup_gui_logging(self.log_frame)

    def _init_balance_updater(self):
        """Initialize and start the balance updater thread."""
        # Initialize queue and event
        self.balance_update_queue = queue.Queue()
        self.balance_update_interval = 5.0  # seconds
        self.balance_stop_event = threading.Event()

        # Create and start balance updater thread
        self.balance_updater_thread = threading.Thread(
            target=self._run_balance_updater,
            name=f"BalanceUpdater-{str(time.time())}",
            daemon=True
        )
        self.balance_updater_thread.start()

        # Schedule balance updates processing
        self.root.after(100, self._process_balance_updates)

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
        if hasattr(self, 'balance_stop_event'):
            self.balance_stop_event.set()
        if hasattr(self, 'balance_updater_thread') and self.balance_updater_thread.is_alive():
            self.balance_updater_thread.join(2.0)  # Give thread 2 seconds to exit

        shutdown_coordinator = GUIShutdownCoordinator(
            config_manager=self.master_config_manager,
            strategies=self.strategy_frames,
            gui_root=self.root
        )
        shutdown_coordinator.initiate_shutdown()

    def _run_balance_updater(self):
        """Runs in a separate thread to collect balance data with event-based stopping."""
        try:
            while not self.balance_stop_event.is_set():
                self._perform_balance_update()
        except Exception as e:
            self._handle_balance_thread_error(e)
        finally:
            self._cleanup_balance_queue()

    def _perform_balance_update(self):
        """Collect balance data and push to queue with error handling."""
        try:
            # Collect balance data
            balances = {}  # Use a dictionary to aggregate balances

            # Use lock to safely access tokens_dict
            with self.master_config_manager.resource_lock:
                for frame in self.strategy_frames.values():
                    if not getattr(frame, 'config_manager', None) or not hasattr(frame.config_manager, 'tokens'):
                        continue
                    tokens = frame.config_manager.tokens
                    for token_symbol, token_obj in tokens.items():
                        # Only process tokens that have both CEX and DEX components
                        if not (getattr(token_obj, 'cex', None) and getattr(token_obj, 'dex', None)):
                            continue

                        balance_total = token_obj.dex_total_balance or 0.0
                        balance_free = token_obj.dex_free_balance or 0.0
                        usd_price = token_obj.cex_usd_price or 0.0

                        if token_symbol not in balances:
                            balances[token_symbol] = {
                                "symbol": token_symbol,
                                "usd_price": usd_price,
                                "total": balance_total,
                                "free": balance_free
                            }
                        else:
                            existing_balance = balances[token_symbol]
                            # Prioritize positive or non-zero values
                            existing_balance["total"] = max(existing_balance["total"], balance_total)
                            existing_balance["free"] = max(existing_balance["free"], balance_free)
                            # Prioritize non-zero usd_price
                            if usd_price > 0:
                                existing_balance["usd_price"] = usd_price

            # Convert the dictionary to a list for the GUI
            data = list(balances.values())
            if not self.running_strategies:
                # logger.debug("No strategies running, displaying initial balances.")
                data = self._get_initial_balances_data()

            self.balance_update_queue.put(data)
        except Exception as e:
            logger.error(f"Error in balance updater thread: {e}", exc_info=True)
            if self.master_config_manager:
                self.master_config_manager.error_handler.handle(
                    OperationalError(f"Balance updater error: {str(e)}"),
                    context={"stage": "balance_updater"}
                )
        finally:
            # Wait for interval or stop signal
            if self.balance_stop_event.wait(self.balance_update_interval):
                raise SystemExit("Shutting down updater")  # Breaks outer loop

    def _handle_balance_thread_error(self, exception: Exception) -> None:
        """Log critical errors in balance updater thread."""
        error_msg = f"Critical error in balance updater thread: {str(exception)}"
        logger.critical(error_msg, exc_info=True)
        if self.master_config_manager:
            self.master_config_manager.error_handler.handle(
                OperationalError(error_msg),
                context={"stage": "balance_updater_thread"}
            )

    def _cleanup_balance_queue(self) -> None:
        """Clear balance queue on thread exit."""
        while not self.balance_update_queue.empty():
            try:
                self.balance_update_queue.get_nowait()
            except queue.Empty:
                break
        logger.info("Balance updater thread terminated")

    def _process_balance_updates(self):
        """Processes queued balance updates in the main Tkinter thread with error handling."""
        if not self.root.winfo_exists():
            return

        try:
            while not self.balance_update_queue.empty():
                data = self.balance_update_queue.get_nowait()
                if data is None:  # Stop signal
                    return
                self.balances_panel.update_data(data)
        except queue.Empty:
            pass  # No updates yet
        except Exception as e:
            logger.error(f"Error processing balance updates: {e}", exc_info=True)
            # Use centralized error handling
            if self.master_config_manager:
                self.master_config_manager.error_handler.handle(
                    OperationalError(f"Balance update processing error: {str(e)}"),
                    context={"stage": "process_balance_updates"}
                )
        finally:
            if self.root.winfo_exists():  # Only reschedule if still running
                self.root.after(250, self._process_balance_updates)  # Schedule next check

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
