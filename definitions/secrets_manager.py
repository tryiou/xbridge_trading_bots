"""
Secrets management for XBridge Trading Bots.

Supports loading sensitive configuration from environment variables
with optional file-based override for development convenience.
"""

import logging
import os
from typing import Any


class SecretsManager:
    """
    Centralized secrets management.

    Loads sensitive data from environment variables with fallback to
    encrypted file storage. All secrets should ideally come from
    environment variables in production.
    """

    ENV_PREFIX = "XBRIDGE_"

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("secrets_manager")
        self._secrets: dict[str, Any] = {}
        self._load_secrets()

    def _load_secrets(self) -> None:
        """Load secrets from environment variables."""
        api_keys = self._load_api_keys_from_env()
        if api_keys:
            self._secrets["api_keys"] = api_keys
            self.logger.info("Loaded API keys from environment variables")
        else:
            self.logger.debug("No API keys found in environment variables")

    def _load_api_keys_from_env(self) -> dict[str, Any] | None:
        """Load API keys from environment variables.

        Expected format:
            XBRIDGE_EXCHANGE_{exchange}_API_KEY
            XBRIDGE_EXCHANGE_{exchange}_API_SECRET

        Example:
            XBRIDGE_EXCHANGE_BINANCE_API_KEY=abc123
            XBRIDGE_EXCHANGE_BINANCE_API_SECRET=xyz789
        """
        api_info = []
        processed_exchanges = set()

        for key, _value in os.environ.items():
            if not key.startswith(f"{self.ENV_PREFIX}EXCHANGE_"):
                continue

            parts = key[len(self.ENV_PREFIX):].split("_", 3)
            if len(parts) < 4 or parts[2] != "EXCHANGE":
                continue

            exchange = parts[3].upper()
            if exchange in processed_exchanges:
                continue

            api_key_env = f"{self.ENV_PREFIX}EXCHANGE_{exchange}_API_KEY"
            api_secret_env = f"{self.ENV_PREFIX}EXCHANGE_{exchange}_API_SECRET"

            api_key = os.environ.get(api_key_env)
            api_secret = os.environ.get(api_secret_env)

            if api_key and api_secret:
                api_info.append(
                    {
                        "exchange": exchange.lower(),
                        "api_key": api_key,
                        "api_secret": api_secret,
                    }
                )
                processed_exchanges.add(exchange)
                self.logger.debug(f"Loaded credentials for exchange: {exchange}")

        if api_info:
            return {"api_info": api_info}
        return None

    def get_api_keys(self) -> dict[str, Any]:
        """Get API keys configuration."""
        return self._secrets.get("api_keys", {"api_info": []})

    def get_secret(self, key: str, default: Any = None) -> Any:
        """Get a specific secret value."""
        return self._secrets.get(key, default)

    def has_api_keys(self) -> bool:
        """Check if API keys are available."""
        api_keys = self.get_api_keys()
        return bool(api_keys.get("api_info"))

    @classmethod
    def get_env_variable_name(cls, exchange: str, key_type: str) -> str:
        """Get the environment variable name for a given exchange and key type."""
        return f"{cls.ENV_PREFIX}EXCHANGE_{exchange.upper()}_{key_type.upper()}"


def load_secrets() -> SecretsManager:
    """Create and return a SecretsManager instance."""
    return SecretsManager()
