import logging
import uuid

import aiohttp

from definitions.rpc import rpc_call


async def _fetch_thorchain_api(session: aiohttp.ClientSession, url: str, error_message: str):
    """Helper function to fetch data from a Thorchain API endpoint."""
    try:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json()
    except Exception as e:
        logging.error(f"{error_message}: {e}")
        return None


async def get_thorchain_quote(from_asset: str, to_asset: str, amount: float, session: aiohttp.ClientSession,
                              quote_url: str):
    """
    Fetches a swap quote from Thorchain's Midgard API.
    Amount is in base units (e.g., 1.5 for 1.5 BTC, not satoshis).
    """
    # Thorchain expects amount in 1e8 format (sats, litoshis, etc.)
    # And asset format is CHAIN.SYMBOL, e.g. BTC.BTC
    amount_1e8 = int(amount * (10 ** 8))
    url = f"{quote_url}/quote/swap?from_asset={from_asset.upper()}&to_asset={to_asset.upper()}&amount={amount_1e8}"
    error_message = f"Error fetching Thorchain quote for {from_asset}->{to_asset}"
    quote = await _fetch_thorchain_api(session, url, error_message)
    # The quote contains expected output, fees, slippage, and the memo to use for the swap.
    return quote


async def get_inbound_addresses(session: aiohttp.ClientSession, api_url: str):
    """
    Fetches the inbound addresses from a THORNode.
    This is necessary to know where to send funds for a swap.
    """
    url = f"{api_url}/thorchain/inbound_addresses"
    error_message = "Error fetching Thorchain inbound addresses"
    return await _fetch_thorchain_api(session, url, error_message)


async def check_thorchain_path_status(from_chain: str, to_chain: str, session: aiohttp.ClientSession, api_url: str) -> \
        tuple[bool, str]:
    """
    Checks if the trading path between two chains is active on Thorchain by inspecting inbound addresses.
    Returns a tuple (is_active: bool, reason: str).
    """
    logger = logging.getLogger('general_log')
    try:
        inbound_addresses = await get_inbound_addresses(session, api_url)
        if not inbound_addresses:
            return False, "Could not fetch Thorchain inbound addresses."

        from_chain_data = next((addr for addr in inbound_addresses if addr.get('chain') == from_chain), None)
        to_chain_data = next((addr for addr in inbound_addresses if addr.get('chain') == to_chain), None)

        if not from_chain_data:
            return False, f"Source chain {from_chain} not found in Thorchain inbound addresses."
        if not to_chain_data:
            return False, f"Destination chain {to_chain} not found in Thorchain inbound addresses."

        # If the chain you are sending *from* is halted, you cannot initiate a swap.
        if from_chain_data.get('halted'):
            return False, f"Trading is halted for the source chain: {from_chain}."

        # If the chain you are swapping *to* is halted, you cannot receive the outbound funds.
        if to_chain_data.get('halted'):
            return False, f"Trading is halted for the destination chain: {to_chain}."

        return True, "Path is active."
    except Exception as e:
        logger.error(f"Exception checking Thorchain path status for {from_chain}->{to_chain}: {e}", exc_info=True)
        return False, "An exception occurred during path status check."


async def execute_thorchain_swap(
        from_token_symbol: str,
        to_address: str,
        amount: float,
        memo: str,
        rpc_config: dict,
        decimal_places: int,
        logger,
        test_mode: bool = False
):
    """
    Constructs and broadcasts the transaction to initiate a Thorchain swap.
    """
    try:
        if not rpc_config:
            logger.error(f"No RPC configuration provided for {from_token_symbol}.")
            return None
        # Get RPC credentials from the parsed xbridge.conf
        rpc_ip = rpc_config.get('ip', '127.0.0.1')
        rpc_user = rpc_config.get('username')
        rpc_password = rpc_config.get('password')
        rpc_port = rpc_config.get('port')

        if not all([rpc_user, rpc_password, rpc_port]):
            logger.error(f"Incomplete RPC configuration for {from_token_symbol}.")
            return None

        amount_str = f"{amount:.{decimal_places}f}"
        full_params = [to_address, amount_str, "", "", False, False, None, "UNSET", None, memo]

        if test_mode:
            mock_txid = f"mock_thor_txid_{uuid.uuid4()}"
            logger.info("[TEST MODE] Would execute Core Wallet RPC Call:")
            logger.info(f"    - Target Coin: {from_token_symbol}")
            logger.info(f"    - RPC IP: {rpc_ip}")
            logger.info(f"    - RPC Port: {rpc_port}")
            logger.info(f"    - Method: sendtoaddress")
            logger.info(f"    - Params: {full_params}")
            logger.info(f"    - Returning mock TXID: {mock_txid}")
            return mock_txid

        logger.info(
            f"Executing Thorchain Swap: send {amount_str} {from_token_symbol} to {to_address} with memo '{memo}'")

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


async def get_thorchain_tx_status(txid: str, session: aiohttp.ClientSession, tx_url: str) -> str:
    """
    Checks the status of a Thorchain transaction.
    Returns 'success', 'refunded', or 'pending'.
    """
    logger = logging.getLogger('general_log')
    url = f"{tx_url}/{txid}"
    try:
        async with session.get(url) as response:
            if response.status == 404:
                # Not found yet, still pending
                return 'pending'
            response.raise_for_status()
            tx_data = await response.json()

            out_txs = tx_data.get('out_txs')
            if not out_txs:
                # No outbound transaction yet, still pending
                return 'pending'

            # Check the memo of the first outbound transaction
            first_out_memo = out_txs[0].get('memo', '')
            if 'REFUND:' in first_out_memo.upper():
                return 'refunded'

            # If there's an out_tx and it's not a refund, it's a success
            return 'success'

    except aiohttp.ClientError as e:
        logger.warning(f"Network error checking Thorchain tx {txid}: {e}. Treating as pending.")
        return 'pending'
    except Exception as e:
        logger.error(f"Unexpected error checking Thorchain tx {txid}: {e}. Treating as pending.", exc_info=True)
        return 'pending'
