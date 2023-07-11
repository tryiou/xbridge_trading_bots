# sudo apt install python3-tk
# from tkinter import *
import ctypes
import inspect
import threading
import time
import tkinter as tk

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


class My_gui():
    def __init__(self):
        self.root = tk.Tk()
        # window = Tk()
        self.root.title("PingPong")
        self.refresh_timer = None
        self.refresh_delay = 30
        self.mp = None
        self.started = False
        self.pairs_dict_gui = None
        self.btn_start = tk.Button(self.root, text="START", command=self.start)
        self.btn_start.grid(column=0, row=0)
        self.btn_stop = tk.Button(self.root, text="STOP", command=self.stop)
        self.btn_stop.grid(column=1, row=0)
        self.btn_stop.config(state="disabled")
        lbl_lst = []
        self.lbl_symbol = tk.Label(self.root, text="SYMBOL:")
        self.lbl_symbol.grid(column=0, row=1)
        # self.lbl_price = tk.Label(self.root, text="USD_PRICE:")
        # self.lbl_price.grid(column=1, row=1)
        self.lbl_status = tk.Label(self.root, text="STATUS:")
        self.lbl_status.grid(column=1, row=1)
        self.lbl_side = tk.Label(self.root, text="SIDE:")
        self.lbl_side.grid(column=2, row=1)
        self.lbl_side = tk.Label(self.root, text="VARIATION:")
        self.lbl_side.grid(column=4, row=1)
        # x = 0
        self.lb_orders_lst = []
        max_row = 1
        canvas_height = 20
        canvas_width = 20
        for x, pair in enumerate(config.user_pairs):
            self.lb_orders_lst.append({
                "symbol_text": pair, "symbol": tk.Label(self.root, text=pair),
                # "price": tk.Label(self.root, text="None"),
                "status": tk.Label(self.root, text="None"),
                "side": tk.Label(self.root, text="None"),
                "canvas": tk.Canvas(self.root, height=canvas_height, width=canvas_width),
                "oval": None,
                "variation": tk.Label(self.root, text="None")
            }
            )
            self.lb_orders_lst[-1]['symbol'].grid(column=0, row=x + 2)
            # self.lb_orders_lst[-1]['price'].grid(column=1, row=x + 2)
            self.lb_orders_lst[-1]['status'].grid(column=1, row=x + 2)
            self.lb_orders_lst[-1]['side'].grid(column=2, row=x + 2)
            self.lb_orders_lst[-1]['canvas'].grid(column=3, row=x + 2)
            self.lb_orders_lst[-1]['oval'] = self.lb_orders_lst[-1]['canvas'].create_oval(1, 1, canvas_width,
                                                                                          canvas_height)
            self.lb_orders_lst[-1]['variation'].grid(column=4, row=x + 2)
            max_row = x + 2

        self.lbl_bal = tk.Label(self.root, text="BALANCES:")
        self.lbl_bal.grid(column=0, row=max_row + 1)
        max_row += 1
        self.initialise()
        self.lb_bals_lst = []
        # print(init.t)
        for x, token in enumerate(init.t):
            bal = init.t[token].dex_total_balance
            if bal:
                bal = float("{:.4f}".format(bal))
            else:
                bal = 0

            # usd_bal = init.t[token].usd_price * bal
            self.lb_bals_lst.append({"symbol_text": token, "symbol": tk.Label(self.root, text=token),
                                     "balance": tk.Label(self.root, text=str(bal)),
                                     "usd_bal": tk.Label(self.root, text=str(None))})
            self.lb_bals_lst[-1]['symbol'].grid(column=x, row=max_row + 1)
            self.lb_bals_lst[-1]['balance'].grid(column=x, row=max_row + 2)
            self.lb_bals_lst[-1]['usd_bal'].grid(column=x, row=max_row + 3)
            # print(x, token, init.t[token].total_balance)
        # exit()

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
        for key, pair in init.p.items():
            for ppair in self.lb_orders_lst:
                if ppair['symbol_text'] == key:
                    # ppair['price'].configure(text="None")
                    ppair['status'].configure(text="None")
                    ppair['side'].configure(text="None")
        self.btn_stop.config(state="disabled")
        self.btn_start.config(state="active")

        # self.initialise()
        self.started = False
        print("stop done")

    def refresh(self):
        if self.started:
            if not self.send_process.is_alive():
                import definitions.xbridge_def as xb
                print("pingpong bot crashed!")
                xb.cancelallorders()
                self.btn_stop.config(state="disabled")
                self.btn_start.config(state="active")

                # self.initialise()
                self.started = False
            for key, pair in init.p.items():
                for ppair in self.lb_orders_lst:
                    if ppair['symbol_text'] == key:
                        # if pair.price:
                        #     ppair['price'].configure(text=float("{:.4f}".format(pair.price * pair.t2.usd_price)))
                        # else:
                        #     ppair['price'].configure(text='None')
                        if pair.dex_order and 'status' in pair.dex_order:
                            ppair['status'].configure(text=pair.dex_order['status'])
                            ppair['variation'].configure(text=str(pair.var))
                            if pair.dex_order['status'] == 'open':
                                ppair['canvas'].itemconfigure(ppair['oval'], fill="green")
                            elif pair.dex_order['status'] == 'new' or pair.dex_order['status'] == 'created':
                                ppair['canvas'].itemconfigure(ppair['oval'], fill="yellow")
                            elif pair.dex_order['status'] == 'accepting' or pair.dex_order['status'] == 'hold' or \
                                    pair.dex_order['status'] == 'initialized' or pair.dex_order[
                                'status'] == 'commited' or pair.dex_order['status'] == 'finished':
                                ppair['canvas'].itemconfigure(ppair['oval'], fill="dark orchid")
                        else:
                            ppair['canvas'].itemconfigure(ppair['oval'], fill="red")
                            ppair['status'].configure(text='None')
                        if pair.current_order and 'side' in pair.current_order:
                            ppair['side'].configure(text=pair.current_order['side'])
                        else:
                            if pair.disabled:
                                ppair['status'].configure(text='Disabled')
                                ppair['side'].configure(text='Disabled')
                                ppair['canvas'].itemconfigure(ppair['oval'], fill="red")
                            else:
                                ppair['status'].configure(text='None')
                                ppair['side'].configure(text='None')
                                ppair['canvas'].itemconfigure(ppair['oval'], fill="red")
                    # print(self.lb_bals_lst)
        else:
            for ppair in self.lb_orders_lst:
                ppair['status'].configure(text='None')
                ppair['side'].configure(text='None')
                ppair['canvas'].itemconfigure(ppair['oval'], fill="red")
        for key, pair in init.p.items():
            for token in self.lb_bals_lst:
                if token['symbol_text'] == pair.t1.symbol:
                    if pair.t1.usd_price:
                        token['symbol'].configure(text=pair.t1.symbol + str(["{:.2f}".format(pair.t1.usd_price)]))
                    if pair.t1.dex_total_balance:
                        if float(pair.t1.dex_total_balance) >= 1:
                            token['balance'].configure(text="{:.2f}".format(pair.t1.dex_total_balance))
                        else:
                            token['balance'].configure(text="{:.6f}".format(pair.t1.dex_total_balance))
                        if init.t[pair.t1.symbol].usd_price:
                            usd_bal = init.t[pair.t1.symbol].usd_price * pair.t1.dex_total_balance
                            token['usd_bal'].configure(text="{:.2f}".format(usd_bal) + "$")
                elif token['symbol_text'] == pair.t2.symbol:
                    if pair.t2.usd_price:
                        token['symbol'].configure(text=pair.t2.symbol + str(["{:.2f}".format(pair.t2.usd_price)]))
                    if pair.t2.dex_total_balance:
                        if float(pair.t2.dex_total_balance) >= 1:
                            token['balance'].configure(text="{:.2f}".format(pair.t2.dex_total_balance))
                        else:
                            token['balance'].configure(text="{:.6f}".format(pair.t2.dex_total_balance))
                        if init.t[pair.t2.symbol].usd_price:
                            usd_bal = init.t[pair.t2.symbol].usd_price * pair.t2.dex_total_balance
                            token['usd_bal'].configure(text="{:.2f}".format(usd_bal) + "$")
                elif token['symbol_text'] == 'BTC':
                    if init.t['BTC'].dex_total_balance:
                        if init.t['BTC'].usd_price:
                            token['symbol'].configure(text='BTC' + str(["{:.2f}".format(init.t['BTC'].usd_price)]))
                        if init.t['BTC'].dex_total_balance >= 1:
                            token['balance'].configure(text=float("{:.2f}".format(init.t['BTC'].dex_total_balance)))
                        else:
                            token['balance'].configure(text=float("{:.8f}".format(init.t['BTC'].dex_total_balance)))
                        if init.t['BTC'].usd_price:
                            usd_bal = init.t['BTC'].usd_price * init.t['BTC'].dex_total_balance
                            token['usd_bal'].configure(text="{:.2f}".format(usd_bal) + "$")

                    # print("BLA BLA", token["balance"].cget("text"))
                    # exit()

            # self.refresh_timer = time.time()
        self.root.after(1500, self.refresh)


if __name__ == '__main__':
    app = My_gui()
    # app.init()
    app.refresh()
    app.root.mainloop()
