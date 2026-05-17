"""Discovery grid specification and canonical hashing.

Defines the frozen search space for the Edge Miner.
A DiscoveryGridSpec declares all axes that a discovery run may scan.
Changing the spec creates a new grid hash, a new run family, and a new FDR denominator.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .exceptions import GridSpecValidationError


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRID_SCHEMA_VERSION = "discovery-grid-v1"
CANDIDATE_SCHEMA_VERSION = "discovery-candidate-v1"

# The 10 primary counted axes whose cardinalities multiply into primary_cell_count.
_PRIMARY_COUNTED_AXES: tuple[str, ...] = (
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

# Fields that are hashed (part of the grid identity) but do NOT multiply
# into primary_cell_count.
_NON_MULTIPLICATIVE_FIELDS: tuple[str, ...] = (
    "cooldown_ms",       # event de-duplication rule, not an axis
    "min_events",        # group admissibility rule, not an axis
    "clustering_keys",   # post-discovery grouping rule, not an axis
    "fdr_family_dimensions",  # statistical-family declaration, not an axis
    "cost_models_centibps",   # evaluation lenses, not source→target→feature→horizons relationships
)

# Fields that are excluded from the grid hash entirely.
_HASH_EXCLUDED_FIELDS: tuple[str, ...] = (
    "created_at_utc",
    "notes",
)

# All valid grid axis names (for clustering_keys and fdr_family_dimensions validation).
_VALID_AXIS_NAMES: tuple[str, ...] = (
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
    "cost_models_centibps",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def counted_axis_names() -> tuple[str, ...]:
    """Return the 10 primary counted axis names.

    These axes multiply into primary_cell_count and define the FDR denominator
    for discovery.
    """
    return _PRIMARY_COUNTED_AXES


def non_multiplicative_field_names() -> tuple[str, ...]:
    """Return field names that are hashed but do not multiply into primary_cell_count.

    These fields change discovery semantics (part of the grid hash) but are not
    source → target → feature → horizon relationships.
    """
    return _NON_MULTIPLICATIVE_FIELDS


def valid_axis_names() -> tuple[str, ...]:
    """Return all valid grid axis names."""
    return _VALID_AXIS_NAMES


def _is_bool(value: Any) -> bool:
    """Strict bool check: bool is a subtype of int in Python.

    Reject bool-typed values for numeric fields.
    """
    return isinstance(value, bool)


def _strict_is_int(value: Any) -> bool:
    """Check that value is an int but NOT a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# Grid Spec
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DiscoveryGridSpec:
    """Frozen specification of a discovery search grid.

    All bps-like numeric values are stored as integer centibps to avoid
    cross-version float serialization drift.
    """

    grid_id: str
    schema_version: str
    signal_family: str
    source_venues: tuple[str, ...]
    source_symbols: tuple[str, ...]
    target_venues: tuple[str, ...]
    target_symbols: tuple[str, ...]
    feature_types: tuple[str, ...]
    lookbacks_ms: tuple[int, ...]
    thresholds_centibps: tuple[int, ...]
    horizons_ms: tuple[int, ...]
    entry_delays_ms: tuple[int, ...]
    cooldown_ms: int
    regime_filters: tuple[str, ...]
    cost_models_centibps: tuple[int, ...]
    min_events: int
    clustering_keys: tuple[str, ...]
    fdr_family_dimensions: tuple[str, ...]
    created_at_utc: str
    notes: str


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _check_non_empty_string_tuple(values: tuple[str, ...], field_name: str) -> None:
    """Validate a tuple of strings is non-empty, contains no whitespace-only strings, and no duplicates."""
    if not values:
        raise GridSpecValidationError(f"{field_name} must be non-empty")
    seen: set[str] = set()
    for i, v in enumerate(values):
        if not isinstance(v, str):
            raise GridSpecValidationError(f"{field_name}[{i}]: expected str, got {type(v).__name__}")
        if not v.strip():
            raise GridSpecValidationError(f"{field_name}[{i}]: string is empty or whitespace-only")
        if v in seen:
            raise GridSpecValidationError(f"{field_name}: duplicate value {v!r}")
        seen.add(v)


def _check_positive_int_tuple(values: tuple[int, ...], field_name: str) -> None:
    """Validate a tuple of positive ints (no bools)."""
    if not values:
        raise GridSpecValidationError(f"{field_name} must be non-empty")
    seen: set[int] = set()
    for i, v in enumerate(values):
        if _is_bool(v):
            raise GridSpecValidationError(f"{field_name}[{i}]: bool is not a valid int value")
        if not _strict_is_int(v):
            raise GridSpecValidationError(f"{field_name}[{i}]: expected int, got {type(v).__name__}")
        if v <= 0:
            raise GridSpecValidationError(f"{field_name}[{i}]: must be positive, got {v}")
        if v in seen:
            raise GridSpecValidationError(f"{field_name}: duplicate value {v}")
        seen.add(v)


def _check_non_negative_int_tuple(values: tuple[int, ...], field_name: str) -> None:
    """Validate a tuple of non-negative ints (no bools)."""
    if not values:
        raise GridSpecValidationError(f"{field_name} must be non-empty")
    seen: set[int] = set()
    for i, v in enumerate(values):
        if _is_bool(v):
            raise GridSpecValidationError(f"{field_name}[{i}]: bool is not a valid int value")
        if not _strict_is_int(v):
            raise GridSpecValidationError(f"{field_name}[{i}]: expected int, got {type(v).__name__}")
        if v < 0:
            raise GridSpecValidationError(f"{field_name}[{i}]: must be non-negative, got {v}")
        if v in seen:
            raise GridSpecValidationError(f"{field_name}: duplicate value {v}")
        seen.add(v)


def _check_positive_int(value: int, field_name: str) -> None:
    """Validate a single positive int."""
    if _is_bool(value):
        raise GridSpecValidationError(f"{field_name}: bool is not a valid int value")
    if not _strict_is_int(value):
        raise GridSpecValidationError(f"{field_name}: expected int, got {type(value).__name__}")
    if value <= 0:
        raise GridSpecValidationError(f"{field_name}: must be positive, got {value}")


def validate_grid_spec(spec: DiscoveryGridSpec) -> None:
    """Validate a DiscoveryGridSpec against all rules.

    Raises GridSpecValidationError on first violation.
    """
    # Schema version
    if not isinstance(spec.schema_version, str) or not spec.schema_version.strip():
        raise GridSpecValidationError("schema_version must be a non-empty string")
    if spec.schema_version != GRID_SCHEMA_VERSION:
        raise GridSpecValidationError(
            f"schema_version must be {GRID_SCHEMA_VERSION!r}, got {spec.schema_version!r}"
        )

    # Non-empty string tuples
    _check_non_empty_string_tuple(spec.source_venues, "source_venues")
    _check_non_empty_string_tuple(spec.source_symbols, "source_symbols")
    _check_non_empty_string_tuple(spec.target_venues, "target_venues")
    _check_non_empty_string_tuple(spec.target_symbols, "target_symbols")
    _check_non_empty_string_tuple(spec.feature_types, "feature_types")
    _check_non_empty_string_tuple(spec.regime_filters, "regime_filters")

    # Positive int tuples
    _check_positive_int_tuple(spec.lookbacks_ms, "lookbacks_ms")
    _check_positive_int_tuple(spec.horizons_ms, "horizons_ms")
    _check_positive_int_tuple(spec.thresholds_centibps, "thresholds_centibps")

    # Non-negative int tuples
    _check_non_negative_int_tuple(spec.entry_delays_ms, "entry_delays_ms")
    _check_non_negative_int_tuple(spec.cost_models_centibps, "cost_models_centibps")

    # Single positive ints
    _check_positive_int(spec.cooldown_ms, "cooldown_ms")
    _check_positive_int(spec.min_events, "min_events")

    # clustering_keys: non-empty, valid axis names, no dupes
    if not spec.clustering_keys:
        raise GridSpecValidationError("clustering_keys must be non-empty")
    seen_cluster: set[str] = set()
    for i, key in enumerate(spec.clustering_keys):
        if not isinstance(key, str):
            raise GridSpecValidationError(f"clustering_keys[{i}]: expected str, got {type(key).__name__}")
        if key not in _VALID_AXIS_NAMES:
            raise GridSpecValidationError(
                f"clustering_keys[{i}]: {key!r} is not a valid grid axis name"
            )
        if key in seen_cluster:
            raise GridSpecValidationError(f"clustering_keys: duplicate value {key!r}")
        seen_cluster.add(key)

    # fdr_family_dimensions: non-empty, valid primary counted axis names, no dupes
    if not spec.fdr_family_dimensions:
        raise GridSpecValidationError("fdr_family_dimensions must be non-empty")
    seen_fdr: set[str] = set()
    for i, dim in enumerate(spec.fdr_family_dimensions):
        if not isinstance(dim, str):
            raise GridSpecValidationError(f"fdr_family_dimensions[{i}]: expected str, got {type(dim).__name__}")
        if dim not in _PRIMARY_COUNTED_AXES:
            raise GridSpecValidationError(
                f"fdr_family_dimensions[{i}]: {dim!r} is not a valid primary counted axis name"
            )
        if dim in seen_fdr:
            raise GridSpecValidationError(f"fdr_family_dimensions: duplicate value {dim!r}")
        seen_fdr.add(dim)

    # grid_id must be non-empty
    if not isinstance(spec.grid_id, str) or not spec.grid_id.strip():
        raise GridSpecValidationError("grid_id must be a non-empty string")

    # signal_family must be non-empty
    if not isinstance(spec.signal_family, str) or not spec.signal_family.strip():
        raise GridSpecValidationError("signal_family must be a non-empty string")


# ---------------------------------------------------------------------------
# Canonical serialization
# ---------------------------------------------------------------------------

def _hashable_payload(spec: DiscoveryGridSpec) -> dict[str, Any]:
    """Build the canonical ordered dict for hashing.

    Only fields that affect the grid identity are included.
    created_at_utc, notes, and any transient runtime fields are excluded.
    Tuples are converted to lists only at this boundary.
    """
    return {
        "clustering_keys": list(spec.clustering_keys),
        "cooldown_ms": spec.cooldown_ms,
        "cost_models_centibps": list(spec.cost_models_centibps),
        "entry_delays_ms": list(spec.entry_delays_ms),
        "fdr_family_dimensions": list(spec.fdr_family_dimensions),
        "feature_types": list(spec.feature_types),
        "grid_id": spec.grid_id,
        "horizons_ms": list(spec.horizons_ms),
        "lookbacks_ms": list(spec.lookbacks_ms),
        "min_events": spec.min_events,
        "regime_filters": list(spec.regime_filters),
        "schema_version": spec.schema_version,
        "signal_family": spec.signal_family,
        "source_symbols": list(spec.source_symbols),
        "source_venues": list(spec.source_venues),
        "target_symbols": list(spec.target_symbols),
        "target_venues": list(spec.target_venues),
        "thresholds_centibps": list(spec.thresholds_centibps),
    }


def canonical_grid_payload(spec: DiscoveryGridSpec) -> dict[str, Any]:
    """Return the canonical ordered dict for the grid spec.

    Keys are sorted alphabetically. Tuples are converted to lists.
    Transient fields (created_at_utc, notes) are excluded.
    """
    return _hashable_payload(spec)


def canonical_grid_json(spec: DiscoveryGridSpec) -> str:
    """Return canonical JSON string for the grid spec.

    Uses sorted keys, compact separators, and ensure_ascii=False.
    This is deterministic and stable across Python versions.
    """
    payload = _hashable_payload(spec)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def grid_sha256(spec: DiscoveryGridSpec) -> str:
    """Compute SHA-256 hex digest over the canonical JSON of the grid spec."""
    canonical = canonical_grid_json(spec)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Cell counts
# ---------------------------------------------------------------------------

def enumerate_primary_cell_count(spec: DiscoveryGridSpec) -> int:
    """Compute the number of primary cells in the discovery grid.

    This is the product of the cardinalities of all 10 primary counted axes.
    It is the FDR denominator for discovery.

    Non-multiplicative fields (cooldown_ms, min_events, clustering_keys,
    fdr_family_dimensions, cost_models_centibps) are NOT included.
    """
    count = 1
    for axis in _PRIMARY_COUNTED_AXES:
        values = getattr(spec, axis)
        count *= len(values)
    return count


def enumerate_cost_sensitivity_cell_count(spec: DiscoveryGridSpec) -> int:
    """Compute the cost-sensitivity cell count.

    This is primary_cell_count * len(cost_models_centibps).
    Diagnostic only. NOT the FDR denominator.
    """
    return enumerate_primary_cell_count(spec) * len(spec.cost_models_centibps)


# ---------------------------------------------------------------------------
# JSON load / save
# ---------------------------------------------------------------------------

def _dict_to_spec(data: dict[str, Any]) -> DiscoveryGridSpec:
    """Convert a parsed JSON dict to a DiscoveryGridSpec."""
    return DiscoveryGridSpec(
        grid_id=str(data["grid_id"]),
        schema_version=str(data["schema_version"]),
        signal_family=str(data["signal_family"]),
        source_venues=tuple(data["source_venues"]),
        source_symbols=tuple(data["source_symbols"]),
        target_venues=tuple(data["target_venues"]),
        target_symbols=tuple(data["target_symbols"]),
        feature_types=tuple(data["feature_types"]),
        lookbacks_ms=tuple(int(v) for v in data["lookbacks_ms"]),
        thresholds_centibps=tuple(int(v) for v in data["thresholds_centibps"]),
        horizons_ms=tuple(int(v) for v in data["horizons_ms"]),
        entry_delays_ms=tuple(int(v) for v in data["entry_delays_ms"]),
        cooldown_ms=int(data["cooldown_ms"]),
        regime_filters=tuple(data["regime_filters"]),
        cost_models_centibps=tuple(int(v) for v in data["cost_models_centibps"]),
        min_events=int(data["min_events"]),
        clustering_keys=tuple(data["clustering_keys"]),
        fdr_family_dimensions=tuple(data["fdr_family_dimensions"]),
        created_at_utc=str(data.get("created_at_utc", "")),
        notes=str(data.get("notes", "")),
    )


def _spec_to_dict(spec: DiscoveryGridSpec) -> dict[str, Any]:
    """Convert a DiscoveryGridSpec to a JSON-compatible dict."""
    return {
        "grid_id": spec.grid_id,
        "schema_version": spec.schema_version,
        "signal_family": spec.signal_family,
        "source_venues": list(spec.source_venues),
        "source_symbols": list(spec.source_symbols),
        "target_venues": list(spec.target_venues),
        "target_symbols": list(spec.target_symbols),
        "feature_types": list(spec.feature_types),
        "lookbacks_ms": list(spec.lookbacks_ms),
        "thresholds_centibps": list(spec.thresholds_centibps),
        "horizons_ms": list(spec.horizons_ms),
        "entry_delays_ms": list(spec.entry_delays_ms),
        "cooldown_ms": spec.cooldown_ms,
        "regime_filters": list(spec.regime_filters),
        "cost_models_centibps": list(spec.cost_models_centibps),
        "min_events": spec.min_events,
        "clustering_keys": list(spec.clustering_keys),
        "fdr_family_dimensions": list(spec.fdr_family_dimensions),
        "created_at_utc": spec.created_at_utc,
        "notes": spec.notes,
    }


def load_grid_spec(path: str | Path) -> DiscoveryGridSpec:
    """Load a DiscoveryGridSpec from a JSON file."""
    path = Path(path)
    with open(path, "r") as f:
        data = json.load(f)
    return _dict_to_spec(data)


def save_grid_spec(spec: DiscoveryGridSpec, path: str | Path) -> None:
    """Save a DiscoveryGridSpec to a JSON file."""
    path = Path(path)
    data = _spec_to_dict(spec)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
