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

from .grid import FrozenGrid
from .grid import GridCell
from .grid import GridSpec
from .grid import build_grid
from .rejection_guard import RejectionGuardResult
from .rejection_guard import check_rejection_guard
from .sweep import CellResult
from .sweep import SweepResult
from .sweep import run_sweep


__all__ = [
    "CellResult",
    "FrozenGrid",
    "GridCell",
    "GridSpec",
    "RejectionGuardResult",
    "SweepResult",
    "build_grid",
    "check_rejection_guard",
    "run_sweep",
]
