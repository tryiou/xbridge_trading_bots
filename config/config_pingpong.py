# BOT WILL CREATE 1 ORDER PER PAIR
# PING                               PONG
# first step sell token1 to token2, second step buy token2 with token1, loop

debug_level = 2  # 0=Off, 2=Display RPC calls, 3=Display answers
ttk_theme = "darkly"

# "BLOCK/LTC" = "token1/token2", "LTC/BLOCK" = "token1/token2"
# each line is one active order
user_pairs = [
    "BLOCK/LTC",
    "LTC/BLOCK"
    # "LTC/DASH",
    # "DASH/LTC",
    # "BLOCK/DASH"
]

# price_variation_tolerance = CANCEL AND REFRESH ORDER IF PRICE CHANGE MORE THAN THIS % SET
price_variation_tolerance = 0.02  # 0.01=1%

# sell_price_offset = % upscale applied to xb sell price based on ccxt ticker
sell_price_offset = 0.05

# usd_amount = PER ORDER USD_TO_TOKEN1 AMOUNT ("BLOCK/LTC" = "token1/token2")
# gather token1/2/usd prices tickers to calc orders sizes
# custom to use per pair setting, else use default
usd_amount_default = 1  # $USD PER ORDER
# optional >
usd_amount_custom = {
    "DASH/BLOCK": 21,
    "BLOCK/DASH": 19
    # "DASH/LTC": 30
    # "BLOCK/LTC": 500,
    # "BLOCK/BTC": 50
}
# optional<

# spread = min % profit lock on PONG action:
# (cex_price *(1+sell_price_offset)) to sell, (sold_price*(1-spread)) to rebuy
# custom to use per pair setting, else use default
spread_default = 0.05  # 0.05=5%
# optional >
spread_custom = {
    "BLOCK/LTC": 0.04,
    "LTC/BLOCK": 0.04
    # "DOGE/BLOCK": 0.03,
    # "BLOCK/DOGE": 0.03
}