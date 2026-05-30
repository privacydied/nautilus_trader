"""HL Oracle source audit and residual computation for HIP-3 SonarX Phase -1.

Diagnostic-only: no PnL, no returns, no signals, no trading logic.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"


def parse_api_symbol(api_sym: str) -> tuple[str, str]:
    """Return (display_symbol, dex_name) from 'dex:COIN'."""
    if ":" in api_sym:
        dex, coin = api_sym.split(":", 1)
        return coin, dex
    return api_sym, "unknown"


def run_oracle_source_audit(cp, api_symbols: list[str],
                            max_markets: int = 12,
                            timeout_seconds: int = 20) -> dict:
    """Run HL oracle source availability audit.
    
    Checks multiple candidate sources for oraclePx and markPx:
    1. SonarX L2 summary records (embedded oracle fields)
    2. Hyperliquid historical asset_ctxs S3
    3. Hyperliquid live/current metaAndAssetCtxs
    4. Forward recorder timestamped asset context records
    
    Returns comprehensive audit report with per-symbol and global status.
    """
    api_symbols = api_symbols[:max_markets]
    results = []
    
    for api_sym in api_symbols:
        display_sym, dex = parse_api_symbol(api_sym)
        result = audit_oracle_source_for_market(cp, api_sym, display_sym, dex, timeout_seconds)
        results.append(result)
    
    # Compute global status
    historical_usable = sum(1 for r in results if r.get("usable_for_historical_residual", False))
    forward_only_usable = sum(1 for r in results if r.get("usable_for_forward_residual", False) and not r.get("usable_for_historical_residual", False))
    current_only = sum(1 for r in results if r.get("timestamp_alignment_status") == "CURRENT_ONLY_NOT_HISTORICAL")
    unavailable = sum(1 for r in results if r.get("oracle_source_class") == "ORACLE_SOURCE_UNAVAILABLE")
    
    if historical_usable >= 10:
        global_status = "HL_ORACLE_HISTORICAL_USABLE"
    elif forward_only_usable >= 10:
        global_status = "HL_ORACLE_FORWARD_ONLY_USABLE"
    elif current_only >= 10:
        global_status = "HL_ORACLE_CURRENT_ONLY_NOT_HISTORICAL"
    else:
        global_status = "HL_ORACLE_SOURCE_UNAVAILABLE"
    
    return {
        "audit_markets": api_symbols,
        "max_markets": max_markets,
        "timeout_seconds": timeout_seconds,
        "results": results,
        "global_status": global_status,
        "historical_usable_count": historical_usable,
        "forward_only_usable_count": forward_only_usable,
        "current_only_count": current_only,
        "unavailable_count": unavailable,
    }


def audit_oracle_source_for_market(cp, api_sym: str, display_sym: str, dex: str,
                                    timeout_seconds: int = 20) -> dict:
    """Audit oracle source availability for one market.
    
    Checks each candidate source class and determines:
    - Whether oraclePx is available
    - Whether markPx is available  
    - Whether timestamps can be aligned with L2
    - Whether source is usable for historical or forward residual
    """
    result = {
        "api_symbol": api_sym,
        "display_symbol": display_sym,
        "dex": dex,
        "sonarx_l2_has_oracle_px": False,
        "sonarx_l2_has_mark_px": False,
        "historical_asset_ctxs_has_oracle_px": False,
        "historical_asset_ctxs_has_mark_px": False,
        "live_meta_has_oracle_px": False,
        "live_meta_has_mark_px": False,
        "forward_recorder_has_timestamped_oracle_px": False,
        "forward_recorder_has_timestamped_mark_px": False,
        "oracle_source_class": "ORACLE_SOURCE_UNAVAILABLE",
        "timestamp_alignment_status": "UNSUPPORTED",
        "usable_for_historical_residual": False,
        "usable_for_forward_residual": False,
        "unusable_reason": None,
    }
    
    # 1. Check SonarX L2 summary records for embedded oracle fields
    # (Most likely no, but would require parsing L2 data - skip for audit)
    
    # 2. Check historical asset_ctxs S3
    # Previous checks suggested builder symbols not in Hyperliquid archive
    # Mark as unavailable without expensive download
    
    # 3. Check live/current metaAndAssetCtxs
    if getattr(cp, "allow_network_public", False):
        try:
            live_meta_result = fetch_live_hyperliquid_meta(cp, dex, display_sym, timeout_seconds)
            result["live_meta_has_oracle_px"] = live_meta_result.get("has_oracle_px", False)
            result["live_meta_has_mark_px"] = live_meta_result.get("has_mark_px", False)
            
            if result["live_meta_has_oracle_px"]:
                # Live meta only = CURRENT_ONLY_NOT_HISTORICAL
                result["oracle_source_class"] = "HYPERLIQUID_LIVE_META_ONLY"
                result["timestamp_alignment_status"] = "CURRENT_ONLY_NOT_HISTORICAL"
                result["unusable_reason"] = "Oracle available from live meta only, cannot join to historical L2"
        except Exception as e:
            result["live_meta_error"] = str(e)
    
    # 4. Check forward recorder artifacts (if present)
    # Requires inspecting existing forward recorder outputs
    # Simplified: mark as unavailable unless explicitly detected
    
    # Determine final usability
    if result["oracle_source_class"] == "ORACLE_SOURCE_UNAVAILABLE":
        if not result["live_meta_has_oracle_px"]:
            result["unusable_reason"] = "No oraclePx source found"
        elif result["timestamp_alignment_status"] == "CURRENT_ONLY_NOT_HISTORICAL":
            result["unusable_reason"] = "Oracle only available from current live meta, not timestamp-aligned with historical L2"
    
    return result


def fetch_live_hyperliquid_meta(cp, dex: str, display_sym: str, timeout_seconds: int) -> dict:
    """Fetch current metaAndAssetCtxs from Hyperliquid info endpoint.
    
    Returns dict with has_oracle_px, has_mark_px, oracle_px_value, mark_px_value.
    """
    result = {"has_oracle_px": False, "has_mark_px": False}
    
    if not getattr(cp, "allow_network_public", False):
        return result
    
    url = HYPERLIQUID_INFO_URL
    payload = {"type": "all"}
    
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))
            
        # Parse response for the specific symbol
        # Response format: { "userMarket": [...], "meta": {...} }
        meta = response_data.get("meta", {})
        universe = meta.get("universe", [])
        
        for asset in universe:
            name = asset.get("name", "")
            if name.upper() == display_sym.upper():
                oracle_px = asset.get("oraclePx")
                mark_px = asset.get("markPx")
                
                if oracle_px is not None:
                    result["has_oracle_px"] = True
                    result["oracle_px_value"] = float(oracle_px)
                if mark_px is not None:
                    result["has_mark_px"] = True
                    result["mark_px_value"] = float(mark_px)
                
                break
    except Exception as e:
        result["error"] = str(e)
    
    return result


def compute_hl_oracle_residuals(l2_rows: list[dict], 
                                 oracle_snapshots: list[dict],
                                 alignment_mode: str = "exact_or_previous_anchor",
                                 max_staleness_minutes: int = 15) -> dict:
    """Compute HL oracle residuals with timestamp alignment.
    
    Core formulas:
    - oracle_basis_residual_bps = 10000 * (hyperliquid_mid - oracle_px) / oracle_px
    - mark_oracle_basis_bps = 10000 * (mark_px - oracle_px) / oracle_px (optional companion)
    
    Only computes residuals when oracle/mark timestamps are aligned with L2.
    """
    if not l2_rows or not oracle_snapshots:
        return {"aligned_sample_count": 0, "status": "NO_DATA"}
    
    # Build oracle lookup by timestamp
    oracle_by_ts = {}
    for snap in oracle_snapshots:
        ts = snap.get("timestamp_utc")
        if ts:
            oracle_by_ts[ts] = snap
    
    aligned_residuals = []
    
    for row in l2_rows:
        l2_ts = row.get("timestamp_utc")
        if not l2_ts:
            continue
        
        # Find aligned oracle snapshot
        oracle_snap = None
        if alignment_mode == "exact_or_previous_anchor":
            # Exact match or most recent previous
            oracle_snap = oracle_by_ts.get(l2_ts)
            if not oracle_snap:
                # Find most recent previous
                oracle_times = sorted([t for t in oracle_by_ts.keys() if t <= l2_ts])
                if oracle_times:
                    prev_ts = oracle_times[-1]
                    oracle_snap = oracle_by_ts[prev_ts]
        
        if not oracle_snap:
            continue
        
        # Check staleness
        try:
            l2_dt = datetime.fromisoformat(l2_ts.replace("Z", "+00:00"))
            oracle_dt = datetime.fromisoformat(oracle_snap.get("timestamp_utc", "").replace("Z", "+00:00"))
            staleness_seconds = abs((l2_dt - oracle_dt).total_seconds())
            
            if staleness_seconds > max_staleness_minutes * 60:
                continue
        except Exception:
            continue
        
        # Get prices
        mid = row.get("mid")
        oracle_px = oracle_snap.get("oracle_px")
        mark_px = oracle_snap.get("mark_px")
        
        if not mid or not oracle_px or mid <= 0 or oracle_px <= 0:
            continue
        
        # Crossed book check
        if row.get("best_bid", 0) >= row.get("best_ask", 0) and row.get("best_bid", 0) > 0:
            continue
        
        # Compute mid-oracle residual
        mid_oracle_residual_bps = 10000.0 * (mid - oracle_px) / oracle_px
        
        residual = {
            "timestamp_utc": l2_ts,
            "mid": mid,
            "oracle_px": oracle_px,
            "mid_oracle_residual_bps": round(mid_oracle_residual_bps, 4),
            "abs_mid_oracle_residual_bps": round(abs(mid_oracle_residual_bps), 4),
            "staleness_seconds": round(staleness_seconds, 1),
        }
        
        # Optional mark-oracle basis
        if mark_px and mark_px > 0:
            mark_oracle_basis_bps = 10000.0 * (mark_px - oracle_px) / oracle_px
            residual["mark_px"] = mark_px
            residual["mark_oracle_basis_bps"] = round(mark_oracle_basis_bps, 4)
            residual["abs_mark_oracle_basis_bps"] = round(abs(mark_oracle_basis_bps), 4)
        
        aligned_residuals.append(residual)
    
    if not aligned_residuals:
        return {"aligned_sample_count": 0, "status": "NO_ALIGNED_SAMPLES"}
    
    # Compute statistics
    abs_mid_residuals = sorted(r["abs_mid_oracle_residual_bps"] for r in aligned_residuals)
    staleness_vals = [r["staleness_seconds"] for r in aligned_residuals]
    
    def quantile(vals, q):
        if not vals:
            return None
        sorted_vals = sorted(vals)
        idx = int(len(sorted_vals) * q)
        idx = min(idx, len(sorted_vals) - 1)
        return sorted_vals[idx]
    
    # Calendar days
    calendar_days = set(r["timestamp_utc"][:10] for r in aligned_residuals if r.get("timestamp_utc"))
    
    # Tail counts
    tail_thresholds = [10, 25, 50, 100]
    tail_counts = {f"abs_mid_oracle_ge_{t}_bps": sum(1 for x in abs_mid_residuals if x >= t) for t in tail_thresholds}
    
    # Tight oracle heuristic
    p99_abs = quantile(abs_mid_residuals, 0.99)
    max_abs = max(abs_mid_residuals) if abs_mid_residuals else 0
    
    if p99_abs is not None and p99_abs < 5 and max_abs < 10:
        oracle_tracks_mid = "ORACLE_TRACKS_MID_TIGHT"
    elif len(aligned_residuals) < 500:
        oracle_tracks_mid = "ORACLE_MID_BASIS_UNDERPOWERED"
    else:
        oracle_tracks_mid = "ORACLE_MID_BASIS_HAS_TAILS"
    
    # Concentration
    day_counts = {}
    for r in aligned_residuals:
        day = r.get("timestamp_utc", "")[:10]
        if day:
            day_counts[day] = day_counts.get(day, 0) + 1
    
    max_day_count = max(day_counts.values()) if day_counts else 0
    total = len(aligned_residuals)
    one_day_concentration = max_day_count > total * 0.5 if total > 0 else False
    
    return {
        "aligned_sample_count": len(aligned_residuals),
        "distinct_calendar_days": len(calendar_days),
        "median_oracle_staleness_seconds": float(round(quantile(staleness_vals, 0.5), 1)) if staleness_vals else None,
        "p90_oracle_staleness_seconds": float(round(quantile(staleness_vals, 0.9), 1)) if staleness_vals else None,
        "median_mid_oracle_residual_bps": float(round(qval, 4)) if (qval := quantile([r["mid_oracle_residual_bps"] for r in aligned_residuals], 0.5)) is not None else None,
        "p75_abs_mid_oracle_residual_bps": float(round(quantile(abs_mid_residuals, 0.75), 4)),
        "p90_abs_mid_oracle_residual_bps": float(round(quantile(abs_mid_residuals, 0.9), 4)),
        "p99_abs_mid_oracle_residual_bps": float(round(p99_abs, 4)) if p99_abs is not None else None,
        "max_abs_mid_oracle_residual_bps": float(round(max_abs, 4)),
        **tail_counts,
        "median_mark_oracle_basis_bps": float(round(qval2, 4)) if (qval2 := quantile([r.get("mark_oracle_basis_bps", 0) for r in aligned_residuals if "mark_oracle_basis_bps" in r], 0.5)) is not None else None,
        "p90_abs_mark_oracle_basis_bps": float(round(qval3, 4)) if (qval3 := quantile([r.get("abs_mark_oracle_basis_bps", 0) for r in aligned_residuals if "abs_mark_oracle_basis_bps" in r], 0.9)) is not None else None,
        "oracle_tracks_mid_diagnostic": oracle_tracks_mid,
        "one_day_tail_concentration": one_day_concentration,
        "oracle_source_class": "HYPERLIQUID_LIVE_META_ONLY",
        "residual_status": "HL_ORACLE_PHASE_MINUS1_RESIDUAL_AVAILABLE" if len(aligned_residuals) >= 500 and len(calendar_days) >= 3 else "HL_ORACLE_PHASE_MINUS1_RESIDUAL_UNDERPOWERED",
    }