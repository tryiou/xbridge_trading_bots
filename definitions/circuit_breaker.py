"""
Circuit Breaker implementation for resilient external service calls.

This module provides a CircuitBreaker pattern implementation to prevent
cascading failures when external services (XBridge RPC, CCXT) are unavailable.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, TypeVar, Generic

from definitions.errors import TransientError, OperationalError


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""
    failure_threshold: int = 5
    success_threshold: int = 2
    timeout: float = 30.0
    excluded_exceptions: tuple = ()


@dataclass
class CircuitBreakerStats:
    """Statistics for circuit breaker."""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    state_changes: list = field(default_factory=list)


T = TypeVar('T')


class CircuitBreakerError(Exception):
    """Exception raised when circuit breaker is open."""
    def __init__(self, message: str, state: CircuitState, recovery_timeout: float):
        self.state = state
        self.recovery_timeout = recovery_timeout
        super().__init__(message)


class CircuitBreaker(Generic[T]):
    """
    Circuit breaker implementation for preventing cascading failures.
    
    The circuit breaker has three states:
    - CLOSED: Normal operation, calls pass through
    - OPEN: Too many failures, calls are rejected immediately
    - HALF_OPEN: Testing recovery, limited calls allowed
    
    Usage:
        async with circuit_breaker.call():
            return await external_service()
    """
    
    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.logger = logger or logging.getLogger(f"circuit_breaker.{name}")
        
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()
        
        self.stats = CircuitBreakerStats()
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        return self._state
    
    def _should_attempt_recovery(self) -> bool:
        """Check if circuit should attempt recovery (transition to HALF_OPEN)."""
        if self._last_failure_time is None:
            return False
        return (time.time() - self._last_failure_time) >= self.config.timeout
    
    def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to a new state."""
        if self._state == new_state:
            return
            
        old_state = self._state
        self._state = new_state
        
        self.stats.state_changes.append({
            'from': old_state.value,
            'to': new_state.value,
            'timestamp': time.time()
        })
        
        self.logger.info(
            f"Circuit '{self.name}' state transition: {old_state.value} -> {new_state.value}"
        )
    
    async def _record_success(self) -> None:
        """Record a successful call."""
        async with self._lock:
            self.stats.successful_calls += 1
            self.stats.last_success_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
                    self._failure_count = 0
                    self._success_count = 0
                    self.logger.info(f"Circuit '{self.name}' recovered and closed")
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0
    
    async def _record_failure(self, exception: Exception) -> None:
        """Record a failed call."""
        async with self._lock:
            self.stats.failed_calls += 1
            self._last_failure_time = time.time()
            self.stats.last_failure_time = self._last_failure_time
            self._failure_count += 1
            
            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.OPEN)
                self._success_count = 0
                self.logger.warning(f"Circuit '{self.name}' failed during half-open, reopening")
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.config.failure_threshold:
                    self._transition_to(CircuitState.OPEN)
                    self.logger.warning(
                        f"Circuit '{self.name}' opened after {self._failure_count} failures"
                    )
    
    def _is_excluded_exception(self, exception: Exception) -> bool:
        """Check if exception type is excluded from circuit breaker."""
        return isinstance(exception, self.config.excluded_exceptions)
    
    async def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute a function with circuit breaker protection.
        
        Args:
            func: Async function to call
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func
            
        Returns:
            Result of func call
            
        Raises:
            CircuitBreakerError: If circuit is open
            Exception: Re-raises any exception from func (after recording)
        """
        async with self._lock:
            self.stats.total_calls += 1
            
            # Check if circuit is open and should reject calls
            if self._state == CircuitState.OPEN:
                if self._should_attempt_recovery():
                    self._transition_to(CircuitState.HALF_OPEN)
                    self._success_count = 0
                    self.logger.info(f"Circuit '{self.name}' attempting recovery (half-open)")
                else:
                    remaining_time = (
                        self.config.timeout - (time.time() - self._last_failure_time)
                        if self._last_failure_time else self.config.timeout
                    )
                    self.stats.rejected_calls += 1
                    raise CircuitBreakerError(
                        f"Circuit '{self.name}' is open. Retry after {remaining_time:.1f}s",
                        state=self._state,
                        recovery_timeout=remaining_time
                    )
        
        # Execute the function
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            await self._record_success()
            return result
            
        except CircuitBreakerError:
            # Re-raise circuit breaker errors directly
            raise
        except Exception as e:
            if not self._is_excluded_exception(e):
                await self._record_failure(e)
            raise
    
    def reset(self) -> None:
        """Manually reset the circuit breaker to closed state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None
        self.logger.info(f"Circuit '{self.name}' manually reset")
    
    def get_status(self) -> dict:
        """Get current circuit breaker status."""
        return {
            'name': self.name,
            'state': self._state.value,
            'failure_count': self._failure_count,
            'success_count': self._success_count,
            'total_calls': self.stats.total_calls,
            'successful_calls': self.stats.successful_calls,
            'failed_calls': self.stats.failed_calls,
            'rejected_calls': self.stats.rejected_calls,
            'last_failure_time': self.stats.last_failure_time,
            'last_success_time': self.stats.last_success_time,
        }


class CircuitBreakerManager:
    """
    Manager for multiple circuit breakers.
    
    Provides a centralized way to manage circuit breakers for different services.
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("circuit_breaker_manager")
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()
    
    def get_breaker(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None
    ) -> CircuitBreaker:
        """Get or create a circuit breaker by name."""
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name, config)
            self.logger.info(f"Created circuit breaker: {name}")
        return self._breakers[name]
    
    async def call(
        self,
        breaker_name: str,
        func: Callable[..., T],
        *args: Any,
        config: Optional[CircuitBreakerConfig] = None,
        **kwargs: Any
    ) -> T:
        """Execute a function with circuit breaker protection."""
        breaker = self.get_breaker(breaker_name, config)
        return await breaker.call(func, *args, **kwargs)
    
    def get_all_status(self) -> dict:
        """Get status of all circuit breakers."""
        return {
            name: breaker.get_status()
            for name, breaker in self._breakers.items()
        }
    
    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        for breaker in self._breakers.values():
            breaker.reset()
        self.logger.info("All circuit breakers reset")


_global_circuit_breaker_manager: Optional[CircuitBreakerManager] = None


def get_circuit_breaker_manager() -> CircuitBreakerManager:
    """Get global circuit breaker manager instance."""
    global _global_circuit_breaker_manager
    if _global_circuit_breaker_manager is None:
        _global_circuit_breaker_manager = CircuitBreakerManager()
    return _global_circuit_breaker_manager
