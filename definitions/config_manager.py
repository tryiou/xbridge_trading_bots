import json
import logging
import os
import shutil
import threading
from typing import Dict, Any, Optional

from ruamel.yaml import YAML

from definitions.ccxt_manager import CCXTManager
from definitions.config_validation import ConfigValidationManager, ValidationResult
from definitions.error_handler import ErrorHandler
from definitions.errors import ConfigurationError
from definitions.logger import setup_logger, setup_logging
from definitions.xbridge_manager import XBridgeManager
from definitions.yaml_mix import YamlToObject
from strategies.base_strategy import BaseStrategy
from strategies.basicseller_strategy import BasicSellerStrategy
from strategies.pingpong_strategy import PingPongStrategy


class ConfigManager:
    def __init__(self, strategy: str, master_manager: Optional['ConfigManager'] = None):
        self.strategy = strategy
        self.ROOT_DIR = os.path.abspath(os.curdir)
        self.resource_lock = threading.RLock()
        self.logger = setup_logging(name="config_manager",
                                    level=logging.DEBUG, console=True)
        self.error_handler = ErrorHandler(self)
        self.current_module = None
        # Initialize validation manager
        self.validation_manager = ConfigValidationManager()
        self.validation_enabled = True  # Enable validation by default
        self.validation_results: Dict[str, ValidationResult] = {}

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
            # "config_arbitrage.yaml",
            # "config_thorchain.yaml",
            # "config_thorchain_continuous.yaml"
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
                            e,
                            context={"file": target_path, "template": template_path,
                                     "operation": f"create_config_{config_file}"}
                        )
                else:
                    self.error_handler.handle(
                        ConfigurationError(f"Template file {config_file}.template not found in config directory"),
                        context={"file": template_path}
                    )
            else:
                # Target file exists
                self.logger.info(f"{config_file}: Already exists")

    def _load_and_update_config(self, config_name: str) -> YamlToObject:
        """
        Loads a YAML config, validates it, compares it to its template, adds missing keys from
        the template, and saves it back if changed.
        Returns the config as a YamlToObject instance.
        """
        config_path = os.path.join(self.ROOT_DIR, "config", config_name)
        template_path = os.path.join(self.ROOT_DIR, "config", "templates", config_name + ".template")

        # Determine config type from filename
        config_type = self._get_config_type_from_filename(config_name)

        # Validate file existence and readability first
        if self.validation_enabled and config_type:
            validation_result = self.validation_manager.validate_file_existence_and_readability(config_path)
            if not validation_result.is_valid:
                self._handle_validation_error(config_type, config_path, validation_result)
                return YamlToObject({})

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
                e,
                context={"config_path": config_path, "operation": "load_config"}
            )
            return YamlToObject({})

        try:
            with open(template_path, 'r') as f:
                template_config = yaml.load(f) or {}
        except Exception as e:
            self.error_handler.handle(
                e,
                context={"template_path": template_path, "operation": "load_template"}
            )
            return YamlToObject(user_config)

        if self._merge_configs(template_config, user_config):
            try:
                with open(config_path, 'w') as f:
                    yaml.dump(user_config, f)
                self.logger.info(f"Updated {os.path.basename(config_path)} with missing keys from template.")
            except Exception as e:
                self.error_handler.handle(
                    e,
                    context={"config_path": config_path, "operation": "save_updated_config"}
                )

        # Validate configuration after loading and merging
        if self.validation_enabled and config_type:
            validation_result = self.validation_manager.validate_config_file(
                config_type, user_config, config_path
            )
            self.validation_results[config_type] = validation_result
            self._log_validation_result(config_type, validation_result)

            # Log warnings but don't fail - only log errors for critical configurations
            if not validation_result.is_valid and config_type in ['ccxt', 'xbridge']:
                # These are critical configurations that should not have errors
                self.logger.error(f"Critical configuration validation failed for {config_type}: {validation_result}")
            elif validation_result.warnings:
                self.logger.warning(f"Configuration warnings for {config_type}: {validation_result.warnings}")

        return YamlToObject(user_config)

    def _merge_configs(self, template: dict, user: dict) -> bool:
        """Recursively merge template config into user config, adding missing keys.
        
        Args:
            template: The template configuration dictionary.
            user: The user configuration dictionary to merge into.
            
        Returns:
            True if any keys were added, False otherwise.
        """
        updated = False
        if not isinstance(user, dict) or not isinstance(template, dict):
            return False
        for key, value in template.items():
            if key not in user:
                user[key] = value
                updated = True
                self.logger.info(
                    f"Added missing key '{key}' to config from template.")
            elif isinstance(value, dict) and isinstance(user.get(key), dict):
                if self._merge_configs(value, user.get(key, {})):
                    updated = True
        return updated

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

        # Load and validate API keys
        self._load_and_validate_api_keys()

    def _get_config_type_from_filename(self, filename: str) -> Optional[str]:
        """Extract configuration type from filename.
        
        Args:
            filename: The config filename to map to a type.
            
        Returns:
            The configuration type string or None if not recognized.
        """
        mapping = {
            "config_ccxt.yaml": "ccxt",
            "config_coins.yaml": "coins",
            "config_pingpong.yaml": "pingpong",
            "config_basic_seller.yaml": "basic_seller",
            "config_xbridge.yaml": "xbridge",
            "api_keys.local.json": "api_keys"
        }
        return mapping.get(filename)

    def _handle_validation_error(self, config_type: str, file_path: str, validation_result: ValidationResult):
        """Handle validation errors based on severity.
        
        Args:
            config_type: The type of configuration being validated.
            file_path: Path to the config file that failed validation.
            validation_result: The validation result containing errors.
        """
        if config_type in ['ccxt', 'xbridge', 'api_keys']:
            # Critical configurations - log as error
            self.logger.error(f"Critical configuration validation failed for {config_type}: {validation_result}")
            self.error_handler.handle(
                ConfigurationError(f"Critical configuration validation failed for {config_type}"),
                context={
                    "config_type": config_type,
                    "file_path": file_path,
                    "errors": validation_result.errors,
                    "validation_result": str(validation_result)
                }
            )
        else:
            # Non-critical configurations - log as warning
            self.logger.warning(f"Configuration validation failed for {config_type}: {validation_result}")

    def _log_validation_result(self, config_type: str, validation_result: ValidationResult):
        """Log validation result at appropriate level.
        
        Args:
            config_type: The type of configuration being validated.
            validation_result: The validation result to log.
        """
        if not validation_result.is_valid:
            if config_type in ['ccxt', 'xbridge', 'api_keys']:
                self.logger.error(f"Validation failed for {config_type}: {validation_result}")
            else:
                self.logger.warning(f"Validation warnings for {config_type}: {validation_result}")
        elif validation_result.warnings:
            self.logger.info(f"Configuration warnings for {config_type}: {validation_result.warnings}")
        elif self.validation_enabled:
            self.logger.debug(f"Configuration validation passed for {config_type}")

    def _load_and_validate_api_keys(self) -> Dict[str, Any]:
        """Load and validate API keys configuration.
        
        Returns:
            Dictionary containing the API keys data.
        """
        api_keys_path = os.path.join(self.ROOT_DIR, "config", "api_keys.local.json")

        # Validate file existence and readability
        if self.validation_enabled:
            validation_result = self.validation_manager.validate_file_existence_and_readability(api_keys_path)
            if not validation_result.is_valid:
                self._handle_validation_error('api_keys', api_keys_path, validation_result)
                return {}

            # Validate file format
            format_result = self.validation_manager.validate_file_format(api_keys_path, 'json')
            if not format_result.is_valid:
                self._handle_validation_error('api_keys', api_keys_path, format_result)
                return {}

        try:
            with open(api_keys_path, 'r') as f:
                api_keys_data = json.load(f)
        except json.JSONDecodeError as e:
            self.error_handler.handle(
                ConfigurationError(f"Invalid JSON in API keys file: {str(e)}"),
                context={"file_path": api_keys_path}
            )
            return {}
        except Exception as e:
            self.error_handler.handle(
                e,
                context={"file_path": api_keys_path, "operation": "load_api_keys"}
            )
            return {}

        # Validate API keys configuration
        if self.validation_enabled:
            validation_result = self.validation_manager.validate_config_file(
                'api_keys', api_keys_data, api_keys_path
            )
            self.validation_results['api_keys'] = validation_result
            self._log_validation_result('api_keys', validation_result)

            if not validation_result.is_valid:
                self.logger.error(f"API keys configuration validation failed: {validation_result}")

        return api_keys_data

    def get_validation_report(self) -> str:
        """Generate a comprehensive validation report."""
        if not self.validation_enabled:
            return "Configuration validation is disabled"

        report = ["=== Configuration Validation Report ==="]
        report.append(f"Total configurations validated: {len(self.validation_results)}")
        report.append("")

        for config_type, result in self.validation_results.items():
            report.append(f"{config_type.upper()} Configuration:")
            report.append(f"  Status: {'✓ VALID' if result.is_valid else '✗ INVALID'}")
            if result.errors:
                report.append(f"  Errors ({len(result.errors)}):")
                for error in result.errors:
                    report.append(f"    - {error}")
            if result.warnings:
                report.append(f"  Warnings ({len(result.warnings)}):")
                for warning in result.warnings:
                    report.append(f"    - {warning}")
            report.append("")

        return "\n".join(report)

    def validate_all_configs(self) -> bool:
        """Validate all loaded configurations and return overall status."""
        if not self.validation_enabled:
            return True

        all_valid = True
        for config_type, result in self.validation_results.items():
            if not result.is_valid:
                all_valid = False
                if config_type in ['ccxt', 'xbridge', 'api_keys']:
                    # For critical configs, raise the error
                    self.logger.error(f"Critical configuration {config_type} validation failed")
                    return False

        return all_valid

    def enable_validation(self, enabled: bool = True):
        """Enable or disable configuration validation."""
        self.validation_enabled = enabled
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
            if self.validation_enabled and config_attr.replace('config_', '') in self.validation_results:
                config_type = config_attr.replace('config_', '')
                validation_result = self.validation_results.get(config_type)
                if validation_result and not validation_result.is_valid:
                    # If critical config is invalid, return default
                    if config_type in ['ccxt', 'xbridge', 'api_keys']:
                        self.logger.error(f"Returning default for invalid critical config: {config_attr}")
                        return default
            return config
        except Exception as e:
            self.logger.warning(f"Error getting config {config_attr}, returning default: {e}")
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

    def get_validation_summary(self) -> Dict[str, Dict[str, Any]]:
        """
        Get a summary of all validation results.
        
        Returns:
            Dictionary with validation status for each config type
        """
        summary = {}
        for config_type in ['ccxt', 'coins', 'pingpong', 'basic_seller', 'xbridge', 'api_keys']:
            validation_result = self.validation_results.get(config_type)
            summary[config_type] = {
                'validated': validation_result is not None,
                'valid': validation_result.is_valid if validation_result else True,
                'error_count': len(validation_result.errors) if validation_result else 0,
                'warning_count': len(validation_result.warnings) if validation_result else 0
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

        required_configs = ['ccxt', 'xbridge', 'api_keys']

        # Add strategy-specific required configs
        if self.strategy in ["pingpong", "gui"]:
            required_configs.append('pingpong')
        if self.strategy in ["basic_seller", "gui"]:
            required_configs.append('basic_seller')

        for config_type in required_configs:
            validation_result = self.validation_results.get(config_type)
            if not validation_result or not validation_result.is_valid:
                self.logger.error(f"Required configuration {config_type} validation failed")
                return False

        return True

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
                e,
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
                e,
                context={"strategy": self.strategy, "stage": "strategy_initialize"}
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
