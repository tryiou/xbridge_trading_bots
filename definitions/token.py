import time

import aiohttp
import yaml

from definitions.errors import OperationalError
from definitions.rpc import rpc_call


class Token:
    """Represents a cryptocurrency token with DEX and CEX trading capabilities.

    Handles both decentralized exchange (DEX) and centralized exchange (CEX)
    operations and pricing for a specific token.

    Attributes:
        symbol: Ticker symbol of the token (e.g., 'BTC')
        strategy: Trading strategy associated with this token
        config_manager: Master configuration manager
        dex: DexToken instance for DEX operations
        cex: CexToken instance for CEX operations
    """

    def __init__(self, symbol: str, strategy, dex_enabled: bool = True, config_manager=None):
        self.symbol = symbol
        self.strategy = strategy
        self.config_manager = config_manager
        self.dex = DexToken(self, dex_enabled)
        self.cex = CexToken(self)

    @property
    def dex_total_balance(self) -> float | None:
        """Total token balance in DEX wallet including locked funds.
        
        Returns:
            Total DEX balance if available, else None
        """
        return getattr(self.dex, 'total_balance', None) if self.dex else None

    @property
    def dex_free_balance(self) -> float | None:
        """Available token balance in DEX wallet (excluding locked funds).
        
        Returns:
            Available DEX balance if available, else None
        """
        return getattr(self.dex, 'free_balance', None) if self.dex else None

    @property
    def cex_usd_price(self) -> float | None:
        """Current USD price of token on the CEX.
        
        Returns:
            USD price if available, else None
        """
        return getattr(self.cex, 'usd_price', None) if self.cex else None


class DexToken:
    """Represents token-specific DEX information and operations.

    Handles wallet address management and DEX balance tracking.

    Attributes:
        token: Parent Token object
        enabled: Flag indicating DEX operations are enabled
        address: Wallet address for this token
        total_balance: Total token balance in DEX wallet
        free_balance: Available token balance in DEX wallet
    """

    def __init__(self, parent_token: Token, dex_enabled: bool = True):
        self.token = parent_token
        self.enabled = dex_enabled
        self.address = None
        self.total_balance = None
        self.free_balance = None
        # self.read_address() must be called asynchronously after object creation.

    def _get_address_file_path(self) -> str:
        """Get path to token's DEX address file from strategy config.
        
        Returns:
            File path string
        """
        return self.token.config_manager.strategy_instance.get_dex_token_address_file_path(self.token.symbol)

    async def read_address(self) -> None:
        """Read DEX wallet address from file or request new address if missing.
        
        Handles file not found and parsing errors by requesting new address.
        """
        if not self.enabled:
            return

        file_path = self._get_address_file_path()
        try:
            with open(file_path, 'r') as fp:
                data = yaml.safe_load(fp)
                self.address = data.get('address') if isinstance(data, dict) else None
        except FileNotFoundError:
            self.token.config_manager.general_log.info(f"File not found: {file_path}")
            await self.request_addr()
        except (yaml.YAMLError, Exception) as e:
            await self.token.config_manager.error_handler.handle_async(
                OperationalError(f"Error reading token address file: {str(e)}"),
                context={"token": self.token.symbol, "stage": "read_address", "file_path": file_path}
            )
            await self.request_addr()

    async def write_address(self) -> None:
        """Write current DEX wallet address to file.
        
        Creates/overwrites file with YAML-formatted address.
        """
        if not self.enabled:
            return

        file_path = self._get_address_file_path()
        try:
            with open(file_path, 'w') as fp:
                yaml.safe_dump({'address': self.address}, fp)
        except (yaml.YAMLError, Exception) as e:
            await self.token.config_manager.error_handler.handle_async(
                OperationalError(f"Error writing token address file: {str(e)}"),
                context={"token": self.token.symbol, "stage": "write_address", "file_path": file_path}
            )

    async def request_addr(self) -> None:
        """Request new DEX wallet address from XBridge manager."""
        try:
            address = (await self.token.config_manager.xbridge_manager.getnewtokenadress(self.token.symbol))[0]
            self.address = address
            self.token.config_manager.general_log.info(f"dx_request_addr: {self.token.symbol}, {address}")
            await self.write_address()
        except Exception as e:
            await self.token.config_manager.error_handler.handle_async(
                OperationalError(f"Error requesting token address: {str(e)}"),
                context={"token": self.token.symbol, "stage": "request_addr"}
            )


class CexToken:
    """Represents token-specific CEX information and operations.

    Handles centralized exchange price updates and balance tracking.

    Attributes:
        token: Parent Token object
        cex_price: Token price in base currency (BTC)
        usd_price: Token price in USD
        cex_price_timer: Timestamp of last CEX price update
        cex_total_balance: Total token balance on CEX
        cex_free_balance: Available token balance on CEX
    """

    def __init__(self, parent_token: Token):
        self.token = parent_token
        self.cex_price = None
        self.usd_price = None
        self.cex_price_timer = None
        self.cex_total_balance = None
        self.cex_free_balance = None

    async def update_price(self, display: bool = False) -> None:
        """Fetch and update token prices from CEX with rate limiting.
        
        Args:
            display: Flag to enable debug logging
        """
        if (self.cex_price_timer is not None and
                time.time() - self.cex_price_timer <= 2):
            if display:
                self.token.config_manager.general_log.debug(
                    f"Token.update_ccxt_price() too fast call? {self.token.symbol}")
            return

        cex_symbol = "BTC/USDT" if self.token.symbol == "BTC" else f"{self.token.symbol}/BTC"
        lastprice_string = {
            'kucoin': 'last',
            'binance': 'lastPrice'
        }.get(self.token.config_manager.my_ccxt.id, 'lastTradeRate')

        async def fetch_ticker_async(cex_symbol: str) -> float | None:
            """Fetch ticker from CEX. Retry logic is handled by ccxt_manager.
            
            Returns:
                Price float on success, None on failure
            """
            try:
                ticker = await self.token.config_manager.ccxt_manager.ccxt_call_fetch_ticker(
                    self.token.config_manager.my_ccxt, cex_symbol)
            except Exception as e:
                await self.token.config_manager.error_handler.handle_async(
                    OperationalError(f"Error fetching ticker: {str(e)}"),
                    context={"token": self.token.symbol, "cex_symbol": cex_symbol, "stage": "fetch_ticker"}
                )
                return None

            if not ticker:
                return None

            try:
                return float(ticker['info'][lastprice_string])
            except (KeyError, TypeError, ValueError) as e:
                await self.token.config_manager.error_handler.handle_async(
                    OperationalError(f"Malformed ticker response: {str(e)}"),
                    context={"token": self.token.symbol, "cex_symbol": cex_symbol, "ticker_response": ticker}
                )
                return None

        btc_price = self.token.config_manager.tokens['BTC'].cex.usd_price
        if btc_price is None or btc_price == 0:
            await self.token.config_manager.error_handler.handle_async(
                OperationalError(f"BTC price unavailable for {self.token.symbol} price calculation"),
                context={"token": self.token.symbol}
            )
            self.usd_price = None
            self.cex_price = None
            return

        # Special case: BTC token doesn't require API calls
        if self.token.symbol == "BTC":
            self.cex_price = 1.0
            self.usd_price = btc_price
            self.cex_price_timer = time.time()
            return

        result = None
        if hasattr(self.token.config_manager.config_coins, 'usd_ticker_custom'):
            custom_tickers = self.token.config_manager.config_coins.usd_ticker_custom
            if hasattr(custom_tickers, self.token.symbol):
                custom_price = getattr(custom_tickers, self.token.symbol)
                try:
                    custom_price_float = float(custom_price)
                    result = custom_price_float / btc_price
                except (TypeError, ValueError):
                    result = None
                    await self.token.config_manager.error_handler.handle_async(
                        OperationalError(f"Invalid custom price value for {self.token.symbol}: {custom_price}"),
                        context={"token": self.token.symbol, "stage": "update_price", "custom_price": custom_price}
                    )
        
        if result is None:
            if hasattr(self.token.config_manager.my_ccxt, 'symbols') and cex_symbol in self.token.config_manager.my_ccxt.symbols:
                result = await fetch_ticker_async(cex_symbol)
            else:
                self.usd_price = None
                self.cex_price = None
                return

        if result is not None:
            self.cex_price = 1 if self.token.symbol == "BTC" else result
            self.usd_price = result if self.token.symbol == "BTC" else (
                    result * self.token.config_manager.tokens['BTC'].cex.usd_price)
            self.cex_price_timer = time.time()
            self.token.config_manager.general_log.debug(
                f"fetch_ticker {self.token.symbol}, BTC_PRICE: {format(float(self.cex_price), '.8f').rstrip('0').rstrip('.')}, "
                f"USD_PRICE: {format(float(self.usd_price), '.8f').rstrip('0').rstrip('.')}, "
                f"BTC_USD_PRICE: {format(float(self.token.config_manager.tokens['BTC'].cex.usd_price), '.8f').rstrip('0').rstrip('.')}"
            )

        else:
            self.usd_price = None
            self.cex_price = None

    async def update_block_ticker(self) -> float | None:
        """Fetch BLOCK token price from proxy or fallback.
        
        Returns:
            Price in BTC if successful, None on failure
        """
        result = None
        used_proxy = False
        async with aiohttp.ClientSession() as session:
            try:
                # First try proxy if available
                if self.token.config_manager.ccxt_manager.isportopen_sync("127.0.0.1", 2233):
                    result = await rpc_call("fetch_ticker_block", rpc_port=2233, debug=2, session=session)
                    used_proxy = True
                else:
                    # Fall back to cryptocompare API
                    async with session.get(
                            'https://min-api.cryptocompare.com/data/price?fsym=BLOCK&tsyms=BTC'
                    ) as response:
                        response.raise_for_status()
                        data = await response.json()
                        result = data.get('BTC')
            except Exception as e:
                await self.token.config_manager.error_handler.handle_async(
                    OperationalError(f"Error updating BLOCK ticker: {str(e)}"),
                    context={"token": "BLOCK", "stage": "update_block_ticker"}
                )
            else:
                if result is not None:
                    try:
                        result = float(result)
                    except (TypeError, ValueError):
                        await self.token.config_manager.error_handler.handle_async(
                            OperationalError(
                                f"Invalid BLOCK ticker price from {'proxy' if used_proxy else 'cryptocompare'}: {result}"),
                            context={"token": "BLOCK", "stage": "update_block_ticker"}
                        )
                        return None
                    else:
                        self.token.config_manager.general_log.info(
                            f"Updated BLOCK ticker: {result} BTC proxy: {used_proxy}"
                        )
                        return result
        return None
