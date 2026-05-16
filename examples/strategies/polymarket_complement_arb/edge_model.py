"""
Edge model for Polymarket complement arb strategy.

Computes gross gap, estimated costs, and net edge for a complementary YES+NO pair.
Uses Polymarket's documented fee formula: fee = C * feeRate * p * (1 - p).

Two views:
1. Maker/resting view: target maker bid prices must clear net edge after costs.
2. Taker/cross diagnostic view: executable ask prices must clear net edge.

Maker rebates are excluded from the conservative acceptance gate in V1.
"""

from __future__ import annotations

from decimal import Decimal

from .config import ComplementArbConfig


def compute_maker_fee(
    quantity: Decimal,
    price: Decimal,
    fee_rate: Decimal,
) -> float:
    """Maker fee is always zero on Polymarket."""
    return 0.0


def compute_taker_fee(
    quantity: Decimal,
    price: Decimal,
    fee_rate: Decimal,
) -> float:
    """
    Compute Polymarket taker fee for a fill.

    Formula: fee = C * feeRate * p * (1 - p)

    Where C is share/contract quantity, feeRate is the effective taker rate,
    and p is the price/probability in [0, 1].
    """
    if fee_rate <= 0:
        return 0.0
    fee = quantity * price * fee_rate * (Decimal(1) - price)
    return round(float(fee), 5)


def compute_gross_gap(yes_price: float, no_price: float) -> float:
    """
    Compute the gross gap between 1.00 and the sum of complementary prices.

    gross_gap = 1.00 - yes_price - no_price
    """
    return 1.0 - yes_price - no_price


def compute_total_costs(
    quantity: Decimal,
    yes_price: Decimal,
    no_price: Decimal,
    fee_rate: Decimal,
    config: ComplementArbConfig,
    taker_close: bool = False,
) -> dict[str, float]:
    """
    Compute all costs for a paired YES+NO position.

    Parameters
    ----------
    quantity : Decimal
        Intended share quantity (same for YES and NO).
    yes_price : Decimal
        YES price.
    no_price : Decimal
        NO price.
    fee_rate : Decimal
        Market's taker fee rate.
    config : ComplementArbConfig
        Strategy configuration.
    taker_close : bool
        Whether one leg would be closed via taker (adds taker fee).

    Returns
    -------
    dict[str, float]
        Cost breakdown with keys: maker_fee, taker_fee, leg_risk, signing_latency,
        gas_redeem, total_cost.
    """
    qty = Decimal(str(quantity))

    if taker_close:
        # Taker close on one leg - worst case at p=0.50 (max fee)
        # Use the higher of the two prices for conservative estimate
        max_price = max(yes_price, no_price)
        taker_fee = compute_taker_fee(qty, max_price, fee_rate)
    else:
        taker_fee = 0.0

    maker_fee = compute_maker_fee(qty, yes_price, fee_rate) + compute_maker_fee(qty, no_price, fee_rate)

    leg_risk = float(qty) * config.leg_risk_buffer_per_share
    signing_latency = float(qty) * config.signing_latency_buffer_per_share
    gas_redeem = config.gas_redeem_buffer_per_pair

    total = maker_fee + taker_fee + leg_risk + signing_latency + gas_redeem

    return {
        "maker_fee": maker_fee,
        "taker_fee": taker_fee,
        "leg_risk": leg_risk,
        "signing_latency": signing_latency,
        "gas_redeem": gas_redeem,
        "total_cost": total,
    }


def evaluate_maker_gate(
    yes_price: Decimal,
    no_price: Decimal,
    quantity: Decimal,
    fee_rate: Decimal,
    config: ComplementArbConfig,
) -> tuple[bool, dict[str, float], float]:
    """
    Evaluate whether a complementary pair passes the maker/resting gate.

    The maker gate requires:
        1.00 - yes_price - no_price > total_cost + min_net_edge

    Where total_cost includes leg_risk, signing_latency, and gas_redeem
    but NOT taker fees (both legs are maker) and NOT maker rebates.

    Parameters
    ----------
    yes_price : Decimal
        Target maker bid price for YES.
    no_price : Decimal
        Target maker bid price for NO.
    quantity : Decimal
        Intended share quantity.
    fee_rate : Decimal
        Market's taker fee rate.
    config : ComplementArbConfig
        Strategy configuration.

    Returns
    -------
    tuple[bool, dict[str, float], float]
        (pass, cost_breakdown, gross_gap)
    """
    qty = Decimal(str(quantity))
    gross_gap = compute_gross_gap(float(yes_price), float(no_price))

    costs = compute_total_costs(
        quantity=qty,
        yes_price=yes_price,
        no_price=no_price,
        fee_rate=fee_rate,
        config=config,
        taker_close=False,
    )

    min_edge = float(qty) * config.min_net_edge_per_share
    required = costs["total_cost"] + min_edge
    pair_value = gross_gap * float(qty)

    passes = pair_value > required
    return passes, costs, gross_gap


def evaluate_taker_close_gate(
    yes_fill_price: Decimal,
    target_no_price: Decimal,
    quantity: Decimal,
    fee_rate: Decimal,
    config: ComplementArbConfig,
) -> tuple[bool, dict[str, float], float]:
    """
    Evaluate whether closing the second leg with a taker order is profitable.

    This is only used when one leg has already filled and we need to close
    the second leg immediately.

    Parameters
    ----------
    yes_fill_price : Decimal
        Price at which YES was filled.
    target_no_price : Decimal
        Current executable ask price for NO (or vice versa).
    quantity : Decimal
        Share quantity.
    fee_rate : Decimal
        Market's taker fee rate.
    config : ComplementArbConfig
        Strategy configuration.

    Returns
    -------
    tuple[bool, dict[str, float], float]
        (pass, cost_breakdown, gross_gap)
    """
    qty = Decimal(str(quantity))

    # Gross gap from the filled leg's price and current executable price
    gross_gap = compute_gross_gap(float(yes_fill_price), float(target_no_price))

    # Now we need taker fee on the second leg close
    costs = compute_total_costs(
        quantity=qty,
        yes_price=yes_fill_price,
        no_price=target_no_price,
        fee_rate=fee_rate,
        config=config,
        taker_close=True,
    )

    min_edge = float(qty) * config.min_net_edge_per_share
    required = costs["total_cost"] + min_edge
    pair_value = gross_gap * float(qty)

    passes = pair_value > required
    return passes, costs, gross_gap
