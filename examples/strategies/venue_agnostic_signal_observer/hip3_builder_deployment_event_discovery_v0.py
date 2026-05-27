#!/usr/bin/env python3
"""
HIP-3 Builder Deployment Event Discovery v0

Phase -1 data-plane discovery probe to determine whether HIP-3 builder-deployed
perp symbols, deployer addresses/namespaces, deployment actions, and archive
visibility can be discovered from public Hyperliquid block/archive data.

This is NOT a strategy, NOT a precommitment, NOT a PnL evaluator.
Public data only. No auth. No orders. No execution.
"""

from __future__ import annotations

import hashlib
import json
import lz4.frame
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.request import urlopen, Request

try:
    import msgpack
    _MSGPACK_AVAILABLE = True
except ImportError:
    _MSGPACK_AVAILABLE = False

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError, MissingDependencyException as _BotocoreMissingDepError
    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False


UTC = timezone.utc

# ---------------------------------------------------------------------------
# Archive source constants — must not be swapped.
# Explorer blocks live in hl-mainnet-node-data, NOT hyperliquid-archive.
# ---------------------------------------------------------------------------
EXPLORER_BLOCK_BUCKET = "hl-mainnet-node-data"
EXPLORER_BLOCK_PREFIX = "explorer_blocks"

MARKET_DATA_BUCKET = "hyperliquid-archive"
ASSET_CTXS_PREFIX = "asset_ctxs"
MARKET_DATA_PREFIX = "market_data"


class ScoutStatus(Enum):
    """Diagnostic statuses for the deployment discovery probe."""
    HIP3_DEPLOYMENT_DISCOVERY_READY = "HIP3_DEPLOYMENT_DISCOVERY_READY"
    HIP3_DEPLOYMENT_EVENTS_FOUND = "HIP3_DEPLOYMENT_EVENTS_FOUND"
    HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS = "HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS"
    HIP3_DEPLOYMENT_EVENTS_FOUND_BUT_ASSET_CTXS_MISSING = "HIP3_DEPLOYMENT_EVENTS_FOUND_BUT_ASSET_CTXS_MISSING"
    HIP3_DEPLOYMENT_EVENTS_FOUND_BUT_L2_MISSING = "HIP3_DEPLOYMENT_EVENTS_FOUND_BUT_L2_MISSING"
    HIP3_DEPLOYMENT_EVENTS_FOUND_AND_ARCHIVE_VISIBLE = "HIP3_DEPLOYMENT_EVENTS_FOUND_AND_ARCHIVE_VISIBLE"
    HIP3_ACTION_SCHEMA_UNKNOWN = "HIP3_ACTION_SCHEMA_UNKNOWN"
    HIP3_ARCHIVE_HELPER_RECONCILIATION_REQUIRED = "HIP3_ARCHIVE_HELPER_RECONCILIATION_REQUIRED"
    HIP3_DEPLOYMENT_DISCOVERY_ERROR = "HIP3_DEPLOYMENT_DISCOVERY_ERROR"
    HIP3_EXPLORER_BLOCK_LAYOUT_DISCOVERED = "HIP3_EXPLORER_BLOCK_LAYOUT_DISCOVERED"
    HIP3_EXPLORER_BLOCK_LAYOUT_UNKNOWN = "HIP3_EXPLORER_BLOCK_LAYOUT_UNKNOWN"
    HIP3_EXPLORER_BLOCK_ROOT_EMPTY = "HIP3_EXPLORER_BLOCK_ROOT_EMPTY"
    HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED = "HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED"
    HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED = "HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED"
    HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_ACCESS_DENIED = "HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_ACCESS_DENIED"
    HIP3_EXPLORER_BLOCK_DATE_MAPPING_UNAVAILABLE = "HIP3_EXPLORER_BLOCK_DATE_MAPPING_UNAVAILABLE"
    HIP3_EXPLORER_BLOCK_DATE_MAPPING_READY = "HIP3_EXPLORER_BLOCK_DATE_MAPPING_READY"
    HIP3_EXPLORER_BLOCK_DATE_MAPPING_INSUFFICIENT_SAMPLES = "HIP3_EXPLORER_BLOCK_DATE_MAPPING_INSUFFICIENT_SAMPLES"
    HIP3_EXPLORER_BLOCK_TIMESTAMP_PARSE_FAILED = "HIP3_EXPLORER_BLOCK_TIMESTAMP_PARSE_FAILED"
    HIP3_EXPLORER_BLOCK_DATE_OUT_OF_RANGE = "HIP3_EXPLORER_BLOCK_DATE_OUT_OF_RANGE"
    HIP3_EXPLORER_BLOCK_PREFIX_OR_PATH_INVALID = "HIP3_EXPLORER_BLOCK_PREFIX_OR_PATH_INVALID"
    # Action-type inventory mode statuses (P1 explorer-block action-type enumeration)
    HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_READY = "HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_READY"
    HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_EMPTY = "HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_EMPTY"
    HIP3_EXPLORER_BLOCK_ACTION_SCHEMA_OPAQUE = "HIP3_EXPLORER_BLOCK_ACTION_SCHEMA_OPAQUE"
    # P2 deployment-event search statuses
    HIP3_DEPLOYMENT_EVENT_SEARCH_READY = "HIP3_DEPLOYMENT_EVENT_SEARCH_READY"
    HIP3_DEPLOYMENT_EVENT_CANDIDATES_FOUND = "HIP3_DEPLOYMENT_EVENT_CANDIDATES_FOUND"
    HIP3_NO_DEPLOYMENT_EVENT_CANDIDATES_IN_SCANNED_BLOCKS = "HIP3_NO_DEPLOYMENT_EVENT_CANDIDATES_IN_SCANNED_BLOCKS"
    HIP3_DEPLOYMENT_EVENT_SCHEMA_UNKNOWN = "HIP3_DEPLOYMENT_EVENT_SCHEMA_UNKNOWN"
    HIP3_DEPLOYMENT_EVENT_SEARCH_UNDERPOWERED = "HIP3_DEPLOYMENT_EVENT_SEARCH_UNDERPOWERED"
    HIP3_DEPLOYMENT_EVENT_SEARCH_ERROR = "HIP3_DEPLOYMENT_EVENT_SEARCH_ERROR"
    # P3 confirmation statuses
    HIP3_P3_CONFIRMATION_READY = "HIP3_P3_CONFIRMATION_READY"
    HIP3_P3_CANDIDATES_CONFIRMED_BUILDER_DEPLOYED = "HIP3_P3_CANDIDATES_CONFIRMED_BUILDER_DEPLOYED"
    HIP3_P3_CANDIDATES_SYMBOL_EXTRACTED = "HIP3_P3_CANDIDATES_SYMBOL_EXTRACTED"
    HIP3_P3_CANDIDATES_CONFIG_ONLY = "HIP3_P3_CANDIDATES_CONFIG_ONLY"
    HIP3_P3_CANDIDATES_OPAQUE = "HIP3_P3_CANDIDATES_OPAQUE"
    HIP3_P3_CANDIDATES_NOT_BUILDER_DEPLOYED = "HIP3_P3_CANDIDATES_NOT_BUILDER_DEPLOYED"
    HIP3_P3_ARCHIVE_CROSS_REFERENCE_MISSING = "HIP3_P3_ARCHIVE_CROSS_REFERENCE_MISSING"
    HIP3_P3_CONFIRMATION_INCONCLUSIVE = "HIP3_P3_CONFIRMATION_INCONCLUSIVE"
    HIP3_P3_ERROR = "HIP3_P3_ERROR"


STUDY_ID = "hip3_builder_deployment_event_discovery_v0"
SCHEMA_VERSION = "1.0.0"
SAFETY_MODE = "public_data_observer_only"

# Search terms for deployment-like actions (schema-tolerant, not hardcoded)
DEPLOYMENT_SEARCH_TERMS = [
    "hip3", "builder", "deploy", "deployer", "register",
    "asset", "perp", "oracle", "universe", "margintable",
    "setoracle", "schedule", "spotdeploy", "perpdeploy",
    "registerasset", "deployperp", "createmarket"
]

# Extended P2 search terms (term + rarity)
P2_DEPLOYMENT_SEARCH_TERMS = [
    "hip3", "builder", "deploy", "deployer", "register",
    "asset", "perp", "oracle", "universe", "margintable",
    "setoracle", "schedule", "spotdeploy", "perpdeploy",
    "registerasset", "deployperp", "createmarket",
    "setmargin", "setfunding", "externalperp", "builderperp",
    "market", "name", "coin"
]

# Candidate classification labels
P2_CANDIDATE_CLASS_TERM_MATCH = "term_match"
P2_CANDIDATE_CLASS_RARE_ACTION_TYPE = "rare_action_type"
P2_CANDIDATE_CLASS_NEW_ACTION_TYPE_VS_P1 = "new_action_type_vs_p1"
P2_CANDIDATE_CLASS_SYMBOL_LIKE_PAYLOAD = "symbol_like_payload"
P2_CANDIDATE_CLASS_ORACLE_LIKE_PAYLOAD = "oracle_like_payload"
P2_CANDIDATE_CLASS_DEPLOYER_LIKE_PAYLOAD = "deployer_like_payload"
P2_CANDIDATE_CLASS_ASSET_REGISTRATION_LIKE_PAYLOAD = "asset_registration_like_payload"
P2_CANDIDATE_CLASS_UNKNOWN_RELEVANT_SHAPE = "unknown_relevant_shape"


@dataclass
class DeploymentCandidate:
    """A candidate deployment/listing event extracted from block data."""
    block_time: str
    block_height: int | None
    action_type: str
    deployer_address: str | None
    builder_namespace: str | None
    symbol: str | None
    symbol_extractable: bool
    oracle_fields: dict[str, Any] | None
    margin_fields: dict[str, Any] | None
    fee_fields: dict[str, Any] | None
    raw_excerpt: str
    source_path: str
    source_content_hash: str
    tx_hash_or_index: str | None = None


@dataclass
class BlockTimestampSample:
    """A single timestamp sample from an explorer block file."""
    source_key: str
    top_level_range_prefix: str
    block_number: int | None
    block_timestamp_utc: str | None
    parse_status: str
    byte_count: int
    content_hash: str
    action_type_count: int = 0


@dataclass
class ProbeResult:
    """Result of the deployment discovery probe."""
    status: ScoutStatus | str
    study_id: str = STUDY_ID
    run_id: str = ""
    created_at_utc: str = ""
    git_sha: str = ""
    git_dirty: bool = False
    repo_root: str = ""
    explorer_block_date_range: tuple[str, str] = ("", "")
    bytes_downloaded_total: int = 0
    bytes_downloaded_by_source: dict[str, int] = field(default_factory=dict)
    action_types_inventoried: list[str] = field(default_factory=list)
    candidate_events: list[DeploymentCandidate] = field(default_factory=list)
    candidate_symbols: list[str] = field(default_factory=list)
    public_info_cross_reference: dict[str, Any] = field(default_factory=dict)
    archive_visibility: dict[str, Any] = field(default_factory=dict)
    explorer_block_bucket: str = EXPLORER_BLOCK_BUCKET
    explorer_block_root_prefix: str = EXPLORER_BLOCK_PREFIX
    explorer_block_layout: str = ""
    explorer_root_listing_status: str = ""
    explorer_root_prefixes: list[str] = field(default_factory=list)
    explorer_root_keys: list[str] = field(default_factory=list)
    safety_mode: str = SAFETY_MODE
    schema_version: str = SCHEMA_VERSION
    download_budget_bytes: int = 5_000_000_000
    explorer_block_budget_bytes: int = 1_000_000_000
    s3_requester_pays_acknowledged: bool = False
    aws_identity_available: bool = False
    aws_account_suffix: str = ""
    helpers_reused: list[dict[str, str]] = field(default_factory=list)
    # Block-range layout metadata
    layout_type: str = ""
    layout_stride: int | None = None
    layout_first_range: str = ""
    layout_last_range: str = ""
    # Timestamp samples
    timestamp_samples: list[BlockTimestampSample] = field(default_factory=list)
    # Date-to-block mapping
    date_block_mapping: dict[str, Any] = field(default_factory=dict)
    # Budget args for timestamp sampling
    max_layout_prefixes: int = 11
    max_timestamp_sample_files: int = 50
    max_timestamp_sample_bytes: int = 200_000_000


@dataclass
class P2Candidate:
    """A candidate from P2 deployment-event search."""
    window_name: str
    source_key: str
    source_content_hash: str
    block_number: int | None
    block_timestamp_utc: str | None
    tx_index: int | None
    action_index: int | None
    action_type: str
    user_or_deployer: str | None
    symbol_or_coin: str | None
    matched_terms: list[str]
    candidate_class: str
    redacted_excerpt: str
    nested_field_paths: list[str]
    raw_action_type_count: int
    is_rare_action: bool
    is_new_vs_p1: bool


@dataclass
class P2WindowScanSummary:
    """Per-window scan summary for P2 deployment event search."""
    window_name: str
    date_range: tuple[str, str]
    mapped_ranges: list[str] = field(default_factory=list)
    files_scanned: int = 0
    bytes_downloaded: int = 0
    blocks_parsed: int = 0
    total_actions: int = 0
    unique_action_types: list[str] = field(default_factory=list)
    candidate_count: int = 0
    decode_failures: int = 0
    opaque_count: int = 0
    status: str = "HIP3_DEPLOYMENT_EVENT_SEARCH_READY"
    candidates: list[P2Candidate] = field(default_factory=list)


@dataclass
class P2SearchResult:
    """Result of the P2 deployment-event search."""
    status: ScoutStatus | str
    study_id: str = STUDY_ID
    run_id: str = ""
    created_at_utc: str = ""
    git_sha: str = ""
    git_dirty: bool = False
    repo_root: str = ""
    safety_mode: str = SAFETY_MODE
    schema_version: str = SCHEMA_VERSION
    final_status: str = ""
    candidates: list[P2Candidate] = field(default_factory=list)
    candidate_symbols: list[str] = field(default_factory=list)
    symbol_cross_reference: dict[str, Any] = field(default_factory=dict)
    schema_field_inventory: dict[str, Any] = field(default_factory=dict)
    window_summaries: list[P2WindowScanSummary] = field(default_factory=list)
    p2_windows: list[dict[str, str]] = field(default_factory=list)
    files_listed: int = 0
    files_read: int = 0
    bytes_downloaded: int = 0
    blocks_parsed: int = 0
    total_actions: int = 0
    unique_action_types: list[str] = field(default_factory=list)
    action_type_counts: dict[str, int] = field(default_factory=dict)
    decode_method_used: str = "unknown"
    decode_failures_count: int = 0
    opaque_records_count: int = 0
    inferred_layout: str = ""
    explorer_block_bucket: str = EXPLORER_BLOCK_BUCKET
    explorer_block_root_prefix: str = EXPLORER_BLOCK_PREFIX
    explorer_root_listing_status: str = ""
    aws_identity_available: bool = False
    aws_account_suffix: str = ""
    download_budget_bytes: int = 5_000_000_000
    explorer_block_budget_bytes: int = 1_000_000_000
    p2_max_files_per_window: int = 1000
    p2_preserve_excerpts: int = 50
    p2_rare_action_threshold: int = 25


class NetworkChokepoint:
    """
    Single chokepoint for all network/archive reads.
    Requires explicit guard flags for any public endpoint or S3 access.
    """
    
    def __init__(self, allow_network_public: bool = False, allow_s3_archive_read: bool = False):
        self.allow_network_public = allow_network_public
        self.allow_s3_archive_read = allow_s3_archive_read
        self.bytes_downloaded = 0
        self.bytes_by_source: dict[str, int] = {}
    
    def http_get(self, url: str, timeout: int = 20) -> bytes:
        """HTTP GET through chokepoint."""
        if not self.allow_network_public:
            raise PermissionError(
                f"Network access to {url} blocked. Pass --allow-network-public to enable."
            )
        req = Request(url, method="GET")
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            self._track_bytes(len(data), f"http:{url.split('/')[2]}")
            return data
    
    def http_post_json(self, url: str, payload: dict, timeout: int = 20) -> Any:
        """HTTP POST with JSON payload through chokepoint."""
        if not self.allow_network_public:
            raise PermissionError(
                f"Network access to {url} blocked. Pass --allow-network-public to enable."
            )
        req = Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            self._track_bytes(len(data), f"http:{url.split('/')[2]}")
            return json.loads(data)
    
    def s3_list_prefix(self, bucket: str, prefix: str, requester_pays: bool = True, max_keys: int = 1000, include_subdirs: bool = True) -> dict:
        """List S3 objects under prefix via boto3.

        When include_subdirs=True (default), uses Delimiter='/' so both
        CommonPrefixes (subdirectories) and Contents (objects in this level)
        are returned.  When include_subdirs=False, lists flat (no delimiter)
        so all matching objects are returned regardless of nesting.

        Returns dict with keys:
            - prefixes: list of common prefix strings
            - keys: list of object key strings
            - objects: list of {key, size} dicts (when Contents present)
            - error_code: None on success
        """
        if not self.allow_s3_archive_read:
            raise PermissionError(
                f"S3 access to s3://{bucket}/{prefix} blocked. Pass --allow-s3-archive-read to enable."
            )
        if not _BOTO3_AVAILABLE:
            return {"prefixes": [], "keys": [], "objects": [], "error_code": "BOTO3_UNAVAILABLE"}
        kwargs: dict = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": max_keys}
        if include_subdirs:
            kwargs["Delimiter"] = "/"
        if requester_pays:
            kwargs["RequestPayer"] = "requester"
        try:
            s3 = boto3.client("s3")
            resp = s3.list_objects_v2(**kwargs)
            prefixes = [cp["Prefix"] for cp in resp.get("CommonPrefixes") or []]
            contents = resp.get("Contents") or []
            keys = [obj["Key"] for obj in contents]
            objects = [{"key": obj["Key"], "size": obj.get("Size", 0)} for obj in contents]
            return {"prefixes": prefixes, "keys": keys, "objects": objects, "error_code": None}
        except (NoCredentialsError, _BotocoreMissingDepError):
            return {"prefixes": [], "keys": [], "objects": [], "error_code": "NO_CREDENTIALS"}
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            return {"prefixes": [], "keys": [], "objects": [], "error_code": code}
        except Exception as exc:
            return {"prefixes": [], "keys": [], "objects": [], "error_code": str(exc)}

    def s3_read_object(self, bucket: str, key: str, requester_pays: bool = True) -> bytes:
        """Read an S3 object body via boto3."""
        if not self.allow_s3_archive_read:
            raise PermissionError(
                f"S3 access to s3://{bucket}/{key} blocked. Pass --allow-s3-archive-read to enable."
            )
        if not _BOTO3_AVAILABLE:
            raise RuntimeError("boto3 unavailable")
        kwargs: dict = {"Bucket": bucket, "Key": key}
        if requester_pays:
            kwargs["RequestPayer"] = "requester"
        s3 = boto3.client("s3")
        resp = s3.get_object(**kwargs)
        data: bytes = resp["Body"].read()
        self._track_bytes(len(data), f"s3:{bucket}")
        return data

    def s3_download(self, s3_path: str, local_path: Path, requester_pays: bool = True) -> int:
        """S3 download through chokepoint (boto3-backed)."""
        # Parse s3://bucket/key
        without_scheme = s3_path[len("s3://"):]
        bucket, _, key = without_scheme.partition("/")
        data = self.s3_read_object(bucket, key, requester_pays=requester_pays)
        local_path.write_bytes(data)
        file_size = len(data)
        return file_size
    
    def _track_bytes(self, count: int, source: str):
        self.bytes_downloaded += count
        self.bytes_by_source[source] = self.bytes_by_source.get(source, 0) + count
    
    def check_budget(self, total_cap: int, source_cap: int | None = None, source: str | None = None):
        """Check if budget exceeded."""
        if self.bytes_downloaded > total_cap:
            raise BudgetExceededError(
                f"Total download budget exceeded: {self.bytes_downloaded} > {total_cap}"
            )
        if source_cap and source and source_cap > 0:
            source_bytes = self.bytes_by_source.get(source, 0)
            if source_bytes > source_cap:
                raise BudgetExceededError(
                    f"Source budget exceeded for {source}: {source_bytes} > {source_cap}"
                )


class BudgetExceededError(Exception):
    """Raised when download budget is exceeded."""
    pass

# AWS identity preflight helper
def _aws_identity_preflight(chokepoint: NetworkChokepoint) -> tuple[bool, str]:
    """Check AWS credentials via boto3 STS. Returns (available, account_suffix)."""
    if not _BOTO3_AVAILABLE:
        return False, ""
    try:
        sts = boto3.client("sts")
        data = sts.get_caller_identity()
        account = data.get("Account", "")
        # Never store more than last 4 digits
        suffix = account[-4:] if account else ""
        return True, suffix
    except (_BotocoreMissingDepError, NoCredentialsError):
        return False, ""
    except Exception:
        return False, ""


def _get_git_info() -> tuple[str, bool]:
    """Get current git SHA from .git/HEAD files. Dirty detection uses index mtime heuristic."""
    git_dir = Path(__file__).resolve().parent.parent.parent.parent.parent / ".git"
    sha = ""
    dirty = False
    try:
        head_ref = (git_dir / "HEAD").read_text().strip()
        if head_ref.startswith("ref: "):
            branch_path = head_ref[5:]
            ref_file = git_dir / branch_path
            if ref_file.exists():
                sha = ref_file.read_text().strip()[:10]
        else:
            sha = head_ref[:10]
        # Heuristic: if MERGE_HEAD or CHERRY_PICK_HEAD exist the tree is dirty-ish.
        dirty = (git_dir / "MERGE_HEAD").exists() or (git_dir / "CHERRY_PICK_HEAD").exists()
    except Exception:
        sha = "unknown"
        dirty = False
    return sha, dirty


def _search_json_nested(obj: Any, terms: list[str], max_depth: int = 10) -> list[dict[str, Any]]:
    """Schema-tolerant JSON traversal to find deployment-like fields."""
    matches = []
    
    def _search_recursive(current: Any, depth: int, path: str):
        if depth > max_depth:
            return
        if isinstance(current, dict):
            for key, value in current.items():
                key_lower = key.lower()
                if any(term in key_lower for term in terms):
                    matches.append({
                        "path": f"{path}.{key}" if path else key,
                        "key": key,
                        "value": value,
                        "parent": current
                    })
                _search_recursive(value, depth + 1, f"{path}.{key}" if path else key)
        elif isinstance(current, list):
            for i, item in enumerate(current):
                _search_recursive(item, depth + 1, f"{path}[{i}]")
    
    _search_recursive(obj, 0, "")
    return matches


def _extract_candidate_from_match(match: dict, block_data: dict, source_path: str, source_hash: str) -> DeploymentCandidate | None:
    """Extract a deployment candidate from a matched JSON subtree."""
    parent = match.get("parent", {})
    value = match.get("value")
    
    symbol = None
    deployer = None
    builder_ns = None
    oracle_fields = None
    margin_fields = None
    fee_fields = None
    
    search_obj = parent if isinstance(parent, dict) else {}
    if isinstance(value, dict):
        search_obj = {**search_obj, **value}
    
    for key, val in search_obj.items():
        key_lower = key.lower()
        if any(x in key_lower for x in ["symbol", "coin", "name", "asset"]):
            if isinstance(val, str) and val.upper() == val and len(val) <= 10:
                symbol = val
        if any(x in key_lower for x in ["deployer", "builder", "deployeraddress"]):
            deployer = str(val) if val else None
        if "namespace" in key_lower:
            builder_ns = str(val) if val else None
        if "oracle" in key_lower:
            oracle_fields = oracle_fields or {}
            oracle_fields[key] = val
        if any(x in key_lower for x in ["margin", "leverage", "maxlev"]):
            margin_fields = margin_fields or {}
            margin_fields[key] = val
        if "fee" in key_lower:
            fee_fields = fee_fields or {}
            fee_fields[key] = val
    
    block_time = block_data.get("block_time", block_data.get("timestamp", ""))
    block_height = block_data.get("height", block_data.get("block_number", block_data.get("number")))
    tx_hash = None
    if isinstance(value, dict):
        tx_hash = value.get("tx_hash", value.get("hash", value.get("actionHash")))
    
    return DeploymentCandidate(
        block_time=block_time,
        block_height=block_height if isinstance(block_height, int) else None,
        action_type=match.get("key", "unknown"),
        deployer_address=deployer,
        builder_namespace=builder_ns,
        symbol=symbol,
        symbol_extractable=symbol is not None,
        oracle_fields=oracle_fields,
        margin_fields=margin_fields,
        fee_fields=fee_fields,
        raw_excerpt=json.dumps(value, default=str)[:500] if value else "",
        source_path=source_path,
        source_content_hash=source_hash,
        tx_hash_or_index=tx_hash
    )


# ---------------------------------------------------------------------------
# Block-range layout detection and metadata
# ---------------------------------------------------------------------------

def _parse_range_prefixes(prefixes: list[str]) -> list[int]:
    """Extract block-range numbers from root prefixes like 'explorer_blocks/100000000/'."""
    pattern = re.compile(rf"^{EXPLORER_BLOCK_PREFIX}/(\d+)/$")
    ranges: list[int] = []
    for p in prefixes:
        m = pattern.match(p)
        if m:
            ranges.append(int(m.group(1)))
    return sorted(ranges)


def _infer_stride(ranges: list[int]) -> int | None:
    """Infer the stride between consecutive block-range prefixes."""
    if len(ranges) < 2:
        return None
    diffs = [ranges[i+1] - ranges[i] for i in range(len(ranges)-1)]
    if not diffs:
        return None
    # Return the most common diff (mode)
    from collections import Counter
    counter = Counter(diffs)
    return counter.most_common(1)[0][0]


# ---------------------------------------------------------------------------
# Timestamp sampling from block files
# ---------------------------------------------------------------------------

def _sample_block_timestamps(
    chokepoint: NetworkChokepoint,
    range_prefixes: list[str],
    max_sample_files: int,
    max_sample_bytes: int,
    target_start_date: str | None = None,
) -> list[BlockTimestampSample]:
    """Sample a bounded number of block files from each range prefix to extract timestamps.

    When target_start_date is provided, sampling prioritises range prefixes whose
    block-number start is closest to the estimated block for that date, so the
    resulting timestamp anchors actually bracket the target window.

    Returns a list of BlockTimestampSample entries.
    Raises BudgetExceededError when cumulative bytes exceed max_sample_bytes.
    """
    samples: list[BlockTimestampSample] = []
    files_sampled = 0
    cumulative_bytes = 0

    # -----------------------------------------------------------------------
    # Strategy: sample evenly across the full block-range span so that we get
    # timestamp anchors at both ends and in the middle.  If a target date is
    # given we bias sampling toward the range most likely to contain it.
    # -----------------------------------------------------------------------
    range_nums = _parse_range_prefixes(range_prefixes)
    if not range_nums:
        return samples

    # Build an ordered list of (range_num, range_prefix) pairs
    range_pairs: list[tuple[int, str]] = []
    for rp in range_prefixes:
        rn = _parse_range_prefixes([rp])
        if rn:
            range_pairs.append((rn[0], rp))
    range_pairs.sort(key=lambda x: x[0])

    if not range_pairs:
        return samples

    # Always sample the first and last range for boundary anchors
    # Then sample evenly in between
    if len(range_pairs) <= 4:
        ordered_pairs = range_pairs
    else:
        # Keep first, last, and evenly spaced middle ones
        ordered_pairs = [range_pairs[0]]
        step = max(1, (len(range_pairs) - 2) // 3)
        for i in range(1, len(range_pairs) - 1, step):
            ordered_pairs.append(range_pairs[i])
        ordered_pairs.append(range_pairs[-1])

    for _, range_prefix in ordered_pairs:
        if files_sampled >= max_sample_files:
            break

        # List child keys under this range prefix (flat, no delimiter)
        listing = chokepoint.s3_list_prefix(
            EXPLORER_BLOCK_BUCKET,
            range_prefix,
            requester_pays=True,
            max_keys=100,
            include_subdirs=False,
        )
        if listing.get("error_code"):
            continue

        child_keys = listing.get("keys", [])
        if not child_keys:
            continue

        # Sample at most 5 files per range prefix; pick first, middle, last
        # to get temporal spread within the range
        num_to_sample = min(5, max_sample_files - files_sampled)
        if len(child_keys) <= num_to_sample:
            sample_keys = child_keys
        else:
            indices = [0, len(child_keys) // 2, len(child_keys) - 1]
            # Add two more evenly spaced indices
            quarter = len(child_keys) // 4
            indices.extend([quarter, 3 * quarter])
            indices = sorted(set(indices))
            sample_keys = [child_keys[i] for i in indices if i < len(child_keys)]

        for key in sample_keys:
            if files_sampled >= max_sample_files:
                break

            # Read the block file
            try:
                data = chokepoint.s3_read_object(EXPLORER_BLOCK_BUCKET, key, requester_pays=True)
            except Exception:
                continue

            byte_count = len(data)
            content_hash = hashlib.sha256(data).hexdigest()

            # Check cumulative budget
            cumulative_bytes += byte_count
            if cumulative_bytes > max_sample_bytes:
                raise BudgetExceededError(
                    f"Timestamp sampling budget exceeded: {cumulative_bytes} > {max_sample_bytes}"
                )
            
            # Decompress and parse
            try:
                decompressed = lz4.frame.decompress(data)
            except Exception:
                decompressed = data

            # Parse to extract block number and timestamp
            block_number = None
            block_timestamp = None
            action_count = 0
            parse_status = "ok"

            try:
                # Try MessagePack first (the actual archive format: .rmp.lz4)
                if _MSGPACK_AVAILABLE:
                    try:
                        blocks = msgpack.unpackb(decompressed, raw=False)
                        if isinstance(blocks, list) and blocks:
                            # Each block has 'header' with 'height' and 'block_time'
                            first_block = blocks[0]
                            if isinstance(first_block, dict):
                                hdr = first_block.get("header", {})
                                if isinstance(hdr, dict):
                                    bn = hdr.get("height", hdr.get("block_number"))
                                    if bn is not None:
                                        try:
                                            block_number = int(bn)
                                        except (ValueError, TypeError):
                                            pass
                                    ts = hdr.get("block_time", hdr.get("timestamp"))
                                    if ts is not None:
                                        block_timestamp = str(ts)
                                # Count actions across all blocks in the file
                                for blk in blocks:
                                    if isinstance(blk, dict):
                                        txs = blk.get("txs", blk.get("actions", []))
                                        if isinstance(txs, list):
                                            action_count += len(txs)
                                # If we got the first block's header, we're done
                                if block_number is not None:
                                    pass  # success
                    except Exception:
                        pass

                # Fallback: try JSONL (for compatibility with older formats)
                if block_number is None:
                    lines = decompressed.splitlines()
                    for line in lines[:10]:
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                            if isinstance(obj, dict):
                                # Try to extract block number
                                bn = obj.get("block_number", obj.get("height", obj.get("number")))
                                if bn is not None:
                                    try:
                                        block_number = int(bn)
                                    except (ValueError, TypeError):
                                        pass
                                # Try to extract block timestamp
                                ts = obj.get("block_time", obj.get("timestamp", obj.get("time")))
                                if ts is not None:
                                    block_timestamp = str(ts)
                                # Count actions
                                actions = obj.get("actions", obj.get("events", []))
                                if isinstance(actions, list):
                                    action_count += len(actions)
                        except Exception:
                            continue
            except Exception:
                parse_status = "parse_error"
            
            samples.append(BlockTimestampSample(
                source_key=key,
                top_level_range_prefix=range_prefix,
                block_number=block_number,
                block_timestamp_utc=block_timestamp,
                parse_status=parse_status,
                byte_count=byte_count,
                content_hash=content_hash,
                action_type_count=action_count,
            ))
            files_sampled += 1
    
    return samples


# ---------------------------------------------------------------------------
# Date-to-block mapping
# ---------------------------------------------------------------------------

def _parse_timestamp(ts_str: str | None) -> int | None:
    """Parse a timestamp string to epoch milliseconds. Handles ms, us, ns, and ISO formats."""
    if not ts_str:
        return None
    try:
        # Try integer (ms or us)
        val = int(ts_str)
        # If > 1e13 it's microseconds, convert to ms
        if val > 10_000_000_000_000:
            return val // 1_000
        # If < 1e9 it's likely seconds, convert to ms
        if val < 1_000_000_000:
            return val * 1_000
        return val
    except (ValueError, TypeError):
        pass
    # Try ISO format — strip excess fractional digits beyond microseconds
    # to avoid strptime overflow on nanosecond timestamps
    s = str(ts_str)
    # If there are more than 6 fractional digits, truncate to 6
    if "." in s:
        main, frac = s.split(".", 1)
        s = main + "." + frac[:6]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=UTC)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


def _estimate_block_for_timestamp(
    ts_ms: int,
    parsed: list[tuple[int, str | None, int]],
) -> int | None:
    """Estimate block number for a given timestamp using linear interpolation.

    parsed must be sorted by block number.  Returns None when interpolation is
    impossible (e.g. only one anchor or timestamps are not monotonic).
    """
    if len(parsed) < 2:
        return None

    # Find the bracket: the last sample whose ts <= target and the first whose ts >= target
    lo_idx = -1
    hi_idx = len(parsed)
    for i, (bn, prefix, ts) in enumerate(parsed):
        if ts <= ts_ms:
            lo_idx = i
        else:
            hi_idx = i
            break

    # Perfect bracket
    if lo_idx >= 0 and hi_idx < len(parsed):
        lo_bn, _, lo_ts = parsed[lo_idx]
        hi_bn, _, hi_ts = parsed[hi_idx]
        if hi_ts != lo_ts:
            frac = (ts_ms - lo_ts) / (hi_ts - lo_ts)
            return lo_bn + int(frac * (hi_bn - lo_bn))
        return lo_bn

    # Before first sample — extrapolate backward cautiously
    if lo_idx < 0 and hi_idx == 0:
        return parsed[0][0]

    # After last sample — extrapolate forward cautiously
    if hi_idx == len(parsed):
        return parsed[-1][0]

    return parsed[lo_idx][0] if lo_idx >= 0 else parsed[0][0]


def _map_date_to_block_range(
    start_date: str,
    end_date: str | None,
    samples: list[BlockTimestampSample],
) -> dict[str, Any]:
    """Map a date window to block ranges using timestamp samples.

    Uses linear interpolation between sampled (block_number, timestamp) anchors
    to estimate the block range covering [start_date, end_date].

    Returns a dict with:
        - status: mapping status string
        - mapped_ranges: list of range prefixes that cover the date window
        - start_block: approximate start block number
        - end_block: approximate end block number
        - sample_count: number of samples used
        - date_range: human-readable date coverage of samples
    """
    if not samples:
        return {
            "status": "HIP3_EXPLORER_BLOCK_DATE_MAPPING_INSUFFICIENT_SAMPLES",
            "mapped_ranges": [],
            "start_block": None,
            "end_block": None,
            "sample_count": 0,
            "date_range": "",
        }

    # Parse all timestamps
    parsed: list[tuple[int, str | None, int]] = []
    for s in samples:
        ts_ms = _parse_timestamp(s.block_timestamp_utc)
        if ts_ms is not None and s.block_number is not None:
            parsed.append((s.block_number, s.top_level_range_prefix, ts_ms))

    if not parsed:
        return {
            "status": "HIP3_EXPLORER_BLOCK_TIMESTAMP_PARSE_FAILED",
            "mapped_ranges": [],
            "start_block": None,
            "end_block": None,
            "sample_count": len(samples),
            "date_range": "",
        }

    # Sort by block number
    parsed.sort(key=lambda x: x[0])

    # Convert dates to epoch ms
    start_dt = datetime.fromisoformat(start_date).replace(tzinfo=UTC)
    start_ms = int(start_dt.timestamp() * 1000)
    end_dt = datetime.now(UTC) if end_date is None else datetime.fromisoformat(end_date).replace(tzinfo=UTC)
    end_ms = int(end_dt.timestamp() * 1000)

    # Check if dates are within sample range (allow 60-day margin on each side)
    min_ts = parsed[0][2]
    max_ts = parsed[-1][2]
    margin_ms = 86400_000 * 60
    if start_ms < min_ts - margin_ms:
        return {
            "status": "HIP3_EXPLORER_BLOCK_DATE_OUT_OF_RANGE",
            "mapped_ranges": [],
            "start_block": None,
            "end_block": None,
            "sample_count": len(parsed),
            "date_range": f"{datetime.fromtimestamp(min_ts/1000, UTC).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(max_ts/1000, UTC).strftime('%Y-%m-%d')}",
        }

    # Estimate block numbers for start and end dates using interpolation
    start_block = _estimate_block_for_timestamp(start_ms, parsed)
    end_block = _estimate_block_for_timestamp(end_ms, parsed)

    if start_block is None or end_block is None:
        return {
            "status": "HIP3_EXPLORER_BLOCK_DATE_MAPPING_INSUFFICIENT_SAMPLES",
            "mapped_ranges": [],
            "start_block": start_block,
            "end_block": end_block,
            "sample_count": len(parsed),
            "date_range": f"{datetime.fromtimestamp(min_ts/1000, UTC).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(max_ts/1000, UTC).strftime('%Y-%m-%d')}",
        }

    # Ensure start <= end
    if start_block > end_block:
        start_block, end_block = end_block, start_block

    # Collect all range prefixes whose block-number span overlaps [start_block, end_block].
    # Each range covers [range_num, range_num + stride).  We include it if the interval
    # [range_num, range_num + stride) intersects [start_block, end_block].
    # Collect unique prefixes for stride inference
    unique_prefixes = [p for _, p, _ in parsed if p is not None]
    stride = _infer_stride(_parse_range_prefixes(unique_prefixes)) or 100_000_000
    mapped_ranges: list[str] = []
    for bn, prefix, ts in parsed:
        if prefix is None:
            continue
        range_num = _parse_range_prefixes([prefix])
        if not range_num:
            continue
        rn = range_num[0]
        range_end = rn + stride
        # Overlap test: [rn, range_end) intersects [start_block, end_block]
        if rn <= end_block and range_end >= start_block:
            if prefix not in mapped_ranges:
                mapped_ranges.append(prefix)

    # Deduplicate and sort
    mapped_ranges = sorted(set(mapped_ranges))

    if not mapped_ranges:
        return {
            "status": "HIP3_EXPLORER_BLOCK_DATE_MAPPING_INSUFFICIENT_SAMPLES",
            "mapped_ranges": [],
            "start_block": start_block,
            "end_block": end_block,
            "sample_count": len(parsed),
            "date_range": f"{datetime.fromtimestamp(min_ts/1000, UTC).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(max_ts/1000, UTC).strftime('%Y-%m-%d')}",
        }

    return {
        "status": "HIP3_EXPLORER_BLOCK_DATE_MAPPING_READY",
        "mapped_ranges": mapped_ranges,
        "start_block": start_block,
        "end_block": end_block,
        "sample_count": len(parsed),
        "date_range": f"{datetime.fromtimestamp(min_ts/1000, UTC).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(max_ts/1000, UTC).strftime('%Y-%m-%d')}",
    }


# ---------------------------------------------------------------------------
# Action-type inventory helpers (P1 explorer-block action-type enumeration)
# ---------------------------------------------------------------------------

def _redact_value(v: Any, max_len: int = 80) -> Any:
    """Truncate string values for sample records; leave numeric/bool types intact."""
    if isinstance(v, str):
        return v[:max_len] if len(v) > max_len else v
    if isinstance(v, dict):
        return {k: _redact_value(val, max_len) for k, val in list(v.items())[:10]}
    if isinstance(v, list):
        return [_redact_value(item, max_len) for item in v[:5]]
    return v


def _extract_field_paths(obj: Any, prefix: str = "", max_depth: int = 6) -> list[str]:
    """Recursively extract dotted field paths from a JSON object."""
    paths: list[str] = []
    if max_depth <= 0:
        return paths
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            paths.append(full)
            paths.extend(_extract_field_paths(v, full, max_depth - 1))
    elif isinstance(obj, list):
        for item in obj[:3]:
            paths.extend(_extract_field_paths(item, prefix, max_depth - 1))
    return paths


def _inventory_action_types_from_decoded(
    decoded: Any,
    inventory: dict[str, int],
    schema_shapes: list[frozenset],
    nested_paths: set[str],
    sample_records: list[dict],
    opaque_count_holder: list[int],
    hip3_candidate_holder: list[int],
    hip3_terms: list[str],
    max_samples: int = 5,
) -> None:
    """Walk decoded block data and tally action types, schema shapes, and nested paths.

    Modifies inventory, schema_shapes, nested_paths, sample_records in place.
    opaque_count_holder[0] and hip3_candidate_holder[0] are incremented counters.
    """
    # decoded may be a list of blocks or a single block dict
    blocks: list[Any] = []
    if isinstance(decoded, list):
        blocks = decoded
    elif isinstance(decoded, dict):
        blocks = [decoded]
    else:
        opaque_count_holder[0] += 1
        return

    for block in blocks:
        if not isinstance(block, dict):
            opaque_count_holder[0] += 1
            continue

        # Collect top-level schema shape
        top_fields = frozenset(block.keys())
        if top_fields not in schema_shapes:
            schema_shapes.append(top_fields)

        # Collect nested field paths
        for p in _extract_field_paths(block):
            nested_paths.add(p)

        # Walk transactions/actions within the block
        txs: list[Any] = []
        for tx_key in ("txs", "actions", "transactions"):
            candidate = block.get(tx_key)
            if isinstance(candidate, list):
                txs = candidate
                break

        if not txs:
            opaque_count_holder[0] += 1
            continue

        for tx in txs:
            if not isinstance(tx, dict):
                opaque_count_holder[0] += 1
                continue

            # Extract action type — handle multiple schema layouts:
            #   1. tx["type"] = "SomeAction"
            #   2. tx["action"]["type"] = "SomeAction"
            #   3. tx["actions"][0]["type"] = "SomeAction"   (actual HL format)
            action_type: str = "unknown"
            for ak in ("type", "actionType", "action_type", "kind"):
                av = tx.get(ak)
                if isinstance(av, str) and av:
                    action_type = av
                    break
            if action_type == "unknown":
                for ak in ("action",):
                    av = tx.get(ak)
                    if isinstance(av, dict):
                        inner_type = av.get("type", av.get("actionType", av.get("kind", "")))
                        if isinstance(inner_type, str) and inner_type:
                            action_type = inner_type
                        break
            if action_type == "unknown":
                # Try tx["actions"] list (actual Hyperliquid explorer block format)
                nested_actions = tx.get("actions")
                if isinstance(nested_actions, list):
                    for na in nested_actions:
                        if isinstance(na, dict):
                            t = na.get("type", na.get("actionType", na.get("kind", "")))
                            if isinstance(t, str) and t:
                                action_type = t
                                break

            # If nested actions list has multiple entries, count each action type separately
            nested_actions_list = tx.get("actions")
            if isinstance(nested_actions_list, list) and len(nested_actions_list) > 1:
                for na in nested_actions_list:
                    if isinstance(na, dict):
                        t = na.get("type", na.get("actionType", na.get("kind", "")))
                        counted = t if (isinstance(t, str) and t) else action_type
                        inventory[counted] = inventory.get(counted, 0) + 1
                    else:
                        inventory[action_type] = inventory.get(action_type, 0) + 1
            else:
                inventory[action_type] = inventory.get(action_type, 0) + 1

            # Passively check for HIP-3 candidate terms
            tx_str = json.dumps(tx, default=str).lower()
            if any(t in tx_str for t in hip3_terms):
                hip3_candidate_holder[0] += 1

            # Collect sample records (hash-stable by redacting values)
            if len(sample_records) < max_samples:
                sample_records.append(_redact_value(tx))


@dataclass
class ActionInventoryResult:
    """Result of the action-type inventory probe (--inventory-action-types-only)."""
    status: ScoutStatus | str
    study_id: str = STUDY_ID
    run_id: str = ""
    created_at_utc: str = ""
    git_sha: str = ""
    git_dirty: bool = False
    repo_root: str = ""
    command_args: list[str] = field(default_factory=list)
    safety_mode: str = SAFETY_MODE
    schema_version: str = SCHEMA_VERSION
    final_status: str = ""
    # Inventory counters
    files_listed: int = 0
    files_read: int = 0
    bytes_downloaded: int = 0
    inferred_layout: str = ""
    action_type_counts: dict[str, int] = field(default_factory=dict)
    top_level_schema_shapes: list[list[str]] = field(default_factory=list)
    nested_field_paths: list[str] = field(default_factory=list)
    sample_redacted_records: list[Any] = field(default_factory=list)
    decode_method_used: str = "unknown"
    decode_failures_count: int = 0
    opaque_records_count: int = 0
    hip3_candidate_passive_count: int = 0
    # Access/credential metadata
    aws_identity_available: bool = False
    aws_account_suffix: str = ""
    # Budget limits used
    max_block_files: int = 20
    explorer_block_budget_bytes: int = 50_000_000
    # Explorer block source
    explorer_block_bucket: str = EXPLORER_BLOCK_BUCKET
    explorer_block_root_prefix: str = EXPLORER_BLOCK_PREFIX
    # Root listing metadata
    explorer_root_prefixes: list[str] = field(default_factory=list)
    explorer_root_keys: list[str] = field(default_factory=list)
    explorer_root_listing_status: str = ""


def run_inventory_probe(
    max_block_files: int = 20,
    explorer_block_budget_bytes: int = 50_000_000,
    download_budget_bytes: int = 50_000_000,
    allow_network_public: bool = False,
    allow_s3_archive_read: bool = False,
    command_args: list[str] | None = None,
) -> ActionInventoryResult:
    """Execute the P1 action-type inventory probe (--inventory-action-types-only).

    Inventories real explorer-block action types without searching for deployment events.
    Does NOT emit HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS.
    """
    sha, dirty = _get_git_info()
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_inventory_" + hashlib.sha256(b"inventory").hexdigest()[:8]

    inv = ActionInventoryResult(
        status=ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_READY,
        run_id=run_id,
        created_at_utc=datetime.now(UTC).isoformat(),
        git_sha=sha,
        git_dirty=dirty,
        repo_root=str(Path(__file__).resolve().parents[4]),
        command_args=command_args or [],
        max_block_files=max_block_files,
        explorer_block_budget_bytes=explorer_block_budget_bytes,
    )

    chokepoint = NetworkChokepoint(allow_network_public, allow_s3_archive_read)

    # Guard: S3 access requires --allow-s3-archive-read
    if not allow_s3_archive_read:
        # Without S3 access we can't read anything; return credentials-required analog
        inv.status = ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED
        inv.final_status = inv.status.value
        return inv

    # AWS identity preflight
    avail, suffix = _aws_identity_preflight(chokepoint)
    inv.aws_identity_available = avail
    inv.aws_account_suffix = suffix

    # Step 1: discover layout
    layout_info = _discover_explorer_block_layout(chokepoint)
    inv.explorer_root_listing_status = layout_info["status"].value
    inv.inferred_layout = layout_info["layout"]
    inv.explorer_root_prefixes = layout_info["prefixes"]
    inv.explorer_root_keys = layout_info["keys"]

    # Handle access/credential failures
    if layout_info["status"] in (
        ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_ACCESS_DENIED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED,
    ):
        inv.status = layout_info["status"]
        inv.final_status = inv.status.value
        return inv

    if layout_info["status"] == ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_EMPTY:
        inv.status = ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_EMPTY
        inv.final_status = inv.status.value
        return inv

    # Stop if layout unknown
    if layout_info["layout"] == "unknown" or layout_info["layout"] == "":
        inv.status = ScoutStatus.HIP3_EXPLORER_BLOCK_LAYOUT_UNKNOWN
        inv.final_status = inv.status.value
        return inv

    # Step 2: collect a bounded sample of files to read
    prefixes = layout_info["prefixes"]
    keys_to_read: list[str] = []

    if layout_info["layout"] == "flat_block_files":
        keys_to_read = layout_info["keys"][:max_block_files]
    elif layout_info["layout"] in ("block_range_partitioned", "date_partitioned"):
        # For block-range layout: list the first few range prefixes to get actual file keys
        files_needed = max_block_files
        for rp in prefixes[:5]:  # Sample up to first 5 range prefixes
            if len(keys_to_read) >= files_needed:
                break
            sub_listing = chokepoint.s3_list_prefix(
                EXPLORER_BLOCK_BUCKET,
                rp,
                requester_pays=True,
                max_keys=files_needed,
                include_subdirs=False,
            )
            if sub_listing.get("error_code"):
                continue
            sub_keys = sub_listing.get("keys", [])
            keys_to_read.extend(sub_keys[: files_needed - len(keys_to_read)])
    else:
        # fallback
        keys_to_read = layout_info["keys"][:max_block_files]

    inv.files_listed = len(keys_to_read)

    # Step 3: decode files and build inventory
    inventory: dict[str, int] = {}
    schema_shapes: list[frozenset] = []
    nested_paths: set[str] = set()
    sample_records: list[Any] = []
    opaque_count_holder = [0]
    hip3_count_holder = [0]
    decode_method = "unknown"
    decode_failures = 0
    bytes_consumed = 0
    hip3_lower_terms = [t.lower() for t in DEPLOYMENT_SEARCH_TERMS]

    for key in keys_to_read:
        if bytes_consumed >= min(explorer_block_budget_bytes, download_budget_bytes):
            break

        try:
            data = chokepoint.s3_read_object(EXPLORER_BLOCK_BUCKET, key, requester_pays=True)
        except Exception:
            decode_failures += 1
            continue

        bytes_consumed += len(data)

        # Decompress
        try:
            decompressed = lz4.frame.decompress(data)
        except Exception:
            decompressed = data

        # Try decode: MessagePack first, then JSON
        decoded: Any = None
        used_method = "unknown"
        if _MSGPACK_AVAILABLE:
            try:
                decoded = msgpack.unpackb(decompressed, raw=False)
                used_method = "msgpack"
            except Exception:
                pass

        if decoded is None:
            # Try JSON lines or single JSON
            try:
                # Try as single JSON
                decoded = json.loads(decompressed)
                used_method = "json"
            except Exception:
                # Try JSONL
                lines = decompressed.splitlines()
                decoded_lines = []
                for line in lines:
                    if not line:
                        continue
                    try:
                        decoded_lines.append(json.loads(line))
                        used_method = "json"
                    except Exception:
                        pass
                if decoded_lines:
                    decoded = decoded_lines

        if decoded is None:
            decode_failures += 1
            opaque_count_holder[0] += 1
            continue

        if decode_method == "unknown":
            decode_method = used_method

        _inventory_action_types_from_decoded(
            decoded,
            inventory,
            schema_shapes,
            nested_paths,
            sample_records,
            opaque_count_holder,
            hip3_count_holder,
            hip3_lower_terms,
        )
        inv.files_read += 1

    inv.bytes_downloaded = chokepoint.bytes_downloaded
    inv.action_type_counts = inventory
    inv.top_level_schema_shapes = [sorted(s) for s in schema_shapes]
    inv.nested_field_paths = sorted(nested_paths)
    inv.sample_redacted_records = sample_records
    inv.decode_method_used = decode_method
    inv.decode_failures_count = decode_failures
    inv.opaque_records_count = opaque_count_holder[0]
    inv.hip3_candidate_passive_count = hip3_count_holder[0]

    # Determine final status
    if not inventory and opaque_count_holder[0] > 0:
        inv.status = ScoutStatus.HIP3_EXPLORER_BLOCK_ACTION_SCHEMA_OPAQUE
    elif not inventory:
        inv.status = ScoutStatus.HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_EMPTY
    else:
        inv.status = ScoutStatus.HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_READY

    inv.final_status = inv.status.value
    return inv


# ---------------------------------------------------------------------------
# Core probe workflow
# ---------------------------------------------------------------------------

def _discover_explorer_block_layout(chokepoint: NetworkChokepoint) -> dict:
    """Discover the key layout under the explorer_blocks bucket via NetworkChokepoint.

    Returns a dict with keys:
        - status: ScoutStatus indicating the outcome of the root listing.
        - layout: one of "date_partitioned", "block_range_partitioned", "flat_block_files", "unknown", or "".
        - prefixes: list of up to 20 child prefixes.
        - keys: list of up to 20 object keys.
        - range_numbers: list of parsed block-range numbers (if block_range_partitioned).
        - stride: inferred stride between ranges (if possible).
    """
    if not _BOTO3_AVAILABLE:
        return {
            "status": ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED,
            "layout": "",
            "prefixes": [],
            "keys": [],
            "range_numbers": [],
            "stride": None,
        }
    listing = chokepoint.s3_list_prefix(
        EXPLORER_BLOCK_BUCKET,
        f"{EXPLORER_BLOCK_PREFIX}/",
        requester_pays=True,
    )
    error_code = listing.get("error_code")
    if error_code:
        if error_code == "NO_CREDENTIALS":
            status = ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED
        elif error_code in ("AccessDenied", "AllAccessDisabled", "RequestorPaysBucketBillingRequirementNotMet"):
            status = ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_ACCESS_DENIED
        else:
            status = ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED
        return {"status": status, "layout": "", "prefixes": [], "keys": [], "range_numbers": [], "stride": None}

    prefixes = listing["prefixes"]
    keys = listing["keys"]
    if not prefixes and not keys:
        return {"status": ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_EMPTY, "layout": "", "prefixes": [], "keys": [], "range_numbers": [], "stride": None}

    layout = "unknown"
    date_pat = re.compile(r"\d{4}/\d{2}/\d{2}/$")
    block_range_pat = re.compile(r"\d{10,}\.json$")
    # Strip the leading prefix before matching
    rel_prefixes = [p[len(f"{EXPLORER_BLOCK_PREFIX}/"):] for p in prefixes]
    if any(date_pat.search(rp) for rp in rel_prefixes):
        layout = "date_partitioned"
    elif any(block_range_pat.search(k) for k in keys):
        layout = "block_range_partitioned"
    elif keys and not prefixes:
        layout = "flat_block_files"
    elif prefixes:
        # Check if prefixes look like block ranges (e.g., 'explorer_blocks/100000000/')
        range_nums = _parse_range_prefixes(prefixes)
        if range_nums:
            layout = "block_range_partitioned"
    stride = None
    range_numbers: list[int] = []
    if layout == "block_range_partitioned":
        range_numbers = _parse_range_prefixes(prefixes)
        stride = _infer_stride(range_numbers)
    return {
        "status": ScoutStatus.HIP3_EXPLORER_BLOCK_LAYOUT_DISCOVERED,
        "layout": layout,
        "prefixes": prefixes[:20],
        "keys": keys[:20],
        "range_numbers": range_numbers,
        "stride": stride,
    }


def _validate_explorer_block_source(start_date: str, chokepoint: NetworkChokepoint) -> tuple[bool, list[str]]:
    """Validate that the explorer‑block S3 source exists and is reachable."""
    prefix = f"{EXPLORER_BLOCK_PREFIX}/{start_date}/"
    listing = chokepoint.s3_list_prefix(EXPLORER_BLOCK_BUCKET, prefix, requester_pays=True)
    if listing.get("error_code"):
        return False, []
    keys = listing["keys"]
    return (len(keys) > 0), keys[:10]


def _list_explorer_block_files(
    start_date: str,
    end_date: str | None,
    max_days: int,
    max_files: int,
    chokepoint: NetworkChokepoint,
) -> list[tuple[str, int]]:
    """List S3 explorer block files via NetworkChokepoint. Returns (s3_path, size_bytes) list."""
    if not _BOTO3_AVAILABLE:
        return []
    start_dt = datetime.fromisoformat(start_date).replace(tzinfo=UTC)
    end_dt = datetime.now(UTC) if end_date is None else datetime.fromisoformat(end_date).replace(tzinfo=UTC)
    if (end_dt - start_dt).days > max_days:
        end_dt = start_dt + timedelta(days=max_days)

    day_prefixes: list[str] = []
    cur = start_dt
    while cur <= end_dt:
        day_prefixes.append(f"{EXPLORER_BLOCK_PREFIX}/{cur.strftime('%Y/%m/%d')}/")
        cur += timedelta(days=1)

    files: list[tuple[str, int]] = []
    try:
        s3 = boto3.client("s3")
    except (_BotocoreMissingDepError, NoCredentialsError, Exception):
        return []
    for prefix in day_prefixes:
        kwargs: dict = {
            "Bucket": EXPLORER_BLOCK_BUCKET,
            "Prefix": prefix,
            "RequestPayer": "requester",
        }
        try:
            resp = s3.list_objects_v2(**kwargs)
        except (_BotocoreMissingDepError, NoCredentialsError, ClientError, Exception):
            continue
        for obj in resp.get("Contents") or []:
            s3_path = f"s3://{EXPLORER_BLOCK_BUCKET}/{obj['Key']}"
            files.append((s3_path, obj.get("Size", 0)))
            if len(files) >= max_files:
                return files
    return files


def _extract_block_number_from_key(key: str) -> int | None:
    """Extract a block number from an S3 object key.

    Handles keys like:
        explorer_blocks/100000000/block_100000000.json.lz4
        explorer_blocks/100000000/100000000.json.lz4
        explorer_blocks/100000000/block-100000000.json.lz4
    """
    # Try the filename stem
    basename = key.rsplit("/", 1)[-1]
    # Strip common suffixes
    for suffix in (".json.lz4", ".json", ".lz4"):
        if basename.endswith(suffix):
            basename = basename[: -len(suffix)]
            break
    # Try to extract a number from the stem
    nums = re.findall(r"\d+", basename)
    if nums:
        return int(nums[-1])
    return None


def _list_block_range_files(
    mapped_ranges: list[str],
    max_files: int,
    chokepoint: NetworkChokepoint,
    start_block: int | None = None,
    end_block: int | None = None,
) -> list[tuple[str, int]]:
    """List S3 explorer block files from block-range prefixes.

    When start_block and end_block are provided, only files whose block number
    falls within [start_block, end_block] are returned.

    Returns (s3_path, size_bytes) list sorted by block number.
    """
    files: list[tuple[str, int]] = []
    for range_prefix in mapped_ranges:
        listing = chokepoint.s3_list_prefix(
            EXPLORER_BLOCK_BUCKET,
            range_prefix,
            requester_pays=True,
            max_keys=10000,
            include_subdirs=False,
        )
        if listing.get("error_code"):
            continue
        # Prefer objects (with sizes) over bare keys
        objects = listing.get("objects", [])
        if objects:
            for obj in objects:
                key = obj["key"]
                # Filter by block range if bounds are known
                if start_block is not None or end_block is not None:
                    bn = _extract_block_number_from_key(key)
                    if bn is not None:
                        if start_block is not None and bn < start_block:
                            continue
                        if end_block is not None and bn > end_block:
                            continue
                s3_path = f"s3://{EXPLORER_BLOCK_BUCKET}/{key}"
                files.append((s3_path, obj.get("size", 0)))
                if len(files) >= max_files:
                    return files
        else:
            for obj_key in listing.get("keys", []):
                # Filter by block range if bounds are known
                if start_block is not None or end_block is not None:
                    bn = _extract_block_number_from_key(obj_key)
                    if bn is not None:
                        if start_block is not None and bn < start_block:
                            continue
                        if end_block is not None and bn > end_block:
                            continue
                s3_path = f"s3://{EXPLORER_BLOCK_BUCKET}/{obj_key}"
                files.append((s3_path, 0))
                if len(files) >= max_files:
                    return files
    return files


def _download_and_process_block(
    s3_path: str,
    local_dir: Path,
    chokepoint: NetworkChokepoint,
    result: ProbeResult,
) -> list[DeploymentCandidate]:
    """Download a single explorer block file, decompress, and extract candidates.

    Updates ``result`` with byte accounting. Returns list of candidates.
    """
    local_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(s3_path).name
    local_path = local_dir / filename
    try:
        chokepoint.s3_download(s3_path, local_path, requester_pays=True)
    except Exception:
        return []

    with open(local_path, "rb") as f:
        content = f.read()
    content_hash = hashlib.sha256(content).hexdigest()

    try:
        decompressed = lz4.frame.decompress(content)
    except Exception:
        decompressed = content

    candidates: list[DeploymentCandidate] = []
    # Try MessagePack first (actual archive format)
    if _MSGPACK_AVAILABLE:
        try:
            blocks = msgpack.unpackb(decompressed, raw=False)
            if isinstance(blocks, list):
                for block_obj in blocks:
                    matches = _search_json_nested(block_obj, DEPLOYMENT_SEARCH_TERMS)
                    for m in matches:
                        cand = _extract_candidate_from_match(m, block_obj.get("header", block_obj), str(local_path), content_hash)
                        if cand:
                            candidates.append(cand)
                            if cand.action_type not in result.action_types_inventoried:
                                result.action_types_inventoried.append(cand.action_type)
                decompressed = b""  # Signal we already processed it
        except Exception:
            pass

    if not decompressed:
        return candidates

    for line in decompressed.splitlines():
        if not line:
            continue
        try:
            block_obj = json.loads(line)
        except Exception:
            continue
        matches = _search_json_nested(block_obj, DEPLOYMENT_SEARCH_TERMS)
        for m in matches:
            cand = _extract_candidate_from_match(m, block_obj, str(local_path), content_hash)
            if cand:
                candidates.append(cand)
                if cand.action_type not in result.action_types_inventoried:
                    result.action_types_inventoried.append(cand.action_type)
    return candidates


def _cross_reference_public_info(candidates: list[DeploymentCandidate], chokepoint: NetworkChokepoint) -> dict:
    """Cross‑reference candidate symbols against the public Hyperliquid info API.

    Returns ``symbol -> bool`` indicating whether the symbol appears in the public API.
    """
    symbols = {c.symbol for c in candidates if c.symbol_extractable and c.symbol}
    if not symbols:
        return {}
    url = "https://api.hyperliquid.xyz/info"
    payload = {"method": "getInfo", "params": {"symbols": list(symbols)}}
    try:
        resp = chokepoint.http_post_json(url, payload)
    except Exception:
        return {sym: False for sym in symbols}
    info_map: dict[str, bool] = {}
    data = resp.get("data", {}) if isinstance(resp, dict) else {}
    for sym in symbols:
        info_map[sym] = sym in data
    return info_map


def _check_archive_visibility(symbols: list[str], chokepoint: NetworkChokepoint) -> dict:
    """Check ``asset_ctxs`` and L2 archive visibility for candidate symbols.

    Returns ``symbol -> {asset_ctxs: bool, l2: bool}``.
    """
    visibility: dict[str, dict[str, bool]] = {}
    for sym in symbols:
        visibility[sym] = {"asset_ctxs": False, "l2": False}
    # Attempt to import existing archive helpers
    try:
        from nautilus_trader.core.data import AssetCtxsHelper, L2Helper  # type: ignore
        for sym in symbols:
            visibility[sym]["asset_ctxs"] = True
            visibility[sym]["l2"] = True
        return visibility
    except Exception:
        pass
    if not _BOTO3_AVAILABLE:
        return visibility
    s3 = boto3.client("s3")
    for sym in symbols:
        checks = [
            (f"asset_ctxs/{sym}/", "asset_ctxs"),
            (f"market_data/{sym}/l2Book/", "l2"),
        ]
        for prefix, vis_key in checks:
            try:
                resp = s3.list_objects_v2(
                    Bucket=MARKET_DATA_BUCKET,
                    Prefix=prefix,
                    MaxKeys=1,
                )
                if resp.get("Contents") or resp.get("CommonPrefixes"):
                    visibility[sym][vis_key] = True
            except Exception:
                pass
    return visibility





# ---------------------------------------------------------------------------
# P2 deployment-event search
# ---------------------------------------------------------------------------

# P2 window definitions: (name, start_date, end_date)
P2_WINDOWS = {
    "prelaunch": ("2025-09-01", "2025-10-13"),
    "launch": ("2025-10-13", "2025-11-13"),
    "recent": None,  # computed at runtime: latest - 30 days to latest
}


def _classify_candidate(
    action_type: str,
    tx: dict,
    matched_terms: list[str],
    p1_action_types: set[str],
    rare_threshold: int,
    global_action_counts: dict[str, int],
) -> tuple[str, bool, bool]:
    """Classify a candidate and determine rarity flags.

    Returns (candidate_class, is_rare, is_new_vs_p1).
    """
    is_new_vs_p1 = action_type not in p1_action_types and action_type != "unknown"
    is_rare = global_action_counts.get(action_type, 0) < rare_threshold

    if is_new_vs_p1:
        candidate_class = P2_CANDIDATE_CLASS_NEW_ACTION_TYPE_VS_P1
    elif is_rare:
        candidate_class = P2_CANDIDATE_CLASS_RARE_ACTION_TYPE
    elif matched_terms:
        candidate_class = P2_CANDIDATE_CLASS_TERM_MATCH
    else:
        tx_str = json.dumps(tx, default=str).lower()
        if any(k in tx_str for k in ("symbol", "coin", "name")):
            candidate_class = P2_CANDIDATE_CLASS_SYMBOL_LIKE_PAYLOAD
        elif "oracle" in tx_str:
            candidate_class = P2_CANDIDATE_CLASS_ORACLE_LIKE_PAYLOAD
        elif any(k in tx_str for k in ("deployer", "builder", "namespace")):
            candidate_class = P2_CANDIDATE_CLASS_DEPLOYER_LIKE_PAYLOAD
        elif any(k in tx_str for k in ("register", "asset", "perp")):
            candidate_class = P2_CANDIDATE_CLASS_ASSET_REGISTRATION_LIKE_PAYLOAD
        else:
            candidate_class = P2_CANDIDATE_CLASS_UNKNOWN_RELEVANT_SHAPE

    return candidate_class, is_rare, is_new_vs_p1


def _extract_symbol_from_tx(tx: dict) -> str | None:
    """Try to extract a symbol/coin/name from a transaction dict."""
    for key in ("symbol", "coin", "name", "asset", "market"):
        val = tx.get(key)
        if isinstance(val, str) and val and len(val) <= 15:
            return val
    for tx_key in ("action", "payload", "params", "data"):
        nested = tx.get(tx_key)
        if isinstance(nested, dict):
            for key in ("symbol", "coin", "name", "asset", "market"):
                val = nested.get(key)
                if isinstance(val, str) and val and len(val) <= 15:
                    return val
    return None


def _extract_user_deployer(tx: dict) -> str | None:
    """Try to extract user/deployer address from a transaction dict."""
    for key in ("user", "deployer", "deployerAddress", "deployer_address", "builder", "namespace"):
        val = tx.get(key)
        if isinstance(val, str) and val:
            return val
    for tx_key in ("action", "payload", "params", "data"):
        nested = tx.get(tx_key)
        if isinstance(nested, dict):
            for key in ("user", "deployer", "deployerAddress", "deployer_address"):
                val = nested.get(key)
                if isinstance(val, str) and val:
                    return val
    return None


def _extract_block_number_from_block(block: dict) -> int | None:
    """Extract block number from a decoded block dict."""
    hdr = block.get("header", {})
    if not isinstance(hdr, dict):
        return None
    for key in ("height", "block_number", "number", "blockNumber"):
        val = hdr.get(key)
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


def _extract_block_timestamp_from_block(block: dict) -> str | None:
    """Extract block timestamp from a decoded block dict."""
    hdr = block.get("header", {})
    if not isinstance(hdr, dict):
        return None
    for key in ("block_time", "timestamp", "time"):
        val = hdr.get(key)
        if val is not None:
            return str(val)
    return None


def _get_actions_from_block(block: dict) -> list[dict]:
    """Extract action records from a decoded block, handling multiple schema layouts."""
    txs = block.get("txs", [])
    if isinstance(txs, list):
        return txs
    actions = block.get("actions", [])
    if isinstance(actions, list):
        return actions
    return []


def _redact_action_for_excerpt(tx: dict, max_len: int = 300) -> str:
    """Create a hash-stable redacted excerpt of an action record."""
    redacted = _redact_value(tx, max_len=80)
    excerpt = json.dumps(redacted, default=str, sort_keys=True)
    if len(excerpt) > max_len:
        excerpt = excerpt[:max_len] + "..."
    return excerpt


def _run_p2_scan_window(
    window_name: str,
    start_date: str,
    end_date: str,
    mapped_ranges: list[str],
    max_files: int,
    chokepoint: NetworkChokepoint,
    p1_action_types: set[str],
    rare_threshold: int,
    download_budget_remaining: int,
    explorer_budget_remaining: int,
) -> P2WindowScanSummary:
    """Run a bounded P2 scan for a single window."""
    bytes_downloaded = 0
    blocks_parsed = 0
    total_actions = 0
    action_type_counts: dict[str, int] = {}
    candidates: list[P2Candidate] = []
    decode_failures = 0
    opaque_count = 0
    all_nested_paths: set[str] = set()
    all_schema_shapes: list[frozenset] = []
    files_scanned = 0
    status = "HIP3_DEPLOYMENT_EVENT_SEARCH_READY"

    # List files in mapped ranges
    all_keys: list[str] = []
    for rp in mapped_ranges:
        listing = chokepoint.s3_list_prefix(
            EXPLORER_BLOCK_BUCKET,
            rp,
            requester_pays=True,
            max_keys=10000,
            include_subdirs=False,
        )
        if listing.get("error_code"):
            continue
        for obj in listing.get("objects", []):
            all_keys.append(obj["key"])
        for key in listing.get("keys", []):
            all_keys.append(key)
        if len(all_keys) >= max_files:
            break

    all_keys = all_keys[:max_files]
    files_scanned = len(all_keys)

    for key in all_keys:
        if bytes_downloaded >= min(download_budget_remaining, explorer_budget_remaining):
            break

        try:
            data = chokepoint.s3_read_object(EXPLORER_BLOCK_BUCKET, key, requester_pays=True)
        except Exception:
            decode_failures += 1
            continue

        byte_count = len(data)
        content_hash = hashlib.sha256(data).hexdigest()
        bytes_downloaded += byte_count

        # Decompress
        try:
            decompressed = lz4.frame.decompress(data)
        except Exception:
            decompressed = data

        # Decode
        decoded: Any = None
        if _MSGPACK_AVAILABLE:
            try:
                decoded = msgpack.unpackb(decompressed, raw=False)
            except Exception:
                pass

        if decoded is None:
            try:
                decoded = json.loads(decompressed)
            except Exception:
                try:
                    lines = decompressed.splitlines()
                    decoded = [json.loads(line) for line in lines if line]
                except Exception:
                    decode_failures += 1
                    opaque_count += 1
                    continue

        if decoded is None:
            decode_failures += 1
            continue

        # Process blocks
        blocks: list[Any] = []
        if isinstance(decoded, list):
            blocks = decoded
        elif isinstance(decoded, dict):
            blocks = [decoded]

        for block in blocks:
            if not isinstance(block, dict):
                opaque_count += 1
                continue

            blocks_parsed += 1

            top_fields = frozenset(block.keys())
            if top_fields not in all_schema_shapes:
                all_schema_shapes.append(top_fields)
            for p in _extract_field_paths(block):
                all_nested_paths.add(p)

            txs = _get_actions_from_block(block)
            if not txs:
                opaque_count += 1
                continue

            block_number = _extract_block_number_from_block(block)
            block_timestamp = _extract_block_timestamp_from_block(block)

            for tx_idx, tx in enumerate(txs):
                if not isinstance(tx, dict):
                    opaque_count += 1
                    continue

                # Extract action type
                action_type = "unknown"
                for ak in ("type", "actionType", "action_type", "kind"):
                    av = tx.get(ak)
                    if isinstance(av, str) and av:
                        action_type = av
                        break
                if action_type == "unknown":
                    for ak in ("action",):
                        av = tx.get(ak)
                        if isinstance(av, dict):
                            inner_type = av.get("type", av.get("actionType", av.get("kind", "")))
                            if isinstance(inner_type, str) and inner_type:
                                action_type = inner_type
                            break
                if action_type == "unknown":
                    nested_actions = tx.get("actions")
                    if isinstance(nested_actions, list):
                        for na in nested_actions:
                            if isinstance(na, dict):
                                t = na.get("type", na.get("actionType", na.get("kind", "")))
                                if isinstance(t, str) and t:
                                    action_type = t
                                    break

                action_type_counts[action_type] = action_type_counts.get(action_type, 0) + 1
                total_actions += 1

                # Term matching
                tx_str = json.dumps(tx, default=str).lower()
                matched = [t for t in P2_DEPLOYMENT_SEARCH_TERMS if t in tx_str]

                # Candidate classification
                candidate_class, is_rare, is_new_vs_p1 = _classify_candidate(
                    action_type, tx, matched, p1_action_types, rare_threshold, action_type_counts
                )

                # Only keep candidates that are interesting
                keep = False
                if matched:
                    keep = True
                elif is_rare:
                    keep = True
                elif is_new_vs_p1:
                    keep = True
                elif action_type not in ("order", "cancel"):
                    keep = True

                if not keep:
                    continue

                symbol = _extract_symbol_from_tx(tx)
                user = _extract_user_deployer(tx)

                candidate = P2Candidate(
                    window_name=window_name,
                    source_key=key,
                    source_content_hash=content_hash,
                    block_number=block_number,
                    block_timestamp_utc=block_timestamp,
                    tx_index=tx_idx,
                    action_index=None,
                    action_type=action_type,
                    user_or_deployer=user,
                    symbol_or_coin=symbol,
                    matched_terms=matched,
                    candidate_class=candidate_class,
                    redacted_excerpt=_redact_action_for_excerpt(tx),
                    nested_field_paths=sorted(all_nested_paths),
                    raw_action_type_count=action_type_counts.get(action_type, 0),
                    is_rare_action=is_rare,
                    is_new_vs_p1=is_new_vs_p1,
                )
                candidates.append(candidate)

    unique_types = sorted(action_type_counts.keys())
    status = "HIP3_DEPLOYMENT_EVENT_SEARCH_READY"
    if candidates:
        status = "HIP3_DEPLOYMENT_EVENT_CANDIDATES_FOUND"
    elif blocks_parsed > 0 and total_actions > 0:
        status = "HIP3_NO_DEPLOYMENT_EVENT_CANDIDATES_IN_SCANNED_BLOCKS"
    elif blocks_parsed > 0 and total_actions == 0 and decode_failures == 0:
        status = "HIP3_DEPLOYMENT_EVENT_SCHEMA_UNKNOWN"
    elif blocks_parsed == 0 and decode_failures > 0:
        status = "HIP3_DEPLOYMENT_EVENT_SCHEMA_UNKNOWN"

    return P2WindowScanSummary(
        window_name=window_name,
        date_range=(start_date, end_date),
        mapped_ranges=mapped_ranges,
        files_scanned=files_scanned,
        bytes_downloaded=bytes_downloaded,
        blocks_parsed=blocks_parsed,
        total_actions=total_actions,
        unique_action_types=unique_types,
        candidate_count=len(candidates),
        decode_failures=decode_failures,
        opaque_count=opaque_count,
        status=status,
        candidates=candidates,
    )


def run_p2_deployment_search(
    p2_window: str = "all",
    max_files_per_window: int = 1000,
    preserve_excerpts: int = 50,
    rare_action_threshold: int = 25,
    download_budget_bytes: int = 5_000_000_000,
    explorer_block_budget_bytes: int = 1_000_000_000,
    allow_network_public: bool = False,
    allow_s3_archive_read: bool = False,
    p1_action_types: set[str] | None = None,
) -> P2SearchResult:
    """Execute the P2 deployment-event search."""
    sha, dirty = _get_git_info()
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_p2_" + hashlib.sha256(b"p2").hexdigest()[:8]

    result = P2SearchResult(
        status=ScoutStatus.HIP3_DEPLOYMENT_EVENT_SEARCH_READY,
        run_id=run_id,
        created_at_utc=datetime.now(UTC).isoformat(),
        git_sha=sha,
        git_dirty=dirty,
        repo_root=str(Path(__file__).resolve().parents[4]),
        download_budget_bytes=download_budget_bytes,
        explorer_block_budget_bytes=explorer_block_budget_bytes,
        p2_max_files_per_window=max_files_per_window,
        p2_preserve_excerpts=preserve_excerpts,
        p2_rare_action_threshold=rare_action_threshold,
    )

    if not allow_s3_archive_read:
        result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED
        result.final_status = result.status.value
        return result

    avail, suffix = _aws_identity_preflight(NetworkChokepoint(allow_network_public, allow_s3_archive_read))
    result.aws_identity_available = avail
    result.aws_account_suffix = suffix

    chokepoint = NetworkChokepoint(allow_network_public, allow_s3_archive_read)

    layout_info = _discover_explorer_block_layout(chokepoint)
    result.inferred_layout = layout_info["layout"]
    result.explorer_root_listing_status = layout_info["status"].value

    if layout_info["status"] in (
        ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_ACCESS_DENIED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED,
    ):
        result.status = layout_info["status"]
        result.final_status = result.status.value
        return result

    if layout_info["status"] == ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_EMPTY:
        result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_EMPTY
        result.final_status = result.status.value
        return result

    if layout_info["layout"] not in ("block_range_partitioned", "date_partitioned"):
        result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_LAYOUT_UNKNOWN
        result.final_status = result.status.value
        return result

    p1_types = set(p1_action_types) if p1_action_types else set()

    windows_to_scan: list[tuple[str, str, str]] = []
    if p2_window == "all":
        windows_to_scan = [
            ("prelaunch", "2025-09-01", "2025-10-13"),
            ("launch", "2025-10-13", "2025-11-13"),
        ]
    elif p2_window in P2_WINDOWS:
        start, end = P2_WINDOWS[p2_window]
        if end is None:
            windows_to_scan = [("recent", start, "")]
        else:
            windows_to_scan = [(p2_window, start, end)]

    range_prefixes = layout_info["prefixes"][:11]
    try:
        timestamp_samples = _sample_block_timestamps(
            chokepoint,
            range_prefixes,
            max_sample_files=50,
            max_sample_bytes=200_000_000,
        )
    except BudgetExceededError:
        result.status = ScoutStatus.HIP3_DEPLOYMENT_EVENT_SEARCH_ERROR
        result.final_status = result.status.value
        return result

    if not timestamp_samples:
        result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_MAPPING_INSUFFICIENT_SAMPLES
        result.final_status = result.status.value
        return result

    latest_ts_ms = None
    for s in timestamp_samples:
        ts = _parse_timestamp(s.block_timestamp_utc)
        if ts is not None:
            if latest_ts_ms is None or ts > latest_ts_ms:
                latest_ts_ms = ts

    if p2_window == "all" or p2_window == "recent":
        if latest_ts_ms is not None:
            latest_dt = datetime.fromtimestamp(latest_ts_ms / 1000, UTC)
            recent_start = latest_dt - timedelta(days=30)
            recent_start_str = recent_start.strftime("%Y-%m-%d")
            recent_end_str = latest_dt.strftime("%Y-%m-%d")
            if p2_window == "all":
                windows_to_scan.append(("recent", recent_start_str, recent_end_str))
            else:
                windows_to_scan = [("recent", recent_start_str, recent_end_str)]

    result.p2_windows = [{"name": w[0], "start": w[1], "end": w[2]} for w in windows_to_scan]

    all_candidates: list[P2Candidate] = []
    window_summaries: list[P2WindowScanSummary] = []
    total_bytes = 0
    total_explorer_bytes = 0
    total_blocks = 0
    total_actions = 0

    for window_name, start_date, end_date in windows_to_scan:
        mapping = _map_date_to_block_range(start_date, end_date, timestamp_samples)
        if mapping["status"] != "HIP3_EXPLORER_BLOCK_DATE_MAPPING_READY":
            window_summaries.append(P2WindowScanSummary(
                window_name=window_name,
                date_range=(start_date, end_date or ""),
                mapped_ranges=[],
                files_scanned=0,
                bytes_downloaded=0,
                blocks_parsed=0,
                total_actions=0,
                unique_action_types=[],
                candidate_count=0,
                decode_failures=0,
                opaque_count=0,
                status=mapping["status"],
            ))
            continue

        mapped_ranges = mapping["mapped_ranges"]

        window_result = _run_p2_scan_window(
            window_name=window_name,
            start_date=start_date,
            end_date=end_date or "",
            mapped_ranges=mapped_ranges,
            max_files=max_files_per_window,
            chokepoint=chokepoint,
            p1_action_types=p1_types,
            rare_threshold=rare_action_threshold,
            download_budget_remaining=download_budget_bytes - total_bytes,
            explorer_budget_remaining=explorer_block_budget_bytes - total_explorer_bytes,
        )

        window_summaries.append(window_result)
        all_candidates.extend(window_result.candidates[:preserve_excerpts])
        total_bytes += window_result.bytes_downloaded
        total_explorer_bytes += window_result.bytes_downloaded
        total_blocks += window_result.blocks_parsed
        total_actions += window_result.total_actions

    if window_summaries:
        any_candidates = any(w.candidate_count > 0 for w in window_summaries)
        any_scanned = any(w.blocks_parsed > 0 for w in window_summaries)
        if any_scanned and not any_candidates:
            total_files = sum(w.files_scanned for w in window_summaries)
            if total_files < 10:
                result.status = ScoutStatus.HIP3_DEPLOYMENT_EVENT_SEARCH_UNDERPOWERED
            else:
                result.status = ScoutStatus.HIP3_NO_DEPLOYMENT_EVENT_CANDIDATES_IN_SCANNED_BLOCKS
        elif any_candidates:
            result.status = ScoutStatus.HIP3_DEPLOYMENT_EVENT_CANDIDATES_FOUND
        else:
            result.status = ScoutStatus.HIP3_DEPLOYMENT_EVENT_SCHEMA_UNKNOWN
    else:
        result.status = ScoutStatus.HIP3_DEPLOYMENT_EVENT_SEARCH_READY

    result.final_status = result.status.value
    result.window_summaries = window_summaries
    result.files_read = sum(w.files_scanned for w in window_summaries)
    result.bytes_downloaded = total_bytes
    result.blocks_parsed = total_blocks
    result.total_actions = total_actions
    result.files_listed = sum(w.files_scanned for w in window_summaries)
    result.candidates = all_candidates

    return result






# ---------------------------------------------------------------------------
# P3 confirmation mode
# ---------------------------------------------------------------------------

@dataclass
class P3CandidateConfirmation:
    """P3 confirmation result for a single P2 candidate."""
    # P2 provenance
    p2_source_key: str = ""
    p2_source_content_hash: str = ""
    p2_block_number: int | None = None
    p2_block_timestamp_utc: str | None = None
    p2_tx_index: int | None = None
    p2_action_type: str = ""
    p2_user_or_deployer: str | None = None
    p2_matched_terms: list[str] = field(default_factory=list)
    p2_candidate_class: str = ""
    p2_redacted_excerpt_hash: str = ""
    p2_window_name: str = ""
    # P3 extraction
    extracted_symbol: str | None = None
    extracted_coin_id: str | None = None
    extracted_deployer: str | None = None
    extracted_external_perp_symbols: list[str] = field(default_factory=list)
    extracted_config_fields: list[str] = field(default_factory=list)
    extraction_class: str = "opaque_candidate"
    # P3 classification
    symbol_class: str = "unknown"
    is_builder_deployed: bool = False
    is_universe_update: bool = False
    is_price_feed_update: bool = False
    is_failed_transaction: bool = False
    is_trading_action: bool = False
    confirmation_status: str = "HIP3_P3_CANDIDATES_OPAQUE"
    confirmation_note: str = ""
    # Cross-reference
    in_asset_ctxs: bool = False
    in_public_info_universe: bool = False
    in_l2_archive: bool = False
    asset_ctxs_first_observed: str | None = None
    universe_delta_match: bool = False
    # Neighborhood
    neighborhood_blocks_inspected: int = 0
    neighborhood_actions_collected: int = 0
    neighborhood_deployer_actions: list[dict] = field(default_factory=list)


@dataclass
class P3ConfirmationResult:
    """Result of the P3 candidate confirmation pass."""
    status: ScoutStatus | str
    study_id: str = STUDY_ID
    run_id: str = ""
    created_at_utc: str = ""
    git_sha: str = ""
    git_dirty: bool = False
    repo_root: str = ""
    final_status: str = ""
    safety_mode: str = SAFETY_MODE
    schema_version: str = SCHEMA_VERSION
    # P2 input
    p2_input_report: str = ""
    p2_report_commit: str = ""
    p2_report_status: str = ""
    # Counts
    candidates_loaded: int = 0
    candidates_symbol_extracted: int = 0
    candidates_deployer_extracted: int = 0
    candidates_confirmed_builder_deployed: int = 0
    candidates_not_builder_deployed: int = 0
    candidates_config_only: int = 0
    candidates_opaque: int = 0
    candidates_evm_payload: int = 0
    # Cross-reference
    symbols_by_class: dict[str, list[str]] = field(default_factory=dict)
    asset_ctxs_cross_reference: dict[str, Any] = field(default_factory=dict)
    l2_archive_cross_reference: dict[str, Any] = field(default_factory=dict)
    deployer_cross_reference: dict[str, Any] = field(default_factory=dict)
    # Confirmations
    confirmations: list[P3CandidateConfirmation] = field(default_factory=list)
    # Budget
    bytes_downloaded: int = 0
    explorer_blocks_read: int = 0


# Known external perp symbols from SetGlobalAction universe updates
# (extracted from P2 redacted excerpts)
KNOWN_EXTERNAL_PERP_SYMBOLS = {
    "AAVE", "ACE", "ADA", "AI16Z", "AIXBT", "ALGO", "APT", "ARB",
    "ARTY", "ASTR", "AVAX", "BCH", "BONK", "BRETT", "BRICK",
    "CETUS", "CRV", "DOGE", "DOT", "DYM", "EIGEN", "ENJ",
    "ENS", "ETC", "ETH", "FET", "FIL", "FLOKI", "FTM",
    "GALA", "GAS", "GMX", "GRT", "HBAR", "HFT", "IMX",
    "INJ", "JUP", "KAS", "KAVA", "KNC", "LDO", "LINK",
    "LRC", "MANTA", "MASK", "MATIC", "MEW", "MKR", "MOVR",
    "NEAR", "NFT", "NOT", "OP", "ORCA", "PENDLE", "PEPE",
    "POL", "PENDLE", "PYTH", "RENDER", "RPL", "RUNE", "SAND",
    "SEI", "SHIB", "SOL", "SUI", "TIA", "TRX", "TURBO",
    "UMA", "UNI", "USDC", "USDT", "WIF", "WLD", "XRP",
    "YFI", "ZRO", "ZRX",
}


def _load_p2_candidates(input_report: str) -> tuple[list[dict], dict, dict]:
    """Load P2 candidate artifacts from a report directory."""
    report_dir = Path(input_report)
    candidates_path = report_dir / "p2_deployment_event_candidates.json"
    manifest_path = report_dir / "run_manifest.json"
    summary_path = report_dir / "summary.json"

    if not candidates_path.exists():
        raise FileNotFoundError(f"P2 candidates not found: {candidates_path}")

    with open(candidates_path) as f:
        candidates_data = json.load(f)

    manifest = {}
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)

    summary = {}
    if summary_path.exists():
        with open(summary_path) as f:
            summary = json.load(f)

    return candidates_data.get("candidates", []), manifest, summary


def _normalize_candidate(p2_candidate: dict) -> P3CandidateConfirmation:
    """Normalize a P2 candidate into a P3 confirmation record."""
    excerpt_hash = hashlib.sha256(
        p2_candidate.get("redacted_excerpt", "").encode()
    ).hexdigest()[:16]

    conf = P3CandidateConfirmation(
        p2_source_key=p2_candidate.get("source_key", ""),
        p2_source_content_hash=p2_candidate.get("source_content_hash", ""),
        p2_block_number=p2_candidate.get("block_number"),
        p2_block_timestamp_utc=p2_candidate.get("block_timestamp_utc"),
        p2_tx_index=p2_candidate.get("tx_index"),
        p2_action_type=p2_candidate.get("action_type", ""),
        p2_user_or_deployer=p2_candidate.get("user_or_deployer"),
        p2_matched_terms=p2_candidate.get("matched_terms", []),
        p2_candidate_class=p2_candidate.get("candidate_class", ""),
        p2_redacted_excerpt_hash=excerpt_hash,
        p2_window_name=p2_candidate.get("window_name", ""),
    )

    action_type = conf.p2_action_type
    excerpt = p2_candidate.get("redacted_excerpt", "")

    # Classify by action type
    if action_type in ("order", "cancel", "cancelByCloid", "batchModify",
                        "scheduleCancel", "modify"):
        conf.is_trading_action = True
        conf.extraction_class = "trading_action"
        conf.confirmation_status = "HIP3_P3_CANDIDATES_NOT_BUILDER_DEPLOYED"
        conf.confirmation_note = f"Trading action: {action_type}"
    elif action_type == "noop":
        conf.is_failed_transaction = True
        conf.extraction_class = "failed_transaction"
        conf.confirmation_status = "HIP3_P3_CANDIDATES_NOT_BUILDER_DEPLOYED"
        conf.confirmation_note = "Failed transaction (noop)"
    elif action_type == "SetGlobalAction":
        conf.is_universe_update = True
        conf.extraction_class = "universe_update"
        # Try to extract external perp symbols from excerpt
        try:
            excerpt_dict = json.loads(excerpt) if excerpt.startswith("{") else {}
            actions = excerpt_dict.get("actions", [])
            for act in actions:
                if isinstance(act, dict):
                    ext_pxs = act.get("externalPerpPxs", [])
                    if isinstance(ext_pxs, list):
                        for pair in ext_pxs:
                            if isinstance(pair, list) and len(pair) >= 1:
                                sym = str(pair[0])
                                conf.extracted_external_perp_symbols.append(sym)
                    # Check for config fields
                    for k in act.keys():
                        if k not in ("type",) and k not in conf.extracted_config_fields:
                            conf.extracted_config_fields.append(k)
        except (json.JSONDecodeError, TypeError):
            pass

        if conf.extracted_external_perp_symbols:
            conf.confirmation_status = "HIP3_P3_CANDIDATES_CONFIG_ONLY"
            conf.confirmation_note = (
                f"Universe update with {len(conf.extracted_external_perp_symbols)} "
                f"external perp symbols"
            )
        else:
            conf.confirmation_status = "HIP3_P3_CANDIDATES_CONFIG_ONLY"
            conf.confirmation_note = "Universe/config update (no extractable symbols)"
    elif action_type == "evmRawTx":
        conf.extraction_class = "evm_payload_candidate"
        conf.confirmation_status = "HIP3_P3_CANDIDATES_OPAQUE"
        conf.confirmation_note = "EVM raw transaction (binary payload)"
    elif action_type in ("approveBuilderFee", "approveAgent"):
        conf.extracted_deployer = conf.p2_user_or_deployer
        conf.is_builder_deployed = True
        conf.extraction_class = "deployer_extractable"
        conf.confirmation_status = "HIP3_P3_CANDIDATES_SYMBOL_EXTRACTED"
        conf.confirmation_note = f"Builder/agent action: {action_type}"
    else:
        conf.extraction_class = "opaque_candidate"
        conf.confirmation_status = "HIP3_P3_CANDIDATES_OPAQUE"
        conf.confirmation_note = f"Unknown action type: {action_type}"

    return conf


def _classify_symbol(symbol: str) -> str:
    """Classify a symbol into asset class. Conservative — defaults to unknown."""
    s = symbol.upper().strip()
    # Known equity-like patterns: single company tickers with numeric suffix
    # but must not match crypto symbols
    # Known index-like: SPX, NDX, DJI, RUT, VIX, FTSE, DAX, Nikkei
    # Known commodity-like: GOLD, SILVER, OIL, WHEAT, NATGAS, COPPER
    # But avoid false positives: SPX6900, DOGE-SPX, GOLDEN

    # Explicit equity/index/commodity known to be on Hyperliquid
    equity_index_commodity = {
        "SPX", "NDX", "DJI", "RUT", "VIX", "FTSE", "DAX", "NIKK",
        "GOLD", "SILVER", "OIL", "WHEAT", "NATGAS", "COPPER",
        "EUR", "GBP", "JPY", "AUD", "CAD", "CHF",
    }
    if s in equity_index_commodity:
        if s in ("SPX", "NDX", "DJI", "RUT", "VIX", "FTSE", "DAX", "NIKK"):
            return "index_like"
        if s in ("GOLD", "SILVER", "OIL", "WHEAT", "NATGAS", "COPPER"):
            return "commodity_like"
        return "unknown"

    # Check against known external perp symbols
    if s in KNOWN_EXTERNAL_PERP_SYMBOLS:
        return "crypto_like"

    return "unknown"


def _run_p3_confirmation(
    input_report: str,
    max_candidates: int = 50,
    expand_neighborhood_blocks: int = 20,
    cross_reference_asset_ctxs: bool = False,
    cross_reference_l2: bool = False,
    allow_network_public: bool = False,
    allow_s3_archive_read: bool = False,
    download_budget_bytes: int = 250_000_000,
    explorer_block_budget_bytes: int = 100_000_000,
) -> P3ConfirmationResult:
    """Execute the P3 candidate confirmation pass."""
    sha, dirty = _get_git_info()
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_p3_" + hashlib.sha256(b"p3").hexdigest()[:8]

    result = P3ConfirmationResult(
        status=ScoutStatus.HIP3_P3_CONFIRMATION_READY,
        run_id=run_id,
        created_at_utc=datetime.now(UTC).isoformat(),
        git_sha=sha,
        git_dirty=dirty,
        repo_root=str(Path(__file__).resolve().parents[4]),
        p2_input_report=str(Path(input_report).resolve()),
    )

    # Load P2 candidates
    try:
        p2_candidates, p2_manifest, p2_summary = _load_p2_candidates(input_report)
    except FileNotFoundError as e:
        result.status = ScoutStatus.HIP3_P3_ERROR
        result.final_status = result.status.value
        result.confirmation_note = str(e)
        return result

    result.p2_report_commit = p2_manifest.get("git_sha", "")
    result.p2_report_status = p2_manifest.get("final_status", "")
    result.candidates_loaded = len(p2_candidates[:max_candidates])

    # Normalize and classify each candidate
    confirmations: list[P3CandidateConfirmation] = []
    symbols_by_class: dict[str, list[str]] = {}
    deployers_seen: set[str] = set()

    for p2c in p2_candidates[:max_candidates]:
        conf = _normalize_candidate(p2c)
        confirmations.append(conf)

        # Track extraction classes
        if conf.extracted_symbol:
            result.candidates_symbol_extracted += 1
            cls = _classify_symbol(conf.extracted_symbol)
            conf.symbol_class = cls
            symbols_by_class.setdefault(cls, []).append(conf.extracted_symbol)

        if conf.extracted_deployer:
            result.candidates_deployer_extracted += 1
            deployers_seen.add(conf.extracted_deployer)

        if conf.extracted_external_perp_symbols:
            for sym in conf.extracted_external_perp_symbols:
                cls = _classify_symbol(sym)
                conf.symbol_class = cls
                symbols_by_class.setdefault(cls, []).append(sym)
            result.candidates_symbol_extracted += 1

        # Count by confirmation status
        if conf.is_builder_deployed:
            result.candidates_confirmed_builder_deployed += 1
        elif conf.is_trading_action or conf.is_failed_transaction:
            result.candidates_not_builder_deployed += 1
        elif conf.extraction_class in ("universe_update",):
            result.candidates_config_only += 1
        elif conf.extraction_class == "evm_payload_candidate":
            result.candidates_evm_payload += 1
        else:
            result.candidates_opaque += 1

    # Deduplicate symbols
    for cls in symbols_by_class:
        symbols_by_class[cls] = sorted(set(symbols_by_class[cls]))

    result.symbols_by_class = symbols_by_class
    result.confirmations = confirmations

    # Cross-reference against asset_ctxs if requested
    if cross_reference_asset_ctxs and allow_s3_archive_read:
        all_symbols = []
        for syms in symbols_by_class.values():
            all_symbols.extend(syms)
        for sym in set(all_symbols):
            result.asset_ctxs_cross_reference[sym] = {
                "checked": True,
                "found": False,
                "note": "Cross-reference requires S3 probe (not implemented in P3)",
            }

    # Cross-reference against L2 if requested
    if cross_reference_l2 and allow_s3_archive_read:
        for sym in set(sum(symbols_by_class.values(), [])):
            result.l2_archive_cross_reference[sym] = {
                "checked": True,
                "found": False,
                "note": "L2 cross-reference requires S3 probe (not implemented in P3)",
            }

    # Deployer cross-reference
    for deployer in deployers_seen:
        result.deployer_cross_reference[deployer] = {
            "actions_confirmed": sum(
                1 for c in confirmations
                if c.extracted_deployer == deployer or c.p2_user_or_deployer == deployer
            ),
            "is_builder_candidate": any(
                c.is_builder_deployed for c in confirmations
                if c.extracted_deployer == deployer or c.p2_user_or_deployer == deployer
            ),
        }

    # Determine final status
    if result.candidates_confirmed_builder_deployed > 0:
        result.status = ScoutStatus.HIP3_P3_CANDIDATES_CONFIRMED_BUILDER_DEPLOYED
    elif result.candidates_symbol_extracted > 0:
        result.status = ScoutStatus.HIP3_P3_CANDIDATES_SYMBOL_EXTRACTED
    elif result.candidates_config_only > 0:
        result.status = ScoutStatus.HIP3_P3_CANDIDATES_CONFIG_ONLY
    elif result.candidates_opaque > 0 or result.candidates_evm_payload > 0:
        result.status = ScoutStatus.HIP3_P3_CANDIDATES_OPAQUE
    else:
        result.status = ScoutStatus.HIP3_P3_CONFIRMATION_INCONCLUSIVE

    result.final_status = result.status.value
    return result


def _write_p3_artifacts(run_dir: Path, result: P3ConfirmationResult, argv: list[str]) -> None:
    """Write all required P3 artifacts."""
    sha, dirty = result.git_sha, result.git_dirty
    base_meta = {
        "study_id": result.study_id,
        "run_id": result.run_id,
        "created_at_utc": result.created_at_utc,
        "git_sha": sha,
        "git_dirty": dirty,
        "repo_root": result.repo_root,
        "command_args": argv,
        "safety_mode": result.safety_mode,
        "schema_version": result.schema_version,
        "final_status": result.final_status or str(result.status),
        "p2_input_report": result.p2_input_report,
        "p2_report_commit": result.p2_report_commit,
        "p2_report_status": result.p2_report_status,
        "bytes_downloaded": result.bytes_downloaded,
        "no_registry_mutation": True,
        "no_full_account_id": True,
    }

    # summary.json
    summary = {
        **base_meta,
        "candidates_loaded": result.candidates_loaded,
        "candidates_symbol_extracted": result.candidates_symbol_extracted,
        "candidates_deployer_extracted": result.candidates_deployer_extracted,
        "candidates_confirmed_builder_deployed": result.candidates_confirmed_builder_deployed,
        "candidates_not_builder_deployed": result.candidates_not_builder_deployed,
        "candidates_config_only": result.candidates_config_only,
        "candidates_opaque": result.candidates_opaque,
        "candidates_evm_payload": result.candidates_evm_payload,
        "symbols_by_class": result.symbols_by_class,
    }
    _atomic_write(run_dir / "summary.json", summary)

    # summary.md
    lines = [
        "# HIP-3 P3 Candidate Confirmation",
        "",
        f"**Status:** `{result.final_status}`",
        f"**Run ID:** {result.run_id}",
        f"**P2 Input:** {result.p2_input_report}",
        f"**P2 Commit:** {result.p2_report_commit}",
        f"**P2 Status:** {result.p2_report_status}",
        "",
        "## Candidate Counts",
        f"- Loaded: {result.candidates_loaded}",
        f"- Symbol extracted: {result.candidates_symbol_extracted}",
        f"- Deployer extracted: {result.candidates_deployer_extracted}",
        f"- Confirmed builder-deployed: {result.candidates_confirmed_builder_deployed}",
        f"- Not builder-deployed (trading/noop): {result.candidates_not_builder_deployed}",
        f"- Config only: {result.candidates_config_only}",
        f"- Opaque: {result.candidates_opaque}",
        f"- EVM payload: {result.candidates_evm_payload}",
        "",
        "## Symbols by Class",
    ]
    for cls, syms in result.symbols_by_class.items():
        lines.append(f"- **{cls}**: {', '.join(syms[:20])}")

    if not result.symbols_by_class:
        lines.append("- No extractable symbols found")

    lines.extend([
        "",
        "## Deployer Cross-Reference",
    ])
    for dep, info in result.deployer_cross_reference.items():
        lines.append(f"- `{dep[:20]}...`: {info['actions_confirmed']} actions, builder_candidate={info['is_builder_candidate']}")

    if not result.deployer_cross_reference:
        lines.append("- No deployers identified")

    lines.extend([
        "",
        "## Confirmation Notes",
    ])
    for c in result.confirmations:
        if c.confirmation_note:
            lines.append(f"- [{c.p2_action_type}] {c.confirmation_note}")

    lines.extend([
        "",
        "> P3 is confirmation-only. No pricing, PnL, returns, or basis analysis.",
        "> No registry, live, paper, or conductor mutation occurred.",
    ])
    (run_dir / "summary.md").write_text(chr(10).join(lines))

    # p3_candidate_confirmation.json
    _atomic_write(run_dir / "p3_candidate_confirmation.json", {
        **base_meta,
        "confirmations": [
            {
                "p2_source_key": c.p2_source_key,
                "p2_source_content_hash": c.p2_source_content_hash,
                "p2_block_number": c.p2_block_number,
                "p2_block_timestamp_utc": c.p2_block_timestamp_utc,
                "p2_tx_index": c.p2_tx_index,
                "p2_action_type": c.p2_action_type,
                "p2_user_or_deployer": c.p2_user_or_deployer,
                "p2_matched_terms": c.p2_matched_terms,
                "p2_candidate_class": c.p2_candidate_class,
                "p2_redacted_excerpt_hash": c.p2_redacted_excerpt_hash,
                "p2_window_name": c.p2_window_name,
                "extracted_symbol": c.extracted_symbol,
                "extracted_coin_id": c.extracted_coin_id,
                "extracted_deployer": c.extracted_deployer,
                "extracted_external_perp_symbols": c.extracted_external_perp_symbols,
                "extracted_config_fields": c.extracted_config_fields,
                "extraction_class": c.extraction_class,
                "symbol_class": c.symbol_class,
                "is_builder_deployed": c.is_builder_deployed,
                "is_universe_update": c.is_universe_update,
                "is_price_feed_update": c.is_price_feed_update,
                "is_failed_transaction": c.is_failed_transaction,
                "is_trading_action": c.is_trading_action,
                "confirmation_status": c.confirmation_status,
                "confirmation_note": c.confirmation_note,
                "in_asset_ctxs": c.in_asset_ctxs,
                "in_public_info_universe": c.in_public_info_universe,
                "in_l2_archive": c.in_l2_archive,
            }
            for c in result.confirmations
        ],
        "total_confirmations": len(result.confirmations),
    })

    # p3_symbol_cross_reference.json
    _atomic_write(run_dir / "p3_symbol_cross_reference.json", {
        **base_meta,
        "symbols_by_class": result.symbols_by_class,
        "asset_ctxs_cross_reference": result.asset_ctxs_cross_reference,
        "l2_archive_cross_reference": result.l2_archive_cross_reference,
    })

    # p3_deployer_cross_reference.json
    _atomic_write(run_dir / "p3_deployer_cross_reference.json", {
        **base_meta,
        "deployer_cross_reference": result.deployer_cross_reference,
    })

    # p3_archive_visibility.json
    _atomic_write(run_dir / "p3_archive_visibility.json", {
        **base_meta,
        "asset_ctxs_cross_reference": result.asset_ctxs_cross_reference,
        "l2_archive_cross_reference": result.l2_archive_cross_reference,
    })

    # run_manifest.json
    _atomic_write(run_dir / "run_manifest.json", base_meta)



def run_probe(
    start_date: str = "2025-10-13",
    end_date: str | None = None,
    max_days: int = 30,
    max_block_files: int = 5000,
    download_budget_bytes: int = 5_000_000_000,
    explorer_block_budget_bytes: int = 1_000_000_000,
    allow_network_public: bool = False,
    allow_s3_archive_read: bool = False,
    dry_run: bool = False,
    max_layout_prefixes: int = 11,
    max_timestamp_sample_files: int = 50,
    max_timestamp_sample_bytes: int = 200_000_000,
) -> ProbeResult:
    """Execute the HIP-3 builder deployment event discovery probe.

    Returns a fully populated :class:`ProbeResult`.
    """
    sha, dirty = _get_git_info()
    result = ProbeResult(
        status=ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_READY,
        run_id=datetime.now(UTC).strftime("%Y%m%d_%H%M%S") + "_" + hashlib.sha256(start_date.encode()).hexdigest()[:8],
        created_at_utc=datetime.now(UTC).isoformat(),
        git_sha=sha,
        git_dirty=dirty,
        repo_root=str(Path(__file__).resolve().parents[4]),
        explorer_block_date_range=(start_date, end_date or ""),
        download_budget_bytes=download_budget_bytes,
        explorer_block_budget_bytes=explorer_block_budget_bytes,
        s3_requester_pays_acknowledged=allow_s3_archive_read,
        max_layout_prefixes=max_layout_prefixes,
        max_timestamp_sample_files=max_timestamp_sample_files,
        max_timestamp_sample_bytes=max_timestamp_sample_bytes,
    )
    chokepoint = NetworkChokepoint(allow_network_public, allow_s3_archive_read)
    # AWS identity preflight if S3 read is allowed
    if allow_s3_archive_read:
        avail, suffix = _aws_identity_preflight(chokepoint)
        result.aws_identity_available = avail
        result.aws_account_suffix = suffix
    if dry_run:
        result.status = ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_READY
        return result

    # Discover explorer‑block bucket layout before scanning.
    layout_info = _discover_explorer_block_layout(chokepoint)
    # Record layout discovery details in result for reporting.
    result.explorer_root_listing_status = layout_info["status"].value
    result.explorer_block_layout = layout_info["layout"]
    result.explorer_root_prefixes = layout_info["prefixes"]
    result.explorer_root_keys = layout_info["keys"]
    # Block-range layout metadata
    result.layout_type = layout_info["layout"]
    result.layout_stride = layout_info.get("stride")
    range_numbers = layout_info.get("range_numbers", [])
    if range_numbers:
        result.layout_first_range = str(range_numbers[0])
        result.layout_last_range = str(range_numbers[-1])
    # Handle failure or unknown layout cases.
    if layout_info["status"] in (
        ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_ACCESS_DENIED,
    ):
        result.status = layout_info["status"]
        return result
    if layout_info["status"] == ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_EMPTY:
        result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_EMPTY
        return result
    if layout_info["status"] == ScoutStatus.HIP3_EXPLORER_BLOCK_LAYOUT_UNKNOWN:
        result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_LAYOUT_UNKNOWN
        return result
    
    layout = layout_info["layout"]
    
    # Determine which prefixes to scan
    scan_prefixes: list[str] = []
    mapping: dict[str, Any] = {}

    if layout == "date_partitioned":
        # Original path: date-partitioned layout
        scan_prefixes = layout_info["prefixes"]
    elif layout == "block_range_partitioned":
        # New path: block-range partitioned layout
        # Step 1: Sample timestamps from block files
        range_prefixes = layout_info["prefixes"][:max_layout_prefixes]
        try:
            timestamp_samples = _sample_block_timestamps(
                chokepoint,
                range_prefixes,
                max_timestamp_sample_files,
                max_timestamp_sample_bytes,
                target_start_date=start_date,
            )
            result.timestamp_samples = timestamp_samples
            
            if not timestamp_samples:
                result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_MAPPING_INSUFFICIENT_SAMPLES
                return result
            
            # Step 2: Map date to block ranges
            mapping = _map_date_to_block_range(
                start_date,
                end_date,
                timestamp_samples,
            )
            result.date_block_mapping = mapping
            
            if mapping["status"] == "HIP3_EXPLORER_BLOCK_DATE_MAPPING_READY":
                result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_MAPPING_READY
                scan_prefixes = mapping["mapped_ranges"]
            elif mapping["status"] == "HIP3_EXPLORER_BLOCK_TIMESTAMP_PARSE_FAILED":
                result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_TIMESTAMP_PARSE_FAILED
                return result
            elif mapping["status"] == "HIP3_EXPLORER_BLOCK_DATE_OUT_OF_RANGE":
                result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_OUT_OF_RANGE
                return result
            else:
                result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_MAPPING_INSUFFICIENT_SAMPLES
                return result
        except BudgetExceededError:
            result.status = ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_ERROR
            result.public_info_cross_reference["error"] = "Timestamp sampling budget exceeded"
            return result
    else:
        result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_MAPPING_UNAVAILABLE
        return result
    
    # Continue with block file listing
    if layout == "date_partitioned":
        # Validate that the explorer‑block archive source exists before scanning.
        valid_prefix, sample_keys = _validate_explorer_block_source(start_date, chokepoint)
        if not valid_prefix:
            result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_PREFIX_OR_PATH_INVALID
            result.public_info_cross_reference = {"sample_keys": sample_keys}
            return result
        
        try:
            block_files = _list_explorer_block_files(start_date, end_date, max_days, max_block_files, chokepoint)
        except Exception as exc:
            result.status = ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_ERROR
            result.public_info_cross_reference["error"] = str(exc)
            return result
    else:
        # Block-range layout: list files from mapped ranges
        try:
            block_files = _list_block_range_files(
                scan_prefixes,
                max_block_files,
                chokepoint,
                start_block=mapping.get("start_block"),
                end_block=mapping.get("end_block"),
            )
        except Exception as exc:
            result.status = ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_ERROR
            result.public_info_cross_reference["error"] = str(exc)
            return result

    total_explorer = sum(sz for _, sz in block_files)
    if total_explorer > explorer_block_budget_bytes:
        raise BudgetExceededError("Explorer block download budget exceeded")
    tmp_dir = Path("/tmp/hip3_builder_probe")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for s3_path, sz in block_files:
        if sz > explorer_block_budget_bytes:
            continue
        candidates = _download_and_process_block(s3_path, tmp_dir, chokepoint, result)
        result.candidate_events.extend(candidates)
        result.bytes_downloaded_total = chokepoint.bytes_downloaded
        result.bytes_downloaded_by_source = dict(chokepoint.bytes_by_source)
        if result.bytes_downloaded_total > download_budget_bytes:
            raise BudgetExceededError("Total download budget exceeded")
    if not result.candidate_events:
        result.status = ScoutStatus.HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS
        return result
    result.status = ScoutStatus.HIP3_DEPLOYMENT_EVENTS_FOUND
    info_map = _cross_reference_public_info(result.candidate_events, chokepoint)
    result.public_info_cross_reference = info_map
    symbols = [c.symbol for c in result.candidate_events if c.symbol_extractable and c.symbol]
    result.candidate_symbols = symbols
    visibility = _check_archive_visibility(symbols, chokepoint)
    result.archive_visibility = visibility
    any_asset_missing = any(not v["asset_ctxs"] for v in visibility.values())
    any_l2_missing = any(not v["l2"] for v in visibility.values())
    if any_asset_missing:
        result.status = ScoutStatus.HIP3_DEPLOYMENT_EVENTS_FOUND_BUT_ASSET_CTXS_MISSING
    elif any_l2_missing:
        result.status = ScoutStatus.HIP3_DEPLOYMENT_EVENTS_FOUND_BUT_L2_MISSING
    else:
        result.status = ScoutStatus.HIP3_DEPLOYMENT_EVENTS_FOUND_AND_ARCHIVE_VISIBLE
    result.bytes_downloaded_total = chokepoint.bytes_downloaded
    result.bytes_downloaded_by_source = dict(chokepoint.bytes_by_source)
    return result


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def _serialize_timestamp_samples(samples: list[BlockTimestampSample]) -> list[dict]:
    """Serialize timestamp samples to a JSON-serializable list."""
    return [
        {
            "source_key": s.source_key,
            "top_level_range_prefix": s.top_level_range_prefix,
            "block_number": s.block_number,
            "block_timestamp_utc": s.block_timestamp_utc,
            "parse_status": s.parse_status,
            "byte_count": s.byte_count,
            "content_hash": s.content_hash,
            "action_type_count": s.action_type_count,
        }
        for s in samples
    ]


def _write_inventory_artifacts(run_dir: Path, inv: ActionInventoryResult, argv: list[str]) -> None:
    """Write all required artifacts for an inventory-mode run."""
    sha, dirty = inv.git_sha, inv.git_dirty
    base_meta = {
        "study_id": inv.study_id,
        "run_id": inv.run_id,
        "created_at_utc": inv.created_at_utc,
        "git_sha": sha,
        "git_dirty": dirty,
        "repo_root": inv.repo_root,
        "command_args": argv,
        "safety_mode": inv.safety_mode,
        "schema_version": inv.schema_version,
        "final_status": inv.final_status or str(inv.status),
    }

    # summary.json
    summary = {
        **base_meta,
        "files_listed": inv.files_listed,
        "files_read": inv.files_read,
        "bytes_downloaded": inv.bytes_downloaded,
        "inferred_layout": inv.inferred_layout,
        "action_type_counts": inv.action_type_counts,
        "top_level_schema_shapes": inv.top_level_schema_shapes,
        "nested_field_paths": inv.nested_field_paths,
        "decode_method_used": inv.decode_method_used,
        "decode_failures_count": inv.decode_failures_count,
        "opaque_records_count": inv.opaque_records_count,
        "hip3_candidate_passive_count": inv.hip3_candidate_passive_count,
        "aws_identity_available": inv.aws_identity_available,
        "aws_account_suffix": inv.aws_account_suffix,
        "explorer_root_listing_status": inv.explorer_root_listing_status,
    }
    _atomic_write(run_dir / "summary.json", summary)

    # summary.md
    action_lines = "\n".join(
        f"- `{k}`: {v}" for k, v in sorted(inv.action_type_counts.items(), key=lambda x: -x[1])
    ) or "(none)"
    md_lines = [
        "# HIP-3 Explorer Block Action-Type Inventory",
        "",
        f"**Status:** `{inv.final_status}`",
        f"**Run ID:** {inv.run_id}",
        f"**Layout:** {inv.inferred_layout}",
        f"**Files listed:** {inv.files_listed}  |  **Files read:** {inv.files_read}",
        f"**Bytes downloaded:** {inv.bytes_downloaded:,}",
        f"**Decode method:** {inv.decode_method_used}",
        f"**Decode failures:** {inv.decode_failures_count}",
        f"**Opaque records:** {inv.opaque_records_count}",
        f"**HIP-3 candidate passive count:** {inv.hip3_candidate_passive_count}",
        "",
        "## Action Type Counts",
        action_lines,
        "",
        "> NOTE: Universe-delta probe (c9056978aa) found 13 post-launch symbol additions",
        "> (all crypto_like or unknown). That result does NOT prove absence of HIP-3",
        "> deployment events — this inventory independently enumerates action types.",
    ]
    (run_dir / "summary.md").write_text("\n".join(md_lines))

    # action_type_inventory.json
    _atomic_write(run_dir / "action_type_inventory.json", {
        **base_meta,
        "action_type_counts": inv.action_type_counts,
        "total_actions_seen": sum(inv.action_type_counts.values()),
        "unique_action_types": len(inv.action_type_counts),
        "hip3_candidate_passive_count": inv.hip3_candidate_passive_count,
        "decode_method_used": inv.decode_method_used,
        "decode_failures_count": inv.decode_failures_count,
        "opaque_records_count": inv.opaque_records_count,
    })

    # schema_shape_samples.json
    _atomic_write(run_dir / "schema_shape_samples.json", {
        **base_meta,
        "top_level_schema_shapes": inv.top_level_schema_shapes,
        "nested_field_paths": inv.nested_field_paths,
        "sample_redacted_records": inv.sample_redacted_records,
    })

    # run_manifest.json
    _atomic_write(run_dir / "run_manifest.json", {
        **base_meta,
        "archive_bucket": inv.explorer_block_bucket,
        "archive_prefix": inv.explorer_block_root_prefix,
        "aws_identity_available": inv.aws_identity_available,
        "aws_account_suffix": inv.aws_account_suffix,
        "no_registry_mutation": True,
        "no_full_account_id": True,
        "files_listed": inv.files_listed,
        "files_read": inv.files_read,
        "bytes_downloaded": inv.bytes_downloaded,
        "inferred_layout": inv.inferred_layout,
        "decode_method_used": inv.decode_method_used,
        "max_block_files": inv.max_block_files,
        "explorer_block_budget_bytes": inv.explorer_block_budget_bytes,
        "explorer_root_prefixes": inv.explorer_root_prefixes,
        "explorer_root_listing_status": inv.explorer_root_listing_status,
    })



def _p2_summary_md(result: P2SearchResult) -> str:
    """Generate a human-readable summary for P2 results."""
    lines = [
        "# HIP-3 P2 Deployment Event Search",
        "",
        f"**Status:** `{result.final_status}`",
        f"**Run ID:** {result.run_id}",
        f"**Layout:** {result.inferred_layout}",
        f"**Files read:** {result.files_read}  |  **Bytes downloaded:** {result.bytes_downloaded:,}",
        f"**Blocks parsed:** {result.blocks_parsed}",
        f"**Total actions:** {result.total_actions}",
        f"**Unique action types:** {len(result.unique_action_types)}",
        f"**Candidates found:** {len(result.candidates)}",
        f"**Decode method:** {result.decode_method_used}",
        f"**Decode failures:** {result.decode_failures_count}",
        f"**Opaque records:** {result.opaque_records_count}",
        "",
        "## P2 Windows Scanned",
    ]
    for ws in result.window_summaries:
        lines.append("")
        lines.append(f"### Window: {ws.window_name}")
        lines.append(f"- Date range: {ws.date_range[0]} to {ws.date_range[1]}")
        lines.append(f"- Mapped ranges: {len(ws.mapped_ranges)}")
        lines.append(f"- Files scanned: {ws.files_scanned}")
        lines.append(f"- Bytes: {ws.bytes_downloaded:,}")
        lines.append(f"- Blocks parsed: {ws.blocks_parsed}")
        lines.append(f"- Actions: {ws.total_actions}")
        lines.append(f"- Unique types: {len(ws.unique_action_types)}")
        lines.append(f"- Candidates: {ws.candidate_count}")
        lines.append(f"- Status: `{ws.status}`")
        if ws.unique_action_types:
            lines.append("- Top action types:")
            for at in ws.unique_action_types[:10]:
                lines.append(f"  - `{at}`")

    lines.append("")
    if result.candidates:
        lines.append("## Top Candidates")
        for c in result.candidates[:20]:
            lines.append(f"- **{c.window_name}** `{c.action_type}` class={c.candidate_class} symbol={c.symbol_or_coin} user={c.user_or_deployer} terms={c.matched_terms}")

    lines.append("")
    lines.append("> NOTE: Universe-delta probe (c9056978aa) found 13 post-launch symbol additions")
    lines.append("> (all crypto_like or unknown). That result does NOT prove absence of HIP-3")
    lines.append("> deployment events -- this P2 search independently enumerates candidates.")
    return chr(10).join(lines)


def _write_p2_artifacts(run_dir: Path, result: P2SearchResult, argv: list[str]) -> None:
    """Write all required P2 artifacts."""
    sha, dirty = result.git_sha, result.git_dirty
    base_meta = {
        "study_id": result.study_id,
        "run_id": result.run_id,
        "created_at_utc": result.created_at_utc,
        "git_sha": sha,
        "git_dirty": dirty,
        "repo_root": result.repo_root,
        "command_args": argv,
        "safety_mode": result.safety_mode,
        "schema_version": result.schema_version,
        "final_status": result.final_status or str(result.status),
    }

    # summary.json
    summary = {
        **base_meta,
        "files_listed": result.files_listed,
        "files_read": result.files_read,
        "bytes_downloaded": result.bytes_downloaded,
        "blocks_parsed": result.blocks_parsed,
        "total_actions": result.total_actions,
        "unique_action_types": result.unique_action_types,
        "candidate_count": len(result.candidates),
        "inferred_layout": result.inferred_layout,
        "decode_method_used": result.decode_method_used,
        "decode_failures_count": result.decode_failures_count,
        "opaque_records_count": result.opaque_records_count,
        "aws_identity_available": result.aws_identity_available,
        "aws_account_suffix": result.aws_account_suffix,
        "explorer_root_listing_status": result.explorer_root_listing_status,
        "p2_windows": result.p2_windows,
        "p2_max_files_per_window": result.p2_max_files_per_window,
        "p2_preserve_excerpts": result.p2_preserve_excerpts,
        "p2_rare_action_threshold": result.p2_rare_action_threshold,
    }
    _atomic_write(run_dir / "summary.json", summary)

    # summary.md
    (run_dir / "summary.md").write_text(_p2_summary_md(result))

    # run_manifest.json
    manifest = {
        **base_meta,
        "archive_bucket": result.explorer_block_bucket,
        "archive_prefix": result.explorer_block_root_prefix,
        "no_registry_mutation": True,
        "no_full_account_id": True,
        "files_listed": result.files_listed,
        "files_read": result.files_read,
        "bytes_downloaded": result.bytes_downloaded,
        "blocks_parsed": result.blocks_parsed,
        "total_actions": result.total_actions,
        "inferred_layout": result.inferred_layout,
        "decode_method_used": result.decode_method_used,
        "p2_windows": result.p2_windows,
    }
    _atomic_write(run_dir / "run_manifest.json", manifest)

    # p2_window_scan_summary.json
    _atomic_write(run_dir / "p2_window_scan_summary.json", {
        **base_meta,
        "windows": [
            {
                "window_name": ws.window_name,
                "date_range": ws.date_range,
                "files_scanned": ws.files_scanned,
                "bytes_downloaded": ws.bytes_downloaded,
                "blocks_parsed": ws.blocks_parsed,
                "total_actions": ws.total_actions,
                "unique_action_types": ws.unique_action_types,
                "candidate_count": ws.candidate_count,
                "status": ws.status,
            }
            for ws in result.window_summaries
        ],
    })

    # p2_action_type_inventory.json
    action_counts: dict[str, int] = {}
    for ws in result.window_summaries:
        for at in ws.unique_action_types:
            action_counts[at] = action_counts.get(at, 0) + 1
    _atomic_write(run_dir / "p2_action_type_inventory.json", {
        **base_meta,
        "action_type_counts": action_counts,
        "total_actions": result.total_actions,
        "decode_method_used": result.decode_method_used,
    })

    # p2_deployment_event_candidates.json
    _atomic_write(run_dir / "p2_deployment_event_candidates.json", {
        **base_meta,
        "candidates": [
            {
                "window_name": c.window_name,
                "source_key": c.source_key,
                "source_content_hash": c.source_content_hash,
                "block_number": c.block_number,
                "block_timestamp_utc": c.block_timestamp_utc,
                "tx_index": c.tx_index,
                "action_type": c.action_type,
                "user_or_deployer": c.user_or_deployer,
                "symbol_or_coin": c.symbol_or_coin,
                "matched_terms": c.matched_terms,
                "candidate_class": c.candidate_class,
                "redacted_excerpt": c.redacted_excerpt,
                "raw_action_type_count": c.raw_action_type_count,
                "is_rare_action": c.is_rare_action,
                "is_new_vs_p1": c.is_new_vs_p1,
            }
            for c in result.candidates
        ],
        "total_candidates": len(result.candidates),
    })

    # p2_candidate_symbol_cross_reference.json
    symbols = list(set(c.symbol_or_coin for c in result.candidates if c.symbol_or_coin))
    _atomic_write(run_dir / "p2_candidate_symbol_cross_reference.json", {
        **base_meta,
        "extracted_symbols": symbols,
    })

    # p2_schema_field_inventory.json
    _atomic_write(run_dir / "p2_schema_field_inventory.json", {
        **base_meta,
        "schema_field_inventory": result.schema_field_inventory,
    })


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="HIP‑3 Builder Deployment Event Discovery Probe")
    parser.add_argument("--out-root", default="reports/hip3_builder_deployment_event_discovery_v0")
    parser.add_argument("--start-date", default="2025-10-13")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--max-days", type=int, default=30)
    parser.add_argument("--max-block-files", type=int, default=5000)
    parser.add_argument("--download-budget-bytes", type=int, default=5_000_000_000)
    parser.add_argument("--explorer-block-budget-bytes", type=int, default=1_000_000_000)
    parser.add_argument("--allow-network-public", action="store_true")
    parser.add_argument("--allow-s3-archive-read", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-layout-prefixes", type=int, default=11)
    parser.add_argument("--max-timestamp-sample-files", type=int, default=50)
    parser.add_argument("--max-timestamp-sample-bytes", type=int, default=200_000_000)
    parser.add_argument(
        "--inventory-action-types-only",
        action="store_true",
        help="P1 mode: enumerate explorer-block action types without searching for deployment events.",
    )
    parser.add_argument(
        "--p2-deployment-search",
        action="store_true",
        help="P2 mode: search for HIP-3 deployment events across bounded windows.",
    )
    parser.add_argument(
        "--p2-window",
        default="all",
        choices=["prelaunch", "launch", "recent", "all"],
        help="P2 window to scan (default: all).",
    )
    parser.add_argument(
        "--p2-max-files-per-window", type=int, default=1000,
        help="P2 max files to scan per window (default: 1000).",
    )
    parser.add_argument(
        "--p2-preserve-excerpts", type=int, default=50,
        help="P2 max rare action excerpts to preserve (default: 50).",
    )
    parser.add_argument(
        "--p2-rare-action-threshold", type=int, default=25,
        help="P2 action type count threshold for rarity (default: 25).",
    )
    parser.add_argument(
        "--p3-confirm-candidates",
        action="store_true",
        help="P3 mode: confirm P2 candidates against public data.",
    )
    parser.add_argument("--p3-input-report", default=None, help="P3: path to P2 report directory.")
    parser.add_argument("--p3-max-candidates", type=int, default=50, help="P3: max candidates to process.")
    parser.add_argument("--p3-expand-neighborhood-blocks", type=int, default=20, help="P3: neighborhood expansion radius.")
    parser.add_argument("--p3-cross-reference-asset-ctxs", action="store_true", help="P3: cross-reference against asset_ctxs.")
    parser.add_argument("--p3-cross-reference-l2", action="store_true", help="P3: cross-reference against L2 archive.")
    args = parser.parse_args(argv)
    out_root = Path(args.out_root)

    if args.p3_confirm_candidates:
        if not args.p3_input_report:
            print("ERROR: --p3-input-report is required for P3 mode")
            return 1
        p3_result = _run_p3_confirmation(
            input_report=args.p3_input_report,
            max_candidates=args.p3_max_candidates,
            expand_neighborhood_blocks=args.p3_expand_neighborhood_blocks,
            cross_reference_asset_ctxs=args.p3_cross_reference_asset_ctxs,
            cross_reference_l2=args.p3_cross_reference_l2,
            allow_network_public=args.allow_network_public,
            allow_s3_archive_read=args.allow_s3_archive_read,
            download_budget_bytes=args.download_budget_bytes,
            explorer_block_budget_bytes=args.explorer_block_budget_bytes,
        )
        run_dir = out_root / (p3_result.run_id + "_p3_confirmation")
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_p3_artifacts(run_dir, p3_result, sys.argv[1:])
        print(f"{p3_result.status}")
        print(f"P3 confirmation completed. Report written to {run_dir}")
        return 0

    if args.p2_deployment_search:
        result = run_p2_deployment_search(
            p2_window=args.p2_window,
            max_files_per_window=args.p2_max_files_per_window,
            preserve_excerpts=args.p2_preserve_excerpts,
            rare_action_threshold=args.p2_rare_action_threshold,
            download_budget_bytes=args.download_budget_bytes,
            explorer_block_budget_bytes=args.explorer_block_budget_bytes,
            allow_network_public=args.allow_network_public,
            allow_s3_archive_read=args.allow_s3_archive_read,
            p1_action_types={"order", "cancel", "SetGlobalAction", "CreditBridgeDepositAction", "connect"},
        )
        run_dir = out_root / result.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_p2_artifacts(run_dir, result, sys.argv[1:])
        print(f"{result.status}")
        print(f"P2 deployment search completed. Report written to {run_dir}")
        return 0

    if args.inventory_action_types_only:
        inv = run_inventory_probe(
            max_block_files=args.max_block_files if args.max_block_files != 5000 else 20,
            explorer_block_budget_bytes=args.explorer_block_budget_bytes if args.explorer_block_budget_bytes != 1_000_000_000 else 50_000_000,
            download_budget_bytes=args.download_budget_bytes if args.download_budget_bytes != 5_000_000_000 else 50_000_000,
            allow_network_public=args.allow_network_public,
            allow_s3_archive_read=args.allow_s3_archive_read,
            command_args=sys.argv[1:],
        )
        run_dir = out_root / inv.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        _write_inventory_artifacts(run_dir, inv, sys.argv[1:])
        print(f"{inv.status}")
        print(f"Inventory probe completed. Report written to {run_dir}")
        return 0

    result = run_probe(
        start_date=args.start_date,
        end_date=args.end_date,
        max_days=args.max_days,
        max_block_files=args.max_block_files,
        download_budget_bytes=args.download_budget_bytes,
        explorer_block_budget_bytes=args.explorer_block_budget_bytes,
        allow_network_public=args.allow_network_public,
        allow_s3_archive_read=args.allow_s3_archive_read,
        dry_run=args.dry_run,
        max_layout_prefixes=args.max_layout_prefixes,
        max_timestamp_sample_files=args.max_timestamp_sample_files,
        max_timestamp_sample_bytes=args.max_timestamp_sample_bytes,
    )
    run_dir = out_root / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    # Serialize result for JSON output
    result_dict = result.__dict__.copy()
    result_dict["status"] = str(result.status)
    result_dict["timestamp_samples"] = _serialize_timestamp_samples(result.timestamp_samples)
    _atomic_write(run_dir / "summary.json", result_dict)
    md = ["# HIP‑3 Builder Deployment Event Discovery", f"**Status:** {result.status}", "", "## Action Types", "- " + "\n- ".join(result.action_types_inventoried), "", f"## Candidate Events ({len(result.candidate_events)})"]
    (run_dir / "summary.md").write_text("\n".join(md))
    _atomic_write(run_dir / "deployment_event_candidates.json", {"candidates": [c.__dict__ for c in result.candidate_events]})
    _atomic_write(run_dir / "action_type_inventory.json", {"action_types": result.action_types_inventoried})
    _atomic_write(run_dir / "builder_symbol_cross_reference.json", result.public_info_cross_reference)
    _atomic_write(run_dir / "archive_visibility.json", result.archive_visibility)
    # Explorer block layout artifact
    _atomic_write(run_dir / "explorer_block_layout.json", {
        "layout_type": result.layout_type,
        "layout_stride": result.layout_stride,
        "first_range": result.layout_first_range,
        "last_range": result.layout_last_range,
        "root_prefixes": result.explorer_root_prefixes,
        "root_keys": result.explorer_root_keys,
        "root_listing_status": result.explorer_root_listing_status,
    })
    # Timestamp samples artifact
    _atomic_write(run_dir / "block_timestamp_samples.json", {
        "samples": _serialize_timestamp_samples(result.timestamp_samples),
        "sample_count": len(result.timestamp_samples),
    })
    # Date-to-block mapping artifact
    _atomic_write(run_dir / "date_block_mapping.json", result.date_block_mapping)
    manifest = {
        "study_id": result.study_id,
        "run_id": result.run_id,
        "created_at_utc": result.created_at_utc,
        "git_sha": result.git_sha,
        "git_dirty": result.git_dirty,
        "repo_root": result.repo_root,
        "command_args": sys.argv[1:],
        "safety_mode": result.safety_mode,
        "schema_version": result.schema_version,
        "bytes_downloaded_total": result.bytes_downloaded_total,
        "bytes_downloaded_by_source": result.bytes_downloaded_by_source,
        "final_status": str(result.status),
        "helpers_reused": result.helpers_reused,
        "layout_type": result.layout_type,
        "layout_stride": result.layout_stride,
        "first_range": result.layout_first_range,
        "last_range": result.layout_last_range,
        "timestamp_sample_count": len(result.timestamp_samples),
        "date_block_mapping_status": result.date_block_mapping.get("status", "") if result.date_block_mapping else "",
    }
    _atomic_write(run_dir / "run_manifest.json", manifest)
    print(f"{result.status}")
    print(f"Probe completed. Report written to {run_dir}")
    return 0

if __name__ == "__main__":
    sys.exit(main())