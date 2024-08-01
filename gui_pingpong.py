import ctypes
import inspect
import threading
import time
import tkinter as tk
from ruamel.yaml import YAML
from tkinter import ttk

from ttkbootstrap import Style
import main_pingpong
from definitions import init
import re


def _async_raise(tid, exctype):
    """raises the exception, performs cleanup if needed"""
    if not inspect.isclass(exctype):
        raise TypeError("Only types can be raised (not instances)")
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(tid), ctypes.py_object(exctype))
    if res == 0:
        raise ValueError("invalid thread id")
    elif res != 1:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, 0)
        raise SystemError("PyThreadState_SetAsyncExc failed")


class Thread(threading.Thread):
    def _get_my_tid(self):
        """determines this (self's) thread id"""
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
        """raises the given exception type in the context of this thread"""
        _async_raise(self._get_my_tid(), exctype)

    def terminate(self):
        """raises SystemExit in the context of the given thread, which should
        cause the thread to exit silently (unless caught)"""
        self.raise_exc(SystemExit)


class MyGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PingPong")
        self.root.resizable(width=False, height=False)
        self.send_process = None
        self.started = False

        self.initialize()
        # Use ttkbootstrap style
        self.style = Style(init.config_pp.ttk_theme)
        self.balances_frame = None
        self.balances_treeview = None
        self.orders_frame = None
        self.orders_treeview = None
        self.selected_item_orders_treeview = None
        self.right_click_order_window = None
        self.status_var = tk.StringVar(value="Idle")

        self.gui_create_widgets()

    def gui_create_widgets(self):
        self.gui_create_buttons()
        self.gui_create_orders_treeview()
        self.gui_create_balances_treeview()
        self.create_status_bar()

    def gui_create_buttons(self):
        button_frame = ttk.Frame(self.root)
        button_frame.grid(column=0, row=0, padx=5, pady=5, sticky='ew')
        btn_width = 10
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

    def gui_create_orders_treeview(self):
        columns = ("Pair", "Status", "Side", "Flag", "Variation")
        self.orders_frame = ttk.LabelFrame(self.root, text="Orders")
        self.orders_frame.grid(row=1, padx=5, pady=5, sticky='ew', columnspan=4)
        sortedpairs = sorted(init.config_pp.user_pairs)
        height = len(sortedpairs) + 1
        self.orders_treeview = ttk.Treeview(self.orders_frame, columns=columns, height=height, show="headings")
        self.orders_treeview.grid(padx=5, pady=5)

        for col, label_text in enumerate(columns):
            self.orders_treeview.heading(label_text, text=label_text, anchor="w")
            self.orders_treeview.column(label_text, width=100, anchor="w")
        for x, pair in enumerate(sortedpairs):
            self.orders_treeview.insert("", tk.END, values=[pair, "None", "None", "X", "None"])

    def gui_create_balances_treeview(self):
        columns = ("Coin", "USD ticker", "Total", "Free", "Total USD")
        self.balances_frame = ttk.LabelFrame(self.root, text="Balances")
        self.balances_frame.grid(row=2, padx=5, pady=5, sticky='ew', columnspan=4)
        height = len(init.t.keys())
        self.balances_treeview = ttk.Treeview(self.balances_frame, columns=columns, show="headings", height=height,
                                              selectmode="none")
        self.balances_treeview.grid(padx=5, pady=5)
        for col in columns:
            self.balances_treeview.heading(col, text=col, anchor="w")
            self.balances_treeview.column(col, width=100)
        for x, token in enumerate(init.t):
            data = (token, str(None), str(None), str(None), str(None))
            self.balances_treeview.insert("", tk.END, values=data)

    def create_status_bar(self):
        self.status_frame = ttk.Frame(self.root)
        self.status_frame.grid(row=3, column=0, columnspan=4, padx=5, pady=5, sticky='ew')
        self.status_label = ttk.Label(self.status_frame, textvariable=self.status_var, anchor='w')
        self.status_label.grid(row=0, column=0, padx=5, pady=5, sticky='ew')

    def initialize(self):
        init.init_pingpong()

    def start(self):
        self.status_var.set("Bot is running...")
        self.send_process = Thread(target=main_pingpong.run_async_main)
        self.send_process.start()
        self.started = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="active")
        self.btn_configure.config(state="disabled")
        print("start done")

    def stop(self):
        import definitions.xbridge_def as xb
        self.status_var.set("Stopping bot...")
        self.send_process.terminate()
        print("send stop order")
        while self.send_process.is_alive():
            print("wait process end...")
            time.sleep(1)
        xb.cancelallorders()
        self.initialize()
        self.btn_stop.config(state="disabled")
        self.btn_start.config(state="active")
        self.started = False
        self.status_var.set("Bot stopped.")
        print("stop done")

    def cancel_all(self):
        import definitions.xbridge_def as xb
        self.status_var.set("Cancelling all orders...")
        xb.cancelallorders()
        print("Cancel All orders done")
        self.status_var.set("All orders cancelled.")

    def refresh_gui(self):
        if self.started:
            self.btn_configure.config(state="disabled")
            if not self.send_process.is_alive():
                import definitions.xbridge_def as xb
                print("pingpong bot crashed!")
                xb.cancelallorders()
                self.btn_stop.config(state="disabled")
                self.btn_start.config(state="active")
                self.started = False
                self.status_var.set("Bot crashed!")
        else:
            self.btn_configure.config(state="active")

        for key, pair in init.p.items():
            for item_id in self.orders_treeview.get_children():
                values = self.orders_treeview.item(item_id, 'values')
                if values[0] == key:
                    self.update_order_display(item_id, pair)
        self.update_balance_display()
        self.root.after(1500, self.refresh_gui)

    def update_order_display(self, item_id, pair):
        values_before = self.orders_treeview.item(item_id, 'values')
        if self.started and pair.dex_order and 'status' in pair.dex_order:
            new_values = [
                pair.symbol,
                pair.dex_order.get('status', 'None'),
                pair.current_order.get('side', 'None'),
                self.get_flag(pair.dex_order.get('status', 'None')),
                str(pair.variation)
            ]
        else:
            new_values = [
                pair.symbol,
                'Disabled' if pair.disabled else 'None',
                'None',
                'X',
                'None'
            ]
        new = list(new_values)
        before = list(values_before)
        condition = (new != before)
        if condition:
            self.orders_treeview.item(item_id, values=new_values)

    def update_balance_display(self):
        for item_id in self.balances_treeview.get_children():
            values = self.balances_treeview.item(item_id, 'values')
            token = values[0]
            usd_price = init.t[token].usd_price
            dex_total_balance = init.t[token].dex_total_balance
            dex_free_balance = init.t[token].dex_free_balance

            new_values = [token]
            if usd_price:
                new_usd_price = "{:.2f}$".format(usd_price)
                new_values.append(new_usd_price)
            else:
                new_values.append("0.00$")
            if dex_total_balance:
                new_total_balance = "{:.4f}".format(dex_total_balance)
                new_values.append(new_total_balance)
            else:
                new_values.append("0.00")
            if dex_free_balance:
                new_free_balance = "{:.4f}".format(dex_free_balance)
                new_values.append(new_free_balance)
            else:
                new_values.append("0.00")
            if usd_price and dex_total_balance:
                usd_bal = usd_price * dex_total_balance
                new_usd_bal = "{:.2f}$".format(usd_bal)
                new_values.append(new_usd_bal)
            else:
                new_values.append("0.00$")
            if new_values != list(values):
                self.balances_treeview.item(item_id, values=new_values)

    def open_configure_window(self):
        self.btn_start.config(state="disabled")  # Disable the start button
        self.btn_configure.config(state="disabled")  # Disable the configure button

        self.config_window = tk.Toplevel(self.root)
        self.config_window.title("Configure Bot")

        def on_configure_window_close():
            self.config_window.destroy()
            self.btn_start.config(state="active")  # Re-enable the start button
            self.btn_configure.config(state="active")  # Re-enable the configure button
            self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        self.config_window.protocol("WM_DELETE_WINDOW", lambda: on_configure_window_close())

        def is_valid_pair(pair_symbol):
            return bool(re.match(r"^[A-Z]+/[A-Z]+$", pair_symbol))

        def save_config():
            config_file_path = './config/config_pingpong.yaml'
            yaml = YAML()
            yaml.default_flow_style = False
            yaml.indent(mapping=2, sequence=4, offset=2)

            m_usd_amount_custom = {}
            for item_id in usd_amount_custom_treeview.get_children():
                values = usd_amount_custom_treeview.item(item_id, 'values')
                if len(values) == 2:
                    m_pair, m_amount = values
                    try:
                        m_amount = float(m_amount)
                        m_usd_amount_custom[m_pair] = m_amount
                    except ValueError:
                        update_status(f"Invalid amount format for pair {m_pair}. Skipping...", 'red')

            m_spread_custom = {}
            for item_id in spread_custom_treeview.get_children():
                values = spread_custom_treeview.item(item_id, 'values')
                if len(values) == 2:
                    m_pair, m_spread = values
                    try:
                        m_spread = float(m_spread)
                        m_spread_custom[m_pair] = m_spread
                    except ValueError:
                        update_status(f"Invalid spread format for pair {m_pair}. Skipping...", 'red')

            new_config = {
                'debug_level': int(debug_level_entry.get()),
                'ttk_theme': ttk_theme_entry.get(),
                'user_pairs': [m_pair for m_pair in user_pairs_listbox.get(0, tk.END)],
                'price_variation_tolerance': float(price_variation_entry.get()),
                'sell_price_offset': float(sell_price_offset_entry.get()),
                'usd_amount_default': float(usd_amount_default_entry.get()),
                'usd_amount_custom': m_usd_amount_custom,
                'spread_default': float(spread_default_entry.get()),
                'spread_custom': m_spread_custom
            }

            try:
                with open(config_file_path, 'r') as file:
                    existing_config = yaml.load(file)

                if existing_config == new_config:
                    update_status("Configuration is already up to date.", 'lightgray')
                    return

                for key, value in new_config.items():
                    existing_config[key] = value

                with open(config_file_path, 'w') as file:
                    yaml.dump(existing_config, file)
                update_status("Configuration saved successfully.", 'lightgreen')

                reload_configuration()

            except Exception as e:
                update_status(f"Failed to save configuration: {e}", 'lightcoral')

        def reload_configuration():
            init.init_pingpong()
            if self.orders_frame:
                for widget in self.orders_frame.winfo_children():
                    widget.destroy()
                self.orders_frame.destroy()
                self.orders_frame = None

            if self.balances_frame:
                for widget in self.balances_frame.winfo_children():
                    widget.destroy()
                self.balances_frame.destroy()
                self.balances_frame = None

            self.gui_create_orders_treeview()
            self.gui_create_balances_treeview()

        def add_pair():
            new_pair = new_pair_entry.get().strip()
            if is_valid_pair(new_pair):
                if new_pair not in user_pairs_listbox.get(0, tk.END):
                    user_pairs_listbox.insert(tk.END, new_pair)
                    new_pair_entry.delete(0, tk.END)
                    update_status("Pair added.", 'lightgreen')
                else:
                    update_status("Pair already exists.", 'red')
            else:
                update_status("Invalid pair format. Must be TOKEN1/TOKEN2.", 'red')

        def remove_pair():
            selected = user_pairs_listbox.curselection()
            if selected:
                user_pairs_listbox.delete(selected[0])
            else:
                update_status("No pair selected.", 'red')

        def add_usd_amount_custom():
            m_pair = usd_amount_custom_pair_entry.get().strip()
            m_amount = usd_amount_custom_amount_entry.get().strip()
            if m_pair and m_amount:
                try:
                    m_amount = float(m_amount)
                    if m_pair not in [item[0] for item in usd_amount_custom_treeview.get_children()]:
                        usd_amount_custom_treeview.insert('', 'end', iid=m_pair, values=(m_pair, m_amount))
                        usd_amount_custom_pair_entry.delete(0, tk.END)
                        usd_amount_custom_amount_entry.delete(0, tk.END)
                        update_status("USD Amount Custom entry added.", 'lightgreen')
                    else:
                        update_status("Pair already exists.", 'red')
                except ValueError:
                    update_status("Invalid amount format. Must be a number.", 'red')
            else:
                update_status("Pair and amount cannot be empty.", 'red')

        def remove_usd_amount_custom():
            selected_item = usd_amount_custom_treeview.selection()
            if selected_item:
                usd_amount_custom_treeview.delete(selected_item)
                update_status("USD Amount Custom entry removed.", 'lightgreen')
            else:
                update_status("No item selected.", 'red')

        def add_spread_custom():
            m_pair = spread_custom_pair_entry.get().strip()
            m_spread = spread_custom_spread_entry.get().strip()
            if m_pair and m_spread:
                try:
                    m_spread = float(m_spread)
                    if m_pair not in [item[0] for item in spread_custom_treeview.get_children()]:
                        spread_custom_treeview.insert('', 'end', iid=m_pair, values=(m_pair, m_spread))
                        spread_custom_pair_entry.delete(0, tk.END)
                        spread_custom_spread_entry.delete(0, tk.END)
                        update_status("Spread Custom entry added.", 'lightgreen')
                    else:
                        update_status("Pair already exists.", 'red')
                except ValueError:
                    update_status("Invalid spread format. Must be a number.", 'red')
            else:
                update_status("Pair and spread cannot be empty.", 'red')

        def remove_spread_custom():
            selected_item = spread_custom_treeview.selection()
            if selected_item:
                spread_custom_treeview.delete(selected_item)
                update_status("Spread Custom entry removed.", 'lightgreen')
            else:
                update_status("No item selected.", 'red')

        def update_status(message, color='black'):
            status_var.set(message)
            status_label.config(foreground=color)

        # Create the main frame to hold all content
        main_frame = ttk.Frame(self.config_window)
        main_frame.pack(fill='both', expand=True)

        # Create a canvas widget
        canvas = tk.Canvas(main_frame)
        canvas.pack(side='left', fill='both', expand=True)

        # Create a scrollbar for the canvas

        style = ttk.Style()
        style.configure("Vertical.TScrollbar",
                        gripcount=0,
                        background="#d9d9d9",
                        troughcolor="#f0f0f0",
                        # Use padding to affect the scrollbar's width
                        troughpadding=2,
                        arrowcolor="#333")
        style.map("Vertical.TScrollbar",
                  background=[('pressed', '#d9d9d9'), ('active', '#d9d9d9')],
                  relief=[('pressed', 'flat'), ('active', 'flat')])

        scrollbar = ttk.Scrollbar(main_frame, orient='vertical', command=canvas.yview, style="Vertical.TScrollbar")
        scrollbar.pack(side='right', fill='y')
        # scrollbar = ttk.Scrollbar(main_frame, orient='vertical', command=canvas.yview)
        # scrollbar.pack(side='right', fill='y')

        # Create a frame inside the canvas
        content_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=content_frame, anchor='nw')

        # Update the scroll region of the canvas
        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        content_frame.bind("<Configure>", on_frame_configure)

        # Configure content_frame to fill the available space
        content_frame.grid_columnconfigure(1, weight=1)
        content_frame.grid_rowconfigure(20, weight=1)  # Last row (status bar) should not expand

        # Create and place widgets inside the content_frame
        ttk.Label(content_frame, text="Debug Level:").grid(row=0, column=0, padx=5, pady=5, sticky='w')
        debug_level_entry = ttk.Entry(content_frame)
        debug_level_entry.grid(row=0, column=1, padx=5, pady=5, sticky='ew')
        debug_level_entry.insert(0, init.config_pp.get('debug_level', ''))

        ttk.Label(content_frame, text="TTK Theme:").grid(row=1, column=0, padx=5, pady=5, sticky='w')
        ttk_theme_entry = ttk.Entry(content_frame)
        ttk_theme_entry.grid(row=1, column=1, padx=5, pady=5, sticky='ew')
        ttk_theme_entry.insert(0, init.config_pp.get('ttk_theme', ''))

        ttk.Label(content_frame, text="User Pairs:").grid(row=2, column=0, padx=5, pady=5, sticky='w')
        user_pairs_frame = ttk.Frame(content_frame)
        user_pairs_frame.grid(row=2, column=1, padx=5, pady=5, sticky='nsew')

        user_pairs_listbox = tk.Listbox(user_pairs_frame, selectmode=tk.SINGLE, height=4, width=30)
        user_pairs_listbox.grid(row=0, column=0, rowspan=2, padx=5, pady=5, sticky='ns')

        user_pairs = init.config_pp.get('user_pairs', [])
        for pair in user_pairs:
            user_pairs_listbox.insert(tk.END, pair)

        new_pair_label = ttk.Label(content_frame, text="New Pair:")
        new_pair_label.grid(row=3, column=0, padx=5, pady=5, sticky='w')

        new_pair_entry = ttk.Entry(content_frame)
        new_pair_entry.grid(row=3, column=1, padx=5, pady=5, sticky='ew')

        add_pair_button = ttk.Button(content_frame, text="Add", command=add_pair)
        add_pair_button.grid(row=4, column=1, padx=5, pady=5, sticky='ew')

        remove_pair_button = ttk.Button(content_frame, text="Remove", command=remove_pair)
        remove_pair_button.grid(row=5, column=1, padx=5, pady=5, sticky='ew')

        ttk.Label(content_frame, text="Price Variation Tolerance:").grid(row=6, column=0, padx=5, pady=5, sticky='w')
        price_variation_entry = ttk.Entry(content_frame)
        price_variation_entry.grid(row=6, column=1, padx=5, pady=5, sticky='ew')
        price_variation_entry.insert(0, init.config_pp.get('price_variation_tolerance', ''))

        ttk.Label(content_frame, text="Sell Price Offset:").grid(row=7, column=0, padx=5, pady=5, sticky='w')
        sell_price_offset_entry = ttk.Entry(content_frame)
        sell_price_offset_entry.grid(row=7, column=1, padx=5, pady=5, sticky='ew')
        sell_price_offset_entry.insert(0, init.config_pp.get('sell_price_offset', ''))

        ttk.Label(content_frame, text="USD Amount Default:").grid(row=8, column=0, padx=5, pady=5, sticky='w')
        usd_amount_default_entry = ttk.Entry(content_frame)
        usd_amount_default_entry.grid(row=8, column=1, padx=5, pady=5, sticky='ew')
        usd_amount_default_entry.insert(0, init.config_pp.get('usd_amount_default', ''))

        ttk.Label(content_frame, text="USD Amount Custom:").grid(row=9, column=0, padx=5, pady=5, sticky='w')
        usd_amount_custom_frame = ttk.Frame(content_frame)
        usd_amount_custom_frame.grid(row=9, column=1, padx=5, pady=5, sticky='nsew')

        usd_amount_custom_treeview = ttk.Treeview(usd_amount_custom_frame, columns=("Pair", "Amount"), show="headings",
                                                  height=4)
        usd_amount_custom_treeview.heading("Pair", text="Pair")
        usd_amount_custom_treeview.heading("Amount", text="Amount")
        usd_amount_custom_treeview.pack(side='left', fill='both', expand=True)

        usd_amount_custom_scroll = ttk.Scrollbar(usd_amount_custom_frame, orient='vertical',
                                                 command=usd_amount_custom_treeview.yview)
        usd_amount_custom_scroll.pack(side='right', fill='y')
        usd_amount_custom_treeview.configure(yscrollcommand=usd_amount_custom_scroll.set)

        ttk.Label(content_frame, text="Pair:").grid(row=10, column=0, padx=5, pady=5, sticky='w')
        usd_amount_custom_pair_entry = ttk.Entry(content_frame)
        usd_amount_custom_pair_entry.grid(row=10, column=1, padx=5, pady=5, sticky='ew')

        ttk.Label(content_frame, text="Amount:").grid(row=11, column=0, padx=5, pady=5, sticky='w')
        usd_amount_custom_amount_entry = ttk.Entry(content_frame)
        usd_amount_custom_amount_entry.grid(row=11, column=1, padx=5, pady=5, sticky='ew')

        add_usd_amount_custom_button = ttk.Button(content_frame, text="Add USD Amount Custom",
                                                  command=add_usd_amount_custom)
        add_usd_amount_custom_button.grid(row=12, column=1, padx=5, pady=5, sticky='ew')

        remove_usd_amount_custom_button = ttk.Button(content_frame, text="Remove USD Amount Custom",
                                                     command=remove_usd_amount_custom)
        remove_usd_amount_custom_button.grid(row=13, column=1, padx=5, pady=5, sticky='ew')

        ttk.Label(content_frame, text="Spread Default:").grid(row=14, column=0, padx=5, pady=5, sticky='w')
        spread_default_entry = ttk.Entry(content_frame)
        spread_default_entry.grid(row=14, column=1, padx=5, pady=5, sticky='ew')
        spread_default_entry.insert(0, init.config_pp.get('spread_default', ''))

        ttk.Label(content_frame, text="Spread Custom:").grid(row=15, column=0, padx=5, pady=5, sticky='w')
        spread_custom_frame = ttk.Frame(content_frame)
        spread_custom_frame.grid(row=15, column=1, padx=5, pady=5, sticky='nsew')

        spread_custom_treeview = ttk.Treeview(spread_custom_frame, columns=("Pair", "Spread"), show="headings",
                                              height=4)
        spread_custom_treeview.heading("Pair", text="Pair")
        spread_custom_treeview.heading("Spread", text="Spread")
        spread_custom_treeview.pack(side='left', fill='both', expand=True)

        spread_custom_scroll = ttk.Scrollbar(spread_custom_frame, orient='vertical',
                                             command=spread_custom_treeview.yview)
        spread_custom_scroll.pack(side='right', fill='y')
        spread_custom_treeview.configure(yscrollcommand=spread_custom_scroll.set)

        ttk.Label(content_frame, text="Pair:").grid(row=16, column=0, padx=5, pady=5, sticky='w')
        spread_custom_pair_entry = ttk.Entry(content_frame)
        spread_custom_pair_entry.grid(row=16, column=1, padx=5, pady=5, sticky='ew')

        ttk.Label(content_frame, text="Spread:").grid(row=17, column=0, padx=5, pady=5, sticky='w')
        spread_custom_spread_entry = ttk.Entry(content_frame)
        spread_custom_spread_entry.grid(row=17, column=1, padx=5, pady=5, sticky='ew')

        add_spread_custom_button = ttk.Button(content_frame, text="Add Spread Custom", command=add_spread_custom)
        add_spread_custom_button.grid(row=18, column=1, padx=5, pady=5, sticky='ew')

        remove_spread_custom_button = ttk.Button(content_frame, text="Remove Spread Custom",
                                                 command=remove_spread_custom)
        remove_spread_custom_button.grid(row=19, column=1, padx=5, pady=5, sticky='ew')

        save_button = ttk.Button(content_frame, text="Save", command=save_config)
        save_button.grid(row=20, column=0, columnspan=2, pady=10, sticky='ew')

        status_frame = ttk.Frame(content_frame)
        status_frame.grid(row=21, column=0, columnspan=2, pady=5, sticky='ew')
        status_var = tk.StringVar()
        status_label = ttk.Label(status_frame, textvariable=status_var, anchor='w')
        status_label.pack(fill='x')

        # Insert data into treeviews
        usd_amount_custom = init.config_pp.get('usd_amount_custom', {})
        for pair, amount in usd_amount_custom.items():
            usd_amount_custom_treeview.insert('', 'end', iid=pair, values=(pair, amount))

        spread_custom = init.config_pp.get('spread_custom', {})
        for pair, spread in spread_custom.items():
            spread_custom_treeview.insert('', 'end', iid=pair, values=(pair, spread))

        # Configure window size to fit screen height
        screen_height = self.config_window.winfo_screenheight()
        self.config_window.geometry(f"650x{screen_height - 100}")  # Adjust width and height as needed

        self.config_window.update_idletasks()  # Update the window to reflect changes

        # Ensure the canvas updates when resizing
        self.config_window.bind("<Configure>",
                           lambda event: canvas.config(width=self.config_window.winfo_width() - scrollbar.winfo_width()))

    def get_flag(self, status):
        status_color_mapping = {
            'open': 'V',
            'new': 'V',
            'created': 'V',
            'accepting': 'V',
            'hold': 'V',
            'initialized': 'V',
            'committed': 'V',
            'finished': 'V'
        }
        return status_color_mapping.get(status, 'X')


if __name__ == '__main__':
    app = MyGUI()
    app.refresh_gui()
    app.root.mainloop()
