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
        self.send_process = None
        self.root = tk.Tk()
        self.root.title("PingPong")
        self.started = False

        # Use ttkbootstrap style
        self.style = Style(config.ttk_theme)

        self.btn_start = ttk.Button(self.root, text="START", command=self.start)
        self.btn_start.grid(column=0, row=0)
        self.btn_stop = ttk.Button(self.root, text="STOP", command=self.stop)
        self.btn_stop.grid(column=1, row=0)
        self.btn_stop.state(["disabled"])
        self.lb_orders_lst = []
        self.lb_bals_lst = []
        self.lbl_bal = ttk.Label(self.root, text="BALANCES", borderwidth=3, relief="raised")

        self.create_gui()
        self.init_bals_gui()

    def create_gui(self):
        labels = ["SYMBOL", "STATUS", "SIDE", "FLAG", "VARIATION"]
        separator_width = 5  # Set the width of the separator

        for col, label_text in enumerate(labels):
            ttk.Label(self.root, text=label_text, borderwidth=3, relief="raised").grid(column=col, row=1, sticky="ew")

        self.root.columnconfigure(len(labels), weight=2)

        canvas_height = 17
        canvas_width = 17
        oval_coords = (11, 1, 29, 18)

        for x, pair in enumerate(config.user_pairs):
            order_info = {
                "symbol_text": pair,
                "symbol": ttk.Label(self.root, text=pair),
                "status": ttk.Label(self.root, text="None"),
                "side": ttk.Label(self.root, text="None"),
                "canvas": tk.Canvas(self.root, height=canvas_height, width=canvas_width),
                "oval": None,

                "variation": ttk.Label(self.root, text="None")
            }
            order_info['symbol'].grid(column=0, row=x + 2, sticky="ew")
            order_info['status'].grid(column=1, row=x + 2, sticky="ew")
            order_info['side'].grid(column=2, row=x + 2, sticky="ew")
            order_info['canvas'].grid(column=3, row=x + 2, sticky="nsew")
            order_info['oval'] = order_info['canvas'].create_oval(oval_coords)
            order_info['variation'].grid(column=4, row=x + 2, sticky="ew")
            self.lb_orders_lst.append(order_info)

        self.lbl_bal.grid(column=0, row=len(config.user_pairs) + 3)
        self.initialise()

    def init_bals_gui(self):
        self.lb_bals_lst = []
        for x, token in enumerate(init.t):
            bal = float("{:.4f}".format(init.t[token].dex_total_balance)) if init.t[token].dex_total_balance else 0

            bal_info = {
                "symbol_text": token,
                "symbol": ttk.Label(self.root, text=token),
                "balance": ttk.Label(self.root, text=str(bal)),
                "usd_bal": ttk.Label(self.root, text=str(None))
            }

            bal_info['symbol'].grid(column=x, row=len(config.user_pairs) + 4)
            bal_info['balance'].grid(column=x, row=len(config.user_pairs) + 5)
            bal_info['usd_bal'].grid(column=x, row=len(config.user_pairs) + 6)
            self.lb_bals_lst.append(bal_info)

    def initialise(self):
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
        self.send_process.terminate()
        while self.send_process.is_alive():
            time.sleep(1)
        xb.cancelallorders()
        self.initialise()
        self.btn_stop.config(state="disabled")
        self.btn_start.config(state="active")
        self.started = False
        print("stop done")

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
            for ppair in self.lb_orders_lst:
                if ppair['symbol_text'] == key:
                    # print(pair.__dict__)
                    self.update_order_display(ppair, pair)
            for token in self.lb_bals_lst:
                update_balance_display(token, pair)

        self.root.after(1500, self.refresh_gui)

    def update_order_display(self, ppair, pair):
        if self.started and pair.dex_order and 'status' in pair.dex_order:
            ppair['status'].configure(text=pair.dex_order['status'])
            ppair['variation'].configure(text=str(pair.var))
            color = get_oval_color(pair.dex_order['status'])
            ppair['canvas'].itemconfigure(ppair['oval'], fill=color)
        update_current_order_display(ppair, pair)

    def disable_stop_button(self):
        self.btn_stop.config(state="disabled")

    def enable_start_button(self):
        self.btn_start.config(state="active")


def update_current_order_display(ppair, pair):
    if pair.current_order and 'side' in pair.current_order:
        ppair['side'].configure(text=pair.current_order['side'])
    else:
        ppair['status'].configure(text='Disabled' if pair.disabled else 'None')
        ppair['side'].configure(text='Disabled' if pair.disabled else 'None')
        ppair['variation'].configure(text='None')
        reset_oval_representation(ppair)


def get_oval_color(status):
    if status == 'open':
        return "green"
    elif status in {'new', 'created'}:
        return "yellow"
    elif status in {'accepting', 'hold', 'initialized', 'commited', 'finished'}:
        return "dark orchid"
    else:
        return "red"


def reset_oval_representation(ppair):
    ppair['canvas'].itemconfigure(ppair['oval'], fill="red")


def update_balance_display(token, pair):
    if token['symbol_text'] == pair.t1.symbol or token['symbol_text'] == pair.t2.symbol:
        update_token_balance_display(token, pair)
    elif token['symbol_text'] == 'BTC':
        update_btc_balance_display(token)


def update_token_balance_display(token, pair):
    symbol = pair.t1.symbol if token['symbol_text'] == pair.t1.symbol else pair.t2.symbol
    usd_price = init.t[symbol].usd_price
    dex_total_balance = pair.t1.dex_total_balance if token[
                                                         'symbol_text'] == pair.t1.symbol else pair.t2.dex_total_balance

    if usd_price is not None:
        token['symbol'].configure(text="{}['{:.2f}']".format(symbol, usd_price))
    else:
        token['symbol'].configure(text=symbol)

    if dex_total_balance is not None:
        if dex_total_balance >= 1:
            balance_text = '{:.2f}'.format(dex_total_balance)
        else:
            balance_text = '{:.6f}'.format(dex_total_balance)
        token['balance'].configure(text=balance_text)

        if usd_price is not None:
            usd_bal = usd_price * dex_total_balance
            token['usd_bal'].configure(text='{:.2f}$'.format(usd_bal))
    else:
        token['balance'].configure(text='0')
        token['usd_bal'].configure(text='None')


def update_btc_balance_display(token):
    btc_balance = init.t['BTC'].dex_total_balance
    usd_price = init.t['BTC'].usd_price
    token['symbol'].configure(text='BTC')

    if btc_balance is not None:
        if btc_balance >= 0.01:
            balance_text = '{:.2f}'.format(btc_balance)
        elif btc_balance > 0:
            balance_text = '{:.8f}'.format(btc_balance)
        else:
            balance_text = '0'
        token['balance'].configure(text=balance_text)

        if usd_price is not None:
            usd_bal = usd_price * btc_balance
            token['usd_bal'].configure(text='{:.2f}$'.format(usd_bal))
    else:
        token['balance'].configure(text='0')
        token['usd_bal'].configure(text='None')


if __name__ == '__main__':
    app = MyGUI()
    # app.init()
    app.refresh_gui()
    app.root.mainloop()
