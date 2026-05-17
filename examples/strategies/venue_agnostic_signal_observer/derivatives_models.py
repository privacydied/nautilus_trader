"""Lightweight models for derivatives-market data.

**Observer-only. No execution, no orders, no private endpoints.**
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


# ---------------------------------------------------------------------------
# 1. Derivative trade tick — superset of TradeTickLite for perps
# ---------------------------------------------------------------------------

@dataclass
class DerivativeTradeTick:
    """A single trade tick on a derivatives venue.

    ts_event is always nanoseconds.  side is buy/sell/unknown as reported by
    the venue or inferred via tick-rule proxy.
    """
    ts_event: int          # nanosecond epoch
    venue: str
    symbol: str
    price: float
    size: float
    side: str              # buy / sell / unknown
    trade_id: str | None = None
    raw: dict | None = None

    @property
    def notional(self) -> float:
        return self.price * self.size

    def to_dict(self) -> dict:
        d: dict[str, Any] = asdict(self)
        d["notional"] = self.notional
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, d: dict) -> "DerivativeTradeTick":
        return cls(
            ts_event=int(d["ts_event"]),
            venue=d["venue"],
            symbol=d["symbol"],
            price=float(d["price"]),
            size=float(d["size"]),
            side=d.get("side", "unknown"),
            trade_id=d.get("trade_id"),
            raw=d.get("raw"),
        )


# ---------------------------------------------------------------------------
# 2. Snapshot models (OI, funding)
# ---------------------------------------------------------------------------

@dataclass
class OpenInterestSnapshot:
    ts_event: int          # nanosecond epoch
    venue: str
    symbol: str
    open_interest: float
    open_interest_value: float | None = None   # notional value if available
    raw: dict | None = None


@dataclass
class FundingSnapshot:
    ts_event: int
    venue: str
    symbol: str
    funding_rate: float      # per-interval (e.g. per 8h)
    funding_apr: float | None = None  # annualized
    mark_price: float | None = None
    index_price: float | None = None
    raw: dict | None = None


# ---------------------------------------------------------------------------
# 3. Impulse event — generated when derivatives flow looks abnormal
# ---------------------------------------------------------------------------

@dataclass
class DerivativeImpulseEvent:
    """A detected impulse event on a derivatives venue."""
    signal_id: str
    ts_event: int
    source_venue: str
    source_symbol: str
    target_venue: str
    target_symbol: str
    asset: str
    signal_type: str            # e.g. "notional_burst", "signed_imbalance",
                                #               "price_shock", "oi_shift"
    direction: str             # long / short
    strength: float            # z-score or multiplier
    lookback_ms: int
    metadata: dict | None = None

    # Optional OI/funding context attached when available
    oi_change_bps: float | None = None
    funding_rate: float | None = None
    funding_apr: float | None = None


# ---------------------------------------------------------------------------
# 4. Lead-lag result — one row per (event, horizon)
# ---------------------------------------------------------------------------

@dataclass
class DerivativeLeadLagResult:
    """One forward-return observation for an impulse event."""
    signal_id: str
    signal_ts: int
    source_venue: str
    target_venue: str
    symbol: str
    signal_type: str
    horizon_ms: int
    entry_reference_price: float | None = None
    forward_price: float | None = None
    raw_return_bps: float | None = None
    direction_adjusted_return_bps: float | None = None
    fee_bps: float | None = None
    slippage_bps: float | None = None
    latency_buffer_bps: float | None = None
    quote_mismatch_bps: float | None = None
    net_return_bps: float | None = None
    valid: bool = True
    rejection_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)
