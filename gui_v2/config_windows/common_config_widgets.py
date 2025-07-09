# gui_v2/config_windows/common_config_widgets.py
import tkinter as tk
from tkinter import ttk
from typing import List, Dict, Tuple, Callable, Any

class TreeviewMixin:
    """
    A mixin class providing common functionality for Treeview widgets
    in configuration windows, including creation, population, and scroll bindings.
    """

    def _setup_scroll_bindings(self, canvas: tk.Canvas) -> None:
        """
        Sets up scroll bindings for a canvas, allowing scrolling with arrow keys and mouse wheel.
        """
        if self.config_window:
            self.config_window.bind("<Up>", lambda event: self._on_key_press_scroll(event, canvas, -1))
            self.config_window.bind("<Down>", lambda event: self._on_key_press_scroll(event, canvas, 1))
            self.config_window.bind("<Prior>", lambda event: canvas.yview_scroll(-10, "units"))
            self.config_window.bind("<Next>", lambda event: canvas.yview_scroll(10, "units"))
            self.config_window.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

    def _on_key_press_scroll(self, event: tk.Event, canvas: tk.Canvas, direction: int) -> None:
        """
        Handles key press events for scrolling the canvas, avoiding interference with Treeview focus.
        """
        # Check if the focus is currently on a Treeview within this config window
        # This prevents the canvas from scrolling when the user intends to scroll the Treeview
        if self.config_window and hasattr(self, 'pairs_treeview') and self.pairs_treeview and \
           self.config_window.focus_get() == self.pairs_treeview:
            return
        if self.config_window and hasattr(self, 'sellers_treeview') and self.sellers_treeview and \
           self.config_window.focus_get() == self.sellers_treeview:
            return
        canvas.yview_scroll(direction, "units")

    def _create_scrollable_content_frame(self, parent_frame: ttk.Frame) -> Tuple[tk.Canvas, ttk.Frame]:
        """
        Creates a canvas and an inner frame for scrollable content.
        Returns the canvas and the content frame.
        """
        canvas = tk.Canvas(parent_frame)
        canvas.grid(row=0, column=0, sticky='nsew')
        parent_frame.grid_rowconfigure(0, weight=1)
        parent_frame.grid_columnconfigure(0, weight=1)

        content_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=content_frame, anchor='nw')

        self._setup_scroll_bindings(canvas)
        content_frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        content_frame.grid_columnconfigure(0, weight=1)
        # content_frame.grid_rowconfigure(1, weight=1) # This might be specific to PingPong, remove here

        return canvas, content_frame

    def _create_treeview_with_scrollbar(self, parent_frame: ttk.LabelFrame, columns: List[Tuple[str, str]],
                                        height: int = 8) -> ttk.Treeview:
        """
        Creates a ttk.Treeview widget with a vertical scrollbar.

        :param parent_frame: The parent LabelFrame for the Treeview.
        :param columns: A list of tuples (internal_name, display_name) for the columns.
        :param height: The initial height of the Treeview in rows.
        :return: The created ttk.Treeview widget.
        """
        treeview = ttk.Treeview(parent_frame, columns=[col[0] for col in columns], show='headings', height=height)

        for col_id, display_name in columns:
            treeview.heading(col_id, text=display_name)

        # Default column configurations, can be overridden by specific implementations
        for col_id, _ in columns:
            treeview.column(col_id, width=100, anchor='w') # Default width and anchor

        scrollbar = ttk.Scrollbar(parent_frame, orient="vertical", command=treeview.yview)
        treeview.configure(yscrollcommand=scrollbar.set)
        treeview.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')

        parent_frame.grid_columnconfigure(0, weight=1)
        parent_frame.grid_rowconfigure(0, weight=1)

        return treeview

    def _create_control_buttons_for_treeview(self, parent_frame: ttk.Frame,
                                             add_command: Callable, remove_command: Callable, edit_command: Callable,
                                             add_text: str = "Add", remove_text: str = "Remove", edit_text: str = "Edit") -> None:
        """
        Creates standard Add, Remove, and Edit buttons for a Treeview.
        """
        btn_frame = ttk.Frame(parent_frame)
        btn_frame.grid(row=1, column=0, padx=5, pady=5, sticky='w')
        ttk.Button(btn_frame, text=add_text, command=add_command).pack(side='left', padx=2)
        ttk.Button(btn_frame, text=remove_text, command=remove_command).pack(side='left', padx=2)
        ttk.Button(btn_frame, text=edit_text, command=edit_command).pack(side='left', padx=2)