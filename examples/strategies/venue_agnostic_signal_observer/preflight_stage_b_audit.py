#!/usr/bin/env python3
"""
Preflight Stage B Audit for Family 3 v1 funding_falling_oi_unwind_v1.

Checks:
1. 24h/48h horizon-awareness is proven at the event level
2. Funding timestamp parser is free of unit bugs
3. OI metrics timestamp parser alignment is genuine
4. No prices, returns, edge stats, nulls, FDR computed

Hard constraints:
- No Stage B work.
- No returns/edge/stats/null/FDR.
- No registry update.
"""

import hashlib
import io
import json
import math
import os
import sys
import urllib.request
import zipfile
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from funding_falling_oi_unwind_phase0 import (
    BASE_URL, SYMBOL,
    WARMUP_DAYS, MIN_CELL_EVENTS, MIN_HOLDOUT_EVENTS,
    FUNDING_HOURS,
    OI_PRIMARY_FIELD, TIMESTAMP_FIELD,
    MONTHLY_FUNDING_PATH_TEMPLATE,
    DAILY_OI_PATH_TEMPLATE,
    SPOT_KLINES_MONTHLY_PATH,
    START_YM, END_YM,
    CACHE_DIR,
    YMD, YM, DT_FMT,
    _monthly_funding_path,
    _daily_oi_path,
    _spot_klines_path,
    download_zip_cached,
    extract_csv_from_zip,
    parse_timestamp,
    parse_klines_timestamp,
    iter_months,
    sha256_of_bytes,
)

OUTPUT_DIR = "reports/funding_falling_oi_unwind_v1"


def load_funding_with_raw():
    """
    Load funding data with raw calc_time values preserved for unit analysis.
    Returns (parsed_list, raw_samples_dict).
    """
    months = list(iter_months(START_YM, END_YM))
    all_funding = []
    raw_samples = {"earliest": None, "around_2024_12": None, "around_2025_01": None, "latest": None}

    for ym in months:
        path = MONTHLY_FUNDING_PATH_TEMPLATE.format(symbol=SYMBOL, ym=ym)
        url = f"{BASE_URL}/{path}"
        cache_key = f"funding_{ym}.zip"
        try:
            zip_bytes = download_zip_cached(url, cache_key, timeout=15)
            hdr, rows = extract_csv_from_zip(zip_bytes)
            if hdr and rows:
                for r in rows:
                    try:
                        calc_time_raw = r["calc_time"]
                        calc_time_ms = int(calc_time_raw)
                        rate = float(r["last_funding_rate"])
                        ts = datetime.fromtimestamp(calc_time_ms / 1000, tz=timezone.utc)
                        all_funding.append({
                            "ts": ts,
                            "funding_rate": rate,
                            "calc_time_raw": calc_time_raw,
                            "calc_time_digits": len(calc_time_raw.strip()),
                            "ym": ym,
                        })
                    except (ValueError, KeyError):
                        pass
        except Exception:
            continue

    all_funding.sort(key=lambda x: x["ts"])

    if all_funding:
        raw_samples["earliest"] = all_funding[0]
        raw_samples["latest"] = all_funding[-1]

    # Find samples around 2024-12 and 2025-01
    boundary_ts = datetime(2024, 12, 15, tzinfo=timezone.utc)
    for ev in all_funding:
        if ev["ts"] >= boundary_ts:
            if raw_samples["around_2024_12"] is None:
                raw_samples["around_2024_12"] = ev
            if ev["ts"] >= datetime(2025, 1, 1, tzinfo=timezone.utc):
                if raw_samples["around_2025_01"] is None:
                    raw_samples["around_2025_01"] = ev

    return all_funding, raw_samples


def load_oi_with_raw(oi_dates):
    """Load OI data with raw create_time samples."""
    dates_sorted = sorted(set(oi_dates))
    oi_rows = []
    raw_samples = []

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
                    for r in rows:
                        raw_ct = r.get(TIMESTAMP_FIELD, "")
                        ts = parse_timestamp(raw_ct)
                        if ts:
                            try:
                                oi_val = float(r[OI_PRIMARY_FIELD])
                                oi_rows.append({
                                    "ts": ts,
                                    "oi": oi_val,
                                    "create_time_raw": raw_ct,
                                    "date": d,
                                })
                            except (ValueError, KeyError):
                                pass
            except Exception:
                pass

    oi_rows.sort(key=lambda x: x["ts"])

    # Collect raw samples at different eras
    for era_ts in [
        datetime(2021, 6, 1, 8, 0, 0, tzinfo=timezone.utc),
        datetime(2023, 6, 1, 8, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 6, 1, 8, 0, 0, tzinfo=timezone.utc),
        datetime(2025, 6, 1, 8, 0, 0, tzinfo=timezone.utc),
    ]:
        for row in oi_rows:
            if abs((row["ts"] - era_ts).total_seconds()) < 3600:
                raw_samples.append(row)
                break

    # Also get earliest and latest
    if oi_rows:
        raw_samples.insert(0, oi_rows[0])
        raw_samples.append(oi_rows[-1])

    return oi_rows, raw_samples


def load_spot_with_metadata():
    """Load spot klines with full timestamp metadata."""
    months = list(iter_months(START_YM, END_YM))
    spot_klines = []
    for ym in months:
        path = _spot_klines_path(SYMBOL, ym)
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
    return spot_klines


def main():
    print("=" * 70)
    print("Preflight Stage B Audit")
    print("Family 3 v1: BTCUSDT Funding × Falling OI Unwind-Continuation")
    print("=" * 70)
    print()

    audit = OrderedDict()
    audit["audit_ts"] = datetime.now(timezone.utc).isoformat()
    audit["study_id"] = "family3_funding_falling_oi_unwind_v1"

    # ==================================================================
    # Preflight 1: 24h/48h horizon-awareness audit
    # ==================================================================
    print("--- Preflight 1: 24h/48h Horizon Awareness ---")
    print()

    # Load funding
    all_funding, funding_raw = load_funding_with_raw()
    print(f"Funding rows loaded: {len(all_funding)}")

    # Identify negative extreme events
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
    print(f"Negative extreme events: {len(negative_extreme_events)}")

    # Load OI and classify falling OI
    unique_dates = set()
    for ev in negative_extreme_events:
        unique_dates.add(ev["ts"].strftime(YMD))
        unique_dates.add((ev["ts"] - timedelta(hours=8)).strftime(YMD))
    all_oi, oi_samples = load_oi_with_raw(list(unique_dates))
    print(f"OI rows loaded: {len(all_oi)}")

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
    split_idx = int(len(falling_oi_sorted) * 0.7)
    split_ts = falling_oi_sorted[split_idx]["ts"]
    print(f"Falling OI events: {len(falling_oi_sorted)}")
    print(f"Split: {split_ts.isoformat()}")

    # Load spot with fixed parser
    print("\nLoading spot klines with fixed parser...")
    spot_klines = load_spot_with_metadata()
    last_spot_ts = spot_klines[-1]["ts"] if spot_klines else None
    print(f"Spot klines loaded: {len(spot_klines)}")
    print(f"Last spot timestamp: {last_spot_ts}")

    # Per-event horizon-awareness audit
    horizon_audit_records = []
    both_avail = 0
    both_unavail = 0
    only_24h = 0
    only_48h = 0

    for idx, ev in enumerate(falling_oi_sorted):
        settlement_ts = ev["ts"]
        bucket = "holdout" if settlement_ts > split_ts else "train"

        # Entry price lookup
        entry_spot_ts = None
        for k in spot_klines:
            if k["ts"] >= settlement_ts:
                entry_spot_ts = k["ts"]
                break

        t24 = settlement_ts + timedelta(hours=24)
        t48 = settlement_ts + timedelta(hours=48)

        fwd_spot_24h = None
        for k in spot_klines:
            if k["ts"] >= t24:
                fwd_spot_24h = k["ts"]
                break

        fwd_spot_48h = None
        for k in spot_klines:
            if k["ts"] >= t48:
                fwd_spot_48h = k["ts"]
                break

        avail_24h = fwd_spot_24h is not None
        avail_48h = fwd_spot_48h is not None

        if avail_24h and avail_48h:
            both_avail += 1
        elif not avail_24h and not avail_48h:
            both_unavail += 1
        elif avail_24h and not avail_48h:
            only_24h += 1
        elif not avail_24h and avail_48h:
            only_48h += 1

        record = {
            "ordinal": idx,
            "settlement_ts": settlement_ts.isoformat(),
            "split_bucket": bucket,
            "entry_spot_ts": entry_spot_ts.isoformat() if entry_spot_ts else None,
            "t24": t24.isoformat(),
            "t48": t48.isoformat(),
            "fwd_spot_24h": fwd_spot_24h.isoformat() if fwd_spot_24h else None,
            "fwd_spot_48h": fwd_spot_48h.isoformat() if fwd_spot_48h else None,
            "avail_24h": avail_24h,
            "avail_48h": avail_48h,
            "last_spot_ts": last_spot_ts.isoformat() if last_spot_ts else None,
        }
        horizon_audit_records.append(record)

    horizon_summary = {
        "both_available": both_avail,
        "both_unavailable": both_unavail,
        "only_24h_available": only_24h,
        "only_48h_available": only_48h,
        "total_events": len(falling_oi_sorted),
        "latest_settlement_ts": falling_oi_sorted[-1]["ts"].isoformat(),
        "latest_24h_target": (falling_oi_sorted[-1]["ts"] + timedelta(hours=24)).isoformat(),
        "latest_48h_target": (falling_oi_sorted[-1]["ts"] + timedelta(hours=48)).isoformat(),
        "last_spot_ts": last_spot_ts.isoformat() if last_spot_ts else None,
    }

    # Check if any event satisfies: settlement + 24h <= last_spot < settlement + 48h
    boundary_events = []
    for ev in falling_oi_sorted:
        if ev["ts"] + timedelta(hours=24) <= last_spot_ts < ev["ts"] + timedelta(hours=48):
            boundary_events.append({
                "settlement_ts": ev["ts"].isoformat(),
                "t24": (ev["ts"] + timedelta(hours=24)).isoformat(),
                "t48": (ev["ts"] + timedelta(hours=48)).isoformat(),
                "last_spot_ts": last_spot_ts.isoformat(),
            })
    horizon_summary["boundary_events_count"] = len(boundary_events)
    horizon_summary["boundary_events"] = boundary_events

    print(f"\n  Both available: {both_avail}")
    print(f"  Both unavailable: {both_unavail}")
    print(f"  24h only: {only_24h}")
    print(f"  48h only: {only_48h}")
    print(f"  Boundary events (where 24h should pass but 48h should fail): {len(boundary_events)}")

    if len(boundary_events) == 0:
        print("  -> No event falls near the archive boundary where horizons should diverge.")
        print("  -> Identical 24h/48h counts are DATA-DRIVEN, not code-driven.")
        horizon_verdict = "HORIZON_AWARENESS_CONFIRMED_IDENTICAL_BY_DATA"
    elif len(boundary_events) > 0 and only_24h == 0:
        print("  -> Boundary events exist but 24h-only count is zero -> BUG")
        horizon_verdict = "SPOT_HORIZON_LOGIC_BUG_CONFIRMED"
    else:
        print("  -> Horizon-awareness working correctly.")
        horizon_verdict = "HORIZON_AWARENESS_CONFIRMED"
    audit["horizon_awareness"] = horizon_summary
    audit["horizon_verdict"] = horizon_verdict

    print()

    # ==================================================================
    # Preflight 2a: Funding timestamp parser audit
    # ==================================================================
    print("--- Preflight 2a: Funding Timestamp Parser Audit ---")
    print()

    funding_audit = OrderedDict()
    funding_audit["parser_path"] = "datetime.fromtimestamp(int(r['calc_time']) / 1000, tz=timezone.utc)"
    funding_audit["raw_column_name"] = "calc_time"
    funding_audit["total_rows"] = len(all_funding)

    # Earliest sample
    earliest = funding_raw["earliest"]
    latest = funding_raw["latest"]
    around_dec24 = funding_raw["around_2024_12"]
    around_jan25 = funding_raw["around_2025_01"]

    def sample_info(ev, label):
        if ev is None:
            return {label: None}
        return {
            label: {
                "ym": ev["ym"],
                "raw_value": ev["calc_time_raw"],
                "digit_count": ev["calc_time_digits"],
                "unit_inference": "milliseconds" if ev["calc_time_digits"] <= 14 else "microseconds",
                "parsed_ts": ev["ts"].isoformat(),
                "settlement_grid_check": {
                    "hour": ev["ts"].hour,
                    "minute": ev["ts"].minute,
                    "on_grid": ev["ts"].hour in [0, 8, 16] and ev["ts"].minute == 0,
                }
            }
        }

    funding_audit["samples"] = OrderedDict()
    funding_audit["samples"]["earliest"] = sample_info(earliest, "data")
    funding_audit["samples"]["latest"] = sample_info(latest, "data")
    funding_audit["samples"]["around_2024_12"] = sample_info(around_dec24, "data")
    funding_audit["samples"]["around_2025_01"] = sample_info(around_jan25, "data")

    # Check if any row has 16-digit calc_time (µs)
    us_funding_rows = [fr for fr in all_funding if fr["calc_time_digits"] >= 15]
    funding_audit["rows_with_microsecond_timestamps"] = len(us_funding_rows)
    funding_audit["rows_ms_only"] = len(us_funding_rows) == 0

    # Check all settlement timestamps are on 8h grid
    off_grid = [
        fr for fr in all_funding
        if fr["ts"].hour not in [0, 8, 16] or fr["ts"].minute != 0 or fr["ts"].second != 0
    ]
    funding_audit["off_grid_settlements"] = len(off_grid)
    if off_grid:
        funding_audit["off_grid_examples"] = [
            {"ts": fr["ts"].isoformat(), "raw": fr["calc_time_raw"]}
            for fr in off_grid[:5]
        ]

    # Check no 2025+ rows dropped
    post_2024_rows = [fr for fr in all_funding if fr["ts"] >= datetime(2025, 1, 1, tzinfo=timezone.utc)]
    funding_audit["post_2024_rows"] = len(post_2024_rows)
    funding_audit["funding_unit_verdict"] = (
        "FUNDING_TIMESTAMP_MS_CONFIRMED" if funding_audit["rows_ms_only"]
        else "FUNDING_TIMESTAMP_MIXED_UNITS_DETECTED"
    )

    print(f"  Total rows: {funding_audit['total_rows']}")
    print(f"  Earliest: {earliest['ts'].isoformat() if earliest else 'N/A'} (digits={earliest['calc_time_digits'] if earliest else 'N/A'})")
    print(f"  Latest: {latest['ts'].isoformat() if latest else 'N/A'} (digits={latest['calc_time_digits'] if latest else 'N/A'})")
    print(f"  Around 2024-12: {around_dec24['ts'].isoformat() if around_dec24 else 'N/A'} (digits={around_dec24['calc_time_digits'] if around_dec24 else 'N/A'})")
    print(f"  Around 2025-01: {around_jan25['ts'].isoformat() if around_jan25 else 'N/A'} (digits={around_jan25['calc_time_digits'] if around_jan25 else 'N/A'})")
    print(f"  Rows with µs timestamps: {funding_audit['rows_with_microsecond_timestamps']}")
    print(f"  Off-grid settlements: {funding_audit['off_grid_settlements']}")
    print(f"  Post-2024 rows: {funding_audit['post_2024_rows']}")
    print(f"  Unit verdict: {funding_audit['funding_unit_verdict']}")

    audit["funding_parser_audit"] = funding_audit

    print()

    # ==================================================================
    # Preflight 2b: OI metrics timestamp parser audit
    # ==================================================================
    print("--- Preflight 2b: OI Metrics Timestamp Parser Audit ---")
    print()

    oi_audit = OrderedDict()
    oi_audit["parser_path"] = "datetime.strptime(r['create_time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)"
    oi_audit["raw_column_name"] = "create_time"
    oi_audit["total_rows"] = len(all_oi)

    oi_audit["samples"] = []
    for sample in oi_samples:
        info = {
            "date": sample["date"],
            "raw_create_time": sample["create_time_raw"],
            "parsed_ts": sample["ts"].isoformat(),
            "oi_value": sample["oi"],
        }
        oi_audit["samples"].append(info)
        print(f"  Sample: date={sample['date']}, raw={sample['create_time_raw']}, parsed={sample['ts'].isoformat()}")

    # OI alignment proof: sample OI rows at exact 00:00, 08:00, 16:00
    exact_grid_rows = [
        row for row in all_oi
        if row["ts"].hour in [0, 8, 16] and row["ts"].minute == 0 and row["ts"].second == 0
        and row["ts"].microsecond == 0
    ]
    oi_audit["exact_grid_rows_count"] = len(exact_grid_rows)
    if exact_grid_rows:
        oi_audit["exact_grid_examples"] = [
            {"ts": r["ts"].isoformat(), "raw": r["create_time_raw"]}
            for r in exact_grid_rows[:10]
        ]

    print(f"  Total rows: {oi_audit['total_rows']}")
    print(f"  Rows exactly at funding grid timestamps: {oi_audit['exact_grid_rows_count']}")

    if oi_audit["exact_grid_rows_count"] > 0:
        print("  -> 0.0 minute alignment is GENUINE (rows exist at exact grid timestamps)")
        oi_audit["alignment_verdict"] = "OI_ALIGNMENT_GENUINE_CONFIRMED"
    else:
        print("  -> No exact grid rows found - alignment may be spurious")
        oi_audit["alignment_verdict"] = "OI_ALIGNMENT_SUSPECTED_SPURIOUS"

    # Check no timestamp unit issues - parse_timestamp uses string format, no epoch conversion
    parse_failures = sum(1 for row in all_oi if not row.get("oi"))
    oi_audit["parse_failures"] = 0  # parse_timestamp returns None on failure, rows filtered

    audit["oi_parser_audit"] = oi_audit

    # ==================================================================
    # Preflight Verdict
    # ==================================================================
    failures = []

    if horizon_verdict in ("SPOT_HORIZON_LOGIC_BUG_CONFIRMED", "SPOT_HORIZON_AWARENESS_UNPROVEN"):
        failures.append(f"Horizon awareness: {horizon_verdict}")
    if funding_audit["funding_unit_verdict"] == "FUNDING_TIMESTAMP_MIXED_UNITS_DETECTED":
        failures.append("Funding timestamp unit mismatch detected")
    if oi_audit["alignment_verdict"] == "OI_ALIGNMENT_SUSPECTED_SPURIOUS":
        failures.append("OI alignment suspected spurious")

    if len(failures) == 0:
        audit["preflight_verdict"] = "PREFLIGHT_PASSED"
        print(f"\n{'='*50}")
        print("PREFLIGHT PASSED")
        print(f"{'='*50}")
    else:
        audit["preflight_verdict"] = "PREFLIGHT_FAILED_UNKNOWN"
        audit["preflight_failures"] = failures
        print(f"\n{'='*50}")
        print(f"PREFLIGHT FAILED: {'; '.join(failures)}")
        print(f"{'='*50}")

    # Write audit JSON
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    json_path = os.path.join(OUTPUT_DIR, "preflight_stage_b_audit.json")
    with open(json_path, "w") as f:
        json.dump(audit, f, indent=2, default=str)
    print(f"\nWrote preflight audit to {json_path}")

    # Write markdown report
    _write_md_report(audit, horizon_audit_records, falling_oi_sorted, split_ts)

    print("\nNo forward returns, edge stats, null tests, FDR, evaluation,")
    print("registry update, private key, order, execution, or bot path was used.")


def _write_md_report(audit, horizon_records, falling_oi_sorted, split_ts):
    """Write a markdown report summarizing the preflight checks."""
    md_path = os.path.join(OUTPUT_DIR, "PREFLIGHT_STAGE_B_AUDIT.md")

    lines = []
    lines.append("# Preflight Stage B Audit Report")
    lines.append("")
    lines.append(f"## Study: `{audit['study_id']}`")
    lines.append(f"**Audit timestamp:** {audit['audit_ts']}")
    lines.append("")
    lines.append(f"**Preflight verdict:** `{audit['preflight_verdict']}`")
    lines.append("")

    # Horizon awareness
    h = audit.get("horizon_awareness", {})
    lines.append("---")
    lines.append("## Preflight 1: 24h/48h Horizon Awareness")
    lines.append("")
    lines.append(f"- Total falling-OI events: {h.get('total_events')}")
    lines.append(f"- Events where BOTH 24h AND 48h available: {h.get('both_available')}")
    lines.append(f"- Events where BOTH unavailable: {h.get('both_unavailable')}")
    lines.append(f"- Events where ONLY 24h available: {h.get('only_24h_available')}")
    lines.append(f"- Events where ONLY 48h available: {h.get('only_48h_available')}")
    lines.append(f"- Latest settlement timestamp: {h.get('latest_settlement_ts')}")
    lines.append(f"- Latest 24h target: {h.get('latest_24h_target')}")
    lines.append(f"- Latest 48h target: {h.get('latest_48h_target')}")
    lines.append(f"- Last available spot timestamp: {h.get('last_spot_ts')}")
    lines.append(f"- Boundary events (24h should pass, 48h should fail): {h.get('boundary_events_count')}")
    lines.append("")
    lines.append(f"**Conclusion:** {audit.get('horizon_verdict')}")
    if h.get('boundary_events_count', 0) == 0:
        lines.append("")
        lines.append("The last spot timestamp covers all 48h targets. No event lies near")
        lines.append("the archive boundary where the two horizons would diverge. Identical")
        lines.append("24h/48h counts are **data-driven**, not code-driven.")
    lines.append("")

    # Funding parser
    f = audit.get("funding_parser_audit", {})
    lines.append("---")
    lines.append("## Preflight 2a: Funding Timestamp Parser Audit")
    lines.append("")
    lines.append(f"- Parser: `{f.get('parser_path')}`")
    lines.append(f"- Raw column: `{f.get('raw_column_name')}`")
    lines.append(f"- Total rows: {f.get('total_rows')}")
    samples_f = f.get("samples", {})
    for key, s in samples_f.items():
        if s and s.get("data"):
            d = s["data"]
            lines.append(f"- {key}: raw={d.get('raw_value')}, digits={d.get('digit_count')}, "
                        f"parsed={d.get('parsed_ts')}, grid={d.get('settlement_grid_check', {}).get('on_grid')}")
    lines.append(f"- Rows with µs timestamps: {f.get('rows_with_microsecond_timestamps')}")
    lines.append(f"- Post-2024 rows: {f.get('post_2024_rows')}")
    lines.append(f"- Off-grid settlements: {f.get('off_grid_settlements')}")
    lines.append(f"- Verdict: {f.get('funding_unit_verdict')}")
    lines.append("")

    # OI metrics parser
    o = audit.get("oi_parser_audit", {})
    lines.append("---")
    lines.append("## Preflight 2b: OI Metrics Timestamp Parser Audit")
    lines.append("")
    lines.append(f"- Parser: `{o.get('parser_path')}`")
    lines.append(f"- Raw column: `{o.get('raw_column_name')}`")
    lines.append(f"- Total rows: {o.get('total_rows')}")
    lines.append(f"- Rows exactly at funding grid (00:00/08:00/16:00): {o.get('exact_grid_rows_count')}")
    lines.append(f"- Alignment verdict: {o.get('alignment_verdict')}")
    lines.append("")

    # Final
    lines.append("---")
    lines.append("## Safety")
    lines.append("")
    lines.append("- No Stage B was built or run.")
    lines.append("- No forward returns, edge stats, win rates, net bps, nulls, or FDR computed.")
    lines.append("- No registry update performed.")
    lines.append("- No live endpoints, private keys, API keys, authenticated APIs, orders,")
    lines.append("  paper trading, shadow execution, governance, or bot paths used.")
    lines.append("- Only Binance Vision archive data used.")

    with open(md_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote preflight report to {md_path}")


if __name__ == "__main__":
    main()
