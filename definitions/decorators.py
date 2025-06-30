import asyncio
import logging
from functools import wraps


def retry_on_failure(retries=3, delay=5):
    """
    A decorator to retry an async function if it returns None or raises an exception.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    result = await func(*args, **kwargs)
                    if result is not None:
                        return result
                    logging.warning(
                        f"Attempt {attempt + 1}/{retries} for {func.__name__} returned None. Retrying in {delay}s...")
                except Exception as e:
                    logging.warning(
                        f"Attempt {attempt + 1}/{retries} for {func.__name__} failed with exception: {e}. Retrying in {delay}s...")

                if attempt < retries - 1:
                    await asyncio.sleep(delay)
            logging.error(f"Function {func.__name__} failed after {retries} attempts.")
            return None

        return wrapper

    return decorator
