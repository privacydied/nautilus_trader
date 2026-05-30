#!/usr/bin/env python3
"""Positive-control validation for FLX oracle frequency scanner.

Runs the existing extraction pipeline against the known-control block
/tmp/test_block.lz4 where flx:BTC was manually observed.
"""

import sys
from pathlib import Path

_PKG_ROOT = str(Path(__file__).resolve().parents[5])
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from examples.strategies.venue_agnostic_signal_observer.hip3_flx_stale_oracle_funding_bias_phase_minus2_v0 import (
    _decode_lz4_json_records,
    _extract_oracle_payloads_from_record,
    _safe_float,
)

CONTROL_BLOCK = Path("/tmp/test_block.lz4")


def main():
    print(f"=== POSITIVE CONTROL VALIDATION ===")
    print(f"Control block: {CONTROL_BLOCK}")
    print(f"Control block size: {CONTROL_BLOCK.stat().st_size / 1e6:.1f} MB")
    print()

    with open(CONTROL_BLOCK, "rb") as f:
        data = f.read()

    print(f"Read {len(data) / 1e6:.1f} MB into memory")

    # Decode LZ4
    all_payloads = []
    record_count = 0
    decode_failures = 0
    flx_payloads = []
    all_dex_keys = {}

    for rec_idx, obj, raw_line in _decode_lz4_json_records(data):
        record_count += 1
        if obj is None:
            decode_failures += 1
            continue
        payloads = _extract_oracle_payloads_from_record(obj)
        for payload in payloads:
            px = payload.get("px")
            if px is None or _safe_float(px) is None:
                continue
            dex = payload.get("dex", "")
            sym = payload.get("symbol", "")
            key = f"{dex}:{sym}"
            all_dex_keys[key] = all_dex_keys.get(key, 0) + 1
            all_payloads.append(payload)
            if dex.lower() == "flx":
                flx_payloads.append(payload)

    print(f"\nRecords decoded: {record_count}")
    print(f"Decode failures: {decode_failures}")
    print(f"Total oracle payloads extracted: {len(all_payloads)}")
    print(f"\n--- All dex:symbol keys (sorted by count) ---")
    for key, count in sorted(all_dex_keys.items(), key=lambda x: -x[1]):
        print(f"  {key}: {count}")

    print(f"\n--- FLX keys found ---")
    if flx_payloads:
        flx_counts = {}
        for p in flx_payloads:
            key = f"{p['dex']}:{p['symbol']}"
            flx_counts[key] = flx_counts.get(key, 0) + 1
        for key, count in sorted(flx_counts.items()):
            print(f"  {key}: {count}")
        # Also show sample payload
        print(f"\nSample FLX payload:")
        print(f"  {flx_payloads[0]}")
    else:
        print("  NONE — no flx:* keys detected")

    # Check specific targets
    print(f"\n--- Target check ---")
    for target in ["flx:BTC", "flx:TSLA", "flx:NVDA"]:
        count = all_dex_keys.get(target, 0)
        status = "DETECTED" if count > 0 else "ABSENT"
        print(f"  {target}: {count} ({status})")

    # Positive control verdict
    btc_count = all_dex_keys.get("flx:BTC", 0)
    print(f"\n=== VERDICT ===")
    if btc_count > 0:
        print(f"POSITIVE_CONTROL_PASSED")
        print(f"flx:BTC detected with count={btc_count}")
        print(f"All flx:* keys: {list(flx_counts.keys()) if flx_payloads else []}")
        print(f"flx:TSLA count: {all_dex_keys.get('flx:TSLA', 0)}")
        print(f"flx:NVDA count: {all_dex_keys.get('flx:NVDA', 0)}")
    else:
        print(f"POSITIVE_CONTROL_FAILED")
        print(f"flx:BTC NOT detected in control block")
        print(f"All flx:* keys: {list(flx_counts.keys()) if flx_payloads else []}")

    return 0 if btc_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
