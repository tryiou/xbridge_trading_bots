from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.
    Defines the interface for strategy-specific logic.
    """

    def __init__(self, config_manager, controller=None):
        self.config_manager = config_manager
        self.controller = controller  # MainController instance, set later

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

    @abstractmethod
    def build_sell_order_details(self, dex_pair_instance, manual_dex_price=None) -> tuple:
        """
        Strategy-specific logic to determine amount and offset for a sell order.
        Returns (amount, offset).
        """
        pass

    @abstractmethod
    def calculate_sell_price(self, dex_pair_instance, manual_dex_price=None) -> float:
        """
        Strategy-specific logic to calculate the sell price.
        """
        pass

    @abstractmethod
    def build_buy_order_details(self, dex_pair_instance, manual_dex_price=None) -> tuple:
        """
        Strategy-specific logic to determine amount and spread for a buy order.
        Returns (amount, spread).
        """
        pass

    @abstractmethod
    def determine_buy_price(self, dex_pair_instance, manual_dex_price=None) -> float:
        """
        Strategy-specific logic to determine the buy price.
        """
        pass

    @abstractmethod
    def get_price_variation_tolerance(self, dex_pair_instance) -> float:
        """
        Returns the price variation tolerance for the strategy.
        """
        pass

    @abstractmethod
    def calculate_variation_based_on_side(self, dex_pair_instance, current_order_side: str, cex_price: float,
                                          original_price: float) -> float:
        """
        Strategy-specific logic to calculate price variation based on order side.
        """
        pass

    @abstractmethod
    def calculate_default_variation(self, dex_pair_instance, cex_price: float, original_price: float) -> float:
        """
        Strategy-specific logic to calculate default price variation.
        """
        pass

    @abstractmethod
    def init_virtual_order_logic(self, dex_pair_instance, order_history: dict):
        """
        Strategy-specific logic for initializing a virtual order.
        """
        pass

    @abstractmethod
    def handle_order_status_error(self, dex_pair_instance):
        """
        Strategy-specific handling for order status errors.
        """
        pass

    @abstractmethod
    def reinit_virtual_order_after_price_variation(self, dex_pair_instance, disabled_coins: list):
        """
        Strategy-specific logic to reinitialize virtual order after price variation.
        """
        pass

    @abstractmethod
    def handle_finished_order(self, dex_pair_instance, disabled_coins: list):
        """
        Strategy-specific logic after an order finishes.
        """
        pass

    @abstractmethod
    def handle_error_swap_status(self, dex_pair_instance):
        """
        Strategy-specific logic for handling ERROR_SWAP status.
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
        """
        pass

    @abstractmethod
    def get_operation_interval(self) -> int:
        """
        Returns the desired operation interval in seconds for the strategy.
        """
        pass
