import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from definitions.pair import Pair

if TYPE_CHECKING:
    from definitions.main_controller import MainController


class TradingProcessor:
    """Processes trading pairs using target functions asynchronously."""

    def __init__(self, controller: "MainController") -> None:
        """
        Initialize TradingProcessor.

        Args:
            controller: Reference to main controller instance
        """
        self.controller: MainController = controller
        self.pairs_dict: dict[str, Pair] = controller.pairs_dict

    async def process_pairs(
        self, target_function: Callable[[Pair], None | Any]
    ) -> None:
        """
        Processes all trading pairs using the target function.

        Handles both async and sync functions. Processes only enabled pairs.
        In-flight tasks always complete even if shutdown is signaled mid-loop.

        Args:
            target_function: Function to execute for each pair. Can be async or sync.
        """
        pair_tasks: list[tuple[Pair, asyncio.Task[Any]]] = []
        for pair in self.pairs_dict.values():
            if pair.disabled:
                continue
            if self.controller.shutdown_event.is_set():
                break
            if asyncio.iscoroutinefunction(target_function):
                task: asyncio.Task[Any] = target_function(pair)
            else:
                task = self.controller.loop.run_in_executor(None, target_function, pair)
            pair_tasks.append((pair, task))

        if pair_tasks:
            await asyncio.gather(*[task for _, task in pair_tasks])
