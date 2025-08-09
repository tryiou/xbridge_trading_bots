import asyncio
import time

from definitions.config_manager import ConfigManager


async def wait_for_pending_rpcs(config_manager, timeout=30):
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
    async def unified_shutdown(config_manager: 'ConfigManager') -> None:  # noqa: F821
        """Core shutdown logic for both CLI and GUI per strategy"""
        # 1. Signal controller shutdown
        if config_manager.controller:
            config_manager.controller.shutdown_event.set()

        # 2. Wait for pending RPCs
        await wait_for_pending_rpcs(config_manager, timeout=30)

        # 3. Cancel ONLY strategy's own orders
        if config_manager.strategy_instance:
            count = await config_manager.strategy_instance.cancel_own_orders()
            config_manager.general_log.info(f"Cancelled {count} strategy orders")

        # 4. Common resource cleanup
        if hasattr(config_manager, 'http_session'):
            await config_manager.http_session.close()

        # NOTE: Proxy cleanup is now handled by reference counting in CCXTManager
        # Do not call _cleanup_proxy() directly here
