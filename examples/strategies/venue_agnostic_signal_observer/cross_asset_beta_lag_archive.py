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
        "engine": "cpu",
    }


def run_null_test_gpu(
    event_net_returns: list,
    *,
    iterations: int = NULL_ITERATIONS,
    seed: int = SEED,
    device: str = "cuda:0",
    batch_size: int = 4096,
) -> dict[str, Any]:
    """GPU-accelerated circular-shift null test. Semantically identical to CPU run_null_test.

    Uses torch to batch random circular shifts of the return vector.
    Conservative p-value with +1 correction preserved.
    Seed stability: random offsets precomputed from Python stdlib random.Random,
    identical to the CPU path for a given seed.
    """
    import torch  # noqa: PLC0415

    if len(event_net_returns) < 2:
        return {
            "p_value": None,
            "null_mean": None,
            "null_std": None,
            "iterations": 0,
            "reason": "insufficient_events",
            "engine": "gpu",
            "device": device,
        }

    dev = torch.device(device)
    n = len(event_net_returns)
    returns = torch.tensor(event_net_returns, dtype=torch.float64, device=dev)
    observed_mean = returns.mean().item()

    # Precompute shift offsets from Python stdlib random — identical to CPU path
    rng = random.Random(seed)
    all_offsets = torch.tensor(
        [rng.randint(0, n - 1) for _ in range(iterations)],
        dtype=torch.int64, device=dev,
    )

    # Batch circular shift: shifted[i, j] = returns[(j + offset[i]) % n]
    indices = torch.arange(n, device=dev).unsqueeze(0).expand(iterations, n)
    shifted_indices = (indices + all_offsets.unsqueeze(1)) % n

    count_extreme = 0
    null_means_list: list[float] = []

    for chunk_start in range(0, iterations, batch_size):
        chunk_end = min(chunk_start + batch_size, iterations)
        chunk_indices = shifted_indices[chunk_start:chunk_end]  # [chunk, n]
        chunk_shifted = returns[chunk_indices]  # [chunk, n]
        chunk_means = chunk_shifted.mean(dim=1)  # [chunk]

        # Floating-point tolerance: circular shift preserves the multiset,
        # so any mean deviation from observed_mean is pure fp noise (~1e-16).
        # Use a tiny epsilon to avoid miscounting due to summation order.
        extreme_mask = chunk_means >= (observed_mean - 1e-12)
        count_extreme += int(extreme_mask.sum().item())
        null_means_list.extend(chunk_means.cpu().tolist())

        del chunk_indices, chunk_shifted, chunk_means

    # Conservative p-value
    p_value = (count_extreme + 1) / (iterations + 1)

    import statistics as _st
    null_mean = _st.mean(null_means_list)
    null_std = _st.stdev(null_means_list) if len(null_means_list) > 1 else 0.0

    return {
        "p_value": p_value,
        "null_mean": null_mean,
        "null_std": null_std,
        "iterations": iterations,
        "observed_mean": observed_mean,
        "count_extreme": count_extreme,
        "reason": None,
        "engine": "gpu",
        "device": device,
        "batch_size": batch_size,
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
# Checkpoint / resume support
# ---------------------------------------------------------------------------


# Phases tracked for resume (order matters — resume picks the latest complete)
CHECKPOINT_PHASES: List[str] = [
    "01_availability",
    "02_kline_prefilter",
    "03_aggtrades",
    "04_stress_labels",
    "05_independent_windows",
    "06_coverage",
    "07_forward_returns",
    "08_cell_results",
    "09_baseline",
    "10_null",
    "11_fdr",
    "12_holdout",
]

# Config fields whose values must match across resume
_CHECKPOINT_CONFIG_FIELDS: List[str] = [
    "study_id",
    "source_symbols",
    "target_symbols",
    "calendar_start",
    "calendar_end",
    "stress_rules",
    "stress_dedup_cooldown_seconds",
    "independent_window_separation_seconds",
    "entry_delay_seconds",
    "cost_total_bps",
    "horizons_ms",
    "stress_window_seconds",
    "directions",
    "family_size",
    "seed",
    "null_iterations",
    "fdr_alpha",
    "null_alpha",
    "min_events_per_cell",
    "min_events_holdout",
    "win_rate_threshold",
    "worst_decile_threshold",
    "baseline_delta_bps",
    "min_independent_windows",
    "train_frac",
    "null_engine",
    "null_method",
    "null_device",
    "null_batch_size",
]

NULL_METHOD_TIMESTAMP_SHIFT = "timestamp_shift"
NULL_METHOD_RETURN_VECTOR_SHIFT = "return_vector_shift"
NULL_METHOD_CHOICES: List[str] = [
    NULL_METHOD_TIMESTAMP_SHIFT,
    NULL_METHOD_RETURN_VECTOR_SHIFT,
]
NULL_ENGINE_METHOD_COMPATIBILITY: Dict[str, str] = {
    "cpu": NULL_METHOD_TIMESTAMP_SHIFT,
    "gpu": NULL_METHOD_RETURN_VECTOR_SHIFT,
}


def default_null_method_for_engine(null_engine: str) -> str:
    """Return the explicit null method for a supported engine."""
    try:
        return NULL_ENGINE_METHOD_COMPATIBILITY[null_engine]
    except KeyError as exc:
        raise ValueError(f"unsupported null engine: {null_engine}") from exc


def validate_null_engine_method(null_engine: str, null_method: str) -> None:
    """Validate that engine and method semantics are not silently substituted."""
    expected = default_null_method_for_engine(null_engine)
    if null_method != expected:
        raise ValueError(
            f"null engine '{null_engine}' supports null method '{expected}', "
            f"not '{null_method}'"
        )


def _build_checkpoint_config_identity() -> Dict[str, Any]:
    """Return a deterministic dict of frozen config values for checkpoint validation."""
    return {
        "study_id": "cross_asset_beta_lag_archive_v0",
        "source_symbols": sorted(SOURCE_SYMBOLS),
        "target_symbols": sorted(TARGET_SYMBOLS),
        "calendar_start": CALENDAR_START,
        "calendar_end": CALENDAR_END,
        "stress_rules": sorted(
            [{"name": r[0], "lookback_seconds": r[1], "threshold_bps": r[2]}
             for r in STRESS_RULES],
            key=lambda x: x["name"],
        ),
        "stress_dedup_cooldown_seconds": STRESS_DEDUP_COOLDOWN_NS // 1_000_000_000,
        "independent_window_separation_seconds": INDEPENDENT_WINDOW_SEPARATION_NS // 1_000_000_000,
        "entry_delay_seconds": ENTRY_DELAY_NS // 1_000_000_000,
        "cost_total_bps": TOTAL_COST_BPS,
        "horizons_ms": sorted(HORIZONS_MS),
        "stress_window_seconds": sorted(STRESS_WINDOW_SECONDS),
        "directions": sorted(DIRECTIONS),
        "family_size": FAMILY_SIZE,
        "seed": SEED,
        "null_iterations": NULL_ITERATIONS,
        "fdr_alpha": FDR_ALPHA,
        "null_alpha": NULL_ALPHA,
        "min_events_per_cell": MIN_EVENTS_PER_CELL,
        "min_events_holdout": MIN_EVENTS_HOLDOUT,
        "win_rate_threshold": WIN_RATE_THRESHOLD,
        "worst_decile_threshold": WORST_DECILE_THRESHOLD,
        "baseline_delta_bps": BASELINE_DELTA_BPS,
        "min_independent_windows": MIN_INDEPENDENT_WINDOWS,
        "train_frac": TRAIN_FRAC,
    }


def _checkpoint_path(output_dir: Path, phase: str) -> Path:
    return output_dir / f"checkpoint_phase_{phase}.json"


def _checkpoint_manifest_path(output_dir: Path) -> Path:
    return output_dir / "checkpoint_manifest.json"


def write_checkpoint(
    output_dir: Path,
    phase: str,
    payload: Dict[str, Any],
    *,
    git_sha: str,
    precommitment_sha: str,
    null_engine: str = "cpu",
    null_method: Optional[str] = None,
    null_device: str = "cpu",
    null_batch_size: int = 0,
) -> None:
    """Atomically write a checkpoint artifact with metadata."""
    effective_null_method = null_method or default_null_method_for_engine(null_engine)
    validate_null_engine_method(null_engine, effective_null_method)
    config_id = _build_checkpoint_config_identity()

    artifact = {
        "phase": phase,
        "study_id": "cross_asset_beta_lag_archive_v0",
        "git_sha": git_sha,
        "precommitment_sha": precommitment_sha,
        "config_identity": config_id,
        "config_identity_sha256": _sha256_json(config_id),
        "timestamp_utc": _now_utc_iso(),
        "null_engine": null_engine,
        "null_method": effective_null_method,
        "null_device": null_device,
        "payload": payload,
    }
    path = _checkpoint_path(output_dir, phase)
    atomic_write_json(path, artifact)


def load_checkpoint_manifest(output_dir: Path) -> Optional[Dict[str, Any]]:
    """Load the checkpoint manifest if it exists and is valid JSON."""
    path = _checkpoint_manifest_path(output_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_checkpoint_manifest(
    output_dir: Path,
    completed_phases: List[str],
    *,
    git_sha: str,
    precommitment_sha: str,
    null_engine: str = "cpu",
    null_method: Optional[str] = None,
) -> None:
    """Write the checkpoint manifest tracking completed phases."""
    effective_null_method = null_method or default_null_method_for_engine(null_engine)
    validate_null_engine_method(null_engine, effective_null_method)
    config_id = _build_checkpoint_config_identity()

    manifest = {
        "study_id": "cross_asset_beta_lag_archive_v0",
        "git_sha": git_sha,
        "precommitment_sha": precommitment_sha,
        "config_identity_sha256": _sha256_json(config_id),
        "null_engine": null_engine,
        "null_method": effective_null_method,
        "completed_phases": completed_phases,
        "timestamp_utc": _now_utc_iso(),
    }
    path = _checkpoint_manifest_path(output_dir)
    atomic_write_json(path, manifest)


def discover_checkpoint_phases(output_dir: Path) -> List[str]:
    """Discover valid phase checkpoint files, independent of manifest presence."""
    expected_id = _sha256_json(_build_checkpoint_config_identity())
    valid_phases: List[str] = []
    for phase in CHECKPOINT_PHASES:
        cp_path = _checkpoint_path(output_dir, phase)
        if not cp_path.exists():
            continue
        try:
            data = json.loads(cp_path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("phase") != phase:
            continue
        if data.get("config_identity_sha256", "") != expected_id:
            continue
        valid_phases.append(phase)
    return valid_phases


def compute_resume_phase(output_dir: Path) -> Optional[str]:
    """Determine latest completed phase from manifest or partial checkpoint files."""
    expected_id = _sha256_json(_build_checkpoint_config_identity())
    manifest = load_checkpoint_manifest(output_dir)
    if manifest is not None:
        stored_id = manifest.get("config_identity_sha256", "")
        if stored_id != expected_id:
            return None
        discovered = set(discover_checkpoint_phases(output_dir))
        valid_phases = [phase for phase in manifest.get("completed_phases", []) if phase in discovered]
    else:
        valid_phases = discover_checkpoint_phases(output_dir)

    if not valid_phases:
        return None

    phase_order = {p: i for i, p in enumerate(CHECKPOINT_PHASES)}
    valid_phases.sort(key=lambda p: phase_order.get(p, len(CHECKPOINT_PHASES)))
    return valid_phases[-1]


def validate_checkpoint_config(
    output_dir: Path,
    *,
    git_sha: str,
    precommitment_sha: str,
    null_engine: str = "cpu",
    null_method: Optional[str] = None,
) -> Tuple[bool, str]:
    """Validate that existing checkpoint matches requested config.

    Returns (is_valid, reason_string). Supports partial phase checkpoint
    directories that do not yet have a final checkpoint_manifest.json.
    """
    requested_method = null_method or default_null_method_for_engine(null_engine)
    try:
        validate_null_engine_method(null_engine, requested_method)
    except ValueError as exc:
        return False, f"null_engine_method_mismatch:{exc}"

    config_id = _build_checkpoint_config_identity()
    expected_id = _sha256_json(config_id)
    manifest = load_checkpoint_manifest(output_dir)
    if manifest is not None:
        stored_id = manifest.get("config_identity_sha256", "")
        if stored_id != expected_id:
            return False, f"checkpoint_config_mismatch:{stored_id[:8]}.._vs_{expected_id[:8]}.."
        stored_engine = manifest.get("null_engine", null_engine)
        stored_method = manifest.get("null_method", default_null_method_for_engine(stored_engine))
        if stored_engine != null_engine or stored_method != requested_method:
            return False, "checkpoint_null_semantics_mismatch"
        return True, "valid"

    phases = discover_checkpoint_phases(output_dir)
    if not phases:
        return False, "no_checkpoint_manifest_or_phase_checkpoints"

    for phase in reversed(phases):
        try:
            data = json.loads(_checkpoint_path(output_dir, phase).read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        stored_engine = data.get("null_engine", null_engine)
        stored_method = data.get("null_method", default_null_method_for_engine(stored_engine))
        if stored_engine != null_engine or stored_method != requested_method:
            return False, "checkpoint_null_semantics_mismatch"
        return True, "valid_partial_checkpoints"

    return False, "no_valid_phase_checkpoints"


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

    # Prefilter info
    pf = summary.get("prefilter_summary", {})
    if pf:
        lines.append(f"\n## Kline Prefilter\n")
        lines.append(f"- Full calendar retained: {pf.get('full_calendar_retained', 'N/A')}")
        lines.append(f"- Kline prefilter: source-only (non-verdict-producing)")
        lines.append(f"- Exact stress labels from aggTrades only")
        lines.append(f"- Brute-force estimate: {pf.get('brute_force_mb', 'N/A')} MB")
        lines.append(f"- Planned download estimate: {pf.get('planned_mb', 'N/A')} MB")
        lines.append(f"- Candidate days: {pf.get('candidate_days', 'N/A')}")
        lines.append(f"- Exact aggTrade stress labels: {pf.get('exact_stress_labels', 'N/A')}")
        lines.append(f"- Independent all-target usable windows: {pf.get('usable_windows', 'N/A')}")
        lines.append(f"- Evaluation reached: {pf.get('evaluation_reached', False)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Download plan builder
# ---------------------------------------------------------------------------


def build_stress_day_download_plan(
    candidate_days: List[Dict[str, Any]],
    all_symbols: List[str],
    source_symbols: List[str],
    date_list: List[str],
    *,
    extra_buffer_days: int = 1,
) -> Dict[str, Any]:
    """Build a download plan from candidate stress days.

    Takes candidate days (from kline prefilter) and produces:
    - Deduplicated list of required aggTrade files
    - Estimated download size
    - Brute-force comparison

    Parameters
    ----------
    candidate_days : list of dicts with 'date' and 'source_symbol' keys
    all_symbols : all 6 symbols needing aggTrades per candidate day
    source_symbols : source symbols (2)
    date_list : full calendar date list (for brute-force estimate)

    Returns
    -------
    dict with:
        candidate_dates_sorted, all_required_files, required file details,
        total_estimated_mb, brute_force_estimated_mb, reduction_ratio
    """
    from .binance_vision_archive import estimate_file_size_mb

    # Collect unique candidate dates and symbols
    candidate_dates_set: set = set()
    for cd in candidate_days:
        candidate_dates_set.add(cd["date"])

    candidate_dates = sorted(candidate_dates_set)

    # For each candidate date, add all 6 symbols
    all_required: List[Dict[str, Any]] = []
    file_set: set = set()
    total_est_mb = 0.0

    for d in candidate_dates:
        for sym in all_symbols:
            fkey = f"{sym}_{d}"
            if fkey not in file_set:
                file_set.add(fkey)
                sz = estimate_file_size_mb(sym)
                total_est_mb += sz
                all_required.append({
                    "symbol": sym,
                    "date": d,
                    "source": "aggTrades",
                    "estimated_mb": sz,
                })

    # Add next-day buffer for each candidate date (forward coverage)
    for d in candidate_dates:
        from datetime import datetime, timedelta, timezone
        dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        next_dt = dt + timedelta(days=extra_buffer_days)
        next_d = next_dt.strftime("%Y-%m-%d")
        # Only if within calendar
        if next_d in date_list:
            for sym in all_symbols:
                fkey = f"{sym}_{next_d}"
                if fkey not in file_set:
                    file_set.add(fkey)
                    sz = estimate_file_size_mb(sym)
                    total_est_mb += sz
                    all_required.append({
                        "symbol": sym,
                        "date": next_d,
                        "source": "aggTrades",
                        "estimated_mb": sz,
                        "reason": "forward_buffer",
                    })

    # Add previous-day buffer for source symbols (rolling context)
    for d in candidate_dates:
        from datetime import datetime, timedelta, timezone
        dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        prev_dt = dt - timedelta(days=extra_buffer_days)
        prev_d = prev_dt.strftime("%Y-%m-%d")
        if prev_d in date_list:
            for sym in source_symbols:
                fkey = f"{sym}_{prev_d}"
                if fkey not in file_set:
                    file_set.add(fkey)
                    sz = estimate_file_size_mb(sym)
                    total_est_mb += sz
                    all_required.append({
                        "symbol": sym,
                        "date": prev_d,
                        "source": "aggTrades",
                        "estimated_mb": sz,
                        "reason": "previous_day_context",
                    })

    # Brute-force estimate
    brute_files = len(all_symbols) * len(date_list)
    brute_mb = sum(estimate_file_size_mb(sym) for sym in all_symbols) * len(date_list)

    reduction_ratio = brute_mb / total_est_mb if total_est_mb > 0 else 1.0

    return {
        "candidate_dates": candidate_dates,
        "candidate_dates_count": len(candidate_dates),
        "required_file_count": len(all_required),
        "required_files": all_required,
        "total_estimated_mb": round(total_est_mb, 1),
        "brute_force_files": brute_files,
        "brute_force_estimated_mb": round(brute_mb, 1),
        "reduction_ratio": round(reduction_ratio, 1),
        "file_keys_sorted": sorted(file_set),
    }
