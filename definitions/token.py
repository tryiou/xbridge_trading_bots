from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from definitions.constants import DEFAULT_PROXY_PORT, DEFAULT_RPC_TIMEOUT
from definitions.rpc import is_port_open, rpc_call
from definitions.yaml_utils import load_yaml, save_yaml

if TYPE_CHECKING:
    from definitions.config_manager import ConfigManager


class Token:
    def __init__(
        self,
        symbol: str,
        strategy: Any,
        dex_enabled: bool = True,
        config_manager: ConfigManager | None = None,
        xbridge_manager: Any | None = None,
        ccxt_manager: Any | None = None,
        error_handler: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.symbol = symbol
        self.strategy = strategy
        self._config_manager = config_manager

        # Dependency Injection with fallback for backward compatibility
        self.xbridge_manager = xbridge_manager or getattr(
            config_manager, "xbridge_manager", None
        )
        self.ccxt_manager = ccxt_manager or getattr(
            config_manager, "ccxt_manager", None
        )
        self.error_handler = error_handler or getattr(
            config_manager, "error_handler", None
        )
        self.logger = logger or getattr(
            config_manager, "general_log", logging.getLogger(__name__)
        )

        self.dex = DexToken(self, dex_enabled)
        self.cex = CexToken(self)

    @property
    def config_manager(self):
        """Deprecated property for backward compatibility."""
        return self._config_manager

    @property
    def dex_total_balance(self) -> float | None:
        return getattr(self.dex, "total_balance", None) if self.dex else None

    @property
    def dex_free_balance(self) -> float | None:
        return getattr(self.dex, "free_balance", None) if self.dex else None

    @property
    def cex_usd_price(self) -> float | None:
        return getattr(self.cex, "usd_price", None) if self.cex else None


class DexToken:
    def __init__(self, parent_token: Token, dex_enabled: bool = True) -> None:
        self.token = parent_token
        self.enabled = dex_enabled
        self.address: str | None = None
        self.total_balance: float | None = None
        self.free_balance: float | None = None

    def _get_address_file_path(self) -> str:
        if self.token.config_manager is None:
            raise ValueError("Config manager not available")
        return (
            self.token.config_manager.strategy_instance.get_dex_token_address_file_path(
                self.token.symbol
            )
        )

    async def read_address(self) -> None:
        if not self.enabled:
            return

        file_path = self._get_address_file_path()
        try:
            data = load_yaml(file_path)
            self.address = data.get("address") if isinstance(data, dict) else None
        except FileNotFoundError:
            self.token.logger.info("File not found: %s", file_path)
            await self.request_addr()
        except Exception as e:
            await self.token.error_handler.handle_async(
                e,
                context={
                    "token": self.token.symbol,
                    "stage": "read_address",
                    "file_path": file_path,
                },
            )
            await self.request_addr()

    async def write_address(self) -> None:
        if not self.enabled:
            return

        file_path = self._get_address_file_path()
        try:
            save_yaml(file_path, {"address": self.address})
        except Exception as e:
            await self.token.error_handler.handle_async(
                e,
                context={
                    "token": self.token.symbol,
                    "stage": "write_address",
                    "file_path": file_path,
                },
            )

    async def request_addr(self) -> None:
        try:
            address = (
                await self.token.xbridge_manager.getnewtokenadress(self.token.symbol)
            )[0]
            self.address = address
            self.token.logger.info(
                "dx_request_addr: %s, %s", self.token.symbol, address
            )
            await self.write_address()
        except Exception as e:
            await self.token.error_handler.handle_async(
                e,
                context={"token": self.token.symbol, "stage": "request_addr"},
            )


class CexToken:
    def __init__(self, parent_token: Token) -> None:
        self.token = parent_token
        self.cex_price: float | None = None
        self.usd_price: float | None = None
        self.cex_price_timer: float | None = None
        self.cex_total_balance: float | None = None
        self.cex_free_balance: float | None = None

    def _get_rpc_timeout(self) -> int:
        try:
            cfg = getattr(self.token.config_manager, "config_xbridge", None)
            cfg_timeout = getattr(cfg, "rpc_timeout", None) if cfg else None
            if isinstance(cfg_timeout, (int, float)) and cfg_timeout > 0:
                return int(cfg_timeout)
        except Exception:
            pass
        return DEFAULT_RPC_TIMEOUT

    async def update_block_ticker(self) -> float | None:
        result = None
        used_proxy = False
        async with aiohttp.ClientSession() as session:
            try:
                if is_port_open("127.0.0.1", DEFAULT_PROXY_PORT):
                    result = await rpc_call(
                        "fetch_ticker_block",
                        rpc_port=DEFAULT_PROXY_PORT,
                        debug=2,
                        timeout=self._get_rpc_timeout(),
                        session=session,
                    )
                    used_proxy = True
                else:
                    async with session.get(
                        "https://min-api.cryptocompare.com/data/price?fsym=BLOCK&tsyms=BTC"
                    ) as response:
                        response.raise_for_status()
                        data = await response.json()
                        result = data.get("BTC")
            except Exception as e:
                await self.token.error_handler.handle_async(
                    e,
                    context={"token": "BLOCK", "stage": "update_block_ticker"},
                )
            else:
                if result is not None:
                    try:
                        result = float(result)
                    except (TypeError, ValueError) as e:
                        await self.token.error_handler.handle_async(
                            e,
                            context={"token": "BLOCK", "stage": "update_block_ticker"},
                        )
                        return None
                    else:
                        self.token.logger.info(
                            "Updated BLOCK ticker: %s BTC proxy: %s", result, used_proxy
                        )
                        return result
        return None
