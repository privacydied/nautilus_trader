"""Exception hierarchy for the discovery freeze system.

All discovery-specific exceptions inherit from DiscoveryFreezeError
so callers can catch the base type or specific subtypes.
"""


class DiscoveryFreezeError(Exception):
    """Base exception for all discovery freeze/precommitment errors."""


class GridSpecValidationError(DiscoveryFreezeError):
    """Raised when a grid spec fails validation."""


class GridLockValidationError(DiscoveryFreezeError):
    """Raised when a grid lock fails validation against a grid spec."""


class GridHashMismatchError(GridLockValidationError):
    """Raised when the grid hash does not match the lock's stored hash."""


class GridCellCountMismatchError(GridLockValidationError):
    """Raised when primary or cost-sensitivity cell count does not match."""


class DiscoverySafetyError(DiscoveryFreezeError):
    """Raised by the AST safety scanner when violations are found."""


class CandidateLockValidationError(DiscoveryFreezeError):
    """Raised when a candidate lock fails validation."""


class CandidateHashMismatchError(CandidateLockValidationError):
    """Raised when the candidate hash does not match expectations."""


class CaptureFingerprintError(DiscoveryFreezeError):
    """Raised when a capture manifest cannot be fingerprinted."""
