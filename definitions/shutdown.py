import asyncio
import logging
import time
from typing import TYPE_CHECKING

from definitions.ccxt_manager import CCXTManager
from definitions.errors import convert_exception

if TYPE_CHECKING:
    from definitions.config_manager import ConfigManager


async def wait_for_pending_rpcs(config_manager: 'ConfigManager', timeout=30):  # noqa: F821
    """Universal function to wait for pending RPCs to complete"""
    start_time = time.time()
    logger = config_manager.general_log
    while time.time() - start_time < timeout:

        active_rpcs = config_manager.xbridge_manager.active_rpc_counter
        if active_rpcs <= 0:
            logger.debug("All pending RPCs completed")
            return

        elapsed = time.time() - start_time
        if int(elapsed) % 5 == 0:
            logger.info(f"Waiting on {active_rpcs} RPCs ({int(elapsed)}s/{timeout}s)...")
            
        await asyncio.sleep(1)
    

    logger.warning(f"RPC wait timeout after {timeout} seconds, proceeding with shutdown")


class ShutdownCoordinator:
    """Orchestrates application shutdown sequence across components"""

    @staticmethod
    async def unified_shutdown(
            config_manager: 'ConfigManager') -> None:  # noqa: F821
        """Core shutdown logic for both CLI and GUI per strategy"""
        from strategies.maker_strategy import MakerStrategy
        try:
            logger = config_manager.general_log if config_manager else logging.getLogger("unified_shutdown")

            if not config_manager:
                logger.warning("Shutdown called without a config_manager. Limited cleanup possible.")
                CCXTManager._cleanup_proxy()
                return

            # 1. Signal controller shutdown
            if config_manager.controller:
                logger.debug("Setting controller shutdown event")
                with config_manager.resource_lock:
                    config_manager.controller.shutdown_event.set()
                logger.info("Controller shutdown event set")

            # 2. Wait for pending RPCs
            await wait_for_pending_rpcs(config_manager, timeout=30)

            # 3. Cancel strategy's own orders
            if config_manager.strategy_instance:
                try:
                    if isinstance(config_manager.strategy_instance, MakerStrategy):
                        logger.info(f"Cancelling {config_manager.strategy} orders...")
                        count = await config_manager.strategy_instance.cancel_own_orders()
                        logger.info(f"Cancelled {count} strategy orders")
                    else:
                        logger.info("Skipping order cancellation - not a maker strategy")
                except Exception as e:
                    context = {"phase": "shutdown", "operation": "order_cancellation"}
                    converted = convert_exception(e)
                    converted.context = context
                    await config_manager.error_handler.handle_async(converted)
            else:
                logger.warning("No strategy instance available for order cancellation")

            # 4. Common resource cleanup
            if hasattr(config_manager, 'http_session') and config_manager.http_session:
                try:
                    await config_manager.http_session.close()
                    logger.debug("HTTP session closed")
                except Exception as e:
                    logger.error(f"Error closing HTTP session: {e}", exc_info=True)

            # 5. proxy termination
            CCXTManager._cleanup_proxy()
        except Exception as e:
            if config_manager:
                converted = convert_exception(e)
                converted.context = {"phase": "shutdown"}
                await config_manager.error_handler.handle_async(converted)
            else:
                # If config_manager is None, we have no error handler, so just log
                logging.getLogger("unified_shutdown").critical(f"Unhandled exception during shutdown: {e}",
                                                               exc_info=True)
