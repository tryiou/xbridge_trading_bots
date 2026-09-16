import asyncio
import time
from typing import TYPE_CHECKING, Any

from definitions.constants import UPDATE_BALANCES_DELAY
from definitions.token import Token

if TYPE_CHECKING:
    from definitions.config_manager import ConfigManager


class BalanceManager:
    """Manages token balance updates for the trading system."""

    def __init__(
        self,
        tokens_dict: dict[str, Token],
        config_manager: "ConfigManager",
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """
        Initialize BalanceManager.

        Args:
            tokens_dict: Dictionary of token symbols to Token instances
            config_manager: Reference to application config manager
            loop: Asyncio event loop instance
        """
        self.tokens_dict: dict[str, Token] = tokens_dict
        self.config_manager: ConfigManager = config_manager
        self.timer_main_dx_update_bals: float | None = None
        self.loop: asyncio.AbstractEventLoop = loop

    async def update_balances(self) -> None:
        """
        Update token balances if update interval has elapsed.

        Retrieves token UTXOs and calculates free/total balances.
        Handles errors through the application's error handler.
        """
        strategy_instance = getattr(self.config_manager, "strategy_instance", None)
        if (
            strategy_instance
            and hasattr(strategy_instance, "dry_mode")
            and strategy_instance.dry_mode
        ):
            self.config_manager.general_log.debug(
                "Skipping balance update in dry_mode."
            )
            return

        if self._should_update_bals():
            try:
                xb_tokens: list[
                    str
                ] = await self.config_manager.xbridge_manager.getlocaltokens()
            except Exception as e:
                await self.config_manager.error_handler.handle_async(
                    e, context={"stage": "get_local_tokens"}
                )
                return

            if xb_tokens is None:
                self.config_manager.general_log.warning(
                    "Could not retrieve local tokens from xbridge, skipping balance update."
                )
                return

            futures = []
            for token_data in self.tokens_dict.values():
                futures.append(self._update_token_balance(token_data, xb_tokens))

            if futures:
                try:
                    await asyncio.gather(*futures)
                except Exception as e:
                    await self.config_manager.error_handler.handle_async(
                        e, context={"stage": "gather_balance_updates"}
                    )

            self.timer_main_dx_update_bals = time.time()

    def _should_update_bals(self) -> bool:
        """Determine if balance update interval has elapsed."""
        return (
            self.timer_main_dx_update_bals is None
            or time.time() - self.timer_main_dx_update_bals > UPDATE_BALANCES_DELAY
        )

    async def _update_token_balance(
        self, token_data: Token, xb_tokens: list[str]
    ) -> None:
        """
        Update balance for a single token.

        Args:
            token_data: Token instance to update
            xb_tokens: List of tokens to get balances for
        """
        with self.config_manager.resource_lock:
            try:
                if token_data.symbol not in xb_tokens:
                    token_data.dex.total_balance = None
                    token_data.dex.free_balance = None
                    return

                utxos = await self.config_manager.xbridge_manager.gettokenutxo(
                    token_data.symbol, used=True
                )
                bal, bal_free = self._calculate_balances(utxos)
                token_data.dex.total_balance = bal
                token_data.dex.free_balance = bal_free
            except Exception as e:
                await self.config_manager.error_handler.handle_async(
                    e, context={"token": token_data.symbol, "stage": "update_balance"}
                )

    def _calculate_balances(self, utxos: list[dict[str, Any]]) -> tuple[float, float]:
        """
        Calculate total and free balances from UTXO list.

        Args:
            utxos: List of UTXO dictionaries

        Returns:
            Tuple of (total_balance, free_balance)
        """
        if not isinstance(utxos, list):
            return (0.0, 0.0)

        bal: float = 0.0
        bal_free: float = 0.0

        for utxo in utxos:
            amount = float(utxo.get("amount", 0))
            bal += amount
            # UTXOs without order IDs are free (not locked in orders)
            if not utxo.get("orderid"):
                bal_free += amount

        return (bal, bal_free)
