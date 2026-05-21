# Copyright (C) 2026. All rights reserved.
"""
Candidate-level lock file for the discovery freeze.

A ``DiscoveryCandidateLock`` freezes a specific discovered rule/cluster
after a discovery scan surfaces it.  It pins the exact grid spec, grid
lock, selected cells, and discovery capture manifest hashes so that a
later validator can independently verify data provenance.

Preserving ``capture_id`` is a convenience for humans; the machine-level
trust anchor is ``manifest_sha256``.  ``manifest_path`` is convenience
metadata only — it is **not** included in the candidate hash because
paths are environment-specific.

Although ``cluster_summary`` and ``selection_reason`` are excluded from
the candidate hash, saved candidate lock files are **immutable artifacts**.
Do not mutate them in place; write a new artifact if explanatory metadata
changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from .capture_fingerprint import CaptureManifestRef
from .capture_fingerprint import build_capture_manifest_ref
from .exceptions import CandidateHashMismatchError
from .exceptions import CandidateLockValidationError
from .exceptions import GridCellCountMismatchError
from .grid_lock import DiscoveryGridLock
from .grid_lock import validate_grid_lock
from .search_space import DiscoveryGridSpec
from .search_space import enumerate_cost_sensitivity_cell_count
from .search_space import enumerate_primary_cell_count
from .search_space import grid_sha256


CANDIDATE_SCHEMA_VERSION = "discovery-candidate-v1"
CANDIDATE_LOCK_TYPE = "DISCOVERY_CANDIDATE_LOCK"

# Primary counted axis keys that every selected cell must contain
_SELECTED_CELL_KEYS: tuple[str, ...] = (
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
_SELECTED_CELL_KEY_SET: frozenset[str] = frozenset(_SELECTED_CELL_KEYS)


@dataclass(frozen=True)
class DiscoveryCandidateLock:
    """
    Frozen lock that records a specific candidate discovery result.

    Fields
    ------
    lock_type : str
        Always ``"DISCOVERY_CANDIDATE_LOCK"``.
    candidate_id : str
        Human-readable label.  **Not** part of the candidate hash.
    candidate_hash : str
        SHA-256 hex digest over canonical candidate payload.
    parent_grid_id : str
    parent_grid_hash : str
    parent_grid_schema_version : str
    parent_grid_primary_cell_count : int
    parent_grid_cost_sensitivity_cell_count : int
    schema_version : str
    selected_cells : tuple[dict, ...]
    cluster_summary : dict
        Excluded from candidate hash.  Must be JSON-serializable.
    selection_reason : str
        Excluded from candidate hash.
    discovery_captures : tuple[CaptureManifestRef, ...]
    frozen_at_utc : str
    """

    lock_type: str = CANDIDATE_LOCK_TYPE
    candidate_id: str = ""
    candidate_hash: str = ""
    parent_grid_id: str = ""
    parent_grid_hash: str = ""
    parent_grid_schema_version: str = ""
    parent_grid_primary_cell_count: int = 0
    parent_grid_cost_sensitivity_cell_count: int = 0
    schema_version: str = CANDIDATE_SCHEMA_VERSION
    selected_cells: tuple[dict, ...] = ()
    cluster_summary: dict = None  # type: ignore[assignment]
    selection_reason: str = ""
    discovery_captures: tuple[CaptureManifestRef, ...] = ()
    frozen_at_utc: str = ""


# ---------------------------------------------------------------------------
# Selected cell helpers
# ---------------------------------------------------------------------------


def canonical_selected_cell_json(cell: dict[str, Any]) -> str:
    """
    Build canonical JSON for a single selected cell.

    - Sorted keys.
    - Compact separators (",", ":").
    - ``ensure_ascii=False``.
    """
    clean = {k: cell[k] for k in _SELECTED_CELL_KEYS if k in cell}
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _cell_axis_values(grid_spec: DiscoveryGridSpec) -> dict[str, frozenset]:
    """Build a lookup of axis name -> frozenset of valid values."""
    result: dict[str, frozenset] = {}
    for axis in _SELECTED_CELL_KEYS:
        vals: set = set(getattr(grid_spec, axis))
        result[axis] = frozenset(vals)
    return result


def validate_selected_cells(
    cells: list[dict[str, Any]],
    grid_spec: DiscoveryGridSpec,
) -> None:
    """
    Validate selected cells against a grid spec.

    Raises
    ------
    CandidateLockValidationError
        If any cell is invalid, contains extra/missing keys, has a value
        outside the parent grid axis, or duplicates another cell.
    """
    if not cells:
        raise CandidateLockValidationError("selected_cells must be non-empty")

    valid_axes = _cell_axis_values(grid_spec)
    seen_canonical: set[str] = set()

    for i, cell in enumerate(cells):
        cell_keys = frozenset(cell.keys())

        # Extra keys
        extra = cell_keys - _SELECTED_CELL_KEY_SET
        if extra:
            raise CandidateLockValidationError(
                f"Selected cell {i} has unexpected keys: {sorted(extra)}"
            )

        # Missing keys
        missing = _SELECTED_CELL_KEY_SET - cell_keys
        if missing:
            raise CandidateLockValidationError(
                f"Selected cell {i} has missing keys: {sorted(missing)}"
            )

        # Validate each value against parent grid axis
        for axis in _SELECTED_CELL_KEYS:
            val = cell[axis]
            valid_values = valid_axes[axis]
            if val not in valid_values:
                raise CandidateLockValidationError(
                    f"Selected cell {i}: {axis}={val!r} is not in the parent grid "
                    f"axis {sorted(valid_values)}"
                )

        # Duplicate check via canonical JSON
        canonical = canonical_selected_cell_json(cell)
        if canonical in seen_canonical:
            raise CandidateLockValidationError(
                f"Selected cell {i} is a duplicate cell of an earlier cell "
                f"(canonical: {canonical})"
            )
        seen_canonical.add(canonical)


def canonicalize_selected_cells(
    cells: list[dict[str, Any]],
    grid_spec: DiscoveryGridSpec,
) -> list[dict[str, Any]]:
    """
    Validate and canonically sort selected cells.

    Sort key: canonical JSON string of each cell.
    Returns the sorted list of cell dicts (already validated).
    """
    validate_selected_cells(cells, grid_spec)
    return sorted(cells, key=canonical_selected_cell_json)


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def canonical_candidate_payload(lock: DiscoveryCandidateLock) -> dict[str, Any]:
    """
    Build the canonical dict for candidate hashing.

    Accepts **only** a fully constructed ``DiscoveryCandidateLock``.
    This ensures creation and validation share one hashing code path.

    Excludes from hash:
    - candidate_id (free-form label)
    - frozen_at_utc (runtime timestamp)
    - cluster_summary (explanatory metadata)
    - selection_reason (explanatory metadata)
    - discovery_captures manifest_path (environment-specific)
    """
    # Canonicalize selected cells
    sorted_cells = sorted(
        [dict(c) for c in lock.selected_cells],
        key=canonical_selected_cell_json,
    )

    # Sort manifest SHA-256 values
    sorted_manifest_hashes = sorted(
        [ref.manifest_sha256 for ref in lock.discovery_captures]
    )

    payload: dict[str, Any] = {
        "parent_grid_id": lock.parent_grid_id,
        "parent_grid_hash": lock.parent_grid_hash,
        "parent_grid_schema_version": lock.parent_grid_schema_version,
        "parent_grid_primary_cell_count": lock.parent_grid_primary_cell_count,
        "parent_grid_cost_sensitivity_cell_count": lock.parent_grid_cost_sensitivity_cell_count,
        "schema_version": lock.schema_version,
        "selected_cells": sorted_cells,
        "discovery_manifest_hashes": sorted_manifest_hashes,
    }
    return payload


def _canonical_candidate_json(lock: DiscoveryCandidateLock) -> str:
    """
    Canonical JSON for candidate hash payload.

    - Sorted object keys.
    - Compact separators (",", ":").
    - ``ensure_ascii=False``.
    """
    payload = canonical_candidate_payload(lock)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def candidate_sha256(lock: DiscoveryCandidateLock) -> str:
    """Compute the SHA-256 hex digest of a candidate lock (hash payload only)."""
    return hashlib.sha256(
        _canonical_candidate_json(lock).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_candidate_lock(
    *,
    candidate_id: str,
    grid_spec: DiscoveryGridSpec,
    grid_lock: DiscoveryGridLock,
    selected_cells: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    cluster_summary: dict[str, Any],
    selection_reason: str,
    capture_paths: list[str | Path] | tuple[str | Path, ...] | None = None,
    discovery_capture_refs: tuple[CaptureManifestRef, ...] | list[CaptureManifestRef] | None = None,
    frozen_at_utc: str | None = None,
) -> DiscoveryCandidateLock:
    """
    Create a candidate lock from validated inputs.

    Parameters
    ----------
    candidate_id : str
        Human-readable label.  Not part of the candidate hash.
    grid_spec : DiscoveryGridSpec
        The parent grid spec (required for selected cell validation).
    grid_lock : DiscoveryGridLock
        The parent grid lock.
    selected_cells : list[dict]
        Cells from the parent grid that define the cluster.
    cluster_summary : dict
        Explanatory metadata.  Must be JSON-serializable.
    selection_reason : str
        Why this cluster was selected.
    capture_paths : list[str | Path]
        Paths to capture manifest files on disk.
    discovery_capture_refs : tuple[CaptureManifestRef, ...] | None
        Pre-built capture manifest references.  Preserves legacy
        ``capture_paths`` support while allowing tests/callers to pass refs
        without path-dependent hash semantics.
    frozen_at_utc : str | None
        Optional timestamp override.  Defaults to current UTC ISO-8601.

    Returns
    -------
    DiscoveryCandidateLock
    """
    # --- Validate candidate_id ---
    if not candidate_id or not candidate_id.strip():
        raise CandidateLockValidationError(
            "candidate_id must be a non-empty, non-whitespace string"
        )

    # --- Validate grid lock ---
    validate_grid_lock(grid_spec, grid_lock)

    # --- Validate selected cells ---
    selected_cell_list = list(selected_cells)
    validate_selected_cells(selected_cell_list, grid_spec)

    # Must have at least some captures
    if capture_paths and discovery_capture_refs:
        raise CandidateLockValidationError(
            "Provide either capture_paths or discovery_capture_refs, not both"
        )
    if not capture_paths and not discovery_capture_refs:
        raise CandidateLockValidationError(
            "At least one discovery capture manifest ref or path is required"
        )

    # --- Validate cluster_summary is JSON-serializable ---
    try:
        json.dumps(cluster_summary, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise CandidateLockValidationError(
            f"cluster_summary is not JSON-serializable: {exc}"
        ) from exc

    # --- Build capture manifest refs ---
    capture_refs: list[CaptureManifestRef] = []
    if discovery_capture_refs is not None:
        capture_refs = list(discovery_capture_refs)
    else:
        for cp in capture_paths or ():
            ref = build_capture_manifest_ref(cp)
            capture_refs.append(ref)

    if frozen_at_utc is None:
        frozen_at_utc = datetime.now(UTC).isoformat()

    # Build provisional lock with empty hash
    provisional = DiscoveryCandidateLock(
        lock_type=CANDIDATE_LOCK_TYPE,
        candidate_id=candidate_id,
        candidate_hash="",
        parent_grid_id=grid_spec.grid_id,
        parent_grid_hash=grid_lock.grid_hash,
        parent_grid_schema_version=grid_spec.schema_version,
        parent_grid_primary_cell_count=enumerate_primary_cell_count(grid_spec),
        parent_grid_cost_sensitivity_cell_count=enumerate_cost_sensitivity_cell_count(grid_spec),
        schema_version=CANDIDATE_SCHEMA_VERSION,
        selected_cells=tuple(selected_cell_list),
        cluster_summary=cluster_summary,
        selection_reason=selection_reason,
        discovery_captures=tuple(capture_refs),
        frozen_at_utc=frozen_at_utc,
    )

    # Compute hash
    computed_hash = candidate_sha256(provisional)

    # Return final lock
    return DiscoveryCandidateLock(
        lock_type=CANDIDATE_LOCK_TYPE,
        candidate_id=candidate_id,
        candidate_hash=computed_hash,
        parent_grid_id=provisional.parent_grid_id,
        parent_grid_hash=provisional.parent_grid_hash,
        parent_grid_schema_version=provisional.parent_grid_schema_version,
        parent_grid_primary_cell_count=provisional.parent_grid_primary_cell_count,
        parent_grid_cost_sensitivity_cell_count=provisional.parent_grid_cost_sensitivity_cell_count,
        schema_version=CANDIDATE_SCHEMA_VERSION,
        selected_cells=provisional.selected_cells,
        cluster_summary=cluster_summary,
        selection_reason=selection_reason,
        discovery_captures=provisional.discovery_captures,
        frozen_at_utc=frozen_at_utc,
    )


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def validate_candidate_lock(
    grid_spec: DiscoveryGridSpec,
    grid_lock: DiscoveryGridLock,
    candidate_lock: DiscoveryCandidateLock,
) -> None:
    """Validate a candidate lock against its parent grid and grid lock."""
    # --- Validate grid lock ---
    validate_grid_lock(grid_spec, grid_lock)

    # --- lock_type ---
    if candidate_lock.lock_type != CANDIDATE_LOCK_TYPE:
        raise CandidateLockValidationError(
            f"Invalid lock_type: expected '{CANDIDATE_LOCK_TYPE}', "
            f"got '{candidate_lock.lock_type}'"
        )

    # --- schema_version ---
    if candidate_lock.schema_version != CANDIDATE_SCHEMA_VERSION:
        raise CandidateLockValidationError(
            f"Candidate lock schema_version must be '{CANDIDATE_SCHEMA_VERSION}', "
            f"got '{candidate_lock.schema_version}'"
        )

    # --- Compatibility matrix ---
    if candidate_lock.parent_grid_schema_version != "discovery-grid-v1":
        raise CandidateLockValidationError(
            f"Candidate lock parent_grid_schema_version must be "
            f"'discovery-grid-v1', got '{candidate_lock.parent_grid_schema_version}'"
        )

    # --- Cross-check parent_grid_id ---
    if candidate_lock.parent_grid_id != grid_spec.grid_id:
        raise CandidateLockValidationError(
            f"parent_grid_id mismatch: lock has '{candidate_lock.parent_grid_id}', "
            f"grid spec has '{grid_spec.grid_id}'"
        )
    if candidate_lock.parent_grid_id != grid_lock.grid_id:
        raise CandidateLockValidationError(
            f"parent_grid_id mismatch against grid lock: "
            f"lock has '{candidate_lock.parent_grid_id}', "
            f"grid lock has '{grid_lock.grid_id}'"
        )

    # --- Cross-check parent_grid_hash ---
    recomputed_hash = grid_sha256(grid_spec)
    if candidate_lock.parent_grid_hash != recomputed_hash:
        raise CandidateHashMismatchError(
            f"parent_grid_hash mismatch: lock has {candidate_lock.parent_grid_hash}, "
            f"recomputed {recomputed_hash}"
        )
    if candidate_lock.parent_grid_hash != grid_lock.grid_hash:
        raise CandidateLockValidationError(
            f"parent_grid_hash mismatch against grid lock: "
            f"lock has {candidate_lock.parent_grid_hash}, "
            f"grid lock has {grid_lock.grid_hash}"
        )

    # --- Cross-check parent_grid_schema_version ---
    if candidate_lock.parent_grid_schema_version != grid_spec.schema_version:
        raise CandidateLockValidationError(
            f"parent_grid_schema_version mismatch against grid spec: "
            f"lock has '{candidate_lock.parent_grid_schema_version}', "
            f"grid spec has '{grid_spec.schema_version}'"
        )
    if candidate_lock.parent_grid_schema_version != grid_lock.schema_version:
        raise CandidateLockValidationError(
            f"parent_grid_schema_version mismatch against grid lock: "
            f"lock has '{candidate_lock.parent_grid_schema_version}', "
            f"grid lock has '{grid_lock.schema_version}'"
        )

    # --- Cross-check parent_grid_primary_cell_count ---
    recomputed_primary = enumerate_primary_cell_count(grid_spec)
    if candidate_lock.parent_grid_primary_cell_count != recomputed_primary:
        raise GridCellCountMismatchError(
            f"parent_grid_primary_cell_count mismatch against recomputed grid spec: "
            f"lock has {candidate_lock.parent_grid_primary_cell_count}, "
            f"recomputed {recomputed_primary}"
        )
    if candidate_lock.parent_grid_primary_cell_count != grid_lock.primary_cell_count:
        raise GridCellCountMismatchError(
            f"parent_grid_primary_cell_count mismatch against grid lock: "
            f"lock has {candidate_lock.parent_grid_primary_cell_count}, "
            f"grid lock has {grid_lock.primary_cell_count}"
        )

    # --- Cross-check parent_grid_cost_sensitivity_cell_count ---
    recomputed_cost = enumerate_cost_sensitivity_cell_count(grid_spec)
    if candidate_lock.parent_grid_cost_sensitivity_cell_count != recomputed_cost:
        raise GridCellCountMismatchError(
            f"parent_grid_cost_sensitivity_cell_count mismatch against "
            f"recomputed grid spec: "
            f"lock has {candidate_lock.parent_grid_cost_sensitivity_cell_count}, "
            f"recomputed {recomputed_cost}"
        )
    if (
        candidate_lock.parent_grid_cost_sensitivity_cell_count
        != grid_lock.cost_sensitivity_cell_count
    ):
        raise GridCellCountMismatchError(
            f"parent_grid_cost_sensitivity_cell_count mismatch against grid lock: "
            f"lock has {candidate_lock.parent_grid_cost_sensitivity_cell_count}, "
            f"grid lock has {grid_lock.cost_sensitivity_cell_count}"
        )

    # --- Recompute candidate hash ---
    recomputed_candidate_hash = candidate_sha256(candidate_lock)
    if recomputed_candidate_hash != candidate_lock.candidate_hash:
        raise CandidateHashMismatchError(
            f"candidate_hash mismatch for candidate "
            f"'{candidate_lock.candidate_id}': "
            f"stored {candidate_lock.candidate_hash}, "
            f"recomputed {recomputed_candidate_hash}"
        )

    # --- Validate selected cells against grid spec ---
    validate_selected_cells(list(candidate_lock.selected_cells), grid_spec)

    # --- Must have discovery captures ---
    if not candidate_lock.discovery_captures:
        raise CandidateLockValidationError(
            "Candidate lock must have at least one discovery capture manifest ref"
        )

    # --- Must have selected cells ---
    if not candidate_lock.selected_cells:
        raise CandidateLockValidationError(
            "Candidate lock must have at least one selected cell"
        )


# ---------------------------------------------------------------------------
# Semantic equality
# ---------------------------------------------------------------------------


def _canonical_candidate_lock_json(lock: DiscoveryCandidateLock) -> str:
    """Canonical JSON for comparing candidate locks semantically."""
    d = asdict(lock)
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _candidate_lock_dict_from_file(path: Path) -> dict[str, Any]:
    """Load a candidate lock file and return the parsed dict."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def candidate_locks_are_semantically_equal(
    lock_a: DiscoveryCandidateLock,
    lock_b: DiscoveryCandidateLock,
) -> bool:
    """Compare two candidate locks via canonical JSON."""
    return _canonical_candidate_lock_json(lock_a) == _canonical_candidate_lock_json(lock_b)


def candidate_lock_file_is_semantically_identical(
    lock: DiscoveryCandidateLock,
    path: Path,
) -> bool:
    """Check if a candidate lock file on disk matches *lock* semantically."""
    existing_dict = _candidate_lock_dict_from_file(path)
    if not existing_dict:
        return False
    existing_lock = DiscoveryCandidateLock(**existing_dict)
    return candidate_locks_are_semantically_equal(lock, existing_lock)


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def save_candidate_lock(lock: DiscoveryCandidateLock, path: str | Path) -> None:
    """
    Write a candidate lock to a JSON file.

    Raises
    ------
    FileExistsError
        If the file exists and content is **not** semantically identical.
    """
    path = Path(path)
    if path.exists():
        if candidate_lock_file_is_semantically_identical(lock, path):
            return
        raise FileExistsError(
            f"Candidate lock file already exists with different content: {path}. "
            "Create a new candidate_id instead.  No --force option exists."
        )
    data = asdict(lock)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_candidate_lock(path: str | Path) -> DiscoveryCandidateLock:
    """Load a candidate lock from a JSON file."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))

    captures_data = data.get("discovery_captures", [])
    captures = tuple(CaptureManifestRef(**c) for c in captures_data)

    selected_data = data.get("selected_cells", [])
    selected = tuple(dict(c) for c in selected_data)

    return DiscoveryCandidateLock(
        lock_type=data.get("lock_type", CANDIDATE_LOCK_TYPE),
        candidate_id=data.get("candidate_id", ""),
        candidate_hash=data.get("candidate_hash", ""),
        parent_grid_id=data.get("parent_grid_id", ""),
        parent_grid_hash=data.get("parent_grid_hash", ""),
        parent_grid_schema_version=data.get("parent_grid_schema_version", ""),
        parent_grid_primary_cell_count=data.get("parent_grid_primary_cell_count", 0),
        parent_grid_cost_sensitivity_cell_count=data.get(
            "parent_grid_cost_sensitivity_cell_count", 0
        ),
        schema_version=data.get("schema_version", CANDIDATE_SCHEMA_VERSION),
        selected_cells=selected,
        cluster_summary=data.get("cluster_summary", {}),
        selection_reason=data.get("selection_reason", ""),
        discovery_captures=captures,
        frozen_at_utc=data.get("frozen_at_utc", ""),
    )
