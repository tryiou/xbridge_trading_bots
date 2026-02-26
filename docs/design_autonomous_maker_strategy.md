# Autonomous Maker Strategy - Design Document

## Overview

The Autonomous Maker Strategy is a pure market-making implementation for XBridge (Blocknet DEX) that operates without external price feeds. The bot creates limit orders on a specified trading pair (e.g., BTC/LTC) and monitors for third-party takers to execute those orders.

**Key Characteristics:**
- No external price ticker dependency (optional CCXT fallback for initial mid-price)
- Autonomous mid-price tracking based on own execution history
- Multiple concurrent orders (configurable 1-100)
- Partial order support
- Profit measured as accumulation of both TOKEN_A and TOKEN_B balances
- No-loss operation through smart pricing and inventory bias

---

## Architecture

### Class Hierarchy

```
BaseStrategy (definitions/strategies/base_strategy.py)
    │
    └── MakerStrategy (definitions/strategies/maker_strategy.py)
            │
            └── AutonomousMakerStrategy (strategies/autonomous_maker_strategy.py)
```

### Core Components

| Component | Responsibility |
|-----------|----------------|
| `AutonomousMakerStrategy` | Main strategy orchestrator |
| `OrderLadder` | Manages multiple price levels |
| `PricingEngine` | Calculates bid/ask prices |
| `InventoryManager` | Tracks balances, biases order placement |
| `TradeRecorder` | Persists execution history |
| `StateManager` | Handles state persistence |

### File Structure

```
strategies/
├── autonomous_maker_strategy.py   # Main strategy class
├── autonomous_order_ladder.py     # Order ladder management
├── autonomous_pricing_engine.py   # Price calculations
├── autonomous_inventory.py        # Balance tracking
├── autonomous_state.py           # Persistence layer

config/
└── config_autonomous_maker.yaml  # Strategy configuration

data/
├── autonomous_{pair}_state.yaml      # Mid-price, trade count
├── autonomous_{pair}_orders.yaml     # Open orders tracking
└── autonomous_{pair}_history.yaml    # Trade execution history
```

---

## Configuration

### Config File Structure

```yaml
# config/config_autonomous_maker.yaml

debug_level: 2
ttk_theme: darkly

pair_configs:
  - name: BTC_LTC_01
    enabled: true
    pair: BTC/LTC
    
    # === INITIAL BALANCES (Profit Reference) ===
    initial_balance_a: 0.5       # BTC (TOKEN_A)
    initial_balance_b: 10.0       # LTC (TOKEN_B)
    
    # === PRICING ===
    # Mid-price reference (TOKEN_B per TOKEN_A)
    # Example: 0.05 means 1 BTC = 20 LTC
    initial_mid_price: 0.05
    use_ccxt_fallback: true      # Use CCXT if initial_mid_price not set
    
    # === ORDER CONFIGURATION ===
    max_open_orders: 10          # 1-100, total buy+sell orders
    partial_percent: 0.1         # 0.0-1.0, min fill ratio
    
    # Order sizing modes:
    # - "equal"         : Same size all levels
    # - "growing_outward" : Larger at extremities
    # - "growing_inward"  : Larger near mid
    # - "manual"        : User-defined amounts per level
    order_sizing_mode: "growing_outward"
    
    # Manual amounts (optional, for "manual" mode)
    # buy_order_amounts: [0.1, 0.15, 0.2, 0.25, 0.3]
    # sell_order_amounts: [0.1, 0.15, 0.2, 0.25, 0.3]
    
    # === SPREAD CONFIGURATION ===
    # Spread modes: "exponential" | "linear"
    spread_mode: "exponential"
    base_spread_percent: 1.0     # Level 1 spread from mid (%)
    spread_multiplier: 2.0       # Each level multiplies by this
    
    # Alternative: linear settings
    # spread_mode: "linear"
    # base_spread_percent: 0.5
    # spread_increment: 0.5       # Each level adds this %
    
    # === INVENTORY BIAS ===
    # How to bias orders based on holdings:
    # - "auto"        : Rebalance toward smaller holding
    # - "balanced"    : Equal buy/sell pressure (50/50)
    # - "fixed_ratio" : Use target_ratio_a below
    inventory_bias: "auto"
    
    # Target ratio when inventory_bias is "fixed_ratio"
    # 0.5 = 50% portfolio value in TOKEN_A
    target_ratio_a: 0.5
    
    # === SAFETY ===
    # Minimum profit per trade (%)
    min_profit_percent: 0.1
    
    # Max price range from mid (%)
    max_price_range_percent: 50.0
    
    # === OPERATION ===
    # Seconds between cycle checks
    check_interval: 15
    
    # Cancel and recreate orders on price variation (%)
    price_variation_tolerance: 5.0
```

### Configuration Parameters Reference

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pair` | string | required | Trading pair (e.g., "BTC/LTC") |
| `initial_balance_a` | float | required | Initial TOKEN_A balance |
| `initial_balance_b` | float | required | Initial TOKEN_B balance |
| `initial_mid_price` | float | optional | Starting mid-price |
| `use_ccxt_fallback` | bool | true | Use CCXT if no mid-price |
| `max_open_orders` | int | 10 | Max total open orders |
| `partial_percent` | float | 0.1 | Min fill ratio (0.1 = 10%) |
| `order_sizing_mode` | string | "equal" | Order amount distribution |
| `spread_mode` | string | "exponential" | Spread scaling |
| `base_spread_percent` | float | 1.0 | Level 1 spread % |
| `spread_multiplier` | float | 2.0 | Exponential multiplier |
| `inventory_bias` | string | "auto" | Balance-based ordering |
| `target_ratio_a` | float | 0.5 | Target TOKEN_A ratio |
| `min_profit_percent` | float | 0.1 | Minimum profit per trade |
| `max_price_range_percent` | float | 50.0 | Max spread from mid |
| `check_interval` | int | 15 | Cycle interval (seconds) |
| `price_variation_tolerance` | float | 5.0 | Recreate on variation % |

---

## Core Logic

### State Machine

```
┌─────────────────┐
│     STARTUP     │  Load config, cancel existing orders
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   INITIALIZE    │  Fetch wallet balances, set mid-price
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│     CYCLE       │  (Repeats every check_interval)
│                 │
│  1. Fetch state│  - Current balances from XBridge
│  2. Check orders│  - Open orders status
│  3. Process fills│ - Record executions
│  4. Reconcile   │  - Cancel stale, create new
│  5. Rebalance   │  - Adjust for inventory bias
└────────┬────────┘
         │
         ▼
    ┌────┴────┐
    │  SHUTDOWN│  Cancel all orders, persist state
    └──────────┘
```

### Order Lifecycle

1. **Create Order**
   - Calculate price from pricing engine
   - Calculate amount from inventory manager
   - Call `xbridge_manager.makepartialorder()` or `makeorder()`
 ID mapped   - Store order to level

2. **Monitor Order**
   - Poll `xbridge_manager.getorderstatus(order_id)`
   - Statuses: open, new, finished, expired, canceled, invalid

3. **Order Filled**
   - Record trade: side, amount, price, timestamp
   - Update current balances
   - Update mid_price = executed price
   - Trigger rebalance calculation
   - Persist state

4. **Order Canceled/Stale**
   - Remove from active orders
   - Mark level as available
   - Reconcile to maintain max_open_orders

### Pricing Engine

#### Mid-Price Tracking

```
initial_mid_price = config.initial_mid_price OR ccxt_fallback
after_execution: mid_price = executed_price
```

#### Price Level Calculation

**Exponential Mode:**
```
level_n_buy_price  = mid_price * (1 - base_spread * (multiplier ^ (n-1)))
level_n_sell_price = mid_price * (1 + base_spread * (multiplier ^ (n-1)))
```

Example with base=1%, multiplier=2, levels=5:
| Level | Buy Price (from mid) | Sell Price (from mid) |
|-------|---------------------|----------------------|
| 1     | -1.0%               | +1.0%                |
| 2     | -2.0%               | +2.0%                |
| 3     | -4.0%               | +4.0%                |
| 4     | -8.0%               | +8.0%                |
| 5     | -16.0%              | +16.0%               |

**Linear Mode:**
```
level_n_buy_price  = mid_price * (1 - base_spread - (increment * (n-1)))
level_n_sell_price = mid_price * (1 + base_spread + (increment * (n-1)))
```

#### Order Sizing Modes

**Equal:**
```
amount_per_order = available_balance / remaining_levels
```

**Growing Outward (away from mid):**
```
level_1_amount = base_amount * 1.0
level_2_amount = base_amount * 1.5
level_3_amount = base_amount * 2.0
...
```

**Growing Inward (toward mid):**
```
level_1_amount = base_amount * 2.0
level_2_amount = base_amount * 1.5
level_3_amount = base_amount * 1.0
...
```

---

## Inventory Management

### Balance Tracking

```python
class InventoryManager:
    initial_balance_a: float  # From config
    initial_balance # From config
_b: float     current_balance_a: float  # From XBridge
    current_balance_b: float  # From XBridge
    
    @property
    def profit_a(self) -> float:
        return self.current_balance_a - self.initial_balance_a
    
    @property
    def profit_b(self) -> float:
        return self.current_balance_b - self.initial_balance_b
    
    @property
    def total_profit(self) -> tuple[float, float]:
        return (self.profit_a, self.profit_b)
```

### Inventory Bias Logic

```python
def calculate_order_bias(self) -> dict:
    """
    Returns dict with 'buy_pressure' and 'sell_pressure' (0.0-1.0)
    """
    if self.inventory_bias == "balanced":
        return {"buy_pressure": 0.5, "sell_pressure": 0.5}
    
    elif self.inventory_bias == "auto":
        ratio_a = self.current_balance_a / (self.current_balance_a + self.current_balance_b / self.mid_price)
        
        if ratio_a > self.target_ratio_a:
            # Too much A, bias toward selling A (buy orders)
            deficit = ratio_a - self.target_ratio_a
            buy_pressure = 0.5 + deficit
            sell_pressure = 0.5 - deficit
        else:
            # Too much B, bias toward buying A (sell orders)
            deficit = self.target_ratio_a - ratio_a
            buy_pressure = 0.5 - deficit
            sell_pressure = 0.5 + deficit
        
        return {
            "buy_pressure": clamp(buy_pressure, 0.1, 0.9),
            "sell_pressure": clamp(sell_pressure, 0.1, 0.9)
        }
    
    elif self.inventory_bias == "fixed_ratio":
        return {"buy_pressure": 1 - self.target_ratio_a, "sell_pressure": self.target_ratio_a}
```

### No-Loss Guarantee

The strategy ensures no-loss operation through:

1. **Price Gating**: Orders only placed within profitable range
2. **Inventory Bias**: More orders on side that increases smaller holding
3. **Mid-Price Update**: After any execution, mid-price = executed price (locks in profit)
4. **Min Profit Check**: Validate execution price against last order on opposite side

```python
def validate_profitable_execution(self, trade: Trade) -> bool:
    """
    Check if execution is profitable vs last opposite-side order
    """
    last_opposite = self.order_ladder.get_last_order(trade.side.opposite)
    
    if trade.side == SELL:
        # Must sell higher than last buy
        return trade.price >= last_opposite.price * (1 + min_profit_percent)
    else:
        # Must buy lower than last sell
        return trade.price <= last_opposite.price * (1 - min_profit_percent)
```

---

## State Management

### Persistence Files

#### State File (`autonomous_{pair}_state.yaml`)

```yaml
mid_price: 0.050123
trade_count: 42
last_updated: "2024-01-15T10:30:00Z"
initial_mid_price: 0.05
config_hash: "sha256_of_config"
```

#### Orders File (`autonomous_{pair}_orders.yaml`)

```yaml
open_orders:
  - id: "order_uuid_1"
    level: 1
    side: "buy"
    price: 0.0495
    amount: 0.1
    created_at: "2024-01-15T10:00:00Z"
  - id: "order_uuid_2"
    level: 2
    side: "sell"
    price: 0.0505
    amount: 0.15
    created_at: "2024-01-15T10:01:00Z"
```

#### History File (`autonomous_{pair}_history.yaml`)

```yaml
trades:
  - id: 1
    side: "buy"
    amount: 0.1
    price: 0.0498
    fee: 0.0001
    timestamp: "2024-01-15T09:45:00Z"
    balance_a_after: 0.4
    balance_b_after: 10.2
  - id: 2
    side: "sell"
    amount: 0.1
    price: 0.0502
    fee: 0.0001
    timestamp: "2024-01-15T10:15:00Z"
    balance_a_after: 0.5
    balance_b_after: 9.9
```

### State Transitions

```
┌──────────────────────────────────────────────────────────────┐
│                        STATE FILE                           │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  STARTUP                                                     │
│    │                                                         │
│    ▼                                                         │
│  Load state.yaml ──► exists? ──Yes──► Load mid_price         │
│    │                              │                          │
│    No                             No                         │
│    │                              ▼                          │
│    ▼                      Use initial_mid_price              │
│  Fetch wallet balances                                      │
│    │                                                         │
│    ▼                                                         │
│  ACTIVE                                                      │
│    │                                                         │
│    ├──► Order Filled ──► Update balances                    │
│    │                     Update mid_price                   │
│    │                     Increment trade_count              │
│    │                     Write state.yaml                    │
│    │                                                         │
│    ├──► Order Cancelled ──► Remove from orders.yaml         │
│    │                           Reconcile ladder              │
│    │                                                         │
│    └──► Check Interval ──► Rebalance if needed              │
│                                Write state.yaml              │
│                                                              │
│  SHUTDOWN                                                    │
│    │                                                         │
│    ▼                                                         │
│  Cancel all open orders                                     │
│  Write final state.yaml                                     │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## Integration Points

### XBridge Manager Integration

| Method | Usage |
|--------|-------|
| `gettokenbalances()` | Fetch current A/B balances |
| `dxgetorderbook(detail, maker, taker)` | Optional: for orderbook display |
| `getmyorders()` | List open orders for pair |
| `getorderstatus(order_id)` | Check order state |
| `makeorder()` | Create exact order |
| `makepartialorder()` | Create partial-fill order |
| `cancelorder(order_id)` | Cancel specific order |
| `cancelallorders()` | Cancel all (startup cleanup) |

### Config Manager Integration

```python
class AutonomousMakerStrategy(MakerStrategy):
    def initialize_strategy_specifics(self, **kwargs):
        self.config_autonomous = self.config_manager.config_autonomous_maker
        
    def get_tokens_for_initialization(self, **kwargs) -> list:
        tokens = set()
        for cfg in self.config_autonomous.pair_configs:
            if cfg.get("enabled", True):
                t1, t2 = cfg["pair"].split("/")
                tokens.add(t1)
                tokens.add(t2)
        return list(tokens)
```

### Controller Integration

The strategy runs within the existing controller framework:
- Uses `controller.shutdown_event` for graceful shutdown
- Uses `controller.disabled_coins` for circuit breaker integration
- Periodic cycle via `get_operation_interval()`

---

## Error Handling

### Error Categories

| Category | Handling |
|----------|----------|
| RPC Connection | Retry with circuit breaker |
| Order Creation Failed | Log error, skip level, continue |
| Order Fill Error | Mark trade, update state, rebalance |
| Balance Fetch Failed | Use cached balance, log warning |
| State Write Failed | Retry, critical error if persistent |

### Recovery Procedures

1. **Startup**: Cancel all existing orders before initializing
2. **Mid-Cycle Failure**: Log error, continue with next step
3. **Order Stuck**: Check status, cancel if expired, recreate
4. **State Corruption**: Reset to initial balances, start fresh

---

## Testing Strategy

### Unit Tests

Unit tests validate individual components in isolation:

```python
# test_units/test_autonomous_maker_strategy.py
# test_units/test_autonomous_pricing_engine.py
# test_units/test_autonomous_inventory.py
```

#### Test Coverage

| Component | Tests |
|-----------|-------|
| `PricingEngine` | Price calculations, spread modes, level generation |
| `InventoryManager` | Bias calculations, balance tracking, profit calculation |
| `OrderLadder` | Order creation, level mapping, reconciliation |
| `StateManager` | File persistence, state loading, migration |

#### Test Patterns

Following existing `PingPongStrategyTester` pattern:

```python
class AutonomousMakerTester:
    """Test helper class for strategy testing"""
    
    def __init__(self, strategy_instance):
        self.strategy = strategy_instance
        
    @contextmanager
    def _patch_dependencies(self):
        """Mock all external dependencies"""
        with (
            patch.object(xbridge_manager, "makeorder", AsyncMock(...)),
            patch.object(xbridge_manager, "getorderstatus", AsyncMock(...)),
            patch.object(xbridge_manager, "gettokenbalances", AsyncMock(...)),
            patch("yaml.safe_load", ...),
            patch("yaml.safe_dump", ...),
        ):
            yield mocks
```

### Integration Tests

- Full cycle with mock XBridge
- Order creation flow
- State file read/write
- Inventory bias behavior
- Multiple order fills and rebalancing

### Manual Tests

- Start with testnet XBridge
- Verify order placement
- Simulate fills
- Verify state persistence

---

## Backtesting Engine

### Overview

The backtesting engine simulates strategy performance over historical price data to evaluate financial results before live deployment.

### Architecture

```
backtesting/
├── __init__.py
├── price_feed.py          # Data fetching (yfinance)
├── price_aggregator.py    # Cross-pair calculation
├── simulator.py          # Order execution simulation
├── engine.py             # Main backtesting engine
├── reporter.py           # Results generation
└── config.py             # Backtest configuration
```

### Price Data Pipeline

#### 1. Data Fetching (yfinance)

```python
# Direct pairs (e.g., BTC/USD)
data = yf.download("BTC-USD", start="2023-01-01", end="2024-01-01")

# Returns: DataFrame with Open, High, Low, Close, Volume, Adj Close
```

#### 2. Cross-Pair Aggregation

For pairs not directly available (e.g., LTC/DOGE):

```python
class PriceAggregator:
    """
    Aggregates two trading pairs to create a synthetic pair.
    
    Example: LTC/DOGE from LTC/USD and DOGE/USD
    
    LTC/DOGE = (LTC/USD) / (DOGE/USD)
    """
    
    def get_synthetic_pair(self, base: str, quote: str) -> pd.DataFrame:
        """
        Calculate synthetic price: base/quote
        
        If direct pair exists (e.g., LTC/DOGE): use it
        If not: calculate from common denominators:
            - base/USD and quote/USD
            - base/BTC and quote/BTC
            - etc.
        """
        # Try direct
        if self._pair_exists(f"{base}-{quote}"):
            return self._fetch_direct(f"{base}-{quote}")
        
        # Try inverse  
        if self._pair_exists(f"{quote}-{base}"):
            return 1 / self._fetch_direct(f"{quote}-{base}")
        
        # Aggregate via USD
        base_usd = self._fetch_direct(f"{base}-USD")
        quote_usd = self._fetch_direct(f"{quote}-USD")
        
        # LTC/DOGE = (LTC/USD) / (DOGE/USD)
        synthetic = base_usd["Close"] / quote_usd["Close"]
        
        return pd.DataFrame({
            "Open": base_usd["Open"] / quote_usd["Open"],
            "High": base_usd["High"] / quote_usd["High"], 
            "Low": base_usd["Low"] / quote_usd["Low"],
            "Close": synthetic,
            "Volume": (base_usd["Volume"] + quote_usd["Volume"]) / 2,
        })
```

#### 3. Available Pairs on yfinance

Common UTXO pairs available:

| Direct Pairs | Notes |
|--------------|-------|
| BTC-USD, LTC-USD, DOGE-USD | Direct USD pairs |
| LTC-BTC, ETH-BTC | Crypto/crypto pairs |
| DASH-USD, PIVX-USD | May not be available |

**Aggregation fallback matrix:**

| Target Pair | Calculation |
|-------------|-------------|
| LTC/DOGE | (LTC/USD) / (DOGE/USD) |
| BTC/LTC | Use LTC-BTC or (BTC/USD)/(LTC/USD) |
| DASH/LTC | (DASH/USD)/(LTC/USD) |
| PIVX/DOGE | (PIVX/USD)/(DOGE/USD) |

### Simulation Engine

#### Core Simulation Loop

```python
class BacktestEngine:
    """
    Runs the strategy against historical price data.
    """
    
    def __init__(self, strategy_config: dict, price_data: pd.DataFrame):
        self.config = strategy_config
        self.prices = price_data
        self.state = self._create_initial_state()
        self.trades = []
        self.order_ladder = OrderLadder(...)
        
    def run(self) -> BacktestResults:
        """
        Execute backtest over entire price history.
        
        For each timestamp:
        1. Get current price
        2. Check if any orders are crossed (price touches)
        3. Execute crossed orders, update balances
        4. Rebalance if needed
        5. Continue
        """
        for idx, row in self.prices.iterrows():
            current_price = row["Close"]
            current_time = idx
            
            # Check for order executions
            executions = self._check_executions(current_price, current_time)
            
            for exec in executions:
                self._process_execution(exec)
                
            # Rebalance periodically or after fills
            if self._should_rebalance():
                self._rebalance()
                
        return self._generate_results()
    
    def _check_executions(self, current_price: float, timestamp) -> list[Execution]:
        """
        Check if current price crosses any of our orders.
        
        BUY order (bid): executes when price <= order.price
        SELL order (ask): executes when price >= order.price
        """
        executions = []
        
        for order in self.order_ladder.active_orders:
            if order.side == "buy" and current_price <= order.price:
                executions.append(Execution(
                    order=order,
                    price=current_price,  # Execute at current market price
                    amount=order.amount,
                    timestamp=timestamp,
                ))
            elif order.side == "sell" and current_price >= order.price:
                executions.append(Execution(
                    order=order,
                    price=current_price,
                    amount=order.amount,
                    timestamp=timestamp,
                ))
                
        return executions
    
    def _process_execution(self, execution: Execution):
        """
        Handle order execution:
        - Update balances
        - Record trade
        - Update mid_price = executed price
        - Mark order as filled
        """
        order = execution.order
        
        if order.side == "sell":
            # Sold TOKEN_A, received TOKEN_B
            received = execution.amount * execution.price
            self.state.balance_a -= execution.amount
            self.state.balance_b += received
        else:
            # Bought TOKEN_A, spent TOKEN_B
            spent = execution.amount * execution.price
            self.state.balance_a += execution.amount
            self.state.balance_b -= spent
            
        # CRITICAL: Mid price becomes executed price
        self.state.mid_price = execution.price
        
        # Record trade
        self.trades.append(Trade(
            side=order.side,
            amount=execution.amount,
            price=execution.price,
            timestamp=execution.timestamp,
            balance_a_after=self.state.balance_a,
            balance_b_after=self.state.balance_b,
        ))
        
        # Remove filled order
        self.order_ladder.remove_order(order.id)
```

#### Order Price Crossing Logic

```
Price Timeline (increasing):

     SELL Order @ 0.052
           │
           ▼
    ──────────────────── 0.051 (current price crosses here!)
           │
     SELL Order @ 0.050
           │
    ──────────────────── 0.049 (current price)
           │
     BUY Order @ 0.048
           │
    ──────────────────── 0.047 (current price)
           │
     BUY Order @ 0.046
```

When current price >= SELL order price → SELL executes
When current price <= BUY order price → BUY executes

### Results Reporter

```python
@dataclass
class BacktestResults:
    initial_balance_a: float
    initial_balance_b: float
    final_balance_a: float
    final_balance_b: float
    
    total_trades: int
    buy_trades: int
    sell_trades: int
    
    profit_a: float
    profit_b: float
    
    # Calculated in TOKEN_A equivalent
    profit_a_equivalent: float
    total_return_percent: float
    
    # Trade analysis
    avg_trade_size: float
    max_single_trade_profit: float
    min_single_trade_profit: float
    
    # Time analysis
    start_date: datetime
    end_date: datetime
    days_elapsed: int
    
    # Equity curve
    equity_curve: list[EquityPoint]  # Timestamp, balance_a, balance_b
    
    # Trade history
    trades: list[Trade]


class Reporter:
    def generate_report(self, results: BacktestResults) -> str:
        """Generate human-readable backtest report"""
        
    def export_csv(self, results: BacktestResults, path: str):
        """Export trade history to CSV"""
        
    def export_charts(self, results: BacktestResults, output_dir: str):
        """Generate equity curve and trade distribution charts"""
```

#### Sample Output

```
══════════════════════════════════════════════════════════════
                    BACKTEST RESULTS
══════════════════════════════════════════════════════════════

Configuration:
  Pair:           LTC/DOGE
  Initial A:      5.0 LTC
  Initial B:      5000.0 DOGE
  Initial Price:  1000.0 DOGE/LTC
  Max Orders:     10
  Spread Mode:    exponential (1.0%, 2x)
  Sizing:         growing_outward
  Bias:           auto

Period:
  Start:          2023-01-01
  End:            2024-01-01
  Duration:       365 days

Results:
  ┌─────────────────────────────────────────────────────────┐
  │  FINAL BALANCES                                        │
  ├─────────────────────────────────────────────────────────┤
  │  TOKEN A (LTC):  5.8234 (+0.8234, +16.47%)             │
  │  TOKEN B (DOGE): 5847.29 (+847.29, +16.95%)           │
  └─────────────────────────────────────────────────────────┘

  Total Trades:        247
  Buy Trades:          123
  Sell Trades:         124
  
  Avg Trade Size:      0.056 LTC
  Largest Trade:      0.234 LTC
  Smallest Trade:     0.012 LTC
  
  Total Return:       33.42% (in TOKEN_A equivalent)
  Daily Avg Return:   0.092%

  Max Drawdown:       -8.34%
  Best Single Trade:  +0.234 LTC
  Worst Single Trade: -0.012 LTC

══════════════════════════════════════════════════════════════
```

### Backtest Configuration

```yaml
# config/backtest_autonomous_maker.yaml

backtest:
  # Price data
  pair: LTC/DOGE
  start_date: "2023-01-01"
  end_date: "2024-01-01"
  
  # Data source
  data_source: "yfinance"  # or "csv" | "xbridge_history"
  
  # CSV input (if data_source: csv)
  # csv_path: "data/prices.csv"
  # csv_price_column: "close"
  
  # Strategy config (mirrors main config)
  initial_balance_a: 5.0
  initial_balance_b: 5000.0
  initial_mid_price: 1000.0
  max_open_orders: 10
  partial_percent: 0.1
  order_sizing_mode: "growing_outward"
  spread_mode: "exponential"
  base_spread_percent: 1.0
  spread_multiplier: 2.0
  inventory_bias: "auto"
  target_ratio_a: 0.5
  
  # Execution settings
  execution_mode: "close"  # "close" | "high_low" | "ohlc"
  # close:      Execute at bar close price
  # high_low:  Check if high/low crosses order
  # olc:       Execute at open, check high/low for stops
  
  # Output
  output_format: "report"  # "report" | "csv" | "json"
  output_file: "backtest_results.txt"
  generate_charts: true
  chart_dir: "backtest_charts/"
```

### Execution Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `close` | Execute at bar close price | Standard backtesting |
| `high_low` | Check if H/L crosses order | Conservative estimate |
| `ohlc` | Execute at open, track H/L for stops | More realistic |

### CLI Usage

```bash
# Run backtest with config
python -m backtesting.engine --config config/backtest_autonomous_maker.yaml

# Run with inline parameters
python -m backtesting.engine \
  --pair LTC/DOGE \
  --start 2023-01-01 \
  --end 2024-01-01 \
  --initial-a 5.0 \
  --initial-b 5000 \
  --mid-price 1000 \
  --orders 10 \
  --spread-mode exponential

# Export results
python -m backtesting.engine --config ... --export-csv trades.csv
```

### Integration with Main Strategy

The backtesting engine reuses strategy components:

```
┌─────────────────────────────────────────────────────────────┐
│                      BACKTEST ENGINE                        │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐     │
│  │ PriceFeed   │   │ Strategy    │   │ XBridge     │     │
│  │ (yfinance)  │──▶│ Logic       │◀──│ Mock        │     │
│  └─────────────┘   │ (shared)    │   │ (simulated)│     │
│                    └─────────────┘   └─────────────┘     │
│                         │                                  │
│                    ┌────▼─────┐                            │
│                    │Reporter  │                            │
│                    │(results) │                            │
│                    └──────────┘                            │
└─────────────────────────────────────────────────────────────┘
```

Components shared between live strategy and backtest:
- `PricingEngine` - same price calculations
- `InventoryManager` - same balance logic
- `OrderLadder` - same order management
- State machine - same flow

Only differences:
- `XBridgeManager` → `MockXBridgeManager` (no RPC calls)
- Price source: yfinance instead of live
- Execution: simulated vs real

---

## Implementation Roadmap

### Phase 1: Core Infrastructure
- [ ] Create `autonomous_maker_strategy.py`
- [ ] Implement config loading
- [ ] Implement state management
- [ ] Basic order creation (single order)

### Phase 2: Order Ladder
- [ ] Implement `OrderLadder` class
- [ ] Implement `PricingEngine`
- [ ] Multiple order levels
- [ ] Order reconciliation

### Phase 3: Inventory Management
- [ ] Implement `InventoryManager`
- [ ] Inventory bias logic
- [ ] Rebalancing on fill

### Phase 4: Polish
- [ ] No-loss validation
- [ ] Error handling
- [ ] Logging improvements
- [ ] Config validation

### Phase 5: Testing - Unit & Integration
- [ ] Unit tests for PricingEngine
- [ ] Unit tests for InventoryManager  
- [ ] Unit tests for OrderLadder
- [ ] Integration tests (full cycle with mocks)
- [ ] Test `AutonomousMakerStrategyTester` class

### Phase 6: Backtesting Engine
- [ ] Create `backtesting/` module
- [ ] Implement PriceFeed (yfinance integration)
- [ ] Implement PriceAggregator (cross-pair calculation)
- [ ] Implement Simulator (execution logic)
- [ ] Implement Engine (main loop)
- [ ] Implement Reporter (results output)
- [ ] CLI interface
- [ ] Chart generation

---

## Appendix

### A. Example Config

```yaml
# Full working config example
strategy: autonomous_maker

pair_configs:
  - name: LTC_DOGE_01
    enabled: true
    pair: LTC/DOGE
    initial_balance_a: 5.0      # LTC
    initial_balance_b: 5000.0   # DOGE
    initial_mid_price: 1000.0   # DOGE per LTC
    max_open_orders: 10
    partial_percent: 0.1
    order_sizing_mode: "growing_outward"
    spread_mode: "exponential"
    base_spread_percent: 1.0
    spread_multiplier: 2.0
    inventory_bias: "auto"
    target_ratio_a: 0.5
    check_interval: 15
```

### B. Profit Calculation

```
Profit = (current_balance_A - initial_balance_A) + 
          (current_balance_B - initial_balance_B) * mid_price

Example:
  Initial: 1.0 BTC, 20.0 LTC (mid=0.05 BTC/LTC = 20 LTC/BTC)
  Current: 1.2 BTC, 18.0 LTC
  
  Profit A = 1.2 - 1.0 = +0.2 BTC
  Profit B = 18.0 - 20.0 = -2.0 LTC = -0.1 BTC equivalent
  
  Total Profit = +0.2 - 0.1 = +0.1 BTC
```

### C. Orderbook Visualization

```
                    SELL ORDERS (LTC → DOGE)
                    ─────────────────────────
    Price (DOGE)    Amount (LTC)    Level
    1600            0.05            5   ▲
    1400            0.08            4   │
    1200            0.10            3   │
    1100            0.12            2   │
    1050            0.15            1   │
    ───────────────────────────────────────
    1000 (MID)      -               -
    ───────────────────────────────────────
    950             0.15            1   │
    900             0.12            2   │
    800             0.10            3   │
    600             0.08            4   │
    200             0.05            5   ▼
                    BUY ORDERS (DOGE → LTC)
```

### D. Backtest Sample Price Aggregation

```
Pair: DASH/LTC (not directly available on yfinance)

Available on yfinance:
  - DASH-USD
  - LTC-USD

Calculation:
  DASH/LTC = DASH-USD / LTC-USD
  
  If DASH-USD = $50.00
  And     LTC-USD = $80.00
  
  Then DASH/LTC = 50/80 = 0.625 LTC per DASH
  
  Or inverted: LTC/DASH = 80/50 = 1.6 DASH per LTC
```

---

*Document Version: 2.0*  
*Last Updated: 2024-01-15*  
*Author: Strategy Design*
