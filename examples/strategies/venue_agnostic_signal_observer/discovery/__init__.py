"""Discovery package for venue-agnostic signal observer.

This package implements Phase 1 of the four-stage research-to-execution pipeline:

    1. Edge Miner (freeze/precommitment foundation)
    2. Validator (holdout, null, FDR, cost sensitivity)
    3. Shadow Executor (queue/fill/spread simulation)
    4. Trading Bot (gated execution)

This package defines and validates frozen discovery contracts only.
It does not execute trades, connect to exchanges, or place orders.
It does not import nautilus_trader.
"""

from .exceptions import (
    DiscoveryFreezeError,
    GridSpecValidationError,
    GridLockValidationError,
    GridHashMismatchError,
    GridCellCountMismatchError,
    DiscoverySafetyError,
    CandidateLockValidationError,
    CandidateHashMismatchError,
    CaptureFingerprintError,
)

from .search_space import (
    DiscoveryGridSpec,
    validate_grid_spec,
    canonical_grid_payload,
    canonical_grid_json,
    grid_sha256,
    enumerate_primary_cell_count,
    enumerate_cost_sensitivity_cell_count,
    counted_axis_names,
    non_multiplicative_field_names,
    load_grid_spec,
    save_grid_spec,
)

from .grid_lock import (
    DiscoveryGridLock,
    create_grid_lock,
    validate_grid_spec_against_lock,
    load_grid_lock,
    save_grid_lock,
    locks_are_semantically_identical,
)

from .capture_fingerprint import (
    CaptureManifestRef,
    sha256_file,
    build_capture_manifest_ref,
)

from .candidate_lock import (
    DiscoveryCandidateLock,
    canonical_selected_cell_json,
    canonicalize_selected_cells,
    validate_selected_cells,
    canonical_candidate_payload,
    candidate_sha256,
    create_candidate_lock,
    validate_candidate_lock,
    load_candidate_lock,
    save_candidate_lock,
)

from .promotion_boundary import (
    require_frozen_candidate_for_validation,
)

from .safety_scan import (
    SafetyFinding,
    scan_discovery_package,
    run_safety_scan_cli,
)

__all__ = [
    # Exceptions
    "DiscoveryFreezeError",
    "GridSpecValidationError",
    "GridLockValidationError",
    "GridHashMismatchError",
    "GridCellCountMismatchError",
    "DiscoverySafetyError",
    "CandidateLockValidationError",
    "CandidateHashMismatchError",
    "CaptureFingerprintError",

    # Search space
    "DiscoveryGridSpec",
    "validate_grid_spec",
    "canonical_grid_payload",
    "canonical_grid_json",
    "grid_sha256",
    "enumerate_primary_cell_count",
    "enumerate_cost_sensitivity_cell_count",
    "counted_axis_names",
    "non_multiplicative_field_names",
    "load_grid_spec",
    "save_grid_spec",

    # Grid lock
    "DiscoveryGridLock",
    "create_grid_lock",
    "validate_grid_spec_against_lock",
    "load_grid_lock",
    "save_grid_lock",
    "locks_are_semantically_identical",

    # Capture fingerprint
    "CaptureManifestRef",
    "sha256_file",
    "build_capture_manifest_ref",

    # Candidate lock
    "DiscoveryCandidateLock",
    "canonical_selected_cell_json",
    "canonicalize_selected_cells",
    "validate_selected_cells",
    "canonical_candidate_payload",
    "candidate_sha256",
    "create_candidate_lock",
    "validate_candidate_lock",
    "load_candidate_lock",
    "save_candidate_lock",

    # Promotion boundary
    "require_frozen_candidate_for_validation",

    # Safety scan
    "SafetyFinding",
    "scan_discovery_package",
    "run_safety_scan_cli",
]
