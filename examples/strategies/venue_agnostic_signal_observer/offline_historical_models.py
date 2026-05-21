"""
Frozen dataclasses for Phase 1 offline historical data lane.

Observer-only, public-data-only, local-file-first. No execution, no order
submission, no private keys, no live adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict
from typing import List
from typing import Sequence


OFFLINE_DATA_SCHEMA_VERSION = "offline_historical_v1"

# ---------------------------------------------------------------------------
# Enums / literals
# ---------------------------------------------------------------------------

# Resolution tagging — every parsed stream carries exactly one of these.
RESOLUTION_TRADE = "trade"
RESOLUTION_AGG_TRADE = "agg_trade"
RESOLUTION_BAR = "bar"

VALID_RESOLUTIONS: frozenset[str] = frozenset(
    {RESOLUTION_TRADE, RESOLUTION_AGG_TRADE, RESOLUTION_BAR}
)

# Timestamp units supported by the normalizer.
TIMESTAMP_UNIT_S = "s"
TIMESTAMP_UNIT_MS = "ms"
TIMESTAMP_UNIT_US = "us"
TIMESTAMP_UNIT_NS = "ns"

VALID_TIMESTAMP_UNITS: frozenset[str] = frozenset(
    {TIMESTAMP_UNIT_S, TIMESTAMP_UNIT_MS, TIMESTAMP_UNIT_US, TIMESTAMP_UNIT_NS}
)

# Stress-window selection mode — Phase 2 enforcement seam.
WINDOW_MODE_CAUSAL = "causal"
WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC = "retrospective_diagnostic_only"

# Family 2 signal variants — explicit allowlist; never accept soft values.
ALLOWED_FAMILY2_SIGNAL_VARIANTS: frozenset[str] = frozenset(
    {"signed_imbalance", "notional_burst", "source_move_impulse"}
)

# Soft values explicitly rejected by the Family 2 validator.
REJECTED_FAMILY2_SOFT_VALUES: frozenset[str] = frozenset(
    {"all", "auto", "default", "existing_implemented_families"}
)


# ---------------------------------------------------------------------------
# Source-file descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OfflineSourceFile:
    """Descriptor for a single local historical file."""

    path: str
    logical_source_id: str
    venue: str
    symbol: str
    base_asset: str
    quote_asset: str
    source_kind: str
    stream_type: str
    resolution_type: str          # one of VALID_RESOLUTIONS
    timestamp_unit: str           # one of VALID_TIMESTAMP_UNITS
    expected_start_ns: int
    expected_end_ns: int
    file_size_bytes: int
    mtime_ns: int
    file_sha256: str
    row_count: int
    data_start_ns: int
    data_end_ns: int


# ---------------------------------------------------------------------------
# Trade / bar records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OfflineTradeRecord:
    """A single trade or agg-trade record from a historical file."""

    venue: str
    symbol: str
    base_asset: str
    quote_asset: str
    timestamp_ns: int
    price: float
    size: float
    side: str | None           # "buy" | "sell" | None
    trade_id: str | None
    source_file: str              # logical_source_id of originating OfflineSourceFile
    source_kind: str
    resolution_type: str          # RESOLUTION_TRADE or RESOLUTION_AGG_TRADE


@dataclass(frozen=True)
class OfflineBarRecord:
    """A single OHLCV bar from a historical file."""

    venue: str
    symbol: str
    base_asset: str
    quote_asset: str
    timestamp_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int | None
    source_file: str              # logical_source_id
    source_kind: str
    resolution_type: str          # must always be RESOLUTION_BAR


# ---------------------------------------------------------------------------
# Prepared dataset
# ---------------------------------------------------------------------------


@dataclass
class OfflinePreparedDataset:
    """Fully validated, hashed dataset ready for downstream Phase 2 use."""

    source_files: List[OfflineSourceFile]
    trades_by_stream: Dict[str, List[OfflineTradeRecord]]  # keyed by logical_source_id
    bars_by_stream: Dict[str, List[OfflineBarRecord]]       # keyed by logical_source_id
    data_corpus_hash: str
    schema_version: str
    created_at_utc: str
    git_sha: str


# ---------------------------------------------------------------------------
# Manifest model
# ---------------------------------------------------------------------------


@dataclass
class OfflinePrepareManifest:
    """JSON-serialisable run manifest for one offline prepare run."""

    run_id: str
    phase: str                        # always "offline_historical_prepare"
    generated_at_utc: str
    git_sha: str
    schema_version: str
    precommitment_hash: str | None  # nullable
    data_corpus_hash: str
    source_files: List[dict]
    timestamp_validation: dict
    hash_cache_used: bool
    hash_cache_entries_reused: int
    hash_cache_entries_recomputed: int
    normalized_time_range: dict
    stream_counts: dict
    resolution_summary: dict
    quote_currency_summary: dict
    safety: str                       # always "public_data_observer_only"
    forbidden_capabilities_present: bool  # must be False
    next_phase_allowed: bool


# ---------------------------------------------------------------------------
# Stress-window mode guard
# ---------------------------------------------------------------------------


def can_promote_from_window_mode(mode: str) -> bool:
    """
    Return True only if the window mode may allow candidate promotion.

    ``retrospective_diagnostic_only`` always returns False — enforced in code,
    not just prose.

    Phase 2 must pass this guard before any candidate promotion logic runs.
    """
    if mode == WINDOW_MODE_RETROSPECTIVE_DIAGNOSTIC:
        return False
    if mode == WINDOW_MODE_CAUSAL:
        return True
    raise ValueError(f"Unknown StressWindowSelectionMode: {mode!r}")


# ---------------------------------------------------------------------------
# Family 2 signal variant validator
# ---------------------------------------------------------------------------


def validate_family2_signal_variants(variants: Sequence[str]) -> None:
    """
    Raise ValueError if any Family 2 variant is soft/implicit or unknown.

    Prevents the Phase 2 grid from silently expanding when new signal
    generators are added.
    """
    for v in variants:
        if v in REJECTED_FAMILY2_SOFT_VALUES:
            raise ValueError(
                f"Family 2 signal variant {v!r} is a soft/implicit value and "
                f"is not allowed. Enumerate variants explicitly."
            )
        if v not in ALLOWED_FAMILY2_SIGNAL_VARIANTS:
            raise ValueError(
                f"Family 2 signal variant {v!r} is unknown. "
                f"Allowed: {sorted(ALLOWED_FAMILY2_SIGNAL_VARIANTS)}"
            )
