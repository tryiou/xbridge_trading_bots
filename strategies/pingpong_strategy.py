from .maker_strategy import MakerStrategy


class PingPongStrategy(MakerStrategy):
    def __init__(self, config_manager, controller=None):
        super().__init__(config_manager, controller)
        self.config_pingpong = config_manager.config_pingpong

    def initialize_strategy_specifics(self, **kwargs):
        self.config_manager.general_log.info("--- PingPong Strategy Parameters ---")
        enabled_pairs = [p for p in self.config_pingpong.pair_configs if p.get('enabled', True)]
        if not enabled_pairs:
            self.config_manager.general_log.info("  - No enabled pairs found in config_pingpong.yaml.")
        else:
            self.config_manager.general_log.info("  - Found %d enabled pair(s):", len(enabled_pairs))
            for pair_cfg in enabled_pairs:
                self.config_manager.general_log.info(
                    "    - %s (%s): USD Amount=$%.2f, Spread=%.2f%%, Sell Offset=%.2f%%",
                    pair_cfg['name'], pair_cfg['pair'], pair_cfg.get('usd_amount', 0.0),
                    pair_cfg.get('spread', 0.0) * 100, pair_cfg.get('sell_price_offset', 0.0) * 100
                )
        self.config_manager.general_log.info("------------------------------------")

    def get_tokens_for_initialization(self, **kwargs) -> list:
        return self.get_tokens_from_pair_configs(self.config_pingpong.pair_configs)

    def get_pairs_for_initialization(self, tokens_dict, **kwargs) -> dict:
        return self._create_pairs_from_configs(self.config_pingpong.pair_configs, tokens_dict, "pingpong", **kwargs)

    def build_sell_order_details(self, dex_pair, manual_dex_price=None) -> tuple:
        usd_amount = dex_pair.pair.cfg['usd_amount']
        btc_usd_price = self.config_manager.tokens['BTC'].cex.usd_price
        t1_cex_price = dex_pair.t1.cex.cex_price

        if not all([btc_usd_price, t1_cex_price, btc_usd_price > 0, t1_cex_price > 0]):
            self.config_manager.general_log.warning(
                "Cannot calculate sell amount for %s due to missing or zero CEX price. BTC/USD: %s, %s/BTC: %s",
                dex_pair.pair.name, btc_usd_price, dex_pair.t1.symbol, t1_cex_price
            )
            amount = 0
        else:
            amount = (usd_amount / btc_usd_price) / t1_cex_price

        offset = dex_pair.pair.cfg.get('sell_price_offset', 0.05)
        return amount, offset

    def calculate_sell_price(self, dex_pair, manual_dex_price=None) -> float:
        return dex_pair.pair.cex.price

    def build_buy_order_details(self, dex_pair, manual_dex_price=None) -> tuple:
        amount = float(dex_pair.order_history['maker_size'])
        spread = dex_pair.pair.cfg.get('spread')
        return amount, spread

    def determine_buy_price(self, dex_pair, manual_dex_price=None) -> float:
        live_cex_price = dex_pair.pair.cex.price
        last_sell_price = dex_pair.order_history.get('dex_price')

        if not last_sell_price:
            self.config_manager.general_log.warning(
                "Could not find 'dex_price' in order history for %s. Defaulting BUY price to live CEX price.",
                dex_pair.pair.name
            )
            return live_cex_price

        base_price = min(live_cex_price, float(last_sell_price))
        self.config_manager.general_log.debug(
            "Determined BUY base price for %s: min(live: %.8f, last_sell: %.8f) -> %.8f",
            dex_pair.pair.name, live_cex_price, float(last_sell_price), base_price
        )
        return base_price

    def get_price_variation_tolerance(self, dex_pair) -> float:
        return dex_pair.pair.cfg.get('price_variation_tolerance')

    def calculate_variation_based_on_side(self, dex_pair, current_order_side: str, cex_price: float,
                                          original_price: float) -> tuple[float, bool]:
        variation = float(cex_price / original_price)

        if current_order_side == 'BUY':
            last_sell_price = dex_pair.order_history.get('dex_price')
            if last_sell_price and cex_price > float(last_sell_price):
                self.config_manager.general_log.debug(
                    "BUY order for %s is price locked. Live price (%.8f) is above last sell price (%.8f).",
                    dex_pair.pair.name, cex_price, float(last_sell_price)
                )
                return variation, True

        return variation, False

    def init_virtual_order_logic(self, dex_pair, order_history: dict):
        if not order_history or ('side' in order_history and order_history['side'] == 'BUY'):
            dex_pair.create_virtual_sell_order()
        elif 'side' in order_history and order_history['side'] == 'SELL':
            dex_pair.create_virtual_buy_order()
        else:
            self.config_manager.general_log.critical("Fatal error during init_order: Unexpected order history state\n%s", order_history)
            raise SystemExit(1)

    async def handle_order_status_error(self, dex_pair):
        dex_pair.order = None

    async def reinit_virtual_order_after_price_variation(self, dex_pair, disabled_coins: list):
        dex_pair.init_virtual_order(disabled_coins)
        if not dex_pair.order:
            await dex_pair.create_order()

    async def handle_finished_order(self, dex_pair, disabled_coins: list):
        dex_pair.init_virtual_order(disabled_coins)
        await dex_pair.create_order()

    async def handle_error_swap_status(self, dex_pair):
        self.config_manager.general_log.error("Order Error:\n%s\n%s", dex_pair.current_order, dex_pair.order)
        self.config_manager.general_log.warning("Disabling pair %s due to order error.", dex_pair.symbol)
        dex_pair.disabled = True

    async def thread_init_async_action(self, pair_instance):
        pair_instance.dex.init_virtual_order(self.controller.disabled_coins)
        await pair_instance.dex.create_order()

    async def process_pair_async(self, pair_instance):
        await pair_instance.dex.status_check(self.controller.disabled_coins)

    def should_update_cex_prices(self) -> bool:
        return True

    def get_operation_interval(self) -> int:
        return 15