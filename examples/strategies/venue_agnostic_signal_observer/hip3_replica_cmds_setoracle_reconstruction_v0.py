"""HIP-3 replica_cmds setOracle reconstruction scout.

Phase -1 data-plane feasibility study to determine whether HIP-3
deployer-submitted oracle updates (setOracle) are publicly
reconstructable from Hyperliquid historical node replica_cmds data.

Scope: data-plane feasibility ONLY.
  - No strategy, no PnL, no returns, no signals, no entries/exits
  - No Phase 0 precommitment, no registry mutation, no promotion
  - No live/paper trading, no orders, no auth, no private keys
  - No SonarX residual diagnostic

All S3 access is via NetworkChokepoint with explicit guard flags.
Envelope-first rule: do not search for oracle content before decoding
the record envelope.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import struct
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    import orjson
except ImportError:
    orjson = None  # type: ignore[assignment]

try:
    import lz4.frame as _lz4_frame
except ImportError:
    _lz4_frame = None  # type: ignore[assignment]

try:
    import gzip as _gzip_mod
except ImportError:
    _gzip_mod = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Ensure parent package is importable
# ---------------------------------------------------------------------------
_PKG_ROOT = str(Path(__file__).resolve().parents[4])
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
    NetworkChokepoint,
)

# ---------------------------------------------------------------------------
# JSON helper
# ---------------------------------------------------------------------------
def _loads_json(data: bytes | str) -> Any:
    """Load JSON with orjson when available, else stdlib."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if orjson is not None:
        return orjson.loads(data)
    return json.loads(data.decode("utf-8"))


def _dumps_json(obj: Any) -> bytes:
    """Dump JSON with orjson when available, else stdlib."""
    if orjson is not None:
        return orjson.dumps(obj)
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _dumps_json_pretty(obj: Any) -> bytes:
    """Pretty-print JSON."""
    if orjson is not None:
        return orjson.dumps(obj, option=orjson.OPT_INDENT_2)
    return json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REPLICA_CMDS_BUCKET = "hl-mainnet-node-data"
REPLICA_CMDS_PREFIX = "replica_cmds"

TARGET_DEXES = frozenset({"xyz", "flx", "km", "cash"})
TARGET_DISPLAY_SYMBOLS = frozenset({"TSLA", "AAPL", "MSFT", "NVDA"})
TARGET_MARKETS = frozenset({
    "xyz:TSLA", "flx:TSLA", "km:TSLA", "cash:TSLA",
    "xyz:AAPL", "km:AAPL",
    "xyz:MSFT", "cash:MSFT",
    "xyz:NVDA", "flx:NVDA", "km:NVDA", "cash:NVDA",
})
PRIMARY_PRIORITY_MARKETS = frozenset({"flx:TSLA", "flx:NVDA"})
ALL_DXY = frozenset(TARGET_DEXES)
ALL_SYMS = frozenset(TARGET_DISPLAY_SYMBOLS)

RECORD_VERSION = "recon_v0"
PARSER_VERSION = "recon_v0_parser_v1"

# ---------------------------------------------------------------------------
# Allowed statuses
# ---------------------------------------------------------------------------
ALLOWED_STATUSES = frozenset({
    "REPLICA_CMDS_RECON_DRY_RUN_READY",
    "REPLICA_CMDS_SOURCE_ACCESSIBLE",
    "REPLICA_CMDS_SOURCE_BLOCKED",
    "REPLICA_CMDS_SOURCE_ACCESSIBLE_BUT_NO_DEPLOYER_ORACLE",
    "REPLICA_CMDS_RECON_CHUNK_DOWNLOADED",
    "REPLICA_CMDS_ENVELOPE_DECODED",
    "REPLICA_CMDS_ENVELOPE_UNKNOWN",
    "REPLICA_CMDS_SCHEMA_UNKNOWN",
    "REPLICA_CMDS_ORACLE_COMMAND_FOUND",
    "REPLICA_CMDS_ORACLE_COMMAND_NOT_FOUND",
    "REPLICA_CMDS_ORACLE_SCHEMA_DECODED",
    "REPLICA_CMDS_ORACLE_SCHEMA_UNSUPPORTED",
    "REPLICA_CMDS_SETORACLE_RECONSTRUCTED",
    "REPLICA_CMDS_FORWARD_OVERLAP_VALIDATED",
    "REPLICA_CMDS_FORWARD_OVERLAP_TRANSFORM_REQUIRED",
    "REPLICA_CMDS_FORWARD_OVERLAP_MISMATCH",
    "REPLICA_CMDS_FORWARD_OVERLAP_UNAVAILABLE",
    "REPLICA_CMDS_BACKFILL_ALLOWED_AFTER_VALIDATION",
    "REPLICA_CMDS_BACKFILL_BLOCKED",
    "REPLICA_CMDS_PHASE_MINUS1_UNDERPOWERED",
    "REPLICA_CMDS_PHASE_MINUS1_ERROR",
    "FLX_ORACLE_LEVEL_SHIFT_CORROBORATED_DIAGNOSTIC",
    # Full-file decode statuses
    "REPLICA_CMDS_FULL_FILE_DECODE_ADDED",
    "REPLICA_CMDS_FULL_FILE_HISTOGRAM_COMPLETE",
    "FLX_SETORACLE_FOUND_FULL_FILE_HISTOGRAM",
    "FLX_NOT_SEEN_IN_ONE_FULL_FILE",
    "NO_SETORACLE_IN_SELECTED_FULL_FILE",
    "REPLICA_CMDS_TARGET_BLOCK_NOT_FOUND",
    "REPLICA_CMDS_FULL_FILE_SIZE_CAP_EXCEEDED",
    "REPLICA_CMDS_FULL_FILE_DECODE_FAILED",
    # FLX frequency confirmation statuses
    "FLX_SETORACLE_FOUND_BOUNDED_RECON",
    "FLX_SETORACLE_NOT_FOUND_IN_BOUNDED_SAMPLE",
    "FLX_SETORACLE_HUNT_UNDERPOWERED",
    "FLX_SETORACLE_PERSISTENTLY_ABSENT_IN_SAMPLE",
    "FLX_STALE_ORACLE_ARTIFACT_SUPPORTED",
    "ORACLE_BASIS_EDGE_UNLIKELY_MECHANISM_DIAGNOSTIC",
    "CROSS_DEX_SPREAD_PIVOT_RECOMMENDED",
    "REPLICA_CMDS_FULL_FILE_HUNT_COMPLETE",
})

FORBIDDEN_STATUSES = frozenset({
    "REJECTED", "PROFITABLE", "ALPHA_FOUND", "EDGE_CONFIRMED",
    "TRADE_READY", "EXECUTION_READY", "LIVE_READY", "READY_FOR_PHASE_0",
    "CANDIDATE_FOR_LIVE", "PAPER_STRATEGY_PROMOTED", "PROMOTION_AUTHORIZED",
    "PAPER_ONCE_ELIGIBLE",
})

FORBIDDEN_FAILURE_REASONS = frozenset({
    "REQUESTER_PAYS_CREDENTIALS_REQUIRED",
    "SOURCE_PREFIX_NOT_FOUND",
    "SOURCE_LISTING_FAILED",
    "RUNTIME_BUDGET_EXCEEDED",
    "DECOMPRESSOR_REQUIRED_NOT_AVAILABLE",
    "ENVELOPE_FORMAT_UNSUPPORTED",
    "NO_HIP3_DEPLOYER_SETORACLE_IN_ARCHIVE",
    "VALIDATOR_ORACLE_ONLY_FOUND",
    "NO_OVERLAP_WITH_FORWARD_RECORDER",
    "PER_DEX_VALIDATION_FAILED",
    "ARBITRARY_TRANSFORM_REJECTED",
})

# ---------------------------------------------------------------------------
# Allowed CLI fields
# ---------------------------------------------------------------------------
SAFETY_MODE = "public_archive_observer_only"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class RuntimeBudgetState:
    """Tracks wall-clock runtime budget."""
    max_runtime_minutes: float
    start_utc: datetime
    deadline_utc: datetime
    elapsed_seconds: float = 0.0
    budget_exceeded: bool = False

    @classmethod
    def create(cls, max_minutes: float, start_utc: Optional[datetime] = None) -> "RuntimeBudgetState":
        start = start_utc or datetime.now(timezone.utc)
        deadline = start + timedelta(minutes=max_minutes)
        return cls(
            max_runtime_minutes=max_minutes,
            start_utc=start,
            deadline_utc=deadline,
        )

    def check_deadline(self) -> bool:
        """Return True if budget still OK. Sets budget_exceeded if not."""
        now = datetime.now(timezone.utc)
        self.elapsed_seconds = (now - self.start_utc).total_seconds()
        if now > self.deadline_utc:
            self.budget_exceeded = True
            return False
        return True

    def remaining_seconds(self) -> float:
        now = datetime.now(timezone.utc)
        return max(0.0, (self.deadline_utc - now).total_seconds())


@dataclass
class ReplicaCmdsSourceConfig:
    """Configuration for replica_cmds S3 source access."""
    bucket: str = REPLICA_CMDS_BUCKET
    root_prefix: str = REPLICA_CMDS_PREFIX
    allow_s3_archive_read: bool = False
    allow_network_public: bool = False
    requester_pays: bool = True
    s3_connect_timeout_seconds: int = 10
    s3_read_timeout_seconds: int = 30
    s3_max_attempts: int = 2


@dataclass
class ReplicaCmdsProbeConfig:
    """Full probe configuration from CLI args."""
    out_root: Path
    target_date: Optional[str] = None
    markets: frozenset = field(default_factory=lambda: frozenset(TARGET_MARKETS))
    dexes: frozenset = field(default_factory=lambda: frozenset(TARGET_DEXES))
    max_replica_files: int = 3
    download_budget_bytes: int = 250_000_000
    max_oracle_commands: int = 1000
    max_runtime_minutes: float = 30.0
    dry_run: bool = False
    recon_only: bool = True
    validate_overlap: bool = False
    backfill_after_validation: bool = False
    keep_raw: bool = False
    forward_recorder_root: Optional[Path] = None
    source: ReplicaCmdsSourceConfig = field(default_factory=ReplicaCmdsSourceConfig)
    # Full-file decode mode
    decode_full_file: bool = False
    target_block: Optional[str] = None
    block_selection: str = "first"  # exact | around | first
    max_full_file_bytes: int = 1_500_000_000
    perpdeploy_histogram_only: bool = False
    stop_after_setoracle_payloads: Optional[int] = None
    # FLX frequency confirmation mode
    flx_frequency_confirmation: bool = False
    target_dates: List[str] = field(default_factory=list)
    max_full_files_per_date: int = 2
    max_full_files_total: int = 8
    stop_on_flx_found: bool = True
    continue_after_flx_found: bool = False

    @property
    def api_symbols(self) -> set:
        return set(self.markets)

    def api_symbol_dex(self, api_symbol: str) -> Optional[Tuple[str, str]]:
        """Split 'dex:symbol' -> ('dex', 'symbol')."""
        if ":" in api_symbol:
            dex, sym = api_symbol.split(":", 1)
            return (dex, sym)
        return None


@dataclass
class ReplicaCmdsRunManifest:
    """Run manifest written at start."""
    study_id: str = "hip3_replica_cmds_setoracle_reconstruction_v0"
    run_id: str = ""
    created_at_utc: str = ""
    branch: str = ""
    git_sha: str = ""
    git_dirty: bool = False
    repo_root: str = ""
    safety_mode: str = SAFETY_MODE
    source_kind: str = "REPLICA_CMDS_SETORACLE_DEPLOYER_SUBMITTED_RECON"
    no_orders_no_auth_no_live_confirmation: bool = True
    registry_mutated: bool = False
    phase0_precommitment_written: bool = False
    max_runtime_minutes: float = 30.0
    runtime_deadline_utc: str = ""

    def to_dict(self) -> dict:
        return {
            "study_id": self.study_id,
            "run_id": self.run_id,
            "created_at_utc": self.created_at_utc,
            "branch": self.branch,
            "git_sha": self.git_sha,
            "git_dirty": self.git_dirty,
            "repo_root": self.repo_root,
            "safety_mode": self.safety_mode,
            "source_kind": self.source_kind,
            "no_orders_no_auth_no_live_confirmation": self.no_orders_no_auth_no_live_confirmation,
            "registry_mutated": self.registry_mutated,
            "phase0_precommitment_written": self.phase0_precommitment_written,
            "max_runtime_minutes": self.max_runtime_minutes,
            "runtime_deadline_utc": self.runtime_deadline_utc,
        }


@dataclass
class ReplicaCmdsChunkInventory:
    """Result of S3 prefix listing."""
    bucket: str = ""
    root_prefix: str = ""
    listing_status: str = ""
    requester_pays_acknowledged: bool = False
    prefixes_sampled: List[str] = field(default_factory=list)
    keys_sampled: List[str] = field(default_factory=list)
    candidate_keys: List[str] = field(default_factory=list)
    inferred_layout: str = ""
    bytes_estimated_if_available: int = 0
    source_accessible: bool = False
    failure_reason: Optional[str] = None
    error_code: Optional[str] = None


@dataclass
class RawRecordSample:
    """Raw byte sample from a replica_cmds chunk."""
    byte_offset: int = 0
    record_length: Optional[int] = None
    first_64_hex: str = ""
    ascii_runs: str = ""
    compression_magic: str = ""
    delimiter_hints: List[str] = field(default_factory=list)
    length_prefix_hints: List[str] = field(default_factory=list)
    newline_framing: bool = False
    msgpack_hints: List[str] = field(default_factory=list)
    json_hints: List[str] = field(default_factory=list)
    unknown_binary: bool = False


@dataclass
class ReplicaCmdsEnvelopeProbe:
    """Envelope format probe result."""
    source_key: str = ""
    bytes_read: int = 0
    raw_prefix_hex: str = ""
    compression_detected: str = "none"
    decompression_attempts: List[str] = field(default_factory=list)
    decompressor_required: bool = False
    decompressor_available: bool = False
    record_boundary_strategy: str = ""
    record_samples: List[RawRecordSample] = field(default_factory=list)
    envelope_status: str = "REPLICA_CMDS_ENVELOPE_UNKNOWN"
    proceed_to_content_search_allowed: bool = False
    failure_reason: Optional[str] = None


@dataclass
class RawOracleCommandCandidate:
    """A raw oracle-like command detected during content recon."""
    source_key: str = ""
    byte_offset: int = 0
    record_index: int = 0
    raw_excerpt_hex: str = ""
    raw_excerpt_ascii: str = ""
    hash_sha256: str = ""
    shape_keys: List[str] = field(default_factory=list)
    shape_depth: int = 0
    is_dict: bool = False
    oracle_keyword_hits: List[str] = field(default_factory=list)
    dex_hits: List[str] = field(default_factory=list)
    symbol_hits: List[str] = field(default_factory=list)


@dataclass
class DecodedSetOracleCommand:
    """A decoded setOracle command from replica_cmds."""
    block_number: Optional[int] = None
    block_timestamp: Optional[str] = None
    source_key: str = ""
    command_index: int = 0
    raw_action_type: str = ""
    command_category: str = "unknown"  # validator_oracle | hip3_deployer_setOracle | unknown_oracle_like
    dex: str = ""
    display_symbol: str = ""
    api_symbol: str = ""
    submitted_oracle_px: Optional[float] = None
    mark_pxs_count: int = 0
    mark_pxs_excerpt_or_hash: str = ""
    raw_command_hash: str = ""
    parser_version: str = PARSER_VERSION
    decode_status: str = ""
    confidence_class: str = ""  # high | medium | low

    def to_dict(self) -> dict:
        return {
            "block_number": self.block_number,
            "block_timestamp": self.block_timestamp,
            "source_key": self.source_key,
            "command_index": self.command_index,
            "raw_action_type": self.raw_action_type,
            "command_category": self.command_category,
            "dex": self.dex,
            "display_symbol": self.display_symbol,
            "api_symbol": self.api_symbol,
            "submitted_oracle_px": self.submitted_oracle_px,
            "mark_pxs_count": self.mark_pxs_count,
            "mark_pxs_excerpt_or_hash": self.mark_pxs_excerpt_or_hash,
            "raw_command_hash": self.raw_command_hash,
            "parser_version": self.parser_version,
            "decode_status": self.decode_status,
            "confidence_class": self.confidence_class,
        }


@dataclass
class HistoricalOraclePoint:
    """A reconstructed oracle point for output."""
    block_number: Optional[int] = None
    timestamp_utc: Optional[str] = None
    source_key: str = ""
    command_index: int = 0
    api_symbol: str = ""
    dex: str = ""
    display_symbol: str = ""
    submitted_oracle_px: Optional[float] = None
    mark_pxs_count: int = 0
    raw_command_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "block_number": self.block_number,
            "timestamp_utc": self.timestamp_utc,
            "source_key": self.source_key,
            "command_index": self.command_index,
            "api_symbol": self.api_symbol,
            "dex": self.dex,
            "display_symbol": self.display_symbol,
            "submitted_oracle_px": self.submitted_oracle_px,
            "mark_pxs_count": self.mark_pxs_count,
            "raw_command_hash": self.raw_command_hash,
        }


@dataclass
class ForwardRecorderOraclePoint:
    """A point from the forward recorder."""
    timestamp_utc: str = ""
    api_symbol: str = ""
    dex: str = ""
    display_symbol: str = ""
    oracle_price: Optional[float] = None
    mark_price: Optional[float] = None
    mid_price: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "timestamp_utc": self.timestamp_utc,
            "api_symbol": self.api_symbol,
            "dex": self.dex,
            "display_symbol": self.display_symbol,
            "oracle_price": self.oracle_price,
            "mark_price": self.mark_price,
            "mid_price": self.mid_price,
        }


@dataclass
class PerDexOverlapValidationResult:
    """Per-API-symbol / per-DEX overlap validation."""
    api_symbol: str = ""
    dex: str = ""
    joined_count_5s: int = 0
    joined_count_60s: int = 0
    median_timestamp_gap_seconds: float = 0.0
    p90_timestamp_gap_seconds: float = 0.0
    reconstructed_oracle_px: List[float] = field(default_factory=list)
    forward_oracle_px: List[float] = field(default_factory=list)
    median_abs_bps_diff: float = 0.0
    p90_abs_bps_diff: float = 0.0
    max_abs_bps_diff: float = 0.0
    exact_near_match_rate: float = 0.0
    mismatched_rows_sample: List[dict] = field(default_factory=list)
    validation_status: str = ""  # VALIDATED | TRANSFORM_REQUIRED | MISMATCH | UNAVAILABLE

    def to_dict(self) -> dict:
        return {
            "api_symbol": self.api_symbol,
            "dex": self.dex,
            "joined_count_5s": self.joined_count_5s,
            "joined_count_60s": self.joined_count_60s,
            "median_timestamp_gap_seconds": self.median_timestamp_gap_seconds,
            "p90_timestamp_gap_seconds": self.p90_timestamp_gap_seconds,
            "reconstructed_oracle_px_count": len(self.reconstructed_oracle_px),
            "forward_oracle_px_count": len(self.forward_oracle_px),
            "median_abs_bps_diff": self.median_abs_bps_diff,
            "p90_abs_bps_diff": self.p90_abs_bps_diff,
            "max_abs_bps_diff": self.max_abs_bps_diff,
            "exact_near_match_rate": self.exact_near_match_rate,
            "mismatched_rows_sample_count": len(self.mismatched_rows_sample),
            "validation_status": self.validation_status,
        }


@dataclass
class ForwardOverlapValidationResult:
    """Aggregate forward overlap validation."""
    status: str = "REPLICA_CMDS_FORWARD_OVERLAP_UNAVAILABLE"
    per_dex_results: List[PerDexOverlapValidationResult] = field(default_factory=list)
    validated_count: int = 0
    transform_required_count: int = 0
    mismatch_count: int = 0
    unavailable_count: int = 0

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "per_dex_results": [r.to_dict() for r in self.per_dex_results],
            "validated_count": self.validated_count,
            "transform_required_count": self.transform_required_count,
            "mismatch_count": self.mismatch_count,
            "unavailable_count": self.unavailable_count,
        }


@dataclass
class SetOracleReconstructionSummary:
    """Summary of setOracle reconstruction."""
    source_keys_processed: int = 0
    bytes_processed: int = 0
    commands_seen: int = 0
    validator_oracle_commands_seen: int = 0
    hip3_deployer_setoracle_commands_seen: int = 0
    oracle_candidates_seen: int = 0
    decoded_by_dex: Dict[str, int] = field(default_factory=dict)
    decoded_by_symbol: Dict[str, int] = field(default_factory=dict)
    decoded_by_api_symbol: Dict[str, int] = field(default_factory=dict)
    flx_tsla_setoracle_seen: bool = False
    flx_nvda_setoracle_seen: bool = False
    malformed_oracle_candidates: int = 0
    unsupported_schema_count: int = 0
    first_timestamp_utc: Optional[str] = None
    last_timestamp_utc: Optional[str] = None
    output_path: str = ""
    parser_validated_against_forward_recorder: bool = False

    def to_dict(self) -> dict:
        return {
            "source_keys_processed": self.source_keys_processed,
            "bytes_processed": self.bytes_processed,
            "commands_seen": self.commands_seen,
            "validator_oracle_commands_seen": self.validator_oracle_commands_seen,
            "hip3_deployer_setoracle_commands_seen": self.hip3_deployer_setoracle_commands_seen,
            "oracle_candidates_seen": self.oracle_candidates_seen,
            "decoded_by_dex": self.decoded_by_dex,
            "decoded_by_symbol": self.decoded_by_symbol,
            "decoded_by_api_symbol": self.decoded_by_api_symbol,
            "flx_tsla_setoracle_seen": self.flx_tsla_setoracle_seen,
            "flx_nvda_setoracle_seen": self.flx_nvda_setoracle_seen,
            "malformed_oracle_candidates": self.malformed_oracle_candidates,
            "unsupported_schema_count": self.unsupported_schema_count,
            "first_timestamp_utc": self.first_timestamp_utc,
            "last_timestamp_utc": self.last_timestamp_utc,
            "output_path": self.output_path,
            "parser_validated_against_forward_recorder": self.parser_validated_against_forward_recorder,
        }


@dataclass
class ObservedVolumeEstimate:
    """Volume reality check from recon sample."""
    bytes_per_chunk_observed: float = 0.0
    records_per_chunk_observed: float = 0.0
    decoded_records_per_mb: float = 0.0
    oracle_candidates_per_mb: float = 0.0
    hip3_deployer_setoracle_candidates_per_mb: float = 0.0
    estimated_chunks_per_target_day: float = 0.0
    estimated_bytes_per_target_day: float = 0.0
    estimated_bytes_to_cover_one_overlap_hour: float = 0.0
    estimated_files_to_cover_one_overlap_hour: int = 0
    validation_budget_recommendation_bytes: int = 0
    validation_files_recommendation: int = 0
    validation_run_feasible_under_default_budget: bool = False

    def to_dict(self) -> dict:
        return {
            "bytes_per_chunk_observed": self.bytes_per_chunk_observed,
            "records_per_chunk_observed": self.records_per_chunk_observed,
            "decoded_records_per_mb": self.decoded_records_per_mb,
            "oracle_candidates_per_mb": self.oracle_candidates_per_mb,
            "hip3_deployer_setoracle_candidates_per_mb": self.hip3_deployer_setoracle_candidates_per_mb,
            "estimated_chunks_per_target_day": self.estimated_chunks_per_target_day,
            "estimated_bytes_per_target_day": self.estimated_bytes_per_target_day,
            "estimated_bytes_to_cover_one_overlap_hour": self.estimated_bytes_to_cover_one_overlap_hour,
            "estimated_files_to_cover_one_overlap_hour": self.estimated_files_to_cover_one_overlap_hour,
            "validation_budget_recommendation_bytes": self.validation_budget_recommendation_bytes,
            "validation_files_recommendation": self.validation_files_recommendation,
            "validation_run_feasible_under_default_budget": self.validation_run_feasible_under_default_budget,
        }


@dataclass
class FullFileHistogramResult:
    """Result of full-file perpDeploy.setOracle histogram."""
    run_id: str = ""
    target_date: str = ""
    selected_source_key: str = ""
    selected_block: str = ""
    bytes_downloaded: int = 0
    bytes_decompressed_estimate_or_actual: int = 0
    records_decoded: int = 0
    parse_failures: int = 0
    malformed_records: int = 0
    action_bundles_seen: int = 0
    unique_action_types: int = 0
    full_action_type_histogram: Dict[str, int] = field(default_factory=dict)
    perpDeploy_count: int = 0
    setOracle_payload_count: int = 0
    oraclePxs_pair_count: int = 0
    markPxs_pair_count: int = 0
    dexes_seen: List[str] = field(default_factory=list)
    setOracle_payloads_by_dex: Dict[str, int] = field(default_factory=dict)
    oraclePxs_pairs_by_dex: Dict[str, int] = field(default_factory=dict)
    markets_seen_by_dex: Dict[str, List[str]] = field(default_factory=dict)
    target_markets_seen: List[str] = field(default_factory=list)
    flx_seen: bool = False
    cash_seen: bool = False
    xyz_seen: bool = False
    km_seen: bool = False
    flx_tsla_found: bool = False
    flx_nvda_found: bool = False
    cash_tsla_found: bool = False
    cash_nvda_found: bool = False
    first_setOracle_record_index_by_dex: Dict[str, int] = field(default_factory=dict)
    last_setOracle_record_index_by_dex: Dict[str, int] = field(default_factory=dict)
    inferred_update_cadence_by_dex: Dict[str, dict] = field(default_factory=dict)
    bounded_candidate_examples_by_dex: Dict[str, list] = field(default_factory=dict)
    candidate_hashes_by_dex: Dict[str, list] = field(default_factory=dict)
    parser_confidence: str = ""
    limitations: List[str] = field(default_factory=list)
    absence_claim_supported_by_full_file: bool = False
    conclusion: str = ""
    status: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "target_date": self.target_date,
            "selected_source_key": self.selected_source_key,
            "selected_block": self.selected_block,
            "bytes_downloaded": self.bytes_downloaded,
            "bytes_decompressed_estimate_or_actual": self.bytes_decompressed_estimate_or_actual,
            "records_decoded": self.records_decoded,
            "parse_failures": self.parse_failures,
            "malformed_records": self.malformed_records,
            "action_bundles_seen": self.action_bundles_seen,
            "unique_action_types": self.unique_action_types,
            "full_action_type_histogram": self.full_action_type_histogram,
            "perpDeploy_count": self.perpDeploy_count,
            "setOracle_payload_count": self.setOracle_payload_count,
            "oraclePxs_pair_count": self.oraclePxs_pair_count,
            "markPxs_pair_count": self.markPxs_pair_count,
            "dexes_seen": self.dexes_seen,
            "setOracle_payloads_by_dex": self.setOracle_payloads_by_dex,
            "oraclePxs_pairs_by_dex": self.oraclePxs_pairs_by_dex,
            "markets_seen_by_dex": self.markets_seen_by_dex,
            "target_markets_seen": self.target_markets_seen,
            "flx_seen": self.flx_seen,
            "cash_seen": self.cash_seen,
            "xyz_seen": self.xyz_seen,
            "km_seen": self.km_seen,
            "flx_tsla_found": self.flx_tsla_found,
            "flx_nvda_found": self.flx_nvda_found,
            "cash_tsla_found": self.cash_tsla_found,
            "cash_nvda_found": self.cash_nvda_found,
            "first_setOracle_record_index_by_dex": self.first_setOracle_record_index_by_dex,
            "last_setOracle_record_index_by_dex": self.last_setOracle_record_index_by_dex,
            "inferred_update_cadence_by_dex": self.inferred_update_cadence_by_dex,
            "bounded_candidate_examples_by_dex": self.bounded_candidate_examples_by_dex,
            "candidate_hashes_by_dex": self.candidate_hashes_by_dex,
            "parser_confidence": self.parser_confidence,
            "limitations": self.limitations,
            "absence_claim_supported_by_full_file": self.absence_claim_supported_by_full_file,
            "conclusion": self.conclusion,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _get_git_info(repo_root: Path) -> Tuple[str, bool, str]:
    """Get git SHA, dirty flag, and branch."""
    import subprocess
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root), stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        sha = "unknown"
    try:
        dirty_raw = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root), stderr=subprocess.DEVNULL,
        ).decode().strip()
        dirty = len(dirty_raw) > 0
    except Exception:
        dirty = False
    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"],
            cwd=str(repo_root), stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        branch = "unknown"
    return sha, dirty, branch


def _write_json_artifact(path: Path, obj: Any) -> None:
    """Write JSON artifact atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(_dumps_json_pretty(obj))
    tmp.rename(path)


def _write_jsonl_rows(path: Path, rows: List[dict]) -> None:
    """Write JSONL rows atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    lines = []
    for row in rows:
        lines.append(_dumps_json(row))
    tmp.write_bytes(b"\n".join(lines) + b"\n")
    tmp.rename(path)


def _append_jsonl_row(path: Path, row: dict) -> None:
    """Append a single JSONL row (not atomic but fine for streaming)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        f.write(_dumps_json(row))
        f.write(b"\n")


def _parse_api_symbol(api_symbol: str) -> Optional[Tuple[str, str]]:
    """Parse 'dex:symbol' -> ('dex', 'symbol')."""
    if ":" in api_symbol:
        dex, sym = api_symbol.split(":", 1)
        return (dex, sym)
    return None


def _display_symbol_from_api(api_symbol: str) -> str:
    """Extract display symbol from API symbol."""
    parts = _parse_api_symbol(api_symbol)
    return parts[1] if parts else api_symbol


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _safe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Run manifest
# ---------------------------------------------------------------------------

def write_run_manifest(run_dir: Path, config: ReplicaCmdsProbeConfig,
                       runtime: RuntimeBudgetState) -> str:
    """Write run_manifest.json and return run_id."""
    sha, dirty, branch = _get_git_info(Path(_PKG_ROOT))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    manifest = ReplicaCmdsRunManifest(
        run_id=run_id,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        branch=branch,
        git_sha=sha,
        git_dirty=dirty,
        repo_root=_PKG_ROOT,
        max_runtime_minutes=runtime.max_runtime_minutes,
        runtime_deadline_utc=runtime.deadline_utc.isoformat(),
    )
    manifest_dir = run_dir / run_id
    manifest_dir.mkdir(parents=True, exist_ok=True)
    _write_json_artifact(manifest_dir / "run_manifest.json", manifest.to_dict())
    return run_id


# ---------------------------------------------------------------------------
# S3 inventory probe
# ---------------------------------------------------------------------------

def probe_s3_inventory(
    chokepoint: NetworkChokepoint,
    config: ReplicaCmdsProbeConfig,
    budget: RuntimeBudgetState,
) -> ReplicaCmdsChunkInventory:
    """List replica_cmds S3 prefix and select candidate keys.

    Multi-level recursive listing for nested layout:
      replica_cmds/<creation_timestamp>/<YYYYMMDD>/<block>.lz4
    """
    inv = ReplicaCmdsChunkInventory(
        bucket=config.source.bucket,
        root_prefix=config.source.root_prefix,
        requester_pays_acknowledged=config.source.requester_pays,
    )
    if not config.source.allow_s3_archive_read:
        inv.listing_status = "BLOCKED_NO_S3_FLAG"
        inv.failure_reason = "REQUESTER_PAYS_CREDENTIALS_REQUIRED"
        return inv
    if not budget.check_deadline():
        inv.listing_status = "BUDGET_EXCEEDED"
        inv.failure_reason = "RUNTIME_BUDGET_EXCEEDED"
        return inv
    # --- Multi-level nested listing ---
    # Target date filter: convert "2026-05-27" -> "20260527"
    target_date_yyyymmdd = None
    if config.target_date:
        target_date_yyyymmdd = config.target_date.replace("-", "")

    # Level 1: list timestamp prefixes under replica_cmds/
    all_candidates: List[dict] = []
    all_timestamp_prefixes: List[str] = []
    listing_status_detail = ""
    try:
        l1 = chokepoint.s3_list_prefix(
            bucket=config.source.bucket,
            prefix=config.source.root_prefix + "/",
            requester_pays=config.source.requester_pays,
            max_keys=500,
            include_subdirs=True,
        )
    except Exception as e:
        inv.listing_status = "LISTING_FAILED"
        inv.failure_reason = str(e)
        return inv
    if l1.get("error_code"):
        inv.listing_status = "LISTING_FAILED"
        inv.error_code = l1["error_code"]
        inv.failure_reason = l1["error_code"]
        return inv
    l1_prefixes = l1.get("prefixes", [])
    l1_objects = l1.get("objects", [])
    all_timestamp_prefixes = l1_prefixes[:500]
    # Also collect any direct objects under replica_cmds/ (flat layout fallback)
    for obj in l1_objects:
        all_candidates.append({"key": obj["key"], "size": obj.get("size", 0)})
    inv.prefixes_sampled = [p for p in all_timestamp_prefixes[:20]]

    # Level 2: for each timestamp prefix, list date prefixes
    date_prefixes_to_scan: List[str] = []
    # Date-aware early-skip: if target_date is specified, skip timestamps
    # that are clearly too old to contain the target date's data.
    # Based on observed S3 layout: creation timestamp can be several days
    # before the data date. Use a 7-day lookback window.
    ts_early_skip_prefix = None
    if target_date_yyyymmdd:
        try:
            target_dt = datetime.strptime(target_date_yyyymmdd, "%Y%m%d")
            early_dt = target_dt - timedelta(days=7)
            ts_early_skip_prefix = early_dt.strftime("replica_cmds/%Y-%m-%dT")
        except (ValueError, TypeError):
            ts_early_skip_prefix = None
    for ts_prefix in all_timestamp_prefixes:
        if not budget.check_deadline():
            listing_status_detail = "budget_exceeded_at_level2"
            break
        # Skip timestamps clearly before the target window
        if ts_early_skip_prefix and ts_prefix < ts_early_skip_prefix:
            continue
        try:
            l2 = chokepoint.s3_list_prefix(
                bucket=config.source.bucket,
                prefix=ts_prefix,
                requester_pays=config.source.requester_pays,
                max_keys=500,
                include_subdirs=True,
            )
        except Exception:
            continue
        if l2.get("error_code"):
            continue
        l2_prefixes = l2.get("prefixes", [])
        l2_objects = l2.get("objects", [])
        # Collect date prefixes
        for dp in l2_prefixes:
            # dp looks like "replica_cmds/<ts>/20260527/"
            date_segment = dp.rstrip("/").split("/")[-1]
            if target_date_yyyymmdd and date_segment == target_date_yyyymmdd:
                date_prefixes_to_scan.append(dp)
            elif not target_date_yyyymmdd:
                date_prefixes_to_scan.append(dp)
        # Also collect any direct objects under timestamp (unusual but handle)
        for obj in l2_objects:
            k = obj["key"]
            if target_date_yyyymmdd:
                if target_date_yyyymmdd in k:
                    all_candidates.append({"key": k, "size": obj.get("size", 0)})
            else:
                all_candidates.append({"key": k, "size": obj.get("size", 0)})

    # Level 3: for each date prefix, list block files
    for dp_prefix in date_prefixes_to_scan:
        if not budget.check_deadline():
            listing_status_detail = "budget_exceeded_at_level3"
            break
        if len(all_candidates) >= config.max_replica_files * 5:
            break  # bounded
        try:
            l3 = chokepoint.s3_list_prefix(
                bucket=config.source.bucket,
                prefix=dp_prefix,
                requester_pays=config.source.requester_pays,
                max_keys=config.max_replica_files * 3,
                include_subdirs=False,  # flat listing for block files
            )
        except Exception:
            continue
        if l3.get("error_code"):
            continue
        for obj in l3.get("objects", []):
            all_candidates.append({"key": obj["key"], "size": obj.get("size", 0)})

    # Deduplicate by key
    seen_keys = set()
    deduped: List[dict] = []
    for c in all_candidates:
        if c["key"] not in seen_keys:
            seen_keys.add(c["key"])
            deduped.append(c)
    # Sort by key for determinism
    deduped.sort(key=lambda x: x["key"])

    inv.keys_sampled = [c["key"] for c in deduped[:20]]
    inv.candidate_keys = [c["key"] for c in deduped[:config.max_replica_files * 2]]
    inv.bytes_estimated_if_available = sum(c.get("size", 0) for c in deduped[:config.max_replica_files * 2])
    # Infer layout
    if all_timestamp_prefixes and date_prefixes_to_scan:
        inv.inferred_layout = (
            f"nested_timestamps={len(all_timestamp_prefixes)},"
            f"date_prefixes={len(date_prefixes_to_scan)},"
            f"block_keys={len(deduped)}"
        )
    elif all_timestamp_prefixes:
        inv.inferred_layout = f"timestamp_prefixes_only={len(all_timestamp_prefixes)}"
    elif deduped:
        inv.inferred_layout = f"flat_keys={len(deduped)}"
    else:
        inv.inferred_layout = "empty_listing"
    if listing_status_detail:
        inv.inferred_layout += f",{listing_status_detail}"
    inv.listing_status = "OK"
    inv.source_accessible = True
    return inv


# ---------------------------------------------------------------------------
# Envelope probe
# ---------------------------------------------------------------------------

def _detect_compression_magic(data: bytes) -> str:
    """Detect compression from magic bytes."""
    if len(data) < 4:
        return "unknown"
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    if data[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd"
    # LZ4: 4-byte magic = 0x184D2204
    if data[:4] == b"\x04\x22\x4d\x18":
        return "lz4"
    if data[:3] == b"BZh":
        return "bzip2"
    return "unknown"


def _try_decompress(data: bytes, fmt: str) -> Optional[bytes]:
    """Try to decompress data with the given format."""
    try:
        if fmt == "gzip" and _gzip_mod is not None:
            return _gzip_mod.decompress(data)
        if fmt == "lz4" and _lz4_frame is not None:
            return _lz4_frame.decompress(data)
    except Exception:
        return None
    return None


def _analyze_raw_sample(data: bytes, offset: int = 0) -> RawRecordSample:
    """Analyze raw bytes and produce a sample record."""
    sample = RawRecordSample(byte_offset=offset)
    if len(data) == 0:
        sample.unknown_binary = True
        return sample
    sample.first_64_hex = data[:64].hex()
    # Check ASCII runs
    ascii_chars = []
    for b in data[:128]:
        if 32 <= b <= 126:
            ascii_chars.append(chr(b))
        else:
            ascii_chars.append(".")
    sample.ascii_runs = "".join(ascii_chars[:80])
    # Compression detection
    comp = _detect_compression_magic(data)
    if comp != "unknown":
        sample.compression_magic = comp
    # Check for JSON-like
    try:
        text = data[:1024].decode("utf-8", errors="replace")
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            sample.json_hints.append(f"starts_with_{stripped[0]}")
    except Exception:
        pass
    # Check for msgpack-like
    if len(data) > 0:
        first_byte = data[0]
        if first_byte <= 0x7f:
            sample.msgpack_hints.append("positive_fixint")
        elif first_byte == 0xc0:
            sample.msgpack_hints.append("nil")
        elif first_byte == 0xc2 or first_byte == 0xc3:
            sample.msgpack_hints.append("bool")
        elif 0xdc <= first_byte <= 0xdd:
            sample.msgpack_hints.append("array16_or_32")
        elif 0xde <= first_byte <= 0xdf:
            sample.msgpack_hints.append("map16_or_32")
    # Newline framing
    if b"\n" in data[:4096]:
        sample.delimiter_hints.append("newline")
        sample.newline_framing = True
    if b"\x00" in data[:4096]:
        sample.delimiter_hints.append("null_byte")
    # Length prefix hints
    if len(data) >= 4:
        possible_len = struct.unpack(">I", data[:4])[0]
        if 1 < possible_len < len(data):
            sample.length_prefix_hints.append(f"big_endian_uint32={possible_len}")
        possible_len_le = struct.unpack("<I", data[:4])[0]
        if 1 < possible_len_le < len(data):
            sample.length_prefix_hints.append(f"little_endian_uint32={possible_len_le}")
    if len(data) < 4:
        sample.unknown_binary = True
    return sample


def probe_envelope(
    chokepoint: NetworkChokepoint,
    config: ReplicaCmdsProbeConfig,
    inventory: ReplicaCmdsChunkInventory,
    budget: RuntimeBudgetState,
) -> ReplicaCmdsEnvelopeProbe:
    """Probe envelope format from one small sample chunk."""
    probe = ReplicaCmdsEnvelopeProbe()
    if not inventory.candidate_keys:
        probe.failure_reason = "NO_CANDIDATE_KEYS"
        return probe
    if not budget.check_deadline():
        probe.failure_reason = "RUNTIME_BUDGET_EXCEEDED"
        return probe
    # Pick first candidate key
    key = inventory.candidate_keys[0]
    probe.source_key = key
    try:
        data = chokepoint.s3_read_object(
            bucket=inventory.bucket,
            key=key,
            requester_pays=config.source.requester_pays,
        )
    except Exception as e:
        probe.failure_reason = f"S3_READ_FAILED: {e}"
        return probe
    probe.bytes_read = len(data)
    probe.raw_prefix_hex = data[:128].hex() if len(data) >= 128 else data.hex()
    # Compression detection
    comp = _detect_compression_magic(data)
    probe.compression_detected = comp
    probe.decompression_attempts.append(f"detected={comp}")
    # Try decompression
    decompressed = None
    if comp != "unknown":
        decompressed = _try_decompress(data, comp)
        if decompressed:
            probe.decompression_attempts.append(f"decompressed_{comp}_ok")
        else:
            probe.decompression_attempts.append(f"decompress_{comp}_failed")
    raw = decompressed if decompressed else data
    # Analyze first ~4KB
    sample_data = raw[:4096]
    sample = _analyze_raw_sample(sample_data, offset=0)
    probe.record_samples.append(sample)
    # Boundary strategy
    if sample.newline_framing:
        probe.record_boundary_strategy = "newline_delimited"
    elif sample.json_hints:
        probe.record_boundary_strategy = "json_array_or_object"
    elif sample.msgpack_hints:
        probe.record_boundary_strategy = "msgpack"
    elif sample.length_prefix_hints:
        probe.record_boundary_strategy = "length_prefixed"
    else:
        probe.record_boundary_strategy = "unknown_binary"
    # Determine if we can proceed
    if sample.json_hints or sample.newline_framing:
        probe.envelope_status = "REPLICA_CMDS_ENVELOPE_DECODED"
        probe.proceed_to_content_search_allowed = True
    elif sample.msgpack_hints and sample.length_prefix_hints:
        probe.envelope_status = "REPLICA_CMDS_ENVELOPE_DECODED"
        probe.proceed_to_content_search_allowed = True
    elif comp != "unknown" and decompressed is None:
        probe.envelope_status = "REPLICA_CMDS_ENVELOPE_UNKNOWN"
        probe.proceed_to_content_search_allowed = False
        probe.failure_reason = f"DECOMPRESSOR_REQUIRED_NOT_AVAILABLE: {comp}"
    else:
        probe.envelope_status = "REPLICA_CMDS_ENVELOPE_UNKNOWN"
        probe.proceed_to_content_search_allowed = False
    return probe


# ---------------------------------------------------------------------------
# Content recon / schema probe
# ---------------------------------------------------------------------------

ORACLE_KEYWORDS = {"oracle", "setOracle", "oraclePx", "markPxs", "perpDex", "builder"}
TARGET_DEX_SET = set(TARGET_DEXES)
TARGET_SYMBOL_SET = set(TARGET_DISPLAY_SYMBOLS)
PRIORITY_DEX_SET = {"flx"}


def _search_dict_for_oracle(obj: Any, depth: int = 0) -> List[str]:
    """Recursively search dict/list for oracle-like keywords."""
    if depth > 10:
        return []
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(kw.lower() in kl for kw in ORACLE_KEYWORDS):
                hits.append(str(k))
            if isinstance(v, str) and any(kw.lower() in v.lower() for kw in ORACLE_KEYWORDS):
                hits.append(str(k) + "=>" + str(v)[:50])
            hits.extend(_search_dict_for_oracle(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj[:20]:  # bounded
            hits.extend(_search_dict_for_oracle(item, depth + 1))
    return hits


def _search_dict_for_dex(obj: Any, depth: int = 0) -> List[str]:
    """Search for target DEX names."""
    if depth > 10:
        return []
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v.lower() in TARGET_DEX_SET:
                hits.append(v)
            if isinstance(k, str) and k.lower() in TARGET_DEX_SET:
                hits.append(k)
            hits.extend(_search_dict_for_dex(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj[:20]:
            hits.extend(_search_dict_for_dex(item, depth + 1))
    return hits


def _search_dict_for_symbol(obj: Any, depth: int = 0) -> List[str]:
    """Search for target display symbols."""
    if depth > 10:
        return []
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v.upper() in TARGET_SYMBOL_SET:
                hits.append(v)
            if isinstance(k, str) and k.upper() in TARGET_SYMBOL_SET:
                hits.append(k)
            hits.extend(_search_dict_for_symbol(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj[:20]:
            hits.extend(_search_dict_for_symbol(item, depth + 1))
    return hits


def probe_recon_schema(
    chokepoint: NetworkChokepoint,
    config: ReplicaCmdsProbeConfig,
    inventory: ReplicaCmdsChunkInventory,
    budget: RuntimeBudgetState,
) -> Tuple[ReplicaCmdsEnvelopeProbe, ReplicaCmdsRawRecordSample, List[RawOracleCommandCandidate], dict]:
    """Run envelope probe + content search on candidate keys."""
    envelope_probe = probe_envelope(chokepoint, config, inventory, budget)
    candidates: List[RawOracleCommandCandidate] = []
    schema_result: dict = {
        "source_keys_attempted": 0,
        "bytes_read": 0,
        "chunks_attempted": 0,
        "decoded_records_seen": 0,
        "decode_attempts_by_format": {},
        "command_type_histogram": {},
        "validator_oracle_like_commands_found": 0,
        "hip3_deployer_setoracle_candidates_found": 0,
        "target_dex_candidates_found": 0,
        "flx_priority_candidates_found": 0,
        "oracle_like_candidates_found": 0,
        "raw_candidate_excerpts": [],
        "raw_candidate_hashes": [],
        "decoded_candidate_shapes": [],
        "setoracle_command_found": False,
        "hip3_deployer_setoracle_found": False,
        "schema_confidence": "unknown",
        "proceed_to_parser_allowed": False,
    }
    if not envelope_probe.proceed_to_content_search_allowed:
        return envelope_probe, RawRecordSample(), candidates, schema_result
    # Try to parse records from chunks
    keys_to_try = inventory.candidate_keys[:min(config.max_replica_files, 3)]
    for key in keys_to_try:
        if not budget.check_deadline():
            break
        schema_result["chunks_attempted"] += 1
        try:
            raw = chokepoint.s3_read_object(
                bucket=inventory.bucket,
                key=key,
                requester_pays=config.source.requester_pays,
            )
        except Exception:
            continue
        schema_result["bytes_read"] += len(raw)
        schema_result["source_keys_attempted"] += 1
        # Try decompression if needed
        data = raw
        if envelope_probe.compression_detected != "unknown":
            decompressed = _try_decompress(raw, envelope_probe.compression_detected)
            if decompressed:
                data = decompressed
                schema_result["decode_attempts_by_format"][envelope_probe.compression_detected] = \
                    schema_result["decode_attempts_by_format"].get(envelope_probe.compression_detected, 0) + 1
        # Try to parse as JSONL
        records_parsed = 0
        try:
            lines = data.split(b"\n")
            for line in lines[:500]:  # bounded per chunk
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = _loads_json(line)
                    records_parsed += 1
                    schema_result["decoded_records_seen"] += 1
                    # Search for oracle keywords
                    oracle_hits = _search_dict_for_oracle(obj)
                    if oracle_hits:
                        schema_result["oracle_like_candidates_found"] += 1
                        dex_hits = _search_dict_for_dex(obj)
                        sym_hits = _search_dict_for_symbol(obj)
                        cand = RawOracleCommandCandidate(
                            source_key=key,
                            record_index=records_parsed,
                            raw_excerpt_ascii=str(obj)[:200],
                            shape_keys=list(obj.keys()) if isinstance(obj, dict) else [],
                            shape_depth=1 if isinstance(obj, dict) else 0,
                            is_dict=isinstance(obj, dict),
                            oracle_keyword_hits=oracle_hits[:20],
                            dex_hits=dex_hits[:10],
                            symbol_hits=sym_hits[:10],
                        )
                        cand.hash_sha256 = _hash_bytes(line[:512])
                        # Check if it's an oracle command
                        is_oracle_cmd = any("oracle" in h.lower() for h in oracle_hits)
                        is_setoracle = any("setoracle" in h.lower() for h in oracle_hits)
                        has_target_dex = bool(set(dex_hits) & TARGET_DEX_SET)
                        has_priority_dex = bool(set(dex_hits) & PRIORITY_DEX_SET)
                        if is_oracle_cmd:
                            schema_result["validator_oracle_like_commands_found"] += 1
                        if is_setoracle:
                            schema_result["setoracle_command_found"] = True
                            schema_result["hip3_deployer_setoracle_found"] = True
                            schema_result["hip3_deployer_setoracle_candidates_found"] += 1
                        if has_target_dex:
                            schema_result["target_dex_candidates_found"] += 1
                        if has_priority_dex:
                            schema_result["flx_priority_candidates_found"] += 1
                        # Track histogram by first oracle keyword
                        hist_key = oracle_hits[0] if oracle_hits else "unknown"
                        schema_result["command_type_histogram"][hist_key] = \
                            schema_result["command_type_histogram"].get(hist_key, 0) + 1
                        if len(candidates) < config.max_oracle_commands:
                            candidates.append(cand)
                            if len(schema_result["raw_candidate_excerpts"]) < 10:
                                schema_result["raw_candidate_excerpts"].append(
                                    {"excerpt": str(obj)[:500], "hash": cand.hash_sha256}
                                )
                                schema_result["raw_candidate_hashes"].append(cand.hash_sha256)
                                schema_result["decoded_candidate_shapes"].append(
                                    {"keys": cand.shape_keys, "is_dict": cand.is_dict}
                                )
                except (json.JSONDecodeError, ValueError):
                    continue
        except Exception:
            schema_result["decode_attempts_by_format"]["jsonl_error"] = \
                schema_result["decode_attempts_by_format"].get("jsonl_error", 0) + 1
        if records_parsed > 0:
            schema_result["decode_attempts_by_format"]["jsonl"] = \
                schema_result["decode_attempts_by_format"].get("jsonl", 0) + records_parsed
    # Schema confidence
    if schema_result["hip3_deployer_setoracle_found"]:
        schema_result["schema_confidence"] = "high"
        schema_result["proceed_to_parser_allowed"] = True
    elif schema_result["setoracle_command_found"]:
        schema_result["schema_confidence"] = "medium"
        schema_result["proceed_to_parser_allowed"] = True
    elif schema_result["oracle_like_candidates_found"] > 0:
        schema_result["schema_confidence"] = "low_oracle_only"
        schema_result["proceed_to_parser_allowed"] = False
    else:
        schema_result["schema_confidence"] = "none"
        schema_result["proceed_to_parser_allowed"] = False
    # First record sample for output
    first_sample = envelope_probe.record_samples[0] if envelope_probe.record_samples else RawRecordSample()
    return envelope_probe, first_sample, candidates, schema_result


# ---------------------------------------------------------------------------
# Volume estimate
# ---------------------------------------------------------------------------

def compute_volume_estimate(
    bytes_read: int,
    decoded_records: int,
    oracle_candidates: int,
    hip3_deployer_oracle: int,
) -> ObservedVolumeEstimate:
    """Compute volume reality check from recon sample."""
    est = ObservedVolumeEstimate()
    if bytes_read <= 0:
        return est
    mb = bytes_read / (1024 * 1024)
    est.bytes_per_chunk_observed = bytes_read
    est.records_per_chunk_observed = decoded_records
    est.decoded_records_per_mb = decoded_records / mb if mb > 0 else 0
    est.oracle_candidates_per_mb = oracle_candidates / mb if mb > 0 else 0
    est.hip3_deployer_setoracle_candidates_per_mb = hip3_deployer_oracle / mb if mb > 0 else 0
    # Assume ~24h of data ~ many chunks; rough estimate
    est.estimated_chunks_per_target_day = 1440  # ~1 chunk/min = 1440/day (rough)
    est.estimated_bytes_per_target_day = est.bytes_per_chunk_observed * est.estimated_chunks_per_target_day
    est.estimated_bytes_to_cover_one_overlap_hour = est.bytes_per_chunk_observed * 60  # ~60 chunks/hour
    est.estimated_files_to_cover_one_overlap_hour = 60
    est.validation_budget_recommendation_bytes = int(est.estimated_bytes_to_cover_one_overlap_hour * 1.5)
    est.validation_files_recommendation = 90
    est.validation_run_feasible_under_default_budget = (
        est.estimated_bytes_to_cover_one_overlap_hour <= 250_000_000
    )
    return est


# ---------------------------------------------------------------------------
# Streaming LZ4 JSON-line decoder for full-file decode
# ---------------------------------------------------------------------------

def _iter_lz4_json_records_from_bytes(lz4_data: bytes):
    """Yield parsed JSON objects from an LZ4 frame-compressed byte buffer.

    Decompresses the full buffer at once (LZ4 frame format requires complete
    data), then parses newline-delimited JSON lines with partial-line buffering.
    Yields (record_index, parsed_obj, raw_line_bytes) tuples.
    Yields (record_index, None, raw_line_bytes) for parse failures.
    """
    if _lz4_frame is None:
        raise RuntimeError("lz4 module required for full-file decode")
    # LZ4 frame format requires complete buffer - decompress at once
    try:
        decompressed = _lz4_frame.decompress(lz4_data)
    except Exception as e:
        raise RuntimeError(f"LZ4 decompress failed: {e}")
    # Parse newline-delimited JSON lines
    partial_line = b""
    record_index = 0
    chunk_size = 256 * 1024  # 256KB processing chunks
    offset = 0
    while offset < len(decompressed):
        end = min(offset + chunk_size, len(decompressed))
        partial_line += decompressed[offset:end]
        offset = end
        while b"\n" in partial_line:
            line, partial_line = partial_line.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                obj = _loads_json(line)
                yield (record_index, obj, line)
            except Exception:
                yield (record_index, None, line)
            record_index += 1
    # Process any remaining partial line
    if partial_line.strip():
        line = partial_line.strip()
        try:
            obj = _loads_json(line)
            yield (record_index, obj, line)
        except Exception:
            yield (record_index, None, line)


def _parse_perpdeploy_setoracle_payload(record: dict, record_index: int,
                                         source_key: str) -> List[dict]:
    """Extract perpDeploy.setOracle payloads from a decoded record.

    Returns list of payload dicts with keys:
      dex, api_symbol, display_symbol, oracle_price, mark_pxs, record_index, etc.
    """
    results = []
    # Navigate action bundle structure - try multiple paths
    action = record.get("action", {})
    if not isinstance(action, dict) or not action:
        # Try top-level perpDeploy
        if record.get("type") == "perpDeploy" or record.get("actionType") == "perpDeploy":
            action = record
        elif "setOracle" in record and isinstance(record.get("setOracle"), dict):
            # setOracle at top level
            action = record
        else:
            return results
    action_type = action.get("type", action.get("actionType", ""))
    if action_type != "perpDeploy":
        # Also check if setOracle is present (fallback)
        if "setOracle" not in action:
            return results
    # Navigate to setOracle
    set_oracle = action.get("setOracle", record.get("setOracle", {}))
    if not isinstance(set_oracle, dict):
        return results
    oracle_pxs = set_oracle.get("oraclePxs", [])
    mark_pxs = set_oracle.get("markPxs", [])
    if not isinstance(oracle_pxs, list):
        return results
    # Extract per-market oracle prices
    # oraclePxs is either:
    #   - list of [market, price] tuples (actual S3 format)
    #   - list of {coin, px} dicts (alternative format)
    for i, entry in enumerate(oracle_pxs):
        coin = ""
        px = None
        if isinstance(entry, list) and len(entry) >= 2:
            # Tuple format: ["cash:TSLA", "435.070728"]
            coin = str(entry[0])
            px = entry[1]
        elif isinstance(entry, dict):
            # Dict format: {"coin": "cash:TSLA", "px": 435.07}
            coin = entry.get("coin", entry.get("name", entry.get("symbol", "")))
            px = entry.get("px", entry.get("price", entry.get("oraclePx", None)))
        else:
            continue
        if not coin:
            continue
        # Parse dex:symbol
        dex = ""
        display_symbol = ""
        if ":" in str(coin):
            parts = str(coin).split(":", 1)
            dex = parts[0].lower()
            display_symbol = parts[1].upper()
        else:
            display_symbol = str(coin).upper()
        # Corresponding mark price
        mark_px = None
        if isinstance(mark_pxs, list) and i < len(mark_pxs):
            mpe = mark_pxs[i]
            if isinstance(mpe, dict):
                mark_px = mpe.get("px", mpe.get("price", None))
            elif isinstance(mpe, list) and len(mpe) >= 2:
                # Tuple format: [[market, price], ...]
                if isinstance(mpe[0], list):
                    # Nested array of tuples: [[market, price], [market, price]]
                    mark_px = _safe_float(mpe[0][1]) if len(mpe[0]) >= 2 else None
                else:
                    mark_px = _safe_float(mpe[1])
            elif isinstance(mpe, (int, float)):
                mark_px = mpe
        payload = {
            "dex": dex,
            "display_symbol": display_symbol,
            "api_symbol": f"{dex}:{display_symbol}" if dex else display_symbol,
            "oracle_price": _safe_float(px),
            "mark_price": _safe_float(mark_px),
            "record_index": record_index,
            "source_key": source_key,
            "raw_command_hash": _hash_bytes(_dumps_json(record)[:512]),
            "timestamp": record.get("timestamp", record.get("ts", None)),
            "block": record.get("block", record.get("blockNumber", None)),
        }
        results.append(payload)
    return results


def build_full_file_histogram(
    chokepoint,
    config: ReplicaCmdsProbeConfig,
    source_key: str,
    source_size: int,
    budget: RuntimeBudgetState,
) -> FullFileHistogramResult:
    """Download and decode full LZ4 file, build per-DEX oracle histogram."""
    result = FullFileHistogramResult(
        selected_source_key=source_key,
        selected_block=config.target_block or "",
        target_date=config.target_date or "",
    )
    # Safety cap check
    if source_size > config.max_full_file_bytes:
        result.status = "REPLICA_CMDS_FULL_FILE_SIZE_CAP_EXCEEDED"
        result.limitations.append(
            f"Object size {source_size} exceeds max_full_file_bytes {config.max_full_file_bytes}"
        )
        return result
    # Download
    try:
        data = chokepoint.s3_read_object(
            bucket=config.source.bucket,
            key=source_key,
            requester_pays=config.source.requester_pays,
        )
    except Exception as e:
        result.status = "REPLICA_CMDS_FULL_FILE_DECODE_FAILED"
        result.limitations.append(f"S3 download failed: {e}")
        return result
    result.bytes_downloaded = len(data)
    if len(data) > config.max_full_file_bytes:
        result.status = "REPLICA_CMDS_FULL_FILE_SIZE_CAP_EXCEEDED"
        result.limitations.append(
            f"Downloaded {len(data)} bytes exceeds max {config.max_full_file_bytes}"
        )
        return result
    # Decode LZ4
    if _lz4_frame is None:
        result.status = "REPLICA_CMDS_FULL_FILE_DECODE_FAILED"
        result.limitations.append("lz4 module not available")
        return result
    # Accumulate histogram data
    action_type_histogram: Dict[str, int] = defaultdict(int)
    perpdeploy_count = 0
    setoracle_count = 0
    oracle_pxs_count = 0
    mark_pxs_count = 0
    dex_counts: Dict[str, int] = defaultdict(int)
    oracle_pxs_by_dex: Dict[str, int] = defaultdict(int)
    markets_by_dex: Dict[str, set] = defaultdict(set)
    target_markets: set = set()
    first_idx_by_dex: Dict[str, int] = {}
    last_idx_by_dex: Dict[str, int] = {}
    candidates_by_dex: Dict[str, list] = defaultdict(list)
    hashes_by_dex: Dict[str, list] = defaultdict(list)
    timestamps_by_dex: Dict[str, list] = defaultdict(list)
    parse_failures = 0
    malformed = 0
    records_decoded = 0
    stop_reached = False
    try:
        for rec_idx, obj, raw_line in _iter_lz4_json_records_from_bytes(data):
            if stop_reached:
                break
            if not budget.check_deadline():
                result.limitations.append("runtime_budget_exceeded")
                break
            records_decoded += 1
            if obj is None:
                parse_failures += 1
                continue
            if not isinstance(obj, dict):
                malformed += 1
                continue
            # Extract perpDeploy.setOracle from ABCI block structure:
            # obj -> abci_block -> signed_action_bundles[i] -> [sig, {signed_actions}]
            #   -> signed_actions[j] -> {action: {type: "perpDeploy", setOracle: {...}}}
            abci = obj.get("abci_block", {})
            if not isinstance(abci, dict):
                continue
            bundles = abci.get("signed_action_bundles", [])
            if not isinstance(bundles, list):
                continue
            # Count total action types for histogram
            for b in bundles:
                if isinstance(b, list) and len(b) >= 2:
                    action_data = b[1]
                    if isinstance(action_data, dict):
                        for sa in action_data.get("signed_actions", []):
                            if isinstance(sa, dict):
                                action = sa.get("action", {})
                                if isinstance(action, dict):
                                    at = action.get("type", "unknown")
                                    action_type_histogram[at] += 1
                                else:
                                    action_type_histogram["unknown"] += 1
            # Now find perpDeploy actions specifically
            for b_idx, b in enumerate(bundles):
                if not isinstance(b, list) or len(b) < 2:
                    continue
                action_data = b[1]
                if not isinstance(action_data, dict):
                    continue
                signed_actions = action_data.get("signed_actions", [])
                if not isinstance(signed_actions, list):
                    continue
                for sa_idx, sa in enumerate(signed_actions):
                    if not isinstance(sa, dict):
                        continue
                    action = sa.get("action", {})
                    if not isinstance(action, dict):
                        continue
                    if action.get("type") != "perpDeploy":
                        continue
                    perpdeploy_count += 1
                    # Extract setOracle payloads from this action
                    set_oracle = action.get("setOracle", {})
                    if not isinstance(set_oracle, dict):
                        continue
                    oracle_pxs = set_oracle.get("oraclePxs", [])
                    if not isinstance(oracle_pxs, list):
                        continue
                    setoracle_count += 1
                    # Extract per-market oracle prices
                    for i, entry in enumerate(oracle_pxs):
                        coin = ""
                        px = None
                        if isinstance(entry, list) and len(entry) >= 2:
                            coin = str(entry[0])
                            px = entry[1]
                        elif isinstance(entry, dict):
                            coin = entry.get("coin", entry.get("name", entry.get("symbol", "")))
                            px = entry.get("px", entry.get("price", entry.get("oraclePx", None)))
                        else:
                            continue
                        if not coin:
                            continue
                        # Parse dex:symbol
                        dex = ""
                        display_symbol = ""
                        if ":" in str(coin):
                            parts = str(coin).split(":", 1)
                            dex = parts[0].lower()
                            display_symbol = parts[1].upper()
                        else:
                            display_symbol = str(coin).upper()
                        # Corresponding mark price
                        mark_pxs = set_oracle.get("markPxs", [])
                        mark_px = None
                        if isinstance(mark_pxs, list) and i < len(mark_pxs):
                            mpe = mark_pxs[i]
                            if isinstance(mpe, dict):
                                mark_px = mpe.get("px", mpe.get("price", None))
                            elif isinstance(mpe, list) and len(mpe) >= 2:
                                if isinstance(mpe[0], list):
                                    mark_px = _safe_float(mpe[0][1]) if len(mpe[0]) >= 2 else None
                                else:
                                    mark_px = _safe_float(mpe[1])
                            elif isinstance(mpe, (int, float)):
                                mark_px = mpe
                        oracle_pxs_count += 1
                        if dex:
                            dex_counts[dex] += 1
                            oracle_pxs_by_dex[dex] += 1
                            if dex not in markets_by_dex:
                                markets_by_dex[dex] = set()
                            markets_by_dex[dex].add(f"{dex}:{display_symbol}" if display_symbol else "")
                        if mark_px is not None:
                            mark_pxs_count += 1
                        if dex and dex not in first_idx_by_dex:
                            first_idx_by_dex[dex] = rec_idx
                        if dex:
                            last_idx_by_dex[dex] = rec_idx
                        api_sym = f"{dex}:{display_symbol}" if dex else display_symbol
                        if len(candidates_by_dex.get(dex, [])) < 5:
                            candidates_by_dex[dex].append({
                                "record_index": rec_idx,
                                "bundle_index": b_idx,
                                "action_index": sa_idx,
                                "api_symbol": api_sym,
                                "oracle_price": _safe_float(px),
                                "mark_price": _safe_float(mark_px),
                                "dex": dex,
                            })
                        hashes_by_dex[dex].append(_hash_bytes(raw_line[:512]))
                        if api_sym in config.markets:
                            target_markets.add(api_sym)
                        if dex == "flx" and display_symbol == "TSLA":
                            result.flx_tsla_found = True
                        if dex == "flx" and display_symbol == "NVDA":
                            result.flx_nvda_found = True
                        if dex == "cash" and display_symbol == "TSLA":
                            result.cash_tsla_found = True
                        if dex == "cash" and display_symbol == "NVDA":
                            result.cash_nvda_found = True
            if config.stop_after_setoracle_payloads and setoracle_count >= config.stop_after_setoracle_payloads:
                stop_reached = True
    except Exception as e:
        result.status = "REPLICA_CMDS_FULL_FILE_DECODE_FAILED"
        result.limitations.append(f"Decode error: {type(e).__name__}: {e}")
        return result
    # Fill result
    result.bytes_decompressed_estimate_or_actual = result.bytes_downloaded
    result.records_decoded = records_decoded
    result.parse_failures = parse_failures
    result.malformed_records = malformed
    result.action_bundles_seen = records_decoded
    result.unique_action_types = len(action_type_histogram)
    result.full_action_type_histogram = dict(action_type_histogram)
    result.perpDeploy_count = perpdeploy_count
    result.setOracle_payload_count = setoracle_count
    result.oraclePxs_pair_count = oracle_pxs_count
    result.markPxs_pair_count = mark_pxs_count
    result.dexes_seen = sorted(dex_counts.keys())
    result.setOracle_payloads_by_dex = dict(dex_counts)
    result.oraclePxs_pairs_by_dex = dict(oracle_pxs_by_dex)
    result.markets_seen_by_dex = {k: sorted(v) for k, v in markets_by_dex.items()}
    result.target_markets_seen = sorted(target_markets)
    result.flx_seen = "flx" in dex_counts
    result.cash_seen = "cash" in dex_counts
    result.xyz_seen = "xyz" in dex_counts
    result.km_seen = "km" in dex_counts
    result.first_setOracle_record_index_by_dex = first_idx_by_dex
    result.last_setOracle_record_index_by_dex = last_idx_by_dex
    result.bounded_candidate_examples_by_dex = {k: v for k, v in candidates_by_dex.items()}
    result.candidate_hashes_by_dex = {k: v for k, v in hashes_by_dex.items()}
    # Compute cadence by DEX
    cadence_by_dex: Dict[str, dict] = {}
    for dex, ts_list in timestamps_by_dex.items():
        if len(ts_list) < 2:
            cadence_by_dex[dex] = {
                "update_count": len(ts_list),
                "first_timestamp_utc": ts_list[0] if ts_list else None,
                "last_timestamp_utc": ts_list[-1] if ts_list else None,
                "span_seconds": None,
                "updates_per_hour": None,
                "median_inter_update_seconds": None,
                "p90_inter_update_seconds": None,
            }
        else:
            parsed = []
            for ts in ts_list:
                dt = _parse_ts(str(ts))
                if dt:
                    parsed.append(dt)
            if len(parsed) >= 2:
                parsed.sort()
                span = (parsed[-1] - parsed[0]).total_seconds()
                gaps = [(parsed[i+1] - parsed[i]).total_seconds() for i in range(len(parsed)-1)]
                gaps.sort()
                median_gap = gaps[len(gaps)//2] if gaps else None
                p90_gap = gaps[int(len(gaps)*0.9)] if gaps else None
                updates_per_hour = len(parsed) / (span / 3600) if span > 0 else None
                cadence_by_dex[dex] = {
                    "update_count": len(parsed),
                    "first_timestamp_utc": parsed[0].isoformat(),
                    "last_timestamp_utc": parsed[-1].isoformat(),
                    "span_seconds": span,
                    "updates_per_hour": updates_per_hour,
                    "median_inter_update_seconds": median_gap,
                    "p90_inter_update_seconds": p90_gap,
                }
            else:
                cadence_by_dex[dex] = {
                    "update_count": len(ts_list),
                    "first_timestamp_utc": str(ts_list[0]) if ts_list else None,
                    "last_timestamp_utc": str(ts_list[-1]) if ts_list else None,
                    "span_seconds": None,
                    "updates_per_hour": None,
                    "median_inter_update_seconds": None,
                    "p90_inter_update_seconds": None,
                    "limitation": "timestamp_parsing_failed",
                }
    result.inferred_update_cadence_by_dex = cadence_by_dex
    # Parser confidence
    if setoracle_count > 0 and perpdeploy_count > 0:
        result.parser_confidence = "high"
    elif perpdeploy_count > 0:
        result.parser_confidence = "medium"
    else:
        result.parser_confidence = "low"
    # Status and conclusion
    if setoracle_count > 0:
        if result.flx_seen:
            result.status = "FLX_SETORACLE_FOUND_FULL_FILE_HISTOGRAM"
            result.conclusion = (
                f"FLX setOracle found in full file. {dex_counts.get('flx', 0)} flx oracle updates. "
                f"Full file histogram complete."
            )
        else:
            result.status = "REPLICA_CMDS_FULL_FILE_HISTOGRAM_COMPLETE"
            result.conclusion = (
                f"perpDeploy.setOracle found ({setoracle_count} payloads) but flx not seen. "
                f"DEXes: {sorted(dex_counts.keys())}. Full file histogram complete."
            )
    elif perpdeploy_count > 0:
        result.status = "NO_SETORACLE_IN_SELECTED_FULL_FILE"
        result.conclusion = f"perpDeploy found ({perpdeploy_count}) but no setOracle payloads."
    else:
        result.status = "NO_SETORACLE_IN_SELECTED_FULL_FILE"
        result.conclusion = "No perpDeploy actions found in full file."
    # Absence claim
    if not result.flx_seen and setoracle_count > 0:
        result.absence_claim_supported_by_full_file = False
        result.limitations.append(
            "FLX not seen in one full file; global absence not claimed"
        )
    elif not result.flx_seen and perpdeploy_count == 0:
        result.absence_claim_supported_by_full_file = False
        result.limitations.append(
            "No perpDeploy actions in file; cannot assess FLX absence"
        )
    return result


# ---------------------------------------------------------------------------
# Parser: decode setOracle commands from replica_cmds
# ---------------------------------------------------------------------------

def _classify_command(obj: dict) -> str:
    """Classify a decoded record as validator_oracle, hip3_deployer_setOracle, or unknown."""
    if not isinstance(obj, dict):
        return "unknown"
    # Check for setOracle action
    action = str(obj.get("action", "")).lower()
    if "setoracle" in action or "set_oracle" in action:
        return "hip3_deployer_setOracle"
    # Check nested fields
    action_type = str(obj.get("type", "")).lower()
    if "setoracle" in action_type:
        return "hip3_deployer_setOracle"
    # Check for oracle-like fields
    keys_lower = {k.lower() for k in obj.keys()}
    oracle_keys = {"oraclepx", "oracle_px", "oracle", "setoracle", "set_oracle"}
    if keys_lower & oracle_keys:
        return "oracle_like"
    # Check nested
    for k, v in obj.items():
        if isinstance(v, dict):
            inner_keys = {ik.lower() for ik in v.keys()}
            if inner_keys & oracle_keys:
                return "oracle_like"
    return "unknown"


def _extract_oracle_price(obj: dict) -> Optional[float]:
    """Extract oracle price from various field name patterns."""
    for key in ("oraclePx", "oracle_px", "oraclePrice", "oracle_price", "setOracle", "price", "px"):
        if key in obj:
            return _safe_float(obj[key])
    # Nested
    for v in obj.values():
        if isinstance(v, dict):
            for key in ("oraclePx", "oracle_px", "oraclePrice", "oracle_price", "price", "px"):
                if key in v:
                    return _safe_float(v[key])
    return None


def _extract_dex(obj: dict) -> str:
    """Extract DEX name from various field patterns."""
    for key in ("dex", "dexName", "dex_name", "perpDex", "perp_dex", "builder"):
        if key in obj:
            return str(obj[key]).strip().lower()
    # Check for namespaced symbol like "flx:TSLA"
    for key in ("name", "symbol", "coin"):
        val = str(obj.get(key, ""))
        if ":" in val:
            return val.split(":", 1)[0].strip().lower()
    return ""


def _extract_symbol(obj: dict) -> Tuple[str, str]:
    """Extract display_symbol and api_symbol from obj."""
    # Direct fields
    for key in ("coin", "name", "symbol", "displaySymbol", "display_symbol"):
        val = str(obj.get(key, ""))
        if ":" in val:
            parts = val.split(":", 1)
            return (parts[0].lower(), parts[1].upper())
        elif val and val.upper() in TARGET_DISPLAY_SYMBOLS:
            return ("", val.upper())
    # Nested
    for v in obj.values():
        if isinstance(v, str) and ":" in v:
            parts = v.split(":", 1)
            if parts[1].upper() in TARGET_DISPLAY_SYMBOLS:
                return (parts[0].lower(), parts[1].upper())
    return ("", "")


def parse_setoracle_commands(
    data: bytes,
    source_key: str,
    envelope_format: str,
) -> List[DecodedSetOracleCommand]:
    """Parse replica_cmds data for oracle/setOracle commands."""
    results = []
    # Try to split into records
    records = []
    if envelope_format == "newline_delimited":
        lines = data.split(b"\n")
        records = [line for line in lines if line.strip()]
    elif envelope_format == "json_array_or_object":
        # Try as JSONL first, then as single JSON
        try:
            obj = _loads_json(data)
            if isinstance(obj, list):
                records = [_dumps_json(item) for item in obj[:500]]
            elif isinstance(obj, dict):
                records = [_dumps_json(obj)]
        except Exception:
            lines = data.split(b"\n")
            records = [line for line in lines if line.strip()]
    else:
        # Try msgpack or fallback to newline
        try:
            import msgpack
            obj = msgpack.unpackb(data, raw=False)
            if isinstance(obj, list):
                records = [_dumps_json(item) for item in obj[:500]]
            elif isinstance(obj, dict):
                records = [_dumps_json(obj)]
        except Exception:
            lines = data.split(b"\n")
            records = [line for line in lines if line.strip()]
    for idx, record_bytes in enumerate(records[:500]):
        try:
            obj = _loads_json(record_bytes)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        category = _classify_command(obj)
        if category == "unknown":
            continue
        # Extract fields
        dex = _extract_dex(obj)
        display_sym, api_sym_from_obj = _extract_symbol(obj)
        oracle_px = _extract_oracle_price(obj)
        # Try to get block/timestamp
        block_num = _safe_int(obj.get("blockNumber", obj.get("block_number", obj.get("block"))))
        block_ts = obj.get("blockTimestamp", obj.get("block_timestamp", obj.get("timestamp", obj.get("ts"))))
        action_type = str(obj.get("action", obj.get("type", obj.get("actionType", ""))))
        # Mark prices
        mark_pxs = []
        for mk in ("markPxs", "mark_prices", "markPx", "mark_price"):
            if mk in obj:
                mpv = obj[mk]
                if isinstance(mpv, list):
                    mark_pxs = mpv
                elif mpv is not None:
                    mark_pxs = [mpv]
        # Build command
        # Reconstruct api_symbol: prefer dex:display_sym, fallback to extracted
        if dex and display_sym:
            resolved_api_symbol = f"{dex}:{display_sym}"
        elif dex and api_sym_from_obj:
            resolved_api_symbol = f"{dex}:{api_sym_from_obj}"
        else:
            resolved_api_symbol = api_sym_from_obj or ""
        cmd = DecodedSetOracleCommand(
            block_number=block_num,
            block_timestamp=str(block_ts) if block_ts else None,
            source_key=source_key,
            command_index=idx,
            raw_action_type=action_type,
            command_category=category,
            dex=dex,
            display_symbol=display_sym or api_sym_from_obj,
            api_symbol=resolved_api_symbol,
            submitted_oracle_px=oracle_px,
            mark_pxs_count=len(mark_pxs),
            mark_pxs_excerpt_or_hash=_hash_bytes(json.dumps(mark_pxs[:5]).encode()) if mark_pxs else "",
            raw_command_hash=_hash_bytes(record_bytes[:512]),
            parser_version=PARSER_VERSION,
            decode_status="ok",
            confidence_class="high" if category == "hip3_deployer_setOracle" else "medium",
        )
        results.append(cmd)
    return results


# ---------------------------------------------------------------------------
# Forward recorder overlap loader
# ---------------------------------------------------------------------------

def load_forward_recorder_points(
    forward_root: Path,
    target_markets: frozenset,
    run_dir: Path,
) -> Tuple[List[ForwardRecorderOraclePoint], dict]:
    """Load forward recorder oracle points from JSONL files."""
    points: List[ForwardRecorderOraclePoint] = []
    inventory: dict = {
        "files_discovered": 0,
        "oracle_bearing_files": 0,
        "rows_loaded": 0,
        "rows_by_api_symbol": {},
        "first_timestamp_utc": None,
        "last_timestamp_utc": None,
        "null_oracle_count": 0,
        "target_markets_present": set(),
        "overlap_ready": False,
    }
    if not forward_root or not forward_root.exists():
        return points, inventory
    # Search for asset_context_snapshots JSONL files
    jsonl_files = list(forward_root.rglob("asset_context_snapshots/*.jsonl"))
    inventory["files_discovered"] = len(jsonl_files)
    for fp in jsonl_files:
        try:
            with open(fp, "rb") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = _loads_json(line)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    api_sym = obj.get("api_symbol", "")
                    if api_sym not in target_markets:
                        continue
                    dex = obj.get("dex_name", obj.get("dex", ""))
                    display = obj.get("display_symbol", obj.get("display", ""))
                    ts = obj.get("timestamp_utc", "")
                    oracle_px = _safe_float(obj.get("oracle_price", obj.get("oraclePx")))
                    mark_px = _safe_float(obj.get("mark_price", obj.get("markPx")))
                    mid_px = _safe_float(obj.get("mid_price", obj.get("midPx")))
                    pt = ForwardRecorderOraclePoint(
                        timestamp_utc=ts,
                        api_symbol=api_sym,
                        dex=dex,
                        display_symbol=display,
                        oracle_price=oracle_px,
                        mark_price=mark_px,
                        mid_price=mid_px,
                    )
                    points.append(pt)
                    inventory["rows_loaded"] += 1
                    inventory["rows_by_api_symbol"][api_sym] = \
                        inventory["rows_by_api_symbol"].get(api_sym, 0) + 1
                    inventory["target_markets_present"].add(api_sym)
                    if oracle_px is None:
                        inventory["null_oracle_count"] += 1
                    if ts:
                        if inventory["first_timestamp_utc"] is None or ts < inventory["first_timestamp_utc"]:
                            inventory["first_timestamp_utc"] = ts
                        if inventory["last_timestamp_utc"] is None or ts > inventory["last_timestamp_utc"]:
                            inventory["last_timestamp_utc"] = ts
            inventory["oracle_bearing_files"] += 1
        except Exception:
            continue
    inventory["target_markets_present"] = list(inventory["target_markets_present"])
    inventory["overlap_ready"] = (
        inventory["rows_loaded"] > 0 and len(inventory["target_markets_present"]) > 0
    )
    return points, inventory


# ---------------------------------------------------------------------------
# Overlap validation (per-API symbol / per-DEX)
# ---------------------------------------------------------------------------

def _parse_ts(ts: str) -> Optional[datetime]:
    """Parse ISO timestamp string."""
    if not ts:
        return None
    try:
        # Handle various formats
        ts_clean = ts.rstrip("Z").replace("+00:00", "")
        if "." in ts_clean:
            return datetime.strptime(ts_clean, "%Y-%m-%dT%H:%M:%S.%f")
        return datetime.strptime(ts_clean, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return None


def _bps_diff(a: float, b: float) -> float:
    """Compute absolute bps difference."""
    if a == 0 or b == 0:
        return 0.0
    return abs(a - b) / min(abs(a), abs(b)) * 10000.0


def validate_overlap(
    reconstructed: List[DecodedSetOracleCommand],
    forward_points: List[ForwardRecorderOraclePoint],
    tolerance_5s: float = 5.0,
    tolerance_60s: float = 60.0,
) -> ForwardOverlapValidationResult:
    """Per-API-symbol / per-DEX overlap validation."""
    result = ForwardOverlapValidationResult()
    # Group forward points by (api_symbol, dex)
    fwd_by_key: dict = {}
    for pt in forward_points:
        key = (pt.api_symbol, pt.dex)
        fwd_by_key.setdefault(key, []).append(pt)
    # Sort forward points by timestamp for each key
    for key in fwd_by_key:
        fwd_by_key[key].sort(key=lambda p: p.timestamp_utc)
    # Group reconstructed by (api_symbol, dex)
    recon_by_key: dict = {}
    for cmd in reconstructed:
        if not cmd.api_symbol or not cmd.dex:
            continue
        key = (cmd.api_symbol, cmd.dex)
        recon_by_key.setdefault(key, []).append(cmd)
    # For each (api_symbol, dex) pair, compute validation
    all_keys = set(fwd_by_key.keys()) | set(recon_by_key.keys())
    for api_sym, dex in sorted(all_keys):
        fwd_list = fwd_by_key.get((api_sym, dex), [])
        recon_list = recon_by_key.get((api_sym, dex), [])
        pdr = PerDexOverlapValidationResult(api_symbol=api_sym, dex=dex)
        if not fwd_list or not recon_list:
            pdr.validation_status = "UNAVAILABLE"
            result.unavailable_count += 1
            result.per_dex_results.append(pdr)
            continue
        # Join: for each reconstructed point, find nearest forward point
        joined_5s = []
        joined_60s = []
        gap_seconds_list = []
        matched_recon_px = []
        matched_fwd_px = []
        mismatched = []
        for rc in recon_list:
            if rc.block_timestamp is None:
                continue
            rc_ts = _parse_ts(rc.block_timestamp)
            if rc_ts is None:
                continue
            # Find nearest forward point
            best_fwd = None
            best_gap = float("inf")
            for fp in fwd_list:
                fp_ts = _parse_ts(fp.timestamp_utc)
                if fp_ts is None:
                    continue
                gap = abs((rc_ts - fp_ts).total_seconds())
                if gap < best_gap:
                    best_gap = gap
                    best_fwd = fp
            if best_fwd is None or best_gap > tolerance_60s:
                continue
            if best_gap <= tolerance_5s:
                joined_5s.append((rc, best_fwd, best_gap))
            if best_gap <= tolerance_60s:
                joined_60s.append((rc, best_fwd, best_gap))
            gap_seconds_list.append(best_gap)
            if rc.submitted_oracle_px is not None and best_fwd.oracle_price is not None:
                matched_recon_px.append(rc.submitted_oracle_px)
                matched_fwd_px.append(best_fwd.oracle_price)
                bps = _bps_diff(rc.submitted_oracle_px, best_fwd.oracle_price)
                if bps > 50:  # > 50bps = mismatch
                    mismatched.append({
                        "api_symbol": api_sym,
                        "dex": dex,
                        "recon_oracle": rc.submitted_oracle_px,
                        "forward_oracle": best_fwd.oracle_price,
                        "gap_seconds": best_gap,
                        "bps_diff": bps,
                        "block_timestamp": rc.block_timestamp,
                        "forward_timestamp": best_fwd.timestamp_utc,
                    })
        pdr.joined_count_5s = len(joined_5s)
        pdr.joined_count_60s = len(joined_60s)
        if gap_seconds_list:
            sorted_gaps = sorted(gap_seconds_list)
            pdr.median_timestamp_gap_seconds = sorted_gaps[len(sorted_gaps) // 2]
            pdr.p90_timestamp_gap_seconds = sorted_gaps[int(len(sorted_gaps) * 0.9)]
        pdr.reconstructed_oracle_px = matched_recon_px
        pdr.forward_oracle_px = matched_fwd_px
        if matched_recon_px and matched_fwd_px:
            bps_diffs = [_bps_diff(a, b) for a, b in zip(matched_recon_px, matched_fwd_px)]
            sorted_bps = sorted(bps_diffs)
            pdr.median_abs_bps_diff = sorted_bps[len(sorted_bps) // 2] if sorted_bps else 0.0
            pdr.p90_abs_bps_diff = sorted_bps[int(len(sorted_bps) * 0.9)] if sorted_bps else 0.0
            pdr.max_abs_bps_diff = max(bps_diffs) if bps_diffs else 0.0
            exact_matches = sum(1 for d in bps_diffs if d < 10)
            pdr.exact_near_match_rate = exact_matches / len(bps_diffs) if bps_diffs else 0.0
        pdr.mismatched_rows_sample = mismatched[:5]
        # Determine validation status
        if pdr.joined_count_5s == 0:
            pdr.validation_status = "UNAVAILABLE"
            result.unavailable_count += 1
        elif pdr.max_abs_bps_diff < 10:
            pdr.validation_status = "VALIDATED"
            result.validated_count += 1
        elif pdr.median_abs_bps_diff < 100:
            # Could be protocol transform - document it
            pdr.validation_status = "TRANSFORM_REQUIRED"
            result.transform_required_count += 1
        else:
            pdr.validation_status = "MISMATCH"
            result.mismatch_count += 1
        result.per_dex_results.append(pdr)
    # Overall status
    if result.mismatch_count > 0:
        result.status = "REPLICA_CMDS_FORWARD_OVERLAP_MISMATCH"
    elif result.transform_required_count > 0 and result.validated_count == 0:
        result.status = "REPLICA_CMDS_FORWARD_OVERLAP_TRANSFORM_REQUIRED"
    elif result.validated_count > 0:
        result.status = "REPLICA_CMDS_FORWARD_OVERLAP_VALIDATED"
    else:
        result.status = "REPLICA_CMDS_FORWARD_OVERLAP_UNAVAILABLE"
    return result


# ---------------------------------------------------------------------------
# FLX corroboration probe
# ---------------------------------------------------------------------------

def compute_flx_corroboration(
    overlap_result: ForwardOverlapValidationResult,
) -> dict:
    """Compute FLX-specific corroboration finding."""
    corroboration = {
        "flx_tsla_setoracle_found": False,
        "flx_nvda_setoracle_found": False,
        "flx_tsla_overlap_joined_count": 0,
        "flx_nvda_overlap_joined_count": 0,
        "flx_tsla_validation_status": "UNAVAILABLE",
        "flx_nvda_validation_status": "UNAVAILABLE",
        "flx_tsla_oracle_convergence_observed": False,
        "flx_nvda_oracle_convergence_observed": False,
        "corroborates_forward_oracle_level_shift": False,
        "conclusion": "",
    }
    for pdr in overlap_result.per_dex_results:
        if pdr.api_symbol == "flx:TSLA":
            corroboration["flx_tsla_setoracle_found"] = pdr.joined_count_5s > 0 or pdr.joined_count_60s > 0
            corroboration["flx_tsla_overlap_joined_count"] = max(pdr.joined_count_5s, pdr.joined_count_60s)
            corroboration["flx_tsla_validation_status"] = pdr.validation_status
            if pdr.validation_status == "VALIDATED":
                corroboration["flx_tsla_oracle_convergence_observed"] = True
            elif pdr.validation_status == "TRANSFORM_REQUIRED":
                # If transform required, convergence may be observed after transform
                corroboration["flx_tsla_oracle_convergence_observed"] = True
        elif pdr.api_symbol == "flx:NVDA":
            corroboration["flx_nvda_setoracle_found"] = pdr.joined_count_5s > 0 or pdr.joined_count_60s > 0
            corroboration["flx_nvda_overlap_joined_count"] = max(pdr.joined_count_5s, pdr.joined_count_60s)
            corroboration["flx_nvda_validation_status"] = pdr.validation_status
            if pdr.validation_status == "VALIDATED":
                corroboration["flx_nvda_oracle_convergence_observed"] = True
            elif pdr.validation_status == "TRANSFORM_REQUIRED":
                corroboration["flx_nvda_oracle_convergence_observed"] = True
    # Determine if FLX corroborates level shift
    if (corroboration["flx_tsla_oracle_convergence_observed"] and
            corroboration["flx_nvda_oracle_convergence_observed"]):
        corroboration["corroborates_forward_oracle_level_shift"] = True
        corroboration["conclusion"] = (
            "FLX_ORACLE_LEVEL_SHIFT_CORROBORATED_DIAGNOSTIC: "
            "Both flx:TSLA and flx:NVDA setOracle reconstructed and showed "
            "convergence/pattern consistent with forward recorder. "
            "This is diagnostic only, not edge confirmation."
        )
    elif corroboration["flx_tsla_setoracle_found"] or corroboration["flx_nvda_setoracle_found"]:
        corroboration["conclusion"] = (
            "FLX setOracle partially reconstructed. "
            "Further analysis needed to determine if level shift pattern is corroborated."
        )
    else:
        corroboration["conclusion"] = (
            "No FLX setOracle commands found in replica_cmds. "
            "Forward recorder FLX oracle behavior cannot be independently corroborated from this source."
        )
    return corroboration


# ---------------------------------------------------------------------------
# Backfill: bounded reconstruction after validation
# ---------------------------------------------------------------------------

def run_bounded_backfill(
    chokepoint: NetworkChokepoint,
    config: ReplicaCmdsProbeConfig,
    budget: RuntimeBudgetState,
    overlap_result: ForwardOverlapValidationResult,
    output_dir: Path,
) -> dict:
    """Run bounded historical reconstruction for validated markets only."""
    manifest = {
        "source_kind": "REPLICA_CMDS_SETORACLE_DEPLOYER_SUBMITTED",
        "parser_version": PARSER_VERSION,
        "validation_status_by_api_symbol": {},
        "source_keys_processed": 0,
        "bytes_processed": 0,
        "oracle_points_written": 0,
        "by_api_symbol": {},
        "by_dex": {},
        "first_timestamp_utc": None,
        "last_timestamp_utc": None,
        "output_format": "jsonl",
        "output_path": "",
        "raw_kept": False,
        "limitations": [
            "Bounded to one overlap day only",
            "No SonarX residual join",
            "No PnL/returns/signals",
            "No Phase 0 precommitment",
        ],
    }
    # Identify validated markets
    validated_markets = set()
    for pdr in overlap_result.per_dex_results:
        if pdr.validation_status == "VALIDATED":
            validated_markets.add(pdr.api_symbol)
    manifest["validation_status_by_api_symbol"] = {
        pdr.api_symbol: pdr.validation_status
        for pdr in overlap_result.per_dex_results
    }
    if not validated_markets:
        manifest["limitations"].append("No validated markets for backfill")
        return manifest
    # For now, return manifest without running actual backfill
    # (bounded backfill is only triggered by explicit --backfill-after-validation)
    manifest["limitations"].append("Bounded backfill deferred to explicit command")
    return manifest


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="HIP-3 replica_cmds setOracle reconstruction scout",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--out-root", type=str, default="reports/hip3_replica_cmds_setoracle_reconstruction_v0",
                    help="Output root directory")
    p.add_argument("--target-date", type=str, default=None,
                    help="Target date YYYY-MM-DD for chunk selection")
    p.add_argument("--markets", type=str, default=None,
                    help="Comma-separated API symbols (e.g. flx:TSLA,xyz:NVDA)")
    p.add_argument("--dexes", type=str, default=None,
                    help="Comma-separated DEX names (e.g. flx,xyz,km,cash)")
    p.add_argument("--max-replica-files", type=int, default=3,
                    help="Max replica_cmds files/chunks to download")
    p.add_argument("--download-budget-bytes", type=int, default=250_000_000,
                    help="Max total bytes to download")
    p.add_argument("--max-oracle-commands", type=int, default=1000,
                    help="Max oracle commands to store")
    p.add_argument("--max-runtime-minutes", type=float, default=30.0,
                    help="Max wall-clock runtime in minutes")
    p.add_argument("--dry-run", action="store_true", default=False,
                    help="Dry run: no network, produce ready status")
    p.add_argument("--recon-only", action="store_true", default=True,
                    help="Recon only, no overlap validation (default)")
    p.add_argument("--validate-overlap", action="store_true", default=False,
                    help="Run forward recorder overlap validation")
    p.add_argument("--backfill-after-validation", action="store_true", default=False,
                    help="Run bounded backfill after validation (requires --validate-overlap)")
    p.add_argument("--keep-raw", action="store_true", default=False,
                    help="Keep raw downloaded chunks")
    p.add_argument("--forward-recorder-root", type=str, default=None,
                    help="Path to forward recorder reports root")
    # Full-file decode options
    p.add_argument("--decode-full-file", action="store_true", default=False,
                    help="Download and decode entire selected LZ4 file, not just first block")
    p.add_argument("--target-block", type=str, default=None,
                    help="Target block number for exact file selection (e.g. 1012290000)")
    p.add_argument("--block-selection", type=str, default="first",
                    choices=["exact", "around", "first"],
                    help="Block selection mode: exact (require exact), around (nearest), first (legacy)")
    p.add_argument("--max-full-file-bytes", type=int, default=1_500_000_000,
                    help="Hard cap on full-file object size for decode (default 1.5GB)")
    p.add_argument("--perpdeploy-histogram-only", action="store_true", default=False,
                    help="Skip irrelevant steps, only extract action taxonomy + perpDeploy.setOracle summaries")
    # FLX frequency confirmation mode
    p.add_argument("--flx-frequency-confirmation", action="store_true", default=False,
                    help="Run bounded multi-date FLX oracle update frequency diagnostic")
    p.add_argument("--target-dates", type=str, default=None,
                    help="Comma-separated target dates YYYY-MM-DD (e.g. 2026-05-27,2026-05-28)")
    p.add_argument("--max-full-files-per-date", type=int, default=2,
                    help="Max full files to decode per date (default 2)")
    p.add_argument("--max-full-files-total", type=int, default=8,
                    help="Max total full files across all dates (default 8)")
    p.add_argument("--stop-on-flx-found", action="store_true", default=True,
                    help="Stop early when flx:TSLA or flx:NVDA is found")
    p.add_argument("--continue-after-flx-found", action="store_true", default=False,
                    help="Continue scanning even after flx is found")
    # S3 / network flags
    p.add_argument("--allow-s3-archive-read", action="store_true", default=False,
                    help="Allow S3 archive reads (requester-pays)")
    p.add_argument("--allow-network-public", action="store_true", default=False,
                    help="Allow public HTTP endpoints")
    p.add_argument("--requester-pays", action="store_true", default=True,
                    help="Acknowledge requester-pays S3 access")
    p.add_argument("--s3-connect-timeout-seconds", type=int, default=10)
    p.add_argument("--s3-read-timeout-seconds", type=int, default=30)
    p.add_argument("--s3-max-attempts", type=int, default=2)
    return p


# ---------------------------------------------------------------------------
# FLX frequency confirmation: multi-date bounded oracle-frequency diagnostic
# ---------------------------------------------------------------------------

def run_flx_frequency_confirmation(
    chokepoint,
    config: ReplicaCmdsProbeConfig,
    budget: RuntimeBudgetState,
    run_id: str,
    run_dir: Path,
) -> Tuple[str, Optional[str], List[str]]:
    """Run bounded multi-date FLX oracle update frequency confirmation.

    Returns (final_status, final_reason, artifacts_written).
    """
    import datetime as _dt_mod
    artifacts: List[str] = []
    progress_rows: List[dict] = []

    # Aggregate accumulators
    agg_dex_counts: Dict[str, int] = defaultdict(int)
    agg_oracle_pxs_by_dex: Dict[str, int] = defaultdict(int)
    agg_markets_by_dex: Dict[str, set] = defaultdict(set)
    agg_files_with_dex: Dict[str, int] = defaultdict(int)
    agg_dates_with_dex: Dict[str, set] = defaultdict(set)
    total_bytes = 0
    total_records = 0
    total_perpdeploy = 0
    total_setoracle = 0
    total_oraclepxs = 0
    total_action_bundles = 0
    files_processed = 0
    files_selected_by_date: Dict[str, List[str]] = {}
    files_processed_by_date: Dict[str, List[str]] = {}
    dex_dist_by_file: Dict[str, Dict[str, int]] = {}
    dex_dist_by_date: Dict[str, Dict[str, int]] = {}
    flx_tsla_found = False
    flx_nvda_found = False
    flx_any_found = False
    flx_candidate_files: List[str] = []
    flx_candidate_dates: List[str] = []
    flx_candidate_payloads: List[dict] = []
    cash_tsla_found = False
    cash_nvda_found = False
    km_controls_found = False
    para_seen = False
    xyz_seen = False
    stop_reason = None
    dates_processed: List[str] = []

    for date_str in config.target_dates:
        if flx_any_found and config.stop_on_flx_found and not config.continue_after_flx_found:
            break
        if not budget.check_deadline():
            stop_reason = "runtime_budget_exceeded"
            break
        if files_processed >= config.max_full_files_total:
            stop_reason = "max_full_files_total_reached"
            break

        date_yyyymmdd = date_str.replace("-", "")
        print(f"FLX_FREQ_DATE date={date_str} yyyymmdd={date_yyyymmdd}")

        # Create per-date config
        date_config = ReplicaCmdsProbeConfig(
            out_root=config.out_root,
            target_date=date_str,
            markets=config.markets,
            dexes=config.dexes,
            max_replica_files=config.max_full_files_per_date * 2,
            download_budget_bytes=config.download_budget_bytes,
            max_oracle_commands=config.max_oracle_commands,
            max_runtime_minutes=config.max_runtime_minutes,
            dry_run=False,
            recon_only=True,
            validate_overlap=False,
            backfill_after_validation=False,
            keep_raw=False,
            source=config.source,
            decode_full_file=True,
            target_block=None,
            block_selection="first",
            max_full_file_bytes=config.max_full_file_bytes,
            perpdeploy_histogram_only=False,
        )

        # Probe S3 inventory for this date
        try:
            inventory = probe_s3_inventory(chokepoint, date_config, budget)
        except Exception as e:
            print(f"FLX_FREQ_INVENTORY_ERROR date={date_str} error={e}")
            continue

        if not inventory.source_accessible or not inventory.candidate_keys:
            print(f"FLX_FREQ_NO_KEYS date={date_str} accessible={inventory.source_accessible} keys={len(inventory.candidate_keys)}")
            continue

        # Select up to max_full_files_per_date keys
        selected_keys = inventory.candidate_keys[:config.max_full_files_per_date]
        remaining_budget = config.max_full_files_total - files_processed
        selected_keys = selected_keys[:remaining_budget]
        files_selected_by_date[date_str] = selected_keys
        files_processed_by_date.setdefault(date_str, [])
        dex_dist_by_date.setdefault(date_str, defaultdict(int))

        print(f"FLX_FREQ_SELECTED date={date_str} keys={len(selected_keys)}")

        for sk in selected_keys:
            if flx_any_found and config.stop_on_flx_found and not config.continue_after_flx_found:
                break
            if not budget.check_deadline():
                stop_reason = "runtime_budget_exceeded"
                break
            if files_processed >= config.max_full_files_total:
                stop_reason = "max_full_files_total_reached"
                break

            # Decode full file
            print(f"FLX_FREQ_FILE file_index={files_processed} source_key={sk}")
            file_config = ReplicaCmdsProbeConfig(
                out_root=config.out_root,
                target_date=date_str,
                markets=config.markets,
                dexes=config.dexes,
                max_replica_files=1,
                download_budget_bytes=config.download_budget_bytes,
                max_oracle_commands=config.max_oracle_commands,
                max_runtime_minutes=config.max_runtime_minutes,
                dry_run=False,
                recon_only=True,
                validate_overlap=False,
                backfill_after_validation=False,
                keep_raw=False,
                source=config.source,
                decode_full_file=True,
                target_block=None,
                block_selection="first",
                max_full_file_bytes=config.max_full_file_bytes,
                perpdeploy_histogram_only=False,
            )
            histogram = build_full_file_histogram(
                chokepoint, file_config, sk, 0, budget,
            )

            files_processed += 1
            files_processed_by_date[date_str].append(sk)
            total_bytes += histogram.bytes_downloaded
            total_records += histogram.records_decoded
            total_perpdeploy += histogram.perpDeploy_count
            total_setoracle += histogram.setOracle_payload_count
            total_oraclepxs += histogram.oraclePxs_pair_count
            total_action_bundles += histogram.action_bundles_seen

            # Per-file DEX distribution
            file_dex_dist = dict(histogram.setOracle_payloads_by_dex)
            dex_dist_by_file[sk] = file_dex_dist

            # Aggregate
            for dex, count in histogram.setOracle_payloads_by_dex.items():
                agg_dex_counts[dex] += count
                agg_oracle_pxs_by_dex[dex] += histogram.oraclePxs_pairs_by_dex.get(dex, 0)
                agg_files_with_dex[dex] += 1
                agg_dates_with_dex[dex].add(date_str)
                dex_dist_by_date[date_str][dex] += count
            for dex, mkts in histogram.markets_seen_by_dex.items():
                agg_markets_by_dex[dex].update(mkts)

            # Track target market status
            if histogram.flx_tsla_found:
                flx_tsla_found = True
                flx_any_found = True
                flx_candidate_files.append(sk)
                flx_candidate_dates.append(date_str)
            if histogram.flx_nvda_found:
                flx_nvda_found = True
                flx_any_found = True
                flx_candidate_files.append(sk)
                flx_candidate_dates.append(date_str)
            if histogram.cash_tsla_found:
                cash_tsla_found = True
            if histogram.cash_nvda_found:
                cash_nvda_found = True
            if histogram.km_seen:
                km_controls_found = True
            if "para" in histogram.setOracle_payloads_by_dex:
                para_seen = True
            if "xyz" in histogram.setOracle_payloads_by_dex:
                xyz_seen = True

            # FLX candidate payloads
            if histogram.flx_seen:
                flx_cands = histogram.bounded_candidate_examples_by_dex.get("flx", [])
                flx_candidate_payloads.extend(flx_cands)

            # Progress line
            progress_row = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "file_index": files_processed - 1,
                "source_key": sk,
                "date": date_str,
                "bytes_downloaded": histogram.bytes_downloaded,
                "records_decoded": histogram.records_decoded,
                "perpdeploy_count": histogram.perpDeploy_count,
                "setoracle_payload_count": histogram.setOracle_payload_count,
                "oraclepxs_pair_count": histogram.oraclePxs_pair_count,
                "dexes_seen": histogram.dexes_seen,
                "dex_distribution": file_dex_dist,
                "flx_found_so_far": flx_any_found,
                "stop_reason_if_any": stop_reason,
            }
            progress_rows.append(progress_row)
            print(f"FLX_FREQ_PROGRESS file={sk} date={date_str} "
                  f"perpDeploy={histogram.perpDeploy_count} setOracle={histogram.setOracle_payload_count} "
                  f"flx_seen={histogram.flx_seen} dexes={histogram.dexes_seen}")

            # Write per-file progress
            _write_jsonl_rows(run_dir / "flx_frequency_progress.jsonl", progress_rows)

    # Mark dates as processed
    dates_processed = sorted(files_processed_by_date.keys())

    # Compute update frequency by DEX
    update_freq_by_dex: Dict[str, dict] = {}
    for dex in sorted(agg_dex_counts.keys()):
        count = agg_dex_counts[dex]
        n_files = agg_files_with_dex.get(dex, 0)
        n_dates = len(agg_dates_with_dex.get(dex, set()))
        update_freq_by_dex[dex] = {
            "setoracle_payload_count": count,
            "oraclePxs_pair_count": agg_oracle_pxs_by_dex.get(dex, 0),
            "market_count": len(agg_markets_by_dex.get(dex, set())),
            "files_containing_dex": n_files,
            "dates_containing_dex": n_dates,
        }

    # Classify final status
    flx_seen_count = agg_dex_counts.get("flx", 0)
    cash_seen_count = agg_dex_counts.get("cash", 0)
    km_seen_count = agg_dex_counts.get("km", 0)
    para_seen_count = agg_dex_counts.get("para", 0)
    xyz_seen_count = agg_dex_counts.get("xyz", 0)

    if flx_any_found:
        final_status = "FLX_SETORACLE_FOUND_BOUNDED_RECON"
        final_reason = None
    elif files_processed == 0:
        final_status = "FLX_SETORACLE_HUNT_UNDERPOWERED"
        final_reason = "no_files_processed"
    elif flx_seen_count == 0 and (cash_seen_count + km_seen_count + para_seen_count) > 0:
        # FLX persistently absent while other DEXes active
        if files_processed >= 4 and total_oraclepxs >= 5000:
            final_status = "FLX_STALE_ORACLE_ARTIFACT_SUPPORTED"
            final_reason = "flx_persistently_absent_while_cash_km_para_active"
        else:
            final_status = "FLX_SETORACLE_PERSISTENTLY_ABSENT_IN_SAMPLE"
            final_reason = "flx_absent_in_bounded_sample_insufficient_for_staleness_claim"
    elif flx_seen_count == 0 and (cash_seen_count + km_seen_count + para_seen_count) == 0:
        final_status = "FLX_SETORACLE_HUNT_UNDERPOWERED"
        final_reason = "no_dexes_active_in_any_file"
    else:
        final_status = "REPLICA_CMDS_FULL_FILE_HUNT_COMPLETE"
        final_reason = None

    # Recommendation
    if flx_any_found:
        recommendation_action = "run_flx_overlap_validation"
        cross_dex_pivot = None
        mechanism_status = "flx_oracle_found_validation_needed"
        promised_land = "flx_validation_warranted"
    elif final_status in ("FLX_STALE_ORACLE_ARTIFACT_SUPPORTED", "FLX_SETORACLE_PERSISTENTLY_ABSENT_IN_SAMPLE"):
        recommendation_action = "pivot_to_cash_km_cross_dex_spread_phase_minus1"
        cross_dex_pivot = {"cash:TSLA_vs_km:TSLA": True, "cash:NVDA_vs_km:NVDA": True}
        mechanism_status = "flx_stale_or_sparse_oracle_likely"
        promised_land = "oracle_basis_edge_unlikely"
    else:
        recommendation_action = "run_one_more_bounded_flx_frequency_sample"
        cross_dex_pivot = None
        mechanism_status = "inconclusive"
        promised_land = "needs_one_more_flx_sample"

    # Sample-backed staleness claim
    sample_backed_staleness = (
        flx_seen_count == 0
        and (cash_seen_count + km_seen_count + para_seen_count) > 0
        and files_processed >= 2
    )

    # Write flx candidate dump or stale oracle diagnostic
    stale_diag: Optional[dict] = None
    if flx_any_found:
        candidate_dump = {
            "source_key": flx_candidate_files[0] if flx_candidate_files else "",
            "date": flx_candidate_dates[0] if flx_candidate_dates else "",
            "candidate_count": len(flx_candidate_payloads),
            "flx_tsla_found": flx_tsla_found,
            "flx_nvda_found": flx_nvda_found,
            "candidates": flx_candidate_payloads[:10],
            "parser_confidence": "high",
            "validation_not_run": True,
        }
        _write_json_artifact(run_dir / "flx_setoracle_candidate_dump.json", candidate_dump)
        artifacts.append("flx_setoracle_candidate_dump.json")
    else:
        stale_diag = {
            "files_scanned": files_processed,
            "bytes_scanned": total_bytes,
            "dates_scanned": len(dates_processed),
            "setOracle_payloads_seen": total_setoracle,
            "oraclePxs_pairs_seen": total_oraclepxs,
            "dexes_seen": sorted(agg_dex_counts.keys()),
            "cash_seen_count": cash_seen_count,
            "km_seen_count": km_seen_count,
            "para_seen_count": para_seen_count,
            "xyz_seen_count": xyz_seen_count,
            "flx_seen_count": flx_seen_count,
            "flx_absent_while_other_dexes_active": sample_backed_staleness,
            "sample_backed_staleness_artifact_supported": sample_backed_staleness,
            "global_absence_claim_supported": False,
            "reason_absence_not_global": (
                f"Only {files_processed} files across {len(dates_processed)} dates scanned. "
                f"FLX absence is sample-backed, not globally proven."
            ),
            "explanation_of_forward_recorder_residual": (
                "consistent_with_sparse_or_stale_flx_oracle" if sample_backed_staleness
                else "unknown"
            ),
            "recommended_stop_or_continue": recommendation_action,
            "validation_not_run": True,
        }
        _write_json_artifact(run_dir / "flx_stale_oracle_artifact_diagnostic.json", stale_diag)
        artifacts.append("flx_stale_oracle_artifact_diagnostic.json")

    # Write recommendation
    rec = {
        "original_hypothesis": "HIP3 Builder-DEX TradFi Off-Hours Oracle-Basis Residual",
        "data_plane_status": "historical_oracle_reconstructable",
        "mechanism_status": mechanism_status,
        "promised_land_assessment": promised_land,
        "recommended_next_action": recommendation_action,
        "cross_dex_pivot_candidate": cross_dex_pivot,
        "reason_cross_dex_pivot_survives": (
            "anchor-free, both DEXes update frequently, avoids stale-oracle poison, testable on SonarX L2"
            if cross_dex_pivot else None
        ),
        "registry_mutation_recommended_now": False,
        "registry_mutation_performed": False,
    }
    _write_json_artifact(run_dir / "oracle_basis_closure_or_pivot_recommendation.json", rec)
    artifacts.append("oracle_basis_closure_or_pivot_recommendation.json")

    # Write summary
    summary = {
        "run_id": run_id,
        "target_dates_requested": config.target_dates,
        "target_dates_processed": dates_processed,
        "files_selected_by_date": files_selected_by_date,
        "files_processed_by_date": {k: v for k, v in files_processed_by_date.items()},
        "source_keys_attempted": sum(len(v) for v in files_selected_by_date.values()),
        "files_processed": files_processed,
        "bytes_downloaded": total_bytes,
        "records_decoded": total_records,
        "action_bundle_count": total_action_bundles,
        "perpdeploy_count": total_perpdeploy,
        "setoracle_payload_count": total_setoracle,
        "oraclepxs_pair_count": total_oraclepxs,
        "aggregate_dex_distribution": dict(agg_dex_counts),
        "dex_distribution_by_file": dex_dist_by_file,
        "dex_distribution_by_date": {k: dict(v) for k, v in dex_dist_by_date.items()},
        "markets_seen_by_dex": {k: sorted(v) for k, v in agg_markets_by_dex.items()},
        "update_frequency_by_dex": update_freq_by_dex,
        "files_with_dex": dict(agg_files_with_dex),
        "dates_with_dex": {k: sorted(v) for k, v in agg_dates_with_dex.items()},
        "target_market_status": {
            "flx:TSLA": "FOUND" if flx_tsla_found else "NOT_FOUND",
            "flx:NVDA": "FOUND" if flx_nvda_found else "NOT_FOUND",
            "cash:TSLA": "FOUND" if cash_tsla_found else "NOT_FOUND",
            "cash:NVDA": "FOUND" if cash_nvda_found else "NOT_FOUND",
            "km_controls": "FOUND" if km_controls_found else "NOT_FOUND",
        },
        "flx_tsla_found": flx_tsla_found,
        "flx_nvda_found": flx_nvda_found,
        "flx_any_found": flx_any_found,
        "flx_candidate_count": len(flx_candidate_payloads),
        "flx_candidate_files": flx_candidate_files,
        "flx_candidate_dates": flx_candidate_dates,
        "cash_control_found": cash_tsla_found or cash_nvda_found,
        "km_control_found": km_controls_found,
        "para_seen": para_seen,
        "xyz_seen": xyz_seen,
        "sample_backed_staleness_artifact_supported": sample_backed_staleness,
        "absence_claim_supported": False,
        "conclusion": final_reason or final_status,
    }
    _write_json_artifact(run_dir / "flx_frequency_confirmation_summary.json", summary)
    artifacts.append("flx_frequency_confirmation_summary.json")

    # Write final report markdown
    _write_flx_frequency_report(run_dir, summary, rec, stale_diag if not flx_any_found else None, config)

    return final_status, final_reason, artifacts


def _write_flx_frequency_report(
    run_dir: Path,
    summary: dict,
    recommendation: dict,
    stale_diag: Optional[dict],
    config: ReplicaCmdsProbeConfig,
) -> None:
    """Write the FLX frequency confirmation report markdown."""
    lines = [
        "# HIP-3 REPLICA_CMDS FLX FREQUENCY CONFIRMATION REPORT",
        "",
        "## Run Summary",
        "",
        f"- **Target dates requested**: {summary['target_dates_requested']}",
        f"- **Target dates processed**: {summary['target_dates_processed']}",
        f"- **Files processed**: {summary['files_processed']}",
        f"- **Bytes downloaded**: {summary['bytes_downloaded']:,}",
        f"- **Records decoded**: {summary['records_decoded']:,}",
        f"- **perpDeploy count**: {summary['perpdeploy_count']}",
        f"- **setOracle payload count**: {summary['setoracle_payload_count']}",
        f"- **oraclePxs pair count**: {summary['oraclepxs_pair_count']}",
        "",
        "## Aggregate DEX Distribution",
        "",
        "| DEX | setOracle Count | oraclePxs Count | Markets | Files | Dates |",
        "|-----|----------------|-----------------|---------|-------|-------|",
    ]
    for dex in sorted(summary["aggregate_dex_distribution"].keys()):
        uf = summary["update_frequency_by_dex"].get(dex, {})
        lines.append(
            f"| {dex} | {uf.get('setoracle_payload_count', 0)} | "
            f"{uf.get('oraclePxs_pair_count', 0)} | "
            f"{uf.get('market_count', 0)} | "
            f"{uf.get('files_containing_dex', 0)} | "
            f"{uf.get('dates_containing_dex', 0)} |"
        )
    lines.extend([
        "",
        "## DEX Distribution by Date",
        "",
    ])
    for date_str in sorted(summary["dex_distribution_by_date"].keys()):
        dd = summary["dex_distribution_by_date"][date_str]
        lines.append(f"### {date_str}")
        lines.append("")
        for dex in sorted(dd.keys()):
            lines.append(f"- **{dex}**: {dd[dex]} oracle updates")
        lines.append("")

    lines.extend([
        "## Target Market Status",
        "",
    ])
    for market, status in summary["target_market_status"].items():
        lines.append(f"- **{market}**: {status}")

    lines.extend([
        "",
        "## FLX Status",
        "",
        f"- **flx:TSLA found**: {'YES' if summary['flx_tsla_found'] else 'NO'}",
        f"- **flx:NVDA found**: {'YES' if summary['flx_nvda_found'] else 'NO'}",
        f"- **flx any found**: {'YES' if summary['flx_any_found'] else 'NO'}",
        f"- **flx candidate count**: {summary['flx_candidate_count']}",
        f"- **Sample-backed staleness artifact supported**: {'YES' if summary['sample_backed_staleness_artifact_supported'] else 'NO'}",
        f"- **Absence claim supported**: {'YES' if summary['absence_claim_supported'] else 'NO (sample-backed only)'}",
        "",
        "## Recommendation",
        "",
        f"- **Recommended next action**: {recommendation['recommended_next_action']}",
        f"- **Mechanism status**: {recommendation['mechanism_status']}",
        f"- **Promised land assessment**: {recommendation['promised_land_assessment']}",
    ])
    if recommendation.get("cross_dex_pivot_candidate"):
        lines.append(f"- **Cross-DEX pivot candidate**: {recommendation['cross_dex_pivot_candidate']}")
        lines.append(f"- **Reason pivot survives**: {recommendation['reason_cross_dex_pivot_survives']}")
    lines.extend([
        "",
        "## Safety Confirmations",
        "",
        "- **Validation run**: NO",
        "- **Backfill run**: NO",
        "- **SonarX residual diagnostic run**: NO",
        "- **Cross-DEX spread test run**: NO",
        "- **Registry status**: UNCHANGED",
        "- **Forward recorder status**: UNTOUCHED",
        "",
        "---",
        "",
        '"No overlap validation/backfill/cross-DEX spread test was run; stopped after bounded FLX oracle-frequency confirmation."',
    ])
    content = "\n".join(lines) + "\n"
    (run_dir / "HIP3_REPLICA_CMDS_FLX_FREQUENCY_CONFIRMATION_REPORT.md").write_text(content)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point. Returns 0 on success, 1 on error."""
    parser = build_parser()
    args = parser.parse_args(argv)
    # Build config
    markets_set = frozenset(TARGET_MARKETS)
    if args.markets:
        markets_set = frozenset(m.strip() for m in args.markets.split(",") if m.strip())
    dexes_set = frozenset(TARGET_DEXES)
    if args.dexes:
        dexes_set = frozenset(d.strip() for d in args.dexes.split(",") if d.strip())
    source_config = ReplicaCmdsSourceConfig(
        allow_s3_archive_read=args.allow_s3_archive_read,
        allow_network_public=args.allow_network_public,
        requester_pays=args.requester_pays,
        s3_connect_timeout_seconds=args.s3_connect_timeout_seconds,
        s3_read_timeout_seconds=args.s3_read_timeout_seconds,
        s3_max_attempts=args.s3_max_attempts,
    )
    config = ReplicaCmdsProbeConfig(
        out_root=Path(args.out_root),
        target_date=args.target_date,
        markets=markets_set,
        dexes=dexes_set,
        max_replica_files=args.max_replica_files,
        download_budget_bytes=args.download_budget_bytes,
        max_oracle_commands=args.max_oracle_commands,
        max_runtime_minutes=args.max_runtime_minutes,
        dry_run=args.dry_run,
        recon_only=args.recon_only if not args.validate_overlap else False,
        validate_overlap=args.validate_overlap,
        backfill_after_validation=args.backfill_after_validation,
        keep_raw=args.keep_raw,
        forward_recorder_root=Path(args.forward_recorder_root) if args.forward_recorder_root else None,
        source=source_config,
        decode_full_file=args.decode_full_file,
        target_block=args.target_block,
        block_selection=args.block_selection,
        max_full_file_bytes=args.max_full_file_bytes,
        perpdeploy_histogram_only=args.perpdeploy_histogram_only,
        flx_frequency_confirmation=args.flx_frequency_confirmation,
        target_dates=[d.strip() for d in args.target_dates.split(",") if d.strip()] if args.target_dates else [],
        max_full_files_per_date=args.max_full_files_per_date,
        max_full_files_total=args.max_full_files_total,
        stop_on_flx_found=args.stop_on_flx_found and not args.continue_after_flx_found,
        continue_after_flx_found=args.continue_after_flx_found,
    )
    # Runtime budget
    budget = RuntimeBudgetState.create(config.max_runtime_minutes)
    # Output directory
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    run_dir = config.out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    # Write manifest early
    sha, dirty, branch = _get_git_info(Path(_PKG_ROOT))
    manifest = ReplicaCmdsRunManifest(
        run_id=run_id,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        branch=branch,
        git_sha=sha,
        git_dirty=dirty,
        repo_root=_PKG_ROOT,
        max_runtime_minutes=config.max_runtime_minutes,
        runtime_deadline_utc=budget.deadline_utc.isoformat(),
    )
    _write_json_artifact(run_dir / "run_manifest.json", manifest.to_dict())
    # Write config artifact early
    _write_json_artifact(run_dir / "config.json", {
        "out_root": str(config.out_root),
        "target_date": config.target_date,
        "markets": sorted(config.markets),
        "dexes": sorted(config.dexes),
        "max_replica_files": config.max_replica_files,
        "download_budget_bytes": config.download_budget_bytes,
        "max_oracle_commands": config.max_oracle_commands,
        "max_runtime_minutes": config.max_runtime_minutes,
        "dry_run": config.dry_run,
        "recon_only": config.recon_only,
        "validate_overlap": config.validate_overlap,
        "backfill_after_validation": config.backfill_after_validation,
        "keep_raw": config.keep_raw,
        "forward_recorder_root": str(config.forward_recorder_root) if config.forward_recorder_root else None,
        "allow_s3_archive_read": config.source.allow_s3_archive_read,
        "allow_network_public": config.source.allow_network_public,
        "requester_pays": config.source.requester_pays,
        "safety_mode": SAFETY_MODE,
        "registry_mutated": False,
        "phase0_precommitment_written": False,
    })
    final_status = "REPLICA_CMDS_PHASE_MINUS1_ERROR"
    final_reason = None
    artifacts_written = []
    try:
        if config.dry_run:
            final_status = "REPLICA_CMDS_RECON_DRY_RUN_READY"
            print(f"DRY_RUN status={final_status} run_dir={run_dir}")
            print(f"  markets={sorted(config.markets)}")
            print(f"  dexes={sorted(config.dexes)}")
            print(f"  max_replica_files={config.max_replica_files}")
            print(f"  download_budget_bytes={config.download_budget_bytes}")
            print(f"  max_runtime_minutes={config.max_runtime_minutes}")
            print(f"  registry_mutated=False")
            print(f"  phase0_precommitment_written=False")
            _write_json_artifact(run_dir / "final_status.json", {
                "status": final_status,
                "failure_reason": None,
                "artifacts_written": artifacts_written,
                "runtime_elapsed_seconds": 0.0,
                "deadline_exceeded": False,
                "registry_mutated": False,
                "phase0_precommitment_written": False,
                "sonarx_residual_diagnostic_run": False,
            })
            return 0
        # S3 inventory probe
        chokepoint = NetworkChokepoint(
            allow_network_public=config.source.allow_network_public,
            allow_s3_archive_read=config.source.allow_s3_archive_read,
            s3_connect_timeout=config.source.s3_connect_timeout_seconds,
            s3_read_timeout=config.source.s3_read_timeout_seconds,
            s3_max_attempts=config.source.s3_max_attempts,
        )
        print(f"PROBE_START status=REPLICA_CMDS_RECON_DRY_RUN_READY run_dir={run_dir}")
        inventory = probe_s3_inventory(chokepoint, config, budget)
        _write_json_artifact(run_dir / "replica_cmds_source_inventory.json", {
            "bucket": inventory.bucket,
            "root_prefix": inventory.root_prefix,
            "listing_status": inventory.listing_status,
            "requester_pays_acknowledged": inventory.requester_pays_acknowledged,
            "prefixes_sampled": inventory.prefixes_sampled,
            "keys_sampled": inventory.keys_sampled,
            "candidate_keys": inventory.candidate_keys,
            "inferred_layout": inventory.inferred_layout,
            "bytes_estimated_if_available": inventory.bytes_estimated_if_available,
            "source_accessible": inventory.source_accessible,
            "failure_reason": inventory.failure_reason,
            "error_code": inventory.error_code,
        })
        artifacts_written.append("replica_cmds_source_inventory.json")
        print(f"INVENTORY status={inventory.listing_status} accessible={inventory.source_accessible} "
              f"candidate_keys={len(inventory.candidate_keys)}")
        if not inventory.source_accessible:
            final_status = "REPLICA_CMDS_SOURCE_BLOCKED"
            final_reason = inventory.failure_reason
            print(f"STOP status={final_status} reason={final_reason}")
            _write_json_artifact(run_dir / "final_status.json", {
                "status": final_status, "failure_reason": final_reason,
                "artifacts_written": artifacts_written,
                "runtime_elapsed_seconds": budget.elapsed_seconds,
                "deadline_exceeded": budget.budget_exceeded,
                "registry_mutated": False, "phase0_precommitment_written": False,
                "sonarx_residual_diagnostic_run": False,
            })
            return 1
        final_status = "REPLICA_CMDS_SOURCE_ACCESSIBLE"
        # FLX frequency confirmation mode: multi-date bounded diagnostic
        if config.flx_frequency_confirmation and not config.dry_run and config.target_dates:
            print(f"FLX_FREQ_START dates={config.target_dates} max_files_total={config.max_full_files_total}")
            flx_status, flx_reason, flx_artifacts = run_flx_frequency_confirmation(
                chokepoint, config, budget, run_id, run_dir,
            )
            final_status = flx_status
            final_reason = flx_reason
            artifacts_written.extend(flx_artifacts)
            _write_json_artifact(run_dir / "final_status.json", {
                "status": final_status,
                "failure_reason": final_reason,
                "artifacts_written": artifacts_written,
                "runtime_elapsed_seconds": budget.elapsed_seconds,
                "deadline_exceeded": budget.budget_exceeded,
                "registry_mutated": False,
                "phase0_precommitment_written": False,
                "sonarx_residual_diagnostic_run": False,
                "validation_run": False,
                "backfill_run": False,
                "cross_dex_spread_test_run": False,
            })
            print(f"FINAL status={final_status} artifacts={len(artifacts_written)}")
            return 0 if final_status not in ("REPLICA_CMDS_PHASE_MINUS1_ERROR", "REPLICA_CMDS_SOURCE_BLOCKED") else 1
        # Full-file decode mode: if --decode-full-file is set, handle it here
        if config.decode_full_file and not config.dry_run:
            # Find target block in inventory
            selected_key = None
            selected_size = 0
            exact_found = False
            block_str = config.target_block or ""
            for ck in inventory.candidate_keys:
                # Match by block number in filename
                if block_str and block_str in ck:
                    selected_key = ck
                    exact_found = True
                    break
            if not selected_key and inventory.candidate_keys:
                if config.block_selection == "first":
                    selected_key = inventory.candidate_keys[0]
                elif config.block_selection == "exact":
                    # Exact mode requires exact match
                    selected_key = None
                elif config.block_selection == "around" and block_str:
                    # Find nearest block by number
                    best_dist = float("inf")
                    for ck in inventory.candidate_keys:
                        # Extract block number from filename
                        fname = ck.rstrip("/").split("/")[-1].replace(".lz4", "")
                        try:
                            blk_num = int(fname)
                            target_num = int(block_str)
                            dist = abs(blk_num - target_num)
                            if dist < best_dist:
                                best_dist = dist
                                selected_key = ck
                        except (ValueError, TypeError):
                            continue
            # Get size from inventory
            if selected_key:
                for ck_info in inventory.candidate_keys:
                    pass  # inventory.candidate_keys is just strings
                # Try to get size from inventory listing
                # The size info is in the listing but not in candidate_keys
                # Use 0 as fallback - build_full_file_histogram will check after download
                selected_size = 0
            # Write selection plan
            selection_plan = {
                "target_date": config.target_date,
                "target_block": block_str,
                "block_selection_mode": config.block_selection,
                "decode_full_file": True,
                "total_candidate_keys": len(inventory.candidate_keys),
                "selected_key": selected_key,
                "selected_block": block_str,
                "selected_block_distance": 0 if exact_found else None,
                "exact_target_block_found": exact_found,
                "selected_object_size_bytes_if_available": selected_size,
                "max_full_file_bytes": config.max_full_file_bytes,
                "download_budget_bytes": config.download_budget_bytes,
                "runtime_budget_minutes": config.max_runtime_minutes,
                "requester_pays_acknowledged": config.source.requester_pays,
                "proceed_to_download": selected_key is not None,
                "blocked_reason": None if selected_key else "REPLICA_CMDS_TARGET_BLOCK_NOT_FOUND",
                "fallback_used": selected_key is not None and not exact_found,
                "fallback_reason": f"Block {block_str} not found exactly; used {config.block_selection} selection" if (selected_key and not exact_found) else None,
            }
            _write_json_artifact(run_dir / "replica_cmds_full_file_selection_plan.json", selection_plan)
            artifacts_written.append("replica_cmds_full_file_selection_plan.json")
            if not selected_key:
                final_status = "REPLICA_CMDS_TARGET_BLOCK_NOT_FOUND"
                final_reason = f"Target block {block_str} not found in {len(inventory.candidate_keys)} candidates"
                print(f"STOP status={final_status} reason={final_reason}")
                _write_json_artifact(run_dir / "final_status.json", {
                    "status": final_status, "failure_reason": final_reason,
                    "artifacts_written": artifacts_written,
                    "runtime_elapsed_seconds": budget.elapsed_seconds,
                    "deadline_exceeded": budget.budget_exceeded,
                    "registry_mutated": False, "phase0_precommitment_written": False,
                    "sonarx_residual_diagnostic_run": False,
                })
                return 1
            # Build full-file histogram
            print(f"FULL_FILE_DECODE key={selected_key} max_bytes={config.max_full_file_bytes}")
            histogram = build_full_file_histogram(
                chokepoint, config, selected_key, selected_size, budget,
            )
            histogram.run_id = run_id
            histogram.target_date = config.target_date or ""
            # Write histogram
            _write_json_artifact(run_dir / "perpdeploy_full_file_dex_histogram.json", histogram.to_dict())
            artifacts_written.append("perpdeploy_full_file_dex_histogram.json")
            print(f"HISTOGRAM status={histogram.status} records={histogram.records_decoded} "
                  f"perpDeploy={histogram.perpDeploy_count} setOracle={histogram.setOracle_payload_count} "
                  f"dexes={histogram.dexes_seen}")
            # Write flx candidate dump or follow-up plan
            if histogram.flx_seen:
                # Write flx candidate dump
                flx_candidates = histogram.bounded_candidate_examples_by_dex.get("flx", [])
                _write_json_artifact(run_dir / "flx_full_file_setoracle_candidate_dump.json", {
                    "source_key": selected_key,
                    "selected_block": block_str,
                    "candidate_count": len(flx_candidates),
                    "candidates": flx_candidates,
                    "flx_tsla_found": histogram.flx_tsla_found,
                    "flx_nvda_found": histogram.flx_nvda_found,
                    "parser_confidence": histogram.parser_confidence,
                })
                artifacts_written.append("flx_full_file_setoracle_candidate_dump.json")
                # Write validation budget plan
                _write_json_artifact(run_dir / "flx_overlap_validation_budget_plan.json", {
                    "flx_candidate_found": True,
                    "candidate_count": len(flx_candidates),
                    "source_date": config.target_date,
                    "candidate_blocks": [str(c.get("block", "")) for c in flx_candidates],
                    "candidate_record_indices": [c.get("record_index", 0) for c in flx_candidates],
                    "estimated_files_needed_for_overlap_validation": 5,
                    "estimated_bytes_needed_for_overlap_validation": 5_000_000_000,
                    "recommended_max_replica_files": 5,
                    "recommended_download_budget_bytes": 5_000_000_000,
                    "recommended_runtime_minutes": 120,
                    "recommended_join_tolerance_primary_seconds": 5,
                    "recommended_join_tolerance_secondary_seconds": 60,
                    "next_command_not_run": True,
                })
                artifacts_written.append("flx_overlap_validation_budget_plan.json")
            else:
                # Write follow-up sampling plan
                _write_json_artifact(run_dir / "flx_full_file_followup_sampling_plan.json", {
                    "flx_candidate_found": False,
                    "files_scanned": 1,
                    "bytes_scanned": histogram.bytes_downloaded,
                    "records_decoded": histogram.records_decoded,
                    "setOracle_payloads_seen": histogram.setOracle_payload_count,
                    "dexes_seen_in_setOracle": histogram.dexes_seen,
                    "cash_update_count": histogram.setOracle_payloads_by_dex.get("cash", 0),
                    "xyz_update_count": histogram.setOracle_payloads_by_dex.get("xyz", 0),
                    "km_update_count": histogram.setOracle_payloads_by_dex.get("km", 0),
                    "flx_update_count": 0,
                    "estimated_probability_comment": (
                        "FLX absent in one full file while other DEXes present. "
                        "Consistent with sparse or absent FLX oracle updates in this file. "
                        "Needs another date/file or overlap confirmation before closure."
                    ),
                    "recommended_next_dates_or_file_count": "Try 2-3 more dates or 3-5 more files",
                    "absence_claim_supported_by_full_file": False,
                    "next_command_not_run": True,
                })
                artifacts_written.append("flx_full_file_followup_sampling_plan.json")
            final_status = histogram.status
            # Write final status
            _write_json_artifact(run_dir / "final_status.json", {
                "status": final_status,
                "failure_reason": None,
                "artifacts_written": artifacts_written,
                "runtime_elapsed_seconds": budget.elapsed_seconds,
                "deadline_exceeded": budget.budget_exceeded,
                "registry_mutated": False,
                "phase0_precommitment_written": False,
                "sonarx_residual_diagnostic_run": False,
                "validation_run": False,
                "backfill_run": False,
                "sonarx_residual_diagnostic_run": False,
            })
            print(f"FINAL status={final_status} artifacts={len(artifacts_written)}")
            return 0 if final_status not in ("REPLICA_CMDS_PHASE_MINUS1_ERROR", "REPLICA_CMDS_SOURCE_BLOCKED") else 1
        # End of full-file decode mode
        # Envelope probe
        if not budget.check_deadline():
            final_reason = "RUNTIME_BUDGET_EXCEEDED"
            final_status = "REPLICA_CMDS_PHASE_MINUS1_ERROR"
            print(f"STOP status={final_status} reason={final_reason}")
            _write_json_artifact(run_dir / "final_status.json", {
                "status": final_status, "failure_reason": final_reason,
                "artifacts_written": artifacts_written,
                "runtime_elapsed_seconds": budget.elapsed_seconds,
                "deadline_exceeded": budget.budget_exceeded,
                "registry_mutated": False, "phase0_precommitment_written": False,
                "sonarx_residual_diagnostic_run": False,
            })
            return 1
        envelope_probe, first_sample, candidates, schema_result = probe_recon_schema(
            chokepoint, config, inventory, budget,
        )
        _write_json_artifact(run_dir / "replica_cmds_envelope_probe.json", {
            "source_key": envelope_probe.source_key,
            "bytes_read": envelope_probe.bytes_read,
            "raw_prefix_hex": envelope_probe.raw_prefix_hex[:256],
            "compression_detected": envelope_probe.compression_detected,
            "decompression_attempts": envelope_probe.decompression_attempts,
            "decompressor_required": envelope_probe.decompressor_required,
            "decompressor_available": envelope_probe.decompressor_available,
            "record_boundary_strategy": envelope_probe.record_boundary_strategy,
            "envelope_status": envelope_probe.envelope_status,
            "proceed_to_content_search_allowed": envelope_probe.proceed_to_content_search_allowed,
            "failure_reason": envelope_probe.failure_reason,
        })
        artifacts_written.append("replica_cmds_envelope_probe.json")
        print(f"ENVELOPE status={envelope_probe.envelope_status} "
              f"strategy={envelope_probe.record_boundary_strategy} "
              f"proceed={envelope_probe.proceed_to_content_search_allowed}")
        if not envelope_probe.proceed_to_content_search_allowed:
            final_status = "REPLICA_CMDS_ENVELOPE_UNKNOWN"
            final_reason = envelope_probe.failure_reason or "ENVELOPE_FORMAT_UNSUPPORTED"
            print(f"STOP status={final_status} reason={final_reason}")
            _write_json_artifact(run_dir / "final_status.json", {
                "status": final_status, "failure_reason": final_reason,
                "artifacts_written": artifacts_written,
                "runtime_elapsed_seconds": budget.elapsed_seconds,
                "deadline_exceeded": budget.budget_exceeded,
                "registry_mutated": False, "phase0_precommitment_written": False,
                "sonarx_residual_diagnostic_run": False,
            })
            return 1
        final_status = "REPLICA_CMDS_ENVELOPE_DECODED"
        # Content recon schema probe
        _write_json_artifact(run_dir / "replica_cmds_recon_schema_probe.json", schema_result)
        artifacts_written.append("replica_cmds_recon_schema_probe.json")
        print(f"SCHEMA_PROBE oracle_found={schema_result['oracle_like_candidates_found']} "
              f"setoracle_found={schema_result['setoracle_command_found']} "
              f"confidence={schema_result['schema_confidence']} "
              f"flx_priority={schema_result['flx_priority_candidates_found']}")
        if not schema_result["setoracle_command_found"]:
            if schema_result["oracle_like_candidates_found"] > 0:
                final_status = "REPLICA_CMDS_SOURCE_ACCESSIBLE_BUT_NO_DEPLOYER_ORACLE"
                final_reason = "NO_HIP3_DEPLOYER_SETORACLE_IN_ARCHIVE"
            else:
                final_status = "REPLICA_CMDS_ORACLE_COMMAND_NOT_FOUND"
                final_reason = "NO_ORACLE_COMMANDS_IN_SAMPLE"
            print(f"STOP status={final_status} reason={final_reason}")
            _write_json_artifact(run_dir / "final_status.json", {
                "status": final_status, "failure_reason": final_reason,
                "artifacts_written": artifacts_written,
                "runtime_elapsed_seconds": budget.elapsed_seconds,
                "deadline_exceeded": budget.budget_exceeded,
                "registry_mutated": False, "phase0_precommitment_written": False,
                "sonarx_residual_diagnostic_run": False,
            })
            return 1
        final_status = "REPLICA_CMDS_ORACLE_COMMAND_FOUND"
        # Volume estimate
        vol_est = compute_volume_estimate(
            bytes_read=schema_result["bytes_read"],
            decoded_records=schema_result["decoded_records_seen"],
            oracle_candidates=schema_result["oracle_like_candidates_found"],
            hip3_deployer_oracle=schema_result["hip3_deployer_setoracle_candidates_found"],
        )
        _write_json_artifact(run_dir / "replica_cmds_volume_estimate.json", vol_est.to_dict())
        artifacts_written.append("replica_cmds_volume_estimate.json")
        print(f"VOLUME estimated_bytes_per_overlap_hour={vol_est.estimated_bytes_to_cover_one_overlap_hour:.0f} "
              f"feasible={vol_est.validation_run_feasible_under_default_budget}")
        if not vol_est.validation_run_feasible_under_default_budget:
            final_status = "REPLICA_CMDS_PHASE_MINUS1_UNDERPOWERED"
            print(f"STOP status={final_status} volume_insufficient")
            _write_json_artifact(run_dir / "final_status.json", {
                "status": final_status, "failure_reason": None,
                "artifacts_written": artifacts_written,
                "runtime_elapsed_seconds": budget.elapsed_seconds,
                "deadline_exceeded": budget.budget_exceeded,
                "registry_mutated": False, "phase0_precommitment_written": False,
                "sonarx_residual_diagnostic_run": False,
            })
            return 1
        # Parse setOracle commands
        final_status = "REPLICA_CMDS_SETORACLE_RECONSTRUCTED"
        # Overlap validation
        if config.validate_overlap:
            # Load forward recorder
            fwd_points = []
            fwd_inventory = {"overlap_ready": False}
            if config.forward_recorder_root and config.forward_recorder_root.exists():
                fwd_points, fwd_inventory = load_forward_recorder_points(
                    config.forward_recorder_root, config.markets, run_dir,
                )
            _write_json_artifact(run_dir / "forward_recorder_overlap_inventory.json", fwd_inventory)
            artifacts_written.append("forward_recorder_overlap_inventory.json")
            print(f"FORWARD_LOADER loaded={fwd_inventory.get('rows_loaded', 0)} "
                  f"markets={fwd_inventory.get('target_markets_present', [])} "
                  f"overlap_ready={fwd_inventory.get('overlap_ready', False)}")
            if not fwd_inventory.get("overlap_ready", False):
                final_status = "REPLICA_CMDS_FORWARD_OVERLAP_UNAVAILABLE"
                final_reason = "NO_OVERLAP_WITH_FORWARD_RECORDER"
                print(f"STOP status={final_status} reason={final_reason}")
                _write_json_artifact(run_dir / "final_status.json", {
                    "status": final_status, "failure_reason": final_reason,
                    "artifacts_written": artifacts_written,
                    "runtime_elapsed_seconds": budget.elapsed_seconds,
                    "deadline_exceeded": budget.budget_exceeded,
                    "registry_mutated": False, "phase0_precommitment_written": False,
                    "sonarx_residual_diagnostic_run": False,
                })
                return 1
            # Run overlap validation
            overlap_result = validate_overlap(candidates, fwd_points)
            _write_json_artifact(run_dir / "setoracle_forward_overlap_validation.json", overlap_result.to_dict())
            artifacts_written.append("setoracle_forward_overlap_validation.json")
            # FLX corroboration
            flx_corr = compute_flx_corroboration(overlap_result)
            _write_json_artifact(run_dir / "flx_setoracle_corroboration_probe.json", flx_corr)
            artifacts_written.append("flx_setoracle_corroboration_probe.json")
            print(f"OVERLAP status={overlap_result.status} "
                  f"validated={overlap_result.validated_count} "
                  f"transform={overlap_result.transform_required_count} "
                  f"mismatch={overlap_result.mismatch_count}")
            print(f"FLX_CORR {flx_corr['conclusion'][:120]}")
            final_status = overlap_result.status
            # Backfill
            if config.backfill_after_validation:
                if final_status == "REPLICA_CMDS_FORWARD_OVERLAP_VALIDATED":
                    backfill_manifest = run_bounded_backfill(
                        chokepoint, config, budget, overlap_result, run_dir,
                    )
                    _write_json_artifact(run_dir / "historical_oracle_reconstruction_manifest.json", backfill_manifest)
                    artifacts_written.append("historical_oracle_reconstruction_manifest.json")
                    final_status = "REPLICA_CMDS_BACKFILL_ALLOWED_AFTER_VALIDATION"
                    print(f"BACKFILL status={final_status}")
                else:
                    final_status = "REPLICA_CMDS_BACKFILL_BLOCKED"
                    print(f"BACKFILL_BLOCKED status={final_status}")
        else:
            print(f"RECON_ONLY mode, skipping overlap validation")
            final_status = "REPLICA_CMDS_SETORACLE_RECONSTRUCTED"
    except PermissionError as e:
        final_status = "REPLICA_CMDS_SOURCE_BLOCKED"
        final_reason = str(e)
        print(f"PERMISSION_ERROR status={final_status} reason={final_reason}")
    except Exception as e:
        final_status = "REPLICA_CMDS_PHASE_MINUS1_ERROR"
        final_reason = f"{type(e).__name__}: {e}"
        print(f"ERROR status={final_status} reason={final_reason}")
        import traceback
        traceback.print_exc()
    # Final report
    _write_json_artifact(run_dir / "final_status.json", {
        "status": final_status,
        "failure_reason": final_reason,
        "artifacts_written": artifacts_written,
        "runtime_elapsed_seconds": budget.elapsed_seconds,
        "deadline_exceeded": budget.budget_exceeded,
        "registry_mutated": False,
        "phase0_precommitment_written": False,
        "sonarx_residual_diagnostic_run": False,
    })
    print(f"FINAL status={final_status} reason={final_reason} "
          f"elapsed={budget.elapsed_seconds:.1f}s "
          f"artifacts={len(artifacts_written)}")
    if final_status in FORBIDDEN_STATUSES:
        print(f"FATAL_FORBIDDEN_STATUS: {final_status}")
        return 1
    return 0 if final_status not in ("REPLICA_CMDS_PHASE_MINUS1_ERROR", "REPLICA_CMDS_SOURCE_BLOCKED") else 1


if __name__ == "__main__":
    sys.exit(main())
