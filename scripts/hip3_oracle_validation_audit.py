#!/usr/bin/env python3
"""
HIP-3 Oracle Residual Validation Audit - Phase -1

Validates whether observed flx oracle residual is a reverting dislocation
or a stale/illiquid oracle artifact.

Public data only. No orders, no auth, no execution.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import statistics

try:
    import orjson
    def loads_line(line):
        if isinstance(line, str):
            line = line.encode('utf-8')
        return orjson.loads(line)
except ImportError:
    orjson = None
    def loads_line(line):
        if isinstance(line, bytes):
            line = line.decode('utf-8')
        return json.loads(line)

# Configuration
FORWARD_RECORDER_ROOT = Path("/mnt/nasirjones/py/nautilus_trader/reports/hip3_builder_dex_tradfi_forward_recorder_v0")
REPORTS_DIR = Path("/mnt/nasirjones/py/nautilus_trader/.worktrees/hip3-residual-validation/reports/hip3_sonarx_tradfi_l2_residual_phase_minus1_v0")
SONARX_L2_ROOT = REPORTS_DIR

TARGET_SYMBOLS = [
    "xyz:TSLA", "flx:TSLA", "km:TSLA", "cash:TSLA",
    "xyz:AAPL", "km:AAPL",
    "xyz:MSFT", "cash:MSFT",
    "xyz:NVDA", "flx:NVDA", "km:NVDA", "cash:NVDA"
]

DISPLAY_SYMBOLS = ["TSLA", "AAPL", "MSFT", "NVDA"]
DEXES = ["xyz", "flx", "km", "cash"]

REPORTS_DIR.mkdir(parents=True, exist_ok=True)

def find_oracle_bearing_files():
    """Step 2: Discover all oracle-bearing forward-recorder daily files."""
    inventory = []
    
    for run_dir in FORWARD_RECORDER_ROOT.iterdir():
        if not run_dir.is_dir():
            continue
        
        asset_ctx_dir = run_dir / "asset_context_snapshots"
        if not asset_ctx_dir.exists():
            continue
        
        for jsonl_file in asset_ctx_dir.glob("*.jsonl"):
            file_info = {
                "path": str(jsonl_file),
                "run_id": run_dir.name,
                "filename": jsonl_file.name,
                "date_inferred": jsonl_file.stem,  # e.g., 20260528
                "file_size_bytes": jsonl_file.stat().st_size,
            }
            
            # Sample first line to check fields
            with open(jsonl_file, 'rb') as f:
                first_line = f.readline()
                if first_line:
                    try:
                        sample = loads_line(first_line)
                        file_info["fields_present"] = list(sample.keys())
                        file_info["has_oracle_price"] = "oracle_price" in sample
                        file_info["has_mark_price"] = "mark_price" in sample
                        file_info["has_mid_price"] = "mid_price" in sample
                        file_info["has_best_bid"] = "best_bid" in sample
                        file_info["has_best_ask"] = "best_ask" in sample
                        
                        # Count lines and get timestamps
                        f.seek(0)
                        lines = f.readlines()
                        file_info["line_count"] = len(lines)
                        
                        if lines:
                            first_sample = loads_line(lines[0])
                            last_sample = loads_line(lines[-1])
                            file_info["first_timestamp"] = first_sample.get("timestamp_utc")
                            file_info["last_timestamp"] = last_sample.get("timestamp_utc")
                            
                            # Symbols present
                            symbols = set()
                            for line in lines[:100]:  # Sample first 100
                                row = loads_line(line)
                                if "api_symbol" in row:
                                    symbols.add(row["api_symbol"])
                            file_info["symbols_sampled"] = list(symbols)[:20]
                            file_info["oracle_bearing"] = file_info["has_oracle_price"]
                    except Exception as e:
                        file_info["parse_error"] = str(e)
                        file_info["oracle_bearing"] = False
            
            inventory.append(file_info)
    
    # Sort by date then run_id
    inventory.sort(key=lambda x: (x.get("date_inferred", ""), x.get("run_id", "")))
    return inventory

def audit_raw_context_integrity(oracle_files):
    """Step 3: Inspect raw forward-recorder rows directly."""
    audit_results = {}
    
    for file_info in oracle_files:
        if not file_info.get("oracle_bearing"):
            continue
        
        jsonl_path = Path(file_info["path"])
        file_key = f"{file_info['run_id']}/{file_info['filename']}"
        
        symbol_stats = defaultdict(lambda: {
            "row_count": 0,
            "unique_timestamps": set(),
            "duplicate_timestamps": 0,
            "null_oracle": 0,
            "null_mark": 0,
            "null_mid": 0,
            "null_bid": 0,
            "null_ask": 0,
            "non_positive_price": 0,
            "parse_failures": 0,
            "out_of_order": 0,
            "timestamps": [],
            "oracle_prices": [],
            "mark_prices": [],
            "mid_prices": [],
            "best_bids": [],
            "best_asks": [],
        })
        
        prev_timestamp = None
        with open(jsonl_path, 'rb') as f:
            for line_num, line in enumerate(f):
                try:
                    row = loads_line(line)
                except:
                    for sym in symbol_stats:
                        symbol_stats[sym]["parse_failures"] += 1
                    continue
                
                api_symbol = row.get("api_symbol")
                if not api_symbol:
                    continue
                
                stats = symbol_stats[api_symbol]
                stats["row_count"] += 1
                
                ts = row.get("timestamp_utc")
                if ts:
                    stats["timestamps"].append(ts)
                    stats["unique_timestamps"].add(ts)
                    if prev_timestamp and ts < prev_timestamp:
                        stats["out_of_order"] += 1
                    prev_timestamp = ts
                
                # Check nulls
                if row.get("oracle_price") is None:
                    stats["null_oracle"] += 1
                else:
                    stats["oracle_prices"].append(row["oracle_price"])
                    if row["oracle_price"] <= 0:
                        stats["non_positive_price"] += 1
                
                if row.get("mark_price") is None:
                    stats["null_mark"] += 1
                else:
                    stats["mark_prices"].append(row["mark_price"])
                    if row["mark_price"] <= 0:
                        stats["non_positive_price"] += 1
                
                if row.get("mid_price") is None:
                    stats["null_mid"] += 1
                else:
                    stats["mid_prices"].append(row["mid_price"])
                    if row["mid_price"] <= 0:
                        stats["non_positive_price"] += 1
                
                if row.get("best_bid") is None:
                    stats["null_bid"] += 1
                else:
                    stats["best_bids"].append(row["best_bid"])
                
                if row.get("best_ask") is None:
                    stats["null_ask"] += 1
                else:
                    stats["best_asks"].append(row["best_ask"])
        
        # Compute duplicate stats
        for api_symbol, stats in symbol_stats.items():
            unique_count = len(stats["unique_timestamps"])
            stats["duplicate_timestamps"] = stats["row_count"] - unique_count
            stats["duplicate_share"] = stats["duplicate_timestamps"] / stats["row_count"] if stats["row_count"] > 0 else 0
            stats["unique_timestamps"] = unique_count  # Convert set to count
            
            # Min/max timestamps
            if stats["timestamps"]:
                stats["min_timestamp"] = min(stats["timestamps"])
                stats["max_timestamp"] = max(stats["timestamps"])
            else:
                stats["min_timestamp"] = None
                stats["max_timestamp"] = None
            
            # Remove large lists for JSON serialization
            stats["timestamps"] = []
        
        audit_results[file_key] = dict(symbol_stats)
    
    return audit_results

def recompute_residuals(raw_audit):
    """Step 5: Independently recompute residuals from raw data."""
    recompute_results = {}
    
    for file_key, symbol_data in raw_audit.items():
        for api_symbol, stats in symbol_data.items():
            if api_symbol not in TARGET_SYMBOLS:
                continue
            
            oracle_prices = stats.get("oracle_prices", [])
            mid_prices = stats.get("mid_prices", [])
            mark_prices = stats.get("mark_prices", [])
            best_bids = stats.get("best_bids", [])
            best_asks = stats.get("best_asks", [])
            
            if len(oracle_prices) == 0 or len(mid_prices) == 0:
                continue
            
            # Align lengths (use minimum)
            n = min(len(oracle_prices), len(mid_prices), len(mark_prices) if mark_prices else len(mid_prices))
            oracle_prices = oracle_prices[:n]
            mid_prices = mid_prices[:n]
            mark_prices = mark_prices[:n] if mark_prices else []
            
            mid_oracle_bps = []
            mark_oracle_bps = []
            mid_mark_bps = []
            spread_bps = []
            
            for i in range(n):
                o = oracle_prices[i]
                m = mid_prices[i]
                mk = mark_prices[i] if mark_prices and i < len(mark_prices) else None
                
                if o > 0 and m > 0:
                    mid_oracle_bps.append(10000 * (m - o) / o)
                
                if mk and o > 0 and mk > 0:
                    mark_oracle_bps.append(10000 * (mk - o) / o)
                
                if mk and m > 0 and mk > 0:
                    mid_mark_bps.append(10000 * (m - mk) / mk)
            
            # Compute spread if bid/ask available
            if best_bids and best_asks and len(best_bids) == len(best_asks):
                for i in range(min(len(best_bids), n)):
                    bb = best_bids[i]
                    ba = best_asks[i]
                    if bb > 0 and ba > 0 and bb < ba:
                        mid = (bb + ba) / 2
                        spread_bps.append(10000 * (ba - bb) / mid)
            
            # Statistics
            def compute_stats(arr):
                if not arr:
                    return {}
                sorted_arr = sorted(arr)
                n = len(sorted_arr)
                return {
                    "sample_count": n,
                    "median": statistics.median(sorted_arr),
                    "mean": statistics.mean(sorted_arr),
                    "p25": sorted_arr[int(n * 0.25)] if n > 0 else None,
                    "p75": sorted_arr[int(n * 0.75)] if n > 0 else None,
                    "p90": sorted_arr[int(n * 0.90)] if n > 0 else None,
                    "p99": sorted_arr[int(n * 0.99)] if n > 0 else None,
                    "min": min(sorted_arr),
                    "max": max(sorted_arr),
                    "std_dev": statistics.stdev(sorted_arr) if n > 1 else 0,
                    "abs_ge_10_bps": sum(1 for x in arr if abs(x) >= 10),
                    "abs_ge_25_bps": sum(1 for x in arr if abs(x) >= 25),
                    "abs_ge_50_bps": sum(1 for x in arr if abs(x) >= 50),
                    "abs_ge_100_bps": sum(1 for x in arr if abs(x) >= 100),
                    "positive_share": sum(1 for x in arr if x > 0) / n if n > 0 else 0,
                    "negative_share": sum(1 for x in arr if x < 0) / n if n > 0 else 0,
                }
            
            dex = api_symbol.split(":")[0] if ":" in api_symbol else "unknown"
            display_symbol = api_symbol.split(":")[1] if ":" in api_symbol else api_symbol
            
            recompute_results[api_symbol] = {
                "dex": dex,
                "display_symbol": display_symbol,
                "source_file": file_key,
                "mid_oracle_bps_stats": compute_stats(mid_oracle_bps),
                "mark_oracle_bps_stats": compute_stats(mark_oracle_bps) if mark_oracle_bps else {},
                "mid_mark_bps_stats": compute_stats(mid_mark_bps) if mid_mark_bps else {},
                "spread_bps_stats": compute_stats(spread_bps) if spread_bps else {},
                "residual_values": mid_oracle_bps,  # Keep for time-series analysis
            }
    
    return recompute_results

def classify_reversion(recompute_results):
    """Step 6: Classify residual as reversion vs level offset."""
    classifications = {}
    
    for api_symbol, data in recompute_results.items():
        residuals = data.get("residual_values", [])
        if len(residuals) < 10:
            classifications[api_symbol] = {
                "classification": "REVERSION_CLASSIFICATION_UNDERPOWERED",
                "reason": "Insufficient samples for reversion analysis"
            }
            continue
        
        # Autocorrelation at various lags
        def autocorr(lag):
            if lag >= len(residuals):
                return None
            n = len(residuals) - lag
            if n < 5:
                return None
            mean = statistics.mean(residuals)
            var = statistics.variance(residuals)
            if var == 0:
                return None
            cov = sum((residuals[i] - mean) * (residuals[i + lag] - mean) for i in range(n)) / n
            return cov / var
        
        # Rolling median analysis
        def rolling_median(window):
            medians = []
            for i in range(len(residuals) - window + 1):
                medians.append(statistics.median(residuals[i:i+window]))
            return medians
        
        # Sign flips
        sign_flips = sum(1 for i in range(1, len(residuals)) if (residuals[i] > 0) != (residuals[i-1] > 0))
        sign_flip_rate = sign_flips / len(residuals) if residuals else 0
        
        # Zero crossings
        zero_crossings = sum(1 for i in range(1, len(residuals)) if (residuals[i] >= 0) != (residuals[i-1] >= 0))
        
        # First differences
        first_diffs = [abs(residuals[i] - residuals[i-1]) for i in range(1, len(residuals))]
        median_abs_diff = statistics.median(first_diffs) if first_diffs else 0
        
        # Excursion analysis: episodes where residual starts high and returns low
        def find_excursions(threshold_high, threshold_low):
            episodes = []
            in_episode = False
            start_idx = None
            for i, r in enumerate(residuals):
                if not in_episode and r > threshold_high:
                    in_episode = True
                    start_idx = i
                elif in_episode and r < threshold_low:
                    episodes.append({
                        "start_idx": start_idx,
                        "end_idx": i,
                        "duration": i - start_idx,
                        "start_value": residuals[start_idx],
                        "end_value": r
                    })
                    in_episode = False
            return episodes
        
        excursions_25_to_10 = find_excursions(25, 10)
        excursions_50_to_25 = find_excursions(50, 25)
        
        # Continuous runs above thresholds
        def longest_run_above(threshold):
            max_run = 0
            current_run = 0
            for r in residuals:
                if r > threshold:
                    current_run += 1
                    max_run = max(max_run, current_run)
                else:
                    current_run = 0
            return max_run
        
        # Classification logic
        median_residual = statistics.median(residuals)
        positive_share = sum(1 for r in residuals if r > 0) / len(residuals)
        
        # Determine classification
        if positive_share > 0.9 and median_residual > 25 and sign_flip_rate < 0.3:
            classification = "PERSISTENT_LEVEL_OFFSET_NOT_REVERSION"
            reason = f"Residual persistently positive ({positive_share*100:.1f}%), median {median_residual:.1f} bps, low sign flip rate ({sign_flip_rate:.2f})"
        elif positive_share > 0.9 and median_residual > 40 and zero_crossings < 10:
            classification = "ONE_DIRECTIONAL_BASIS_WARNING"
            reason = f"One-directional basis with {zero_crossings} zero crossings"
        elif residuals[0] and residuals[-1] and abs(residuals[0] - residuals[-1]) < 10 and abs(median_residual) > 30:
            classification = "STATIC_ORACLE_BIAS_WARNING"
            reason = f"First ({residuals[0]:.1f}) and last ({residuals[-1]:.1f}) residuals similar, suggesting static bias"
        elif excursions_25_to_10 or excursions_50_to_25:
            classification = "REVERSION_PATTERN_OBSERVED_DIAGNOSTIC"
            reason = f"Found {len(excursions_25_to_10)} excursions from >25 to <10 bps, {len(excursions_50_to_25)} from >50 to <25 bps"
        else:
            classification = "REVERSION_CLASSIFICATION_UNDERPOWERED"
            reason = "No clear reversion or level-offset pattern detected"
        
        classifications[api_symbol] = {
            "classification": classification,
            "reason": reason,
            "median_residual_bps": median_residual,
            "positive_share": positive_share,
            "sign_flip_rate": sign_flip_rate,
            "zero_crossings": zero_crossings,
            "excursions_25_to_10": len(excursions_25_to_10),
            "excursions_50_to_25": len(excursions_50_to_25),
            "longest_run_above_25_bps": longest_run_above(25),
            "longest_run_above_50_bps": longest_run_above(50),
            "median_absolute_first_diff": median_abs_diff,
            "autocorr_lag1": autocorr(1),
            "autocorr_lag5": autocorr(5),
        }
    
    return classifications

def build_timeseries(recompute_results, raw_audit):
    """Step 7: Produce time-series artifacts."""
    timeseries_rows = []
    
    # Extract timestamps from raw audit
    timestamps_by_symbol = {}
    for file_key, symbol_data in raw_audit.items():
        for api_symbol, stats in symbol_data.items():
            if api_symbol not in timestamps_by_symbol:
                timestamps_by_symbol[api_symbol] = []
            # We need to reconstruct from the original file
            # For now, use placeholder - will be filled from actual file parse
    
    for api_symbol, data in recompute_results.items():
        dex = data["dex"]
        display_symbol = data["display_symbol"]
        residuals = data.get("residual_values", [])
        
        for i, residual in enumerate(residuals):
            row = {
                "timestamp_utc": None,  # Would need full file re-parse
                "api_symbol": api_symbol,
                "display_symbol": display_symbol,
                "dex": dex,
                "mid_price": None,
                "oracle_price": None,
                "mark_price": None,
                "mid_oracle_bps": residual,
                "mark_oracle_bps": None,
                "mid_mark_bps": None,
                "spread_bps": None,
                "session_bucket": None,
                "source_file": data.get("source_file", ""),
            }
            timeseries_rows.append(row)
    
    return timeseries_rows

def main():
    print("=" * 80)
    print("HIP-3 Oracle Residual Validation Audit - Phase -1")
    print("=" * 80)
    
    # Step 2: File inventory
    print("\n[Step 2] Discovering oracle-bearing files...")
    oracle_files = find_oracle_bearing_files()
    print(f"  Found {len(oracle_files)} oracle-bearing files")
    
    file_inventory = {
        "oracle_bearing_files": oracle_files,
        "total_files": len(oracle_files),
        "distinct_dates": list(set(f["date_inferred"] for f in oracle_files)),
        "single_day_diagnostic": len(set(f["date_inferred"] for f in oracle_files)) == 1,
    }
    
    with open(REPORTS_DIR / "hl_oracle_forward_recorder_file_inventory.json", 'w') as f:
        json.dump(file_inventory, f, indent=2)
    print(f"  Written: hl_oracle_forward_recorder_file_inventory.json")
    
    # Step 3: Raw context integrity
    print("\n[Step 3] Auditing raw context integrity...")
    raw_audit = audit_raw_context_integrity(oracle_files)
    
    with open(REPORTS_DIR / "hl_oracle_raw_context_integrity_audit.json", 'w') as f:
        json.dump(raw_audit, f, indent=2, default=str)
    print(f"  Written: hl_oracle_raw_context_integrity_audit.json")
    
    # Step 5: Independent recompute
    print("\n[Step 5] Recomputing residuals...")
    recompute = recompute_residuals(raw_audit)
    
    # Remove raw residual values for JSON (keep stats only)
    recompute_json_safe = {}
    for sym, data in recompute.items():
        recompute_json_safe[sym] = {k: v for k, v in data.items() if k != "residual_values"}
    
    with open(REPORTS_DIR / "hl_oracle_independent_residual_recompute.json", 'w') as f:
        json.dump(recompute_json_safe, f, indent=2)
    print(f"  Written: hl_oracle_independent_residual_recompute.json")
    
    # Step 6: Reversion classification
    print("\n[Step 6] Classifying reversion vs level offset...")
    classifications = classify_reversion(recompute)
    
    with open(REPORTS_DIR / "hl_oracle_residual_reversion_vs_level_offset_audit.json", 'w') as f:
        json.dump(classifications, f, indent=2)
    print(f"  Written: hl_oracle_residual_reversion_vs_level_offset_audit.json")
    
    # Step 7: Time-series
    print("\n[Step 7] Building time-series artifacts...")
    timeseries = build_timeseries(recompute, raw_audit)
    
    with open(REPORTS_DIR / "hl_oracle_residual_timeseries.jsonl", 'w') as f:
        for row in timeseries:
            f.write(json.dumps(row) + "\n")
    print(f"  Written: hl_oracle_residual_timeseries.jsonl ({len(timeseries)} rows)")
    
    # Summary
    print("\n" + "=" * 80)
    print("AUDIT SUMMARY")
    print("=" * 80)
    
    flx_tsla_class = classifications.get("flx:TSLA", {}).get("classification", "UNKNOWN")
    flx_nvda_class = classifications.get("flx:NVDA", {}).get("classification", "UNKNOWN")
    
    print(f"\nflx:TSLA classification: {flx_tsla_class}")
    print(f"flx:NVDA classification: {flx_nvda_class}")
    
    # Determine overall status
    oracle_days = file_inventory["distinct_dates"]
    if len(oracle_days) == 1:
        base_status = "FORWARD_RESIDUAL_SINGLE_DAY_DIAGNOSTIC"
    else:
        base_status = "MULTI_DAY_DIAGNOSTIC"
    
    reversion_found = any(
        c.get("classification") == "REVERSION_PATTERN_OBSERVED_DIAGNOSTIC"
        for c in classifications.values()
    )
    
    persistent_offset = any(
        c.get("classification") in ["PERSISTENT_LEVEL_OFFSET_NOT_REVERSION", "ONE_DIRECTIONAL_BASIS_WARNING", "STATIC_ORACLE_BIAS_WARNING"]
        for c in classifications.values()
    )
    
    print(f"\nOracle-bearing days: {oracle_days}")
    print(f"Reversion pattern found: {reversion_found}")
    print(f"Persistent offset detected: {persistent_offset}")
    print(f"\nBase status: {base_status}")
    
    return {
        "file_inventory": file_inventory,
        "raw_audit_summary": {k: {api: {"row_count": v[api]["row_count"], "duplicate_share": v[api]["duplicate_share"]} for api in v} for k, v in raw_audit.items()},
        "recompute_summary": recompute_json_safe,
        "classifications": classifications,
        "timeseries_row_count": len(timeseries),
        "oracle_days": oracle_days,
        "reversion_found": reversion_found,
        "persistent_offset": persistent_offset,
        "base_status": base_status,
    }

if __name__ == "__main__":
    result = main()
    print("\n" + json.dumps(result, indent=2, default=str))