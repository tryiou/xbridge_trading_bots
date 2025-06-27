import asyncio
import re
import threading
import tkinter as tk  # type: ignore
from tkinter import ttk

from ruamel.yaml import YAML  # type: ignore
from ttkbootstrap import Style

from definitions.config_manager import ConfigManager

TOTAL_WIDTH = 500


class GUI_Orders:
    """
    Manages the display and updates of trading orders in a Treeview widget.
    """

    def __init__(self, parent: "GUI_Main") -> None:
        self.parent = parent
        self.sortedpairs: list[str] = []
        self.orders_frame: ttk.LabelFrame | None = None
        self.orders_treeview: ttk.Treeview | None = None

    def create_orders_treeview(self) -> None:
        # Get enabled pairs from config
        self.sortedpairs = sorted(
            [cfg['name'] for cfg in self.parent.config_manager.config_pp.pair_configs if cfg.get('enabled', True)]
        )
        columns = ("Pair", "Status", "Side", "Flag", "Variation")
        self.orders_frame = ttk.LabelFrame(self.parent.root, text="Orders")
        self.orders_frame.grid(row=1, padx=5, pady=5, sticky='ew', columnspan=4)

        height = len(self.sortedpairs) + 1
        self.orders_treeview = ttk.Treeview(
            self.orders_frame,
            columns=list(columns),  # Convert tuple to list for type consistency
            height=height,
            show="headings"
        )
        self.orders_treeview.grid(padx=5, pady=5)

        pair_weight = 2
        other_weight = 1
        weights = [pair_weight if col == "Pair" else other_weight for col in columns]
        total_weight = sum(weights)
        unit_width = TOTAL_WIDTH / total_weight

        for label_text in columns:
            anchor: str
            width: int
            if label_text == "Pair":
                anchor = "w"
                width = int(unit_width * pair_weight)
            else:
                anchor = "center" if label_text != "Variation" else "e"
                width = int(unit_width * other_weight)
            if self.orders_treeview:
                self.orders_treeview.heading(label_text, text=label_text, anchor=anchor)
                self.orders_treeview.column(label_text, width=width, anchor=anchor)

        for pair in self.sortedpairs:
            if self.orders_treeview:
                self.orders_treeview.insert("", tk.END, values=[pair, "None", "None", "X", "None"])

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
                            # Simplify repeated access to order status and side
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
                                GUI_Main.get_flag(order_status),  # Use static method
                                variation_display
                            ]
                            if list(new_values) != list(values):
                                self.orders_treeview.item(item_id, values=new_values)

    def purge_treeview(self) -> None:
        # Destroy existing orders and balances frames within their respective classes.
        if self.orders_frame:
            for widget in self.orders_frame.winfo_children():
                widget.destroy()
            self.orders_frame.destroy()
        self.orders_frame = None
        self.orders_treeview = None


class GUI_Balances:
    def __init__(self, parent):
        self.parent = parent
        self.balances_frame = None
        self.balances_treeview = None

    def create_balances_treeview(self) -> None:
        columns = ("Coin", "USD ticker", "Total", "Free", "Total USD")
        self.balances_frame = ttk.LabelFrame(self.parent.root, text="Balances")
        self.balances_frame.grid(row=2, padx=5, pady=5, sticky='ew', columnspan=4)

        height = len(self.parent.config_manager.tokens.keys())
        self.balances_treeview = ttk.Treeview(self.balances_frame, columns=columns, show="headings",
                                              height=height, selectmode="none")
        self.balances_treeview.grid(padx=5, pady=5)
        width = int(TOTAL_WIDTH / len(columns))
        for col in columns:
            if col == "Coin":
                anchor = "w"
            else:
                anchor = "e"
            self.balances_treeview.heading(col, text=col, anchor=anchor)
            self.balances_treeview.column(col, width=width, anchor=anchor)

        for token in self.parent.config_manager.tokens:
            if self.balances_treeview:
                data = (token, str(None), str(None), str(None), str(None))
                self.balances_treeview.insert("", tk.END, values=data)

    def update_balance_display(self) -> None:
        """
        Updates the balance display in the Treeview with current token balances.
        """
        if self.balances_treeview:
            for item_id in self.balances_treeview.get_children():
                values = self.balances_treeview.item(item_id, 'values')
                token = values[0]

                # Simplify repeated access to balance data
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
        """
        Opens the configuration window or brings it to the front if already open.
        """
        if self.config_window:
            self.config_window.tkraise()
            return

        self.parent.btn_start.config(state="disabled")
        self.parent.btn_configure.config(state="disabled")

        self.config_window = tk.Toplevel(self.parent.root)
        self.config_window.title("Configure Bot")  # type: ignore
        self.config_window.protocol("WM_DELETE_WINDOW", self.on_close)  # type: ignore

        main_frame = ttk.Frame(self.config_window)  # type: ignore
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
        """
        Handles keyboard scrolling for the canvas, but allows widgets like
        Treeview to handle their own keyboard navigation.
        """
        # If the widget with focus is the pairs_treeview, do not scroll the canvas.
        # Let the Treeview handle its default Up/Down key behavior (selection change).
        if self.config_window and self.pairs_treeview and self.config_window.focus_get() == self.pairs_treeview:
            return

        canvas.yview_scroll(direction, "units")

    def _setup_scroll_bindings(self, canvas: tk.Canvas) -> None:
        """
        Sets up keyboard and mouse scroll bindings for the canvas.
        """
        if self.config_window:
            self.config_window.bind("<Up>", lambda event: self._on_key_press_scroll(event, canvas, -1))
            self.config_window.bind("<Down>", lambda event: self._on_key_press_scroll(event, canvas, 1))
            self.config_window.bind("<Prior>", lambda event: canvas.yview_scroll(-10, "units"))  # Page Up
            self.config_window.bind("<Next>", lambda event: canvas.yview_scroll(10, "units"))  # Page Down

            def mouse_scroll(event: tk.Event) -> None:
                delta = event.delta if event.delta != 0 else event.widget.winfo_pointery()  # type: ignore
                if delta > 0:
                    canvas.yview_scroll(-1, "units")
                else:
                    canvas.yview_scroll(1, "units")

            self.config_window.bind("<MouseWheel>", mouse_scroll)

    def _create_general_settings_widgets(self, parent_frame: ttk.Frame) -> None:
        """
        Creates widgets for general settings like Debug Level and TTK Theme.
        """
        ttk.Label(parent_frame, text="Debug Level:").grid(row=0, column=0, padx=5, pady=5, sticky='w')
        self.debug_level_entry = ttk.Entry(parent_frame)
        self.debug_level_entry.grid(row=0, column=1, padx=5, pady=5, sticky='ew')
        self.debug_level_entry.insert(0, str(self.parent.config_manager.config_pp.debug_level))

        ttk.Label(parent_frame, text="TTK Theme:").grid(row=1, column=0, padx=5, pady=5, sticky='w')
        self.ttk_theme_entry = ttk.Entry(parent_frame)
        self.ttk_theme_entry.grid(row=1, column=1, padx=5, pady=5, sticky='ew')
        self.ttk_theme_entry.insert(0, self.parent.config_manager.config_pp.ttk_theme)

    def _create_pairs_treeview_widgets(self, parent_frame: ttk.Frame) -> None:
        """
        Creates the Treeview widget for displaying and managing pair configurations.
        """
        ttk.Label(parent_frame, text="Pair Configurations:").grid(row=2, column=0, columnspan=2, padx=5, pady=5,
                                                                  sticky='w')

        tree_frame = ttk.Frame(parent_frame)
        tree_frame.grid(row=3, column=0, columnspan=2, padx=5, pady=5, sticky='nsew')

        columns = (
            'name', 'enabled', 'pair', 'price_variation_tolerance',
            'sell_price_offset', 'usd_amount', 'spread'
        )
        self.pairs_treeview = ttk.Treeview(tree_frame, columns=columns, show='headings', height=8)

        # Define headings
        if self.pairs_treeview:
            self.pairs_treeview.heading('name', text='Name')
            self.pairs_treeview.heading('enabled', text='Enabled')
            self.pairs_treeview.heading('pair', text='Pair')
            self.pairs_treeview.heading('price_variation_tolerance', text='Var. Tol.')
            self.pairs_treeview.heading('sell_price_offset', text='Sell Offset')
            self.pairs_treeview.heading('usd_amount', text='USD Amt')
            self.pairs_treeview.heading('spread', text='Spread')

        # Set column widths and alignment
        if self.pairs_treeview:
            self.pairs_treeview.column('name', width=150, anchor='w')
            self.pairs_treeview.column('enabled', width=75, anchor='center')
            self.pairs_treeview.column('pair', width=150, anchor='w')
            self.pairs_treeview.column('price_variation_tolerance', width=120, anchor='e')
            self.pairs_treeview.column('sell_price_offset', width=120, anchor='e')
            self.pairs_treeview.column('usd_amount', width=120, anchor='e')
            self.pairs_treeview.column('spread', width=120, anchor='e')

        # Add scrollbar
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.pairs_treeview.yview)
        scrollbar.pack(side="right", fill="y")
        self.pairs_treeview.configure(yscrollcommand=scrollbar.set)
        self.pairs_treeview.pack(fill="both", expand=True)
        # Bind double-click to edit
        self.pairs_treeview.bind("<Double-1>", lambda event: self.edit_pair_config())
        # Populate with existing configs
        self._populate_pairs_treeview()

    def _populate_pairs_treeview(self) -> None:
        """
        Populates the pairs Treeview with data from the current configuration.
        """
        if self.pairs_treeview:
            for cfg in self.parent.config_manager.config_pp.pair_configs:
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
        """
        Creates buttons for adding, removing, and editing pair configurations.
        """
        btn_frame = ttk.Frame(parent_frame)
        btn_frame.grid(row=4, column=0, columnspan=2, padx=5, pady=5, sticky='ew')

        ttk.Button(btn_frame, text="Add Pair", command=self.add_pair_config).pack(side='left', padx=2)
        ttk.Button(btn_frame, text="Remove Pair", command=self.remove_pair_config).pack(side='left', padx=2)
        ttk.Button(btn_frame, text="Edit Config", command=self.edit_pair_config).pack(side='left', padx=2)

    def _create_save_button(self, parent_frame: ttk.Frame) -> None:
        """
        Creates the save button for the configuration.
        """
        save_button = ttk.Button(parent_frame, text="Save", command=self.save_config)
        save_button.grid(row=20, column=0, columnspan=2, pady=10, sticky='ew')

    def _create_status_bar(self, parent_frame: ttk.Frame) -> None:
        """
        Creates the status bar for displaying messages in the configuration window.
        """
        status_frame = ttk.Frame(parent_frame)
        status_frame.grid(row=21, column=0, columnspan=2, pady=5, sticky='ew')
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        self.status_label.pack(fill='x')

    def _set_window_geometry(self) -> None:
        """
        Sets the minimum size and initial geometry of the configuration window.
        """
        if self.config_window:
            x = 900
            y = 450
            self.config_window.minsize(x, y)
            self.config_window.geometry(f"{x}x{y}")
            self.config_window.update_idletasks()

    def on_close(self) -> None:
        """
        Handles the closing of the configuration window.
        Re-enables main window buttons.
        """
        self.parent.btn_start.config(state="active")
        self.parent.btn_configure.config(state="active")
        if self.config_window:
            self.config_window.destroy()
        self.config_window = None

    def _open_single_dialog(self, dialog_class, *dialog_args) -> tk.Toplevel:
        """
        Manages opening a single dialog, ensuring any previously open dialog is closed.
        """
        if self.active_dialog and self.active_dialog.winfo_exists():
            self.active_dialog.destroy()

        dialog = dialog_class(self.config_window, *dialog_args)
        self.active_dialog = dialog
        self.config_window.wait_window(dialog)

        # If the dialog that just closed is the one we were tracking, clear it.
        # This prevents a race condition where a new dialog is opened before
        # this wait_window returns.
        if self.active_dialog is dialog:
            self.active_dialog = None
        return dialog

    @staticmethod
    def is_valid_pair(pair_symbol: str) -> bool:
        """
        Validates if a given string is in the format TOKEN1/TOKEN2.
        """
        return bool(re.match(r"^[A-Z]+/[A-Z]+$", pair_symbol))

    def add_pair_config(self) -> None:
        """
        Opens a dialog to add a new pair configuration to the Treeview.
        """
        if not self.config_window:
            return
        dialog = self._open_single_dialog(AddPairDialog, self)

        # Check if the window was closed while the dialog was open
        if not self.config_window or not self.config_window.winfo_exists():
            return

        if dialog.result:
            pair_values = dialog.result
            # Allow multiple entries for same pair with different settings
            if self.pairs_treeview:
                self.pairs_treeview.insert('', 'end', values=pair_values)
            self.update_status(f"Pair {pair_values[1]} added successfully.", 'lightgreen')

    def remove_pair_config(self) -> None:
        """
        Removes the selected pair configuration from the Treeview.
        """
        if not self.pairs_treeview:
            return
        selected = self.pairs_treeview.selection()  # type: ignore
        if selected:
            self.pairs_treeview.delete(selected)  # type: ignore

    def edit_pair_config(self) -> None:
        """
        Opens a dialog to edit the selected pair configuration in the Treeview.
        """
        if not self.pairs_treeview:
            return
        selected = self.pairs_treeview.selection()  # type: ignore
        if selected:
            values = self.pairs_treeview.item(selected, 'values')  # type: ignore
            dialog = self._open_single_dialog(PairConfigDialog, values, self)

            # Check if the window was closed while the dialog was open
            if not self.config_window or not self.config_window.winfo_exists():
                return

            if dialog.result:
                # Validate the result before updating
                try:
                    name, enabled, pair, var_tol, sell_offset, usd_amt, spread = dialog.result
                    # Ensure all numeric values are properly formatted
                    float(var_tol)
                    float(sell_offset)
                    float(usd_amt)
                    float(spread)

                    self.pairs_treeview.item(selected, values=dialog.result)
                    self.update_status(f"Pair {pair} updated successfully.", 'lightgreen')
                except ValueError as e:
                    self.update_status(f"Invalid values: {str(e)}", 'red')
                except Exception as e:
                    self.update_status(f"Error updating pair: {str(e)}", 'red')
            else:
                self.update_status("Edit cancelled.", 'lightgray')

    def save_config(self) -> None:
        """
        Saves the current configuration from the GUI to the YAML file.
        """
        config_file_path = './config/config_pingpong.yaml'
        yaml = YAML()
        yaml.default_flow_style = False
        yaml.indent(mapping=2, sequence=4, offset=2)

        # Build new pair configs from the treeview
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
                except ValueError as e:
                    self.update_status(f"Invalid numeric value in pair config: {e}", 'red')
                    self.parent.config_manager.general_log.error(f"Failed to parse pair config: {e}")
                    return

        new_config = {
            'debug_level': int(self.debug_level_entry.get()) if self.debug_level_entry else 0,
            'ttk_theme': self.ttk_theme_entry.get() if self.ttk_theme_entry else 'flatly',
            'pair_configs': pair_configs
        }

        try:
            with open(config_file_path, 'r') as file:
                existing_config = yaml.load(file)
            if existing_config == new_config:
                self.update_status("Configuration is already up to date.", 'lightgray')
                return

            with open(config_file_path, 'w') as file:
                yaml.dump(new_config, file)
            self.update_status("Configuration saved successfully.", 'lightgreen')
            self.parent.reload_configuration(loadxbridgeconf=True)
        except Exception as e:
            self.update_status(f"Failed to save configuration: {e}", 'lightcoral')
            self.parent.config_manager.general_log.error(f"Failed to save config: {e}")

    def update_status(self, message: str, color: str = 'black') -> None:
        """
        Updates the status bar message in the configuration window.
        """
        if self.status_label:
            self.status_var.set(message)
            self.status_label.config(foreground=color)


class AddPairDialog(tk.Toplevel):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.title("Add New Pair")
        self.result = None
        self.config = config

        self.enabled_var = tk.BooleanVar(value=True)
        self.name_var = tk.StringVar()
        self.pair_var = tk.StringVar()
        self.var_tol_var = tk.StringVar(value="0.02")
        self.sell_offset_var = tk.StringVar(value="0.05")
        self.usd_amt_var = tk.StringVar(value="0.5")
        self.spread_var = tk.StringVar(value="0.1")

        ttk.Checkbutton(self, text="Enabled", variable=self.enabled_var).grid(row=0, column=0, padx=5, pady=2,
                                                                              sticky='w')
        ttk.Label(self, text="Name:").grid(row=1, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.name_var).grid(row=1, column=1, padx=5, pady=2)
        ttk.Label(self, text="Pair:").grid(row=2, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.pair_var).grid(row=2, column=1, padx=5, pady=2)
        ttk.Label(self, text="Price Variation Tolerance:").grid(row=3, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.var_tol_var).grid(row=3, column=1, padx=5, pady=2)
        ttk.Label(self, text="Sell Price Offset:").grid(row=4, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.sell_offset_var).grid(row=4, column=1, padx=5, pady=2)
        ttk.Label(self, text="USD Amount:").grid(row=5, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.usd_amt_var).grid(row=5, column=1, padx=5, pady=2)
        ttk.Label(self, text="Spread:").grid(row=6, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.spread_var).grid(row=6, column=1, padx=5, pady=2)

        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=7, column=0, columnspan=2, pady=5)
        ttk.Button(btn_frame, text="Add", command=self.on_add).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side='left', padx=5)

        self.bind('<Return>', lambda event: self.on_add())
        self.bind('<Escape>', lambda event: self.destroy())

    def on_add(self):
        pair = self.pair_var.get().strip().upper()
        if not re.match(r"^[A-Z]{2,}/[A-Z]{2,}$", pair):
            self.config.update_status("Invalid pair format. Must be TOKEN1/TOKEN2 (both tokens 2+ chars)", 'red')
            return

        try:
            # Validate numeric fields
            float(self.var_tol_var.get())
            float(self.sell_offset_var.get())
            float(self.usd_amt_var.get())
            float(self.spread_var.get())

            self.result = (
                self.name_var.get(),
                'Yes' if self.enabled_var.get() else 'No',
                pair,
                float(self.var_tol_var.get()),
                float(self.sell_offset_var.get()),
                float(self.usd_amt_var.get()),
                float(self.spread_var.get())
            )
            self.destroy()
        except ValueError as e:
            self.config.update_status(f"Invalid numeric value: {str(e)}", 'red')


class PairConfigDialog(tk.Toplevel):
    def __init__(self, parent: tk.Toplevel, values: tuple, config: GUI_Config) -> None:
        super().__init__(parent)
        self.title("Edit Pair Configuration")
        self.result = None
        self.config = config

        self.enabled_var = tk.BooleanVar(value=values[1] == 'Yes')
        self.name_var = tk.StringVar(value=values[0])
        self.pair_var = tk.StringVar(value=values[2])
        self.var_tol_var = tk.StringVar(value=values[3])
        self.sell_offset_var = tk.StringVar(value=values[4])
        self.usd_amt_var = tk.StringVar(value=values[5])
        self.spread_var = tk.StringVar(value=values[6])

        ttk.Checkbutton(self, text="Enabled", variable=self.enabled_var).grid(row=0, column=0, padx=5, pady=2,
                                                                              sticky='w')
        ttk.Label(self, text="Name:").grid(row=1, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.name_var).grid(row=1, column=1, padx=5, pady=2)
        ttk.Label(self, text="Pair:").grid(row=2, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.pair_var, state='readonly').grid(row=2, column=1, padx=5, pady=2)
        ttk.Label(self, text="Price Variation Tolerance:").grid(row=3, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.var_tol_var).grid(row=3, column=1, padx=5, pady=2)
        ttk.Label(self, text="Sell Price Offset:").grid(row=4, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.sell_offset_var).grid(row=4, column=1, padx=5, pady=2)
        ttk.Label(self, text="USD Amount:").grid(row=5, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.usd_amt_var).grid(row=5, column=1, padx=5, pady=2)
        ttk.Label(self, text="Spread:").grid(row=6, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.spread_var).grid(row=6, column=1, padx=5, pady=2)

        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=7, column=0, columnspan=2, pady=5)
        ttk.Button(btn_frame, text="Save", command=self.on_save).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side='left', padx=5)

        self.bind('<Return>', lambda event: self.on_save())
        self.bind('<Escape>', lambda event: self.destroy())

    def on_save(self) -> None:
        """
        Handles saving the edited pair configuration."""
        try:
            # Get and validate all values first
            pair = self.pair_var.get().strip().upper()
            var_tol = float(self.var_tol_var.get())
            sell_offset = float(self.sell_offset_var.get())
            usd_amt = float(self.usd_amt_var.get())
            spread = float(self.spread_var.get())

            self.result = (
                self.name_var.get(),
                'Yes' if self.enabled_var.get() else 'No',
                pair,
                var_tol,
                sell_offset,
                usd_amt,
                spread
            )
            self.destroy()
        except ValueError as e:
            self.config.update_status(f"Invalid numeric value: {str(e)}", 'red')
            self.config.parent.config_manager.general_log.error(f"Invalid numeric value in PairConfigDialog: {e}")
            self.result = None  # Prevent saving invalid values


class GUI_Main:
    """Main GUI application class for the PingPong bot."""

    def __init__(self):
        self.config_manager = None
        self.initialize(loadxbridgeconf=True)

        self.config_window = None
        self.root = tk.Tk()
        self.root.title("PingPong")
        self.root.resizable(width=False, height=False)
        self.send_process = None
        self.started = False

        self.style = Style(self.config_manager.config_pp.ttk_theme)
        self.status_var = tk.StringVar(value="Idle")

        self.gui_orders = GUI_Orders(self)
        self.gui_balances = GUI_Balances(self)
        self.create_widgets()
        self.gui_config = GUI_Config(self)
        self.refresh_gui()

    def create_widgets(self) -> None:
        """
        Creates all the main widgets for the GUI.
        """
        self.create_buttons()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()
        self.create_status_bar()

    def create_buttons(self) -> None:
        """
        Creates the main control buttons (START, STOP, CANCEL ALL, CONFIGURE).
        """
        button_frame = ttk.Frame(self.root)
        button_frame.grid(column=0, row=0, padx=5, pady=5, sticky='ew')
        btn_width = 12
        self.btn_start = ttk.Button(button_frame, text="START", command=self.start, width=btn_width)
        self.btn_start.grid(column=0, row=0, padx=5, pady=5)
        self.btn_stop = ttk.Button(button_frame, text="STOP", command=self.stop, width=btn_width)
        self.btn_stop.grid(column=1, row=0, padx=5, pady=5)
        self.btn_stop.state(["disabled"])
        self.btn_cancel_all = ttk.Button(button_frame, text="CANCEL ALL", command=self.cancel_all, width=btn_width)
        self.btn_cancel_all.grid(column=2, row=0, padx=5, pady=5)
        self.btn_configure = ttk.Button(button_frame, text="CONFIGURE", command=self.open_configure_window,
                                        width=btn_width)
        self.btn_configure.grid(column=3, row=0, padx=5, pady=5)

    def create_status_bar(self) -> None:
        """
        Creates the status bar at the bottom of the main window.
        """
        status_frame = ttk.Frame(self.root)
        status_frame.grid(row=3, column=0, columnspan=4, padx=5, pady=5, sticky='ew')
        status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        status_label.grid(row=0, column=0, padx=5, pady=5, sticky='ew')

    def initialize(self, loadxbridgeconf: bool = True) -> None:
        """
        Initializes the configuration manager.
        """
        self.config_manager = ConfigManager(strategy="pingpong")
        self.config_manager.initialize(loadxbridgeconf=loadxbridgeconf)

    def start(self) -> None:
        """
        Starts the PingPong bot in a separate thread.
        """
        import main_pingpong  # Import here to avoid circular dependency at startup
        self.status_var.set("Bot is running...")
        self.send_process = threading.Thread(target=main_pingpong.run_async_main,
                                             args=(self.config_manager,),
                                             daemon=True)
        try:
            self.send_process.start()
            self.started = True
            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="active")
            self.btn_configure.config(state="disabled")
            self.config_manager.general_log.info("Bot started successfully.")
        except Exception as e:
            self.status_var.set(f"Error starting bot: {e}")
            self.config_manager.general_log.error(f"Error starting bot thread: {e}")
            self.stop(reload_config=False)  # Attempt to clean up

    def stop(self, reload_config: bool = True) -> None:
        """
        Stops the PingPong bot and performs cleanup.
        :param reload_config: Whether to reload configuration after stopping.
        """
        self.status_var.set("Stopping bot...")
        self.config_manager.general_log.info("Attempting to stop bot...")

        if self.config_manager.controller:
            self.config_manager.controller.stop_order = True

        if self.send_process:
            self.send_process.join(timeout=5)  # Wait up to 5 seconds for thread to finish
            if self.send_process.is_alive():
                self.config_manager.general_log.warning("Bot thread did not terminate gracefully within timeout.")
                self.status_var.set("Bot stopped (thread timeout).")
            else:
                self.status_var.set("Bot stopped.")
                self.config_manager.general_log.info("Bot stopped successfully.")
        else:
            self.status_var.set("Bot not running.")
            self.config_manager.general_log.info("Stop requested, but bot was not running.")

        # Always attempt to cancel all orders and reset GUI state
        self.cancel_all()  # Ensure all orders are cancelled regardless of thread state
        self.started = False
        self.btn_stop.config(state="disabled")
        self.btn_start.config(state="active")
        self.btn_configure.config(state="active")

        if reload_config:
            self.reload_configuration(loadxbridgeconf=False)

    def cancel_all(self) -> None:
        """
        Cancels all open orders on the exchange.
        """
        self.status_var.set("Cancelling all open orders...")
        # The bot must be running to have a controller and a running event loop
        if self.started and self.config_manager.controller and self.config_manager.controller.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self.config_manager.xbridge_manager.cancelallorders(),
                self.config_manager.controller.loop
            )
            try:
                future.result(timeout=15)  # Wait for up to 15 seconds
                self.status_var.set("Cancelled all open orders.")
                self.config_manager.general_log.info("cancel_all: All orders cancelled successfully.")
            except Exception as e:
                self.status_var.set(f"Error cancelling orders: {e}")
                self.config_manager.general_log.error(f"Error during cancel_all: {e}")
        else:
            self.config_manager.general_log.info("cancel_all: Bot not running, using new event loop.")
            try:
                asyncio.run(self.config_manager.xbridge_manager.cancelallorders())
                self.status_var.set("Cancelled all open orders.")
                self.config_manager.general_log.info("cancel_all: All orders cancelled successfully.")
            except Exception as e:
                self.status_var.set(f"Error cancelling orders: {e}")
                self.config_manager.general_log.error(f"Error during cancel_all with new loop: {e}")

    def refresh_gui(self) -> None:
        """
        Refreshes the GUI display periodically. Checks bot thread status.
        """
        if self.started:
            if self.send_process and not self.send_process.is_alive():
                self.config_manager.general_log.error("pingpong bot crashed!")
                self.status_var.set("pingpong bot crashed!")
                self.stop(reload_config=False)
                self.cancel_all()
        self.gui_orders.update_order_display()
        self.gui_balances.update_balance_display()
        if self.root:
            self.root.after(1500, self.refresh_gui)

    def open_configure_window(self) -> None:
        """
        Opens the configuration window.
        """
        self.gui_config.open()

    @staticmethod
    def get_flag(status: str) -> str:
        """
        Returns a flag ('V' or 'X') based on the order status.
        'V' for active/open statuses, 'X' otherwise.
        """
        return 'V' if status in {
            'open', 'new', 'created', 'accepting', 'hold', 'initialized', 'committed', 'finished'
        } else 'X'

    def on_closing(self, reload_config: bool = False) -> None:
        """
        Handles the application closing event. Stops the bot and destroys the GUI.
        :param reload_config: Whether to reload configuration during stop.
        """
        # Perform any necessary cleanup before closing the app
        self.config_manager.general_log.info("Closing application...")
        self.stop(reload_config=reload_config)
        if self.root:
            self.root.destroy()

    def reload_configuration(self, loadxbridgeconf: bool = True) -> None:
        """
        Reloads the bot's configuration and refreshes the GUI display.
        :param loadxbridgeconf: Whether to load xbridge specific configuration.
        """
        self.config_manager.load_configs()
        self.config_manager.initialize(loadxbridgeconf=loadxbridgeconf)
        self.gui_orders.purge_treeview()
        self.gui_balances.purge_treeview()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()
