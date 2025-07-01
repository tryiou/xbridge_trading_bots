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
        self.config_manager.general_log.info(f"  - Amount to Sell: {self.amount_token_to_sell} {self.token_to_sell}")
        self.config_manager.general_log.info(f"  - Minimum Sell Price: ${self.min_sell_price_usd:.4f} USD")
        self.config_manager.general_log.info(f"  - Sell Price Upscale: {self.sell_price_offset * 100:.2f}%")
        if self.partial_percent:
            self.config_manager.general_log.info(
                f"  - Partial Order Minimum Size: {self.partial_percent * 100:.1f}% of total")
        self.config_manager.general_log.info("---------------------------------------")

    def get_tokens_for_initialization(self, **kwargs) -> list:
        # These come from CLI args
        token_to_sell = kwargs.get('token_to_sell')
        token_to_buy = kwargs.get('token_to_buy')
        if token_to_sell is None or token_to_buy is None:
            raise ValueError("TokenToSell and TokenToBuy must be provided for BasicSeller strategy.")
        return [token_to_sell, token_to_buy]

    def get_pairs_for_initialization(self, tokens_dict, **kwargs) -> dict:
        from definitions.pair import Pair  # Import here to avoid circular dependency
        pairs = {}
        token_to_sell = kwargs.get('token_to_sell')
        token_to_buy = kwargs.get('token_to_buy')
        amount_token_to_sell = kwargs.get('amount_token_to_sell')
        min_sell_price_usd = kwargs.get('min_sell_price_usd')
        sell_price_offset = kwargs.get('sell_price_offset')
        partial_percent = kwargs.get('partial_percent')

        if token_to_sell is None or token_to_buy is None:
            raise ValueError("Need at least two tokens for basic_seller strategy")

        pair_key = f"{token_to_sell}/{token_to_buy}"

        pairs[pair_key] = Pair(
            token1=tokens_dict[token_to_sell],
            token2=tokens_dict[token_to_buy],
            cfg={'name': "basic_seller"},  # Basic seller doesn't use a config file for pairs
            strategy="basic_seller",
            amount_token_to_sell=amount_token_to_sell,
            min_sell_price_usd=min_sell_price_usd,
            sell_price_offset=sell_price_offset,
            partial_percent=partial_percent,
            config_manager=self.config_manager
        )
        return pairs

    def get_dex_history_file_path(self, pair_name: str) -> str:
        unique_id = pair_name.replace("/", "_")
        return f"{self.config_manager.ROOT_DIR}/data/basic_seller_{unique_id}_last_order.yaml"

    def get_dex_token_address_file_path(self, token_symbol: str) -> str:
        return f"{self.config_manager.ROOT_DIR}/data/basic_seller_{token_symbol}_addr.yaml"

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
        self.config_manager.general_log.info('Order sold, signaling bot termination.')
        if self.controller:
            self.controller.shutdown_event.set()

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
