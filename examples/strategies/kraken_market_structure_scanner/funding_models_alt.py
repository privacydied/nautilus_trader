"""V6-C: Altcoin funding/basis observation models with cost scenarios."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class DepthLevel:
    """Single order book depth level."""
    price: float
    size: Optional[float] = None  # base units; None if not reported


@dataclass(frozen=True)
class FundingObservationAlt:
    """One V6-C altcoin funding/basis observation with three cost scenarios."""
    timestamp_ms: int
    asset: str
    spot_venue: str
    perp_venue: str
    spot_symbol: str
    perp_symbol: str
    # Quotes
    quote_source: str          # e.g. "USD" or "USDT"
    quote_mismatch: bool

    # Prices
    spot_bid: Optional[float]
    spot_ask: Optional[float]
    spot_mid: Optional[float]
    perp_bid: Optional[float]
    perp_ask: Optional[float]
    perp_mid: Optional[float]
    mark_price: Optional[float]
    index_price: Optional[float]

    # Depth (top-of-book sizes; None if not available)
    spot_bid_size: Optional[float]
    spot_ask_size: Optional[float]
    perp_bid_size: Optional[float]
    perp_ask_size: Optional[float]

    # Basis and funding
    basis_bps: Optional[float]
    funding_rate: Optional[float]
    funding_interval_hours: Optional[float]
    funding_apr: Optional[float]

    # Three cost scenarios (all in bps)
    conservative_taker_cost_bps: float
    mixed_maker_taker_cost_bps: float
    optimistic_maker_cost_bps: float

    # Net edge per scenario: expected_funding - total_cost
    conservative_net_edge_bps: float
    mixed_net_edge_bps: float
    optimistic_net_edge_bps: float

    candidate: bool
    durable_candidate: bool
    rejection_reason: Optional[str]

    # Quote freshness
    quote_stale: bool
    quote_age_ms: Optional[int]


@dataclass
class CostScenario:
    """Fee/buffer assumptions for one cost scenario."""
    name: str
    spot_fee_bps: float
    perp_fee_bps: float
    # Buffers
    slippage_bps: float
    latency_bps: float
    basis_risk_bps: float
    quote_mismatch_bps: float  # applied if mismatch

    def total_cost_bps(self, round_trip: bool = True, has_mismatch: bool = True) -> float:
        entry = self.spot_fee_bps + self.perp_fee_bps
        exit_fee = entry if round_trip else 0.0
        buffers = self.slippage_bps + self.latency_bps + self.basis_risk_bps
        if has_mismatch:
            buffers += self.quote_mismatch_bps
        return entry + exit_fee + buffers


@dataclass
class PersistenceTracker:
    """Tracks how many consecutive polls an asset+venue has been a candidate."""
    asset: str
    perp_venue: str
    first_seen_ms: int = 0
    last_seen_ms: int = 0
    consecutive_count: int = 0
    max_consecutive_count: int = 0
    broken_at_ms: Optional[int] = None

    def poll_candidate(self, ts_ms: int) -> None:
        if self.first_seen_ms == 0:
            self.first_seen_ms = ts_ms
            self.consecutive_count = 1
        else:
            self.consecutive_count += 1
        self.last_seen_ms = ts_ms
        if self.consecutive_count > self.max_consecutive_count:
            self.max_consecutive_count = self.consecutive_count

    def poll_non_candidate(self, ts_ms: Optional[int] = None) -> None:
        if self.consecutive_count > self.max_consecutive_count:
            self.max_consecutive_count = self.consecutive_count
        self.consecutive_count = 0
        if ts_ms:
            self.broken_at_ms = ts_ms

    def is_durable(self, min_polls: int) -> bool:
        return self.max_consecutive_count >= min_polls


# Three standard scenarios
def make_cost_scenarios(
    spot_taker_bps: float = 40.0,
    perp_taker_bps: float = 5.0,
    spot_maker_bps: float = 20.0,
    perp_maker_bps: float = 2.0,
    slippage_bps: float = 10.0,
    latency_bps: float = 10.0,
    basis_risk_bps: float = 25.0,
    quote_mismatch_bps: float = 20.0,
) -> dict:
    """Return three CostScenario dicts."""
    return {
        "conservative_taker": CostScenario(
            name="conservative_taker",
            spot_fee_bps=spot_taker_bps,
            perp_fee_bps=perp_taker_bps,
            slippage_bps=slippage_bps,
            latency_bps=latency_bps,
            basis_risk_bps=basis_risk_bps,
            quote_mismatch_bps=quote_mismatch_bps,
        ),
        "mixed_maker_taker": CostScenario(
            name="mixed_maker_taker",
            spot_fee_bps=spot_maker_bps,
            perp_fee_bps=perp_maker_bps,
            slippage_bps=slippage_bps / 2,
            latency_bps=latency_bps / 2,
            basis_risk_bps=basis_risk_bps,
            quote_mismatch_bps=quote_mismatch_bps / 2,
        ),
        "optimistic_maker": CostScenario(
            name="optimistic_maker",
            spot_fee_bps=spot_maker_bps,
            perp_fee_bps=perp_maker_bps,
            slippage_bps=1.0,
            latency_bps=1.0,
            basis_risk_bps=basis_risk_bps / 2,
            quote_mismatch_bps=quote_mismatch_bps / 2,
        ),
    }
