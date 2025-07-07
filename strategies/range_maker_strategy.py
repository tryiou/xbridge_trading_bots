"""
Autonomous range-based market maker inspired by Uniswap V3 mechanics,
adapted for XBridge's order book model.

This strategy emulates a Uniswap V3-style liquidity pool by creating
concentrated liquidity positions via XBridge limit orders. Users provide:
- Asset1/Asset2 pair with XBridge balances
- Min/Max price range for liquidity concentration
- Number of orders (grid density) within the price range

The bot:
1. Allocates funds algorithmically across buy/sell orders within the price range
2. Automatically creates counter-orders when orders fill to maintain liquidity
3. Purely relies on market-making mechanics - no external price signals
4. Extracts profit through:
  - Buy orders filled below fair value
  - Sell orders filled above fair value


External traders (arbitrageurs) trigger orders when profitable, allowing the
strategy to function as a self-contained auction mechanism resembling an AMM.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np

from definitions.logger import setup_logging
from strategies.base_strategy import BaseStrategy


@dataclass
class RangePosition:
    token_pair: str
    min_price: float
    max_price: float
    grid_density: int
    profit_margin: float = 0.002  # Default 0.2% profit margin
    curve: str = 'linear'
    curve_strength: float = 10  # Steepness of sigmoid curve
    fee_accumulated: float = 0.0
    created_at: datetime = datetime.now()


class RangeMakerStrategy(BaseStrategy):
    """
    Implements concentrated liquidity ranges with active order book management.
    Manages a grid of limit orders within user-defined price bounds to:
    1. Emulate Uniswap V3-like liquidity provisioning
    2. Generate profit through automated market making
    
    """

    def __init__(self, config_manager, controller=None):
        super().__init__(config_manager, controller)
        self.logger = setup_logging(name="range_maker", level=logging.DEBUG, console=True)
        self.active_positions: Dict[str, RangePosition] = {}
        self.order_grids: Dict[str, dict] = {}
        self.historical_metrics = {
            'daily_volume': [],
            'fee_income': [],
            'inventory_changes': []
        }
        self.pairs = {}
        self.logger.info("RangeMakerStrategy initialized")

    def initialize_strategy_specifics(self, **kwargs):
        """Configures range parameters for each pair"""
        # Only proceed if 'pair' is provided in kwargs, otherwise it's likely an initial call from ConfigManager
        if 'pair' not in kwargs:
            self.logger.debug(
                "Skipping initialize_strategy_specifics call as 'pair' argument is missing. This is expected during initial ConfigManager setup.")
            return

        try:
            pos = RangePosition(
                token_pair=kwargs['pair'],
                min_price=kwargs['min_price'],
                max_price=kwargs['max_price'],
                grid_density=kwargs['grid_density'],
                curve=kwargs.get('curve', 'linear'),
                curve_strength=kwargs.get('curve_strength', 10)
            )
            self.active_positions[pos.token_pair] = pos
            self.logger.info(f"Added position: {pos.token_pair} - price range [{pos.min_price}, {pos.max_price}], "
                             f"{pos.grid_density} orders, curve: {pos.curve}")
        except Exception as e:
            self.logger.error(f"Error initializing position: {e}")

    def get_tokens_for_initialization(self, **kwargs) -> list:
        """Extract unique tokens from all configured pairs"""
        tokens = set()
        for pair in kwargs.get('pairs', []):
            t1, t2 = pair['pair'].split('/')
            tokens.update([t1, t2])
        return list(tokens)

    def get_pairs_for_initialization(self, tokens_dict, **kwargs) -> dict:
        """Create Pair instances for all configured ranges"""
        from definitions.pair import Pair
        pairs = {}
        for pair_cfg in kwargs.get('pairs', []):
            t1, t2 = pair_cfg['pair'].split('/')
            # Use pair symbol as name to preserve program structure
            # This matches how other strategies implement the 'name'
            pair_name = pair_cfg['pair']
            # Ensure the cfg has 'name' field for Pair initialization
            pair_cfg_with_name = {**pair_cfg, 'name': pair_name}
            pairs[pair_name] = Pair(
                token1=tokens_dict[t1],
                token2=tokens_dict[t2],
                cfg=pair_cfg_with_name,
                strategy="range_maker",
                config_manager=self.config_manager
            )
        return pairs

    async def thread_loop_async_action(self, pair_instance):
        """Main auction management loop - processes orders and handles rebalancing"""
        pair_key = pair_instance.symbol
        position = self.active_positions.get(pair_key)

        if not position:
            self.logger.warning(f"No position found for {pair_key}, skipping cycle")
            return

        # Initialize grid on first run
        if pair_key not in self.order_grids:
            self.logger.info(f"First run for {pair_key}, initializing grid")
            await self.initialize_order_grid(pair_instance, position)

        # Monitor order fills and update the grid
        filled_orders = await self.process_order_updates(pair_instance)
        if filled_orders:
            self.logger.info(f"Processed {len(filled_orders)} fills for {pair_key}")

        # No rebalancing needed

    async def initialize_order_grid(self, pair_instance, position):
        """Creates the initial set of auction orders (buy/sell) within the price range"""
        self.logger.info(f"Initializing order grid for {pair_instance.symbol}")
        buy_orders, sell_orders = self.calculate_grid_allocations(
            pair_instance, position
        )

        self.logger.info(
            f"Generated {len(buy_orders)} buy orders and {len(sell_orders)} sell orders for {pair_instance.symbol}")

        all_orders = buy_orders + sell_orders
        self.logger.info(
            f"SIMULATED ORDER PLACEMENT - placing {len(all_orders)} orders for {pair_instance.symbol}")

        self.order_grids[pair_instance.symbol] = {
            'buy': {o['price']: o for o in buy_orders},
            'sell': {o['price']: o for o in sell_orders},
            'active_orders': all_orders
        }
        self.logger.info(
            f"Order grid initialized with {len(buy_orders)} buy orders and {len(sell_orders)} sell orders.")

    def calculate_grid_allocations(self, pair_instance, position) -> Tuple[List[dict], List[dict]]:
        """Distributes funds algorithmically across the price range"""
        self.logger.info(f"Calculating grid allocations for {pair_instance.symbol} with position: {position}")
        self.logger.debug(
            f"Position details: Min Price={position.min_price}, Max Price={position.max_price}, Grid Density={position.grid_density}, Curve={position.curve}, Curve Strength={position.curve_strength}")

        # Validate price range
        if position.min_price <= 0 or position.max_price <= position.min_price:
            self.logger.error(f"Invalid price range: min_price={position.min_price}, max_price={position.max_price}")
            raise ValueError(f"Invalid price range: min_price={position.min_price}, max_price={position.max_price}")
        self.logger.debug("Price range validated.")

        # Calculate price points and weights based on curve type
        prices = np.linspace(position.min_price, position.max_price, position.grid_density)
        mid_price = (position.min_price + position.max_price) / 2
        self.logger.debug(f"Generated {len(prices)} price points. Mid price: {mid_price:.6f}")

        if position.curve == 'linear':
            weights = np.linspace(1, 0, position.grid_density)
            self.logger.debug("Using linear curve for weights.")
        elif position.curve == 'exp_decay':
            weights = np.exp(-np.abs(prices - mid_price))
            self.logger.debug("Using exponential decay curve for weights.")
        elif position.curve == 'sigmoid':
            k = position.curve_strength  # Steepness parameter
            weights = 1 / (1 + np.exp(-k * (prices - mid_price) / (position.max_price - position.min_price)))
            self.logger.debug(f"Using sigmoid curve for weights with strength: {k}.")
        elif position.curve == 'constant_product':
            weights = 1 / (prices ** 2)
            weights /= weights.sum()
            self.logger.debug("Using constant product curve for weights.")
        else:
            weights = np.ones(position.grid_density)
            self.logger.warning(f"Unknown curve type '{position.curve}'. Using uniform weights.")

        # Normalize weights to sum to 1
        initial_sum = weights.sum()
        weights /= weights.sum()
        self.logger.debug(f"Weights normalized. Original sum: {initial_sum:.6f}, New sum: {weights.sum():.6f}")

        buy_orders = []
        sell_orders = []
        t1_balance = pair_instance.t1.dex.free_balance
        t2_balance = pair_instance.t2.dex.free_balance
        self.logger.debug(
            f"Current balances: {pair_instance.t1.symbol}={t1_balance:.6f}, {pair_instance.t2.symbol}={t2_balance:.6f}")

        # Create buy orders below mid-price (funded with quote token T2)
        for i, (price, weight) in enumerate(zip(prices, weights)):
            if price < mid_price:
                maker_size = t2_balance * weight
                min_order_size = 0.01  # Minimum order size in quote currency
                if price <= 0 or maker_size < min_order_size:
                    self.logger.debug(
                        f"Skipping buy order {i}: price={price:.6f} or maker_size={maker_size:.6f} below minimum ({min_order_size}).")
                    continue  # Prevent tiny or invalid orders
                taker_size = maker_size / price
                buy_orders.append({
                    'maker': pair_instance.t2.symbol,
                    'maker_size': maker_size,
                    'taker': pair_instance.t1.symbol,
                    'taker_size': taker_size,
                    'price': price,
                    'type': 'buy'
                })
            # Create sell orders above mid-price (funded with base token T1)
            else:
                maker_size = t1_balance * weight
                min_order_size = 0.01 / price  # Minimum order size in base currency
                if maker_size < min_order_size:
                    self.logger.debug(
                        f"Skipping sell order {i}: maker_size={maker_size:.6f} below minimum ({min_order_size}).")
                    continue  # Prevent tiny orders
                taker_size = maker_size * price
                sell_orders.append({
                    'maker': pair_instance.t1.symbol,
                    'maker_size': maker_size,
                    'taker': pair_instance.t2.symbol,
                    'taker_size': taker_size,
                    'price': price,
                    'type': 'sell'
                })
        self.logger.info(
            f"Finished calculating grid allocations. Generated {len(buy_orders)} buy orders and {len(sell_orders)} sell orders.")
        return buy_orders, sell_orders

    async def place_grid_orders(self, pair_instance, orders: List[dict]):
        """Batch order placement with XBridgeManager"""
        self.logger.info(f"Placing {len(orders)} orders for {pair_instance.symbol}")
        try:
            # XBridge calls commented out for beta development
            # result = await self.config_manager.xbridge_manager.place_grid_orders(
            #     pair_instance.symbol,
            #     orders
            # )
            result = [{"id": f"SIMULATED_ORDER_{i}", "status": "simulated", **order} for i, order in
                      enumerate(orders)]  # Simulation for testing, include order details
            self.logger.debug(f"Simulated order placement result: {result}")
            self.logger.info(f"Successfully simulated placement of {len(result)} orders.")
            return result
        except Exception as e:
            self.logger.error(f"Error placing orders: {str(e)}", exc_info=True)
            raise

    async def process_order_updates(self, pair_instance, current_price: float = None):
        """Monitors active orders and processes fills:
        1. Updates the active order list
        2. Trigger counter-orders for filled positions
        3. Records trading metrics
        """
        if pair_instance is None:
            self.logger.error("Method process_order_updates received None for pair_instance")
            return []

        if not hasattr(pair_instance, 'symbol'):
            self.logger.error("pair_instance is missing 'symbol' attribute")
            return []

        symbol = pair_instance.symbol

        if symbol not in self.order_grids:
            self.logger.warning(f"No order grid found for {symbol}. Skipping order updates.")
            return []

        active_orders = self.order_grids[symbol]['active_orders']
        # self.logger.debug(f"Currently {len(active_orders)} active orders for {symbol}.") # Too spammy
        filled = []
        if current_price is not None:  # Backtesting mode
            # self.logger.debug(f"Backtesting mode: Checking for fills against current price {current_price:.6f}.") # Too spammy
            for order in active_orders:
                is_filled = False
                if order['type'] == 'buy' and current_price <= order['price']:
                    is_filled = True
                    # self.logger.debug(f"  Buy order filled: Price={order['price']:.6f}, Current Price={current_price:.6f}") # Too spammy
                elif order['type'] == 'sell' and current_price >= order['price']:
                    is_filled = True
                    # self.logger.debug(f"  Sell order filled: Price={order['price']:.6f}, Current Price={current_price:.6f}") # Too spammy

                if is_filled:
                    filled.append(order)
        else:  # Live trading mode
            self.logger.debug("Live trading mode: Checking for actual filled orders.")
            filled = [o for o in active_orders if o.get('status') == 'filled']

        if filled:
            self.logger.debug(f"Detected {len(filled)} filled orders for {symbol}.")
            for order in filled:
                self.logger.debug(
                    f"Filled order: ID={order.get('id', 'simulated')} | Type={order['type']} | Price={order['price']:.6f} | Maker Size={order['maker_size']:.6f}")

            for order in filled:
                await self.handle_filled_order(pair_instance, order, current_price)

            self.order_grids[symbol]['active_orders'] = [
                o for o in active_orders if o not in filled
            ]
            self.logger.info(
                f"Updated active orders: {len(self.order_grids[symbol]['active_orders'])} remaining for {symbol}.")
        else:
            pass  # Removed spammy debug log

        return filled

    async def handle_filled_order(self, pair_instance, order, current_price: float):
        """Handles completed orders by:
        1. Recording volume and P&L metrics
        2. Creating a counter-order with inflated size (profit mechanism)
        """

        self.historical_metrics['daily_volume'].append(order['maker_size'])
        self.historical_metrics['fee_income'].append(order.get('fee', 0))
        # self.logger.debug(f"Updated daily volume: {order['maker_size']}, fee: {order.get('fee', 0)}") # Too spammy

        position = self.active_positions[pair_instance.symbol]
        profit_margin = position.profit_margin

        if order['type'] == 'buy':  # Original was a buy, counter is a sell
            new_price = current_price * (1 + profit_margin)
            new_order_type = 'sell'
            new_maker_size = order['taker_size']  # Sell the asset we just bought
            new_taker_size = new_maker_size * new_price
        else:  # Original was a sell, counter is a buy
            new_price = current_price * (1 - profit_margin)
            new_order_type = 'buy'
            new_maker_size = order['taker_size']  # Buy back the asset we sold
            if new_price <= 0:
                self.logger.warning(f"Cannot create counter-order with non-positive price: {new_price}")
                return
            new_taker_size = new_maker_size / new_price

        new_order = {
            'maker': order['taker'],
            'maker_size': new_maker_size,
            'taker': order['maker'],
            'taker_size': new_taker_size,
            'price': new_price,
            'type': new_order_type,
            'original_price': order['price'],  # Track original fill price
            'expected_profit': abs(new_price - order['price']),  # Calculate expected profit
            'is_counter': True  # Tag this as a counter-order
        }

        self.logger.info(
            f"Creating counter order for {pair_instance.symbol}: {new_order['type']} {new_order['maker_size']:.4f} @ {new_order['price']:.4f}")

        self.order_grids[pair_instance.symbol]['active_orders'].append(new_order)

        pair_instance.order_history = {
            'last_fill': datetime.now(),
            'side': 'buy' if order['maker'] == pair_instance.t2.symbol else 'sell',
            'size': order['maker_size']
        }

    def get_dex_history_file_path(self, pair_name: str) -> str:
        """Path for storing range maker order history"""
        unique_id = pair_name.replace("/", "_")
        return f"{self.config_manager.ROOT_DIR}/data/range_maker_{unique_id}_history.yaml"

    def get_dex_token_address_file_path(self, token_symbol: str) -> str:
        """Path for storing token addresses"""
        return f"{self.config_manager.ROOT_DIR}/data/range_maker_{token_symbol}_addr.yaml"

    async def thread_init_async_action(self, pair_instance):
        """Initial setup for pairs - no action needed for range maker"""
        pass

    def get_operation_interval(self) -> int:
        return 60  # Check every minute

    def should_update_cex_prices(self) -> bool:
        return False  # Purely self-contained market-making - no external prices

    def get_startup_tasks(self) -> list:
        return [
            # self.config_manager.xbridge_manager.cancelallorders(),
            # self.config_manager.xbridge_manager.dxflushcancelledorders()
        ]
