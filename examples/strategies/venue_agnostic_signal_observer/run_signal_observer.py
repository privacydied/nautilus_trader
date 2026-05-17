#!/usr/bin/env python3
"""Venue-agnostic signal observer — CLI entry point.

Usage (synthetic smoke test):
    python run_signal_observer.py --synthetic --out reports/signal_observer

Usage (real CSV data):
    python run_signal_observer.py --bars-csv data.csv --signals-csv signals.csv --out reports/signal_observer
"""
import argparse
import math
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from examples.strategies.venue_agnostic_signal_observer.config import ObserverConfig, FeeModel, Horizon, SignalSourceConfig
from examples.strategies.venue_agnostic_signal_observer.signals import load_signals_from_csv, CrossMarketSignalGenerator
from examples.strategies.venue_agnostic_signal_observer.data_loading import load_bars_from_csv, generate_synthetic_data
from examples.strategies.venue_agnostic_signal_observer.observer import SignalObserver, _build_summary
from examples.strategies.venue_agnostic_signal_observer.reports import write_outputs


def main():
    parser = argparse.ArgumentParser(description="Venue-agnostic signal observer")
    parser.add_argument("--synthetic", action="store_true", help="Run deterministic synthetic smoke test")
    parser.add_argument("--signals-csv", type=str, default=None, help="Path to signals CSV")
    parser.add_argument("--bars-csv", type=str, default=None, help="Path to target bars CSV")
    parser.add_argument("--fee-bps", type=float, default=5.0, help="Estimated fee in bps")
    parser.add_argument("--slippage-bps", type=float, default=1.0, help="Estimated slippage in bps")
    parser.add_argument("--quote-mismatch-buffer-bps", type=float, default=0.0, help="Quote currency mismatch buffer")
    parser.add_argument("--out", type=str, default="reports/signal_observer")
    args = parser.parse_args()

    cfg = ObserverConfig(
        fee_model=FeeModel(
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps,
        ),
        signal_source=SignalSourceConfig(),
        output_dir=args.out,
        synthetic=args.synthetic,
    )

    observer = SignalObserver(cfg)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.synthetic:
        print("Running synthetic smoke test...")
        ts, source_prices, target_prices = generate_synthetic_data(
            num_bars=5_000,
            dt_seconds=10.0,
            base_price=50_000.0,
            target_follow_delay=30.0,
            target_follow_fraction=0.7,
            jump_threshold_bps=50.0,
            jump_interval=200,
        )
        gen = CrossMarketSignalGenerator(cfg.signal_source)
        signals = gen.generate(ts, source_prices)
        print(f"Generated {len(signals)} synthetic signals")

        signals_list, results, summary = observer.run(
            signals=signals,
            target_timestamps=ts,
            target_prices=target_prices,
        )

    elif args.signals_csv and args.bars_csv:
        signals_list, results, summary = observer.run(
            signals_csv=args.signals_csv,
            bars_csv=args.bars_csv,
        )
    else:
        parser.error("Use --synthetic or provide --signals-csv + --bars-csv")
        sys.exit(1)

    # Write outputs
    signals_dicts = [s.to_dict() for s in signals_list]
    write_outputs(signals_dicts, results, summary, out_dir)

    # Print summary
    net_values = [r.net_return_bps for r in results if r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
    mean_net = sum(net_values) / len(net_values) if net_values else 0
    print(f"\nObserver Summary:")
    print(f"  Signals: {summary.total_signals}")
    print(f"  Valid evaluations: {summary.valid_evaluations}")
    print(f"  Rejected evaluations: {summary.rejected_evaluations}")
    print(f"  Mean net return: {mean_net:.4f} bps")
    print(f"  Recommendation: {summary.final_recommendation}")
    print(f"\nOutputs written to {out_dir}")


if __name__ == "__main__":
    main()
