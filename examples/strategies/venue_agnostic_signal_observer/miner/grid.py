"""
Frozen grid definition.

The grid is frozen before discovery. The Miner must not mutate the grid
during a run family. Grid identity is established by its hash. The Miner
cannot self-certify structural-change exemptions for locked-rejected gates.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class GridCell:
    cell_id: str
    source_venue: str
    source_instrument: str
    target_venue: str
    target_instrument: str
    entry_delay_seconds: float
    forward_horizon_seconds: float
    signal_type: str
    extra_params: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    blocked_by_locked_rejection: bool = False
    structural_change_rationale: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GridSpec:
    """Specification for a frozen discovery grid."""
    name: str
    version: str
    cells: list[GridCell]
    locked_rejection_refs: list[str] = field(default_factory=list)
    structural_change_rationales: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "n_cells": len(self.cells),
            "cells": [c.to_dict() for c in self.cells],
            "locked_rejection_refs": self.locked_rejection_refs,
            "structural_change_rationales": self.structural_change_rationales,
        }


@dataclass
class FrozenGrid:
    """An immutable, hash-identified grid ready for sweeping."""
    spec: GridSpec
    grid_hash: str

    @property
    def cells(self) -> list[GridCell]:
        return self.spec.cells

    def active_cells(self) -> list[GridCell]:
        """Return cells not blocked by locked rejections."""
        return [c for c in self.spec.cells if not c.blocked_by_locked_rejection]

    def to_dict(self) -> dict[str, Any]:
        return {
            "grid_hash": self.grid_hash,
            "spec": self.spec.to_dict(),
        }


def _compute_grid_hash(spec: GridSpec) -> str:
    raw = json.dumps(spec.to_dict(), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def build_grid(spec: GridSpec) -> FrozenGrid:
    """Freeze a GridSpec into an immutable FrozenGrid with a content hash."""
    grid_hash = _compute_grid_hash(spec)
    return FrozenGrid(spec=spec, grid_hash=grid_hash)
