"""Tests for SecretsManager environment-variable credential loading."""

import logging
import os

import pytest

from definitions.secrets_manager import SecretsManager

PREFIX = "CCXT_"


def _isolate_env(monkeypatch):
    """Remove any real CCXT_EXCHANGE_* variables so tests are deterministic."""
    for key in list(os.environ):
        if key.startswith(f"{PREFIX}EXCHANGE_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def manager():
    return SecretsManager(logger=logging.getLogger("test_secrets_manager"))


@pytest.mark.usefixtures("manager")
class TestLoadApiKeysFromEnv:
    def test_documented_format_loads_exchange(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv("CCXT_EXCHANGE_BINANCE_API_KEY", "abc123")
        monkeypatch.setenv("CCXT_EXCHANGE_BINANCE_API_SECRET", "xyz789")

        result = SecretsManager().get_api_keys()

        assert result["api_info"] == [
            {
                "exchange": "binance",
                "api_key": "abc123",
                "api_secret": "xyz789",
            }
        ]

    def test_exchange_with_underscore_in_name(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv("CCXT_EXCHANGE_BINANCE_US_API_KEY", "k1")
        monkeypatch.setenv("CCXT_EXCHANGE_BINANCE_US_API_SECRET", "s1")

        result = SecretsManager().get_api_keys()

        assert result["api_info"][0]["exchange"] == "binance_us"

    def test_malformed_short_key_is_skipped_without_crash(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv("CCXT_EXCHANGE_BINANCE_KEY", "orphan")

        result = SecretsManager().get_api_keys()

        assert result == {"api_info": []}

    def test_missing_secret_skips_exchange(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv("CCXT_EXCHANGE_KRAKEN_API_KEY", "only-key")

        result = SecretsManager().get_api_keys()

        assert result == {"api_info": []}

    def test_unrelated_env_vars_are_ignored(self, monkeypatch):
        _isolate_env(monkeypatch)
        monkeypatch.setenv("CCXT_MY_SETTING", "value")
        monkeypatch.setenv("OTHER_EXCHANGE_BINANCE_API_KEY", "nope")

        result = SecretsManager().get_api_keys()

        assert result == {"api_info": []}

    def test_has_api_keys_reflects_state(self, monkeypatch):
        _isolate_env(monkeypatch)
        manager = SecretsManager()
        assert not manager.has_api_keys()

        monkeypatch.setenv("CCXT_EXCHANGE_BINANCE_API_KEY", "abc123")
        monkeypatch.setenv("CCXT_EXCHANGE_BINANCE_API_SECRET", "xyz789")
        manager = SecretsManager()
        assert manager.has_api_keys()

    def test_get_env_variable_name_helper(self):
        name = SecretsManager.get_env_variable_name("binance", "api_key")
        assert name == "CCXT_EXCHANGE_BINANCE_API_KEY"
