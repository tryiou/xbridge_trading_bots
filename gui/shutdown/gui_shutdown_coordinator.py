# gui/shutdown/gui_shutdown_coordinator.py
import logging
import threading
import time
from typing import TYPE_CHECKING, Dict, Any

from definitions.config_manager import ConfigManager
from definitions.error_handler import OperationalError

if TYPE_CHECKING:
    from gui.frames.base_frames import BaseStrategyFrame
    from gui.main_app import MainApplication

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
        """                                                                                                                                                                                 
        Performs the actual shutdown tasks in a separate thread with error handling.                                                                                                        
        """                                                                                                                                                                                 
        try:                                                                                                                                                                                
            self._stop_all_strategies()                                                                                                                                                     
            self._join_all_threads()                                                                                                                                                        
            self._stop_gui_refreshers()                                                                                                                                                     
            self._cleanup_frames()                                                                                                                                                          
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
                                                                                                                                                                                            
    def _stop_all_strategies(self):                                                                                                                                                         
        """Stop all active strategy bots."""                                                                                                                                                
        logger.info("Stopping all active strategy bots...")                                                                                                                                 
        for name, frame in self.strategy_frames.items():                                                                                                                                    
            try:                                                                                                                                                                            
                if frame.started or frame.stopping:                                                                                                                                         
                    logger.info(f"Signaling {name} bot to stop...")                                                                                                                         
                    frame.stop(reload_config=False)  # Do not reload config during shutdown                                                                                                 
                    if frame.cancel_all_thread and frame.cancel_all_thread.is_alive():                                                                                                      
                        logger.info(f"Waiting for {name} cancel_all thread to finish...")                                                                                                   
                        frame.cancel_all_thread.join()                                                                                                                                      
            except Exception as e:                                                                                                                                                          
                error_msg = f"Error stopping {name} bot: {e}"                                                                                                                               
                logger.error(error_msg, exc_info=True)                                                                                                                                      
                self.master_config_manager.error_handler.handle(                                                                                                                            
                    OperationalError(error_msg),                                                                                                                                            
                    context={"strategy": name, "stage": "shutdown"},                                                                                                                        
                    exc_info=True                                                                                                                                                           
                )                                                                                                                                                                           
        time.sleep(2)  # Give bots some time to stop gracefully                                                                                                                             
                                                                                                                                                                                            
    def _join_all_threads(self):                                                                                                                                                            
        """Ensure all bot threads are joined or forcefully terminated."""                                                                                                                   
        logger.info("Waiting for bot threads to terminate...")                                                                                                                              
        for name, frame in self.strategy_frames.items():                                                                                                                                    
            try:                                                                                                                                                                            
                if frame.send_process and frame.send_process.is_alive():                                                                                                                    
                    logger.warning(f"Bot thread for {name} is still alive. Attempting to join.")                                                                                            
                    frame._join_bot_thread(timeout=5)  # Give it 5 seconds to join                                                                                                          
                    if frame.send_process.is_alive():                                                                                                                                       
                        logger.critical(                                                                                                                                                    
                            f"Bot thread for {name} did not terminate gracefully. May require manual intervention.")                                                                        
                        self.master_config_manager.error_handler.handle(                                                                                                                    
                            OperationalError(f"Bot thread for {name} did not terminate"),                                                                                                   
                            context={"strategy": name, "stage": "shutdown"},                                                                                                                
                            severity="CRITICAL"                                                                                                                                             
                        )                                                                                                                                                                   
            except Exception as e:                                                                                                                                                          
                error_msg = f"Error joining {name} bot thread: {e}"                                                                                                                         
                logger.error(error_msg, exc_info=True)                                                                                                                                      
                self.master_config_manager.error_handler.handle(                                                                                                                            
                    OperationalError(error_msg),                                                                                                                                            
                    context={"strategy": name, "stage": "shutdown"},                                                                                                                        
                    exc_info=True                                                                                                                                                           
                )                                                                                                                                                                           
                                                                                                                                                                                            
    def _stop_gui_refreshers(self):                                                                                                                                                         
        """Stop all GUI refreshers (orders and balances)."""                                                                                                                                
        logger.info("Stopping GUI refreshers...")                                                                                                                                           
        # for name, frame in self.strategy_frames.items():                                                                                                                                  
        #     frame.stop_refresh()                                                                                                                                                          
        # Stop the main balance updater                                                                                                                                                     
        try:                                                                                                                                                                                
            if hasattr(self.gui_root, 'balance_updater') and self.gui_root.balance_updater:                                                                                                 
                self.gui_root.balance_updater.stop()                                                                                                                                        
        except Exception as e:                                                                                                                                                              
            error_msg = f"Error stopping balance updater: {e}"                                                                                                                              
            logger.error(error_msg, exc_info=True)                                                                                                                                          
            self.master_config_manager.error_handler.handle(                                                                                                                                
                OperationalError(error_msg),                                                                                                                                                
                context={"stage": "shutdown"},                                                                                                                                              
                exc_info=True                                                                                                                                                               
            )                                                                                                                                                                               
                                                                                                                                                                                            
    def _cleanup_frames(self):                                                                                                                                                              
        """Perform final cleanup on strategy frames."""                                                                                                                                     
        logger.info("Performing final cleanup on strategy frames...")                                                                                                                       
        for name, frame in self.strategy_frames.items():                                                                                                                                    
            try:                                                                                                                                                                            
                frame.cleanup()                                                                                                                                                             
            except Exception as e:                                                                                                                                                          
                error_msg = f"Error cleaning up {name} frame: {e}"                                                                                                                          
                logger.error(error_msg, exc_info=True)                                                                                                                                      
                self.master_config_manager.error_handler.handle(                                                                                                                            
                    OperationalError(error_msg),                                                                                                                                            
                    context={"strategy": name, "stage": "shutdown"},                                                                                                                        
                    exc_info=True                                                                                                                                                           
                )                                                                                                                                                                           
        logger.info("GUI shutdown tasks completed.")                                                                                                                                        
        
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