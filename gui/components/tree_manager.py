# gui/components/tree_manager.py
import logging
import queue
import re
from tkinter import ttk
from typing import List, Dict, Tuple, Any, Callable

logger = logging.getLogger(__name__)


class TreeManager:
    """
    Manages a ttk.Treeview with thread-safe updates, sorting, and column management.
    """

    def __init__(self,
                 parent_frame: ttk.Frame,
                 columns: List[Tuple[str, str, int]],
                 redraw_callback: Callable):
        self.parent = parent_frame
        self.columns = columns
        self.redraw_callback = redraw_callback
        self.current_data: List[Dict] = []

        self.tree = ttk.Treeview(self.parent, columns=[c[0] for c in columns], show='headings')
        self.scroll = ttk.Scrollbar(self.parent, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scroll.set)

        self._clean_pattern = re.compile(r'[$%,\[\]]')
        self._setup_tree()

        self.sort_column: str | None = None
        self.sort_ascending: bool = True

        self._update_queue = queue.Queue()
        self._after_id: str | None = None
        self._is_running: bool = False
        self.parent.bind('<Destroy>', self._stop, add='+')
        self._start()

    def grid(self, **kwargs):
        """Grids the tree and scrollbar within the parent frame."""
        self.tree.grid(row=0, column=0, sticky='nsew', **kwargs)
        self.scroll.grid(row=0, column=1, sticky='ns', **kwargs)
        self.parent.grid_columnconfigure(0, weight=1)
        self.parent.grid_rowconfigure(0, weight=1)

    def _start(self):
        if not self._is_running:
            self._is_running = True
            self._after_id = self.parent.after(250, self._process_updates)

    def _stop(self, event=None):
        self._is_running = False
        if self._after_id:
            try:
                self.parent.after_cancel(self._after_id)
            except Exception:
                pass
        self._after_id = None

    def _setup_tree(self):
        """Configures tree columns, bindings, and appearance."""
        self.tree.tag_configure('evenrow', background='#333333')
        self.tree.tag_configure('oddrow', background='#404040')

        total_weight = sum(col[2] for col in self.columns)

        def set_column_weights(event=None):
            width = self.tree.winfo_width()
            if width <= 1: return
            for col_id, _, weight in self.columns:
                self.tree.column(col_id, width=int(width * (weight / total_weight)), stretch=True)

        self.tree.bind('<Configure>', set_column_weights)

        for col_id, col_name, _ in self.columns:
            self.tree.heading(col_id, text=col_name, command=lambda c=col_id: self._sort_column(c))

        self.parent.after(100, set_column_weights)

    def queue_update(self, data: List[Dict]):
        """Thread-safe entry point to update data."""
        if not self._is_running or not self.parent.winfo_exists():
            return
        try:
            self._update_queue.put(data)
        except RuntimeError:
            pass

    def _process_updates(self):
        """Processes queued updates in the main GUI thread."""
        try:
            items = None
            while not self._update_queue.empty():
                items = self._update_queue.get_nowait()

            if items is not None:
                self.current_data = self._sort_data(items)
                self.redraw_callback()

        except queue.Empty:
            pass
        finally:
            if self._is_running and self.parent.winfo_exists():
                self._after_id = self.parent.after(250, self._process_updates)

    def _sort_column(self, col_id: str):
        """Handles column header click for sorting."""
        if self.sort_column == col_id:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_column = col_id
            self.sort_ascending = True

        for col_id_, col_name, _ in self.columns:
            text = col_name
            if col_id_ == col_id:
                text += " ▲" if self.sort_ascending else " ▼"
            self.tree.heading(col_id_, text=text)

        self.current_data = self._sort_data(self.current_data)
        self.redraw_callback()

    def _sort_data(self, items: List[Dict]) -> List[Dict]:
        """Sorts data based on current sorting configuration."""
        if not self.sort_column or not items:
            return items
        return sorted(items, key=lambda k: self._get_sort_value(k.get(self.sort_column, '')),
                      reverse=not self.sort_ascending)

    def _get_sort_value(self, value):
        """
        Attempt to convert value to float for sorting. Returns tuple with type indicator and value.
        Handles None, empty strings, list values, direct float conversion, cleaned float conversion,
        then falls back to string.
        """
        try:
            if value is None or value == '':
                return (0, float('-inf'))
            if isinstance(value, list) and len(value) > 0:
                value = value[0]
            elif isinstance(value, list) and len(value) == 0:
                return (0, float('-inf'))
            s_value = str(value).strip().lower()
            if s_value == "none":
                return (0, float('-inf'))
            try:
                float_value = float(s_value)
                return (0, float_value)
            except ValueError:
                pass
            cleaned_value = self._clean_pattern.sub('', s_value)
            try:
                float_value = float(cleaned_value)
                return (0, float_value)
            except ValueError:
                return (1, s_value)
        except Exception as e:
            logger.error(f"Error getting sort value: {e}", exc_info=True)
            return (1, str(value))
