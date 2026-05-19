#!/usr/bin/env python3
"""
Stage A: Phase 0A archive feasibility + Phase 0B population sizing.

Family 3 v1: funding falling-OI unwind hypothesis.
Study: family3_funding_falling_oi_unwind_v1

Hard constraints:
- Public-data observer only.
- No authentication, API keys, private keys.
- No orders, execution imports, bot path, paper trading.
- No forward returns, edge stats, nulls, FDR, holdout verdicts, registry updates.
- Phase 0B is deterministic (no randomness).
"""

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://data.binance.vision"
SYMBOL = "BTCUSDT"

FIXED_PROBE_DATES = [
    "2020-09-01",
    "2021-01-01",
    "2021-06-01",
    "2022-01-01",
    "2023-01-01",
    "2024-01-01",
]

# Path templates
MONTHLY_FUNDING_PATH_TEMPLATE = (
    "data/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{ym}.zip"
)
DAILY_OI_PATH_TEMPLATE = (
    "data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{date}.zip"
)
MONTHLY_OI_PATH_TEMPLATE = (
    "data/futures/um/monthly/metrics/{symbol}/{symbol}-metrics-{ym}.zip"
)
SPOT_KLINES_MONTHLY_PATH = (
    "data/spot/monthly/klines/{symbol}/1h/{symbol}-1h-{ym}.zip"
)

# Funding settlement grid (UTC)
FUNDING_HOURS = [0, 8, 16]

# Coverage requirements
WARMUP_DAYS = 180
MIN_CELL_EVENTS = 50
MIN_HOLDOUT_EVENTS = 50

# OI field names in CSV
OI_PRIMARY_FIELD = "sum_open_interest"
TIMESTAMP_FIELD = "create_time"

# OI alignment threshold
MAX_ACCEPTABLE_P95_OFFSET_MINUTES = 5

# Cache
CACHE_DIR = "/tmp/binance_metrics_falling_oi"

# Full monthly range for population sizing
START_YM = "2020-09"
END_YM = "2026-01"

# Format constants
YMD = "%Y-%m-%d"
YM = "%Y-%m"
DT_FMT = "%Y-%m-%d %H:%M:%S"

# Output path
OUTPUT_DIR = "reports/funding_falling_oi_unwind_v1"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "phase0_population_report.json")

# Precommitment path
PRECOMMITMENT_PATH = "examples/strategies/venue_agnostic_signal_observer/docs/FUNDING_FALLING_OI_UNWIND_PRECOMMITMENT.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mkdir_cache():
    os.makedirs(CACHE_DIR, exist_ok=True)


def ym_from_date(date_str):
    return date_str[:7]


def _monthly_funding_path(symbol, date_str):
    return MONTHLY_FUNDING_PATH_TEMPLATE.format(
        symbol=symbol, ym=ym_from_date(date_str)
    )


def _daily_oi_path(symbol, date_str):
    return DAILY_OI_PATH_TEMPLATE.format(symbol=symbol, date=date_str)


def _monthly_oi_path(symbol, date_str):
    return MONTHLY_OI_PATH_TEMPLATE.format(symbol=symbol, ym=ym_from_date(date_str))


def _spot_klines_path(symbol, ym):
    return SPOT_KLINES_MONTHLY_PATH.format(symbol=symbol, ym=ym)


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
    except Exception:
        return 0, None  # Network error


def download_zip(url, timeout=30):
    """Download a ZIP file and return content bytes."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def download_zip_cached(url, cache_key, timeout=30):
    """Download with local file cache."""
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
    """Extract the first CSV from a ZIP and return (header, rows)."""
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
    """Parse create_time field (UTC). Returns datetime or None."""
    try:
        return datetime.strptime(ts_str.strip(), DT_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def parse_klines_timestamp(ts_int):
    """
    Parse Binance Vision klines open_time timestamp.

    The archive changed from milliseconds (13-digit, ~2024 and earlier)
    to microseconds (16-digit, 2025+) at the 2025 year boundary.

    Detection: values > 1e14 are microseconds; otherwise milliseconds.
    Returns timezone-aware UTC datetime or None on overflow.
    """
    try:
        if ts_int > 100_000_000_000_000:  # > 1e14 → microseconds
            return datetime.fromtimestamp(ts_int / 1_000_000, tz=timezone.utc)
        else:  # milliseconds (standard)
            return datetime.fromtimestamp(ts_int / 1_000, tz=timezone.utc)
    except (OverflowError, ValueError, OSError):
        return None


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
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        return result.stdout.strip()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Phase 0A: Archive Probe (fixed small date set)
# ---------------------------------------------------------------------------

def run_phase0a():
    """
    Run bounded archive probe.
    Returns dict with fixed probe results, alignment assessment, and verdict.
    """
    _mkdir_cache()
    results = OrderedDict()
    results["phase"] = "0A"
    results["symbol"] = SYMBOL
    results["fixed_probe_dates"] = FIXED_PROBE_DATES
    results["funding_monthly_probes"] = OrderedDict()
    results["oi_daily_probes"] = OrderedDict()
    results["oi_monthly_probes"] = OrderedDict()
    results["spot_probes"] = OrderedDict()

    # --- Funding monthly probes ---
    funding_found = 0
    for date_str in FIXED_PROBE_DATES:
        path = _monthly_funding_path(SYMBOL, date_str)
        url = f"{BASE_URL}/{path}"
        status, _ = probe_url(url)
        results["funding_monthly_probes"][date_str] = {
            "path": path, "http_status": status
        }
        if status == 200:
            funding_found += 1

    funding_available = funding_found > 0
    results["funding_available"] = funding_available

    if not funding_available:
        results["outcome"] = "ARCHIVE_FEASIBILITY_FAILED"
        results["outcome_reason"] = "No funding archive files found at fixed probe dates"
        return results

    # --- OI daily probes ---
    oi_daily_found = 0
    for date_str in FIXED_PROBE_DATES:
        path = _daily_oi_path(SYMBOL, date_str)
        url = f"{BASE_URL}/{path}"
        status, _ = probe_url(url)
        results["oi_daily_probes"][date_str] = {
            "path": path, "http_status": status
        }
        if status == 200:
            oi_daily_found += 1

    # --- OI monthly probes (fallback) ---
    oi_monthly_found = 0
    for date_str in FIXED_PROBE_DATES:
        path = _monthly_oi_path(SYMBOL, date_str)
        url = f"{BASE_URL}/{path}"
        status, _ = probe_url(url)
        results["oi_monthly_probes"][date_str] = {
            "path": path, "http_status": status
        }
        if status == 200:
            oi_monthly_found += 1

    oi_daily_available = oi_daily_found > 0
    oi_monthly_available = oi_monthly_found > 0
    results["oi_daily_available"] = oi_daily_available
    results["oi_monthly_available"] = oi_monthly_available

    if not oi_daily_available and not oi_monthly_available:
        results["outcome"] = "ARCHIVE_FEASIBILITY_FAILED"
        results["outcome_reason"] = "No OI archive files found at fixed probe dates"
        return results

    # --- Spot klines monthly probes ---
    spot_found = 0
    for date_str in FIXED_PROBE_DATES:
        ym = ym_from_date(date_str)
        path = _spot_klines_path(SYMBOL, ym)
        url = f"{BASE_URL}/{path}"
        status, _ = probe_url(url)
        results["spot_probes"][date_str] = {
            "path": path, "http_status": status
        }
        if status == 200:
            spot_found += 1

    spot_available = spot_found > 0
    results["spot_available"] = spot_available

    if not spot_available:
        results["outcome"] = "SPOT_AVAILABILITY_FAILED"
        results["outcome_reason"] = "No spot klines archive found at fixed probe dates"
        return results

    # --- OI schema inspection and alignment assessment ---
    # Use earliest found daily OI probe for schema & cadence
    oi_probe_dates = [
        d for d in FIXED_PROBE_DATES
        if results["oi_daily_probes"].get(d, {}).get("http_status") == 200
    ]
    if not oi_probe_dates:
        oi_probe_dates = [
            d for d in FIXED_PROBE_DATES
            if results["oi_monthly_probes"].get(d, {}).get("http_status") == 200
        ]
        probe_type = "monthly"
    else:
        probe_type = "daily"

    # Schema inspection from first probe
    if oi_probe_dates:
        probe_date_0 = oi_probe_dates[0]
        if probe_type == "daily":
            p = _daily_oi_path(SYMBOL, probe_date_0)
        else:
            p = _monthly_oi_path(SYMBOL, probe_date_0)
        url = f"{BASE_URL}/{p}"
        try:
            zip_bytes = download_zip(url)
            hdr, rows = extract_csv_from_zip(zip_bytes)
            if hdr is None or rows is None or len(rows) == 0:
                results["outcome"] = "ARCHIVE_FEASIBILITY_FAILED"
                results["outcome_reason"] = "OI archive files are empty or unreadable"
                return results

            results["oi_schema_fields"] = hdr

            # Check timestamp field
            if TIMESTAMP_FIELD not in hdr:
                results["outcome"] = "ARCHIVE_FEASIBILITY_FAILED"
                results["outcome_reason"] = f"OI archive missing required field: {TIMESTAMP_FIELD}"
                return results

            # Check OI field
            if OI_PRIMARY_FIELD not in hdr:
                results["outcome"] = "ARCHIVE_FEASIBILITY_FAILED"
                results["outcome_reason"] = f"OI archive missing required field: {OI_PRIMARY_FIELD}"
                return results

            # Cadence & alignment assessment (daily probes)
            if probe_type == "daily":
                parsed_rows = []
                for r in rows:
                    ts = parse_timestamp(r.get(TIMESTAMP_FIELD, ""))
                    if ts:
                        try:
                            oi_val = float(r[OI_PRIMARY_FIELD])
                            parsed_rows.append({"ts": ts, "oi": oi_val})
                        except (ValueError, KeyError):
                            pass

                if len(parsed_rows) >= 2:
                    # Cadence inference
                    diffs = []
                    for i in range(1, min(len(parsed_rows), 100)):
                        diff = (parsed_rows[i]["ts"] - parsed_rows[i - 1]["ts"]).total_seconds()
                        if diff > 0:
                            diffs.append(diff)
                    if diffs:
                        median_diff = sorted(diffs)[len(diffs) // 2]
                        results["oi_observed_cadence_seconds"] = median_diff
                        results["oi_observed_cadence_label"] = f"{median_diff / 60:.0f} minutes"

                        # Check daily-only granularity
                        if median_diff > 4 * 3600:  # > 4h = daily or worse
                            results["oi_granularity_verdict"] = "OI_GRANULARITY_UNSUPPORTED"
                            results["oi_granularity_reason"] = (
                                f"Median OI cadence {median_diff / 60:.0f} minutes (>4h) "
                                f"cannot support 8h bracket"
                            )
                            results["outcome"] = "OI_GRANULARITY_UNSUPPORTED"
                            return results

                    # --- 8h funding grid alignment assessment ---
                    day_start = datetime(
                        parsed_rows[0]["ts"].year,
                        parsed_rows[0]["ts"].month,
                        parsed_rows[0]["ts"].day,
                        tzinfo=timezone.utc,
                    )
                    funding_settlements = [
                        day_start + timedelta(hours=h) for h in FUNDING_HOURS
                    ]

                    offsets_end = []
                    offsets_start = []

                    for settlement_ts in funding_settlements:
                        s8h = settlement_ts - timedelta(hours=8)

                        oi_end_ts = None
                        for p in reversed(parsed_rows):
                            if p["ts"] <= settlement_ts:
                                oi_end_ts = p["ts"]
                                break
                        oi_start_ts = None
                        for p in reversed(parsed_rows):
                            if p["ts"] <= s8h:
                                oi_start_ts = p["ts"]
                                break

                        if oi_end_ts:
                            offsets_end.append(
                                (settlement_ts - oi_end_ts).total_seconds() / 60
                            )
                        if oi_start_ts:
                            offsets_start.append(
                                (s8h - oi_start_ts).total_seconds() / 60
                            )

                    # Add alignment from a second probe sample for better statistics
                    if len(oi_probe_dates) >= 2:
                        probe_date_1 = oi_probe_dates[1]
                        p2 = _daily_oi_path(SYMBOL, probe_date_1) if probe_type == "daily" else _monthly_oi_path(SYMBOL, probe_date_1)
                        u2 = f"{BASE_URL}/{p2}"
                        try:
                            zb2 = download_zip(u2)
                            _, rows2 = extract_csv_from_zip(zb2)
                            if rows2:
                                parsed2 = []
                                for r in rows2:
                                    ts = parse_timestamp(r.get(TIMESTAMP_FIELD, ""))
                                    if ts:
                                        try:
                                            oi_val = float(r[OI_PRIMARY_FIELD])
                                            parsed2.append({"ts": ts, "oi": oi_val})
                                        except (ValueError, KeyError):
                                            pass
                                if parsed2:
                                    day_start2 = datetime(
                                        parsed2[0]["ts"].year, parsed2[0]["ts"].month,
                                        parsed2[0]["ts"].day, tzinfo=timezone.utc,
                                    )
                                    fs2 = [day_start2 + timedelta(hours=h) for h in FUNDING_HOURS]
                                    for settlement_ts in fs2:
                                        s8h2 = settlement_ts - timedelta(hours=8)
                                        oie = None
                                        ois = None
                                        for p in reversed(parsed2):
                                            if p["ts"] <= settlement_ts:
                                                oie = p["ts"]
                                                break
                                        for p in reversed(parsed2):
                                            if p["ts"] <= s8h2:
                                                ois = p["ts"]
                                                break
                                        if oie:
                                            offsets_end.append(
                                                (settlement_ts - oie).total_seconds() / 60
                                            )
                                        if ois:
                                            offsets_start.append(
                                                (s8h2 - ois).total_seconds() / 60
                                            )
                        except Exception:
                            pass

                    # Compute alignment stats
                    all_offsets_end = sorted(offsets_end) if offsets_end else []
                    all_offsets_start = sorted(offsets_start) if offsets_start else []
                    all_offsets_combined = sorted(offsets_end + offsets_start)

                    alignment_stats = {}
                    if all_offsets_end:
                        n_end = len(all_offsets_end)
                        p50_idx = int(n_end * 0.5)
                        p95_idx = int(n_end * 0.95)
                        alignment_stats["settlement_bracket"] = {
                            "p50_minutes": all_offsets_end[min(p50_idx, n_end - 1)],
                            "p95_minutes": all_offsets_end[min(p95_idx, n_end - 1)],
                            "max_minutes": max(all_offsets_end),
                            "n_samples": n_end,
                        }
                    if all_offsets_start:
                        n_start = len(all_offsets_start)
                        p50_idx = int(n_start * 0.5)
                        p95_idx = int(n_start * 0.95)
                        alignment_stats["settlement_minus_8h_bracket"] = {
                            "p50_minutes": all_offsets_start[min(p50_idx, n_start - 1)],
                            "p95_minutes": all_offsets_start[min(p95_idx, n_start - 1)],
                            "max_minutes": max(all_offsets_start),
                            "n_samples": n_start,
                        }
                    if all_offsets_combined:
                        n_comb = len(all_offsets_combined)
                        p50_idx = int(n_comb * 0.5)
                        p95_idx = int(n_comb * 0.95)
                        alignment_stats["combined"] = {
                            "p50_minutes": all_offsets_combined[min(p50_idx, n_comb - 1)],
                            "p95_minutes": all_offsets_combined[min(p95_idx, n_comb - 1)],
                            "max_minutes": max(all_offsets_combined),
                            "n_samples": n_comb,
                        }

                    results["oi_alignment_stats"] = alignment_stats

                    # OI granularity verdict
                    if not all_offsets_combined:
                        results["oi_granularity_verdict"] = "OI_CADENCE_UNUSABLE"
                        results["oi_granularity_reason"] = "No alignment offsets could be computed"
                        results["outcome"] = "OI_ALIGNMENT_FAILED"
                        return results

                    # Check p95 threshold
                    p95_combined = all_offsets_combined[
                        min(int(len(all_offsets_combined) * 0.95), len(all_offsets_combined) - 1)
                    ]
                    p95_end = all_offsets_end[
                        min(int(len(all_offsets_end) * 0.95), len(all_offsets_end) - 1)
                    ] if all_offsets_end else float("inf")
                    p95_start = all_offsets_start[
                        min(int(len(all_offsets_start) * 0.95), len(all_offsets_start) - 1)
                    ] if all_offsets_start else float("inf")

                    max_p95 = max(p95_end, p95_start)
                    if max_p95 <= MAX_ACCEPTABLE_P95_OFFSET_MINUTES:
                        results["oi_granularity_verdict"] = "OI_GRANULARITY_OK"
                        results["oi_granularity_reason"] = (
                            f"P95 alignment offset {max_p95:.2f} minutes "
                            f"<= {MAX_ACCEPTABLE_P95_OFFSET_MINUTES} minute threshold"
                        )
                    else:
                        results["oi_granularity_verdict"] = "OI_ALIGNMENT_FAILED"
                        results["oi_granularity_reason"] = (
                            f"P95 alignment offset {max_p95:.2f} minutes "
                            f"> {MAX_ACCEPTABLE_P95_OFFSET_MINUTES} minute threshold"
                        )
                        results["outcome"] = "OI_ALIGNMENT_FAILED"
                        return results
                else:
                    results["oi_granularity_verdict"] = "OI_CADENCE_UNUSABLE"
                    results["oi_granularity_reason"] = "Insufficient rows in OI file"
                    results["outcome"] = "OI_ALIGNMENT_FAILED"
                    return results
            else:
                # Monthly-only OI - likely too coarse for 8h bracket
                results["oi_granularity_verdict"] = "OI_GRANULARITY_UNSUPPORTED"
                results["oi_granularity_reason"] = (
                    "Monthly OI archive only (daily not available). "
                    "Monthly data cannot support an 8h OI bracket."
                )
                results["outcome"] = "OI_GRANULARITY_UNSUPPORTED"
                return results

        except Exception as e:
            results["outcome"] = "ARCHIVE_FEASIBILITY_FAILED"
            results["outcome_reason"] = f"OI schema inspection failed: {e}"
            return results

    results["outcome"] = "PHASE0A_PASSED"
    return results


# ---------------------------------------------------------------------------
# Phase 0B: Population Sizing (counts-only, deterministic)
# ---------------------------------------------------------------------------

def run_phase0b():
    """
    Counts-only population sizing for the falling-OI unwind hypothesis.

    Identifies:
    1. All funding settlement timestamps
    2. Those with full 180-day past-only warmup
    3. Those in bottom 5% (negative funding extreme)
    4. Those with falling OI in the 8h bracket
    5. Split 70/30 chronologically
    6. Check spot availability at 24h and 48h horizons

    Returns dict with counts and verdict.
    No randomness. No forward returns. No edge stats.
    """
    results = OrderedDict()
    results["phase"] = "0B"
    results["population_sizing_rule"] = (
        "Counts-only: past-only 180-day percentile membership. "
        "Bottom 5% = negative funding extreme. "
        "OI regime from Binance Vision daily metrics (sum_open_interest). "
        "No threshold values, timestamps, or returns in output."
    )
    # Content hashes
    results["archive_content_hashes"] = OrderedDict()

    # ------------------------------------------------------------------
    # 1. Load ALL monthly funding data
    # ------------------------------------------------------------------
    months = list(iter_months(START_YM, END_YM))
    print(f"Loading {len(months)} monthly funding rate files {START_YM} to {END_YM}...")

    all_funding = []
    funding_hashes = OrderedDict()
    for i, ym in enumerate(months):
        path = MONTHLY_FUNDING_PATH_TEMPLATE.format(symbol=SYMBOL, ym=ym)
        url = f"{BASE_URL}/{path}"
        cache_key = f"funding_{ym}.zip"
        try:
            zip_bytes = download_zip_cached(url, cache_key, timeout=15)
            if ym not in funding_hashes:
                funding_hashes[ym] = sha256_of_bytes(zip_bytes)
            hdr, rows = extract_csv_from_zip(zip_bytes)
            if hdr and rows:
                for r in rows:
                    try:
                        calc_time_ms = int(r["calc_time"])
                        rate = float(r["last_funding_rate"])
                        ts = datetime.fromtimestamp(
                            calc_time_ms / 1000, tz=timezone.utc
                        )
                        all_funding.append({"ts": ts, "funding_rate": rate})
                    except (ValueError, KeyError):
                        pass
        except Exception:
            continue
        if (i + 1) % 15 == 0:
            print(f"  ... {i+1}/{len(months)} months, {len(all_funding)} rows")

    all_funding.sort(key=lambda x: x["ts"])
    results["archive_content_hashes"]["funding_months"] = funding_hashes
    results["funding_rows_loaded"] = len(all_funding)
    print(f"  Total: {len(all_funding)} funding rows")

    if len(all_funding) < 200:
        results["outcome"] = "NEEDS_MORE_DATA"
        results["outcome_reason"] = f"Insufficient funding data ({len(all_funding)} rows)"
        return results

    # ------------------------------------------------------------------
    # 2. Identify eligible settlement timestamps (post-warmup)
    #    and negative funding extreme events
    # ------------------------------------------------------------------
    first_ts = all_funding[0]["ts"]
    warmup_end = first_ts + timedelta(days=WARMUP_DAYS)

    all_eligible = []
    negative_extreme_events = []

    for i, fr in enumerate(all_funding):
        if fr["ts"] <= warmup_end:
            continue
        # Check settlement grid alignment
        if fr["ts"].hour not in FUNDING_HOURS or fr["ts"].minute != 0 or fr["ts"].second != 0:
            continue  # excluded from eligibility

        lookback_start = fr["ts"] - timedelta(days=WARMUP_DAYS)
        past_rows = [r for r in all_funding[:i] if r["ts"] >= lookback_start]
        if len(past_rows) < 50:
            continue

        past_rates = sorted([r["funding_rate"] for r in past_rows])
        n = len(past_rates)
        bot5 = past_rates[min(int(n * 0.05), n - 1)]

        is_negative_extreme = fr["funding_rate"] <= bot5

        all_eligible.append({
            "ts": fr["ts"],
            "funding_rate": fr["funding_rate"],
            "is_negative_extreme": is_negative_extreme,
        })

        if is_negative_extreme:
            negative_extreme_events.append({
                "ts": fr["ts"],
                "funding_rate": fr["funding_rate"],
            })

    results["total_eligible_settlements"] = len(all_eligible)
    results["total_negative_extreme_settlements"] = len(negative_extreme_events)
    print(f"  Eligible settlements: {len(all_eligible)}")
    print(f"  Negative extreme: {len(negative_extreme_events)}")

    if len(negative_extreme_events) < MIN_CELL_EVENTS:
        results["outcome"] = "NEEDS_MORE_DATA"
        results["outcome_reason"] = (
            f"Only {len(negative_extreme_events)} negative extreme funding events "
            f"(need >= {MIN_CELL_EVENTS})"
        )
        return results

    # ------------------------------------------------------------------
    # 3. Load OI data for all unique dates needed
    # ------------------------------------------------------------------
    unique_dates = set()
    for ev in negative_extreme_events:
        unique_dates.add(ev["ts"].strftime(YMD))
        unique_dates.add((ev["ts"] - timedelta(hours=8)).strftime(YMD))

    dates_sorted = sorted(unique_dates)
    print(f"\nLoading {len(dates_sorted)} unique daily OI files (parallel, 8 workers)...")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    oi_by_date = {}
    loaded = 0
    failed = 0
    oi_hashes = OrderedDict()

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
                if d not in oi_hashes:
                    oi_hashes[d] = sha256_of_bytes(zip_bytes)
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

    results["archive_content_hashes"]["oi_daily"] = oi_hashes
    results["oi_daily_files_loaded"] = loaded
    results["oi_daily_files_missing"] = failed
    print(f"  Loaded {loaded} daily files, {failed} not found")

    # Merge all OI rows into a single sorted list
    all_oi = []
    for date_str in sorted(oi_by_date.keys()):
        all_oi.extend(oi_by_date[date_str])
    all_oi.sort(key=lambda x: x["ts"])
    print(f"  Total OI rows: {len(all_oi)}")

    # ------------------------------------------------------------------
    # 4. Load spot klines for availability check only
    # ------------------------------------------------------------------
    print("\nLoading spot klines (header only) for availability check...")
    spot_klines = []
    spot_hashes = OrderedDict()
    for ym in months:
        path = _spot_klines_path(SYMBOL, ym)
        url = f"{BASE_URL}/{path}"
        cache_key = f"spot_klines_{ym}.zip"
        try:
            zip_bytes = download_zip_cached(url, cache_key, timeout=30)
            if ym not in spot_hashes:
                spot_hashes[ym] = sha256_of_bytes(zip_bytes)
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
    results["archive_content_hashes"]["spot_klines"] = spot_hashes
    print(f"  Total: {len(spot_klines)} spot klines")

    # ------------------------------------------------------------------
    # 5. OI classification for negative extreme events
    # ------------------------------------------------------------------
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

    import math

    falling_oi_events = []
    unaligned_count = 0

    for ev in negative_extreme_events:
        regime, change_pct = compute_oi_regime(ev["ts"], all_oi)
        if regime == "OI_UNALIGNED":
            unaligned_count += 1
        elif regime == "falling_oi":
            falling_oi_events.append(ev)

    results["negative_extreme_unaligned_count"] = unaligned_count
    results["negative_extreme_falling_oi_count"] = len(falling_oi_events)
    print(f"\n  OI UNALIGNED: {unaligned_count}")
    print(f"  Falling OI events: {len(falling_oi_events)}")

    if len(falling_oi_events) < MIN_CELL_EVENTS:
        results["outcome"] = "NEEDS_MORE_DATA"
        results["outcome_reason"] = (
            f"Only {len(falling_oi_events)} negative extreme + falling OI events "
            f"(need >= {MIN_CELL_EVENTS})"
        )
        return results

    # ------------------------------------------------------------------
    # 6. Chronological 70/30 split on falling OI events
    # ------------------------------------------------------------------
    falling_oi_sorted = sorted(falling_oi_events, key=lambda x: x["ts"])
    split_idx = int(len(falling_oi_sorted) * 0.7)
    split_ts = falling_oi_sorted[split_idx]["ts"]
    results["chronological_split_timestamp_utc"] = split_ts.isoformat()

    train_events = [e for e in falling_oi_sorted if e["ts"] <= split_ts]
    holdout_events = [e for e in falling_oi_sorted if e["ts"] > split_ts]
    results["train_count"] = len(train_events)
    results["holdout_count"] = len(holdout_events)
    print(f"  Split: {split_ts.isoformat()}")
    print(f"  Train: {len(train_events)}, Holdout: {len(holdout_events)}")

    # Check holdout power
    if len(holdout_events) < MIN_HOLDOUT_EVENTS:
        results["outcome"] = "UNDERPOWERED_HOLDOUT_FAILURE"
        results["outcome_reason"] = (
            f"Only {len(holdout_events)} holdout events "
            f"(need >= {MIN_HOLDOUT_EVENTS})"
        )
        return results

    if len(train_events) + len(holdout_events) < MIN_CELL_EVENTS:
        results["outcome"] = "NEEDS_MORE_DATA"
        results["outcome_reason"] = (
            f"Only {len(train_events) + len(holdout_events)} total events "
            f"(need >= {MIN_CELL_EVENTS})"
        )
        return results

    # ------------------------------------------------------------------
    # 7. Check spot availability for 24h and 48h horizons
    # ------------------------------------------------------------------
    spot_available_24h = 0
    spot_available_48h = 0

    for ev in falling_oi_sorted:
        settlement_ts = ev["ts"]

        # Entry price
        entry_price = None
        for k in spot_klines:
            if k["ts"] >= settlement_ts:
                entry_price = k["close"]
                break

        if entry_price is None or entry_price <= 0:
            continue

        # 24h forward
        fwd_24h = settlement_ts + timedelta(hours=24)
        fwd_price_24h = None
        for k in spot_klines:
            if k["ts"] >= fwd_24h:
                fwd_price_24h = k["close"]
                break

        # 48h forward
        fwd_48h = settlement_ts + timedelta(hours=48)
        fwd_price_48h = None
        for k in spot_klines:
            if k["ts"] >= fwd_48h:
                fwd_price_48h = k["close"]
                break

        if fwd_price_24h is not None and fwd_price_24h > 0:
            spot_available_24h += 1
        if fwd_price_48h is not None and fwd_price_48h > 0:
            spot_available_48h += 1

    results["spot_available_24h_count"] = spot_available_24h
    results["spot_available_48h_count"] = spot_available_48h

    # Per-cell checks
    if spot_available_24h < MIN_CELL_EVENTS:
        results["outcome"] = "SPOT_AVAILABILITY_FAILED"
        results["outcome_reason"] = (
            f"Only {spot_available_24h} events with 24h spot availability "
            f"(need >= {MIN_CELL_EVENTS})"
        )
        return results

    if spot_available_48h < MIN_CELL_EVENTS:
        results["outcome"] = "SPOT_AVAILABILITY_FAILED"
        results["outcome_reason"] = (
            f"Only {spot_available_48h} events with 48h spot availability "
            f"(need >= {MIN_CELL_EVENTS})"
        )
        return results

    # ------------------------------------------------------------------
    # 8. Verdict
    # ------------------------------------------------------------------
    # Per-cell counts (same event set, different horizon availability)
    # Both cells share the same negative-extreme + falling-OI events.
    # We need >= 50 total and >= 50 holdout for each cell.
    # Since both use the same event set, we already checked total and holdout.

    # Holdout counts for each horizon
    holdout_24h = sum(
        1 for e in holdout_events
        if _has_spot_availability(e["ts"], spot_klines, 24)
    )
    holdout_48h = sum(
        1 for e in holdout_events
        if _has_spot_availability(e["ts"], spot_klines, 48)
    )

    train_24h = spot_available_24h - holdout_24h
    train_48h = spot_available_48h - holdout_48h

    results["train_24h_count"] = train_24h
    results["holdout_24h_count"] = holdout_24h
    results["train_48h_count"] = train_48h
    results["holdout_48h_count"] = holdout_48h

    cells_ok = True
    cell_details = {}

    for horizon_h, avail, hold in [
        (24, spot_available_24h, holdout_24h),
        (48, spot_available_48h, holdout_48h),
    ]:
        cell_ok = True
        issues = []
        if avail < MIN_CELL_EVENTS:
            cell_ok = False
            issues.append(f"total={avail} < {MIN_CELL_EVENTS}")
        if hold < MIN_HOLDOUT_EVENTS:
            cell_ok = False
            issues.append(f"holdout={hold} < {MIN_HOLDOUT_EVENTS}")
        cell_details[f"{horizon_h}h"] = {
            "total_events": avail,
            "holdout_events": hold,
            "pass": cell_ok,
            "issues": issues,
        }
        if not cell_ok:
            cells_ok = False

    results["per_cell_details"] = cell_details

    if not cells_ok:
        # Determine specific verdict
        if any(
            cell_details[h]["total_events"] < MIN_CELL_EVENTS
            for h in ["24h", "48h"]
        ):
            results["outcome"] = "NEEDS_MORE_DATA"
            results["outcome_reason"] = "One or both cells have insufficient total events"
        else:
            results["outcome"] = "UNDERPOWERED_HOLDOUT_FAILURE"
            results["outcome_reason"] = "One or both cells have insufficient holdout events"
        return results

    results["outcome"] = "POPULATION_SUFFICIENT"
    results["outcome_reason"] = (
        f"Both cells have >= {MIN_CELL_EVENTS} total events and "
        f">= {MIN_HOLDOUT_EVENTS} holdout events"
    )
    return results


def _has_spot_availability(settlement_ts, spot_klines, horizon_h):
    """Check if spot price is available at entry and for the given horizon."""
    entry_price = None
    for k in spot_klines:
        if k["ts"] >= settlement_ts:
            entry_price = k["close"]
            break
    if entry_price is None or entry_price <= 0:
        return False

    fwd_ts = settlement_ts + timedelta(hours=horizon_h)
    fwd_price = None
    for k in spot_klines:
        if k["ts"] >= fwd_ts:
            fwd_price = k["close"]
            break

    return fwd_price is not None and fwd_price > 0


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def build_phase0_report(phase0a_results, phase0b_results):
    """Build and write the combined phase0_population_report.json."""
    # Compute precommitment SHA
    precommitment_sha = sha256_of_file(PRECOMMITMENT_PATH)

    report = OrderedDict()
    report["study_id"] = "family3_funding_falling_oi_unwind_v1"
    report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["git_sha"] = get_git_sha()
    report["precommitment_sha256"] = precommitment_sha

    # Phase 0A results
    report["phase_0a"] = OrderedDict()
    report["phase_0a"]["fixed_archive_probe_results"] = {
        "funding_monthly": phase0a_results.get("funding_monthly_probes", {}),
        "oi_daily": phase0a_results.get("oi_daily_probes", {}),
        "oi_monthly": phase0a_results.get("oi_monthly_probes", {}),
        "spot": phase0a_results.get("spot_probes", {}),
    }
    report["phase_0a"]["funding_available"] = phase0a_results.get("funding_available", False)
    report["phase_0a"]["oi_daily_available"] = phase0a_results.get("oi_daily_available", False)
    report["phase_0a"]["spot_available"] = phase0a_results.get("spot_available", False)

    # OI alignment / granularity
    report["phase_0a"]["oi_alignment_stats"] = phase0a_results.get("oi_alignment_stats", None)
    report["phase_0a"]["oi_granularity_verdict"] = phase0a_results.get("oi_granularity_verdict")
    report["phase_0a"]["oi_granularity_reason"] = phase0a_results.get("oi_granularity_reason")

    # Phase 0A outcome
    report["phase_0a"]["outcome"] = phase0a_results.get("outcome")

    # Phase 0B results
    report["phase_0b"] = OrderedDict()
    if phase0b_results:
        report["phase_0b"]["total_eligible_settlements"] = phase0b_results.get("total_eligible_settlements")
        report["phase_0b"]["total_negative_extreme_settlements"] = phase0b_results.get("total_negative_extreme_settlements")
        report["phase_0b"]["negative_extreme_falling_oi_count"] = phase0b_results.get("negative_extreme_falling_oi_count")
        report["phase_0b"]["negative_extreme_unaligned_count"] = phase0b_results.get("negative_extreme_unaligned_count")
        report["phase_0b"]["chronological_split_timestamp_utc"] = phase0b_results.get("chronological_split_timestamp_utc")
        report["phase_0b"]["train_count"] = phase0b_results.get("train_count")
        report["phase_0b"]["holdout_count"] = phase0b_results.get("holdout_count")
        report["phase_0b"]["spot_available_24h_count"] = phase0b_results.get("spot_available_24h_count")
        report["phase_0b"]["spot_available_48h_count"] = phase0b_results.get("spot_available_48h_count")
        report["phase_0b"]["per_cell_details"] = phase0b_results.get("per_cell_details")
        report["phase_0b"]["outcome"] = phase0b_results.get("outcome")

    # Archive content hashes
    if phase0b_results:
        report["archive_content_hashes"] = phase0b_results.get("archive_content_hashes", {})
    else:
        report["archive_content_hashes"] = {}

    # Final outcome
    if phase0a_results.get("outcome") == "PHASE0A_PASSED" and phase0b_results:
        report["outcome"] = phase0b_results.get("outcome")
        report["outcome_reason"] = phase0b_results.get("outcome_reason")
    elif phase0a_results.get("outcome") != "PHASE0A_PASSED":
        report["outcome"] = phase0a_results.get("outcome")
        report["outcome_reason"] = phase0a_results.get("outcome_reason")
    else:
        report["outcome"] = "PHASE0B_FAILED"
        report["outcome_reason"] = "Phase 0B did not produce a result"

    # Write report
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nWrote phase0 population report: {OUTPUT_FILE}")

    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("Stage A: Phase 0A Archive Feasibility + Phase 0B Population Sizing")
    print("Family 3 v1: BTCUSDT Funding × Falling OI Unwind-Continuation")
    print("=" * 70)
    print()

    # --- Phase 0A ---
    print("--- Phase 0A: Archive Feasibility ---")
    print(f"Fixed probe dates: {FIXED_PROBE_DATES}")
    print()
    phase0a_results = run_phase0a()

    p0a_outcome = phase0a_results.get("outcome", "UNKNOWN")
    print(f"\nPhase 0A outcome: {p0a_outcome}")
    print(f"  Funding available: {phase0a_results.get('funding_available')}")
    print(f"  OI daily available: {phase0a_results.get('oi_daily_available')}")
    print(f"  Spot available: {phase0a_results.get('spot_available')}")

    if phase0a_results.get("oi_alignment_stats"):
        combined = phase0a_results["oi_alignment_stats"].get("combined", {})
        print(f"  OI alignment p50: {combined.get('p50_minutes')} min")
        print(f"  OI alignment p95: {combined.get('p95_minutes')} min")
        print(f"  OI alignment max: {combined.get('max_minutes')} min")

    oi_gv = phase0a_results.get("oi_granularity_verdict")
    if oi_gv:
        print(f"  OI granularity: {oi_gv}")

    if p0a_outcome != "PHASE0A_PASSED":
        print(f"\nPhase 0A failed: {phase0a_results.get('outcome_reason', 'Unknown')}")
        print("Stopping. Did not proceed to Phase 0B.")
        report = build_phase0_report(phase0a_results, None)
        print(f"\nFinal outcome: {report['outcome']}")
        return

    # --- Phase 0B ---
    print("\n--- Phase 0B: Population Sizing (Counts-Only) ---")
    print("Period: 2020-09 to 2026-01")
    print()
    phase0b_results = run_phase0b()

    p0b_outcome = phase0b_results.get("outcome", "UNKNOWN")
    print(f"\nPhase 0B outcome: {p0b_outcome}")
    print(f"  Total eligible settlements: {phase0b_results.get('total_eligible_settlements')}")
    print(f"  Negative extreme: {phase0b_results.get('total_negative_extreme_settlements')}")
    print(f"  Neg extreme + falling OI: {phase0b_results.get('negative_extreme_falling_oi_count')}")
    print(f"  Train: {phase0b_results.get('train_count')}")
    print(f"  Holdout: {phase0b_results.get('holdout_count')}")
    print(f"  Spot available 24h: {phase0b_results.get('spot_available_24h_count')}")
    print(f"  Spot available 48h: {phase0b_results.get('spot_available_48h_count')}")

    # --- Build and write combined report ---
    report = build_phase0_report(phase0a_results, phase0b_results)
    print(f"\n=== Stage A Final Outcome: {report['outcome']} ===")
    print(f"  Reason: {report.get('outcome_reason', 'N/A')}")

    if report["outcome"] == "POPULATION_SUFFICIENT":
        print("\nStage A PASSED. Proceeding to Stage B requires running the evaluator.")
        print("The evaluator must read this report and proceed only if outcome == POPULATION_SUFFICIENT.")
    else:
        print(f"\nStage A FAILED. Stopping. Do not build evaluator.")
        print(f"Outcome: {report['outcome']}")

    print("\nNo forward returns, edge stats, null tests, FDR, evaluation,")
    print("registry update, private key, order, execution, or bot path was used.")

    return report


if __name__ == "__main__":
    main()
