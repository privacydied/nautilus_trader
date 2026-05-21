"""
Combinatorial Purged Cross-Validation (CPCV) — candidate/cell-level honest
performance estimation.

Uses purging and time-domain embargo. Applies to timestamped observations
with explicit label intervals. Should catch non-stationary candidates —
signals real early and gone later.

CPCV has its own combinatorial partitioner. It must NOT share a partitioner
with CSCV/PBO. Duplicated combinatorial logic is acceptable to preserve
statistical semantics and test clarity.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from itertools import combinations
from typing import Any
from typing import Sequence

from .embargo import TimeInterval
from .embargo import TimestampedObservation
from .embargo import compute_embargo_seconds
from .embargo import embargo_train_obs
from .embargo import purge_train_obs
from .metadata import EstimatorMetadata
from .metadata import make_metadata


ESTIMATOR_NAME = "cpcv"
ESTIMATOR_VERSION = "1.0.0"


@dataclass
class SplitResult:
    split_index: int
    test_interval: dict[str, str]
    n_train_before_purge: int
    purged_count: int
    embargoed_count: int
    n_train_used: int
    n_test: int
    mean_return: float | None
    hit_rate: float | None
    sharpe_like: float | None


@dataclass
class FoldStabilityMetrics:
    sharpe_std: float | None
    hit_rate_std: float | None
    mean_return_std: float | None
    monotone_degradation: bool


@dataclass
class NonStationarityDiagnostics:
    early_mean_return: float | None
    late_mean_return: float | None
    early_hit_rate: float | None
    late_hit_rate: float | None
    suspected_decay: bool


@dataclass
class CPCVResult:
    n_splits: int
    n_combinations: int
    train_test_intervals: list[dict[str, Any]]
    purged_count_total: int
    embargoed_count_total: int
    per_split_results: list[SplitResult]
    fold_stability: FoldStabilityMetrics
    nonstationarity_diagnostics: NonStationarityDiagnostics
    aggregate_mean_return: float | None
    aggregate_hit_rate: float | None
    aggregate_sharpe_like: float | None
    estimator_metadata: EstimatorMetadata
    input_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _std(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _sharpe_like(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    m = sum(returns) / len(returns)
    s = math.sqrt(sum((r - m) ** 2 for r in returns) / (len(returns) - 1))
    if s == 0.0:
        return None
    return m / s


def _block_split(
    obs: list[TimestampedObservation],
    n_blocks: int,
) -> list[list[TimestampedObservation]]:
    """Split observations chronologically into n_blocks."""
    obs_sorted = sorted(obs, key=lambda o: o.event_time)
    total = len(obs_sorted)
    if total == 0 or n_blocks <= 0:
        return []
    block_size = math.ceil(total / n_blocks)
    blocks = []
    for i in range(n_blocks):
        start = i * block_size
        end = min(start + block_size, total)
        if start < total:
            blocks.append(obs_sorted[start:end])
    return blocks


def compute_cpcv(
    observations: Sequence[TimestampedObservation],
    n_splits: int = 6,
    n_test_splits: int = 2,
    label_horizon_seconds: float = 180.0,
    entry_delay_seconds: float = 0.0,
    staleness_buffer_seconds: float = 0.0,
    estimator_metadata: EstimatorMetadata | None = None,
    input_metadata: dict[str, Any] | None = None,
) -> CPCVResult:
    """
    Compute CPCV for a candidate return series.

    Parameters
    ----------
    observations:
        Timestamped observations with label intervals.
    n_splits:
        Number of chronological blocks (S in the plan). Combinations
        are C(n_splits, n_test_splits).
    n_test_splits:
        Number of blocks used as test per combination.
    label_horizon_seconds:
        Used to compute embargo duration.
    """
    meta = estimator_metadata or make_metadata(
        ESTIMATOR_NAME,
        ESTIMATOR_VERSION,
        config={
            "n_splits": n_splits,
            "n_test_splits": n_test_splits,
            "label_horizon_seconds": label_horizon_seconds,
            "entry_delay_seconds": entry_delay_seconds,
            "staleness_buffer_seconds": staleness_buffer_seconds,
        },
    )
    inp = input_metadata or {}

    embargo_secs = compute_embargo_seconds(
        label_horizon_seconds, entry_delay_seconds, staleness_buffer_seconds
    )

    obs_list = list(observations)
    blocks = _block_split(obs_list, n_splits)
    actual_n_splits = len(blocks)

    all_combos = list(combinations(range(actual_n_splits), n_test_splits))
    n_combinations = len(all_combos)

    split_results: list[SplitResult] = []
    train_test_intervals: list[dict[str, Any]] = []
    total_purged = 0
    total_embargoed = 0

    for split_idx, test_block_indices in enumerate(all_combos):
        test_blocks = [blocks[i] for i in test_block_indices]
        train_block_indices = [i for i in range(actual_n_splits) if i not in test_block_indices]
        train_blocks = [blocks[i] for i in train_block_indices]

        test_obs_flat = [o for block in test_blocks for o in block]
        train_obs_flat = [o for block in train_blocks for o in block]

        if not test_obs_flat:
            continue

        test_times = [o.event_time for o in test_obs_flat]
        test_interval = TimeInterval(
            start=min(test_times),
            end=max(test_times),
        )

        # Purge
        train_after_purge, purged = purge_train_obs(train_obs_flat, test_interval)
        # Embargo
        train_after_embargo, embargoed = embargo_train_obs(
            train_after_purge, test_interval, embargo_secs
        )

        total_purged += purged
        total_embargoed += embargoed

        test_returns = [o.value for o in test_obs_flat]
        mr = _mean(test_returns)
        hr = sum(1 for r in test_returns if r > 0) / len(test_returns) if test_returns else None
        sl = _sharpe_like(test_returns)

        split_results.append(SplitResult(
            split_index=split_idx,
            test_interval={
                "start": test_interval.start.isoformat(),
                "end": test_interval.end.isoformat(),
            },
            n_train_before_purge=len(train_obs_flat),
            purged_count=purged,
            embargoed_count=embargoed,
            n_train_used=len(train_after_embargo),
            n_test=len(test_obs_flat),
            mean_return=round(mr, 8) if mr is not None else None,
            hit_rate=round(hr, 6) if hr is not None else None,
            sharpe_like=round(sl, 6) if sl is not None else None,
        ))

        train_test_intervals.append({
            "split_index": split_idx,
            "test_block_indices": list(test_block_indices),
            "train_block_indices": train_block_indices,
            "test_interval_start": test_interval.start.isoformat(),
            "test_interval_end": test_interval.end.isoformat(),
            "embargo_seconds": embargo_secs,
        })

    sharpe_vals = [s.sharpe_like for s in split_results if s.sharpe_like is not None]
    hit_vals = [s.hit_rate for s in split_results if s.hit_rate is not None]
    mr_vals = [s.mean_return for s in split_results if s.mean_return is not None]

    sharpe_std = _std(sharpe_vals)
    hit_std = _std(hit_vals)
    mr_std = _std(mr_vals)

    # Non-stationarity: compare first half vs second half of splits by time
    half = len(split_results) // 2
    early_mr = _mean([s.mean_return for s in split_results[:half] if s.mean_return is not None])
    late_mr = _mean([s.mean_return for s in split_results[half:] if s.mean_return is not None])
    early_hr = _mean([s.hit_rate for s in split_results[:half] if s.hit_rate is not None])
    late_hr = _mean([s.hit_rate for s in split_results[half:] if s.hit_rate is not None])

    suspected_decay = (
        early_mr is not None
        and late_mr is not None
        and early_mr > 0
        and late_mr < early_mr * 0.5
    )

    monotone_degradation = False
    if len(sharpe_vals) >= 3:
        decreasing = all(
            sharpe_vals[i] >= sharpe_vals[i + 1] for i in range(len(sharpe_vals) - 1)
        )
        monotone_degradation = decreasing and sharpe_vals[-1] < 0

    agg_mr = _mean(mr_vals)
    agg_hr = _mean(hit_vals)
    agg_sl = _mean(sharpe_vals)

    return CPCVResult(
        n_splits=actual_n_splits,
        n_combinations=n_combinations,
        train_test_intervals=train_test_intervals,
        purged_count_total=total_purged,
        embargoed_count_total=total_embargoed,
        per_split_results=split_results,
        fold_stability=FoldStabilityMetrics(
            sharpe_std=round(sharpe_std, 6) if sharpe_std is not None else None,
            hit_rate_std=round(hit_std, 6) if hit_std is not None else None,
            mean_return_std=round(mr_std, 8) if mr_std is not None else None,
            monotone_degradation=monotone_degradation,
        ),
        nonstationarity_diagnostics=NonStationarityDiagnostics(
            early_mean_return=round(early_mr, 8) if early_mr is not None else None,
            late_mean_return=round(late_mr, 8) if late_mr is not None else None,
            early_hit_rate=round(early_hr, 6) if early_hr is not None else None,
            late_hit_rate=round(late_hr, 6) if late_hr is not None else None,
            suspected_decay=suspected_decay,
        ),
        aggregate_mean_return=round(agg_mr, 8) if agg_mr is not None else None,
        aggregate_hit_rate=round(agg_hr, 6) if agg_hr is not None else None,
        aggregate_sharpe_like=round(agg_sl, 6) if agg_sl is not None else None,
        estimator_metadata=meta,
        input_metadata=inp,
    )
