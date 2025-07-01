# gui/frames.py
import tkinter as tk
from tkinter import ttk
from enum import Enum
from typing import TYPE_CHECKING

from ruamel.yaml import YAML

from .components import AddPairDialog, PairConfigDialog

if TYPE_CHECKING:
    from .gui import GUI_Main

TOTAL_WIDTH = 500


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


    def __init__(self, parent: "GUI_Main") -> None:
        self.parent = parent
        self.sortedpairs: list[str] = []
        self.orders_frame: ttk.LabelFrame | None = None
        self.orders_treeview: ttk.Treeview | None = None

    def create_orders_treeview(self) -> None:
        # Get enabled pairs from config
        self.sortedpairs = sorted(
            [cfg['name'] for cfg in self.parent.config_manager.config_pingppong.pair_configs if
             cfg.get('enabled', True)]
        )
        columns = [col.value for col in self.OrdersColumns]
        self.orders_frame = ttk.LabelFrame(self.parent.root, text="Orders")
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

    def __init__(self, parent: "GUI_Main"):
        self.parent = parent
        self.balances_frame: ttk.LabelFrame | None = None
        self.balances_treeview: ttk.Treeview | None = None

    def create_balances_treeview(self) -> None:
        columns = [col.value for col in self.BalancesColumns]
        self.balances_frame = ttk.LabelFrame(self.parent.root, text="Balances")
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


class GUI_Config:
    """
    Manages the configuration window for the bot settings.
    """

    def __init__(self, parent: "GUI_Main") -> None:
        self.parent = parent
        self.config_window: tk.Toplevel | None = None
        self.debug_level_entry: ttk.Entry | None = None
        self.ttk_theme_entry: ttk.Entry | None = None
        self.pairs_treeview: ttk.Treeview | None = None
        self.status_var = tk.StringVar()
        self.status_label: ttk.Label | None = None
        self.active_dialog: tk.Toplevel | None = None

    def open(self) -> None:
        if self.config_window and self.config_window.winfo_exists():
            self.config_window.tkraise()
            return

        self.parent.btn_start.config(state="disabled")
        self.parent.btn_configure.config(state="disabled")

        self.config_window = tk.Toplevel(self.parent.root)
        self.config_window.title("Configure Bot")
        self.config_window.protocol("WM_DELETE_WINDOW", self.on_close)

        main_frame = ttk.Frame(self.config_window)
        main_frame.pack(fill='both', expand=True)

        canvas = tk.Canvas(main_frame)
        canvas.pack(side='left', fill='both', expand=True)

        content_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=content_frame, anchor='nw')

        self._setup_scroll_bindings(canvas)
        content_frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        content_frame.grid_columnconfigure(1, weight=1)
        content_frame.grid_rowconfigure(20, weight=1)

        self._create_general_settings_widgets(content_frame)
        self._create_pairs_treeview_widgets(content_frame)
        self._create_control_buttons(content_frame)
        self._create_save_button(content_frame)
        self._create_status_bar(content_frame)
        self._set_window_geometry()

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

    def _create_general_settings_widgets(self, parent_frame: ttk.Frame) -> None:
        ttk.Label(parent_frame, text="Debug Level:").grid(row=0, column=0, padx=5, pady=5, sticky='w')
        self.debug_level_entry = ttk.Entry(parent_frame)
        self.debug_level_entry.grid(row=0, column=1, padx=5, pady=5, sticky='ew')
        self.debug_level_entry.insert(0, str(self.parent.config_manager.config_pingppong.debug_level))

        ttk.Label(parent_frame, text="TTK Theme:").grid(row=1, column=0, padx=5, pady=5, sticky='w')
        self.ttk_theme_entry = ttk.Entry(parent_frame)
        self.ttk_theme_entry.grid(row=1, column=1, padx=5, pady=5, sticky='ew')
        self.ttk_theme_entry.insert(0, self.parent.config_manager.config_pingppong.ttk_theme)

    def _create_pairs_treeview_widgets(self, parent_frame: ttk.Frame) -> None:
        ttk.Label(parent_frame, text="Pair Configurations:").grid(row=2, column=0, columnspan=2, padx=5, pady=5,
                                                                  sticky='w')
        tree_frame = ttk.Frame(parent_frame)
        tree_frame.grid(row=3, column=0, columnspan=2, padx=5, pady=5, sticky='nsew')

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
        scrollbar.pack(side="right", fill="y")
        self.pairs_treeview.configure(yscrollcommand=scrollbar.set)
        self.pairs_treeview.pack(fill="both", expand=True)
        self.pairs_treeview.bind("<Double-1>", lambda event: self.edit_pair_config())
        self._populate_pairs_treeview()

    def _populate_pairs_treeview(self) -> None:
        if self.pairs_treeview:
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

    def _create_control_buttons(self, parent_frame: ttk.Frame) -> None:
        btn_frame = ttk.Frame(parent_frame)
        btn_frame.grid(row=4, column=0, columnspan=2, padx=5, pady=5, sticky='ew')
        ttk.Button(btn_frame, text="Add Pair", command=self.add_pair_config).pack(side='left', padx=2)
        ttk.Button(btn_frame, text="Remove Pair", command=self.remove_pair_config).pack(side='left', padx=2)
        ttk.Button(btn_frame, text="Edit Config", command=self.edit_pair_config).pack(side='left', padx=2)

    def _create_save_button(self, parent_frame: ttk.Frame) -> None:
        save_button = ttk.Button(parent_frame, text="Save", command=self.save_config)
        save_button.grid(row=20, column=0, columnspan=2, pady=10, sticky='ew')

    def _create_status_bar(self, parent_frame: ttk.Frame) -> None:
        status_frame = ttk.Frame(parent_frame)
        status_frame.grid(row=21, column=0, columnspan=2, pady=5, sticky='ew')
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        self.status_label.pack(fill='x')

    def _set_window_geometry(self) -> None:
        if self.config_window:
            x, y = 900, 450
            self.config_window.minsize(x, y)
            self.config_window.geometry(f"{x}x{y}")
            self.config_window.update_idletasks()

    def on_close(self) -> None:
        self.parent.btn_start.config(state="active")
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

    def save_config(self) -> None:
        config_file_path = './config/config_pingpong.yaml'
        yaml: YAML = YAML()
        yaml.default_flow_style = False
        yaml.indent(mapping=2, sequence=4, offset=2)

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
                    self.parent.config_manager.general_log.error(f"Failed to parse pair config: {e}")
                    return

        new_config = {
            'debug_level': int(self.debug_level_entry.get()) if self.debug_level_entry else 0,
            'ttk_theme': self.ttk_theme_entry.get() if self.ttk_theme_entry else 'flatly',
            'pair_configs': pair_configs
        }

        try:
            with open(config_file_path, 'w') as file:
                yaml.dump(new_config, file)
            self.update_status("Configuration saved successfully. Restart required to apply changes.", 'lightgreen')
            self.parent.reload_configuration(loadxbridgeconf=True)
        except Exception as e:
            self.update_status(f"Failed to save configuration: {e}", 'lightcoral')
            self.parent.config_manager.general_log.error(f"Failed to save config: {e}")

    def update_status(self, message: str, color: str = 'black') -> None:
        if self.status_label:
            self.status_var.set(message)
            self.status_label.config(foreground=color)