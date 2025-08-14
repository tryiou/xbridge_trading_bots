import asyncio
import json
import os
import time
import uuid
from itertools import combinations
from typing import List, Dict, Any, Optional, Callable, Coroutine, TYPE_CHECKING

import aiohttp

from definitions.error_handler import OperationalError
from definitions.trade_state import TradeState
from strategies.base_strategy import BaseStrategy

if TYPE_CHECKING:
    from definitions.config_manager import ConfigManager
    from definitions.pair import Pair
    from definitions.starter import MainController


class ArbitrageStrategy(BaseStrategy):

    def __init__(self, config_manager: 'ConfigManager', controller: Optional['MainController'] = None):
        super().__init__(config_manager, controller)
        # Initialize with default values; these will be set by initialize_strategy_specifics
        self.min_profit_margin = 0.01
        self.dry_mode = True
        self.test_mode = False
        self.http_session: Optional['aiohttp.ClientSession'] = None  # Will be set by ConfigManager
        self.xbridge_taker_fee = self.config_manager.config_xbridge.taker_fee_block
        self.xb_monitor_timeout = 300
        self.xb_monitor_poll = 15
        self.thor_monitor_timeout = 600
        self.thor_monitor_poll = 30
        self.thor_api_url = "https://thornode.ninerealms.com"
        self.thor_quote_url = "https://thornode.ninerealms.com/thorchain"
        self.thor_tx_url = "https://thornode.ninerealms.com/thorchain/tx"
        self.thorchain_asset_decimals: Dict[str, int] = {}

    def initialize_strategy_specifics(self, dry_mode: bool = None, min_profit_margin: float = None,
                                      test_mode: bool = False, **kwargs):
        # Load defaults from config file first
        config = self.config_manager.config_arbitrage
        config_dry_mode = getattr(config, 'dry_mode', True)
        config_min_profit = getattr(config, 'min_profit_margin', 0.01)

        # CLI/direct-call arguments take precedence over the config file.
        # This makes the strategy robust whether launched from CLI or GUI.
        self.dry_mode = dry_mode if dry_mode is not None else config_dry_mode
        self.min_profit_margin = min_profit_margin if min_profit_margin is not None else config_min_profit
        self.test_mode = test_mode
        # Safely access monitoring config using attribute access, falling back to defaults.
        self._load_strategy_configs()
        self.pause_file_path = os.path.join(self.config_manager.ROOT_DIR, "data", "TRADING_PAUSED.json")

        trading_tokens = self.config_manager.config_arbitrage.trading_tokens
        fee_token = self.config_manager.config_arbitrage.fee_token

        self.config_manager.general_log.info("--- Arbitrage Strategy Parameters ---")
        self.config_manager.general_log.info(f"  - Mode: {'DRY RUN' if self.dry_mode else 'LIVE'}")
        self.config_manager.general_log.info(f"  - Minimum Profit Margin: {self.min_profit_margin * 100:.2f}%")
        self.config_manager.general_log.info(f"  - Trading Tokens: {', '.join(trading_tokens)}")
        self.config_manager.general_log.info(f"  - Fee Token: {fee_token}")
        self.config_manager.general_log.info(f"  - Test Mode: {self.test_mode}")
        self.config_manager.general_log.info("------------------------------------")

    def _load_strategy_configs(self):
        """Loads strategy-specific configurations from the config files, with fallbacks."""
        try:
            def get_nested_attr(obj, attrs, default):
                """Safely gets a nested attribute from an object."""
                for attr in attrs:
                    obj = getattr(obj, attr, None)
                    if obj is None:
                        return default
                return obj

            self.xb_monitor_timeout = get_nested_attr(self.config_manager.config_xbridge, ['monitoring', 'timeout'],
                                                      self.xb_monitor_timeout)
            self.xb_monitor_poll = get_nested_attr(self.config_manager.config_xbridge, ['monitoring', 'poll_interval'],
                                                   self.xb_monitor_poll)
            self.thor_monitor_timeout = get_nested_attr(self.config_manager.config_thorchain, ['monitoring', 'timeout'],
                                                        self.thor_monitor_timeout)
            self.thor_monitor_poll = get_nested_attr(self.config_manager.config_thorchain,
                                                     ['monitoring', 'poll_interval'],
                                                     self.thor_monitor_poll)
            self.thor_api_url = get_nested_attr(self.config_manager.config_thorchain, ['api', 'thornode_url'],
                                                self.thor_api_url)
            self.thor_quote_url = get_nested_attr(self.config_manager.config_thorchain, ['api', 'thornode_quote_url'],
                                                  self.thor_quote_url)
            self.thor_tx_url = get_nested_attr(self.config_manager.config_thorchain, ['api', 'thornode_tx_url'],
                                               self.thor_tx_url)
        except Exception as e:
            self.config_manager.error_handler.handle(
                OperationalError(f"Error loading strategy configs: {str(e)}"),
                context={"stage": "_load_strategy_configs"}
            )
            raise

    def get_tokens_for_initialization(self, **kwargs) -> List[str]:
        """Gets the list of tokens from the arbitrage config file."""
        trading_tokens = self.config_manager.config_arbitrage.trading_tokens
        fee_token = self.config_manager.config_arbitrage.fee_token
        if fee_token and fee_token not in trading_tokens:
            # Ensure the fee token is always included for balance checks.
            return trading_tokens + [fee_token]
        return trading_tokens

    def get_pairs_for_initialization(self, tokens_dict: Dict[str, Any], **kwargs) -> Dict[str, 'Pair']:
        from definitions.pair import Pair

        # BLOCK is used for fees and does not need a DEX address.
        # Disable dex functionality for it to prevent unnecessary address requests.
        if 'BLOCK' in tokens_dict:
            tokens_dict['BLOCK'].dex.enabled = False

        pairs = {}
        trading_tokens = self.config_manager.config_arbitrage.trading_tokens
        # Create all permutations of the available tokens
        # Use a filtered list to avoid creating pairs with BLOCK as a primary token
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

    async def thread_loop_async_action(self, pair_instance: 'Pair'):
        """The core arbitrage logic. This is now an async method."""
        # --- PAUSE CHECK ---
        if os.path.exists(self.pause_file_path):
            try:
                with open(self.pause_file_path, 'r') as f:
                    pause_reason = json.load(f).get('reason', 'Unknown reason.')
                self.config_manager.general_log.warning(
                    f"TRADING PAUSED. Reason: {pause_reason}. "
                    f"Bot is monitoring for refund. Trading will resume automatically."
                )
            except (IOError, json.JSONDecodeError) as e:
                self.config_manager.error_handler.handle(
                    OperationalError(f"Could not read pause file: {str(e)}"),
                    context={"file": self.pause_file_path}
                )
            return

        check_id = str(uuid.uuid4())

        log_prefix = check_id if self.test_mode else check_id[:8]
        if pair_instance.disabled:
            return

        # Check for BLOCK balance for the fee once per cycle
        if not self.dry_mode:
            block_balance = self.config_manager.tokens.get('BLOCK').dex.free_balance or 0
            if block_balance < self.xbridge_taker_fee:
                self.config_manager.general_log.debug(
                    f"[{log_prefix}] Insufficient BLOCK balance for any trade. "
                    f"Have: {block_balance:.8f}, Need: {self.xbridge_taker_fee:.8f}. Skipping {pair_instance.symbol}."
                )
                return

        self.config_manager.general_log.info(f"[{log_prefix}] Checking arbitrage for {pair_instance.symbol}...")

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
            self.config_manager.error_handler.handle(
                OperationalError(f"Error fetching XBridge order book: {str(e)}"),
                context={
                    "pair": pair_instance.symbol,
                    "log_prefix": log_prefix
                }
            )
            return

        # 2. Check both arbitrage legs
        leg1_result = await self._check_arbitrage_leg(pair_instance, xbridge_bids, check_id, 'bid')
        leg2_result = await self._check_arbitrage_leg(pair_instance, xbridge_asks, check_id, 'ask')

        # 3. Log a comprehensive report at DEBUG level
        report_lines = [f"\nArbitrage Report [{log_prefix}] for {pair_instance.symbol}:"]
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
            self.config_manager.general_log.info(f"[{log_prefix}] {profitable_leg['opportunity_details']}")
            if not self.dry_mode:
                await self.execute_arbitrage(profitable_leg, check_id)
            else:
                leg_num = profitable_leg['execution_data']['leg']
                self.config_manager.general_log.info(
                    f"[{log_prefix}] [DRY RUN] Would execute arbitrage for {pair_instance.symbol} (Leg {leg_num}).")

        self.config_manager.general_log.info(f"[{log_prefix}] Finished check for {pair_instance.symbol}.")

    def _generate_arbitrage_report(self, leg: int, pair_instance: 'Pair', report_data: Dict[str, Any]) -> str:
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

    def _calculate_profitability_and_fees(self, cost_amount: float, gross_receive_amount: float, outbound_fee: float,
                                          xbridge_fee: float) -> Dict[str, Any]:
        """
        A pure calculation function to determine profitability and fee structures.
        """
        net_receive_amount = gross_receive_amount - outbound_fee
        net_profit_amount = net_receive_amount - cost_amount - xbridge_fee

        is_profitable = (net_profit_amount > 0) and \
                        ((net_profit_amount / cost_amount) > self.min_profit_margin) if cost_amount > 0 else False

        return {
            'net_profit_amount': net_profit_amount,
            'net_profit_ratio': (net_profit_amount / cost_amount) * 100 if cost_amount > 0 else 0,
            'is_profitable': is_profitable,
            'network_fee_ratio': (outbound_fee / gross_receive_amount) * 100 if gross_receive_amount > 0 else 0,
            'xbridge_fee_ratio': (xbridge_fee / cost_amount) * 100 if cost_amount > 0 else 0,
        }

    async def _evaluate_opportunity(self, pair_instance: 'Pair', order_data: Dict[str, Any], check_id: str,
                                    is_bid: bool) -> Optional[Dict[str, Any]]:
        """
        Evaluates a single arbitrage opportunity after the leg-specific parameters have been set.
        This helper centralizes quote fetching, profit calculation, and report generation.
        """
        from definitions.thorchain_def import get_thorchain_quote
        log_prefix = check_id if self.test_mode else check_id[:8]

        try:
            thorchain_quote = await get_thorchain_quote(
                from_asset=order_data['thorchain_from_asset'],
                to_asset=order_data['thorchain_to_asset'],
                amount=order_data['thorchain_swap_amount'],
                session=self.http_session,
                quote_url=self.thor_quote_url
            )
        except Exception as e:
            direction_desc = "Sell->Buy" if is_bid else "Buy->Sell"
            self.config_manager.error_handler.handle(
                OperationalError(f"Thorchain quote fetch failed: {str(e)}"),
                context={
                    "pair": pair_instance.symbol,
                    "direction": direction_desc,
                    "log_prefix": log_prefix
                }
            )
            return None

        if not (thorchain_quote and thorchain_quote.get('expected_amount_out')):
            direction_desc = "Sell->Buy" if is_bid else "Buy->Sell"
            self.config_manager.general_log.debug(
                f"[{log_prefix}] Thorchain quote was invalid for {pair_instance.symbol} ({direction_desc}).")
            return None  # Stop if quote is invalid

        thorchain_inbound_address = thorchain_quote.get('inbound_address')
        if not thorchain_inbound_address:
            self.config_manager.general_log.error(
                f"[{log_prefix}] No Thorchain inbound address found in the quote response.")
            return None

        # --- Calculation ---
        gross_receive_amount = float(thorchain_quote['expected_amount_out']) / (10 ** 8)
        outbound_fee = float(thorchain_quote.get('fees', {}).get('outbound', '0')) / (10 ** 8)

        profit_data = self._calculate_profitability_and_fees(
            cost_amount=order_data['cost_amount'],
            gross_receive_amount=gross_receive_amount,
            outbound_fee=outbound_fee,
            xbridge_fee=order_data['xbridge_fee']
        )

        # --- Reporting & Data Structuring ---
        if is_bid:
            report_data = {
                'order_amount': order_data['order_amount'],
                'amount_t2_from_xb_sell': order_data['thorchain_swap_amount'],
                'order_price': order_data['order_price'], 'xbridge_fee_t1': order_data['xbridge_fee'],
                'xbridge_fee_t1_ratio': profit_data['xbridge_fee_ratio'],
                'gross_thorchain_received_t1': gross_receive_amount, 'outbound_fee_t1': outbound_fee,
                'network_fee_t1_ratio': profit_data['network_fee_ratio'],
                'net_thorchain_received_t1': gross_receive_amount - outbound_fee,
                'net_profit_t1_amount': profit_data['net_profit_amount'],
                'net_profit_t1_ratio': profit_data['net_profit_ratio']
            }
            report = self._generate_arbitrage_report(1, pair_instance, report_data)
            short_header = f"Sell {pair_instance.t1.symbol} on XBridge -> Buy on Thorchain"
        else:  # ask
            report_data = {
                'xbridge_cost_t2': order_data['cost_amount'], 'order_amount': order_data['order_amount'],
                'order_price': order_data['order_price'], 'xbridge_fee_t2': order_data['xbridge_fee'],
                'xbridge_fee_t2_ratio': profit_data['xbridge_fee_ratio'],
                'gross_thorchain_received_t2': gross_receive_amount, 'outbound_fee_t2': outbound_fee,
                'network_fee_t2_ratio': profit_data['network_fee_ratio'],
                'net_thorchain_received_t2': gross_receive_amount - outbound_fee,
                'net_profit_t2_amount': profit_data['net_profit_amount'],
                'net_profit_t2_ratio': profit_data['net_profit_ratio']
            }
            report = self._generate_arbitrage_report(2, pair_instance, report_data)
            short_header = f"Buy {pair_instance.t1.symbol} on XBridge -> Sell on Thorchain"

        opportunity_details = f"Arbitrage Found ({short_header}): Net Profit: {profit_data['net_profit_ratio']:.2f}% on {pair_instance.symbol}." if \
            profit_data['is_profitable'] else None

        execution_data = {
            'leg': 1 if is_bid else 2,
            'xbridge_from_amount': order_data['cost_amount'],
            'pair_symbol': pair_instance.symbol,
            'xbridge_order_id': order_data['order_id'],
            'xbridge_from_token': pair_instance.t1.symbol if is_bid else pair_instance.t2.symbol,
            'xbridge_to_token': pair_instance.t2.symbol if is_bid else pair_instance.t1.symbol,
            'xbridge_fee': order_data['xbridge_fee'],
            'thorchain_memo': thorchain_quote.get('memo'),
            'thorchain_inbound_address': thorchain_inbound_address,
            'thorchain_from_token': pair_instance.t2.symbol if is_bid else pair_instance.t1.symbol,
            'thorchain_to_token': pair_instance.t1.symbol if is_bid else pair_instance.t2.symbol,
            'thorchain_swap_amount': order_data['thorchain_swap_amount'],
            'thorchain_quote': thorchain_quote,
        } if thorchain_quote.get('memo') else None

        return {
            'report': report,
            'profitable': profit_data['is_profitable'],
            'opportunity_details': opportunity_details,
            'execution_data': execution_data
        }

    async def _check_arbitrage_leg(self, pair_instance: 'Pair', order_book: List[List[str]], check_id: str,
                                   direction: str) -> Optional[Dict[str, Any]]:
        """
        Private method to handle both arbitrage legs.
        Preserves exact original logic for both bid and ask scenarios.
        """
        if not order_book:
            return None

        log_prefix = check_id if self.test_mode else check_id[:8]
        is_bid = direction == 'bid'

        for order in order_book:
            order_price = float(order[0])
            order_amount = float(order[1])
            order_id = order[2]
            order_data = {'order_price': order_price, 'order_amount': order_amount, 'order_id': order_id}

            if is_bid:
                # Leg 1: Sell t1 on XBridge (we give t1), Buy t1 on Thorchain (we give t2)
                balance_token, fee_token = pair_instance.t1, pair_instance.t1
                required_amount = order_amount
                order_data['cost_amount'] = order_amount
                order_data['thorchain_swap_amount'] = order_amount * order_price
                order_data['thorchain_from_asset'] = f"{pair_instance.t2.symbol}.{pair_instance.t2.symbol}"
                order_data['thorchain_to_asset'] = f"{pair_instance.t1.symbol}.{pair_instance.t1.symbol}"
            else:
                # Leg 2: Buy t1 on XBridge (we give t2), Sell t1 on Thorchain (we give t1)
                balance_token, fee_token = pair_instance.t2, pair_instance.t2
                required_amount = order_amount * order_price
                order_data['cost_amount'] = required_amount
                order_data['thorchain_swap_amount'] = order_amount
                order_data['thorchain_from_asset'] = f"{pair_instance.t1.symbol}.{pair_instance.t1.symbol}"
                order_data['thorchain_to_asset'] = f"{pair_instance.t2.symbol}.{pair_instance.t2.symbol}"

            # Check for sufficient balance
            if not self.dry_mode and (balance_token.dex.free_balance or 0) < required_amount:
                self.config_manager.general_log.debug(
                    f"[{log_prefix}] Insufficient balance for {direction} order. "
                    f"Need {required_amount:.8f} {balance_token.symbol}, "
                    f"Have: {balance_token.dex.free_balance or 0:.8f}. Checking next order."
                )
                continue

            # Assign fee and log that we found an affordable order
            order_data['xbridge_fee'] = self.config_manager.xbridge_manager.xbridge_fees_estimate.get(
                fee_token.symbol, {}).get('estimated_fee_coin', 0)
            self.config_manager.general_log.debug(
                f"[{log_prefix}] Found affordable XBridge {direction}: {order_amount:.8f} {pair_instance.t1.symbol} at {order_price:.8f}. Evaluating..."
            )

            # --- Pre-flight Check: Ensure Thorchain path is active before getting a quote ---
            from definitions.thorchain_def import check_thorchain_path_status
            thor_from_chain = order_data['thorchain_from_asset'].split('.')[0]
            thor_to_chain = order_data['thorchain_to_asset'].split('.')[0]

            is_path_active, reason = await check_thorchain_path_status(
                from_chain=thor_from_chain,
                to_chain=thor_to_chain,
                session=self.http_session,
                api_url=self.thor_api_url
            )

            if not is_path_active:
                self.config_manager.general_log.warning(
                    f"[{log_prefix}] Skipping opportunity for {pair_instance.symbol}: {reason}"
                )
                # Stop checking this leg for this cycle since the path is halted.
                return None

            # This is the first affordable order, so we evaluate it and then stop.
            return await self._evaluate_opportunity(pair_instance, order_data, check_id, is_bid)

        # If loop finishes, no affordable orders were found
        return None

    async def execute_arbitrage(self, leg_result: Dict[str, Any], check_id: str):
        """Executes the arbitrage trade for a profitable leg."""
        exec_data = leg_result['execution_data']
        leg_num = exec_data['leg']
        log_prefix = check_id if self.test_mode else check_id[:8]
        state = TradeState(self, check_id)

        xb_trade_id = None
        thor_txid = None

        self.config_manager.general_log.info(
            f"[{log_prefix}] EXECUTING LIVE ARBITRAGE for {exec_data['pair_symbol']} (Leg {leg_num})."
        )

        try:
            state.save('INITIATED', {'execution_data': exec_data})

            # --- Step 1: Initiate XBridge Trade ---
            self.config_manager.general_log.info(f"[{log_prefix}] --- Step 1: Initiate XBridge Trade ---")
            xb_from_token = self.config_manager.tokens[exec_data['xbridge_from_token']]
            xb_to_token = self.config_manager.tokens[exec_data['xbridge_to_token']]

            self.config_manager.general_log.info(f"[{log_prefix}] Preparing to call take_order with:")
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
                self.config_manager.error_handler.handle(
                    OperationalError("XBridge trade failed to initiate or was already taken"),
                    context={
                        "order_id": exec_data['xbridge_order_id'],
                        "log_prefix": log_prefix
                    }
                )
                state.archive("xbridge-init-failed")
                return

            xb_trade_id = xb_result.get('id')
            state.save('XBRIDGE_INITIATED', {'xbridge_trade_id': xb_trade_id})
            self.config_manager.general_log.info(
                f"[{log_prefix}] XBridge trade initiated (ID: {xb_trade_id}). Now monitoring for completion...")

            # --- Step 2: Monitor XBridge Trade ---
            xbridge_completed = await self._monitor_xbridge_order(xb_trade_id, check_id)
            if not xbridge_completed:
                self.config_manager.error_handler.handle(
                    OperationalError("XBridge trade did not complete successfully"),
                    context={
                        "xbridge_trade_id": xb_trade_id,
                        "log_prefix": log_prefix
                    }
                )
                state.archive("xbridge-monitor-failed")
                return

            self.config_manager.general_log.info(
                f"[{log_prefix}] XBridge trade {xb_trade_id} completed successfully. Proceeding with Thorchain swap."
            )

            state.save('XBRIDGE_CONFIRMED', {'xbridge_trade_id': xb_trade_id})

            # --- Step 3: Re-evaluate profitability and execute Thorchain swap ---
            # This is the crucial pre-flight check before committing to the second leg.
            await self._reevaluate_and_execute_thorchain(state, state.state_data)

        except Exception as e:
            self.config_manager.error_handler.handle(
                OperationalError(f"Arbitrage execution failed: {str(e)}"),
                context={
                    "last_known_xb_id": state.state_data.get('xbridge_trade_id', 'N/A'),
                    "log_prefix": log_prefix
                },
                exc_info=True
            )
            state.archive("execution-error")

    async def resume_interrupted_trades(self):
        """On startup, check for and delegate handling of any trades that didn't complete."""
        await asyncio.sleep(5)  # Wait for other initializations to complete
        self.config_manager.general_log.info("Checking for interrupted arbitrage trades...")
        unfinished_trades = TradeState.get_unfinished_trades(self)

        if not unfinished_trades:
            self.config_manager.general_log.info("No interrupted trades found.")
            return

        # Create a mapping from status to handler function
        status_handlers = {
            'XBRIDGE_INITIATED': self._resume_from_xb_initiated,
            'XBRIDGE_CONFIRMED': self._resume_from_xb_confirmed,
            'THORCHAIN_INITIATED': self._resume_from_thor_initiated,
            'AWAITING_REFUND': self._resume_from_awaiting_refund,
        }

        for state_data in unfinished_trades:
            check_id = state_data['check_id']
            initial_status = state_data['status']
            log_prefix = check_id if self.test_mode else check_id[:8]
            self.config_manager.general_log.warning(
                f"[{log_prefix}] Resuming interrupted trade with status: {initial_status}")

            state = TradeState(self, check_id)
            handler = status_handlers.get(initial_status)
            if handler:
                await handler(state, state_data)
            else:
                self.config_manager.general_log.error(
                    f"[{log_prefix}] No handler found for resumption status '{initial_status}'. Archiving for manual review.")
                state.archive("unknown-resume-status")

            if os.path.exists(state.state_file_path):
                # Check the status again after the handler has run to see if it changed.
                with open(state.state_file_path, 'r') as f:
                    final_state_data = json.load(f)
                final_status = final_state_data.get('status')

                # Only warn if the status hasn't changed, indicating a potential stall.
                if initial_status == final_status:
                    self.config_manager.general_log.warning(
                        f"[{log_prefix}] State file for status '{initial_status}' was not resolved after resumption logic. It may require manual review.")

    async def _resume_from_xb_initiated(self, state: TradeState, state_data: Dict[str, Any]):
        """Handler for resuming from XBRIDGE_INITIATED state."""
        xb_trade_id = state_data['xbridge_trade_id']
        self.config_manager.general_log.info(
            f"[{state.log_prefix}] Resuming: Monitoring XBridge order {xb_trade_id}...")

        xbridge_completed = await self._monitor_xbridge_order(xb_trade_id, state.check_id)
        if not xbridge_completed:
            self.config_manager.general_log.error(
                f"[{state.log_prefix}] Resumed XBridge trade {xb_trade_id} failed. Aborting.")
            state.archive("resumed-xb-failed")
            return

        # Preserve the full state from the file before saving the new status.
        state.state_data = state_data
        state.save('XBRIDGE_CONFIRMED', {})  # No new data to add, just updating status.
        # Now that the state is confirmed, we can delegate to the next handler
        await self._resume_from_xb_confirmed(state, state.state_data)

    async def _resume_from_xb_confirmed(self, state: TradeState, state_data: Dict[str, Any]):
        """Handler for resuming from XBRIDGE_CONFIRMED state. Delegates to the re-evaluation helper."""
        xb_trade_id = state_data['xbridge_trade_id']
        self.config_manager.general_log.info(
            f"[{state.log_prefix}] Resuming: XBridge trade {xb_trade_id} is confirmed. Re-evaluating Thorchain leg.")
        await self._reevaluate_and_execute_thorchain(state, state_data)

    async def _resume_from_thor_initiated(self, state: TradeState, state_data: Dict[str, Any]):
        """Handler for resuming from THORCHAIN_INITIATED state."""
        thor_txid = state_data['thorchain_txid']
        self.config_manager.general_log.info(f"[{state.log_prefix}] Resuming: Monitoring Thorchain tx {thor_txid}...")

        thorchain_completed = await self._monitor_thorchain_swap(thor_txid, state.check_id)
        if thorchain_completed:
            self.config_manager.general_log.info(
                f"[{state.log_prefix}] Resumed Thorchain tx {thor_txid} confirmed as successful.")
            state.delete()
        else:
            # This is the critical failure point where a refund occurs.
            exec_data = state_data.get('execution_data', {})
            refund_asset = exec_data.get('thorchain_from_token', 'UNKNOWN')
            refund_amount = exec_data.get('thorchain_swap_amount', 0)

            # 1. Create pause file to halt new trades immediately
            pause_reason = (
                f"Thorchain swap {thor_txid} for {refund_amount} {refund_asset} was refunded. "
                "Actively monitoring for fund return. All new trading is paused until refund is confirmed."
            )
            with open(self.pause_file_path, 'w') as f:
                json.dump({'reason': pause_reason, 'trade_details': state_data}, f, indent=4)

            self.config_manager.general_log.critical(f"[{state.log_prefix}] {pause_reason}")

            # 2. Update state to AWAITING_REFUND instead of archiving
            state.state_data = state_data
            # Add a specific timestamp for when the refund wait began
            state.save('AWAITING_REFUND', {'awaiting_refund_since': time.time()})

    async def _resume_from_awaiting_refund(self, state: TradeState, state_data: Dict[str, Any]):
        exec_data = state_data.get('execution_data', {})
        refund_asset = exec_data.get('thorchain_from_token', 'UNKNOWN')
        refund_amount = exec_data.get('thorchain_swap_amount')
        since_timestamp = state_data.get('awaiting_refund_since')

        self.config_manager.general_log.info(
            f"[{state.log_prefix}] Verifying return of {refund_amount} {refund_asset}...")

        async with aiohttp.ClientSession() as req_session:
            refund_confirmed = await self._verify_refund_received(refund_asset, refund_amount, state.check_id,
                                                                  since_timestamp, req_session)

        if refund_confirmed:
            self.config_manager.general_log.info(
                f"[{state.log_prefix}] Refund of {refund_amount} {refund_asset} confirmed in wallet.")

            # 1. Remove the pause file to resume trading
            if os.path.exists(self.pause_file_path):
                os.remove(self.pause_file_path)
                self.config_manager.general_log.info(f"[{state.log_prefix}] Trading pause has been lifted.")

            # 2. Archive the completed (refunded) trade state
            state.archive("refund-confirmed")
        else:
            self.config_manager.general_log.info(
                f"[{state.log_prefix}] Refund not yet confirmed. Will check again next cycle.")

    async def _verify_refund_received(self, token_symbol: str, expected_amount: float, check_id: str,
                                      since_timestamp: Optional[float] = None, session=None) -> bool:
        from definitions.rpc import rpc_call
        logger = self.config_manager.general_log
        log_prefix = check_id if self.test_mode else check_id[:8]

        coin_conf = self.config_manager.xbridge_manager.xbridge_conf.get(token_symbol)
        if not coin_conf:
            logger.error(f"[{log_prefix}] No RPC configuration for {token_symbol} to verify refund.")
            return False

        # Retry logic for RPC call to handle transient node issues
        max_retries = 3
        retry_delay = 5  # seconds
        for attempt in range(max_retries):
            try:
                transactions = await rpc_call(
                    method="listtransactions", params=["*", 500, 0], url=f"http://{coin_conf.get('ip', '127.0.0.1')}",
                    # Increased count for safety
                    rpc_user=coin_conf.get('username'), rpc_port=coin_conf.get('port'),
                    rpc_password=coin_conf.get('password'), logger=logger, session=session
                )
                if transactions is not None:
                    # If the call was successful, proceed with verification
                    amount_tolerance = 0.01  # 1% tolerance
                    min_amount = expected_amount * (1 - amount_tolerance)

                    for tx in reversed(transactions):
                        # If we have a timestamp, only consider transactions after that time.
                        if since_timestamp and tx.get('timereceived', 0) < since_timestamp:
                            continue

                        if tx.get('category') == 'receive' and tx.get('amount', 0) >= min_amount and not tx.get(
                                'abandoned', False):
                            logger.info(
                                f"[{log_prefix}] Found potential refund transaction: {tx.get('txid')} for {tx.get('amount')} {token_symbol}.")
                            return True
                    return False  # No refund found in the transaction list

                # If transactions is None, fall through to the retry logic
            except Exception as e:
                logger.warning(
                    f"[{log_prefix}] Attempt {attempt + 1}/{max_retries} failed for listtransactions: {e}. Retrying in {retry_delay}s...")

            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)

        logger.error(f"[{log_prefix}] Failed to verify refund for {token_symbol} after {max_retries} attempts.")
        return False

    async def _get_thorchain_decimals(self, chain_symbol: str) -> int:
        """
        Lazily fetches and caches the native decimal precision for all assets from Thorchain.
        Returns the decimal precision for the requested chain.
        """
        if not self.thorchain_asset_decimals:
            self.config_manager.general_log.info("Thorchain asset decimal cache is empty. Populating...")
            from definitions.thorchain_def import get_inbound_addresses
            inbound_addresses = await get_inbound_addresses(self.http_session, self.thor_api_url)
            if inbound_addresses:
                for asset in inbound_addresses:
                    if asset.get('chain') and asset.get('decimals') is not None:
                        self.thorchain_asset_decimals[asset.get('chain')] = int(asset.get('decimals'))
                self.config_manager.general_log.info(
                    f"Successfully cached decimal info for {len(self.thorchain_asset_decimals)} chains.")
            else:
                self.config_manager.general_log.error(
                    "Could not populate Thorchain asset decimal cache. Will use default of 8.")
                return 8
        # Return the cached value, or a default of 8 if the specific chain wasn't found.
        return self.thorchain_asset_decimals.get(chain_symbol, 8)

    async def _reevaluate_and_execute_thorchain(self, state: TradeState, state_data: Dict[str, Any]):
        """Helper to re-evaluate profitability and execute the Thorchain leg of a resumed trade."""
        check_id = state_data['check_id']
        exec_data = state_data['execution_data']
        xb_trade_id = state_data['xbridge_trade_id']

        from definitions.thorchain_def import get_thorchain_quote, execute_thorchain_swap
        log_prefix = check_id if self.test_mode else check_id[:8]
        try:
            thorchain_from_asset = f"{exec_data['thorchain_from_token']}.{exec_data['thorchain_from_token']}"
            thorchain_to_asset = f"{exec_data['thorchain_to_token']}.{exec_data['thorchain_to_token']}"
            new_quote = await get_thorchain_quote(
                from_asset=thorchain_from_asset, to_asset=thorchain_to_asset,
                amount=exec_data['thorchain_swap_amount'], session=self.http_session, quote_url=self.thor_quote_url
            )
            if not (new_quote and new_quote.get('expected_amount_out')):
                raise ValueError("Invalid new quote received during resumption.")

            gross_received = float(new_quote['expected_amount_out']) / (10 ** 8)
            outbound_fee = float(new_quote.get('fees', {}).get('outbound', '0')) / (10 ** 8)
            net_received = gross_received - outbound_fee
            cost_amount = exec_data['xbridge_from_amount']
            xbridge_fee = exec_data.get('xbridge_fee', 0)
            net_profit_amount = net_received - cost_amount - xbridge_fee
            is_still_profitable = (net_profit_amount > 0) and \
                                  ((net_profit_amount / cost_amount) > self.min_profit_margin) if cost_amount else False

            if is_still_profitable:
                self.config_manager.general_log.info(
                    f"[{log_prefix}] Resumed trade is still profitable. Proceeding with Thorchain swap.")

                from_token_symbol = exec_data['thorchain_from_token']
                # Get RPC credentials from the local xbridge.conf
                rpc_config = self.config_manager.xbridge_manager.xbridge_conf.get(from_token_symbol)
                if not rpc_config:
                    self.config_manager.general_log.error(
                        f"[{log_prefix}] Could not find RPC config for {from_token_symbol} in xbridge.conf. Aborting swap.")
                    return

                # Get native decimal precision from Thorchain endpoint
                decimal_places = await self._get_thorchain_decimals(from_token_symbol)

                thor_txid = await execute_thorchain_swap(
                    from_token_symbol=from_token_symbol, to_address=new_quote['inbound_address'],
                    amount=exec_data['thorchain_swap_amount'], memo=new_quote['memo'],
                    rpc_config=rpc_config,
                    decimal_places=decimal_places,
                    logger=self.config_manager.general_log,
                    test_mode=self.test_mode
                )
                if thor_txid:
                    state.state_data = state_data
                    state.save('THORCHAIN_INITIATED', {'thorchain_txid': thor_txid})
                    await self._resume_from_thor_initiated(state, state.state_data)
                else:
                    self.config_manager.general_log.critical(
                        f"[{log_prefix}] Resumed Thorchain swap FAILED to initiate. Manual intervention required.")
            else:
                self.config_manager.general_log.critical(
                    f"[{log_prefix}] ABORTING RESUMED TRADE. No longer profitable. "
                    f"XBridge trade {xb_trade_id} is complete, but Thorchain leg was not executed. "
                    f"MANUAL INTERVENTION REQUIRED to rebalance funds."
                )
                state.archive("resumed-unprofitable")
        except Exception as e:
            self.config_manager.general_log.error(
                f"[{log_prefix}] Error during re-evaluation of resumed trade: {e}. Manual intervention required.",
                exc_info=True)

    async def _monitor_with_polling(self, item_id: str, check_id: str,
                                    status_coro: Callable[[], Coroutine[Any, Any, str]], timeout: int,
                                    poll_interval: int, success_statuses: List[str], failure_statuses: List[str],
                                    entity_name: str) -> bool:
        """A generic monitoring function that polls for a status and handles timeouts."""
        log_prefix = check_id if self.test_mode else check_id[:8]
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                status = await status_coro()
                self.config_manager.general_log.info(
                    f"[{log_prefix}] Monitoring {entity_name} {item_id}: status is '{status}'.")

                if status in success_statuses:
                    return True
                if status in failure_statuses:
                    self.config_manager.general_log.error(
                        f"[{log_prefix}] {entity_name} {item_id} failed with status: {status}.")
                    return False
            except Exception as e:
                self.config_manager.general_log.warning(
                    f"[{log_prefix}] Error checking status for {entity_name} {item_id}: {e}. Retrying..."
                )
            await asyncio.sleep(poll_interval)

        self.config_manager.general_log.error(
            f"[{log_prefix}] Timed out waiting for {entity_name} {item_id} to complete.")
        return False

    async def _monitor_xbridge_order(self, order_id: str, check_id: str) -> bool:
        """Monitors an XBridge order until it reaches a terminal state."""
        log_prefix = check_id if self.test_mode else check_id[:8]
        if self.test_mode:
            self.config_manager.general_log.info(
                f"[{log_prefix}] [TEST MODE] Simulating successful XBridge order completion for {order_id}.")
            return True

        async def get_status():
            status_result = await self.config_manager.xbridge_manager.getorderstatus(order_id)
            return status_result.get('status')

        return await self._monitor_with_polling(
            item_id=order_id, check_id=check_id, status_coro=get_status,
            timeout=self.xb_monitor_timeout, poll_interval=self.xb_monitor_poll,
            success_statuses=['finished'],
            failure_statuses=['expired', 'canceled', 'invalid', 'rolled back', 'rollback failed', 'offline'],
            entity_name="XBridge order"
        )

    async def _monitor_thorchain_swap(self, txid: str, check_id: str) -> bool:
        """Monitors a Thorchain swap until it reaches a terminal state."""
        from definitions.thorchain_def import get_thorchain_tx_status
        log_prefix = check_id if self.test_mode else check_id[:8]
        if self.test_mode:
            self.config_manager.general_log.info(
                f"[{log_prefix}] [TEST MODE] Simulating successful Thorchain swap completion for {txid}.")
            return True

        async def get_status():
            return await get_thorchain_tx_status(txid, self.http_session, self.thor_tx_url)

        return await self._monitor_with_polling(
            item_id=txid, check_id=check_id, status_coro=get_status,
            timeout=self.thor_monitor_timeout, poll_interval=self.thor_monitor_poll,
            success_statuses=['success'], failure_statuses=['refunded'],
            entity_name="Thorchain swap"
        )

    async def thread_init_async_action(self, pair_instance: 'Pair'):
        pass

    def get_startup_tasks(self) -> list:
        """
        Arbitrage strategy has its own state recovery mechanism and should not
        blindly cancel all orders on startup.
        """
        return []
