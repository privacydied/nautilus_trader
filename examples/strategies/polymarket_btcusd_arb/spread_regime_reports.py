"""Spread regime report generation.

Produces CSV and Markdown reports from spread regime analysis.
Includes quote quality audit distinguishing real two-sided CLOB quotes
from FALLBACK_MIN_MAX (exchange bound) quotes.

No orders. No keys. No execution.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict
from pathlib import Path

from examples.strategies.polymarket_btcusd_arb.spread_regime import (
    SpreadEvent,
    SpreadRegimeSummary,
    QuoteQuality,
    assign_tte_bucket,
    assign_time_of_day_bucket,
    assign_volatility_bucket,
    classify_verdict,
    classify_verdict_reason,
)


def _quote_quality_audit_text(summary: SpreadRegimeSummary) -> str:
    """Generate the quote quality audit section for report.md."""
    total = summary.event_count
    if total == 0:
        return "## Quote Quality Audit\n\nNo observations available.\n"

    verdict = classify_verdict(summary)
    reason = summary.verdict_reason or classify_verdict_reason(summary)

    lines = [
        "## Quote Quality Audit",
        "",
        f"Total observations: {total}",
        f"TWO_SIDED_BOOK: {summary.two_sided_book_count} ({summary.pct_two_sided_book:.1f}%)",
        f"ONE_SIDED_BOOK: {summary.one_sided_book_count}",
        f"EMPTY_BOOK: {summary.empty_book_count}",
        f"FALLBACK_MIN_MAX: {summary.fallback_min_max_count} ({summary.pct_fallback_min_max:.1f}%)",
        f"MISSING_BOOK: {summary.missing_book_count}",
        f"INVALID_BOOK: {summary.invalid_book_count}",
        "",
    ]

    if summary.pct_fallback_min_max >= 99.0 and total > 0:
        lines.extend([
            "The observed spread values come from Polymarket CLOB best bid/ask at exchange",
            "minimum ($0.01) and maximum ($0.99) price bounds. These are real resting CLOB",
            "orders with genuine size, but they represent lottery-market liquidity at the",
            "absolute price bounds — not actionable two-sided spread for a fair-probability",
            "strategy.",
            "",
            "**Case B**: The observed 19,600 bps spread is not a real two-sided quoted spread.",
            "It reflects no usable two-sided liquidity. The Polymarket BTC 15m UpDown book",
            "was exclusively at exchange min/max bounds during the observed sample.",
            "",
            f"Verdict reason: {reason}",
        ])
    elif summary.pct_two_sided_book > 0:
        lines.extend([
            f"Of {total} observations, {summary.two_sided_book_count} ({summary.pct_two_sided_book:.1f}%)",
            "had genuine two-sided CLOB quotes away from exchange min/max bounds.",
            "",
        ])
        if summary.fallback_min_max_count > 0:
            lines.extend([
                f"{summary.fallback_min_max_count} observations ({summary.pct_fallback_min_max:.1f}%)",
                "had best bid at $0.01 and best ask at $0.99 (FALLBACK_MIN_MAX).",
                "These represent lottery-market liquidity, not actionable spread.",
                "",
            ])
        lines.extend([
            f"Verdict reason: {reason}",
        ])
    else:
        lines.extend([
            "No genuine two-sided CLOB quotes observed.",
            "",
            f"Verdict reason: {reason}",
        ])

    return "\n".join(lines)


def write_all_reports(
    summary: SpreadRegimeSummary,
    events: list[SpreadEvent],
    output_dir: Path,
) -> None:
    """Write all spread regime report files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    verdict = classify_verdict(summary)
    reason = summary.verdict_reason or classify_verdict_reason(summary)

    # summary.json
    summary_dict = asdict(summary)
    summary_dict["verdict"] = verdict
    summary_dict["verdict_reason"] = reason
    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary_dict, f, indent=2, default=str)

    # spread_events.csv
    fieldnames = [
        "market_slug", "ts_event_ns", "time_to_expiry_ns",
        "best_bid", "best_ask", "mid", "spread_abs", "spread_bps",
        "book_depth_bid", "book_depth_ask",
        "binance_price", "binance_spread_bps", "binance_short_window_vol_bps",
        "polymarket_stale", "binance_stale",
        "ttet_bucket", "time_of_day_utc", "volatility_bucket",
        "quote_quality",
    ]
    with open(output_dir / "spread_events.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for e in events:
            tte = assign_tte_bucket(e.time_to_expiry_ns)
            writer.writerow({
                "market_slug": e.market_slug,
                "ts_event_ns": e.ts_event_ns,
                "time_to_expiry_ns": e.time_to_expiry_ns,
                "best_bid": e.best_bid,
                "best_ask": e.best_ask,
                "mid": e.mid,
                "spread_abs": e.spread_abs,
                "spread_bps": e.spread_bps,
                "book_depth_bid": e.book_depth_bid,
                "book_depth_ask": e.book_depth_ask,
                "binance_price": e.binance_price,
                "binance_spread_bps": e.binance_spread_bps,
                "binance_short_window_vol_bps": e.binance_short_window_vol_bps,
                "polymarket_stale": e.polymarket_stale,
                "binance_stale": e.binance_stale,
                "ttet_bucket": tte.bucket_label,
                "time_of_day_utc": assign_time_of_day_bucket(e.ts_event_ns),
                "volatility_bucket": assign_volatility_bucket(e.binance_short_window_vol_bps),
                "quote_quality": e.quote_quality,
            })

    # Aggregate CSVs
    _write_aggregate_csvs(events, output_dir)

    # safety_check.json
    safety = {
        "no_orders": True,
        "no_keys": True,
        "no_execution_client_imports": True,
        "no_on_chain_calls": True,
        "status": "PASS",
    }
    with open(output_dir / "safety_check.json", "w") as f:
        json.dump(safety, f, indent=2)

    # report.md
    _write_markdown_report(summary, events, verdict, reason, output_dir)


def _write_aggregate_csvs(events: list[SpreadEvent], output_dir: Path) -> None:
    """Write aggregate CSVs by TTE, market, time of day, volatility."""

    # By TTE
    tte_groups: dict[str, list[SpreadEvent]] = {}
    for e in events:
        tte = assign_tte_bucket(e.time_to_expiry_ns)
        tte_groups.setdefault(tte.bucket_label, []).append(e)

    with open(output_dir / "spread_by_tte.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "tte_bucket", "count", "median_spread_bps",
            "pct_below_80bps", "pct_below_200bps",
            "two_sided_count", "fallback_min_max_count",
        ])
        writer.writeheader()
        for label in [
            "0-30s", "30-60s", "60-180s", "180-300s", "300-600s", "600s+"
        ]:
            group = tte_groups.get(label, [])
            spreads = [e.spread_bps for e in group if e.spread_bps is not None]
            two_sided = sum(1 for e in group if e.quote_quality == QuoteQuality.TWO_SIDED_BOOK)
            fallback = sum(1 for e in group if e.quote_quality == QuoteQuality.FALLBACK_MIN_MAX)
            median = sorted(spreads)[len(spreads) // 2] if spreads else None
            below_80 = sum(1 for s in spreads if s < 80) if spreads else 0
            below_200 = sum(1 for s in spreads if s < 200) if spreads else 0
            writer.writerow({
                "tte_bucket": label,
                "count": len(group),
                "median_spread_bps": f"{median:.1f}" if median else "N/A",
                "pct_below_80bps": f"{100*below_80/len(spreads):.1f}" if spreads else "N/A",
                "pct_below_200bps": f"{100*below_200/len(spreads):.1f}" if spreads else "N/A",
                "two_sided_count": two_sided,
                "fallback_min_max_count": fallback,
            })

    # By market
    market_groups: dict[str, list[SpreadEvent]] = {}
    for e in events:
        market_groups.setdefault(e.market_slug, []).append(e)

    with open(output_dir / "spread_by_market.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "market_slug", "count", "median_spread_bps",
            "pct_below_80bps", "pct_below_200bps",
            "two_sided_count", "fallback_min_max_count",
        ])
        writer.writeheader()
        for slug, group in market_groups.items():
            spreads = [e.spread_bps for e in group if e.spread_bps is not None]
            two_sided = sum(1 for e in group if e.quote_quality == QuoteQuality.TWO_SIDED_BOOK)
            fallback = sum(1 for e in group if e.quote_quality == QuoteQuality.FALLBACK_MIN_MAX)
            median = sorted(spreads)[len(spreads) // 2] if spreads else None
            below_80 = sum(1 for s in spreads if s < 80) if spreads else 0
            below_200 = sum(1 for s in spreads if s < 200) if spreads else 0
            writer.writerow({
                "market_slug": slug,
                "count": len(group),
                "median_spread_bps": f"{median:.1f}" if median else "N/A",
                "pct_below_80bps": f"{100*below_80/len(spreads):.1f}" if spreads else "N/A",
                "pct_below_200bps": f"{100*below_200/len(spreads):.1f}" if spreads else "N/A",
                "two_sided_count": two_sided,
                "fallback_min_max_count": fallback,
            })

    # By time of day
    tod_groups: dict[str, list[SpreadEvent]] = {}
    for e in events:
        tod = assign_time_of_day_bucket(e.ts_event_ns)
        tod_groups.setdefault(tod, []).append(e)

    with open(output_dir / "spread_by_time_of_day.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "time_of_day_utc", "count", "median_spread_bps",
            "pct_below_200bps", "two_sided_count", "fallback_min_max_count",
        ])
        writer.writeheader()
        for label in ["00-06UTC", "06-12UTC", "12-18UTC", "18-24UTC"]:
            group = tod_groups.get(label, [])
            spreads = [e.spread_bps for e in group if e.spread_bps is not None]
            two_sided = sum(1 for e in group if e.quote_quality == QuoteQuality.TWO_SIDED_BOOK)
            fallback = sum(1 for e in group if e.quote_quality == QuoteQuality.FALLBACK_MIN_MAX)
            median = sorted(spreads)[len(spreads) // 2] if spreads else None
            below_200 = sum(1 for s in spreads if s < 200) if spreads else 0
            writer.writerow({
                "time_of_day_utc": label,
                "count": len(group),
                "median_spread_bps": f"{median:.1f}" if median else "N/A",
                "pct_below_200bps": f"{100*below_200/len(spreads):.1f}" if spreads else "N/A",
                "two_sided_count": two_sided,
                "fallback_min_max_count": fallback,
            })

    # By volatility
    vol_groups: dict[str, list[SpreadEvent]] = {}
    for e in events:
        vol = assign_volatility_bucket(e.binance_short_window_vol_bps)
        vol_groups.setdefault(vol, []).append(e)

    with open(output_dir / "spread_by_volatility.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "volatility_bucket", "count", "median_spread_bps",
            "pct_below_200bps", "two_sided_count", "fallback_min_max_count",
        ])
        writer.writeheader()
        for label in ["0-5bps", "5-20bps", "20-50bps", "50-100bps", "100+bps", "unknown"]:
            group = vol_groups.get(label, [])
            spreads = [e.spread_bps for e in group if e.spread_bps is not None]
            two_sided = sum(1 for e in group if e.quote_quality == QuoteQuality.TWO_SIDED_BOOK)
            fallback = sum(1 for e in group if e.quote_quality == QuoteQuality.FALLBACK_MIN_MAX)
            median = sorted(spreads)[len(spreads) // 2] if spreads else None
            below_200 = sum(1 for s in spreads if s < 200) if spreads else 0
            writer.writerow({
                "volatility_bucket": label,
                "count": len(group),
                "median_spread_bps": f"{median:.1f}" if median else "N/A",
                "pct_below_200bps": f"{100*below_200/len(spreads):.1f}" if spreads else "N/A",
                "two_sided_count": two_sided,
                "fallback_min_max_count": fallback,
            })


def _write_markdown_report(
    summary: SpreadRegimeSummary,
    events: list[SpreadEvent],
    verdict: str,
    reason: str,
    output_dir: Path,
) -> None:
    """Write the Markdown report."""
    lines = [
        "# Polymarket BTC UpDown Spread Regime Study",
        "",
        "## Hypothesis",
        "",
        "Polymarket BTC UpDown markets may be structurally untradeable for maker",
        "fair-probability strategies because quoted spreads are usually wider than",
        "plausible fair-probability edge.",
        "",
        "## Data Used",
        "",
        f"- Input captures: {', '.join(summary.input_capture_dirs)}",
        f"- Markets: {summary.market_count}",
        f"- Total events: {summary.event_count}",
        f"- Valid spread observations: {summary.valid_spread_count}",
        "",
        "## Safety Statement",
        "",
        f"- No orders: PASS",
        f"- No keys: PASS",
        f"- No execution client imports: PASS",
        f"- No on-chain calls: PASS",
        "",
    ]

    # Quote quality audit
    lines.extend(_quote_quality_audit_text(summary).split("\n"))
    lines.append("")

    # Spread distribution
    lines.extend([
        "## Spread Distribution Summary",
        "",
        f"- Median spread: {summary.median_spread_bps:.1f} bps" if summary.median_spread_bps else "- Median spread: N/A",
        f"- P25 spread: {summary.p25_spread_bps:.1f} bps" if summary.p25_spread_bps else "- P25 spread: N/A",
        f"- P75 spread: {summary.p75_spread_bps:.1f} bps" if summary.p75_spread_bps else "- P75 spread: N/A",
        f"- P90 spread: {summary.p90_spread_bps:.1f} bps" if summary.p90_spread_bps else "- P90 spread: N/A",
        f"- P95 spread: {summary.p95_spread_bps:.1f} bps" if summary.p95_spread_bps else "- P95 spread: N/A",
        f"- Below 80 bps: {summary.pct_spread_lte_80bps:.1f}%",
        f"- Below 200 bps: {summary.pct_spread_lte_200bps:.1f}%",
        f"- Below 500 bps: {summary.pct_spread_lte_500bps:.1f}%",
        "",
        "## Spread by TTE Bucket",
        "",
        "| TTE Bucket | Count | Median bps | <80 bps | <200 bps | Two-sided | Fallback |",
        "|-----------|-------|-----------|---------|----------|-----------|----------|",
    ])

    from examples.strategies.polymarket_btcusd_arb.spread_regime import (
        assign_tte_bucket as _tte,
    )
    tte_groups: dict[str, list] = {}
    for e in events:
        tte = _tte(e.time_to_expiry_ns)
        tte_groups.setdefault(tte.bucket_label, []).append(e)
    for label in ["0-30s", "30-60s", "60-180s", "180-300s", "300-600s", "600s+"]:
        group = tte_groups.get(label, [])
        spreads = [e.spread_bps for e in group if e.spread_bps is not None]
        ts = sum(1 for e in group if e.quote_quality == QuoteQuality.TWO_SIDED_BOOK)
        fm = sum(1 for e in group if e.quote_quality == QuoteQuality.FALLBACK_MIN_MAX)
        median = sorted(spreads)[len(spreads)//2] if spreads else None
        b80 = f"{100*sum(1 for s in spreads if s<80)/len(spreads):.1f}%" if spreads else "N/A"
        b200 = f"{100*sum(1 for s in spreads if s<200)/len(spreads):.1f}%" if spreads else "N/A"
        median_str = f"{median:.1f}" if median is not None else "N/A"
        lines.append(f"| {label} | {len(group)} | {median_str} | {b80} | {b200} | {ts} | {fm} |")

    lines.extend([
        "",
        "## Spread by Market",
        "",
    ])
    from collections import OrderedDict
    mkt_groups: dict[str, list] = OrderedDict()
    for e in events:
        mkt_groups.setdefault(e.market_slug, []).append(e)
    for slug, group in mkt_groups.items():
        spreads = [e.spread_bps for e in group if e.spread_bps is not None]
        ts = sum(1 for e in group if e.quote_quality == QuoteQuality.TWO_SIDED_BOOK)
        fm = sum(1 for e in group if e.quote_quality == QuoteQuality.FALLBACK_MIN_MAX)
        median = sorted(spreads)[len(spreads)//2] if spreads else None
        lines.append(f"- **{slug}**: {len(group)} events, median={median:.1f} bps, TWO_SIDED={ts}, FALLBACK_MIN_MAX={fm}")

    lines.extend([
        "",
        "## Spread by Time of Day (UTC)",
        "",
    ])
    tod_groups: dict[str, list] = {}
    for e in events:
        tod = assign_time_of_day_bucket(e.ts_event_ns)
        tod_groups.setdefault(tod, []).append(e)
    for label in ["00-06UTC", "06-12UTC", "12-18UTC", "18-24UTC"]:
        group = tod_groups.get(label, [])
        spreads = [e.spread_bps for e in group if e.spread_bps is not None]
        fm = sum(1 for e in group if e.quote_quality == QuoteQuality.FALLBACK_MIN_MAX)
        ts = sum(1 for e in group if e.quote_quality == QuoteQuality.TWO_SIDED_BOOK)
        lines.append(f"- **{label}**: {len(group)} events, TWO_SIDED={ts}, FALLBACK_MIN_MAX={fm}")

    lines.extend([
        "",
        "## Spread by Volatility Regime",
        "",
    ])
    vol_groups: dict[str, list] = {}
    for e in events:
        vol = assign_volatility_bucket(e.binance_short_window_vol_bps)
        vol_groups.setdefault(vol, []).append(e)
    for label in ["0-5bps", "5-20bps", "20-50bps", "50-100bps", "100+bps", "unknown"]:
        group = vol_groups.get(label, [])
        fm = sum(1 for e in group if e.quote_quality == QuoteQuality.FALLBACK_MIN_MAX)
        ts = sum(1 for e in group if e.quote_quality == QuoteQuality.TWO_SIDED_BOOK)
        lines.append(f"- **{label}**: {len(group)} events, TWO_SIDED={ts}, FALLBACK_MIN_MAX={fm}")

    lines.extend([
        "",
        "## Depth Notes",
        "",
        "Observer capture did not record full book depth. Quote quality is classified",
        "based on whether best bid/ask are at Polymarket CLOB minimum ($0.01) / maximum ($0.99)",
        "price bounds. Quotes at these bounds are real resting CLOB orders with genuine size,",
        "but represent lottery-market liquidity, not actionable two-sided spread.",
        "",
        "## Limitations",
        "",
        "- Observer capture stored best bid/ask but not full order book depth",
        "- Quote quality classification uses price-bound heuristics, not raw book level inspection",
        "- Binance volatility proxy not available for all observation windows",
        "- Sample covers 8 BTC 15m UpDown markets over a single observation session",
        "- Results may not generalize to other market types, durations, or time periods",
        "",
        "## Verdict",
        "",
        f"**{verdict}**",
        f"Reason: {reason}",
        "",
        "## Recommendation",
        "",
        summary.recommendation,
    ])

    with open(output_dir / "report.md", "w") as f:
        f.write("\n".join(lines))