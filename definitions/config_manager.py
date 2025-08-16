import logging
import os
import shutil
import threading
from typing import Dict, Any

from ruamel.yaml import YAML

from definitions.ccxt_manager import CCXTManager
from definitions.error_handler import ErrorHandler, OperationalError
from definitions.errors import ConfigurationError
from definitions.logger import setup_logger, setup_logging
from definitions.xbridge_manager import XBridgeManager
from definitions.yaml_mix import YamlToObject
from strategies.arbitrage_strategy import ArbitrageStrategy
from strategies.base_strategy import BaseStrategy
from strategies.basicseller_strategy import BasicSellerStrategy
from strategies.pingpong_strategy import PingPongStrategy
from strategies.range_maker_strategy import RangeMakerStrategy


class ConfigManager:
    def __init__(self, strategy, master_manager=None):
        self.strategy = strategy
        self.ROOT_DIR = os.path.abspath(os.curdir)
        self.resource_lock = threading.RLock()
        self.logger = setup_logging(name="config_manager",
                                    level=logging.DEBUG, console=True)
        self.error_handler = ErrorHandler(self)
        self.current_module = None

        if master_manager:
            # In GUI slave mode, get references to strategy-specific loggers
            self.general_log = logging.getLogger(f"{strategy}.general")
            self.trade_log = logging.getLogger(f"{strategy}.trade")
            self.ccxt_log = logging.getLogger(f"{strategy}.ccxt")

            # Ensure trade logger has file handler for strategy-specific trade log
            if not self.trade_log.handlers:
                logs_dir = os.path.join(self.ROOT_DIR, 'logs')
                os.makedirs(logs_dir, exist_ok=True)
                trade_log_file = os.path.join(logs_dir, f"{strategy}_trade.log")
                trade_handler = logging.FileHandler(trade_log_file)
                trade_handler.setFormatter(
                    logging.Formatter('[%(asctime)s] [%(name)-20s] %(levelname)-8s - %(message)s'))
                trade_handler.setLevel(logging.INFO)
                self.trade_log.addHandler(trade_handler)
        else:
            # In standalone or master GUI mode, set up the loggers from scratch.
            self.general_log, self.trade_log, self.ccxt_log = setup_logger(strategy, self.ROOT_DIR)

        # Update error handler logger
        self.error_handler.logger = self.general_log

        # Initialize config attributes to None
        self.config_ccxt = None
        self.config_coins = None
        self.config_pingpong = None
        self.config_basicseller = None
        self.config_xbridge = None
        self.config_arbitrage = None
        self.config_thorchain = None

        # Mark role for resource management
        self.is_master = not master_manager
        role = "master" if self.is_master else "slave"
        self.logger.debug(f"ConfigManager initializing as {role} for '{strategy}' strategy")

        if master_manager:
            # GUI Slave Mode: Inherit configs, but create own managers to ensure
            # correct logger context.
            self.logger.info(f"Attaching to shared resources for strategy: {self.strategy}")
            self.config_ccxt = master_manager.config_ccxt
            self.config_coins = master_manager.config_coins
            self.config_pingpong = master_manager.config_pingpong
            self.config_basicseller = master_manager.config_basicseller
            self.config_xbridge = master_manager.config_xbridge
            self.config_arbitrage = master_manager.config_arbitrage
            self.config_thorchain = master_manager.config_thorchain

            # Create new manager instances. They will be initialized with this
            # slave ConfigManager instance, giving them the correct logger.

            self.xbridge_manager = XBridgeManager(self)

            self.ccxt_manager = CCXTManager(self)
            # Share the underlying CCXT connection object from the master to avoid
            # re-initializing it (e.g., re-loading markets).
            if master_manager.ccxt_manager:
                self.ccxt_manager.my_ccxt = getattr(master_manager.ccxt_manager, 'my_ccxt', None)
        else:
            # Standalone or Master GUI Mode: Create all resources from scratch
            try:
                self.load_configs()
            except Exception as e:
                self.error_handler.handle(
                    ConfigurationError(f"Failed to load configs: {str(e)}"),
                    context={"stage": "load_configs"}
                )
                raise
            self.xbridge_manager = XBridgeManager(self)
            self.ccxt_manager = CCXTManager(self)
            # If this is the master GUI manager, initialize shared components now.
            if self.strategy == "gui":
                self._init_ccxt()
        self.strategy_config: Dict[str, Any] = {}
        self.strategy_instance: BaseStrategy = None
        self.tokens = {}  # Token data
        self.pairs = {}  # Pair data
        self.load_xbridge_conf_on_startup = True  # Default value, will be updated by initialize
        self.disabled_coins = []  # Centralized disabled coins tracking
        self.controller = None
        self.logger.debug("ConfigManager setup complete")

    @property
    def my_ccxt(self):
        """Provides backward compatibility for accessing the ccxt instance."""
        if self.ccxt_manager:
            return getattr(self.ccxt_manager, 'my_ccxt', None)
        return None

    def create_configs_from_templates(self):
        # Common config files
        config_files = [
            "config_ccxt.yaml",
            "config_coins.yaml",
            "api_keys.local.json",
            "config_pingpong.yaml",
            "config_basic_seller.yaml",
            "config_xbridge.yaml",
            "config_arbitrage.yaml",
            "config_thorchain.yaml"
        ]

        for config_file in config_files:
            target_path = os.path.join(self.ROOT_DIR, "config", config_file)
            template_path = os.path.join(self.ROOT_DIR, "config", "templates", config_file + ".template")
            # Check if target file exists
            if not os.path.exists(target_path):
                # Check if template file exists
                if os.path.exists(template_path):
                    try:
                        shutil.copy(template_path, target_path)
                        self.logger.info(f"Created config file: {config_file} from template")
                    except Exception as e:
                        self.error_handler.handle(
                            OperationalError(f"Failed to create config file {config_file}: {str(e)}"),
                            context={"file": target_path, "template": template_path}
                        )
                else:
                    self.error_handler.handle(
                        ConfigurationError(f"Template file {config_file}.template not found in config directory"),
                        context={"file": template_path}
                    )
            else:
                # Target file exists
                self.logger.info(f"{config_file}: Already exists")

    def _load_and_update_config(self, config_name: str):
        """
        Loads a YAML config, compares it to its template, adds missing keys from
        the template, and saves it back if changed.
        Returns the config as a YamlToObject instance.
        """
        config_path = os.path.join(self.ROOT_DIR, "config", config_name)
        template_path = os.path.join(self.ROOT_DIR, "config", "templates", config_name + ".template")

        if not os.path.exists(template_path):
            self.error_handler.handle(
                ConfigurationError(f"Template file not found: {template_path}. Cannot check for missing keys."),
                context={"template_path": template_path}
            )
            return YamlToObject(config_path)

        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.indent(mapping=2, sequence=4, offset=2)

        try:
            with open(config_path, 'r') as f:
                user_config = yaml.load(f) or {}
        except Exception as e:
            self.error_handler.handle(
                OperationalError(f"Error loading config file {config_path}: {str(e)}"),
                context={"config_path": config_path}
            )
            return YamlToObject({})

        try:
            with open(template_path, 'r') as f:
                template_config = yaml.load(f) or {}
        except Exception as e:
            self.error_handler.handle(
                OperationalError(f"Error loading template file {template_path}: {str(e)}"),
                context={"template_path": template_path}
            )
            return YamlToObject(user_config)

        def merge_configs(template, user):
            updated = False
            if not isinstance(user, dict) or not isinstance(template, dict):
                return False
            for key, value in template.items():
                if key not in user:
                    user[key] = value
                    updated = True
                    self.logger.info(
                        f"Added missing key '{key}' to {os.path.basename(config_path)} from template.")
                elif isinstance(value, dict) and isinstance(user.get(key), dict):
                    if merge_configs(value, user.get(key, {})):
                        updated = True
            return updated

        if merge_configs(template_config, user_config):
            try:
                with open(config_path, 'w') as f:
                    yaml.dump(user_config, f)
                self.logger.info(f"Updated {os.path.basename(config_path)} with missing keys from template.")
            except Exception as e:
                self.error_handler.handle(
                    OperationalError(f"Failed to save updated config file {config_path}: {e}"),
                    context={"config_path": config_path}
                )

        return YamlToObject(user_config)

    def load_configs(self):
        self.create_configs_from_templates()
        self.config_ccxt = self._load_and_update_config("config_ccxt.yaml")
        self.config_coins = self._load_and_update_config("config_coins.yaml")
        self.config_xbridge = self._load_and_update_config("config_xbridge.yaml")
        # In standalone mode, only load the relevant strategy config.
        # In GUI mode (strategy='gui'), load all of them.
        if self.strategy in ["pingpong", "gui"]:
            self.config_pingpong = self._load_and_update_config("config_pingpong.yaml")
        if self.strategy in ["basic_seller", "gui"]:
            self.config_basicseller = self._load_and_update_config("config_basic_seller.yaml")
        if self.strategy in ["arbitrage", "gui"]:
            self.config_arbitrage = self._load_and_update_config("config_arbitrage.yaml")
            self.config_thorchain = self._load_and_update_config("config_thorchain.yaml")

    def _init_ccxt(self):
        """Initialize CCXT instance with error handling"""
        try:
            self.ccxt_manager.my_ccxt = self.ccxt_manager.init_ccxt_instance(
                exchange=self.config_ccxt.ccxt_exchange,
                hostname=self.config_ccxt.ccxt_hostname,
                private_api=False,
                debug_level=self.config_ccxt.debug_level
            )
        except Exception as e:
            self.error_handler.handle(
                OperationalError(f"CCXT initialization failed: {str(e)}"),
                context={
                    "exchange": self.config_ccxt.ccxt_exchange,
                    "hostname": self.config_ccxt.ccxt_hostname
                }
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
            loadxbridgeconf = kwargs.get('loadxbridgeconf', True)
            self.strategy_config.update(kwargs)

            self.tokens = {}  # Token data
            self.pairs = {}  # Pair data
            self.load_xbridge_conf_on_startup = loadxbridgeconf  # Store the flag

            strategy_map = {
                "pingpong": PingPongStrategy,
                "basic_seller": BasicSellerStrategy,
                "arbitrage": ArbitrageStrategy,
                "range_maker": RangeMakerStrategy,
                "gui": None,  # 'gui' strategy doesn't have a strategy instance
            }
            strategy_class = strategy_map.get(self.strategy)
            if not strategy_class:
                raise ConfigurationError(f"Unknown strategy: {self.strategy}")
            self.strategy_instance = strategy_class(self)
            self.strategy_instance.initialize_strategy_specifics(**kwargs)

            # Delegate token and pair initialization to the strategy instance
            self.strategy_instance.initialize_tokens_and_pairs(**kwargs)
            if self.ccxt_manager and getattr(self.ccxt_manager, 'my_ccxt', None) is None:
                self._init_ccxt()
            # dxloadxbridgeconf is now called asynchronously in MainController.main_init_loop
            # self._init_xbridge() # This method is now effectively a no-op if dxloadxbridgeconf is removed
        except Exception as e:
            self.error_handler.handle(
                OperationalError(f"Initialization failed: {str(e)}"),
                context={"strategy": self.strategy}
            )
            raise

    def initialize_ccxt(self):
        """
        Initializes only the CCXT component. This is used by the master config
        manager in the GUI to ensure that the CCXT instance is available to all
        strategies from the start.
        """
        if self.ccxt_manager and getattr(self.ccxt_manager, 'my_ccxt', None) is None:
            self._init_ccxt()
