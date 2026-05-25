"""Generic altcoin stress regime ablation Phase 0A.

Price-only stress detector replacing liquidation/OI inputs.
Uses trailing 1h return + trailing 6h realized volatility percentile
to identify stress events, then applies 48h cooldown.

No OI, no liquidation, no funding inputs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    ArchiveRow,
    LoadDiagnostics,
    compute_precommitment_hash,
    load_archive_rows,
    parse_timestamp,
    utc_iso,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import (
    atomic_write_json,
    atomic_write_text,
)

STUDY_ID = "generic_altcoin_stress_regime_ablation_phase0"
STAGE = "phase0a_price_only_stress_detector"
VENUE = "hyperliquid"
SAFETY_MODE = "public_data_observer_only"

ALTCOIN_EXCLUDED_SYMBOLS = frozenset({"BTC", "ETH"})

# Frozen thresholds
TRAILING_1H_RETURN_THRESHOLD_BPS = -300.0
TRAILING_6H_VOL_PERCENTILE_THRESHOLD = 0.80
COOLDOWN_HOURS = 48
MIN_COVERAGE_MONTHS = 9.0
MIN_ACCEPTED_EVENTS = 300
MIN_ACCEPTED_SYMBOLS = 8
MIN_SYMBOLS_WITH_3_EVENTS = 8
MAX_SYMBOL_EVENT_SHARE = 0.20
MAX_MONTH_EVENT_SHARE = 0.25
MAX_QUARTER_EVENT_SHARE = 0.45
MIN_DISTINCT_MONTHS = 6
MIN_DISTINCT_QUARTERS = 2
VOL_LOOKBACK_DAYS = 30
VOL_MIN_HISTORY_DAYS = 14

# Status constants
STATUS_READY = "GENERIC_STRESS_PHASE0A_READY"
STATUS_UNDERPOWERED = "GENERIC_STRESS_PHASE0A_UNDERPOWERED"
STATUS_COVERAGE_FAILED = "GENERIC_STRESS_PHASE0A_COVERAGE_FAILED"
STATUS_CONCENTRATION_FAILED = "GENERIC_STRESS_PHASE0A_CONCENTRATION_FAILED"
STATUS_INVALID_INPUT = "GENERIC_STRESS_PHASE0A_ERROR_INVALID_INPUT"
STATUS_INVALID_PRECOMMITMENT = "GENERIC_STRESS_PHASE0A_ERROR_INVALID_PRECOMMITMENT"
STATUS_ARCHIVE_MISSING = "ABLATION_BLOCKED_ARCHIVE_MISSING"

ACTIVE_ARCHIVE_PATH = Path("data/hyperliquid_oi_velocity_compression_phase0")


@dataclass(frozen=True)
class StressWindowPoint:
    """A candidate point with price-only features computed."""
    timestamp: datetime
    symbol: str
    price_t: float
    trailing_1h_return_bps: float
    trailing_6h_realized_vol_bps: float
    trailing_6h_realized_vol_percentile: float


@dataclass(frozen=True)
class StressEventRecord:
    """An accepted stress event after cooldown."""
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
class SymbolAudit:
    symbol: str
    status: str = "rejected"
    first_timestamp_utc: str = ""
    last_timestamp_utc: str = ""
    usable_months: float = 0.0
    row_count: int = 0
    candidate_points: int = 0
    accepted_events_before_cooldown: int = 0
    accepted_events_after_cooldown: int = 0
    rejection_reason: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class Phase0AResult:
    summary: dict[str, Any]
    accepted_events: list[StressEventRecord]
    symbol_coverage: list[SymbolAudit]
    rejected_symbols: list[dict[str, Any]]
    warnings: list[str]
    report_dir: str | None = None


def is_altcoin_symbol(symbol: str) -> bool:
    return symbol.strip().upper() not in ALTCOIN_EXCLUDED_SYMBOLS


def _quarter_key(ts: datetime) -> str:
    return f"{ts.year}Q{((ts.month - 1) // 3) + 1}"


def _month_key(ts: datetime) -> str:
    return f"{ts.year:04d}-{ts.month:02d}"


def _event_timestamp(event: StressEventRecord) -> datetime:
    return datetime.fromisoformat(event.event_timestamp_utc.replace("Z", "+00:00"))


def compute_quarter_month_distributions(
    events: Sequence[StressEventRecord],
) -> tuple[dict[str, int], dict[str, int]]:
    quarters: dict[str, int] = {}
    months: dict[str, int] = {}
    for event in events:
        ts = _event_timestamp(event)
        q = _quarter_key(ts)
        m = _month_key(ts)
        quarters[q] = quarters.get(q, 0) + 1
        months[m] = months.get(m, 0) + 1
    return dict(sorted(quarters.items())), dict(sorted(months.items()))


def _max_distribution_share(
    distribution: dict[str, int],
    total: int,
) -> tuple[float, str | None]:
    if not distribution or total <= 0:
        return 0.0, None
    bucket, count = max(distribution.items(), key=lambda kv: (kv[1], kv[0]))
    return count / total, bucket


def git_metadata(repo_root: Path) -> tuple[str, bool]:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=repo_root, text=True,
        )
        return sha, bool(status.strip())
    except Exception:
        return "", False


def discover_archive_paths(repo_root: Path) -> list[Path]:
    active = repo_root / ACTIVE_ARCHIVE_PATH
    if active.is_dir():
        return [active]
    return []


# ------------------------------------------------------------------
# Feature computation (price-only, past-only)
# ------------------------------------------------------------------

def compute_trailing_1h_return_bps(price_t: float, price_t_minus_1h: float) -> float:
    """Compute 1h trailing return in basis points."""
    if price_t_minus_1h <= 0:
        return 0.0
    return (price_t / price_t_minus_1h - 1.0) * 10000.0


def compute_trailing_6h_realized_vol_bps(
    prices_6h: Sequence[float],
) -> float:
    """Sum of absolute 1h returns over past 6 hours in bps."""
    if len(prices_6h) < 2:
        return 0.0
    total = 0.0
    for i in range(1, len(prices_6h)):
        if prices_6h[i - 1] > 0:
            total += abs(prices_6h[i] / prices_6h[i - 1] - 1.0) * 10000.0
    return total


def compute_vol_percentile(
    current_vol: float,
    lookback_vols: Sequence[float],
) -> float:
    """Percentile rank of current_vol among lookback_vols."""
    if not lookback_vols:
        return 0.5
    count_below = sum(1 for v in lookback_vols if v < current_vol)
    return count_below / len(lookback_vols)


def build_hourly_price_series(
    rows: Sequence[ArchiveRow],
) -> dict[str, list[tuple[datetime, float]]]:
    """Build per-symbol hourly price series from archive rows."""
    series: dict[str, list[tuple[datetime, float]]] = {}
    for row in rows:
        if not is_altcoin_symbol(row.symbol):
            continue
        series.setdefault(row.symbol, []).append((row.timestamp, row.price))
    for sym in series:
        series[sym].sort(key=lambda x: x[0])
    return series


def compute_stress_points(
    symbol: str,
    price_series: list[tuple[datetime, float]],
) -> list[StressWindowPoint]:
    """Compute stress candidate points for a single symbol.

    Uses hourly grid. For each timestamp t:
    - trailing_1h_return_bps from price at t-1h
    - trailing_6h_realized_vol_bps from 6 hourly prices ending at t
    - rolling vol percentile over 30-day lookback (min 14 days history)
    """
    points: list[StressWindowPoint] = []
    n = len(price_series)
    if n < 7:
        return points

    # Precompute vol values for lookback
    vol_values: list[float] = []
    for i in range(6, n):
        prices_window = [price_series[j][1] for j in range(i - 5, i + 1)]
        vol = compute_trailing_6h_realized_vol_bps(prices_window)
        vol_values.append(vol)

    for i in range(6, n):
        ts = price_series[i][0]
        price_t = price_series[i][1]

        # 1h return
        price_1h_ago = price_series[i - 1][1]
        ret_1h = compute_trailing_1h_return_bps(price_t, price_1h_ago)

        # 6h realized vol
        prices_6h = [price_series[j][1] for j in range(i - 5, i + 1)]
        vol_6h = compute_trailing_6h_realized_vol_bps(prices_6h)

        # Vol percentile: lookback = all vol values computed before this index
        # (i.e., vol_values[0..i-7] correspond to timestamps before current)
        lookback_idx = i - 6  # 0-indexed into vol_values
        if lookback_idx <= 0:
            continue
        lookback_vols = vol_values[:lookback_idx]

        # Check minimum history: need at least 14 days of hourly data
        hours_of_history = i
        days_of_history = hours_of_history / 24.0
        if days_of_history < VOL_MIN_HISTORY_DAYS:
            continue

        vol_pctile = compute_vol_percentile(vol_6h, lookback_vols)

        points.append(
            StressWindowPoint(
                timestamp=ts,
                symbol=symbol,
                price_t=price_t,
                trailing_1h_return_bps=ret_1h,
                trailing_6h_realized_vol_bps=vol_6h,
                trailing_6h_realized_vol_percentile=vol_pctile,
            )
        )

    return points


def filter_stress_candidates(
    points: Sequence[StressWindowPoint],
) -> list[StressWindowPoint]:
    """Apply primary stress thresholds."""
    return [
        p for p in points
        if p.trailing_1h_return_bps <= TRAILING_1H_RETURN_THRESHOLD_BPS
        and p.trailing_6h_realized_vol_percentile >= TRAILING_6H_VOL_PERCENTILE_THRESHOLD
    ]


def apply_cooldown(
    candidates: Sequence[StressWindowPoint],
) -> list[StressWindowPoint]:
    """48h global cooldown, greedy from earliest."""
    accepted: list[StressWindowPoint] = []
    cooldown_until: datetime | None = None
    for point in sorted(candidates, key=lambda p: p.timestamp):
        if cooldown_until is not None and point.timestamp <= cooldown_until:
            continue
        accepted.append(point)
        cooldown_until = point.timestamp + timedelta(hours=COOLDOWN_HOURS)
    return accepted


def has_forward_24h_coverage(
    price_series: list[tuple[datetime, float]],
    event_ts: datetime,
) -> bool:
    """Check if forward 24h price data exists."""
    target = event_ts + timedelta(hours=24)
    for ts, _ in price_series:
        if ts >= target:
            # Check tolerance
            if (ts - target).total_seconds() <= 65 * 60:
                return True
            break
    return False


# ------------------------------------------------------------------
# Precommitment validation
# ------------------------------------------------------------------

def validate_precommitment(
    precommitment_path: Path,
) -> tuple[bool, str, list[str]]:
    """Validate precommitment document hash and frozen values."""
    warnings: list[str] = []
    try:
        actual = compute_precommitment_hash(precommitment_path)
        with precommitment_path.open("r", encoding="utf-8") as f:
            recorded = ""
            for line in f:
                if line.startswith("Precommitment SHA-256 (self):"):
                    recorded = line.split(":", 1)[1].strip()
    except Exception as exc:
        return False, "", [f"precommitment hash read failed: {exc}"]
    if actual != recorded:
        return False, actual, [f"precommitment hash mismatch actual={actual} recorded={recorded}"]
    text = precommitment_path.read_text(encoding="utf-8")
    required_snippets = [
        "<= -300 bps",
        ">= 0.80",
        "Cooldown: 48h",
        "at least 300",
        "at least 8",
        "no more than 20%",
        "no more than 25%",
        "no more than 45%",
    ]
    missing = [s for s in required_snippets if s not in text]
    if missing:
        return False, actual, [f"precommitment missing frozen value: {s}" for s in missing]
    return True, actual, warnings


# ------------------------------------------------------------------
# Symbol coverage and audit
# ------------------------------------------------------------------

def validate_symbol_coverage(
    symbol: str,
    price_series: list[tuple[datetime, float]],
) -> SymbolAudit:
    """Check symbol has sufficient coverage."""
    audit = SymbolAudit(symbol=symbol)
    if not price_series:
        audit.rejection_reason = "no_usable_rows"
        return audit
    audit.row_count = len(price_series)
    audit.first_timestamp_utc = utc_iso(price_series[0][0])
    audit.last_timestamp_utc = utc_iso(price_series[-1][0])
    span_seconds = (price_series[-1][0] - price_series[0][0]).total_seconds()
    audit.usable_months = span_seconds / (86400.0 * 30.4375)
    if audit.usable_months < MIN_COVERAGE_MONTHS:
        audit.rejection_reason = f"coverage_less_than_{MIN_COVERAGE_MONTHS}_months"
        return audit
    audit.status = "accepted"
    return audit


# ------------------------------------------------------------------
# Main audit
# ------------------------------------------------------------------

def determine_status(
    clean_symbol_count: int,
    event_count: int,
    symbols_with_3: int,
    max_symbol_share: float,
    max_month_share: float,
    max_quarter_share: float,
    distinct_months: int,
    distinct_quarters: int,
    invalid_input: bool,
    invalid_precommitment: bool,
) -> tuple[str, str]:
    if invalid_precommitment:
        return STATUS_INVALID_PRECOMMITMENT, "invalid precommitment self-check or frozen constants"
    if invalid_input:
        return STATUS_INVALID_INPUT, "invalid input archive"
    if clean_symbol_count < MIN_ACCEPTED_SYMBOLS:
        return STATUS_COVERAGE_FAILED, f"fewer than {MIN_ACCEPTED_SYMBOLS} symbols with clean usable coverage"
    if event_count < MIN_ACCEPTED_EVENTS or symbols_with_3 < MIN_SYMBOLS_WITH_3_EVENTS:
        return STATUS_UNDERPOWERED, f"accepted events {event_count} below {MIN_ACCEPTED_EVENTS} or fewer than {MIN_SYMBOLS_WITH_3_EVENTS} symbols with 3+ events"
    if max_symbol_share > MAX_SYMBOL_EVENT_SHARE:
        return STATUS_CONCENTRATION_FAILED, f"max symbol event share {max_symbol_share:.3f} exceeds {MAX_SYMBOL_EVENT_SHARE}"
    if max_month_share > MAX_MONTH_EVENT_SHARE:
        return STATUS_CONCENTRATION_FAILED, f"max month event share {max_month_share:.3f} exceeds {MAX_MONTH_EVENT_SHARE}"
    if max_quarter_share > MAX_QUARTER_EVENT_SHARE:
        return STATUS_CONCENTRATION_FAILED, f"max quarter event share {max_quarter_share:.3f} exceeds {MAX_QUARTER_EVENT_SHARE}"
    if distinct_months < MIN_DISTINCT_MONTHS or distinct_quarters < MIN_DISTINCT_QUARTERS:
        return STATUS_COVERAGE_FAILED, f"insufficient distinct months ({distinct_months}) or quarters ({distinct_quarters})"
    return STATUS_READY, ""


def run_phase0a_audit(
    rows: Sequence[ArchiveRow],
    diagnostics: LoadDiagnostics,
    precommitment_path: Path,
    generated_at: datetime | None = None,
    repo_root: Path | None = None,
    archive_source_path: str = "",
) -> Phase0AResult:
    """Run the full Phase 0A audit on loaded archive rows."""
    generated_at = generated_at or datetime.now(UTC)
    repo_root = repo_root or Path.cwd()
    pre_ok, pre_hash, pre_warnings = validate_precommitment(precommitment_path)
    invalid_precommitment = not pre_ok
    invalid_input = False
    if diagnostics.total_raw_rows == 0 and len(rows) == 0:
        invalid_input = True
    if diagnostics.total_raw_rows > 0 and diagnostics.field_validation_failures / diagnostics.total_raw_rows > 0.50:
        invalid_input = True
    if diagnostics.total_raw_rows > 0 and diagnostics.timestamp_parse_failures == diagnostics.total_raw_rows:
        invalid_input = True

    # Filter to altcoins only
    alt_rows = [r for r in rows if is_altcoin_symbol(r.symbol)]

    # Build price series per symbol
    price_series = build_hourly_price_series(alt_rows)

    # Evaluate each symbol
    coverage: list[SymbolAudit] = []
    all_candidates_before_cooldown: list[StressWindowPoint] = []
    all_candidates_after_cooldown: list[StressWindowPoint] = []
    warnings = list(pre_warnings)

    for symbol in sorted(price_series):
        series = price_series[symbol]
        audit = validate_symbol_coverage(symbol, series)
        if audit.status == "accepted":
            points = compute_stress_points(symbol, series)
            audit.candidate_points = len(points)
            candidates = filter_stress_candidates(points)
            audit.accepted_events_before_cooldown = len(candidates)
            # Check forward 24h coverage
            candidates_with_coverage = [
                p for p in candidates
                if has_forward_24h_coverage(series, p.timestamp)
            ]
            after_cooldown = apply_cooldown(candidates_with_coverage)
            audit.accepted_events_after_cooldown = len(after_cooldown)
            all_candidates_before_cooldown.extend(candidates)
            all_candidates_after_cooldown.extend(after_cooldown)
        coverage.append(audit)

    clean_symbols = sum(1 for a in coverage if a.status == "accepted")

    # Event stats
    event_count = len(all_candidates_after_cooldown)
    by_symbol: dict[str, int] = {}
    for point in all_candidates_after_cooldown:
        by_symbol[point.symbol] = by_symbol.get(point.symbol, 0) + 1
    max_symbol_share = 0.0
    max_symbol_name = ""
    if by_symbol:
        max_sym, max_cnt = max(by_symbol.items(), key=lambda kv: (kv[1], kv[0]))
        max_symbol_share = max_cnt / event_count if event_count else 0.0
        max_symbol_name = max_sym
    symbols_with_3 = sum(1 for n in by_symbol.values() if n >= 3)

    # Quarter/month distributions
    # Build temp event records for distribution computation
    temp_events: list[StressEventRecord] = []
    for i, point in enumerate(all_candidates_after_cooldown):
        event_id = f"{point.symbol}_{point.timestamp.strftime('%Y%m%dT%H%M%SZ')}"
        temp_events.append(
            StressEventRecord(
                event_id=event_id,
                symbol=point.symbol,
                event_timestamp_utc=utc_iso(point.timestamp),
                detector_family=STUDY_ID,
                direction="long",
                price_t=point.price_t,
                trailing_1h_return_bps=point.trailing_1h_return_bps,
                trailing_6h_realized_vol_bps=point.trailing_6h_realized_vol_bps,
                trailing_6h_realized_vol_percentile=point.trailing_6h_realized_vol_percentile,
                cooldown_key=point.symbol,
                archive_source_path=archive_source_path,
            )
        )

    quarter_dist, month_dist = compute_quarter_month_distributions(temp_events)
    max_quarter_share, max_quarter = _max_distribution_share(quarter_dist, event_count)
    max_month_share, max_month = _max_distribution_share(month_dist, event_count)
    distinct_quarters = sum(1 for v in quarter_dist.values() if v > 0)
    distinct_months = sum(1 for v in month_dist.values() if v > 0)

    status, locked_reason = determine_status(
        clean_symbols,
        event_count,
        symbols_with_3,
        max_symbol_share,
        max_month_share,
        max_quarter_share,
        distinct_months,
        distinct_quarters,
        invalid_input,
        invalid_precommitment,
    )
    unlocks = status == STATUS_READY

    archive_times = [r.timestamp for r in rows]
    archive_start = min(archive_times) if archive_times else None
    archive_end = max(archive_times) if archive_times else None
    archive_end_age_days = (generated_at - archive_end).days if archive_end else None
    git_sha, git_dirty = git_metadata(repo_root)

    summary = {
        "study_id": STUDY_ID,
        "stage": STAGE,
        "venue": VENUE,
        "status": status,
        "unlocks_phase0b": unlocks,
        "phase0b_locked_reason": locked_reason,
        "detector_family": "generic_altcoin_stress_regime_ablation",
        "trailing_1h_return_threshold_bps": TRAILING_1H_RETURN_THRESHOLD_BPS,
        "trailing_6h_vol_percentile_threshold": TRAILING_6H_VOL_PERCENTILE_THRESHOLD,
        "cooldown_hours": COOLDOWN_HOURS,
        "total_symbols_requested": len(price_series),
        "total_symbols_accepted": clean_symbols,
        "total_symbols_rejected": len(coverage) - clean_symbols,
        "accepted_event_count_before_cooldown": len(all_candidates_before_cooldown),
        "accepted_event_count_after_cooldown": event_count,
        "symbols_with_at_least_3_events": symbols_with_3,
        "max_symbol_event_share": max_symbol_share,
        "max_symbol_event_share_symbol": max_symbol_name,
        "max_calendar_quarter_event_share": max_quarter_share,
        "max_calendar_quarter": max_quarter,
        "max_calendar_month_event_share": max_month_share,
        "max_calendar_month": max_month,
        "distinct_months_with_events": distinct_months,
        "distinct_quarters_with_events": distinct_quarters,
        "quarter_distribution": quarter_dist,
        "month_distribution": month_dist,
        "archive_start_utc": utc_iso(archive_start) if archive_start else None,
        "archive_end_utc": utc_iso(archive_end) if archive_end else None,
        "archive_end_age_days": archive_end_age_days,
        "generated_at_utc": utc_iso(generated_at),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "precommitment_sha256": pre_hash,
        "archive_source_path": archive_source_path,
        "data_source_summary": {
            "total_raw_rows": diagnostics.total_raw_rows,
            "loaded_rows": diagnostics.loaded_rows,
            "field_validation_failures": diagnostics.field_validation_failures,
            "timestamp_parse_failures": diagnostics.timestamp_parse_failures,
        },
        "safety_mode": SAFETY_MODE,
        "btc_eth_excluded": True,
        "price_only_detector": True,
    }

    # Build final event records
    final_events: list[StressEventRecord] = []
    cooldown_group = 0
    for point in all_candidates_after_cooldown:
        cooldown_group += 1
        event_id = f"{point.symbol}_{point.timestamp.strftime('%Y%m%dT%H%M%SZ')}"
        final_events.append(
            StressEventRecord(
                event_id=event_id,
                symbol=point.symbol,
                event_timestamp_utc=utc_iso(point.timestamp),
                detector_family=STUDY_ID,
                direction="long",
                price_t=point.price_t,
                trailing_1h_return_bps=point.trailing_1h_return_bps,
                trailing_6h_realized_vol_bps=point.trailing_6h_realized_vol_bps,
                trailing_6h_realized_vol_percentile=point.trailing_6h_realized_vol_percentile,
                cooldown_key=point.symbol,
                archive_source_path=archive_source_path,
            )
        )

    rejected = [
        {
            "symbol": a.symbol,
            "rejection_reason": a.rejection_reason,
            "first_timestamp_utc": a.first_timestamp_utc,
            "last_timestamp_utc": a.last_timestamp_utc,
            "usable_months": a.usable_months,
            "row_count": a.row_count,
        }
        for a in coverage
        if a.status != "accepted"
    ]

    return Phase0AResult(summary, final_events, coverage, rejected, warnings)


# ------------------------------------------------------------------
# Report artifacts
# ------------------------------------------------------------------

def accepted_event_json(event: StressEventRecord) -> dict[str, Any]:
    """Convert event to JSON-serializable dict for Phase 0B/0C compatibility."""
    return {
        "event_id": event.event_id,
        "symbol": event.symbol,
        "event_timestamp_utc": event.event_timestamp_utc,
        "event_direction": "downside_liquidation_flush",
        "flush_side": "long_wipe",
        "detector_family": event.detector_family,
        "direction": event.direction,
        "price_t": event.price_t,
        "trailing_1h_return_bps": event.trailing_1h_return_bps,
        "trailing_6h_realized_vol_bps": event.trailing_6h_realized_vol_bps,
        "trailing_6h_realized_vol_percentile": event.trailing_6h_realized_vol_percentile,
        "cooldown_key": event.cooldown_key,
        "archive_source_path": event.archive_source_path,
    }


def accepted_event_json_rows(
    events: Sequence[StressEventRecord],
) -> list[dict[str, Any]]:
    return [
        accepted_event_json(e)
        for e in sorted(
            events,
            key=lambda e: (e.event_timestamp_utc, e.symbol, e.event_id),
        )
    ]


def summary_markdown(result: Phase0AResult) -> str:
    s = result.summary
    warnings_text = "\n".join(f"- {w}" for w in result.warnings) if result.warnings else "- none"
    return f"""# Generic altcoin stress regime ablation Phase 0A summary

Status: `{s['status']}`

Unlocks Phase 0B: `{s['unlocks_phase0b']}`

Locked reason: {s['phase0b_locked_reason'] or 'none'}

Precommitment SHA-256: `{s['precommitment_sha256']}`

Archive source path: `{s['archive_source_path']}`

Accepted events after cooldown: {s['accepted_event_count_after_cooldown']}

Max symbol event share: {s['max_symbol_event_share']} ({s['max_symbol_event_share_symbol']})

Max calendar quarter event share: {s['max_calendar_quarter_event_share']} ({s['max_calendar_quarter']})

Max calendar month event share: {s['max_calendar_month_event_share']} ({s['max_calendar_month']})

## Scope

Price-only generic altcoin stress detector. No OI, liquidation, or funding inputs. No returns, PnL, null tests, FDR, live execution, paper trading, shadow execution, orders, private keys, or trading auth were used.

## Distribution consistency

Quarter distribution: `{s['quarter_distribution']}`

Month distribution: `{s['month_distribution']}`

## Diagnostic warnings

{warnings_text}
"""


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    tmp.replace(path)


def _write_jsonl_with_hash(path: Path, rows: Sequence[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows]
    payload = "".join(lines).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)
    return hashlib.sha256(payload).hexdigest()


def write_report_artifacts(
    result: Phase0AResult,
    report_dir: Path,
) -> Phase0AResult:
    report_dir.mkdir(parents=True, exist_ok=True)
    event_rows = accepted_event_json_rows(result.accepted_events)
    event_hash = _write_jsonl_with_hash(report_dir / "accepted_events.jsonl", event_rows)
    result.summary["accepted_events_jsonl_path"] = "accepted_events.jsonl"
    result.summary["accepted_events_jsonl_sha256"] = event_hash
    result.summary["accepted_events_jsonl_count"] = len(event_rows)
    atomic_write_json(report_dir / "summary.json", result.summary)
    atomic_write_text(report_dir / "summary.md", summary_markdown(result))
    event_fields = [
        "event_id", "symbol", "event_timestamp_utc", "event_direction",
        "detector_family", "direction", "price_t",
        "trailing_1h_return_bps", "trailing_6h_realized_vol_bps",
        "trailing_6h_realized_vol_percentile", "cooldown_key",
    ]
    _write_csv(
        report_dir / "accepted_events.csv",
        event_fields,
        [
            {
                "event_id": e.event_id,
                "symbol": e.symbol,
                "event_timestamp_utc": e.event_timestamp_utc,
                "event_direction": "downside_liquidation_flush",
                "detector_family": e.detector_family,
                "direction": e.direction,
                "price_t": e.price_t,
                "trailing_1h_return_bps": e.trailing_1h_return_bps,
                "trailing_6h_realized_vol_bps": e.trailing_6h_realized_vol_bps,
                "trailing_6h_realized_vol_percentile": e.trailing_6h_realized_vol_percentile,
                "cooldown_key": e.cooldown_key,
            }
            for e in result.accepted_events
        ],
    )
    _write_csv(
        report_dir / "symbol_coverage.csv",
        [
            "symbol", "status", "first_timestamp_utc", "last_timestamp_utc",
            "usable_months", "row_count", "candidate_points",
            "accepted_events_before_cooldown", "accepted_events_after_cooldown",
            "rejection_reason",
        ],
        [
            {
                "symbol": a.symbol,
                "status": a.status,
                "first_timestamp_utc": a.first_timestamp_utc,
                "last_timestamp_utc": a.last_timestamp_utc,
                "usable_months": a.usable_months,
                "row_count": a.row_count,
                "candidate_points": a.candidate_points,
                "accepted_events_before_cooldown": a.accepted_events_before_cooldown,
                "accepted_events_after_cooldown": a.accepted_events_after_cooldown,
                "rejection_reason": a.rejection_reason,
            }
            for a in result.symbol_coverage
        ],
    )
    _write_csv(
        report_dir / "rejected_symbols.csv",
        ["symbol", "rejection_reason", "first_timestamp_utc", "last_timestamp_utc", "usable_months", "row_count"],
        result.rejected_symbols,
    )
    result.report_dir = str(report_dir)
    return result


def run_from_archive_paths(
    archive_paths: Sequence[Path],
    precommitment_path: Path,
    repo_root: Path,
    out_dir: Path | None = None,
) -> Phase0AResult:
    if not archive_paths:
        result = Phase0AResult(
            {
                "study_id": STUDY_ID,
                "stage": STAGE,
                "status": STATUS_ARCHIVE_MISSING,
                "unlocks_phase0b": False,
                "phase0b_locked_reason": "no archive paths found",
                "accepted_event_count_after_cooldown": 0,
            },
            [], [], [], [],
        )
    else:
        rows, diagnostics = load_archive_rows(archive_paths)
        result = run_phase0a_audit(
            rows,
            diagnostics,
            precommitment_path,
            repo_root=repo_root,
            archive_source_path=":".join(str(p) for p in archive_paths),
        )
    if out_dir is not None:
        result = write_report_artifacts(result, out_dir)
    return result


__all__ = [
    "ACTIVE_ARCHIVE_PATH",
    "ALTCOIN_EXCLUDED_SYMBOLS",
    "COOLDOWN_HOURS",
    "MAX_MONTH_EVENT_SHARE",
    "MAX_QUARTER_EVENT_SHARE",
    "MAX_SYMBOL_EVENT_SHARE",
    "MIN_ACCEPTED_EVENTS",
    "MIN_ACCEPTED_SYMBOLS",
    "MIN_SYMBOLS_WITH_3_EVENTS",
    "STATUS_ARCHIVE_MISSING",
    "STATUS_CONCENTRATION_FAILED",
    "STATUS_COVERAGE_FAILED",
    "STATUS_READY",
    "STATUS_UNDERPOWERED",
    "TRAILING_1H_RETURN_THRESHOLD_BPS",
    "TRAILING_6H_VOL_PERCENTILE_THRESHOLD",
    "StressEventRecord",
    "StressWindowPoint",
    "SymbolAudit",
    "Phase0AResult",
    "accepted_event_json",
    "accepted_event_json_rows",
    "apply_cooldown",
    "compute_quarter_month_distributions",
    "compute_stress_points",
    "compute_trailing_1h_return_bps",
    "compute_trailing_6h_realized_vol_bps",
    "compute_vol_percentile",
    "discover_archive_paths",
    "filter_stress_candidates",
    "git_metadata",
    "has_forward_24h_coverage",
    "is_altcoin_symbol",
    "run_from_archive_paths",
    "run_phase0a_audit",
    "validate_precommitment",
    "validate_symbol_coverage",
    "write_report_artifacts",
]
