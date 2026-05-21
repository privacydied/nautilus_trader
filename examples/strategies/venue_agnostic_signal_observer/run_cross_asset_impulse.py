#!/usr/bin/env python3
r"""
Cross-asset spot impulse lead-lag runner.

**RESEARCH MEASUREMENT TOOL ONLY.**  Orchestrates the full cross-asset spot
impulse pipeline:

1. Loads pre-captured tick data from JSONL files.
2. Builds stream health for every venue/symbol combination.
3. Pairs BTC/ETH source streams with alt target streams (skipping same-asset).
4. Generates trade-flow impulse signals on each source stream.
5. Measures forward returns on each target stream.
6. Compares against a random baseline.
7. Applies the six-gate candidate evaluation.
8. Produces a verdict and writes reports.

There is **no execution logic, no order submission, no position tracking, and
no live-trading code** anywhere in this module.

Safety:
- No orders.
- No keys or credentials.
- No private endpoints.
- No derivatives execution.
- Public spot data only.

Usage example::

    python -m examples.strategies.venue_agnostic_signal_observer.run_cross_asset_impulse \\
      --ticks data/cross_asset_spot_capture_v1 \\
      --source-venues coinbase,kraken \\
      --source-symbols BTC/USD,ETH/USD \\
      --target-venues kraken,coinbase \\
      --target-symbols SOL/USD,LINK/USD,AVAX/USD,ADA/USD,DOGE/USD \\
      --signal-types price_shock,notional_burst,large_trade,signed_imbalance \\
      --lookbacks-ms 1000,5000,10000,30000 \\
      --horizons-ms 5000,10000,30000,60000,180000,300000 \\
      --cooldown-ms 10000 \\
      --fee-bps 40 \\
      --slippage-bps 5 \\
      --quote-mismatch-buffer-bps 5 \\
      --min-source-range-bps 30 \\
      --min-target-range-bps 30 \\
      --min-events 50 \\
      --baseline-window-ms 60000 \\
      --out reports/cross_asset_impulse_v1
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

from .cross_asset_impulse import PairKey
from .cross_asset_impulse import PairResult
from .cross_asset_impulse import StreamHealth
from .cross_asset_impulse import _format_optional_bps
from .cross_asset_impulse import _remap_signal_for_target
from .cross_asset_impulse import compute_overlap
from .cross_asset_impulse import compute_verdict
from .cross_asset_impulse import generate_markdown_report
from .cross_asset_impulse import generate_source_impulses
from .event_study import evaluate_candidate_group
from .event_study import evaluate_tick_signal
from .event_study import generate_random_baseline
from .tick_store import load_trades_jsonl
from .tick_store import tick_file_discovery


logger = logging.getLogger(__name__)

ALL_SIGNAL_TYPES = {"price_shock", "notional_burst", "large_trade", "signed_imbalance", "count_burst"}
MAP_SIGNAL_TYPE = {
    "price_shock": "notional_burst",     # map price_shock to notional_burst
    "count_burst": "count_burst",
    "notional_burst": "notional_burst",
    "large_trade": "large_trade",
    "signed_imbalance": "signed_imbalance",
}


def _resolve_signal_types(raw: list[str]) -> list[str]:
    """Map user-facing signal type names to TradeFlowImpulse internals."""
    resolved: list[str] = []
    for t in raw:
        mapped = MAP_SIGNAL_TYPE.get(t, t)
        if mapped in ALL_SIGNAL_TYPES:
            resolved.append(mapped)
    return list(dict.fromkeys(resolved))


def load_tick_data(
    data_dir: str,
    venues: list[str],
    symbols: list[str],
) -> dict[tuple[str, str], list]:
    """
    Discover and load JSONL trade files, grouped by (venue, symbol).

    Normalizes symbols using symbol_aliases.  Deduplicates and sorts by
    timestamp.
    """
    from .tick_models import TradeTickLite

    ticks: dict[tuple[str, str], list[TradeTickLite]] = defaultdict(list)

    for venue in venues:
        for symbol in symbols:
            files = tick_file_discovery(data_dir, venue, symbol, tick_type="trades")
            if not files:
                logger.warning("No trade files found for %s %s", venue, symbol)
                continue

            for fpath in files:
                logger.debug("Loading %s", fpath)
                loaded = load_trades_jsonl(fpath)
                ticks[(venue, symbol)].extend(loaded)

    # Deduplicate and sort per (venue, symbol)
    for key in list(ticks.keys()):
        seen = set()
        unique = []
        for t in ticks[key]:
            dedup_key = (t.ts_event, t.venue, t.symbol, t.price, t.size)
            if dedup_key not in seen:
                seen.add(dedup_key)
                unique.append(t)
        unique.sort(key=lambda tix: tix.ts_event)
        ticks[key] = unique

    return ticks


def build_stream_health(
    tick_data: dict[tuple[str, str], list],
) -> dict[str, StreamHealth]:
    """Build StreamHealth for every (venue, symbol) with loaded ticks."""
    health: dict[str, StreamHealth] = {}
    for (venue, symbol), tlist in tick_data.items():
        sh = StreamHealth(venue=venue, symbol=symbol)
        if tlist:
            sh.tick_count = len(tlist)
            sh.first_tick_ts = tlist[0].ts_event
            sh.last_tick_ts = tlist[-1].ts_event
            sh.price_min = min(t.price for t in tlist)
            sh.price_max = max(t.price for t in tlist)
        else:
            sh.zero_tick_warning = True
        health[f"{venue}|{symbol}"] = sh
    return health


def _is_same_symbol(source_symbol: str, target_symbol: str) -> bool:
    """
    Check if source and target symbols resolve to the same asset.

    For cross-asset, we skip any pairing where source and target are the
    same asset (e.g., BTC/USD -> BTC/USD).
    """
    try:
        from .symbol_aliases import same_asset as sa
        return sa(source_symbol, target_symbol)
    except (ValueError, KeyError):
        return source_symbol.upper().replace("-", "/") == target_symbol.upper().replace("-", "/")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _run(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-asset spot impulse lead-lag evaluator (research only)."
    )
    parser.add_argument(
        "--ticks",
        type=str,
        required=True,
        help="Directory containing pre-captured tick JSONL files.",
    )
    parser.add_argument(
        "--source-venues",
        type=str,
        default="coinbase,kraken",
        help="Comma-separated source venue names.",
    )
    parser.add_argument(
        "--source-symbols",
        type=str,
        default="BTC/USD,ETH/USD",
        help="Comma-separated source symbols.",
    )
    parser.add_argument(
        "--target-venues",
        type=str,
        default="kraken,coinbase",
        help="Comma-separated target venue names.",
    )
    parser.add_argument(
        "--target-symbols",
        type=str,
        default="SOL/USD,LINK/USD,AVAX/USD,ADA/USD,DOGE/USD",
        help="Comma-separated target symbols.",
    )
    parser.add_argument(
        "--signal-types",
        type=str,
        default="notional_burst,large_trade,signed_imbalance",
        help="Comma-separated signal types.",
    )
    parser.add_argument(
        "--lookbacks-ms",
        type=str,
        default="1000,5000,10000,30000",
        help="Comma-separated lookback windows in milliseconds.",
    )
    parser.add_argument(
        "--horizons-ms",
        type=str,
        default="5000,10000,30000,60000,180000,300000",
        help="Comma-separated forward-return horizons in milliseconds.",
    )
    parser.add_argument(
        "--cooldown-ms",
        type=int,
        default=10000,
        help="Cooldown between signals of the same type (ms).",
    )
    parser.add_argument(
        "--fee-bps",
        type=float,
        default=40.0,
        help="Assumed fee in bps.",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        default=5.0,
        help="Assumed slippage in bps.",
    )
    parser.add_argument(
        "--quote-mismatch-buffer-bps",
        type=float,
        default=5.0,
        help="Quote mismatch cost buffer (bps).",
    )
    parser.add_argument(
        "--min-source-range-bps",
        type=float,
        default=30.0,
        help="Minimum source price range (bps) to consider the run informative.",
    )
    parser.add_argument(
        "--min-target-range-bps",
        type=float,
        default=30.0,
        help="Minimum target price range (bps) to consider the run informative.",
    )
    parser.add_argument(
        "--min-events",
        type=int,
        default=50,
        help="Minimum valid forward-return events per pair.",
    )
    parser.add_argument(
        "--baseline-window-ms",
        type=int,
        default=60000,
        help="Random baseline window in milliseconds.",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="reports/cross_asset_impulse_v1",
        help="Output directory for reports.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    return parser


def _run(args: argparse.Namespace) -> None:
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    t_start = time.monotonic()

    source_venues = [v.strip() for v in args.source_venues.split(",")]
    source_symbols = [s.strip() for s in args.source_symbols.split(",")]
    target_venues = [v.strip() for v in args.target_venues.split(",")]
    target_symbols = [s.strip() for s in args.target_symbols.split(",")]
    signal_types = _resolve_signal_types(
        [s.strip() for s in args.signal_types.split(",")]
    )
    lookbacks_ms = [int(x) for x in args.lookbacks_ms.split(",")]
    horizons_ms = [int(x) for x in args.horizons_ms.split(",")]

    fee_bps = args.fee_bps
    slippage_bps = args.slippage_bps
    quote_mismatch_buffer_bps = args.quote_mismatch_buffer_bps
    all_in_cost_bps = fee_bps + slippage_bps + quote_mismatch_buffer_bps
    min_source_range_bps = args.min_source_range_bps
    min_target_range_bps = args.min_target_range_bps
    min_events = args.min_events
    baseline_window_ms = args.baseline_window_ms

    # ── Load tick data ──────────────────────────────────────────────────
    all_symbols = sorted(set(source_symbols + target_symbols))
    all_venues = sorted(set(source_venues + target_venues))

    logger.info("Loading tick data from %s ...", args.ticks)
    tick_data = load_tick_data(args.ticks, all_venues, all_symbols)
    stream_health = build_stream_health(tick_data)

    # Build stream health entries for missing combos
    for v in all_venues:
        for s in all_symbols:
            key = f"{v}|{s}"
            if key not in stream_health:
                sh = StreamHealth(venue=v, symbol=s)
                sh.zero_tick_warning = True
                sh.subscription_ok = False
                stream_health[key] = sh

    total_ticks = sum(sh.tick_count for sh in stream_health.values())
    logger.info("Loaded %d total ticks across %d streams.", total_ticks, len(stream_health))

    # ── Build source-target pairs, skipping same-asset ──────────────────
    pairs: list[PairKey] = []
    same_symbol_skips: list[str] = []

    for sv in source_venues:
        for ss in source_symbols:
            for tv in target_venues:
                for tsym in target_symbols:
                    if _is_same_symbol(ss, tsym):
                        same_symbol_skips.append(f"{sv} {ss} -> {tv} {tsym}")
                        continue
                    pairs.append(PairKey(
                        source_venue=sv,
                        source_symbol=ss,
                        target_venue=tv,
                        target_symbol=tsym,
                    ))

    logger.info(
        "Same-symbol skips: %d, cross-asset pairs: %d",
        len(same_symbol_skips), len(pairs),
    )

    if same_symbol_skips:
        for skip in same_symbol_skips:
            logger.info("  SKIP (same symbol): %s", skip)

    if not pairs:
        logger.error("No valid cross-asset pairs to evaluate.")
        sys.exit(1)

    # ── Generate source impulses ────────────────────────────────────────
    logger.info("Generating source impulse signals ...")

    # Group source signals by (venue, symbol)
    source_signals: dict[tuple[str, str], list] = defaultdict(list)
    for sv in source_venues:
        for ss in source_symbols:
            skey = (sv, ss)
            stlist = tick_data.get(skey, [])
            if not stlist:
                logger.warning("No source ticks for %s %s — skipping.", sv, ss)
                continue

            events = generate_source_impulses(
                source_ticks=stlist,
                source_venue=sv,
                source_symbol=ss,
                signal_types=signal_types,
                flow_lookbacks_ms=lookbacks_ms,
                baseline_window_ms=baseline_window_ms,
                cooldown_ms=args.cooldown_ms,
            )
            source_signals[skey] = events
            logger.info(
                "  %s %s -> %d source impulses generated",
                sv, ss, len(events),
            )

    # ── Evaluate each pair ──────────────────────────────────────────────
    logger.info("Evaluating %d cross-asset pairs ...", len(pairs))

    pair_results: list[PairResult] = []
    all_events_dicts: list[dict] = []
    all_forward_returns_dicts: list[dict] = []

    for pair in pairs:
        pair_key_str = pair.group_key()
        skey = (pair.source_venue, pair.source_symbol)
        tkey = (pair.target_venue, pair.target_symbol)

        src_signals = source_signals.get(skey, [])
        tgt_ticks = tick_data.get(tkey, [])
        src_stream = stream_health.get(f"{pair.source_venue}|{pair.source_symbol}")
        tgt_stream = stream_health.get(f"{pair.target_venue}|{pair.target_symbol}")

        if not src_stream or not tgt_stream:
            pair_results.append(PairResult(
                pair_key=pair_key_str,
                overlap={},
                signal_count=0,
                long_executable_count=0,
                diagnostic_only_count=0,
                valid_returns=[],
                valid_long_executable_returns=[],
                valid_diagnostic_returns=[],
                data_issue_reasons=["missing_stream_health"],
            ))
            continue

        overlap = compute_overlap(src_stream, tgt_stream)

        # Check for data issues
        data_issues: list[str] = []
        if not src_stream.subscription_ok:
            data_issues.append("source_subscription_failed")
        if not tgt_stream.subscription_ok:
            data_issues.append("target_subscription_failed")
        if src_stream.zero_tick_warning:
            data_issues.append("source_zero_ticks")
        if tgt_stream.zero_tick_warning:
            data_issues.append("target_zero_ticks")
        if not overlap.get("overlap_duration_ms", 0):
            data_issues.append("no_true_overlap")

        if data_issues:
            pair_results.append(PairResult(
                pair_key=pair_key_str,
                overlap=overlap,
                signal_count=0,
                long_executable_count=0,
                diagnostic_only_count=0,
                valid_returns=[],
                valid_long_executable_returns=[],
                valid_diagnostic_returns=[],
                data_issue_reasons=data_issues,
            ))
            continue

        # Remap signals for this target
        sum(1 for s in src_signals if s.signal_id.startswith(
            pair.source_venue + "_"
        ))
        remapped_signals = [
            _remap_signal_for_target(s, pair.target_venue, pair.target_symbol, i)
            for i, s in enumerate(src_signals)
        ]

        # Evaluate signals against target ticks
        all_signals = 0
        long_signals = 0
        diag_signals = 0
        valid_returns: list = []
        valid_long_returns: list = []
        valid_diag_returns: list = []

        for sig in remapped_signals:
            returns = evaluate_tick_signal(
                signal=sig,
                target_ticks=tgt_ticks,
                horizons_ms=horizons_ms,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
            )
            all_signals += 1
            is_long = sig.direction == "long"
            if is_long:
                long_signals += 1
            else:
                diag_signals += 1

            for r in returns:
                rdict = r.to_dict()
                all_forward_returns_dicts.append(rdict)
                valid_returns.append(r)
                if is_long:
                    valid_long_returns.append(r)
                else:
                    valid_diag_returns.append(r)

        # Build event dicts
        for sig in remapped_signals:
            ed = sig.to_dict()
            ed["pair_key"] = pair_key_str
            ed["long_executable"] = sig.direction == "long"
            ed["diagnostic_only"] = sig.direction != "long"
            all_events_dicts.append(ed)

        pair_res = PairResult(
            pair_key=pair_key_str,
            overlap=overlap,
            signal_count=all_signals,
            long_executable_count=long_signals,
            diagnostic_only_count=diag_signals,
            valid_returns=valid_returns,
            valid_long_executable_returns=valid_long_returns,
            valid_diagnostic_returns=valid_diag_returns,
        )

        # Run candidate gate evaluation
        valid_only = [r for r in valid_returns if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
        if valid_only:
            # Generate random baseline from source ticks
            src_ticks = tick_data.get(skey, [])
            if src_ticks:
                baseline_events = generate_random_baseline(
                    source_ticks=src_ticks,
                    signal_count=len(remapped_signals),
                    source_venue=pair.source_venue,
                    target_venue=pair.target_venue,
                    symbol=pair.target_symbol,
                    asset="CROSS_ASSET",
                    seed=42,
                )
                baseline_returns = []
                for be in baseline_events:
                    be.target_venue = pair.target_venue
                    be.target_symbol = pair.target_symbol
                    br = evaluate_tick_signal(
                        signal=be,
                        target_ticks=tgt_ticks,
                        horizons_ms=horizons_ms,
                        fee_bps=fee_bps,
                        slippage_bps=slippage_bps,
                        quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
                    )
                    baseline_returns.extend(br)

                gate_result = evaluate_candidate_group(
                    forward_returns=valid_only,
                    baseline_forward_returns=baseline_returns,
                    min_events=min_events,
                    baseline_margin_bps=1.0,
                )
                pair_res.candidate_gate = gate_result

                # Separate gate for long-executable subset
                valid_long_only = [r for r in valid_long_returns if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
                if valid_long_only:
                    # Subset baseline
                    baseline_long_events = generate_random_baseline(
                        source_ticks=src_ticks,
                        signal_count=long_signals if long_signals else len(remapped_signals),
                        source_venue=pair.source_venue,
                        target_venue=pair.target_venue,
                        symbol=pair.target_symbol,
                        asset="CROSS_ASSET",
                        seed=42,
                    )
                    baseline_long_returns = []
                    for be in baseline_long_events:
                        be.target_venue = pair.target_venue
                        be.target_symbol = pair.target_symbol
                        br = evaluate_tick_signal(
                            signal=be,
                            target_ticks=tgt_ticks,
                            horizons_ms=horizons_ms,
                            fee_bps=fee_bps,
                            slippage_bps=slippage_bps,
                            quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
                        )
                        baseline_long_returns.extend(br)

                    pair_res.candidate_gate_long = evaluate_candidate_group(
                        forward_returns=valid_long_only,
                        baseline_forward_returns=baseline_long_returns,
                        min_events=min_events,
                        baseline_margin_bps=1.0,
                    )

                # Separate gate for diagnostic subset
                valid_diag_only = [r for r in valid_diag_returns if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
                if valid_diag_only:
                    baseline_diag_events = generate_random_baseline(
                        source_ticks=src_ticks,
                        signal_count=diag_signals if diag_signals else len(remapped_signals),
                        source_venue=pair.source_venue,
                        target_venue=pair.target_venue,
                        symbol=pair.target_symbol,
                        asset="CROSS_ASSET",
                        seed=42 + 1,
                    )
                    baseline_diag_returns = []
                    for be in baseline_diag_events:
                        be.target_venue = pair.target_venue
                        be.target_symbol = pair.target_symbol
                        br = evaluate_tick_signal(
                            signal=be,
                            target_ticks=tgt_ticks,
                            horizons_ms=horizons_ms,
                            fee_bps=fee_bps,
                            slippage_bps=slippage_bps,
                            quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
                        )
                        baseline_diag_returns.extend(br)

                    pair_res.candidate_gate_diagnostic = evaluate_candidate_group(
                        forward_returns=valid_diag_only,
                        baseline_forward_returns=baseline_diag_returns,
                        min_events=min_events,
                        baseline_margin_bps=1.0,
                    )
            else:
                pair_res.candidate_gate = {
                    "candidate": False,
                    "rejection_reasons": ["no_source_ticks_for_baseline"],
                }

        pair_results.append(pair_res)
        logger.info(
            "  %s: %d signals (%d long, %d diagnostic), %d valid returns",
            pair_key_str, all_signals, long_signals, diag_signals, len(valid_returns),
        )

    # ── Compute verdict ─────────────────────────────────────────────────
    logger.info("Computing verdict ...")
    verdict_obj = compute_verdict(
        pair_results=pair_results,
        stream_health=stream_health,
        same_symbol_skips=same_symbol_skips,
        source_venues=source_venues,
        source_symbols=source_symbols,
        target_venues=target_venues,
        target_symbols=target_symbols,
        min_source_range_bps=min_source_range_bps,
        min_target_range_bps=min_target_range_bps,
        min_events=min_events,
    )

    # ── Write outputs ───────────────────────────────────────────────────
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Events JSONL
    events_path = out_dir / "cross_asset_impulse_events.jsonl"
    with open(events_path, "w") as fh:
        for ed in all_events_dicts:
            fh.write(json.dumps(ed) + "\n")

    # Forward returns JSONL
    fr_path = out_dir / "cross_asset_forward_returns.jsonl"
    with open(fr_path, "w") as fh:
        fh.writelines(json.dumps(rd) + "\n" for rd in all_forward_returns_dicts)

    # Summary JSON
    summary_dict = {
        "verdict": verdict_obj.verdict,
        "reasons": verdict_obj.reasons,
        "total_signal_events": verdict_obj.total_signal_events,
        "total_long_executable": verdict_obj.total_long_executable,
        "total_diagnostic_only": verdict_obj.total_diagnostic_only,
        "pairs_evaluated": verdict_obj.pairs_evaluated,
        "pairs_with_overlap": verdict_obj.pairs_with_overlap,
        "pairs_with_sufficient_data": verdict_obj.pairs_with_sufficient_data,
        "pairs_with_events": verdict_obj.pairs_with_events,
        "best_pair": verdict_obj.best_pair,
        "best_pair_mean_net_bps": verdict_obj.best_pair_mean_net,
        "worst_pair": verdict_obj.worst_pair,
        "worst_pair_mean_net_bps": verdict_obj.worst_pair_mean_net,
        "best_long_executable_group": verdict_obj.best_long_executable_group,
        "best_long_executable_mean_net_bps": verdict_obj.best_long_executable_mean_net,
        "best_diagnostic_group": verdict_obj.best_diagnostic_group,
        "best_diagnostic_mean_net_bps": verdict_obj.best_diagnostic_mean_net,
        "candidate_pairs": verdict_obj.candidate_pairs,
        "stream_health": verdict_obj.stream_health,
        "stream_warnings": verdict_obj.stream_warnings,
        "same_symbol_skips": verdict_obj.same_symbol_skips,
        "elapsed_seconds": round(time.monotonic() - t_start, 2),
    }

    summary_path = out_dir / "cross_asset_summary.json"
    with open(summary_path, "w") as fh:
        json.dump(summary_dict, fh, indent=2)

    # Summary CSV
    csv_path = out_dir / "cross_asset_summary.csv"
    with open(csv_path, "w") as fh:
        fh.write("pair_key,signals,long_executable,diagnostic_only,has_overlap,overlap_duration_ms,source_range_bps,target_range_bps,valid_returns,candidate,data_issue\n")
        for pr in pair_results:
            cg = pr.candidate_gate or {}
            is_candidate = cg.get("candidate", False)
            fh.write(
                f"{pr.pair_key},{pr.signal_count},{pr.long_executable_count},"
                f"{pr.diagnostic_only_count},{pr.has_true_overlap},"
                f"{pr.overlap.get('overlap_duration_ms', 0)},{pr.overlap.get('source_range_bps', 0)},"
                f"{pr.overlap.get('target_range_bps', 0)},{len(pr.valid_returns)},"
                f"{is_candidate},{','.join(pr.data_issue_reasons)}\n"
            )

    # Rejections JSON
    rejections = []
    for pr in pair_results:
        if pr.data_issue_reasons:
            rejections.append({
                "pair_key": pr.pair_key,
                "reason": "data_issue",
                "data_issues": pr.data_issue_reasons,
            })
        elif pr.candidate_gate and not pr.candidate_gate.get("candidate", False):
            rejections.append({
                "pair_key": pr.pair_key,
                "reason": "candidate_gate",
                "rejection_reasons": pr.candidate_gate.get("rejection_reasons", []),
            })

    rej_path = out_dir / "cross_asset_rejections.json"
    with open(rej_path, "w") as fh:
        json.dump(rejections, fh, indent=2)

    # Markdown report
    generate_markdown_report(
        verdict_obj=verdict_obj,
        all_in_cost_bps=all_in_cost_bps,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
        signal_types=signal_types,
        lookbacks_ms=lookbacks_ms,
        horizons_ms=horizons_ms,
        cooldown_ms=args.cooldown_ms,
        baseline_window_ms=baseline_window_ms,
        min_source_range_bps=min_source_range_bps,
        min_target_range_bps=min_target_range_bps,
        out_dir=str(out_dir),
        num_pairs_evaluated=len(pair_results),
    )

    # ── Print verdict ───────────────────────────────────────────────────
    elapsed = time.monotonic() - t_start
    print()
    print("=" * 92)
    print(f"  CROSS-ASSET SPOT IMPULSE v1 — {verdict_obj.verdict}")
    print("=" * 92)
    print(f"  Source: {', '.join(source_venues)} | {', '.join(source_symbols)}")
    print(f"  Target: {', '.join(target_venues)} | {', '.join(target_symbols)}")
    print(f"  Signals: {verdict_obj.total_signal_events} "
          f"({verdict_obj.total_long_executable} long-executable, "
          f"{verdict_obj.total_diagnostic_only} diagnostic)")
    print(f"  Pairs evaluated: {verdict_obj.pairs_evaluated} "
          f"(overlap: {verdict_obj.pairs_with_overlap}, "
          f"sufficient: {verdict_obj.pairs_with_sufficient_data})")
    print(f"  All-in cost wall: {all_in_cost_bps} bps")
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Reports: {out_dir}")

    if verdict_obj.candidate_pairs:
        print(f"  Candidates: {verdict_obj.candidate_pairs}")
    if verdict_obj.best_pair:
        print(f"  Best pair: {verdict_obj.best_pair} ({_format_optional_bps(verdict_obj.best_pair_mean_net)} bps)")
    if verdict_obj.worst_pair:
        print(f"  Worst pair: {verdict_obj.worst_pair} ({_format_optional_bps(verdict_obj.worst_pair_mean_net)} bps)")
    if verdict_obj.best_long_executable_group:
        print(f"  Best long-executable: {verdict_obj.best_long_executable_group} "
              f"({_format_optional_bps(verdict_obj.best_long_executable_mean_net)} bps)")
    if verdict_obj.stream_warnings:
        print(f"  ⚠️ Stream warnings: {len(verdict_obj.stream_warnings)}")
        for w in verdict_obj.stream_warnings[:5]:
            print(f"    - {w}")
    print()

    for r in verdict_obj.reasons:
        print(f"  Reason: {r}")
    print()


if __name__ == "__main__":
    main()
