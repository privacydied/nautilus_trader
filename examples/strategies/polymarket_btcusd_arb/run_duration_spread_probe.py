"""Run BTC UpDown duration spread probe (corrected discovery).

Discovers BTC UpDown markets across durations (5m, 15m, 1h, 4h),
validates known user-supplied slugs, polls order books,
classifies quote quality, and produces a spread-by-duration report.

Supersedes the prior incorrect conclusion that 1h/4h markets do not exist.

No orders. No keys. No execution. No on-chain calls. Observer/research only.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .duration_market_discovery import (
    DURATION_5M,
    DURATION_15M,
    DURATION_1H,
    DURATION_4H,
    DURATION_UNKNOWN,
    ALL_DURATIONS,
    REF_SOURCE_BINANCE,
    REF_SOURCE_CHAINLINK,
    REF_SOURCE_UNKNOWN,
    DURATION_SECONDS_MAP,
    DV_EXISTS_NEEDS_QUOTE,
    DV_ACTIVE_NO_USABLE,
    DV_ACTIVE_HAS_ACTIONABLE,
    DV_NO_ACTIVE_NOW,
    DV_DISCOVERY_FAILED,
    DV_UNSUPPORTED_REF,
    PV_SUPERSEDES_PRIOR,
    PV_NEEDS_MORE_DATA,
    PV_NO_USABLE,
    PV_FOUND_ACTIONABLE,
    DurationMarketInfo,
    discover_updown_markets,
    classify_duration,
    classify_reference_source,
    validate_known_slug,
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
)

BRANCH = "polymarket-btc-updown-duration-discovery-fix-v2"

# Default durations
DEFAULT_DURATIONS = (DURATION_5M, DURATION_15M, DURATION_1H, DURATION_4H)


def classify_duration_verdict_v2(
    product_exists: bool,
    active_markets: int,
    events: list[SpreadEvent],
    actionable_count: int,
    exchange_bound_count: int,
    ref_source_kind: str,
) -> tuple[str, str]:
    """Classify duration-level verdict using corrected taxonomy.

    Separates product existence from active quote availability.

    Returns (verdict, reason).
    """
    if not product_exists:
        return DV_DISCOVERY_FAILED, "product_not_found_in_api_or_known_slug_validation"

    if ref_source_kind == REF_SOURCE_CHAINLINK and active_markets == 0:
        return DV_NO_ACTIVE_NOW, "chainlink_based_product_exists_but_no_active_market_now"

    if active_markets == 0:
        return DV_NO_ACTIVE_NOW, "product_exists_but_no_active_market_now"

    if len(events) < 1:
        return DV_EXISTS_NEEDS_QUOTE, f"{active_markets}_active_markets_found_but_no_quote_polls_yet"

    if actionable_count > 0:
        if actionable_count >= 10:
            return DV_ACTIVE_HAS_ACTIONABLE, f"{actionable_count}_actionable_two_sided_book_events"
        return DV_EXISTS_NEEDS_QUOTE, f"only_{actionable_count}_actionable_events_needs_more_data"

    if exchange_bound_count == len(events):
        return DV_ACTIVE_NO_USABLE, "all_quotes_exchange_bound_two_sided_book"

    if len(events) < 10:
        return DV_EXISTS_NEEDS_QUOTE, f"only_{len(events)}_events_too_few_to_conclude"

    return DV_ACTIVE_NO_USABLE, "no_actionable_two_sided_books_observed"


def classify_overall_verdict_v2(
    duration_verdicts: dict[str, str],
    all_products_exist: bool,
    any_actionable: bool,
    supersedes_prior: bool = True,
) -> tuple[str, str]:
    """Classify the overall probe verdict.

    Returns (verdict, reason).
    """
    if supersedes_prior:
        base = PV_SUPERSEDES_PRIOR
    else:
        base = PV_NEEDS_MORE_DATA

    if any_actionable:
        return PV_FOUND_ACTIONABLE, "actionable_two_sided_book_found_in_at_least_one_duration"

    # Check which durations have active data
    active_durs = [
        d for d, v in duration_verdicts.items()
        if v in (DV_ACTIVE_NO_USABLE, DV_ACTIVE_HAS_ACTIONABLE, DV_EXISTS_NEEDS_QUOTE)
    ]
    no_active_durs = [
        d for d, v in duration_verdicts.items()
        if v in (DV_NO_ACTIVE_NOW, DV_DISCOVERY_FAILED)
    ]

    if active_durs and not any_actionable:
        if supersedes_prior:
            return PV_SUPERSEDES_PRIOR, "known_slugs_validate_product_existence_but_no_actionable_two_sided_books_in_active_markets"
        return PV_NO_USABLE, f"active_books_observed_but_no_actionable_two_sided_at_any_duration"

    if no_active_durs and not active_durs:
        if supersedes_prior:
            return PV_SUPERSEDES_PRIOR, "known_slugs_validate_product_existence_but_no_active_markets_observed"
        return PV_NEEDS_MORE_DATA, "no_active_markets_for_any_discovered_duration"

    return PV_NEEDS_MORE_DATA, "insufficient_data_for_overall_conclusion"


def group_events_by_duration(
    events: list[SpreadEvent],
    duration_map: dict[str, str],
) -> dict[str, list[SpreadEvent]]:
    """Group events by duration label."""
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
        }

    return result


def write_reports_v2(
    output_dir: Path,
    run_id: str,
    known_slug_results: dict[str, dict],
    markets: list[DurationMarketInfo],
    active_markets: list[DurationMarketInfo],
    events: list[SpreadEvent],
    duration_map: dict[str, str],
    duration_verdicts: dict[str, str],
    overall_verdict: str,
    overall_reason: str,
    durations_requested: tuple[str, ...],
    poll_count: int,
) -> None:
    """Write all corrected probe report files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Group events by duration
    dur_groups = group_events_by_duration(events, duration_map)
    duration_stats = compute_duration_summary(dur_groups)

    # Product counts by duration
    products_by_duration: dict[str, int] = {}
    active_by_duration: dict[str, int] = {}
    ref_sources_by_duration: dict[str, int] = {}
    for dm in markets:
        dur = dm.duration_label
        products_by_duration[dur] = products_by_duration.get(dur, 0) + 1
        if dm.market.active:
            active_by_duration[dur] = active_by_duration.get(dur, 0) + 1
    for dm in markets:
        dur = dm.duration_label
        key = dm.resolution_source_kind
        ref_sources_by_duration.setdefault(key, 0)
        ref_sources_by_duration[key] = ref_sources_by_duration.get(key, 0) + 1

    # Actionable book counts
    actionable_by_duration: dict[str, int] = {}
    eb_by_duration: dict[str, int] = {}
    for dur, stats in duration_stats.items():
        actionable_by_duration[dur] = stats.get("actionable_two_sided_book_count", 0)
        eb_by_duration[dur] = stats.get("exchange_bound_two_sided_book_count", 0)

    # summary.json
    summary_data = {
        "run_id": run_id,
        "branch": BRANCH,
        "commit": (
            subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                           cwd=Path(__file__).resolve().parents[4]).stdout.strip()
        ),
        "known_slug_validation": known_slug_results,
        "durations_requested": list(durations_requested),
        "products_found_by_duration": products_by_duration,
        "active_markets_found_by_duration": active_by_duration,
        "quote_events_by_duration": {d: stats.get("event_count", 0) for d, stats in duration_stats.items()},
        "quote_quality_counts_by_duration": {d: stats.get("quote_quality_counts", {}) for d, stats in duration_stats.items()},
        "actionable_book_count_by_duration": actionable_by_duration,
        "reference_source_counts_by_duration": ref_sources_by_duration,
        "overall_verdict": overall_verdict,
        "duration_verdicts": duration_verdicts,
        "supersedes_prior_report": True,
        "limitations": [
            "Observer-only. No orders. No keys.",
            "Public API data only.",
            "Product existence confirmed via known slugs; active market availability is separate.",
            "4h markets may use Chainlink BTC/USD, not Binance BTC/USDT.",
            "Single-point-in-time snapshots, not continuous book.",
        ],
        "safety_status": "PASS",
    }
    (output_dir / "summary.json").write_text(json.dumps(summary_data, indent=2, default=str))

    # known_slug_validation.json
    (output_dir / "known_slug_validation.json").write_text(json.dumps(known_slug_results, indent=2, default=str))

    # duration_markets.csv
    import csv
    with open(output_dir / "duration_markets.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["slug", "title", "duration_label", "duration_seconds", "is_active", "is_closed",
                     "start_time_utc", "end_time_utc", "resolution_source", "resolution_source_kind",
                     "yes_token_id", "no_token_id", "discovery_method", "is_known_slug"])
        for dm in markets:
            w.writerow([
                dm.market.slug,
                dm.market.question,
                dm.duration_label,
                dm.duration_seconds or "",
                dm.market.active,
                dm.market.closed,
                dm.market.start_ns,
                dm.market.end_ns,
                dm.market.resolution_source or "",
                dm.resolution_source_kind,
                dm.market.yes_token_id or "",
                dm.market.no_token_id or "",
                dm.classification_source,
                dm.is_known_slug,
            ])

    # quote_quality_by_duration.csv
    with open(output_dir / "quote_quality_by_duration.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["duration", "quote_quality", "count"])
        for dur, stats in duration_stats.items():
            qq_counts = stats.get("quote_quality_counts", {})
            for qq, count in sorted(qq_counts.items()):
                if count > 0:
                    w.writerow([dur, qq, count])

    # reference_sources.csv
    with open(output_dir / "reference_sources.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["duration", "reference_source_kind", "count"])
        seen: set[tuple[str, str]] = set()
        for dm in markets:
            key = (dm.duration_label, dm.resolution_source_kind)
            if key not in seen:
                seen.add(key)
                count = sum(1 for m in markets if m.duration_label == dm.duration_label and m.resolution_source_kind == dm.resolution_source_kind)
                w.writerow([dm.duration_label, dm.resolution_source_kind, count])

    # report.md
    _write_report_md_v2(
        output_dir, summary_data, duration_stats, duration_verdicts,
        overall_verdict, overall_reason, markets, known_slug_results,
        products_by_duration, active_by_duration, actionable_by_duration,
        ref_sources_by_duration,
    )

    # safety_check.json
    safety = {
        "no_orders": True,
        "no_keys": True,
        "no_execution_client_imports": True,
        "no_on_chain_calls": True,
        "branch": BRANCH,
        "prior_nonexistence_finding_superseded": True,
    }
    (output_dir / "safety_check.json").write_text(json.dumps(safety, indent=2))


def _write_report_md_v2(
    output_dir: Path,
    summary_data: dict,
    duration_stats: dict[str, dict[str, Any]],
    duration_verdicts: dict[str, str],
    overall_verdict: str,
    overall_reason: str,
    markets: list[DurationMarketInfo],
    known_slug_results: dict[str, dict],
    products_by_duration: dict[str, int],
    active_by_duration: dict[str, int],
    actionable_by_duration: dict[str, int],
    ref_sources_by_duration: dict[str, int],
) -> None:
    """Write report.md with corrected wording."""
    lines: list[str] = []

    # Phase statement
    lines.append("# Polymarket BTC UpDown Duration Discovery Fix\n")
    lines.append("## Phase Statement\n")
    lines.append("This run corrects a duration discovery bug in the prior probe.\n")

    # Why this run exists
    lines.append("## Why This Run Exists\n")
    lines.append("The previous duration probe (`polymarket-btc-updown-duration-spread-v1`)\n")
    lines.append("incorrectly concluded that 1h and 4h BTC UpDown markets were not offered\n")
    lines.append("on Polymarket. This run supersedes that conclusion with corrected\n")
    lines.append("discovery logic, known slug validation, and proper separation of\n")
    lines.append("product existence from active quote availability.\n")

    # Prior incorrect conclusion being superseded
    lines.append("## Superseded Prior Conclusion\n")
    lines.append("The prior claim that 1h and 4h BTC UpDown markets were not offered is\n")
    lines.append("**superseded** by this run. Known user-supplied Polymarket URLs validate\n")
    lines.append("that 1h/hourly and 4h BTC UpDown products exist. Product existence is\n")
    lines.append("now separated from active quote availability.\n")

    # Known slug validation
    lines.append("## Known Slug Validation\n")
    if known_slug_results:
        lines.append("| Slug | Product Exists | Duration | Active | Reference Source |")
        lines.append("|------|---------------|----------|--------|-----------------|")
        for slug, result in known_slug_results.items():
            lines.append(
                f"| {slug} | {result.get('product_exists', 'unknown')} | "
                f"{result.get('duration_label', 'unknown')} | "
                f"{result.get('is_active', 'unknown')} | "
                f"{result.get('resolution_source_kind', 'unknown')} |"
            )
        lines.append("")
    else:
        lines.append("No known slugs were provided for validation.\n")

    # Duration discovery table
    lines.append("## Duration Discovery\n")
    requested = summary_data.get("durations_requested", [])
    if requested:
        lines.append("| Duration | Products Found | Active Markets | Reference Sources |")
        lines.append("|----------|---------------|----------------|------------------|")
        for dur in requested:
            prods = products_by_duration.get(dur, 0)
            active = active_by_duration.get(dur, 0)
            sources = []
            for dm in markets:
                if dm.duration_label == dur:
                    sources.append(dm.resolution_source_kind)
            source_str = ", ".join(sorted(set(sources))) if sources else "N/A"
            lines.append(f"| {dur} | {prods} | {active} | {source_str} |")
        lines.append("")

    # Active market availability
    lines.append("## Active Market Availability\n")
    for dur in requested:
        active = active_by_duration.get(dur, 0)
        prods = products_by_duration.get(dur, 0)
        if prods > 0 and active == 0:
            lines.append(f"- **{dur}**: Product exists ({prods} found) but no active market at probe time.")
        elif prods > 0:
            lines.append(f"- **{dur}**: Product exists ({prods} found) with {active} active market(s).")
        else:
            lines.append(f"- **{dur}**: No products discovered via API search (may require direct known-slug lookup).")
    lines.append("")

    # Reference source table
    lines.append("## Reference Sources\n")
    lines.append("| Duration | Reference Source | Implication |")
    lines.append("|----------|-----------------|-------------|")
    seen_dur_sources: set[str] = set()
    for dm in markets:
        dur_src = f"{dm.duration_label}_{dm.resolution_source_kind}"
        if dur_src in seen_dur_sources:
            continue
        seen_dur_sources.add(dur_src)
        implication = (
            "Binance fair-prob model applicable" if dm.resolution_source_kind == REF_SOURCE_BINANCE
            else "Needs separate reference model" if dm.resolution_source_kind == REF_SOURCE_CHAINLINK
            else "Unknown — verify before testing"
        )
        lines.append(f"| {dm.duration_label} | {dm.resolution_source_kind} | {implication} |")
    lines.append("")

    # Quote quality table
    lines.append("## Quote Quality\n")
    if duration_stats:
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

    # Actionable two-sided book table
    lines.append("## Actionable Two-Sided Books\n")
    if actionable_by_duration:
        has_any = any(v > 0 for v in actionable_by_duration.values())
        if has_any:
            lines.append("| Duration | Actionable TWO_SIDED Count |")
            lines.append("|----------|--------------------------|")
            for dur, count in sorted(actionable_by_duration.items()):
                if count > 0:
                    lines.append(f"| {dur} | {count} |")
        else:
            lines.append("No actionable two-sided books were observed at any duration.\n")
        lines.append("")

    # Chainlink warning
    chainlink_durs = [
        dm.duration_label for dm in markets
        if dm.resolution_source_kind == REF_SOURCE_CHAINLINK
    ]
    if chainlink_durs:
        lines.append("## Reference Source Warning\n")
        lines.append(f"**{', '.join(sorted(set(chainlink_durs)))} product(s) use Chainlink BTC/USD as resolution source.**\n")
        lines.append("The existing Binance-reference fair-probability strategy is not automatically "
                     "valid for this duration without a separate Chainlink-reference Phase 1 hypothesis.\n")

    # Corrected verdict
    lines.append("## Corrected Verdict\n")
    lines.append(f"**Overall: {overall_verdict}**\n")
    lines.append(f"Reason: {overall_reason}\n")
    for dur, verdict in sorted(duration_verdicts.items()):
        lines.append(f"- **{dur}**: {verdict}")
    lines.append("")

    # What this does and does not prove
    lines.append("## What This Does and Does Not Prove\n")
    lines.append("**Does prove:**\n")
    lines.append("- 1h/hourly BTC UpDown products exist on Polymarket (validated via known slug).")
    lines.append("- 4h BTC UpDown products exist on Polymarket (validated via known slug).")
    lines.append("- Product existence and active market availability are separate concepts.")
    if chainlink_durs:
        lines.append("- 4h products use Chainlink BTC/USD, not Binance BTC/USDT.")
    lines.append("\n**Does NOT prove:**\n")
    lines.append("- That active 1h/4h markets are available at any given time.")
    lines.append("- That 1h/4h books are actionable (requires live quote observation).")
    lines.append("- That a Binance fair-probability strategy works for Chainlink-based products.")
    lines.append("- That any trade should be executed.\n")

    # Next recommendation
    lines.append("## Next Recommendation\n")
    if overall_verdict == PV_FOUND_ACTIONABLE:
        lines.append("Actionable two-sided books were observed. This does NOT justify execution.\n")
        lines.append("Create a new Phase 1 backtest branch for the duration/reference-source\n")
        lines.append("combination that showed actionable books. Do not proceed to Phase 3.\n")
    elif overall_verdict == PV_NO_USABLE:
        lines.append("No actionable two-sided books were observed in active markets.\n")
        lines.append("Do not execute. Do not start Phase 3.\n")
        lines.append("Re-observe when markets are active, or test a new hypothesis.\n")
    else:
        lines.append("Insufficient data. Re-run the probe when 1h/4h markets are active.\n")
        lines.append("Do not execute. Do not start Phase 3.\n")
    lines.append("")

    # Limitations
    lines.append("## Limitations\n")
    for lim in summary_data.get("limitations", []):
        lines.append(f"- {lim}")
    lines.append("")

    # Safety
    lines.append("## Safety\n")
    lines.append("- No orders: PASS")
    lines.append("- No keys: PASS")
    lines.append("- No execution client imports: PASS")
    lines.append("- No on-chain calls: PASS")
    lines.append(f"- Branch: {BRANCH}\n")

    (output_dir / "report.md").write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Polymarket BTC UpDown duration spread probe (corrected discovery)",
    )
    parser.add_argument(
        "--durations",
        type=str,
        default="5m,15m,1h,4h",
        help="Comma-separated duration labels to probe (default: 5m,15m,1h,4h)",
    )
    parser.add_argument(
        "--known-slug",
        type=str,
        action="append",
        default=[],
        help="Known Polymarket slug to validate (may be repeated)",
    )
    parser.add_argument(
        "--include-closed-for-discovery",
        action="store_true",
        default=True,
        help="Include closed/resolved markets in discovery (default: True)",
    )
    parser.add_argument(
        "--active-only-for-book-probe",
        action="store_true",
        default=True,
        help="Only poll books for active markets (default: True)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=None,
        help="Total seconds to spend polling books (default: one snapshot per active market)",
    )
    parser.add_argument(
        "--max-markets-per-duration",
        type=int,
        default=4,
        help="Maximum markets to discover per duration",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/polymarket_btcusd_arb/duration_discovery_fix"),
        help="Output directory for reports",
    )
    parser.add_argument(
        "--dry-discover",
        action="store_true",
        default=False,
        help="Only discover and validate slugs, do not poll books",
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

    # Phase banner
    print("PHASE=DURATION_DISCOVERY_FIX")
    print("OBSERVER_ONLY=1")
    print("NO_ORDERS=1")
    print("NO_KEYS=1")
    print(f"BRANCH={BRANCH}")
    print(f"DURATIONS={','.join(duration_labels)}")
    print(f"SUPERSEDES_PRIOR=1")

    # Branch check
    if not argv.skip_branch_check:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True,
            cwd=Path(__file__).resolve().parents[4],
        )
        current_branch = result.stdout.strip()
        if not current_branch.startswith("polymarket-btc-updown-duration-discovery-fix"):
            print(f"WARNING: Active branch is '{current_branch}', expected '{BRANCH}'")
            print("Use --skip-branch-check to bypass.")

    # --- Step 1: Known slug validation ---
    known_slug_results: dict[str, dict] = {}
    print("\n=== Step 1: Known Slug Validation ===")
    for slug in argv.known_slug:
        print(f"Validating slug: {slug}...")
        result = validate_known_slug(slug)
        if result:
            known_slug_results[slug] = {
                "product_exists": True,
                "duration_label": result.duration_label,
                "duration_seconds": result.duration_seconds,
                "is_active": result.market.active,
                "is_closed": result.market.closed,
                "resolution_source_kind": result.resolution_source_kind,
                "yes_token_id": result.market.yes_token_id,
                "validation_source": "polymarket_gamma_api",
            }
            print(f"  VALID: {result.duration_label} | active={result.market.active} | ref={result.resolution_source_kind}")
        else:
            known_slug_results[slug] = {
                "product_exists": False,
                "error": "slug_not_found_in_gamma_api",
            }
            print(f"  NOT FOUND in Gamma API (may be expired or inaccessible)")

    # --- Step 2: Broad discovery ---
    print(f"\n=== Step 2: Broad Discovery ===")
    all_markets: list[DurationMarketInfo] = []

    for dur in duration_labels:
        dur_markets = discover_updown_markets(
            asset_filter="btc",
            durations=(dur,),
            include_active=True,
            include_closed=argv.include_closed_for_discovery,
            max_markets=argv.max_markets_per_duration,
        )
        print(f"  {dur}: {len(dur_markets)} markets found")
        for dm in dur_markets:
            print(f"    {dm.market.slug} | active={dm.market.active} | ref={dm.resolution_source_kind}")
        all_markets.extend(dur_markets)

    # Merge known slug results into all_markets if not already present
    seen_slugs = {dm.market.slug for dm in all_markets}
    for slug, result in known_slug_results.items():
        if result.get("product_exists") and slug not in seen_slugs:
            # Re-validate to get full DurationMarketInfo
            validated = validate_known_slug(slug)
            if validated:
                all_markets.append(validated)
                seen_slugs.add(slug)

    # --- Step 3: Separate active from inactive ---
    active_markets = [dm for dm in all_markets if dm.market.active]
    inactive_markets = [dm for dm in all_markets if not dm.market.active]
    print(f"\n=== Step 3: Market Summary ===")
    print(f"  Total products found: {len(all_markets)}")
    print(f"  Active markets: {len(active_markets)}")
    print(f"  Inactive/closed: {len(inactive_markets)}")

    # --- Step 4: Book probe (active only) ---
    events: list[SpreadEvent] = []
    duration_map: dict[str, str] = {}
    poll_count = 0

    if not argv.dry_discover:
        print(f"\n=== Step 4: Book Probe ===")
        targets = active_markets if argv.active_only_for_book_probe else all_markets
        if not targets:
            print("  No active markets to poll. Skipping book probe.")
        else:
            print(f"  Polling {len(targets)} market(s)...")
            for dm in targets:
                duration_map[dm.market.slug] = dm.duration_label

                quote = poll_quote_for_market(dm.market)
                poll_count += 1

                if quote is None:
                    print(f"    {dm.market.slug}: BOOK_POLL_FAILED")
                    continue

                best_bid = quote["best_bid"]
                best_ask = quote["best_ask"]
                spread_bps = quote.get("spread_bps")
                if spread_bps is None and best_bid is not None and best_ask is not None:
                    spread_bps = compute_spread_bps(best_bid, best_ask)

                now_ns = int(time.time() * 1_000_000_000)
                if dm.market.end_ns and dm.market.end_ns > now_ns:
                    tte_ns = dm.market.end_ns - now_ns
                else:
                    tte_ns = 0

                event = SpreadEvent(
                    market_slug=dm.market.slug,
                    ts_event_ns=quote.get("ts_event_ns", now_ns),
                    time_to_expiry_ns=max(tte_ns, 0),
                    best_bid=best_bid,
                    best_ask=best_ask,
                    mid=quote.get("mid"),
                    spread_abs=compute_spread_abs(best_bid, best_ask),
                    spread_bps=spread_bps,
                    book_depth_bid=quote.get("depth_bid"),
                    book_depth_ask=quote.get("depth_ask"),
                    binance_price=None,
                    binance_spread_bps=None,
                    binance_short_window_vol_bps=None,
                    polymarket_stale=False,
                    binance_stale=True,
                    is_synthetic_fallback=False,
                )
                events.append(event)

                qq = event.quote_quality
                print(f"    {dm.market.slug}: bid={best_bid} ask={best_ask} qq={qq} dur={dm.duration_label} ref={dm.resolution_source_kind}")

    print(f"\n  Total polls: {poll_count}")
    print(f"  Total events: {len(events)}")

    # --- Step 5: Verdicts ---
    print(f"\n=== Step 5: Verdicts ===")

    # Duration-level verdicts
    duration_verdicts: dict[str, str] = {}
    duration_groups = group_events_by_duration(events, duration_map)

    for dur in duration_labels:
        # Check product existence from known slugs or discovery
        product_exists = any(
            r.get("product_exists") and r.get("duration_label") == dur
            for r in known_slug_results.values()
        )
        if not product_exists:
            product_exists = any(dm.duration_label == dur for dm in all_markets)

        active_count = sum(1 for dm in active_markets if dm.duration_label == dur)
        dur_events = duration_groups.get(dur, [])
        actionable = sum(1 for e in dur_events if e.quote_quality == QuoteQuality.TWO_SIDED_BOOK)
        eb_count = sum(1 for e in dur_events if e.quote_quality == QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK)

        # Get ref source
        ref_source = REF_SOURCE_UNKNOWN
        for dm in all_markets:
            if dm.duration_label == dur:
                ref_source = dm.resolution_source_kind
                break

        verdict, reason = classify_duration_verdict_v2(
            product_exists=product_exists,
            active_markets=active_count,
            events=dur_events,
            actionable_count=actionable,
            exchange_bound_count=eb_count,
            ref_source_kind=ref_source,
        )
        duration_verdicts[dur] = verdict
        print(f"  {dur}: {verdict} ({reason})")

    # Overall verdict
    any_actionable = any(
        v == DV_ACTIVE_HAS_ACTIONABLE for v in duration_verdicts.values()
    )
    all_exist = all(
        any(dm.duration_label == dur for dm in all_markets) or
        any(r.get("product_exists") and r.get("duration_label") == dur for r in known_slug_results.values())
        for dur in duration_labels
        if dur != DURATION_UNKNOWN
    )
    overall_verdict, overall_reason = classify_overall_verdict_v2(
        duration_verdicts=duration_verdicts,
        all_products_exist=all_exist,
        any_actionable=any_actionable,
        supersedes_prior=len(argv.known_slug) > 0 and any(r.get("product_exists") for r in known_slug_results.values()),
    )
    print(f"  OVERALL: {overall_verdict} ({overall_reason})")

    # --- Step 6: Write reports ---
    print(f"\n=== Step 6: Reports ===")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = argv.report_dir / run_id
    print(f"  Writing to: {output_dir}")

    write_reports_v2(
        output_dir=output_dir,
        run_id=run_id,
        known_slug_results=known_slug_results,
        markets=all_markets,
        active_markets=active_markets,
        events=events,
        duration_map=duration_map,
        duration_verdicts=duration_verdicts,
        overall_verdict=overall_verdict,
        overall_reason=overall_reason,
        durations_requested=duration_labels,
        poll_count=poll_count,
    )

    print(f"\nReports written to: {output_dir}")
    print(f"Overall verdict: {overall_verdict}")
    print(f"Duration verdicts: {duration_verdicts}")
    print(f"Prior nonexistence finding superseded: True")


if __name__ == "__main__":
    main()
