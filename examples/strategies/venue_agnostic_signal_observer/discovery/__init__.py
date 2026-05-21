# Copyright (C) 2026. All rights reserved.
"""
Discovery freeze/precommitment subsystem for the Edge Miner pipeline.

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

from .candidate_lock import CANDIDATE_LOCK_TYPE
from .candidate_lock import CANDIDATE_SCHEMA_VERSION
from .candidate_lock import DiscoveryCandidateLock
from .candidate_lock import candidate_lock_file_is_semantically_identical
from .candidate_lock import candidate_locks_are_semantically_equal
from .candidate_lock import candidate_sha256
from .candidate_lock import canonical_candidate_payload
from .candidate_lock import canonical_selected_cell_json
from .candidate_lock import canonicalize_selected_cells
from .candidate_lock import create_candidate_lock
from .candidate_lock import load_candidate_lock
from .candidate_lock import save_candidate_lock
from .candidate_lock import validate_candidate_lock
from .candidate_lock import validate_selected_cells
from .capture_fingerprint import CaptureManifestRef
from .capture_fingerprint import build_capture_manifest_ref
from .capture_fingerprint import sha256_file
from .exceptions import CandidateHashMismatchError
from .exceptions import CandidateLockValidationError
from .exceptions import CaptureFingerprintError
from .exceptions import DiscoveryFreezeError
from .exceptions import DiscoverySafetyError
from .exceptions import GridCellCountMismatchError
from .exceptions import GridHashMismatchError
from .exceptions import GridLockValidationError
from .exceptions import GridSpecValidationError
from .grid_lock import DiscoveryGridLock
from .grid_lock import create_grid_lock
from .grid_lock import load_grid_lock
from .grid_lock import lock_file_is_semantically_identical
from .grid_lock import locks_are_semantically_equal
from .grid_lock import save_grid_lock
from .grid_lock import validate_grid_lock
from .promotion_boundary import require_frozen_candidate_for_validation
from .safety_scan import SafetyFinding
from .safety_scan import run_safety_scan_cli
from .safety_scan import scan_discovery_package
from .search_space import COUNTED_AXIS_NAMES
from .search_space import GRID_AXIS_NAMES
from .search_space import NON_MULTIPLICATIVE_FIELDS
from .search_space import DiscoveryGridSpec
from .search_space import canonical_grid_json
from .search_space import canonical_grid_payload
from .search_space import counted_axis_names
from .search_space import enumerate_cost_sensitivity_cell_count
from .search_space import enumerate_primary_cell_count
from .search_space import grid_sha256
from .search_space import load_grid_spec
from .search_space import non_multiplicative_field_names
from .search_space import save_grid_spec
from .search_space import validate_grid_spec


__all__ = [
    # Candidate lock
    "CANDIDATE_LOCK_TYPE",
    "CANDIDATE_SCHEMA_VERSION",
    "COUNTED_AXIS_NAMES",
    "GRID_AXIS_NAMES",
    "NON_MULTIPLICATIVE_FIELDS",
    "CandidateHashMismatchError",
    "CandidateLockValidationError",
    "CaptureFingerprintError",
    # Capture fingerprint
    "CaptureManifestRef",
    "DiscoveryCandidateLock",
    # Exceptions
    "DiscoveryFreezeError",
    # Grid lock
    "DiscoveryGridLock",
    # Search space
    "DiscoveryGridSpec",
    "DiscoverySafetyError",
    "GridCellCountMismatchError",
    "GridHashMismatchError",
    "GridLockValidationError",
    "GridSpecValidationError",
    # Safety scan
    "SafetyFinding",
    "build_capture_manifest_ref",
    "candidate_lock_file_is_semantically_identical",
    "candidate_locks_are_semantically_equal",
    "candidate_sha256",
    "canonical_candidate_payload",
    "canonical_grid_json",
    "canonical_grid_payload",
    "canonical_selected_cell_json",
    "canonicalize_selected_cells",
    "counted_axis_names",
    "create_candidate_lock",
    "create_grid_lock",
    "enumerate_cost_sensitivity_cell_count",
    "enumerate_primary_cell_count",
    "grid_sha256",
    "load_candidate_lock",
    "load_grid_lock",
    "load_grid_spec",
    "lock_file_is_semantically_identical",
    "locks_are_semantically_equal",
    "non_multiplicative_field_names",
    # Promotion boundary
    "require_frozen_candidate_for_validation",
    "run_safety_scan_cli",
    "save_candidate_lock",
    "save_grid_lock",
    "save_grid_spec",
    "scan_discovery_package",
    "sha256_file",
    "validate_candidate_lock",
    "validate_grid_lock",
    "validate_grid_spec",
    "validate_selected_cells",
]
