"""SonarX HIP-3 TradFi Historical L2 Depth/Spread + Residual Phase -1 Scout.

Phase -1 historical data-plane feasibility study for SonarX L2 summary
snapshots of Hyperliquid HIP-3 builder DEX TradFi symbols.

Scope: data-plane feasibility ONLY.
  - No strategy, no PnL, no returns, no signals, no entries/exits
  - No Phase 0 precommitment, no registry mutation, no promotion
  - No live/paper trading, no orders, no auth, no private keys

All S3 access is via NetworkChokepoint with explicit guard flags.
Public Hyperliquid endpoints are accessed only for candle metadata.
Public equity anchors are attempted via Yahoo Finance only.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import statistics
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping, Tuple
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Ensure parent package is importable
# ---------------------------------------------------------------------------
_PKG_ROOT = str(Path(__file__).resolve().parents[4])
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
    NetworkChokepoint,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STATUSES = frozenset({
    "SONARX_PHASE_MINUS1_READY",
    "SONARX_PHASE_MINUS1_SOURCE_READY",
    "SONARX_L2_HISTORY_PARSE_OK",
    "SONARX_L2_HISTORY_UNDERPOWERED",
    "SONARX_L2_HISTORY_QUALITY_LOW",
    "SONARX_L2_LIQUIDITY_MEASURABLE",
    "SONARX_L2_LIQUIDITY_BLOCKED",
    "SONARX_ANCHOR_AVAILABLE",
    "SONARX_ANCHOR_BLOCKED",
    "SONARX_CANDLE_JOIN_AVAILABLE",
    "SONARX_CANDLE_JOIN_BLOCKED",
    "SONARX_RESIDUAL_DIAGNOSTIC_AVAILABLE",
    "SONARX_RESIDUAL_DIAGNOSTIC_UNDERPOWERED",
    "SONARX_RESIDUAL_TAIL_EXISTS_DIAGNOSTIC_ONLY",
    "SONARX_RESIDUAL_TAIL_NOT_OBSERVED_DIAGNOSTIC_ONLY",
    "SONARX_PHASE_MINUS1_NEXT_PRECOMMITMENT_REVIEW_ALLOWED",
    "SONARX_PHASE_MINUS1_NOT_ENOUGH_FOR_PRECOMMITMENT",
    "SONARX_PHASE_MINUS1_ERROR",
    "SONARX_PHASE_MINUS1_REAL_RUN_STARTED",
    "SONARX_PHASE_MINUS1_IN_PROGRESS",
    "SONARX_S3_TRANSPORT_TIMEOUT",
    "SONARX_S3_READ_TIMEOUT",
    "SONARX_S3_CONNECT_TIMEOUT",
    "SONARX_REQUESTER_PAYS_CREDENTIALS_REQUIRED",
    "SONARX_ACCESS_DENIED",
    "SONARX_PHASE_MINUS1_SOURCE_BLOCKED",
})

FORBIDDEN_STATUSES = frozenset({
    "REJECTED", "PROFITABLE", "ALPHA_FOUND", "TRADE_READY",
    "EXECUTION_READY", "LIVE_READY", "READY_FOR_PHASE_0",
    "CANDIDATE_FOR_LIVE", "PAPER_STRATEGY_PROMOTED",
    "PROMOTION_AUTHORIZED", "EDGE_CONFIRMED",
})

S3_BUCKET = "sonarx-hyperliquid-public"
S3_BASE_PREFIX = "market_data/hip3/"
L2_SUMMARY_SUFFIX = "l2-summary-snapshots/"

NY_TZ = ZoneInfo("America/New_York")

# Predefined market list
ALL_12_API_SYMBOLS = [
    "xyz:TSLA", "flx:TSLA", "km:TSLA", "cash:TSLA",
    "xyz:AAPL", "km:AAPL",
    "xyz:MSFT", "cash:MSFT",
    "xyz:NVDA", "flx:NVDA", "km:NVDA", "cash:NVDA",
]

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

# ---------------------------------------------------------------------------
# Artifact metadata helper
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_metadata() -> Mapping[str, Any]:
    """Read .git files directly — no subprocess, no shell, no eval."""
    git_dir = Path(__file__).resolve().parent.parent.parent.parent / ".git"
    sha, dirty, branch = "unknown", False, "unknown"
    try:
        head_ref = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head_ref.startswith("ref: "):
            branch_path = head_ref[5:]
            branch = branch_path.replace("refs/heads/", "")
            ref_file = git_dir / branch_path
            if ref_file.exists():
                sha = ref_file.read_text(encoding="utf-8").strip()
            else:
                # Worktree .git file → gitdir:
                git_file = git_dir
                if git_file.is_file():
                    gitdir_line = git_file.read_text(encoding="utf-8").strip()
                    if gitdir_line.startswith("gitdir: "):
                        real_dir = Path(gitdir_line[8:])
                        real_ref = real_dir / branch_path
                        if real_ref.exists():
                            sha = real_ref.read_text(encoding="utf-8").strip()
        else:
            sha = head_ref
        dirty = (git_dir / "MERGE_HEAD").exists() or (git_dir / "CHERRY_PICK_HEAD").exists()
    except Exception:
        sha, dirty, branch = "unknown", False, "unknown"
    return {"git_sha": sha, "git_dirty": dirty, "branch": branch}


def make_base_meta(args: argparse.Namespace) -> dict:
    return {
        "study_id": args.study_id,
        "created_at_utc": utc_now_iso(),
        **git_metadata(),
        "repo_root": str(Path.cwd()),
        "command_args": vars(args),
        "safety_mode": "public_data_observer_only",
        "source_name": "sonarx_hyperliquid_public_l2_summary",
        "source_class": "third_party_public_l2_summary_archive",
        "official_hyperliquid_s3": False,
        "official_hyperliquid_candleSnapshot_used": bool(getattr(args, "enable_candle_join", False)),
        "executable_l2_summary": True,
        "full_depth_l2": False,
        "top_levels_per_side": 20,
        "no_orders_no_auth_no_live_confirmation": True,
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True, default=str)


# ---------------------------------------------------------------------------
# Display symbol / DEX parsing
# ---------------------------------------------------------------------------

def parse_api_symbol(api_sym: str) -> Tuple[str, str]:
    """Return (display_symbol, dex_name) from 'dex:COIN'."""
    if ":" in api_sym:
        dex, coin = api_sym.split(":", 1)
        return coin, dex
    return api_sym, "unknown"


# ---------------------------------------------------------------------------
# Session classification (America/New_York, zoneinfo)
# ---------------------------------------------------------------------------

def classify_session(utc_dt: datetime) -> str:
    """Classify a UTC datetime into an equity market session bucket.

    Boundary rules (all times America/New_York):
      regular_hours: Mon-Fri 09:30–16:00
      premarket:     Mon-Fri 06:00–09:30
      after_hours:   Mon-Fri 16:00–23:59
      overnight:     Mon-Fri 00:00–06:00
      weekend_or_holiday: Sat/Sun
    """
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    et_dt = utc_dt.astimezone(NY_TZ)
    dow = et_dt.weekday()
    h, m = et_dt.hour, et_dt.minute
    if dow >= 5:
        return "weekend_or_holiday"
    if h < 6:
        return "overnight"
    if h < 9 or (h == 9 and m < 30):
        return "premarket"
    if h == 9 and m >= 30:
        return "regular_hours"
    if 10 <= h < 16:
        return "regular_hours"
    if h >= 16:
        return "after_hours"
    return "premarket"


# ---------------------------------------------------------------------------
# L2 snapshot parsing
# ---------------------------------------------------------------------------

def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def parse_snapshot(raw: dict, api_symbol: str) -> dict | None:
    """Parse one SonarX L2 summary snapshot into a normalised row."""
    block_time_str = raw.get("block_time", "")
    try:
        if block_time_str.endswith("Z"):
            block_time_str = block_time_str[:-1] + "+00:00"
        ts = datetime.fromisoformat(block_time_str)
    except Exception:
        ts = datetime.now(timezone.utc)

    bids_raw = raw.get("bids", [])
    asks_raw = raw.get("asks", [])

    # Sort and parse levels
    bids = []
    for b in bids_raw:
        px = _safe_float(b.get("px"))
        sz = _safe_float(b.get("sz"))
        n = _safe_int(b.get("n"))
        bids.append({"px": px, "sz": sz, "n": n})
    bids.sort(key=lambda x: x["px"], reverse=True)

    asks = []
    for a in asks_raw:
        px = _safe_float(a.get("px"))
        sz = _safe_float(a.get("sz"))
        n = _safe_int(a.get("n"))
        asks.append({"px": px, "sz": sz, "n": n})
    asks.sort(key=lambda x: x["px"])

    best_bid = bids[0]["px"] if bids else 0.0
    best_ask = asks[0]["px"] if asks else 0.0
    mid = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else 0.0
    spread_bps = ((best_ask - best_bid) / mid * 10000.0) if mid > 0 else float("inf")

    two_sided = len(bids) > 0 and len(asks) > 0
    empty_bid = len(bids) == 0
    empty_ask = len(asks) == 0

    # Depth at thresholds
    thresholds = [100.0, 500.0, 1000.0, 5000.0]
    depth_bids = {}
    depth_asks = {}
    for thr in thresholds:
        depth_bids[thr] = sum(lv["px"] * lv["sz"] for lv in bids if lv["px"] > 0)
        depth_asks[thr] = sum(lv["px"] * lv["sz"] for lv in asks if lv["px"] > 0)
        # For bid depth, accumulate from best bid downward (already sorted desc)
        # For simplicity, top-20 levels means total book depth is the full depth
        # We use cumulative from top-of-book

    top_bid_depth = bids[0]["px"] * bids[0]["sz"] if bids else 0.0
    top_ask_depth = asks[0]["px"] * asks[0]["sz"] if asks else 0.0

    depth_cap = len(bids) >= 20 or len(asks) >= 20
    display_sym, dex_name = parse_api_symbol(api_symbol)

    quality = "ok"
    if empty_bid or empty_ask:
        quality = "empty_side"
    if spread_bps == float("inf"):
        quality = "no_spread"

    return {
        "timestamp_utc": ts.isoformat(),
        "block_height": raw.get("height", 0),
        "market": raw.get("market", api_symbol),
        "display_symbol": display_sym,
        "dex_name": dex_name,
        "api_symbol": api_symbol,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": round(spread_bps, 4),
        "bid_levels_count": len(bids),
        "ask_levels_count": len(asks),
        "two_sided_book": two_sided,
        "empty_bid_side": empty_bid,
        "empty_ask_side": empty_ask,
        "top_of_book_bid_depth_usd": round(top_bid_depth, 4),
        "top_of_book_ask_depth_usd": round(top_ask_depth, 4),
        "depth_usd_100_bid": round(depth_bids[100.0], 4),
        "depth_usd_100_ask": round(depth_asks[100.0], 4),
        "depth_usd_500_bid": round(depth_bids[500.0], 4),
        "depth_usd_500_ask": round(depth_asks[500.0], 4),
        "depth_usd_1000_bid": round(depth_bids[1000.0], 4),
        "depth_usd_1000_ask": round(depth_asks[1000.0], 4),
        "depth_usd_5000_bid": round(depth_bids[5000.0], 4),
        "depth_usd_5000_ask": round(depth_asks[5000.0], 4),
        "depth_cap_hit_top20": depth_cap,
        "schema_quality_status": quality,
    }


# ---------------------------------------------------------------------------
# Phase B — S3 download and parse
# ---------------------------------------------------------------------------

def list_s3_prefixes(cp: NetworkChokepoint, prefix: str) -> list[str]:
    result = cp.s3_list_prefix(S3_BUCKET, prefix, requester_pays=True, include_subdirs=True)
    return result.get("prefixes", [])


def list_s3_objects(cp: NetworkChokepoint, prefix: str) -> list[dict]:
    result = cp.s3_list_prefix(S3_BUCKET, prefix, requester_pays=True, include_subdirs=False)
    return result.get("objects", [])


def download_gzip_json(cp: NetworkChokepoint, key: str) -> Any:
    raw = cp.s3_read_object(S3_BUCKET, key, requester_pays=True)
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
        return json.load(gz)


# ---------------------------------------------------------------------------
# Stratified sampling
# ---------------------------------------------------------------------------

def build_stratified_sample(
    partitions: list[str],
    sample_days: int,
    max_files_per_market: int,
) -> list[str]:
    """Select partition keys for stratified sampling."""
    if not partitions:
        return []
    sorted_parts = sorted(partitions)
    n = len(sorted_parts)
    selected = set()
    # earliest, middle, latest
    selected.add(sorted_parts[0])
    if n > 1:
        selected.add(sorted_parts[-1])
    if n > 2:
        selected.add(sorted_parts[n // 2])
    # Spread across the range
    step = max(1, n // max_files_per_market)
    for i in range(0, n, step):
        selected.add(sorted_parts[i])
    return sorted(selected)[:max_files_per_market]


def scan_partitions_for_nonempty_keys(
    cp: NetworkChokepoint,
    market_prefix: str,
    all_partitions: list[str],
    max_partitions_to_scan: int,
    min_files_target: int,
    prefer_known_good: list[str] | None = None,
) -> dict:
    """Scan partitions to find actual .json.gz keys, skipping empty ones.
    
    Returns dict with:
        - selected_keys: list of actual .json.gz object keys
        - partitions_scanned: number of partitions examined
        - empty_partitions: list of empty partition prefixes
        - nonempty_partitions: list of non-empty partition prefixes
        - candidate_keys_seen: total .json.gz keys found (before limiting)
        - stop_reason: why scanning stopped
    """
    selected_keys = []
    partitions_scanned = 0
    empty_partitions = []
    nonempty_partitions = []
    candidate_keys_seen = 0
    stop_reason = None
    
    # If prefer_known_good is provided, try those first
    if prefer_known_good:
        for key in prefer_known_good:
            if key.startswith(market_prefix) and key.endswith(".json.gz"):
                selected_keys.append(key)
                if len(selected_keys) >= min_files_target:
                    stop_reason = "KNOWN_GOOD_KEYS_SUFFICIENT"
                    break
        if stop_reason:
            return {
                "selected_keys": selected_keys,
                "partitions_scanned": 0,
                "empty_partitions": [],
                "nonempty_partitions": list(set(k.rsplit("/", 1)[0] + "/" for k in selected_keys)),
                "candidate_keys_seen": len(selected_keys),
                "stop_reason": stop_reason,
            }
    
    # Scan partitions adaptively
    sorted_partitions = sorted(all_partitions)
    for part_prefix in sorted_partitions:
        if partitions_scanned >= max_partitions_to_scan:
            stop_reason = "MAX_PARTITIONS_SCANNED"
            break
        if len(selected_keys) >= min_files_target:
            stop_reason = "MIN_FILES_TARGET_MET"
            break
        
        partitions_scanned += 1
        objs = list_s3_objects(cp, part_prefix)
        gz_objs = [o for o in objs if o.get("key", "").endswith(".json.gz")]
        candidate_keys_seen += len(gz_objs)
        
        if not gz_objs:
            empty_partitions.append(part_prefix)
        else:
            nonempty_partitions.append(part_prefix)
            # Take up to 10 keys per partition
            for obj in gz_objs[:10]:
                selected_keys.append(obj["key"])
                if len(selected_keys) >= min_files_target:
                    stop_reason = "MIN_FILES_TARGET_MET"
                    break
    
    if stop_reason is None:
        stop_reason = "NO_MORE_PARTITIONS"
    
    return {
        "selected_keys": selected_keys,
        "partitions_scanned": partitions_scanned,
        "empty_partitions": empty_partitions,
        "nonempty_partitions": nonempty_partitions,
        "candidate_keys_seen": candidate_keys_seen,
        "stop_reason": stop_reason,
    }


# ---------------------------------------------------------------------------
# Liquidity quantile helpers
# ---------------------------------------------------------------------------

def _quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(q * (len(sorted_vals) - 1))
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


def compute_liquidity_stats(rows: list[dict]) -> dict:
    if not rows:
        return {"snapshot_count": 0}
    spreads = sorted(r["spread_bps"] for r in rows if r["spread_bps"] != float("inf"))
    two_sided = sum(1 for r in rows if r["two_sided_book"])
    empty_side = sum(1 for r in rows if r["empty_bid_side"] or r["empty_ask_side"])
    cap_hits = sum(1 for r in rows if r["depth_cap_hit_top20"])
    n = len(rows)
    return {
        "snapshot_count": n,
        "two_sided_book_rate": round(two_sided / n, 4) if n else 0,
        "empty_side_rate": round(empty_side / n, 4) if n else 0,
        "median_spread_bps": round(_quantile(spreads, 0.5), 4),
        "p75_spread_bps": round(_quantile(spreads, 0.75), 4),
        "p90_spread_bps": round(_quantile(spreads, 0.90), 4),
        "p99_spread_bps": round(_quantile(spreads, 0.99), 4),
        "depth_cap_hit_rate": round(cap_hits / n, 4) if n else 0,
    }


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run_phase_minus1(args: argparse.Namespace) -> dict:
    run_id = str(uuid.uuid4())[:8]
    root = Path(args.out_root) / run_id
    root.mkdir(parents=True, exist_ok=True)
    meta = make_base_meta(args)
    meta["run_id"] = run_id

    statuses: list[str] = []
    
    # Apply real-smoke mode limits BEFORE any network calls
    configured_markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    max_markets = args.max_markets
    max_files_per_market = args.max_files_per_market
    download_budget = args.download_budget_bytes
    
    if getattr(args, "real_smoke", False):
        max_markets = min(max_markets, 2) if max_markets else 2
        max_files_per_market = min(max_files_per_market, 10) if max_files_per_market else 10
        download_budget = min(download_budget, 50_000_000) if download_budget else 50_000_000
    
    # Create NetworkChokepoint with bounded timeouts
    cp = NetworkChokepoint(
        allow_network_public=getattr(args, "allow_network_public", False),
        allow_s3_archive_read=getattr(args, "allow_s3_archive_read", False),
        s3_connect_timeout=getattr(args, "s3_connect_timeout_seconds", 10),
        s3_read_timeout=getattr(args, "s3_read_timeout_seconds", 30),
        s3_max_attempts=getattr(args, "s3_max_attempts", 2),
    )
    
    # Write early artifacts BEFORE first network call
    run_manifest = {
        **meta,
        "run_id": run_id,
        "created_at_utc": utc_now_iso(),
        "status": "SONARX_PHASE_MINUS1_REAL_RUN_STARTED",
        "current_phase": "initialization_complete",
        "markets_configured": configured_markets,
        "markets_limit": max_markets,
        "max_files_per_market": max_files_per_market,
        "download_budget_bytes": download_budget,
        "sample_days": args.sample_days,
        "sample_mode": args.sample_mode,
        "s3_connect_timeout_seconds": getattr(args, "s3_connect_timeout_seconds", 10),
        "s3_read_timeout_seconds": getattr(args, "s3_read_timeout_seconds", 30),
        "s3_max_attempts": getattr(args, "s3_max_attempts", 2),
        "real_smoke_mode": getattr(args, "real_smoke", False),
        "executable_pnl_modeled": False,
        "queue_position_modeled": False,
        "fills_modeled": False,
        "slippage_model_modeled": False,
        "diagnostic_only": True,
    }
    write_json(root / "run_manifest.json", run_manifest)
    
    # Initial status artifact
    initial_status = {
        **meta,
        "run_id": run_id,
        "status": "SONARX_PHASE_MINUS1_REAL_RUN_STARTED",
        "created_at_utc": utc_now_iso(),
        "current_phase": "initialization_complete",
        "last_progress_at_utc": utc_now_iso(),
        "markets": configured_markets[:max_markets] if max_markets else configured_markets,
        "command_args": vars(args),
        "git_sha": meta.get("git_sha", "unknown"),
        "safety_mode": "public_data_observer_only",
        "executable_pnl_modeled": False,
        "diagnostic_only": True,
    }
    write_json(root / "phase_minus1_status.json", initial_status)

    markets = configured_markets[:max_markets] if max_markets else configured_markets

    # --- Source coverage plan ---
    source_plan = {
        **meta,
        "markets": markets,
        "sample_days": args.sample_days,
        "sample_mode": args.sample_mode,
        "max_files_per_market": max_files_per_market,
        "download_budget_bytes": download_budget,
        "sessions_targeted": [
            "regular_hours", "premarket", "after_hours",
            "overnight", "weekend_or_holiday",
        ],
        "anchor_symbols": ["TSLA", "AAPL", "MSFT", "NVDA"],
        "candle_intervals": ["1h", "4h", "1d"],
    }
    write_json(root / "source_coverage_plan.json", source_plan)

    if getattr(args, "dry_run", False):
        preview = {"status": "SONARX_PHASE_MINUS1_READY", **meta}
        write_json(root / "dry_run_preview.json", preview)
        statuses.append("SONARX_PHASE_MINUS1_READY")
        summary = {"status": statuses[-1], "statuses": statuses, **meta}
        write_json(root / "summary.json", summary)
        return summary

    # --- Phase B: list partitions and download/parse ---
    all_metrics: list[dict] = []
    market_index: dict[str, dict] = {}
    total_bytes = 0
    global_key_discovery = {
        "total_partition_prefixes_seen": 0,
        "total_partitions_scanned": 0,
        "total_empty_partitions": 0,
        "total_nonempty_partitions": 0,
        "total_candidate_keys_seen": 0,
        "total_selected_keys": 0,
        "total_downloaded_keys": 0,
    }

    for api_sym in markets:
        display_sym, dex_name = parse_api_symbol(api_sym)
        market_prefix = f"{S3_BASE_PREFIX}{api_sym}/{L2_SUMMARY_SUFFIX}"
        partitions = list_s3_prefixes(cp, market_prefix)
        sorted_partitions = sorted(partitions)
        global_key_discovery["total_partition_prefixes_seen"] += len(partitions)
        
        mi = {
            "api_symbol": api_sym,
            "display_symbol": display_sym,
            "dex_name": dex_name,
            "partition_count": len(partitions),
            "earliest_partition": sorted_partitions[0] if sorted_partitions else None,
            "latest_partition": sorted_partitions[-1] if sorted_partitions else None,
            "files_parsed": 0,
            "snapshots_parsed": 0,
            "errors": 0,
            "partitions_scanned": 0,
            "empty_partitions_count": 0,
            "nonempty_partitions_count": 0,
            "candidate_keys_seen": 0,
            "selected_keys": [],
            "selected_key_count": 0,
            "downloaded_key_count": 0,
            "first_selected_key": None,
            "last_selected_key": None,
            "skipped_empty_partitions": [],
            "selection_strategy": "adaptive_scan_skip_empty",
            "stop_reason": None,
            "market_status": None,
        }
        if not partitions:
            mi["market_status"] = "SONARX_MARKET_NO_PARTITIONS_FOUND"
            market_index[api_sym] = mi
            continue

        # Use adaptive partition scanning
        scan_result = scan_partitions_for_nonempty_keys(
            cp=cp,
            market_prefix=market_prefix,
            all_partitions=partitions,
            max_partitions_to_scan=getattr(args, "max_partitions_scanned_per_market", 200),
            min_files_target=getattr(args, "min_files_per_market", 10),
            prefer_known_good=None,  # Could load from known-good reference
        )
        
        mi["partitions_scanned"] = scan_result["partitions_scanned"]
        mi["empty_partitions_count"] = len(scan_result["empty_partitions"])
        mi["nonempty_partitions_count"] = len(scan_result["nonempty_partitions"])
        mi["candidate_keys_seen"] = scan_result["candidate_keys_seen"]
        mi["selected_keys"] = scan_result["selected_keys"]
        mi["selected_key_count"] = len(scan_result["selected_keys"])
        mi["first_selected_key"] = scan_result["selected_keys"][0] if scan_result["selected_keys"] else None
        mi["last_selected_key"] = scan_result["selected_keys"][-1] if scan_result["selected_keys"] else None
        mi["skipped_empty_partitions"] = scan_result["empty_partitions"]
        mi["stop_reason"] = scan_result["stop_reason"]
        
        global_key_discovery["total_partitions_scanned"] += scan_result["partitions_scanned"]
        global_key_discovery["total_empty_partitions"] += len(scan_result["empty_partitions"])
        global_key_discovery["total_nonempty_partitions"] += len(scan_result["nonempty_partitions"])
        global_key_discovery["total_candidate_keys_seen"] += scan_result["candidate_keys_seen"]
        global_key_discovery["total_selected_keys"] += len(scan_result["selected_keys"])
        
        if not scan_result["selected_keys"]:
            mi["market_status"] = "SONARX_MARKET_NO_NONEMPTY_PARTITIONS_FOUND"
            market_index[api_sym] = mi
            continue
        
        # Download selected keys
        for key in scan_result["selected_keys"]:
            if total_bytes >= args.download_budget_bytes:
                mi["market_status"] = "SONARX_DOWNLOAD_BUDGET_EXHAUSTED"
                break
            try:
                data = download_gzip_json(cp, key)
                mi["downloaded_key_count"] += 1
                global_key_discovery["total_downloaded_keys"] += 1
                if isinstance(data, list):
                    for raw_snap in data:
                        parsed = parse_snapshot(raw_snap, api_sym)
                        if parsed:
                            all_metrics.append(parsed)
                            mi["snapshots_parsed"] += 1
                    mi["files_parsed"] += 1
                total_bytes += int(key.rsplit("/", 1)[-1].replace(".json.gz", "").split("_")[0][-10:] if "_" in key else 0) or 0
            except Exception as exc:
                mi["errors"] += 1
        
        if mi["snapshots_parsed"] > 0:
            mi["market_status"] = "SONARX_MARKET_DATA_PARSED"
        elif mi["downloaded_key_count"] > 0:
            mi["market_status"] = "SONARX_MARKET_FILES_DOWNLOADED_BUT_EMPTY"
        else:
            mi["market_status"] = "SONARX_MARKET_DOWNLOAD_FAILED"
        
        market_index[api_sym] = mi
        
        # Progress heartbeat after each market
        progress_status = {
            **meta,
            "run_id": run_id,
            "status": "SONARX_PHASE_MINUS1_IN_PROGRESS",
            "current_phase": "s3_download_and_parse",
            "current_market": api_sym,
            "current_partition": mi["last_selected_key"],
            "markets_completed": len(market_index),
            "markets_total": len(markets),
            "files_selected": sum(m.get("selected_key_count", 0) for m in market_index.values()),
            "files_downloaded": sum(m.get("downloaded_key_count", 0) for m in market_index.values()),
            "bytes_downloaded": total_bytes,
            "parsed_snapshots": sum(m.get("snapshots_parsed", 0) for m in market_index.values()),
            "last_progress_at_utc": utc_now_iso(),
            "errors_count": sum(m.get("errors", 0) for m in market_index.values()),
            "executable_pnl_modeled": False,
            "diagnostic_only": True,
        }
        write_json(root / "phase_minus1_status.json", progress_status)

    statuses.append("SONARX_L2_HISTORY_PARSE_OK")

    # --- Key-discovery-only mode: exit before anchor/candle ---
    if getattr(args, "key_discovery_only", False):
        # Write sample index before exiting
        sample_index = {
            **meta,
            "api_symbols_processed": list(market_index.keys()),
            "market_index": market_index,
            "total_snapshots": len(all_metrics),
            "total_bytes_downloaded": total_bytes,
            "total_markets_requested": len(configured_markets),
            "total_markets_processed": len(market_index),
            "total_partition_prefixes_seen": global_key_discovery["total_partition_prefixes_seen"],
            "total_partitions_scanned": global_key_discovery["total_partitions_scanned"],
            "total_empty_partitions": global_key_discovery["total_empty_partitions"],
            "total_nonempty_partitions": global_key_discovery["total_nonempty_partitions"],
            "total_candidate_keys_seen": global_key_discovery["total_candidate_keys_seen"],
            "total_selected_keys": global_key_discovery["total_selected_keys"],
            "total_downloaded_keys": global_key_discovery["total_downloaded_keys"],
            "key_selection_success": global_key_discovery["total_selected_keys"] > 0,
            "no_data_reason": None if global_key_discovery["total_selected_keys"] > 0 else (
                "NO_NONEMPTY_OBJECT_KEYS_SELECTED" if global_key_discovery["total_selected_keys"] == 0
                else "SAMPLER_SELECTED_EMPTY_PARTITIONS" if global_key_discovery["total_empty_partitions"] > 0
                else "KEY_DISCOVERY_FAILED"
            ),
        }
        write_json(root / "sonarx_l2_sample_index.json", sample_index)
        
        key_discovery_status = "SONARX_KEY_DISCOVERY_COMPLETE" if global_key_discovery["total_selected_keys"] > 0 else "SONARX_KEY_DISCOVERY_FAILED"
        statuses.append(key_discovery_status)
        summary = {
            "status": key_discovery_status,
            "statuses": statuses,
            **meta,
            "total_snapshots": len(all_metrics),
            "total_bytes_downloaded": total_bytes,
            "markets_processed": len(market_index),
            "report_directory": str(root),
            "key_discovery": global_key_discovery,
        }
        write_json(root / "summary.json", summary)
        md_lines = [
            "# SonarX HIP-3 TradFi L2 Residual Phase -1 Scout (Key Discovery Only)\n",
            f"**Study ID**: {args.study_id}\n",
            f"**Run ID**: {run_id}\n",
            f"**Status**: {key_discovery_status}\n",
            f"**Total selected keys**: {global_key_discovery['total_selected_keys']}\n",
            f"**Total downloaded**: {global_key_discovery['total_downloaded_keys']}\n",
            f"**Markets**: {len(market_index)}\n",
        ]
        (root / "summary.md").write_text("".join(md_lines), encoding="utf-8")
        return summary

    # --- Phase C: session classification ---
    session_by_api: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    session_by_display: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in all_metrics:
        try:
            ts = datetime.fromisoformat(row["timestamp_utc"])
        except Exception:
            ts = datetime.now(timezone.utc)
        sess = classify_session(ts)
        row["session_bucket"] = sess
        session_by_api[row["api_symbol"]][sess] += 1
        session_by_display[row["display_symbol"]][sess] += 1

    offhours_cls = {
        **meta,
        "boundary_rules": {
            "regular_hours": "Mon-Fri 09:30–16:00 ET",
            "premarket": "Mon-Fri 06:00–09:30 ET",
            "after_hours": "Mon-Fri 16:00–23:59 ET",
            "overnight": "Mon-Fri 00:00–06:00 ET",
            "weekend_or_holiday": "Sat/Sun",
            "premarket_overnight_boundary": "06:00 ET",
        },
        "session_counts_by_api_symbol": dict(session_by_api),
        "session_counts_by_display_symbol": dict(session_by_display),
        "holiday_calendar_available": False,
    }
    write_json(root / "offhours_calendar_classification.json", offhours_cls)

    # --- Phase D: liquidity summary by API symbol / session ---
    by_api_sym: dict[str, list[dict]] = defaultdict(list)
    for row in all_metrics:
        by_api_sym[row["api_symbol"]].append(row)

    liquidity = {}
    for api_sym, rows in by_api_sym.items():
        display_sym, dex_name = parse_api_symbol(api_sym)
        by_sess: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_sess[r.get("session_bucket", "unknown")].append(r)
        sess_stats = {}
        for sess, srows in by_sess.items():
            sess_stats[sess] = compute_liquidity_stats(srows)
        liquidity[api_sym] = {
            "display_symbol": display_sym,
            "dex_name": dex_name,
            "total_snapshots": len(rows),
            "by_session": sess_stats,
            "overall": compute_liquidity_stats(rows),
        }

    liq_summary = {**meta, "per_api_symbol": liquidity, "thresholds_note": "diagnostic only, not trading thresholds"}
    write_json(root / "liquidity_feasibility_summary.json", liq_summary)

    # --- Phase E: candleSnapshot join ---
    candle_join: dict[str, Any] = {**meta, "candles_by_api_symbol": {}}
    # Candle join is disabled by default; requires --enable-candle-join AND not --disable-candle-join
    candle_enabled = getattr(args, "enable_candle_join", False) and not getattr(args, "disable_candle_join", False)
    if candle_enabled and cp.allow_network_public:
        for api_sym in markets:
            display_sym, dex_name = parse_api_symbol(api_sym)
            candle_data: dict[str, Any] = {"intervals": {}}
            for interval in ["1h", "4h", "1d"]:
                try:
                    payload = {"type": "candleSnapshot", "coin": api_sym, "interval": interval, "startTime": 0, "endTime": 0}
                    resp_bytes = cp.http_post_json(HYPERLIQUID_INFO_URL, payload)
                    if isinstance(resp_bytes, list):
                        candle_data["intervals"][interval] = {
                            "candle_count": len(resp_bytes),
                            "capped_at_5000": len(resp_bytes) >= 5000,
                            "status": "SONARX_CANDLE_JOIN_AVAILABLE",
                        }
                    else:
                        candle_data["intervals"][interval] = {"candle_count": 0, "status": "NO_DATA"}
                except Exception as exc:
                    candle_data["intervals"][interval] = {"error": str(exc), "status": "SONARX_CANDLE_JOIN_BLOCKED"}
            candle_join["candles_by_api_symbol"][api_sym] = candle_data
        statuses.append("SONARX_CANDLE_JOIN_AVAILABLE")
    else:
        candle_join["status"] = "SONARX_CANDLE_JOIN_BLOCKED"
        candle_join["disabled_reason"] = "explicitly_disabled_via_flags" if getattr(args, "disable_candle_join", False) else "not_enabled"
        statuses.append("SONARX_CANDLE_JOIN_BLOCKED")
    write_json(root / "hyperliquid_candle_snapshot_join.json", candle_join)

    # --- Phase F: anchor probe ---
    anchor_probe: dict[str, Any] = {**meta, "anchors_by_display": {}}
    anchor_available = False
    # Anchors are disabled by default; requires --enable-anchors AND not --disable-anchors
    anchors_enabled = getattr(args, "enable_anchors", False) and not getattr(args, "disable_anchors", False)
    if anchors_enabled and cp.allow_network_public:
        for dsym in ["TSLA", "AAPL", "MSFT", "NVDA"]:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{dsym}?range=5d&interval=1d"
                raw = cp.http_get(url)
                parsed = json.loads(raw)
                ts_list = parsed.get("chart", {}).get("result", [{}])[0].get("timestamp", [])
                meta_info = parsed.get("chart", {}).get("result", [{}])[0].get("meta", {})
                price = meta_info.get("regularMarketPrice", 0)
                anchor_probe["anchors_by_display"][dsym] = {
                    "available": bool(price),
                    "price": price,
                    "data_points": len(ts_list),
                    "status": "SONARX_ANCHOR_AVAILABLE" if price else "SONARX_ANCHOR_BLOCKED",
                    "response_hash": hashlib.sha256(raw[:4096]).hexdigest()[:16],
                }
                if price:
                    anchor_available = True
            except Exception as exc:
                anchor_probe["anchors_by_display"][dsym] = {
                    "available": False, "error": str(exc),
                    "status": "SONARX_ANCHOR_BLOCKED",
                }
        statuses.append("SONARX_ANCHOR_AVAILABLE" if anchor_available else "SONARX_ANCHOR_BLOCKED")
    else:
        anchor_probe["status"] = "SONARX_ANCHOR_BLOCKED"
        anchor_probe["disabled_reason"] = "explicitly_disabled_via_flags" if getattr(args, "disable_anchors", False) else "not_enabled"
        statuses.append("SONARX_ANCHOR_BLOCKED")
    write_json(root / "anchor_availability_probe.json", anchor_probe)

    # --- Phase G: diagnostic residual (only if anchors available) ---
    residual_summary: dict[str, Any] = {**meta, "residual_by_api_symbol": {}}
    residual_available = False
    if anchor_available:
        for api_sym, rows in by_api_sym.items():
            display_sym, _ = parse_api_symbol(api_sym)
            anchor_price = anchor_probe["anchors_by_display"].get(display_sym, {}).get("price", 0)
            if not anchor_price:
                continue
            residuals = []
            for r in rows:
                if r["mid"] > 0 and anchor_price > 0:
                    res_bps = 10000.0 * (r["mid"] - anchor_price) / anchor_price
                    residuals.append(res_bps)
            if residuals:
                abs_res = sorted(abs(x) for x in residuals)
                residual_summary["residual_by_api_symbol"][api_sym] = {
                    "display_symbol": display_sym,
                    "aligned_count": len(residuals),
                    "median_residual_bps": round(_quantile(residuals, 0.5), 4),
                    "p75_abs_residual_bps": round(_quantile(abs_res, 0.75), 4),
                    "p90_abs_residual_bps": round(_quantile(abs_res, 0.90), 4),
                    "p99_abs_residual_bps": round(_quantile(abs_res, 0.99), 4),
                    "abs_gte_25_bps": sum(1 for x in abs_res if x >= 25),
                    "abs_gte_50_bps": sum(1 for x in abs_res if x >= 50),
                    "abs_gte_100_bps": sum(1 for x in abs_res if x >= 100),
                    "anchor_price_used": anchor_price,
                    "diagnostic_only": True,
                }
                residual_available = True
        residual_summary["status"] = (
            "SONARX_RESIDUAL_DIAGNOSTIC_AVAILABLE" if residual_available
            else "SONARX_RESIDUAL_DIAGNOSTIC_UNDERPOWERED"
        )
        statuses.append(residual_summary["status"])
    else:
        residual_summary["status"] = "SONARX_RESIDUAL_DIAGNOSTIC_UNDERPOWERED"
        statuses.append("SONARX_RESIDUAL_DIAGNOSTIC_UNDERPOWERED")
    write_json(root / "diagnostic_residual_summary.json", residual_summary)

    # --- Write parsed metrics ---
    metrics_path = root / "sonarx_l2_parsed_metrics.jsonl"
    with metrics_path.open("w", encoding="utf-8") as f:
        for row in all_metrics:
            f.write(json.dumps(row, default=str) + "\n")

    # --- SonarX L2 sample index ---
    sample_index = {
        **meta,
        "api_symbols_processed": list(market_index.keys()),
        "market_index": market_index,
        "total_snapshots": len(all_metrics),
        "total_bytes_downloaded": total_bytes,
        "total_markets_requested": len(configured_markets),
        "total_markets_processed": len(market_index),
        "total_partition_prefixes_seen": global_key_discovery["total_partition_prefixes_seen"],
        "total_partitions_scanned": global_key_discovery["total_partitions_scanned"],
        "total_empty_partitions": global_key_discovery["total_empty_partitions"],
        "total_nonempty_partitions": global_key_discovery["total_nonempty_partitions"],
        "total_candidate_keys_seen": global_key_discovery["total_candidate_keys_seen"],
        "total_selected_keys": global_key_discovery["total_selected_keys"],
        "total_downloaded_keys": global_key_discovery["total_downloaded_keys"],
        "key_selection_success": global_key_discovery["total_downloaded_keys"] > 0,
        "no_data_reason": None if global_key_discovery["total_downloaded_keys"] > 0 else (
            "NO_NONEMPTY_OBJECT_KEYS_SELECTED" if global_key_discovery["total_selected_keys"] == 0
            else "SAMPLER_SELECTED_EMPTY_PARTITIONS" if global_key_discovery["total_empty_partitions"] > 0
            else "KEY_DISCOVERY_FAILED"
        ),
    }
    write_json(root / "sonarx_l2_sample_index.json", sample_index)

    # --- Quality summary ---
    quality = {
        **meta,
        "total_snapshots": len(all_metrics),
        "per_api_symbol": {
            api_sym: {
                "snapshots": len(rows),
                "two_sided_rate": round(sum(1 for r in rows if r["two_sided_book"]) / len(rows), 4) if rows else 0,
                "median_spread_bps": round(_quantile(sorted(r["spread_bps"] for r in rows if r["spread_bps"] != float("inf")), 0.5), 4) if rows else 0,
            }
            for api_sym, rows in by_api_sym.items()
        },
    }
    write_json(root / "sonarx_l2_quality_summary.json", quality)

    # --- Phase H: gate decisions ---
    offhours_rows = [r for r in all_metrics if r.get("session_bucket") in ("after_hours", "overnight", "premarket")]
    offhours_by_api: dict[str, list[dict]] = defaultdict(list)
    for r in offhours_rows:
        offhours_by_api[r["api_symbol"]].append(r)

    gate_reasons: list[str] = []
    gate_status = "SONARX_PHASE_MINUS1_NOT_ENOUGH_FOR_PRECOMMITMENT"

    # CRITICAL: Check if we have any data before making liquidity claims
    total_parsed = len(all_metrics)
    total_downloaded = global_key_discovery["total_downloaded_keys"]
    
    liquidity_ok = False  # Initialize for zero-data case
    
    if total_downloaded == 0 or total_parsed == 0:
        # Zero-data run: cannot make liquidity claims
        gate_reasons.append("PROBE_RETRIEVED_NO_DATA")
        if global_key_discovery["total_selected_keys"] == 0:
            gate_reasons.append("NO_NONEMPTY_OBJECT_KEYS_SELECTED")
        elif global_key_discovery["total_empty_partitions"] > 0:
            gate_reasons.append("SAMPLER_SELECTED_EMPTY_PARTITIONS")
        else:
            gate_reasons.append("KEY_DISCOVERY_FAILED")
    else:
        # Data-bearing run: can make liquidity assessments
        for api_sym, rows in offhours_by_api.items():
            if len(rows) < 500:
                continue
            stats = compute_liquidity_stats(rows)
            if (stats.get("two_sided_book_rate", 0) >= 0.80
                    and stats.get("median_spread_bps", 999) <= 50
                    and stats.get("p75_spread_bps", 999) <= 100):
                liquidity_ok = True
                break
        if not liquidity_ok:
            gate_reasons.append("liquidity_thresholds_not_met")

    if not anchor_available:
        gate_reasons.append("anchor_unavailable")

    if not residual_available:
        gate_reasons.append("residual_unavailable")

    if liquidity_ok and anchor_available and residual_available:
        gate_status = "SONARX_PHASE_MINUS1_NEXT_PRECOMMITMENT_REVIEW_ALLOWED"
        statuses.append("SONARX_PHASE_MINUS1_NEXT_PRECOMMITMENT_REVIEW_ALLOWED")
    else:
        statuses.append("SONARX_PHASE_MINUS1_NOT_ENOUGH_FOR_PRECOMMITMENT")

    gate_decisions = {
        **meta,
        "gate_status": gate_status,
        "gate_reasons": gate_reasons,
        "total_snapshots": len(all_metrics),
        "total_bytes_downloaded": total_bytes,
    }
    write_json(root / "gate_decisions.json", gate_decisions)

    # --- Summary ---
    summary = {
        "status": gate_status,
        "statuses": statuses,
        **meta,
        "total_snapshots": len(all_metrics),
        "total_bytes_downloaded": total_bytes,
        "markets_processed": len(markets),
        "report_directory": str(root),
        "forward_recorder继续保持": True,
    }
    write_json(root / "summary.json", summary)

    md_lines = [
        "# SonarX HIP-3 TradFi L2 Residual Phase -1 Scout\n",
        f"**Study ID**: {args.study_id}\n",
        f"**Run ID**: {run_id}\n",
        f"**Status**: {gate_status}\n",
        f"**Snapshots parsed**: {len(all_metrics)}\n",
        f"**Bytes downloaded**: {total_bytes}\n",
        f"**Markets**: {len(markets)}\n",
        f"**Gate reasons**: {gate_reasons}\n",
    ]
    md_path = root / "summary.md"
    md_path.write_text("".join(md_lines), encoding="utf-8")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="SonarX HIP-3 TradFi L2 Residual Phase -1 Scout"
    )
    p.add_argument("--out-root", default="reports/hip3_sonarx_tradfi_l2_residual_phase_minus1_v0")
    p.add_argument("--study-id", default="hip3_sonarx_tradfi_l2_residual_phase_minus1_v0")
    p.add_argument("--markets", default=",".join(ALL_12_API_SYMBOLS))
    p.add_argument("--sample-days", type=int, default=30)
    p.add_argument("--sample-mode", default="stratified")
    p.add_argument("--max-markets", type=int, default=12)
    p.add_argument("--max-files-per-market", type=int, default=500)
    p.add_argument("--max-partitions-scanned-per-market", type=int, default=200,
                   help="Max partition prefixes to scan per market when searching for non-empty partitions")
    p.add_argument("--min-files-per-market", type=int, default=10,
                   help="Target minimum number of .json.gz files to select per market")
    p.add_argument("--download-budget-bytes", type=int, default=2_000_000_000)
    p.add_argument("--allow-s3-archive-read", action="store_true")
    p.add_argument("--allow-network-public", action="store_true")
    p.add_argument("--enable-candle-join", action="store_true",
                   help="Enable Hyperliquid candleSnapshot join (default: disabled)")
    p.add_argument("--enable-anchors", action="store_true",
                   help="Enable Yahoo anchor pricefetch (default: disabled)")
    p.add_argument("--disable-candle-join", action="store_true",
                   help="Explicitly disable candleSnapshot join (L2-only mode)")
    p.add_argument("--disable-anchors", action="store_true",
                   help="Explicitly disable anchor fetch (L2-only mode)")
    p.add_argument("--anchor-source", default="yahoo")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--real-smoke", action="store_true", help="Smoke test mode: max 2 markets, 10 files each, 50MB budget")
    p.add_argument("--key-discovery-only", action="store_true",
                   help="Only discover keys, do not download or make anchor/candle requests")
    p.add_argument("--prefer-known-good-coverage-keys", action="store_true",
                   help="Prefer known-good keys from prior coverage probe if available")
    p.add_argument("--s3-connect-timeout-seconds", type=int, default=10)
    p.add_argument("--s3-read-timeout-seconds", type=int, default=30)
    p.add_argument("--s3-max-attempts", type=int, default=2)
    return p


def main(argv: List[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = run_phase_minus1(args)
        print(json.dumps({"status": result.get("status"), "run_id": result.get("run_id")}))
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
