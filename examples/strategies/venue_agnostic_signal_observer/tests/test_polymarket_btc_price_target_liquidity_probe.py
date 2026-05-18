"""Focused tests for the Polymarket BTC Price Target liquidity probe.

Tests cover:
- Adapter inspection / safety
- Market-family classification
- Strike extraction
- Token/outcome parsing
- TTE buckets
- Distance computation
- Convex danger zone
- Orderbook parsing
- Verdict/report guards
- CLI/import smoke
- Up/Down exclusion fixtures
"""

from __future__ import annotations

import json
import math
import time

import pytest

from examples.strategies.venue_agnostic_signal_observer.polymarket_btc_price_target_liquidity_probe import (
    # Market-family classification
    FAMILY_BTC_PRICE_TARGET,
    FAMILY_BTC_UPDOWN,
    FAMILY_BTC_OTHER,
    FAMILY_NON_BTC,
    classify_btc_market_family,
    # Strike extraction
    STRIKE_EXTRACTED,
    NO_STRIKE_FOUND,
    AMBIGUOUS_STRIKE,
    UNSUPPORTED_MARKET_TEXT,
    extract_strike,
    # Token parsing
    parse_tokens_from_market,
    # TTE
    TTE_GT_15M, TTE_5M_15M, TTE_2M_5M, TTE_1M_2M,
    TTE_30S_1M, TTE_0_30S, TTE_EXPIRED, TTE_UNKNOWN,
    compute_tte_seconds,
    classify_tte_bucket,
    # Distance
    DIST_DEEP_BELOW, DIST_BELOW_100_500, DIST_BELOW_25_100,
    DIST_NEAR_25,
    DIST_ABOVE_25_100, DIST_ABOVE_100_500, DIST_DEEP_ABOVE,
    DIST_UNKNOWN,
    compute_distance_bps,
    classify_distance_bucket,
    is_convex_danger_zone,
    # Orderbook
    BOOK_TWO_SIDED, BOOK_ONE_SIDED_BID, BOOK_ONE_SIDED_ASK,
    BOOK_EMPTY, BOOK_CROSSED,
    parse_orderbook_snapshot,
    empty_snapshot,
    # Verdicts
    GREEN_DIAG, YELLOW_DIAG, RED_DIAG,
    NEEDS_MORE_DATA_V, CAPTURE_UNUSABLE_V,
    FORBIDDEN_VERDICTS, ALLOWED_VERDICTS,
    compute_liquidity_verdict,
    compute_v1_recommendation,
    V1_JUSTIFIED, V1_HUMAN_REVIEW,
    V1_BLOCKED_LIQUIDITY, V1_BLOCKED_DATA,
    # Models
    LiquiditySample,
    OrderbookSnapshot,
    MarketMetadata,
    AdapterInspectionResult,
    # Adapter inspection
    inspect_nautilus_polymarket_adapter,
    DATA_PATH_NAUTILUS,
    DATA_PATH_MIXED,
    DATA_PATH_PUBLIC_REST,
    # BTC proxy
    poll_btc_binance_proxy,
    BtcProxyTick,
    # Misc
    _safe_float,
    _parse_levels,
    _primary_subset,
)


# ===================================================================
# Adapter inspection / safety
# ===================================================================


class TestAdapterInspection:
    """Adapter inspection records Polymarket modules without crashing."""

    def test_inspection_runs(self) -> None:
        result = inspect_nautilus_polymarket_adapter()
        assert isinstance(result, AdapterInspectionResult)
        assert len(result.modules_inspected) > 0

    def test_inspection_reports_data_path(self) -> None:
        result = inspect_nautilus_polymarket_adapter()
        assert result.selected_data_path in (
            DATA_PATH_NAUTILUS, DATA_PATH_MIXED, DATA_PATH_PUBLIC_REST
        )

    def test_inspection_finds_execution_modules(self) -> None:
        result = inspect_nautilus_polymarket_adapter()
        assert len(result.execution_auth_components_found) > 0
        assert any("execution" in m for m in result.execution_auth_components_found)

    def test_probe_source_no_execution_imports(self) -> None:
        """Probe source must not import execution/auth paths."""
        import inspect as _inspect
        import ast
        from examples.strategies.venue_agnostic_signal_observer import (
            polymarket_btc_price_target_liquidity_probe as probe_mod,
        )
        source = _inspect.getsource(probe_mod)
        # Parse AST and check import statements
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name.lower()
                    if any(t in name for t in ["execution", "wallet", "signing", "credentials"]):
                        pytest.fail(f"Core module imports forbidden path: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "").lower()
                if any(t in module for t in ["execution", "wallet", "signing", "credentials"]):
                    pytest.fail(f"Core module imports from forbidden path: {node.module}")


# ===================================================================
# Market-family classification
# ===================================================================


class TestMarketFamilyClassification:
    """All classification rules."""

    def test_updown_slug_btc_updown_5m(self) -> None:
        family, reason = classify_btc_market_family(
            "BTC Up or Down 5m", "btc-updown-5m-1779137400"
        )
        assert family == FAMILY_BTC_UPDOWN, f"Got {family}: {reason}"

    def test_updown_slug_btc_updown_15m(self) -> None:
        family, _ = classify_btc_market_family(
            "BTC Up or Down 15m", "btc-updown-15m-1779137100"
        )
        assert family == FAMILY_BTC_UPDOWN

    def test_updown_slug_bitcoin_up_or_down_may_18(self) -> None:
        family, _ = classify_btc_market_family(
            "Bitcoin Up or Down - May 18, 2026 4PM ET",
            "bitcoin-up-or-down-may-18-2026-4pm-et",
        )
        assert family == FAMILY_BTC_UPDOWN

    def test_updown_slug_btc_updown_4h(self) -> None:
        family, _ = classify_btc_market_family(
            "BTC Up or Down 4H", "btc-updown-4h-1779134400"
        )
        assert family == FAMILY_BTC_UPDOWN

    def test_updown_slug_bitcoin_up_or_down_may_19(self) -> None:
        family, _ = classify_btc_market_family(
            "Bitcoin Up or Down on May 19, 2026",
            "bitcoin-up-or-down-on-may-19-2026",
        )
        assert family == FAMILY_BTC_UPDOWN

    def test_price_target_with_explicit_strike(self) -> None:
        family, _ = classify_btc_market_family(
            "Will Bitcoin be above $105,000 on May 20?",
        )
        assert family == FAMILY_BTC_PRICE_TARGET

    def test_price_target_with_k_format(self) -> None:
        family, _ = classify_btc_market_family(
            "Will BTC hit $110k by Friday?",
        )
        assert family == FAMILY_BTC_PRICE_TARGET

    def test_generic_btc_without_strike(self) -> None:
        family, _ = classify_btc_market_family(
            "What will be the price of Bitcoin?",
        )
        assert family == FAMILY_BTC_OTHER

    def test_non_btc_returns_non_btc(self) -> None:
        family, _ = classify_btc_market_family(
            "Will ETH be above $5000?",
        )
        assert family == FAMILY_NON_BTC

    def test_price_target_from_slug(self) -> None:
        family, _ = classify_btc_market_family(
            "Will Bitcoin be above $100k?",
            "will-bitcoin-be-above-100k",
        )
        assert family == FAMILY_BTC_PRICE_TARGET


# ===================================================================
# Strike extraction
# ===================================================================


class TestStrikeExtraction:
    """Strike extraction rules with up/down exclusion fixtures."""

    def test_extract_105000_dollar_format(self) -> None:
        """$105,000 => 105000"""
        status, price, _, _, _ = extract_strike("Will Bitcoin be above $105,000 on May 20?")
        assert status == STRIKE_EXTRACTED
        assert price == pytest.approx(105000.0)

    def test_extract_110k(self) -> None:
        """110k => 110000; also handle $110k properly to avoid $110 + 110k ambiguity."""
        status, price, _, _, _ = extract_strike("Will BTC hit $110k by Friday?")
        assert status == STRIKE_EXTRACTED, f"Expected STRIKE_EXTRACTED, got {status}"
        assert price == pytest.approx(110000.0)

    def test_extract_100000_raw(self) -> None:
        """100000 => 100000"""
        status, price, _, _, _ = extract_strike("Bitcoin above 100000?")
        assert status == STRIKE_EXTRACTED
        assert price == pytest.approx(100000.0)

    def test_extract_95000_dollar_format(self) -> None:
        """$95,000 => 95000"""
        status, price, _, _, _ = extract_strike("BTC price target $95,000")
        assert status == STRIKE_EXTRACTED
        assert price == pytest.approx(95000.0)

    def test_extract_btc_above_100k(self) -> None:
        """BTC above $100k => 100000"""
        status, price, _, _, _ = extract_strike("BTC above $100k")
        assert status == STRIKE_EXTRACTED
        assert price == pytest.approx(100000.0)

    # Up/Down exclusion fixtures
    def test_updown_5m_slug(self) -> None:
        """btc-updown-5m-1779137400 must not extract a strike."""
        status, price, _, _, reason = extract_strike(
            "btc-updown-5m-1779137400 Up or Down 5m"
        )
        assert status == NO_STRIKE_FOUND
        assert price is None
        assert "EXCLUDED_BTC_UPDOWN" in reason

    def test_updown_15m_slug(self) -> None:
        status, price, _, _, reason = extract_strike(
            "btc-updown-15m-1779137100 Up or Down 15m"
        )
        assert status == NO_STRIKE_FOUND
        assert price is None
        assert "EXCLUDED_BTC_UPDOWN" in reason

    def test_updown_may_18_slug(self) -> None:
        status, price, _, _, reason = extract_strike(
            "Bitcoin Up or Down - May 18, 2026 4PM ET"
        )
        assert status == NO_STRIKE_FOUND
        assert price is None
        assert "EXCLUDED_BTC_UPDOWN" in reason

    def test_updown_4h_slug(self) -> None:
        status, price, _, _, reason = extract_strike(
            "btc-updown-4h-1779134400 Up or Down 4H"
        )
        assert status == NO_STRIKE_FOUND
        assert price is None
        assert "EXCLUDED_BTC_UPDOWN" in reason

    def test_updown_may_19_slug(self) -> None:
        status, price, _, _, reason = extract_strike(
            "Bitcoin Up or Down on May 19, 2026"
        )
        assert status == NO_STRIKE_FOUND
        assert price is None
        assert "EXCLUDED_BTC_UPDOWN" in reason

    def test_ambiguous_multiple_strikes(self) -> None:
        """Multiple plausible strikes => AMBIGUOUS_STRIKE."""
        status, price, _, _, reason = extract_strike(
            "Will BTC be between $100,000 and $110,000?"
        )
        assert status == AMBIGUOUS_STRIKE
        assert price is None
        assert "multiple" in reason.lower()

    def test_non_btc_unsupported(self) -> None:
        """Non-BTC text => returns STRIKE_EXTRACTED for the dollar amount.
        The extract_strike function is a pure string parser that extracts
        numerical values. BTC-context filtering is handled by the
        market-family classifier, not the strike extraction function.
        """
        status, price, _, _, _ = extract_strike("Will ETH be above $5000?")
        assert status == STRIKE_EXTRACTED
        assert price == pytest.approx(5000.0)

    def test_no_numerical_strike(self) -> None:
        """BTC text without any strike => NO_STRIKE_FOUND."""
        status, price, _, _, _ = extract_strike("What will be the price of Bitcoin?")
        assert status == NO_STRIKE_FOUND


# ===================================================================
# Token/outcome parsing
# ===================================================================


class TestTokenParsing:
    """Token/outcome parsing from Gamma market dicts."""

    def test_json_string_clob_ids(self) -> None:
        """JSON string clobTokenIds with JSON array outcomes."""
        market = {
            "clobTokenIds": '["0xtoken1", "0xtoken2"]',
            "outcomes": '["Yes", "No"]',
        }
        yes, no, all_tokens = parse_tokens_from_market(market)
        assert yes == "0xtoken1"
        assert no == "0xtoken2"
        assert len(all_tokens) == 2

    def test_list_clob_ids(self) -> None:
        """List clobTokenIds."""
        market = {
            "clobTokenIds": ["tok_a", "tok_b"],
            "outcomes": ["Yes", "No"],
        }
        yes, no, _ = parse_tokens_from_market(market)
        assert yes == "tok_a"
        assert no == "tok_b"

    def test_up_down_outcomes(self) -> None:
        """Up/Down outcomes map to yes/no appropriately."""
        market = {
            "clobTokenIds": ["tok_up", "tok_down"],
            "outcomes": ["Up", "Down"],
        }
        yes, no, _ = parse_tokens_from_market(market)
        assert yes == "tok_up"
        assert no == "tok_down"

    def test_ambiguous_mapping_empty(self) -> None:
        """Missing/empty tokens produce None."""
        market = {}
        yes, no, tokens = parse_tokens_from_market(market)
        assert yes is None
        assert no is None
        assert tokens == []


# ===================================================================
# TTE buckets
# ===================================================================


class TestTTEBuckets:
    """TTE bucket boundaries."""

    def test_gt_15m(self) -> None:
        assert classify_tte_bucket(901) == TTE_GT_15M

    def test_5_15m(self) -> None:
        assert classify_tte_bucket(301) == TTE_5M_15M
        assert classify_tte_bucket(900) == TTE_5M_15M

    def test_2_5m(self) -> None:
        assert classify_tte_bucket(121) == TTE_2M_5M
        assert classify_tte_bucket(300) == TTE_2M_5M

    def test_1_2m(self) -> None:
        assert classify_tte_bucket(61) == TTE_1M_2M
        assert classify_tte_bucket(120) == TTE_1M_2M

    def test_30s_1m(self) -> None:
        assert classify_tte_bucket(31) == TTE_30S_1M
        assert classify_tte_bucket(60) == TTE_30S_1M

    def test_0_30s(self) -> None:
        assert classify_tte_bucket(0) == TTE_0_30S
        assert classify_tte_bucket(30) == TTE_0_30S

    def test_expired(self) -> None:
        assert classify_tte_bucket(-1) == TTE_EXPIRED

    def test_unknown(self) -> None:
        assert classify_tte_bucket(None) == TTE_UNKNOWN

    def test_tte_seconds_computation(self) -> None:
        """compute_tte_seconds returns float for valid ISO date."""
        # Use a future date far enough away
        from datetime import datetime, timezone, timedelta
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        tte = compute_tte_seconds(future)
        assert tte is not None
        assert tte > 3000  # close to 3600

    def test_tte_seconds_none(self) -> None:
        assert compute_tte_seconds(None) is None

    def test_tte_seconds_invalid(self) -> None:
        assert compute_tte_seconds("not-a-date") is None


# ===================================================================
# Distance
# ===================================================================


class TestDistance:
    """Distance computation and bucket classification."""

    def test_distance_above(self) -> None:
        d = compute_distance_bps(110000, 100000)
        assert d is not None
        assert d == pytest.approx(1000.0)  # (10000/100000)*10000

    def test_distance_below(self) -> None:
        d = compute_distance_bps(90000, 100000)
        assert d is not None
        assert d == pytest.approx(-1000.0)

    def test_distance_none(self) -> None:
        assert compute_distance_bps(None, 100000) is None
        assert compute_distance_bps(100000, None) is None
        assert compute_distance_bps(100000, 0) is None

    def test_deep_below(self) -> None:
        assert classify_distance_bucket(-600) == DIST_DEEP_BELOW
        assert classify_distance_bucket(-500) == DIST_DEEP_BELOW

    def test_below_100_500(self) -> None:
        assert classify_distance_bucket(-101) == DIST_BELOW_100_500
        assert classify_distance_bucket(-200) == DIST_BELOW_100_500
        assert classify_distance_bucket(-500) == DIST_DEEP_BELOW  # boundary

    def test_below_25_100(self) -> None:
        assert classify_distance_bucket(-26) == DIST_BELOW_25_100
        assert classify_distance_bucket(-99) == DIST_BELOW_25_100

    def test_near_strike(self) -> None:
        assert classify_distance_bucket(-25) == DIST_NEAR_25
        assert classify_distance_bucket(0) == DIST_NEAR_25
        assert classify_distance_bucket(25) == DIST_NEAR_25

    def test_above_25_100(self) -> None:
        assert classify_distance_bucket(26) == DIST_ABOVE_25_100
        assert classify_distance_bucket(99) == DIST_ABOVE_25_100

    def test_above_100_500(self) -> None:
        assert classify_distance_bucket(101) == DIST_ABOVE_100_500
        assert classify_distance_bucket(499) == DIST_ABOVE_100_500

    def test_deep_above(self) -> None:
        assert classify_distance_bucket(500) == DIST_DEEP_ABOVE
        assert classify_distance_bucket(1000) == DIST_DEEP_ABOVE

    def test_unknown(self) -> None:
        assert classify_distance_bucket(None) == DIST_UNKNOWN
        assert classify_distance_bucket(float("nan")) == DIST_UNKNOWN


# ===================================================================
# Convex danger zone
# ===================================================================


class TestConvexDangerZone:
    """Convex danger zone logic."""

    def test_true_closestrike_short_tte(self) -> None:
        assert is_convex_danger_zone(60, 10) is True
        assert is_convex_danger_zone(120, 25) is True
        assert is_convex_danger_zone(30, 0) is True

    def test_false_long_tte(self) -> None:
        assert is_convex_danger_zone(121, 10) is False

    def test_false_wide_distance(self) -> None:
        assert is_convex_danger_zone(60, 26) is False

    def test_false_expired(self) -> None:
        assert is_convex_danger_zone(-1, 10) is False

    def test_false_unknown(self) -> None:
        assert is_convex_danger_zone(None, 10) is False
        assert is_convex_danger_zone(60, None) is False


# ===================================================================
# Orderbook parser
# ===================================================================


class TestOrderbookParser:
    """Orderbook parsing with corrected top-of-book semantics."""

    def test_best_bid_is_max_bid(self) -> None:
        """best bid = max bid price."""
        raw = {
            "bids": [["0.5", "100"], ["0.55", "200"]],
            "asks": [["0.6", "150"]],
        }
        snap = parse_orderbook_snapshot(raw, 1000, "m1", "t1")
        assert snap.book_status == BOOK_TWO_SIDED
        assert snap.best_bid_price == pytest.approx(0.55)
        assert snap.best_bid_size == pytest.approx(200)

    def test_best_ask_is_min_ask(self) -> None:
        """best ask = min ask price."""
        raw = {
            "bids": [["0.5", "100"]],
            "asks": [["0.7", "50"], ["0.65", "80"]],
        }
        snap = parse_orderbook_snapshot(raw, 1000, "m1", "t1")
        assert snap.best_ask_price == pytest.approx(0.65)
        assert snap.best_ask_size == pytest.approx(80)

    def test_spread_cents(self) -> None:
        raw = {
            "bids": [["0.50", "100"]],
            "asks": [["0.55", "100"]],
        }
        snap = parse_orderbook_snapshot(raw, 1000, "m1", "t1")
        assert snap.executable_spread_price_units == pytest.approx(0.05)
        assert snap.executable_spread_cents == pytest.approx(5.0)

    def test_one_sided_bid_only(self) -> None:
        raw = {"bids": [["0.5", "100"]], "asks": []}
        snap = parse_orderbook_snapshot(raw, 1000, "m1", "t1")
        assert snap.book_status == BOOK_ONE_SIDED_BID

    def test_one_sided_ask_only(self) -> None:
        raw = {"bids": [], "asks": [["0.6", "100"]]}
        snap = parse_orderbook_snapshot(raw, 1000, "m1", "t1")
        assert snap.book_status == BOOK_ONE_SIDED_ASK

    def test_empty_book(self) -> None:
        raw = {"bids": [], "asks": []}
        snap = parse_orderbook_snapshot(raw, 1000, "m1", "t1")
        assert snap.book_status == BOOK_EMPTY

    def test_crossed_book(self) -> None:
        """Best bid > best ask => crossed."""
        raw = {
            "bids": [["0.60", "100"]],
            "asks": [["0.55", "100"]],
        }
        snap = parse_orderbook_snapshot(raw, 1000, "m1", "t1")
        assert snap.book_status == BOOK_CROSSED

    def test_none_raw(self) -> None:
        snap = parse_orderbook_snapshot(None, 1000, "m1", "t1")
        assert snap.book_status == BOOK_EMPTY

    def test_top_depth_usd(self) -> None:
        raw = {
            "bids": [["0.5", "100"]],
            "asks": [["0.6", "200"]],
        }
        snap = parse_orderbook_snapshot(raw, 1000, "m1", "t1")
        assert snap.top_bid_depth_usd == pytest.approx(50.0)  # 0.5 * 100
        assert snap.top_ask_depth_usd == pytest.approx(120.0)  # 0.6 * 200
        assert snap.top_of_book_depth_usd == pytest.approx(170.0)

    def test_malformed_levels_skipped(self) -> None:
        """Malformed levels don't crash; incomplete entries skipped."""
        raw = {
            "bids": [["0.5", "100"], None, "invalid", ["0.55"]],
            "asks": [["0.6", "200"]],
        }
        snap = parse_orderbook_snapshot(raw, 1000, "m1", "t1")
        # Only ["0.5", "100"] has both price and size; ["0.55"] only has 1 element
        assert snap.best_bid_price == pytest.approx(0.5)
        assert snap.best_bid_size == pytest.approx(100.0)

    def test_string_decimal_prices(self) -> None:
        """String and decimal-like price values are handled."""
        raw = {
            "bids": [["0.550", "100.5"]],
            "asks": [["0.600", "200.3"]],
        }
        snap = parse_orderbook_snapshot(raw, 1000, "m1", "t1")
        assert snap.best_bid_price == pytest.approx(0.55)
        assert snap.best_bid_size == pytest.approx(100.5)

    def test_empty_snapshot_with_side(self) -> None:
        snap = empty_snapshot(1000, "m1", "t1", "yes")
        assert snap.token_id == "t1"
        assert snap.side_label == "yes"
        assert snap.book_status == BOOK_EMPTY


# ===================================================================
# Verdict / report guards
# ===================================================================


class TestVerdictGuards:
    """Only Phase 0 verdicts allowed; forbidden verdicts fail."""

    def test_allowed_verdicts_defined(self) -> None:
        assert GREEN_DIAG in ALLOWED_VERDICTS
        assert YELLOW_DIAG in ALLOWED_VERDICTS
        assert RED_DIAG in ALLOWED_VERDICTS
        assert NEEDS_MORE_DATA_V in ALLOWED_VERDICTS
        assert CAPTURE_UNUSABLE_V in ALLOWED_VERDICTS

    def test_forbidden_verdicts_not_in_allowed(self) -> None:
        for v in FORBIDDEN_VERDICTS:
            assert v not in ALLOWED_VERDICTS

    def test_v1_recommendation_mapping(self) -> None:
        assert compute_v1_recommendation(GREEN_DIAG) == V1_JUSTIFIED
        assert compute_v1_recommendation(YELLOW_DIAG) == V1_HUMAN_REVIEW
        assert compute_v1_recommendation(RED_DIAG) == V1_BLOCKED_LIQUIDITY
        assert compute_v1_recommendation(NEEDS_MORE_DATA_V) == V1_BLOCKED_DATA
        assert compute_v1_recommendation(CAPTURE_UNUSABLE_V) == V1_BLOCKED_DATA

    def test_verdict_no_candidate_wording(self) -> None:
        """Verdict names must not imply candidate/trade-ready."""
        for v in ALLOWED_VERDICTS:
            assert "CANDIDATE" not in v
            assert "TRADE" not in v
            assert "EXECUTION" not in v
            assert "REJECTED" not in v

    def test_liquidity_verdict_empty_samples(self) -> None:
        """Empty samples should produce NEEDS_MORE_DATA or CAPTURE_UNUSABLE."""
        verdict, _ = compute_liquidity_verdict([])
        assert verdict == NEEDS_MORE_DATA_V

    def test_v1_recommendation_no_trade_ready(self) -> None:
        """V1 recommendation should not say trade-ready."""
        for v in [V1_JUSTIFIED, V1_HUMAN_REVIEW, V1_BLOCKED_LIQUIDITY, V1_BLOCKED_DATA]:
            assert "TRADE" not in v
            assert "EXECUTION" not in v


# ===================================================================
# CLI / import smoke
# ===================================================================


class TestCliSmoke:
    """CLI runner imports and default existence."""

    def test_runner_imports(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.run_polymarket_btc_price_target_liquidity_probe import (
            main, _parse_args,
        )
        assert callable(main)
        assert callable(_parse_args)

    def test_argparse_defaults(self) -> None:
        from examples.strategies.venue_agnostic_signal_observer.run_polymarket_btc_price_target_liquidity_probe import (
            _parse_args,
            DEFAULT_DURATION_SECONDS,
            DEFAULT_POLL_INTERVAL,
            DEFAULT_MAX_MARKETS,
        )
        assert DEFAULT_DURATION_SECONDS == 1800
        assert DEFAULT_POLL_INTERVAL == 5
        assert DEFAULT_MAX_MARKETS == 25


# ===================================================================
# Up/Down exclusion — Up/Down markets not counted as Price Target
# ===================================================================


class TestUpDownNotCounted:
    """Up/Down markets must not be counted as Price Target markets."""

    def test_updown_markets_excluded_from_price_target(self) -> None:
        """Verify classification doesn't return PRICE_TARGET for any Up/Down fixture."""
        fixtures = [
            ("btc-updown-5m-1779137400", "BTC Up or Down 5m"),
            ("btc-updown-15m-1779137100", "BTC Up or Down 15m"),
            ("bitcoin-up-or-down-may-18-2026-4pm-et", "Bitcoin Up or Down - May 18, 2026 4PM ET"),
            ("btc-updown-4h-1779134400", "BTC Up or Down 4H"),
            ("bitcoin-up-or-down-on-may-19-2026", "Bitcoin Up or Down on May 19, 2026"),
        ]
        for slug, title in fixtures:
            family, _ = classify_btc_market_family(title, slug)
            assert family == FAMILY_BTC_UPDOWN, (
                f"Fixture {slug} classified as {family}, expected BTC_UPDOWN"
            )

    def test_updown_strike_extraction_returns_no_strike(self) -> None:
        """All Up/Down fixtures must return NO_STRIKE_FOUND from strike extraction."""
        texts = [
            "btc-updown-5m-1779137400 Up or Down",
            "btc-updown-15m-1779137100 Up or Down",
            "Bitcoin Up or Down - May 18, 2026 4PM ET",
            "btc-updown-4h-1779134400 Up or Down",
            "Bitcoin Up or Down on May 19, 2026",
        ]
        for text in texts:
            status, price, _, _, reason = extract_strike(text)
            assert status == NO_STRIKE_FOUND, f"Text '{text}' extracted status {status}"
            assert price is None


# ===================================================================
# Helpers
# ===================================================================


class TestHelpers:
    """Small utility function tests."""

    def test_safe_float(self) -> None:
        assert _safe_float("0.55") == pytest.approx(0.55)
        assert _safe_float(0.55) == pytest.approx(0.55)
        assert _safe_float(None) is None
        assert _safe_float("invalid") is None
        assert _safe_float(float("nan")) is None
        assert _safe_float(float("inf")) is None

    def test_parse_levels(self) -> None:
        levels = [["0.5", "100"], ["0.55", "200"], None]
        result = _parse_levels(levels)
        assert len(result) == 2
        assert result[0] == (0.5, 100.0)
        assert result[1] == (0.55, 200.0)

    def test_parse_levels_dict_format(self) -> None:
        levels = [{"price": "0.5", "size": "100"}, {"price": "0.55", "size": "200"}]
        result = _parse_levels(levels)
        assert len(result) == 2

    def test_primary_subset_empty(self) -> None:
        assert _primary_subset([]) == []


# ===================================================================
# BTC proxy
# ===================================================================


class TestBtcProxy:
    """BTC proxy model tests."""

    def test_btc_tick_creation(self) -> None:
        tick = BtcProxyTick(ts_event=1000.0, price=50000.0)
        assert tick.price == 50000.0
        assert tick.venue == "binance"
        assert tick.error is None

    def test_btc_tick_error(self) -> None:
        tick = BtcProxyTick(ts_event=1000.0, price=None, error="timeout")
        assert tick.price is None
        assert tick.error == "timeout"

    def test_btc_tick_to_dict(self) -> None:
        tick = BtcProxyTick(ts_event=1000.0, price=50000.0)
        d = tick.to_dict()
        assert d["price"] == 50000.0
        assert d["venue"] == "binance"
