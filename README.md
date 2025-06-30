# Xbridge Trading Bots

Automated trading bots for Blocknet's Xbridge decentralized exchange protocol, enabling cross-chain cryptocurrency
trading without intermediaries.

[![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## 🔗 Resources

- **[Blocknet GitHub](https://github.com/blocknetdx/)** - Official repository
- **[Xbridge Documentation](https://docs.blocknet.org/protocol/xbridge/introduction/)** - Protocol specifications

## 📋 Prerequisites

- **Python 3.10+** - Required for all functionality
- **Blocknet Core** - Must be running and synchronized
- **Tkinter** (GUI components) - Pre-installed on Windows, see [GUI requirements](#gui-requirements) for other platforms

## 🚀 Quick Start

### Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/tryiou/xbridge_trading_bots.git
   cd xbridge_trading_bots
   ```

2. **Set up virtual environment** (recommended)

   ```bash
   # Create virtual environment
   python -m venv venv
   
   # Activate environment
   source venv/bin/activate          # Linux/Mac
   venv\Scripts\activate             # Windows
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

### Updates

Keep your installation current with the latest features and fixes:

```bash
cd xbridge_trading_bots
source venv/bin/activate    # Linux/Mac
venv\Scripts\activate       # Windows
git pull
pip install -r requirements.txt
```

## ⚙️ Initial Configuration

### Blocknet Core Connection

The bots automatically detect and connect to your Blocknet Core instance:

- **Default setup**: Automatically locates `blocknet.conf` in the standard data directory
- **Custom setup**: Interactive prompt requests the path to your `blocknet.conf` file
- **Path persistence**: Custom paths are saved for future sessions

> **Note**: Ensure Blocknet Core is running, fully synchronized, and unlocked.

### Configuration Templates

Before using any trading bot, you'll need to configure the relevant template files. Each bot uses different combinations
of these templates:

#### Available Templates

| Template File                   | Purpose                                              | Used By      |
|---------------------------------|------------------------------------------------------|--------------|
| `config_pingpong.yaml.template` | Trading pairs, amounts, and strategy parameters      | PingPong Bot |
| `config_coins.yaml.template`    | Static USD prices for coins without live market data | Both Bots    |
| `config_ccxt.yaml.template`     | Exchange API configuration for real-time price feeds | Both Bots    |

#### Setup Commands

**Linux/macOS:**

```bash
# Copy all templates (recommended)
cp config/templates/config_pingpong.yaml.template config/config_pingpong.yaml
cp config/templates/config_coins.yaml.template config/config_coins.yaml
cp config/templates/config_ccxt.yaml.template config/config_ccxt.yaml
```

**Windows:**

```cmd
REM Copy all templates (recommended)
copy config\templates\config_pingpong.yaml.template config\config_pingpong.yaml
copy config\templates\config_coins.yaml.template config\config_coins.yaml
copy config\templates\config_ccxt.yaml.template config\config_ccxt.yaml
```

> **Note**: You can copy only the templates needed for your chosen trading strategy, but copying all is recommended for
> flexibility.

## 🤖 Trading Strategies

## Strategy 1: PingPong Bot

A market-making bot that places buy and sell orders around current market prices, profiting from spread on
ping-SELL/pong-BUY cycles.

### PingPong Bot Configuration

- ✅ `config_pingpong.yaml` - Primary bot configuration
- ✅ `config_coins.yaml` - Price fallback data
- ✅ `config_ccxt.yaml` - Market data source

### GUI Requirements

| Platform    | Installation                                                                         |
|-------------|--------------------------------------------------------------------------------------|
| **Windows** | Pre-installed with Python 3                                                          |
| **macOS**   | [Installation Guide](https://www.pythonguis.com/installation/install-tkinter-mac/)   |
| **Linux**   | [Installation Guide](https://www.pythonguis.com/installation/install-tkinter-linux/) |

### PingPong Bot Usage

**Config**:

- define your `pairs` in `config_pingpong.yaml`
- define static usd tickers if needed in `config_coins.yaml`
- GUI offer a configuration panel to define `pairs`.

**GUI Interface** (recommended for beginners):

```bash
python gui_pingpong.py
```

**Console Interface** (for automation/servers):

```bash
python main_pingpong.py
```

---

## Strategy 2: BasicSeller Bot

A straightforward selling bot that places sell orders at specified price targets with market-based pricing.

### BasicSeller Bot Configuration

- ✅ `config_coins.yaml` - Price fallback data
- ✅ `config_ccxt.yaml` - Market data source

### BasicSeller Bot Usage

**View all options:**

```bash
python main_basic_seller.py --help
```

**Basic Example** - Sell 200 BLOCK for PIVX at minimum $0.33 per BLOCK with 1.5% upscale on live pair price:

```bash
python main_basic_seller.py -tts BLOCK -ttb PIVX -atts 200 -mup 0.33 -spu 0.015
```

**Partial Sell Example** - Partial order allows selling a fraction of the total amount:

```bash
python main_basic_seller.py -tts BLOCK -ttb PIVX -atts 200 -mup 0.33 -spu 0.015 --partial 0.5
```

### Command Line Parameters

| Parameter             | Short   | Required | Type   | Description                                           |
|-----------------------|---------|----------|--------|-------------------------------------------------------|
| `--TokenToSell`       | `-tts`  | ✅        | string | Cryptocurrency to sell (e.g., BLOCK, LTC)             |
| `--TokenToBuy`        | `-ttb`  | ✅        | string | Cryptocurrency to receive (e.g., PIVX, BTC)           |
| `--AmountTokenToSell` | `-atts` | ✅        | float  | Quantity of tokens to sell                            |
| `--MinUsdPrice`       | `-mup`  | ✅        | float  | Minimum acceptable USD price per token                |
| `--SellPriceUpscale`  | `-spu`  | ❌        | float  | Pair price upscale percentage (default: 0.015 = 1.5%) |
| `--partial`           | `-p`    | ❌        | float  | Partial sell ratio (0.001-0.999, e.g., 0.5 = 50%)     |

### Parameter Details

- **SellPriceUpscale**: Adds a percentage markup to the current pair price. For example, `0.015` adds 1.5% above the
  calculated pair price
- **Partial**: Enables selling a fraction of the total amount.

## 🛠️ Troubleshooting

### Common Issues

**Connection Problems:**

- Verify Blocknet Core is running, synchronized, unlocked
- Check that RPC credentials in `blocknet.conf` are correct
- Ensure firewall allows local RPC connections

**GUI Issues:**

- Install Tkinter using the platform-specific guides above
- Try the console version if GUI fails to start

**Configuration Errors:**

- Verify YAML syntax in configuration files
- Ensure all required parameters are specified
- Check that coin symbols match Xbridge supported assets

## 📁 Project Structure

```text
xbridge_trading_bots/
├── config/
│   ├── templates/          # Configuration templates
│   └── [generated configs] # Your customized configurations
├── data/                   # Stores trade history, state files, and generated addresses
├── definitions/            # Core functionality, data models, and API managers
│   ├── bcolors.py          # Color definitions for console output
│   ├── ccxt_manager.py     # CCXT exchange API wrapper
│   ├── config_manager.py   # Configuration loading and management
│   ├── decorators.py       # Decorators for retries, etc.
│   ├── detect_rpc.py       # RPC connection detection utilities
│   ├── gui.py              # GUI components and dialogs
│   ├── logger.py           # Logging configuration and handlers
│   ├── pair.py             # Trading pair data model
│   ├── pingpong_loader.py  # Configuration loading utilities
│   ├── rpc.py              # RPC communication wrappers
│   ├── starter.py          # Core application controller and main async loop
│   ├── test_arbitrage_strategy.py # Internal test suite for arbitrage logic
│   ├── thorchain_def.py    # Thorchain API wrappers
│   ├── token.py            # Token data model
│   ├── trade_state.py      # Manages state for recoverable trades (arbitrage)
│   ├── xbridge_manager.py  # Xbridge protocol API wrapper
│   └── yaml_mix.py         # YAML to object conversion utility
├── data/                   # Store trades history and generated addresses
├── logs/                   # Store logs
├── strategies/             # Contains the logic for different trading strategies
│   ├── arbitrage_strategy.py
│   ├── base_strategy.py
│   ├── basicseller_strategy.py
│   ├── maker_strategy.py
│   └── pingpong_strategy.py
├── cancelallorders.py      # Utility script to cancel all open XBridge orders
├── gui_pingpong.py         # PingPong bot GUI interface
├── main_arbitrage.py       # Arbitrage bot console interface
├── main_basic_seller.py    # BasicSeller bot console interface
├── main_pingpong.py        # PingPong bot console interface
├── pingpong_logparser.py   # PingPong bot Trade log parsing and analysis tool
├── requirements.txt        # Python dependencies
└── README.md               # This file
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit issues, feature requests, or pull requests to help improve the
trading bots.

## ⚠️ Disclaimer

**Use at your own risk.** Cryptocurrency trading involves substantial risk of loss. These bots are provided as-is
without warranty. Always test with small amounts first and understand the risks involved in automated trading.

---

>*Built for the Blocknet ecosystem - Enabling truly decentralized cross-chain trading*
