# user_pairs = BOT WILL CREATE 1 ORDER PER PAIR
# first sell token 1 to token2, second buy token2 with token1, loop
# ex : "BLOCK/LTC" = "token1/token2", "LTC/LTC" = "token1/token2"
# each line is one side only

debug_level = 2  # 0=Off, 2=Display RPC calls, 3=Display answers
user_pairs = [
    # "DOGE/LTC",
    # "LTC/DOGE",
    # "LTC/PIVX",
    # "PIVX/LTC",
    "BLOCK/LTC",
    "LTC/BLOCK",
    # "RVN/LTC",
    # "LTC/RVN",
    "LTC/DASH",
    "DASH/LTC",
    # "BLOCK/DASH",
    # "DASH/BLOCK",
    # "BLOCK/PIVX",
    # "PIVX/BLOCK",
]

cc_coins = []
# "BTC"
# "DASH",

# sell_price_offset = % upscale applied to xb sell price based on ccxt ticker
sell_price_offset = 0.05

# spread = min % lock on xb rebuy action:
# (cex_price *(1+sell_price_offset)) to sell, (sold_price*(1-spread)) to rebuy
# custom to use per pair setting, else use default
spread_default = 0.05  # 0.05=5%
# optional >
spread_custom = {
    # "BLOCK/LTC": 0.04,
    # "LTC/BLOCK": 0.04,
    # "DOGE/BLOCK": 0.03,
    # "BLOCK/DOGE": 0.03,
    # "DOGE/LTC": 0.03,
    # "LTC/DOGE": 0.03,
    # "DASH/BLOCK": 0.03,
    # "BLOCK/DASH": 0.03,
    # "PIVX/BLOCK": 0.04,
    # "BLOCK/PIVX": 0.04
    # "BLOCK/BTC": 0.1
}
# usd_amount = PER ORDER USD_TO_TOKEN1 AMOUNT ("BLOCK/LTC" = "token1/token2")
# convert token price to usd to calc order size
# custom to use per pair setting, else use default
usd_amount_default = 5  # 5 $USD PER ORDER
# optional >
usd_amount_custom = {
    # "DASH/BLOCK": 30,
    # "BLOCK/DASH": 30,
    # "DASH/LTC": 30
    # "BLOCK/LTC": 500,
    # "BLOCK/BTC": 50,
    # "BLOCK/LTC": 50,
    # "LTC/BLOCK": 50
}
# price_variation_tolerance = CANCEL AND REFRESH ORDER IF PRICE CHANGE MORE THAN THIS % SET
price_variation_tolerance = 0.01  # 0.01=1%

# display_max_stars = CHANGE RETURN TO LINE AFTER PRINTING STARS DURING ORDER CHECKS, ONLY CHANGE CONSOLE DISPLAY
display_max_stars = 10

# optional func>
arb_team = False
arb_team_spread = 0.04
arb_team_pairs = ["BLOCK/BTC"]  # ["BLOCK/LTC"]
# optional<
