"""
Hyperliquid BTC/ETH ML+ATR Official S3 Bars v0 — S3 Archive → 1h OHLCV
=======================================================================

Fetch official Hyperliquid historical fills from S3, aggregate to 1h bars.
NOT live trading. NOT exchange-connected. S3 archive only.

Statuses:
  S3_BARS_V0_PLAN_READY
  S3_BARS_V0_READY_FOR_ML_ATR
  S3_BARS_V0_NEEDS_MORE_DATA
  S3_BARS_V0_BLOCKED_AWS_CLI_MISSING
  S3_BARS_V0_BLOCKED_LZ4_MISSING
  S3_BARS_V0_BLOCKED_AWS_REQUESTER_PAYS_CREDENTIALS_REQUIRED
  S3_BARS_V0_BLOCKED_S3_LIST_FAILED
  S3_BARS_V0_BLOCKED_S3_COST_OR_SIZE_CAP
  S3_BARS_V0_BLOCKED_S3_SCHEMA_UNRECOGNIZED
  S3_BARS_V0_BLOCKED_PARSE_FAILED
  S3_BARS_V0_BLOCKED_INSUFFICIENT_COVERAGE
  S3_BARS_V0_BLOCKED_V0_PREFLIGHT_FAILED
  S3_BARS_V0_ERROR_INVALID_OUTPUT
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_SYMBOLS = frozenset({"BTC", "ETH"})
SPEC_VERSION = "official_s3_bars_v0"

DEFAULT_S3_PREFIXES = [
    "s3://hl-mainnet-node-data/node_fills_by_block",
    "s3://hl-mainnet-node-data/node_fills",
    "s3://hl-mainnet-node-data/node_trades",
]

BARS_CSV_COLUMNS = ["timestamp", "symbol", "open", "high", "low", "close", "volume"]
FUNDING_CSV_COLUMNS = ["timestamp", "symbol", "funding_rate"]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class S3BarsConfig:
    output_root: Path = Path("reports/hyperliquid_btc_eth_ml_atr_official_s3_bars_v0")
    run_id: Optional[str] = None
    start_date: str = "2023-10-01"
    end_date: str = "2026-05-27"
    symbols: Tuple[str, ...] = ("BTC", "ETH")
    s3_prefixes: Tuple[str, ...] = tuple(DEFAULT_S3_PREFIXES)
    funding_root: Path = Path("examples/strategies/venue_agnostic_signal_observer/data/hyperliquid_funding_archive_phase0")
    max_download_gb: float = 25.0
    plan_only: bool = False
    sample_only: bool = False
    execute: bool = False
    keep_raw: bool = False
    request_payer: bool = True
    no_sign_request: bool = False
    max_objects: Optional[int] = None
    max_gap_hours: int = 24
    write_csv: bool = True
    write_parquet: bool = True
    dry_run: bool = False


@dataclass
class S3ObjectInfo:
    key: str
    size: int
    last_modified: str = ""
    etag: str = ""


@dataclass
class S3DownloadPlan:
    status: str
    reason: str = ""
    objects: List[S3ObjectInfo] = field(default_factory=list)
    total_bytes: int = 0
    total_gb: float = 0.0
    earliest_date: str = ""
    latest_date: str = ""
    prefixes_inspected: List[str] = field(default_factory=list)


@dataclass
class S3BarsBuildSummary:
    status: str
    reason: str = ""
    bars_row_count: int = 0
    coverage_by_symbol: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    gap_hours_by_symbol: Dict[str, int] = field(default_factory=dict)
    ohlc_sanity_rejects: int = 0
    zero_volume_bar_count: int = 0
    warnings: List[str] = field(default_factory=list)


@dataclass
class S3BarsRunSummary:
    status: str
    reason: str = ""
    run_id: str = ""
    plan: Optional[S3DownloadPlan] = None
    bars_summary: Optional[S3BarsBuildSummary] = None
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tool Checks
# ---------------------------------------------------------------------------
def check_required_tools() -> Dict[str, bool]:
    """Check if aws CLI and lz4 are available."""
    result = {}
    for cmd, name in [("aws", "aws_cli"), ("lz4", "lz4"), ("unlz4", "unlz4")]:
        try:
            subprocess.run([cmd, "--version"], capture_output=True, timeout=5)
            result[name] = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            result[name] = False
    # Also accept if either lz4 or unlz4 exists
    result["lz4"] = result.get("lz4", False) or result.get("unlz4", False)
    return result


# ---------------------------------------------------------------------------
# S3 Listing
# ---------------------------------------------------------------------------
def _aws_cmd(args: List[str], request_payer: bool = True, no_sign_request: bool = False) -> subprocess.CompletedProcess:
    """Run AWS CLI command with requester-pays."""
    cmd = ["aws"] + args
    if request_payer:
        cmd.extend(["--request-payer", "requester"])
    if no_sign_request:
        cmd.append("--no-sign-request")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def list_s3_objects(prefix: str, request_payer: bool = True, max_items: Optional[int] = None) -> Tuple[List[S3ObjectInfo], str]:
    """
    List objects under an S3 prefix.
    Returns (objects, error_or_empty).
    """
    args = ["s3", "ls", prefix, "--recursive"]
    result = _aws_cmd(args, request_payer=request_payer)
    if result.returncode != 0:
        return [], result.stderr.strip()

    objects = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("PRE"):
            continue
        parts = line.split(None, 3)
        if len(parts) >= 4:
            date_str = parts[0] + " " + parts[1]
            size = int(parts[2])
            key = parts[3]
            objects.append(S3ObjectInfo(key=key, size=size, last_modified=date_str))
    return objects, ""


def discover_s3_layout(prefixes: List[str], request_payer: bool = True) -> S3DownloadPlan:
    """Discover S3 object layout and build initial plan."""
    all_objects = []
    for prefix in prefixes:
        objects, err = list_s3_objects(prefix, request_payer=request_payer, max_items=500)
        if err and "AccessDenied" in err:
            return S3DownloadPlan(
                status="S3_BARS_V0_BLOCKED_AWS_REQUESTER_PAYS_CREDENTIALS_REQUIRED",
                reason=f"S3 access denied: {err[:200]}",
                prefixes_inspected=prefixes,
            )
        if err:
            return S3DownloadPlan(
                status="S3_BARS_V0_BLOCKED_S3_LIST_FAILED",
                reason=f"S3 list failed for {prefix}: {err[:200]}",
                prefixes_inspected=prefixes,
            )
        all_objects.extend(objects)

    if not all_objects:
        return S3DownloadPlan(
            status="S3_BARS_V0_BLOCKED_S3_LIST_FAILED",
            reason="No objects found under any prefix",
            prefixes_inspected=prefixes,
        )

    total_bytes = sum(o.size for o in all_objects)
    total_gb = total_bytes / (1024**3)

    return S3DownloadPlan(
        status="S3_BARS_V0_PLAN_READY",
        objects=all_objects,
        total_bytes=total_bytes,
        total_gb=total_gb,
        prefixes_inspected=prefixes,
    )


# ---------------------------------------------------------------------------
# Coverage Check
# ---------------------------------------------------------------------------
def check_coverage_against_v0_requirements(plan: S3DownloadPlan, config: S3BarsConfig) -> S3DownloadPlan:
    """Check if S3 data covers the v0 train/validation/test windows."""
    # v0 requires:
    # train: late 2023 through 2024-12-31
    # validation: 2025-01-01 through 2025-06-30
    # test: 2025-07-01 onward

    # Check if any objects exist before 2025-01-01
    earliest_date = "9999-99-99"
    for obj in plan.objects:
        # Try to extract date from key (YYYYMMDD pattern)
        for part in obj.key.split("/"):
            if len(part) == 8 and part.isdigit():
                date_str = f"{part[:4]}-{part[4:6]}-{part[6:8]}"
                if date_str < earliest_date:
                    earliest_date = date_str
                break

    plan.earliest_date = earliest_date

    if earliest_date > "2024-12-31":
        plan.status = "S3_BARS_V0_BLOCKED_INSUFFICIENT_COVERAGE"
        plan.reason = (
            f"Earliest S3 data ({earliest_date}) is after v0 train window end (2024-12-31). "
            f"v0 requires train data from late 2023 through 2024-12-31. "
            f"Official Hyperliquid S3 archive starts from {earliest_date}."
        )

    return plan


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------
def run_s3_bars(config: S3BarsConfig) -> S3BarsRunSummary:
    """Run the S3 bars pipeline."""
    run_id = config.run_id or f"s3_bars_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    output_dir = config.output_root / run_id

    # Check tools
    tools = check_required_tools()
    if not tools.get("aws_cli"):
        return S3BarsRunSummary(
            status="S3_BARS_V0_BLOCKED_AWS_CLI_MISSING",
            reason="AWS CLI not found. Install with: sudo pacman -S aws-cli",
            run_id=run_id,
        )
    if not tools.get("lz4"):
        return S3BarsRunSummary(
            status="S3_BARS_V0_BLOCKED_LZ4_MISSING",
            reason="lz4 not found. Install with: sudo pacman -S lz4",
            run_id=run_id,
        )

    # Discover S3 layout
    plan = discover_s3_layout(list(config.s3_prefixes), request_payer=config.request_payer)

    if plan.status != "S3_BARS_V0_PLAN_READY":
        return S3BarsRunSummary(
            status=plan.status,
            reason=plan.reason,
            run_id=run_id,
            plan=plan,
        )

    # Check cost/size cap
    if plan.total_gb > config.max_download_gb:
        plan.status = "S3_BARS_V0_BLOCKED_S3_COST_OR_SIZE_CAP"
        plan.reason = f"Estimated download {plan.total_gb:.2f} GiB exceeds cap {config.max_download_gb} GiB"
        return S3BarsRunSummary(
            status=plan.status,
            reason=plan.reason,
            run_id=run_id,
            plan=plan,
        )

    # Check coverage
    plan = check_coverage_against_v0_requirements(plan, config)

    if plan.status != "S3_BARS_V0_PLAN_READY":
        return S3BarsRunSummary(
            status=plan.status,
            reason=plan.reason,
            run_id=run_id,
            plan=plan,
        )

    # If plan-only, return here
    if config.plan_only:
        if not config.dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
            _write_plan_artifacts(output_dir, plan, run_id)
        return S3BarsRunSummary(
            status="S3_BARS_V0_PLAN_READY",
            run_id=run_id,
            plan=plan,
        )

    # For now, report the plan status
    return S3BarsRunSummary(
        status="S3_BARS_V0_PLAN_READY",
        run_id=run_id,
        plan=plan,
    )


def _write_plan_artifacts(output_dir: Path, plan: S3DownloadPlan, run_id: str) -> None:
    """Write plan artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # download_plan.json
    (output_dir / "download_plan.json").write_text(json.dumps({
        "status": plan.status,
        "total_bytes": plan.total_bytes,
        "total_gb": plan.total_gb,
        "object_count": len(plan.objects),
        "prefixes_inspected": plan.prefixes_inspected,
        "earliest_date": plan.earliest_date,
    }, sort_keys=True, indent=2) + "\n")

    # summary.json
    (output_dir / "summary.json").write_text(json.dumps({
        "status": plan.status,
        "reason": plan.reason,
        "run_id": run_id,
        "total_bytes": plan.total_bytes,
        "total_gb": plan.total_gb,
        "object_count": len(plan.objects),
        "earliest_date": plan.earliest_date,
    }, sort_keys=True, indent=2) + "\n")

    # summary.md
    lines = [
        "DO NOT USE FOR LIVE TRADING",
        "",
        f"Status: {plan.status}",
    ]
    if plan.reason:
        lines.append(f"Reason: {plan.reason}")
    lines.extend([
        "",
        f"Run ID: {run_id}",
        f"Prefixes inspected: {', '.join(plan.prefixes_inspected)}",
        f"Objects found: {len(plan.objects)}",
        f"Total bytes: {plan.total_bytes:,}",
        f"Total GiB: {plan.total_gb:.2f}",
        f"Earliest date: {plan.earliest_date}",
        "",
        "This does not authorize live, exchange-paper, bot, or order-routing execution.",
    ])
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n")

    # manifest.json
    (output_dir / "manifest.json").write_text(json.dumps({
        "spec_version": SPEC_VERSION,
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "s3_prefixes_inspected": plan.prefixes_inspected,
        "object_count": len(plan.objects),
        "total_bytes": plan.total_bytes,
        "total_gb": plan.total_gb,
        "earliest_date": plan.earliest_date,
        "requester_pays": True,
        "max_download_gb": 25.0,
        "safety_flags": {
            "official_s3_only": True,
            "no_exchange_orders": True,
            "no_exchange_auth": True,
            "no_live_execution": True,
        },
    }, sort_keys=True, indent=2) + "\n")
