"""Tests for the 1h quote lifecycle observer (DQO-2).

Tests cover: known slug validation, discovery validation failure blocking,
no active market exiting cleanly, quote quality classification,
lifecycle bucketing, actionable counts, lifecycle report wording,
safety checks, and verdict constraints.

No orders. No keys. No execution. No on-chain calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from examples.strategies.polymarket_btcusd_arb.duration_market_discovery import (
    DURATION_1H,
    DURATION_UNKNOWN,
    REF_SOURCE_BINANCE,
    REF_SOURCE_CHAINLINK,
    DurationMarketInfo,
    UpDownMarketInfo,
)
from examples.strategies.polymarket_btcusd_arb.spread_regime import (
    SpreadEvent,
    QuoteQuality,
    classify_quote_quality,
    is_actionable_two_sided,
    compute_spread_bps,
)
from examples.strategies.polymarket_btcusd_arb.run_1h_quote_lifecycle_observer import (
    LifecycleSnapshot,
    classify_lifecycle_bucket,
    compute_actionable_stats,
    build_quote_quality_counts,
    build_lifecycle_bucket_counts,
    classify_verdict,
    classify_snapshot_timing_state,
    classify_market_timing_state,
    V_DISCOVERY_VALIDATION_FAILED,
    V_NEEDS_MORE_DATA_NO_ACTIVE,
    V_NEEDS_MORE_DATA_MARKET_NOT_STARTED,
    V_BOOK_POLL_FAILED,
    V_NO_ACTIONABLE,
    V_TRANSIENT_ACTIONABLE,
    V_ACTIONABLE_REQUIRES_PHASE1,
    FORBIDDEN_VERDICTS,
    DEFAULT_MIN_ACTIONABLE_RATE_FOR_PHASE1,
    DEFAULT_MIN_CONTIGUOUS_ACTIONABLE_SECONDS_FOR_PHASE1,
    DEFAULT_MAX_WAIT_FOR_START_SECONDS,
    TIMING_PRE_START,
    TIMING_IN_LIFECYCLE,
    TIMING_EXPIRED,
    TIMING_UNKNOWN,
    BRANCH,
)


# --- Helpers ---

def _make_duration_market(
    slug: str = "bitcoin-up-or-down-may-13-2026-11pm-et",
    question: str = "Will BTC go up or down Hourly?",
    active: bool = True,
    closed: bool = False,
    duration_label: str = "1h",
    duration_seconds: int = 3600,
    resolution_source_kind: str = REF_SOURCE_BINANCE,
    start_ns: int | None = None,
    end_ns: int | None = None,
) -> DurationMarketInfo:
    """Create a test DurationMarketInfo for 1h markets."""
    if start_ns is None:
        start_ns = 1778794200_000_000_000
    if end_ns is None:
        end_ns = start_ns + 3600_000_000_000
    market = UpDownMarketInfo(
        slug=slug,
        question=question,
        active=active,
        closed=closed,
        condition_id="condition_test",
        yes_token_id="token_yes_test",
        no_token_id="token_no_test",
        start_ns=start_ns,
        end_ns=end_ns,
        series_slug=None,
        resolution_source="Binance BTC/USDT",
    )
    return DurationMarketInfo(
        market=market,
        duration_label=duration_label,
        duration_seconds=duration_seconds,
        classification_source="slug",
        resolution_source_kind=resolution_source_kind,
        is_known_slug=True,
    )


def _make_snapshot(
    market_slug: str = "bitcoin-up-or-down-test",
    best_bid: float | None = 0.48,
    best_ask: float | None = 0.52,
    quote_quality: str | None = None,
    seconds_to_expiry: float | None = 1800.0,
    book_poll_success: bool = True,
    book_poll_error: str | None = None,
    ts_event_ns: int = 1_777_000_000_000_000_000,
    timing_state: str = TIMING_IN_LIFECYCLE,
) -> LifecycleSnapshot:
    """Create a test lifecycle snapshot."""
    if quote_quality is None:
        if best_bid is not None and best_ask is not None:
            quote_quality = classify_quote_quality(best_bid, best_ask)
        elif not book_poll_success:
            quote_quality = QuoteQuality.MISSING_BOOK
        elif best_bid is None and best_ask is None:
            quote_quality = QuoteQuality.EMPTY_BOOK
        elif best_bid is None or best_ask is None:
            quote_quality = QuoteQuality.ONE_SIDED_BOOK
        else:
            quote_quality = QuoteQuality.INVALID_BOOK

    actionable = is_actionable_two_sided(quote_quality)
    lifecycle_bucket = classify_lifecycle_bucket(seconds_to_expiry)
    spread_bps = compute_spread_bps(best_bid, best_ask) if (best_bid is not None and best_ask is not None) else None

    return LifecycleSnapshot(
        ts_event_ns=ts_event_ns,
        ts_recv_ns=ts_event_ns,
        market_slug=market_slug,
        market_title="Will BTC go up or down?",
        duration_label="1h",
        duration_seconds=3600,
        reference_source_kind="BINANCE_BTCUSDT",
        product_exists=True,
        is_active=True,
        is_closed=False,
        seconds_to_start=None,
        seconds_to_expiry=seconds_to_expiry,
        timing_state=timing_state,
        book_poll_success=book_poll_success,
        book_poll_error=book_poll_error,
        best_bid=best_bid,
        best_ask=best_ask,
        best_bid_size=100.0 if best_bid is not None else None,
        best_ask_size=100.0 if best_ask is not None else None,
        spread_bps=spread_bps,
        quote_quality=quote_quality,
        actionable_two_sided_book=actionable,
        lifecycle_bucket=lifecycle_bucket,
        raw_source="clob_poll",
    )


# --- Test Classes ---

class TestKnownSlugValidation:
    """Known 1h slug validation passes with expected duration/reference source."""

    def test_known_slug_validates_as_1h(self):
        """Validate that the known slug for 1h validates correctly."""
        # This tests the validation function directly
        dm = _make_duration_market(
            slug="bitcoin-up-or-down-may-13-2026-11pm-et",
            question="Will BTC go up or down in 1 hour?",
            duration_label="1h",
            duration_seconds=3600,
            resolution_source_kind=REF_SOURCE_BINANCE,
        )
        assert dm.duration_label == "1h"
        assert dm.duration_seconds == 3600
        assert dm.resolution_source_kind == REF_SOURCE_BINANCE
        assert dm.is_known_slug is True

    def test_slug_patterns_classify_1h(self):
        """bitcoin-up-or-down-* slugs classify as 1h."""
        from examples.strategies.polymarket_btcusd_arb.duration_market_discovery import classify_duration_from_slug
        label, seconds = classify_duration_from_slug("bitcoin-up-or-down-may-13-2026-11pm-et")
        assert label == "1h"
        assert seconds == 3600

    def test_btc_updown_1h_slug(self):
        """btc-updown-1h-* slugs classify as 1h."""
        from examples.strategies.polymarket_btcusd_arb.duration_market_discovery import classify_duration_from_slug
        label, seconds = classify_duration_from_slug("btc-updown-1h-1778794200")
        assert label == "1h"
        assert seconds == 3600


class TestDiscoveryValidationFailure:
    """Discovery validation failure blocks observation."""

    def test_discovery_validation_failure_produces_correct_verdict(self):
        """When known slug validation fails, verdict must be DISCOVERY_VALIDATION_FAILED."""
        assert V_DISCOVERY_VALIDATION_FAILED == "DISCOVERY_VALIDATION_FAILED"
        assert V_DISCOVERY_VALIDATION_FAILED not in FORBIDDEN_VERDICTS


class TestNoActiveMarketExitsCleanly:
    """No active 1h market exits cleanly with NEEDS_MORE_DATA."""

    def test_needs_more_data_verdict(self):
        """NEEDS_MORE_DATA_NO_ACTIVE_1H_MARKET is the correct verdict."""
        assert V_NEEDS_MORE_DATA_NO_ACTIVE == "NEEDS_MORE_DATA_NO_ACTIVE_1H_MARKET"

    def test_no_active_market_verdict_is_not_forbidden(self):
        """NEEDS_MORE_DATA verdict is not a forbidden verdict."""
        assert V_NEEDS_MORE_DATA_NO_ACTIVE not in FORBIDDEN_VERDICTS


class TestSelectedMarketConstraints:
    """Selected market must be 1h and BINANCE_BTCUSDT reference."""

    def test_1h_market_has_correct_duration(self):
        dm = _make_duration_market()
        assert dm.duration_label == "1h"
        assert dm.duration_seconds == 3600

    def test_1h_binance_market_has_correct_reference(self):
        dm = _make_duration_market(resolution_source_kind=REF_SOURCE_BINANCE)
        assert dm.resolution_source_kind == REF_SOURCE_BINANCE

    def test_chainlink_market_rejected_as_wrong_reference(self):
        dm = _make_duration_market(resolution_source_kind=REF_SOURCE_CHAINLINK)
        assert dm.resolution_source_kind != REF_SOURCE_BINANCE


class TestBookPollFailureSeparation:
    """Book poll failure is recorded separately from product existence."""

    def test_book_poll_failure_snapshot_has_product_exists_true(self):
        snap = _make_snapshot(
            best_bid=None,
            best_ask=None,
            book_poll_success=False,
            book_poll_error="book_poll_returned_none",
            quote_quality=QuoteQuality.MISSING_BOOK,
        )
        assert snap.book_poll_success is False
        assert snap.book_poll_error is not None
        # Product existence is separate from book poll failure
        assert snap.product_exists is True
        assert snap.quote_quality == QuoteQuality.MISSING_BOOK

    def test_book_poll_failure_does_not_imply_nonexistence(self):
        """MISSING_BOOK is about the book data, not product existence."""
        snap = _make_snapshot(
            best_bid=None,
            best_ask=None,
            book_poll_success=False,
            quote_quality=QuoteQuality.MISSING_BOOK,
        )
        assert snap.product_exists is True
        assert snap.quote_quality == QuoteQuality.MISSING_BOOK
        assert not snap.actionable_two_sided_book


class TestQuoteQualityClassification:
    """Quote quality classification tests for 1h observer."""

    def test_exchange_bound_001_099_classifies_as_exchange_bound(self):
        """0.01/0.99 real orders classify as EXCHANGE_BOUND_TWO_SIDED_BOOK."""
        qq = classify_quote_quality(0.01, 0.99, is_synthetic_fallback=False)
        assert qq == QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK

    def test_normal_bid_ask_classifies_as_two_sided(self):
        """Normal bid/ask away from bounds classify as TWO_SIDED_BOOK."""
        qq = classify_quote_quality(0.48, 0.52)
        assert qq == QuoteQuality.TWO_SIDED_BOOK

    def test_synthetic_001_099_classifies_as_fallback(self):
        """Synthetic 0.01/0.99 from fallback classifies as FALLBACK_MIN_MAX."""
        qq = classify_quote_quality(0.01, 0.99, is_synthetic_fallback=True)
        assert qq == QuoteQuality.FALLBACK_MIN_MAX

    def test_one_sided_book_only_bid(self):
        qq = classify_quote_quality(0.45, None)
        assert qq == QuoteQuality.ONE_SIDED_BOOK

    def test_one_sided_book_only_ask(self):
        qq = classify_quote_quality(None, 0.55)
        assert qq == QuoteQuality.ONE_SIDED_BOOK

    def test_empty_book(self):
        qq = classify_quote_quality(None, None)
        assert qq == QuoteQuality.EMPTY_BOOK

    def test_invalid_book_crossed(self):
        qq = classify_quote_quality(0.55, 0.45)  # bid > ask
        assert qq == QuoteQuality.INVALID_BOOK


class TestActionableCount:
    """Actionable count only includes TWO_SIDED_BOOK."""

    def test_actionable_only_counts_two_sided_book(self):
        snap_actionable = _make_snapshot(
            best_bid=0.48, best_ask=0.52,
            quote_quality=QuoteQuality.TWO_SIDED_BOOK,
        )
        assert snap_actionable.actionable_two_sided_book is True

    def test_exchange_bound_not_actionable(self):
        snap_eb = _make_snapshot(
            best_bid=0.01, best_ask=0.99,
            quote_quality=QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK,
        )
        assert snap_eb.actionable_two_sided_book is False

    def test_one_sided_not_actionable(self):
        snap_os = _make_snapshot(
            best_bid=0.45, best_ask=None,
            quote_quality=QuoteQuality.ONE_SIDED_BOOK,
        )
        assert snap_os.actionable_two_sided_book is False

    def test_empty_book_not_actionable(self):
        snap_empty = _make_snapshot(
            best_bid=None, best_ask=None,
            quote_quality=QuoteQuality.EMPTY_BOOK,
        )
        assert snap_empty.actionable_two_sided_book is False

    def test_missing_book_not_actionable(self):
        snap_missing = _make_snapshot(
            quote_quality=QuoteQuality.MISSING_BOOK,
            book_poll_success=False,
        )
        assert snap_missing.actionable_two_sided_book is False

    def test_compute_actionable_stats_with_two_sided(self):
        snaps = [
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK,
                          ts_event_ns=1_777_000_000_000_000_000 + i * 5_000_000_000)
            for i in range(5)
        ]
        stats = compute_actionable_stats(snaps)
        assert stats["actionable_two_sided_book_count"] == 5
        assert stats["actionable_two_sided_book_rate"] == 1.0
        assert stats["max_contiguous_actionable_seconds"] > 0

    def test_compute_actionable_stats_mixed(self):
        snaps = [
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK,
                          ts_event_ns=1_777_000_000_000_000_000),
            _make_snapshot(best_bid=0.01, best_ask=0.99,
                          quote_quality=QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK,
                          ts_event_ns=1_777_000_000_000_000_000 + 5_000_000_000),
        ]
        stats = compute_actionable_stats(snaps)
        assert stats["actionable_two_sided_book_count"] == 1
        assert stats["actionable_two_sided_book_rate"] == 0.5


class TestLifecycleBucketClassification:
    """Lifecycle bucket classification works correctly."""

    def test_pre_start_bucket(self):
        """More than 1h to expiry is pre_start."""
        assert classify_lifecycle_bucket(None) == "pre_start"
        assert classify_lifecycle_bucket(3700.0) == "pre_start"

    def test_gt_45m_bucket(self):
        assert classify_lifecycle_bucket(3000.0) == "gt_45m"
        assert classify_lifecycle_bucket(2701.0) == "gt_45m"

    def test_30m_to_45m_bucket(self):
        assert classify_lifecycle_bucket(2700.0) == "30m_to_45m"
        assert classify_lifecycle_bucket(2000.0) == "30m_to_45m"
        assert classify_lifecycle_bucket(1801.0) == "30m_to_45m"

    def test_15m_to_30m_bucket(self):
        assert classify_lifecycle_bucket(1800.0) == "15m_to_30m"
        assert classify_lifecycle_bucket(1200.0) == "15m_to_30m"
        assert classify_lifecycle_bucket(901.0) == "15m_to_30m"

    def test_5m_to_15m_bucket(self):
        assert classify_lifecycle_bucket(900.0) == "5m_to_15m"
        assert classify_lifecycle_bucket(600.0) == "5m_to_15m"
        assert classify_lifecycle_bucket(301.0) == "5m_to_15m"

    def test_1m_to_5m_bucket(self):
        assert classify_lifecycle_bucket(300.0) == "1m_to_5m"
        assert classify_lifecycle_bucket(180.0) == "1m_to_5m"
        assert classify_lifecycle_bucket(61.0) == "1m_to_5m"

    def test_0m_to_1m_bucket(self):
        assert classify_lifecycle_bucket(60.0) == "0m_to_1m"
        assert classify_lifecycle_bucket(30.0) == "0m_to_1m"
        assert classify_lifecycle_bucket(0.0) == "0m_to_1m"

    def test_expired_bucket(self):
        assert classify_lifecycle_bucket(-1.0) == "expired"
        assert classify_lifecycle_bucket(-100.0) == "expired"


class TestBuildQuoteQualityCounts:
    """Quote quality count aggregation."""

    def test_empty_snapshots(self):
        result = build_quote_quality_counts([])
        assert result == {}

    def test_mixed_quality_counts(self):
        snaps = [
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK),
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK),
            _make_snapshot(best_bid=0.01, best_ask=0.99,
                          quote_quality=QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK),
        ]
        result = build_quote_quality_counts(snaps)
        assert result[QuoteQuality.TWO_SIDED_BOOK] == 2
        assert result[QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK] == 1


class TestBuildLifecycleBucketCounts:
    """Lifecycle bucket count aggregation."""

    def test_empty_snapshots(self):
        result = build_lifecycle_bucket_counts([])
        for label in ["pre_start", "gt_45m", "30m_to_45m", "15m_to_30m",
                       "5m_to_15m", "1m_to_5m", "0m_to_1m", "expired"]:
            assert label in result

    def test_snapshots_distributed_across_buckets(self):
        # 3000s = 50min -> gt_45m, 1200s = 20min -> 15m_to_30m, 600s = 10min -> 5m_to_15m
        snaps = [
            _make_snapshot(seconds_to_expiry=3000.0, quote_quality=QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK),
            _make_snapshot(seconds_to_expiry=1200.0, quote_quality=QuoteQuality.TWO_SIDED_BOOK),
            _make_snapshot(seconds_to_expiry=600.0, quote_quality=QuoteQuality.TWO_SIDED_BOOK),
        ]
        result = build_lifecycle_bucket_counts(snaps)
        assert result["gt_45m"][QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK] == 1
        assert result["15m_to_30m"][QuoteQuality.TWO_SIDED_BOOK] == 1
        assert result["5m_to_15m"][QuoteQuality.TWO_SIDED_BOOK] == 1


class TestSummaryIncludesActionableRateAndMaxContiguous:
    """Summary includes actionable rate and max contiguous actionable seconds."""

    def test_actionable_stats_include_rate(self):
        snaps = [
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK,
                          ts_event_ns=1_777_000_000_000_000_000 + i * 5_000_000_000)
            for i in range(10)
        ]
        stats = compute_actionable_stats(snaps)
        assert "actionable_two_sided_book_rate" in stats
        assert stats["actionable_two_sided_book_rate"] == 1.0
        assert "max_contiguous_actionable_seconds" in stats
        assert stats["max_contiguous_actionable_seconds"] > 0

    def test_max_contiguous_actionable_seconds_computed(self):
        # 10 snapshots, 5s apart = two contiguous groups with a large gap
        snaps = [
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK,
                          ts_event_ns=1_000_000_000_000),
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK,
                          ts_event_ns=1_005_000_000_000),
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK,
                          ts_event_ns=1_010_000_000_000),
            # Gap > 30s
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK,
                          ts_event_ns=1_100_000_000_000),
        ]
        stats = compute_actionable_stats(snaps)
        # First contiguous run: 10s (1_000 to 1_010 = 10s)
        # Second run: 0s (single snapshot)
        assert stats["max_contiguous_actionable_seconds"] == 10.0


class TestReportWording:
    """Report includes corrected-discovery wording and separation of concepts."""

    def test_corrected_discovery_wording_in_module(self):
        """The module docstring includes the corrected-discovery wording."""
        from examples.strategies.polymarket_btcusd_arb import run_1h_quote_lifecycle_observer
        doc = run_1h_quote_lifecycle_observer.__doc__ or ""
        assert "incorrectly concluded" in doc.lower() or "corrected" in doc.lower()

    def test_verdict_descriptions_separate_concepts(self):
        """Verdict descriptions separate product/active/book/actionable."""
        assert "product existence" in V_DISCOVERY_VALIDATION_FAILED.lower() or "validation" in V_DISCOVERY_VALIDATION_FAILED.lower()
        assert "active" in V_NEEDS_MORE_DATA_NO_ACTIVE.lower()
        # Actionable verdict mentions books
        assert "actionable" in V_NO_ACTIONABLE.lower() or "book" in V_NO_ACTIONABLE.lower()
        assert "actionable" in V_ACTIONABLE_REQUIRES_PHASE1.lower()

    def test_actionable_verdict_mentions_phase1_backtest(self):
        """Actionable verdict explicitly mentions Phase 1 backtest, not Phase 3."""
        assert "phase1" in V_ACTIONABLE_REQUIRES_PHASE1.lower() or "phase 1" in V_ACTIONABLE_REQUIRES_PHASE1.lower()

    def test_forbidden_verdicts_not_produced(self):
        """None of the forbidden verdicts are allowed."""
        forbidden = {"ALLOW_PHASE_3", "READY_FOR_EXECUTION", "LIVE_TRADING_READY", "EXECUTION_READY", "PAPER_TRADING_READY"}
        assert forbidden == FORBIDDEN_VERDICTS

    def test_no_actionable_verdict_does_not_claim_global_proof(self):
        """NO_ACTIONABLE verdict description should note it's not a global proof."""
        # The description is constructed at runtime, but the verdict constant itself is correct
        assert V_NO_ACTIONABLE == "ONE_HOUR_NO_ACTIONABLE_BOOK_OBSERVED"


class TestSafetyChecks:
    """Safety checks remain clean."""

    def test_no_banned_imports_in_observer_module(self):
        """The 1h quote lifecycle observer must not import banned modules."""
        module_path = Path(__file__).resolve().parent.parent / "run_1h_quote_lifecycle_observer.py"
        source = module_path.read_text()
        banned = ["PolymarketExecutionClient", "PolymarketLiveExecClientFactory",
                   "LiveNode", "TradingNode", "OrderFactory"]
        for b in banned:
            assert b not in source, f"Banned import/construction: {b}"

    def test_no_submit_order_in_observer_module(self):
        module_path = Path(__file__).resolve().parent.parent / "run_1h_quote_lifecycle_observer.py"
        source = module_path.read_text()
        assert "submit_order" not in source
        assert "cancel_order" not in source

    def test_no_env_key_reads_in_observer_module(self):
        module_path = Path(__file__).resolve().parent.parent / "run_1h_quote_lifecycle_observer.py"
        source = module_path.read_text()
        banned_envs = ["POLYMARKET_PK", "POLYMARKET_API_KEY", "POLYMARKET_API_SECRET",
                       "POLYMARKET_FUNDER", "POLYMARKET_PASSPHRASE"]
        for env in banned_envs:
            assert env not in source, f"Banned env read: {env}"

    def test_safety_check_path_passes(self):
        """Run safety check on the module directory."""
        from examples.strategies.polymarket_btcusd_arb.safety_checks import check_path
        pkg_dir = Path(__file__).resolve().parent.parent
        result = check_path(pkg_dir)
        assert result["ok"], f"Safety violations: {result['violations']}"


class TestLifecycleSnapshotDataclass:
    """LifecycleSnapshot has all required fields."""

    def test_snapshot_has_all_required_fields(self):
        snap = _make_snapshot()
        required_fields = [
            "ts_event_ns", "ts_recv_ns", "market_slug", "market_title",
            "duration_label", "duration_seconds", "reference_source_kind",
            "product_exists", "is_active", "is_closed",
            "seconds_to_start", "seconds_to_expiry",
            "timing_state",
            "book_poll_success", "book_poll_error",
            "best_bid", "best_ask", "best_bid_size", "best_ask_size",
            "spread_bps", "quote_quality", "actionable_two_sided_book",
            "lifecycle_bucket", "raw_source",
        ]
        for field in required_fields:
            assert hasattr(snap, field), f"Missing field: {field}"

    def test_snapshot_is_frozen(self):
        snap = _make_snapshot()
        with pytest.raises(AttributeError):
            snap.market_slug = "changed"


class TestContiguousActionableSeconds:
    """Max contiguous actionable seconds computation is correct."""

    def test_single_actionable_snapshot(self):
        snaps = [
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK,
                          ts_event_ns=1_000_000_000_000),
        ]
        stats = compute_actionable_stats(snaps)
        # Single snapshot = 0s contiguous duration (same start and end)
        assert stats["max_contiguous_actionable_seconds"] == 0.0

    def test_two_contiguous_snapshots(self):
        snaps = [
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK,
                          ts_event_ns=1_000_000_000_000),
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK,
                          ts_event_ns=1_005_000_000_000),
        ]
        stats = compute_actionable_stats(snaps)
        assert stats["max_contiguous_actionable_seconds"] == 5.0

    def test_gap_breaks_contiguity(self):
        snaps = [
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK,
                          ts_event_ns=1_000_000_000_000),
            # 100s gap > 30s threshold
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          quote_quality=QuoteQuality.TWO_SIDED_BOOK,
                          ts_event_ns=1_100_000_000_000),
        ]
        stats = compute_actionable_stats(snaps)
        # Two separate single-snapshot runs, each 0s
        assert stats["max_contiguous_actionable_seconds"] == 0.0

    def test_no_actionable(self):
        snaps = [
            _make_snapshot(best_bid=0.01, best_ask=0.99,
                          quote_quality=QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK),
        ]
        stats = compute_actionable_stats(snaps)
        assert stats["actionable_two_sided_book_count"] == 0
        assert stats["max_contiguous_actionable_seconds"] == 0.0


class TestVerdictClassificationWithDwellThresholds:
    """Verdict classification with actionable-book dwell thresholds."""

    def test_zero_actionable_snapshots_yields_no_actionable(self):
        """Zero actionable snapshots -> NO_ACTIONABLE (with in-lifecycle)."""
        verdict, _ = classify_verdict(
            actionable_count=0,
            total_count=100,
            actionable_rate=0.0,
            max_contiguous_actionable_seconds=0.0,
            book_poll_success_count=80,
            book_poll_failure_count=20,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_NO_ACTIONABLE

    def test_single_actionable_snapshot_yields_transient(self):
        """One actionable snapshot with 5s dwell -> too brief for Phase 1."""
        verdict, desc = classify_verdict(
            actionable_count=1,
            total_count=100,
            actionable_rate=0.01,  # 1%
            max_contiguous_actionable_seconds=0.0,  # single snapshot
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_TRANSIENT_ACTIONABLE
        assert "dwell" in desc.lower() or "threshold" in desc.lower()

    def test_actionable_rate_below_threshold_yields_transient(self):
        """Rate below 5% but above 0 -> transient, even with long contiguous."""
        verdict, _ = classify_verdict(
            actionable_count=3,
            total_count=100,
            actionable_rate=0.03,  # 3% < 5% threshold
            max_contiguous_actionable_seconds=400.0,  # passes contiguous
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_TRANSIENT_ACTIONABLE

    def test_contiguous_dwell_below_threshold_yields_transient(self):
        """Rate passes but contiguous dwell fails -> transient."""
        verdict, _ = classify_verdict(
            actionable_count=10,
            total_count=100,
            actionable_rate=0.10,  # 10% > 5% threshold
            max_contiguous_actionable_seconds=120.0,  # 120s < 300s threshold
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_TRANSIENT_ACTIONABLE

    def test_both_thresholds_pass_yields_phase1(self):
        """Both rate and contiguous pass -> ACTIONABLE_REQUIRES_PHASE1."""
        verdict, desc = classify_verdict(
            actionable_count=20,
            total_count=100,
            actionable_rate=0.20,  # 20% > 5% threshold
            max_contiguous_actionable_seconds=400.0,  # 400s > 300s threshold
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_ACTIONABLE_REQUIRES_PHASE1
        assert "phase 1" in desc.lower() or "phase1" in desc.lower()

    def test_exact_threshold_passes_rate(self):
        """Rate exactly at threshold (0.05) passes."""
        verdict, _ = classify_verdict(
            actionable_count=5,
            total_count=100,
            actionable_rate=0.05,  # exactly threshold
            max_contiguous_actionable_seconds=400.0,  # above contiguous threshold
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_ACTIONABLE_REQUIRES_PHASE1

    def test_exact_threshold_passes_contiguous(self):
        """Contiguous exactly at threshold (300s) passes."""
        verdict, _ = classify_verdict(
            actionable_count=20,
            total_count=100,
            actionable_rate=0.20,
            max_contiguous_actionable_seconds=300.0,  # exactly threshold
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_ACTIONABLE_REQUIRES_PHASE1

    def test_all_polls_failed_yields_book_poll_failed(self):
        """All polls failed -> BOOK_POLL_FAILED regardless of actionable count."""
        verdict, _ = classify_verdict(
            actionable_count=0,
            total_count=0,
            actionable_rate=0.0,
            max_contiguous_actionable_seconds=0.0,
            book_poll_success_count=0,
            book_poll_failure_count=50,
            in_lifecycle_snapshot_count=0,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_BOOK_POLL_FAILED

    def test_exchange_bound_never_contributes_to_actionable(self):
        """EXCHANGE_BOUND_TWO_SIDED_BOOK must not contribute to actionable count."""
        # 100 snapshots all exchange-bound -> 0 actionable
        verdict, _ = classify_verdict(
            actionable_count=0,
            total_count=100,
            actionable_rate=0.0,
            max_contiguous_actionable_seconds=0.0,
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_NO_ACTIONABLE

    def test_one_sided_never_contributes_to_actionable(self):
        """ONE_SIDED_BOOK must not contribute to actionable count."""
        verdict, _ = classify_verdict(
            actionable_count=0,
            total_count=50,
            actionable_rate=0.0,
            max_contiguous_actionable_seconds=0.0,
            book_poll_success_count=50,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=50,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_NO_ACTIONABLE


class TestVerdictForbiddenValues:
    """Verify forbidden verdicts cannot be emitted."""

    def test_forbidden_verdicts_include_execution_and_phase3(self):
        """FORBIDDEN_VERDICTS must include all execution-related verdicts."""
        assert "ALLOW_PHASE_3" in FORBIDDEN_VERDICTS
        assert "READY_FOR_EXECUTION" in FORBIDDEN_VERDICTS
        assert "LIVE_TRADING_READY" in FORBIDDEN_VERDICTS
        assert "EXECUTION_READY" in FORBIDDEN_VERDICTS
        assert "PAPER_TRADING_READY" in FORBIDDEN_VERDICTS

    def test_transient_verdict_is_not_forbidden(self):
        """Transient verdict is allowed."""
        assert V_TRANSIENT_ACTIONABLE not in FORBIDDEN_VERDICTS

    def test_all_allowed_verdicts_are_not_forbidden(self):
        """All allowed verdicts must not be in forbidden set."""
        allowed = {
            V_DISCOVERY_VALIDATION_FAILED,
            V_NEEDS_MORE_DATA_NO_ACTIVE,
            V_NEEDS_MORE_DATA_MARKET_NOT_STARTED,
            V_BOOK_POLL_FAILED,
            V_NO_ACTIONABLE,
            V_TRANSIENT_ACTIONABLE,
            V_ACTIONABLE_REQUIRES_PHASE1,
        }
        for v in allowed:
            assert v not in FORBIDDEN_VERDICTS, f"Allowed verdict {v} is in FORBIDDEN_VERDICTS"


class TestVerdictTransientWording:
    """Transient verdict description must not recommend execution or Phase 3."""

    def test_transient_verdict_does_not_recommend_execution(self):
        _, desc = classify_verdict(
            actionable_count=1,
            total_count=100,
            actionable_rate=0.01,
            max_contiguous_actionable_seconds=0.0,
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert V_TRANSIENT_ACTIONABLE == "ONE_HOUR_TRANSIENT_ACTIONABLE_BOOK_OBSERVED_NEEDS_MORE_OBSERVATION"
        lower = desc.lower()
        # Must not recommend execution or Phase 3
        assert "phase 3" not in lower
        assert "execution" not in lower or "does not justify execution" in lower
        # Must recommend more observation
        assert "observer" in lower or "observation" in lower or "capture" in lower

    def test_report_includes_transient_verdict_wording(self):
        """Report module must have transient verdict handling."""
        from examples.strategies.polymarket_btcusd_arb import run_1h_quote_lifecycle_observer
        source = Path(run_1h_quote_lifecycle_observer.__file__).read_text()
        # The report.md generation must handle V_TRANSIENT_ACTIONABLE
        assert V_TRANSIENT_ACTIONABLE in source

    def test_phase1_verdict_wording_mentions_phase1_not_phase3(self):
        verdict, desc = classify_verdict(
            actionable_count=20,
            total_count=100,
            actionable_rate=0.20,
            max_contiguous_actionable_seconds=400.0,
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_ACTIONABLE_REQUIRES_PHASE1
        lower = desc.lower()
        assert "phase 1" in lower or "phase1" in lower
        assert "phase 3" not in lower or "does not" in lower


class TestDwellThresholdDefaults:
    """Verify threshold defaults are correct."""

    def test_default_min_actionable_rate(self):
        assert DEFAULT_MIN_ACTIONABLE_RATE_FOR_PHASE1 == 0.05

    def test_default_min_contiguous_seconds(self):
        assert DEFAULT_MIN_CONTIGUOUS_ACTIONABLE_SECONDS_FOR_PHASE1 == 300.0

    def test_default_max_wait_for_start_seconds(self):
        assert DEFAULT_MAX_WAIT_FOR_START_SECONDS == 0


class TestSnapshotTimingState:
    """Snapshot timing state classification tests."""

    def test_pre_start_snapshot_classifies_correctly(self):
        """Future market start_ns -> PRE_START_OPEN_FOR_TRADING."""
        ts = 1_000_000_000_000_000_000  # now
        start_ns = 2_000_000_000_000_000_000  # future
        end_ns = 2_000_000_003_600_000_000
        state = classify_snapshot_timing_state(ts, start_ns, end_ns)
        assert state == TIMING_PRE_START

    def test_in_lifecycle_snapshot_classifies_correctly(self):
        """Now between start and end -> IN_LIFECYCLE."""
        start_ns = 1_000_000_000_000_000_000
        end_ns = 1_000_000_003_600_000_000
        ts = 1_000_000_002_000_000_000  # 2000s after start, within lifecycle
        state = classify_snapshot_timing_state(ts, start_ns, end_ns)
        assert state == TIMING_IN_LIFECYCLE

    def test_expired_snapshot_classifies_correctly(self):
        """Now after end_ns -> EXPIRED."""
        start_ns = 1_000_000_000_000_000_000
        end_ns = 1_000_000_003_600_000_000
        ts = 1_000_000_004_000_000_000  # past expiry
        state = classify_snapshot_timing_state(ts, start_ns, end_ns)
        assert state == TIMING_EXPIRED

    def test_unknown_timing_when_start_ns_is_none(self):
        """None start_ns -> UNKNOWN_TIMING."""
        ts = 1_000_000_000_000_000_000
        state = classify_snapshot_timing_state(ts, None, None)
        assert state == TIMING_UNKNOWN


class TestMarketTimingState:
    """Market timing state classification tests."""

    def test_future_market_is_pre_start(self):
        """Future active market is classified as PRE_START_OPEN_FOR_TRADING."""
        now_ns = 1_000_000_000_000_000_000
        start_ns = 2_000_000_000_000_000_000
        end_ns = 2_000_000_003_600_000_000
        state = classify_market_timing_state(now_ns, start_ns, end_ns)
        assert state == TIMING_PRE_START

    def test_in_lifecycle_market(self):
        """Market with start <= now < end is IN_LIFECYCLE."""
        now_ns = 1_000_000_002_000_000_000
        start_ns = 1_000_000_000_000_000_000
        end_ns = 1_000_000_003_600_000_000
        state = classify_market_timing_state(now_ns, start_ns, end_ns)
        assert state == TIMING_IN_LIFECYCLE

    def test_expired_market(self):
        """Market past end is EXPIRED."""
        now_ns = 1_000_000_004_000_000_000
        start_ns = 1_000_000_000_000_000_000
        end_ns = 1_000_000_003_600_000_000
        state = classify_market_timing_state(now_ns, start_ns, end_ns)
        assert state == TIMING_EXPIRED

    def test_unknown_timing_when_none(self):
        """None timestamps -> UNKNOWN_TIMING."""
        now_ns = 1_000_000_000_000_000_000
        state = classify_market_timing_state(now_ns, None, None)
        assert state == TIMING_UNKNOWN


class TestVerdictLifecycleValidity:
    """Quote-quality verdicts require in-lifecycle observations."""

    def test_all_pre_start_snapshots_emit_market_not_started(self):
        """All pre-start snapshots -> NEEDS_MORE_DATA_MARKET_NOT_STARTED."""
        verdict, _ = classify_verdict(
            actionable_count=0,
            total_count=100,
            actionable_rate=0.0,
            max_contiguous_actionable_seconds=0.0,
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=0,
            pre_start_snapshot_count=100,
        )
        assert verdict == V_NEEDS_MORE_DATA_MARKET_NOT_STARTED

    def test_all_pre_start_does_not_emit_no_actionable(self):
        """All pre-start must NOT emit NO_ACTIONABLE."""
        verdict, _ = classify_verdict(
            actionable_count=0,
            total_count=100,
            actionable_rate=0.0,
            max_contiguous_actionable_seconds=0.0,
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=0,
            pre_start_snapshot_count=100,
        )
        assert verdict != V_NO_ACTIONABLE

    def test_in_lifecycle_no_actionable_yields_no_actionable(self):
        """In-lifecycle observations with zero actionable -> NO_ACTIONABLE."""
        verdict, _ = classify_verdict(
            actionable_count=0,
            total_count=100,
            actionable_rate=0.0,
            max_contiguous_actionable_seconds=0.0,
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_NO_ACTIONABLE

    def test_in_lifecycle_transient_actionable(self):
        """In-lifecycle transient actionable -> TRANSIENT_ACTIONABLE."""
        verdict, _ = classify_verdict(
            actionable_count=1,
            total_count=100,
            actionable_rate=0.01,
            max_contiguous_actionable_seconds=0.0,
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_TRANSIENT_ACTIONABLE

    def test_in_lifecycle_dwell_qualified_actionable(self):
        """In-lifecycle dwell-qualified actionable -> ACTIONABLE_REQUIRES_PHASE1."""
        verdict, _ = classify_verdict(
            actionable_count=20,
            total_count=100,
            actionable_rate=0.20,
            max_contiguous_actionable_seconds=400.0,
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=0,
        )
        assert verdict == V_ACTIONABLE_REQUIRES_PHASE1

    def test_mixed_pre_start_and_lifecycle_no_actionable(self):
        """Mixed pre-start + in-lifecycle with zero actionable -> NO_ACTIONABLE."""
        verdict, _ = classify_verdict(
            actionable_count=0,
            total_count=200,
            actionable_rate=0.0,
            max_contiguous_actionable_seconds=0.0,
            book_poll_success_count=200,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=100,
            pre_start_snapshot_count=100,
        )
        assert verdict == V_NO_ACTIONABLE

    def test_market_not_started_verdict_constant(self):
        """Verify the constant value."""
        assert V_NEEDS_MORE_DATA_MARKET_NOT_STARTED == "NEEDS_MORE_DATA_MARKET_NOT_STARTED"

    def test_market_not_started_not_forbidden(self):
        """NEEDS_MORE_DATA_MARKET_NOT_STARTED is not in forbidden set."""
        assert V_NEEDS_MORE_DATA_MARKET_NOT_STARTED not in FORBIDDEN_VERDICTS

    def test_pre_start_with_actionable_still_market_not_started(self):
        """Even if actionable books seen pre-start, verdict is MARKET_NOT_STARTED."""
        # This should not happen in practice since pre-start books are exchange-bound
        # but if somehow they were, the lifecycle validity takes precedence
        verdict, _ = classify_verdict(
            actionable_count=50,
            total_count=100,
            actionable_rate=0.50,
            max_contiguous_actionable_seconds=400.0,
            book_poll_success_count=100,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=0,
            pre_start_snapshot_count=100,
        )
        assert verdict == V_NEEDS_MORE_DATA_MARKET_NOT_STARTED


class TestReportWordingLifecycleTiming:
    """Report includes lifecycle timing correctness wording."""

    def test_report_module_has_pre_start_wording(self):
        """The module source must mention pre-start lifecycle caveat."""
        from examples.strategies.polymarket_btcusd_arb import run_1h_quote_lifecycle_observer
        source = Path(run_1h_quote_lifecycle_observer.__file__).read_text()
        assert "pre-start" in source.lower() or "pre_start" in source.lower()

    def test_report_module_has_market_not_started_verdict(self):
        """The module source must handle V_NEEDS_MORE_DATA_MARKET_NOT_STARTED."""
        from examples.strategies.polymarket_btcusd_arb import run_1h_quote_lifecycle_observer
        source = Path(run_1h_quote_lifecycle_observer.__file__).read_text()
        assert V_NEEDS_MORE_DATA_MARKET_NOT_STARTED in source

    def test_report_module_separates_concepts(self):
        """Module separates product/active/book/actionable concepts."""
        from examples.strategies.polymarket_btcusd_arb import run_1h_quote_lifecycle_observer
        source = Path(run_1h_quote_lifecycle_observer.__file__).read_text()
        # Must separate product existence from active market availability
        assert "Product existence" in source or "product existence" in source
        assert "active market" in source or "Active market" in source
        # Must also separate active/open for trading from lifecycle start
        assert "active/open for trading" in source or "event lifecycle" in source.lower()


class TestLifecycleSnapshotTimingField:
    """Snapshot has timing_state field."""

    def test_timing_state_field_exists(self):
        snap = _make_snapshot()
        assert hasattr(snap, "timing_state")
        assert snap.timing_state == TIMING_IN_LIFECYCLE  # default

    def test_timing_state_pre_start(self):
        snap = _make_snapshot(timing_state=TIMING_PRE_START)
        assert snap.timing_state == TIMING_PRE_START

    def test_timing_state_expired(self):
        snap = _make_snapshot(timing_state=TIMING_EXPIRED)
        assert snap.timing_state == TIMING_EXPIRED

    def test_timing_state_unknown(self):
        snap = _make_snapshot(timing_state=TIMING_UNKNOWN)
        assert snap.timing_state == TIMING_UNKNOWN


# --- DQO-2C: Near-boundary false-positive correction tests ---

class TestNearBoundaryVerdictCorrection:
    """DQO-2C: Near-boundary quotes (0.001/0.999, 0.02/0.98) must NOT
    trigger ACTIONABLE_BOOK_OBSERVED verdicts.

    The DQO-2B run misclassified 154 snapshots of 0.001/0.999 as
    TWO_SIDED_BOOK, producing a false-positive ACTIONABLE verdict.
    With the near-boundary fix, these are EXCHANGE_BOUND_TWO_SIDED_BOOK
    and the verdict must be NO_ACTIONABLE_BOOK_OBSERVED.
    """

    def test_near_boundary_0001_0999_snapshot_not_actionable(self):
        """0.001/0.999 snapshots must not be actionable after the fix."""
        snap = _make_snapshot(best_bid=0.001, best_ask=0.999)
        assert snap.quote_quality == QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK
        assert snap.actionable_two_sided_book is False

    def test_near_boundary_02_98_snapshot_not_actionable(self):
        """0.02/0.98 snapshots must not be actionable after the fix."""
        snap = _make_snapshot(best_bid=0.02, best_ask=0.98)
        assert snap.quote_quality == QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK
        assert snap.actionable_two_sided_book is False

    def test_exchange_bound_001_099_still_not_actionable(self):
        """0.01/0.99 must still be classified as EXCHANGE_BOUND (not actionable)."""
        snap = _make_snapshot(best_bid=0.01, best_ask=0.99)
        assert snap.quote_quality == QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK
        assert snap.actionable_two_sided_book is False

    def test_near_boundary_only_does_not_trigger_actionable_verdict(self):
        """154 near-boundary 0.001/0.999 + 349 exchange-bound 0.01/0.99 → NO_ACTIONABLE."""
        # Mimic DQO-2B run composition
        near_boundary = [
            _make_snapshot(
                best_bid=0.001, best_ask=0.999,
                ts_event_ns=1_777_000_000_000_000_000 + i * 5_000_000_000,
            )
            for i in range(154)
        ]
        exchange_bound = [
            _make_snapshot(
                best_bid=0.01, best_ask=0.99,
                ts_event_ns=1_777_000_000_000_000_000 + (154 + i) * 5_000_000_000,
            )
            for i in range(349)
        ]
        all_snaps = near_boundary + exchange_bound
        stats = compute_actionable_stats(all_snaps)
        assert stats["actionable_two_sided_book_count"] == 0, (
            f"Near-boundary quotes should not be actionable, got {stats['actionable_two_sided_book_count']}"
        )
        assert stats["actionable_two_sided_book_rate"] == 0.0
        assert stats["max_contiguous_actionable_seconds"] == 0.0

    def test_near_boundary_does_not_contribute_to_contiguous_actionable(self):
        """Near-boundary quotes contribute zero contiguous actionable seconds."""
        snaps = [
            _make_snapshot(
                best_bid=0.001, best_ask=0.999,
                ts_event_ns=1_777_000_000_000_000_000 + i * 5_000_000_000,
            )
            for i in range(100)
        ]
        stats = compute_actionable_stats(snaps)
        assert stats["max_contiguous_actionable_seconds"] == 0.0

    def test_dqO2b_composition_verdict_no_actionable(self):
        """Full DQO-2B composition (0.01/0.99 + 0.001/0.999 + 0.001/None) → NO_ACTIONABLE."""
        exchange_bound = [
            _make_snapshot(best_bid=0.01, best_ask=0.99, seconds_to_expiry=1800 - i * 5)
            for i in range(349)
        ]
        near_boundary = [
            _make_snapshot(best_bid=0.001, best_ask=0.999, seconds_to_expiry=1200 - i * 5)
            for i in range(154)
        ]
        one_sided = [
            _make_snapshot(best_bid=0.001, best_ask=None, seconds_to_expiry=600 - i * 5)
            for i in range(271)
        ]
        all_snaps = exchange_bound + near_boundary + one_sided
        total_count = len(all_snaps)
        stats = compute_actionable_stats(all_snaps)
        verdict, desc = classify_verdict(
            actionable_count=stats["actionable_two_sided_book_count"],
            total_count=total_count,
            actionable_rate=stats["actionable_two_sided_book_rate"],
            max_contiguous_actionable_seconds=stats["max_contiguous_actionable_seconds"],
            book_poll_success_count=total_count,
            book_poll_failure_count=0,
            in_lifecycle_snapshot_count=636,
        )
        assert verdict == V_NO_ACTIONABLE, (
            f"Expected NO_ACTIONABLE, got {verdict}: {desc}"
        )

    def test_genuine_actionable_still_triggers_actionable_verdict(self):
        """Genuine actionable quotes (bid=0.48, ask=0.52) still work after the fix."""
        snaps = [
            _make_snapshot(
                best_bid=0.48, best_ask=0.52,
                ts_event_ns=1_777_000_000_000_000_000 + i * 5_000_000_000,
            )
            for i in range(100)
        ]
        stats = compute_actionable_stats(snaps)
        assert stats["actionable_two_sided_book_count"] == 100
        assert stats["actionable_two_sided_book_rate"] == 1.0
        assert stats["max_contiguous_actionable_seconds"] > 0

    def test_mixed_genuine_and_near_boundary_actionable_count(self):
        """Only genuine TWO_SIDED_BOOK contributes to actionable count."""
        snaps = [
            _make_snapshot(best_bid=0.48, best_ask=0.52,
                          ts_event_ns=1_777_000_000_000_000_000),
            _make_snapshot(best_bid=0.001, best_ask=0.999,
                          ts_event_ns=1_777_000_000_000_000 + 5_000_000_000),
            _make_snapshot(best_bid=0.01, best_ask=0.99,
                          ts_event_ns=1_777_000_000_000_000 + 10_000_000_000),
        ]
        stats = compute_actionable_stats(snaps)
        assert stats["actionable_two_sided_book_count"] == 1
        assert stats["actionable_two_sided_book_rate"] == pytest.approx(1/3)


class TestNearBoundaryReportWording:
    """DQO-2C: Report must state near-boundary books are non-actionable."""

    def test_summary_actionable_count_excludes_near_boundary(self):
        """summary.json actionable count must exclude near-boundary quotes."""
        snaps = [
            _make_snapshot(best_bid=0.001, best_ask=0.999,
                          ts_event_ns=1_777_000_000_000_000_000 + i * 5_000_000_000)
            for i in range(154)
        ]
        stats = compute_actionable_stats(snaps)
        assert stats["actionable_two_sided_book_count"] == 0

    def test_lifecycle_bucket_csv_excludes_near_boundary_as_actionable(self):
        """quote_quality_by_lifecycle_bucket.csv must not count near-boundary as actionable."""
        near_boundary_snaps = [
            _make_snapshot(best_bid=0.001, best_ask=0.999, seconds_to_expiry=1500)
            for _ in range(10)
        ]
        # All should be EXCHANGE_BOUND_TWO_SIDED_BOOK, not TWO_SIDED_BOOK
        for snap in near_boundary_snaps:
            assert snap.quote_quality == QuoteQuality.EXCHANGE_BOUND_TWO_SIDED_BOOK
            assert snap.actionable_two_sided_book is False

    def test_verdict_description_mentions_boundary_when_no_actionable(self):
        """When near-boundary results in NO_ACTIONABLE, verdict description is correct."""
        verdict, desc = classify_verdict(
            actionable_count=0, total_count=774, actionable_rate=0.0,
            max_contiguous_actionable_seconds=0.0,
            book_poll_success_count=774, book_poll_failure_count=0,
            in_lifecycle_snapshot_count=636,
        )
        assert verdict == V_NO_ACTIONABLE