# gui/shutdown/gui_shutdown_coordinator.py
import logging
import threading
from typing import TYPE_CHECKING

from definitions.errors import CriticalError, OperationalError

if TYPE_CHECKING:
    from gui.main_app import MainApplication

logger = logging.getLogger(__name__)


class GUIShutdownCoordinator:
    """
    Coordinates the shutdown process specifically for the GUI application.
    This class is responsible for gracefully stopping all active strategy bots
    and GUI-related background tasks with centralized error handling.
    """

    def __init__(self, main_app: "MainApplication"):
        """
        Initializes the GUI Shutdown Coordinator.

        :param main_app: The main application instance.
        """
        self.main_app = main_app
        self.master_config_manager = main_app.master_config_manager
        self.strategy_frames = main_app.strategy_frames
        self.gui_root = main_app.root
        self._shutdown_in_progress = False

    def initiate_shutdown(self):
        """
        Initiates the coordinated shutdown process for the GUI.
        This method is designed to be called from the main Tkinter thread.
        """
        if self._shutdown_in_progress:
            logger.debug("GUI shutdown already in progress, ignoring repeated call.")
            return

        self._shutdown_in_progress = True
        logger.info("Initiating GUI application shutdown...")

        try:
            # Disable GUI interaction during shutdown
            self.gui_root.grab_set()
            self.gui_root.focus_force()
        except Exception as e:
            error_msg = f"Error disabling GUI interaction: {e}"
            logger.error(error_msg, exc_info=True)
            self.master_config_manager.error_handler.handle(
                OperationalError(error_msg, context={"stage": "shutdown_init"}),
            )

        # Start shutdown in a separate thread to keep GUI responsive
        shutdown_thread = threading.Thread(target=self._perform_shutdown_tasks, daemon=True)
        shutdown_thread.start()

    def _perform_shutdown_tasks(self):
        """Performs the actual shutdown tasks in a separate thread with error handling."""
        try:
            # 1. Stop GUI-specific background tasks like balance updater
            logger.info("Stopping balance updater thread...")
            if hasattr(self.main_app, 'balance_stop_event'):
                self.main_app.balance_stop_event.set()
            if hasattr(self.main_app, 'balance_updater_thread') and self.main_app.balance_updater_thread.is_alive():
                self.main_app.balance_updater_thread.join(2.0)
            logger.info("Balance updater stopped.")

            # 2. Stop all running strategies
            stopper_threads = []
            for name, frame in self.strategy_frames.items():
                if frame.started:
                    logger.info(f"Stopping {name} strategy...")
                    thread = frame.stop(reload_config=False)
                    if thread:
                        stopper_threads.append(thread)

            # Wait for all stopper threads to complete.
            for thread in stopper_threads:
                thread.join()

            if stopper_threads:
                logger.info("All strategies have been stopped.")
        except Exception as e:
            error_msg = f"Critical error during GUI shutdown: {e}"
            logger.critical(error_msg, exc_info=True)
            self.master_config_manager.error_handler.handle(
                CriticalError(error_msg, context={"stage": "shutdown"}),
            )
        finally:
            # Brief pause to allow in-flight operations to settle/terminate
            # time.sleep(0.5)
            # Finalize GUI resources after stopping threads
            self.gui_root.after(0, self._finalize_gui_exit)

    def _finalize_gui_exit(self):
        """
        Finalizes the GUI exit on the main Tkinter thread.
        """
        try:
            logger.info("Quitting Tkinter mainloop and destroying root window.")
            # First, break out of the main loop
            self.gui_root.quit()
            # Then clean up all widgets
            self.gui_root.destroy()
        except Exception as e:
            # If we can't destroy the root window, log and try to exit
            error_msg = f"Error during GUI finalization: {e}"
            logger.critical(error_msg, exc_info=True)
            self.master_config_manager.error_handler.handle(
                OperationalError(error_msg),
                context={"stage": "shutdown_finalize"},
                severity="CRITICAL",
                exc_info=True
            )
            # Attempt to exit the application
            import sys
            sys.exit(1)
