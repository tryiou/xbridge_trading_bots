# gui/config_windows/basicseller_config.py
from tkinter import ttk
from typing import TYPE_CHECKING, Dict, Any

from gui.components.dialogs import AddSellerDialog, SellerConfigDialog
from gui.config_windows.base_config_window import BaseConfigWindow
from gui.config_windows.common_config_widgets import TreeviewMixin

if TYPE_CHECKING:
    from gui.frames.base_frames import BaseStrategyFrame


class GUI_Config_BasicSeller(BaseConfigWindow, TreeviewMixin):
    """
    Manages the configuration window for the Basic Seller bot settings.
    """

    def __init__(self, parent: "BaseStrategyFrame"):
        super().__init__(parent)
        self.sellers_treeview: ttk.Treeview | None = None

    def _create_widgets(self, parent_frame: ttk.Frame):
        content_frame = ttk.Frame(parent_frame)
        content_frame.grid(row=0, column=0, sticky='nsew')
        content_frame.grid_rowconfigure(0, weight=1)
        content_frame.grid_columnconfigure(0, weight=1)

        self._create_sellers_treeview(content_frame)

    def _create_sellers_treeview(self, parent_frame: ttk.Frame) -> None:
        """Creates the Treeview for displaying and managing seller configurations."""
        tree_frame = ttk.LabelFrame(parent_frame, text="Seller Configurations")
        tree_frame.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        columns = [
            ('name', 'Name'), ('enabled', 'Enabled'), ('pair', 'Pair'),
            ('amount_to_sell', 'Amount'), ('min_sell_price_usd', 'Min Price (USD)'),
            ('sell_price_offset', 'Offset')
        ]
        self.sellers_treeview = self._create_treeview_with_scrollbar(tree_frame, columns, height=10)

        # Specific column configurations for Basic Seller
        col_configs = {
            'name': (150, 'w'), 'enabled': (75, 'center'), 'pair': (150, 'w'),
            'amount_to_sell': (120, 'e'), 'min_sell_price_usd': (150, 'e'),
            'sell_price_offset': (120, 'e')
        }
        for col, (width, anchor) in col_configs.items():
            self.sellers_treeview.column(col, width=width, anchor=anchor)

        self.sellers_treeview.bind("<Double-1>", lambda event: self.edit_seller_config())
        self._populate_sellers_treeview()

    def _populate_sellers_treeview(self):
        """Populates the sellers Treeview with data from the configuration manager."""
        if self.sellers_treeview and self.parent.config_manager and self.parent.config_manager.config_basicseller:
            for cfg in self.parent.config_manager.config_basicseller.seller_configs:
                self.sellers_treeview.insert('', 'end', values=(
                    cfg.get('name', ''),
                    'Yes' if cfg.get('enabled', True) else 'No',
                    cfg.get('pair', 'N/A'),
                    cfg.get('amount_to_sell', 0.0),
                    cfg.get('min_sell_price_usd', 0.0),
                    cfg.get('sell_price_offset', 0.0)
                ))

    def _create_control_buttons_area(self, parent_frame: ttk.Frame):
        """Creates control buttons (Add, Remove, Edit) for seller configurations."""
        self._create_control_buttons_for_treeview(
            parent_frame,
            add_command=self.add_seller_config,
            remove_command=self.remove_seller_config,
            edit_command=self.edit_seller_config,
            add_text="Add Seller",
            remove_text="Remove Seller",
            edit_text="Edit Seller"
        )

    def _set_window_geometry(self) -> None:
        """Sets the initial size and minimum size of the configuration window."""
        if self.config_window:
            x, y = 800, 400
            self.config_window.minsize(x, y)
            self.config_window.geometry(f"{x}x{y}")

    def add_seller_config(self):
        """Opens a dialog to add a new seller configuration."""
        dialog = self._open_single_dialog(AddSellerDialog, self)
        if dialog.result and self.sellers_treeview:
            self.sellers_treeview.insert('', 'end', values=dialog.result)
            self.update_status(f"Seller {dialog.result[2]} added successfully.", 'lightgreen')

    def remove_seller_config(self):
        """Removes the selected seller configuration from the Treeview."""
        if self.sellers_treeview:
            selected = self.sellers_treeview.selection()
            if selected:
                self.sellers_treeview.delete(selected)
                self.update_status("Selected seller removed.", 'lightgray')

    def edit_seller_config(self):
        """Opens a dialog to edit the selected seller configuration."""
        if self.sellers_treeview:
            selected = self.sellers_treeview.selection()
            if selected:
                values = self.sellers_treeview.item(selected, 'values')
                dialog = self._open_single_dialog(SellerConfigDialog, values, self)
                if dialog.result:
                    self.sellers_treeview.item(selected, values=dialog.result)
                    self.update_status(f"Seller {dialog.result[2]} updated successfully.", 'lightgreen')
                else:
                    self.update_status("Edit cancelled.", 'lightgray')

    def _get_config_data_to_save(self) -> Dict[str, Any] | None:
        """
        Collects all configuration data from the widgets and returns it as a dictionary
        ready for saving to a YAML file.
        """
        seller_configs = []
        if self.sellers_treeview:
            for item_id in self.sellers_treeview.get_children():
                values = self.sellers_treeview.item(item_id, 'values')
                try:
                    seller_configs.append({
                        'name': values[0],
                        'enabled': values[1] == 'Yes',
                        'pair': values[2],
                        'amount_to_sell': float(values[3]),
                        'min_sell_price_usd': float(values[4]),
                        'sell_price_offset': float(values[5])
                    })
                except (ValueError, IndexError) as e:
                    self.update_status(f"Invalid numeric value in seller config: {e}", 'red')
                    if self.parent.config_manager:
                        self.parent.config_manager.general_log.error(f"Failed to parse seller config: {e}")
                    return None
        return {'seller_configs': seller_configs}
