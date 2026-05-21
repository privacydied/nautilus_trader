"""
PBO / CSCV — grid-level overfitting diagnostic.

Probability of Backtest Overfitting (PBO) via Combinatorial Symmetric
Cross-Validation (CSCV).

This is grid-level, not candidate-level. The API takes a full grid/cell
performance matrix. `compute_pbo(candidate)` is the wrong shape — this
estimator takes all cells at once.

PBO has its own combinatorial partitioner. It must NOT share a partitioner
with CPCV. Purging and embargo must NOT be applied here — doing so would
change the statistical semantics of the overfitting diagnostic.

Cost scales as: n_partitions × n_cells × cost_per_eval.
For S=16 blocks: C(16, 8) = 12,870 partitions.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from itertools import combinations
from typing import Any
from typing import Sequence

from .metadata import EstimatorMetadata
from .metadata import make_metadata


ESTIMATOR_NAME = "pbo_cscv"
ESTIMATOR_VERSION = "1.0.0"


@dataclass
class SelectedCellRankDiagnostics:
    selected_cell_index: int
    oos_rank_distribution: list[float]
    median_oos_rank: float | None
    fraction_oos_rank_below_median: float | None


@dataclass
class PBOResult:
    n_blocks: int
    n_partitions: int
    n_cells: int
    selected_cell_index: int | None
    selected_cell_rank_diagnostics: SelectedCellRankDiagnostics | None
    pbo_estimate: float | None
    grid_level_status: str
    estimator_metadata: EstimatorMetadata
    input_metadata: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mean(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    return sum(xs) / len(xs)


def _rank(values: list[float], ascending: bool = False) -> list[int]:
    """Return 1-based ranks. ascending=False means highest value = rank 1."""
    indexed = sorted(enumerate(values), key=lambda iv: iv[1], reverse=not ascending)
    ranks = [0] * len(values)
    for rank_pos, (orig_idx, _) in enumerate(indexed, start=1):
        ranks[orig_idx] = rank_pos
    return ranks


def _block_split_returns(
    cell_returns: list[list[float]],
    n_blocks: int,
) -> list[list[list[float]]]:
    """
    Split each cell's return series into n_blocks equal time blocks.

    Returns: blocks[block_idx][cell_idx] = list of returns in that block.
    """
    n_cells = len(cell_returns)
    if n_cells == 0:
        return []
    n_obs = min(len(r) for r in cell_returns)
    if n_obs == 0:
        return []

    block_size = max(1, n_obs // n_blocks)
    actual_blocks = []
    for b in range(n_blocks):
        start = b * block_size
        end = start + block_size if b < n_blocks - 1 else n_obs
        block_cell_returns = [cell_returns[c][start:end] for c in range(n_cells)]
        actual_blocks.append(block_cell_returns)
    return actual_blocks


def _cell_performance(returns: list[float]) -> float:
    """Sharpe-like performance metric for a cell's returns in a partition."""
    if len(returns) < 2:
        return 0.0
    m = _mean(returns)
    var = sum((r - m) ** 2 for r in returns) / (len(returns) - 1)
    if var <= 0:
        return 0.0
    return m / math.sqrt(var)


def compute_pbo(
    cell_returns: list[list[float]],
    n_blocks: int = 16,
    estimator_metadata: EstimatorMetadata | None = None,
    input_metadata: dict[str, Any] | None = None,
) -> PBOResult:
    """
    Compute Probability of Backtest Overfitting for a frozen grid.

    Parameters
    ----------
    cell_returns:
        One return series per grid cell, all aligned to the same time
        basis (same length). Shape: [n_cells][n_observations].
    n_blocks:
        Number of time blocks to split into. C(n_blocks, n_blocks//2)
        partitions are evaluated. Default 16 → 12,870 partitions.

    Returns
    -------
    PBOResult with pbo_estimate and selected_cell_rank_diagnostics.
    """
    meta = estimator_metadata or make_metadata(
        ESTIMATOR_NAME,
        ESTIMATOR_VERSION,
        config={"n_blocks": n_blocks},
    )
    inp = input_metadata or {}

    n_cells = len(cell_returns)

    def _error(msg: str) -> PBOResult:
        return PBOResult(
            n_blocks=n_blocks,
            n_partitions=0,
            n_cells=n_cells,
            selected_cell_index=None,
            selected_cell_rank_diagnostics=None,
            pbo_estimate=None,
            grid_level_status="ERROR",
            estimator_metadata=meta,
            input_metadata=inp,
            error_message=msg,
        )

    if n_cells == 0:
        return _error("No cells provided")
    if n_cells == 1:
        return _error("PBO requires at least 2 cells")

    blocks = _block_split_returns(cell_returns, n_blocks)
    actual_n_blocks = len(blocks)
    if actual_n_blocks < 2:
        return _error(f"Too few observations to form {n_blocks} blocks")

    n_is_blocks = actual_n_blocks // 2
    actual_n_blocks - n_is_blocks

    all_block_indices = list(range(actual_n_blocks))
    is_combos = list(combinations(all_block_indices, n_is_blocks))
    len(is_combos)

    # In-sample performance across all partitions for each cell
    # → select the best IS cell per partition
    # → record its OOS rank
    best_cell_oos_ranks: list[float] = []

    for is_indices in is_combos:
        oos_indices = [i for i in all_block_indices if i not in is_indices]

        # IS performance per cell
        is_perf = []
        for c in range(n_cells):
            is_returns = [r for b in is_indices for r in blocks[b][c]]
            is_perf.append(_cell_performance(is_returns))

        # Select best IS cell
        best_cell = max(range(n_cells), key=lambda c: is_perf[c])

        # OOS performance per cell
        oos_perf = []
        for c in range(n_cells):
            oos_returns = [r for b in oos_indices for r in blocks[b][c]]
            oos_perf.append(_cell_performance(oos_returns))

        # OOS rank of the best IS cell (1 = best OOS)
        oos_ranks = _rank(oos_perf, ascending=False)
        best_cell_oos_ranks.append(float(oos_ranks[best_cell]))

    n_partitions_actual = len(best_cell_oos_ranks)
    median_oos_rank = sorted(best_cell_oos_ranks)[n_partitions_actual // 2] if best_cell_oos_ranks else None
    median_grid_rank = n_cells / 2.0

    # PBO = fraction of partitions where best IS cell's OOS rank > median grid rank
    if best_cell_oos_ranks and median_grid_rank > 0:
        pbo = sum(1 for r in best_cell_oos_ranks if r > median_grid_rank) / n_partitions_actual
    else:
        pbo = None

    frac_below_median = (
        sum(1 for r in best_cell_oos_ranks if r <= median_grid_rank) / n_partitions_actual
        if best_cell_oos_ranks
        else None
    )

    # Overall IS selection across all data to identify the "selected cell"
    all_is_perf = [_cell_performance(cell_returns[c]) for c in range(n_cells)]
    selected_cell = max(range(n_cells), key=lambda c: all_is_perf[c])

    status = "OVERFIT_SUSPECTED" if (pbo is not None and pbo > 0.5) else "OK"

    return PBOResult(
        n_blocks=actual_n_blocks,
        n_partitions=n_partitions_actual,
        n_cells=n_cells,
        selected_cell_index=selected_cell,
        selected_cell_rank_diagnostics=SelectedCellRankDiagnostics(
            selected_cell_index=selected_cell,
            oos_rank_distribution=best_cell_oos_ranks,
            median_oos_rank=median_oos_rank,
            fraction_oos_rank_below_median=round(frac_below_median, 4) if frac_below_median is not None else None,
        ),
        pbo_estimate=round(pbo, 4) if pbo is not None else None,
        grid_level_status=status,
        estimator_metadata=meta,
        input_metadata=inp,
    )
