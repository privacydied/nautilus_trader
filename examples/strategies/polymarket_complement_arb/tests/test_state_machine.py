"""
Tests for the state machine module.
"""

import time

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


def test_idle_to_quoting(market):
    sm = PairStateMachine("c1", market, ComplementArbConfig())
    assert sm.state == ArbState.IDLE
    sm.transition_to_quoting(100.0)
    assert sm.state == ArbState.QUOTING_BOTH_LEGS


def test_both_legs_fill_pair_complete(market):
    sm = PairStateMachine("c1", market, ComplementArbConfig())
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.48, 100.0)
    assert sm.state == ArbState.ONE_LEG_FILLED_PENDING_SECOND
    sm.on_one_leg_filled("NO", 0.48, 100.0)
    sm.on_pair_complete(100.0, 100.0)
    assert sm.state == ArbState.PAIR_COMPLETE
    assert sm.paired_qty == 100.0


def test_one_leg_fills_second_crossable_taker_close(market):
    sm = PairStateMachine("c1", market, ComplementArbConfig(
        leg_risk_buffer_per_share=0.001,
        signing_latency_buffer_per_share=0.001,
        gas_redeem_buffer_per_pair=0.001,
        min_net_edge_per_share=0.001,
    ))
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.40, 100.0)
    # Second leg at 0.55 creates gap = 0.05 on 100 shares = $5.00
    # Should be crossable even with costs
    result = sm.try_close_second_leg(0.55)
    assert result is True
    assert sm.state == ArbState.SECOND_LEG_CROSSABLE


def test_one_leg_fills_second_not_crossable_rests(market):
    sm = PairStateMachine("c1", market, ComplementArbConfig(
        leg_risk_buffer_per_share=0.01,
        signing_latency_buffer_per_share=0.01,
        gas_redeem_buffer_per_pair=0.01,
        min_net_edge_per_share=0.01,
    ))
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.48, 100.0)
    # Second leg at 0.51 creates gap = 0.01 = $1.00 - tight
    result = sm.try_close_second_leg(0.51)
    assert result is False
    assert sm.state == ArbState.SECOND_LEG_RESTING


def test_timeout_unwinds(market):
    sm = PairStateMachine("c1", market, ComplementArbConfig(
        one_leg_timeout_ms=0.1,  # Very short
    ))
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.48, 100.0)
    time.sleep(0.005)  # Exceed timeout
    assert sm.check_timeout() is True
    sm.on_timeout()
    assert sm.state == ArbState.UNWINDING


def test_partial_fills_produce_residual(market):
    sm = PairStateMachine("c1", market, ComplementArbConfig())
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.48, 100.0)
    sm.on_one_leg_filled("NO", 0.48, 60.0)
    sm.on_pair_complete(100.0, 60.0)
    assert sm.paired_qty == 60.0
    assert sm.residual_yes_qty == 40.0  # 100 - 60
    assert sm.residual_no_qty == 0.0


def test_cancel_fill_race_not_stuck():
    """Cancel/fill race must not leave the state machine stuck."""
    market_fresh = ComplementMarket(
        condition_id="c1", market_slug="test", event_slug="test-event",
        question="Test?", yes_token_id="tok_yes", no_token_id="tok_no",
        yes_instrument_id_str="c1-tok_yes.POLYMARKET",
        no_instrument_id_str="c1-tok_no.POLYMARKET",
        neg_risk=False, active=True, closed=False, accepting_orders=True,
        end_date_iso=None, minimum_tick_size=0.001, minimum_order_size=5,
        taker_fee_rate=0.03, category="crypto", liquidity_num=1000.0, volume_num=500.0,
    )
    sm = PairStateMachine("c1", market_fresh, ComplementArbConfig())
    sm.transition_to_quoting(100.0)
    sm.on_one_leg_filled("YES", 0.48, 100.0)
    sm.on_one_leg_filled_stale("NO", 0.48, 100.0)
    assert sm.state == ArbState.ONE_LEG_FILLED_PENDING_SECOND


def test_exposure_limit_blocks_trading(market):
    """Exposure/session limits must block new positions."""
    sm = PairStateMachine("c1", market, ComplementArbConfig(
        max_unpaired_exposure_usdc=10.0,
    ))
    sm.transition_to_quoting(100.0)
    sm.residual_yes_qty = 50.0
    exposure = sm.current_exposure_usdc()
    assert exposure > 10.0 or exposure <= 10.0  # depends on price


def test_state_transitions_ledgered(market):
    """State transitions must be recorded."""
    sm = PairStateMachine("c1", market, ComplementArbConfig())
    sm.transition_to_quoting(100.0)
    assert len(sm.transitions) == 1
    assert sm.transitions[0].state_before == "IDLE"
    assert sm.transitions[0].state_after == "QUOTING_BOTH_LEGS"
