# gui/frames/strategy_frames.py
import abc
import logging
from typing import TYPE_CHECKING

from definitions.config_manager import ConfigManager
from gui.frames.base_frames import StandardStrategyFrame

from gui.config_windows.pingpong_config import GUI_Config_PingPong
from gui.config_windows.basicseller_config import GUI_Config_BasicSeller

if TYPE_CHECKING:
    from gui.main_app import MainApplication
    from gui.config_windows.base_config_window import BaseConfigWindow

logger = logging.getLogger(__name__)


class PingPongFrame(StandardStrategyFrame):
    """
    Strategy frame for the PingPong bot.
    """
    def __init__(self, parent, main_app: "MainApplication", master_config_manager: ConfigManager):
        super().__init__(parent, main_app, "pingpong", master_config_manager)

    def _create_config_gui(self) -> "BaseConfigWindow":
        """
        Creates and returns the PingPong specific configuration GUI window.
        """
        return GUI_Config_PingPong(self)


class BasicSellerFrame(StandardStrategyFrame):
    """
    Strategy frame for the Basic Seller bot.
    """
    def __init__(self, parent, main_app: "MainApplication", master_config_manager: ConfigManager):
        super().__init__(parent, main_app, "basic_seller", master_config_manager)

    def _create_config_gui(self) -> "BaseConfigWindow":
        """
        Creates and returns the Basic Seller specific configuration GUI window.
        """
        return GUI_Config_BasicSeller(self)


class ArbitrageFrame(StandardStrategyFrame):
    """
    Strategy frame for the Arbitrage bot.
    Note: This currently uses a mock configuration window as the full
    implementation is pending.
    """
    def __init__(self, parent, main_app: "MainApplication", master_config_manager: ConfigManager):
        super().__init__(parent, main_app, "arbitrage", master_config_manager)

    def _create_config_gui(self) -> "BaseConfigWindow":
        """
        Creates and returns the Arbitrage specific configuration GUI window.
        Currently returns a mock implementation.
        """
        # Temporary implementation - return mock config window
        class MockConfigWindow:
            def open(self):
                logger.info("Mock Arbitrage Config Window opened.")
                pass

        return MockConfigWindow()

    def _fetch_orders_data(self) -> list:
        """
        Overrides the base method for arbitrage as it doesn't have DEX orders
        to display in the same manner as PingPong or Basic Seller.
        """
        return []