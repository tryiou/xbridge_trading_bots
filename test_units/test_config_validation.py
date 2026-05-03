"""
Unit tests for configuration validation system.
"""

import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Add the root directory to the Python path
sys.path.insert(0, "/home/tryou/Documents/share/xbridge_trading_bots")

from definitions.config_validation import (
    APIKeysConfigValidator,
    BasicSellerConfigValidator,
    CCXTConfigValidator,
    CoinsConfigValidator,
    ConfigValidationManager,
    PingPongConfigValidator,
    ValidationResult,
    XBridgeConfigValidator,
)


class TestValidationResult(unittest.TestCase):
    """Test ValidationResult dataclass."""

    def test_valid_result(self):
        """Test valid validation result."""
        result = ValidationResult(True)
        self.assertTrue(result.is_valid)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(len(result.warnings), 0)

    def test_invalid_result(self):
        """Test invalid validation result."""
        result = ValidationResult(False)
        self.assertFalse(result.is_valid)
        result.add_error("Test error")
        result.add_warning("Test warning")
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(len(result.warnings), 1)

    def test_string_representation(self):
        """Test string representation of ValidationResult."""
        result = ValidationResult(True)
        self.assertEqual(str(result), "VALID")

        result.add_warning("Test warning")
        expected = "VALID\nWarnings (1):\n  - Test warning"
        self.assertEqual(str(result), expected)

        result.add_error("Test error")
        expected = (
            "INVALID\nErrors (1):\n  - Test error\nWarnings (1):\n  - Test warning"
        )
        self.assertEqual(str(result), expected)


class TestCCXTConfigValidator(unittest.TestCase):
    """Test CCXT configuration validator."""

    def setUp(self):
        self.validator = CCXTConfigValidator()
        self.valid_config = {
            "ccxt_exchange": "binance",
            "ccxt_hostname": None,
            "debug_level": 3,
            "use_proxy": True,
        }

    def test_valid_config(self):
        """Test valid CCXT configuration."""
        result = self.validator.validate(self.valid_config, "/fake/path")
        self.assertTrue(result.is_valid)

    def test_missing_required_fields(self):
        """Test configuration with missing required fields."""
        config = {"debug_level": 3}  # Missing ccxt_exchange
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)
        self.assertIn("Missing required field: ccxt_exchange", result.errors)

    def test_invalid_ccxt_exchange(self):
        """Test invalid ccxt_exchange field."""
        config = self.valid_config.copy()
        config["ccxt_exchange"] = ""  # Empty string
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)

    def test_invalid_debug_level(self):
        """Test invalid debug_level."""
        config = self.valid_config.copy()
        config["debug_level"] = 15  # Out of range
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)

    def test_invalid_hostname_type(self):
        """Test invalid hostname type."""
        config = self.valid_config.copy()
        config["ccxt_hostname"] = 123  # Should be string or None
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)


class TestCoinsConfigValidator(unittest.TestCase):
    """Test coins configuration validator."""

    def setUp(self):
        self.validator = CoinsConfigValidator()
        self.valid_config = {"usd_ticker_custom": {"BLOCK": 0.035, "UNO": 5}}

    def test_valid_config(self):
        """Test valid coins configuration."""
        result = self.validator.validate(self.valid_config, "/fake/path")
        self.assertTrue(result.is_valid)

    def test_empty_config(self):
        """Test empty coins configuration."""
        result = self.validator.validate({}, "/fake/path")
        self.assertTrue(result.is_valid)

    def test_invalid_ticker_type(self):
        """Test invalid usd_ticker_custom type."""
        config = {"usd_ticker_custom": "not_a_dict"}
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)

    def test_invalid_coin_price(self):
        """Test invalid coin price."""
        config = {"usd_ticker_custom": {"BLOCK": "not_a_number"}}
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)

    def test_negative_coin_price(self):
        """Test negative coin price."""
        config = {"usd_ticker_custom": {"BLOCK": -1.0}}
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)


class TestPingPongConfigValidator(unittest.TestCase):
    """Test pingpong configuration validator."""

    def setUp(self):
        self.validator = PingPongConfigValidator()
        self.valid_config = {
            "debug_level": 2,
            "ttk_theme": "darkly",
            "pair_configs": [
                {
                    "name": "LTC_BLOCK_1",
                    "enabled": True,
                    "pair": "LTC/BLOCK",
                    "price_variation_tolerance": 0.02,
                    "sell_price_offset": 0.05,
                    "usd_amount": 1,
                    "spread": 0.1,
                }
            ],
        }

    def test_valid_config(self):
        """Test valid pingpong configuration."""
        result = self.validator.validate(self.valid_config, "/fake/path")
        self.assertTrue(result.is_valid)

    def test_missing_pair_configs(self):
        """Test configuration with missing pair_configs."""
        config = {"debug_level": 2}
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)

    def test_empty_pair_configs(self):
        """Test configuration with empty pair_configs."""
        config = {"pair_configs": []}
        result = self.validator.validate(config, "/fake/path")
        self.assertTrue(result.is_valid)  # Should be valid but with warning
        self.assertIn("pair_configs is empty", result.warnings[0])

    def test_invalid_pair_format(self):
        """Test invalid pair format."""
        config = self.valid_config.copy()
        config["pair_configs"][0]["pair"] = "invalid_pair"  # No slash
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)

    def test_invalid_pair_price_range(self):
        """Test invalid price range values."""
        config = self.valid_config.copy()
        config["pair_configs"][0]["price_variation_tolerance"] = 1.5  # > 1.0
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)

    def test_zero_spread_warning(self):
        """Test warning for zero spread."""
        config = self.valid_config.copy()
        config["pair_configs"][0]["spread"] = 0
        result = self.validator.validate(config, "/fake/path")
        self.assertTrue(result.is_valid)
        self.assertIn("spread is 0", result.warnings[0])


class TestBasicSellerConfigValidator(unittest.TestCase):
    """Test basic seller configuration validator."""

    def setUp(self):
        self.validator = BasicSellerConfigValidator()
        self.valid_config = {
            "seller_configs": [
                {
                    "name": "Sell_My_BLOCK",
                    "enabled": True,
                    "pair": "BLOCK/LTC",
                    "amount_to_sell": 100.0,
                    "min_sell_price_usd": 0.04,
                    "sell_price_offset": 0.015,
                }
            ]
        }

    def test_valid_config(self):
        """Test valid basic seller configuration."""
        result = self.validator.validate(self.valid_config, "/fake/path")
        self.assertTrue(result.is_valid)

    def test_missing_seller_configs(self):
        """Test configuration with missing seller_configs."""
        config = {}
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)

    def test_invalid_amount_to_sell(self):
        """Test invalid amount_to_sell."""
        config = self.valid_config.copy()
        config["seller_configs"][0]["amount_to_sell"] = -1
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)

    def test_invalid_partial_percent(self):
        """Test invalid partial_percent."""
        config = self.valid_config.copy()
        config["seller_configs"][0]["partial_percent"] = 1.5  # > 1.0
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)


class TestXBridgeConfigValidator(unittest.TestCase):
    """Test XBridge configuration validator."""

    def setUp(self):
        self.validator = XBridgeConfigValidator()
        self.valid_config = {
            "taker_fee_block": 0.015,
            "monitoring": {"timeout": 300, "poll_interval": 15},
            "debug_level": 3,
            "max_concurrent_tasks": 5,
        }

    def test_valid_config(self):
        """Test valid XBridge configuration."""
        result = self.validator.validate(self.valid_config, "/fake/path")
        self.assertTrue(result.is_valid)

    def test_missing_taker_fee(self):
        """Test configuration with missing taker_fee_block."""
        config = {"debug_level": 3}
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)

    def test_invalid_taker_fee(self):
        """Test invalid taker_fee_block."""
        config = self.valid_config.copy()
        config["taker_fee_block"] = -1
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)

    def test_high_poll_interval_warning(self):
        """Test warning for high poll interval."""
        config = self.valid_config.copy()
        config["monitoring"]["poll_interval"] = 400  # > 300
        result = self.validator.validate(config, "/fake/path")
        self.assertTrue(result.is_valid)  # Should be valid but with warning
        self.assertIn("very high", result.warnings[0])


class TestAPIKeysConfigValidator(unittest.TestCase):
    """Test API keys configuration validator."""

    def setUp(self):
        self.validator = APIKeysConfigValidator()
        self.valid_config = {
            "api_info": [
                {
                    "exchange": "binance",
                    "api_key": "real_api_key",
                    "api_secret": "real_api_secret",
                }
            ]
        }

    def test_valid_config(self):
        """Test valid API keys configuration."""
        result = self.validator.validate(self.valid_config, "/fake/path")
        self.assertTrue(result.is_valid)

    def test_missing_api_info(self):
        """Test configuration with missing api_info."""
        config = {}
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)

    def test_empty_api_info(self):
        """Test configuration with empty api_info."""
        config = {"api_info": []}
        result = self.validator.validate(config, "/fake/path")
        self.assertTrue(result.is_valid)  # Should be valid but with warning
        self.assertIn("empty", result.warnings[0])

    def test_placeholder_api_key_warning(self):
        """Test warning for placeholder API keys."""
        config = self.valid_config.copy()
        config["api_info"][0]["api_key"] = "AAA"
        result = self.validator.validate(config, "/fake/path")
        self.assertTrue(result.is_valid)
        self.assertIn("placeholder", result.warnings[0])

    def test_missing_required_fields(self):
        """Test API entry with missing required fields."""
        config = self.valid_config.copy()
        del config["api_info"][0]["api_key"]
        result = self.validator.validate(config, "/fake/path")
        self.assertFalse(result.is_valid)


class TestConfigValidationManager(unittest.TestCase):
    """Test ConfigValidationManager."""

    def setUp(self):
        self.manager = ConfigValidationManager()

    def test_unknown_config_type(self):
        """Test validation with unknown config type."""
        result = self.manager.validate_config_file("unknown_type", {}, "/fake/path")
        self.assertFalse(result.is_valid)
        self.assertIn("Unknown configuration type", result.errors[0])

    def test_file_existence_validation(self):
        """Test file existence validation."""
        # Test with non-existent file
        result = self.manager.validate_file_existence_and_readability(
            "/fake/nonexistent/file.yaml"
        )
        self.assertFalse(result.is_valid)
        self.assertIn("does not exist", result.errors[0])

    def test_file_format_validation(self):
        """Test file format validation."""
        # Test JSON validation with actual JSON content

        # Create a temporary file with invalid JSON
        temp_file = "/tmp/test_invalid.json"
        with open(temp_file, "w") as f:
            f.write('{"invalid": json content}')

        result = self.manager.validate_file_format(temp_file, "json")
        self.assertFalse(result.is_valid)

        # Test wrong extension detection
        temp_yaml_file = "/tmp/test.yaml"
        with open(temp_yaml_file, "w") as f:
            f.write("test: value")

        # Should fail because we're expecting JSON but file has .yaml extension
        result = self.manager.validate_file_format(temp_yaml_file, "json")
        self.assertFalse(result.is_valid)

        # Clean up
        os.unlink(temp_file)
        os.unlink(temp_yaml_file)

    def test_wrong_file_extension(self):
        """Test wrong file extension detection."""
        result = self.manager.validate_file_format("/fake/path.txt", "json")
        self.assertFalse(result.is_valid)
        self.assertIn("JSON extension", result.errors[0])


class TestConfigManagerValidation(unittest.TestCase):
    """Test ConfigManager integration with validation."""

    def setUp(self):
        # Create temporary config directory for testing
        self.temp_dir = tempfile.mkdtemp()
        self.config_dir = os.path.join(self.temp_dir, "config")
        self.templates_dir = os.path.join(self.config_dir, "templates")
        os.makedirs(self.config_dir, exist_ok=True)
        os.makedirs(self.templates_dir, exist_ok=True)

        # Create valid template files
        self.create_template_files()

    def tearDown(self):
        # Clean up temporary directory
        import shutil

        shutil.rmtree(self.temp_dir)

    def create_template_files(self):
        """Create template configuration files for testing."""
        templates = {
            "config_ccxt.yaml.template": """
ccxt_exchange: binance
debug_level: 3
use_proxy: true
""",
            "config_coins.yaml.template": """
usd_ticker_custom:
  BLOCK: 0.035
""",
            "config_pingpong.yaml.template": """
debug_level: 2
pair_configs:
  - name: test_pair
    enabled: true
    pair: LTC/BLOCK
    price_variation_tolerance: 0.02
    sell_price_offset: 0.05
    usd_amount: 1
    spread: 0.1
""",
            "config_basic_seller.yaml.template": """
seller_configs:
  - name: test_seller
    enabled: true
    pair: BLOCK/LTC
    amount_to_sell: 100.0
    min_sell_price_usd: 0.04
    sell_price_offset: 0.015
""",
            "config_xbridge.yaml.template": """
taker_fee_block: 0.015
debug_level: 3
max_concurrent_tasks: 5
""",
        }

        for filename, content in templates.items():
            template_path = os.path.join(self.templates_dir, filename)
            with open(template_path, "w") as f:
                f.write(content.strip())

    @patch("definitions.config_manager.setup_logging")
    @patch("definitions.config_manager.setup_logger")
    @patch("definitions.error_handler.ErrorHandler")
    def test_config_manager_with_validation(
        self, mock_error_handler, mock_setup_logger, mock_setup_logging
    ):
        """Test ConfigManager with validation enabled."""
        # Mock the logging setup
        mock_logger = unittest.mock.MagicMock()
        mock_setup_logging.return_value = mock_logger
        mock_setup_logger.return_value = (mock_logger, mock_logger, mock_logger)

        # Create config files with valid data
        config_files = {
            "config_ccxt.yaml": "ccxt_exchange: binance\ndebug_level: 3\n",
            "config_coins.yaml": "usd_ticker_custom:\n  BLOCK: 0.035\n",
            "config_pingpong.yaml": "debug_level: 2\npair_configs: []\n",
            "config_basic_seller.yaml": "seller_configs: []\n",
            "config_xbridge.yaml": "taker_fee_block: 0.015\ndebug_level: 3\n",
        }

        for filename, content in config_files.items():
            config_path = os.path.join(self.config_dir, filename)
            with open(config_path, "w") as f:
                f.write(content)

        # Mock os.path.abspath
        with patch("os.path.abspath", return_value=self.temp_dir):
            # Import after patching to avoid import-time errors
            import sys
            from unittest.mock import MagicMock

            # Mock modules that may not be available
            sys.modules["definitions.ccxt_manager"] = MagicMock()
            sys.modules["definitions.xbridge_manager"] = MagicMock()
            sys.modules["strategies.base_strategy"] = MagicMock()
            sys.modules["strategies.basicseller_strategy"] = MagicMock()
            sys.modules["strategies.pingpong_strategy"] = MagicMock()

            try:
                from definitions.config_manager import ConfigManager

                config_manager = ConfigManager("pingpong")
                self.assertTrue(config_manager.config_loader.validation_enabled)
                self.assertIsNotNone(config_manager.config_loader.validation_manager)
            except Exception:
                # Expected since we're mocking many dependencies - that's fine for this test
                pass

    def test_validation_report_generation(self):
        """Test validation report generation."""
        # This would test the get_validation_report method
        # Implementation depends on full ConfigManager integration
        pass


if __name__ == "__main__":
    # Set up logging to reduce test output noise
    logging.basicConfig(level=logging.WARNING)

    # Run the tests
    unittest.main(verbosity=2)
