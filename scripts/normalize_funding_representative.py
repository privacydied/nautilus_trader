#!/usr/bin/env python3
"""Normalize Hyperliquid funding archive into representative parquet/csv."""
import json
import os
import pathlib

import pandas as pd


def main():
    out_dir = pathlib.Path(os.environ["REP_BAR_OUT_DIR"])
    funding_dir = pathlib.Path(
        "examples/strategies/venue_agnostic_signal_observer/data/hyperliquid_funding_archive_phase0"
    )

    rows = []
    for sym in ["BTC", "ETH"]:
        fpath = funding_dir / f"hyperliquid_funding_{sym}_2025-05-23_to_2026-05-22.jsonl"
        if not fpath.exists():
            raise SystemExit(f"missing funding file: {fpath}")
        with open(fpath) as f:
            for line in f:
                r = json.loads(line.strip())
                rows.append({
                    "timestamp": pd.Timestamp(r["timestamp_iso"]).tz_convert("UTC"),
                    "symbol": r["coin"],
                    "funding_rate": r["funding_rate"],
                })

    fund = pd.DataFrame(rows)
    fund = fund.sort_values(["symbol", "timestamp"]).reset_index(drop=True)

    windows = [
        ("2025-08-01", "2025-08-31"),
        ("2026-01-01", "2026-01-31"),
        ("2026-03-01", "2026-03-31"),
    ]
    mask = pd.Series(False, index=fund.index)
    for start, end in windows:
        s = pd.Timestamp(start, tz="UTC")
        e = pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23, minutes=59)
        mask |= (fund["timestamp"] >= s) & (fund["timestamp"] <= e)
    fund = fund[mask].copy()

    fund = fund.drop_duplicates(subset=["symbol", "timestamp"], keep="first")

    fund.to_parquet(out_dir / "hyperliquid_btc_eth_hourly_funding.parquet", index=False)
    fund.to_csv(out_dir / "hyperliquid_btc_eth_hourly_funding.csv", index=False)

    print("funding rows:", len(fund))
    print("columns:", list(fund.columns))
    for sym, g in fund.groupby("symbol"):
        print(sym, len(g), g["timestamp"].min(), g["timestamp"].max())
    print("max abs funding:", fund["funding_rate"].abs().max())


if __name__ == "__main__":
    main()
