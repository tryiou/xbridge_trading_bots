import asyncio
import logging
import threading

from proxy_ccxt import AsyncPriceService


class ProxyManager:
    """Deep module for managing CCXT proxy service.

    Handles proxy service lifecycle (start/stop) and reference counting
    for multiple strategies using the proxy.

    Interface:
        ensure_running() -> None: Start proxy if not running.
        register() -> None: Register a strategy using the proxy.
        unregister() -> None: Unregister a strategy; stop proxy if refcount reaches 0.
        get_port() -> int: Get the proxy port number.
        get_proxy_url() -> str: Get the full proxy URL.
    """

    def __init__(self, port: int = 2233) -> None:
        self._port = port
        self._service_instance: AsyncPriceService | None = None
        self._service_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._ref_count: int = 0
        self._logger = logging.getLogger("proxy_manager")

    def ensure_running(self) -> None:
        """Start the proxy service if not already running."""
        with self._lock:
            if self._service_instance is not None and self._service_thread and self._service_thread.is_alive():
                return
            self._logger.info(f"Starting proxy service on port {self._port}...")
            try:
                service = AsyncPriceService(port=self._port)
                self._service_instance = service

                def _thread_target():
                    asyncio.run(service.run())

                self._service_thread = threading.Thread(
                    target=_thread_target,
                    name="ProxyService",
                    daemon=True,
                )
                self._service_thread.start()
                self._logger.info("Proxy service started")
            except Exception as e:
                self._logger.error(f"Failed to start proxy service: {e!s}")
                self._service_instance = None
                self._service_thread = None

    def register(self) -> None:
        """Register a strategy using the proxy. Increments reference count."""
        with self._lock:
            self._ref_count += 1
            self._logger.debug(
                f"Strategy registered. New refcount: {self._ref_count}"
            )

    def unregister(self) -> None:
        """Unregister a strategy. Stops proxy if refcount reaches 0."""
        with self._lock:
            if self._ref_count > 0:
                self._ref_count -= 1
            self._logger.debug(
                f"Strategy unregistered. New refcount: {self._ref_count}"
            )
            if self._ref_count == 0:
                self._stop_service()

    def get_port(self) -> int:
        """Get the proxy port number."""
        return self._port

    def get_proxy_url(self) -> str:
        """Get the full proxy URL."""
        return f"127.0.0.1:{self._port}"

    def _stop_service(self) -> None:
        """Stop the proxy service. Must be called with self._lock held."""
        if not self._service_instance or not self._service_thread:
            return
        if not self._service_thread.is_alive():
            self._service_instance = None
            self._service_thread = None
            return
        try:
            self._logger.info("Stopping proxy service...")
            self._service_instance.stop()
            self._service_thread.join(timeout=10.0)
            if self._service_thread.is_alive():
                self._logger.warning("Proxy service thread failed to stop after 10s")
            else:
                self._logger.info("Proxy service stopped successfully")
        except Exception as e:
            self._logger.error(f"Error during proxy service stop: {e!s}")
            return
        finally:
            self._service_instance = None
            self._service_thread = None
            self._logger.info("Proxy state cleared")
