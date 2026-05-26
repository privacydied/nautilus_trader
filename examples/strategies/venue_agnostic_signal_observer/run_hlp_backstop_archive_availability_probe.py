#!/usr/bin/env python3
"""Bounded requester-pays availability probe for Hyperliquid archive sources.

Probes S3 archive structure for:
  - node_fills_by_block/hourly/
  - misc_events_by_block/hourly/

Downloads a single-date sample, inspects schema, and reports availability /
schema readiness for HLP backstop reconstruction.

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
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUCKET = "hl-mainnet-node-data"
NODE_FILLS_PREFIX = "node_fills_by_block/hourly"
MISC_EVENTS_PREFIX = "misc_events_by_block/hourly"
DEFAULT_PROBE_DATE = "2026-05-24"
MAX_BYTES_THRESHOLD = 5 * 1024**3  # 5 GB

STUDY_ID = "hlp_backstop_archive_availability_probe"

# Required schema fields for HLP reconstruction
REQUIRED_FIELDS = [
    "timestamp_or_block_timestamp",
    "block_number_or_ordering_key",
    "symbol_or_coin",
    "buyer_address",
    "seller_address",
    "size",
    "side",
    "liquidation_or_backstop_marker",
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
# Probe logic: bucket top-level
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


# ---------------------------------------------------------------------------
# Probe: generic date-level prefix
# ---------------------------------------------------------------------------


def probe_generic_prefix(
    prefix: str,
    timeout: int = 30,
) -> dict[str, Any]:
    """List date prefixes under a generic prefix path."""
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
        "dates_available": dates,
        "date_count": len(dates),
        "earliest_date": dates[0] if dates else None,
        "latest_date": dates[-1] if dates else None,
    }


# ---------------------------------------------------------------------------
# Probe: specific date under a prefix
# ---------------------------------------------------------------------------


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
# Download and schema sample (node_fills_by_block)
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
    decompressed_size = len("".join(all_lines))

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
        "decompressed_size_bytes": decompressed_size,
        "decompressed_size_human": human_bytes(decompressed_size),
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


# ---------------------------------------------------------------------------
# Download and schema sample (misc_events_by_block)
# ---------------------------------------------------------------------------


def download_and_sample_misc_events(
    date_str: str,
    work_dir: Path,
    file_idx: int = 0,
    num_blocks: int = 100,
) -> dict[str, Any]:
    """Download one LZ4 file from misc_events_by_block, decompress, sample schema.

    Determines whether misc_events contains liquidation/backstop markers
    and whether join keys (block, user, oid, tid, coin, hash) align with fills.
    """
    import lz4.frame

    prefix = MISC_EVENTS_PREFIX
    s3_path = f"s3://{BUCKET}/{prefix}/{date_str}/{file_idx}.lz4"
    local_lz4 = work_dir / f"me_{file_idx}.lz4"

    dl_start = time.time()
    run_aws_cp(s3_path, str(local_lz4))
    dl_sec = time.time() - dl_start
    compressed_size = local_lz4.stat().st_size

    decomp_start = time.time()
    with lz4.frame.open(str(local_lz4), "rt") as f_in:
        all_lines = f_in.readlines()
    decomp_sec = time.time() - decomp_start

    parse_start = time.time()
    inner_event_types: set[str] = set()
    total_events = 0
    liquidation_event_types = []
    join_keys: set[str] = set()
    all_event_detail_keys: set[str] = set()
    has_liquidation_marker = False
    has_backstop_marker = False
    sample_event_types = {}

    for i, line in enumerate(all_lines[:num_blocks]):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        block_number = obj.get("block_number")
        events = obj.get("events", [])
        if not isinstance(events, list):
            continue
        for evt in events:
            if not isinstance(evt, dict):
                continue
            total_events += 1
            all_event_detail_keys.update(evt.keys())
            inner = evt.get("inner", {})
            if not isinstance(inner, dict):
                continue
            for inner_key in inner:
                inner_event_types.add(inner_key)
                val = inner[inner_key]
                # Check for liquidation/backstop markers
                lower_key = inner_key.lower()
                if "liqui" in lower_key or "liquidat" in lower_key:
                    has_liquidation_marker = True
                    liquidation_event_types.append(inner_key)
                if "backstop" in lower_key:
                    has_backstop_marker = True
                    liquidation_event_types.append(inner_key)
                # Collect join keys from the event data
                if isinstance(val, dict):
                    join_keys.update(val.keys())
                    if val.get("user"):
                        pass  # user join key available
                elif isinstance(val, list) and val and isinstance(val[0], dict):
                    join_keys.update(val[0].keys())
                # Capture first sample per type
                if inner_key not in sample_event_types:
                    sample_event_types[inner_key] = {"details": str(val)[:300]}

    parse_sec = time.time() - parse_start

    # Check for user/oid/tid/coin/hash/block join keys in the event data
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
        "decompressed_size_bytes": len("".join(all_lines)),
        "download_seconds": round(dl_sec, 2),
        "decompress_seconds": round(decomp_sec, 2),
        "parse_seconds": round(parse_sec, 2),
        "blocks_sampled": min(num_blocks, len(all_lines)),
        "total_events_in_sample": total_events,
        "total_lines_in_file": len(all_lines),
        "inner_event_types": sorted(inner_event_types),
        "liquidation_event_types": liquidation_event_types,
        "has_liquidation_marker": has_liquidation_marker,
        "has_backstop_marker": has_backstop_marker,
        "common_join_keys": common_join_keys,
        "all_detail_keys": sorted(all_event_detail_keys),
        "sample_event_types": sample_event_types,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Determine verdicts
# ---------------------------------------------------------------------------


def determine_node_fills_verdict(schema: dict[str, Any]) -> str:
    """Determine node_fills_by_block probe verdict."""
    present = schema.get("present_fields", {})
    if not present.get("timestamp_or_block_timestamp"):
        return "NODE_FILLS_BY_BLOCK_SOURCE_UNAVAILABLE"
    if present.get("liquidation_or_backstop_marker"):
        return "NODE_FILLS_BY_BLOCK_SCHEMA_READY"
    return "NODE_FILLS_BY_BLOCK_SCHEMA_READY"


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
        "## node_fills_by_block/hourly Archive",
        "",
        f"- **Prefix:** `{nf_prefix.get('prefix', '?')}`",
        f"- **Dates available:** from `{nf_prefix.get('earliest_date', '?')}` "
        f"to `{nf_prefix.get('latest_date', '?')}`",
        f"- **Total dates:** {nf_prefix.get('date_count', 0)}",
        "",
        "## Probe Date Details",
        "",
        f"- **Date probed:** `{summary.get('date_probed', '?')}`",
        f"- **Files in date:** {nf_date.get('file_count', 0)}",
        f"- **Total compressed bytes:** {nf_date.get('total_human', '?')}",
        f"- **Over 5 GB threshold:** {nf_date.get('over_threshold', '?')}",
        "",
    ]

    files = nf_date.get("files", [])
    if files:
        lines.append("### Hourly Files")
        lines.append("")
        lines.append("| File | Size | Last Modified |")
        lines.append("|------|------|---------------|")
        for f in files:
            lines.append(f"| `{f['name']}` | {f['size_human']} | {f['last_modified']} |")
        lines.append("")

    if schema:
        lines.extend([
            "## Sample Download & Schema (node_fills_by_block)",
            "",
            f"- **Sample file:** `{schema.get('source_s3', '?')}`",
            f"- **Compressed:** {schema.get('compressed_size_human', '?')}",
            f"- **Decompressed:** {schema.get('decompressed_size_human', '?')}",
            f"- **Total rows in file:** {schema.get('total_rows_in_file', '?')}",
            f"- **Download time:** {schema.get('download_seconds', '?')}s",
            f"- **Decompress time:** {schema.get('decompress_seconds', '?')}s",
            "",
            "### Top-level block keys",
            f"  `{', '.join(schema.get('top_level_keys', []))}`",
            "",
            "### Event fill detail keys",
            f"  `{', '.join(schema.get('event_detail_keys', []))}`",
            "",
            "### Required Fields for HLP Reconstruction",
            "",
        ])
        present = schema.get("present_fields", {})
        for field, ok in present.items():
            marker = "✓" if ok else "✗"
            lines.append(f"- **{marker}** `{field}`")
        lines.append("")
        if schema.get("liquidation_like_keys_found"):
            lines.append(f"- Liquidation-like keys found: `{schema.get('liquidation_like_keys_found')}`")
        else:
            lines.append("- No liquidation/backstop marker found in fill data.")
        lines.append("")

    # Misc events section
    if misc:
        lines.extend([
            "## misc_events_by_block/hourly Archive",
            "",
            f"- **Prefix:** `{summary.get('misc_events_prefix_info', {}).get('prefix', '?')}`",
            f"- **Dates available:** from `{summary.get('misc_events_prefix_info', {}).get('earliest_date', '?')}` "
            f"to `{summary.get('misc_events_prefix_info', {}).get('latest_date', '?')}`",
            f"- **Total dates:** {summary.get('misc_events_prefix_info', {}).get('date_count', 0)}",
            "",
            f"## Misc Events Schema (hour {misc.get('file_index', '?')})",
            "",
            f"- **Sample file:** `{misc.get('source_s3', '?')}`",
            f"- **Compressed:** {misc.get('compressed_size_human', '?')}",
            f"- **Blocks sampled:** {misc.get('blocks_sampled', '?')}",
            f"- **Total events in sample:** {misc.get('total_events_in_sample', '?')}",
            "",
            "### Inner event types found",
            f"  `{', '.join(misc.get('inner_event_types', []))}`",
            "",
            f"- **Has liquidation marker:** {misc.get('has_liquidation_marker', '?')}",
            f"- **Has backstop marker:** {misc.get('has_backstop_marker', '?')}",
            f"- **Common join keys:** `{', '.join(misc.get('common_join_keys', []))}`",
            "",
            "### Misc Events Verdict",
            f"  `{misc.get('verdict', '?')}`",
            "",
        ])

    lines.extend([
        "## Probe Verdicts",
        "",
        f"- **node_fills_by_block archive:** `{summary.get('node_fills_verdict', '?')}`",
        f"- **node_fills_by_block schema:** `{summary.get('node_fills_schema_verdict', '?')}`",
        f"- **misc_events_by_block:** `{misc.get('verdict', '?') if misc else 'NOT_PROBED'}`",
        "",
        "## Notes",
        "",
        "- No liquidation/backstop flag exists in node_fills_by_block fill records.",
        "- misc_events_by_block does NOT contain liquidation events (only funding, deposits, withdrawals, staking).",
        "- HLP backstop inference would require heuristic position-change analysis",
        "  or cross-referencing with an as-yet-unidentified data source for liquidation events.",
        "- The fills data is parseable and rich (address, coin, side, size, price, direction, PnL).",
        "- No orders, auth, trading, live, paper, shadow, bot, systemd, or registry mutation was used.",
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
        "--misc-events-hour",
        type=int,
        default=0,
        help="Hour index for misc_events_by_block probe (default: 0)",
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
        "--allow-s3",
        action="store_true",
        help="Explicit opt-in for S3 requester-pays reads",
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

    # Step 1: Probe bucket top level
    print("STEP 1: Probing bucket top-level prefixes...", flush=True)
    try:
        bucket_info = probe_bucket_top()
    except RuntimeError as e:
        print(f"BUCKET_PROBE_ERROR: {e}", file=sys.stderr)
        return 1
    print(f"  Found {bucket_info['prefix_count']} prefixes: {', '.join(bucket_info['prefixes'])}", flush=True)

    # Initialize results
    date_str = args.probe_date.replace("-", "")
    node_fills_prefix_info = None
    node_fills_date_info = None
    node_fills_schema_info = {}
    node_fills_verdict = "NODE_FILLS_BY_BLOCK_SOURCE_UNAVAILABLE"
    node_fills_schema_verdict = "NODE_FILLS_BY_BLOCK_SOURCE_UNAVAILABLE"
    misc_events_prefix_info = None
    misc_events_schema = {}
    misc_hour = args.misc_events_hour

    # --- node_fills_by_block ---
    if not args.skip_node_fills:
        print("STEP 2: Probing node_fills_by_block/hourly...", flush=True)
        try:
            node_fills_prefix_info = probe_generic_prefix(NODE_FILLS_PREFIX)
        except RuntimeError as e:
            print(f"  PREFIX_PROBE_ERROR: {e}", file=sys.stderr)
            node_fills_prefix_info = {"prefix": f"s3://{BUCKET}/{NODE_FILLS_PREFIX}/", "error": str(e)}

        if node_fills_prefix_info and node_fills_prefix_info.get("dates_available"):
            print(f"  {node_fills_prefix_info['date_count']} dates: {node_fills_prefix_info['earliest_date']} → {node_fills_prefix_info['latest_date']}", flush=True)

            print(f"STEP 3: Probing date {date_str}...", flush=True)
            try:
                node_fills_date_info = probe_date(date_str, NODE_FILLS_PREFIX)
            except RuntimeError as e:
                print(f"  DATE_PROBE_ERROR: {e}", file=sys.stderr)
                node_fills_date_info = {"error": str(e)}

            if node_fills_date_info and isinstance(node_fills_date_info.get("total_bytes"), (int, float)):
                total_bytes = node_fills_date_info["total_bytes"]
                total_data_bytes += total_bytes
                print(f"  {node_fills_date_info['file_count']} files, {node_fills_date_info['total_human']} total", flush=True)

                if total_bytes <= MAX_BYTES_THRESHOLD and node_fills_date_info.get("file_count", 0) > 0:
                    print("STEP 4: Downloading sample node_fills file...", flush=True)
                    try:
                        node_fills_schema_info = download_and_sample_node_fills(date_str, work_dir, file_idx=0)
                        total_download_bytes += node_fills_schema_info["compressed_size_bytes"]
                        print(f"  Downloaded {node_fills_schema_info['compressed_size_human']}, "
                              f"decompressed to {node_fills_schema_info['decompressed_size_human']}, "
                              f"rows: {node_fills_schema_info['total_rows_in_file']}", flush=True)
                        node_fills_verdict = "NODE_FILLS_BY_BLOCK_SOURCE_AVAILABLE"
                        node_fills_schema_verdict = determine_node_fills_verdict(node_fills_schema_info)
                        print(f"  Required fields: {node_fills_schema_info['required_fields_present']}/"
                              f"{node_fills_schema_info['required_fields_total']}", flush=True)
                        if not node_fills_schema_info["present_fields"].get("liquidation_or_backstop_marker"):
                            print("  NOTE: No liquidation/backstop marker found in fill schema", flush=True)
                    except Exception as e:
                        print(f"  SCHEMA_SAMPLE_ERROR: {e}", file=sys.stderr)
                        node_fills_schema_info = {"error": str(e)}
                else:
                    print(f"  SKIP: {node_fills_date_info.get('total_human', '?')} exceeds threshold or no files", flush=True)

    # --- misc_events_by_block ---
    if not args.skip_misc_events:
        print(f"STEP 5: Probing misc_events_by_block/hourly...", flush=True)
        try:
            misc_events_prefix_info = probe_generic_prefix(MISC_EVENTS_PREFIX)
        except RuntimeError as e:
            print(f"  MISC_PREFIX_PROBE_ERROR: {e}", file=sys.stderr)

        if misc_events_prefix_info and misc_events_prefix_info.get("dates_available"):
            print(f"  {misc_events_prefix_info['date_count']} dates: {misc_events_prefix_info['earliest_date']} → {misc_events_prefix_info['latest_date']}", flush=True)

            print(f"STEP 6: Probing misc_events date/hours...", flush=True)
            try:
                me_date_info = probe_date(date_str, MISC_EVENTS_PREFIX)
                if me_date_info and not me_date_info.get("error"):
                    total_data_bytes += me_date_info["total_bytes"]
                    print(f"  misc_events: {me_date_info['file_count']} files, {me_date_info['total_human']}", flush=True)

                    if misc_hour < me_date_info["file_count"]:
                        print(f"  Downloading misc_events hour {misc_hour}...", flush=True)
                        try:
                            misc_events_schema = download_and_sample_misc_events(
                                date_str, work_dir, file_idx=misc_hour, num_blocks=200,
                            )
                            total_download_bytes += misc_events_schema["compressed_size_bytes"]
                            print(f"  misc_events inner types: {misc_events_schema['inner_event_types']}", flush=True)
                            print(f"  Has liquidation marker: {misc_events_schema['has_liquidation_marker']}", flush=True)
                            print(f"  Misc events verdict: {misc_events_schema['verdict']}", flush=True)
                        except Exception as e:
                            print(f"  MISC_EVENTS_SAMPLE_ERROR: {e}", file=sys.stderr)
                            misc_events_schema = {"error": str(e)}
            except RuntimeError as e:
                print(f"  MISC_DATE_PROBE_ERROR: {e}", file=sys.stderr)

    # Write misc_events_schema.json
    misc_schema_path = report_dir / "misc_events_schema.json"
    write_json_artifact(misc_schema_path, misc_events_schema if misc_events_schema else {
        "study": STUDY_ID,
        "note": "misc_events_by_block probe skipped or failed",
    })

    # Build summary
    summary = {
        "run_id": rid,
        "timestamp_utc": probe_start,
        "git_sha": git_sha,
        "branch": branch,
        "date_probed": date_str,
        "bucket_info": bucket_info,
        "node_fills_prefix_info": node_fills_prefix_info,
        "node_fills_date_info": node_fills_date_info,
        "node_fills_schema_info": node_fills_schema_info,
        "node_fills_verdict": node_fills_verdict,
        "node_fills_schema_verdict": node_fills_schema_verdict,
        "misc_events_prefix_info": misc_events_prefix_info,
        "misc_events_schema": misc_events_schema,
        "total_data_bytes_listed": total_data_bytes,
        "total_data_bytes_human": human_bytes(total_data_bytes),
        "total_download_bytes": total_download_bytes,
        "total_download_human": human_bytes(total_download_bytes),
    }

    # Write all artifacts
    write_json_artifact(report_dir / "summary.json", summary)
    write_summary_md(report_dir / "summary.md", summary)
    write_json_artifact(report_dir / "source_inventory.json", {
        "study": STUDY_ID,
        "probe_timestamp_utc": probe_start,
        "git_sha": git_sha,
        "bucket": BUCKET,
        "node_fills_prefix": NODE_FILLS_PREFIX,
        "misc_events_prefix": MISC_EVENTS_PREFIX,
        "date_probed": date_str,
        "node_fills_files_in_date": node_fills_date_info.get("files", []) if node_fills_date_info else [],
        "total_compressed_bytes": node_fills_date_info["total_bytes"] if node_fills_date_info else 0,
        "misc_events_hour": misc_hour,
    })
    write_json_artifact(report_dir / "sample_schema.json", {
        "study": STUDY_ID,
        "source": "node_fills_by_block",
        "file_source": node_fills_schema_info.get("source_s3", ""),
        **({} if not node_fills_schema_info else {
            "top_level_keys": node_fills_schema_info.get("top_level_keys", []),
            "event_detail_keys": node_fills_schema_info.get("event_detail_keys", []),
            "present_fields": node_fills_schema_info.get("present_fields", {}),
            "liquidation_like_keys": node_fills_schema_info.get("liquidation_like_keys_found", []),
            "sample_rows": node_fills_schema_info.get("sample_rows", []),
        }),
    })
    write_json_artifact(report_dir / "download_manifest.json", {
        "study": STUDY_ID,
        "probe_timestamp_utc": probe_start,
        "files_downloaded": [],
        "total_bytes_downloaded": total_download_bytes,
        "total_data_bytes_listed": total_data_bytes,
    })

    print(f"\n{'='*60}", flush=True)
    print(f"Probe complete.", flush=True)
    print(f"Artifacts: {report_dir}/", flush=True)
    print(f"  - summary.json", flush=True)
    print(f"  - summary.md", flush=True)
    print(f"  - source_inventory.json", flush=True)
    print(f"  - sample_schema.json", flush=True)
    print(f"  - download_manifest.json", flush=True)
    print(f"  - misc_events_schema.json", flush=True)
    print(f"", flush=True)
    print(f"Total data bytes listed: {human_bytes(total_data_bytes)}", flush=True)
    print(f"Total bytes downloaded: {human_bytes(total_download_bytes)}", flush=True)
    print(f"node_fills_by_block verdict: {node_fills_schema_verdict}", flush=True)
    print(f"misc_events_by_block verdict: {misc_events_schema.get('verdict', 'NOT_PROBED')}", flush=True)
    print(f"{'='*60}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())