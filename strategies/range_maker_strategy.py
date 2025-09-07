"""
Refactored Range Maker Strategy

Key improvements:
1. Simplified configuration and initialization
2. Better separation of concerns
3. Reduced code duplication
4. Clearer error handling
5. More maintainable architecture
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from definitions.logger import setup_logging
from strategies.base_strategy import BaseStrategy


class CurveType(Enum):
    """Enumeration of supported curve types for price distribution."""
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    SIGMOID = "sigmoid"
    POWER = "power"


class PriceStepType(Enum):
    """Enumeration of supported price step calculation methods."""
    LINEAR = "linear"
    SIGMOID = "sigmoid"
    EXPONENTIAL = "exponential"
    POWER = "power"


@dataclass
class RangeConfig:
    """Configuration for a range-based liquidity position."""
    token_pair: str
    min_price: float
    max_price: float
    grid_density: int
    current_mid_price: float
    curve: CurveType = CurveType.LINEAR
    curve_strength: float = 10.0
    percent_min_size: float = 0.0001
    price_steps: PriceStepType = PriceStepType.LINEAR
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.min_price <= 0:
            raise ValueError("min_price must be positive")
        if self.max_price <= self.min_price:
            raise ValueError("max_price must be greater than min_price")
        if self.grid_density <= 0:
            raise ValueError("grid_density must be positive")
        if not (0 < self.percent_min_size < 1):
            raise ValueError("percent_min_size must be between 0 and 1")


@dataclass
class OrderGrid:
    """Represents an order grid for a trading pair."""
    buy_orders: Dict[float, Dict[str, Any]] = field(default_factory=dict)
    sell_orders: Dict[float, Dict[str, Any]] = field(default_factory=dict)
    active_orders: List[Dict[str, Any]] = field(default_factory=list)
    # Track order history
    filled_orders_history: List[Dict[str, Any]] = field(default_factory=list)

    def clear(self):
        """Clear all orders from the grid."""
        self.buy_orders.clear()
        self.sell_orders.clear()
        self.active_orders.clear()


class PriceCalculator:
    """Handles price step calculations with different distribution methods."""

    @staticmethod
    def calculate_steps(config: RangeConfig) -> np.ndarray:
        """Calculate price steps based on configuration."""
        if config.price_steps == PriceStepType.LINEAR:
            return PriceCalculator._linear_steps(config)
        return PriceCalculator._transformed_steps(config)

    @staticmethod
    def _linear_steps(config: RangeConfig) -> np.ndarray:
        """Calculate linear price steps."""
        return np.linspace(config.min_price, config.max_price, config.grid_density)

    @staticmethod
    def _transformed_steps(config: RangeConfig) -> np.ndarray:
        """Calculate transformed price steps."""
        x_values = np.linspace(-1, 1, config.grid_density)

        if config.price_steps == PriceStepType.SIGMOID:
            y_values = PriceCalculator._sigmoid_transform(x_values)
        elif config.price_steps == PriceStepType.EXPONENTIAL:
            y_values = PriceCalculator._exponential_transform(x_values)
        elif config.price_steps == PriceStepType.POWER:
            y_values = PriceCalculator._power_transform(x_values)
        else:
            y_values = x_values

        return PriceCalculator._map_to_price_range(y_values, x_values, config)

    @staticmethod
    def _sigmoid_transform(x_values: np.ndarray) -> np.ndarray:
        """Apply sigmoid transformation."""
        k = 2.5
        return x_values / (1 + (k - 1) * x_values ** 2)

    @staticmethod
    def _exponential_transform(x_values: np.ndarray) -> np.ndarray:
        """Apply exponential transformation."""
        k = 1.0
        return np.sign(x_values) * (np.exp(k * np.abs(x_values)) - 1) / (np.exp(k) - 1)

    @staticmethod
    def _power_transform(x_values: np.ndarray) -> np.ndarray:
        """Apply power transformation."""
        k = 1.5
        return np.sign(x_values) * (np.abs(x_values) ** k)

    @staticmethod
    def _map_to_price_range(y_values: np.ndarray, x_values: np.ndarray,
                            config: RangeConfig) -> np.ndarray:
        """Map transformed values to actual price range."""
        prices = []
        current_price = config.current_mid_price

        # Left side (below current price)
        left_mask = x_values <= 0
        if np.any(left_mask):
            y_left = y_values[left_mask]
            if len(y_left) > 0 and np.min(y_left) < 0:
                left_prices = current_price + y_left * (current_price - config.min_price) / abs(np.min(y_left))
            else:
                left_prices = np.linspace(config.min_price, current_price, len(y_left))
            prices.extend(left_prices)

        # Right side (above current price)
        right_mask = x_values > 0
        if np.any(right_mask):
            y_right = y_values[right_mask]
            if len(y_right) > 0 and np.max(y_right) > 0:
                right_prices = current_price + y_right * (config.max_price - current_price) / np.max(y_right)
            else:
                right_prices = np.linspace(current_price, config.max_price, len(y_right))
            prices.extend(right_prices)

        prices = np.unique(np.clip(prices, config.min_price, config.max_price))
        return np.sort(prices)


class WeightCalculator:
    """Handles weight calculations for fund allocation."""

    @staticmethod
    def calculate_weights(prices: np.ndarray, mid_price: float, config: RangeConfig) -> np.ndarray:
        """Calculate allocation weights based on curve type."""
        if config.curve == CurveType.LINEAR:
            return WeightCalculator._linear_weights(len(prices))
        elif config.curve == CurveType.EXPONENTIAL:
            return WeightCalculator._exponential_weights(prices, mid_price, config)
        elif config.curve == CurveType.SIGMOID:
            return WeightCalculator._sigmoid_weights(prices, mid_price, config)
        elif config.curve == CurveType.POWER:
            return WeightCalculator._power_weights(prices)
        else:
            return WeightCalculator._uniform_weights(len(prices))

    @staticmethod
    def _linear_weights(num_prices: int) -> np.ndarray:
        """Calculate linear weights."""
        return np.ones(num_prices)

    @staticmethod
    def _exponential_weights(prices: np.ndarray, mid_price: float, config: RangeConfig) -> np.ndarray:
        """Calculate exponential decay weights."""
        range_width = max(config.max_price - config.min_price, 1e-8)
        relative_dist = np.abs(prices - mid_price) / range_width
        return np.exp(-config.curve_strength * relative_dist)

    @staticmethod
    def _sigmoid_weights(prices: np.ndarray, mid_price: float, config: RangeConfig) -> np.ndarray:
        """Calculate sigmoid weights."""
        range_width = config.max_price - config.min_price
        normalized_prices = (prices - mid_price) / range_width
        return 1 / (1 + np.exp(-config.curve_strength * normalized_prices))

    @staticmethod
    def _power_weights(prices: np.ndarray) -> np.ndarray:
        """Calculate power law weights."""
        return 1 / (prices ** 2)

    @staticmethod
    def _uniform_weights(num_prices: int) -> np.ndarray:
        """Calculate uniform weights."""
        return np.ones(num_prices) / num_prices


class OrderBuilder:
    """Builds orders from calculated parameters."""

    @staticmethod
    def build_orders(prices: np.ndarray, weights: np.ndarray, config: RangeConfig,
                     pair_instance: Any, t1_balance: float, t2_balance: float) -> Tuple[List[Dict], List[Dict]]:
        """Build buy and sell orders."""
        mid_price = config.current_mid_price

        # Ensure buy prices are strictly less than mid price and sell prices are strictly greater
        buffer = (config.max_price - config.min_price) / config.grid_density * 0.01
        buy_threshold = mid_price - buffer
        sell_threshold = mid_price + buffer

        # Filter base prices
        buy_base_prices = [p for p in prices if p < buy_threshold]
        sell_base_prices = [p for p in prices if p > sell_threshold]

        # Use base prices directly 
        buy_prices = buy_base_prices
        sell_prices = sell_base_prices

        # Get corresponding weights
        buy_weights = [weights[i] for i, p in enumerate(prices) if p < buy_threshold]
        sell_weights = [weights[i] for i, p in enumerate(prices) if p > sell_threshold]

        # For sell orders, reverse the weights to make them increase with price only for non-exponential curves
        if sell_prices and sell_weights:
            sorted_data = sorted(zip(sell_prices, sell_weights), key=lambda x: x[0])
            sell_prices = [x[0] for x in sorted_data]
            sell_weights = [x[1] for x in sorted_data]
            if config.curve != CurveType.EXPONENTIAL:
                sell_weights = sell_weights[::-1]

        # Determine which side is depleted for order sizing
        asset_ratio = t1_balance / (t2_balance + 1e-8)
        base_depleted = asset_ratio < 0.5

        buy_orders = OrderBuilder._build_buy_orders(
            buy_prices, buy_base_prices, buy_weights, config, pair_instance, t2_balance, t1_balance, base_depleted
        )
        sell_orders = OrderBuilder._build_sell_orders(
            sell_prices, sell_base_prices, sell_weights, config, pair_instance, t1_balance, t2_balance, base_depleted
        )

        return buy_orders, sell_orders

    @staticmethod
    def _build_buy_orders(prices: List[float], base_prices: List[float], weights: List[float],
                          config: RangeConfig, pair_instance: Any, balance: float,
                          other_balance: float, base_depleted: bool) -> List[Dict]:
        """Build buy orders."""
        if not prices:
            return []

        # For buy orders, we're spending quote currency to buy base currency
        # If base is depleted, we want to conserve quote currency (use minimum sizes)
        # If quote is depleted, we want to spend quote currency normally
        is_depleted_side = not base_depleted  # We're spending quote currency
        sizes = OrderBuilder._calculate_order_sizes(weights, balance, config, len(prices), is_depleted_side)

        orders = []
        for price, base_price, size in zip(prices, base_prices, sizes):
            if price <= 0:
                continue

            taker_size = size / price
            orders.append({
                'maker': pair_instance.t2.symbol,
                'maker_size': size,
                'taker': pair_instance.t1.symbol,
                'taker_size': taker_size,
                'price': base_price,
                'type': 'buy'
            })

        return orders

    @staticmethod
    def _build_sell_orders(prices: List[float], base_prices: List[float], weights: List[float],
                           config: RangeConfig, pair_instance: Any, balance: float,
                           other_balance: float, base_depleted: bool) -> List[Dict]:
        """Build sell orders."""
        if not prices:
            return []

        # For sell orders, we're spending base currency to get quote currency
        # If base is depleted, we want to conserve base currency (use minimum sizes)
        # If quote is depleted, we want to spend base currency normally
        is_depleted_side = base_depleted  # We're spending base currency
        sizes = OrderBuilder._calculate_order_sizes(weights, balance, config, len(prices), is_depleted_side)

        orders = []
        for price, base_price, size in zip(prices, base_prices, sizes):
            taker_size = size * price
            orders.append({
                'maker': pair_instance.t1.symbol,
                'maker_size': size,
                'taker': pair_instance.t2.symbol,
                'taker_size': taker_size,
                'price': base_price,
                'type': 'sell'
            })

        return orders

    @staticmethod
    def _calculate_order_sizes(weights: List[float], balance: float,
                               config: RangeConfig, num_orders: int,
                               is_depleted_side: bool) -> List[float]:
        """Calculate individual order sizes with accumulation focus."""
        if not weights or sum(weights) <= 0:
            weights = [1.0] * num_orders

        # Always use maximum 30% of balance for orders to maintain reserves
        max_balance_usage = 0.3
        max_alloc = balance * max_balance_usage

        # Calculate size based on weights
        total_weight = sum(weights)
        sizes = []
        for w in weights:
            # Distribute allocation proportionally
            size = max_alloc * (w / total_weight)

            # Apply minimum size constraint
            min_size = balance * config.percent_min_size
            size = max(size, min_size)

            sizes.append(size)

        # Normalize to not exceed max allocation
        total_alloc = sum(sizes)
        if total_alloc > max_alloc:
            scale = max_alloc / total_alloc
            sizes = [s * scale for s in sizes]

        return sizes


class RangeMakerStrategy(BaseStrategy):
    """
    Range Maker Strategy.
    
    Implements concentrated liquidity ranges with active order book management.
    """

    def __init__(self, config_manager: Any, controller: Optional[Any] = None) -> None:
        super().__init__(config_manager, controller)
        # Use the root logger's level
        root_level = logging.getLogger().getEffectiveLevel()
        self.logger = setup_logging(name="range_maker", level=root_level, console=True)

        # Core state
        self.positions: Dict[str, RangeConfig] = {}
        self.order_grids: Dict[str, OrderGrid] = {}
        self.pairs: Dict[str, Any] = {}

        self.logger.info("RangeMakerStrategy initialized successfully")

    def initialize_strategy_specifics(self, **kwargs: Any) -> None:
        """Initialize strategy-specific configuration."""
        if 'pair' not in kwargs:
            self.logger.debug("Skipping strategy initialization - 'pair' not provided")
            return

        try:
            config = self._create_range_config(**kwargs)
            self.positions[config.token_pair] = config
            self.order_grids[config.token_pair] = OrderGrid()

            self.logger.info(f"Initialized position for {config.token_pair}")
            self._log_position_details(config)

        except Exception as e:
            self.logger.error(f"Failed to initialize position: {e}")
            raise

    def _create_range_config(self, **kwargs) -> RangeConfig:
        """Create RangeConfig from kwargs with validation."""
        required_fields = ['pair', 'min_price', 'max_price', 'grid_density']
        for field in required_fields:
            if field not in kwargs:
                raise ValueError(f"Missing required field: {field}")

        # Handle enum conversion
        curve = kwargs.get('curve', 'linear')
        if isinstance(curve, str):
            try:
                curve = CurveType(curve.lower())
            except ValueError:
                self.logger.warning(f"Invalid curve type: {curve}. Using LINEAR")
                curve = CurveType.LINEAR

        price_steps = kwargs.get('price_steps', 'linear')
        if isinstance(price_steps, str):
            try:
                price_steps = PriceStepType(price_steps.lower())
            except ValueError:
                self.logger.warning(f"Invalid price_steps type: {price_steps}. Using LINEAR")
                price_steps = PriceStepType.LINEAR

        # Get initial mid price, default to average of min and max if not provided
        initial_middle_price = kwargs.get('initial_middle_price')
        if initial_middle_price is None:
            initial_middle_price = (float(kwargs['min_price']) + float(kwargs['max_price'])) / 2
            self.logger.info(f"Using average of min and max as initial mid price: {initial_middle_price:.6f}")

        return RangeConfig(
            token_pair=kwargs['pair'],
            min_price=float(kwargs['min_price']),
            max_price=float(kwargs['max_price']),
            grid_density=int(kwargs['grid_density']),
            current_mid_price=float(initial_middle_price),
            curve=curve,
            curve_strength=float(kwargs.get('curve_strength', 10.0)),
            percent_min_size=float(kwargs.get('percent_min_size', 0.0001)),
            price_steps=price_steps
        )

    def _log_position_details(self, config: RangeConfig) -> None:
        """Log detailed position information."""
        self.logger.info(
            f"Position {config.token_pair}: "
            f"range=[{config.min_price:.4f}, {config.max_price:.4f}] "
            f"density={config.grid_density} "
            f"curve={config.curve.value} "
            f"strength={config.curve_strength}"
        )

    async def process_pair_async(self, pair_instance: Any) -> List[Dict[str, Any]]:
        """Main processing loop for a trading pair."""
        pair_key = pair_instance.symbol
        config = self.positions.get(pair_key)

        if not config:
            self.logger.warning(f"No configuration for pair {pair_key}")
            return []

        # Check for fills and handle them first
        filled_orders = self._get_filled_orders(pair_key)

        # Log fills if there were any
        if filled_orders:
            grid = self.order_grids[pair_key]
            self._log_fill_update(pair_key, grid, filled_orders)

        # Initialize or regenerate grid if needed
        if (pair_key not in self.order_grids or
                not self.order_grids[pair_key].active_orders or
                filled_orders):
            await self._regrid_orders(pair_instance, config)

        return filled_orders

    def _get_filled_orders(self, pair_key: str) -> List[Dict[str, Any]]:
        """Get orders that have been filled."""
        if hasattr(self, 'is_backtesting') and self.is_backtesting:
            # In backtest mode, filled orders are handled by backtester directly
            return []
        # Live trading would interface with exchange here
        return []

    def _log_fill_update(self, pair_key: str, grid: OrderGrid, filled_orders: List[Dict[str, Any]]) -> None:
        """Log fill updates."""
        total_fills = len(grid.filled_orders_history)
        if filled_orders:
            self.logger.info(
                f"Fills detected for {pair_key}: "
                f"{len(filled_orders)} new fills, "
                f"Total fills={total_fills}"
            )

            # Log details of each fill
            for i, order in enumerate(filled_orders):
                self.logger.debug(
                    f"Fill #{total_fills - len(filled_orders) + i + 1}: "
                    f"{order['type'].upper()} {order['taker_size']:.6f} @ {order['price']:.6f}"
                )

    async def _initialize_grid(self, pair_instance: Any, config: RangeConfig) -> None:
        """Initialize the order grid for a pair."""
        self.logger.info(f"Initializing grid for {pair_instance.symbol}")
        await self._regrid_orders(pair_instance, config)

    async def _regrid_orders(self, pair_instance: Any, config: RangeConfig) -> None:
        """Recalculate and place the entire order grid."""
        pair_key = pair_instance.symbol
        grid = self.order_grids[pair_key]

        # Clear existing orders
        grid.buy_orders.clear()
        grid.sell_orders.clear()
        grid.active_orders.clear()

        # Calculate available balances
        balances = self._get_available_balances(pair_instance, pair_key)

        # Generate new orders using full available balances
        self.logger.debug(
            f"Generating orders with balances: "
            f"T1={balances['available_t1']:.6f}, "
            f"T2={balances['available_t2']:.6f}"
        )
        self.logger.debug(f"Current mid price: {config.current_mid_price:.6f}")

        buy_orders, sell_orders = self._generate_orders(config, pair_instance, grid, **balances)

        # Update grid
        if buy_orders or sell_orders:
            self._update_grid(grid, buy_orders, sell_orders)
            self.logger.info(
                f"Regrid complete: "
                f"{len(buy_orders)} buys, "
                f"{len(sell_orders)} sells"
            )
        else:
            self.logger.warning("No orders generated - check configuration and balances")

    def _get_available_balances(self, pair_instance: Any, pair_key: str) -> Dict[str, float]:
        """Calculate available balances for order placement."""
        try:
            committed = self._calculate_committed_balances(pair_key)

            # Base balances
            base_t1 = max(0, pair_instance.t1.dex.free_balance - committed['t1'])
            base_t2 = max(0, pair_instance.t2.dex.free_balance - committed['t2'])

            return {
                'available_t1': base_t1,
                'available_t2': base_t2
            }
        except Exception as e:
            self.logger.error(f"Error calculating balances: {e}")
            return {
                'available_t1': pair_instance.t1.dex.free_balance,
                'available_t2': pair_instance.t2.dex.free_balance
            }

    def _calculate_committed_balances(self, pair_key: str) -> Dict[str, float]:
        """Calculate balances committed to existing orders."""
        committed = {'t1': 0.0, 't2': 0.0}
        grid = self.order_grids.get(pair_key)

        if grid:
            for order in grid.active_orders:
                if order['type'] == 'sell':
                    committed['t1'] += order['maker_size']
                elif order['type'] == 'buy':
                    committed['t2'] += order['maker_size']

        return committed

    def _generate_orders(self, config: RangeConfig, pair_instance: Any, grid: OrderGrid,
                         available_t1: float, available_t2: float) -> Tuple[List[Dict], List[Dict]]:
        """Generate buy and sell orders based on configuration."""
        try:
            self.logger.debug("Calculating price steps...")
            prices = PriceCalculator.calculate_steps(config)
            self.logger.debug(f"Calculated {len(prices)} price steps: min={min(prices):.6f}, max={max(prices):.6f}")

            self.logger.debug("Calculating weights...")
            weights = WeightCalculator.calculate_weights(prices, config.current_mid_price, config)
            self.logger.debug(f"Weight sum: {weights.sum()}, min={min(weights):.6f}, max={max(weights):.6f}")

            # Normalize weights to sum to 1
            if weights.sum() > 0:
                weights = weights / weights.sum()
                self.logger.debug(f"Normalized weights: sum={weights.sum():.6f}")
            else:
                # Fallback to equal weights if sum is 0
                weights = np.ones(len(prices)) / len(prices)
                self.logger.debug(f"Using equal weights: sum={weights.sum():.6f}")

            self.logger.debug("Building orders...")
            buy_orders, sell_orders = OrderBuilder.build_orders(
                prices, weights, config, pair_instance, available_t1, available_t2
            )

            # Log order details in a more concise format
            if buy_orders:
                buy_info = []
                base_token = pair_instance.t1.symbol
                quote_token = pair_instance.t2.symbol
                for i, order in enumerate(buy_orders):
                    buy_info.append(
                        f"#{i + 1}: {order['price']:.4f} "
                        f"(b:{order['taker_size']:.6f} {base_token}, "
                        f"q:{order['maker_size']:.6f} {quote_token})"
                    )
                self.logger.debug(f"Buy orders [{len(buy_orders)}]: {' | '.join(buy_info)}")

            if sell_orders:
                sell_info = []
                base_token = pair_instance.t1.symbol
                quote_token = pair_instance.t2.symbol
                for i, order in enumerate(sell_orders):
                    sell_info.append(
                        f"#{i + 1}: {order['price']:.4f} "
                        f"(b:{order['maker_size']:.6f} {base_token}, "
                        f"q:{order['taker_size']:.6f} {quote_token})"
                    )
                self.logger.debug(f"Sell orders [{len(sell_orders)}]: {' | '.join(sell_info)}")

            # Ensure no overlap between buy and sell prices
            if buy_orders and sell_orders:
                max_buy_price = max(order['price'] for order in buy_orders)
                min_sell_price = min(order['price'] for order in sell_orders)
                if max_buy_price >= min_sell_price:
                    self.logger.error(
                        f"PRICE OVERLAP DETECTED: max buy {max_buy_price:.6f} >= min sell {min_sell_price:.6f}")
                    # Filter out overlapping orders
                    buy_orders = [order for order in buy_orders if order['price'] < min_sell_price]
                    sell_orders = [order for order in sell_orders if order['price'] > max_buy_price]
                    self.logger.warning(f"Filtered to {len(buy_orders)} buy and {len(sell_orders)} sell orders")

            # Ensure mid price is between buy and sell prices
            if buy_orders and sell_orders:
                max_buy_price = max(order['price'] for order in buy_orders)
                min_sell_price = min(order['price'] for order in sell_orders)
                if not (max_buy_price < config.current_mid_price < min_sell_price):
                    self.logger.warning(
                        f"Mid price {config.current_mid_price:.6f} not between buy ({max_buy_price:.6f}) and sell ({min_sell_price:.6f})")

            # Store original prices for debugging
            for order in buy_orders + sell_orders:
                order['original_price'] = order.get('base_price', order['price'])

            return buy_orders, sell_orders

        except Exception as e:
            self.logger.error(f"Error generating orders: {e}", exc_info=True)
            return [], []

    def _update_grid(self, grid: OrderGrid, buy_orders: List[Dict], sell_orders: List[Dict]) -> None:
        """Update the order grid with new orders."""
        grid.buy_orders = {order['price']: order for order in buy_orders}
        grid.sell_orders = {order['price']: order for order in sell_orders}
        grid.active_orders = buy_orders + sell_orders

    def update_mid_price(self, pair_key: str, new_mid_price: float) -> None:
        """
        Update the mid price for a pair (typically called after fills).
        This method can be called by the backtester or controller.
        """
        if pair_key in self.positions:
            old_price = self.positions[pair_key].current_mid_price
            self.positions[pair_key].current_mid_price = new_mid_price
            self.logger.info(f"Updated mid price for {pair_key}: {old_price:.6f} -> {new_mid_price:.6f}")

    def get_fill_summary(self) -> Dict[str, Any]:
        """Get a summary of fills across all pairs."""
        summary = {}
        for pair_key, grid in self.order_grids.items():
            summary[pair_key] = {
                'total_fills': len(grid.filled_orders_history),
                'last_10_fills': grid.filled_orders_history[-10:] if grid.filled_orders_history else []
            }
        return summary

    # Utility methods for BaseStrategy compatibility
    def get_tokens_for_initialization(self, **kwargs: Any) -> List[str]:
        """Extract tokens from pair configurations."""
        tokens = set()
        for pair_cfg in kwargs.get('pairs', []):
            token1, token2 = pair_cfg['pair'].split('/')
            tokens.update([token1, token2])
        return list(tokens)

    def get_pairs_for_initialization(self, tokens_dict: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        """Create Pair instances for configured ranges."""
        from definitions.pair import Pair

        pairs = {}
        for pair_cfg in kwargs.get('pairs', []):
            token1, token2 = pair_cfg['pair'].split('/')
            pair_name = pair_cfg['pair']

            pairs[pair_name] = Pair(
                token1=tokens_dict[token1],
                token2=tokens_dict[token2],
                cfg={**pair_cfg, 'name': pair_name},
                strategy="range_maker",
                config_manager=self.config_manager
            )
        return pairs

    async def thread_init_async_action(self, pair_instance: Any) -> None:
        """Initial setup - no action needed."""
        pass

    def get_operation_interval(self) -> int:
        """Get operation interval in seconds."""
        return 60

    def should_update_cex_prices(self) -> bool:
        """Range maker doesn't need external price feeds."""
        return False

    def get_startup_tasks(self) -> List[Any]:
        """No startup tasks needed."""
        return []

    def get_dex_history_file_path(self, pair_name: str) -> str:
        """Get history file path."""
        safe_name = pair_name.replace("/", "_")
        return f"{self.config_manager.ROOT_DIR}/data/range_maker_{safe_name}_history.yaml"
