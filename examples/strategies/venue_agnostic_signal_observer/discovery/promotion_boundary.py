"""Structural choke point between discovery and validation.

Discovery outputs are NOT trading candidates.
Validators must consume DiscoveryCandidateLock via this function.
Validators must NOT consume ad-hoc discovery output.
Trading code must NEVER consume discovery output directly.

Phase 1 cannot force Phase 5 to call this function; Phase 5 must wire it as the
sole entrypoint into validation. This module exists so that contract has a single
named, tested home.
"""

from __future__ import annotations

from .candidate_lock import (
    DiscoveryCandidateLock,
    validate_candidate_lock,
)
from .grid_lock import DiscoveryGridLock
from .search_space import DiscoveryGridSpec


def require_frozen_candidate_for_validation(
    grid_spec: DiscoveryGridSpec,
    grid_lock: DiscoveryGridLock,
    candidate_lock: DiscoveryCandidateLock,
) -> DiscoveryCandidateLock:
    """Validate and return a frozen candidate lock for validation use.

    This is the only entrypoint through which a validator may accept a
    discovery candidate.  Ad-hoc discovery output is rejected.

    Args:
        grid_spec: The parent grid spec.
        grid_lock: The parent grid lock.
        candidate_lock: The candidate lock to validate.

    Returns:
        The validated candidate lock.

    Raises:
        DiscoveryFreezeError (subclasses) on any validation failure.
    """
    # Delegate to candidate lock validation (which validates parent chain)
    validate_candidate_lock(grid_spec, grid_lock, candidate_lock)

    # If validation passed, return the lock for consumption
    return candidate_lock
