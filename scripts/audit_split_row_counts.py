#!/usr/bin/env python3
"""Audit the 721-row split behavior."""
import pandas as pd

bars = pd.read_parquet("reports/2025_window_smoke_v0/ml_atr_2025_window_tranche2_bars_20260529T074946Z/hyperliquid_btc_eth_1h_bars.parquet")
bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)

windows = {
    "train": ("2025-08-01", "2025-08-31"),
    "validation": ("2026-01-01", "2026-01-31"),
    "test": ("2026-03-01", "2026-03-31"),
}

for split_name, (start, end) in windows.items():
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23, minutes=59)
    for sym in ["BTC", "ETH"]:
        mask = (bars["symbol"] == sym) & (bars["timestamp"] >= s) & (bars["timestamp"] <= e)
        count = mask.sum()
        print(f"{sym} {split_name}: {count} rows (expected 744 for 31 days)")

# Check for gaps
for split_name, (start, end) in windows.items():
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23, minutes=59)
    expected_hours = pd.date_range(s, e, freq="h")
    for sym in ["BTC", "ETH"]:
        mask = (bars["symbol"] == sym) & (bars["timestamp"] >= s) & (bars["timestamp"] <= e)
        actual_hours = set(bars.loc[mask, "timestamp"])
        missing = expected_hours[~expected_hours.isin(actual_hours)]
        if len(missing) > 0:
            print(f"{sym} {split_name} MISSING: {len(missing)} hours")
            for m in sorted(missing):
                print(f"  {m}")
        else:
            print(f"{sym} {split_name}: no gaps")
