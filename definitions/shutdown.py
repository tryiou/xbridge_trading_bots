import asyncio
import os
import sys
import threading
from typing import Optional, Dict, Any


class ShutdownCoordinator:
    """Orchestrates application shutdown sequence across components"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._shutdown_in_progress = False
        return cls._instance

    @classmethod
    def initiate_shutdown(cls, config_manager, strategies: Dict[str, Any], gui_root: Optional[Any] = None) -> None:
        """Initiate coordinated shutdown sequence"""
        instance = cls()
        if instance._shutdown_in_progress:
            return

        instance._shutdown_in_progress = True
        instance.config_manager = config_manager
        instance.strategies = strategies
        instance.gui_root = gui_root

        instance._log_shutdown_start()
        instance._disable_gui_actions()
        instance._begin_shutdown_sequence()

    def _log_shutdown_start(self) -> None:
        """Log shutdown initiation with system state"""
        running = [name for name, frame in self.strategies.items() if frame.started]
        self.config_manager.general_log.info(
            f"Shutdown initiated | Strategies running: {len(running)} | Active: {', '.join(running)}"
        )

    def _disable_gui_actions(self) -> None:
        """Disable GUI interactions during shutdown"""
        if self.gui_root:
            self.gui_root.protocol("WM_DELETE_WINDOW", lambda: None)
            if hasattr(self.gui_root, '_balances_update_id'):
                self.gui_root.after_cancel(self.gui_root._balances_update_id)

    def _begin_shutdown_sequence(self) -> None:
        """Start asynchronous shutdown process"""
        shutdown_thread = threading.Thread(
            target=self.execute_shutdown_sequence,
            daemon=True
        )
        shutdown_thread.start()

    def execute_shutdown_sequence(self) -> None:
        """Perform ordered shutdown steps"""
        self.config_manager.general_log.info("Starting shutdown sequence - Phase 1: Stopping strategies")
        self._stop_strategies()

        self.config_manager.general_log.info("Shutdown phase 2: Canceling outstanding orders")
        self._cancel_outstanding_orders()

        self.config_manager.general_log.info("Shutdown phase 3: Cleaning up resources")
        self._cleanup_resources()

        self.config_manager.general_log.info("Shutdown phase 4: Finalizing process termination")
        self._finalize_shutdown()

    def _stop_strategies(self) -> None:
        """Gracefully stop all running strategies"""
        running_strategies = [s for s in self.strategies.values() if s.started and not s.stopping]
        self.config_manager.general_log.info(f"Stopping {len(running_strategies)} running strategies")

        for strategy in running_strategies:
            try:
                self.config_manager.general_log.info(f"Attempting to stop {strategy.strategy_name} strategy")
                strategy.stop(reload_config=False)

                # Additional check if strategy thread is still alive
                if strategy.send_process and strategy.send_process.is_alive():
                    self.config_manager.general_log.warning(
                        f"{strategy.strategy_name} thread did not terminate, sending final SIGTERM")
                    strategy._finalize_stop(reload_config=False)

            except Exception as e:
                self.config_manager.general_log.error(
                    f"Failed to stop {strategy.strategy_name}: {str(e)}",
                    exc_info=True
                )
            finally:
                # Force release resources if still holding any
                if hasattr(strategy, 'cleanup'):
                    strategy.cleanup()

    def _cancel_outstanding_orders(self) -> None:
        """Cancel any remaining open orders"""
        self.config_manager.general_log.info("Initiating cancellation of all open orders")
        try:
            # Add timeout for order cancellation
            canceled = asyncio.run(asyncio.wait_for(
                self.config_manager.xbridge_manager.cancelallorders(),
                timeout=10.0
            ))
            if canceled:
                self.config_manager.general_log.info(f"Successfully canceled {len(canceled)} open orders")
            else:
                self.config_manager.general_log.warning("No open orders found to cancel")
        except asyncio.TimeoutError:
            self.config_manager.general_log.error("Timed out canceling orders - proceeding with shutdown")
        except Exception as e:
            self.config_manager.general_log.error(
                f"Failed to cancel orders: {str(e)}",
                exc_info=True
            )

    def _cleanup_resources(self) -> None:
        """Release system resources and connections"""
        # Clean up proxy process using centralized method
        from definitions.ccxt_manager import CCXTManager
        CCXTManager._cleanup_proxy()

        if hasattr(self.config_manager, 'http_session'):
            self.config_manager.general_log.info("Closing HTTP session and cleaning up resources")
            try:
                self.config_manager.http_session.close()
                self.config_manager.general_log.debug("HTTP session closed successfully")
            except Exception as e:
                self.config_manager.general_log.error(
                    f"Error closing HTTP session: {str(e)}",
                    exc_info=True
                )
        else:
            self.config_manager.general_log.debug("No HTTP session found to close")

    def _finalize_shutdown(self) -> None:
        """Perform final termination steps"""
        self.config_manager.general_log.info("Finalizing shutdown process")

        if self.gui_root:
            self.config_manager.general_log.debug("Closing GUI root window")
            try:
                self.gui_root.quit()
                self.gui_root.destroy()
                self.config_manager.general_log.debug("GUI resources released")
            except Exception as e:
                self.config_manager.general_log.error(
                    f"Error closing GUI: {str(e)}",
                    exc_info=True
                )

        self.config_manager.general_log.info("Shutdown sequence completed. Exiting process")
        try:
            # First try normal exit to allow cleanup handlers
            sys.exit(0)
        except SystemExit as e:
            # If normal exit blocked, force exit after final log
            self.config_manager.general_log.critical(
                f"Force exiting process after failed clean exit: {str(e)}"
            )
            os._exit(e.code)
        except Exception as e:
            self.config_manager.general_log.critical(
                f"CRITICAL FAILURE DURING EXIT: {str(e)}",
                exc_info=True
            )
            os._exit(1)

    @classmethod
    async def shutdown_async(cls, config_manager) -> None:
        """Asynchronous shutdown entry point"""
        config_manager.general_log.info("Starting asynchronous shutdown sequence")

        try:
            config_manager.general_log.debug("Canceling XBridge orders")
            count = await config_manager.strategy_instance.cancel_own_orders()
            # canceled = await config_manager.xbridge_manager.cancelallorders()
            config_manager.general_log.info(f"Canceled {count} orders in async shutdown")

            if config_manager.controller:
                config_manager.general_log.debug("Setting shutdown event flag")
                config_manager.controller.shutdown_event.set()
                config_manager.general_log.debug("Shutdown event flag set successfully")

        except Exception as e:
            config_manager.general_log.error(
                f"Async shutdown failed: {str(e)}",
                exc_info=True
            )
            raise

        config_manager.general_log.info("Async shutdown sequence completed")
