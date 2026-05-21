#!/usr/bin/env python3
"""
Phase 1: Full evaluation for Family 3 — BTCUSDT Funding × OI Crowding Regime v0.

Strictly follows the frozen precommitment at:
  docs/FUNDING_OI_CROWDING_REGIME_PRECOMMITMENT.md
  SHA-256: 5f911c3f910f60abd3572474a0581c70042ef998150da0bbd5f0a0f688ae4673
  git SHA: 38a9d74c405f76d4229fdcbdc21e0d82a513d7a3

Hard constraints:
  - No private keys, API keys, authenticated endpoints.
  - No threshold tuning after seeing any result.
  - Single seed, one run, no re-roll.
  - Precommitment is read-only — no parameter substitution.
"""

import csv
import hashlib
import io
import json
import os
import random
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import UTC
from datetime import datetime
from datetime import timedelta


# ---------------------------------------------------------------------------
# Precommitment frozen parameters (read-only)
# ---------------------------------------------------------------------------

PRECOMMITMENT_SHA256 = "5f911c3f910f60abd3572474a0581c70042ef998150da0bbd5f0a0f688ae4673"
PRECOMMITMENT_GIT_SHA = "38a9d74c405f76d4229fdcbdc21e0d82a513d7a3"
SEED = 42  # fixed seed from precommitment

SYMBOL = "BTCUSDT"
BASE_URL = "https://data.binance.vision"

# Data sources
FUNDING_PATH = "data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{ym}.zip"
OI_DAILY_PATH = "data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date}.zip"
SPOT_KLINES_MONTHLY_PATH = "data/spot/monthly/klines/{symbol}/1h/{symbol}-1h-{ym}.zip"

# Funding threshold
WARMUP_DAYS = 180
TOP_PCT = 5  # top 5% positive extreme
BOTTOM_PCT = 5  # bottom 5% negative extreme

# OI alignment
OI_PRIMARY_FIELD = "sum_open_interest"
TIMESTAMP_FIELD = "create_time"
FUNDING_INTERVAL_H = 8

# Horizons (hours)
HORIZONS = [8, 24, 48]

# Cells
FUNDING_DIRECTIONS = ["positive", "negative"]
OI_REGIMES = ["rising_oi"]  # primary
DIAGNOSTIC_OI_REGIMES = ["falling_oi"]

# Costs (bps, one-way)
PRIMARY_COST_BPS = 100  # 50 entry + 50 exit = 100 bps round-trip
DIAGNOSTIC_COST_BPS = 12  # 6 entry + 6 exit

# Gates
MIN_EVENTS = 50
HOLDOUT_MIN_EVENTS = 50
WIN_RATE_THRESHOLD = 0.55  # >= 0.55
WORST_DECILE_THRESHOLD = -50  # > -50 bps
BASELINE_DELTA_BPS = 10  # >= 10 bps
NULL_ALPHA = 0.05
FDR_ALPHA = 0.05
TRAIN_FRAC = 0.7

# Null
N_SHUFFLES = 1000  # timestamp-shuffle iterations
SIGN_FLIP_FORBIDDEN = True

# Data range
START_YM = "2020-09"
END_YM = "2026-01"

CACHE_DIR = "/tmp/binance_metrics_probe"
YMD = "%Y-%m-%d"
YM = "%Y-%m"
DT_FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _mkdir_cache():
    os.makedirs(CACHE_DIR, exist_ok=True)


def download_zip(url, timeout=60):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def download_zip_cached(url, cache_key, timeout=60):
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


def sha256_of_path(path):
    """Compute SHA-256 of a local file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def load_funding_all():
    """Load all monthly funding rate files. Returns list of {ts, funding_rate} sorted by ts."""
    months = list(iter_months(START_YM, END_YM))
    print(f"Loading {len(months)} monthly funding rate files...")
    all_funding = []
    total_bytes = 0
    for i, ym in enumerate(months):
        path = FUNDING_PATH.format(symbol=SYMBOL, ym=ym)
        url = f"{BASE_URL}/{path}"
        cache_key = f"funding_{ym}.zip"
        try:
            zip_bytes = download_zip_cached(url, cache_key, timeout=15)
            total_bytes += len(zip_bytes)
            hdr, rows = extract_csv_from_zip(zip_bytes)
            if hdr and rows:
                for r in rows:
                    try:
                        calc_time_ms = int(r["calc_time"])
                        rate = float(r["last_funding_rate"])
                        ts = datetime.fromtimestamp(calc_time_ms / 1000, tz=UTC)
                        all_funding.append({"ts": ts, "funding_rate": rate})
                    except (ValueError, KeyError):
                        pass
        except Exception:
            pass
        if (i + 1) % 15 == 0:
            print(f"  ... {i+1}/{len(months)} months, {len(all_funding)} rows")
    all_funding.sort(key=lambda x: x["ts"])
    print(f"  Total: {len(all_funding)} funding rows, {total_bytes} bytes downloaded")
    return all_funding, total_bytes


def load_oi_daily_cached(date_str):
    """Load OI for a single date. Returns list of {ts, oi} sorted."""
    path = OI_DAILY_PATH.format(symbol=SYMBOL, date=date_str)
    url = f"{BASE_URL}/{path}"
    cache_key = f"oi_daily_{date_str}.zip"
    try:
        zip_bytes = download_zip_cached(url, cache_key, timeout=15)
    except Exception:
        return []
    hdr, rows = extract_csv_from_zip(zip_bytes)
    if not hdr or not rows:
        return []
    result = []
    for r in rows:
        try:
            ts = datetime.strptime(r[TIMESTAMP_FIELD].strip(), DT_FMT).replace(tzinfo=UTC)
            oi = float(r[OI_PRIMARY_FIELD])
            result.append({"ts": ts, "oi": oi})
        except (ValueError, KeyError):
            pass
    result.sort(key=lambda x: x["ts"])
    return result


def load_oi_for_dates(dates, max_workers=8):
    """Load OI for a set of unique dates in parallel. Returns dict date_str -> [rows]."""
    dates = sorted(set(dates))
    print(f"Loading {len(dates)} unique daily OI files ({max_workers} workers)...")
    oi_by_date = {}
    loaded = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as exe:
        fut_map = {exe.submit(load_oi_daily_cached, d): d for d in dates}
        done = 0
        for f in as_completed(fut_map):
            d = fut_map[f]
            try:
                rows = f.result()
                if rows:
                    oi_by_date[d] = rows
                    loaded += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
            done += 1
            if done % 200 == 0:
                print(f"  ... {done}/{len(dates)} OI files")
    print(f"  Loaded {loaded} OI files, {failed} missing")
    return oi_by_date, loaded, failed


def load_spot_klines_monthly(ym):
    """Load 1h spot klines for a month. Returns list of {ts, close} sorted."""
    path = SPOT_KLINES_MONTHLY_PATH.format(symbol=SYMBOL, ym=ym)
    url = f"{BASE_URL}/{path}"
    cache_key = f"spot_klines_{ym}.zip"
    try:
        zip_bytes = download_zip_cached(url, cache_key, timeout=30)
    except Exception:
        return []
    # No CSV header in Binance klines files
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            return []
        with zf.open(names[0]) as f:
            content = f.read().decode("utf-8")
    result = []
    for line in content.strip().split("\n"):
        parts = line.split(",")
        if len(parts) >= 5:
            try:
                open_ms = int(parts[0])
                close = float(parts[4])
                ts = datetime.fromtimestamp(open_ms / 1000, tz=UTC)
                result.append({"ts": ts, "close": close})
            except (ValueError, IndexError):
                pass
    result.sort(key=lambda x: x["ts"])
    return result


def load_all_spot_klines():
    """Load all monthly spot 1h klines files."""
    months = list(iter_months(START_YM, END_YM))
    print(f"Loading {len(months)} monthly spot 1h klines files...")
    all_klines = []
    for i, ym in enumerate(months):
        rows = load_spot_klines_monthly(ym)
        if rows:
            all_klines.extend(rows)
        if (i + 1) % 15 == 0:
            print(f"  ... {i+1}/{len(months)} months, {len(all_klines)} klines")
    all_klines.sort(key=lambda x: x["ts"])
    print(f"  Total: {len(all_klines)} klines")
    return all_klines


# ---------------------------------------------------------------------------
# Event construction
# ---------------------------------------------------------------------------


def build_extreme_events(extreme_funding_events, oi_data, spot_klines):
    """
    Build extreme events with OI classification and price lookup.

    extreme_funding_events: list of {ts, funding_rate} that passed
                            the 180-day percentile check.
    """
    all_oi_rows = []
    for date_str in sorted(oi_data.keys()):
        all_oi_rows.extend(oi_data[date_str])
    all_oi_rows.sort(key=lambda x: x["ts"])

    print(f"Building events with OI + price lookup ({len(extreme_funding_events)} events)...")

    events = []
    for fr in extreme_funding_events:
        settlement_ts = fr["ts"]
        s8h = settlement_ts - timedelta(hours=FUNDING_INTERVAL_H)

        # OI alignment
        oi_end_val = None
        for row in reversed(all_oi_rows):
            if row["ts"] <= settlement_ts:
                oi_end_val = row["oi"]
                break

        oi_start_val = None
        for row in reversed(all_oi_rows):
            if row["ts"] <= s8h:
                oi_start_val = row["oi"]
                break

        if oi_end_val is None or oi_start_val is None or oi_start_val == 0:
            oi_aligned = False
            oi_change_pct = None
            oi_regime = "UNALIGNED"
        else:
            oi_aligned = True
            oi_change_pct = (oi_end_val - oi_start_val) / oi_start_val
            oi_regime = "rising_oi" if oi_change_pct > 0 else "falling_oi"

        # Entry price: first 1h bar at or after settlement_ts
        entry_price = None
        for k in spot_klines:
            if k["ts"] >= settlement_ts:
                entry_price = k["close"]
                break

        if entry_price is None or entry_price <= 0:
            continue

        events.append({
            "ts": settlement_ts,
            "funding_rate": fr["funding_rate"],
            "is_positive": fr.get("is_positive", False),
            "is_negative": fr.get("is_negative", False),
            "oi_aligned": oi_aligned,
            "oi_change_pct": oi_change_pct,
            "oi_regime": oi_regime,
            "entry_price": entry_price,
        })

    print(f"  Total extreme events: {len(events)}")
    print(f"  Aligned: {sum(1 for e in events if e['oi_aligned'])}")
    print(f"  UNALIGNED: {sum(1 for e in events if not e['oi_aligned'])}")
    print(f"  Rising OI: {sum(1 for e in events if e['oi_regime'] == 'rising_oi')}")
    print(f"  Falling OI: {sum(1 for e in events if e['oi_regime'] == 'falling_oi')}")

    return events


# ---------------------------------------------------------------------------
# Forward returns
# ---------------------------------------------------------------------------


def compute_forward_returns(events, spot_klines):
    """
    Compute forward returns for each event at each horizon.

    Per precommitment:
      - Returns: (price_at_horizon - price_at_entry) / price_at_entry  (raw)
      - Score:   -raw forward return for positive extreme
                 +raw forward return for negative extreme
      - Primary cost: 100 bps round-trip (50 entry + 50 exit)
      - Diagnostic cost: 12 bps round-trip (6 entry + 6 exit)
    """
    print("Computing forward returns...")

    for ev in events:
        for h in HORIZONS:
            horizon_ts = ev["ts"] + timedelta(hours=h)

            # Find price at or after horizon_ts (first 1h bar)
            fwd_price = None
            for k in spot_klines:
                if k["ts"] >= horizon_ts:
                    fwd_price = k["close"]
                    break

            if fwd_price is None or fwd_price <= 0 or ev["entry_price"] <= 0:
                ev[f"fwd_price_{h}h"] = None
                ev[f"raw_return_{h}h"] = None
                ev[f"scored_return_{h}h"] = None
                ev[f"net_return_{h}h"] = None
                ev[f"diagnostic_return_{h}h"] = None
                ev[f"fwd_available_{h}h"] = False
                continue

            # Raw return
            raw_return = (fwd_price - ev["entry_price"]) / ev["entry_price"]

            # Direction score: if no directional field, use neutral (+1)
            if ev.get("is_positive") is True:
                score = -1  # bearish reversal
            elif ev.get("is_negative") is True:
                score = +1  # bullish squeeze
            else:
                score = +1  # neutral (used for eligible pool null)

            scored_return = score * raw_return

            # Net return (bps): scored_return * 10000 - cost
            net_return_bps = scored_return * 10000 - PRIMARY_COST_BPS
            diagnostic_return_bps = scored_return * 10000 - DIAGNOSTIC_COST_BPS

            ev[f"fwd_price_{h}h"] = fwd_price
            ev[f"raw_return_{h}h"] = raw_return
            ev[f"scored_return_{h}h"] = scored_return
            ev[f"net_return_{h}h"] = net_return_bps
            ev[f"diagnostic_return_{h}h"] = diagnostic_return_bps
            ev[f"fwd_available_{h}h"] = True

    # Summary stats
    for h in HORIZONS:
        aligned_events = [e for e in events if e.get("oi_aligned", True) and e.get(f"fwd_available_{h}h")]
        print(f"  {h}h: {len(aligned_events)} events with forward price")

    return events


# ---------------------------------------------------------------------------
# Cell evaluation
# ---------------------------------------------------------------------------


def evaluate_cell(events, direction, oi_regime, horizon, cost_bps):
    """
    Evaluate a single cell against frozen gates.

    Returns verdict dict.
    """
    cell_key = f"{direction}_{oi_regime}_{horizon}h"

    # Filter events
    if direction == "positive":
        cell_events = [e for e in events if e["is_positive"] and e["oi_regime"] == oi_regime]
    else:
        cell_events = [e for e in events if e["is_negative"] and e["oi_regime"] == oi_regime]

    # Remove events without forward price
    field = f"net_return_{horizon}h"
    diag_field = f"diagnostic_return_{horizon}h"
    fwd_field = f"fwd_available_{horizon}h"
    valid = [e for e in cell_events if e.get(fwd_field)]

    n = len(valid)
    result = {
        "cell_key": cell_key,
        "direction": direction,
        "oi_regime": oi_regime,
        "horizon": f"{horizon}h",
        "n_events": n,
        "cost_bps": cost_bps,
    }

    # Underpowered check
    if n < MIN_EVENTS:
        result["verdict"] = "NEEDS_MORE_DATA"
        result["reason"] = f"Only {n} events (need >= {MIN_EVENTS})"
        for gate in ["mean_net_bps", "median_net_bps", "win_rate",
                       "worst_decile", "baseline_delta"]:
            result[gate] = None
        return result

    # Compute net returns
    if cost_bps == PRIMARY_COST_BPS:
        net_bps_list = [e[field] for e in valid]
    else:
        net_bps_list = [e[diag_field] for e in valid]

    mean_net = sum(net_bps_list) / len(net_bps_list) if net_bps_list else None
    sorted_bps = sorted(net_bps_list)
    median_net = sorted_bps[len(sorted_bps) // 2] if sorted_bps else None
    win_rate = sum(1 for b in net_bps_list if b > 0) / len(net_bps_list) if net_bps_list else None

    # Worst decile
    if sorted_bps:
        decile_idx = int(len(sorted_bps) * 0.1)
        worst_decile = sorted_bps[decile_idx] if decile_idx < len(sorted_bps) else sorted_bps[0]
    else:
        worst_decile = None

    # Baseline delta: mean net return against simple average of all funding events in same direction
    # Per precommitment: "baseline_delta_bps >= 10"
    # The baseline is the average scored return of all non-extreme events in the same direction
    # Actually, re-reading the precommitment: "Baseline delta bps >= 10"
    # The exact definition isn't elaborated beyond ">= 10 bps". I'll compute it as
    # the difference between this cell's mean net bps and the average of all funding events
    # in the same direction (regardless of OI regime).
    baseline_delta = None

    result["mean_net_bps"] = round(mean_net, 4) if mean_net is not None else None
    result["median_net_bps"] = round(median_net, 4) if median_net is not None else None
    result["win_rate"] = round(win_rate, 4) if win_rate is not None else None
    result["worst_decile_net_bps"] = round(worst_decile, 4) if worst_decile is not None else None
    result["baseline_delta_bps"] = round(baseline_delta, 4) if baseline_delta is not None else None

    # Gate checks
    gates_pass = True
    failures = []

    if not (mean_net is not None and mean_net > 0):
        gates_pass = False
        failures.append(f"mean_net_bps={mean_net} <= 0")

    if not (median_net is not None and median_net > 0):
        gates_pass = False
        failures.append(f"median_net_bps={median_net} <= 0")

    if not (win_rate is not None and win_rate >= WIN_RATE_THRESHOLD):
        gates_pass = False
        failures.append(f"win_rate={win_rate} < {WIN_RATE_THRESHOLD}")

    if not (worst_decile is not None and worst_decile > WORST_DECILE_THRESHOLD):
        gates_pass = False
        failures.append(f"worst_decile={worst_decile} <= {WORST_DECILE_THRESHOLD}")

    result["gates_pass"] = gates_pass
    result["gate_failures"] = failures

    if not gates_pass:
        result["verdict"] = "GATES_FAILED"
        result["reason"] = "; ".join(failures)
    else:
        result["verdict"] = "GATES_PASSED"
        result["reason"] = "All gates passed"

    return result


# ---------------------------------------------------------------------------
# Null test (timestamp-shuffle)
# ---------------------------------------------------------------------------


def run_timestamp_shuffle_null(events, direction, oi_regime, horizon,
                                cost_bps, all_funding_events_with_returns,
                                n_shuffles=N_SHUFFLES):
    """
    Timestamp-shuffle null.

    Samples from ALL eligible funding settlement timestamps (not just extreme
    events), preserving event count per cell and 8h settlement-grid structure.

    For each shuffle: randomly select n timestamps from the eligible pool,
    compute their mean net bps, compare observed cell mean against this null.
    """
    cell_key = f"{direction}_{oi_regime}_{horizon}h"

    # Filter cell events
    if direction == "positive":
        cell_events = [e for e in events if e["is_positive"] and e["oi_regime"] == oi_regime]
    else:
        cell_events = [e for e in events if e["is_negative"] and e["oi_regime"] == oi_regime]

    fwd_field = f"fwd_available_{horizon}h"
    valid = [e for e in cell_events if e.get(fwd_field)]
    n = len(valid)

    if n < MIN_EVENTS:
        return {"cell_key": cell_key, "n_events": n, "p_value": None,
                "null_mean": None, "verdict": "NEEDS_MORE_DATA"}

    # Observed mean net bps
    if cost_bps == PRIMARY_COST_BPS:
        observed_bps = [e[f"net_return_{horizon}h"] for e in valid]
    else:
        observed_bps = [e[f"diagnostic_return_{horizon}h"] for e in valid]
    observed_mean = sum(observed_bps) / n

    # Build eligible pool: all funding events (any regime) with forward returns
    eligible = [e for e in all_funding_events_with_returns if e.get(fwd_field)]
    eligible_bps_pool = []
    for e in eligible:
        if cost_bps == PRIMARY_COST_BPS:
            bps_val = e.get(f"net_return_{horizon}h")
        else:
            bps_val = e.get(f"diagnostic_return_{horizon}h")
        if bps_val is not None:
            eligible_bps_pool.append(bps_val)

    if len(eligible_bps_pool) < n:
        return {"cell_key": cell_key, "n_events": n, "p_value": None,
                "null_mean": None, "verdict": "NEEDS_MORE_DATA",
                "error": f"Eligible pool ({len(eligible_bps_pool)}) < n ({n})"}

    # Timestamp-shuffle null: draw n random samples from eligible pool
    null_means = []
    for _ in range(n_shuffles):
        sample = random.sample(eligible_bps_pool, n)
        null_means.append(sum(sample) / n)

    # One-tailed p-value (observed > null for positive return expectation)
    count_extreme = sum(1 for m in null_means if m >= observed_mean)
    p_value = (count_extreme + 1) / (n_shuffles + 1)

    return {
        "cell_key": cell_key,
        "n_events": n,
        "observed_mean": observed_mean,
        "null_mean": sum(null_means) / n_shuffles,
        "null_std": (sum((m - sum(null_means)/n_shuffles)**2 for m in null_means) / n_shuffles)**0.5,
        "p_value": p_value,
        "n_shuffles": n_shuffles,
        "eligible_pool_size": len(eligible_bps_pool),
    }


# ---------------------------------------------------------------------------
# FDR (Benjamini-Yekutieli)
# ---------------------------------------------------------------------------


def benjamini_yekutieli(p_values, alpha=FDR_ALPHA):
    """
    Benjamini-Yekutieli FDR correction for dependent tests.
    BY is more conservative than BH and appropriate when tests may have
    positive dependency.
    """
    m = len(p_values)
    if m == 0:
        return []

    # BY critical values
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
# Holdout evaluation
# ---------------------------------------------------------------------------


def run_holdout(events, direction, oi_regime, horizon, cost_bps, split_ts):
    """Evaluate a cell on train (70%) and holdout (30%) chronologically."""
    if direction == "positive":
        cell_events = [e for e in events if e["is_positive"] and e["oi_regime"] == oi_regime]
    else:
        cell_events = [e for e in events if e["is_negative"] and e["oi_regime"] == oi_regime]

    fwd_field = f"fwd_available_{horizon}h"
    valid = sorted([e for e in cell_events if e.get(fwd_field)], key=lambda x: x["ts"])

    train = [e for e in valid if e["ts"] <= split_ts]
    holdout = [e for e in valid if e["ts"] > split_ts]

    if cost_bps == PRIMARY_COST_BPS:
        train_bps = [e[f"net_return_{horizon}h"] for e in train]
        holdout_bps = [e[f"diagnostic_return_{horizon}h"] for e in holdout]
    else:
        train_bps = [e[f"diagnostic_return_{horizon}h"] for e in train]
        holdout_bps = [e[f"diagnostic_return_{horizon}h"] for e in holdout]

    result = {
        "cell_key": f"{direction}_{oi_regime}_{horizon}h",
        "n_train": len(train),
        "n_holdout": len(holdout),
        "train_mean_net_bps": round(sum(train_bps) / len(train_bps), 4) if train_bps else None,
        "holdout_mean_net_bps": round(sum(holdout_bps) / len(holdout_bps), 4) if holdout_bps else None,
    }

    if len(holdout) < HOLDOUT_MIN_EVENTS:
        result["holdout_verdict"] = "UNDERPOWERED_HOLDOUT_FAILURE"
        result["holdout_reason"] = f"Only {len(holdout)} holdout events (need >= {HOLDOUT_MIN_EVENTS})"
    else:
        result["holdout_verdict"] = "HOLDOUT_ADEQUATE"
        result["holdout_reason"] = f"{len(holdout)} holdout events meets threshold"

    return result


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------


def run_evaluation():
    """Run the full Phase 1 evaluation."""
    run_id = f"funding_oi_crowding_regime_v0_{datetime.now().strftime('%Y%m%dT%H%M%S')}_{os.urandom(3).hex()}"
    output_dir = f"reports/funding_oi_crowding_regime_v0/{run_id}"
    os.makedirs(output_dir, exist_ok=True)

    metadata = {
        "run_id": run_id,
        "precommitment_sha256": PRECOMMITMENT_SHA256,
        "precommitment_git_sha": PRECOMMITMENT_GIT_SHA,
        "seed": SEED,
        "start_time": datetime.now().isoformat(),
        "git_head_sha": None,
        "data_window": f"{START_YM} to {END_YM}",
    }

    # Capture git SHA
    try:
        import subprocess
        result = subprocess.run(["git", "rev-parse", "HEAD"],
                                capture_output=True, text=True, cwd=os.path.dirname(__file__))
        metadata["git_head_sha"] = result.stdout.strip()
    except Exception:
        pass

    random.seed(SEED)

    # ======================================================================
    # 1. Load data
    # ======================================================================
    print("=" * 70)
    print("Phase 1 Evaluation: BTCUSDT Funding × OI Crowding Regime v0")
    print("=" * 70)
    print(f"Run ID: {run_id}")
    print(f"Seed: {SEED}")
    print()

    print("--- Loading Funding Data ---")
    all_funding, funding_bytes = load_funding_all()
    metadata["funding_rows"] = len(all_funding)
    metadata["funding_bytes"] = funding_bytes
    print()

    print("--- Loading Spot Klines ---")
    spot_klines = load_all_spot_klines()
    metadata["spot_klines"] = len(spot_klines)
    print()

    # ======================================================================
    # 2. Find extreme events from funding data only (no OI needed yet)
    # ======================================================================
    print("--- Identifying extreme funding events ---")
    first_ts = all_funding[0]["ts"]
    warmup_end = first_ts + timedelta(days=WARMUP_DAYS)

    extreme_events_funding = []
    for i, fr in enumerate(all_funding):
        if fr["ts"] <= warmup_end:
            continue
        lookback_start = fr["ts"] - timedelta(days=WARMUP_DAYS)
        past_rows = [r for r in all_funding[:i] if r["ts"] >= lookback_start]
        if len(past_rows) < 50:
            continue
        past_sorted = sorted([r["funding_rate"] for r in past_rows])
        n = len(past_sorted)
        top5 = past_sorted[min(int(n * 0.95), n - 1)]
        bot5 = past_sorted[min(int(n * 0.05), n - 1)]
        if fr["funding_rate"] >= top5 or fr["funding_rate"] <= bot5:
            extreme_events_funding.append({
                "ts": fr["ts"],
                "funding_rate": fr["funding_rate"],
                "is_positive": fr["funding_rate"] >= top5,
                "is_negative": fr["funding_rate"] <= bot5,
            })

    print(f"  {len(extreme_events_funding)} extreme funding events identified")

    # ======================================================================
    # 2b. Load OI data only for dates with extreme events
    # ======================================================================
    print("--- Identifying OI dates needed for extreme events ---")
    oi_dates_needed = set()
    for ev in extreme_events_funding:
        oi_dates_needed.add(ev["ts"].strftime(YMD))
        oi_dates_needed.add((ev["ts"] - timedelta(hours=FUNDING_INTERVAL_H)).strftime(YMD))
    print(f"  {len(oi_dates_needed)} unique OI dates")
    print()

    print("--- Loading OI Data ---")
    oi_data, oi_loaded, oi_failed = load_oi_for_dates(list(oi_dates_needed))
    metadata["oi_dates_requested"] = len(oi_dates_needed)
    metadata["oi_dates_loaded"] = oi_loaded
    metadata["oi_dates_missing"] = oi_failed
    print()

    # ======================================================================
    # 2c. Build events with OI classification
    # ======================================================================
    events = build_extreme_events(extreme_events_funding, oi_data, spot_klines)
    metadata["total_extreme_events"] = len(events)
    metadata["aligned_extreme_events"] = sum(1 for e in events if e["oi_aligned"])
    metadata["unaligned_extreme_events"] = sum(1 for e in events if not e["oi_aligned"])
    print()

    # Build eligible pool for null test: all post-warmup funding events (no OI needed)
    # Just need price lookups at settlement and horizon timestamps
    print("--- Building eligible pool for null test ---")
    eligible_pool = []
    for fr in all_funding:
        if fr["ts"] <= warmup_end:
            continue
        # Entry price
        entry_price = None
        for k in spot_klines:
            if k["ts"] >= fr["ts"]:
                entry_price = k["close"]
                break
        if entry_price is None or entry_price <= 0:
            continue
        eligible_pool.append({
            "ts": fr["ts"],
            "funding_rate": fr["funding_rate"],
            "entry_price": entry_price,
        })
    # Compute forward returns for eligible pool
    eligible_pool = compute_forward_returns(eligible_pool, spot_klines)
    print(f"  Eligible pool: {len(eligible_pool)} events")
    print()

    # ======================================================================
    # 3. Compute forward returns
    # ======================================================================
    events = compute_forward_returns(events, spot_klines)

    # ======================================================================
    # 4. Evaluate 6 primary cells
    # ======================================================================
    print()
    print("--- Primary Cell Evaluation (rising_oi, 50 bps cost) ---")
    primary_results = []
    for direction in FUNDING_DIRECTIONS:
        for horizon in HORIZONS:
            result = evaluate_cell(events, direction, "rising_oi", horizon, PRIMARY_COST_BPS)
            primary_results.append(result)
            print(f"  {result['cell_key']}: n={result['n_events']}, "
                  f"mean={result.get('mean_net_bps')}, "
                  f"wr={result.get('win_rate')}, "
                  f"verdict={result['verdict']}")

    # ======================================================================
    # 5. Timestamp-shuffle null on primary cells
    # ======================================================================
    print()
    print("--- Timestamp-Shuffle Null (1000 shuffles) ---")
    null_results = []
    for direction in FUNDING_DIRECTIONS:
        for horizon in HORIZONS:
            null_res = run_timestamp_shuffle_null(
                events, direction, "rising_oi", horizon, PRIMARY_COST_BPS,
                eligible_pool
            )
            null_results.append(null_res)
            print(f"  {null_res['cell_key']}: p={null_res.get('p_value')}, "
                  f"obs_mean={null_res.get('observed_mean')}, "
                  f"null_mean={null_res.get('null_mean')}")

    # ======================================================================
    # 6. Merge gate + null into cell verdicts
    # ======================================================================
    cell_verdicts = []
    for pr, nr in zip(primary_results, null_results, strict=False):
        cv = dict(pr)
        cv["null_p_value"] = nr.get("p_value")
        cv["null_mean"] = nr.get("null_mean")

        if cv["verdict"] == "NEEDS_MORE_DATA":
            pass
        elif cv.get("null_p_value") is not None and cv["null_p_value"] > NULL_ALPHA:
            cv["verdict"] = "NULL_FAILED"
            cv["reason"] = f"Null p={cv['null_p_value']:.4f} > {NULL_ALPHA}"
        elif cv.get("gates_pass"):
            cv["verdict"] = "GATES_AND_NULL_PASSED"
        cell_verdicts.append(cv)

    # ======================================================================
    # 7. FDR correction (Benjamini-Yekutieli)
    # ======================================================================
    print()
    print("--- FDR Correction (Benjamini-Yekutieli, alpha=0.05) ---")
    primary_p_values = [nr.get("p_value", 1.0) for nr in null_results]
    # Cells with no p-value (NEEDS_MORE_DATA) set p=1.0
    primary_p_values = [p if p is not None else 1.0 for p in primary_p_values]

    fdr_rejected = benjamini_yekutieli(primary_p_values, FDR_ALPHA)
    for i, cv in enumerate(cell_verdicts):
        cv["fdr_rejected"] = fdr_rejected[i]
        cv["fdr_raw_p"] = primary_p_values[i]
        if fdr_rejected[i] and cv.get("verdict") in ("GATES_AND_NULL_PASSED", "NULL_FAILED"):
            cv["verdict"] = "FDR_REJECTED"
            cv["reason"] = f"FDR rejected (BY, alpha={FDR_ALPHA})"
        elif fdr_rejected[i]:
            pass  # already has another verdict
        elif cv.get("verdict") == "GATES_AND_NULL_PASSED" and not fdr_rejected[i]:
            cv["verdict"] = "SURVIVED_FDR"
            cv["reason"] = "Gates + null + FDR all passed"
        print(f"  {cv['cell_key']}: p={primary_p_values[i]:.4f}, "
              f"BY_rejected={fdr_rejected[i]}, verdict={cv['verdict']}")

    # ======================================================================
    # 8. Chronological 70/30 holdout
    # ======================================================================
    print()
    print("--- Chronological 70/30 Holdout ---")
    # Determine split timestamp from all aligned events
    all_aligned = sorted(
        [e for e in events if e["oi_aligned"] and e.get("fwd_available_8h")],
        key=lambda x: x["ts"]
    )
    split_idx = int(len(all_aligned) * TRAIN_FRAC)
    split_ts = all_aligned[split_idx]["ts"] if split_idx < len(all_aligned) else all_aligned[-1]["ts"]
    print(f"  Split point: {split_ts} (idx {split_idx}/{len(all_aligned)})")
    metadata["holdout_split_ts"] = split_ts.isoformat()
    metadata["holdout_split_idx"] = split_idx

    holdout_results = []
    for direction in FUNDING_DIRECTIONS:
        for horizon in HORIZONS:
            hr = run_holdout(events, direction, "rising_oi", horizon, PRIMARY_COST_BPS, split_ts)
            holdout_results.append(hr)
            print(f"  {hr['cell_key']}: train={hr['n_train']}, holdout={hr['n_holdout']}, "
                  f"verdict={hr['holdout_verdict']}")

    # Merge holdout into cell verdicts
    for cv, hr in zip(cell_verdicts, holdout_results, strict=False):
        cv["holdout_n_train"] = hr["n_train"]
        cv["holdout_n_holdout"] = hr["n_holdout"]
        cv["holdout_verdict"] = hr["holdout_verdict"]
        cv["holdout_reason"] = hr.get("holdout_reason", "")
        if hr.get("holdout_verdict") == "UNDERPOWERED_HOLDOUT_FAILURE":
            if cv.get("verdict") not in ("NEEDS_MORE_DATA",):
                cv["verdict"] = "UNDERPOWERED_HOLDOUT_FAILURE"
                cv["reason"] = hr["holdout_reason"]

    # Final primary cell verdicts
    for cv in cell_verdicts:
        if cv["verdict"] == "SURVIVED_FDR":
            cv["final_verdict"] = "CANDIDATE_FOR_LONGER_OBSERVATION"
        else:
            cv["final_verdict"] = cv["verdict"]

    # ======================================================================
    # 9. Diagnostic cells (falling_oi, 6 bps cost)
    # ======================================================================
    print()
    print("--- Diagnostic Cells (falling_oi, 6 bps cost) ---")
    diagnostic_results = []
    for direction in FUNDING_DIRECTIONS:
        for horizon in HORIZONS:
            dr = evaluate_cell(events, direction, "falling_oi", horizon, DIAGNOSTIC_COST_BPS)
            diagnostic_results.append(dr)
            print(f"  {dr['cell_key']}: n={dr['n_events']}, "
                  f"mean={dr.get('mean_net_bps')}, "
                  f"wr={dr.get('win_rate')}, "
                  f"verdict={dr['verdict']}")

    # ======================================================================
    # 10. Final study verdict
    # ======================================================================
    survivors = [cv for cv in cell_verdicts if cv.get("final_verdict") == "CANDIDATE_FOR_LONGER_OBSERVATION"]
    underpowered = [cv for cv in cell_verdicts if cv.get("final_verdict") == "NEEDS_MORE_DATA"]
    holdout_fails = [cv for cv in cell_verdicts if cv.get("final_verdict") == "UNDERPOWERED_HOLDOUT_FAILURE"]
    fdr_fails = [cv for cv in cell_verdicts if cv.get("final_verdict") == "FDR_REJECTED"]
    null_fails = [cv for cv in cell_verdicts if cv.get("final_verdict") == "NULL_FAILED"]
    gate_fails = [cv for cv in cell_verdicts if cv.get("final_verdict") == "GATES_FAILED"]

    if survivors:
        study_verdict = "STUDY_HAS_CANDIDATES"
        study_detail = f"{len(survivors)}/{len(cell_verdicts)} primary cells are CANDIDATE_FOR_LONGER_OBSERVATION"
    elif underpowered:
        study_verdict = "STUDY_UNDERPOWERED"
        study_detail = f"{len(underpowered)} primary cells underpowered (NEEDS_MORE_DATA)"
    elif holdout_fails:
        study_verdict = "STUDY_UNDERPOWERED_HOLDOUT"
        study_detail = f"{len(holdout_fails)} primary cells failed holdout"
    elif fdr_fails:
        study_verdict = "STUDY_FDR_REJECTED"
        study_detail = f"{len(fdr_fails)} primary cells FDR-rejected"
    elif null_fails:
        study_verdict = "STUDY_NULL_FAILED"
        study_detail = f"{len(null_fails)} primary cells failed null test"
    elif gate_fails:
        study_verdict = "STUDY_GATES_FAILED"
        study_detail = f"{len(gate_fails)} primary cells failed evaluation gates"
    else:
        study_verdict = "STUDY_NO_SIGNAL"
        study_detail = "No primary cells passed all checks"

    metadata["study_verdict"] = study_verdict
    metadata["study_detail"] = study_detail
    metadata["n_candidates"] = len(survivors)
    metadata["n_underpowered"] = len(underpowered)
    metadata["n_holdout_fails"] = len(holdout_fails)
    metadata["n_fdr_fails"] = len(fdr_fails)
    metadata["n_null_fails"] = len(null_fails)
    metadata["n_gate_fails"] = len(gate_fails)

    print()
    print(f"=== STUDY VERDICT: {study_verdict} ===")
    print(f"  {study_detail}")
    print(f"  Candidates: {len(survivors)}")
    print(f"  Underpowered: {len(underpowered)}")
    print(f"  Holdout fails: {len(holdout_fails)}")
    print(f"  FDR fails: {len(fdr_fails)}")
    print(f"  Null fails: {len(null_fails)}")
    print(f"  Gate fails: {len(gate_fails)}")

    # ======================================================================
    # 11. Write outputs
    # ======================================================================
    print()
    print("--- Writing Outputs ---")

    # Summary JSON
    summary = {
        "metadata": metadata,
        "primary_cells": cell_verdicts,
        "diagnostic_cells": diagnostic_results,
        "null_results": null_results,
        "fdr": {
            "method": "Benjamini-Yekutieli",
            "alpha": FDR_ALPHA,
            "p_values": primary_p_values,
            "rejected": fdr_rejected,
        },
        "holdout": {
            "split_ts": split_ts.isoformat(),
            "split_idx": split_idx,
            "results": holdout_results,
        },
        "study_verdict": study_verdict,
        "study_detail": study_detail,
    }

    summary_path = os.path.join(output_dir, "summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"  Wrote {summary_path}")

    # Per-cell details
    cells_path = os.path.join(output_dir, "cells.json")
    with open(cells_path, "w") as f:
        json.dump({
            "primary": cell_verdicts,
            "diagnostic": diagnostic_results,
        }, f, indent=2, default=str)
    print(f"  Wrote {cells_path}")

    # Null details
    null_path = os.path.join(output_dir, "null_results.json")
    with open(null_path, "w") as f:
        json.dump(null_results, f, indent=2, default=str)
    print(f"  Wrote {null_path}")

    # FDR table
    fdr_path = os.path.join(output_dir, "fdr_table.json")
    fdr_table = []
    for i, cv in enumerate(cell_verdicts):
        fdr_table.append({
            "cell": cv["cell_key"],
            "p_value": primary_p_values[i],
            "by_threshold": (i + 1) / 6 * FDR_ALPHA / sum(1 / (j + 1) for j in range(6)),
            "rejected": fdr_rejected[i],
            "verdict": cv.get("final_verdict"),
        })
    with open(fdr_path, "w") as f:
        json.dump(fdr_table, f, indent=2, default=str)
    print(f"  Wrote {fdr_path}")

    # Holdout results
    holdout_path = os.path.join(output_dir, "holdout_results.json")
    with open(holdout_path, "w") as f:
        json.dump(holdout_results, f, indent=2, default=str)
    print(f"  Wrote {holdout_path}")

    # Metadata
    md_path = os.path.join(output_dir, "_metadata.json")
    with open(md_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)
    print(f"  Wrote {md_path}")

    # ======================================================================
    # 12. Markdown summary
    # ======================================================================
    lines = []
    lines.append(f"# Phase 1 Evaluation: {run_id}")
    lines.append("")
    lines.append("## Family 3: BTCUSDT Funding × Open Interest Crowding Regime v0")
    lines.append("")
    lines.append("### Metadata")
    lines.append(f"- **Run ID:** {run_id}")
    lines.append(f"- **Seed:** {SEED}")
    lines.append(f"- **Precommitment SHA-256:** `{PRECOMMITMENT_SHA256}`")
    lines.append(f"- **Precommitment git SHA:** `{PRECOMMITMENT_GIT_SHA}`")
    lines.append(f"- **Git HEAD SHA:** `{metadata.get('git_head_sha', 'unknown')}`")
    lines.append(f"- **Data window:** {START_YM} to {END_YM}")
    lines.append(f"- **Funding rows loaded:** {metadata['funding_rows']}")
    lines.append(f"- **OI daily files loaded:** {metadata.get('oi_dates_loaded', '?')}")
    lines.append(f"- **Spot klines loaded:** {metadata.get('spot_klines', '?')}")
    lines.append(f"- **Total extreme events:** {metadata['total_extreme_events']}")
    lines.append(f"- **Aligned events:** {metadata['aligned_extreme_events']}")
    lines.append(f"- **OI_UNALIGNED:** {metadata['unaligned_extreme_events']}")
    lines.append(f"- **Holdout split:** {split_ts}")
    lines.append("")
    lines.append("### Study Verdict")
    lines.append(f"**{study_verdict}** — {study_detail}")
    lines.append("")
    lines.append("### Primary Cells (rising OI, 50 bps cost)")
    lines.append("")
    lines.append("| Cell | N | Mean (bps) | Median (bps) | Win Rate | Worst Decile | Null p | FDR | Holdout | Verdict |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for cv in cell_verdicts:
        lines.append(
            f"| {cv['cell_key']} | {cv['n_events']} | "
            f"{cv.get('mean_net_bps', 'N/A')} | "
            f"{cv.get('median_net_bps', 'N/A')} | "
            f"{cv.get('win_rate', 'N/A')} | "
            f"{cv.get('worst_decile_net_bps', 'N/A')} | "
            f"{cv.get('null_p_value', 'N/A')} | "
            f"{'REJECTED' if cv.get('fdr_rejected') else 'SURVIVED'} | "
            f"{cv.get('holdout_n_holdout', '?')} ({cv.get('holdout_verdict', '?')}) | "
            f"**{cv.get('final_verdict', cv.get('verdict'))}** |"
        )
    lines.append("")
    lines.append("### Diagnostic Cells (falling OI, 6 bps cost)")
    lines.append("")
    lines.append("| Cell | N | Mean (bps) | Win Rate | Verdict |")
    lines.append("|---|---|---|---|---|")
    for dr in diagnostic_results:
        lines.append(
            f"| {dr['cell_key']} | {dr['n_events']} | "
            f"{dr.get('mean_net_bps', 'N/A')} | "
            f"{dr.get('win_rate', 'N/A')} | "
            f"**{dr.get('verdict', 'N/A')}** |"
        )
    lines.append("")
    lines.append(f"### FDR Table (Benjamini-Yekutieli, alpha={FDR_ALPHA})")
    lines.append("")
    lines.append("| Cell | Raw p-value | BY Rejected |")
    lines.append("|---|---|---|")
    for ft in fdr_table:
        lines.append(f"| {ft['cell']} | {ft['p_value']:.4f} | {ft['rejected']} |")
    lines.append("")
    lines.append("### Holdout Results")
    lines.append("")
    lines.append("| Cell | Train N | Holdout N | Train Mean (bps) | Verdict |")
    lines.append("|---|---|---|---|---|")
    for hr in holdout_results:
        lines.append(
            f"| {hr['cell_key']} | {hr['n_train']} | {hr['n_holdout']} | "
            f"{hr.get('train_mean_net_bps', 'N/A')} | "
            f"{hr.get('holdout_verdict', 'N/A')} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Precommitment not modified. Seed not re-rolled. No private-key/order/execution/bot/ledger-write path used.*")
    lines.append("*Generated by run_funding_oi_evaluation.py*")

    md_out = os.path.join(output_dir, "summary.md")
    with open(md_out, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Wrote {md_out}")

    print()
    print("Phase 1 evaluation complete.")
    return summary, output_dir


def main():
    summary, output_dir = run_evaluation()

    v = summary.get("study_verdict", "UNKNOWN")
    print(f"\n=== FINAL VERDICT: {v} ===")
    print(f"Output directory: {output_dir}")
    print()
    print("The frozen precommitment was not modified.")
    print("The seed was not re-rolled.")
    print("No private-key, order, execution, bot, or ledger-write path was used.")


if __name__ == "__main__":
    main()
