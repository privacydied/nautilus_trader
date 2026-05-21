# Copyright (C) 2026. All rights reserved.
"""
Grid-level lock file for the discovery freeze.

A ``DiscoveryGridLock`` freezes the full search space and the FDR
denominator before any discovery scan begins.  It records the grid
spec hash and the exact primary cell count so that a later validator
can prove the grid has not drifted.

The two-freeze rule
-------------------
1. Grid freeze before discovery scan starts.
   Freezes the full search space and the FDR denominator.

2. Candidate freeze after a cluster surfaces.
   Freezes the exact discovered rule/cluster and the discovery data
   it came from.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from .exceptions import GridCellCountMismatchError
from .exceptions import GridHashMismatchError
from .exceptions import GridLockValidationError
from .search_space import DiscoveryGridSpec
from .search_space import enumerate_cost_sensitivity_cell_count
from .search_space import enumerate_primary_cell_count
from .search_space import grid_sha256
from .search_space import validate_grid_spec


GRID_LOCK_TYPE = "DISCOVERY_GRID_LOCK"


def _get_git_sha() -> str | None:
    """
    Return ``git rev-parse HEAD`` or ``None`` if unavailable.

    Never raises.  Never falls back to any other command.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    return None


@dataclass(frozen=True)
class DiscoveryGridLock:
    """
    Frozen lock that records a discovery grid hash and cell counts.

    Validated fields
    ----------------
    lock_type : ``"DISCOVERY_GRID_LOCK"``
    grid_id : str
        Must match the parent ``DiscoveryGridSpec.grid_id``.
    grid_hash : str
        SHA-256 hex digest of the canonical grid JSON.
    schema_version : str
        Must be ``"discovery-grid-v1"``.
    primary_cell_count : int
        The enumerated primary cell count of the parent grid.
    cost_sensitivity_cell_count : int
        ``primary_cell_count × len(cost_models_centibps)``.
    git_sha : str | None
        Optional git commit SHA at lock time.  ``None`` is valid
        (CI environments without git).
    locked_at_utc : str
        ISO-8601 UTC timestamp.
    """

    lock_type: str = GRID_LOCK_TYPE
    grid_id: str = ""
    grid_hash: str = ""
    schema_version: str = ""
    primary_cell_count: int = 0
    cost_sensitivity_cell_count: int = 0
    git_sha: str | None = None
    locked_at_utc: str = ""


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_grid_lock(
    spec: DiscoveryGridSpec,
    *,
    locked_at_utc: str | None = None,
) -> DiscoveryGridLock:
    """
    Create a grid lock from a validated grid spec.

    Parameters
    ----------
    spec : DiscoveryGridSpec
        The grid spec to freeze.  Must be validated first.
    locked_at_utc : str | None
        Optional override timestamp.  Defaults to current UTC ISO-8601.

    Returns
    -------
    DiscoveryGridLock

    Raises
    ------
    GridSpecValidationError
        If the spec fails validation.
    """
    validate_grid_spec(spec)
    if locked_at_utc is None:
        locked_at_utc = datetime.now(UTC).isoformat()

    grid_hash = grid_sha256(spec)
    primary = enumerate_primary_cell_count(spec)
    cost_sens = enumerate_cost_sensitivity_cell_count(spec)

    git_sha = _get_git_sha()

    return DiscoveryGridLock(
        lock_type=GRID_LOCK_TYPE,
        grid_id=spec.grid_id,
        grid_hash=grid_hash,
        schema_version=spec.schema_version,
        primary_cell_count=primary,
        cost_sensitivity_cell_count=cost_sens,
        git_sha=git_sha,
        locked_at_utc=locked_at_utc,
    )


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


def validate_grid_lock(
    spec: DiscoveryGridSpec,
    lock: DiscoveryGridLock,
) -> None:
    """
    Validate that a grid spec matches an existing grid lock.

    Raises
    ------
    GridSpecValidationError
        If the spec fails validation.
    GridLockValidationError
        If the lock is inconsistent with the spec.

    Does **not** silently regenerate or overwrite locks.
    """
    validate_grid_spec(spec)

    if lock.lock_type != GRID_LOCK_TYPE:
        raise GridLockValidationError(
            f"Invalid lock_type: expected '{GRID_LOCK_TYPE}', "
            f"got '{lock.lock_type}'"
        )

    if lock.grid_id != spec.grid_id:
        raise GridLockValidationError(
            f"grid_id mismatch: lock has '{lock.grid_id}', "
            f"spec has '{spec.grid_id}'"
        )

    if lock.schema_version != spec.schema_version:
        raise GridLockValidationError(
            f"schema_version mismatch: lock has '{lock.schema_version}', "
            f"spec has '{spec.schema_version}'"
        )

    recomputed_hash = grid_sha256(spec)
    if recomputed_hash != lock.grid_hash:
        raise GridHashMismatchError(
            f"grid_hash mismatch for grid '{spec.grid_id}': "
            f"lock has {lock.grid_hash}, recomputed {recomputed_hash}"
        )

    recomputed_primary = enumerate_primary_cell_count(spec)
    if recomputed_primary != lock.primary_cell_count:
        raise GridCellCountMismatchError(
            f"primary_cell_count mismatch: lock has {lock.primary_cell_count}, "
            f"recomputed {recomputed_primary}"
        )

    recomputed_cost = enumerate_cost_sensitivity_cell_count(spec)
    if recomputed_cost != lock.cost_sensitivity_cell_count:
        raise GridCellCountMismatchError(
            f"cost_sensitivity_cell_count mismatch: "
            f"lock has {lock.cost_sensitivity_cell_count}, "
            f"recomputed {recomputed_cost}"
        )


# ---------------------------------------------------------------------------
# Semantic equality
# ---------------------------------------------------------------------------


def _canonical_lock_json(lock: DiscoveryGridLock) -> str:
    """Canonical JSON for a lock dict (sorted keys, compact separators)."""
    d = asdict(lock)
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _lock_dict_from_file(path: Path) -> dict[str, Any]:
    """Load a lock file and return the parsed dict."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def locks_are_semantically_equal(lock_a: DiscoveryGridLock,
                                 lock_b: DiscoveryGridLock) -> bool:
    """Compare two locks via canonical JSON (ignores whitespace, key order)."""
    return _canonical_lock_json(lock_a) == _canonical_lock_json(lock_b)


def lock_file_is_semantically_identical(
    lock: DiscoveryGridLock,
    path: Path,
) -> bool:
    """Check if a lock file on disk is semantically identical to *lock*."""
    existing_dict = _lock_dict_from_file(path)
    if not existing_dict:
        return False
    # Parse into a DiscoveryGridLock for canonical comparison
    existing_lock = DiscoveryGridLock(**existing_dict)
    return locks_are_semantically_equal(lock, existing_lock)


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def save_grid_lock(lock: DiscoveryGridLock, path: str | Path) -> None:
    """
    Write a grid lock to a JSON file.

    Raises
    ------
    FileExistsError
        If the file exists and the content is **not** semantically
        identical.
    """
    path = Path(path)
    if path.exists():
        if lock_file_is_semantically_identical(lock, path):
            return  # Already locked with identical content
        raise FileExistsError(
            f"Lock file already exists with different content: {path}. "
            "Create a new grid_id instead.  No --force option exists."
        )
    data = asdict(lock)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_grid_lock(path: str | Path) -> DiscoveryGridLock:
    """Load a grid lock from a JSON file."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return DiscoveryGridLock(**data)
