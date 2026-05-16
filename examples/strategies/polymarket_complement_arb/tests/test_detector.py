"""
Tests for the detector module.
"""

import time

import pytest

from examples.strategies.polymarket_complement_arb.config import ComplementArbConfig
from examples.strategies.polymarket_complement_arb.detector import (
    compute_target_maker_bids,
    detect_opportunity,
    _is_book_stale,
    _round_down,
    _round_up,
)
from examples.strategies.polymarket_complement_arb.models import (
    BookSnapshot,
    ComplementBookState,
    ComplementMarket,
)


def test_no_midpoint_use():
    """Detector must use top-of-book prices, not midpoint."""
    config = ComplementArbConfig()
    yes_book = BookSnapshot(
        instrument_id_str="c1-tok_yes.POLYMARKET",
        token_id="tok_yes",
        bids=[(0.49, 100.0)],
        asks=[(0.51, 100.0)],
        timestamp_ms=time.time() * 1000,
    )
    no_book = BookSnapshot(
        instrument_id_str="c1-tok_no.POLYMARKET",
        token_id="tok_no",
        bids=[(0.48, 100.0)],
        asks=[(0.52, 100.0)],
        timestamp_ms=time.time() * 1000,
    )
    market = ComplementMarket(
        condition_id="c1", market_slug="test", event_slug="test-event",
        question="Test?", yes_token_id="tok_yes", no_token_id="tok_no",
        yes_instrument_id_str="c1-tok_yes.POLYMARKET",
        no_instrument_id_str="c1-tok_no.POLYMARKET",
        neg_risk=False, active=True, closed=False, accepting_orders=True,
        end_date_iso=None, minimum_tick_size=0.001, minimum_order_size=5,
        taker_fee_rate=0.03, category="crypto", liquidity_num=1000.0, volume_num=500.0,
    )
    state = ComplementBookState(
        condition_id="c1", market_slug="test",
        yes_book=yes_book, no_book=no_book,
        ts_event_ns=time.time_ns(),
    )
    diag = detect_opportunity(state, market, config, now_ms=time.time() * 1000)
    assert diag is not None
    # Should use bid prices (0.49, 0.48), not midpoint (0.50, 0.50)
    assert diag.yes_bid == 0.49
    assert diag.no_bid == 0.48


def test_stale_data_rejects():
    config = ComplementArbConfig(max_book_age_ms=100)
    yes_book = BookSnapshot(
        instrument_id_str="c1-tok_yes.POLYMARKET",
        token_id="tok_yes",
        bids=[(0.49, 100.0)], asks=[(0.51, 100.0)],
        timestamp_ms=time.time() * 1000 - 10000,  # 10 seconds stale
    )
    no_book = BookSnapshot(
        instrument_id_str="c1-tok_no.POLYMARKET",
        token_id="tok_no",
        bids=[(0.48, 100.0)], asks=[(0.52, 100.0)],
        timestamp_ms=time.time() * 1000 - 10000,
    )
    market = ComplementMarket(
        condition_id="c1", market_slug="test", event_slug="test-event",
        question="Test?", yes_token_id="tok_yes", no_token_id="tok_no",
        yes_instrument_id_str="c1-tok_yes.POLYMARKET",
        no_instrument_id_str="c1-tok_no.POLYMARKET",
        neg_risk=False, active=True, closed=False, accepting_orders=True,
        end_date_iso=None, minimum_tick_size=0.001, minimum_order_size=5,
        taker_fee_rate=0.03, category="crypto", liquidity_num=1000.0, volume_num=500.0,
    )
    state = ComplementBookState(
        condition_id="c1", market_slug="test",
        yes_book=yes_book, no_book=no_book,
        ts_event_ns=time.time_ns(),
    )
    diag = detect_opportunity(state, market, config, now_ms=time.time() * 1000)
    assert diag is None  # Stale


def test_insufficient_depth_rejects():
    config = ComplementArbConfig(min_top_depth_usdc=100.0)
    yes_book = BookSnapshot(
        instrument_id_str="c1-tok_yes.POLYMARKET",
        token_id="tok_yes",
        bids=[(0.49, 0.001)], asks=[(0.51, 0.001)],  # Tiny depth
        timestamp_ms=time.time() * 1000,
    )
    no_book = BookSnapshot(
        instrument_id_str="c1-tok_no.POLYMARKET",
        token_id="tok_no",
        bids=[(0.48, 0.001)], asks=[(0.52, 0.001)],
        timestamp_ms=time.time() * 1000,
    )
    market = ComplementMarket(
        condition_id="c1", market_slug="test", event_slug="test-event",
        question="Test?", yes_token_id="tok_yes", no_token_id="tok_no",
        yes_instrument_id_str="c1-tok_yes.POLYMARKET",
        no_instrument_id_str="c1-tok_no.POLYMARKET",
        neg_risk=False, active=True, closed=False, accepting_orders=True,
        end_date_iso=None, minimum_tick_size=0.001, minimum_order_size=5,
        taker_fee_rate=0.03, category="crypto", liquidity_num=1000.0, volume_num=500.0,
    )
    state = ComplementBookState(
        condition_id="c1", market_slug="test",
        yes_book=yes_book, no_book=no_book,
        ts_event_ns=time.time_ns(),
    )
    diag = detect_opportunity(state, market, config, now_ms=time.time() * 1000)
    assert diag is None or not diag.depth_ok


def test_one_sided_data_rejects():
    config = ComplementArbConfig()
    yes_book = BookSnapshot(
        instrument_id_str="c1-tok_yes.POLYMARKET",
        token_id="tok_yes",
        bids=[], asks=[(0.51, 100.0)],  # No bids
        timestamp_ms=time.time() * 1000,
    )
    no_book = BookSnapshot(
        instrument_id_str="c1-tok_no.POLYMARKET",
        token_id="tok_no",
        bids=[(0.48, 100.0)], asks=[(0.52, 100.0)],
        timestamp_ms=time.time() * 1000,
    )
    market = ComplementMarket(
        condition_id="c1", market_slug="test", event_slug="test-event",
        question="Test?", yes_token_id="tok_yes", no_token_id="tok_no",
        yes_instrument_id_str="c1-tok_yes.POLYMARKET",
        no_instrument_id_str="c1-tok_no.POLYMARKET",
        neg_risk=False, active=True, closed=False, accepting_orders=True,
        end_date_iso=None, minimum_tick_size=0.001, minimum_order_size=5,
        taker_fee_rate=0.03, category="crypto", liquidity_num=1000.0, volume_num=500.0,
    )
    state = ComplementBookState(
        condition_id="c1", market_slug="test",
        yes_book=yes_book, no_book=no_book,
        ts_event_ns=time.time_ns(),
    )
    diag = detect_opportunity(state, market, config, now_ms=time.time() * 1000)
    assert diag is None


def test_rounded_prices_preserve_edge():
    """Round-down must not destroy the edge."""
    config = ComplementArbConfig()
    yes_book = BookSnapshot(
        instrument_id_str="c1-tok_yes.POLYMARKET",
        token_id="tok_yes",
        bids=[(0.491, 100.0)], asks=[(0.50, 100.0)],
        timestamp_ms=time.time() * 1000,
    )
    no_book = BookSnapshot(
        instrument_id_str="c1-tok_no.POLYMARKET",
        token_id="tok_no",
        bids=[(0.491, 100.0)], asks=[(0.50, 100.0)],
        timestamp_ms=time.time() * 1000,
    )
    target_yes, target_no = compute_target_maker_bids(yes_book, no_book, config)
    assert target_yes is not None
    assert target_no is not None
    # After round-down, sum should still be < 1.0
    assert target_yes + target_no < 1.0


def test_round_down():
    assert _round_down(0.123456, 0.001) == 0.123
    assert _round_down(0.123956, 0.001) == 0.123
    assert _round_down(0.5, 0.01) == 0.5
    assert _round_down(0.501, 0.01) == 0.5


def test_round_up():
    assert _round_up(0.124, 0.001) == 0.124  # Already on tick
    assert _round_up(0.123456, 0.001) == 0.124
    assert _round_up(0.5, 0.01) == 0.5
    assert _round_up(0.5, 0.001) == 0.5


def test_is_book_stale():
    now = 1000000.0
    stale_snap = BookSnapshot("", "t", [(1, 1)], [(1, 1)], timestamp_ms=now - 1000)
    fresh_snap = BookSnapshot("", "t", [(1, 1)], [(1, 1)], timestamp_ms=now - 10)
    assert _is_book_stale(stale_snap, 100, now) is True
    assert _is_book_stale(fresh_snap, 100, now) is False
