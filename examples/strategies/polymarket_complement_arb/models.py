"""
Data models for the Polymarket complement arb strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# --- Market metadata ---

@dataclass(frozen=True)
class ComplementMarket:
    """A binary market with both YES and NO tokens from the same condition_id."""
    condition_id: str
    market_slug: str
    event_slug: str
    question: str
    yes_token_id: str
    no_token_id: str
    yes_instrument_id_str: str
    no_instrument_id_str: str
    neg_risk: bool
    active: bool
    closed: bool
    accepting_orders: bool
    end_date_iso: str | None
    minimum_tick_size: float
    minimum_order_size: float
    taker_fee_rate: float
    category: str | None
    liquidity_num: float | None
    volume_num: float | None


@dataclass(frozen=True)
class MarketSkipReason:
    """Record of why a market was skipped during filtering."""
    condition_id: str
    market_slug: str
    reason: str


# --- Opportunity detection ---

@dataclass(frozen=True)
class BookSnapshot:
    """A snapshot of the order book for a single token."""
    instrument_id_str: str
    token_id: str
    bids: list[tuple[float, float]]  # (price, size)
    asks: list[tuple[float, float]]  # (price, size)
    timestamp_ms: float
    stale: bool = False


@dataclass(frozen=True)
class ComplementBookState:
    """Combined book state for both YES and NO tokens of a condition."""
    condition_id: str
    market_slug: str
    yes_book: BookSnapshot
    no_book: BookSnapshot
    ts_event_ns: int


@dataclass(frozen=True)
class OpportunityDiagnostic:
    """Diagnostic view of a potential complement arb opportunity."""
    condition_id: str
    market_slug: str
    question: str
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    yes_top_bid_size: float
    yes_top_ask_size: float
    no_top_bid_size: float
    no_top_ask_size: float
    gross_gap: float
    taker_fee_rate: float
    taker_fee_yes: float
    taker_fee_no: float
    total_taker_fee: float
    leg_risk_buffer: float
    signing_latency_buffer: float
    gas_redeem_buffer: float
    total_cost: float
    net_edge: float
    maker_gate_pass: bool
    taker_diagnostic_pass: bool
    depth_ok: bool
    stale: bool
    ts_event_ns: int


# --- Passive fill estimate ---

@dataclass(frozen=True)
class PassiveFillEstimate:
    """Record of a would-be maker quote and whether it was later touched."""
    condition_id: str
    market_slug: str
    side: Literal["YES", "NO"]
    quote_price: float
    quote_size: float
    quote_timestamp_ns: int
    touched: bool
    crossed: bool
    time_to_touch_ms: float | None
    edge_lifetime_ms: float
    edge_remained_profitable: bool
    second_leg_available: bool
    stale_before_touch: bool
    resolution_danger: bool


# --- State machine ---

@dataclass(frozen=True)
class StateTransition:
    """Record of a state machine transition."""
    condition_id: str
    state_before: str
    state_after: str
    reason: str
    ts_event_ns: int


# --- Ledger ---

@dataclass(frozen=True)
class LedgerEntry:
    """A single entry in the append-only ledger."""
    run_id: str
    timestamp_ns: int
    mode: str
    event_type: str
    condition_id: str | None = None
    market_slug: str | None = None
    question: str | None = None
    yes_instrument_id: str | None = None
    no_instrument_id: str | None = None
    state_before: str | None = None
    state_after: str | None = None
    prices: dict[str, float] = field(default_factory=dict)
    sizes: dict[str, float] = field(default_factory=dict)
    intended_qty: float | None = None
    filled_qty: float | None = None
    paired_qty: float | None = None
    residual_qty: float | None = None
    fee_inputs: dict[str, Any] = field(default_factory=dict)
    cost_breakdown: dict[str, float] = field(default_factory=dict)
    net_edge: float | None = None
    order_side: str | None = None
    order_type: str | None = None
    time_in_force: str | None = None
    post_only: bool | None = None
    quote_quantity: bool | None = None
    config_hash: str | None = None
    details: dict[str, Any] = field(default_factory=dict)