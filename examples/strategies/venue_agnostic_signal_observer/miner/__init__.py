"""
Edge Miner — Phase 3.

Generalizes existing per-hypothesis forward-return evaluation into
frozen-grid vectorized sweeps.

Invariants:
- The Miner must not mutate the grid.
- The Miner must not validate candidates (no Validator imports for verdicts).
- The Miner must not promote candidates.
- The Miner must not execute orders.
- The Miner must not import live execution clients.
- No private-key env vars.
- A frozen grid must not silently re-test locked-rejected gates.
"""

from .grid import FrozenGrid, GridCell, GridSpec, build_grid
from .sweep import run_sweep, SweepResult, CellResult
from .rejection_guard import check_rejection_guard, RejectionGuardResult

__all__ = [
    "FrozenGrid",
    "GridCell",
    "GridSpec",
    "build_grid",
    "run_sweep",
    "SweepResult",
    "CellResult",
    "check_rejection_guard",
    "RejectionGuardResult",
]
