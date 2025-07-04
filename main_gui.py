import argparse
import signal
import sys

from definitions.logger import set_gui_mode
from gui.gui import GUI_Main

app = None

def signal_handler(sig, frame):
    print("\nKeyboard interrupt detected. Scheduling application shutdown...")
    # Schedule the on_closing method to be called on the main Tkinter thread.
    # This is crucial for safely interacting with GUI elements from a signal handler.
    if app and app.root:
        app.root.after(0, app.on_closing)
    else:
        sys.exit(0)  # Fallback if root is somehow already destroyed (unlikely during normal operation)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="XBridge Trading Bots GUI")
    parser.add_argument("--run-tests", action="store_true", help="Run the GUI unit test suite.")
    args = parser.parse_args()

    # Signal that the application is running in GUI mode.
    # This allows other modules to adjust their behavior (e.g., logging).
    set_gui_mode(True)

    if args.run_tests:
        from test_units.test_gui_app import GUITester
        print("Running GUI unit tests...")
        tester = GUITester()
        tester.run_all_tests()
        # Exit with a non-zero status code if tests failed
        if not all(r['passed'] for r in tester.test_results):
            sys.exit(1)
    else:
        app = GUI_Main()
        app.root.protocol("WM_DELETE_WINDOW", app.on_closing)
        signal.signal(signal.SIGINT, signal_handler)
        try:
            app.root.mainloop()
        except KeyboardInterrupt:
            print("\nKeyboard interrupt detected. Shutting down...")
            app.on_closing()
