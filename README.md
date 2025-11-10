# XBridge Trading Bots

Automated trading bots for Blocknet's XBridge decentralized exchange protocol, enabling cross-chain cryptocurrency trading.

[![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Resources

- [Blocknet GitHub](https://github.com/blocknetdx/) - Official repository
- [XBridge Documentation](https://docs.blocknet.org/protocol/xbridge/introduction/) - Protocol specifications

## Prerequisites

- **Python 3.10+** - Required for all functionality
- **Blocknet Core** - Must be running and synchronized
- **Tkinter** (GUI components):

| Platform | Status | Installation |
|----------|--------|--------------|
| Windows  | Pre-installed | Included with Python 3 |
| macOS    | Required | [Installation Guide](https://www.pythonguis.com/installation/install-tkinter-mac/) |
| Linux    | Required | [Installation Guide](https://www.pythonguis.com/installation/install-tkinter-linux/) |

## Quick Start

### Installation

```bash
# Clone repository
git clone https://github.com/tryiou/xbridge_trading_bots.git
cd xbridge_trading_bots

# Create virtual environment
python -m venv venv

# Activate environment
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt
```

### Updates

```bash
cd xbridge_trading_bots
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
git pull
pip install -r requirements.txt
```

## Initial Configuration

### Blocknet Core Connection

- **Default**: Auto-locates `blocknet.conf` in standard data directory
- **Custom**: Interactive prompt for custom `blocknet.conf` path
- **Persistence**: Custom paths saved for future sessions

> **Note**: Ensure Blocknet Core is running, synchronized, and unlocked.

### Configuration Files

```bash
python prepare_configs.py
```

| Template File | Purpose | Used By |
|---------------|---------|---------|
| `config_pingpong.yaml` | Trading pairs and parameters | PingPong Bot |
| `config_ccxt.yaml` | Exchange API configuration | PingPong & BasicSeller |
| `config_coins.yaml` | Static USD prices (optional) | All Bots |
| `config_xbridge.yaml` | XBridge fees and monitoring | All Bots |

## Usage

### GUI (All Strategies)

```bash
python main_gui.py
```

Configuration and monitoring for both strategies. Requires Tkinter.

### Console Interfaces

```bash
python main_pingpong.py      # PingPong strategy
python main_basic_seller.py  # BasicSeller strategy
```

## Trading Strategies

### PingPong Bot

**Type:** Automated Market Making

**Description:** Profitable buy/sell cycles with guaranteed spread.

#### How It Works

1. **PING:** Sell TokenA for TokenB at market price
2. **PONG:** Buy TokenA with TokenB at `(PING price - spread)`
3. **Profit:** Earn spread percentage per cycle

**Example:** $100 PING sale, 2% spread
- PONG buy executes at $98
- Profit = $2 per cycle

#### Features

- Single active order per pair
- Auto-adjusts prices to market movements
- Expands spread on favorable movement
- Protects minimum spread on unfavorable movement
- Continuous cycling until stopped

#### Configuration

- `config_pingpong.yaml` - Pairs and spread parameters
- `config_ccxt.yaml` - Exchange API for price data
- `config_coins.yaml` - Static USD prices (optional)

#### Usage

```bash
# Console interface
python main_pingpong.py

# GUI configuration
python main_gui.py
```

### BasicSeller Bot

**Type:** Conditional Sell Order

**Description:** TokenA→TokenB sell orders with USD price floor protection.

#### Pricing Rules

Orders use the **better** of:
1. `min_sell_price_usd` (floor price)
2. Current USD price + `sell_price_offset`

#### Order Behavior

| Market Condition | Order Price |
|------------------|-------------|
| TokenA price ≥ floor | Market price + offset |
| TokenA price < floor | Floor price |
| TokenA price recovers | Auto-adjusts upward |

#### Configuration

- `config_ccxt.yaml` - Exchange API configuration
- `config_coins.yaml` - Static USD prices (optional)

#### Usage

**View all options:**
```bash
python main_basic_seller.py --help
```

**Basic example:**
```bash
python main_basic_seller.py -tts BLOCK -ttb PIVX -atts 200 -mup 0.33 -spu 0.015
```

**Partial sell example:**
```bash
python main_basic_seller.py -tts BLOCK -ttb PIVX -atts 200 -mup 0.33 -spu 0.015 --partial 0.5
```

#### Command Line Parameters

| Parameter | Short | Required | Type | Description |
|-----------|-------|----------|------|-------------|
| `--TokenToSell` | `-tts` | ✅ | string | Token to sell (e.g., BLOCK, LTC) |
| `--TokenToBuy` | `-ttb` | ✅ | string | Token to receive (e.g., PIVX, BTC) |
| `--AmountTokenToSell` | `-atts` | ✅ | float | Quantity to sell |
| `--MinUsdPrice` | `-mup` | ✅ | float | Minimum USD price per token |
| `--SellPriceUpscale` | `-spu` | ❌ | float | Price markup % (default: 0.015 = 1.5%) |
| `--partial` | `-p` | ❌ | float | Partial sell ratio (0.001-0.999) |

#### Parameter Details

- **SellPriceUpscale**: Percentage markup added to current pair price
- **Partial**: Enables selling a fraction of total amount

## Troubleshooting

### Common Issues

**Connection Problems:**
- Verify Blocknet Core is running, synchronized, unlocked
- Check RPC credentials in `blocknet.conf`
- Ensure firewall allows local RPC connections

**GUI Issues:**
- Install Tkinter using platform-specific guides
- Use console version if GUI fails to start

**Configuration Errors:**
- Verify YAML syntax in config files
- Ensure all required parameters are specified
- Confirm coin symbols match XBridge supported assets

## Project Structure

```
xbridge_trading_bots/
├── config/                    # Configuration management
│   ├── templates/            # Config templates
│   └── [generated configs]   # Your configs
├── data/                     # Trade history, state files
├── definitions/              # Core functionality and models
├── gui/                      # GUI components
│   ├── components/           # Reusable widgets
│   ├── config_windows/       # Strategy config windows
│   ├── frames/               # Main application frames
│   └── utils/                # GUI utilities
├── logs/                     # Application logs
├── strategies/               # Trading strategy implementations
├── test_units/               # Unit and integration tests
├── main_gui.py               # GUI launcher
├── main_pingpong.py          # PingPong launcher
├── main_basic_seller.py      # BasicSeller launcher
├── proxy_ccxt.py             # CCXT proxy and price fetcher
├── prepare_configs.py        # Config preparation utility
└── requirements.txt          # Python dependencies
```

## Contributing

Contributions welcome. Submit issues, feature requests, or pull requests.

## Disclaimer

**Use at your own risk.** Cryptocurrency trading involves substantial risk of loss. These bots are provided as-is without warranty. Test with small amounts first.
