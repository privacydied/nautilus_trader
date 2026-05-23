"""Hyperliquid funding history archive backfill from public REST API.

Fetches historical funding data from Hyperliquid's public /info endpoint
using request type `fundingHistory`. Writes immutable JSONL archive files
with provenance metadata.

Public unauthenticated data only. No API keys. No auth headers.
No wallet. No signing. No orders. No execution.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from urllib.request import Request, urlopen
from urllib.error import HTTPError


ENDPOINT: Final[str] = "https://api.hyperliquid.xyz/info"
ASSETS: Final[tuple[str, str]] = ("BTC", "ETH")
DEFAULT_SLEEP_MS: Final[int] = 500
MAX_ROWS_PER_REQUEST: Final[int] = 1000


@dataclass(frozen=True)
class FundingRow:
    """Normalized funding rate row from Hyperliquid."""
    coin: str
    timestamp_ms: int
    timestamp_iso: str
    funding_rate: float
    hourly_funding_bps: float
    projected_8h_funding_bps: float
    source_endpoint: str


@dataclass
class BackfillResult:
    """Result of a backfill operation."""
    asset: str
    start_date: str
    end_date: str
    rows_fetched: int
    output_path: str
    manifest: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _sha256_text(text: str) -> str:
    """Compute SHA256 hash of text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ms_to_iso(ms: int) -> str:
    """Convert milliseconds timestamp to ISO8601 string."""
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def _parse_date(date_str: str) -> datetime:
    """Parse YYYY-MM-DD date string to datetime at start of day UTC."""
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)


def _date_to_ms(date_str: str) -> int:
    """Convert YYYY-MM-DD to milliseconds timestamp at start of day UTC."""
    dt = _parse_date(date_str)
    return int(dt.timestamp() * 1000)


def fetch_funding_history(
    coin: str,
    start_time_ms: int,
    end_time_ms: int | None = None,
    sleep_ms: int = DEFAULT_SLEEP_MS,
) -> list[dict[str, Any]]:
    """Fetch funding history from Hyperliquid public API.
    
    Args:
        coin: Asset symbol (e.g., "BTC", "ETH")
        start_time_ms: Start timestamp in milliseconds
        end_time_ms: Optional end timestamp in milliseconds
        sleep_ms: Milliseconds to sleep between requests for rate limiting
    
    Returns:
        List of funding history entries from the API response.
    """
    payload: dict[str, Any] = {
        "type": "fundingHistory",
        "coin": coin,
        "startTime": start_time_ms,
    }
    if end_time_ms is not None:
        payload["endTime"] = end_time_ms
    
    req = Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    
    try:
        with urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            # Small sleep for rate limiting
            if sleep_ms > 0:
                time.sleep(sleep_ms / 1000.0)
            return data if isinstance(data, list) else []
    except HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} from {ENDPOINT}: {error_body}") from e


def normalize_funding_entry(
    entry: dict[str, Any],
    coin: str,
    source_endpoint: str = ENDPOINT,
) -> FundingRow:
    """Normalize a raw API response entry to FundingRow.
    
    Args:
        entry: Raw entry from API response
        coin: Asset symbol
        source_endpoint: Source endpoint for provenance
    
    Returns:
        Normalized FundingRow
    """
    ts_ms = entry.get("time", 0)
    rate = float(entry.get("fundingRate", 0))
    
    # Hyperliquid funding is 1-hour intervals
    hourly_bps = rate * 10000.0
    projected_8h = hourly_bps * 8.0
    
    return FundingRow(
        coin=coin.upper(),
        timestamp_ms=ts_ms,
        timestamp_iso=_ms_to_iso(ts_ms),
        funding_rate=rate,
        hourly_funding_bps=hourly_bps,
        projected_8h_funding_bps=projected_8h,
        source_endpoint=source_endpoint,
    )


def backfill_asset(
    coin: str,
    start_date: str,
    end_date: str,
    output_dir: Path,
    sleep_ms: int = DEFAULT_SLEEP_MS,
) -> BackfillResult:
    """Backfill funding history for a single asset.
    
    Paginates through the API by advancing startTime from the last returned
    timestamp. Writes immutable JSONL archive and sidecar manifest.
    
    Args:
        coin: Asset symbol (e.g., "BTC", "ETH")
        start_date: Start date as YYYY-MM-DD
        end_date: End date as YYYY-MM-DD
        output_dir: Directory to write output files
        sleep_ms: Milliseconds to sleep between API requests
    
    Returns:
        BackfillResult with metadata and any error message
    """
    coin = coin.upper()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Output filenames
    jsonl_filename = f"hyperliquid_funding_{coin}_{start_date}_to_{end_date}.jsonl"
    manifest_filename = f"hyperliquid_funding_{coin}_{start_date}_to_{end_date}.manifest.json"
    jsonl_path = output_dir / jsonl_filename
    manifest_path = output_dir / manifest_filename
    
    # Parse dates
    start_ms = _date_to_ms(start_date)
    end_ms = _date_to_ms(end_date) + (24 * 60 * 60 * 1000) - 1  # End of day
    
    # Collect all rows
    all_rows: list[FundingRow] = []
    current_start_ms = start_ms
    total_api_calls = 0
    total_rows_fetched = 0
    
    while current_start_ms <= end_ms:
        rows = fetch_funding_history(
            coin=coin,
            start_time_ms=current_start_ms,
            end_time_ms=end_ms,
            sleep_ms=sleep_ms,
        )
        
        total_api_calls += 1
        
        if not rows:
            # No more data
            break
        
        # Normalize and collect
        for entry in rows:
            try:
                normalized = normalize_funding_entry(entry, coin)
                all_rows.append(normalized)
            except (KeyError, ValueError, TypeError) as e:
                # Skip malformed entries but continue
                continue
        
        # Pagination: advance start time from last returned timestamp
        if len(rows) < MAX_ROWS_PER_REQUEST:
            # Got fewer than max, so we've reached the end
            break
        
        # Advance to next page using the last timestamp
        last_ts = rows[-1].get("time", current_start_ms)
        current_start_ms = last_ts + 1  # Move past last returned entry
        
        # Safety: prevent infinite loops
        if total_api_calls > 1000:
            raise RuntimeError("Exceeded maximum API calls (1000) - possible pagination loop")
    
    # Sort by timestamp
    all_rows.sort(key=lambda r: r.timestamp_ms)
    
    # Write JSONL archive (immutable)
    jsonl_lines = []
    for row in all_rows:
        jsonl_lines.append(json.dumps({
            "coin": row.coin,
            "timestamp_ms": row.timestamp_ms,
            "timestamp_iso": row.timestamp_iso,
            "funding_rate": row.funding_rate,
            "hourly_funding_bps": row.hourly_funding_bps,
            "projected_8h_funding_bps": row.projected_8h_funding_bps,
            "source_endpoint": row.source_endpoint,
        }))
    
    jsonl_content = "\n".join(jsonl_lines) + "\n" if jsonl_lines else ""
    jsonl_path.write_text(jsonl_content, encoding="utf-8")
    
    # Build manifest
    manifest: dict[str, Any] = {
        "asset": coin,
        "start_date": start_date,
        "end_date": end_date,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "rows_fetched": len(all_rows),
        "api_calls": total_api_calls,
        "output_jsonl": str(jsonl_path),
        "output_jsonl_sha256": _sha256_text(jsonl_content) if jsonl_content else "",
        "endpoint": ENDPOINT,
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "provenance": {
            "source": "Hyperliquid public /info endpoint",
            "request_type": "fundingHistory",
            "authentication": "none",
            "pagination_method": "startTime advancement from last returned timestamp",
        },
        "validation": {
            "rows_sorted_by_timestamp": True,
            "immutable_archive": True,
            "jsonl_format": True,
        },
    }
    
    # Check for cadence consistency (should be 1-hour intervals)
    if len(all_rows) >= 2:
        intervals = []
        for i in range(1, len(all_rows)):
            delta_ms = all_rows[i].timestamp_ms - all_rows[i-1].timestamp_ms
            intervals.append(delta_ms / (1000 * 3600))  # Convert to hours
        
        if intervals:
            median_interval = sorted(intervals)[len(intervals) // 2]
            manifest["validation"]["detected_interval_hours"] = median_interval
            manifest["validation"]["interval_consistent_with_1h"] = abs(median_interval - 1.0) < 0.1
    
    # Write manifest
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    
    return BackfillResult(
        asset=coin,
        start_date=start_date,
        end_date=end_date,
        rows_fetched=len(all_rows),
        output_path=str(jsonl_path),
        manifest=manifest,
    )


def backfill_assets(
    assets: list[str],
    start_date: str,
    end_date: str,
    output_dir: Path,
    sleep_ms: int = DEFAULT_SLEEP_MS,
) -> list[BackfillResult]:
    """Backfill funding history for multiple assets.
    
    Args:
        assets: List of asset symbols
        start_date: Start date as YYYY-MM-DD
        end_date: End date as YYYY-MM-DD
        output_dir: Directory to write output files
        sleep_ms: Milliseconds to sleep between API requests
    
    Returns:
        List of BackfillResult for each asset
    """
    results = []
    for asset in assets:
        result = backfill_asset(
            coin=asset,
            start_date=start_date,
            end_date=end_date,
            output_dir=output_dir,
            sleep_ms=sleep_ms,
        )
        results.append(result)
    return results
