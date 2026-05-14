"""Tests for duration spread probe.

No orders. No keys. No execution. No on-chain calls.
"""
from __future__ import annotations

import ast
import json
import math
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from examples.strategies.polymarket_btcusd_arb.duration_market_discovery import (
    DURATION_15M,
    DURATION_1H,
    DURATION_4H,
    DURATION_UNKNOWN,
    DurationMarketInfo,
    UpDownMarketInfo,
    classify_duration,
    classify_duration_from_slug,
    classify_duration_from_start_end,
    classify_duration_from_title,
    discover_updown_markets,
    discover_duration_markets,
)
from examples.strategies.polymarket_btcusd_arb.spread_regime import (
    SpreadEvent,
    SpreadRegimeSummary,
    QuoteQuality,
    compute_spread_bps,
    compute_spread_abs,
    compute_summary_from_events,
)
from examples.strategies.polymarket_btcusd_arb.run_duration_spread_probe import (
    V_NO_ACTIVE,
    V_TOO_FEW,
    V_NO_USABLE,
    V_ACTIONABLE,
    classify_duration_verdict,
    group_events_by_duration,
    compute_duration_summary,
)


# --- Helpers ---

def _make_market(
    slug: str = "btc-updown-1h-1778794200",
    question: str = "Will BTC go up or down in 1 hour?",
    active: bool = True,
    closed: bool = False,
    start_ns: int | None = None,
    end_ns: int | None = None,
    yes_token_id: str | None = "token_yes_123",
    no_token_id: str | None = "token_no_456",
) -> UpDownMarketInfo:
    """Create a test UpDownMarketInfo."""
    if start_ns is None:
        start_ns = 1778794200_000_000_000
    if end_ns is None:
        # Default: 1h duration
        end_ns = start_ns + 3600_000_000_000
    return UpDownMarketInfo(
        slug=slug,
        question=question,
        active=active,
        closed=closed,
        condition_id="condition_123",
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        start_ns=start_ns,
        end_ns=end_ns,
        series_slug=None,
        resolution_source=None,
    )


def _make_event(
    market_slug: str = "btc-updown-1h-test",
    ts_ns: int = 1_777_000_000_000_000_000,
    tte_ns: int = 300_000_000_000,
    best_bid: float | None = 0.48,
    best_ask: float | None = 0.52,
    mid: float | None = 0.50,
    spread_bps: float | None = None,
    is_synthetic_fallback: bool = False,
) -> SpreadEvent:
    """Create a test spread event."""
    if spread_bps is None and best_bid is not None and best_ask is not None:
        spread_bps = compute_spread_bps(best_bid, best_ask)
    return SpreadEvent(
        market_slug=market_slug,
        ts_event_ns=ts_ns,
        time_to_expiry_ns=tte_ns,
        best_bid=best_bid,
        best_ask=best_ask,
        mid=mid,
        spread_abs=compute_spread_abs(best_bid, best_ask),
        spread_bps=spread_bps,
        book_depth_bid=None,
        book_depth_ask=None,
        binance_price=None,
        binance_spread_bps=None,
        binance_short_window_vol_bps=None,
        polymarket_stale=False,
        binance_stale=True,
        is_synthetic_fallback=is_synthetic_fallback,
    )


# --- Duration Classification ---

class TestDurationClassification:
    """Test duration classification from various sources."""

    def test_classifies_15m_from_slug(self):
        """15m slug → '15m' with 900 seconds."""
        label, seconds = classify_duration_from_slug("btc-updown-15m-1778794200")
        assert label == "15m"
        assert seconds == 900

    def test_classifies_1h_from_slug(self):
        """1h slug → '1h' with 3600 seconds."""
        label, seconds = classify_duration_from_slug("btc-updown-1h-1778794200")
        assert label == "1h"
        assert seconds == 3600

    def test_classifies_4h_from_slug(self):
        """4h slug → '4h' with 14400 seconds."""
        label, seconds = classify_duration_from_slug("btc-updown-4h-1778794200")
        assert label == "4h"
        assert seconds == 14400

    def test_classifies_unknown_when_ambiguous(self):
        """Non-matching slug → 'unknown'."""
        label, seconds = classify_duration_from_slug("btc-something-else")
        assert label == DURATION_UNKNOWN
        assert seconds is None

    def test_classifies_15m_from_start_end(self):
        """start and end 15 minutes apart → '15m'."""
        start_ns = 1778794200_000_000_000
        end_ns = start_ns + 900_000_000_000  # 15 minutes
        label, seconds = classify_duration_from_start_end(start_ns, end_ns)
        assert label == "15m"

    def test_classifies_1h_from_start_end(self):
        """start and end 1 hour apart → '1h'."""
        start_ns = 1778794200_000_000_000
        end_ns = start_ns + 3600_000_000_000  # 1 hour
        label, seconds = classify_duration_from_start_end(start_ns, end_ns)
        assert label == "1h"

    def test_classifies_4h_from_start_end(self):
        """start and end 4 hours apart → '4h'."""
        start_ns = 1778794200_000_000_000
        end_ns = start_ns + 14400_000_000_000  # 4 hours
        label, seconds = classify_duration_from_start_end(start_ns, end_ns)
        assert label == "4h"

    def test_unknown_when_start_end_none(self):
        """None start/end → 'unknown'."""
        label, seconds = classify_duration_from_start_end(None, None)
        assert label == DURATION_UNKNOWN
        assert seconds is None

    def test_unknown_when_start_end_zero(self):
        """Zero start/end → 'unknown'."""
        label, seconds = classify_duration_from_start_end(0, 0)
        assert label == DURATION_UNKNOWN

    def test_classifies_duration_from_title_15m(self):
        """Title with '15 minute' → '15m'."""
        label, seconds = classify_duration_from_title("Will BTC go up in the next 15 minutes?")
        assert label == "15m"
        assert seconds == 900

    def test_classifies_duration_from_title_1h(self):
        """Title with '1 hour' → '1h'."""
        label, seconds = classify_duration_from_title("Will BTC go up in the next 1 hour?")
        assert label == "1h"
        assert seconds == 3600

    def test_classifies_duration_from_title_4h(self):
        """Title with '4 hour' → '4h'."""
        label, seconds = classify_duration_from_title("Will BTC go up or down in 4 hours?")
        assert label == "4h"
        assert seconds == 14400

    def test_classify_duration_prefers_slug(self):
        """classify_duration should prefer slug over start_end and title."""
        m = _make_market(slug="btc-updown-1h-1778794200", start_ns=None, end_ns=None)
        result = classify_duration(m)
        assert result.duration_label == "1h"
        assert result.classification_source == "slug"

    def test_classify_duration_uses_start_end_when_no_slug(self):
        """classify_duration should use start_end when slug doesn't match."""
        # Use a slug that doesn't match the pattern, but start/end that = 1h
        m = _make_market(slug="btc-market-unknown-slug", start_ns=1778794200_000_000_000, end_ns=1778794200_000_000_000 + 3600_000_000_000)
        result = classify_duration(m)
        assert result.duration_label == "1h"
        assert result.classification_source == "start_end"

    def test_classify_duration_uses_title_as_fallback(self):
        """classify_duration should use title when slug and start_end don't classify."""
        m = UpDownMarketInfo(
            slug="btc-market-unknown-slug",
            question="Will BTC move in the next 4 hours?",
            active=True,
            closed=False,
            condition_id="cond_123",
            yes_token_id="tok_yes",
            no_token_id="tok_no",
            start_ns=None,
            end_ns=None,
            series_slug=None,
            resolution_source=None,
        )
        result = classify_duration(m)
        assert result.duration_label == "4h"
        assert result.classification_source == "title"

    def test_classify_duration_unknown_when_all_fail(self):
        """classify_duration should return unknown when all methods fail."""
        m = _make_market(
            slug="btc-market-xyz",
            question="What will BTC do?",
            start_ns=None,
            end_ns=None,
        )
        # Override the default start/end to be None so no start_end classification
        m_unknown = UpDownMarketInfo(
            slug="btc-market-xyz",
            question="What will BTC do?",
            active=True,
            closed=False,
            condition_id="cond_123",
            yes_token_id="tok_yes",
            no_token_id="tok_no",
            start_ns=None,
            end_ns=None,
            series_slug=None,
            resolution_source=None,
        )
        result = classify_duration(m_unknown)
        assert result.duration_label == DURATION_UNKNOWN
        assert result.classification_source == "unknown"


# --- Quote Quality Grouped By Duration ---

class TestQuoteQualityByDuration:
    """Test quote quality grouping by duration."""

    def test_group_events_by_duration(self):
        """Events group correctly by duration label."""
        duration_map = {
            "market-1h": "1h",
            "market-4h": "4h",
            "market-15m": "15m",
        }
        e1 = _make_event(market_slug="market-1h", best_bid=0.48, best_ask=0.52)
        e2 = _make_event(market_slug="market-4h", best_bid=0.45, best_ask=0.55)
        e3 = _make_event(market_slug="market-15m", best_bid=0.01, best_ask=0.99)
        groups = group_events_by_duration([e1, e2, e3], duration_map)
        assert "1h" in groups
        assert "4h" in groups
        assert "15m" in groups
        assert len(groups["1h"]) == 1
        assert len(groups["4h"]) == 1
        assert len(groups["15m"]) == 1

    def test_actionable_book_count_excludes_exchange_bound_books(self):
        """Actionable two-sided book count must not count EXCHANGE_BOUND_TWO_SIDED_BOOK."""
        e_exc = _make_event(market_slug="m1", best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)
        e_real = _make_event(market_slug="m1", best_bid=0.48, best_ask=0.52, mid=0.50, spread_bps=800.0)
        events = [e_exc] * 80 + [e_real] * 20
        duration_map = {"m1": "1h"}
        dur_stats = compute_duration_summary(group_events_by_duration(events, duration_map))
        assert dur_stats["1h"]["actionable_two_sided_book_count"] == 20
        assert dur_stats["1h"]["exchange_bound_two_sided_book_count"] == 80


# --- Duration Verdicts ---

class TestDurationVerdict:
    """Test duration spread verdict classification."""

    def test_no_active_markets(self):
        """Zero markets → NO_ACTIVE_MARKETS."""
        events = [_make_event()]
        summary = compute_summary_from_events(events, "test", [])
        verdict, reason = classify_duration_verdict(0, events, summary)
        assert verdict == V_NO_ACTIVE
        assert "no_active" in reason

    def test_too_few_events(self):
        """Fewer than 10 events with markets → TOO_FEW."""
        events = [_make_event(best_bid=0.48, best_ask=0.52)] * 5
        summary = compute_summary_from_events(events, "test", [])
        verdict, reason = classify_duration_verdict(1, events, summary)
        assert verdict == V_TOO_FEW
        assert "too_few" in reason

    def test_no_usable_books_exchange_bound(self):
        """All exchange-bound → NO_USABLE_BOOKS."""
        events = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)] * 100
        summary = compute_summary_from_events(events, "test", [])
        verdict, reason = classify_duration_verdict(1, events, summary)
        assert verdict == V_NO_USABLE
        assert "exchange_bound" in reason or "no_usable" in reason

    def test_has_actionable_books(self):
        """Enough actionable events → HAS_ACTIONABLE_BOOKS."""
        events = [_make_event(best_bid=0.48, best_ask=0.52, mid=0.50, spread_bps=800.0)] * 50
        summary = compute_summary_from_events(events, "test", [])
        verdict, reason = classify_duration_verdict(1, events, summary)
        assert verdict == V_ACTIONABLE

    def test_no_usable_books_fallback(self):
        """All fallback → NO_USABLE_BOOKS."""
        events = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0, is_synthetic_fallback=True)] * 100
        summary = compute_summary_from_events(events, "test", [])
        verdict, reason = classify_duration_verdict(1, events, summary)
        assert verdict == V_NO_USABLE


# --- Report Content ---

class TestDurationReportContent:
    """Test that duration probe reports contain required sections."""

    def test_summary_contains_duration_fields(self, tmp_path):
        """summary.json must include duration-specific fields."""
        events = [_make_event(market_slug="m1", best_bid=0.48, best_ask=0.52)]
        duration_map = {"m1": "1h"}
        summary = compute_summary_from_events(events, "test", [])
        dur_stats = compute_duration_summary(group_events_by_duration(events, duration_map))

        assert "1h" in dur_stats
        assert "event_count" in dur_stats["1h"]
        assert "actionable_two_sided_book_count" in dur_stats["1h"]
        assert "exchange_bound_two_sided_book_count" in dur_stats["1h"]
        assert "median_spread_bps" in dur_stats["1h"]

    def test_report_md_contains_duration_sections(self, tmp_path):
        """report.md must contain duration-specific sections."""
        from examples.strategies.polymarket_btcusd_arb.run_duration_spread_probe import (
            _write_duration_report_md,
        )

        events = [_make_event(market_slug="m1", best_bid=0.48, best_ask=0.52)]
        duration_map = {"m1": "1h"}
        summary = compute_summary_from_events(events, "test", [])
        dur_stats = compute_duration_summary({"1h": events})

        summary_data = {
            "run_id": "test", "branch": "test",
            "durations_requested": ["1h", "4h"],
            "markets_discovered": 1, "markets_observed": 1,
            "event_count": 1,
            "actionable_two_sided_book_count": 1,
            "pct_actionable_two_sided_book": 100.0,
            "exchange_bound_two_sided_book_count": 0,
            "pct_exchange_bound_two_sided_book": 0.0,
            "fallback_min_max_count": 0, "pct_fallback_min_max": 0.0,
            "missing_or_empty_book_count": 0,
            "poll_count": 1,
            "verdict": V_ACTIONABLE, "reason": "1_actionable",
            "safety_status": "PASS",
            "limitations": ["Observer-only."],
        }

        _write_duration_report_md(tmp_path, summary_data, dur_stats, [], events, duration_map)
        report_text = (tmp_path / "report.md").read_text()

        assert "## Hypothesis" in report_text
        assert "## Market Discovery" in report_text
        assert "## Duration Classification" in report_text
        assert "## Quote Quality Summary" in report_text
        assert "## Verdict" in report_text
        assert "## Recommendation" in report_text
        assert "## Safety" in report_text

    def test_no_execution_recommendation(self, tmp_path):
        """report.md must not recommend execution regardless of verdict."""
        from examples.strategies.polymarket_btcusd_arb.run_duration_spread_probe import (
            _write_duration_report_md,
        )

        # Test with V_NO_USABLE
        events = [_make_event(best_bid=0.01, best_ask=0.99, mid=0.50, spread_bps=19600.0)]
        summary_data = {
            "run_id": "test", "branch": "test",
            "durations_requested": ["1h"],
            "markets_discovered": 1, "markets_observed": 1,
            "event_count": 1,
            "actionable_two_sided_book_count": 0,
            "pct_actionable_two_sided_book": 0.0,
            "exchange_bound_two_sided_book_count": 1,
            "pct_exchange_bound_two_sided_book": 100.0,
            "fallback_min_max_count": 0, "pct_fallback_min_max": 0.0,
            "missing_or_empty_book_count": 0,
            "poll_count": 1,
            "verdict": V_NO_USABLE, "reason": "exchange_bound",
            "safety_status": "PASS",
            "limitations": ["Observer-only."],
        }

        _write_duration_report_md(tmp_path, summary_data, {}, [], events, {})
        report_text = (tmp_path / "report.md").read_text()
        assert "No execution" in report_text or "Do not execute" in report_text


# --- Safety Checks ---

class TestDurationProbeSafety:
    """AST-based safety checks for duration probe modules."""

    def test_no_order_imports_in_duration_probe(self):
        """Duration probe modules must not import order/execution modules."""
        pkg_dir = Path(__file__).resolve().parent.parent
        for pyfile in [
            "duration_market_discovery.py",
            "run_duration_spread_probe.py",
        ]:
            filepath = pkg_dir / pyfile
            if not filepath.exists():
                continue
            source = filepath.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "OrderFactory" not in alias.name
                        assert "submit_order" not in alias.name
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert "nautilus_trader.live" not in node.module
                        assert "PolymarketExecutionClient" not in (node.module or "")
                    for alias in node.names:
                        assert alias.name not in (
                            "PolymarketExecutionClient",
                            "PolymarketLiveExecClientFactory",
                            "LiveNode",
                            "TradingNode",
                            "OrderFactory",
                        )

    def test_no_private_key_env_vars_in_duration_probe(self):
        """Duration probe must not read Polymarket credential env vars."""
        forbidden_env = {
            "POLYMARKET_PK",
            "POLYMARKET_API_KEY",
            "POLYMARKET_API_SECRET",
            "POLYMARKET_FUNDER",
            "POLYMARKET_PASSPHRASE",
        }
        pkg_dir = Path(__file__).resolve().parent.parent
        for pyfile in [
            "duration_market_discovery.py",
            "run_duration_spread_probe.py",
        ]:
            filepath = pkg_dir / pyfile
            if not filepath.exists():
                continue
            source = filepath.read_text()
            for env_var in forbidden_env:
                assert env_var not in source, f"Forbidden env var {env_var} found in {pyfile}"

    def test_no_submit_order_in_duration_probe(self):
        """Duration probe must not contain submit_order calls."""
        pkg_dir = Path(__file__).resolve().parent.parent
        for pyfile in [
            "duration_market_discovery.py",
            "run_duration_spread_probe.py",
        ]:
            filepath = pkg_dir / pyfile
            if not filepath.exists():
                continue
            source = filepath.read_text()
            assert "submit_order" not in source
            assert "cancel_order" not in source