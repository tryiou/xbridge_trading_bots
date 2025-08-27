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
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from definitions.logger import setup_logging
from strategies.base_strategy import BaseStrategy


@dataclass
class RangePosition:
    """Configuration for a range-based liquidity position."""

    token_pair: str
    min_price: float
    max_price: float
    grid_density: int
    current_mid_price: float
    curve: str = 'linear'
    curve_strength: float = 10
    fee_accumulated: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    percent_min_size: float = 0.0001
    price_steps: str = 'linear'


class RangeMakerStrategy(BaseStrategy):
    """
    Implements concentrated liquidity ranges with active order book management.
    
    Manages a grid of limit orders within user-defined price bounds to:
    1. Emulate Uniswap V3-like liquidity provisioning
    2. Generate profit through automated market making
    """

    def __init__(self, config_manager: Any, controller: Optional[Any] = None) -> None:
        """
        Initialize the range maker strategy.
        
        Args:
            config_manager: Configuration management instance
            controller: Optional controller instance
        """
        super().__init__(config_manager, controller)
        self.logger = setup_logging(name="range_maker", level=logging.DEBUG, console=True)
        self.active_positions: Dict[str, RangePosition] = {}
        self.order_grids: Dict[str, Dict[str, Any]] = {}
        self.historical_metrics: Dict[str, List[Any]] = {
            'daily_volume': [],
            'fee_income': [],
            'inventory_changes': []
        }
        self.pairs: Dict[str, Any] = {}
        self.logger.info("RangeMakerStrategy initialization complete")

    def initialize_strategy_specifics(self, **kwargs: Any) -> None:
        """
        Configure range parameters for each pair.
        
        Args:
            **kwargs: Strategy configuration parameters including:
                pair: Trading pair (e.g., "LTC/DOGE")
                min_price: Minimum price for liquidity range
                max_price: Maximum price for liquidity range
                grid_density: Number of orders to place
                percent_min_size: Minimum order size as percentage of balance
                initial_middle_price: Starting mid-price for grid
                curve: Grid spacing type - 'linear', 'sigmoid', 'exponential', or 'power'
                curve_strength: Controls concentration for non-linear curves
                price_steps: Price step calculation method
        """
        if 'pair' not in kwargs:
            self.logger.debug(
                "Skipping strategy-specific initialization: 'pair' argument missing. "
                "This is normal during master ConfigManager setup."
            )
            return

        self._create_position_from_config(**kwargs)

    def _create_position_from_config(self, **kwargs: Any) -> None:
        """
        Create a single range position from configuration parameters.
        
        Args:
            **kwargs: Position configuration parameters
        """
        try:
            min_price_val = kwargs['min_price']
            max_price_val = kwargs['max_price']
            initial_mid = kwargs.get('initial_middle_price', (min_price_val + max_price_val) / 2)

            position = RangePosition(
                token_pair=kwargs['pair'],
                min_price=min_price_val,
                max_price=max_price_val,
                grid_density=kwargs['grid_density'],
                current_mid_price=initial_mid,
                curve=kwargs.get('curve', 'linear'),
                curve_strength=kwargs.get('curve_strength', 10),
                percent_min_size=kwargs.get('percent_min_size', 0.0001),
                price_steps=kwargs.get('price_steps', 'linear')
            )

            self.active_positions[position.token_pair] = position
            self._log_position_initialization(position)

        except Exception as e:
            self.logger.error(f"Error initializing position: {e}")

    def _log_position_initialization(self, position: RangePosition) -> None:
        """
        Log position initialization details.
        
        Args:
            position: The initialized position to log
        """
        self.logger.info(
            f"Initialized position for {position.token_pair}: "
            f"price_range=[{position.min_price:.4f}, {position.max_price:.4f}] "
            f"grid_density={position.grid_density} "
            f"curve={position.curve} "
            f"strength={position.curve_strength} "
            f"min_size_pct={position.percent_min_size * 100:.4f}% "
            f"initial_mid_price={position.current_mid_price:.4f}"
        )

    def get_tokens_for_initialization(self, **kwargs: Any) -> List[str]:
        """
        Extract unique tokens from all configured pairs.
        
        Args:
            **kwargs: Configuration including 'pairs' list
            
        Returns:
            List of unique token symbols
        """
        tokens = set()
        for pair in kwargs.get('pairs', []):
            token1, token2 = pair['pair'].split('/')
            tokens.update([token1, token2])
        return list(tokens)

    def get_pairs_for_initialization(self, tokens_dict: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        """
        Create Pair instances for all configured ranges.
        
        Args:
            tokens_dict: Dictionary mapping token symbols to token instances
            **kwargs: Configuration including 'pairs' list
            
        Returns:
            Dictionary mapping pair names to Pair instances
        """
        from definitions.pair import Pair

        pairs = {}
        for pair_cfg in kwargs.get('pairs', []):
            token1, token2 = pair_cfg['pair'].split('/')
            pair_name = pair_cfg['pair']
            pair_cfg_with_name = {**pair_cfg, 'name': pair_name}

            pairs[pair_name] = Pair(
                token1=tokens_dict[token1],
                token2=tokens_dict[token2],
                cfg=pair_cfg_with_name,
                strategy="range_maker",
                config_manager=self.config_manager
            )
        return pairs

    async def process_pair_async(self, pair_instance: Any) -> List[Dict[str, Any]]:
        """
        Main auction management loop - processes orders and handles rebalancing.
        
        Args:
            pair_instance: Trading pair instance to process
            
        Returns:
            List of filled orders
        """
        pair_key = pair_instance.symbol
        position = self.active_positions.get(pair_key)

        if not position:
            self.logger.warning(f"Skipping processing for pair {pair_key}: No range position configured")
            return []

        if pair_key not in self.order_grids:
            self.logger.info(f"Initializing order grid for {pair_key} for the first time")
            await self._initialize_order_grid_for_pair(pair_instance, position)

        active_orders = self.order_grids[pair_key]['active_orders']
        filled_orders = self._get_live_fills(active_orders)

        if filled_orders:
            await self._handle_filled_orders(pair_instance, position, filled_orders)
        else:
            # Log a simple debug message instead of full status
            self.logger.debug(f"No orders filled for {pair_key} in the last period.")

        return filled_orders

    async def _handle_filled_orders(self, pair_instance: Any, position: RangePosition,
                                    filled_orders: List[Dict[str, Any]]) -> None:
        """
        Handle processing of filled orders and regridding.
        
        Args:
            pair_instance: Trading pair instance
            position: Range position configuration
            filled_orders: List of orders that were filled
        """
        last_fill_price = filled_orders[-1]['price']
        position.current_mid_price = last_fill_price

        self.logger.info(
            f"{pair_instance.symbol}: Detected {len(filled_orders)} filled orders. "
            f"Setting mid-price to {last_fill_price:.6f} and regridding"
        )

        self._log_filled_orders(filled_orders)
        self._reset_order_grid(pair_instance.symbol)
        await self._regrid_liquidity(pair_instance, position)

    def _log_no_fills_status(self, pair_key: str, current_price: Optional[float]) -> None:
        """
        Log status when no orders are filled (reduced verbosity).
        
        Args:
            pair_key: Trading pair symbol
            current_price: Current market price
        """
        if not (pair_key in self.order_grids and self.order_grids[pair_key].get('active_orders')):
            return

        buy_orders = list(self.order_grids[pair_key]['buy'].values())
        sell_orders = list(self.order_grids[pair_key]['sell'].values())

        if not (buy_orders and sell_orders and current_price):
            return

        order_ranges = self._calculate_order_ranges(buy_orders, sell_orders)

        if self._is_price_in_gap(current_price, order_ranges):
            self._log_price_gap_status(current_price, order_ranges)

    def _calculate_order_ranges(self, buy_orders: List[Dict[str, Any]], sell_orders: List[Dict[str, Any]]) -> Dict[
        str, float]:
        """
        Calculate price ranges for buy and sell orders.
        
        Args:
            buy_orders: List of buy orders
            sell_orders: List of sell orders
            
        Returns:
            Dictionary with min/max prices for buy/sell orders
        """
        return {
            'min_buy': min(buy_orders, key=lambda o: o['price'])['price'] if buy_orders else None,
            'max_buy': max(buy_orders, key=lambda o: o['price'])['price'] if buy_orders else None,
            'min_sell': min(sell_orders, key=lambda o: o['price'])['price'] if sell_orders else None,
            'max_sell': max(sell_orders, key=lambda o: o['price'])['price'] if sell_orders else None
        }

    def _is_price_in_gap(self, current_price: float, ranges: Dict[str, float]) -> bool:
        """
        Check if current price is in the gap between buy and sell orders.
        
        Args:
            current_price: Current market price
            ranges: Order price ranges
            
        Returns:
            True if price is in gap, False otherwise
        """
        max_buy = ranges.get('max_buy')
        min_sell = ranges.get('min_sell')

        return (max_buy and min_sell and max_buy < min_sell and
                max_buy < current_price < min_sell)

    def _log_price_gap_status(self, current_price: float, ranges: Dict[str, float]) -> None:
        """
        Log when price is in gap between orders (debug level only).
        
        Args:
            current_price: Current market price
            ranges: Order price ranges
        """
        self.logger.debug(
            f"Price {current_price:.6f} is in grid gap: [{ranges.get('max_buy'):.6f} - {ranges.get('min_sell'):.6f}]"
        )

    def _get_live_fills(self, active_orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Get filled orders in live trading mode by checking order status.
        
        Args:
            active_orders: List of active orders
            
        Returns:
            List of orders with 'filled' status
        """
        self.logger.debug(f"Live trading: Checking XBridge order status for {len(active_orders)} open orders")
        return [order for order in active_orders if order.get('status') == 'filled']

    def _log_filled_orders(self, filled: List[Dict[str, Any]]) -> None:
        """
        Log details of filled orders.
        
        Args:
            filled: List of filled orders to log
        """
        for i, order in enumerate(filled):
            self.logger.debug(
                f"Order {i + 1}: {order['type']} - {order['maker_size']:.6f} {order['maker']} "
                f"@ {order['price']:.6f} for {order['taker_size']:.6f} {order['taker']}"
            )

        types_count = self._count_order_types(filled)
        self.logger.info(f"Filled orders: {types_count['buy']} buys, {types_count['sell']} sells")

    def _count_order_types(self, orders: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Count orders by type.
        
        Args:
            orders: List of orders to count
            
        Returns:
            Dictionary with counts by order type
        """
        types_count = {'buy': 0, 'sell': 0}
        for order in orders:
            order_type = order.get('type', 'unknown')
            if order_type in types_count:
                types_count[order_type] += 1
        return types_count

    def _reset_order_grid(self, symbol: str) -> None:
        """
        Clear active orders for the given symbol.
        
        Args:
            symbol: Trading pair symbol to reset
        """
        self.order_grids[symbol]['active_orders'] = []
        self.logger.debug(f"Cleared all active orders for {symbol} for re-gridding.")

    async def _regrid_liquidity(self, pair_instance: Any, position: RangePosition) -> None:
        """
        Re-calculate and place the entire order grid.
        
        Args:
            pair_instance: Trading pair instance
            position: Range position configuration
        """
        symbol = pair_instance.symbol
        self.logger.info(
            f"Regridding for {symbol} - "
            f"{len(self.order_grids.get(symbol, {}).get('active_orders', []))} "
            "active orders will be replaced"
        )

        self._ensure_order_grid_exists(symbol)
        available_balances = self._calculate_available_balances(pair_instance, symbol)

        buy_orders, sell_orders = self.calculate_grid_allocations(
            pair_instance, position, **available_balances
        )

        await self._apply_regrid_results(symbol, pair_instance, position, buy_orders, sell_orders)

    def _ensure_order_grid_exists(self, symbol: str) -> None:
        """
        Ensure order grid structure exists for symbol.
        
        Args:
            symbol: Trading pair symbol
        """
        if symbol not in self.order_grids:
            self.order_grids[symbol] = {
                'buy': {},
                'sell': {},
                'active_orders': []
            }
            self.logger.debug(f"Created new order grid for {symbol}")

    def _calculate_available_balances(self, pair_instance: Any, symbol: str) -> Dict[str, float]:
        """
        Calculate available balances considering committed orders.
        
        Args:
            pair_instance: Trading pair instance
            symbol: Trading pair symbol
            
        Returns:
            Dictionary with available_t1 and available_t2 balances
        """
        try:
            committed_balances = self._calculate_committed_balances(symbol)

            available_t1 = max(0, pair_instance.t1.dex.free_balance - committed_balances['t1'])
            available_t2 = max(0, pair_instance.t2.dex.free_balance - committed_balances['t2'])

            return {'available_t1': available_t1, 'available_t2': available_t2}

        except Exception as e:
            self.logger.error(f"Error calculating committed balance: {str(e)}")
            return {
                'available_t1': pair_instance.t1.dex.free_balance,
                'available_t2': pair_instance.t2.dex.free_balance
            }

    def _calculate_committed_balances(self, symbol: str) -> Dict[str, float]:
        """
        Calculate balances committed to existing orders.
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            Dictionary with committed t1 and t2 balances
        """
        committed_t1 = 0.0
        committed_t2 = 0.0

        if symbol in self.order_grids:
            for order in self.order_grids[symbol]['active_orders']:
                if order['type'] == 'sell':
                    committed_t1 += order['maker_size']
                elif order['type'] == 'buy':
                    committed_t2 += order['maker_size']

        return {'t1': committed_t1, 't2': committed_t2}

    async def _apply_regrid_results(self, symbol: str, pair_instance: Any, position: RangePosition,
                                    buy_orders: List[Dict[str, Any]], sell_orders: List[Dict[str, Any]]) -> None:
        """
        Apply the results of regridding to the order grid.
        
        Args:
            symbol: Trading pair symbol
            pair_instance: Trading pair instance
            position: Range position configuration
            buy_orders: Generated buy orders
            sell_orders: Generated sell orders
        """
        all_orders = buy_orders + sell_orders

        if all_orders:
            self._update_order_grids(symbol, buy_orders, sell_orders, all_orders)
            self._log_regrid_success(symbol, buy_orders, sell_orders, position, pair_instance)
        else:
            self._handle_empty_regrid(symbol)

    def _update_order_grids(self, symbol: str, buy_orders: List[Dict[str, Any]],
                            sell_orders: List[Dict[str, Any]], all_orders: List[Dict[str, Any]]) -> None:
        """
        Update order grids with new orders.
        
        Args:
            symbol: Trading pair symbol
            buy_orders: New buy orders
            sell_orders: New sell orders
            all_orders: Combined list of all orders
        """
        self.order_grids[symbol]['buy'] = {order['price']: order for order in buy_orders}
        self.order_grids[symbol]['sell'] = {order['price']: order for order in sell_orders}
        self.order_grids[symbol]['active_orders'] = all_orders

    def _log_regrid_success(self, symbol: str, buy_orders: List[Dict[str, Any]],
                            sell_orders: List[Dict[str, Any]], position: RangePosition,
                            pair_instance: Any) -> None:
        """
        Log successful regrid operation.
        
        Args:
            symbol: Trading pair symbol
            buy_orders: Generated buy orders
            sell_orders: Generated sell orders
            position: Range position configuration
            pair_instance: Trading pair instance
        """
        self.logger.info(f"Regrid complete: {len(buy_orders)} bids, {len(sell_orders)} asks for {symbol}")

        self._log_grid_gap(buy_orders, sell_orders)
        self._log_cumulative_fees(position, pair_instance)
        base_token, quote_token = pair_instance.symbol.split('/')
        self._log_order_samples(buy_orders, sell_orders, 20, base_token, quote_token)

    def _log_grid_gap(self, buy_orders: List[Dict[str, Any]], sell_orders: List[Dict[str, Any]]) -> None:
        """
        Log grid gap information if it exists.
        
        Args:
            buy_orders: Buy orders list
            sell_orders: Sell orders list
        """
        if buy_orders and sell_orders:
            max_buy = max(float(order['price']) for order in buy_orders)
            min_sell = min(float(order['price']) for order in sell_orders)

            if min_sell - max_buy > 0:
                gap_pct = (min_sell - max_buy) / max_buy * 100
                self.logger.info(f"Grid gap: [{max_buy:.6f} - {min_sell:.6f}] ({gap_pct:.2f}% gap)")

    def _log_cumulative_fees(self, position: RangePosition, pair_instance: Any) -> None:
        """
        Log cumulative fees for the position.
        
        Args:
            position: Range position configuration
            pair_instance: Trading pair instance
        """
        self.logger.debug(f"Cumulative fees: {position.fee_accumulated:.8f} {pair_instance.t2.symbol}")

    def _log_order_samples(
            self,
            buy_orders: List[Dict[str, Any]],
            sell_orders: List[Dict[str, Any]],
            sample_size: int,
            base: str,
            quote: str,
    ) -> None:
        """Log sample of new orders for debugging with full pair data."""
        if not buy_orders and not sell_orders:
            return

        self.logger.debug("=" * 100)
        self.logger.debug(f" ORDER BOOK SNAPSHOT ({base}/{quote}) ".center(100, "="))
        self.logger.debug("=" * 100)

        if buy_orders:
            self._log_order_table("BIDS (buy orders)", buy_orders, sample_size, base, quote)
        if sell_orders:
            self._log_order_table("ASKS (sell orders)", sell_orders, sample_size, base, quote)

        self.logger.debug("=" * 100)

    def _log_order_table(
            self,
            title: str,
            orders: List[Dict[str, Any]],
            sample_size: int,
            base: str,
            quote: str,
    ) -> None:
        """Pretty-print table of orders with clear action labels."""
        count = len(orders)
        shown = min(sample_size, count)

        # Determine action type from title
        count = len(orders)
        shown = min(sample_size, count)

        # Determine action type from title
        is_buy = "buy" in title.lower()

        if is_buy:
            amount1_label = f"Get ({base})"
            amount2_label = f"Pay ({quote})"
        else:
            amount1_label = f"Sell ({base})"
            amount2_label = f"Receive ({quote})"

        self.logger.debug(f"{title} — showing {shown} of {count}")
        self.logger.debug(
            f"{'Price (' + base + '/' + quote + ')':>18} | "
            f"{'Price (' + quote + '/' + base + ')':>18} | "
            f"{amount1_label:>15} | "
            f"{amount2_label:>15}"
        )
        self.logger.debug("-" * 100)

        for order in orders[:shown]:
            price_base_quote = order["price"]  # base per quote
            price_quote_base = 1.0 / price_base_quote if price_base_quote != 0 else float("inf")

            # For buy orders: taker_size is base amount (received), maker_size is quote amount (paid)
            # For sell orders: maker_size is base amount (sold), taker_size is quote amount (received)
            if is_buy:
                amount_base = order["taker_size"]
                amount_quote = order["maker_size"]
            else:
                amount_base = order["maker_size"]
                amount_quote = order["taker_size"]

            self.logger.debug(
                f"{price_base_quote:>18.8f} | {price_quote_base:>18.8f} | {amount_base:>15.8f} | {amount_quote:>15.8f}"
            )

        if count > sample_size:
            self.logger.debug(f"... {count - sample_size} more not shown")
        self.logger.debug("")

    def _handle_empty_regrid(self, symbol: str) -> None:
        """
        Handle case where no orders are generated during regrid.
        
        Args:
            symbol: Trading pair symbol
        """
        self.logger.warning("Zero orders generated for %s - check min_size and balances", symbol)
        self.order_grids[symbol]['active_orders'] = []

    async def _initialize_order_grid_for_pair(self, pair_instance: Any, position: RangePosition) -> None:
        """
        Initialize order grid for a trading pair.
        
        Args:
            pair_instance: Trading pair instance
            position: Range position configuration
        """
        self.logger.info(f"Initializing order grid for {pair_instance.symbol}")
        await self._regrid_liquidity(pair_instance, position)

    async def initialize_order_grid(self, pair_instance: Any, position: RangePosition) -> None:
        """
        Create the initial set of auction orders (buy/sell) within the price range.
        
        Args:
            pair_instance: Trading pair instance
            position: Range position configuration
        """
        await self._initialize_order_grid_for_pair(pair_instance, position)

    def calculate_price_steps(self, position: RangePosition) -> np.ndarray:
        """
        Create customized price distributions for order placement.

        Four methods for concentrating orders around mid-price:

        Methods:
        - linear: Uniform distribution - order prices spaced equally (default)
        - sigmoid: Sigmoid curve - orders clustered near mid-price (good for stable pairs)
        - exponential: Exponential curve - tighter grouping at mid-price (aggressive concentration)
        - power: Power law - smooth concentration with customizable steepness

        Args:
            position: Strategy position config including curve_strength parameter

        Returns:
            Array of price points for grid orders
        """
        valid_steps = ['linear', 'sigmoid', 'exponential', 'power']
        applied_method = self._validate_price_steps_method(position.price_steps, valid_steps)

        if applied_method == 'linear':
            return self._calculate_linear_price_steps(position)

        return self._calculate_transformed_price_steps(position, applied_method)

    def _validate_price_steps_method(self, price_steps: str, valid_steps: List[str]) -> str:
        """
        Validate and return the price steps method to use.
        
        Args:
            price_steps: Requested price steps method
            valid_steps: List of valid method names
            
        Returns:
            Validated method name (defaults to 'linear' if invalid)
        """
        if price_steps in valid_steps:
            return price_steps

        self.logger.warning(
            f"Invalid price_steps: '{price_steps}'. Valid options: {valid_steps}. "
            "Defaulting to 'linear'"
        )
        return 'linear'

    def _calculate_linear_price_steps(self, position: RangePosition) -> np.ndarray:
        """
        Calculate linear price steps.
        
        Args:
            position: Range position configuration
            
        Returns:
            Array of linearly spaced price points
        """
        prices = np.linspace(position.min_price, position.max_price, position.grid_density)
        self.logger.debug(f"calculate_price_steps (linear): {prices}")
        return prices

    def _calculate_transformed_price_steps(self, position: RangePosition, method: str) -> np.ndarray:
        """
        Calculate price steps using transformation methods.
        
        Args:
            position: Range position configuration
            method: Transformation method to apply
            
        Returns:
            Array of transformed price points
        """
        x_linear_symmetric = np.linspace(-1, 1, position.grid_density)
        y_transformed = self._apply_price_transformation(x_linear_symmetric, method)

        prices = self._map_transformed_to_prices(
            y_transformed, x_linear_symmetric, position
        )

        return self._finalize_price_steps(prices, position)

    def _apply_price_transformation(self, x_values: np.ndarray, method: str) -> np.ndarray:
        """
        Apply the specified transformation to x values.
        
        Args:
            x_values: Linear symmetric x values from -1 to 1
            method: Transformation method
            
        Returns:
            Transformed y values
        """
        if method == 'sigmoid':
            return self._apply_sigmoid_transformation(x_values)
        elif method == 'exponential':
            return self._apply_exponential_transformation(x_values)
        elif method == 'power':
            return self._apply_power_transformation(x_values)
        else:
            return x_values

    def _apply_sigmoid_transformation(self, x_values: np.ndarray) -> np.ndarray:
        """
        Apply sigmoid transformation to concentrate points around center.
        
        Args:
            x_values: Input values
            
        Returns:
            Sigmoid-transformed values
        """
        k_param = 2.5
        return x_values / (1 + (k_param - 1) * x_values ** 2)

    def _apply_exponential_transformation(self, x_values: np.ndarray) -> np.ndarray:
        """
        Apply exponential transformation to concentrate points around center.
        
        Args:
            x_values: Input values
            
        Returns:
            Exponentially-transformed values
        """
        k_param = 1.0
        return (np.sign(x_values) *
                (np.exp(k_param * np.abs(x_values)) - 1) / (np.exp(k_param) - 1))

    def _apply_power_transformation(self, x_values: np.ndarray) -> np.ndarray:
        """
        Apply power law transformation for concentration around center.
        
        Args:
            x_values: Input values
            
        Returns:
            Power-transformed values
        """
        k_param = 1.5
        return np.sign(x_values) * (np.abs(x_values) ** k_param)

    def _map_transformed_to_prices(self, y_transformed: np.ndarray, x_linear_symmetric: np.ndarray,
                                   position: RangePosition) -> np.ndarray:
        """
        Map transformed y values to actual price range.
        
        Args:
            y_transformed: Transformed values in [-1, 1] range
            x_linear_symmetric: Original linear symmetric values
            position: Range position configuration
            
        Returns:
            Array of prices mapped to actual range
        """
        prices = np.array([])
        current_price = position.current_mid_price
        min_price = position.min_price
        max_price = position.max_price

        # Scale left side (from current_price down to min_price)
        y_left = y_transformed[x_linear_symmetric <= 0]
        if len(y_left) > 0:
            scaled_left = self._scale_left_prices(y_left, current_price, min_price)
            prices = np.append(prices, scaled_left)

        # Scale right side (from current_price up to max_price)
        y_right = y_transformed[x_linear_symmetric > 0]
        if len(y_right) > 0:
            scaled_right = self._scale_right_prices(y_right, current_price, max_price)
            prices = np.append(prices, scaled_right)

        return prices

    def _scale_left_prices(self, y_left: np.ndarray, current_price: float, min_price: float) -> np.ndarray:
        """
        Scale left side prices (below current price).
        
        Args:
            y_left: Transformed y values for left side
            current_price: Current mid price
            min_price: Minimum price boundary
            
        Returns:
            Scaled prices for left side
        """
        if np.min(y_left) < 0:
            return current_price + y_left * (current_price - min_price) / np.abs(np.min(y_left))
        else:
            return np.linspace(min_price, current_price, len(y_left))

    def _scale_right_prices(self, y_right: np.ndarray, current_price: float, max_price: float) -> np.ndarray:
        """
        Scale right side prices (above current price).
        
        Args:
            y_right: Transformed y values for right side
            current_price: Current mid price
            max_price: Maximum price boundary
            
        Returns:
            Scaled prices for right side
        """
        if np.max(y_right) > 0:
            return current_price + y_right * (max_price - current_price) / np.max(y_right)
        else:
            return np.linspace(current_price, max_price, len(y_right))

    def _finalize_price_steps(self, prices: np.ndarray, position: RangePosition) -> np.ndarray:
        """
        Finalize price steps by removing duplicates, sorting, and validating.
        
        Args:
            prices: Raw price array
            position: Range position configuration
            
        Returns:
            Final validated price steps array
        """
        # Handle single density case
        if position.grid_density == 1:
            prices = np.array([position.current_mid_price])
        else:
            # Remove duplicates and sort
            prices = np.unique(prices)
            prices = np.sort(prices)

            # Warn if significantly fewer points than requested
            if len(prices) < position.grid_density * 0.5 and position.grid_density > 1:
                self.logger.warning(
                    f"Generated {len(prices)} unique price steps, less than requested density {position.grid_density}. "
                    "Consider adjusting curve_strength or increasing price range."
                )

        # Clip to ensure bounds are respected
        prices = np.clip(prices, position.min_price, position.max_price)

        self.logger.debug(f"calculate_price_steps ({position.price_steps}): {prices}")
        return prices

    def calculate_grid_allocations(self, pair_instance: Any, position: RangePosition,
                                   available_t1: Optional[float] = None,
                                   available_t2: Optional[float] = None) -> Tuple[
        List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Distribute funds algorithmically across the price range.
        
        Args:
            pair_instance: Trading pair instance
            position: Range position configuration
            available_t1: Available balance for token 1
            available_t2: Available balance for token 2
            
        Returns:
            Tuple of (buy_orders, sell_orders) lists
        """
        symbol = pair_instance.symbol
        t1_balance = available_t1 if available_t1 is not None else pair_instance.t1.dex.free_balance
        t2_balance = available_t2 if available_t2 is not None else pair_instance.t2.dex.free_balance

        self._log_grid_allocation_start(symbol, position, pair_instance, t1_balance, t2_balance)

        self._validate_price_range(position, symbol)

        prices = self.calculate_price_steps(position=position)
        mid_price = position.current_mid_price
        weights = self._calculate_weights(prices, mid_price, position)

        self._log_price_calculation_details(prices, mid_price)

        buy_orders, sell_orders = self._generate_buy_sell_orders(
            prices, weights, position, pair_instance, t1_balance, t2_balance
        )

        self._log_grid_allocation_results(symbol, buy_orders, sell_orders, position.grid_density)

        return buy_orders, sell_orders

    def _log_grid_allocation_start(self, symbol: str, position: RangePosition, pair_instance: Any,
                                   t1_balance: float, t2_balance: float) -> None:
        """
        Log the start of grid allocation process.
        
        Args:
            symbol: Trading pair symbol
            position: Range position configuration
            pair_instance: Trading pair instance
            t1_balance: Available balance for token 1
            t2_balance: Available balance for token 2
        """
        self.logger.info(
            f"Computing grid allocations for {symbol} with {position.grid_density} orders "
            f"from {position.min_price:.4f} to {position.max_price:.4f}"
        )
        self.logger.debug(
            f"Curve='{position.curve}' with strength={position.curve_strength}, "
            f"min_size_pct={position.percent_min_size}"
        )
        self.logger.debug(
            f"Min_price={position.min_price:.6f}, Max_price={position.max_price:.6f}, "
            f"Available balances: {pair_instance.t1.symbol}={t1_balance:.6f}, "
            f"{pair_instance.t2.symbol}={t2_balance:.6f}"
        )

    def _validate_price_range(self, position: RangePosition, symbol: str) -> None:
        """
        Validate the price range configuration.
        
        Args:
            position: Range position configuration
            symbol: Trading pair symbol
            
        Raises:
            ValueError: If price range is invalid
        """
        if position.min_price <= 0 or position.max_price <= position.min_price:
            self.logger.error(
                f"Invalid price range for {symbol}: min={position.min_price}, max={position.max_price}"
            )
            raise ValueError(f"Invalid price range: min={position.min_price}, max={position.max_price}")

        self.logger.debug(f"Validated price range: min={position.min_price:.4f} max={position.max_price:.4f}")

    def _log_price_calculation_details(self, prices: np.ndarray, mid_price: float) -> None:
        """
        Log details about price calculation.
        
        Args:
            prices: Generated price points
            mid_price: Current mid price
        """
        self.logger.debug(f"Generated {len(prices)} price points. Using dynamic mid price: {mid_price:.6f}")

    def _generate_buy_sell_orders(self, prices: np.ndarray, weights: np.ndarray, position: RangePosition,
                                  pair_instance: Any, t1_balance: float, t2_balance: float) -> Tuple[
        List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Generate buy and sell orders from price points and weights.
        
        Args:
            prices: Array of price points
            weights: Array of allocation weights
            position: Range position configuration
            pair_instance: Trading pair instance
            t1_balance: Available balance for token 1
            t2_balance: Available balance for token 2
            
        Returns:
            Tuple of (buy_orders, sell_orders) lists
        """
        mid_price = position.current_mid_price
        step_size = self._calculate_grid_step_size(position)
        buffer_size = step_size * 0.01

        low_threshold = mid_price - buffer_size
        high_threshold = mid_price + buffer_size

        self.logger.debug(
            f"Mid-price gap defined: [{low_threshold:.6f}, {high_threshold:.6f}] (step_size={step_size:.6f})"
        )

        buy_orders = self._create_buy_orders(prices, weights, low_threshold, position, pair_instance, t2_balance)
        sell_orders = self._create_sell_orders(prices, weights, high_threshold, position, pair_instance, t1_balance)

        return buy_orders, sell_orders

    def _calculate_grid_step_size(self, position: RangePosition) -> float:
        """
        Calculate the step size for the grid.
        
        Args:
            position: Range position configuration
            
        Returns:
            Grid step size
        """
        if position.grid_density > 1:
            return (position.max_price - position.min_price) / (position.grid_density - 1)
        else:
            return 0.0

    def _filter_prices_and_weights_below_threshold(self, prices: np.ndarray, weights: np.ndarray,
                                                   threshold: float) -> Tuple[List[float], List[float]]:
        """
        Filter prices and weights below a threshold.
        
        Args:
            prices: Array of price points
            weights: Array of allocation weights
            threshold: Price threshold
            
        Returns:
            Tuple of filtered prices and weights lists
        """
        filtered_prices = [price for price in prices if price < threshold]
        filtered_weights = [weights[i] for i, price in enumerate(prices) if price < threshold]
        return filtered_prices, filtered_weights

    def _calculate_order_sizes(self, weights: List[float], available_balance: float,
                               min_size_pct: float, num_orders: int) -> List[float]:
        """
        Calculate order sizes based on weights and balance.
        
        Args:
            weights: List of allocation weights
            available_balance: Available token balance
            min_size_pct: Minimum order size percentage
            num_orders: Number of orders to create
            
        Returns:
            List of order sizes
        """
        # Calculate minimum order size
        min_size = available_balance * min_size_pct
        total_min_required = min_size * num_orders

        # Adjust minimum if needed
        if total_min_required > available_balance:
            min_size = available_balance / num_orders
            total_min_required = available_balance

        # Start with minimum sizes
        sizes = [min_size] * num_orders
        remaining = available_balance - total_min_required

        # Distribute remaining balance proportionally
        if remaining > 0 and weights and sum(weights) > 0:
            total_weight = sum(weights)
            for i in range(num_orders):
                sizes[i] += (weights[i] / total_weight) * remaining

        return sizes

    def _build_buy_orders(self, buy_prices: List[float], maker_sizes: List[float],
                          pair_instance: Any) -> List[Dict[str, Any]]:
        """
        Build buy order dictionaries from prices and sizes.
        
        Args:
            buy_prices: List of buy order prices
            maker_sizes: List of maker sizes
            pair_instance: Trading pair instance
            
        Returns:
            List of buy order dictionaries
        """
        buy_orders = []

        for price, maker_size in zip(buy_prices, maker_sizes):
            if price <= 0:
                self.logger.error(f"Invalid buy price: {price:.6f}. Skipping this buy order")
                continue

            taker_size = maker_size / price
            buy_orders.append({
                'maker': pair_instance.t2.symbol,
                'maker_size': maker_size,
                'taker': pair_instance.t1.symbol,
                'taker_size': taker_size,
                'price': price,
                'type': 'buy'
            })

        return buy_orders

    def _create_buy_orders(self, prices: np.ndarray, weights: np.ndarray, low_threshold: float,
                           position: RangePosition, pair_instance: Any, t2_balance: float) -> List[Dict[str, Any]]:
        """
        Create buy orders below the low threshold.
        
        Args:
            prices: Array of price points
            weights: Array of allocation weights
            low_threshold: Price threshold for buy orders
            position: Range position configuration
            pair_instance: Trading pair instance
            t2_balance: Available balance for token 2
            
        Returns:
            List of buy order dictionaries
        """
        buy_prices, buy_weights = self._filter_prices_and_weights_below_threshold(prices, weights, low_threshold)

        if not buy_prices:
            return []

        self.logger.debug(f"Calculating {len(buy_prices)} buy orders")

        # Calculate order sizes using new helper
        raw_maker_sizes = self._calculate_order_sizes(
            weights=buy_weights,
            available_balance=t2_balance,
            min_size_pct=position.percent_min_size,
            num_orders=len(buy_prices)
        )

        return self._build_buy_orders(buy_prices, raw_maker_sizes, pair_instance)

    def _build_sell_orders(self, sell_prices: List[float], maker_sizes: List[float],
                           pair_instance: Any) -> List[Dict[str, Any]]:
        """
        Build sell order dictionaries from prices and sizes.
        
        Args:
            sell_prices: List of sell order prices
            maker_sizes: List of maker sizes
            pair_instance: Trading pair instance
            
        Returns:
            List of sell order dictionaries
        """
        sell_orders = []

        for price, maker_size in zip(sell_prices, maker_sizes):
            taker_size = maker_size * price
            sell_orders.append({
                'maker': pair_instance.t1.symbol,
                'maker_size': maker_size,
                'taker': pair_instance.t2.symbol,
                'taker_size': taker_size,
                'price': price,
                'type': 'sell'
            })

        return sell_orders

    def _create_sell_orders(self, prices: np.ndarray, weights: np.ndarray, high_threshold: float,
                            position: RangePosition, pair_instance: Any, t1_balance: float) -> List[Dict[str, Any]]:
        """
        Create sell orders above the high threshold.
        
        Args:
            prices: Array of price points
            weights: Array of allocation weights
            high_threshold: Price threshold for sell orders
            position: Range position configuration
            pair_instance: Trading pair instance
            t1_balance: Available balance for token 1
            
        Returns:
            List of sell order dictionaries
        """
        sell_prices, sell_weights = self._filter_prices_and_weights_above_threshold(prices, weights, high_threshold)

        if not sell_prices:
            return []

        self.logger.debug(f"Calculating {len(sell_prices)} sell orders")

        # Calculate order sizes using new helper
        raw_maker_sizes = self._calculate_order_sizes(
            weights=sell_weights,
            available_balance=t1_balance,
            min_size_pct=position.percent_min_size,
            num_orders=len(sell_prices)
        )

        return self._build_sell_orders(sell_prices, raw_maker_sizes, pair_instance)

    def _filter_prices_and_weights_above_threshold(self, prices: np.ndarray, weights: np.ndarray,
                                                   threshold: float) -> Tuple[List[float], List[float]]:
        """
        Filter prices and weights above a threshold.
        
        Args:
            prices: Array of price points
            weights: Array of allocation weights
            threshold: Price threshold
            
        Returns:
            Tuple of filtered prices and weights lists
        """
        filtered_prices = [price for price in prices if price > threshold]
        filtered_weights = [weights[i] for i, price in enumerate(prices) if price > threshold]
        return filtered_prices, filtered_weights

    def _log_grid_allocation_results(self, symbol: str, buy_orders: List[Dict[str, Any]],
                                     sell_orders: List[Dict[str, Any]], grid_density: int) -> None:
        """
        Log the results of grid allocation.
        
        Args:
            symbol: Trading pair symbol
            buy_orders: Generated buy orders
            sell_orders: Generated sell orders
            grid_density: Requested grid density
        """
        total_generated = len(buy_orders) + len(sell_orders)

        if buy_orders:
            buy_price_range = self._get_price_range(buy_orders)
            self.logger.debug(f"Buy orders price range: {buy_price_range}")

        if sell_orders:
            sell_price_range = self._get_price_range(sell_orders)
            self.logger.debug(f"Sell orders price range: {sell_price_range}")

        self.logger.debug(
            f"Generated {len(buy_orders)} buy orders and {len(sell_orders)} sell orders (Total: {total_generated}).")

    def _get_price_range(self, orders: List[Dict[str, Any]]) -> str:
        """
        Get price range string for a list of orders.
        
        Args:
            orders: List of orders
            
        Returns:
            Formatted price range string
        """
        if not orders:
            return "No orders"

        prices = [order['price'] for order in orders]
        return f"min={min(prices):.6f}, max={max(prices):.6f}"

    def _calculate_weights(self, prices: np.ndarray, mid_price: float, position: RangePosition) -> np.ndarray:
        """
        Calculate fund allocation weights across price grid.

        Four methods for distributing capital:

        Methods:
        - linear: Equal weighting - uniform fund distribution (default)
        - exp_decay: Exponential decay - more funds near mid-price
        - sigmoid: Sigmoid curve - concentrates funding at mid-price
        - constant_product: Constant product (x*y=k) - mimics Uniswap V2

        Args:
            prices: Price points from calculate_price_steps
            mid_price: Current mid-price reference
            position: Strategy config including curve and curve_strength

        Returns:
            Normalized weights for fund allocation
        """
        self.logger.debug(f"Calculating weights with curve='{position.curve}' strength={position.curve_strength}")

        try:
            weights = self._compute_weights_by_curve(prices, mid_price, position)
        except Exception as e:
            self.logger.error(f"Error calculating weights: {str(e)}", exc_info=True)
            weights = self._get_fallback_weights(position.grid_density)
            self.logger.warning("Fell back to uniform weights")

        return self._normalize_weights(weights)

    def _compute_weights_by_curve(self, prices: np.ndarray, mid_price: float, position: RangePosition) -> np.ndarray:
        """
        Compute weights based on the specified curve type.
        
        Args:
            prices: Price points array
            mid_price: Current mid-price reference
            position: Range position configuration
            
        Returns:
            Raw weights array (before normalization)
        """
        if position.curve == 'linear':
            weights = np.linspace(1, 0.001, position.grid_density)
            self.logger.debug("Using linear curve for weights.")
        elif position.curve == 'exp_decay':                                                                                                                            
            # Calculate relative position in range (0-1)                                                                                                               
            range_width = position.max_price - position.min_price                                                                                                      
            if range_width <= 0:                                                                                                                                       
                range_width = 1e-8                                                                                                                                     
                                                                                                                                                                       
            # Calculate normalized distance from mid-price in percentage terms                                                                                         
            relative_dist = np.abs(prices - mid_price) / range_width                                                                                                   
                                                                                                                                                                       
            # Apply exponential decay - highest weight at mid-price                                                                                                    
            weights = np.exp(-position.curve_strength * relative_dist)
        elif position.curve == 'sigmoid':
            k = position.curve_strength
            weights = 1 / (1 + np.exp(-k * (prices - mid_price) / (position.max_price - position.min_price)))
            self.logger.debug(f"Using sigmoid curve for weights with strength: {k}.")
        elif position.curve == 'constant_product':
            weights = 1 / (prices ** 2)
        else:
            weights = np.ones(position.grid_density)
            self.logger.warning(f"Unknown curve type '{position.curve}'. Using uniform weights.")

        return weights

    def _get_fallback_weights(self, grid_density: int) -> np.ndarray:
        """
        Get fallback uniform weights.
        
        Args:
            grid_density: Number of grid points
            
        Returns:
            Uniform weights array
        """
        return np.ones(grid_density) / grid_density

    def _normalize_weights(self, weights: np.ndarray) -> np.ndarray:
        """
        Normalize weights to sum to 1.
        
        Args:
            weights: Raw weights array
            
        Returns:
            Normalized weights array
        """
        if weights.sum() > 0:
            initial_sum = weights.sum()
            weights /= initial_sum
            self.logger.debug(f"Weights normalized: original_sum={initial_sum:.6f} final_sum={weights.sum():.6f}")
            self.logger.debug(f"Weights sample (first 5): {weights[:5]}")
        else:
            self.logger.warning("Sum of weights is zero, cannot normalize. Using uniform distribution")
            weights = np.ones_like(weights) / len(weights)

        return weights

    async def place_grid_orders(self, pair_instance: Any, orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Batch order placement with XBridgeManager.
        
        Args:
            pair_instance: Trading pair instance
            orders: List of orders to place
            
        Returns:
            List of placed order results
            
        Raises:
            Exception: If order placement fails
        """
        self.logger.info(f"Placing {len(orders)} orders for {pair_instance.symbol}")

        try:
            # XBridge calls commented out for beta development
            result = self._simulate_order_placement(orders)
            self.logger.debug(f"Simulated order placement:\n{json.dumps(result, indent=2)}")
            self.logger.info(f"Successfully placed {len(result)} simulated orders")
            return result

        except Exception as e:
            self.logger.error(f"Order placement failed for {pair_instance.symbol}: {str(e)}", exc_info=True)
            raise

    def _simulate_order_placement(self, orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Simulate order placement for testing purposes.
        
        Args:
            orders: List of orders to simulate
            
        Returns:
            List of simulated order results
        """
        return [
            {"id": f"SIMULATED_ORDER_{i}", "status": "simulated", **order}
            for i, order in enumerate(orders)
        ]

    def get_dex_history_file_path(self, pair_name: str) -> str:
        """
        Get path for storing range maker order history.
        
        Args:
            pair_name: Trading pair name
            
        Returns:
            File path for history storage
        """
        unique_id = pair_name.replace("/", "_")
        return f"{self.config_manager.ROOT_DIR}/data/range_maker_{unique_id}_history.yaml"

    async def thread_init_async_action(self, pair_instance: Any) -> None:
        """
        Initial setup for pairs - no action needed for range maker.
        
        Args:
            pair_instance: Trading pair instance (unused)
        """
        pass

    def get_operation_interval(self) -> int:
        """
        Get the operation interval for the strategy.
        
        Returns:
            Interval in seconds between strategy operations
        """
        return 60

    def should_update_cex_prices(self) -> bool:
        """
        Determine if CEX prices should be updated.
        
        Returns:
            False - purely self-contained market-making, no external prices needed
        """
        return False

    def get_startup_tasks(self) -> List[Any]:
        """
        Get list of startup tasks for the strategy.
        
        Returns:
            Empty list - no startup tasks needed for range maker
        """
        return []
