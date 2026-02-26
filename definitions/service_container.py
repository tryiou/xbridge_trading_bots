"""
Service Container for Dependency Injection.

This module provides a centralized service container for managing
dependencies across the application, enabling better testability and loose coupling.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import (
    Any,
    Generic,
    Optional,
    Protocol,
    TypeVar,
    runtime_checkable,
)

from definitions.circuit_breaker import (
    CircuitBreakerManager,
    get_circuit_breaker_manager,
)

T = TypeVar("T")


class ServiceNotFoundError(Exception):
    """Exception raised when a requested service is not registered."""

    pass


class ServiceAlreadyRegisteredError(Exception):
    """Exception raised when trying to register a service that already exists."""

    pass


@runtime_checkable
class IConfigManager(Protocol):
    """Protocol defining the ConfigManager interface required by managers."""

    strategy: str
    ROOT_DIR: str
    general_log: logging.Logger
    trade_log: logging.Logger
    ccxt_log: logging.Logger
    error_handler: Any
    controller: Any

    @property
    def config_xbridge(self) -> Any: ...

    @property
    def config_ccxt(self) -> Any: ...


@runtime_checkable
class IXBridgeManager(Protocol):
    """Protocol defining the XBridgeManager interface."""

    blocknet_user_rpc: str
    blocknet_port_rpc: int
    blocknet_password_rpc: str
    xbridge_conf: dict[str, dict[str, Any]] | None
    xbridge_fees_estimate: dict[str, dict[str, Any] | None]
    logger: logging.Logger

    async def rpc_wrapper(
        self,
        method: str,
        params: list[Any] | None = None,
        shutdown_event: asyncio.Event | None = None,
        use_shutdown_event: bool = True,
    ) -> Any: ...

    async def gettokenbalances(self) -> Any: ...

    async def gettokenutxo(self, token: str, used: bool = False) -> Any: ...

    async def getlocaltokens(self) -> Any: ...

    async def makeorder(
        self,
        maker: str,
        makeramount: float,
        makeraddress: str,
        taker: str,
        takeramount: float,
        takeraddress: str,
    ) -> Any: ...

    async def cancelorder(
        self, order_id: str, use_shutdown_event: bool = True
    ) -> Any: ...

    async def getorderstatus(self, oid: str) -> Any: ...


@runtime_checkable
class ICCXTManager(Protocol):
    """Protocol defining the CCXTManager interface."""

    logger: logging.Logger
    error_handler: Any
    cex_orderbook: dict[str, Any] | None

    def init_ccxt_instance(
        self,
        exchange: str,
        hostname: str | None = None,
        private_api: bool = False,
        debug_level: int = 1,
    ) -> Any: ...

    async def ccxt_call_fetch_order_book(
        self, ccxt_o: Any, symbol: str, limit: int = 25, ignore_timer: bool = False
    ) -> dict[str, Any] | None: ...

    async def ccxt_call_fetch_ticker(
        self, ccxt_o: Any, symbol: str
    ) -> dict[str, Any] | None: ...

    async def ccxt_call_fetch_free_balance(
        self, ccxt_o: Any
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class IErrorHandler(Protocol):
    """Protocol defining the ErrorHandler interface."""

    logger: logging.Logger

    def handle(
        self, error: Exception, context: dict[str, Any] | None = None
    ) -> bool: ...

    async def handle_async(
        self, error: Exception, context: dict[str, Any] | None = None
    ) -> bool: ...


class ServiceContainer(Generic[T]):
    """
    Generic service container for dependency injection.

    Supports:
    - Singleton registration (one instance per container)
    - Factory registration (new instance each time)
    - Lazy initialization

    Usage:
        container = ServiceContainer()
        container.register_singleton(ICache, RedisCache)
        cache = container.resolve(ICache)
    """

    def __init__(self, name: str = "default"):
        self.name = name
        self._singletons: dict[type, Any] = {}
        self._factories: dict[type, Callable[..., Any]] = {}
        self._logger = logging.getLogger(f"service_container.{name}")

    def register_singleton(
        self, interface: type[T], implementation: type[T] | T, *args: Any, **kwargs: Any
    ) -> None:
        """
        Register a singleton service.

        Args:
            interface: The interface/type to register
            implementation: The concrete implementation or instance
            *args: Positional arguments for instantiation
            **kwargs: Keyword arguments for instantiation
        """
        if interface in self._singletons or interface in self._factories:
            raise ServiceAlreadyRegisteredError(
                f"Service {interface.__name__} is already registered"
            )

        if callable(implementation) and not isinstance(implementation, type):
            # It's already an instance
            self._singletons[interface] = implementation
            self._logger.debug(f"Registered singleton instance: {interface.__name__}")
        elif callable(implementation):
            # It's a class, store factory for later instantiation
            self._singletons[interface] = {
                "class": implementation,
                "args": args,
                "kwargs": kwargs,
            }
            self._logger.debug(f"Registered singleton factory: {interface.__name__}")
        else:
            raise TypeError(
                f"Implementation must be a class or callable, got {type(implementation)}"
            )

    def register_factory(self, interface: type[T], factory: Callable[..., T]) -> None:
        """
        Register a factory service.

        A factory creates a new instance each time resolve is called.

        Args:
            interface: The interface/type to register
            factory: A callable that returns an instance
        """
        if interface in self._singletons or interface in self._factories:
            raise ServiceAlreadyRegisteredError(
                f"Service {interface.__name__} is already registered"
            )

        self._factories[interface] = factory
        self._logger.debug(f"Registered factory: {interface.__name__}")

    def register_instance(self, interface: type[T], instance: T) -> None:
        """
        Register an existing instance as a singleton.

        Args:
            interface: The interface/type to register
            instance: The instance to register
        """
        if interface in self._singletons:
            self._logger.warning(
                f"Overwriting existing singleton: {interface.__name__}"
            )

        self._singletons[interface] = instance
        self._logger.debug(f"Registered instance: {interface.__name__}")

    def resolve(self, interface: type[T]) -> T:
        """
        Resolve a service from the container.

        Args:
            interface: The interface/type to resolve

        Returns:
            An instance of the requested service

        Raises:
            ServiceNotFoundError: If the service is not registered
        """
        # Check for singleton instance
        if interface in self._singletons:
            entry = self._singletons[interface]

            # If it's a dict, it's a lazy singleton (factory stored)
            if isinstance(entry, dict):
                cls = entry["class"]
                args = entry.get("args", ())
                kwargs = entry.get("kwargs", {})
                instance = cls(*args, **kwargs)
                self._singletons[interface] = instance
                self._logger.debug(f"Created lazy singleton: {interface.__name__}")

            return self._singletons[interface]

        # Check for factory
        if interface in self._factories:
            factory = self._factories[interface]
            instance = factory()
            self._logger.debug(f"Created instance via factory: {interface.__name__}")
            return instance

        raise ServiceNotFoundError(
            f"Service {interface.__name__} is not registered in container '{self.name}'"
        )

    def resolve_optional(
        self, interface: type[T], default: T | None = None
    ) -> T | None:
        """
        Resolve a service, returning default if not found.

        Args:
            interface: The interface/type to resolve
            default: Default value if not found

        Returns:
            An instance or default
        """
        try:
            return self.resolve(interface)
        except ServiceNotFoundError:
            return default

    def is_registered(self, interface: type[T]) -> bool:
        """Check if a service is registered."""
        return interface in self._singletons or interface in self._factories

    def clear(self) -> None:
        """Clear all registered services."""
        self._singletons.clear()
        self._factories.clear()
        self._logger.debug(f"Cleared all services from container '{self.name}'")


class ApplicationContainer:
    """
    Application-wide service container with pre-configured services.

    This is the main container that holds all application services.
    """

    _instance: Optional["ApplicationContainer"] = None

    def __new__(cls) -> "ApplicationContainer":
        """Ensure singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self.name = "application_container"
        self.logger = logging.getLogger("application_container")

        # Core containers
        self.services = ServiceContainer("core")
        self.strategies = ServiceContainer("strategies")

        # Circuit breaker manager
        self.circuit_breaker_manager: CircuitBreakerManager = (
            get_circuit_breaker_manager()
        )

        self._setup_core_services()

    def _setup_core_services(self) -> None:
        """Set up core application services."""
        # These will be populated by ConfigManager during initialization
        self.logger.debug("Application container initialized")

    def reset(self) -> None:
        """Reset the container (useful for testing)."""
        self.services.clear()
        self.strategies.clear()
        self.circuit_breaker_manager.reset_all()
        self._setup_core_services()
        self.logger.info("Application container reset")


def get_application_container() -> ApplicationContainer:
    """Get the global application container instance."""
    return ApplicationContainer()


# Protocol definitions for type-safe service resolution
class SupportsConfigManager:
    """Protocol for components that need ConfigManager."""

    config_manager: Any


class SupportsXBridgeManager:
    """Protocol for components that need XBridgeManager."""

    xbridge_manager: Any


class SupportsCCXTManager:
    """Protocol for components that need CCXTManager."""

    ccxt_manager: Any
