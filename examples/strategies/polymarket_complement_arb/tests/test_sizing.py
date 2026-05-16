"""
Tests for the sizing module.
"""

import pytest

from examples.strategies.polymarket_complement_arb.config import ComplementArbConfig
from examples.strategies.polymarket_complement_arb.models import BookSnapshot, OpportunityDiagnostic
from examples.strategies.polymarket_complement_arb.sizing import compute_quote_size


def _make_diagnostic(
    yes_ask=0.52, no_ask=0.52, yes_bid=0.48, no_bid=0.48,
    gross_gap=0.04,
):
    return OpportunityDiagnostic(
        condition_id="c1", market_slug="test", question="Test?",
        yes_bid=yes_bid, yes_ask=yes_ask,
        no_bid=no_bid, no_ask=no_ask,
        yes_top_bid_size=100.0, yes_top_ask_size=100.0,
        no_top_bid_size=100.0, no_top_ask_size=100.0,
        gross_gap=gross_gap,
        taker_fee_rate=0.03,
        taker_fee_yes=0.0, taker_fee_no=0.0,
        total_taker_fee=0.0,
        leg_risk_buffer=0.0, signing_latency_buffer=0.0,
        gas_redeem_buffer=0.0,
        total_cost=0.0, net_edge=0.0,
        maker_gate_pass=True, taker_diagnostic_pass=True,
        depth_ok=True, stale=False,
        ts_event_ns=0,
    )


def test_compute_quote_size_basic():
    config = ComplementArbConfig(max_order_usdc=100.0)
    diag = _make_diagnostic(yes_ask=0.50, no_ask=0.50)
    yes_book = BookSnapshot("", "t", [(0.48, 100)], [(0.50, 200)], 0)
    no_book = BookSnapshot("", "t", [(0.48, 100)], [(0.50, 200)], 0)
    qty, reason = compute_quote_size(diag, yes_book, no_book, config)
    assert qty is not None and qty > 0
    assert reason is None


def test_negative_rejection():
    config = ComplementArbConfig(max_order_usdc=0)
    diag = _make_diagnostic(yes_ask=0.50, no_ask=0.50)
    qty, reason = compute_quote_size(diag, None, None, config)
    assert qty is None
    assert reason is not None


def test_depth_constraint():
    """Size should be bound by available depth."""
    config = ComplementArbConfig(max_order_usdc=1000.0)
    diag = _make_diagnostic(yes_ask=0.50, no_ask=0.50)
    yes_book = BookSnapshot("", "t", [(0.48, 100)], [(0.50, 2)], 0)  # Only 2 shares at ask
    no_book = BookSnapshot("", "t", [(0.48, 100)], [(0.50, 100)], 0)
    qty, reason = compute_quote_size(diag, yes_book, no_book, config)
    assert qty is not None
    # Should be bound by depth (2 shares at YES ask)
    assert qty <= 2


def test_total_open_cap():
    config = ComplementArbConfig(max_order_usdc=100.0, max_total_open_usdc=50.0)
    diag = _make_diagnostic(yes_ask=0.50, no_ask=0.50)
    qty, reason = compute_quote_size(diag, None, None, config, current_total_open_usdc=40.0)
    assert qty is None or reason is not None
    if qty is None:
        assert reason is not None


def test_unpaired_exposure_cap():
    config = ComplementArbConfig(
        max_order_usdc=100.0, max_total_open_usdc=500.0,
        max_unpaired_exposure_usdc=10.0,
    )
    diag = _make_diagnostic(yes_ask=0.50, no_ask=0.50)
    qty, reason = compute_quote_size(
        diag, None, None, config,
        current_total_open_usdc=0.0,
        current_unpaired_exposure_usdc=5.0,
    )
    assert qty is None or reason is not None


def test_respects_min_order():
    config = ComplementArbConfig(
        max_order_usdc=100.0, min_order_usdc=1000.0,
    )
    diag = _make_diagnostic(yes_ask=0.50, no_ask=0.50)
    qty, reason = compute_quote_size(diag, None, None, config)
    assert qty is None
    assert reason is not None
