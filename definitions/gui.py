import ctypes
import inspect
import logging
import re
import threading
import time
import tkinter as tk
from tkinter import ttk

from ruamel.yaml import YAML
from ttkbootstrap import Style

import main_pingpong
from definitions import bot_init

logger = logging.getLogger()

TOTAL_WIDTH = 500


def _async_raise(tid, exctype):
    if not inspect.isclass(exctype):
        raise TypeError("Only types can be raised (not instances)")
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(tid), ctypes.py_object(exctype))
    if res == 0:
        raise ValueError("invalid thread id")
    elif res != 1:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, 0)
        raise SystemError("PyThreadState_SetAsyncExc failed")


class ThreadWithExc(threading.Thread):
    def _get_my_tid(self):
        if not self.is_alive():
            raise threading.ThreadError("the thread is not active")
        if hasattr(self, "_thread_id"):
            return self._thread_id
        for tid, tobj in threading._active.items():
            if tobj is self:
                self._thread_id = tid
                return tid
        raise AssertionError("could not determine the thread's id")

    def raise_exc(self, exctype):
        _async_raise(self._get_my_tid(), exctype)

    def terminate(self):
        self.raise_exc(SystemExit)


class GUI_Orders:
    def __init__(self, parent):
        self.parent = parent
        self.sortedpairs = None
        self.orders_frame = None
        self.orders_treeview = None

    def create_orders_treeview(self):
        # Get enabled pairs from config
        self.sortedpairs = sorted(
            [cfg['name'] for cfg in bot_init.context.config_pp.pair_configs if cfg.get('enabled', True)]
        )
        columns = ("Pair", "Status", "Side", "Flag", "Variation")
        self.orders_frame = ttk.LabelFrame(self.parent.root, text="Orders")
        self.orders_frame.grid(row=1, padx=5, pady=5, sticky='ew', columnspan=4)

        height = len(self.sortedpairs) + 1
        self.orders_treeview = ttk.Treeview(
            self.orders_frame,
            columns=columns,
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
            if label_text == "Pair":
                anchor = "w"
                width = int(unit_width * pair_weight)
            else:
                anchor = "center" if label_text != "Variation" else "e"
                width = int(unit_width * other_weight)

            self.orders_treeview.heading(label_text, text=label_text, anchor=anchor)
            self.orders_treeview.column(label_text, width=width, anchor=anchor)

        for pair in self.sortedpairs:
            self.orders_treeview.insert("", tk.END, values=[pair, "None", "None", "X", "None"])

    def update_order_display(self):
        if self.parent.started:
            for key, pair in bot_init.context.p.items():
                for item_id in self.orders_treeview.get_children():
                    values = self.orders_treeview.item(item_id, 'values')

                    display_text = pair.cfg['name']
                    if values[0] == display_text:
                        new_values = [
                            display_text,
                            pair.dex.order.get('status',
                                               'None') if self.parent.started and pair.dex.order and 'status' in pair.dex.order else 'Disabled' if pair.dex.disabled else 'None',
                            pair.dex.current_order.get('side',
                                                       'None') if self.parent.started and pair.dex.order and 'status' in pair.dex.order else 'None',
                            self.parent.get_flag(pair.dex.order.get('status',
                                                                    'None') if self.parent.started and pair.dex.order and 'status' in pair.dex.order else 'None'),
                            str(pair.dex.variation) if self.parent.started and pair.dex.order and 'status' in pair.dex.order else 'None'
                        ]
                        if list(new_values) != list(values):
                            self.orders_treeview.item(item_id, values=new_values)

    def purge_treeview(self):
        # Destroy existing orders and balances frames within their respective classes.
        if self.orders_frame:
            for widget in self.orders_frame.winfo_children():
                widget.destroy()
            self.orders_frame.destroy()


class GUI_Balances:
    def __init__(self, parent):
        self.parent = parent
        self.balances_frame = None
        self.balances_treeview = None

    def create_balances_treeview(self):
        columns = ("Coin", "USD ticker", "Total", "Free", "Total USD")
        self.balances_frame = ttk.LabelFrame(self.parent.root, text="Balances")
        self.balances_frame.grid(row=2, padx=5, pady=5, sticky='ew', columnspan=4)

        height = len(bot_init.context.t.keys())
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

        for token in bot_init.context.t:
            data = (token, str(None), str(None), str(None), str(None))
            self.balances_treeview.insert("", tk.END, values=data)

    def update_balance_display(self):
        for item_id in self.balances_treeview.get_children():
            values = self.balances_treeview.item(item_id, 'values')
            token = values[0]
            usd_price = bot_init.context.t[token].cex.usd_price
            dex_total_balance = bot_init.context.t[token].dex.total_balance
            dex_free_balance = bot_init.context.t[token].dex.free_balance

            new_values = [
                token,
                f"{usd_price:.3f}$" if usd_price else f"{0:.3f}$",
                f"{dex_total_balance:.4f}" if dex_total_balance else f"{0:.4f}",
                f"{dex_free_balance:.4f}" if dex_free_balance else f"{0:.4f}",
                f"{usd_price * dex_total_balance:.3f}$" if usd_price and dex_total_balance else f"{0:.3f}$"
            ]

            if list(new_values) != list(values):
                self.balances_treeview.item(item_id, values=new_values)

    def purge_treeview(self):
        if self.balances_frame:
            for widget in self.balances_frame.winfo_children():
                widget.destroy()
            self.balances_frame.destroy()


class GUI_Config:
    def __init__(self, parent):
        self.parent = parent
        self.config_window = None
        self.debug_level_entry = None
        self.ttk_theme_entry = None
        self.user_pairs_listbox = None
        self.price_variation_entry = None
        self.sell_price_offset_entry = None
        self.usd_amount_default_entry = None
        self.spread_default_entry = None

    def open(self):
        if self.config_window:
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

        # scrollbar = ttk.Scrollbar(main_frame, orient='vertical', command=canvas.yview)
        # scrollbar.pack(side='right', fill='y')

        content_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=content_frame, anchor='nw')

        self.config_window.bind("<Up>", lambda event: canvas.yview_scroll(-1, "units"))
        self.config_window.bind("<Down>", lambda event: canvas.yview_scroll(1, "units"))
        self.config_window.bind("<Prior>", lambda event: canvas.yview_scroll(-10, "units"))  # Page Up
        self.config_window.bind("<Next>", lambda event: canvas.yview_scroll(10, "units"))  # Page Down

        def mouse_scroll(event):
            delta = event.delta if event.delta != 0 else event.widget.winfo_pointery()
            if delta > 0:
                canvas.yview_scroll(-1, "units")
            else:
                canvas.yview_scroll(1, "units")

        self.config_window.bind("<MouseWheel>", lambda event: mouse_scroll(event))

        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        content_frame.bind("<Configure>", on_frame_configure)

        content_frame.grid_columnconfigure(1, weight=1)
        content_frame.grid_rowconfigure(20, weight=1)

        ttk.Label(content_frame, text="Debug Level:").grid(row=0, column=0, padx=5, pady=5, sticky='w')
        self.debug_level_entry = ttk.Entry(content_frame)
        self.debug_level_entry.grid(row=0, column=1, padx=5, pady=5, sticky='ew')
        self.debug_level_entry.insert(0, bot_init.context.config_pp.get('debug_level', ''))

        ttk.Label(content_frame, text="TTK Theme:").grid(row=1, column=0, padx=5, pady=5, sticky='w')
        self.ttk_theme_entry = ttk.Entry(content_frame)
        self.ttk_theme_entry.grid(row=1, column=1, padx=5, pady=5, sticky='ew')
        self.ttk_theme_entry.insert(0, bot_init.context.config_pp.get('ttk_theme', ''))

        # Pair configurations table
        ttk.Label(content_frame, text="Pair Configurations:").grid(row=2, column=0, columnspan=2, padx=5, pady=5,
                                                                   sticky='w')

        # Create the Treeview with scrollbar
        tree_frame = ttk.Frame(content_frame)
        tree_frame.grid(row=3, column=0, columnspan=2, padx=5, pady=5, sticky='nsew')

        self.pairs_treeview = ttk.Treeview(tree_frame, columns=(
            'name', 'enabled', 'pair', 'price_variation_tolerance',
            'sell_price_offset', 'usd_amount', 'spread'), show='headings', height=8)

        # Define headings
        self.pairs_treeview.heading('name', text='Name')
        self.pairs_treeview.heading('enabled', text='Enabled')
        self.pairs_treeview.heading('pair', text='Pair')
        self.pairs_treeview.heading('price_variation_tolerance', text='Var. Tol.')
        self.pairs_treeview.heading('sell_price_offset', text='Sell Offset')
        self.pairs_treeview.heading('usd_amount', text='USD Amt')
        self.pairs_treeview.heading('spread', text='Spread')

        # Set column widths and alignment
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

        # Populate with existing configs
        for cfg in bot_init.context.config_pp.pair_configs:
            self.pairs_treeview.insert('', 'end', values=(
                cfg.get('name', ''),
                'Yes' if cfg.get('enabled', True) else 'No',
                cfg['pair'],
                cfg.get('price_variation_tolerance', 0.02),
                cfg.get('sell_price_offset', 0.05),
                cfg.get('usd_amount', 0.5),
                cfg.get('spread', 0.1)
            ))

        # Control buttons frame
        btn_frame = ttk.Frame(content_frame)
        btn_frame.grid(row=4, column=0, columnspan=2, padx=5, pady=5, sticky='ew')

        ttk.Button(btn_frame, text="Add Pair", command=self.add_pair_config).pack(side='left', padx=2)
        ttk.Button(btn_frame, text="Remove Pair", command=self.remove_pair_config).pack(side='left', padx=2)
        ttk.Button(btn_frame, text="Edit Config", command=self.edit_pair_config).pack(side='left', padx=2)

        save_button = ttk.Button(content_frame, text="Save", command=self.save_config)
        save_button.grid(row=20, column=0, columnspan=2, pady=10, sticky='ew')

        status_frame = ttk.Frame(content_frame)
        status_frame.grid(row=21, column=0, columnspan=2, pady=5, sticky='ew')
        self.status_var = tk.StringVar()
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        self.status_label.pack(fill='x')

        # Configure window size and position
        x = 900
        y = 450
        self.config_window.minsize(x, y)
        self.config_window.geometry(f"{x}x{y}")
        self.config_window.update_idletasks()

        # Add status bar at bottom
        self.status_frame = ttk.Frame(self.config_window)
        self.status_frame.pack(side='bottom', fill='x', padx=5, pady=5)

        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(
            self.status_frame,
            textvariable=self.status_var,
            anchor='w',
            padding=(5, 2),
            relief='sunken',
            font=('Helvetica', 10)
        )
        self.status_label.pack(fill='x')

    def on_close(self):
        self.parent.btn_start.config(state="active")
        self.parent.btn_configure.config(state="active")
        self.config_window.destroy()
        self.config_window = None

    def is_valid_pair(self, pair_symbol):
        return bool(re.match(r"^[A-Z]+/[A-Z]+$", pair_symbol))

    def add_pair_config(self):
        dialog = AddPairDialog(self.config_window, self)
        self.config_window.wait_window(dialog)  # Wait for dialog to close

        if dialog.result:
            pair_values = dialog.result
            # Allow multiple entries for same pair with different settings
            self.pairs_treeview.insert('', 'end', values=pair_values)
            self.update_status(f"Pair {pair_values[1]} added successfully.", 'lightgreen')

    def remove_pair_config(self):
        selected = self.pairs_treeview.selection()
        if selected:
            self.pairs_treeview.delete(selected)

    def edit_pair_config(self):
        selected = self.pairs_treeview.selection()
        if selected:
            values = self.pairs_treeview.item(selected, 'values')
            dialog = PairConfigDialog(self.config_window, values, self)
            self.config_window.wait_window(dialog)  # Wait for dialog to close

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
                self.update_status("Edit canceled.", 'lightgray')

    def save_config(self):
        config_file_path = './config/config_pingpong.yaml'
        yaml = YAML()
        yaml.default_flow_style = False
        yaml.indent(mapping=2, sequence=4, offset=2)

        # Build new pair configs from the treeview
        pair_configs = []
        for item_id in self.pairs_treeview.get_children():
            values = self.pairs_treeview.item(item_id, 'values')
            pair_configs.append({
                'name': values[0],
                'enabled': values[1] == 'Yes',
                'pair': values[2],
                'price_variation_tolerance': float(values[3]),
                'sell_price_offset': float(values[4]),
                'usd_amount': float(values[5]),
                'spread': float(values[6])
            })

        new_config = {
            'debug_level': int(self.debug_level_entry.get()),
            'ttk_theme': self.ttk_theme_entry.get(),
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
            self.parent.reload_configuration()

        except Exception as e:
            self.update_status(f"Failed to save configuration: {e}", 'lightcoral')

    def add_pair(self, new_pair):
        new_pair = new_pair.strip()
        if self.is_valid_pair(new_pair):
            if new_pair not in self.user_pairs_listbox.get(0, tk.END):
                self.user_pairs_listbox.insert(tk.END, new_pair)
                self.update_status("Pair added.", 'lightgreen')
            else:
                self.update_status("Pair already exists.", 'red')
        else:
            self.update_status("Invalid pair format. Must be TOKEN1/TOKEN2.", 'red')

    def remove_pair(self):
        selected = self.user_pairs_listbox.curselection()
        if selected:
            self.user_pairs_listbox.delete(selected[0])
        else:
            self.update_status("No pair selected.", 'red')

    def update_status(self, message, color='black'):
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
    def __init__(self, parent, values, config):
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

    def on_save(self):
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
            print('test')
            self.result = None  # Prevent saving invalid values


class GUI_Main:
    def __init__(self):
        self.config_window = None
        self.root = tk.Tk()
        self.root.title("PingPong")
        self.root.resizable(width=False, height=False)
        self.send_process = None
        self.started = False
        self.initialize()

        self.style = Style(bot_init.context.config_pp.ttk_theme)
        self.status_var = tk.StringVar(value="Idle")

        self.gui_orders = GUI_Orders(self)
        self.gui_balances = GUI_Balances(self)
        self.create_widgets()
        self.gui_config = GUI_Config(self)
        self.refresh_gui()

    def create_widgets(self):
        self.create_buttons()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()
        self.create_status_bar()

    def create_buttons(self):
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

    def create_status_bar(self):
        status_frame = ttk.Frame(self.root)
        status_frame.grid(row=3, column=0, columnspan=4, padx=5, pady=5, sticky='ew')
        status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        status_label.grid(row=0, column=0, padx=5, pady=5, sticky='ew')

    def initialize(self, loadxbridgeconf=True):
        bot_init.initialize(strategy="pingpong", loadxbridgeconf=loadxbridgeconf)

    def start(self):
        self.status_var.set("Bot is running...")
        self.send_process = ThreadWithExc(target=main_pingpong.run_async_main)
        self.send_process.start()
        self.started = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="active")
        self.btn_configure.config(state="disabled")
        print("start done")

    def stop(self):
        if self.send_process and self.send_process.is_alive():
            self.cancel_all()
            self.status_var.set("Stopping bot...")
            self.send_process.terminate()
            print("Stopping bot...")
            while self.send_process.is_alive():
                print("Wait for process end...")
                time.sleep(1)
        self.started = False
        self.btn_stop.config(state="disabled")
        self.btn_start.config(state="active")
        self.btn_configure.config(state="active")
        self.reload_configuration(loadxbridgeconf=False)
        self.status_var.set("Bot stopped.")
        print("Bot stopped")

    def cancel_all(self):
        import definitions.xbridge_def as xb
        self.status_var.set("Cancelling all orders...")
        xb.cancelallorders()
        print("Cancel All orders done")
        self.status_var.set("All orders cancelled.")

    def refresh_gui(self):
        if self.started:
            if not self.send_process.is_alive():
                print("pingpong bot crashed!")
                self.status_var.set("pingpong bot crashed!")
                self.stop()
                self.cancel_all()
        self.gui_orders.update_order_display()
        self.gui_balances.update_balance_display()
        self.root.after(1500, self.refresh_gui)

    def open_configure_window(self):
        self.gui_config.open()

    def get_flag(self, status):
        return 'V' if status in {
            'open', 'new', 'created', 'accepting', 'hold', 'initialized', 'committed', 'finished'
        } else 'X'

    def on_closing(self):
        # Perform any necessary cleanup before closing the app
        print("Closing application...")
        self.stop()
        self.root.destroy()

    def reload_configuration(self, loadxbridgeconf=True):
        bot_init.initialize(strategy="pingpong", loadxbridgeconf=loadxbridgeconf)
        self.gui_orders.purge_treeview()
        self.gui_balances.purge_treeview()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()
