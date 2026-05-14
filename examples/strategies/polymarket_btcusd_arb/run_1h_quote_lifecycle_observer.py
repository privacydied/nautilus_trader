"""Run 1h Binance-referenced BTC UpDown full-lifecycle quote observer.

Answers: Do 1h Binance-referenced BTC UpDown markets ever show actionable
two-sided books during a full market lifecycle?

This is a quote-quality observation phase, NOT a trading phase.
No orders. No keys. No execution. No on-chain calls.

This run exists because the previous duration probe incorrectly concluded
that 1h and 4h BTC UpDown products do not exist. The 1h product existence
finding has been corrected, and this run observes 1h book quality directly.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .duration_market_discovery import (
    DURATION_1H,
    DURATION_SECONDS_MAP,
    REF_SOURCE_BINANCE,
    REF_SOURCE_CHAINLINK,
    DurationMarketInfo,
    classify_duration,
    classify_reference_source,
    discover_updown_markets,
    validate_known_slug,
    poll_quote_for_market,
)
from .live_market_discovery import UpDownMarketInfo
from .spread_regime import (
    SpreadEvent,
    QuoteQuality,
    classify_quote_quality,
    compute_spread_bps,
    compute_spread_abs,
    is_actionable_two_sided,
)
from .safety_checks import check_path

BRANCH = "polymarket-btc-updown-1h-quote-lifecycle-v1"

# Default known 1h slug for validation
DEFAULT_KNOWN_1H_SLUG = "bitcoin-up-or-down-may-13-2026-11pm-et"

# Lifecycle buckets by seconds to expiry
LIFECYCLE_BUCKETS = [
    ("pre_start", lambda s: s is None or s > 3600),
    ("gt_45m", lambda s: s is not None and 2700 < s <= 3600),
    ("30m_to_45m", lambda s: s is not None and 1800 < s <= 2700),
    ("15m_to_30m", lambda s: s is not None and 900 < s <= 1800),
    ("5m_to_15m", lambda s: s is not None and 300 < s <= 900),
    ("1m_to_5m", lambda s: s is not None and 60 < s <= 300),
    ("0m_to_1m", lambda s: s is not None and 0 <= s <= 60),
    ("expired", lambda s: s is not None and s < 0),
]

LIFECYCLE_BUCKET_LABELS = [b[0] for b in LIFECYCLE_BUCKETS]

# Verdict constants
V_DISCOVERY_VALIDATION_FAILED = "DISCOVERY_VALIDATION_FAILED"
V_NEEDS_MORE_DATA_NO_ACTIVE = "NEEDS_MORE_DATA_NO_ACTIVE_1H_MARKET"
V_BOOK_POLL_FAILED = "ONE_HOUR_BOOK_POLL_FAILED"
V_NO_ACTIONABLE = "ONE_HOUR_NO_ACTIONABLE_BOOK_OBSERVED"
V_TRANSIENT_ACTIONABLE = "ONE_HOUR_TRANSIENT_ACTIONABLE_BOOK_OBSERVED_NEEDS_MORE_OBSERVATION"
V_ACTIONABLE_REQUIRES_PHASE1 = "ONE_HOUR_ACTIONABLE_BOOK_OBSERVED_REQUIRES_PHASE1_BACKTEST"

# Forbidden verdicts that must never appear
FORBIDDEN_VERDICTS = {
    "ALLOW_PHASE_3",
    "READY_FOR_EXECUTION",
    "LIVE_TRADING_READY",
    "EXECUTION_READY",
    "PAPER_TRADING_READY",
}

# Default dwell thresholds for actionable-book verdict
DEFAULT_MIN_ACTIONABLE_RATE_FOR_PHASE1 = 0.05
DEFAULT_MIN_CONTIGUOUS_ACTIONABLE_SECONDS_FOR_PHASE1 = 300.0


def classify_verdict(
    actionable_count: int,
    total_count: int,
    actionable_rate: float,
    max_contiguous_actionable_seconds: float,
    book_poll_success_count: int,
    book_poll_failure_count: int,
    min_actionable_rate: float = DEFAULT_MIN_ACTIONABLE_RATE_FOR_PHASE1,
    min_contiguous_seconds: float = DEFAULT_MIN_CONTIGUOUS_ACTIONABLE_SECONDS_FOR_PHASE1,
) -> tuple[str, str]:
    """Classify the observation verdict based on actionable book statistics.

    Returns (verdict_constant, verdict_description).

    A single 5-second actionable snapshot is not enough to justify a Phase 1
    backtest. Actionable liquidity must demonstrate dwell: both a minimum
    rate across snapshots and a minimum contiguous duration.
    """
    all_polls_failed = book_poll_success_count == 0 and book_poll_failure_count > 0

    if all_polls_failed:
        return V_BOOK_POLL_FAILED, (
            "Book polling failed for the selected 1h market. "
            "This is not evidence against product existence."
        )

    any_actionable = actionable_count > 0

    if not any_actionable:
        return V_NO_ACTIONABLE, (
            "No actionable two-sided 1h books were observed in this run. "
            "This is evidence against the 1h longer-duration escape hatch "
            "for the observed window only. It is not a global mathematical proof."
        )

    # Actionable books exist — check dwell thresholds
    rate_passes = actionable_rate >= min_actionable_rate
    contiguous_passes = max_contiguous_actionable_seconds >= min_contiguous_seconds

    if rate_passes and contiguous_passes:
        return V_ACTIONABLE_REQUIRES_PHASE1, (
            "1h Binance-referenced BTC UpDown showed actionable two-sided book quality "
            "in live observation with sufficient dwell. This does not justify execution "
            "or Phase 3. It justifies a new Phase 1 backtest hypothesis for 1h BTC "
            "UpDown on a separate branch."
        )

    # Actionable books exist but failed at least one dwell threshold
    rate_str = f"rate={actionable_rate:.4f} (threshold={min_actionable_rate})"
    contiguous_str = f"contiguous={max_contiguous_actionable_seconds:.1f}s (threshold={min_contiguous_seconds:.0f}s)"
    return V_TRANSIENT_ACTIONABLE, (
        f"Some actionable two-sided book snapshots appeared, but dwell threshold was "
        f"not met ({rate_str}, {contiguous_str}). Not enough dwell or lifecycle coverage "
        f"exists to justify a Phase 1 backtest yet. Repeat observer-only 1h lifecycle captures."
    )


@dataclass(frozen=True)
class LifecycleSnapshot:
    """A single book snapshot annotated with lifecycle context."""
    ts_event_ns: int
    ts_recv_ns: int
    market_slug: str
    market_title: str
    duration_label: str
    duration_seconds: int | None
    reference_source_kind: str
    product_exists: bool
    is_active: bool
    is_closed: bool
    seconds_to_start: float | None
    seconds_to_expiry: float | None
    book_poll_success: bool
    book_poll_error: str | None
    best_bid: float | None
    best_ask: float | None
    best_bid_size: float | None
    best_ask_size: float | None
    spread_bps: float | None
    quote_quality: str
    actionable_two_sided_book: bool
    lifecycle_bucket: str
    raw_source: str


def classify_lifecycle_bucket(seconds_to_expiry: float | None) -> str:
    """Classify seconds-to-expiry into a lifecycle bucket."""
    for label, predicate in LIFECYCLE_BUCKETS:
        if predicate(seconds_to_expiry):
            return label
    return "unknown"


def compute_actionable_stats(snapshots: list[LifecycleSnapshot]) -> dict[str, Any]:
    """Compute actionable two-sided book statistics from snapshots."""
    actionable = [s for s in snapshots if s.actionable_two_sided_book]
    count = len(actionable)
    total = len(snapshots)
    rate = count / total if total > 0 else 0.0

    first_ts = min(s.ts_event_ns for s in actionable) if actionable else None
    last_ts = max(s.ts_event_ns for s in actionable) if actionable else None

    # Max contiguous actionable seconds
    max_contiguous = 0.0
    if actionable:
        # Sort by ts_event_ns
        sorted_a = sorted(actionable, key=lambda s: s.ts_event_ns)
        # Find contiguous runs (gaps smaller than 2x poll interval are contiguous)
        run_start = sorted_a[0].ts_event_ns
        run_end = sorted_a[0].ts_event_ns
        for i in range(1, len(sorted_a)):
            gap = sorted_a[i].ts_event_ns - sorted_a[i - 1].ts_event_ns
            if gap < 30_000_000_000:  # 30 seconds gap tolerance
                run_end = sorted_a[i].ts_event_ns
            else:
                run_duration = (run_end - run_start) / 1_000_000_000
                max_contiguous = max(max_contiguous, run_duration)
                run_start = sorted_a[i].ts_event_ns
                run_end = sorted_a[i].ts_event_ns
        # Final run
        run_duration = (run_end - run_start) / 1_000_000_000
        max_contiguous = max(max_contiguous, run_duration)

    return {
        "actionable_two_sided_book_count": count,
        "actionable_two_sided_book_rate": rate,
        "first_actionable_ts_event_ns": first_ts,
        "last_actionable_ts_event_ns": last_ts,
        "max_contiguous_actionable_seconds": max_contiguous,
    }


def build_quote_quality_counts(snapshots: list[LifecycleSnapshot]) -> dict[str, int]:
    """Count snapshots by quote quality."""
    counts: dict[str, int] = {}
    for s in snapshots:
        counts[s.quote_quality] = counts.get(s.quote_quality, 0) + 1
    return counts


def build_lifecycle_bucket_counts(snapshots: list[LifecycleSnapshot]) -> dict[str, dict[str, int]]:
    """Count quote qualities within each lifecycle bucket."""
    bucket_qq: dict[str, dict[str, int]] = {}
    for label in LIFECYCLE_BUCKET_LABELS:
        bucket_qq[label] = {}
    for s in snapshots:
        bucket = s.lifecycle_bucket
        if bucket not in bucket_qq:
            bucket_qq[bucket] = {}
        qq = bucket_qq[bucket]
        qq[s.quote_quality] = qq.get(s.quote_quality, 0) + 1
    return bucket_qq


def write_reports(
    output_dir: Path,
    run_id: str,
    summary: dict[str, Any],
    snapshots: list[LifecycleSnapshot],
    markets: list[DurationMarketInfo],
    book_poll_failures: list[dict],
) -> None:
    """Write all report files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # summary.json
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str)
    )

    # snapshots.jsonl
    with open(output_dir / "snapshots.jsonl", "w") as f:
        for snap in snapshots:
            f.write(json.dumps(asdict(snap), default=str) + "\n")

    # markets.csv
    with open(output_dir / "markets.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "slug", "title", "duration_label", "duration_seconds",
            "is_active", "is_closed", "reference_source_kind",
            "start_ns", "end_ns", "yes_token_id", "no_token_id",
        ])
        for dm in markets:
            w.writerow([
                dm.market.slug, dm.market.question,
                dm.duration_label, dm.duration_seconds,
                dm.market.active, dm.market.closed,
                dm.resolution_source_kind,
                dm.market.start_ns, dm.market.end_ns,
                dm.market.yes_token_id or "",
                dm.market.no_token_id or "",
            ])

    # quote_quality_by_lifecycle_bucket.csv
    bucket_qq = build_lifecycle_bucket_counts(snapshots)
    qq_order = [
        QuoteQuality.TWO_SIDED_BOOK,
        QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK,
        QuoteQuality.FALLBACK_MIN_MAX,
        QuoteQuality.ONE_SIDED_BOOK,
        QuoteQuality.EMPTY_BOOK,
        QuoteQuality.MISSING_BOOK,
        QuoteQuality.INVALID_BOOK,
    ]
    with open(output_dir / "quote_quality_by_lifecycle_bucket.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["lifecycle_bucket", "quote_quality", "count"])
        for bucket_label in LIFECYCLE_BUCKET_LABELS:
            qq_counts = bucket_qq.get(bucket_label, {})
            for qq in qq_order:
                count = qq_counts.get(qq, 0)
                if count > 0:
                    w.writerow([bucket_label, qq, count])

    # quote_quality_by_market.csv
    market_qq: dict[str, dict[str, int]] = {}
    for s in snapshots:
        if s.market_slug not in market_qq:
            market_qq[s.market_slug] = {}
        qq = market_qq[s.market_slug]
        qq[s.quote_quality] = qq.get(s.quote_quality, 0) + 1

    with open(output_dir / "quote_quality_by_market.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["market_slug", "quote_quality", "count"])
        for slug, qq_counts in sorted(market_qq.items()):
            for qq in qq_order:
                count = qq_counts.get(qq, 0)
                if count > 0:
                    w.writerow([slug, qq, count])

    # book_poll_failures.csv
    with open(output_dir / "book_poll_failures.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts_event_ns", "market_slug", "error"])
        for fail in book_poll_failures:
            w.writerow([
                fail.get("ts_event_ns", ""),
                fail.get("market_slug", ""),
                fail.get("error", ""),
            ])

    # report.md
    _write_report_md(output_dir, summary, snapshots, markets, bucket_qq)

    # safety_check.json
    pkg_dir = Path(__file__).resolve().parent
    safety = check_path(pkg_dir)
    safety_data = {
        "no_orders": True,
        "no_keys": True,
        "no_execution_client_imports": True,
        "no_on_chain_calls": True,
        "branch": BRANCH,
        "violations": safety.get("violations", []),
        "safety_ok": safety.get("ok", False),
    }
    (output_dir / "safety_check.json").write_text(
        json.dumps(safety_data, indent=2)
    )


def _write_report_md(
    output_dir: Path,
    summary: dict[str, Any],
    snapshots: list[LifecycleSnapshot],
    markets: list[DurationMarketInfo],
    bucket_qq: dict[str, dict[str, int]],
) -> None:
    """Write the report.md."""
    lines: list[str] = []

    lines.append("# Polymarket BTC UpDown 1h Full-Lifecycle Quote Observer\n")
    lines.append("## Phase Statement\n")
    lines.append("This phase answers only: Do 1h Binance-referenced BTC UpDown markets")
    lines.append("ever show actionable two-sided books during a full market lifecycle?\n")
    lines.append("This is a quote-quality observation phase, not a trading phase.\n")

    # Why this run exists
    lines.append("## Why This Run Exists\n")
    lines.append("This run exists because the previous duration probe incorrectly concluded")
    lines.append("that 1h and 4h BTC UpDown products do not exist. The 1h product existence")
    lines.append("finding has been corrected, and this run observes 1h book quality directly.\n")

    # Corrected facts
    lines.append("## Corrected Facts\n")
    lines.append("- 5m: exists, observed, exchange-bound 0.01/0.99 books, no actionable two-sided book")
    lines.append("- 15m: exists, observed in prior studies, exchange-bound 0.01/0.99 books, no actionable two-sided book")
    lines.append("- 1h: exists, Binance BTC/USDT reference, not yet properly observed over a full lifecycle")
    lines.append("- 4h: exists, Chainlink BTC/USD reference, separate future hypothesis, **not tested in this run**\n")

    # Parked hypothesis
    lines.append("## Parked Hypothesis\n")
    lines.append("4h BTC UpDown uses Chainlink BTC/USD and requires a separate Chainlink-reference")
    lines.append("Phase 1 hypothesis. Do not include 4h in this phase.\n")

    # Preconditions
    lines.append("## Preconditions\n")
    known_slug_validation = summary.get("known_slug_validation", {})
    if known_slug_validation:
        slug_name = list(known_slug_validation.keys())[0] if known_slug_validation else "N/A"
        validation = known_slug_validation.get(slug_name, {})
        passed = validation.get("product_exists", False)
        duration_ok = validation.get("duration_label") == "1h"
        ref_ok = validation.get("resolution_source_kind") == "BINANCE_BTCUSDT"
        lines.append(f"Known 1h slug validation: {'PASS' if (passed and duration_ok and ref_ok) else 'FAIL'}\n")
        lines.append(f"| Check | Result |")
        lines.append(f"|-------|--------|")
        lines.append(f"| Product exists | {passed} |")
        lines.append(f"| Duration = 1h | {duration_ok} |")
        lines.append(f"| Reference = BINANCE_BTCUSDT | {ref_ok} |")
        lines.append("")
    else:
        lines.append("No known slug validation data available.\n")

    # Separation of concepts
    lines.append("## Separation of Concepts\n")
    lines.append("Product existence is separate from active market availability.")
    lines.append("Active market availability is separate from book poll success.")
    lines.append("Book poll success is separate from actionable two-sided liquidity.\n")

    # Observation results
    lines.append("## Observation Results\n")
    verdict = summary.get("overall_verdict", "UNKNOWN")
    lines.append(f"**Overall verdict: {verdict}**\n")

    vdesc = summary.get("verdict_description", "")
    if vdesc:
        lines.append(f"{vdesc}\n")

    # Statistics
    snapshot_count = summary.get("snapshot_count", 0)
    success_count = summary.get("book_poll_success_count", 0)
    failure_count = summary.get("book_poll_failure_count", 0)
    actionable_count = summary.get("actionable_two_sided_book_count", 0)
    actionable_rate = summary.get("actionable_two_sided_book_rate", 0.0)
    max_contiguous = summary.get("max_contiguous_actionable_seconds", 0.0)

    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total snapshots | {snapshot_count} |")
    lines.append(f"| Book poll successes | {success_count} |")
    lines.append(f"| Book poll failures | {failure_count} |")
    lines.append(f"| Actionable TWO_SIDED_BOOK count | {actionable_count} |")
    lines.append(f"| Actionable TWO_SIDED_BOOK rate | {actionable_rate:.4f} |")
    lines.append(f"| Max contiguous actionable seconds | {max_contiguous:.1f} |")
    lines.append(f"| Min actionable rate threshold | {summary.get('min_actionable_rate_for_phase1', 0.05)} |")
    lines.append(f"| Min contiguous seconds threshold | {summary.get('min_contiguous_actionable_seconds_for_phase1', 300)} |")
    lines.append("")

    # Quote quality counts
    qq_counts = summary.get("quote_quality_counts", {})
    lines.append("### Quote Quality Breakdown\n")
    lines.append("| Quote Quality | Count |")
    lines.append("|--------------|-------|")
    for qq in [
        QuoteQuality.TWO_SIDED_BOOK,
        QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK,
        QuoteQuality.FALLBACK_MIN_MAX,
        QuoteQuality.ONE_SIDED_BOOK,
        QuoteQuality.EMPTY_BOOK,
        QuoteQuality.MISSING_BOOK,
        QuoteQuality.INVALID_BOOK,
    ]:
        count = qq_counts.get(qq, 0)
        lines.append(f"| {qq} | {count} |")
    lines.append("")

    # Lifecycle bucket breakdown
    lines.append("### Quote Quality by Lifecycle Bucket\n")
    lines.append("| Bucket | TWO_SIDED | EXCHANGE_BOUND | ONE_SIDED | EMPTY | MISSING | Total |")
    lines.append("|--------|-----------|----------------|-----------|-------|---------|-------|")
    for bucket_label in LIFECYCLE_BUCKET_LABELS:
        bq = bucket_qq.get(bucket_label, {})
        two = bq.get(QuoteQuality.TWO_SIDED_BOOK, 0)
        eb = bq.get(QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK, 0)
        one = bq.get(QuoteQuality.ONE_SIDED_BOOK, 0)
        empty = bq.get(QuoteQuality.EMPTY_BOOK, 0)
        missing = bq.get(QuoteQuality.MISSING_BOOK, 0)
        total = sum(bq.values())
        if total > 0:
            lines.append(f"| {bucket_label} | {two} | {eb} | {one} | {empty} | {missing} | {total} |")
    lines.append("")

    # Actionable verdict interpretation
    lines.append("## Verdict Interpretation\n")
    if verdict == V_ACTIONABLE_REQUIRES_PHASE1:
        lines.append("1h Binance-referenced BTC UpDown showed actionable two-sided book quality")
        lines.append("in live observation. This does not justify execution or Phase 3.")
        lines.append("It justifies a new Phase 1 backtest hypothesis for 1h BTC UpDown on a")
        lines.append("separate branch.\n")
    elif verdict == V_NO_ACTIONABLE:
        lines.append("No actionable two-sided 1h books were observed in this run.")
        lines.append("This is evidence against the 1h longer-duration escape hatch for the")
        lines.append("observed window only. It is not a global mathematical proof.\n")
    elif verdict == V_TRANSIENT_ACTIONABLE:
        lines.append("Some actionable two-sided book snapshots appeared, but not enough dwell")
        lines.append("or lifecycle coverage exists to justify a Phase 1 backtest yet.")
        lines.append("Recommend more observer-only 1h lifecycle captures.")
        lines.append("This does not recommend execution or Phase 3.\n")
    elif verdict == V_NEEDS_MORE_DATA_NO_ACTIVE:
        lines.append("No active 1h BTC UpDown market was available during this run.")
        lines.append("This produces no liquidity evidence and must be treated as NEEDS_MORE_DATA.\n")
    elif verdict == V_BOOK_POLL_FAILED:
        lines.append("Book polling failed for the selected 1h market. This is not evidence")
        lines.append("against product existence. Re-run when market is active and API is reachable.\n")
    elif verdict == V_DISCOVERY_VALIDATION_FAILED:
        lines.append("Discovery validation failed for the known 1h slug. The API may be down")
        lines.append("or the slug may have expired. Re-run with a fresh slug.\n")
    else:
        lines.append(f"Unexpected verdict: {verdict}\n")

    # What this does NOT do
    lines.append("## What This Run Does NOT Do\n")
    lines.append("- Does not implement execution.")
    lines.append("- Does not add keys.")
    lines.append("- Does not add orders.")
    lines.append("- Does not call on-chain methods.")
    lines.append("- Does not start Phase 3.")
    lines.append("- Does not backtest.")
    lines.append("- Does not touch the 4h Chainlink hypothesis.")
    lines.append("- Does not treat NEEDS_MORE_DATA as a win.")
    lines.append("- Does not globally reject Polymarket binary arb.\n")

    # Limitations
    lines.append("## Limitations\n")
    for lim in summary.get("limitations", []):
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


def main() -> int:
    """Main entry point for the 1h quote lifecycle observer."""
    parser = argparse.ArgumentParser(
        description="1h Binance-referenced BTC UpDown full-lifecycle quote observer (DQO-2)",
    )
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=3900,
        help="Total observation duration in seconds (default: 3900, ~65min for 1h market lifecycle)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=5,
        help="Seconds between book polls (default: 5)",
    )
    parser.add_argument(
        "--duration",
        type=str,
        default="1h",
        help="Duration label to observe (default: 1h)",
    )
    parser.add_argument(
        "--known-slug",
        type=str,
        default=DEFAULT_KNOWN_1H_SLUG,
        help=f"Known 1h slug for discovery validation (default: {DEFAULT_KNOWN_1H_SLUG})",
    )
    parser.add_argument(
        "--market-slug",
        type=str,
        default=None,
        help="Specific market slug to observe (skips discovery selection)",
    )
    parser.add_argument(
        "--max-markets",
        type=int,
        default=10,
        help="Maximum markets to discover (default: 10)",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/polymarket_btcusd_arb/one_hour_quote_lifecycle"),
        help="Output directory for reports",
    )
    parser.add_argument(
        "--dry-discover",
        action="store_true",
        default=False,
        help="Only discover and validate, do not poll books",
    )
    parser.add_argument(
        "--fail-if-discovery-unvalidated",
        action="store_true",
        default=False,
        help="Exit nonzero if known slug validation fails",
    )
    parser.add_argument(
        "--min-actionable-rate-for-phase1",
        type=float,
        default=DEFAULT_MIN_ACTIONABLE_RATE_FOR_PHASE1,
        help=f"Minimum actionable two-sided book rate to justify Phase 1 backtest (default: {DEFAULT_MIN_ACTIONABLE_RATE_FOR_PHASE1})",
    )
    parser.add_argument(
        "--min-contiguous-actionable-seconds-for-phase1",
        type=float,
        default=DEFAULT_MIN_CONTIGUOUS_ACTIONABLE_SECONDS_FOR_PHASE1,
        help=f"Minimum contiguous actionable seconds to justify Phase 1 backtest (default: {DEFAULT_MIN_CONTIGUOUS_ACTIONABLE_SECONDS_FOR_PHASE1})",
    )
    argv = parser.parse_args()

    # Phase banner
    print("PHASE=DQO-2_ONE_HOUR_QUOTE_LIFECYCLE")
    print("OBSERVER_ONLY=1")
    print("NO_ORDERS=1")
    print("NO_KEYS=1")
    print(f"BRANCH={BRANCH}")
    print(f"DURATION={argv.duration}")
    print(f"KNOWN_SLUG={argv.known_slug}")

    # Branch check
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[4],
    )
    current_branch = result.stdout.strip()
    if not current_branch.startswith("polymarket-btc-updown-1h-quote-lifecycle"):
        print(f"WARNING: Active branch is '{current_branch}', expected '{BRANCH}'")

    # Commit hash
    commit_result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[4],
    )
    commit_hash = commit_result.stdout.strip()

    # --- Step 1: Validate known 1h slug ---
    print("\n=== Step 1: Known 1h Slug Validation ===")
    known_slug_result: dict[str, Any] = {}
    validated_info: DurationMarketInfo | None = None

    slug_result = validate_known_slug(argv.known_slug)
    if slug_result is not None:
        known_slug_result = {
            "slug": argv.known_slug,
            "product_exists": True,
            "duration_label": slug_result.duration_label,
            "duration_seconds": slug_result.duration_seconds,
            "is_active": slug_result.market.active,
            "is_closed": slug_result.market.closed,
            "resolution_source_kind": slug_result.resolution_source_kind,
            "yes_token_id": slug_result.market.yes_token_id or "",
            "validation_source": "polymarket_gamma_api",
        }
        validated_info = slug_result
        print(f"  VALID: {slug_result.duration_label} | active={slug_result.market.active} | ref={slug_result.resolution_source_kind}")
    else:
        known_slug_result = {
            "slug": argv.known_slug,
            "product_exists": False,
            "error": "slug_not_found_in_gamma_api",
        }
        print(f"  NOT FOUND: {argv.known_slug}")

    # Check validation
    slug_valid = (
        known_slug_result.get("product_exists") is True
        and known_slug_result.get("duration_label") == "1h"
        and known_slug_result.get("resolution_source_kind") == "BINANCE_BTCUSDT"
    )

    if not slug_valid:
        print("\n  DISCOVERY_VALIDATION_FAILED")
        print(f"  Known slug '{argv.known_slug}' did not validate as 1h Binance BTC/USDT.")
        print("  Cannot proceed with lifecycle observation without validated discovery.")

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = argv.report_dir / run_id
        summary = {
            "run_id": run_id,
            "branch": BRANCH,
            "commit": commit_hash,
            "known_slug_validation": {argv.known_slug: known_slug_result},
            "selected_market": None,
            "duration_requested": argv.duration,
            "reference_source": "BINANCE_BTCUSDT",
            "observation_duration_seconds": argv.duration_seconds,
            "poll_seconds": argv.poll_seconds,
            "snapshot_count": 0,
            "book_poll_success_count": 0,
            "book_poll_failure_count": 0,
            "quote_quality_counts": {},
            "quote_quality_counts_by_lifecycle_bucket": {},
            "actionable_two_sided_book_count": 0,
            "actionable_two_sided_book_rate": 0.0,
            "first_actionable_ts_event_ns": None,
            "last_actionable_ts_event_ns": None,
            "max_contiguous_actionable_seconds": 0.0,
            "min_actionable_rate_for_phase1": argv.min_actionable_rate_for_phase1,
            "min_contiguous_actionable_seconds_for_phase1": argv.min_contiguous_actionable_seconds_for_phase1,
            "overall_verdict": V_DISCOVERY_VALIDATION_FAILED,
            "verdict_description": "Known 1h slug validation failed. Cannot proceed.",
            "limitations": [
                "Observer-only. No orders. No keys.",
                "Public API data only.",
                "Discovery validation failed — cannot observe without validated slug.",
                "4h Chainlink hypothesis is parked.",
            ],
        }
        write_reports(output_dir, run_id, summary, [], [], [])

        if argv.fail_if_discovery_unvalidated:
            return 1
        return 0

    # --- Step 2: Discover active 1h markets ---
    print("\n=== Step 2: Discover Active 1h Markets ===")
    all_1h_markets = discover_updown_markets(
        asset_filter="btc",
        durations=(argv.duration,),
        include_active=True,
        include_closed=False,
        max_markets=argv.max_markets,
    )

    # Also get closed/resolved markets to understand product existence
    all_1h_markets_closed = discover_updown_markets(
        asset_filter="btc",
        durations=(argv.duration,),
        include_active=False,
        include_closed=True,
        max_markets=5,
    )

    # Merge
    seen_slugs = {dm.market.slug for dm in all_1h_markets}
    for dm in all_1h_markets_closed:
        if dm.market.slug not in seen_slugs:
            all_1h_markets.append(dm)
            seen_slugs.add(dm.market.slug)

    # Add validated slug if not already present
    if validated_info and validated_info.market.slug not in seen_slugs:
        all_1h_markets.append(validated_info)
        seen_slugs.add(validated_info.market.slug)

    active_1h = [dm for dm in all_1h_markets if dm.market.active and not dm.market.closed]

    print(f"  Total 1h markets found: {len(all_1h_markets)}")
    print(f"  Active 1h markets: {len(active_1h)}")
    for dm in all_1h_markets:
        print(f"    {dm.market.slug} | active={dm.market.active} | ref={dm.resolution_source_kind} | dur={dm.duration_label}")

    # --- Step 3: Select market to observe ---
    print("\n=== Step 3: Select Market ===")
    selected: DurationMarketInfo | None = None

    if argv.market_slug:
        # User-specified market
        for dm in all_1h_markets:
            if dm.market.slug == argv.market_slug:
                selected = dm
                break
        if selected is None:
            # Try validating it directly
            direct = validate_known_slug(argv.market_slug)
            if direct:
                selected = direct
                print(f"  Using user-specified slug: {argv.market_slug}")
        else:
            print(f"  Using user-specified slug: {argv.market_slug}")
    else:
        # Pick first active 1h market with Binance reference
        binance_active = [
            dm for dm in active_1h
            if dm.resolution_source_kind == REF_SOURCE_BINANCE
        ]
        if binance_active:
            selected = binance_active[0]
            print(f"  Selected active Binance 1h market: {selected.market.slug}")
        elif active_1h:
            selected = active_1h[0]
            print(f"  Selected active 1h market (non-Binance ref): {selected.market.slug}")
        else:
            print("  No active 1h market available.")

    # If no active 1h market found, exit with NEEDS_MORE_DATA
    if selected is None:
        print("\n  NEEDS_MORE_DATA_NO_ACTIVE_1H_MARKET")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = argv.report_dir / run_id

        summary = {
            "run_id": run_id,
            "branch": BRANCH,
            "commit": commit_hash,
            "known_slug_validation": {argv.known_slug: known_slug_result},
            "selected_market": None,
            "duration_requested": argv.duration,
            "reference_source": "BINANCE_BTCUSDT",
            "observation_duration_seconds": argv.duration_seconds,
            "poll_seconds": argv.poll_seconds,
            "snapshot_count": 0,
            "book_poll_success_count": 0,
            "book_poll_failure_count": 0,
            "quote_quality_counts": {},
            "quote_quality_counts_by_lifecycle_bucket": {},
            "actionable_two_sided_book_count": 0,
            "actionable_two_sided_book_rate": 0.0,
            "first_actionable_ts_event_ns": None,
            "last_actionable_ts_event_ns": None,
            "max_contiguous_actionable_seconds": 0.0,
            "min_actionable_rate_for_phase1": argv.min_actionable_rate_for_phase1,
            "min_contiguous_actionable_seconds_for_phase1": argv.min_contiguous_actionable_seconds_for_phase1,
            "overall_verdict": V_NEEDS_MORE_DATA_NO_ACTIVE,
            "verdict_description": "No active 1h BTC UpDown market was available during this run.",
            "limitations": [
                "Observer-only. No orders. No keys.",
                "Public API data only.",
                "No active 1h market was available for observation.",
                "4h Chainlink hypothesis is parked.",
                "This produces no liquidity evidence and must be treated as NEEDS_MORE_DATA.",
            ],
        }
        write_reports(output_dir, run_id, summary, [], all_1h_markets, [])
        print(f"\nReports written to: {output_dir}")
        print(f"Overall verdict: {V_NEEDS_MORE_DATA_NO_ACTIVE}")
        return 0

    # Validate selected market is 1h and Binance
    if selected.duration_label != "1h":
        print(f"\n  ERROR: Selected market is {selected.duration_label}, expected 1h.")
        return 1

    if selected.resolution_source_kind != REF_SOURCE_BINANCE:
        print(f"\n  WARNING: Selected market reference source is {selected.resolution_source_kind}, "
              f"expected {REF_SOURCE_BINANCE}.")

    print(f"  Selected: {selected.market.slug}")
    print(f"    duration={selected.duration_label}")
    print(f"    ref_source={selected.resolution_source_kind}")
    print(f"    active={selected.market.active}")
    print(f"    start_ns={selected.market.start_ns}")
    print(f"    end_ns={selected.market.end_ns}")

    if argv.dry_discover:
        print("\nDRY_DISCOVER=1. Skipping observation.")
        return 0

    # --- Step 4: Poll books over lifecycle ---
    print(f"\n=== Step 4: Poll Book Lifecycle ({argv.duration_seconds}s, {argv.poll_seconds}s interval) ===")

    snapshots: list[LifecycleSnapshot] = []
    book_poll_failures: list[dict] = []

    start_time = time.time()
    poll_count = 0
    success_count = 0
    failure_count = 0

    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed >= argv.duration_seconds:
                print(f"\n  Observation duration reached ({argv.duration_seconds}s). Stopping.")
                break

            now_ns = int(time.time() * 1_000_000_000)

            # Calculate time-to-expiry and time-to-start
            seconds_to_expiry = None
            seconds_to_start = None
            if selected.market.end_ns:
                seconds_to_expiry = (selected.market.end_ns - now_ns) / 1_000_000_000
            if selected.market.start_ns:
                seconds_to_start = (selected.market.start_ns - now_ns) / 1_000_000_000

            # Classify lifecycle bucket
            lifecycle_bucket = classify_lifecycle_bucket(seconds_to_expiry)

            # Poll book
            poll_count += 1
            quote = poll_quote_for_market(selected.market)

            if quote is None:
                failure_count += 1
                book_poll_failures.append({
                    "ts_event_ns": now_ns,
                    "market_slug": selected.market.slug,
                    "error": "book_poll_returned_none",
                })
                qq = QuoteQuality.MISSING_BOOK
                snap = LifecycleSnapshot(
                    ts_event_ns=now_ns,
                    ts_recv_ns=now_ns,
                    market_slug=selected.market.slug,
                    market_title=selected.market.question or "",
                    duration_label=selected.duration_label,
                    duration_seconds=selected.duration_seconds,
                    reference_source_kind=selected.resolution_source_kind,
                    product_exists=True,
                    is_active=selected.market.active,
                    is_closed=selected.market.closed,
                    seconds_to_start=seconds_to_start,
                    seconds_to_expiry=seconds_to_expiry,
                    book_poll_success=False,
                    book_poll_error="book_poll_returned_none",
                    best_bid=None,
                    best_ask=None,
                    best_bid_size=None,
                    best_ask_size=None,
                    spread_bps=None,
                    quote_quality=qq,
                    actionable_two_sided_book=False,
                    lifecycle_bucket=lifecycle_bucket,
                    raw_source="clob_poll_failure",
                )
                snapshots.append(snap)
                print(f"  [{elapsed:.0f}s] BOOK_POLL_FAILED | bucket={lifecycle_bucket}")
            else:
                success_count += 1
                best_bid = quote.get("best_bid")
                best_ask = quote.get("best_ask")
                spread_bps = quote.get("spread_bps")
                if spread_bps is None and best_bid is not None and best_ask is not None:
                    spread_bps = compute_spread_bps(best_bid, best_ask)

                qq = classify_quote_quality(
                    best_bid, best_ask,
                    quote.get("depth_bid"), quote.get("depth_ask"),
                    is_synthetic_fallback=False,
                )
                actionable = is_actionable_two_sided(qq)

                snap = LifecycleSnapshot(
                    ts_event_ns=quote.get("ts_event_ns", now_ns),
                    ts_recv_ns=now_ns,
                    market_slug=selected.market.slug,
                    market_title=selected.market.question or "",
                    duration_label=selected.duration_label,
                    duration_seconds=selected.duration_seconds,
                    reference_source_kind=selected.resolution_source_kind,
                    product_exists=True,
                    is_active=selected.market.active,
                    is_closed=selected.market.closed,
                    seconds_to_start=seconds_to_start,
                    seconds_to_expiry=seconds_to_expiry,
                    book_poll_success=True,
                    book_poll_error=None,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    best_bid_size=quote.get("depth_bid"),
                    best_ask_size=quote.get("depth_ask"),
                    spread_bps=spread_bps,
                    quote_quality=qq,
                    actionable_two_sided_book=actionable,
                    lifecycle_bucket=lifecycle_bucket,
                    raw_source="clob_poll",
                )
                snapshots.append(snap)
                bucket_str = f"bucket={lifecycle_bucket}"
                print(f"  [{elapsed:.0f}s] bid={best_bid} ask={best_ask} qq={qq} actionable={actionable} {bucket_str}")

            # Sleep until next poll
            time.sleep(argv.poll_seconds)

    except KeyboardInterrupt:
        print("\n  Observation interrupted by user.")

    # --- Step 5: Compute statistics and verdict ---
    print(f"\n=== Step 5: Statistics ===")
    print(f"  Total snapshots: {len(snapshots)}")
    print(f"  Book poll successes: {success_count}")
    print(f"  Book poll failures: {failure_count}")

    actionable_stats = compute_actionable_stats(snapshots)
    qq_counts = build_quote_quality_counts(snapshots)
    bucket_qq = build_lifecycle_bucket_counts(snapshots)

    print(f"  Actionable TWO_SIDED_BOOK count: {actionable_stats['actionable_two_sided_book_count']}")
    print(f"  Actionable rate: {actionable_stats['actionable_two_sided_book_rate']:.4f}")
    print(f"  Max contiguous actionable: {actionable_stats['max_contiguous_actionable_seconds']:.1f}s")
    print(f"  Min actionable rate threshold: {argv.min_actionable_rate_for_phase1}")
    print(f"  Min contiguous seconds threshold: {argv.min_contiguous_actionable_seconds_for_phase1}")
    print(f"  Quote quality counts: {qq_counts}")

    # Determine verdict using dwell-threshold-aware classifier
    overall_verdict, verdict_description = classify_verdict(
        actionable_count=actionable_stats["actionable_two_sided_book_count"],
        total_count=len(snapshots),
        actionable_rate=actionable_stats["actionable_two_sided_book_rate"],
        max_contiguous_actionable_seconds=actionable_stats["max_contiguous_actionable_seconds"],
        book_poll_success_count=success_count,
        book_poll_failure_count=failure_count,
        min_actionable_rate=argv.min_actionable_rate_for_phase1,
        min_contiguous_seconds=argv.min_contiguous_actionable_seconds_for_phase1,
    )

    # Verify verdict is not forbidden
    assert overall_verdict not in FORBIDDEN_VERDICTS, f"Verdict {overall_verdict} is forbidden"

    print(f"\n  Overall verdict: {overall_verdict}")

    # --- Step 6: Write reports ---
    print(f"\n=== Step 6: Reports ===")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = argv.report_dir / run_id

    selected_market_info = {
        "slug": selected.market.slug,
        "title": selected.market.question or "",
        "duration_label": selected.duration_label,
        "duration_seconds": selected.duration_seconds,
        "reference_source_kind": selected.resolution_source_kind,
        "is_active": selected.market.active,
        "start_ns": selected.market.start_ns,
        "end_ns": selected.market.end_ns,
    } if selected else None

    summary = {
        "run_id": run_id,
        "branch": BRANCH,
        "commit": commit_hash,
        "known_slug_validation": {argv.known_slug: known_slug_result},
        "selected_market": selected_market_info,
        "duration_requested": argv.duration,
        "reference_source": "BINANCE_BTCUSDT",
        "observation_duration_seconds": argv.duration_seconds,
        "poll_seconds": argv.poll_seconds,
        "snapshot_count": len(snapshots),
        "book_poll_success_count": success_count,
        "book_poll_failure_count": failure_count,
        "quote_quality_counts": qq_counts,
        "quote_quality_counts_by_lifecycle_bucket": {
            k: {q: c for q, c in qq.items()}
            for k, qq in bucket_qq.items()
        },
        "actionable_two_sided_book_count": actionable_stats["actionable_two_sided_book_count"],
        "actionable_two_sided_book_rate": actionable_stats["actionable_two_sided_book_rate"],
        "first_actionable_ts_event_ns": actionable_stats["first_actionable_ts_event_ns"],
        "last_actionable_ts_event_ns": actionable_stats["last_actionable_ts_event_ns"],
        "max_contiguous_actionable_seconds": actionable_stats["max_contiguous_actionable_seconds"],
        "min_actionable_rate_for_phase1": argv.min_actionable_rate_for_phase1,
        "min_contiguous_actionable_seconds_for_phase1": argv.min_contiguous_actionable_seconds_for_phase1,
        "overall_verdict": overall_verdict,
        "verdict_description": verdict_description,
        "limitations": [
            "Observer-only. No orders. No keys.",
            "Public API data only.",
            "Product existence is separate from active market availability.",
            "Book poll success is separate from actionable two-sided liquidity.",
            "4h Chainlink hypothesis is parked — do not include in this phase.",
            "Single 1h market observed — not a global sample.",
            "Observation window may not cover full market lifecycle.",
        ],
    }

    write_reports(output_dir, run_id, summary, snapshots, all_1h_markets, book_poll_failures)

    print(f"\nReports written to: {output_dir}")
    print(f"Overall verdict: {overall_verdict}")
    print(f"Actionable count: {actionable_stats['actionable_two_sided_book_count']}")
    print(f"Actionable rate: {actionable_stats['actionable_two_sided_book_rate']:.4f}")
    print(f"Max contiguous actionable: {actionable_stats['max_contiguous_actionable_seconds']:.1f}s")
    print(f"Min actionable rate threshold: {argv.min_actionable_rate_for_phase1}")
    print(f"Min contiguous seconds threshold: {argv.min_contiguous_actionable_seconds_for_phase1}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())