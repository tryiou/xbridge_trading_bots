from definitions.errors import ConfigurationError
from strategies.basicseller_strategy import BasicSellerStrategy
from strategies.pingpong_strategy import PingPongStrategy

STRATEGY_MAP = {
    "pingpong": PingPongStrategy,
    "basic_seller": BasicSellerStrategy,
}


def create_strategy(strategy_type: str, config_manager) -> object:
    """Create a strategy instance based on the strategy type.

    Args:
        strategy_type: The type of strategy to create ('pingpong', 'basic_seller', etc.)
        config_manager: The ConfigManager instance to pass to the strategy.

    Returns:
        A strategy instance.

    Raises:
        ConfigurationError: If the strategy type is unknown.
    """
    strategy_class = STRATEGY_MAP.get(strategy_type)
    if not strategy_class:
        raise ConfigurationError(f"Unknown strategy: {strategy_type}")
    return strategy_class(config_manager)
