# gui/frames.py
import asyncio
import logging
import threading
import abc
import tkinter as tk
from enum import Enum
from tkinter import ttk
from typing import TYPE_CHECKING

from ruamel.yaml import YAML

from definitions.config_manager import ConfigManager
from definitions.starter import run_async_main
from gui.components import AddPairDialog, PairConfigDialog, AddSellerDialog, SellerConfigDialog

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
            logging.error("Cannot start: config_manager is not initialized.")
            return

        log = self.config_manager.general_log
        log.debug("GUI: START button clicked.")

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
                self.send_process.join()  # Wait indefinitely
            self._finalize_stop(reload_config)

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
                self.main_app.root.after(0, lambda: self.main_app.status_var.set(f"Error cancelling orders: {e}"))
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
        """Perform any final cleanup, like unbinding events."""
        pass

    def open_configure_window(self):
        """Opens the configuration window for this strategy. To be overridden."""
        pass


class StandardStrategyFrame(BaseStrategyFrame, metaclass=abc.ABCMeta):
    """Base class for standard strategy frames with Orders and Balances views."""

    def __init__(self, parent, main_app: "GUI_Main", strategy_name: str, master_config_manager: ConfigManager):
        self.gui_orders: "GUI_Orders"
        self.gui_balances: "GUI_Balances"
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
        self.gui_orders = GUI_Orders(self)
        self.gui_balances = GUI_Balances(self)
        self.gui_config = self._create_config_gui()
        self.create_standard_buttons()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()

    def refresh_gui(self):
        """Refreshes the Orders and Balances display."""
        if self.winfo_exists():  # Check if widget exists before proceeding
            self.gui_orders.update_order_display()
            self.gui_balances.update_balance_display()
            super().refresh_gui()

    def open_configure_window(self):
        """Opens the configuration window for this strategy."""
        self.gui_config.open()

    def purge_and_recreate_widgets(self):
        """Purges and recreates the Orders and Balances treeviews."""
        self.gui_orders.purge_treeview()
        self.gui_balances.purge_treeview()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()

    def cleanup(self):
        """Unbind events to prevent errors during teardown."""
        super().cleanup()
        # The <Configure> event is bound to this frame by its GUI_Orders component.
        # Unbinding it here prevents TclErrors during test teardown.
        self.unbind("<Configure>")


class PingPongFrame(StandardStrategyFrame):
    def __init__(self, parent, main_app: "GUI_Main", master_config_manager: ConfigManager):
        super().__init__(parent, main_app, "pingpong", master_config_manager)

    def _create_config_gui(self) -> "BaseConfigWindow":
        return GUI_Config_PingPong(self)


class GUI_Orders:
    """
    Manages the display and updates of trading orders in a Treeview widget.
    """

    class OrdersColumns(Enum):
        PAIR = "Pair"
        STATUS = "Status"
        SIDE = "Side"
        FLAG = "Flag"
        VARIATION = "Variation"

    def __init__(self, parent: "BaseStrategyFrame") -> None:
        self.parent = parent
        self.sortedpairs: list[str] = []
        self.orders_frame: ttk.LabelFrame | None = None
        self.orders_treeview: ttk.Treeview | None = None
        self._is_resizing = False  # Add a re-entry guard flag
        # Create a logger specific to this GUI component instance
        self.logger = logging.getLogger(f"gui.{self.parent.strategy_name}.orders")
        # Bind the resize event to the parent frame once during initialization.
        # This prevents adding duplicate bindings on each configuration reload.
        self.logger.debug(f"Binding <Configure> event.")
        self.parent.bind("<Configure>", self._on_resize)

    def create_orders_treeview(self) -> None:
        # Get enabled pairs from config
        self.sortedpairs = sorted(self.parent.config_manager.pairs.keys())
        columns = [col.value for col in self.OrdersColumns]
        self.orders_frame = ttk.LabelFrame(self.parent, text="Orders")
        self.orders_frame.grid(row=1, column=0, padx=5, pady=5, sticky='nsew')
        self.orders_frame.grid_rowconfigure(0, weight=1)
        self.orders_frame.grid_columnconfigure(0, weight=1)

        height = len(self.sortedpairs) + 1
        self.orders_treeview = ttk.Treeview(
            self.orders_frame,
            columns=list(columns),
            height=height,
            show="headings"
        )
        self.orders_treeview.grid(row=0, column=0, padx=5, pady=5, sticky='nsew')

        # Bind to the <Map> event for the *initial* configuration.
        # This ensures the widget is drawn and has a size before we configure columns.
        self.orders_treeview.bind("<Map>", self._initial_configure)

        for pair in self.sortedpairs:
            if self.orders_treeview:
                self.orders_treeview.insert("", tk.END, values=[pair, "None", "None", "X", "None"])

    def _initial_configure(self, event=None):
        """A one-time configuration that runs after the widget is mapped to the screen."""
        # The _on_resize method contains the logic to configure both treeviews.
        self._on_resize()
        # Unbind after the first run to avoid this logic running again if the widget is hidden and re-shown.
        self.orders_treeview.unbind("<Map>")

    def _on_resize(self, event=None):
        """
        Schedules the column resizing to avoid recursive event loops.
        This is the entry point for the <Configure> event.
        """
        if self._is_resizing:
            # self.logger.debug("Resize event ignored due to re-entry guard.")
            return
        # self.logger.debug("Resize event triggered, scheduling resize action.")
        self._is_resizing = True
        # Schedule the actual resize work to run after a short delay.
        # This breaks the synchronous event cascade that causes the freeze.
        self.parent.after(50, self._execute_resize)

    def _execute_resize(self):
        """Performs the actual column resizing and resets the guard flag."""
        # self.logger.debug("Executing scheduled resize.")
        try:
            # We only need to re-configure, as the logic is now dynamic.
            if self.orders_treeview and self.orders_treeview.winfo_exists():
                # self.logger.debug("Configuring orders columns.")
                self._configure_columns()
            # The balances treeview is part of the same parent frame, so we can resize it from here.
            if hasattr(self.parent,
                       'gui_balances') and self.parent.gui_balances.balances_treeview and self.parent.gui_balances.balances_treeview.winfo_exists():
                # self.logger.debug("Configuring balances columns.")
                self.parent.gui_balances._configure_balance_columns()
        finally:
            # Reset the flag *after* the work is done.
            # self.logger.debug("Resize execution finished, resetting flag.")
            self._is_resizing = False

    def _configure_columns(self):
        if not self.orders_treeview or self.orders_treeview.winfo_width() <= 1:
            return  # Don't configure if widget isn't drawn yet

        # Ratios for proportional scaling
        col_ratios = {
            self.OrdersColumns.PAIR: 25,
            self.OrdersColumns.STATUS: 25,
            self.OrdersColumns.SIDE: 20,
            self.OrdersColumns.FLAG: 10,
            self.OrdersColumns.VARIATION: 20,
        }
        col_anchors = {
            self.OrdersColumns.PAIR: "w",
            self.OrdersColumns.STATUS: "center",
            self.OrdersColumns.SIDE: "center",
            self.OrdersColumns.FLAG: "center",
            self.OrdersColumns.VARIATION: "e",
        }

        total_ratio = sum(col_ratios.values())
        available_width = self.orders_treeview.winfo_width()

        for column in self.OrdersColumns:
            if self.orders_treeview and total_ratio > 0:
                ratio = col_ratios.get(column, 0)
                anchor = col_anchors.get(column, "center")
                width = int((ratio / total_ratio) * available_width)
                self.orders_treeview.heading(column.value, text=column.value, anchor=anchor)
                self.orders_treeview.column(column.value, width=width, anchor=anchor, stretch=False)

    def update_order_display(self) -> None:
        """
        Updates the order display in the Treeview with current bot status.
        """
        if self.parent.started:
            for key, pair in self.parent.config_manager.pairs.items():
                if self.orders_treeview:
                    for item_id in self.orders_treeview.get_children():
                        values = self.orders_treeview.item(item_id, 'values')

                        display_text = pair.cfg['name']
                        if values and values[0] == display_text:
                            order_status = 'None'
                            current_order_side = 'None'
                            variation_display = 'None'

                            if self.parent.started and pair.dex.order and 'status' in pair.dex.order:
                                order_status = pair.dex.order.get('status', 'None')
                                current_order_side = pair.dex.current_order.get('side', 'None')
                                variation_display = str(pair.dex.variation)
                            elif pair.dex.disabled:
                                order_status = 'Disabled'

                            new_values = [
                                display_text,
                                order_status,
                                current_order_side,
                                self.get_flag(order_status),
                                variation_display
                            ]
                            if tuple(new_values) != values:
                                self.orders_treeview.item(item_id, values=new_values)

    def purge_treeview(self) -> None:
        if self.orders_frame:
            for widget in self.orders_frame.winfo_children():
                widget.destroy()
            self.orders_frame.destroy()
        self.orders_frame = None
        self.orders_treeview = None

    @staticmethod
    def get_flag(status: str) -> str:
        """Returns a flag ('V' or 'X') based on the order status."""
        return 'V' if status in {
            'open', 'new', 'created', 'accepting', 'hold', 'initialized', 'committed', 'finished'
        } else 'X'


class GUI_Balances:
    class BalancesColumns(Enum):
        COIN = "Coin"
        USD_TICKER = "USD ticker"
        TOTAL = "Total"
        FREE = "Free"
        TOTAL_USD = "Total USD"

    def __init__(self, parent: "BaseStrategyFrame"):
        self.parent = parent
        # Create a logger specific to this GUI component instance
        self.logger = logging.getLogger(f"gui.{self.parent.strategy_name}.balances")
        self.balances_frame: ttk.LabelFrame | None = None
        self.balances_treeview: ttk.Treeview | None = None

    def create_balances_treeview(self) -> None:
        columns = [col.value for col in self.BalancesColumns]
        self.balances_frame = ttk.LabelFrame(self.parent, text="Balances")
        self.balances_frame.grid(row=2, column=0, padx=5, pady=5, sticky='nsew')
        self.balances_frame.grid_rowconfigure(0, weight=1)
        self.balances_frame.grid_columnconfigure(0, weight=1)

        height = len(self.parent.config_manager.tokens.keys())
        self.balances_treeview = ttk.Treeview(self.balances_frame, columns=list(columns), show="headings",
                                              height=height, selectmode="none")
        self.balances_treeview.grid(row=0, column=0, padx=5, pady=5, sticky='nsew')
        # The initial configuration is now triggered by the <Map> event on the Orders treeview.
        for token in self.parent.config_manager.tokens:
            if self.balances_treeview:
                data = (token, str(None), str(None), str(None), str(None))
                self.balances_treeview.insert("", tk.END, values=data)

    def _configure_balance_columns(self):
        if not self.balances_treeview or self.balances_treeview.winfo_width() <= 1:
            return  # Don't configure if widget isn't drawn yet

        # Distribution: Coin (25%), USD Ticker (20%), Total (20%), Free (20%), Total USD (15%) = 100%
        col_ratios = {
            self.BalancesColumns.COIN: 25,
            self.BalancesColumns.USD_TICKER: 20,
            self.BalancesColumns.TOTAL: 20,
            self.BalancesColumns.FREE: 20,
            self.BalancesColumns.TOTAL_USD: 15,
        }
        col_anchors = {
            self.BalancesColumns.COIN: "w",
            self.BalancesColumns.USD_TICKER: "e",
            self.BalancesColumns.TOTAL: "e",
            self.BalancesColumns.FREE: "e",
            self.BalancesColumns.TOTAL_USD: "e",
        }

        total_ratio = sum(col_ratios.values())
        available_width = self.balances_treeview.winfo_width()

        for column in self.BalancesColumns:
            if self.balances_treeview and total_ratio > 0:
                ratio = col_ratios.get(column, 0)
                anchor = col_anchors.get(column, "center")
                width = int((ratio / total_ratio) * available_width)
                self.balances_treeview.heading(column.value, text=column.value, anchor=anchor)
                self.balances_treeview.column(column.value, width=width, anchor=anchor, stretch=False)

    def update_balance_display(self) -> None:
        if self.balances_treeview:
            for item_id in self.balances_treeview.get_children():
                values = self.balances_treeview.item(item_id, 'values')
                token = values[0]

                token_data = self.parent.config_manager.tokens[token]
                usd_price = token_data.cex.usd_price
                dex_total_balance = token_data.dex.total_balance
                dex_free_balance = token_data.dex.free_balance

                new_values = [
                    token,
                    f"{usd_price:.3f}$" if usd_price else f"{0:.3f}$",
                    f"{dex_total_balance:.4f}" if dex_total_balance else f"{0:.4f}",
                    f"{dex_free_balance:.4f}" if dex_free_balance else f"{0:.4f}",
                    f"{usd_price * dex_total_balance:.3f}$" if usd_price and dex_total_balance else f"{0:.3f}$"
                ]
                if list(new_values) != list(values):
                    self.balances_treeview.item(item_id, values=new_values)

    def purge_treeview(self) -> None:
        if self.balances_frame:
            for widget in self.balances_frame.winfo_children():
                widget.destroy()
            self.balances_frame.destroy()
        self.balances_frame = None
        self.balances_treeview = None


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


class ArbitrageFrame(BaseStrategyFrame):
    def __init__(self, parent, main_app: "GUI_Main", master_config_manager: ConfigManager):
        super().__init__(parent, main_app, "arbitrage", master_config_manager)
        # Configure the grid for expansion. This allows the child frames
        # (orders, balances) to grow with the window.
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)  # Orders frame
        self.grid_rowconfigure(2, weight=1)  # Balances frame

    def create_widgets(self):
        ttk.Label(self, text="Arbitrage Controls Go Here").pack(padx=20, pady=20)
        # TODO: Add entry field for min_profit_margin.
        # TODO: Add a Text widget to display arbitrage opportunities.
        # TODO: Add START/STOP buttons.


# --- New Logging Components ---

class LogFrame(ttk.Frame):
    """A frame for displaying application logs."""

    def __init__(self, parent):
        super().__init__(parent)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

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
        # The level name is used as the tag for coloring
        self.log_text.insert(tk.END, message, (level,))
        if not message.endswith('\n'):
            self.log_text.insert(tk.END, '\n')
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')


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
