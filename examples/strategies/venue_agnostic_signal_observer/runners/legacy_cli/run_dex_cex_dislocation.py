"""CLI runner for DEX→CEX spot dislocation research.

**RESEARCH MEASUREMENT TOOL ONLY.**  No orders, no execution, no live trading.

Reads DEX pool snapshots, generates dislocation events, evaluates CEX spot
forward returns, and writes bps-gated reports.

Usage (synthetic/mocked data)::

    python -m examples.strategies.venue_agnostic_signal_observer.run_dex_cex_dislocation \\
        --dex-snapshots data/dex_snapshots.jsonl \\
        --target-ticks data/kraken_btcusd.jsonl \\
        --assets SOL,LINK,AVAX \\
        --target-venues kraken \\
        --horizons-ms 30000,60000,300000 \\
        --fee-bps 40 --slippage-bps 5 \\
        --stale-data-buffer-bps 10 --quote-mismatch-buffer-bps 5 \\
        --min-liquidity-usd 500000 --min-volume-1h-usd 100000 \\
        --out-dir reports/dex_cex_spot_dislocation_v1
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
import csv
import statistics
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from examples.strategies.venue_agnostic_signal_observer.dex_models import DexPoolSnapshot, DexDislocationEvent, DexCexForwardResult
from examples.strategies.venue_agnostic_signal_observer.dex_cex_dislocation import DexCexDislocationDetector, dex_event_to_tick_signal
from examples.strategies.venue_agnostic_signal_observer.dex_adapters import load_dex_snapshots_from_jsonl
from examples.strategies.venue_agnostic_signal_observer.tick_models import TickForwardReturn, TradeTickLite
from examples.strategies.venue_agnostic_signal_observer.event_study import (
    evaluate_tick_signal,
    generate_random_baseline as _orig_random_baseline,
    evaluate_candidate_group,
)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

@dataclass
class DexCexDislocationSummary:
    total_snapshots: int = 0
    total_events: int = 0
    valid_evaluations: int = 0
    rejected_evaluations: int = 0
    fee_bps: float = 40.0
    slippage_bps: float = 5.0
    stale_data_buffer_bps: float = 10.0
    quote_mismatch_buffer_bps: float = 5.0
    results_by_group: list[dict] = field(default_factory=list)
    candidate_groups: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    run_start: float = 0.0
    run_end: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MS_TO_NS = 1_000_000


def _split_strings(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _split_ints(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


# ---------------------------------------------------------------------------
# Asset mapping: asset symbol -> DEX chain / known pool addresses
# ---------------------------------------------------------------------------

DEFAULT_DEX_ASSET_MAP = {
    "SOL": {"chain": "solana", "known_symbols": ["SOL"]},
    "LINK": {"chain": "ethereum", "known_symbols": ["LINK"]},
    "AVAX": {"chain": "avalanche", "known_symbols": ["WAVAX", "AVAX"]},
    "DOGE": {"chain": "bsc", "known_symbols": ["DOGE"]},
    "ADA": {"chain": "cardano", "known_symbols": ["ADA"]},
    "XRP": {"chain": "xrpl", "known_symbols": ["XRP"]},
    "PEPE": {"chain": "ethereum", "known_symbols": ["PEPE"]},
}


# ---------------------------------------------------------------------------
# Target tick loader
# ---------------------------------------------------------------------------

def load_target_ticks(
    tick_dirs: list[str],
    target_venues: list[str],
    assets: list[str],
) -> dict[str, list[TradeTickLite]]:
    """Discover tick files from directories.

    Tries tick_file_discovery per venue+symbol, then falls back to
    scanning for trades_*.jsonl files in the directory.
    """
    from examples.strategies.venue_agnostic_signal_observer.tick_store import tick_file_discovery

    result: dict[str, list[TradeTickLite]] = {}
    for d in tick_dirs:
        dp = Path(d)
        for venue in target_venues:
            # Try per-venue symbol discovery
            for asset in assets:
                for sym_variant in [f"{asset}/USD", f"{asset}-USD", f"{asset}/USDT", f"{asset}-USDT"]:
                    files = tick_file_discovery(str(dp), venue=venue, symbol=sym_variant)
                    for fpath in files:
                        ticks = _read_jsonl_trades(fpath)
                        if ticks:
                            key = f"{venue}:{ticks[0].symbol}"
                            if key not in result:
                                result[key] = ticks

            # Also do a broad directory scan for any remaining trades files
            for fpath in sorted(dp.glob("trades_*.jsonl")):
                ticks = _read_jsonl_trades(str(fpath))
                if ticks:
                    key = f"{ticks[0].venue}:{ticks[0].symbol}"
                    if key not in result:
                        result[key] = ticks
    return result


def _read_jsonl_trades(path: str) -> list[TradeTickLite]:
    p = Path(path)
    if not p.exists():
        return []
    ticks: list[TradeTickLite] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            ticks.append(TradeTickLite.from_dict(d))
        except Exception:
            continue
    ticks.sort(key=lambda t: t.ts_event)
    # Dedup
    seen = set()
    deduped = []
    for t in ticks:
        key = (t.ts_event, t.venue, t.symbol, t.price, t.size)
        if key not in seen:
            seen.add(key)
            deduped.append(t)
    return deduped


# ---------------------------------------------------------------------------
# Event evaluation
# ---------------------------------------------------------------------------

def evaluate_dex_events(
    events: list[DexDislocationEvent],
    target_ticks: dict[str, list[TradeTickLite]],
    horizons_ms: list[int],
    fee_bps: float,
    slippage_bps: float,
    stale_data_buffer_bps: float,
    quote_mismatch_buffer_bps: float,
    min_liquidity_usd: float,
) -> tuple[list[DexCexForwardResult], dict]:
    """Evaluate DEX dislocation events against CEX spot tick data."""
    results: list[DexCexForwardResult] = []
    stats: dict = defaultdict(int)

    total_cost = fee_bps + slippage_bps + stale_data_buffer_bps + quote_mismatch_buffer_bps

    for evt in events:
        # Find matching target venue
        asset = evt.asset
        matched = False
        best_result: DexCexForwardResult | None = None

        for tv_key, ticks in target_ticks.items():
            tv_venue = tv_key.split(":")[0] if ":" in tv_key else tv_key
            # Simple symbol match: asset name should be first part of target symbol
            if not ticks or asset.upper() not in ticks[0].symbol.upper().replace("/", ""):
                continue

            matched = True
            # Convert to tick signal
            sig = _dex_event_to_forward_signal(evt, tv_venue, ticks)
            if sig is None:
                continue

            forward_returns = evaluate_tick_signal(
                signal=sig,
                target_ticks=ticks,
                horizons_ms=horizons_ms,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
                quote_mismatch=True,
            )

            for fr in forward_returns:
                valid = fr.valid and fr.net_return_bps is not None and math.isfinite(fr.net_return_bps)
                result = DexCexForwardResult(
                    event_id=evt.event_id,
                    asset=evt.asset,
                    source_chain=evt.chain,
                    source_dex=evt.dex,
                    target_venue=ticks[0].venue,
                    target_symbol=ticks[0].symbol,
                    horizon_ms=fr.horizon_ms,
                    entry_price=fr.entry_reference_price,
                    forward_price=fr.forward_price,
                    gross_bps=fr.direction_adjusted_return_bps,
                    fee_bps=fr.fee_bps,
                    slippage_bps=fr.slippage_bps,
                    stale_data_buffer_bps=fr.quote_mismatch_buffer_bps,
                    quote_mismatch_buffer_bps=fr.quote_mismatch_buffer_bps,
                    net_bps=fr.net_return_bps,
                    valid=valid,
                    rejection_reason=fr.rejection_reason,
                )
                if result.valid:
                    stats["valid"] += 1
                else:
                    stats["rejected"] += 1
                results.append(result)

        if not matched:
            stats["unmatched"] += 1

    return results, stats


def _dex_event_to_forward_signal(
    event: DexDislocationEvent,
    target_venue: str,
    target_ticks: list[TradeTickLite],
) -> Any | None:
    """Convert a DexDislocationEvent into a TickSignalEvent for evaluation."""
    sig = dex_event_to_tick_signal(event)
    # Patch the target info
    if target_ticks:
        sig.target_venue = target_venue
        sig.target_symbol = target_ticks[0].symbol
    else:
        sig.target_venue = target_venue
        sig.target_symbol = event.asset

    # Handle unknown direction — reject rather than guess
    if sig.direction == "unknown":
        return None

    return sig


# ---------------------------------------------------------------------------
# Core sweep
# ---------------------------------------------------------------------------

def run_sweep(args: argparse.Namespace) -> DexCexDislocationSummary:
    summary = DexCexDislocationSummary(
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        stale_data_buffer_bps=args.stale_data_buffer_bps,
        quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps,
        run_start=time.time(),
    )

    total_cost = args.fee_bps + args.slippage_bps + args.stale_data_buffer_bps + args.quote_mismatch_buffer_bps
    assets = _split_strings(args.assets)
    horizons_ms = _split_ints(args.horizons_ms)

    print("=" * 70)
    print("DEX-CEX SPOT DISLOCATION OBSERVER  (observer-only)")
    print("=" * 70)
    print()
    print(f"  Assets: {', '.join(assets)}")
    print(f"  Target venues: {', '.join(_split_strings(args.target_venues))}")
    print(f"  Horizons (ms): {args.horizons_ms}")
    print(f"  Total cost: {total_cost:.1f} bps")
    print()

    # Load DEX snapshots
    print("[1] Loading DEX snapshots …")
    if not Path(args.dex_snapshots).exists():
        summary.warnings.append(f"DEX snapshots file not found: {args.dex_snapshots}")
        summary.run_end = time.time()
        return summary

    snapshots = load_dex_snapshots_from_jsonl(args.dex_snapshots)
    summary.total_snapshots = len(snapshots)
    print(f"  Loaded {len(snapshots)} snapshots")
    print()

    if not snapshots:
        summary.warnings.append("No valid DEX snapshots loaded")
        summary.run_end = time.time()
        return summary

    # Scan for dislocation events
    print("[2] Scanning for dislocation events …")
    detector = DexCexDislocationDetector(
        min_liquidity_usd=args.min_liquidity_usd,
        min_volume_1h_usd=args.min_volume_1h_usd,
    )
    events, scan_warnings = detector.scan(snapshots)
    summary.total_events = len(events)
    summary.warnings.extend(scan_warnings)
    print(f"  Generated {len(events)} dislocation events")
    print(f"  Warnings: {len(scan_warnings)}")
    print()

    if not events:
        summary.warnings.append("No dislocation events generated")
        summary.run_end = time.time()
        return summary

    # Load CEX target ticks
    print("[3] Loading CEX target ticks …")
    tick_dirs = _split_strings(args.target_ticks)
    target_venues = _split_strings(args.target_venues)
    target_ticks = load_target_ticks(tick_dirs, target_venues, assets)
    print(f"  Loaded {len(target_ticks)} target tick streams")
    print()

    if not target_ticks:
        summary.warnings.append("No target ticks loaded for CEX venues")
        summary.run_end = time.time()
        return summary

    # Evaluate forward returns
    print("[4] Evaluating forward returns …")
    results, eval_stats = evaluate_dex_events(
        events, target_ticks, horizons_ms,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        stale_data_buffer_bps=args.stale_data_buffer_bps,
        quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps,
        min_liquidity_usd=args.min_liquidity_usd,
    )
    print(f"  Forward results: {len(results)}")
    print(f"  Matched: {eval_stats.get('valid', 0)} valid, {eval_stats.get('rejected', 0)} rejected, {eval_stats.get('unmatched', 0)} unmatched")
    print()

    # Group by signal_type + asset + horizon
    groups: dict[tuple, list] = defaultdict(list)
    for r in results:
        groups[(r.target_venue, r.asset, r.horizon_ms)].append(r)

    all_group_results: list[dict] = []
    candidate_groups: list[dict] = []

    for (venue, asset, horizon), group_results in sorted(groups.items()):
        valid = [r for r in group_results if r.valid and r.net_bps is not None and math.isfinite(r.net_bps)]
        rejected = [r for r in group_results if not r.valid]

        stats_group = {
            "target_venue": venue,
            "asset": asset,
            "horizon_ms": horizon,
            "total_events": len(group_results),
            "valid_events": len(valid),
            "rejected_events": len(rejected),
        }

        if valid:
            nets = [r.net_bps for r in valid]
            mean_net = statistics.mean(nets)
            median_net = statistics.median(nets)
            win_rate = sum(1 for x in nets if x > 0) / len(nets)
            std_net = statistics.stdev(nets) if len(nets) >= 2 else 0.0
            stats_group.update({
                "mean_net_bps": round(mean_net, 4),
                "median_net_bps": round(median_net, 4),
                "win_rate": round(win_rate, 4),
                "std_net_bps": round(std_net, 4),
                "max_net_bps": round(max(nets), 4),
                "min_net_bps": round(min(nets), 4),
                "total_cost_bps": total_cost,
            })
        else:
            stats_group.update({
                "mean_net_bps": None, "median_net_bps": None,
                "win_rate": None, "std_net_bps": None,
                "max_net_bps": None, "min_net_bps": None,
                "total_cost_bps": total_cost,
            })

        # Candidate gate
        all_nets = [r.net_bps for r in group_results if r.net_bps is not None and math.isfinite(r.net_bps)]
        gross_nets = [r.gross_bps for r in group_results if r.gross_bps is not None and math.isfinite(r.gross_bps)]
        gate = evaluate_candidate_group(
            forward_returns=[_r2tfr(r) for r in valid],
            baseline_forward_returns=[],
            min_events=getattr(args, 'min_events', 50),
        )

        # Extra DEX-specific gate checks
        if gate.get("candidate"):
            gross_mean = statistics.mean(gross_nets) if gross_nets else 0
            net_mean = statistics.mean(all_nets) if all_nets else 0
            net_median = statistics.median(all_nets) if all_nets else 0
            wr = sum(1 for x in all_nets if x > 0) / len(all_nets) if all_nets else 0

            if gross_mean <= 0:
                gate["candidate"] = False
                gate["rejection_reasons"] = gate.get("rejection_reasons", []) + ["gross_mean_negative"]
            if net_mean <= 0:
                gate["candidate"] = False
                gate["rejection_reasons"] = gate.get("rejection_reasons", []) + ["net_mean_negative"]
            if net_median < -5:
                gate["candidate"] = False
                gate["rejection_reasons"] = gate.get("rejection_reasons", []) + ["median_too_negative"]
            if wr < 0.5:
                gate["candidate"] = False
                gate["rejection_reasons"] = gate.get("rejection_reasons", []) + ["win_rate_below_50pct"]
            if len(valid) < getattr(args, 'min_events', 50):
                gate["candidate"] = False
                gate["rejection_reasons"] = gate.get("rejection_reasons", []) + ["insufficient_events"]

        if gate.get("candidate"):
            candidate_groups.append({**stats_group, **gate})
        else:
            stats_group["gate"] = gate
            stats_group["candidate"] = False
            rr = gate.get("rejection_reasons", [])
            stats_group["rejection_reasons"] = "; ".join(rr) if isinstance(rr, list) else str(rr)

        all_group_results.append(stats_group)
        summary.valid_evaluations += len(valid)
        summary.rejected_evaluations += len(rejected)

    summary.results_by_group = all_group_results
    summary.candidate_groups = candidate_groups
    summary.run_end = time.time()
    return summary


def _r2tfr(r: DexCexForwardResult) -> TickForwardReturn:
    """Convert DexCexForwardResult -> TickForwardReturn for candidate gate."""
    return TickForwardReturn(
        signal_id=r.event_id,
        signal_ts=0,
        target_venue=r.target_venue,
        target_symbol=r.target_symbol,
        horizon_ms=r.horizon_ms,
        entry_reference_price=r.entry_price,
        forward_price=r.forward_price,
        raw_return_bps=r.gross_bps,
        direction_adjusted_return_bps=r.gross_bps,
        fee_bps=r.fee_bps,
        slippage_bps=r.slippage_bps,
        quote_mismatch_buffer_bps=r.quote_mismatch_buffer_bps,
        net_return_bps=r.net_bps,
        valid=r.valid,
        rejection_reason=r.rejection_reason,
    )


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="DEX-CEX spot dislocation research CLI. Observer-only."
    )
    p.add_argument(
        "--dex-snapshots", required=True,
        help="Path to DEX snapshots JSONL file",
    )
    p.add_argument(
        "--target-ticks", required=True,
        help="Directory(s) containing CEX tick JSONL files",
    )
    p.add_argument("--assets", default="SOL,LINK,AVAX,DOGE,ADA")
    p.add_argument("--target-venues", default="kraken,coinbase")
    p.add_argument(
        "--horizons-ms", default="30000,60000,300000,900000,3600000",
        help="Comma-separated forward horizons in ms",
    )
    p.add_argument("--duration-seconds", type=int, default=600)
    p.add_argument("--poll-interval-seconds", type=int, default=10)
    p.add_argument("--min-liquidity-usd", type=float, default=500_000)
    p.add_argument("--min-volume-1h-usd", type=float, default=100_000)
    p.add_argument("--fee-bps", type=float, default=40.0)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--stale-data-buffer-bps", type=float, default=10.0)
    p.add_argument("--quote-mismatch-buffer-bps", type=float, default=5.0)
    p.add_argument("--min-events", type=int, default=50)
    p.add_argument("--skip-baseline", action="store_true")
    p.add_argument("--baseline-seed", type=int, default=42)
    p.add_argument("--out-dir", default="reports/dex_cex_spot_dislocation_v1")
    return p


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _write_outputs(summary: DexCexDislocationSummary, args: argparse.Namespace) -> None:
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    total_cost = summary.fee_bps + summary.slippage_bps + summary.stale_data_buffer_bps + summary.quote_mismatch_buffer_bps

    # Compute verdict
    if summary.total_snapshots == 0 or summary.total_events == 0:
        overall = "NEEDS_MORE_DATA"
    elif summary.candidate_groups:
        overall = "CANDIDATE_FOR_LONGER_OBSERVATION"
    elif summary.valid_evaluations > 0:
        overall = "REJECTED"
    else:
        overall = "NEEDS_MORE_DATA"

    # --- JSON ---
    data = {
        "summary": {
            "total_snapshots": summary.total_snapshots,
            "total_events": summary.total_events,
            "valid_evaluations": summary.valid_evaluations,
            "rejected_evaluations": summary.rejected_evaluations,
            "fee_bps": summary.fee_bps,
            "slippage_bps": summary.slippage_bps,
            "stale_data_buffer_bps": summary.stale_data_buffer_bps,
            "quote_mismatch_buffer_bps": summary.quote_mismatch_buffer_bps,
            "total_cost_bps": total_cost,
            "results_by_group": summary.results_by_group,
            "candidate_groups": summary.candidate_groups,
            "run_duration_s": round(summary.run_end - summary.run_start, 2),
        },
        "warnings": summary.warnings,
    }
    (out / "summary.json").write_text(json.dumps(data, indent=2, default=str))

    # --- CSV ---
    csv_path = out / "summary.csv"
    fields = [
        "target_venue", "asset", "horizon_ms",
        "total_events", "valid_events", "rejected_events",
        "mean_net_bps", "median_net_bps", "win_rate",
        "candidate", "rejection_reasons",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for g in summary.results_by_group:
            w.writerow(g)

    # --- MD report ---
    lines = [
        "# DEX-CEX Spot Dislocation Report",
        "",
        "> **This is an observer-only research report.**",
        "> No orders, no execution, no private keys, no live trading.",
        "> **This is not a trading recommendation.**",
        "",
        "## Study Parameters",
        "",
        f"- **DEX source:** {args.dex_snapshots}",
        f"- **Target CEX venues:** {args.target_venues}",
        f"- **Asset universe:** {args.assets}",
        f"- **Horizons (ms):** {args.horizons_ms}",
        f"- **Fee (bps):** {summary.fee_bps:.1f}",
        f"- **Slippage (bps):** {summary.slippage_bps:.1f}",
        f"- **Stale data buffer (bps):** {summary.stale_data_buffer_bps:.1f}",
        f"- **Quote mismatch buffer (bps):** {summary.quote_mismatch_buffer_bps:.1f}",
        f"- **Total cost (bps):** {total_cost:.1f}",
        f"- **Min pool liquidity (USD):** {args.min_liquidity_usd:,.0f}",
        f"- **Min 1h volume (USD):** {args.min_volume_1h_usd:,.0f}",
        "",
        "## Results by Group",
        "",
        "| Venue | Asset | Horizon(ms) | Events | Valid | Mean Net(bps) | Win Rate | Candidate |",
        "|-------|-------|-------------|--------|-------|---------------|----------|-----------|",
    ]
    for g in summary.results_by_group:
        cand = g.get("candidate", False)
        bl = g.get("mean_net_bps")
        bl_str = f"{bl}" if bl is not None else "-"
        wr = g.get("win_rate")
        wr_str = f"{wr:.2%}" if wr is not None else "-"
        lines.append(
            f"| {g.get('target_venue','')} | {g.get('asset','')} | {g.get('horizon_ms','-')} "
            f"| {g.get('total_events','-')} | {g.get('valid_events','-')} "
            f"| {bl_str} | {wr_str} | {'YES' if cand else 'NO'} |"
        )

    lines.append("")
    lines.append("## Summary Stats")
    lines.append("")
    lines.append(f"- Total DEX snapshots: {summary.total_snapshots}")
    lines.append(f"- Total dislocation events: {summary.total_events}")
    lines.append(f"- Valid forward returns: {summary.valid_evaluations}")
    lines.append(f"- Rejected: {summary.rejected_evaluations}")
    lines.append(f"- Candidate groups: {len(summary.candidate_groups)}")
    if summary.warnings:
        lines.append(f"- Warnings: {len(summary.warnings)}")
        for w in summary.warnings[:10]:
            lines.append(f"  - {w}")
        if len(summary.warnings) > 10:
            lines.append(f"  ... and {len(summary.warnings) - 10} more")

    lines.append("")
    lines.append("## Final Verdict")
    lines.append("")
    lines.append(f"**{overall}**")
    if overall == "REJECTED":
        lines.append("No signal group passed the candidate gate after net-cost evaluation.")
    elif overall == "NEEDS_MORE_DATA":
        lines.append("Insufficient events to evaluate. Collect more DEX data and re-run.")
    elif overall == "CANDIDATE_FOR_LONGER_OBSERVATION":
        lines.append("At least one group passed all gates. Worthy of further observation. NOT tradable.")
    lines.append("")

    (out / "report.md").write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    summary = run_sweep(args)
    _write_outputs(summary, args)

    total_cost = summary.fee_bps + summary.slippage_bps + summary.stale_data_buffer_bps + summary.quote_mismatch_buffer_bps
    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    if summary.total_snapshots == 0 or summary.total_events == 0:
        verdict = "NEEDS_MORE_DATA"
    elif summary.candidate_groups:
        verdict = "CANDIDATE_FOR_LONGER_OBSERVATION"
    elif summary.valid_evaluations > 0:
        verdict = "REJECTED"
    else:
        verdict = "UNKNOWN"
    print(f"  Verdict       : {verdict}")
    print(f"  Snapshots     : {summary.total_snapshots}")
    print(f"  Events        : {summary.total_events}")
    print(f"  Valid returns : {summary.valid_evaluations}")
    print(f"  Rejected      : {summary.rejected_evaluations}")
    print(f"  Candidates    : {len(summary.candidate_groups)}")
    print(f"  Total cost    : {total_cost:.1f} bps")
    print(f"  Run duration  : {summary.run_end - summary.run_start:.2f}s")
    print()


if __name__ == "__main__":
    main()
