# gui/components.py
import re
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .frames import GUI_Config


class AddPairDialog(tk.Toplevel):
    def __init__(self, parent, config: 'GUI_Config'):
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
    def __init__(self, parent: tk.Toplevel, values: tuple, config: 'GUI_Config') -> None:
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