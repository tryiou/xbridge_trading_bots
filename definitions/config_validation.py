import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ValidationResult:
    """Result of a validation operation."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, message: str):
        self.errors.append(message)
        self.is_valid = False

    def add_warning(self, message: str):
        self.warnings.append(message)

    def __str__(self) -> str:
        result = "VALID" if self.is_valid else "INVALID"
        if self.errors:
            result += f"\nErrors ({len(self.errors)}):\n" + "\n".join(
                f"  - {err}" for err in self.errors
            )
        if self.warnings:
            result += f"\nWarnings ({len(self.warnings)}):\n" + "\n".join(
                f"  - {warn}" for warn in self.warnings
            )
        return result


class ConfigValidator:
    """Base class for configuration validators."""

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"config_validator.{name}")

    def validate(self, config: dict[str, Any], file_path: str) -> ValidationResult:
        raise NotImplementedError("Subclasses must implement validate method")

    def _validate_required_fields(
        self, data: dict, required_fields: list, prefix: str = ""
    ) -> ValidationResult:
        """DRY helper for validating required keys exist in a dictionary."""
        result = ValidationResult(True)
        prefix_str = f"{prefix}: " if prefix else ""
        for field_name in required_fields:
            if field_name not in data:
                result.add_error(f"{prefix_str}Missing required field: {field_name}")
        return result


class CCXTConfigValidator(ConfigValidator):
    def __init__(self):
        super().__init__("ccxt")

    def validate(self, config: dict[str, Any], file_path: str) -> ValidationResult:
        result = self._validate_required_fields(
            config, ["ccxt_exchange", "debug_level"]
        )

        if "ccxt_exchange" in config:
            exchange = config["ccxt_exchange"]
            if not isinstance(exchange, str) or not exchange.strip():
                result.add_error("ccxt_exchange must be a non-empty string")

        if "ccxt_hostname" in config:
            hostname = config["ccxt_hostname"]
            if hostname is not None and not isinstance(hostname, str):
                result.add_error("ccxt_hostname must be a string or None")

        if "debug_level" in config:
            debug_level = config["debug_level"]
            if not isinstance(debug_level, int) or not (0 <= debug_level <= 10):
                result.add_error("debug_level must be an integer between 0 and 10")

        if "use_proxy" in config:
            use_proxy = config["use_proxy"]
            if not isinstance(use_proxy, bool):
                result.add_error("use_proxy must be a boolean")

        return result


class CoinsConfigValidator(ConfigValidator):
    def __init__(self):
        super().__init__("coins")

    def validate(self, config: dict[str, Any], file_path: str) -> ValidationResult:
        result = ValidationResult(True)

        if "usd_ticker_custom" in config:
            ticker_custom = config["usd_ticker_custom"]
            if not isinstance(ticker_custom, dict):
                result.add_error("usd_ticker_custom must be a dictionary")
            else:
                for coin, price in ticker_custom.items():
                    if not isinstance(coin, str) or not coin.strip():
                        result.add_error(
                            f"Invalid coin symbol in usd_ticker_custom: {coin}"
                        )
                    elif not isinstance(price, (int, float)) or price <= 0:
                        result.add_error(
                            f"Invalid price for {coin}: must be a positive number, got {price}"
                        )

        return result


class PingPongConfigValidator(ConfigValidator):
    def __init__(self):
        super().__init__("pingpong")

    def validate(self, config: dict[str, Any], file_path: str) -> ValidationResult:
        result = ValidationResult(True)

        if "debug_level" in config:
            debug_level = config["debug_level"]
            if not isinstance(debug_level, int) or not (0 <= debug_level <= 10):
                result.add_error("debug_level must be an integer between 0 and 10")

        if "ttk_theme" in config:
            theme = config["ttk_theme"]
            if not isinstance(theme, str) or not theme.strip():
                result.add_error("ttk_theme must be a non-empty string")

        if "pair_configs" not in config:
            result.add_error("Missing required field: pair_configs")
            return result

        pair_configs = config["pair_configs"]
        if not isinstance(pair_configs, list):
            result.add_error("pair_configs must be a list")
            return result

        if not pair_configs:
            result.add_warning(
                "pair_configs is empty - strategy will not place any orders"
            )
            return result

        for i, pair_config in enumerate(pair_configs):
            pair_result = self._validate_pair_config(pair_config, i)
            result.errors.extend(pair_result.errors)
            result.warnings.extend(pair_result.warnings)

        result.is_valid = len(result.errors) == 0
        return result

    def _validate_pair_config(
        self, pair_config: dict[str, Any], index: int
    ) -> ValidationResult:
        prefix = f"pair_configs[{index}]"
        required_fields = [
            "name",
            "enabled",
            "pair",
            "price_variation_tolerance",
            "sell_price_offset",
            "usd_amount",
            "spread",
        ]

        result = self._validate_required_fields(pair_config, required_fields, prefix)

        if "name" in pair_config:
            name = pair_config["name"]
            if not isinstance(name, str) or not name.strip():
                result.add_error(f"{prefix}: name must be a non-empty string")

        if "enabled" in pair_config:
            enabled = pair_config["enabled"]
            if not isinstance(enabled, bool):
                result.add_error(f"{prefix}: enabled must be a boolean")

        if "pair" in pair_config:
            pair = pair_config["pair"]
            if not isinstance(pair, str) or "/" not in pair or not pair.strip():
                result.add_error(
                    f"{prefix}: pair must be in format 'BASE/QUOTE' (e.g., 'LTC/BLOCK')"
                )

        numeric_fields = {
            "price_variation_tolerance": (0.0, 1.0),
            "sell_price_offset": (0.0, 10.0),
            "usd_amount": (0.0, float("inf")),
            "spread": (0.0, float("inf")),
        }

        for field_name, (min_val, max_val) in numeric_fields.items():
            if field_name in pair_config:
                value = pair_config[field_name]
                if not isinstance(value, (int, float)):
                    result.add_error(f"{prefix}: {field_name} must be a number")
                elif not (min_val <= value <= max_val):
                    result.add_error(
                        f"{prefix}: {field} must be between {min_val} and {max_val}"
                    )

        if "spread" in pair_config:
            spread = pair_config["spread"]
            if isinstance(spread, (int, float)) and spread == 0:
                result.add_warning(f"{prefix}: spread is 0 - this might cause issues")

        return result


class BasicSellerConfigValidator(ConfigValidator):
    def __init__(self):
        super().__init__("basic_seller")

    def validate(self, config: dict[str, Any], file_path: str) -> ValidationResult:
        result = ValidationResult(True)

        if "seller_configs" not in config:
            result.add_error("Missing required field: seller_configs")
            return result

        seller_configs = config["seller_configs"]
        if not isinstance(seller_configs, list):
            result.add_error("seller_configs must be a list")
            return result

        if not seller_configs:
            result.add_warning(
                "seller_configs is empty - strategy will not place any sell orders"
            )
            return result

        for i, seller_config in enumerate(seller_configs):
            seller_result = self._validate_seller_config(seller_config, i)
            result.errors.extend(seller_result.errors)
            result.warnings.extend(seller_result.warnings)

        result.is_valid = len(result.errors) == 0
        return result

    def _validate_seller_config(
        self, seller_config: dict[str, Any], index: int
    ) -> ValidationResult:
        prefix = f"seller_configs[{index}]"
        required_fields = [
            "name",
            "enabled",
            "pair",
            "amount_to_sell",
            "min_sell_price_usd",
            "sell_price_offset",
        ]

        result = self._validate_required_fields(seller_config, required_fields, prefix)

        if "name" in seller_config:
            name = seller_config["name"]
            if not isinstance(name, str) or not name.strip():
                result.add_error(f"{prefix}: name must be a non-empty string")

        if "enabled" in seller_config:
            enabled = seller_config["enabled"]
            if not isinstance(enabled, bool):
                result.add_error(f"{prefix}: enabled must be a boolean")

        if "pair" in seller_config:
            pair = seller_config["pair"]
            if not isinstance(pair, str) or "/" not in pair or not pair.strip():
                result.add_error(
                    f"{prefix}: pair must be in format 'BASE/QUOTE' (e.g., 'BLOCK/LTC')"
                )

        if "amount_to_sell" in seller_config:
            amount = seller_config["amount_to_sell"]
            if not isinstance(amount, (int, float)) or amount <= 0:
                result.add_error(f"{prefix}: amount_to_sell must be a positive number")

        if "min_sell_price_usd" in seller_config:
            price = seller_config["min_sell_price_usd"]
            if not isinstance(price, (int, float)) or price <= 0:
                result.add_error(
                    f"{prefix}: min_sell_price_usd must be a positive number"
                )

        if "sell_price_offset" in seller_config:
            offset = seller_config["sell_price_offset"]
            if not isinstance(offset, (int, float)) or offset < 0:
                result.add_error(
                    f"{prefix}: sell_price_offset must be a non-negative number"
                )

        if "partial_percent" in seller_config:
            percent = seller_config["partial_percent"]
            if not isinstance(percent, (int, float)) or not (0.0 < percent <= 1.0):
                result.add_error(
                    f"{prefix}: partial_percent must be between 0 and 1 (exclusive of 0)"
                )

        return result


class XBridgeConfigValidator(ConfigValidator):
    def __init__(self):
        super().__init__("xbridge")

    def validate(self, config: dict[str, Any], file_path: str) -> ValidationResult:
        result = ValidationResult(True)

        if "taker_fee_block" not in config:
            result.add_error("Missing required field: taker_fee_block")
        else:
            fee = config["taker_fee_block"]
            if not isinstance(fee, (int, float)) or fee < 0:
                result.add_error("taker_fee_block must be a non-negative number")

        if "debug_level" in config:
            debug_level = config["debug_level"]
            if not isinstance(debug_level, int) or not (0 <= debug_level <= 10):
                result.add_error("debug_level must be an integer between 0 and 10")

        if "max_concurrent_tasks" in config:
            tasks = config["max_concurrent_tasks"]
            if not isinstance(tasks, int) or not (1 <= tasks <= 100):
                result.add_error(
                    "max_concurrent_tasks must be an integer between 1 and 100"
                )

        if "monitoring" in config:
            monitoring = config["monitoring"]
            if not isinstance(monitoring, dict):
                result.add_error("monitoring must be a dictionary")
            else:
                if "timeout" in monitoring:
                    timeout = monitoring["timeout"]
                    if not isinstance(timeout, (int, float)) or timeout <= 0:
                        result.add_error("monitoring.timeout must be a positive number")

                if "poll_interval" in monitoring:
                    interval = monitoring["poll_interval"]
                    if not isinstance(interval, (int, float)) or interval <= 0:
                        result.add_error(
                            "monitoring.poll_interval must be a positive number"
                        )
                    elif interval > 300:
                        result.add_warning(
                            "poll_interval is very high (>300s) - this might affect responsiveness"
                        )

        return result


class APIKeysConfigValidator(ConfigValidator):
    def __init__(self):
        super().__init__("api_keys")

    def validate(self, config: dict[str, Any], file_path: str) -> ValidationResult:
        result = ValidationResult(True)

        if "api_info" not in config:
            result.add_error("Missing required field: api_info")
            return result

        api_info = config["api_info"]
        if not isinstance(api_info, list):
            result.add_error("api_info must be a list")
            return result

        if not api_info:
            result.add_warning(
                "api_info is empty - exchange trading will not be available"
            )
            return result

        for i, api_entry in enumerate(api_info):
            prefix = f"api_info[{i}]"
            required_fields = ["exchange", "api_key", "api_secret"]

            entry_result = self._validate_required_fields(
                api_entry, required_fields, prefix
            )
            result.errors.extend(entry_result.errors)

            if "exchange" in api_entry:
                exchange = api_entry["exchange"]
                if not isinstance(exchange, str) or not exchange.strip():
                    result.add_error(f"{prefix}: exchange must be a non-empty string")

            if "api_key" in api_entry:
                api_key = api_entry["api_key"]
                if not isinstance(api_key, str) or not api_key.strip():
                    result.add_error(f"{prefix}: api_key must be a non-empty string")
                elif api_key in ["AAA", "BBB", "your_api_key_here"]:
                    result.add_warning(
                        f"{prefix}: api_key appears to be a placeholder - exchange access will not work"
                    )

            if "api_secret" in api_entry:
                api_secret = api_entry["api_secret"]
                if not isinstance(api_secret, str) or not api_secret.strip():
                    result.add_error(f"{prefix}: api_secret must be a non-empty string")
                elif api_secret in ["BBB", "your_api_secret_here"]:
                    result.add_warning(
                        f"{prefix}: api_secret appears to be a placeholder - exchange access will not work"
                    )

        result.is_valid = len(result.errors) == 0
        return result


class ConfigValidationManager:
    def __init__(self):
        self.logger = logging.getLogger("config_validation_manager")
        self.validators = {
            "ccxt": CCXTConfigValidator(),
            "coins": CoinsConfigValidator(),
            "pingpong": PingPongConfigValidator(),
            "basic_seller": BasicSellerConfigValidator(),
            "xbridge": XBridgeConfigValidator(),
            "api_keys": APIKeysConfigValidator(),
        }

    def validate_config_file(
        self, config_type: str, config_data: dict[str, Any], file_path: str
    ) -> ValidationResult:
        validator = self.validators.get(config_type)
        if not validator:
            return ValidationResult(
                False, [f"Unknown configuration type: {config_type}"]
            )
        return validator.validate(config_data, file_path)

    def validate_file_existence_and_readability(
        self, file_path: str
    ) -> ValidationResult:
        result = ValidationResult(True)
        path = Path(file_path)
        if not path.exists():
            result.add_error(f"Configuration file does not exist: {file_path}")
            return result
        if not path.is_file():
            result.add_error(f"Path is not a file: {file_path}")
            return result
        try:
            path.stat()
        except PermissionError:
            result.add_error(f"Configuration file is not readable: {file_path}")
        except Exception as e:
            result.add_error(f"Error accessing configuration file {file_path}: {e!s}")
        return result

    def validate_file_format(
        self, file_path: str, expected_format: str = "yaml"
    ) -> ValidationResult:
        result = ValidationResult(True)
        path = Path(file_path)
        extension = path.suffix.lower()
        if expected_format == "yaml" and extension not in [".yaml", ".yml"]:
            result.add_error(
                f"File does not have YAML extension (.yaml or .yml): {file_path}"
            )
        elif expected_format == "json" and extension != ".json":
            result.add_error(f"File does not have JSON extension (.json): {file_path}")

        try:
            with open(file_path) as f:
                content = f.read().strip()
                if not content:
                    result.add_error(f"Configuration file is empty: {file_path}")
                    return result
                if expected_format == "yaml":
                    import yaml

                    yaml.safe_load(content)
                elif expected_format == "json":
                    json.loads(content)
        except json.JSONDecodeError as e:
            result.add_error(f"Invalid JSON format in {file_path}: {e!s}")
        except ImportError:
            pass
        except Exception as e:
            result.add_error(f"Error parsing {file_path}: {e!s}")
        return result
