from .maker_strategy import MakerStrategy


class PingPongStrategy(MakerStrategy):
    def __init__(self, config_manager, controller=None):
        super().__init__(config_manager, controller)
        self.config_pingpong = config_manager.config_pingpong  # Direct access to pingpong config

    def initialize_strategy_specifics(self, **kwargs):
        # PingPong doesn't need specific args passed from CLI, its config is loaded
        self.config_manager.general_log.info("--- PingPong Strategy Parameters ---")
        enabled_pairs = [p for p in self.config_pingpong.pair_configs if p.get('enabled', True)]
        if not enabled_pairs:
            self.config_manager.general_log.info("  - No enabled pairs found in config_pingpong.yaml.")
        else:
            self.config_manager.general_log.info(f"  - Found {len(enabled_pairs)} enabled pair(s):")
            for pair_cfg in enabled_pairs:
                self.config_manager.general_log.info(
                    f"    - {pair_cfg['name']} ({pair_cfg['pair']}): "
                    f"USD Amount=${pair_cfg.get('usd_amount', 0.0):.2f}, "
                    f"Spread={pair_cfg.get('spread', 0.0) * 100:.2f}%, "
                    f"Sell Offset={pair_cfg.get('sell_price_offset', 0.0) * 100:.2f}%"
                )
        self.config_manager.general_log.info("------------------------------------")

    def get_tokens_for_initialization(self, **kwargs) -> list:
        return self.get_tokens_from_pair_configs(self.config_pingpong.pair_configs)

    def get_pairs_for_initialization(self, tokens_dict, **kwargs) -> dict:
        from definitions.pair import Pair  # Import here to avoid circular dependency
        pairs = {}
        enabled_pairs = [cfg for cfg in self.config_pingpong.pair_configs if cfg.get('enabled', True)]
        for cfg in enabled_pairs:
            t1, t2 = cfg['pair'].split("/")
            pair_name = f"{cfg['name']}"
            pairs[pair_name] = Pair(
                token1=tokens_dict[t1],
                token2=tokens_dict[t2],
                cfg=cfg,
                strategy="pingpong",
                dex_enabled=True,
                partial_percent=None,
                config_manager=self.config_manager
            )
        return pairs

    def build_sell_order_details(self, dex_pair_instance) -> tuple:
        # PingPong specific logic for amount and offset for sell side
        usd_amount = dex_pair_instance.pair.cfg['usd_amount']
        btc_usd_price = self.config_manager.tokens['BTC'].cex.usd_price
        t1_cex_price = dex_pair_instance.t1.cex.cex_price

        # Add robustness to prevent division by zero or TypeError if prices are not available
        if not all([btc_usd_price, t1_cex_price, btc_usd_price > 0, t1_cex_price > 0]):
            self.config_manager.general_log.warning(
                f"Cannot calculate sell amount for {dex_pair_instance.pair.name} due to missing or zero CEX price. "
                f"BTC/USD: {btc_usd_price}, {dex_pair_instance.t1.symbol}/BTC: {t1_cex_price}"
            )
            amount = 0
        else:
            amount = (usd_amount / btc_usd_price) / t1_cex_price

        offset = dex_pair_instance.pair.cfg.get('sell_price_offset', 0.05)
        return amount, offset

    def calculate_sell_price(self, dex_pair_instance) -> float:
        # Sell price is always based on the current live CEX price.
        # The offset is applied in _build_sell_order in pair.py
        return dex_pair_instance.pair.cex.price

    def build_buy_order_details(self, dex_pair_instance) -> tuple:
        # PingPong specific logic for amount and spread for buy side
        amount = float(dex_pair_instance.order_history['maker_size'])
        spread = dex_pair_instance.pair.cfg.get('spread')
        return amount, spread

    def determine_buy_price(self, dex_pair_instance) -> float:
        """
        Determines the base price for a BUY order.
        The logic is to take the minimum of the current live price and the
        price of the last completed SELL order. This ensures the bot never
        buys back higher than it sold, and takes advantage of price drops.
        """
        live_cex_price = dex_pair_instance.pair.cex.price
        last_sell_price = dex_pair_instance.order_history.get('dex_price')

        if not last_sell_price:
            # This should not happen in a BUY state, but as a fallback, use live price.
            self.config_manager.general_log.warning(
                f"Could not find 'dex_price' in order history for {dex_pair_instance.pair.name}. "
                f"Defaulting BUY price to live CEX price."
            )
            return live_cex_price

        # The core logic: never buy higher than the last sell.
        base_price = min(live_cex_price, float(last_sell_price))
        self.config_manager.general_log.debug(
            f"Determined BUY base price for {dex_pair_instance.pair.name}: "
            f"min(live: {live_cex_price:.8f}, last_sell: {float(last_sell_price):.8f}) -> {base_price:.8f}"
        )
        return base_price

    def get_price_variation_tolerance(self, dex_pair_instance) -> float:
        return dex_pair_instance.pair.cfg.get('price_variation_tolerance')

    def calculate_variation_based_on_side(self, dex_pair_instance, current_order_side: str, cex_price: float,
                                          original_price: float) -> tuple[float, bool]:
        """
        Calculates price variation and determines if the order should be price-locked.
        Returns:
            A tuple (variation: float, is_locked: bool).
        """
        variation = float(cex_price / original_price)

        if current_order_side == 'BUY':
            last_sell_price = dex_pair_instance.order_history.get('dex_price')
            if last_sell_price and cex_price > float(last_sell_price):
                self.config_manager.general_log.debug(
                    f"BUY order for {dex_pair_instance.pair.name} is price locked. "
                    f"Live price ({cex_price:.8f}) is above last sell price ({float(last_sell_price):.8f})."
                )
                return variation, True  # Signal a lock

        return variation, False

    def init_virtual_order_logic(self, dex_pair_instance, order_history: dict):
        if not order_history or ('side' in order_history and order_history['side'] == 'BUY'):
            dex_pair_instance.create_virtual_sell_order()
        elif 'side' in order_history and order_history['side'] == 'SELL':
            dex_pair_instance.create_virtual_buy_order()
        else:
            self.config_manager.general_log.critical(
                f"Fatal error during init_order: Unexpected order history state\n{order_history}")
            raise SystemExit(1)  # Raise an exception to allow for graceful shutdown

    def handle_order_status_error(self, dex_pair_instance):
        dex_pair_instance.order = None  # Reset order to try creating a new one

    async def reinit_virtual_order_after_price_variation(self, dex_pair_instance, disabled_coins: list):
        dex_pair_instance.init_virtual_order(disabled_coins)
        if not dex_pair_instance.order:
            await dex_pair_instance.create_order()

    async def handle_finished_order(self, dex_pair_instance, disabled_coins: list):
        dex_pair_instance.init_virtual_order(disabled_coins)
        await dex_pair_instance.create_order()

    async def handle_error_swap_status(self, dex_pair_instance):
        self.config_manager.general_log.error(
            f"Order Error:\n{dex_pair_instance.current_order}\n{dex_pair_instance.order}")
        self.config_manager.general_log.warning(f"Disabling pair {dex_pair_instance.symbol} due to order error.")
        dex_pair_instance.disabled = True  # Disable the pair instead of stopping the whole bot.

    async def thread_init_async_action(self, pair_instance):
        pair_instance.dex.init_virtual_order(self.controller.disabled_coins)
        await pair_instance.dex.create_order()

    async def thread_loop_async_action(self, pair_instance):
        await pair_instance.dex.status_check(self.controller.disabled_coins)

    def should_update_cex_prices(self) -> bool:
        return True

    def get_operation_interval(self) -> int:
        return 15
