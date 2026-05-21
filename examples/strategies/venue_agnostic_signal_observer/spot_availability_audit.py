#!/usr/bin/env python3
"""
Spot-Availability Diagnostic Auditor.

Instruments the spot availability check for Family 3 v1 falling-OI unwind.
Identifies root cause of 66 -> 26 holdout collapse.
Does NOT compute returns, edge stats, nulls, FDR, or update registry.
"""

import io
import json
import math
import os
import sys
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import UTC
from datetime import datetime
from datetime import timedelta


# Ensure the module is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from funding_falling_oi_unwind_phase0 import BASE_URL
from funding_falling_oi_unwind_phase0 import CACHE_DIR
from funding_falling_oi_unwind_phase0 import DAILY_OI_PATH_TEMPLATE
from funding_falling_oi_unwind_phase0 import END_YM
from funding_falling_oi_unwind_phase0 import FUNDING_HOURS
from funding_falling_oi_unwind_phase0 import MONTHLY_FUNDING_PATH_TEMPLATE
from funding_falling_oi_unwind_phase0 import OI_PRIMARY_FIELD
from funding_falling_oi_unwind_phase0 import START_YM
from funding_falling_oi_unwind_phase0 import SYMBOL
from funding_falling_oi_unwind_phase0 import TIMESTAMP_FIELD
from funding_falling_oi_unwind_phase0 import WARMUP_DAYS
from funding_falling_oi_unwind_phase0 import YMD
from funding_falling_oi_unwind_phase0 import _spot_klines_path
from funding_falling_oi_unwind_phase0 import download_zip_cached
from funding_falling_oi_unwind_phase0 import extract_csv_from_zip
from funding_falling_oi_unwind_phase0 import iter_months
from funding_falling_oi_unwind_phase0 import parse_klines_timestamp
from funding_falling_oi_unwind_phase0 import parse_timestamp


OUTPUT_DIR = "reports/funding_falling_oi_unwind_v1"


def load_funding_all():
    """Load all monthly funding files. Returns list of {ts, funding_rate} sorted."""
    months = list(iter_months(START_YM, END_YM))
    print(f"Loading {len(months)} monthly funding rate files {START_YM} to {END_YM}...")
    all_funding = []
    for i, ym in enumerate(months):
        path = MONTHLY_FUNDING_PATH_TEMPLATE.format(symbol=SYMBOL, ym=ym)
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
                        ts = datetime.fromtimestamp(calc_time_ms / 1000, tz=UTC)
                        all_funding.append({"ts": ts, "funding_rate": rate})
                    except (ValueError, KeyError):
                        pass
        except Exception:
            continue
        if (i + 1) % 15 == 0:
            print(f"  ... {i+1}/{len(months)} months, {len(all_funding)} rows")
    all_funding.sort(key=lambda x: x["ts"])
    print(f"  Total: {len(all_funding)} funding rows")
    return all_funding


def load_oi_for_dates(dates):
    """Load OI data for specific dates. Returns a big sorted list and per-file hashes."""
    dates_sorted = sorted(set(dates))
    print(f"Loading {len(dates_sorted)} unique daily OI files (parallel)...")
    oi_by_date = {}
    loaded = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {}
        for d in dates_sorted:
            path = DAILY_OI_PATH_TEMPLATE.format(symbol=SYMBOL, date=d)
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
    print(f"  Loaded {loaded} OI files, {failed} missing, {len(all_oi)} rows total")
    return all_oi


def load_spot_with_diagnostics():
    """
    Load spot klines with full diagnostic metadata per monthly ZIP.
    Returns (spot_klines_list, zip_metadata_dict).
    zip_metadata: {ym: {"exists": bool, "first_ts": str, "last_ts": str, "n_rows": int}}
    """
    months = list(iter_months(START_YM, END_YM))
    spot_klines = []
    zip_metadata = OrderedDict()

    for ym in months:
        path = _spot_klines_path(SYMBOL, ym)
        url = f"{BASE_URL}/{path}"
        cache_key = f"spot_klines_{ym}.zip"
        meta = {"ym": ym, "path": path, "exists": False, "download_error": None,
                "first_ts": None, "last_ts": None, "n_rows": 0}
        try:
            zip_bytes = download_zip_cached(url, cache_key, timeout=30)
            meta["exists"] = True
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                names = [n for n in zf.namelist() if n.endswith(".csv")]
                if not names:
                    meta["download_error"] = "No CSV in ZIP"
                    zip_metadata[ym] = meta
                    continue
                with zf.open(names[0]) as f:
                    content = f.read().decode("utf-8")
            rows_parsed = 0
            for line in content.strip().split("\n"):
                parts = line.split(",")
                if len(parts) >= 5:
                    try:
                        open_ms = int(parts[0])
                        close = float(parts[4])
                        ts = parse_klines_timestamp(open_ms)
                        if ts:
                            spot_klines.append({"ts": ts, "close": close})
                            rows_parsed += 1
                            if meta["first_ts"] is None:
                                meta["first_ts"] = ts.isoformat()
                            meta["last_ts"] = ts.isoformat()
                    except (ValueError, IndexError):
                        pass
            meta["n_rows"] = rows_parsed
        except Exception as e:
            meta["download_error"] = str(e)

        zip_metadata[ym] = meta

    spot_klines.sort(key=lambda x: x["ts"])
    print(f"  Total: {len(spot_klines)} spot klines across {len([m for m in zip_metadata.values() if m['exists']])} months")
    return spot_klines, zip_metadata


def compute_oi_regime(settlement_ts, oi_rows):
    """Compute OI regime for a given settlement timestamp."""
    s8h = settlement_ts - timedelta(hours=8)
    oi_end_val = None
    for row in reversed(oi_rows):
        if row["ts"] <= settlement_ts:
            oi_end_val = row["oi"]
            break
    oi_start_val = None
    for row in reversed(oi_rows):
        if row["ts"] <= s8h:
            oi_start_val = row["oi"]
            break
    if oi_end_val is None or oi_start_val is None:
        return "OI_UNALIGNED", None
    if oi_start_val <= 0 or not math.isfinite(oi_start_val):
        return "OI_UNALIGNED", None
    if oi_end_val <= 0 or not math.isfinite(oi_end_val):
        return "OI_UNALIGNED", None
    oi_change_pct = (oi_end_val - oi_start_val) / oi_start_val
    regime = "falling_oi" if oi_change_pct <= 0 else "rising_oi"
    return regime, oi_change_pct


def diagnostic_spot_availability(settlement_ts, spot_klines, horizon_h, spot_zip_meta):
    """
    Full diagnostic for a single event's spot availability.
    Returns (available_bool, diagnostic_dict).
    """
    diag = {
        "settlement_ts": settlement_ts.isoformat(),
        "horizon_h": horizon_h,
        "target_ts": None,
        "day_of_month": settlement_ts.day,
        "days_until_month_end": None,
        "settlement_month": settlement_ts.strftime("%Y-%m"),
        "settlement_month_zip_exists": None,
        "target_month": None,
        "target_month_zip_exists": None,
        "available_months_loaded": [],
        "spot_first_ts": None,
        "spot_last_ts": None,
        "spot_n_rows": len(spot_klines),
        "entry_price_ts": None,
        "entry_price": None,
        "fwd_price_ts": None,
        "fwd_price": None,
        "failure_reason_code": None,
        "availability": False,
    }

    # Compute target
    target_ts = settlement_ts + timedelta(hours=horizon_h)
    diag["target_ts"] = target_ts.isoformat()
    diag["target_month"] = target_ts.strftime("%Y-%m")

    # Days until month end
    import calendar
    last_day = calendar.monthrange(settlement_ts.year, settlement_ts.month)[1]
    diag["days_until_month_end"] = last_day - settlement_ts.day

    # First/last spot timestamps
    if spot_klines:
        diag["spot_first_ts"] = spot_klines[0]["ts"].isoformat()
        diag["spot_last_ts"] = spot_klines[-1]["ts"].isoformat()

    # Check spot ZIP existence
    settlement_ym = settlement_ts.strftime("%Y-%m")
    target_ym = target_ts.strftime("%Y-%m")

    settlement_meta = spot_zip_meta.get(settlement_ym, {})
    target_meta = spot_zip_meta.get(target_ym, {})

    diag["settlement_month_zip_exists"] = settlement_meta.get("exists", False)
    diag["target_month_zip_exists"] = target_meta.get("exists", False)
    diag["available_months_loaded"] = [
        ym for ym, m in spot_zip_meta.items() if m.get("exists")
    ]

    # Check cross-month: does target cross into a different month?
    crosses_month = settlement_ym != target_ym
    diag["crosses_month_boundary"] = crosses_month

    # Entry price
    entry_price = None
    entry_ts = None
    for k in spot_klines:
        if k["ts"] >= settlement_ts:
            entry_price = k["close"]
            entry_ts = k["ts"]
            break
    diag["entry_price_ts"] = entry_ts.isoformat() if entry_ts else None
    diag["entry_price"] = entry_price

    if entry_price is None or entry_price <= 0:
        # Determine why entry failed
        if not settlement_meta.get("exists", False):
            diag["failure_reason_code"] = "SPOT_MONTH_ZIP_MISSING"
        elif len(spot_klines) == 0:
            diag["failure_reason_code"] = "SPOT_ROWS_EMPTY"
        elif spot_klines and spot_klines[-1]["ts"] < settlement_ts:
            diag["failure_reason_code"] = "SPOT_TARGET_BEYOND_ARCHIVE_END"
        elif spot_klines and spot_klines[0]["ts"] > settlement_ts:
            diag["failure_reason_code"] = "SPOT_TARGET_BEFORE_ARCHIVE_START"
        else:
            diag["failure_reason_code"] = "SPOT_TIMESTAMP_UNIT_MISMATCH_SUSPECTED"
        return False, diag

    # Forward price
    fwd_price = None
    fwd_ts = None
    for k in spot_klines:
        if k["ts"] >= target_ts:
            fwd_price = k["close"]
            fwd_ts = k["ts"]
            break

    diag["fwd_price_ts"] = fwd_ts.isoformat() if fwd_ts else None
    diag["fwd_price"] = fwd_price

    if fwd_price is None or fwd_price <= 0:
        if crosses_month and not target_meta.get("exists", False):
            diag["failure_reason_code"] = "SPOT_NEXT_MONTH_ZIP_MISSING"
        elif crosses_month and target_meta.get("exists", False):
            diag["failure_reason_code"] = "SPOT_NEXT_MONTH_ZIP_NOT_LOADED"
        elif spot_klines and spot_klines[-1]["ts"] < target_ts:
            diag["failure_reason_code"] = "SPOT_TARGET_BEYOND_ARCHIVE_END"
        elif not crosses_month and settlement_meta.get("exists", False):
            diag["failure_reason_code"] = "SPOT_FORWARD_BOUNDARY_OFF_BY_ONE_SUSPECTED"
        else:
            diag["failure_reason_code"] = "SPOT_UNKNOWN_FAILURE"
        return False, diag

    diag["availability"] = True
    diag["failure_reason_code"] = "SPOT_OK"
    return True, diag


def main():
    print("=" * 70)
    print("Spot-Availability Diagnostic Auditor")
    print("Family 3 v1: BTCUSDT Funding × Falling OI Unwind-Continuation")
    print("=" * 70)
    print()
    print(f"Archive range: {START_YM} to {END_YM}")
    print(f"Cache dir: {CACHE_DIR}")
    print()

    # ------------------------------------------------------------------
    # 1. Load funding data
    # ------------------------------------------------------------------
    print("--- Loading funding data ---")
    all_funding = load_funding_all()
    first_ts = all_funding[0]["ts"]
    warmup_end = first_ts + timedelta(days=WARMUP_DAYS)
    print(f"  Funding range: {first_ts} to {all_funding[-1]['ts']}")
    print(f"  Warmup end: {warmup_end}")
    print()

    # ------------------------------------------------------------------
    # 2. Identify negative extreme events
    # ------------------------------------------------------------------
    print("--- Identifying negative funding extreme events ---")
    negative_extreme_events = []
    for i, fr in enumerate(all_funding):
        if fr["ts"] <= warmup_end:
            continue
        # Check settlement grid alignment
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
            negative_extreme_events.append({"ts": fr["ts"], "funding_rate": fr["funding_rate"]})

    print(f"  Negative extreme events: {len(negative_extreme_events)}")
    print()

    # ------------------------------------------------------------------
    # 3. Load OI data
    # ------------------------------------------------------------------
    print("--- Loading OI data ---")
    unique_dates = set()
    for ev in negative_extreme_events:
        unique_dates.add(ev["ts"].strftime(YMD))
        unique_dates.add((ev["ts"] - timedelta(hours=8)).strftime(YMD))
    all_oi = load_oi_for_dates(list(unique_dates))
    print()

    # ------------------------------------------------------------------
    # 4. OI classification -> falling OI events
    # ------------------------------------------------------------------
    print("--- OI classification ---")
    falling_oi_events = []
    for ev in negative_extreme_events:
        regime, _ = compute_oi_regime(ev["ts"], all_oi)
        if regime == "falling_oi":
            falling_oi_events.append(ev)

    falling_oi_sorted = sorted(falling_oi_events, key=lambda x: x["ts"])
    print(f"  Falling OI events: {len(falling_oi_sorted)}")
    print(f"  Date range: {falling_oi_sorted[0]['ts']} to {falling_oi_sorted[-1]['ts']}")

    # Split
    split_idx = int(len(falling_oi_sorted) * 0.7)
    split_ts = falling_oi_sorted[split_idx]["ts"]
    train_events = [e for e in falling_oi_sorted if e["ts"] <= split_ts]
    holdout_events = [e for e in falling_oi_sorted if e["ts"] > split_ts]
    print(f"  Split: {split_ts.isoformat()}")
    print(f"  Train: {len(train_events)}, Holdout: {len(holdout_events)}")
    print()

    # ------------------------------------------------------------------
    # 5. Load spot klines with full ZIP metadata
    # ------------------------------------------------------------------
    print("--- Loading spot klines with ZIP metadata ---")
    spot_klines, spot_zip_meta = load_spot_with_diagnostics()

    # Report ZIP coverage
    missing_zips = [ym for ym, m in spot_zip_meta.items() if not m["exists"]]
    existing_zips = [ym for ym, m in spot_zip_meta.items() if m["exists"]]
    print(f"  Spot months: {len(existing_zips)} loaded, {len(missing_zips)} missing")
    if missing_zips:
        print(f"  Missing ZIPs: {missing_zips}")
    if spot_klines:
        print(f"  Spot range: {spot_klines[0]['ts']} to {spot_klines[-1]['ts']}")
    print()

    # ------------------------------------------------------------------
    # 6. Full diagnostic spot-availability audit
    # ------------------------------------------------------------------
    print("--- Running diagnostic spot-availability audit ---")
    audit_records = []

    # Track aggregates
    total_fail_24h = 0
    total_fail_48h = 0
    holdout_fail_24h = 0
    holdout_fail_48h = 0
    fail_by_dom = {}
    fail_by_month = {}
    fail_by_days_until_month_end = {}
    fail_crosses_month = 0
    fail_not_cross_month = 0
    fail_next_month_not_loaded = 0
    fail_next_month_exists_not_loaded = 0

    fail_24h_only = 0
    fail_48h_only = 0
    fail_both = 0

    for ev in falling_oi_sorted:
        settlement_ts = ev["ts"]
        bucket = "holdout" if settlement_ts > split_ts else "train"

        for _horizon_h in [24, 48]:
            avail_24, diag_24 = diagnostic_spot_availability(
                settlement_ts, spot_klines, 24, spot_zip_meta
            )
            avail_48, diag_48 = diagnostic_spot_availability(
                settlement_ts, spot_klines, 48, spot_zip_meta
            )

            record = {
                "settlement_ts": settlement_ts.isoformat(),
                "funding_rate": ev["funding_rate"],
                "split_bucket": bucket,
                "day_of_month": settlement_ts.day,
                "days_until_month_end": diag_24["days_until_month_end"],
                "settlement_month": settlement_ts.strftime("%Y-%m"),
                "available_24h": avail_24,
                "available_48h": avail_48,
                "reason_24h": diag_24["failure_reason_code"],
                "reason_48h": diag_48["failure_reason_code"],
                "last_spot_ts": spot_klines[-1]["ts"].isoformat() if spot_klines else None,
                "first_spot_ts": spot_klines[0]["ts"].isoformat() if spot_klines else None,
                "entry_price_ts_24h": diag_24.get("entry_price_ts"),
                "fwd_price_ts_24h": diag_24.get("fwd_price_ts"),
                "fwd_price_ts_48h": diag_48.get("fwd_price_ts"),
                "target_ts_24h": diag_24["target_ts"],
                "target_ts_48h": diag_48["target_ts"],
                "crosses_month_24h": diag_24.get("crosses_month_boundary", False),
                "crosses_month_48h": diag_48.get("crosses_month_boundary", False),
                "settlement_month_zip_exists": diag_24.get("settlement_month_zip_exists"),
                "target_month_24h": diag_24.get("target_month"),
                "target_month_24h_zip_exists": diag_24.get("target_month_zip_exists"),
                "target_month_48h": diag_48.get("target_month"),
                "target_month_48h_zip_exists": diag_48.get("target_month_zip_exists"),
            }
            audit_records.append(record)

        # Track aggregate failure patterns per-event
        if not avail_24 or not avail_48:
            fail_24 = not avail_24
            fail_48 = not avail_48
            if fail_24 and fail_48:
                fail_both += 1
            elif fail_24:
                fail_24h_only += 1
            elif fail_48:
                fail_48h_only += 1

            if not avail_24:
                total_fail_24h += 1
                if bucket == "holdout":
                    holdout_fail_24h += 1
                dom = settlement_ts.day
                fail_by_dom[dom] = fail_by_dom.get(dom, 0) + 1
                month_key = settlement_ts.strftime("%Y-%m")
                fail_by_month[month_key] = fail_by_month.get(month_key, 0) + 1
                dum = diag_24["days_until_month_end"]
                bucket_dum = _bucket_days_until_end(dum)
                fail_by_days_until_month_end[bucket_dum] = fail_by_days_until_month_end.get(bucket_dum, 0) + 1
                if diag_24.get("crosses_month_boundary"):
                    fail_crosses_month += 1
                else:
                    fail_not_cross_month += 1
                if diag_24["failure_reason_code"] == "SPOT_NEXT_MONTH_ZIP_NOT_LOADED":
                    fail_next_month_not_loaded += 1
                if diag_24["failure_reason_code"] == "SPOT_NEXT_MONTH_ZIP_MISSING":
                    fail_next_month_exists_not_loaded += 1

            if not avail_48:
                total_fail_48h += 1
                if bucket == "holdout":
                    holdout_fail_48h += 1

    print("\n=== Aggregate Results ===")
    print(f"  Total falling OI events: {len(falling_oi_sorted)}")
    print(f"  Train: {len(train_events)}, Holdout: {len(holdout_events)}")
    print(f"  Total fail 24h: {total_fail_24h}")
    print(f"  Total fail 48h: {total_fail_48h}")
    print(f"  Holdout fail 24h: {holdout_fail_24h}")
    print(f"  Holdout fail 48h: {holdout_fail_48h}")

    # Events that pass both horizons
    pass_24h = 0
    pass_48h = 0
    for ev in falling_oi_sorted:
        _, d24 = diagnostic_spot_availability(ev["ts"], spot_klines, 24, spot_zip_meta)
        _, d48 = diagnostic_spot_availability(ev["ts"], spot_klines, 48, spot_zip_meta)
        if d24["availability"]:
            pass_24h += 1
        if d48["availability"]:
            pass_48h += 1

    # Holdout-specific
    holdout_pass_24h = 0
    holdout_pass_48h = 0
    for ev in holdout_events:
        _, d24 = diagnostic_spot_availability(ev["ts"], spot_klines, 24, spot_zip_meta)
        _, d48 = diagnostic_spot_availability(ev["ts"], spot_klines, 48, spot_zip_meta)
        if d24["availability"]:
            holdout_pass_24h += 1
        if d48["availability"]:
            holdout_pass_48h += 1

    print(f"  Events with 24h spot: {pass_24h} (holdout: {holdout_pass_24h})")
    print(f"  Events with 48h spot: {pass_48h} (holdout: {holdout_pass_48h})")
    print(f"  Fail both 24h+48h: {fail_both}")
    print(f"  Fail 24h only: {fail_24h_only}")
    print(f"  Fail 48h only: {fail_48h_only}")

    print("\n  Failures by day_of_month:")
    for dom in sorted(fail_by_dom.keys()):
        print(f"    Day {dom}: {fail_by_dom[dom]} failures")

    print("\n  Failures by settlement_month:")
    for m in sorted(fail_by_month.keys()):
        print(f"    {m}: {fail_by_month[m]} failures")

    print("\n  Failures by days_until_month_end bucket:")
    for b in sorted(fail_by_days_until_month_end.keys()):
        print(f"    {b}: {fail_by_days_until_month_end[b]} failures")

    print(f"\n  Cross-month boundary failures: {fail_crosses_month}")
    print(f"  Non-cross-month failures: {fail_not_cross_month}")
    print(f"  Fail where next month ZIP not loaded: {fail_next_month_not_loaded}")
    print(f"  Fail where next month ZIP exists but not loaded: {fail_next_month_exists_not_loaded}")

    # ---- Diagnostic checks on the spot loader ----
    print("\n--- Spot Loader Diagnostics ---")
    print(f"  Spot last timestamp: {spot_klines[-1]['ts'].isoformat() if spot_klines else 'NONE'}")
    print(f"  Spot first timestamp: {spot_klines[0]['ts'].isoformat() if spot_klines else 'NONE'}")

    # Check if last spot month covers last event + 48h
    last_event_ts = falling_oi_sorted[-1]["ts"]
    last_event_48h = last_event_ts + timedelta(hours=48)
    print(f"  Last event: {last_event_ts.isoformat()}, +48h: {last_event_48h.isoformat()}")
    if spot_klines:
        if spot_klines[-1]["ts"] >= last_event_48h:
            print("  Spot covers last event + 48h: YES")
        else:
            print(f"  Spot covers last event + 48h: NO (last spot: {spot_klines[-1]['ts'].isoformat()})")

    # Check if any events near end fail only because of archive tail
    near_end_count = sum(1 for ev in falling_oi_sorted
                         if ev["ts"] + timedelta(hours=48) > spot_klines[-1]["ts"])
    print(f"  Events where 48h target beyond last spot row: {near_end_count}")

    # ---- Determine failure distribution ----
    print("\n--- Failure Distribution Analysis ---")
    if spot_klines:
        last_spot = spot_klines[-1]["ts"]
        early_event_count = 0
        mid_event_count = 0
        late_event_count = 0
        for ev in falling_oi_sorted:
            ts = ev["ts"]
            if ts < split_ts:
                early_event_count += 1
            elif ts + timedelta(hours=48) > last_spot:
                late_event_count += 1
            else:
                mid_event_count += 1
        print(f"  Before split (train): {early_event_count}")
        print(f"  After split but within spot range (mid): {mid_event_count}")
        print(f"  After split and near/at archive tail (late): {late_event_count}")

    # ---- Interpret ---
    print("\n--- Interpretation ---")

    # Check if 24h and 48h failure sets are identical
    # Examine unique failure patterns by event
    identical_failures = True
    for ev in falling_oi_sorted:
        _, d24 = diagnostic_spot_availability(ev["ts"], spot_klines, 24, spot_zip_meta)
        _, d48 = diagnostic_spot_availability(ev["ts"], spot_klines, 48, spot_zip_meta)
        if d24["availability"] != d48["availability"]:
            identical_failures = False
            break

    if identical_failures or (fail_24h_only == 0 and fail_48h_only == 0):
        print("  FAILURE SETS: 24h and 48h failure sets are IDENTICAL")
        print("  -> Evidence of: SPOT_HORIZON_LOGIC_BUG_CONFIRMED")
    else:
        print(f"  FAILURE SETS: 24h and 48h differ (24h-only: {fail_24h_only}, 48h-only: {fail_48h_only})")

    # Check for month-boundary clustering
    # If failures cluster at high days_until_month_end values, entries failing are early-month
    # If failures cluster at low days_until_month_end values, entries failing are late-month
    late_month_fails = fail_by_days_until_month_end.get("0 days", 0) + \
                       fail_by_days_until_month_end.get("1 day", 0) + \
                       fail_by_days_until_month_end.get("2 days", 0) + \
                       fail_by_days_until_month_end.get("3 days", 0)
    early_month_fails = fail_by_days_until_month_end.get("8+ days", 0)
    print(f"  Late-month failures (0-3 days to month end): {late_month_fails}")
    print(f"  Early-month failures (8+ days to month end): {early_month_fails}")

    if late_month_fails > 0 and early_month_fails == 0:
        print("  -> Evidence of: SPOT_CROSS_MONTH_LOADER_BUG_CONFIRMED")
    elif late_month_fails > 0 and early_month_fails > 0:
        print("  -> Mixed distribution - not purely month-boundary")
    else:
        print("  -> Not a month-boundary pattern")

    # Check cross-month boundary
    if fail_crosses_month > 0 and fail_not_cross_month == 0:
        print("  ALL failures cross month boundaries -> SPOT_CROSS_MONTH_LOADER_BUG_CONFIRMED")
    elif fail_crosses_month == 0 and fail_not_cross_month > 0:
        print("  NO failures cross month boundaries -> not a cross-month issue")

    # ---- Write audit JSON ---
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    audit_json_path = os.path.join(OUTPUT_DIR, "spot_availability_audit.json")

    audit_report = OrderedDict()
    audit_report["diagnostic_run_ts"] = datetime.now(UTC).isoformat()
    audit_report["total_events"] = len(falling_oi_sorted)
    audit_report["train_count"] = len(train_events)
    audit_report["holdout_count"] = len(holdout_events)
    audit_report["pass_24h"] = pass_24h
    audit_report["pass_48h"] = pass_48h
    audit_report["holdout_pass_24h"] = holdout_pass_24h
    audit_report["holdout_pass_48h"] = holdout_pass_48h
    audit_report["total_fail_24h"] = total_fail_24h
    audit_report["total_fail_48h"] = total_fail_48h
    audit_report["holdout_fail_24h"] = holdout_fail_24h
    audit_report["holdout_fail_48h"] = holdout_fail_48h
    audit_report["fail_both"] = fail_both
    audit_report["fail_24h_only"] = fail_24h_only
    audit_report["fail_48h_only"] = fail_48h_only
    audit_report["fail_by_day_of_month"] = {str(k): v for k, v in sorted(fail_by_dom.items())}
    audit_report["fail_by_settlement_month"] = dict(sorted(fail_by_month.items()))
    audit_report["fail_by_days_until_month_end"] = dict(sorted(fail_by_days_until_month_end.items()))
    audit_report["fail_crosses_month_boundary"] = fail_crosses_month
    audit_report["fail_not_cross_month_boundary"] = fail_not_cross_month
    audit_report["spot_last_ts"] = spot_klines[-1]["ts"].isoformat() if spot_klines else None
    audit_report["spot_first_ts"] = spot_klines[0]["ts"].isoformat() if spot_klines else None
    audit_report["last_event_ts"] = last_event_ts.isoformat()
    audit_report["last_event_48h_target"] = last_event_48h.isoformat()
    audit_report["last_event_48h_in_range"] = spot_klines[-1]["ts"] >= last_event_48h if spot_klines else False
    audit_report["near_end_count_48h_beyond"] = near_end_count
    audit_report["spot_zip_metadata"] = spot_zip_meta
    audit_report["identical_failure_sets"] = identical_failures or (fail_24h_only == 0 and fail_48h_only == 0)

    # Verdict
    early_non_cross = fail_not_cross_month - late_month_fails if fail_not_cross_month > late_month_fails else 0
    if identical_failures or (fail_24h_only == 0 and fail_48h_only == 0):
        audit_report["verdict"] = "SPOT_HORIZON_LOGIC_BUG_CONFIRMED"
        audit_report["verdict_reason"] = "24h and 48h failure sets are identical; horizon-specific target_ts not affecting result"
    elif fail_crosses_month > 0 and (fail_not_cross_month == 0 or late_month_fails == fail_not_cross_month):
        audit_report["verdict"] = "SPOT_CROSS_MONTH_LOADER_BUG_CONFIRMED"
        audit_report["verdict_reason"] = f"All {fail_crosses_month} failures cross month boundaries"
    elif late_month_fails > 0 and early_non_cross == 0:
        audit_report["verdict"] = "SPOT_CROSS_MONTH_LOADER_BUG_CONFIRMED"
        audit_report["verdict_reason"] = f"Failures cluster at month-end (late-month: {late_month_fails})"
    elif near_end_count >= holdout_fail_48h:
        audit_report["verdict"] = "SPOT_ARCHIVE_TAIL_LIMIT_CONFIRMED"
        audit_report["verdict_reason"] = f"All {near_end_count} failures are near archive tail"
    else:
        audit_report["verdict"] = "SPOT_AVAILABILITY_COLLAPSE_UNEXPLAINED"
        audit_report["verdict_reason"] = "No clear pattern found in failure distribution"

    with open(audit_json_path, "w") as f:
        json.dump(audit_report, f, indent=2, default=str)
    print(f"\nWrote audit JSON to {audit_json_path}")

    # Also write per-event CSV for deep analysis
    # Summary level only
    print("\nDone. No forward returns, edge stats, null, FDR, or registry update was used.")

    return audit_report


def _bucket_days_until_end(days):
    if days is None:
        return "unknown"
    if days == 0:
        return "0 days"
    elif days == 1:
        return "1 day"
    elif days == 2:
        return "2 days"
    elif days == 3:
        return "3 days"
    elif days <= 7:
        return "4-7 days"
    else:
        return "8+ days"


if __name__ == "__main__":
    main()
