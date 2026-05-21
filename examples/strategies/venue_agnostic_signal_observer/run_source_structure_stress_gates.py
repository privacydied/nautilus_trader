"""
CLI runner for source-structure stress gates (Phase 0).

Source-only: Hawkes self-exciting volatility labels + permutation entropy
measurements on BTC/ETH tick streams.

No target returns. No forward returns. No beta-lag evaluation. No null/FDR.
No execution. No orders. No API keys. No private keys.

Safety mode: public_data_observer_only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    SourceStructureStressConfig,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    build_hawkes_stress_labels,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    build_source_return_buckets,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    build_volatility_events,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    compute_hawkes_intensity,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    compute_permutation_entropy_points,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    merge_hawkes_stress_windows,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    validate_verdict,
)
from examples.strategies.venue_agnostic_signal_observer.source_structure_stress_gates import (
    write_source_structure_stress_artifacts,
)
from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite


# Forbidden source symbol patterns — target assets must not be passed
_TARGET_SYMBOLS = frozenset({"SOLUSDT", "LINKUSDT", "DOGEUSDT", "AVAXUSDT", "solusdt", "linkusdt", "dogeusdt", "avaxusdt"})
_SOURCE_SYMBOLS = frozenset({"BTCUSDT", "ETHUSDT", "btcusdt", "ethusdt"})


def _load_ticks_from_jsonl(path: Path) -> list[TradeTickLite]:
    """Load trade ticks from a JSONL file."""
    ticks = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            ticks.append(TradeTickLite.from_dict(d))
    return ticks


def _check_forbidden_symbols(symbols: list[str]) -> None:
    """Reject target-style symbol arguments."""
    forbidden = [s for s in symbols if s in _TARGET_SYMBOLS]
    if forbidden:
        raise SystemExit(
            f"TARGET_INPUT_FORBIDDEN_FOR_SOURCE_STRUCTURE_GATES: "
            f"target symbols {forbidden} are not allowed. "
            f"Only source symbols (BTCUSDT, ETHUSDT) permitted."
        )


def _check_forbidden_args(args: argparse.Namespace) -> None:
    """Reject target-related CLI arguments."""
    forbidden_args = []
    for attr in ("target_symbols", "target_files", "forward_returns", "beta_lag"):
        if hasattr(args, attr) and getattr(args, attr):
            forbidden_args.append(attr)
    if forbidden_args:
        raise SystemExit(
            f"TARGET_INPUT_FORBIDDEN_FOR_SOURCE_STRUCTURE_GATES: "
            f"forbidden args: {forbidden_args}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Source-structure stress gates (Phase 0) — Hawkes + permutation entropy on BTC/ETH source ticks.",
    )

    parser.add_argument(
        "--source-files",
        nargs="+",
        type=Path,
        required=True,
        help="Path(s) to source tick JSONL files (BTCUSDT, ETHUSDT only).",
    )
    parser.add_argument(
        "--source-symbols",
        nargs="+",
        default=None,
        help="Source symbols for the input files. Inferred from filenames if not provided.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Output directory for artifacts.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        default=False,
        help="Compute summary only; skip writing large JSONL files.",
    )

    # Bucket config
    parser.add_argument("--bucket-size-seconds", type=float, default=1.0)

    # Hawkes config
    parser.add_argument("--hawkes-event-threshold-bps", type=float, default=5.0)
    parser.add_argument("--hawkes-tau-seconds", type=float, default=30.0)
    parser.add_argument("--hawkes-branching-ratio", type=float, default=0.5)
    parser.add_argument("--hawkes-min-events", type=int, default=30)
    parser.add_argument("--hawkes-intensity-multiple-threshold", type=float, default=3.0)
    parser.add_argument("--hawkes-prior-event-lookback-seconds", type=float, default=60.0)
    parser.add_argument("--hawkes-min-prior-events", type=int, default=3)

    # Entropy config
    parser.add_argument("--entropy-embedding-dim", type=int, default=3)
    parser.add_argument("--entropy-delay-buckets", type=int, default=1)
    parser.add_argument("--entropy-window-seconds", type=int, default=120)
    parser.add_argument("--entropy-min-patterns", type=int, default=30)

    # Label config
    parser.add_argument("--label-cooldown-seconds", type=float, default=30.0)
    parser.add_argument("--merge-gap-seconds", type=float, default=30.0)

    # Safety: explicitly reject target-related flags
    parser.add_argument("--target-symbols", nargs="*", default=None, help="FORBIDDEN: target symbols are not allowed.")
    parser.add_argument("--target-files", nargs="*", default=None, help="FORBIDDEN: target files are not allowed.")

    return parser.parse_args(argv)


def _infer_symbol(filepath: Path) -> str:
    """Infer symbol from filename."""
    stem = filepath.stem.lower()
    if "btc" in stem:
        return "BTCUSDT"
    if "eth" in stem:
        return "ETHUSDT"
    return filepath.stem.upper()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Safety: reject target inputs
    _check_forbidden_args(args)
    if args.target_symbols:
        raise SystemExit("TARGET_INPUT_FORBIDDEN_FOR_SOURCE_STRUCTURE_GATES")
    if args.target_files:
        raise SystemExit("TARGET_INPUT_FORBIDDEN_FOR_SOURCE_STRUCTURE_GATES")

    # Determine symbols
    symbols = args.source_symbols or [_infer_symbol(p) for p in args.source_files]
    _check_forbidden_symbols(symbols)

    # Build config
    config = SourceStructureStressConfig(
        bucket_size_seconds=args.bucket_size_seconds,
        hawkes_event_threshold_bps=args.hawkes_event_threshold_bps,
        hawkes_tau_seconds=args.hawkes_tau_seconds,
        hawkes_branching_ratio=args.hawkes_branching_ratio,
        hawkes_min_events=args.hawkes_min_events,
        hawkes_intensity_multiple_threshold=args.hawkes_intensity_multiple_threshold,
        hawkes_prior_event_lookback_seconds=args.hawkes_prior_event_lookback_seconds,
        hawkes_min_prior_events=args.hawkes_min_prior_events,
        entropy_embedding_dim=args.entropy_embedding_dim,
        entropy_delay_buckets=args.entropy_delay_buckets,
        entropy_window_seconds=args.entropy_window_seconds,
        entropy_min_patterns=args.entropy_min_patterns,
        label_cooldown_seconds=args.label_cooldown_seconds,
        merge_gap_seconds=args.merge_gap_seconds,
    )

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Source-structure stress gates v0")
    print(f"  Config hash: {config.config_hash()}")
    print(f"  Source symbols: {symbols}")
    print(f"  Files: {[str(p) for p in args.source_files]}")
    print(f"  Output: {out_dir}")

    # Load all source ticks
    all_ticks: list[TradeTickLite] = []
    for fp in args.source_files:
        if not fp.exists():
            print(f"  WARNING: {fp} not found, skipping")
            continue
        ticks = _load_ticks_from_jsonl(fp)
        print(f"  Loaded {len(ticks)} ticks from {fp.name}")
        all_ticks.extend(ticks)

    if not all_ticks:
        print("  ERROR: No source ticks loaded")
        print("  Verdict: SOURCE_INPUT_UNUSABLE")
        return 1

    all_ticks.sort(key=lambda t: t.ts_event)
    print(f"  Total source ticks: {len(all_ticks)}")

    # A. Build return buckets
    buckets = build_source_return_buckets(all_ticks, bucket_size_seconds=config.bucket_size_seconds)
    print(f"  Return buckets: {len(buckets)}")

    if not buckets:
        print("  Verdict: SOURCE_INPUT_UNUSABLE")
        return 1

    # B. Build volatility events
    events = build_volatility_events(buckets, threshold_bps=config.hawkes_event_threshold_bps)
    print(f"  Volatility events: {len(events)}")

    # C. Compute Hawkes intensity
    hawkes_points = compute_hawkes_intensity(events, config)
    print(f"  Hawkes intensity points: {len(hawkes_points)}")

    # D. Compute permutation entropy
    entropy_points = compute_permutation_entropy_points(buckets, config)
    print(f"  Entropy points: {len(entropy_points)}")

    # E. Build Hawkes stress labels
    if len(events) < config.hawkes_min_events:
        hawkes_verdict = "HAWKES_INSUFFICIENT_SOURCE_EVENTS"
        labels = []
        print(f"  {hawkes_verdict}: {len(events)} < {config.hawkes_min_events}")
    else:
        labels = build_hawkes_stress_labels(events, hawkes_points, entropy_points, config)
        if labels:
            hawkes_verdict = "HAWKES_STRESS_LABELS_READY"
        else:
            hawkes_verdict = "NO_HAWKES_STRESS_LABELS"
        print(f"  Hawkes stress labels: {len(labels)}")

    # Validate verdict
    validate_verdict(hawkes_verdict)

    # F. Merge windows
    windows = merge_hawkes_stress_windows(labels, merge_gap_seconds=config.merge_gap_seconds)
    print(f"  Hawkes stress windows: {len(windows)}")

    # Entropy verdict
    computed_ent = [e for e in entropy_points if e.status == "computed"]
    if computed_ent:
        entropy_verdict = "ENTROPY_MEASUREMENTS_READY"
    else:
        entropy_verdict = "ENTROPY_INSUFFICIENT_PATTERNS"
    validate_verdict(entropy_verdict)

    # Write artifacts
    if args.summary_only:
        print("\n  --summary-only mode: writing summary only")
        from examples.strategies.venue_agnostic_signal_observer.artifact_metadata import (
            build_metadata,
        )

        ent_norms = [e.normalized_entropy for e in computed_ent]
        summary = {
            "study_id": "source_structure_stress_gates_v0",
            "source_symbols": symbols,
            "config_hash": config.config_hash(),
            "total_buckets": len(buckets),
            "total_volatility_events": len(events),
            "total_hawkes_labels": len(labels),
            "total_hawkes_windows": len(windows),
            "total_entropy_points": len(entropy_points),
            "entropy_min_normalized": min(ent_norms) if ent_norms else None,
            "entropy_max_normalized": max(ent_norms) if ent_norms else None,
            "entropy_mean_normalized": sum(ent_norms) / len(ent_norms) if ent_norms else None,
            "hawkes_verdict": hawkes_verdict,
            "entropy_verdict": entropy_verdict,
            "_metadata": build_metadata(capture_mode="PHASE0_OBSERVER", run_args=args),
        }
        summary_path = out_dir / "source_structure_stress_summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"  Summary written to {summary_path}")
    else:
        result = write_source_structure_stress_artifacts(
            out_dir=out_dir,
            buckets=buckets,
            events=events,
            hawkes_points=hawkes_points,
            entropy_points=entropy_points,
            labels=labels,
            windows=windows,
            config=config,
            hawkes_verdict=hawkes_verdict,
            entropy_verdict=entropy_verdict,
            run_args=args,
        )
        print("\n  Artifacts written:")
        for key, path in result.items():
            if isinstance(path, str):
                print(f"    {key}: {path}")
        summary_data = result.get("summary", {})
        print("\n  Verdicts:")
        print(f"    Hawkes: {summary_data.get('hawkes_verdict')}")
        print(f"    Entropy: {summary_data.get('entropy_verdict')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
