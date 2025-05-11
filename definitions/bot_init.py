import os

import definitions.xbridge_def as xbridge_def
from definitions.ccxt_def import init_ccxt_instance
from definitions.logger import setup_logger
from definitions.pair import Pair
from definitions.pingpong_loader import ConfigPP
from definitions.token import Token
from definitions.yaml_mix import YamlToObject


class BotContext:
    def __init__(self):
        self.ROOT_DIR = os.path.abspath(os.curdir)
        self.config_ccxt = YamlToObject("./config/config_ccxt.yaml")
        self.t = None  # Tokens dict
        self.p = None  # Pairs dict
        self.my_ccxt = None  # CCXT instance
        self.config_pp = None  # PingPong config
        self.ccxt_log = None
        self.general_log = None
        self.trade_log = None
        self.controller = None


# Global context instance (temporary during transition)
context = BotContext()


def initialize(strategy, loadxbridgeconf=True, tokens_list=None, amount_token_to_sell=None, min_sell_price_usd=None,
               sell_price_offset=None, partial_percent=None):
    context.general_log, context.trade_log, context.ccxt_log = setup_logger(strategy)
    context.config_pp = ConfigPP.load_config("./config/config_pingpong.yaml") if strategy == 'pingpong' else None

    # Initialize CCXT instance
    context.my_ccxt = init_ccxt_instance(
        exchange=context.config_ccxt.ccxt_exchange,
        hostname=context.config_ccxt.ccxt_hostname,
        private_api=False,
        debug_level=context.config_ccxt.debug_level
    )

    if strategy == 'pingpong':
        print(context.config_pp)
        if loadxbridgeconf:
            xbridge_def.dxloadxbridgeconf()

        tokens = []
        # Get enabled pairs from config
        sorted_pairs = sorted([cfg['pair'] for cfg in context.config_pp.pair_configs if cfg.get('enabled', True)])
        print(sorted_pairs)
        for pair in sorted_pairs:
            t1, t2 = pair.split("/")
            if t1 not in tokens:
                tokens.append(t1)
            if t2 not in tokens:
                tokens.append(t2)
        if 'BTC' not in tokens:
            tokens.append('BTC')

        # Ensure BTC is first in the list, needed for price updates
        tokens.insert(0, tokens.pop(tokens.index('BTC')))

        context.t = {token: Token(token, strategy="pingpong") for token in tokens}

        # Create pair entries with unique IDs for each config
        context.p = {}
        for cfg in [c for c in context.config_pp.pair_configs if c.get('enabled', True)]:
            t1, t2 = cfg['pair'].split("/")
            context.p[cfg['name']] = Pair(
                context.t[t1],
                context.t[t2],
                cfg=cfg,
                strategy="pingpong",
                dex_enabled=True,
                partial_percent=None
            )

    elif strategy == 'basic_seller':
        if tokens_list is None or amount_token_to_sell is None or min_sell_price_usd is None:
            raise ValueError("Missing required arguments for basic_seller strategy")

        context.t = {}
        for token in tokens_list:
            context.t[token] = Token(symbol=token, strategy="basic_seller")
        if "BTC" not in context.t:
            context.t["BTC"] = Token(symbol='BTC', strategy="basic_seller", dex_enabled=False)

        pair_key = f"{tokens_list[0]}/{tokens_list[1]}"
        context.p = {
            pair_key: Pair(
                token1=context.t[tokens_list[0]],
                token2=context.t[tokens_list[1]],
                cfg={'name': "basic_seller"},
                strategy="basic_seller",
                amount_token_to_sell=amount_token_to_sell,
                min_sell_price_usd=min_sell_price_usd,
                sell_price_offset=sell_price_offset,
                partial_percent=partial_percent
            )
        }
