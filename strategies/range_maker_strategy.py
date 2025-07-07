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
   - Continuous position rebalancing
5. Rebalances positions periodically to:
   - Maintain balanced inventory
   - Adjust to price shifts by expanding/contracting the range

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
    curve_type: str = 'linear'
    fee_accumulated: float = 0.0
    created_at: datetime = datetime.now()


class RangeMakerStrategy(BaseStrategy):
    """
    Implements concentrated liquidity ranges with active order book management.
    Manages a grid of limit orders within user-defined price bounds to:
    1. Emulate Uniswap V3-like liquidity provisioning
    2. Generate profit through automated market making
    3. Dynamically rebalance based on inventory shifts
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
        self.logger.info("RangeMakerStrategy initialized")

    def initialize_strategy_specifics(self, **kwargs):
        """Configures range parameters for each pair"""
        for pair_cfg in kwargs.get('pairs', []):
            pos = RangePosition(
                token_pair=pair_cfg['pair'],
                min_price=pair_cfg['min_price'],
                max_price=pair_cfg['max_price'],
                grid_density=pair_cfg['grid_density'],
                curve_type=pair_cfg.get('curve_type', 'linear')
            )
            self.active_positions[pos.token_pair] = pos
            self.logger.info(f"Added position: {pos.token_pair} - price range [{pos.min_price}, {pos.max_price}], "
                             f"{pos.grid_density} orders, curve: {pos.curve_type}")

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

        self.logger.debug(f"Processing {pair_key}")

        # Initialize grid on first run
        if pair_key not in self.order_grids:
            self.logger.info(f"First run for {pair_key}, initializing grid")
            await self.initialize_order_grid(pair_instance, position)

        # Monitor order fills and update the grid
        await self.process_order_updates(pair_instance)

        # Check if inventory drift requires rebalancing
        if self.needs_rebalance(pair_instance):
            self.logger.warning(f"Rebalance triggered for {pair_key}")
            await self.rebalance_position(pair_instance, position)
        else:
            self.logger.debug(f"No rebalance needed for {pair_key}")

    async def initialize_order_grid(self, pair_instance, position):
        """Creates the initial set of auction orders (buy/sell) within the price range"""
        self.logger.info(f"Initializing order grid for {pair_instance.symbol}")
        buy_orders, sell_orders = self.calculate_grid_allocations(
            pair_instance, position
        )

        self.logger.info(
            f"Generated {len(buy_orders)} buy orders and {len(sell_orders)} sell orders for {pair_instance.symbol}")

        # Place orders through XBridgeManager (Simulated during development)
        # placed_orders = await self.place_grid_orders(pair_instance, buy_orders + sell_orders)

        # Simulate order placement 
        placed_orders = [{"id": "SIMULATED_ORDER", "status": "simulated"}]
        self.logger.info(
            f"SIMULATED ORDER PLACEMENT - would have placed {len(buy_orders + sell_orders)} orders for {pair_instance.symbol}")
        self.logger.debug(f"Order details simulation: {buy_orders + sell_orders}")

        self.order_grids[pair_instance.symbol] = {
            'buy': {o['price']: o for o in buy_orders},
            'sell': {o['price']: o for o in sell_orders},
            'active_orders': placed_orders
        }

    def calculate_grid_allocations(self, pair_instance, position) -> Tuple[List[dict], List[dict]]:
        """Distributes funds algorithmically across the price range"""
        # Calculate price points and weights based on curve type
        prices = np.linspace(position.min_price, position.max_price, position.grid_density)
        mid_price = (position.min_price + position.max_price) / 2

        if position.curve_type == 'linear':
            weights = np.linspace(1, 0, position.grid_density)
        elif position.curve_type == 'exp_decay':
            weights = np.exp(-np.abs(prices - mid_price))
        else:
            weights = np.ones(position.grid_density)

        # Normalize weights to sum to 1
        weights /= weights.sum()

        buy_orders = []
        sell_orders = []
        t1_balance = pair_instance.t1.dex.free_balance
        t2_balance = pair_instance.t2.dex.free_balance

        # Create buy orders below mid-price (funded with quote token T2)
        for price, weight in zip(prices, weights):
            if price < mid_price:
                size = t2_balance * weight
                buy_orders.append({
                    'maker': pair_instance.t2.symbol,
                    'maker_size': size,
                    'taker': pair_instance.t1.symbol,
                    'price': price,
                    'type': 'limit'
                })
            # Create sell orders above mid-price (funded with base token T1)
            else:
                size = t1_balance * weight
                sell_orders.append({
                    'maker': pair_instance.t1.symbol,
                    'maker_size': size,
                    'taker': pair_instance.t2.symbol,
                    'price': price,
                    'type': 'limit'
                })

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
            result = [{"id": "SIMULATED_ORDER", "status": "simulated"}]  # Simulation for testing
            self.logger.debug(f"Order placement result: {result}")
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
            return

        if not hasattr(pair_instance, 'symbol'):
            self.logger.error("pair_instance is missing 'symbol' attribute")
            return

        symbol = pair_instance.symbol
        self.logger.info(f"Processing order updates for {symbol}")
        
        if symbol not in self.order_grids:
            self.logger.warning(f"No order grid found for {symbol}")
            return

        active_orders = self.order_grids[symbol]['active_orders']

        # Get filled orders either from exchange feed or simulated fills
        filled = []
        if current_price is not None:  # Backtesting mode
            filled = [
                order for order in active_orders
                if (order['type'] == 'buy' and current_price <= order['price']) or
                   (order['type'] == 'sell' and current_price >= order['price'])
            ]
        else:  # Live trading mode
            filled = [o for o in active_orders if o.get('status') == 'filled']

        # Log fills
        self.logger.info(f"Detected {len(filled)} filled orders for {symbol}")
        for order in filled:
            self.logger.debug(f"Filled order: {order.get('id', 'simulated')}")

        # Process filled orders using core strategy logic
        for order in filled:
            self.logger.info(f"Handling filled order: {order.get('id', 'simulated')} - "
                             f"{order['maker']}/{order['taker']} at {order['price']}")
            await self.handle_filled_order(pair_instance, order)

        # Update order grid state
        self.order_grids[symbol]['active_orders'] = [
            o for o in active_orders if o not in filled
        ]
        self.logger.info(f"Updated active orders: {len(active_orders)} remaining for {symbol}")

    async def handle_filled_order(self, pair_instance, order):
        """Handles completed orders by:
        1. Recording volume and P&L metrics
        2. Creating a counter-order with inflated size (profit mechanism)
        """
        self.logger.info(f"Processing filled order: {order.get('id')}")

        # Record trading volume and fees
        self.historical_metrics['daily_volume'].append(order['size'])
        self.historical_metrics['fee_income'].append(order.get('fee', 0))
        self.logger.debug(f"Updated daily volume: {order['size']}, fee: {order.get('fee', 0)}")

        # Generate counter-order: take proceeds and create opposite side order
        # with 1% size boost to extract spread as profit
        boost_factor = 1.01
        new_order = {
            'maker': order['taker'],  # Flipping token roles
            'maker_size': order['taker_size'] * boost_factor,
            'taker': order['maker'],
            'price': 1.0 / order['price'],  # Inverse price
            'type': 'limit'
        }

        self.logger.info(f"Creating counter order for {pair_instance.symbol}: "
                         f"{new_order['maker']} {new_order['maker_size']} @ {new_order['price']}")

        await self.place_grid_orders(pair_instance, [new_order])

        # Update pair's order history
        pair_instance.order_history = {
            'last_fill': datetime.now(),
            'side': 'buy' if order['maker'] == pair_instance.t2.symbol else 'sell',
            'size': order['maker_size']
        }

        self.logger.info("Order history updated")

    def needs_rebalance(self, pair_instance) -> bool:
        """Detects significant (>20%) inventory imbalance requiring intervention"""
        total_balance = pair_instance.t1.dex.free_balance + pair_instance.t2.dex.free_balance
        if total_balance == 0:
            return False

        # Measure deviation from 50/50 inventory target
        t1_ratio = pair_instance.t1.dex.free_balance / total_balance
        needs_rebal = abs(t1_ratio - 0.5) > 0.2  # 20% threshold

        if needs_rebal:
            self.logger.warning(f"Rebalance needed for {pair_instance.symbol}: "
                                f"T1 ratio ({t1_ratio * 100:.1f}%) deviates >20%")

        return needs_rebal

    async def rebalance_position(self, pair_instance, position):
        """Performs inventory rebalancing by:
        1. Cancelling all active orders
        2. Recalculating order grid with current balances
        3. Expanding price range (±0.5%) to account for market shifts
        """
        self.logger.warning(f"Initiating rebalance for {pair_instance.symbol}")

        # Cancel existing orders
        self.logger.info(f"Canceling all active orders for {pair_instance.symbol}")
        # await self.config_manager.xbridge_manager.cancelallorders()
        self.logger.info(f"SIMULATED ORDER CANCELLATION - would cancel all orders for {pair_instance.symbol}")

        # Reset grid with current balances
        self.logger.info(f"Rebuilding order grid for {pair_instance.symbol}")
        await self.initialize_order_grid(pair_instance, position)

        # Shift price range to adapt to market conditions
        new_min = position.min_price * 0.995
        new_max = position.max_price * 1.005
        self.active_positions[pair_instance.symbol].min_price = new_min
        self.active_positions[pair_instance.symbol].max_price = new_max

        self.logger.info(f"Adjusted price range: new min={new_min}, new max={new_max}")

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
