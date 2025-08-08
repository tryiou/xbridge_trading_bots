from abc import ABC, abstractmethod


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

    @abstractmethod
    def get_dex_history_file_path(self, pair_name: str) -> str:
        """
        Returns the file path for storing DEX order history for a given pair.
        """
        pass

    @abstractmethod
    def get_dex_token_address_file_path(self, token_symbol: str) -> str:
        """
        Returns the file path for storing DEX token address for a given token.
        """
        pass

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
    async def thread_loop_async_action(self, pair_instance):
        """
        Strategy-specific asynchronous action for the main loop processing.
        Should implement error handling using self.error_handler.
        """
        pass

    @abstractmethod
    def get_operation_interval(self) -> int:
        """
        Returns the desired operation interval in seconds for the strategy.
        """
        pass

    @abstractmethod
    def get_startup_tasks(self) -> list:
        """
        Returns a list of async tasks to be run at startup.
        """
        pass
