import uuid
import json
import time
import asyncio
from itertools import combinations
from unittest.mock import patch, AsyncMock

from strategies.base_strategy import BaseStrategy


class ArbitrageStrategy(BaseStrategy):

    def __init__(self, config_manager, controller=None):
        super().__init__(config_manager, controller)
        # Initialize with default values; these will be set by initialize_strategy_specifics
        self.min_profit_margin = 0.01
        self.dry_mode = True
        self.test_mode = False
        self.http_session = None  # Will be set by ConfigManager
        self.xbridge_taker_fee = self.config_manager.config_xbridge.taker_fee_block
        self.xb_monitor_timeout = 300
        self.xb_monitor_poll = 15
        self.thor_monitor_timeout = 600
        self.thor_monitor_poll = 30
        self.thor_api_url = "https://thornode.ninerealms.com"
        self.thor_quote_url = "https://thornode.ninerealms.com/thorchain"
        self.thor_tx_url = "https://thornode.ninerealms.com/thorchain/tx"

    def initialize_strategy_specifics(self, dry_mode: bool = True, min_profit_margin: float = 0.01, test_mode: bool = False, **kwargs):
        self.dry_mode = dry_mode
        self.min_profit_margin = min_profit_margin
        self.test_mode = test_mode
        # Safely access monitoring config using attribute access, falling back to defaults.
        xb_monitoring_config = getattr(self.config_manager.config_xbridge, 'monitoring', None)
        if xb_monitoring_config:
            self.xb_monitor_timeout = getattr(xb_monitoring_config, 'timeout', self.xb_monitor_timeout)
            self.xb_monitor_poll = getattr(xb_monitoring_config, 'poll_interval', self.xb_monitor_poll)

        thor_config = self.config_manager.config_thorchain
        if thor_config:
            thor_monitoring_config = getattr(thor_config, 'monitoring', None)
            if thor_monitoring_config:
                self.thor_monitor_timeout = getattr(thor_monitoring_config, 'timeout', self.thor_monitor_timeout)
                self.thor_monitor_poll = getattr(thor_monitoring_config, 'poll_interval', self.thor_monitor_poll)
            thor_api_config = getattr(thor_config, 'api', None)
            if thor_api_config:
                self.thor_api_url = getattr(thor_api_config, 'thornode_url', self.thor_api_url)
                self.thor_quote_url = getattr(thor_api_config, 'thornode_quote_url', self.thor_quote_url)
                self.thor_tx_url = getattr(thor_api_config, 'thornode_tx_url', self.thor_tx_url)
        self.config_manager.general_log.info(
            f"ArbitrageStrategy initialized. Dry mode: {self.dry_mode}, Min profit: {self.min_profit_margin * 100:.2f}%, Test mode: {self.test_mode}")

    def get_tokens_for_initialization(self, **kwargs) -> list:
        # Define the tokens needed for arbitrage as per the proposal
        return ['LTC', 'DOGE', 'BLOCK']  # Add BLOCK for fee calculation

    def get_pairs_for_initialization(self, tokens_dict, **kwargs) -> dict:
        from definitions.pair import Pair

        # BLOCK is used for fees and does not need a DEX address.
        # Disable dex functionality for it to prevent unnecessary address requests.
        if 'BLOCK' in tokens_dict:
            tokens_dict['BLOCK'].dex.enabled = False

        pairs = {}
        # Create all permutations of the available tokens
        # Use a filtered list to avoid creating pairs with BLOCK as a primary token
        trading_tokens = [t for t in self.get_tokens_for_initialization() if t != 'BLOCK']
        for t1_sym, t2_sym in combinations(trading_tokens, 2):
            pair_key = f"{t1_sym}/{t2_sym}"
            pairs[pair_key] = Pair(
                token1=tokens_dict[t1_sym],
                token2=tokens_dict[t2_sym],
                cfg={'name': pair_key, 'enabled': True},  # Simplified config for arbitrage pairs
                strategy="arbitrage",
                config_manager=self.config_manager
            )
        return pairs

    def get_dex_history_file_path(self, pair_name: str) -> str:
        # Taker strategy might not need history files in the same way, but can be used for logging trades
        return f"{self.config_manager.ROOT_DIR}/data/arbitrage_{pair_name.replace('/', '_')}_trades.log"

    def get_dex_token_address_file_path(self, token_symbol: str) -> str:
        # Not strictly needed for taking, but good to have for balance checks
        return f"{self.config_manager.ROOT_DIR}/data/arbitrage_{token_symbol}_addr.yaml"

    def get_operation_interval(self) -> int:
        return 60

    def should_update_cex_prices(self) -> bool:
        return False

    async def thread_loop_async_action(self, pair_instance):
        """The core arbitrage logic. This is now an async method."""
        check_id = str(uuid.uuid4())[:8]
        if pair_instance.disabled:
            return

        # Check for BLOCK balance for the fee once per cycle
        if not self.dry_mode:
            block_balance = self.config_manager.tokens.get('BLOCK').dex.free_balance or 0
            if block_balance < self.xbridge_taker_fee:
                self.config_manager.general_log.debug(
                    f"[{check_id}] Insufficient BLOCK balance for any trade. "
                    f"Have: {block_balance:.8f}, Need: {self.xbridge_taker_fee:.8f}. Skipping {pair_instance.symbol}."
                )
                return

        self.config_manager.general_log.info(f"[{check_id}] Checking arbitrage for {pair_instance.symbol}...")

        # 1. Get XBridge order book for the pair
        try:
            await pair_instance.dex.update_dex_orderbook()
            xbridge_asks = pair_instance.dex.orderbook.get('asks', [])
            xbridge_bids = pair_instance.dex.orderbook.get('bids', [])
            # Ensure order books are sorted correctly, as API might not guarantee it.
            # Bids should be sorted from highest to lowest price.
            xbridge_bids.sort(key=lambda x: float(x[0]), reverse=True)
            # Asks should be sorted from lowest to highest price.
            xbridge_asks.sort(key=lambda x: float(x[0]), reverse=False)

            self.config_manager.general_log.debug(f"Sorted xbridge_asks: {xbridge_asks}")
            self.config_manager.general_log.debug(f"Sorted xbridge_bids: {xbridge_bids}")
        except Exception as e:
            self.config_manager.general_log.error(
                f"[{check_id}] Error fetching XBridge order book for {pair_instance.symbol}: {e}", exc_info=True)
            return

        # 2. Check both arbitrage legs
        leg1_result = await self._check_arbitrage_leg(pair_instance, xbridge_bids, check_id, 'bid')
        leg2_result = await self._check_arbitrage_leg(pair_instance, xbridge_asks, check_id, 'ask')

        # 3. Log a comprehensive report at DEBUG level
        report_lines = [f"\nArbitrage Report [{check_id}] for {pair_instance.symbol}:"]
        if leg1_result:
            report_lines.append(leg1_result['report'])
        if leg2_result:
            report_lines.append(leg2_result['report'])
        if len(report_lines) > 1:  # Only log if there's something to report
            self.config_manager.general_log.debug("\n".join(report_lines))

        # 4. Log any profitable opportunities at INFO level and execute if not in dry mode
        profitable_leg = None
        if leg1_result and leg1_result.get('profitable'):
            profitable_leg = leg1_result
        elif leg2_result and leg2_result.get('profitable'):
            profitable_leg = leg2_result

        if profitable_leg:
            self.config_manager.general_log.info(f"[{check_id}] {profitable_leg['opportunity_details']}")
            if not self.dry_mode:
                await self.execute_arbitrage(profitable_leg, check_id)
            else:
                leg_num = profitable_leg['execution_data']['leg']
                self.config_manager.general_log.info(
                    f"[{check_id}] [DRY RUN] Would execute arbitrage for {pair_instance.symbol} (Leg {leg_num}).")

        self.config_manager.general_log.info(f"[{check_id}] Finished check for {pair_instance.symbol}.")

    def _generate_arbitrage_report(self, leg: int, pair_instance, report_data: dict) -> str:
        """Generates a formatted string report for a given arbitrage leg."""
        if leg == 1:
            # Leg 1: Sell t1 on XBridge, Buy t1 on Thorchain
            leg_header = f"  Leg 1: Sell {pair_instance.t1.symbol} on XBridge -> Buy {pair_instance.t1.symbol} on Thorchain"
            report = (
                f"{leg_header}\n"
                f"    - XBridge Trade:  Sell {report_data['order_amount']:.8f} {pair_instance.t1.symbol} -> Receive {report_data['amount_t2_from_xb_sell']:.8f} {pair_instance.t2.symbol} (at {report_data['order_price']:.8f} {pair_instance.t2.symbol}/{pair_instance.t1.symbol})\n"
                f"    - XBridge TX Fee:    {report_data['xbridge_fee_t1']:.8f} {pair_instance.t1.symbol} ({report_data['xbridge_fee_t1_ratio']:.2f}%)\n"
                f"    - Thorchain Swap: Sell {report_data['amount_t2_from_xb_sell']:.8f} {pair_instance.t2.symbol} -> Gross Receive {report_data['gross_thorchain_received_t1']:.8f} {pair_instance.t1.symbol}\n"
                f"    - Thorchain Fee:  {report_data['outbound_fee_t1']:.8f} {pair_instance.t1.symbol} ({report_data['network_fee_t1_ratio']:.2f}%)\n"
                f"    - Net Receive:    {report_data['net_thorchain_received_t1']:.8f} {pair_instance.t1.symbol}\n"
                f"    - Net Profit:     {report_data['net_profit_t1_ratio']:.2f}% ({report_data['net_profit_t1_amount']:+.8f} {pair_instance.t1.symbol})"
            )
        else:  # leg == 2
            # Leg 2: Buy t1 on XBridge, Sell t1 on Thorchain
            leg_header = f"  Leg 2: Buy {pair_instance.t1.symbol} on XBridge -> Sell {pair_instance.t1.symbol} on Thorchain"
            report = (
                f"{leg_header}\n"
                f"    - XBridge Trade:  Sell {report_data['xbridge_cost_t2']:.8f} {pair_instance.t2.symbol} -> Receive {report_data['order_amount']:.8f} {pair_instance.t1.symbol} (at {report_data['order_price']:.8f} {pair_instance.t2.symbol}/{pair_instance.t1.symbol})\n"
                f"    - XBridge TX Fee:    {report_data['xbridge_fee_t2']:.8f} {pair_instance.t2.symbol} ({report_data['xbridge_fee_t2_ratio']:.2f}%)\n"
                f"    - Thorchain Swap: Sell {report_data['order_amount']:.8f} {pair_instance.t1.symbol} -> Gross Receive {report_data['gross_thorchain_received_t2']:.8f} {pair_instance.t2.symbol}\n"
                f"    - Thorchain Fee:  {report_data['outbound_fee_t2']:.8f} {pair_instance.t2.symbol} ({report_data['network_fee_t2_ratio']:.2f}%)\n"
                f"    - Net Receive:    {report_data['net_thorchain_received_t2']:.8f} {pair_instance.t2.symbol}\n"
                f"    - Net Profit:     {report_data['net_profit_t2_ratio']:.2f}% ({report_data['net_profit_t2_amount']:+.8f} {pair_instance.t2.symbol})"
            )
        return report


    async def _check_arbitrage_leg(self, pair_instance, order_book, check_id, direction):
        """
        Private method to handle both arbitrage legs.
        Preserves exact original logic for both bid and ask scenarios.
        """
        if not order_book:
            return None

        is_bid = direction == 'bid'
        
        for order in order_book:
            order_price = float(order[0])
            order_amount = float(order[1])
            order_id = order[2]
            
            if is_bid:
                # Original bid logic: bid_amount * bid_price
                amount_t2_from_xb_sell = order_amount * order_price
                
                # Balance Check (ignored in dry mode)
                t1_balance = pair_instance.t1.dex.free_balance or 0
                if not self.dry_mode and t1_balance < order_amount:
                    self.config_manager.general_log.debug(
                        f"[{check_id}] Cannot afford XBridge bid for {order_amount:.8f} {pair_instance.t1.symbol}. "
                        f"Have: {t1_balance:.8f}. Checking next bid."
                    )
                    continue  # Move to the next bid in the order book

                # This is the first affordable order, so we evaluate it and then stop.
                self.config_manager.general_log.debug(
                    f"[{check_id}] Found affordable XBridge bid: {order_amount:.8f} {pair_instance.t1.symbol} at {order_price:.8f}. Evaluating..."
                )

                thorchain_swap_amount = amount_t2_from_xb_sell
                thorchain_from_asset = f"{pair_instance.t2.symbol}.{pair_instance.t2.symbol}"
                thorchain_to_asset = f"{pair_instance.t1.symbol}.{pair_instance.t1.symbol}"
                inbound_chain = pair_instance.t2.symbol
            else:
                # Original ask logic: ask_amount * ask_price
                xbridge_cost_t2 = order_amount * order_price
                
                # Balance Check (ignored in dry mode)
                t2_balance = pair_instance.t2.dex.free_balance or 0
                if not self.dry_mode and t2_balance < xbridge_cost_t2:
                    self.config_manager.general_log.debug(
                        f"[{check_id}] Cannot afford XBridge ask costing {xbridge_cost_t2:.8f} {pair_instance.t2.symbol}. "
                        f"Have: {t2_balance:.8f}. Checking next ask."
                    )
                    continue  # Move to the next ask in the order book

                # This is the first affordable order, so we evaluate it and then stop.
                self.config_manager.general_log.debug(
                    f"[{check_id}] Found affordable XBridge ask: {order_amount:.8f} {pair_instance.t1.symbol} at {order_price:.8f}. Evaluating..."
                )

                thorchain_swap_amount = order_amount
                thorchain_from_asset = f"{pair_instance.t1.symbol}.{pair_instance.t1.symbol}"
                thorchain_to_asset = f"{pair_instance.t2.symbol}.{pair_instance.t2.symbol}"

            from definitions.thorchain_def import get_thorchain_quote

            try:
                thorchain_quote = await get_thorchain_quote(
                    from_asset=thorchain_from_asset,
                    to_asset=thorchain_to_asset,
                    amount=thorchain_swap_amount,
                    session=self.http_session,
                    quote_url=self.thor_quote_url
                )
            except Exception as e:
                direction_desc = "Sell->Buy" if is_bid else "Buy->Sell"
                self.config_manager.general_log.error(
                    f"[{check_id}] Exception during Thorchain quote fetch for {pair_instance.symbol} ({direction_desc}): {e}",
                    exc_info=True)
                return None  # Stop on error

            if not (thorchain_quote and thorchain_quote.get('expected_amount_out')):
                direction_desc = "Sell->Buy" if is_bid else "Buy->Sell"
                self.config_manager.general_log.debug(
                    f"[{check_id}] Thorchain quote was invalid for {pair_instance.symbol} ({direction_desc}).")
                return None  # Stop if quote is invalid

            # The inbound address is provided directly in the quote response
            thorchain_inbound_address = thorchain_quote.get('inbound_address')
            if not thorchain_inbound_address:
                self.config_manager.general_log.error(f"[{check_id}] No Thorchain inbound address found in the quote response.")
                return None

            # Gross amount received from Thorchain
            gross_thorchain_received = float(thorchain_quote['expected_amount_out']) / (10 ** 8)

            # Extract Thorchain fees
            thorchain_fees = thorchain_quote.get('fees', {})
            outbound_fee = float(thorchain_fees.get('outbound', '0')) / (10 ** 8)

            if is_bid:
                # Original bid logic preserved exactly
                gross_thorchain_received_t1 = gross_thorchain_received
                
                # Get XBridge fee for t1
                xbridge_fee_t1 = self.config_manager.xbridge_manager.xbridge_fees_estimate.get(pair_instance.t1.symbol, {}).get('estimated_fee_coin', 0)
                
                outbound_fee_t1 = outbound_fee

                # Calculate Net Profit
                net_thorchain_received_t1 = gross_thorchain_received_t1 - outbound_fee_t1
                net_profit_t1_amount = net_thorchain_received_t1 - order_amount - xbridge_fee_t1

                # Profitability check based on NET profit
                is_profitable = (net_profit_t1_amount > 0) and (
                        (net_profit_t1_amount / order_amount) > self.min_profit_margin) if order_amount else False

                # Update report
                net_profit_t1_ratio = (net_profit_t1_amount / order_amount) * 100 if order_amount else 0
                network_fee_t1_ratio = (outbound_fee_t1 / gross_thorchain_received_t1) * 100 if gross_thorchain_received_t1 else 0
                xbridge_fee_t1_ratio = (xbridge_fee_t1 / order_amount) * 100 if order_amount else 0
                
                report_data = {
                    'order_amount': order_amount, 'amount_t2_from_xb_sell': amount_t2_from_xb_sell,
                    'order_price': order_price, 'xbridge_fee_t1': xbridge_fee_t1,
                    'xbridge_fee_t1_ratio': xbridge_fee_t1_ratio,
                    'gross_thorchain_received_t1': gross_thorchain_received_t1,
                    'outbound_fee_t1': outbound_fee_t1, 'network_fee_t1_ratio': network_fee_t1_ratio,
                    'net_thorchain_received_t1': net_thorchain_received_t1,
                    'net_profit_t1_amount': net_profit_t1_amount,
                    'net_profit_t1_ratio': net_profit_t1_ratio
                }
                report = self._generate_arbitrage_report(1, pair_instance, report_data)

                opportunity_details = None
                if is_profitable:
                    short_header = f"Sell {pair_instance.t1.symbol} on XBridge -> Buy on Thorchain"
                    opportunity_details = (
                        f"Arbitrage Found ({short_header}): "
                        f"Net Profit: {net_profit_t1_ratio:.2f}% on {pair_instance.symbol}."
                    )

                execution_data = None
                if thorchain_quote.get('memo'):
                    execution_data = {
                        'leg': 1,
                        'pair_symbol': pair_instance.symbol,
                        'xbridge_order_id': order_id,
                        'xbridge_from_token': pair_instance.t1.symbol,
                        'xbridge_to_token': pair_instance.t2.symbol,
                        'thorchain_memo': thorchain_quote.get('memo'),
                        'thorchain_inbound_address': thorchain_inbound_address,
                        'thorchain_from_token': pair_instance.t2.symbol,
                        'thorchain_to_token': pair_instance.t1.symbol,
                        'thorchain_swap_amount': amount_t2_from_xb_sell,
                    }
            else:
                # Original ask logic preserved exactly
                gross_thorchain_received_t2 = gross_thorchain_received
                
                # Get XBridge fee for t2
                xbridge_fee_t2 = self.config_manager.xbridge_manager.xbridge_fees_estimate.get(pair_instance.t2.symbol, {}).get('estimated_fee_coin', 0)
                
                outbound_fee_t2 = outbound_fee

                # Calculate Net Profit
                net_thorchain_received_t2 = gross_thorchain_received_t2 - outbound_fee_t2
                net_profit_t2_amount = net_thorchain_received_t2 - xbridge_cost_t2 - xbridge_fee_t2

                # Profitability check based on NET profit
                is_profitable = (net_profit_t2_amount > 0) and (
                        (net_profit_t2_amount / xbridge_cost_t2) > self.min_profit_margin) if xbridge_cost_t2 else False

                # Update report
                net_profit_t2_ratio = (net_profit_t2_amount / xbridge_cost_t2) * 100 if xbridge_cost_t2 else 0
                network_fee_t2_ratio = (outbound_fee_t2 / gross_thorchain_received_t2) * 100 if gross_thorchain_received_t2 else 0
                xbridge_fee_t2_ratio = (xbridge_fee_t2 / xbridge_cost_t2) * 100 if xbridge_cost_t2 else 0
                
                report_data = {
                    'xbridge_cost_t2': xbridge_cost_t2, 'order_amount': order_amount,
                    'order_price': order_price, 'xbridge_fee_t2': xbridge_fee_t2,
                    'xbridge_fee_t2_ratio': xbridge_fee_t2_ratio,
                    'gross_thorchain_received_t2': gross_thorchain_received_t2,
                    'outbound_fee_t2': outbound_fee_t2, 'network_fee_t2_ratio': network_fee_t2_ratio,
                    'net_thorchain_received_t2': net_thorchain_received_t2,
                    'net_profit_t2_amount': net_profit_t2_amount,
                    'net_profit_t2_ratio': net_profit_t2_ratio
                }
                report = self._generate_arbitrage_report(2, pair_instance, report_data)

                opportunity_details = None
                if is_profitable:
                    short_header = f"Buy {pair_instance.t1.symbol} on XBridge -> Sell on Thorchain"
                    opportunity_details = (
                        f"Arbitrage Found ({short_header}): "
                        f"Net Profit: {net_profit_t2_ratio:.2f}% on {pair_instance.symbol}."
                    )

                execution_data = None
                if thorchain_quote.get('memo'):
                    execution_data = {
                        'leg': 2,
                        'pair_symbol': pair_instance.symbol,
                        'xbridge_order_id': order_id,
                        'xbridge_from_token': pair_instance.t2.symbol,
                        'xbridge_to_token': pair_instance.t1.symbol,
                        'thorchain_memo': thorchain_quote.get('memo'),
                        'thorchain_inbound_address': thorchain_inbound_address,
                        'thorchain_from_token': pair_instance.t1.symbol,
                        'thorchain_to_token': pair_instance.t2.symbol,
                        'thorchain_swap_amount': order_amount,
                    }

            return {
                'report': report,
                'profitable': is_profitable,
                'opportunity_details': opportunity_details,
                'execution_data': execution_data
            }

        # If loop finishes, no affordable orders were found
        return None

    async def execute_arbitrage(self, leg_result, check_id):
        """Executes the arbitrage trade for a profitable leg."""
        from definitions.thorchain_def import execute_thorchain_swap, get_thorchain_tx_status
        exec_data = leg_result['execution_data']
        leg_num = exec_data['leg']

        xb_trade_id = None
        thor_txid = None

        self.config_manager.general_log.info(
            f"[{check_id}] EXECUTING LIVE ARBITRAGE for {exec_data['pair_symbol']} (Leg {leg_num})."
        )

        try:
            # --- Step 1: Initiate XBridge Trade ---
            self.config_manager.general_log.info(f"[{check_id}] --- Step 1: Initiate XBridge Trade ---")
            xb_from_token = self.config_manager.tokens[exec_data['xbridge_from_token']]
            xb_to_token = self.config_manager.tokens[exec_data['xbridge_to_token']]

            self.config_manager.general_log.info(f"[{check_id}] Preparing to call take_order with:")
            self.config_manager.general_log.info(f"    - order_id: {exec_data['xbridge_order_id']}")
            self.config_manager.general_log.info(f"    - from_address: {xb_from_token.dex.address}")
            self.config_manager.general_log.info(f"    - to_address: {xb_to_token.dex.address}")
            self.config_manager.general_log.info(f"    - test_mode: {self.test_mode}")

            xb_result = await self.config_manager.xbridge_manager.take_order(
                order_id=exec_data['xbridge_order_id'],
                from_address=xb_from_token.dex.address,
                to_address=xb_to_token.dex.address,
                test_mode=self.test_mode
            )

            if not xb_result or not xb_result.get('id'):
                self.config_manager.general_log.error(
                    f"[{check_id}] XBridge trade failed to initiate or was already taken. Aborting arbitrage.")
                return

            xb_trade_id = xb_result.get('id')
            self.config_manager.general_log.info(
                f"[{check_id}] XBridge trade initiated (ID: {xb_trade_id}). Now monitoring for completion...")

            # --- Step 2: Monitor XBridge Trade ---
            xbridge_completed = await self._monitor_xbridge_order(xb_trade_id, check_id)
            if not xbridge_completed:
                self.config_manager.general_log.error(
                    f"[{check_id}] XBridge trade {xb_trade_id} did not complete successfully. Aborting arbitrage."
                )
                return

            self.config_manager.general_log.info(
                f"[{check_id}] XBridge trade {xb_trade_id} completed successfully. Proceeding with Thorchain swap."
            )

            # --- Step 3: Initiate Thorchain Swap ---
            self.config_manager.general_log.info(f"[{check_id}] --- Step 3: Initiate Thorchain Swap ---")
            self.config_manager.general_log.info(f"[{check_id}] Preparing to call execute_thorchain_swap with:")
            self.config_manager.general_log.info(f"    - from_token_symbol: {exec_data['thorchain_from_token']}")
            self.config_manager.general_log.info(f"    - to_address: {exec_data['thorchain_inbound_address']}")
            self.config_manager.general_log.info(f"    - amount: {exec_data['thorchain_swap_amount']}")
            self.config_manager.general_log.info(f"    - memo: {exec_data['thorchain_memo']}")
            self.config_manager.general_log.info(f"    - test_mode: {self.test_mode}")

            thor_txid = await execute_thorchain_swap(
                from_token_symbol=exec_data['thorchain_from_token'],
                to_address=exec_data['thorchain_inbound_address'],
                amount=exec_data['thorchain_swap_amount'],
                memo=exec_data['thorchain_memo'],
                config_manager=self.config_manager,
                test_mode=self.test_mode
            )

            if not thor_txid:
                self.config_manager.general_log.critical(
                    f"[{check_id}] CRITICAL: XBridge trade {xb_trade_id} was completed, but Thorchain swap FAILED to initiate. "
                    f"Manual intervention REQUIRED."
                )
                return

            self.config_manager.general_log.info(
                f"[{check_id}] Thorchain swap initiated (TXID: {thor_txid}). Now monitoring for completion...")

            # --- Step 4: Monitor Thorchain Swap ---
            thorchain_completed = await self._monitor_thorchain_swap(thor_txid, check_id)
            if not thorchain_completed:
                self.config_manager.general_log.critical(
                    f"[{check_id}] CRITICAL: Thorchain swap {thor_txid} did not complete successfully after initiation. "
                    f"Manual intervention may be required to check balances."
                )
                return

            self.config_manager.general_log.info(
                f"[{check_id}] SUCCESS: Full arbitrage cycle completed. XBridge ID: {xb_trade_id}, Thorchain TXID: {thor_txid}")

        except Exception as e:
            self.config_manager.general_log.error(
                f"[{check_id}] An unexpected error occurred during arbitrage execution: {e}", exc_info=True
            )
            self.config_manager.general_log.critical(
                f"[{check_id}] Arbitrage failed. Last known state: XBridge ID: {xb_trade_id}, Thorchain TXID: {thor_txid}. Manual intervention may be required."
            )

    async def _monitor_xbridge_order(self, order_id: str, check_id: str) -> bool:
        """Monitors an XBridge order until it reaches a terminal state."""
        if self.test_mode:
            self.config_manager.general_log.info(f"[{check_id}] [TEST MODE] Simulating successful XBridge order completion for {order_id}.")
            return True

        timeout = self.xb_monitor_timeout
        poll_interval = self.xb_monitor_poll
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                status_result = await self.config_manager.xbridge_manager.getorderstatus(order_id)
                status = status_result.get('status')
                self.config_manager.general_log.info(f"[{check_id}] Monitoring XBridge order {order_id}: status is '{status}'.")

                if status == 'finished':
                    return True
                if status in ['expired', 'canceled', 'invalid', 'rolled back', 'rollback failed', 'offline']:
                    self.config_manager.general_log.error(f"[{check_id}] XBridge order {order_id} failed with status: {status}.")
                    return False
            except Exception as e:
                self.config_manager.general_log.warning(
                    f"[{check_id}] Error checking status for XBridge order {order_id}: {e}. Retrying..."
                )
            await asyncio.sleep(poll_interval)

        self.config_manager.general_log.error(f"[{check_id}] Timed out waiting for XBridge order {order_id} to complete.")
        return False

    async def _monitor_thorchain_swap(self, txid: str, check_id: str) -> bool:
        """Monitors a Thorchain swap until it reaches a terminal state."""
        from definitions.thorchain_def import get_thorchain_tx_status

        if self.test_mode:
            self.config_manager.general_log.info(f"[{check_id}] [TEST MODE] Simulating successful Thorchain swap completion for {txid}.")
            return True

        timeout = self.thor_monitor_timeout
        poll_interval = self.thor_monitor_poll
        start_time = time.time()

        while time.time() - start_time < timeout:
            status = await get_thorchain_tx_status(txid, self.http_session, self.thor_tx_url)
            self.config_manager.general_log.info(f"[{check_id}] Monitoring Thorchain tx {txid}: status is '{status}'.")

            if status == 'success':
                return True
            if status == 'refunded':
                self.config_manager.general_log.error(f"[{check_id}] Thorchain swap {txid} was refunded.")
                return False

            # if status is 'pending', just sleep and retry
            await asyncio.sleep(poll_interval)

        self.config_manager.general_log.error(f"[{check_id}] Timed out waiting for Thorchain swap {txid} to complete.")
        return False

    async def run_arbitrage_test(self, leg_to_test: int):
        """
        Runs a one-off test of the arbitrage execution logic for a specific leg.
        This method constructs mock data, calls the internal _check_arbitrage_leg
        to generate execution data, and then calls the execute_arbitrage method in test mode.
        This ensures the test uses the actual calculation logic from the strategy.
        """
        if not self.test_mode:
            self.config_manager.general_log.error("run_arbitrage_test can only be run if test_mode is enabled.")
            return

        # Use the first configured pair for the test
        pair_symbol = next(iter(self.config_manager.pairs))
        pair_instance = self.config_manager.pairs[pair_symbol]
        check_id = "test-run"

        self.config_manager.general_log.info(f"Using pair {pair_symbol} for the test.")

        # Ensure tokens have addresses for the test
        if not pair_instance.t1.dex.address:
            await pair_instance.t1.dex.read_address()
        if not pair_instance.t2.dex.address:
            await pair_instance.t2.dex.read_address()

        # Mock external dependencies
        mock_thorchain_quote = AsyncMock()
        mock_inbound_addresses = AsyncMock()

        # Common mock data
        mock_order_id = f'mock_xb_order_{uuid.uuid4()}'
        mock_xb_price = 1500.0
        mock_order_amount_t1 = 0.05

        leg_result = None

        if leg_to_test == 1:
            # Leg 1: Sell t1 on XBridge, Buy t1 on Thorchain
            self.config_manager.general_log.info("Testing Leg 1: Sell XBridge, Buy Thorchain")
            with patch('definitions.thorchain_def.get_thorchain_quote', mock_thorchain_quote):
                # Mock Thorchain quote for profitability. We sell 0.05 t1 for 75 t2. We want to get back > 0.05 t1.
                mock_thorchain_quote.return_value = {
                    'expected_amount_out': str(int(0.0515 * 10**8)),  # e.g., 0.0515 t1
                    'fees': {'outbound': str(int(0.0001 * 10**8))},  # e.g., 0.0001 t1 fee
                    'memo': f'SWAP:{pair_instance.t1.symbol}.{pair_instance.t1.symbol}:{pair_instance.t1.dex.address}',
                    'inbound_address': 'mock_thor_inbound_address_for_' + pair_instance.t2.symbol
                }

                # Mock XBridge bids (profitable)
                mock_bids = [[str(mock_xb_price), str(mock_order_amount_t1), mock_order_id]]

                leg_result = await self._check_arbitrage_leg(pair_instance, mock_bids, check_id, 'bid')

        elif leg_to_test == 2:
            # Leg 2: Buy t1 on XBridge, Sell t1 on Thorchain
            self.config_manager.general_log.info("Testing Leg 2: Buy XBridge, Sell Thorchain")
            with patch('definitions.thorchain_def.get_thorchain_quote', mock_thorchain_quote):
                # Mock Thorchain quote for profitability. We buy 0.05 t1 for 75 t2. We sell 0.05 t1 and want > 75 t2 back.
                mock_thorchain_quote.return_value = {
                    'expected_amount_out': str(int(80 * 10**8)),  # e.g., 80 t2
                    'fees': {'outbound': str(int(0.1 * 10**8))},  # e.g., 0.1 t2 fee
                    'memo': f'SWAP:{pair_instance.t2.symbol}.{pair_instance.t2.symbol}:{pair_instance.t2.dex.address}',
                    'inbound_address': 'mock_thor_inbound_address_for_' + pair_instance.t1.symbol
                }

                # Mock XBridge asks (profitable)
                mock_asks = [[str(mock_xb_price), str(mock_order_amount_t1), mock_order_id]]

                leg_result = await self._check_arbitrage_leg(pair_instance, mock_asks, check_id, 'ask')

        else:
            self.config_manager.general_log.error(f"Invalid leg_to_test: {leg_to_test}. Must be 1 or 2.")
            return

        if leg_result and leg_result.get('profitable'):
            self.config_manager.general_log.info(f"--- [TEST] Profitability Report ---")
            self.config_manager.general_log.info(leg_result['report'])
            self.config_manager.general_log.info(f"--- [TEST] End of Report ---")
            self.config_manager.general_log.info(f"Leg {leg_to_test} Test: Profitable arbitrage found: {leg_result['opportunity_details']}")
            await self.execute_arbitrage(leg_result, check_id)
        else:
            self.config_manager.general_log.warning(f"Leg {leg_to_test} Test: No profitable arbitrage found with mock data.")
            if leg_result:
                self.config_manager.general_log.info(f"--- [TEST] Non-Profitable Report ---")
                self.config_manager.general_log.info(leg_result['report'])
                self.config_manager.general_log.info(f"--- [TEST] End of Report ---")

    def build_sell_order_details(self, dex_pair_instance, manual_dex_price=None) -> tuple:
        pass

    def calculate_sell_price(self, dex_pair_instance, manual_dex_price=None) -> float:
        pass

    def build_buy_order_details(self, dex_pair_instance, manual_dex_price=None) -> tuple:
        pass

    def determine_buy_price(self, dex_pair_instance, manual_dex_price=None) -> float:
        pass

    def get_price_variation_tolerance(self, dex_pair_instance) -> float:
        pass

    def calculate_variation_based_on_side(self, dex_pair_instance, current_order_side: str, cex_price: float,
                                          original_price: float) -> float:
        pass

    def calculate_default_variation(self, dex_pair_instance, cex_price: float, original_price: float) -> float:
        pass

    def init_virtual_order_logic(self, dex_pair_instance, order_history: dict):
        pass

    def handle_order_status_error(self, dex_pair_instance):
        pass

    def reinit_virtual_order_after_price_variation(self, dex_pair_instance, disabled_coins: list):
        pass

    def handle_finished_order(self, dex_pair_instance, disabled_coins: list):
        pass

    def handle_error_swap_status(self, dex_pair_instance):
        pass

    async def thread_init_async_action(self, pair_instance):
        pass
