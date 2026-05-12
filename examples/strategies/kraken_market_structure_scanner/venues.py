"""V6: Venue adapters — public REST endpoints only. No API keys."""
import time
import requests
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class Ticker:
    venue: str
    symbol: str
    quote: str
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    ts_exchange_ms: Optional[int]
    ts_recv_ms: int

def fetch_kraken(pair, std_symbol, quote):
    url = "https://api.kraken.com/0/public/Ticker"
    try:
        resp = requests.get(url, params={"pair": pair}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            return None
        result = data.get("result", {})
        ticker_raw = None
        for k, v in result.items():
            if k != "last":
                ticker_raw = v
                break
        if ticker_raw is None:
            return None
        bid = float(ticker_raw["b"][0]) if ticker_raw.get("b") and ticker_raw["b"][0] else None
        ask = float(ticker_raw["a"][0]) if ticker_raw.get("a") and ticker_raw["a"][0] else None
        ts_recv = int(time.time() * 1000)
        return Ticker("kraken", std_symbol, quote, bid, ask, None, None, ts_recv)
    except Exception:
        return None

def fetch_binance(symbol, std_symbol, quote):
    """Fetch Binance public bookTicker. No API key needed."""
    url = "https://api.binance.com/api/v3/ticker/bookTicker"
    try:
        resp = requests.get(url, params={"symbol": symbol}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        bid = float(data["bidPrice"]) if data.get("bidPrice") else None
        ask = float(data["askPrice"]) if data.get("askPrice") else None
        ts_recv = int(time.time() * 1000)
        return Ticker("binance", std_symbol, quote, bid, ask, bid, None, ts_recv)
    except Exception:
        return None

FETCHERS = {"kraken": fetch_kraken, "binance": fetch_binance}
