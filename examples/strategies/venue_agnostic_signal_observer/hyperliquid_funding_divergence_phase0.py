"""Hyperliquid funding divergence Phase 0 distribution-only audit.

Public/archive data only. Observer-only. No auth. No orders. No execution.
This module computes same-asset funding divergence distributions only; it does
not inspect forward returns, PnL, post-event price paths, nulls, FDR, holdouts,
or any dependent variable.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any, Final

KILL_CRITERION_MAX_ABS_BPS: Final[float] = 10.0
KILL_CRITERION_P99_ABS_BPS: Final[float] = 8.0
MIN_ALIGNED_ROWS_PER_ASSET: Final[int] = 100

STATUS_READY = "PHASE0_DISTRIBUTION_READY"
STATUS_SINGLE_ASSET = "PHASE0_DISTRIBUTION_READY_SINGLE_ASSET"
STATUS_KILLED = "PHASE0_KILLED_NO_FUNDING_DIVERGENCE_TAIL"
STATUS_NEEDS_MORE_DATA = "PHASE0_NEEDS_MORE_DATA"
STATUS_ALIGNMENT_FAILED = "PHASE0_ALIGNMENT_FAILED"
STATUS_SOURCE_UNAVAILABLE = "PHASE0_SOURCE_UNAVAILABLE"
STATUS_DATA_UNUSABLE = "PHASE0_DATA_UNUSABLE"
ALLOWED_STATUSES: Final[frozenset[str]] = frozenset({
    STATUS_READY,
    STATUS_SINGLE_ASSET,
    STATUS_KILLED,
    STATUS_NEEDS_MORE_DATA,
    STATUS_ALIGNMENT_FAILED,
    STATUS_SOURCE_UNAVAILABLE,
    STATUS_DATA_UNUSABLE,
})
SAFETY_MODE: Final[str] = "public_data_observer_only"
INSUFFICIENT_SAMPLES_FOR_PERCENTILE: Final[str] = "INSUFFICIENT_SAMPLES_FOR_PERCENTILE"
INSUFFICIENT_SAMPLES: Final[str] = "INSUFFICIENT_SAMPLES"

EXPECTED_INTERVAL_HOURS: Final[dict[str, float]] = {"hyperliquid": 1.0, "binance": 8.0, "bybit": 8.0}
ASSETS: Final[tuple[str, str]] = ("BTC", "ETH")
BUCKETS_BPS_HOURLY: Final[tuple[float, ...]] = (2.0, 5.0, 8.0, 10.0, 20.0, 40.0)

DIVERGENCE_DISTRIBUTION_COLUMNS: Final[list[str]] = [
    "asset", "reference_basis", "count", "p50_absolute_divergence_bps_hourly",
    "p75_absolute_divergence_bps_hourly", "p90_absolute_divergence_bps_hourly",
    "p95_absolute_divergence_bps_hourly", "p99_absolute_divergence_bps_hourly",
    "max_absolute_divergence_bps_hourly", "mean_signed_divergence_bps_hourly",
    "median_signed_divergence_bps_hourly", "count_hl_funding_above_reference",
    "count_hl_funding_below_reference", "p99_positive_side_divergence_bps_hourly",
    "p99_negative_side_absolute_divergence_bps_hourly",
]
BUCKET_COUNT_COLUMNS: Final[list[str]] = ["asset", "reference_basis", "threshold_bps_hourly", "count"]
PERSISTENCE_COLUMNS: Final[list[str]] = [
    "asset", "reference_basis", "threshold_bps_hourly", "n_observations", "n_censored",
    "median_half_life_hours", "p75_half_life_hours", "p95_half_life_hours", "percentile_status",
]
CALENDAR_COLUMNS: Final[list[str]] = [
    "asset", "reference_basis", "calendar_bucket_type", "calendar_bucket", "count",
    "p50_absolute_divergence_bps_hourly", "p95_absolute_divergence_bps_hourly", "percentile_status",
]
ALIGNMENT_COLUMNS: Final[list[str]] = [
    "asset", "reference_venue", "hyperliquid_rows", "aligned_rows", "missing_reference_rows",
    "p50_alignment_lag_seconds", "p95_alignment_lag_seconds", "max_alignment_lag_seconds", "alignment_status",
]


@dataclass(frozen=True)
class NormalizedFundingRow:
    venue: str
    asset: str
    native_symbol: str
    timestamp_utc: datetime
    native_funding_rate: float
    native_interval_hours: float
    hourly_funding_bps: float
    projected_8h_funding_bps: float
    source_file_or_endpoint: str
    parse_status: str


@dataclass(frozen=True)
class AlignedDivergenceRow:
    asset: str
    reference_basis: str
    hyperliquid_timestamp_utc: datetime
    reference_timestamp_utc: datetime
    alignment_lag_seconds: float
    hyperliquid_funding_bps_hourly: float
    reference_funding_bps_hourly: float
    divergence_bps_hourly: float
    absolute_divergence_bps_hourly: float
    hyperliquid_funding_bps_projected_8h: float
    reference_funding_bps_projected_8h: float


def pre_data_kill_criteria() -> dict[str, Any]:
    return {
        "recorded_before_data_load": True,
        "max_abs_threshold_bps_hourly": KILL_CRITERION_MAX_ABS_BPS,
        "p99_abs_threshold_bps_hourly": KILL_CRITERION_P99_ABS_BPS,
        "constant_names": ["KILL_CRITERION_MAX_ABS_BPS", "KILL_CRITERION_P99_ABS_BPS"],
    }


def normalize_funding_row(*, venue: str, asset: str, native_symbol: str, timestamp_utc: datetime,
                          native_funding_rate: float, native_interval_hours: float,
                          source_file_or_endpoint: str) -> NormalizedFundingRow:
    hourly = native_funding_rate * 10000.0 / native_interval_hours
    return NormalizedFundingRow(
        venue=venue.lower(), asset=asset.upper(), native_symbol=native_symbol,
        timestamp_utc=timestamp_utc, native_funding_rate=native_funding_rate,
        native_interval_hours=float(native_interval_hours), hourly_funding_bps=hourly,
        projected_8h_funding_bps=hourly * 8.0, source_file_or_endpoint=source_file_or_endpoint,
        parse_status="ok",
    )


def _parse_timestamp(value: str) -> tuple[datetime, str]:
    v = str(value).strip()
    if any(c in v for c in "TZ:-"):
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(UTC), "iso8601"
    n = int(float(v))
    digits = len(str(abs(n)))
    if digits <= 10:
        return datetime.fromtimestamp(n, tz=UTC), "s"
    if digits <= 13:
        return datetime.fromtimestamp(n / 1000, tz=UTC), "ms"
    return datetime.fromtimestamp(n / 1_000_000, tz=UTC), "us"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _detect_interval_hours(times: list[datetime]) -> float | None:
    if len(times) < 2:
        return None
    diffs = sorted((b - a).total_seconds() / 3600 for a, b in zip(times, times[1:]) if b > a)
    return median(diffs) if diffs else None


def load_funding_csv(path: Path, venue: str, asset: str) -> tuple[list[NormalizedFundingRow], dict[str, Any], dict[str, Any] | None]:
    rows: list[NormalizedFundingRow] = []
    units: set[str] = set()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"empty funding file: {path}")
        for raw in reader:
            ts_raw = raw.get("timestamp") or raw.get("timestamp_utc") or raw.get("time") or raw.get("fundingRateTimestamp") or raw.get("calc_time")
            rate_raw = raw.get("funding_rate") or raw.get("native_funding_rate") or raw.get("fundingRate") or raw.get("last_funding_rate")
            sym = raw.get("symbol") or raw.get("native_symbol") or raw.get("coin") or f"{asset}-PERP"
            interval_raw = raw.get("native_interval_hours") or raw.get("funding_interval_hours") or ""
            if ts_raw is None or rate_raw is None:
                continue
            ts, unit = _parse_timestamp(ts_raw)
            units.add(unit)
            interval = float(interval_raw) if interval_raw not in (None, "") else EXPECTED_INTERVAL_HOURS[venue]
            rows.append(normalize_funding_row(
                venue=venue, asset=asset, native_symbol=str(sym), timestamp_utc=ts,
                native_funding_rate=float(rate_raw), native_interval_hours=interval,
                source_file_or_endpoint=str(path),
            ))
    rows.sort(key=lambda r: r.timestamp_utc)
    detected = _detect_interval_hours([r.timestamp_utc for r in rows])
    expected = EXPECTED_INTERVAL_HOURS[venue]
    provenance = {
        "source_name": f"{venue}_{asset}", "venue": venue, "asset": asset,
        "native_symbol": rows[0].native_symbol if rows else f"{asset}-PERP",
        "source_path_or_endpoint": str(path),
        "source_first_row_timestamp_utc": rows[0].timestamp_utc.isoformat().replace("+00:00", "Z") if rows else "",
        "source_last_row_timestamp_utc": rows[-1].timestamp_utc.isoformat().replace("+00:00", "Z") if rows else "",
        "source_row_count": len(rows), "source_sha256": _sha256(path),
        "source_native_funding_interval_hours": detected,
        "source_timestamp_unit_detected": sorted(units)[0] if len(units) == 1 else ("iso8601" if not units else "ambiguous"),
        "timestamp_unit_detection_method": "iso8601 marker or epoch digit length",
        "interval_detection_method": "median adjacent timestamp delta hours",
    }
    if detected is None or abs(detected - expected) > 1e-6:
        return rows, provenance, {
            "interval_ambiguity_detected": True, "unusable_reason": "native funding interval does not match expected venue cadence",
            "offending_source": str(path), "expected_interval_hours": expected, "detected_interval_hours": detected,
        }
    return rows, provenance, None


def align_last_observed_reference(hyperliquid_rows: list[NormalizedFundingRow], reference_rows: list[NormalizedFundingRow], reference_basis: str) -> tuple[list[AlignedDivergenceRow], int]:
    refs = sorted(reference_rows, key=lambda r: r.timestamp_utc)
    ref_ts = [r.timestamp_utc for r in refs]
    aligned: list[AlignedDivergenceRow] = []
    missing = 0
    for hl in sorted(hyperliquid_rows, key=lambda r: r.timestamp_utc):
        idx = bisect.bisect_right(ref_ts, hl.timestamp_utc) - 1
        if idx < 0:
            missing += 1
            continue
        ref = refs[idx]
        div = hl.hourly_funding_bps - ref.hourly_funding_bps
        aligned.append(AlignedDivergenceRow(
            asset=hl.asset, reference_basis=reference_basis,
            hyperliquid_timestamp_utc=hl.timestamp_utc, reference_timestamp_utc=ref.timestamp_utc,
            alignment_lag_seconds=(hl.timestamp_utc - ref.timestamp_utc).total_seconds(),
            hyperliquid_funding_bps_hourly=hl.hourly_funding_bps,
            reference_funding_bps_hourly=ref.hourly_funding_bps,
            divergence_bps_hourly=div, absolute_divergence_bps_hourly=abs(div),
            hyperliquid_funding_bps_projected_8h=hl.projected_8h_funding_bps,
            reference_funding_bps_projected_8h=ref.projected_8h_funding_bps,
        ))
    return aligned, missing


def align_median_reference(hyperliquid_rows: list[NormalizedFundingRow], reference_sets: dict[str, list[NormalizedFundingRow]]) -> list[AlignedDivergenceRow]:
    per_ref = {name: align_last_observed_reference(hyperliquid_rows, rows, name)[0] for name, rows in reference_sets.items()}
    by_time: dict[datetime, list[AlignedDivergenceRow]] = defaultdict(list)
    for rows in per_ref.values():
        for r in rows:
            by_time[r.hyperliquid_timestamp_utc].append(r)
    out: list[AlignedDivergenceRow] = []
    for hl in hyperliquid_rows:
        matches = by_time.get(hl.timestamp_utc, [])
        if len(matches) < 2:
            continue
        ref_hourly = median([m.reference_funding_bps_hourly for m in matches])
        ref_proj = median([m.reference_funding_bps_projected_8h for m in matches])
        ref_ts = max(m.reference_timestamp_utc for m in matches)
        div = hl.hourly_funding_bps - ref_hourly
        out.append(AlignedDivergenceRow(hl.asset, "median_reference", hl.timestamp_utc, ref_ts,
            (hl.timestamp_utc - ref_ts).total_seconds(), hl.hourly_funding_bps, ref_hourly, div, abs(div), hl.projected_8h_funding_bps, ref_proj))
    return out


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    k = (len(vals) - 1) * p / 100.0
    lo = math.floor(k); hi = math.ceil(k)
    if lo == hi:
        return vals[int(k)]
    return vals[lo] * (hi - k) + vals[hi] * (k - lo)


def distribution_summary(rows: list[AlignedDivergenceRow]) -> dict[str, Any]:
    abs_vals = [r.absolute_divergence_bps_hourly for r in rows]
    signed = [r.divergence_bps_hourly for r in rows]
    pos = [v for v in signed if v > 0]
    neg_abs = [abs(v) for v in signed if v < 0]
    return {
        "count": len(rows),
        "p50_absolute_divergence_bps_hourly": percentile(abs_vals, 50),
        "p75_absolute_divergence_bps_hourly": percentile(abs_vals, 75),
        "p90_absolute_divergence_bps_hourly": percentile(abs_vals, 90),
        "p95_absolute_divergence_bps_hourly": percentile(abs_vals, 95),
        "p99_absolute_divergence_bps_hourly": percentile(abs_vals, 99),
        "max_absolute_divergence_bps_hourly": max(abs_vals) if abs_vals else None,
        "mean_signed_divergence_bps_hourly": sum(signed) / len(signed) if signed else None,
        "median_signed_divergence_bps_hourly": median(signed) if signed else None,
        "count_hl_funding_above_reference": len(pos),
        "count_hl_funding_below_reference": len(neg_abs),
        "p99_positive_side_divergence_bps_hourly": percentile(pos, 99),
        "p99_negative_side_absolute_divergence_bps_hourly": percentile(neg_abs, 99),
    }


def decide_phase0_status(
    asset_metrics: dict[str, dict[str, float]],
    max_abs_threshold_bps: float = KILL_CRITERION_MAX_ABS_BPS,
    p99_abs_threshold_bps: float = KILL_CRITERION_P99_ABS_BPS,
) -> str:
    if any(asset_metrics.get(a, {}).get("count", 0) < MIN_ALIGNED_ROWS_PER_ASSET for a in ASSETS):
        return STATUS_NEEDS_MORE_DATA
    clears = {}
    for asset in ASSETS:
        m = asset_metrics[asset]
        clears[asset] = (m["max"] >= max_abs_threshold_bps) and (m["p99"] >= p99_abs_threshold_bps)
    if not clears["BTC"] and not clears["ETH"]:
        return STATUS_KILLED
    if clears["BTC"] != clears["ETH"]:
        return STATUS_SINGLE_ASSET
    return STATUS_READY


def compute_bucket_counts(rows: list[AlignedDivergenceRow]) -> list[dict[str, Any]]:
    out = []
    groups: dict[tuple[str, str], list[AlignedDivergenceRow]] = defaultdict(list)
    for r in rows: groups[(r.asset, r.reference_basis)].append(r)
    for (asset, basis), rs in groups.items():
        for b in BUCKETS_BPS_HOURLY:
            out.append({"asset": asset, "reference_basis": basis, "threshold_bps_hourly": b, "count": sum(r.absolute_divergence_bps_hourly >= b for r in rs)})
    return out


def compute_persistence_half_life(rows: list[AlignedDivergenceRow]) -> list[dict[str, Any]]:
    out = []
    groups: dict[tuple[str, str], list[AlignedDivergenceRow]] = defaultdict(list)
    for r in rows: groups[(r.asset, r.reference_basis)].append(r)
    for (asset, basis), rs0 in groups.items():
        rs = sorted(rs0, key=lambda r: r.hyperliquid_timestamp_utc)
        for b in BUCKETS_BPS_HOURLY:
            vals = []; censored = 0
            for i, r in enumerate(rs):
                if r.absolute_divergence_bps_hourly < b: continue
                target = r.absolute_divergence_bps_hourly / 2.0
                found = None
                for later in rs[i+1:]:
                    if later.absolute_divergence_bps_hourly < target:
                        found = (later.hyperliquid_timestamp_utc - r.hyperliquid_timestamp_utc).total_seconds() / 3600
                        break
                if found is None: censored += 1
                else: vals.append(found)
            n = len(vals) + censored
            status = "OK" if n >= 30 else INSUFFICIENT_SAMPLES_FOR_PERCENTILE
            out.append({
                "asset": asset, "reference_basis": basis, "threshold_bps_hourly": b,
                "n_observations": n, "n_censored": censored,
                "median_half_life_hours": percentile(vals, 50) if vals else None,
                "p75_half_life_hours": percentile(vals, 75) if n >= 30 and vals else INSUFFICIENT_SAMPLES_FOR_PERCENTILE,
                "p95_half_life_hours": percentile(vals, 95) if n >= 30 and vals else INSUFFICIENT_SAMPLES_FOR_PERCENTILE,
                "percentile_status": status,
            })
    return out


def compute_calendar_stratification(rows: list[AlignedDivergenceRow]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
    for r in rows:
        t = r.hyperliquid_timestamp_utc
        for typ, bucket in [("utc_hour_of_day", str(t.hour)), ("utc_day_of_week", str(t.weekday())), ("weekday_vs_weekend", "weekend" if t.weekday() >= 5 else "weekday")]:
            buckets[(r.asset, r.reference_basis, typ, bucket)].append(r.absolute_divergence_bps_hourly)
    out = []
    for (asset, basis, typ, bucket), vals in buckets.items():
        ok = len(vals) >= 30
        out.append({"asset": asset, "reference_basis": basis, "calendar_bucket_type": typ, "calendar_bucket": bucket, "count": len(vals),
                    "p50_absolute_divergence_bps_hourly": percentile(vals, 50) if ok else INSUFFICIENT_SAMPLES,
                    "p95_absolute_divergence_bps_hourly": percentile(vals, 95) if ok else INSUFFICIENT_SAMPLES,
                    "percentile_status": "OK" if ok else INSUFFICIENT_SAMPLES})
    return out


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def atomic_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
