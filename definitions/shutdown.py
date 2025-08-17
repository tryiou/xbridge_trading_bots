import asyncio
import logging
import time
from typing import TYPE_CHECKING

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

            # 1. Wait for pending RPCs
            await wait_for_pending_rpcs(config_manager, timeout=30)

            # 2. Cancel strategy's own orders
            if config_manager.strategy_instance:
                try:
                    if isinstance(config_manager.strategy_instance, MakerStrategy):
                        logger.info(f"Cancelling {config_manager.strategy} orders...")
                        count = await config_manager.strategy_instance.cancel_own_orders()
                        logger.info(f"Cancelled {count} strategy orders")
                    else:
                        logger.info("Skipping order cancellation - not a maker strategy")
                except asyncio.CancelledError:
                    logger.warning("Order cancellation was cancelled during shutdown.")
                    raise
                except Exception as e:
                    context = {"phase": "shutdown", "operation": "order_cancellation"}
                    await config_manager.error_handler.handle_async(e, context=context)
            else:
                logger.warning("No strategy instance available for order cancellation")

        except asyncio.CancelledError:
            logging.getLogger("unified_shutdown").warning("Shutdown was cancelled.")
            raise
        except Exception as e:
            if config_manager:
                await config_manager.error_handler.handle_async(e, context={"phase": "shutdown"})
            else:
                # If config_manager is None, we have no error handler, so just log
                logging.getLogger("unified_shutdown").critical(f"Unhandled exception during shutdown: {e}",
                                                               exc_info=True)
