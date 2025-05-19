# xbridge_trading_bots

Trading bots for Blocknet Xbridge.  
[Blocknet GitHub](https://github.com/blocknetdx/)  
[Blocknet Documentation](https://docs.blocknet.org/protocol/xbridge/introduction/)

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
    - `-tts, --TokenToSell`  
      Required. The token you wish to sell (e.g., BLOCK).  
      Type: string

    - `-ttb, --TokenToBuy`  
      Required. The token you want to buy (e.g., LTC).  
      Type: string

    - `-atts, --AmountTokenToSell`  
      Required. The amount of the token you want to sell.  
      Type: float

    - `-mup, --MinUsdPrice`  
      Required. The minimum USD price at which you want to sell the token.  
      Type: float

    - `-spu, --SellPriceUpscale`  
      Optional. Percentage upscale applied to the CCXT ticker price for the token sale. For example, 0.015 represents a
      1.5% upscale. The default value is 0.015.  
      Type: float

    - `-p, --partial`  
      Optional. Minimum size of the partial sell as a percentage of the total size (between 0.001 (inclusive) and 1 (
      exclusive)). For example, --partial 0.5 means selling 50% of the specified amount. The default is None.  
      Type: float
