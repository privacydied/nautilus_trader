"""Cross-asset beta-lag archive v0 study implementation.

Study: cross_asset_beta_lag_archive_v0

This is an archive backfill of the cross-asset beta-lag stress thread.
BTC/ETH spot stress impulses -> delayed repricing in SOL/LINK/DOGE/AVAX.

Public data observer only. No orders. No execution. No auth.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import time
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .tick_models import TickForwardReturn, TickSignalEvent, TradeTickLite
from .event_study import evaluate_tick_signal
from .binance_vision_archive import (
    download_daily_agg_trades,
    download_daily_klines_1m,
    parse_agg_trade_csv,
    parse_1m_klines_csv,
    scan_archive_availability,
    compute_common_calendar,
    build_file_manifest,
    _iter_date_range,
    _sha256_file,
)
from .run_artifacts import (
    create_run_id,
    create_run_dir,
    atomic_write_json,
    atomic_write_text,
    atomic_write_jsonl,
)

# ---------------------------------------------------------------------------
# Constants (from precommitment)
# ---------------------------------------------------------------------------

SOURCE_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
TARGET_SYMBOLS = ["SOLUSDT", "LINKUSDT", "DOGEUSDT", "AVAXUSDT"]
ALL_SYMBOLS = list(dict.fromkeys(SOURCE_SYMBOLS + TARGET_SYMBOLS))

CALENDAR_START = "2024-01-01"
CALENDAR_END = "2026-04-30"
MIN_CALENDAR_DAYS = 90
MIN_INDEPENDENT_WINDOWS = 20

STRESS_RULES = [
    ("30s_30bps", 30, 30.0),
    ("60s_50bps", 60, 50.0),
]

STRESS_DEDUP_COOLDOWN_NS = 30 * 1_000_000_000  # 30 seconds
INDEPENDENT_WINDOW_SEPARATION_NS = 1800 * 1_000_000_000  # 30 minutes
ENTRY_DELAY_NS = 1_000_000_000  # 1 second

FEE_BPS = 40
SLIPPAGE_BPS = 5
QUOTE_MISMATCH_BUFFER_BPS = 5
TOTAL_COST_BPS = FEE_BPS + SLIPPAGE_BPS + QUOTE_MISMATCH_BUFFER_BPS  # 50

HORIZONS_MS = [30000, 60000, 300000]
STRESS_WINDOW_SECONDS = [30, 60]
DIRECTIONS = ["bullish", "bearish"]

MIN_EVENTS_PER_CELL = 50
MIN_EVENTS_HOLDOUT = 20
WIN_RATE_THRESHOLD = 0.55
WORST_DECILE_THRESHOLD = -50.0
BASELINE_DELTA_BPS = 10.0
NULL_ALPHA = 0.05
FDR_ALPHA = 0.05
TRAIN_FRAC = 0.7
NULL_ITERATIONS = 1000
SEED = 42

FAMILY_SIZE = len(SOURCE_SYMBOLS) * len(TARGET_SYMBOLS) * len(STRESS_WINDOW_SECONDS) * len(DIRECTIONS) * len(HORIZONS_MS)
# 2 * 4 * 2 * 2 * 3 = 96

VENUE = "binance_spot_archive"

MS_TO_NS = 1_000_000

# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_json(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _get_git_sha() -> str:
    import subprocess
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StressLabel:
    """A single archive-based stress label."""
    label_id: str
    source_symbol: str
    stress_start_ns: int
    stress_end_ns: int
    stress_window_seconds: int
    source_move_bps: float
    direction: str  # "bullish" or "bearish"
    source_start_price: float
    source_end_price: float
    independent_window_id: str
    rule_name: str


@dataclass
class CoverageInterval:
    """Tick coverage info for a symbol within a stress window."""
    symbol: str
    tick_count: int
    first_ts_ns: int
    last_ts_ns: int
    has_coverage: bool


@dataclass
class TickerArchiveManifest:
    """Download metadata for files used in this study."""
    run_id: str
    study_id: str = "cross_asset_beta_lag_archive_v0"
    source_type: str = "binance_vision_archive"
    safety_mode: str = "public_data_observer_only"
    precommitment_hash: str = ""
    git_sha: str = ""
    archive_files: List[dict[str, Any]] = field(default_factory=list)
    global_coverage: dict[str, Any] = field(default_factory=dict)
    per_symbol_coverage: dict[str, dict[str, Any]] = field(default_factory=dict)
    missing_symbols: List[str] = field(default_factory=list)
    failed_files: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Stress label generation from tick data
# ---------------------------------------------------------------------------


def _compute_move_bps(ticks: List[TradeTickLite], lookback_ns: int) -> List[dict[str, Any]]:
    """Compute rolling absolute move bps over lookback window on tick data.

    Returns list of dicts: {ts_ns, price, move_bps, start_price, end_price}
    Sorted by ts_ns.
    """
    if not ticks:
        return []

    results: List[dict[str, Any]] = []
    window_start = 0

    for i, t in enumerate(ticks):
        ts = t.ts_event
        price = t.price

        # Advance window_start to within lookback
        while window_start < i and (ts - ticks[window_start].ts_event) > lookback_ns:
            window_start += 1

        ref = ticks[window_start]
        ref_price = ref.price

        if ref_price <= 0 or not math.isfinite(ref_price) or not math.isfinite(price):
            continue

        move_bps = (price - ref_price) / ref_price * 10000.0

        results.append({
            "ts_ns": ts,
            "price": price,
            "move_bps": move_bps,
            "start_price": ref_price,
            "end_price": price,
            "start_ts_ns": ref.ts_event,
        })

    return results


def generate_stress_labels(
    ticks: List[TradeTickLite],
    source_symbol: str,
    *,
    lookback_seconds: int,
    threshold_bps: float,
) -> List[StressLabel]:
    """Generate stress labels from tick data for one source symbol.

    Returns labels with dedup cooldown applied.
    """
    lookback_ns = lookback_seconds * 1_000_000_000
    moves = _compute_move_bps(ticks, lookback_ns)

    labels: List[StressLabel] = []
    last_label_ts: Optional[int] = None

    # Independent window tracking
    rng = random.Random(SEED)

    for mv in moves:
        abs_move = abs(mv["move_bps"])
        if abs_move < threshold_bps:
            continue

        # Cooldown check
        if last_label_ts is not None and (mv["ts_ns"] - last_label_ts) < STRESS_DEDUP_COOLDOWN_NS:
            continue

        direction = "bullish" if mv["move_bps"] > 0 else "bearish"

        stress_window = lookback_seconds
        # Independent window id
        window_id = f"iw_{source_symbol}_{stress_window}s_{mv['start_ts_ns']}_{mv['ts_ns']}_{rng.randint(0, 999999):06d}"

        label = StressLabel(
            label_id=f"sl_{source_symbol}_{stress_window}s_{mv['ts_ns']}_{rng.randint(0, 999999):06d}",
            source_symbol=source_symbol,
            stress_start_ns=mv["start_ts_ns"],
            stress_end_ns=mv["ts_ns"],
            stress_window_seconds=lookback_seconds,
            source_move_bps=mv["move_bps"],
            direction=direction,
            source_start_price=mv["start_price"],
            source_end_price=mv["end_price"],
            independent_window_id=window_id,
            rule_name=f"{lookback_seconds}s_{int(threshold_bps)}bps",
        )

        labels.append(label)
        last_label_ts = mv["ts_ns"]

    return labels


def deduplicate_labels(labels: List[StressLabel]) -> List[StressLabel]:
    """Apply cross-label dedup: same source + direction within cooldown.

    Also assigns independent window IDs with separation >= 30 minutes.
    """
    if not labels:
        return []

    # Sort by timestamp
    sorted_labels = sorted(labels, key=lambda x: x.stress_end_ns)

    # Per (source, direction) cooldown tracking
    last_by_key: Dict[Tuple[str, str], int] = {}
    deduped: List[StressLabel] = []

    for lbl in sorted_labels:
        key = (lbl.source_symbol, lbl.direction)
        last_ts = last_by_key.get(key, -STRESS_DEDUP_COOLDOWN_NS)
        if lbl.stress_end_ns - last_ts < STRESS_DEDUP_COOLDOWN_NS:
            continue
        deduped.append(lbl)
        last_by_key[key] = lbl.stress_end_ns

    return deduped


def assign_independent_windows(
    labels: List[StressLabel],
) -> List[StressLabel]:
    """Assign independent window IDs.

    Any two labels separated by >= 30 minutes get different window IDs.
    """
    if not labels:
        return []

    sorted_labels = sorted(labels, key=lambda x: x.stress_end_ns)
    result: List[StressLabel] = []
    window_counter = 0
    window_start_ts = sorted_labels[0].stress_end_ns
    rng = random.Random(SEED)

    for lbl in sorted_labels:
        if lbl.stress_end_ns - window_start_ts >= INDEPENDENT_WINDOW_SEPARATION_NS:
            window_counter += 1
            window_start_ts = lbl.stress_end_ns

        base = f"iw{window_counter:06d}_{lbl.source_symbol}_{lbl.stress_window_seconds}s"
        new_lbl = StressLabel(
            label_id=lbl.label_id,
            source_symbol=lbl.source_symbol,
            stress_start_ns=lbl.stress_start_ns,
            stress_end_ns=lbl.stress_end_ns,
            stress_window_seconds=lbl.stress_window_seconds,
            source_move_bps=lbl.source_move_bps,
            direction=lbl.direction,
            source_start_price=lbl.source_start_price,
            source_end_price=lbl.source_end_price,
            independent_window_id=base,
            rule_name=lbl.rule_name,
        )
        result.append(new_lbl)

    return result


# ---------------------------------------------------------------------------
# Coverage check
# ---------------------------------------------------------------------------


def check_target_coverage(
    all_target_ticks: Dict[str, List[TradeTickLite]],
    entry_ns: int,
    max_horizon_ns: int,
    *,
    buffer_ns: int = 5_000_000_000,
) -> Tuple[bool, Dict[str, CoverageInterval]]:
    """Check if all targets have tick coverage from entry through horizon.

    Returns (all_covered, coverage_map).
    """
    horizon_end = entry_ns + max_horizon_ns + buffer_ns
    coverage: Dict[str, CoverageInterval] = {}

    for sym, ticks in all_target_ticks.items():
        if not ticks:
            coverage[sym] = CoverageInterval(
                symbol=sym, tick_count=0, first_ts_ns=0, last_ts_ns=0, has_coverage=False,
            )
            continue

        first_ts = ticks[0].ts_event
        last_ts = ticks[-1].ts_event

        # Check if we have coverage from entry through horizon_end
        has_coverage = first_ts <= entry_ns and last_ts >= horizon_end

        # Check tick count in forward interval
        forward_ticks = [t for t in ticks if entry_ns <= t.ts_event <= horizon_end]
        if not forward_ticks:
            has_coverage = False

        coverage[sym] = CoverageInterval(
            symbol=sym,
            tick_count=len(forward_ticks),
            first_ts_ns=first_ts,
            last_ts_ns=last_ts,
            has_coverage=has_coverage,
        )

    all_covered = all(c.has_coverage for c in coverage.values())
    return all_covered, coverage


# ---------------------------------------------------------------------------
# Cell / group key
# ---------------------------------------------------------------------------


def cell_group_key(
    source_symbol: str,
    target_symbol: str,
    stress_window_seconds: int,
    direction: str,
    horizon_ms: int,
) -> str:
    return f"{source_symbol}->{target_symbol}/{stress_window_seconds}s/{direction}/{horizon_ms}ms"


def all_cell_keys() -> List[str]:
    """Return all 96 primary cell group keys."""
    keys: List[str] = []
    for src in SOURCE_SYMBOLS:
        for tgt in TARGET_SYMBOLS:
            for sw in STRESS_WINDOW_SECONDS:
                for d in DIRECTIONS:
                    for h in HORIZONS_MS:
                        keys.append(cell_group_key(src, tgt, sw, d, h))
    return keys


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def compute_forward_returns_for_stress(
    stress_label: StressLabel,
    target_ticks: List[TradeTickLite],
    target_symbol: str,
    horizons_ms: List[int],
) -> List[TickForwardReturn]:
    """Compute forward returns for a stress label on one target.

    Entry is fixed 1 second after stress end timestamp.
    Direction adjustment: bullish -> positive beta, bearish -> negative beta.
    """
    entry_ts_ns = stress_label.stress_end_ns + ENTRY_DELAY_NS

    timestamps, prices = _extract_ts_prices(target_ticks)
    results: List[TickForwardReturn] = []

    # Find entry reference at or after entry_ts_ns
    entry_idx = bisect_left(timestamps, entry_ts_ns)
    if entry_idx >= len(timestamps):
        # No entry price
        for h in horizons_ms:
            results.append(TickForwardReturn(
                signal_id=stress_label.label_id,
                signal_ts=entry_ts_ns,
                target_venue=VENUE,
                target_symbol=target_symbol,
                horizon_ms=h,
                entry_reference_price=None,
                forward_price=None,
                fee_bps=FEE_BPS,
                slippage_bps=SLIPPAGE_BPS,
                quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
                valid=False,
                rejection_reason="no_entry_reference_price",
            ))
        return results

    entry_price = prices[entry_idx]

    for horizon_ms in horizons_ms:
        horizon_ns = entry_ts_ns + horizon_ms * MS_TO_NS
        fwd_idx = bisect_left(timestamps, horizon_ns)

        if fwd_idx >= len(timestamps):
            # No forward price
            net_return = None
            raw_return = None
            dir_adj = None
            valid = False
            rejection = f"no_forward_price_at_{horizon_ms}ms"

            if entry_price is not None and entry_price > 0 and math.isfinite(entry_price):
                # Try with last available price
                fwd_price = prices[-1]
                raw_return = (fwd_price - entry_price) / entry_price * 10000.0
                dir_adj = raw_return if stress_label.direction == "bullish" else -raw_return
                net_return = dir_adj - TOTAL_COST_BPS
                valid = True
                rejection = None
            else:
                fwd_price = None

            results.append(TickForwardReturn(
                signal_id=stress_label.label_id,
                signal_ts=entry_ts_ns,
                target_venue=VENUE,
                target_symbol=target_symbol,
                horizon_ms=horizon_ms,
                entry_reference_price=entry_price,
                forward_price=fwd_price,
                raw_return_bps=raw_return,
                direction_adjusted_return_bps=dir_adj,
                fee_bps=FEE_BPS,
                slippage_bps=SLIPPAGE_BPS,
                quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
                net_return_bps=net_return,
                valid=valid,
                rejection_reason=rejection,
            ))
            continue

        fwd_price = prices[fwd_idx]
        if not math.isfinite(fwd_price) or entry_price <= 0 or not math.isfinite(entry_price):
            results.append(TickForwardReturn(
                signal_id=stress_label.label_id,
                signal_ts=entry_ts_ns,
                target_venue=VENUE,
                target_symbol=target_symbol,
                horizon_ms=horizon_ms,
                entry_reference_price=entry_price,
                forward_price=fwd_price,
                fee_bps=FEE_BPS,
                slippage_bps=SLIPPAGE_BPS,
                quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
                valid=False,
                rejection_reason="non_finite_or_zero_prices",
            ))
            continue

        raw_return = (fwd_price - entry_price) / entry_price * 10000.0
        # Direction adjustment
        dir_adj = raw_return if stress_label.direction == "bullish" else -raw_return
        net_return = dir_adj - TOTAL_COST_BPS

        results.append(TickForwardReturn(
            signal_id=stress_label.label_id,
            signal_ts=entry_ts_ns,
            target_venue=VENUE,
            target_symbol=target_symbol,
            horizon_ms=horizon_ms,
            entry_reference_price=entry_price,
            forward_price=fwd_price,
            raw_return_bps=raw_return,
            direction_adjusted_return_bps=dir_adj,
            fee_bps=FEE_BPS,
            slippage_bps=SLIPPAGE_BPS,
            quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
            net_return_bps=net_return,
            valid=True,
            rejection_reason=None,
        ))

    return results


def _extract_ts_prices(ticks: List[TradeTickLite]) -> Tuple[List[int], List[float]]:
    """Extract parallel (ts_event, price) arrays."""
    if not ticks:
        return [], []
    return [t.ts_event for t in ticks], [t.price for t in ticks]


# ---------------------------------------------------------------------------
# Cell stats
# ---------------------------------------------------------------------------


@dataclass
class CellStats:
    group_key: str
    valid_count: int
    valid_events: List[TickForwardReturn]
    net_returns_bps: List[float]
    mean_net_bps: Optional[float]
    median_net_bps: Optional[float]
    win_rate: Optional[float]
    worst_decile_net_bps: Optional[float]
    best_decile_net_bps: Optional[float]
    sum_net_bps: float


def compute_cell_stats(events: List[TickForwardReturn]) -> CellStats:
    """Compute summary stats for a list of forward returns."""
    valid_raw = [e for e in events if e.valid and e.net_return_bps is not None and math.isfinite(e.net_return_bps)]

    if not valid_raw:
        return CellStats(
            group_key="",
            valid_count=0,
            valid_events=[],
            net_returns_bps=[],
            mean_net_bps=None,
            median_net_bps=None,
            win_rate=None,
            worst_decile_net_bps=None,
            best_decile_net_bps=None,
            sum_net_bps=0.0,
        )

    # Safe list of float values — explicitly checked
    nets_checked: List[float] = []
    for e in valid_raw:
        nb = e.net_return_bps
        if nb is not None and math.isfinite(nb):
            nets_checked.append(nb)
    nets = nets_checked
    mean_net = statistics.mean(nets)
    median_net = statistics.median(nets)
    wins = sum(1 for n in nets if n > 0)
    win_rate_val = wins / len(nets)

    sorted_nets = sorted(nets)
    worst_decile = sorted_nets[len(sorted_nets) // 10] if len(sorted_nets) >= 10 else sorted_nets[0]
    best_decile = sorted_nets[-(len(sorted_nets) // 10)] if len(sorted_nets) >= 10 else sorted_nets[-1]

    return CellStats(
        group_key="",
        valid_count=len(valid_raw),
        valid_events=valid_raw,
        net_returns_bps=nets,
        mean_net_bps=mean_net,
        median_net_bps=median_net,
        win_rate=win_rate_val,
        worst_decile_net_bps=worst_decile,
        best_decile_net_bps=best_decile,
        sum_net_bps=sum(nets),
    )


def get_group_key(event: TickForwardReturn, stress_label: StressLabel) -> str:
    """Derive cell group key from an event and stress label."""
    raise NotImplementedError("Use make_event_row instead")


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


def generate_baseline_events(
    all_target_ticks: Dict[str, List[TradeTickLite]],
    source_symbol: str,
    label_count: int,
    *,
    seed: int = SEED,
) -> List[TickForwardReturn]:
    """Generate random baseline forward returns.

    Random timestamps drawn from available tick range.
    """
    if not all_target_ticks:
        return []

    rng = random.Random(seed)

    # Get eligible timestamp range from target data
    all_ts: List[int] = []
    for sym, ticks in all_target_ticks.items():
        if ticks:
            all_ts.extend(t.ts_event for t in ticks)

    if not all_ts:
        return []

    min_ts = min(all_ts)
    max_ts = max(all_ts)

    baseline_frs: List[TickForwardReturn] = []

    for i in range(label_count):
        # Random timestamp in range
        rand_ts = rng.randint(min_ts, max_ts)

        for tgt_sym in TARGET_SYMBOLS:
            target_ticks = all_target_ticks.get(tgt_sym, [])
            if not target_ticks:
                continue

            # Create a pseudo-label for baseline
            pseudo_direction = "bullish" if rng.random() < 0.5 else "bearish"

            for horizon_ms in HORIZONS_MS:
                entry_ts_ns = rand_ts + ENTRY_DELAY_NS
                timestamps, prices = _extract_ts_prices(target_ticks)
                entry_idx = bisect_left(timestamps, entry_ts_ns)

                if entry_idx >= len(timestamps):
                    continue

                entry_price = prices[entry_idx]
                horizon_ns = entry_ts_ns + horizon_ms * MS_TO_NS
                fwd_idx = bisect_left(timestamps, horizon_ns)

                if fwd_idx >= len(timestamps):
                    continue

                fwd_price = prices[fwd_idx]

                if not math.isfinite(fwd_price) or entry_price <= 0:
                    continue

                raw_return = (fwd_price - entry_price) / entry_price * 10000.0
                dir_adj = raw_return if pseudo_direction == "bullish" else -raw_return
                net_return = dir_adj - TOTAL_COST_BPS

                fr = TickForwardReturn(
                    signal_id=f"baseline_{i}_{tgt_sym}_{horizon_ms}ms",
                    signal_ts=entry_ts_ns,
                    target_venue=VENUE,
                    target_symbol=tgt_sym,
                    horizon_ms=horizon_ms,
                    entry_reference_price=entry_price,
                    forward_price=fwd_price,
                    raw_return_bps=raw_return,
                    direction_adjusted_return_bps=dir_adj,
                    fee_bps=FEE_BPS,
                    slippage_bps=SLIPPAGE_BPS,
                    quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
                    net_return_bps=net_return,
                    valid=True,
                )
                baseline_frs.append(fr)

    return baseline_frs


# ---------------------------------------------------------------------------
# Null test (circular time shift)
# ---------------------------------------------------------------------------


def run_null_test(
    event_net_returns: List[float],
    *,
    iterations: int = NULL_ITERATIONS,
    seed: int = SEED,
) -> dict[str, Any]:
    """Run circular-shift null test on event return vector.

    Shifts timestamps circularly. Returns dict with p_value and null stats.
    """
    if len(event_net_returns) < 2:
        return {
            "p_value": None,
            "null_mean": None,
            "null_std": None,
            "iterations": 0,
            "reason": "insufficient_events",
        }

    observed_mean = sum(event_net_returns) / len(event_net_returns)

    rng = random.Random(seed)
    n = len(event_net_returns)

    count_extreme = 0
    null_means: List[float] = []

    for _ in range(iterations):
        # Random circular shift
        shift = rng.randint(0, n - 1)
        shifted = event_net_returns[shift:] + event_net_returns[:shift]
        null_mean = sum(shifted) / n
        null_means.append(null_mean)

        if null_mean >= observed_mean:
            count_extreme += 1

    # Conservative p-value
    p_value = (count_extreme + 1) / (iterations + 1)

    null_mean = statistics.mean(null_means)
    null_std = statistics.stdev(null_means) if len(null_means) > 1 else 0.0

    return {
        "p_value": p_value,
        "null_mean": null_mean,
        "null_std": null_std,
        "iterations": iterations,
        "observed_mean": observed_mean,
        "count_extreme": count_extreme,
        "reason": None,
    }


# ---------------------------------------------------------------------------
# FDR - Benjamini-Yekutieli
# ---------------------------------------------------------------------------


def _harmonic_sum(n: int) -> float:
    s = 0.0
    for j in range(1, n + 1):
        s += 1.0 / j
    return s


def apply_by_fdr(
    pvalues: List[Tuple[str, float]],
    alpha: float = FDR_ALPHA,
    family_size: int = FAMILY_SIZE,
) -> Dict[str, dict[str, Any]]:
    """Apply Benjamini-Yekutieli FDR correction.

    Args:
        pvalues: List of (cell_key, p_value) for p-valued cells only.
        alpha: FDR alpha threshold.
        family_size: Frozen primary family size (for reporting).

    Returns:
        Dict cell_key -> {p_value, q_value, threshold, fdr_passed, rank}
    """
    m = len(pvalues)
    results: Dict[str, dict[str, Any]] = {}

    if m == 0:
        return results

    c_m = _harmonic_sum(m)
    sorted_pv = sorted(pvalues, key=lambda x: (x[1], x[0]))

    max_q = 0.0
    for i, (cell_id, p_val) in enumerate(sorted_pv):
        rank = i + 1
        threshold = (rank / (m * c_m)) * alpha
        raw_q = (p_val * m * c_m) / rank
        q_val = max(raw_q, max_q)
        max_q = q_val
        q_val = max(0.0, min(1.0, q_val))

        fdr_passed = p_val <= threshold

        results[cell_id] = {
            "p_value": p_val,
            "q_value": q_val,
            "threshold": threshold,
            "fdr_passed": fdr_passed,
            "rank": rank,
        }

    # Monotonic adjustment
    sorted_ids = [r[0] for r in sorted_pv]
    last_passing_rank = 0
    for cell_id in sorted_ids:
        if results[cell_id]["fdr_passed"]:
            last_passing_rank = results[cell_id]["rank"]

    prev_q = 0.0
    for cell_id in sorted_ids:
        cur_q = results[cell_id]["q_value"]
        if cur_q < prev_q:
            results[cell_id]["q_value"] = prev_q
        else:
            prev_q = cur_q
        if results[cell_id]["rank"] > last_passing_rank:
            results[cell_id]["fdr_passed"] = False

    return results


# ---------------------------------------------------------------------------
# Event vector reconciliation
# ---------------------------------------------------------------------------


def reconcile_event_vector(cell_stats: CellStats, raw_events: List[TickForwardReturn]) -> bool:
    """Verify summary stats reproduce from raw event rows."""
    valid = [e for e in raw_events if e.valid and e.net_return_bps is not None and math.isfinite(e.net_return_bps)]

    if len(valid) != cell_stats.valid_count:
        return False

    if not valid:
        return True  # both zero

    nets = [e.net_return_bps for e in valid]
    computed_mean = statistics.mean(nets)
    computed_median = statistics.median(nets)
    computed_wins = sum(1 for n in nets if n > 0)
    computed_win_rate = computed_wins / len(nets)

    # Allow tiny float diffs
    if abs(computed_mean - cell_stats.mean_net_bps) > 0.001:
        return False
    if abs(computed_median - cell_stats.median_net_bps) > 0.001:
        return False
    if abs(computed_win_rate - cell_stats.win_rate) > 0.001:
        return False

    return True


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------


def write_report(
    output_dir: Path,
    run_id: str,
    precommitment_hash: str,
    availability: dict[str, Any],
    file_manifest: List[dict[str, Any]],
    stress_labels: List[dict[str, Any]],
    independent_windows: List[dict[str, Any]],
    coverage_summary: dict[str, Any],
    signal_rows: List[dict[str, Any]],
    forward_return_rows: List[dict[str, Any]],
    cell_results: Dict[str, dict[str, Any]],
    baseline_results: Dict[str, dict[str, Any]],
    null_results: Dict[str, dict[str, Any]],
    fdr_results: Dict[str, dict[str, Any]],
    holdout_results: Dict[str, dict[str, Any]],
    reconciliation: dict[str, Any],
    summary: dict[str, Any],
    final_verdict: str,
    git_sha: str,
    end_sha: str,
    dirty_status: str,
) -> None:
    """Write all report artifacts."""
    # PRECOMMITMENT_SHA256.txt
    atomic_write_text(output_dir / "PRECOMMITMENT_SHA256.txt", precommitment_hash)

    # preflight.json
    atomic_write_json(output_dir / "preflight.json", {
        "run_id": run_id,
        "study_id": "cross_asset_beta_lag_archive_v0",
        "safety_mode": "public_data_observer_only",
        "git_sha": git_sha,
        "precommitment_hash": precommitment_hash,
        "generated_at": _now_utc_iso(),
    })

    # archive_availability.json
    atomic_write_json(output_dir / "archive_availability.json", availability)

    # archive_file_manifest.json
    atomic_write_json(output_dir / "archive_file_manifest.json", file_manifest)

    # stress_labels.jsonl
    atomic_write_jsonl(output_dir / "stress_labels.jsonl", stress_labels)

    # independent_windows.jsonl
    atomic_write_jsonl(output_dir / "independent_windows.jsonl", independent_windows)

    # coverage_summary.json
    atomic_write_json(output_dir / "coverage_summary.json", coverage_summary)

    # signals.jsonl
    atomic_write_jsonl(output_dir / "signals.jsonl", signal_rows)

    # forward_returns.jsonl
    atomic_write_jsonl(output_dir / "forward_returns.jsonl", forward_return_rows)

    # cell_results.csv (also json)
    atomic_write_json(output_dir / "cell_results.json", cell_results)

    # baseline_results.json
    atomic_write_json(output_dir / "baseline_results.json", baseline_results)

    # null_results.json
    atomic_write_json(output_dir / "null_results.json", null_results)

    # fdr_results.json
    atomic_write_json(output_dir / "fdr_results.json", fdr_results)

    # holdout_results.json
    atomic_write_json(output_dir / "holdout_results.json", holdout_results)

    # event_vector_reconciliation.json
    atomic_write_json(output_dir / "event_vector_reconciliation.json", reconciliation)

    # summary.json
    atomic_write_json(output_dir / "summary.json", summary)

    # FINAL_REPORT.md
    report_md = _build_final_report_md(
        run_id=run_id,
        git_sha=git_sha,
        end_sha=end_sha,
        dirty_status=dirty_status,
        precommitment_hash=precommitment_hash,
        availability=availability,
        file_manifest=file_manifest,
        stress_labels=stress_labels,
        independent_windows=independent_windows,
        coverage_summary=coverage_summary,
        cell_results=cell_results,
        null_results=null_results,
        fdr_results=fdr_results,
        holdout_results=holdout_results,
        summary=summary,
        final_verdict=final_verdict,
    )
    atomic_write_text(output_dir / "FINAL_REPORT.md", report_md)


def _build_final_report_md(
    run_id: str,
    git_sha: str,
    end_sha: str,
    dirty_status: str,
    precommitment_hash: str,
    availability: dict[str, Any],
    file_manifest: List[dict[str, Any]],
    stress_labels: List[dict[str, Any]],
    independent_windows: List[dict[str, Any]],
    coverage_summary: dict[str, Any],
    cell_results: Dict[str, dict[str, Any]],
    null_results: Dict[str, dict[str, Any]],
    fdr_results: Dict[str, dict[str, Any]],
    holdout_results: Dict[str, dict[str, Any]],
    summary: dict[str, Any],
    final_verdict: str,
) -> str:
    """Build FINAL_REPORT.md content."""
    lines: List[str] = []
    lines.append(f"# Cross-Asset Beta-Lag Archive v0 — FINAL REPORT\n")
    lines.append(f"**Run ID:** {run_id}")
    lines.append(f"**Study:** cross_asset_beta_lag_archive_v0")
    lines.append(f"**Branch:** feat/cross-asset-beta-lag-archive-v0")
    lines.append(f"**Starting SHA:** {git_sha}")
    lines.append(f"**Ending SHA:** {end_sha}")
    lines.append(f"**Dirty status:** {dirty_status}")
    lines.append(f"**Safety posture:** public_data_observer_only\n")

    lines.append(f"## Precommitment\n")
    lines.append(f"- Hash: `{precommitment_hash}`")
    lines.append(f"- Family size: {FAMILY_SIZE} primary cells\n")

    lines.append(f"## Archive Data\n")
    lines.append(f"- Date range: {availability.get('common_start', 'N/A')} to {availability.get('common_end', 'N/A')}")
    lines.append(f"- Common calendar days: {availability.get('common_days', 0)}")
    lines.append(f"- Archive files used: {len(file_manifest)}\n")

    stress_count = len(stress_labels)
    win_count = len(independent_windows)
    lines.append(f"## Stress Labels\n")
    lines.append(f"- Source stress labels: {stress_count}")
    lines.append(f"- Independent windows: {win_count}\n")

    lines.append(f"## Coverage\n")
    lines.append(f"- Per-target coverage: {json.dumps(coverage_summary.get('per_target', {}), indent=2)}\n")

    total_events = summary.get("total_valid_events", 0)
    powered = summary.get("powered_cells", 0)
    underpowered = summary.get("underpowered_cells", 0)
    lines.append(f"## Events\n")
    lines.append(f"- Total valid events: {total_events}")
    lines.append(f"- Powered cells: {powered}")
    lines.append(f"- Underpowered cells: {underpowered}\n")

    lines.append(f"## Best Cells (by mean net bps)\n")
    best = summary.get("best_cells_mean_net", [])
    for b in best[:5]:
        lines.append(f"- {b.get('group_key', '?')}: {b.get('mean_net_bps', '?')} bps (n={b.get('n', 0)})")

    lines.append(f"\n## Best Cells (by baseline delta)\n")
    best_delta = summary.get("best_cells_baseline_delta", [])
    for b in best_delta[:5]:
        lines.append(f"- {b.get('group_key', '?')}: delta={b.get('baseline_delta_bps', '?')} bps")

    lines.append(f"\n## Null / FDR Outcomes\n")
    null_passed = summary.get("null_passed_cells", 0)
    fdr_passed = summary.get("fdr_passed_cells", 0)
    lines.append(f"- Cells passing null: {null_passed}")
    lines.append(f"- Cells passing FDR: {fdr_passed}\n")

    lines.append(f"## Holdout Outcomes\n")
    holdout_passed = summary.get("holdout_passed_cells", 0)
    lines.append(f"- Cells passing holdout: {holdout_passed}\n")

    lines.append(f"## Final Verdict\n")
    lines.append(f"**{final_verdict}**\n")

    if summary.get("early_stop_reason"):
        lines.append(f"**Early stop reason:** {summary['early_stop_reason']}\n")

    lines.append(f"## What This Rejects\n")
    if "rejected" in final_verdict.lower():
        lines.append(f"- Cross-asset beta-lag (BTC/ETH -> SOL/LINK/DOGE/AVAX) under archive v0 design")
        lines.append(f"- Binance Vision archive only, 50bps cost, 96-cell family")
    else:
        lines.append("- Nothing — evaluation did not reach a rejection verdict\n")

    lines.append(f"## What This Does NOT Reject\n")
    lines.append("- Different venues (Coinbase, Kraken)")
    lines.append("- Order-book / microstructure beta-lag")
    lines.append("- Maker/rebate execution")
    lines.append("- Futures/perp target leg")
    lines.append("- Options IV/RV overlays")
    lines.append("- Live stress replication")
    lines.append("- Target universe beyond SOL/LINK/DOGE/AVAX")
    lines.append("- Lower-cost execution")
    lines.append("- Shortability/execution feasibility of bearish cells\n")

    registry_updated = summary.get("registry_updated", False)
    lines.append(f"## Registry\n")
    lines.append(f"- REJECTED_RESEARCH.md updated: {registry_updated}\n")

    lines.append(f"## Tests\n")
    tests = summary.get("test_results", {})
    lines.append(f"- Tests run: {tests.get('run', 0)}")
    lines.append(f"- Tests passed: {tests.get('passed', 0)}")
    lines.append(f"- Tests failed: {tests.get('failed', 0)}")

    return "\n".join(lines)
