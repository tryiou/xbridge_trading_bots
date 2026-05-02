import logging
import os
import threading
from typing import Any, Optional

from definitions.ccxt_manager import CCXTManager
from definitions.config_loader import ConfigLoader
from definitions.config_validation import ConfigValidationManager
from definitions.detect_rpc import detect_rpc
from definitions.error_handler import ErrorHandler
from definitions.errors import ConfigurationError
from definitions.logger import setup_logger, setup_logging
from definitions.xbridge_manager import XBridgeManager
from strategies.autonomous_maker_strategy import AutonomousMakerStrategy
from strategies.base_strategy import BaseStrategy
from strategies.basicseller_strategy import BasicSellerStrategy
from strategies.pingpong_strategy import PingPongStrategy


class ConfigManager:
    def __init__(self, strategy: str, master_manager: Optional["ConfigManager"] = None):
        self.strategy = strategy
        self.ROOT_DIR = os.path.abspath(os.curdir)
        self.resource_lock = threading.RLock()
        self.logger = setup_logging(
            name="config_manager", level=logging.DEBUG, console=True
        )
        self.error_handler = ErrorHandler(self)
        self.current_module = None
        # Initialize ConfigLoader for loading and validating configs
        self.config_loader = ConfigLoader(
            root_dir=self.ROOT_DIR,
            logger=self.logger,
            error_handler=self.error_handler,
            validation_manager=ConfigValidationManager(),
            validation_enabled=True,
        )

        if master_manager:
            # In GUI slave mode, get references to strategy-specific loggers
            self.general_log = logging.getLogger(f"{strategy}.general")
            self.trade_log = logging.getLogger(f"{strategy}.trade")
            self.ccxt_log = logging.getLogger(f"{strategy}.ccxt")

            # Ensure trade logger has file handler for strategy-specific trade log
            if not self.trade_log.handlers:
                logs_dir = os.path.join(self.ROOT_DIR, "logs")
                os.makedirs(logs_dir, exist_ok=True)
                trade_log_file = os.path.join(logs_dir, f"{strategy}_trade.log")
                trade_handler = logging.FileHandler(trade_log_file)
                trade_handler.setFormatter(
                    logging.Formatter(
                        "[%(asctime)s] [%(name)-20s] %(levelname)-8s - %(message)s"
                    )
                )
                trade_handler.setLevel(logging.INFO)
                self.trade_log.addHandler(trade_handler)
        else:
            # In standalone or master GUI mode, set up the loggers from scratch.
            self.general_log, self.trade_log, self.ccxt_log = setup_logger(
                strategy, self.ROOT_DIR
            )

        # Update error handler logger
        self.error_handler.logger = self.general_log

        # Initialize config attributes to None
        self.config_ccxt = None
        self.config_coins = None
        self.config_pingpong = None
        self.config_basicseller = None
        self.config_autonomous_maker = None
        self.config_xbridge = None

        # Mark role for resource management
        self.is_master = not master_manager
        role = "master" if self.is_master else "slave"
        self.logger.debug(
            f"ConfigManager initializing as {role} for '{strategy}' strategy"
        )

        if XBridgeManager._rpc_config is None:
            with XBridgeManager._rpc_config_lock:
                if XBridgeManager._rpc_config is None:
                    XBridgeManager._rpc_config = detect_rpc()

        if master_manager:
            # GUI Slave Mode: Inherit configs, but create own managers to ensure
            # correct logger context.
            self.logger.info(
                f"Attaching to shared resources for strategy: {self.strategy}"
            )
            self.config_ccxt = master_manager.config_ccxt
            self.config_coins = master_manager.config_coins
            self.config_pingpong = master_manager.config_pingpong
            self.config_basicseller = master_manager.config_basicseller
            self.config_xbridge = master_manager.config_xbridge

            # Create new manager instances. They will be initialized with this
            # slave ConfigManager instance, giving them the correct logger.

            self.xbridge_manager = XBridgeManager(self, rpc_config=XBridgeManager._rpc_config)

            self.ccxt_manager = CCXTManager(self)
            # Share the underlying CCXT connection object from the master to avoid
            # re-initializing it (e.g., re-loading markets).
            if master_manager.ccxt_manager:
                self.ccxt_manager.my_ccxt = getattr(
                    master_manager.ccxt_manager, "my_ccxt", None
                )
        else:
            # Standalone or Master GUI Mode: Create all resources from scratch
            try:
                self.load_configs()
            except Exception as e:
                self.error_handler.handle(
                    ConfigurationError(f"Failed to load configs: {e!s}"),
                    context={"stage": "load_configs"},
                )
                raise
            self.xbridge_manager = XBridgeManager(self, rpc_config=XBridgeManager._rpc_config)
            self.ccxt_manager = CCXTManager(self)
            # If this is the master GUI manager, initialize shared components now.
            if self.strategy == "gui":
                self._init_ccxt()
        self.strategy_config: dict[str, Any] = {}
        self.strategy_instance: BaseStrategy = None
        self.tokens = {}  # Token data
        self.pairs = {}  # Pair data
        self.load_xbridge_conf_on_startup = (
            True  # Default value, will be updated by initialize
        )
        self.disabled_coins = []  # Centralized disabled coins tracking
        self.controller = None
        self.logger.debug("ConfigManager setup complete")

    @property
    def my_ccxt(self):
        """Provides backward compatibility for accessing the ccxt instance."""
        if self.ccxt_manager:
            return getattr(self.ccxt_manager, "my_ccxt", None)
        return None

    @property
    def validation_manager(self):
        """Proxy to config_loader's validation_manager for backward compatibility."""
        return self.config_loader.validation_manager

    @property
    def validation_results(self):
        """Proxy to config_loader's validation_results for backward compatibility."""
        return self.config_loader.validation_results

    @property
    def validation_enabled(self):
        """Proxy to config_loader's validation_enabled for backward compatibility."""
        return self.config_loader.validation_enabled

    @validation_enabled.setter
    def validation_enabled(self, value: bool):
        """Set validation_enabled on config_loader."""
        self.config_loader.validation_enabled = value

    @property
    def secrets_manager(self):
        """Proxy to config_loader's secrets_manager for backward compatibility."""
        return self.config_loader.secrets_manager

    def load_configs(self):
        """Load all configuration files using ConfigLoader."""
        configs, api_keys = self.config_loader.load_all_configs(self.strategy)

        self.config_ccxt = configs.get("ccxt")
        self.config_coins = configs.get("coins")
        self.config_xbridge = configs.get("xbridge")
        self.config_pingpong = configs.get("pingpong")
        self.config_basicseller = configs.get("basic_seller")
        self.config_autonomous_maker = configs.get("autonomous_maker")

        return configs, api_keys

    def get_validation_report(self) -> str:
        """Generate a comprehensive validation report."""
        return self.config_loader.get_validation_report()

    def validate_all_configs(self) -> bool:
        """Validate all loaded configurations and return overall status."""
        return self.config_loader.validate_all_configs()

    def enable_validation(self, enabled: bool = True):
        """Enable or disable configuration validation."""
        self.config_loader.validation_enabled = enabled
        if enabled:
            self.logger.info("Configuration validation enabled")
        else:
            self.logger.warning("Configuration validation disabled")

    def get_config_safe(self, config_attr: str, default: Any = None) -> Any:
        """
        Safely get configuration with backward compatibility.

        Args:
            config_attr: The configuration attribute name (e.g., 'config_ccxt')
            default: Default value to return if config is invalid or missing

        Returns:
            Configuration value or default if validation failed
        """
        try:
            config = getattr(self, config_attr, None)
            if config is None:
                return default
            # If validation is enabled and this config was validated, check if it's valid
            if (
                self.validation_enabled
                and config_attr.replace("config_", "") in self.validation_results
            ):
                config_type = config_attr.replace("config_", "")
                validation_result = self.validation_results.get(config_type)
                if (
                    validation_result
                    and not validation_result.is_valid
                    and config_type in ["ccxt", "xbridge", "api_keys"]
                ):
                    self.logger.error(
                        f"Returning default for invalid critical config: {config_attr}"
                    )
                    return default
            return config
        except Exception as e:
            self.logger.warning(
                f"Error getting config {config_attr}, returning default: {e}"
            )
            return default

    def is_config_valid(self, config_type: str) -> bool:
        """
        Check if a configuration type is valid.

        Args:
            config_type: The configuration type (e.g., 'ccxt', 'pingpong')

        Returns:
            True if configuration is valid or not validated
        """
        if not self.validation_enabled:
            return True

        validation_result = self.validation_results.get(config_type)
        return validation_result.is_valid if validation_result else True

    def get_validation_summary(self) -> dict[str, dict[str, Any]]:
        """
        Get a summary of all validation results.

        Returns:
            Dictionary with validation status for each config type
        """
        summary = {}
        for config_type in [
            "ccxt",
            "coins",
            "pingpong",
            "basic_seller",
            "xbridge",
            "api_keys",
        ]:
            validation_result = self.validation_results.get(config_type)
            summary[config_type] = {
                "validated": validation_result is not None,
                "valid": validation_result.is_valid if validation_result else True,
                "error_count": len(validation_result.errors)
                if validation_result
                else 0,
                "warning_count": len(validation_result.warnings)
                if validation_result
                else 0,
            }
        return summary

    def validate_required_configs(self) -> bool:
        """
        Validate only the configurations required for the current strategy.

        Returns:
            True if all required configs are valid
        """
        if not self.validation_enabled:
            return True

        required_configs = ["ccxt", "xbridge", "api_keys"]

        # Add strategy-specific required configs
        if self.strategy in ["pingpong", "gui"]:
            required_configs.append("pingpong")
        if self.strategy in ["basic_seller", "gui"]:
            required_configs.append("basic_seller")

        for config_type in required_configs:
            validation_result = self.validation_results.get(config_type)
            if not validation_result or not validation_result.is_valid:
                self.logger.error(
                    f"Required configuration {config_type} validation failed"
                )
                return False

        return True

    def _init_ccxt(self):
        """Initialize CCXT instance with error handling"""
        try:
            self.ccxt_manager.my_ccxt = self.ccxt_manager.init_ccxt_instance(
                exchange=self.config_ccxt.ccxt_exchange,
                hostname=self.config_ccxt.ccxt_hostname,
                private_api=False,
                debug_level=self.config_ccxt.debug_level,
            )
        except Exception as e:
            self.error_handler.handle(
                e,
                context={
                    "exchange": self.config_ccxt.ccxt_exchange,
                    "hostname": self.config_ccxt.ccxt_hostname,
                },
            )
            raise

    def _init_xbridge(self):
        """Initialize XBridge configuration"""
        self.xbridge_manager.dxloadxbridgeconf()

    def initialize(self, **kwargs):
        """
        Initializes the ConfigManager, preparing strategy-specific configurations,
        tokens, and pairs.  This should only be used for strategies that require
        their own isolated environment.  For the GUI, see `initialize_ccxt`.
        """
        try:
            loadxbridgeconf = kwargs.get("loadxbridgeconf", True)
            self.strategy_config.update(kwargs)

            self.tokens = {}  # Token data
            self.pairs = {}  # Pair data
            self.load_xbridge_conf_on_startup = loadxbridgeconf  # Store the flag

            strategy_map = {
                "pingpong": PingPongStrategy,
                "basic_seller": BasicSellerStrategy,
                "autonomous_maker": AutonomousMakerStrategy,
                "gui": None,
            }
            strategy_class = strategy_map.get(self.strategy)
            if not strategy_class:
                raise ConfigurationError(f"Unknown strategy: {self.strategy}")
            self.strategy_instance = strategy_class(self)
            self.strategy_instance.initialize_strategy_specifics(**kwargs)

            # Delegate token and pair initialization to the strategy instance
            self.strategy_instance.initialize_tokens_and_pairs(**kwargs)
            if (
                self.ccxt_manager
                and getattr(self.ccxt_manager, "my_ccxt", None) is None
            ):
                self._init_ccxt()
            # dxloadxbridgeconf is now called asynchronously in MainController.main_init_loop
            # self._init_xbridge() # This method is now effectively a no-op if dxloadxbridgeconf is removed
        except Exception as e:
            self.error_handler.handle(
                e, context={"strategy": self.strategy, "stage": "strategy_initialize"}
            )
            raise

    def initialize_ccxt(self):
        """
        Initializes only the CCXT component. This is used by the master config
        manager in the GUI to ensure that the CCXT instance is available to all
        strategies from the start.
        """
        if self.ccxt_manager and getattr(self.ccxt_manager, "my_ccxt", None) is None:
            self._init_ccxt()
