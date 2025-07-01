# gui/gui.py
import asyncio
import threading
import tkinter as tk
from tkinter import ttk

from ttkbootstrap import Style

from .frames import PingPongFrame, BasicSellerFrame, ArbitrageFrame


class GUI_Main:
    """Main GUI application class that hosts different strategy frames."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("XBridge Trading Bots")
        self.style = Style(theme="darkly")
        self.status_var = tk.StringVar(value="Idle")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(pady=10, padx=10, fill="both", expand=True)

        # Create frames for each strategy
        self.pingpong_frame = PingPongFrame(self.notebook, self)
        self.basicseller_frame = BasicSellerFrame(self.notebook, self)
        self.arbitrage_frame = ArbitrageFrame(self.notebook, self)

        self.notebook.add(self.pingpong_frame, text='PingPong')
        self.notebook.add(self.basicseller_frame, text='Basic Seller')
        self.notebook.add(self.arbitrage_frame, text='Arbitrage')

        self.create_status_bar()

        # Start the refresh loop for the initially selected tab
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        self.on_tab_changed() # Manually trigger for the first tab

    def create_status_bar(self) -> None:
        """Creates the status bar at the bottom of the main window."""
        status_frame = ttk.Frame(self.root)
        status_frame.pack(side="bottom", fill="x", padx=5, pady=5)
        status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        status_label.pack(fill="x")

    def on_tab_changed(self, event=None):
        """Handle tab changes to start/stop the appropriate refresh loops."""
        selected_tab_index = self.notebook.index(self.notebook.select())
        
        # Stop all refresh loops first
        self.pingpong_frame.stop_refresh()
        self.basicseller_frame.stop_refresh()
        self.arbitrage_frame.stop_refresh()

        # Start the refresh loop for the selected tab
        if selected_tab_index == 0:
            self.pingpong_frame.start_refresh()
        elif selected_tab_index == 1:
            self.basicseller_frame.start_refresh()
        elif selected_tab_index == 2:
            self.arbitrage_frame.start_refresh()

    def on_closing(self) -> None:
        """Handles the application closing event."""
        self.pingpong_frame.on_closing()
        self.basicseller_frame.on_closing()
        self.arbitrage_frame.on_closing()
        self.root.destroy()
