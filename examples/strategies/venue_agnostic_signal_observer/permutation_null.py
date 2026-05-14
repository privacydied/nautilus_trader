"""Permutation / null test module for venue_agnostic_signal_observer.

Pure functions only. No network, no capture, no live imports.

This module tests whether an observed candidate signal beats a realistic null
of randomly-shifted source event timing.  It preserves the inter-event structure
(circular time shift, block time shift) rather than shuffling individual ticks
independently — that would destroy burst structure and produce an overly weak
null distribution.

Key functions
-------------
is_null_worthy_group          – gate: should this group be null-tested?
select_null_candidate_groups  – pick top N groups for null testing
circular_time_shift            – shift all event timestamps by a random offset (circular)
block_time_shift               – shift event timestamps in blocks (preserves clustering)
compute_null_distribution      – build null distribution via repeated shifts
run_null_test_for_group        – high-level: run full null test for one group
"""
from __future__ import annotations

import json
import math
import random
import statistics
import uuid
from pathlib import Path
from typing import Any

from .tick_models import TickSignalEvent, TickForwardReturn
from .event_study import evaluate_tick_signal
from .mcpt_export import (
    is_mcpt_worthy_group,
    _load_jsonl,
    _load_summary_json,
)
from .artifact_metadata import build_metadata, get_metadata_field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_COST_FLOOR_BPS: float = 50.0
DEFAULT_MIN_EVENTS: int = 30
DEFAULT_ITERATIONS: int = 1000
DEFAULT_SEED: int = 42


# ---------------------------------------------------------------------------
# is_null_worthy_group
# ---------------------------------------------------------------------------

def is_null_worthy_group(
    *,
    valid_count: int | None = None,
    mean_net_bps: float | None = None,
    candidate: bool | None = None,
    cost_floor_bps: float = DEFAULT_COST_FLOOR_BPS,
    min_events: int = DEFAULT_MIN_EVENTS,
) -> tuple[bool, str]:
    """Decide whether a group is worth null-testing.

    Returns (worthy, reason).

    A group is null-worthy when:
    - valid_count >= min_events
    - mean_net_bps is finite and not NaN
    - mean_net_bps > -(cost_floor_bps * 0.5) (not deeply negative)
    - candidate flag is True OR mean_net_bps > 0 (positive after costs)
    """
    count = valid_count if valid_count is not None else 0
    if count < min_events:
        return False, f"valid_count={count} < min_events={min_events}"

    if mean_net_bps is None:
        return False, "mean_net_bps is None"

    if not math.isfinite(mean_net_bps):
        return False, "mean_net_bps is not finite"

    if mean_net_bps <= -(cost_floor_bps * 0.5):
        return False, f"mean_net={mean_net_bps:.2f} bps, deeply negative (near cost floor)"

    if candidate is True:
        return True, "candidate_group"

    if mean_net_bps > 0:
        return True, f"positive_net_bps={mean_net_bps:.2f}"

    return False, f"non_positive_net_bps={mean_net_bps:.2f}"


# ---------------------------------------------------------------------------
# select_null_candidate_groups
# ---------------------------------------------------------------------------

def select_null_candidate_groups(
    summary_rows: list[dict[str, Any]],
    *,
    max_groups: int = 3,
    cost_floor_bps: float = DEFAULT_COST_FLOOR_BPS,
    min_events: int = DEFAULT_MIN_EVENTS,
) -> list[dict[str, Any]]:
    """Select at most *max_groups* best candidate / near-candidate groups for null testing.

    Sort priority: candidate=True first, then mean_net_bps desc.
    Deduplicate by (signal_type, lookback_ms) — keep best horizon per variant.
    """
    worthy: list[dict[str, Any]] = []
    for row in summary_rows:
        ok, reason = is_null_worthy_group(
            valid_count=row.get("valid_count"),
            mean_net_bps=row.get("mean_net_bps"),
            candidate=row.get("candidate"),
            cost_floor_bps=cost_floor_bps,
            min_events=min_events,
        )
        if ok:
            row_copy = dict(row)
            row_copy["_null_reason"] = reason
            worthy.append(row_copy)

    # Sort: candidate groups first, then mean_net_bps desc
    def _sort_key(r: dict) -> tuple:
        is_cand = 0 if r.get("candidate") is True else 1
        mnb = r.get("mean_net_bps") or 0.0
        if not math.isfinite(mnb):
            mnb = -1e9
        return (is_cand, -mnb)

    worthy.sort(key=_sort_key)

    # Deduplicate by (signal_type, lookback_ms)
    seen_variants: set[tuple[str, int]] = set()
    result: list[dict[str, Any]] = []
    for row in worthy:
        sig_type = str(row.get("signal_type", ""))
        lb = int(row.get("lookback_ms", 0))
        variant = (sig_type, lb)
        if variant in seen_variants:
            continue
        seen_variants.add(variant)
        result.append(row)
        if len(result) >= max_groups:
            break

    return result


# ---------------------------------------------------------------------------
# circular_time_shift
# ---------------------------------------------------------------------------

def circular_time_shift(
    source_event_timestamps: list[int],
    rng: random.Random,
) -> list[int]:
    """Circularly shift source event timestamps by a random offset.

    Takes sorted source event timestamps, computes the series duration
    (max - min), generates a random offset in [0, duration), and adds
    the offset to each timestamp, wrapping around modulo duration.

    Preserves total event count and relative spacing distribution.

    Returns shifted timestamps (sorted).
    """
    if len(source_event_timestamps) < 2:
        return list(source_event_timestamps)

    ts_min = min(source_event_timestamps)
    ts_max = max(source_event_timestamps)
    duration = ts_max - ts_min

    if duration <= 0:
        # All timestamps are identical; nothing to shift
        return list(source_event_timestamps)

    offset = rng.randint(0, duration - 1)

    shifted: list[int] = []
    for ts in source_event_timestamps:
        new_ts = (ts - ts_min + offset) % duration + ts_min
        shifted.append(new_ts)

    shifted.sort()
    return shifted


# ---------------------------------------------------------------------------
# block_time_shift
# ---------------------------------------------------------------------------

def block_time_shift(
    source_event_timestamps: list[int],
    block_size: int,
    rng: random.Random,
) -> list[int]:
    """Divide events into contiguous blocks and shift each block circularly.

    Preserves intra-block clustering structure better than a single global
    circular shift or naive independent shuffle.

    Parameters
    ----------
    source_event_timestamps:
        Sorted list of nanosecond timestamps.
    block_size:
        Number of consecutive events per block.
    rng:
        Seeded RNG for deterministic reproducibility.

    Returns
    -------
    list[int]
        Shifted timestamps (sorted).
    """
    if len(source_event_timestamps) < 2:
        return list(source_event_timestamps)

    ts_min = min(source_event_timestamps)
    ts_max = max(source_event_timestamps)
    duration = ts_max - ts_min

    if duration <= 0:
        return list(source_event_timestamps)

    shifted: list[int] = list(source_event_timestamps)

    n = len(shifted)
    block_idx = 0
    while block_idx < n:
        block_end = min(block_idx + block_size, n)

        # Generate a per-block random offset
        offset = rng.randint(0, duration - 1)

        for i in range(block_idx, block_end):
            new_ts = (shifted[i] - ts_min + offset) % duration + ts_min
            shifted[i] = new_ts

        block_idx = block_end

    shifted.sort()
    return shifted


# ---------------------------------------------------------------------------
# compute_null_distribution
# ---------------------------------------------------------------------------

def _percentile(sorted_values: list[float], p: float) -> float:
    """Compute percentile from a sorted list using linear interpolation."""
    if not sorted_values:
        return float("nan")
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    rank = p / 100.0 * (n - 1)
    lower = int(math.floor(rank))
    upper = lower + 1
    if upper >= n:
        return sorted_values[-1]
    frac = rank - lower
    return sorted_values[lower] + frac * (sorted_values[upper] - sorted_values[lower])


def compute_null_distribution(
    source_event_timestamps: list[int],
    target_timestamps: list[int],
    target_prices: list[float],
    direction: str,
    horizons_ms: list[int],
    fee_bps: float,
    slippage_bps: float,
    quote_mismatch_buffer_bps: float = 0.0,
    quote_mismatch: bool = False,
    iterations: int = DEFAULT_ITERATIONS,
    seed: int = DEFAULT_SEED,
    shift_mode: str = "circular_time_shift",
    block_size: int = 10,
) -> dict[str, Any]:
    """Build a null distribution by repeatedly shifting source timestamps.

    For each iteration:
        a. Apply shift_mode to get null timestamps.
        b. Create a synthetic TickSignalEvent for each shifted timestamp
           (direction from real data, source_move_bps=0).
        c. Measure forward returns on the REAL target price path using
           evaluate_tick_signal.
        d. Compute per-iteration: mean net bps, median net bps, win rate.

    Returns a dict with:
        - null_mean_net_bps:  list[float] per iteration
        - null_win_rates:     list[float] per iteration
        - null_median_net_bps: list[float] per iteration
        - percentiles for mean_net_bps and win_rate (p50, p95, p99)
    """
    rng = random.Random(seed)

    # Build lightweight target ticks for evaluate_tick_signal.
    # We pass timestamps + prices as TradeTickLite objects.
    from .tick_models import TradeTickLite as _TTL

    target_ticks = []
    for ts, pr in zip(target_timestamps, target_prices):
        target_ticks.append(
            _TTL(
                ts_event=ts,
                venue="",
                symbol="",
                price=pr,
                size=0.0,
                side="unknown",
            )
        )

    null_means: list[float] = []
    null_medians: list[float] = []
    null_win_rates: list[float] = []

    total_cost = fee_bps + slippage_bps + (quote_mismatch_buffer_bps if quote_mismatch else 0.0)

    for _iter_num in range(iterations):
        # Shift timestamps
        if shift_mode == "block_time_shift":
            shifted_ts = block_time_shift(source_event_timestamps, block_size=block_size, rng=rng)
        else:
            # Default: circular_time_shift
            shifted_ts = circular_time_shift(source_event_timestamps, rng=rng)

        # Create synthetic signals for each shifted timestamp
        synth_signals: list[TickSignalEvent] = []
        for ts in shifted_ts:
            sig = TickSignalEvent(
                signal_id=str(uuid.uuid4()),
                ts_event=ts,
                source_venue="",
                source_symbol="",
                target_venue="",
                target_symbol="",
                asset="",
                signal_type="null_permutation",
                direction=direction,
                lookback_ms=0,
                threshold_bps=0.0,
                source_move_bps=0.0,
                source_start_price=0.0,
                source_end_price=0.0,
                strength=0.0,
                metadata=None,
            )
            synth_signals.append(sig)

        # Evaluate forward returns for each synthetic signal
        all_net_returns: list[float] = []
        for sig in synth_signals:
            fwd_rets = evaluate_tick_signal(
                signal=sig,
                target_ticks=target_ticks,
                horizons_ms=horizons_ms,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
                quote_mismatch=quote_mismatch,
            )
            for fr in fwd_rets:
                if fr.valid and fr.net_return_bps is not None and math.isfinite(fr.net_return_bps):
                    all_net_returns.append(fr.net_return_bps)

        # Compute per-iteration statistics
        if all_net_returns:
            iter_mean = statistics.mean(all_net_returns)
            iter_median = statistics.median(all_net_returns)
            iter_win_rate = sum(1 for x in all_net_returns if x > 0) / len(all_net_returns)
        else:
            iter_mean = float("nan")
            iter_median = float("nan")
            iter_win_rate = float("nan")

        null_means.append(iter_mean)
        null_medians.append(iter_median)
        null_win_rates.append(iter_win_rate)

    # Compute percentiles over valid iterations
    valid_means = sorted([x for x in null_means if math.isfinite(x)])
    valid_win_rates = sorted([x for x in null_win_rates if math.isfinite(x)])

    percentiles: dict[str, float] = {}
    if valid_means:
        percentiles["mean_net_bps_p50"] = _percentile(valid_means, 50)
        percentiles["mean_net_bps_p95"] = _percentile(valid_means, 95)
        percentiles["mean_net_bps_p99"] = _percentile(valid_means, 99)
    else:
        percentiles["mean_net_bps_p50"] = float("nan")
        percentiles["mean_net_bps_p95"] = float("nan")
        percentiles["mean_net_bps_p99"] = float("nan")

    if valid_win_rates:
        percentiles["win_rate_p50"] = _percentile(valid_win_rates, 50)
        percentiles["win_rate_p95"] = _percentile(valid_win_rates, 95)
        percentiles["win_rate_p99"] = _percentile(valid_win_rates, 99)
    else:
        percentiles["win_rate_p50"] = float("nan")
        percentiles["win_rate_p95"] = float("nan")
        percentiles["win_rate_p99"] = float("nan")

    return {
        "null_mean_net_bps": null_means,
        "null_win_rates": null_win_rates,
        "null_median_net_bps": null_medians,
        "percentiles": percentiles,
        "iterations": iterations,
        "seed": seed,
        "shift_mode": shift_mode,
    }


# ---------------------------------------------------------------------------
# run_null_test_for_group
# ---------------------------------------------------------------------------

def run_null_test_for_group(
    *,
    candidate_group: dict[str, Any],
    source_event_timestamps: list[int],
    direction: str,
    target_timestamps: list[int],
    target_prices: list[float],
    horizons_ms: list[int],
    fee_bps: float,
    slippage_bps: float,
    quote_mismatch_buffer_bps: float = 0.0,
    quote_mismatch: bool = False,
    iterations: int = DEFAULT_ITERATIONS,
    seed: int = DEFAULT_SEED,
    shift_mode: str = "circular_time_shift",
    block_size: int = 10,
    min_events: int = DEFAULT_MIN_EVENTS,
    cost_floor_bps: float = DEFAULT_COST_FLOOR_BPS,
    capture_mode: str = "",
    report_dir: str = "",
    capture_dir: str = "",
) -> dict[str, Any]:
    """Run a full null/permutation test for one candidate group.

    Computes real statistics from the group dict, runs the null distribution,
    and evaluates survival criteria.

    Survival criteria (ALL must pass):
        1. real mean net bps > 0 (after costs)
        2. event_count >= min_events
        3. real_mean > null_p95
        4. p_value <= 0.05
        5. real_win_rate > null_median_win_rate

    Returns a result dict with all specified fields.
    """
    # Extract real statistics from the candidate group dict
    real_event_count = int(candidate_group.get("valid_count", 0))
    real_mean_net_bps = candidate_group.get("mean_net_bps")
    real_median_net_bps = candidate_group.get("median_net_bps")
    real_win_rate = candidate_group.get("win_rate")

    # Coerce None / non-finite to safe defaults
    if real_mean_net_bps is None or not math.isfinite(real_mean_net_bps):
        real_mean_net_bps = float("nan")
    if real_median_net_bps is None or not math.isfinite(real_median_net_bps):
        real_median_net_bps = float("nan")
    if real_win_rate is None or not math.isfinite(real_win_rate):
        real_win_rate = float("nan")

    # Extract group-identifying fields
    source_venue = str(candidate_group.get("source_venue", ""))
    target_venue = str(candidate_group.get("target_venue", ""))
    source_symbol = str(candidate_group.get("source_symbol", ""))
    target_symbol = str(candidate_group.get("target_symbol", ""))
    signal_type = str(candidate_group.get("signal_type", ""))
    lookback_ms = int(candidate_group.get("lookback_ms", 0))
    horizon_ms = int(candidate_group.get("horizon_ms", 0))

    # OI bucket from group metadata if present
    metadata = candidate_group.get("metadata") or {}
    oi_bucket = str(metadata.get("oi_bucket", ""))

    # Early exit: not enough events or non-positive mean
    if real_event_count < min_events:
        return _null_result(
            candidate_group=candidate_group,
            source_event_timestamps=source_event_timestamps,
            direction=direction,
            target_timestamps=target_timestamps,
            target_prices=target_prices,
            horizons_ms=horizons_ms,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
            quote_mismatch=quote_mismatch,
            iterations=iterations,
            seed=seed,
            shift_mode=shift_mode,
            block_size=block_size,
            min_events=min_events,
            cost_floor_bps=cost_floor_bps,
            capture_mode=capture_mode,
            report_dir=report_dir,
            capture_dir=capture_dir,
            real_event_count=real_event_count,
            real_mean_net_bps=real_mean_net_bps,
            real_median_net_bps=real_median_net_bps,
            real_win_rate=real_win_rate,
            null_dist=None,
            candidate_survives_null=False,
            reason=f"insufficient_events: {real_event_count} < {min_events}",
        )

    # Compute null distribution
    null_dist = compute_null_distribution(
        source_event_timestamps=source_event_timestamps,
        target_timestamps=target_timestamps,
        target_prices=target_prices,
        direction=direction,
        horizons_ms=horizons_ms,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
        quote_mismatch=quote_mismatch,
        iterations=iterations,
        seed=seed,
        shift_mode=shift_mode,
        block_size=block_size,
    )

    # Compute empirical p-value
    null_means = null_dist["null_mean_net_bps"]
    pctls = null_dist["percentiles"]

    valid_null_means = [x for x in null_means if math.isfinite(x)]
    valid_null_win_rates = [x for x in null_dist["null_win_rates"] if math.isfinite(x)]

    # p-value = fraction of null iterations where null_mean >= real_mean
    real_mean = real_mean_net_bps
    if math.isfinite(real_mean) and len(valid_null_means) > 0:
        count_ge = sum(1 for nm in valid_null_means if nm >= real_mean)
        p_value = count_ge / len(valid_null_means)
    else:
        p_value = 1.0

    # Percentiles
    null_mean_p50 = pctls.get("mean_net_bps_p50", float("nan"))
    null_mean_p95 = pctls.get("mean_net_bps_p95", float("nan"))
    null_mean_p99 = pctls.get("mean_net_bps_p99", float("nan"))
    null_wr_p50 = pctls.get("win_rate_p50", float("nan"))

    # Determine if real beats null
    real_beats_null = False
    if math.isfinite(real_mean) and len(valid_null_means) > 0:
        real_beats_null = real_mean > null_mean_p95

    # Evaluate survival criteria
    # 1. real mean net bps > 0
    criterion_mean_positive = math.isfinite(real_mean) and real_mean > 0

    # 2. event_count >= min_events (already checked above)

    # 3. real_mean > null_p95
    criterion_beats_p95 = (
        math.isfinite(real_mean)
        and math.isfinite(null_mean_p95)
        and real_mean > null_mean_p95
    )

    # 4. p_value <= 0.05
    criterion_p_value = p_value <= 0.05

    # 5. real_win_rate > null_median_win_rate
    criterion_win_rate = (
        math.isfinite(real_win_rate)
        and math.isfinite(null_wr_p50)
        and real_win_rate > null_wr_p50
    )

    all_pass = (
        criterion_mean_positive
        and (real_event_count >= min_events)
        and criterion_beats_p95
        and criterion_p_value
        and criterion_win_rate
    )

    # Build rejection reasons list for transparency
    reasons: list[str] = []
    if not criterion_mean_positive:
        reasons.append(f"mean_net_bps_not_positive: {real_mean:.2f}")
    if real_event_count < min_events:
        reasons.append(f"insufficient_events: {real_event_count} < {min_events}")
    if not criterion_beats_p95:
        reasons.append(
            f"real_mean_not_above_null_p95: {real_mean:.2f} vs {null_mean_p95:.2f}"
        )
    if not criterion_p_value:
        reasons.append(f"p_value_above_0.05: {p_value:.4f}")
    if not criterion_win_rate:
        reasons.append(
            f"win_rate_not_above_null_median: {real_win_rate:.4f} vs {null_wr_p50:.4f}"
        )

    if all_pass:
        final_reason = "SURVIVED_NULL_TEST"
    else:
        # Use NULL_REJECTED_DIAGNOSTIC rather than REJECTED
        final_reason = "NULL_REJECTED_DIAGNOSTIC: " + "; ".join(reasons)

    return _null_result(
        candidate_group=candidate_group,
        source_event_timestamps=source_event_timestamps,
        direction=direction,
        target_timestamps=target_timestamps,
        target_prices=target_prices,
        horizons_ms=horizons_ms,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
        quote_mismatch=quote_mismatch,
        iterations=iterations,
        seed=seed,
        shift_mode=shift_mode,
        block_size=block_size,
        min_events=min_events,
        cost_floor_bps=cost_floor_bps,
        capture_mode=capture_mode,
        report_dir=report_dir,
        capture_dir=capture_dir,
        real_event_count=real_event_count,
        real_mean_net_bps=real_mean_net_bps,
        real_median_net_bps=real_median_net_bps,
        real_win_rate=real_win_rate,
        null_dist=null_dist,
        candidate_survives_null=all_pass,
        reason=final_reason,
    )


def _null_result(
    *,
    candidate_group: dict[str, Any],
    source_event_timestamps: list[int],
    direction: str,
    target_timestamps: list[int],
    target_prices: list[float],
    horizons_ms: list[int],
    fee_bps: float,
    slippage_bps: float,
    quote_mismatch_buffer_bps: float,
    quote_mismatch: bool,
    iterations: int,
    seed: int,
    shift_mode: str,
    block_size: int,
    min_events: int,
    cost_floor_bps: float,
    capture_mode: str,
    report_dir: str,
    capture_dir: str,
    real_event_count: int,
    real_mean_net_bps: float,
    real_median_net_bps: float,
    real_win_rate: float,
    null_dist: dict[str, Any] | None,
    candidate_survives_null: bool,
    reason: str,
) -> dict[str, Any]:
    """Build the result dict for run_null_test_for_group."""
    # Extract group-identifying fields
    source_venue = str(candidate_group.get("source_venue", ""))
    target_venue = str(candidate_group.get("target_venue", ""))
    source_symbol = str(candidate_group.get("source_symbol", ""))
    target_symbol = str(candidate_group.get("target_symbol", ""))
    signal_type = str(candidate_group.get("signal_type", ""))
    lookback_ms = int(candidate_group.get("lookback_ms", 0))
    horizon_ms = int(candidate_group.get("horizon_ms", 0))

    metadata = candidate_group.get("metadata") or {}
    oi_bucket = str(metadata.get("oi_bucket", ""))

    result: dict[str, Any] = {
        "source_report_path": report_dir,
        "capture_dir": capture_dir,
        "capture_mode": capture_mode,
        "selected_group_identity": f"{source_venue}_{target_venue}_{signal_type}_{lookback_ms}ms_{horizon_ms}ms",
        "signal_type": signal_type,
        "source_venue": source_venue,
        "source_symbol": source_symbol,
        "target_venue": target_venue,
        "target_symbol": target_symbol,
        "lookback_ms": lookback_ms,
        "horizon_ms": horizon_ms,
        "oi_bucket": oi_bucket,
        "real_event_count": real_event_count,
        "real_mean_net_bps": real_mean_net_bps,
        "real_median_net_bps": real_median_net_bps,
        "real_win_rate": real_win_rate,
        "null_iterations": iterations,
        "candidate_survives_null": candidate_survives_null,
        "reason": reason,
    }

    if null_dist is not None:
        pctls = null_dist.get("percentiles", {})
        null_means = null_dist.get("null_mean_net_bps", [])
        null_win_rates = null_dist.get("null_win_rates", [])

        # Compute p-value
        valid_null_means = [x for x in null_means if math.isfinite(x)]
        valid_null_win_rates = [x for x in null_win_rates if math.isfinite(x)]

        if math.isfinite(real_mean_net_bps) and len(valid_null_means) > 0:
            count_ge = sum(1 for nm in valid_null_means if nm >= real_mean_net_bps)
            p_value = count_ge / len(valid_null_means)
        else:
            p_value = 1.0

        null_wr_p50 = pctls.get("win_rate_p50", float("nan"))

        result["null_mean_net_bps_p50"] = pctls.get("mean_net_bps_p50", float("nan"))
        result["null_mean_net_bps_p95"] = pctls.get("mean_net_bps_p95", float("nan"))
        result["null_mean_net_bps_p99"] = pctls.get("mean_net_bps_p99", float("nan"))
        result["null_win_rate_p50"] = null_wr_p50
        result["empirical_p_value"] = p_value
        result["real_beats_null"] = (
            math.isfinite(real_mean_net_bps)
            and len(valid_null_means) > 0
            and real_mean_net_bps > pctls.get("mean_net_bps_p95", float("nan"))
        )
    else:
        result["null_mean_net_bps_p50"] = float("nan")
        result["null_mean_net_bps_p95"] = float("nan")
        result["null_mean_net_bps_p99"] = float("nan")
        result["null_win_rate_p50"] = float("nan")
        result["empirical_p_value"] = 1.0
        result["real_beats_null"] = False

    return result