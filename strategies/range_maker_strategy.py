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
from dataclasses import dataclass, field
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
    created_at: datetime = field(default_factory=datetime.now)
    percent_min_size: float = 0.0001  # New: e.g., 0.01% of allocated funds


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
                curve_strength=kwargs.get('curve_strength', 10),
                percent_min_size=kwargs.get('percent_min_size', 0.0001)  # New line
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

    async def thread_loop_async_action(self, pair_instance, current_price=None):
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
        filled = []
        symbol = pair_instance.symbol
        active_orders = self.order_grids[symbol]['active_orders']

        if current_price is not None:  # Backtesting mode
            for order in active_orders:
                is_filled = False
                if order['type'] == 'buy' and current_price <= order['price']:
                    is_filled = True
                elif order['type'] == 'sell' and current_price >= order['price']:
                    is_filled = True

                if is_filled:
                    filled.append(order)
        else:  # Live trading mode
            self.logger.debug("Live trading mode: Checking for actual filled orders.")
            filled = [o for o in active_orders if o.get('status') == 'filled']

        if filled:
            self.logger.info(f"Detected {len(filled)} filled orders for {symbol}. Re-gridding liquidity.")
            for order in filled:
                self.logger.debug(
                    f"Filled order: ID={order.get('id', 'simulated')} | Type={order['type']} | Price={order['price']:.6f} | Maker Size={order['maker_size']:.6f}")

            # Clear all active orders for this pair before re-gridding
            self.order_grids[symbol]['active_orders'] = []
            self.logger.debug(f"Cleared all active orders for {symbol} for re-gridding.")

            # Re-calculate and place the entire grid based on new balances
            await self._regrid_liquidity(pair_instance, position)

            self.logger.info(
                f"Processed {len(filled)} filled orders and re-gridded liquidity for {symbol}. Now maintaining {len(self.order_grids[symbol]['active_orders'])} active orders.")
        else:
            self.logger.debug(f"No filled orders detected for {symbol}.")

        return filled

    async def _regrid_liquidity(self, pair_instance, position):
        """Helper to re-calculate and place the entire order grid."""
        symbol = pair_instance.symbol
        self.logger.info(f"Re-gridding liquidity for {symbol}.")

        # Ensure the order_grids entry for this symbol is initialized
        if symbol not in self.order_grids:
            self.order_grids[symbol] = {
                'buy': {},
                'sell': {},
                'active_orders': []
            }
            self.logger.debug(f"Initialized order_grids entry for {symbol}.")

        buy_orders, sell_orders = self.calculate_grid_allocations(
            pair_instance, position
        )

        all_orders = buy_orders + sell_orders
        if all_orders:
            # In a real scenario, you'd cancel existing orders here before placing new ones.
            # For simulation, we just replace the active_orders list.
            self.order_grids[symbol]['buy'] = {o['price']: o for o in buy_orders}
            self.order_grids[symbol]['sell'] = {o['price']: o for o in sell_orders}
            self.order_grids[symbol]['active_orders'] = all_orders
            self.logger.info(
                f"Re-gridded with {len(buy_orders)} buy orders and {len(sell_orders)} sell orders for {symbol}.")
        else:
            self.logger.warning(
                f"No orders generated during re-gridding for {symbol}. Check balances and min_order_size.")
            # If no orders are generated, ensure active_orders is empty
            self.order_grids[symbol]['active_orders'] = []

    async def initialize_order_grid(self, pair_instance, position):
        """Creates the initial set of auction orders (buy/sell) within the price range"""
        self.logger.info(f"Initializing order grid for {pair_instance.symbol}")
        await self._regrid_liquidity(pair_instance, position)  # Use the new re-grid helper

    def calculate_grid_allocations(self, pair_instance, position) -> Tuple[List[dict], List[dict]]:
        """Distributes funds algorithmically across the price range"""
        self.logger.info(f"Calculating grid allocations for {pair_instance.symbol} with position: {position}")
        self.logger.debug(
            f"Position details: Min Price={position.min_price}, Max Price={position.max_price}, Grid Density={position.grid_density}, Curve={position.curve}, Curve Strength={position.curve_strength}, Percent Min Size={position.percent_min_size}")

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
                min_order_size_quote = maker_size * position.percent_min_size  # Dynamic min_order_size
                self.logger.debug(
                    f"Buy order {i}: maker_size={maker_size:.6f}, percent_min_size={position.percent_min_size:.6f}, min_order_size={min_order_size_quote:.6f}")
                if price <= 0 or maker_size < min_order_size_quote:
                    self.logger.debug(
                        f"Skipping buy order {i}: price={price:.6f} or maker_size={maker_size:.6f} below minimum ({min_order_size_quote:.6f}).")
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
                self.logger.debug(
                    f"Buy order {i}: price={price:.6f}, maker_size={maker_size:.6f}, taker_size={taker_size:.6f}")
            # Create sell orders above mid-price (funded with base token T1)
            else:
                maker_size = t1_balance * weight
                min_order_size_base = maker_size * position.percent_min_size  # Dynamic min_order_size
                self.logger.debug(
                    f"Sell order {i}: maker_size={maker_size:.6f}, percent_min_size={position.percent_min_size:.6f}, min_order_size={min_order_size_base:.6f}")
                if maker_size < min_order_size_base:
                    self.logger.debug(
                        f"Skipping sell order {i}: maker_size={maker_size:.6f} below minimum ({min_order_size_base:.6f}).")
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
                self.logger.debug(
                    f"Sell order {i}: price={price:.6f}, maker_size={maker_size:.6f}, taker_size={taker_size:.6f}")
        total_generated_orders = len(buy_orders) + len(sell_orders)
        self.logger.info(
            f"Finished calculating grid allocations. Generated {len(buy_orders)} buy orders and {len(sell_orders)} sell orders (Total: {total_generated_orders}).")
        if total_generated_orders < position.grid_density:
            self.logger.warning(
                f"Only {total_generated_orders} orders generated out of {position.grid_density} grid density due to minimum order size constraints. Consider adjusting percent_min_size or initial balances.")
        return buy_orders, sell_orders

    async def place_grid_orders(self, pair_instance, orders: List[dict]):
        """Batch order placement with XBridgeManager"""
        self.logger.info(f"Placing {len(orders)} orders for {pair_instance.symbol}")
        try:
            # XBridge calls commented out for beta development
            # result = await self.config_manager.xbridge_manager.cancelallorders(pair_instance.symbol) # Cancel existing orders
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
