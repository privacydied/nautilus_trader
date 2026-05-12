"""Download public OHLCV data from Binance and Kraken REST APIs.

No authentication required — these are public market data endpoints.

Binance klines: GET /api/v3/klines
  https://binance-docs.github.io/apidocs/spot/en/#klinecandlestick-data

Kraken OHLC:   POST /0/public/OHLC
  https://docs.kraken.com/rest/#tag/Market-Data/operation/getOHLCData
"""
import csv
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import httpx

# ---------------------------------------------------------------------------
# Binance klines (public, no auth)
# ---------------------------------------------------------------------------

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# Binance interval strings mapped to our internal label
_BINANCE_INTERVALS = {
    "1s": "1s",
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
}


def fetch_binance_klines(
    symbol: str,
    interval: str,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
    limit: int = 1000,
) -> List[dict]:
    """Fetch klines from Binance.

    Args:
        symbol: e.g. "BTCUSDT", "ETHUSDT", "SOLUSDT"
        interval: one of "1s", "1m", "5m", "15m", "1h"
        start_time: optional unix ms start (inclusive)
        end_time: optional unix ms end (inclusive)
        limit: max klines per request (Binance max 1000)

    Returns:
        List of dicts with keys: timestamp, open, high, low, close, volume
    """
    bi = _BINANCE_INTERVALS.get(interval)
    if bi is None:
        raise ValueError(f"Unsupported Binance interval: {interval}. Use one of {list(_BINANCE_INTERVALS)}")

    all_rows: List[dict] = []
    current_start = start_time

    with httpx.Client(timeout=30.0) as client:
        while True:
            params: dict = {
                "symbol": symbol.upper(),
                "interval": bi,
                "limit": min(limit, 1000),
            }
            if current_start is not None:
                params["startTime"] = current_start
            if end_time is not None:
                params["endTime"] = end_time

            resp = client.get(BINANCE_KLINES_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            if not data:
                break

            for kline in data:
                all_rows.append({
                    "timestamp": kline[0] / 1000.0,  # convert ms to seconds
                    "open": float(kline[1]),
                    "high": float(kline[2]),
                    "low": float(kline[3]),
                    "close": float(kline[4]),
                    "volume": float(kline[5]),
                })

            # Pagination: next start is last timestamp + 1ms
            last_ts = data[-1][0]
            if len(data) < limit:
                break
            current_start = last_ts + 1
            if end_time is not None and current_start > end_time:
                break

            # Be polite to the API
            time.sleep(0.25)

    return all_rows


# ---------------------------------------------------------------------------
# Kraken OHLC (public, no auth)
# ---------------------------------------------------------------------------

KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"

_KRAKEN_INTERVALS = {
    "1s": 1,
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
}

# Kraken symbol mapping
_KRAKEN_SYMBOLS = {
    "BTCUSD": "XXBTZUSD",
    "ETHUSD": "XETHZUSD",
    "SOLUSD": "SOLUSD",
}


def fetch_kraken_ohlc(
    pair: str,
    interval: str,
    start_time: Optional[int] = None,
) -> List[dict]:
    """Fetch OHLC from Kraken.

    Args:
        pair: e.g. "BTCUSD", "ETHUSD", "SOLUSD"
        interval: one of "1s", "1m", "5m", "15m", "1h"
        start_time: optional unix seconds (Kraken uses since=unix-time)

    Returns:
        List of dicts with keys: timestamp, open, high, low, close, volume
        Note: Kraken returns the most recent 720 candles unless since= is given.
        For 1s/1m bars, 720 is very little history. We paginate via the 'last'
        field in the response.
    """
    ki = _KRAKEN_INTERVALS.get(interval)
    if ki is None:
        raise ValueError(f"Unsupported Kraken interval: {interval}. Use one of {list(_KRAKEN_INTERVALS)}")

    kraken_pair = _KRAKEN_SYMBOLS.get(pair, pair)

    all_rows: List[dict] = []
    last_id: Optional[str] = None
    since = start_time

    with httpx.Client(timeout=30.0) as client:
        for _ in range(50):  # safety limit on pagination loops
            params: dict = {
                "pair": kraken_pair,
                "interval": ki,
            }
            if since is not None:
                params["since"] = since

            resp = client.get(KRAKEN_OHLC_URL, params=params)
            resp.raise_for_status()
            body = resp.json()

            # Kraken nests data under the pair name (which can differ from input)
            data_key = None
            for key in body.get("result", {}):
                if key != "last":
                    data_key = key
                    break

            if data_key is None:
                errors = body.get("error", [])
                if errors:
                    raise ValueError(f"Kraken API error: {errors}")
                break

            candles = body["result"][data_key]
            last_id = body["result"].get("last")

            for candle in candles:
                all_rows.append({
                    "timestamp": float(candle[0]),
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[6]),
                })

            if last_id is None or len(candles) == 0:
                break

            # Continue from where we left off
            since = int(float(last_id))
            time.sleep(0.25)

    return all_rows


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def write_ohlc_csv(rows: List[dict], path: str):
    """Write OHLC data to a CSV file."""
    if not rows:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).touch()
        return

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["timestamp", "open", "high", "low", "close", "volume"]

    # Sort by timestamp
    rows.sort(key=lambda r: r["timestamp"])

    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
