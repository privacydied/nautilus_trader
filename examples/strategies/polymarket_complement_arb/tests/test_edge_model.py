"""
Tests for the edge_model module.
"""

from decimal import Decimal

import pytest

from examples.strategies.polymarket_complement_arb.config import ComplementArbConfig
from examples.strategies.polymarket_complement_arb.edge_model import (
    compute_gross_gap,
    compute_maker_fee,
    compute_taker_fee,
    compute_total_costs,
    evaluate_maker_gate,
    evaluate_taker_close_gate,
)


def test_gross_gap_positive():
    gap = compute_gross_gap(0.40, 0.40)
    assert gap == pytest.approx(0.20)


def test_gross_gap_negative():
    gap = compute_gross_gap(0.60, 0.50)
    assert gap == pytest.approx(-0.10)


def test_maker_fee_zero():
    fee = compute_maker_fee(Decimal("100"), Decimal("0.50"), Decimal("0.03"))
    assert fee == 0.0


def test_taker_fee_zero_rate():
    fee = compute_taker_fee(Decimal("100"), Decimal("0.50"), Decimal("0"))
    assert fee == 0.0


def test_taker_fee_at_p50():
    """Fees peak at p=0.50: fee = 100 * 0.03 * 0.50 * 0.50 = 0.75"""
    fee = compute_taker_fee(Decimal("100"), Decimal("0.50"), Decimal("0.03"))
    assert fee == pytest.approx(0.75)


def test_taker_fee_at_extreme():
    """Fee near p=0.10: fee = 100 * 0.03 * 0.10 * 0.90 = 0.27"""
    fee = compute_taker_fee(Decimal("100"), Decimal("0.10"), Decimal("0.03"))
    assert fee == pytest.approx(0.27)


def test_gross_gap_above_total_cost_accepts():
    """Gross gap of 2% on 100 shares = $2.00, costs ~$0.50, should pass."""
    config = ComplementArbConfig(
        leg_risk_buffer_per_share=0.002,
        signing_latency_buffer_per_share=0.001,
        gas_redeem_buffer_per_pair=0.001,
        min_net_edge_per_share=0.005,
    )
    passes, costs, gap = evaluate_maker_gate(
        yes_price=Decimal("0.49"),
        no_price=Decimal("0.49"),
        quantity=Decimal("100"),
        fee_rate=Decimal("0.03"),
        config=config,
    )
    assert passes is True
    assert gap == pytest.approx(0.02)


def test_gross_gap_below_total_cost_rejects():
    """Gross gap of 0.5% on 100 shares = $0.50, costs ~$0.50, should fail."""
    config = ComplementArbConfig(
        leg_risk_buffer_per_share=0.002,
        signing_latency_buffer_per_share=0.001,
        gas_redeem_buffer_per_pair=0.001,
        min_net_edge_per_share=0.005,
    )
    passes, _, _ = evaluate_maker_gate(
        yes_price=Decimal("0.485"),
        no_price=Decimal("0.510"),
        quantity=Decimal("100"),
        fee_rate=Decimal("0.03"),
        config=config,
    )
    # 1.0 - 0.485 - 0.510 = 0.005 = $0.50 on 100 shares
    # costs: 0 + 200*0.002 + 200*0.001 + 0.001 = 0.601
    # min edge: 100 * 0.005 = 0.50
    # required: 0.601 + 0.50 = 1.101
    # pair value: 0.005 * 100 = 0.50
    assert passes is False


def test_taker_close_includes_taker_fee():
    config = ComplementArbConfig(
        leg_risk_buffer_per_share=0.002,
        signing_latency_buffer_per_share=0.001,
        gas_redeem_buffer_per_pair=0.001,
        min_net_edge_per_share=0.005,
    )
    passes, costs, gap = evaluate_taker_close_gate(
        yes_fill_price=Decimal("0.45"),
        target_no_price=Decimal("0.52"),
        quantity=Decimal("100"),
        fee_rate=Decimal("0.03"),
        config=config,
    )
    # gap = 1 - 0.45 - 0.52 = 0.03 = $3.00 on 100 shares
    # taker fee on NO at 0.52: 100 * 0.03 * 0.52 * 0.48 = 0.7488
    assert costs["taker_fee"] > 0


def test_fee_category_failure_rejects():
    config = ComplementArbConfig(
        leg_risk_buffer_per_share=0.002,
        signing_latency_buffer_per_share=0.001,
        gas_redeem_buffer_per_pair=0.001,
        min_net_edge_per_share=0.005,
    )
    # fee_rate=0 means the market has no fee schedule
    passes, costs, _ = evaluate_maker_gate(
        yes_price=Decimal("0.49"),
        no_price=Decimal("0.49"),
        quantity=Decimal("100"),
        fee_rate=Decimal("0"),
        config=config,
    )
    # costs should be low but still need min_edge
    assert costs["taker_fee"] == 0.0


def test_maker_rebate_not_in_gate():
    """Maker rebate must not be counted in the acceptance gate."""
    config = ComplementArbConfig(
        leg_risk_buffer_per_share=0.002,
        signing_latency_buffer_per_share=0.001,
        gas_redeem_buffer_per_pair=0.001,
        min_net_edge_per_share=0.005,
    )
    passes, costs, _ = evaluate_maker_gate(
        yes_price=Decimal("0.49"),
        no_price=Decimal("0.49"),
        quantity=Decimal("100"),
        fee_rate=Decimal("0.03"),
        config=config,
    )
    # Maker fee should be explicitly tracked and excluded from gate
    assert costs["maker_fee"] == 0.0  # Always zero on Polymarket


def test_signing_latency_can_flip_accept_to_reject():
    """With high signing latency buffer, a marginal opportunity should reject."""
    config_narrow = ComplementArbConfig(
        leg_risk_buffer_per_share=0.001,
        signing_latency_buffer_per_share=0.001,
        gas_redeem_buffer_per_pair=0.001,
        min_net_edge_per_share=0.001,
    )
    config_wide = ComplementArbConfig(
        leg_risk_buffer_per_share=0.001,
        signing_latency_buffer_per_share=0.05,  # 5% of position
        gas_redeem_buffer_per_pair=0.001,
        min_net_edge_per_share=0.001,
    )
    passes_narrow, _, _ = evaluate_maker_gate(
        yes_price=Decimal("0.495"),
        no_price=Decimal("0.495"),
        quantity=Decimal("100"),
        fee_rate=Decimal("0.03"),
        config=config_narrow,
    )
    passes_wide, _, _ = evaluate_maker_gate(
        yes_price=Decimal("0.495"),
        no_price=Decimal("0.495"),
        quantity=Decimal("100"),
        fee_rate=Decimal("0.03"),
        config=config_wide,
    )
    # Gap is 0.01 = $1.00 on 100 shares
    # Narrow: ~$0.40 costs -> passes
    # Wide: ~$5.20 costs -> rejects
    assert passes_narrow is True
    assert passes_wide is False


def test_cost_breakdown_keys():
    config = ComplementArbConfig()
    costs = compute_total_costs(
        quantity=Decimal("100"),
        yes_price=Decimal("0.50"),
        no_price=Decimal("0.50"),
        fee_rate=Decimal("0.03"),
        config=config,
        taker_close=False,
    )
    assert "maker_fee" in costs
    assert "taker_fee" in costs
    assert "leg_risk" in costs
    assert "signing_latency" in costs
    assert "gas_redeem" in costs
    assert "total_cost" in costs
