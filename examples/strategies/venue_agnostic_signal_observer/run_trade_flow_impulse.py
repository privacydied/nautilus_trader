r"""
CLI runner for trade-flow impulse signal research.

**RESEARCH MEASUREMENT TOOL ONLY.**  This script scans trade-tick data for
abnormal flow patterns (count burst, notional burst, large-trade outliers,
signed imbalance) and measures target-venue forward returns.  There is **no
order submission, no position tracking, no execution engine, and no
live-trading code**.

Usage::

    python -m examples.strategies.venue_agnostic_signal_observer.run_trade_flow_impulse \\
        --ticks data/signal_observer_ticks_v3 \\
        --source-venues coinbase kraken \\
        --target-venues kraken coinbase \\
        --symbols BTC/USD ETH/USD \\
        --signal-types count_burst notional_burst large_trade signed_imbalance \\
        --lookbacks-ms 1000,5000,10000,30000 \\
        --baseline-window-ms 60000 \\
        --horizons-ms 1000,2000,5000,10000,30000,60000 \\
        --cooldown-ms 10000 \\
        --fee-bps 12 \\
        --slippage-bps 2 \\
        --quote-mismatch-buffer-bps 5 \\
        --min-events 50 \\
        --out reports/trade_flow_impulse_v1
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
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from .event_study import evaluate_candidate_group
from .event_study import evaluate_tick_signal
from .event_study import generate_random_baseline
from .symbol_aliases import resolve_symbol
from .tick_models import TickForwardReturn
from .tick_models import TradeTickLite
from .tick_store import load_trades_jsonl
from .tick_store import tick_file_discovery
from .trade_flow_impulse import TradeFlowImpulseConfig
from .trade_flow_impulse import TradeFlowImpulseSignalGenerator


_MS_TO_NS = 1_000_000


# -- Summary dataclass -----------------------------------------------------

@dataclass
class TradeFlowImpulseSummary:
    """Aggregate summary produced by one run of the trade-flow sweep."""

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


# -- CLI -------------------------------------------------------------------

def _split_ints(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]

def _split_floats(s: str) -> list[float]:
    return [float(x.strip()) for x in s.split(",") if x.strip()]

def _split_strings(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trade-flow impulse signal research CLI. "
        "No orders, no trading — purely measurement and reporting."
    )
    parser.add_argument("--ticks", type=str, default="data/signal_observer_ticks")
    parser.add_argument("--source-venues", type=str, default="kraken")
    parser.add_argument("--target-venues", type=str, default="coinbase")
    parser.add_argument("--symbols", type=str, default="BTC/USD")
    parser.add_argument(
        "--signal-types", type=str,
        default="count_burst,notional_burst,large_trade,signed_imbalance",
    )
    parser.add_argument(
        "--lookbacks-ms", type=str, default="1000,5000,10000,30000"
    )
    parser.add_argument("--baseline-window-ms", type=int, default=60000)
    parser.add_argument(
        "--horizons-ms", type=str,
        default="1000,2000,5000,10000,30000,60000",
    )
    parser.add_argument("--cooldown-ms", type=int, default=10000)
    parser.add_argument("--fee-bps", type=float, default=12.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--quote-mismatch-buffer-bps", type=float, default=5.0)
    parser.add_argument("--min-events", type=int, default=50)
    parser.add_argument("--out", type=str, default="reports/trade_flow_impulse_v1")
    parser.add_argument("--baseline-seed", type=int, default=42)
    parser.add_argument("--skip-baseline", action="store_true", default=False)
    parser.add_argument(
        "--allow-same-venue-diagnostics",
        action="store_true", default=False,
    )
    # Threshold overrides per signal type
    parser.add_argument("--count-burst-multiplier", type=float, default=3.0)
    parser.add_argument("--notional-burst-multiplier", type=float, default=3.0)
    parser.add_argument("--min-trades-in-window", type=int, default=3)
    parser.add_argument("--large-trade-min-notional-usd", type=float, default=0.0)
    parser.add_argument("--large-trade-multiplier", type=float, default=5.0)
    parser.add_argument("--imbalance-threshold", type=float, default=0.65)
    parser.add_argument("--enable-tick-rule-side-proxy", action="store_true", default=False)
    return parser


# -- Data loading (reuses same logic as tick lead-lag) ---------------------

def load_tick_data(
    data_dir: str,
    source_venues: list[str],
    target_venues: list[str],
    symbols: list[str],
) -> dict[tuple[str, str], list[TradeTickLite]]:
    """Discover and load trade tick JSONL files, grouped by (venue, symbol)."""
    grouped: dict[tuple[str, str], list[TradeTickLite]] = {}
    all_venues = list(set(source_venues + target_venues))

    for venue in all_venues:
        for symbol in symbols:
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
            files = sorted(set(files))

            if not files:
                print(f"  [INFO] No trade files found for venue={venue} symbol={symbol}")
                continue

            combined: list[TradeTickLite] = []
            for fp in files:
                ticks = load_trades_jsonl(fp)
                combined.extend(ticks)

            # Normalize symbols to canonical form
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
                print(f"  [WARN] venue={venue} symbol={symbol}: skipped {skipped} unresolvable ticks")

            # Deduplicate and sort
            seen: set[tuple] = set()
            unique: list[TradeTickLite] = []
            for t in normalized:
                key = (t.ts_event, t.venue, t.symbol, t.price, t.size)
                if key not in seen:
                    seen.add(key)
                    unique.append(t)
            unique.sort(key=lambda t: t.ts_event)

            try:
                canon = resolve_symbol(symbol)
                store_sym = f"{canon.asset}-{canon.quote}"
            except ValueError:
                store_sym = symbol

            grouped[(venue, store_sym)] = unique
            print(f"  [LOADED] venue={venue} symbol={store_sym} ticks={len(unique)} files={len(files)}")

    return grouped


# -- Sweep logic -----------------------------------------------------------

def _format_ts(ts_ns: int) -> str:
    import datetime
    dt = datetime.datetime.fromtimestamp(ts_ns / 1e9, tz=datetime.UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def run_sweep(args: argparse.Namespace) -> TradeFlowImpulseSummary:
    summary = TradeFlowImpulseSummary(
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps,
        run_start=time.time(),
    )

    source_venues = _split_strings(args.source_venues)
    target_venues = _split_strings(args.target_venues)
    symbols = _split_strings(args.symbols)
    signal_types = _split_strings(args.signal_types)
    lookbacks_ms = _split_ints(args.lookbacks_ms)
    horizons_ms = _split_ints(args.horizons_ms)

    print("=" * 70)
    print("TRADE-FLOW IMPULSE SIGNAL OBSERVER")
    print("=" * 70)
    print()
    print("[1] Loading tick data …")
    grouped = load_tick_data(args.ticks, source_venues, target_venues, symbols)
    print()

    if not grouped:
        print("[WARN] No tick data was loaded. Verdict: NEEDS_MORE_DATA")
        summary.run_end = time.time()
        return summary

    data_coverage = []
    for (venue, sym), ticks in grouped.items():
        if ticks:
            data_coverage.append({
                "venue": venue,
                "symbol": sym,
                "tick_count": len(ticks),
                "first_ts": ticks[0].ts_event,
                "last_ts": ticks[-1].ts_event,
            })

    pair_reports: list[dict] = []
    all_group_results: list[dict] = []
    all_rejections: list[dict] = []

    # For each (source, target, symbol) cross-venue pair
    for source_venue in source_venues:
        for target_venue in target_venues:
            if source_venue == target_venue and not args.allow_same_venue_diagnostics:
                print(f"[SKIP] same-venue pair {source_venue}→{source_venue}")
                continue

            for symbol in symbols:
                # Normalize lookup key to canonical hyphen form (what loader stores).
                try:
                    canon_sym = resolve_symbol(symbol)
                    lookup_sym = f"{canon_sym.asset}-{canon_sym.quote}"
                    display_sym = f"{canon_sym.asset}/{canon_sym.quote}"
                    asset = canon_sym.asset
                except ValueError:
                    lookup_sym = symbol
                    display_sym = symbol
                    asset = symbol.split("/")[0] if "/" in symbol else symbol

                source_key = (source_venue, lookup_sym)
                target_key = (target_venue, lookup_sym)

                if source_key not in grouped or target_key not in grouped:
                    print(f"[SKIP] source={source_venue} target={target_venue} symbol={display_sym}: missing data")
                    continue

                source_ticks = grouped[source_key]
                target_ticks = grouped[target_key]

                if not source_ticks:
                    print(f"[SKIP] source={source_venue} symbol={display_sym}: no source ticks")
                    continue
                if not target_ticks:
                    print(f"[SKIP] target={target_venue} symbol={display_sym}: no target ticks")
                    continue

                pair_label = f"{source_venue} -> {target_venue} @ {display_sym}"
                print(f"\n[3] Analyzing pair: {pair_label}")
                print(f"    source: {len(source_ticks):>8} ticks  [{_format_ts(source_ticks[0].ts_event)} … {_format_ts(source_ticks[-1].ts_event)}]")
                print(f"    target: {len(target_ticks):>8} ticks  [{_format_ts(target_ticks[0].ts_event)} … {_format_ts(target_ticks[-1].ts_event)}]")

                pair_total_signals = 0
                pair_results: list[dict] = []

                # For each signal type, for each lookback
                for sig_type in signal_types:
                    config = TradeFlowImpulseConfig(
                        source_venue=source_venue,
                        target_venue=target_venue,
                        symbol=symbol,
                        asset=asset,
                        flow_lookbacks_ms=lookbacks_ms,
                        baseline_window_ms=args.baseline_window_ms,
                        signal_types=[sig_type],
                        count_burst_multiplier=args.count_burst_multiplier,
                        notional_burst_multiplier=args.notional_burst_multiplier,
                        min_trades_in_window=args.min_trades_in_window,
                        large_trade_min_notional_usd=args.large_trade_min_notional_usd,
                        large_trade_multiplier=args.large_trade_multiplier,
                        imbalance_threshold=args.imbalance_threshold,
                        enable_tick_rule_side_proxy=args.enable_tick_rule_side_proxy,
                        cooldown_ms=args.cooldown_ms,
                    )
                    TradeFlowImpulseSignalGenerator(config)

                    for lb_ms in lookbacks_ms:
                        sub_config = TradeFlowImpulseConfig(
                            source_venue=source_venue,
                            target_venue=target_venue,
                            symbol=symbol,
                            asset=asset,
                            flow_lookbacks_ms=[lb_ms],
                            baseline_window_ms=args.baseline_window_ms,
                            signal_types=[sig_type],
                            count_burst_multiplier=args.count_burst_multiplier,
                            notional_burst_multiplier=args.notional_burst_multiplier,
                            min_trades_in_window=args.min_trades_in_window,
                            large_trade_min_notional_usd=args.large_trade_min_notional_usd,
                            large_trade_multiplier=args.large_trade_multiplier,
                            imbalance_threshold=args.imbalance_threshold,
                            enable_tick_rule_side_proxy=args.enable_tick_rule_side_proxy,
                            cooldown_ms=args.cooldown_ms,
                        )
                        gen = TradeFlowImpulseSignalGenerator(sub_config)
                        signals = gen.generate(source_ticks)
                        pair_total_signals += len(signals)

                        group_key = {
                            "source_venue": source_venue,
                            "target_venue": target_venue,
                            "symbol": symbol,
                            "asset": asset,
                            "signal_type": sig_type,
                            "lookback_ms": lb_ms,
                        }

                        if not signals:
                            all_group_results.append({
                                **group_key,
                                "total_signals": 0,
                                "total_events": 0,
                                "valid_events": 0,
                                "rejected_events": 0,
                                "mean_net_return_bps": None,
                                "median_net_return_bps": None,
                                "win_rate": None,
                            })
                            continue

                        # Evaluate each signal
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
                        valid = [r for r in forward_returns if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
                        rejected = [r for r in forward_returns if not r.valid]

                        if valid:
                            nets = [r.net_return_bps for r in valid]
                            mean_net = statistics.mean(nets)
                            median_net = statistics.median(nets)
                            win_rate = sum(1 for x in nets if x > 0) / len(nets)
                            std_net = statistics.stdev(nets) if len(nets) >= 2 else 0.0
                            stats = {
                                **group_key,
                                "total_signals": len(signals),
                                "total_events": len(forward_returns),
                                "valid_events": len(valid),
                                "rejected_events": len(rejected),
                                "mean_net_return_bps": round(mean_net, 4),
                                "median_net_return_bps": round(median_net, 4),
                                "win_rate": round(win_rate, 4),
                                "std_net_return_bps": round(std_net, 4),
                                "max_net_return_bps": round(max(nets), 4),
                                "min_net_return_bps": round(min(nets), 4),
                            }
                        else:
                            stats = {
                                **group_key,
                                "total_signals": len(signals),
                                "total_events": len(forward_returns),
                                "valid_events": 0,
                                "rejected_events": len(rejected),
                                "mean_net_return_bps": None,
                                "median_net_return_bps": None,
                                "win_rate": None,
                                "std_net_return_bps": None,
                                "max_net_return_bps": None,
                                "min_net_return_bps": None,
                            }

                        all_group_results.append(stats)
                        pair_results.append(stats)

                        # Collect rejections
                        for r in forward_returns:
                            if not r.valid:
                                all_rejections.append({
                                    "signal_id": r.signal_id,
                                    "signal_ts": r.signal_ts,
                                    "target_venue": r.target_venue,
                                    "target_symbol": r.target_symbol,
                                    "signal_type": sig_type,
                                    "horizon_ms": r.horizon_ms,
                                    "rejection_reason": r.rejection_reason,
                                })

                        summary.total_signals += len(signals)
                        summary.valid_evaluations += stats["valid_events"]
                        summary.rejected_evaluations += stats["rejected_events"]

                print(f"    total signals across configs: {pair_total_signals}")

                # Baseline
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

                    bl_valid = [r for r in baseline_returns if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
                    if bl_valid:
                        bnets = [r.net_return_bps for r in bl_valid]
                        baseline_stats = {
                            "source_venue": source_venue,
                            "target_venue": target_venue,
                            "symbol": symbol,
                            "total_signals": len(baseline_signals),
                            "valid_events": len(bl_valid),
                            "mean_net_return_bps": round(statistics.mean(bnets), 4),
                            "median_net_return_bps": round(statistics.median(bnets), 4),
                            "win_rate": round(sum(1 for x in bnets if x > 0) / len(bnets), 4),
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

    # Candidate gate
    candidate_groups: list[dict] = []
    rejected_groups: list[dict] = []

    for gres in all_group_results:
        if gres.get("valid_events", 0) == 0:
            rejected_groups.append({**gres, "candidate": False, "rejection_reason": "no_valid_events"})
            continue

        sv = gres["source_venue"]
        tv = gres["target_venue"]
        sym = gres["symbol"]
        lb = gres["lookback_ms"]
        st = gres["signal_type"]
        asset = gres.get("asset", sym.split("/")[0] if "/" in sym else sym)

        source_ticks = grouped.get((sv, sym), [])
        target_ticks = grouped.get((tv, sym), [])
        if not source_ticks or not target_ticks:
            rejected_groups.append({**gres, "candidate": False, "rejection_reason": "missing_ticks"})
            continue

        sub_config = TradeFlowImpulseConfig(
            source_venue=sv, target_venue=tv, symbol=sym, asset=asset,
            flow_lookbacks_ms=[lb], baseline_window_ms=args.baseline_window_ms,
            signal_types=[st],
            count_burst_multiplier=args.count_burst_multiplier,
            notional_burst_multiplier=args.notional_burst_multiplier,
            min_trades_in_window=args.min_trades_in_window,
            large_trade_min_notional_usd=args.large_trade_min_notional_usd,
            large_trade_multiplier=args.large_trade_multiplier,
            imbalance_threshold=args.imbalance_threshold,
            enable_tick_rule_side_proxy=args.enable_tick_rule_side_proxy,
            cooldown_ms=args.cooldown_ms,
        )
        gen = TradeFlowImpulseSignalGenerator(sub_config)
        signals = gen.generate(source_ticks)

        forward_returns: list[TickForwardReturn] = []
        for sig in signals:
            rets = evaluate_tick_signal(
                signal=sig, target_ticks=target_ticks,
                horizons_ms=horizons_ms, fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
                quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps,
            )
            forward_returns.extend(rets)

        bl_fwr: list[TickForwardReturn] = []
        if not args.skip_baseline and source_ticks:
            bl_signals = generate_random_baseline(
                source_ticks=source_ticks, signal_count=len(signals),
                source_venue=sv, target_venue=tv, symbol=sym, asset=asset,
                seed=args.baseline_seed,
            )
            for bsig in bl_signals:
                rets = evaluate_tick_signal(
                    signal=bsig, target_ticks=target_ticks,
                    horizons_ms=horizons_ms, fee_bps=args.fee_bps,
                    slippage_bps=args.slippage_bps,
                    quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps,
                )
                bl_fwr.extend(rets)

        gate_result = evaluate_candidate_group(
            forward_returns=forward_returns,
            baseline_forward_returns=bl_fwr,
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
    summary._data_coverage = data_coverage
    summary._pair_reports = pair_reports
    summary._all_rejections = all_rejections
    summary._source_venues = source_venues
    summary._target_venues = target_venues
    summary._symbols = symbols
    summary._signal_types = signal_types
    summary._lookbacks_ms = lookbacks_ms
    summary._horizons_ms = horizons_ms
    summary._cooldown_ms = args.cooldown_ms
    summary._rejected_groups = rejected_groups
    return summary


# -- Output writing --------------------------------------------------------

def _write_json(path: str, obj: object) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, default=str)

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


def _write_outputs(summary: TradeFlowImpulseSummary, out_dir: str, skip_baseline: bool) -> None:
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    full_report = {
        "summary": {
            "total_signals": summary.total_signals,
            "valid_evaluations": summary.valid_evaluations,
            "rejected_evaluations": summary.rejected_evaluations,
            "fee_bps": summary.fee_bps,
            "slippage_bps": summary.slippage_bps,
            "quote_mismatch_buffer_bps": summary.quote_mismatch_buffer_bps,
            "results_by_group": summary.results_by_group,
            "baseline_results": summary.baseline_results,
            "run_duration_s": round(summary.run_end - summary.run_start, 2),
        },
        "data_coverage": getattr(summary, "_data_coverage", []),
        "pair_reports": getattr(summary, "_pair_reports", []),
        "candidate_groups": summary.candidate_groups,
        "rejected_groups": getattr(summary, "_rejected_groups", []),
    }
    _write_json(os.path.join(out_dir, "tick_summary.json"), full_report)

    # CSV
    csv_rows = []
    for g in summary.results_by_group:
        csv_rows.append({
            "source_venue": g.get("source_venue", ""),
            "target_venue": g.get("target_venue", ""),
            "symbol": g.get("symbol", ""),
            "asset": g.get("asset", ""),
            "signal_type": g.get("signal_type", ""),
            "lookback_ms": g.get("lookback_ms", ""),
            "total_signals": g.get("total_signals", 0),
            "total_events": g.get("total_events", 0),
            "valid_events": g.get("valid_events", 0),
            "rejected_events": g.get("rejected_events", 0),
            "mean_net_return_bps": g.get("mean_net_return_bps"),
            "median_net_return_bps": g.get("median_net_return_bps"),
            "win_rate": g.get("win_rate"),
        })
    _write_csv(
        os.path.join(out_dir, "tick_summary.csv"),
        csv_rows,
        fieldnames=[
            "source_venue", "target_venue", "symbol", "asset",
            "signal_type", "lookback_ms", "total_signals",
            "total_events", "valid_events", "rejected_events",
            "mean_net_return_bps", "median_net_return_bps", "win_rate",
        ],
    )

    # Rejections
    _write_json(os.path.join(out_dir, "rejections.json"), getattr(summary, "_all_rejections", []))

    # By signal type
    st_agg: dict[str, dict] = {}
    for g in summary.results_by_group:
        st = g.get("signal_type", "unknown")
        if st not in st_agg:
            st_agg[st] = {"signal_type": st, "total_signals": 0, "valid_events": 0, "mean_nets": []}
        st_agg[st]["total_signals"] += g.get("total_signals", 0)
        st_agg[st]["valid_events"] += g.get("valid_events", 0)
        if g.get("mean_net_return_bps") is not None and math.isfinite(g["mean_net_return_bps"]):
            st_agg[st]["mean_nets"].append(g["mean_net_return_bps"])

    st_rows = []
    for st_name in sorted(st_agg.keys()):
        v = st_agg[st_name]
        nets = v["mean_nets"]
        st_rows.append({
            "signal_type": st_name,
            "total_signals": v["total_signals"],
            "valid_events": v["valid_events"],
            "avg_mean_net_return_bps": round(statistics.mean(nets), 4) if nets else None,
        })
    _write_csv(
        os.path.join(out_dir, "by_signal_type.csv"),
        st_rows,
        fieldnames=["signal_type", "total_signals", "valid_events", "avg_mean_net_return_bps"],
    )

    # By venue pair
    vp_agg: dict[str, dict] = {}
    for g in summary.results_by_group:
        pk = f"{g.get('source_venue','')}->{g.get('target_venue','')}"
        if pk not in vp_agg:
            vp_agg[pk] = {"venue_pair": pk, "total_signals": 0, "valid_events": 0, "symbols": set()}
        vp_agg[pk]["total_signals"] += g.get("total_signals", 0)
        vp_agg[pk]["valid_events"] += g.get("valid_events", 0)
        if g.get("symbol"):
            vp_agg[pk]["symbols"].add(g["symbol"])

    vp_rows = []
    for pk_s in sorted(vp_agg.keys()):
        v = vp_agg[pk_s]
        vp_rows.append({
            "venue_pair": pk_s,
            "symbols": ",".join(sorted(v["symbols"])),
            "total_signals": v["total_signals"],
            "valid_events": v["valid_events"],
        })
    _write_csv(
        os.path.join(out_dir, "by_venue_pair.csv"),
        vp_rows,
        fieldnames=["venue_pair", "symbols", "total_signals", "valid_events"],
    )

    # Baseline
    if not skip_baseline and summary.baseline_results is not None:
        bl = summary.baseline_results
        _write_csv(
            os.path.join(out_dir, "random_baseline.csv"),
            [{
                "source_venue": bl.get("source_venue",""),
                "target_venue": bl.get("target_venue",""),
                "symbol": bl.get("symbol",""),
                "total_signals": bl.get("total_signals", 0),
                "valid_events": bl.get("valid_events", 0),
                "mean_net_return_bps": bl.get("mean_net_return_bps"),
                "median_net_return_bps": bl.get("median_net_return_bps"),
                "win_rate": bl.get("win_rate"),
            }],
        )


# -- Verdict ---------------------------------------------------------------

def print_verdict_table(summary: TradeFlowImpulseSummary, data_loaded: bool) -> str:
    verdict = "NEEDS_MORE_DATA"
    if data_loaded:
        if summary.candidate_groups:
            verdict = "CANDIDATE_FOR_LONGER_OBSERVATION"
        elif summary.total_signals == 0:
            verdict = "NEEDS_MORE_DATA"
        else:
            verdict = "REJECTED"

    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    print(f"  Verdict           : {verdict}")
    print(f"  Total signals    : {summary.total_signals}")
    print(f"  Valid events     : {summary.valid_evaluations}")
    print(f"  Rejected events  : {summary.rejected_evaluations}")
    print(f"  Candidate groups : {len(summary.candidate_groups)}")
    print(f"  Fee (bps)        : {summary.fee_bps}")
    print(f"  Slippage (bps)   : {summary.slippage_bps}")
    print(f"  Run duration     : {summary.run_end - summary.run_start:.2f}s")
    print()

    if summary.baseline_results:
        bl = summary.baseline_results
        print(f"  Baseline mean net : {bl.get('mean_net_return_bps')} bps")
        print(f"  Baseline win rate : {bl.get('win_rate')}")
        print()

    if summary.candidate_groups:
        print("  Top candidate groups:")
        sorted_cands = sorted(
            summary.candidate_groups,
            key=lambda x: x.get("mean_net_return_bps") or 0,
            reverse=True,
        )
        for c in sorted_cands[:5]:
            print(
                f"    {c.get('source_venue')}->{c.get('target_venue')} @ "
                f"{c.get('symbol')} {c.get('signal_type')} lb={c.get('lookback_ms')}ms  "
                f"mean_net={c.get('mean_net_return_bps')}bps  n={c.get('valid_events')}"
            )

    return verdict


# -- Markdown report -------------------------------------------------------

def generate_markdown_report(
    summary: TradeFlowImpulseSummary, out_dir: str, verdict: str, data_loaded: bool
) -> str:
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

    h("Hypothesis")
    p(
        "Trade-flow impulse: abnormal trade count, notional volume, signed "
        "imbalance, or large-trade bursts on one venue may predict short-horizon "
        "forward returns on another venue."
    )
    p("**This is a research measurement tool only.** No trading or execution.")

    h("Data Coverage")
    data_cov = getattr(summary, "_data_coverage", [])
    if data_cov:
        header = "| Venue | Symbol | Ticks | First Timestamp | Last Timestamp |"
        rows = [[d["venue"], d["symbol"], d["tick_count"],
                 _format_ts(d["first_ts"]), _format_ts(d["last_ts"])] for d in data_cov]
        table(header, rows)
    else:
        p("No data loaded.")

    h("Signal Parameters")
    p(f"- **Signal types:** {', '.join(getattr(summary, '_signal_types', []))}")
    p(f"- **Lookbacks (ms):** {', '.join(str(x) for x in getattr(summary, '_lookbacks_ms', []))}")
    p(f"- **Baseline window (ms):** {60000}")
    p(f"- **Horizons (ms):** {', '.join(str(x) for x in getattr(summary, '_horizons_ms', []))}")
    p(f"- **Cooldown (ms):** {getattr(summary, '_cooldown_ms', 'N/A')}")

    h("Fee / Slippage")
    p(f"- **Fee:** {summary.fee_bps} bps")
    p(f"- **Slippage:** {summary.slippage_bps} bps")
    p(f"- **Quote mismatch buffer:** {summary.quote_mismatch_buffer_bps} bps")
    total = summary.fee_bps + summary.slippage_bps + summary.quote_mismatch_buffer_bps
    p(f"- **Total cost per trade:** {total:.1f} bps")

    h("Results by Group")
    groups = summary.results_by_group
    if groups:
        header = "| Source | Target | Symbol | Signal Type | LB(ms) | Signals | Valid | Rejected | Mean Net(bps) | Win Rate |"
        rows = [[g.get("source_venue",""), g.get("target_venue",""), g.get("symbol",""),
                 g.get("signal_type",""), str(g.get("lookback_ms","")),
                 str(g.get("total_signals",0)), str(g.get("valid_events",0)),
                 str(g.get("rejected_events",0)), str(g.get("mean_net_return_bps","N/A")),
                 str(g.get("win_rate","N/A"))] for g in groups]
        table(header, rows)
    else:
        p("No group results.")

    h("Best Groups by Mean Net Return (Top 5)")
    valid_groups = [g for g in groups if g.get("mean_net_return_bps") is not None and math.isfinite(g["mean_net_return_bps"])]
    top5 = sorted(valid_groups, key=lambda x: x["mean_net_return_bps"], reverse=True)[:5]
    if top5:
        header = "| Source | Target | Symbol | Type | LB(ms) | Mean Net(bps) | Valid | Win Rate |"
        rows = [[g.get("source_venue",""), g.get("target_venue",""), g.get("symbol",""),
                 g.get("signal_type",""), str(g.get("lookback_ms","")),
                 str(g.get("mean_net_return_bps")), str(g.get("valid_events",0)),
                 str(g.get("win_rate"))] for g in top5]
        table(header, rows)
    else:
        p("No groups with valid mean net returns.")

    h("Baseline Comparison")
    if summary.baseline_results:
        bl = summary.baseline_results
        p(f"Baseline mean net: **{bl.get('mean_net_return_bps','N/A')} bps**  "
          f"(valid events: {bl.get('valid_events',0)}, win rate: {bl.get('win_rate','N/A')})")
    else:
        p("No baseline generated.")

    h("Candidate Groups")
    if summary.candidate_groups:
        p(f"{len(summary.candidate_groups)} group(s) passed all candidate gates:")
        for cg in summary.candidate_groups:
            p(f"- {cg.get('source_venue')}->{cg.get('target_venue')} @ {cg.get('symbol')} "
              f"{cg.get('signal_type')} lb={cg.get('lookback_ms')}ms: "
              f"mean_net={cg.get('mean_net_return_bps')}bps, n={cg.get('valid_events')}")
    else:
        p("No candidate groups passed all gates.")

    h("Final Verdict")
    p(f"**{verdict}**")
    if verdict == "REJECTED":
        p("Data was loaded but no signal group passed all candidate gates against "
          "the random baseline.")

    h("Limitations")
    p("- Single time period — results may not generalise to other regimes.")
    p("- Fee, slippage, and quote mismatch assumptions are simplified.")
    p("- Random baseline is a simple sanity check, not a rigorous statistical test.")
    p("- Signals from trade ticks only; quote-level dynamics not modelled.")

    h("Next Step")
    if verdict == "REJECTED":
        p("Consider different signal types, longer capture windows, or additional "
          "venues/assets. Trade-flow impulse may require larger or more diverse "
          "datasets to detect statistically significant patterns.")

    report_text = "\n".join(lines)
    report_path = os.path.join(out_dir, "trade_flow_impulse_report.md")
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report_text)
    return report_path


# -- Main ------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:])
    summary = run_sweep(args)
    data_loaded = bool(getattr(summary, "_data_coverage", []))
    _write_outputs(summary, args.out, args.skip_baseline)
    verdict = print_verdict_table(summary, data_loaded)
    report_path = generate_markdown_report(summary, args.out, verdict, data_loaded)
    print(f"Markdown report: {report_path}")
    print()


if __name__ == "__main__":
    main()
