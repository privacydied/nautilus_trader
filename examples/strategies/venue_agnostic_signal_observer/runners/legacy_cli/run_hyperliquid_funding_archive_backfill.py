#!/usr/bin/env python3
"""CLI runner for Hyperliquid funding archive backfill.

Fetches historical funding data from Hyperliquid's public /info endpoint
and writes immutable JSONL archive files with provenance metadata.

Usage:
    uv run --no-sync python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_funding_archive_backfill \\
        --asset BTC \\
        --start-date 2024-01-01 \\
        --end-date 2024-12-31 \\
        --output-dir data/hyperliquid_funding_archive \\
        --sleep-ms 500
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from examples.strategies.venue_agnostic_signal_observer import hyperliquid_funding_archive_backfill as backfill


def _git(args: list[str]) -> str:
    """Get git information for provenance."""
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser."""
    parser = argparse.ArgumentParser(
        description="Hyperliquid funding archive backfill from public REST API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Backfill BTC for 2024
  uv run --no-sync python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_funding_archive_backfill \\
      --asset BTC --start-date 2024-01-01 --end-date 2024-12-31 \\
      --output-dir data/hyperliquid_funding_archive --sleep-ms 500

  # Backfill ETH for Q1 2025
  uv run --no-sync python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_funding_archive_backfill \\
      --asset ETH --start-date 2025-01-01 --end-date 2025-03-31 \\
      --output-dir data/hyperliquid_funding_archive --sleep-ms 500

  # Backfill both BTC and ETH
  uv run --no-sync python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_funding_archive_backfill \\
      --asset BTC --asset ETH --start-date 2024-06-01 --end-date 2024-06-30 \\
      --output-dir data/hyperliquid_funding_archive --sleep-ms 500
        """,
    )
    
    parser.add_argument(
        "--asset",
        action="append",
        required=True,
        help="Asset symbol (BTC or ETH). Can be specified multiple times.",
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="Start date as YYYY-MM-DD",
    )
    parser.add_argument(
        "--end-date",
        required=True,
        help="End date as YYYY-MM-DD",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Output directory for JSONL archive files",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=500,
        help="Milliseconds to sleep between API requests (default: 500)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Legacy alias for --output-dir (deprecated, use --output-dir)",
    )
    
    return parser


def _validate_date(date_str: str) -> bool:
    """Validate date format YYYY-MM-DD."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _report(results: list[backfill.BackfillResult], run_manifest: dict[str, Any]) -> str:
    """Generate human-readable report."""
    lines = [
        "# Hyperliquid Funding Archive Backfill Report",
        "",
        "## Summary",
        f"Generated at: {run_manifest['generated_at_utc']}",
        f"Git SHA: {run_manifest.get('git_sha', 'N/A')}",
        f"Git branch: {run_manifest.get('git_branch', 'N/A')}",
        "",
        "## Configuration",
        f"Assets: {', '.join(run_manifest['assets'])}",
        f"Date range: {run_manifest['start_date']} to {run_manifest['end_date']}",
        f"Output directory: {run_manifest['output_dir']}",
        f"Sleep between requests: {run_manifest['sleep_ms']}ms",
        "",
        "## Results by Asset",
    ]
    
    for result in results:
        if result.error:
            lines.append(f"- **{result.asset}**: ERROR - {result.error}")
        else:
            lines.append(f"- **{result.asset}**: {result.rows_fetched} rows fetched")
            lines.append(f"  - Output: {result.output_path}")
            lines.append(f"  - Manifest: {result.output_path.replace('.jsonl', '.manifest.json')}")
    
    lines.extend([
        "",
        "## Safety Guarantees",
        "- Public unauthenticated data only",
        "- No API keys required",
        "- No auth headers",
        "- No private keys",
        "- No wallet",
        "- No signing",
        "- No orders",
        "- No execution client imports",
        "- No live trading",
        "- No paper trading",
        "- No shadow execution",
        "- No bot path",
        "- No forward returns",
        "- No PnL",
        "- No post-event price-path inspection",
        "- No null testing",
        "- No FDR",
        "- No holdout",
        "- No v1 precommitment",
        "- No evaluator",
        "- No generic exchange data framework",
        "- No continuous polling mode",
        "- No 'fetch latest forever' mode",
        "",
        "## Data Format",
        "Output files are in JSONL format (one JSON object per line) with fields:",
        "- `coin`: Asset symbol (e.g., 'BTC', 'ETH')",
        "- `timestamp_ms`: Millisecond timestamp",
        "- `timestamp_iso`: ISO8601 timestamp string",
        "- `funding_rate`: Raw funding rate from API",
        "- `hourly_funding_bps`: Hourly funding rate in basis points",
        "- `projected_8h_funding_bps`: 8-hour projected funding in basis points",
        "- `source_endpoint`: API endpoint used",
        "",
        "## Usage Notes",
        "- Archives are immutable once written",
        "- Each asset gets its own JSONL file",
        "- Sidecar manifest contains provenance metadata",
        "- SHA256 hash allows integrity verification",
        "",
    ])
    
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    
    # Handle legacy --output-root alias
    output_dir = args.output_dir
    if args.output_root is not None:
        output_dir = args.output_root
    
    # Validate assets
    assets = [a.upper() for a in args.asset]
    valid_assets = {"BTC", "ETH"}
    for asset in assets:
        if asset not in valid_assets:
            print(f"Error: Invalid asset '{asset}'. Must be one of: {', '.join(sorted(valid_assets))}", file=sys.stderr)
            return 1
    
    # Validate dates
    if not _validate_date(args.start_date):
        print(f"Error: Invalid start-date format '{args.start_date}'. Use YYYY-MM-DD.", file=sys.stderr)
        return 1
    if not _validate_date(args.end_date):
        print(f"Error: Invalid end-date format '{args.end_date}'. Use YYYY-MM-DD.", file=sys.stderr)
        return 1
    
    # Validate date range
    start_dt = datetime.strptime(args.start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end_date, "%Y-%m-%d")
    if end_dt < start_dt:
        print(f"Error: end-date ({args.end_date}) is before start-date ({args.start_date})", file=sys.stderr)
        return 1
    
    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Build run manifest
    run_manifest: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "git_sha": _git(["rev-parse", "--short=12", "HEAD"]),
        "git_branch": _git(["branch", "--show-current"]),
        "git_dirty": bool(_git(["status", "--porcelain"])),
        "assets": assets,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "output_dir": str(output_dir),
        "sleep_ms": args.sleep_ms,
        "endpoint": backfill.ENDPOINT,
        "safety_mode": "public_data_observer_only",
    }
    
    print(f"Starting Hyperliquid funding archive backfill...")
    print(f"Assets: {', '.join(assets)}")
    print(f"Date range: {args.start_date} to {args.end_date}")
    print(f"Output directory: {output_dir}")
    print(f"Sleep between requests: {args.sleep_ms}ms")
    print()
    
    # Perform backfill
    results: list[backfill.BackfillResult] = []
    try:
        results = backfill.backfill_assets(
            assets=assets,
            start_date=args.start_date,
            end_date=args.end_date,
            output_dir=output_dir,
            sleep_ms=args.sleep_ms,
        )
    except Exception as e:
        print(f"Error during backfill: {e}", file=sys.stderr)
        run_manifest["error"] = str(e)
        # Write error manifest
        error_manifest_path = output_dir / "backfill_error.manifest.json"
        error_manifest_path.write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")
        return 1
    
    # Print report
    report = _report(results, run_manifest)
    print(report)
    
    # Write run manifest
    run_manifest["results"] = [
        {
            "asset": r.asset,
            "rows_fetched": r.rows_fetched,
            "output_path": r.output_path,
            "error": r.error,
        }
        for r in results
    ]
    run_manifest_path = output_dir / f"backfill_{args.start_date}_to_{args.end_date}.run_manifest.json"
    run_manifest_path.write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")
    
    # Return non-zero if any errors
    has_errors = any(r.error for r in results)
    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
