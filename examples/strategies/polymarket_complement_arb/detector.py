"""
Opportunity detector for Polymarket complement arb.

Evaluates complementary YES+NO pairs against the book, computes target maker
bid prices, and determines whether the pair is quoteable. Uses executable
prices (not midpoint). Rejects stale data and insufficient depth.
"""

from __future__ import annotations

from decimal import Decimal

from .config import ComplementArbConfig
from .edge_model import evaluate_maker_gate
from .models import (
    BookSnapshot,
    ComplementBookState,
    ComplementMarket,
    OpportunityDiagnostic,
)


def _is_book_stale(snapshot: BookSnapshot, max_age_ms: float, now_ms: float) -> bool:
    return (now_ms - snapshot.timestamp_ms) > max_age_ms


def _round_down(price: float, tick_size: float) -> float:
    """Round price DOWN to nearest valid tick (safe for maker BUY bids)."""
    if tick_size <= 0:
        return price
    # Handle floating point: price is effectively on a tick boundary
    n = round(price / tick_size, 10)
    if abs(n - round(n)) < 1e-10:
        return round(n * tick_size, 6)
    return round(int(price / tick_size) * tick_size, 6)


def _round_up(price: float, tick_size: float) -> float:
    """Round price UP to nearest valid tick (safe for maker SELL asks)."""
    if tick_size <= 0:
        return price
    # Handle floating point: price is effectively on a tick boundary
    n = round(price / tick_size, 10)
    if abs(n - round(n)) < 1e-10:
        return round(n * tick_size, 6)
    return round((int(price / tick_size) + 1) * tick_size, 6)


def compute_target_maker_bids(
    yes_book: BookSnapshot,
    no_book: BookSnapshot,
    config: ComplementArbConfig,
) -> tuple[float | None, float | None]:
    """
    Compute target maker bid prices for YES and NO based on current book state.

    The target bid is set just above the current best bid to improve queue
    position without crossing the spread.

    Parameters
    ----------
    yes_book : BookSnapshot
        Current book for YES token.
    no_book : BookSnapshot
        Current book for NO token.
    config : ComplementArbConfig
        Strategy configuration.

    Returns
    -------
    tuple[float | None, float | None]
        (target_yes_bid, target_no_bid) or (None, None) if not quoteable.
    """
    if not yes_book.bids or not no_book.bids:
        return None, None

    # Best bid for each
    yes_best_bid = yes_book.bids[0][0]
    no_best_bid = no_book.bids[0][0]

    # Target: match the best bid (post-only, won't walk the book)
    # Round down for safety
    target_yes_bid = _round_down(yes_best_bid, 0.001)
    target_no_bid = _round_down(no_best_bid, 0.001)

    return target_yes_bid, target_no_bid


def detect_opportunity(
    book_state: ComplementBookState,
    market: ComplementMarket,
    config: ComplementArbConfig,
    now_ms: float,
) -> OpportunityDiagnostic | None:
    """
    Detect a potential complement arb opportunity from current book state.

    Uses executable/top-of-book prices, not midpoint.
    Rejects stale data, insufficient depth, and one-sided books.

    Parameters
    ----------
    book_state : ComplementBookState
        Current book state for YES and NO tokens.
    market : ComplementMarket
        Market metadata.
    config : ComplementArbConfig
        Strategy configuration.
    now_ms : float
        Current time in milliseconds (for staleness check).

    Returns
    -------
    OpportunityDiagnostic | None
        Diagnostic if opportunity is detected, None if not quoteable.
    """
    yes_book = book_state.yes_book
    no_book = book_state.no_book

    # Stale check
    yes_stale = _is_book_stale(yes_book, config.max_book_age_ms, now_ms)
    no_stale = _is_book_stale(no_book, config.max_book_age_ms, now_ms)
    if yes_stale or no_stale:
        return None

    # Depth check - need both sides
    if not yes_book.asks or not no_book.asks:
        return None
    if not yes_book.bids or not no_book.bids:
        return None

    yes_ask = yes_book.asks[0][0]
    yes_ask_size = yes_book.asks[0][1]
    ya_top_bid_price = yes_book.bids[0][0]
    ya_top_bid_size = yes_book.bids[0][1]

    no_ask = no_book.asks[0][0]
    no_ask_size = no_book.asks[0][1]
    no_top_bid_price = no_book.bids[0][0]
    no_top_bid_size = no_book.bids[0][1]

    # Top-of-book depth must meet minimum
    min_depth_usdc = config.min_top_depth_usdc
    if yes_ask_size * yes_ask < min_depth_usdc and no_ask_size * no_ask < min_depth_usdc:
        return None

    # Compute target maker bids
    target_yes_bid, target_no_bid = compute_target_maker_bids(yes_book, no_book, config)
    if target_yes_bid is None or target_no_bid is None:
        return None

    # Compute gross gap from maker bids (resting view)
    gross_gap = 1.0 - target_yes_bid - target_no_bid

    # Also compute from ask side (taker diagnostic view)
    taker_gross_gap = 1.0 - yes_ask - no_ask

    # Fee rate
    fee_rate = Decimal(str(market.taker_fee_rate))

    # Evaluate maker gate
    qty = Decimal(str(config.min_order_usdc / 0.50))  # Default size at p=0.50
    maker_pass, maker_costs, _ = evaluate_maker_gate(
        yes_price=Decimal(str(target_yes_bid)),
        no_price=Decimal(str(target_no_bid)),
        quantity=qty,
        fee_rate=fee_rate,
        config=config,
    )

    # Evaluate taker diagnostic
    taker_costs_total = 0.0
    if taker_gross_gap > 0:
        taker_yes_fee = compute_taker_diagnostic_fee(qty, Decimal(str(yes_ask)), fee_rate)
        taker_no_fee = compute_taker_diagnostic_fee(qty, Decimal(str(no_ask)), fee_rate)
        taker_fee_total = float(taker_yes_fee) + float(taker_no_fee)
        taker_costs_total = (
            taker_fee_total
            + float(qty) * config.leg_risk_buffer_per_share
            + float(qty) * config.signing_latency_buffer_per_share
            + config.gas_redeem_buffer_per_pair
        )
    else:
        taker_yes_fee = 0.0
        taker_no_fee = 0.0
        taker_fee_total = 0.0

    taker_net = taker_gross_gap * float(qty) - taker_costs_total
    taker_pass = taker_net > (float(qty) * config.min_net_edge_per_share)

    # Compute total cost for maker view
    total_cost = (
        maker_costs["total_cost"]
        + float(qty) * config.leg_risk_buffer_per_share
        + float(qty) * config.signing_latency_buffer_per_share
        + config.gas_redeem_buffer_per_pair
    )

    net_edge = gross_gap * float(qty) - total_cost

    return OpportunityDiagnostic(
        condition_id=book_state.condition_id,
        market_slug=book_state.market_slug,
        question=market.question,
        yes_bid=ya_top_bid_price,
        yes_ask=yes_ask,
        no_bid=no_top_bid_price,
        no_ask=no_ask,
        yes_top_bid_size=ya_top_bid_size,
        yes_top_ask_size=yes_ask_size,
        no_top_bid_size=no_top_bid_size,
        no_top_ask_size=no_ask_size,
        gross_gap=gross_gap,
        taker_fee_rate=market.taker_fee_rate,
        taker_fee_yes=float(taker_yes_fee),
        taker_fee_no=float(taker_no_fee),
        total_taker_fee=float(taker_fee_total),
        leg_risk_buffer=float(qty) * config.leg_risk_buffer_per_share,
        signing_latency_buffer=float(qty) * config.signing_latency_buffer_per_share,
        gas_redeem_buffer=config.gas_redeem_buffer_per_pair,
        total_cost=total_cost,
        net_edge=net_edge,
        maker_gate_pass=maker_pass,
        taker_diagnostic_pass=taker_pass,
        depth_ok=(yes_ask_size * yes_ask >= min_depth_usdc or no_ask_size * no_ask >= min_depth_usdc),
        stale=False,
        ts_event_ns=book_state.ts_event_ns,
    )


def compute_taker_diagnostic_fee(
    quantity: Decimal,
    price: Decimal,
    fee_rate: Decimal,
) -> float:
    """Compute taker fee for diagnostic purposes (same formula as edge model)."""
    from .edge_model import compute_taker_fee
    return compute_taker_fee(quantity, price, fee_rate)
