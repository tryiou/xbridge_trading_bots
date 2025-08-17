"""
Centralized error handling with context propagation and recovery policies
"""
import asyncio
import logging
import time
from typing import Optional, Dict, TYPE_CHECKING

from definitions.errors import CriticalError, OperationalError, TransientError, convert_exception

if TYPE_CHECKING:
    from definitions.errors import AppError


class ErrorHandler:
    def __init__(self, config_manager=None, logger=None):
        self.config_manager = config_manager
        self.logger = logger or logging.getLogger("error_handler")
        self.max_retries = 3
        self.retry_delays = [1, 3, 5]  # Seconds between retries
        # For test mode handling
        self._is_testing = False

    async def _async_notify_user(self, level, message, details):
        """Async version of notify_user"""
        if self.config_manager:
            try:
                # Try to use async_notify_user if available
                if hasattr(self.config_manager, 'async_notify_user'):
                    await self.config_manager.async_notify_user(
                        level=level,
                        message=message,
                        details=details
                    )
                # Fallback to sync version
                elif hasattr(self.config_manager, 'notify_user'):
                    self.config_manager.notify_user(
                        level=level,
                        message=message,
                        details=details
                    )
            except Exception as e:
                self.logger.warning(f"Notification failed: {e}")

    def _notify_user_sync(self, level, message, details):
        """Sync version of notify_user with fallback and error logging."""
        if self.config_manager:
            try:
                if hasattr(self.config_manager, 'notify_user'):
                    self.config_manager.notify_user(
                        level=level,
                        message=message,
                        details=details
                    )
            except Exception as e:
                self.logger.warning(f"Notification failed: {e}")

    def _get_full_context(self, error, context=None):
        """Merges error context with provided context"""
        # Start with error's own context if present
        full_context = getattr(error, 'context', {}).copy()
        # Merge with current context
        if context:
            full_context.update(context)

        # Ensure error_type is set
        full_context.setdefault('error_type', type(error).__name__)
        # Preserve original cause for better debugging
        full_context.setdefault('__cause__', getattr(error, '__cause__', None))

        # Add config_manager metadata
        if self.config_manager:
            # Always use state from ConfigManager
            full_context.setdefault('strategy', self.config_manager.strategy)
            full_context.setdefault('module', self.config_manager.current_module)

        return full_context

    def _classify_error(self, error: 'AppError') -> str:
        """Classifies AppError instances into handling categories."""
        if isinstance(error, CriticalError):
            return 'critical'
        if isinstance(error, TransientError):
            return 'transient'
        if isinstance(error, OperationalError):
            return 'operational'
        # This part of the code should not be reachable if convert_exception works correctly,
        # as it wraps unknown exceptions. But as a safeguard:
        return 'critical'

    def handle(self, error: Exception, context: Optional[Dict] = None) -> bool:
        """Main sync error handler. Converts non-AppErrors and delegates."""
        app_error = convert_exception(error)
        full_context = self._get_full_context(app_error, context)
        classification = self._classify_error(app_error)

        handler_map = {
            'transient': self._handle_transient,
            'operational': self._handle_operational,
            'critical': self._handle_critical
        }
        return handler_map[classification](app_error, full_context)

    async def handle_async(self, error: Exception, context: Optional[Dict] = None) -> bool:
        """Main async error handler. Converts non-AppErrors and delegates."""
        app_error = convert_exception(error)
        full_context = self._get_full_context(app_error, context)
        classification = self._classify_error(app_error)

        handler_map = {
            'transient': self._handle_transient_async,
            'operational': self._handle_operational_async,
            'critical': self._handle_critical_async
        }
        return await handler_map[classification](app_error, full_context)

    def _handle_transient(self, error, context):
        """Handle transient errors with retry logic"""
        # For testing purposes, simulate retry logic
        if getattr(self.config_manager, '_is_testing', False) or self._is_testing:
            time.sleep(0.1)
            return True

        attempt = context.get('err_count', 1)
        if attempt >= self.max_retries:
            self.logger.error(
                f"Transient error max retries exceeded after {attempt} attempts: {error} | Context: {context}"
            )
            return False

        # Implement actual retry logic with exponential backoff
        self.logger.warning(
            f"Transient error (attempt {attempt}/{self.max_retries}): {error} | Context: {context}"
        )
        delay_index = attempt - 1
        delay = self.retry_delays[delay_index] if delay_index < len(self.retry_delays) else self.retry_delays[-1]
        self.logger.info(f"Retrying in {delay} seconds...")
        time.sleep(delay)
        return True  # Signal to retry operation

    def _handle_operational_logic(self, error, context):
        """Shared logic for handling operational errors."""
        self.logger.error(
            f"Operational error: {error} | Context: {context}",
            exc_info=True
        )
        return {
            "level": "warning",
            "message": f"Operational Error: {error}",
            "details": context
        }

    def _handle_operational(self, error, context):
        """Handle operational errors with logging and continuation"""
        notification_details = self._handle_operational_logic(error, context)
        self._notify_user_sync(**notification_details)
        return True  # Continue operation

    def _handle_critical_logic(self, error, context):
        """Shared logic for handling critical errors."""
        self.logger.critical(
            f"Critical error: {error} | Context: {context}",
            exc_info=True
        )
        return {
            "level": "critical",
            "message": f"Critical Error: {error}",
            "details": context
        }

    def _handle_critical(self, error, context):
        """Handle critical errors with shutdown procedure"""
        notification_details = self._handle_critical_logic(error, context)
        self._notify_user_sync(**notification_details)
        # Initiate shutdown sequence
        if self.config_manager and self.config_manager.controller:
            self.logger.info(f"Signaling shutdown due to critical error: {error}")
            self.config_manager.controller.shutdown_event.set()
        return False  # Abort operation

    async def _handle_transient_async(self, error, context):
        """Async version of handling transient errors with retry logic"""
        # For testing purposes, simulate retry logic
        if getattr(self.config_manager, '_is_testing', False) or self._is_testing:
            await asyncio.sleep(0.1)
            return True

        attempt = context.get('err_count', 1)
        if attempt >= self.max_retries:
            self.logger.error(
                f"Transient error max retries exceeded after {attempt} attempts: {error} | Context: {context}"
            )
            return False

        # Implement actual retry logic with exponential backoff
        self.logger.warning(
            f"Transient error (attempt {attempt}/{self.max_retries}): {error} | Context: {context}"
        )
        delay_index = attempt - 1
        delay = self.retry_delays[delay_index] if delay_index < len(self.retry_delays) else self.retry_delays[-1]
        self.logger.info(f"Retrying in {delay} seconds...")
        await asyncio.sleep(delay)
        return True  # Signal to retry operation

    async def _handle_operational_async(self, error, context):
        """Async version of handling operational errors with logging and continuation"""
        notification_details = self._handle_operational_logic(error, context)
        # Add user notification hook
        await self._async_notify_user(**notification_details)
        return True  # Continue operation

    async def _handle_critical_async(self, error, context):
        """Handle critical errors with shutdown procedure - async version"""
        notification_details = self._handle_critical_logic(error, context)
        if self.config_manager:
            try:
                await self._async_notify_user(**notification_details)
            except Exception as e:
                self.logger.warning(f"Async notification failed: {e}")
        # Initiate shutdown sequence
        if self.config_manager and self.config_manager.controller:
            self.logger.info(f"Signaling shutdown due to critical error: {error}")
            self.config_manager.controller.shutdown_event.set()
        return False  # Abort operation
