"""V6-B: Funding/Basis observation models."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PriceLevel:
    """Bid/ask or mark/index price from a venue."""
    venue: str
    symbol: str
    quote: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    mark: Optional[float] = None
    index: Optional[float] = None
    last: Optional[float] = None
    ts_exchange_ms: Optional[int] = None
    ts_recv_ms: int = 0

    @property
    def mid(self) -> Optional[float]:
        if self.bid is not None and self.ask is not None and self.bid > 0:
            return (self.bid + self.ask) / 2.0
        if self.mark is not None and self.mark > 0:
            return self.mark
        return None

    @property
    def is_stale(self, max_age_ms: int = 30_000) -> bool:
        if self.ts_exchange_ms is None:
            return False
        return (self.ts_recv_ms - self.ts_exchange_ms) > max_age_ms


@dataclass(frozen=True)
class FundingObservation:
    """One observation of spot/perp basis + funding rate."""
    timestamp_ms: int
    asset: str
    spot_venue: str
    perp_venue: str
    spot_symbol: str
    perp_symbol: str
    spot_bid: Optional[float]
    spot_ask: Optional[float]
    spot_mid: Optional[float]
    perp_bid: Optional[float]
    perp_ask: Optional[float]
    perp_mid: Optional[float]
    mark_price: Optional[float]
    index_price: Optional[float]
    quote_source: str
    basis_bps: Optional[float]
    funding_rate: Optional[float]
    funding_interval_hours: Optional[float]
    funding_apr: Optional[float]
    estimated_entry_fees_bps: float
    estimated_exit_fees_bps: float
    slippage_buffer_bps: float
    basis_risk_buffer_bps: float
    latency_buffer_bps: float
    quote_currency_mismatch: bool
    quote_mismatch_buffer_bps: float
    estimated_total_cost_bps: float
    expected_funding_bps: float
    estimated_net_edge_bps: Optional[float]
    opportunity_type: str
    is_candidate: bool
    reason_if_rejected: Optional[str]
