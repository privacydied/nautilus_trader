"""Archive ingestion helpers for Hyperliquid Supertrend Phase 0.

Converts existing public/local Hyperliquid archives into the canonical CSV
contract consumed by hyperliquid_supertrend_4h1d_phase0.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from . import hyperliquid_supertrend_4h1d_phase0 as phase0

PUBLIC_INFO_ENDPOINT: Final[str] = "https://api.hyperliquid.xyz/info"
SAFETY_MODE: Final[str] = "public_archive_phase0a_unlock_only"


@dataclass(frozen=True)
class IngestResult:
    price_csv: Path
    funding_csv: Path
    manifest_path: Path
    manifest: dict[str, Any]


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_ts(value: Any) -> datetime:
    return phase0.parse_timestamp(value)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_asset_ctxs_minute_prices(source_dir: Path, symbols: tuple[str, ...] = phase0.FROZEN_UNIVERSE) -> dict[str, list[dict[str, Any]]]:
    """Load price rows from per-symbol asset context JSONL, keeping only price/timestamp fields."""
    loaded: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
    for symbol in symbols:
        path = source_dir / f"{symbol}.jsonl"
        if not path.exists():
            continue
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                raw = json.loads(line)
                ts_raw = raw.get("ts_event") or raw.get("timestamp_utc") or raw.get("timestamp") or raw.get("time")
                price_raw = raw.get("price") or raw.get("mark_px") or raw.get("markPx") or raw.get("mark_price")
                if ts_raw is None or price_raw is None:
                    continue
                ts = _parse_ts(ts_raw)
                rows.append({
                    "timestamp_utc": ts,
                    "symbol": symbol,
                    "price": float(price_raw),
                    "price_source": str(raw.get("price_source") or "mark"),
                })
        loaded[symbol] = sorted(rows, key=lambda r: r["timestamp_utc"])
    return loaded


def minute_prices_to_hourly_ohlc(minute_by_symbol: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Resample timestamped snapshots to strict hourly OHLC rows."""
    out: list[dict[str, Any]] = []
    for symbol, rows in minute_by_symbol.items():
        buckets: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            ts = row["timestamp_utc"].astimezone(UTC)
            hour = ts.replace(minute=0, second=0, microsecond=0)
            buckets[hour].append(row)
        for hour in sorted(buckets):
            bucket = sorted(buckets[hour], key=lambda r: r["timestamp_utc"])
            # Require intrahour evidence; one collapsed row per day/hour is not enough.
            if len(bucket) < 2:
                continue
            prices = [float(r["price"]) for r in bucket]
            out.append({
                "timestamp_utc": _iso(hour),
                "symbol": symbol,
                "open": prices[0],
                "high": max(prices),
                "low": min(prices),
                "close": prices[-1],
                "price_source": bucket[0]["price_source"],
            })
    return out


def fetch_hyperliquid_funding_page(symbol: str, start_ms: int, end_ms: int, timeout_s: int = 30) -> list[dict[str, Any]]:
    payload = {"type": "fundingHistory", "coin": symbol, "startTime": start_ms, "endTime": end_ms}
    req = Request(
        PUBLIC_INFO_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(5):
        try:
            with urlopen(req, timeout=timeout_s) as response:
                data = json.loads(response.read().decode("utf-8"))
            time.sleep(0.25)
            return data if isinstance(data, list) else []
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429 and attempt < 4:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {e.code} from Hyperliquid fundingHistory for {symbol}: {body}") from e
    return []


def fetch_hyperliquid_funding_history(symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Bounded public fundingHistory fetch with timestamp-advancing pagination."""
    end_ms = int(end.astimezone(UTC).timestamp() * 1000)
    current_ms = int(start.astimezone(UTC).timestamp() * 1000)
    rows: list[dict[str, Any]] = []
    api_calls = 0
    while current_ms <= end_ms:
        page = fetch_hyperliquid_funding_page(symbol, current_ms, end_ms)
        api_calls += 1
        if not page:
            break
        rows.extend(page)
        max_ts = max(int(r.get("time", current_ms)) for r in page)
        if max_ts >= end_ms:
            break
        next_ms = max_ts + 1
        if next_ms <= current_ms:
            raise RuntimeError(f"Funding pagination did not advance for {symbol}")
        current_ms = next_ms
        if api_calls > 1000:
            raise RuntimeError(f"Funding pagination exceeded safety call cap for {symbol}")
    normalized = []
    seen: set[int] = set()
    for row in rows:
        ts_ms = int(row["time"])
        if ts_ms in seen:
            continue
        seen.add(ts_ms)
        normalized.append({
            "timestamp_utc": _iso(datetime.fromtimestamp(ts_ms / 1000, tz=UTC)),
            "symbol": symbol,
            "funding_rate": float(row["fundingRate"]),
            "funding_source": "hyperliquid_public_info_fundingHistory",
        })
    return sorted(normalized, key=lambda r: r["timestamp_utc"])


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def _intraday_summary(hourly_rows: list[dict[str, Any]], symbols: tuple[str, ...]) -> dict[str, Any]:
    by_symbol_day: dict[str, dict[str, set[str]]] = {s: defaultdict(set) for s in symbols}
    for row in hourly_rows:
        ts = _parse_ts(row["timestamp_utc"])
        by_symbol_day[row["symbol"]][ts.date().isoformat()].add(ts.time().isoformat())
    out: dict[str, Any] = {}
    for symbol in symbols:
        counts = [len(v) for v in by_symbol_day[symbol].values()]
        out[symbol] = {
            "max_distinct_intraday_timestamps_per_day": max(counts) if counts else 0,
            "days_with_multiple_intraday_timestamps": sum(1 for c in counts if c > 1),
        }
    return out


def convert_asset_ctxs_and_fetch_funding(
    *,
    asset_ctxs_dir: Path,
    output_dir: Path,
    symbols: tuple[str, ...] = phase0.FROZEN_UNIVERSE,
) -> IngestResult:
    """Convert local asset_ctxs mark snapshots and bounded public funding to canonical CSVs."""
    minute_by_symbol = load_asset_ctxs_minute_prices(asset_ctxs_dir, symbols)
    hourly_rows = minute_prices_to_hourly_ohlc(minute_by_symbol)
    if not hourly_rows:
        raise RuntimeError("No hourly price rows produced from asset_ctxs source")

    price_ts = [_parse_ts(r["timestamp_utc"]) for r in hourly_rows]
    funding_start = min(price_ts)
    funding_end = max(price_ts)
    funding_rows: list[dict[str, Any]] = []
    funding_api_calls: dict[str, int] = {}
    for symbol in symbols:
        fetched = fetch_hyperliquid_funding_history(symbol, funding_start, funding_end)
        funding_rows.extend(fetched)
        funding_api_calls[symbol] = len(fetched)

    price_csv = output_dir / "hourly_prices.csv"
    funding_csv = output_dir / "funding_history.csv"
    _write_csv(price_csv, hourly_rows, ["timestamp_utc", "symbol", "open", "high", "low", "close", "price_source"])
    _write_csv(funding_csv, funding_rows, ["timestamp_utc", "symbol", "funding_rate", "funding_source"])

    manifest = {
        "generated_at_utc": _iso(datetime.now(UTC)),
        "safety_mode": SAFETY_MODE,
        "study_id": phase0.STUDY_ID,
        "asset_ctxs_source_dir": str(asset_ctxs_dir),
        "price_csv": str(price_csv),
        "funding_csv": str(funding_csv),
        "symbols": list(symbols),
        "network_used": True,
        "network_scope": "bounded Hyperliquid public /info fundingHistory only",
        "price_source": "asset_ctxs mark price resampled minute snapshots to hourly OHLC",
        "funding_source": "hyperliquid public /info fundingHistory",
        "price_rows": len(hourly_rows),
        "funding_rows": len(funding_rows),
        "price_sha256": _sha256_file(price_csv),
        "funding_sha256": _sha256_file(funding_csv),
        "first_price_ts": _iso(min(price_ts)),
        "last_price_ts": _iso(max(price_ts)),
        "intraday_timestamp_preservation": _intraday_summary(hourly_rows, symbols),
        "funding_rows_by_symbol": funding_api_calls,
        "non_price_archive_fields_ignored": True,
        "funding_used_as_signal": False,
    }
    manifest_path = output_dir / "coverage_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return IngestResult(price_csv=price_csv, funding_csv=funding_csv, manifest_path=manifest_path, manifest=manifest)
