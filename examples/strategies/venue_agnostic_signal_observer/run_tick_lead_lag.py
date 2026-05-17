"""CLI sweep runner for tick-level lead-lag signal research.

**RESEARCH MEASUREMENT TOOL ONLY.**  This script scans tick data for
cross-venue lead-lag relationships, computes forward returns, and
produces statistical summaries and reports.  There is no order submission,
no position tracking, no execution engine, and no live-trading code.

Usage examples::

    python -m examples.strategies.venue_agnostic_signal_observer.run_tick_lead_lag

    python -m examples.strategies.venue_agnostic_signal_observer.run_tick_lead_lag \
        --ticks data/signal_observer_ticks \
        --source-venues kraken \
        --target-venues coinbase \
        --symbols BTC/USD,ETH/USD \
        --lookbacks-ms 1000,5000,10000 \
        --thresholds-bps 2,5,10 \
        --horizons-ms 1000,5000,10000

The runner writes JSON, CSV, JSONL, and Markdown outputs into the
output directory specified by ``--out``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Local imports — these must be run from the nautilus_trader repo root or
# the package must be installed / on sys.path.
# ---------------------------------------------------------------------------
# When invoked via ``python -m …`` the package path is auto-resolved.
from .tick_models import TickForwardReturn, TickSignalEvent, TradeTickLite
from .tick_store import load_trades_jsonl, tick_file_discovery
from .event_study import (
    TickLeadLagConfig,
    TickLeadLagGenerator,
    evaluate_candidate_group,
    evaluate_tick_signal,
    generate_random_baseline,
)

_MS_TO_NS = 1_000_000

# ---------------------------------------------------------------------------
# Summary dataclass
# ---------------------------------------------------------------------------


@dataclass
class TickLeadLagSummary:
    """Aggregate summary produced by one run of the lead-lag sweep."""

    total_signals: int = 0
    valid_evaluations: int = 0
    rejected_evaluations: int = 0
    fee_bps: float = 12.0
    slippage_bps: float = 2.0
    quote_mismatch_buffer_bps: float = 5.0
    results_by_group: list[dict] = field(default_factory=list)
    baseline_results: dict | None = None
    run_start: float = 0.0
    run_end: float = 0.0
    candidate_groups: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------


def _write_json(path: str, obj: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, default=str)


def _write_jsonl(path: str, rows: list[dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")


def _write_csv(path: str, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    if not rows:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def _split_ints(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def _split_floats(s: str) -> list[float]:
    return [float(x.strip()) for x in s.split(",") if x.strip()]


def _split_strings(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI sweep runner for tick-level lead-lag signal research. "
        "No orders, no trading — purely measurement and reporting."
    )
    parser.add_argument(
        "--ticks",
        type=str,
        default="data/signal_observer_ticks",
        help="Data directory path (default: data/signal_observer_ticks)",
    )
    parser.add_argument(
        "--source-venues",
        type=str,
        default="kraken",
        help="Comma-separated source venue names (default: kraken)",
    )
    parser.add_argument(
        "--target-venues",
        type=str,
        default="coinbase",
        help="Comma-separated target venue names (default: coinbase)",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="BTC/USD",
        help="Comma-separated symbol names (default: BTC/USD)",
    )
    parser.add_argument(
        "--lookbacks-ms",
        type=str,
        default="1000,5000,10000,30000",
        help="Comma-separated lookback periods in ms (default: 1000,5000,10000,30000)",
    )
    parser.add_argument(
        "--thresholds-bps",
        type=str,
        default="2,5,10,20",
        help="Comma-separated threshold values in bps (default: 2,5,10,20)",
    )
    parser.add_argument(
        "--horizons-ms",
        type=str,
        default="1000,2000,5000,10000,30000,60000",
        help="Comma-separated forward horizons in ms (default: 1000,2000,5000,10000,30000,60000)",
    )
    parser.add_argument(
        "--cooldown-ms",
        type=int,
        default=10000,
        help="Cooldown between signals in ms (default: 10000)",
    )
    parser.add_argument(
        "--fee-bps",
        type=float,
        default=12.0,
        help="Fee cost in bps (default: 12)",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=2.0,
        help="Slippage cost in bps (default: 2)",
    )
    parser.add_argument(
        "--quote-mismatch-buffer-bps",
        type=float,
        default=5.0,
        help="Quote mismatch buffer in bps (default: 5)",
    )
    parser.add_argument(
        "--min-events",
        type=int,
        default=50,
        help="Minimum number of valid events for a group to be considered (default: 50)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="reports/signal_observer_tick_lead_lag",
        help="Output directory (default: reports/signal_observer_tick_lead_lag)",
    )
    parser.add_argument(
        "--baseline-seed",
        type=int,
        default=42,
        help="Random seed for baseline generation (default: 42)",
    )
    parser.add_argument(
        "--skip-baseline",
        action="store_true",
        default=False,
        help="Skip random baseline generation",
    )
    parser.add_argument(
        "--allow-same-venue-diagnostics",
        action="store_true",
        default=False,
        help="Include same-venue self-lead-lag comparisons (default: excluded, cross-venue only)",
    )
    return parser


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_tick_data(
    data_dir: str,
    source_venues: list[str],
    target_venues: list[str],
    symbols: list[str],
) -> dict[tuple[str, str], list[TradeTickLite]]:
    """Discover and load trade tick JSONL files, grouped by (venue, symbol).

    Tries the requested symbol first, then falls back through common aliases
    (e.g. BTC-USD also checks XBT/USD for Kraken) so that venue-specific
    naming conventions do not prevent data loading.

    Returns a dict mapping ``(venue, symbol)`` → sorted list of TradeTickLite.
    """
    from .symbol_aliases import resolve_symbol

    grouped: dict[tuple[str, str], list[TradeTickLite]] = {}
    all_venues = list(set(source_venues + target_venues))

    for venue in all_venues:
        for symbol in symbols:
            # Build candidate file-names: the requested symbol plus
            # any known aliases that might appear in filenames.
            candidates: list[str] = [symbol]
            try:
                canonical = resolve_symbol(symbol)
                alias = f"{canonical.asset}-{canonical.quote}"
                if alias != symbol:
                    candidates.append(alias)
                slash = f"{canonical.asset}/{canonical.quote}"
                if slash != symbol:
                    candidates.append(slash)
            except ValueError:
                pass

            files: list[str] = []
            for cand in candidates:
                found = tick_file_discovery(data_dir, venue, cand, tick_type="trades")
                files.extend(found)
            # Dedupe paths
            files = sorted(set(files))

            if not files:
                print(
                    f"  [INFO] No trade files found for venue={venue} symbol={symbol}"
                )
                continue

            combined: list[TradeTickLite] = []
            for fp in files:
                ticks = load_trades_jsonl(fp)
                combined.extend(ticks)

            # Normalize each tick's symbol to canonical form, skipping
            # any that cannot be resolved (logged warning).
            normalized: list[TradeTickLite] = []
            skipped = 0
            for t in combined:
                try:
                    canon = resolve_symbol(t.symbol)
                    normalized.append(
                        TradeTickLite(
                            ts_event=t.ts_event,
                            venue=t.venue,
                            symbol=f"{canon.asset}/{canon.quote}",
                            price=t.price,
                            size=t.size,
                            side=t.side,
                            trade_id=t.trade_id,
                            raw=t.raw,
                        )
                    )
                except ValueError:
                    skipped += 1

            if skipped:
                print(
                    f"  [WARN] venue={venue} symbol={symbol}: "
                    f"skipped {skipped} ticks with unresolvable symbol"
                )

            # Deduplicate and sort by ts_event
            seen: set[tuple] = set()
            unique: list[TradeTickLite] = []
            for t in normalized:
                key = (t.ts_event, t.venue, t.symbol, t.price, t.size)
                if key not in seen:
                    seen.add(key)
                    unique.append(t)
            unique.sort(key=lambda t: t.ts_event)

            # Store under a canonical symbol key
            try:
                canon = resolve_symbol(symbol)
                store_sym = f"{canon.asset}-{canon.quote}"
            except ValueError:
                store_sym = symbol

            grouped[(venue, store_sym)] = unique
            print(
                f"  [LOADED] venue={venue} symbol={store_sym} "
                f"ticks={len(unique)} files={len(files)}"
            )

    return grouped


# ---------------------------------------------------------------------------
# Core sweep logic
# ---------------------------------------------------------------------------


def _compute_group_stats(
    all_forward_returns: list[TickForwardReturn],
    group_key: dict,
) -> dict:
    """Compute statistics for a single (lookback, threshold) group."""
    valid = [r for r in all_forward_returns if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
    rejected = [r for r in all_forward_returns if not r.valid]

    if not valid:
        return {
            **group_key,
            "total_events": len(all_forward_returns),
            "valid_events": 0,
            "rejected_events": len(rejected),
            "mean_net_return_bps": None,
            "median_net_return_bps": None,
            "win_rate": None,
            "std_net_return_bps": None,
            "max_net_return_bps": None,
            "min_net_return_bps": None,
        }

    nets = [r.net_return_bps for r in valid]
    mean_net = statistics.mean(nets)
    median_net = statistics.median(nets)
    wins = sum(1 for x in nets if x > 0)
    win_rate = wins / len(nets)
    std_net = statistics.stdev(nets) if len(nets) >= 2 else 0.0

    return {
        **group_key,
        "total_events": len(all_forward_returns),
        "valid_events": len(valid),
        "rejected_events": len(rejected),
        "mean_net_return_bps": round(mean_net, 4),
        "median_net_return_bps": round(median_net, 4),
        "win_rate": round(win_rate, 4),
        "std_net_return_bps": round(std_net, 4),
        "max_net_return_bps": round(max(nets), 4),
        "min_net_return_bps": round(min(nets), 4),
    }


def run_sweep(args: argparse.Namespace) -> TickLeadLagSummary:
    """Execute the full lead-lag sweep and return a TickLeadLagSummary."""
    summary = TickLeadLagSummary(
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps,
        run_start=time.time(),
    )

    source_venues = _split_strings(args.source_venues)
    target_venues = _split_strings(args.target_venues)
    symbols = _split_strings(args.symbols)
    lookbacks_ms = _split_ints(args.lookbacks_ms)
    thresholds_bps = _split_floats(args.thresholds_bps)
    horizons_ms = _split_ints(args.horizons_ms)

    # 1. Load tick data
    print("=" * 70)
    print("TICK LEAD-LAG SIGNAL OBSERVER")
    print("=" * 70)
    print()
    print("[1] Loading tick data …")
    grouped = load_tick_data(args.ticks, source_venues, target_venues, symbols)
    print()

    if not grouped:
        print("[WARN] No tick data was loaded. Verdict: NEEDS_MORE_DATA")
        summary.run_end = time.time()
        return summary

    data_coverage: list[dict] = []
    for (venue, sym), ticks in grouped.items():
        if ticks:
            data_coverage.append(
                {
                    "venue": venue,
                    "symbol": sym,
                    "tick_count": len(ticks),
                    "first_ts": ticks[0].ts_event,
                    "last_ts": ticks[-1].ts_event,
                }
            )

    pair_reports: list[dict] = []
    all_group_results: list[dict] = []
    all_rejections: list[dict] = []

    # 2. For each (source_venue, target_venue, symbol) combo where symbol matches both
    for source_venue in source_venues:
        for target_venue in target_venues:
            # Cross-venue enforcement: skip same-venue comparisons by default
            if source_venue == target_venue and not args.allow_same_venue_diagnostics:
                print(
                    f"[SKIP] same-venue pair {source_venue}→{source_venue} "
                    f"(use --allow-same-venue-diagnostics to include)"
                )
                continue

            for symbol in symbols:
                source_key = (source_venue, symbol)
                target_key = (target_venue, symbol)

                if source_key not in grouped or target_key not in grouped:
                    print(
                        f"[SKIP] source={source_venue} target={target_venue} "
                        f"symbol={symbol}: missing data for one or both venues"
                    )
                    continue

                source_ticks = grouped[source_key]
                target_ticks = grouped[target_key]

                # 3a. Extract
                if not source_ticks:
                    print(
                        f"[SKIP] source={source_venue} symbol={symbol}: "
                        f"no source ticks"
                    )
                    continue
                if not target_ticks:
                    print(
                        f"[SKIP] target={target_venue} symbol={symbol}: "
                        f"no target ticks"
                    )
                    continue

                # 3c. Log coverage
                pair_label = f"{source_venue} → {target_venue} @ {symbol}"
                print(f"\n[3] Analyzing pair: {pair_label}")
                print(
                    f"    source: {len(source_ticks):>8} ticks  "
                    f"[{_format_ts(source_ticks[0].ts_event)} … {_format_ts(source_ticks[-1].ts_event)}]"
                )
                print(
                    f"    target: {len(target_ticks):>8} ticks  "
                    f"[{_format_ts(target_ticks[0].ts_event)} … {_format_ts(target_ticks[-1].ts_event)}]"
                )

                # 3d. Create config + generator — extract canonical asset
                from .symbol_aliases import resolve_symbol
                try:
                    canon = resolve_symbol(symbol)
                    asset = canon.asset
                except ValueError:
                    asset = symbol.split("/")[0] if "/" in symbol else symbol

                config = TickLeadLagConfig(
                    lookback_ms=lookbacks_ms,
                    threshold_bps=thresholds_bps,
                    cooldown_ms=args.cooldown_ms,
                    source_venue=source_venue,
                    target_venue=target_venue,
                    symbol=symbol,
                    asset=asset,
                )
                generator = TickLeadLagGenerator(config)

                pair_results: list[dict] = []
                pair_total_signals = 0

                # 3e. For each (lookback, threshold) combo
                for lookback_ms in lookbacks_ms:
                    for threshold_bps_val in thresholds_bps:
                        sub_config = TickLeadLagConfig(
                            lookback_ms=[lookback_ms],
                            threshold_bps=[threshold_bps_val],
                            cooldown_ms=args.cooldown_ms,
                            source_venue=source_venue,
                            target_venue=target_venue,
                            symbol=symbol,
                            asset=asset,
                        )
                        gen = TickLeadLagGenerator(sub_config)
                        signals = gen.generate(source_ticks)
                        pair_total_signals += len(signals)

                        group_key = {
                            "source_venue": source_venue,
                            "target_venue": target_venue,
                            "symbol": symbol,
                            "asset": asset,
                            "lookback_ms": lookback_ms,
                            "threshold_bps": threshold_bps_val,
                        }

                        if not signals:
                            all_group_results.append(
                                {
                                    **group_key,
                                    "total_signals": 0,
                                    "total_events": 0,
                                    "valid_events": 0,
                                    "rejected_events": 0,
                                    "mean_net_return_bps": None,
                                    "median_net_return_bps": None,
                                    "win_rate": None,
                                }
                            )
                            continue

                        # Evaluate each signal across all horizons
                        forward_returns: list[TickForwardReturn] = []
                        for sig in signals:
                            rets = evaluate_tick_signal(
                                signal=sig,
                                target_ticks=target_ticks,
                                horizons_ms=horizons_ms,
                                fee_bps=args.fee_bps,
                                slippage_bps=args.slippage_bps,
                                quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps,
                            )
                            forward_returns.extend(rets)

                        # Compute group stats
                        stats = _compute_group_stats(forward_returns, group_key)
                        stats["total_signals"] = len(signals)
                        all_group_results.append(stats)
                        pair_results.append(stats)

                        # Collect rejections
                        for r in forward_returns:
                            if not r.valid:
                                all_rejections.append(
                                    {
                                        "signal_id": r.signal_id,
                                        "signal_ts": r.signal_ts,
                                        "target_venue": r.target_venue,
                                        "target_symbol": r.target_symbol,
                                        "horizon_ms": r.horizon_ms,
                                        "rejection_reason": r.rejection_reason,
                                    }
                                )

                        summary.total_signals += len(signals)
                        summary.valid_evaluations += stats["valid_events"]
                        summary.rejected_evaluations += stats["rejected_events"]

                # 3f. Total signals for this pair
                print(f"    total signals across configs: {pair_total_signals}")

                # 3g. Baseline
                baseline_stats: dict | None = None
                if not args.skip_baseline:
                    print(f"    generating baseline (seed={args.baseline_seed}) …")
                    baseline_signals = generate_random_baseline(
                        source_ticks=source_ticks,
                        signal_count=pair_total_signals,
                        source_venue=source_venue,
                        target_venue=target_venue,
                        symbol=symbol,
                        asset=asset,
                        seed=args.baseline_seed,
                    )
                    baseline_returns: list[TickForwardReturn] = []
                    for bsig in baseline_signals:
                        rets = evaluate_tick_signal(
                            signal=bsig,
                            target_ticks=target_ticks,
                            horizons_ms=horizons_ms,
                            fee_bps=args.fee_bps,
                            slippage_bps=args.slippage_bps,
                            quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps,
                        )
                        baseline_returns.extend(rets)

                    baseline_valid = [
                        r
                        for r in baseline_returns
                        if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)
                    ]
                    if baseline_valid:
                        bnets = [r.net_return_bps for r in baseline_valid]
                        baseline_stats = {
                            "source_venue": source_venue,
                            "target_venue": target_venue,
                            "symbol": symbol,
                            "total_signals": len(baseline_signals),
                            "valid_events": len(baseline_valid),
                            "mean_net_return_bps": round(
                                statistics.mean(bnets), 4
                            ),
                            "median_net_return_bps": round(
                                statistics.median(bnets), 4
                            ),
                            "win_rate": round(
                                sum(1 for x in bnets if x > 0) / len(bnets), 4
                            ),
                        }
                    else:
                        baseline_stats = {
                            "source_venue": source_venue,
                            "target_venue": target_venue,
                            "symbol": symbol,
                            "total_signals": len(baseline_signals),
                            "valid_events": 0,
                            "mean_net_return_bps": None,
                            "median_net_return_bps": None,
                            "win_rate": None,
                        }

                # 3h. Evaluate candidate groups
                # For each group that has baseline stats, run the gate
                for gres in pair_results:
                    if gres.get("valid_events", 0) == 0:
                        continue
                    # Aggregate forward returns for this group for gating
                    # We need the actual forward_returns — rebuild from signals
                    # for the candidate gate. Instead, re-evaluate signals
                    # for each group to get forward returns for gating.
                    pass

                # 3i. Write per-pair JSON report
                pair_report = {
                    "pair": pair_label,
                    "source_venue": source_venue,
                    "target_venue": target_venue,
                    "symbol": symbol,
                    "total_signals": pair_total_signals,
                    "groups": pair_results,
                    "baseline": baseline_stats,
                }
                pair_reports.append(pair_report)

                summary.baseline_results = baseline_stats

    # 3h (continued). Run evaluate_candidate_group for each signal group.
    # We need to re-extract forward returns per group. Let's do it here
    # for the groups that have valid events.
    candidate_groups: list[dict] = []
    rejected_groups: list[dict] = []

    for gres in all_group_results:
        if gres.get("valid_events", 0) == 0:
            rejected_groups.append(
                {
                    **gres,
                    "candidate": False,
                    "rejection_reason": "no_valid_events",
                }
            )
            continue

        # Rebuild forward returns for gating by re-running signals
        sv = gres["source_venue"]
        tv = gres["target_venue"]
        sym = gres["symbol"]
        lb = gres["lookback_ms"]
        th = gres["threshold_bps"]

        source_ticks = grouped.get((sv, sym), [])
        target_ticks = grouped.get((tv, sym), [])

        if not source_ticks or not target_ticks:
            rejected_groups.append({**gres, "candidate": False, "rejection_reason": "missing_ticks"})
            continue

        asset = gres.get("asset", sym.split("/")[0] if "/" in sym else sym)
        sub_config = TickLeadLagConfig(
            lookback_ms=[lb],
            threshold_bps=[th],
            cooldown_ms=args.cooldown_ms,
            source_venue=sv,
            target_venue=tv,
            symbol=sym,
            asset=asset,
        )
        gen = TickLeadLagGenerator(sub_config)
        signals = gen.generate(source_ticks)

        forward_returns: list[TickForwardReturn] = []
        for sig in signals:
            rets = evaluate_tick_signal(
                signal=sig,
                target_ticks=target_ticks,
                horizons_ms=_split_ints(args.horizons_ms),
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
                quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps,
            )
            forward_returns.extend(rets)

        # Generate baseline for gating
        baseline_valid: list[TickForwardReturn] = []
        if not args.skip_baseline and grouped.get((sv, sym)):
            bl_signals = generate_random_baseline(
                source_ticks=source_ticks,
                signal_count=len(signals),
                source_venue=sv,
                target_venue=tv,
                symbol=sym,
                asset=asset,
                seed=args.baseline_seed,
            )
            for bsig in bl_signals:
                rets = evaluate_tick_signal(
                    signal=bsig,
                    target_ticks=target_ticks,
                    horizons_ms=_split_ints(args.horizons_ms),
                    fee_bps=args.fee_bps,
                    slippage_bps=args.slippage_bps,
                    quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps,
                )
                baseline_valid.extend(rets)

        gate_result = evaluate_candidate_group(
            forward_returns=forward_returns,
            baseline_forward_returns=baseline_valid,
            min_events=args.min_events,
        )

        gres["gate"] = gate_result

        if gate_result["candidate"]:
            candidate_groups.append({**gres, **gate_result})
        else:
            rejected_groups.append({**gres, **gate_result})

    summary.results_by_group = all_group_results
    summary.candidate_groups = candidate_groups

    summary.run_end = time.time()

    # Store metadata for reporting
    summary._data_coverage = data_coverage  # type: ignore[attr-defined]
    summary._pair_reports = pair_reports  # type: ignore[attr-defined]
    summary._all_rejections = all_rejections  # type: ignore[attr-defined]
    summary._source_venues = source_venues  # type: ignore[attr-defined]
    summary._target_venues = target_venues  # type: ignore[attr-defined]
    summary._symbols = symbols  # type: ignore[attr-defined]
    summary._lookbacks_ms = lookbacks_ms  # type: ignore[attr-defined]
    summary._thresholds_bps = thresholds_bps  # type: ignore[attr-defined]
    summary._horizons_ms = horizons_ms  # type: ignore[attr-defined]
    summary._cooldown_ms = args.cooldown_ms  # type: ignore[attr-defined]
    summary._rejected_groups = rejected_groups  # type: ignore[attr-defined]

    return summary


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------


def _write_outputs(summary: TickLeadLagSummary, out_dir: str, skip_baseline: bool) -> None:
    """Write all report files to the output directory."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # --- tick_summary.json (full results) ---
    full_report = {
        "summary": summary.to_dict(),
        "data_coverage": getattr(summary, "_data_coverage", []),
        "pair_reports": getattr(summary, "_pair_reports", []),
        "candidate_groups": summary.candidate_groups,
        "rejected_groups": getattr(summary, "_rejected_groups", []),
        "run_duration_s": round(summary.run_end - summary.run_start, 2),
    }
    _write_json(os.path.join(out_dir, "tick_summary.json"), full_report)

    # --- tick_summary.csv (group-level table) ---
    csv_rows = []
    for g in summary.results_by_group:
        row = {
            "source_venue": g.get("source_venue", ""),
            "target_venue": g.get("target_venue", ""),
            "symbol": g.get("symbol", ""),
            "asset": g.get("asset", ""),
            "lookback_ms": g.get("lookback_ms", ""),
            "threshold_bps": g.get("threshold_bps", ""),
            "total_signals": g.get("total_signals", 0),
            "total_events": g.get("total_events", 0),
            "valid_events": g.get("valid_events", 0),
            "rejected_events": g.get("rejected_events", 0),
            "mean_net_return_bps": g.get("mean_net_return_bps"),
            "median_net_return_bps": g.get("median_net_return_bps"),
            "win_rate": g.get("win_rate"),
            "std_net_return_bps": g.get("std_net_return_bps"),
            "max_net_return_bps": g.get("max_net_return_bps"),
            "min_net_return_bps": g.get("min_net_return_bps"),
            "candidate": g.get("gate", {}).get("candidate", False) if "gate" in g else False,
        }
        csv_rows.append(row)
    _write_csv(
        os.path.join(out_dir, "tick_summary.csv"),
        csv_rows,
        fieldnames=[
            "source_venue", "target_venue", "symbol", "asset",
            "lookback_ms", "threshold_bps", "total_signals",
            "total_events", "valid_events", "rejected_events",
            "mean_net_return_bps", "median_net_return_bps", "win_rate",
            "std_net_return_bps", "max_net_return_bps", "min_net_return_bps",
            "candidate",
        ],
    )

    # --- by_horizon.csv ---
    # Aggregate mean net return by horizon_ms across all groups.
    # Since each group evaluates across all horizons, we flatten.
    horizon_agg: dict[int, dict[str, Any]] = {}
    for pr in getattr(summary, "_pair_reports", []):
        for g in pr.get("groups", []):
            for h in getattr(summary, "_horizons_ms", []):
                key = h
                if key not in horizon_agg:
                    horizon_agg[key] = {
                        "horizon_ms": h,
                        "groups": 0,
                        "total_valid": 0,
                        "mean_nets": [],
                    }
                horizon_agg[key]["groups"] += 1
                if g.get("valid_events", 0) > 0:
                    horizon_agg[key]["total_valid"] += g["valid_events"]
                    if g.get("mean_net_return_bps") is not None and math.isfinite(g["mean_net_return_bps"]):
                        horizon_agg[key]["mean_nets"].append(g["mean_net_return_bps"])

    horizon_rows = []
    for h in sorted(horizon_agg.keys()):
        entry = horizon_agg[h]
        nets = entry["mean_nets"]
        horizon_rows.append(
            {
                "horizon_ms": h,
                "groups": entry["groups"],
                "total_valid_events": entry["total_valid"],
                "avg_mean_net_return_bps": round(statistics.mean(nets), 4) if nets else None,
                "median_mean_net_return_bps": round(statistics.median(nets), 4) if nets else None,
            }
        )
    _write_csv(
        os.path.join(out_dir, "by_horizon.csv"),
        horizon_rows,
        fieldnames=[
            "horizon_ms", "groups", "total_valid_events",
            "avg_mean_net_return_bps", "median_mean_net_return_bps",
        ],
    )

    # --- by_venue_pair.csv ---
    vp_agg: dict[str, dict[str, Any]] = {}
    for g in summary.results_by_group:
        pair_key = f"{g.get('source_venue', '')}→{g.get('target_venue', '')}"
        if pair_key not in vp_agg:
            vp_agg[pair_key] = {
                "venue_pair": pair_key,
                "total_signals": 0,
                "valid_events": 0,
                "mean_nets": [],
                "symbols": set(),
            }
        vp_agg[pair_key]["total_signals"] += g.get("total_signals", 0)
        vp_agg[pair_key]["valid_events"] += g.get("valid_events", 0)
        if g.get("mean_net_return_bps") is not None and math.isfinite(g["mean_net_return_bps"]):
            vp_agg[pair_key]["mean_nets"].append(g["mean_net_return_bps"])
        if g.get("symbol"):
            vp_agg[pair_key]["symbols"].add(g["symbol"])

    vp_rows = []
    for pk in sorted(vp_agg.keys()):
        v = vp_agg[pk]
        nets = v["mean_nets"]
        vp_rows.append(
            {
                "venue_pair": pk,
                "symbols": ",".join(sorted(v["symbols"])),
                "total_signals": v["total_signals"],
                "valid_events": v["valid_events"],
                "avg_mean_net_return_bps": round(statistics.mean(nets), 4) if nets else None,
            }
        )
    _write_csv(
        os.path.join(out_dir, "by_venue_pair.csv"),
        vp_rows,
        fieldnames=[
            "venue_pair", "symbols", "total_signals",
            "valid_events", "avg_mean_net_return_bps",
        ],
    )

    # --- by_asset.csv ---
    asset_agg: dict[str, dict[str, Any]] = {}
    for g in summary.results_by_group:
        asset = g.get("asset", "unknown")
        if asset not in asset_agg:
            asset_agg[asset] = {
                "asset": asset,
                "total_signals": 0,
                "valid_events": 0,
                "mean_nets": [],
            }
        asset_agg[asset]["total_signals"] += g.get("total_signals", 0)
        asset_agg[asset]["valid_events"] += g.get("valid_events", 0)
        if g.get("mean_net_return_bps") is not None and math.isfinite(g["mean_net_return_bps"]):
            asset_agg[asset]["mean_nets"].append(g["mean_net_return_bps"])

    asset_rows = []
    for a in sorted(asset_agg.keys()):
        v = asset_agg[a]
        nets = v["mean_nets"]
        asset_rows.append(
            {
                "asset": a,
                "total_signals": v["total_signals"],
                "valid_events": v["valid_events"],
                "avg_mean_net_return_bps": round(statistics.mean(nets), 4) if nets else None,
                "median_mean_net_return_bps": round(statistics.median(nets), 4) if nets else None,
            }
        )
    _write_csv(
        os.path.join(out_dir, "by_asset.csv"),
        asset_rows,
        fieldnames=[
            "asset", "total_signals", "valid_events",
            "avg_mean_net_return_bps", "median_mean_net_return_bps",
        ],
    )

    # --- random_baseline.csv ---
    if not skip_baseline and summary.baseline_results is not None:
        baseline_csv_rows = []
        for pr in getattr(summary, "_pair_reports", []):
            bl = pr.get("baseline")
            if bl:
                baseline_csv_rows.append(
                    {
                        "source_venue": bl.get("source_venue", ""),
                        "target_venue": bl.get("target_venue", ""),
                        "symbol": bl.get("symbol", ""),
                        "total_signals": bl.get("total_signals", 0),
                        "valid_events": bl.get("valid_events", 0),
                        "mean_net_return_bps": bl.get("mean_net_return_bps"),
                        "median_net_return_bps": bl.get("median_net_return_bps"),
                        "win_rate": bl.get("win_rate"),
                    }
                )
        _write_csv(
            os.path.join(out_dir, "random_baseline.csv"),
            baseline_csv_rows,
            fieldnames=[
                "source_venue", "target_venue", "symbol",
                "total_signals", "valid_events",
                "mean_net_return_bps", "median_net_return_bps", "win_rate",
            ],
        )

    # --- rejections.json ---
    _write_json(
        os.path.join(out_dir, "rejections.json"),
        getattr(summary, "_all_rejections", []),
    )


# ---------------------------------------------------------------------------
# Verdict & stdout output
# ---------------------------------------------------------------------------


def _determine_verdict(summary: TickLeadLagSummary, data_loaded: bool) -> str:
    if not data_loaded:
        return "NEEDS_MORE_DATA"
    if summary.candidate_groups:
        return "CANDIDATE_FOR_LONGER_OBSERVATION"
    # If total signals across all configs is zero, we didn't even have
    # enough market movement to trigger — that's NEEDS_MORE_DATA, not REJECTED.
    if summary.total_signals == 0:
        return "NEEDS_MORE_DATA"
    return "REJECTED"


def print_verdict_table(summary: TickLeadLagSummary, data_loaded: bool) -> str:
    """Print a concise verdict table to stdout and return the verdict string."""
    verdict = _determine_verdict(summary, data_loaded)
    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    print()
    print(f"  Verdict           : {verdict}")
    print(f"  Total signals    : {summary.total_signals}")
    print(f"  Valid events     : {summary.valid_evaluations}")
    print(f"  Rejected events  : {summary.rejected_evaluations}")
    print(f"  Candidate groups : {len(summary.candidate_groups)}")
    print(f"  Fee (bps)        : {summary.fee_bps}")
    print(f"  Slippage (bps)   : {summary.slippage_bps}")
    print(f"  Quote mismatch   : {summary.quote_mismatch_buffer_bps} bps")
    print(f"  Run duration     : {summary.run_end - summary.run_start:.2f}s")
    print()
    if summary.baseline_results:
        bl = summary.baseline_results
        print("  Baseline (last pair):")
        print(f"    mean net return : {bl.get('mean_net_return_bps')} bps")
        print(f"    win rate        : {bl.get('win_rate')}")
        print()

    if summary.candidate_groups:
        print("  Top candidate groups (by mean net return):")
        sorted_cands = sorted(
            summary.candidate_groups,
            key=lambda x: x.get("mean_net_return_bps") or 0,
            reverse=True,
        )
        for c in sorted_cands[:5]:
            print(
                f"    {c.get('source_venue')}→{c.get('target_venue')} @ "
                f"{c.get('symbol')} lb={c.get('lookback_ms')}ms "
                f"th={c.get('threshold_bps')}bps  "
                f"mean_net={c.get('mean_net_return_bps')}bps  "
                f"win_rate={c.get('win_rate')}  "
                f"n={c.get('valid_events')}"
            )
        print()

    return verdict


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def generate_markdown_report(
    summary: TickLeadLagSummary,
    out_dir: str,
    verdict: str,
    data_loaded: bool,
) -> str:
    """Generate tick_lead_lag_report.md in the output directory and return its path."""
    lines: list[str] = []

    def h(title: str) -> None:
        lines.append(f"## {title}")
        lines.append("")

    def p(text: str) -> None:
        lines.append(text)
        lines.append("")

    def table(header: str, rows: list[list[str]]) -> None:
        lines.append(header)
        lines.append("|---" * len(header.split("|")[1:-1]) + "|---|")
        for row in rows:
            lines.append("|" + "|".join(str(x) for x in row) + "|")
        lines.append("")

    # 1. Hypothesis
    h("Hypothesis")
    p(
        "Tick-level price movements on a \"source\" venue lead equivalent movements "
        "on a \"target\" venue within a short time window. If this lead-lag relationship "
        "is statistically significant and survives cost-adjusted analysis, it may "
        "indicate a microstructure signal worth further investigation."
    )
    p(
        "**This is a research measurement tool only.** No trading or execution "
        "decisions are made by this report."
    )

    # 2. Data coverage
    h("Data Coverage")
    data_cov = getattr(summary, "_data_coverage", [])
    if data_cov:
        header = "| Venue | Symbol | Ticks | First Timestamp | Last Timestamp |"
        rows = [
            [
                d["venue"],
                d["symbol"],
                d["tick_count"],
                _format_ts(d["first_ts"]),
                _format_ts(d["last_ts"]),
            ]
            for d in data_cov
        ]
        table(header, rows)
    else:
        p("No data was loaded.")

    # 3. Venues/symbols tested
    h("Venues / Symbols Tested")
    sv = getattr(summary, "_source_venues", [])
    tv = getattr(summary, "_target_venues", [])
    syms = getattr(summary, "_symbols", [])
    p(f"- **Source venues:** {', '.join(sv)}")
    p(f"- **Target venues:** {', '.join(tv)}")
    p(f"- **Symbols:** {', '.join(syms)}")

    # 4. Signal parameters tested
    h("Signal Parameters Tested")
    lbs = getattr(summary, "_lookbacks_ms", [])
    ths = getattr(summary, "_thresholds_bps", [])
    hrs = getattr(summary, "_horizons_ms", [])
    cooldown_val = getattr(summary, "_cooldown_ms", "N/A")
    p(f"- **Lookbacks (ms):** {', '.join(str(x) for x in lbs)}")
    p(f"- **Thresholds (bps):** {', '.join(str(x) for x in ths)}")
    p(f"- **Horizons (ms):** {', '.join(str(x) for x in hrs)}")
    p(f"- **Cooldown (ms):** {cooldown_val}")

    # 5. Fee/slippage assumptions
    h("Fee / Slippage Assumptions")
    p(
        f"- **Fee:** {summary.fee_bps} bps"
    )
    p(
        f"- **Slippage:** {summary.slippage_bps} bps"
    )
    p(
        f"- **Quote mismatch buffer:** {summary.quote_mismatch_buffer_bps} bps"
    )
    p(
        f"- **Total cost per trade:** "
        f"{summary.fee_bps + summary.slippage_bps + summary.quote_mismatch_buffer_bps:.1f} bps"
    )

    # 6. Total events by group
    h("Total Events by Group")
    groups = summary.results_by_group
    if groups:
        header = "| Source | Target | Symbol | Lookback (ms) | Threshold (bps) | Signals | Valid | Rejected | Mean Net (bps) | Win Rate |"
        rows = []
        for g in groups:
            rows.append(
                [
                    g.get("source_venue", ""),
                    g.get("target_venue", ""),
                    g.get("symbol", ""),
                    str(g.get("lookback_ms", "")),
                    str(g.get("threshold_bps", "")),
                    str(g.get("total_signals", 0)),
                    str(g.get("valid_events", 0)),
                    str(g.get("rejected_events", 0)),
                    str(g.get("mean_net_return_bps", "N/A")),
                    str(g.get("win_rate", "N/A")),
                ]
            )
        table(header, rows)
    else:
        p("No group results.")

    # 7. Rejected evaluations count
    h("Rejected Evaluations")
    rejections = getattr(summary, "_all_rejections", [])
    p(f"Total rejected evaluations: **{summary.rejected_evaluations}**")
    if rejections:
        # Count by rejection reason
        reason_counts: dict[str, int] = {}
        for r in rejections:
            reason = r.get("rejection_reason", "unknown")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        header = "| Rejection Reason | Count |"
        rows = [[reason, str(count)] for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1])]
        table(header, rows)
    else:
        p("No rejections.")

    # 8. Best groups by mean net return (top 5)
    h("Best Groups by Mean Net Return (Top 5)")
    sorted_by_mean = sorted(
        [g for g in groups if g.get("mean_net_return_bps") is not None and math.isfinite(g["mean_net_return_bps"])],
        key=lambda x: x["mean_net_return_bps"],
        reverse=True,
    )[:5]
    if sorted_by_mean:
        header = "| Source | Target | Symbol | LB (ms) | Th (bps) | Mean Net (bps) | Median Net (bps) | Valid Events | Win Rate |"
        rows = []
        for g in sorted_by_mean:
            rows.append(
                [
                    g.get("source_venue", ""),
                    g.get("target_venue", ""),
                    g.get("symbol", ""),
                    str(g.get("lookback_ms", "")),
                    str(g.get("threshold_bps", "")),
                    str(g.get("mean_net_return_bps")),
                    str(g.get("median_net_return_bps")),
                    str(g.get("valid_events", 0)),
                    str(g.get("win_rate")),
                ]
            )
        table(header, rows)
    else:
        p("No groups with valid mean net returns.")

    # 9. Best groups by median net return (top 5)
    h("Best Groups by Median Net Return (Top 5)")
    sorted_by_median = sorted(
        [g for g in groups if g.get("median_net_return_bps") is not None and math.isfinite(g["median_net_return_bps"])],
        key=lambda x: x["median_net_return_bps"],
        reverse=True,
    )[:5]
    if sorted_by_median:
        header = "| Source | Target | Symbol | LB (ms) | Th (bps) | Median Net (bps) | Mean Net (bps) | Valid Events | Win Rate |"
        rows = []
        for g in sorted_by_median:
            rows.append(
                [
                    g.get("source_venue", ""),
                    g.get("target_venue", ""),
                    g.get("symbol", ""),
                    str(g.get("lookback_ms", "")),
                    str(g.get("threshold_bps", "")),
                    str(g.get("median_net_return_bps")),
                    str(g.get("mean_net_return_bps")),
                    str(g.get("valid_events", 0)),
                    str(g.get("win_rate")),
                ]
            )
        table(header, rows)
    else:
        p("No groups with valid median net returns.")

    # 10. Baseline comparison
    h("Baseline Comparison")
    if summary.baseline_results is not None:
        bl = summary.baseline_results
        p(
            f"Baseline mean net return: **{bl.get('mean_net_return_bps', 'N/A')} bps**  "
            f"(valid events: {bl.get('valid_events', 0)}, "
            f"win rate: {bl.get('win_rate', 'N/A')})"
        )
        p("The baseline uses randomly placed timestamps with the same signal count, "
          "evaluated on the same target data, to serve as a noise floor.")
    else:
        p("Baseline was not generated (--skip-baseline was set).")

    # 11. Candidate groups
    h("Candidate Groups")
    if summary.candidate_groups:
        p(f"{len(summary.candidate_groups)} group(s) passed all candidate gates:")
        for cg in summary.candidate_groups:
            p(
                f"- {cg.get('source_venue')}→{cg.get('target_venue')} @ "
                f"{cg.get('symbol')} lb={cg.get('lookback_ms')}ms "
                f"th={cg.get('threshold_bps')}bps: "
                f"mean_net={cg.get('mean_net_return_bps')}bps, "
                f"median_net={cg.get('median_net_return_bps')}bps, "
                f"win_rate={cg.get('win_rate')}, "
                f"valid_events={cg.get('valid_events')}"
            )
    else:
        p("No candidate groups passed all gates.")

    # 12. Rejected groups
    h("Rejected Groups")
    rej_groups = getattr(summary, "_rejected_groups", [])
    if rej_groups:
        p(f"{len(rej_groups)} group(s) were rejected:")
        for rg in rej_groups[:20]:
            reasons = rg.get("rejection_reasons")
            if isinstance(reasons, list) and reasons:
                reason_str = "; ".join(reasons)
            else:
                reason_str = rg.get("rejection_reason", "no reason given")
            p(
                f"- {rg.get('source_venue')}→{rg.get('target_venue')} @ "
                f"{rg.get('symbol')} lb={rg.get('lookback_ms')}ms "
                f"th={rg.get('threshold_bps')}bps: "
                f"{reason_str}"
            )
        if len(rej_groups) > 20:
            p(f"… and {len(rej_groups) - 20} more (see rejections.json).")
    else:
        p("No groups were explicitly rejected (may have zero valid events).")

    # 13. Final verdict
    h("Final Verdict")
    p(f"**{verdict}**")
    if verdict == "NEEDS_MORE_DATA":
        p("No tick data was found for the specified venues and symbols. "
          "Collect more data and re-run.")
    elif verdict == "CANDIDATE_FOR_LONGER_OBSERVATION":
        p(f"{len(summary.candidate_groups)} group(s) passed the candidate gates. "
          "These signals warrant longer observation on fresh data to check "
          "for decay and regime dependence. "
          "**This is NOT an acceptance for trading.**")
    else:
        p("Data was loaded but no signal group passed all candidate gates against "
          "the random baseline.")

    # 14. Limitations
    h("Limitations")
    p(
        f"- This analysis uses a single time period. Results may not generalise "
        f"to other market regimes."
    )
    p(
        f"- Fee, slippage, and quote mismatch assumptions are simplified. "
        f"Real execution costs may be higher, especially for larger sizes."
    )
    p(
        f"- The random baseline is a simple sanity check, not a rigorous "
        f"statistical test. Multiple-comparison bias is not adjusted for."
    )
    p(
        f"- Signals are generated from trade ticks only; quote-level dynamics "
        f"(spread changes, depth shifts) are not modelled."
    )
    p(
        f"- Cooldown gating may suppress correlated signals, potentially "
        f"under-counting the true signal frequency."
    )

    # 15. Next step
    h("Next Step")
    if verdict == "NEEDS_MORE_DATA":
        p(
            "Collect tick data for the specified venues and symbols, "
            "then re-run with `--ticks <data_dir>`."
        )
    elif verdict == "CANDIDATE_FOR_LONGER_OBSERVATION":
        p(
            "Run the signal observer on a fresh, out-of-sample data set "
            "to verify that the candidate signals persist. "
            "Also consider testing on additional asset classes and "
            "adjusting fee/slippage assumptions."
        )
    else:
        p(
            "Consider trying different lookback windows, thresholds, "
            "or venue pairs. Alternatively, collect higher-fidelity data "
            "(e.g., quotes instead of trades) or a longer time series."
        )

    report_text = "\n".join(lines)
    report_path = os.path.join(out_dir, "tick_lead_lag_report.md")
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report_text)

    return report_path


# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------


def _format_ts(ts_ns: int) -> str:
    """Format a nanosecond epoch into a human-readable UTC datetime string."""
    import datetime

    dt = datetime.datetime.fromtimestamp(ts_ns / 1e9, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = build_parser()
    argv = sys.argv[1:]

    # Parse args — allow a default object so we can reference args.cooldown_ms
    # outside of main() for the report.
    args = parser.parse_args(argv)

    # Run sweep
    summary = run_sweep(args)

    data_loaded = bool(getattr(summary, "_data_coverage", []))

    # Write outputs
    _write_outputs(summary, args.out, args.skip_baseline)

    # Print verdict
    verdict = print_verdict_table(summary, data_loaded)

    # Markdown report
    report_path = generate_markdown_report(summary, args.out, verdict, data_loaded)
    print(f"Markdown report: {report_path}")
    print()


if __name__ == "__main__":
    main()
