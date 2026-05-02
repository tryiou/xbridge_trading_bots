from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import aiohttp

from definitions.constants import CCXT_PRICE_REFRESH_INTERVAL, DEFAULT_PROXY_PORT
from definitions.errors import OperationalError
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

    async def update_price(self, display: bool = False) -> None:
        if (
            self.cex_price_timer is not None
            and time.time() - self.cex_price_timer <= CCXT_PRICE_REFRESH_INTERVAL
        ):
            if display:
                self.token.logger.debug(
                    "Token.update_ccxt_price() too fast call? %s", self.token.symbol
                )
            return

        cex_symbol = (
            "BTC/USDT" if self.token.symbol == "BTC" else f"{self.token.symbol}/BTC"
        )
        my_ccxt = (
            getattr(self.token.config_manager, "my_ccxt", None)
            if self.token.config_manager
            else None
        )
        exchange_id = getattr(my_ccxt, "id", "default") if my_ccxt else "default"
        lastprice_string = {"kucoin": "last", "binance": "lastPrice"}.get(
            exchange_id, "lastTradeRate"
        )

        async def fetch_ticker_async(symbol: str) -> float | None:
            try:
                ticker = await self.token.ccxt_manager.ccxt_call_fetch_ticker(
                    my_ccxt, symbol
                )
            except Exception as e:
                await self.token.error_handler.handle_async(
                    e,
                    context={
                        "token": self.token.symbol,
                        "cex_symbol": symbol,
                        "stage": "fetch_ticker",
                    },
                )
                return None

            if not ticker:
                return None
            try:
                return float(ticker["info"][lastprice_string])
            except (KeyError, TypeError, ValueError) as e:
                await self.token.error_handler.handle_async(
                    e,
                    context={
                        "token": self.token.symbol,
                        "cex_symbol": symbol,
                        "ticker_response": ticker,
                    },
                )
                return None

        btc_price = self.token.config_manager.tokens["BTC"].cex.usd_price
        if btc_price is None or btc_price == 0:
            if self.token.error_handler:
                await self.token.error_handler.handle_async(
                    OperationalError(
                        f"BTC price unavailable for {self.token.symbol} price calculation"
                    ),
                    context={"token": self.token.symbol},
                )
            self.usd_price, self.cex_price = None, None
            return

        if self.token.symbol == "BTC":
            self.cex_price, self.usd_price = 1.0, btc_price
            self.cex_price_timer = time.time()
            return

        result = None
        if hasattr(self.token.config_manager.config_coins, "usd_ticker_custom"):
            custom_tickers = self.token.config_manager.config_coins.usd_ticker_custom
            if hasattr(custom_tickers, self.token.symbol):
                custom_price = getattr(custom_tickers, self.token.symbol)
                try:
                    result = float(custom_price) / btc_price
                except (TypeError, ValueError) as e:
                    await self.token.error_handler.handle_async(
                        e,
                        context={
                            "token": self.token.symbol,
                            "stage": "update_price",
                            "custom_price": custom_price,
                        },
                    )

        if result is None:
            if hasattr(my_ccxt, "symbols") and cex_symbol in my_ccxt.symbols:
                result = await fetch_ticker_async(cex_symbol)
            else:
                self.usd_price, self.cex_price = None, None
                return

        if result is not None:
            self.cex_price = result
            self.usd_price = result * btc_price
            self.cex_price_timer = time.time()
            btc_price_fmt = format(float(btc_price), ".8f").rstrip("0").rstrip(".")
            self.token.logger.debug(
                "fetch_ticker %s, BTC_PRICE: %s, USD_PRICE: %s, BTC_USD_PRICE: %s",
                self.token.symbol,
                format(float(self.cex_price), ".8f").rstrip("0").rstrip("."),
                format(float(self.usd_price), ".8f").rstrip("0").rstrip("."),
                btc_price_fmt,
            )
        else:
            self.usd_price, self.cex_price = None, None

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
