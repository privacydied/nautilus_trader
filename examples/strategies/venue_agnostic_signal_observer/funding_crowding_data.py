"""
Archive data fetch + cache for Family 2 funding crowding reversal.

Source: Binance Vision archive (data.binance.vision) only.
No authentication required — these are public static archives.

Provides:
- fetch_funding_rate_archive() — per-month BTCUSDT funding rate CSV
- fetch_spot_klines_archive() — per-month BTCUSDT 1h klines
- compute_content_hash() — SHA-256 of cached file
- CacheLayer — local-dir cache with hash manifests

RESTRICTIONS
- No Binance REST API (api.binance.com). Archives only.
- No private keys, no API secrets, no auth.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Archive URL templates (Binance Vision)
# ---------------------------------------------------------------------------

FUNDING_ARCHIVE_URL_TEMPLATE: str = (
    "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
    "{symbol}/{symbol}-fundingRate-{year}-{month:02d}.zip"
)

SPOT_KLINES_1H_URL_TEMPLATE: str = (
    "https://data.binance.vision/data/spot/monthly/klines/"
    "{symbol}/1h/{symbol}-1h-{year}-{month:02d}.zip"
)

# ---------------------------------------------------------------------------
# Parsed row types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FundingRateRow:
    """One funding-rate observation from a Binance Vision archive CSV."""

    timestamp_ns: int  # nanosecond Unix timestamp (converted from archive ms)
    funding_rate: float  # last funding rate (e.g. 0.0001 = 1 bp)
    interval_hours: float  # funding interval in hours from archive metadata


@dataclass(frozen=True)
class SpotKlineRow:
    """One 1-hour spot kline from a Binance Vision archive CSV.

    Binance Vision spot kline timestamps are in **microseconds**.
    They are converted to nanoseconds on parsing.
    """

    open_time_ns: int  # kline open timestamp (nanoseconds)
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    close_time_ns: int  # kline close timestamp (nanoseconds)


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------


def compute_content_hash(data: bytes) -> str:
    """Return hex SHA-256 of *data*."""
    return hashlib.sha256(data).hexdigest()


def compute_file_hash(path: Path) -> str:
    """Return hex SHA-256 of the file at *path*."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

MANIFEST_FILENAME = ".cache_manifest.json"


def _load_manifest(cache_dir: Path) -> dict[str, str]:
    """Load the cache manifest mapping URL -> content hash."""
    m_path = cache_dir / MANIFEST_FILENAME
    if m_path.exists():
        return json.loads(m_path.read_text(encoding="utf-8"))
    return {}


def _save_manifest(cache_dir: Path, manifest: dict[str, str]) -> None:
    """Save the cache manifest."""
    (cache_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def _cache_filename(url: str) -> str:
    """Derive a safe filename from the archive URL."""
    # e.g. BTCUSDT-fundingRate-2025-01.zip
    return url.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# HTTP fetch helpers
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT_SEC = 120.0


def _fetch_bytes(url: str) -> bytes:
    """Download *url* and return raw bytes.

    Raises
    ------
    httpx.HTTPStatusError
        If the server returns a non-2xx status.
    httpx.RequestError
        If the connection fails.
    """
    with httpx.Client(timeout=_HTTP_TIMEOUT_SEC, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Archive fetch + cache
# ---------------------------------------------------------------------------


class ArchiveCache:
    """Cache layer for Binance Vision archive downloads.

    Each downloaded file is cached by URL. The cache records a SHA-256
    content hash so that subsequent runs detect corruption or drift.

    Parameters
    ----------
    cache_dir:
        Local directory for cached archive files.
    """

    def __init__(self, cache_dir: str | Path) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._manifest = _load_manifest(self._cache_dir)

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def get_or_fetch(self, url: str) -> tuple[bytes, str]:
        """Return (data_bytes, sha256_hex) for *url*.

        Fetches from the network if not cached, or if the cached file's
        content hash does not match the manifest record.

        Raises
        ------
        httpx.HTTPStatusError
            On failed download.
        """
        fname = _cache_filename(url)
        cached_path = self._cache_dir / fname
        expected_hash = self._manifest.get(url)

        # If cached and hash matches, reuse
        if cached_path.exists() and expected_hash is not None:
            actual_hash = compute_file_hash(cached_path)
            if actual_hash == expected_hash:
                return cached_path.read_bytes(), actual_hash
            # Hash mismatch — corrupted or stale
            raise RuntimeError(
                f"Cache hash mismatch for {fname}: "
                f"expected {expected_hash}, got {actual_hash}. "
                f"Delete {cached_path} and rerun to re-fetch."
            )

        # Fetch from archive
        data = _fetch_bytes(url)
        content_hash = compute_content_hash(data)

        # Write cache
        cached_path.write_bytes(data)

        # Update manifest
        self._manifest[url] = content_hash
        _save_manifest(self._cache_dir, self._manifest)

        return data, content_hash

    def get_cached_hash(self, url: str) -> str | None:
        """Return the recorded content hash for *url*, or None."""
        return self._manifest.get(url)

    def content_hashes_snapshot(self) -> dict[str, str]:
        """Return a snapshot of all URL -> hash entries."""
        return dict(self._manifest)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def parse_funding_rate_csv(csv_text: str) -> list[FundingRateRow]:
    """Parse Binance Vision funding rate CSV text.

    Binance Vision CSV columns:
        calc_time (ms), funding_interval_hours, last_funding_rate

    The calc_time is in Unix milliseconds; it is converted to nanoseconds.
    The funding_interval_hours column provides metadata about the funding
    cadence, fulfilling the FUNDING_INTERVAL_METADATA_REQUIRED invariant.

    Example:
        1735689600015,8,0.00010000
    """
    rows: list[FundingRateRow] = []
    reader = csv.reader(io.StringIO(csv_text))
    for i, row in enumerate(reader):
        if not row or len(row) < 3:
            continue
        if i == 0 and "calc_time" in row[0]:
            # Skip header row if present
            continue
        try:
            calc_time_ms = int(row[0].strip())
            interval_h = float(row[1].strip())
            fr = float(row[2].strip())
        except (ValueError, IndexError):
            continue
        # Convert ms to ns
        ts_ns = calc_time_ms * 1_000_000
        rows.append(FundingRateRow(timestamp_ns=ts_ns, funding_rate=fr, interval_hours=interval_h))
    return rows


def parse_spot_kline_csv(csv_text: str) -> list[SpotKlineRow]:
    """Parse Binance Vision 1h spot kline CSV text.

    Binance Vision CSV columns (12 columns):
        open_time, open, high, low, close, volume,
        close_time, quote_vol, count, taker_buy_vol,
        taker_buy_quote_vol, ignore

    **Timestamp unit:** Pre-2025 archives use **milliseconds** (13 digits).
    2025+ archives use **microseconds** (16 digits). The parser detects
    the unit from the first row's digit count and scales to nanoseconds
    accordingly.
    """
    rows: list[SpotKlineRow] = []
    reader = csv.reader(io.StringIO(csv_text))
    first_row = True
    unit_detected = False

    for row in reader:
        if not row or len(row) < 7:
            continue
        if first_row and "open_time" in row[0].lower():
            # Skip header if present
            first_row = False
            continue
        first_row = False

        try:
            raw_ot = row[0].strip()
            raw_ct = row[6].strip()
            if not raw_ot or not raw_ct:
                continue
            ot_val = int(raw_ot)
            op = float(row[1].strip())
            hp = float(row[2].strip())
            lp = float(row[3].strip())
            cp = float(row[4].strip())
            vol = float(row[5].strip())
            ct_val = int(raw_ct)
        except (ValueError, IndexError):
            continue

        # Detect unit from first valid timestamp
        if not unit_detected:
            unit_detected = True
            raw_digits = len(raw_ot) if raw_ot.startswith("1") else len(str(int(raw_ot)))
            if raw_digits <= 13:  # milliseconds (pre-2025 format)
                _unit_mult = 1_000_000  # ms → ns
            else:  # microseconds (2025+ format)
                _unit_mult = 1_000  # μs → ns

        ot_ns = ot_val * _unit_mult
        ct_ns = ct_val * _unit_mult

        rows.append(
            SpotKlineRow(
                open_time_ns=ot_ns,
                open_price=op,
                high_price=hp,
                low_price=lp,
                close_price=cp,
                volume=vol,
                close_time_ns=ct_ns,
            )
        )
    return rows


def extract_csv_from_zip(archive_bytes: bytes) -> str:
    """Extract the first CSV from a Binance Vision monthly zip archive."""
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        # Find the CSV file in the zip
        csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_names:
            raise ValueError("No CSV found in archive zip")
        # Use the first CSV (there's typically one per month)
        return zf.read(csv_names[0]).decode("utf-8")


# ---------------------------------------------------------------------------
# High-level archive fetchers
# ---------------------------------------------------------------------------


def fetch_and_parse_funding_month(
    cache: ArchiveCache,
    symbol: str,
    year: int,
    month: int,
) -> tuple[list[FundingRateRow], str]:
    """Fetch and parse one month of funding rate data.

    Returns (rows, content_hash_hex).
    """
    url = FUNDING_ARCHIVE_URL_TEMPLATE.format(symbol=symbol, year=year, month=month)
    data_bytes, content_hash = cache.get_or_fetch(url)
    csv_text = extract_csv_from_zip(data_bytes)
    rows = parse_funding_rate_csv(csv_text)
    return rows, content_hash


def fetch_and_parse_spot_klines_month(
    cache: ArchiveCache,
    symbol: str,
    year: int,
    month: int,
) -> tuple[list[SpotKlineRow], str]:
    """Fetch and parse one month of 1h spot klines.

    Returns (rows, content_hash_hex).
    """
    url = SPOT_KLINES_1H_URL_TEMPLATE.format(symbol=symbol, year=year, month=month)
    data_bytes, content_hash = cache.get_or_fetch(url)
    csv_text = extract_csv_from_zip(data_bytes)
    rows = parse_spot_kline_csv(csv_text)
    return rows, content_hash


# ---------------------------------------------------------------------------
# Multi-month fetch helpers
# ---------------------------------------------------------------------------


def fetch_funding_range(
    cache: ArchiveCache,
    symbol: str = "BTCUSDT",
    start_year: int = 2020,
    start_month: int = 1,
    end_year: int | None = None,
    end_month: int | None = None,
) -> tuple[list[FundingRateRow], dict[str, str]]:
    """Fetch funding rate data for a range of months.

    Returns (all_rows_chronological, content_hashes_by_url).
    """
    if end_year is None:
        now = datetime.now(timezone.utc)
        end_year = now.year
        end_month = now.month

    all_rows: list[FundingRateRow] = []
    hashes: dict[str, str] = {}

    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        try:
            rows, ch = fetch_and_parse_funding_month(cache, symbol, year, month)
            all_rows.extend(rows)
            url = FUNDING_ARCHIVE_URL_TEMPLATE.format(symbol=symbol, year=year, month=month)
            hashes[url] = ch
        except Exception as e:
            # Month may not exist (e.g. future month, or gap in archives)
            # Log and continue
            pass
        month += 1
        if month > 12:
            month = 1
            year += 1

    # Sort chronologically (ts_ns)
    all_rows.sort(key=lambda r: r.timestamp_ns)
    return all_rows, hashes


def fetch_spot_klines_range(
    cache: ArchiveCache,
    symbol: str = "BTCUSDT",
    start_year: int = 2020,
    start_month: int = 1,
    end_year: int | None = None,
    end_month: int | None = None,
) -> tuple[list[SpotKlineRow], dict[str, str]]:
    """Fetch 1h spot klines for a range of months.

    Returns (all_rows_chronological, content_hashes_by_url).
    """
    if end_year is None:
        now = datetime.now(timezone.utc)
        end_year = now.year
        end_month = now.month

    all_rows: list[SpotKlineRow] = []
    hashes: dict[str, str] = {}

    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        try:
            rows, ch = fetch_and_parse_spot_klines_month(cache, symbol, year, month)
            all_rows.extend(rows)
            url = SPOT_KLINES_1H_URL_TEMPLATE.format(symbol=symbol, year=year, month=month)
            hashes[url] = ch
        except Exception:
            pass
        month += 1
        if month > 12:
            month = 1
            year += 1

    all_rows.sort(key=lambda r: r.open_time_ns)
    return all_rows, hashes


# ---------------------------------------------------------------------------
# Coverage stats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FundingCoverageStats:
    """Coverage statistics for fetched funding data."""

    symbol: str
    total_observations: int
    earliest_timestamp_ns: int | None
    latest_timestamp_ns: int | None
    year_months_fetched: list[tuple[int, int]]

    @property
    def earliest_dt(self) -> str | None:
        if self.earliest_timestamp_ns is None:
            return None
        dt = datetime.fromtimestamp(self.earliest_timestamp_ns / 1_000_000_000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

    @property
    def latest_dt(self) -> str | None:
        if self.latest_timestamp_ns is None:
            return None
        dt = datetime.fromtimestamp(self.latest_timestamp_ns / 1_000_000_000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def compute_funding_coverage(
    rows: list[FundingRateRow],
    symbol: str = "BTCUSDT",
) -> FundingCoverageStats:
    """Compute coverage stats from fetched funding rows."""
    if not rows:
        return FundingCoverageStats(
            symbol=symbol,
            total_observations=0,
            earliest_timestamp_ns=None,
            latest_timestamp_ns=None,
            year_months_fetched=[],
        )

    earliest = rows[0].timestamp_ns
    latest = rows[-1].timestamp_ns

    # Extract unique year-month combos
    seen: set[tuple[int, int]] = set()
    for r in rows:
        dt = datetime.fromtimestamp(r.timestamp_ns / 1_000_000_000, tz=timezone.utc)
        seen.add((dt.year, dt.month))

    return FundingCoverageStats(
        symbol=symbol,
        total_observations=len(rows),
        earliest_timestamp_ns=earliest,
        latest_timestamp_ns=latest,
        year_months_fetched=sorted(seen),
    )
