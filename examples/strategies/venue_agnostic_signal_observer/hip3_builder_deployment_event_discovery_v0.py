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
    args = parser.parse_args(argv)
    out_root = Path(args.out_root)

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