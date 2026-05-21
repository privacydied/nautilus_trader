"""
Corpus aggregation — repeated capture aggregation for candidate recurrence.

Sort by recurrence/consistency before best return. Single-window artifacts
should be demoted or kept diagnostic-only. effective_trial_count must be
recomputed for each corpus — it is not a constant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field
from typing import Any


@dataclass
class CaptureRecord:
    """Result of one capture window for one candidate."""

    capture_id: str
    candidate_hash: str
    grid_hash: str
    corpus_hash: str
    mean_return: float | None
    hit_rate: float | None
    sharpe_like: float | None
    n_observations: int
    return_series: list[float] = field(default_factory=list)
    estimator_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class CorpusAggregationResult:
    candidate_hash: str
    grid_hash: str
    corpus_hash: str
    n_captures: int
    n_positive_captures: int
    recurrence_rate: float
    consistency_score: float
    aggregate_mean_return: float | None
    aggregate_hit_rate: float | None
    best_single_capture_return: float | None
    worst_single_capture_return: float | None
    return_std_across_captures: float | None
    effective_trial_count_per_corpus: int | None
    effective_trial_method: str
    effective_trial_correlation_threshold: float
    verdict: str
    demotion_reason: str | None
    per_capture_summaries: list[dict[str, Any]]


def _mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _std(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _effective_trials(captures: list[CaptureRecord], threshold: float = 0.7) -> int | None:
    """
    Recompute effective_trial_count from cross-capture return-series correlation.

    Uses the same correlation-cluster method as the Phase 1 estimator, applied
    to the cross-capture dimension rather than the cross-cell dimension.
    This is a per-corpus computation — must not be carried from prior corpora.
    """
    series = [c.return_series for c in captures if len(c.return_series) >= 2]
    if not series:
        return None

    from venue_agnostic_signal_observer.validator.effective_trials import (
        compute_effective_trial_count,
    )
    result = compute_effective_trial_count(series, correlation_threshold=threshold)
    return result.effective_trial_count


def aggregate_corpus(
    captures: list[CaptureRecord],
    min_recurrence_rate: float = 0.6,
    demotion_threshold_recurrence: float = 0.3,
    correlation_threshold: float = 0.7,
) -> CorpusAggregationResult:
    """
    Aggregate repeated captures for one candidate.

    Parameters
    ----------
    captures:
        All capture records for one candidate across all windows.
    min_recurrence_rate:
        Fraction of positive captures required for a strong verdict.
    demotion_threshold_recurrence:
        Below this rate, candidate is flagged for demotion.
    """
    if not captures:
        return CorpusAggregationResult(
            candidate_hash="",
            grid_hash="",
            corpus_hash="",
            n_captures=0,
            n_positive_captures=0,
            recurrence_rate=0.0,
            consistency_score=0.0,
            aggregate_mean_return=None,
            aggregate_hit_rate=None,
            best_single_capture_return=None,
            worst_single_capture_return=None,
            return_std_across_captures=None,
            effective_trial_count_per_corpus=None,
            effective_trial_method="correlation_cluster",
            effective_trial_correlation_threshold=correlation_threshold,
            verdict="NO_DATA",
            demotion_reason="No captures provided",
            per_capture_summaries=[],
        )

    candidate_hash = captures[0].candidate_hash
    grid_hash = captures[0].grid_hash
    corpus_hash = captures[0].corpus_hash

    means = [c.mean_return for c in captures if c.mean_return is not None]
    positive = [m for m in means if m > 0]
    n_pos = len(positive)
    recurrence = n_pos / len(captures)

    mean_returns_list = list(means)
    agg_mr = _mean(mean_returns_list)
    best_mr = max(mean_returns_list) if mean_returns_list else None
    worst_mr = min(mean_returns_list) if mean_returns_list else None
    ret_std = _std(mean_returns_list)

    hit_rates = [c.hit_rate for c in captures if c.hit_rate is not None]
    agg_hr = _mean(hit_rates)

    # Consistency: lower std relative to mean is more consistent
    if agg_mr and ret_std is not None and agg_mr != 0:
        consistency = max(0.0, 1.0 - abs(ret_std / agg_mr))
    else:
        consistency = 0.0

    # Effective trial count — per-corpus, recomputed now
    eff_trials = _effective_trials(captures, correlation_threshold)

    # Verdict: single-window check takes priority over recurrence check
    demotion_reason = None
    if len(captures) == 1:
        verdict = "SINGLE_WINDOW_UNVERIFIED"
        demotion_reason = "Only one capture window; cannot assess recurrence"
    elif recurrence < demotion_threshold_recurrence:
        verdict = "SINGLE_WINDOW_ARTIFACT"
        demotion_reason = (
            f"Recurrence rate {recurrence:.2f} < demotion threshold "
            f"{demotion_threshold_recurrence:.2f}"
        )
    elif recurrence >= min_recurrence_rate and (agg_mr or 0) > 0:
        verdict = "RECURRING_CANDIDATE"
    else:
        verdict = "INCONSISTENT"

    per_capture = [
        {
            "capture_id": c.capture_id,
            "mean_return": c.mean_return,
            "hit_rate": c.hit_rate,
            "n_observations": c.n_observations,
        }
        for c in captures
    ]

    return CorpusAggregationResult(
        candidate_hash=candidate_hash,
        grid_hash=grid_hash,
        corpus_hash=corpus_hash,
        n_captures=len(captures),
        n_positive_captures=n_pos,
        recurrence_rate=round(recurrence, 4),
        consistency_score=round(consistency, 4),
        aggregate_mean_return=round(agg_mr, 8) if agg_mr is not None else None,
        aggregate_hit_rate=round(agg_hr, 6) if agg_hr is not None else None,
        best_single_capture_return=round(best_mr, 8) if best_mr is not None else None,
        worst_single_capture_return=round(worst_mr, 8) if worst_mr is not None else None,
        return_std_across_captures=round(ret_std, 8) if ret_std is not None else None,
        effective_trial_count_per_corpus=eff_trials,
        effective_trial_method="correlation_cluster",
        effective_trial_correlation_threshold=correlation_threshold,
        verdict=verdict,
        demotion_reason=demotion_reason,
        per_capture_summaries=per_capture,
    )
