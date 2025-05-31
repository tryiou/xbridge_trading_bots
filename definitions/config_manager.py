import os

from definitions import xbridge_def
from definitions.ccxt_def import CCXTManager
from definitions.logger import setup_logger
from definitions.pair import Pair
from definitions.token import Token
from definitions.yaml_mix import YamlToObject


class ConfigManager:
    def __init__(self, strategy):
        self.strategy = strategy
        self.ROOT_DIR = os.path.abspath(os.curdir)
        self.config_ccxt = YamlToObject("./config/config_ccxt.yaml")
        self.config_coins = YamlToObject("config/config_coins.yaml")
        self.config_pp = YamlToObject("config/config_pingpong.yaml") if strategy == "pingpong" else None
        self.tokens = {}  # Token data
        self.pairs = {}  # Pair data
        self.my_ccxt = None  # CCXT instance
        self.ccxt_manager = CCXTManager(self)
        self.xbridge_conf = None  # XBridge configuration
        self.logger = setup_logger(strategy, self.ROOT_DIR)
        self.disabled_coins = []  # Centralized disabled coins tracking
        self.controller = None
        self.general_log, self.trade_log, self.ccxt_log = setup_logger(strategy, self.ROOT_DIR)

    def _init_tokens(self, token_to_sell=None, token_to_buy=None):
        """Initialize token objects based on strategy configuration"""
        tokens_list = None
        if self.strategy == "pingpong":
            tokens_list = [cfg['pair'].split("/")[0] for cfg in self.config_pp.pair_configs if cfg.get('enabled', True)]
            tokens_list.extend(
                [cfg['pair'].split("/")[1] for cfg in self.config_pp.pair_configs if cfg.get('enabled', True)])
        elif self.strategy == "basic_seller":
            tokens_list = [token_to_sell, token_to_buy]

        if not tokens_list or len(tokens_list) < 2:
            raise ValueError(f"tokens_list must contain at least two tokens: {tokens_list}")

        # ENSURE BTC IS PRESENT.
        if 'BTC' not in tokens_list:
            self.tokens['BTC'] = Token('BTC',
                                       strategy=self.strategy,
                                       config_manager=self,
                                       dex_enabled=False)

        # REMOVE DOUBLE ENTRIES
        tokens_list = list(set(tokens_list))
        for token_symbol in tokens_list:
            self.tokens[token_symbol] = Token(token_symbol,
                                              strategy=self.strategy,
                                              config_manager=self)

    def _init_pairs(self, token_to_sell=None, token_to_buy=None, amount_token_to_sell=None, min_sell_price_usd=None,
                    sell_price_offset=None,
                    partial_percent=None):
        """Initialize trading pairs based on strategy configuration"""
        if self.strategy == "pingpong":
            enabled_pairs = [cfg for cfg in self.config_pp.pair_configs if cfg.get('enabled', True)]
            print(f"enabled_pairs: {enabled_pairs}")
            # exit(1)
            for cfg in enabled_pairs:
                t1, t2 = cfg['pair'].split("/")
                pair_name = f"{cfg['name']}"
                self.pairs[pair_name] = Pair(
                    token1=self.tokens[t1],
                    token2=self.tokens[t2],
                    cfg=cfg,
                    strategy="pingpong",
                    dex_enabled=True,
                    partial_percent=None,
                    config_manager=self
                )
        elif self.strategy == "basic_seller":
            if token_to_sell is None or token_to_buy is None:
                raise ValueError("Need at least two tokens for basic_seller strategy")

            pair_key = f"{token_to_sell}/{token_to_buy}"

            self.pairs[pair_key] = Pair(
                token1=self.tokens[token_to_sell],
                token2=self.tokens[token_to_buy],
                cfg={'name': "basic_seller"},
                strategy="basic_seller",
                amount_token_to_sell=amount_token_to_sell,
                min_sell_price_usd=min_sell_price_usd,
                sell_price_offset=sell_price_offset,
                partial_percent=partial_percent,
                config_manager=self
            )

    def _init_ccxt(self):
        """Initialize CCXT instance"""
        self.my_ccxt = self.ccxt_manager.init_ccxt_instance(
            exchange=self.config_ccxt.ccxt_exchange,
            hostname=self.config_ccxt.ccxt_hostname,
            private_api=False,
            debug_level=self.config_ccxt.debug_level
        )

    def _init_xbridge(self):
        """Initialize XBridge configuration"""
        xbridge_def.dxloadxbridgeconf()

    def initialize(self, token_to_sell=None, token_to_buy=None, amount_token_to_sell=None, min_sell_price_usd=None,
                   sell_price_offset=None, partial_percent=None, loadxbridgeconf=True):
        # Initialize tokens based on strategy
        self._init_tokens(token_to_sell, token_to_buy)

        # Initialize pairs based on strategy
        self._init_pairs(token_to_sell, token_to_buy, amount_token_to_sell, min_sell_price_usd, sell_price_offset,
                         partial_percent)
        if self.my_ccxt is None:
            self._init_ccxt()
        if loadxbridgeconf:
            self._init_xbridge()
