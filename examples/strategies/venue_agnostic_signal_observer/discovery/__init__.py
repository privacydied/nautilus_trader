# Copyright (C) 2026. All rights reserved.
"""Discovery freeze/precommitment subsystem for the Edge Miner pipeline.

This package defines the contracts for Stage 1 of the four-stage model:

    Edge Miner -> Validator -> Shadow Executor -> Trading Bot

It implements the two-freeze rule (grid freeze, then candidate freeze),
providing deterministic, verifiable lock files that prevent silent drift
across the discovery-to-validation boundary.

**This package does not execute trades.**
**This package does not create live trading candidates.**
**This package does not place orders.**
**This package does not connect to exchanges.**
**This package only defines and validates frozen discovery contracts.**
"""

from .exceptions import (
    CandidateHashMismatchError,
    CandidateLockValidationError,
    CaptureFingerprintError,
    DiscoveryFreezeError,
    DiscoverySafetyError,
    GridCellCountMismatchError,
    GridHashMismatchError,
    GridLockValidationError,
    GridSpecValidationError,
)
from .search_space import (
    COUNTED_AXIS_NAMES,
    DiscoveryGridSpec,
    GRID_AXIS_NAMES,
    NON_MULTIPLICATIVE_FIELDS,
    canonical_grid_json,
    canonical_grid_payload,
    counted_axis_names,
    enumerate_cost_sensitivity_cell_count,
    enumerate_primary_cell_count,
    grid_sha256,
    load_grid_spec,
    non_multiplicative_field_names,
    save_grid_spec,
    validate_grid_spec,
)
from .grid_lock import (
    DiscoveryGridLock,
    create_grid_lock,
    load_grid_lock,
    lock_file_is_semantically_identical,
    locks_are_semantically_equal,
    save_grid_lock,
    validate_grid_lock,
)
from .capture_fingerprint import (
    CaptureManifestRef,
    build_capture_manifest_ref,
    sha256_file,
)
from .candidate_lock import (
    CANDIDATE_LOCK_TYPE,
    CANDIDATE_SCHEMA_VERSION,
    DiscoveryCandidateLock,
    candidate_lock_file_is_semantically_identical,
    candidate_locks_are_semantically_equal,
    candidate_sha256,
    canonical_candidate_payload,
    canonical_selected_cell_json,
    canonicalize_selected_cells,
    create_candidate_lock,
    load_candidate_lock,
    save_candidate_lock,
    validate_candidate_lock,
    validate_selected_cells,
)
from .promotion_boundary import require_frozen_candidate_for_validation
from .safety_scan import SafetyFinding, run_safety_scan_cli, scan_discovery_package

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
    "GRID_AXIS_NAMES",
    "COUNTED_AXIS_NAMES",
    "NON_MULTIPLICATIVE_FIELDS",
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
    "validate_grid_lock",
    "save_grid_lock",
    "load_grid_lock",
    "locks_are_semantically_equal",
    "lock_file_is_semantically_identical",
    # Capture fingerprint
    "CaptureManifestRef",
    "sha256_file",
    "build_capture_manifest_ref",
    # Candidate lock
    "CANDIDATE_LOCK_TYPE",
    "CANDIDATE_SCHEMA_VERSION",
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
    "candidate_locks_are_semantically_equal",
    "candidate_lock_file_is_semantically_identical",
    # Promotion boundary
    "require_frozen_candidate_for_validation",
    # Safety scan
    "SafetyFinding",
    "scan_discovery_package",
    "run_safety_scan_cli",
]
