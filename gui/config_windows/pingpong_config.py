# gui/config_windows/pingpong_config.py
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Dict, Any, List, Optional

from gui.config_windows.base_config_window import BaseConfigWindow
from gui.config_windows.common_config_widgets import TreeviewMixin
from gui.components.dialogs import AddPairDialog, PairConfigDialog

if TYPE_CHECKING:
    from gui.frames.base_frames import BaseStrategyFrame


class GUI_Config_PingPong(BaseConfigWindow, TreeviewMixin):
    """
    Manages the configuration window for the PingPong bot settings.
    """

    def __init__(self, parent: "BaseStrategyFrame") -> None:
        super().__init__(parent)
        self.debug_level_entry: ttk.Entry | None = None
        self.ttk_theme_entry: ttk.Entry | None = None
        self.pairs_treeview: ttk.Treeview | None = None

    def _create_widgets(self, parent_frame: ttk.Frame):
        canvas, content_frame = self._create_scrollable_content_frame(parent_frame)
        
        self._create_general_settings_widgets(content_frame)
        self._create_pairs_treeview_widgets(content_frame)

    def _create_general_settings_widgets(self, parent_frame: ttk.Frame) -> None:
        """Creates widgets for general settings like debug level and theme."""
        general_frame = ttk.LabelFrame(parent_frame, text="General Settings")
        general_frame.grid(row=0, column=0, padx=5, pady=5, sticky='ew')
        general_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(general_frame, text="Debug Level:").grid(row=0, column=0, padx=5, pady=5, sticky='w')
        self.debug_level_entry = ttk.Entry(general_frame)
        self.debug_level_entry.grid(row=0, column=1, padx=5, pady=5, sticky='ew')
        if self.parent.config_manager and self.parent.config_manager.config_pingpong:
            self.debug_level_entry.insert(0, str(self.parent.config_manager.config_pingpong.debug_level))

        ttk.Label(general_frame, text="TTK Theme:").grid(row=1, column=0, padx=5, pady=5, sticky='w')
        self.ttk_theme_entry = ttk.Entry(general_frame)
        self.ttk_theme_entry.grid(row=1, column=1, padx=5, pady=5, sticky='ew')
        if self.parent.config_manager and self.parent.config_manager.config_pingpong:
            self.ttk_theme_entry.insert(0, self.parent.config_manager.config_pingpong.ttk_theme)

    def _create_pairs_treeview_widgets(self, parent_frame: ttk.Frame) -> None:
        """Creates the Treeview for displaying and managing pair configurations."""
        tree_frame = ttk.LabelFrame(parent_frame, text="Pair Configurations")
        tree_frame.grid(row=1, column=0, padx=5, pady=5, sticky='nsew')
        parent_frame.grid_rowconfigure(1, weight=1) # Allow this section to expand

        columns = [
            ('name', 'Name'), ('enabled', 'Enabled'), ('pair', 'Pair'),
            ('price_variation_tolerance', 'Var. Tol.'), ('sell_price_offset', 'Sell Offset'),
            ('usd_amount', 'USD Amt'), ('spread', 'Spread')
        ]
        self.pairs_treeview = self._create_treeview_with_scrollbar(tree_frame, columns, height=8)

        # Specific column configurations for PingPong pairs
        col_configs = {
            'name': (150, 'w'), 'enabled': (75, 'center'), 'pair': (150, 'w'),
            'price_variation_tolerance': (120, 'e'), 'sell_price_offset': (120, 'e'),
            'usd_amount': (120, 'e'), 'spread': (120, 'e')
        }
        for col, (width, anchor) in col_configs.items():
            self.pairs_treeview.column(col, width=width, anchor=anchor)

        self.pairs_treeview.bind("<Double-1>", lambda event: self.edit_pair_config())
        self._populate_pairs_treeview()

    def _populate_pairs_treeview(self) -> None:
        """Populates the pairs Treeview with data from the configuration manager."""
        if self.pairs_treeview and self.parent.config_manager and self.parent.config_manager.config_pingpong:
            for cfg in self.parent.config_manager.config_pingpong.pair_configs:
                self.pairs_treeview.insert('', 'end', values=(
                    cfg.get('name', ''),
                    'Yes' if cfg.get('enabled', True) else 'No',
                    cfg['pair'],
                    cfg.get('price_variation_tolerance', 0.02),
                    cfg.get('sell_price_offset', 0.05),
                    cfg.get('usd_amount', 0.5),
                    cfg.get('spread', 0.1)
                ))

    def _create_control_buttons_area(self, parent_frame: ttk.Frame) -> None:
        """Creates control buttons (Add, Remove, Edit) for pair configurations."""
        self._create_control_buttons_for_treeview(
            parent_frame,
            add_command=self.add_pair_config,
            remove_command=self.remove_pair_config,
            edit_command=self.edit_pair_config,
            add_text="Add Pair",
            remove_text="Remove Pair",
            edit_text="Edit Config"
        )

    def add_pair_config(self) -> None:
        """Opens a dialog to add a new pair configuration."""
        if not self.config_window:
            return
        dialog = self._open_single_dialog(AddPairDialog, self)

        if not self.config_window or not self.config_window.winfo_exists():
            return

        if dialog.result and self.pairs_treeview:
            self.pairs_treeview.insert('', 'end', values=dialog.result)
            self.update_status(f"Pair {dialog.result[2]} added successfully.", 'lightgreen')

    def remove_pair_config(self) -> None:
        """Removes the selected pair configuration from the Treeview."""
        if not self.pairs_treeview:
            return
        selected = self.pairs_treeview.selection()
        if selected:
            self.pairs_treeview.delete(selected)
            self.update_status("Selected pair removed.", 'lightgray')

    def edit_pair_config(self) -> None:
        """Opens a dialog to edit the selected pair configuration."""
        if not self.pairs_treeview:
            return
        selected = self.pairs_treeview.selection()
        if selected:
            values = self.pairs_treeview.item(selected, 'values')
            dialog = self._open_single_dialog(PairConfigDialog, values, self)

            if not self.config_window or not self.config_window.winfo_exists():
                return

            if dialog.result:
                self.pairs_treeview.item(selected, values=dialog.result)
                self.update_status(f"Pair {dialog.result[2]} updated successfully.", 'lightgreen')
            else:
                self.update_status("Edit cancelled.", 'lightgray')

    def _set_window_geometry(self) -> None:
        """Sets the initial size and minimum size of the configuration window."""
        if self.config_window:
            x, y = 900, 450
            self.config_window.minsize(x, y)
            self.config_window.geometry(f"{x}x{y}")

    def _get_config_data_to_save(self) -> Dict[str, Any] | None:
        """
        Collects all configuration data from the widgets and returns it as a dictionary
        ready for saving to a YAML file.
        """
        pair_configs = []
        if self.pairs_treeview:
            for item_id in self.pairs_treeview.get_children():
                values = self.pairs_treeview.item(item_id, 'values')
                try:
                    pair_configs.append({
                        'name': values[0],
                        'enabled': values[1] == 'Yes',
                        'pair': values[2],
                        'price_variation_tolerance': float(values[3]),
                        'sell_price_offset': float(values[4]),
                        'usd_amount': float(values[5]),
                        'spread': float(values[6])
                    })
                except (ValueError, IndexError) as e:
                    self.update_status(f"Invalid numeric value in pair config: {e}", 'red')
                    if self.parent.config_manager:
                        self.parent.config_manager.general_log.error(f"Failed to parse pair config: {e}")
                    return None

        new_config = {
            'debug_level': int(self.debug_level_entry.get()) if self.debug_level_entry else 0,
            'ttk_theme': self.ttk_theme_entry.get() if self.ttk_theme_entry else 'flatly',
            'pair_configs': pair_configs
        }
        return new_config