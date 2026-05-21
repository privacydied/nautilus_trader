"""
Frozen-grid vectorized sweep.

Generalizes the existing per-hypothesis forward-return evaluation into a
sweep over all active grid cells. Uses VectorBT-style vectorized execution
semantics as a reference model.

The Miner:
- Does not mutate the grid.
- Does not validate candidates (no promotion or TRADE_READY verdict).
- Does not execute orders or import live execution clients.
- Feeds raw sweep results to the Validator (Phase 1) for honest estimation.
"""

from __future__ import annotations

import math
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Any
from typing import Callable

from .grid import FrozenGrid
from .grid import GridCell
from .rejection_guard import check_rejection_guard


@dataclass
class CellResult:
    cell_id: str
    grid_hash: str
    n_observations: int
    mean_return: float | None
    hit_rate: float | None
    sharpe_like: float | None
    return_series: list[float]
    skipped: bool = False
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SweepResult:
    grid_hash: str
    n_cells_total: int
    n_cells_active: int
    n_cells_blocked: int
    n_cells_skipped: int
    cell_results: list[CellResult]
    sweep_created_at: str
    input_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _sharpe_like(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    if var <= 0:
        return None
    return m / math.sqrt(var)


ObservationFn = Callable[[GridCell], list[float]]


def run_sweep(
    grid: FrozenGrid,
    observation_fn: ObservationFn,
    input_metadata: dict[str, Any] | None = None,
    additional_locked_refs: list[str] | None = None,
) -> SweepResult:
    """
    Run a vectorized sweep over all active cells in a frozen grid.

    Parameters
    ----------
    grid:
        A frozen (hashed) grid. Must not be mutated during the sweep.
    observation_fn:
        Called for each active cell. Returns a list of forward returns.
        This is where the per-cell evaluation logic plugs in — analogous
        to how VectorBT applies a signal function vectorized over a grid.
    input_metadata:
        Optional metadata about the input corpus (hash, capture IDs, etc.)
    additional_locked_refs:
        Extra locked rejection keys to check against, beyond defaults.

    Returns
    -------
    SweepResult with per-cell raw returns. Not a promotion verdict.
    """
    guard = check_rejection_guard(grid, additional_locked_refs)
    active_cell_ids = set(guard.active_cells)

    cell_results: list[CellResult] = []
    n_skipped = 0

    for cell in grid.cells:
        if cell.cell_id not in active_cell_ids:
            cell_results.append(CellResult(
                cell_id=cell.cell_id,
                grid_hash=grid.grid_hash,
                n_observations=0,
                mean_return=None,
                hit_rate=None,
                sharpe_like=None,
                return_series=[],
                skipped=True,
                skip_reason="blocked_by_rejection_guard",
            ))
            n_skipped += 1
            continue

        try:
            returns = observation_fn(cell)
        except Exception as exc:
            cell_results.append(CellResult(
                cell_id=cell.cell_id,
                grid_hash=grid.grid_hash,
                n_observations=0,
                mean_return=None,
                hit_rate=None,
                sharpe_like=None,
                return_series=[],
                skipped=True,
                skip_reason=f"observation_fn_error: {exc}",
            ))
            n_skipped += 1
            continue

        mr = _mean(returns)
        hr = sum(1 for r in returns if r > 0) / len(returns) if returns else None
        sl = _sharpe_like(returns)

        cell_results.append(CellResult(
            cell_id=cell.cell_id,
            grid_hash=grid.grid_hash,
            n_observations=len(returns),
            mean_return=round(mr, 8) if mr is not None else None,
            hit_rate=round(hr, 6) if hr is not None else None,
            sharpe_like=round(sl, 6) if sl is not None else None,
            return_series=returns,
        ))

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return SweepResult(
        grid_hash=grid.grid_hash,
        n_cells_total=len(grid.cells),
        n_cells_active=guard.n_cells_active,
        n_cells_blocked=guard.n_cells_blocked,
        n_cells_skipped=n_skipped,
        cell_results=cell_results,
        sweep_created_at=now,
        input_metadata=input_metadata or {},
    )
