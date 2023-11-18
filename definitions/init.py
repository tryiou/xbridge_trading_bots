import os

from definitions.classes import setup_logger

ROOT_DIR = os.path.abspath(os.curdir)


def init_pingpong():
    setup_logger("pingpong")
    global t, p, my_ccxt
    # , pairs_dict
    from definitions.classes import Token, Pair
    from config.config_pingpong import user_pairs
    from config.ccxt_cfg import ccxt_exchange, ccxt_hostname
    # from main_pingpong import main_dx_update_bals
    from definitions.ccxt_def import init_ccxt_instance
    import definitions.xbridge_def as xbridge_def
    # from multiprocessing import shared_memory
    xbridge_def.dxloadxbridgeconf()
    # xbridge_def.proxy_init_storage()
    my_ccxt = init_ccxt_instance(exchange=ccxt_exchange, hostname=ccxt_hostname, private_api=False)
    # ACTIVE TOKENS LIST, KEEP BTC INSIDE EVEN IF UNUSED
    tokens = []
    for pair in user_pairs:
        sep = pair.find("/")
        t1 = pair[0:sep]
        t2 = pair[sep + 1::]
        if t1 not in tokens:
            tokens.append(t1)
        if t2 not in tokens:
            tokens.append(t2)
    if 'BTC' not in tokens:
        tokens.append('BTC')
    # BTC FIRST IN LIST
    tokens.insert(0, tokens.pop(tokens.index('BTC')))
    t = {}
    for token in tokens:
        t[token] = Token(token, strategy="pingpong")
        # t[token].read_xb_address()
    # main_dx_update_bals(t)
    p = {}
    for pair in user_pairs:
        sep = pair.find("/")
        t1 = pair[0:sep]
        t2 = pair[sep + 1::]
        p[pair] = Pair(t[t1], t[t2], strategy="pingpong", dex_enabled=True)
    # print(t, p)


def init_coins_dict_arbtaker():
    import definitions.xbridge_def as xb
    from definitions.classes import Token

    coins_dict = {}
    dx_tokens = xb.getlocaltokens()
    for token_name in dx_tokens:
        if 'Wallet' not in token_name:
            coins_dict[token_name] = Token(token_name, strategy="arbtaker")
            if not ('BTC' in coins_dict):
                coins_dict['BTC'] = Token('BTC', strategy="arbtaker", dex_enabled=False)
    return coins_dict


def init_pairs_dict_arbtaker(tokens_dict, dex_markets, strategy):
    from definitions.classes import Pair
    pairs_dict = {}
    for market in dex_markets:
        pairs_dict[market[0] + '/' + market[1]] = Pair(token1=tokens_dict[market[0]], token2=tokens_dict[market[1]],
                                                       strategy=strategy)
    return pairs_dict


def init_arbtaker():
    global t, p, my_ccxt
    from main_arbtaker import main_dx_get_markets
    from definitions.ccxt_def import init_ccxt_instance
    from config.config_arbtaker import ccxt_exchange_name, ccxt_exchange_hostname
    setup_logger("arbtaker")
    my_ccxt = init_ccxt_instance(exchange=ccxt_exchange_name, hostname=ccxt_exchange_hostname, private_api=True)
    t = init_coins_dict_arbtaker()
    dex_markets = main_dx_get_markets(t)
    p = init_pairs_dict_arbtaker(tokens_dict=t, dex_markets=dex_markets, strategy="arbtaker")


def init_basic_seller(tokens_list, amount_token_to_sell, min_sell_price_usd, ccxt_sell_price_upscale):
    global t, p, my_ccxt
    from definitions.classes import Token, Pair
    from definitions.ccxt_def import init_ccxt_instance
    from config.ccxt_cfg import ccxt_hostname, ccxt_exchange
    setup_logger("basic_seller")
    my_ccxt = init_ccxt_instance(exchange=ccxt_exchange, hostname=ccxt_hostname, private_api=False)
    t = {}
    # [token_to_sell,token_to_buy]
    if "BTC" not in t.items():
        t["BTC"] = Token(symbol='BTC', strategy="basic_seller", dex_enabled=False)
    for token in tokens_list:
        t[token] = Token(symbol=token, strategy="basic_seller")
    # print(t)
    # pairs_dict[market[0] + '/' + market[1]]
    p = {}
    p[tokens_list[0] + '/' + tokens_list[1]] = Pair(token1=t[tokens_list[0]],
                                                    token2=t[tokens_list[1]],
                                                    strategy="basic_seller",
                                                    amount_token_to_sell=amount_token_to_sell,
                                                    min_sell_price_usd=min_sell_price_usd,
                                                    ccxt_sell_price_upscale=ccxt_sell_price_upscale)
