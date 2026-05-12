"""High-resolution tick-level data models for the signal observer event-study framework.

This module provides lightweight, serializable dataclasses for capturing tick-level
price, signal, and forward-return measurements at nanosecond precision. These models
are used exclusively for observation, analysis, and signal measurement — there is
**no execution logic, no order submission, no position tracking, and no live-trading
code** in this module or any code that imports it.

All timestamps (ts_event, signal_ts) are integer nanosecond Unix epochs for
sub-millisecond precision during event-study alignment.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass


# ---------------------------------------------------------------------------
# TradeTickLite
# ---------------------------------------------------------------------------


@dataclass
class TradeTickLite:
    """A minimal representation of a single trade tick.

    Designed for high-resolution signal measurement only.  No order or execution
    semantics are attached.
    """

    ts_event: int  # nanosecond unix epoch
    venue: str
    symbol: str
    price: float
    size: float
    side: str  # "buy", "sell", or "unknown"
    trade_id: str | None = None
    raw: dict | None = None

    def to_dict(self) -> dict:
        """Return a plain dict suitable for JSON serialisation."""
        return asdict(self)

    def to_json(self) -> str:
        """Return a JSON string representation."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> TradeTickLite:
        """Construct a ``TradeTickLite`` from a plain dict.

        *ts_event* is explicitly cast to ``int`` to guard against deserialised
        floats from JSON.
        """
        return cls(
            ts_event=int(d["ts_event"]),
            venue=d["venue"],
            symbol=d["symbol"],
            price=float(d["price"]),
            size=float(d["size"]),
            side=d["side"],
            trade_id=d.get("trade_id"),
            raw=d.get("raw"),
        )


# ---------------------------------------------------------------------------
# QuoteTickLite
# ---------------------------------------------------------------------------


@dataclass
class QuoteTickLite:
    """A minimal representation of a single quote tick.

    Provides computed *mid* price and spread-in-bps via properties.
    """

    ts_event: int  # nanosecond unix epoch
    venue: str
    symbol: str
    bid: float
    ask: float
    bid_size: float = 0.0
    ask_size: float = 0.0
    raw: dict | None = None

    # -- computed properties ------------------------------------------------

    @property
    def mid(self) -> float:
        """Mid-price: ``(bid + ask) / 2``."""
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        """Bid-ask spread expressed in basis points of mid price."""
        m = self.mid
        if m > 0:
            return (self.ask - self.bid) / m * 10000
        return 0.0

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> QuoteTickLite:
        """Construct a ``QuoteTickLite`` from a plain dict.

        Raises ``ValueError`` if ``ask`` is not strictly greater than ``bid``
        or if ``bid`` is not strictly positive.
        """
        bid = float(d["bid"])
        ask = float(d["ask"])
        if not (ask > bid > 0):
            raise ValueError(
                f"Invalid quote: ask ({ask}) must be > bid ({bid}) > 0"
            )
        return cls(
            ts_event=int(d["ts_event"]),
            venue=d["venue"],
            symbol=d["symbol"],
            bid=bid,
            ask=ask,
            bid_size=float(d.get("bid_size", 0.0)),
            ask_size=float(d.get("ask_size", 0.0)),
            raw=d.get("raw"),
        )


# ---------------------------------------------------------------------------
# TickSignalEvent
# ---------------------------------------------------------------------------


@dataclass
class TickSignalEvent:
    """A detected cross-venue / cross-symbol tick-level signal.

    Records the context of the signal (source, target, asset) along with the
    measured price movement and metadata. Used purely for measurement — this
    event **is not** sent to any execution engine.
    """

    signal_id: str
    ts_event: int  # nanosecond epoch
    source_venue: str
    source_symbol: str
    target_venue: str
    target_symbol: str
    asset: str  # base asset e.g. "BTC"
    signal_type: str  # "tick_lead_lag"
    direction: str  # "long" (source moved up) or "short" (source moved down)
    lookback_ms: int
    threshold_bps: float
    source_move_bps: float
    source_start_price: float
    source_end_price: float
    strength: float  # abs(source_move_bps)
    metadata: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> TickSignalEvent:
        """Construct a ``TickSignalEvent`` from a plain dict."""
        return cls(
            signal_id=d["signal_id"],
            ts_event=int(d["ts_event"]),
            source_venue=d["source_venue"],
            source_symbol=d["source_symbol"],
            target_venue=d["target_venue"],
            target_symbol=d["target_symbol"],
            asset=d["asset"],
            signal_type=d.get("signal_type", "tick_lead_lag"),
            direction=d["direction"],
            lookback_ms=int(d["lookback_ms"]),
            threshold_bps=float(d["threshold_bps"]),
            source_move_bps=float(d["source_move_bps"]),
            source_start_price=float(d["source_start_price"]),
            source_end_price=float(d["source_end_price"]),
            strength=float(d["strength"]),
            metadata=d.get("metadata"),
        )


# ---------------------------------------------------------------------------
# TickForwardReturn
# ---------------------------------------------------------------------------


@dataclass
class TickForwardReturn:
    """Measured forward return for a tick signal over a specified horizon.

    Captures the raw and cost-adjusted return (in basis points) from the
    signal timestamp through ``horizon_ms``.  When ``valid`` is ``False`` the
    ``rejection_reason`` explains why the observation was discarded.
    """

    signal_id: str
    signal_ts: int  # nanosecond epoch
    target_venue: str
    target_symbol: str
    horizon_ms: int
    entry_reference_price: float | None = None
    forward_price: float | None = None
    raw_return_bps: float | None = None
    direction_adjusted_return_bps: float | None = None
    fee_bps: float | None = None
    slippage_bps: float | None = None
    quote_mismatch_buffer_bps: float | None = None
    net_return_bps: float | None = None
    valid: bool = True
    rejection_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())
