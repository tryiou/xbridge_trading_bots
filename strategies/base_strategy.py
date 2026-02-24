import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from definitions.starter import run_async_main


class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.
    Defines the interface for strategy-specific logic with error handling.
    """

    def __init__(self, config_manager, controller=None):
        self.config_manager = config_manager
        self.controller = controller
        self.error_handler = config_manager.error_handler
        self._bot_thread: threading.Thread | None = None
        self.is_running = False
        self._critical_error_callback: Callable | None = None

    @abstractmethod
    def initialize_strategy_specifics(self, **kwargs):
        pass

    @abstractmethod
    def get_tokens_for_initialization(self, **kwargs) -> list:
        pass

    @abstractmethod
    def get_pairs_for_initialization(self, tokens_dict, **kwargs) -> dict:
        pass

    def initialize_tokens_and_pairs(self, **kwargs):
        from definitions.token import Token

        tokens_list = self.get_tokens_for_initialization(**kwargs)

        self.config_manager.tokens = {}
        if "BTC" not in tokens_list:
            self.config_manager.tokens["BTC"] = Token(
                "BTC",
                strategy=self.config_manager.strategy,
                config_manager=self.config_manager,
                dex_enabled=False,
            )
        for token_symbol in list(set(tokens_list)):
            if token_symbol not in self.config_manager.tokens:
                self.config_manager.tokens[token_symbol] = Token(
                    token_symbol,
                    strategy=self.config_manager.strategy,
                    config_manager=self.config_manager,
                    dex_enabled=True,
                )

        self.config_manager.pairs = self.get_pairs_for_initialization(
            self.config_manager.tokens, **kwargs
        )

    def get_dex_history_file_path(self, pair_name: str) -> str:
        unique_id = pair_name.replace("/", "_")
        return f"{self.config_manager.ROOT_DIR}/data/{self.config_manager.strategy}_{unique_id}_last_order.yaml"

    def get_dex_token_address_file_path(self, token_symbol: str) -> str:
        return f"{self.config_manager.ROOT_DIR}/data/{self.config_manager.strategy}_{token_symbol}_addr.yaml"

    def get_tokens_from_pair_configs(
            self, pair_configs: list[dict[str, Any]]
    ) -> list[str]:
        tokens = set()
        for cfg in pair_configs:
            if cfg.get("enabled", True):
                t1, t2 = cfg["pair"].split("/")
                tokens.add(t1)
                tokens.add(t2)
        return list(tokens)

    def _create_pairs_from_configs(
            self,
            configs: list[dict[str, Any]],
            tokens_dict: dict,
            strategy_name: str,
            **extra_kwargs,
    ) -> dict:
        """DRY helper method to generate Pair objects from config arrays."""
        from definitions.pair import Pair

        pairs = {}
        enabled_pairs = [cfg for cfg in configs if cfg.get("enabled", True)]
        for cfg in enabled_pairs:
            t1, t2 = cfg["pair"].split("/")
            pair_name = cfg["name"]

            # Map specific config keys directly if they exist
            amount = cfg.get("amount_to_sell", extra_kwargs.get("amount_token_to_sell"))
            min_price = cfg.get(
                "min_sell_price_usd", extra_kwargs.get("min_sell_price_usd")
            )
            offset = cfg.get("sell_price_offset", extra_kwargs.get("sell_price_offset"))
            partial = cfg.get("partial_percent", extra_kwargs.get("partial_percent"))

            pairs[pair_name] = Pair(
                token1=tokens_dict[t1],
                token2=tokens_dict[t2],
                cfg=cfg,
                strategy=strategy_name,
                dex_enabled=True,
                amount_token_to_sell=amount,
                min_sell_price_usd=min_price,
                sell_price_offset=offset,
                partial_percent=partial,
                config_manager=self.config_manager,
            )
        return pairs

    @abstractmethod
    def should_update_cex_prices(self) -> bool:
        pass

    @abstractmethod
    async def thread_init_async_action(self, pair_instance):
        pass

    @abstractmethod
    async def process_pair_async(self, pair_instance):
        pass

    async def safe_thread_loop(self, pair_instance):
        try:
            await self.process_pair_async(pair_instance)
        except Exception as e:
            context = {"pair": pair_instance.symbol, "component": "strategy_loop"}
            await self.config_manager.error_handler.handle_async(e, context=context)

    async def safe_order_creation(self, pair_instance, create_func):
        try:
            await create_func()
        except Exception as e:
            context = {"pair": pair_instance.symbol, "stage": "order_creation"}
            await self.config_manager.error_handler.handle_async(e, context=context)

    @abstractmethod
    def get_operation_interval(self) -> int:
        pass

    @abstractmethod
    def get_startup_tasks(self) -> list:
        pass

    def register_critical_error_callback(self, callback: Callable):
        self._critical_error_callback = callback

    def _thread_wrapper(self, func, *args):
        try:
            func(*args)
        except Exception as e:
            if self._critical_error_callback:
                self._critical_error_callback(e)
            else:
                self.config_manager.general_log.critical(
                    "Unhandled exception in bot thread: %s", e, exc_info=True
                )

    def start(self):
        if self.is_running:
            self.config_manager.general_log.warning(
                "Attempted to start an already running strategy."
            )
            return

        startup_tasks = self.get_startup_tasks()
        self._bot_thread = threading.Thread(
            target=self._thread_wrapper,
            args=(run_async_main, self.config_manager, startup_tasks),
            daemon=True,
            name=f"BotThread-{self.config_manager.strategy}",
        )
        self.config_manager.general_log.info(
            "Starting %s bot thread.", self.config_manager.strategy.capitalize()
        )
        self._bot_thread.start()
        self.is_running = True

    def stop(self, timeout: float = 45.0):
        if not self.is_running:
            self.config_manager.general_log.warning(
                "Attempted to stop a non-running strategy."
            )
            return

        self.config_manager.general_log.info(
            "Attempting to stop %s bot...", self.config_manager.strategy
        )

        if self.config_manager.controller and self.config_manager.controller.loop:
            loop = self.config_manager.controller.loop
            if not loop.is_closed() and loop.is_running():
                with self.config_manager.resource_lock:
                    loop.call_soon_threadsafe(
                        self.config_manager.controller.shutdown_event.set
                    )
        else:
            self.config_manager.general_log.warning(
                "No active controller/loop to signal for shutdown."
            )

        if self._bot_thread:
            self._bot_thread.join(timeout=timeout)

            if self._bot_thread.is_alive():
                self.config_manager.general_log.warning(
                    "Bot thread for %s did not terminate gracefully within %ss.",
                    self.config_manager.strategy,
                    timeout,
                )
            else:
                self.config_manager.general_log.info(
                    "%s bot stopped successfully.",
                    self.config_manager.strategy.capitalize(),
                )

        self.is_running = False
        self._bot_thread = None
