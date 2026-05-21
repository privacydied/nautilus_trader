# Copyright (C) 2026. All rights reserved.
"""
Discovery grid specification — frozen search space for Edge Miner.

Defines the ``DiscoveryGridSpec`` dataclass, validation, canonical
serialization, deterministic hashing, and cell-count enumeration.

Canonical serialisation
-----------------------
- Sorted object keys.
- Compact separators (",", ":").
- ensure_ascii=False.
- Tuples converted to lists at the serialisation boundary only.
- Fields excluded from hash: ``created_at_utc``, ``notes``.

No floats in any hashed payload.  All bps-like values are centibps (int).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import ClassVar

# ---------------------------------------------------------------------------
# Exception re-exports for convenience
# ---------------------------------------------------------------------------
from .exceptions import GridSpecValidationError


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

GRID_SCHEMA_VERSION = "discovery-grid-v1"

# ---------------------------------------------------------------------------
# Axis names — source of truth
# ---------------------------------------------------------------------------

_GRID_AXIS_NAMES: tuple[str, ...] = (
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

GRID_AXIS_NAMES: tuple[str, ...] = _GRID_AXIS_NAMES

_PRIMARY_COUNTED_AXIS_NAMES: tuple[str, ...] = (
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

COUNTED_AXIS_NAMES: tuple[str, ...] = _PRIMARY_COUNTED_AXIS_NAMES

_PRIMARY_COUNTED_AXIS_SET: frozenset[str] = frozenset(_PRIMARY_COUNTED_AXIS_NAMES)

# Fields that are part of the grid hash but do NOT multiply into
# primary_cell_count.
#   cooldown_ms:          event de-duplication rule, not an axis
#   min_events:           group admissibility rule, not an axis
#   clustering_keys:      post-discovery grouping rule, not an axis
#   fdr_family_dimensions: statistical-family declaration, not an axis
#   cost_models_centibps: evaluation lenses over same event-return series
#
NON_MULTIPLICATIVE_FIELDS: tuple[str, ...] = (
    "cooldown_ms",
    "min_events",
    "clustering_keys",
    "fdr_family_dimensions",
    "cost_models_centibps",
)


def counted_axis_names() -> tuple[str, ...]:
    """Return the tuple of primary counted axis names."""
    return _PRIMARY_COUNTED_AXIS_NAMES


def non_multiplicative_field_names() -> tuple[str, ...]:
    """Return the tuple of non-multiplicative hashed field names."""
    return NON_MULTIPLICATIVE_FIELDS


_HASH_EXCLUDED_FIELDS: frozenset[str] = frozenset({
    "created_at_utc",
    "notes",
    "git_sha",
    "lock_paths",
    "generated_at",
    "runtime_args",
})

# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveryGridSpec:
    """
    Frozen specification of a discovery grid.

    All fields are immutable.  Validation is a separate step.
    All fields have defaults so that test code can construct partial specs.
    """

    grid_id: str = ""
    schema_version: str = GRID_SCHEMA_VERSION
    signal_family: str = ""

    source_venues: tuple[str, ...] = ()
    source_symbols: tuple[str, ...] = ()
    target_venues: tuple[str, ...] = ()
    target_symbols: tuple[str, ...] = ()
    feature_types: tuple[str, ...] = ()
    lookbacks_ms: tuple[int, ...] = ()
    thresholds_centibps: tuple[int, ...] = ()
    horizons_ms: tuple[int, ...] = ()
    entry_delays_ms: tuple[int, ...] = ()
    cooldown_ms: int = 0
    regime_filters: tuple[str, ...] = ()
    cost_models_centibps: tuple[int, ...] = ()
    min_events: int = 0
    clustering_keys: tuple[str, ...] = ()
    fdr_family_dimensions: tuple[str, ...] = ()
    created_at_utc: str = ""
    notes: str = ""

    # ------------------------------------------------------------------
    # Class-level lists so introspection does not require an instance
    # ------------------------------------------------------------------
    AXIS_NAMES: ClassVar[tuple[str, ...]] = _GRID_AXIS_NAMES
    COUNTED_AXES: ClassVar[tuple[str, ...]] = _PRIMARY_COUNTED_AXIS_NAMES

    _STRING_AXES: ClassVar[tuple[str, ...]] = (
        "source_venues",
        "source_symbols",
        "target_venues",
        "target_symbols",
        "feature_types",
        "regime_filters",
    )

    _INT_AXES: ClassVar[tuple[str, ...]] = (
        "lookbacks_ms",
        "thresholds_centibps",
        "horizons_ms",
        "entry_delays_ms",
        "cost_models_centibps",
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_non_empty(
    spec: DiscoveryGridSpec,
) -> None:
    """Raise GridSpecValidationError on empty or invalid axes."""
    # --- string-tuple axes: non-empty, non-whitespace-only strings ---
    for axis_name in DiscoveryGridSpec._STRING_AXES:
        values = getattr(spec, axis_name)
        if not isinstance(values, tuple) or len(values) == 0:
            raise GridSpecValidationError(
                f"{axis_name} must be a non-empty tuple"
            )
        for val in values:
            if not isinstance(val, str) or val.strip() == "":
                raise GridSpecValidationError(
                    f"Every element in {axis_name} must be a non-empty string; "
                    f"got {val!r} (type={type(val).__name__})"
                )

    # --- int-tuple axes: non-empty, positive/non-negative, strict int ---
    for axis_name in DiscoveryGridSpec._INT_AXES:
        values = getattr(spec, axis_name)
        if not isinstance(values, tuple) or len(values) == 0:
            raise GridSpecValidationError(
                f"{axis_name} must be a non-empty tuple"
            )

        for val in values:
            # bool is a subclass of int — reject it explicitly
            if isinstance(val, bool):
                raise GridSpecValidationError(
                    f"{axis_name} element {val!r} is bool, not int"
                )
            if not isinstance(val, int):
                raise GridSpecValidationError(
                    f"{axis_name} element {val!r} must be an int; "
                    f"got {type(val).__name__}"
                )

        # positivity requirements vary by axis
        if axis_name == "entry_delays_ms" or axis_name == "cost_models_centibps":
            _require_non_negative(values, axis_name)
        else:
            _require_positive(values, axis_name)

    # --- misc scalar int fields ---
    _validate_scalar_positive_int(spec.cooldown_ms, "cooldown_ms")
    _validate_scalar_positive_int(spec.min_events, "min_events")

    # --- clustering_keys: non-empty, valid axis names ---
    if not isinstance(spec.clustering_keys, tuple) or len(spec.clustering_keys) == 0:
        raise GridSpecValidationError(
            "clustering_keys must be a non-empty tuple"
        )
    _validate_axis_names(spec.clustering_keys, "clustering_keys", _GRID_AXIS_NAMES)

    # --- fdr_family_dimensions: non-empty, valid counted axis names ---
    if (
        not isinstance(spec.fdr_family_dimensions, tuple)
        or len(spec.fdr_family_dimensions) == 0
    ):
        raise GridSpecValidationError(
            "fdr_family_dimensions must be a non-empty tuple"
        )
    _validate_axis_names(
        spec.fdr_family_dimensions,
        "fdr_family_dimensions",
        _PRIMARY_COUNTED_AXIS_NAMES,
    )

    # --- duplicate checks ---
    dup = _find_duplicates(spec.source_venues)
    if dup:
        raise GridSpecValidationError(f"source_venues has duplicate(s): {dup}")
    dup = _find_duplicates(spec.source_symbols)
    if dup:
        raise GridSpecValidationError(f"source_symbols has duplicate(s): {dup}")
    dup = _find_duplicates(spec.target_venues)
    if dup:
        raise GridSpecValidationError(f"target_venues has duplicate(s): {dup}")
    dup = _find_duplicates(spec.target_symbols)
    if dup:
        raise GridSpecValidationError(f"target_symbols has duplicate(s): {dup}")
    dup = _find_duplicates(spec.feature_types)
    if dup:
        raise GridSpecValidationError(f"feature_types has duplicate(s): {dup}")
    dup = _find_duplicates(spec.lookbacks_ms)
    if dup:
        raise GridSpecValidationError(f"lookbacks_ms has duplicate(s): {dup}")
    dup = _find_duplicates(spec.thresholds_centibps)
    if dup:
        raise GridSpecValidationError(f"thresholds_centibps has duplicate(s): {dup}")
    dup = _find_duplicates(spec.horizons_ms)
    if dup:
        raise GridSpecValidationError(f"horizons_ms has duplicate(s): {dup}")
    dup = _find_duplicates(spec.entry_delays_ms)
    if dup:
        raise GridSpecValidationError(f"entry_delays_ms has duplicate(s): {dup}")
    dup = _find_duplicates(spec.regime_filters)
    if dup:
        raise GridSpecValidationError(f"regime_filters has duplicate(s): {dup}")
    dup = _find_duplicates(spec.cost_models_centibps)
    if dup:
        raise GridSpecValidationError(
            f"cost_models_centibps has duplicate(s): {dup}"
        )
    dup = _find_duplicates(spec.clustering_keys)
    if dup:
        raise GridSpecValidationError(f"clustering_keys has duplicate(s): {dup}")
    dup = _find_duplicates(spec.fdr_family_dimensions)
    if dup:
        raise GridSpecValidationError(
            f"fdr_family_dimensions has duplicate(s): {dup}"
        )

    # --- schema version ---
    if spec.schema_version != GRID_SCHEMA_VERSION:
        raise GridSpecValidationError(
            f"schema_version must be '{GRID_SCHEMA_VERSION}', "
            f"got '{spec.schema_version}'"
        )


def _validate_axis_names(
    keys: tuple[str, ...],
    field_name: str,
    valid_names: tuple[str, ...],
) -> None:
    """Check every entry in *keys* is a member of *valid_names*."""
    valid_set = frozenset(valid_names)
    for key in keys:
        if key not in valid_set:
            raise GridSpecValidationError(
                f"{field_name} contains invalid axis '{key}'. "
                f"Valid axes: {valid_names}"
            )


def _validate_scalar_positive_int(val: Any, name: str) -> None:
    if isinstance(val, bool):
        raise GridSpecValidationError(
            f"{name} must be a positive int, got bool ({val!r})"
        )
    if not isinstance(val, int):
        raise GridSpecValidationError(
            f"{name} must be a positive int, got {type(val).__name__}"
        )
    if val <= 0:
        raise GridSpecValidationError(
            f"{name} must be a positive int, got {val}"
        )


def _require_positive(values: tuple[int, ...], name: str) -> None:
    for v in values:
        if v <= 0:
            raise GridSpecValidationError(
                f"Every element in {name} must be positive; got {v}"
            )


def _require_non_negative(values: tuple[int, ...], name: str) -> None:
    for v in values:
        if v < 0:
            raise GridSpecValidationError(
                f"Every element in {name} must be non-negative; got {v}"
            )


def _find_duplicates(values: tuple) -> list:
    """Return list of duplicate values in the order first seen."""
    seen: set = set()
    dup = []
    for v in values:
        if v in seen:
            dup.append(v)
        else:
            seen.add(v)
    return dup


def validate_grid_spec(spec: DiscoveryGridSpec) -> None:
    """Validate a grid spec in-place, raising GridSpecValidationError."""
    _validate_non_empty(spec)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _hash_payload_dict(spec: DiscoveryGridSpec) -> dict[str, Any]:
    """Build the dict used for canonical hashing (excludes transient fields)."""
    payload: dict[str, Any] = {}
    for key in _hash_ordered_keys():
        if key in _HASH_EXCLUDED_FIELDS:
            continue
        val = getattr(spec, key)
        # Convert tuples to lists at serialisation boundary
        if isinstance(val, tuple):
            val = list(val)
        payload[key] = val
    return payload


def _hash_ordered_keys() -> tuple[str, ...]:
    """Return the sorted hash-field key order for canonical output."""
    # All hash fields, alphabetically sorted
    return tuple(sorted([
        "clustering_keys",
        "cooldown_ms",
        "cost_models_centibps",
        "entry_delays_ms",
        "fdr_family_dimensions",
        "feature_types",
        "grid_id",
        "horizons_ms",
        "lookbacks_ms",
        "min_events",
        "regime_filters",
        "schema_version",
        "signal_family",
        "source_symbols",
        "source_venues",
        "target_symbols",
        "target_venues",
        "thresholds_centibps",
    ]))


def canonical_grid_payload(spec: DiscoveryGridSpec) -> dict[str, Any]:
    """
    Return the canonical dict for the grid spec (hash payload only).

    Excludes ``created_at_utc``, ``notes``, git sha, lock paths, and
    runtime/generated-at timestamps.
    """
    return _hash_payload_dict(spec)


def canonical_grid_json(spec: DiscoveryGridSpec) -> str:
    """
    Return the canonical JSON string for hashing.

    - Sorted object keys.
    - Compact separators (",", ":").
    - ``ensure_ascii=False``.
    - Tuples converted to lists.
    """
    payload = canonical_grid_payload(spec)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def grid_sha256(spec: DiscoveryGridSpec) -> str:
    """Return the SHA-256 hex digest over canonical JSON bytes."""
    return hashlib.sha256(canonical_grid_json(spec).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Cell-count enumeration
# ---------------------------------------------------------------------------


def enumerate_primary_cell_count(spec: DiscoveryGridSpec) -> int:
    """
    Compute the number of enumerable primary cells.

    This is the product of the lengths of all primary counted axes:

        source_venues × source_symbols × target_venues × target_symbols ×
        feature_types × lookbacks_ms × thresholds_centibps × horizons_ms ×
        entry_delays_ms × regime_filters

    This is the FDR denominator for discovery.

    ``cooldown_ms``, ``min_events``, ``clustering_keys``,
    ``fdr_family_dimensions``, and ``cost_models_centibps`` are **not**
    part of this count.
    """
    count = 1
    for axis in _PRIMARY_COUNTED_AXIS_NAMES:
        count *= len(getattr(spec, axis))
    return count


def enumerate_cost_sensitivity_cell_count(spec: DiscoveryGridSpec) -> int:
    """
    Compute the cost-sensitivity cell count.

    Definition::

        primary_cell_count * len(cost_models_centibps)

    This is diagnostic only.  It is **not** the FDR denominator.
    """
    return enumerate_primary_cell_count(spec) * len(spec.cost_models_centibps)


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _spec_from_dict(data: dict[str, Any]) -> DiscoveryGridSpec:
    """
    Build a DiscoveryGridSpec from a deserialised JSON dict.

    Converts lists to tuples on read.
    """
    kwargs: dict[str, Any] = {}
    for key in _GRID_AXIS_NAMES:
        val = data.get(key)
        if isinstance(val, list):
            kwargs[key] = tuple(val)
        elif val is not None:
            kwargs[key] = val

    # Scalar fields
    for scalar_key in ("grid_id", "schema_version", "signal_family",
                       "cooldown_ms", "min_events", "created_at_utc", "notes"):
        if scalar_key in data:
            kwargs[scalar_key] = data[scalar_key]

    for tuple_key in ("clustering_keys", "fdr_family_dimensions"):
        if tuple_key in data:
            val = data[tuple_key]
            if isinstance(val, list):
                kwargs[tuple_key] = tuple(val)
            else:
                kwargs[tuple_key] = val

    return DiscoveryGridSpec(**kwargs)


def load_grid_spec(path: str | Path) -> DiscoveryGridSpec:
    """Load and return a ``DiscoveryGridSpec`` from a JSON file."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return _spec_from_dict(data)


def save_grid_spec(spec: DiscoveryGridSpec, path: str | Path) -> None:
    """
    Save a grid spec as canonical JSON.

    The saved file includes all fields (including ``created_at_utc``
    and ``notes``), not just the hash payload.
    """
    path = Path(path)
    raw = asdict(spec)
    # Convert tuples to lists for JSON
    raw = {k: (list(v) if isinstance(v, tuple) else v) for k, v in raw.items()}
    path.write_text(
        json.dumps(raw, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
