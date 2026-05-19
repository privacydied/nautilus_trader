#!/usr/bin/env python3
"""
Stage B: Full evaluator for Family 3 v1 funding_falling_oi_unwind.

Study: family3_funding_falling_oi_unwind_v1

Primary hypothesis:
  Negative funding extreme + falling OI -> positive BTC spot forward return

Primary cells (exactly 2):
  1. negative_funding_extreme + falling_oi + 24h
  2. negative_funding_extreme + falling_oi + 48h

Hard constraints:
- Public-data observer only.
- No auth, API keys, private keys, orders, execution, paper/shadow/governance/bot.
- Reuse Stage A event-selection logic exactly.
- Do not modify precommitment after Stage B starts.
- Do not re-tune thresholds, costs, horizons, null method, or FDR.
"""

import argparse
import csv
import hashlib
import io
import json
import math
import os
import random
import sys
import urllib.request
import zipfile
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Frozen constants (from precommitment)
# ---------------------------------------------------------------------------

SYMBOL = "BTCUSDT"
BASE_URL = "https://data.binance.vision"
WARMUP_DAYS = 180
OI_PRIMARY_FIELD = "sum_open_interest"
TIMESTAMP_FIELD = "create_time"
FUNDING_INTERVAL_H = 8
FUNDING_HOURS = [0, 8, 16]

HORIZONS = [24, 48]
COST_BPS = 50  # one-way per entry or exit; net = scored_return_bps - 100 (round trip)
ROUND_TRIP_BPS = 100

# Gates
MIN_EVENTS = 50
HOLDOUT_MIN_EVENTS = 50
WIN_RATE_THRESHOLD = 0.55
WORST_DECILE_THRESHOLD = -50  # > -50 bps
BASELINE_DELTA_BPS = 10  # >= 10
NULL_ALPHA = 0.05
FDR_ALPHA = 0.05
TRAIN_FRAC = 0.7
N_SHUFFLES = 1000
SEED = 42

START_YM = "2020-09"
END_YM = "2026-01"
YMD = "%Y-%m-%d"
YM = "%Y-%m"
DT_FMT = "%Y-%m-%d %H:%M:%S"

CACHE_DIR = "/tmp/binance_metrics_falling_oi"

# Paths
MONTHLY_FUNDING_PATH = "data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{ym}.zip"
DAILY_OI_PATH = "data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date}.zip"
SPOT_KLINES_PATH = "data/spot/monthly/klines/{symbol}/1h/{symbol}-1h-{ym}.zip"

# Report paths
OUTPUT_DIR = "reports/funding_falling_oi_unwind_v1"
PRECOMMITMENT_PATH = "examples/strategies/venue_agnostic_signal_observer/docs/FUNDING_FALLING_OI_UNWIND_PRECOMMITMENT.md"
PHASE0_REPORT_PATH = "reports/funding_falling_oi_unwind_v1/phase0_population_report.json"
PREFLIGHT_PATH = "reports/funding_falling_oi_unwind_v1/preflight_stage_b_audit.json"
REGISTRY_PATH = "examples/strategies/venue_agnostic_signal_observer/docs/REJECTED_RESEARCH.md"

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _mkdir_cache():
    os.makedirs(CACHE_DIR, exist_ok=True)

def iter_months(start_ym, end_ym):
    start = datetime.strptime(start_ym, YM)
    end = datetime.strptime(end_ym, YM)
    cur = start
    while cur <= end:
        yield cur.strftime(YM)
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)

def download_zip(url, timeout=30):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

def download_zip_cached(url, cache_key, timeout=30):
    _mkdir_cache()
    cache_path = os.path.join(CACHE_DIR, cache_key.replace("/", "_"))
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            return f.read()
    data = download_zip(url, timeout)
    with open(cache_path, "wb") as f:
        f.write(data)
    return data

def extract_csv_from_zip(zip_bytes):
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            return None, None
        with zf.open(names[0]) as f:
            content = f.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    return reader.fieldnames, rows

def parse_timestamp(ts_str):
    try:
        return datetime.strptime(ts_str.strip(), DT_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None

def parse_klines_timestamp(ts_int):
    try:
        if ts_int > 100_000_000_000_000:  # > 1e14 -> us
            return datetime.fromtimestamp(ts_int / 1_000_000, tz=timezone.utc)
        else:
            return datetime.fromtimestamp(ts_int / 1_000, tz=timezone.utc)
    except (OverflowError, ValueError, OSError):
        return None

def sha256_of_bytes(data):
    return hashlib.sha256(data).hexdigest()

def sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def get_git_sha():
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        return result.stdout.strip()
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_funding_all():
    months = list(iter_months(START_YM, END_YM))
    print(f"Loading {len(months)} monthly funding rate files...")
    all_funding = []
    for i, ym in enumerate(months):
        path = MONTHLY_FUNDING_PATH.format(symbol=SYMBOL, ym=ym)
        url = f"{BASE_URL}/{path}"
        cache_key = f"funding_{ym}.zip"
        try:
            zip_bytes = download_zip_cached(url, cache_key, timeout=15)
            hdr, rows = extract_csv_from_zip(zip_bytes)
            if hdr and rows:
                for r in rows:
                    try:
                        calc_time_ms = int(r["calc_time"])
                        rate = float(r["last_funding_rate"])
                        ts = datetime.fromtimestamp(calc_time_ms / 1000, tz=timezone.utc)
                        all_funding.append({"ts": ts, "funding_rate": rate})
                    except (ValueError, KeyError):
                        pass
        except Exception:
            continue
    all_funding.sort(key=lambda x: x["ts"])
    print(f"  Total: {len(all_funding)} funding rows")
    return all_funding


def load_oi_for_dates(dates):
    dates_sorted = sorted(set(dates))
    print(f"Loading {len(dates_sorted)} daily OI files (parallel)...")
    oi_by_date = {}
    loaded = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {}
        for d in dates_sorted:
            path = DAILY_OI_PATH.format(symbol=SYMBOL, date=d)
            url = f"{BASE_URL}/{path}"
            cache_key = f"oi_daily_{d}.zip"
            future = executor.submit(download_zip_cached, url, cache_key, 15)
            future_map[future] = (d, path, cache_key)
        for f in as_completed(future_map):
            d, path, cache_key = future_map[f]
            try:
                zip_bytes = f.result()
                hdr, rows = extract_csv_from_zip(zip_bytes)
                if hdr and rows:
                    parsed = []
                    for r in rows:
                        ts = parse_timestamp(r.get(TIMESTAMP_FIELD, ""))
                        if ts:
                            try:
                                oi_val = float(r[OI_PRIMARY_FIELD])
                                parsed.append({"ts": ts, "oi": oi_val})
                            except (ValueError, KeyError):
                                pass
                    if parsed:
                        parsed.sort(key=lambda x: x["ts"])
                        oi_by_date[d] = parsed
                        loaded += 1
                    else:
                        failed += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
    all_oi = []
    for date_str in sorted(oi_by_date.keys()):
        all_oi.extend(oi_by_date[date_str])
    all_oi.sort(key=lambda x: x["ts"])
    print(f"  Loaded {loaded} files, {failed} missing, {len(all_oi)} rows")
    return all_oi


def load_spot_klines():
    months = list(iter_months(START_YM, END_YM))
    print(f"Loading {len(months)} monthly spot klines files...")
    spot_klines = []
    for i, ym in enumerate(months):
        path = SPOT_KLINES_PATH.format(symbol=SYMBOL, ym=ym)
        url = f"{BASE_URL}/{path}"
        cache_key = f"spot_klines_{ym}.zip"
        try:
            zip_bytes = download_zip_cached(url, cache_key, timeout=30)
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                names = [n for n in zf.namelist() if n.endswith(".csv")]
                if not names:
                    continue
                with zf.open(names[0]) as f:
                    content = f.read().decode("utf-8")
            for line in content.strip().split("\n"):
                parts = line.split(",")
                if len(parts) >= 5:
                    try:
                        open_ms = int(parts[0])
                        close = float(parts[4])
                        ts = parse_klines_timestamp(open_ms)
                        if ts:
                            spot_klines.append({"ts": ts, "close": close})
                    except (ValueError, IndexError):
                        pass
        except Exception:
            continue
    spot_klines.sort(key=lambda x: x["ts"])
    print(f"  Total: {len(spot_klines)} klines")
    return spot_klines


# ---------------------------------------------------------------------------
# Stage A reconciliation
# ---------------------------------------------------------------------------

def read_phase0_report():
    """Read and return the Stage A phase0 report. Exits on failure."""
    if not os.path.exists(PHASE0_REPORT_PATH):
        print(f"ERROR: Stage A report not found: {PHASE0_REPORT_PATH}")
        print("Run Stage A first.")
        sys.exit(1)

    with open(PHASE0_REPORT_PATH) as f:
        report = json.load(f)

    if report.get("outcome") != "POPULATION_SUFFICIENT":
        print(f"Stage A outcome is {report['outcome']}, not POPULATION_SUFFICIENT.")
        print("Stage B cannot proceed.")
        sys.exit(1)
    return report


def read_preflight_verdict():
    """Read and check preflight verdict."""
    if not os.path.exists(PREFLIGHT_PATH):
        print(f"ERROR: Preflight audit not found: {PREFLIGHT_PATH}")
        print("Run preflight Stage B audit first.")
        sys.exit(1)
    with open(PREFLIGHT_PATH) as f:
        preflight = json.load(f)
    v = preflight.get("preflight_verdict")
    if v != "PREFLIGHT_PASSED":
        print(f"Preflight verdict is {v}, not PREFLIGHT_PASSED. Stage B blocked.")
        sys.exit(1)
    return preflight


# ---------------------------------------------------------------------------
# Event selection (reuse Stage A logic exactly)
# ---------------------------------------------------------------------------

def select_events(all_funding, all_oi):
    """
    Replicate Stage A event selection exactly.

    Returns:
        falling_oi_events: list of {ts, funding_rate}
        split_ts: datetime of 70/30 split
        counts: dict with expected Stage A counts
    """
    first_ts = all_funding[0]["ts"]
    warmup_end = first_ts + timedelta(days=WARMUP_DAYS)

    negative_extreme_events = []
    for i, fr in enumerate(all_funding):
        if fr["ts"] <= warmup_end:
            continue
        if fr["ts"].hour not in FUNDING_HOURS or fr["ts"].minute != 0 or fr["ts"].second != 0:
            continue
        lookback_start = fr["ts"] - timedelta(days=WARMUP_DAYS)
        past_rows = [r for r in all_funding[:i] if r["ts"] >= lookback_start]
        if len(past_rows) < 50:
            continue
        past_rates = sorted([r["funding_rate"] for r in past_rows])
        n = len(past_rates)
        bot5 = past_rates[min(int(n * 0.05), n - 1)]
        if fr["funding_rate"] <= bot5:
            negative_extreme_events.append({
                "ts": fr["ts"],
                "funding_rate": fr["funding_rate"],
            })

    # OI classification
    falling_oi_events = []
    for ev in negative_extreme_events:
        s8h = ev["ts"] - timedelta(hours=8)
        oi_end_val = None
        oi_start_val = None
        for row in reversed(all_oi):
            if row["ts"] <= ev["ts"]:
                oi_end_val = row["oi"]
                break
        for row in reversed(all_oi):
            if row["ts"] <= s8h:
                oi_start_val = row["oi"]
                break
        if oi_end_val is not None and oi_start_val is not None and oi_start_val > 0:
            oi_change_pct = (oi_end_val - oi_start_val) / oi_start_val
            if oi_change_pct <= 0:
                falling_oi_events.append(ev)

    falling_oi_sorted = sorted(falling_oi_events, key=lambda x: x["ts"])
    split_idx = int(len(falling_oi_sorted) * TRAIN_FRAC)
    split_ts = falling_oi_sorted[split_idx]["ts"]
    train_events = [e for e in falling_oi_sorted if e["ts"] <= split_ts]
    holdout_events = [e for e in falling_oi_sorted if e["ts"] > split_ts]

    counts = {
        "total": len(falling_oi_sorted),
        "train": len(train_events),
        "holdout": len(holdout_events),
    }
    return falling_oi_sorted, split_ts, counts


def reconcile_counts(computed_counts, phase0_report):
    """Assert computed counts match Stage A report."""
    p0b = phase0_report.get("phase_0b", {})
    expected = {
        "total": p0b.get("negative_extreme_falling_oi_count"),
        "train": p0b.get("train_count"),
        "holdout": p0b.get("holdout_count"),
    }
    for key in ["total", "train", "holdout"]:
        c = computed_counts.get(key)
        e = expected.get(key)
        if c != e:
            print(f"RECONCILIATION FAILED: {key} computed={c} expected={e}")
            print("Stage A and Stage B event selection diverged. Aborting.")
            sys.exit(1)
    print(f"  Reconciliation: total={computed_counts['total']}, "
          f"train={computed_counts['train']}, holdout={computed_counts['holdout']}")


# ---------------------------------------------------------------------------
# Return computation (exact 1h kline alignment)
# ---------------------------------------------------------------------------

def compute_returns(events, spot_klines, horizons):
    """
    Compute forward returns using exact kline timestamp matching.

    Entry price: 1h kline whose open_time == settlement_ts (funding grid aligns to 1h).
    Horizon price: 1h kline whose open_time == settlement_ts + horizon.
    If exact match not found, mark fwd_available=False.

    Net return bps = scored_return * 10000 - ROUND_TRIP_BPS
    scored_return = +raw_return (long BTC = positive score)

    Returns events list with return fields added.
    """
    # Build dict of ts -> close for O(1) lookup
    spot_by_ts = {}
    for k in spot_klines:
        ts_key = k["ts"].replace(microsecond=0) if k["ts"].microsecond else k["ts"]
        if ts_key not in spot_by_ts:
            spot_by_ts[ts_key] = k["close"]

    for ev in events:
        settlement_ts = ev["ts"].replace(microsecond=0)

        # Entry price: exact 1h kline at settlement_ts
        entry_price = spot_by_ts.get(settlement_ts)

        ev["entry_price"] = entry_price
        ev["entry_exact"] = entry_price is not None

        if entry_price is None or entry_price <= 0:
            for h in horizons:
                ev[f"fwd_available_{h}h"] = False
                ev[f"raw_return_{h}h"] = None
                ev[f"net_return_{h}h"] = None
            continue

        for h in horizons:
            horizon_ts = (settlement_ts + timedelta(hours=h)).replace(microsecond=0)
            fwd_price = spot_by_ts.get(horizon_ts)

            if fwd_price is not None and fwd_price > 0:
                raw_return = (fwd_price - entry_price) / entry_price
                scored_return = raw_return  # long BTC = positive score
                net_return_bps = scored_return * 10000 - ROUND_TRIP_BPS
                ev[f"fwd_price_{h}h"] = fwd_price
                ev[f"fwd_available_{h}h"] = True
                ev[f"raw_return_{h}h"] = raw_return
                ev[f"net_return_{h}h"] = net_return_bps
            else:
                ev[f"fwd_available_{h}h"] = False
                ev[f"raw_return_{h}h"] = None
                ev[f"net_return_{h}h"] = None

    return events


# ---------------------------------------------------------------------------
# Cell evaluation
# ---------------------------------------------------------------------------

def evaluate_cell(events, horizon):
    """
    Evaluate a single cell on its holdout set.

    Cells: negative_funding_extreme + falling_oi at given horizon.
    All events in the provided list are already pre-filtered.

    Returns dict with gate results.
    """
    cell_key = f"negative_funding_extreme_falling_oi_{horizon}h"

    fwd_field = f"fwd_available_{horizon}h"
    net_field = f"net_return_{horizon}h"
    raw_field = f"raw_return_{horizon}h"

    valid = [e for e in events if e.get(fwd_field)]
    n = len(valid)

    result = {
        "cell_key": cell_key,
        "horizon": f"{horizon}h",
        "n_events": n,
        "cost_bps": ROUND_TRIP_BPS,
    }

    if n < MIN_EVENTS:
        result["verdict"] = "NEEDS_MORE_DATA"
        return result

    net_bps_list = [e[net_field] for e in valid]
    raw_bps_list = [e[raw_field] * 10000 for e in valid]

    mean_net = sum(net_bps_list) / n
    mean_gross = sum(raw_bps_list) / n
    sorted_bps = sorted(net_bps_list)
    median_net = sorted_bps[n // 2]
    win_count = sum(1 for b in net_bps_list if b > 0)
    win_rate = win_count / n
    worst_decile = sorted_bps[int(n * 0.1)]

    result["mean_gross_bps"] = round(mean_gross, 4)
    result["mean_net_bps"] = round(mean_net, 4)
    result["median_net_bps"] = round(median_net, 4)
    result["win_rate"] = round(win_rate, 4)
    result["win_count"] = win_count
    result["worst_decile_net_bps"] = round(worst_decile, 4)

    # Baseline delta: compare cell mean net vs all eligible funding settlements
    # (computed and set externally)
    baseline_delta = None
    result["baseline_delta_bps"] = baseline_delta if baseline_delta is not None else None

    # Gate checks
    gates_pass = True
    failures = []

    if not (mean_net > 0):
        gates_pass = False
        failures.append(f"mean_net_bps={mean_net:.4f} <= 0")
    if not (median_net > 0):
        gates_pass = False
        failures.append(f"median_net_bps={median_net:.4f} <= 0")
    if not (win_rate >= WIN_RATE_THRESHOLD):
        gates_pass = False
        failures.append(f"win_rate={win_rate:.4f} < {WIN_RATE_THRESHOLD}")
    if not (worst_decile > WORST_DECILE_THRESHOLD):
        gates_pass = False
        failures.append(f"worst_decile={worst_decile:.4f} <= {WORST_DECILE_THRESHOLD}")

    result["gates_pass"] = gates_pass
    result["gate_failures"] = failures

    if gates_pass:
        result["verdict"] = "GATES_PASSED"
    else:
        result["verdict"] = "GATES_FAILED"
        result["reason"] = "; ".join(failures)

    return result


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

def compute_baseline(all_eligible_with_returns, horizon):
    """
    Compute baseline mean net bps from all eligible (non-extreme) funding settlements.
    Eligible = post-warmup, on-grid settlements (may include non-extreme).
    """
    fwd_field = f"fwd_available_{horizon}h"
    net_field = f"net_return_{horizon}h"
    valid = [e for e in all_eligible_with_returns if e.get(fwd_field)]
    if not valid:
        return None
    return sum(e[net_field] for e in valid) / len(valid)


# ---------------------------------------------------------------------------
# Null test (timestamp-shuffle)
# ---------------------------------------------------------------------------

def run_timestamp_shuffle_null(events, horizon, eligible_pool,
                                n_shuffles=N_SHUFFLES, seed=SEED):
    """
    Timestamp-shuffle null test.

    For each shuffle: draw n random eligible settlement bps values,
    compute mean net bps, compare observed cell mean to null distribution.
    """
    cell_key = f"negative_funding_extreme_falling_oi_{horizon}h"
    fwd_field = f"fwd_available_{horizon}h"
    net_field = f"net_return_{horizon}h"

    valid = [e for e in events if e.get(fwd_field)]
    n = len(valid)

    if n < MIN_EVENTS:
        return {"cell_key": cell_key, "n_events": n, "p_value": None,
                "null_mean": None, "verdict": "NEEDS_MORE_DATA"}

    observed_mean = sum(e[net_field] for e in valid) / n

    # Build eligible pool bps
    eligible_bps = [e[net_field] for e in eligible_pool if e.get(fwd_field)]
    if len(eligible_bps) < n:
        return {"cell_key": cell_key, "n_events": n, "p_value": None,
                "null_mean": None, "error": f"Pool {len(eligible_bps)} < n {n}"}

    rng = random.Random(seed)
    null_means = []
    for _ in range(n_shuffles):
        sample = rng.sample(eligible_bps, n)
        null_means.append(sum(sample) / n)

    null_mean = sum(null_means) / n_shuffles
    null_var = sum((m - null_mean) ** 2 for m in null_means) / n_shuffles
    null_std = null_var ** 0.5

    # One-tailed: how many null means >= observed mean
    count_extreme = sum(1 for m in null_means if m >= observed_mean)
    p_value = (count_extreme + 1) / (n_shuffles + 1)

    return {
        "cell_key": cell_key,
        "horizon": f"{horizon}h",
        "n_events": n,
        "observed_mean": observed_mean,
        "null_mean": null_mean,
        "null_std": null_std,
        "p_value": p_value,
        "n_shuffles": n_shuffles,
        "eligible_pool_size": len(eligible_bps),
    }


# ---------------------------------------------------------------------------
# FDR (Benjamini-Yekutieli)
# ---------------------------------------------------------------------------

def benjamini_yekutieli(p_values, alpha=FDR_ALPHA):
    m = len(p_values)
    if m == 0:
        return []
    c_m = sum(1 / (i + 1) for i in range(m))
    sorted_idx = sorted(range(m), key=lambda i: p_values[i])
    sorted_p = [p_values[i] for i in sorted_idx]
    thresholds = [(i + 1) / m * alpha / c_m for i in range(m)]
    max_reject = -1
    for i in range(m):
        if sorted_p[i] <= thresholds[i]:
            max_reject = i
    rejected = [False] * m
    for i in range(max_reject + 1):
        rejected[sorted_idx[i]] = True
    return rejected


# ---------------------------------------------------------------------------
# Train/holdout split
# ---------------------------------------------------------------------------

def compute_holdout(events, horizon, split_ts):
    """Split events into train (<=split_ts) and holdout (>split_ts)."""
    fwd_field = f"fwd_available_{horizon}h"
    valid = [e for e in events if e.get(fwd_field)]
    train = [e for e in valid if e["ts"] <= split_ts]
    holdout = [e for e in valid if e["ts"] > split_ts]
    return train, holdout


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def run_evaluation():
    """Run the full Stage B evaluation."""
    run_id = f"funding_falling_oi_unwind_v1_{datetime.now().strftime('%Y%m%dT%H%M%S')}_{os.urandom(3).hex()}"
    output_dir = os.path.join(OUTPUT_DIR, run_id)
    os.makedirs(output_dir, exist_ok=True)

    # Read Stage A report and preflight
    phase0_report = read_phase0_report()
    global preflight
    preflight = read_preflight_verdict()

    metadata = {
        "run_id": run_id,
        "study_id": "family3_funding_falling_oi_unwind_v1",
        "precommitment_sha256": phase0_report.get("precommitment_sha256"),
        "git_sha": get_git_sha(),
        "git_generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "preflight_verdict": preflight.get("preflight_verdict"),
        "stage_a_outcome": phase0_report.get("outcome"),
        "safety_mode": "public_data_observer_only",
    }

    print("=" * 70)
    print("Stage B: Full Evaluation — Family 3 v1 Falling OI Unwind")
    print("=" * 70)
    print(f"Run ID: {run_id}")
    print(f"Seed: {SEED}")
    print()

    # ---- Load data ----
    print("--- Loading Funding Data ---")
    all_funding = load_funding_all()
    print()

    print("--- Loading OI Data ---")
    # Need OI for event selection
    # First identify potential event dates (funding extreme events)
    first_ts = all_funding[0]["ts"]
    warmup_end = first_ts + timedelta(days=WARMUP_DAYS)
    extreme_dates = set()
    for i, fr in enumerate(all_funding):
        if fr["ts"] <= warmup_end:
            continue
        if fr["ts"].hour not in FUNDING_HOURS or fr["ts"].minute != 0:
            continue
        lookback_start = fr["ts"] - timedelta(days=WARMUP_DAYS)
        past_rows = [r for r in all_funding[:i] if r["ts"] >= lookback_start]
        if len(past_rows) < 50:
            continue
        past_rates = sorted([r["funding_rate"] for r in past_rows])
        n = len(past_rates)
        bot5 = past_rates[min(int(n * 0.05), n - 1)]
        if fr["funding_rate"] <= bot5:
            extreme_dates.add(fr["ts"].strftime(YMD))
            extreme_dates.add((fr["ts"] - timedelta(hours=8)).strftime(YMD))
    all_oi = load_oi_for_dates(list(extreme_dates))
    print()

    print("--- Selecting Events ---")
    falling_oi_events, split_ts, counts = select_events(all_funding, all_oi)
    reconcile_counts(counts, phase0_report)
    print(f"  Split: {split_ts.isoformat()}")
    print()

    print("--- Loading Spot Klines ---")
    spot_klines = load_spot_klines()
    print()

    # ---- Compute returns ----
    print("--- Computing Forward Returns (exact 1h kline alignment) ---")
    falling_oi_events = compute_returns(falling_oi_events, spot_klines, HORIZONS)

    # Summary
    for h in HORIZONS:
        avail = sum(1 for e in falling_oi_events if e.get(f"fwd_available_{h}h"))
        print(f"  {h}h: {avail}/{len(falling_oi_events)} events with forward price")

    # Build eligible pool for null/baseline
    eligible_pool = []
    for i, fr in enumerate(all_funding):
        if fr["ts"] <= warmup_end:
            continue
        if fr["ts"].hour not in FUNDING_HOURS or fr["ts"].minute != 0:
            continue
        # No extreme filtering - all post-warmup on-grid settlements
        eligible_pool.append({"ts": fr["ts"], "funding_rate": fr["funding_rate"]})
    eligible_pool = compute_returns(eligible_pool, spot_klines, HORIZONS)
    print(f"  Eligible pool for null: {len(eligible_pool)} events\n")

    # ---- Cell evaluation ----
    print("--- Primary Cell Evaluation (negative_extreme + falling_oi) ---")
    cell_results = []
    for h in HORIZONS:
        cr = evaluate_cell(falling_oi_events, h)
        cell_results.append(cr)
        print(f"  {cr['cell_key']}: n={cr['n_events']}, mean_net={cr.get('mean_net_bps')}, "
              f"wr={cr.get('win_rate')}, verdict={cr['verdict']}")

    # ---- Baseline ----
    print("\n--- Baseline ---")
    for i, h in enumerate(HORIZONS):
        baseline_mean = compute_baseline(eligible_pool, h)
        if baseline_mean is not None and cell_results[i].get("mean_net_bps") is not None:
            delta = cell_results[i]["mean_net_bps"] - baseline_mean
            cell_results[i]["baseline_bps"] = round(baseline_mean, 4)
            cell_results[i]["baseline_delta_bps"] = round(delta, 4)
            if delta < BASELINE_DELTA_BPS:
                cell_results[i]["gates_pass"] = False
                cell_results[i]["gate_failures"].append(
                    f"baseline_delta={delta:.4f} < {BASELINE_DELTA_BPS}")
                cell_results[i]["verdict"] = "GATES_FAILED"
                cell_results[i]["reason"] = "; ".join(cell_results[i]["gate_failures"])
        print(f"  {h}h: baseline={cell_results[i].get('baseline_bps')}, "
              f"delta={cell_results[i].get('baseline_delta_bps')}")

    # ---- Null test ----
    print("\n--- Timestamp-Shuffle Null ---")
    null_results = []
    for h in HORIZONS:
        nr = run_timestamp_shuffle_null(falling_oi_events, h, eligible_pool)
        null_results.append(nr)
        print(f"  {h}h: p={nr.get('p_value')}, obs_mean={nr.get('observed_mean')}, "
              f"null_mean={nr.get('null_mean')}")

    # ---- Merge gate + null ----
    cell_verdicts = []
    for cr, nr in zip(cell_results, null_results):
        cv = dict(cr)
        cv["null_p_value"] = nr.get("p_value")
        cv["null_mean"] = nr.get("null_mean")

        if cv["verdict"] == "NEEDS_MORE_DATA":
            pass
        elif cv.get("null_p_value") is not None and cv["null_p_value"] > NULL_ALPHA:
            # Gates passed but null failed
            if cv["verdict"] == "GATES_PASSED":
                cv["verdict"] = "NULL_FAILED"
                cv["reason"] = f"Null p={cv['null_p_value']:.4f} > {NULL_ALPHA}"
        elif cv.get("gates_pass"):
            cv["verdict"] = "GATES_AND_NULL_PASSED"
        cell_verdicts.append(cv)

    # ---- FDR ----
    print("\n--- FDR (Benjamini-Yekutieli, alpha=0.05, family_size=2) ---")
    p_values = [nr.get("p_value", 1.0) for nr in null_results]
    p_values = [p if p is not None else 1.0 for p in p_values]
    fdr_rejected = benjamini_yekutieli(p_values, FDR_ALPHA)
    for i, cv in enumerate(cell_verdicts):
        cv["fdr_rejected"] = fdr_rejected[i]
        cv["fdr_raw_p"] = p_values[i]
        if fdr_rejected[i] and cv.get("verdict") in ("GATES_AND_NULL_PASSED", "NULL_FAILED"):
            cv["verdict"] = "FDR_REJECTED"
            cv["reason"] = f"FDR rejected (BY, alpha={FDR_ALPHA})"
        elif cv.get("verdict") == "GATES_AND_NULL_PASSED" and not fdr_rejected[i]:
            cv["verdict"] = "SURVIVED_FDR"
            cv["reason"] = "Gates + null + FDR all passed"
        print(f"  {cv['cell_key']}: p={p_values[i]:.4f}, "
              f"BY_rejected={fdr_rejected[i]}, verdict={cv['verdict']}")

    # ---- Chronological holdout ----
    print("\n--- Chronological 70/30 Holdout ---")
    holdout_results = []
    for h in HORIZONS:
        train, holdout = compute_holdout(falling_oi_events, h, split_ts)
        train_net = [e[f"net_return_{h}h"] for e in train]
        holdout_net = [e[f"net_return_{h}h"] for e in holdout]
        hr = {
            "horizon": f"{h}h",
            "n_train": len(train),
            "n_holdout": len(holdout),
            "train_mean_net_bps": round(sum(train_net) / len(train_net), 4) if train_net else None,
            "holdout_mean_net_bps": round(sum(holdout_net) / len(holdout_net), 4) if holdout_net else None,
        }
        if len(holdout) < HOLDOUT_MIN_EVENTS:
            hr["holdout_verdict"] = "UNDERPOWERED_HOLDOUT_FAILURE"
        else:
            hr["holdout_verdict"] = "HOLDOUT_ADEQUATE"
        holdout_results.append(hr)
        print(f"  {h}h: train={hr['n_train']}, holdout={hr['n_holdout']}, "
              f"train_mean={hr['train_mean_net_bps']}, holdout_mean={hr['holdout_mean_net_bps']}, "
              f"verdict={hr['holdout_verdict']}")

    # Merge holdout into cell verdicts
    for cv, hr in zip(cell_verdicts, holdout_results):
        cv["holdout_n_train"] = hr["n_train"]
        cv["holdout_n_holdout"] = hr["n_holdout"]
        cv["holdout_mean_net_bps"] = hr["holdout_mean_net_bps"]
        cv["train_mean_net_bps"] = hr["train_mean_net_bps"]

    # Final verdicts
    fdr_blocked_verdict = "FDR_BLOCKED_DIAGNOSTIC"
    for cv in cell_verdicts:
        if cv["verdict"] == "SURVIVED_FDR":
            cv["final_verdict"] = "CANDIDATE_FOR_LONGER_OBSERVATION"
        elif cv["verdict"] == "FDR_REJECTED":
            cv["final_verdict"] = fdr_blocked_verdict
        elif cv["verdict"] == "NULL_FAILED":
            cv["final_verdict"] = "NULL_REJECTED_DIAGNOSTIC"
        else:
            cv["final_verdict"] = cv["verdict"]

    # ---- Study verdict ----
    survivors = [cv for cv in cell_verdicts if cv.get("final_verdict") == "CANDIDATE_FOR_LONGER_OBSERVATION"]
    null_fails = [cv for cv in cell_verdicts if cv.get("final_verdict") == "NULL_REJECTED_DIAGNOSTIC"]
    fdr_fails = [cv for cv in cell_verdicts if cv.get("final_verdict") == fdr_blocked_verdict]
    gate_fails = [cv for cv in cell_verdicts if cv.get("final_verdict") == "GATES_FAILED"]

    if survivors:
        study_verdict = "STUDY_HAS_CANDIDATES"
        study_detail = f"{len(survivors)}/2 cells are CANDIDATE_FOR_LONGER_OBSERVATION"
    elif gate_fails:
        study_verdict = "STUDY_GATES_FAILED"
        study_detail = f"{len(gate_fails)}/2 cells failed economic gates"
    elif null_fails:
        study_verdict = "STUDY_NULL_FAILED"
        study_detail = f"{len(null_fails)}/2 cells failed null test"
    elif fdr_fails:
        study_verdict = "STUDY_FDR_BLOCKED"
        study_detail = f"{len(fdr_fails)}/2 cells FDR-blocked"
    else:
        study_verdict = "STUDY_COMPLETED"
        study_detail = "No survivors but no clear failure pattern"

    print(f"\n=== Study Verdict: {study_verdict} ===")
    print(f"  {study_detail}")

    # ---- Write artifacts ----
    _write_artifacts(output_dir, run_id, metadata, cell_verdicts, null_results,
                     p_values, fdr_rejected, holdout_results, phase0_report,
                     falling_oi_events, spot_klines, study_verdict)

    # ---- Registry update ----
    _update_registry(cell_verdicts, run_id, metadata, study_verdict)

    print("\nDone. No live endpoints, auth, private keys, orders, execution,")
    print("paper trading, shadow execution, governance, or bot path was used.")

    return cell_verdicts, study_verdict


# ---------------------------------------------------------------------------
# Write artifacts
# ---------------------------------------------------------------------------

def _write_artifacts(output_dir, run_id, metadata, cell_verdicts,
                     null_results, p_values, fdr_rejected, holdout_results,
                     phase0_report, events, spot_klines, study_verdict):
    """Write all Stage B output artifacts."""

    # Summary
    summary = OrderedDict()
    summary["run_id"] = run_id
    summary["study_id"] = metadata["study_id"]
    summary["metadata"] = metadata
    summary["cell_results"] = [dict(cv) for cv in cell_verdicts]
    with open(os.path.join(output_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"  Wrote summary.json")

    # Null results
    nr_out = []
    for nr in null_results:
        nr_out.append(dict(nr))
    with open(os.path.join(output_dir, "null_results.json"), "w") as f:
        json.dump(nr_out, f, indent=2, default=str)
    print(f"  Wrote null_results.json")

    # FDR results
    fdr_out = {
        "method": "Benjamini-Yekutieli",
        "alpha": FDR_ALPHA,
        "family_size": len(HORIZONS),
        "p_values": p_values,
        "rejected": fdr_rejected,
    }
    with open(os.path.join(output_dir, "fdr_results.json"), "w") as f:
        json.dump(fdr_out, f, indent=2, default=str)
    print(f"  Wrote fdr_results.json")

    # Holdout results
    with open(os.path.join(output_dir, "holdout_results.json"), "w") as f:
        json.dump(holdout_results, f, indent=2, default=str)
    print(f"  Wrote holdout_results.json")

    # Markdown report
    lines = []
    lines.append(f"# Stage B Evaluation Report: {run_id}")
    lines.append(f"## Study: {metadata['study_id']}")
    lines.append("")
    lines.append(f"- Run: {run_id}")
    lines.append(f"- Preflight verdict: {preflight.get('preflight_verdict', 'N/A')}")
    lines.append(f"- Stage A outcome: {phase0_report.get('outcome')}")
    lines.append(f"- Seed: {SEED}")
    lines.append(f"- Safety: {metadata['safety_mode']}")
    lines.append("")
    lines.append("### Cell Results")
    lines.append("")
    lines.append("| Cell | N | Holdout | Mean Gross (bps) | Mean Net (bps) | Median Net (bps) | Win Rate | Worst Decile | Baseline Delta | Null p | FDR | Final Verdict |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for cv in cell_verdicts:
        lines.append(
            f"| {cv['cell_key']} | {cv['n_events']} | {cv.get('holdout_n_holdout', '?')} "
            f"| {cv.get('mean_gross_bps', '?')} | {cv.get('mean_net_bps', '?')} "
            f"| {cv.get('median_net_bps', '?')} | {cv.get('win_rate', '?')} "
            f"| {cv.get('worst_decile_net_bps', '?')} "
            f"| {cv.get('baseline_delta_bps', '?')} "
            f"| {cv.get('null_p_value', '?')} "
            f"| {'REJECTED' if cv.get('fdr_rejected') else 'SURVIVED'} "
            f"| {cv.get('final_verdict', '?')} |"
        )
    lines.append("")
    lines.append("### Data Sources")
    lines.append("- Funding: Binance Vision archive")
    lines.append("- OI metrics: Binance Vision archive")
    lines.append("- Spot klines: Binance Vision archive")
    lines.append("- Archive only. No authenticated endpoints.")
    lines.append("")

    with open(os.path.join(output_dir, "STAGE_B_REPORT.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Wrote STAGE_B_REPORT.md")

    # Also write to shared output dir
    shared_dir = os.path.join(OUTPUT_DIR)
    for fn in ["summary.json", "null_results.json", "fdr_results.json", "holdout_results.json"]:
        src = os.path.join(output_dir, fn)
        dst = os.path.join(shared_dir, fn.replace("summary", "stage_b_results").replace("holdout", "holdout"))
        import shutil
        shutil.copy2(src, dst)

    # Write combined results
    stage_b_results = OrderedDict()
    stage_b_results["metadata"] = metadata
    stage_b_results["cell_results"] = [dict(cv) for cv in cell_verdicts]
    stage_b_results["null_results"] = nr_out
    stage_b_results["fdr_results"] = fdr_out
    stage_b_results["holdout_results"] = holdout_results
    stage_b_results["study_verdict"] = study_verdict
    with open(os.path.join(OUTPUT_DIR, "stage_b_results.json"), "w") as f:
        json.dump(stage_b_results, f, indent=2, default=str)
    print(f"  Wrote stage_b_results.json")

preflight = None  # module-level for _write_artifacts access


# ---------------------------------------------------------------------------
# Registry update
# ---------------------------------------------------------------------------

def _update_registry(cell_verdicts, run_id, metadata, study_verdict):
    """Append falling-OI v1 results to REJECTED_RESEARCH.md."""
    if not os.path.exists(REGISTRY_PATH):
        print("WARNING: REJECTED_RESEARCH.md not found, cannot update registry.")
        return

    with open(REGISTRY_PATH) as f:
        current = f.read()

    # Build status row
    verdict_text = "REJECTED"
    detail_text = ""

    cell_summaries = []
    for cv in cell_verdicts:
        fv = cv.get("final_verdict", "?")
        if fv == "CANDIDATE_FOR_LONGER_OBSERVATION":
            verdict_text = "CANDIDATE_FOR_LONGER_OBSERVATION"
        elif fv == "GATES_FAILED":
            cell_summaries.append(f"{cv['cell_key']}: GATES_FAILED (n={cv['n_events']})")
        elif fv == "NULL_REJECTED_DIAGNOSTIC":
            cell_summaries.append(f"{cv['cell_key']}: NULL_REJECTED_DIAGNOSTIC (p={cv.get('null_p_value', '?')})")
        elif fv == "FDR_BLOCKED_DIAGNOSTIC":
            cell_summaries.append(f"{cv['cell_key']}: FDR_BLOCKED_DIAGNOSTIC (p={cv.get('null_p_value', '?')})")
        cell_summaries.append(
            f"{cv['cell_key']}: {fv}, n={cv.get('n_events', '?')}, "
            f"holdout={cv.get('holdout_n_holdout', '?')}, "
            f"mean_net={cv.get('mean_net_bps', '?')}, "
            f"wr={cv.get('win_rate', '?')}, "
            f"p={cv.get('null_p_value', '?')}, "
            f"FDR={cv.get('fdr_rejected', '?')}"
        )

    if verdict_text == "REJECTED":
        detail_text = "; ".join(cell_summaries)

    registry_entry = f"""|
| **Family 3 v1 funding × falling-OI unwind v1** | BTCUSDT negative funding extreme + falling OI → spot BTC forward returns (24h, 48h) | Binance Vision archive (funding + metrics + spot klines) | **{verdict_text}** | {detail_text}

## Family 3 v1 Funding × Falling-OI Unwind — Registry Detail

**Tag:** `family3-funding-falling-oi-unwind-v1-{'rejected' if 'REJECTED' in verdict_text else 'candidate'}`

**Verdict:** `{verdict_text}`

**Hypothesis:** When BTCUSDT funding is a negative extreme and BTCUSDT open interest is falling
over the prior 8h into the funding settlement, the short unwind continuation produces positive
BTC spot forward returns over 24h and 48h.

**Primary cells:** 2 cells (negative funding extreme + falling OI × 24h, 48h). 50 bps one-way cost.
Family size: exactly 2. BY FDR across both cells.

**Run artifacts:**
- Run: `{run_id}`
- Precommitment SHA: `{metadata['precommitment_sha256']}`
- Git SHA: `{metadata['git_sha']}`
- Seed: {SEED}
- Safety: {metadata['safety_mode']}

**Per-cell results:**

"""

    for cv in cell_verdicts:
        registry_entry += (
            f"- **{cv['cell_key']}**: Final={cv.get('final_verdict', '?')}, "
            f"N={cv.get('n_events', '?')}, "
            f"Holdout={cv.get('holdout_n_holdout', '?')}, "
            f"MeanNet={cv.get('mean_net_bps', '?')} bps, "
            f"WR={cv.get('win_rate', '?')}, "
            f"WorstDecile={cv.get('worst_decile_net_bps', '?')}, "
            f"BaselineDelta={cv.get('baseline_delta_bps', '?')}, "
            f"NullP={cv.get('null_p_value', '?')}, "
            f"FDR={'rejected' if cv.get('fdr_rejected') else 'survived'}\n"
        )

    registry_entry += (
        "\n**Lock discipline:** Lock #12 (rising-OI crowding-build mapping) is unchanged. "
        "This entry covers the falling-OI unwind mapping only.\n"
        "\n**What this rejects:**\n"
        "- BTCUSDT negative-funding + falling-OI unwind v1 under the exact frozen 2-cell design,\n"
        "  50 bps cost, Binance Vision archive, spot return leg, timestamp-shuffle null, and BY FDR\n"
        "  family size 2.\n"
        "\n**What this does NOT reject:** Multi-asset funding unwind, perp return leg with\n"
        "  funding-paid-while-held, cross-exchange OI, tick-level OI, lower-cost execution, or maker/rebate\n"
        "  models.\n"
    )

    registry_entry += "\n---\n"

    with open(REGISTRY_PATH, "a") as f:
        f.write(registry_entry)

    print(f"\n  Registry updated: {REGISTRY_PATH}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Stage B evaluator for Family 3 v1 falling-OI unwind")
    args = parser.parse_args()
    run_evaluation()


if __name__ == "__main__":
    main()
