#!/usr/bin/env python3
"""CLI runner for HIP-3 Builder-DEX TradFi Forward Recorder v0.

Usage:
  uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0 \
    --out-root reports/hip3_builder_dex_tradfi_forward_recorder_v0 \
    --symbols TSLA,AAPL,MSFT,NVDA \
    --poll-seconds 60 \
    --duration-minutes 1440 \
    --allow-network-public \
    --enable-anchors \
    --anchor-source yahoo

One-shot smoke:
  uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0 \
    --out-root reports/hip3_builder_dex_tradfi_forward_recorder_v0 \
    --symbols TSLA,AAPL,MSFT,NVDA \
    --poll-seconds 60 \
    --once \
    --allow-network-public \
    --enable-anchors \
    --anchor-source yahoo

Summarize:
  uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_builder_dex_tradfi_forward_recorder_v0 \
    --summarize reports/hip3_builder_dex_tradfi_forward_recorder_v0/<run_id>
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.hip3_builder_dex_tradfi_forward_recorder_v0 import (
    ForwardRecorderConfig,
    run_forward_recorder,
    summarize_forward_capture,
    _now_utc,
    _git_info,
    _write_json_atomic,
)


def main():
    parser = argparse.ArgumentParser(description="HIP-3 Builder-DEX TradFi Forward Recorder v0")
    parser.add_argument("--out-root", default="reports/hip3_builder_dex_tradfi_forward_recorder_v0")
    parser.add_argument("--symbols", default="TSLA,AAPL,MSFT,NVDA")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--duration-minutes", type=int, default=1440)
    parser.add_argument("--max-runtime-seconds", type=int, default=None)
    parser.add_argument("--max-records", type=int, default=None)
    parser.add_argument("--allow-network-public", action="store_true")
    parser.add_argument("--enable-anchors", action="store_true")
    parser.add_argument("--anchor-source", default="yahoo")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--stop-after-init", action="store_true")
    parser.add_argument("--include-secondary-symbols", action="store_true")
    parser.add_argument("--resolution-policy", default="capture_all",
                        choices=["capture_all", "canonical_by_liquidity"],
                        help="Multi-DEX symbol resolution policy (default: capture_all)")
    parser.add_argument("--summarize", type=str, default=None)
    args = parser.parse_args()

    # Summarize mode
    if args.summarize:
        summary = summarize_forward_capture(args.summarize)
        print(json.dumps(summary, indent=2, default=str))
        print(f"\nSummary written to: {args.summarize}/forward_capture_summary.json")
        print(f"Markdown written to: {args.summarize}/forward_capture_summary.md")
        return

    symbols = [s.strip() for s in args.symbols.split(",")]
    if args.include_secondary_symbols:
        from examples.strategies.venue_agnostic_signal_observer.hip3_builder_dex_tradfi_forward_recorder_v0 import SECONDARY_SYMBOLS
        symbols = list(set(symbols + SECONDARY_SYMBOLS))

    run_id = args.run_id if hasattr(args, 'run_id') and args.run_id else uuid.uuid4().hex[:8]
    config = ForwardRecorderConfig(
        out_root=args.out_root,
        symbols=symbols,
        include_secondary=args.include_secondary_symbols,
        poll_seconds=args.poll_seconds,
        duration_minutes=args.duration_minutes,
        max_runtime_seconds=args.max_runtime_seconds,
        max_records=args.max_records,
        allow_network_public=args.allow_network_public,
        enable_anchors=args.enable_anchors,
        anchor_source=args.anchor_source,
        dry_run=args.dry_run,
        resolution_policy=args.resolution_policy,
        once=args.once,
        stop_after_init=args.stop_after_init,
        run_id=run_id,
    )

    result = run_forward_recorder(config)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
