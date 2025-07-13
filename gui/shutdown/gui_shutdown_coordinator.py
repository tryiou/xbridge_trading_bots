# gui/shutdown/gui_shutdown_coordinator.py
import logging
import threading
import time
from typing import TYPE_CHECKING, Dict, Any

from definitions.config_manager import ConfigManager

if TYPE_CHECKING:
    from gui.frames.base_frames import BaseStrategyFrame
    from gui.main_app import MainApplication

logger = logging.getLogger(__name__)

class GUIShutdownCoordinator:
    """
    Coordinates the shutdown process specifically for the GUI application.
    This class is responsible for gracefully stopping all active strategy bots
    and GUI-related background tasks.
    """

    def __init__(self, config_manager: ConfigManager, strategies: Dict[str, "BaseStrategyFrame"], gui_root: Any):
        """
        Initializes the GUI Shutdown Coordinator.

        :param config_manager: The master ConfigManager instance.
        :param strategies: A dictionary of active strategy frames.
        :param gui_root: The main Tkinter root window.
        """
        self.master_config_manager = config_manager
        self.strategy_frames = strategies
        self.gui_root = gui_root
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

        # Disable GUI interaction during shutdown
        self.gui_root.grab_set()
        self.gui_root.focus_force()

        # Start shutdown in a separate thread to keep GUI responsive
        shutdown_thread = threading.Thread(target=self._perform_shutdown_tasks, daemon=True)
        shutdown_thread.start()

    def _perform_shutdown_tasks(self):
        """
        Performs the actual shutdown tasks in a separate thread.
        """
        try:
            # 1. Stop all strategy frames
            logger.info("Stopping all active strategy bots...")
            for name, frame in self.strategy_frames.items():
                if frame.started or frame.stopping:
                    logger.info(f"Signaling {name} bot to stop...")
                    frame.stop(reload_config=False) # Do not reload config during shutdown
                    if frame.cancel_all_thread and frame.cancel_all_thread.is_alive():
                        logger.info(f"Waiting for {name} cancel_all thread to finish...")
                        frame.cancel_all_thread.join()

            # Give bots some time to stop gracefully
            time.sleep(2)

            # 2. Ensure all bot threads are joined or forcefully terminated
            logger.info("Waiting for bot threads to terminate...")
            for name, frame in self.strategy_frames.items():
                if frame.send_process and frame.send_process.is_alive():
                    logger.warning(f"Bot thread for {name} is still alive. Attempting to join.")
                    frame._join_bot_thread(timeout=5) # Give it 5 seconds to join
                    if frame.send_process.is_alive():
                        logger.critical(f"Bot thread for {name} did not terminate gracefully. May require manual intervention.")
            
            # 3. Stop all GUI refreshers (orders and balances)
            logger.info("Stopping GUI refreshers...")
            # for name, frame in self.strategy_frames.items():
            #     frame.stop_refresh()
            
            # Stop the main balance updater
            if hasattr(self.gui_root, 'balance_updater') and self.gui_root.balance_updater:
                self.gui_root.balance_updater.stop()

            # 4. Perform final cleanup on strategy frames
            logger.info("Performing final cleanup on strategy frames...")
            for name, frame in self.strategy_frames.items():
                frame.cleanup()

            # 5. Log final status
            logger.info("GUI shutdown tasks completed.")
            self.gui_root.after(0, self._finalize_gui_exit)

        except Exception as e:
            logger.critical(f"An error occurred during GUI shutdown: {e}", exc_info=True)
            self.gui_root.after(0, self._finalize_gui_exit) # Attempt to exit even on error

    def _finalize_gui_exit(self):
        """
        Finalizes the GUI exit on the main Tkinter thread.
        """
        logger.info("Destroying GUI root window.")
        self.gui_root.destroy()