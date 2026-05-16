# Copyright (C) 2026. All rights reserved.
"""Exception hierarchy for the discovery freeze/precommitment subsystem.

All discovery-specific exceptions inherit from DiscoveryFreezeError,
which itself inherits from Exception.
"""


class DiscoveryFreezeError(Exception):
    """Base exception for all discovery freeze/precommitment errors."""


class GridSpecValidationError(DiscoveryFreezeError):
    """Raised when a grid spec fails validation."""


class GridLockValidationError(DiscoveryFreezeError):
    """Raised when a grid lock fails validation against its grid spec."""


class GridHashMismatchError(GridLockValidationError):
    """Raised when the computed grid hash does not match the stored lock hash."""


class GridCellCountMismatchError(GridLockValidationError):
    """Raised when the computed cell count does not match the stored lock count."""


class DiscoverySafetyError(DiscoveryFreezeError):
    """Raised when a safety scan finds violations."""


class CandidateLockValidationError(DiscoveryFreezeError):
    """Raised when a candidate lock fails validation."""


class CandidateHashMismatchError(CandidateLockValidationError):
    """Raised when the recomputed candidate hash does not match the stored hash."""


class CaptureFingerprintError(DiscoveryFreezeError):
    """Raised when a capture manifest cannot be read, parsed, or fingerprinted."""
