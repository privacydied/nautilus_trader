"""Binance Vision public archive downloader and parser.

Download-only mode for cross-asset beta-lag archive v0 study.
No authentication. No live endpoints. Public data observer only.

Supports:
- Spot daily aggTrades -> TradeTickLite
- Spot daily 1m klines for stress prefiltering
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple, Dict

from .tick_models import TradeTickLite

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://data.binance.vision"

# Spot daily aggTrades URL template
AGGTRADE_PATH = "data/spot/daily/aggTrades/{symbol}/{symbol}-aggTrades-{date}.zip"

# Spot daily 1m klines URL template
KLINES_1M_PATH = "data/spot/daily/klines/{symbol}/1m/{symbol}-1m-{date}.zip"

# Cache directory relative to repo root
CACHE_ROOT = "data/binance_vision/cross_asset_beta_lag_archive_v0"

# aggTrade CSV columns (Binance Vision standard, no header)
# Columns: agg_trade_id, price, quantity, first_trade_id, last_trade_id, timestamp, is_buyer_maker, is_best_match
AGGTRADE_COLS = [
    "agg_trade_id", "price", "quantity", "first_trade_id",
    "last_trade_id", "timestamp", "is_buyer_maker", "is_best_match",
]

# 1m klines CSV columns (Binance Vision standard, with header)
KLINES_1M_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_asset_volume", "number_of_trades",
    "taker_buy_base_vol", "taker_buy_quote_vol", "ignore",
]

# HTTP request timeout
DOWNLOAD_TIMEOUT = 30

# Max retries for downloads
MAX_RETRIES = 3

# Retry backoff in seconds
RETRY_BACKOFF = 2.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_cache_dir(symbol: str) -> Path:
    """Ensure cache directory exists and return its Path."""
    p = Path(CACHE_ROOT) / symbol.lower()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _iter_date_range(start_date: str, end_date: str) -> List[str]:
    """Yield YYYY-MM-DD strings from start_date to end_date inclusive."""
    dates: List[str] = []
    fmt = "%Y-%m-%d"
    cur = datetime.strptime(start_date, fmt).replace(tzinfo=timezone.utc)
    end = datetime.strptime(end_date, fmt).replace(tzinfo=timezone.utc)
    while cur <= end:
        dates.append(cur.strftime(fmt))
        cur += _timedelta_days(1)
    return dates


def _timedelta_days(n: int):
    """Simple timedelta for Python 3.8 compat."""
    from datetime import timedelta
    return timedelta(days=n)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def _download_with_retry(url: str, timeout: int = DOWNLOAD_TIMEOUT) -> bytes:
    """Download URL with bounded retries and exponential backoff."""
    import time
    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
    raise IOError(f"Download failed after {MAX_RETRIES} retries: {url} -- {last_err}")


def download_daily_agg_trades(
    symbol: str,
    date_str: str,
    *,
    cache: bool = True,
) -> Tuple[Optional[bytes], Optional[str]]:
    """Download one daily aggTrade zip from Binance Vision.

    Args:
        symbol: e.g. "BTCUSDT"
        date_str: "YYYY-MM-DD"
        cache: If True, cache to disk and reuse on repeat calls.

    Returns:
        (zip_bytes, sha256_hex) or (None, None) if file not found.
    """
    path_part = AGGTRADE_PATH.format(symbol=symbol.upper(), date=date_str)
    url = f"{BASE_URL}/{path_part}"
    cache_key = f"{symbol.upper()}_{date_str}_aggTrades.zip"

    if cache:
        cache_dir = _ensure_cache_dir(symbol)
        cache_path = cache_dir / cache_key
        if cache_path.exists():
            data = cache_path.read_bytes()
            return data, _sha256_bytes(data)

    try:
        data = _download_with_retry(url)
    except IOError:
        return None, None

    h = _sha256_bytes(data)

    if cache:
        cache_dir = _ensure_cache_dir(symbol)
        cache_path = cache_dir / cache_key
        cache_path.write_bytes(data)

    return data, h


def download_daily_klines_1m(
    symbol: str,
    date_str: str,
    *,
    cache: bool = True,
) -> Tuple[Optional[bytes], Optional[str]]:
    """Download one daily 1m kline zip from Binance Vision.

    Returns (zip_bytes, sha256) or (None, None).
    """
    path_part = KLINES_1M_PATH.format(symbol=symbol.upper(), date=date_str)
    url = f"{BASE_URL}/{path_part}"
    cache_key = f"{symbol.upper()}_{date_str}_1m_klines.zip"

    cache_path = None
    if cache:
        cache_dir = _ensure_cache_dir(symbol)
        cache_path = cache_dir / cache_key
        if cache_path.exists():
            data = cache_path.read_bytes()
            return data, _sha256_bytes(data)

    try:
        data = _download_with_retry(url)
    except IOError:
        return None, None

    h = _sha256_bytes(data)

    if cache and cache_path is not None:
        cache_path.write_bytes(data)

    return data, h


def check_archive_availability(
    symbol: str,
    date_str: str,
    *,
    source: str = "aggTrades",
    timeout: int = 10,
) -> bool:
    """Quick HEAD/GET check if an archive file exists for given symbol/date.

    Does not download the full file.
    """
    if source == "aggTrades":
        path_part = AGGTRADE_PATH.format(symbol=symbol.upper(), date=date_str)
    else:
        path_part = KLINES_1M_PATH.format(symbol=symbol.upper(), date=date_str)
    url = f"{BASE_URL}/{path_part}"
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _detect_ts_unit(ts_value: int) -> int:
    """Auto-detect timestamp unit: ms (13 digits), us (16 digits), ns (19 digits).

    Returns divisor to convert to seconds.
    """
    s = str(abs(ts_value))
    if len(s) >= 18:
        return 1_000_000_000  # ns
    elif len(s) >= 15:
        return 1_000  # us -> ms (keeping ms for aggTrades)
    elif len(s) >= 12:
        return 1  # ms
    return 1  # default ms


def _ts_to_ns(ts_value: int) -> int:
    """Convert timestamp to nanosecond epoch.

    Binance aggTrade timestamps are in milliseconds.
    Binance 1m kline timestamps may be ms or us depending on year.
    """
    unit = _detect_ts_unit(ts_value)
    if unit == 1:
        # ms -> ns
        return ts_value * 1_000_000
    elif unit == 1_000:
        # us -> ns
        return ts_value * 1_000
    elif unit == 1_000_000_000:
        return ts_value
    return ts_value


def parse_agg_trade_csv(
    zip_bytes: bytes,
    symbol: str,
    venue: str = "binance_spot_archive",
) -> List[TradeTickLite]:
    """Parse aggTrade CSV from a Binance Vision zip (no-header format).

    aggTrade CSVs have no header row. Columns are positional:
      0: agg_trade_id
      1: price
      2: quantity
      3: first_trade_id
      4: last_trade_id
      5: timestamp (ms)
      6: is_buyer_maker
      7: is_best_match
    """
    ticks: List[TradeTickLite] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            return ticks
        with zf.open(names[0]) as f:
            content = f.read().decode("utf-8", errors="replace")

    reader = csv.reader(io.StringIO(content))
    for row in reader:
        if len(row) < 6:
            continue
        try:
            price = float(row[1])
            size = abs(float(row[2]))
            ts_ms = int(row[5])
            is_buyer_maker_raw = row[6].strip().lower() if len(row) > 6 else "false"
        except (ValueError, IndexError):
            continue

        # Reject non-finite, zero, or negative prices/sizes
        if not _isfinite_positive(price):
            continue
        if not _isfinite_positive(size):
            continue

        # Side inference from is_buyer_maker (string "True"/"False" or int 0/1)
        if is_buyer_maker_raw in ("true", "1"):
            side = "sell"
        elif is_buyer_maker_raw in ("false", "0"):
            side = "buy"
        else:
            side = "unknown"

        trade_id = str(row[0]) if len(row) > 0 and row[0] else None

        ts_ns = _ts_to_ns(ts_ms)

        ticks.append(TradeTickLite(
            ts_event=ts_ns,
            venue=venue,
            symbol=symbol.upper(),
            price=price,
            size=size,
            side=side,
            trade_id=trade_id,
            raw={"agg_trade_id": row[0], "is_buyer_maker": row[6] if len(row) > 6 else None},
        ))

    return ticks


def parse_1m_klines_csv(zip_bytes: bytes) -> List[dict[str, Any]]:
    """Parse 1m klines CSV from Binance Vision (has header).

    Returns list of dicts with keys:
      open_time_ns, open, high, low, close, volume
    """
    rows: List[dict[str, Any]] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            return rows
        with zf.open(names[0]) as f:
            content = f.read().decode("utf-8", errors="replace")

    reader = csv.DictReader(io.StringIO(content))
    for r in reader:
        try:
            open_time = int(r.get("open_time", "0") or "0")
            k = {
                "open_time_ns": _ts_to_ns(open_time),
                "open": float(r.get("open", "0") or "0"),
                "high": float(r.get("high", "0") or "0"),
                "low": float(r.get("low", "0") or "0"),
                "close": float(r.get("close", "0") or "0"),
                "volume": float(r.get("volume", "0") or "0"),
            }
            if _isfinite_positive(k["close"]) and _isfinite_positive(k["open"]):
                rows.append(k)
        except (ValueError, TypeError):
            continue

    return rows


def _isfinite_positive(v: float) -> bool:
    """Check value is finite and positive."""
    import math
    return math.isfinite(v) and v > 0


# ---------------------------------------------------------------------------
# Availability scanning
# ---------------------------------------------------------------------------


def scan_archive_availability(
    symbols: List[str],
    start_date: str,
    end_date: str,
    *,
    source: str = "aggTrades",
    max_workers: int = 8,
) -> dict[str, dict[str, Any]]:
    """Scan archive availability for all symbols across date range.

    Uses sparse sampling at boundaries and known Binance Vision conventions
    to estimate availability without checking every single date.
    """
    results: Dict[str, Dict[str, Any]] = {}

    for sym in symbols:
        # Check first, last, and a few sample dates
        dates = _iter_date_range(start_date, end_date)
        first_date = dates[0]
        last_date = dates[-1]

        # Sample middle dates
        mid_idx = len(dates) // 2
        mid_date = dates[mid_idx]

        first_ok = check_archive_availability(sym, first_date, source=source)
        last_ok = check_archive_availability(sym, last_date, source=source)
        mid_ok = check_archive_availability(sym, mid_date, source=source)

        # If first, mid, and last are all available, assume full range available
        # This is a safe assumption for actively traded Binance spot pairs
        if first_ok and last_ok and mid_ok:
            avail = [first_date, mid_date, last_date]
            # Estimate all dates as available
            total_avail = len(dates)
            estimated_missing = 0
        elif first_ok:
            # Estimate forward from first date
            avail = [first_date]
            estimated_missing = len(dates) - 1
        else:
            avail = []
            estimated_missing = len(dates)

        results[sym] = {
            "available_dates": avail,
            "missing_dates": [],  # sparse check
            "total_available": len(avail),
            "total_missing": estimated_missing,
            "first_available": avail[0] if avail else None,
            "last_available": avail[-1] if avail else None,
            "sample_checked": [first_date, mid_date, last_date],
            "sample_results": {"first": first_ok, "mid": mid_ok, "last": last_ok},
        }

    return results


def compute_common_calendar(
    availability: dict[str, dict[str, Any]],
) -> Tuple[Optional[str], Optional[str], int]:
    """Compute the common date range across all symbols.

    Returns (common_start, common_end, common_days) or (None, None, 0) if no
    overlap.
    """
    firsts = []
    lasts = []
    for sym_data in availability.values():
        avail = sym_data.get("available_dates", [])
        if avail:
            firsts.append(avail[0])  # first available
            lasts.append(avail[-1])  # last available

    if not firsts or not lasts:
        return None, None, 0

    # latest start date, earliest end date
    common_start = max(firsts)
    common_end = min(lasts)

    if common_start > common_end:
        return None, None, 0

    common_dates = _iter_date_range(common_start, common_end)
    return common_start, common_end, len(common_dates)


# ---------------------------------------------------------------------------
# File manifest
# ---------------------------------------------------------------------------


def build_file_manifest(
    downloads: List[dict[str, Any]],
) -> List[dict[str, Any]]:
    """Build an archive file manifest from download metadata."""
    return [
        {
            "symbol": d.get("symbol"),
            "date": d.get("date"),
            "source": d.get("source", "aggTrades"),
            "url": d.get("url"),
            "sha256": d.get("sha256"),
            "size_bytes": d.get("size_bytes"),
            "row_count": d.get("row_count"),
            "status": d.get("status", "unknown"),
        }
        for d in downloads
    ]
