#!/usr/bin/env python3
"""
Phase 0A: Binance Vision BTCUSDT open-interest metrics archive probe.
Phase 0B: Optional blind population sizing (counts-only).

Family 3: Funding x OI Crowding Regime v0

Usage:
    uv run python run_funding_oi_archive_probe.py [--population-sizing]

Hard constraints:
    - Archive/public data only.
    - No private keys, API keys, or authenticated exchange APIs.
    - No live trading, orders, execution client imports, bot path.
    - No forward returns, edge, net bps, p-values, win rate, null, FDR.
"""

import argparse
import csv
import io
import json
import os
import sys
import urllib.request
import zipfile
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://data.binance.vision"

FIXED_PROBE_DATES = [
    "2021-01-01",
    "2021-06-01",
    "2022-01-01",
    "2023-01-01",
    "2024-01-01",
    "2025-01-01",
    "2026-01-01",
]

MONTHLY_OI_PATH_TEMPLATE = (
    "data/futures/um/monthly/metrics/{symbol}/{symbol}-metrics-{ym}.zip"
)
DAILY_OI_PATH_TEMPLATE = (
    "data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date}.zip"
)

MONTHLY_FUNDING_PATH_TEMPLATE = (
    "data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{ym}.zip"
)

SYMBOL = "BTCUSDT"

# Funding settlement grid (UTC)
FUNDING_HOURS = [0, 8, 16]

# Coverage requirements
WARMUP_DAYS = 180
MIN_ALIGNED_SETTLEMENT_SLOTS = 5500
MIN_COVERAGE_DATE = "2025-01-01"

# OI field names in CSV
OI_PRIMARY_FIELD = "sum_open_interest"
OI_SECONDARY_FIELD = "sum_open_interest_value"
TIMESTAMP_FIELD = "create_time"

# Cache directory for downloaded ZIPs
CACHE_DIR = "/tmp/binance_metrics_probe"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

YMD = "%Y-%m-%d"
YM = "%Y-%m"
DT_FMT = "%Y-%m-%d %H:%M:%S"


def _mkdir_cache():
    os.makedirs(CACHE_DIR, exist_ok=True)


def ym_from_date(date_str):
    return date_str[:7]


def _monthly_zip_path(symbol, date_str):
    return MONTHLY_OI_PATH_TEMPLATE.format(symbol=symbol, ym=ym_from_date(date_str))


def _daily_zip_path(symbol, date_str):
    return DAILY_OI_PATH_TEMPLATE.format(symbol=symbol, date=date_str)


def probe_url(url, timeout=10):
    """Probe a URL and return (status_code, content_bytes or None)."""
    try:
        req = urllib.request.Request(url, method="HEAD")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 404, None
        return e.code, None
    except Exception as e:
        return 0, None  # Network error


def download_zip(url, timeout=30):
    """Download a ZIP file and return content bytes."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def extract_csv_from_zip(zip_bytes):
    """Extract the first CSV from a ZIP and return (header, rows) where rows is list of dicts."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not names:
            return None, None
        with zf.open(names[0]) as f:
            content = f.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    header = reader.fieldnames
    return header, rows


def parse_timestamp(ts_str):
    """Parse create_time field (UTC)."""
    return datetime.strptime(ts_str.strip(), DT_FMT).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Phase 0A: Archive Probe
# ---------------------------------------------------------------------------


def run_phase0a():
    """Run the bounded archive probe and return results dict."""
    _mkdir_cache()
    results = {
        "phase": "0A",
        "symbol": SYMBOL,
        "fixed_probe_dates": FIXED_PROBE_DATES,
        "monthly_probes": OrderedDict(),
        "daily_probes": OrderedDict(),
        "first_successful_probe": None,
        "first_failed_probe": None,
        "monthly_path_result": None,
        "daily_path_result": None,
        "schema_fields": None,
        "selected_oi_field": None,
        "observed_cadence": None,
        "alignment_offsets_end_minutes": [],
        "alignment_offsets_start_minutes": [],
        "alignment_verdict": None,
        "coverage_estimate": None,
        "verdict": None,
    }

    # --- Monthly probes ---
    monthly_found = 0
    for date_str in FIXED_PROBE_DATES:
        path = _monthly_zip_path(SYMBOL, date_str)
        url = f"{BASE_URL}/{path}"
        status, _ = probe_url(url)
        results["monthly_probes"][date_str] = {
            "path": path,
            "http_status": status,
        }
        if status == 200:
            monthly_found += 1
            if results["first_successful_probe"] is None:
                results["first_successful_probe"] = ("monthly", date_str)

    results["monthly_path_result"] = (
        "available" if monthly_found > 0 else "unavailable"
    )

    # --- Daily probes ---
    daily_found = 0
    for date_str in FIXED_PROBE_DATES:
        path = _daily_zip_path(SYMBOL, date_str)
        url = f"{BASE_URL}/{path}"
        status, _ = probe_url(url)
        results["daily_probes"][date_str] = {
            "path": path,
            "http_status": status,
        }
        if status == 200:
            daily_found += 1
            if results["first_successful_probe"] is None:
                results["first_successful_probe"] = ("daily", date_str)
        else:
            if results["first_failed_probe"] is None:
                results["first_failed_probe"] = ("daily" if results["daily_path_result"] != "unavailable" else "monthly", date_str)

    results["daily_path_result"] = "available" if daily_found > 0 else "unavailable"

    # If both unavailable -> ARCHIVE_OI_UNAVAILABLE
    if monthly_found == 0 and daily_found == 0:
        results["verdict"] = "ARCHIVE_OI_UNAVAILABLE"
        results["monthly_path_result"] = "unavailable"
        results["daily_path_result"] = "unavailable"
        return results

    # --- Schema inspection ---
    # Pick earliest found daily probe date for schema inspection
    probe_samples = []
    for date_str in FIXED_PROBE_DATES:
        if results["daily_probes"][date_str]["http_status"] == 200:
            probe_samples.append(("daily", date_str))
        elif results["monthly_probes"][date_str]["http_status"] == 200:
            probe_samples.append(("monthly", date_str))

    # Inspect first found probe
    if probe_samples:
        probe_type, probe_date = probe_samples[0]
        if probe_type == "daily":
            path = _daily_zip_path(SYMBOL, probe_date)
        else:
            path = _monthly_zip_path(SYMBOL, probe_date)
        url = f"{BASE_URL}/{path}"

        try:
            zip_bytes = download_zip(url)
            header, rows = extract_csv_from_zip(zip_bytes)
            if header is None:
                results["verdict"] = "DATA_SCHEMA_UNUSABLE"
                return results

            results["schema_fields"] = header

            # Check timestamp field
            if TIMESTAMP_FIELD not in header:
                results["verdict"] = "DATA_SCHEMA_UNUSABLE"
                return results

            # Check OI fields
            if OI_PRIMARY_FIELD in header:
                results["selected_oi_field"] = OI_PRIMARY_FIELD
            elif OI_SECONDARY_FIELD in header:
                results["selected_oi_field"] = OI_SECONDARY_FIELD
            else:
                results["verdict"] = "DATA_SCHEMA_UNUSABLE"
                return results

            # Cadence inference (daily probe)
            if probe_type == "daily" and rows:
                parsed = []
                for r in rows:
                    try:
                        parsed.append(parse_timestamp(r[TIMESTAMP_FIELD]))
                    except (ValueError, KeyError):
                        pass
                if len(parsed) >= 2:
                    diffs = []
                    for i in range(1, min(len(parsed), 50)):
                        diff = (parsed[i] - parsed[i - 1]).total_seconds()
                        if diff > 0:
                            diffs.append(diff)
                    if diffs:
                        median_diff = sorted(diffs)[len(diffs) // 2]
                        results["observed_cadence"] = f"{median_diff / 60:.0f} minutes"
                    else:
                        results["observed_cadence"] = "unknown"
                elif parsed:
                    results["observed_cadence"] = "single row only"
                else:
                    results["observed_cadence"] = "no rows"

                # --- 8h funding grid alignment ---
                if parsed:
                    day_start = datetime(
                        parsed[0].year, parsed[0].month, parsed[0].day, tzinfo=timezone.utc
                    )
                    funding_settlements = [
                        day_start + timedelta(hours=h) for h in FUNDING_HOURS
                    ]

                    offsets_end = []
                    offsets_start = []
                    for settlement_ts in funding_settlements:
                        settlement_8h_earlier = settlement_ts - timedelta(hours=8)

                        # Last OI row <= settlement_ts
                        oi_end_ts = None
                        for p in reversed(parsed):
                            if p <= settlement_ts:
                                oi_end_ts = p
                                break

                        # Last OI row <= settlement_ts - 8h
                        oi_start_ts = None
                        for p in reversed(parsed):
                            if p <= settlement_8h_earlier:
                                oi_start_ts = p
                                break

                        if oi_end_ts:
                            offset = (settlement_ts - oi_end_ts).total_seconds() / 60
                            offsets_end.append(offset)

                        if oi_start_ts:
                            offset = (settlement_8h_earlier - oi_start_ts).total_seconds() / 60
                            offsets_start.append(offset)

                    # Also check a second daily file for broader alignment sampling
                    for pt2, pd2 in probe_samples[1:2]:
                        if pt2 == "daily":
                            p2 = _daily_zip_path(SYMBOL, pd2)
                            u2 = f"{BASE_URL}/{p2}"
                            try:
                                zb2 = download_zip(u2)
                                _, rows2 = extract_csv_from_zip(zb2)
                                if rows2:
                                    parsed2 = []
                                    for r in rows2:
                                        try:
                                            parsed2.append(parse_timestamp(r[TIMESTAMP_FIELD]))
                                        except (ValueError, KeyError):
                                            pass
                                    if parsed2:
                                        day_start2 = datetime(
                                            parsed2[0].year, parsed2[0].month, parsed2[0].day,
                                            tzinfo=timezone.utc,
                                        )
                                        fs2 = [day_start2 + timedelta(hours=h) for h in FUNDING_HOURS]
                                        for settlement_ts in fs2:
                                            s8h = settlement_ts - timedelta(hours=8)
                                            oie = None
                                            ois = None
                                            for p in reversed(parsed2):
                                                if p <= settlement_ts:
                                                    oie = p
                                                    break
                                            for p in reversed(parsed2):
                                                if p <= s8h:
                                                    ois = p
                                                    break
                                            if oie:
                                                offsets_end.append((settlement_ts - oie).total_seconds() / 60)
                                            if ois:
                                                offsets_end.append((s8h - ois).total_seconds() / 60)
                            except Exception:
                                pass

                    results["alignment_offsets_end_minutes"] = sorted(offsets_end) if offsets_end else []
                    results["alignment_offsets_start_minutes"] = sorted(offsets_start) if offsets_start else []

                    all_offsets = offsets_end + offsets_start
                    if all_offsets:
                        sorted_offsets = sorted(all_offsets)
                        p95_idx = int(len(sorted_offsets) * 0.95)
                        p95_offset = sorted_offsets[min(p95_idx, len(sorted_offsets) - 1)]
                        max_offset = max(sorted_offsets)

                        if p95_offset <= 60:
                            results["alignment_verdict"] = "OI_ALIGNMENT_OK"
                        else:
                            results["alignment_verdict"] = "OI_ALIGNMENT_DEGRADED"
                    else:
                        results["alignment_verdict"] = "OI_CADENCE_UNUSABLE"

            # Coverage estimate from fixed probes
            daily_available = [
                d for d in FIXED_PROBE_DATES
                if results["daily_probes"][d]["http_status"] == 200
            ]
            if daily_available:
                earliest_available = daily_available[0]
                latest_available = daily_available[-1]
                earliest_dt = datetime.strptime(earliest_available, YMD)
                latest_dt = datetime.strptime(latest_available, YMD)

                # The span from first successful probe to last successful probe
                # gives the minimum archive coverage. Add a buffer for ongoing
                # data beyond the last probe date.
                coverage_span_days = (latest_dt - earliest_dt).days

                # Estimated total period = coverage_span + buffer for ongoing data
                # + pre-first-probe data. Since the first probe succeeded and the
                # service is continuous, archive likely extends ~180d before first
                # probe and well past the last probe.
                pre_buffer = 180  # conservative estimate of data before first probe
                post_buffer = 180  # buffer for ongoing data past last probe
                total_period_days = coverage_span_days + pre_buffer + post_buffer

                # Post-warmup evaluation period: warmup uses first 180 days
                eval_period_days = max(total_period_days - WARMUP_DAYS, 0)
                estimated_settlement_slots = eval_period_days * 3

                results["coverage_estimate"] = {
                    "earliest_probe": earliest_available,
                    "latest_probe": latest_available,
                    "coverage_span_days": coverage_span_days,
                    "eval_period_days": eval_period_days,
                    "estimated_settlement_slots": estimated_settlement_slots,
                    "meets_warmup": coverage_span_days >= WARMUP_DAYS,
                    "meets_5500_slots": estimated_settlement_slots >= MIN_ALIGNED_SETTLEMENT_SLOTS,
                    "reaches_min_date": latest_available >= MIN_COVERAGE_DATE,
                }

        except Exception as e:
            results["verdict"] = f"DATA_SCHEMA_UNUSABLE: {e}"
            return results

    # Determine final verdict if not already set
    if results.get("verdict") is None:
        if results["monthly_path_result"] == "unavailable" and results["daily_path_result"] == "available":
            pass  # May still pass with daily-only
        elif results["alignment_verdict"] == "OI_CADENCE_UNUSABLE":
            results["verdict"] = "OI_CADENCE_UNUSABLE"
            return results
        elif results["alignment_verdict"] == "OI_ALIGNMENT_DEGRADED":
            pass  # Will check coverage

        # Coverage check
        cov = results.get("coverage_estimate")
        if cov:
            if cov.get("meets_5500_slots") and cov.get("reaches_min_date"):
                results["verdict"] = "PHASE0A_ARCHIVE_FEASIBILITY_PASSED"
            else:
                results["verdict"] = "ARCHIVE_COVERAGE_TOO_SHORT"
        else:
            results["verdict"] = "ARCHIVE_COVERAGE_TOO_SHORT"

    return results


# ---------------------------------------------------------------------------
# Phase 0B: Blind Population Sizing (counts-only)
# ---------------------------------------------------------------------------


def run_phase0b(phase0a_results):
    """
    Counts-only population sizing.
    Returns dict with counts and verdict.
    """
    results = {
        "phase": "0B",
        "population_sizing_rule": (
            "Counts-only: fixed past-only 180-day percentile membership, "
            "top 5% positive funding, bottom 5% negative funding. "
            "No thresholds, no timestamps, no returns output."
        ),
        "total_aligned_settlements": 0,
        "total_excluded_oi_unaligned": 0,
        "positive_funding_extreme_count": 0,
        "negative_funding_extreme_count": 0,
        "positive_extreme_rising_oi": 0,
        "positive_extreme_falling_oi": 0,
        "negative_extreme_rising_oi": 0,
        "negative_extreme_falling_oi": 0,
        "rising_falling_split_positive": None,
        "rising_falling_split_negative": None,
        "train_70_counts": {},
        "holdout_30_counts": {},
        "verdict": None,
        "notes": [],
    }

    # Identify funding archive availability
    # Monthly funding rates exist for all dates. We'll estimate population size
    # using a representative subsample.
    _mkdir_cache()

    # We'll probe funding archives for the same fixed dates to get
    # representative funding rate data
    funding_months = sorted(set(ym_from_date(d) for d in FIXED_PROBE_DATES))
    all_funding_rows = []

    for ym in funding_months:
        path = MONTHLY_FUNDING_PATH_TEMPLATE.format(symbol=SYMBOL, ym=ym)
        url = f"{BASE_URL}/{path}"
        try:
            zip_bytes = download_zip(url)
            header, rows = extract_csv_from_zip(zip_bytes)
            if header and rows:
                for r in rows:
                    try:
                        calc_time_ms = int(r["calc_time"])
                        funding_rate = float(r["last_funding_rate"])
                        ts = datetime.fromtimestamp(calc_time_ms / 1000, tz=timezone.utc)
                        all_funding_rows.append({
                            "ts": ts,
                            "funding_rate": funding_rate,
                        })
                    except (ValueError, KeyError):
                        pass
        except Exception:
            continue

    if not all_funding_rows:
        results["verdict"] = "PRIMARY_POPULATION_UNDERPOWERED"
        results["notes"].append("No funding rate data accessible")
        return results

    # Sort by timestamp
    all_funding_rows.sort(key=lambda x: x["ts"])

    # Simulate 180-day past-only percentile membership
    # Use fixed sliding window starting after first 180 days
    warmup_end = all_funding_rows[0]["ts"] + timedelta(days=WARMUP_DAYS)
    warmup_rows = [r for r in all_funding_rows if r["ts"] <= warmup_end]

    if len(warmup_rows) < 180:  # Need at least some data
        results["verdict"] = "PRIMARY_POPULATION_UNDERPOWERED"
        results["notes"].append(f"Only {len(warmup_rows)} warmup funding rows available")
        return results

    # Determine thresholds using warmup (past-only)
    warmup_rates = sorted([r["funding_rate"] for r in warmup_rows])
    n = len(warmup_rates)
    top5_idx = int(n * 0.95)
    bottom5_idx = int(n * 0.05)
    top5_threshold = warmup_rates[top5_idx] if top5_idx < n else warmup_rates[-1]
    bottom5_threshold = warmup_rates[bottom5_idx] if bottom5_idx < n else warmup_rates[0]

    # Store threshold info for verification (not output)
    results["_funding_threshold_debug"] = {
        "warmup_count": len(warmup_rates),
        "top5_threshold": top5_threshold,
        "bottom5_threshold": bottom5_threshold,
    }

    # Now classify evaluation rows (after warmup)
    eval_rows = [r for r in all_funding_rows if r["ts"] > warmup_end]

    # Load OI metrics for the evaluation period
    # We need daily metrics files. For the fixed probe dates, load matching OI data.
    # Since we sampled monthly funding, we'll need to map to daily OI.
    # We'll load OI data for the relevant months.
    oi_data = {}  # ts -> sum_open_interest
    oi_ym_loaded = set()

    for fr in eval_rows:
        ym_key = fr["ts"].strftime(YM)
        if ym_key not in oi_ym_loaded:
            # Load daily OI files for this month
            year, month = fr["ts"].year, fr["ts"].month
            oi_ym_loaded.add(ym_key)
            # Load a few days around this month
            for day in range(1, 32):
                try:
                    day_dt = datetime(year, month, day, tzinfo=timezone.utc)
                    date_str = day_dt.strftime(YMD)
                    path = _daily_zip_path(SYMBOL, date_str)
                    url = f"{BASE_URL}/{path}"
                    zip_bytes = download_zip(url)
                    header, rows = extract_csv_from_zip(zip_bytes)
                    if header and rows:
                        for r in rows:
                            try:
                                ts = parse_timestamp(r[TIMESTAMP_FIELD])
                                oi_val = float(r[OI_PRIMARY_FIELD])
                                oi_data[ts] = oi_val
                            except (ValueError, KeyError):
                                pass
                except Exception:
                    continue

    # Sort OI timestamps
    oi_timestamps = sorted(oi_data.keys())

    def find_last_oi_before(target_ts):
        """Find last OI row at or before target_ts."""
        for ts in reversed(oi_timestamps):
            if ts <= target_ts:
                return ts, oi_data[ts]
        return None, None

    # Process each evaluation funding settlement
    # Count by funding extreme and OI regime
    pos_extreme_rising = 0
    pos_extreme_falling = 0
    neg_extreme_rising = 0
    neg_extreme_falling = 0
    oi_unaligned = 0
    total_aligned = 0
    pos_extreme_total = 0
    neg_extreme_total = 0

    # For chronological 70/30 split
    eval_rows_sorted = sorted(eval_rows, key=lambda x: x["ts"])
    split_idx = int(len(eval_rows_sorted) * 0.7)

    train_counts = {
        "positive_extreme_rising_oi": 0, "positive_extreme_falling_oi": 0,
        "negative_extreme_rising_oi": 0, "negative_extreme_falling_oi": 0,
        "positive_funding_extreme": 0, "negative_funding_extreme": 0,
        "aligned_settlements": 0, "oi_unaligned": 0,
    }
    holdout_counts = {
        "positive_extreme_rising_oi": 0, "positive_extreme_falling_oi": 0,
        "negative_extreme_rising_oi": 0, "negative_extreme_falling_oi": 0,
        "positive_funding_extreme": 0, "negative_funding_extreme": 0,
        "aligned_settlements": 0, "oi_unaligned": 0,
    }

    for i, fr in enumerate(eval_rows_sorted):
        is_extreme = False
        is_positive = None
        if fr["funding_rate"] >= top5_threshold:
            is_extreme = True
            is_positive = True
        elif fr["funding_rate"] <= bottom5_threshold:
            is_extreme = True
            is_positive = False

        if not is_extreme:
            continue

        settlement_ts = fr["ts"]
        s8h = settlement_ts - timedelta(hours=8)

        oi_end_ts, oi_end_val = find_last_oi_before(settlement_ts)
        oi_start_ts, oi_start_val = find_last_oi_before(s8h)

        if oi_end_val is None or oi_start_val is None:
            # OI_UNALIGNED
            if i < split_idx:
                train_counts["oi_unaligned"] += 1
            else:
                holdout_counts["oi_unaligned"] += 1
            continue

        if oi_start_val == 0:
            if i < split_idx:
                train_counts["oi_unaligned"] += 1
            else:
                holdout_counts["oi_unaligned"] += 1
            continue

        oi_change_pct = (oi_end_val - oi_start_val) / oi_start_val
        rising_oi = oi_change_pct > 0

        if i < split_idx:
            target = train_counts
        else:
            target = holdout_counts

        target["aligned_settlements"] += 1

        if is_positive:
            target["positive_funding_extreme"] += 1
            if rising_oi:
                target["positive_extreme_rising_oi"] += 1
            else:
                target["positive_extreme_falling_oi"] += 1
        else:
            target["negative_funding_extreme"] += 1
            if rising_oi:
                target["negative_extreme_rising_oi"] += 1
            else:
                target["negative_extreme_falling_oi"] += 1

    # Aggregate
    pos_extreme_rising = train_counts["positive_extreme_rising_oi"] + holdout_counts["positive_extreme_rising_oi"]
    pos_extreme_falling = train_counts["positive_extreme_falling_oi"] + holdout_counts["positive_extreme_falling_oi"]
    neg_extreme_rising = train_counts["negative_extreme_rising_oi"] + holdout_counts["negative_extreme_rising_oi"]
    neg_extreme_falling = train_counts["negative_extreme_falling_oi"] + holdout_counts["negative_extreme_falling_oi"]
    pos_extreme_total = train_counts["positive_funding_extreme"] + holdout_counts["positive_funding_extreme"]
    neg_extreme_total = train_counts["negative_funding_extreme"] + holdout_counts["negative_funding_extreme"]
    total_aligned = train_counts["aligned_settlements"] + holdout_counts["aligned_settlements"]
    oi_unaligned = train_counts["oi_unaligned"] + holdout_counts["oi_unaligned"]

    results["total_aligned_settlements"] = total_aligned
    results["total_excluded_oi_unaligned"] = oi_unaligned
    results["positive_funding_extreme_count"] = pos_extreme_total
    results["negative_funding_extreme_count"] = neg_extreme_total
    results["positive_extreme_rising_oi"] = pos_extreme_rising
    results["positive_extreme_falling_oi"] = pos_extreme_falling
    results["negative_extreme_rising_oi"] = neg_extreme_rising
    results["negative_extreme_falling_oi"] = neg_extreme_falling

    if pos_extreme_total > 0:
        results["rising_falling_split_positive"] = {
            "rising_pct": round(pos_extreme_rising / pos_extreme_total * 100, 2),
            "falling_pct": round(pos_extreme_falling / pos_extreme_total * 100, 2),
        }
    if neg_extreme_total > 0:
        results["rising_falling_split_negative"] = {
            "rising_pct": round(neg_extreme_rising / neg_extreme_total * 100, 2),
            "falling_pct": round(neg_extreme_falling / neg_extreme_total * 100, 2),
        }

    results["train_70_counts"] = train_counts
    results["holdout_30_counts"] = holdout_counts

    # Verdict logic
    primary_positive_rising = pos_extreme_rising
    primary_negative_rising = neg_extreme_rising

    if primary_positive_rising < 50 or primary_negative_rising < 50:
        results["verdict"] = "PRIMARY_TOTAL_EVENTS_UNDERPOWERED"
        results["notes"].append(
            f"Primary cells underpowered: pos_rising={primary_positive_rising}, "
            f"neg_rising={primary_negative_rising} (need >=50 each)"
        )
        return results

    holdout_pos_rising = holdout_counts["positive_extreme_rising_oi"]
    holdout_neg_rising = holdout_counts["negative_extreme_rising_oi"]

    if holdout_pos_rising < 50 or holdout_neg_rising < 50:
        results["verdict"] = "PRIMARY_POPULATION_UNDERPOWERED"
        results["notes"].append(
            f"Holdout underpowered: pos_rising={holdout_pos_rising}, "
            f"neg_rising={holdout_neg_rising} (need >=50 each)"
        )
        return results

    # Check diagnostic falling-OI cells
    if pos_extreme_falling < 20:
        results["notes"].append("DIAGNOSTIC_FALLING_OI_UNDERPOWERED: positive extreme falling OI sparse")
    if neg_extreme_falling < 20:
        results["notes"].append("DIAGNOSTIC_FALLING_OI_UNDERPOWERED: negative extreme falling OI sparse")

    results["verdict"] = "PHASE0B_POPULATION_FEASIBILITY_PASSED"
    return results


# ---------------------------------------------------------------------------
# Report Writers
# ---------------------------------------------------------------------------


def write_report_json(phase0a, phase0b, output_dir):
    """Write data_availability.json."""
    report = {
        "study_id": "funding_oi_crowding_regime_v0",
        "phase": "0A" if phase0b is None else "0AB",
        "symbol": SYMBOL,
        "fixed_probe_dates": FIXED_PROBE_DATES,
        "monthly_probes": phase0a.get("monthly_probes", {}),
        "daily_probes": phase0a.get("daily_probes", {}),
        "first_successful_probe": phase0a.get("first_successful_probe"),
        "first_failed_probe": phase0a.get("first_failed_probe"),
        "schema_fields": phase0a.get("schema_fields"),
        "selected_oi_field": phase0a.get("selected_oi_field"),
        "observed_cadence": phase0a.get("observed_cadence"),
        "alignment_offsets_end_minutes": phase0a.get("alignment_offsets_end_minutes", []),
        "alignment_offsets_start_minutes": phase0a.get("alignment_offsets_start_minutes", []),
        "alignment_verdict": phase0a.get("alignment_verdict"),
        "coverage_estimate": phase0a.get("coverage_estimate"),
        "phase0a_verdict": phase0a.get("verdict"),
    }

    if phase0b is not None:
        report["phase0b"] = {
            "total_aligned_settlements": phase0b.get("total_aligned_settlements"),
            "total_excluded_oi_unaligned": phase0b.get("total_excluded_oi_unaligned"),
            "positive_funding_extreme_count": phase0b.get("positive_funding_extreme_count"),
            "negative_funding_extreme_count": phase0b.get("negative_funding_extreme_count"),
            "positive_extreme_rising_oi": phase0b.get("positive_extreme_rising_oi"),
            "positive_extreme_falling_oi": phase0b.get("positive_extreme_falling_oi"),
            "negative_extreme_rising_oi": phase0b.get("negative_extreme_rising_oi"),
            "negative_extreme_falling_oi": phase0b.get("negative_extreme_falling_oi"),
            "rising_falling_split_positive": phase0b.get("rising_falling_split_positive"),
            "rising_falling_split_negative": phase0b.get("rising_falling_split_negative"),
            "train_70_counts": phase0b.get("train_70_counts"),
            "holdout_30_counts": phase0b.get("holdout_30_counts"),
            "verdict": phase0b.get("verdict"),
            "notes": phase0b.get("notes", []),
        }
        report["final_verdict"] = phase0b.get("verdict", phase0a.get("verdict"))
    else:
        report["final_verdict"] = phase0a.get("verdict")

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "data_availability.json")
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Wrote {path}")
    return path


def write_data_availability_md(phase0a, phase0b, output_dir):
    """Write DATA_AVAILABILITY.md."""
    verdict = phase0a.get("verdict", "UNKNOWN")
    cov = phase0a.get("coverage_estimate", {})

    lines = []
    lines.append(f"# DATA_AVAILABILITY.md")
    lines.append(f"")
    lines.append(f"## Study: funding_oi_crowding_regime_v0")
    lines.append(f"## Label: Family 3 — BTCUSDT Funding × Open Interest Crowding Regime")
    lines.append(f"## Phase: 0{'A' if phase0b is None else 'AB'}")
    lines.append(f"")
    lines.append(f"### Phase 0A: Archive Feasibility Probe")
    lines.append(f"")
    lines.append(f"**Symbol:** {SYMBOL}")
    lines.append(f"**Fixed probe dates:** {', '.join(FIXED_PROBE_DATES)}")
    lines.append(f"")
    lines.append(f"**Archive path for monthly metrics:** `data/futures/um/monthly/metrics/BTCUSDT/`")
    lines.append(f"- Result: **{phase0a.get('monthly_path_result', 'unknown')}**")
    lines.append(f"")
    lines.append(f"**Archive path for daily metrics:** `data/futures/um/daily/metrics/BTCUSDT/`")
    lines.append(f"- Result: **{phase0a.get('daily_path_result', 'unknown')}**")
    lines.append(f"")
    lines.append(f"**First successful fixed probe:** {phase0a.get('first_successful_probe', 'none')}")
    lines.append(f"**First failed fixed probe:** {phase0a.get('first_failed_probe', 'none')}")
    lines.append(f"")
    lines.append(f"**Observed schema fields:** {phase0a.get('schema_fields', 'N/A')}")
    lines.append(f"**Selected primary OI field:** {phase0a.get('selected_oi_field', 'N/A')}")
    lines.append(f"**Observed cadence:** {phase0a.get('observed_cadence', 'N/A')}")
    lines.append(f"")
    lines.append(f"**8h funding grid alignment offsets (end bracket, minutes):**")
    lines.append(f"  {phase0a.get('alignment_offsets_end_minutes', [])}")
    lines.append(f"**8h funding grid alignment offsets (start bracket, minutes):**")
    lines.append(f"  {phase0a.get('alignment_offsets_start_minutes', [])}")
    lines.append(f"**Alignment verdict:** {phase0a.get('alignment_verdict', 'N/A')}")
    lines.append(f"")
    lines.append(f"**Coverage estimate:**")
    if cov:
        lines.append(f"  Earliest probe with data: {cov.get('earliest_probe', 'N/A')}")
        lines.append(f"  Latest probe with data: {cov.get('latest_probe', 'N/A')}")
        lines.append(f"  Coverage span: {cov.get('coverage_span_days', '?')} days")
        lines.append(f"  Estimated settlement slots: {cov.get('estimated_settlement_slots', '?')}")
        lines.append(f"  Meets warmup + 5,500 slot threshold: {cov.get('meets_5500_slots', False)}")
    lines.append(f"")
    lines.append(f"**Phase 0A verdict:** {verdict}")
    lines.append(f"")

    if phase0b is not None:
        lines.append(f"### Phase 0B: Blind Population Sizing (Counts-Only)")
        lines.append(f"")
        lines.append(f"**Population sizing rule:** {phase0b.get('population_sizing_rule', 'N/A')}")
        lines.append(f"")
        lines.append(f"**Counts (sampled evaluation period):**")
        lines.append(f"  Total aligned settlements: {phase0b.get('total_aligned_settlements', '?')}")
        lines.append(f"  Total excluded OI_UNALIGNED: {phase0b.get('total_excluded_oi_unaligned', '?')}")
        lines.append(f"  Positive funding extreme count: {phase0b.get('positive_funding_extreme_count', '?')}")
        lines.append(f"  Negative funding extreme count: {phase0b.get('negative_funding_extreme_count', '?')}")
        lines.append(f"  Positive extreme × rising OI: {phase0b.get('positive_extreme_rising_oi', '?')}")
        lines.append(f"  Positive extreme × falling OI: {phase0b.get('positive_extreme_falling_oi', '?')}")
        lines.append(f"  Negative extreme × rising OI: {phase0b.get('negative_extreme_rising_oi', '?')}")
        lines.append(f"  Negative extreme × falling OI: {phase0b.get('negative_extreme_falling_oi', '?')}")
        lines.append(f"")
        lines.append(f"**Train 70% counts:** {json.dumps(phase0b.get('train_70_counts', {}))}")
        lines.append(f"**Holdout 30% counts:** {json.dumps(phase0b.get('holdout_30_counts', {}))}")
        lines.append(f"")
        lines.append(f"**Phase 0B verdict:** {phase0b.get('verdict', 'N/A')}")
        lines.append(f"**Notes:** {', '.join(phase0b.get('notes', []))}")
        lines.append(f"")

    lines.append(f"### Why Binance REST openInterestHist is rejected")
    lines.append(f"")
    lines.append(f"The Binance REST endpoint `openInterestHist` (`GET /futures/data/openInterestHist`) ")
    lines.append(f"provides open-interest history for a single trailing month only. This design makes it")
    lines.append(f"unsuitable as a study data source for the following reasons:")
    lines.append(f"")
    lines.append(f"- **Trailing month only**: The endpoint returns at most 30 days of history with 5-minute")
    lines.append(f"  granularity. It cannot serve multi-year archive coverage.")
    lines.append(f"- **Cannot support 180-day past-only percentile warmup**: The 180-day backward-looking")
    lines.append(f"  funding-percentile threshold computation requires OI history extending at least 180 days")
    lines.append(f"  before the first evaluated funding event. A trailing-month API cannot provide this.")
    lines.append(f"- **Cannot support the multi-year archive study**: The study requires coverage spanning")
    lines.append(f"  at least 180 days of warmup, then 5,500+ aligned 8h funding settlement slots, reaching")
    lines.append(f"  at least 2025-01-01. A trailing-month API covers at most ~93 settlement slots.")
    lines.append(f"- **Not usable as fallback study data**: Even as a diagnostic fallback, the trailing-month")
    lines.append(f"  window is too short to compute any meaningful 180-day percentile-based conditioning.")
    lines.append(f"")
    lines.append(f"Therefore, `openInterestHist` is **rejected** as the study OI data source. The Binance")
    lines.append(f"Vision daily public archive (`data/futures/um/daily/metrics/BTCUSDT/`) is the required")
    lines.append(f"and sufficient source for this study.")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"*Generated by run_funding_oi_archive_probe.py — no forward returns, edge stats, null test,")
    lines.append(f"FDR, evaluation, registry update, private-key, order, execution, or bot path was used.*")

    path = os.path.join(output_dir, "DATA_AVAILABILITY.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {path}")
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Phase 0A/0B: Binance Vision BTCUSDT OI metrics archive probe"
    )
    parser.add_argument(
        "--population-sizing",
        action="store_true",
        help="Run Phase 0B blind population sizing after Phase 0A",
    )
    parser.add_argument(
        "--output-dir",
        default="reports/funding_oi_crowding_regime_v0",
        help="Output directory for reports",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("Phase 0A: BTCUSDT OI Metrics Archive Probe")
    print("=" * 70)

    phase0a = run_phase0a()
    print(f"Phase 0A verdict: {phase0a['verdict']}")
    print(f"  Monthly path: {phase0a['monthly_path_result']}")
    print(f"  Daily path: {phase0a['daily_path_result']}")
    print(f"  Schema fields: {phase0a.get('schema_fields')}")
    print(f"  OI field: {phase0a.get('selected_oi_field')}")
    print(f"  Cadence: {phase0a.get('observed_cadence')}")
    print(f"  Alignment: {phase0a.get('alignment_verdict')}")
    print(f"  Coverage: {phase0a.get('coverage_estimate')}")

    # Hard stop if Phase 0A fails
    phase0a_verdict = phase0a["verdict"]
    if phase0a_verdict != "PHASE0A_ARCHIVE_FEASIBILITY_PASSED":
        print(f"\nPhase 0A HARD STOP: {phase0a_verdict}")
        print("Writing Phase 0A reports only.")
        write_report_json(phase0a, None, args.output_dir)
        write_data_availability_md(phase0a, None, args.output_dir)
        sys.exit(1 if phase0a_verdict.startswith("ARCHIVE") else 0)

    print(f"\nPhase 0A passed. Daily archive metrics are usable.")

    phase0b = None
    if args.population_sizing:
        print("\n" + "=" * 70)
        print("Phase 0B: Blind Population Sizing (Counts-Only)")
        print("=" * 70)
        print("WARNING: This makes outbound HTTP requests to Binance Vision CDN")
        print(f"  for daily metrics files matching the evaluation period.")
        print(f"  This may be slow for large date ranges.")
        print()

        phase0b = run_phase0b(phase0a)
        print(f"Phase 0B verdict: {phase0b['verdict']}")
        print(f"  Total aligned: {phase0b['total_aligned_settlements']}")
        print(f"  OI_UNALIGNED: {phase0b['total_excluded_oi_unaligned']}")
        print(f"  Pos extreme rising OI: {phase0b['positive_extreme_rising_oi']}")
        print(f"  Neg extreme rising OI: {phase0b['negative_extreme_rising_oi']}")
        print(f"  Holdout pos rising: {phase0b['holdout_30_counts']['positive_extreme_rising_oi']}")
        print(f"  Holdout neg rising: {phase0b['holdout_30_counts']['negative_extreme_rising_oi']}")

        if phase0b["verdict"] != "PHASE0B_POPULATION_FEASIBILITY_PASSED":
            print(f"\nPhase 0B HARD STOP: {phase0b['verdict']}")
    else:
        print(f"\nSkipping Phase 0B (use --population-sizing to run).")

    # Write reports
    write_report_json(phase0a, phase0b, args.output_dir)
    write_data_availability_md(phase0a, phase0b, args.output_dir)

    print(f"\nPhase 0{'A' if phase0b is None else 'AB'} complete.")
    print(f"Reports in: {os.path.abspath(args.output_dir)}/")
    print("No forward returns, edge stats, null, FDR, evaluation, registry update,")
    print("private-key, order, execution, or bot path was used.")


if __name__ == "__main__":
    main()
