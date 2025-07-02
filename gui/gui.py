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

        # Create and add frames for each strategy
        self.strategy_frames = {
            'PingPong': PingPongFrame(self.notebook, self),
            'Basic Seller': BasicSellerFrame(self.notebook, self),
            'Arbitrage': ArbitrageFrame(self.notebook, self),
        }
        for text, frame in self.strategy_frames.items():
            self.notebook.add(frame, text=text)

        self.create_status_bar()

        # Start the refresh loop for the initially selected tab
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)
        self.on_tab_changed()  # Manually trigger for the first tab

    def create_status_bar(self) -> None:
        """Creates the status bar at the bottom of the main window."""
        status_frame = ttk.Frame(self.root)
        status_frame.pack(side="bottom", fill="x", padx=5, pady=5)
        status_label = ttk.Label(status_frame, textvariable=self.status_var, anchor='w')
        status_label.pack(fill="x")

    def on_tab_changed(self, event=None):
        """Handle tab changes to start/stop the appropriate refresh loops."""
        # Stop all refresh loops first
        for frame in self.strategy_frames.values():
            frame.stop_refresh()

        # Start the refresh loop for the selected tab
        selected_widget = self.root.nametowidget(self.notebook.select())
        if hasattr(selected_widget, 'start_refresh'):
            selected_widget.start_refresh()

    def on_closing(self) -> None:
        """Handles the application closing event."""
        for frame in self.strategy_frames.values():
            frame.on_closing()
        self.root.destroy()
