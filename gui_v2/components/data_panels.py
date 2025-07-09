import queue
from tkinter import ttk
from typing import List, Dict, Tuple


class BaseDataPanel(ttk.Frame):
    """Base class for data display panels with thread-safe updates"""

    def __init__(self, parent, columns: List[Tuple[str, str, int]]):
        """                                                                                                                                                                               
        :param columns: (internal_name, display_name, width_percentage)                                                                                                                   
        """
        super().__init__(parent)
        self.columns = columns
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show='headings')
        self.scroll = ttk.Scrollbar(self, orient='vertical', command=self.tree.yview)

        # Configure zebra striping
        self.tree.tag_configure('evenrow', background='#333333')
        self.tree.tag_configure('oddrow', background='#404040')

        # Configure tree columns with percentage-based weights
        self.columns = columns  # Store columns for resize handling
        total_weight = sum(col[2] for col in columns)
        
        # Set initial widths based on current window size
        def set_column_weights(event=None):
            width = self.tree.winfo_width()
            for col_id, _, weight in self.columns:
                self.tree.column(col_id,
                               width=int(width * weight/total_weight),
                               stretch=True)

        # Bind resize event and do initial configuration
        self.tree.bind('<Configure>', set_column_weights)
        for col_id, col_name, _ in columns:
            self.tree.heading(col_id, text=col_name)
            self.tree.column(col_id, stretch=True)  # Enable proportional resizing
            
        # Trigger initial layout
        self.after(100, lambda: set_column_weights(None))

        self.tree.configure(yscrollcommand=self.scroll.set)

        # Grid layout                                                                                                                                                                     
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.scroll.grid(row=0, column=1, sticky='ns')

        # Responsive configuration                                                                                                                                                        
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Prepare update queue
        self._update_queue = queue.Queue()
        self.after(100, self._process_updates)

    def update_data(self, items: List[Dict]):
        """Thread-safe entry point for all updates"""
        if not self.winfo_exists():
            return
        try:
            self._update_queue.put(items.copy())
        except RuntimeError:
            # Panel being destroyed - ignore
            pass

    def _process_updates(self):
        """Process queued updates in main thread"""
        try:
            while not self._update_queue.empty():
                items = self._update_queue.get_nowait()
                self._safe_update(items)
        except Exception as e:
            # Logging handled through parent frame's config manager 
            pass  # Errors are already logged in the strategy thread
        finally:
            self.after(250, self._process_updates)

    def _safe_update(self, items: List[Dict]):
        """To be implemented by subclasses"""
        pass


class OrdersPanel(BaseDataPanel):
    """Replacement for original GUI_Orders"""
    COLUMNS = [
        ('name', 'Name', 11),
        ('pair', 'Pair', 10),
        ('status', 'Status', 5), 
        ('side', 'Side', 5),
        ('flag', 'Flag', 4),
        ('variation', 'Variation', 6),
        ('maker_size', 'Maker Amount', 10),
        ('maker', 'Maker', 5),
        ('taker_size', 'Taker Amount', 10),
        ('taker', 'Taker', 5),
        ('dex_price', 'Price', 9),
        ('order_id', 'Order ID', 20)
    ]

    def __init__(self, parent):
        super().__init__(parent, self.COLUMNS)
        # Set initial height to 5 rows                                                                                                                                                    
        self.tree.configure(height=5)

    def _safe_update(self, orders: List[Dict]):
        """Main thread only - actual UI update"""
        self.tree.delete(*self.tree.get_children())
        display_height = max(min(len(orders), 15), 5)
        
        for i, order in enumerate(orders):
            self.tree.insert('', 'end', values=(
                order['name'],
                order['symbol'],
                order.get('status', 'None'),
                order.get('side', 'None'),
                order.get('flag', 'X'),
                order.get('variation', 'None'),
                order.get('maker_size', 'None'),
                order.get('maker', 'None'),
                order.get('taker_size', 'None'),
                order.get('taker', 'None'),
                order.get('dex_price', 'None'),
                order.get('order_id', 'None'),
            ), tags=('evenrow' if i % 2 == 0 else 'oddrow',))

        self.tree.configure(height=display_height)


class BalancesPanel(BaseDataPanel):
    """Replacement for original GUI_Balances"""
    COLUMNS = [
        ('coin', 'Coin', 25),
        ('usd_price', 'USD Price', 20),
        ('total', 'Total', 20),
        ('free', 'Free', 20),
        ('total_usd', 'Total USD', 15)
    ]

    def __init__(self, parent):
        super().__init__(parent, self.COLUMNS)
        # Set initial height to 5 rows
        self.tree.configure(height=5)

    def _safe_update(self, balances: List[Dict]):
        """Main thread only - actual UI update"""
        self.tree.delete(*self.tree.get_children())
        display_height = max(min(len(balances), 15), 5)

        for i, balance in enumerate(balances):
            self.tree.insert('', 'end', values=(
                balance['symbol'],
                f"${balance['usd_price']:.3f}",
                f"{balance['total']:.4f}",
                f"{balance['free']:.4f}",
                f"${balance['total'] * balance['usd_price']:.2f}"
            ), tags=('evenrow' if i % 2 == 0 else 'oddrow',))

        self.tree.configure(height=display_height)
