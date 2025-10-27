import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, asdict
from enum import Enum
from typing import TYPE_CHECKING, List, Dict, Any, Optional, Tuple

import aiohttp
import yaml

from definitions.error_handler import OperationalError
from definitions.pair import Pair
from definitions.thorchain_def import get_thorchain_quote, execute_thorchain_swap, check_thorchain_path_status, \
    get_actual_swap_received
from definitions.token import Token
from strategies.base_strategy import BaseStrategy

if TYPE_CHECKING:
    from definitions.config_manager import ConfigManager
    from definitions.starter import MainController


class TradeDirection(Enum):
    """Enum for swap directions in the continuous chain."""
    TOKEN1_TO_TOKEN2 = "TOKEN1_TO_TOKEN2"
    TOKEN2_TO_TOKEN1 = "TOKEN2_TO_TOKEN1"


@dataclass
class TradeMetrics:
    """Dataclass for per-trade and cumulative metrics."""
    effective_rate: float
    spread_captured: float
    volume_asymmetry_efficiency: float  # e.g., tokens saved/gained per cycle
    net_accumulation_token1: float
    net_accumulation_token2: float
    dual_growth_rate: float  # % increase in both tokens
    surplus_t1: float = 0.0  # Abs tokens gained/saved T1
    surplus_t2: float = 0.0  # Abs T2
    cumulative_trades: int = 0
    success_count: int = 0  # Add to dataclass
    cumulative_spread_consistency: float = 0.0  # % of trades meeting target


class ContinuousTradeState:
    """Manages persistent state for continuous trading: anchor, direction, metrics."""

    def __init__(self, strategy: 'ThorChainContinuousStrategy', state_id: str = "global"):
        self.strategy = strategy
        self.check_id = state_id
        self.state_file_path = strategy.get_dex_history_file_path("state")

        # Initialize with default state
        self.state_data: Dict[str, Any] = {
            'anchor_rate': float(0.0),
            'last_direction': None,
            'metrics': asdict(TradeMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
            'pause_reason': None,
            'last_update': time.time(),
            'cumulative_surplus_t1': float(0.0),
            'cumulative_surplus_t2': float(0.0),
            'last_sent': float(0.0),
            'last_received': float(0.0),
            'success_count': 0
        }

        # Set balances from strategy config
        self.state_data.update({
            'starting_balances': dict(strategy.starting_balances),
            'virtual_balances': {
                strategy.token1: strategy.starting_balances.get(strategy.token1, 0.0),
                strategy.token2: strategy.starting_balances.get(strategy.token2, 0.0)
            }
        })

        self._ensure_dict_floats('starting_balances')
        self._ensure_dict_floats('virtual_balances')
        self.load()

    def serialize_for_json(self, data: Any) -> Any:
        """Recursively serialize data to JSON-safe primitives (dicts of str-float, etc.)."""
        from decimal import Decimal
        if isinstance(data, dict):
            return {k: self.serialize_for_json(v) for k, v in data.items()}
        elif isinstance(data, (list, tuple)):
            return [self.serialize_for_json(item) for item in data]
        elif isinstance(data, Enum):
            return data.value  # Store enum as its string value
        elif isinstance(data, Decimal):
            return float(data)
        elif hasattr(data, '__dict__'):  # dataclass
            # For dataclass, assume already asdict
            d = vars(data) if hasattr(data, 'vars') else data.__dict__
            return self.serialize_for_json(d)
        elif not isinstance(data, (str, int, float, bool, type(None))):  # Convert custom floats/objects
            try:
                return float(data) if isinstance(data, (int, float)) else str(data)  # Force primitive
            except (ValueError, TypeError):
                self.strategy.config_manager.general_log.warning(
                    f"Skipping non-serializable value: {type(data)} = {data}")
                return str(data)  # Fallback to string representation
        return data  # Primitives are fine

    def load(self) -> None:
        """Load state from JSON file if exists."""
        if self.strategy.dry_mode:
            self._handle_dry_mode_load()
            return

        if os.path.exists(self.state_file_path):
            self._load_existing_state()
        else:
            self._ensure_dict_floats('starting_balances')
            self._ensure_dict_floats('virtual_balances')

    def _handle_dry_mode_load(self):
        self.strategy.config_manager.general_log.debug(
            "Dry-mode: Skipping state load to start fresh without history.")
        self._ensure_dict_floats('starting_balances')
        self._ensure_dict_floats('virtual_balances')

    def _load_existing_state(self):
        try:
            with open(self.state_file_path, 'r') as f:
                loaded = yaml.safe_load(f)
                self._process_loaded_state(loaded)
        except Exception as e:
            self._handle_load_error(e)

    def _process_loaded_state(self, loaded):
        if loaded is not None:
            self.state_data.update(loaded)
            self.strategy.config_manager.general_log.debug(
                f"Loaded continuous state: anchor={self.state_data['anchor_rate']:.8f}")
        else:
            self.strategy.config_manager.general_log.warning(
                f"State file '{self.state_file_path}' exists but is empty/invalid; using defaults.")
        self._set_default_fields()
        self._ensure_numeric_fields()
        self._process_metrics_field()
        self._ensure_dict_floats('starting_balances')
        self._ensure_dict_floats('virtual_balances')
        self.state_data = self.serialize_for_json(self.state_data)
        self._check_and_reset_invalid_anchor_rate()
        # Convert last_direction from string to TradeDirection if needed
        if 'last_direction' in self.state_data and isinstance(self.state_data['last_direction'], str):
            try:
                self.state_data['last_direction'] = TradeDirection(self.state_data['last_direction'])
            except ValueError:
                self.strategy.config_manager.general_log.warning(
                    f"Invalid last_direction value: {self.state_data['last_direction']}; resetting to None")
                self.state_data['last_direction'] = None
        self.strategy.config_manager.general_log.info(
            f"Loaded continuous state: anchor={self.state_data['anchor_rate']:.8f}, last_direction={self.state_data.get('last_direction')}")

    def _set_default_fields(self):
        self.state_data.setdefault('cumulative_surplus_t1', 0.0)
        self.state_data.setdefault('cumulative_surplus_t2', 0.0)
        self.state_data.setdefault('last_sent', 0.0)
        self.state_data.setdefault('last_received', 0.0)
        self.state_data.setdefault('success_count', 0)
        self.state_data.setdefault('virtual_balances', self.state_data['starting_balances'])

    def _ensure_numeric_fields(self):
        numeric_fields = {
            'anchor_rate': 0.0,
            'last_sent': 0.0,
            'last_received': 0.0,
            'cumulative_surplus_t1': 0.0,
            'cumulative_surplus_t2': 0.0,
        }
        int_fields = {
            'success_count': 0,
        }
        for key, default in numeric_fields.items():
            if key in self.state_data:
                try:
                    self.state_data[key] = float(self.state_data[key])
                except (ValueError, TypeError):
                    self.strategy.config_manager.general_log.warning(
                        f"Invalid {key}: {self.state_data[key]}; defaulting to {default}.")
                    self.state_data[key] = default
        for key, default in int_fields.items():
            if key in self.state_data:
                try:
                    self.state_data[key] = int(self.state_data[key])
                except (ValueError, TypeError):
                    self.strategy.config_manager.general_log.warning(
                        f"Invalid {key}: {self.state_data[key]}; defaulting to {default}.")
                    self.state_data[key] = default

    def _process_metrics_field(self):
        if 'metrics' in self.state_data and isinstance(self.state_data['metrics'], dict):
            metrics_keys = ['effective_rate', 'spread_captured', 'volume_asymmetry_efficiency',
                            'net_accumulation_token1', 'net_accumulation_token2', 'dual_growth_rate',
                            'surplus_t1', 'surplus_t2', 'cumulative_trades', 'success_count',
                            'cumulative_spread_consistency']
            default_metrics = {
                'effective_rate': 0.0,
                'spread_captured': 0.0,
                'volume_asymmetry_efficiency': 0.0,
                'net_accumulation_token1': 0.0,
                'net_accumulation_token2': 0.0,
                'dual_growth_rate': 0.0,
                'surplus_t1': 0.0,
                'surplus_t2': 0.0,
                'cumulative_trades': 0,
                'success_count': 0,
                'cumulative_spread_consistency': 0.0
            }
            self.state_data['metrics'].update(
                {k: default_metrics[k] for k in metrics_keys if k not in self.state_data['metrics']})
            for mkey in metrics_keys:
                if mkey in self.state_data['metrics']:
                    val = self.state_data['metrics'][mkey]
                    try:
                        if mkey in ['cumulative_trades', 'success_count']:
                            self.state_data['metrics'][mkey] = int(float(val))
                        else:
                            self.state_data['metrics'][mkey] = float(val)
                    except (ValueError, TypeError):
                        self.strategy.config_manager.general_log.warning(
                            f"Invalid metrics.{mkey}: {val}; default 0.0")
                        self.state_data['metrics'][mkey] = 0.0 if mkey not in ['cumulative_trades',
                                                                               'success_count'] else 0
            self.state_data['metrics'] = TradeMetrics(**self.state_data['metrics'])

    def _check_and_reset_invalid_anchor_rate(self):
        if self.state_data['anchor_rate'] == 1.0:
            self.strategy.config_manager.general_log.warning(
                "Invalid anchor rate 1.00000000 detected; resetting to perform anchor trade.")
            self.state_data['anchor_rate'] = 0.0
            self.save({'anchor_rate': 0.0})

    def _handle_load_error(self, e):
        self.strategy.error_handler.handle(OperationalError(f"Failed to load state: {e}"),
                                           context={"stage": "load_state"})
        self.strategy.config_manager.general_log.warning(
            f"Failed to load state from '{self.state_file_path}': {e}; using defaults.")

    def _ensure_dict_floats(self, key: str) -> None:
        """Ensure state_data[key] is dict with float values."""
        if key not in self.state_data:
            return
        data = self.state_data[key]
        if not isinstance(data, dict):
            self.strategy.config_manager.general_log.warning(f"{key} not dict; defaulting to {{}}.")
            self.state_data[key] = {}
            return
        for k, v in list(data.items()):
            try:
                # Always convert to float
                data[k] = float(v)
            except (ValueError, TypeError):
                self.strategy.config_manager.general_log.warning(f"Invalid {key}.{k}: {v}; defaulting to 0.0.")
                data[k] = 0.0

    def save(self, update_data: Dict[str, Any] = None) -> None:
        """Save state to YAML with optional updates."""
        if update_data:
            # Ensure numeric fields stay as floats
            for key in ['last_sent', 'last_received', 'anchor_rate', 'cumulative_surplus_t1', 'cumulative_surplus_t2']:
                if key in update_data:
                    try:
                        update_data[key] = float(update_data[key])
                    except (TypeError, ValueError):
                        update_data[key] = 0.0
            self.state_data.update(update_data)
        # Ensure all numeric fields are floats
        self._ensure_numeric_fields()
        self._ensure_dict_floats('starting_balances')
        self._ensure_dict_floats('virtual_balances')
        # Set last_update before serialization
        self.state_data['last_update'] = time.time()
        # Create a copy for saving to avoid modifying in-memory state
        save_data = self.state_data.copy()
        # Convert metrics to dict if it's a dataclass
        if 'metrics' in save_data and hasattr(save_data['metrics'], '__dict__'):
            save_data['metrics'] = asdict(save_data['metrics'])
        # Convert enums to strings for serialization
        if 'last_direction' in save_data and isinstance(save_data['last_direction'], TradeDirection):
            save_data['last_direction'] = save_data['last_direction'].value
        # Avoid saving empty/invalid state
        if not save_data or all(v is None for v in save_data.values()):
            self.strategy.config_manager.general_log.warning("Skipping save: state is empty/invalid.")
            return
        try:
            with open(self.state_file_path, 'w') as f:
                yaml.safe_dump(save_data, f)
            self.strategy.config_manager.general_log.debug("State saved successfully in YAML format.")
        except Exception as e:
            self.strategy.error_handler.handle(OperationalError(f"Failed to save state: {e}"),
                                               context={"stage": "save_state"})

    def archive(self, reason: str) -> None:
        """Archive state to metrics file on pause/error."""
        metrics_file = self.strategy.get_dex_history_file_path("metrics")
        try:
            with open(metrics_file, 'a') as f:
                yaml.safe_dump({**self.state_data, 'archive_reason': reason, 'timestamp': time.time()}, f)
            os.remove(self.state_file_path)  # Clear active state
        except Exception as e:
            self.strategy.error_handler.handle(OperationalError(f"Failed to archive state: {e}"),
                                               context={"stage": "archive"})

    def log_trade(self, trade_data: Dict) -> None:                                                                                                                                           
        """Append trade to persistent log file using JSON Lines format."""                                                                                                                   
        log_file = self.strategy.get_dex_history_file_path("trades")                                                                                                                         
        trade_entry = {                                                                                                                                                                      
            **trade_data,                                                                                                                                                                    
            'timestamp': time.time(),                                                                                                                                                        
            'direction': self.state_data.get('last_direction')                                                                                                                               
        }                                                                                                                                                                                    
        try:                                                                                                                                                                                 
            with open(log_file, 'a') as f:                                                                                                                                                   
                # Use JSON Lines format: one JSON object per line                                                                                                                            
                json_line = json.dumps(trade_entry)                                                                                                                                          
                f.write(json_line + '\n')                                                                                                                                                    
            self.strategy.config_manager.general_log.debug("Trade logged in JSON Lines format.")                                                                                             
        except Exception as e:                                                                                                                                                               
            self.strategy.error_handler.handle(OperationalError(f"Failed to log trade: {e}"),                                                                                                
                                               context={"stage": "log_trade"})  



class ThorChainContinuousStrategy(BaseStrategy):

    def __init__(self, config_manager: 'ConfigManager', controller: Optional['MainController'] = None):
        super().__init__(config_manager, controller)
        # Initialize with default values; these will be set by initialize_strategy_specifics
        self.target_spread = 0.01
        self.dry_mode = True
        self.test_mode = False
        self.http_session: Optional['aiohttp.ClientSession'] = None  # Will be set by ConfigManager
        self.thor_monitor_timeout = 600
        self.thor_monitor_poll = 30
        self.thor_api_url = "https://thornode.ninerealms.com"
        self.thor_quote_url = "https://thornode.ninerealms.com/thorchain"
        self.thor_tx_url = "https://thornode.ninerealms.com/thorchain/tx"
        self.thorchain_asset_decimals: Dict[str, int] = {}
        self.pause_file_path = os.path.join(self.config_manager.ROOT_DIR, "data", "TRADING_PAUSED.json")
        self.state: Optional[ContinuousTradeState] = None

    def initialize_strategy_specifics(self, token1: str = None, token2: str = None, target_spread: float = None,
                                      dry_mode: bool = None, test_mode: bool = False, **kwargs):
        # Load defaults from config file first
        config = self.config_manager.config_thorchain_continuous
        config_token1 = getattr(config, 'token1', 'LTC')
        config_token2 = getattr(config, 'token2', 'DOGE')
        config_target_spread = getattr(config, 'target_spread', 0.01)

        # Validate configuration - no defaults, raise error if missing
        if not token1 and not hasattr(config, 'token1'):
            raise OperationalError("token1 must be provided in configuration or arguments")
        if not token2 and not hasattr(config, 'token2'):
            raise OperationalError("token2 must be provided in configuration or arguments")

        self.token1 = token1 or config_token1
        self.token2 = token2 or config_token2
        self.target_spread = target_spread or config_target_spread
        self.dry_mode = dry_mode if dry_mode is not None else True
        self.test_mode = test_mode

        # Validate min_trade_size configuration
        min_trade_config = getattr(config, 'min_trade_size', {})
        if not min_trade_config:
            raise OperationalError("min_trade_size configuration is required")

        if hasattr(min_trade_config, '__dict__'):
            self.min_trade_size = {}
            for k, v in min_trade_config.__dict__.items():
                if v is None:
                    raise OperationalError(f"min_trade_size.{k} must have a value")
                self.min_trade_size[k] = float(v)
        else:
            self.min_trade_size = {}
            for k, v in min_trade_config.items():
                if v is None:
                    raise OperationalError(f"min_trade_size.{k} must have a value")
                self.min_trade_size[k] = float(v)

        # Validate starting_balances configuration
        starting_config = getattr(config, 'starting_balances', {})
        if not starting_config:
            raise OperationalError("starting_balances configuration is required")

        self.starting_balances = {}
        if hasattr(starting_config, '__dict__'):
            for k, v in starting_config.__dict__.items():
                if v is None:
                    raise OperationalError(f"starting_balances.{k} must have a value")
                self.starting_balances[k] = float(v)
        else:
            for k, v in starting_config.items():
                if v is None:
                    raise OperationalError(f"starting_balances.{k} must have a value")
                self.starting_balances[k] = float(v)

        self.anchor_trade_size = getattr(config, 'anchor_trade_size', 1.0)
        self.slippage_max = getattr(config, 'slippage_max', 0.005)
        self.max_fee_threshold = getattr(config, 'max_fee_threshold', 0.001)
        self.max_volatility_threshold = getattr(config, 'max_volatility_threshold', 0.05)
        self.max_failure_threshold = getattr(config, 'max_failure_threshold', 3)
        self.consecutive_failures = 0

        # Safely access monitoring config using attribute access, falling back to defaults.
        self._load_strategy_configs()
        self.pause_file_path = os.path.join(self.config_manager.ROOT_DIR, "data", "TRADING_PAUSED.json")

        self.config_manager.general_log.info("--- Continuous Trading Strategy Parameters ---")
        self.config_manager.general_log.info(f"  - Mode: {'DRY RUN' if self.dry_mode else 'LIVE'}")
        self.config_manager.general_log.info(f"  - Target Spread: {self.target_spread * 100:.2f}%")
        self.config_manager.general_log.info(f"  - Trading Tokens: {self.token1}/{self.token2}")
        self.config_manager.general_log.info(f"  - Test Mode: {self.test_mode}")
        self.config_manager.general_log.info("------------------------------------------")
        self.config_manager.general_log.propagate = False
        # Create state after params set
        self.state = ContinuousTradeState(self)
        # Initialize virtual balances in token objects for dry mode
        # This must happen AFTER tokens are fully initialized in config manager
        # Moved to end of function

    def _load_strategy_configs(self):
        """Loads strategy-specific configurations from the config files, with fallbacks."""
        try:
            def get_nested_attr(obj, attrs, default):
                """Safely gets a nested attribute from an object."""
                for attr in attrs:
                    obj = getattr(obj, attr, None)
                    if obj is None:
                        return default
                return obj

            self.thor_monitor_timeout = get_nested_attr(self.config_manager.config_thorchain_continuous,
                                                        ['monitoring', 'timeout'],
                                                        self.thor_monitor_timeout)
            self.thor_monitor_poll = get_nested_attr(self.config_manager.config_thorchain_continuous,
                                                     ['monitoring', 'poll_interval'],
                                                     self.thor_monitor_poll)
            self.thor_api_url = get_nested_attr(self.config_manager.config_thorchain_continuous,
                                                ['api', 'thornode_url'],
                                                self.thor_api_url)
            self.thor_quote_url = get_nested_attr(self.config_manager.config_thorchain_continuous,
                                                  ['api', 'thornode_quote_url'],
                                                  self.thor_quote_url)
            self.thor_tx_url = get_nested_attr(self.config_manager.config_thorchain_continuous,
                                               ['api', 'thornode_tx_url'],
                                               self.thor_tx_url)
        except Exception as e:
            self.config_manager.error_handler.handle(
                OperationalError(f"Error loading strategy configs: {str(e)}"),
                context={"stage": "_load_strategy_configs"}
            )
            raise

    def get_tokens_for_initialization(self, **kwargs) -> List[str]:
        """Gets the list of tokens from the continuous config file."""
        trading_tokens = [self.token1, self.token2]
        return trading_tokens

    def get_pairs_for_initialization(self, tokens_dict: Dict[str, Token], token1: Optional[str] = None,
                                     token2: Optional[str] = None, **kwargs) -> Dict[str, Pair]:
        """Create single pair for the pool; disable DexPair orderbook."""
        token1 = self.token1
        token2 = self.token2

        # Validate tokens exist in configuration
        if token1 not in tokens_dict:
            raise OperationalError(f"Token {token1} not found in tokens configuration")
        if token2 not in tokens_dict:
            raise OperationalError(f"Token {token2} not found in tokens configuration")

        pair_key = f"{token1}/{token2}"
        pair = Pair(
            token1=tokens_dict[token1],
            token2=tokens_dict[token2],
            cfg={'name': pair_key, 'enabled': True},
            strategy="thorchain_continuous",
            config_manager=self.config_manager
        )
        pair.dex.orderbook = {}  # Ignore; use quotes only
        return {pair_key: pair}

    def should_update_cex_prices(self) -> bool:
        return False  # Pure THORChain, no CEX needed

    def get_operation_interval(self) -> int:
        # Use the monitoring.poll_interval from config_thorchain_continuous for quote polling frequency
        return self.thor_monitor_poll

    def _get_next_direction(self) -> TradeDirection:
        """Determine next alternation direction from state.last_direction."""
        last_dir = self.state.state_data.get('last_direction')
        if last_dir is None:
            # First trade after anchor must be opposite of anchor (which was TOKEN1_TO_TOKEN2)
            return TradeDirection.TOKEN2_TO_TOKEN1
        elif last_dir == TradeDirection.TOKEN1_TO_TOKEN2:
            return TradeDirection.TOKEN2_TO_TOKEN1
        else:
            return TradeDirection.TOKEN1_TO_TOKEN2

    async def _get_trade_amount(self, direction: TradeDirection) -> Optional[float]:
        """Determine the trade amount for the given direction."""
        from_symbol = self.token1 if direction == TradeDirection.TOKEN1_TO_TOKEN2 else self.token2
        min_size = self.min_trade_size.get(from_symbol, 0.0)

        # For non-anchor trades (alternating direction), use the last_received amount reduced by half the spread
        if (self.state.state_data.get('last_direction') is not None and
                direction != self.state.state_data['last_direction']):
            last_received = self.state.state_data.get('last_received', 0)
            if last_received > 0:
                # Apply half spread reduction to trade amount
                amount = last_received * (1 - (self.target_spread / 2))
                # Enforce minimum trade size
                if amount < min_size:
                    self.config_manager.general_log.warning(
                        f"Amount {amount:.8f} for {from_symbol} below min_trade_size {min_size:.8f}; using min_size"
                    )
                    amount = min_size
                return amount
            else:
                self.config_manager.general_log.warning(
                    f"last_received is {last_received} for {from_symbol}; falling back to min_trade_size"
                )
        return min_size

    async def _fetch_quote(self, direction: TradeDirection, amount: float) -> Optional[Dict[str, Any]]:
        """Fetch quote from Thorchain for given direction and amount."""
        from_asset = f"{self.token1}.{self.token1}" if direction == TradeDirection.TOKEN1_TO_TOKEN2 else f"{self.token2}.{self.token2}"
        to_asset = f"{self.token2}.{self.token2}" if direction == TradeDirection.TOKEN1_TO_TOKEN2 else f"{self.token1}.{self.token1}"

        self.config_manager.general_log.debug(
            f"Fetching Thorchain quote for {direction}: "
            f"from_asset={from_asset}, to_asset={to_asset}, amount={amount:.8f}"
        )

        try:
            return await get_thorchain_quote(
                from_asset=from_asset,
                to_asset=to_asset,
                base_amount=amount,
                session=self.http_session,
                quote_url=self.thor_quote_url,
                logger=self.config_manager.general_log
            )
        except Exception as e:
            self.config_manager.general_log.error(
                f"Exception fetching Thorchain quote for {from_asset}->{to_asset}: {type(e).__name__}: {str(e)}",
                exc_info=True
            )
            return None

    async def _process_quote(self, quote: Dict, direction: TradeDirection) -> Optional[Dict[str, Any]]:
        """Standardized quote processing: set decimals, convert amounts, validate structure"""
        if not quote:
            self.config_manager.general_log.warning(
                f"Empty quote response for {direction}: {quote.get('from_asset')}->{quote.get('to_asset')}"
            )
            return None

        if 'expected_amount_out' not in quote:
            self.config_manager.general_log.error(
                f"Invalid quote structure for {direction}: {quote.get('from_asset')}->{quote.get('to_asset')}. "
                f"Response keys: {list(quote.keys())}"
            )
            return None

        from_asset = quote.get('from_asset', '')
        to_asset = quote.get('to_asset', '')
        from_symbol = from_asset.split('.')[0] if from_asset else self.token1
        to_symbol = to_asset.split('.')[0] if to_asset else self.token2

        # Set decimals and convert to base units
        quote['decimals_from'] = await self._get_decimals(from_symbol)
        quote['decimals_to'] = await self._get_decimals(to_symbol)
        quote['expected_amount_out_base'] = (
            float(quote['expected_amount_out']) / 
            (10 ** quote['decimals_to'])
        )

        self.config_manager.general_log.debug(f"Processed quote for {direction}")
        return quote

    async def _poll_quotes(self, pair_instance: 'Pair', direction: TradeDirection) -> Optional[Dict[str, Any]]:
        """Poll quote for specific direction; validate volatility, path, slippage."""
        from_symbol = self.token1 if direction == TradeDirection.TOKEN1_TO_TOKEN2 else self.token2
        amount = await self._get_trade_amount(direction)
        if amount <= 0:
            self.config_manager.general_log.warning(
                f"Invalid amount {amount} for {from_symbol}; skipping quote poll for {direction}."
            )
            return None

        quote = await self._fetch_quote(direction, amount)
        if not quote:
            return None

        # Set from_asset and to_asset for validation
        from_asset = f"{self.token1}.{self.token1}" if direction == TradeDirection.TOKEN1_TO_TOKEN2 else f"{self.token2}.{self.token2}"
        to_asset = f"{self.token2}.{self.token2}" if direction == TradeDirection.TOKEN1_TO_TOKEN2 else f"{self.token1}.{self.token1}"
        quote['from_asset'] = from_asset
        quote['to_asset'] = to_asset
        quote['amount_base'] = amount

        processed_quote = await self._process_quote(quote, direction)
        if not processed_quote:
            return None

        # Check path status
        from_symbol = processed_quote['from_asset'].split('.')[0]
        to_symbol = processed_quote['to_asset'].split('.')[0]
        is_path_active, reason = await check_thorchain_path_status(
            from_chain=from_symbol,
            to_chain=to_symbol,
            session=self.http_session,
            api_url=self.thor_api_url
        )
        if not is_path_active:
            self.config_manager.general_log.warning(f"Path inactive for {direction}: {reason}")
            return None

        return processed_quote

    async def _evaluate_opportunity(self, pair_instance: 'Pair', quote: Dict, direction: TradeDirection) -> Dict[
        str, Any]:
        """Evaluate if quote meets all conditions: spread, asymmetry, dual accumulation, balance."""
        amount = quote['amount_base']
        decimals_to = quote['decimals_to']
        gross_receive = float(quote['expected_amount_out']) / (10 ** decimals_to)
        outbound_fee = float(quote.get('fees', {}).get('outbound', 0)) / (10 ** decimals_to)

        # Calculate core metrics
        profit_data = self._calculate_profitability(amount, gross_receive, outbound_fee)
        spread = profit_data['net_profit_ratio'] / 100  # Normalize to decimal
        q_rate = gross_receive / amount if direction == TradeDirection.TOKEN1_TO_TOKEN2 else amount / gross_receive
        asymmetry = self._calculate_asymmetry(q_rate, self.state.state_data.get('anchor_rate', 0.0), direction)
        projection = await self._project_dual_accumulation(pair_instance, amount, gross_receive, direction, quote)

        # Build report data first
        report_data = self._build_opportunity_report_data(
            direction, amount, gross_receive, outbound_fee, profit_data, asymmetry, projection
        )

        # Validate conditions
        meets_conditions, reason = self._validate_opportunity_conditions(
            amount, asymmetry, projection, pair_instance, direction, report_data
        )

        # Generate report string and include in result
        report_str = self._generate_continuous_report(direction, pair_instance, report_data)
        return self._format_opportunity_result(
            meets_conditions, reason, spread, asymmetry, projection, report_data, report_str,
            profit_data, direction, quote, amount
        )

    def _validate_opportunity_conditions(self, amount: float, asymmetry: float,
                                         projection: Dict, pair_instance: 'Pair',
                                         direction: TradeDirection, report_data: Dict[str, Any]) -> Tuple[
        bool, List[str]]:
        """Check if opportunity meets execution conditions."""
        from_token = pair_instance.t1 if direction == TradeDirection.TOKEN1_TO_TOKEN2 else pair_instance.t2
        available = from_token.dex.free_balance or 0 if not self.dry_mode else self.state.state_data[
            'virtual_balances'].get(from_token.symbol, 0.0)
        balance_ok = available >= amount

        # Calculate actual spread as rate difference
        anchor_rate = self.state.state_data.get('anchor_rate', 0.0)
        if anchor_rate > 0:
            if direction == TradeDirection.TOKEN1_TO_TOKEN2:
                current_rate = report_data['gross_thorchain_received_t2'] / report_data['order_amount']
                actual_spread = (current_rate - anchor_rate) / anchor_rate
            else:
                current_rate = report_data['order_amount'] / report_data['gross_thorchain_received_t1']
                actual_spread = (anchor_rate - current_rate) / anchor_rate
        else:
            actual_spread = 0.0

        meets_conditions = (
                actual_spread >= self.target_spread and
                asymmetry > 0 and
                projection['both_positive'] and
                balance_ok
        )
        reason = []
        if actual_spread < self.target_spread:
            reason.append(f"Spread vs anchor {actual_spread:+.2%} < target {self.target_spread:.2%}")
        if asymmetry <= 0:
            reason.append(f"Asymmetry {asymmetry:.2%} <= 0")
        if not balance_ok:
            reason.append(f"Insufficient balance: need {amount:.8f}, have {available:.8f}")

        return meets_conditions, reason

    def _build_opportunity_report_data(self, direction: TradeDirection, amount: float,
                                       gross_receive: float, outbound_fee: float,
                                       profit_data: Dict, asymmetry: float,
                                       projection: Dict) -> Dict[str, Any]:
        """Build trading opportunity report data."""
        report_data = {
            'order_amount': amount,
            'gross_thorchain_received_t1': gross_receive if direction == TradeDirection.TOKEN2_TO_TOKEN1 else 0,
            'gross_thorchain_received_t2': gross_receive if direction == TradeDirection.TOKEN1_TO_TOKEN2 else 0,
            'outbound_fee_t1': outbound_fee if direction == TradeDirection.TOKEN2_TO_TOKEN1 else 0,
            'outbound_fee_t2': outbound_fee if direction == TradeDirection.TOKEN1_TO_TOKEN2 else 0,
            'network_fee_t1_ratio': profit_data[
                'network_fee_ratio'] if direction == TradeDirection.TOKEN2_TO_TOKEN1 else 0,
            'network_fee_t2_ratio': profit_data[
                'network_fee_ratio'] if direction == TradeDirection.TOKEN1_TO_TOKEN2 else 0,
            'net_thorchain_received_t1': gross_receive - outbound_fee if direction == TradeDirection.TOKEN2_TO_TOKEN1 else 0,
            'net_thorchain_received_t2': gross_receive - outbound_fee if direction == TradeDirection.TOKEN1_TO_TOKEN2 else 0,
            'net_profit_t1_ratio': profit_data[
                'net_profit_ratio'] if direction == TradeDirection.TOKEN2_TO_TOKEN1 else 0,
            'net_profit_t2_ratio': profit_data[
                'net_profit_ratio'] if direction == TradeDirection.TOKEN1_TO_TOKEN2 else 0,
            'net_profit_t1_amount': profit_data[
                'net_profit_amount'] if direction == TradeDirection.TOKEN2_TO_TOKEN1 else 0,
            'net_profit_t2_amount': profit_data[
                'net_profit_amount'] if direction == TradeDirection.TOKEN1_TO_TOKEN2 else 0,
            'asymmetry': asymmetry,
            'surplus_t1': projection['surplus_t1'],
            'surplus_t2': projection['surplus_t2'],
            'both_positive': projection['both_positive']  # Add this key
        }
        return report_data

    def _format_opportunity_result(self, meets_conditions: bool, reason: List[str],
                                   spread: float, asymmetry: float, projection: Dict,
                                   report_data: Dict, report_str: str, profit_data: Dict,
                                   direction: TradeDirection, quote: Dict, amount: float) -> Dict[str, Any]:
        """Format final opportunity evaluation result."""
        opportunity_details = f"Opportunity meets conditions for {direction.value}: " \
                              f"Spread {spread:.2%}, Asymmetry {asymmetry:.2%}, Dual Growth: Yes" \
            if meets_conditions else None

        return {
            'meets_conditions': meets_conditions,
            'reason': '; '.join(reason) if reason else None,
            'spread': spread,
            'asymmetry': asymmetry,
            'projection': projection,
            'report_data': report_data,
            'report': report_str,  # Add generated report string
            'opportunity_details': opportunity_details,
            'profitable': profit_data['is_profitable'],
            'execution_data': {
                'direction': direction,
                'quote': quote,
                'amount': amount
            } if meets_conditions else None
        }

    async def _execute_swap(self, quote: Dict, direction: TradeDirection) -> Dict[str, Any]:
        """Unified swap execution for both dry and live modes"""
        if self.dry_mode:
            return await self._execute_dry_swap(quote, direction)
        return await self._execute_live_swap(quote, direction)

    async def execute_continuous_swap(self, eval_result: Dict, check_id: str) -> Dict[str, Any]:
        """Execute continuous swap with unified dry/live handling"""
        ex_data = eval_result['execution_data']
        direction = ex_data['direction']
        quote = ex_data['quote']
        log_prefix = check_id if self.test_mode else check_id[:8]

        self.config_manager.general_log.info(
            f"{'[DRY RUN] ' if self.dry_mode else ''}[{log_prefix}] Executing {direction.value} swap")

        if not (fresh_quote := await self._revalidate_quote(quote)):
            return {'success': False, 'reason': 'Invalid quote'}

        # Unified execution
        swap_result = await self._execute_swap(fresh_quote, direction)

        if not swap_result['success']:
            await self._handle_swap_failure(swap_result, direction, eval_result, log_prefix)
        return swap_result

    async def _handle_swap_failure(self, result: Dict, direction: TradeDirection,
                                   eval_result: Dict, log_prefix: str) -> None:
        """Handle swap failure and pause trading if refunded."""
        if 'refunded' in result.get('reason', '').lower():
            reason = f"Swap refunded for {direction.value}"
            with open(self.pause_file_path, 'w') as f:
                yaml.dump({'reason': reason, 'trade_details': eval_result}, f)

            self.config_manager.general_log.critical(f"[{log_prefix}] {reason}")
            self.state.save({
                'status': 'AWAITING_REFUND',
                'awaiting_refund_since': time.time()
            })

    def _calculate_profitability(self, cost: float, gross: float, fee: float) -> Dict[str, Any]:
        """Calculate profitability metrics."""
        net_received = gross - fee
        net_profit = net_received - cost
        profit_ratio = (net_profit / cost) * 100 if cost > 0 else 0
        fee_ratio = (fee / gross) * 100 if gross > 0 else 0

        return {
            'net_profit_amount': net_profit,
            'net_profit_ratio': profit_ratio,
            'is_profitable': net_profit > 0 and (net_profit / cost) > self.target_spread,
            'network_fee_ratio': fee_ratio
        }

    def _update_virtual_after_trade(self, direction: TradeDirection, amount_sent: float,
                                    amount_received: float) -> None:
        """Update virtual balances in dry mode post-trade."""
        # Ensure amounts are floats
        amount_sent = float(amount_sent)
        amount_received = float(amount_received)

        virtual_t1 = float(self.state.state_data['virtual_balances'].get(self.token1, 0.0))
        virtual_t2 = float(self.state.state_data['virtual_balances'].get(self.token2, 0.0))
        if direction == TradeDirection.TOKEN1_TO_TOKEN2:
            self.state.state_data['virtual_balances'][self.token1] = virtual_t1 - amount_sent
            self.state.state_data['virtual_balances'][self.token2] = virtual_t2 + amount_received
        else:
            self.state.state_data['virtual_balances'][self.token2] = virtual_t2 - amount_sent
            self.state.state_data['virtual_balances'][self.token1] = virtual_t1 + amount_received
        # Save state - will handle float conversion automatically
        self.state.save({'virtual_balances': self.state.state_data['virtual_balances']})

    def get_startup_tasks(self) -> list:
        """
        Continuous strategy has its own state recovery mechanism and should not
        blindly cancel all orders on startup.
        """
        return []

    async def thread_init_async_action(self, pair_instance: 'Pair'):
        pass

    async def process_pair_async(self, pair_instance: 'Pair') -> None:
        """Core continuous trading logic executed asynchronously."""
        if self._is_paused():
            return

        # Execute anchor trade once if anchor rate is not set
        # Check if anchor rate is already set to avoid re-executing
        anchor_rate = self.state.state_data.get('anchor_rate', 0.0)
        if anchor_rate <= 0:
            # Check if we've already tried to execute anchor trade in this instance
            if not hasattr(self, '_anchor_execution_attempted'):
                self._anchor_execution_attempted = True
                await self._execute_anchor_trade()
                return  # Skip rest of cycle after anchor trade
            else:
                # Anchor trade was attempted but rate is still <= 0, which indicates failure
                # We need to handle this case to prevent infinite loops
                self.config_manager.general_log.warning(
                    "Anchor trade appears to have failed. Trading is paused."
                )
                return
        # Ensure the flag is set for future reference
        if not hasattr(self, '_anchor_executed'):
            self._anchor_executed = True

        check_id = str(uuid.uuid4())
        log_prefix = check_id if self.test_mode else check_id[:8]

        if pair_instance.disabled:
            return

        self.config_manager.general_log.info(
            f"[{log_prefix}] Checking continuous trading for {pair_instance.symbol}...")

        # Determine next required trade direction (opposite of last trade)
        next_direction = self._get_next_direction()
        self.config_manager.general_log.debug(
            f"[{log_prefix}] Next required direction: {next_direction.value}")

        # Fetch quote only for the required direction
        quote = await self._poll_quotes(pair_instance, next_direction)
        if quote is None:
            self.config_manager.general_log.info(
                f"[{log_prefix}] No valid quote for {next_direction.value}; skipping.")
            return

        # Evaluate opportunity only for the required direction
        eval_result = await self._evaluate_opportunity(pair_instance, quote, next_direction)
        if not eval_result:
            self.config_manager.general_log.info(
                f"[{log_prefix}] Evaluation failed for {next_direction.value}; skipping.")
            return

        # Generate and log comprehensive report
        report_str = self._generate_trading_report(eval_result, next_direction, pair_instance)
        self.config_manager.general_log.info(report_str)

        # Handle profitable opportunity if conditions met
        if eval_result['meets_conditions']:
            self.config_manager.general_log.info(f"[{log_prefix}] {eval_result['opportunity_details']}")
            swap_result = await self.execute_continuous_swap(eval_result, check_id)

            if swap_result['success']:
                # Common success handling for both modes
                await self._update_anchor_and_metrics(swap_result, next_direction, is_anchor=False)
        else:
            self.config_manager.general_log.info(
                f"[{log_prefix}] Conditions not met for {next_direction.value}: {eval_result['reason']}")

        self.config_manager.general_log.info(f"[{log_prefix}] Finished check for {pair_instance.symbol}.")

    def _is_paused(self) -> bool:
        """Check if trading is paused."""
        if not os.path.exists(self.pause_file_path):
            return False

        try:
            with open(self.pause_file_path, 'r') as f:
                pause_reason = yaml.load(f).get('reason', 'Unknown reason.')
            self.config_manager.general_log.warning(
                f"TRADING PAUSED. Reason: {pause_reason}. "
                f"Bot is monitoring for refund. Trading will resume automatically."
            )
            return True
        except (Exception) as e:
            self.config_manager.error_handler.handle(
                OperationalError(f"Could not read pause file: {str(e)}"),
                context={"file": self.pause_file_path}
            )
            return True

    def _generate_trading_report(self, eval_result: Dict, direction: TradeDirection, pair_instance: 'Pair') -> str:
        """Generate comprehensive trading report showing Thorchain quote vs required conditions."""
        # Get anchor trade details
        anchor_rate = self.state.state_data.get('anchor_rate', 0.0)
        last_direction = self.state.state_data.get('last_direction')
        last_sent = self.state.state_data.get('last_sent', 0.0)
        last_received = self.state.state_data.get('last_received', 0.0)
        last_update = self.state.state_data.get('last_update', 0.0)
        timestamp_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_update)) if last_update > 0 else "N/A"

        report_lines = [
            f"\nContinuous Trading Report for {pair_instance.symbol}:",
            f"  Last Anchor Trade:",
            f"    - Direction: {last_direction.value if last_direction else 'N/A'}",
            f"    - Sent: {last_sent:.8f} {pair_instance.t1.symbol if last_direction == TradeDirection.TOKEN1_TO_TOKEN2 else pair_instance.t2.symbol}",
            f"    - Received: {last_received:.8f} {pair_instance.t2.symbol if last_direction == TradeDirection.TOKEN1_TO_TOKEN2 else pair_instance.t1.symbol}",
            f"    - Effective Rate: {anchor_rate:.8f} T2/T1" if last_direction == TradeDirection.TOKEN1_TO_TOKEN2 else f"    - Effective Rate: {1 / anchor_rate:.8f} T1/T2" if anchor_rate != 0 else "    - Effective Rate: N/A",
            f"    - Timestamp: {timestamp_str}",
            f"  Thorchain Quote Analysis for {direction.value}:"
        ]

        # Add Thorchain quote details
        report_data = eval_result['report_data']
        if direction == TradeDirection.TOKEN1_TO_TOKEN2:
            report_lines.extend([
                f"    - Sell Amount: {report_data['order_amount']:.8f} {pair_instance.t1.symbol}",
                f"    - Expected Gross Receive: {report_data['gross_thorchain_received_t2']:.8f} {pair_instance.t2.symbol}",
                f"    - Expected Net Receive: {report_data['net_thorchain_received_t2']:.8f} {pair_instance.t2.symbol} (after fees)",
                f"    - Effective Rate: {report_data['gross_thorchain_received_t2'] / report_data['order_amount']:.8f} T2/T1"
            ])
        else:
            report_lines.extend([
                f"    - Sell Amount: {report_data['order_amount']:.8f} {pair_instance.t2.symbol}",
                f"    - Expected Gross Receive: {report_data['gross_thorchain_received_t1']:.8f} {pair_instance.t1.symbol}",
                f"    - Expected Net Receive: {report_data['net_thorchain_received_t1']:.8f} {pair_instance.t1.symbol} (after fees)",
                f"    - Effective Rate: {report_data['order_amount'] / report_data['gross_thorchain_received_t1']:.8f} T2/T1"
            ])

        # Add spread analysis (shows positive/negative spread)
        if anchor_rate > 0:
            if direction == TradeDirection.TOKEN1_TO_TOKEN2:
                current_rate = report_data['gross_thorchain_received_t2'] / report_data['order_amount']
                spread = (current_rate - anchor_rate) / anchor_rate
            else:
                current_rate = report_data['order_amount'] / report_data['gross_thorchain_received_t1']
                spread = (anchor_rate - current_rate) / anchor_rate
            report_lines.append(f"    - Spread vs Anchor: {spread:+.2%}")  # + sign shows negative/positive

        # Add execution conditions with clear requirements
        report_lines.append(f"  Required Conditions to Execute:")
        report_lines.append(
            f"    - Sell Amount: {report_data['order_amount']:.8f} {pair_instance.t2.symbol if direction == TradeDirection.TOKEN2_TO_TOKEN1 else pair_instance.t1.symbol}")

        # Calculate required net receive based on spread
        if direction == TradeDirection.TOKEN2_TO_TOKEN1:
            required_net_receive = last_sent * (1 + (self.target_spread / 2))
            token_symbol = pair_instance.t1.symbol
        else:
            required_net_receive = last_received * (1 + (self.target_spread / 2))
            token_symbol = pair_instance.t2.symbol
        report_lines.append(f"    - Expected Net Receive: {required_net_receive:.8f} {token_symbol}")

        # Calculate required effective rate in T2/T1
        if direction == TradeDirection.TOKEN2_TO_TOKEN1:
            required_rate = report_data['order_amount'] / required_net_receive
        else:
            required_rate = required_net_receive / report_data['order_amount']
        rate_unit = "T2/T1"
        report_lines.append(f"    - Effective Rate: {required_rate:.8f} {rate_unit}")

        report_lines.append(f"    - Minimum Spread: {self.target_spread:.2%}")

        # Calculate dual accumulation projection for required conditions
        if direction == TradeDirection.TOKEN2_TO_TOKEN1:
            t1_projection = required_net_receive - last_sent
            t2_projection = last_received - report_data['order_amount']
        else:
            t1_projection = last_sent - report_data['order_amount']
            t2_projection = required_net_receive - last_received

        both_positive_required = t1_projection > 0 and t2_projection > 0
        report_lines.append(
            f"    - Dual Accumulation Projected: {'Yes' if both_positive_required else 'No'} (T1: {t1_projection:.8f}, T2: {t2_projection:.8f})")

        return "\n".join(report_lines)

    def _generate_continuous_report(self, direction: TradeDirection, pair_instance: 'Pair',
                                    report_data: Dict[str, Any]) -> str:
        """Generates a formatted string report for a given trading direction."""
        if direction == TradeDirection.TOKEN1_TO_TOKEN2:
            # Sell t1 for t2
            dir_header = f"  {direction.value}: Sell {pair_instance.t1.symbol} for {pair_instance.t2.symbol} on Thorchain"
            report = (
                f"{dir_header}\n"
                f"    - Swap:  Sell {report_data['order_amount']:.8f} {pair_instance.t1.symbol} -> Gross Receive {report_data['gross_thorchain_received_t2']:.8f} {pair_instance.t2.symbol}\n"
                f"    - Thorchain Fee:  {report_data['outbound_fee_t2']:.8f} {pair_instance.t2.symbol} ({report_data['network_fee_t2_ratio']:.2f}%)\n"
                f"    - Net Receive:    {report_data['net_thorchain_received_t2']:.8f} {pair_instance.t2.symbol}\n"
                f"    - Net Profit:     {report_data['net_profit_t2_ratio']:.2f}% ({report_data['net_profit_t2_amount']:+.8f} {pair_instance.t2.symbol})"
            )
        else:
            # Buy t1 with t2
            dir_header = f"  {direction.value}: Buy {pair_instance.t1.symbol} with {pair_instance.t2.symbol} on Thorchain"
            report = (
                f"{dir_header}\n"
                f"    - Swap:  Sell {report_data['order_amount']:.8f} {pair_instance.t2.symbol} -> Gross Receive {report_data['gross_thorchain_received_t1']:.8f} {pair_instance.t1.symbol}\n"
                f"    - Thorchain Fee:  {report_data['outbound_fee_t1']:.8f} {pair_instance.t1.symbol} ({report_data['network_fee_t1_ratio']:.2f}%)\n"
                f"    - Net Receive:    {report_data['net_thorchain_received_t1']:.8f} {pair_instance.t1.symbol}\n"
                f"    - Net Profit:     {report_data['net_profit_t1_ratio']:.2f}% ({report_data['net_profit_t1_amount']:+.8f} {pair_instance.t1.symbol})"
            )
        return report

    async def _monitor_thorchain_swap(self, txid: str) -> str:
        """Monitor swap status with timeout."""
        from definitions.thorchain_def import get_thorchain_tx_status

        if self.dry_mode:
            self.config_manager.general_log.info(f"[DRY RUN] Simulated swap success for {txid}")
            return 'success'

        start_time = time.time()
        while time.time() - start_time < self.thor_monitor_timeout:
            status = await get_thorchain_tx_status(txid, self.http_session, self.thor_tx_url)
            self.config_manager.general_log.info(f"Monitoring tx {txid}: {status}")

            if status == 'success':
                return 'success'
            if status == 'refunded':
                self._increment_failures("Swap refunded")
                return 'refunded'

            await asyncio.sleep(self.thor_monitor_poll)

        self._increment_failures("Swap timeout")
        return 'pending'

    async def _get_decimals(self, chain_symbol: str) -> int:
        """Fetch and cache decimals for chain from THORChain API."""
        if chain_symbol not in self.thorchain_asset_decimals:
            try:
                from definitions.thorchain_def import _get_thorchain_decimals
                self.thorchain_asset_decimals[chain_symbol] = await _get_thorchain_decimals(chain_symbol,
                                                                                            self.http_session,
                                                                                            self.thor_api_url)
            except Exception as e:
                self.config_manager.general_log.warning(
                    f"Failed to fetch decimals for {chain_symbol}: {e}; defaulting to 8")
                self.thorchain_asset_decimals[chain_symbol] = 8
            self.config_manager.general_log.debug(
                f"Cached decimals for {chain_symbol}: {self.thorchain_asset_decimals[chain_symbol]}")
        return self.thorchain_asset_decimals[chain_symbol]

    async def _project_dual_accumulation(self, pair: Pair, amount: float, expected_out: float,
                                         direction: TradeDirection, quote: Dict) -> Dict[str, Any]:
        """Project net balances post-trade; check if both tokens grow vs starting."""
        # Calculate net received after fees
        decimals_to = quote.get('decimals_to', 8)
        outbound_fee_base = float(quote.get('fees', {}).get('outbound', 0)) / (10 ** decimals_to)
        net_out = expected_out - outbound_fee_base

        # Calculate surplus based on previous trade
        surplus_t1, surplus_t2 = self._calculate_surplus(amount, net_out, direction)

        # Project new balances
        current_t1 = pair.t1.dex.total_balance or 0
        current_t2 = pair.t2.dex.total_balance or 0
        if direction == TradeDirection.TOKEN1_TO_TOKEN2:  # Sell: +net_out T2, -amount T1
            projected_t1 = current_t1 - amount
            projected_t2 = current_t2 + net_out
        else:  # Buy: +net_out T1, -amount T2
            projected_t1 = current_t1 + net_out
            projected_t2 = current_t2 - amount

        # Check dual accumulation
        cumulative_surplus_t1 = self.state.state_data.get('cumulative_surplus_t1', 0.0)
        cumulative_surplus_t2 = self.state.state_data.get('cumulative_surplus_t2', 0.0)
        starting_t1 = self._get_starting_balance(self.token1)
        starting_t2 = self._get_starting_balance(self.token2)
        both_positive = (projected_t1 > starting_t1 + cumulative_surplus_t1) and (
                projected_t2 > starting_t2 + cumulative_surplus_t2)

        return {
            'both_positive': both_positive,
            'projected_token1': projected_t1,
            'projected_token2': projected_t2,
            'surplus_t1': surplus_t1,
            'surplus_t2': surplus_t2
        }

    def _safe_float(self, value, default=0.0) -> float:
        """Safely convert value to float handling dict edge case."""
        if isinstance(value, dict):
            return float(value.get('value', default))
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _calculate_surplus(self, amount: float, net_out: float, direction: TradeDirection) -> Tuple[float, float]:
        """Calculate surplus tokens gained from trade."""
        previous_last_sent = self._safe_float(self.state.state_data.get('last_sent', 0.0))
        previous_last_received = self._safe_float(self.state.state_data.get('last_received', 0.0))

        if previous_last_sent == 0 or previous_last_received == 0:
            return 0.0, 0.0  # Anchor trade has no surplus

        if direction == TradeDirection.TOKEN1_TO_TOKEN2:
            # Selling: we received T2 last time, now sending T1
            surplus_t2 = max(net_out - previous_last_sent, 0.0)
            surplus_t1 = max(previous_last_received - amount, 0.0)
        else:
            # Buying: we received T1 last time, now sending T2
            surplus_t1 = max(net_out - previous_last_sent, 0.0)
            surplus_t2 = max(previous_last_received - amount, 0.0)

        return surplus_t1, surplus_t2

    async def _revalidate_quote(self, quote: Dict) -> Optional[Dict]:
        """Re-fetch quote immediately before execution for freshness."""
        temporary_session_created = False
        try:
            if self.http_session is None or self.http_session.closed:
                self.http_session = aiohttp.ClientSession()
                temporary_session_created = True
                self.config_manager.general_log.debug("Created temporary HTTP session for quote revalidation.")

            fresh_quote = await get_thorchain_quote(
                from_asset=quote['from_asset'],
                to_asset=quote['to_asset'],
                base_amount=quote['amount_base'],
                session=self.http_session,
                quote_url=self.thor_quote_url,
                logger=self.config_manager.general_log
            )
            if not fresh_quote:
                self.config_manager.general_log.warning("Quote invalid during revalidation.")
                return None

            # Preserve amount_base from original quote
            fresh_quote['amount_base'] = quote['amount_base']
            # Set from_asset and to_asset for processing
            fresh_quote['from_asset'] = quote['from_asset']
            fresh_quote['to_asset'] = quote['to_asset']

            # Use standard processing
            processed_quote = await self._process_quote(fresh_quote, None)  # Direction not needed for revalidation
            if not processed_quote:
                return None

            # Check expiration
            if time.time() - fresh_quote.get('expiry', 0) > 5:
                self.config_manager.general_log.warning("Quote expired during revalidation.")
                return None

            return processed_quote
        finally:
            if temporary_session_created:
                await self.http_session.close()
                self.config_manager.general_log.debug("Closed temporary HTTP session after quote revalidation.")

    async def _execute_dry_swap(self, quote: Dict, direction: TradeDirection) -> Dict[str, Any]:
        """Simulate swap execution in dry mode"""
        from_token = self.token1 if direction == TradeDirection.TOKEN1_TO_TOKEN2 else self.token2
        to_token = self.token2 if direction == TradeDirection.TOKEN1_TO_TOKEN2 else self.token1

        # Use real quote values for realistic simulation
        decimals_to = quote.get('decimals_to', 8)
        expected_base = float(quote['expected_amount_out']) / (10 ** decimals_to)
        outbound_fee_base = float(quote.get('fees', {}).get('outbound', 0)) / (10 ** decimals_to)
        actual_received = expected_base - outbound_fee_base

        self.config_manager.general_log.info(
            f"[DRY RUN] Simulated Thorchain swap: send {quote['amount_base']:.8f} {from_token} -> "
            f"receive {actual_received:.8f} {to_token}")

        return self._prepare_swap_result(
            f"mock_thor_txid_{int(time.time())}",  # Simulate txid
            float(quote['amount_base']),
            actual_received,
            direction,
            'success'
        )

    async def _execute_live_swap(self, quote: Dict, direction: TradeDirection) -> Dict[str, Any]:
        """Execute real swap on THORChain."""
        pair = next(iter(self.config_manager.pairs.values()))
        from_token_symbol = pair.t1.symbol if direction == TradeDirection.TOKEN1_TO_TOKEN2 else pair.t2.symbol
        to_token_symbol = pair.t2.symbol if direction == TradeDirection.TOKEN1_TO_TOKEN2 else pair.t1.symbol
        amount = quote['amount_base']
        to_address = quote['inbound_address']
        memo = quote['memo']

        rpc_config = self.config_manager.xbridge_manager.xbridge_conf.get(from_token_symbol)
        decimal_places = quote.get('decimals_from', 8)

        txid = await execute_thorchain_swap(
            from_token_symbol=from_token_symbol,
            to_address=to_address,
            amount=amount,
            memo=memo,
            rpc_config=rpc_config,
            decimal_places=decimal_places,
            logger=self.config_manager.general_log,
            test_mode=False
        )
        if not txid:
            return {'success': False, 'reason': 'Execution failed'}

        # Validate fees
        decimals_to = quote.get('decimals_to', 8)
        outbound_fee_base = float(quote.get('fees', {}).get('outbound', 0)) / (10 ** decimals_to)
        if outbound_fee_base > self.max_fee_threshold * amount:
            return {
                'success': False,
                'reason': f'Abnormal fees: {outbound_fee_base} > {self.max_fee_threshold * amount}'
            }

        # Monitor transaction
        status = await self._monitor_thorchain_swap(txid)
        if status != 'success':
            return {'success': False, 'reason': f'Status: {status}'}

        # Get actual received amount
        to_chain = to_token_symbol
        actual_received = await get_actual_swap_received(
            txid, self.http_session, self.thor_tx_url, to_chain,
            quote.get('inbound_address'), self.thor_api_url
        )
        if actual_received is None:
            return {'success': False, 'reason': 'Failed to parse actual received'}

        return self._prepare_swap_result(
            txid, amount, actual_received, direction, status
        )

    def _prepare_swap_result(self, txid: str, amount: float, actual_received: float,
                             direction: TradeDirection, status: str) -> Dict[str, Any]:
        """Prepare swap result dictionary for both dry and live modes."""
        # Update state with new trade - ENSURE VALUES ARE FLOATS
        self.state.state_data['last_sent'] = float(amount)
        self.state.state_data['last_received'] = float(actual_received)

        # Calculate metrics
        effective_rate = actual_received / amount if direction == TradeDirection.TOKEN1_TO_TOKEN2 else amount / actual_received
        anchor_rate = self.state.state_data.get('anchor_rate', 0.0)
        spread = (effective_rate / anchor_rate - 1) if anchor_rate > 0 else 0
        asymmetry = self._calculate_asymmetry(effective_rate, anchor_rate, direction)

        # Calculate surplus
        previous_last_sent = float(self.state.state_data.get('last_sent', 0.0))
        previous_last_received = float(self.state.state_data.get('last_received', 0.0))
        if previous_last_sent > 0 and previous_last_received > 0:
            if direction == TradeDirection.TOKEN2_TO_TOKEN1:
                surplus_received = actual_received - previous_last_sent
                surplus_sent = previous_last_received - amount
            else:
                surplus_received = actual_received - previous_last_received
                surplus_sent = previous_last_sent - amount
            surplus_t1 = max(surplus_received if direction == TradeDirection.TOKEN2_TO_TOKEN1 else surplus_sent, 0.0)
            surplus_t2 = max(surplus_sent if direction == TradeDirection.TOKEN2_TO_TOKEN1 else surplus_received, 0.0)
        else:
            surplus_t1 = surplus_t2 = 0.0

        # Log trade
        trade_log = {
            'txid': txid,
            'sent': amount,
            'received': actual_received,
            'effective_rate': effective_rate,
            'spread': spread,
            'asymmetry': asymmetry,
            'surplus_t1': surplus_t1,
            'surplus_t2': surplus_t2
        }
        self.state.log_trade(trade_log)

        return {
            'success': True,
            'txid': txid,
            'effective_rate': effective_rate,
            'actual_received': actual_received,
            'metrics': TradeMetrics(effective_rate, spread, asymmetry, 0.0, 0.0, 0.0),
            'projection': {'surplus_t1': surplus_t1, 'surplus_t2': surplus_t2}
        }

    async def _execute_anchor_trade(self) -> None:
        """Execute initial anchor: Fixed sell TOKEN1 for TOKEN2, set anchor rate."""
        pair, direction = self._get_anchor_trade_params()
        amount = await self._get_anchor_trade_amount(pair, direction)
        if not amount:
            return

        anchor_quote = await self._get_anchor_quote(pair, direction, amount)
        if not anchor_quote:
            return

        self._log_anchor_quote_info(direction, amount, anchor_quote)
        anchor_quote['amount_base'] = amount
        eval_result = {'meets_conditions': True, 'quote': anchor_quote, 'spread': 0.0, 'asymmetry': 0.0,
                       'projection': {'both_positive': True, 'surplus_t1': 0.0, 'surplus_t2': 0.0}}

        fresh_quote = await self._revalidate_quote(anchor_quote)
        if not fresh_quote:
            self.config_manager.general_log.warning("Anchor quote expired during revalidation; pausing.")
            self.state.save({'pause_reason': 'Anchor quote expired'})
            return

        # Unified execution
        swap_result = await self._execute_swap(fresh_quote, direction)

        if swap_result['success']:
            # Common success handling
            self.state.state_data['last_sent'] = float(amount)
            self.state.state_data['last_received'] = float(swap_result['actual_received'])
            self._update_virtual_after_trade(direction, float(amount), float(swap_result['actual_received']))
            self.state.save({'anchor_rate': float(swap_result['effective_rate']), 'last_direction': direction})
            self.config_manager.general_log.info(
                f"{'[DRY RUN] ' if self.dry_mode else ''}Anchor trade executed: "
                f"rate {swap_result['effective_rate']:.8f} T2/T1")
        else:
            self.state.save({'pause_reason': f'Anchor failed: {swap_result.get("reason", "Unknown")}'})
            self.config_manager.general_log.warning(
                f"{'[DRY RUN] ' if self.dry_mode else ''}Anchor initialization failed: "
                f"{swap_result.get('reason', 'Unknown')}")

    def _get_anchor_trade_params(self):
        pair = list(self.config_manager.pairs.values())[0]
        direction = TradeDirection.TOKEN1_TO_TOKEN2
        return pair, direction

    async def _get_anchor_trade_amount(self, pair, direction):
        from_token = pair.t1
        amount = self.anchor_trade_size

        if not self.dry_mode:
            free_bal = from_token.dex.free_balance or 0
            if free_bal is None or free_bal < amount:
                self.config_manager.general_log.warning(
                    f"Insufficient balance for anchor: Need {amount}, Have {free_bal} (None? Update failed)."
                )
                self.state.save({'pause_reason': 'Insufficient anchor balance'})
                return None
        else:
            virtual_bal = self.state.state_data['virtual_balances'].get(self.token1, 0.0)
            min_size = self.min_trade_size.get(self.token1, amount)
            if virtual_bal < min_size:
                self.config_manager.general_log.warning(
                    f"Dry-mode: Virtual {virtual_bal:.8f} < min_trade_size {min_size:.8f} for {self.token1}; simulation will use min_size.")
                amount = min_size
            else:
                self.config_manager.general_log.debug(f"Dry-mode: Virtual sufficient for anchor.")
        return amount

    async def _get_anchor_quote(self, pair, direction, amount):
        anchor_quote = await self._poll_quotes(pair, direction)
        if not anchor_quote:
            self.config_manager.general_log.warning(f"No valid anchor quote for {direction}; pausing.")
            self.state.save({'pause_reason': 'No anchor quote'})
            return None
        return anchor_quote

    def _log_anchor_quote_info(self, direction, amount, anchor_quote):
        self.config_manager.general_log.info(
            f"Anchor initialization: Got quote for {direction.value}: Send {amount:.8f} {self.token1}")
        self.config_manager.general_log.info(
            f"Anchor initialization: Using real quote for {direction}: Send {amount:.8f} {self.token1} -> Expected {float(anchor_quote['expected_amount_out']) / (10 ** anchor_quote['decimals_to']):.8f} {self.token2} at rate {float(anchor_quote['expected_amount_out']) / (amount * 10 ** anchor_quote['decimals_from']):.8f}.")

    async def _update_anchor_and_metrics(self, swap_result: Dict, current_direction: TradeDirection,
                                         is_anchor: bool = False) -> None:
        """Update anchor to new effective rate; accumulate metrics; persist. Uses current trade direction."""
        new_anchor = swap_result['effective_rate']  # Now always T2/T1
        await self.config_manager.controller.balance_manager.update_balances()  # Re-fetch
        pair = list(self.config_manager.pairs.values())[0]
        new_t1 = pair.t1.dex.total_balance or 0  # Use total for accumulation
        new_t2 = pair.t2.dex.total_balance or 0
        starting_total = sum(self.state.state_data['starting_balances'].values())
        current_total = new_t1 + new_t2
        growth_rate = (current_total / starting_total - 1) if starting_total > 0 else 0
        starting_t1 = self._get_starting_balance(self.token1)
        starting_t2 = self._get_starting_balance(self.token2)
        delta_t1 = new_t1 - starting_t1
        delta_t2 = new_t2 - starting_t2
        both_growing = (delta_t1 > 0) and (delta_t2 > 0)  # Simple net growth vs starting
        if not is_anchor and not both_growing:
            self.state.save({'pause_reason': 'No dual accumulation verified post-trade'})
            self.config_manager.general_log.warning("Dual accumulation not verified; pausing.")
            return
        if is_anchor:
            self.config_manager.general_log.info(
                f"Anchor baseline set: rate {new_anchor:.8f} T2/T1 (skipping growth check).")
        metrics = swap_result['metrics']
        # Use projection surpluses if available, else deltas
        projection = {'surplus_t1': swap_result.get('projection', {}).get('surplus_t1', delta_t1),
                      'surplus_t2': swap_result.get('projection', {}).get('surplus_t2', delta_t2)}
        metrics.surplus_t1 = projection['surplus_t1']
        metrics.surplus_t2 = projection['surplus_t2']
        metrics.cumulative_trades += 1
        # Note: Assumes initial success_count=0 in mock; +1 only if spread >= target
        if metrics.spread_captured >= self.target_spread:
            metrics.success_count += 1
        else:
            metrics.success_count += 0  # No increment for missed
        metrics.cumulative_spread_consistency = (
                                                        metrics.success_count / metrics.cumulative_trades) * 100 if metrics.cumulative_trades > 0 else 0.0
        metrics.dual_growth_rate = growth_rate
        # Accumulate surpluses for compounding (from projection)
        self.state.state_data['cumulative_surplus_t1'] += projection['surplus_t1']
        self.state.state_data['cumulative_surplus_t2'] += projection['surplus_t2']
        metrics.net_accumulation_token1 = self.state.state_data['cumulative_surplus_t1']
        metrics.net_accumulation_token2 = self.state.state_data['cumulative_surplus_t2']
        # Asymmetry efficiency: avg tokens gained per cycle
        total_surplus = metrics.surplus_t1 + metrics.surplus_t2
        if metrics.cumulative_trades > 0:
            metrics.volume_asymmetry_efficiency = (getattr(metrics, 'volume_asymmetry_efficiency', 0.0) * (
                    metrics.cumulative_trades - 1) + total_surplus) / metrics.cumulative_trades
        else:
            metrics.volume_asymmetry_efficiency = total_surplus
        # Log MD metrics
        self.config_manager.general_log.info(
            f"Trade Metrics: Effective Rate {metrics.effective_rate:.8f}, Spread {metrics.spread_captured:.2%}, "
            f"Asymmetry Efficiency (tokens gained/cycle) {metrics.volume_asymmetry_efficiency:.4f}, Net Acc T1 {metrics.net_accumulation_token1:.8f}, T2 {metrics.net_accumulation_token2:.8f}, "
            f"Growth Rate {metrics.dual_growth_rate:.2%}, Consistency {metrics.cumulative_spread_consistency:.1f}%, Surplus T1 {metrics.surplus_t1:.8f}, T2 {metrics.surplus_t2:.8f}, Compound Residuals: T1 {self.state.state_data['cumulative_surplus_t1']:.8f}, T2 {self.state.state_data['cumulative_surplus_t2']:.8f}"
        )
        self.state.save({
            'anchor_rate': new_anchor,
            'last_direction': current_direction,  # Save current
            'metrics': asdict(metrics)  # Serialize dataclass properly
        })

    def _get_starting_balance(self, token_symbol: str) -> float:
        """Return the starting balance for a token from the configuration."""
        return self.starting_balances.get(token_symbol, 0.0)

    def _increment_failures(self, reason: str) -> None:
        """Increment failures; pause if max reached (circuit breaker for errors/abnormal markets)."""
        if self.dry_mode:
            self.config_manager.general_log.debug(f"[DRY MODE] Simulated failure ignored: {reason}")
            return
        self.consecutive_failures += 1
        self.config_manager.general_log.warning(f"Consecutive failure {self.consecutive_failures}: {reason}")
        if self.consecutive_failures >= self.max_failure_threshold:
            pause_reason = f"Circuit breaker: {self.consecutive_failures} failures - {reason}"
            self.state.save({'pause_reason': pause_reason})
            self.state.archive(pause_reason)

    def _calculate_asymmetry(self, q_rate: float, anchor_rate: float, direction: TradeDirection) -> float:
        """
        Calculate the percentage spread improvement (asymmetry) of the current quote over the last trade.

        Args:
            q_rate: The effective rate from the current quote (in the terms of the quote direction).
            anchor_rate: The effective rate from the last executed trade (in the terms of that trade's direction).
            direction: The direction of the current quote (TOKEN1_TO_TOKEN2 or TOKEN2_TO_TOKEN1).

        Returns:
            The spread improvement as a decimal (e.g., 0.01 for 1%).
        """
        if anchor_rate == 0:
            # Avoid division by zero; no anchor set -> no improvement.
            return 0.0

        # For TOKEN1_TO_TOKEN2: q_rate is T2 per T1
        # For TOKEN2_TO_TOKEN1: q_rate is T1 per T2
        # Anchor rate is always stored as T2 per T1 from the last trade

        if direction == TradeDirection.TOKEN1_TO_TOKEN2:
            # Current quote rate is T2/T1
            current_rate = q_rate
            anchor_in_same_terms = anchor_rate
        else:  # TOKEN2_TO_TOKEN1
            # Current quote rate is T1/T2, convert to T2/T1 for comparison
            if q_rate == 0:
                return 0.0
            current_rate = 1 / q_rate
            anchor_in_same_terms = anchor_rate

        # Calculate improvement: positive when current rate is better than anchor
        # For buying back T1 (TOKEN2_TO_TOKEN1), we want to spend less T2 per T1 -> higher T1/T2 rate is worse, so invert
        if direction == TradeDirection.TOKEN2_TO_TOKEN1:
            # We are comparing T2/T1 rates: lower is better when buying back T1
            return (anchor_in_same_terms - current_rate) / anchor_in_same_terms
        else:
            # For selling T1 (TOKEN1_TO_TOKEN2), higher T2/T1 is better
            return (current_rate - anchor_in_same_terms) / anchor_in_same_terms
