from .maker_strategy import MakerStrategy


class BasicSellerStrategy(MakerStrategy):
    def __init__(self, config_manager, controller=None):
        super().__init__(config_manager, controller)
        # BasicSeller specific args are passed directly to initialize_strategy_specifics
        self.token_to_sell = None
        self.token_to_buy = None
        self.amount_token_to_sell = None
        self.min_sell_price_usd = None
        self.sell_price_offset = None
        self.partial_percent = None

    def initialize_strategy_specifics(self, token_to_sell=None, token_to_buy=None, amount_token_to_sell=None,
                                      min_sell_price_usd=None, sell_price_offset=None, partial_percent=None, **kwargs):
        self.token_to_sell = token_to_sell
        self.token_to_buy = token_to_buy
        self.amount_token_to_sell = amount_token_to_sell
        self.min_sell_price_usd = min_sell_price_usd
        self.sell_price_offset = sell_price_offset
        self.partial_percent = partial_percent

        self.config_manager.general_log.info("--- Basic Seller Strategy Parameters ---")
        self.config_manager.general_log.info(f"  - Token to Sell: {self.token_to_sell}")
        self.config_manager.general_log.info(f"  - Token to Buy: {self.token_to_buy}")
        if self.amount_token_to_sell is not None:
            self.config_manager.general_log.info(
                f"  - Amount to Sell: {self.amount_token_to_sell} {self.token_to_sell}")
        else:
            self.config_manager.general_log.info(f"  - Amount to Sell: None")
        if self.min_sell_price_usd is not None:
            self.config_manager.general_log.info(f"  - Minimum Sell Price: ${self.min_sell_price_usd:.4f} USD")
        else:
            self.config_manager.general_log.info(f"  - Minimum Sell Price: None")
        if self.sell_price_offset is not None:
            self.config_manager.general_log.info(f"  - Sell Price Upscale: {self.sell_price_offset * 100:.2f}%")
        else:
            self.config_manager.general_log.info(f"  - Sell Price Upscale: None")
        if self.partial_percent:
            self.config_manager.general_log.info(
                f"  - Partial Order Minimum Size: {self.partial_percent * 100:.1f}% of total")
        self.config_manager.general_log.info("---------------------------------------")

    def get_tokens_for_initialization(self, **kwargs) -> list:
        # If CLI args are provided, use them for backward compatibility.
        if kwargs.get('token_to_sell') and kwargs.get('token_to_buy'):
            return [kwargs['token_to_sell'], kwargs['token_to_buy']]

        # Otherwise, get tokens from the config file.
        return self.get_tokens_from_pair_configs(self.config_manager.config_basicseller.seller_configs)

    def get_pairs_for_initialization(self, tokens_dict, **kwargs) -> dict:
        from definitions.pair import Pair  # Import here to avoid circular dependency
        pairs = {}

        # Handle CLI mode for backward compatibility
        if kwargs.get('token_to_sell') and kwargs.get('token_to_buy'):
            token_to_sell = kwargs['token_to_sell']
            token_to_buy = kwargs['token_to_buy']
            # Create a unique name for the CLI-based pair
            pair_key = f"{token_to_sell}_{token_to_buy}_cli"
            pairs[pair_key] = Pair(
                token1=tokens_dict[token_to_sell],
                token2=tokens_dict[token_to_buy],
                cfg={'name': pair_key, 'enabled': True},
                strategy="basic_seller",
                amount_token_to_sell=kwargs.get('amount_token_to_sell'),
                min_sell_price_usd=kwargs.get('min_sell_price_usd'),
                sell_price_offset=kwargs.get('sell_price_offset'),
                partial_percent=kwargs.get('partial_percent'),
                config_manager=self.config_manager
            )
            return pairs

        # Handle config file mode (for GUI)
        enabled_sellers = [cfg for cfg in self.config_manager.config_basicseller.seller_configs if
                           cfg.get('enabled', True)]
        for cfg in enabled_sellers:
            t1, t2 = cfg['pair'].split("/")
            pair_name = cfg['name']
            pairs[pair_name] = Pair(
                token1=tokens_dict[t1],
                token2=tokens_dict[t2],
                cfg=cfg,
                strategy="basic_seller",
                amount_token_to_sell=cfg.get('amount_to_sell'),
                min_sell_price_usd=cfg.get('min_sell_price_usd'),
                sell_price_offset=cfg.get('sell_price_offset'),
                partial_percent=cfg.get('partial_percent'),
                config_manager=self.config_manager
            )
        return pairs


    def build_sell_order_details(self, dex_pair_instance, manual_dex_price=None) -> tuple:
        # BasicSeller specific logic for amount and offset for sell side
        amount = dex_pair_instance.pair.amount_token_to_sell
        offset = dex_pair_instance.pair.sell_price_offset
        return amount, offset

    def calculate_sell_price(self, dex_pair_instance, manual_dex_price=None) -> float:
        # BasicSeller sells at min_sell_price_usd if current price is lower
        if manual_dex_price:
            return manual_dex_price
        if dex_pair_instance.pair.min_sell_price_usd and dex_pair_instance.t1.cex.usd_price < dex_pair_instance.pair.min_sell_price_usd:
            return dex_pair_instance.pair.min_sell_price_usd / dex_pair_instance.t2.cex.usd_price
        return dex_pair_instance.pair.cex.price

    def build_buy_order_details(self, dex_pair_instance, manual_dex_price=None) -> tuple:
        # BasicSeller does not build buy orders
        self.config_manager.general_log.error(
            f"Bot strategy is basic_seller, no rule for this strat on build_buy_order_details")
        return 0, 0

    def determine_buy_price(self, dex_pair_instance, manual_dex_price=None) -> float:
        # BasicSeller does not determine buy prices
        self.config_manager.general_log.error(
            f"Bot strategy is basic_seller, no rule for this strat on determine_buy_price")
        return 0

    def get_price_variation_tolerance(self, dex_pair_instance) -> float:
        return dex_pair_instance.PRICE_VARIATION_TOLERANCE_DEFAULT

    def calculate_variation_based_on_side(self, dex_pair_instance, current_order_side: str, cex_price: float,
                                          original_price: float) -> float:
        # BasicSeller specific default variation logic
        if dex_pair_instance.pair.min_sell_price_usd and dex_pair_instance.t1.cex.usd_price < dex_pair_instance.pair.min_sell_price_usd:
            return (dex_pair_instance.pair.min_sell_price_usd / dex_pair_instance.t2.cex.usd_price) / original_price
        return float(cex_price / original_price)

    def init_virtual_order_logic(self, dex_pair_instance, order_history: dict):
        # BasicSeller always creates a sell order
        dex_pair_instance.create_virtual_sell_order()

    def handle_order_status_error(self, dex_pair_instance):
        dex_pair_instance.order = None  # Reset order to try creating a new one

    async def reinit_virtual_order_after_price_variation(self, dex_pair_instance, disabled_coins: list):
        dex_pair_instance.create_virtual_sell_order()
        if dex_pair_instance.order is None:
            await dex_pair_instance.create_order(dry_mode=False)

    async def handle_finished_order(self, dex_pair_instance, disabled_coins: list):
        self.config_manager.general_log.info(
            f"Sell order for '{dex_pair_instance.pair.name}' completed successfully. Disabling instance.")
        dex_pair_instance.disabled = True  # Mark this seller instance as complete.

    async def handle_error_swap_status(self, dex_pair_instance):
        self.config_manager.general_log.error(
            f"Order Error:\n{dex_pair_instance.current_order}\n{dex_pair_instance.order}")
        dex_pair_instance.disabled = True  # Disable pair on error

    async def thread_init_async_action(self, pair_instance):
        pair_instance.dex.init_virtual_order(self.controller.disabled_coins)
        await pair_instance.dex.create_order()

    async def thread_loop_async_action(self, pair_instance):
        await pair_instance.dex.status_check(self.controller.disabled_coins)

    def should_update_cex_prices(self) -> bool:
        return True

    def get_operation_interval(self) -> int:
        return 15
