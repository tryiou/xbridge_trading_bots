# gui/shutdown/gui_shutdown_coordinator.py
import logging
import threading
import time
from typing import TYPE_CHECKING, Dict, Any

from definitions.config_manager import ConfigManager
from definitions.error_handler import OperationalError

if TYPE_CHECKING:
    from gui.frames.base_frames import BaseStrategyFrame

logger = logging.getLogger(__name__)


class GUIShutdownCoordinator:
    """
    Coordinates the shutdown process specifically for the GUI application.
    This class is responsible for gracefully stopping all active strategy bots
    and GUI-related background tasks with centralized error handling.
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

        try:
            # Disable GUI interaction during shutdown
            self.gui_root.grab_set()
            self.gui_root.focus_force()
        except Exception as e:
            error_msg = f"Error disabling GUI interaction: {e}"
            logger.error(error_msg, exc_info=True)
            self.master_config_manager.error_handler.handle(
                OperationalError(error_msg),
                context={"stage": "shutdown_init"},
                exc_info=True
            )

        # Start shutdown in a separate thread to keep GUI responsive
        shutdown_thread = threading.Thread(target=self._perform_shutdown_tasks, daemon=True)
        shutdown_thread.start()

    def _perform_shutdown_tasks(self):
        """Performs the actual shutdown tasks in a separate thread with error handling."""
        try:
            # Phase 1: Non-blocking coordination
            self._coordinate_shutdown_signals()

            # Phase 2: Graceful termination
            termination_success = self._await_component_termination(timeout=5)

            # Phase 3: Guaranteed cleanup
            self._cleanup_resources()
        except Exception as e:
            error_msg = f"Critical error during GUI shutdown: {e}"
            logger.critical(error_msg, exc_info=True)
            self.master_config_manager.error_handler.handle(
                OperationalError(error_msg),
                context={"stage": "shutdown"},
                severity="CRITICAL",
                exc_info=True
            )
        finally:
            self.gui_root.after(0, self._finalize_gui_exit)

    def _coordinate_shutdown_signals(self):
        """Send non-blocking shutdown signals to all components."""
        logger.info("Initiating non-blocking shutdown signals...")

        # Signal strategy components
        for name, frame in self.strategy_frames.items():
            try:
                if frame.started or frame.stopping:
                    logger.info(f"Signaling {name} bot to stop...")
                    # Send non-blocking stop signal
                    frame.stop(reload_config=False)
                    # Trigger controller shutdown sequence
                    frame._signal_controller_shutdown()
            except Exception as e:
                error_msg = f"Error signaling {name} to stop: {e}"
                logger.error(error_msg, exc_info=True)
                # Non-critical during shutdown, continue

    def _await_component_termination(self, timeout: float) -> bool:
        """Wait for components to gracefully terminate within timeout period."""
        logger.info(f"Awaiting component termination with {timeout} second timeout...")
        deadline = time.time() + timeout
        active_components = []

        # Build initial status
        for name, frame in self.strategy_frames.items():
            if frame.started and getattr(frame, 'send_process', None) and frame.send_process.is_alive():
                active_components.append(name)

        # Periodic status updates
        while time.time() < deadline and active_components:
            logger.info(f"Awaiting termination for: {', '.join(active_components)}")
            for name in active_components[:]:
                try:
                    frame = self.strategy_frames[name]
                    if frame.send_process is not None and not frame.send_process.is_alive():
                        logger.info(f"{name} successfully terminated")
                        active_components.remove(name)
                except Exception as e:
                    logger.error(f"Termination check error for {name}: {e}")
                    active_components.remove(name)  # Remove to avoid endless loop

            if active_components:
                time.sleep(min(0.5, deadline - time.time()))  # Small sleep to avoid busy-wait

        return len(active_components) == 0

    def _cleanup_resources(self):
        """Final cleanup resource release both graceful and forced."""
        logger.info("Releasing resources...")
        # Cleanup strategy frames
        for name, frame in self.strategy_frames.items():
            try:
                if getattr(frame, 'cleanup', None):
                    frame.cleanup()
                # Clear running processes
                if getattr(frame, 'send_process', None):
                    if frame.send_process.is_alive():
                        logger.warning(f"Terminating {name}'s bot thread forcibly")
                        # Insist on thread termination
                        frame.send_process.join(timeout=0.5)
                    frame.send_process = None
            except Exception as e:
                logger.warning(f"Resource cleanup failed for {name}: {e}", exc_info=True)

        logger.info("Resources cleanup completed.")

    def _finalize_gui_exit(self):
        """
        Finalizes the GUI exit on the main Tkinter thread.
        """
        try:
            logger.info("Destroying GUI root window.")
            self.gui_root.destroy()
        except Exception as e:
            # If we can't destroy the root window, log and try to exit
            error_msg = f"Error destroying GUI root: {e}"
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
