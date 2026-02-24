import asyncio
import logging
import threading

logger = logging.getLogger(__name__)


def run_async_in_bg(coro_func, *args, **kwargs):
    """
    Executes an async function in a background thread safely.
    Replaces boilerplate loop creation to adhere to DRY principles.
    """

    def worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro_func(*args, **kwargs))
        except Exception as e:
            logger.error("Error in background async task: %s", e, exc_info=True)
        finally:
            loop.close()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread
