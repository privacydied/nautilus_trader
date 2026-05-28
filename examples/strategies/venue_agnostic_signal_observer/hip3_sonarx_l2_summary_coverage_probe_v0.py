"""Hip3 SonarX L2 Summary Coverage Probe.

Implements a standalone probe that inspects the public SonarX S3 bucket for
HIP‑3 L2 summary snapshots for a set of DEX {dex}:{coin} markets.

The probe runs in two modes:
* ``--dry-run`` – validates configuration, writes a preview JSON and exits.
* real run – performs S3 queries (list objects and optionally download a few
  sample files), validates schema and produces diagnostic artifacts.

All S3 access is performed via ``boto3`` with ``RequestPayer='requester'`` so
that the public requester‑pays bucket can be read without credentials.

The implementation purposefully avoids any live‑trading, order submission or
private‑key usage. It is safe to run on any machine with network access to the
public bucket.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Constants & status taxonomy
# ---------------------------------------------------------------------------
STATUSES = {
    "SONARX_L2_SUMMARY_READY",
    "SONARX_L2_SUMMARY_BUCKET_ACCESSIBLE",
    "SONARX_L2_SUMMARY_BUCKET_BLOCKED",
    "SONARX_HIP3_MARKET_FOUND",
    "SONARX_HIP3_MARKET_NOT_FOUND",
    "SONARX_HIP3_PARTITIONS_FOUND",
    "SONARX_HIP3_PARTITIONS_EMPTY",
    "SONARX_SAMPLE_FILE_FOUND",
    "SONARX_SAMPLE_FILE_PARSE_OK",
    "SONARX_SAMPLE_FILE_PARSE_FAILED",
    "SONARX_COVERAGE_SUFFICIENT_FOR_PHASE_MINUS1",
    "SONARX_COVERAGE_INSUFFICIENT",
    "SONARX_REQUESTER_PAYS_CREDENTIALS_REQUIRED",
    "SONARX_ACCESS_DENIED",
    "SONARX_TRANSPORT_ERROR",
}

BUCKET_NAME = "sonarx-hyperliquid-public"
BASE_PREFIX = "market_data/hip3/"

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_metadata() -> Mapping[str, Any]:
    """Collect minimal git information for artifact metadata.

    Returns a mapping with ``git_sha``, ``git_dirty`` (bool) and ``branch``.
    """
    def run(cmd: str) -> str:
        import subprocess
        return subprocess.check_output(cmd, shell=True, text=True).strip()

    sha = run("git rev-parse HEAD")
    dirty = run("git diff --quiet || echo dirty") != ""
    branch = run("git rev-parse --abbrev-ref HEAD")
    return {"git_sha": sha, "git_dirty": dirty, "branch": branch}


def make_artifact_path(root: Path, run_id: str, name: str) -> Path:
    return root / run_id / name


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)

# ---------------------------------------------------------------------------
# S3 interaction layer (requester‑pays)
# ---------------------------------------------------------------------------

def s3_client() -> Any:
    cfg = Config(signature_version="s3v4", retries={"max_attempts": 3})
    return boto3.client("s3", config=cfg)


def list_common_prefixes(s3: Any, prefix: str) -> List[str]:
    """List immediate sub‑prefixes under ``prefix`` using ``Delimiter='/'``.

    Returns a list of prefixes (with trailing slash) or an empty list.
    """
    paginator = s3.get_paginator("list_objects_v2")
    result: List[str] = []
    for page in paginator.paginate(
        Bucket=BUCKET_NAME, Prefix=prefix, Delimiter="/", RequestPayer="requester"
    ):
        for cp in page.get("CommonPrefixes", []):
            result.append(cp["Prefix"])
    return result


def market_prefix(market: str) -> str:
    return f"{BASE_PREFIX}{market}/l2-summary-snapshots/"


def try_market_prefixes(s3: Any, market: str) -> Tuple[str, str]:
    """Attempt to locate the market prefix.

    Returns a tuple ``(status, prefix)`` where ``status`` is one of the
    ``STATUSES`` values and ``prefix`` is the successful S3 prefix (or empty).
    The function tries the documented plain path first and falls back to
    URL‑encoded colon, underscore and hyphen variants.
    """
    variants = [market, market.replace(":", "%3A"), market.replace(":", "_"), market.replace(":", "-")]
    for var in variants:
        pref = market_prefix(var)
        try:
            sub = list_common_prefixes(s3, pref)
            if sub:
                return ("SONARX_HIP3_MARKET_FOUND", pref)
        except ClientError as e:
            code = e.response["Error"].get("Code", "")
            if code in {"AccessDenied", "AllAccessDisabled"}:
                return ("SONARX_ACCESS_DENIED", "")
            raise
    return ("SONARX_HIP3_MARKET_NOT_FOUND", "")


def list_partitions(s3: Any, market_pref: str) -> List[str]:
    """List partition prefixes for a given market prefix."""
    return list_common_prefixes(s3, market_pref)


def list_snapshot_files(s3: Any, partition_prefix: str) -> List[str]:
    """List ``.json.gz`` objects inside a partition prefix."""
    paginator = s3.get_paginator("list_objects_v2")
    results: List[str] = []
    for page in paginator.paginate(
        Bucket=BUCKET_NAME, Prefix=partition_prefix, RequestPayer="requester"
    ):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith('.json.gz'):
                results.append(key)
    return results


def download_and_parse(s3: Any, key: str) -> Any:
    """Download a ``.json.gz`` object and parse the JSON payload.

    Returns the parsed JSON (expected a list of snapshot dicts).
    """
    resp = s3.get_object(Bucket=BUCKET_NAME, Key=key, RequestPayer="requester")
    body = resp["Body"].read()
    with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
        return json.load(gz)

# ---------------------------------------------------------------------------
# Schema validation (lightweight)
# ---------------------------------------------------------------------------

def validate_snapshot(snapshot: Mapping[str, Any], expected_market: str) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    # required top‑level keys
    for k in ("height", "block_time", "market", "bids", "asks"):
        if k not in snapshot:
            errors.append(f"missing {k}")
    if snapshot.get("market") != expected_market:
        errors.append(f"market mismatch {snapshot.get('market')} != {expected_market}")
    bids = snapshot.get("bids", [])
    asks = snapshot.get("asks", [])
    if len(bids) > 20:
        errors.append("bids > 20")
    if len(asks) > 20:
        errors.append("asks > 20")
    # price ordering – values may be str or float
    bid_prices = []
    for b in bids:
        px = b.get("px")
        if px is None:
            errors.append("bid missing px")
        else:
            bid_prices.append(float(px))
    ask_prices = []
    for a in asks:
        px = a.get("px")
        if px is None:
            errors.append("ask missing px")
        else:
            ask_prices.append(float(px))
    if bid_prices and ask_prices:
        if max(bid_prices, default=-float('inf')) >= min(ask_prices, default=float('inf')):
            errors.append("best bid >= best ask")
    # monotonic ordering
    if any(bid_prices[i] < bid_prices[i+1] for i in range(len(bid_prices)-1)):
        errors.append("bids not descending")
    if any(ask_prices[i] > ask_prices[i+1] for i in range(len(ask_prices)-1)):
        errors.append("asks not ascending")
    # non‑negative sizes / counts – values may be str or float
    for side, name in ((bids, "bid"), (asks, "ask")):
        for lvl in side:
            sz = lvl.get("sz")
            if sz is not None and float(sz) < 0:
                errors.append(f"{name} sz negative")
            n = lvl.get("n")
            if n is not None and float(n) < 0:
                errors.append(f"{name} n negative")
    return (len(errors) == 0, errors)

# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run_probe(args: argparse.Namespace) -> Mapping[str, Any]:
    # Prepare run metadata
    run_id = str(uuid.uuid4())
    root = Path(args.out_root)
    meta = {
        "study_id": args.study_id,
        "run_id": run_id,
        "created_at_utc": utc_now_iso(),
        **git_metadata(),
        "repo_root": str(Path.cwd()),
        "command_args": vars(args),
        "safety_mode": "public_data_observer_only",
        "source_name": "sonarx_hyperliquid_public_l2_summary",
        "source_class": "third_party_public_l2_summary_archive",
        "license": "CC0-1.0-as-documented",
        "requester_pays": True,
        "official_hyperliquid_archive": False,
        "executable_l2_summary": True,
        "full_depth_l2": False,
        "top_levels_per_side": 20,
        "no_orders_no_auth_no_live_confirmation": True,
    }

    # Dry‑run handling – only write preview of configuration
    if args.dry_run:
        preview = {"status": "SONARX_L2_SUMMARY_READY", **meta}
        preview_path = make_artifact_path(root, run_id, "dry_run_preview.json")
        write_json(preview_path, preview)
        return preview

    # Real run – S3 interactions
    s3 = s3_client()
    # Phase A – bucket accessibility
    try:
        base_prefixes = list_common_prefixes(s3, BASE_PREFIX)
        bucket_status = "SONARX_L2_SUMMARY_BUCKET_ACCESSIBLE"
    except ClientError as e:
        code = e.response["Error"].get("Code", "")
        if code == "AccessDenied":
            bucket_status = "SONARX_L2_SUMMARY_BUCKET_BLOCKED"
        elif code == "RequestTimeout":
            bucket_status = "SONARX_TRANSPORT_ERROR"
        else:
            bucket_status = "SONARX_ACCESS_DENIED"
        base_prefixes = []
    manifest = {"bucket_status": bucket_status, "base_prefixes": base_prefixes}
    write_json(make_artifact_path(root, run_id, "run_manifest.json"), manifest)

    # Phase B‑D – iterate markets
    market_results: List[dict] = []
    total_bytes = 0
    for market in args.markets.split(',')[: args.max_markets]:
        market_info: dict = {"market": market}
        status, prefix = try_market_prefixes(s3, market)
        market_info["status"] = status
        if status != "SONARX_HIP3_MARKET_FOUND":
            market_results.append(market_info)
            continue
        partitions = list_partitions(s3, prefix)
        market_info["partitions"] = partitions
        if not partitions:
            market_info["partition_status"] = "SONARX_HIP3_PARTITIONS_EMPTY"
            market_results.append(market_info)
            continue
        market_info["partition_status"] = "SONARX_HIP3_PARTITIONS_FOUND"
        # select partitions (earliest, latest, middle)
        selected: List[str] = []
        sorted_parts = sorted(partitions)
        if sorted_parts:
            selected.append(sorted_parts[0])
            if len(sorted_parts) > 1:
                selected.append(sorted_parts[-1])
            if len(sorted_parts) > 2:
                selected.append(sorted_parts[len(sorted_parts)//2])
        selected = selected[: args.max_partitions_per_market]
        market_info["selected_partitions"] = selected
        sample_files: List[dict] = []
        for part in selected:
            files = list_snapshot_files(s3, part)[: args.max_files_per_partition]
            for key in files:
                if total_bytes >= args.download_budget_bytes:
                    break
                try:
                    data = download_and_parse(s3, key)
                    total_bytes += len(json.dumps(data).encode())
                    if isinstance(data, list) and data:
                        ok, _ = validate_snapshot(data[0], market)
                        sample_status = "SONARX_SAMPLE_FILE_PARSE_OK" if ok else "SONARX_SAMPLE_FILE_PARSE_FAILED"
                    else:
                        sample_status = "SONARX_SAMPLE_FILE_PARSE_FAILED"
                except Exception:
                    sample_status = "SONARX_SAMPLE_FILE_PARSE_FAILED"
                sample_files.append({"key": key, "status": sample_status})
        market_info["samples"] = sample_files
        market_results.append(market_info)
        if total_bytes >= args.download_budget_bytes:
            break

    write_json(make_artifact_path(root, run_id, "sonarx_market_coverage.json"), market_results)

    # Build partition inventory artifact
    partition_inventory: List[dict] = []
    for m in market_results:
        partitions = m.get("partitions", [])
        if partitions:
            sorted_p = sorted(partitions)
            partition_inventory.append({
                "market": m["market"],
                "partition_count": len(partitions),
                "earliest_partition": sorted_p[0] if sorted_p else None,
                "latest_partition": sorted_p[-1] if sorted_p else None,
            })
    write_json(make_artifact_path(root, run_id, "sonarx_partition_inventory.json"), partition_inventory)

    # Build sample schema probe artifact
    schema_probe: List[dict] = []
    for m in market_results:
        for s in m.get("samples", []):
            schema_probe.append({
                "market": m["market"],
                "key": s["key"],
                "status": s["status"],
            })
    write_json(make_artifact_path(root, run_id, "sonarx_sample_schema_probe.json"), schema_probe)

    # Build sample quality probe artifact
    quality_probe: List[dict] = []
    for m in market_results:
        parsed_samples = [s for s in m.get("samples", []) if s["status"] == "SONARX_SAMPLE_FILE_PARSE_OK"]
        if parsed_samples:
            quality_probe.append({
                "market": m["market"],
                "files_parsed": len(parsed_samples),
                "snapshots_parsed": sum(1 for s in parsed_samples),
            })
    write_json(make_artifact_path(root, run_id, "sonarx_sample_quality_probe.json"), quality_probe)

    sufficient = any(
        any(s["status"] == "SONARX_SAMPLE_FILE_PARSE_OK" for s in m.get("samples", []))
        for m in market_results
    )
    gate_status = "SONARX_COVERAGE_SUFFICIENT_FOR_PHASE_MINUS1" if sufficient else "SONARX_COVERAGE_INSUFFICIENT"
    gate = {"gate_status": gate_status, "total_bytes_downloaded": total_bytes}
    write_json(make_artifact_path(root, run_id, "gate_decisions.json"), gate)

    summary = {"status": gate_status, **meta, "total_bytes_downloaded": total_bytes}
    write_json(make_artifact_path(root, run_id, "summary.json"), summary)
    md_path = make_artifact_path(root, run_id, "summary.md")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    with md_path.open("w", encoding="utf-8") as f:
        f.write("# SonarX HIP-3 L2 Summary Coverage Probe\n\n")
        f.write(f"**Study ID**: {args.study_id}\n")
        f.write(f"**Run ID**: {run_id}\n")
        f.write(f"**Status**: {gate_status}\n")
        f.write(f"**Bytes downloaded**: {total_bytes}\n")
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SonarX HIP-3 L2 summary coverage probe")
    p.add_argument("--out-root", required=True, help="Root directory for reports")
    p.add_argument("--study-id", default="hip3_sonarx_l2_summary_coverage_probe_v0", help="Study identifier")
    p.add_argument("--markets", required=True, help="Comma‑separated list of DEX:COIN markets")
    p.add_argument("--max-markets", type=int, default=12)
    p.add_argument("--max-partitions-per-market", type=int, default=3)
    p.add_argument("--max-files-per-partition", type=int, default=3)
    p.add_argument("--max-sample-files-total", type=int, default=24)
    p.add_argument("--download-budget-bytes", type=int, default=500_000_000)
    p.add_argument("--allow-s3-archive-read", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p


def main(argv: List[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = run_probe(args)
        print(json.dumps({"status": result.get("status"), "run_id": result.get("run_id")}))
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
