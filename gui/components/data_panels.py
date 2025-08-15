import logging
from tkinter import ttk
from typing import List, Dict

from .tree_manager import TreeManager

logger = logging.getLogger(__name__)


class BaseDataPanel(ttk.Frame):
    """Base class for data display panels using TreeManager."""

    def __init__(self, parent, columns):
        super().__init__(parent)
        self.tree_manager = TreeManager(self, columns, self._redraw_tree)
        self.tree_manager.grid()

    def update_data(self, items: List[Dict]):
        """Thread-safe entry point for all updates."""
        self.tree_manager.queue_update(items.copy())

    def _redraw_tree(self):
        """To be implemented by subclasses."""
        raise NotImplementedError


class OrdersPanel(BaseDataPanel):
    """Replacement for original GUI_Orders"""
    COLUMNS = [
        ('name', 'Name', 11),
        ('symbol', 'Symbol', 10),
        ('status', 'Status', 6),
        ('side', 'Side', 5),
        ('flag', 'Flag', 5),
        ('variation', 'Variation', 7),
        ('maker_size', 'Maker Amount', 10),
        ('maker', 'Maker', 6),
        ('taker_size', 'Taker Amount', 10),
        ('taker', 'Taker', 6),
        ('dex_price', 'Price', 9),
        ('order_id', 'Order ID', 15)
    ]

    def __init__(self, parent):
        super().__init__(parent, self.COLUMNS)
        self.tree_manager.tree.configure(height=5)

    def _redraw_tree(self):
        """Main thread only - actual UI update"""
        tree = self.tree_manager.tree
        data = self.tree_manager.current_data

        tree.delete(*tree.get_children())
        display_height = max(min(len(data), 15), 5)

        for i, order in enumerate(data):
            tree.insert('', 'end', values=(
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

        tree.configure(height=display_height)


class BalancesPanel(BaseDataPanel):
    """Replacement for original GUI_Balances"""
    COLUMNS = [
        ('symbol', 'Coin', 25),
        ('usd_price', 'USD Price', 20),
        ('total', 'Total', 20),
        ('free', 'Free', 20),
        ('total_usd', 'Total USD', 15)
    ]

    def __init__(self, parent):
        super().__init__(parent, self.COLUMNS)
        self.tree_manager.tree.configure(height=5)

    def update_data(self, items: List[Dict]):
        """Calculates total USD value before queueing the update."""
        items_copy = [item.copy() for item in items]
        for item in items_copy:
            if 'total' in item and 'usd_price' in item:
                item['total_usd'] = item['total'] * item['usd_price']
            else:
                item['total_usd'] = 0.0
        super().update_data(items_copy)

    def _redraw_tree(self):
        """Main thread only - actual UI update"""
        tree = self.tree_manager.tree
        data = self.tree_manager.current_data

        tree.delete(*tree.get_children())
        display_height = max(min(len(data), 15), 5)

        for i, balance in enumerate(data):
            tree.insert('', 'end', values=(
                str(balance['symbol']),
                f"${balance['usd_price']:.3f}",
                f"{balance['total']:.4f}",
                f"{balance['free']:.4f}",
                f"${balance.get('total_usd', 0.0):.2f}"
            ), tags=('evenrow' if i % 2 == 0 else 'oddrow',))

        tree.configure(height=display_height)
