from .maker_strategy import MakerStrategy


class BasicSellerStrategy(MakerStrategy):
    def __init__(self, config_manager, controller=None):
        super().__init__(config_manager, controller)
        self.token_to_sell = None
        self.token_to_buy = None
        self.amount_token_to_sell = None
        self.min_sell_price_usd = None
        self.sell_price_offset = None
        self.partial_percent = None

    def initialize_strategy_specifics(
        self,
        token_to_sell=None,
        token_to_buy=None,
        amount_token_to_sell=None,
        min_sell_price_usd=None,
        sell_price_offset=None,
        partial_percent=None,
        **kwargs,
    ):
        self.token_to_sell = token_to_sell
        self.token_to_buy = token_to_buy
        self.amount_token_to_sell = amount_token_to_sell
        self.min_sell_price_usd = min_sell_price_usd
        self.sell_price_offset = sell_price_offset
        self.partial_percent = partial_percent

        self.config_manager.general_log.info("--- Basic Seller Strategy Parameters ---")
        self.config_manager.general_log.info(
            "  - Token to Sell: %s", self.token_to_sell
        )
        self.config_manager.general_log.info("  - Token to Buy: %s", self.token_to_buy)

        if self.amount_token_to_sell is not None:
            self.config_manager.general_log.info(
                "  - Amount to Sell: %s %s",
                self.amount_token_to_sell,
                self.token_to_sell,
            )
        else:
            self.config_manager.general_log.info("  - Amount to Sell: None")

        if self.min_sell_price_usd is not None:
            self.config_manager.general_log.info(
                "  - Minimum Sell Price: $%.4f USD", self.min_sell_price_usd
            )
        else:
            self.config_manager.general_log.info("  - Minimum Sell Price: None")

        if self.sell_price_offset is not None:
            self.config_manager.general_log.info(
                "  - Sell Price Upscale: %.2f%%", self.sell_price_offset * 100
            )
        else:
            self.config_manager.general_log.info("  - Sell Price Upscale: None")

        if self.partial_percent:
            self.config_manager.general_log.info(
                "  - Partial Order Minimum Size: %.1f%% of total",
                self.partial_percent * 100,
            )
        self.config_manager.general_log.info("---------------------------------------")

    def get_tokens_for_initialization(self, **kwargs) -> list:
        if kwargs.get("token_to_sell") and kwargs.get("token_to_buy"):
            return [kwargs["token_to_sell"], kwargs["token_to_buy"]]
        return self.get_tokens_from_pair_configs(
            self.config_manager.config_basicseller.seller_configs
        )

    def get_pairs_for_initialization(self, tokens_dict, **kwargs) -> dict:
        from definitions.pair import Pair

        if kwargs.get("token_to_sell") and kwargs.get("token_to_buy"):
            token_to_sell = kwargs["token_to_sell"]
            token_to_buy = kwargs["token_to_buy"]
            pair_key = f"{token_to_sell}_{token_to_buy}_cli"
            return {
                pair_key: Pair(
                    token1=tokens_dict[token_to_sell],
                    token2=tokens_dict[token_to_buy],
                    cfg={"name": pair_key, "enabled": True},
                    strategy="basic_seller",
                    amount_token_to_sell=kwargs.get("amount_token_to_sell"),
                    min_sell_price_usd=kwargs.get("min_sell_price_usd"),
                    sell_price_offset=kwargs.get("sell_price_offset"),
                    partial_percent=kwargs.get("partial_percent"),
                    config_manager=self.config_manager,
                )
            }

        return self._create_pairs_from_configs(
            self.config_manager.config_basicseller.seller_configs,
            tokens_dict,
            "basic_seller",
            **kwargs,
        )

    def build_sell_order_details(self, dex_pair, manual_dex_price=None) -> tuple:
        amount = dex_pair.pair.amount_token_to_sell
        offset = dex_pair.pair.sell_price_offset
        return amount, offset

    def calculate_sell_price(self, dex_pair, manual_dex_price=None) -> float:
        if manual_dex_price:
            return manual_dex_price
        if (
            dex_pair.pair.min_sell_price_usd
            and dex_pair.t1.cex.usd_price < dex_pair.pair.min_sell_price_usd
        ):
            return dex_pair.pair.min_sell_price_usd / dex_pair.t2.cex.usd_price
        return dex_pair.pair.cex.price

    def build_buy_order_details(self, dex_pair, manual_dex_price=None) -> tuple:
        raise RuntimeError(
            "BasicSeller is a sell-only strategy — buy orders are not supported"
        )

    def determine_buy_price(self, dex_pair, manual_dex_price=None) -> float:
        raise RuntimeError(
            "BasicSeller is a sell-only strategy — buy orders are not supported"
        )

    def get_price_variation_tolerance(self, dex_pair) -> float:
        return dex_pair.PRICE_VARIATION_TOLERANCE_DEFAULT

    def calculate_variation_based_on_side(
        self, dex_pair, current_order_side: str, cex_price: float, original_price: float
    ) -> float:
        if (
            dex_pair.pair.min_sell_price_usd
            and dex_pair.t1.cex.usd_price < dex_pair.pair.min_sell_price_usd
        ):
            return (
                dex_pair.pair.min_sell_price_usd / dex_pair.t2.cex.usd_price
            ) / original_price
        return float(cex_price / original_price)

    def init_virtual_order_logic(self, dex_pair, order_history: dict):
        dex_pair.create_virtual_sell_order()

    async def handle_order_status_error(self, dex_pair):
        dex_pair.order = None

    async def reinit_virtual_order_after_price_variation(self, dex_pair):
        dex_pair.create_virtual_sell_order()
        if dex_pair.order is None:
            await dex_pair.create_order(dry_mode=False)

    async def handle_finished_order(self, dex_pair):
        self.config_manager.general_log.info(
            "Sell order for '%s' completed successfully. Disabling instance.",
            dex_pair.pair.name,
        )
        dex_pair.disabled = True

    async def handle_error_swap_status(self, dex_pair):
        self.config_manager.general_log.error(
            "Order Error:\n%s\n%s", dex_pair.current_order, dex_pair.order
        )
        dex_pair.disabled = True
