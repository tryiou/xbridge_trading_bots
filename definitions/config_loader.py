import logging
import os
import shutil
from typing import Any

from ruamel.yaml import YAML

from definitions.config_validation import ConfigValidationManager, ValidationResult
from definitions.errors import ConfigurationError
from definitions.secrets_manager import SecretsManager
from definitions.yaml_mix import YamlToObject


class ConfigLoader:
    """Handles loading, merging, and validating configuration files.

    Responsible for:
    - Creating config files from templates
    - Loading YAML configs
    - Merging user configs with templates
    - Validating configs
    - Loading API keys

    Attributes:
        root_dir: Root directory containing config files
        logger: Logger instance for logging
        error_handler: Error handler for handling errors
        validation_manager: Manager for config validation
        validation_enabled: Whether validation is enabled
    """

    CONFIG_FILES = [
        "config_ccxt.yaml",
        "config_coins.yaml",
        "config_pingpong.yaml",
        "config_basic_seller.yaml",
        "config_xbridge.yaml",
    ]

    CONFIG_TYPE_MAP = {
        "config_ccxt.yaml": "ccxt",
        "config_coins.yaml": "coins",
        "config_pingpong.yaml": "pingpong",
        "config_basic_seller.yaml": "basic_seller",
        "config_xbridge.yaml": "xbridge",
    }

    CRITICAL_CONFIGS = {"ccxt", "xbridge", "api_keys"}

    def __init__(
            self,
            root_dir: str,
            logger: logging.Logger,
            error_handler: Any,
            validation_manager: ConfigValidationManager | None = None,
            validation_enabled: bool = True,
    ) -> None:
        self.root_dir = root_dir
        self.logger = logger
        self.error_handler = error_handler
        self.validation_manager = validation_manager or ConfigValidationManager()
        self.validation_enabled = validation_enabled
        self.validation_results: dict[str, ValidationResult] = {}
        self.secrets_manager = SecretsManager(logger)

    def create_configs_from_templates(self) -> None:
        """Create config files from templates if they don't exist."""
        for config_file in self.CONFIG_FILES:
            target_path = os.path.join(self.root_dir, "config", config_file)
            template_path = os.path.join(
                self.root_dir, "config", "templates", config_file + ".template"
            )

            if not os.path.exists(target_path):
                if os.path.exists(template_path):
                    try:
                        shutil.copy(template_path, target_path)
                        self.logger.info(
                            f"Created config file: {config_file} from template"
                        )
                    except Exception as e:
                        self.error_handler.handle(
                            e,
                            context={
                                "file": target_path,
                                "template": template_path,
                                "operation": f"create_config_{config_file}",
                            },
                        )
                else:
                    self.error_handler.handle(
                        ConfigurationError(
                            f"Template file {config_file}.template not found in config directory"
                        ),
                        context={"file": template_path},
                    )
            else:
                self.logger.info(f"{config_file}: Already exists")

    def load_all_configs(
            self, strategy: str
    ) -> tuple[dict[str, YamlToObject], dict[str, Any]]:
        """Load all configuration files for a given strategy.

        Args:
            strategy: The strategy name (e.g., 'pingpong', 'basic_seller', 'gui')

        Returns:
            Tuple of (configs dict, api_keys dict)
        """
        self.create_configs_from_templates()

        configs = {}

        configs["ccxt"] = self._load_and_update_config("config_ccxt.yaml")
        configs["coins"] = self._load_and_update_config("config_coins.yaml")
        configs["xbridge"] = self._load_and_update_config("config_xbridge.yaml")

        if strategy in ["pingpong", "gui"]:
            configs["pingpong"] = self._load_and_update_config("config_pingpong.yaml")
        if strategy in ["basic_seller", "gui"]:
            configs["basic_seller"] = self._load_and_update_config(
                "config_basic_seller.yaml"
            )
        if strategy in ["autonomous_maker", "gui"]:
            configs["autonomous_maker"] = self._load_and_update_config(
                "config_autonomous_maker.yaml"
            )

        api_keys = self._load_and_validate_api_keys()

        return configs, api_keys

    def _load_and_update_config(self, config_name: str) -> YamlToObject:
        """Load a YAML config, merge with template, validate, and return as YamlToObject."""
        config_path = os.path.join(self.root_dir, "config", config_name)
        template_path = os.path.join(
            self.root_dir, "config", "templates", config_name + ".template"
        )

        config_type = self.CONFIG_TYPE_MAP.get(config_name)

        if self.validation_enabled and config_type:
            validation_result = (
                self.validation_manager.validate_file_existence_and_readability(
                    config_path
                )
            )
            if not validation_result.is_valid:
                self._handle_validation_error(
                    config_type, config_path, validation_result
                )
                return YamlToObject({})

        if not os.path.exists(template_path):
            self.error_handler.handle(
                ConfigurationError(
                    f"Template file not found: {template_path}. Cannot check for missing keys."
                ),
                context={"template_path": template_path},
            )
            return YamlToObject(config_path)

        yaml = self._create_yaml_parser()

        user_config = self._load_yaml_file(config_path, yaml)
        if user_config is None:
            return YamlToObject({})

        template_config = self._load_yaml_file(template_path, yaml)
        if template_config is None:
            return YamlToObject(user_config)

        if self._merge_configs(template_config, user_config):
            self._save_updated_config(config_path, yaml, user_config, config_name)

        if self.validation_enabled and config_type:
            self._validate_config(config_type, user_config, config_path)

        return YamlToObject(user_config)

    def _create_yaml_parser(self) -> YAML:
        """Create a configured YAML parser."""
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.indent(mapping=2, sequence=4, offset=2)
        return yaml

    def _load_yaml_file(self, file_path: str, yaml: YAML) -> dict[str, Any] | None:
        """Load a YAML file and return its contents as a dict."""
        try:
            with open(file_path) as f:
                return yaml.load(f) or {}
        except Exception as e:
            self.error_handler.handle(
                e, context={"file_path": file_path, "operation": "load_yaml"}
            )
            return None

    def _save_updated_config(
            self,
            config_path: str,
            yaml: YAML,
            user_config: dict[str, Any],
            config_name: str,
    ) -> None:
        """Save the updated config back to the file."""
        try:
            with open(config_path, "w") as f:
                yaml.dump(user_config, f)
            self.logger.info(
                f"Updated {os.path.basename(config_path)} with missing keys from template."
            )
        except Exception as e:
            self.error_handler.handle(
                e,
                context={
                    "config_path": config_path,
                    "operation": "save_updated_config",
                },
            )

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
                self.logger.info(f"Added missing key '{key}' to config from template.")
            elif isinstance(value, dict) and isinstance(user.get(key), dict):
                if self._merge_configs(value, user.get(key, {})):
                    updated = True
        return updated

    def _validate_config(
            self, config_type: str, user_config: dict[str, Any], config_path: str
    ) -> None:
        """Validate a configuration file."""
        validation_result = self.validation_manager.validate_config_file(
            config_type, user_config, config_path
        )
        self.validation_results[config_type] = validation_result
        self._log_validation_result(config_type, validation_result)

    def _handle_validation_error(
            self, config_type: str, file_path: str, validation_result: ValidationResult
    ) -> None:
        """Handle validation errors based on severity."""
        if config_type in self.CRITICAL_CONFIGS:
            self.logger.error(
                f"Critical configuration validation failed for {config_type}: {validation_result}"
            )
            self.error_handler.handle(
                ConfigurationError(
                    f"Critical configuration validation failed for {config_type}"
                ),
                context={
                    "config_type": config_type,
                    "file_path": file_path,
                    "errors": validation_result.errors,
                    "validation_result": str(validation_result),
                },
            )
        else:
            self.logger.warning(
                f"Configuration validation failed for {config_type}: {validation_result}"
            )

    def _log_validation_result(
            self, config_type: str, validation_result: ValidationResult
    ) -> None:
        """Log validation result at appropriate level."""
        if not validation_result.is_valid:
            if config_type in self.CRITICAL_CONFIGS:
                self.logger.error(
                    f"Validation failed for {config_type}: {validation_result}"
                )
            else:
                self.logger.warning(
                    f"Configuration warnings for {config_type}: {validation_result.warnings}"
                )
        elif validation_result.warnings:
            self.logger.info(
                f"Configuration warnings for {config_type}: {validation_result.warnings}"
            )
        elif self.validation_enabled:
            self.logger.debug(f"Configuration validation passed for {config_type}")

    def _load_and_validate_api_keys(self) -> dict[str, Any]:
        """Load API keys from secrets manager and validate them."""
        api_keys_data = self.secrets_manager.get_api_keys()

        if not api_keys_data.get("api_info"):
            self.logger.warning(
                "No API keys found in environment variables. "
                "Set XBRIDGE_EXCHANGE_<EXCHANGE>_API_KEY and "
                "XBRIDGE_EXCHANGE_<EXCHANGE>_API_SECRET for each exchange."
            )

        if self.validation_enabled and api_keys_data.get("api_info"):
            validation_result = self.validation_manager.validate_config_file(
                "api_keys", api_keys_data, "environment"
            )
            self.validation_results["api_keys"] = validation_result
            self._log_validation_result("api_keys", validation_result)

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
            report.append(f"  Status: {'VALID' if result.is_valid else 'INVALID'}")
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
                if config_type in self.CRITICAL_CONFIGS:
                    self.logger.error(
                        f"Critical configuration {config_type} validation failed"
                    )
                    return False

        return all_valid

    def is_config_valid(self, config_type: str) -> bool:
        """Check if a configuration type is valid."""
        if not self.validation_enabled:
            return True

        validation_result = self.validation_results.get(config_type)
        return validation_result.is_valid if validation_result else True

    def get_validation_summary(self) -> dict[str, dict[str, Any]]:
        """Get a summary of all validation results."""
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
