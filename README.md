# xbridge_trading_bots
Trading bots for Blocknet Xbridge.

Should works with python3.X \
v3.10 actually

```
git clone https://github.com/tryiou/xbridge_trading_bots.git
cd xbridge_trading_bots
# optional step: create and activate python venv
# pip / pip3, python / python3 depending on OS
pip install -r requirements.txt
```
# Pingpong
gui version need tkinter package\
https://www.pythonguis.com/installation/install-tkinter-mac/ \
https://www.pythonguis.com/installation/install-tkinter-linux/
```
# edit config/blocknet_rpc_cfg.py
# set your blocknet rpc credentials/port
# edit config/config_pingpong.py
# set desired coins / size / spread

# to display little user interface to start/stop/watch orders 
# run the gui version with :
python gui_pingpong.py 
# or console version with:
python main_pingpong.py

```

# BasicSeller
```
# edit config/blocknet_rpc_cfg.py
# set your blocknet rpc credentials/port
# example usage:
python basic_seller.py --help
python basic_seller.py -tts BLOCK -ttb PIVX -atts 200 -mup 0.33 -spu 0.015
# -tts  : Token to sell
# -ttb  : Token to buy
# -atts : Amount TokenToSell
# -mup  : Min Usd Price TokenToSell
# -spu  : Sell Price Upscale over ccxt price calcs
  
```
 
