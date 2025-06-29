import asyncio
import logging
import uuid

import aiohttp

from definitions.rpc import rpc_call

# Using THORNode for quotes. Midgard is for historical data. This can be made configurable.
THORNODE_QUOTE_URL = "https://thornode.ninerealms.com/thorchain"
# A public THORNode endpoint. This can also be made configurable.
THORNODE_URL = "https://thornode.ninerealms.com"


@asyncio.coroutine
async def get_thorchain_quote(from_asset: str, to_asset: str, amount: float, session: aiohttp.ClientSession):
    """
    Fetches a swap quote from Thorchain's Midgard API.
    Amount is in base units (e.g., 1.5 for 1.5 BTC, not satoshis).
    """
    # Thorchain expects amount in 1e8 format (sats, litoshis, etc.)
    # And asset format is CHAIN.SYMBOL, e.g. BTC.BTC
    amount_1e8 = int(amount * (10 ** 8))
    url = f"{THORNODE_QUOTE_URL}/quote/swap?from_asset={from_asset.upper()}&to_asset={to_asset.upper()}&amount={amount_1e8}"

    try:
        async with session.get(url) as response:
            response.raise_for_status()
            quote = await response.json()
            # The quote contains expected output, fees, slippage, and the memo to use for the swap.
            return quote
    except Exception as e:
        logging.error(f"Error fetching Thorchain quote for {from_asset}->{to_asset}: {e}")
        return None


@asyncio.coroutine
async def get_inbound_addresses(session: aiohttp.ClientSession):
    """
    Fetches the inbound addresses from a THORNode.
    This is necessary to know where to send funds for a swap.
    """
    url = f"{THORNODE_URL}/thorchain/inbound_addresses"
    try:
        async with session.get(url) as response:
            response.raise_for_status()
            addresses = await response.json()
            return addresses
    except Exception as e:
        logging.error(f"Error fetching Thorchain inbound addresses: {e}")
        return None


async def execute_thorchain_swap(
    from_token_symbol: str,
    to_address: str,
    amount: float,
    memo: str,
    config_manager,
    test_mode: bool = False
):
    """
    Constructs and broadcasts the transaction to initiate a Thorchain swap.
    """
    logger = config_manager.general_log
    try:
        coin_conf = config_manager.xbridge_manager.xbridge_conf.get(from_token_symbol)
        if not coin_conf:
            logger.error(f"No RPC configuration found for {from_token_symbol} in xbridge.conf.")
            return None
        # Get RPC credentials from the parsed xbridge.conf
        rpc_ip = coin_conf.get('ip', '127.0.0.1')
        rpc_user = coin_conf.get('username')
        rpc_password = coin_conf.get('password')
        rpc_port = coin_conf.get('port')

        if not all([rpc_user, rpc_password, rpc_port]):
            logger.error(f"Incomplete RPC configuration for {from_token_symbol} in xbridge.conf.")
            return None

        satoshi_multiplier = coin_conf.get('coin', 100000000)
        decimal_places = len(str(satoshi_multiplier)) - 1
        amount_str = f"{amount:.{decimal_places}f}"
        full_params = [to_address, amount_str, "", "", False, False, None, "UNSET", None, memo]

        if test_mode:
            mock_txid = f"mock_thor_txid_{uuid.uuid4()}"
            logger.info(f"[TEST MODE] Would execute Core Wallet RPC Call:")
            logger.info(f"    - Target Coin: {from_token_symbol}")
            logger.info(f"    - RPC IP: {rpc_ip}")
            logger.info(f"    - RPC Port: {rpc_port}")
            logger.info(f"    - Method: sendtoaddress")
            logger.info(f"    - Params: {full_params}")
            logger.info(f"    - Returning mock TXID: {mock_txid}")
            return mock_txid

        logger.info(f"Executing Thorchain Swap: send {amount_str} {from_token_symbol} to {to_address} with memo '{memo}'")

        # The actual RPC call to the coin's daemon
        txid = await rpc_call(
            method="sendtoaddress",
            params=full_params,
            url=f"http://{rpc_ip}",
            rpc_user=rpc_user,
            rpc_port=rpc_port,
            rpc_password=rpc_password,
            logger=logger
        )

        if txid:
            logger.info(f"Thorchain swap initiated successfully. TXID: {txid}")
            return txid
        else:
            logger.error(f"Thorchain swap failed. RPC call did not return a TXID.")
            return None

    except Exception as e:
        logger.error(f"Exception during Thorchain swap execution for {from_token_symbol}: {e}", exc_info=True)
        return None
