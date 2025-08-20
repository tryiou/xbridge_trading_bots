import threading
from abc import ABC, abstractmethod
from typing import Optional, Callable, List, Dict, Any

from definitions.starter import run_async_main


class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.
    Defines the interface for strategy-specific logic with error handling.
    """

    def __init__(self, config_manager, controller=None):
        self.config_manager = config_manager
        self.controller = controller  # MainController instance, set later
        # All derived strategies inherit access to the error handler
        self.error_handler = config_manager.error_handler
        self._bot_thread: Optional[threading.Thread] = None
        self.is_running = False
        self._critical_error_callback: Optional[Callable] = None

    @abstractmethod
    def initialize_strategy_specifics(self, **kwargs):
        """
        Initializes strategy-specific configurations and components.
        This method will be called by ConfigManager.initialize.
        """
        pass

    @abstractmethod
    def get_tokens_for_initialization(self, **kwargs) -> list:
        """
        Returns a list of token symbols required for the strategy.
        """
        pass

    @abstractmethod
    def get_pairs_for_initialization(self, tokens_dict, **kwargs) -> dict:
        """
        Returns a dictionary of Pair objects required for the strategy.
        """
        pass

    def initialize_tokens_and_pairs(self, **kwargs):
        """Initializes token and pair objects based on strategy configuration."""
        from definitions.token import Token
        tokens_list = self.get_tokens_for_initialization(**kwargs)

        # Initialize tokens
        self.config_manager.tokens = {}
        if 'BTC' not in tokens_list:
            self.config_manager.tokens['BTC'] = Token(
                'BTC', strategy=self.config_manager.strategy, config_manager=self.config_manager, dex_enabled=False
            )
        for token_symbol in list(set(tokens_list)):
            if token_symbol not in self.config_manager.tokens:
                dex_enabled = self.config_manager.strategy == 'arbitrage' or token_symbol != 'BTC'
                self.config_manager.tokens[token_symbol] = Token(
                    token_symbol, strategy=self.config_manager.strategy, config_manager=self.config_manager,
                    dex_enabled=dex_enabled
                )

        # Initialize pairs
        self.config_manager.pairs = self.get_pairs_for_initialization(self.config_manager.tokens, **kwargs)

    def get_dex_history_file_path(self, pair_name: str) -> str:
        """
        Returns the file path for storing DEX order history for a given pair.
        This can be overridden by subclasses if a different naming scheme is needed.
        """
        unique_id = pair_name.replace("/", "_")
        return f"{self.config_manager.ROOT_DIR}/data/{self.config_manager.strategy}_{unique_id}_last_order.yaml"

    def get_dex_token_address_file_path(self, token_symbol: str) -> str:
        """
        Returns the file path for storing DEX token address for a given token.
        """
        return f"{self.config_manager.ROOT_DIR}/data/{self.config_manager.strategy}_{token_symbol}_addr.yaml"

    def get_tokens_from_pair_configs(self, pair_configs: List[Dict[str, Any]]) -> List[str]:
        """Helper to extract unique token symbols from a list of pair configuration dictionaries."""
        tokens = set()
        for cfg in pair_configs:
            if cfg.get('enabled', True):
                t1, t2 = cfg['pair'].split('/')
                tokens.add(t1)
                tokens.add(t2)
        return list(tokens)

    @abstractmethod
    def should_update_cex_prices(self) -> bool:
        """
        Indicates whether the strategy requires CEX price updates from the main PriceHandler.
        """
        pass

    # Methods for MainController to call strategy-specific actions
    @abstractmethod  # Renamed for clarity
    async def thread_init_async_action(self, pair_instance):
        """
        Strategy-specific asynchronous action for initial pair processing.
        """
        pass

    @abstractmethod  # Renamed for clarity
    async def process_pair_async(self, pair_instance):
        """
        Strategy-specific asynchronous action for the main loop processing.
        Should implement error handling using self.error_handler.
        """
        pass

    async def safe_thread_loop(self, pair_instance):
        try:
            await self.process_pair_async(pair_instance)
        except Exception as e:
            context = {"pair": pair_instance.symbol, "component": "strategy_loop"}
            await self.config_manager.error_handler.handle_async(e, context=context)

    async def safe_order_creation(self, pair_instance, create_func):
        """Handles safe order creation with error classification"""
        try:
            await create_func()
        except Exception as e:
            context = {"pair": pair_instance.symbol, "stage": "order_creation"}
            await self.config_manager.error_handler.handle_async(e, context=context)

    @abstractmethod
    def get_operation_interval(self) -> int:
        """
        Returns the desired operation interval in seconds for the strategy.
        """
        pass

    @abstractmethod
    def get_startup_tasks(self) -> list:
        """
        Returns a list of callables that return async tasks (coroutines)
        to be run at startup. Deferring the creation of the coroutine
        ensures that the controller and its shutdown event are available.
        """
        pass

    def register_critical_error_callback(self, callback: Callable):
        """Registers a callback to be invoked on critical, unhandled exceptions."""
        self._critical_error_callback = callback

    def _thread_wrapper(self, func, *args):
        """Wraps the bot thread's target function for centralized error handling."""
        try:
            func(*args)
        except Exception as e:
            # If a critical error callback is registered (e.g., by the GUI), invoke it.
            if self._critical_error_callback:
                # The callback implementation is responsible for thread-safety (e.g., using root.after).
                self._critical_error_callback(e)
            else:
                # Fallback for non-GUI execution: log and potentially exit.
                self.config_manager.general_log.critical(f"Unhandled exception in bot thread: {e}", exc_info=True)

    def start(self):
        """Starts the strategy in a separate thread."""
        if self.is_running:
            self.config_manager.general_log.warning("Attempted to start an already running strategy.")
            return

        startup_tasks = self.get_startup_tasks()
        self._bot_thread = threading.Thread(
            target=self._thread_wrapper,
            args=(run_async_main, self.config_manager, startup_tasks),
            daemon=True,
            name=f"BotThread-{self.config_manager.strategy}"
        )
        self.config_manager.general_log.info(f"Starting {self.config_manager.strategy.capitalize()} bot thread.")
        self._bot_thread.start()
        self.is_running = True

    def stop(self, timeout: float = 45.0):
        """
        Stops the strategy and waits for its thread to terminate.
        This is a blocking call.
        """
        if not self.is_running:
            self.config_manager.general_log.warning("Attempted to stop a non-running strategy.")
            return

        self.config_manager.general_log.info(f"Attempting to stop {self.config_manager.strategy} bot...")

        # Signal the asyncio event loop to shut down
        if self.config_manager.controller and self.config_manager.controller.loop:
            loop = self.config_manager.controller.loop
            if not loop.is_closed() and loop.is_running():
                # Use a lock to ensure thread-safe access to the shutdown event
                with self.config_manager.resource_lock:
                    loop.call_soon_threadsafe(self.config_manager.controller.shutdown_event.set)
        else:
            self.config_manager.general_log.warning("No active controller/loop to signal for shutdown.")

        # Wait for the thread to finish (only if it's a separate thread)
        if self._bot_thread:
            self._bot_thread.join(timeout=timeout)

            if self._bot_thread.is_alive():
                self.config_manager.general_log.warning(
                    f"Bot thread for {self.config_manager.strategy} did not terminate gracefully within {timeout}s.")
            else:
                self.config_manager.general_log.info(
                    f"{self.config_manager.strategy.capitalize()} bot stopped successfully.")

        self.is_running = False
        self._bot_thread = None
