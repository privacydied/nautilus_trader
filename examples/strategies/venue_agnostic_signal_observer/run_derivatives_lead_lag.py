"""
CLI runner for derivatives lead-lag signal research.

**RESEARCH MEASUREMENT TOOL ONLY.**  No orders, no execution, no live trading.

Reads source/target trade tick JSONL files, generates derivatives impulse
events, evaluates target forward returns, and writes bps-gated reports.

Usage::

    python examples/strategies/venue_agnostic_signal_observer/run_derivatives_lead_lag.py \
        --source-ticks data/binance_btcusdt.jsonl \
        --target-ticks data/kraken_btcusd.jsonl \
        --source-venue binance --target-venue kraken \
        --symbol BTC/USD --asset BTC \
        --signal-types notional_burst,price_shock,signed_imbalance \
        --lookbacks-ms 1000,5000,10000,30000 \
        --horizons-ms 1000,2000,5000,10000,30000,60000 \
        --cooldown-ms 10000 \
        --fee-bps 12 --slippage-bps 2 \
        --latency-buffer-bps 5 --quote-mismatch-bps 5 \
        --min-events 50 \
        --out-dir reports/derivatives_lead_lag_v1
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from .derivatives_lead_lag import DerivativesImpulseGenerator
from .derivatives_lead_lag import impulse_to_tick_signal
from .derivatives_models import DerivativeTradeTick
from .event_study import evaluate_candidate_group
from .event_study import evaluate_tick_signal
from .event_study import generate_random_baseline as _orig_random_baseline
from .tick_models import TickForwardReturn


VALID_INSTRUMENT_TYPES = {"spot", "perp", "futures", "unknown"}
_DERIVATIVE_SOURCE_TYPES = {"perp", "futures"}


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

@dataclass
class DerivativesLeadLagSummary:
    total_signals: int = 0
    valid_evaluations: int = 0
    rejected_evaluations: int = 0
    fee_bps: float = 12.0
    slippage_bps: float = 2.0
    latency_buffer_bps: float = 5.0
    quote_mismatch_bps: float = 5.0
    source_instrument_type: str = "unknown"
    target_instrument_type: str = "unknown"
    results_by_group: list[dict] = field(default_factory=list)
    candidate_groups: list[dict] = field(default_factory=list)
    run_start: float = 0.0
    run_end: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _split_ints(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]

def _split_strings(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def load_deriv_ticks(path: str) -> list[DerivativeTradeTick]:
    """
    Load DerivativeTradeTick from JSONL.  Tolerates plain dicts with
    the same keys as TradeTickLite (ts_event, venue, symbol, price,
    size, side, trade_id).
    """
    ticks: list[DerivativeTradeTick] = []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        ticks.append(DerivativeTradeTick(
            ts_event=int(d.get("ts_event", 0)),
            venue=d.get("venue", ""),
            symbol=d.get("symbol", ""),
            price=float(d.get("price", 0)),
            size=float(d.get("size", 0)),
            side=d.get("side", "unknown"),
            trade_id=d.get("trade_id"),
            raw=d if d.get("raw") else None,
        ))
    ticks.sort(key=lambda t: t.ts_event)
    return ticks


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Derivatives lead-lag signal research CLI. Observer-only."
    )
    p.add_argument("--source-ticks", required=True, help="Source venue JSONL (trades)")
    p.add_argument("--target-ticks", required=True, help="Target venue JSONL (trades)")
    p.add_argument("--source-venue", default="BINANCE")
    p.add_argument("--target-venue", default="KRAKEN")
    p.add_argument("--symbol", default="BTC/USD")
    p.add_argument("--asset", default="BTC")
    p.add_argument(
        "--signal-types",
        default="notional_burst,price_shock,signed_imbalance",
        help="Comma-separated signal types to generate",
    )
    p.add_argument("--lookbacks-ms", default="1000,5000,10000,30000")
    p.add_argument("--horizons-ms", default="1000,2000,5000,10000,30000,60000")
    p.add_argument("--cooldown-ms", type=int, default=10000)
    p.add_argument("--fee-bps", type=float, default=12.0)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--latency-buffer-bps", type=float, default=5.0)
    p.add_argument("--quote-mismatch-bps", type=float, default=5.0)
    p.add_argument("--min-events", type=int, default=50)
    p.add_argument("--baseline-seed", type=int, default=42)
    p.add_argument("--skip-baseline", action="store_true")
    p.add_argument(
        "--notional-zscore-threshold", type=float, default=4.0,
        help="Z-score threshold for notional burst",
    )
    p.add_argument(
        "--price-shock-multiplier", type=float, default=3.0,
        help="Multiplier above median for price shock",
    )
    p.add_argument(
        "--imbalance-threshold", type=float, default=0.6,
        help="Min absolute imbalance to fire",
    )
    p.add_argument(
        "--source-instrument-type",
        default="unknown",
        choices=sorted(VALID_INSTRUMENT_TYPES),
        help="Source instrument type: spot, perp, futures, unknown",
    )
    p.add_argument(
        "--target-instrument-type",
        default="unknown",
        choices=sorted(VALID_INSTRUMENT_TYPES),
        help="Target instrument type: spot, perp, futures, unknown",
    )
    p.add_argument("--out-dir", default="reports/derivatives_lead_lag_v1")
    return p


# ---------------------------------------------------------------------------
# Core sweep
# ---------------------------------------------------------------------------

def run_sweep(args: argparse.Namespace) -> DerivativesLeadLagSummary:
    summary = DerivativesLeadLagSummary(
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        latency_buffer_bps=args.latency_buffer_bps,
        quote_mismatch_bps=args.quote_mismatch_bps,
        source_instrument_type=args.source_instrument_type,
        target_instrument_type=args.target_instrument_type,
        run_start=time.time(),
    )

    signal_types_map = {
        "notional_burst": True,
        "price_shock": True,
        "signed_imbalance": True,
    }
    st_active = {s: s in signal_types_map for s in _split_strings(args.signal_types)}

    total_cost = args.fee_bps + args.slippage_bps + args.latency_buffer_bps + args.quote_mismatch_bps

    print("=" * 70)
    print("DERIVATIVES LEAD-LAG SIGNAL OBSERVER  (observer-only)")
    print("=" * 70)
    print()
    print(f"  Source: {args.source_venue}  Target: {args.target_venue}")
    print(f"  Symbol: {args.symbol}  Asset: {args.asset}")
    print(f"  Total cost: {total_cost:.1f} bps")
    print()
    print("[1] Loading tick data …")
    source_ticks = load_deriv_ticks(args.source_ticks)
    target_ticks = load_deriv_ticks(args.target_ticks)
    print(f"  Source: {len(source_ticks):>8} ticks")
    print(f"  Target: {len(target_ticks):>8} ticks")
    print()

    if not source_ticks or not target_ticks:
        print("[WARN] Missing data. Verdict: NEEDS_MORE_DATA")
        summary.run_end = time.time()
        return summary

    lookbacks_ms = _split_ints(args.lookbacks_ms)
    horizons_ms = _split_ints(args.horizons_ms)

    # Convert target ticks to a list of dicts with price/ts for bisect
    # We will reuse evaluate_tick_signal which expects TickSignalEvent
    # and a list of TradeTickLite.  Since DerivativeTradeTick has same
    # interface, we can build TradeTickLite adapters.
    from .tick_models import TradeTickLite
    target_tt = [
        TradeTickLite(
            ts_event=t.ts_event, venue=t.venue, symbol=t.symbol,
            price=t.price, size=t.size, side=t.side,
            trade_id=t.trade_id, raw=t.raw,
        )
        for t in target_ticks
    ]

    # Generate impulses
    print("[2] Generating derivative impulse events …")
    generator = DerivativesImpulseGenerator(
        lookbacks_ms=lookbacks_ms,
        cooldown_ms=args.cooldown_ms,
        source_venue=args.source_venue,
        target_venue=args.target_venue,
        symbol=args.symbol,
        asset=args.asset,
        price_shock_multiplier=args.price_shock_multiplier,
        imbalance_threshold=args.imbalance_threshold,
        min_trades_in_window=3,
        enable_notional_burst=st_active.get("notional_burst", True),
        enable_price_shock=st_active.get("price_shock", True),
        enable_signed_imbalance=st_active.get("signed_imbalance", True),
    )
    impulses = generator.generate(source_ticks)
    print(f"  Generated {len(impulses)} impulse events")
    print()

    # Group impulses by signal_type + lookback_ms for summary rows
    from collections import defaultdict
    groups: dict[tuple[str, int], list] = defaultdict(list)
    for imp in impulses:
        groups[(imp.signal_type, imp.lookback_ms)].append(imp)

    all_group_results: list[dict] = []
    candidate_groups: list[dict] = []

    # For each group, evaluate forward returns
    for (sig_type, lb_ms), group_impulses in sorted(groups.items()):
        group_key = {
            "source_venue": args.source_venue,
            "target_venue": args.target_venue,
            "symbol": args.symbol,
            "asset": args.asset,
            "signal_type": sig_type,
            "lookback_ms": lb_ms,
        }

        # Convert impulses to TickSignalEvent for the existing evaluator
        signals = [impulse_to_tick_signal(imp) for imp in group_impulses]

        # Evaluate forward returns
        forward_returns: list[TickForwardReturn] = []
        for sig in signals:
            rets = evaluate_tick_signal(
                signal=sig,
                target_ticks=target_tt,
                horizons_ms=horizons_ms,
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
                quote_mismatch_buffer_bps=args.quote_mismatch_bps,
                quote_mismatch=True,
            )
            forward_returns.extend(rets)

        valid = [r for r in forward_returns if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
        rejected = [r for r in forward_returns if not r.valid]

        if valid:
            nets = [r.net_return_bps for r in valid]
            mean_net = statistics.mean(nets)
            median_net = statistics.median(nets)
            win_rate = sum(1 for x in nets if x > 0) / len(nets)
            std_net = statistics.stdev(nets) if len(nets) >= 2 else 0.0
        else:
            mean_net = median_net = win_rate = std_net = None
            nets = []

        stats = {
            **group_key,
            "total_signals": len(group_impulses),
            "total_events": len(forward_returns),
            "valid_events": len(valid),
            "rejected_events": len(rejected),
            "mean_net_return_bps": round(mean_net, 4) if mean_net is not None and math.isfinite(mean_net) else None,
            "median_net_return_bps": round(median_net, 4) if median_net is not None and math.isfinite(median_net) else None,
            "win_rate": round(win_rate, 4) if win_rate is not None and math.isfinite(win_rate) else None,
            "std_net_return_bps": round(std_net, 4) if std_net is not None and math.isfinite(std_net) else None,
            "max_net_return_bps": round(max(nets), 4) if nets else None,
            "min_net_return_bps": round(min(nets), 4) if nets else None,
            "total_cost_bps": total_cost,
        }

        # Random baseline
        if not args.skip_baseline and source_ticks:
            from .tick_models import TradeTickLite as TT
            # Build source TradeTickLite for random baseline
            src_tt = [
                TT(ts_event=t.ts_event, venue=t.venue, symbol=t.symbol,
                   price=t.price, size=t.size, side=t.side)
                for t in source_ticks
            ]
            baseline = _orig_random_baseline(
                source_ticks=src_tt,
                signal_count=len(group_impulses),
                source_venue=args.source_venue,
                target_venue=args.target_venue,
                symbol=args.symbol,
                asset=args.asset,
                seed=args.baseline_seed,
            )
            bl_fwd: list[TickForwardReturn] = []
            for bsig in baseline:
                bl_fwd.extend(evaluate_tick_signal(
                    signal=bsig, target_ticks=target_tt,
                    horizons_ms=horizons_ms,
                    fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
                    quote_mismatch_buffer_bps=args.quote_mismatch_bps,
                    quote_mismatch=True,
                ))
            bl_valid = [r for r in bl_fwd if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
            if bl_valid:
                bl_nets = [r.net_return_bps for r in bl_valid]
                stats["baseline_mean_net_bps"] = round(statistics.mean(bl_nets), 4)
                stats["baseline_win_rate"] = round(
                    sum(1 for x in bl_nets if x > 0) / len(bl_nets), 4)
            else:
                stats["baseline_mean_net_bps"] = None
        else:
            stats["baseline_mean_net_bps"] = None

        # Candidate gate
        if valid and not args.skip_baseline:
            gate = evaluate_candidate_group(
                forward_returns=forward_returns,
                baseline_forward_returns=[r for bl_r in bl_fwd for r in [bl_r] if bl_r.valid],
                min_events=args.min_events,
            )
            if gate.get("candidate"):
                candidate_groups.append({**stats, **gate})
            else:
                stats["gate"] = gate
        elif valid:
            stats["gate"] = {"candidate": False, "rejection_reasons": ["no_valid_baseline"]}
        else:
            stats["gate"] = {"candidate": False, "rejection_reasons": ["no_valid_events"]}

        if stats["gate"] and not stats["gate"].get("candidate"):
            rr = stats["gate"].get("rejection_reasons", [])
            if isinstance(rr, list):
                stats["rejection_reasons"] = rr
            elif isinstance(rr, str):
                stats["rejection_reasons"] = [rr]
            else:
                stats["rejection_reasons"] = []

        all_group_results.append(stats)
        summary.total_signals += len(group_impulses)
        summary.valid_evaluations += len(valid)
        summary.rejected_evaluations += len(rejected)

    summary.results_by_group = all_group_results
    summary.candidate_groups = candidate_groups
    summary.run_end = time.time()
    return summary


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _write_outputs(summary: DerivativesLeadLagSummary, args: argparse.Namespace) -> None:
    out = Path(args.out_dir)

    src_type = summary.source_instrument_type
    tgt_type = summary.target_instrument_type

    # --- JSON ---
    data = {
        "summary": {
            "total_signals": summary.total_signals,
            "valid_evaluations": summary.valid_evaluations,
            "rejected_evaluations": summary.rejected_evaluations,
            "fee_bps": summary.fee_bps,
            "slippage_bps": summary.slippage_bps,
            "latency_buffer_bps": summary.latency_buffer_bps,
            "quote_mismatch_bps": summary.quote_mismatch_bps,
            "source_instrument_type": src_type,
            "target_instrument_type": tgt_type,
            "results_by_group": summary.results_by_group,
            "candidate_groups": summary.candidate_groups,
            "run_duration_s": round(summary.run_end - summary.run_start, 2),
        },
        "warnings": [],
    }
    (out / "summary.json").write_text(json.dumps(data, indent=2, default=str))
    print(f"Written: {out}/summary.json")

    # --- CSV ---
    csv_path = out / "summary.csv"
    fields = [
        "source_venue", "target_venue", "symbol", "asset",
        "source_instrument_type", "target_instrument_type",
        "signal_type", "lookback_ms", "total_signals", "total_events",
        "valid_events", "rejected_events", "mean_net_return_bps",
        "median_net_return_bps", "win_rate", "baseline_mean_net_bps",
        "candidate", "rejection_reasons",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for g in summary.results_by_group:
            row = {}
            for k in fields:
                v = g.get(k)
                if isinstance(v, list):
                    row[k] = "; ".join(str(x) for x in v[:3])
                else:
                    row[k] = v
            w.writerow(row)
    print(f"Written: {csv_path}")

    # --- events.jsonl ---
    (out / "events.jsonl").write_text("")  # placeholder

    # --- Compute verdict ---
    verdict_count = len(summary.candidate_groups)
    if summary.total_signals == 0:
        overall = "NEEDS_MORE_DATA"
    elif verdict_count > 0:
        overall = "CANDIDATE_FOR_LONGER_OBSERVATION"
    else:
        overall = "REJECTED"

    is_derivative_source = src_type in _DERIVATIVE_SOURCE_TYPES

    # --- Per-pair rejection wording ---
    if overall == "REJECTED":
        if is_derivative_source:
            pair_verdict = "REJECTED"
            pair_detail = (
                "No signal group passed the candidate gate after net-cost evaluation.\n"
                "This run rejected the configured derivatives-source -> target pair "
                f"(source type: {src_type}) under the configured cost model."
            )
        else:
            # Must NOT use derivatives-branch rejection language for non-derivative sources
            pair_verdict = "REJECTED_SPOT_SPOT_SMOKE" if src_type == "spot" and tgt_type == "spot" else "REJECTED"
            pair_detail = (
                "This run rejected the configured source->target pair under the "
                f"configured cost model. Source instrument type: {src_type}; "
                f"target instrument type: {tgt_type}.\n\n"
                "This is NOT a rejection of the derivatives lead-lag thesis. "
                "A true derivatives-source test requires source_instrument_type "
                "to be 'perp' or 'futures' (e.g. Binance/Bybit/Kraken perps as source)."
            )
    else:
        pair_verdict = overall
        pair_detail = ""

    # --- MD report ---
    lines: list[str] = [
        "# Derivatives Lead-Lag Signal Report",
        "",
        "> **This is an observer-only research report.**",
        "> No orders, no execution, no private keys, no live trading.",
        "> **This is not a trading recommendation.**",
        "",
        "## Study Parameters",
        "",
        f"- **Source venue:** {args.source_venue}",
        f"- **Target venue:** {args.target_venue}",
        f"- **Source instrument type:** {src_type}",
        f"- **Target instrument type:** {tgt_type}",
        f"- **Symbol:** {args.symbol}",
        f"- **Signal types:** {args.signal_types}",
        f"- **Lookbacks (ms):** {args.lookbacks_ms}",
        f"- **Horizons (ms):** {args.horizons_ms}",
        f"- **Total cost (bps):** {summary.fee_bps + summary.slippage_bps + summary.latency_buffer_bps + summary.quote_mismatch_bps:.1f}",
        "",
        "## Results by Group",
        "",
        "| Source | Target | Src Type | Tgt Type | Symbol | Signal | LB(ms) | Signals | Valid | Net(bps) | Win% | Baseline | Candidate |",
        "|--------|--------|----------|----------|--------|--------|--------|---------|-------|----------|------|----------|-----------|",
    ]
    for g in summary.results_by_group:
        cand = g.get("gate", {}).get("candidate", False) if isinstance(g.get("gate"), dict) else False
        bl = g.get("baseline_mean_net_bps")
        bl_str = f"{bl}" if bl is not None else "-"
        lines.append(
            f"| {g.get('source_venue','')} | {g.get('target_venue','')} | {src_type} | {tgt_type} "
            f"| {g.get('symbol','')} | {g.get('signal_type','')} | {g.get('lookback_ms','')} "
            f"| {g.get('total_signals','-')} | {g.get('valid_events','-')} "
            f"| {g.get('mean_net_return_bps', '-')} | {g.get('win_rate','-')} "
            f"| {bl_str} | {'YES' if cand else 'NO'} |"
        )

    lines.append("")
    lines.append("## Final Verdict")
    lines.append("")
    lines.append(f"**{pair_verdict}**")
    lines.append(pair_detail)
    lines.append("")

    # --- Thesis-level status note ---
    if not is_derivative_source:
        lines.append("## Thesis Status")
        lines.append("")
        lines.append(
            f"This run used source_instrument_type='{src_type}' and "
            f"target_instrument_type='{tgt_type}', so it does NOT test the "
            "derivatives lead-lag thesis. The actual thesis (perp/futures source "
            "-> spot target) remains OPEN_UNTESTED until tested with real "
            "derivatives data."
        )
        lines.append("")

    (out / "report.md").write_text("\n".join(lines))
    print(f"Written: {out}/report.md")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    summary = run_sweep(args)
    _write_outputs(summary, args)

    total_cost = args.fee_bps + args.slippage_bps + args.latency_buffer_bps + args.quote_mismatch_bps
    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    if summary.total_signals == 0:
        verdict = "NEEDS_MORE_DATA"
    elif summary.candidate_groups:
        verdict = "CANDIDATE_FOR_LONGER_OBSERVATION"
    else:
        verdict = "REJECTED"
    print(f"  Verdict       : {verdict}")
    print(f"  Signals       : {summary.total_signals}")
    print(f"  Valid events  : {summary.valid_evaluations}")
    print(f"  Rejected      : {summary.rejected_evaluations}")
    print(f"  Candidates    : {len(summary.candidate_groups)}")
    print(f"  Total cost    : {total_cost:.1f} bps")
    print(f"  Run duration  : {summary.run_end - summary.run_start:.2f}s")
    print()


if __name__ == "__main__":
    main()
