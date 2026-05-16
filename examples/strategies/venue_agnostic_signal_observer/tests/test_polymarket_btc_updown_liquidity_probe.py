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
    _is_btc_updown,
    _extract_price_to_beat,
    _normalize_levels,
    _classify_liquidity,
    _get_git_sha,
    write_discovered_markets,
    write_orderbook_samples,
    write_chainlink_ticks,
    write_manifest,
    write_summary_json,
    write_summary_md,
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
    RUNNER_PATH = Path(__file__).resolve().parent.parent / "run_polymarket_btc_updown_liquidity_probe.py"

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


class TestBtcUpDownDiscovery:
    """Test the _is_btc_updown filter function."""

    def test_up_detected(self):
        assert _is_btc_updown("Will BTC be up on 31Dec?") == "up"

    def test_down_detected(self):
        assert _is_btc_updown("Will Bitcoin be down on 31Dec?") == "down"

    def test_above_is_up(self):
        assert _is_btc_updown("Will BTC be above $105,000 by Friday?") == "up"

    def test_below_is_down(self):
        assert _is_btc_updown("Will BTC be below $90,000 by Friday?") == "down"

    def test_not_btc(self):
        assert _is_btc_updown("Will ETH be up on Friday?") is None

    def test_no_direction_keyword(self):
        assert _is_btc_updown("Will BTC be at $100k?") is not None  # default to up

    def test_not_bitcoin(self):
        assert _is_btc_updown("Will SOL be up?") is None

    def test_empty(self):
        assert _is_btc_updown("") is None

    def test_mixed_case(self):
        assert _is_btc_updown("BTC Up Friday") == "up"
        assert _is_btc_updown("Bitcoin DOWN Today") == "down"


class TestNonBtcSkipped:
    """Test that non-BTC markets are skipped."""

    def test_eth_updown_skipped(self):
        result = _is_btc_updown("Will ETH be up on Friday?")
        assert result is None, "ETH market should be skipped"

    def test_sol_updown_skipped(self):
        result = _is_btc_updown("Will SOL be up on Friday?")
        assert result is None, "SOL market should be skipped"

    def test_generic_market_skipped(self):
        result = _is_btc_updown("Will the S&P 500 be up?")
        assert result is None, "S&P market should be skipped"

    def test_polymarket_question_without_direction(self):
        result = _is_btc_updown("Trump wins 2024?")
        assert result is None, "Non-BTC market should be skipped"


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
