from abc import abstractmethod
from typing import TYPE_CHECKING

from .base_strategy import BaseStrategy

if TYPE_CHECKING:
    from ..definitions.pair import DexPair


class MakerStrategy(BaseStrategy):
    """
    Abstract base class for "maker" strategies that create and manage
    orders on the DEX order book (e.g., PingPong, BasicSeller).
    """

    def get_dex_history_file_path(self, pair_name: str) -> str:
        """
        Returns the file path for storing DEX order history for a given pair.
        This can be overridden by subclasses if a different naming scheme is needed.
        """
        unique_id = pair_name.replace("/", "_")
        return f"{self.config_manager.ROOT_DIR}/data/{self.config_manager.strategy}_{unique_id}_last_order.yaml"

    def get_dex_token_address_file_path(self, token_symbol: str) -> str:
        """
        Returns the file path for storing the DEX token address for a given token.
        """
        return f"{self.config_manager.ROOT_DIR}/data/{self.config_manager.strategy}_{token_symbol}_addr.yaml"

    @abstractmethod
    def build_sell_order_details(self, dex_pair_instance: 'DexPair', manual_dex_price=None) -> tuple:
        """
        Strategy-specific logic to determine amount and offset for a sell order.
        Returns (amount, offset).
        """
        pass

    @abstractmethod
    def calculate_sell_price(self, dex_pair_instance: 'DexPair', manual_dex_price=None) -> float:
        """
        Strategy-specific logic to calculate the sell price.
        """
        pass

    @abstractmethod
    def build_buy_order_details(self, dex_pair_instance: 'DexPair', manual_dex_price=None) -> tuple:
        """
        Strategy-specific logic to determine amount and spread for a buy order.
        Returns (amount, spread).
        """
        pass

    @abstractmethod
    def determine_buy_price(self, dex_pair_instance: 'DexPair', manual_dex_price=None) -> float:
        """
        Strategy-specific logic to determine the buy price.
        """
        pass

    @abstractmethod
    def get_price_variation_tolerance(self, dex_pair_instance: 'DexPair') -> float:
        """
        Returns the price variation tolerance for the strategy.
        """
        pass

    @abstractmethod
    def calculate_variation_based_on_side(self, dex_pair_instance: 'DexPair', current_order_side: str, cex_price: float,
                                          original_price: float) -> float:
        """
        Strategy-specific logic to calculate price variation based on order side.
        """
        pass

    @abstractmethod
    def init_virtual_order_logic(self, dex_pair_instance: 'DexPair', order_history: dict):
        pass

    @abstractmethod
    def handle_order_status_error(self, dex_pair_instance: 'DexPair'):
        pass

    @abstractmethod
    def reinit_virtual_order_after_price_variation(self, dex_pair_instance: 'DexPair', disabled_coins: list):
        pass

    @abstractmethod
    def handle_finished_order(self, dex_pair_instance: 'DexPair', disabled_coins: list):
        pass

    @abstractmethod
    async def handle_error_swap_status(self, dex_pair_instance: 'DexPair'):
        pass

    def get_startup_tasks(self) -> list:
        """
        For maker strategies, it's often useful to clear out any old,
        stale orders before starting fresh.
        """
        return [
            self.config_manager.xbridge_manager.cancelallorders(),
            self.config_manager.xbridge_manager.dxflushcancelledorders()
        ]
