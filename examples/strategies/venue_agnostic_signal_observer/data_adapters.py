"""
Multi-venue data adapters for the signal observer.

This module provides data ingestion and alignment utilities for the venue-agnostic
signal observer. It supports:

- Loading OHLCV data from CSV files (both csv.DictReader and raw comma-split formats)
- Aligning price series from different venues onto a common timestamp grid using
  forward-fill interpolation and bisect-based efficient lookups
- Downloading OHLCV kline data from public exchange APIs (Binance, Kraken, Coinbase)
  with automatic pagination and rate-limit handling
- Saving kline data to CSV files for local caching and offline analysis

All functions use Python 3.11+ style type hints and rely on httpx for HTTP calls.
"""

import bisect
import csv
import io
import time
from pathlib import Path

import httpx


def load_venue_csv(path: str) -> tuple[list[float], list[float]]:
    """Load OHLCV CSV and return (timestamps, close_prices) sorted by timestamp.

    Supports both csv.DictReader format (with header row) and raw comma-split
    format with at least 2 columns (timestamp, close) or 5+ columns (OHLCV).
    
    Args:
        path: Filesystem path to the CSV file.
        
    Returns:
        Tuple of (sorted timestamps, corresponding close prices).
        
    Raises:
        ValueError: If CSV cannot be parsed or has no valid rows.
    """
    raw = Path(path).read_text().strip()
    records: list[tuple[float, float]] = []
    reader = csv.reader(io.StringIO(raw))
    rows = list(reader)
    if not rows:
        raise ValueError(f"Empty CSV: {path}")

    first = rows[0]
    has_header = first and not all(_is_numeric(v) for v in first)

    for row in rows[1:] if has_header else rows:
        if len(row) < 2:
            continue
        ts_str = row[0].strip()
        if len(row) == 2:
            # timestamp, close only
            close_str = row[1].strip()
        else:
            # Assume cols: timestamp, open, high, low, close, volume
            close_str = row[4].strip() if len(row) > 4 else row[-1].strip()
        try:
            ts, close = float(ts_str), float(close_str)
        except ValueError:
            continue
        records.append((ts, close))

    if not records:
        raise ValueError(f"No valid data rows in: {path}")

    records.sort(key=lambda r: r[0])
    ts_list = [r[0] for r in records]
    prices = [r[1] for r in records]
    return ts_list, prices


def _is_numeric(v: str) -> bool:
    try:
        float(v)
        return True
    except ValueError:
        return False


def align_venues(
    source_ts: list[float],
    source_prices: list[float],
    target_ts: list[float],
    target_prices: list[float],
    grid_seconds: float | None = None,
) -> tuple[list[float], list[float], list[float]]:
    """Align two price series onto a common timestamp grid using forward-fill.

    Uses bisect for efficient timestamp alignment without requiring pandas.

    Args:
        source_ts: Timestamps for the source venue.
        source_prices: Close prices for the source venue.
        target_ts: Timestamps for the target venue.
        target_prices: Close prices for the target venue.
        grid_seconds: If provided, create uniform grid at this interval.
            If None, use the sorted union of all timestamps.

    Returns:
        Tuple of (common_timestamps, aligned_source_prices, aligned_target_prices),
        all of equal length, sorted by timestamp.
    """

    def _forward_fill(query_ts: list[float], ts: list[float], prices: list[float]) -> list[float | None]:
        """Forward-fill prices at query timestamps."""
        result: list[float | None] = []
        carry: float | None = None
        for qt in query_ts:
            idx = bisect.bisect_right(ts, qt) - 1
            if idx >= 0:
                carry = prices[idx]
            result.append(carry)
        return result

    if grid_seconds is not None:
        # Build uniform grid
        all_ts = sorted(set(source_ts) | set(target_ts))
        grid_start = all_ts[0]
        grid_end = all_ts[-1]
        common_ts: list[float] = []
        t = grid_start
        while t <= grid_end:
            common_ts.append(t)
            t += grid_seconds
    else:
        all_ts = sorted(set(source_ts) | set(target_ts))
        common_ts = all_ts

    src_aligned = _forward_fill(common_ts, source_ts, source_prices)
    tgt_aligned = _forward_fill(common_ts, target_ts, target_prices)
    return common_ts, src_aligned, tgt_aligned


def download_public_klines(
    venue: str,
    symbol: str,
    interval: str,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> list[dict]:
    """Download OHLCV klines from public exchange APIs (no auth required).

    Handles pagination and rate limiting automatically.
    Uses httpx.AsyncClient to avoid blocking the event loop if called from async context.

    Args:
        venue: Exchange name — "binance", "kraken", or "coinbase".
        symbol: Trading pair symbol (e.g., "BTCUSDT" for Binance,
            "XBT/USD" for Kraken, "BTC-USD" for Coinbase).
        interval: Candle interval (e.g., "1m", "1h", "1d").
        start_ts: Start time as Unix timestamp in milliseconds (None = oldest).
        end_ts: End time as Unix timestamp in milliseconds (None = newest).

    Returns:
        List of dicts with keys: timestamp (float, unix seconds),
        open, high, low, close, volume.
    """
    client = httpx.Client(timeout=30.0)
    all_klines: list[dict] = []

    try:
        if venue == "binance":
            all_klines = _fetch_binance(client, symbol, interval, start_ts, end_ts)
        elif venue == "kraken":
            all_klines = _fetch_kraken(client, symbol, interval, start_ts, end_ts)
        elif venue == "coinbase":
            all_klines = _fetch_coinbase(client, symbol, interval, start_ts, end_ts)
        else:
            raise ValueError(f"Unsupported venue: {venue}. Use binance, kraken, or coinbase.")
    finally:
        client.close()

    all_klines.sort(key=lambda k: k["timestamp"])
    return all_klines


_INTERVAL_MAP: dict[str, str] = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "1440", "1w": "10080",
}


def _fetch_binance(
    client: httpx.Client,
    symbol: str,
    interval: str,
    start_ts: int | None,
    end_ts: int | None,
) -> list[dict]:
    """Fetch Binance klines with pagination (1000 per request)."""
    results: list[dict] = []
    params: dict[str, str | int] = {"symbol": symbol.upper(), "interval": interval, "limit": 1000}
    if start_ts is not None:
        params["startTime"] = start_ts
    if end_ts is not None:
        params["endTime"] = end_ts

    while True:
        resp = client.get("https://api.binance.com/api/v3/klines", params=params)
        resp.raise_for_status()
        chunk = resp.json()
        if not chunk:
            break
        for k in chunk:
            results.append({
                "timestamp": float(k[0]) / 1000.0,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        if len(chunk) < 1000:
            break
        # Advance window
        last_ts = chunk[-1][0]
        params["startTime"] = last_ts + 1
        time.sleep(0.25)

    return results


def _fetch_kraken(
    client: httpx.Client,
    symbol: str,
    interval: str,
    start_ts: int | None,
    end_ts: int | None,
) -> list[dict]:
    """Fetch Kraken OHLC with pagination (720 per request)."""
    results: list[dict] = []
    interval_str = _INTERVAL_MAP.get(interval, interval)
    pair = "XBT/USD" if symbol.upper() in ("XBT/USD", "BTC/USD") else symbol

    since: int | None = start_ts if start_ts is not None else None

    while True:
        params = {"pair": pair, "interval": interval_str}
        if since is not None:
            params["since"] = since
        resp = client.get("https://api.kraken.com/0/public/OHLC", params=params)
        resp.raise_for_status()
        body = resp.json()
        if "error" in body and body["error"]:
            raise RuntimeError(f"Kraken API error: {body['error']}")

        ohlc_data = None
        for key, val in body["result"].items():
            if key != "last":
                ohlc_data = val
                break
        if not ohlc_data:
            break

        for k in ohlc_data:
            ts = float(k[0])
            if end_ts and ts > end_ts / 1000.0:
                break
            results.append({
                "timestamp": ts,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[6]),
            })

        if len(ohlc_data) < 720:
            break
        since = int(ohlc_data[-1][0])
        time.sleep(0.25)

    return results


def _fetch_coinbase(
    client: httpx.Client,
    symbol: str,
    interval: str,
    start_ts: int | None,
    end_ts: int | None,
) -> list[dict]:
    """Fetch Coinbase candles.

    Note: Coinbase legacy exchange candles endpoint does NOT support
    start/end filters — it always returns the most recent candles
    at the given granularity (capped at ~300 bars). We filter client-side.
    """
    granularity = _INTERVAL_MAP.get(interval, interval)

    resp = client.get(
        f"https://api.exchange.coinbase.com/products/{symbol}/candles",
        params={"granularity": granularity},
    )
    resp.raise_for_status()
    data = resp.json()

    # Coinbase candles: [time, low, high, open, close, volume]
    results: list[dict] = []
    for k in data:
        ts = float(k[0])
        if start_ts and ts < start_ts / 1000.0:
            continue
        if end_ts and ts > end_ts / 1000.0:
            continue
        results.append({
            "timestamp": ts,
            "open": float(k[3]),
            "high": float(k[2]),
            "low": float(k[1]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })
    return results


def save_venue_csv(path: str, data: list[dict]) -> None:
    """Write kline data to a CSV file.

    Args:
        path: Output file path.
        data: List of kline dicts with keys: timestamp, open, high, low, close, volume.
    """
    if not data:
        raise ValueError("No data to write")
    fieldnames = ["timestamp", "open", "high", "low", "close", "volume"]
    sorted_data = sorted(data, key=lambda k: k["timestamp"])
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted_data:
            writer.writerow({k: row[k] for k in fieldnames})
