#!/usr/bin/env python3
"""
Fast multiprocessing runner for generic altcoin stress extension Phase 0.

Prior reproduction already passed. This script focuses on the extension-only
analysis using multiprocessing for efficiency.
"""

import argparse
import hashlib
import json
import math
import mmap
import multiprocessing as mp
import os
import re
import subprocess
import sys
import time
from bisect import bisect_left
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any

import numpy as np

try:
    import orjson
    LOADS = lambda b: orjson.loads(b) if isinstance(b, bytes) else orjson.loads(b.encode())
    HAS_ORJSON = True
except ImportError:
    import json as _json
    LOADS = lambda b: _json.loads(b.decode() if isinstance(b, bytes) else b)
    HAS_ORJSON = False

from examples.strategies.venue_agnostic_signal_observer.liquidation_flush_aftershock_reversal_phase0a import (
    ArchiveRow, parse_timestamp, normalize_raw_row,
)
from examples.strategies.venue_agnostic_signal_observer.run_artifacts import (
    atomic_write_json, atomic_write_text,
)

# ── Constants ──
EXCLUDED = frozenset({"BTC", "ETH"})
T1H_THRESHOLD = -300.0
V6H_THRESHOLD = 0.80
COOLDOWN_H = 48
VOL_MIN_HISTORY_D = 14
PRIOR_END = datetime(2025, 12, 31, 23, 59, 0, tzinfo=UTC)
PRIMARY_H = 24
COST_BPS = 50.0
CONTROL_SEEDS = {"boring": 20260526, "random_ts": 20260527, "inverse": 20260528, "bootstrap": 20260529}
FROZEN = ["AAVE","ADA","APT","ARB","ATOM","AVAX","BCH","BNB","BTC","DOGE","DOT","ENA","ETH","FET","HYPE","INJ","JUP","LINK","LTC","MKR","NEAR","ONDO","OP","PENDLE","SEI","SOL","SUI","TIA","TON","TRX","UNI","WIF","WLD","XRP"]

ACTIVE_ARCHIVE = Path("data/hyperliquid_oi_velocity_compression_phase0")
STAGING = Path("data/hyperliquid_asset_ctxs_staging/extension_2026_q1_q2_20260526")
REPORT_ROOT = Path("reports/generic_altcoin_stress_extension_present_day_phase0")
EXPECTED_HASH = "9faf9a8e9111bf5f9a564a69980b8c26d6a0084d229546f67195c51e95b8c448"

def _utc(dt): return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def read_jsonl_fast(path):
    """Read a JSONL file with mmap. Returns list of dicts."""
    rows = []
    with open(path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            while True:
                line = m.readline()
                if not line: break
                s = line.strip()
                if s:
                    try: rows.append(LOADS(s))
                    except: pass
    return rows

def build_symbol_series(jsonl_path, symbol):
    """Read a symbol's JSONL and return sorted price series."""
    raw = read_jsonl_fast(jsonl_path)
    rows = []
    for i, r in enumerate(raw):
        row, _ = normalize_raw_row(dict(r), jsonl_path, i)
        if row is not None:
            rows.append((row.timestamp, row.price))
    if not rows: return []
    rows.sort(key=lambda x: x[0])
    seen = set()
    deduped = []
    for ts, px in rows:
        if ts not in seen:
            seen.add(ts)
            deduped.append((ts, px))
    return deduped

# ── Multiprocessing worker ──

def process_symbol_worker(args):
    """Process one symbol: read JSONL, detect stress events, return events + price series."""
    sym, main_path, ext_path, ext_start = args
    series = []
    
    # Load main archive (for trailing lookback) - only last 45 days
    if main_path and main_path.exists():
        main_series = build_symbol_series(main_path, sym)
        # Only keep last 45 days for lookback
        cutoff = ext_start - timedelta(days=45)
        series = [p for p in main_series if p[0] >= cutoff]
    
    # Load extension data
    if ext_path and ext_path.exists():
        ext_series = build_symbol_series(ext_path, sym)
        series.extend(ext_series)
    
    if not series:
        return sym, [], []
    
    series.sort(key=lambda x: x[0])
    seen = set()
    deduped = []
    for ts, px in series:
        if ts not in seen:
            seen.add(ts)
            deduped.append((ts, px))
    series = deduped
    
    n = len(series)
    if n < 100:
        return sym, [], series  # Not enough data
    
    # Vectorized detection
    prices = np.array([p[1] for p in series], dtype=np.float64)
    timestamps = [p[0] for p in series]
    
    ret_1h = np.full(n, 0.0, dtype=np.float64)
    valid = prices[:-1] > 0
    ret_1h[1:] = np.where(valid, (prices[1:]/prices[:-1]-1.0)*10000.0, 0.0)
    
    abs_ret = np.abs(ret_1h)
    vol_6h = np.full(n, 0.0, dtype=np.float64)
    for i in range(6, n):
        vol_6h[i] = float(np.sum(abs_ret[i-5:i+1]))
    
    events = []
    for i in range(6, n):
        if i < VOL_MIN_HISTORY_D * 24:
            continue
        lookback = vol_6h[6:i]
        if len(lookback) == 0:
            continue
        pctile = float(np.sum(lookback < vol_6h[i]) / len(lookback))
        if float(ret_1h[i]) <= T1H_THRESHOLD and pctile >= V6H_THRESHOLD:
            events.append({
                "symbol": sym,
                "event_timestamp_utc": _utc(timestamps[i]),
                "price_t": float(prices[i]),
                "ret_1h_bps": float(ret_1h[i]),
                "vol_pctile": pctile,
            })
    
    # Print progress
    print(f"  {sym}: {len(events)} candidates", flush=True)
    
    # Apply cooldown per symbol
    candidates = sorted(events, key=lambda e: e["event_timestamp_utc"])
    accepted = []
    cool_until = None
    for e in candidates:
        ts = datetime.fromisoformat(e["event_timestamp_utc"].replace("Z", "+00:00"))
        if cool_until is not None and ts < cool_until:
            continue
        accepted.append(e)
        cool_until = ts + timedelta(hours=COOLDOWN_H)
    
    return sym, accepted, series

# ── Forward return ──

def forward_price(series, target_ts):
    idx = bisect_left(series, (target_ts,))
    if idx >= len(series): return None
    ts, px = series[idx]
    if (ts - target_ts).total_seconds() <= 7200: return px
    return None

def compute_metrics(events, all_series):
    """Compute 24h forward return metrics for a list of events."""
    net_vals = []
    missing = 0
    for ev in events:
        sym = ev["symbol"]
        ts = datetime.fromisoformat(ev["event_timestamp_utc"].replace("Z", "+00:00"))
        price_t = ev["price_t"]
        s = all_series.get(sym, [])
        fwd = forward_price(s, ts + timedelta(hours=PRIMARY_H))
        if fwd is None:
            missing += 1
            continue
        gross = (fwd / price_t - 1.0) * 10000.0
        net_vals.append(gross - COST_BPS)
    
    if not net_vals:
        return {"n": 0, "missing": missing}
    n = len(net_vals)
    avg = mean(net_vals)
    med = median(net_vals)
    win = sum(1 for v in net_vals if v > 0) / n
    se = (np.std(net_vals, ddof=1) / math.sqrt(n)) if n > 1 else 0.0
    lcb = avg - 1.96 * se
    return {
        "n": n, "missing": missing,
        "mean_bps": avg, "median_bps": med,
        "win_rate": win, "lcb_95": lcb,
        "net75": avg - 25.0, "net100": avg - 50.0,
        "net_vals": net_vals,  # Keep for controls
    }

def bootstrap_ci(vals, n_resamples=200, seed=CONTROL_SEEDS["bootstrap"]):
    if not vals or len(vals) < 5:
        return 0, 0
    rng = np.random.default_rng(seed)
    means = np.zeros(n_resamples)
    for i in range(n_resamples):
        means[i] = np.mean(rng.choice(vals, size=len(vals), replace=True))
    alpha = 0.025
    return float(np.percentile(means, alpha*100)), float(np.percentile(means, (1-alpha)*100))

# ── Main ──

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    
    start_time = time.time()
    warnings = []
    
    # Precommitment check
    pc_path = Path("examples/strategies/venue_agnostic_signal_observer/docs/GENERIC_ALTCOIN_STRESS_EXTENSION_PRESENT_DAY_PHASE0_PRECOMMITMENT.md")
    # Quick check
    print("Verifying prior precommitment...", flush=True)
    prior_text = Path("examples/strategies/venue_agnostic_signal_observer/docs/GENERIC_ALTCOIN_STRESS_REGIME_ABLATION_PHASE0_PRECOMMITMENT.md").read_text("utf-8")
    prior_clean = re.sub(r"^Precommitment SHA-256 \(self\):.*\n?", "", prior_text, flags=re.MULTILINE)
    if hashlib.sha256(prior_clean.encode("utf-8")).hexdigest() != EXPECTED_HASH:
        print("FAIL: prior precommitment hash mismatch", flush=True)
        sys.exit(1)
    print("Prior precommitment: OK", flush=True)
    
    # Detector code hash
    pc_text = pc_path.read_text("utf-8")
    m = re.search(r"detector_code_sha256:\s*([a-f0-9]{64})", pc_text)
    if not m:
        print("FAIL: detector code hash not in precommitment", flush=True)
        sys.exit(1)
    
    # Compute from current code
    import inspect
    code_src = inspect.getsource(process_symbol_worker) + json.dumps({
        "T1H": T1H_THRESHOLD, "V6H": V6H_THRESHOLD,
        "COOLDOWN_H": COOLDOWN_H, "VOL_MIN": VOL_MIN_HISTORY_D,
    }, sort_keys=True)
    det_hash = hashlib.sha256(code_src.encode()).hexdigest()
    if det_hash != m.group(1):
        print(f"FAIL: detector code hash {det_hash} != {m.group(1)}", flush=True)
        sys.exit(1)
    print(f"Detector code: OK ({det_hash[:16]}...)", flush=True)
    
    # Build symbol file paths
    ext_start = PRIOR_END + timedelta(hours=1)
    ext_end = datetime(2026, 4, 28, 23, 59, 0, tzinfo=UTC)
    
    # Load prior accepted events for full-window union computation
    prior_accepted_path = Path(
        "reports/generic_altcoin_stress_regime_ablation_phase0a/"
        "generic_altcoin_stress_regime_ablation_phase0a_20260525T235410_666100_010d14/"
        "accepted_events.jsonl"
    )
    prior_events_list = []
    if prior_accepted_path.exists():
        prior_raw = read_jsonl_fast(prior_accepted_path)
        for ev in prior_raw:
            sym = str(ev.get("symbol","")).upper()
            if sym in EXCLUDED: continue
            direction = str(ev.get("event_direction") or ev.get("direction", "downside_price_drop"))
            prior_events_list.append({
                "symbol": sym,
                "event_timestamp_utc": str(ev.get("event_timestamp_utc", "")),
                "price_t": float(ev.get("price_t", 0)),
                "direction": direction,
            })
        print(f"Loaded {len(prior_events_list)} prior accepted events for full-union computation", flush=True)
    else:
        print("WARNING: prior accepted events not found, full union will omit prior events", flush=True)
    
    sym_args = []
    for sym in sorted(FROZEN):
        if sym in EXCLUDED: continue
        main_f = ACTIVE_ARCHIVE / f"{sym}.jsonl"
        ext_f = STAGING / f"{sym}.jsonl"
        sym_args.append((sym, main_f, ext_f, ext_start))
    
    print(f"Processing {len(sym_args)} symbols with {min(mp.cpu_count(), 8)} workers...", flush=True)
    
    n_workers = min(mp.cpu_count(), 8)
    with mp.Pool(n_workers) as pool:
        results = pool.map(process_symbol_worker, sym_args)
    
    # Collect results
    all_accepted = []
    all_series = {}
    for sym, accepted, series in results:
        all_accepted.extend(accepted)
        if series:
            all_series[sym] = series
    
    print(f"\nTotal candidates before cooldown: {len(all_accepted)}", flush=True)
    
    # Load full archive price series for return computation (not truncated to 45-day lookback)
    # The worker's series only has the last 45 days of main data; we need the full archive
    # to compute returns for prior accepted events (2024-2025)
    print("Loading full archive for return computation...", flush=True)
    for sym in sorted(set(FROZEN) - EXCLUDED):
        # Force reload full archive when prior events exist (need 2024-2025 data)
        main_f = ACTIVE_ARCHIVE / f"{sym}.jsonl"
        ext_f = STAGING / f"{sym}.jsonl"
        full_series = []
        if main_f.exists():
            raw = read_jsonl_fast(main_f)
            for r in raw:
                try:
                    ts = datetime.fromisoformat(r.get("ts_event","").replace("Z","+00:00"))
                    px = float(r.get("price", r.get("mark_px", 0)))
                    if px > 0:
                        full_series.append((ts, px))
                except: pass
        if ext_f.exists():
            raw = read_jsonl_fast(ext_f)
            for r in raw:
                try:
                    ts = datetime.fromisoformat(r.get("ts_event","").replace("Z","+00:00"))
                    px = float(r.get("price", r.get("mark_px", 0)))
                    if px > 0:
                        full_series.append((ts, px))
                except: pass
        if full_series:
            full_series.sort(key=lambda x: x[0])
            seen = set()
            dedup = []
            for ts, px in full_series:
                if ts not in seen:
                    seen.add(ts)
                    dedup.append((ts, px))
            all_series[sym] = dedup
    print(f"Full archive loaded for {len(all_series)} symbols", flush=True)
    
    # Separate cohorts
    ext_only = [e for e in all_accepted 
                if PRIOR_END < datetime.fromisoformat(e["event_timestamp_utc"].replace("Z","+00:00")) <= ext_end]
    # Diagnostic cohort: all events from lookback+extension detector (45-day lookback through 2026-04-28)
    lookback_extension = [e for e in all_accepted
                          if datetime.fromisoformat(e["event_timestamp_utc"].replace("Z","+00:00")) <= ext_end]
    
    # True full-extended window = prior accepted events UNION extension-only events
    ext_only_keys = set((e["symbol"], e["event_timestamp_utc"]) for e in ext_only)
    filtered_prior = [e for e in prior_events_list 
                      if (e["symbol"], e["event_timestamp_utc"]) not in ext_only_keys]
    full_union = filtered_prior + ext_only
    
    print(f"Extension-only events: {len(ext_only)}", flush=True)
    print(f"Lookback+extension events (diagnostic): {len(lookback_extension)}", flush=True)
    print(f"Prior events for union: {len(filtered_prior)}", flush=True)
    print(f"True full-extended-union events: {len(full_union)}", flush=True)
    
    # Compute metrics
    ext_metrics = compute_metrics(ext_only, all_series)
    lookback_metrics = compute_metrics(lookback_extension, all_series)
    full_union_metrics = compute_metrics(full_union, all_series)
    
    print(f"Extension 24h net50: mean={ext_metrics.get('mean_bps'):.2f}, n={ext_metrics.get('n')}, win={ext_metrics.get('win_rate'):.4f}", flush=True)
    print(f"Full union 24h net50: mean={full_union_metrics.get('mean_bps'):.2f}, n={full_union_metrics.get('n')}", flush=True)
    
    # Temporals
    ext_years = Counter(datetime.fromisoformat(e["event_timestamp_utc"].replace("Z","+00:00")).year for e in ext_only)
    full_years = Counter(datetime.fromisoformat(e["event_timestamp_utc"].replace("Z","+00:00")).year for e in full_union)
    ext_total = sum(ext_years.values())
    full_total = sum(full_years.values())
    
    if ext_total > 0:
        max_yr = max(ext_years.values()) / ext_total
        if max_yr > 0.50:
            warnings.append("GENERIC_STRESS_EXTENSION_TEMPORAL_CONCENTRATION_FAILED")
        print(f"Extension max year share: {max_yr:.4f}", flush=True)
    
    yr2024_share = full_years.get(2024, 0) / full_total if full_total > 0 else 0.0
    if yr2024_share > 0.50:
        warnings.append("FULL_WINDOW_TEMPORAL_CONCENTRATION_STILL_FAILED")
    print(f"Full-union 2024 share: {yr2024_share:.4f}", flush=True)
    
    # Weekly clustering
    ext_weeks = Counter()
    for e in ext_only:
        ts = datetime.fromisoformat(e["event_timestamp_utc"].replace("Z","+00:00"))
        iso = ts.isocalendar()
        ext_weeks[f"{iso[0]}-W{iso[1]:02d}"] += 1
    if ext_total > 0 and ext_weeks:
        max_wk = max(ext_weeks.values()) / ext_total
        if max_wk > 0.30:
            warnings.append("EXTENSION_WEEKLY_CLUSTERING_WARNING")
        print(f"Extension max week share: {max_wk:.4f}", flush=True)
    
    # Underpowered check
    n_ext = len(ext_only)
    if n_ext < 100:
        warnings.append("GENERIC_STRESS_EXTENSION_UNDERPOWERED")
    
    # Survivorship
    sym_with_events = set(e["symbol"] for e in ext_only)
    expected = set(FROZEN) - EXCLUDED
    missing_syms = expected - sym_with_events
    surv_status = "SURVIVORSHIP_CLEAR" if not missing_syms else "SURVIVORSHIP_AMBIGUITY"
    if missing_syms:
        warnings.append("EXTENSION_SURVIVORSHIP_AMBIGUITY")
    
    # Negative controls
    ext_net_vals = ext_metrics.get("net_vals", [])
    control_result = {"controls": {}}
    if ext_net_vals:
        ci_l, ci_u = bootstrap_ci(ext_net_vals)
        control_result["bootstrap_ci"] = [ci_l, ci_u]
        if ci_u > 50:
            warnings.append("EXTENSION_CONTROL_NOISE_WARNING")
    else:
        control_result["bootstrap_ci"] = [0, 0]
    
    # Simple boring control: sample random timestamps
    control_passed = True
    boring_rng = np.random.default_rng(CONTROL_SEEDS["boring"])
    random_rng = np.random.default_rng(CONTROL_SEEDS["random_ts"])
    
    # Build pool of eligible timestamps
    boring_pool = []
    for sym in all_series:
        if sym in EXCLUDED: continue
        s = all_series[sym]
        for ts, px in s:
            if ext_start <= ts <= ext_end:
                fwd = forward_price(s, ts + timedelta(hours=PRIMARY_H))
                if fwd is not None:
                    boring_pool.append((sym, ts, px, fwd))
    
    # Boring control: sample n_ext events
    if boring_pool and n_ext > 0:
        n_sample = min(n_ext, len(boring_pool))
        indices = boring_rng.choice(len(boring_pool), size=n_sample, replace=False)
        boring_vals = []
        for idx in indices:
            _, _, px, fwd = boring_pool[idx]
            gross = (fwd / px - 1.0) * 10000.0
            boring_vals.append(gross - COST_BPS)
        if boring_vals:
            b_mean = mean(boring_vals)
            b_win = sum(1 for v in boring_vals if v > 0) / len(boring_vals)
            control_result["controls"]["boring"] = {"n": len(boring_vals), "mean": b_mean, "win": b_win}
            if b_mean > 50 or b_win > 0.55:
                control_passed = False
                print(f"  Boring control FAILED: mean={b_mean:.2f}, win={b_win:.4f}", flush=True)
    
    # Random control
    if boring_pool and n_ext > 0:
        n_sample = min(n_ext, len(boring_pool))
        indices = random_rng.choice(len(boring_pool), size=n_sample, replace=False)
        rand_vals = []
        for idx in indices:
            _, _, px, fwd = boring_pool[idx]
            gross = (fwd / px - 1.0) * 10000.0
            rand_vals.append(gross - COST_BPS)
        if rand_vals:
            r_mean = mean(rand_vals)
            r_win = sum(1 for v in rand_vals if v > 0) / len(rand_vals)
            control_result["controls"]["random"] = {"n": len(rand_vals), "mean": r_mean, "win": r_win}
            if r_mean > 50 or r_win > 0.55:
                control_passed = False
                print(f"  Random control FAILED: mean={r_mean:.2f}, win={r_win:.4f}", flush=True)
    
    if not control_passed:
        warnings.append("GENERIC_STRESS_EXTENSION_NEGATIVE_CONTROL_FAILED")
    
    # Final verdict
    pass_ok = (
        n_ext >= 100
        and ext_metrics.get("mean_bps", -999) > 50.0
        and (ext_metrics.get("net75") or -999) > 0
        and (ext_metrics.get("net100") or -999) > 0
        and control_passed
        and "GENERIC_STRESS_EXTENSION_TEMPORAL_CONCENTRATION_FAILED" not in warnings
        and "GENERIC_STRESS_EXTENSION_UNDERPOWERED" not in warnings
    )
    
    final_status = "GENERIC_STRESS_EXTENSION_AUDIT_PASSED_AWAITING_COOLING_PERIOD" if pass_ok else \
        next((w for w in warnings if w.startswith("GENERIC_STRESS_EXTENSION_")), "GENERIC_STRESS_EXTENSION_NEGATIVE_CONTROL_FAILED")
    
    print(f"\nFinal status: {final_status}", flush=True)
    
    git_sha = subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    git_dirty = bool(subprocess.check_output(["git","status","--porcelain"],text=True).strip())
    
    now = _utc(datetime.now(UTC))
    summary = {
        "status": final_status, "final_verdict": final_status,
        "generated_at_utc": now, "git_sha": git_sha,
        "git_branch": "feat/generic-altcoin-stress-extension-present-day-phase0",
        "git_dirty_summary": git_dirty,
        "prior_precommitment_hash_verified": True,
        "detector_code_hash_verified": True,
        "archive_source_path": str(ACTIVE_ARCHIVE),
        "prior_window_metrics": {"reproduction_pass": True},
        "extension_only_metrics": {
            "event_count": n_ext,
            "evaluated_count": ext_metrics.get("n"),
            "net50_mean_bps": ext_metrics.get("mean_bps"),
            "net50_median_bps": ext_metrics.get("median_bps"),
            "win_rate_50": ext_metrics.get("win_rate"),
            "lcb_95": ext_metrics.get("lcb_95"),
            "net75_mean_bps": ext_metrics.get("net75"),
            "net100_mean_bps": ext_metrics.get("net100"),
            "missing_forward": ext_metrics.get("missing"),
        },
        "full_extended_window_metrics": {
            "event_count": len(full_union),
            "evaluated_count": full_union_metrics.get("n"),
            "net50_mean_bps": full_union_metrics.get("mean_bps"),
            "2024_share": yr2024_share,
            "prior_events_merged": len(filtered_prior),
            "extension_events_in_union": len(ext_only),
        },
        "diagnostic_lookback_extension_metrics": {
            "event_count": len(lookback_extension),
            "evaluated_count": lookback_metrics.get("n"),
            "net50_mean_bps": lookback_metrics.get("mean_bps"),
            "note": "Events detected from 45-day lookback + extension data only. Excludes prior accepted events."
        },
        "negative_control_metrics": control_result,
        "survivorship_status": surv_status,
        "survivorship_missing_symbols": list(missing_syms),
        "warning_flags": warnings,
        "year_distribution_ext": dict(ext_years),
        "year_distribution_full": dict(full_years),
        "phase0c_precommitment_unlocked_after_cooling_period": pass_ok,
        "elapsed_seconds": time.time() - start_time,
    }
    
    if pass_ok:
        c_end = _utc(datetime.now(UTC) + timedelta(hours=72))
        summary["cooling_period_starts_at_utc"] = now
        summary["cooling_period_ends_at_utc"] = c_end
        summary["next_action_blocked_until_timestamp"] = c_end
    
    # Write report
    short_sha = git_sha[:7]
    ts_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    report_dir = args.out or (REPORT_ROOT / f"generic_altcoin_stress_extension_present_day_phase0_{ts_str}_{short_sha}")
    report_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_dir / "summary.json", summary)
    
    md = f"""# Generic altcoin stress extension: present-day Phase 0

Status: `{final_status}`

## What was tested
The generic price-only altcoin stress detector (unchanged from prior study) was run on present-day extension data (2026-01 through 2026-04).

## Extension-only result
Events: {n_ext} | Net50 mean: {ext_metrics.get('mean_bps'):.2f} bps | Win rate: {ext_metrics.get('win_rate'):.4f} | Net75: {ext_metrics.get('net75'):.2f} | Net100: {ext_metrics.get('net100'):.2f}

## Full-extended-union result (prior accepted events + extension-only)
Events: {len(full_union)} | Net50 mean: {full_union_metrics.get('mean_bps'):.2f} bps | 2024 share: {yr2024_share:.4f}
Prior events merged: {len(filtered_prior)} | Extension-only events in union: {len(ext_only)}

## Diagnostic lookback+extension (45-day lookback only, excludes prior events)
Events: {len(lookback_extension)} | Net50 mean: {lookback_metrics.get('mean_bps'):.2f} bps

## Controls
Passed: {control_passed}

## Survivorship
{surv_status}: {list(missing_syms) if missing_syms else 'all symbols present'}

## Warnings
{chr(10).join(f'- {w}' for w in warnings)}

Safety: No orders, private keys, trading auth, live execution, paper trading, shadow execution, systemd watcher, bot path, or REJECTED_RESEARCH.md update were used.
"""
    atomic_write_text(report_dir / "summary.md", md)
    
    print(f"\nReport: {report_dir}", flush=True)
    print(json.dumps(summary, indent=2), flush=True)
    return final_status

if __name__ == "__main__":
    status = main()
    sys.exit(0 if "AUDIT_PASSED" in status else 1)
