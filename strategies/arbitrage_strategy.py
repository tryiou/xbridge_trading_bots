import uuid
from itertools import combinations

from strategies.base_strategy import BaseStrategy


class ArbitrageStrategy(BaseStrategy):

    def __init__(self, config_manager, controller=None):
        super().__init__(config_manager, controller)
        # Initialize with default values; these will be set by initialize_strategy_specifics
        self.min_profit_margin = 0.01
        self.dry_mode = True
        self.http_session = None  # Will be set by ConfigManager

    def initialize_strategy_specifics(self, dry_mode: bool = True, min_profit_margin: float = 0.01, **kwargs):
        self.dry_mode = dry_mode
        self.min_profit_margin = min_profit_margin
        self.config_manager.general_log.info(
            f"ArbitrageStrategy initialized. Dry mode: {self.dry_mode}, Min profit: {self.min_profit_margin * 100:.2f}%")

    def get_tokens_for_initialization(self, **kwargs) -> list:
        # Define the tokens needed for arbitrage as per the proposal
        return ['LTC', 'DOGE']  # ,'BTC', 'DASH' disabled for testing

    def get_pairs_for_initialization(self, tokens_dict, **kwargs) -> dict:
        from definitions.pair import Pair
        pairs = {}
        # Create all permutations of the available tokens
        # for t1_sym, t2_sym in permutations(self.get_tokens_for_initialization(), 2):
        for t1_sym, t2_sym in combinations(self.get_tokens_for_initialization(), 2):
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
        leg1_result = await self.check_arb_leg_xb_sell_thor_buy(pair_instance, xbridge_bids, check_id)
        leg2_result = await self.check_arb_leg_xb_buy_thor_sell(pair_instance, xbridge_asks, check_id)

        # 3. Log a comprehensive report at DEBUG level
        report_lines = [f"\nArbitrage Report [{check_id}] for {pair_instance.symbol}:"]
        if leg1_result:
            report_lines.append(leg1_result['report'])
        if leg2_result:
            report_lines.append(leg2_result['report'])
        if len(report_lines) > 1:  # Only log if there's something to report
            self.config_manager.general_log.debug("\n".join(report_lines))

        # 4. Log any profitable opportunities at INFO level and execute if not in dry mode
        if leg1_result and leg1_result['profitable']:
            self.config_manager.general_log.info(f"[{check_id}] {leg1_result['opportunity_details']}")
            if not self.dry_mode:
                self.config_manager.general_log.info(
                    f"[{check_id}] EXECUTING LIVE ARBITRAGE for {pair_instance.symbol} (Leg 1)...")
                # TODO: Implement actual trade execution
            else:
                self.config_manager.general_log.info(
                    f"[{check_id}] [DRY RUN] Would execute arbitrage for {pair_instance.symbol} (Leg 1).")

        if leg2_result and leg2_result['profitable']:
            self.config_manager.general_log.info(f"[{check_id}] {leg2_result['opportunity_details']}")
            if not self.dry_mode:
                self.config_manager.general_log.info(
                    f"[{check_id}] EXECUTING LIVE ARBITRAGE for {pair_instance.symbol} (Leg 2)...")
                # TODO: Implement actual trade execution
            else:
                self.config_manager.general_log.info(
                    f"[{check_id}] [DRY RUN] Would execute arbitrage for {pair_instance.symbol} (Leg 2).")

        self.config_manager.general_log.info(f"[{check_id}] Finished check for {pair_instance.symbol}.")

    async def check_arb_leg_xb_sell_thor_buy(self, pair_instance, xbridge_bids, check_id):
        """ Arbitrage Leg: Sell on XBridge (by hitting a bid), Buy on Thorchain. """
        if not xbridge_bids:
            return None

        best_xbridge_bid_price = float(xbridge_bids[0][0])
        best_xbridge_bid_amount = float(xbridge_bids[0][1])
        amount_t2_from_xb_sell = best_xbridge_bid_amount * best_xbridge_bid_price

        from definitions.thorchain_def import get_thorchain_quote  # Import locally to avoid circular dependency issues
        try:
            thorchain_buy_quote = await get_thorchain_quote(
                from_asset=f"{pair_instance.t2.symbol}.{pair_instance.t2.symbol}",
                to_asset=f"{pair_instance.t1.symbol}.{pair_instance.t1.symbol}",
                amount=amount_t2_from_xb_sell,
                session=self.http_session
            )
        except Exception as e:
            self.config_manager.general_log.error(
                f"[{check_id}] Exception during Thorchain quote fetch for {pair_instance.symbol} (Sell->Buy): {e}",
                exc_info=True)
            return None

        if not (thorchain_buy_quote and thorchain_buy_quote.get('expected_amount_out')):
            self.config_manager.general_log.debug(
                f"[{check_id}] Thorchain quote was invalid for {pair_instance.symbol} (Sell->Buy).")
            return None

        thorchain_received_t1 = float(thorchain_buy_quote['expected_amount_out']) / (10 ** 8)
        profit_t1_amount = thorchain_received_t1 - best_xbridge_bid_amount
        profit_t1_ratio = (profit_t1_amount / best_xbridge_bid_amount) * 100 if best_xbridge_bid_amount else 0
        is_profitable = (profit_t1_ratio / 100) > self.min_profit_margin

        leg_header = f"  Leg 1: Sell {pair_instance.t1.symbol} on XBridge -> Buy {pair_instance.t1.symbol} on Thorchain"
        report = (
            f"{leg_header}\n"
            f"    - XBridge Trade:   Sell {best_xbridge_bid_amount:.8f} {pair_instance.t1.symbol} -> Receive {amount_t2_from_xb_sell:.8f} {pair_instance.t2.symbol} (at {best_xbridge_bid_price:.8f} {pair_instance.t2.symbol}/{pair_instance.t1.symbol})\n"
            f"    - Thorchain Trade: Sell {amount_t2_from_xb_sell:.8f} {pair_instance.t2.symbol} -> Receive {thorchain_received_t1:.8f} {pair_instance.t1.symbol}\n"
            f"    - Result:          Profit = {profit_t1_ratio:.2f}% ({profit_t1_amount:+.8f} {pair_instance.t1.symbol})"
        )

        opportunity_details = None
        if is_profitable:
            short_header = f"Sell {pair_instance.t1.symbol} on XBridge -> Buy on Thorchain"
            opportunity_details = (
                f"Arbitrage Found ({short_header}): "
                f"Profit: {profit_t1_ratio:.2f}% on {pair_instance.symbol}."
            )

        return {
            'report': report,
            'profitable': is_profitable,
            'opportunity_details': opportunity_details
        }

    async def check_arb_leg_xb_buy_thor_sell(self, pair_instance, xbridge_asks, check_id):
        """ Arbitrage Leg: Buy on XBridge (by hitting an ask), Sell on Thorchain. """
        if not xbridge_asks:
            return None

        best_xbridge_ask_price = float(xbridge_asks[0][0])
        best_xbridge_ask_amount = float(xbridge_asks[0][1])
        xbridge_cost_t2 = best_xbridge_ask_amount * best_xbridge_ask_price

        from definitions.thorchain_def import get_thorchain_quote  # Import locally to avoid circular dependency issues
        try:
            thorchain_sell_quote = await get_thorchain_quote(
                from_asset=f"{pair_instance.t1.symbol}.{pair_instance.t1.symbol}",
                to_asset=f"{pair_instance.t2.symbol}.{pair_instance.t2.symbol}",
                amount=best_xbridge_ask_amount,
                session=self.http_session
            )
        except Exception as e:
            self.config_manager.general_log.error(
                f"[{check_id}] Exception during Thorchain quote fetch for {pair_instance.symbol} (Buy->Sell): {e}",
                exc_info=True)
            return None

        if not (thorchain_sell_quote and thorchain_sell_quote.get('expected_amount_out')):
            self.config_manager.general_log.debug(
                f"[{check_id}] Thorchain quote was invalid for {pair_instance.symbol} (Buy->Sell).")
            return None

        thorchain_received_t2 = float(thorchain_sell_quote['expected_amount_out']) / (10 ** 8)
        profit_t2_amount = thorchain_received_t2 - xbridge_cost_t2
        profit_t2_ratio = (profit_t2_amount / xbridge_cost_t2) * 100 if xbridge_cost_t2 else 0
        is_profitable = (profit_t2_ratio / 100) > self.min_profit_margin

        leg_header = f"  Leg 2: Buy {pair_instance.t1.symbol} on XBridge -> Sell {pair_instance.t1.symbol} on Thorchain"
        report = (
            f"{leg_header}\n"
            f"    - XBridge Trade:   Sell {xbridge_cost_t2:.8f} {pair_instance.t2.symbol} -> Receive {best_xbridge_ask_amount:.8f} {pair_instance.t1.symbol} (at {best_xbridge_ask_price:.8f} {pair_instance.t2.symbol}/{pair_instance.t1.symbol})\n"
            f"    - Thorchain Trade: Sell {best_xbridge_ask_amount:.8f} {pair_instance.t1.symbol} -> Receive {thorchain_received_t2:.8f} {pair_instance.t2.symbol}\n"
            f"    - Result:          Profit = {profit_t2_ratio:.2f}% ({profit_t2_amount:+.8f} {pair_instance.t2.symbol})"
        )

        opportunity_details = None
        if is_profitable:
            short_header = f"Buy {pair_instance.t1.symbol} on XBridge -> Sell on Thorchain"
            opportunity_details = (
                f"Arbitrage Found ({short_header}): "
                f"Profit: {profit_t2_ratio:.2f}% on {pair_instance.symbol}."
            )

        return {
            'report': report,
            'profitable': is_profitable,
            'opportunity_details': opportunity_details
        }

    # --- Stub out unused abstract methods from BaseStrategy ---
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
