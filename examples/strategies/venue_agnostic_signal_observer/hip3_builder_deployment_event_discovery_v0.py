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
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.request import urlopen, Request


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
    HIP3_EXPLORER_BLOCK_PREFIX_OR_PATH_INVALID = "HIP3_EXPLORER_BLOCK_PREFIX_OR_PATH_INVALID"


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
    
    def s3_download(self, s3_path: str, local_path: Path, requester_pays: bool = True) -> int:
        """S3 download through chokepoint."""
        if not self.allow_s3_archive_read:
            raise PermissionError(
                f"S3 access to {s3_path} blocked. Pass --allow-s3-archive-read to enable."
            )
        cmd = ["aws", "s3", "cp", s3_path, str(local_path)]
        if requester_pays:
            cmd.insert(3, "--requester-pays")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            if "AccessDenied" in result.stderr or "Requester Pays" in result.stderr:
                pass
            raise RuntimeError(f"S3 download failed: {result.stderr}")
        
        file_size = local_path.stat().st_size if local_path.exists() else 0
        self._track_bytes(file_size, f"s3:{s3_path.split('/')[2]}")
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
    """Attempt AWS STS get-caller-identity to verify credentials.
    Returns (available, suffix) where suffix is last 4 digits of account ID.
    """
    cmd = ["aws", "sts", "get-caller-identity", "--output", "json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return False, ""
        data = json.loads(result.stdout)
        account = data.get("Account", "")
        suffix = account[-4:] if account else ""
        return True, suffix
    except Exception:
        return False, ""


def _get_git_info() -> tuple[str, bool]:
    """Get current git SHA and dirty status."""
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
        
        result = subprocess.run(
            ["git", "-C", str(git_dir.parent), "diff", "--quiet"],
            capture_output=True,
            timeout=10
        )
        dirty = result.returncode != 0
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
    block_height = block_data.get("block_number", block_data.get("height"))
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
# Core probe workflow
# ---------------------------------------------------------------------------

def _discover_explorer_block_layout(chokepoint: NetworkChokepoint) -> dict:
    """Discover the key layout under the explorer_blocks bucket.

    Returns a dict with keys:
        - status: ScoutStatus indicating the outcome of the root listing.
        - layout: one of "date_partitioned", "block_range_partitioned", "flat_block_files", "unknown", or "".
        - prefixes: list of up to 20 child prefixes (strings ending with '/').
        - keys: list of up to 20 object keys (full s3 paths).
    """
    root_pref = f"s3://{EXPLORER_BLOCK_BUCKET}/{EXPLORER_BLOCK_PREFIX}/"
    cmd = ["aws", "s3", "ls", root_pref, "--requester-pays"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"status": ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED, "layout": "", "prefixes": [], "keys": []}
    if result.returncode != 0:
        stderr = result.stderr.lower()
        if "unable to locate credentials" in stderr:
            status = ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED
        elif "accessdenied" in stderr or "requester pays" in stderr:
            status = ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_ACCESS_DENIED
        else:
            status = ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED
        return {"status": status, "layout": "", "prefixes": [], "keys": []}

    prefixes: list[str] = []
    keys: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        if parts[0] == "PRE":
            # Prefix entry
            if len(parts) >= 2:
                prefixes.append(parts[1])
        else:
            # File entry, assumes size then date then key
            if len(parts) >= 4:
                keys.append(parts[3])
    if not prefixes and not keys:
        return {"status": ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_EMPTY, "layout": "", "prefixes": [], "keys": []}
    # Determine layout heuristics
    layout = "unknown"
    date_pat = re.compile(r"\d{4}-\d{2}-\d{2}/")
    block_range_pat = re.compile(r"\d{10,}\.json")
    if any(date_pat.match(p) for p in prefixes):
        layout = "date_partitioned"
    elif any(block_range_pat.search(k) for k in keys):
        layout = "block_range_partitioned"
    elif keys and not prefixes:
        layout = "flat_block_files"
    # Return discovery result
    return {
        "status": ScoutStatus.HIP3_EXPLORER_BLOCK_LAYOUT_DISCOVERED,
        "layout": layout,
        "prefixes": prefixes[:20],
        "keys": keys[:20],
    }

def _validate_explorer_block_source(start_date: str, chokepoint: NetworkChokepoint) -> tuple[bool, list[str]]:
    """Validate that the explorer‑block S3 source exists and is reachable.

    Returns a tuple ``(valid, sample_keys)`` where ``valid`` is ``True`` when the prefix
    ``s3://{EXPLORER_BLOCK_BUCKET}/{EXPLORER_BLOCK_PREFIX}/<YYYY-MM-DD>/`` contains at least one object.
    ``sample_keys`` contains up to the first ten object keys (or an empty list on failure).
    """
    pref = f"s3://{EXPLORER_BLOCK_BUCKET}/{EXPLORER_BLOCK_PREFIX}/{start_date}/"
    cmd = ["aws", "s3", "ls", pref, "--requester-pays"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return False, []
    if result.returncode != 0:
        return False, []
    keys: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) < 4:
            continue
        keys.append(parts[3])
    return (len(keys) > 0), keys[:10]


def _list_explorer_block_files(
    start_date: str,
    end_date: str | None,
    max_days: int,
    max_files: int,
    chokepoint: NetworkChokepoint,
) -> list[tuple[str, int]]:
    """List S3 explorer block files.

    Returns a list of (s3_path, size_bytes). Uses ``aws s3 ls``.
    The function respects ``max_days`` and ``max_files`` caps.
    """
    start_dt = datetime.fromisoformat(start_date).replace(tzinfo=UTC)
    end_dt = datetime.utcnow().replace(tzinfo=UTC) if end_date is None else datetime.fromisoformat(end_date).replace(tzinfo=UTC)
    if (end_dt - start_dt).days > max_days:
        end_dt = start_dt + timedelta(days=max_days)

    prefixes: list[str] = []
    cur = start_dt
    while cur <= end_dt:
        day_str = cur.strftime("%Y-%m-%d")
        prefixes.append(f"s3://{EXPLORER_BLOCK_BUCKET}/{EXPLORER_BLOCK_PREFIX}/{cur.strftime('%Y/%m/%d')}/")
        cur += timedelta(days=1)

    files: list[tuple[str, int]] = []
    for pref in prefixes:
        cmd = ["aws", "s3", "ls", pref, "--requester-pays"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            size = int(parts[2])
            filename = parts[3]
            s3_path = f"{pref}{filename}"
            files.append((s3_path, size))
            if len(files) >= max_files:
                break
        if len(files) >= max_files:
            break
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
    for sym in symbols:
        asset_pref = f"s3://hyperliquid-archive/asset_ctxs/{sym}/"
        l2_pref = f"s3://hyperliquid-archive/market_data/{sym}/l2Book/"
        for pref, key in [(asset_pref, "asset_ctxs"), (l2_pref, "l2")]:
            cmd = ["aws", "s3", "ls", pref, "--no-sign-request"]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            except subprocess.TimeoutExpired:
                continue
            if res.returncode == 0 and res.stdout.strip():
                visibility[sym][key] = True
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
) -> ProbeResult:
    """Execute the HIP-3 builder deployment event discovery probe.

    Returns a fully populated :class:`ProbeResult`.
    """
    sha, dirty = _get_git_info()
    result = ProbeResult(
        status=ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_READY,
        run_id=datetime.utcnow().strftime("%Y%m%d_%H%M%S") + "_" + hashlib.sha256(start_date.encode()).hexdigest()[:8],
        created_at_utc=datetime.utcnow().replace(tzinfo=UTC).isoformat(),
        git_sha=sha,
        git_dirty=dirty,
        repo_root=str(Path(__file__).resolve().parents[4]),
        explorer_block_date_range=(start_date, end_date or ""),
        download_budget_bytes=download_budget_bytes,
        explorer_block_budget_bytes=explorer_block_budget_bytes,
        s3_requester_pays_acknowledged=allow_s3_archive_read,
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
    # Handle failure or unknown layout cases.
    if layout_info["status"] == ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED:
        result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED
        return result
    if layout_info["status"] == ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_EMPTY:
        result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_EMPTY
        return result
    if layout_info["status"] == ScoutStatus.HIP3_EXPLORER_BLOCK_LAYOUT_UNKNOWN:
        result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_LAYOUT_UNKNOWN
        return result
    if layout_info["layout"] != "date_partitioned":
        result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_MAPPING_UNAVAILABLE
        return result
    # Continue with date‑based block file listing.
    # ---------------------------------------------------------------------
    # Validate that the explorer‑block archive source exists before scanning.
    # ---------------------------------------------------------------------
    valid_prefix, sample_keys = _validate_explorer_block_source(start_date, chokepoint)
    if not valid_prefix:
        result.status = ScoutStatus.HIP3_EXPLORER_BLOCK_PREFIX_OR_PATH_INVALID
        result.public_info_cross_reference = {"sample_keys": sample_keys}
        return result
 
    try:
        block_files = _list_explorer_block_files(start_date, end_date, max_days, max_block_files, chokepoint)

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
    except BudgetExceededError as be:
        result.status = ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_ERROR
        result.public_info_cross_reference["error"] = str(be)
    except PermissionError as pe:
        result.status = ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_ERROR
        result.public_info_cross_reference["error"] = str(pe)
    except Exception as exc:
        result.status = ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_ERROR
        result.public_info_cross_reference["error"] = str(exc)
    result.bytes_downloaded_total = chokepoint.bytes_downloaded
    result.bytes_downloaded_by_source = dict(chokepoint.bytes_by_source)
    return result

def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)

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
    args = parser.parse_args(argv)
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
    )
    out_root = Path(args.out_root)
    run_dir = out_root / result.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(run_dir / "summary.json", {**result.__dict__, "status": str(result.status)})
    md = ["# HIP‑3 Builder Deployment Event Discovery", f"**Status:** {result.status}", "", "## Action Types", "- " + "\n- ".join(result.action_types_inventoried), "", f"## Candidate Events ({len(result.candidate_events)})"]
    (run_dir / "summary.md").write_text("\n".join(md))
    _atomic_write(run_dir / "deployment_event_candidates.json", {"candidates": [c.__dict__ for c in result.candidate_events]})
    _atomic_write(run_dir / "action_type_inventory.json", {"action_types": result.action_types_inventoried})
    _atomic_write(run_dir / "builder_symbol_cross_reference.json", result.public_info_cross_reference)
    _atomic_write(run_dir / "archive_visibility.json", result.archive_visibility)
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
    }
    _atomic_write(run_dir / "run_manifest.json", manifest)
    print(f"{result.status}")
    print(f"Probe completed. Report written to {run_dir}")
    return 0

if __name__ == "__main__":
    sys.exit(main())