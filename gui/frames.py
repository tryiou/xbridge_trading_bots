# gui/frames.py
import asyncio
import logging
import threading
import tkinter as tk
from tkinter import ttk
from enum import Enum
from typing import TYPE_CHECKING

from ruamel.yaml import YAML

from definitions.config_manager import ConfigManager
from definitions.starter import run_async_main
from .components import AddPairDialog, PairConfigDialog, AddSellerDialog, SellerConfigDialog

if TYPE_CHECKING:
    from .gui import GUI_Main

TOTAL_WIDTH = 500

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
            return

        self.main_app.status_var.set(f"{self.strategy_name.capitalize()} bot is running...")
        startup_tasks = self.config_manager.strategy_instance.get_startup_tasks()
        self.send_process = threading.Thread(target=run_async_main,
                                             args=(self.config_manager, None, startup_tasks),
                                             daemon=True)
        try:
            self.send_process.start()
            self.started = True
            self.update_button_states()
            self.config_manager.general_log.info(f"{self.strategy_name.capitalize()} bot started successfully.")
        except Exception as e:
            self.main_app.status_var.set(f"Error starting {self.strategy_name} bot: {e}")
            self.config_manager.general_log.error(f"Error starting bot thread: {e}")
            self.stop(reload_config=False)

    def stop(self, reload_config: bool = True, join_timeout: int | None = 5):
        """Stops the bot and performs cleanup."""
        if not self.config_manager:
            return

        self.main_app.status_var.set(f"Stopping {self.strategy_name} bot...")
        self.config_manager.general_log.info(f"Attempting to stop {self.strategy_name} bot...")

        if self.config_manager.controller:
            self.config_manager.controller.shutdown_event.set()

        if self.send_process:
            self.send_process.join(timeout=join_timeout)
            if self.send_process.is_alive():
                self.config_manager.general_log.warning("Bot thread did not terminate gracefully.")
                self.main_app.status_var.set("Bot stopped (thread timeout).")
            else:
                self.main_app.status_var.set("Bot stopped.")
                self.config_manager.general_log.info("Bot stopped successfully.")

        self.started = False
        self.update_button_states()

        if reload_config:
            self.reload_configuration(loadxbridgeconf=False)

    def cancel_all(self):
        """Cancels all open orders on the exchange."""
        if not self.config_manager:
            return
        self.main_app.status_var.set("Cancelling all open orders...")
        try:
            asyncio.run(self.config_manager.xbridge_manager.cancelallorders())
            self.main_app.status_var.set("Cancelled all open orders.")
            self.config_manager.general_log.info("cancel_all: All orders cancelled successfully.")
        except Exception as e:
            self.main_app.status_var.set(f"Error cancelling orders: {e}")
            self.config_manager.general_log.error(f"Error during cancel_all: {e}")

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
        if self.started and self.send_process and not self.send_process.is_alive():
            self.config_manager.general_log.error(f"{self.strategy_name} bot crashed!")
            self.main_app.status_var.set(f"{self.strategy_name} bot crashed!")
            self.stop(reload_config=False)
            self.cancel_all()
        
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
        self.initialize_config(loadxbridgeconf=loadxbridgeconf)
        self.purge_and_recreate_widgets()

    def purge_and_recreate_widgets(self):
        """Purges and recreates widgets. To be overridden."""
        pass

    def update_button_states(self):
        """Updates button states based on bot status. To be overridden."""
        pass

class PingPongFrame(BaseStrategyFrame):
    def __init__(self, parent, main_app: "GUI_Main", master_config_manager: ConfigManager):
        super().__init__(parent, main_app, "pingpong", master_config_manager)

    def create_widgets(self):
        self.gui_orders = GUI_Orders(self)
        self.gui_balances = GUI_Balances(self)
        self.gui_config = GUI_Config(self)
        self.create_buttons()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()

    def create_buttons(self):
        button_frame = ttk.Frame(self)
        button_frame.grid(column=0, row=0, padx=5, pady=5, sticky='ew')
        btn_width = 12
        self.btn_start = ttk.Button(button_frame, text="START", command=self.start, width=btn_width)
        self.btn_start.grid(column=0, row=0, padx=5, pady=5)
        self.btn_stop = ttk.Button(button_frame, text="STOP", command=self.stop, width=btn_width)
        self.btn_stop.grid(column=1, row=0, padx=5, pady=5)
        self.btn_cancel_all = ttk.Button(button_frame, text="CANCEL ALL", command=self.cancel_all, width=btn_width)
        self.btn_cancel_all.grid(column=2, row=0, padx=5, pady=5)
        self.btn_configure = ttk.Button(button_frame, text="CONFIGURE", command=self.open_configure_window, width=btn_width)
        self.btn_configure.grid(column=3, row=0, padx=5, pady=5)
        self.update_button_states()

    def update_button_states(self):
        self.btn_start.config(state="disabled" if self.started else "active")
        self.btn_stop.config(state="active" if self.started else "disabled")
        self.btn_configure.config(state="disabled" if self.started else "active")

    def refresh_gui(self):
        if self.winfo_exists(): # Check if widget exists before proceeding
            self.gui_orders.update_order_display()
            self.gui_balances.update_balance_display()
            super().refresh_gui()

    def open_configure_window(self):
        self.gui_config.open()

    def purge_and_recreate_widgets(self):
        self.gui_orders.purge_treeview()
        self.gui_balances.purge_treeview()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()


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

    def create_orders_treeview(self) -> None:
        # Get enabled pairs from config
        self.sortedpairs = sorted(self.parent.config_manager.pairs.keys())
        columns = [col.value for col in self.OrdersColumns]
        self.orders_frame = ttk.LabelFrame(self.parent, text="Orders")
        self.orders_frame.grid(row=1, padx=5, pady=5, sticky='ew', columnspan=4)

        height = len(self.sortedpairs) + 1
        self.orders_treeview = ttk.Treeview(
            self.orders_frame,
            columns=list(columns),
            height=height,
            show="headings"
        )
        self.orders_treeview.grid(padx=5, pady=5)

        self._configure_columns()

        for pair in self.sortedpairs:
            if self.orders_treeview:
                self.orders_treeview.insert("", tk.END, values=[pair, "None", "None", "X", "None"])

    def _configure_columns(self):
        for column in self.OrdersColumns:
            width: int
            anchor: str
            # Distribution: Pair (25%), Status (25%), Side (20%), Flag (10%), Variation (20%) = 100%
            col_configs = {
                self.OrdersColumns.PAIR: (0.25, "w"),
                self.OrdersColumns.STATUS: (0.25, "center"),
                self.OrdersColumns.SIDE: (0.20, "center"),
                self.OrdersColumns.FLAG: (0.10, "center"),
                self.OrdersColumns.VARIATION: (0.20, "e"),
            }
            percentage, anchor = col_configs[column]
            width = int(TOTAL_WIDTH * percentage)
            if self.orders_treeview:
                self.orders_treeview.heading(column.value, text=column.value, anchor=anchor)
                self.orders_treeview.column(column.value, width=width, anchor=anchor)

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
        self.balances_frame: ttk.LabelFrame | None = None
        self.balances_treeview: ttk.Treeview | None = None

    def create_balances_treeview(self) -> None:
        columns = [col.value for col in self.BalancesColumns]
        self.balances_frame = ttk.LabelFrame(self.parent, text="Balances")
        self.balances_frame.grid(row=2, padx=5, pady=5, sticky='ew', columnspan=4)

        height = len(self.parent.config_manager.tokens.keys())
        self.balances_treeview = ttk.Treeview(self.balances_frame, columns=list(columns), show="headings",
                                              height=height, selectmode="none")
        self.balances_treeview.grid(padx=5, pady=5)
        self._configure_balance_columns()

        for token in self.parent.config_manager.tokens:
            if self.balances_treeview:
                data = (token, str(None), str(None), str(None), str(None))
                self.balances_treeview.insert("", tk.END, values=data)

    def _configure_balance_columns(self):
        # Distribution: Coin (25%), USD Ticker (20%), Total (20%), Free (20%), Total USD (15%) = 100%
        col_configs = {
            self.BalancesColumns.COIN: (0.25, "w"),
            self.BalancesColumns.USD_TICKER: (0.20, "e"),
            self.BalancesColumns.TOTAL: (0.20, "e"),
            self.BalancesColumns.FREE: (0.20, "e"),
            self.BalancesColumns.TOTAL_USD: (0.15, "e"),
        }

        for column, (percentage, anchor) in col_configs.items():
            if self.balances_treeview:
                width = int(TOTAL_WIDTH * percentage)
                self.balances_treeview.heading(column.value, text=column.value, anchor=anchor)
                self.balances_treeview.column(column.value, width=width, anchor=anchor)

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

    def __init__(self, parent: "BaseStrategyFrame", title: str, config_file_path: str):
        self.parent = parent
        self.title_text = title
        self.config_file_path = config_file_path
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
            self.update_status("Configuration saved successfully. Restart required to apply changes.", 'lightgreen')
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


class GUI_Config(BaseConfigWindow):
    """
    Manages the configuration window for the bot settings.
    """

    def __init__(self, parent: "BaseStrategyFrame") -> None:
        super().__init__(parent, "Configure PingPong Bot", './config/config_pingpong.yaml')
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
        super().__init__(parent, "Configure Basic Seller", './config/config_basicseller.yaml')
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

class BasicSellerFrame(BaseStrategyFrame):
    def __init__(self, parent, main_app: "GUI_Main", master_config_manager: ConfigManager):
        super().__init__(parent, main_app, "basic_seller", master_config_manager)

    def create_widgets(self):
        self.gui_orders = GUI_Orders(self)
        self.gui_balances = GUI_Balances(self)
        self.gui_config = GUI_Config_BasicSeller(self)
        self.create_buttons()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()

    def create_buttons(self):
        button_frame = ttk.Frame(self)
        button_frame.grid(column=0, row=0, padx=5, pady=5, sticky='ew')
        btn_width = 12
        self.btn_start = ttk.Button(button_frame, text="START", command=self.start, width=btn_width)
        self.btn_start.grid(column=0, row=0, padx=5, pady=5)
        self.btn_stop = ttk.Button(button_frame, text="STOP", command=self.stop, width=btn_width)
        self.btn_stop.grid(column=1, row=0, padx=5, pady=5)
        self.btn_cancel_all = ttk.Button(button_frame, text="CANCEL ALL", command=self.cancel_all, width=btn_width)
        self.btn_cancel_all.grid(column=2, row=0, padx=5, pady=5)
        self.btn_configure = ttk.Button(button_frame, text="CONFIGURE", command=self.open_configure_window, width=btn_width)
        self.btn_configure.grid(column=3, row=0, padx=5, pady=5)
        self.update_button_states()

    def update_button_states(self):
        self.btn_start.config(state="disabled" if self.started else "active")
        self.btn_stop.config(state="active" if self.started else "disabled")
        self.btn_configure.config(state="disabled" if self.started else "active")

    def open_configure_window(self):
        self.gui_config.open()

    def refresh_gui(self):
        if self.winfo_exists():  # Check if widget exists before proceeding
            self.gui_orders.update_order_display()
            self.gui_balances.update_balance_display()
            super().refresh_gui()

    def purge_and_recreate_widgets(self):
        self.gui_orders.purge_treeview()
        self.gui_balances.purge_treeview()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()

class ArbitrageFrame(BaseStrategyFrame):
    def __init__(self, parent, main_app: "GUI_Main", master_config_manager: ConfigManager):
        super().__init__(parent, main_app, "arbitrage", master_config_manager)

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
        """Adds a log message to the text widget. Thread-safe."""
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, message, (level,))
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')


class TextLogHandler(logging.Handler):
    """A logging handler that directs output to a Tkinter Text widget."""
    def __init__(self, log_frame: LogFrame):
        super().__init__()
        self.log_frame = log_frame
        self.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S'))

    def emit(self, record):
        msg = self.format(record) + '\n'
        self.log_frame.after(0, self.log_frame.add_log, msg, record.levelname)


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