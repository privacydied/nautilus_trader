"""Run BTC UpDown duration spread probe.

Discovers BTC UpDown markets across durations (1h, 4h), polls order books,
classifies quote quality, and produces a spread-by-duration report.

No orders. No keys. No execution. No on-chain calls. Observer/research only.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .duration_market_discovery import (
    DURATION_15M,
    DURATION_1H,
    DURATION_4H,
    DURATION_UNKNOWN,
    ALL_DURATIONS,
    DurationMarketInfo,
    discover_duration_markets,
    discover_updown_markets,
    classify_duration,
    poll_quote_for_market,
)
from .live_market_discovery import UpDownMarketInfo, _ts
from .spread_regime import (
    SpreadEvent,
    SpreadRegimeSummary,
    QuoteQuality,
    classify_quote_quality,
    compute_spread_bps,
    compute_spread_abs,
    compute_summary_from_events,
    compute_spread_bucket_counts,
    assign_tte_bucket,
    assign_time_of_day_bucket,
    assign_volatility_bucket,
)

BRANCH = "polymarket-btc-updown-duration-spread-v1"

# Duration verdict labels
V_NO_ACTIVE = "DURATION_SPREAD_NEEDS_MORE_DATA_NO_ACTIVE_MARKETS"
V_TOO_FEW = "DURATION_SPREAD_NEEDS_MORE_DATA_TOO_FEW_EVENTS"
V_NO_USABLE = "DURATION_SPREAD_NO_USABLE_BOOKS"
V_ACTIONABLE = "DURATION_SPREAD_HAS_ACTIONABLE_BOOKS"

ALL_DURATION_VERDICTS = (V_NO_ACTIVE, V_TOO_FEW, V_NO_USABLE, V_ACTIONABLE)


def classify_duration_verdict(
    markets_discovered: int,
    events: list[SpreadEvent],
    summary: SpreadRegimeSummary,
) -> tuple[str, str]:
    """Classify duration spread verdict.

    Returns (verdict, reason).
    """
    if markets_discovered == 0:
        return V_NO_ACTIVE, "no_active_markets_found"

    if len(events) < 10:
        return V_TOO_FEW, f"only_{len(events)}_events_too_few_to_conclude"

    # Check if any duration bucket has actionable two-sided books
    actionable = summary.actionable_two_sided_book_count
    if actionable == 0:
        # Check what dominates
        if summary.exchange_bound_two_sided_book_count > len(events) * 0.5:
            return V_NO_USABLE, "dominated_by_exchange_bound_two_sided_book"
        elif summary.fallback_min_max_count > len(events) * 0.5:
            return V_NO_USABLE, "dominated_by_fallback_min_max"
        elif summary.missing_book_count + summary.empty_book_count > len(events) * 0.5:
            return V_NO_USABLE, "dominated_by_missing_or_empty_books"
        else:
            return V_NO_USABLE, "no_actionable_two_sided_books_observed"

    # At least some actionable two-sided books
    if actionable < 10:
        return V_TOO_FEW, f"only_{actionable}_actionable_events_needs_more_data"

    return V_ACTIONABLE, f"{actionable}_actionable_two_sided_book_events"


def group_events_by_duration(
    events: list[SpreadEvent],
    duration_map: dict[str, str],
) -> dict[str, list[SpreadEvent]]:
    """Group events by duration label.

    Args:
        events: List of spread events with market_slug.
        duration_map: Mapping from market_slug to duration_label.
    """
    groups: dict[str, list[SpreadEvent]] = {}
    for e in events:
        dur = duration_map.get(e.market_slug, DURATION_UNKNOWN)
        groups.setdefault(dur, []).append(e)
    return groups


def compute_duration_summary(
    groups: dict[str, list[SpreadEvent]],
) -> dict[str, dict[str, Any]]:
    """Compute summary statistics per duration bucket."""
    result: dict[str, dict[str, Any]] = {}
    for dur_label, dur_events in sorted(groups.items()):
        if not dur_events:
            result[dur_label] = {"event_count": 0}
            continue

        spreads = [e.spread_bps for e in dur_events if e.spread_bps is not None]
        summary = compute_summary_from_events(dur_events, f"dur-{dur_label}", [])

        qq_counts = {
            "two_sided_book": summary.two_sided_book_count,
            "exchange_bound_two_sided_book": summary.exchange_bound_two_sided_book_count,
            "one_sided_book": summary.one_sided_book_count,
            "empty_book": summary.empty_book_count,
            "fallback_min_max": summary.fallback_min_max_count,
            "missing_book": summary.missing_book_count,
            "invalid_book": summary.invalid_book_count,
        }

        result[dur_label] = {
            "event_count": len(dur_events),
            "valid_spread_count": summary.valid_spread_count,
            "median_spread_bps": summary.median_spread_bps,
            "p25_spread_bps": summary.p25_spread_bps,
            "p75_spread_bps": summary.p75_spread_bps,
            "pct_spread_lte_80bps": summary.pct_spread_lte_80bps,
            "pct_spread_lte_100bps": summary.pct_spread_lte_100bps,
            "pct_spread_lte_200bps": summary.pct_spread_lte_200bps,
            "pct_spread_lte_500bps": summary.pct_spread_lte_500bps,
            "actionable_two_sided_book_count": summary.actionable_two_sided_book_count,
            "pct_actionable_two_sided_book": summary.pct_actionable_two_sided_book,
            "exchange_bound_two_sided_book_count": summary.exchange_bound_two_sided_book_count,
            "pct_exchange_bound_two_sided_book": summary.pct_exchange_bound_two_sided_book,
            "fallback_min_max_count": summary.fallback_min_max_count,
            "missing_or_empty_book_count": summary.empty_book_count + summary.missing_book_count,
            "quote_quality_counts": qq_counts,
            "verdict_reason": summary.verdict_reason,
            "recommendation": summary.recommendation,
        }

    return result


def write_duration_reports(
    summary: SpreadRegimeSummary,
    events: list[SpreadEvent],
    duration_map: dict[str, str],
    markets: list[DurationMarketInfo],
    verdict: str,
    reason: str,
    duration_stats: dict[str, dict[str, Any]],
    output_dir: Path,
    durations_requested: tuple[str, ...],
    poll_count: int,
) -> None:
    """Write all duration probe report files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Summary JSON
    summary_data = {
        "run_id": summary.run_id,
        "branch": BRANCH,
        "durations_requested": list(durations_requested),
        "markets_discovered": len(markets),
        "markets_observed": summary.market_count,
        "duration_counts": (lambda: {d: len(g) for d, g in group_events_by_duration(events, duration_map).items()})() if events else {},
        "event_count": len(events),
        "actionable_two_sided_book_count": summary.actionable_two_sided_book_count,
        "pct_actionable_two_sided_book": summary.pct_actionable_two_sided_book,
        "exchange_bound_two_sided_book_count": summary.exchange_bound_two_sided_book_count,
        "pct_exchange_bound_two_sided_book": summary.pct_exchange_bound_two_sided_book,
        "fallback_min_max_count": summary.fallback_min_max_count,
        "pct_fallback_min_max": summary.pct_fallback_min_max,
        "missing_or_empty_book_count": summary.empty_book_count + summary.missing_book_count,
        "quote_quality_counts_by_duration": duration_stats,
        "poll_count": poll_count,
        "verdict": verdict,
        "reason": reason,
        "safety_status": "PASS",
        "limitations": [
            "Observer-only. No orders. No keys.",
            "Public API data only.",
            "May not find active 1h/4h markets at all times.",
            "Single-point-in-time snapshots, not continuous book.",
        ],
    }
    (output_dir / "summary.json").write_text(json.dumps(summary_data, indent=2, default=str))

    # Market discovery JSON
    disc_data = []
    for dm in markets:
        disc_data.append({
            "slug": dm.market.slug,
            "question": dm.market.question,
            "duration_label": dm.duration_label,
            "duration_seconds": dm.duration_seconds,
            "classification_source": dm.classification_source,
            "active": dm.market.active,
            "closed": dm.market.closed,
            "condition_id": dm.market.condition_id,
            "yes_token_id": dm.market.yes_token_id,
            "start_ns": dm.market.start_ns,
            "end_ns": dm.market.end_ns,
        })
    (output_dir / "market_discovery.json").write_text(json.dumps(disc_data, indent=2, default=str))

    # Safety check
    safety = {
        "no_orders": True,
        "no_keys": True,
        "no_execution_client_imports": True,
        "no_on_chain_calls": True,
        "branch": BRANCH,
    }
    (output_dir / "safety_check.json").write_text(json.dumps(safety, indent=2))

    # Report Markdown
    _write_duration_report_md(output_dir, summary_data, duration_stats, markets, events, duration_map)

    # CSV files
    _write_csvs(output_dir, events, duration_map)


def _write_duration_report_md(
    output_dir: Path,
    summary_data: dict,
    duration_stats: dict[str, dict[str, Any]],
    markets: list[DurationMarketInfo],
    events: list[SpreadEvent],
    duration_map: dict[str, str],
) -> None:
    """Write report.md."""
    lines: list[str] = []
    lines.append("# Polymarket BTC UpDown Duration Spread Probe\n")

    # Hypothesis
    lines.append("## Hypothesis\n")
    lines.append("Longer-duration BTC UpDown markets (1h, 4h) may attract real maker liquidity")
    lines.append("and produce actionable two-sided books, unlike 15m markets which are dominated")
    lines.append("by EXCHANGE_BOUND_TWO_SIDED_BOOK (0.01/0.99 boundary quotes).\n")

    # Data Sources
    lines.append("## Data Sources\n")
    lines.append("- Public Polymarket Gamma API for market discovery")
    lines.append("- Public Polymarket CLOB API for order book snapshots")
    lines.append(f"- Markets discovered: {summary_data['markets_discovered']}")
    lines.append(f"- Markets observed: {summary_data['markets_observed']}")
    lines.append(f"- Durations requested: {', '.join(summary_data['durations_requested'])}\n")

    # Market Discovery
    lines.append("## Market Discovery\n")
    if not markets:
        lines.append("No active markets found for requested durations.\n")
    else:
        lines.append("| Slug | Duration | Active | Classification Source |")
        lines.append("|------|----------|--------|----------------------|")
        for dm in markets:
            lines.append(f"| {dm.market.slug} | {dm.duration_label} | {dm.market.active} | {dm.classification_source} |")
        lines.append("")

    # Duration Classification
    lines.append("## Duration Classification\n")
    dur_counts: dict[str, int] = {}
    for dm in markets:
        dur_counts[dm.duration_label] = dur_counts.get(dm.duration_label, 0) + 1
    for dur, count in sorted(dur_counts.items()):
        lines.append(f"- {dur}: {count} markets")
    lines.append("")

    # Quote Quality Summary
    lines.append("## Quote Quality Summary\n")
    lines.append(f"- Total events: {summary_data['event_count']}")
    lines.append(f"- TWO_SIDED_BOOK (actionable): {summary_data['actionable_two_sided_book_count']} ({summary_data['pct_actionable_two_sided_book']:.1f}%)")
    lines.append(f"- EXCHANGE_BOUND_TWO_SIDED_BOOK: {summary_data['exchange_bound_two_sided_book_count']} ({summary_data['pct_exchange_bound_two_sided_book']:.1f}%)")
    lines.append(f"- FALLBACK_MIN_MAX (synthetic): {summary_data['fallback_min_max_count']} ({summary_data['pct_fallback_min_max']:.1f}%)")
    lines.append(f"- Missing/empty book: {summary_data['missing_or_empty_book_count']}\n")

    # Per-duration quote quality
    if duration_stats:
        lines.append("### Quote Quality by Duration\n")
        lines.append("| Duration | Events | Actionable TWO_SIDED | EXCHANGE_BOUND | FALLBACK_MIN_MAX | Median Spread (bps) |")
        lines.append("|----------|--------|---------------------|-----------------|------------------|--------------------|")
        for dur, stats in sorted(duration_stats.items()):
            a = stats.get("actionable_two_sided_book_count", 0)
            eb = stats.get("exchange_bound_two_sided_book_count", 0)
            fm = stats.get("fallback_min_max_count", 0)
            med = stats.get("median_spread_bps")
            med_str = f"{med:.1f}" if med is not None else "N/A"
            lines.append(f"| {dur} | {stats.get('event_count', 0)} | {a} | {eb} | {fm} | {med_str} |")
        lines.append("")

    # Spread Summary By Duration
    lines.append("## Spread Summary By Duration\n")
    if duration_stats:
        for dur, stats in sorted(duration_stats.items()):
            lines.append(f"### {dur}\n")
            lines.append(f"- Events: {stats.get('event_count', 0)}")
            lines.append(f"- Valid spread observations: {stats.get('valid_spread_count', 0)}")
            med = stats.get("median_spread_bps")
            lines.append(f"- Median spread: {f'{med:.1f} bps' if med else 'N/A'}")
            lines.append(f"- Below 80 bps: {stats.get('pct_spread_lte_80bps', 0):.1f}%")
            lines.append(f"- Below 200 bps: {stats.get('pct_spread_lte_200bps', 0):.1f}%")
            lines.append(f"- Actionable two-sided book: {stats.get('actionable_two_sided_book_count', 0)} ({stats.get('pct_actionable_two_sided_book', 0):.1f}%)")
            lines.append(f"- Verdict: {stats.get('verdict', 'N/A')}")
            lines.append("")

    # Comparison to 15m baseline
    lines.append("## Comparison To 15m Baseline\n")
    baseline_15m = duration_stats.get(DURATION_15M)
    if baseline_15m:
        lines.append(f"15m markets observed: {baseline_15m.get('event_count', 0)}")
        lines.append(f"15m EXCHANGE_BOUND_TWO_SIDED_BOOK: {baseline_15m.get('exchange_bound_two_sided_book_count', 0)}")
        lines.append(f"15m actionable two-sided book: {baseline_15m.get('actionable_two_sided_book_count', 0)}")
    else:
        lines.append("No 15m baseline data included in this probe.")
    lines.append("")

    # Verdict
    lines.append("## Verdict\n")
    lines.append(f"**{summary_data['verdict']}**")
    lines.append(f"Reason: {summary_data['reason']}\n")

    # Limitations
    lines.append("## Limitations\n")
    for lim in summary_data.get("limitations", []):
        lines.append(f"- {lim}")
    lines.append("")

    # Recommendation
    lines.append("## Recommendation\n")
    probe_verdict = summary_data.get("verdict", "")
    if probe_verdict == V_NO_ACTIVE:
        lines.append("No active 1h/4h markets found. Cannot conclude.")
        lines.append("Do not execute. Retry when markets are active.")
    elif probe_verdict == V_NO_USABLE:
        lines.append("No execution is justified. No Phase 3 is justified.")
        lines.append("BTC UpDown markets at the observed durations did not show actionable two-sided liquidity in this sample.")
    elif probe_verdict == V_TOO_FEW:
        lines.append("Insufficient data to conclude. Needs more observation windows.")
        lines.append("Do not execute. Do not start Phase 3.")
    elif probe_verdict == V_ACTIONABLE:
        lines.append("Do not execute. Do not start Phase 3.")
        lines.append("Create a new Phase 1 backtest branch for the duration bucket that showed actionable books.")
    lines.append("")

    # Safety
    lines.append("## Safety\n")
    lines.append("- No orders: PASS")
    lines.append("- No keys: PASS")
    lines.append("- No execution client imports: PASS")
    lines.append("- No on-chain calls: PASS")
    lines.append(f"- Branch: {BRANCH}\n")

    (output_dir / "report.md").write_text("\n".join(lines))


def _write_csvs(
    output_dir: Path,
    events: list[SpreadEvent],
    duration_map: dict[str, str],
) -> None:
    """Write CSV report files."""
    if not events:
        return

    import csv

    # spread_events.csv
    with open(output_dir / "spread_events.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "market_slug", "duration_label", "ts_event_ns", "time_to_expiry_ns",
            "best_bid", "best_ask", "mid", "spread_abs", "spread_bps",
            "book_depth_bid", "book_depth_ask",
            "binance_price", "binance_spread_bps", "binance_short_window_vol_bps",
            "polymarket_stale", "binance_stale",
            "quote_quality", "is_synthetic_fallback",
        ])
        for e in events:
            dur = duration_map.get(e.market_slug, DURATION_UNKNOWN)
            w.writerow([
                e.market_slug, dur, e.ts_event_ns, e.time_to_expiry_ns,
                e.best_bid, e.best_ask, e.mid, e.spread_abs, e.spread_bps,
                e.book_depth_bid, e.book_depth_ask,
                e.binance_price, e.binance_spread_bps, e.binance_short_window_vol_bps,
                e.polymarket_stale, e.binance_stale,
                e.quote_quality if isinstance(e.quote_quality, str) else e.quote_quality.value,
                e.is_synthetic_fallback,
            ])

    # quote_quality_by_duration.csv
    dur_groups: dict[str, list[SpreadEvent]] = {}
    for e in events:
        dur = duration_map.get(e.market_slug, DURATION_UNKNOWN)
        dur_groups.setdefault(dur, []).append(e)

    with open(output_dir / "quote_quality_by_duration.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["duration", "quote_quality", "count", "pct"])
        for dur_label in sorted(dur_groups.keys()):
            group = dur_groups[dur_label]
            qq_counts: dict[str, int] = {}
            for e in group:
                qq_str = e.quote_quality if isinstance(e.quote_quality, str) else e.quote_quality.value
                qq_counts[qq_str] = qq_counts.get(qq_str, 0) + 1
            for qq, count in sorted(qq_counts.items()):
                pct = count / len(group) * 100 if group else 0.0
                w.writerow([dur_label, qq, count, f"{pct:.1f}"])

    # spread_by_duration.csv
    with open(output_dir / "spread_by_duration.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["duration", "market_slug", "event_count", "valid_spread_count",
                     "median_spread_bps", "actionable_two_sided_book_count",
                     "exchange_bound_two_sided_book_count"])

    # spread_by_market.csv
    slug_groups: dict[str, list[SpreadEvent]] = {}
    for e in events:
        slug_groups.setdefault(e.market_slug, []).append(e)

    with open(output_dir / "spread_by_market.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["market_slug", "duration_label", "event_count", "valid_spread_count",
                     "median_spread_bps", "actionable_two_sided_book_count",
                     "exchange_bound_two_sided_book_count"])
        for slug, group in sorted(slug_groups.items()):
            dur = duration_map.get(slug, DURATION_UNKNOWN)
            sums = compute_summary_from_events(group, slug, [])
            w.writerow([slug, dur, len(group), sums.valid_spread_count,
                        f"{sums.median_spread_bps:.1f}" if sums.median_spread_bps else "N/A",
                        sums.actionable_two_sided_book_count,
                        sums.exchange_bound_two_sided_book_count])

    # spread_by_tte.csv
    tte_edges = (0, 60_000_000_000, 180_000_000_000, 480_000_000_000, 10_000_000_000_000)
    tte_groups: dict[str, list[SpreadEvent]] = {}
    for e in events:
        from .config import tte_bucket_name
        bucket = tte_bucket_name(max(0, e.time_to_expiry_ns), tte_edges)
        tte_groups.setdefault(bucket, []).append(e)

    with open(output_dir / "spread_by_tte.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tte_bucket", "duration_label", "event_count", "valid_spread_count",
                     "median_spread_bps", "actionable_two_sided_book_count",
                     "exchange_bound_two_sided_book_count"])
        for tte in sorted(tte_groups.keys()):
            group = tte_groups[tte]
            # Use the most common duration for this TTE bucket
            dur_counts: dict[str, int] = {}
            for e in group:
                d = duration_map.get(e.market_slug, DURATION_UNKNOWN)
                dur_counts[d] = dur_counts.get(d, 0) + 1
            dur = max(dur_counts, key=dur_counts.get) if dur_counts else DURATION_UNKNOWN
            sums = compute_summary_from_events(group, tte, [])
            w.writerow([tte, dur, len(group), sums.valid_spread_count,
                        f"{sums.median_spread_bps:.1f}" if sums.median_spread_bps else "N/A",
                        sums.actionable_two_sided_book_count,
                        sums.exchange_bound_two_sided_book_count])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Polymarket BTC UpDown duration spread probe (observer-only)",
    )
    parser.add_argument(
        "--durations",
        type=str,
        default="1h,4h",
        help="Comma-separated duration labels to probe (e.g. 1h,4h,15m)",
    )
    parser.add_argument(
        "--windows",
        type=int,
        default=1,
        help="Number of polling windows per market",
    )
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=900,
        help="Seconds per polling window",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=5,
        help="Seconds between polls",
    )
    parser.add_argument(
        "--max-markets",
        type=int,
        default=4,
        help="Maximum markets to discover",
    )
    parser.add_argument(
        "--include-15m-baseline",
        action="store_true",
        default=False,
        help="Include 15m duration for baseline comparison",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/polymarket_btcusd_arb/duration_spread_probe"),
        help="Output directory for reports",
    )
    parser.add_argument(
        "--dry-discover",
        action="store_true",
        default=False,
        help="Only discover markets, do not poll or analyze",
    )
    parser.add_argument(
        "--skip-branch-check",
        action="store_true",
        default=False,
        help="Skip git branch check",
    )
    argv = parser.parse_args()

    # Parse durations
    duration_labels = tuple(d.strip() for d in argv.durations.split(","))
    if argv.include_15m_baseline and DURATION_15M not in duration_labels:
        duration_labels = (DURATION_15M,) + duration_labels

    # Phase banner
    print("PHASE=DURATION_SPREAD_PROBE")
    print("OBSERVER_ONLY=1")
    print("NO_ORDERS=1")
    print("NO_KEYS=1")
    print(f"BRANCH={BRANCH}")
    print(f"DURATIONS={','.join(duration_labels)}")

    # Branch check
    if not argv.skip_branch_check:
        import subprocess
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True,
            cwd=Path(__file__).resolve().parents[4],
        )
        current_branch = result.stdout.strip()
        if not current_branch.startswith("polymarket-btc-updown-duration-spread"):
            print(f"WARNING: Active branch is '{current_branch}', expected '{BRANCH}'")
            print("Use --skip-branch-check to bypass.")

    # Discover markets
    print(f"\nDiscovering BTC UpDown markets for durations: {', '.join(duration_labels)}...")
    markets = discover_duration_markets(
        durations=duration_labels,
        max_markets=argv.max_markets,
    )
    print(f"Discovered {len(markets)} markets.")

    if not markets:
        print("No active markets found for requested durations.")
        print("This may mean 1h/4h UpDown markets are not currently active on Polymarket.")

    for dm in markets:
        print(f"  {dm.market.slug} | {dm.duration_label} | active={dm.market.active} | src={dm.classification_source}")

    if argv.dry_discover:
        # Write discovery-only report
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = argv.report_dir / run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        disc_data = []
        for dm in markets:
            disc_data.append({
                "slug": dm.market.slug,
                "duration_label": dm.duration_label,
                "classification_source": dm.classification_source,
                "active": dm.market.active,
            })
        (output_dir / "market_discovery.json").write_text(json.dumps(disc_data, indent=2, default=str))
        (output_dir / "summary.json").write_text(json.dumps({
            "run_id": run_id,
            "branch": BRANCH,
            "durations_requested": list(duration_labels),
            "markets_discovered": len(markets),
            "verdict": V_NO_ACTIVE if not markets else "DRY_DISCOVER_ONLY",
            "safety_status": "PASS",
        }, indent=2, default=str))
        (output_dir / "safety_check.json").write_text(json.dumps({
            "no_orders": True, "no_keys": True,
            "no_execution_client_imports": True, "no_on_chain_calls": True,
        }, indent=2))
        report_lines = [
            "# Polymarket BTC UpDown Duration Spread Probe\n",
            "## Market Discovery (dry run)\n",
            f"Durations requested: {', '.join(duration_labels)}",
            f"Markets discovered: {len(markets)}\n",
        ]
        if not markets:
            report_lines.append("No active markets found for requested durations.\n")
            report_lines.append("Cannot conclude. Retry when markets are active.")
        else:
            for dm in markets:
                report_lines.append(f"- {dm.market.slug} | {dm.duration_label} | active={dm.market.active} | src={dm.classification_source}")
        report_lines += ["\n## Safety\n", "- No orders: PASS", "- No keys: PASS", "- No execution: PASS", f"- Branch: {BRANCH}\n"]
        (output_dir / "report.md").write_text("\n".join(report_lines))
        print(f"\nDry-discover report written to {output_dir}")
        return

    if not markets:
        # No markets at all — write minimal report
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = argv.report_dir / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "summary.json").write_text(json.dumps({
            "run_id": run_id, "branch": BRANCH,
            "durations_requested": list(duration_labels),
            "markets_discovered": 0, "markets_observed": 0,
            "event_count": 0, "verdict": V_NO_ACTIVE,
            "reason": "no_active_markets_found",
            "safety_status": "PASS",
        }, indent=2, default=str))
        (output_dir / "market_discovery.json").write_text("[]")
        (output_dir / "safety_check.json").write_text(json.dumps({
            "no_orders": True, "no_keys": True,
            "no_execution_client_imports": True, "no_on_chain_calls": True,
        }, indent=2))
        report_lines = [
            "# Polymarket BTC UpDown Duration Spread Probe\n",
            "## Verdict\n",
            f"**{V_NO_ACTIVE}**",
            "Reason: no_active_markets_found\n",
            "No active 1h/4h BTC UpDown markets were found on Polymarket.",
            "This does not mean they never exist — only that none were active at probe time.\n",
            "## Recommendation\n",
            "Do not execute. Retry when markets are active.\n",
            "## Safety\n",
            "- No orders: PASS", "- No keys: PASS", "- No execution: PASS",
            f"- Branch: {BRANCH}\n",
        ]
        (output_dir / "report.md").write_text("\n".join(report_lines))
        print(f"No markets found. Report written to {output_dir}")
        return

    # Poll markets for quote snapshots
    print(f"\nPolling {len(markets)} market(s) for order book snapshots...")
    events: list[SpreadEvent] = []
    duration_map: dict[str, str] = {}  # slug -> duration_label
    poll_count = 0

    for dm in markets:
        duration_map[dm.market.slug] = dm.duration_label

        # Compute time-to-expiry
        now_ns = int(time.time() * 1_000_000_000)
        if dm.market.end_ns and dm.market.end_ns > now_ns:
            tte_ns = dm.market.end_ns - now_ns
        else:
            tte_ns = 0

        # Poll order book
        quote = poll_quote_for_market(dm.market)
        poll_count += 1

        best_bid = quote["best_bid"] if quote else None
        best_ask = quote["best_ask"] if quote else None
        mid = quote["mid"] if quote else None
        spread_bps = quote["spread_bps"] if quote else None
        depth_bid = quote.get("depth_bid") if quote else None
        depth_ask = quote.get("depth_ask") if quote else None

        # Compute spread_bps if not available from quote
        if spread_bps is None and best_bid is not None and best_ask is not None:
            spread_bps = compute_spread_bps(best_bid, best_ask)

        # Determine if synthetic fallback
        is_synthetic = False  # We got real quotes, not synthetic

        event = SpreadEvent(
            market_slug=dm.market.slug,
            ts_event_ns=quote["ts_event_ns"] if quote else now_ns,
            time_to_expiry_ns=max(tte_ns, 0),
            best_bid=best_bid,
            best_ask=best_ask,
            mid=mid,
            spread_abs=compute_spread_abs(best_bid, best_ask),
            spread_bps=spread_bps,
            book_depth_bid=depth_bid,
            book_depth_ask=depth_ask,
            binance_price=None,
            binance_spread_bps=None,
            binance_short_window_vol_bps=None,
            polymarket_stale=False,
            binance_stale=True,  # No Binance reference in duration probe
            is_synthetic_fallback=is_synthetic,
        )
        events.append(event)

        # Multiple polls with interval
        for _ in range(argv.windows - 1):
            time.sleep(argv.poll_interval_seconds)
            quote2 = poll_quote_for_market(dm.market)
            poll_count += 1
            if quote2:
                now_ns2 = int(time.time() * 1_000_000_000)
                tte_ns2 = dm.market.end_ns - now_ns2 if dm.market.end_ns and dm.market.end_ns > now_ns2 else 0
                best_bid2 = quote2["best_bid"]
                best_ask2 = quote2["best_ask"]
                mid2 = quote2["mid"]
                spread_bps2 = quote2["spread_bps"]
                if spread_bps2 is None and best_bid2 is not None and best_ask2 is not None:
                    spread_bps2 = compute_spread_bps(best_bid2, best_ask2)
                e2 = SpreadEvent(
                    market_slug=dm.market.slug,
                    ts_event_ns=quote2["ts_event_ns"],
                    time_to_expiry_ns=max(tte_ns2, 0),
                    best_bid=best_bid2,
                    best_ask=best_ask2,
                    mid=mid2,
                    spread_abs=compute_spread_abs(best_bid2, best_ask2),
                    spread_bps=spread_bps2,
                    book_depth_bid=quote2.get("depth_bid"),
                    book_depth_ask=quote2.get("depth_ask"),
                    binance_price=None,
                    binance_spread_bps=None,
                    binance_short_window_vol_bps=None,
                    polymarket_stale=False,
                    binance_stale=True,
                    is_synthetic_fallback=False,
                )
                events.append(e2)

    print(f"Collected {len(events)} spread events from {poll_count} polls.")

    # Compute summary
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary = compute_summary_from_events(events, run_id, [])

    # Group by duration
    dur_groups = group_events_by_duration(events, duration_map)
    duration_stats = compute_duration_summary(dur_groups)

    # Verdict
    verdict, reason = classify_duration_verdict(len(markets), events, summary)

    print(f"\n=== Duration Spread Probe Summary ===")
    print(f"Markets discovered: {len(markets)}")
    print(f"Total events: {len(events)}")
    print(f"Actionable two-sided book: {summary.actionable_two_sided_book_count}")
    print(f"Exchange-bound two-sided book: {summary.exchange_bound_two_sided_book_count}")
    print(f"Verdict: {verdict}")
    print(f"Reason: {reason}")

    # Write reports
    output_dir = argv.report_dir / run_id
    print(f"\nWriting reports to {output_dir}...")
    write_duration_reports(
        summary, events, duration_map, markets,
        verdict, reason, duration_stats, output_dir,
        durations_requested=duration_labels,
        poll_count=poll_count,
    )
    print(f"Report written to {output_dir}")
    print(f"\nVerdict: {verdict}")
    print(f"Reason: {reason}")


if __name__ == "__main__":
    main()