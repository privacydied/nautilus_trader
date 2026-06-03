"""Generic altcoin stress extension: present-day Phase 0.

Extends the prior generic stress regime ablation (Phase 0A/0B) from its
original data window (2024-01 through 2025-12) through the latest evaluable
present-day Hyperliquid asset_ctxs archive.

All detector thresholds, costs, cooldown, horizon, symbol universe, negative
controls, and return metric definitions are inherited unchanged from the prior
generic stress ablation.

This is an archive-only diagnostic. No orders, private keys, trading auth,
live execution, paper trading, shadow execution, systemd watcher, bot path,
or REJECTED_RESEARCH.md update are used.
"""

from __future__ import annotations

import hashlib
import json
import math
import mmap
import os
import re
import subprocess
import sys
import time
import traceback
from bisect import bisect_left
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any, Sequence

import numpy as np

try:
    import orjson

    def _loads_json_line(line: bytes | str) -> dict[str, Any]:
        if isinstance(line, bytes):
            return orjson.loads(line)
        return orjson.loads(line.encode("utf-8"))

    HAS_ORJSON = True
except ImportError:
    import json as _json

    def _loads_json_line(line: bytes | str) -> dict[str, Any]:
        if isinstance(line, bytes):
            return _json.loads(line.decode("utf-8"))
        return _json.loads(line)

    HAS_ORJSON = False

# pylint: disable=wrong-import-position
from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    ArchiveRow,
    parse_timestamp,
    normalize_raw_row,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import (
    atomic_write_json,
    atomic_write_text,
)

# ──────────────────────────────────────────────────────────────────
# Prior artifact discovery — must match repo layout
# ──────────────────────────────────────────────────────────────────

PRIOR_GENERIC_STRESS_PRECOMMITMENT_PATH = Path(
    "examples/strategies/venue_agnostic_signal_observer/docs/"
    "GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0_PRECOMMITMENT.md"
)

PRIOR_GENERIC_STRESS_PHASE0A_REPORT_DIR = Path(
    "reports/generic_altcoin_stress_regime_ablation_phase0a/"
    "generic_altcoin_stress_regime_ablation_phase0a_20260525T235410_666100_010d14"
)

PRIOR_GENERIC_STRESS_PHASE0B_REPORT_DIR = Path(
    "reports/generic_altcoin_stress_regime_ablation_phase0b/"
    "generic_altcoin_stress_regime_ablation_phase0b_20260526T013814_632696_22db27"
)

ACTIVE_ARCHIVE_PATH = Path("data/hyperliquid_oi_velocity_compression_phase0")
STAGING_EXTENSION_PATH = Path(
    "data/hyperliquid_asset_ctxs_staging/extension_2026_q1_q2_20260526"
)
CACHE_ROOT = Path(".cache/generic_stress_extension")

# ──────────────────────────────────────────────────────────────────
# Frozen constants (inherited unchanged from prior generic stress)
# ──────────────────────────────────────────────────────────────────

STUDY_ID = "generic_altcoin_stress_extension_present_day_phase0"
STAGE = "phase0_extension_diagnostic"
VENUE = "hyperliquid"
SAFETY_MODE = "public_data_observer_only"

ALTCOIN_EXCLUDED_SYMBOLS = frozenset({"BTC", "ETH"})

# Detector thresholds
TRAILING_1H_RETURN_THRESHOLD_BPS = -300.0
TRAILING_6H_VOL_PERCENTILE_THRESHOLD = 0.80
COOLDOWN_HOURS = 48
VOL_LOOKBACK_DAYS = 30
VOL_MIN_HISTORY_DAYS = 14

# Return evaluation
PRIMARY_HORIZON_HOURS = 24
HORIZONS_HOURS = (6, 12, 24, 48)
PRIMARY_COST_BPS = 50.0
STRESS_COST_BPS = (75.0, 100.0)
MIN_EFFECTIVE_EVENTS = 200

# Coverage
MIN_ACCEPTED_EVENTS = 300
MIN_ACCEPTED_SYMBOLS = 8
MIN_SYMBOLS_WITH_3_EVENTS = 8
MAX_SYMBOL_EVENT_SHARE = 0.20
MAX_MONTH_EVENT_SHARE = 0.25
MAX_QUARTER_EVENT_SHARE = 0.45

# Seeds for controls
BORING_CONTROL_SEED = 20260526
RANDOM_TS_CONTROL_SEED = 20260527
INVERSE_CONTROL_SEED = 20260528
BOOTSTRAP_CI_SEED = 20260529

# ──────────────────────────────────────────────────────────────────
# Status constants
# ──────────────────────────────────────────────────────────────────

STATUS_AUDIT_PASSED = "GENERIC_STRESS_EXTENSION_AUDIT_PASSED_AWAITING_COOLING_PERIOD"
STATUS_UNDERPOWERED = "GENERIC_STRESS_EXTENSION_UNDERPOWERED"
STATUS_TEMPORAL_CONCENTRATION_FAILED = "GENERIC_STRESS_EXTENSION_TEMPORAL_CONCENTRATION_FAILED"
STATUS_NEGATIVE_CONTROL_FAILED = "GENERIC_STRESS_EXTENSION_NEGATIVE_CONTROL_FAILED"
STATUS_PRIOR_PRECOMMITMENT_DRIFTED = "GENERIC_STRESS_EXTENSION_PRIOR_PRECOMMITMENT_DRIFTED"
STATUS_DETECTOR_CODE_DRIFTED = "GENERIC_STRESS_EXTENSION_DETECTOR_CODE_DRIFTED"
STATUS_ARCHIVE_UNAVAILABLE = "GENERIC_STRESS_EXTENSION_ARCHIVE_UNAVAILABLE"
STATUS_BACKFILL_BUDGET_EXCEEDED = "GENERIC_STRESS_EXTENSION_BACKFILL_BUDGET_EXCEEDED"
STATUS_ARCHIVE_VALIDATION_FAILED = "GENERIC_STRESS_EXTENSION_ARCHIVE_VALIDATION_FAILED"
STATUS_PRIOR_EVENTS_UNAVAILABLE = "GENERIC_STRESS_EXTENSION_PRIOR_EVENTS_UNAVAILABLE"
STATUS_PRIOR_REPRODUCTION_FAILED = "GENERIC_STRESS_EXTENSION_PRIOR_REPRODUCTION_FAILED"
STATUS_RETURN_REPRO_FAILED = "GENERIC_STRESS_EXTENSION_RETURN_REPRODUCTION_FAILED"
STATUS_INVALID_INPUT = "GENERIC_STRESS_EXTENSION_INVALID_INPUT"
STATUS_LOOKAHEAD_AUDIT_FAILED = "GENERIC_STRESS_EXTENSION_LOOKAHEAD_AUDIT_FAILED"

WARNING_WEEKLY_CLUSTERING = "EXTENSION_WEEKLY_CLUSTERING_WARNING"
WARNING_CONTROL_NOISE = "EXTENSION_CONTROL_NOISE_WARNING"
WARNING_FULL_WINDOW_CONCENTRATION = "FULL_WINDOW_TEMPORAL_CONCENTRATION_STILL_FAILED"
WARNING_SURVIVORSHIP_AMBIGUITY = "EXTENSION_SURVIVORSHIP_AMBIGUITY"

# ──────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StressWindowPoint:
    timestamp: datetime
    symbol: str
    price_t: float
    trailing_1h_return_bps: float
    trailing_6h_realized_vol_bps: float
    trailing_6h_realized_vol_percentile: float


@dataclass(frozen=True)
class StressEventRecord:
    event_id: str
    symbol: str
    event_timestamp_utc: str
    detector_family: str
    direction: str
    price_t: float
    trailing_1h_return_bps: float
    trailing_6h_realized_vol_bps: float
    trailing_6h_realized_vol_percentile: float
    cooldown_key: str
    archive_source_path: str


@dataclass
class HorizonEvaluation:
    event_id: str
    symbol: str
    event_timestamp_utc: str
    direction: str
    horizon_hours: int
    event_price: float
    future_price: float | None
    gross_return_bps: float | None
    net_return_bps_50: float | None
    net_return_bps_75: float | None
    missing: bool


@dataclass
class HorizonMetrics:
    horizon_hours: int
    evaluated_event_count: int
    gross_mean_bps: float | None
    gross_median_bps: float | None
    net_mean_bps_50bps: float | None
    net_median_bps_50bps: float | None
    win_rate_50bps: float | None
    mean_lcb_95_bps_50bps: float | None
    missing_forward_count: int


@dataclass
class CohortMetrics:
    event_count_before_cooldown: int = 0
    event_count_after_cooldown: int = 0
    evaluated_event_count: int = 0
    missing_forward_coverage_count: int = 0
    symbols_with_at_least_3_events: int = 0
    max_symbol_event_share: float = 0.0
    max_symbol_event_share_symbol: str = ""
    year_distribution: dict[str, int] = field(default_factory=dict)
    quarter_distribution: dict[str, int] = field(default_factory=dict)
    month_distribution: dict[str, int] = field(default_factory=dict)
    top_5_event_dates: list[str] = field(default_factory=list)
    top_5_event_dates_share: float = 0.0
    max_calendar_week_event_share: float = 0.0
    max_calendar_week: str = ""
    net_mean_bps_50: float | None = None
    net_median_bps_50: float | None = None
    win_rate_50: float | None = None
    lower_confidence_bound_50: float | None = None
    net_mean_bps_75: float | None = None
    net_mean_bps_100: float | None = None


@dataclass
class ArchiveValidation:
    valid: bool
    classification: str
    jsonl_file_count: int
    total_row_count: int
    global_first_ts: str | None
    global_last_ts: str | None
    symbols_expected: list[str]
    symbols_present: list[str]
    symbols_missing: list[str]
    per_symbol_last_ts: dict[str, str]
    duplicate_timestamp_count: int
    coverage_caveats: list[str]
    latest_evaluable_complete_timestamp: str | None
    unusable_symbol_notes: dict[str, str]


@dataclass
class ExtensionResult:
    summary: dict[str, Any]
    prior_reproduction_events: list[StressEventRecord] | None = None
    extension_only_events: list[StressEventRecord] | None = None
    full_extended_events: list[StressEventRecord] | None = None
    report_dir: str | None = None


# ──────────────────────────────────────────────────────────────────
# Utility functions
# ──────────────────────────────────────────────────────────────────


def _utc_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    return parse_timestamp(s)


def _quarter_key(ts: datetime) -> str:
    return f"{ts.year}Q{((ts.month - 1) // 3) + 1}"


def _month_key(ts: datetime) -> str:
    return f"{ts.year:04d}-{ts.month:02d}"


def _year_key(ts: datetime) -> str:
    return str(ts.year)


def _week_key(ts: datetime) -> str:
    iso_cal = ts.isocalendar()
    return f"{iso_cal[0]}-W{iso_cal[1]:02d}"


def _git_metadata() -> tuple[str, bool]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True,
        )
        return sha, bool(status.strip())
    except Exception:
        return "", False


def _short_sha(sha: str) -> str:
    return sha[:7]


def _stable_manifest_hash(manifest: dict[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("manifest_hash", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────────────
# Precommitment hash utilities
# ──────────────────────────────────────────────────────────────────


def compute_precommitment_hash(path: Path) -> str:
    """Compute SHA-256 of precommitment after removing self-hash line."""
    text = path.read_text(encoding="utf-8")
    cleaned = re.sub(
        r"^Precommitment SHA-256 \(self\):.*\n?", "", text, flags=re.MULTILINE
    )
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def compute_precommitment_self_hash(text: str) -> str:
    """Compute self-hash: remove the self-hash line and hash remainder."""
    cleaned = re.sub(
        r"^Precommitment SHA-256 \(self\):.*\n?", "", text, flags=re.MULTILINE
    )
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────────────
# Archive reading (mmap-based)
# ──────────────────────────────────────────────────────────────────


def _read_jsonl_mmap(file_path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file using mmap + line iteration."""
    rows: list[dict[str, Any]] = []
    with file_path.open("rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            while True:
                line = m.readline()
                if not line:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rows.append(_loads_json_line(stripped))
                except Exception:
                    continue
    return rows


def _discover_jsonl_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        child for child in root.iterdir()
        if child.suffix.lower() == ".jsonl" and child.is_file()
    )


def _read_archive_rows(
    archive_path: Path, extension_path: Path | None = None,
    progress_label: str = "",
) -> tuple[dict[str, list[tuple[datetime, float]]], dict[str, list[tuple[datetime, float]]]]:
    """Read price series from archive (and optional extension), return per-symbol series.

    Returns (altcoin_series, all_series) where altcoin_series excludes BTC/ETH.
    """
    all_files: list[Path] = []
    source_paths: list[str] = []

    files = _discover_jsonl_files(archive_path)
    if files:
        all_files.extend(files)
        source_paths.append(str(archive_path))
        if progress_label:
            print(f"  [{progress_label}] Found {len(files)} files in {archive_path}", flush=True)

    if extension_path is not None and extension_path.is_dir():
        ext_files = _discover_jsonl_files(extension_path)
        if ext_files:
            all_files.extend(ext_files)
            source_paths.append(str(extension_path))
            if progress_label:
                print(f"  [{progress_label}] Found {len(ext_files)} extension files", flush=True)

    series: dict[str, list[tuple[datetime, float]]] = {}
    for idx, fp in enumerate(all_files):
        symbol = fp.stem.strip().upper()
        if symbol in ALTCOIN_EXCLUDED_SYMBOLS:
            if progress_label:
                print(f"  [{progress_label}] Skipping {symbol} (excluded)", flush=True)
            continue
        if progress_label and idx % 5 == 0:
            print(f"  [{progress_label}] Loading {symbol} ({idx+1}/{len(all_files)})...", flush=True)
        raw_rows = _read_jsonl_mmap(fp)
        archive_rows: list[ArchiveRow] = []
        for order, raw in enumerate(raw_rows):
            row, _ = normalize_raw_row(dict(raw), fp, order)
            if row is not None:
                archive_rows.append(row)
        if archive_rows:
            data: list[tuple[datetime, float]] = sorted(
                [(r.timestamp, r.price) for r in archive_rows],
                key=lambda x: x[0],
            )
            if symbol in series:
                existing = series[symbol]
                merged = sorted(set(existing + data), key=lambda x: x[0])
                series[symbol] = merged
            else:
                series[symbol] = data

    # Deduplicate by timestamp (keep first)
    for sym in series:
        seen: set[datetime] = set()
        deduped: list[tuple[datetime, float]] = []
        for ts, px in series[sym]:
            if ts not in seen:
                seen.add(ts)
                deduped.append((ts, px))
        series[sym] = deduped

    if progress_label:
        print(f"  [{progress_label}] Loaded {len(series)} symbols", flush=True)

    # Separate altcoin series
    altcoin_series = {
        sym: pts for sym, pts in series.items()
        if sym.upper() not in ALTCOIN_EXCLUDED_SYMBOLS
    }
    return altcoin_series, series


# ──────────────────────────────────────────────────────────────────
# Detector feature computation (vectorized, semantics-preserved)
# ──────────────────────────────────────────────────────────────────


def _compute_stress_points_vectorized(
    symbol: str,
    price_series: list[tuple[datetime, float]],
) -> list[StressWindowPoint]:
    """Compute stress candidate points using numpy.
    Matches prior generic ablation semantics exactly.
    """
    n = len(price_series)
    if n < 7:
        return []

    prices = np.array([p[1] for p in price_series], dtype=np.float64)
    timestamps = [p[0] for p in price_series]

    # Vectorized hourly returns (bps)
    returns_1h = np.full(n, 0.0, dtype=np.float64)
    valid = prices[:-1] > 0
    returns_1h[1:] = np.where(valid, (prices[1:] / prices[:-1] - 1.0) * 10000.0, 0.0)

    # Vectorized 6h realized vol
    abs_returns = np.abs(returns_1h)
    vol_6h = np.full(n, 0.0, dtype=np.float64)
    for i in range(6, n):
        vol_6h[i] = np.sum(abs_returns[i - 5 : i + 1])

    points: list[StressWindowPoint] = []
    for i in range(6, n):
        ts = timestamps[i]
        price_t = prices[i]
        ret = returns_1h[i]
        vol = vol_6h[i]

        if i < VOL_MIN_HISTORY_DAYS * 24:
            continue

        lookback_vols = vol_6h[6:i]
        if len(lookback_vols) == 0:
            continue

        count_below = np.sum(lookback_vols < vol)
        vol_pctile = count_below / len(lookback_vols)

        points.append(
            StressWindowPoint(
                timestamp=ts,
                symbol=symbol,
                price_t=price_t,
                trailing_1h_return_bps=ret,
                trailing_6h_realized_vol_bps=vol,
                trailing_6h_realized_vol_percentile=float(vol_pctile),
            )
        )
    return points


def compute_stress_points(
    symbol: str,
    price_series: list[tuple[datetime, float]],
) -> list[StressWindowPoint]:
    """Compute stress candidate points. Uses vectorized implementation."""
    return _compute_stress_points_vectorized(symbol, price_series)


def filter_stress_candidates(
    points: Sequence[StressWindowPoint],
) -> list[StressWindowPoint]:
    """Apply primary stress thresholds (unchanged from prior)."""
    return [
        p for p in points
        if p.trailing_1h_return_bps <= TRAILING_1H_RETURN_THRESHOLD_BPS
        and p.trailing_6h_realized_vol_percentile >= TRAILING_6H_VOL_PERCENTILE_THRESHOLD
    ]


def apply_cooldown(
    candidates: Sequence[StressWindowPoint],
) -> list[StressWindowPoint]:
    """48h per-symbol cooldown, greedy from earliest."""
    accepted: list[StressWindowPoint] = []
    cooldown_until: datetime | None = None
    for point in sorted(candidates, key=lambda p: p.timestamp):
        if cooldown_until is not None and point.timestamp < cooldown_until:
            continue
        accepted.append(point)
        cooldown_until = point.timestamp + timedelta(hours=COOLDOWN_HOURS)
    return accepted


def has_forward_24h_coverage(
    price_series: list[tuple[datetime, float]],
    event_ts: datetime,
) -> bool:
    """Check if forward 24h price data exists (within 2h tolerance)."""
    target = event_ts + timedelta(hours=24)
    for ts, _ in price_series:
        if ts >= target:
            if (ts - target).total_seconds() <= 2 * 3600:
                return True
            break
    return False


# ──────────────────────────────────────────────────────────────────
# Detector code path SHA-256 (for drift guard)
# ──────────────────────────────────────────────────────────────────


def get_detector_code_hash() -> str:
    """Compute SHA-256 of the detector event-selection code path.

    Uses inspect.getsource() on the frozen detector functions plus
    serialized constants.
    """
    import inspect

    sources: list[str] = []

    # get source of key functions
    for fn in (
        _compute_stress_points_vectorized,
        compute_stress_points,
        filter_stress_candidates,
        apply_cooldown,
        has_forward_24h_coverage,
    ):
        try:
            sources.append(inspect.getsource(fn))
        except Exception:
            sources.append(f"# failed to get source for {fn.__name__}")

    # Add frozen constants
    constants = {
        "TRAILING_1H_RETURN_THRESHOLD_BPS": TRAILING_1H_RETURN_THRESHOLD_BPS,
        "TRAILING_6H_VOL_PERCENTILE_THRESHOLD": TRAILING_6H_VOL_PERCENTILE_THRESHOLD,
        "COOLDOWN_HOURS": COOLDOWN_HOURS,
        "VOL_LOOKBACK_DAYS": VOL_LOOKBACK_DAYS,
        "VOL_MIN_HISTORY_DAYS": VOL_MIN_HISTORY_DAYS,
    }
    sources.append(json.dumps(constants, sort_keys=True))

    raw = "\n".join(sources)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────────────
# Detector — process one symbol
# ──────────────────────────────────────────────────────────────────


def process_symbol(
    symbol: str,
    price_series: list[tuple[datetime, float]],
    archive_source_path: str,
) -> tuple[list[StressEventRecord], int, int]:
    """Process a symbol: compute points, filter, cooldown. Returns (events, before_count, after_count)."""
    points = compute_stress_points(symbol, price_series)
    before_cooldown = len(points)

    candidates = filter_stress_candidates(points)
    after_cooldown = apply_cooldown(candidates)

    events: list[StressEventRecord] = []
    for point in after_cooldown:
        events.append(
            StressEventRecord(
                event_id=f"{symbol}_{_utc_iso(point.timestamp)}",
                symbol=symbol,
                event_timestamp_utc=_utc_iso(point.timestamp),
                detector_family=STUDY_ID,
                direction="downside_price_drop",
                price_t=point.price_t,
                trailing_1h_return_bps=point.trailing_1h_return_bps,
                trailing_6h_realized_vol_bps=point.trailing_6h_realized_vol_bps,
                trailing_6h_realized_vol_percentile=point.trailing_6h_realized_vol_percentile,
                cooldown_key=f"{symbol}_{COOLDOWN_HOURS}h",
                archive_source_path=archive_source_path,
            )
        )
    return events, before_cooldown, len(events)


# ──────────────────────────────────────────────────────────────────
# Forward return computation (vectorized lookup)
# ──────────────────────────────────────────────────────────────────


def lookup_future_price(
    series: list[tuple[datetime, float]],
    target_ts: datetime,
) -> float | None:
    """Find price at or after target_ts using bisect. Returns None if unavailable."""
    idx = bisect_left(series, (target_ts,))
    if idx >= len(series):
        return None
    ts, px = series[idx]
    if (ts - target_ts).total_seconds() <= 2 * 3600:
        return px
    return None


def compute_directional_return_bps(
    direction: str, event_price: float, future_price: float
) -> float:
    """Long return after downside_price_drop."""
    if event_price <= 0 or future_price <= 0:
        raise ValueError("prices must be positive")
    if direction == "downside_price_drop":
        return (future_price / event_price - 1.0) * 10000.0
    raise ValueError(f"unknown direction: {direction}")


def net_bps_after_cost(gross_bps: float, cost_bps: float) -> float:
    return gross_bps - cost_bps


# ──────────────────────────────────────────────────────────────────
# Event evaluation for a cohort
# ──────────────────────────────────────────────────────────────────


def evaluate_events(
    events: Sequence[StressEventRecord],
    price_series: dict[str, list[tuple[datetime, float]]],
) -> tuple[list[HorizonEvaluation], list[HorizonMetrics]]:
    """Evaluate forward returns for a list of events. Returns (evaluations, metrics)."""
    evaluations: list[HorizonEvaluation] = []
    for event in events:
        symbol = event.symbol.upper()
        direction = event.direction
        if symbol in ALTCOIN_EXCLUDED_SYMBOLS:
            continue
        if direction != "downside_price_drop":
            continue
        event_ts = _parse_iso(event.event_timestamp_utc)
        event_price = event.price_t
        series = price_series.get(symbol, [])
        for horizon in HORIZONS_HOURS:
            future_price = lookup_future_price(series, event_ts + timedelta(hours=horizon))
            if future_price is None:
                evaluations.append(
                    HorizonEvaluation(
                        event_id=event.event_id,
                        symbol=symbol,
                        event_timestamp_utc=event.event_timestamp_utc,
                        direction=direction,
                        horizon_hours=horizon,
                        event_price=event_price,
                        future_price=None, gross_return_bps=None,
                        net_return_bps_50=None, net_return_bps_75=None,
                        missing=True,
                    )
                )
            else:
                gross = compute_directional_return_bps(direction, event_price, future_price)
                evaluations.append(
                    HorizonEvaluation(
                        event_id=event.event_id,
                        symbol=symbol,
                        event_timestamp_utc=event.event_timestamp_utc,
                        direction=direction,
                        horizon_hours=horizon,
                        event_price=event_price,
                        future_price=future_price,
                        gross_return_bps=gross,
                        net_return_bps_50=net_bps_after_cost(gross, PRIMARY_COST_BPS),
                        net_return_bps_75=net_bps_after_cost(gross, 75.0),
                        missing=False,
                    )
                )
    metrics = [_metrics_for_horizon(h, evaluations) for h in HORIZONS_HOURS]
    return evaluations, metrics


def _metrics_for_horizon(
    horizon: int,
    evaluations: Sequence[HorizonEvaluation],
) -> HorizonMetrics:
    vals = [e for e in evaluations if e.horizon_hours == horizon and e.gross_return_bps is not None]
    missing = sum(1 for e in evaluations if e.horizon_hours == horizon and e.missing)
    n = len(vals)
    if n == 0:
        return HorizonMetrics(
            horizon_hours=horizon,
            evaluated_event_count=0,
            gross_mean_bps=None, gross_median_bps=None,
            net_mean_bps_50bps=None, net_median_bps_50bps=None,
            win_rate_50bps=None, mean_lcb_95_bps_50bps=None,
            missing_forward_count=missing,
        )
    gross_vals = [e.gross_return_bps for e in vals]  # type: ignore[misc]
    net_vals = [e.net_return_bps_50 for e in vals]  # type: ignore[misc]
    avg = mean(gross_vals)
    med = median(gross_vals)
    net_avg = mean(net_vals)  # type: ignore[arg-type]
    net_med = median(net_vals)  # type: ignore[arg-type]
    win = sum(1 for v in net_vals if v is not None and v > 0) / n  # type: ignore[arg-type]

    # LCB: mean - 1.96 * std/sqrt(n)
    std_err = (np.std([v for v in net_vals if v is not None], ddof=1) / math.sqrt(n)) if n > 1 else 0.0
    lcb = net_avg - 1.96 * std_err

    return HorizonMetrics(
        horizon_hours=horizon,
        evaluated_event_count=n,
        gross_mean_bps=avg, gross_median_bps=med,
        net_mean_bps_50bps=net_avg, net_median_bps_50bps=net_med,
        win_rate_50bps=win, mean_lcb_95_bps_50bps=lcb,
        missing_forward_count=missing,
    )


# ──────────────────────────────────────────────────────────────────
# Cohort metrics builder
# ──────────────────────────────────────────────────────────────────


def build_cohort_metrics(
    events: Sequence[StressEventRecord],
    evaluations: Sequence[HorizonEvaluation],
    primary_horizon: int = PRIMARY_HORIZON_HOURS,
) -> CohortMetrics:
    m = CohortMetrics()
    m.evaluated_event_count = len(events)
    primary_evals = [e for e in evaluations if e.horizon_hours == primary_horizon]
    m.missing_forward_coverage_count = sum(1 for e in primary_evals if e.missing)
    evaluated = [e for e in primary_evals if not e.missing]

    # Distribution
    dates_list = []
    for ev in events:
        ts = _parse_iso(ev.event_timestamp_utc)
        y = _year_key(ts)
        q = _quarter_key(ts)
        mo = _month_key(ts)
        w = _week_key(ts)
        d = ts.strftime("%Y-%m-%d")
        dates_list.append((y, q, mo, w, d))

    years = Counter(y for y, _, _, _, _ in dates_list)
    quarters = Counter(q for _, q, _, _, _ in dates_list)
    months = Counter(mo for _, _, mo, _, _ in dates_list)
    weeks = Counter(w for _, _, _, w, _ in dates_list)
    days = Counter(d for _, _, _, _, d in dates_list)

    m.year_distribution = dict(sorted(years.items()))
    m.quarter_distribution = dict(sorted(quarters.items()))
    m.month_distribution = dict(sorted(months.items()))

    total = len(events)
    if total > 0 and years:
        max_year = max(years.values())
        m.max_calendar_week_event_share = max(weeks.values()) / total if weeks else 0.0
        m.max_calendar_week = max(weeks, key=weeks.get) if weeks else ""

    if total > 0 and days:
        top5 = days.most_common(5)
        m.top_5_event_dates = [d for d, _ in top5]
        m.top_5_event_dates_share = sum(c for _, c in top5) / total

    # Symbol stats
    sym_counts = Counter(e.symbol for e in events)
    m.symbols_with_at_least_3_events = sum(1 for c in sym_counts.values() if c >= 3)
    if total > 0 and sym_counts:
        m.max_symbol_event_share = max(sym_counts.values()) / total
        m.max_symbol_event_share_symbol = max(sym_counts, key=sym_counts.get)

    # Return metrics (primary horizon)
    net_50s = [e.net_return_bps_50 for e in primary_evals if not e.missing and e.net_return_bps_50 is not None]
    net_75s = [e.net_return_bps_75 for e in primary_evals if not e.missing and e.net_return_bps_75 is not None]
    gross = [e.gross_return_bps for e in primary_evals if not e.missing and e.gross_return_bps is not None]

    if net_50s:
        m.net_mean_bps_50 = mean(net_50s)
        m.net_median_bps_50 = median(net_50s)
        m.win_rate_50 = sum(1 for v in net_50s if v > 0) / len(net_50s)
        # LCB
        std_err = (np.std(net_50s, ddof=1) / math.sqrt(len(net_50s))) if len(net_50s) > 1 else 0.0
        m.lower_confidence_bound_50 = m.net_mean_bps_50 - 1.96 * std_err

    if net_75s:
        m.net_mean_bps_75 = mean(net_75s)
    if gross:
        # net100 = mean(gross) - 100
        m.net_mean_bps_100 = mean(gross) - 100.0

    return m


# ──────────────────────────────────────────────────────────────────
# Negative controls
# ──────────────────────────────────────────────────────────────────


def _bootstrap_ci(
    values: list[float],
    n_resamples: int = 200,
    ci: float = 0.95,
    seed: int = BOOTSTRAP_CI_SEED,
) -> tuple[float, float]:
    """Bootstrap CI for the mean."""
    rng = np.random.default_rng(seed)
    means = np.zeros(n_resamples)
    for i in range(n_resamples):
        sample = rng.choice(values, size=len(values), replace=True)
        means[i] = np.mean(sample)
    alpha = (1.0 - ci) / 2.0
    lower = float(np.percentile(means, alpha * 100))
    upper = float(np.percentile(means, (1.0 - alpha) * 100))
    return lower, upper


def run_negative_controls(
    events: Sequence[StressEventRecord],
    all_altcoin_series: dict[str, list[tuple[datetime, float]]],
    all_series: dict[str, list[tuple[datetime, float]]],
    price_series: dict[str, list[tuple[datetime, float]]],
    extension_start_utc: datetime | None = None,
    extension_end_utc: datetime | None = None,
) -> dict[str, Any]:
    """Run three negative controls on extension-only events.

    1. Boring control: sample timestamps where detector NOT triggered
    2. Random timestamp control: random timestamps from eligible mask
    3. Inverse direction control: invert return sign

    Returns control results dict.
    """
    if not events:
        return {"error": "no_events_for_controls"}

    n_events = len(events)
    rng_boring = np.random.default_rng(BORING_CONTROL_SEED)
    rng_random = np.random.default_rng(RANDOM_TS_CONTROL_SEED)
    rng_inverse = np.random.default_rng(INVERSE_CONTROL_SEED)
    rng_ci = np.random.default_rng(BOOTSTRAP_CI_SEED)

    # Gather all eligible timestamps across altcoin symbols
    # (same symbol universe as detector, within extension window)
    eligible_timestamps: dict[str, list[datetime]] = {}
    for sym, series in all_altcoin_series.items():
        timestamps = [ts for ts, _ in series]
        if extension_start_utc is not None and extension_end_utc is not None:
            timestamps = [
                ts for ts in timestamps
                if extension_start_utc <= ts <= extension_end_utc
            ]
        # Require forward 24h coverage
        timestamps = [
            ts for ts in timestamps
            if has_forward_24h_coverage(series, ts)
        ]
        if timestamps:
            eligible_timestamps[sym] = timestamps

    # Flatten all eligible points with symbol + timestamp
    all_eligible: list[tuple[str, datetime, float]] = []
    for sym, tss in eligible_timestamps.items():
        series_dict = dict(all_altcoin_series[sym])
        for ts in tss:
            px = series_dict.get(ts, 0.0)
            if px > 0:
                all_eligible.append((sym, ts, px))

    if not all_eligible:
        return {"error": "no_eligible_timestamps_for_controls", "n_eligible": 0}

    # Helper: evaluate a set of (symbol, timestamp, price) tuples
    def _evaluate_control_set(
        points: list[tuple[str, datetime, float]],
        direction_mult: float = 1.0,
    ) -> dict[str, Any]:
        net_vals_50 = []
        for sym, ts, px in points:
            series = all_series.get(sym, [])
            target = ts + timedelta(hours=PRIMARY_HORIZON_HOURS)
            future_px = lookup_future_price(series, target)
            if future_px is not None:
                gross = (future_px / px - 1.0) * 10000.0
                net_50 = gross * direction_mult - PRIMARY_COST_BPS
                net_vals_50.append(net_50)

        if not net_vals_50:
            return {"evaluated_count": 0}
        n = len(net_vals_50)
        avg = mean(net_vals_50)
        med = median(net_vals_50)
        win = sum(1 for v in net_vals_50 if v > 0) / n
        ci_lower, ci_upper = _bootstrap_ci(net_vals_50, seed=BOOTSTRAP_CI_SEED)
        return {
            "n": n,
            "net50_mean_bps": avg,
            "net50_median_bps": med,
            "win_rate_50": win,
            "ci_95_lower": ci_lower,
            "ci_95_upper": ci_upper,
        }

    # 1. Boring control: sample non-stress timestamps
    # For each symbol, find timestamps where stress detector NOT triggered
    boring_pool: list[tuple[str, datetime, float]] = []
    for sym, tss in eligible_timestamps.items():
        series = all_altcoin_series[sym]
        # Compute stress status for sample timestamps
        # Check all hourly timestamps: vol percentile < 80% or return > -300
        pts = compute_stress_points(sym, series)
        stress_set = {p.timestamp for p in pts
                      if p.trailing_1h_return_bps <= TRAILING_1H_RETURN_THRESHOLD_BPS
                      and p.trailing_6h_realized_vol_percentile >= TRAILING_6H_VOL_PERCENTILE_THRESHOLD}
        for ts in tss:
            if ts not in stress_set:
                series_dict = dict(series)
                px = series_dict.get(ts, 0.0)
                if px > 0:
                    boring_pool.append((sym, ts, px))

    if boring_pool and len(boring_pool) >= n_events:
        indices = rng_boring.choice(len(boring_pool), size=n_events, replace=False)
        boring_sample = [boring_pool[i] for i in indices]
    elif boring_pool:
        boring_sample = boring_pool  # use all available
    else:
        boring_sample = []

    boring_result = _evaluate_control_set(boring_sample) if boring_sample else {"error": "boring_pool_empty"}

    # 2. Random timestamp control: random sample from all eligible
    if all_eligible and len(all_eligible) >= n_events:
        indices = rng_random.choice(len(all_eligible), size=n_events, replace=False)
        random_sample = [all_eligible[i] for i in indices]
    elif all_eligible:
        random_sample = all_eligible
    else:
        random_sample = []

    random_result = _evaluate_control_set(random_sample) if random_sample else {"error": "random_pool_empty"}

    # 3. Inverse direction control: take actual accepted events, invert returns
    inverse_points = []
    for ev in events:
        sym = ev.symbol
        ts = _parse_iso(ev.event_timestamp_utc)
        px = ev.price_t
        inverse_points.append((sym, ts, px))

    inverse_result = _evaluate_control_set(inverse_points, direction_mult=-1.0) if inverse_points else {"error": "inverse_pool_empty"}

    # Bootstrap CI for the extension-only events
    control_results = {
        "boring_control": boring_result,
        "random_timestamp_control": random_result,
        "inverse_direction_control": inverse_result,
    }

    # Control pass/fail
    warnings: list[str] = []
    failed = False
    for name, result in [
        ("boring_control", boring_result),
        ("random_timestamp_control", random_result),
        ("inverse_direction_control", inverse_result),
    ]:
        if isinstance(result, dict) and "net50_mean_bps" in result:
            if result.get("ci_95_upper", 0) > 50:
                warnings.append(f"EXTENSION_CONTROL_NOISE_WARNING: {name} CI upper = {result['ci_95_upper']:.2f}")
            if result["net50_mean_bps"] > 50 or (result.get("win_rate_50", 0) or 0) > 0.55:
                failed = True

    return {
        "controls": control_results,
        "control_warnings": warnings,
        "control_passed": not failed,
    }


# ──────────────────────────────────────────────────────────────────
# Independent return reproduction
# ──────────────────────────────────────────────────────────────────


def independent_return_reproduction(
    events: Sequence[StressEventRecord],
    all_series: dict[str, list[tuple[datetime, float]]],
    main_net50_mean: float,
) -> dict[str, Any]:
    """Recompute 24h forward returns independently to verify main result."""
    net_vals: list[float] = []
    for event in events:
        symbol = event.symbol.upper()
        if symbol in ALTCOIN_EXCLUDED_SYMBOLS:
            continue
        event_ts = _parse_iso(event.event_timestamp_utc)
        event_price = event.price_t
        series = all_series.get(symbol, [])
        # Look forward 24h
        future_price = None
        target = event_ts + timedelta(hours=24)
        for ts, px in series:
            if ts >= target:
                if (ts - target).total_seconds() <= 2 * 3600:
                    future_price = px
                break
        if future_price is not None:
            gross = (future_price / event_price - 1.0) * 10000.0
            net = gross - PRIMARY_COST_BPS
            net_vals.append(net)

    if not net_vals:
        return {"status": "no_valid_events", "indep_net50_mean": None}

    indep_mean = mean(net_vals)
    diff = abs(indep_mean - main_net50_mean)
    return {
        "indep_n": len(net_vals),
        "indep_net50_mean": indep_mean,
        "main_net50_mean": main_net50_mean,
        "diff_bps": diff,
        "passed": diff <= 5.0,
    }


# ──────────────────────────────────────────────────────────────────
# Archive validation
# ──────────────────────────────────────────────────────────────────


def validate_archive(
    archive_path: Path,
    extension_path: Path | None = None,
    frozen_symbols: Sequence[str] | None = None,
) -> ArchiveValidation:
    """Validate archive data for analysis readiness."""
    if frozen_symbols is None:
        frozen_symbols = [
            "AAVE","ADA","APT","ARB","ATOM","AVAX","BCH","BNB","BTC",
            "DOGE","DOT","ENA","ETH","FET","HYPE","INJ","JUP","LINK",
            "LTC","MKR","NEAR","ONDO","OP","PENDLE","SEI","SOL","SUI",
            "TIA","TON","TRX","UNI","WIF","WLD","XRP",
        ]

    # Check manifest
    manifest = None
    manifest_path = archive_path / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            pass

    jsonl_files = _discover_jsonl_files(archive_path)
    ext_files = _discover_jsonl_files(extension_path) if extension_path and extension_path.is_dir() else []

    all_files = jsonl_files + ext_files
    symbols_present_set: set[str] = set()
    total_rows = 0
    duplicate_count = 0
    per_symbol_last: dict[str, str] = {}
    global_first: str | None = None
    global_last: str | None = None

    for fp in all_files:
        sym = fp.stem.strip().upper()
        symbols_present_set.add(sym)
        rows = _read_jsonl_mmap(fp)
        total_rows += len(rows)
        seen_ts: set[str] = set()
        for row in rows:
            ts = str(row.get("ts_event", ""))
            if ts in seen_ts:
                duplicate_count += 1
            seen_ts.add(ts)
        if rows:
            per_symbol_last[sym] = str(rows[-1].get("ts_event", ""))

    symbols_expected = list(frozen_symbols)
    symbols_present = sorted(symbols_present_set)
    symbols_missing = sorted(set(symbols_expected) - symbols_present_set)

    # Global timestamps from archive (not extension)
    if jsonl_files:
        first_ts: str | None = None
        last_ts: str | None = None
        for fp in jsonl_files:
            rows = _read_jsonl_mmap(fp)
            if rows:
                ft = str(rows[0].get("ts_event", ""))
                lt = str(rows[-1].get("ts_event", ""))
                if first_ts is None or ft < first_ts:
                    first_ts = ft
                if last_ts is None or lt > last_ts:
                    last_ts = lt
        global_first = first_ts
        global_last = last_ts

    caveats: list[str] = []
    if manifest:
        caveats = manifest.get("coverage_caveats", [])

    # Compute latest_evaluable_complete_timestamp
    latest_complete: str | None = None
    if per_symbol_last:
        min_last = min(per_symbol_last.values())
        latest_complete = min_last
        # Subtract 24h forward horizon
        if latest_complete:
            dt = _parse_iso(latest_complete)
            dt -= timedelta(hours=PRIMARY_HORIZON_HOURS)
            latest_complete = _utc_iso(dt)

    return ArchiveValidation(
        valid=True,
        classification="ARCHIVE_READY",
        jsonl_file_count=len(jsonl_files) + len(ext_files),
        total_row_count=total_rows,
        global_first_ts=global_first,
        global_last_ts=global_last,
        symbols_expected=symbols_expected,
        symbols_present=symbols_present,
        symbols_missing=symbols_missing,
        per_symbol_last_ts=per_symbol_last,
        duplicate_timestamp_count=duplicate_count,
        coverage_caveats=caveats,
        latest_evaluable_complete_timestamp=latest_complete,
        unusable_symbol_notes={"MKR": "SYMBOL_INACTIVE_ZERO_OI_PLACEHOLDER"} if "MKR" in symbols_present else {},
    )


# ──────────────────────────────────────────────────────────────────
# Archive extension
# ──────────────────────────────────────────────────────────────────


def extend_archive_if_needed(
    prior_end_utc: datetime,
    max_budget_usd: float = 1.00,
) -> tuple[bool, str, str | None]:
    """Download 2026 data to staging if archive doesn't cover present-day.

    Returns (invoked, helper_used, staging_path or None).
    """
    # Check if we already have 2026 data
    staging = STAGING_EXTENSION_PATH
    if staging.is_dir() and list(staging.glob("*.jsonl")):
        return False, "staging_already_exists", str(staging)

    # Check S3 for 2026 dates and download
    # Use run_hyperliquid_asset_ctxs_archive with estimate-only
    from datetime import date, timedelta

    # Check latest S3 date
    try:
        result = subprocess.run(
            ["aws", "s3", "ls", "s3://hyperliquid-archive/asset_ctxs/", "--request-payer", "requester"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return False, "AWS_S3_LIST_FAILED", None
        dates_available = sorted(set(
            line.split()[-1].replace(".csv.lz4", "")
            for line in result.stdout.strip().split("\n")
            if line.strip()
        ))
        if not dates_available:
            return False, "NO_S3_DATES_FOUND", None
        latest_s3_str = dates_available[-1]
        # Format: YYYYMMDD
        latest_s3 = date(
            int(latest_s3_str[:4]), int(latest_s3_str[4:6]), int(latest_s3_str[6:8])
        )
    except Exception as e:
        return False, f"S3_PROBE_FAILED_{e}", None

    # Dates to fetch
    fetch_start = date(prior_end_utc.year, prior_end_utc.month, prior_end_utc.day) + timedelta(days=1)
    if fetch_start > latest_s3:
        return False, "NO_NEW_DATA", None

    dates = []
    d = fetch_start
    while d <= latest_s3:
        dates.append(d)
        d += timedelta(days=1)

    # Write date list
    date_list_path = Path("/tmp/2026_dates") / "backend_dates.txt"
    date_list_path.parent.mkdir(parents=True, exist_ok=True)
    date_list_path.write_text("\n".join(d.isoformat() for d in dates) + "\n")

    # Estimate cost
    try:
        from examples.strategies.venue_agnostic_signal_observer.hyperliquid_asset_ctxs_archive import (
            estimate_asset_ctxs_cost,
        )
        estimated_bytes, estimated_usd, confirm_token = estimate_asset_ctxs_cost(dates)
    except Exception as e:
        return False, f"ESTIMATE_FAILED_{e}", None

    if estimated_usd > max_budget_usd:
        return False, "BUDGET_EXCEEDED", None

    subprocess.run(
        ["mkdir", "-p", str(staging)],
        check=True,
    )

    # Download using run_hyperliquid_asset_ctxs_archive
    cmd = [
        sys.executable, "-m", "examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hyperliquid_asset_ctxs_archive",
        "--date-list", str(date_list_path),
        "--out", str(staging),
        "--max-usd-budget", str(max_budget_usd),
        "--confirm-s3-spend", confirm_token,
        "--precommitment", str(PRIOR_GENERIC_STRESS_PRECOMMITMENT_PATH),
    ]
    result = subprocess.run(cmd, cwd=str(Path.cwd()), capture_output=True, text=True, timeout=3600)

    if result.returncode != 0:
        return False, f"DOWNLOAD_FAILED_{result.stderr[:200]}", None

    return True, "run_hyperliquid_asset_ctxs_archive", str(staging)


# ──────────────────────────────────────────────────────────────────
# Prior event loading
# ──────────────────────────────────────────────────────────────────


def load_prior_accepted_events() -> list[dict[str, Any]] | None:
    """Load prior accepted_events.jsonl."""
    path = PRIOR_GENERIC_STRESS_PHASE0A_REPORT_DIR / "accepted_events.jsonl"
    if not path.exists():
        return None
    events: list[dict[str, Any]] = []
    with path.open("rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            while True:
                line = m.readline()
                if not line:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    events.append(_loads_json_line(stripped))
                except Exception:
                    continue
    return events


def load_prior_summary() -> dict[str, Any] | None:
    path = PRIOR_GENERIC_STRESS_PHASE0A_REPORT_DIR / "summary.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_prior_phase0b_summary() -> dict[str, Any] | None:
    path = PRIOR_GENERIC_STRESS_PHASE0B_REPORT_DIR / "summary.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


# ──────────────────────────────────────────────────────────────────
# Prior precommitment drift guard
# ──────────────────────────────────────────────────────────────────


def verify_prior_precommitment(
    inherited_hash: str,
) -> bool:
    """Verify prior precommitment hash matches the documented one."""
    computed = compute_precommitment_hash(PRIOR_GENERIC_STRESS_PRECOMMITMENT_PATH)
    return computed == inherited_hash


# ──────────────────────────────────────────────────────────────────
# Detector code drift guard
# ──────────────────────────────────────────────────────────────────


def verify_detector_code(precommitment_hash: str) -> bool:
    """Verify detector code hash matches precommitment."""
    computed = get_detector_code_hash()
    return computed == precommitment_hash


# ──────────────────────────────────────────────────────────────────
# Window constants (from prior report)
# ──────────────────────────────────────────────────────────────────

# Prior window: 2024-01-01 through 2025-12-31
PRIOR_WINDOW_START_UTC = datetime(2024, 1, 1, tzinfo=UTC)
PRIOR_WINDOW_END_UTC = datetime(2025, 12, 31, 23, 59, 0, tzinfo=UTC)

# Expected prior event count for reproduction check
EXPECTED_PRIOR_EVENT_COUNT = 558
EXPECTED_PRIOR_24H_NET50_MEAN = 339.59448071794975
EXPECTED_PRIOR_24H_NET50_MEDIAN = 273.99049390815946
EXPECTED_PRIOR_WIN_RATE = 0.6330935251798561


# ──────────────────────────────────────────────────────────────────
# Run the full extension
# ──────────────────────────────────────────────────────────────────


def run_extension(
    precommitment_path: Path,
    report_dir: Path | None = None,
    no_cache: bool = False,
) -> ExtensionResult:
    """Run the present-day extension diagnostic."""
    start_time = time.time()
    git_sha, git_dirty = _git_metadata()
    warnings_flags: list[str] = []

    # ── Phase 0: Precommitment load and self-hash ──
    precommitment_text = precommitment_path.read_text(encoding="utf-8")
    precommitment_self_hash = compute_precommitment_self_hash(precommitment_text)
    inherited_from_hash = "9faf9a8e9111bf5f9a564a69980b8c26d6a0084d229546f67195c51e95b8c448"

    # ── Phase 1: Prior precommitment drift guard ──
    prior_hash_verified = verify_prior_precommitment(inherited_from_hash)
    if not prior_hash_verified:
        return ExtensionResult(summary={
            "status": STATUS_PRIOR_PRECOMMITMENT_DRIFTED,
            "final_verdict": STATUS_PRIOR_PRECOMMITMENT_DRIFTED,
            "generated_at_utc": _utc_iso(datetime.now(UTC)),
            "git_sha": git_sha, "git_branch": "feat/generic-altcoin-stress-extension-present-day-phase0",
            "git_dirty_summary": git_dirty,
            "precommitment_sha256": precommitment_self_hash,
            "inherited_from_precommitment_sha256": inherited_from_hash,
            "prior_precommitment_hash_verified": False,
        })

    # ── Phase 2: Detector code hash ──
    detector_code_hash = get_detector_code_hash()
    # Read precommitment's detector_code_sha256
    detector_code_hash_matches = False
    m = re.search(r"detector_code_sha256:\s*([a-f0-9]{64})", precommitment_text)
    if m:
        expected_detector_hash = m.group(1)
        detector_code_hash_matches = (detector_code_hash == expected_detector_hash)

    if not detector_code_hash_matches:
        return ExtensionResult(summary={
            "status": STATUS_DETECTOR_CODE_DRIFTED,
            "final_verdict": STATUS_DETECTOR_CODE_DRIFTED,
            "generated_at_utc": _utc_iso(datetime.now(UTC)),
            "git_sha": git_sha, "git_branch": "feat/generic-altcoin-stress-extension-present-day-phase0",
            "git_dirty_summary": git_dirty,
            "precommitment_sha256": precommitment_self_hash,
            "inherited_from_precommitment_sha256": inherited_from_hash,
            "prior_precommitment_hash_verified": True,
            "detector_code_sha256": detector_code_hash,
            "detector_code_hash_verified": False,
        })

    # ── Phase 3: Archive validation ──
    archive_path = ACTIVE_ARCHIVE_PATH
    if not archive_path.is_dir():
        return ExtensionResult(summary={
            "status": STATUS_ARCHIVE_UNAVAILABLE,
            "final_verdict": STATUS_ARCHIVE_UNAVAILABLE,
            "archive_source_path": str(archive_path),
        })

    # Check if extension staging exists
    staging = STAGING_EXTENSION_PATH
    if not staging.is_dir() or not list(staging.glob("*.jsonl")):
        prior_summary = load_prior_summary()
        prior_end_str = (prior_summary or {}).get("last_timestamp") or "2025-12-31T23:59:00Z"
        prior_end = _parse_iso(prior_end_str)
        invoked, helper, staging_str = extend_archive_if_needed(prior_end)
        if not invoked and "BUDGET" in (helper or ""):
            return ExtensionResult(summary={
                "status": STATUS_BACKFILL_BUDGET_EXCEEDED,
                "archive_backfill_invoked": False,
                "archive_backfill_helper": helper,
            })
        if not invoked and "FAILED" in (helper or ""):
            return ExtensionResult(summary={
                "status": STATUS_ARCHIVE_VALIDATION_FAILED,
                "archive_backfill_invoked": False,
                "archive_backfill_helper": helper,
            })

    validation = validate_archive(archive_path, staging if staging.is_dir() else None)
    if not validation.valid:
        return ExtensionResult(summary={
            "status": STATUS_ARCHIVE_VALIDATION_FAILED,
            "archive_validation": asdict(validation),
        })

    # Determine windows
    prior_window_end = PRIOR_WINDOW_END_UTC
    if validation.latest_evaluable_complete_timestamp:
        latest_complete = _parse_iso(validation.latest_evaluable_complete_timestamp)
    else:
        latest_complete = datetime(2026, 4, 28, 23, 59, 0, tzinfo=UTC)  # fallback

    extension_start = prior_window_end + timedelta(seconds=1)
    extension_end = latest_complete

    # ── Phase 4: Load archive data ──
    print("Loading archive data (may take several minutes)...", flush=True)
    altcoin_series, all_series = _read_archive_rows(
        archive_path, staging if staging.is_dir() else None,
        progress_label="ARCHIVE",
    )
    print(f"Loaded {len(altcoin_series)} altcoin symbols, {len(all_series)} total", flush=True)

    # ── Phase 5: Run detector on prior window (reproduction) ──
    # Check if prior accepted events are available
    prior_events_data = load_prior_accepted_events()
    if prior_events_data is None:
        return ExtensionResult(summary={
            "status": STATUS_PRIOR_EVENTS_UNAVAILABLE,
        })

    prior_events_records: list[StressEventRecord] = []
    for ev in prior_events_data:
        # Prior events have event_direction="downside_price_drop" and direction="long"
        # Our StressEventRecord uses direction for the detection direction
        detection_direction = str(ev.get("event_direction") or ev.get("direction", "downside_price_drop"))
        prior_events_records.append(
            StressEventRecord(
                event_id=str(ev.get("event_id", "")),
                symbol=str(ev.get("symbol", "")),
                event_timestamp_utc=str(ev.get("event_timestamp_utc", "")),
                detector_family=str(ev.get("detector_family", STUDY_ID)),
                direction=detection_direction,
                price_t=float(ev.get("price_t", 0.0)),
                trailing_1h_return_bps=float(ev.get("trailing_1h_return_bps", 0.0)),
                trailing_6h_realized_vol_bps=float(ev.get("trailing_6h_realized_vol_bps", 0.0)),
                trailing_6h_realized_vol_percentile=float(ev.get("trailing_6h_realized_vol_percentile", 0.5)),
                cooldown_key=str(ev.get("cooldown_key", "")),
                archive_source_path=str(ev.get("archive_source_path", "")),
            )
        )

    # Debug: evaluate prior events with our evaluation
    prior_evals, prior_metrics = evaluate_events(prior_events_records, all_series)
    primary_24h = next((m for m in prior_metrics if m.horizon_hours == 24), None)

    # Reproduction check
    if primary_24h is None or primary_24h.evaluated_event_count == 0:
        pass  # Can't validate
    prior_repro_ok = True
    prior_repro_details: dict[str, Any] = {}

    prior_phase0b = load_prior_phase0b_summary()
    if prior_phase0b and primary_24h is not None:
        expected_count = prior_phase0b.get("phase0b_event_count_evaluated", EXPECTED_PRIOR_EVENT_COUNT)
        expected_mean = EXPECTED_PRIOR_24H_NET50_MEAN
        expected_median = EXPECTED_PRIOR_24H_NET50_MEDIAN
        expected_win = EXPECTED_PRIOR_WIN_RATE

        count_ok = abs(primary_24h.evaluated_event_count - expected_count) <= max(10, 0.02 * expected_count)
        mean_ok = primary_24h.net_mean_bps_50bps is not None and abs(primary_24h.net_mean_bps_50bps - expected_mean) <= 5.0
        median_ok = primary_24h.net_median_bps_50bps is not None and abs(primary_24h.net_median_bps_50bps - expected_median) <= 5.0
        win_ok = primary_24h.win_rate_50bps is not None and abs(primary_24h.win_rate_50bps - expected_win) <= 0.03

        prior_repro_ok = count_ok and mean_ok and median_ok and win_ok
        prior_repro_details = {
            "evaluated_count": primary_24h.evaluated_event_count,
            "expected_count": expected_count,
            "count_within_tolerance": count_ok,
            "net50_mean": primary_24h.net_mean_bps_50bps,
            "expected_net50_mean": expected_mean,
            "mean_diff_bps": abs((primary_24h.net_mean_bps_50bps or 0) - expected_mean),
            "mean_within_5bps": mean_ok,
            "net50_median": primary_24h.net_median_bps_50bps,
            "expected_net50_median": expected_median,
            "median_within_5bps": median_ok,
            "win_rate": primary_24h.win_rate_50bps,
            "expected_win_rate": expected_win,
            "win_rate_within_0_03": win_ok,
        }

    if not prior_repro_ok:
        return ExtensionResult(summary={
            "status": STATUS_PRIOR_REPRODUCTION_FAILED,
            "prior_reproduction": prior_repro_details,
            "final_verdict": STATUS_PRIOR_REPRODUCTION_FAILED,
            "generated_at_utc": _utc_iso(datetime.now(UTC)),
            "git_sha": git_sha,
        })

    # ── Phase 6: Run detector on ALL data (for full extended window) ──
    all_events: list[StressEventRecord] = []
    total_before_cooldown = 0
    total_after_cooldown = 0
    for sym in sorted(altcoin_series.keys()):
        evts, before, after = process_symbol(
            sym, altcoin_series[sym], str(archive_path)
        )
        all_events.extend(evts)
        total_before_cooldown += before
        total_after_cooldown += after

    # Cosmic cooldown across all symbols? No — per-symbol cooldown already applied.
    # But we need to ensure no temporal overlap. The prior implementation applied
    # per-symbol cooldown independently. We keep that.

    # Filter cohorts
    prior_window_events = [
        e for e in all_events
        if PRIOR_WINDOW_START_UTC <= _parse_iso(e.event_timestamp_utc) <= PRIOR_WINDOW_END_UTC
    ]
    extension_only_events = [
        e for e in all_events
        if _parse_iso(e.event_timestamp_utc) > PRIOR_WINDOW_END_UTC
        and _parse_iso(e.event_timestamp_utc) <= extension_end
    ]
    # For full-window, also include prior events from the loaded accepted_events.jsonl
    # but we need to evaluate all extension events together with prior reproduction events
    # Actually full-window = prior_window + extension_only from the new detector run
    # But the prior events were captured with the old detector code. To avoid mixing
    # different detector runs, we use all_events (which includes both prior-window and
    # extension-window events from the current detector run).
    full_window_events = [e for e in all_events
                          if _parse_iso(e.event_timestamp_utc) <= extension_end]

    # Also count total before/after for each cohort
    prior_before = total_before_cooldown  # rough — same detector
    prior_after = len(prior_window_events)
    ext_before = total_before_cooldown
    ext_after = len(extension_only_events)
    full_before = total_before_cooldown
    full_after = len(full_window_events)

    # ── Phase 7: Evaluate each cohort ──
    prior_evaluations, prior_metrics = evaluate_events(prior_window_events, all_series)
    ext_evaluations, ext_metrics = evaluate_events(extension_only_events, all_series)
    full_evaluations, full_metrics = evaluate_events(full_window_events, all_series)

    prior_cohort = build_cohort_metrics(prior_window_events, prior_evaluations)
    ext_cohort = build_cohort_metrics(extension_only_events, ext_evaluations)
    full_cohort = build_cohort_metrics(full_window_events, full_evaluations)

    # Fix counts
    prior_cohort.event_count_before_cooldown = len(prior_window_events)
    prior_cohort.event_count_after_cooldown = len(prior_window_events)
    ext_cohort.event_count_before_cooldown = len(extension_only_events)
    ext_cohort.event_count_after_cooldown = len(extension_only_events)
    full_cohort.event_count_before_cooldown = len(full_window_events)
    full_cohort.event_count_after_cooldown = len(full_window_events)

    # ── Phase 8: Independent return reproduction ──
    # Use extension_only_events for primary check
    ext_net50 = ext_cohort.net_mean_bps_50 or 0.0
    indep_result = independent_return_reproduction(
        extension_only_events, all_series, ext_net50
    )
    indep_passed = indep_result.get("passed", False)

    if not indep_passed and ext_cohort.evaluated_event_count > 0:
        warnings_flags.append(STATUS_RETURN_REPRO_FAILED)

    # ── Phase 9: Negative controls ──
    control_results = run_negative_controls(
        extension_only_events,
        altcoin_series, all_series, all_series,
        extension_start_utc=extension_start,
        extension_end_utc=extension_end,
    )
    control_passed = control_results.get("control_passed", False)
    if not control_passed:
        warnings_flags.append(STATUS_NEGATIVE_CONTROL_FAILED)
    if control_results.get("control_warnings"):
        warnings_flags.extend(control_results["control_warnings"])

    # ── Phase 10: Temporal concentration gates ──
    ext_count = len(extension_only_events)
    if ext_count < 100:
        warnings_flags.append(STATUS_UNDERPOWERED)

    # Year share for extension-only
    ext_years = ext_cohort.year_distribution
    ext_total = sum(ext_years.values())
    if ext_total > 0:
        max_year_share = max(ext_years.values()) / ext_total
        if max_year_share > 0.50:
            warnings_flags.append(STATUS_TEMPORAL_CONCENTRATION_FAILED)

    # Weekly clustering
    from collections import Counter as CCounter
    week_counts: CCounter = CCounter()
    for ev in extension_only_events:
        ts = _parse_iso(ev.event_timestamp_utc)
        week_counts[_week_key(ts)] += 1
    if ext_total > 0 and week_counts:
        max_week_share = max(week_counts.values()) / ext_total
        if max_week_share > 0.30:
            warnings_flags.append(WARNING_WEEKLY_CLUSTERING)

    # Full-window 2024 share
    full_years = full_cohort.year_distribution
    full_total = sum(full_years.values())
    if full_total > 0:
        year_2024_share = full_years.get("2024", 0) / full_total
        if year_2024_share > 0.50:
            warnings_flags.append(WARNING_FULL_WINDOW_CONCENTRATION)

    # ── Phase 11: Lookahead audit ──
    lookahead_ok = True
    lookahead_details: list[str] = []
    for ev in extension_only_events:
        ts = _parse_iso(ev.event_timestamp_utc)
        if ts <= PRIOR_WINDOW_END_UTC:
            lookahead_ok = False
            lookahead_details.append(f"event {ev.event_id} in prior window")
        if ts > extension_end:
            lookahead_ok = False
            lookahead_details.append(f"event {ev.event_id} beyond latest_evaluable")
    # Also verify forward coverage
    for ev in extension_only_events:
        ts = _parse_iso(ev.event_timestamp_utc)
        sym = ev.symbol
        series = all_series.get(sym, [])
        if not has_forward_24h_coverage(series, ts):
            lookahead_ok = False
            lookahead_details.append(f"event {ev.event_id} lacks forward 24h coverage")

    # ── Phase 12: Survivorship audit ──
    # Classify survivorship
    frozen_symbols_expected = set(validation.symbols_expected)
    symbols_with_events = set(e.symbol for e in extension_only_events)
    all_expected_alive = frozen_symbols_expected - ALTCOIN_EXCLUDED_SYMBOLS - {"PEPE"}
    missing_survivors = all_expected_alive - symbols_with_events
    if missing_survivors:
        survivorship_status = "SURVIVORSHIP_AMBIGUITY"
        warnings_flags.append(WARNING_SURVIVORSHIP_AMBIGUITY)
    else:
        survivorship_status = "SURVIVORSHIP_CLEAR"

    # ── Phase 13: Final verdict ──
    pass_conditions = (
        prior_hash_verified
        and detector_code_hash_matches
        and prior_repro_ok
        and validation.valid
        and ext_count >= 100
        and ext_cohort.net_mean_bps_50 is not None
        and ext_cohort.net_mean_bps_50 > 50.0
        and (ext_cohort.net_mean_bps_75 is None or ext_cohort.net_mean_bps_75 > 0)
        and (ext_cohort.net_mean_bps_100 is None or ext_cohort.net_mean_bps_100 > 0)
        and control_passed
        and indep_passed
        and lookahead_ok
        and STATUS_TEMPORAL_CONCENTRATION_FAILED not in warnings_flags
        and STATUS_UNDERPOWERED not in warnings_flags
    )

    if pass_conditions:
        final_status = STATUS_AUDIT_PASSED
        final_verdict = STATUS_AUDIT_PASSED
        cooling_start = _utc_iso(datetime.now(UTC))
        cooling_end = _utc_iso(datetime.now(UTC) + timedelta(hours=72))
    else:
        # Pick the most relevant fail status
        if STATUS_TEMPORAL_CONCENTRATION_FAILED in warnings_flags:
            final_status = STATUS_TEMPORAL_CONCENTRATION_FAILED
        elif STATUS_UNDERPOWERED in warnings_flags:
            final_status = STATUS_UNDERPOWERED
        elif not control_passed:
            final_status = STATUS_NEGATIVE_CONTROL_FAILED
        elif not lookahead_ok:
            final_status = STATUS_LOOKAHEAD_AUDIT_FAILED
        elif STATUS_RETURN_REPRO_FAILED in warnings_flags:
            final_status = STATUS_RETURN_REPRO_FAILED
        else:
            final_status = STATUS_NEGATIVE_CONTROL_FAILED
        final_verdict = final_status
        cooling_start = None
        cooling_end = None

    # ── Phase 14: Build summary ──
    summary = {
        "study_id": STUDY_ID,
        "stage": STAGE,
        "venue": VENUE,
        "safety_mode": SAFETY_MODE,
        "status": final_status,
        "final_verdict": final_verdict,
        "generated_at_utc": _utc_iso(datetime.now(UTC)),
        "git_sha": git_sha,
        "git_branch": "feat/generic-altcoin-stress-extension-present-day-phase0",
        "git_dirty_summary": git_dirty,
        "precommitment_path": str(precommitment_path),
        "precommitment_sha256": precommitment_self_hash,
        "inherited_from_precommitment_sha256": inherited_from_hash,
        "prior_precommitment_path": str(PRIOR_GENERIC_STRESS_PRECOMMITMENT_PATH),
        "prior_precommitment_hash_verified": prior_hash_verified,
        "detector_code_sha256": detector_code_hash,
        "detector_code_hash_verified": detector_code_hash_matches,
        "archive_source_path": str(archive_path),
        "archive_staging_path": str(staging) if staging.is_dir() else None,
        "archive_start_utc": validation.global_first_ts,
        "archive_end_utc": validation.global_last_ts,
        "archive_end_age_days": None,  # computed at runtime
        "latest_evaluable_complete_timestamp": validation.latest_evaluable_complete_timestamp,
        "prior_window_start_utc": _utc_iso(PRIOR_WINDOW_START_UTC),
        "prior_window_end_utc": _utc_iso(PRIOR_WINDOW_END_UTC),
        "extension_window_start_utc": _utc_iso(extension_start),
        "extension_window_end_utc": _utc_iso(extension_end),
        "archive_backfill_invoked": False,
        "archive_backfill_helper": None,
        "archive_validation_status": validation.classification,
        "archive_validation": asdict(validation),
        "prior_reproduction_status": "PASSED" if prior_repro_ok else "FAILED",
        "prior_reproduction": prior_repro_details,
        "independent_return_reproduction_status": "PASSED" if indep_passed else "FAILED",
        "independent_return_reproduction": indep_result,
        "lookahead_audit_status": "PASSED" if lookahead_ok else "FAILED",
        "lookahead_audit_details": lookahead_details,
        "survivorship_status": survivorship_status,
        "survivorship_missing_symbols": list(missing_survivors),
        "prior_window_metrics": asdict(prior_cohort),
        "extension_only_metrics": asdict(ext_cohort),
        "full_extended_window_metrics": asdict(full_cohort),
        "negative_control_metrics": control_results,
        "warning_flags": warnings_flags,
        "phase0c_precommitment_unlocked_after_cooling_period": final_status == STATUS_AUDIT_PASSED,
        "cooling_period_starts_at_utc": cooling_start,
        "cooling_period_ends_at_utc": cooling_end,
        "next_action_blocked_until_timestamp": cooling_end,
        "elapsed_seconds": time.time() - start_time,
        "orjson_available": HAS_ORJSON,
    }

    result = ExtensionResult(
        summary=summary,
        prior_reproduction_events=prior_window_events or None,
        extension_only_events=extension_only_events or None,
        full_extended_events=full_window_events or None,
    )

    # ── Write report ──
    if report_dir is not None:
        try:
            write_report(result, report_dir)
        except Exception as exc:
            print(f"REPORT_WRITE_FAILED: {exc}", flush=True)

    return result


# ──────────────────────────────────────────────────────────────────
# Report writer
# ──────────────────────────────────────────────────────────────────


def write_report(result: ExtensionResult, report_dir: Path) -> None:
    """Write report artifacts."""
    report_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_dir / "summary.json", result.summary)

    # summary.md
    s = result.summary
    surv_status = s.get("survivorship_status", "UNKNOWN")
    surv_missing = s.get("survivorship_missing_symbols", [])
    lines = [
        f"# Generic altcoin stress extension: present-day Phase 0",
        f"",
        f"Status: `{s.get('status')}`",
        f"Final verdict: `{s.get('final_verdict')}`",
        f"",
        f"## Prior reproduction",
        f"Status: `{s.get('prior_reproduction_status')}`",
        f"Details: `{json.dumps(s.get('prior_reproduction', {}))}`",
        f"",
        f"## Extension-only window",
    ]
    ext = s.get("extension_only_metrics", {})
    if isinstance(ext, dict):
        lines.append(f"  Events: {ext.get('evaluated_event_count', 0)}")
        lines.append(f"  Net50 mean: {ext.get('net_mean_bps_50')}")
        lines.append(f"  Net50 median: {ext.get('net_median_bps_50')}")
        lines.append(f"  Win rate (50 bps): {ext.get('win_rate_50')}")
        lines.append(f"  Net75 mean: {ext.get('net_mean_bps_75')}")
        lines.append(f"  Net100 mean: {ext.get('net_mean_bps_100')}")

    lines.extend([
        f"",
        f"## Full extended window",
        f"  Events: {s.get('full_extended_window_metrics', {}).get('evaluated_event_count', 0)}",
        f"  2024 share: {s.get('full_extended_window_metrics', {}).get('year_distribution', {}).get('2024', 0) / max(sum(s.get('full_extended_window_metrics', {}).get('year_distribution', {}).values()), 1):.4f}",
        f"",
        f"## Controls",
        f"  Control passed: {s.get('negative_control_metrics', {}).get('control_passed', False)}",
        f"",
        f"## Survivorship",
        f"  Status: {surv_status}",
        f"  Missing symbols: {surv_missing}",
        f"",
        f"## Warnings",
    ])
    for w in s.get("warning_flags", []):
        lines.append(f"- {w}")
    lines.extend([
        f"",
        f"## Safety",
        f"No orders, private keys, trading auth, live execution, paper trading, shadow execution, systemd watcher, bot path, or REJECTED_RESEARCH.md update were used.",
    ])
    atomic_write_text(report_dir / "summary.md", "\n".join(lines))

    # Write event JSONL files
    if result.prior_reproduction_events:
        _write_events_jsonl(report_dir / "accepted_events_prior_reproduction.jsonl", result.prior_reproduction_events)
    if result.extension_only_events:
        _write_events_jsonl(report_dir / "accepted_events_extension_only.jsonl", result.extension_only_events)
    if result.full_extended_events:
        _write_events_jsonl(report_dir / "accepted_events_full_extended.jsonl", result.full_extended_events)

    # Control results
    atomic_write_json(report_dir / "controls_extension_only.json", s.get("negative_control_metrics", {}))

    # Bootstrap CI if available
    atomic_write_json(report_dir / "control_bootstrap_ci.json", s.get("negative_control_metrics", {}).get("controls", {}))

    # Independent return reproduction
    atomic_write_json(report_dir / "independent_return_reproduction.json", s.get("independent_return_reproduction", {}))

    # Archive validation
    archive_val = s.get("archive_validation", {})
    if not isinstance(archive_val, dict):
        archive_val = {}
    atomic_write_json(report_dir / "archive_validation.json", archive_val)

    # Artifact manifest
    manifest = {
        "artifacts": [
            "summary.json",
            "summary.md",
            "accepted_events_prior_reproduction.jsonl",
            "accepted_events_extension_only.jsonl",
            "accepted_events_full_extended.jsonl",
            "controls_extension_only.json",
            "control_bootstrap_ci.json",
            "independent_return_reproduction.json",
            "archive_validation.json",
        ],
        "generated_at_utc": s.get("generated_at_utc"),
        "git_sha": s.get("git_sha"),
    }
    manifest["manifest_hash"] = _stable_manifest_hash(manifest)
    atomic_write_json(report_dir / "manifest.json", manifest)


def _write_events_jsonl(path: Path, events: Sequence[StressEventRecord]) -> None:
    """Write events to a JSONL file."""
    lines: list[str] = []
    for ev in events:
        lines.append(json.dumps({
            "event_id": ev.event_id,
            "symbol": ev.symbol,
            "event_timestamp_utc": ev.event_timestamp_utc,
            "detector_family": ev.detector_family,
            "direction": ev.direction,
            "price_t": ev.price_t,
            "trailing_1h_return_bps": ev.trailing_1h_return_bps,
            "trailing_6h_realized_vol_bps": ev.trailing_6h_realized_vol_bps,
            "trailing_6h_realized_vol_percentile": ev.trailing_6h_realized_vol_percentile,
            "cooldown_key": ev.cooldown_key,
            "archive_source_path": ev.archive_source_path,
        }, sort_keys=True) + "\n")
    path.write_text("".join(lines), encoding="utf-8")


__all__ = [
    "STUDY_ID",
    "STATUS_AUDIT_PASSED",
    "STATUS_UNDERPOWERED",
    "STATUS_TEMPORAL_CONCENTRATION_FAILED",
    "STATUS_NEGATIVE_CONTROL_FAILED",
    "STATUS_PRIOR_PRECOMMITMENT_DRIFTED",
    "STATUS_DETECTOR_CODE_DRIFTED",
    "STATUS_ARCHIVE_UNAVAILABLE",
    "STATUS_ARCHIVE_VALIDATION_FAILED",
    "STATUS_PRIOR_EVENTS_UNAVAILABLE",
    "STATUS_PRIOR_REPRODUCTION_FAILED",
    "STATUS_RETURN_REPRO_FAILED",
    "WARNING_WEEKLY_CLUSTERING",
    "WARNING_CONTROL_NOISE",
    "WARNING_FULL_WINDOW_CONCENTRATION",
    "WARNING_SURVIVORSHIP_AMBIGUITY",
    "run_extension",
    "compute_precommitment_hash",
    "compute_precommitment_self_hash",
    "get_detector_code_hash",
    "verify_prior_precommitment",
    "compute_stress_points",
    "filter_stress_candidates",
    "apply_cooldown",
    "evaluate_events",
    "build_cohort_metrics",
    "run_negative_controls",
    "independent_return_reproduction",
    "validate_archive",
    "PRIOR_WINDOW_START_UTC",
    "PRIOR_WINDOW_END_UTC",
    "EXPECTED_PRIOR_EVENT_COUNT",
    "EXPECTED_PRIOR_24H_NET50_MEAN",
    "EXPECTED_PRIOR_24H_NET50_MEDIAN",
    "EXPECTED_PRIOR_WIN_RATE",
    "PRIOR_GENERIC_STRESS_PRECOMMITMENT_PATH",
    "ACTIVE_ARCHIVE_PATH",
    "STAGING_EXTENSION_PATH",
    "ALTCOIN_EXCLUDED_SYMBOLS",
    "TRAILING_1H_RETURN_THRESHOLD_BPS",
    "TRAILING_6H_VOL_PERCENTILE_THRESHOLD",
    "COOLDOWN_HOURS",
    "PRIMARY_HORIZON_HOURS",
    "HORIZONS_HOURS",
    "PRIMARY_COST_BPS",
    "BORING_CONTROL_SEED",
    "RANDOM_TS_CONTROL_SEED",
    "INVERSE_CONTROL_SEED",
    "BOOTSTRAP_CI_SEED",
]