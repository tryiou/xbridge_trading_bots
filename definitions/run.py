import asyncio
import contextlib
import time
import traceback
from collections.abc import Callable
from typing import TYPE_CHECKING

import aiohttp

from definitions.constants import FLUSH_DELAY, SLEEP_INTERVAL
from definitions.errors import RPCConfigError
from definitions.main_controller import MainController
from definitions.shutdown import ShutdownCoordinator

if TYPE_CHECKING:
    from definitions.config_manager import ConfigManager


def run_async_main(
        config_manager: "ConfigManager", startup_tasks: list[Callable] | None = None
) -> None:
    """
    Run main application loop with proper signal handling and cleanup.

    Args:
        config_manager: Application configuration manager
        startup_tasks: Optional list of async tasks to run at startup
    """

    async def main_wrapper() -> None:
        """Async wrapper for main application logic."""
        if proxy_mgr := config_manager.context.get("proxy_manager"):
            proxy_mgr.register()
        controller: MainController | None = None
        try:
            loop = asyncio.get_running_loop()
            controller = MainController(config_manager, loop)
            config_manager.controller = controller
            config_manager.strategy_instance.is_running = True
            await main(config_manager, loop, startup_tasks)
        except (SystemExit, asyncio.CancelledError):
            config_manager.general_log.info(
                "Received stop signal. Initiating coordinated shutdown..."
            )
            if config_manager.strategy_instance:
                config_manager.strategy_instance.stop()
            raise
        except RPCConfigError as e:
            config_manager.general_log.critical(f"Fatal RPC configuration error: {e}")
            raise
        finally:
            if controller is not None:
                await ShutdownCoordinator.unified_shutdown(config_manager)

            if proxy_mgr := config_manager.context.get("proxy_manager"):
                proxy_mgr.unregister()
            await asyncio.sleep(0.5)  # Allow proxy cleanup

    try:
        asyncio.run(main_wrapper())
    except RPCConfigError:
        raise
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            config_manager.general_log.info("Shutdown signal received")
        else:
            config_manager.general_log.error(f"Unhandled exception: {e}")
            traceback.print_exc()
        raise


async def main(
        config_manager: "ConfigManager",
        loop: asyncio.AbstractEventLoop,
        startup_tasks: list[Callable] | None = None,
) -> None:
    """
    Execute the main trading loop.

    Args:
        config_manager: Application configuration manager
        loop: Asyncio event loop
        startup_tasks: Optional list of async startup tasks
    """
    try:
        # Run startup tasks if provided
        if startup_tasks:
            config_manager.general_log.info("Running startup tasks...")
            coros = [task() for task in startup_tasks]
            await asyncio.gather(*coros)
            config_manager.general_log.info("Startup tasks finished.")

        try:
            # Create and manage HTTP session
            session = aiohttp.ClientSession()
            controller = config_manager.controller
            controller.http_session = session
            controller._http_session_owner = True
            strategy = config_manager.strategy_instance
            if hasattr(strategy, "http_session"):
                strategy.http_session = session

            config_manager.general_log.info(
                "Performing initial operation (creating or resuming orders)..."
            )
            await controller.main_init_loop()

            if controller.shutdown_event.is_set():
                config_manager.general_log.info(
                    "Shutdown requested during initial operation. Exiting without starting main loop."
                )
                return

            # Configure main loop interval
            operation_interval: int = strategy.get_operation_interval()
            config_manager.general_log.info(
                f"Using operation interval of {operation_interval} seconds "
                f"for {config_manager.strategy} strategy."
            )

            flush_timer: float = time.time()
            await controller.main_loop()  # Initial run
            operation_timer: float = time.time()

            while not controller.shutdown_event.is_set():
                current_time: float = time.time()

                # Flush cancelled orders periodically
                if current_time - flush_timer > FLUSH_DELAY:
                    xbm = config_manager.xbridge_manager
                    await xbm.dxflushcancelledorders()
                    flush_timer = current_time

                sleep_needed: float = operation_interval - (
                        current_time - operation_timer
                )
                if sleep_needed <= 0:
                    await controller.main_loop()
                    operation_timer = current_time

                # Short sleep while checking for shutdown
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        controller.shutdown_event.wait(), timeout=SLEEP_INTERVAL
                    )
        finally:
            await controller.close_http_session()
    except asyncio.CancelledError:
        config_manager.general_log.info(
            "Main task cancelled. Preparing for shutdown..."
        )
        raise
