#!/usr/bin/env python3
"""
Download official Hyperliquid node_fills_by_block 2025-window data from S3.

Usage:
    uv run --no-sync python scripts/download_hl_node_fills_2025_window.py \\
        --source-prefix s3://hl-mainnet-node-data/node_fills_by_block/hourly/ \\
        --start-date 2025-08-01 \\
        --end-date 2026-05-28 \\
        --cache-root .local_data/hyperliquid_s3_cache/node_fills_2025_window \\
        --max-download-gb 25 \\
        --request-payer \\
        --plan-only

    # Then execute:
    uv run --no-sync python scripts/download_hl_node_fills_2025_window.py \\
        --source-prefix s3://hl-mainnet-node-data/node_fills_by_block/hourly/ \\
        --start-date 2025-08-01 \\
        --end-date 2026-05-28 \\
        --cache-root .local_data/hyperliquid_s3_cache/node_fills_2025_window \\
        --max-download-gb 25 \\
        --request-payer \\
        --execute
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def parse_args():
    parser = argparse.ArgumentParser(description="Download HL node_fills_by_block 2025-window data")
    parser.add_argument("--source-prefix", required=True, help="S3 prefix, e.g. s3://hl-mainnet-node-data/node_fills_by_block/hourly/")
    parser.add_argument("--start-date", required=True, help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end-date", required=True, help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument("--cache-root", required=True, help="Local cache directory")
    parser.add_argument("--max-download-gb", type=float, default=25.0, help="Max download cap in GiB (default: 25)")
    parser.add_argument("--request-payer", action="store_true", help="Add --request-payer to aws s3 cp")
    parser.add_argument("--plan-only", action="store_true", help="List objects and report sizes, do not download")
    parser.add_argument("--execute", action="store_true", help="Actually download files")
    return parser.parse_args()


def list_objects(source_prefix: str, start_date: str, end_date: str, request_payer: bool) -> list[dict]:
    """List objects in S3 for the date range using aws s3api list-objects-v2."""
    # Extract bucket and base prefix from source_prefix
    # e.g. s3://hl-mainnet-node-data/node_fills_by_block/hourly/
    bucket = source_prefix.replace("s3://", "").split("/")[0]
    base_prefix = source_prefix.replace("s3://", "").replace(bucket + "/", "", 1)

    # Parse date range
    start_str = start_date.replace("-", "")  # 20250801
    end_str = end_date.replace("-", "")       # 20260529

    # List the entire prefix once with pagination, then filter to date range
    results = []
    continuation_token = None
    while True:
        cmd = ["aws", "s3api", "list-objects-v2", "--bucket", bucket,
               "--prefix", base_prefix, "--max-keys", "1000"]
        if request_payer:
            cmd.extend(["--request-payer", "requester"])
        if continuation_token:
            cmd.extend(["--continuation-token", continuation_token])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            logger.warning(f"Failed to list {base_prefix}: {r.stderr.strip()}")
            break
        try:
            parsed = json.loads(r.stdout)
            for obj in parsed.get("Contents", []):
                key = obj["Key"]
                if not key.startswith(base_prefix):
                    continue
                # Extract date from key: base_prefix/YYYYMMDD/filename.lz4
                parts = key.split("/")
                if len(parts) < 3:
                    continue
                date_str = parts[2]
                if date_str < start_str or date_str > end_str:
                    continue
                results.append({
                    "key": key,
                    "size": obj["Size"],
                    "date": date_str,
                })
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse response for {base_prefix}")
            break
        if "NextContinuationToken" in parsed:
            continuation_token = parsed["NextContinuationToken"]
        else:
            break
    return results


def plan(objects: list[dict], max_download_gb: float, source_prefix: str) -> dict:
    """Plan download: compute sizes, check cap."""
    total_bytes = sum(o["size"] for o in objects)
    total_gb = total_bytes / (1024 ** 3)
    max_bytes = max_download_gb * (1024 ** 3)

    return {
        "total_objects": len(objects),
        "total_bytes": total_bytes,
        "total_gb": round(total_gb, 3),
        "max_gb": max_download_gb,
        "under_cap": total_bytes <= max_bytes,
        "source_prefix": source_prefix,
        "object_keys": [o["key"] for o in objects],
        "object_sizes": {o["key"]: o["size"] for o in objects},
    }


def download(objects: list[dict], cache_root: str, request_payer: bool) -> dict:
    """Download files, skipping existing ones."""
    cache = Path(cache_root)
    cache.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    skipped = 0
    failed = 0
    total_bytes = 0

    for obj in objects:
        # Extract just the filename from the S3 key for local path
        filename = obj["key"].split("/")[-1]
        local_path = cache / obj["date"] / filename
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if local_path.exists() and local_path.stat().st_size == obj["size"]:
            skipped += 1
            continue

        s3_key = obj["key"]
        cmd = ["aws", "s3", "cp", s3_key, str(local_path)]
        if request_payer:
            cmd.append("--request-payer")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode == 0:
            downloaded += 1
            total_bytes += obj["size"]
            logger.info(f"Downloaded {s3_key} ({obj['size']} bytes)")
        else:
            failed += 1
            logger.error(f"Failed {s3_key}: {r.stderr.strip()}")

    return {
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "total_bytes_downloaded": total_bytes,
    }


def main():
    args = parse_args()

    start_date = args.start_date
    end_date = args.end_date
    request_payer = args.request_payer
    max_gb = args.max_download_gb

    logger.info(f"Listing objects from {args.source_prefix} for {start_date} to {end_date}")
    objects = list_objects(args.source_prefix, start_date, end_date, request_payer)

    if not objects:
        logger.error("No objects found in the specified date range.")
        sys.exit(1)

    plan_result = plan(objects, max_gb, args.source_prefix)

    if not plan_result["under_cap"]:
        logger.error(
            f"Planned download {plan_result['total_gb']} GiB exceeds cap of {max_gb} GiB. "
            f"Stopping. Set --max-download-gb higher to override."
        )
        sys.exit(2)

    logger.info(
        f"Plan: {plan_result['total_objects']} objects, "
        f"{plan_result['total_gb']} GiB total, under {max_gb} GiB cap."
    )

    if args.plan_only:
        manifest = {
            "source_prefix": plan_result["source_prefix"],
            "start_date": start_date,
            "end_date": end_date,
            "request_payer": request_payer,
            "max_download_gb": max_gb,
            "total_objects": plan_result["total_objects"],
            "total_bytes": plan_result["total_bytes"],
            "total_gb": plan_result["total_gb"],
            "object_keys": plan_result["object_keys"],
            "object_sizes": plan_result["object_sizes"],
            "bytes_planned": plan_result["total_bytes"],
            "bytes_downloaded": 0,
            "skipped_existing": 0,
            "downloaded_count": 0,
            "source_date_range": [start_date, end_date],
            "aws_requester_pays": request_payer,
            "credentials_stored": False,
        }
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return

    if not args.execute:
        logger.info("Use --execute to download.")
        return

    logger.info("Downloading...")
    dl_result = download(objects, args.cache_root, request_payer)

    manifest = {
        "source_prefix": plan_result["source_prefix"],
        "start_date": start_date,
        "end_date": end_date,
        "request_payer": request_payer,
        "max_download_gb": max_gb,
        "total_objects": plan_result["total_objects"],
        "total_bytes": plan_result["total_bytes"],
        "total_gb": plan_result["total_gb"],
        "object_keys": plan_result["object_keys"],
        "object_sizes": plan_result["object_sizes"],
        "bytes_planned": plan_result["total_bytes"],
        "bytes_downloaded": dl_result["total_bytes_downloaded"],
        "skipped_existing": dl_result["skipped"],
        "downloaded_count": dl_result["downloaded"],
        "failed_count": dl_result["failed"],
        "source_date_range": [start_date, end_date],
        "aws_requester_pays": request_payer,
        "credentials_stored": False,
    }
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
