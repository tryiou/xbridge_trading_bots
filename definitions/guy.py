import ctypes
import inspect

import re
import threading
import time
import tkinter as tk
from tkinter import ttk

from ruamel.yaml import YAML
from ttkbootstrap import Style

import main_pingpong
from definitions import init


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
        self.sortedpairs = sorted(init.config_pp.user_pairs)
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

        for label_text in columns:
            self.orders_treeview.heading(label_text, text=label_text, anchor="w")
            self.orders_treeview.column(label_text, width=100, anchor="w")

        for pair in self.sortedpairs:
            self.orders_treeview.insert("", tk.END, values=[pair, "None", "None", "X", "None"])

    def update_order_display(self):
        if self.parent.started:
            for key, pair in init.p.items():
                for item_id in self.orders_treeview.get_children():
                    values = self.orders_treeview.item(item_id, 'values')
                    if values[0] == key:
                        new_values = [
                            pair.symbol,
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

        height = len(init.t.keys())
        self.balances_treeview = ttk.Treeview(self.balances_frame, columns=columns, show="headings",
                                              height=height, selectmode="none")
        self.balances_treeview.grid(padx=5, pady=5)

        for col in columns:
            self.balances_treeview.heading(col, text=col, anchor="w")
            self.balances_treeview.column(col, width=100)

        for token in init.t:
            data = (token, str(None), str(None), str(None), str(None))
            self.balances_treeview.insert("", tk.END, values=data)

    def update_balance_display(self):
        for item_id in self.balances_treeview.get_children():
            values = self.balances_treeview.item(item_id, 'values')
            token = values[0]
            usd_price = init.t[token].cex.usd_price
            dex_total_balance = init.t[token].dex.total_balance
            dex_free_balance = init.t[token].dex.free_balance

            new_values = [
                token,
                f"{usd_price:.2f}$" if usd_price else "0.00$",
                f"{dex_total_balance:.4f}" if dex_total_balance else "0.00",
                f"{dex_free_balance:.4f}" if dex_free_balance else "0.00",
                f"{usd_price * dex_total_balance:.2f}$" if usd_price and dex_total_balance else "0.00$"
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

        scrollbar = ttk.Scrollbar(main_frame, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y')

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
        self.debug_level_entry.insert(0, init.config_pp.get('debug_level', ''))

        ttk.Label(content_frame, text="TTK Theme:").grid(row=1, column=0, padx=5, pady=5, sticky='w')
        self.ttk_theme_entry = ttk.Entry(content_frame)
        self.ttk_theme_entry.grid(row=1, column=1, padx=5, pady=5, sticky='ew')
        self.ttk_theme_entry.insert(0, init.config_pp.get('ttk_theme', ''))

        ttk.Label(content_frame, text="User Pairs:").grid(row=2, column=0, padx=5, pady=5, sticky='w')
        user_pairs_frame = ttk.Frame(content_frame)
        user_pairs_frame.grid(row=2, column=1, padx=5, pady=5, sticky='nsew')

        self.user_pairs_listbox = tk.Listbox(user_pairs_frame, selectmode=tk.SINGLE, height=4, width=30)
        self.user_pairs_listbox.grid(row=0, column=0, rowspan=2, padx=5, pady=5, sticky='ns')

        user_pairs = init.config_pp.get('user_pairs', [])
        for pair in user_pairs:
            self.user_pairs_listbox.insert(tk.END, pair)

        new_pair_label = ttk.Label(content_frame, text="New Pair:")
        new_pair_label.grid(row=3, column=0, padx=5, pady=5, sticky='w')

        new_pair_entry = ttk.Entry(content_frame)
        new_pair_entry.grid(row=3, column=1, padx=5, pady=5, sticky='ew')

        add_pair_button = ttk.Button(content_frame, text="Add",
                                     command=lambda: self.add_pair(new_pair_entry.get().strip()))
        add_pair_button.grid(row=4, column=1, padx=5, pady=5, sticky='ew')

        remove_pair_button = ttk.Button(content_frame, text="Remove", command=self.remove_pair)
        remove_pair_button.grid(row=5, column=1, padx=5, pady=5, sticky='ew')

        ttk.Label(content_frame, text="Price Variation Tolerance:").grid(row=6, column=0, padx=5, pady=5, sticky='w')
        self.price_variation_entry = ttk.Entry(content_frame)
        self.price_variation_entry.grid(row=6, column=1, padx=5, pady=5, sticky='ew')
        self.price_variation_entry.insert(0, init.config_pp.get('price_variation_tolerance', ''))

        ttk.Label(content_frame, text="Sell Price Offset:").grid(row=7, column=0, padx=5, pady=5, sticky='w')
        self.sell_price_offset_entry = ttk.Entry(content_frame)
        self.sell_price_offset_entry.grid(row=7, column=1, padx=5, pady=5, sticky='ew')
        self.sell_price_offset_entry.insert(0, init.config_pp.get('sell_price_offset', ''))

        ttk.Label(content_frame, text="USD Amount Default:").grid(row=8, column=0, padx=5, pady=5, sticky='w')
        self.usd_amount_default_entry = ttk.Entry(content_frame)
        self.usd_amount_default_entry.grid(row=8, column=1, padx=5, pady=5, sticky='ew')
        self.usd_amount_default_entry.insert(0, init.config_pp.get('usd_amount_default', ''))

        ttk.Label(content_frame, text="USD Amount Custom:").grid(row=9, column=0, padx=5, pady=5, sticky='w')
        usd_amount_custom_frame = ttk.Frame(content_frame)
        usd_amount_custom_frame.grid(row=9, column=1, padx=5, pady=5, sticky='nsew')

        self.usd_amount_custom_treeview = ttk.Treeview(usd_amount_custom_frame, columns=("Pair", "Amount"),
                                                       show="headings",
                                                       height=4)
        self.usd_amount_custom_treeview.heading("Pair", text="Pair")
        self.usd_amount_custom_treeview.heading("Amount", text="Amount")
        self.usd_amount_custom_treeview.pack(side='left', fill='both', expand=True)

        usd_amount_custom_scroll = ttk.Scrollbar(usd_amount_custom_frame, orient='vertical',
                                                 command=self.usd_amount_custom_treeview.yview)
        usd_amount_custom_scroll.pack(side='right', fill='y')
        self.usd_amount_custom_treeview.configure(yscrollcommand=usd_amount_custom_scroll.set)

        ttk.Label(content_frame, text="Pair:").grid(row=10, column=0, padx=5, pady=5, sticky='w')
        usd_amount_custom_pair_entry = ttk.Entry(content_frame)
        usd_amount_custom_pair_entry.grid(row=10, column=1, padx=5, pady=5, sticky='ew')

        ttk.Label(content_frame, text="Amount:").grid(row=11, column=0, padx=5, pady=5, sticky='w')
        usd_amount_custom_amount_entry = ttk.Entry(content_frame)
        usd_amount_custom_amount_entry.grid(row=11, column=1, padx=5, pady=5, sticky='ew')

        add_usd_amount_custom_button = ttk.Button(content_frame, text="Add USD Amount Custom",
                                                  command=lambda: self.add_usd_amount_custom(
                                                      usd_amount_custom_pair_entry.get().strip(),
                                                      usd_amount_custom_amount_entry.get().strip()))
        add_usd_amount_custom_button.grid(row=12, column=1, padx=5, pady=5, sticky='ew')

        remove_usd_amount_custom_button = ttk.Button(content_frame, text="Remove USD Amount Custom",
                                                     command=self.remove_usd_amount_custom)
        remove_usd_amount_custom_button.grid(row=13, column=1, padx=5, pady=5, sticky='ew')

        ttk.Label(content_frame, text="Spread Default:").grid(row=14, column=0, padx=5, pady=5, sticky='w')
        self.spread_default_entry = ttk.Entry(content_frame)
        self.spread_default_entry.grid(row=14, column=1, padx=5, pady=5, sticky='ew')
        self.spread_default_entry.insert(0, init.config_pp.get('spread_default', ''))

        ttk.Label(content_frame, text="Spread Custom:").grid(row=15, column=0, padx=5, pady=5, sticky='w')
        spread_custom_frame = ttk.Frame(content_frame)
        spread_custom_frame.grid(row=15, column=1, padx=5, pady=5, sticky='nsew')

        self.spread_custom_treeview = ttk.Treeview(spread_custom_frame, columns=("Pair", "Spread"), show="headings",
                                                   height=4)
        self.spread_custom_treeview.heading("Pair", text="Pair")
        self.spread_custom_treeview.heading("Spread", text="Spread")
        self.spread_custom_treeview.pack(side='left', fill='both', expand=True)

        spread_custom_scroll = ttk.Scrollbar(spread_custom_frame, orient='vertical',
                                             command=self.spread_custom_treeview.yview)
        spread_custom_scroll.pack(side='right', fill='y')
        self.spread_custom_treeview.configure(yscrollcommand=spread_custom_scroll.set)

        ttk.Label(content_frame, text="Pair:").grid(row=16, column=0, padx=5, pady=5, sticky='w')
        spread_custom_pair_entry = ttk.Entry(content_frame)
        spread_custom_pair_entry.grid(row=16, column=1, padx=5, pady=5, sticky='ew')

        ttk.Label(content_frame, text="Spread:").grid(row=17, column=0, padx=5, pady=5, sticky='w')
        spread_custom_spread_entry = ttk.Entry(content_frame)
        spread_custom_spread_entry.grid(row=17, column=1, padx=5, pady=5, sticky='ew')

        add_spread_custom_button = ttk.Button(content_frame, text="Add Spread Custom",
                                              command=lambda: self.add_spread_custom(
                                                  spread_custom_pair_entry.get().strip(),
                                                  spread_custom_spread_entry.get().strip()))
        add_spread_custom_button.grid(row=18, column=1, padx=5, pady=5, sticky='ew')

        remove_spread_custom_button = ttk.Button(content_frame, text="Remove Spread Custom",
                                                 command=self.remove_spread_custom)
        remove_spread_custom_button.grid(row=19, column=1, padx=5, pady=5, sticky='ew')

        save_button = ttk.Button(content_frame, text="Save", command=self.save_config)
        save_button.grid(row=20, column=0, columnspan=2, pady=10, sticky='ew')

        status_frame = ttk.Frame(content_frame)
        status_frame.grid(row=21, column=0, columnspan=2, pady=5, sticky='ew')
        self.status_var = tk.StringVar()
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        self.status_label.pack(fill='x')

        usd_amount_custom = init.config_pp.get('usd_amount_custom', {})
        for pair, amount in usd_amount_custom.items():
            self.usd_amount_custom_treeview.insert('', 'end', iid=pair, values=(pair, amount))

        spread_custom = init.config_pp.get('spread_custom', {})
        for pair, spread in spread_custom.items():
            self.spread_custom_treeview.insert('', 'end', iid=pair, values=(pair, spread))

        screen_height = self.config_window.winfo_screenheight()
        self.config_window.geometry(f"650x{screen_height - 100}")

        self.config_window.update_idletasks()

    def on_close(self):
        self.parent.btn_start.config(state="active")
        self.parent.btn_configure.config(state="active")
        self.config_window.destroy()
        self.config_window = None

    def is_valid_pair(self, pair_symbol):
        return bool(re.match(r"^[A-Z]+/[A-Z]+$", pair_symbol))

    def save_config(self):
        config_file_path = './config/config_pingpong.yaml'
        yaml = YAML()
        yaml.default_flow_style = False
        yaml.indent(mapping=2, sequence=4, offset=2)

        m_usd_amount_custom = {
            self.usd_amount_custom_treeview.item(item_id, 'values')[0]: float(
                self.usd_amount_custom_treeview.item(item_id, 'values')[1])
            for item_id in self.usd_amount_custom_treeview.get_children()
            if len(self.usd_amount_custom_treeview.item(item_id, 'values')) == 2
        }

        m_spread_custom = {
            self.spread_custom_treeview.item(item_id, 'values')[0]: float(
                self.spread_custom_treeview.item(item_id, 'values')[1])
            for item_id in self.spread_custom_treeview.get_children()
            if len(self.spread_custom_treeview.item(item_id, 'values')) == 2
        }

        new_config = {
            'debug_level': int(self.debug_level_entry.get()),
            'ttk_theme': self.ttk_theme_entry.get(),
            'user_pairs': [self.user_pairs_listbox.get(i) for i in range(self.user_pairs_listbox.size())],
            'price_variation_tolerance': float(self.price_variation_entry.get()),
            'sell_price_offset': float(self.sell_price_offset_entry.get()),
            'usd_amount_default': float(self.usd_amount_default_entry.get()),
            'usd_amount_custom': m_usd_amount_custom,
            'spread_default': float(self.spread_default_entry.get()),
            'spread_custom': m_spread_custom
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

        # Destroy existing orders and balances frames within their respective classes.
        # if self.parent.gui_orders.orders_frame:
        #     for widget in self.parent.gui_orders.orders_frame.winfo_children():
        #         widget.destroy()
        #     self.parent.gui_orders.orders_frame.destroy()
        # if self.parent.gui_balances.balances_frame:
        #     for widget in self.parent.gui_balances.balances_frame.winfo_children():
        #         widget.destroy()
        # if self.parent.balances_frame:
        #     for widget in self.parent.balances_frame.winfo_children():
        #         widget.destroy()
        #     self.parent.balances_frame.destroy()

        # # Re-create the orders and balances treeviews via their respective classes.
        # self.parent.gui_orders.create_orders_treeview()
        # self.parent.gui_balances.create_balances_treeview()

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

    def add_usd_amount_custom(self, m_pair, m_amount):
        try:
            m_amount = float(m_amount)
            if m_pair and m_pair not in [self.usd_amount_custom_treeview.item(iid, 'values')[0] for iid in
                                         self.usd_amount_custom_treeview.get_children()]:
                self.usd_amount_custom_treeview.insert('', 'end', iid=m_pair, values=(m_pair, m_amount))
                self.update_status("USD Amount Custom entry added.", 'lightgreen')
            else:
                self.update_status("Pair already exists.", 'red')
        except ValueError:
            self.update_status("Invalid amount format. Must be a number.", 'red')

    def remove_usd_amount_custom(self):
        selected_item = self.usd_amount_custom_treeview.selection()
        if selected_item:
            self.usd_amount_custom_treeview.delete(selected_item)
            self.update_status("USD Amount Custom entry removed.", 'lightgreen')
        else:
            self.update_status("No item selected.", 'red')

    def add_spread_custom(self, m_pair, m_spread):
        try:
            m_spread = float(m_spread)
            if m_pair and m_pair not in [self.spread_custom_treeview.item(iid, 'values')[0] for iid in
                                         self.spread_custom_treeview.get_children()]:
                self.spread_custom_treeview.insert('', 'end', iid=m_pair, values=(m_pair, m_spread))
                self.update_status("Spread Custom entry added.", 'lightgreen')
            else:
                self.update_status("Pair already exists.", 'red')
        except ValueError:
            self.update_status("Invalid spread format. Must be a number.", 'red')

    def remove_spread_custom(self):
        selected_item = self.spread_custom_treeview.selection()
        if selected_item:
            self.spread_custom_treeview.delete(selected_item)
            self.update_status("Spread Custom entry removed.", 'lightgreen')
        else:
            self.update_status("No item selected.", 'red')

    def update_status(self, message, color='black'):
        self.status_var.set(message)
        self.status_label.config(foreground=color)


class GUI_Main:
    def __init__(self):
        self.config_window = None
        self.root = tk.Tk()
        self.root.title("PingPong")
        self.root.resizable(width=False, height=False)
        self.send_process = None
        self.started = False
        self.initialize()

        self.style = Style(init.config_pp.ttk_theme)
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
        init.init(strategy="pingpong", loadxbridgeconf=loadxbridgeconf)

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
        init.init(strategy="pingpong", loadxbridgeconf=loadxbridgeconf)
        self.gui_orders.purge_treeview()
        self.gui_balances.purge_treeview()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()
