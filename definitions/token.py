import asyncio
import time

import aiohttp
import yaml


class Token:
    def __init__(self, symbol, strategy, dex_enabled=True, config_manager=None):
        self.symbol = symbol
        self.strategy = strategy
        self.config_manager = config_manager
        self.dex = DexToken(self, dex_enabled)
        self.cex = CexToken(self)

    @property
    def dex_total_balance(self):
        return getattr(self.dex, 'total_balance', None) if self.dex else None

    @property
    def dex_free_balance(self):
        return getattr(self.dex, 'free_balance', None) if self.dex else None

    @property
    def cex_usd_price(self):
        return getattr(self.cex, 'usd_price', None) if self.cex else None


class DexToken:
    def __init__(self, parent_token, dex_enabled=True):
        self.token = parent_token
        self.enabled = dex_enabled
        self.address = None
        self.total_balance = None
        self.free_balance = None
        # self.read_address() must be called asynchronously after object creation.

    def _get_address_file_path(self):
        return self.token.config_manager.strategy_instance.get_dex_token_address_file_path(self.token.symbol)

    async def read_address(self):
        if not self.enabled:
            return

        file_path = self._get_address_file_path()
        try:
            with open(file_path, 'r') as fp:
                self.address = yaml.safe_load(fp).get('address')
        except FileNotFoundError:
            self.token.config_manager.general_log.info(f"File not found: {file_path}")
            await self.request_addr()
        except (yaml.YAMLError, Exception) as e:
            self.token.config_manager.general_log.error(
                f"Error reading XB address from file: {file_path} - {type(e).__name__}: {e}")
            await self.request_addr()

    async def write_address(self):
        if not self.enabled:
            return

        file_path = self._get_address_file_path()
        try:
            with open(file_path, 'w') as fp:
                yaml.safe_dump({'address': self.address}, fp)
        except (yaml.YAMLError, Exception) as e:
            self.token.config_manager.general_log.error(
                f"Error writing XB address to file: {file_path} - {type(e).__name__}: {e}")

    async def request_addr(self):
        try:
            address = (await self.token.config_manager.xbridge_manager.getnewtokenadress(self.token.symbol))[0]
            self.address = address
            self.token.config_manager.general_log.info(f"dx_request_addr: {self.token.symbol}, {address}")
            await self.write_address()
        except Exception as e:
            self.token.config_manager.general_log.error(
                f"Error requesting XB address for {self.token.symbol}: {type(e).__name__}: {e}")


class CexToken:
    def __init__(self, parent_token):
        self.token = parent_token
        self.cex_price = None
        self.usd_price = None
        self.cex_price_timer = None
        self.cex_total_balance = None
        self.cex_free_balance = None

    async def update_price(self, display=False):
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

        async def fetch_ticker_async(cex_symbol):
            for _ in range(3):  # Attempt to fetch the ticker up to 3 times
                try:
                    ticker = await self.token.config_manager.ccxt_manager.ccxt_call_fetch_ticker(
                        self.token.config_manager.my_ccxt, cex_symbol)
                    result = float(ticker['info'][lastprice_string])
                    return result
                except Exception as e:
                    self.token.config_manager.general_log.error(
                        f"fetch_ticker: {cex_symbol} error: {type(e).__name__}: {e}")
                    await asyncio.sleep(1)  # Sleep for a second before retrying
            return None

        if hasattr(self.token.config_manager.config_coins.usd_ticker_custom, self.token.symbol):
            custom_price = getattr(self.token.config_manager.config_coins.usd_ticker_custom, self.token.symbol)
            result = custom_price / self.token.config_manager.tokens[
                'BTC'].cex.usd_price
        elif cex_symbol in self.token.config_manager.my_ccxt.symbols:
            result = await fetch_ticker_async(cex_symbol)
        else:
            self.token.config_manager.general_log.info(
                f"{cex_symbol} not in cex {str(self.token.config_manager.my_ccxt)}")
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

    async def update_block_ticker(self):
        count = 0
        done = False
        used_proxy = False
        result = None
        from definitions.rpc import rpc_call  # Local import to avoid circular dependency issues at module level
        async with aiohttp.ClientSession() as session:
            while not done:
                count += 1
                try:
                    if self.token.config_manager.ccxt_manager.isportopen("127.0.0.1", 2233):
                        result = await rpc_call("fetch_ticker_block", rpc_port=2233, debug=2, display=False,
                                                session=session)
                        used_proxy = True
                    else:
                        async with session.get(
                                'https://min-api.cryptocompare.com/data/price?fsym=BLOCK&tsyms=BTC') as response:
                            if response.status == 200:
                                result = (await response.json()).get('BTC')
                except Exception as e:
                    self.token.config_manager.general_log.error(
                        f"update_block_ticker: BLOCK error({count}): {type(e).__name__}: {e}")
                    await asyncio.sleep(count)
                else:
                    if isinstance(result, float):
                        self.token.config_manager.general_log.info(
                            f"Updated BLOCK ticker: {result} BTC proxy: {used_proxy}")
                        return result
                    await asyncio.sleep(count)
