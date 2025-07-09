# gui_v2/utils/async_updater.py
import queue
import threading
import time
import logging
import asyncio
from typing import Callable, Any, List, Dict

logger = logging.getLogger(__name__)

class AsyncUpdater:
    """
    A generic class to manage asynchronous data fetching in a separate thread
    and thread-safe updates to a Tkinter GUI component.
    """

    def __init__(self, tk_widget: Any, update_target_method: Callable,
                 fetch_data_callable: Callable, update_interval_ms: int = 1500,
                 process_interval_ms: int = 250, name: str = "AsyncUpdater"):
        """
        Initializes the AsyncUpdater.

        :param tk_widget: The Tkinter widget (e.g., root, frame) that provides .after and .after_cancel methods.
        :param update_target_method: The method on the GUI component that will receive and display the data.
                                     This method must be thread-safe (i.e., called via `tk_widget.after`).
        :param fetch_data_callable: A callable (function or method) that fetches the data in a blocking manner.
                                    This will be run in a separate thread.
        :param update_interval_ms: How often (in milliseconds) the `fetch_data_callable` should be run.
        :param process_interval_ms: How often (in milliseconds) the GUI update queue should be checked.
        :param name: A name for this updater instance, used for logging.
        """
        self.tk_widget = tk_widget
        self.update_target_method = update_target_method
        self.fetch_data_callable = fetch_data_callable
        self.update_interval_sec = update_interval_ms / 1000.0
        self.process_interval_ms = process_interval_ms
        self.name = name

        self._update_queue = queue.Queue()
        self._updater_thread: threading.Thread | None = None
        self._running = False
        self._process_id = None # For scheduling _process_updates

    def start(self):
        """Starts the background data fetching thread and GUI update processing."""
        if self._running:
            logger.debug(f"{self.name}: Already running, ignoring start call.")
            return

        logger.info(f"{self.name}: Starting updater.")
        self._running = True
        self._updater_thread = threading.Thread(target=self._run_fetcher, daemon=True, name=f"{self.name}Fetcher")
        self._updater_thread.start()
        self._process_id = self.tk_widget.after(self.process_interval_ms, self._process_updates)

    def stop(self):
        """Signals the updater to stop and waits for the background thread to finish."""
        if not self._running:
            logger.debug(f"{self.name}: Not running, ignoring stop call.")
            return

        logger.info(f"{self.name}: Stopping updater.")
        self._running = False
        self._update_queue.put(None) # Signal the fetcher thread to stop
        
        if self._process_id:
            self.tk_widget.after_cancel(self._process_id)
            self._process_id = None

        # It's a daemon thread, so no need to explicitly join in most GUI shutdown scenarios,
        # but for completeness or if non-daemon, one might add:
        # if self._updater_thread and self._updater_thread.is_alive():
        #     self._updater_thread.join(timeout=self.update_interval_sec * 2 + 1) # Give it some time

    def _run_fetcher(self):
        """
        The main loop for the background data fetching thread.
        Puts fetched data into the queue for GUI processing.
        """
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while self._running:
            try:
                # If fetch_data_callable is async, run it in the event loop
                if asyncio.iscoroutinefunction(self.fetch_data_callable):
                    data = loop.run_until_complete(self.fetch_data_callable())
                else:
                    # Otherwise, run it as a regular blocking call
                    data = self.fetch_data_callable()

                if data is not None:
                    self._update_queue.put(data)
                
                # Check for stop signal in queue
                if not self._update_queue.empty() and self._update_queue.queue[0] is None:
                    self._update_queue.get() # Consume the None
                    break

                time.sleep(self.update_interval_sec)
            except Exception as e:
                logger.error(f"{self.name}: Error in fetcher thread: {e}", exc_info=True)
                time.sleep(self.update_interval_sec * 2) # Wait longer on error
        
        # Close the event loop when the thread terminates
        if not loop.is_closed():
            loop.close()

        logger.info(f"{self.name}: Fetcher thread terminated.")


    def _process_updates(self):
        """
        Processes queued data updates in the main Tkinter thread.
        """
        if not self._running:
            return # Stop processing if updater is no longer running

        try:
            while not self._update_queue.empty():
                data = self._update_queue.get_nowait()
                if data is None: # Stop signal
                    return
                self.update_target_method(data)
        except queue.Empty:
            pass # No updates yet
        except Exception as e:
            logger.error(f"{self.name}: Error processing updates in main thread: {e}", exc_info=True)
        finally:
            if self._running: # Only reschedule if still running
                self._process_id = self.tk_widget.after(self.process_interval_ms, self._process_updates)