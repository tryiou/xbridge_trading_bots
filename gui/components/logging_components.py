# gui/components/logging_components.py
import logging
import queue
import time
import tkinter as tk
from tkinter import ttk

logger = logging.getLogger(__name__)


class LogFrame(ttk.Frame):
    """A frame for displaying application logs."""

    def __init__(self, parent):
        super().__init__(parent)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.log_entries = []  # Track (timestamp, line_start, line_end)

        self.log_update_queue = queue.Queue()
        self.after(250, self._process_log_updates)

        self.log_text = tk.Text(self, wrap='word', state='disabled', height=10, background="#222", foreground="white")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.log_text.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')

        # Configure tags for different log levels
        self.log_text.tag_config("INFO", foreground="white")
        self.log_text.tag_config("DEBUG", foreground="gray")
        self.log_text.tag_config("WARNING", foreground="orange")
        self.log_text.tag_config("ERROR", foreground="red")
        self.log_text.tag_config("CRITICAL", foreground="red", underline=1)

    def add_log(self, message: str, level: str):
        """
        Thread-safe entry point to add a log message to the queue.
        """
        try:
            self.log_update_queue.put((message, level))
        except RuntimeError:
            # LogFrame being destroyed - ignore
            pass

    def _safe_add_log(self, message: str, level: str):
        """
        Adds a pre-formatted log message to the text widget.
        This method should only be called from the main Tkinter thread.
        """
        self.log_text.config(state='normal')

        # Store current line count before adding
        line_count = int(self.log_text.index('end-1c').split('.')[0])

        # Add new log with timestamp
        self.log_text.insert(tk.END, message, (level,))
        if not message.endswith('\n'):
            self.log_text.insert(tk.END, '\n')

        # Record entry time and line numbers
        now = time.time()
        self.log_entries.append((now, line_count, line_count + 1))

        # Keep text widget manageable
        if len(self.log_entries) > 10000:  # Safety valve
            self.log_text.delete(1.0, f'{len(self.log_entries) - 5000}.0')
            self.log_entries = self.log_entries[-5000:]

        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def _process_log_updates(self):
        """
        Processes queued log updates in the main Tkinter thread.
        """
        if not self.winfo_exists():
            return

        try:
            while not self.log_update_queue.empty():
                message, level = self.log_update_queue.get_nowait()
                self._safe_add_log(message, level)
        except queue.Empty:
            pass  # No updates yet
        except Exception as e:
            logger.error(f"Error processing log updates in main thread: {e}", exc_info=True)
        finally:
            if self.winfo_exists():
                self.after(250, self._process_log_updates)  # Schedule next check


class TextLogHandler(logging.Handler):
    """A logging handler that directs output to a Tkinter Text widget."""

    def __init__(self, log_frame: LogFrame):
        super().__init__()
        self.log_frame = log_frame

    def emit(self, record):
        # The handler's formatter (set in gui.py) creates the string.
        # We pass the formatted string and the original levelname to the LogFrame.
        self.log_frame.add_log(self.format(record), record.levelname)


class StdoutRedirector:
    """A class to redirect stdout/stderr to the GUI log frame."""

    def __init__(self, log_frame: LogFrame, level: str, original_stream):
        self.log_frame = log_frame
        self.level = level
        self.original_stream = original_stream

    def write(self, message: str):
        # Write to the original stream (console) first
        if self.original_stream:
            self.original_stream.write(message)
            self.original_stream.flush()

        # Then write to the GUI log frame
        if message.strip():
            self.log_frame.add_log(message, self.level)

    def flush(self):
        if self.original_stream:
            self.original_stream.flush()
