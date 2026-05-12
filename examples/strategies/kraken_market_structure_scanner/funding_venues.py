"""V6-B: Public-data funding/basis venue adapters."""
import time
import requests
from typing import Optional, Dict, Any, List
from .funding_models import PriceLevel


def _recv_ms() -> int:
    return int(time.time() * 1000)


# ---------- Kraken ----------

def kraken_spot_ticker(pair, standard_symbol, quote):
    """Kraken spot public ticker. Returns PriceLevel or None."""
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
        return PriceLevel("kraken", standard_symbol, quote,
                           bid=bid, ask=ask, last=None, ts_exchange_ms=None, ts_recv_ms=_recv_ms())
    except Exception:
        return None


def kraken_futures_tickers(symbols):
    """Fetch all Kraken perps tickers. Returns dict of futures symbols -> ticker dict."""
    url = "https://futures.kraken.com/derivatives/api/v3/tickers"
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        tickers = {}
        for t in data.get("tickers", []):
            s = t.get("symbol")
            if s:
                tickers[s] = t
        return tickers
    except Exception:
        return {}


def kraken_funding_rates(asset_suffix, limit=1):
    """Fetch recent settled funding rates for Kraken perps.
    endpoint: /derivatives/api/v3/historical-funding-rates
    Returns list of {timestamp, funding_rate} dicts or []."""
    url = "https://futures.kraken.com/derivatives/api/v3/historical-funding-rates"
    # Kraken uses PI_XBTUSD for Bitcoin perpetual
    params = {"symbol": asset_suffix, "count": limit}
    try:
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if "fundingRates" not in data:
            return []
        # Returns newest first per Kraken docs
        rates = []
        for r in data["fundingRates"]:
            rates.append({
                "timestamp_ms": int(r.get("timestamp", "0").replace("Z", "").split("+")[0].split(".")[0].replace("T", " ")),
                "rate": float(r.get("fundingRate", 0)),
                "relative_funding": float(r.get("relativeFunding", 0)),
            })
        return rates
    except Exception:
        return []


def kraken_futures_funding_latest(symbol):
    """Get most recent funding rate for a Kraken perp using tickers endpoint if available,
    or parse from historical rates as fallback."""
    tickers = kraken_futures_tickers([symbol])
    t = tickers.get(symbol, {})
    # Kraken tickers do not include current funding rate directly; need separate endpoint
    rates = kraken_funding_rates(symbol, limit=1)
    if rates:
        return rates[0]["relative_funding"], rates[0].get("timestamp_ms")
    return None, None


# ---------- Binance ----------

def binance_fapi_latest_funding(symbol, limit=3):
    """Fetch recent funding rate from Binance USDⓈ-M Futures.
    Endpoint: /fapi/v1/fundingRate — returns array oldest first."""
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    params = {"symbol": symbol, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None, None
        # Return newest (last element)
        latest = data[-1]
        rate = float(latest["fundingRate"])
        ts_ms = int(latest["fundingTime"])
        return rate, ts_ms
    except Exception:
        return None, None


def binance_perp_ticker(symbol):
    """Get Binance perp bid/ask and mark price from /fapi/v1/ticker/bookTicker."""
    url = "https://fapi.binance.com/fapi/v1/ticker/bookTicker"
    try:
        resp = requests.get(url, params={"symbol": symbol}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        bid = float(data.get("bidPrice", 0)) if data.get("bidPrice") else None
        ask = float(data.get("askPrice", 0)) if data.get("askPrice") else None
        return {"bid": bid, "ask": ask, "ts_recv_ms": _recv_ms()}
    except Exception:
        return None


# ---------- Bybit ----------

def bybit_funding_history(category, symbol, limit=3):
    """Fetch funding rate from Bybit V5.
    Endpoint: /v5/market/funding/history
    category=linear for USDT perpetuals."""
    url = "https://api.bybit.com/v5/market/funding/history"
    params = {"category": category, "symbol": symbol, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            return None, None
        items = data.get("result", {}).get("list", [])
        if not items:
            return None, None
        # Return newest (first element)
        latest = items[0]
        rate = float(latest.get("fundingRate", 0))
        ts_ms = int(latest.get("nextFundingTime", 0))
        # Bybit also provides fundingRateInterval in instruments endpoint
        return rate, ts_ms
    except Exception:
        return None, None


def bybit_perp_ticker(category, symbol):
    url = "https://api.bybit.com/v5/market/tickers"
    params = {"category": category, "symbol": symbol}
    try:
        resp = requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if data.get("retCode") != 0:
            return None
        items = data.get("result", {}).get("list", [])
        if not items:
            return None
        t = items[0]
        bid = float(t.get("bid1Price", 0)) if t.get("bid1Price") else None
        ask = float(t.get("ask1Price", 0)) if t.get("ask1Price") else None
        mark = float(t.get("markPrice", 0)) if t.get("markPrice") else None
        index = float(t.get("indexPrice", 0)) if t.get("indexPrice") else None
        return {"bid": bid, "ask": ask, "mark": mark, "index": index, "ts_recv_ms": _recv_ms()}
    except Exception:
        return None
