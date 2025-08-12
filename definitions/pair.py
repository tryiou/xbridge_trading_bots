import asyncio
import math
import time

import yaml

from definitions.errors import convert_exception
from definitions.token import Token


class Pair:
    """Represents a trading pair between two tokens.
    
    Manages both DEX and CEX trading operations.
    
    Attributes:
        cfg: Pair configuration dictionary
        name: Unique name identifier
        strategy: Trading strategy name
        t1: First token in pair
        t2: Second token in pair
        symbol: Trading symbol (e.g., 'BTC/BLOCK')
        disabled: Flag if pair is disabled
        variation: Price variation threshold
        dex_enabled: Flag if DEX trading is enabled
        amount_token_to_sell: Sell amount for token
        min_sell_price_usd: Minimum USD sell price
        sell_price_offset: Fractional offset for sell price
        config_manager: Master configuration manager
        dex: DexPair instance for DEX operations
        cex: CexPair instance for CEX operations
    """

    def __init__(self, token1: Token, token2: Token, config_manager, cfg: dict, amount_token_to_sell: float = None,
                 min_sell_price_usd: float = None, sell_price_offset: float = None, strategy: str = None,
                 dex_enabled: bool = True, partial_percent: float = None):
        self.cfg = cfg
        self.name = cfg['name']
        self.strategy = strategy  # e.g.,  pingpong, basic_seller
        self.t1 = token1
        self.t2 = token2
        self.symbol = f'{self.t1.symbol}/{self.t2.symbol}'
        self.disabled = False
        self.variation = None
        self.dex_enabled = dex_enabled
        self.amount_token_to_sell = amount_token_to_sell
        self.min_sell_price_usd = min_sell_price_usd
        if 'sell_price_offset' in self.cfg:
            offset = self.cfg['sell_price_offset']
        else:
            offset = sell_price_offset
        self.sell_price_offset = offset
        self.config_manager = config_manager
        self.dex = DexPair(self, partial_percent)
        self.cex = CexPair(self)


class DexPair:
    """Handles DEX trading operations for a token pair.
    
    Implements order creation, cancellation, and tracking.
    
    Class Constants:
        STATUS_OPEN (0): Order is open
        STATUS_FINISHED (1): Order is finished
        STATUS_OTHERS (2): Order is in other state
        STATUS_ERROR_SWAP (-1): Swap error occurred
        STATUS_CANCELLED_WITHOUT_CALL (-2): Order cancelled without explicit call
        
    Attributes:
        pair: Parent Pair object
        t1: First token in pair
        t2: Second token in pair
        symbol: Trading symbol
        order_history: Last completed order details
        current_order: Current virtual order
        disabled: Flag if DEX operations disabled
        variation: Current price variation
        partial_percent: Partial order percentage
        orderbook: Current DEX orderbook
        orderbook_timer: Last orderbook update timestamp
        order: Active order on DEX
    """

    # Constants for status codes
    STATUS_OPEN = 0
    STATUS_FINISHED = 1
    STATUS_OTHERS = 2
    STATUS_ERROR_SWAP = -1
    STATUS_CANCELLED_WITHOUT_CALL = -2

    PRICE_VARIATION_TOLERANCE_DEFAULT = 0.01

    def __init__(self, pair: Pair, partial_percent: float):
        self.pair = pair
        self.t1 = pair.t1
        self.t2 = pair.t2
        self.symbol = pair.symbol
        self.order_history = None
        self.current_order = None  # Virtual order
        self.disabled = False
        self.variation = None
        self.partial_percent = partial_percent
        self.orderbook = None
        self.orderbook_timer = None
        self.order = None
        self.read_last_order_history()

    async def update_dex_orderbook(self):
        self.orderbook = await self.pair.config_manager.xbridge_manager.dxgetorderbook(detail=3, maker=self.t1.symbol,
                                                                                       taker=self.t2.symbol)
        self.orderbook.pop('detail', None)

    def _get_history_file_path(self):
        return self.pair.config_manager.strategy_instance.get_dex_history_file_path(self.pair.name)

    def read_last_order_history(self):
        if not self.pair.dex_enabled:
            return
        file_path = self._get_history_file_path()
        try:
            with open(file_path, 'r') as fp:
                self.order_history = yaml.safe_load(fp)
        except FileNotFoundError:
            self.pair.config_manager.general_log.info(f"File not found: {file_path}")
        except Exception as e:
            self.pair.config_manager.general_log.error(f"read_pair_last_order_history: {type(e)}, {e}")
            self.order_history = None

    def write_last_order_history(self):
        # Get exact USD amount from our specific config entry # TODO: This comment seems misplaced.

        file_path = self._get_history_file_path()
        try:
            with open(file_path, 'w') as fp:
                yaml.safe_dump(self.order_history, fp)
        except Exception as e:
            self.pair.config_manager.general_log.error(f"error write_pair_last_order_history: {type(e)}, {e}")

    def create_virtual_sell_order(self):
        self.current_order = self._build_sell_order()
        self.pair.config_manager.general_log.info(
            f"Virtual sell order created for {self.pair.name} | "
            f"Symbol: {self.symbol} | "
            f"Maker: {self.t1.symbol} | "
            f"Taker: {self.t2.symbol} | "
            f"Maker size: {self.current_order['maker_size']:.6f} | "
            f"Taker size: {self.current_order['taker_size']:.6f} | "
            f"Price: {self.current_order['dex_price']:.8f}"
        )

    @staticmethod
    def truncate(value: float, digits: int = 8) -> float:
        """
        Truncates a float to a specified number of decimal places without rounding.
        """
        if not isinstance(value, (int, float)):
            return value
        stepper = 10.0 ** digits
        return math.trunc(stepper * value) / stepper

    def _construct_order_dict(self, side, maker_token, taker_token, maker_size, taker_size, original_price,
                              final_price):
        """A helper to construct the common order dictionary structure."""
        order = {
            'symbol': self.symbol,
            'side': side,
            'maker': maker_token.symbol,
            'maker_address': maker_token.dex.address,
            'taker': taker_token.symbol,
            'taker_address': taker_token.dex.address,
            'type': 'partial' if self.partial_percent and side == 'SELL' else 'exact',
            'maker_size': DexPair.truncate(maker_size),
            'taker_size': DexPair.truncate(taker_size),
            'dex_price': DexPair.truncate(final_price),  # The effective price of the order
            'org_pprice': DexPair.truncate(original_price),
            'org_t1price': DexPair.truncate(self.t1.cex.cex_price),
            'org_t2price': DexPair.truncate(self.t2.cex.cex_price),
        }
        if self.partial_percent and side == 'SELL':
            order['minimum_size'] = maker_size * self.partial_percent
        return order

    def _build_sell_order(self):
        original_price = self.pair.config_manager.strategy_instance.calculate_sell_price(self)
        maker_size, offset = self.pair.config_manager.strategy_instance.build_sell_order_details(self)

        final_price = original_price * (1 + offset)
        taker_size = maker_size * final_price

        return self._construct_order_dict(
            side='SELL', maker_token=self.t1, taker_token=self.t2,
            maker_size=maker_size, taker_size=taker_size,
            original_price=original_price, final_price=final_price
        )

    def create_virtual_buy_order(self):
        self.current_order = self._build_buy_order()
        self.pair.config_manager.general_log.info(
            f"Virtual buy order created for {self.pair.name} | "
            f"Symbol: {self.symbol} | "
            f"Maker: {self.t2.symbol} | "
            f"Taker: {self.t1.symbol} | "
            f"Maker size: {self.current_order['maker_size']:.6f} | "
            f"Taker size: {self.current_order['taker_size']:.6f} | "
            f"Price: {self.current_order['dex_price']:.8f}"
        )

    def _build_buy_order(self):
        original_price = self.pair.config_manager.strategy_instance.determine_buy_price(self)
        taker_size, spread = self.pair.config_manager.strategy_instance.build_buy_order_details(self)

        final_price = original_price * (1 - spread)
        maker_size = taker_size * final_price

        return self._construct_order_dict(
            side='BUY', maker_token=self.t2, taker_token=self.t1,
            maker_size=maker_size, taker_size=taker_size,
            original_price=original_price, final_price=final_price
        )

    def check_price_in_range(self, display=False):
        price_variation_tolerance = self.pair.config_manager.strategy_instance.get_price_variation_tolerance(self)

        # The strategy itself now determines how to calculate variation based on the order side.
        # It can return a float (for normal checks) or a list [float] for locked BUY orders.
        var = self.pair.config_manager.strategy_instance.calculate_variation_based_on_side(
            self,
            self.current_order.get('side'),
            self.pair.cex.price,
            self.current_order['org_pprice']
        )
        self._set_variation(var)

        # For logging and comparison, we need the raw float value
        compare_var = var[0] if isinstance(var, list) else var

        if display:
            self._log_price_check(compare_var)

        # If var is a list, it's a signal that the order is locked and should not be cancelled.
        if isinstance(var, list):
            return True  # Price is considered "in range" because it's locked.

        return self._is_price_in_range(compare_var, price_variation_tolerance)

    def _set_variation(self, var):
        # Store the value, preserving the list format for locked orders.
        self.variation = [DexPair.truncate(var[0], 3)] if isinstance(var, list) else DexPair.truncate(var, 3)

    def _log_price_check(self, var):
        self.pair.config_manager.general_log.info(
            f"Price variation check for {self.symbol}: "
            f"Variation: {var:.4f}, Stored variation: {self.variation:.4f}, "
            f"Live price: {self.pair.cex.price:.8f}, Original price: {self.current_order['org_pprice']:.8f}, "
            f"Price ratio: {self.pair.cex.price / self.current_order['org_pprice']:.4f}"
        )

    def _is_price_in_range(self, var, price_variation_tolerance):
        return 1 - price_variation_tolerance < var < 1 + price_variation_tolerance

    def init_virtual_order(self, disabled_coins=None, display=True):
        if self._is_pair_disabled(disabled_coins):
            self.disabled = True
            self.pair.config_manager.general_log.info(f"{self.symbol} disabled due to cc checks: {disabled_coins}")
            return

        if not self.disabled:
            self.pair.config_manager.strategy_instance.init_virtual_order_logic(self, self.order_history)
            if display:
                self._log_virtual_order()

    def _is_pair_disabled(self, disabled_coins):  # Moved here from _initialize_order
        return disabled_coins and (self.t1.symbol in disabled_coins or self.t2.symbol in disabled_coins)

    def _log_virtual_order(self):
        self.pair.config_manager.general_log.info(
            f"live pair prices : {DexPair.truncate(self.pair.cex.price)} {self.symbol} | "
            f"{self.t1.symbol}/USD: {DexPair.truncate(self.t1.cex.usd_price, 3)} | "
            f"{self.t2.symbol}/USD: {DexPair.truncate(self.t2.cex.usd_price, 3)}"
        )
        self.pair.config_manager.general_log.info(f"Current virtual order details: {self.current_order}")

    async def cancel_myorder_async(self):
        if self.order and 'id' in self.order and self.order['id'] is not None:
            await self.pair.config_manager.xbridge_manager.cancelorder(self.order['id'])
        self.order = None

    async def create_order(self, dry_mode=False):
        # First check if pair is already disabled                                                                                                                                               
        if self.disabled:
            return

            # Check the global shutdown state
        if self._is_shutting_down():
            self.pair.config_manager.general_log.warning(
                f"Skipping order creation for {self.symbol} - shutdown in progress"
            )
            return

        self.order = None

        maker_size = f"{self.current_order['maker_size']:.6f}"
        bal = self._get_balance()

        if self._is_balance_valid(bal, maker_size):
            await self._create_order(dry_mode, maker_size)
        else:
            self.pair.config_manager.general_log.error(
                f"dex_create_order, balance too low: {bal}, need: {maker_size} {self.current_order['maker']}")

    def _get_balance(self):
        return self.t2.dex.free_balance if self.current_order['side'] == "BUY" else self.t1.dex.free_balance

    def _is_balance_valid(self, bal, maker_size):
        return bal is not None and maker_size.replace('.', '').isdigit()

    async def _create_order(self, dry_mode, maker_size):
        if float(self._get_balance()) > float(maker_size):
            try:
                order = await self._generate_order(dry_mode)
                if not dry_mode:
                    self.order = order
                    if self.order and 'error' in self.order:
                        self._handle_order_error()
                else:
                    self._log_dry_mode_order(order)
            except Exception as e:
                context = {"pair": self.pair.symbol, "stage": "order_creation"}
                err = convert_exception(e)
                err.context = context
                await self.pair.config_manager.error_handler.handle_async(err)
        else:
            self.pair.config_manager.general_log.error(
                f"dex_create_order, balance too low: {self._get_balance()}, need: {maker_size} {self.current_order['maker']}")

    async def _generate_order(self, dry_mode):
        maker = self.current_order['maker']
        maker_size = f"{self.current_order['maker_size']:.6f}"
        maker_address = self.current_order['maker_address']
        taker = self.current_order['taker']
        taker_size = f"{self.current_order['taker_size']:.6f}"
        taker_address = self.current_order['taker_address']

        if self.partial_percent:
            minimum_size = f"{self.current_order['minimum_size']:.6f}"
            return await self.pair.config_manager.xbridge_manager.makepartialorder(maker, maker_size, maker_address,
                                                                                   taker, taker_size, taker_address,
                                                                                   minimum_size)
        return await self.pair.config_manager.xbridge_manager.makeorder(maker, maker_size, maker_address, taker,
                                                                        taker_size, taker_address)

    def _handle_order_error(self):
        # Store the original error object before it's potentially modified
        original_order_error = self.order

        if 'code' in original_order_error and original_order_error['code'] not in {1019, 1018, 1026, 1032}:
            self.disabled = True  # This line was already here, keep it.

        # This call sets self.order to None in some strategies
        self.pair.config_manager.strategy_instance.handle_order_status_error(self)

        # Log the original error object for better debugging
        self.pair.config_manager.general_log.error(
            f"Error making order on Pair: {self.pair.name} | "
            f"Symbol: {self.symbol} | "
            f"disabled: {self.disabled} | "
            f"Details: {original_order_error}")

    def _log_dry_mode_order(self, order):
        msg = (f"xb.makeorder({self.current_order['maker']}, {self.current_order['maker_size']:.6f}, "
               f"{self.current_order['maker_address']}, {self.current_order['taker']}, "
               f"{self.current_order['taker_size']:.6f}, {self.current_order['taker_address']})")
        self.pair.config_manager.general_log.info(f"dex_create_order, Dry mode enabled. {msg}")

    async def check_order_status(self) -> int:
        counter = 0
        max_count = 3

        while counter < max_count:
            try:
                local_dex_order = await self.pair.config_manager.xbridge_manager.getorderstatus(self.order['id'])
                if 'status' in local_dex_order:
                    self.order = local_dex_order
                    return self._map_order_status()
            except Exception as e:
                self.pair.config_manager.general_log.error(
                    f"Error in dex_check_order_status: {type(e).__name__}, {e}\n{self.order}")
            counter += 1
            await asyncio.sleep(counter)

        self._handle_order_status_error()
        return self.STATUS_CANCELLED_WITHOUT_CALL

    def _map_order_status(self):
        status_mapping = {
            "open": self.STATUS_OPEN,
            "new": self.STATUS_OPEN,
            "created": self.STATUS_OTHERS,
            "initialized": self.STATUS_OTHERS,
            "committed": self.STATUS_OTHERS,
            "finished": self.STATUS_FINISHED,
            "expired": self.STATUS_CANCELLED_WITHOUT_CALL,
            "offline": self.STATUS_ERROR_SWAP,
            "canceled": self.STATUS_CANCELLED_WITHOUT_CALL,
            "invalid": self.STATUS_ERROR_SWAP,
            "rolled back": self.STATUS_ERROR_SWAP,
            "rollback failed": self.STATUS_ERROR_SWAP
        }
        return status_mapping.get(self.order.get('status'), self.STATUS_OPEN)

    def _handle_order_status_error(self):
        self.pair.config_manager.general_log.error(
            f"Error in dex_check_order_status: 'status' not in order. {self.order}")
        if self.pair.strategy in ['pingpong', 'basic_seller']:
            self.order = None

    async def check_price_variation(self, disabled_coins, display=False):
        if 'side' in self.current_order and not self.check_price_in_range(display=display):
            self._log_price_variation()
            if self.order:
                await self.cancel_myorder_async()
            await self._reinit_virtual_order(disabled_coins)

    def _log_price_variation(self):
        msg = (f"check_price_variation, {self.symbol}, variation: {self.variation}, "
               f"{self.order['status']}, live_price: {self.pair.cex.price:.8f}, "
               f"order_price: {self.current_order['dex_price']:.8f}")
        self.pair.config_manager.general_log.warning(msg)
        if self.order and 'id' in self.order and self.order['id'] is not None:
            msg = f"check_price_variation, dex cancel: {self.order['id']}"
            self.pair.config_manager.general_log.warning(msg)

    async def _reinit_virtual_order(self, disabled_coins):
        await self.pair.config_manager.strategy_instance.reinit_virtual_order_after_price_variation(self,
                                                                                                    disabled_coins)

    async def status_check(self, disabled_coins=None, display=False, partial_percent=None):
        await self.pair.cex.update_pricing(display)
        if self.disabled:
            self.pair.config_manager.general_log.info(f"Pair {self.symbol} Disabled, error: {self.order}")
            return

        status = await self._check_order_status(disabled_coins)
        await self._handle_status(status, disabled_coins, display)

    async def _check_order_status(self, disabled_coins):
        if self.order and 'id' in self.order and self.order['id'] is not None:
            return await self.check_order_status()
        if not self.disabled and self.current_order:
            self.init_virtual_order(disabled_coins)
            if self.order and "id" in self.order:
                return await self.check_order_status()
        return None

    async def _handle_status(self, status, disabled_coins, display):
        if status == self.STATUS_OPEN:
            await self.handle_status_open(disabled_coins, display)
        elif status == self.STATUS_FINISHED:
            await self.at_order_finished(disabled_coins)
        elif status == self.STATUS_OTHERS:
            self.check_price_in_range(display=display)
        elif status == self.STATUS_ERROR_SWAP:
            await self.handle_status_error_swap()
        else:
            await self.handle_status_default(disabled_coins)

    async def handle_status_open(self, disabled_coins, display):
        if self._is_pair_disabled(disabled_coins):
            await self._cancel_order_due_to_disabled_coins(disabled_coins)
        else:
            await self.check_price_variation(disabled_coins, display=display)

    async def _cancel_order_due_to_disabled_coins(self, disabled_coins):
        if self.order:
            self.pair.config_manager.general_log.info(
                f"Disabled pairs due to cc_height_check {self.symbol}, {disabled_coins}")
            self.pair.config_manager.general_log.info(f"status_check, dex cancel {self.order['id']}")
            await self.cancel_myorder_async()

    async def handle_status_error_swap(self):
        await self.pair.config_manager.strategy_instance.handle_error_swap_status(self)

    async def handle_status_default(self, disabled_coins=None):
        # This handles statuses like 'canceled' and 'expired'
        if not self.disabled:
            if self.order:
                self.pair.config_manager.general_log.info(
                    f"Order {self.order.get('id')} is {self.order.get('status')}. Re-initializing order for {self.symbol}.")
            else:
                self.pair.config_manager.general_log.info(
                    f"No active order found for {self.symbol}. Re-initializing order.")
            self.order = None  # Clear the completed/cancelled/expired order
            self.init_virtual_order(disabled_coins)  # Re-create the virtual order based on history
            await self.create_order()  # Attempt to place it again

    async def at_order_finished(self, disabled_coins):
        """Handle order completion workflow."""
        side = self._determine_order_side()
        self._log_finished_order_details(side)

        self.order_history = self.current_order
        self.write_last_order_history()
        await self._update_taker_address()
        await self.pair.config_manager.strategy_instance.handle_finished_order(self, disabled_coins)

    def _determine_order_side(self) -> str:
        """Determine order side based on maker token."""
        if self.current_order['maker'] == self.pair.t1.symbol:
            return 'SELL'
        return 'BUY'

    def _construct_order_summary(self, side: str) -> dict:
        """Construct order summary dictionary for logging."""
        return {
            "name": self.pair.cfg['name'],
            "pair": self.pair.symbol,
            "side": side,
            "orderid": self.order['id']
        }

    def _log_finished_order_details(self, side: str):
        """Log detailed information about finished orders."""
        order_summary = self._construct_order_summary(side)

        self.pair.config_manager.general_log.info(f"order FINISHED: {order_summary}")
        self.pair.config_manager.trade_log.info(f"order FINISHED: {order_summary}")
        self.pair.config_manager.trade_log.info(f"virtual order: {self.current_order}")
        self.pair.config_manager.trade_log.info(f"xbridge order: {self.order}")

    async def _update_taker_address(self):
        """Request address update for taker token."""
        if not self.order or 'taker' not in self.order:
            return

        if self.order['taker'] == self.t1.symbol:
            await self.t1.dex.request_addr()
        elif self.order['taker'] == self.t2.symbol:
            await self.t2.dex.request_addr()

    def _is_shutting_down(self) -> bool:
        """Check if shutdown has been requested"""
        try:
            if (self.pair.config_manager and
                    self.pair.config_manager.controller and
                    self.pair.config_manager.controller.shutdown_event and
                    self.pair.config_manager.controller.shutdown_event.is_set()):
                return True
        except Exception as e:
            self.pair.config_manager.general_log.error(
                f"Error checking shutdown status: {e}"
            )
        return False


class CexPair:
    def __init__(self, pair):
        self.pair = pair
        self.t1 = pair.t1
        self.t2 = pair.t2
        self.symbol = pair.symbol
        self.price = None
        self.cex_orderbook = None
        self.cex_orderbook_timer = None

    async def update_pricing(self, display=False):
        await self._update_token_prices()
        if self.t1.cex.cex_price is not None and self.t2.cex.cex_price is not None and self.t2.cex.cex_price != 0:
            self.price = self.t1.cex.cex_price / self.t2.cex.cex_price
        else:
            self.price = None
        if display:
            self.pair.config_manager.general_log.info(
                f"update_pricing: {self.t1.symbol} btc_p: {self.t1.cex.cex_price}, "
                f"{self.t2.symbol} btc_p: {self.t2.cex.cex_price}, "
                f"{self.symbol} price: {self.price}"
            )

    async def _update_token_prices(self):
        if self.t1.cex.cex_price is None:
            await self.t1.cex.update_price()
        if self.t2.cex.cex_price is None:
            await self.t2.cex.update_price()

    async def update_orderbook(self, limit=25, ignore_timer=False):
        update_cex_orderbook_timer_delay = 2
        if ignore_timer or not self.cex_orderbook_timer or time.time() - self.cex_orderbook_timer > update_cex_orderbook_timer_delay:
            self.cex_orderbook = await self.pair.config_manager.ccxt_manager.ccxt_call_fetch_order_book(
                self.pair.config_manager.my_ccxt, self.symbol, self.symbol)
            self.cex_orderbook_timer = time.time()
