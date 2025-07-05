# gui/frames.py
import abc
import asyncio
import logging
import threading
import time
import tkinter as tk
from tkinter import ttk

# Get module-specific logger
logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING

from ruamel.yaml import YAML

from definitions.config_manager import ConfigManager
from definitions.starter import run_async_main
from .components.dialogs import AddPairDialog, PairConfigDialog, AddSellerDialog, SellerConfigDialog
from .components.data_panels import OrdersPanel, BalancesPanel

if TYPE_CHECKING:
    from gui.gui import GUI_Main


class BaseStrategyFrame(ttk.Frame):
    """Base class for strategy-specific frames in the GUI."""

    def __init__(self, parent, main_app: "GUI_Main", strategy_name: str, master_config_manager: ConfigManager):
        super().__init__(parent)
        self.main_app = main_app
        self.master_config_manager = master_config_manager
        self.strategy_name = strategy_name
        self.config_manager: ConfigManager | None = None
        self.send_process: threading.Thread | None = None
        self.started = False
        self.stopping = False
        # Button attributes that will be created by create_standard_buttons
        self.btn_start: ttk.Button | None = None
        self.btn_stop: ttk.Button | None = None
        self.btn_cancel_all: ttk.Button | None = None
        self.btn_configure: ttk.Button | None = None
        self.refresh_id = None

        self.initialize_config()
        self.create_widgets()

    def initialize_config(self, loadxbridgeconf: bool = True):
        """Initializes the configuration manager for the specific strategy."""
        try:
            self.config_manager = ConfigManager(strategy=self.strategy_name, master_manager=self.master_config_manager)
            self.config_manager.initialize(loadxbridgeconf=loadxbridgeconf)
        except Exception as e:
            self.main_app.status_var.set(f"Error initializing {self.strategy_name}: {e}")
            if self.config_manager:
                self.config_manager.general_log.error(f"Error initializing {self.strategy_name}: {e}", exc_info=True)

    def create_widgets(self):
        """Placeholder for creating strategy-specific widgets. To be overridden."""
        pass

    def start(self):
        """Starts the bot in a separate thread."""
        if not self.config_manager:
            logger.error("Cannot start: config_manager is not initialized.")
            return

        logger.info(f"User clicked START for {self.strategy_name}")
        logger.debug(
            f"Initializing bot thread for {self.strategy_name} | Config: {self.config_manager.__dict__.keys()}")

        log = self.config_manager.general_log
        log.debug("Validating configuration parameters")

        self.stopping = False  # Reset stopping flag on start
        self.main_app.status_var.set(f"{self.strategy_name.capitalize()} bot is running...")
        log.debug("GUI: Fetching startup tasks.")
        startup_tasks = self.config_manager.strategy_instance.get_startup_tasks()
        log.debug("GUI: Creating bot thread.")
        self.send_process = threading.Thread(target=run_async_main,
                                             args=(self.config_manager, None, startup_tasks),
                                             daemon=True)
        try:
            self.config_manager.general_log.info(f"{self.strategy_name.capitalize()} bot starting.")
            self.send_process.start()
            self.started = True
            self.update_button_states()
            self.after(0, self.start_refresh)

        except Exception as e:
            self.main_app.status_var.set(f"Error starting {self.strategy_name} bot: {e}")
            log.error(f"Error starting bot thread: {e}", exc_info=True)
            logger.critical(f"Failed to start {self.strategy_name} bot", exc_info=True)
            self.stop(blocking=True, reload_config=False)

    def stop(self, blocking: bool = False, reload_config: bool = True):
        """
        Signals the bot to stop. If blocking is True, waits for the thread to finish.
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

        if self.config_manager.controller and self.config_manager.controller.loop:
            # Safely set the asyncio event from a different thread
            self.config_manager.controller.loop.call_soon_threadsafe(
                self.config_manager.controller.shutdown_event.set
            )

        if blocking:
            # Used for application shutdown where we need to wait
            if self.send_process:
                # First try orderly shutdown
                if self.config_manager.controller:
                    self.config_manager.controller.shutdown_event.set()

                # Wait with timeout and force exit if needed
                self.send_process.join(2)  # Reduced from 10 to 2 seconds

                if self.send_process.is_alive():
                    self.config_manager.general_log.warning("Force shutdown of stuck thread")
                    # Force stop the event loop
                    if self.config_manager.controller and self.config_manager.controller.loop:
                        self.config_manager.controller.loop.stop()

            self._finalize_stop(reload_config)
            # HTTP session is managed by async context, no need for explicit close

    def _finalize_stop(self, reload_config: bool = True):
        """Cleans up the state after the bot thread has stopped."""
        if self.config_manager:
            self.config_manager.general_log.debug(
                f"GUI: Finalizing stop for {self.strategy_name}. Reload config: {reload_config}")
        if self.send_process:
            if self.send_process.is_alive():
                self.config_manager.general_log.warning("Bot thread did not terminate gracefully.")
                self.main_app.status_var.set("Bot stopped (forcefully).")
            else:
                self.main_app.status_var.set("Bot stopped.")
                self.config_manager.general_log.info("Bot stopped successfully.")

        self.send_process = None
        self.started = False
        self.stopping = False
        self.update_button_states()

        # Purge and recreate orders display                                                                                                                                                                            
        self.purge_and_recreate_widgets()
        self.refresh_gui()  # Force immediate refresh                                                                                                                                                                  

        if reload_config:
            self.reload_configuration(loadxbridgeconf=False)

    def cancel_all(self):
        """Cancels all open orders on the exchange."""
        if not self.config_manager:
            return

        log = self.config_manager.general_log
        log.debug("GUI: cancel_all called. Creating worker thread.")
        self.main_app.status_var.set("Cancelling all open orders...")

        def worker():
            log.debug("GUI: cancel_all worker thread started.")
            try:
                # This now runs in a dedicated thread, so asyncio.run is safe.
                asyncio.run(self.config_manager.xbridge_manager.cancelallorders())
                # We need to schedule the GUI update back on the main thread.
                self.main_app.root.after(0, lambda: self.main_app.status_var.set("Cancelled all open orders."))
                log.info("cancel_all: All orders cancelled successfully.")
            except Exception as e:
                # Schedule GUI update on main thread
                self.main_app.root.after(0, lambda e=e: self.main_app.status_var.set(f"Error cancelling orders: {e}"))
                log.error(f"Error during cancel_all worker: {e}", exc_info=True)
            log.debug("GUI: cancel_all worker thread finished.")

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def start_refresh(self):
        """Starts the periodic GUI refresh loop."""
        self.refresh_gui()

    def stop_refresh(self):
        """Stops the periodic GUI refresh loop."""
        if self.refresh_id:
            self.after_cancel(self.refresh_id)
            self.refresh_id = None

    def refresh_gui(self):
        """Refreshes the GUI display periodically. To be overridden."""
        log = self.config_manager.general_log

        if self.winfo_exists():
            self._update_orders_display()

        # Check if a non-blocking stop has completed
        if self.stopping and self.send_process and not self.send_process.is_alive():
            log.info(f"Detected stopped thread for {self.strategy_name}. Finalizing...")
            self._finalize_stop()
            return  # Stop the refresh loop for this frame

        # Check for a crash (thread died while it was supposed to be running)
        if self.started and not self.stopping and self.send_process and not self.send_process.is_alive():
            log.error(f"{self.strategy_name} bot thread died unexpectedly (crashed)!")
            self.main_app.status_var.set(f"{self.strategy_name} bot CRASHED!")
            # The thread is already dead, so just finalize the stop state.
            self._finalize_stop(reload_config=False)
            log.info("GUI: Issuing cancel_all after crash detection.")
            self.cancel_all()
            return  # Stop the refresh loop for this frame

        # If we get here, the bot is running normally, so schedule the next check.
        self.refresh_id = self.after(1500, self.refresh_gui)

    @staticmethod
    def _get_flag(status: str) -> str:
        """Returns a flag ('V' or 'X') based on the order status."""
        return 'V' if status in {
            'open', 'new', 'created', 'accepting', 'hold', 'initialized', 'committed', 'finished'
        } else 'X'

    def _update_orders_display(self):
        """Collects current order data and updates display"""
        if not self.config_manager or not hasattr(self.config_manager, 'pairs'):
            return
        orders = []
        for pair_obj in self.config_manager.pairs.values():
            pair = pair_obj.cfg
            status = 'None'
            if self.started and pair_obj.dex.order and 'status' in pair_obj.dex.order:
                status = pair_obj.dex.order.get('status', 'None')
                current_order_side = pair_obj.dex.current_order.get('side',
                                                                    'None') if pair_obj.dex.current_order else 'None'
            elif pair_obj.dex.disabled:
                status = 'Disabled'
                current_order_side = 'None'
            else:
                current_order_side = 'None'

            variation_display = 'None'
            if self.started and pair_obj.dex.order and 'status' in pair_obj.dex.order:
                variation_display = str(pair_obj.dex.variation)

            orders.append({
                "pair": pair.get("name", "Unnamed"),
                "status": status,
                "side": current_order_side,
                "flag": self._get_flag(status),
                "variation": variation_display
            })
        self.orders_panel.update_data(orders)

    def on_closing(self):
        """Handles the application closing event."""
        if self.config_manager:
            self.config_manager.general_log.info(f"Closing {self.strategy_name} strategy...")
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
        self.btn_stop = ttk.Button(button_frame, text="STOP", command=lambda: self.stop(blocking=False),
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
        # Cancel any pending refresh loop
        self.stop_refresh()
        # Unbind all event listeners
        self.unbind_all("<Motion>")  # Example for all motion events
        self.unbind_all("<Button>")  # Example for all button events

    def open_configure_window(self):
        """Opens the configuration window for this strategy. To be overridden."""
        pass


class StandardStrategyFrame(BaseStrategyFrame, metaclass=abc.ABCMeta):
    """Base class for standard strategy frames with Orders and Balances views."""

    def __init__(self, parent, main_app: "GUI_Main", strategy_name: str, master_config_manager: ConfigManager):
        self.orders_panel: OrdersPanel
        self.balances_panel: BalancesPanel
        self.gui_config: "BaseConfigWindow"
        super().__init__(parent, main_app, strategy_name, master_config_manager)
        # Configure the grid for expansion. This allows the child frames
        # (orders, balances) to grow with the window.
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)  # Orders frame
        self.grid_rowconfigure(2, weight=1)  # Balances frame

    @abc.abstractmethod
    def _create_config_gui(self) -> "BaseConfigWindow":
        """Creates the strategy-specific configuration GUI window."""
        pass

    def create_widgets(self):
        """Creates the common widgets for a standard strategy frame."""
        # Create orders frame
        orders_frame = ttk.LabelFrame(self, text="Orders")
        orders_frame.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")
        orders_frame.grid_rowconfigure(0, weight=1)
        orders_frame.grid_columnconfigure(0, weight=1)

        # Create orders panel
        self.orders_panel = OrdersPanel(orders_frame)
        self.orders_panel.grid(row=0, column=0, padx=0, pady=0, sticky="nsew")

        self.gui_config = self._create_config_gui()
        self.create_standard_buttons()

    def open_configure_window(self):
        """Opens the configuration window for this strategy."""
        self.gui_config.open()

    def purge_and_recreate_widgets(self):
        """Purges and recreates the Orders treeview."""
        self.orders_panel.destroy()
        self.orders_panel = OrdersPanel(self)
        self.orders_panel.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")

    def cleanup(self):
        """Unbind events to prevent errors during teardown."""
        super().cleanup()


class PingPongFrame(StandardStrategyFrame):
    def __init__(self, parent, main_app: "GUI_Main", master_config_manager: ConfigManager):
        super().__init__(parent, main_app, "pingpong", master_config_manager)

    def _create_config_gui(self) -> "BaseConfigWindow":
        return GUI_Config_PingPong(self)


class BaseConfigWindow:
    """Base class for strategy configuration Toplevel windows."""

    def __init__(self, parent: "BaseStrategyFrame"):
        self.parent = parent
        strategy_title = parent.strategy_name.replace('_', ' ').title()
        self.title_text = f"Configure {strategy_title} Bot"
        self.config_file_path = f'./config/config_{parent.strategy_name}.yaml'
        self.config_window: tk.Toplevel | None = None
        self.status_var = tk.StringVar()
        self.status_label: ttk.Label | None = None
        self.active_dialog: tk.Toplevel | None = None

    def open(self) -> None:
        if self.config_window and self.config_window.winfo_exists():
            self.config_window.tkraise()
            return

        self.parent.btn_start.config(state="disabled")
        if hasattr(self.parent, 'btn_configure'):
            self.parent.btn_configure.config(state="disabled")

        self.config_window = tk.Toplevel(self.parent)
        self.config_window.title(self.title_text)
        self.config_window.protocol("WM_DELETE_WINDOW", self.on_close)

        main_frame = ttk.Frame(self.config_window)
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)
        main_frame.grid_rowconfigure(0, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)

        self._create_widgets(main_frame)
        self._create_control_buttons_area(main_frame)
        self._create_save_button(main_frame)
        self._create_status_bar(main_frame)
        self._set_window_geometry()

    def _create_widgets(self, parent_frame: ttk.Frame):
        """Placeholder for subclass to create specific widgets."""
        raise NotImplementedError

    def _create_control_buttons_area(self, parent_frame: ttk.Frame):
        """Placeholder for subclass to create control buttons outside the main widget area."""
        pass

    def _create_save_button(self, parent_frame: ttk.Frame) -> None:
        save_button = ttk.Button(parent_frame, text="Save", command=self.save_config)
        save_button.grid(row=2, column=0, pady=10, sticky='ew')

    def _create_status_bar(self, parent_frame: ttk.Frame) -> None:
        status_frame = ttk.Frame(parent_frame)
        status_frame.grid(row=3, column=0, pady=5, sticky='ew')
        self.status_var.set("Ready")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        self.status_label.pack(fill='x')

    def _set_window_geometry(self):
        """Placeholder for subclass to set window size."""
        pass

    def on_close(self) -> None:
        if hasattr(self.parent, 'btn_start'):
            self.parent.btn_start.config(state="active")
        if hasattr(self.parent, 'btn_configure'):
            self.parent.btn_configure.config(state="active")
        if self.config_window:
            self.config_window.destroy()
        self.config_window = None

    def _open_single_dialog(self, dialog_class, *dialog_args) -> tk.Toplevel:
        if self.active_dialog and self.active_dialog.winfo_exists():
            self.active_dialog.destroy()

        dialog = dialog_class(self.config_window, *dialog_args)
        self.active_dialog = dialog
        self.config_window.wait_window(dialog)

        if self.active_dialog is dialog:
            self.active_dialog = None
        return dialog

    def save_config(self) -> None:
        new_config = self._get_config_data_to_save()
        if new_config is None:
            return  # Save operation was cancelled or failed validation

        yaml_writer = YAML()
        yaml_writer.default_flow_style = False
        yaml_writer.indent(mapping=2, sequence=4, offset=2)

        try:
            with open(self.config_file_path, 'w') as file:
                yaml_writer.dump(new_config, file)

            # Reload the master configuration manager to pick up changes from the file.
            if self.parent.master_config_manager:
                self.parent.master_config_manager.load_configs()

            self.update_status("Configuration saved and reloaded successfully.", 'lightgreen')
            # Now, reload the strategy frame's specific configuration from the master.
            self.parent.reload_configuration(loadxbridgeconf=True)
        except Exception as e:
            self.update_status(f"Failed to save configuration: {e}", 'lightcoral')
            if self.parent.config_manager:
                self.parent.config_manager.general_log.error(f"Failed to save config: {e}")

    def _get_config_data_to_save(self) -> dict | None:
        """Placeholder for subclass to return the config dictionary to be saved."""
        raise NotImplementedError

    def update_status(self, message: str, color: str = 'black') -> None:
        if self.status_label:
            self.status_var.set(message)
            self.status_label.config(foreground=color)


class GUI_Config_PingPong(BaseConfigWindow):
    """
    Manages the configuration window for the PingPong bot settings.
    """

    def __init__(self, parent: "BaseStrategyFrame") -> None:
        super().__init__(parent)
        self.debug_level_entry: ttk.Entry | None = None
        self.ttk_theme_entry: ttk.Entry | None = None
        self.pairs_treeview: ttk.Treeview | None = None

    def _on_key_press_scroll(self, event: tk.Event, canvas: tk.Canvas, direction: int) -> None:
        if self.config_window and self.pairs_treeview and self.config_window.focus_get() == self.pairs_treeview:
            return
        canvas.yview_scroll(direction, "units")

    def _setup_scroll_bindings(self, canvas: tk.Canvas) -> None:
        if self.config_window:
            self.config_window.bind("<Up>", lambda event: self._on_key_press_scroll(event, canvas, -1))
            self.config_window.bind("<Down>", lambda event: self._on_key_press_scroll(event, canvas, 1))
            self.config_window.bind("<Prior>", lambda event: canvas.yview_scroll(-10, "units"))
            self.config_window.bind("<Next>", lambda event: canvas.yview_scroll(10, "units"))
            self.config_window.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

    def _create_widgets(self, parent_frame: ttk.Frame):
        canvas = tk.Canvas(parent_frame)
        canvas.grid(row=0, column=0, sticky='nsew')
        parent_frame.grid_rowconfigure(0, weight=1)
        parent_frame.grid_columnconfigure(0, weight=1)

        content_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=content_frame, anchor='nw')

        self._setup_scroll_bindings(canvas)
        content_frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        content_frame.grid_columnconfigure(0, weight=1)
        content_frame.grid_rowconfigure(1, weight=1)

        self._create_general_settings_widgets(content_frame)
        self._create_pairs_treeview_widgets(content_frame)

    def _create_general_settings_widgets(self, parent_frame: ttk.Frame) -> None:
        general_frame = ttk.LabelFrame(parent_frame, text="General Settings")
        general_frame.grid(row=0, column=0, padx=5, pady=5, sticky='ew')
        general_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(general_frame, text="Debug Level:").grid(row=0, column=0, padx=5, pady=5, sticky='w')
        self.debug_level_entry = ttk.Entry(general_frame)
        self.debug_level_entry.grid(row=0, column=1, padx=5, pady=5, sticky='ew')
        if self.parent.config_manager and self.parent.config_manager.config_pingppong:
            self.debug_level_entry.insert(0, str(self.parent.config_manager.config_pingppong.debug_level))

        ttk.Label(general_frame, text="TTK Theme:").grid(row=1, column=0, padx=5, pady=5, sticky='w')
        self.ttk_theme_entry = ttk.Entry(general_frame)
        self.ttk_theme_entry.grid(row=1, column=1, padx=5, pady=5, sticky='ew')
        if self.parent.config_manager and self.parent.config_manager.config_pingppong:
            self.ttk_theme_entry.insert(0, self.parent.config_manager.config_pingppong.ttk_theme)

    def _create_pairs_treeview_widgets(self, parent_frame: ttk.Frame) -> None:
        tree_frame = ttk.LabelFrame(parent_frame, text="Pair Configurations")
        tree_frame.grid(row=1, column=0, padx=5, pady=5, sticky='nsew')
        parent_frame.grid_rowconfigure(1, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)

        columns = ('name', 'enabled', 'pair', 'price_variation_tolerance', 'sell_price_offset', 'usd_amount', 'spread')
        self.pairs_treeview = ttk.Treeview(tree_frame, columns=columns, show='headings', height=8)

        headings = {'name': 'Name', 'enabled': 'Enabled', 'pair': 'Pair', 'price_variation_tolerance': 'Var. Tol.',
                    'sell_price_offset': 'Sell Offset', 'usd_amount': 'USD Amt', 'spread': 'Spread'}
        for col, text in headings.items():
            self.pairs_treeview.heading(col, text=text)

        col_configs = {'name': (150, 'w'), 'enabled': (75, 'center'), 'pair': (150, 'w'),
                       'price_variation_tolerance': (120, 'e'), 'sell_price_offset': (120, 'e'),
                       'usd_amount': (120, 'e'), 'spread': (120, 'e')}
        for col, (width, anchor) in col_configs.items():
            self.pairs_treeview.column(col, width=width, anchor=anchor)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.pairs_treeview.yview)
        self.pairs_treeview.configure(yscrollcommand=scrollbar.set)
        self.pairs_treeview.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.pairs_treeview.bind("<Double-1>", lambda event: self.edit_pair_config())
        self._populate_pairs_treeview()

    def _populate_pairs_treeview(self) -> None:
        if self.pairs_treeview and self.parent.config_manager and self.parent.config_manager.config_pingppong:
            for cfg in self.parent.config_manager.config_pingppong.pair_configs:
                self.pairs_treeview.insert('', 'end', values=(
                    cfg.get('name', ''),
                    'Yes' if cfg.get('enabled', True) else 'No',
                    cfg['pair'],
                    cfg.get('price_variation_tolerance', 0.02),
                    cfg.get('sell_price_offset', 0.05),
                    cfg.get('usd_amount', 0.5),
                    cfg.get('spread', 0.1)
                ))

    def _create_control_buttons_area(self, parent_frame: ttk.Frame) -> None:
        btn_frame = ttk.Frame(parent_frame)
        btn_frame.grid(row=1, column=0, padx=5, pady=5, sticky='w')
        ttk.Button(btn_frame, text="Add Pair", command=self.add_pair_config).pack(side='left', padx=2)
        ttk.Button(btn_frame, text="Remove Pair", command=self.remove_pair_config).pack(side='left', padx=2)
        ttk.Button(btn_frame, text="Edit Config", command=self.edit_pair_config).pack(side='left', padx=2)

    def add_pair_config(self) -> None:
        if not self.config_window:
            return
        dialog = self._open_single_dialog(AddPairDialog, self)

        if not self.config_window or not self.config_window.winfo_exists():
            return

        if dialog.result and self.pairs_treeview:
            self.pairs_treeview.insert('', 'end', values=dialog.result)
            self.update_status(f"Pair {dialog.result[2]} added successfully.", 'lightgreen')

    def remove_pair_config(self) -> None:
        if not self.pairs_treeview:
            return
        selected = self.pairs_treeview.selection()
        if selected:
            self.pairs_treeview.delete(selected)

    def edit_pair_config(self) -> None:
        if not self.pairs_treeview:
            return
        selected = self.pairs_treeview.selection()
        if selected:
            values = self.pairs_treeview.item(selected, 'values')
            dialog = self._open_single_dialog(PairConfigDialog, values, self)

            if not self.config_window or not self.config_window.winfo_exists():
                return

            if dialog.result:
                self.pairs_treeview.item(selected, values=dialog.result)
                self.update_status(f"Pair {dialog.result[2]} updated successfully.", 'lightgreen')
            else:
                self.update_status("Edit cancelled.", 'lightgray')

    def _set_window_geometry(self) -> None:
        if self.config_window:
            x, y = 900, 450
            self.config_window.minsize(x, y)
            self.config_window.geometry(f"{x}x{y}")

    def _get_config_data_to_save(self) -> dict | None:
        pair_configs = []
        if self.pairs_treeview:
            for item_id in self.pairs_treeview.get_children():
                values = self.pairs_treeview.item(item_id, 'values')
                try:
                    pair_configs.append({
                        'name': values[0],
                        'enabled': values[1] == 'Yes',
                        'pair': values[2],
                        'price_variation_tolerance': float(values[3]),
                        'sell_price_offset': float(values[4]),
                        'usd_amount': float(values[5]),
                        'spread': float(values[6])
                    })
                except (ValueError, IndexError) as e:
                    self.update_status(f"Invalid numeric value in pair config: {e}", 'red')
                    if self.parent.config_manager:
                        self.parent.config_manager.general_log.error(f"Failed to parse pair config: {e}")
                    return None

        new_config = {
            'debug_level': int(self.debug_level_entry.get()) if self.debug_level_entry else 0,
            'ttk_theme': self.ttk_theme_entry.get() if self.ttk_theme_entry else 'flatly',
            'pair_configs': pair_configs
        }
        return new_config


class GUI_Config_BasicSeller(BaseConfigWindow):
    def __init__(self, parent: "BasicSellerFrame"):
        super().__init__(parent)
        self.sellers_treeview: ttk.Treeview | None = None

    def _create_widgets(self, parent_frame: ttk.Frame):
        content_frame = ttk.Frame(parent_frame)
        content_frame.grid(row=0, column=0, sticky='nsew')
        content_frame.grid_rowconfigure(0, weight=1)
        content_frame.grid_columnconfigure(0, weight=1)

        self._create_sellers_treeview(content_frame)

    def _create_sellers_treeview(self, parent_frame: ttk.Frame) -> None:
        tree_frame = ttk.LabelFrame(parent_frame, text="Seller Configurations")
        tree_frame.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        columns = ('name', 'enabled', 'pair', 'amount_to_sell', 'min_sell_price_usd', 'sell_price_offset')
        self.sellers_treeview = ttk.Treeview(tree_frame, columns=columns, show='headings', height=10)

        headings = {'name': 'Name', 'enabled': 'Enabled', 'pair': 'Pair', 'amount_to_sell': 'Amount',
                    'min_sell_price_usd': 'Min Price (USD)', 'sell_price_offset': 'Offset'}
        for col, text in headings.items():
            self.sellers_treeview.heading(col, text=text)

        col_configs = {'name': (150, 'w'), 'enabled': (75, 'center'), 'pair': (150, 'w'),
                       'amount_to_sell': (120, 'e'), 'min_sell_price_usd': (150, 'e'),
                       'sell_price_offset': (120, 'e')}
        for col, (width, anchor) in col_configs.items():
            self.sellers_treeview.column(col, width=width, anchor=anchor)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.sellers_treeview.yview)
        self.sellers_treeview.configure(yscrollcommand=scrollbar.set)
        self.sellers_treeview.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.sellers_treeview.bind("<Double-1>", lambda event: self.edit_seller_config())
        self._populate_sellers_treeview()

    def _populate_sellers_treeview(self):
        if self.sellers_treeview and self.parent.config_manager and self.parent.config_manager.config_basicseller:
            for cfg in self.parent.config_manager.config_basicseller.seller_configs:
                self.sellers_treeview.insert('', 'end', values=(
                    cfg.get('name', ''),
                    'Yes' if cfg.get('enabled', True) else 'No',
                    cfg.get('pair', 'N/A'),
                    cfg.get('amount_to_sell', 0.0),
                    cfg.get('min_sell_price_usd', 0.0),
                    cfg.get('sell_price_offset', 0.0)
                ))

    def _create_control_buttons_area(self, parent_frame: ttk.Frame):
        btn_frame = ttk.Frame(parent_frame)
        btn_frame.grid(row=1, column=0, sticky='w', padx=5, pady=5)
        ttk.Button(btn_frame, text="Add Seller", command=self.add_seller_config).pack(side='left', padx=2)
        ttk.Button(btn_frame, text="Remove Seller", command=self.remove_seller_config).pack(side='left', padx=2)
        ttk.Button(btn_frame, text="Edit Seller", command=self.edit_seller_config).pack(side='left', padx=2)

    def _set_window_geometry(self) -> None:
        if self.config_window:
            x, y = 800, 400
            self.config_window.minsize(x, y)
            self.config_window.geometry(f"{x}x{y}")

    def add_seller_config(self):
        dialog = self._open_single_dialog(AddSellerDialog, self)
        if dialog.result and self.sellers_treeview:
            self.sellers_treeview.insert('', 'end', values=dialog.result)

    def remove_seller_config(self):
        if self.sellers_treeview:
            selected = self.sellers_treeview.selection()
            if selected:
                self.sellers_treeview.delete(selected)

    def edit_seller_config(self):
        if self.sellers_treeview:
            selected = self.sellers_treeview.selection()
            if selected:
                values = self.sellers_treeview.item(selected, 'values')
                dialog = self._open_single_dialog(SellerConfigDialog, values, self)
                if dialog.result:
                    self.sellers_treeview.item(selected, values=dialog.result)

    def _get_config_data_to_save(self) -> dict | None:
        seller_configs = []
        if self.sellers_treeview:
            for item_id in self.sellers_treeview.get_children():
                values = self.sellers_treeview.item(item_id, 'values')
                try:
                    seller_configs.append({
                        'name': values[0],
                        'enabled': values[1] == 'Yes',
                        'pair': values[2],
                        'amount_to_sell': float(values[3]),
                        'min_sell_price_usd': float(values[4]),
                        'sell_price_offset': float(values[5])
                    })
                except (ValueError, IndexError) as e:
                    self.update_status(f"Invalid numeric value in seller config: {e}", 'red')
                    if self.parent.config_manager:
                        self.parent.config_manager.general_log.error(f"Failed to parse seller config: {e}")
                    return None
        return {'seller_configs': seller_configs}


class BasicSellerFrame(StandardStrategyFrame):
    def __init__(self, parent, main_app: "GUI_Main", master_config_manager: ConfigManager):
        super().__init__(parent, main_app, "basic_seller", master_config_manager)

    def _create_config_gui(self) -> "BaseConfigWindow":
        return GUI_Config_BasicSeller(self)


class ArbitrageFrame(StandardStrategyFrame):
    def __init__(self, parent, main_app: "GUI_Main", master_config_manager: ConfigManager):
        super().__init__(parent, main_app, "arbitrage", master_config_manager)

    def _create_config_gui(self) -> "BaseConfigWindow":
        # Temporary implementation - return mock config window
        class MockConfigWindow:
            def open(self): pass

        return MockConfigWindow()

    def _update_orders_display(self):
        """Override for arbitrage since it doesn't have DEX orders"""
        self.orders_panel.update_data([])


# --- New Logging Components ---

class LogFrame(ttk.Frame):
    """A frame for displaying application logs."""

    def __init__(self, parent):
        super().__init__(parent)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.log_entries = []  # Track (timestamp, line_start, line_end)
        self.prune_interval = 30 * 60 * 1000  # Check every 30 minutes
        self.after(self.prune_interval, self.prune_old_logs)

        self.log_text = tk.Text(self, wrap='word', state='disabled', height=10, background="#222", foreground="white")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.log_text.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')

        # Configure tags for different log levels
        self.log_text.tag_config("INFO", foreground="white")
        self.log_text.tag_config("DEBUG", foreground="gray")
        self.log_text.tag_config("WARNING", foreground="orange")
        self.log_text.tag_config("ERROR", foreground="red")
        self.log_text.tag_config("CRITICAL", foreground="red", underline=1)

    def add_log(self, message: str, level: str):
        """
        Adds a pre-formatted log message to the text widget. Thread-safe.
        """
        self.log_text.config(state='normal')

        # Store current line count before adding
        line_count = int(self.log_text.index('end-1c').split('.')[0])

        # Add new log with timestamp
        self.log_text.insert(tk.END, message, (level,))
        if not message.endswith('\n'):
            self.log_text.insert(tk.END, '\n')

        # Record entry time and line numbers
        now = time.time()
        self.log_entries.append((now, line_count, line_count + 1))

        # Keep text widget manageable
        if len(self.log_entries) > 10000:  # Safety valve
            self.log_text.delete(1.0, f'{len(self.log_entries) - 5000}.0')
            self.log_entries = self.log_entries[-5000:]

        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def prune_old_logs(self):
        if not self.winfo_exists():
            return

        cutoff = time.time() - 6 * 60 * 60  # 6 hours ago
        keep = []

        try:
            self.log_text.config(state='normal')
            
            # Iterate in reverse to maintain correct indices after deletions
            for i in reversed(range(len(self.log_entries))):
                entry = self.log_entries[i]
                if entry[0] <= cutoff:
                    self.log_text.delete(f'{entry[1]}.0', f'{entry[2]}.0')
                    del self.log_entries[i]
                    
            # Rebase remaining entries with correct line numbers
            new_entries = []
            current_line = 1
            for ts, _, _ in self.log_entries:
                new_entries.append((ts, current_line, current_line + 1))
                current_line += 1
                
            self.log_entries = new_entries

        finally:
            self.log_text.config(state='disabled')
            # Reschedule pruning only if window exists
            if self.winfo_exists():
                self.after(self.prune_interval, self.prune_old_logs)


class TextLogHandler(logging.Handler):
    """A logging handler that directs output to a Tkinter Text widget."""

    def __init__(self, log_frame: LogFrame):
        super().__init__()
        self.log_frame = log_frame

    def emit(self, record):
        # The handler's formatter (set in gui.py) creates the string.
        # We pass the formatted string and the original levelname to the LogFrame.
        self.log_frame.after(0, self.log_frame.add_log, self.format(record), record.levelname)


class StdoutRedirector:
    """A class to redirect stdout/stderr to the GUI log frame."""

    def __init__(self, log_frame: LogFrame, level: str, original_stream):
        self.log_frame = log_frame
        self.level = level
        self.original_stream = original_stream

    def write(self, message: str):
        # Write to the original stream (console) first
        if self.original_stream:
            self.original_stream.write(message)
            self.original_stream.flush()

        # Then write to the GUI log frame
        if message.strip():
            self.log_frame.after(0, self.log_frame.add_log, message, self.level)

    def flush(self):
        if self.original_stream:
            self.original_stream.flush()
