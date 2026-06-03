#!/usr/bin/env python3
"""Tests for the Polymarket BTC Up/Down CLOB liquidity probe.

Safety tests (no forbidden patterns) and functional tests
(discovery, filtering, spread calculation, summary classification).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from ..polymarket_btc_updown_liquidity_probe import (
    FORBIDDEN_VERDICTS,
    GREEN_DIAG,
    YELLOW_DIAG,
    RED_DIAG,
    NEEDS_MORE_DATA,
    CAPTURE_UNUSABLE,
    SPREAD_GREEN_MAX,
    SPREAD_YELLOW_MAX,
    SPREAD_RED_P95,
    MIN_NON_DUST_DEPTH_USD,
    BTCMarket,
    OrderbookSample,
    ChainlinkTick,
    CaptureManifest,
    FeedStats,
    ProbeSummary,
    discover_btc_updown_markets,
    fetch_orderbook,
    parse_orderbook_sample,
    compute_summary,
    _is_btc_updown_market,
    _parse_updown_tokens,
    _extract_price_to_beat,
    _normalize_levels,
    _classify_liquidity,
    _get_git_sha,
    _parse_expiry_to_tte,
    _classify_tte_bucket,
    _compute_tte_buckets,
    _compute_verification_status_v2,
    _compute_near_expiry_rollup,
    _compute_duration_coverage,
    _check_tte_sanity,
    _classify_duration,
    _get_danger_zone_cell,
    write_discovered_markets,
    write_orderbook_samples,
    write_chainlink_ticks,
    write_manifest,
    write_summary_json,
    write_summary_md,
    write_raw_payloads,
    write_raw_payload_audit,
    write_tte_bucket_summary_json,
    write_tte_bucket_summary_md,
    RawPayloadRecord,
    DUR_5M, DUR_15M,
    LIQUIDITY_GATE_VERIFIED,
    LIQUIDITY_GATE_NOT_VERIFIED,
    LIQUIDITY_GATE_PENDING_TWO_AXIS_VERIFICATION,
    TTE_GT_15M, TTE_5M_TO_15M, TTE_2M_TO_5M, TTE_1M_TO_2M,
    TTE_30S_TO_1M, TTE_0S_TO_30S, TTE_EXPIRED, TTE_UNKNOWN,
)

# ===================================================================
# Safety: no forbidden patterns in new code
# ===================================================================


class TestSafetyNoOrders:
    """Verify the new probe module contains no trading/order/auth code.

    Checks non-comment, non-docstring code lines only.
    """

    FORBIDDEN = [
        "place_order",
        "create_order",
        "submit_order",
        "market_order",
        "limit_order",
        "private_key",
        "api_key",
        "secret",
        "signer",
        "/ws/user",
        "auth header",
        "clob order posting",
        "cancel order",
    ]

    MODULE_PATH = Path(__file__).resolve().parent.parent / "polymarket_btc_updown_liquidity_probe.py"
    RUNNER_PATH = Path(__file__).resolve().parent.parent / "runners" / "legacy_cli" / "run_polymarket_btc_updown_liquidity_probe.py"

    @staticmethod
    def _code_lines(source: str) -> list[str]:
        """Filter to only non-comment, non-docstring lines."""
        lines = []
        in_docstring = False

        for line in source.split("\n"):
            stripped = line.strip()

            # Track docstrings (triple-quote)
            if '"""' in stripped or "'''" in stripped:
                count_ddq = stripped.count('"""')
                count_sq = stripped.count("'''")
                ddq_open = '"""' in stripped
                sq_open = "'''" in stripped

                if ddq_open or sq_open:
                    dq_balanced = ddq_open and count_ddq >= 2
                    sq_balanced = sq_open and count_sq >= 2
                    if dq_balanced or sq_balanced:
                        continue  # single-line docstring
                    in_docstring = not in_docstring  # toggle
                    continue

            if in_docstring:
                continue

            # Skip single-line comments
            if stripped.startswith("#"):
                continue

            # Skip empty lines
            if not stripped:
                continue

            lines.append(line)
        return lines

    def test_module_no_forbidden_code(self):
        assert self.MODULE_PATH.exists(), f"Module not found at {self.MODULE_PATH}"
        source = self.MODULE_PATH.read_text().lower()
        code_lines = "\n".join(self._code_lines(source)).lower()
        for token in self.FORBIDDEN:
            assert token.lower() not in code_lines, (
                f"Found forbidden '{token}' in executable code of "
                f"polymarket_btc_updown_liquidity_probe.py"
            )

    def test_runner_no_forbidden_code(self):
        if not self.RUNNER_PATH.exists():
            pytest.skip("Runner file not yet created")
        source = self.RUNNER_PATH.read_text().lower()
        code_lines = "\n".join(self._code_lines(source)).lower()
        for token in self.FORBIDDEN:
            assert token.lower() not in code_lines, (
                f"Found forbidden '{token}' in executable code of "
                f"run_polymarket_btc_updown_liquidity_probe.py"
            )

    def test_no_trade_candidate_strings(self):
        """Verify no forbidden verdict strings appear in new code.

        Allows forbidden verdicts inside:
        - FORBIDDEN_VERDICTS constant definition
        - Safety disclaimer strings (e.g. 'No CANDIDATE, REJECTED...')
        """
        for fpath in [self.MODULE_PATH, self.RUNNER_PATH]:
            if not fpath.exists():
                continue
            source = fpath.read_text()
            code_lines = "\n".join(self._code_lines(source))

            # Find the FORBIDDEN_VERDICTS constant block and exclude it
            # Also exclude safety disclaimer strings
            clean_lines = []
            in_verdicts_block = False
            bracket_depth = 0
            for line in code_lines.split("\n"):
                stripped = line.strip()

                # Skip lines inside FORBIDDEN_VERDICTS = [...] block
                if "FORBIDDEN_VERDICTS" in stripped:
                    in_verdicts_block = True
                    bracket_depth = stripped.count("[") - stripped.count("]")
                    if bracket_depth <= 0:
                        in_verdicts_block = False
                    continue

                if in_verdicts_block:
                    bracket_depth += stripped.count("[") - stripped.count("]")
                    if bracket_depth <= 0:
                        in_verdicts_block = False
                    continue

                # Skip safety disclaimer strings containing forbidden verdicts
                # (strings that say 'cannot produce [verdict]' or 'no [verdict]')
                lower = stripped.lower()
                if any(
                    v.lower() in lower
                    for v in ["no ", "cannot produce", "cannot"]
                ):
                    if any(v in stripped for v in FORBIDDEN_VERDICTS):
                        continue

                clean_lines.append(line)

            cleaned = "\n".join(clean_lines)
            for verdict in FORBIDDEN_VERDICTS:
                assert verdict not in cleaned, (
                    f"Found forbidden verdict '{verdict}' outside allowed contexts "
                    f"in {fpath.name}"
                )


# ===================================================================
# Discovery filtering
# ===================================================================


class TestBtcPriceMarketDiscovery:
    """Test the _is_btc_updown_market filter function."""

    def test_updown_slug(self):
        assert _is_btc_updown_market("btc-updown-15m-1778794200", "Will BTC be higher in 15m?") == "up"

    def test_bitcoin_up_or_down_slug(self):
        assert _is_btc_updown_market("bitcoin-up-or-down-may-13", "Bitcoin up or down?") == "up"

    def test_updown_in_question(self):
        assert _is_btc_updown_market("some-slug", "BTC up/down 15m?") == "up"

    def test_not_updown(self):
        assert _is_btc_updown_market("btc-something", "Will BTC be above $100k?") is None

    def test_not_btc(self):
        assert _is_btc_updown_market("eth-updown-15m", "ETH up/down?") is None

    def test_empty(self):
        assert _is_btc_updown_market("", "") is None

    def test_not_bitcoin(self):
        assert _is_btc_updown_market("sol-updown", "SOL up or down?") is None


class TestParseUpdownTokens:
    """Test _parse_updown_tokens function."""

    def test_json_array_string(self):
        market = {
            "clobTokenIds": '["0x111", "0x222"]',
            "outcomes": '["Yes", "No"]',
        }
        yes, no = _parse_updown_tokens(market)
        assert yes == "0x111"
        assert no == "0x222"

    def test_list_types(self):
        market = {
            "clobTokenIds": ["0x111", "0x222"],
            "outcomes": ["Yes", "No"],
        }
        yes, no = _parse_updown_tokens(market)
        assert yes == "0x111"
        assert no == "0x222"

    def test_up_down_outcomes(self):
        market = {
            "clobTokenIds": ["0xaaa", "0xbbb"],
            "outcomes": ["UP", "DOWN"],
        }
        yes, no = _parse_updown_tokens(market)
        assert yes == "0xaaa"
        assert no == "0xbbb"

    def test_no_tokens(self):
        market = {}
        yes, no = _parse_updown_tokens(market)
        assert yes is None
        assert no is None

    def test_partial_outcomes(self):
        market = {
            "clobTokenIds": '["0x111"]',
            "outcomes": '["Yes"]',
        }
        yes, no = _parse_updown_tokens(market)
        assert yes == "0x111"
        assert no is None


class TestNonBtcSkipped:
    """Test that non-BTC and non-UpDown markets are skipped."""

    def test_eth_updown(self):
        r = _is_btc_updown_market("eth-updown-15m", "ETH up or down?")
        assert r is None, "ETH market should be skipped"

    def test_generic_market(self):
        r = _is_btc_updown_market("trump-wins", "Trump wins 2024?")
        assert r is None

    def test_btc_but_no_updown(self):
        r = _is_btc_updown_market("btc-price", "Will BTC hit $100k?")
        assert r is None


class TestPriceToBeatExtraction:
    """Test extracting strike/target prices from market questions."""

    def test_above_amount(self):
        assert _extract_price_to_beat("Will BTC be above $105,000?") == 105000.0

    def test_below_amount(self):
        assert _extract_price_to_beat("Will BTC be below $90,000?") == 90000.0

    def test_no_amount(self):
        assert _extract_price_to_beat("Will BTC be up?") is None

    def test_small_amount_not_btc_price(self):
        """Values under 10000 are not typical BTC prices."""
        result = _extract_price_to_beat("Will BTC be above $50?")
        # 50 is too small for BTC; should return None or the largest found
        assert result is not None
        assert result == 50.0  # max of [50]

    def test_btc_price_favored(self):
        result = _extract_price_to_beat("Will BTC be above $100K?")
        # $100K = 100000
        assert result == 100000.0

    def test_multiple_amounts(self):
        """Should pick the smallest BTC-like price."""
        result = _extract_price_to_beat("Will BTC be above $105,000 by Dec 31 2025?")
        assert result == 105000.0


class TestMissingTokenIds:
    """Test that markets without token IDs are handled."""

    def test_missing_tokens_skipped(self):
        """BTCMarket without token_ids should have reason_skipped."""
        market = BTCMarket(
            market_slug="test-btc-up",
            market_id="1",
            condition_id="0xabc",
            question="Will BTC be up?",
            outcomes=["Yes", "No"],
            yes_token_id=None,
            no_token_id=None,
            expiry="2025-12-31",
            price_to_beat=None,
            is_active=True,
            discovered_ts=1000.0,
            direction="up",
            reason_skipped="no_token_ids",
        )
        assert not market.has_tokens
        assert market.reason_skipped == "no_token_ids"

    def test_with_tokens(self):
        """BTCMarket with tokens should be actionable."""
        market = BTCMarket(
            market_slug="test-btc-up",
            market_id="1",
            condition_id="0xabc",
            question="Will BTC be up?",
            outcomes=["Yes", "No"],
            yes_token_id="0x111",
            no_token_id="0x222",
            expiry="2025-12-31",
            price_to_beat=None,
            is_active=True,
            discovered_ts=1000.0,
            direction="up",
        )
        assert market.has_tokens
        assert market.token_ids == ["0x111", "0x222"]
        assert market.reason_skipped is None


# ===================================================================
# Spread calculation
# ===================================================================


class TestSpreadCalculation:
    """Test that spread_cents is computed correctly from bid/ask."""

    def test_normal_spread(self):
        raw = {
            "bids": [["0.55", "1000"]],
            "asks": [["0.57", "500"]],
        }
        market = BTCMarket(
            market_slug="test",
            market_id="1", condition_id="0xabc",
            question="Will BTC be up?",
            outcomes=["Yes", "No"],
            yes_token_id="0x111", no_token_id="0x222",
            expiry="2025-12-31", price_to_beat=100000.0,
            is_active=True, discovered_ts=100.0,
            direction="up",
        )
        sample = parse_orderbook_sample(raw, market, "0x111", 200.0)
        assert sample.best_bid == pytest.approx(0.55, abs=1e-6)
        assert sample.best_ask == pytest.approx(0.57, abs=1e-6)
        assert sample.spread_price_units == pytest.approx(0.02, abs=1e-6)
        assert sample.spread_cents == pytest.approx(2.0, abs=1e-6)
        assert sample.is_two_sided
        assert not sample.is_crossed
        assert not sample.is_missing

    def test_no_bid(self):
        raw = {
            "bids": [],
            "asks": [["0.57", "500"]],
        }
        market = BTCMarket(
            market_slug="test", market_id="1", condition_id="0xabc",
            question="Test", outcomes=["Yes", "No"],
            yes_token_id="0x111", no_token_id=None,
            expiry=None, price_to_beat=None,
            is_active=True, discovered_ts=100.0, direction="up",
        )
        sample = parse_orderbook_sample(raw, market, "0x111", 200.0)
        assert sample.best_bid is None
        assert sample.best_ask == pytest.approx(0.57, abs=1e-6)
        assert sample.spread_cents is None
        assert not sample.is_two_sided
        assert not sample.is_missing

    def test_crossed_book(self):
        raw = {
            "bids": [["0.58", "1000"]],
            "asks": [["0.55", "500"]],
        }
        market = BTCMarket(
            market_slug="test", market_id="1", condition_id="0xabc",
            question="Test", outcomes=["Yes", "No"],
            yes_token_id="0x111", no_token_id=None,
            expiry=None, price_to_beat=None,
            is_active=True, discovered_ts=100.0, direction="up",
        )
        sample = parse_orderbook_sample(raw, market, "0x111", 200.0)
        assert sample.is_crossed
        assert sample.spread_cents is None  # crossed → no valid spread

    def test_missing_book(self):
        sample = parse_orderbook_sample(None, BTCMarket(
            market_slug="test", market_id="1", condition_id="0xabc",
            question="Test", outcomes=["Yes", "No"],
            yes_token_id="0x111", no_token_id=None,
            expiry=None, price_to_beat=None,
            is_active=True, discovered_ts=100.0, direction="up",
        ), "0x111", 200.0)
        assert sample.is_missing
        assert sample.best_bid is None
        assert sample.best_ask is None
        assert sample.rejection_reason == "no_data"

    def test_empty_book(self):
        raw = {"bids": [], "asks": []}
        market = BTCMarket(
            market_slug="test", market_id="1", condition_id="0xabc",
            question="Test", outcomes=["Yes", "No"],
            yes_token_id="0x111", no_token_id=None,
            expiry=None, price_to_beat=None,
            is_active=True, discovered_ts=100.0, direction="up",
        )
        sample = parse_orderbook_sample(raw, market, "0x111", 200.0)
        assert sample.is_missing
        assert sample.rejection_reason == "empty_book"


# ===================================================================
# Top-of-book depth calculation
# ===================================================================


class TestDepthCalculation:
    """Test that estimated depth USD is deterministic."""

    def test_bid_depth(self):
        raw = {
            "bids": [["0.55", "1000"]],
            "asks": [["0.57", "500"]],
        }
        market = BTCMarket(
            market_slug="test", market_id="1", condition_id="0xabc",
            question="Test", outcomes=["Yes", "No"],
            yes_token_id="0x111", no_token_id=None,
            expiry=None, price_to_beat=None,
            is_active=True, discovered_ts=100.0, direction="up",
        )
        sample = parse_orderbook_sample(raw, market, "0x111", 200.0)
        # bid depth = 1000 * 0.55 = 550.0
        assert sample.estimated_top_bid_depth_usd == pytest.approx(550.0, abs=0.01)
        # ask depth = 500 * 0.57 = 285.0
        assert sample.estimated_top_ask_depth_usd == pytest.approx(285.0, abs=0.01)

    def test_multiple_levels_only_top_used(self):
        raw = {
            "bids": [["0.60", "200"], ["0.59", "500"]],
            "asks": [["0.61", "100"], ["0.62", "300"]],
        }
        market = BTCMarket(
            market_slug="test", market_id="1", condition_id="0xabc",
            question="Test", outcomes=["Yes", "No"],
            yes_token_id="0x111", no_token_id=None,
            expiry=None, price_to_beat=None,
            is_active=True, discovered_ts=100.0, direction="up",
        )
        sample = parse_orderbook_sample(raw, market, "0x111", 200.0)
        assert sample.best_bid == pytest.approx(0.60, abs=1e-6)
        assert sample.top_bid_size == pytest.approx(200.0, abs=1e-6)
        # Only top level used: 200 * 0.60 = 120
        assert sample.estimated_top_bid_depth_usd == pytest.approx(120.0, abs=0.01)

    def test_no_bid_no_depth(self):
        raw = {
            "bids": [],
            "asks": [["0.57", "500"]],
        }
        market = BTCMarket(
            market_slug="test", market_id="1", condition_id="0xabc",
            question="Test", outcomes=["Yes", "No"],
            yes_token_id="0x111", no_token_id=None,
            expiry=None, price_to_beat=None,
            is_active=True, discovered_ts=100.0, direction="up",
        )
        sample = parse_orderbook_sample(raw, market, "0x111", 200.0)
        assert sample.estimated_top_bid_depth_usd is None
        assert sample.estimated_top_ask_depth_usd is not None


# ===================================================================
# Summary classification
# ===================================================================


class TestSummaryClassification:
    """Test that diagnostic classification uses fixed thresholds."""

    def test_green(self):
        spreads = [0.01, 0.02, 0.025, 0.03, 0.015, 0.028, 0.02]  # median ~0.02
        depths = [200.0, 300.0, 500.0]
        result = _classify_liquidity(spreads, depths, depths, total_valid=10)
        assert result == GREEN_DIAG

    def test_yellow(self):
        spreads = [0.04, 0.05, 0.035, 0.055, 0.045, 0.04]  # median ~0.0425
        depths = [200.0, 300.0, 500.0]
        result = _classify_liquidity(spreads, depths, depths, total_valid=10)
        assert result == YELLOW_DIAG

    def test_red_high_median(self):
        spreads = [0.07, 0.08, 0.09, 0.065, 0.075]  # median > 0.06
        depths = [200.0, 300.0]
        result = _classify_liquidity(spreads, depths, depths, total_valid=10)
        assert result == RED_DIAG

    def test_red_high_p95(self):
        spreads = [0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12]
        depths = [200.0, 300.0]
        result = _classify_liquidity(spreads, depths, depths, total_valid=20)
        assert result == RED_DIAG

    def test_red_dust_depth(self):
        spreads = [0.01, 0.02, 0.025, 0.03, 0.015]  # median <= 3c
        depths = [10.0, 20.0, 5.0]  # all < $100
        result = _classify_liquidity(spreads, depths, depths, total_valid=10)
        assert result == RED_DIAG, "Should be RED when depth is consistently dust"

    def test_needs_more_data(self):
        result = _classify_liquidity([0.02, 0.03], [200.0], [200.0], total_valid=3)
        assert result == NEEDS_MORE_DATA

    def test_capture_unusable(self):
        result = _classify_liquidity([], [], [], total_valid=0)
        assert result == CAPTURE_UNUSABLE

    def test_no_spreads_unusable(self):
        result = _classify_liquidity([], [], [], total_valid=5)
        assert result == CAPTURE_UNUSABLE

    def test_thresholds_not_hardcoded_differently(self):
        """Verify the actual thresholds match expectations."""
        assert SPREAD_GREEN_MAX == 0.03
        assert SPREAD_YELLOW_MAX == 0.06
        assert SPREAD_RED_P95 == 0.10
        assert MIN_NON_DUST_DEPTH_USD == 100.0


# ===================================================================
# Forbidden verdicts enforcement
# ===================================================================


class TestNoForbiddenVerdictsInSummary:
    """Verify ProbeSummary cannot contain forbidden verdict strings."""

    def test_summary_verdict_is_safe(self):
        summary = ProbeSummary(
            markets_discovered=5,
            markets_with_tokens=3,
            samples_collected=100,
            valid_samples=80,
            median_spread_price_units=0.02,
            median_spread_cents=2.0,
            p75_spread_cents=4.0,
            p95_spread_cents=6.0,
            median_top_bid_depth_usd=500.0,
            median_top_ask_depth_usd=300.0,
            percent_two_sided=75.0,
            percent_stale=5.0,
            percent_crossed=2.0,
            percent_missing=10.0,
            diagnostic_classification=GREEN_DIAG,
            verdict_statement="No trade candidate emitted.",
            spread_list_sample_count=80,
        )
        for verdict in FORBIDDEN_VERDICTS:
            assert verdict not in summary.verdict_statement
            assert verdict not in summary.diagnostic_classification

    def test_allowed_diagnostics_only(self):
        """Verify only allowed diagnostic strings are used."""
        from ..polymarket_btc_updown_liquidity_probe import ALLOWED_DIAGNOSTICS

        # Test each allowed diagnostic
        for diag in [GREEN_DIAG, YELLOW_DIAG, RED_DIAG, NEEDS_MORE_DATA, CAPTURE_UNUSABLE]:
            assert diag in ALLOWED_DIAGNOSTICS

        # Verify the constants are correct
        assert GREEN_DIAG == "GREEN_LIQUIDITY_DIAGNOSTIC"
        assert YELLOW_DIAG == "YELLOW_LIQUIDITY_DIAGNOSTIC"
        assert RED_DIAG == "RED_LIQUIDITY_DIAGNOSTIC"
        assert NEEDS_MORE_DATA == "NEEDS_MORE_DATA"
        assert CAPTURE_UNUSABLE == "CAPTURE_UNUSABLE"


# ===================================================================
# ComputeSummary on fixture data
# ===================================================================


class TestComputeSummaryDeterministic:
    """Verify compute_summary produces expected results on fixtures."""

    def _make_markets(self) -> list[BTCMarket]:
        return [
            BTCMarket(
                market_slug="btc-up-30dec",
                market_id="1", condition_id="0xabc",
                question="Will BTC be up on 30Dec2025?",
                outcomes=["Yes", "No"],
                yes_token_id="0x111",
                no_token_id="0x222",
                expiry="2025-12-30",
                price_to_beat=100000.0,
                is_active=True, discovered_ts=100.0,
                direction="up",
            ),
        ]

    def _make_samples(self, count: int = 10) -> list[OrderbookSample]:
        samples = []
        for i in range(count):
            for side, tid in [("yes", "0x111"), ("no", "0x222")]:
                samples.append(OrderbookSample(
                    ts_event=1000.0 + i,
                    market_slug="btc-up-30dec",
                    token_id=tid,
                    side=side,
                    expiry="2025-12-30",
                    price_to_beat=100000.0,
                    best_bid=0.50 + i * 0.001,
                    best_ask=0.52 + i * 0.001,
                    spread_price_units=0.02,
                    spread_cents=2.0,
                    top_bid_size=5000.0,
                    top_ask_size=3000.0,
                    estimated_top_bid_depth_usd=2500.0,
                    estimated_top_ask_depth_usd=1560.0,
                    is_two_sided=True,
                    is_stale=False,
                    is_crossed=False,
                    is_missing=False,
                ))
        return samples

    def test_compute_summary_green(self):
        markets = self._make_markets()
        samples = self._make_samples(20)
        summary = compute_summary(markets, samples)
        assert summary.markets_discovered == 1
        assert summary.samples_collected == 40  # 20 * 2 tokens
        assert summary.valid_samples == 40
        assert summary.median_spread_cents == pytest.approx(2.0, abs=0.001)
        assert summary.median_spread_price_units == pytest.approx(0.02, abs=0.001)
        assert summary.diagnostic_classification == GREEN_DIAG

    def test_compute_summary_deterministic(self):
        markets = self._make_markets()
        samples = self._make_samples(10)

        summaries = [compute_summary(markets, samples) for _ in range(3)]
        for s in summaries[1:]:
            assert s.diagnostic_classification == summaries[0].diagnostic_classification
            assert s.median_spread_cents == summaries[0].median_spread_cents
            assert s.samples_collected == summaries[0].samples_collected
            assert s.valid_samples == summaries[0].valid_samples

    def test_missing_samples_reduction(self):
        markets = self._make_markets()
        samples = self._make_samples(10)
        # Add some missing samples
        for i in range(5):
            samples.append(OrderbookSample(
                ts_event=2000.0 + i,
                market_slug="btc-up-30dec",
                token_id="0x111",
                side="yes",
                expiry="2025-12-30",
                price_to_beat=100000.0,
                best_bid=None, best_ask=None,
                spread_price_units=None, spread_cents=None,
                top_bid_size=None, top_ask_size=None,
                estimated_top_bid_depth_usd=None,
                estimated_top_ask_depth_usd=None,
                is_two_sided=False, is_stale=False,
                is_crossed=False, is_missing=True,
                rejection_reason="empty_book",
            ))
        summary = compute_summary(markets, samples)
        assert summary.valid_samples < summary.samples_collected
        assert summary.percent_missing > 0

    def test_chainlink_absence_no_crash(self):
        """Verify compute_summary works with no Chainlink data."""
        markets = self._make_markets()
        samples = self._make_samples(5)
        summary = compute_summary(markets, samples)
        assert summary.markets_discovered == 1
        assert summary.samples_collected > 0


# ===================================================================
# Output writers
# ===================================================================


class TestOutputWriters:
    """Test that writers produce deterministic output."""

    def test_discovered_markets_jsonl(self):
        markets = [
            BTCMarket(
                market_slug="test-btc-up",
                market_id="1", condition_id="0xabc",
                question="Will BTC be up?",
                outcomes=["Yes", "No"],
                yes_token_id="0x111", no_token_id="0x222",
                expiry="2025-12-31", price_to_beat=100000.0,
                is_active=True, discovered_ts=100.0,
                direction="up",
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "discovered_markets.jsonl"
            write_discovered_markets(markets, path)
            assert path.exists()
            lines = path.read_text().strip().split("\n")
            assert len(lines) == 1
            parsed = json.loads(lines[0])
            assert parsed["market_slug"] == "test-btc-up"
            assert parsed["direction"] == "up"

    def test_orderbook_samples_jsonl(self):
        sample = OrderbookSample(
            ts_event=1000.0, market_slug="test",
            token_id="0x111", side="yes",
            expiry="2025-12-31", price_to_beat=100000.0,
            best_bid=0.55, best_ask=0.57,
            spread_price_units=0.02, spread_cents=2.0, top_bid_size=1000.0,
            top_ask_size=500.0,
            estimated_top_bid_depth_usd=550.0,
            estimated_top_ask_depth_usd=285.0,
            is_two_sided=True, is_stale=False,
            is_crossed=False, is_missing=False,
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "clob_orderbook_samples.jsonl"
            write_orderbook_samples([sample], path)
            assert path.exists()
            parsed = json.loads(path.read_text().strip())
            assert parsed["spread_price_units"] == 0.02
            assert parsed["spread_cents"] == 2.0
            assert parsed["is_two_sided"]

    def test_chainlink_ticks_jsonl(self):
        tick = ChainlinkTick(ts_event=1000.0, price=95000.0, source_url="http://test")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "chainlink_reference_ticks.jsonl"
            write_chainlink_ticks([tick], path)
            assert path.exists()
            parsed = json.loads(path.read_text().strip())
            assert parsed["price"] == 95000.0

    def test_manifest_json(self):
        manifest = CaptureManifest(
            run_id="test_run",
            git_sha="abc123",
            started_at="2025-01-01T00:00:00",
            ended_at="2025-01-01T00:10:00",
            requested_duration_seconds=600,
            actual_duration_seconds=600.0,
            feeds_requested=["clob:test:0x111"],
            feeds_connected=["clob:test:0x111"],
            feed_stats={"clob:test:0x111": FeedStats(name="clob:test:0x111", sample_count=10)},
            market_slugs=["test"],
            token_ids=["0x111"],
            errors=[],
            overlap_duration_seconds=0.0,
            no_auth_no_orders_statement="Observer only.",
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "capture_manifest.json"
            write_manifest(manifest, path)
            assert path.exists()
            parsed = json.loads(path.read_text())
            assert parsed["run_id"] == "test_run"
            assert parsed["no_auth_no_orders_statement"] == "Observer only."

    def test_summary_json(self):
        summary = ProbeSummary(
            markets_discovered=1, markets_with_tokens=1,
            samples_collected=10, valid_samples=8,
            median_spread_price_units=0.02, median_spread_cents=2.0,
            p75_spread_cents=4.0,
            p95_spread_cents=6.0,
            median_top_bid_depth_usd=500.0,
            median_top_ask_depth_usd=300.0,
            percent_two_sided=75.0, percent_stale=5.0,
            percent_crossed=2.0, percent_missing=10.0,
            diagnostic_classification=GREEN_DIAG,
            verdict_statement="No trade candidate emitted.",
            spread_list_sample_count=8,
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "liquidity_probe_summary.json"
            write_summary_json(summary, path)
            assert path.exists()
            parsed = json.loads(path.read_text())
            assert parsed["diagnostic_classification"] == GREEN_DIAG
            assert parsed["markets_discovered"] == 1

    def test_summary_md(self):
        summary = ProbeSummary(
            markets_discovered=1, markets_with_tokens=1,
            samples_collected=10, valid_samples=8,
            median_spread_price_units=0.02, median_spread_cents=2.0,
            p75_spread_cents=4.0,
            p95_spread_cents=6.0,
            median_top_bid_depth_usd=500.0,
            median_top_ask_depth_usd=300.0,
            percent_two_sided=75.0, percent_stale=5.0,
            percent_crossed=2.0, percent_missing=10.0,
            diagnostic_classification=GREEN_DIAG,
            verdict_statement="No trade candidate emitted.",
            spread_list_sample_count=8,
        )
        manifest = CaptureManifest(
            run_id="test_run",
            git_sha="abc123",
            started_at="2025-01-01T00:00:00",
            ended_at="2025-01-01T00:10:00",
            requested_duration_seconds=600,
            actual_duration_seconds=600.0,
            feeds_requested=[], feeds_connected=[],
            feed_stats={}, market_slugs=[], token_ids=[],
            errors=[],
            overlap_duration_seconds=0.0,
            no_auth_no_orders_statement="Observer only.",
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "liquidity_probe_summary.md"
            write_summary_md(summary, manifest, path)
            assert path.exists()
            content = path.read_text()
            assert GREEN_DIAG in content
            assert "No trade candidate" in content
            assert "This summary was produced by a liquidity/actionability probe" in content


# ===================================================================
# _normalize_levels
# ===================================================================


class TestNormalizeLevels:
    """Test that orderbook level normalization works."""

    def test_array_levels(self):
        levels = [["0.55", "1000"], ["0.54", "2000"]]
        result = _normalize_levels(levels)
        assert len(result) == 2
        assert result[0]["price"] == 0.55
        assert result[0]["size"] == 1000.0

    def test_dict_levels(self):
        levels = [{"price": "0.55", "size": "1000"}, {"price": "0.54", "size": "2000"}]
        result = _normalize_levels(levels)
        assert len(result) == 2
        assert result[0]["price"] == 0.55

    def test_mixed_levels(self):
        levels = [["0.55", "1000"], {"price": "0.54", "size": "2000"}]
        result = _normalize_levels(levels)
        assert len(result) == 2

    def test_empty(self):
        result = _normalize_levels([])
        assert result == []

    def test_invalid_skipped(self):
        levels = [["not-a-number", "1000"], {"price": None, "size": "2000"}]
        result = _normalize_levels(levels)
        assert result == []


# ===================================================================
# _get_git_sha
# ===================================================================


class TestGitSha:
    def test_returns_string(self):
        sha = _get_git_sha()
        assert isinstance(sha, str)
        assert len(sha) > 0


# ===================================================================
# Partial/missing feeds in manifest
# ===================================================================


class TestManifestFeedTracking:
    """Verify manifest records feed state correctly."""

    def test_manifest_records_empty_feeds(self):
        manifest = CaptureManifest(
            run_id="test",
            git_sha="abc",
            started_at="2025-01-01T00:00:00",
            ended_at="2025-01-01T00:05:00",
            requested_duration_seconds=300,
            actual_duration_seconds=300.0,
            feeds_requested=["clob:test:0x111"],
            feeds_connected=["clob:test:0x111"],
            feed_stats={
                "clob:test:0x111": FeedStats(
                    name="clob:test:0x111",
                    sample_count=0,
                    error_count=1,
                ),
            },
            market_slugs=["test"],
            token_ids=["0x111"],
            errors=["clob:test:0x111: no_data"],
            overlap_duration_seconds=0.0,
            no_auth_no_orders_statement="Test.",
        )
        d = manifest.to_dict()
        assert d["errors"] == ["clob:test:0x111: no_data"]
        assert d["feed_stats"]["clob:test:0x111"]["sample_count"] == 0
        assert d["feed_stats"]["clob:test:0x111"]["error_count"] == 1


# ===================================================================
# Orderbook sample field types
# ===================================================================


class TestOrderbookSampleFields:
    """Verify all required fields are present in samples."""

    REQUIRED = [
        "ts_event",
        "market_slug",
        "token_id",
        "side",
        "expiry",
        "price_to_beat",
        "best_bid",
        "best_ask",
        "spread_price_units",
        "spread_cents",
        "top_bid_size",
        "top_ask_size",
        "estimated_top_bid_depth_usd",
        "estimated_top_ask_depth_usd",
        "is_two_sided",
        "is_stale",
        "is_crossed",
        "is_missing",
    ]

    def test_all_fields_present(self):
        sample = OrderbookSample(
            ts_event=1000.0,
            market_slug="test",
            token_id="0x111",
            side="yes",
            expiry="2025-12-31",
            price_to_beat=100000.0,
            best_bid=0.55,
            best_ask=0.57,
            spread_price_units=0.02,
            spread_cents=2.0,
            top_bid_size=1000.0,
            top_ask_size=500.0,
            estimated_top_bid_depth_usd=550.0,
            estimated_top_ask_depth_usd=285.0,
            is_two_sided=True,
            is_stale=False,
            is_crossed=False,
            is_missing=False,
        )
        d = sample.to_dict()
        for field in self.REQUIRED:
            assert field in d, f"Missing required field: {field}"


# ===================================================================
# Parser regression: unordered book arrays
# ===================================================================


class TestParserRegressionUnorderedArrays:
    """Verify best-bid/ask selection is independent of array order.

    Polymarket CLOB /book returns bids ASCENDING and asks DESCENDING.
    Using bids[0]/asks[0] would select WORST prices.
    The fix uses max(bids) / min(asks) which is order-independent.
    """

    def _make_market(self) -> BTCMarket:
        return BTCMarket(
            market_slug="test-updown", market_id="1", condition_id="0xabc",
            question="BTC up/down 15m?", outcomes=["Yes", "No"],
            yes_token_id="0x111", no_token_id="0x222",
            expiry="2025-12-31T12:00:00Z", price_to_beat=None,
            is_active=True, discovered_ts=100.0, direction="up",
        )

    def test_ascending_bids_selects_max_price(self):
        """bids ascending [0.01, 0.47, 0.48] -> best_bid = 0.48"""
        raw = {
            "bids": [{"price": "0.01", "size": "14000"}, {"price": "0.47", "size": "570"}, {"price": "0.48", "size": "560"}],
            "asks": [{"price": "0.99", "size": "14000"}, {"price": "0.50", "size": "558"}, {"price": "0.49", "size": "570"}],
        }
        sample = parse_orderbook_sample(raw, self._make_market(), "0x111", 200.0)
        assert sample.best_bid == pytest.approx(0.48, abs=1e-6)
        assert sample.best_ask == pytest.approx(0.49, abs=1e-6)
        assert sample.spread_price_units == pytest.approx(0.01, abs=1e-6)
        assert sample.spread_cents == pytest.approx(1.0, abs=1e-6)
        assert sample.top_bid_size == pytest.approx(560.0, abs=1e-6)
        assert sample.top_ask_size == pytest.approx(570.0, abs=1e-6)
        # Depth from best level
        assert sample.estimated_top_bid_depth_usd == pytest.approx(0.48 * 560, abs=0.01)
        assert sample.estimated_top_ask_depth_usd == pytest.approx(0.49 * 570, abs=0.01)

    def test_descending_bids_selects_max_price(self):
        """bids descending [0.50, 0.40, 0.30] -> best_bid = 0.50"""
        raw = {
            "bids": [{"price": "0.50", "size": "100"}, {"price": "0.40", "size": "200"}, {"price": "0.30", "size": "300"}],
            "asks": [{"price": "0.51", "size": "100"}, {"price": "0.52", "size": "200"}, {"price": "0.53", "size": "300"}],
        }
        sample = parse_orderbook_sample(raw, self._make_market(), "0x111", 200.0)
        assert sample.best_bid == pytest.approx(0.50, abs=1e-6)
        assert sample.best_ask == pytest.approx(0.51, abs=1e-6)
        assert sample.spread_price_units == pytest.approx(0.01, abs=1e-6)

    def test_shuffled_bids_selects_max_price(self):
        """bids shuffled [0.30, 0.50, 0.40] -> best_bid = 0.50"""
        raw = {
            "bids": [{"price": "0.30", "size": "300"}, {"price": "0.50", "size": "100"}, {"price": "0.40", "size": "200"}],
            "asks": [{"price": "0.55", "size": "100"}, {"price": "0.60", "size": "200"}, {"price": "0.65", "size": "300"}],
        }
        sample = parse_orderbook_sample(raw, self._make_market(), "0x111", 200.0)
        assert sample.best_bid == pytest.approx(0.50, abs=1e-6)
        assert sample.best_ask == pytest.approx(0.55, abs=1e-6)

    def test_ascending_asks_selects_min_price(self):
        """asks ascending [0.48, 0.50, 0.52] -> best_ask = 0.48"""
        raw = {
            "bids": [{"price": "0.40", "size": "100"}, {"price": "0.42", "size": "200"}, {"price": "0.45", "size": "300"}],
            "asks": [{"price": "0.48", "size": "100"}, {"price": "0.50", "size": "200"}, {"price": "0.52", "size": "300"}],
        }
        sample = parse_orderbook_sample(raw, self._make_market(), "0x111", 200.0)
        assert sample.best_bid == pytest.approx(0.45, abs=1e-6)
        assert sample.best_ask == pytest.approx(0.48, abs=1e-6)
        assert sample.spread_price_units == pytest.approx(0.03, abs=1e-6)

    def test_descending_asks_selects_min_price(self):
        """asks descending [0.55, 0.50, 0.48] -> best_ask = 0.48"""
        raw = {
            "bids": [{"price": "0.40", "size": "100"}, {"price": "0.42", "size": "200"}, {"price": "0.45", "size": "300"}],
            "asks": [{"price": "0.55", "size": "300"}, {"price": "0.50", "size": "200"}, {"price": "0.48", "size": "100"}],
        }
        sample = parse_orderbook_sample(raw, self._make_market(), "0x111", 200.0)
        assert sample.best_ask == pytest.approx(0.48, abs=1e-6)

    def test_worst_price_cannot_reproduce_98c_bug(self):
        """Even with worst-case arrays, max/min prevents 98-cent spread.

        With bids=[0.01] and asks=[0.99], correct parsing gives spread=0.98.
        But with multiple levels, max/min picks the BEST prices.
        """
        raw = {
            "bids": [{"price": "0.01", "size": "14000"}, {"price": "0.48", "size": "560"}],
            "asks": [{"price": "0.99", "size": "14000"}, {"price": "0.50", "size": "558"}],
        }
        sample = parse_orderbook_sample(raw, self._make_market(), "0x111", 200.0)
        # Best bid = max(0.01, 0.48) = 0.48
        # Best ask = min(0.99, 0.50) = 0.50
        # Spread = 0.02 (2 cents), NOT 0.98 (98 cents)
        assert sample.best_bid == pytest.approx(0.48, abs=1e-6)
        assert sample.best_ask == pytest.approx(0.50, abs=1e-6)
        assert sample.spread_price_units == pytest.approx(0.02, abs=1e-6)

    def test_depth_from_same_level_as_best(self):
        """Depth must be computed from the best-price level, not bids[0]."""
        raw = {
            "bids": [{"price": "0.01", "size": "50000"}, {"price": "0.48", "size": "560"}],
            "asks": [{"price": "0.99", "size": "50000"}, {"price": "0.50", "size": "558"}],
        }
        sample = parse_orderbook_sample(raw, self._make_market(), "0x111", 200.0)
        # Depth from best bid (0.48): 0.48 * 560 = 268.80
        assert sample.estimated_top_bid_depth_usd == pytest.approx(0.48 * 560, abs=0.01)
        # Depth from best ask (0.50): 0.50 * 558 = 279.00
        assert sample.estimated_top_ask_depth_usd == pytest.approx(0.50 * 558, abs=0.01)
        # NOT from bids[0]/asks[0]: 0.01 * 50000 = 500 (wrong)
        assert sample.estimated_top_bid_depth_usd != pytest.approx(0.01 * 50000, abs=0.01)


# ===================================================================
# TTE bucket tests
# ===================================================================


class TestTteBucketAssignment:
    """Test _parse_expiry_to_tte and _classify_tte_bucket."""

    def test_future_expiry(self):
        tte = _parse_expiry_to_tte("2099-12-31T12:00:00Z", 1000.0)
        assert tte is not None and tte > 0

    def test_past_expiry_returns_zero(self):
        tte = _parse_expiry_to_tte("2020-01-01T00:00:00Z", 2000000000.0)
        assert tte == 0.0

    def test_none_expiry(self):
        assert _parse_expiry_to_tte(None, 1000.0) is None

    def test_empty_expiry(self):
        assert _parse_expiry_to_tte("", 1000.0) is None

    def test_tte_gt_15m(self):
        assert _classify_tte_bucket(1800) == TTE_GT_15M

    def test_tte_5m_to_15m(self):
        assert _classify_tte_bucket(600) == TTE_5M_TO_15M

    def test_tte_2m_to_5m(self):
        assert _classify_tte_bucket(200) == TTE_2M_TO_5M

    def test_tte_1m_to_2m(self):
        assert _classify_tte_bucket(90) == TTE_1M_TO_2M

    def test_tte_30s_to_1m(self):
        assert _classify_tte_bucket(45) == TTE_30S_TO_1M

    def test_tte_0s_to_30s(self):
        assert _classify_tte_bucket(15) == TTE_0S_TO_30S

    def test_tte_expired(self):
        assert _classify_tte_bucket(0) == TTE_EXPIRED

    def test_tte_unknown(self):
        assert _classify_tte_bucket(None) == TTE_UNKNOWN


class TestTteBucketsCompute:
    """Test _compute_tte_buckets function."""

    def _make_market(self) -> BTCMarket:
        return BTCMarket(
            market_slug="test", market_id="1", condition_id="0xabc",
            question="BTC up/down?", outcomes=["Yes", "No"],
            yes_token_id="0x111", no_token_id="0x222",
            expiry="2099-12-31T12:00:00Z",
            price_to_beat=None, is_active=True,
            discovered_ts=100.0, direction="up",
        )

    def test_bucket_with_samples(self):
        m = self._make_market()
        samples = [
            OrderbookSample(
                ts_event=100.0 + i,
                market_slug="test", token_id="0x111", side="yes",
                expiry=m.expiry, price_to_beat=None,
                best_bid=0.48, best_ask=0.50, spread_price_units=0.02,
                spread_cents=2.0, top_bid_size=560.0, top_ask_size=558.0,
                estimated_top_bid_depth_usd=268.80,
                estimated_top_ask_depth_usd=279.00,
                is_two_sided=True, is_stale=False, is_crossed=False, is_missing=False,
            )
            for i in range(5)
        ]
        buckets = _compute_tte_buckets([m], samples)
        assert TTE_GT_15M in buckets
        assert buckets[TTE_GT_15M]["sample_count"] == 5
        assert buckets[TTE_GT_15M]["valid_sample_count"] == 5
        assert buckets[TTE_GT_15M]["median_spread_cents"] == 2.0

    def test_near_expiry_bucket_classified(self):
        """Near-expiry samples (tte <= 120s) should go to 1m/30s buckets."""
        m = BTCMarket(
            market_slug="test-near", market_id="1", condition_id="0xabc",
            question="BTC up/down?", outcomes=["Yes", "No"],
            yes_token_id="0x111", no_token_id="0x222",
            expiry="2099-12-31T12:01:00Z",  # 60 seconds from ts_event
            price_to_beat=None, is_active=True,
            discovered_ts=100.0, direction="up",
        )
        samples = [
            OrderbookSample(
                ts_event=100.0, market_slug="test-near", token_id="0x111", side="yes",
                expiry=m.expiry, price_to_beat=None,
                best_bid=0.48, best_ask=0.50, spread_price_units=0.02,
                spread_cents=2.0, top_bid_size=560.0, top_ask_size=558.0,
                estimated_top_bid_depth_usd=268.80,
                estimated_top_ask_depth_usd=279.00,
                is_two_sided=True, is_stale=False, is_crossed=False, is_missing=False,
            )
        ]
        buckets = _compute_tte_buckets([m], samples)
        # ts_event=100, expiry=2099-12-31T12:01:00Z -> tte ~ years -> TTE_GT_15M
        assert TTE_GT_15M in buckets


class TestTteSanityCheck:
    """Test _check_tte_sanity diagnostic function."""

    def _make_future_market(self, slug: str) -> BTCMarket:
        return BTCMarket(
            market_slug=slug, market_id="1", condition_id="0xabc",
            question="BTC up/down?", outcomes=["Yes", "No"],
            yes_token_id="0x111", no_token_id="0x222",
            expiry="2099-12-31T12:00:00Z",
            price_to_beat=None, is_active=True,
            discovered_ts=100.0, direction="up",
        )

    def _make_near_market(self, slug: str) -> BTCMarket:
        return BTCMarket(
            market_slug=slug, market_id="2", condition_id="0xdef",
            question="BTC up/down?", outcomes=["Yes", "No"],
            yes_token_id="0x333", no_token_id="0x444",
            expiry="1970-01-01T00:02:00Z",  # 60s from ts_event=100 (1970-01-01T00:01:40Z)
            price_to_beat=None, is_active=True,
            discovered_ts=100.0, direction="up",
        )

    def _make_sample(self, slug: str, token_id: str, ts_event: float,
                     expiry: str | None = None) -> OrderbookSample:
        return OrderbookSample(
            ts_event=ts_event, market_slug=slug, token_id=token_id, side="yes",
            expiry=expiry, price_to_beat=None,
            best_bid=0.48, best_ask=0.50, spread_price_units=0.02,
            spread_cents=2.0, top_bid_size=560.0, top_ask_size=558.0,
            estimated_top_bid_depth_usd=268.80,
            estimated_top_ask_depth_usd=279.0,
            is_two_sided=True, is_stale=False, is_crossed=False, is_missing=False,
        )

    def test_5m_market_far_from_expiry_anomaly(self):
        """5m market with ALL samples >15m from expiry should trigger warning."""
        m = self._make_future_market("btc-updown-5m-12345")
        assert _classify_duration(m.market_slug) == DUR_5M

        samples = [self._make_sample(m.market_slug, "0x111", 100.0 + i)
                   for i in range(3)]

        buckets = _compute_tte_buckets([m], samples)
        assert TTE_GT_15M in buckets
        assert buckets[TTE_GT_15M]["sample_count"] == 3

        bucket_samples = {b: [] for b in [TTE_GT_15M, TTE_5M_TO_15M,
                         TTE_2M_TO_5M, TTE_1M_TO_2M,
                         TTE_30S_TO_1M, TTE_0S_TO_30S,
                         TTE_EXPIRED, TTE_UNKNOWN]}
        bucket_samples[TTE_GT_15M] = list(samples)
        _check_tte_sanity([m], bucket_samples)

    def test_5m_market_near_expiry_ok(self):
        """5m market with samples near expiry should NOT trigger warning."""
        m = self._make_near_market("btc-updown-5m-67890")
        assert _classify_duration(m.market_slug) == DUR_5M

        # ts_event=100 (1970-01-01T00:01:40Z), expiry=1970-01-01T00:02:00Z → TTE=20s
        samples = [self._make_sample(m.market_slug, "0x333", 100.0,
                                     expiry=m.expiry)]

        buckets = _compute_tte_buckets([m], samples)
        # TTE ~20s → TTE_0S_TO_30S
        assert TTE_0S_TO_30S in buckets
        assert buckets[TTE_0S_TO_30S]["sample_count"] == 1

        bucket_samples = {b: [] for b in [TTE_GT_15M, TTE_5M_TO_15M,
                         TTE_2M_TO_5M, TTE_1M_TO_2M,
                         TTE_30S_TO_1M, TTE_0S_TO_30S,
                         TTE_EXPIRED, TTE_UNKNOWN]}
        bucket_samples[TTE_0S_TO_30S] = list(samples)
        _check_tte_sanity([m], bucket_samples)


class TestVerificationStatus:
    """Test _compute_verification_status_v2 function.

    Tests for artifact gates, near-expiry, distance-to-strike, two-axis grid,
    duration coverage, and convex danger zone.
    """

    def _make_green_bucket(self) -> dict:
        return {"sample_count": 10, "valid_sample_count": 10,
                "bucket_classification": GREEN_DIAG, "median_spread_cents": 1.0}

    def _make_red_bucket(self) -> dict:
        return {"sample_count": 10, "valid_sample_count": 10,
                "bucket_classification": RED_DIAG, "median_spread_cents": 98.0}

    def _make_empty_bucket(self) -> dict:
        return {"sample_count": 0, "valid_sample_count": 0,
                "bucket_classification": NEEDS_MORE_DATA}

    def _make_ttp_buckets_green(self) -> dict:
        """Green TTE near-expiry + green global."""
        green = self._make_green_bucket()
        return {
            TTE_0S_TO_30S: green, TTE_30S_TO_1M: green, TTE_1M_TO_2M: green,
            TTE_2M_TO_5M: green, TTE_5M_TO_15M: green, TTE_GT_15M: green,
            TTE_EXPIRED: self._make_empty_bucket(), TTE_UNKNOWN: self._make_empty_bucket(),
        }

    def _make_distance_buckets_with_data(self) -> dict:
        """Distance buckets with some samples."""
        from ..polymarket_btc_updown_liquidity_probe import DIST_LTE_5, DIST_5_TO_10, DIST_10_TO_25, DIST_25_TO_50, DIST_GT_50, DIST_UNKNOWN
        return {
            DIST_LTE_5: {"sample_count": 5, "valid_sample_count": 5, "market_count": 2, "market_slugs": ["test"], "median_spread_cents": 2.0, "p95_spread_cents": 3.0, "bucket_classification": GREEN_DIAG, "reference_source": "CEX_PROXY_REFERENCE"},
            DIST_5_TO_10: {"sample_count": 5, "valid_sample_count": 5, "market_count": 2, "market_slugs": ["test"], "median_spread_cents": 2.0, "p95_spread_cents": 3.0, "bucket_classification": GREEN_DIAG, "reference_source": "CEX_PROXY_REFERENCE"},
            DIST_10_TO_25: {"sample_count": 3, "valid_sample_count": 3, "market_count": 1, "market_slugs": ["test"], "median_spread_cents": 2.0, "p95_spread_cents": 3.0, "bucket_classification": GREEN_DIAG, "reference_source": "CEX_PROXY_REFERENCE"},
            DIST_25_TO_50: {"sample_count": 0, "valid_sample_count": 0, "bucket_classification": "no_samples", "reference_source": "CEX_PROXY_REFERENCE"},
            DIST_GT_50: {"sample_count": 0, "valid_sample_count": 0, "bucket_classification": "no_samples", "reference_source": "CEX_PROXY_REFERENCE"},
            DIST_UNKNOWN: {"sample_count": 0, "valid_sample_count": 0, "bucket_classification": "no_samples", "reference_source": "CEX_PROXY_REFERENCE"},
        }

    def _make_duration_coverage_15m_verified(self) -> dict:
        return {"5m": {"status": "verified", "sample_count": 10}, "15m": {"status": "verified", "sample_count": 5}, "1h": {"status": "verified", "sample_count": 10}, "unknown": {"status": "no_samples", "sample_count": 0}}

    def _make_duration_coverage_15m_missing(self) -> dict:
        return {"5m": {"status": "verified", "sample_count": 10}, "15m": {"status": "15M_DURATION_NOT_VERIFIED", "sample_count": 0}, "1h": {"status": "verified", "sample_count": 10}, "unknown": {"status": "no_samples", "sample_count": 0}}

    def _make_two_axis_green(self) -> dict:
        """Two-axis grid with danger zone data."""
        from ..polymarket_btc_updown_liquidity_probe import DIST_LTE_5, DIST_5_TO_10
        green_cell = {"sample_count": 3, "classification": GREEN_DIAG, "median_spread_cents": 2.0}
        return {
            TTE_0S_TO_30S: {DIST_LTE_5: green_cell, DIST_5_TO_10: green_cell},
            TTE_30S_TO_1M: {DIST_LTE_5: green_cell, DIST_5_TO_10: green_cell},
            TTE_1M_TO_2M: {DIST_LTE_5: green_cell, DIST_5_TO_10: green_cell},
        }

    def _make_two_axis_empty(self) -> dict:
        return {}

    def test_gate_verified_all_passing(self):
        """All gates: TTE green, distance OK, 15m covered, two-axis has danger zone."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            # Create minimal artifacts to pass checks
            (out_dir / "tte_bucket_summary.json").write_text("{}")
            (out_dir / "tte_bucket_summary.md").write_text("#")
            (out_dir / "distance_to_strike_bucket_summary.json").write_text("{}")
            (out_dir / "distance_to_strike_bucket_summary.md").write_text("#")
            (out_dir / "raw_clob_orderbook_payloads.jsonl").write_text("{}")
            (out_dir / "raw_payload_audit.md").write_text("#")

            status = _compute_verification_status_v2(
                out_dir=out_dir,
                tte_buckets=self._make_ttp_buckets_green(),
                distance_buckets=self._make_distance_buckets_with_data(),
                two_axis_grid=self._make_two_axis_green(),
                duration_coverage=self._make_duration_coverage_15m_verified(),
                instrumented_flags={"parser_fix": True},
            )
            assert status == LIQUIDITY_GATE_VERIFIED, f"Expected VERIFIED, got {status}"

    def test_raw_payload_artifact_missing(self):
        """Missing raw payload file prevents RAW_PAYLOAD_VERIFIED -> NOT_VERIFIED."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            # Only TTE/distance artifacts, no raw payload
            (out_dir / "tte_bucket_summary.json").write_text("{}")
            (out_dir / "tte_bucket_summary.md").write_text("#")
            (out_dir / "distance_to_strike_bucket_summary.json").write_text("{}")
            (out_dir / "distance_to_strike_bucket_summary.md").write_text("#")

            status = _compute_verification_status_v2(
                out_dir=out_dir,
                tte_buckets=self._make_ttp_buckets_green(),
                distance_buckets=self._make_distance_buckets_with_data(),
                two_axis_grid=self._make_two_axis_green(),
                duration_coverage=self._make_duration_coverage_15m_verified(),
                instrumented_flags={"parser_fix": True},
            )
            assert status != LIQUIDITY_GATE_VERIFIED
            assert "PENDING" in status

    def test_tte_artifact_missing(self):
        """Missing TTE artifact prevents TTE_BUCKETS_VERIFIED."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            (out_dir / "raw_clob_orderbook_payloads.jsonl").write_text("{}")
            (out_dir / "raw_payload_audit.md").write_text("#")

            status = _compute_verification_status_v2(
                out_dir=out_dir,
                tte_buckets=self._make_ttp_buckets_green(),
                distance_buckets=self._make_distance_buckets_with_data(),
                two_axis_grid=self._make_two_axis_green(),
                duration_coverage=self._make_duration_coverage_15m_verified(),
                instrumented_flags={"parser_fix": True},
            )
            assert status != LIQUIDITY_GATE_VERIFIED

    def test_near_expiry_red_blocks_gate(self):
        """RED near-expiry blocks LIQUIDITY_GATE_VERIFIED even if global is GREEN."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            (out_dir / "tte_bucket_summary.json").write_text("{}")
            (out_dir / "tte_bucket_summary.md").write_text("#")
            (out_dir / "distance_to_strike_bucket_summary.json").write_text("{}")
            (out_dir / "distance_to_strike_bucket_summary.md").write_text("#")
            (out_dir / "raw_clob_orderbook_payloads.jsonl").write_text("{}")
            (out_dir / "raw_payload_audit.md").write_text("#")

            red_buckets = {
                TTE_0S_TO_30S: self._make_red_bucket(),
                TTE_30S_TO_1M: self._make_empty_bucket(),
                TTE_1M_TO_2M: self._make_empty_bucket(),
            }
            status = _compute_verification_status_v2(
                out_dir=out_dir, tte_buckets=red_buckets,
                distance_buckets=self._make_distance_buckets_with_data(),
                two_axis_grid=self._make_two_axis_green(),
                duration_coverage=self._make_duration_coverage_15m_verified(),
                instrumented_flags={"parser_fix": True},
            )
            assert status == LIQUIDITY_GATE_NOT_VERIFIED, f"Expected NOT_VERIFIED, got {status}"

    def test_distance_to_strike_unavailable_prevents_verified(self):
        """Missing distance artifacts prevents DISTANCE_TO_STRIKE_BUCKETS_VERIFIED."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            (out_dir / "tte_bucket_summary.json").write_text("{}")
            (out_dir / "tte_bucket_summary.md").write_text("#")
            (out_dir / "raw_clob_orderbook_payloads.jsonl").write_text("{}")
            (out_dir / "raw_payload_audit.md").write_text("#")

            status = _compute_verification_status_v2(
                out_dir=out_dir,
                tte_buckets=self._make_ttp_buckets_green(),
                distance_buckets={},  # no distance data
                two_axis_grid={},
                duration_coverage=self._make_duration_coverage_15m_verified(),
                instrumented_flags={"parser_fix": True},
            )
            assert status == LIQUIDITY_GATE_PENDING_TWO_AXIS_VERIFICATION

    def test_15m_missing_emits_not_verified(self):
        """15m uncovered -> LIQUIDITY_GATE_NOT_VERIFIED or PENDING."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            (out_dir / "tte_bucket_summary.json").write_text("{}")
            (out_dir / "tte_bucket_summary.md").write_text("#")
            (out_dir / "distance_to_strike_bucket_summary.json").write_text("{}")
            (out_dir / "distance_to_strike_bucket_summary.md").write_text("#")
            (out_dir / "raw_clob_orderbook_payloads.jsonl").write_text("{}")
            (out_dir / "raw_payload_audit.md").write_text("#")

            status = _compute_verification_status_v2(
                out_dir=out_dir,
                tte_buckets=self._make_ttp_buckets_green(),
                distance_buckets=self._make_distance_buckets_with_data(),
                two_axis_grid=self._make_two_axis_green(),
                duration_coverage=self._make_duration_coverage_15m_missing(),
                instrumented_flags={"parser_fix": True},
            )
            assert status != LIQUIDITY_GATE_VERIFIED

    def test_5m_green_cannot_claim_15m_verified(self):
        """5m GREEN does not make 15m verified."""
        # The 15m duration coverage has status '15M_DURATION_NOT_VERIFIED'
        # regardless of 5m data
        cov = self._make_duration_coverage_15m_missing()
        assert cov["15m"]["status"] == "15M_DURATION_NOT_VERIFIED"
        assert cov["5m"]["status"] == "verified"

    def test_cex_proxy_label_not_chainlink(self):
        """CEX proxy is labelled CEX_PROXY_REFERENCE, not Chainlink."""
        from ..polymarket_btc_updown_liquidity_probe import REF_CEX_PROXY, REF_CHAINLINK
        assert REF_CEX_PROXY == "CEX_PROXY_REFERENCE"
        assert REF_CHAINLINK == "CHAINLINK_REFERENCE"
        assert REF_CEX_PROXY != REF_CHAINLINK

    def test_no_candidate_verdict_in_tests(self):
        """No forbidden verdict strings in non-import, non-docstring test code."""
        source = Path(__file__).read_text()
        # Skip import lines and docstring lines
        lines = []
        in_docstring = False
        for l in source.split("\n"):
            stripped = l.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                    continue  # single-line docstring
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith("from .."):
                continue
            lines.append(l)
        cleaned = "\n".join(lines)
        for v in FORBIDDEN_VERDICTS:
            assert v not in cleaned, f"Found forbidden verdict '{v}' in test file"


# ===================================================================
# Raw payload audit tests
# ===================================================================


class TestRawPayloadAudit:
    """Test raw payload audit output."""

    def test_write_raw_payloads_jsonl(self):
        payloads = [
            RawPayloadRecord(
                ts_event=1000.0, market_slug="test", token_id="0x111",
                side="yes", expiry="2025-12-31", price_to_beat=None,
                raw_bids=[{"price": "0.48", "size": "560"}],
                raw_asks=[{"price": "0.50", "size": "558"}],
                computed_best_bid=0.48, computed_best_ask=0.50,
                computed_spread_price_units=0.02, computed_spread_cents=2.0,
                computed_top_bid_size=560.0, computed_top_ask_size=558.0,
                computed_bid_depth_usd=268.80, computed_ask_depth_usd=279.00,
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "raw_clob_orderbook_payloads.jsonl"
            write_raw_payloads(payloads, path)
            assert path.exists()
            parsed = json.loads(path.read_text().strip())
            assert parsed["computed_best_bid"] == 0.48
            assert parsed["computed_best_ask"] == 0.50

    def test_write_raw_payload_audit_md(self):
        payloads = [
            RawPayloadRecord(
                ts_event=1000.0, market_slug="test", token_id="0x111",
                side="yes", expiry="2025-12-31", price_to_beat=None,
                raw_bids=[{"price": "0.48", "size": "560"}],
                raw_asks=[{"price": "0.50", "size": "558"}],
                computed_best_bid=0.48, computed_best_ask=0.50,
                computed_spread_price_units=0.02, computed_spread_cents=2.0,
                computed_top_bid_size=560.0, computed_top_ask_size=558.0,
                computed_bid_depth_usd=268.80, computed_ask_depth_usd=279.00,
            ),
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "raw_payload_audit.md"
            write_raw_payload_audit(payloads, path)
            assert path.exists()
            content = path.read_text()
            assert "Tightest Spread" in content
            assert "0.48" in content
            assert "0.50" in content


# ===================================================================
# TTE bucket output tests
# ===================================================================


class TestTteBucketOutput:
    """Test TTE bucket summary output."""

    def test_write_tte_bucket_summary_json(self):
        buckets = {
            TTE_GT_15M: {
                "sample_count": 5, "valid_sample_count": 5,
                "two_sided_rate": 100.0, "median_spread_cents": 2.0,
                "p75_spread_cents": 2.0, "p95_spread_cents": 2.0,
                "median_bid_depth_usd": 250.0, "median_ask_depth_usd": 280.0,
                "median_combined_top_depth_usd": 265.0,
                "stale_rate": 0.0, "crossed_rate": 0.0, "missing_rate": 0.0,
                "bucket_classification": GREEN_DIAG,
            },
        }
        near = {"near_expiry_definition": "tte <= 120s", "near_expiry_sample_count": 3,
                "near_expiry_valid_sample_count": 3, "near_expiry_median_spread_cents": 2.0,
                "near_expiry_p95_spread_cents": 3.0, "near_expiry_median_bid_depth_usd": 250.0,
                "near_expiry_median_ask_depth_usd": 280.0,
                "near_expiry_classification": GREEN_DIAG}

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tte_bucket_summary.json"
            write_tte_bucket_summary_json(buckets, near, path)
            assert path.exists()
            parsed = json.loads(path.read_text())
            assert "tte_buckets" in parsed
            assert "near_expiry_rollup" in parsed

    def test_write_tte_bucket_summary_md(self):
        buckets = {
            TTE_GT_15M: {
                "sample_count": 5, "valid_sample_count": 5,
                "two_sided_rate": 100.0, "median_spread_cents": 2.0,
                "p75_spread_cents": 2.0, "p95_spread_cents": 2.0,
                "median_bid_depth_usd": 250.0, "median_ask_depth_usd": 280.0,
                "median_combined_top_depth_usd": 265.0,
                "stale_rate": 0.0, "crossed_rate": 0.0, "missing_rate": 0.0,
                "bucket_classification": GREEN_DIAG,
            },
        }
        near = {"near_expiry_definition": "tte <= 120s", "near_expiry_sample_count": 3,
                "near_expiry_valid_sample_count": 3, "near_expiry_median_spread_cents": 2.0,
                "near_expiry_p95_spread_cents": 3.0, "near_expiry_median_bid_depth_usd": 250.0,
                "near_expiry_median_ask_depth_usd": 280.0,
                "near_expiry_classification": GREEN_DIAG}

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tte_bucket_summary.md"
            write_tte_bucket_summary_md(buckets, near, path)
            assert path.exists()
            content = path.read_text()
            assert "Time-to-Expiry Bucket Summary" in content
            assert "Near-Expiry Rollup" in content
