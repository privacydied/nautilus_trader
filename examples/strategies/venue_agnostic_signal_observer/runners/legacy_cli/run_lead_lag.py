"""CLI runner for the real-data lead-lag experiment.

Sweeps source→target venue pairs across lookback windows and move thresholds.
Produces forward return reports and compares against a random baseline.

Usage:
    # Download data first (one-time)
    uv run -m examples.strategies.venue_agnostic_signal_observer.data_download

    # Run the sweep
    uv run -m examples.strategies.venue_agnostic_signal_observer.run_lead_lag \
        --data-dir data/lead_lag \
        --source-venues BINANCE,KRAKEN \
        --source-assets BTC/USDT,ETH/USDT,SOL/USDT \
        --target-venues KRAKEN,COINBASE \
        --target-assets BTC/USD,ETH/USD,SOL/USD \
        --grid-seconds 10 \
        --output-dir reports/lead_lag_v1

The sweep generates a JSON report per source→target pair plus an aggregate
summary.  Each report includes the real signal results and the random baseline
for comparison.
"""

from __future__ import annotations

import argparse
import math
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from examples.strategies.venue_agnostic_signal_observer.config import ObserverConfig, FeeModel, Horizon
from examples.strategies.venue_agnostic_signal_observer.data_loading import load_bars_from_csv
from examples.strategies.venue_agnostic_signal_observer.forward_returns import evaluate_signal
from examples.strategies.venue_agnostic_signal_observer.lead_lag import generate_lead_lag_signals, generate_random_baseline
from examples.strategies.venue_agnostic_signal_observer.models import ForwardReturnResult, SignalEvaluationSummary


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run a lead-lag cross-venue forward-return sweep."
    )
    p.add_argument(
        "--data-dir",
        type=str,
        default="data/lead_lag",
        help="Directory containing venue CSVs (venue_asset.csv).",
    )
    p.add_argument(
        "--source-venues",
        type=str,
        default="BINANCE",
        help="Comma-separated source venues to test as the leader.",
    )
    p.add_argument(
        "--source-assets",
        type=str,
        default="BTC/USDT,ETH/USDT,SOL/USDT",
        help="Comma-separated asset symbols for source venues.",
    )
    p.add_argument(
        "--target-venues",
        type=str,
        default="KRAKEN",
        help="Comma-separated target venues to test as the follower.",
    )
    p.add_argument(
        "--target-assets",
        type=str,
        default="BTC/USD,ETH/USD,SOL/USD",
        help="Comma-separated asset symbols for target venues.",
    )
    p.add_argument(
        "--lookbacks",
        type=str,
        default="10,30,60",
        help="Comma-separated lookback windows in seconds.",
    )
    p.add_argument(
        "--thresholds",
        type=str,
        default="5,10,20",
        help="Comma-separated move thresholds in basis points.",
    )
    p.add_argument(
        "--cooldown",
        type=float,
        default=30.0,
        help="Cooldown between signals in seconds.",
    )
    p.add_argument(
        "--horizons",
        type=str,
        default="10,30,60,300",
        help="Comma-separated forward-return horizons in seconds.",
    )
    p.add_argument(
        "--fee-bps",
        type=float,
        default=10.0,
        help="Target venue taker fee in basis points.",
    )
    p.add_argument(
        "--slippage-bps",
        type=float,
        default=2.0,
        help="Slippage buffer in basis points.",
    )
    p.add_argument(
        "--grid-seconds",
        type=float,
        default=10.0,
        help="Grid alignment interval in seconds (resample period).",
    )
    p.add_argument(
        "--output-dir",
        type=str,
        default="reports/lead_lag_v1",
        help="Output directory for JSON reports and summary.",
    )
    p.add_argument(
        "--skip-baseline",
        action="store_true",
        help="Skip the random baseline comparison.",
    )
    p.add_argument(
        "--baseline-seed",
        type=int,
        default=42,
        help="Random seed for baseline reproducibility.",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_csv(data_dir: str, venue: str, asset: str) -> str | None:
    """Find a CSV matching venue+asset, tolerating date-suffix variants."""
    safe_asset = asset.replace("/", "_")
    prefix = f"{venue.lower()}_{safe_asset.lower()}"
    data_path = Path(data_dir)
    # First try exact match
    exact = data_path / f"{prefix}.csv"
    if exact.exists():
        return str(exact)
    # Then try glob for date-suffix variants
    candidates = sorted(data_path.glob(f"{prefix}_*.csv"))
    if candidates:
        return str(candidates[-1])  # most recent
    return None


def _safe_stats(values: list[float]) -> dict[str, float | None]:
    """Compute stats for a list of net returns."""
    if not values:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "p25": None,
            "p75": None,
            "win_rate": None,
            "best": None,
            "worst": None,
        }
    s = sorted(values)
    n = len(s)
    wins = sum(1 for v in s if v > 0)
    return {
        "count": n,
        "mean": round(statistics.mean(s), 4),
        "median": round(statistics.median(s), 4) if n > 0 else None,
        "p25": round(s[n // 4], 4) if n >= 4 else round(s[0], 4),
        "p75": round(s[3 * n // 4], 4) if n >= 4 else round(s[-1], 4),
        "win_rate": round(wins / n, 4),
        "best": round(max(s), 4),
        "worst": round(min(s), 4),
    }


# ---------------------------------------------------------------------------
# Core sweep logic
# ---------------------------------------------------------------------------

def run_sweep(args: argparse.Namespace):
    """Run the full lead-lag sweep and write reports."""
    source_venues = [v.strip() for v in args.source_venues.split(",")]
    source_assets = [a.strip() for a in args.source_assets.split(",")]
    target_venues = [v.strip() for v in args.target_venues.split(",")]
    target_assets = [a.strip() for a in args.target_assets.split(",")]
    lookbacks = [float(x) for x in args.lookbacks.split(",")]
    thresholds = [float(x) for x in args.thresholds.split(",")]
    horizons = [Horizon(f"{int(s)}s", s) for s in [float(x) for x in args.horizons.split(",")]]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fee_model = FeeModel(
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        quote_mismatch_buffer_bps=0.0,
    )

    run_meta = {
        "start_utc": datetime.now(timezone.utc).isoformat(),
        "source_venues": source_venues,
        "source_assets": source_assets,
        "target_venues": target_venues,
        "target_assets": target_assets,
        "lookbacks": lookbacks,
        "thresholds": thresholds,
        "horizons": [h.name for h in horizons],
        "cooldown_seconds": args.cooldown,
        "fee_bps": args.fee_bps,
        "slippage_bps": args.slippage_bps,
        "grid_seconds": args.grid_seconds,
    }

    pair_results: list[dict[str, Any]] = []
    overall_summary: list[dict[str, Any]] = []

    for sv in source_venues:
        for sa in source_assets:
            for tv in target_venues:
                for ta in target_assets:
                    label = f"{sv}({sa}) → {tv}({ta})"

                    # Match assets by underlying (BTC→BTC, ETH→ETH, etc.)
                    src_base = sa.split("/")[0].upper().replace("XBT", "BTC")
                    tgt_base = ta.split("/")[0].upper().replace("XBT", "BTC")
                    if src_base != tgt_base:
                        print(f"  Skip {label}: asset mismatch ({src_base} vs {tgt_base})")
                        continue

                    src_csv = _find_csv(args.data_dir, sv, sa)
                    tgt_csv = _find_csv(args.data_dir, tv, ta)

                    if src_csv is None or tgt_csv is None:
                        missing = []
                        if src_csv is None:
                            missing.append(f"source: {sv}/{sa}")
                        if tgt_csv is None:
                            missing.append(f"target: {tv}/{ta}")
                        print(f"  Skip {label}: missing CSV ({', '.join(missing)})")
                        continue

                    print(f"\n{'='*60}")
                    print(f"Pair: {label}")
                    print(f"  Source CSV: {src_csv}")
                    print(f"  Target CSV: {tgt_csv}")

                    try:
                        src_ts, src_prices = load_bars_from_csv(src_csv)
                        tgt_ts, tgt_prices = load_bars_from_csv(tgt_csv)
                    except Exception as e:
                        print(f"  Load error: {e}")
                        continue

                    if not src_ts or not tgt_ts:
                        print("  No data loaded after alignment — skipping.")
                        continue

                    # Align to common grid
                    aligned_ts, aligned_src, aligned_tgt = _align(
                        src_ts, src_prices, tgt_ts, tgt_prices,
                        interval=args.grid_seconds,
                    )

                    if len(aligned_ts) < 100:
                        print(f"  Alignment produced only {len(aligned_ts)} shared bars — skipping.")
                        continue

                    print(f"  Align  ed to {len(aligned_ts)} shared bars (grid={args.grid_seconds}s)")

                    # Sweep lookback × threshold
                    sweep_results: list[dict[str, Any]] = []

                    for lb in lookbacks:
                        for thr in thresholds:
                            signals = generate_lead_lag_signals(
                                source_timestamps=aligned_ts,
                                source_prices=aligned_src,
                                source_venue=sv,
                                source_instrument=sa,
                                target_venue=tv,
                                target_instrument=ta,
                                lookback_seconds=lb,
                                move_threshold_bps=thr,
                                cooldown_seconds=args.cooldown,
                            )

                            all_fwd: list[ForwardReturnResult] = []
                            for sig in signals:
                                fwd = evaluate_signal(sig, aligned_ts, aligned_tgt, horizons, fee_model)
                                all_fwd.extend(fwd)

                            valid = [r for r in all_fwd if r.valid]
                            nets = [r.net_return_bps for r in valid if r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
                            stats = _safe_stats(nets)

                            entry = {
                                "lookback_s": lb,
                                "threshold_bps": thr,
                                "signal_count": len(signals),
                                "valid_evaluations": len(valid),
                                "net_return_stats": stats,
                            }
                            sweep_results.append(entry)

                            # Acceptance gate check
                            verdict = "pending"
                            if stats["count"] >= 10 and stats["mean"] is not None:
                                if stats["mean"] > 0 and (stats["median"] or 0) > -5:
                                    verdict = "PASS"
                                else:
                                    verdict = "FAIL"
                            entry["verdict"] = verdict

                            print(
                                f"  lb={lb:5.0f}s  thr={thr:5.0f}bps  "
                                f"signals={len(signals):5d}  "
                                f"mean_net={stats['mean']:>7.2f}bps  "
                                f"median_net={stats['median']:>7.2f}bps  "
                                f"win={stats['win_rate'] or 0:>5.1%}  "
                                f"verdict={verdict}"
                            )

                    # Random baseline
                    baseline_results: dict[str, Any] | None = None
                    if not args.skip_baseline:
                        total_signals = sum(e["signal_count"] for e in sweep_results)
                        if total_signals > 0:
                            baseline_signals = generate_random_baseline(
                                source_timestamps=aligned_ts,
                                signal_count=total_signals,
                                source_venue=sv,
                                source_instrument=sa,
                                target_venue=tv,
                                target_instrument=ta,
                                seed=args.baseline_seed,
                            )

                            baseline_fwd: list[ForwardReturnResult] = []
                            for sig in baseline_signals:
                                fwd = evaluate_signal(sig, aligned_ts, aligned_tgt, horizons, fee_model)
                                baseline_fwd.extend(fwd)

                            b_valid = [r for r in baseline_fwd if r.valid]
                            b_nets = [r.net_return_bps for r in b_valid if r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
                            b_stats = _safe_stats(b_nets)
                            baseline_results = {
                                "signal_count": len(baseline_signals),
                                "valid_evaluations": len(b_valid),
                                "net_return_stats": b_stats,
                            }
                            print(
                                f"  BASELINE                          "
                                f"signals={baseline_results['signal_count']:5d}  "
                                f"mean_net={b_stats['mean']:>7.2f}bps  "
                                f"median_net={b_stats['median']:>7.2f}bps  "
                                f"win={b_stats['win_rate'] or 0:>5.1%}"
                            )

                    pair_entry = {
                        "label": label,
                        "source_venue": sv,
                        "source_asset": sa,
                        "target_venue": tv,
                        "target_asset": ta,
                        "shared_bars": len(aligned_ts),
                        "sweep": sweep_results,
                        "baseline": baseline_results,
                    }
                    pair_results.append(pair_entry)

                    # Write per-pair report
                    pair_path = output_dir / f"pair_{sv.lower()}_{ta.replace('/', '_').lower()}.json"
                    with open(pair_path, "w") as f:
                        json.dump(pair_entry, f, indent=2)
                    print(f"  Report: {pair_path}")

                    # Track overall
                    best = max(
                        (e for e in sweep_results if e.get("verdict") == "PASS"),
                        key=lambda e: e["net_return_stats"]["mean"] or -999,
                        default=None,
                    )
                    overall_summary.append({
                        "label": label,
                        "best_pass": best["net_return_stats"] if best else None,
                        "total_signals": sum(e["signal_count"] for e in sweep_results),
                        "pass_count": sum(1 for e in sweep_results if e.get("verdict") == "PASS"),
                    })

    # Write aggregate summary
    summary_out = {
        "run_metadata": run_meta,
        "pairs_tested": len(pair_results),
        "overall_summary": overall_summary,
    }
    summary_path = output_dir / "summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary_out, f, indent=2, default=str)
    print(f"\n{'='*60}")
    print(f"Aggregate summary: {summary_path}")

    # Print final verdict table
    print(f"\n{'Pair':<40} {'Pass?':>6} {'Best mean net':>14} {'Signals':>8}")
    for item in overall_summary:
        best_pass = item.get("best_pass")
        best_mean = (best_pass.get("mean") if best_pass else None) or "N/A"
        pass_count = item.get("pass_count", 0)
        total = item.get("total_signals", 0)
        print(
            f"{item['label']:<40} "
            f"{pass_count:>6} "
            f"{best_mean:>14} "
            f"{total:>8}"
        )


def _align(
    ts1: list[float],
    prices1: list[float],
    ts2: list[float],
    prices2: list[float],
    interval: float,
) -> tuple[list[float], list[float], list[float]]:
    """Resample two series to a shared uniform grid using forward-fill."""
    t_start = max(ts1[0], ts2[0])
    t_end = min(ts1[-1], ts2[-1])

    grid: list[float] = []
    t = t_start
    while t <= t_end:
        grid.append(t)
        t += interval

    def ffill(query_ts: list[float], ts: list[float], prices: list[float]) -> list[float]:
        import bisect
        out: list[float] = []
        carry = prices[0]
        for qt in query_ts:
            idx = bisect.bisect_right(ts, qt) - 1
            if idx >= 0:
                carry = prices[idx]
            out.append(carry)
        return out

    return grid, ffill(grid, ts1, prices1), ffill(grid, ts2, prices2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    run_sweep(args)


if __name__ == "__main__":
    main()
