# xbridge_trading_bots
Trading bots for Blocknet Xbridge.\
https://github.com/blocknetdx/ \
https://docs.blocknet.org/protocol/xbridge/introduction/

Should works with python3.10 python3.11, \
python3.12 fail to build packages for now. \
Tested on python3.10
# Install 
```
git clone https://github.com/tryiou/xbridge_trading_bots.git
cd xbridge_trading_bots
# optional step: create and activate python venv
# pip / pip3, python / python3 depending on OS
pip install -r requirements.txt
```
# Update
```
cd xbridge_trading_bots
# optional step: activate python venv
git pull
pip install -r requirements.txt
```
# Connection with blocknet-core
Bot will automatically attempt to grab blocknet.conf rpc credentials/port when starting one of the scripts,\
if default chaindir path is used, it will pick from it and start,\
if another custom path is used, a prompt box will open asking for blocknet.conf path, or a console prompt if tk is not installed.\
this custom path will be stored in config folder for consequent uses.

# Pingpong
gui version need tkinter package\
https://www.pythonguis.com/installation/install-tkinter-mac/ \
https://www.pythonguis.com/installation/install-tkinter-linux/ \
(python3 for windows already got tkinter installed)
```
# edit config/config_pingpong.py
# set desired user_pairs / usd_amount_default / spread_default
# optional: customise per pair

# to display little user interface to start/stop/watch orders 
# run the gui version with :
python gui_pingpong.py 
# or console version with:
python main_pingpong.py

```

# BasicSeller
```
# example usage:
python basic_seller.py --help
python basic_seller.py -tts BLOCK -ttb PIVX -atts 200 -mup 0.33 -spu 0.015
# -tts  : Token to sell
# -ttb  : Token to buy
# -atts : Amount TokenToSell
# -mup  : Min Usd Price TokenToSell
# -spu  : Sell Price Upscale over ccxt price calcs 
```
 
