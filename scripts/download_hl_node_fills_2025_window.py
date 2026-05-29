"""Download Hyperliquid node fills from official S3 archive for 2025 window.

This script plans and executes downloads from:
    s3://hl-mainnet-node-data/node_fills_by_block/hourly/

Supports:
    - Multiple date windows via repeated --window START:END
    - Cost cap via --max-download-gb
    - Disk space check via --require-free-disk-gb
    - Plan-only mode via --plan-only
    - Execute mode via --execute
    - Resume-safe downloads (skips existing files matching expected size)
    - Manifest generation

IMPORTANT:
    - No credentials are committed or printed.
    - Uses AWS CLI for requester-pays bucket access.
    - Default safety margin: 25 GiB.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import orjson
except ImportError:
    orjson = None


@dataclass
class S3Object:
    """Represents a single S3 object in the archive."""
    key: str
    size: int
    last_modified: str


@dataclass
class DownloadPlan:
    """Complete download plan manifest."""
    stage: str  # "representative" or "full"
    source_prefix: str
    selected_windows: List[str]
    object_count: int
    planned_bytes: int
    max_cap_gi: float
    required_free_disk_gi: float
    free_disk_before_gi: float
    requester_pays: bool
    cache_root: str
    credentials_in_manifest: bool = False
    
    # Tracking fields (populated during/after download)
    downloaded_bytes: int = 0
    skipped_bytes: int = 0
    downloaded_objects: int = 0
    skipped_objects: int = 0
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DateWindow:
    """A date range window for filtering."""
    start: datetime
    end: datetime
    
    def contains(self, dt: datetime) -> bool:
        return self.start <= dt <= self.end
    
    def __str__(self) -> str:
        return f"{self.start.strftime('%Y-%m-%d')}:{self.end.strftime('%Y-%m-%d')}"


def parse_window(window_str: str) -> DateWindow:
    """Parse a window string like '2025-08-01:2025-08-31'."""
    parts = window_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid window format: {window_str}. Expected START:END")
    start = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    # End date is inclusive, so extend to end of day
    end = datetime.strptime(parts[1], "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    return DateWindow(start=start, end=end)


def get_free_disk_gi(path: Path) -> float:
    """Get free disk space in GiB for the filesystem containing path."""
    stat = os.statvfs(path)
    free_bytes = stat.f_bavail * stat.f_frsize
    return free_bytes / (1024 ** 3)


def list_s3_objects(prefix: str, request_payer: bool = False) -> List[S3Object]:
    """List all objects under an S3 prefix using AWS CLI.
    
    Returns a list of S3Object with key, size, and last_modified.
    """
    cmd = ["aws", "s3", "ls", "--recursive", "--human-readable"]
    if request_payer:
        cmd.extend(["--request-payer", "requester"])
    cmd.append(prefix)
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            print(f"AWS CLI error: {result.stderr}", file=sys.stderr)
            return []
    except subprocess.TimeoutExpired:
        print("AWS CLI timed out after 600s", file=sys.stderr)
        return []
    except FileNotFoundError:
        print("AWS CLI not found. Please install: aws", file=sys.stderr)
        return []
    
    objects = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        # Format: DATE TIME SIZE KEY
        date_str = parts[0]
        time_str = parts[1]
        size_str = parts[2]
        key = " ".join(parts[3:])  # Key may contain spaces
        
        # Parse size (may have K/M/G suffix)
        size_match = re.match(r'^([\d.]+)([KMGT]?i?B)?', size_str, re.IGNORECASE)
        if not size_match:
            continue
        
        size_value = float(size_match.group(1))
        suffix = (size_match.group(2) or "").upper()
        
        multipliers = {
            'B': 1,
            'KIB': 1024, 'KB': 1024,
            'MIB': 1024**2, 'MB': 1024**2,
            'GIB': 1024**3, 'GB': 1024**3,
            'TIB': 1024**4, 'TB': 1024**4,
        }
        size_bytes = int(size_value * multipliers.get(suffix, 1))
        
        # Parse timestamp
        try:
            modified = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
            modified = modified.replace(tzinfo=timezone.utc)
        except ValueError:
            modified = datetime.now(timezone.utc)
        
        objects.append(S3Object(key=key, size=size_bytes, last_modified=modified.isoformat()))
    
    return objects


def extract_date_from_key(key: str) -> Optional[datetime]:
    """Extract date from an S3 key.
    
    Expected format: s3://bucket/path/2025/08/01/... or similar with YYYY/MM/DD or YYYY-MM-DD
    """
    # Try YYYY/MM/DD pattern
    match = re.search(r'(\d{4})/(\d{2})/(\d{2})', key)
    if match:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)
    
    # Try YYYY-MM-DD pattern
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', key)
    if match:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc)
    
    return None


def filter_objects_by_windows(
    objects: List[S3Object],
    windows: List[DateWindow],
) -> Tuple[List[S3Object], Dict[str, int]]:
    """Filter S3 objects to only those falling within any of the date windows.
    
    Returns (filtered_objects, per_window_counts)
    """
    filtered = []
    per_window_counts = {str(w): 0 for w in windows}
    
    for obj in objects:
        obj_date = extract_date_from_key(obj.key)
        if obj_date is None:
            continue
        
        for window in windows:
            if window.contains(obj_date):
                filtered.append(obj)
                per_window_counts[str(window)] = per_window_counts.get(str(window), 0) + 1
                break
    
    return filtered, per_window_counts


def plan_download(
    source_prefix: str,
    windows: List[DateWindow],
    cache_root: Path,
    max_download_gi: float,
    require_free_disk_gi: float,
    request_payer: bool,
    stage: str,
) -> Tuple[DownloadPlan, List[S3Object]]:
    """Create a download plan without downloading anything.
    
    Returns (plan, filtered_objects)
    """
    print(f"Listing objects from {source_prefix}...")
    objects = list_s3_objects(source_prefix, request_payer=request_payer)
    print(f"Found {len(objects)} total objects in archive.")
    
    # Filter by windows
    filtered_objects, per_window_counts = filter_objects_by_windows(objects, windows)
    print(f"Filtered to {len(filtered_objects)} objects within specified windows.")
    
    for window_str, count in per_window_counts.items():
        print(f"  {window_str}: {count} objects")
    
    # Calculate planned bytes
    planned_bytes = sum(obj.size for obj in filtered_objects)
    planned_gi = planned_bytes / (1024 ** 3)
    
    print(f"Planned download: {planned_gi:.2f} GiB ({len(filtered_objects)} objects)")
    
    # Check cap
    if planned_gi > max_download_gi:
        print(f"BLOCKED: Planned {planned_gi:.2f} GiB exceeds cap of {max_download_gi:.2f} GiB")
        print("STATUS: BLOCKED_COST_OR_SIZE_CAP")
    
    # Check free disk
    cache_root.mkdir(parents=True, exist_ok=True)
    free_disk = get_free_disk_gi(cache_root)
    safety_margin = 25.0  # GiB
    required_disk = planned_gi + safety_margin
    
    if free_disk < require_free_disk_gi:
        print(f"BLOCKED: Free disk {free_disk:.2f} GiB < required {require_free_disk_gi:.2f} GiB")
        print("STATUS: BLOCKED_INSUFFICIENT_DISK")
    
    plan = DownloadPlan(
        stage=stage,
        source_prefix=source_prefix,
        selected_windows=[str(w) for w in windows],
        object_count=len(filtered_objects),
        planned_bytes=planned_bytes,
        max_cap_gi=max_download_gi,
        required_free_disk_gi=require_free_disk_gi,
        free_disk_before_gi=free_disk,
        requester_pays=request_payer,
        cache_root=str(cache_root),
        credentials_in_manifest=False,
    )
    
    return plan, filtered_objects


def download_object(
    obj: S3Object,
    cache_root: Path,
    source_prefix: str,
    request_payer: bool,
) -> Tuple[int, bool]:
    """Download a single S3 object to cache.
    
    Returns (bytes_downloaded, was_skipped)
    
    Resume-safe: skips if file exists with matching size.
    """
    # Local path mirrors S3 key structure
    relative_path = obj.key.replace(source_prefix, "").lstrip("/")
    local_path = cache_root / relative_path
    
    # Check if already downloaded
    if local_path.exists():
        existing_size = local_path.stat().st_size
        if existing_size == obj.size:
            return 0, True  # Skipped
        else:
            print(f"  Size mismatch for {relative_path}: existing={existing_size}, expected={obj.size}, re-downloading...")
    
    # Create parent directories
    local_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Download
    cmd = ["aws", "s3", "cp"]
    if request_payer:
        cmd.extend(["--request-payer", "requester"])
    cmd.extend([f"s3://{obj.key}", str(local_path)])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"  Download failed: {result.stderr}", file=sys.stderr)
            return 0, False
        return obj.size, False
    except subprocess.TimeoutExpired:
        print(f"  Download timed out: {obj.key}", file=sys.stderr)
        return 0, False


def execute_download(
    plan: DownloadPlan,
    objects: List[S3Object],
    cache_root: Path,
    source_prefix: str,
    request_payer: bool,
) -> DownloadPlan:
    """Execute the download plan."""
    print(f"Starting download of {len(objects)} objects...")
    
    downloaded_bytes = 0
    skipped_bytes = 0
    downloaded_count = 0
    skipped_count = 0
    
    for i, obj in enumerate(objects):
        if i % 10 == 0:
            downloaded_gi = downloaded_bytes / (1024 ** 3)
            print(f"Progress: {i}/{len(objects)} objects, {downloaded_gi:.2f} GiB downloaded, {skipped_count} skipped")
        
        bytes_down, was_skipped = download_object(obj, cache_root, source_prefix, request_payer)
        
        if was_skipped:
            skipped_bytes += obj.size
            skipped_count += 1
        else:
            downloaded_bytes += bytes_down
            downloaded_count += 1
    
    plan.downloaded_bytes = downloaded_bytes
    plan.skipped_bytes = skipped_bytes
    plan.downloaded_objects = downloaded_count
    plan.skipped_objects = skipped_count
    
    print(f"\nDownload complete:")
    print(f"  Downloaded: {downloaded_count} objects, {downloaded_bytes / (1024**3):.2f} GiB")
    print(f"  Skipped: {skipped_count} objects, {skipped_bytes / (1024**3):.2f} GiB")
    print(f"  Total: {downloaded_bytes + skipped_bytes} bytes")
    
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Hyperliquid node fills from official S3 archive.")
    parser.add_argument("--source-prefix", required=True,
                        help="S3 prefix (e.g., s3://hl-mainnet-node-data/node_fills_by_block/hourly/)")
    parser.add_argument("--window", action="append", required=False,
                        help="Date window in START:END format (e.g., 2025-08-01:2025-08-31). Can be repeated.")
    parser.add_argument("--start-date", required=False,
                        help="Start date for full-window mode (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=False,
                        help="End date for full-window mode (YYYY-MM-DD)")
    parser.add_argument("--cache-root", required=True, type=Path,
                        help="Root directory for cached downloads.")
    parser.add_argument("--max-download-gb", type=float, required=True,
                        help="Maximum download cap in GiB.")
    parser.add_argument("--require-free-disk-gb", type=float, required=True,
                        help="Required free disk space in GiB before download.")
    parser.add_argument("--request-payer", action="store_true",
                        help="Use requester-pays mode for S3 access.")
    parser.add_argument("--plan-only", action="store_true",
                        help="Only create a plan without downloading.")
    parser.add_argument("--execute", action="store_true",
                        help="Execute the download.")
    parser.add_argument("--manifest-path", required=True, type=Path,
                        help="Path to write the manifest JSON.")
    
    args = parser.parse_args()
    
    if not args.plan_only and not args.execute:
        print("Must specify either --plan-only or --execute", file=sys.stderr)
        sys.exit(1)
    
    if args.plan_only and args.execute:
        print("Cannot specify both --plan-only and --execute", file=sys.stderr)
        sys.exit(1)
    
    # Parse windows
    windows: List[DateWindow] = []
    if args.window:
        for w in args.window:
            windows.append(parse_window(w))
    elif args.start_date and args.end_date:
        windows.append(DateWindow(
            start=datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc),
            end=datetime.strptime(args.end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
        ))
    else:
        print("Must specify either --window or --start-date/--end-date", file=sys.stderr)
        sys.exit(1)
    
    # Determine stage
    stage = "representative" if len(windows) == 3 else "full"
    print(f"Stage: {stage}")
    print(f"Windows: {[str(w) for w in windows]}")
    
    # Create plan
    plan, filtered_objects = plan_download(
        source_prefix=args.source_prefix,
        windows=windows,
        cache_root=args.cache_root,
        max_download_gi=args.max_download_gb,
        require_free_disk_gi=args.require_free_disk_gb,
        request_payer=args.request_payer,
        stage=stage,
    )
    
    # Check if blocked
    if plan.planned_bytes / (1024 ** 3) > args.max_download_gb:
        plan_status = f"BLOCKED_{stage.upper()}_COST_OR_SIZE_CAP"
        plan_dict = plan.to_dict()
        plan_dict["block_status"] = plan_status
        args.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_path.write_text(json.dumps(plan_dict, indent=2, sort_keys=True) + "\n")
        print(f"Manifest written to {args.manifest_path}")
        print(f"STATUS: {plan_status}")
        sys.exit(0)
    
    free_disk = get_free_disk_gi(args.cache_root)
    if free_disk < args.require_free_disk_gb:
        plan_status = f"BLOCKED_{stage.upper()}_INSUFFICIENT_DISK"
        plan_dict = plan.to_dict()
        plan_dict["block_status"] = plan_status
        args.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_path.write_text(json.dumps(plan_dict, indent=2, sort_keys=True) + "\n")
        print(f"Manifest written to {args.manifest_path}")
        print(f"STATUS: {plan_status}")
        sys.exit(0)
    
    # Write plan manifest
    args.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    
    if args.plan_only:
        plan_dict = plan.to_dict()
        plan_dict["status"] = f"ML_ATR_2025_WINDOW_STAGED_CAP_V0_{stage.upper()}_PLAN_READY"
        args.manifest_path.write_text(json.dumps(plan_dict, indent=2, sort_keys=True) + "\n")
        print(f"Plan manifest written to {args.manifest_path}")
        print(f"STATUS: ML_ATR_2025_WINDOW_STAGED_CAP_V0_{stage.upper()}_PLAN_READY")
    elif args.execute:
        plan = execute_download(
            plan=plan,
            objects=filtered_objects,
            cache_root=args.cache_root,
            source_prefix=args.source_prefix,
            request_payer=args.request_payer,
        )
        plan_dict = plan.to_dict()
        plan_dict["status"] = f"ML_ATR_2025_WINDOW_STAGED_CAP_V0_{stage.upper()}_DOWNLOAD_COMPLETE"
        args.manifest_path.write_text(json.dumps(plan_dict, indent=2, sort_keys=True) + "\n")
        print(f"Download manifest written to {args.manifest_path}")
        print(f"STATUS: ML_ATR_2025_WINDOW_STAGED_CAP_V0_{stage.upper()}_DOWNLOAD_COMPLETE")


if __name__ == "__main__":
    main()