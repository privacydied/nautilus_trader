"""
Effective trial count for DSR.

Method: cluster grid cells by return-series correlation. The number of
clusters is the effective number of independent trials.

effective_trial_count is per-grid-per-corpus. If the corpus changes,
correlation structure can change, so this must be recomputed — never
carried forward as a constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import math


@dataclass
class EffectiveTrialResult:
    raw_cell_count: int
    effective_trial_count: int
    correlation_threshold: float
    method: str
    cluster_assignments: list[int]


def _correlation(a: Sequence[float], b: Sequence[float]) -> float:
    """Pearson correlation between two aligned return series."""
    n = len(a)
    if n < 2:
        return 0.0
    mean_a = sum(a) / n
    mean_b = sum(b) / n
    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((x - mean_b) ** 2 for x in b)
    denom = math.sqrt(var_a * var_b)
    if denom == 0.0:
        return 0.0
    return cov / denom


def compute_effective_trial_count(
    cell_return_series: list[list[float]],
    correlation_threshold: float = 0.7,
) -> EffectiveTrialResult:
    """Cluster cells by return-series correlation; count clusters.

    Parameters
    ----------
    cell_return_series:
        One return series per grid cell, all aligned to the same
        evaluation basis (same length, same time index).
    correlation_threshold:
        Cells with |correlation| >= this threshold are considered the
        same cluster. Threshold is recorded in output metadata.

    Returns
    -------
    EffectiveTrialResult with raw_cell_count and effective_trial_count.
    """
    n = len(cell_return_series)
    if n == 0:
        return EffectiveTrialResult(
            raw_cell_count=0,
            effective_trial_count=0,
            correlation_threshold=correlation_threshold,
            method="correlation_cluster",
            cluster_assignments=[],
        )

    # Single-linkage clustering: assign each cell to the first cluster
    # whose representative has |correlation| >= threshold with this cell.
    cluster_assignments = [-1] * n
    cluster_representatives: list[int] = []

    for i in range(n):
        assigned = False
        for rep in cluster_representatives:
            r_series = cell_return_series[rep]
            c_series = cell_return_series[i]
            # Align lengths
            min_len = min(len(r_series), len(c_series))
            if min_len < 2:
                continue
            corr = _correlation(r_series[:min_len], c_series[:min_len])
            if abs(corr) >= correlation_threshold:
                cluster_assignments[i] = rep
                assigned = True
                break
        if not assigned:
            cluster_representatives.append(i)
            cluster_assignments[i] = i

    effective_trial_count = len(cluster_representatives)

    return EffectiveTrialResult(
        raw_cell_count=n,
        effective_trial_count=effective_trial_count,
        correlation_threshold=correlation_threshold,
        method="correlation_cluster",
        cluster_assignments=cluster_assignments,
    )
