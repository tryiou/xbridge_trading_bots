import signal
import sys

from gui.gui import GUI_Main
from definitions.logger import set_gui_mode


def signal_handler(sig, frame):
    print("\nKeyboard interrupt detected. Scheduling application shutdown...")
    # Schedule the on_closing method to be called on the main Tkinter thread.
    # This is crucial for safely interacting with GUI elements from a signal handler.
    if app.root:
        app.root.after(0, app.on_closing)
    else:
        sys.exit(0)  # Fallback if root is somehow already destroyed (unlikely during normal operation)


if __name__ == '__main__':
    # Signal that the application is running in GUI mode.
    # This allows other modules to adjust their behavior (e.g., logging).
    set_gui_mode(True)
    app = GUI_Main()
    app.root.protocol("WM_DELETE_WINDOW", app.on_closing)

    # Register the signal handler for SIGINT (Ctrl+C)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        app.root.mainloop()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt detected. Shutting down...")
        app.on_closing()
