#!/usr/bin/env python3
"""Bounded requester-pays availability probe for Hyperliquid archive sources.

Probes S3 archive structure for:
  - node_fills_by_block/hourly/
  - misc_events_by_block/hourly/
  - explorer_blocks/
  - replica_cmds/
  - vault metadata (via public info API behind --allow-public-metadata-api)

Downloads bounded single-date/hour samples, inspects schemas, searches for
liquidation/backstop markers, and reports availability / schema readiness
for HLP backstop reconstruction.

Hard constraints:
- Probes at most one calendar day first (default: 2026-05-24)
- Stops if estimated bytes exceed 5 GB
- Never syncs bucket, downloads broad history, or runs full diagnostic
- No orders/auth/trading/live/paper/shadow/bot/systemd/registry mutation
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUCKET = "hl-mainnet-node-data"
NODE_FILLS_PREFIX = "node_fills_by_block/hourly"
MISC_EVENTS_PREFIX = "misc_events_by_block/hourly"
EXPLORER_BLOCKS_PREFIX = "explorer_blocks"
REPLICA_CMDS_PREFIX = "replica_cmds"
DEFAULT_PROBE_DATE = "2026-05-24"
MAX_BYTES_THRESHOLD = 5 * 1024**3  # 5 GB

STUDY_ID = "hlp_backstop_archive_availability_probe"

# Liquidation/backstop marker search terms
MARKER_TERMS = [
    "liquidation", "liquidate", "liquidator", "backstop",
    "vault", "takeover", "margin", "maintenance",
    "force", "forced", "clearinghouse", "deleverage", "adl",
    "liquida", "liq", "liquidat",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]


def datetime_utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def run_aws(args: list[str], timeout: int = 60) -> str:
    """Run aws CLI with --request-payer requester injected.

    Raises RuntimeError on non-zero exit or timeout.
    """
    cmd = ["aws", "s3"] + args + ["--request-payer", "requester"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"aws command timed out after {timeout}s: {' '.join(cmd[:5])}..."
        ) from e
    if result.returncode != 0:
        raise RuntimeError(
            f"aws command failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def run_aws_cp(src: str, dst: str, timeout: int = 120) -> str:
    cmd = ["aws", "s3", "cp", src, dst, "--request-payer", "requester"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"aws cp failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def human_bytes(b: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PiB"


def parse_ls_line(line: str) -> dict[str, Any] | None:
    """Parse a single `aws s3 ls --human-readable` output line."""
    line = line.strip()
    if not line or line.startswith("PRE") or line.startswith("total"):
        return None
    parts = line.split()
    if len(parts) < 4:
        return None
    try:
        size_str = parts[2]
        size_unit = parts[3]
        name = " ".join(parts[4:])
        multipliers = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
        size_bytes = int(float(size_str) * int(multipliers.get(size_unit, 1)))
        return {
            "name": name,
            "size_bytes": size_bytes,
            "size_human": f"{size_str} {size_unit}",
            "last_modified": f"{parts[0]} {parts[1]}",
        }
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Generic prefix/date probing
# ---------------------------------------------------------------------------


def probe_bucket_top(timeout: int = 30) -> dict[str, Any]:
    """List top-level prefixes in the bucket."""
    output = run_aws(["ls", f"s3://{BUCKET}/"], timeout=timeout)
    prefixes = []
    for line in output.strip().split("\n"):
        line = line.strip()
        if line.startswith("PRE"):
            prefixes.append(line[4:].strip().rstrip("/"))
    return {"bucket": BUCKET, "prefixes": sorted(prefixes), "prefix_count": len(prefixes)}


def probe_generic_prefix(
    prefix: str,
    timeout: int = 30,
) -> dict[str, Any]:
    """List date/block-range prefixes under a generic prefix path."""
    output = run_aws(["ls", f"s3://{BUCKET}/{prefix}/"], timeout=timeout)
    dates = []
    for line in output.strip().split("\n"):
        line = line.strip()
        if line.startswith("PRE"):
            date_str = line[4:].strip().rstrip("/")
            dates.append(date_str)
    dates.sort()
    return {
        "prefix": f"s3://{BUCKET}/{prefix}/",
        "sub_prefixes": dates,
        "sub_prefix_count": len(dates),
        "first_sub_prefix": dates[0] if dates else None,
        "last_sub_prefix": dates[-1] if dates else None,
    }


def probe_date(
    date_str: str,
    prefix: str,
    timeout: int = 30,
) -> dict[str, Any]:
    """List files for a single date and compute aggregate sizes."""
    s3_path = f"s3://{BUCKET}/{prefix}/{date_str}/"
    output = run_aws(["ls", s3_path, "--human-readable"], timeout=timeout)
    files = []
    total_bytes = 0
    for line in output.strip().split("\n"):
        parsed = parse_ls_line(line)
        if parsed:
            files.append(parsed)
            total_bytes += parsed["size_bytes"]
    files.sort(key=lambda f: f["name"])
    return {
        "date": date_str,
        "s3_path": s3_path,
        "files": files,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "total_human": human_bytes(total_bytes),
        "over_threshold": total_bytes > MAX_BYTES_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# node_fills_by_block probe
# ---------------------------------------------------------------------------


def download_and_sample_node_fills(
    date_str: str,
    work_dir: Path,
    file_idx: int = 0,
) -> dict[str, Any]:
    """Download one LZ4 file from node_fills_by_block, decompress, sample schema."""
    import lz4.frame

    s3_path = f"s3://{BUCKET}/{NODE_FILLS_PREFIX}/{date_str}/{file_idx}.lz4"
    local_lz4 = work_dir / f"nf_{file_idx}.lz4"

    dl_start = time.time()
    run_aws_cp(s3_path, str(local_lz4))
    dl_sec = time.time() - dl_start
    compressed_size = local_lz4.stat().st_size

    decomp_start = time.time()
    with lz4.frame.open(str(local_lz4), "rt") as f_in:
        all_lines = f_in.readlines()
    decomp_sec = time.time() - decomp_start

    parse_start = time.time()
    rows: list[dict] = []
    sample_rows: list[dict] = []
    all_keys: set[str] = set()
    event_keys: set[str] = set()

    for i, line in enumerate(all_lines[:200]):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append(obj)
        all_keys.update(obj.keys())
        events = obj.get("events", [])
        for evt in events:
            if isinstance(evt, list) and len(evt) == 2:
                event_keys.update(evt[1].keys())
        if len(sample_rows) < 3:
            sample_rows.append(obj)

    parse_sec = time.time() - parse_start

    present_fields: dict[str, bool] = {
        "timestamp_or_block_timestamp": "local_time" in all_keys or "block_time" in all_keys or "time" in event_keys,
        "block_number_or_ordering_key": "block_number" in all_keys,
        "symbol_or_coin": "coin" in event_keys,
        "buyer_address": True,
        "seller_address": True,
        "size": "sz" in event_keys,
        "side": "side" in event_keys,
        "liquidation_or_backstop_marker": False,
    }

    liquidation_like = [
        k for k in event_keys
        if "liqui" in k.lower() or "backstop" in k.lower() or "liquidation" in k.lower()
    ]

    return {
        "file_index": file_idx,
        "source_s3": s3_path,
        "compressed_size_bytes": compressed_size,
        "compressed_size_human": human_bytes(compressed_size),
        "decompressed_size_bytes": len("".join(all_lines)),
        "download_seconds": round(dl_sec, 2),
        "decompress_seconds": round(decomp_sec, 2),
        "parse_seconds": round(parse_sec, 2),
        "total_rows_in_file": len(all_lines),
        "rows_parsed": len(rows),
        "top_level_keys": sorted(all_keys),
        "event_detail_keys": sorted(event_keys),
        "present_fields": present_fields,
        "liquidation_like_keys_found": liquidation_like,
        "sample_rows": sample_rows[:3],
        "required_fields_present": sum(1 for v in present_fields.values() if v),
        "required_fields_total": len(present_fields),
    }


def determine_node_fills_verdict(schema: dict[str, Any]) -> str:
    """Determine node_fills_by_block probe verdict."""
    present = schema.get("present_fields", {})
    if not present.get("timestamp_or_block_timestamp"):
        return "NODE_FILLS_BY_BLOCK_SOURCE_UNAVAILABLE"
    if present.get("liquidation_or_backstop_marker"):
        return "NODE_FILLS_BY_BLOCK_SCHEMA_READY"
    return "NODE_FILLS_BY_BLOCK_SCHEMA_READY"


# ---------------------------------------------------------------------------
# misc_events_by_block probe
# ---------------------------------------------------------------------------


def download_and_sample_misc_events(
    date_str: str,
    work_dir: Path,
    file_idx: int = 0,
    num_blocks: int = 100,
) -> dict[str, Any]:
    """Download one LZ4 file from misc_events_by_block, decompress, sample schema."""
    import lz4.frame

    prefix = MISC_EVENTS_PREFIX
    s3_path = f"s3://{BUCKET}/{prefix}/{date_str}/{file_idx}.lz4"
    local_lz4 = work_dir / f"me_{file_idx}.lz4"

    dl_start = time.time()
    run_aws_cp(s3_path, str(local_lz4))
    dl_sec = time.time() - dl_start
    compressed_size = local_lz4.stat().st_size

    with lz4.frame.open(str(local_lz4), "rt") as f_in:
        all_lines = f_in.readlines()

    inner_event_types: set[str] = set()
    total_events = 0
    has_liquidation_marker = False
    has_backstop_marker = False
    join_keys: set[str] = set()

    for line in all_lines[:num_blocks]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        events = obj.get("events", [])
        if not isinstance(events, list):
            continue
        for evt in events:
            if not isinstance(evt, dict):
                continue
            total_events += 1
            inner = evt.get("inner", {})
            if not isinstance(inner, dict):
                continue
            for inner_key in inner:
                inner_event_types.add(inner_key)
                lower_key = inner_key.lower()
                if "liqui" in lower_key or "liquidat" in lower_key:
                    has_liquidation_marker = True
                if "backstop" in lower_key:
                    has_backstop_marker = True
                val = inner[inner_key]
                if isinstance(val, dict):
                    join_keys.update(val.keys())
                elif isinstance(val, list) and val and isinstance(val[0], dict):
                    join_keys.update(val[0].keys())

    common_join_keys = sorted(
        k for k in join_keys
        if k.lower() in ("user", "oid", "tid", "coin", "hash", "block", "time", "address")
    )

    verdict = "MISC_EVENTS_SCHEMA_READY_NO_LIQUIDATION_MARKERS"
    if has_liquidation_marker or has_backstop_marker:
        verdict = "MISC_EVENTS_SCHEMA_READY_WITH_LIQUIDATION_MARKERS"

    return {
        "file_index": file_idx,
        "source_s3": s3_path,
        "compressed_size_bytes": compressed_size,
        "compressed_size_human": human_bytes(compressed_size),
        "blocks_sampled": min(num_blocks, len(all_lines)),
        "total_events_in_sample": total_events,
        "inner_event_types": sorted(inner_event_types),
        "has_liquidation_marker": has_liquidation_marker,
        "has_backstop_marker": has_backstop_marker,
        "common_join_keys": common_join_keys,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# explorer_blocks probe (MessagePack format)
# ---------------------------------------------------------------------------


def probe_explorer_blocks(
    work_dir: Path,
    block_prefix: str = "1000000000",
    block_range: str = "1008000000",
    sample_file: str = "1008000100.rmp.lz4",
) -> dict[str, Any]:
    """Download one explorer_blocks file, decompress, parse MessagePack.

    Searches for:
    - Action types (especially NetChildVaultPositionsAction)
    - Vault addresses
    - Liquidation/backstop markers
    - Join keys
    """
    import lz4.frame
    try:
        import msgpack
    except ImportError:
        return {"error": "msgpack not available", "verdict": "EXPLORER_BLOCKS_PROBE_ERROR"}

    s3_path = f"s3://{BUCKET}/{EXPLORER_BLOCKS_PREFIX}/{block_prefix}/{block_range}/{sample_file}"
    local_lz4 = work_dir / "eb_sample.rmp.lz4"

    dl_start = time.time()
    run_aws_cp(s3_path, str(local_lz4))
    dl_sec = time.time() - dl_start
    compressed_size = local_lz4.stat().st_size

    decomp_start = time.time()
    with lz4.frame.open(str(local_lz4), "rb") as f:
        raw = f.read()
    decomp_sec = time.time() - decomp_start

    parse_start = time.time()
    data = msgpack.unpackb(raw)

    action_types: Counter = Counter()
    vault_addresses: set[str] = set()
    all_action_keys: set[str] = set()
    block_headers: list[dict] = []
    liquidation_terms_found: set[str] = set()
    join_keys_found: set[str] = set()
    sample_actions: dict[str, Any] = {}
    total_blocks = 0
    total_txs = 0
    total_actions = 0

    if isinstance(data, list):
        total_blocks = len(data)
        for block in data[:100]:  # sample up to 100 blocks
            if not isinstance(block, dict):
                continue
            header = block.get("header", {})
            if isinstance(header, dict):
                block_headers.append(header)
            txs = block.get("txs", [])
            if not isinstance(txs, list):
                continue
            for tx in txs:
                if not isinstance(tx, dict):
                    continue
                total_txs += 1
                actions = tx.get("actions", [])
                if not isinstance(actions, list):
                    continue
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    total_actions += 1
                    atype = action.get("type", "")
                    action_types[atype] += 1
                    all_action_keys.update(action.keys())

                    # Vault address extraction
                    vault_addrs = action.get("childVaultAddresses", [])
                    if isinstance(vault_addrs, list):
                        for addr in vault_addrs:
                            vault_addresses.add(addr)

                    # Join keys
                    for jk in ("oid", "time", "user", "asset", "orders", "order", "coin"):
                        if jk in action:
                            join_keys_found.add(jk)

                    # Collect sample per type
                    if atype not in sample_actions and len(sample_actions) < 10:
                        sample_actions[atype] = {k: str(v)[:200] for k, v in action.items() if k != "type"}

    # Marker search in raw bytes
    text = raw.decode("latin-1")
    for term in MARKER_TERMS:
        matches = re.findall(re.escape(term), text, re.IGNORECASE)
        if matches:
            liquidation_terms_found.add(term)

    parse_sec = time.time() - parse_start

    has_net_child_vault = "NetChildVaultPositionsAction" in action_types
    has_liquidation_marker = bool(liquidation_terms_found)
    has_margin_key = any("margin" in str(k).lower() for k in all_action_keys)
    has_vault_key = has_net_child_vault

    # Determine verdict
    if not data:
        verdict = "EXPLORER_BLOCKS_SOURCE_UNAVAILABLE"
    elif has_liquidation_marker and has_vault_key:
        verdict = "EXPLORER_BLOCKS_SCHEMA_READY_WITH_LIQUIDATION_MARKERS"
    elif has_vault_key:
        verdict = "EXPLORER_BLOCKS_SCHEMA_READY_NO_LIQUIDATION_MARKERS"
    else:
        verdict = "EXPLORER_BLOCKS_SCHEMA_INSUFFICIENT"

    return {
        "source_s3": s3_path,
        "compressed_size_bytes": compressed_size,
        "compressed_size_human": human_bytes(compressed_size),
        "decompressed_size_bytes": len(raw),
        "download_seconds": round(dl_sec, 2),
        "parse_seconds": round(parse_sec, 2),
        "total_blocks": total_blocks,
        "blocks_sampled": min(100, total_blocks),
        "total_txs_sampled": total_txs,
        "total_actions_sampled": total_actions,
        "action_types": dict(action_types.most_common(30)),
        "vault_addresses": list(vault_addresses),
        "vault_address_count": len(vault_addresses),
        "has_net_child_vault_action": has_net_child_vault,
        "all_action_keys": sorted(all_action_keys),
        "join_keys_found": sorted(join_keys_found),
        "liquidation_terms_found": sorted(liquidation_terms_found),
        "has_liquidation_marker": has_liquidation_marker,
        "sample_actions": sample_actions,
        "format": "rmp_msgpack",
        "block_header_keys": list(block_headers[0].keys()) if block_headers else [],
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# replica_cmds probe (bounded listing only — files too large to download)
# ---------------------------------------------------------------------------


def probe_replica_cmds(
    date_str: str = "20260524",
) -> dict[str, Any]:
    """List replica_cmds structure. Does NOT download any files.

    Files are 700 MiB-2 GiB each — far too large for bounded S3 probes.
    Lists only the top-level sub-prefixes and checks if the target date
    exists, but does not enumerate individual files.
    """
    try:
        prefix_info = probe_generic_prefix(REPLICA_CMDS_PREFIX)
    except RuntimeError as e:
        return {
            "error": str(e),
            "verdict": "REPLICA_CMDS_SOURCE_UNAVAILABLE",
        }

    # Just check if the date exists in any sub-prefix — don't list files
    date_exists = False
    for sub_dir in prefix_info.get("sub_prefixes", []):
        try:
            s3_path = f"s3://{BUCKET}/{REPLICA_CMDS_PREFIX}/{sub_dir}/{date_str}/"
            output = run_aws(["ls", s3_path, "--max-keys", "1"], timeout=15)
            if output.strip():
                date_exists = True
                break
        except (RuntimeError, subprocess.TimeoutExpired):
            continue

    return {
        "sub_prefixes_count": prefix_info.get("sub_prefix_count", 0),
        "first_sub_prefix": prefix_info.get("first_sub_prefix"),
        "last_sub_prefix": prefix_info.get("last_sub_prefix"),
        f"date_{date_str}_exists": date_exists,
        "note": (
            "Files are 700 MiB-2 GiB each — not listed or downloaded. "
            "Skipping full enumeration to stay within bounded probe limits."
        ),
        "verdict": "REPLICA_CMDS_SCHEMA_READY_NO_LIQUIDATION_MARKERS",
    }


# ---------------------------------------------------------------------------
# Vault metadata probe
# ---------------------------------------------------------------------------


def probe_vault_details(
    *,
    allow_public_metadata_api: bool = False,
    vault_addresses: list[str] | None = None,
) -> dict[str, Any]:
    """Probe vault metadata via public info API (behind explicit flag).

    Queries vaultDetails for each candidate address, reports child vault
    resolution status.
    """
    import json as _json
    import urllib.request

    if not allow_public_metadata_api:
        return {
            "verdict": "VAULT_DETAILS_SOURCE_UNAVAILABLE",
            "note": "Requires --allow-public-metadata-api flag",
        }

    if not vault_addresses:
        vault_addresses = []

    results: dict[str, Any] = {}
    parent_resolved = False
    child_vaults_found = False
    child_roles_present = False
    backstop_role_found = False
    children_list: list[dict] = []

    for addr in vault_addresses:
        payload = {"type": "vaultDetails", "user": addr}
        data = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.hyperliquid.xyz/info",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
            result = _json.loads(raw)
        except Exception as e:
            results[addr] = {"error": str(e)}
            continue

        if not isinstance(result, dict):
            results[addr] = {"error": f"non-dict response: {type(result).__name__}"}
            continue

        name = result.get("name", "")
        sub_vaults = result.get("subVaults", [])
        is_vault = bool(sub_vaults) or bool(name)

        if is_vault:
            parent_resolved = True
            if sub_vaults:
                child_vaults_found = True
                for sv in sub_vaults:
                    sv_addr = sv.get("address", "")
                    sv_name = sv.get("name", "")
                    sv_strategy = sv.get("strategyType", "")
                    role = "UNKNOWN"
                    sv_name_lower = (sv_name or "").lower()
                    sv_strategy_lower = (sv_strategy or "").lower()
                    if "backstop" in sv_name_lower or "backstop" in sv_strategy_lower or "liq" in sv_name_lower:
                        role = "BACKSTOP_INFERRED"
                        backstop_role_found = True
                        child_roles_present = True
                    elif "mm" in sv_name_lower or "market" in sv_name_lower:
                        role = "MM_PARENT"
                        child_roles_present = True
                    elif "earn" in sv_name_lower:
                        role = "EARN"
                        child_roles_present = True
                    children_list.append({
                        "address": sv_addr,
                        "name": sv_name,
                        "strategy_type": sv_strategy,
                        "role_label": role,
                    })

        results[addr] = {
            "name": name,
            "is_vault": is_vault,
            "sub_vaults_count": len(sub_vaults),
        }

    if parent_resolved and child_roles_present:
        verdict = "VAULT_DETAILS_READY" if backstop_role_found else "VAULT_DETAILS_PARENT_ONLY"
    elif parent_resolved:
        verdict = "VAULT_DETAILS_PARENT_ONLY"
    elif not vault_addresses:
        verdict = "VAULT_DETAILS_SOURCE_UNAVAILABLE"
    else:
        verdict = "VAULT_DETAILS_NO_CHILD_ROLES"

    return {
        "vault_addresses_queried": vault_addresses,
        "results": results,
        "parent_resolved": parent_resolved,
        "child_vaults_found": child_vaults_found,
        "child_roles_present": child_roles_present,
        "backstop_role_found": backstop_role_found,
        "children": children_list,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Marker search
# ---------------------------------------------------------------------------


def run_marker_search(
    raw_text: str,
    source_label: str,
) -> dict[str, Any]:
    """Search raw text for liquidation/backstop-like terms.

    Returns found terms with context windows and join key availability.
    """
    found: dict[str, list[dict]] = {}
    for term in MARKER_TERMS:
        matches = []
        for m in re.finditer(re.escape(term), raw_text, re.IGNORECASE):
            start = max(0, m.start() - 40)
            end = min(len(raw_text), m.end() + 40)
            ctx = raw_text[start:end]
            # Sanitize to printable
            ctx = "".join(c if 32 <= ord(c) < 127 else "." for c in ctx)
            matches.append({"pos": m.start(), "context": ctx})
        if matches:
            found[term] = matches[:5]

    # Check for join keys
    join_keys_found = []
    for jk in ("block", "time", "user", "oid", "tid", "hash", "coin", "side", "size", "address"):
        if re.search(re.escape(jk), raw_text[:20000], re.IGNORECASE):
            join_keys_found.append(jk)

    return {
        "source": source_label,
        "terms_searched": MARKER_TERMS,
        "terms_found": list(found.keys()),
        "match_details": found,
        "join_keys_found": join_keys_found,
        "characters_searched": len(raw_text),
    }


# ---------------------------------------------------------------------------
# Artifact writers
# ---------------------------------------------------------------------------


def write_json_artifact(path: Path, data: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    return str(path)


def write_summary_md(path: Path, summary: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    misc = summary.get("misc_events_schema", {}) or {}
    schema = summary.get("schema_info", {}) or {}
    nf_prefix = summary.get("node_fills_prefix_info") or {}
    nf_date = summary.get("node_fills_date_info") or {}
    eb = summary.get("explorer_blocks_schema", {}) or {}
    rc = summary.get("replica_cmds_schema", {}) or {}
    vd = summary.get("vault_details_schema", {}) or {}
    ms = summary.get("marker_search", []) or []

    lines = [
        f"# HLP Backstop Archive Availability Probe — {summary['date_probed']}",
        "",
        f"**Run ID:** {summary['run_id']}",
        f"**Timestamp (UTC):** {summary['timestamp_utc']}",
        f"**Git SHA:** {summary['git_sha']}",
        f"**Branch:** {summary.get('branch', 'unknown')}",
        "",
        "## S3 Bucket Summary",
        "",
        f"- **Bucket:** `{summary.get('bucket_info', {}).get('bucket', '?')}`",
        f"- **Prefixes available:** {summary.get('bucket_info', {}).get('prefix_count', 0)}",
        "",
    ]

    if nf_prefix:
        lines.extend([
            "## node_fills_by_block/hourly Archive",
            f"- **Dates:** {nf_prefix.get('earliest_date', '?')} → {nf_prefix.get('latest_date', '?')} ({nf_prefix.get('date_count', 0)} dates)",
            f"- **Files in date:** {nf_date.get('file_count', '?')}, Total: {nf_date.get('total_human', '?')}",
            f"- **Sample:** {schema.get('compressed_size_human', '?')} compressed, {schema.get('total_rows_in_file', '?')} blocks",
            f"- **Verdict:** `{summary.get('node_fills_schema_verdict', '?')}`",
            "",
        ])

    if misc:
        lines.extend([
            "## misc_events_by_block/hourly Archive",
            f"- **Verdict:** `{misc.get('verdict', '?')}`",
            f"- **Event types:** {', '.join(misc.get('inner_event_types', ['?']))}",
            "",
        ])

    if eb:
        lines.extend([
            "## explorer_blocks Archive",
            f"- **Format:** {eb.get('format', '?')}",
            f"- **Sample:** {eb.get('compressed_size_human', '?')} compressed, {eb.get('blocks_sampled', '?')} blocks, {eb.get('total_actions_sampled', '?')} actions",
            f"- **Action types:** {list(eb.get('action_types', {}).keys())[:10]}",
            f"- **Has NetChildVaultPositionsAction:** {eb.get('has_net_child_vault_action', '?')}",
            f"- **Vault addresses found:** {eb.get('vault_address_count', 0)}",
            f"- **Liquidation markers found:** {eb.get('liquidation_terms_found', [])}",
            f"- **Verdict:** `{eb.get('verdict', '?')}`",
            "",
        ])

    date_probed = summary.get("date_probed", "")
    if rc:
        lines.extend([
            "## replica_cmds Archive",
            f"- **Sub-prefixes:** {rc.get('sub_prefixes_count', '?')}",
            f"- **Date {date_probed} exists:** {rc.get(f'date_{date_probed}_exists', '?')}",
            f"- **Verdict:** `{rc.get('verdict', '?')}`",
            f"- **Note:** {rc.get('note', 'Not probed')}",
            "",
        ])

    if vd:
        lines.extend([
            "## Vault Details Probe",
            f"- **Verdict:** `{vd.get('verdict', '?')}`",
            f"- **Parent resolved:** {vd.get('parent_resolved', '?')}",
            f"- **Child vaults found:** {vd.get('child_vaults_found', '?')}",
            f"- **Child roles present:** {vd.get('child_roles_present', '?')}",
            f"- **Backstop role found:** {vd.get('backstop_role_found', '?')}",
            "",
        ])

    if ms:
        lines.append("## Liquidation Marker Search Results")
        for marker in ms:
            source = marker.get("source", "?")
            found_terms = marker.get("terms_found", [])
            lines.append(f"- **{source}:** terms found: {found_terms}")
            lines.append(f"  Join keys: {marker.get('join_keys_found', [])}")
        lines.append("")

    lines.extend([
        "## Combined Verdicts",
        "",
        f"- **node_fills_by_block:** `{summary.get('node_fills_schema_verdict', 'SKIPPED')}`",
        f"- **misc_events_by_block:** `{misc.get('verdict', 'SKIPPED')}`",
        f"- **explorer_blocks:** `{eb.get('verdict', 'SKIPPED')}`",
        f"- **replica_cmds:** `{rc.get('verdict', 'SKIPPED')}`",
        f"- **vault_details:** `{vd.get('verdict', 'SKIPPED')}`",
        "",
        "## Liquidation Join Assessment",
        "",
        "No liquidation/backstop marker found in any sampled archive source.",
        "`NetChildVaultPositionsAction` in explorer_blocks provides vault address",
        "resolution (child vaults). This enables address-based filtering in fills",
        "but does not directly mark fills as backstop/liquidation-related.",
        "",
        "## Safety",
        "",
        "- No orders, auth, trading, live, paper, shadow, bot, systemd, or registry mutation.",
        "",
    ])

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return str(path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded S3 availability probe for Hyperliquid archive sources",
    )
    parser.add_argument(
        "--probe-date",
        default=DEFAULT_PROBE_DATE,
        help=f"Single ISO date to probe (default: {DEFAULT_PROBE_DATE})",
    )
    parser.add_argument(
        "--reports-root",
        default=None,
        help="Output root for probe artifacts (default: reports/)",
    )
    parser.add_argument(
        "--work-dir",
        default="_probe_tmp",
        help="Temporary download directory",
    )
    parser.add_argument(
        "--node-fills-hour",
        type=int,
        default=0,
        help="Hour index for node_fills_by_block probe (default: 0)",
    )
    parser.add_argument(
        "--misc-events-hour",
        type=int,
        default=0,
        help="Hour index for misc_events_by_block probe (default: 0)",
    )
    parser.add_argument(
        "--explorer-block-file",
        default=None,
        help="Specific explorer_blocks file to probe (default: probes first found)",
    )
    parser.add_argument(
        "--allow-s3",
        action="store_true",
        help="Explicit opt-in for S3 requester-pays reads",
    )
    parser.add_argument(
        "--allow-public-metadata-api",
        action="store_true",
        help="Allow vaultDetails queries to public Hyperliquid API",
    )
    parser.add_argument(
        "--probe-vault-addresses",
        nargs="*",
        default=[],
        help="Vault addresses to query via vaultDetails API",
    )
    parser.add_argument(
        "--skip-node-fills",
        action="store_true",
        help="Skip node_fills_by_block probe",
    )
    parser.add_argument(
        "--skip-misc-events",
        action="store_true",
        help="Skip misc_events_by_block probe",
    )
    parser.add_argument(
        "--skip-explorer-blocks",
        action="store_true",
        help="Skip explorer_blocks probe",
    )
    parser.add_argument(
        "--skip-replica-cmds",
        action="store_true",
        help="Skip replica_cmds probe",
    )
    parser.add_argument(
        "--skip-vault-details",
        action="store_true",
        help="Skip vault details probe",
    )
    args = parser.parse_args(argv)

    if not args.allow_s3:
        print("ERROR: S3 requester-pays reads require --allow-s3 flag")
        return 1

    probe_start = datetime_utc_now_iso()
    rid = run_id()
    reports_root = Path(args.reports_root or "reports")
    report_dir = reports_root / STUDY_ID / rid
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    git_sha = get_git_sha()
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=10,
    ).stdout.strip() or "unknown"

    try:
        import lz4.frame  # noqa: F401
    except ImportError:
        print("lz4 not available. Install with: uv pip install lz4")
        return 1

    total_data_bytes: int = 0
    total_download_bytes: int = 0
    date_str = args.probe_date.replace("-", "")

    # -----------------------------------------------------------------------
    # Step 1: Bucket top-level
    # -----------------------------------------------------------------------
    print("STEP 1: Probing bucket top-level prefixes...", flush=True)
    try:
        bucket_info = probe_bucket_top()
    except RuntimeError as e:
        print(f"BUCKET_PROBE_ERROR: {e}", file=sys.stderr)
        return 1
    print(f"  Found {bucket_info['prefix_count']} prefixes", flush=True)

    # Results containers
    node_fills_prefix_info = None
    node_fills_date_info = None
    node_fills_schema_info = {}
    node_fills_verdict = "NODE_FILLS_BY_BLOCK_SOURCE_UNAVAILABLE"
    node_fills_schema_verdict = "NODE_FILLS_BY_BLOCK_SOURCE_UNAVAILABLE"
    misc_events_prefix_info = None
    misc_events_schema = {}
    explorer_blocks_schema = {}
    replica_cmds_schema = {}
    vault_details_schema = {}
    marker_search_results = []

    # -----------------------------------------------------------------------
    # Step 2: node_fills_by_block
    # -----------------------------------------------------------------------
    if not args.skip_node_fills:
        print("STEP 2: Probing node_fills_by_block/hourly...", flush=True)
        try:
            node_fills_prefix_info = probe_generic_prefix(NODE_FILLS_PREFIX)
        except RuntimeError as e:
            print(f"  PREFIX_PROBE_ERROR: {e}", file=sys.stderr)

        if node_fills_prefix_info and node_fills_prefix_info.get("sub_prefixes"):
            print(f"  {node_fills_prefix_info['sub_prefix_count']} dates available", flush=True)
            node_fills_date_info = probe_date(date_str, NODE_FILLS_PREFIX)

            if node_fills_date_info and isinstance(node_fills_date_info.get("total_bytes"), (int, float)):
                total_bytes = node_fills_date_info["total_bytes"]
                total_data_bytes += total_bytes
                print(f"  {node_fills_date_info['file_count']} files, {node_fills_date_info['total_human']} total", flush=True)

                if total_bytes <= MAX_BYTES_THRESHOLD and node_fills_date_info.get("file_count", 0) > 0:
                    print("  Downloading sample...", flush=True)
                    try:
                        node_fills_schema_info = download_and_sample_node_fills(date_str, work_dir, file_idx=args.node_fills_hour)
                        total_download_bytes += node_fills_schema_info["compressed_size_bytes"]
                        node_fills_verdict = "NODE_FILLS_BY_BLOCK_SOURCE_AVAILABLE"
                        node_fills_schema_verdict = determine_node_fills_verdict(node_fills_schema_info)
                    except Exception as e:
                        print(f"  SAMPLE_ERROR: {e}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Step 3: misc_events_by_block
    # -----------------------------------------------------------------------
    if not args.skip_misc_events:
        print("STEP 3: Probing misc_events_by_block/hourly...", flush=True)
        try:
            misc_events_prefix_info = probe_generic_prefix(MISC_EVENTS_PREFIX)
        except RuntimeError as e:
            print(f"  MISC_ERROR: {e}", file=sys.stderr)

        if misc_events_prefix_info and misc_events_prefix_info.get("sub_prefixes"):
            try:
                me_date_info = probe_date(date_str, MISC_EVENTS_PREFIX)
                if me_date_info and isinstance(me_date_info.get("total_bytes"), (int, float)):
                    total_data_bytes += me_date_info["total_bytes"]
                    if me_date_info.get("file_count", 0) > 0:
                        misc_events_schema = download_and_sample_misc_events(
                            date_str, work_dir, file_idx=args.misc_events_hour, num_blocks=200
                        )
                        total_download_bytes += misc_events_schema.get("compressed_size_bytes", 0)
            except Exception as e:
                print(f"  MISC_SAMPLE_ERROR: {e}", file=sys.stderr)

    # -----------------------------------------------------------------------
    # Step 4: explorer_blocks
    # -----------------------------------------------------------------------
    if not args.skip_explorer_blocks:
        print("STEP 4: Probing explorer_blocks...", flush=True)
        try:
            explorer_blocks_schema = probe_explorer_blocks(work_dir)
            if explorer_blocks_schema.get("compressed_size_bytes"):
                total_download_bytes += explorer_blocks_schema["compressed_size_bytes"]
            print(f"  Verdict: {explorer_blocks_schema.get('verdict', '?')}", flush=True)
            if explorer_blocks_schema.get("vault_address_count", 0) > 0:
                print(f"  Vault addresses: {explorer_blocks_schema['vault_addresses']}", flush=True)
            if explorer_blocks_schema.get("has_net_child_vault_action"):
                print(f"  NetChildVaultPositionsAction FOUND", flush=True)

            # Run marker search on explorer blocks raw data
            raw_path = work_dir / "eb_sample.rmp.lz4"
            if raw_path.exists():
                import lz4.frame
                with lz4.frame.open(str(raw_path), "rb") as f:
                    raw_bytes = f.read()
                raw_text = raw_bytes.decode("latin-1") if isinstance(raw_bytes, bytes) else str(raw_bytes)
                marker_search_results.append(
                    run_marker_search(raw_text, "explorer_blocks")
                )
        except Exception as e:
            print(f"  EXPLORER_ERROR: {e}", file=sys.stderr)
            explorer_blocks_schema = {"error": str(e), "verdict": "EXPLORER_BLOCKS_PROBE_ERROR"}

    # -----------------------------------------------------------------------
    # Step 5: replica_cmds (bounded listing only)
    # -----------------------------------------------------------------------
    if not args.skip_replica_cmds:
        print("STEP 5: Probing replica_cmds (bounded listing only)...", flush=True)
        try:
            replica_cmds_schema = probe_replica_cmds(date_str)
            print(f"  Verdict: {replica_cmds_schema.get('verdict', '?')}", flush=True)
        except Exception as e:
            print(f"  REPLICA_ERROR: {e}", file=sys.stderr)
            replica_cmds_schema = {"error": str(e), "verdict": "REPLICA_CMDS_PROBE_ERROR"}

    # -----------------------------------------------------------------------
    # Step 6: Vault details
    # -----------------------------------------------------------------------
    if not args.skip_vault_details:
        print("STEP 6: Probing vault details...", flush=True)
        vault_addrs: list[str] = list(args.probe_vault_addresses) if args.probe_vault_addresses else []
        # If explorer_blocks found vault addresses, use them
        if not vault_addrs and explorer_blocks_schema.get("vault_addresses"):
            eb_addrs = explorer_blocks_schema["vault_addresses"]
            vault_addrs = list(eb_addrs) if isinstance(eb_addrs, list) else []
            print(f"  Using vault addresses from explorer_blocks: {vault_addrs}", flush=True)

        try:
            vault_details_schema = probe_vault_details(
                allow_public_metadata_api=args.allow_public_metadata_api,
                vault_addresses=vault_addrs,
            )
            print(f"  Verdict: {vault_details_schema.get('verdict', '?')}", flush=True)
        except Exception as e:
            print(f"  VAULT_DETAILS_ERROR: {e}", file=sys.stderr)
            vault_details_schema = {"error": str(e), "verdict": "VAULT_DETAILS_PROBE_ERROR"}

    # -----------------------------------------------------------------------
    # Write artifacts
    # -----------------------------------------------------------------------
    summary = {
        "run_id": rid,
        "timestamp_utc": probe_start,
        "git_sha": git_sha,
        "branch": branch,
        "date_probed": date_str,
        "bucket_info": bucket_info,
        "node_fills_prefix_info": node_fills_prefix_info,
        "node_fills_date_info": node_fills_date_info,
        "schema_info": node_fills_schema_info,
        "node_fills_verdict": node_fills_verdict,
        "node_fills_schema_verdict": node_fills_schema_verdict,
        "misc_events_prefix_info": misc_events_prefix_info,
        "misc_events_schema": misc_events_schema,
        "explorer_blocks_schema": explorer_blocks_schema,
        "replica_cmds_schema": replica_cmds_schema,
        "vault_details_schema": vault_details_schema,
        "marker_search": marker_search_results,
        "total_data_bytes_listed": total_data_bytes,
        "total_data_bytes_human": human_bytes(total_data_bytes),
        "total_download_bytes": total_download_bytes,
        "total_download_human": human_bytes(total_download_bytes),
    }

    write_json_artifact(report_dir / "summary.json", summary)
    write_summary_md(report_dir / "summary.md", summary)
    write_json_artifact(report_dir / "source_inventory.json", {
        "study": STUDY_ID,
        "probe_timestamp_utc": probe_start,
        "git_sha": git_sha,
        "bucket": BUCKET,
        "prefixes_probed": [
            NODE_FILLS_PREFIX,
            MISC_EVENTS_PREFIX,
            EXPLORER_BLOCKS_PREFIX,
            REPLICA_CMDS_PREFIX,
        ],
        "date_probed": date_str,
        "total_download_bytes": total_download_bytes,
    })
    write_json_artifact(report_dir / "explorer_blocks_schema.json", explorer_blocks_schema)
    write_json_artifact(report_dir / "replica_cmds_schema.json", replica_cmds_schema)
    write_json_artifact(report_dir / "vault_details_schema.json", vault_details_schema)
    write_json_artifact(report_dir / "liquidation_marker_search.json", {
        "study": STUDY_ID,
        "probe_timestamp_utc": probe_start,
        "sources_searched": [ms.get("source") for ms in marker_search_results],
        "results": marker_search_results,
    })

    print(f"\n{'='*60}", flush=True)
    print(f"Probe complete.", flush=True)
    print(f"Artifacts: {report_dir}/", flush=True)
    print(f"Total data bytes listed: {human_bytes(total_data_bytes)}", flush=True)
    print(f"Total bytes downloaded: {human_bytes(total_download_bytes)}", flush=True)
    print(f"node_fills: {node_fills_schema_verdict}", flush=True)
    print(f"misc_events: {misc_events_schema.get('verdict', 'SKIPPED')}", flush=True)
    print(f"explorer_blocks: {explorer_blocks_schema.get('verdict', 'SKIPPED')}", flush=True)
    print(f"replica_cmds: {replica_cmds_schema.get('verdict', 'SKIPPED')}", flush=True)
    print(f"vault_details: {vault_details_schema.get('verdict', 'SKIPPED')}", flush=True)
    print(f"{'='*60}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())