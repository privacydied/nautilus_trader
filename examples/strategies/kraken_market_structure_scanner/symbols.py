"""V6: Symbol normalization across venues.

IMPORTANT: USD and USDT are NOT treated as identical.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class SymbolSpec:
    base: str
    quote: str
    kraken: str
    coinbase: str
    binance: str

STANDARD_SYMBOLS = {
    "BTC/USD": SymbolSpec("BTC", "USD", "XBTUSD", "BTC-USD", "BTCUSDT"),
    "BTC/USDT": SymbolSpec("BTC", "USDT", "XBTUSDT", "BTC-USDT", "BTCUSDT"),
    "ETH/USD": SymbolSpec("ETH", "USD", "ETHUSD", "ETH-USD", "ETHUSDT"),
    "ETH/USDT": SymbolSpec("ETH", "USDT", "ETHUSDT", "ETH-USDT", "ETHUSDT"),
    "SOL/USD": SymbolSpec("SOL", "USD", "SOLUSD", "SOL-USD", "SOLUSDT"),
}

def parse_standard(s):
    parts = s.split("/")
    if len(parts) != 2:
        raise ValueError(f"Invalid symbol: {s}")
    return parts[0], parts[1]

def get_spec(standard):
    spec = STANDARD_SYMBOLS.get(standard)
    if spec is None:
        raise KeyError(f"Unknown symbol: {standard}")
    return spec
