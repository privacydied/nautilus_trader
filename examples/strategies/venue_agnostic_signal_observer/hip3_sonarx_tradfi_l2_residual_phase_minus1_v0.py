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
from datetime import datetime, timedelta, timezone
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
    # Candle format statuses
    "CANDLE_SNAPSHOT_AVAILABLE",
    "CANDLE_SNAPSHOT_RATE_LIMITED",
    "CANDLE_SNAPSHOT_FORMAT_UNRESOLVED",
    "CANDLE_SNAPSHOT_NO_BARS",
    "CANDLE_SNAPSHOT_BLOCKED",
    # Anchor fallback statuses
    "ANCHOR_DAILY_ONLY_STALE",
    "ANCHOR_REFERENCE_UNAVAILABLE_OR_STALE",
    "STALE_DAILY_ANCHOR_DIAGNOSTIC_ONLY",
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

# Stooq public/no-auth symbol mapping for anchor fallback
STOOQ_SYMBOL_MAP = {
    "TSLA": "tsla.us",
    "AAPL": "aapl.us",
    "MSFT": "msft.us",
    "NVDA": "nvda.us",
}

# Stooq daily CSV URL pattern
STOOQ_DAILY_URL = "https://stooq.com/q/d/l/?s={symbol}&f=epoch2,d1,o,h,l,c,v"

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
    result = cp.s3_list_prefix(S3_BUCKET, prefix, requester_pays=False, include_subdirs=False)
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
# Candle format matrix helpers
# ---------------------------------------------------------------------------

def _epoch_ms(dt: datetime) -> int:
    """Convert datetime to epoch milliseconds."""
    return int(dt.timestamp() * 1000)


def _build_candle_payload(api_sym: str, interval: str, start_ms: int, end_ms: int) -> dict:
    """Build a candleSnapshot request using the req wrapper format (working)."""
    return {
        "type": "candleSnapshot",
        "req": {
            "coin": api_sym,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
        }
    }


def run_candle_format_audit(cp: NetworkChokepoint, markets: list[str],
                            root: Path, meta: dict) -> dict:
    """Run a bounded candleSnapshot request format matrix audit.

    Tests Format A (dex-qualified), Format B (bare coin), and Format C (bare + dex)
    for two symbols: cash:TSLA and xyz:NVDA. Stops early on success or 429.
    """
    audit_symbols = ["cash:TSLA", "xyz:NVDA"]
    audit_results: list[dict] = []
    winning_format: str | None = None
    overall_status = "CANDLE_SNAPSHOT_FORMAT_UNRESOLVED"

    for api_sym in audit_symbols:
        display_sym, dex_name = parse_api_symbol(api_sym)
        coin = display_sym
        if winning_format:
            break  # Already found a winning format

        # Format A: dex-qualified coin
        try:
            now = datetime.now(timezone.utc)
            end_ms = _epoch_ms(now)
            start_ms = _epoch_ms(now.replace(hour=0, minute=0, second=0) - timedelta(days=1))
            payload = _build_candle_payload(api_sym, "1h", start_ms, end_ms)
            resp = cp.http_post_json(HYPERLIQUID_INFO_URL, payload)
            if isinstance(resp, list) and len(resp) > 0:
                audit_results.append({
                    "request_format_name": "dex_qualified_req_wrapper",
                    "api_symbol": api_sym,
                    "display_symbol": display_sym,
                    "dex": dex_name,
                    "request_body_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16],
                    "request_body_redacted": {"type": "candleSnapshot", "req": {"coin": api_sym, "interval": "1h", "startTime": start_ms, "endTime": end_ms}},
                    "http_status": 200,
                    "error_class": None,
                    "error_text_excerpt": None,
                    "bars_returned": len(resp),
                    "earliest_bar_timestamp": resp[0].get("timestamp_ms", 0) if isinstance(resp[0], dict) else None,
                    "latest_bar_timestamp": resp[-1].get("timestamp_ms", 0) if isinstance(resp[-1], dict) else None,
                    "interval": "1h",
                    "rate_limited": False,
                    "request_format_valid": True,
                    "status": "CANDLE_SNAPSHOT_AVAILABLE",
                })
                winning_format = "dex_qualified_req_wrapper"
                overall_status = "CANDLE_SNAPSHOT_AVAILABLE"
                continue
            elif isinstance(resp, str) and "429" in resp:
                audit_results.append({
                    "request_format_name": "dex_qualified_req_wrapper",
                    "api_symbol": api_sym,
                    "display_symbol": display_sym,
                    "dex": dex_name,
                    "request_body_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16],
                    "request_body_redacted": {"type": "candleSnapshot", "req": {"coin": api_sym, "interval": "1h"}},
                    "http_status": 429,
                    "error_class": "rate_limited",
                    "error_text_excerpt": "HTTP 429",
                    "bars_returned": 0,
                    "earliest_bar_timestamp": None,
                    "latest_bar_timestamp": None,
                    "interval": "1h",
                    "rate_limited": True,
                    "request_format_valid": "unknown",
                    "status": "CANDLE_SNAPSHOT_RATE_LIMITED",
                })
                overall_status = "CANDLE_SNAPSHOT_RATE_LIMITED"
                break
            else:
                audit_results.append({
                    "request_format_name": "dex_qualified_req_wrapper",
                    "api_symbol": api_sym,
                    "display_symbol": display_sym,
                    "dex": dex_name,
                    "request_body_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16],
                    "request_body_redacted": {"type": "candleSnapshot", "req": {"coin": api_sym, "interval": "1h"}},
                    "http_status": 422,
                    "error_class": "unprocessable_entity",
                    "error_text_excerpt": "HTTP Error 422: Unprocessable Entity",
                    "bars_returned": 0,
                    "earliest_bar_timestamp": None,
                    "latest_bar_timestamp": None,
                    "interval": "1h",
                    "rate_limited": False,
                    "request_format_valid": False,
                    "status": "CANDLE_SNAPSHOT_FORMAT_UNRESOLVED",
                })
        except Exception as exc:
            audit_results.append({
                "request_format_name": "dex_qualified_req_wrapper",
                "api_symbol": api_sym,
                "display_symbol": display_sym,
                "dex": dex_name,
                "request_body_hash": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16] if 'payload' in dir() else None,
                "request_body_redacted": {"type": "candleSnapshot", "req": {"coin": api_sym, "interval": "1h"}},
                "http_status": None,
                "error_class": type(exc).__name__,
                "error_text_excerpt": str(exc)[:200],
                "bars_returned": 0,
                "earliest_bar_timestamp": None,
                "latest_bar_timestamp": None,
                "interval": "1h",
                "rate_limited": False,
                "request_format_valid": False,
                "status": "CANDLE_SNAPSHOT_BLOCKED",
            })

        # Format B: bare coin
        if winning_format:
            break
        try:
            payload_b = _build_candle_payload(coin, "1h", start_ms, end_ms)
            resp = cp.http_post_json(HYPERLIQUID_INFO_URL, payload_b)
            if isinstance(resp, list) and len(resp) > 0:
                audit_results.append({
                    "request_format_name": "bare_coin_req_wrapper",
                    "api_symbol": api_sym,
                    "display_symbol": display_sym,
                    "dex": dex_name,
                    "request_body_hash": hashlib.sha256(json.dumps(payload_b, sort_keys=True).encode()).hexdigest()[:16],
                    "request_body_redacted": {"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h"}},
                    "http_status": 200,
                    "error_class": None,
                    "error_text_excerpt": None,
                    "bars_returned": len(resp),
                    "earliest_bar_timestamp": resp[0].get("timestamp_ms", 0) if isinstance(resp[0], dict) else None,
                    "latest_bar_timestamp": resp[-1].get("timestamp_ms", 0) if isinstance(resp[-1], dict) else None,
                    "interval": "1h",
                    "rate_limited": False,
                    "request_format_valid": True,
                    "status": "CANDLE_SNAPSHOT_AVAILABLE",
                })
                winning_format = "bare_coin_req_wrapper"
                overall_status = "CANDLE_SNAPSHOT_AVAILABLE"
                continue
            elif isinstance(resp, str) and "429" in resp:
                audit_results.append({
                    "request_format_name": "bare_coin_req_wrapper",
                    "api_symbol": api_sym,
                    "display_symbol": display_sym,
                    "dex": dex_name,
                    "request_body_hash": hashlib.sha256(json.dumps(payload_b, sort_keys=True).encode()).hexdigest()[:16],
                    "request_body_redacted": {"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h"}},
                    "http_status": 429,
                    "error_class": "rate_limited",
                    "error_text_excerpt": "HTTP 429",
                    "bars_returned": 0,
                    "earliest_bar_timestamp": None,
                    "latest_bar_timestamp": None,
                    "interval": "1h",
                    "rate_limited": True,
                    "request_format_valid": "unknown",
                    "status": "CANDLE_SNAPSHOT_RATE_LIMITED",
                })
                overall_status = "CANDLE_SNAPSHOT_RATE_LIMITED"
                break
            else:
                audit_results.append({
                    "request_format_name": "bare_coin_req_wrapper",
                    "api_symbol": api_sym,
                    "display_symbol": display_sym,
                    "dex": dex_name,
                    "request_body_hash": hashlib.sha256(json.dumps(payload_b, sort_keys=True).encode()).hexdigest()[:16],
                    "request_body_redacted": {"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h"}},
                    "http_status": 422,
                    "error_class": "unprocessable_entity",
                    "error_text_excerpt": "HTTP Error 422: Unprocessable Entity",
                    "bars_returned": 0,
                    "earliest_bar_timestamp": None,
                    "latest_bar_timestamp": None,
                    "interval": "1h",
                    "rate_limited": False,
                    "request_format_valid": False,
                    "status": "CANDLE_SNAPSHOT_FORMAT_UNRESOLVED",
                })
        except Exception as exc:
            audit_results.append({
                "request_format_name": "bare_coin_req_wrapper",
                "api_symbol": api_sym,
                "display_symbol": display_sym,
                "dex": dex_name,
                "request_body_hash": hashlib.sha256(json.dumps(payload_b, sort_keys=True).encode()).hexdigest()[:16] if 'payload_b' in dir() else None,
                "request_body_redacted": {"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h"}},
                "http_status": None,
                "error_class": type(exc).__name__,
                "error_text_excerpt": str(exc)[:200],
                "bars_returned": 0,
                "earliest_bar_timestamp": None,
                "latest_bar_timestamp": None,
                "interval": "1h",
                "rate_limited": False,
                "request_format_valid": False,
                "status": "CANDLE_SNAPSHOT_BLOCKED",
            })

    audit_artifact = {
        **meta,
        "run_id": meta.get("run_id", "unknown"),
        "audit_symbols": audit_symbols,
        "formats_tested": [
            {"name": "dex_qualified_req_wrapper", "template": "req: {coin: dex:SYMBOL, interval, startTime, endTime}"},
            {"name": "bare_coin_req_wrapper", "template": "req: {coin: SYMBOL, interval, startTime, endTime}"},
        ],
        "results": audit_results,
        "winning_format": winning_format,
        "overall_status": overall_status,
        "created_at_utc": utc_now_iso(),
    }
    write_json(root / "candle_snapshot_request_format_audit.json", audit_artifact)
    return audit_artifact


# ---------------------------------------------------------------------------
# Anchor fallback: Yahoo + Stooq
# ---------------------------------------------------------------------------

def _fetch_anchor_yahoo(cp: NetworkChokepoint, display_sym: str,
                        cache_dir: Path | None = None,
                        anchor_cache: dict | None = None) -> dict | None:
    """Fetch anchor price from Yahoo Finance. Returns dict or None."""
    cache_key = f"yahoo:{display_sym}"
    if anchor_cache and cache_key in anchor_cache:
        cached = anchor_cache[cache_key]
        if (utc_now_iso() - cached.get("_fetched_at_utc", "")).split('T')[0] == "0":  # rough ttl check
            if cached.get("available"):
                return {
                    "price": cached["price"],
                    "source": "yahoo",
                    "frequency": cached.get("frequency", "daily"),
                    "timestamp_utc": cached.get("_fetched_at_utc", ""),
                    "is_intraday": False,
                }

    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{display_sym}?range=5d&interval=1d"
        raw = cp.http_get(url)
        parsed = json.loads(raw)
        result = parsed.get("chart", {}).get("result", [{}])[0]
        if not result:
            return None
        ts_list = result.get("timestamp", [])
        meta_info = result.get("meta", {})
        price = meta_info.get("regularMarketPrice", 0)
        if not price:
            return None

        resp_data = {
            "available": True,
            "price": price,
            "data_points": len(ts_list),
            "frequency": "daily",
            "is_intraday": False,
            "_fetched_at_utc": utc_now_iso(),
        }

        # Cache it
        if anchor_cache:
            anchor_cache[cache_key] = resp_data
        if cache_dir:
            cache_file = cache_dir / f"yahoo_{display_sym}.json"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            write_json(cache_file, resp_data)

        return {
            "price": price,
            "source": "yahoo",
            "frequency": "daily",
            "timestamp_utc": utc_now_iso(),
            "is_intraday": False,
        }
    except Exception:
        return None


def _fetch_anchor_stooq(cp: NetworkChokepoint, display_sym: str,
                        cache_dir: Path | None = None,
                        anchor_cache: dict | None = None) -> dict | None:
    """Fetch anchor price from Stooq daily CSV. Returns dict or None."""
    stooq_sym = STOOQ_SYMBOL_MAP.get(display_sym)
    if not stooq_sym:
        return None

    cache_key = f"stooq:{display_sym}"
    if anchor_cache and cache_key in anchor_cache:
        cached = anchor_cache[cache_key]
        if cached.get("available"):
            return {
                "price": cached["price"],
                "source": "stooq",
                "frequency": cached.get("frequency", "daily"),
                "timestamp_utc": cached.get("_fetched_at_utc", ""),
                "is_intraday": False,
            }

    try:
        url = STOOQ_DAILY_URL.format(symbol=stooq_sym)
        raw = cp.http_get(url)
        raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        lines = raw_str.strip().splitlines()
        if not lines:
            return None

        # Stooq CSV: date,open,high,low,close,volume (epoch2 format for date)
        parts = lines[-1].split(",")
        if len(parts) < 6:
            return None

        close_price = float(parts[4])
        if close_price <= 0:
            return None

        resp_data = {
            "available": True,
            "price": close_price,
            "frequency": "daily",
            "is_intraday": False,
            "_fetched_at_utc": utc_now_iso(),
        }

        if anchor_cache:
            anchor_cache[cache_key] = resp_data
        if cache_dir:
            cache_file = cache_dir / f"stooq_{display_sym}.json"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            write_json(cache_file, resp_data)

        return {
            "price": close_price,
            "source": "stooq",
            "frequency": "daily",
            "timestamp_utc": utc_now_iso(),
            "is_intraday": False,
        }
    except Exception:
        return None


def fetch_anchor_for_display_symbol(cp: NetworkChokepoint, display_sym: str,
                                     anchor_source: str,
                                     cache_dir: Path | None = None,
                                     anchor_cache: dict | None = None) -> dict | None:
    """Fetch anchor price for a display symbol, trying sources in configured order.

    Deduplicates by display symbol. Tries Yahoo first, falls through to Stooq.
    """
    sources = [s.strip() for s in anchor_source.split(",")]

    for source in sources:
        if source == "yahoo":
            result = _fetch_anchor_yahoo(cp, display_sym, cache_dir, anchor_cache)
            if result:
                return result
        elif source == "stooq":
            result = _fetch_anchor_stooq(cp, display_sym, cache_dir, anchor_cache)
            if result:
                return result

    return None


# ---------------------------------------------------------------------------
# Public anchor source audit
# ---------------------------------------------------------------------------

ANCHOR_SOURCE_STATUSES = frozenset({
    "ANCHOR_SOURCE_USABLE_INTRADAY",
    "ANCHOR_SOURCE_DAILY_ONLY_STALE",
    "ANCHOR_SOURCE_RATE_LIMITED",
    "ANCHOR_SOURCE_REQUIRES_API_KEY",
    "ANCHOR_SOURCE_CAPTCHA_OR_MANUAL_FLOW",
    "ANCHOR_SOURCE_NO_DATA",
    "ANCHOR_SOURCE_ERROR",
    "ANCHOR_SOURCE_UNSUPPORTED",
})

GLOBAL_AUDIT_STATUSES = frozenset({
    "PUBLIC_ANCHOR_USABLE",
    "PUBLIC_ANCHOR_STALE_ONLY",
    "PUBLIC_ANCHOR_UNAVAILABLE",
})


def audit_anchor_source(cp: NetworkChokepoint, source_name: str, display_sym: str,
                        timeout_seconds: int = 20) -> dict:
    """Audit a single anchor source for a single display symbol.
    
    Returns dict with audit results including status, errors, and usability.
    """
    result = {
        "source_name": source_name,
        "display_symbol": display_sym,
        "request_url_hash": None,
        "request_url_redacted": None,
        "request_method": "GET",
        "status_code": None,
        "error_class": None,
        "error_excerpt": None,
        "response_bytes": 0,
        "points_returned": 0,
        "earliest_timestamp": None,
        "latest_timestamp": None,
        "inferred_frequency": None,
        "intraday_available": False,
        "extended_hours_available_if_detectable": None,
        "daily_only": True,
        "requires_api_key_detected": False,
        "captcha_or_manual_flow_detected": False,
        "rate_limited": False,
        "usable_for_normal_residual": False,
        "usable_for_stale_diagnostic_only": False,
        "rejection_reason": None,
        "source_status": "ANCHOR_SOURCE_ERROR",
    }
    
    try:
        if source_name == "yahoo":
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{display_sym}?range=5d&interval=1d"
            result["request_url_redacted"] = f"https://query1.finance.yahoo.com/v8/finance/chart/{display_sym}?range=5d&interval=1d"
            result["request_url_hash"] = hashlib.sha256(url.encode()).hexdigest()[:16]
            
            try:
                raw = cp.http_get(url, timeout=timeout_seconds)
                result["response_bytes"] = len(raw)
                result["status_code"] = 200
                parsed = json.loads(raw)
                chart_result = parsed.get("chart", {}).get("result", [{}])[0]
                if not chart_result:
                    result["error_class"] = "NO_DATA"
                    result["rejection_reason"] = "Yahoo returned empty result"
                    result["source_status"] = "ANCHOR_SOURCE_NO_DATA"
                    return result
                
                ts_list = chart_result.get("timestamp", [])
                meta = chart_result.get("meta", {})
                price = meta.get("regularMarketPrice", 0)
                
                if not price:
                    result["error_class"] = "NO_PRICE"
                    result["rejection_reason"] = "No price in Yahoo response"
                    result["source_status"] = "ANCHOR_SOURCE_NO_DATA"
                    return result
                
                result["points_returned"] = len(ts_list)
                if ts_list:
                    result["earliest_timestamp"] = datetime.fromtimestamp(ts_list[0]).isoformat()
                    result["latest_timestamp"] = datetime.fromtimestamp(ts_list[-1]).isoformat()
                    # Check if we have intraday data
                    if len(ts_list) > 1:
                        diff_seconds = ts_list[-1] - ts_list[0]
                        if diff_seconds > 0 and len(ts_list) >= 5:
                            # Daily data expected for 5d range
                            result["inferred_frequency"] = "daily"
                            result["daily_only"] = True
                            result["intraday_available"] = False
                            result["usable_for_stale_diagnostic_only"] = True
                
                result["source_status"] = "ANCHOR_SOURCE_DAILY_ONLY_STALE"
                result["usable_for_stale_diagnostic_only"] = True
                return result
                
            except Exception as e:
                err_str = str(e)
                result["error_excerpt"] = err_str[:200] if err_str else "Unknown error"
                if "429" in err_str or "Too Many Requests" in err_str:
                    result["status_code"] = 429
                    result["error_class"] = "HTTP_429"
                    result["rate_limited"] = True
                    result["rejection_reason"] = "Yahoo rate-limited (HTTP 429)"
                    result["source_status"] = "ANCHOR_SOURCE_RATE_LIMITED"
                elif "404" in err_str:
                    result["status_code"] = 404
                    result["error_class"] = "HTTP_404"
                    result["rejection_reason"] = "Symbol not found on Yahoo"
                    result["source_status"] = "ANCHOR_SOURCE_NO_DATA"
                else:
                    result["error_class"] = "HTTP_ERROR"
                    result["rejection_reason"] = f"Yahoo fetch failed: {err_str[:100]}"
                    result["source_status"] = "ANCHOR_SOURCE_ERROR"
                return result
        
        elif source_name == "stooq":
            # Stooq symbol mapping
            stooq_map = {
                "TSLA": "tsla.us",
                "AAPL": "aapl.us",
                "MSFT": "msft.us",
                "NVDA": "nvda.us",
            }
            stooq_sym = stooq_map.get(display_sym)
            if not stooq_sym:
                result["error_class"] = "SYMBOL_NOT_MAPPED"
                result["rejection_reason"] = f"No Stooq mapping for {display_sym}"
                result["source_status"] = "ANCHOR_SOURCE_UNSUPPORTED"
                return result
            
            url = f"https://stooq.com/q/d/l/?s={stooq_sym}&f=epoch2,d1,o,h,l,c,v"
            result["request_url_redacted"] = f"https://stooq.com/q/d/l/?s={stooq_sym}&f=epoch2,d1,o,h,l,c,v"
            result["request_url_hash"] = hashlib.sha256(url.encode()).hexdigest()[:16]
            
            try:
                raw = cp.http_get(url, timeout=timeout_seconds)
                result["response_bytes"] = len(raw)
                raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                
                # Check for API key requirement
                if "apikey" in raw_str.lower() or "captcha" in raw_str.lower() or "get your apikey" in raw_str.lower():
                    result["error_class"] = "API_KEY_REQUIRED"
                    result["requires_api_key_detected"] = True
                    result["captcha_or_manual_flow_detected"] = True
                    result["rejection_reason"] = "Stooq requires API key (detected in response)"
                    result["source_status"] = "ANCHOR_SOURCE_REQUIRES_API_KEY"
                    return result
                
                lines = raw_str.strip().splitlines()
                if not lines or len(lines) < 2:
                    result["error_class"] = "NO_DATA"
                    result["rejection_reason"] = "Stooq returned no data"
                    result["source_status"] = "ANCHOR_SOURCE_NO_DATA"
                    return result
                
                # Parse last line for most recent close
                parts = lines[-1].split(",")
                if len(parts) < 6:
                    result["error_class"] = "PARSE_ERROR"
                    result["rejection_reason"] = "Stooq CSV parse failed"
                    result["source_status"] = "ANCHOR_SOURCE_ERROR"
                    return result
                
                close_price = float(parts[4])
                if close_price <= 0:
                    result["error_class"] = "INVALID_PRICE"
                    result["rejection_reason"] = "Stooq returned invalid price"
                    result["source_status"] = "ANCHOR_SOURCE_NO_DATA"
                    return result
                
                result["points_returned"] = len(lines) - 1  # Exclude header
                result["inferred_frequency"] = "daily"
                result["daily_only"] = True
                result["intraday_available"] = False
                result["usable_for_stale_diagnostic_only"] = True
                result["source_status"] = "ANCHOR_SOURCE_DAILY_ONLY_STALE"
                return result
                
            except Exception as e:
                err_str = str(e)
                result["error_excerpt"] = err_str[:200] if err_str else "Unknown error"
                if "429" in err_str:
                    result["status_code"] = 429
                    result["error_class"] = "HTTP_429"
                    result["rate_limited"] = True
                    result["source_status"] = "ANCHOR_SOURCE_RATE_LIMITED"
                elif "apikey" in err_str.lower() or "captcha" in err_str.lower():
                    result["error_class"] = "API_KEY_REQUIRED"
                    result["requires_api_key_detected"] = True
                    result["source_status"] = "ANCHOR_SOURCE_REQUIRES_API_KEY"
                else:
                    result["error_class"] = "HTTP_ERROR"
                    result["source_status"] = "ANCHOR_SOURCE_ERROR"
                return result
        
        else:
            result["error_class"] = "UNSUPPORTED_SOURCE"
            result["rejection_reason"] = f"Unknown anchor source: {source_name}"
            result["source_status"] = "ANCHOR_SOURCE_UNSUPPORTED"
            return result
            
    except Exception as e:
        result["error_class"] = "UNEXPECTED_ERROR"
        result["error_excerpt"] = str(e)[:200]
        result["rejection_reason"] = f"Unexpected error: {str(e)[:100]}"
        result["source_status"] = "ANCHOR_SOURCE_ERROR"
        return result


def run_anchor_source_audit(cp: NetworkChokepoint, symbols: list[str],
                            sources: list[str],
                            max_symbols: int = 4,
                            timeout_seconds: int = 20,
                            max_requests_per_source: int = 4,
                            cache_dir: Path | None = None) -> dict:
    """Run anchor source audit for multiple symbols and sources.
    
    Returns comprehensive audit report with per-source and global status.
    """
    symbols = symbols[:max_symbols]
    results = []
    
    for display_sym in symbols:
        for source_name in sources:
            result = audit_anchor_source(cp, source_name, display_sym, timeout_seconds)
            results.append(result)
    
    # Compute global status
    usable_intraday = any(r["usable_for_normal_residual"] for r in results)
    usable_daily_only = any(r["usable_for_stale_diagnostic_only"] and not r["usable_for_normal_residual"] for r in results)
    all_blocked = all(r["source_status"] in ("ANCHOR_SOURCE_RATE_LIMITED", "ANCHOR_SOURCE_REQUIRES_API_KEY", "ANCHOR_SOURCE_ERROR", "ANCHOR_SOURCE_NO_DATA", "ANCHOR_SOURCE_UNSUPPORTED") for r in results)
    
    if usable_intraday:
        global_status = "PUBLIC_ANCHOR_USABLE"
    elif usable_daily_only:
        global_status = "PUBLIC_ANCHOR_STALE_ONLY"
    else:
        global_status = "PUBLIC_ANCHOR_UNAVAILABLE"
    
    return {
        "audit_symbols": symbols,
        "audit_sources": sources,
        "max_symbols": max_symbols,
        "timeout_seconds": timeout_seconds,
        "max_requests_per_source": max_requests_per_source,
        "results": results,
        "global_status": global_status,
        "usable_intraday_count": sum(1 for r in results if r["usable_for_normal_residual"]),
        "usable_daily_only_count": sum(1 for r in results if r["usable_for_stale_diagnostic_only"] and not r["usable_for_normal_residual"]),
        "rate_limited_count": sum(1 for r in results if r["rate_limited"]),
        "requires_api_key_count": sum(1 for r in results if r["requires_api_key_detected"]),
        "captcha_detected_count": sum(1 for r in results if r["captcha_or_manual_flow_detected"]),
    }


# ---------------------------------------------------------------------------
# Residual diagnostics with alignment
# ---------------------------------------------------------------------------

def compute_residual_for_api_symbol(rows: list[dict],
                                     anchor_price: float,
                                     anchor_source: str,
                                     anchor_timestamp_utc: str,
                                     anchor_is_intraday: bool,
                                     max_staleness_minutes: int,
                                     allow_daily_stale: bool) -> dict | None:
    """Compute residual diagnostics for one API symbol.

    Returns dict with residual stats or None if insufficient data.
    """
    if not anchor_price or not rows:
        return None

    anchor_dt = None
    try:
        anchor_dt = datetime.fromisoformat(anchor_timestamp_utc.replace("Z", "+00:00"))
    except Exception:
        anchor_dt = None

    residuals: list[dict] = []
    for r in rows:
        if r["mid"] <= 0 or anchor_price <= 0:
            continue
        # Crossed book check
        if r["best_bid"] >= r["best_ask"] and r["best_bid"] > 0:
            continue
        # Two-sided check
        if not r.get("two_sided_book", False):
            continue

        # Anchor staleness check
        staleness_seconds = None
        if anchor_dt and r.get("timestamp_utc"):
            try:
                row_dt = datetime.fromisoformat(r["timestamp_utc"].replace("Z", "+00:00"))
                diff = abs((row_dt - anchor_dt).total_seconds())
                staleness_seconds = diff
            except Exception:
                pass

        # Staleness gate
        if staleness_seconds is not None:
            if staleness_seconds > max_staleness_minutes * 60:
                if not (allow_daily_stale and not anchor_is_intraday):
                    continue

        # Compute residual
        res_bps = 10000.0 * (r["mid"] - anchor_price) / anchor_price
        residuals.append({
            "residual_bps": round(res_bps, 4),
            "abs_residual_bps": round(abs(res_bps), 4),
            "staleness_seconds": round(staleness_seconds, 1) if staleness_seconds is not None else None,
            "spread_bps": r["spread_bps"],
            "two_sided": r.get("two_sided_book", False),
            "timestamp_utc": r.get("timestamp_utc", ""),
        })

    if not residuals:
        return None

    abs_res = sorted(r["abs_residual_bps"] for r in residuals)
    staleness_vals = [r["staleness_seconds"] for r in residuals if r["staleness_seconds"] is not None]

    # Calendar days
    calendar_days = set()
    for r in residuals:
        ts = r.get("timestamp_utc", "")
        if ts:
            try:
                day = ts[:10]
                calendar_days.add(day)
            except Exception:
                pass

    # Concentration
    day_counts: dict[str, int] = {}
    for r in residuals:
        ts = r.get("timestamp_utc", "")
        if ts:
            day = ts[:10]
            day_counts[day] = day_counts.get(day, 0) + 1

    max_day_count = max(day_counts.values()) if day_counts else 0
    total = len(residuals)
    concentration_warning = max_day_count > total * 0.5 if total > 0 else False

    # Underpowered check
    underpowered = len(residuals) < 500 or len(calendar_days) < 3

    # Tail diagnostics
    tail_thresholds = [25, 50, 100]
    tail_counts = {f"abs_gte_{t}_bps": sum(1 for x in abs_res if x >= t) for t in tail_thresholds}

    # Staleness stats
    median_staleness = round(_quantile(staleness_vals, 0.5), 1) if staleness_vals else None
    p90_staleness = round(_quantile(staleness_vals, 0.9), 1) if staleness_vals else None
    stale_anchor_warning = (
        (median_staleness is not None and median_staleness > 900) or
        (p90_staleness is not None and p90_staleness > 1800)
    )

    # Tail spread diagnostics
    tail_rows = sorted(residuals, key=lambda x: x["abs_residual_bps"], reverse=True)[:100]
    tail_spreads = [r["spread_bps"] for r in tail_rows if r["spread_bps"] != float("inf")]
    tail_two_sided = sum(1 for r in tail_rows if r.get("two_sided", False)) / len(tail_rows) if tail_rows else 0

    # Status
    if underpowered:
        status = "SONARX_RESIDUAL_DIAGNOSTIC_UNDERPOWERED"
    elif stale_anchor_warning and not anchor_is_intraday:
        status = "ANCHOR_DAILY_ONLY_STALE"
    elif stale_anchor_warning:
        status = "SONARX_RESIDUAL_DIAGNOSTIC_AVAILABLE"
    else:
        status = "SONARX_RESIDUAL_DIAGNOSTIC_AVAILABLE"

    return {
        "aligned_sample_count": len(residuals),
        "distinct_calendar_days": len(calendar_days),
        "anchor_source": anchor_source,
        "anchor_frequency": "daily" if not anchor_is_intraday else "intraday",
        "median_anchor_staleness_seconds": median_staleness,
        "p90_anchor_staleness_seconds": p90_staleness,
        "median_residual_bps": round(_quantile([r["residual_bps"] for r in residuals], 0.5), 4),
        "p75_abs_residual_bps": round(_quantile(abs_res, 0.75), 4),
        "p90_abs_residual_bps": round(_quantile(abs_res, 0.90), 4),
        "p99_abs_residual_bps": round(_quantile(abs_res, 0.99), 4),
        "abs_residual_ge_25_bps_count": tail_counts["abs_gte_25_bps"],
        "abs_residual_ge_50_bps_count": tail_counts["abs_gte_50_bps"],
        "abs_residual_ge_100_bps_count": tail_counts["abs_gte_100_bps"],
        "concentration_by_day": day_counts,
        "concentration_by_week": {},
        "tail_dominated_by_one_day": concentration_warning,
        "tail_timestamps_two_sided_rate": round(tail_two_sided, 4),
        "tail_timestamps_median_spread_bps": round(_quantile(tail_spreads, 0.5), 4) if tail_spreads else None,
        "tail_timestamps_p75_spread_bps": round(_quantile(tail_spreads, 0.75), 4) if tail_spreads else None,
        "tail_timestamps_depth_500_available_rate": 0.0,
        "residual_status": status,
        "underpowered": underpowered,
        "stale_anchor_warning": stale_anchor_warning,
        "concentration_warning": concentration_warning,
    }

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
        "anchor_source_audit_mode": getattr(args, "anchor_source_audit", False),
        "executable_pnl_modeled": False,
        "queue_position_modeled": False,
        "fills_modeled": False,
        "slippage_model_modeled": False,
        "diagnostic_only": True,
    }
    write_json(root / "run_manifest.json", run_manifest)
    
    # Handle anchor source audit mode
    if getattr(args, "anchor_source_audit", False):
        audit_symbols = [s.strip() for s in getattr(args, "anchor_audit_symbols", "TSLA,AAPL,MSFT,NVDA").split(",") if s.strip()]
        max_audit_symbols = getattr(args, "anchor_audit_max_symbols", 4)
        audit_timeout = getattr(args, "anchor_audit_timeout_seconds", 20)
        max_requests_per_source = getattr(args, "anchor_audit_max_requests_per_source", 4)
        anchor_sources = [s.strip() for s in getattr(args, "anchor_source", "yahoo,stooq").split(",") if s.strip()]
        
        audit_result = run_anchor_source_audit(
            cp, audit_symbols, anchor_sources,
            max_symbols=max_audit_symbols,
            timeout_seconds=audit_timeout,
            max_requests_per_source=max_requests_per_source,
            cache_dir=getattr(args, "anchor_cache_dir", None)
        )
        
        write_json(root / "public_anchor_source_audit.json", audit_result)
        
        # Determine final status based on audit
        global_status = audit_result.get("global_status", "PUBLIC_ANCHOR_UNAVAILABLE")
        if global_status == "PUBLIC_ANCHOR_USABLE":
            final_status = "SONARX_RESIDUAL_DIAGNOSTIC_AVAILABLE"
        elif global_status == "PUBLIC_ANCHOR_STALE_ONLY":
            final_status = "SONARX_PHASE_MINUS1_NOT_ENOUGH_FOR_PRECOMMITMENT"
        else:
            final_status = "SONARX_PHASE_MINUS1_NOT_ENOUGH_FOR_PRECOMMITMENT"
        
        # Write summary
        summary = {
            **meta,
            "run_id": run_id,
            "status": final_status,
            "audit_global_status": global_status,
            "audit_symbols": audit_symbols,
            "audit_sources": anchor_sources,
            "usable_intraday_count": audit_result.get("usable_intraday_count", 0),
            "usable_daily_only_count": audit_result.get("usable_daily_only_count", 0),
            "rate_limited_count": audit_result.get("rate_limited_count", 0),
            "requires_api_key_count": audit_result.get("requires_api_key_count", 0),
            "safety_mode": "public_data_observer_only",
            "no_orders_no_auth_no_live_confirmation": True,
        }
        write_json(root / "summary.json", summary)
        
        return {
            "status": final_status,
            "run_id": run_id,
            "audit_result": audit_result,
        }
    
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

    for api_sym in markets:
        display_sym, dex_name = parse_api_symbol(api_sym)
        market_prefix = f"{S3_BASE_PREFIX}{api_sym}/{L2_SUMMARY_SUFFIX}"
        partitions = list_s3_prefixes(cp, market_prefix)
        sorted_partitions = sorted(partitions)
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
        }
        if not partitions:
            market_index[api_sym] = mi
            continue

        sample_keys = build_stratified_sample(
            sorted_partitions, args.sample_days, args.max_files_per_market
        )

        for part_prefix in sample_keys:
            objs = list_s3_objects(cp, part_prefix)
            gz_objs = [o for o in objs if o.get("key", "").endswith(".json.gz")]
            for obj in gz_objs[:10]:  # cap per partition
                if total_bytes >= args.download_budget_bytes:
                    break
                key = obj["key"]
                try:
                    data = download_gzip_json(cp, key)
                    if isinstance(data, list):
                        for raw_snap in data:
                            parsed = parse_snapshot(raw_snap, api_sym)
                            if parsed:
                                all_metrics.append(parsed)
                                mi["snapshots_parsed"] += 1
                        mi["files_parsed"] += 1
                    total_bytes += obj.get("size", 0)
                except Exception as exc:
                    mi["errors"] += 1
        market_index[api_sym] = mi
        
        # Progress heartbeat after each market
        progress_status = {
            **meta,
            "run_id": run_id,
            "status": "SONARX_PHASE_MINUS1_IN_PROGRESS",
            "current_phase": "s3_download_and_parse",
            "current_market": api_sym,
            "current_partition": sample_keys[-1] if sample_keys else None,
            "markets_completed": len(market_index),
            "markets_total": len(markets),
            "files_selected": sum(m.get("files_parsed", 0) for m in market_index.values()),
            "files_downloaded": sum(m.get("files_parsed", 0) for m in market_index.values()),
            "bytes_downloaded": total_bytes,
            "parsed_snapshots": sum(m.get("snapshots_parsed", 0) for m in market_index.values()),
            "last_progress_at_utc": utc_now_iso(),
            "errors_count": sum(m.get("errors", 0) for m in market_index.values()),
            "executable_pnl_modeled": False,
            "diagnostic_only": True,
        }
        write_json(root / "phase_minus1_status.json", progress_status)

    statuses.append("SONARX_L2_HISTORY_PARSE_OK")

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

    # --- Phase E: candleSnapshot join (legacy, uses old flat format) ---
    candle_join: dict[str, Any] = {**meta, "candles_by_api_symbol": {}}
    if getattr(args, "enable_candle_join", False) and cp.allow_network_public:
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
        statuses.append("SONARX_CANDLE_JOIN_BLOCKED")
    write_json(root / "hyperliquid_candle_snapshot_join.json", candle_join)

    # --- Phase E2: candle format audit (if candle join enabled) ---
    candle_format_audit: dict[str, Any] = {"status": "SKIPPED"}
    if getattr(args, "enable_candle_join", False) and cp.allow_network_public:
        candle_format_audit = run_candle_format_audit(cp, markets, root, meta)
        if candle_format_audit.get("overall_status") == "CANDLE_SNAPSHOT_AVAILABLE":
            if "CANDLE_SNAPSHOT_AVAILABLE" not in statuses:
                statuses.append("CANDLE_SNAPSHOT_AVAILABLE")
        elif candle_format_audit.get("overall_status") == "CANDLE_SNAPSHOT_RATE_LIMITED":
            if "CANDLE_SNAPSHOT_RATE_LIMITED" not in statuses:
                statuses.append("CANDLE_SNAPSHOT_RATE_LIMITED")
        else:
            if "CANDLE_SNAPSHOT_FORMAT_UNRESOLVED" not in statuses:
                statuses.append("CANDLE_SNAPSHOT_FORMAT_UNRESOLVED")

    # --- Phase F: anchor probe (Yahoo + Stooq fallback, deduped by display symbol) ---
    anchor_probe: dict[str, Any] = {**meta, "anchors_by_display": {}}
    anchor_available = False
    anchor_cache: dict[str, Any] = {}
    cache_dir = None
    if getattr(args, "enable_anchors", False) and cp.allow_network_public:
        cache_dir = getattr(args, "anchor_cache_dir", None)
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)

        anchor_source = getattr(args, "anchor_source", "yahoo")
        max_staleness = getattr(args, "max_anchor_staleness_minutes", 15)
        allow_daily_stale = getattr(args, "allow_daily_stale_anchor_diagnostic", False)

        display_symbols = sorted(set(parse_api_symbol(m)[0] for m in markets))

        for dsym in display_symbols:
            anchor_result = fetch_anchor_for_display_symbol(
                cp, dsym, anchor_source, cache_dir, anchor_cache
            )
            if anchor_result:
                anchor_probe["anchors_by_display"][dsym] = {
                    "available": True,
                    "price": anchor_result["price"],
                    "source": anchor_result["source"],
                    "frequency": anchor_result["frequency"],
                    "is_intraday": anchor_result["is_intraday"],
                    "timestamp_utc": anchor_result["timestamp_utc"],
                    "status": "SONARX_ANCHOR_AVAILABLE",
                }
                anchor_available = True
            else:
                anchor_probe["anchors_by_display"][dsym] = {
                    "available": False,
                    "status": "SONARX_ANCHOR_BLOCKED",
                }

        # Check if any anchors are daily-only (no intraday available)
        daily_only = all(
            not anchor_probe["anchors_by_display"].get(d, {}).get("is_intraday", False)
            for d in display_symbols
            if anchor_probe["anchors_by_display"].get(d, {}).get("available")
        )
        if daily_only and anchor_available:
            anchor_probe["daily_only"] = True
            anchor_probe["status"] = "ANCHOR_DAILY_ONLY_STALE"
            if "ANCHOR_DAILY_ONLY_STALE" not in statuses:
                statuses.append("ANCHOR_DAILY_ONLY_STALE")
        elif anchor_available:
            anchor_probe["status"] = "SONARX_ANCHOR_AVAILABLE"
            if "SONARX_ANCHOR_AVAILABLE" not in statuses:
                statuses.append("SONARX_ANCHOR_AVAILABLE")
        else:
            anchor_probe["status"] = "SONARX_ANCHOR_BLOCKED"
            if "SONARX_ANCHOR_BLOCKED" not in statuses:
                statuses.append("SONARX_ANCHOR_BLOCKED")
    else:
        anchor_probe["status"] = "SONARX_ANCHOR_BLOCKED"
        anchor_probe["disabled_reason"] = "explicitly_disabled_via_flags"
        if "SONARX_ANCHOR_BLOCKED" not in statuses:
            statuses.append("SONARX_ANCHOR_BLOCKED")
    write_json(root / "anchor_availability_probe.json", anchor_probe)

    # --- Phase G: diagnostic residual (only if anchors available) ---
    residual_summary: dict[str, Any] = {**meta, "residual_by_api_symbol": {}}
    residual_available = False
    max_staleness = getattr(args, "max_anchor_staleness_minutes", 15)
    allow_daily_stale = getattr(args, "allow_daily_stale_anchor_diagnostic", False)

    if anchor_available:
        for api_sym, rows in by_api_sym.items():
            display_sym, _ = parse_api_symbol(api_sym)
            anchor_info = anchor_probe["anchors_by_display"].get(display_sym, {})
            anchor_price = anchor_info.get("price", 0)
            anchor_src = anchor_info.get("source", "unknown")
            anchor_ts = anchor_info.get("timestamp_utc", "")
            anchor_intraday = anchor_info.get("is_intraday", False)

            if not anchor_price:
                continue

            res = compute_residual_for_api_symbol(
                rows, anchor_price, anchor_src, anchor_ts,
                anchor_intraday, max_staleness, allow_daily_stale
            )
            if res:
                residual_summary["residual_by_api_symbol"][api_sym] = res
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

    # Check liquidity thresholds
    liquidity_ok = False
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
        "forward_recorder_status": "running (unclear from local state, not modified)",
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
    p.add_argument("--download-budget-bytes", type=int, default=2_000_000_000)
    p.add_argument("--allow-s3-archive-read", action="store_true")
    p.add_argument("--allow-network-public", action="store_true")
    p.add_argument("--enable-candle-join", action="store_true")
    p.add_argument("--enable-anchors", action="store_true")
    p.add_argument("--anchor-source", default="yahoo", help="Comma-separated anchor sources: yahoo,stooq,auto_public")
    p.add_argument("--anchor-cache-dir", default=None, help="Directory for anchor response cache")
    p.add_argument("--anchor-max-retries", type=int, default=2, help="Max retries per anchor fetch")
    p.add_argument("--anchor-backoff-base-seconds", type=int, default=2, help="Backoff base in seconds")
    p.add_argument("--anchor-timeout-seconds", type=int, default=20, help="HTTP timeout per anchor request")
    p.add_argument("--anchor-cache-ttl-seconds", type=int, default=900, help="Cache TTL in seconds")
    p.add_argument("--anchor-alignment-mode", default="exact_or_previous_anchor", help="Anchor alignment mode")
    p.add_argument("--max-anchor-staleness-minutes", type=int, default=15, help="Max anchor staleness in minutes")
    p.add_argument("--allow-daily-stale-anchor-diagnostic", action="store_true", help="Allow daily-only stale anchors for diagnostic residuals")
    p.add_argument("--anchor-source-audit", action="store_true", help="Run no-auth public anchor source audit only")
    p.add_argument("--anchor-audit-symbols", default="TSLA,AAPL,MSFT,NVDA", help="Comma-separated display symbols for anchor audit")
    p.add_argument("--anchor-audit-max-symbols", type=int, default=4, help="Max symbols to audit")
    p.add_argument("--anchor-audit-timeout-seconds", type=int, default=20, help="HTTP timeout per anchor audit request")
    p.add_argument("--anchor-audit-max-requests-per-source", type=int, default=4, help="Max requests per source in audit")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--real-smoke", action="store_true", help="Smoke test mode: max 2 markets, 10 files each, 50MB budget")
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
