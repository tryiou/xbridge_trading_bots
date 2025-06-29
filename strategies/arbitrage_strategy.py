import uuid
import json
from itertools import combinations

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

    def initialize_strategy_specifics(self, dry_mode: bool = True, min_profit_margin: float = 0.01, test_mode: bool = False, **kwargs):
        self.dry_mode = dry_mode
        self.min_profit_margin = min_profit_margin
        self.test_mode = test_mode
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
                inbound_chain = pair_instance.t1.symbol

            from definitions.thorchain_def import get_thorchain_quote, get_inbound_addresses

            try:
                thorchain_quote = await get_thorchain_quote(
                    from_asset=thorchain_from_asset,
                    to_asset=thorchain_to_asset,
                    amount=thorchain_swap_amount,
                    session=self.http_session
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

            # Get Thorchain inbound address for the swap
            inbound_addresses = await get_inbound_addresses(self.http_session)
            if not inbound_addresses:
                self.config_manager.general_log.error(f"[{check_id}] Could not fetch Thorchain inbound addresses.")
                return None
            thorchain_inbound_address = next(
                (addr['address'] for addr in inbound_addresses if addr['chain'] == inbound_chain), None)
            if not thorchain_inbound_address:
                self.config_manager.general_log.error(f"[{check_id}] No Thorchain inbound address found for {inbound_chain}.")
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

                leg_header = f"  Leg 1: Sell {pair_instance.t1.symbol} on XBridge -> Buy {pair_instance.t1.symbol} on Thorchain"
                report = (
                    f"{leg_header}\n"
                    f"    - XBridge Trade:  Sell {order_amount:.8f} {pair_instance.t1.symbol} -> Receive {amount_t2_from_xb_sell:.8f} {pair_instance.t2.symbol} (at {order_price:.8f} {pair_instance.t2.symbol}/{pair_instance.t1.symbol})\n"
                    f"    - XBridge Fee:    {xbridge_fee_t1:.8f} {pair_instance.t1.symbol} ({xbridge_fee_t1_ratio:.2f}%)\n"
                    f"    - Thorchain Swap: Sell {amount_t2_from_xb_sell:.8f} {pair_instance.t2.symbol} -> Gross Receive {gross_thorchain_received_t1:.8f} {pair_instance.t1.symbol}\n"
                    f"    - Thorchain Fee:  {outbound_fee_t1:.8f} {pair_instance.t1.symbol} ({network_fee_t1_ratio:.2f}%)\n"
                    f"    - Net Receive:    {net_thorchain_received_t1:.8f} {pair_instance.t1.symbol}\n"
                    f"    - Net Profit:     {net_profit_t1_ratio:.2f}% ({net_profit_t1_amount:+.8f} {pair_instance.t1.symbol})"
                )

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

                leg_header = f"  Leg 2: Buy {pair_instance.t1.symbol} on XBridge -> Sell {pair_instance.t1.symbol} on Thorchain"
                report = (
                    f"{leg_header}\n"
                    f"    - XBridge Trade:  Sell {xbridge_cost_t2:.8f} {pair_instance.t2.symbol} -> Receive {order_amount:.8f} {pair_instance.t1.symbol} (at {order_price:.8f} {pair_instance.t2.symbol}/{pair_instance.t1.symbol})\n"
                    f"    - XBridge Fee:    {xbridge_fee_t2:.8f} {pair_instance.t2.symbol} ({xbridge_fee_t2_ratio:.2f}%)\n"
                    f"    - Thorchain Swap: Sell {order_amount:.8f} {pair_instance.t1.symbol} -> Gross Receive {gross_thorchain_received_t2:.8f} {pair_instance.t2.symbol}\n"
                    f"    - Thorchain Fee:  {outbound_fee_t2:.8f} {pair_instance.t2.symbol} ({network_fee_t2_ratio:.2f}%)\n"
                    f"    - Net Receive:    {net_thorchain_received_t2:.8f} {pair_instance.t2.symbol}\n"
                    f"    - Net Profit:     {net_profit_t2_ratio:.2f}% ({net_profit_t2_amount:+.8f} {pair_instance.t2.symbol})"
                )

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
        from definitions.thorchain_def import execute_thorchain_swap
        exec_data = leg_result['execution_data']
        leg_num = exec_data['leg']
        self.config_manager.general_log.info(
            f"[{check_id}] EXECUTING LIVE ARBITRAGE for {exec_data['pair_symbol']} (Leg {leg_num})."
        )

        # --- Step 1: XBridge Trade ---
        self.config_manager.general_log.info(f"[{check_id}] --- Step 1: XBridge Trade ---")
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
                f"[{check_id}] XBridge trade failed or was already taken. Aborting Thorchain swap.")
            return

        # --- Step 2: Thorchain Swap ---
        self.config_manager.general_log.info(
            f"[{check_id}] XBridge trade successful (ID: {xb_result.get('id')}). Proceeding with Thorchain swap.")
        self.config_manager.general_log.info(f"[{check_id}] --- Step 2: Thorchain Swap ---")
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
                f"[{check_id}] CRITICAL: XBridge trade {xb_result.get('id')} was taken, but Thorchain swap FAILED. "
                f"Manual intervention may be required."
            )
            return

        self.config_manager.general_log.info(
            f"[{check_id}] Both trades initiated successfully. XBridge ID: {xb_result.get('id')}, Thorchain TXID: {thor_txid}")

    async def run_arbitrage_test(self, leg_to_test: int):
        """
        Runs a one-off test of the arbitrage execution logic for a specific leg.
        This method constructs mock data and calls the execute_arbitrage method in test mode.
        """
        if not self.test_mode:
            self.config_manager.general_log.error("run_arbitrage_test can only be run if test_mode is enabled.")
            return

        # Use the first configured pair for the test
        pair_symbol = next(iter(self.config_manager.pairs))
        pair_instance = self.config_manager.pairs[pair_symbol]
        t1 = pair_instance.t1.symbol
        t2 = pair_instance.t2.symbol
        check_id = "test-run"

        self.config_manager.general_log.info(f"Using pair {pair_symbol} for the test.")

        # Ensure tokens have addresses for the test
        if not pair_instance.t1.dex.address:
            await pair_instance.t1.dex.read_address()
        if not pair_instance.t2.dex.address:
            await pair_instance.t2.dex.read_address()

        mock_execution_data = {}
        report = ""
        opportunity_details = ""

        if leg_to_test == 1:
            # Leg 1: Sell t1 on XBridge, Buy t1 on Thorchain
            self.config_manager.general_log.info("Constructing mock data for Leg 1 (Sell XBridge, Buy Thorchain)")

            # Mock trade values for report
            mock_xb_sell_amount_t1 = 0.05  # e.g., selling 0.05 LTC
            mock_xb_price = 1500.0  # e.g., 1500 DOGE per LTC
            mock_xb_receive_amount_t2 = mock_xb_sell_amount_t1 * mock_xb_price
            mock_thor_gross_receive_t1 = 0.0515  # What we get back from Thorchain before fees
            mock_thor_fee_t1 = 0.0001
            mock_xb_fee_t1 = 0.00005

            # Calculations for report
            net_thor_received_t1 = mock_thor_gross_receive_t1 - mock_thor_fee_t1
            net_profit_t1 = net_thor_received_t1 - mock_xb_sell_amount_t1 - mock_xb_fee_t1
            net_profit_t1_ratio = (net_profit_t1 / mock_xb_sell_amount_t1) * 100 if mock_xb_sell_amount_t1 else 0
            thor_fee_ratio = (mock_thor_fee_t1 / mock_thor_gross_receive_t1) * 100 if mock_thor_gross_receive_t1 else 0
            xb_fee_ratio = (mock_xb_fee_t1 / mock_xb_sell_amount_t1) * 100 if mock_xb_sell_amount_t1 else 0

            # Construct report strings
            leg_header = f"  Leg 1: Sell {t1} on XBridge -> Buy {t1} on Thorchain"
            report = (
                f"{leg_header}\n"
                f"    - XBridge Trade:  Sell {mock_xb_sell_amount_t1:.8f} {t1} -> Receive {mock_xb_receive_amount_t2:.8f} {t2} (at {mock_xb_price:.8f} {t2}/{t1})\n"
                f"    - XBridge Fee:    {mock_xb_fee_t1:.8f} {t1} ({xb_fee_ratio:.2f}%)\n"
                f"    - Thorchain Swap: Sell {mock_xb_receive_amount_t2:.8f} {t2} -> Gross Receive {mock_thor_gross_receive_t1:.8f} {t1}\n"
                f"    - Thorchain Fee:  {mock_thor_fee_t1:.8f} {t1} ({thor_fee_ratio:.2f}%)\n"
                f"    - Net Receive:    {net_thor_received_t1:.8f} {t1}\n"
                f"    - Net Profit:     {net_profit_t1_ratio:.2f}% ({net_profit_t1:+.8f} {t1})"
            )
            short_header = f"Sell {t1} on XBridge -> Buy on Thorchain"
            opportunity_details = (
                f"Arbitrage Found ({short_header}): "
                f"Net Profit: {net_profit_t1_ratio:.2f}% on {pair_symbol}."
            )

            # Construct execution data
            mock_execution_data = {
                'leg': 1, 'pair_symbol': pair_symbol, 'xbridge_order_id': f'mock_xb_bid_{uuid.uuid4()}',
                'xbridge_from_token': t1, 'xbridge_to_token': t2,
                'thorchain_memo': f'SWAP:{t1}.{t1}:{pair_instance.t1.dex.address}',
                'thorchain_inbound_address': 'mock_thor_inbound_address_for_' + t2,
                'thorchain_from_token': t2, 'thorchain_to_token': t1, 'thorchain_swap_amount': mock_xb_receive_amount_t2,
            }
        elif leg_to_test == 2:
            # Leg 2: Buy t1 on XBridge, Sell t1 on Thorchain
            self.config_manager.general_log.info("Constructing mock data for Leg 2 (Buy XBridge, Sell Thorchain)")

            # Mock trade values for report
            mock_xb_buy_amount_t1 = 0.05  # e.g. buying 0.05 LTC
            mock_xb_price = 1500.0  # e.g. 1500 DOGE per LTC
            mock_xb_cost_t2 = mock_xb_buy_amount_t1 * mock_xb_price
            mock_thor_gross_receive_t2 = 76.0  # What we get back from Thorchain before fees
            mock_thor_fee_t2 = 0.1
            mock_xb_fee_t2 = 0.001

            # Calculations for report
            net_thor_received_t2 = mock_thor_gross_receive_t2 - mock_thor_fee_t2
            net_profit_t2 = net_thor_received_t2 - mock_xb_cost_t2 - mock_xb_fee_t2
            net_profit_t2_ratio = (net_profit_t2 / mock_xb_cost_t2) * 100 if mock_xb_cost_t2 else 0
            thor_fee_ratio = (mock_thor_fee_t2 / mock_thor_gross_receive_t2) * 100 if mock_thor_gross_receive_t2 else 0
            xb_fee_ratio = (mock_xb_fee_t2 / mock_xb_cost_t2) * 100 if mock_xb_cost_t2 else 0

            # Construct report strings
            leg_header = f"  Leg 2: Buy {t1} on XBridge -> Sell {t1} on Thorchain"
            report = (
                f"{leg_header}\n"
                f"    - XBridge Trade:  Sell {mock_xb_cost_t2:.8f} {t2} -> Receive {mock_xb_buy_amount_t1:.8f} {t1} (at {mock_xb_price:.8f} {t2}/{t1})\n"
                f"    - XBridge Fee:    {mock_xb_fee_t2:.8f} {t2} ({xb_fee_ratio:.2f}%)\n"
                f"    - Thorchain Swap: Sell {mock_xb_buy_amount_t1:.8f} {t1} -> Gross Receive {mock_thor_gross_receive_t2:.8f} {t2}\n"
                f"    - Thorchain Fee:  {mock_thor_fee_t2:.8f} {t2} ({thor_fee_ratio:.2f}%)\n"
                f"    - Net Receive:    {net_thor_received_t2:.8f} {t2}\n"
                f"    - Net Profit:     {net_profit_t2_ratio:.2f}% ({net_profit_t2:+.8f} {t2})"
            )
            short_header = f"Buy {t1} on XBridge -> Sell on Thorchain"
            opportunity_details = (
                f"Arbitrage Found ({short_header}): "
                f"Net Profit: {net_profit_t2_ratio:.2f}% on {pair_symbol}."
            )

            # Construct execution data
            mock_execution_data = {
                'leg': 2, 'pair_symbol': pair_symbol, 'xbridge_order_id': f'mock_xb_ask_{uuid.uuid4()}',
                'xbridge_from_token': t2, 'xbridge_to_token': t1,
                'thorchain_memo': f'SWAP:{t2}.{t2}:{pair_instance.t2.dex.address}',
                'thorchain_inbound_address': 'mock_thor_inbound_address_for_' + t1,
                'thorchain_from_token': t1, 'thorchain_to_token': t2, 'thorchain_swap_amount': mock_xb_buy_amount_t1,
            }

        mock_leg_result = {
            'report': report,
            'profitable': True,  # Assume profitable for test
            'opportunity_details': opportunity_details,
            'execution_data': mock_execution_data
        }

        # Log the profitability report
        self.config_manager.general_log.info(f"--- [TEST] Profitability Report ---")
        self.config_manager.general_log.info(mock_leg_result['report'])
        self.config_manager.general_log.info(f"--- [TEST] End of Report ---")

        await self.execute_arbitrage(mock_leg_result, check_id)

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
