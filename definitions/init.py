import json
import os

import ccxt

import definitions.xbridge_def as xbridge_def
from definitions.ccxt_def import ccxt_manage_error
from definitions.logger import setup_logger
from definitions.pair import Pair
from definitions.pingpong_loader import ConfigPP
from definitions.token import Token
from definitions.yaml_mix import YamlToObject

ROOT_DIR = os.path.abspath(os.curdir)
config_ccxt = YamlToObject("./config/config_ccxt.yaml")


def init_ccxt_instance(exchange, hostname=None, private_api=False):
    # CCXT instance
    api_key = None
    api_secret = None
    if private_api:
        with open(ROOT_DIR + '/config/api_keys.local.json') as json_file:
            data_json = json.load(json_file)
            for data in data_json['api_info']:
                if exchange in data['exchange']:
                    api_key = data['api_key']
                    api_secret = data['api_secret']
    if exchange in ccxt.exchanges:
        exchange_class = getattr(ccxt, exchange)
        if hostname:
            instance = exchange_class({
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True,
                'rateLimit': 1000,
                'hostname': hostname,  # 'global.bittrex.com',
            })
        else:
            instance = exchange_class({
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True,
                'rateLimit': 1000,
            })
        done = False
        while not done:
            try:
                instance.load_markets()
            except Exception as e:
                ccxt_manage_error(e)
            else:
                done = True
        return instance
    else:
        return None


def bot_init(strategy, loadxbridgeconf=True, tokens_list=None, amount_token_to_sell=None, min_sell_price_usd=None,
             ccxt_sell_price_upscale=None, partial_percent=None):
    global t, p, my_ccxt, config_pp
    setup_logger(strategy)
    # Initialize CCXT instance
    my_ccxt = init_ccxt_instance(
        exchange=config_ccxt.ccxt_exchange,
        hostname=config_ccxt.ccxt_hostname,
        private_api=False
    )

    if strategy == 'pingpong':
        config_pp = ConfigPP.load_config("./config/config_pingpong.yaml")
        print(config_pp)
        if loadxbridgeconf:
            xbridge_def.dxloadxbridgeconf()

        tokens = []
        # Get enabled pairs from config
        sorted_pairs = sorted([cfg['pair'] for cfg in config_pp.pair_configs if cfg.get('enabled', True)])
        print(sorted_pairs)
        for pair in sorted_pairs:
            t1, t2 = pair.split("/")
            if t1 not in tokens:
                tokens.append(t1)
            if t2 not in tokens:
                tokens.append(t2)
        if 'BTC' not in tokens:
            tokens.append('BTC')
        tokens.insert(0, tokens.pop(tokens.index('BTC')))  # Ensure BTC is first in the list

        t = {token: Token(token, strategy="pingpong") for token in tokens}
        # Create pair entries with unique IDs for each config
        p = {}
        for cfg in [c for c in config_pp.pair_configs if c.get('enabled', True)]:
            t1, t2 = cfg['pair'].split("/")
            p[cfg['name']] = Pair(
                t[t1],
                t[t2],
                cfg=cfg,
                strategy="pingpong",
                dex_enabled=True,
                partial_percent=None
            )

    elif strategy == 'basic_seller':
        if tokens_list is None or amount_token_to_sell is None or min_sell_price_usd is None:
            raise ValueError("Missing required arguments for basic_seller strategy")

        t = {}
        for token in tokens_list:
            t[token] = Token(symbol=token, strategy="basic_seller")
        if "BTC" not in t:
            t["BTC"] = Token(symbol='BTC', strategy="basic_seller", dex_enabled=False)

        pair_key = f"{tokens_list[0]}/{tokens_list[1]}"
        p = {
            pair_key: Pair(
                token1=t[tokens_list[0]],
                token2=t[tokens_list[1]],
                cfg={'name': "basic_seller"},
                strategy="basic_seller",
                amount_token_to_sell=amount_token_to_sell,
                min_sell_price_usd=min_sell_price_usd,
                ccxt_sell_price_upscale=ccxt_sell_price_upscale,
                partial_percent=partial_percent
            )
        }

# Example usage:
# init('pingpong')
# init('basic_seller', tokens_list=['ETH', 'BTC'], amount_token_to_sell=1.0, min_sell_price_usd=100, ccxt_sell_price_upscale=1.01, partial_percent=0.5)


# def init_coins_dict_arbtaker():
#     import definitions.xbridge_def as xb
#     from definitions.classes import Token
#
#     coins_dict = {}
#     dx_tokens = xb.getlocaltokens()
#     for token_name in dx_tokens:
#         if 'Wallet' not in token_name:
#             coins_dict[token_name] = Token(token_name, strategy="arbtaker")
#             if not ('BTC' in coins_dict):
#                 coins_dict['BTC'] = Token('BTC', strategy="arbtaker", dex_enabled=False)
#     return coins_dict
#
#
# def init_pairs_dict_arbtaker(tokens_dict, dex_markets, strategy):
#     from definitions.classes import Pair
#     pairs_dict = {}
#     for market in dex_markets:
#         pairs_dict[market[0] + '/' + market[1]] = Pair(token1=tokens_dict[market[0]], token2=tokens_dict[market[1]],
#                                                        strategy=strategy)
#     return pairs_dict


# def init_arbtaker():
#     from definitions.classes import setup_logger
#     global t, p, my_ccxt
#     from main_arbtaker import main_dx_get_markets
#     from definitions.ccxt_def import init_ccxt_instance
#     setup_logger("arbtaker")
#     my_ccxt = init_ccxt_instance(exchange=config_ccxt.ccxt_exchange_name, hostname=config_ccxt.ccxt_exchange_hostname,
#                                  private_api=True)
#     t = init_coins_dict_arbtaker()
#     dex_markets = main_dx_get_markets(t)
#     p = init_pairs_dict_arbtaker(tokens_dict=t, dex_markets=dex_markets, strategy="arbtaker")
