"""V7: Symbol mapping for Kraken public data."""

# Standard symbol → Kraken pair name for WebSocket
SYMBOL_MAP = {
    "BTC/USD": "BTC/USD",       # WS v2 format
    "ETH/USD": "ETH/USD",
}

# Standard symbol → Kraken pair name for REST
REST_PAIR_MAP = {
    "BTC/USD": "XBTUSD",
    "ETH/USD": "ETHUSD",
}

def to_ws_symbol(standard: str) -> str:
    s = SYMBOL_MAP.get(standard)
    if s is None:
        raise ValueError(f"Unknown symbol: {standard}")
    return s

def to_rest_pair(standard: str) -> str:
    p = REST_PAIR_MAP.get(standard)
    if p is None:
        raise ValueError(f"Unknown symbol: {standard}")
    return p
