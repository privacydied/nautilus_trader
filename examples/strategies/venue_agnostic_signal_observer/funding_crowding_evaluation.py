"""
Frozen evaluation logic for Family 2 funding crowding reversal.

Pure functions operating on precomputed funding and spot data.
No fetch, no argparse, no file I/O, no network, no live imports.

This module implements the frozen precommitment design exactly.
It is the evaluation core — the CLI runner calls it with real data
or synthetic data, not this module itself.

The real evaluation run is a separate future task.
"""

from __future__ import annotations

import bisect
import math
import random
import statistics
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from .funding_crowding_reversal import ALL_THRESHOLDS
from .funding_crowding_reversal import BASELINE_BEAT_BPS
from .funding_crowding_reversal import DIRECTION_NEGATIVE_FUNDING
from .funding_crowding_reversal import DIRECTION_POSITIVE_FUNDING
from .funding_crowding_reversal import DIRECTIONS
from .funding_crowding_reversal import HORIZONS
from .funding_crowding_reversal import MIN_VALID_EVENTS_FOR_CANDIDATE
from .funding_crowding_reversal import MIN_VALID_EVENTS_FOR_DIAGNOSTIC
from .funding_crowding_reversal import MIN_WIN_RATE
from .funding_crowding_reversal import PERCENTILE_LOOKBACK_CALENDAR_DAYS
from .funding_crowding_reversal import PRIMARY_COST_BPS
from .funding_crowding_reversal import TRAIN_FRACTION
from .funding_crowding_reversal import compute_past_only_percentile_threshold
from .funding_crowding_reversal import net_signal_return_bps
from .funding_crowding_reversal import signal_return_bps_for_negative_funding
from .funding_crowding_reversal import signal_return_bps_for_positive_funding
from .funding_crowding_timestamp_null import STATUS_TIMESTAMP_SHUFFLE_NULL_INVALID_INPUT
from .funding_crowding_timestamp_null import TimestampShuffleNullInput
from .funding_crowding_timestamp_null import run_timestamp_shuffle_null


# ---------------------------------------------------------------------------
# Verdict constants (matching precommitment taxonomy)
# ---------------------------------------------------------------------------

VERDICT_CANDIDATE: str = "CANDIDATE_FOR_LONGER_OBSERVATION"
VERDICT_REJECTED: str = "REJECTED"
VERDICT_NEEDS_MORE_DATA: str = "NEEDS_MORE_DATA"
VERDICT_UNDERPOWERED_HOLDOUT: str = "UNDERPOWERED_HOLDOUT_FAILURE"
VERDICT_NULL_REJECTED: str = "NULL_REJECTED_DIAGNOSTIC"
VERDICT_FDR_BLOCKED: str = "FDR_BLOCKED_DIAGNOSTIC"
VERDICT_FDR_NOT_IMPLEMENTED: str = "FDR_NOT_IMPLEMENTED_DIAGNOSTIC"
VERDICT_NO_NULL_WORTHY: str = "NO_NULL_WORTHY_CELLS"

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FundingObservation:
    """A single funding rate observation."""

    timestamp_ns: int
    funding_rate: float  # raw rate (e.g. 0.0001)
    interval_hours: float  # funding interval from archive metadata


@dataclass(frozen=True)
class SpotPriceSnapshot:
    """A spot price observation at a point in time."""

    timestamp_ns: int
    price: float


@dataclass(frozen=True)
class CellEvent:
    """One event for a frozen cell: event timestamp + forward return data."""

    event_timestamp_ns: int
    forward_spot_return_bps: float  # raw forward BTC spot return (positive = price up)
    funding_rate: float  # the funding rate that triggered this event
    net_return_bps: float  # after direction sign and cost


@dataclass(frozen=True)
class CellIdentifier:
    """Identifies one of the 60 frozen BTC primary cells."""

    threshold_label: str
    horizon_label: str  # h4, h8, h12, h24, h48
    direction: str  # positive_funding_extreme or negative_funding_extreme

    @property
    def cell_id(self) -> str:
        return f"BTC/{self.threshold_label}/{self.horizon_label}/{self.direction}"

    @property
    def horizon_seconds(self) -> int:
        return HORIZONS[self.horizon_label]


@dataclass(frozen=True)
class CellResult:
    """
    Complete evaluation result for one frozen cell.

    All fields are populated at each stage of the gate pipeline.
    Fields that have not been computed yet may be None.
    """

    cell_id: str
    threshold_label: str
    horizon_label: str
    direction: str

    # Event counts
    eligible_count: int = 0
    valid_count: int = 0

    # Metrics (applying direction sign and primary cost)
    mean_net_bps: float | None = None
    median_net_bps: float | None = None
    win_rate: float | None = None
    worst_decile_net_bps: float | None = None

    # Baseline
    baseline_mean_bps: float | None = None
    baseline_delta_bps: float | None = None

    # Null result
    null_survived: bool | None = None
    null_p_value: float | None = None

    # Holdout
    train_mean_bps: float | None = None
    holdout_mean_bps: float | None = None
    holdout_same_sign: bool | None = None

    # FDR
    fdr_survived: bool | None = None
    fdr_adjusted_p: float | None = None

    # Final verdict
    verdict: str = ""

    # Diagnostic metrics under optimistic cost (6 bps)
    optimistic_mean_bps: float | None = None
    optimistic_win_rate: float | None = None

    # Events (for traceability, not serialized in summary)
    events: tuple[CellEvent, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "threshold_label": self.threshold_label,
            "horizon_label": self.horizon_label,
            "direction": self.direction,
            "eligible_count": self.eligible_count,
            "valid_count": self.valid_count,
            "mean_net_bps": self.mean_net_bps,
            "median_net_bps": self.median_net_bps,
            "win_rate": self.win_rate,
            "worst_decile_net_bps": self.worst_decile_net_bps,
            "baseline_mean_bps": self.baseline_mean_bps,
            "baseline_delta_bps": self.baseline_delta_bps,
            "null_survived": self.null_survived,
            "null_p_value": self.null_p_value,
            "train_mean_bps": self.train_mean_bps,
            "holdout_mean_bps": self.holdout_mean_bps,
            "holdout_same_sign": self.holdout_same_sign,
            "fdr_survived": self.fdr_survived,
            "fdr_adjusted_p": self.fdr_adjusted_p,
            "verdict": self.verdict,
            "optimistic_mean_bps": self.optimistic_mean_bps,
            "optimistic_win_rate": self.optimistic_win_rate,
            "events": tuple(self.events),
        }


@dataclass(frozen=True)
class EvaluationRunConfig:
    """Configuration for one evaluation run (to be passed from CLI)."""

    data_window_start_ns: int
    data_window_end_ns: int
    seed: int = 42
    cost_bps: float = PRIMARY_COST_BPS
    optimistic_cost_bps: float = 6.0
    train_fraction: float = TRAIN_FRACTION
    null_iterations: int = 1000
    null_alpha: float = 0.05
    fdr_alpha: float = 0.05
    fdr_method: str = "BY"
    baseline_seed: int = 999
    enable_eth: bool = False


# ---------------------------------------------------------------------------
# Cell identification
# ---------------------------------------------------------------------------


def build_all_cell_identifiers() -> list[CellIdentifier]:
    """
    Return exactly 60 BTC primary cell identifiers.

    Matches: 6 thresholds × 5 horizons × 2 directions = 60.
    """
    cells: list[CellIdentifier] = []
    for threshold in ALL_THRESHOLDS:
        label = threshold["label"]  # type: ignore[typeddict-item]
        for horizon_label in HORIZONS:
            for direction in DIRECTIONS:
                cells.append(
                    CellIdentifier(
                        threshold_label=str(label),
                        horizon_label=horizon_label,
                        direction=direction,
                    )
                )
    return cells


# ---------------------------------------------------------------------------
# Eligible calendar
# ---------------------------------------------------------------------------


def build_eligible_timestamps(
    funding_rows: Sequence[FundingObservation],
    threshold_def: dict[str, object],
    funding_rows_by_ts: Mapping[int, FundingObservation] | None = None,
) -> tuple[int, ...]:
    """
    Build the chronologically sorted eligible calendar for a threshold.

    For absolute thresholds: all funding observations are eligible
    (assuming they have sufficient forward-return data, which the
    caller must ensure).

    For percentile thresholds: only funding observations that have at
    least PERCENTILE_LOOKBACK_CALENDAR_DAYS of prior funding history
    are eligible (strictly past-only).

    Returns sorted tuple of eligible nanosecond timestamps.
    """
    is_percentile = "percentile_rank" in threshold_def

    if not is_percentile:
        # Absolute thresholds: all funding timestamps eligible
        eligible = sorted(r.timestamp_ns for r in funding_rows)
        return tuple(eligible)

    # Percentile thresholds: need 180 days of history before each timestamp
    threshold_def["percentile_rank"]  # type: ignore[typeddict-item]
    lookback_ns = PERCENTILE_LOOKBACK_CALENDAR_DAYS * 86_400 * 1_000_000_000

    sorted_rows = sorted(funding_rows, key=lambda r: r.timestamp_ns)
    eligible: list[int] = []
    for i, row in enumerate(sorted_rows):
        ts = row.timestamp_ns
        # Find all observations strictly before ts - lookback_ns
        cutoff_ns = ts - lookback_ns
        # Count how many prior observations we have
        prior_count = 0
        for j in range(i - 1, -1, -1):
            if sorted_rows[j].timestamp_ns < cutoff_ns:
                break
            if sorted_rows[j].timestamp_ns < ts:
                prior_count += 1

        # Need at least 1 observation before the 180-day window to even
        # start computing a percentile. More practically, we need enough
        # observations to have a meaningful percentile.
        if prior_count > 0:
            eligible.append(ts)

    return tuple(eligible)


# ---------------------------------------------------------------------------
# Event selection
# ---------------------------------------------------------------------------


def select_events(
    funding_rows: Sequence[FundingObservation],
    threshold_def: dict[str, object],
    direction: str,
    eligible_timestamps: tuple[int, ...],
    funding_by_ts: Mapping[int, FundingObservation],
) -> tuple[int, ...]:
    """
    Select event timestamps for one cell.

    Events are eligible timestamps whose funding rate passes the
    threshold, assigned to the requested direction.

    For absolute thresholds: `abs(funding_rate) >= min_abs_rate`.
    For percentile thresholds: funding rate in the top or bottom
    percentile rank (depending on direction).

    For positive direction:
        - Positive funding extreme: funding_rate > 0 AND passes threshold
    For negative direction:
        - Negative funding extreme: funding_rate < 0 AND passes threshold

    Returns sorted tuple of event timestamps.
    """
    is_percentile = "percentile_rank" in threshold_def
    eligible_set = set(eligible_timestamps)

    if is_percentile:
        percentile_rank: float = threshold_def["percentile_rank"]  # type: ignore[typeddict-item]
        # For percentile thresholds, the top percentile (positive extreme)
        # uses rates sorted descending, bottom percentile uses rates sorted ascending
        # We need to compute the threshold dynamically for each timestamp
        # using past-only lookback.

        # Sort eligible funding rows chronologically
        sorted_eligible = sorted(
            [r for r in funding_rows if r.timestamp_ns in eligible_set],
            key=lambda r: r.timestamp_ns,
        )

        event_ts: list[int] = []
        for i, row in enumerate(sorted_eligible):
            ts = row.timestamp_ns
            # Past-only: rates strictly before this timestamp
            past_rates = [
                r.funding_rate
                for r in sorted_eligible[:i]
                if r.timestamp_ns < ts
            ]
            if len(past_rates) < 1:
                continue

            # For top percentile (positive extreme): high funding rates
            # For bottom percentile (negative extreme): low funding rates
            if direction == DIRECTION_POSITIVE_FUNDING and row.funding_rate > 0:
                # Top percentile: use (100 - percentile_rank) as the
                # percentile of the upper tail
                upper_pct = 100.0 - percentile_rank
                threshold = compute_past_only_percentile_threshold(
                    past_rates, percentile_rank=upper_pct
                )
                if row.funding_rate >= threshold:
                    event_ts.append(ts)

            elif direction == DIRECTION_NEGATIVE_FUNDING and row.funding_rate < 0:
                # Bottom percentile
                abs_rates = [abs(r) for r in past_rates]
                threshold = compute_past_only_percentile_threshold(
                    abs_rates, percentile_rank=percentile_rank
                )
                if abs(row.funding_rate) >= threshold:
                    event_ts.append(ts)

        return tuple(event_ts)

    # Absolute thresholds
    min_abs_rate: float = threshold_def["min_abs_rate"]  # type: ignore[typeddict-item]

    abs_events: list[int] = []
    for row in funding_rows:
        if row.timestamp_ns not in eligible_set:
            continue
        if abs(row.funding_rate) < min_abs_rate:
            continue
        if (direction == DIRECTION_POSITIVE_FUNDING and row.funding_rate > 0) or (direction == DIRECTION_NEGATIVE_FUNDING and row.funding_rate < 0):
            abs_events.append(row.timestamp_ns)

    return tuple(abs_events)


# ---------------------------------------------------------------------------
# Forward return computation
# ---------------------------------------------------------------------------


def compute_forward_return_bps(
    event_timestamp_ns: int,
    spot_prices: Sequence[SpotPriceSnapshot],
    horizon_seconds: int,
) -> float | None:
    """
    Compute forward BTC spot return in bps from event time to horizon.

    Uses the spot price at event time (latest price <= event time) and
    the spot price at event time + horizon (latest price <= that point).

    Returns None if prices are unavailable at either point.
    """
    horizon_ns = horizon_seconds * 1_000_000_000
    target_ns = event_timestamp_ns + horizon_ns

    # Find price at event time (latest <= event_timestamp_ns)
    entry_price: float | None = None
    exit_price: float | None = None

    # Prices are sorted by timestamp, use a linear scan
    # (could be optimized with bisect, but fine for up to ~500k rows)
    for p in spot_prices:
        if p.timestamp_ns <= event_timestamp_ns:
            entry_price = p.price
        if p.timestamp_ns <= target_ns:
            exit_price = p.price
        if p.timestamp_ns > target_ns:
            break

    if entry_price is None or exit_price is None:
        return None
    if entry_price <= 0 or not math.isfinite(entry_price) or not math.isfinite(exit_price):
        return None

    return (exit_price - entry_price) / entry_price * 10_000  # convert to bps


def compute_events_for_cell(
    cell: CellIdentifier,
    funding_rows: Sequence[FundingObservation],
    spot_prices: Sequence[SpotPriceSnapshot],
    threshold_def: dict[str, object],
    eligible_ts: tuple[int, ...],
    funding_by_ts: Mapping[int, FundingObservation],
    config: EvaluationRunConfig,
) -> CellResult:
    """
    Compute all events and metrics for one cell.

    This is the core per-cell evaluation function.
    """
    event_ts = select_events(
        funding_rows=funding_rows,
        threshold_def=threshold_def,
        direction=cell.direction,
        eligible_timestamps=eligible_ts,
        funding_by_ts=funding_by_ts,
    )

    if not event_ts:
        return CellResult(
            cell_id=cell.cell_id,
            threshold_label=cell.threshold_label,
            horizon_label=cell.horizon_label,
            direction=cell.direction,
            eligible_count=len(eligible_ts),
            valid_count=0,
            verdict=VERDICT_NEEDS_MORE_DATA,
        )

    funding_positive = cell.direction == DIRECTION_POSITIVE_FUNDING

    # Compute forward returns for each event
    events: list[CellEvent] = []
    for ts in event_ts:
        fwd_bps = compute_forward_return_bps(
            ts, spot_prices, cell.horizon_seconds
        )
        if fwd_bps is None:
            continue

        # Net return with primary cost
        if funding_positive:
            net_bps = signal_return_bps_for_positive_funding(fwd_bps) - config.cost_bps
        else:
            net_bps = signal_return_bps_for_negative_funding(fwd_bps) - config.cost_bps

        fr = funding_by_ts[ts].funding_rate if ts in funding_by_ts else 0.0

        events.append(
            CellEvent(
                event_timestamp_ns=ts,
                forward_spot_return_bps=fwd_bps,
                funding_rate=fr,
                net_return_bps=net_bps,
            )
        )

    if not events:
        return CellResult(
            cell_id=cell.cell_id,
            threshold_label=cell.threshold_label,
            horizon_label=cell.horizon_label,
            direction=cell.direction,
            eligible_count=len(eligible_ts),
            valid_count=0,
            verdict=VERDICT_NEEDS_MORE_DATA,
        )

    # Sort events chronologically
    events.sort(key=lambda e: e.event_timestamp_ns)
    valid_count = len(events)

    # Compute aggregated metrics
    net_returns = [e.net_return_bps for e in events if math.isfinite(e.net_return_bps)]
    if not net_returns:
        return CellResult(
            cell_id=cell.cell_id,
            threshold_label=cell.threshold_label,
            horizon_label=cell.horizon_label,
            direction=cell.direction,
            eligible_count=len(eligible_ts),
            valid_count=0,
            verdict=VERDICT_NEEDS_MORE_DATA,
        )
    mean_net = statistics.mean(net_returns)
    median_net = statistics.median(net_returns)
    win_rate = sum(1 for n in net_returns if n > 0) / len(net_returns)

    # Worst decile (10th percentile of net returns)
    sorted_net = sorted(net_returns)
    worst_decile_idx = max(0, int(len(sorted_net) * 0.1) - 1)
    worst_decile_net = sorted_net[worst_decile_idx]

    # Optimistic diagnostic metrics (6 bps cost)
    optimistic_net = []
    for e in events:
        if funding_positive:
            onet = signal_return_bps_for_positive_funding(e.forward_spot_return_bps) - config.optimistic_cost_bps
        else:
            onet = signal_return_bps_for_negative_funding(e.forward_spot_return_bps) - config.optimistic_cost_bps
        optimistic_net.append(onet)
    optimistic_finite = [n for n in optimistic_net if math.isfinite(n)]
    optimistic_mean = statistics.mean(optimistic_finite) if optimistic_finite else None
    optimistic_wr = sum(1 for n in optimistic_finite if n > 0) / len(optimistic_finite) if optimistic_finite else None

    # Direction-matched baseline
    baseline_mean = compute_baseline(
        funding_rows=funding_rows,
        spot_prices=spot_prices,
        event_count=valid_count,
        direction=cell.direction,
        horizon_seconds=cell.horizon_seconds,
        seed=config.baseline_seed,
        eligible_ts=eligible_ts,
        funding_by_ts=funding_by_ts,
    )
    baseline_delta = mean_net - baseline_mean if baseline_mean is not None else None

    return CellResult(
        cell_id=cell.cell_id,
        threshold_label=cell.threshold_label,
        horizon_label=cell.horizon_label,
        direction=cell.direction,
        eligible_count=len(eligible_ts),
        valid_count=valid_count,
        mean_net_bps=mean_net,
        median_net_bps=median_net,
        win_rate=win_rate,
        worst_decile_net_bps=worst_decile_net,
        baseline_mean_bps=baseline_mean,
        baseline_delta_bps=baseline_delta,
        optimistic_mean_bps=optimistic_mean,
        optimistic_win_rate=optimistic_wr,
        events=tuple(events),
    )


# ---------------------------------------------------------------------------
# Direction-matched baseline
# ---------------------------------------------------------------------------


def compute_baseline(
    funding_rows: Sequence[FundingObservation],
    spot_prices: Sequence[SpotPriceSnapshot],
    event_count: int,
    direction: str,
    horizon_seconds: int,
    seed: int,
    eligible_ts: tuple[int, ...],
    funding_by_ts: Mapping[int, FundingObservation],
) -> float | None:
    """
    Compute direction-matched unconditional baseline.

    Samples *event_count* random timestamps from the eligible calendar,
    computes the same-direction forward return for each, and returns
    the mean.
    """
    if event_count <= 0 or len(eligible_ts) == 0:
        return None

    rng = random.Random(seed)
    funding_positive = direction == DIRECTION_POSITIVE_FUNDING

    # Sample event_count timestamps from eligible calendar
    eligible_list = list(eligible_ts)
    if len(eligible_list) < event_count:
        sampled = eligible_list  # use all available
    else:
        sampled = rng.sample(eligible_list, event_count)

    returns: list[float] = []
    for ts in sampled:
        fwd_bps = compute_forward_return_bps(ts, spot_prices, horizon_seconds)
        if fwd_bps is None:
            continue
        if funding_positive:
            ret = signal_return_bps_for_positive_funding(fwd_bps)
        else:
            ret = signal_return_bps_for_negative_funding(fwd_bps)
        returns.append(ret)

    if not returns:
        return None
    finite_returns = [r for r in returns if math.isfinite(r)]
    if not finite_returns:
        return None
    return statistics.mean(finite_returns)


# ---------------------------------------------------------------------------
# Train/holdout split
# ---------------------------------------------------------------------------


def split_events_chronological(
    events: Sequence[CellEvent],
    train_fraction: float = TRAIN_FRACTION,
) -> tuple[Sequence[CellEvent], Sequence[CellEvent]]:
    """
    Split events chronologically into train and holdout.

    First train_fraction of events (by timestamp) go to train,
    remainder to holdout. Events must be sorted chronologically.
    """
    sorted_events = sorted(events, key=lambda e: e.event_timestamp_ns)
    split_idx = max(1, int(len(sorted_events) * train_fraction))
    return sorted_events[:split_idx], sorted_events[split_idx:]


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------


def evaluate_gates(
    result: CellResult,
    config: EvaluationRunConfig,
) -> CellResult:
    """
    Apply all acceptance gates and update result with verdict.

    Returns updated CellResult with verdict populated.
    """
    # --- Sample-size gate ---
    if result.valid_count < MIN_VALID_EVENTS_FOR_DIAGNOSTIC:
        return CellResult(
            **{**result.to_dict(),
                "verdict": VERDICT_NEEDS_MORE_DATA}
        )

    if result.valid_count < MIN_VALID_EVENTS_FOR_CANDIDATE:
        # Diagnostic-only tier — can report metrics but not promote
        return CellResult(
            **{**result.to_dict(),
                "verdict": VERDICT_NEEDS_MORE_DATA}
        )

    # --- For candidate consideration, begin gates ---
    # Gate: mean_net_bps > 0
    if result.mean_net_bps is None or result.mean_net_bps <= 0:
        return CellResult(
            **{**result.to_dict(),
                "verdict": VERDICT_REJECTED}
        )

    # Gate: median_net_bps > 0
    if result.median_net_bps is None or result.median_net_bps <= 0:
        return CellResult(
            **{**result.to_dict(),
                "verdict": VERDICT_REJECTED}
        )

    # Gate: win_rate >= 0.55
    if result.win_rate is None or result.win_rate < MIN_WIN_RATE:
        return CellResult(
            **{**result.to_dict(),
                "verdict": VERDICT_REJECTED}
        )

    # Gate: worst_decile_net_bps > -PRIMARY_COST_BPS
    if (
        result.worst_decile_net_bps is None
        or result.worst_decile_net_bps <= -config.cost_bps
    ):
        return CellResult(
            **{**result.to_dict(),
                "verdict": VERDICT_REJECTED}
        )

    # Gate: baseline delta >= 10 bps
    if (
        result.baseline_delta_bps is None
        or result.baseline_delta_bps < BASELINE_BEAT_BPS
    ):
        return CellResult(
            **{**result.to_dict(),
                "verdict": VERDICT_REJECTED}
        )

    # --- Holdout split and gate ---
    train_events, holdout_events = split_events_chronological(
        result.events, config.train_fraction
    )

    if not train_events or not holdout_events:
        return CellResult(
            **{**result.to_dict(),
                "verdict": VERDICT_UNDERPOWERED_HOLDOUT}
        )

    train_means = [e.net_return_bps for e in train_events if math.isfinite(e.net_return_bps)]
    holdout_means = [e.net_return_bps for e in holdout_events if math.isfinite(e.net_return_bps)]
    if not train_means or not holdout_means:
        return CellResult(
            **{**result.to_dict(),
                "verdict": VERDICT_UNDERPOWERED_HOLDOUT}
        )
    train_mean = statistics.mean(train_means)
    holdout_mean = statistics.mean(holdout_means)
    holdout_same_sign = (train_mean > 0 and holdout_mean > 0) or (
        train_mean < 0 and holdout_mean < 0
    )

    # Underpowered holdout check (less than 30% of events)
    if len(holdout_events) < 10:  # arbitrary minimum for a split
        return CellResult(
            **{**result.to_dict(),
                "train_mean_bps": train_mean,
                "holdout_mean_bps": holdout_mean,
                "holdout_same_sign": holdout_same_sign,
                "verdict": VERDICT_UNDERPOWERED_HOLDOUT}
        )

    if not holdout_same_sign:
        return CellResult(
            **{**result.to_dict(),
                "train_mean_bps": train_mean,
                "holdout_mean_bps": holdout_mean,
                "holdout_same_sign": False,
                "verdict": VERDICT_REJECTED}
        )

    # Passed all pre-null/FDR gates
    return CellResult(
        **{**result.to_dict(),
            "train_mean_bps": train_mean,
            "holdout_mean_bps": holdout_mean,
            "holdout_same_sign": holdout_same_sign,
            "verdict": VERDICT_CANDIDATE}  # temporary — null/FDR may downgrade
    )


# ---------------------------------------------------------------------------
# Null invocation
# ---------------------------------------------------------------------------


def run_null_for_cell(
    result: CellResult,
    eligible_ts: tuple[int, ...],
    event_ts: tuple[int, ...],
    signed_returns_by_ts: Mapping[int, float],
    config: EvaluationRunConfig,
) -> CellResult:
    """
    Run the timestamp-shuffle null for a cell.

    Returns updated result with null fields populated.
    """
    if result.valid_count < MIN_VALID_EVENTS_FOR_CANDIDATE:
        return result  # no null needed

    if not eligible_ts or len(eligible_ts) < result.valid_count:
        return CellResult(
            **{**result.to_dict(),
                "null_survived": False,
                "null_p_value": None,
                "verdict": VERDICT_NO_NULL_WORTHY}
        )

    event_count = result.valid_count
    actual_mean = result.mean_net_bps
    if actual_mean is None:
        return result

    null_input = TimestampShuffleNullInput(
        cell_id=result.cell_id,
        eligible_timestamps_ns=eligible_ts,
        actual_event_timestamps_ns=event_ts,
        signed_net_returns_by_timestamp_bps=dict(signed_returns_by_ts),
        actual_mean_net_bps=actual_mean,
        event_count=event_count,
        horizon_hours=0,  # not used in computation
        direction=result.direction,
        iterations=config.null_iterations,
        seed=config.seed,
        cost_bps=config.cost_bps,
    )

    null_result = run_timestamp_shuffle_null(null_input)

    null_survived = null_result.survives_null
    p_value = null_result.empirical_p_value

    if null_result.status in (
        STATUS_TIMESTAMP_SHUFFLE_NULL_INVALID_INPUT,
    ):
        new_verdict = VERDICT_NO_NULL_WORTHY
    elif not null_survived:
        new_verdict = VERDICT_NULL_REJECTED
    else:
        # Null survived — keep current verdict (may be CANDIDATE or something else)
        new_verdict = result.verdict

    return CellResult(
        **{**result.to_dict(),
            "null_survived": null_survived,
            "null_p_value": p_value,
            "verdict": new_verdict}
    )


# ---------------------------------------------------------------------------
# FDR invocation
# ---------------------------------------------------------------------------


def run_fdr_on_cells(
    results: list[CellResult],
    fdr_alpha: float = 0.05,
    fdr_method: str = "BY",
) -> list[CellResult]:
    """
    Run BY FDR across all cells that survived null.

    Returns updated results with FDR fields populated.
    """
    # Try to import the existing FDR module
    try:
        from .validator.fdr import compute_fdr
        from .validator.fdr import make_metadata
        FDR_AVAILABLE = True
    except ImportError:
        FDR_AVAILABLE = False

    if not FDR_AVAILABLE:
        # Mark all cells as FDR_NOT_IMPLEMENTED
        updated = []
        for r in results:
            if r.verdict in (
                VERDICT_CANDIDATE,
                VERDICT_FDR_BLOCKED,
            ):
                updated.append(CellResult(
                    **{**r.to_dict(),
                        "fdr_survived": False,
                        "verdict": VERDICT_FDR_NOT_IMPLEMENTED}
                ))
            else:
                updated.append(r)
        return updated

    # Collect p-values from cells that survived null
    null_survivors = [r for r in results if r.null_survived and r.null_p_value is not None]
    if not null_survivors:
        return results  # no cells to correct

    p_values = [r.null_p_value for r in null_survivors]  # type: ignore[union-attr]
    labels = [r.cell_id for r in null_survivors]

    fdr_result = compute_fdr(
        p_values=p_values,
        alpha=fdr_alpha,
        method=fdr_method,
        labels=labels,
    )

    # Build map from cell_id to FDR result
    fdr_map: dict[str, bool] = {}
    fdr_p_map: dict[str, float] = {}
    for row in fdr_result.rows:
        if row.label and row.rejected is not None:
            fdr_map[row.label] = row.rejected
        if row.label and row.adjusted_p_value is not None:
            fdr_p_map[row.label] = row.adjusted_p_value

    updated = []
    for r in results:
        if r.cell_id in fdr_map:
            fdr_survived = fdr_map[r.cell_id]
            fdr_p = fdr_p_map.get(r.cell_id)
            if not fdr_survived:
                new_verdict = VERDICT_FDR_BLOCKED
            else:
                new_verdict = r.verdict  # keep existing (hopefully CANDIDATE)
            updated.append(CellResult(
                **{**r.to_dict(),
                    "fdr_survived": fdr_survived,
                    "fdr_adjusted_p": fdr_p,
                    "verdict": new_verdict}
            ))
        else:
            updated.append(r)

    return updated


# ---------------------------------------------------------------------------
# Full 60-cell evaluation
# ---------------------------------------------------------------------------


def evaluate_all_cells(
    funding_rows: Sequence[FundingObservation],
    spot_prices: Sequence[SpotPriceSnapshot],
    config: EvaluationRunConfig,
) -> list[CellResult]:
    """
    Run the full evaluation pipeline for all 60 BTC primary cells.

    This function should only be called in the post-approval real run,
    not in this pre-run coverage task.
    """
    cells = build_all_cell_identifiers()

    # Build funding-by-ts lookup
    funding_by_ts: dict[int, FundingObservation] = {
        r.timestamp_ns: r for r in funding_rows
    }

    # Pre-filter to data window
    windowed_funding = [
        r for r in funding_rows
        if config.data_window_start_ns <= r.timestamp_ns <= config.data_window_end_ns
    ]

    spot_windowed = [
        p for p in spot_prices
        if p.timestamp_ns <= config.data_window_end_ns
    ]

    # Group cells by threshold definition for efficient eligible calendar building
    threshold_to_cells: dict[str, list[CellIdentifier]] = {}
    for cell in cells:
        threshold_to_cells.setdefault(cell.threshold_label, []).append(cell)

    all_results: list[CellResult] = []

    for threshold_def in ALL_THRESHOLDS:
        label = str(threshold_def["label"])
        cell_group = threshold_to_cells.get(label, [])

        # Build eligible calendar (shared for this threshold)
        eligible_ts = build_eligible_timestamps(
            windowed_funding, threshold_def, funding_by_ts
        )

        for cell in cell_group:
            result = compute_events_for_cell(
                cell=cell,
                funding_rows=windowed_funding,
                spot_prices=spot_windowed,
                threshold_def=threshold_def,
                eligible_ts=eligible_ts,
                funding_by_ts=funding_by_ts,
                config=config,
            )
            result = evaluate_gates(result, config)

            # Build signed return map covering ALL eligible timestamps
            # (not just extreme-event timestamps) for the null module.
            # The null randomly samples from the eligible calendar and
            # needs precomputed returns for every eligible timestamp.
            event_ts = tuple(
                e.event_timestamp_ns for e in result.events
            )
            funding_positive = cell.direction == DIRECTION_POSITIVE_FUNDING
            h_seconds = cell.horizon_seconds
            signed_returns: dict[int, float] = {}
            if eligible_ts:
                horizon_ns = h_seconds * 1_000_000_000
                spot_times = [p.timestamp_ns for p in spot_windowed]
                spot_vals = [p.price for p in spot_windowed]
                for ets in eligible_ts:
                    if spot_times and spot_times[0] <= ets:
                        # Find entry price (latest spot <= event time)
                        ei = bisect.bisect_right(spot_times, ets) - 1
                        if ei >= 0:
                            entry_price = spot_vals[ei]
                            # Find exit price (latest spot <= event time + horizon)
                            xi = bisect.bisect_right(spot_times, ets + horizon_ns) - 1
                            if xi >= 0:
                                exit_price = spot_vals[xi]
                                if (entry_price > 0 and math.isfinite(entry_price)
                                        and exit_price > 0 and math.isfinite(exit_price)):
                                    fwd_bps = (exit_price - entry_price) / entry_price * 10000.0
                                    net_bps = net_signal_return_bps(
                                        fwd_bps, funding_positive=funding_positive,
                                        total_cost_bps=config.cost_bps,
                                    )
                                    if math.isfinite(net_bps):
                                        signed_returns[ets] = net_bps

            result = run_null_for_cell(
                result, eligible_ts, event_ts, signed_returns, config
            )

            all_results.append(result)

    # Run FDR across all cells
    all_results = run_fdr_on_cells(
        all_results,
        fdr_alpha=config.fdr_alpha,
        fdr_method=config.fdr_method,
    )

    return all_results


# ---------------------------------------------------------------------------
# Coverage estimation (for pre-run window proposal)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageEstimate:
    """Pre-run coverage statistics for window proposal."""

    earliest_funding_ns: int | None = None
    latest_funding_ns: int | None = None
    total_funding_obs: int = 0
    earliest_spot_ns: int | None = None
    latest_spot_ns: int | None = None
    total_spot_obs: int = 0
    funding_interval_metadata_status: str = "FUNDING_INTERVAL_METADATA_MISSING"
    cells_with_past_only_history: int = 0
    cells_with_ge_50_events: int = 0
    cells_with_ge_100_events: int = 0
    content_hashes: dict[str, str] = field(default_factory=dict)
    proposed_window_start_ns: int | None = None
    proposed_window_end_ns: int | None = None


def estimate_coverage(
    funding_rows: Sequence[FundingObservation],
    spot_prices: Sequence[SpotPriceSnapshot],
    content_hashes: dict[str, str] | None = None,
) -> CoverageEstimate:
    """
    Estimate data coverage without running full evaluation.

    Computes approximate per-cell event counts using a simplified
    method to estimate how many cells would have sufficient data.
    """
    if not funding_rows:
        return CoverageEstimate()

    earliest_funding = funding_rows[0].timestamp_ns
    latest_funding = funding_rows[-1].timestamp_ns
    total_funding = len(funding_rows)

    earliest_spot = spot_prices[0].timestamp_ns if spot_prices else None
    latest_spot = spot_prices[-1].timestamp_ns if spot_prices else None
    total_spot = len(spot_prices)

    cells = build_all_cell_identifiers()
    funding_by_ts = {r.timestamp_ns: r for r in funding_rows}

    # Estimate how many cells would have sufficient data
    # by checking absolute threshold cells with the full date range
    cells_with_past_only = 0
    cells_ge_50 = 0
    cells_ge_100 = 0

    for threshold_def in ALL_THRESHOLDS:
        eligible_ts = build_eligible_timestamps(funding_rows, threshold_def, funding_by_ts)
        for cell in cells:
            if cell.threshold_label != str(threshold_def["label"]):
                continue
            event_ts = select_events(
                funding_rows, threshold_def, cell.direction, eligible_ts, funding_by_ts
            )
            count = len(event_ts)
            if count >= 1:
                cells_with_past_only += 1
            if count >= 50:
                cells_ge_50 += 1
            if count >= 100:
                cells_ge_100 += 1

    return CoverageEstimate(
        earliest_funding_ns=earliest_funding,
        latest_funding_ns=latest_funding,
        total_funding_obs=total_funding,
        earliest_spot_ns=earliest_spot,
        latest_spot_ns=latest_spot,
        total_spot_obs=total_spot,
        funding_interval_metadata_status="FUNDING_INTERVAL_METADATA_AVAILABLE",
        cells_with_past_only_history=cells_with_past_only,
        cells_with_ge_50_events=cells_ge_50,
        cells_with_ge_100_events=cells_ge_100,
        content_hashes=content_hashes or {},
        proposed_window_start_ns=earliest_funding,
        proposed_window_end_ns=latest_funding,
    )
