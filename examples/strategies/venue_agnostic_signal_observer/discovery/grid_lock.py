"""Grid-level lock file for discovery freeze.

A DiscoveryGridLock freezes the grid spec at discovery-start time.
The lock records grid_hash, primary_cell_count, and cost_sensitivity_cell_count
so that validation can detect any drift in enumeration logic.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .exceptions import (
    GridHashMismatchError,
    GridLockValidationError,
    GridCellCountMismatchError,
)
from .search_space import (
    DiscoveryGridSpec,
    validate_grid_spec,
    canonical_grid_json,
    grid_sha256,
    enumerate_primary_cell_count,
    enumerate_cost_sensitivity_cell_count,
    GRID_SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRID_LOCK_TYPE = "DISCOVERY_GRID_LOCK"


# ---------------------------------------------------------------------------
# Grid Lock
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveryGridLock:
    """Frozen grid-level lock that records what was searched.

    Attributes:
        lock_type: Must be "DISCOVERY_GRID_LOCK".
        grid_id: Matches the grid spec's grid_id.
        grid_hash: SHA-256 of the grid spec's canonical JSON.
        schema_version: Grid schema version.
        primary_cell_count: Computed primary cell count of the grid spec.
        cost_sensitivity_cell_count: Computed cost-sensitivity cell count.
        git_sha: Optional git commit SHA from git rev-parse HEAD.
        locked_at_utc: ISO-8601 UTC timestamp of lock creation.
    """

    lock_type: str
    grid_id: str
    grid_hash: str
    schema_version: str
    primary_cell_count: int
    cost_sensitivity_cell_count: int
    git_sha: str | None
    locked_at_utc: str


# ---------------------------------------------------------------------------
# git_sha discovery
# ---------------------------------------------------------------------------

def _discover_git_sha() -> str | None:
    """Run git rev-parse HEAD to discover the current commit.

    Returns None if git is unavailable or the command fails.
    Never raises an exception.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            return sha if sha else None
        return None
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None


# ---------------------------------------------------------------------------
# Create / Validate
# ---------------------------------------------------------------------------

def create_grid_lock(spec: DiscoveryGridSpec) -> DiscoveryGridLock:
    """Create a DiscoveryGridLock from a validated grid spec.

    Validates the spec first, then computes hash and cell counts.
    """
    validate_grid_spec(spec)

    grid_hash = grid_sha256(spec)
    primary_cell_count = enumerate_primary_cell_count(spec)
    cost_sensitivity_cell_count = enumerate_cost_sensitivity_cell_count(spec)
    git_sha = _discover_git_sha()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return DiscoveryGridLock(
        lock_type=GRID_LOCK_TYPE,
        grid_id=spec.grid_id,
        grid_hash=grid_hash,
        schema_version=spec.schema_version,
        primary_cell_count=primary_cell_count,
        cost_sensitivity_cell_count=cost_sensitivity_cell_count,
        git_sha=git_sha,
        locked_at_utc=now_utc,
    )


def validate_grid_spec_against_lock(
    spec: DiscoveryGridSpec,
    lock: DiscoveryGridLock,
) -> None:
    """Validate that a grid spec matches an existing grid lock.

    Raises GridLockValidationError (or subclass) on any mismatch.
    Does NOT silently regenerate locks.
    """
    # lock_type check
    if lock.lock_type != GRID_LOCK_TYPE:
        raise GridLockValidationError(
            f"Invalid lock_type: expected {GRID_LOCK_TYPE!r}, got {lock.lock_type!r}"
        )

    # grid_id
    if spec.grid_id != lock.grid_id:
        raise GridLockValidationError(
            f"grid_id mismatch: spec={spec.grid_id!r}, lock={lock.grid_id!r}"
        )

    # schema_version
    if spec.schema_version != lock.schema_version:
        raise GridLockValidationError(
            f"schema_version mismatch: spec={spec.schema_version!r}, lock={lock.schema_version!r}"
        )

    # grid_hash
    computed_hash = grid_sha256(spec)
    if computed_hash != lock.grid_hash:
        raise GridHashMismatchError(
            f"grid_hash mismatch: computed={computed_hash}, lock={lock.grid_hash}"
        )

    # primary_cell_count
    computed_pcc = enumerate_primary_cell_count(spec)
    if computed_pcc != lock.primary_cell_count:
        raise GridCellCountMismatchError(
            f"primary_cell_count mismatch: computed={computed_pcc}, lock={lock.primary_cell_count}"
        )

    # cost_sensitivity_cell_count
    computed_csc = enumerate_cost_sensitivity_cell_count(spec)
    if computed_csc != lock.cost_sensitivity_cell_count:
        raise GridCellCountMismatchError(
            f"cost_sensitivity_cell_count mismatch: computed={computed_csc}, "
            f"lock={lock.cost_sensitivity_cell_count}"
        )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def locks_are_semantically_identical(
    lock_a: DiscoveryGridLock,
    lock_b: DiscoveryGridLock,
) -> bool:
    """Compare two locks by canonical JSON of their payloads.

    Whitespace and key order in the original file do not matter.
    Only semantic equality of all fields matters.
    """
    return _lock_canonical(lock_a) == _lock_canonical(lock_b)


def _lock_canonical(lock: DiscoveryGridLock) -> str:
    """Serialize a lock to canonical JSON for comparison."""
    payload: dict[str, Any] = {
        "lock_type": lock.lock_type,
        "grid_id": lock.grid_id,
        "grid_hash": lock.grid_hash,
        "schema_version": lock.schema_version,
        "primary_cell_count": lock.primary_cell_count,
        "cost_sensitivity_cell_count": lock.cost_sensitivity_cell_count,
        "git_sha": lock.git_sha,
        "locked_at_utc": lock.locked_at_utc,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def _dict_to_lock(data: dict[str, Any]) -> DiscoveryGridLock:
    """Convert a parsed JSON dict to a DiscoveryGridLock."""
    return DiscoveryGridLock(
        lock_type=str(data["lock_type"]),
        grid_id=str(data["grid_id"]),
        grid_hash=str(data["grid_hash"]),
        schema_version=str(data["schema_version"]),
        primary_cell_count=int(data["primary_cell_count"]),
        cost_sensitivity_cell_count=int(data["cost_sensitivity_cell_count"]),
        git_sha=data.get("git_sha"),  # may be None
        locked_at_utc=str(data["locked_at_utc"]),
    )


def _lock_to_dict(lock: DiscoveryGridLock) -> dict[str, Any]:
    """Convert a DiscoveryGridLock to a JSON-compatible dict."""
    return {
        "lock_type": lock.lock_type,
        "grid_id": lock.grid_id,
        "grid_hash": lock.grid_hash,
        "schema_version": lock.schema_version,
        "primary_cell_count": lock.primary_cell_count,
        "cost_sensitivity_cell_count": lock.cost_sensitivity_cell_count,
        "git_sha": lock.git_sha,
        "locked_at_utc": lock.locked_at_utc,
    }


def load_grid_lock(path: str | Path) -> DiscoveryGridLock:
    """Load a DiscoveryGridLock from a JSON file."""
    path = Path(path)
    with open(path, "r") as f:
        data = json.load(f)
    return _dict_to_lock(data)


def save_grid_lock(lock: DiscoveryGridLock, path: str | Path) -> None:
    """Save a DiscoveryGridLock to a JSON file."""
    path = Path(path)
    data = _lock_to_dict(lock)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
