"""Download Hyperliquid official S3 node_trades hourly data into a persistent local cache.

This script manages the download of trade data from:
    s3://hl-mainnet-node-data/node_trades/hourly/

It supports resume, size caps, and manifest tracking. All data is stored in a
persistent cache directory (NOT /tmp) to survive crashes.

Usage examples:

    # Plan only - see what would be downloaded
    uv run --no-sync python scripts/download_hl_node_trades_hourly.py \\
      --plan-only \\
      --cache-root /mnt/nasirjones/py/nautilus_trader/.local_data/hyperliquid_s3_cache/node_trades_hourly \\
      --start-date 2025-03-01 \\
      --max-download-gb 25

    # Execute download
    uv run --no-sync python scripts/download_hl_node_trades_hourly.py \\
      --execute \\
      --cache-root /mnt/nasirjones/py/nautilus_trader/.local_data/hyperliquid_s3_cache/node_trades_hourly \\
      --start-date 2025-03-01 \\
      --max-download-gb 25
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Hyperliquid S3 node_trades hourly data into a persistent cache."
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path("/mnt/nasirjones/py/nautilus_trader/.local_data/hyperliquid_s3_cache/node_trades_hourly"),
        help="Root directory for the persistent cache (default: .local_data in repo)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("reports/hyperliquid_btc_eth_ml_atr_s3_cache_recovery_v0"),
        help="Directory for manifests and reports (default: reports/...)",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Unique run identifier (default: auto-generated timestamp)",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default="2025-03-01",
        help="Start date in YYYY-MM-DD format (default: 2025-03-01)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="End date in YYYY-MM-DD format (default: latest available)",
    )
    parser.add_argument(
        "--max-download-gb",
        type=float,
        default=25.0,
        help="Maximum total download size in GiB (default: 25)",
    )
    parser.add_argument(
        "--request-payer",
        action="store_true",
        default=True,
        help="Include --request-payer requester in AWS CLI calls (default: True)",
    )
    
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--plan-only",
        action="store_true",
        help="Plan the download without executing",
    )
    mode_group.add_argument(
        "--execute",
        action="store_true",
        help="Execute the download",
    )
    
    return parser.parse_args()


def _list_s3_objects(start_date: str, end_date: Optional[str], request_payer: bool = True) -> List[Dict[str, Any]]:
    """List S3 objects in the hourly node_trades bucket.
    
    Returns a list of dicts with keys: key, size, last_modified
    """
    s3_prefix = "s3://hl-mainnet-node-data/node_trades/hourly/"
    
    # Build date filter for ls command
    # We list all and filter locally since S3 ls doesn't have great date filtering
    cmd = ["aws", "s3", "ls", s3_prefix, "--recursive"]
    if request_payer:
        cmd.append("--request-payer")
        cmd.append("requester")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
    except subprocess.CalledProcessError as e:
        # Check for requester-pays credential issues
        if "403" in str(e.stderr) or "AccessDenied" in str(e.stderr):
            print("BLOCKED_AWS_REQUESTER_PAYS_CREDENTIALS_REQUIRED", file=sys.stderr)
            print("AWS requester-pays credentials are missing or expired.", file=sys.stderr)
            sys.exit(1)
        raise
    
    objects = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        # Format: 2025-03-22 10:00:00  12345678  node_trades/hourly/...
        date_str = parts[0]
        time_str = parts[1]
        size = int(parts[2])
        key = " ".join(parts[3:])  # Handle filenames with spaces
        
        # Filter by date range
        obj_date = date_str.replace("-", "")
        start_filter = start_date.replace("-", "")
        
        if obj_date < start_filter:
            continue
        if end_date:
            end_filter = end_date.replace("-", "")
            if obj_date > end_filter:
                continue
        
        objects.append({
            "key": key,
            "size": size,
            "last_modified": f"{date_str}T{time_str}Z",
        })
    
    return objects


def _compute_sha256(filepath: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def _should_download(s3_obj: Dict[str, Any], cache_root: Path) -> Tuple[bool, str, Optional[str]]:
    """Check if a file should be downloaded.
    
    Returns (should_download, reason, local_sha256_if_exists)
    """
    local_path = cache_root / s3_obj["key"]
    s3_size = s3_obj["size"]
    
    if not local_path.exists():
        return True, "missing", None
    
    local_size = local_path.stat().st_size
    if local_size != s3_size:
        return True, f"size_mismatch (local={local_size}, s3={s3_size})", None
    
    # Size matches, compute SHA256 for verification
    local_sha256 = _compute_sha256(local_path)
    # Note: We could also fetch S3 ETag but for now size match is sufficient
    
    return False, "size_match", local_sha256


def _download_object(s3_key: str, cache_root: Path, request_payer: bool = True) -> Path:
    """Download a single S3 object to the cache."""
    local_path = cache_root / s3_key
    local_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Download to temp file first, then rename
    temp_path = local_path.with_suffix(".tmp")
    
    s3_uri = f"s3://hl-mainnet-node-data/{s3_key}"
    cmd = ["aws", "s3", "cp", s3_uri, str(temp_path)]
    if request_payer:
        cmd.append("--request-payer")
        cmd.append("requester")
    
    subprocess.run(cmd, check=True, timeout=600)
    
    # Atomic rename
    temp_path.rename(local_path)
    
    return local_path


def _plan_download(
    cache_root: Path,
    output_root: Path,
    run_id: str,
    start_date: str,
    end_date: Optional[str],
    max_download_gb: float,
    request_payer: bool = True,
) -> Dict[str, Any]:
    """Plan the download and write manifest."""
    
    print(f"Listing S3 objects from {start_date} to {end_date or 'latest'}...")
    objects = _list_s3_objects(start_date, end_date, request_payer)
    print(f"Found {len(objects)} objects")
    
    # Build plan
    manifest_entries = []
    total_bytes = 0
    earliest_date = None
    latest_date = None
    
    for obj in objects:
        should_dl, reason, local_sha256 = _should_download(obj, cache_root)
        
        local_path = cache_root / obj["key"]
        entry = {
            "s3_key": obj["key"],
            "s3_size": obj["size"],
            "local_path": str(local_path),
            "status": "download" if should_dl else "skip",
            "reason": reason,
            "existing_sha256": local_sha256,
        }
        manifest_entries.append(entry)
        
        if should_dl:
            total_bytes += obj["size"]
        
        # Track date range from key
        # Keys typically contain date like: node_trades/hourly/2025/03/22/BTC.jsonl
        key_parts = obj["key"].split("/")
        for part in key_parts:
            if len(part) == 10 and part.count("-") == 2:
                if earliest_date is None or part < earliest_date:
                    earliest_date = part
                if latest_date is None or part > latest_date:
                    latest_date = part
    
    total_gb = total_bytes / (1024 ** 3)
    
    # Check cap
    if total_gb > max_download_gb:
        print(f"\nBLOCKED_S3_COST_OR_SIZE_CAP", file=sys.stderr)
        print(f"Planned download size: {total_gb:.2f} GiB exceeds cap: {max_download_gb} GiB", file=sys.stderr)
        print("Reduce date range or increase cap.", file=sys.stderr)
        
        # Still write the plan for inspection
        summary = {
            "status": "BLOCKED_S3_COST_OR_SIZE_CAP",
            "run_id": run_id,
            "cache_root": str(cache_root),
            "start_date": start_date,
            "end_date": end_date,
            "max_download_gb": max_download_gb,
            "planned_bytes": total_bytes,
            "planned_gb": total_gb,
            "object_count": len(objects),
            "to_download": sum(1 for e in manifest_entries if e["status"] == "download"),
            "to_skip": sum(1 for e in manifest_entries if e["status"] == "skip"),
            "earliest_date": earliest_date,
            "latest_date": latest_date,
        }
        
        run_dir = output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        (run_dir / "download_manifest.json").write_text(
            json.dumps({"entries": manifest_entries}, indent=2, sort_keys=True) + "\n"
        )
        
        sys.exit(1)
    
    summary = {
        "status": "PLAN_READY",
        "run_id": run_id,
        "cache_root": str(cache_root),
        "start_date": start_date,
        "end_date": end_date,
        "max_download_gb": max_download_gb,
        "planned_bytes": total_bytes,
        "planned_gb": total_gb,
        "object_count": len(objects),
        "to_download": sum(1 for e in manifest_entries if e["status"] == "download"),
        "to_skip": sum(1 for e in manifest_entries if e["status"] == "skip"),
        "earliest_date": earliest_date,
        "latest_date": latest_date,
    }
    
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (run_dir / "download_manifest.json").write_text(
        json.dumps({"entries": manifest_entries}, indent=2, sort_keys=True) + "\n"
    )
    
    return summary


def _execute_download(
    cache_root: Path,
    output_root: Path,
    run_id: str,
    start_date: str,
    end_date: Optional[str],
    max_download_gb: float,
    request_payer: bool = True,
) -> Dict[str, Any]:
    """Execute the download and write manifest with results."""
    
    # First, get the plan
    print("Building download plan...")
    objects = _list_s3_objects(start_date, end_date, request_payer)
    print(f"Found {len(objects)} objects")
    
    # Build initial plan
    manifest_entries = []
    total_bytes = 0
    
    for obj in objects:
        should_dl, reason, local_sha256 = _should_download(obj, cache_root)
        
        local_path = cache_root / obj["key"]
        entry = {
            "s3_key": obj["key"],
            "s3_size": obj["size"],
            "local_path": str(local_path),
            "status": "download" if should_dl else "skip",
            "reason": reason,
            "existing_sha256": local_sha256,
        }
        manifest_entries.append(entry)
        
        if should_dl:
            total_bytes += obj["size"]
    
    total_gb = total_bytes / (1024 ** 3)
    
    if total_gb > max_download_gb:
        print(f"\nBLOCKED_S3_COST_OR_SIZE_CAP", file=sys.stderr)
        print(f"Planned download size: {total_gb:.2f} GiB exceeds cap: {max_download_gb} GiB", file=sys.stderr)
        sys.exit(1)
    
    print(f"\nStarting download: {len([e for e in manifest_entries if e['status']=='download'])} files, {total_gb:.2f} GiB")
    
    # Execute downloads
    downloaded = 0
    skipped = 0
    failed = 0
    downloaded_bytes = 0
    
    for entry in manifest_entries:
        if entry["status"] == "skip":
            skipped += 1
            continue
        
        try:
            local_path = _download_object(entry["s3_key"], cache_root, request_payer)
            sha256 = _compute_sha256(local_path)
            entry["status"] = "downloaded"
            entry["downloaded_sha256"] = sha256
            downloaded += 1
            downloaded_bytes += entry["s3_size"]
            
            # Progress heartbeat
            if downloaded % 10 == 0 or downloaded == len([e for e in manifest_entries if e["status"] == "download"]):
                print(f"Progress: {downloaded}/{len([e for e in manifest_entries if e['status']=='download'])} files, "
                      f"{downloaded_bytes / (1024**3):.2f} GiB downloaded")
                      
        except Exception as e:
            entry["status"] = "failed"
            entry["error"] = str(e)
            failed += 1
            print(f"Failed to download {entry['s3_key']}: {e}", file=sys.stderr)
    
    # Write final manifest
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    
    final_status = "COMPLETE" if failed == 0 else "PARTIAL"
    if failed > 0 and downloaded == 0:
        final_status = "FAILED"
    
    summary = {
        "status": final_status,
        "run_id": run_id,
        "cache_root": str(cache_root),
        "start_date": start_date,
        "end_date": end_date,
        "max_download_gb": max_download_gb,
        "total_objects": len(manifest_entries),
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "downloaded_bytes": downloaded_bytes,
        "downloaded_gb": downloaded_bytes / (1024 ** 3),
    }
    
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    (run_dir / "download_manifest.json").write_text(
        json.dumps({"entries": manifest_entries}, indent=2, sort_keys=True) + "\n"
    )
    
    print(f"\nDownload complete: {downloaded} downloaded, {skipped} skipped, {failed} failed")
    print(f"Manifest: {run_dir / 'download_manifest.json'}")
    print(f"Summary: {run_dir / 'summary.json'}")
    
    if final_status == "FAILED":
        sys.exit(1)
    
    return summary


def main() -> None:
    args = _parse_args()
    
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    
    # Ensure cache root exists
    args.cache_root.mkdir(parents=True, exist_ok=True)
    
    # Ensure output root exists (relative to cwd)
    args.output_root.mkdir(parents=True, exist_ok=True)
    
    print(f"Cache root: {args.cache_root}")
    print(f"Output root: {args.output_root}")
    print(f"Run ID: {run_id}")
    print(f"Start date: {args.start_date}")
    print(f"End date: {args.end_date or 'latest'}")
    print(f"Max download: {args.max_download_gb} GiB")
    print()
    
    if args.plan_only:
        summary = _plan_download(
            cache_root=args.cache_root,
            output_root=args.output_root,
            run_id=run_id,
            start_date=args.start_date,
            end_date=args.end_date,
            max_download_gb=args.max_download_gb,
            request_payer=args.request_payer,
        )
        print("\nPlan written. Review and then run with --execute")
        
    elif args.execute:
        summary = _execute_download(
            cache_root=args.cache_root,
            output_root=args.output_root,
            run_id=run_id,
            start_date=args.start_date,
            end_date=args.end_date,
            max_download_gb=args.max_download_gb,
            request_payer=args.request_payer,
        )


if __name__ == "__main__":
    main()