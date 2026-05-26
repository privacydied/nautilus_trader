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
import multiprocessing
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import orjson

    def _loads_json_line(line: bytes | str) -> dict[str, Any]:
        """Parse a JSON line with orjson (bytes) or stdlib fallback."""
        if isinstance(line, bytes):
            return orjson.loads(line)
        return orjson.loads(line.encode("utf-8"))

    HAS_ORJSON = True
except ImportError:
    import json

    def _loads_json_line(line: bytes | str) -> dict[str, Any]:
        """Parse a JSON line with stdlib json."""
        if isinstance(line, bytes):
            return json.loads(line.decode("utf-8"))
        return json.loads(line)

    HAS_ORJSON = False

import numpy as np

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    ArchiveRow,
    LoadDiagnostics,
    compute_precommitment_hash,
    normalize_raw_row,
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

# Default worker count
_DEFAULT_WORKERS = max(min(os.cpu_count() or 1, 8), 1)


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


# ------------------------------------------------------------------
# Per-symbol worker result
# ------------------------------------------------------------------


@dataclass
class SymbolWorkerResult:
    """Result from a per-symbol worker process."""
    symbol: str
    status: str  # "accepted" or "rejected"
    rejection_reason: str = ""
    price_series_count: int = 0
    first_timestamp_utc: str = ""
    last_timestamp_utc: str = ""
    usable_months: float = 0.0
    candidate_points: int = 0
    events_before_cooldown: list[StressWindowPoint] = field(default_factory=list)
    events_after_cooldown: list[StressWindowPoint] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    archive_path: str = ""


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
# JSONL file reader (orjson-accelerated)
# ------------------------------------------------------------------


def _read_jsonl_lines(file_path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file and return parsed rows using orjson."""
    raw_lines: list[dict[str, Any]] = []
    with file_path.open("rb") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw_lines.append(_loads_json_line(stripped))
            except Exception:
                continue
    return raw_lines


def discover_jsonl_files(root: Path) -> list[Path]:
    """Discover JSONL files in an archive directory."""
    if not root.is_dir():
        return []
    files: list[Path] = []
    for child in sorted(root.iterdir()):
        if child.suffix.lower() == ".jsonl" and child.is_file():
            files.append(child)
    return files


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


# ------------------------------------------------------------------
# Numpy-accelerated rolling feature computation
# ------------------------------------------------------------------


def compute_stress_points_vectorized(
    symbol: str,
    price_series: list[tuple[datetime, float]],
) -> list[StressWindowPoint]:
    """Compute stress candidate points using numpy for rolling ops.

    Matches compute_stress_points semantics exactly, but uses
    vectorized operations where safe.
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

    # Vectorized 6h realized vol: sum of abs returns over sliding window of 6
    abs_returns = np.abs(returns_1h)
    vol_6h = np.full(n, 0.0, dtype=np.float64)
    for i in range(6, n):
        vol_6h[i] = np.sum(abs_returns[i - 5 : i + 1])

    # Rolling percentile (sequential, not vectorizable with simple numpy)
    # But we use numpy for the per-point percentile lookup
    points: list[StressWindowPoint] = []

    for i in range(6, n):
        ts = timestamps[i]
        price_t = prices[i]
        ret = returns_1h[i]
        vol = vol_6h[i]

        # Lookback = all vol values before this index (past-only)
        lookback_count = i - 6
        if lookback_count <= 0:
            continue

        # Check minimum history
        if i < VOL_MIN_HISTORY_DAYS * 24:
            continue

        # Percentile rank: count values < vol / total
        lookback_vols = vol_6h[6:i]  # vol_6h[6] is first valid, up to i-1
        count_below = np.sum(lookback_vols < vol)
        vol_pctile = count_below / len(lookback_vols) if len(lookback_vols) > 0 else 0.5

        points.append(
            StressWindowPoint(
                timestamp=ts,
                symbol=symbol,
                price_t=price_t,
                trailing_1h_return_bps=ret,
                trailing_6h_realized_vol_bps=vol,
                trailing_6h_realized_vol_percentile=vol_pctile,
            )
        )

    return points


def compute_stress_points(
    symbol: str,
    price_series: list[tuple[datetime, float]],
) -> list[StressWindowPoint]:
    """Compute stress candidate points for a single symbol.

    Uses hourly grid. For each timestamp t:
    - trailing_1h_return_bps from price at t-1h
    - trailing_6h_realized_vol_bps from 6 hourly prices ending at t
    - rolling vol percentile over 30-day lookback (min 14 days history)

    Uses numpy-accelerated implementation.
    """
    return compute_stress_points_vectorized(symbol, price_series)


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


# ------------------------------------------------------------------
# Per-symbol worker (multiprocessing-safe)
# ------------------------------------------------------------------


def _worker_args_setup() -> tuple:
    """Per-process import guard for multiprocessing workers.

    Returns (STUDY_ID, ALTCOIN_EXCLUDED_SYMBOLS, thresholds).
    """
    return (
        STUDY_ID,
        ALTCOIN_EXCLUDED_SYMBOLS,
        TRAILING_1H_RETURN_THRESHOLD_BPS,
        TRAILING_6H_VOL_PERCENTILE_THRESHOLD,
        COOLDOWN_HOURS,
        VOL_MIN_HISTORY_DAYS,
    )


def _process_one_file(
    file_path: str,
    archive_source: str,
) -> SymbolWorkerResult:
    """Process a single JSONL file and return symbol-level results.

    This is the multiprocessing worker entry point.
    """
    path = Path(file_path)
    symbol = path.stem.strip().upper()

    if not is_altcoin_symbol(symbol):
        return SymbolWorkerResult(
            symbol=symbol,
            status="rejected",
            rejection_reason="excluded_symbol_btc_or_eth",
            archive_path=file_path,
        )

    # Read and parse with orjson
    raw_rows = _read_jsonl_lines(path)
    if not raw_rows:
        return SymbolWorkerResult(
            symbol=symbol,
            status="rejected",
            rejection_reason="no_parseable_rows",
            archive_path=file_path,
        )

    # Normalize rows to ArchiveRow
    archive_rows: list[ArchiveRow] = []
    order = 0
    for raw in raw_rows:
        row, reason = normalize_raw_row(dict(raw), path, order)
        order += 1
        if row is None:
            continue
        archive_rows.append(row)

    if not archive_rows:
        return SymbolWorkerResult(
            symbol=symbol,
            status="rejected",
            rejection_reason="no_valid_rows",
            archive_path=file_path,
        )

    # Build price series (sorted by time)
    series: list[tuple[datetime, float]] = sorted(
        [(r.timestamp, r.price) for r in archive_rows],
        key=lambda x: x[0],
    )

    # Coverage check
    n = len(series)
    first_ts = utc_iso(series[0][0])
    last_ts = utc_iso(series[-1][0])
    span_seconds = (series[-1][0] - series[0][0]).total_seconds()
    usable_months = span_seconds / (86400.0 * 30.4375)

    if usable_months < MIN_COVERAGE_MONTHS:
        return SymbolWorkerResult(
            symbol=symbol,
            status="rejected",
            rejection_reason=f"coverage_less_than_{MIN_COVERAGE_MONTHS}_months",
            price_series_count=n,
            first_timestamp_utc=first_ts,
            last_timestamp_utc=last_ts,
            usable_months=usable_months,
            archive_path=file_path,
        )

    # Compute stress points using accelerated implementation
    points = compute_stress_points_vectorized(symbol, series)

    # Filter candidates
    candidates = filter_stress_candidates(points)
    events_before = len(candidates)

    # Forward coverage check
    candidates_with_coverage = [
        p for p in candidates
        if has_forward_24h_coverage(series, p.timestamp)
    ]

    # Apply cooldown (per-symbol, safe in worker)
    after_cooldown = apply_cooldown(candidates_with_coverage)

    return SymbolWorkerResult(
        symbol=symbol,
        status="accepted",
        price_series_count=n,
        first_timestamp_utc=first_ts,
        last_timestamp_utc=last_ts,
        usable_months=usable_months,
        candidate_points=len(points),
        events_before_cooldown=list(points),  # all points for diagnostics
        events_after_cooldown=after_cooldown,
        archive_path=file_path,
    )


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
    """Run the full Phase 0A audit on loaded archive rows (legacy path)."""
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
    series: dict[str, list[tuple[datetime, float]]] = {}
    for row in alt_rows:
        series.setdefault(row.symbol, []).append((row.timestamp, row.price))
    for sym in series:
        series[sym].sort(key=lambda x: x[0])

    # Evaluate each symbol
    coverage: list[SymbolAudit] = []
    all_candidates_before_cooldown: list[StressWindowPoint] = []
    all_candidates_after_cooldown: list[StressWindowPoint] = []
    warnings = list(pre_warnings)

    for symbol in sorted(series):
        ser = series[symbol]
        audit = validate_symbol_coverage(symbol, ser)
        if audit.status == "accepted":
            points = compute_stress_points(symbol, ser)
            audit.candidate_points = len(points)
            candidates = filter_stress_candidates(points)
            audit.accepted_events_before_cooldown = len(candidates)
            candidates_with_coverage = [
                p for p in candidates
                if has_forward_24h_coverage(ser, p.timestamp)
            ]
            after_cooldown = apply_cooldown(candidates_with_coverage)
            audit.accepted_events_after_cooldown = len(after_cooldown)
            all_candidates_before_cooldown.extend(candidates)
            all_candidates_after_cooldown.extend(after_cooldown)
        coverage.append(audit)

    clean_symbols = sum(1 for a in coverage if a.status == "accepted")
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
        "total_symbols_requested": len(series),
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

    final_events: list[StressEventRecord] = []
    for point in all_candidates_after_cooldown:
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
        "event_direction": "downside_price_drop",
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

{warnings_text}"""


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
                "event_direction": "downside_price_drop",
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


# ------------------------------------------------------------------
# Worker pool
# ------------------------------------------------------------------


def _merge_worker_results(workers: list[SymbolWorkerResult]) -> tuple[
    list[SymbolWorkerResult],
    list[StressWindowPoint],
    list[SymbolAudit],
]:
    """Merge per-symbol worker results into global lists."""
    all_accepted: list[StressWindowPoint] = []
    coverages: list[SymbolAudit] = []

    for wr in workers:
        audit = SymbolAudit(
            symbol=wr.symbol,
            status=wr.status,
            first_timestamp_utc=wr.first_timestamp_utc,
            last_timestamp_utc=wr.last_timestamp_utc,
            usable_months=wr.usable_months,
            row_count=wr.price_series_count,
            candidate_points=wr.candidate_points,
            accepted_events_before_cooldown=len(wr.events_before_cooldown),
            accepted_events_after_cooldown=len(wr.events_after_cooldown),
            rejection_reason=wr.rejection_reason,
            warnings=list(wr.warnings),
        )
        coverages.append(audit)

        if wr.status == "accepted":
            all_accepted.extend(wr.events_after_cooldown)

    # Sort by timestamp for deterministic output
    all_accepted.sort(key=lambda p: p.timestamp)
    return workers, all_accepted, coverages


def run_from_archive_paths(
    archive_paths: Sequence[Path],
    precommitment_path: Path,
    repo_root: Path,
    out_dir: Path | None = None,
    workers: int | None = None,
) -> Phase0AResult:
    """Run the full Phase 0A audit using multiprocessing workers.

    Args:
        archive_paths: Paths to archive directories.
        precommitment_path: Path to precommitment doc.
        repo_root: Repository root for git metadata.
        out_dir: Output report directory.
        workers: Number of parallel processes. Defaults to min(cpu_count, 8).
            Pass 1 for deterministic single-process mode.
    """
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
        if out_dir is not None:
            result = write_report_artifacts(result, out_dir)
        return result

    num_workers = workers if workers is not None else _DEFAULT_WORKERS
    # Clamp workers
    if num_workers < 1:
        num_workers = 1

    # Precommitment validation
    pre_ok, pre_hash, pre_warnings = validate_precommitment(precommitment_path)
    invalid_precommitment = not pre_ok
    warnings = list(pre_warnings)
    generated_at = datetime.now(UTC)
    git_sha, git_dirty = git_metadata(repo_root)

    # Discover JSONL files
    file_paths: list[Path] = []
    for ap in archive_paths:
        file_paths.extend(discover_jsonl_files(ap))

    if not file_paths:
        # No files found
        result = Phase0AResult(
            summary={
                "study_id": STUDY_ID,
                "stage": STAGE,
                "status": STATUS_ARCHIVE_MISSING,
                "unlocks_phase0b": False,
                "phase0b_locked_reason": "no jsonl files found in archive",
                "accepted_event_count_after_cooldown": 0,
                "precommitment_sha256": pre_hash,
                "generated_at_utc": utc_iso(generated_at),
                "git_sha": git_sha,
                "git_dirty": git_dirty,
                "btc_eth_excluded": True,
                "price_only_detector": True,
                "safety_mode": SAFETY_MODE,
                "orjson_available": HAS_ORJSON,
                "workers": num_workers,
            },
            accepted_events=[],
            symbol_coverage=[],
            rejected_symbols=[],
            warnings=warnings,
        )
        if out_dir is not None:
            result = write_report_artifacts(result, out_dir)
        return result

    archive_source = ":".join(str(p) for p in archive_paths)
    total_files = len(file_paths)

    start_time = time.monotonic()

    # Run workers
    worker_args = [
        (str(fp), archive_source)
        for fp in file_paths
    ]

    if num_workers == 1:
        # Single-process mode (deterministic, good for tests/debug)
        worker_results: list[SymbolWorkerResult] = []
        for idx, (file_path_str, source) in enumerate(worker_args):
            wr = _process_one_file(file_path_str, source)
            worker_results.append(wr)
            elapsed = time.monotonic() - start_time
            print(
                f"GENERIC_STRESS_PHASE0A_PROGRESS "
                f"completed={idx + 1}/{total_files} "
                f"symbol={wr.symbol} "
                f"status={wr.status} "
                f"rows={wr.price_series_count} "
                f"candidates={wr.candidate_points} "
                f"accepted={len(wr.events_after_cooldown)} "
                f"elapsed_sec={elapsed:.1f}",
                flush=True,
            )
    else:
        # Multiprocessing mode
        with multiprocessing.Pool(processes=num_workers) as pool:
            results_iter = pool.starmap(_process_one_file, worker_args)
            worker_results = []
            for idx, wr in enumerate(results_iter):
                worker_results.append(wr)
                elapsed = time.monotonic() - start_time
                print(
                    f"GENERIC_STRESS_PHASE0A_PROGRESS "
                    f"completed={idx + 1}/{total_files} "
                    f"symbol={wr.symbol} "
                    f"status={wr.status} "
                    f"rows={wr.price_series_count} "
                    f"candidates={wr.candidate_points} "
                    f"accepted={len(wr.events_after_cooldown)} "
                    f"elapsed_sec={elapsed:.1f}",
                    flush=True,
                )

    total_elapsed = time.monotonic() - start_time

    # Sort workers by symbol for deterministic merge
    worker_results.sort(key=lambda w: w.symbol)

    # Merge results
    _, all_accepted, coverages = _merge_worker_results(worker_results)

    clean_symbols = sum(1 for a in coverages if a.status == "accepted")
    event_count = len(all_accepted)

    # Event stats
    by_symbol: dict[str, int] = {}
    for point in all_accepted:
        by_symbol[point.symbol] = by_symbol.get(point.symbol, 0) + 1
    max_symbol_share = 0.0
    max_symbol_name = ""
    if by_symbol:
        max_sym, max_cnt = max(by_symbol.items(), key=lambda kv: (kv[1], kv[0]))
        max_symbol_share = max_cnt / event_count if event_count else 0.0
        max_symbol_name = max_sym
    symbols_with_3 = sum(1 for n in by_symbol.values() if n >= 3)

    # Build temp event records for distribution
    temp_events: list[StressEventRecord] = []
    for point in all_accepted:
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
                archive_source_path=archive_source,
            )
        )

    quarter_dist, month_dist = compute_quarter_month_distributions(temp_events)
    max_quarter_share, max_quarter = _max_distribution_share(quarter_dist, event_count)
    max_month_share, max_month = _max_distribution_share(month_dist, event_count)
    distinct_quarters = sum(1 for v in quarter_dist.values() if v > 0)
    distinct_months = sum(1 for v in month_dist.values() if v > 0)

    # Track IO diagnostics from worker results
    total_raw_rows = sum(wr.price_series_count for wr in worker_results if wr.status == "accepted")

    status, locked_reason = determine_status(
        clean_symbols,
        event_count,
        symbols_with_3,
        max_symbol_share,
        max_month_share,
        max_quarter_share,
        distinct_months,
        distinct_quarters,
        invalid_input=False,
        invalid_precommitment=invalid_precommitment,
    )

    summary = {
        "study_id": STUDY_ID,
        "stage": STAGE,
        "venue": VENUE,
        "status": status,
        "unlocks_phase0b": status == STATUS_READY,
        "phase0b_locked_reason": locked_reason,
        "detector_family": "generic_altcoin_stress_regime_ablation",
        "trailing_1h_return_threshold_bps": TRAILING_1H_RETURN_THRESHOLD_BPS,
        "trailing_6h_vol_percentile_threshold": TRAILING_6H_VOL_PERCENTILE_THRESHOLD,
        "cooldown_hours": COOLDOWN_HOURS,
        "total_symbols_requested": len(coverages),
        "total_symbols_accepted": clean_symbols,
        "total_symbols_rejected": len(coverages) - clean_symbols,
        "accepted_event_count_before_cooldown": sum(len(wr.events_before_cooldown) for wr in worker_results if wr.status == "accepted"),
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
        "generated_at_utc": utc_iso(generated_at),
        "git_sha": git_sha,
        "git_dirty": git_dirty,
        "precommitment_sha256": pre_hash,
        "archive_source_path": archive_source,
        "data_source_summary": {
            "total_symbol_files": total_files,
            "total_raw_rows": total_raw_rows,
        },
        "safety_mode": SAFETY_MODE,
        "btc_eth_excluded": True,
        "price_only_detector": True,
        "orjson_available": HAS_ORJSON,
        "workers": num_workers,
        "total_elapsed_sec": round(total_elapsed, 1),
    }

    # Build final event records
    final_events: list[StressEventRecord] = []
    for point in all_accepted:
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
                archive_source_path=archive_source,
            )
        )
    # Sort deterministically
    final_events.sort(key=lambda e: (e.event_timestamp_utc, e.symbol, e.event_id))

    rejected = [
        {
            "symbol": a.symbol,
            "rejection_reason": a.rejection_reason,
            "first_timestamp_utc": a.first_timestamp_utc,
            "last_timestamp_utc": a.last_timestamp_utc,
            "usable_months": a.usable_months,
            "row_count": a.row_count,
        }
        for a in coverages
        if a.status != "accepted"
    ]

    result = Phase0AResult(summary, final_events, coverages, rejected, warnings)

    if out_dir is not None:
        result = write_report_artifacts(result, out_dir)

    print(f"GENERIC_STRESS_PHASE0A_DONE status={status} accepted={event_count} elapsed_sec={total_elapsed:.1f}", flush=True)
    return result


__all__ = [
    "ACTIVE_ARCHIVE_PATH",
    "ALTCOIN_EXCLUDED_SYMBOLS",
    "COOLDOWN_HOURS",
    "HAS_ORJSON",
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
    "SymbolWorkerResult",
    "accepted_event_json",
    "accepted_event_json_rows",
    "apply_cooldown",
    "compute_quarter_month_distributions",
    "compute_stress_points",
    "compute_stress_points_vectorized",
    "compute_trailing_1h_return_bps",
    "compute_trailing_6h_realized_vol_bps",
    "compute_vol_percentile",
    "discover_archive_paths",
    "discover_jsonl_files",
    "filter_stress_candidates",
    "git_metadata",
    "has_forward_24h_coverage",
    "is_altcoin_symbol",
    "run_from_archive_paths",
    "run_phase0a_audit",
    "validate_precommitment",
    "validate_symbol_coverage",
    "write_report_artifacts",
    "_DEFAULT_WORKERS",
    "_loads_json_line",
    "_merge_worker_results",
    "_process_one_file",
    "_read_jsonl_lines",
]