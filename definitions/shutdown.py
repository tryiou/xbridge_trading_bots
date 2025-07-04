import asyncio
import os
import subprocess
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
                strategy.stop(blocking=True, reload_config=False)
                self.config_manager.general_log.debug(f"Successfully sent stop signal to {strategy.strategy_name}")
            except Exception as e:
                self.config_manager.general_log.error(
                    f"Failed to stop {strategy.strategy_name}: {str(e)}",
                    exc_info=True
                )

    def _cancel_outstanding_orders(self) -> None:
        """Cancel any remaining open orders"""
        self.config_manager.general_log.info("Initiating cancellation of all open orders")
        try:
            canceled = asyncio.run(self.config_manager.xbridge_manager.cancelallorders())
            if canceled:
                self.config_manager.general_log.info(f"Successfully canceled {len(canceled)} open orders")
            else:
                self.config_manager.general_log.warning("No open orders found to cancel")
        except Exception as e:
            self.config_manager.general_log.error(
                f"Failed to cancel orders: {str(e)}",
                exc_info=True
            )

    def _cleanup_resources(self) -> None:
        """Release system resources and connections"""
        # Clean up proxy process first
        if getattr(self.config_manager, 'ccxt_manager', None):
            cm = self.config_manager.ccxt_manager
            if cm.proxy_process and cm.proxy_process.poll() is None:
                try:
                    self.config_manager.general_log.info(
                        f"Stopping CCXT proxy (PID: {cm.proxy_process.pid}) on port {cm.proxy_port}..."
                    )
                    self.config_manager.general_log.debug("Sending SIGTERM to proxy process")
                    cm.proxy_process.terminate()
                    exit_code = cm.proxy_process.wait(timeout=5)
                    self.config_manager.general_log.info(
                        f"Proxy exited with code {exit_code}"
                    )
                except subprocess.TimeoutExpired:
                    self.config_manager.general_log.warning(
                        f"Proxy did not terminate gracefully after 5s, sending SIGKILL to PID {cm.proxy_process.pid}"
                    )
                    cm.proxy_process.kill()
                except Exception as e:
                    self.config_manager.general_log.error(
                        f"Error stopping proxy: {str(e)} (PID: {cm.proxy_process.pid})"
                    )

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
            os._exit(0)
        except Exception as e:
            self.config_manager.general_log.critical(
                f"CRITICAL FAILURE DURING EXIT: {str(e)}",
                exc_info=True
            )
            raise SystemExit(1) from e

    @classmethod
    async def shutdown_async(cls, config_manager) -> None:
        """Asynchronous shutdown entry point"""
        config_manager.general_log.info("Starting asynchronous shutdown sequence")

        try:
            config_manager.general_log.debug("Canceling XBridge orders")
            canceled = await config_manager.xbridge_manager.cancelallorders()
            config_manager.general_log.info(f"Canceled {len(canceled) if canceled else 0} orders in async shutdown")

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
