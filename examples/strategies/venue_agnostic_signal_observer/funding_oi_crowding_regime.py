#!/usr/bin/env python3
"""
Phase 0B: Blind population sizing (counts-only) for Family 3.

Run after Phase 0A passes. Uses funding archive + OI metrics to estimate
population sizes for the funding × OI crowding regime study.

Applies rolling 180-day past-only percentile membership:
  - For each 8h funding settlement, the 180-day window looks backward
  - Top 5% and bottom 5% determine extreme events
  - OI regime classified from daily metrics at or before settlement timestamps

Uses ThreadPoolExecutor for concurrent HTTP downloads.

No threshold values, event timestamps, or returns are output.
"""

import csv
import io
import json
import os
import urllib.request
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://data.binance.vision"
SYMBOL = "BTCUSDT"
WARMUP_DAYS = 180
MIN_EVENTS = 50

YMD = "%Y-%m-%d"
YM = "%Y-%m"
DT_FMT = "%Y-%m-%d %H:%M:%S"
OI_PRIMARY_FIELD = "sum_open_interest"
TIMESTAMP_FIELD = "create_time"
FUNDING_INTERVAL_HOURS = 8

# Full monthly range
START_YM = "2020-09"
END_YM = "2026-01"

FUNDING_MONTHLY_PATH = (
    "data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{ym}.zip"
)
DAILY_OI_PATH = (
    "data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date}.zip"
)

CACHE_DIR = "/tmp/binance_metrics_probe"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def download_zip(url, timeout=30):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def download_zip_cached(url, cache_key, timeout=30):
    """Download with local file cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
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


def parse_ts(ts_str):
    return datetime.strptime(ts_str.strip(), DT_FMT).replace(tzinfo=timezone.utc)


def load_funding_monthly(ym):
    path = FUNDING_MONTHLY_PATH.format(symbol=SYMBOL, ym=ym)
    url = f"{BASE_URL}/{path}"
    cache_key = f"funding_{ym}.zip"
    try:
        zip_bytes = download_zip_cached(url, cache_key, timeout=15)
    except Exception:
        return []
    header, rows = extract_csv_from_zip(zip_bytes)
    if not header or not rows:
        return []
    result = []
    for r in rows:
        try:
            calc_time_ms = int(r["calc_time"])
            funding_rate = float(r["last_funding_rate"])
            ts = datetime.fromtimestamp(calc_time_ms / 1000, tz=timezone.utc)
            result.append({"ts": ts, "funding_rate": funding_rate})
        except (ValueError, KeyError):
            continue
    return result


def load_oi_daily_cached(date_str):
    """Load OI from cache or HTTP. Returns list of {ts, oi}."""
    path = DAILY_OI_PATH.format(symbol=SYMBOL, date=date_str)
    url = f"{BASE_URL}/{path}"
    cache_key = f"oi_daily_{date_str}.zip"
    try:
        zip_bytes = download_zip_cached(url, cache_key, timeout=15)
    except Exception:
        return []
    header, rows = extract_csv_from_zip(zip_bytes)
    if not header or not rows:
        return []
    result = []
    for r in rows:
        try:
            ts = parse_ts(r[TIMESTAMP_FIELD])
            oi = float(r[OI_PRIMARY_FIELD])
            result.append({"ts": ts, "oi": oi})
        except (ValueError, KeyError):
            continue
    return result


def iter_months(start_ym, end_ym):
    start = datetime.strptime(start_ym, YM)
    end = datetime.strptime(end_ym, YM)
    current = start
    while current <= end:
        yield current.strftime(YM)
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_phase0b(output_dir="reports/funding_oi_crowding_regime_v0"):
    results = OrderedDict()
    results["phase"] = "0B"
    results["population_sizing_rule"] = (
        "Rolling 180-day past-only percentile membership. "
        "Each 8h funding settlement classified using trailing 180 days. "
        "Top 5% = positive extreme, bottom 5% = negative extreme. "
        "OI regime from Binance Vision daily metrics (sum_open_interest). "
        "No threshold values, timestamps, or returns in output."
    )
    results["funding_coverage"] = f"{START_YM} to {END_YM}"

    # ------------------------------------------------------------------
    # 1. Load ALL monthly funding data
    # ------------------------------------------------------------------
    months = list(iter_months(START_YM, END_YM))
    print(f"Loading {len(months)} monthly funding rate files {START_YM} to {END_YM}...")

    all_funding = []
    for i, ym in enumerate(months):
        rows = load_funding_monthly(ym)
        if rows:
            all_funding.extend(rows)
        if (i + 1) % 15 == 0:
            print(f"  ... {i+1}/{len(months)} months, {len(all_funding)} rows")

    all_funding.sort(key=lambda x: x["ts"])
    results["funding_rows_loaded"] = len(all_funding)
    print(f"  Total: {len(all_funding)} funding rows across {len(months)} months")

    if len(all_funding) < 200:
        results["error"] = f"Insufficient funding data ({len(all_funding)} rows)"
        return results

    # ------------------------------------------------------------------
    # 2. Compute rolling 180-day past-only percentile
    # ------------------------------------------------------------------
    first_ts = all_funding[0]["ts"]
    warmup_end = first_ts + timedelta(days=WARMUP_DAYS)

    eval_events = []
    for i, fr in enumerate(all_funding):
        if fr["ts"] <= warmup_end:
            continue
        lookback_start = fr["ts"] - timedelta(days=WARMUP_DAYS)
        past_rows = [r for r in all_funding[:i] if r["ts"] >= lookback_start]
        if len(past_rows) < 50:
            continue
        past_rates = sorted([r["funding_rate"] for r in past_rows])
        n = len(past_rates)
        top5 = past_rates[min(int(n * 0.95), n - 1)]
        bot5 = past_rates[min(int(n * 0.05), n - 1)]
        if fr["funding_rate"] >= top5:
            eval_events.append({"ts": fr["ts"], "funding_rate": fr["funding_rate"], "is_positive": True})
        elif fr["funding_rate"] <= bot5:
            eval_events.append({"ts": fr["ts"], "funding_rate": fr["funding_rate"], "is_positive": False})

    print(f"  Warmup: {first_ts} to {warmup_end} ({len(all_funding) - len(eval_events)} rows)")
    print(f"  Extreme events: {len(eval_events)} total")
    print(f"    Positive: {sum(1 for e in eval_events if e['is_positive'])}")
    print(f"    Negative: {sum(1 for e in eval_events if not e['is_positive'])}")

    results["eval_events_total"] = len(eval_events)
    results["eval_positive_extreme"] = sum(1 for e in eval_events if e["is_positive"])
    results["eval_negative_extreme"] = sum(1 for e in eval_events if not e["is_positive"])

    if len(eval_events) < 50:
        results["error"] = f"Too few extreme events ({len(eval_events)})"
        return results

    # ------------------------------------------------------------------
    # 3. Load OI data (parallelized)
    # ------------------------------------------------------------------
    unique_dates = set()
    for ev in eval_events:
        unique_dates.add(ev["ts"].strftime(YMD))
        unique_dates.add((ev["ts"] - timedelta(hours=FUNDING_INTERVAL_HOURS)).strftime(YMD))

    dates_sorted = sorted(unique_dates)
    print(f"\nLoading {len(dates_sorted)} unique daily OI files (parallel, 8 workers)...")

    oi_by_date = {}
    loaded = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(load_oi_daily_cached, d): d for d in dates_sorted}
        done = 0
        for f in as_completed(future_map):
            d = future_map[f]
            try:
                rows = f.result()
                if rows:
                    rows.sort(key=lambda x: x["ts"])
                    oi_by_date[d] = rows
                    loaded += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
            done += 1
            if done % 100 == 0:
                print(f"  ... {done}/{len(dates_sorted)} OI files loaded")

    print(f"  Loaded {loaded} daily files, {failed} not found")
    results["oi_daily_files_loaded"] = loaded
    results["oi_daily_files_missing"] = failed

    all_oi = []
    for date_str in sorted(oi_by_date.keys()):
        all_oi.extend(oi_by_date[date_str])
    all_oi.sort(key=lambda x: x["ts"])
    print(f"  Total OI rows: {len(all_oi)}")

    # ------------------------------------------------------------------
    # 4. OI classification
    # ------------------------------------------------------------------
    counts = {
        "total_aligned_settlements": 0,
        "total_excluded_oi_unaligned": 0,
        "positive_funding_extreme_count": 0,
        "negative_funding_extreme_count": 0,
        "positive_extreme_rising_oi": 0,
        "positive_extreme_falling_oi": 0,
        "negative_extreme_rising_oi": 0,
        "negative_extreme_falling_oi": 0,
    }

    train_counts = {k: 0 for k in [
        "aligned_settlements", "oi_unaligned", "positive_extreme_rising_oi",
        "positive_extreme_falling_oi", "negative_extreme_rising_oi",
        "negative_extreme_falling_oi"]}
    holdout_counts = {k: 0 for k in train_counts}

    eval_sorted = sorted(eval_events, key=lambda x: x["ts"])
    split_idx = int(len(eval_sorted) * 0.7)
    split_ts = eval_sorted[split_idx]["ts"]

    for ev in eval_sorted:
        settlement_ts = ev["ts"]
        s8h = settlement_ts - timedelta(hours=FUNDING_INTERVAL_HOURS)

        oi_end_val = None
        oi_start_val = None
        for item in all_oi:
            if item["ts"] <= settlement_ts:
                oi_end_val = item["oi"]
            else:
                break
        for item in all_oi:
            if item["ts"] <= s8h:
                oi_start_val = item["oi"]
            else:
                break

        is_train = ev["ts"] <= split_ts
        target = train_counts if is_train else holdout_counts

        if oi_end_val is None or oi_start_val is None or oi_start_val == 0:
            target["oi_unaligned"] += 1
            continue

        oi_change_pct = (oi_end_val - oi_start_val) / oi_start_val
        rising_oi = oi_change_pct > 0
        target["aligned_settlements"] += 1

        if ev["is_positive"]:
            if rising_oi:
                target["positive_extreme_rising_oi"] += 1
            else:
                target["positive_extreme_falling_oi"] += 1
        else:
            if rising_oi:
                target["negative_extreme_rising_oi"] += 1
            else:
                target["negative_extreme_falling_oi"] += 1

    def sum_up(key):
        return train_counts.get(key, 0) + holdout_counts.get(key, 0)

    counts["total_aligned_settlements"] = sum_up("aligned_settlements")
    counts["total_excluded_oi_unaligned"] = sum_up("oi_unaligned")
    counts["positive_extreme_rising_oi"] = sum_up("positive_extreme_rising_oi")
    counts["positive_extreme_falling_oi"] = sum_up("positive_extreme_falling_oi")
    counts["negative_extreme_rising_oi"] = sum_up("negative_extreme_rising_oi")
    counts["negative_extreme_falling_oi"] = sum_up("negative_extreme_falling_oi")
    counts["positive_funding_extreme_count"] = (
        sum_up("positive_extreme_rising_oi") + sum_up("positive_extreme_falling_oi")
    )
    counts["negative_funding_extreme_count"] = (
        sum_up("negative_extreme_rising_oi") + sum_up("negative_extreme_falling_oi")
    )

    pos_total = counts["positive_funding_extreme_count"]
    neg_total = counts["negative_funding_extreme_count"]
    if pos_total > 0:
        counts["rising_falling_split_positive"] = {
            "rising_pct": round(counts["positive_extreme_rising_oi"] / pos_total * 100, 2),
            "falling_pct": round(counts["positive_extreme_falling_oi"] / pos_total * 100, 2),
        }
    if neg_total > 0:
        counts["rising_falling_split_negative"] = {
            "rising_pct": round(counts["negative_extreme_rising_oi"] / neg_total * 100, 2),
            "falling_pct": round(counts["negative_extreme_falling_oi"] / neg_total * 100, 2),
        }

    counts["train_70_counts"] = {
        k: train_counts[k]
        for k in ["aligned_settlements", "oi_unaligned", "positive_extreme_rising_oi",
                   "positive_extreme_falling_oi", "negative_extreme_rising_oi",
                   "negative_extreme_falling_oi"]
    }
    counts["holdout_30_counts"] = {
        k: holdout_counts[k]
        for k in ["aligned_settlements", "oi_unaligned", "positive_extreme_rising_oi",
                   "positive_extreme_falling_oi", "negative_extreme_rising_oi",
                   "negative_extreme_falling_oi"]
    }

    # Verdict
    primary_pos_rising = counts["positive_extreme_rising_oi"]
    primary_neg_rising = counts["negative_extreme_rising_oi"]
    holdout_pos_rising = holdout_counts["positive_extreme_rising_oi"]
    holdout_neg_rising = holdout_counts["negative_extreme_rising_oi"]

    notes = []
    if primary_pos_rising < MIN_EVENTS or primary_neg_rising < MIN_EVENTS:
        counts["verdict"] = "PRIMARY_TOTAL_EVENTS_UNDERPOWERED"
        notes.append(
            f"Primary cells underpowered: pos_rising={primary_pos_rising}, "
            f"neg_rising={primary_neg_rising} (need >= {MIN_EVENTS} each)"
        )
    elif holdout_pos_rising < MIN_EVENTS or holdout_neg_rising < MIN_EVENTS:
        counts["verdict"] = "PRIMARY_POPULATION_UNDERPOWERED"
        notes.append(
            f"Holdout underpowered: pos_rising={holdout_pos_rising}, "
            f"neg_rising={holdout_neg_rising} (need >= {MIN_EVENTS} each)"
        )
    else:
        counts["verdict"] = "PHASE0B_POPULATION_FEASIBILITY_PASSED"

    if counts.get("positive_extreme_falling_oi", 0) < 20:
        notes.append("DIAGNOSTIC_FALLING_OI_UNDERPOWERED: positive extreme falling OI sparse")
    if counts.get("negative_extreme_falling_oi", 0) < 20:
        notes.append("DIAGNOSTIC_FALLING_OI_UNDERPOWERED: negative extreme falling OI sparse")

    counts["notes"] = notes
    results.update(counts)

    json_path = os.path.join(output_dir, "population_counts.json")
    os.makedirs(output_dir, exist_ok=True)
    with open(json_path, "w") as f:
        json.dump(counts, f, indent=2, default=str)
    print(f"\nWrote {json_path}")

    return results


def main():
    print("=" * 70)
    print("Phase 0B: Blind Population Sizing (Counts-Only)")
    print("=" * 70)
    print("Family 3: BTCUSDT Funding × Open Interest Crowding Regime")
    print("Data source: Binance Vision public archive (threaded downloads)")
    print("Method: Rolling 180-day past-only percentile membership")
    print()

    output_dir = "reports/funding_oi_crowding_regime_v0"
    results = run_phase0b(output_dir)

    v = results.get("verdict", "N/A")
    print(f"\n=== Phase 0B Verdict: {v} ===")
    print(f"  Extreme events: {results.get('eval_events_total', '?')}")
    print(f"  Positive extremes: {results.get('eval_positive_extreme', '?')}")
    print(f"  Negative extremes: {results.get('eval_negative_extreme', '?')}")
    print(f"  OI-aligned settlements: {results.get('total_aligned_settlements', '?')}")
    print(f"  OI_UNALIGNED: {results.get('total_excluded_oi_unaligned', '?')}")
    print(f"  Pos rising: {results.get('positive_extreme_rising_oi', '?')}")
    print(f"  Pos falling: {results.get('positive_extreme_falling_oi', '?')}")
    print(f"  Neg rising: {results.get('negative_extreme_rising_oi', '?')}")
    print(f"  Neg falling: {results.get('negative_extreme_falling_oi', '?')}")
    print(f"  Holdout pos rising: {results.get('holdout_30_counts', {}).get('positive_extreme_rising_oi', '?')}")
    print(f"  Holdout neg rising: {results.get('holdout_30_counts', {}).get('negative_extreme_rising_oi', '?')}")

    if results.get("notes"):
        for n in results["notes"]:
            print(f"  Note: {n}")

    print("\nNo threshold values, event timestamps, forward returns, edge stats,")
    print("null, FDR, evaluation, registry update, or bot path was used.")


if __name__ == "__main__":
    main()
