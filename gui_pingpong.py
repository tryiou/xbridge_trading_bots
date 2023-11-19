# sudo apt install python3-tk
# from tkinter import *
import ctypes
import inspect
import threading
import time
import tkinter as tk
from tkinter import ttk

from ttkbootstrap import Style
import main_pingpong
from config import config_pingpong as config
from definitions import init


def _async_raise(tid, exctype):
    """raises the exception, performs cleanup if needed"""
    if not inspect.isclass(exctype):
        raise TypeError("Only types can be raised (not instances)")
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(tid), ctypes.py_object(exctype))
    if res == 0:
        raise ValueError("invalid thread id")
    elif res != 1:
        # """if it returns a number greater than one, you're in trouble,
        # and you should call it again with exc=NULL to revert the effect"""
        ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, 0)
        raise SystemError("PyThreadState_SetAsyncExc failed")


class Thread(threading.Thread):
    def _get_my_tid(self):
        """determines this (self's) thread id"""
        if not self.is_alive():
            raise threading.ThreadError("the thread is not active")

        # do we have it cached?
        if hasattr(self, "_thread_id"):
            return self._thread_id

        # no, look for it in the _active dict
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
        self.btn_cancel_all = None
        self.btn_stop = None
        self.btn_start = None
        self.send_process = None
        self.started = False
        # Use ttkbootstrap style
        self.style = Style(config.ttk_theme)

        self.balances_frame = None
        self.balances_treeview = None
        self.orders_frame = None
        self.orders_treeview = None
        self.gui_create_buttons()
        self.gui_create_orders_treeview()
        self.gui_create_balances_treeview()

    def gui_create_buttons(self):
        btn_width = 10
        self.btn_start = ttk.Button(self.root, text="START", command=self.start, width=btn_width)
        self.btn_start.grid(column=0, row=0, padx=5, pady=5)
        self.btn_stop = ttk.Button(self.root, text="STOP", command=self.stop, width=btn_width)
        self.btn_stop.grid(column=1, row=0, padx=5, pady=5)
        self.btn_stop.state(["disabled"])
        self.btn_cancel_all = ttk.Button(self.root, text="CANCEL ALL", command=self.cancel_all, width=btn_width)
        self.btn_cancel_all.grid(column=2, row=0, padx=5, pady=5)

    def gui_create_orders_treeview(self):
        columns = ("Pair", "Status", "Side", "Flag", "Variation")
        # Create a frame for the labels and other widgets
        self.orders_frame = ttk.Frame(self.root)
        self.orders_frame.grid(row=1, sticky='ew', columnspan=3)  #

        height = len(config.user_pairs) + 1
        self.orders_treeview = ttk.Treeview(self.orders_frame, columns=columns, height=height, show="headings")

        # Create Treeview inside the frame
        self.orders_treeview.grid()

        # Define column headings
        for col, label_text in enumerate(columns):
            self.orders_treeview.heading(label_text, text=label_text, anchor="w")
            self.orders_treeview.column(label_text, width=100, anchor="w")  # Adjust width as needed
        # Adjust the weight of the last column to make it resizable
        # self.orders_treeview.column("#4", stretch=tk.YES)
        for x, pair in enumerate(config.user_pairs):
            self.orders_treeview.insert("", tk.END, values=[pair, "None", "None", "X", "None"])
        self.initialize()

    def gui_create_balances_treeview(self):
        columns = ("Coin", "USD ticker", "Total", "Free", "Total USD")
        # Create a frame for the headers
        self.balances_frame = ttk.Frame(self.root)
        self.balances_frame.grid(columnspan=3)

        # Create Treeview on the header frame
        height = len(init.t.keys())
        self.balances_treeview = ttk.Treeview(self.balances_frame, columns=columns, show="headings", height=height,
                                              selectmode="none")

        for col in columns:
            self.balances_treeview.heading(col, text=col, anchor="w")
            self.balances_treeview.column(col, width=100)  # Adjust width as needed

        # Place the Treeview on the window
        self.balances_treeview.grid()

        # Initialize content for each token
        for x, token in enumerate(init.t):
            # bal = float("{:.4f}".format(init.t[token].dex_total_balance)) if init.t[token].dex_total_balance else 0
            data = (token, str(None), str(None), str(None), str(None))
            self.balances_treeview.insert("", tk.END, values=data)

    def initialize(self):
        init.init_pingpong()

    def start(self):
        self.send_process = Thread(target=main_pingpong.main)
        self.send_process.start()
        self.started = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="active")
        print("start done")

    def stop(self):
        import definitions.xbridge_def as xb
        xb.cancelallorders()
        self.send_process.terminate()
        print("send stop order")
        while self.send_process.is_alive():
            print("wait process end...")
            time.sleep(1)
        self.initialize()
        self.btn_stop.config(state="disabled")
        self.btn_start.config(state="active")
        self.started = False
        print("stop done")

    def cancel_all(self):
        import definitions.xbridge_def as xb
        xb.cancelallorders()
        print("Cancel All orders done")

    def refresh_gui(self):
        if self.started:
            if not self.send_process.is_alive():
                import definitions.xbridge_def as xb
                print("pingpong bot crashed!")
                xb.cancelallorders()
                self.btn_stop.config(state="disabled")
                self.btn_start.config(state="active")
                self.started = False

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
                # "",
                get_flag(pair.dex_order.get('status', 'None')),
                str(pair.variation)
            ]
            # Get the color based on the status
            # color = get_flag(pair.dex_order.get('status', 'None'))
            # self.update_color_indicator(item_id, color)
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
        # print(f"new: {new}, before: {before} if : {condition}")
        # print(f"type(new): {type(new)}, type(before): {type(before)}")
        if condition:
            self.orders_treeview.item(item_id, values=new_values)

    def update_color_indicator(self, item_id, color):
        # Get the canvas widget from the treeview
        canvas = self.orders_treeview.item(item_id, 'values')[-1]

        if canvas:
            # Clear existing drawings
            canvas.delete("all")

            # Draw a colored circle
            canvas.create_oval(5, 5, 25, 25, fill=color, outline=color)

    def disable_stop_button(self):
        self.btn_stop.config(state="disabled")

    def enable_start_button(self):
        self.btn_start.config(state="active")

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

            # Update the values in the Treeview if they have changed
            if new_values != list(values):
                self.balances_treeview.item(item_id, values=new_values)


def get_flag(status):
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
    # expired
    # offline
    # canceled
    # invalid
    # rolled back
    # rollback failed

    return status_color_mapping.get(status, 'X')


if __name__ == '__main__':
    app = MyGUI()
    # app.init()
    app.refresh_gui()
    app.root.mainloop()
