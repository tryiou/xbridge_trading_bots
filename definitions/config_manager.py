import os
import shutil

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
        self.general_log, self.trade_log, self.ccxt_log = setup_logger(strategy, self.ROOT_DIR)
        self.config_ccxt = None
        self.config_coins = None
        self.config_pp = None
        self.load_configs()
        self.tokens = {}  # Token data
        self.pairs = {}  # Pair data
        self.my_ccxt = None  # CCXT instance
        self.ccxt_manager = CCXTManager(self)
        self.xbridge_conf = None  # XBridge configuration
        self.disabled_coins = []  # Centralized disabled coins tracking
        self.controller = None

    def create_configs_from_templates(self):
        # Check and create config files if they don't exist
        config_files = {
            "config_ccxt.yaml": os.path.join(self.ROOT_DIR, "config", "config_ccxt.yaml"),
            "config_coins.yaml": os.path.join(self.ROOT_DIR, "config", "config_coins.yaml"),
            "config_pingpong.yaml": os.path.join(self.ROOT_DIR, "config", "config_pingpong.yaml"),
            "api_keys.local.json": os.path.join(self.ROOT_DIR, "config", "api_keys.local.json")
        }
        for target_name, target_path in config_files.items():
            # Check if target file exists
            if not os.path.exists(target_path):
                # Check if template file exists
                template_path = os.path.join(self.ROOT_DIR, "config", "templates",
                                             os.path.basename(target_path) + ".template")
                if os.path.exists(template_path):
                    try:
                        shutil.copy(template_path, target_path)
                        self.general_log.info(f"Created config file: {target_name} from template")
                    except Exception as e:
                        self.general_log.error(f"Failed to create config file {target_name}: {str(e)}")
                else:
                    self.general_log.error(f"Template file {template_path} not found in config directory")
            else:
                # Target file exists
                self.general_log.info(f"{target_name}: Already exists")

    def load_configs(self):
        self.create_configs_from_templates()

        self.config_ccxt = YamlToObject("./config/config_ccxt.yaml")
        self.config_coins = YamlToObject("./config/config_coins.yaml")
        self.config_pp = YamlToObject("./config/config_pingpong.yaml") if self.strategy == "pingpong" else None

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

        self.tokens = {}  # Token data
        self.pairs = {}  # Pair data
        # Initialize tokens based on strategy
        self._init_tokens(token_to_sell, token_to_buy)

        # Initialize pairs based on strategy
        self._init_pairs(token_to_sell, token_to_buy, amount_token_to_sell, min_sell_price_usd, sell_price_offset,
                         partial_percent)
        if self.my_ccxt is None:
            self._init_ccxt()
        if loadxbridgeconf:
            self._init_xbridge()
