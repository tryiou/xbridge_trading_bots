import re
import queue
import logging
from tkinter import ttk
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)


class BaseDataPanel(ttk.Frame):
    """Base class for data display panels with thread-safe updates and error handling"""

    def __init__(self, parent, columns: List[Tuple[str, str, int]]):
        """
        :param columns: (internal_name, display_name, width_percentage)
        """
        super().__init__(parent)
        self.columns = columns
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show='headings')
        self.scroll = ttk.Scrollbar(self, orient='vertical', command=self.tree.yview)
        # Pre-compile regex once for all instances
        self._clean_pattern = re.compile(r'[$%,\[\]]')

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

        # Initialize sorting state
        self.sort_column = None
        self.sort_ascending = True
        self.current_data = []  # Initialize current_data to empty list

        for col_id, col_name, _ in columns:
            self.tree.heading(col_id, text=col_name, command=lambda col=col_id: self._sort_column(col))
            self.tree.column(col_id, stretch=True)  # Enable proportional resizing
            
        # Trigger initial layout
        self.after(250, lambda: set_column_weights(None))

        self.tree.configure(yscrollcommand=self.scroll.set)

        # Grid layout
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.scroll.grid(row=0, column=1, sticky='ns')

        # Responsive configuration
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # Prepare update queue
        self._update_queue = queue.Queue()
        self.after(250, self._process_updates)

    def _sort_column(self, col_id):
        """Handle header button click; sort the column immediately"""
        try:
            if self.sort_column == col_id:
                self.sort_ascending = not self.sort_ascending
            else:
                self.sort_column = col_id
                self.sort_ascending = True
                                                                                                                                                                                      
            # Update column heading with sort indicator
            for col_id_, col_name, _ in self.columns:
                if col_id_ == col_id:
                    if self.sort_ascending:
                        self.tree.heading(col_id_, text=col_name + " ▲")
                    else:
                        self.tree.heading(col_id_, text=col_name + " ▼")
                else:
                    self.tree.heading(col_id_, text=col_name)
                                                                                                                                                                                      
            # Skip queue, process immediately
            if self.current_data:
                self.current_data = self._sort_data(self.current_data)
                self._redraw_tree()
        except Exception as e:
            logger.error(f"Error sorting column: {e}", exc_info=True)

    def update_data(self, items: List[Dict]):
        """Thread-safe entry point for all updates"""
        if not self.winfo_exists():
            return
        try:
            self._update_queue.put(items.copy())
        except RuntimeError:
            # Panel being destroyed - ignore
            pass
        except Exception as e:
            logger.error(f"Error updating data: {e}", exc_info=True)

    def _process_updates(self):
        """Process queued updates in main thread with error handling"""
        try:
            while not self._update_queue.empty():
                try:
                    items = self._update_queue.get_nowait()
                    items = self._sort_data(items)
                    self._safe_update(items)
                except Exception as e:
                    logger.error(f"Error processing update: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error in update processing loop: {e}", exc_info=True)
        finally:
            if self.winfo_exists():
                self.after(250, self._process_updates)

    def _safe_update(self, items: List[Dict]):
        """Sorts the items, stores them and redraws the tree with error handling."""
        try:
            items = self._sort_data(items)
            self.current_data = items
            self._redraw_tree()
        except Exception as e:
            logger.error(f"Error during safe update: {e}", exc_info=True)

    def _sort_data(self, items: List[Dict]) -> List[Dict]:
        """Sort data based on current sort column and order with error handling."""
        try:
            if not self.sort_column:
                return items  # No sort column selected

            reverse = not self.sort_ascending
            return sorted(items, key=lambda k: self._get_sort_value(k.get(self.sort_column, '')), reverse=reverse)
        except Exception as e:
            logger.error(f"Error sorting data: {e}", exc_info=True)
            return items

    def _get_sort_value(self, value):
        """
        Attempt to convert value to float for sorting. Returns tuple with type indicator and value.
        Handles None, empty strings, list values, direct float conversion, cleaned float conversion,
        then falls back to string.
        :param value: any cell value from data
        """
        try:
            # Handle None and empty strings first, sort them as negative infinity for numerical columns
            if value is None or value == '':
                return (0, float('-inf'))

            # Handle list values (e.g., for 'variation' column which can be [float_value])
            if isinstance(value, list) and len(value) > 0:
                value = value[0]
            elif isinstance(value, list) and len(value) == 0:
                return (0, float('-inf')) # Treat empty lists like None/empty string

            s_value = str(value).strip().lower()

            # Handle the string "None" explicitly, sort as negative infinity
            if s_value == "none":
                return (0, float('-inf'))

            try:
                # Attempt 1: Direct conversion to float (handles "123.45", "nan", "inf")
                float_value = float(s_value)
                return (0, float_value)
            except ValueError:
                pass # Fall through to next attempt

            # Attempt 2: Clean specific non-numeric symbols and try again
            # This regex is precise, only removing currency symbols, commas, and brackets.
            cleaned_value = self._clean_pattern.sub('', s_value)
            try:
                float_value = float(cleaned_value)
                return (0, float_value)
            except ValueError:
                # Fallback: If both numerical conversions fail, treat as a string
                # This ensures that non-numerical strings (like "BTC", "N/A", or "" from "$")
                # are correctly sorted alphabetically, not numerically.
                return (1, s_value) # Return original case for string sorting after numerical attempts
        except Exception as e:
            logger.error(f"Error getting sort value: {e}", exc_info=True)
            return (1, str(value))  # Fallback to string representation

    def _redraw_tree(self):
        """To be implemented by subclasses"""
        pass
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
        # Set initial height to 5 rows                                                                                                                                                    
        self.tree.configure(height=5)

    def _redraw_tree(self):
        """Main thread only - actual UI update"""
        self.tree.delete(*self.tree.get_children())
        display_height = max(min(len(self.current_data), 15), 5)

        for i, order in enumerate(self.current_data):
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
        ('symbol', 'Coin', 25), # Changed 'coin' to 'symbol'
        ('usd_price', 'USD Price', 20),
        ('total', 'Total', 20),
        ('free', 'Free', 20),
        ('total_usd', 'Total USD', 15)
    ]

    def __init__(self, parent):
        super().__init__(parent, self.COLUMNS)
        # Set initial height to 5 rows
        self.tree.configure(height=5)

    def _safe_update(self, items: List[Dict]):
        """Sorts the items, stores them and redraws the tree."""
        # Calculate total_usd and add it to each item before sorting
        for item in items:
            if 'total' in item and 'usd_price' in item:
                item['total_usd'] = item['total'] * item['usd_price']
            else:
                item['total_usd'] = 0.0 # Default to 0 or handle as appropriate

        super()._safe_update(items) # Call parent's _safe_update to sort and redraw

    def _redraw_tree(self):
        """Main thread only - actual UI update"""
        self.tree.delete(*self.tree.get_children())
        display_height = max(min(len(self.current_data), 15), 5)

        for i, balance in enumerate(self.current_data):
            # total_usd is now pre-calculated in _safe_update
            self.tree.insert('', 'end', values=(
                str(balance['symbol']),
                f"${balance['usd_price']:.3f}",
                f"{balance['total']:.4f}",
                f"{balance['free']:.4f}",
                f"${balance.get('total_usd', 0.0):.2f}" # Use the pre-calculated value
            ), tags=('evenrow' if i % 2 == 0 else 'oddrow',))

        self.tree.configure(height=display_height)
