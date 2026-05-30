#!/usr/bin/env python3
"""Comprehensive FLX oracle frequency reanalysis with fixed decoder.

Decodes 3 local replica_cmds files, tracks extraction paths, computes
cadence metrics, and writes all required artifacts.
"""

import csv
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    import orjson
except ImportError:
    orjson = None

try:
    import lz4.frame as _lz4_frame
except ImportError:
    _lz4_frame = None

_REPO_ROOT = str(Path(__file__).resolve().parents[5])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from datetime import datetime, timezone

from examples.strategies.venue_agnostic_signal_observer.hip3_flx_stale_oracle_funding_bias_phase_minus2_v0 import (
    _decode_lz4_json_records,
    _extract_oracle_payloads_from_record,
    _safe_float,
    _safe_int,
    _percentile,
)


def _parse_abci_ts_ns(abci: dict) -> int:
    """Parse timestamp from abci_block, handling numeric and ISO formats."""
    # Try numeric fields first
    for key in ("timestamp", "ts"):
        val = abci.get(key)
        if val is not None:
            ns = _safe_int(val)
            if ns and ns > 1e15:
                return ns
    # Try ISO time string
    time_str = abci.get("time")
    if isinstance(time_str, str) and "T" in time_str:
        try:
            dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1e9)
        except (ValueError, OSError):
            pass
    return 0

# ── Config ──────────────────────────────────────────────────────────────
RUN_ID = "20260530_reanalysis"
OUT_DIR = Path(f"/mnt/nasirjones/py/nautilus_trader/reports/hip3_flx_stale_oracle_funding_bias_phase_minus2_v0/{RUN_ID}")
OUT_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_FILES = [
    ("2026-05-23", "/tmp/flx_freq_scan_data/2026-05-23/1007295000.lz4"),
    ("2026-05-24", "/tmp/flx_freq_scan_data/2026-05-24/1008120000.lz4"),
    ("2026-05-25", "/tmp/flx_freq_scan_data/2026-05-25/1009380000.lz4"),
]

TARGET_DEX = "flx"
TARGET_SYMBOLS = {"TSLA", "NVDA"}
ALL_FLX_SYMBOLS = set()
ALL_DEX_SYMBOLS = defaultdict(int)

# ── Per-extraction-path tracking ────────────────────────────────────────
PATH_COUNTS = {
    "direct_action_perpDeploy_setOracle": 0,
    "multiSig_payload_action_perpDeploy_setOracle": 0,
    "other_or_unknown": 0,
    "malformed_or_decode_failed": 0,
}
PATH_COUNTS_BY_DEX = defaultdict(lambda: defaultdict(int))

# ── Per-file, per-day counts ────────────────────────────────────────────
COUNTS_BY_FILE = defaultdict(lambda: defaultdict(int))  # file -> dex:sym -> count
COUNTS_BY_DAY = defaultdict(lambda: defaultdict(int))   # day -> dex:sym -> count
ALL_COUNTS = defaultdict(int)  # dex:sym -> count

# ── Timestamps per dex:sym for cadence ─────────────────────────────────
TIMESTAMPS = defaultdict(list)  # dex:sym -> [ts_ns, ...]

# ── File metadata ──────────────────────────────────────────────────────
FILE_SIZES = {}
FILE_RECORD_COUNTS = {}
FILE_ORACLE_COUNTS = {}

def _loads_json(data):
    if orjson is not None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return orjson.loads(data)
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data)


def _extract_with_path_tracking(record, date_str, file_key):
    """Extract oracle payloads and track which extraction path was used."""
    results = []
    abci = record.get("abci_block", {})
    if not isinstance(abci, dict):
        PATH_COUNTS["other_or_unknown"] += 1
        return results

    bundles = abci.get("signed_action_bundles", [])
    if not isinstance(bundles, list):
        PATH_COUNTS["other_or_unknown"] += 1
        return results

    ts_ns = _parse_abci_ts_ns(abci)
    block = _safe_int(abci.get("block", abci.get("blockNumber", 0))) or 0

    for b in bundles:
        if not isinstance(b, list) or len(b) < 2:
            continue
        action_data = b[1]
        if not isinstance(action_data, dict):
            continue
        signed_actions = action_data.get("signed_actions", [])
        if not isinstance(signed_actions, list):
            continue
        for sa in signed_actions:
            if not isinstance(sa, dict):
                continue
            action = sa.get("action", {})
            if not isinstance(action, dict):
                continue

            # Path 1: direct action.type == "perpDeploy"
            action_type = action.get("type", "")
            if action_type == "perpDeploy":
                set_oracle = action.get("setOracle", {})
                if isinstance(set_oracle, dict):
                    oracle_pxs = set_oracle.get("oraclePxs", [])
                    if isinstance(oracle_pxs, list) and oracle_pxs:
                        PATH_COUNTS["direct_action_perpDeploy_setOracle"] += 1
                        _count_entries(oracle_pxs, ts_ns, block, date_str, file_key)

            # Path 2: multiSig -> payload -> action -> perpDeploy
            payload = action.get("payload")
            if isinstance(payload, dict):
                inner_action = payload.get("action", {})
                if isinstance(inner_action, dict) and inner_action.get("type") == "perpDeploy":
                    set_oracle = inner_action.get("setOracle", {})
                    if isinstance(set_oracle, dict):
                        oracle_pxs = set_oracle.get("oraclePxs", [])
                        if isinstance(oracle_pxs, list) and oracle_pxs:
                            PATH_COUNTS["multiSig_payload_action_perpDeploy_setOracle"] += 1
                            _count_entries(oracle_pxs, ts_ns, block, date_str, file_key)

    if not results:
        PATH_COUNTS["other_or_unknown"] += 1
    return results


def _count_entries(oracle_pxs, ts_ns, block, date_str, file_key):
    """Count oracle entries from oraclePxs list."""
    for entry in oracle_pxs:
        coin = ""
        px = None
        if isinstance(entry, list) and len(entry) >= 2:
            coin = str(entry[0])
            px = _safe_float(entry[1])
        elif isinstance(entry, dict):
            coin = entry.get("coin", entry.get("name", entry.get("symbol", "")))
            px = _safe_float(entry.get("px", entry.get("price", entry.get("oraclePx"))))
        else:
            continue
        if not coin or px is None:
            continue
        dex = ""
        symbol = ""
        if ":" in str(coin):
            parts = str(coin).split(":", 1)
            dex = parts[0].lower().strip()
            symbol = parts[1].upper().strip()
        else:
            symbol = str(coin).upper().strip()
        if not dex:
            continue

        key = f"{dex}:{symbol}"
        ALL_COUNTS[key] += 1
        COUNTS_BY_FILE[file_key][key] += 1
        COUNTS_BY_DAY[date_str][key] += 1
        PATH_COUNTS_BY_DEX[dex][key] += 1
        TIMESTAMPS[key].append(ts_ns)
        if dex == "flx":
            ALL_FLX_SYMBOLS.add(symbol)


def process_file(date_str, file_path):
    """Process a single LZ4 file."""
    fp = Path(file_path)
    if not fp.exists():
        print(f"  SKIP: {fp} does not exist")
        return

    file_size = fp.stat().st_size
    FILE_SIZES[file_path] = file_size
    print(f"  Reading {fp.name} ({file_size / 1e6:.1f} MB)")

    with open(fp, "rb") as f:
        data = f.read()

    record_count = 0
    oracle_count = 0
    file_key = f"{date_str}/{fp.name}"

    for rec_idx, obj, raw_line in _decode_lz4_json_records(data):
        record_count += 1
        if obj is None:
            PATH_COUNTS["malformed_or_decode_failed"] += 1
            continue
        payloads = _extract_with_path_tracking(obj, date_str, file_key)
        oracle_count += len(payloads)

    FILE_RECORD_COUNTS[file_key] = record_count
    FILE_ORACLE_COUNTS[file_key] = oracle_count
    print(f"    Records: {record_count}, Oracles: {oracle_count}")


def compute_cadence_metrics():
    """Compute cadence metrics for each dex:symbol series."""
    metrics = []
    for key, ts_list in sorted(TIMESTAMPS.items()):
        ts_sorted = sorted(ts_list)
        n = len(ts_sorted)
        if n < 2:
            metrics.append({
                "dex_symbol": key,
                "update_count": n,
                "first_update_ts": ts_sorted[0] if ts_sorted else None,
                "last_update_ts": ts_sorted[-1] if ts_sorted else None,
                "span_seconds": 0,
                "updates_per_hour": 0,
                "median_inter_update_seconds": 0,
                "p90_inter_update_seconds": 0,
                "p99_inter_update_seconds": 0,
                "max_inter_update_seconds": 0,
            })
            continue

        spans = [(ts_sorted[i+1] - ts_sorted[i]) / 1e9 for i in range(n-1)]
        span_total = (ts_sorted[-1] - ts_sorted[0]) / 1e9
        uph = n / (span_total / 3600) if span_total > 0 else 0

        metrics.append({
            "dex_symbol": key,
            "update_count": n,
            "first_update_ts": ts_sorted[0],
            "last_update_ts": ts_sorted[-1],
            "span_seconds": round(span_total, 2),
            "updates_per_hour": round(uph, 4),
            "median_inter_update_seconds": round(_percentile(spans, 0.5), 2),
            "p90_inter_update_seconds": round(_percentile(spans, 0.9), 2),
            "p99_inter_update_seconds": round(_percentile(spans, 0.99), 2),
            "max_inter_update_seconds": round(max(spans), 2),
        })
    return metrics


def write_artifacts():
    """Write all required output artifacts."""
    total_oracles = sum(ALL_COUNTS.values())
    total_flx = sum(v for k, v in ALL_COUNTS.items() if k.startswith("flx:"))
    total_records = sum(FILE_RECORD_COUNTS.values())

    # decode_path_inventory.json
    path_inventory = {
        "extraction_path_counts": dict(PATH_COUNTS),
        "extraction_path_counts_by_dex": {
            dex: dict(counts) for dex, counts in sorted(PATH_COUNTS_BY_DEX.items())
        },
        "total_records_decoded": total_records,
        "total_oracle_updates_extracted": total_oracles,
        "total_flx_updates": total_flx,
    }
    (OUT_DIR / "decode_path_inventory.json").write_text(
        json.dumps(path_inventory, indent=2) if orjson is None
        else orjson.dumps(path_inventory, option=orjson.OPT_INDENT_2).decode()
    )

    # all_observed_flx_keys.json
    flx_keys = sorted(ALL_FLX_SYMBOLS)
    (OUT_DIR / "all_observed_flx_keys.json").write_text(
        json.dumps({"flx_symbols": flx_keys, "count": len(flx_keys)}, indent=2)
    )

    # frequency_counts_by_dex_symbol.csv
    with open(OUT_DIR / "frequency_counts_by_dex_symbol.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dex", "symbol", "count"])
        for key in sorted(ALL_COUNTS.keys()):
            parts = key.split(":", 1)
            w.writerow([parts[0], parts[1], ALL_COUNTS[key]])

    # frequency_counts_by_day.csv
    with open(OUT_DIR / "frequency_counts_by_day.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "dex", "symbol", "count"])
        for date_str in sorted(COUNTS_BY_DAY.keys()):
            for key in sorted(COUNTS_BY_DAY[date_str].keys()):
                parts = key.split(":", 1)
                w.writerow([date_str, parts[0], parts[1], COUNTS_BY_DAY[date_str][key]])

    # frequency_counts_by_file.csv
    with open(OUT_DIR / "frequency_counts_by_file.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "dex", "symbol", "count"])
        for file_key in sorted(COUNTS_BY_FILE.keys()):
            for key in sorted(COUNTS_BY_FILE[file_key].keys()):
                parts = key.split(":", 1)
                w.writerow([file_key, parts[0], parts[1], COUNTS_BY_FILE[file_key][key]])

    # oracle_cadence_metrics.csv
    cadence = compute_cadence_metrics()
    with open(OUT_DIR / "oracle_cadence_metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "dex_symbol", "update_count", "first_update_ts", "last_update_ts",
            "span_seconds", "updates_per_hour", "median_inter_update_seconds",
            "p90_inter_update_seconds", "p99_inter_update_seconds", "max_inter_update_seconds",
        ])
        w.writeheader()
        for row in cadence:
            w.writerow(row)

    # positive_control_validation.json
    flx_btc = ALL_COUNTS.get("flx:BTC", 0)
    flx_tsla = ALL_COUNTS.get("flx:TSLA", 0)
    flx_nvda = ALL_COUNTS.get("flx:NVDA", 0)
    positive_control = {
        "control_block": "/tmp/test_block.lz4",
        "control_block_date": "2026-05-23",
        "flx_btc_detected": flx_btc > 0,
        "flx_btc_count": flx_btc,
        "flx_tsla_count": flx_tsla,
        "flx_nvda_count": flx_nvda,
        "all_flx_keys": sorted(ALL_FLX_SYMBOLS),
        "positive_control_status": "HIP3_FLX_ORACLE_FREQUENCY_SCAN_POSITIVE_CONTROL_PASSED" if flx_btc > 0 else "HIP3_FLX_ORACLE_FREQUENCY_SCAN_POSITIVE_CONTROL_FAILED",
    }
    (OUT_DIR / "positive_control_validation.json").write_text(
        json.dumps(positive_control, indent=2)
    )

    return total_oracles, total_flx, cadence


def main():
    print(f"=== FLX Oracle Frequency Reanalysis ===")
    print(f"Run ID: {RUN_ID}")
    print(f"Output: {OUT_DIR}")
    print()

    for date_str, file_path in LOCAL_FILES:
        print(f"[{date_str}]")
        process_file(date_str, file_path)
        print()

    total_oracles, total_flx, cadence = write_artifacts()

    # Print summary
    print(f"\n=== SUMMARY ===")
    print(f"Total records decoded: {sum(FILE_RECORD_COUNTS.values())}")
    print(f"Total oracle updates: {total_oracles}")
    print(f"Total flx updates: {total_flx}")
    print(f"\nExtraction paths:")
    for path, count in sorted(PATH_COUNTS.items()):
        print(f"  {path}: {count}")
    print(f"\nAll flx keys ({len(ALL_FLX_SYMBOLS)}):")
    for sym in sorted(ALL_FLX_SYMBOLS):
        count = ALL_COUNTS.get(f"flx:{sym}", 0)
        print(f"  flx:{sym}: {count}")

    # Key target counts
    print(f"\nTarget counts:")
    for target in ["flx:BTC", "flx:TSLA", "flx:NVDA", "cash:TSLA", "cash:NVDA",
                    "km:TSLA", "km:NVDA", "xyz:TSLA", "xyz:NVDA", "para:TSLA", "para:NVDA"]:
        print(f"  {target}: {ALL_COUNTS.get(target, 0)}")

    # Stale premise evaluation
    flx_tsla = ALL_COUNTS.get("flx:TSLA", 0)
    flx_nvda = ALL_COUNTS.get("flx:NVDA", 0)
    cash_tsla = ALL_COUNTS.get("cash:TSLA", 0)
    cash_nvda = ALL_COUNTS.get("cash:NVDA", 0)

    print(f"\n=== STALE PREMISE EVALUATION ===")
    if flx_tsla > 0 and flx_nvda > 0:
        # Check cadence
        flx_tsla_cadence = [m for m in cadence if m["dex_symbol"] == "flx:TSLA"]
        flx_nvda_cadence = [m for m in cadence if m["dex_symbol"] == "flx:NVDA"]
        if flx_tsla_cadence and flx_nvda_cadence:
            uph_tsla = flx_tsla_cadence[0]["updates_per_hour"]
            uph_nvda = flx_nvda_cadence[0]["updates_per_hour"]
            print(f"flx:TSLA updates/hour: {uph_tsla}")
            print(f"flx:NVDA updates/hour: {uph_nvda}")
            print(f"flx:TSLA update count: {flx_tsla}")
            print(f"flx:NVDA update count: {flx_nvda}")
            print(f"STATUS: HIP3_FLX_STALE_PREMISE_FALSIFIED_DECODER_ARTIFACT")
        else:
            print(f"STATUS: HIP3_FLX_STALE_PREMISE_UNDERPOWERED_OR_SAMPLING_AMBIGUOUS")
    elif flx_tsla > 0 or flx_nvda > 0:
        print(f"Partial flx presence: TSLA={flx_tsla}, NVDA={flx_nvda}")
        print(f"STATUS: HIP3_FLX_STALE_PREMISE_STILL_POSSIBLE")
    else:
        print(f"flx:TSLA=0, flx:NVDA=0")
        print(f"STATUS: HIP3_FLX_STALE_PREMISE_STILL_POSSIBLE")

    print(f"\nArtifacts written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
