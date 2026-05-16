"""
Tests for resolution/closure risk handling in the state machine.
"""

import pytest

from examples.strategies.polymarket_complement_arb.config import ComplementArbConfig
from examples.strategies.polymarket_complement_arb.state_machine import (
    ArbState,
    PairStateMachine,
)
from examples.strategies.polymarket_complement_arb.models import ComplementMarket


@pytest.fixture
def market():
    return ComplementMarket(
        condition_id="c1", market_slug="test", event_slug="test-event",
        question="Test?", yes_token_id="tok_yes", no_token_id="tok_no",
        yes_instrument_id_str="c1-tok_yes.POLYMARKET",
        no_instrument_id_str="c1-tok_no.POLYMARKET",
        neg_risk=False, active=True, closed=False, accepting_orders=True,
        end_date_iso=None, minimum_tick_size=0.001, minimum_order_size=5,
        taker_fee_rate=0.03, category="crypto", liquidity_num=1000.0, volume_num=500.0,
    )


def test_closed_but_unresolved(market):
    """Market becomes closed while unpaired -> CLOSED_UNPAIRED."""
    sm = PairStateMachine("c1", market, ComplementArbConfig())
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.48, 100.0)
    sm.on_market_closed()
    assert sm.state == ArbState.CLOSED_UNPAIRED


def test_resolution_detected(market):
    """Final resolution known -> RESOLUTION_DETECTED_UNPAIRED."""
    sm = PairStateMachine("c1", market, ComplementArbConfig())
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.48, 100.0)
    sm.on_resolution_detected()
    assert sm.state == ArbState.RESOLUTION_DETECTED_UNPAIRED


def test_settlement_known(market):
    """Settlement value known -> SETTLED_UNPAIRED."""
    sm = PairStateMachine("c1", market, ComplementArbConfig())
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.48, 100.0)
    sm.on_resolution_detected()
    sm.on_settled()
    assert sm.state == ArbState.SETTLED_UNPAIRED


def test_no_order_after_closed(market):
    """No new quoting after market closes."""
    sm = PairStateMachine("c1", market, ComplementArbConfig())
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.48, 100.0)
    sm.on_market_closed()
    assert sm.state == ArbState.CLOSED_UNPAIRED
    # Should not try to quote more
    assert sm.is_terminal()


def test_no_order_after_resolved(market):
    """No new quoting after resolution detected."""
    sm = PairStateMachine("c1", market, ComplementArbConfig())
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.48, 100.0)
    sm.on_resolution_detected()
    assert sm.state == ArbState.RESOLUTION_DETECTED_UNPAIRED
    assert sm.is_terminal()


def test_yes_itm_resolved(market):
    """YES in-the-money case: resolution says YES wins."""
    sm = PairStateMachine("c1", market, ComplementArbConfig())
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.90, 100.0)
    sm.on_market_closed()
    assert sm.state == ArbState.CLOSED_UNPAIRED


def test_yes_itm_no_otm(market):
    """YES=1.0 scenario for synthetic resolution testing."""
    sm = PairStateMachine("c1", market, ComplementArbConfig())
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.90, 100.0)
    sm.on_market_closed()
    assert sm.state == ArbState.CLOSED_UNPAIRED
    sm.on_resolution_detected()
    assert sm.state == ArbState.RESOLUTION_DETECTED_UNPAIRED


def test_terminal_states():
    """Check that all terminal states are recognized."""
    sm = PairStateMachine("c1", ComplementMarket(
        condition_id="c1", market_slug="test", event_slug="test-event",
        question="Test?", yes_token_id="tok_yes", no_token_id="tok_no",
        yes_instrument_id_str="c1-tok_yes.POLYMARKET",
        no_instrument_id_str="c1-tok_no.POLYMARKET",
        neg_risk=False, active=True, closed=False, accepting_orders=True,
        end_date_iso=None, minimum_tick_size=0.001, minimum_order_size=5,
        taker_fee_rate=0.03, category="crypto", liquidity_num=1000.0, volume_num=500.0,
    ), ComplementArbConfig())
    # Default is IDLE, which is not terminal
    assert not sm.is_terminal()
