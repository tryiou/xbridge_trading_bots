import argparse
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
    parser = argparse.ArgumentParser(description="XBridge Trading Bots GUI (v2)")
    parser.add_argument("--run-tests", action="store_true", help="Run the GUI unit test suite.")
    args = parser.parse_args()

    # Signal that the application is running in GUI mode.
    # This allows other modules to adjust their behavior (e.g., logging).
    set_gui_mode(True)

    if args.run_tests:
        # Note: The original test_units.test_gui_app needs to be updated to test gui
        # For now, this path is kept as is, assuming the user will update tests later.
        from test_units.test_gui_app import GUITester

        print("Running GUI unit tests...")
        tester = GUITester()
        tester.run_all_tests()
        # Exit with 1 if any tests failed, 0 otherwise
        sys.exit(0 if all(r['passed'] for r in tester.test_results) else 1)
    else:
        app = MainApplication()
        # Set up protocol for window close button
        app.root.protocol("WM_DELETE_WINDOW", app.on_closing)
        # Set up signal handler for Ctrl+C
        signal.signal(signal.SIGINT, signal_handler)
        try:
            app.root.mainloop()
        except KeyboardInterrupt:
            print("\nKeyboard interrupt detected. Shutting down...")
            # The signal_handler should have already scheduled on_closing,
            # but this acts as a fallback if the signal is caught directly here.
            if app:
                app.on_closing()
