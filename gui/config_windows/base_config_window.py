# gui/config_windows/base_config_window.py
import os
import threading
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any, Dict

from ruamel.yaml import YAML

if TYPE_CHECKING:
    from gui.frames.base_frames import BaseStrategyFrame


class BaseConfigWindow:
    """Base class for strategy configuration Toplevel windows."""

    def __init__(self, parent: "BaseStrategyFrame"):
        self.parent = parent
        strategy_title = parent.strategy_name.replace('_', ' ').title()
        self.title_text = f"Configure {strategy_title} Bot"
        self.config_file_path = f'./config/config_{parent.strategy_name}.yaml'
        self.config_window: tk.Toplevel | None = None
        self.status_var = tk.StringVar()
        self.status_label: ttk.Label | None = None
        self.active_dialog: tk.Toplevel | None = None

    def open(self) -> None:
        """Opens the configuration window, preventing multiple instances."""
        if self.config_window and self.config_window.winfo_exists():
            self.config_window.tkraise()
            return

        # Disable parent buttons while config window is open
        if self.parent.btn_start:
            self.parent.btn_start.config(state="disabled")
        if self.parent.btn_configure:
            self.parent.btn_configure.config(state="disabled")

        self.config_window = tk.Toplevel(self.parent)
        self.config_window.title(self.title_text)
        self.config_window.protocol("WM_DELETE_WINDOW", self.on_close)

        main_frame = ttk.Frame(self.config_window)
        main_frame.pack(fill='both', expand=True, padx=10, pady=10)
        main_frame.grid_rowconfigure(0, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)

        self._create_widgets(main_frame)
        self._create_control_buttons_area(main_frame)
        self._create_save_button(main_frame)
        self._create_status_bar(main_frame)
        self._set_window_geometry()

    def _create_widgets(self, parent_frame: ttk.Frame):
        """Placeholder for subclass to create specific widgets."""
        raise NotImplementedError

    def _create_control_buttons_area(self, parent_frame: ttk.Frame):
        """Placeholder for subclass to create control buttons outside the main widget area."""
        pass

    def _create_save_button(self, parent_frame: ttk.Frame) -> None:
        """Creates the save button for the configuration window."""
        save_button = ttk.Button(parent_frame, text="Save", command=self.save_config)
        save_button.grid(row=2, column=0, pady=10, sticky='ew')

    def _create_status_bar(self, parent_frame: ttk.Frame) -> None:
        """Creates the status bar at the bottom of the configuration window."""
        status_frame = ttk.Frame(parent_frame)
        status_frame.grid(row=3, column=0, pady=5, sticky='ew')
        self.status_var.set("Ready")
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        self.status_label.pack(fill='x')

    def _set_window_geometry(self):
        """Placeholder for subclass to set window size."""
        pass

    def on_close(self) -> None:
        """Handles the window closing event, re-enabling parent buttons."""
        if self.parent.btn_start:
            self.parent.btn_start.config(state="normal")
        if self.parent.btn_configure:
            self.parent.btn_configure.config(state="normal")
        if self.config_window:
            self.config_window.destroy()
        self.config_window = None

    def _open_single_dialog(self, dialog_class: Any, *dialog_args: Any) -> tk.Toplevel:
        """
        Opens a single instance of a dialog, destroying any existing active dialog.
        """
        if self.active_dialog and self.active_dialog.winfo_exists():
            self.active_dialog.destroy()

        dialog = dialog_class(self.config_window, *dialog_args)
        self.active_dialog = dialog
        self.config_window.wait_window(dialog)

        if self.active_dialog is dialog:
            self.active_dialog = None
        return dialog

    def _atomic_save(self, new_config: Dict[str, Any]) -> bool:
        """
        Performs a safe configuration save using a temporary file and atomic replace.
        """
        yaml_writer = YAML()
        yaml_writer.default_flow_style = False
        yaml_writer.indent(mapping=2, sequence=4, offset=2)

        temp_path = f"{self.config_file_path}.tmp"
        try:
            with open(temp_path, 'w') as f:
                yaml_writer.dump(new_config, f)
            os.replace(temp_path, self.config_file_path)
            return True
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def save_config(self) -> None:
        """Saves configuration asynchronously with transaction safety."""
        try:
            new_config = self._get_config_data_to_save()
            if new_config is None:
                # Status already set by _get_config_data_to_save, so we don't overwrite it
                return

            self.update_status("Saving configuration...", 'blue')

            # Start a new thread for saving
            save_thread = threading.Thread(target=self._async_save_worker, args=(new_config,))
            save_thread.daemon = True
            save_thread.name = "SaveWorker"  # Set name for easier identification
            save_thread.start()

        except Exception as e:
            self.update_status(f"Failed to initiate save: {e}", 'lightcoral')
            if self.parent.config_manager:
                self.parent.config_manager.general_log.error(f"Failed to initiate config save: {e}", exc_info=True)

    def _async_save_worker(self, new_config: Dict[str, Any]):
        """Worker function for asynchronous configuration saving."""
        try:
            self._atomic_save(new_config)
            # Reload the master configuration manager to pick up changes from the file.
            if self.parent.master_config_manager:
                self.parent.master_config_manager.load_configs()

            # Schedule GUI update on the main thread
            self.parent.main_app.root.after(0,
                                            lambda: self.update_status("Configuration saved and reloaded successfully.",
                                                                       'lightgreen'))

            # Now, reload the strategy frame's specific configuration from the master.
            self.parent.main_app.root.after(0, lambda: self.parent.reload_configuration(loadxbridgeconf=True))

        except Exception as e:
            import traceback
            error_msg = f"Failed to save configuration: {e}"
            tb_str = traceback.format_exc()
            full_message = f"{error_msg}\nDetails:\n{tb_str}"

            self.parent.main_app.root.after(0, lambda: self.update_status(full_message, 'lightcoral'))
            if self.parent.config_manager:
                self.parent.config_manager.general_log.error(full_message, exc_info=True)

    def _get_config_data_to_save(self) -> Dict[str, Any] | None:
        """Placeholder for subclass to return the config dictionary to be saved."""
        raise NotImplementedError

    def update_status(self, message: str, color: str = 'black') -> None:
        """Updates the status bar message and color."""
        if self.status_label:
            self.status_var.set(message)
            self.status_label.config(foreground=color)
