#!/usr/bin/env python3
"""
Size-ladder diagnostic closeout for complement arb.

Runs a bounded fresh observation, captures cumulative_fillable_volume per leg
alongside depth_ahead, then recomputes pessimistic fill outcomes at each
ladder quote size [5, 10, 25, 50, 100].

This is a diagnostic closeout only. It does not:
- Promote the strategy to live trading
- Create CANDIDATE_FOR_LIVE / TRADE_READY / EXECUTION_READY
- Add order submission, signing, or private-key handling
- Tune thresholds after seeing results
- Loosen pessimistic fill rules
- Alter the base frozen status

Usage:
    cd /path/to/nautilus_trader
    python -m examples.strategies.polymarket_complement_arb.run_size_ladder_diagnostic
        --event-slugs btc-updown-15m-1778956200
        --duration 180
        --quote-sizes 5,10,25,50,100
        --output-root reports/polymarket_complement_arb_size_ladder_closeout
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import ComplementArbConfig
from .market_filter import extract_complement_markets
from .models import BookSnapshot
from .reports import compute_config_hash, generate_run_id
from .shadow_execution import (
    FillAssumption,
    FillStatus,
    LegFillResult,
    LegQuote,
    MarketTrade,
    ShadowOpportunity,
    ShadowOpportunityResult,
    TradeSide,
    _is_trade_fill_compatible,
    classify_shadow_verdict,
    evaluate_pessimistic_fill,
    evaluate_shadow_opportunity,
    shadow_result_to_row,
    write_shadow_reports,
)
from .run_shadow_observe import (
    CRYPTO_UP_PATTERN,
    TRADE_FETCH_LIMIT,
    _HOURLY_CRYPTO_PATTERN,
    _HOURLY_ASSET_MAP,
    DURATION_LABELS,
    UrlPublicDataClient,
    _build_market_from_raw,
    _depth_ahead,
    _discover_crypto_updown,
    _discover_markets,
    _get_json,
    _is_crypto_updown_event,
    _levels,
    _market_meta,
    _market_trades,
    _parse_asset_from_slug,
    _parse_duration_from_slug,
    _resolution_danger,
    _resolve_event_slugs,
    build_shadow_opportunity,
)

logger = logging.getLogger(__name__)

# Ladder quote sizes for diagnostic closeout
DEFAULT_LADDER_SIZES = [5, 10, 25, 50, 100]


def git_sha() -> str:
    try:
        return subprocess.check_output(
            ["/usr/bin/git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _leg_effective_price(quote: LegQuote) -> float:
    """Return the price to use for sizing/usdc calculations."""
    return max(quote.price, 0.01)


def _classify_dust(
    quote_size: float,
    max_price: float,
    net_edge_per_share: float,
    config: ComplementArbConfig,
) -> tuple[bool, str]:
    """
    Classify a quote size as dust or economic.

    Returns (is_dust, reason_code).
    """
    # Notional check: quote_size * max_price must meet min_order_usdc
    notional = quote_size * max_price
    if notional < config.min_order_usdc:
        return True, f"NOTIONAL_DUST: {notional:.4f} USDC < min_order_usdc={config.min_order_usdc}"

    # Net edge per share check
    if net_edge_per_share < config.diagnostic_min_net_edge_per_share:
        return True, (
            f"NET_EDGE_DUST: {net_edge_per_share:.6f} per share "
            f"< diagnostic_min_net_edge_per_share={config.diagnostic_min_net_edge_per_share}"
        )

    # Net harvest USDC check: net_edge_per_share * quote_size
    net_harvest = net_edge_per_share * quote_size
    if net_harvest < config.diagnostic_min_economic_net_harvest_usdc:
        return True, (
            f"HARVEST_DUST: {net_harvest:.6f} USDC "
            f"< diagnostic_min_economic_net_harvest_usdc={config.diagnostic_min_economic_net_harvest_usdc}"
        )

    return False, "ECONOMIC"


def _fill_status_str(fill_result: LegFillResult) -> str:
    return fill_result.status.value


def _compute_ladder_for_opportunity(
    opp: ShadowOpportunity,
    yes_trades: list[MarketTrade],
    no_trades: list[MarketTrade],
    edge_timeline: list[tuple[int, float]] | None,
    unwind_price: float | None,
    ladder_sizes: list[int],
    config: ComplementArbConfig,
    midpoint_edge_used: bool = False,
) -> dict[str, Any]:
    """Compute pessimistic fill outcomes for a single opportunity at all ladder sizes."""
    max_price = max(
        opp.yes_quote.best_ask,
        opp.no_quote.best_ask,
        0.01,
    )

    # Evaluate at base size (max_safe_shares) for reference
    base_result = evaluate_shadow_opportunity(
        opp, FillAssumption.PESSIMISTIC, yes_trades, no_trades,
        edge_timeline=edge_timeline, midpoint_edge_used=midpoint_edge_used,
        unwind_price=unwind_price,
    )

    entries = []
    for qsize in ladder_sizes:
        # Create modified quotes with the ladder size
        yes_quote = LegQuote(
            token_side=opp.yes_quote.token_side,
            quote_side=opp.yes_quote.quote_side,
            timestamp_ns=opp.yes_quote.timestamp_ns,
            price=opp.yes_quote.price,
            size=float(qsize),
            depth_ahead=opp.yes_quote.depth_ahead,
            best_bid=opp.yes_quote.best_bid,
            best_ask=opp.yes_quote.best_ask,
            executable_ask=opp.yes_quote.executable_ask,
            visible_depth_usdc=opp.yes_quote.visible_depth_usdc,
        )
        no_quote = LegQuote(
            token_side=opp.no_quote.token_side,
            quote_side=opp.no_quote.quote_side,
            timestamp_ns=opp.no_quote.timestamp_ns,
            price=opp.no_quote.price,
            size=float(qsize),
            depth_ahead=opp.no_quote.depth_ahead,
            best_bid=opp.no_quote.best_bid,
            best_ask=opp.no_quote.best_ask,
            executable_ask=opp.no_quote.executable_ask,
            visible_depth_usdc=opp.no_quote.visible_depth_usdc,
        )

        yes_fill = evaluate_pessimistic_fill(yes_quote, yes_trades)
        no_fill = evaluate_pessimistic_fill(no_quote, no_trades)

        full_yes = yes_fill.status == FillStatus.FULL
        full_no = no_fill.status == FillStatus.FULL
        partial_yes = yes_fill.status == FillStatus.PARTIAL
        partial_no = no_fill.status == FillStatus.PARTIAL
        paired = full_yes and full_no
        one_leg = full_yes ^ full_no

        # Dust classification
        is_dust, dust_reason = _classify_dust(
            float(qsize), max_price, opp.net_edge_per_share, config
        )

        entries.append({
            "quote_size": qsize,
            "yes_fill_status": yes_fill.status.value,
            "no_fill_status": no_fill.status.value,
            "yes_cumulative_fillable_volume": yes_fill.cumulative_fillable_volume,
            "no_cumulative_fillable_volume": no_fill.cumulative_fillable_volume,
            "yes_depth_ahead": opp.yes_quote.depth_ahead,
            "no_depth_ahead": opp.no_quote.depth_ahead,
            "paired_fill": paired,
            "one_leg_fill": one_leg,
            "net_edge_per_share": opp.net_edge_per_share,
            "notional_usdc": float(qsize) * max_price,
            "is_dust": is_dust,
            "dust_reason": dust_reason,
        })

    return {
        "opportunity": {
            "condition_id": opp.condition_id,
            "market_slug": opp.market_slug,
            "timestamp_ns": opp.timestamp_ns,
            "yes_token_id": opp.yes_token_id,
            "no_token_id": opp.no_token_id,
            "sum_asks": opp.sum_asks,
            "net_edge_per_share": opp.net_edge_per_share,
            "max_safe_shares": opp.max_safe_shares,
            "pre_fill_reject_reason": base_result.reject_reason,
        },
        "ladder_entries": entries,
    }


def _accumulate_ladder(
    ladder_results: list[dict[str, Any]],
    config: ComplementArbConfig,
) -> dict[int, dict[str, Any]]:
    """Accumulate ladder statistics across all opportunities."""
    by_size: dict[int, dict[str, Any]] = {}
    for qsize in DEFAULT_LADDER_SIZES:
        by_size[qsize] = {
            "quote_size": qsize,
            "total_opportunities_considered": 0,
            "with_sufficient_trade_evidence": 0,
            "non_dust_opportunities": 0,
            "pessimistic_paired_fills": 0,
            "one_leg_fills": 0,
            "median_paired_fill_realized_net_edge_per_share": None,
            "paired_gain": 0.0,
            "unwind_loss": 0.0,
            "net_shadow_harvest": 0.0,
            "expected_edge_per_detected_opportunity": 0.0,
            "dust_classifications": [],
            "economic_classifications": [],
            "paired_fill_edges": [],
        }

    for result in ladder_results:
        opp = result["opportunity"]
        for entry in result["ladder_entries"]:
            qs = entry["quote_size"]
            stats = by_size[qs]
            stats["total_opportunities_considered"] += 1

            # Count sufficient trade evidence (both legs had trades)
            yes_vol = entry["yes_cumulative_fillable_volume"]
            no_vol = entry["no_cumulative_fillable_volume"]
            if yes_vol > 0 or no_vol > 0:
                stats["with_sufficient_trade_evidence"] += 1

            dust_class = "dust" if entry["is_dust"] else "economic"
            stats["dust_classifications"].append(dust_class)
            if not entry["is_dust"]:
                stats["economic_classifications"].append(dust_class)
                stats["non_dust_opportunities"] += 1

            if entry["paired_fill"]:
                stats["pessimistic_paired_fills"] += 1
                edge = opp["net_edge_per_share"]
                gain = edge * float(qs)
                stats["paired_gain"] += gain
                stats["paired_fill_edges"].append(edge)

            if entry["one_leg_fill"]:
                stats["one_leg_fills"] += 1
                # Unwind loss: one filled leg × edge loss
                filled_size = float(qs)
                stats["unwind_loss"] += filled_size * opp["net_edge_per_share"]

    # Finalize per-size stats
    for qs, stats in by_size.items():
        n = len(stats.get("paired_fill_edges", []))
        if n > 0:
            edges = sorted(stats["paired_fill_edges"])
            stats["median_paired_fill_realized_net_edge_per_share"] = edges[n // 2]
        stats["net_shadow_harvest"] = stats["paired_gain"] - stats["unwind_loss"]
        total = stats["total_opportunities_considered"]
        stats["expected_edge_per_detected_opportunity"] = (
            stats["net_shadow_harvest"] / total if total else 0.0
        )
        dust_d = stats["dust_classifications"].count("dust")
        econ_d = stats["dust_classifications"].count("economic")
        stats["dust_count"] = dust_d
        stats["economic_count"] = econ_d
        del stats["dust_classifications"]
        del stats["economic_classifications"]
        del stats["paired_fill_edges"]

    return by_size


def _render_ladder_report(
    by_size: dict[int, dict[str, Any]],
    config: ComplementArbConfig,
    total_opportunities: int,
) -> str:
    lines = [
        "# Size Ladder Diagnostic Closeout — Complement Arb",
        "",
        "## Configuration",
        "",
        f"- dust threshold notional: min_order_usdc = {config.min_order_usdc}",
        f"- min net edge per share: {config.diagnostic_min_net_edge_per_share}",
        f"- min economic net harvest USDC: {config.diagnostic_min_economic_net_harvest_usdc}",
        "",
        "A quote size is NOT economically valid unless it passes ALL three thresholds:",
        "1. Notional >= min_order_usdc",
        "2. Net edge per share >= diagnostic_min_net_edge_per_share",
        "3. Net harvest USDC >= diagnostic_min_economic_net_harvest_usdc",
        "",
        "## Per-Size Results",
        "",
    ]

    # Table header
    header = (
        "| Quote Size | Opportunities | Non-Dust | Pess Paired | One-Leg | "
        "Median Paired Edge/Shr | Paired Gain | Unwind Loss | Net Harvest | "
        "Exp Edge/Opp | Dust Count |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|"
    lines.extend([header, sep])

    for qs in sorted(by_size.keys()):
        s = by_size[qs]
        med = s["median_paired_fill_realized_net_edge_per_share"]
        med_str = f"{med:.6f}" if med is not None else "N/A"
        lines.append(
            f"| {qs} | {s['total_opportunities_considered']} | "
            f"{s['non_dust_opportunities']} | {s['pessimistic_paired_fills']} | "
            f"{s['one_leg_fills']} | {med_str} | "
            f"{s['paired_gain']:.4f} | {s['unwind_loss']:.4f} | "
            f"{s['net_shadow_harvest']:.4f} | "
            f"{s['expected_edge_per_detected_opportunity']:.6f} | "
            f"{s['dust_count']} |"
        )

    lines.extend([
        "",
        "## Interpretation",
        "",
        "The base frozen status FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE applies to",
        f"base_quote_size = 100. The size ladder above adds resolution at smaller",
        f"quote sizes without altering the base verdict.",
        "",
        "If any smaller quote size shows positive pessimistic paired fills:",
        "- It does NOT overturn the 100-share freeze",
        "- It reveals a possible FROZEN_EDGE_ONLY_AT_DUST_SIZE outcome",
        "- It does NOT create CANDIDATE_FOR_LONGER_OBSERVATION",
        "",
        "If ALL sizes show zero fills: the freeze deepens — no quote size produces",
        "executable pessimistic queue turnover on these markets.",
        "",
        "## Note",
        "",
        "This diagnostic was run after the original campaign and the hourly addendum.",
        f"Total opportunities observed in this diagnostic run: {total_opportunities}.",
        "",
    ])
    return "\n".join(lines)


async def run_size_ladder_diagnostic(
    *,
    markets: list,
    config: ComplementArbConfig,
    duration_secs: float,
    poll_interval_secs: float,
    ladder_sizes: list[int] | None = None,
    output_root: str | Path = "reports/polymarket_complement_arb_size_ladder_closeout",
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run a bounded diagnostic evaluation with size ladder."""
    ladder_sizes = ladder_sizes or DEFAULT_LADDER_SIZES
    run_id = run_id or generate_run_id()
    git_sha_val = git_sha()
    config_hash = compute_config_hash(config)
    start = time.time()
    deadline = start + max(0.0, duration_secs)
    output_dir = Path(output_root) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "config.json").write_text(
        json.dumps(asdict(config), indent=2, sort_keys=True, default=str)
    )
    (output_dir / "diagnostic_parameters.json").write_text(
        json.dumps({
            "run_id": run_id,
            "duration_secs": duration_secs,
            "poll_interval_secs": poll_interval_secs,
            "ladder_sizes": ladder_sizes,
            "git_sha": git_sha_val,
            "config_hash": config_hash,
            "ladder_run_type": "size_ladder_diagnostic",
            "replay_verdict": "REPLAY_SCHEMA_INSUFFICIENT",
            "source": "fresh_diagnostic_capture",
        }, indent=2, sort_keys=True, default=str)
    )

    client = UrlPublicDataClient()
    opportunities: list[ShadowOpportunity] = []
    trades_by_token: dict[str, list[dict[str, Any]]] = {}
    edge_timeline_by_condition: dict[str, list[tuple[int, float]]] = {}
    windows = 0
    book_fetches = 0
    book_errors = 0
    missing_trade_data_events = 0
    trade_fetch_attempt_count = 0
    trade_fetch_success_count = 0
    trade_fetch_empty_count = 0
    trade_fetch_error_count = 0
    trade_rows_total = 0
    last_trade_ts: int | None = None

    logger.info(
        "size-ladder diagnostic starting: duration=%ss, poll=%ss, sizes=%s, markets=%s",
        duration_secs, poll_interval_secs, ladder_sizes, len(markets),
    )

    while time.time() < deadline:
        windows += 1
        now_ms = time.time() * 1000
        ts_event_ns = time.time_ns()

        observed_condition_ids = ",".join(
            sorted({m.condition_id for m in markets})
        )
        trade_fetch_attempt_count += 1
        try:
            trade_rows = await client.fetch_trades(
                condition_ids=observed_condition_ids,
                after_ts=last_trade_ts,
                limit=TRADE_FETCH_LIMIT,
            )
            if isinstance(trade_rows, list):
                trade_fetch_success_count += 1
                if not trade_rows:
                    trade_fetch_empty_count += 1
                else:
                    max_row_ts = max(
                        int(r.get("timestamp", 0))
                        for r in trade_rows
                        if r.get("timestamp") is not None
                    )
                    if max_row_ts > (last_trade_ts or 0):
                        last_trade_ts = max_row_ts
                    for row in trade_rows:
                        asset = str(row.get("asset") or "")
                        if not asset:
                            continue
                        existing = {
                            json.dumps(r, sort_keys=True, default=str)
                            for r in trades_by_token.get(asset, [])
                        }
                        key = json.dumps(row, sort_keys=True, default=str)
                        if key not in existing:
                            trades_by_token.setdefault(asset, []).append(row)
                            trade_rows_total += 1
            else:
                trade_fetch_success_count += 1
        except Exception as exc:
            trade_fetch_error_count += 1
            logger.warning("trade fetch poll error=%s", exc)

        for market in markets:
            yes_book = await client.fetch_book(market.yes_token_id)
            no_book = await client.fetch_book(market.no_token_id)
            book_fetches += 2
            if yes_book is None or no_book is None:
                book_errors += 1
                continue
            opp = build_shadow_opportunity(
                market, yes_book, no_book, config,
                run_id=run_id, git_sha=git_sha_val,
                config_hash=config_hash,
                now_ms=now_ms, ts_event_ns=ts_event_ns,
            )
            if opp is not None:
                opportunities.append(opp)
                edge_timeline_by_condition.setdefault(
                    market.condition_id, []
                ).append((ts_event_ns, opp.net_edge_per_share))

        await asyncio.sleep(max(0.0, min(poll_interval_secs, deadline - time.time())))

    elapsed = time.time() - start
    logger.info(
        "observation loop complete: %ds, %d windows, %d opportunities, %d trade rows",
        elapsed, windows, len(opportunities), trade_rows_total,
    )

    # For each opportunity, compute ladder results
    ladder_results: list[dict[str, Any]] = []
    for opp in opportunities:
        yes_rows = trades_by_token.get(opp.yes_token_id, [])
        no_rows = trades_by_token.get(opp.no_token_id, [])
        if not yes_rows or not no_rows:
            missing_trade_data_events += 1
        yes_trades = _market_trades(yes_rows)
        no_trades = _market_trades(no_rows)
        edge_timeline = edge_timeline_by_condition.get(opp.condition_id, [])
        unwind_price = (
            opp.yes_quote.best_bid
            if yes_trades and not no_trades
            else opp.no_quote.best_bid
        )
        ladder_result = _compute_ladder_for_opportunity(
            opp, yes_trades, no_trades, edge_timeline, unwind_price,
            ladder_sizes, config,
        )
        ladder_results.append(ladder_result)

    # Accumulate per-size statistics
    by_size = _accumulate_ladder(ladder_results, config)

    # Write ladder results per opportunity
    ladder_rows_path = output_dir / "ladder_opportunities.jsonl"
    with ladder_rows_path.open("w") as f:
        for result in ladder_results:
            f.write(json.dumps(result, sort_keys=True, default=str) + "\n")

    # Write per-size summary
    by_size_path = output_dir / "ladder_summary.json"
    summary_data = {
        "run_id": run_id,
        "git_sha": git_sha_val,
        "config_hash": config_hash,
        "diagnostic_type": "size_ladder_diagnostic",
        "replay_verdict": "REPLAY_SCHEMA_INSUFFICIENT",
        "total_opportunities_observed": len(opportunities),
        "missing_trade_data_events": missing_trade_data_events,
        "windows_completed": windows,
        "book_fetches": book_fetches,
        "book_errors": book_errors,
        "trade_fetch_attempts": trade_fetch_attempt_count,
        "trade_fetch_successes": trade_fetch_success_count,
        "trade_rows_total": trade_rows_total,
        "ladder_sizes": ladder_sizes,
        "dust_thresholds": {
            "min_order_usdc": config.min_order_usdc,
            "diagnostic_min_net_edge_per_share": config.diagnostic_min_net_edge_per_share,
            "diagnostic_min_economic_net_harvest_usdc": config.diagnostic_min_economic_net_harvest_usdc,
        },
        "per_size": {str(qs): by_size[qs] for qs in sorted(by_size.keys())},
    }
    by_size_path.write_text(
        json.dumps(summary_data, indent=2, sort_keys=True, default=str)
    )

    # Write MD report
    report_path = output_dir / "ladder_report.md"
    report_content = _render_ladder_report(
        by_size, config, len(opportunities)
    )
    report_path.write_text(report_content)

    # Also run the standard shadow report for reference (at base size)
    all_results: list[ShadowOpportunityResult] = []
    for opp in opportunities:
        yes_rows = trades_by_token.get(opp.yes_token_id, [])
        no_rows = trades_by_token.get(opp.no_token_id, [])
        yes_trades = _market_trades(yes_rows)
        no_trades = _market_trades(no_rows)
        edge_timeline = edge_timeline_by_condition.get(opp.condition_id, [])
        unwind_price = (
            opp.yes_quote.best_bid
            if yes_trades and not no_trades
            else opp.no_quote.best_bid
        )
        for mode in (FillAssumption.PESSIMISTIC, FillAssumption.NEUTRAL, FillAssumption.OPTIMISTIC):
            r = evaluate_shadow_opportunity(
                opp, mode, yes_trades, no_trades,
                edge_timeline=edge_timeline, unwind_price=unwind_price,
            )
            all_results.append(r)

    write_shadow_reports(
        output_dir / "standard_reports",
        all_results,
        observer_window_count=windows,
    )

    logger.info("size-ladder diagnostic complete: %s", output_dir)
    return {
        "run_id": run_id,
        "output_dir": str(output_dir),
        "ladder_opportunities_path": str(ladder_rows_path),
        "ladder_summary_path": str(by_size_path),
        "ladder_report_path": str(report_path),
        "total_opportunities": len(opportunities),
        "by_size": {str(qs): by_size[qs] for qs in sorted(by_size.keys())},
    }


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run bounded size-ladder diagnostic for complement arb."
    )
    parser.add_argument("--duration", type=float, default=180.0)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--quote-sizes", type=str, default="5,10,25,50,100")
    parser.add_argument("--market-slug", type=str)
    parser.add_argument("--event-slug", type=str)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("reports/polymarket_complement_arb_size_ladder_closeout"),
    )
    parser.add_argument("--universe", type=str, default="general", choices=["general", "crypto-updown"])
    parser.add_argument("--assets", type=str, default="")
    parser.add_argument("--durations", type=str, default="")
    parser.add_argument("--event-slugs", type=str, default="")
    parser.add_argument(
        "--max-markets", type=int, default=20,
        help="Max markets to scan (passed to general discovery)",
    )
    args = parser.parse_args()

    quote_sizes = [int(s.strip()) for s in args.quote_sizes.split(",") if s.strip()]

    config = ComplementArbConfig(
        mode="observe",
        dry_run=True,
        max_markets=args.max_markets,
        market_slug_allowlist=(args.market_slug,) if args.market_slug else (),
        event_slug_allowlist=(args.event_slug,) if args.event_slug else (),
    )
    client = UrlPublicDataClient()

    event_slugs = (
        [s.strip() for s in args.event_slugs.split(",") if s.strip()]
        if args.event_slugs else None
    )
    crypto_assets: set[str] | None = None
    crypto_durations: set[str] | None = None
    if args.universe == "crypto-updown":
        crypto_assets = set(
            a.strip().upper() for a in args.assets.split(",") if a.strip()
        ) or {"BTC", "ETH"}
        crypto_durations = set(
            d.strip().lower() for d in args.durations.split(",") if d.strip()
        ) or {"5m", "15m", "1h"}
        logger.info(
            "diagnostic universe=crypto-updown assets=%s durations=%s",
            sorted(crypto_assets), sorted(crypto_durations),
        )

    markets, _ = await _discover_markets(
        config, client,
        event_slugs=event_slugs,
        crypto_assets=crypto_assets,
        crypto_durations=crypto_durations,
        limit=max(args.max_markets * 20, 200),
    )
    if not markets:
        logger.warning("no markets discovered for size-ladder diagnostic")
        print(json.dumps({"status": "NO_MARKETS_DISCOVERED"}, indent=2))
        return

    logger.info("size-ladder diagnostic discovered %d markets", len(markets))
    result = await run_size_ladder_diagnostic(
        markets=markets,
        config=config,
        duration_secs=args.duration,
        poll_interval_secs=args.poll_interval,
        ladder_sizes=quote_sizes,
        output_root=args.output_root,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(main())
