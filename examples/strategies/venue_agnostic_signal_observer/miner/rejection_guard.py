"""
Rejection guard — prevent laundering of locked-rejected hypotheses.

A frozen discovery grid must not silently re-test an already locked-rejected
gate as 'just another grid cell' unless the structural-change rule is
satisfied and explicitly recorded.

Structural-change rationale must be:
- Human-authored (not Miner-self-certified)
- Recorded in the grid lock artifact or a grid-lock-linked rationale artifact
- Covered by the grid hash

The Miner cannot self-certify structural-change exemptions.
"""

from __future__ import annotations

from dataclasses import dataclass

from .grid import FrozenGrid
from .grid import GridCell


KNOWN_LOCKED_REJECTION_KEYS = {
    "same_asset_cross_venue_tick_lead_lag",
    "derivatives_source_spot_target_lead_lag_v1",
    "trade_flow_impulse_v1",
    "polymarket_btc_updown_liquidity_actionability",
}

STRUCTURAL_CHANGE_EXAMPLES = [
    "different_venue_instrument_class",
    "different_fee_tier_cost_model_with_evidence",
    "different_execution_mode_with_shadow_fill_model",
    "different_regime_gate_precommitted_before_capture",
    "different_data_type_changes_mechanism",
]


@dataclass
class CellGuardResult:
    cell_id: str
    blocked: bool
    reason: str | None
    structural_change_rationale: str | None


@dataclass
class RejectionGuardResult:
    grid_hash: str
    n_cells_total: int
    n_cells_blocked: int
    n_cells_active: int
    blocked_cells: list[CellGuardResult]
    active_cells: list[str]
    guard_passed: bool


def _overlaps_locked_rejection(cell: GridCell, locked_refs: list[str]) -> bool:
    """Check if a cell's signal_type or cell_id overlaps a locked rejection."""
    cell_key = cell.signal_type.lower().replace("-", "_").replace(" ", "_")
    return any(ref in cell_key or cell_key in ref for ref in locked_refs)


def check_rejection_guard(
    grid: FrozenGrid,
    additional_locked_refs: list[str] | None = None,
) -> RejectionGuardResult:
    """
    Check all grid cells against locked rejections.

    Cells that overlap locked rejections without human-authored
    structural-change rationale are flagged as blocked.

    The Miner must not self-certify exemptions — if structural_change_rationale
    is None or empty, a cell overlapping a locked rejection is blocked.
    """
    locked_refs = list(KNOWN_LOCKED_REJECTION_KEYS)
    if additional_locked_refs:
        locked_refs.extend(additional_locked_refs)
    locked_refs += grid.spec.locked_rejection_refs

    blocked: list[CellGuardResult] = []
    active: list[str] = []

    for cell in grid.cells:
        if cell.blocked_by_locked_rejection:
            blocked.append(CellGuardResult(
                cell_id=cell.cell_id,
                blocked=True,
                reason="explicitly_marked_blocked_by_locked_rejection",
                structural_change_rationale=None,
            ))
            continue

        if _overlaps_locked_rejection(cell, locked_refs):
            rationale = cell.structural_change_rationale or grid.spec.structural_change_rationales.get(cell.cell_id)
            if not rationale:
                blocked.append(CellGuardResult(
                    cell_id=cell.cell_id,
                    blocked=True,
                    reason="overlaps_locked_rejection_no_rationale",
                    structural_change_rationale=None,
                ))
            else:
                # Has rationale — passes guard
                active.append(cell.cell_id)
        else:
            active.append(cell.cell_id)

    return RejectionGuardResult(
        grid_hash=grid.grid_hash,
        n_cells_total=len(grid.cells),
        n_cells_blocked=len(blocked),
        n_cells_active=len(active),
        blocked_cells=blocked,
        active_cells=active,
        guard_passed=len(blocked) == 0,
    )
