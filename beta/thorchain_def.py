import logging
import sys
import time
import uuid
from typing import Dict, Optional

import aiohttp

###############################################################################
# WARNING: This Thorchain connector module was generated with LLM assistance.  #
# It has NOT been thoroughly reviewed or tested.                               #
#                                                                              #
# DO NOT use in production environments.                                       #
# Attempting real swaps with this module may result in LOSS OF FUNDS.          #
#                                                                              #
# For development/testing purposes only.                                       #
###############################################################################

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


async def get_thorchain_quote(from_asset: str, to_asset: str, base_amount: float, session: aiohttp.ClientSession,
                              quote_url: str, logger: logging.Logger):
    """
    Fetches a swap quote from Thorchain's Midgard API.
    Amount is in base units (e.g., 1.5 for 1.5 BTC, not satoshis).
    """
    # Log pre-call parameters
    # logger.debug("Preparing Thorchain quote request with parameters:")
    # logger.debug(f"  from_asset: {from_asset}")
    # logger.debug(f"  to_asset: {to_asset}")
    # logger.debug(f"  base_amount: {base_amount}")
    # logger.debug(f"  quote_url: {quote_url}")

    # Thorchain expects amount in 1e8 format (sats, litoshis, etc.)
    # And asset format is CHAIN.SYMBOL, e.g. BTC.BTC
    amount_1e8 = int(base_amount * (10 ** 8))
    url = f"{quote_url}/quote/swap?from_asset={from_asset.upper()}&to_asset={to_asset.upper()}&amount={amount_1e8}"

    # Log constructed URL and parameters
    # logger.debug("Constructed Thorchain API request:")
    # logger.debug(f"  URL: {url}")
    # logger.debug(f"  Converted amount: {amount_1e8} (1e8 units)")

    error_message = f"Error fetching Thorchain quote for {from_asset}->{to_asset}"
    # logger.debug(f"Initiating API call to Thorchain: {url}")

    quote = await _fetch_thorchain_api(session, url, error_message)

    # Log the response
    if quote:
        # logger.debug("Received Thorchain quote response:")
        logger.debug(f"Thorchain Quote data: {quote}")
    else:
        logger.debug("Received empty or None quote response from Thorchain API")

    # The quote contains expected output, fees, slippage, and the memo to use for the swap.
    if quote:
        # Fallback/compute missing fields per MD quote requirements
        quote.setdefault('timestamp', time.time())  # Assume fresh if missing
        quote['ttl'] = quote.get('ttl', 30)  # Default 30s expiry
        quote['expiry'] = quote['timestamp'] + quote['ttl']

        # Compute slippage if not provided (bps from in/out ratio; simplistic, as no pool depth in quote)
        if 'slippage_bps' not in quote:
            # Placeholder: 50 bps default; real calc needs pool reserves (future enhancement)
            quote['slippage_bps'] = 50
            logger.warning(f"slippage_bps not in quote for {from_asset}->{to_asset}; defaulted to 50")

        # Ensure inbound_address and memo (critical for execution)
        if not quote.get('inbound_address'):
            # Fetch from separate API if needed; for now, warn and skip
            logger.warning(f"No inbound_address in quote for {from_asset}->{to_asset}")
            quote = None  # Invalidate if critical
        if not quote.get('memo'):
            quote['memo'] = f":swap:{to_asset.upper()}:{int(base_amount * 10 ** 8)}:"  # Basic fallback
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
    logger = logging.getLogger('check_thorchain_path_status')
    try:
        inbound_addresses = await get_inbound_addresses(session, api_url)
        if inbound_addresses is None or not inbound_addresses:
            return False, "Could not fetch Thorchain inbound addresses."

        # Safe iteration: skip invalid elements
        from_chain_data = None
        to_chain_data = None
        for addr in (inbound_addresses or []):
            if isinstance(addr, dict) and addr.get('chain') == from_chain:
                from_chain_data = addr
            if isinstance(addr, dict) and addr.get('chain') == to_chain:
                to_chain_data = addr
            if from_chain_data and to_chain_data:
                break

        if not from_chain_data:
            inbound_snippet = str(inbound_addresses)[:100] if inbound_addresses is not None else 'None/Empty'
            logger.warning(
                f"No valid data for source chain {from_chain} in inbound_addresses response: {inbound_snippet}")  # Log snippet for debug
            return False, f"Source chain {from_chain} not found or invalid in Thorchain inbound addresses."
        if not to_chain_data:
            inbound_snippet = str(inbound_addresses)[:100] if inbound_addresses is not None else 'None/Empty'
            logger.warning(
                f"No valid data for destination chain {to_chain} in inbound_addresses response: {inbound_snippet}")
            return False, f"Destination chain {to_chain} not found or invalid in Thorchain inbound addresses."

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
    [RESTRICTED TO TEST MODE ONLY]
    Simulates Thorchain swap for testing purposes only.
    """
    try:
        if not test_mode:
            print("\n" + "=" * 60)
            print("ERROR: Thorchain swap execution disabled - still in development")
            print("=" * 60)
            print("The thorchain swap function is not yet ready for production use.")
            print("=" * 60 + "\n")
            sys.exit(1)
            
        # Force test mode ON regardless of input parameter
        test_mode = True
        
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


_thorchain_decimals_cache: Dict[str, int] = {}  # Module-level for shared use


async def _get_thorchain_decimals(chain_symbol: str, session: aiohttp.ClientSession, api_url: str) -> int:
    """Lazily cache and return decimals for chain from inbound_addresses."""
    global _thorchain_decimals_cache
    if chain_symbol not in _thorchain_decimals_cache:
        inbound_addresses = await get_inbound_addresses(session, api_url)
        if inbound_addresses:
            for asset in inbound_addresses:
                if isinstance(asset, dict) and asset.get('chain') == chain_symbol:
                    _thorchain_decimals_cache[chain_symbol] = int(asset.get('decimals', 8))
                    break
            else:
                _thorchain_decimals_cache[chain_symbol] = 8
        else:
            _thorchain_decimals_cache[chain_symbol] = 8
        logging.debug(f"Cached decimals for {chain_symbol}: {_thorchain_decimals_cache[chain_symbol]}")
    return _thorchain_decimals_cache[chain_symbol]


async def get_actual_swap_received(txid: str, session: aiohttp.ClientSession, tx_url: str, to_chain: str,
                                   to_address: Optional[str] = None, api_url: str = None) -> Optional[float]:
    """
    Parse confirmed THORChain tx for actual received amount in base units, filtered by destination.
    
    Args:
        txid: Transaction ID
        session: aiohttp session
        tx_url: Base TX URL
        to_chain: Expected destination chain (e.g., 'DOGE')
        to_address: Optional exact to_address for precision
        api_url: API URL for decimals lookup
        
    Returns:
        Actual received float or None on failure/pending
    """
    try:
        url = f"{tx_url}/{txid}"
        async with session.get(url) as response:
            response.raise_for_status()
            data = await response.json()
            out_txs = data.get('out_txs', [])
            if not out_txs or not isinstance(out_txs, list):
                logging.warning(f"No valid out_txs in tx {txid}; still pending.")
                return None

            # Filter: Match chain and optionally address; skip refunds; take first matching (swap output)
            matching_tx = None
            for out_tx in out_txs:
                if isinstance(out_tx, dict) and out_tx.get('chain') == to_chain and \
                        (not to_address or out_tx.get('to_address') == to_address) and \
                        'REFUND:' not in str(out_tx.get('memo', '')).upper():
                    matching_tx = out_tx
                    break

            if not matching_tx:
                logging.error(f"No matching non-refund out_tx for {to_chain} (addr: {to_address}) in tx {txid}.")
                return None

            # Parse amount with decimals from cache/helper
            decimals = await _get_thorchain_decimals(to_chain, session, api_url) if api_url else 8
            amount_str = matching_tx.get('amount', '0')
            received = float(amount_str) / 10 ** decimals
            logging.info(f"Parsed actual received for tx {txid}: {received} on {to_chain} to {to_address or 'any'}.")
            return received
    except Exception as e:
        logging.error(f"Error parsing actual received for tx {txid}: {e}")
        return None
