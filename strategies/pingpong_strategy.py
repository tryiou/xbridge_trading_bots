from strategies.base_strategy import BaseStrategy


class PingPongStrategy(BaseStrategy):
    def __init__(self, config_manager, controller=None):
        super().__init__(config_manager, controller)
        self.config_pingppong = config_manager.config_pingppong  # Direct access to pingpong config

    def initialize_strategy_specifics(self, **kwargs):
        # PingPong doesn't need specific args passed from CLI, its config is loaded
        pass

    def get_tokens_for_initialization(self, **kwargs) -> list:
        tokens_list = [cfg['pair'].split("/")[0] for cfg in self.config_pingppong.pair_configs if cfg.get('enabled', True)]
        tokens_list.extend(
            [cfg['pair'].split("/")[1] for cfg in self.config_pingppong.pair_configs if cfg.get('enabled', True)])
        return list(set(tokens_list))

    def get_pairs_for_initialization(self, tokens_dict, **kwargs) -> dict:
        from definitions.pair import Pair  # Import here to avoid circular dependency
        pairs = {}
        enabled_pairs = [cfg for cfg in self.config_pingppong.pair_configs if cfg.get('enabled', True)]
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

    def get_dex_history_file_path(self, pair_name: str) -> str:
        unique_id = pair_name.replace("/", "_")
        return f"{self.config_manager.ROOT_DIR}/data/pingpong_{unique_id}_last_order.yaml"

    def get_dex_token_address_file_path(self, token_symbol: str) -> str:
        return f"{self.config_manager.ROOT_DIR}/data/pingpong_{token_symbol}_addr.yaml"

    def build_sell_order_details(self, dex_pair_instance, manual_dex_price=None) -> tuple:
        # PingPong specific logic for amount and offset for sell side
        usd_amount = dex_pair_instance.pair.cfg['usd_amount']
        btc_usd_price = self.config_manager.tokens['BTC'].cex.usd_price
        amount = (
                         usd_amount / btc_usd_price) / dex_pair_instance.t1.cex.cex_price if dex_pair_instance.t1.cex.cex_price and btc_usd_price else 0
        offset = dex_pair_instance.pair.sell_price_offset
        return amount, offset

    def calculate_sell_price(self, dex_pair_instance, manual_dex_price=None) -> float:
        # PingPong sells at CEX price + offset
        return dex_pair_instance.pair.cex.price

    def build_buy_order_details(self, dex_pair_instance, manual_dex_price=None) -> tuple:
        # PingPong specific logic for amount and spread for buy side
        amount = float(dex_pair_instance.order_history['maker_size'])
        spread = dex_pair_instance.pair.cfg.get('spread')
        return amount, spread

    def determine_buy_price(self, dex_pair_instance, manual_dex_price=None) -> float:
        # PingPong buys at min(live_price, sold_price)
        if manual_dex_price:
            return min(dex_pair_instance.pair.cex.price, dex_pair_instance.order_history['dex_price'])
        return dex_pair_instance.pair.cex.price

    def get_price_variation_tolerance(self, dex_pair_instance) -> float:
        return dex_pair_instance.pair.cfg.get('price_variation_tolerance')

    def calculate_variation_based_on_side(self, dex_pair_instance, current_order_side: str, cex_price: float,
                                          original_price: float) -> float:
        # LOCK PRICE TO POSITIVE ACTION ONLY, PINGPONG DO NOT REBUY UNDER SELL PRICE
        if current_order_side == 'BUY' and cex_price < dex_pair_instance.order_history['org_pprice']:
            return float(cex_price / original_price)
        # SELL SIDE FLOAT ON CURRENT PRICE
        if current_order_side == 'SELL':
            return float(cex_price / original_price)
        else:
            return 1  # Should not happen for pingpong

    def calculate_default_variation(self, dex_pair_instance, cex_price: float, original_price: float) -> float:
        # PingPong doesn't have a specific default variation logic beyond the side-based one
        return float(cex_price / original_price)

    def init_virtual_order_logic(self, dex_pair_instance, order_history: dict):
        if not order_history or ('side' in order_history and order_history['side'] == 'BUY'):
            dex_pair_instance.create_virtual_sell_order()
        elif 'side' in order_history and order_history['side'] == 'SELL':
            dex_pair_instance.create_virtual_buy_order(manual_dex_price=True)
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

    def handle_error_swap_status(self, dex_pair_instance):
        self.config_manager.general_log.error(
            f"Order Error:\n{dex_pair_instance.current_order}\n{dex_pair_instance.order}")
        raise SystemExit(1)  # Raise an exception to allow for graceful shutdown

    async def thread_init_async_action(self, pair_instance):
        pair_instance.dex.init_virtual_order(self.controller.disabled_coins)
        await pair_instance.dex.create_order()

    async def thread_loop_async_action(self, pair_instance):
        await pair_instance.dex.status_check(self.controller.disabled_coins)

    def should_update_cex_prices(self) -> bool:
        return True

    def get_operation_interval(self) -> int:
        return 15
