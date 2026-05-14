"""CLI runner for lead/lag heatmap diagnostics.

Reads existing capture data only. No network, no orders, no live trading.
Diagnostic only: does not create candidates or alter evaluator verdicts.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .lead_lag_heatmap_gpu import (
    check_cuda_available,
    compute_lead_lag_heatmap,
    write_heatmap_reports,
)
from .run_derivatives_spot_lead_lag import load_capture_data, _split_strings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Lead/lag heatmap diagnostics runner.")
    p.add_argument("--capture-dir", type=str, required=True)
    p.add_argument("--report-dir", type=str, default="")
    p.add_argument("--out", type=str, required=True)
    p.add_argument("--engine", type=str, default="cpu", choices=["cpu", "gpu"])
    p.add_argument("--device", type=str, default="cuda:0")
    p.add_argument("--batch-size", type=int, default=8192)
    p.add_argument("--lags-ms", type=str, default="100,250,500,1000,2000,5000,10000,30000")
    p.add_argument("--bucket-ms", type=int, default=250)
    p.add_argument("--min-samples", type=int, default=50)
    p.add_argument("--source-venues", type=str, default="binance_perp")
    p.add_argument("--target-venues", type=str, default="kraken,coinbase")
    p.add_argument("--symbols", type=str, default="BTC/USD,ETH/USD,SOL/USD")
    p.add_argument("--signal-type", type=str, default="lead_lag")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.engine == "gpu":
        ok, reason = check_cuda_available(args.device)
        if not ok:
            raise SystemExit(f"GPU_UNAVAILABLE_DIAGNOSTIC: {reason}")
    capture_dir = Path(args.capture_dir)
    grouped = load_capture_data(capture_dir, _split_strings(args.source_venues), _split_strings(args.target_venues), _split_strings(args.symbols))
    if not grouped:
        raise SystemExit("NO_SIGNAL_SERIES")
    any_key = next(iter(grouped))
    ticks = grouped[any_key]
    ts = [t.ts_event for t in ticks]
    vals = [t.price for t in ticks]
    summary = compute_lead_lag_heatmap(
        source_timestamps=ts,
        source_values=vals,
        target_timestamps=ts,
        target_values=vals,
        lags_ms=[int(x) for x in args.lags_ms.split(",") if x.strip()],
        bucket_ms=args.bucket_ms,
        engine=args.engine,
        device=args.device,
        batch_size=args.batch_size,
        min_samples=args.min_samples,
        source_venue=any_key[0],
        target_venue=any_key[0],
        symbol=any_key[1],
        signal_type=args.signal_type,
    )
    write_heatmap_reports(summary, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
