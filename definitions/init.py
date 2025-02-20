import os

from definitions.yaml_mix import YamlToObject

ROOT_DIR = os.path.abspath(os.curdir)
config_ccxt = YamlToObject("./config/config_ccxt.yaml")


def init_pingpong(loadxbridgeconf=True):
    global t, p, my_ccxt, config_pp
    from definitions.classes import Token, Pair, ConfigPP, setup_logger
    from definitions.ccxt_def import init_ccxt_instance
    import definitions.xbridge_def as xbridge_def

    setup_logger("pingpong")
    config_pp = ConfigPP.load_config("./config/config_pingpong.yaml")

    print(config_pp)
    if loadxbridgeconf:
        # Load xbridge configuration
        xbridge_def.dxloadxbridgeconf()

    # Initialize CCXT instance
    my_ccxt = init_ccxt_instance(
        exchange=config_ccxt.ccxt_exchange,
        hostname=config_ccxt.ccxt_hostname,
        private_api=False
    )

    # Prepare the list of active tokens, ensuring BTC is included
    tokens = []
    sorted_pairs = sorted(config_pp.user_pairs)
    for pair in sorted_pairs:
        t1, t2 = pair.split("/")
        if t1 not in tokens:
            tokens.append(t1)
        if t2 not in tokens:
            tokens.append(t2)
    if 'BTC' not in tokens:
        tokens.append('BTC')
    tokens.insert(0, tokens.pop(tokens.index('BTC')))  # Ensure BTC is first in the list

    # Initialize the token and pair dictionaries
    t = {token: Token(token, strategy="pingpong") for token in tokens}
    p = {
        pair: Pair(t[t1], t[t2], strategy="pingpong", dex_enabled=True)
        for pair in sorted_pairs
        for t1, t2 in [pair.split("/")]
    }


def init_basic_seller(tokens_list, amount_token_to_sell, min_sell_price_usd, ccxt_sell_price_upscale, partial_percent):
    global t, p, my_ccxt
    from definitions.classes import Token, Pair, setup_logger
    from definitions.ccxt_def import init_ccxt_instance

    setup_logger("basic_seller")

    # Initialize CCXT instance
    my_ccxt = init_ccxt_instance(
        exchange=config_ccxt.ccxt_exchange,
        hostname=config_ccxt.ccxt_hostname,
        private_api=False
    )

    # Initialize the token dictionary
    t = {}

    # Ensure BTC is included in the tokens dictionary
    if "BTC" not in t:
        t["BTC"] = Token(symbol='BTC', strategy="basic_seller", dex_enabled=False)

    for token in tokens_list:
        t[token] = Token(symbol=token, strategy="basic_seller")

    # Initialize the pair dictionary
    pair_key = f"{tokens_list[0]}/{tokens_list[1]}"
    p = {
        pair_key: Pair(
            token1=t[tokens_list[0]],
            token2=t[tokens_list[1]],
            strategy="basic_seller",
            amount_token_to_sell=amount_token_to_sell,
            min_sell_price_usd=min_sell_price_usd,
            ccxt_sell_price_upscale=ccxt_sell_price_upscale,
            partial_percent=partial_percent
        )
    }

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
