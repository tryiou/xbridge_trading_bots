def get_price_field_for_exchange(exchange_id: str) -> str:
    """Get the price field name for a given exchange ID.

    Args:
        exchange_id: The exchange identifier (e.g., 'kucoin', 'binance')

    Returns:
        The field name to use for getting the last price from ticker data.
    """
    mapping = {"kucoin": "last", "binance": "lastPrice"}
    return mapping.get(exchange_id, "lastTradeRate")
