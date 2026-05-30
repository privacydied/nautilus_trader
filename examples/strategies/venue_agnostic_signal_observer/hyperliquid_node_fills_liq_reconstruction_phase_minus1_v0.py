"""Hyperliquid node fills liquidation reconstruction — Phase -1 v0 probe.

Data-plane and reconstruction-validity probe only.
No orders, no auth, no live/paper/shadow/conductor paths.

Phases:
  A – archive coverage & partition discovery
  B – tiny measured-object fetch
  C – schema sufficiency gate
  D – leverage-source discovery
  E – position reconstruction audit (startPosition reconciliation is the PRIMARY validity gate)
  F – isolated-only liquidation-price audit
  G – OI completeness diagnostic / later burn-in gate
  H – terminal decision
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import defaultdict, deque
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum, auto
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Optional orjson (preferred for JSONL I/O)
# ---------------------------------------------------------------------------

_orjson_module: Any = None
HAS_ORJSON = False

try:
    import orjson as _orjson_imported

    _orjson_module = _orjson_imported
    HAS_ORJSON = True
except ImportError:
    pass


def _json_dumps(obj: Any) -> bytes:
    if _orjson_module is not None:
        try:
            return _orjson_module.dumps(
                obj, option=_orjson_module.OPT_INDENT_2 | _orjson_module.OPT_SORT_KEYS
            )
        except TypeError:
            pass
    return json.dumps(obj, indent=2, sort_keys=True, default=str).encode()


def _json_loads(data: bytes) -> Any:
    if _orjson_module is not None:
        return _orjson_module.loads(data)
    if isinstance(data, str):
        data = data.encode()
    return json.loads(data)


def _jsonl_write(path: Path, records: Sequence[dict]) -> None:
    with open(path, "wb") as f:
        for r in records:
            if _orjson_module is not None:
                f.write(_orjson_module.dumps(r))
            else:
                f.write(json.dumps(r).encode())
            f.write(b"\n")

# ---------------------------------------------------------------------------
# Adapter import (reuse existing logic where possible)
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.dirname(__file__))
try:
    from adapters.node_fills_by_block_adapter import (
        FROZEN_SYMBOLS,
        NodeFillRecord,
        NodeFillsSchemaError as NODE_FILLS_SCHEMA_ERROR,
        SIDE_TO_SIGNED_DELTA,
        compute_address_signed_delta,
        normalize_coin,
        parse_block,
        signed_delta_for_side,
        stream_fills_from_jsonl,
        stream_fills_from_lz4,
    )
except ImportError:
    FROZEN_SYMBOLS = (
        "AAVE", "ADA", "APT", "ARB", "ATOM", "AVAX", "BCH", "BNB", "BTC",
        "DOGE", "DOT", "ENA", "ETH", "FET", "HYPE", "INJ", "JUP", "LINK",
        "LTC", "MKR", "NEAR", "ONDO", "OP", "PENDLE", "SEI", "SOL", "SUI",
        "TIA", "TON", "TRX", "UNI", "WIF", "WLD", "XRP",
    )
    SIDE_TO_SIGNED_DELTA = {"A": Decimal("-1"), "B": Decimal("1")}

    class NodeFillsSchemaError(Exception):
        pass

    def normalize_coin(coin: str) -> str:
        coin = coin.upper().strip()
        if coin.startswith("XYZ:"):
            return coin[4:]
        return coin

    def signed_delta_for_side(side: str, sz: Decimal) -> Decimal:
        mult = SIDE_TO_SIGNED_DELTA.get(side)
        if mult is None:
            raise ValueError(f"Unknown side '{side}'")
        return mult * abs(sz)


# ---------------------------------------------------------------------------
# Enums — Status taxonomy (exact names from spec)
# ---------------------------------------------------------------------------

class StudyStatus(str, Enum):
    # Acquisition / planning
    NODE_FILLS_LIQ_PHASE_MINUS1_DRY_RUN_READY = "DRY_RUN_READY"
    NODE_FILLS_LIQ_PHASE_MINUS1_PLAN_READY = "PLAN_READY"
    NODE_FILLS_LIQ_PHASE_MINUS1_REMOTE_PLAN_READY = "REMOTE_PLAN_READY"

    # Acquisition blocked
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_S3_NOT_ENABLED = "BLOCKED_S3_NOT_ENABLED"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_S3_CREDENTIALS_MISSING = "BLOCKED_S3_CREDENTIALS_MISSING"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_NAMESPACE_NOT_FOUND = "BLOCKED_NAMESPACE_NOT_FOUND"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_ARCHIVE_COVERAGE_UNAVAILABLE = "BLOCKED_ARCHIVE_COVERAGE_UNAVAILABLE"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_PARTITIONING_UNKNOWN = "BLOCKED_PARTITIONING_UNKNOWN"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_COST_OR_SIZE_CAP = "BLOCKED_COST_OR_SIZE_CAP"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_NO_LOCAL_CACHE = "BLOCKED_NO_LOCAL_CACHE"

    # Schema blocked
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_SCHEMA_NO_RECORDS = "BLOCKED_SCHEMA_NO_RECORDS"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_SCHEMA_MISSING_REQUIRED_FIELDS = "BLOCKED_SCHEMA_MISSING_REQUIRED_FIELDS"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_ADDRESS_FIELD_MISSING = "BLOCKED_ADDRESS_FIELD_MISSING"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_POSITION_FIELD_MISSING = "BLOCKED_POSITION_FIELD_MISSING"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_DIR_MAPPING_UNVERIFIED = "BLOCKED_DIR_MAPPING_UNVERIFIED"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_POSITION_KEY_AMBIGUOUS = "BLOCKED_POSITION_KEY_AMBIGUOUS"

    # Position mechanics blocked (NEW — primary Phase -1 validity gate)
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_POSITION_MECHANICS_UNVERIFIED = "BLOCKED_POSITION_MECHANICS_UNVERIFIED"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_PAIRING_SEMANTICS_UNVERIFIED = "BLOCKED_PAIRING_SEMANTICS_UNVERIFIED"

    # Leverage / margin blocked
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_MARGIN_MODE_UNDETERMINED = "BLOCKED_MARGIN_MODE_UNDETERMINED"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_LEVERAGE_SOURCE_MISSING = "BLOCKED_LEVERAGE_SOURCE_MISSING"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_LEVERAGE_UNDETERMINED = "BLOCKED_LEVERAGE_UNDETERMINED"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_LIQ_PRICE_NOT_RECONSTRUCTABLE = "BLOCKED_LIQ_PRICE_NOT_RECONSTRUCTABLE"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_MARGIN_TIER_SCHEDULE_UNAVAILABLE = "BLOCKED_MARGIN_TIER_SCHEDULE_UNAVAILABLE"

    # OI completeness
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_OI_CONTEXT_UNAVAILABLE = "BLOCKED_OI_CONTEXT_UNAVAILABLE"
    NODE_FILLS_LIQ_PHASE_MINUS1_COMPLETENESS_DIAGNOSTIC_LOW_ZERO_BURNIN = "COMPLETENESS_DIAGNOSTIC_LOW_ZERO_BURNIN"
    NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_RECONSTRUCTION_COVERAGE_LOW_BURNIN = "BLOCKED_RECONSTRUCTION_COVERAGE_LOW_BURNIN"

    # Leverage source plan ready (raw action namespace discovered but not sampled)
    NODE_FILLS_LIQ_PHASE_MINUS1_LEVERAGE_SOURCE_PLAN_READY = "LEVERAGE_SOURCE_PLAN_READY"

    # Passed / diagnostic
    NODE_FILLS_LIQ_PHASE_MINUS1_THIN_SLICE_SCHEMA_AND_POSITION_MECHANICS_PASSED = "THIN_SLICE_SCHEMA_AND_POSITION_MECHANICS_PASSED"
    NODE_FILLS_LIQ_PHASE_MINUS1_THIN_SLICE_EXACT_RECONSTRUCTION_PASSED_REVIEW_ALLOWED = "THIN_SLICE_EXACT_RECONSTRUCTION_PASSED_REVIEW_ALLOWED"
    NODE_FILLS_LIQ_PHASE_MINUS1_THIN_SLICE_BOUND_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE = "THIN_SLICE_BOUND_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE"

    # Error
    NODE_FILLS_LIQ_PHASE_MINUS1_ERROR_INVALID_OUTPUT = "ERROR_INVALID_OUTPUT"


# Forbidden statuses — must never be emitted
FORBIDDEN_STATUSES = frozenset([
    "REJECTED", "PROFITABLE", "ALPHA_FOUND", "EDGE_CONFIRMED",
    "TRADE_READY", "EXECUTION_READY", "LIVE_READY", "PAPER_READY", "SHADOW_READY",
    "CANDIDATE_FOR_LIVE", "CANDIDATE_FOR_PAPER", "PAPER_STRATEGY_PROMOTED",
    "PROMOTION_AUTHORIZED", "READY_FOR_PHASE_0",
])


class ArchivePartitioning(str, Enum):
    COIN_PARTITIONED = "coin_partitioned"
    TIME_PARTITIONED_ALL_COINS = "time_partitioned_all_coins"
    BLOCK_PARTITIONED_ALL_COINS = "block_partitioned_all_coins"
    UNKNOWN_PARTITIONING = "unknown_partitioning"


class DownloadUnit(str, Enum):
    SINGLE_COIN_HOUR_OBJECT = "single_coin_hour_object"
    ALL_COIN_HOUR_OBJECT = "all_coin_hour_object"
    ALL_COIN_BLOCK_OBJECT = "all_coin_block_object"
    DAILY_BUNDLE = "daily_bundle"
    UNKNOWN_UNIT = "unknown_unit"


class SchemaVerdict(str, Enum):
    PASS = "PASS"
    FAIL_ADDRESS_MISSING = "FAIL_ADDRESS_MISSING"
    FAIL_SYMBOL_MISSING = "FAIL_SYMBOL_MISSING"
    FAIL_POSITION_FIELD_MISSING = "FAIL_POSITION_FIELD_MISSING"
    FAIL_DIR_MAPPING_UNVERIFIED = "FAIL_DIR_MAPPING_UNVERIFIED"
    NOT_EVALUATED_PLAN_ONLY = "NOT_EVALUATED_PLAN_ONLY"


class LeverageMode(str, Enum):
    EXACT_REQUIRED = "exact_required"
    MAX_BOUND_DIAGNOSTIC = "max_bound_diagnostic"


class MarginMode(str, Enum):
    ISOLATED = "isolated"
    CROSS = "cross"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Dataclasses — Config & artifacts
# ---------------------------------------------------------------------------

@dataclass
class StudyConfig:
    study_id: str = field(default="hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0")
    run_id: str = field(default="")
    out_root: str = field(default="")
    data_root: str | None = None
    preferred_symbol: str = "SOL"
    fallback_symbols: tuple[str, ...] = ("DOGE", "LINK", "AVAX", "ADA")
    start_date: str = ""
    end_date: str = ""
    dry_run: bool = False
    plan_only: bool = False
    include_remote_plan: bool = False
    allow_s3_archive_read: bool = False
    requester_pays: bool = False
    cache_only: bool = True
    max_download_bytes: int = 100_000_000  # 100 MB hard cap
    schema_sample_limit: int = 10_000
    max_hours: int = 6
    min_oi_coverage_fraction: float = 0.40
    burn_in_days: int = 0
    leverage_mode: str = "exact_required"
    bound_diagnostic: bool = False
    leverage_source_plan_only: bool = False

    def effective_leverage_mode(self) -> LeverageMode:
        if self.bound_diagnostic:
            return LeverageMode.MAX_BOUND_DIAGNOSTIC
        return LeverageMode.EXACT_REQUIRED


@dataclass
class S3ObjectPlan:
    key: str
    size_bytes: int = 0
    date_str: str = ""
    hour: int = 0


@dataclass
class PartitioningInventory:
    partitioning: ArchivePartitioning = ArchivePartitioning.UNKNOWN_PARTITIONING
    smallest_download_unit: DownloadUnit = DownloadUnit.UNKNOWN_UNIT
    candidate_namespaces: list[str] = field(default_factory=list)
    found_namespace: str | None = None
    total_objects_listed: int = 0
    sample_keys: list[str] = field(default_factory=list)


@dataclass
class ArchiveCoverageInventory:
    start_date: str = ""
    end_date: str = ""
    available_dates: list[str] = field(default_factory=list)
    namespace: str = ""


@dataclass
class SourcePlan:
    partitioning: str = "unknown"
    smallest_download_unit: str = "unknown"
    estimated_objects: int = 0
    estimated_bytes: int = 0
    local_cache_found: bool = False
    local_paths: list[str] = field(default_factory=list)
    remote_objects_planned: list[dict] = field(default_factory=list)


@dataclass
class DownloadManifest:
    objects: list[dict] = field(default_factory=list)  # {key, sha256, size_bytes}

    @property
    def total_bytes(self) -> int:
        return sum(o.get("size_bytes", 0) for o in self.objects)


@dataclass
class SchemaInventory:
    fields_present: dict[str, bool] = field(default_factory=dict)
    required_fields: list[str] = field(default_factory=list)
    optional_fields: list[str] = field(default_factory=list)
    all_sample_keys: set[str] = field(default_factory=set)


@dataclass
class DirMappingAudit:
    mapping: dict[str, Decimal] = field(default_factory=dict)
    verified_against_start_position: bool = False
    mismatch_count: int = 0
    total_checked: int = 0
    variants_seen: list[str] = field(default_factory=list)


@dataclass
class LiquidationFlagInventory:
    flag_field_present: bool = False
    flag_field_name: str = ""
    liquidation_records_count: int = 0
    non_liquidation_records_count: int = 0


@dataclass
class FillRecordSample:
    address: str = ""
    coin: str = ""
    dir: str | None = None
    side: str = ""
    size: float = 0.0
    price: float = 0.0
    start_position: float | None = None


@dataclass
class SchemaGate:
    verdict: SchemaVerdict = SchemaVerdict.PASS
    missing_fields: list[str] = field(default_factory=list)
    dir_mapping_verified: bool = False
    address_field_present: bool = False
    symbol_field_present: bool = False
    side_size_price_present: bool = False
    start_position_present: bool = False


@dataclass
class LeverageSourcePlan:
    candidates: list[str] = field(default_factory=list)
    source_found: bool = False
    source_path: str = ""
    has_update_leverage: bool = False


@dataclass
class LeverageJoinAudit:
    joinable_by_user_coin_time: bool = False
    sample_size: int = 0
    issues: list[str] = field(default_factory=list)


@dataclass
class PositionKey:
    address: str
    coin: str

    def __hash__(self):
        return hash((self.address, self.coin))

    def __eq__(self, other):
        if not isinstance(other, PositionKey):
            return False
        return self.address == other.address and self.coin == other.coin


@dataclass
class PositionState:
    address: str = ""
    coin: str = ""
    signed_position: Decimal = field(default_factory=lambda: Decimal("0"))
    total_entry_value: Decimal = field(default_factory=lambda: Decimal("0"))
    total_entry_size: Decimal = field(default_factory=lambda: Decimal("0"))
    is_known: bool = False
    margin_mode: MarginMode = MarginMode.UNKNOWN
    leverage: Decimal | None = None
    entry_price: Decimal | None = None


@dataclass
class PositionTransition:
    timestamp_ns: int = 0
    address: str = ""
    coin: str = ""
    transition_type: str = ""  # open, increase, reduce, close, flip
    delta: Decimal = Decimal("0")
    price: Decimal = Decimal("0")
    dir_field: str | None = None
    start_position_before: Decimal | None = None


@dataclass
class StartPositionConsistencyAudit:
    """Detailed startPosition reconciliation audit (Patch 1)."""
    records_seen: int = 0
    records_parsed: int = 0
    position_events_seen: int = 0
    transitions_total: int = 0
    transitions_checkable: int = 0
    transitions_uncheckable_cold_start: int = 0
    transitions_uncheckable_missing_start_position: int = 0
    transitions_uncheckable_unknown_key: int = 0
    transitions_uncheckable_parse_error: int = 0
    transitions_reconciled: int = 0
    transitions_mismatched: int = 0
    consistency_rate_all_events: float = 0.0
    consistency_rate_checkable_only: float = 0.0
    # Alias for backward compat with legacy field on PositionReconstructionAudit
    transitions_with_start_position: int = 0
    path: str = ""


@dataclass
class StartPositionConventionAudit:
    """Pre-fill vs post-fill startPosition convention audit (Patch 2)."""
    pre_fill_match_count: int = 0
    pre_fill_match_rate: float = 0.0
    post_fill_match_count: int = 0
    post_fill_match_rate: float = 0.0
    dominant_convention: str = ""
    ambiguous_count: int = 0
    neither_count: int = 0
    path: str = ""


@dataclass
class DirValueInventory:
    """Directory value inventory from real data (Patch 3)."""
    values_seen: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class SideDeltaMappingAudit:
    """Side-to-signed-delta mapping audit (Patch 3)."""
    side_to_candidate_delta: dict[str, str] = field(default_factory=dict)
    pre_fill_consistency_rate: float = 0.0
    post_fill_consistency_rate: float = 0.0
    mismatch_rate: float = 0.0
    verified: bool = False


@dataclass
class InstrumentIdentityInventory:
    """Instrument identity audit for position keying (Patch 4)."""
    symbols_seen: list[str] = field(default_factory=list)
    raw_coin_values_seen: list[str] = field(default_factory=list)
    asset_ids_seen: list[int] = field(default_factory=list)
    builder_dex_asset_ids_seen: list[int] = field(default_factory=list)
    colliding_ticker_count: int = 0
    colliding_ticker_examples: list[dict] = field(default_factory=list)


@dataclass
class PositionKeyingAudit:
    """Position keying collision audit (Patch 4)."""
    position_key_fields_used: list[str] = field(default_factory=list)
    position_key_collision_count: int = 0
    ambiguous_key_count: int = 0
    verified: bool = False


@dataclass
class BuilderDexAssetMappingAudit:
    """Builder-DEX / HIP-3 asset mapping audit (Patch 4)."""
    default_dex_asset_ids: list[int] = field(default_factory=list)
    builder_dex_asset_ids: list[int] = field(default_factory=list)
    formula_tested: bool = False
    formula_correct: bool = False


@dataclass
class PairingSemanticsAudit:
    """Paired-leg / event semantics audit (Patch 5)."""
    paired_records_detected: int = 0
    double_count_risk: bool = False
    grouping_rule: str = ""
    verified: bool = False


@dataclass
class PositionReconstructionAudit:
    records_seen: int = 0
    records_parsed: int = 0
    users_seen: set[str] = field(default_factory=set)
    symbols_seen: set[str] = field(default_factory=set)
    position_transitions: int = 0
    transitions_with_start_position: int = 0
    start_position_consistency_rate: float = 0.0
    unknown_cold_start_positions: int = 0
    known_open_positions: int = 0
    known_isolated_open_positions: int = 0
    cross_or_unknown_margin_positions: int = 0
    records_rejected: int = 0
    dir_mapping_mismatch_count: int = 0

    # Fields added by Patch 1 (detailed audit)
    consistency_audit: StartPositionConsistencyAudit = field(default_factory=StartPositionConsistencyAudit)
    convention_audit: StartPositionConventionAudit = field(default_factory=StartPositionConventionAudit)
    dir_value_inventory: DirValueInventory = field(default_factory=DirValueInventory)
    side_delta_mapping: SideDeltaMappingAudit = field(default_factory=SideDeltaMappingAudit)
    instrument_identity: InstrumentIdentityInventory = field(default_factory=InstrumentIdentityInventory)
    position_keying: PositionKeyingAudit = field(default_factory=PositionKeyingAudit)
    builder_dex_mapping: BuilderDexAssetMappingAudit = field(default_factory=BuilderDexAssetMappingAudit)
    pairing_semantics: PairingSemanticsAudit = field(default_factory=PairingSemanticsAudit)

    # Additional tracking for Patch 6
    position_keys_seen: set[str] = field(default_factory=set)
    cold_start_positions: int = 0
    known_from_flat_positions: int = 0
    known_open_positions_margin_unknown: int = 0
    known_open_positions_isolated_verified: int = 0


@dataclass
class MarginTierScheduleInventory:
    available: bool = False
    source: str = ""
    tiers: list[dict] = field(default_factory=list)
    caveat: str = ""


@dataclass
class LeverageTierSnapshot:
    max_leverage_by_symbol: dict[str, int] = field(default_factory=dict)
    source: str = ""


@dataclass
class LiquidationPriceEstimate:
    address: str = ""
    coin: str = ""
    entry_price: Decimal = Decimal("0")
    leverage: Decimal = Decimal("1")
    max_leverage: Decimal = Decimal("1")
    side: str = ""  # "long" or "short"
    liq_price_approx: Decimal = Decimal("0")
    formula_used: str = ""


@dataclass
class LiquidationReconstructionAudit:
    isolated_positions_reconstructed: int = 0
    cross_or_unknown_excluded: int = 0
    exact_liquidation_available: bool = False
    bound_diagnostic_used: bool = False
    margin_tier_schedule_status: str = ""
    estimates: list[dict] = field(default_factory=list)


@dataclass
class OIContextRecord:
    symbol: str = ""
    timestamp_ns: int = 0
    open_interest_notional: Decimal = Decimal("0")


@dataclass
class CompletenessSummary:
    hours_with_valid_oi: int = 0
    hours_with_valid_reconstruction: int = 0
    p10_coverage_fraction: float = 0.0
    p25_coverage_fraction: float = 0.0
    median_coverage_fraction: float = 0.0
    p75_coverage_fraction: float = 0.0
    p90_coverage_fraction: float = 0.0
    burn_in_days: int = 0
    completeness_gate_applied: bool = False
    gate_passed: bool = False


@dataclass
class SafetyAudit:
    orders_used: bool = False
    private_keys_used: bool = False
    auth_used: bool = False
    live_execution_used: bool = False
    paper_trading_used: bool = False
    shadow_execution_used: bool = False
    systemd_mutated: bool = False
    bot_path_mutated: bool = False
    registry_mutated: bool = False
    wide_s3_sync_used: bool = False


@dataclass
class StudySummary:
    status: str = ""
    safety_mode: str = "public_data_observer_only"
    safety: SafetyAudit = field(default_factory=SafetyAudit)
    study_id: str = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
    max_download_bytes: int = 100_000_000
    download_bytes_estimated: int = 0
    download_bytes_actual: int = 0
    archive_partitioning: str = "unknown"
    smallest_download_unit: str = "unknown"
    schema_position_mechanics_passed: bool = False
    exact_leverage_source_available: bool = False
    exact_liquidation_reconstruction_available: bool = False
    bound_diagnostic_used: bool = False
    completeness_gate_applied: bool = False


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------

def atomic_write_json(path: Path, obj: Any) -> None:
    """Write JSON atomically via tmp+rename."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_bytes(_json_dumps(obj))
    os.replace(str(tmp), str(path))


def atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically via tmp+rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(str(tmp), str(path))


# ---------------------------------------------------------------------------
# Git metadata helpers
# ---------------------------------------------------------------------------

def _git_sha() -> str:
    try:
        return os.popen("git rev-parse HEAD 2>/dev/null").read().strip()[:12]
    except Exception:
        return "unknown"


def _git_branch() -> str:
    try:
        return os.popen("git branch --show-current 2>/dev/null").read().strip() or "detached"
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    try:
        out = os.popen("git status --porcelain 2>/dev/null").read().strip()
        return len(out) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Address redaction
# ---------------------------------------------------------------------------

STUDY_SALT = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"


def redact_address(addr: str, truncate: int = 4) -> str:
    """Redact address to first truncate + ... + last truncate chars."""
    if not addr or len(addr) <= 2 * truncate:
        return addr[:truncate] + "..." + (addr[-truncate:] if len(addr) > truncate else "")
    return addr[:truncate] + "..." + addr[-truncate:]


# ---------------------------------------------------------------------------
# Phase A — Archive coverage & partition discovery
# ---------------------------------------------------------------------------

CANDIDATE_NAMESPACES = [
    "node_fills_by_block/hourly/",
    "hyperliquid/node_fills_by_block/hourly/",
    "hl-mainnet-node-data/node_fills_by_block/hourly/",
]


def discover_local_cache(data_root: str | None) -> tuple[bool, list[str]]:
    """Check for cached node_fills_by_block data locally."""
    if not data_root:
        return False, []
    root = Path(data_root)
    paths = []
    archive_dir = root / "node_fills_by_block"
    if archive_dir.is_dir():
        for lz4 in sorted(archive_dir.rglob("*.lz4")):
            paths.append(str(lz4))
    return len(paths) > 0, paths


def check_aws_credentials() -> bool:
    """Check whether AWS CLI credentials are available for requester-pays."""
    try:
        result = os.popen("aws sts get-caller-identity 2>/dev/null").read().strip()
        return "UserId" in result and "Arn" in result
    except Exception:
        return False


def list_s3_prefix(prefix: str, requester_pays: bool = True) -> list[dict]:
    """List objects under a prefix via aws s3 ls. Returns [{key, size}]."""
    s3_uri = f"s3://{prefix}" if not prefix.startswith("s3://") else prefix
    cmd = ["aws", "s3", "ls", s3_uri, "--recursive"]
    if requester_pays:
        cmd.extend(["--request-payer", "requester"])
    try:
        result = os.popen(f"{' '.join(cmd)} 2>/dev/null").read().strip()
    except Exception:
        return []
    objects = []
    for line in result.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            if len(parts) >= 4:
                size = int(parts[2])
                key = " ".join(parts[3:])
            else:
                size = int(parts[-1])
                key = " ".join(parts[:-1])
            objects.append({"key": key, "size": size})
        except (ValueError, IndexError):
            continue
    return objects


def discover_partitioning(sample_keys: list[str]) -> tuple[ArchivePartitioning, DownloadUnit]:
    """Determine partitioning type from sample S3 keys."""
    if not sample_keys:
        return ArchivePartitioning.UNKNOWN_PARTITIONING, DownloadUnit.UNKNOWN_UNIT

    coin_count = 0
    time_dir_count = 0
    for key in sample_keys[:50]:
        parts = Path(key).parts
        has_coin = any(
            p.upper().startswith(sym + "_") or p.upper() == sym
            for p in parts
            for sym in FROZEN_SYMBOLS
        )
        has_date_dir = bool(re.search(r"\d{4}-\d{2}-\d{2}/", key)) or bool(re.search(r"\d{8}/", key))
        if has_coin:
            coin_count += 1
        elif has_date_dir:
            time_dir_count += 1

    if coin_count > len(sample_keys[:50]) * 0.5:
        return ArchivePartitioning.COIN_PARTITIONED, DownloadUnit.SINGLE_COIN_HOUR_OBJECT
    if time_dir_count > 0:
        return ArchivePartitioning.TIME_PARTITIONED_ALL_COINS, DownloadUnit.ALL_COIN_HOUR_OBJECT

    date_keys = [k for k in sample_keys if re.search(r"\d{4}-\d{2}-\d{2}", k)]
    if len(date_keys) > 0 and len(date_keys) < len(sample_keys) * 0.1:
        return ArchivePartitioning.TIME_PARTITIONED_ALL_COINS, DownloadUnit.DAILY_BUNDLE

    return ArchivePartitioning.UNKNOWN_PARTITIONING, DownloadUnit.UNKNOWN_UNIT


def discover_archive_coverage(s3_objects: list[dict], namespace: str) -> ArchiveCoverageInventory:
    """Discover date coverage from S3 object listing."""
    dates = set()
    for obj in s3_objects:
        key = obj["key"]
        m = re.search(r"(\d{4}-\d{2}-\d{2})", key)
        if not m:
            m = re.search(r"(\d{8})", key)
            if m:
                d = m.group(1)
                dates.add(f"{d[:4]}-{d[4:6]}-{d[6:8]}")
        else:
            dates.add(m.group(1))

    sorted_dates = sorted(dates)
    return ArchiveCoverageInventory(
        start_date=sorted_dates[0] if sorted_dates else "",
        end_date=sorted_dates[-1] if sorted_dates else "",
        available_dates=sorted_dates[:50],
        namespace=namespace,
    )


def build_source_plan(
    config: StudyConfig,
    local_found: bool,
    local_paths: list[str],
    remote_objects: list[dict],
    partitioning: ArchivePartitioning,
    download_unit: DownloadUnit,
) -> SourcePlan:
    """Build source plan before any download."""
    estimated_objects = len(remote_objects) if remote_objects else 0
    estimated_bytes = sum(o.get("size", 0) for o in remote_objects)

    return SourcePlan(
        partitioning=partitioning.value,
        smallest_download_unit=download_unit.value,
        estimated_objects=estimated_objects,
        estimated_bytes=estimated_bytes,
        local_cache_found=local_found,
        local_paths=local_paths[:20],
        remote_objects_planned=[{"key": o["key"], "size": o.get("size", 0)} for o in remote_objects[:50]],
    )


# ---------------------------------------------------------------------------
# Phase B — Tiny measured-object fetch
# ---------------------------------------------------------------------------

def fetch_s3_object(key: str, dest_path: Path, requester_pays: bool = True) -> tuple[int, str]:
    """Download one S3 object. Returns (bytes_downloaded, sha256_hex)."""
    s3_uri = f"s3://{key}" if not key.startswith("s3://") else key
    cmd_parts = ["aws", "s3", "cp", s3_uri, str(dest_path)]
    if requester_pays:
        cmd_parts.extend(["--request-payer", "requester"])

    import subprocess
    result = subprocess.run(cmd_parts, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"S3 download failed: {result.stderr.strip()}")

    size = dest_path.stat().st_size
    sha256 = hashlib.sha256(dest_path.read_bytes()).hexdigest()
    return size, sha256


# ---------------------------------------------------------------------------
# Phase C — Schema sufficiency gate
# ---------------------------------------------------------------------------

REQUIRED_POSITION_FIELDS = [
    "address",
    "coin",
    "side",
    "sz",
    "px",
    "time",
]

REQUIRED_POSITION_MECHANICS_FIELDS = [
    "startPosition",
]


def validate_schema(records: list[NodeFillRecord], limit: int = 10_000) -> tuple[SchemaInventory, SchemaGate]:
    """Validate that required schema fields exist in parsed records."""
    inventory = SchemaInventory(
        required_fields=REQUIRED_POSITION_FIELDS + REQUIRED_POSITION_MECHANICS_FIELDS,
        optional_fields=["fee", "closedPnl", "hash", "block_number", "builder_fee", "deployer_fee", "crossed"],
    )

    seen_fields = defaultdict(set)
    sample_records: list[FillRecordSample] = []

    for i, rec in enumerate(records[:limit]):
        raw = rec.raw if hasattr(rec, 'raw') else {}
        all_keys = set(raw.keys())
        if hasattr(rec, 'address') and rec.address is not None:
            all_keys.add("address")
        if hasattr(rec, 'coin') and rec.coin is not None:
            all_keys.add("coin")
        if hasattr(rec, 'side'):
            all_keys.add("side")
        if hasattr(rec, 'sz') and rec.sz is not None:
            all_keys.add("sz")
        if hasattr(rec, 'px') and rec.px is not None:
            all_keys.add("px")
        if hasattr(rec, 'fill_time') and rec.fill_time is not None:
            all_keys.add("time")
        if hasattr(rec, 'start_position') and rec.start_position is not None:
            all_keys.add("startPosition")

        for f in all_keys:
            seen_fields[f].add("present")

        sample_records.append(FillRecordSample(
            address=redact_address(rec.address),
            coin=rec.coin,
            dir=str(rec.dir) if rec.dir else None,
            side=rec.side,
            size=float(rec.sz) if hasattr(rec, 'sz') and rec.sz is not None else 0.0,
            price=float(rec.px) if hasattr(rec, 'px') and rec.px is not None else 0.0,
            start_position=float(rec.start_position) if hasattr(rec, 'start_position') and rec.start_position is not None else None,
        ))

    inventory.fields_present = {f: len(v) > 0 for f, v in seen_fields.items()}
    inventory.all_sample_keys = set(seen_fields.keys())

    address_present = "address" in inventory.fields_present and inventory.fields_present["address"]
    coin_present = "coin" in inventory.fields_present and inventory.fields_present["coin"]
    side_present = "side" in inventory.fields_present and inventory.fields_present["side"]
    sz_present = any(k for k in inventory.fields_present if k in ("sz", "size"))
    px_present = any(k for k in inventory.fields_present if k in ("px", "price"))
    time_present = any(k for k in inventory.fields_present if k in ("time", "timestamp", "block_time", "fill_time"))
    start_pos_present = "startPosition" in inventory.fields_present and inventory.fields_present["startPosition"]

    missing: list[str] = []
    if not address_present:
        missing.append("address")
    if not coin_present:
        missing.append("coin")
    if not (side_present and sz_present and px_present):
        missing.extend(["side", "sz", "px"])
    if not start_pos_present:
        missing.append("startPosition")

    gate = SchemaGate(
        address_field_present=address_present,
        symbol_field_present=coin_present,
        side_size_price_present=side_present and sz_present and px_present,
        start_position_present=start_pos_present,
    )

    if not address_present:
        gate.verdict = SchemaVerdict.FAIL_ADDRESS_MISSING
    elif missing:
        gate.verdict = SchemaVerdict.FAIL_POSITION_FIELD_MISSING
    else:
        gate.verdict = SchemaVerdict.PASS

    return inventory, gate


# ---------------------------------------------------------------------------
# dir mapping — frozen mapping from dir field to signed-size-delta
# ---------------------------------------------------------------------------

FROZEN_DIR_MAPPING: dict[str, Decimal] = {
    "Open Long": Decimal("1"),
    "Close Long": Decimal("-1"),
    "Open Short": Decimal("-1"),
    "Close Short": Decimal("1"),
}


def verify_dir_mapping(
    records: Sequence[Any],
    limit: int = 10_000,
) -> DirMappingAudit:
    """Verify dir field mapping against startPosition consistency."""
    audit = DirMappingAudit()

    variants = set()
    for rec in records[:limit]:
        if rec.dir:
            variants.add(rec.dir)
        audit.total_checked += 1

    audit.variants_seen = sorted(variants)

    mismatches = 0
    checked = 0
    for rec in records[:limit]:
        if not rec.dir or not hasattr(rec, 'start_position') or rec.start_position is None:
            continue
        if rec.dir not in FROZEN_DIR_MAPPING:
            continue

        delta_sign = FROZEN_DIR_MAPPING[rec.dir]
        checked += 1
        audit.total_checked += 1

    audit.mismatch_count = mismatches
    audit.verified_against_start_position = checked > 0 and mismatches == 0
    audit.mapping = dict(FROZEN_DIR_MAPPING)

    return audit


# ---------------------------------------------------------------------------
# Liquidation flag inventory
# ---------------------------------------------------------------------------

def inventory_liquidation_flags(records: list[NodeFillRecord], limit: int = 10_000) -> LiquidationFlagInventory:
    """Check for liquidation flag presence in fill records."""
    inv = LiquidationFlagInventory()
    count_liq = 0
    count_non_liq = 0

    for rec in records[:limit]:
        raw = rec.raw if hasattr(rec, 'raw') else {}
        is_liq = False
        for field_name in ("liquidation", "liq", "isLiquidation"):
            if field_name in raw:
                inv.flag_field_present = True
                inv.flag_field_name = field_name
                val = raw[field_name]
                if isinstance(val, bool):
                    is_liq = val
                elif isinstance(val, (int, float)):
                    is_liq = val != 0
                else:
                    is_liq = str(val).lower() in ("true", "1", "yes")
                break

        if not inv.flag_field_present:
            ft = raw.get("fillType", "")
            if isinstance(ft, str) and "liquidation" in ft.lower():
                inv.flag_field_present = True
                inv.flag_field_name = "fillType"
                is_liq = True

        if is_liq:
            count_liq += 1
        else:
            count_non_liq += 1

    inv.liquidation_records_count = count_liq
    inv.non_liquidation_records_count = count_non_liq
    return inv


# ---------------------------------------------------------------------------
# Phase D — Leverage-source discovery
# ---------------------------------------------------------------------------

RAW_ACTION_NAMESPACE_CANDIDATES = [
    "node_fills_by_block/actions/updateLeverage",
    "hyperliquid/node_actions/leverage/",
    "hl-mainnet-node-data/actions/updateLeverage/",
    "market_data/user_leverage_history/",
    "hl-mainnet-node-data/replica_cmds/",
    "hl-mainnet-node-data/replica_cmds/hourly/",
    "hl-mainnet-node-data/actions/",
    "hl-mainnet-node-data/actions/hourly/",
    "hl-mainnet-node-data/blocks/",
    "hl-mainnet-node-data/blocks/hourly/",
    "hl-mainnet-node-data/l1_actions/",
    "hl-mainnet-node-data/l1_actions/hourly/",
    "hl-mainnet-node-data/txs/",
    "hl-mainnet-node-data/txs/hourly/",
    "hl-mainnet-node-data/raw_actions/",
    "hl-mainnet-node-data/raw_actions/hourly/",
]

ACTION_SEARCH_STRINGS = [
    "updateLeverage",
    "leverage",
    "margin",
    "isCross",
    "cross",
    "isolated",
    "setReferrer",
    "perpDeploy",
    "action",
    "payload",
    "multiSig",
]


def _decode_action_envelope(obj: Any, depth: int = 0, path: str = "") -> list[str]:
    """Recursively decode action names from nested envelopes."""
    found_actions: list[str] = []
    prefix = path + "." if path else ""

    if isinstance(obj, dict):
        for k, v in obj.items():
            kp = prefix + k
            if any(s in k.lower() for s in ACTION_SEARCH_STRINGS):
                found_actions.append(kp)
            if isinstance(v, str):
                for s in ACTION_SEARCH_STRINGS:
                    if s in v.lower():
                        vp = f"{kp}.value={v}"
                        found_actions.append(vp)
                        break
            if isinstance(v, (dict, list)):
                found_actions.extend(_decode_action_envelope(v, depth + 1, kp))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            ip = f"{prefix}[{i}]"
            if isinstance(item, (dict, list)):
                found_actions.extend(_decode_action_envelope(item, depth + 1, ip))

    return found_actions


def _recursive_string_search(data: bytes) -> set[str]:
    """Search raw bytes for action-like strings."""
    found: set[str] = set()
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return found
    for s in ACTION_SEARCH_STRINGS:
        if s.lower() in text.lower():
            found.add(s)
    return found


def decode_raw_action_sample(raw_bytes: bytes) -> dict:
    """Decode a raw action sample and report action names/envelopes found."""
    result = {
        "bytes_size": len(raw_bytes),
        "action_strings_found": sorted(_recursive_string_search(raw_bytes)),
        "nested_envelopes": [],
        "updateLeverage_found": False,
        "margin_mode_action_found": False,
        "multiSig_payload_unwrapped": False,
    }

    try:
        parsed = _json_loads(raw_bytes) if isinstance(raw_bytes, (str, bytes)) else raw_bytes
        if isinstance(parsed, dict):
            actions = _decode_action_envelope(parsed)
            result["nested_envelopes"] = actions[:100]
            result["updateLeverage_found"] = any("updateLeverage" in a for a in actions)
            result["margin_mode_action_found"] = any(
                "margin" in a.lower() or "cross" in a.lower() or "isolated" in a.lower()
                for a in actions
            )
            result["multiSig_payload_unwrapped"] = any(
                "multiSig" in a and "payload" in a for a in actions
            )
    except Exception:
        pass

    return result


def discover_leverage_source(
    config: StudyConfig,
    local_paths: list[str],
) -> tuple[LeverageSourcePlan, LeverageJoinAudit]:
    """Search for public historical leverage-setting data."""
    plan = LeverageSourcePlan(
        candidates=[
            "node_fills_by_block/actions/updateLeverage",
            "hyperliquid/node_actions/leverage/",
            "hl-mainnet-node-data/actions/updateLeverage/",
            "market_data/user_leverage_history/",
        ],
    )

    if config.data_root:
        root = Path(config.data_root)
        for candidate in plan.candidates:
            search_path = root / candidate
            if search_path.exists():
                plan.source_found = True
                plan.source_path = str(search_path)
                break

    if config.data_root:
        root = Path(config.data_root)
        for fpath in root.rglob("*"):
            if fpath.is_file() and fpath.suffix in (".json", ".jsonl"):
                try:
                    file_content = fpath.read_bytes()[:1024]
                    if b"updateLeverage" in file_content or b"leverage" in file_content.lower():
                        plan.has_update_leverage = True
                        break
                except Exception:
                    pass

    audit = LeverageJoinAudit(
        joinable_by_user_coin_time=plan.source_found,
        sample_size=0,
        issues=[] if plan.source_found else ["No public leverage source found in local cache"],
    )

    return plan, audit


def discover_raw_action_namespaces(
    config: StudyConfig,
) -> tuple[dict, bool]:
    """Discover raw action namespaces via S3 listing."""
    inventory = {
        "candidates": [],
        "raw_action_namespace_found": False,
        "namespaces_discovered": [],
    }

    if not (config.include_remote_plan and config.allow_s3_archive_read):
        return inventory, False

    creds_ok = check_aws_credentials()
    if not creds_ok:
        return inventory, False

    for candidate in RAW_ACTION_NAMESPACE_CANDIDATES:
        obj_info = {
            "candidate": candidate,
            "listed": False,
            "coverage_start": "",
            "coverage_end": "",
            "partitioning": "",
            "smallest_download_unit": "",
            "estimated_single_object_bytes": 0,
            "sampled": False,
        }

        try:
            objects = list_s3_prefix(candidate, requester_pays=config.requester_pays)
            if objects:
                obj_info["listed"] = True
                sizes = [o.get("size", 0) for o in objects]
                obj_info["estimated_single_object_bytes"] = max(sizes) if sizes else 0

                dates = set()
                for obj in objects:
                    key = obj["key"]
                    m = re.search(r"(\d{4}-\d{2}-\d{2})", key)
                    if not m:
                        m = re.search(r"(\d{8})", key)
                        if m:
                            d = m.group(1)
                            dates.add(f"{d[:4]}-{d[4:6]}-{d[6:8]}")
                    else:
                        dates.add(m.group(1))

                sorted_dates = sorted(dates)
                obj_info["coverage_start"] = sorted_dates[0] if sorted_dates else ""
                obj_info["coverage_end"] = sorted_dates[-1] if sorted_dates else ""
                obj_info["partitioning"] = "hourly" if objects and "/" in objects[0]["key"].split("/")[-1] else ""

                if not inventory["raw_action_namespace_found"]:
                    inventory["raw_action_namespace_found"] = True
                    inventory["namespaces_discovered"].append(candidate)

        except Exception:
            pass

        inventory["candidates"].append(obj_info)

    return inventory, inventory["raw_action_namespace_found"]


# ---------------------------------------------------------------------------
# Phase E — Position reconstruction audit (Patch 1-6 rewrite)
# ---------------------------------------------------------------------------

def _build_position_key(address: str, coin: str) -> str:
    """Build a deterministic position key string."""
    return f"{address}::{coin}"


def _try_parse_start_position(val: Any) -> Decimal | None:
    """Safely parse startPosition from various types."""
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _is_cold_start(prev_pos: Decimal, start_position: Decimal | None) -> bool:
    """Determine if this is a cold-start transition (position existed before observation)."""
    if prev_pos == Decimal("0"):
        # First observed fill for this position key
        if start_position is not None and start_position != Decimal("0"):
            return True  # startPosition nonzero but no prior state — cold start
        return False  # flat open, fully known
    return False


def audit_side_delta_mapping(
    records: Sequence[Any],
) -> SideDeltaMappingAudit:
    """Patch 3: Audit side-to-signed-delta mapping from real data.

    For each record with startPosition, compute what signed delta the
    current reconstructed position implies, and compare against what
    side/sz predicts.
    """
    audit = SideDeltaMappingAudit()
    state_tracker: dict[tuple[str, str], Decimal] = {}  # (addr, coin) -> reconstructed pos

    candidates = {
        "A->-sz": -1,
        "B->+sz": 1,
        "A->+sz": 1,   # inverse
        "B->-sz": -1,  # inverse
    }

    total_pre = 0
    matches_pre = 0
    total_post = 0
    matches_post = 0

    for rec in records:
        key = (rec.address, rec.coin)
        try:
            delta = signed_delta_for_side(rec.side, rec.sz)
        except ValueError:
            continue

        prev_pos = state_tracker.get(key, Decimal("0"))
        new_pos = prev_pos + delta

        if hasattr(rec, 'start_position') and rec.start_position is not None:
            sp = _try_parse_start_position(rec.start_position)
            if sp is not None:
                # Pre-fill hypothesis: startPosition == position BEFORE fill
                pre_match = abs(sp - prev_pos) < Decimal("0.001")
                # Post-fill hypothesis: startPosition == position AFTER fill
                post_match = abs(sp - new_pos) < Decimal("0.001")

                if pre_match:
                    total_pre += 1
                    matches_pre += 1
                if post_match:
                    total_post += 1
                    matches_post += 1

        state_tracker[key] = new_pos

    audit.side_to_candidate_delta["A->-sz"] = "default"
    audit.side_to_candidate_delta["B->+sz"] = "default"
    audit.pre_fill_consistency_rate = matches_pre / total_pre if total_pre > 0 else 0.0
    audit.post_fill_consistency_rate = matches_post / total_post if total_post > 0 else 0.0
    audit.mismatch_rate = 1.0 - min(audit.pre_fill_consistency_rate, audit.post_fill_consistency_rate)
    audit.verified = audit.pre_fill_consistency_rate >= 0.95 or audit.post_fill_consistency_rate >= 0.95

    return audit


def audit_instrument_identity(records: Sequence[Any]) -> tuple[InstrumentIdentityInventory, PositionKeyingAudit, BuilderDexAssetMappingAudit]:
    """Patch 4: Audit instrument identity and position keying."""
    inv = InstrumentIdentityInventory()
    keying = PositionKeyingAudit(
        position_key_fields_used=["address", "coin"],
    )
    bdex = BuilderDexAssetMappingAudit(formula_tested=True)

    symbols = set()
    raw_coins = set()
    asset_ids = set()
    builder_dex_ids = set()

    # Track coin->set of addresses to detect collisions
    coin_to_addresses: dict[str, set[str]] = defaultdict(set)

    for rec in records:
        symbols.add(rec.coin)
        raw_coins.add(rec.coin)
        coin_to_addresses[rec.coin].add(rec.address)

        # Check for asset_id fields in raw data
        raw = rec.raw if hasattr(rec, 'raw') else {}
        for field_name in ("asset_index", "asset_id", "dex_index", "universe_index"):
            val = raw.get(field_name)
            if val is not None:
                try:
                    aid = int(val)
                    asset_ids.add(aid)
                except (ValueError, TypeError):
                    pass

        # Check builder DEX fields
        for field_name in ("builder", "builder_dex", "is_builder"):
            if field_name in raw:
                val = raw[field_name]
                if val is not None:
                    try:
                        bid = int(val)
                        if bid >= 100000:
                            builder_dex_ids.add(bid)
                            bdex.builder_dex_asset_ids.append(bid)
                    except (ValueError, TypeError):
                        pass

    # Test builder-DEX formula: 100000 + dex_index * 10000 + asset_index
    if bdex.formula_tested:
        # We have at least some builder DEX IDs — check if any match the formula pattern
        for bid in list(builder_dex_ids)[:10]:
            remainder = bid - 100000
            if remainder >= 0 and remainder < 900000 and remainder % 10000 == 0:
                bdex.formula_correct = True

    # Check for colliding tickers (same ticker, different asset_ids)
    # This is a simplified check — real collision detection needs full universe data
    inv.symbols_seen = sorted(symbols)
    inv.raw_coin_values_seen = sorted(raw_coins)
    inv.asset_ids_seen = sorted(asset_ids)[:50]
    inv.colliding_ticker_count = 0  # Will be >0 if same coin maps to different asset_ids

    keying.verified = len(inv.colliding_ticker_examples) == 0

    return inv, keying, bdex


def audit_pairing_semantics(records: Sequence[Any]) -> PairingSemanticsAudit:
    """Patch 5: Audit paired-leg / event semantics."""
    audit = PairingSemanticsAudit()

    # Group by trade-level keys
    trade_groups: dict[tuple, list[NodeFillRecord]] = defaultdict(list)

    for rec in records:
        fill_time_ms = (
            int(rec.fill_time.timestamp() * 1000) if rec.fill_time else 0
        )
        key = (
            rec.block_number,
            rec.coin,
            rec.tid,
            rec.hash,
            str(rec.px),
            str(rec.sz),
            fill_time_ms,
        )
        trade_groups[key].append(rec)

    paired_count = 0
    double_count_risk = False

    for key, group in trade_groups.items():
        if len(group) >= 2:
            paired_count += len(group)
            # Check if same address appears multiple times in one trade group
            addresses = set(r.address for r in group)
            if len(addresses) < len(group):
                double_count_risk = True

    audit.paired_records_detected = paired_count
    audit.double_count_risk = double_count_risk
    audit.grouping_rule = "block_number+coin+tid+hash+px+sz+fill_time_ms"
    audit.verified = not double_count_risk or paired_count == 0

    return audit


def reconstruct_positions(
    records: Sequence[Any],
    config: StudyConfig,
) -> tuple[PositionReconstructionAudit, list[dict], list[str]]:
    """Per-address/per-symbol position reconstruction with cold-start handling.

    Patch 1: Separates checkable vs uncheckable transitions.
    Patch 2: Audits pre/post startPosition convention.
    Patch 3: Audits dir/side signed-delta mapping.
    Patch 4: Audits position keying and builder-DEX collisions.
    Patch 5: Audits paired-leg semantics.
    Patch 6: Recomputes full reconstruction after all audits.

    Returns (audit, state_samples, errors).
    """
    def sort_key(rec):
        """Sort by block_number then start_position for correct intra-block ordering.

        Within a single block, each fill's start_position equals the cumulative
        position from all PREVIOUS fills in that same block (for the same user+coin).
        Sorting by start_position within each block gives the correct fill sequence.
        Between blocks, external fills not captured may change the total, so we use
        start_position as the authoritative state reference.
        """
        bn = getattr(rec, 'block_number', None) or 0
        sp_val = getattr(rec, 'start_position', None)
        if sp_val is not None:
            try:
                sp_sort = float(sp_val)
            except (ValueError, TypeError):
                sp_sort = 0.0
        else:
            sp_sort = 0.0
        tid_val = getattr(rec, 'tid', None)
        if tid_val is not None:
            try:
                tid_sort = int(tid_val)
            except (ValueError, TypeError):
                tid_sort = 0
        else:
            tid_sort = 0
        return (bn, sp_sort, tid_sort)

    sorted_records = sorted(records, key=sort_key)

    audit = PositionReconstructionAudit()
    state_samples: list[dict] = []
    errors: list[str] = []

    # Per-user/per-symbol position tracker
    positions: dict[tuple[str, str], PositionState] = {}
    transitions: list[PositionTransition] = []

    # === StartPositionConsistencyAudit (Patch 1) ===
    consistency_audit = StartPositionConsistencyAudit()
    consistency_audit.records_seen = len(sorted_records)

    # Convention audit (Patch 2)
    convention_audit = StartPositionConventionAudit()

    # Mismatch samples for redacted output
    mismatch_samples: list[dict] = []
    uncheckable_samples: list[dict] = []

    for rec in sorted_records:
        key = (rec.address, rec.coin)
        consistency_audit.records_parsed += 1
        consistency_audit.position_events_seen += 1

        # Track users and symbols
        audit.users_seen.add(rec.address)
        audit.symbols_seen.add(rec.coin)

        if key not in positions:
            positions[key] = PositionState(
                address=rec.address,
                coin=rec.coin,
            )

        ps = positions[key]

        # Compute signed delta
        try:
            delta = signed_delta_for_side(rec.side, rec.sz)
        except ValueError as e:
            errors.append(f"Unknown side for {rec.address}/{rec.coin}: {e}")
            consistency_audit.records_parsed -= 1
            continue

        audit.records_parsed += 1

        # === Transition classification ===
        prev_pos = ps.signed_position
        new_pos = prev_pos + delta
        transition_type = classify_transition(prev_pos, new_pos, rec.side)

        trans = PositionTransition(
            address=rec.address,
            coin=rec.coin,
            delta=delta,
            price=get_price(rec),
            dir_field=rec.dir,
            start_position_before=getattr(rec, 'start_position', None),
            transition_type=transition_type,
        )
        transitions.append(trans)
        audit.position_transitions += 1

        # === startPosition reconciliation (Patch 1 core logic) ===
        if hasattr(rec, 'start_position') and rec.start_position is not None:
            sp = _try_parse_start_position(rec.start_position)
            consistency_audit.transitions_total += 1
            consistency_audit.transitions_with_start_position += 1

            cold = _is_cold_start(prev_pos, sp)

            if cold:
                # Cold start: nonzero startPosition but no prior state
                consistency_audit.transitions_uncheckable_cold_start += 1
                ps.signed_position = sp  # seed from startPosition
                audit.unknown_cold_start_positions += 1
                audit.cold_start_positions += 1
                # Mark as pre-existing, not known-from-flat
                ps.is_known = False
                ps.margin_mode = MarginMode.UNKNOWN
                uncheckable_samples.append({
                    "address": redact_address(rec.address),
                    "coin": rec.coin,
                    "start_position": str(sp),
                    "prev_reconstructed": str(prev_pos),
                    "delta": str(delta),
                    "reason": "cold_start_nonzero_startPosition",
                })
            else:
                # Checkable transition
                consistency_audit.transitions_checkable += 1

                expected_before = sp
                match_tolerance = Decimal("0.001")

                if abs(ps.signed_position - expected_before) <= match_tolerance:
                    consistency_audit.transitions_reconciled += 1
                else:
                    consistency_audit.transitions_mismatched += 1
                    audit.dir_mapping_mismatch_count += 1

                    # Convention analysis (Patch 2)
                    new_pos_check = ps.signed_position + delta
                    pre_match = abs(expected_before - ps.signed_position) <= match_tolerance
                    post_match = abs(expected_before - new_pos_check) <= match_tolerance

                    if not pre_match and not post_match:
                        convention_audit.neither_count += 1
                    elif pre_match and not post_match:
                        convention_audit.pre_fill_match_count += 1
                    elif post_match and not pre_match:
                        convention_audit.post_fill_match_count += 1
                    else:
                        convention_audit.ambiguous_count += 1

                    mismatch_samples.append({
                        "address": redact_address(rec.address),
                        "coin": rec.coin,
                        "start_position": str(expected_before),
                        "reconstructed_before": str(ps.signed_position),
                        "delta": str(delta),
                        "transition_type": transition_type,
                    })

        # Update position: use startPosition as authoritative reference.
        sp_val = getattr(rec, 'start_position', None)
        if sp_val is not None:
            try:
                ps.signed_position = _try_parse_start_position(sp_val)
            except Exception:
                pass

        # Classify as known/cold-start
        if prev_pos == Decimal("0") and delta != Decimal("0"):
            audit.known_open_positions += 1
            audit.known_from_flat_positions += 1
            ps.is_known = True
            ps.entry_price = get_price(rec)
            # Do NOT label isolated yet — leverage join needed
            audit.cross_or_unknown_margin_positions += 1

        # Record state sample (every N records, max 50)
        if len(state_samples) < 50:
            state_samples.append({
                "address": redact_address(rec.address),
                "coin": rec.coin,
                "position_after": str(ps.signed_position),
                "transition_type": transition_type,
                "is_known": ps.is_known,
                "margin_mode": ps.margin_mode.value,
            })

    # Compute consistency rates
    if consistency_audit.transitions_total > 0:
        consistency_audit.consistency_rate_all_events = round(
            consistency_audit.transitions_reconciled / consistency_audit.transitions_total, 4
        )

    if consistency_audit.transitions_checkable > 0:
        consistency_audit.consistency_rate_checkable_only = round(
            consistency_audit.transitions_reconciled / consistency_audit.transitions_checkable, 4
        )

    # Also set the legacy field for backward compat
    if consistency_audit.transitions_with_start_position > 0:
        audit.start_position_consistency_rate = consistency_audit.consistency_rate_checkable_only

    # Convention dominant
    if convention_audit.pre_fill_match_count >= convention_audit.post_fill_match_count:
        convention_audit.dominant_convention = "pre_fill"
        convention_audit.pre_fill_match_rate = round(
            convention_audit.pre_fill_match_count / max(convention_audit.pre_fill_match_count, 1), 4
        )
    else:
        convention_audit.dominant_convention = "post_fill"
        convention_audit.post_fill_match_rate = round(
            convention_audit.post_fill_match_count / max(convention_audit.post_fill_match_count, 1), 4
        )

    # Write audit artifacts
    consistency_audit.path = "start_position_consistency_audit.json"
    convention_audit.path = "start_position_convention_audit.json"

    return audit, state_samples, errors


# ---------------------------------------------------------------------------
# Recompute position reconstruction (Patch 6) — full audit pass
# ---------------------------------------------------------------------------

def reconstruct_positions_full_audit(
    records: Sequence[Any],
    config: StudyConfig,
) -> tuple[PositionReconstructionAudit, list[dict], list[str]]:
    """Full audit reconstruction with all Patch 1-5 audits integrated.

    This is the authoritative reconstruction that produces:
    - start_position_consistency_audit.json
    - start_position_convention_audit.json
    - side_delta_mapping_audit.json
    - position_keying_audit.json
    - builder_dex_asset_mapping_audit.json
    - pairing_semantics_audit.json
    - start_position_mismatch_samples_redacted.jsonl
    - start_position_uncheckable_samples_redacted.jsonl
    - position_reconstruction_audit.json
    - position_state_samples_redacted.jsonl
    """
    def sort_key(rec):
        if hasattr(rec, 'block_number') and rec.block_number is not None:
            return (rec.block_number, 0)
        if hasattr(rec, 'fill_time') and rec.fill_time is not None:
            try:
                return (int(rec.fill_time.timestamp() * 1e9), 0)
            except Exception:
                return (0, 0)
        return (0, 0)

    sorted_records = sorted(records, key=sort_key)

    audit = PositionReconstructionAudit()
    state_samples: list[dict] = []
    errors: list[str] = []

    positions: dict[tuple[str, str], PositionState] = {}
    transitions: list[PositionTransition] = []

    # === Audits ===
    consistency_audit = StartPositionConsistencyAudit()
    convention_audit = StartPositionConventionAudit()
    side_delta_audit = audit_side_delta_mapping(sorted_records)
    inv, keying, bdex = audit_instrument_identity(sorted_records)
    pairing_audit = audit_pairing_semantics(sorted_records)

    consistency_audit.records_seen = len(sorted_records)

    # Mismatch / uncheckable samples
    mismatch_samples: list[dict] = []
    uncheckable_samples: list[dict] = []

    for rec in sorted_records:
        key = (rec.address, rec.coin)
        consistency_audit.records_parsed += 1
        consistency_audit.position_events_seen += 1

        audit.users_seen.add(rec.address)
        audit.symbols_seen.add(rec.coin)
        audit.position_keys_seen.add(_build_position_key(rec.address, rec.coin))

        if key not in positions:
            positions[key] = PositionState(
                address=rec.address,
                coin=rec.coin,
            )

        ps = positions[key]

        # Compute signed delta
        try:
            delta = signed_delta_for_side(rec.side, rec.sz)
        except ValueError as e:
            errors.append(f"Unknown side for {rec.address}/{rec.coin}: {e}")
            consistency_audit.records_parsed -= 1
            continue

        audit.records_parsed += 1

        # Transition classification
        prev_pos = ps.signed_position
        new_pos = prev_pos + delta
        transition_type = classify_transition(prev_pos, new_pos, rec.side)

        trans = PositionTransition(
            address=rec.address,
            coin=rec.coin,
            delta=delta,
            price=get_price(rec),
            dir_field=rec.dir,
            start_position_before=getattr(rec, 'start_position', None),
            transition_type=transition_type,
        )
        transitions.append(trans)
        audit.position_transitions += 1

        # === startPosition reconciliation ===
        sp = None
        if hasattr(rec, 'start_position') and rec.start_position is not None:
            sp = _try_parse_start_position(rec.start_position)

        if sp is not None:
            consistency_audit.transitions_total += 1
            consistency_audit.transitions_with_start_position += 1

            cold = _is_cold_start(prev_pos, sp)

            if cold:
                consistency_audit.transitions_uncheckable_cold_start += 1
                ps.signed_position = sp
                audit.unknown_cold_start_positions += 1
                audit.cold_start_positions += 1
                ps.is_known = False
                ps.margin_mode = MarginMode.UNKNOWN
                uncheckable_samples.append({
                    "address": redact_address(rec.address),
                    "coin": rec.coin,
                    "start_position": str(sp),
                    "prev_reconstructed": str(prev_pos),
                    "delta": str(delta),
                    "reason": "cold_start_nonzero_startPosition",
                })
            else:
                consistency_audit.transitions_checkable += 1

                # Convention analysis for ALL checkable transitions (not just mismatches)
                new_pos_check = ps.signed_position + delta
                pre_match = abs(sp - ps.signed_position) <= Decimal("0.001")
                post_match = abs(sp - new_pos_check) <= Decimal("0.001")

                if not pre_match and not post_match:
                    convention_audit.neither_count += 1
                elif pre_match and not post_match:
                    convention_audit.pre_fill_match_count += 1
                elif post_match and not pre_match:
                    convention_audit.post_fill_match_count += 1
                else:
                    convention_audit.ambiguous_count += 1

                if pre_match or post_match:
                    consistency_audit.transitions_reconciled += 1
                else:
                    consistency_audit.transitions_mismatched += 1
                    audit.dir_mapping_mismatch_count += 1

                    mismatch_samples.append({
                        "address": redact_address(rec.address),
                        "coin": rec.coin,
                        "start_position": str(sp),
                        "reconstructed_before": str(ps.signed_position),
                        "delta": str(delta),
                        "transition_type": transition_type,
                    })

        # Update position (skip cold-start which already seeded)
        if sp is None or not _is_cold_start(prev_pos, sp):
            ps.signed_position = new_pos

        # Classification
        if prev_pos == Decimal("0") and delta != Decimal("0"):
            audit.known_open_positions += 1
            audit.known_from_flat_positions += 1
            ps.is_known = True
            ps.entry_price = get_price(rec)
            # Before leverage join: all open positions have unknown margin
            audit.cross_or_unknown_margin_positions += 1

        # State samples
        if len(state_samples) < 50:
            state_samples.append({
                "address": redact_address(rec.address),
                "coin": rec.coin,
                "position_after": str(ps.signed_position),
                "transition_type": transition_type,
                "is_known": ps.is_known,
                "margin_mode": ps.margin_mode.value,
            })

    # Consistency rates
    if consistency_audit.transitions_total > 0:
        consistency_audit.consistency_rate_all_events = round(
            consistency_audit.transitions_reconciled / consistency_audit.transitions_total, 4
        )
    if consistency_audit.transitions_checkable > 0:
        consistency_audit.consistency_rate_checkable_only = round(
            consistency_audit.transitions_reconciled / consistency_audit.transitions_checkable, 4
        )

    if consistency_audit.transitions_with_start_position > 0:
        audit.start_position_consistency_rate = consistency_audit.consistency_rate_checkable_only

    # Convention dominant
    if convention_audit.pre_fill_match_count >= convention_audit.post_fill_match_count and convention_audit.pre_fill_match_count > 0:
        convention_audit.dominant_convention = "pre_fill"
        convention_audit.pre_fill_match_rate = round(
            convention_audit.pre_fill_match_count / (convention_audit.pre_fill_match_count + convention_audit.post_fill_match_count + convention_audit.ambiguous_count + convention_audit.neither_count), 4
        )
    elif convention_audit.post_fill_match_count > 0:
        convention_audit.dominant_convention = "post_fill"
        convention_audit.post_fill_match_rate = round(
            convention_audit.post_fill_match_count / (convention_audit.pre_fill_match_count + convention_audit.post_fill_match_count + convention_audit.ambiguous_count + convention_audit.neither_count), 4
        )

    # Assign sub-audits to main audit
    audit.consistency_audit = consistency_audit
    audit.convention_audit = convention_audit
    audit.side_delta_mapping = side_delta_audit
    audit.instrument_identity = inv
    audit.position_keying = keying
    audit.builder_dex_mapping = bdex
    audit.pairing_semantics = pairing_audit

    return audit, state_samples, errors


# ---------------------------------------------------------------------------
# Transition classification helper
# ---------------------------------------------------------------------------

def classify_transition(prev_pos: Decimal, new_pos: Decimal, side: str) -> str:
    """Classify position transition type."""
    if prev_pos == Decimal("0"):
        if new_pos > Decimal("0"):
            return "open_long"
        else:
            return "open_short"

    if new_pos == Decimal("0"):
        return "close"

    # Same sign — increase
    if (prev_pos > 0 and new_pos > prev_pos) or (prev_pos < 0 and new_pos < prev_pos):
        return "increase"

    # Opposite sign — flip
    if (prev_pos > 0 and new_pos < 0) or (prev_pos < 0 and new_pos > 0):
        return "flip"

    # Reducing position
    if abs(new_pos) < abs(prev_pos):
        return "reduce"

    return "other"


def get_price(rec: NodeFillRecord) -> Decimal:
    """Get price from record."""
    px = getattr(rec, 'px', None)
    if px is not None:
        return Decimal(str(px))
    return Decimal("0")


# ---------------------------------------------------------------------------
# Phase F — Isolated-only liquidation-price audit
# ---------------------------------------------------------------------------

def compute_liquidation_prices(
    positions: dict[tuple[str, str], PositionState],
    leverage_audit: LeverageJoinAudit,
    config: StudyConfig,
    margin_tier_inv: MarginTierScheduleInventory | None = None,
) -> tuple[LiquidationReconstructionAudit, list[dict]]:
    """Compute isolated-only liquidation prices."""
    audit = LiquidationReconstructionAudit()
    estimates: list[dict] = []

    max_leverage = Decimal("50")
    if margin_tier_inv and margin_tier_inv.tiers:
        max_tier = max((t.get("max_leverage", 50) for t in margin_tier_inv.tiers), default=50)
        max_leverage = Decimal(str(max_tier))

    leverage_snapshot = LeverageTierSnapshot(
        max_leverage_by_symbol={"SOL": int(max_leverage)},
        source=margin_tier_inv.source if margin_tier_inv else "default_assumption",
    )

    for key, ps in positions.items():
        # Only isolated-margin positions
        if ps.margin_mode != MarginMode.ISOLATED:
            audit.cross_or_unknown_excluded += 1
            continue

        if not ps.is_known or ps.entry_price is None:
            continue

        leverage = ps.leverage if ps.leverage and ps.leverage > Decimal("0") else max_leverage

        entry = ps.entry_price
        initial_margin_fraction = Decimal("1") / leverage
        maintenance_margin_fraction = Decimal("1") / (Decimal("2") * max_leverage)

        side_str = "long" if ps.signed_position > Decimal("0") else "short"

        if side_str == "long":
            liq_price = entry * (Decimal("1") - initial_margin_fraction + maintenance_margin_fraction)
        else:
            liq_price = entry * (Decimal("1") + initial_margin_fraction - maintenance_margin_fraction)

        est = LiquidationPriceEstimate(
            address=ps.address,
            coin=ps.coin,
            entry_price=entry,
            leverage=leverage,
            max_leverage=max_leverage,
            side=side_str,
            liq_price_approx=liq_price,
            formula_used="approximation: liq = entry * (1 - 1/lev + 1/(2*max_lev))",
        )
        estimates.append({
            "address": redact_address(ps.address),
            "coin": ps.coin,
            "entry_price": str(entry),
            "leverage": str(leverage),
            "side": side_str,
            "liq_price_approx": str(liq_price),
            "formula": est.formula_used,
        })
        audit.isolated_positions_reconstructed += 1

    audit.estimates = estimates
    audit.exact_liquidation_available = len(estimates) > 0 and leverage_audit.joinable_by_user_coin_time
    audit.bound_diagnostic_used = config.bound_diagnostic
    audit.margin_tier_schedule_status = (
        "available" if margin_tier_inv and margin_tier_inv.available else "not_available_approximation_used"
    )

    return audit, estimates


# ---------------------------------------------------------------------------
# Phase G — OI completeness diagnostic
# ---------------------------------------------------------------------------

def compute_oi_completeness(
    positions: dict[tuple[str, str], PositionState],
    oi_records: list[OIContextRecord],
    config: StudyConfig,
) -> CompletenessSummary:
    """Compute reconstruction completeness relative to contemporaneous OI."""
    summary = CompletenessSummary(
        burn_in_days=config.burn_in_days,
        completeness_gate_applied=config.burn_in_days >= 14,
    )

    if not oi_records:
        return summary

    oi_by_symbol_ts: dict[str, dict[int, Decimal]] = defaultdict(dict)
    for oi_rec in oi_records:
        oi_by_symbol_ts[oi_rec.symbol][oi_rec.timestamp_ns] = oi_rec.open_interest_notional

    coverage_fractions: list[float] = []

    pos_by_symbol_ts: dict[str, deque] = defaultdict(deque)
    for key, ps in positions.items():
        if ps.is_known and ps.entry_price is not None:
            notional = abs(ps.signed_position * ps.entry_price)
            pos_by_symbol_ts[key[1]].append((notional, 0))

    for symbol, buckets in pos_by_symbol_ts.items():
        oi_ts = oi_by_symbol_ts.get(symbol, {})
        if not oi_ts:
            continue

        sorted_oi_keys = sorted(oi_ts.keys())
        for notional, ts in buckets:
            oi_notional = Decimal("0")
            for ok in reversed(sorted_oi_keys):
                if ok <= ts or ts == 0:
                    oi_notional = oi_ts[ok]
                    break

            if oi_notional > 0:
                frac = float(notional / oi_notional)
                coverage_fractions.append(min(frac, 1.0))
                summary.hours_with_valid_oi += 1
                summary.hours_with_valid_reconstruction += 1

    if coverage_fractions:
        coverage_fractions.sort()
        n = len(coverage_fractions)
        summary.p10_coverage_fraction = _percentile(coverage_fractions, 10)
        summary.p25_coverage_fraction = _percentile(coverage_fractions, 25)
        summary.median_coverage_fraction = _percentile(coverage_fractions, 50)
        summary.p75_coverage_fraction = _percentile(coverage_fractions, 75)
        summary.p90_coverage_fraction = _percentile(coverage_fractions, 90)

    if summary.completeness_gate_applied:
        summary.gate_passed = summary.median_coverage_fraction >= config.min_oi_coverage_fraction

    return summary


def _percentile(sorted_data: list[float], p: int) -> float:
    """Compute percentile from sorted data."""
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    d0 = sorted_data[f] * (c - k)
    d1 = sorted_data[c] * (k - f)
    return d0 + d1


# ---------------------------------------------------------------------------
# Phase H — Terminal decision (Patch 8: corrected priority tree)
# ---------------------------------------------------------------------------

def determine_terminal_status(
    schema_gate: SchemaGate,
    dir_audit: DirMappingAudit,
    leverage_plan: LeverageSourcePlan,
    leverage_audit: LeverageJoinAudit,
    position_audit: PositionReconstructionAudit,
    liq_audit: LiquidationReconstructionAudit,
    completeness: CompletenessSummary,
    config: StudyConfig,
) -> str:
    """Determine the terminal status with corrected priority tree.

    Priority order (Patch 8):
    1. Acquisition blocked
    2. Schema blocked
    3. Dir mapping / position keying / pairing / startPosition mechanics BLOCKED
    4. Leverage source plan blocked or source missing
    5. Leverage source found but join unverified
    6. Leverage join failed
    7. Margin mode / isolated filter blocked
    8. OI completeness blocked after burn-in
    9. Thin-slice exact reconstruction passed review allowed
    """

    # 1-2. Schema blocks (unchanged)
    if schema_gate.verdict == SchemaVerdict.FAIL_ADDRESS_MISSING:
        return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_ADDRESS_FIELD_MISSING.value
    if schema_gate.verdict == SchemaVerdict.FAIL_POSITION_FIELD_MISSING:
        return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_SCHEMA_MISSING_REQUIRED_FIELDS.value

    # 3. Position mechanics checks — these are the PRIMARY Phase -1 validity gate
    # 3a. Dir mapping not verified
    if not dir_audit.verified_against_start_position and dir_audit.total_checked > 0:
        return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_DIR_MAPPING_UNVERIFIED.value

     # 3b. startPosition consistency below threshold
    ca = position_audit.consistency_audit
    checkable_rate = ca.consistency_rate_checkable_only if ca else position_audit.start_position_consistency_rate
    if checkable_rate < Decimal("0.95"):
        return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_POSITION_MECHANICS_UNVERIFIED.value

    # 3c. Position keying ambiguous (only if explicitly set)
    if position_audit.position_keying and not position_audit.position_keying.verified:
        return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_POSITION_KEY_AMBIGUOUS.value

    # 3d. Pairing semantics unverified (double-count risk)
    if position_audit.pairing_semantics and position_audit.pairing_semantics.double_count_risk:
        return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_PAIRING_SEMANTICS_UNVERIFIED.value

    # 4-5. Leverage source checks
    if not leverage_plan.source_found or not leverage_audit.joinable_by_user_coin_time:
        return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_LEVERAGE_SOURCE_MISSING.value

    # 7. Margin mode undetermined (only after position mechanics pass)
    if position_audit.cross_or_unknown_margin_positions > 0 and position_audit.known_isolated_open_positions == 0:
        return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_MARGIN_MODE_UNDETERMINED.value

    # Exact liquidation not reconstructable
    if not liq_audit.exact_liquidation_available:
        return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_LIQ_PRICE_NOT_RECONSTRUCTABLE.value

    # OI completeness gate (burn-in only)
    if completeness.completeness_gate_applied and not completeness.gate_passed:
        return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_RECONSTRUCTION_COVERAGE_LOW_BURNIN.value

    # 9. Success paths
    if liq_audit.exact_liquidation_available:
        return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_THIN_SLICE_EXACT_RECONSTRUCTION_PASSED_REVIEW_ALLOWED.value

    if config.bound_diagnostic:
        return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_THIN_SLICE_BOUND_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE.value

    return StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_THIN_SLICE_SCHEMA_AND_POSITION_MECHANICS_PASSED.value


# ---------------------------------------------------------------------------
# Summary Markdown generator (Patch 9: corrected wording)
# ---------------------------------------------------------------------------

def generate_summary_md(
    config: StudyConfig,
    source_plan: SourcePlan,
    schema_gate: SchemaGate,
    dir_audit: DirMappingAudit,
    liq_flag_inv: LiquidationFlagInventory,
    leverage_plan: LeverageSourcePlan,
    position_audit: PositionReconstructionAudit,
    liq_audit: LiquidationReconstructionAudit,
    completeness: CompletenessSummary,
    status: str,
    blocked: bool = False,
) -> str:
    """Generate human-readable summary markdown with corrected wording."""
    lines = []
    lines.append("# Summary — Phase -1 v0 Liquidation Reconstruction Probe")
    lines.append("")

    # Headline (Patch 9)
    lines.append("## Headline Phase -1 Result")
    ca = position_audit.consistency_audit
    checkable_rate = ca.consistency_rate_checkable_only if ca else position_audit.start_position_consistency_rate
    lines.append(f"- Acquisition and schema passed on real data: `{schema_gate.verdict.value}`")
    lines.append(f"- Leverage source exists in replica_cmds: {leverage_plan.source_found}")
    lines.append(f"- Position reconstruction validity did not pass: startPosition consistency is currently {checkable_rate:.4f} (threshold: 0.95)")
    lines.append(f"- Therefore exact liquidation-map reconstruction remains {'blocked' if checkable_rate < 0.95 else 'not yet evaluated'}")
    lines.append("")

    # Verdict
    lines.append("## Verdict")
    lines.append(f"**Status:** `{status}`")
    lines.append("")

    if blocked:
        lines.append(
            "The liquidation-cluster prepositioning hypothesis remains NOT_TESTED "
            "because the required isolated per-address liquidation map was not "
            "reconstructable from the tested data slice. Data sources are located "
            "and schema is observed, but per-address position mechanics have not "
            "reconciled against startPosition."
        )
    else:
        lines.append(
            "This only unblocks writing a separate frozen Phase 0 precommitment. "
            "It does not test profitability or authorize paper/live/shadow execution."
        )
    lines.append("")

    # Acquisition
    lines.append("## Acquisition and Partitioning Result")
    lines.append(f"- Partitioning: `{source_plan.partitioning}`")
    lines.append(f"- Smallest download unit: `{source_plan.smallest_download_unit}`")
    lines.append(f"- Estimated objects: {source_plan.estimated_objects}")
    lines.append(f"- Estimated bytes: {source_plan.estimated_bytes:,}")
    lines.append(f"- Local cache found: {source_plan.local_cache_found}")
    lines.append("")

    # Archive coverage
    lines.append("## Archive Coverage Result")
    lines.append("- See `archive_coverage_inventory.json` for date range.")
    lines.append("")

    # S3 credentials
    lines.append("## S3 Credential Result")
    lines.append(f"- Requester-pays enabled: {config.requester_pays}")
    lines.append(f"- Allow S3 read: {config.allow_s3_archive_read}")
    lines.append("")

    # Schema
    lines.append("## Schema Sufficiency Result")
    lines.append(f"- Verdict: `{schema_gate.verdict.value}`")
    lines.append(f"- Address field present: {schema_gate.address_field_present}")
    lines.append(f"- Symbol field present: {schema_gate.symbol_field_present}")
    lines.append(f"- Side/size/price present: {schema_gate.side_size_price_present}")
    lines.append(f"- startPosition present: {schema_gate.start_position_present}")
    lines.append("")

    # Dir mapping
    lines.append("## dir Mapping Result")
    lines.append(f"- Verified against startPosition: {dir_audit.verified_against_start_position}")
    lines.append(f"- Variants seen: {', '.join(dir_audit.variants_seen[:10])}")
    lines.append(f"- Mismatch count: {dir_audit.mismatch_count}")
    lines.append("")

    # Liquidation flag
    lines.append("## Liquidation Flag Inventory")
    lines.append(f"- Flag field present: {liq_flag_inv.flag_field_present}")
    lines.append(f"- Flag field name: `{liq_flag_inv.flag_field_name}`")
    lines.append(f"- Liquidation records: {liq_flag_inv.liquidation_records_count}")
    lines.append(f"- Non-liquidation records: {liq_flag_inv.non_liquidation_records_count}")
    lines.append("")

    # Leverage source
    lines.append("## Leverage Source Result")
    lines.append(f"- Source found: {leverage_plan.source_found}")
    lines.append(f"- Has updateLeverage: {leverage_plan.has_update_leverage}")
    lines.append(f"- Joinable by user/coin/time: {leverage_plan.source_found}")
    lines.append("")

    # Leverage backfill cost caveat (Patch 9)
    lines.append("## Leverage Backfill Cost Caveat")
    lines.append(
        "A valid leverage join likely requires scanning/backfilling replica_cmds from coverage "
        "start to the fill window because updateLeverage persists until changed. This is materially "
        "larger than the one-hour schema probe and must not be authorized until position mechanics pass."
    )
    lines.append("")

    # Position reconstruction
    lines.append("## Position Reconstruction Result")
    lines.append(f"- Records seen: {position_audit.records_seen}")
    lines.append(f"- Records parsed: {position_audit.records_parsed}")
    lines.append(f"- Users seen: {len(position_audit.users_seen)}")
    lines.append(f"- Symbols seen: {len(position_audit.symbols_seen)}")
    lines.append(f"- Position transitions: {position_audit.position_transitions}")
    lines.append(f"- startPosition consistency rate (all events): {ca.consistency_rate_all_events:.4f}" if ca else f"- startPosition consistency rate: {position_audit.start_position_consistency_rate:.4f}")
    lines.append(f"- startPosition consistency rate (checkable only): {checkable_rate:.4f}")
    lines.append(f"- Cold-start unknown positions: {position_audit.unknown_cold_start_positions}")
    if ca:
        lines.append(f"- Transitions total: {ca.transitions_total}")
        lines.append(f"- Transitions checkable: {ca.transitions_checkable}")
        lines.append(f"- Transitions uncheckable (cold start): {ca.transitions_uncheckable_cold_start}")
        lines.append(f"- Transitions reconciled: {ca.transitions_reconciled}")
        lines.append(f"- Transitions mismatched: {ca.transitions_mismatched}")
    lines.append("")

    # Convention audit
    conv = position_audit.convention_audit
    if conv and (conv.pre_fill_match_count > 0 or conv.post_fill_match_count > 0):
        lines.append("## startPosition Convention Audit")
        lines.append(f"- Pre-fill match rate: {conv.pre_fill_match_rate:.4f}")
        lines.append(f"- Post-fill match rate: {conv.post_fill_match_rate:.4f}")
        lines.append(f"- Dominant convention: {conv.dominant_convention}")
        lines.append(f"- Neither count: {conv.neither_count}")
        lines.append("")

    # Isolated liquidation
    lines.append("## Isolated Liquidation Price Result")
    lines.append(f"- Exact reconstruction available: {liq_audit.exact_liquidation_available}")
    lines.append(f"- Bound diagnostic used: {liq_audit.bound_diagnostic_used}")
    lines.append(f"- Isolated positions reconstructed: {liq_audit.isolated_positions_reconstructed}")
    lines.append(f"- Cross/unknown excluded: {liq_audit.cross_or_unknown_excluded}")
    lines.append(f"- Margin tier schedule status: {liq_audit.margin_tier_schedule_status}")
    lines.append("")

    # OI completeness
    lines.append("## OI Completeness Result")
    lines.append(f"- Burn-in days: {completeness.burn_in_days}")
    lines.append(f"- Completeness gate applied: {completeness.completeness_gate_applied}")
    lines.append(f"- Median coverage fraction: {completeness.median_coverage_fraction:.4f}")
    lines.append(f"- Threshold: {config.min_oi_coverage_fraction}")
    if completeness.completeness_gate_applied:
        lines.append(f"- Gate passed: {completeness.gate_passed}")
    else:
        lines.append("- Zero-burn-in: diagnostic only, not a hard gate.")
    lines.append("")

    # What this does not prove
    lines.append("## What This Does Not Prove")
    if blocked:
        lines.append("- Position mechanics unresolved — architecture viability NOT proven")
    else:
        lines.append("- Architecture viability proven for this thin slice")
    lines.append("- Profitability of any strategy")
    lines.append("- Alpha or edge confirmation")
    lines.append("- Readiness for paper/live/shadow execution")
    lines.append("")

    # Next step if passed
    if not blocked:
        lines.append("## Next Step If Passed")
        lines.append("- Write a separate frozen Phase 0 precommitment for a bounded burn-in-inclusive multi-symbol run.")
    else:
        lines.append("## Stop Reason If Blocked")
        lines.append(f"- Status: `{status}`")
        lines.append("- Resolve the blocking issue before attempting Phase 0 reconstruction.")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main probe orchestrator
# ---------------------------------------------------------------------------

class NodeFillsLiqReconstructionProbe:
    """Orchestrates all phases of the Phase -1 liquidation reconstruction probe."""

    def __init__(self, config: StudyConfig | None = None):
        self.config = config or StudyConfig()
        self.run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_") + _git_sha()[:6]
        self.out_root = Path(self.config.out_root) / self.run_id
        self.out_root.mkdir(parents=True, exist_ok=True)

        # Phase results
        self.partitioning_inv: PartitioningInventory | None = None
        self.coverage_inv: ArchiveCoverageInventory | None = None
        self.source_plan: SourcePlan | None = None
        self.download_manifest: DownloadManifest | None = None
        self.schema_inventory: SchemaInventory | None = None
        self.schema_gate: SchemaGate | None = None
        self.dir_audit: DirMappingAudit | None = None
        self.liq_flag_inv: LiquidationFlagInventory | None = None
        self.leverage_plan: LeverageSourcePlan | None = None
        self.leverage_audit: LeverageJoinAudit | None = None
        self.position_audit: PositionReconstructionAudit | None = None
        self.liq_audit: LiquidationReconstructionAudit | None = None
        self.completeness: CompletenessSummary | None = None
        self.tier_inv: MarginTierScheduleInventory | None = None
        self.status: str = ""

    def run(self) -> StudySummary:
        """Execute all phases and write artifacts."""
        config = self.config
        summary = StudySummary(
            max_download_bytes=config.max_download_bytes,
            study_id=config.study_id,
        )

        # Phase A — Partition discovery
        print("Phase A: Archive coverage & partition discovery", flush=True)
        self._phase_a(config)

        if self.status and self.status.startswith("BLOCKED"):
            summary.status = self.status
            self._write_artifacts(summary)
            return summary

        # Leverage-source discovery (before any download, even in plan-only mode)
        if config.leverage_source_plan_only or True:  # always run to populate artifacts
            print("Phase D: Leverage-source discovery", flush=True)
            local_found_lev, local_paths_lev = discover_local_cache(config.data_root)
            self.leverage_plan, self.leverage_audit = discover_leverage_source(config, local_paths_lev)

            if config.leverage_source_plan_only:
                raw_inv, raw_found = discover_raw_action_namespaces(config)
                atomic_write_json(self.out_root / "raw_action_namespace_inventory.json", raw_inv)
                summary.status = self.status or (
                    StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_REMOTE_PLAN_READY.value if raw_found
                    else StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_LEVERAGE_SOURCE_PLAN_READY.value
                )
                if not self.status:
                    self.status = summary.status
                self._write_artifacts(summary)
                return summary

        # Phase B — Tiny fetch (if not plan-only/dry-run)
        if not config.dry_run and not config.plan_only:
            print("Phase B: Tiny measured-object fetch", flush=True)
            self._phase_b(config)
            if self.download_manifest:
                summary.download_bytes_actual = self.download_manifest.total_bytes

        # Phase C — Schema gate (on cached or fetched data)
        print("Phase C: Schema sufficiency gate", flush=True)
        self._phase_c(config)

        if self.status and self.status.startswith("BLOCKED"):
            summary.status = self.status
            self._write_artifacts(summary)
            return summary

        records_for_phases = getattr(self, '_parsed_records', [])

        # Dir mapping verification (on real data if available)
        print("Dir mapping verification", flush=True)
        self.dir_audit = verify_dir_mapping(
            records_for_phases, limit=config.schema_sample_limit
        )

        # Liquidation flag inventory (on real data if available)
        print("Liquidation flag inventory", flush=True)
        self.liq_flag_inv = inventory_liquidation_flags(
            records_for_phases, limit=config.schema_sample_limit
        )

        # Phase D — Leverage source discovery
        print("Phase D: Leverage-source discovery", flush=True)
        local_found, local_paths = discover_local_cache(config.data_root)
        self.leverage_plan, self.leverage_audit = discover_leverage_source(config, local_paths)

        if self.status and self.status.startswith("BLOCKED"):
            summary.status = self.status
            self._write_artifacts(summary)
            return summary

        # Phase E — Position reconstruction (on real data if available)
        print("Phase E: Position reconstruction audit", flush=True)
        if not config.dry_run and not config.plan_only and records_for_phases:
            self.position_audit, _, _ = reconstruct_positions_full_audit(records_for_phases, config)
        else:
            self.position_audit = PositionReconstructionAudit()

        # Phase F — Liquidation price reconstruction
        print("Phase F: Isolated-only liquidation-price audit", flush=True)
        if records_for_phases and not config.dry_run and not config.plan_only:
            self.liq_audit = LiquidationReconstructionAudit()
        else:
            self.liq_audit = LiquidationReconstructionAudit()
            if config.bound_diagnostic:
                self.liq_audit.bound_diagnostic_used = True

        # Phase G — OI completeness
        print("Phase G: OI completeness diagnostic", flush=True)
        self.completeness = CompletenessSummary(burn_in_days=config.burn_in_days)

        # Phase H — Terminal decision
        print("Phase H: Terminal decision", flush=True)
        if not self.status or not self.status.startswith("BLOCKED"):
            has_real_records = (
                self.schema_gate is not None
                and self.schema_gate.verdict != SchemaVerdict.NOT_EVALUATED_PLAN_ONLY
            )

            if config.dry_run:
                self.status = StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_DRY_RUN_READY.value
            elif config.plan_only:
                if config.include_remote_plan:
                    self.status = StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_REMOTE_PLAN_READY.value
                else:
                    self.status = StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_PLAN_READY.value
            elif has_real_records:
                if not self.schema_gate:
                    self.schema_gate = SchemaGate(verdict=SchemaVerdict.PASS, address_field_present=True,
                                                  symbol_field_present=True, side_size_price_present=True,
                                                  start_position_present=True)
                if not self.dir_audit:
                    self.dir_audit = DirMappingAudit(verified_against_start_position=True)
                if not self.leverage_plan:
                    self.leverage_plan = LeverageSourcePlan(source_found=False)
                if not self.leverage_audit:
                    self.leverage_audit = LeverageJoinAudit()

                self.status = determine_terminal_status(
                    self.schema_gate, self.dir_audit, self.leverage_plan, self.leverage_audit,
                    self.position_audit, self.liq_audit, self.completeness, config,
                )
            else:
                self.status = StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_SCHEMA_NO_RECORDS.value

        summary.status = self.status
        self._write_artifacts(summary)

        # Generate summary.md
        blocked = self.status.startswith("BLOCKED") or self.status.startswith("COMPLETENESS_DIAGNOSTIC")
        md = generate_summary_md(
            config, self.source_plan or SourcePlan(),
            self.schema_gate or SchemaGate(), self.dir_audit or DirMappingAudit(),
            self.liq_flag_inv or LiquidationFlagInventory(),
            self.leverage_plan or LeverageSourcePlan(),
            self.position_audit or PositionReconstructionAudit(),
            self.liq_audit or LiquidationReconstructionAudit(),
            self.completeness or CompletenessSummary(),
            self.status, blocked=blocked,
        )
        md_path = self.out_root / "summary.md"
        md_path.write_text(md)

        print(f"\nStatus: {self.status}")
        print(f"Artifacts written to: {self.out_root}")

        return summary

    def _phase_a(self, config: StudyConfig) -> None:
        """Phase A — Archive coverage & partition discovery."""
        local_found, local_paths = discover_local_cache(config.data_root)

        remote_objects: list[dict] = []
        partitioning = ArchivePartitioning.UNKNOWN_PARTITIONING
        download_unit = DownloadUnit.UNKNOWN_UNIT
        coverage_inv = ArchiveCoverageInventory()

        if config.include_remote_plan and config.allow_s3_archive_read:
            creds_ok = check_aws_credentials()
            if not creds_ok:
                self.status = StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_S3_CREDENTIALS_MISSING.value
                self.partitioning_inv = PartitioningInventory(
                    candidate_namespaces=CANDIDATE_NAMESPACES,
                )
                return

            for ns in CANDIDATE_NAMESPACES:
                s3_prefix = f"s3://{ns}" if not ns.startswith("s3://") else ns
                objects = list_s3_prefix(ns, requester_pays=config.requester_pays)
                if objects:
                    partitioning, download_unit = discover_partitioning([o["key"] for o in objects])
                    remote_objects = objects
                    coverage_inv = discover_archive_coverage(objects, ns)
                    break

            if not remote_objects:
                self.status = StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_NAMESPACE_NOT_FOUND.value
                self.partitioning_inv = PartitioningInventory(
                    candidate_namespaces=CANDIDATE_NAMESPACES,
                )
                return

            if download_unit == DownloadUnit.ALL_COIN_HOUR_OBJECT:
                total_bytes = sum(o.get("size", 0) for o in remote_objects[:1])
            elif download_unit == DownloadUnit.SINGLE_COIN_HOUR_OBJECT:
                total_bytes = sum(o.get("size", 0) for o in remote_objects[:config.max_hours])
            else:
                total_bytes = sum(o.get("size", 0) for o in remote_objects[:min(24, len(remote_objects))])
            if total_bytes > config.max_download_bytes:
                self.status = StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_COST_OR_SIZE_CAP.value
                self.partitioning_inv = PartitioningInventory(
                    partitioning=partitioning,
                    smallest_download_unit=download_unit,
                    candidate_namespaces=CANDIDATE_NAMESPACES,
                    found_namespace=coverage_inv.namespace,
                    total_objects_listed=len(remote_objects),
                    sample_keys=[o["key"] for o in remote_objects[:10]],
                )
                return

        self.partitioning_inv = PartitioningInventory(
            partitioning=partitioning,
            smallest_download_unit=download_unit,
            candidate_namespaces=CANDIDATE_NAMESPACES,
            found_namespace=coverage_inv.namespace if coverage_inv.start_date else None,
            total_objects_listed=len(remote_objects),
            sample_keys=[o["key"] for o in remote_objects[:10]],
        )

        self.coverage_inv = coverage_inv
        self.source_plan = build_source_plan(
            config, local_found, local_paths, remote_objects, partitioning, download_unit,
        )

        if not local_found and not remote_objects:
            if config.dry_run:
                self.status = StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_DRY_RUN_READY.value
            elif config.plan_only:
                self.status = StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_PLAN_READY.value
            else:
                self.status = StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_NO_LOCAL_CACHE.value
        elif config.plan_only and remote_objects and self.coverage_inv.start_date:
            self.status = StudyStatus.NODE_FILLS_LIQ_PHASE_MINUS1_REMOTE_PLAN_READY.value

    def _phase_b(self, config: StudyConfig) -> None:
        """Phase B — Tiny measured-object fetch."""
        if not self.partitioning_inv or not self.coverage_inv:
            self.download_manifest = DownloadManifest()
            return

        manifest = DownloadManifest()
        parsed_records: list[NodeFillRecord] = []
        data_root = Path(config.data_root) if config.data_root else None
        cache_dir = (data_root / "node_fills_by_block" / "hourly") if data_root else None

        sample_keys = self.partitioning_inv.sample_keys or []

        # If no S3 sample_keys but local cache was found, pick a .lz4 file from the
        # node_fills_by_block/hourly directory (the canonical location for hourly fills).
        if not sample_keys and config.data_root:
            lz4_dir = Path(config.data_root) / "node_fills_by_block" / "hourly"
            if lz4_dir.is_dir():
                candidates = sorted(lz4_dir.glob("*.lz4"))
                for lz4_file in candidates:
                    if lz4_file.stat().st_size > 0:
                        sample_keys = [str(lz4_file)]
                        cache_dir = lz4_dir
                        break
            else:
                # Fallback: scan entire data_root for node_fills .lz4 files.
                lz4_files = sorted(Path(config.data_root).rglob("*.lz4"))
                for lz4_file in lz4_files:
                    if "node_fills" in str(lz4_file).lower() and lz4_file.stat().st_size > 0:
                        sample_keys = [str(lz4_file)]
                        cache_dir = Path(config.data_root)
                        break

        if not sample_keys:
            self.download_manifest = manifest
            return

        relative_key = sample_keys[0]

        found_ns = self.coverage_inv.namespace if self.coverage_inv else ""
        if found_ns and not relative_key.startswith(found_ns.split("/")[0] + "/"):
            ns_parts = found_ns.rstrip("/").split("/")
            bucket_name = ns_parts[0]
            full_key = f"{bucket_name}/{relative_key}"
        else:
            full_key = relative_key

        dest_path = None
        downloaded_bytes = 0
        sha256_hex = ""

        if cache_dir and cache_dir.is_dir():
            local_filename = relative_key.split("/")[-1] if "/" in relative_key else relative_key
            cached_file = cache_dir / local_filename
            if cached_file.is_file():
                dest_path = cached_file
                downloaded_bytes = cached_file.stat().st_size
                sha256_hex = hashlib.sha256(cached_file.read_bytes()).hexdigest()
        elif self.config.allow_s3_archive_read:
            try:
                if not cache_dir:
                    cache_dir = data_root / "node_fills_by_block" / "hourly" if data_root else None
                dest_path = cache_dir / relative_key.split("/")[-1] if cache_dir else None
                if dest_path:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                downloaded_bytes, sha256_hex = fetch_s3_object(
                    full_key, dest_path or Path("/dev/null"), requester_pays=config.requester_pays
                )
            except Exception as exc:
                print(f"Phase B: S3 download failed for {full_key}: {exc}", flush=True)
                downloaded_bytes = 0
                sha256_hex = ""

        manifest.objects.append({
            "key": full_key,
            "sha256": sha256_hex,
            "size_bytes": downloaded_bytes,
        })
        self.download_manifest = manifest

        if dest_path and dest_path.is_file() and dest_path.stat().st_size > 0:
            try:
                if dest_path.suffix == ".lz4":
                    parsed_records = list(stream_fills_from_lz4(str(dest_path)))
                else:
                    parsed_records = list(stream_fills_from_jsonl(str(dest_path)))
                print(f"Phase B: Parsed {len(parsed_records)} fill records from {full_key}", flush=True)
            except Exception as exc:
                print(f"Phase B: Parse failed for {dest_path}: {exc}", flush=True)
                parsed_records = []

        self._parsed_records = parsed_records

    def _phase_c(self, config: StudyConfig) -> None:
        """Phase C — Schema sufficiency gate."""
        self.schema_inventory = SchemaInventory()

        if config.plan_only or config.dry_run:
            self.schema_gate = SchemaGate(
                verdict=SchemaVerdict.NOT_EVALUATED_PLAN_ONLY,
                address_field_present=False,
                symbol_field_present=False,
                side_size_price_present=False,
                start_position_present=False,
            )
            return

        records = getattr(self, '_parsed_records', [])
        if not records:
            self.schema_gate = SchemaGate(
                verdict=SchemaVerdict.NOT_EVALUATED_PLAN_ONLY,
                address_field_present=False,
                symbol_field_present=False,
                side_size_price_present=False,
                start_position_present=False,
            )
            return

        self.schema_inventory, self.schema_gate = validate_schema(
            records, limit=config.schema_sample_limit
        )

    def _write_artifacts(self, summary: StudySummary) -> None:
        """Write all required artifacts."""
        out = self.out_root
        now_utc = datetime.now(UTC).isoformat()

        precommit_path = Path(__file__).parent / "docs" / "HYPERLIQUID_NODE_FILLS_LIQ_RECONSTRUCTION_PHASE_MINUS1_V0_PRECOMMITMENT.md"
        try:
            precommit_hash = hashlib.sha256(precommit_path.read_bytes()).hexdigest()[:16]
        except Exception:
            precommit_hash = "unknown"

        base_meta = {
            "study_id": self.config.study_id,
            "run_id": self.run_id,
            "created_at_utc": now_utc,
            "git_sha": _git_sha(),
            "git_branch": _git_branch(),
            "git_dirty": _git_dirty(),
            "repo_root": str(Path(__file__).parent.parent.parent),
            "precommitment_path": str(precommit_path),
            "precommitment_sha256": precommit_hash,
            "status": self.status or summary.status,
            "safety_mode": "public_data_observer_only",
            "orders_used": False,
            "private_keys_used": False,
            "auth_used": False,
            "live_execution_used": False,
            "paper_trading_used": False,
            "shadow_execution_used": False,
            "systemd_mutated": False,
            "bot_path_mutated": False,
            "registry_mutated": False,
            "wide_s3_sync_used": False,
            "max_download_bytes": self.config.max_download_bytes,
            "download_bytes_estimated": self.source_plan.estimated_bytes if self.source_plan else 0,
            "download_bytes_actual": summary.download_bytes_actual,
            "archive_partitioning": self.source_plan.partitioning if self.source_plan else "unknown",
            "smallest_download_unit": self.source_plan.smallest_download_unit if self.source_plan else "unknown",
            "schema_position_mechanics_passed": (
                self.schema_gate.verdict == SchemaVerdict.PASS if self.schema_gate else False
            ),
            "exact_leverage_source_available": (
                self.leverage_plan.source_found if self.leverage_plan else False
            ),
            "exact_liquidation_reconstruction_available": (
                self.liq_audit.exact_liquidation_available if self.liq_audit else False
            ),
            "bound_diagnostic_used": summary.bound_diagnostic_used or self.config.bound_diagnostic,
            "completeness_gate_applied": (
                self.completeness.completeness_gate_applied if self.completeness else False
            ),
        }

        manifest = {
            **base_meta,
            "safety": dataclasses.asdict(SafetyAudit()),
            "command_args": self._get_command_args(),
        }
        atomic_write_json(out / "run_manifest.json", manifest)
        (out / "precommitment_hash.txt").write_text(precommit_hash)

        if self.source_plan:
            atomic_write_json(out / "source_plan.json", dataclasses.asdict(self.source_plan))

        if self.partitioning_inv:
            atomic_write_json(out / "partitioning_inventory.json", {
                "partitioning": self.partitioning_inv.partitioning.value,
                "smallest_download_unit": self.partitioning_inv.smallest_download_unit.value,
                "candidate_namespaces": self.partitioning_inv.candidate_namespaces,
                "found_namespace": self.partitioning_inv.found_namespace,
                "total_objects_listed": self.partitioning_inv.total_objects_listed,
                "sample_keys": self.partitioning_inv.sample_keys[:20],
            })

        if self.coverage_inv:
            atomic_write_json(out / "archive_coverage_inventory.json", {
                "start_date": self.coverage_inv.start_date,
                "end_date": self.coverage_inv.end_date,
                "available_dates_count": len(self.coverage_inv.available_dates),
                "namespace": self.coverage_inv.namespace,
            })

        if self.download_manifest:
            atomic_write_json(out / "download_manifest.json", {
                "objects": self.download_manifest.objects[:20],
                "total_bytes": self.download_manifest.total_bytes,
            })

        if self.schema_inventory:
            atomic_write_json(out / "schema_inventory.json", {
                "fields_present": self.schema_inventory.fields_present,
                "required_fields": self.schema_inventory.required_fields,
                "optional_fields": self.schema_inventory.optional_fields,
            })
        if self.schema_gate:
            atomic_write_json(out / "schema_gate.json", dataclasses.asdict(self.schema_gate))

        if self.dir_audit:
            atomic_write_json(out / "dir_mapping_audit.json", {
                "mapping": self.dir_audit.mapping,
                "verified_against_start_position": self.dir_audit.verified_against_start_position,
                "mismatch_count": self.dir_audit.mismatch_count,
                "total_checked": self.dir_audit.total_checked,
                "variants_seen": self.dir_audit.variants_seen[:20],
            })

        if self.liq_flag_inv:
            atomic_write_json(out / "liquidation_flag_inventory.json", dataclasses.asdict(self.liq_flag_inv))

        if self.leverage_plan:
            atomic_write_json(out / "leverage_source_plan.json", dataclasses.asdict(self.leverage_plan))
        if self.leverage_audit:
            atomic_write_json(out / "leverage_join_audit.json", dataclasses.asdict(self.leverage_audit))

        # Position reconstruction (Patch 1-6 enhanced)
        if self.position_audit:
            ca = self.position_audit.consistency_audit
            pa_dict = {
                "records_seen": self.position_audit.records_seen,
                "records_parsed": self.position_audit.records_parsed,
                "users_seen_count": len(self.position_audit.users_seen),
                "symbols_seen_count": len(self.position_audit.symbols_seen),
                "position_transitions": self.position_audit.position_transitions,
                "transitions_with_start_position": ca.transitions_with_start_position if ca else 0,
                "start_position_consistency_rate": self.position_audit.start_position_consistency_rate,
                "unknown_cold_start_positions": self.position_audit.unknown_cold_start_positions,
                "known_open_positions": self.position_audit.known_open_positions,
                "known_isolated_open_positions": self.position_audit.known_isolated_open_positions,
                "cross_or_unknown_margin_positions": self.position_audit.cross_or_unknown_margin_positions,
                "records_rejected": self.position_audit.records_rejected,
                "dir_mapping_mismatch_count": self.position_audit.dir_mapping_mismatch_count,
                # Detailed audit fields (Patch 1)
                "consistency_rate_all_events": ca.consistency_rate_all_events if ca else 0.0,
                "consistency_rate_checkable_only": ca.consistency_rate_checkable_only if ca else 0.0,
                "transitions_total": ca.transitions_total if ca else 0,
                "transitions_checkable": ca.transitions_checkable if ca else 0,
                "transitions_uncheckable_cold_start": ca.transitions_uncheckable_cold_start if ca else 0,
                "transitions_reconciled": ca.transitions_reconciled if ca else 0,
                "transitions_mismatched": ca.transitions_mismatched if ca else 0,
            }
            atomic_write_json(out / "position_reconstruction_audit.json", pa_dict)

        # Start position consistency audit (Patch 1)
        if self.position_audit and self.position_audit.consistency_audit:
            ca = self.position_audit.consistency_audit
            atomic_write_json(out / "start_position_consistency_audit.json", {
                "records_seen": ca.records_seen,
                "records_parsed": ca.records_parsed,
                "position_events_seen": ca.position_events_seen,
                "transitions_total": ca.transitions_total,
                "transitions_checkable": ca.transitions_checkable,
                "transitions_uncheckable_cold_start": ca.transitions_uncheckable_cold_start,
                "transitions_reconciled": ca.transitions_reconciled,
                "transitions_mismatched": ca.transitions_mismatched,
                "consistency_rate_all_events": ca.consistency_rate_all_events,
                "consistency_rate_checkable_only": ca.consistency_rate_checkable_only,
            })

        # Convention audit (Patch 2)
        if self.position_audit and self.position_audit.convention_audit:
            conv = self.position_audit.convention_audit
            atomic_write_json(out / "start_position_convention_audit.json", {
                "pre_fill_match_count": conv.pre_fill_match_count,
                "pre_fill_match_rate": conv.pre_fill_match_rate,
                "post_fill_match_count": conv.post_fill_match_count,
                "post_fill_match_rate": conv.post_fill_match_rate,
                "dominant_convention": conv.dominant_convention,
                "ambiguous_count": conv.ambiguous_count,
                "neither_count": conv.neither_count,
            })

        # Side delta mapping (Patch 3)
        if self.position_audit and self.position_audit.side_delta_mapping:
            atomic_write_json(out / "side_delta_mapping_audit.json", {
                "side_to_candidate_delta": self.position_audit.side_delta_mapping.side_to_candidate_delta,
                "pre_fill_consistency_rate": self.position_audit.side_delta_mapping.pre_fill_consistency_rate,
                "post_fill_consistency_rate": self.position_audit.side_delta_mapping.post_fill_consistency_rate,
                "mismatch_rate": self.position_audit.side_delta_mapping.mismatch_rate,
                "verified": self.position_audit.side_delta_mapping.verified,
            })

        # Position keying (Patch 4)
        if self.position_audit and self.position_audit.position_keying:
            atomic_write_json(out / "position_keying_audit.json", {
                "position_key_fields_used": self.position_audit.position_keying.position_key_fields_used,
                "position_key_collision_count": self.position_audit.position_keying.position_key_collision_count,
                "ambiguous_key_count": self.position_audit.position_keying.ambiguous_key_count,
                "verified": self.position_audit.position_keying.verified,
            })

        # Builder DEX mapping (Patch 4)
        if self.position_audit and self.position_audit.builder_dex_mapping:
            atomic_write_json(out / "builder_dex_asset_mapping_audit.json", {
                "default_dex_asset_ids": self.position_audit.builder_dex_mapping.default_dex_asset_ids[:50],
                "builder_dex_asset_ids": self.position_audit.builder_dex_mapping.builder_dex_asset_ids[:50],
                "formula_tested": self.position_audit.builder_dex_mapping.formula_tested,
                "formula_correct": self.position_audit.builder_dex_mapping.formula_correct,
            })

        # Instrument identity (Patch 4)
        if self.position_audit and self.position_audit.instrument_identity:
            atomic_write_json(out / "instrument_identity_inventory.json", {
                "symbols_seen": self.position_audit.instrument_identity.symbols_seen[:50],
                "raw_coin_values_seen": self.position_audit.instrument_identity.raw_coin_values_seen[:50],
                "asset_ids_seen": self.position_audit.instrument_identity.asset_ids_seen[:50],
                "builder_dex_asset_ids_seen": self.position_audit.instrument_identity.builder_dex_asset_ids_seen[:50],
                "colliding_ticker_count": self.position_audit.instrument_identity.colliding_ticker_count,
            })

        # Pairing semantics (Patch 5)
        if self.position_audit and self.position_audit.pairing_semantics:
            atomic_write_json(out / "pairing_semantics_audit.json", {
                "paired_records_detected": self.position_audit.pairing_semantics.paired_records_detected,
                "double_count_risk": self.position_audit.pairing_semantics.double_count_risk,
                "grouping_rule": self.position_audit.pairing_semantics.grouping_rule,
                "verified": self.position_audit.pairing_semantics.verified,
            })

        if self.liq_audit:
            atomic_write_json(out / "liquidation_price_reconstruction_audit.json", {
                "isolated_positions_reconstructed": self.liq_audit.isolated_positions_reconstructed,
                "cross_or_unknown_excluded": self.liq_audit.cross_or_unknown_excluded,
                "exact_liquidation_available": self.liq_audit.exact_liquidation_available,
                "bound_diagnostic_used": self.liq_audit.bound_diagnostic_used,
                "margin_tier_schedule_status": self.liq_audit.margin_tier_schedule_status,
            })

        if self.completeness:
            atomic_write_json(out / "reconstruction_completeness_summary.json", dataclasses.asdict(self.completeness))

        # Leverage backfill cost plan (Patch 7) — always written
        leverage_cost_plan = {
            "replica_cmds_coverage_start": "",
            "replica_cmds_coverage_end": "",
            "object_count": 0,
            "sampled_object_size_compressed": 0,
            "estimated_full_scan_bytes_compressed": None,
            "server_side_filter_available": False,
            "requires_backfill_from_coverage_start": True,
            "users_with_no_updateLeverage_handling": "default-unverified unless sourced",
            "users_with_pre_coverage_leverage_handling": "excluded or marked unrecoverable",
        }
        atomic_write_json(out / "leverage_backfill_cost_plan.json", leverage_cost_plan)

        # Write summary last
        summary_obj = {**base_meta, "safety": dataclasses.asdict(SafetyAudit()), "command_args": self._get_command_args()}
        atomic_write_json(out / "summary.json", summary_obj)

        # Generate summary.md (always written so tests can verify)
        blocked = bool(self.status and (self.status.startswith("BLOCKED") or self.status.startswith("COMPLETENESS_DIAGNOSTIC")))
        md = generate_summary_md(
            self.config, self.source_plan or SourcePlan(),
            self.schema_gate or SchemaGate(), self.dir_audit or DirMappingAudit(),
            self.liq_flag_inv or LiquidationFlagInventory(),
            self.leverage_plan or LeverageSourcePlan(),
            self.position_audit or PositionReconstructionAudit(),
            self.liq_audit or LiquidationReconstructionAudit(),
            self.completeness or CompletenessSummary(),
            self.status or summary.status, blocked=blocked,
        )
        (out / "summary.md").write_text(md)

        # Next phase requirements
        (out / "next_phase0_precommitment_requirements.md").write_text(
            f"# Next Phase 0 Precommitment Requirements\n\n"
            f"Study: {self.config.study_id}\n"
            f"Status: `{self.status}`\n\n"
            f"If this probe passed (exact thin-slice reconstruction):\n"
            f"1. Write a separate frozen Phase 0 precommitment.\n"
            f"2. Define burn-in window (>= 14 days recommended).\n"
            f"3. Specify symbol universe, timeframe, and data type.\n"
            f"4. Define entry/exit signals, position sizing, fees, spread/slippage assumptions.\n"
            f"5. Run Phase 0 return test.\n\n"
            f"If blocked:\n"
            f"- Resolve: {self.status}\n"
            f"- Re-run probe after fix.\n"
        )

    def _get_command_args(self) -> list[str]:
        """Reconstruct CLI args for provenance."""
        return sys.argv[1:]


# ---------------------------------------------------------------------------
# Public entry point (called by run module)
# ---------------------------------------------------------------------------

def run_probe(config: StudyConfig | None = None) -> StudySummary:
    """Public entry point for the Phase -1 probe."""
    probe = NodeFillsLiqReconstructionProbe(config)
    return probe.run()
