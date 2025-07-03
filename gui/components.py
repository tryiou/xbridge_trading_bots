# gui/components.py
import re
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .frames import GUI_Config_PingPong, GUI_Config_BasicSeller


class BaseDialog(tk.Toplevel):
    """Base class for configuration dialogs."""

    def __init__(self, parent, config, title):
        super().__init__(parent)
        self.title(title)
        self.result = None
        self.config = config
        self.transient(parent)
        # Force the window to be drawn and handle pending events before grabbing.
        # This is a more reliable way to prevent the "window not viewable" error
        # than self.after_idle(), which can sometimes fire too early.
        self.update_idletasks()
        self.grab_set()

    def _validate_pair(self, pair_var):
        pair = pair_var.get().strip().upper()
        if not re.match(r"^[A-Z]{2,}/[A-Z]{2,}$", pair):
            self.config.update_status("Invalid pair format. Must be TOKEN1/TOKEN2 (both tokens 2+ chars)", 'red')
            return False
        return True

    def _validate_numeric(self, *string_vars):
        try:
            for var in string_vars:
                float(var.get())
            return True
        except ValueError as e:
            self.config.update_status(f"Invalid numeric value: {str(e)}", 'red')
            return False


class BasePairDialog(BaseDialog):
    """Base dialog for adding/editing PingPong pairs."""

    def __init__(self, parent, config: 'GUI_Config_PingPong', title: str, values: tuple | None = None):
        super().__init__(parent, config, title)

        # Initialize variables with defaults for "add" or provided values for "edit"
        self.enabled_var = tk.BooleanVar(value=True if values is None else values[1] == 'Yes')
        self.name_var = tk.StringVar(value="" if values is None else values[0])
        self.pair_var = tk.StringVar(value="" if values is None else values[2])
        self.var_tol_var = tk.StringVar(value="0.02" if values is None else values[3])
        self.sell_offset_var = tk.StringVar(value="0.05" if values is None else values[4])
        self.usd_amt_var = tk.StringVar(value="0.5" if values is None else values[5])
        self.spread_var = tk.StringVar(value="0.1" if values is None else values[6])

        self._create_widgets()

    def _create_widgets(self):
        ttk.Checkbutton(self, text="Enabled", variable=self.enabled_var).grid(row=0, column=0, padx=5, pady=2,
                                                                              sticky='w')
        ttk.Label(self, text="Name:").grid(row=1, column=0, padx=5, pady=2, sticky='w')
        self.name_entry = ttk.Entry(self, textvariable=self.name_var)
        self.name_entry.grid(row=1, column=1, padx=5, pady=2)
        ttk.Label(self, text="Pair:").grid(row=2, column=0, padx=5, pady=2, sticky='w')
        self.pair_entry = ttk.Entry(self, textvariable=self.pair_var)
        self.pair_entry.grid(row=2, column=1, padx=5, pady=2)
        ttk.Label(self, text="Price Variation Tolerance:").grid(row=3, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.var_tol_var).grid(row=3, column=1, padx=5, pady=2)
        ttk.Label(self, text="Sell Price Offset:").grid(row=4, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.sell_offset_var).grid(row=4, column=1, padx=5, pady=2)
        ttk.Label(self, text="USD Amount:").grid(row=5, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.usd_amt_var).grid(row=5, column=1, padx=5, pady=2)
        ttk.Label(self, text="Spread:").grid(row=6, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.spread_var).grid(row=6, column=1, padx=5, pady=2)

    def _create_buttons(self, ok_text: str, ok_command: callable):
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=7, column=0, columnspan=2, pady=5)
        ttk.Button(btn_frame, text=ok_text, command=ok_command).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side='left', padx=5)
        self.bind('<Return>', lambda event: ok_command())
        self.bind('<Escape>', lambda event: self.destroy())

    def _get_values(self):
        return (
            self.name_var.get(),
            'Yes' if self.enabled_var.get() else 'No',
            self.pair_var.get().strip().upper(),
            float(self.var_tol_var.get()),
            float(self.sell_offset_var.get()),
            float(self.usd_amt_var.get()),
            float(self.spread_var.get())
        )


class AddPairDialog(BasePairDialog):
    def __init__(self, parent, config: 'GUI_Config_PingPong'):
        super().__init__(parent, config, "Add New Pair")
        self._create_buttons("Add", self.on_add)

    def on_add(self):
        if not self._validate_pair(self.pair_var) or not self._validate_numeric(
                self.var_tol_var, self.sell_offset_var, self.usd_amt_var, self.spread_var):
            return
        self.result = self._get_values()
        self.destroy()


class PairConfigDialog(BasePairDialog):
    def __init__(self, parent: tk.Toplevel, values: tuple, config: 'GUI_Config_PingPong') -> None:
        super().__init__(parent, config, "Edit Pair Configuration", values=values)
        self.pair_entry.config(state='readonly')
        self._create_buttons("Save", self.on_save)

    def on_save(self) -> None:
        if not self._validate_numeric(
                self.var_tol_var, self.sell_offset_var, self.usd_amt_var, self.spread_var):
            self.result = None
            return
        self.result = self._get_values()
        self.destroy()


class BaseSellerDialog(BaseDialog):
    """Base dialog for adding/editing BasicSeller instances."""

    def __init__(self, parent, config: 'GUI_Config_BasicSeller', title: str, values: tuple | None = None):
        super().__init__(parent, config, title)

        self.enabled_var = tk.BooleanVar(value=True if values is None else values[1] == 'Yes')
        self.name_var = tk.StringVar(value="" if values is None else values[0])
        self.pair_var = tk.StringVar(value="" if values is None else values[2])
        self.amount_var = tk.StringVar(value="100.0" if values is None else values[3])
        self.min_price_var = tk.StringVar(value="0.01" if values is None else values[4])
        self.offset_var = tk.StringVar(value="0.015" if values is None else values[5])

        self._create_widgets()

    def _create_widgets(self):
        ttk.Checkbutton(self, text="Enabled", variable=self.enabled_var).grid(row=0, column=0, padx=5, pady=2,
                                                                              sticky='w')
        ttk.Label(self, text="Name:").grid(row=1, column=0, padx=5, pady=2, sticky='w')
        self.name_entry = ttk.Entry(self, textvariable=self.name_var)
        self.name_entry.grid(row=1, column=1, padx=5, pady=2)
        ttk.Label(self, text="Pair (MAKER/TAKER):").grid(row=2, column=0, padx=5, pady=2, sticky='w')
        self.pair_entry = ttk.Entry(self, textvariable=self.pair_var)
        self.pair_entry.grid(row=2, column=1, padx=5, pady=2)
        ttk.Label(self, text="Amount to Sell:").grid(row=3, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.amount_var).grid(row=3, column=1, padx=5, pady=2)
        ttk.Label(self, text="Min Sell Price (USD):").grid(row=4, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.min_price_var).grid(row=4, column=1, padx=5, pady=2)
        ttk.Label(self, text="Sell Price Offset:").grid(row=5, column=0, padx=5, pady=2, sticky='w')
        ttk.Entry(self, textvariable=self.offset_var).grid(row=5, column=1, padx=5, pady=2)

    def _create_buttons(self, ok_text: str, ok_command: callable):
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=6, column=0, columnspan=2, pady=5)
        ttk.Button(btn_frame, text=ok_text, command=ok_command).pack(side='left', padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(side='left', padx=5)
        self.bind('<Return>', lambda event: ok_command())
        self.bind('<Escape>', lambda event: self.destroy())

    def _get_values(self):
        return (
            self.name_var.get(),
            'Yes' if self.enabled_var.get() else 'No',
            self.pair_var.get().strip().upper(),
            float(self.amount_var.get()),
            float(self.min_price_var.get()),
            float(self.offset_var.get())
        )


class AddSellerDialog(BaseSellerDialog):
    def __init__(self, parent, config: 'GUI_Config_BasicSeller'):
        super().__init__(parent, config, "Add New Seller Instance")
        self._create_buttons("Add", self.on_add)

    def on_add(self):
        if not self._validate_pair(self.pair_var) or not self._validate_numeric(
                self.amount_var, self.min_price_var, self.offset_var):
            return
        self.result = self._get_values()
        self.destroy()


class SellerConfigDialog(BaseSellerDialog):
    def __init__(self, parent: tk.Toplevel, values: tuple, config: 'GUI_Config_BasicSeller') -> None:
        super().__init__(parent, config, "Edit Seller Instance", values=values)
        self.pair_entry.config(state='readonly')
        self._create_buttons("Save", self.on_save)

    def on_save(self) -> None:
        if not self._validate_numeric(self.amount_var, self.min_price_var, self.offset_var):
            self.result = None
            return
        self.result = self._get_values()
        self.destroy()
