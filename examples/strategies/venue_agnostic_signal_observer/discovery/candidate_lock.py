"""Candidate-level lock file for the discovery freeze system.

A DiscoveryCandidateLock freezes a discovered cluster after it surfaces.
It references the parent grid lock and the specific capture data that
produced it.  This lock is the artifact that future validators consume.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .capture_fingerprint import CaptureManifestRef, build_capture_manifest_ref
from .exceptions import (
    CandidateHashMismatchError,
    CandidateLockValidationError,
    CaptureFingerprintError,
    GridCellCountMismatchError,
    GridHashMismatchError,
    GridLockValidationError,
    GridSpecValidationError,
)
from .grid_lock import DiscoveryGridLock, validate_grid_spec_against_lock
from .search_space import (
    DiscoveryGridSpec,
    enumerate_cost_sensitivity_cell_count,
    enumerate_primary_cell_count,
    grid_sha256,
    validate_grid_spec,
    counted_axis_names,
    GRID_SCHEMA_VERSION,
    CANDIDATE_SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CANDIDATE_LOCK_TYPE = "DISCOVERY_CANDIDATE_LOCK"

# The exact set of primary counted axis keys required in each selected cell.
_PRIMARY_COUNTED_AXIS_KEYS: tuple[str, ...] = (
    "source_venues",
    "source_symbols",
    "target_venues",
    "target_symbols",
    "feature_types",
    "lookbacks_ms",
    "thresholds_centibps",
    "horizons_ms",
    "entry_delays_ms",
    "regime_filters",
)


# ---------------------------------------------------------------------------
# Candidate Lock
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveryCandidateLock:
    """Frozen candidate-level lock.

    References the parent grid and the discovery capture manifest hashes
    that surfaced the candidate.

    candidate_id is a human-readable label and is NOT part of the candidate hash.
    cluster_summary is excluded from the candidate hash.
    selection_reason is excluded from the candidate hash.
    frozen_at_utc is excluded from the candidate hash.

    Although cluster_summary and selection_reason are excluded from
    candidate_hash, saved candidate lock files are immutable artifacts.
    Do not mutate them in place; write a new artifact if explanatory
    metadata changes.
    """

    lock_type: str
    candidate_id: str
    candidate_hash: str
    parent_grid_id: str
    parent_grid_hash: str
    parent_grid_schema_version: str
    parent_grid_primary_cell_count: int
    parent_grid_cost_sensitivity_cell_count: int
    schema_version: str
    selected_cells: tuple[dict, ...]
    cluster_summary: dict
    selection_reason: str
    discovery_captures: tuple[CaptureManifestRef, ...]
    frozen_at_utc: str


# ---------------------------------------------------------------------------
# Selected-cell helpers
# ---------------------------------------------------------------------------

def canonical_selected_cell_json(cell: dict) -> str:
    """Serialize a single selected cell dict to canonical JSON.

    Uses sorted keys and compact separators for deterministic hashing.
    """
    return json.dumps(cell, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonicalize_selected_cells(
    cells: tuple[dict, ...],
    grid_spec: DiscoveryGridSpec,
) -> tuple[dict, ...]:
    """Validate and canonicalize selected cells.

    Sorts cells by their canonical JSON string so cell ordering does not
    affect the candidate hash.

    Returns a tuple of dicts with canonical key ordering.
    """
    validate_selected_cells(cells, grid_spec)
    sorted_cells = sorted(cells, key=lambda c: canonical_selected_cell_json(c))
    return tuple(sorted_cells)


def validate_selected_cells(
    cells: tuple[dict, ...],
    grid_spec: DiscoveryGridSpec,
) -> None:
    """Validate that selected cells are real, valid cells in the parent grid.

    Raises CandidateLockValidationError on any violation.
    """
    if not cells:
        raise CandidateLockValidationError("selected_cells must be non-empty")

    # Build lookup sets from grid spec for axis membership checking
    axis_values: dict[str, set] = {}
    for key in _PRIMARY_COUNTED_AXIS_KEYS:
        values = getattr(grid_spec, key)
        axis_values[key] = set(values)

    # Track seen canonical forms to detect duplicates
    seen_cells: set[str] = set()

    for i, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise CandidateLockValidationError(
                f"selected_cells[{i}]: expected dict, got {type(cell).__name__}"
            )

        cell_keys = set(cell.keys())
        expected_keys = set(_PRIMARY_COUNTED_AXIS_KEYS)

        # Check for extra keys
        extra_keys = cell_keys - expected_keys
        if extra_keys:
            raise CandidateLockValidationError(
                f"selected_cells[{i}]: unexpected keys {sorted(extra_keys)}"
            )

        # Check for missing keys
        missing_keys = expected_keys - cell_keys
        if missing_keys:
            raise CandidateLockValidationError(
                f"selected_cells[{i}]: missing keys {sorted(missing_keys)}"
            )

        # Check each value is a scalar member of the parent axis
        for key in _PRIMARY_COUNTED_AXIS_KEYS:
            value = cell[key]
            if value not in axis_values[key]:
                raise CandidateLockValidationError(
                    f"selected_cells[{i}].{key}: {value!r} is not in the parent grid axis "
                    f"(allowed: {sorted(axis_values[key])})"
                )

        # Check duplicates via canonical JSON
        cell_canon = canonical_selected_cell_json(cell)
        if cell_canon in seen_cells:
            raise CandidateLockValidationError(
                f"selected_cells[{i}]: duplicate cell {cell_canon}"
            )
        seen_cells.add(cell_canon)


# ---------------------------------------------------------------------------
# Canonical payload (shared between create and validate)
# ---------------------------------------------------------------------------

def canonical_candidate_payload(candidate_lock: DiscoveryCandidateLock) -> str:
    """Build the canonical JSON payload for candidate hashing.

    Accepts ONLY a fully constructed DiscoveryCandidateLock.
    Creation and validation share this one code path.

    Fields included in the hash:
    - parent_grid_id
    - parent_grid_hash
    - parent_grid_schema_version
    - parent_grid_primary_cell_count
    - parent_grid_cost_sensitivity_cell_count
    - selected_cells (canonicalized, sorted)
    - discovery capture manifest hashes (sorted)
    - schema_version

    Fields NOT included:
    - candidate_id
    - frozen_at_utc
    - cluster_summary
    - selection_reason
    - filesystem output path
    - generated_at runtime timestamp
    - discovery capture manifest_path values
    """
    # Extract and sort discovery capture manifest hashes
    capture_hashes = sorted(
        ref.manifest_sha256 for ref in candidate_lock.discovery_captures
    )

    # Build the canonical payload dict
    payload: dict[str, Any] = {
        "parent_grid_id": candidate_lock.parent_grid_id,
        "parent_grid_hash": candidate_lock.parent_grid_hash,
        "parent_grid_schema_version": candidate_lock.parent_grid_schema_version,
        "parent_grid_primary_cell_count": candidate_lock.parent_grid_primary_cell_count,
        "parent_grid_cost_sensitivity_cell_count": candidate_lock.parent_grid_cost_sensitivity_cell_count,
        "schema_version": candidate_lock.schema_version,
        # selected_cells must already be canonicalized and sorted
        "selected_cells": [
            json.loads(canonical_selected_cell_json(cell))
            for cell in candidate_lock.selected_cells
        ],
        "discovery_capture_manifest_hashes": capture_hashes,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def candidate_sha256(candidate_lock: DiscoveryCandidateLock) -> str:
    """Compute SHA-256 hex digest over the canonical candidate payload."""
    canonical = canonical_candidate_payload(candidate_lock)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def _validate_cluster_summary(cluster_summary: dict) -> None:
    """Ensure cluster_summary is JSON-serializable.

    Raises CandidateLockValidationError before any hash computation or file write.
    """
    try:
        json.dumps(cluster_summary, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise CandidateLockValidationError(
            f"cluster_summary is not JSON-serializable: {e}"
        )


def create_candidate_lock(
    grid_spec: DiscoveryGridSpec,
    grid_lock: DiscoveryGridLock,
    candidate_id: str,
    selected_cells: tuple[dict, ...],
    cluster_summary: dict,
    selection_reason: str,
    discovery_capture_refs: tuple[CaptureManifestRef, ...],
) -> DiscoveryCandidateLock:
    """Create a DiscoveryCandidateLock.

    Validates all inputs, then builds a provisional lock, computes the hash
    from the provisional lock's canonical payload, and returns the final lock
    with candidate_hash filled in.

    Args:
        grid_spec: The parent grid spec.
        grid_lock: The parent grid lock.
        candidate_id: Human-readable label (not part of hash).
        selected_cells: Selected cell dicts from the parent grid.
        cluster_summary: JSON-serializable cluster metadata (not part of hash).
        selection_reason: Free-text selection rationale (not part of hash).
        discovery_capture_refs: Capture manifest refs used to surface this candidate.

    Returns:
        A fully populated DiscoveryCandidateLock.
    """
    # Validate candidate_id
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise CandidateLockValidationError("candidate_id must be a non-empty string")

    # Validate that cluster_summary is JSON-serializable BEFORE hashing
    _validate_cluster_summary(cluster_summary)

    # Validate grid spec against grid lock
    validate_grid_spec_against_lock(grid_spec, grid_lock)

    # Validate and canonicalize selected cells
    canonicalized_cells = canonicalize_selected_cells(selected_cells, grid_spec)

    # Validate capture refs
    if not discovery_capture_refs:
        raise CandidateLockValidationError(
            "At least one discovery capture manifest ref is required"
        )

    # Build provisional lock with empty hash
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    provisional = DiscoveryCandidateLock(
        lock_type=CANDIDATE_LOCK_TYPE,
        candidate_id=candidate_id,
        candidate_hash="",
        parent_grid_id=grid_lock.grid_id,
        parent_grid_hash=grid_lock.grid_hash,
        parent_grid_schema_version=grid_lock.schema_version,
        parent_grid_primary_cell_count=grid_lock.primary_cell_count,
        parent_grid_cost_sensitivity_cell_count=grid_lock.cost_sensitivity_cell_count,
        schema_version=CANDIDATE_SCHEMA_VERSION,
        selected_cells=canonicalized_cells,
        cluster_summary=cluster_summary,
        selection_reason=selection_reason,
        discovery_captures=discovery_capture_refs,
        frozen_at_utc=now_utc,
    )

    # Compute hash from provisional lock
    computed_hash = candidate_sha256(provisional)

    # Return final lock with hash filled in
    return DiscoveryCandidateLock(
        lock_type=CANDIDATE_LOCK_TYPE,
        candidate_id=candidate_id,
        candidate_hash=computed_hash,
        parent_grid_id=grid_lock.grid_id,
        parent_grid_hash=grid_lock.grid_hash,
        parent_grid_schema_version=grid_lock.schema_version,
        parent_grid_primary_cell_count=grid_lock.primary_cell_count,
        parent_grid_cost_sensitivity_cell_count=grid_lock.cost_sensitivity_cell_count,
        schema_version=CANDIDATE_SCHEMA_VERSION,
        selected_cells=canonicalized_cells,
        cluster_summary=cluster_summary,
        selection_reason=selection_reason,
        discovery_captures=discovery_capture_refs,
        frozen_at_utc=now_utc,
    )


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

def validate_candidate_lock(
    grid_spec: DiscoveryGridSpec,
    grid_lock: DiscoveryGridLock,
    candidate_lock: DiscoveryCandidateLock,
) -> None:
    """Validate a candidate lock against its parent grid spec and grid lock.

    Raises CandidateLockValidationError (or subclass) on any mismatch.
    """
    # Validate grid spec against grid lock first
    validate_grid_spec_against_lock(grid_spec, grid_lock)

    # lock_type check
    if candidate_lock.lock_type != CANDIDATE_LOCK_TYPE:
        raise CandidateLockValidationError(
            f"Invalid lock_type: expected {CANDIDATE_LOCK_TYPE!r}, "
            f"got {candidate_lock.lock_type!r}"
        )

    # schema_version check
    if candidate_lock.schema_version != CANDIDATE_SCHEMA_VERSION:
        raise CandidateLockValidationError(
            f"candidate schema_version must be {CANDIDATE_SCHEMA_VERSION!r}, "
            f"got {candidate_lock.schema_version!r}"
        )

    # Cross-check parent_grid_id
    if candidate_lock.parent_grid_id != grid_lock.grid_id:
        raise CandidateLockValidationError(
            f"parent_grid_id mismatch: candidate={candidate_lock.parent_grid_id!r}, "
            f"grid_lock={grid_lock.grid_id!r}"
        )
    if candidate_lock.parent_grid_id != grid_spec.grid_id:
        raise CandidateLockValidationError(
            f"parent_grid_id mismatch: candidate={candidate_lock.parent_grid_id!r}, "
            f"grid_spec={grid_spec.grid_id!r}"
        )

    # Cross-check parent_grid_hash
    computed_grid_hash = grid_sha256(grid_spec)
    if candidate_lock.parent_grid_hash != grid_lock.grid_hash:
        raise CandidateLockValidationError(
            f"parent_grid_hash mismatch: candidate={candidate_lock.parent_grid_hash}, "
            f"grid_lock={grid_lock.grid_hash}"
        )
    if candidate_lock.parent_grid_hash != computed_grid_hash:
        raise CandidateHashMismatchError(
            f"parent_grid_hash mismatch: candidate={candidate_lock.parent_grid_hash}, "
            f"computed={computed_grid_hash}"
        )

    # Cross-check parent_grid_schema_version
    if candidate_lock.parent_grid_schema_version != grid_lock.schema_version:
        raise CandidateLockValidationError(
            f"parent_grid_schema_version mismatch: candidate={candidate_lock.parent_grid_schema_version}, "
            f"grid_lock={grid_lock.schema_version}"
        )
    if candidate_lock.parent_grid_schema_version != GRID_SCHEMA_VERSION:
        raise CandidateLockValidationError(
            f"parent_grid_schema_version {candidate_lock.parent_grid_schema_version!r} "
            f"is not compatible with {GRID_SCHEMA_VERSION!r}"
        )

    # Cross-check parent_grid_primary_cell_count
    if candidate_lock.parent_grid_primary_cell_count != grid_lock.primary_cell_count:
        raise GridCellCountMismatchError(
            f"parent_grid_primary_cell_count mismatch: candidate={candidate_lock.parent_grid_primary_cell_count}, "
            f"grid_lock={grid_lock.primary_cell_count}"
        )
    computed_pcc = enumerate_primary_cell_count(grid_spec)
    if candidate_lock.parent_grid_primary_cell_count != computed_pcc:
        raise GridCellCountMismatchError(
            f"parent_grid_primary_cell_count mismatch: candidate={candidate_lock.parent_grid_primary_cell_count}, "
            f"computed={computed_pcc}"
        )

    # Cross-check parent_grid_cost_sensitivity_cell_count
    if candidate_lock.parent_grid_cost_sensitivity_cell_count != grid_lock.cost_sensitivity_cell_count:
        raise GridCellCountMismatchError(
            f"parent_grid_cost_sensitivity_cell_count mismatch: "
            f"candidate={candidate_lock.parent_grid_cost_sensitivity_cell_count}, "
            f"grid_lock={grid_lock.cost_sensitivity_cell_count}"
        )
    computed_csc = enumerate_cost_sensitivity_cell_count(grid_spec)
    if candidate_lock.parent_grid_cost_sensitivity_cell_count != computed_csc:
        raise GridCellCountMismatchError(
            f"parent_grid_cost_sensitivity_cell_count mismatch: "
            f"candidate={candidate_lock.parent_grid_cost_sensitivity_cell_count}, "
            f"computed={computed_csc}"
        )

    # Recompute candidate hash and compare
    computed_candidate_hash = candidate_sha256(candidate_lock)
    if computed_candidate_hash != candidate_lock.candidate_hash:
        raise CandidateHashMismatchError(
            f"candidate_hash mismatch: computed={computed_candidate_hash}, "
            f"stored={candidate_lock.candidate_hash}"
        )


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def _capture_ref_to_dict(ref: CaptureManifestRef) -> dict[str, Any]:
    return {
        "capture_id": ref.capture_id,
        "manifest_path": ref.manifest_path,
        "manifest_sha256": ref.manifest_sha256,
        "data_window_start_utc": ref.data_window_start_utc,
        "data_window_end_utc": ref.data_window_end_utc,
        "stream_count": ref.stream_count,
    }


def _dict_to_capture_ref(data: dict[str, Any]) -> CaptureManifestRef:
    return CaptureManifestRef(
        capture_id=str(data["capture_id"]),
        manifest_path=str(data["manifest_path"]),
        manifest_sha256=str(data["manifest_sha256"]),
        data_window_start_utc=data.get("data_window_start_utc"),
        data_window_end_utc=data.get("data_window_end_utc"),
        stream_count=data.get("stream_count"),
    )


def _lock_to_dict(lock: DiscoveryCandidateLock) -> dict[str, Any]:
    return {
        "lock_type": lock.lock_type,
        "candidate_id": lock.candidate_id,
        "candidate_hash": lock.candidate_hash,
        "parent_grid_id": lock.parent_grid_id,
        "parent_grid_hash": lock.parent_grid_hash,
        "parent_grid_schema_version": lock.parent_grid_schema_version,
        "parent_grid_primary_cell_count": lock.parent_grid_primary_cell_count,
        "parent_grid_cost_sensitivity_cell_count": lock.parent_grid_cost_sensitivity_cell_count,
        "schema_version": lock.schema_version,
        "selected_cells": [dict(c) for c in lock.selected_cells],
        "cluster_summary": lock.cluster_summary,
        "selection_reason": lock.selection_reason,
        "discovery_captures": [_capture_ref_to_dict(r) for r in lock.discovery_captures],
        "frozen_at_utc": lock.frozen_at_utc,
    }


def _dict_to_lock(data: dict[str, Any]) -> DiscoveryCandidateLock:
    return DiscoveryCandidateLock(
        lock_type=str(data["lock_type"]),
        candidate_id=str(data["candidate_id"]),
        candidate_hash=str(data["candidate_hash"]),
        parent_grid_id=str(data["parent_grid_id"]),
        parent_grid_hash=str(data["parent_grid_hash"]),
        parent_grid_schema_version=str(data["parent_grid_schema_version"]),
        parent_grid_primary_cell_count=int(data["parent_grid_primary_cell_count"]),
        parent_grid_cost_sensitivity_cell_count=int(data["parent_grid_cost_sensitivity_cell_count"]),
        schema_version=str(data["schema_version"]),
        selected_cells=tuple(data["selected_cells"]),
        cluster_summary=dict(data.get("cluster_summary", {})),
        selection_reason=str(data.get("selection_reason", "")),
        discovery_captures=tuple(
            _dict_to_capture_ref(r) for r in data.get("discovery_captures", [])
        ),
        frozen_at_utc=str(data["frozen_at_utc"]),
    )


def candidate_locks_are_semantically_identical(
    lock_a: DiscoveryCandidateLock,
    lock_b: DiscoveryCandidateLock,
) -> bool:
    """Compare two candidate locks by their candidate_hash."""
    return lock_a.candidate_hash == lock_b.candidate_hash


def load_candidate_lock(path: str | Path) -> DiscoveryCandidateLock:
    """Load a DiscoveryCandidateLock from a JSON file."""
    path = Path(path)
    with open(path, "r") as f:
        data = json.load(f)
    return _dict_to_lock(data)


def save_candidate_lock(lock: DiscoveryCandidateLock, path: str | Path) -> None:
    """Save a DiscoveryCandidateLock to a JSON file."""
    path = Path(path)
    data = _lock_to_dict(lock)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
