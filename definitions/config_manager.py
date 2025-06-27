import os
import shutil

from definitions.ccxt_def import CCXTManager
from definitions.logger import setup_logger
from definitions.strategy import BaseStrategy, PingPongStrategy, BasicSellerStrategy, ArbitrageStrategy
from definitions.token import Token
from definitions.xbridge_def import XBridgeManager
from definitions.yaml_mix import YamlToObject


class ConfigManager:
    def __init__(self, strategy):
        self.strategy = strategy
        self.ROOT_DIR = os.path.abspath(os.curdir)
        self.general_log, self.trade_log, self.ccxt_log = setup_logger(strategy, self.ROOT_DIR)
        self.config_ccxt = None
        self.config_coins = None
        self.config_pp = None
        self.strategy_config = {}
        self.strategy_instance: BaseStrategy = None
        self.load_configs()
        self.tokens = {}  # Token data
        self.pairs = {}  # Pair data
        self.my_ccxt = None  # CCXT instance
        self.xbridge_manager = None
        self.ccxt_manager = CCXTManager(self)
        self.load_xbridge_conf_on_startup = True  # Default value, will be updated by initialize
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

    def _init_tokens(self, **kwargs):
        """Initialize token objects based on strategy configuration, delegated to strategy instance."""
        tokens_list = self.strategy_instance.get_tokens_for_initialization(**kwargs)

        if not tokens_list or len(tokens_list) < 2:
            raise ValueError(f"tokens_list must contain at least two tokens: {tokens_list}")

        # ENSURE BTC IS PRESENT.
        if 'BTC' not in tokens_list:
            self.tokens['BTC'] = Token(
                'BTC',
                strategy=self.strategy,  # Keep strategy for now, might be removed later
                config_manager=self,
                dex_enabled=False  # BTC is usually for pricing only, unless specified by strategy
            )

        # REMOVE DOUBLE ENTRIES
        tokens_list = list(set(tokens_list))
        for token_symbol in tokens_list:
            # Only create if not already created (e.g., BTC)
            if token_symbol not in self.tokens:
                dex_enabled = self.strategy == 'arbitrage' or token_symbol != 'BTC'
                self.tokens[token_symbol] = Token(
                    token_symbol,
                    strategy=self.strategy,  # Keep strategy for now, might be removed later
                    config_manager=self,
                    dex_enabled=dex_enabled
                )

    def _init_pairs(self, **kwargs):
        """Initialize trading pairs based on strategy configuration, delegated to strategy instance."""
        self.pairs = self.strategy_instance.get_pairs_for_initialization(self.tokens, **kwargs)

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
        self.xbridge_manager.dxloadxbridgeconf()

    def initialize(self, **kwargs):
        loadxbridgeconf = kwargs.get('loadxbridgeconf', True)
        self.strategy_config.update(kwargs)

        self.tokens = {}  # Token data
        self.pairs = {}  # Pair data
        self.xbridge_manager = XBridgeManager(self)
        self.load_xbridge_conf_on_startup = loadxbridgeconf  # Store the flag

        if self.strategy == "pingpong":
            self.strategy_instance = PingPongStrategy(self)
        elif self.strategy == "basic_seller":
            self.strategy_instance = BasicSellerStrategy(self)
        elif self.strategy == "arbitrage":
            self.strategy_instance = ArbitrageStrategy(self)
        else:
            raise ValueError(f"Unknown strategy: {self.strategy}")
        self.strategy_instance.initialize_strategy_specifics(**kwargs)

        # Initialize tokens based on strategy
        self._init_tokens(**kwargs)

        # Initialize pairs based on strategy
        self._init_pairs(**kwargs)
        if self.my_ccxt is None:
            self._init_ccxt()
        # dxloadxbridgeconf is now called asynchronously in MainController.main_init_loop
        # self._init_xbridge() # This method is now effectively a no-op if dxloadxbridgeconf is removed
