# xbridge_trading_bots

Trading bots for Blocknet Xbridge.  
[Blocknet GitHub](https://github.com/blocknetdx/)  
[Blocknet Documentation](https://docs.blocknet.org/protocol/xbridge/introduction/)

Compatible with Python 3.10 and 3.11.  
Python 3.12 currently fails to build packages.  
Tested on Python 3.10.

## Installation

1. Clone the repository:
    ```sh
    git clone https://github.com/tryiou/xbridge_trading_bots.git
    cd xbridge_trading_bots
    ```

2. (Optional) Create and activate a Python virtual environment:
    ```sh
    python -m venv venv
    source venv/bin/activate   # On Windows use `venv\Scripts\activate`
    ```

3. Install the required packages:
    ```sh
    pip install -r requirements.txt
    ```

## Update

1. Navigate to the project directory:
    ```sh
    cd xbridge_trading_bots
    ```

2. (Optional) Activate the Python virtual environment:
    ```sh
    source venv/bin/activate   # On Windows use `venv\Scripts\activate`
    ```

3. Pull the latest changes and update dependencies:
    ```sh
    git pull
    pip install -r requirements.txt
    ```

## Connection with Blocknet Core

The bot will automatically attempt to grab `blocknet.conf` RPC credentials and port when starting one of the scripts.

- If the default chain directory path is used, it will pick from it and start.
- If a custom path is used, a prompt box will appear asking for the `blocknet.conf` path, or a console prompt if Tkinter
  is not installed.
- This custom path will be stored in the config folder for subsequent uses.

## Pingpong Bot

The GUI version requires the Tkinter package. Installation guides can be found here:

- [Tkinter for Mac](https://www.pythonguis.com/installation/install-tkinter-mac/)
- [Tkinter for Linux](https://www.pythonguis.com/installation/install-tkinter-linux/)

Note: Python 3 for Windows already includes Tkinter.

1. Edit the configuration:
    ```python
    # Edit config/config_pingpong.yaml
    # Set desired user_pairs / usd_amount_default / spread_default
    # Optional: customize per pair
    ```

2. Run the GUI version:
    ```sh
    python gui_pingpong.py 
    ```

3. Or run the console version:
    ```sh
    python main_pingpong.py
    ```

## BasicSeller Bot

1. Example usage:
    ```sh
    python basic_seller.py --help
    python basic_seller.py -tts BLOCK -ttb PIVX -atts 200 -mup 0.33 -spu 0.015
    ```

   Options:
    - `-tts`  : Token to sell
    - `-ttb`  : Token to buy
    - `-atts` : Amount of Token to Sell
    - `-mup`  : Minimum USD Price of Token to Sell
    - `-spu`  : Sell Price Upscale over ccxt price calculations
