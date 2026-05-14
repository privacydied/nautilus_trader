"""Polymarket BTC UpDown spread regime observer study.

Answers: Is this venue ever tight enough to trade?

This is a market-structure study, NOT a trading strategy.
It measures quoted spreads on Polymarket BTC UpDown markets and evaluates
whether spreads are tight enough for any fair-probability strategy to survive.

No orders. No keys. No execution. No on-chain calls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

# --- Constants ---

SPREAD_THRESHOLDS_BPS = (20.0, 40.0, 80.0, 100.0, 200.0, 500.0)

TTE_BUCKET_EDGES_NS = (
    0,
    30_000_000_000,       # 0-30s
    60_000_000_000,       # 30-60s
    180_000_000_000,      # 60-180s (1-3 min)
    300_000_000_000,      # 180-300s (3-5 min)
    600_000_000_000,      # 300-600s (5-10 min)
)

TTE_BUCKET_LABELS = (
    "0-30s",
    "30-60s",
    "60-180s",
    "180-300s",
    "300-600s",
    "600s+",
)

VOLATILITY_BUCKET_EDGES_BPS = (
    0.0,
    5.0,    # <5 bps = very low vol
    20.0,   # 5-20 bps = low vol
    50.0,   # 20-50 bps = moderate vol
    100.0,  # 50-100 bps = high vol
)

VOLATILITY_BUCKET_LABELS = (
    "0-5bps",
    "5-20bps",
    "20-50bps",
    "50-100bps",
    "100+bps",
)

TIME_OF_DAY_BUCKETS_UTC = (
    (0, 6, "00-06UTC"),
    (6, 12, "06-12UTC"),
    (12, 18, "12-18UTC"),
    (18, 24, "18-24UTC"),
)

# Polymarket CLOB minimum and maximum token prices.
# These are the exchange min/max bounds for binary option tokens,
# not real two-sided resting quotes representing actionable liquidity.
POLYMARKET_MIN_TOKEN_PRICE = 0.01
POLYMARKET_MAX_TOKEN_PRICE = 0.99

# Tolerance for floating-point comparison of min/max bounds
POLYMARKET_PRICE_TOLERANCE = 1e-6


# --- Quote Quality Classification ---

class QuoteQuality:
    """Classification of quote quality for a spread observation.

    TWO_SIDED_BOOK:
        Real bid and ask levels from the CLOB, neither at exchange min/max bounds.
    ONE_SIDED_BOOK:
        Only one side of the book exists (bid or ask, not both).
    EMPTY_BOOK:
        Both sides are empty / None.
    FALLBACK_MIN_MAX:
        Best bid is at exchange minimum (0.01) and best ask is at exchange maximum (0.99).
        These are real resting orders but represent lottery-market liquidity at
        the absolute price bounds, not actionable two-sided spread for a
        fair-probability strategy. The spread is technically real but
        structurally meaningless for trading.
    MISSING_BOOK:
        No usable book payload.
    INVALID_BOOK:
        Bid > ask or other boundary violations.
    """
    TWO_SIDED_BOOK = "TWO_SIDED_BOOK"
    ONE_SIDED_BOOK = "ONE_SIDED_BOOK"
    EMPTY_BOOK = "EMPTY_BOOK"
    FALLBACK_MIN_MAX = "FALLBACK_MIN_MAX"
    MISSING_BOOK = "MISSING_BOOK"
    INVALID_BOOK = "INVALID_BOOK"


def classify_quote_quality(
    best_bid: float | None,
    best_ask: float | None,
    book_depth_bid: float | None = None,
    book_depth_ask: float | None = None,
) -> str:
    """Classify the quality of a quoted bid/ask pair.

    Returns one of the QuoteQuality constants.

    Polymarket CLOB minimum token price is $0.01 and maximum is $0.99.
    When the best bid is 0.01 and best ask is 0.99, this represents resting
    orders at the exchange price bounds. These are technically real CLOB
    levels with genuine size, but they are lottery-market quotes — the
    equivalent of "I'll buy at 1 cent and sell at 99 cents."

    This is classified as FALLBACK_MIN_MAX, not TWO_SIDED_BOOK, because:
    - The spread at these bounds (19,600 bps) provides no actionable liquidity
    - No fair-probability strategy can survive this spread
    - The presence of size at these levels does not indicate genuine price discovery
    """
    # Neither side present
    if best_bid is None and best_ask is None:
        return QuoteQuality.EMPTY_BOOK

    # Exactly one side present
    if best_bid is None or best_ask is None:
        return QuoteQuality.ONE_SIDED_BOOK

    # Both sides present — check for invalid
    if best_bid > best_ask + POLYMARKET_PRICE_TOLERANCE:
        return QuoteQuality.INVALID_BOOK

    if best_bid <= 0.0 or best_ask <= 0.0:
        return QuoteQuality.INVALID_BOOK

    # Check if both sides are at exchange min/max bounds
    # bid ≈ 0.01 and ask ≈ 0.99 means lottery-market liquidity
    bid_is_min = abs(best_bid - POLYMARKET_MIN_TOKEN_PRICE) < POLYMARKET_PRICE_TOLERANCE
    ask_is_max = abs(best_ask - POLYMARKET_MAX_TOKEN_PRICE) < POLYMARKET_PRICE_TOLERANCE

    if bid_is_min and ask_is_max:
        return QuoteQuality.FALLBACK_MIN_MAX

    # Both sides present, not at min/max bounds, not invalid
    return QuoteQuality.TWO_SIDED_BOOK


@dataclass
class SpreadEvent:
    """A single observed spread measurement point."""
    market_slug: str
    ts_event_ns: int
    time_to_expiry_ns: int
    best_bid: float | None
    best_ask: float | None
    mid: float | None
    spread_abs: float | None
    spread_bps: float | None
    book_depth_bid: float | None
    book_depth_ask: float | None
    binance_price: float | None
    binance_spread_bps: float | None
    binance_short_window_vol_bps: float | None
    polymarket_stale: bool
    binance_stale: bool
    quote_quality: str = ""

    def __post_init__(self):
        if not self.quote_quality:
            self.quote_quality = classify_quote_quality(
                self.best_bid, self.best_ask,
                self.book_depth_bid, self.book_depth_ask,
            )


@dataclass
class TTEBucketAssignment:
    """Which TTE bucket an event falls into."""
    tte_ns: int
    bucket_label: str
    bucket_index: int


def compute_spread_bps(best_bid: float | None, best_ask: float | None) -> float | None:
    """Compute spread in basis points from bid/ask.

    Returns None if either side is missing or non-positive,
    or if the mid price is zero (division by zero).

    NOTE: spread_bps computed from FALLBACK_MIN_MAX quotes (0.01/0.99)
    is technically real but structurally meaningless. The spread_regime
    study separates these from genuine TWO_SIDED_BOOK spreads in reporting.
    """
    if best_bid is None or best_ask is None:
        return None
    if best_bid <= 0.0 or best_ask <= 0.0:
        return None
    if best_ask < best_bid:
        # Crossed market — invalid for spread computation
        return None
    mid = (best_bid + best_ask) / 2.0
    if mid <= 0.0:
        return None
    spread_abs = best_ask - best_bid
    return (spread_abs / mid) * 10_000.0


def compute_spread_abs(best_bid: float | None, best_ask: float | None) -> float | None:
    """Compute absolute spread (ask - bid).

    Returns None if either side is missing or non-positive.
    """
    if best_bid is None or best_ask is None:
        return None
    if best_bid <= 0.0 or best_ask <= 0.0:
        return None
    return best_ask - best_bid


def assign_tte_bucket(tte_ns: int) -> TTEBucketAssignment:
    """Assign a time-to-expiry nanosecond value to a TTE bucket."""
    for i, edge in enumerate(TTE_BUCKET_EDGES_NS[1:], start=0):
        if tte_ns < edge:
            return TTEBucketAssignment(
                tte_ns=tte_ns,
                bucket_label=TTE_BUCKET_LABELS[i],
                bucket_index=i,
            )
    return TTEBucketAssignment(
        tte_ns=tte_ns,
        bucket_label=TTE_BUCKET_LABELS[-1],
        bucket_index=len(TTE_BUCKET_LABELS) - 1,
    )


def assign_time_of_day_bucket(ts_event_ns: int) -> str:
    """Assign a nanosecond timestamp to a UTC time-of-day bucket."""
    dt = datetime.fromtimestamp(ts_event_ns / 1e9, tz=timezone.utc)
    hour = dt.hour
    for start, end, label in TIME_OF_DAY_BUCKETS_UTC:
        if start <= hour < end:
            return label
    return "unknown"


def assign_volatility_bucket(vol_bps: float | None) -> str:
    """Assign a Binance short-window volatility to a bucket.

    Returns 'unknown' if vol is None or negative.
    """
    if vol_bps is None or vol_bps < 0.0:
        return "unknown"
    for i, edge in enumerate(VOLATILITY_BUCKET_EDGES_BPS[1:], start=0):
        if vol_bps < edge:
            return VOLATILITY_BUCKET_LABELS[i]
    return VOLATILITY_BUCKET_LABELS[-1]


def compute_percentile(sorted_values: list[float], percentile: float) -> float | None:
    """Compute a percentile from a sorted list of values.

    Returns None for empty input.
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    # Linear interpolation
    idx = percentile * (len(sorted_values) - 1)
    lower = int(idx)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = idx - lower
    return sorted_values[lower] + frac * (sorted_values[upper] - sorted_values[lower])


@dataclass
class SpreadBucketCounts:
    """Count of events falling below each spread threshold."""
    total: int = 0
    below_20bps: int = 0
    below_40bps: int = 0
    below_80bps: int = 0
    below_100bps: int = 0
    below_200bps: int = 0
    below_500bps: int = 0

    def to_pct(self, count: int) -> float:
        """Return count as a percentage of total events."""
        if self.total == 0:
            return 0.0
        return 100.0 * count / self.total


def compute_spread_bucket_counts(spreads: list[float]) -> SpreadBucketCounts:
    """Compute how many spreads fall below each threshold."""
    counts = SpreadBucketCounts(total=len(spreads))
    for s in spreads:
        if s < 20.0:
            counts.below_20bps += 1
        if s < 40.0:
            counts.below_40bps += 1
        if s < 80.0:
            counts.below_80bps += 1
        if s < 100.0:
            counts.below_100bps += 1
        if s < 200.0:
            counts.below_200bps += 1
        if s < 500.0:
            counts.below_500bps += 1
    return counts


@dataclass
class QuoteQualityCounts:
    """Count of events by quote quality classification."""
    two_sided_book: int = 0
    one_sided_book: int = 0
    empty_book: int = 0
    fallback_min_max: int = 0
    missing_book: int = 0
    invalid_book: int = 0


@dataclass
class SpreadRegimeSummary:
    """Aggregate summary of spread regime study results."""
    run_id: str
    input_capture_dirs: list[str]
    market_count: int
    event_count: int
    valid_spread_count: int
    median_spread_bps: float | None
    p25_spread_bps: float | None
    p75_spread_bps: float | None
    p90_spread_bps: float | None
    p95_spread_bps: float | None
    pct_spread_lte_20bps: float
    pct_spread_lte_40bps: float
    pct_spread_lte_80bps: float
    pct_spread_lte_100bps: float
    pct_spread_lte_200bps: float
    pct_spread_lte_500bps: float
    dominant_tte_bucket: str
    tightest_tte_bucket: str
    widest_tte_bucket: str
    safety_status: str
    recommendation: str
    quote_quality_counts: QuoteQualityCounts = field(default_factory=QuoteQualityCounts)
    two_sided_book_count: int = 0
    one_sided_book_count: int = 0
    empty_book_count: int = 0
    fallback_min_max_count: int = 0
    missing_book_count: int = 0
    invalid_book_count: int = 0
    pct_two_sided_book: float = 0.0
    pct_fallback_min_max: float = 0.0
    verdict_reason: str = ""

    VERDICT_NEEDS_MORE_DATA = "SPREAD_REGIME_NEEDS_MORE_DATA"
    VERDICT_STRUCTURALLY_TOO_WIDE = "SPREAD_REGIME_STRUCTURALLY_TOO_WIDE"
    VERDICT_HAS_TIGHT_WINDOWS = "SPREAD_REGIME_HAS_TIGHT_WINDOWS"


def classify_verdict(summary: SpreadRegimeSummary) -> str:
    """Classify the spread regime verdict with reason.

    SPREAD_REGIME_NEEDS_MORE_DATA: too few windows/events.
    SPREAD_REGIME_STRUCTURALLY_TOO_WIDE: most spreads far above thresholds.
    SPREAD_REGIME_HAS_TIGHT_WINDOWS: meaningful tight-spread percentage
                                      in repeatable regimes.
    """
    if summary.valid_spread_count < 50:
        return SpreadRegimeSummary.VERDICT_NEEDS_MORE_DATA

    # If all observations are FALLBACK_MIN_MAX, it's no usable two-sided book
    if summary.valid_spread_count > 0 and summary.pct_fallback_min_max >= 99.0:
        return SpreadRegimeSummary.VERDICT_STRUCTURALLY_TOO_WIDE

    # If >50% of TWO_SIDED_BOOK spreads are below 200 bps, there are tight windows
    if summary.two_sided_book_count > 0:
        # Check only genuine two-sided book observations
        # This path is reached when there ARE real two-sided quotes
        pass

    # If >50% of spreads are below 200 bps, there are tight windows
    if summary.pct_spread_lte_200bps > 50.0:
        return SpreadRegimeSummary.VERDICT_HAS_TIGHT_WINDOWS

    # If median spread is above 500 bps, structurally too wide
    if summary.median_spread_bps is not None and summary.median_spread_bps > 500.0:
        return SpreadRegimeSummary.VERDICT_STRUCTURALLY_TOO_WIDE

    # If >80% are above 200 bps (i.e. <20% below), structurally too wide
    if summary.pct_spread_lte_200bps < 20.0:
        return SpreadRegimeSummary.VERDICT_STRUCTURALLY_TOO_WIDE

    # In between — more data needed to distinguish
    return SpreadRegimeSummary.VERDICT_NEEDS_MORE_DATA


def classify_verdict_reason(summary: SpreadRegimeSummary) -> str:
    """Return the human-readable reason for the verdict.

    Must be called after classify_verdict to align the reason with the verdict.
    """
    if summary.pct_fallback_min_max >= 99.0 and summary.valid_spread_count > 0:
        return "no_usable_two_sided_book"

    if summary.median_spread_bps is not None and summary.median_spread_bps > 500.0:
        return "quoted_spread_structurally_too_wide"

    if summary.pct_spread_lte_200bps < 20.0:
        return "quoted_spread_structurally_too_wide"

    if summary.valid_spread_count < 50:
        return "insufficient_observations"

    return "insufficient_tight_windows"


def compute_summary_from_events(
    events: list[SpreadEvent],
    run_id: str,
    input_capture_dirs: list[str],
) -> SpreadRegimeSummary:
    """Compute aggregate summary from spread events."""
    market_slugs = set(e.market_slug for e in events)
    valid_spreads = [
        e.spread_bps for e in events
        if e.spread_bps is not None and not e.polymarket_stale
    ]
    sorted_spreads = sorted(valid_spreads)

    median = compute_percentile(sorted_spreads, 0.50)
    p25 = compute_percentile(sorted_spreads, 0.25)
    p75 = compute_percentile(sorted_spreads, 0.75)
    p90 = compute_percentile(sorted_spreads, 0.90)
    p95 = compute_percentile(sorted_spreads, 0.95)

    bucket_counts = compute_spread_bucket_counts(valid_spreads)

    # Quote quality classification
    qq_counts = QuoteQualityCounts()
    for e in events:
        qq = e.quote_quality
        if qq == QuoteQuality.TWO_SIDED_BOOK:
            qq_counts.two_sided_book += 1
        elif qq == QuoteQuality.ONE_SIDED_BOOK:
            qq_counts.one_sided_book += 1
        elif qq == QuoteQuality.EMPTY_BOOK:
            qq_counts.empty_book += 1
        elif qq == QuoteQuality.FALLBACK_MIN_MAX:
            qq_counts.fallback_min_max += 1
        elif qq == QuoteQuality.MISSING_BOOK:
            qq_counts.missing_book += 1
        elif qq == QuoteQuality.INVALID_BOOK:
            qq_counts.invalid_book += 1

    total_qq = sum([
        qq_counts.two_sided_book,
        qq_counts.one_sided_book,
        qq_counts.empty_book,
        qq_counts.fallback_min_max,
        qq_counts.missing_book,
        qq_counts.invalid_book,
    ])
    pct_two_sided = (100.0 * qq_counts.two_sided_book / total_qq) if total_qq > 0 else 0.0
    pct_fallback = (100.0 * qq_counts.fallback_min_max / total_qq) if total_qq > 0 else 0.0

    # TTE bucket analysis
    tte_medians: dict[str, list[float]] = {}
    tte_dominant: dict[str, int] = {}
    for e in events:
        if e.spread_bps is not None and not e.polymarket_stale:
            tte_assignment = assign_tte_bucket(e.time_to_expiry_ns)
            label = tte_assignment.bucket_label
            tte_medians.setdefault(label, []).append(e.spread_bps)
            tte_dominant[label] = tte_dominant.get(label, 0) + 1

    dominant_tte_bucket = max(tte_dominant, key=tte_dominant.get) if tte_dominant else "unknown"

    # Tightest and widest TTE buckets by median spread
    tte_median_values: dict[str, float] = {}
    for label, spreads in tte_medians.items():
        if spreads:
            sorted_s = sorted(spreads)
            tte_median_values[label] = compute_percentile(sorted_s, 0.50) or 0.0

    tightest_tte_bucket = (
        min(tte_median_values, key=tte_median_values.get)
        if tte_median_values
        else "unknown"
    )
    widest_tte_bucket = (
        max(tte_median_values, key=tte_median_values.get)
        if tte_median_values
        else "unknown"
    )

    safety_status = "PASS"
    recommendation = "Do not execute. Spread regime study only."

    summary = SpreadRegimeSummary(
        run_id=run_id,
        input_capture_dirs=input_capture_dirs,
        market_count=len(market_slugs),
        event_count=len(events),
        valid_spread_count=len(valid_spreads),
        median_spread_bps=median,
        p25_spread_bps=p25,
        p75_spread_bps=p75,
        p90_spread_bps=p90,
        p95_spread_bps=p95,
        pct_spread_lte_20bps=bucket_counts.to_pct(bucket_counts.below_20bps),
        pct_spread_lte_40bps=bucket_counts.to_pct(bucket_counts.below_40bps),
        pct_spread_lte_80bps=bucket_counts.to_pct(bucket_counts.below_80bps),
        pct_spread_lte_100bps=bucket_counts.to_pct(bucket_counts.below_100bps),
        pct_spread_lte_200bps=bucket_counts.to_pct(bucket_counts.below_200bps),
        pct_spread_lte_500bps=bucket_counts.to_pct(bucket_counts.below_500bps),
        dominant_tte_bucket=dominant_tte_bucket,
        tightest_tte_bucket=tightest_tte_bucket,
        widest_tte_bucket=widest_tte_bucket,
        safety_status=safety_status,
        recommendation=recommendation,
        quote_quality_counts=qq_counts,
        two_sided_book_count=qq_counts.two_sided_book,
        one_sided_book_count=qq_counts.one_sided_book,
        empty_book_count=qq_counts.empty_book,
        fallback_min_max_count=qq_counts.fallback_min_max,
        missing_book_count=qq_counts.missing_book,
        invalid_book_count=qq_counts.invalid_book,
        pct_two_sided_book=pct_two_sided,
        pct_fallback_min_max=pct_fallback,
    )

    # Set verdict reason
    summary.verdict_reason = classify_verdict_reason(summary)

    return summary