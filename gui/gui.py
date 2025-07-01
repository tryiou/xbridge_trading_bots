# gui/gui.py
import asyncio
import threading
import tkinter as tk
from tkinter import ttk

from ttkbootstrap import Style

from definitions.config_manager import ConfigManager
from definitions.starter import run_async_main
from gui.frames import GUI_Balances, GUI_Config, GUI_Orders


class GUI_Main:
    """Main GUI application class for the PingPong bot."""

    def __init__(self):
        self.config_manager: ConfigManager | None = None
        self.initialize(loadxbridgeconf=True)

        self.root = tk.Tk()
        self.root.title("PingPong")
        self.root.resizable(width=False, height=False)

        self.send_process: threading.Thread | None = None
        self.started = False

        # Ensure config_manager is not None before accessing attributes
        if not self.config_manager:
            raise RuntimeError("ConfigManager failed to initialize.")

        self.style = Style(self.config_manager.config_pingppong.ttk_theme)
        self.status_var = tk.StringVar(value="Idle")

        # Instantiate frame handlers from the new frames.py module
        self.gui_orders = GUI_Orders(self)
        self.gui_balances = GUI_Balances(self)
        self.gui_config = GUI_Config(self)

        self.create_widgets()
        self.refresh_gui()

    def create_widgets(self) -> None:
        """Creates all the main widgets for the GUI by delegating to frame handlers."""
        self.create_buttons()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()
        self.create_status_bar()

    def create_buttons(self) -> None:
        """Creates the main control buttons (START, STOP, CANCEL ALL, CONFIGURE)."""
        button_frame = ttk.Frame(self.root)
        button_frame.grid(column=0, row=0, padx=5, pady=5, sticky='ew')
        btn_width = 12
        self.btn_start = ttk.Button(button_frame, text="START", command=self.start, width=btn_width)
        self.btn_start.grid(column=0, row=0, padx=5, pady=5)
        self.btn_stop = ttk.Button(button_frame, text="STOP", command=self.stop, width=btn_width)
        self.btn_stop.grid(column=1, row=0, padx=5, pady=5)
        self.btn_stop.config(state="disabled")
        self.btn_cancel_all = ttk.Button(button_frame, text="CANCEL ALL", command=self.cancel_all, width=btn_width)
        self.btn_cancel_all.grid(column=2, row=0, padx=5, pady=5)
        self.btn_configure = ttk.Button(button_frame, text="CONFIGURE", command=self.open_configure_window,
                                        width=btn_width)
        self.btn_configure.grid(column=3, row=0, padx=5, pady=5)

    def create_status_bar(self) -> None:
        """Creates the status bar at the bottom of the main window."""
        status_frame = ttk.Frame(self.root)
        status_frame.grid(row=3, column=0, columnspan=4, padx=5, pady=5, sticky='ew')
        status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        status_label.grid(row=0, column=0, padx=5, pady=5, sticky='ew')

    def initialize(self, loadxbridgeconf: bool = True) -> None:
        """Initializes the configuration manager."""
        self.config_manager = ConfigManager(strategy="pingpong")
        self.config_manager.initialize(loadxbridgeconf=loadxbridgeconf)

    def start(self) -> None:
        """Starts the PingPong bot in a separate thread."""
        if not self.config_manager:
            return

        self.status_var.set("Bot is running...")
        self.send_process = threading.Thread(target=run_async_main,
                                             args=(self.config_manager,),
                                             daemon=True)
        try:
            self.send_process.start()
            self.started = True
            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="active")
            self.btn_configure.config(state="disabled")
            self.config_manager.general_log.info("Bot started successfully.")
        except Exception as e:
            self.status_var.set(f"Error starting bot: {e}")
            self.config_manager.general_log.error(f"Error starting bot thread: {e}")
            self.stop(reload_config=False)

    def stop(self, reload_config: bool = True) -> None:
        """Stops the PingPong bot and performs cleanup."""
        if not self.config_manager:
            return

        self.status_var.set("Stopping bot...")
        self.config_manager.general_log.info("Attempting to stop bot...")

        if self.config_manager.controller:
            self.config_manager.controller.stop_order = True

        if self.send_process:
            self.send_process.join(timeout=5)
            if self.send_process.is_alive():
                self.config_manager.general_log.warning("Bot thread did not terminate gracefully within timeout.")
                self.status_var.set("Bot stopped (thread timeout).")
            else:
                self.status_var.set("Bot stopped.")
                self.config_manager.general_log.info("Bot stopped successfully.")
        else:
            self.status_var.set("Bot not running.")
            self.config_manager.general_log.info("Stop requested, but bot was not running.")

        self.started = False
        self.btn_stop.config(state="disabled")
        self.btn_start.config(state="active")
        self.btn_configure.config(state="active")

        if reload_config:
            self.reload_configuration(loadxbridgeconf=False)

    def cancel_all(self) -> None:
        """Cancels all open orders on the exchange."""
        if not self.config_manager:
            return

        self.status_var.set("Cancelling all open orders...")
        if self.started and self.config_manager.controller and self.config_manager.controller.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self.config_manager.xbridge_manager.cancelallorders(),
                self.config_manager.controller.loop
            )
            try:
                future.result(timeout=15)
                self.status_var.set("Cancelled all open orders.")
                self.config_manager.general_log.info("cancel_all: All orders cancelled successfully.")
            except Exception as e:
                self.status_var.set(f"Error cancelling orders: {e}")
                self.config_manager.general_log.error(f"Error during cancel_all: {e}")
        else:
            self.config_manager.general_log.info("cancel_all: Bot not running, using new event loop.")
            try:
                asyncio.run(self.config_manager.xbridge_manager.cancelallorders())
                self.status_var.set("Cancelled all open orders.")
                self.config_manager.general_log.info("cancel_all: All orders cancelled successfully.")
            except Exception as e:
                self.status_var.set(f"Error cancelling orders: {e}")
                self.config_manager.general_log.error(f"Error during cancel_all with new loop: {e}")

    def refresh_gui(self) -> None:
        """Refreshes the GUI display periodically. Checks bot thread status."""
        if self.started:
            if self.send_process and not self.send_process.is_alive() and self.config_manager:
                self.config_manager.general_log.error("pingpong bot crashed!")
                self.status_var.set("pingpong bot crashed!")
                self.stop(reload_config=False)
                self.cancel_all()

        self.gui_orders.update_order_display()
        self.gui_balances.update_balance_display()
        self.root.after(1500, self.refresh_gui)

    def open_configure_window(self) -> None:
        """Opens the configuration window."""
        self.gui_config.open()

    def on_closing(self, reload_config: bool = False) -> None:
        """Handles the application closing event."""
        if self.config_manager:
            self.config_manager.general_log.info("Closing application...")
        self.stop(reload_config=reload_config)
        self.root.destroy()

    def reload_configuration(self, loadxbridgeconf: bool = True) -> None:
        """Reloads the bot's configuration and refreshes the GUI display."""
        if not self.config_manager:
            return

        self.config_manager.load_configs()
        self.config_manager.initialize(loadxbridgeconf=loadxbridgeconf)
        self.gui_orders.purge_treeview()
        self.gui_balances.purge_treeview()
        self.gui_orders.create_orders_treeview()
        self.gui_balances.create_balances_treeview()
