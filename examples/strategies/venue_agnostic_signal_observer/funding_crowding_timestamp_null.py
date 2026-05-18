"""Timestamp-shuffle null for Family 2 funding crowding reversal.

This module implements the TIMESTAMP_SHUFFLE_NULL_ONLY invariant from the
Family 2 precommitment. It tests whether the observed net reversal return
from actual funding-extreme events could plausibly arise from randomly
selected funding timestamps (preserving event count, funding cadence,
and direction convention).

Correct null hypothesis:
    Funding extremes are unrelated to subsequent returns.

Correct null operation:
    Randomly select which eligible funding timestamps are labeled as
    extreme from the full eligible funding observation calendar, while
    preserving:
    - funding cadence (via eligible calendar)
    - BTC return series (via precomputed signed net returns)
    - event count
    - horizon
    - direction convention
    - cell identity
    - no future leakage

Forbidden null operations (not implemented here):
    - sign flipping
    - funding sign randomization
    - circularly shifting only actual extreme-event timestamps
    - block-shuffling actual extreme-event timestamps
    - randomizing forward returns directly
    - shuffling prices
    - changing thresholds, horizons, gates, FDR family size, costs,
      or direction conventions

SIGNED_RETURNS_ARE_PRE_NET:
    The signed_net_returns_by_timestamp_bps field contains returns that
    already have direction applied and costs subtracted. Positive values
    mean favorable reversal return after costs. The null module must NOT
    subtract cost_bps again.

Pure functions only. No network, no capture, no live imports, no GPU.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_ITERATIONS: int = 1000
DEFAULT_SEED: int = 42
MINIMUM_ITERATIONS: int = 100
ACTUAL_MEAN_TOLERANCE: float = 1e-9
ALPHA: float = 0.05

# ---------------------------------------------------------------------------
# Status values
# ---------------------------------------------------------------------------

STATUS_TIMESTAMP_SHUFFLE_NULL_READY: str = "TIMESTAMP_SHUFFLE_NULL_READY"
STATUS_TIMESTAMP_SHUFFLE_NULL_SURVIVED: str = "TIMESTAMP_SHUFFLE_NULL_SURVIVED"
STATUS_TIMESTAMP_SHUFFLE_NULL_REJECTED_DIAGNOSTIC: str = (
    "TIMESTAMP_SHUFFLE_NULL_REJECTED_DIAGNOSTIC"
)
STATUS_TIMESTAMP_SHUFFLE_NULL_UNDERPOWERED: str = (
    "TIMESTAMP_SHUFFLE_NULL_UNDERPOWERED"
)
STATUS_TIMESTAMP_SHUFFLE_NULL_INVALID_INPUT: str = (
    "TIMESTAMP_SHUFFLE_NULL_INVALID_INPUT"
)

# ---------------------------------------------------------------------------
# Diagnostic labels
# ---------------------------------------------------------------------------

NEAR_EXHAUSTIVE_ELIGIBLE_CALENDAR_DIAGNOSTIC: str = (
    "NEAR_EXHAUSTIVE_ELIGIBLE_CALENDAR_DIAGNOSTIC"
)
ACTUAL_MEAN_NET_BPS_MISMATCH: str = "ACTUAL_MEAN_NET_BPS_MISMATCH"

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TimestampShuffleNullInput:
    """Input for one frozen cell's timestamp-shuffle null test.

    Parameters
    ----------
    cell_id:
        Unique identifier for this frozen cell (e.g., ``"BTC/abs_funding_ge_5bp/h24/
        positive_funding_extreme"``).
    eligible_timestamps_ns:
        All eligible funding observation timestamps (nanoseconds). This is
        the full eligible calendar from which random draws are made.
    actual_event_timestamps_ns:
        The subset of eligible timestamps that were actually selected as
        extreme events by the frozen threshold rule. Must be a subset of
        eligible_timestamps_ns.
    signed_net_returns_by_timestamp_bps:
        Precomputed signed net reversal returns keyed by timestamp (ns).
        Direction has already been applied and costs have already been
        subtracted. Positive values mean favorable reversal return after
        costs. Must cover all timestamps in both eligible_timestamps_ns
        and actual_event_timestamps_ns.
    actual_mean_net_bps:
        The mean of signed_net_returns_by_timestamp_bps over the actual
        event timestamps. Passed in as a convenience; validated against
        recomputation.
    event_count:
        Number of actual extreme events (len(actual_event_timestamps_ns)).
    horizon_hours:
        Forward-return horizon in hours (metadata / traceability).
    direction:
        Direction label, e.g. ``"positive_funding_extreme"``. Metadata only.
        The direction is already encoded in signed_net_returns_by_timestamp_bps.
    iterations:
        Number of null iterations.
    seed:
        RNG seed for deterministic reproducibility.
    cost_bps:
        Total cost in bps (metadata only). NOT subtracted from returns
        here. Returns are already pre-net of cost.
    """

    cell_id: str
    eligible_timestamps_ns: tuple[int, ...]
    actual_event_timestamps_ns: tuple[int, ...]
    signed_net_returns_by_timestamp_bps: dict[int, float]
    actual_mean_net_bps: float
    event_count: int
    horizon_hours: int = 24
    direction: str = ""
    iterations: int = DEFAULT_ITERATIONS
    seed: int = DEFAULT_SEED
    cost_bps: float = 0.0


@dataclass(frozen=True)
class TimestampShuffleNullResult:
    """Result of one frozen cell's timestamp-shuffle null test.

    Parameters
    ----------
    cell_id:
        Matches the input cell_id.
    status:
        One of the STATUS_* constants above.
    iterations:
        Number of null iterations performed.
    seed:
        RNG seed used.
    event_count:
        Number of actual extreme events.
    eligible_count:
        Size of the eligible funding observation calendar.
    eligible_to_event_ratio:
        eligible_count / event_count. If small, the null distribution
        may overlap heavily with the actual draw.
    actual_mean_net_bps:
        The actual mean signed net return (validated).
    actual_recomputed_mean_net_bps:
        The recomputed mean from actual_event_timestamps_ns and
        signed_net_returns_by_timestamp_bps. Should match actual_mean_net_bps.
    null_mean_bps_p50:
        Median of the null distribution of mean signed net returns.
    null_mean_bps_p95:
        95th percentile of the null distribution.
    null_mean_bps_p99:
        99th percentile of the null distribution.
    empirical_p_value:
        One-sided p-value: (count(null_mean >= actual_mean) + 1) / (iterations + 1).
    survives_null:
        True only if p_value <= alpha, actual_mean > null_p95,
        event/eligible counts are valid, and actual mean consistency passes.
    reason:
        Human-readable summary of the outcome, including any diagnostic
        warnings (e.g., near-exhaustive eligible calendar).
    """

    cell_id: str
    status: str
    iterations: int
    seed: int
    event_count: int
    eligible_count: int
    eligible_to_event_ratio: float
    actual_mean_net_bps: float
    actual_recomputed_mean_net_bps: float
    null_mean_bps_p50: float
    null_mean_bps_p95: float
    null_mean_bps_p99: float
    empirical_p_value: float
    survives_null: bool
    reason: str
    # Full null distribution for downstream diagnostics (optional)
    null_mean_distribution: tuple[float, ...] = ()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _percentile_from_sorted(sorted_values: list[float], p: float) -> float:
    """Compute percentile from a sorted list using linear interpolation.

    Same method used by permutation_null.py for consistency.
    """
    n = len(sorted_values)
    if n == 0:
        return float("nan")
    if n == 1:
        return sorted_values[0]
    rank = p / 100.0 * (n - 1)
    lower = int(math.floor(rank))
    upper = min(lower + 1, n - 1)
    frac = rank - lower
    return sorted_values[lower] + frac * (sorted_values[upper] - sorted_values[lower])


def _validate_input(input_data: TimestampShuffleNullInput) -> str | None:
    """Validate input and return an error reason, or None if valid."""
    # --- iteration count ---
    if input_data.iterations <= 0:
        return "iterations must be positive"
    if input_data.iterations < MINIMUM_ITERATIONS:
        return (
            f"iterations={input_data.iterations} < {MINIMUM_ITERATIONS} "
            f"(minimum for meaningful p-value)"
        )

    # --- event count ---
    if input_data.event_count <= 0:
        return "event_count must be positive"
    if input_data.event_count != len(input_data.actual_event_timestamps_ns):
        return (
            f"event_count={input_data.event_count} does not match "
            f"actual_event_timestamps_ns length={len(input_data.actual_event_timestamps_ns)}"
        )

    # --- eligible timestamps ---
    if not input_data.eligible_timestamps_ns:
        return "eligible_timestamps_ns is empty"

    if not input_data.actual_event_timestamps_ns:
        return "actual_event_timestamps_ns is empty"

    # --- event count vs eligible count ---
    if input_data.event_count > len(input_data.eligible_timestamps_ns):
        return (
            f"event_count={input_data.event_count} exceeds "
            f"eligible_count={len(input_data.eligible_timestamps_ns)}"
        )

    # --- duplicate eligible timestamps ---
    if len(set(input_data.eligible_timestamps_ns)) != len(input_data.eligible_timestamps_ns):
        return "duplicate timestamps in eligible_timestamps_ns"

    # --- duplicate actual event timestamps ---
    if len(set(input_data.actual_event_timestamps_ns)) != len(input_data.actual_event_timestamps_ns):
        return "duplicate timestamps in actual_event_timestamps_ns"

    # --- actual events must be subset of eligible ---
    eligible_set = set(input_data.eligible_timestamps_ns)
    actual_set = set(input_data.actual_event_timestamps_ns)
    if not actual_set.issubset(eligible_set):
        missing = actual_set - eligible_set
        return f"actual_event_timestamps_ns contains timestamps not in eligible_set: {len(missing)} missing"

    # --- validate signed net returns ---
    returns = input_data.signed_net_returns_by_timestamp_bps

    # Check all eligible timestamps have returns
    missing_returns_eligible = [ts for ts in input_data.eligible_timestamps_ns if ts not in returns]
    if missing_returns_eligible:
        return f"{len(missing_returns_eligible)} eligible timestamps missing signed net returns"

    # Check all actual event timestamps have returns
    missing_returns_actual = [ts for ts in input_data.actual_event_timestamps_ns if ts not in returns]
    if missing_returns_actual:
        return f"{len(missing_returns_actual)} actual event timestamps missing signed net returns"

    # Check for NaN / inf
    for ts, val in returns.items():
        if not math.isfinite(val):
            return f"non-finite signed net return at timestamp {ts}: {val}"

    # --- actual_mean_net_bps ---
    if not math.isfinite(input_data.actual_mean_net_bps):
        return f"actual_mean_net_bps is not finite: {input_data.actual_mean_net_bps}"

    # --- recompute actual mean ---
    actual_vals = [returns[ts] for ts in input_data.actual_event_timestamps_ns]
    recomputed_mean = sum(actual_vals) / len(actual_vals)
    if abs(recomputed_mean - input_data.actual_mean_net_bps) > ACTUAL_MEAN_TOLERANCE:
        return (
            f"{ACTUAL_MEAN_NET_BPS_MISMATCH}: passed actual_mean_net_bps="
            f"{input_data.actual_mean_net_bps} but recomputed="
            f"{recomputed_mean} from actual_event_timestamps_ns "
            f"(tolerance={ACTUAL_MEAN_TOLERANCE})"
        )

    # --- cost_bps ---
    if not math.isfinite(input_data.cost_bps):
        return f"cost_bps is not finite: {input_data.cost_bps}"

    return None


# ---------------------------------------------------------------------------
# Core null computation
# ---------------------------------------------------------------------------


def run_timestamp_shuffle_null(
    input_data: TimestampShuffleNullInput,
) -> TimestampShuffleNullResult:
    """Run the timestamp-shuffle null test for one frozen cell.

    Parameters
    ----------
    input_data:
        Precomputed input for this cell. Must satisfy all validation
        checks defined in _validate_input.

    Returns
    -------
    TimestampShuffleNullResult
        The null test result with diagnostic metadata.

    Notes
    -----
    The null sampling algorithm:

    1. Create a local RNG from input_data.seed.
    2. For each iteration, randomly sample ``event_count`` distinct
       timestamps from ``eligible_timestamps_ns`` (without replacement
       within the iteration).
    3. Read precomputed signed net returns for the sampled timestamps.
    4. Compute the sampled null mean net return.
    5. Build a null distribution of ``iterations`` sampled mean returns.
    6. Compute one-sided empirical p-value:
       (count(null_mean >= actual_mean) + 1) / (iterations + 1)
    7. Determine survival based on p-value and secondary gates.
    """
    # --- Validate ---
    error = _validate_input(input_data)
    if error is not None:
        return TimestampShuffleNullResult(
            cell_id=input_data.cell_id,
            status=STATUS_TIMESTAMP_SHUFFLE_NULL_INVALID_INPUT,
            iterations=input_data.iterations,
            seed=input_data.seed,
            event_count=input_data.event_count,
            eligible_count=len(input_data.eligible_timestamps_ns),
            eligible_to_event_ratio=_safe_ratio(
                len(input_data.eligible_timestamps_ns), input_data.event_count
            ),
            actual_mean_net_bps=input_data.actual_mean_net_bps,
            actual_recomputed_mean_net_bps=float("nan"),
            null_mean_bps_p50=float("nan"),
            null_mean_bps_p95=float("nan"),
            null_mean_bps_p99=float("nan"),
            empirical_p_value=float("nan"),
            survives_null=False,
            reason=f"INVALID_INPUT: {error}",
        )

    eligible_timestamps = list(input_data.eligible_timestamps_ns)
    eligible_count = len(eligible_timestamps)
    event_count = input_data.event_count
    returns = input_data.signed_net_returns_by_timestamp_bps

    # --- Recompute actual mean ---
    actual_vals = [returns[ts] for ts in input_data.actual_event_timestamps_ns]
    recomputed_mean = sum(actual_vals) / len(actual_vals)

    # --- Compute eligible-to-event ratio ---
    ratio = _safe_ratio(eligible_count, event_count)

    # --- Run null iterations ---
    rng = random.Random(input_data.seed)
    null_means: list[float] = []

    for _ in range(input_data.iterations):
        # Sample without replacement
        sampled_ts = rng.sample(eligible_timestamps, event_count)
        sampled_vals = [returns[ts] for ts in sampled_ts]
        iter_mean = sum(sampled_vals) / event_count
        null_means.append(iter_mean)

    # --- Compute percentiles of null distribution ---
    sorted_null = sorted(null_means)
    p50 = _percentile_from_sorted(sorted_null, 50)
    p95 = _percentile_from_sorted(sorted_null, 95)
    p99 = _percentile_from_sorted(sorted_null, 99)

    # --- Compute one-sided empirical p-value ---
    actual_mean = input_data.actual_mean_net_bps
    count_ge = sum(1 for m in null_means if m >= actual_mean)
    p_value = (count_ge + 1) / (input_data.iterations + 1)

    # --- Determine survival ---
    # Survival requires:
    # 1. p_value <= ALPHA (0.05)
    # 2. actual_mean > null_p95
    # 3. All values finite
    all_finite = (
        math.isfinite(p50)
        and math.isfinite(p95)
        and math.isfinite(p99)
        and math.isfinite(p_value)
        and math.isfinite(actual_mean)
        and math.isfinite(recomputed_mean)
    )

    survives = (
        all_finite
        and p_value <= ALPHA
        and actual_mean > p95
        and event_count > 0
        and eligible_count > 0
    )

    # --- Build reason ---
    reason_parts: list[str] = []

    if survives:
        status = STATUS_TIMESTAMP_SHUFFLE_NULL_SURVIVED
        reason_parts.append(
            f"Null survived: p={p_value:.4f} <= {ALPHA}, "
            f"actual_mean={actual_mean:.4f} > p95={p95:.4f}"
        )
    else:
        if not all_finite:
            reason_parts.append("Non-finite values in null distribution")
            status = STATUS_TIMESTAMP_SHUFFLE_NULL_UNDERPOWERED
        elif p_value > ALPHA:
            reason_parts.append(
                f"p_value={p_value:.4f} > {ALPHA}: actual mean not extreme "
                f"in null distribution"
            )
            status = STATUS_TIMESTAMP_SHUFFLE_NULL_REJECTED_DIAGNOSTIC
        elif actual_mean <= p95:
            reason_parts.append(
                f"actual_mean={actual_mean:.4f} <= p95={p95:.4f}: "
                f"not above null 95th percentile"
            )
            status = STATUS_TIMESTAMP_SHUFFLE_NULL_REJECTED_DIAGNOSTIC
        else:
            reason_parts.append("Null survival criteria not met")
            status = STATUS_TIMESTAMP_SHUFFLE_NULL_REJECTED_DIAGNOSTIC

    # --- Diagnostic: near-exhaustive eligible calendar ---
    if ratio >= 1.0 and ratio < 2.0:
        reason_parts.append(
            f"{NEAR_EXHAUSTIVE_ELIGIBLE_CALENDAR_DIAGNOSTIC}: "
            f"eligible_to_event_ratio={ratio:.4f}"
        )

    if status == STATUS_TIMESTAMP_SHUFFLE_NULL_SURVIVED:
        reason_parts.append(f"event_count={event_count}, eligible_count={eligible_count}")

    return TimestampShuffleNullResult(
        cell_id=input_data.cell_id,
        status=status,
        iterations=input_data.iterations,
        seed=input_data.seed,
        event_count=event_count,
        eligible_count=eligible_count,
        eligible_to_event_ratio=ratio,
        actual_mean_net_bps=actual_mean,
        actual_recomputed_mean_net_bps=recomputed_mean,
        null_mean_bps_p50=p50,
        null_mean_bps_p95=p95,
        null_mean_bps_p99=p99,
        empirical_p_value=p_value,
        survives_null=survives,
        reason="; ".join(reason_parts),
        null_mean_distribution=tuple(null_means),
    )


def _safe_ratio(numerator: int, denominator: int) -> float:
    """Compute numerator / denominator safely, returning nan if invalid."""
    if denominator <= 0:
        return float("nan")
    return numerator / denominator
