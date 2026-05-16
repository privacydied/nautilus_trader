"""CLI runner for lead/lag heatmap diagnostics.

Reads existing capture data only. No network, no orders, no live trading.
Diagnostic only: does not create candidates or alter evaluator verdicts.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .lead_lag_heatmap_gpu import (
    compute_lead_lag_heatmap,
    write_heatmap_reports,
    check_cuda_available,
)
from .gpu_devices import parse_cuda_devices, validate_cuda_devices
from .run_derivatives_spot_lead_lag import load_capture_data, _split_strings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Lead/lag heatmap diagnostics runner.")
    p.add_argument("--capture-dir", type=str, required=True,
                   help="Path to capture data directory.")
    p.add_argument("--report-dir", type=str, default="",
                   help="Optional path to evaluated report directory for signal groups.")
    p.add_argument("--out", type=str, required=True,
                   help="Output directory for heatmap reports.")
    p.add_argument("--engine", type=str, default="cpu", choices=["cpu", "gpu"],
                   help="Computation engine: 'cpu' (default) or 'gpu'. No fallback.")
    p.add_argument("--device", type=str, default="cuda:0",
                   help="CUDA device for --engine gpu. Default: cuda:0.")
    p.add_argument("--devices", type=str, default="",
                   help="Multi-GPU: comma-separated CUDA devices, e.g. cuda:0,cuda:1. "
                        "Takes precedence over --device. Lag pairs are sharded across devices. "
                        "If any device is unavailable, emits GPU_UNAVAILABLE_DIAGNOSTIC and exits.")
    p.add_argument("--batch-size", type=int, default=8192,
                   help="GPU batch size. Default: 8192.")
    p.add_argument("--lags-ms", type=str, default="100,250,500,1000,2000,5000,10000,30000",
                   help="Comma-separated lag values in milliseconds.")
    p.add_argument("--bucket-ms", type=int, default=250,
                   help="Resampling bucket size in milliseconds. Default: 250.")
    p.add_argument("--min-samples", type=int, default=50,
                   help="Minimum sample count for LEAD_LAG_DIAGNOSTIC_READY verdict. Default: 50.")
    p.add_argument("--source-venues", type=str, default="binance_perp")
    p.add_argument("--target-venues", type=str, default="kraken,coinbase")
    p.add_argument("--symbols", type=str, default="BTC/USD,ETH/USD,SOL/USD")
    p.add_argument("--signal-type", type=str, default="lead_lag",
                   help="Label for signal_type column. Default: lead_lag.")
    return p


def main() -> int:
    args = build_parser().parse_args()

    # GPU availability check — fail fast, no silent fallback.
    # --devices overrides --device; validate all requested devices up front.
    import sys as _sys
    heatmap_devices: list[str] | None = None
    if args.engine == "gpu":
        raw_devices = getattr(args, "devices", "")
        if raw_devices and raw_devices.strip():
            try:
                heatmap_devices = parse_cuda_devices(raw_devices, fallback_device=args.device, engine="gpu")
            except ValueError as exc:
                print(f"ERROR: invalid --devices: {exc}", file=_sys.stderr)
                _sys.exit(1)
            ok, reason = validate_cuda_devices(heatmap_devices)
            if not ok:
                print(
                    f"ERROR: --devices unavailable: {reason}",
                    file=_sys.stderr,
                )
                print(
                    f'{{"verdict": "GPU_UNAVAILABLE_DIAGNOSTIC", "reason": "{reason}"}}',
                    file=_sys.stderr,
                )
                _sys.exit(1)
            print(f"  [GPU] Multi-GPU enabled: {heatmap_devices}")
        else:
            ok, reason = check_cuda_available(args.device)
            if not ok:
                print(
                    f"ERROR: --engine gpu requested but CUDA unavailable: {reason}",
                    file=_sys.stderr,
                )
                print(
                    f'{{"verdict": "GPU_UNAVAILABLE_DIAGNOSTIC", "reason": "{reason}"}}',
                    file=_sys.stderr,
                )
                _sys.exit(1)

    capture_dir = Path(args.capture_dir)
    if not capture_dir.exists():
        print(f"ERROR: capture directory not found: {capture_dir}", file=__import__("sys").stderr)
        return 1

    source_venues = _split_strings(args.source_venues)
    target_venues = _split_strings(args.target_venues)
    symbols = _split_strings(args.symbols)
    lags_ms = [int(x.strip()) for x in args.lags_ms.split(",") if x.strip()]

    grouped = load_capture_data(capture_dir, source_venues, target_venues, symbols)
    if not grouped:
        print("No capture data loaded. Exiting with NO_SIGNAL_SERIES.")
        return 1

    from .symbol_aliases import resolve_symbol

    summaries = []

    for source_venue in source_venues:
        for target_venue in target_venues:
            if source_venue == target_venue:
                continue
            for symbol in symbols:
                try:
                    canon = resolve_symbol(symbol)
                except ValueError:
                    continue

                asset = canon.asset
                src_key = (source_venue, f"{asset}/USDT")
                tgt_key = (target_venue, f"{asset}/USD")

                src_ticks = grouped.get(src_key, [])
                tgt_ticks = grouped.get(tgt_key, [])

                if not src_ticks or not tgt_ticks:
                    print(f"  [SKIP] {source_venue}->{target_venue}@{asset}: no ticks")
                    continue

                src_ts = [t.ts_event for t in src_ticks]
                src_vals = [t.price for t in src_ticks]
                tgt_ts = [t.ts_event for t in tgt_ticks]
                tgt_vals = [t.price for t in tgt_ticks]

                print(f"  [HEATMAP] {source_venue}->{target_venue}@{asset}: "
                      f"src={len(src_ticks)} tgt={len(tgt_ticks)}")

                summary = compute_lead_lag_heatmap(
                    source_timestamps=src_ts,
                    source_values=src_vals,
                    target_timestamps=tgt_ts,
                    target_values=tgt_vals,
                    lags_ms=lags_ms,
                    bucket_ms=args.bucket_ms,
                    engine=args.engine,
                    device=args.device,
                    batch_size=args.batch_size,
                    min_samples=args.min_samples,
                    source_venue=source_venue,
                    target_venue=target_venue,
                    symbol=f"{asset}/USD",
                    signal_type=args.signal_type,
                    devices=heatmap_devices,
                )
                summaries.append(summary)

    if not summaries:
        print("No heatmap summaries produced.")
        return 1

    # Merge summaries
    all_rows = []
    for s in summaries:
        all_rows.extend(s.rows)

    # Use first summary as base, override rows and verdict
    merged = summaries[0]
    merged.rows = all_rows
    if any(r["verdict"] == "LEAD_LAG_DIAGNOSTIC_READY" for r in all_rows):
        merged.verdict = "LEAD_LAG_DIAGNOSTIC_READY"
    else:
        merged.verdict = "INSUFFICIENT_SAMPLES"
    merged.reason_counts = {k: sum(1 for r in all_rows if r["verdict"] == k) for k in {r["verdict"] for r in all_rows}}

    write_heatmap_reports(merged, args.out)

    print()
    print(f"HEATMAP VERDICT: {merged.verdict}")
    print(f"  Total rows: {len(all_rows)}")
    print(f"  Diagnostic-ready rows: {sum(1 for r in all_rows if r['verdict'] == 'LEAD_LAG_DIAGNOSTIC_READY')}")
    print(f"  Reports: {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())