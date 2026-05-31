#!/usr/bin/env python3
"""Audit exactly why the split produces 721 rows per symbol per split."""
import json
import pathlib
import pandas as pd

bars = pd.read_parquet("reports/2025_window_smoke_v0/ml_atr_2025_window_tranche2_bars_20260529T074946Z/hyperliquid_btc_eth_1h_bars.parquet")
bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)

# Case A: End date interpreted as midnight (what the previous diagnostic did)
# This is what happens when you pass --train-end 2025-08-31 to split_data
# which uses <= pd.Timestamp("2025-08-31", tz="UTC") = midnight
windows_midnight = {
    "train": ("2025-08-01", "2025-08-31"),
    "validation": ("2026-01-01", "2026-01-31"),
    "test": ("2026-03-01", "2026-03-31"),
}

print("=== Case A: End date as midnight (<= midnight of end date) ===")
for split_name, (start, end) in windows_midnight.items():
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC")  # This is midnight!
    for sym in ["BTC", "ETH"]:
        mask = (bars["symbol"] == sym) & (bars["timestamp"] >= s) & (bars["timestamp"] <= e)
        count = mask.sum()
        if count > 0:
            first = bars.loc[mask, "timestamp"].min()
            last = bars.loc[mask, "timestamp"].max()
            print(f"  {sym} {split_name}: {count} rows  (first={first}, last={last})")
        else:
            print(f"  {sym} {split_name}: {count} rows")

# Case B: End date interpreted as end-of-day (what we should do for real strategy)
windows_eod = {
    "train": ("2025-08-01", "2025-08-31 23:00"),
    "validation": ("2026-01-01", "2026-01-31 23:00"),
    "test": ("2026-03-01", "2026-03-31 23:00"),
}

print("\n=== Case B: End date as end-of-day (<= 23:00 of end date) ===")
for split_name, (start, end) in windows_eod.items():
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC")
    for sym in ["BTC", "ETH"]:
        mask = (bars["symbol"] == sym) & (bars["timestamp"] >= s) & (bars["timestamp"] <= e)
        count = mask.sum()
        if count > 0:
            first = bars.loc[mask, "timestamp"].min()
            last = bars.loc[mask, "timestamp"].max()
            print(f"  {sym} {split_name}: {count} rows  (first={first}, last={last})")
        else:
            print(f"  {sym} {split_name}: {count} rows")

# Write audit
audit = {
    "verdict": "REPRESENTATIVE_SPLIT_ROW_AUDIT_EXPLAINED",
    "explanation": "721 rows caused by date boundary semantics: --train-end 2025-08-31 is interpreted as midnight Aug 31 by split_data(), which uses <= comparison. This excludes Aug 31 hours 01:00-23:00 (23 rows). 744 - 23 = 721.",
    "case_a_midnight": {},
    "case_b_eod": {},
}

for split_name, (start, end) in windows_midnight.items():
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC")
    for sym in ["BTC", "ETH"]:
        mask = (bars["symbol"] == sym) & (bars["timestamp"] >= s) & (bars["timestamp"] <= e)
        audit["case_a_midnight"][f"{sym}_{split_name}"] = int(mask.sum())

for split_name, (start, end) in windows_eod.items():
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC")
    for sym in ["BTC", "ETH"]:
        mask = (bars["symbol"] == sym) & (bars["timestamp"] >= s) & (bars["timestamp"] <= e)
        audit["case_b_eod"][f"{sym}_{split_name}"] = int(mask.sum())

pathlib.Path("reports/hyperliquid_btc_eth_ml_atr_representative_real_strategy_v0").mkdir(parents=True, exist_ok=True)
(pathlib.Path("reports/hyperliquid_btc_eth_ml_atr_representative_real_strategy_v0/split_row_audit.json")).write_text(
    json.dumps(audit, indent=2, sort_keys=True)
)

print(f"\nVerdict: {audit['verdict']}")
print(json.dumps(audit, indent=2, sort_keys=True))
