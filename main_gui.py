import signal
import sys

from definitions.logger import set_gui_mode
from gui.main_app import MainApplication

app = None


def signal_handler(sig, frame):
    """
    Handles keyboard interrupt signals (Ctrl+C) for graceful application shutdown.
    Schedules the GUI's on_closing method to be called on the main Tkinter thread.
    """
    print("\nKeyboard interrupt detected. Scheduling application shutdown...")
    if app and app.root:
        app.root.after(0, app.on_closing)
    else:
        sys.exit(0)  # Fallback if root is somehow already destroyed (unlikely during normal operation)


if __name__ == '__main__':

    # Set up signal handler for Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    set_gui_mode(True)

    app = MainApplication()
    app.root.protocol("WM_DELETE_WINDOW", app.on_closing)
    try:
        app.root.mainloop()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt detected. Shutting down...")
