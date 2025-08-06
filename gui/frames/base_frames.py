# gui/frames/base_frames.py
import abc
import asyncio
import logging
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ruamel.yaml import YAML

from definitions.config_manager import ConfigManager
from definitions.starter import run_async_main
from gui.components.data_panels import OrdersPanel
from gui.utils.async_updater import AsyncUpdater
from definitions.error_handler import OperationalError, ConfigurationError

if TYPE_CHECKING:
    from gui.main_app import MainApplication
    from gui.config_windows.base_config_window import BaseConfigWindow

logger = logging.getLogger(__name__)


class BaseStrategyFrame(ttk.Frame):
    """Base class for strategy-specific frames in the GUI with centralized error handling."""

    def __init__(self, parent, main_app: "MainApplication", strategy_name: str, master_config_manager: ConfigManager):
        super().__init__(parent)
        self.main_app = main_app
        self.master_config_manager = master_config_manager
        self.strategy_name = strategy_name
        self.config_manager: ConfigManager | None = None
        self.send_process: threading.Thread | None = None
        self.cancel_all_thread: threading.Thread | None = None
        self.started = False
        self.stopping = False
        self.cleaned = True  # initialize as True; no cleanup needed on init
        # Button attributes that will be created by create_standard_buttons
        self.btn_start: ttk.Button | None = None
        self.btn_stop: ttk.Button | None = None
        self.btn_cancel_all: ttk.Button | None = None
        self.btn_configure: ttk.Button | None = None

        self.orders_updater: AsyncUpdater | None = None
        self.orders_panel: OrdersPanel | None = None # Will be set by StandardStrategyFrame or subclasses

        self.stop_check_id = None # New attribute for periodic stop check
        self.force_stop_timeout_id = None # New attribute for failsafe force-stop timeout

        self.initialize_config()
        self.create_widgets()

    def initialize_config(self, loadxbridgeconf: bool = True):
        """Initializes the configuration manager for the specific strategy."""
        try:
            self.config_manager = ConfigManager(strategy=self.strategy_name, master_manager=self.master_config_manager)
            self.config_manager.initialize(loadxbridgeconf=loadxbridgeconf)
        except Exception as e:
            error_msg = f"Error initializing {self.strategy_name}: {e}"
            self.main_app.status_var.set(error_msg)
            # Use centralized error handling if available
            if self.config_manager:
                self.config_manager.error_handler.handle(
                    ConfigurationError(error_msg),
                    context={"strategy": self.strategy_name}
                )

    def create_widgets(self):
        """Placeholder for creating strategy-specific widgets. To be overridden."""
        pass

    def _pre_start_validation(self):
        """Validate critical pre-conditions before starting"""
        if not self.config_manager:
            raise RuntimeError("Config manager not initialized")
        if not hasattr(self.config_manager, 'strategy_instance'):
            raise RuntimeError("Strategy not properly initialized")
        if self.send_process and self.send_process.is_alive():
            raise RuntimeError("Bot thread already running")

    def _critical_error_handler(self, error):
        """Handle critical errors in GUI-safe context"""
        self.stop() # Call stop without blocking argument
        error_msg = f"CRITICAL ERROR: {str(error)}"
        self.main_app.status_var.set(error_msg)
        # Use centralized error handling if available
        if self.config_manager:
            self.config_manager.error_handler.handle(
                OperationalError(error_msg),
                context={"strategy": self.strategy_name},
                exc_info=True
            )

    def _thread_wrapper(self, func, *args):
        """Wrap thread execution for proper error handling"""
        try:
            func(*args)
        except Exception as e:
            self.main_app.root.after(0, self._critical_error_handler, e)

    def _signal_controller_shutdown(self):
        """Signals the strategy controller's asyncio loop to shut down."""
        if self.config_manager and self.config_manager.controller and self.config_manager.controller.loop:
            shutdown_event = self.config_manager.controller.shutdown_event
            lconf = self.config_manager
            loop = self.config_manager.controller.loop

            if not loop.is_closed():
                def set_event():
                    with lconf.resource_lock:
                        shutdown_event.set()

                if loop.is_running():
                    loop.call_soon_threadsafe(set_event)

    def _join_bot_thread(self, timeout: float):
        """Waits for the bot thread to terminate, with a timeout."""
        if self.send_process:
            self.send_process.join(timeout)

            if self.send_process.is_alive():
                if self.config_manager: # Ensure config_manager exists before logging
                    self.config_manager.general_log.warning("Bot thread did not terminate gracefully.")
                    if (self.config_manager.controller and
                            self.config_manager.controller.loop and
                            not self.config_manager.controller.loop.is_closed()):
                        self.config_manager.controller.loop.call_soon_threadsafe(
                            self.config_manager.controller.shutdown_event.set)

    def _start_stop_monitoring(self, reload_config: bool):
        """Starts periodic monitoring of the bot thread's status."""
        # Cancel any existing monitoring to prevent duplicates
        if self.stop_check_id:
            self.after_cancel(self.stop_check_id)
        if self.force_stop_timeout_id:
            self.after_cancel(self.force_stop_timeout_id)

        # Schedule periodic check for thread termination
        self.stop_check_id = self.after(250, self._check_bot_thread_status, reload_config)
        # Schedule a failsafe force-stop after a longer timeout (e.g., 10 seconds)
        self.force_stop_timeout_id = self.after(10000, self._force_finalize_stop, reload_config)

    def _check_bot_thread_status(self, reload_config: bool):
        """Periodically checks if the bot thread has terminated."""
        if not self.winfo_exists(): # Check if GUI element still exists
            self._cancel_stop_monitoring()
            return

        if self.send_process and not self.send_process.is_alive():
            # Thread has terminated, proceed with finalization
            if self.config_manager:
                self.config_manager.general_log.info(f"Detected {self.strategy_name} bot thread terminated. Finalizing stop.")
            self._finalize_stop(reload_config)
            self._cancel_stop_monitoring()
        else:
            # Thread is still alive, reschedule check
            self.stop_check_id = self.after(250, self._check_bot_thread_status, reload_config)

    def _force_finalize_stop(self, reload_config: bool):
        """Failsafe to finalize stop if the bot thread doesn't terminate gracefully."""
        if not self.winfo_exists(): # Check if GUI element still exists
            self._cancel_stop_monitoring()
            return

        if self.send_process and self.send_process.is_alive():
            if self.config_manager:
                self.config_manager.general_log.warning(
                    f"{self.strategy_name} bot thread did not terminate gracefully after timeout. Forcing cleanup.")
            # Attempt to join one last time with a very short timeout, then proceed
            self.send_process.join(0.1)
        
        self._finalize_stop(reload_config)
        self._cancel_stop_monitoring()

    def _cancel_stop_monitoring(self):
        """Cancels any pending stop monitoring and force-stop timeouts."""
        if self.stop_check_id:
            self.after_cancel(self.stop_check_id)
            self.stop_check_id = None
        if self.force_stop_timeout_id:
            self.after_cancel(self.force_stop_timeout_id)
            self.force_stop_timeout_id = None

    def start(self):
        """Starts the bot in a separate thread with error handling."""
        try:
            self._pre_start_validation()
            log = self.config_manager.general_log
            log.info(f"User clicked START for {self.strategy_name}")
            self.stopping = False
            self.main_app.status_var.set(f"{self.strategy_name.capitalize()} bot is running...")

            startup_tasks = self.config_manager.strategy_instance.get_startup_tasks()
            self.send_process = threading.Thread(
                target=self._thread_wrapper,
                args=(run_async_main, self.config_manager, None, startup_tasks),
                daemon=True,
                name=f"BotThread-{self.strategy_name}"
            )
            log.info(f"{self.strategy_name.capitalize()} bot starting.")
            self.send_process.start()
            self.started = True
            self.cleaned = False  # Mark that we are starting; not cleaned yet
            self.update_button_states()
            self.start_refresh() # Call the new start_refresh
            self.main_app.notify_strategy_started(self.strategy_name)

        except Exception as e:
            error_msg = f"Error starting {self.strategy_name} bot: {e}"
            self.main_app.status_var.set(error_msg)
            # Use centralized error handling if available
            if self.config_manager:
                self.config_manager.error_handler.handle(
                    OperationalError(error_msg),
                    context={"strategy": self.strategy_name},
                    exc_info=True
                )
            else:
                logger.error(error_msg, exc_info=True)
            self.stop(reload_config=False)

    def stop(self, reload_config: bool = True):
        """
        Signals the bot to stop. This method is non-blocking for the GUI.
        """
        if not self.config_manager or not self.started or self.stopping:
            if self.config_manager:
                self.config_manager.general_log.debug(
                    f"GUI: Ignoring STOP click for {self.strategy_name} (started={self.started}, stopping={self.stopping}).")
            return

        self.stopping = True
        self.update_button_states()  # Disable buttons immediately
        self.main_app.status_var.set(f"Stopping {self.strategy_name} bot...")
        self.config_manager.general_log.info(f"Attempting to stop {self.strategy_name} bot...")


        try:                                                                                                                                                                                
            # 1. Signal controller shutdown to prevent new operations                                                                                                                       
            self._signal_controller_shutdown()                                                                                                                                              
                                                                                                                                                                                            
            # 2. Run a blocking operation to wait for pending RPCs                                                                                                                          
            self._wait_for_pending_rpc()                                                                                                                                                    
                                                                                                                                                                                            
            # 3. Proceed with cancellation AFTER operations complete                                                                                                                        
            self.cancel_own_orders()                                                                                                                                                        
                                                                                                                                                                                            
            # 4. Continue with normal shutdown sequence                                                                                                                                     
            self._start_stop_monitoring(reload_config)                                                                                                                                      
                                                                                                                                                                                            
        except Exception as e:                                                                                                                                                              
            self.config_manager.general_log.error(f"Error during shutdown: {e}")
                                                                                                                                                                                            
    def _wait_for_pending_rpc(self, timeout=20):                                                                                                                                            
        """Blocks until all pending RPC operations complete"""                                                                                                                              
        start_time = time.time()                                                                                                                                                            
        logger.info("Syncing with strategy thread...")                                                                                                             
                                                                                                                                                                                            
        while (time.time() - start_time) < timeout:                                                                                                                                         
            # Check if any RPC calls are still processing                                                                                                                                   
            if not self._has_pending_operations():                                                                                                                                          
                logger.debug("All strategy operations completed")                                                                                                  
                return                                                                                                                                                                      
                                                                                                                                                                                            
            # Log progress periodically                                                                                                                                                     
            elapsed = time.time() - start_time                                                                                                                                              
            if int(elapsed) % 5 == 0:  # Update every 5 seconds                                                                                                                             
                logger.info(                                                                                                                                       
                    f"Waiting for operations to finish ({int(elapsed)}s/{timeout}s)..."                                                                                                     
                )                                                                                                                                                                           
                                                                                                                                                                                            
            time.sleep(0.1)                                                                                                                                                                 
                                                                                                                                                                                            
        logger.warning(                                                                                                                                            
            f"Timeout waiting for RPC operations after {timeout} seconds"                                                                                                                   
        )                                                                                                                                                                                   
                                                                                                                                                                                            
    def _has_pending_operations(self):                                                                                                                                                      
        """Check if there are active RPC calls"""                                                                                                                                           
        if not hasattr(self.config_manager, 'xbridge_manager'):                                                                                                                             
            return False                                                                                                                                                                    
                                                                                                                                                                                            
        # Access the RPC tracking mechanism from XBridgeManager                                                                                                                             
        if hasattr(self.config_manager.xbridge_manager, 'active_rpc_counter'):                                                                                                              
            return self.config_manager.xbridge_manager.active_rpc_counter > 0                                                                                                               
                                                                                                                                                                                            
        # Fallback for managers without counters                                                                                                                                            
        return False                                                                                                                                                               


    def _finalize_stop(self, reload_config: bool = True):
        """Cleans up the state after the bot thread has stopped."""
        self._cancel_stop_monitoring() # Ensure monitoring is stopped

        if self.config_manager:
            self.config_manager.general_log.debug(
                f"GUI: Finalizing stop for {self.strategy_name}. Reload config: {reload_config}")
        
        if self.send_process and self.send_process.is_alive():
            self.config_manager.general_log.warning("Bot thread did not terminate gracefully.")
            self.main_app.status_var.set("Bot stopped (forcefully).")
        else:
            self.main_app.status_var.set("Bot stopped.")
            self.config_manager.general_log.info("Bot stopped successfully.")


        self.send_process = None
        self.started = False
        self.stopping = False
        self.cleaned = True  # Mark that cleanup is complete
        self.update_button_states()

        # Purge and recreate orders display
        self.purge_and_recreate_widgets()
        # No longer calling refresh_gui directly here, as AsyncUpdater handles it

        if reload_config:
            self.reload_configuration(loadxbridgeconf=False)
        
        self.main_app.notify_strategy_stopped(self.strategy_name)

    def cancel_all(self):
        """Cancels all open orders on the exchange with error handling."""
        if not self.config_manager:
            return

        log = self.config_manager.general_log
        log.debug("GUI: cancel_all called - START")
        log.debug("GUI: cancel_all called. Creating worker thread.")
        self.main_app.status_var.set("Cancelling all open orders...")

        def worker():
            log.debug("GUI: cancel_all worker thread started.")
            try:
                # This now runs in a dedicated thread, so asyncio.run is safe.
                log.debug("GUI: cancel_all - calling cancelallorders()")
                asyncio.run(self.config_manager.xbridge_manager.cancelallorders())
                log.debug("GUI: cancel_all - cancelallorders() returned")
                # Schedule GUI update on main thread
                self.main_app.root.after(0, lambda: self.main_app.status_var.set("Cancelled all open orders."))
                log.info("cancel_all: All orders cancelled successfully.")
            except Exception as e:
                error_msg = f"Error cancelling orders: {e}"
                # Schedule GUI update on main thread
                self.main_app.root.after(0, lambda: self.main_app.status_var.set(error_msg))
                # Use centralized error handling
                self.config_manager.error_handler.handle(
                    OperationalError(error_msg),
                    context={"stage": "cancel_all"},
                    exc_info=True
                )
            log.debug("GUI: cancel_all worker thread finished.")
            # Restart the orders updater to refresh the orders panel
            if self.orders_updater:
                self.orders_updater.start()

        self.cancel_all_thread = threading.Thread(target=worker, daemon=True)
        self.cancel_all_thread.start()
        log.debug("GUI: cancel_all called - END")
    def cancel_own_orders(self):
        """Cancel only orders belonging to this strategy"""
        if not self.config_manager or not self.config_manager.strategy_instance:
            return
            
        try:
            if hasattr(self.config_manager.strategy_instance, 'cancel_own_orders'):
                # Create a new thread to run the cancellation
                thread = threading.Thread(
                    target=self._run_cancel_own_orders,
                    daemon=True
                )
                thread.start()
        except Exception as e:
            logger.error(f"Error canceling strategy orders: {e}")

    def _run_cancel_own_orders(self):
        """Runs strategy-specific order cancellation in background"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.config_manager.strategy_instance.cancel_own_orders())
        except Exception as e:
            logger.error(f"Cancel own orders error: {e}", exc_info=True)


    def start_refresh(self):
        """Starts the periodic GUI refresh loop and orders updater."""
        if self.orders_updater:
            self.orders_updater.start()

    
    @staticmethod
    def _get_flag(status: str) -> str:
        """Returns a flag ('V' or 'X') based on the order status."""
        return 'V' if status in {
            'open', 'new', 'created', 'accepting', 'hold', 'initialized', 'committed', 'finished'
        } else 'X'

    def _fetch_orders_data(self) -> List[Dict[str, Any]]:
        """Fetches order data for the AsyncUpdater."""
        orders = []
        if not self.config_manager or not hasattr(self.config_manager, 'pairs'):
            logger.debug("GUI: _fetch_orders_data - config_manager or pairs not initialized")
            return orders

        with self.config_manager.resource_lock:
            # logger.debug("GUI: _fetch_orders_data - acquired config_manager.resource_lock")
            for pair_obj in self.config_manager.pairs.values():
                name = pair_obj.name
                symbol = pair_obj.symbol
                # pair = pair_obj.cfg
                status = 'None'
                current_order_side = 'None'
                maker_size = 'None'
                maker = 'None'
                taker_size = 'None'
                taker = 'None'
                dex_price = 'None'
                order_id = 'None'

                if self.started and pair_obj.dex.order and 'status' in pair_obj.dex.order:
                    status = pair_obj.dex.order.get('status', 'None')
                    current_order_side = pair_obj.dex.current_order.get('side', 'None') if pair_obj.dex.current_order else 'None'
                    maker_size = pair_obj.dex.current_order.get('maker_size', 'None') if pair_obj.dex.current_order else 'None'
                    maker = pair_obj.dex.current_order.get('maker', 'None') if pair_obj.dex.current_order else 'None'
                    taker_size = pair_obj.dex.current_order.get('taker_size', 'None') if pair_obj.dex.current_order else 'None'
                    taker = pair_obj.dex.current_order.get('taker', 'None') if pair_obj.dex.current_order else 'None'
                    dex_price = pair_obj.dex.current_order.get('dex_price', 'None') if pair_obj.dex.current_order else 'None'
                    order_id = pair_obj.dex.order.get('id', 'None') if pair_obj.dex.current_order else 'None'
                elif pair_obj.dex.disabled:
                    status = 'Disabled'
                
                variation_display = 'None'
                if self.started and pair_obj.dex.order and 'status' in pair_obj.dex.order:
                    variation_display = str(pair_obj.dex.variation)

                orders.append({
                    "name": name,
                    "symbol": symbol,
                    "status": status,
                    "side": current_order_side,
                    "flag": self._get_flag(status),
                    "variation": variation_display,
                    "maker_size": maker_size,
                    "maker": maker,
                    "taker_size": taker_size,
                    "taker": taker,
                    "dex_price": dex_price,
                    "order_id": order_id
                })
        # logger.debug(f"GUI: _fetch_orders_data - orders: {orders}")
        return orders


    def on_closing(self):
        """Handles the application closing event."""
        if self.config_manager:
            self.config_manager.general_log.info(f"Closing {self.strategy_name} strategy...")
        # self.stop_refresh() # Ensure orders updater is signaled to stop
        self.stop(reload_config=False)

    def reload_configuration(self, loadxbridgeconf: bool = True):
        """Reloads the bot's configuration and refreshes the GUI display."""
        if not self.config_manager:
            return
        self.config_manager.general_log.debug(f"GUI: Reloading configuration for {self.strategy_name}.")
        self.initialize_config(loadxbridgeconf=loadxbridgeconf)
        self.purge_and_recreate_widgets()

    def purge_and_recreate_widgets(self):
        """Purges and recreates widgets. To be overridden."""
        pass

    def create_standard_buttons(self):
        """Creates the standard START, STOP, CANCEL ALL, CONFIGURE buttons."""
        button_frame = ttk.Frame(self)
        button_frame.grid(column=0, row=0, padx=5, pady=5, sticky='ew')
        btn_width = 12
        self.btn_start = ttk.Button(button_frame, text="START", command=self.start, width=btn_width)
        self.btn_start.grid(column=0, row=0, padx=5, pady=5)
        self.btn_stop = ttk.Button(button_frame, text="STOP", command=lambda: self.stop(),
                                   width=btn_width)
        self.btn_stop.grid(column=1, row=0, padx=5, pady=5)
        self.btn_cancel_all = ttk.Button(button_frame, text="CANCEL ALL", command=self.cancel_all, width=btn_width)
        self.btn_cancel_all.grid(column=2, row=0, padx=5, pady=5)
        self.btn_configure = ttk.Button(button_frame, text="CONFIGURE", command=self.open_configure_window,
                                        width=btn_width)
        self.btn_configure.grid(column=3, row=0, padx=5, pady=5)
        self.update_button_states()

    def update_button_states(self):
        """Updates button states based on bot status."""
        start_enabled = not self.started and not self.stopping
        stop_enabled = self.started and not self.stopping
        configure_enabled = not self.started and not self.stopping

        if self.btn_start: self.btn_start.config(state="normal" if start_enabled else "disabled")
        if self.btn_stop: self.btn_stop.config(state="normal" if stop_enabled else "disabled")
        if self.btn_configure: self.btn_configure.config(state="normal" if configure_enabled else "disabled")
        # cancel_all can always be active, or you can add logic for it too

    def cleanup(self):
        """Perform final cleanup including canceling periodic tasks and detaching events."""
        # Unbind all event listeners
        self.unbind_all("<Motion>")  # Example for all motion events
        self.unbind_all("<Button>")  # Example for all button events

    def open_configure_window(self):
        """Opens the configuration window for this strategy. To be overridden."""
        pass


class StandardStrategyFrame(BaseStrategyFrame, metaclass=abc.ABCMeta):
    """Base class for standard strategy frames with Orders and Balances views."""

    def __init__(self, parent, main_app: "MainApplication", strategy_name: str, master_config_manager: ConfigManager):
        self.orders_panel: OrdersPanel
        self.gui_config: "BaseConfigWindow"
        super().__init__(parent, main_app, strategy_name, master_config_manager)
        # Configure the grid for expansion. This allows the child frames
        # (orders, balances) to grow with the window.
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)  # Orders frame
        # Note: Balances panel is now shared and managed by MainApplication

    @abc.abstractmethod
    def _create_config_gui(self) -> "BaseConfigWindow":
        """Creates the strategy-specific configuration GUI window."""
        pass

    def create_widgets(self):
        """Creates the common widgets for a standard strategy frame."""
        # Create and preserve orders frame
        self.orders_frame = ttk.LabelFrame(self, text="Orders")
        self.orders_frame.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")
        self.orders_frame.grid_rowconfigure(0, weight=1)
        self.orders_frame.grid_columnconfigure(0, weight=1)

        # Create orders panel inside preserved frame
        self.orders_panel = OrdersPanel(self.orders_frame)
        self.orders_panel.grid(row=0, column=0, padx=0, pady=0, sticky="nsew")

        # Initialize AsyncUpdater for orders
        self.orders_updater = AsyncUpdater(
            tk_widget=self,
            update_target_method=self.orders_panel.update_data,
            fetch_data_callable=self._fetch_orders_data,
            update_interval_ms=1500,
            name=f"{self.strategy_name}OrdersUpdater"
        )

        self.gui_config = self._create_config_gui()
        self.create_standard_buttons()

    def open_configure_window(self):
        """Opens the configuration window for this strategy."""
        self.gui_config.open()

    def purge_and_recreate_widgets(self):
        """Purges and recreates the Orders treeview while preserving the frame."""
        # Only destroy/recreate the orders panel contents
        self.orders_panel.destroy()
        self.orders_panel = OrdersPanel(self.orders_frame)
        self.orders_panel.grid(row=0, column=0, padx=0, pady=0, sticky="nsew")
        # Re-initialize the updater with the new panel
        self.orders_updater = AsyncUpdater(
            tk_widget=self,
            update_target_method=self.orders_panel.update_data,
            fetch_data_callable=self._fetch_orders_data,
            update_interval_ms=1500,
            name=f"{self.strategy_name}OrdersUpdater"
        )
        # Immediately update the orders panel with current data after recreation
        initial_orders_data = self._fetch_orders_data()
        self.orders_panel.update_data(initial_orders_data)

    def cleanup(self):
        """Unbind events to prevent errors during teardown."""
