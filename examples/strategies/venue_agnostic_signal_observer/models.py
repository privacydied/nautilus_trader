"""Data models for the signal observer."""
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List
import json


@dataclass
class SignalEvent:
    """A timestamped signal event."""
    signal_id: str
    timestamp: float           # unix epoch seconds
    source_venue: str
    source_instrument: str
    target_venue: str
    target_instrument: str
    signal_type: str           # "cross_market_move", "manual_csv", etc.
    direction: str             # "long" or "short"
    strength: float            # magnitude in bps or arbitrary scale
    metadata: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> "SignalEvent":
        return cls(
            signal_id=d["signal_id"],
            timestamp=float(d["timestamp"]),
            source_venue=d["source_venue"],
            source_instrument=d["source_instrument"],
            target_venue=d["target_venue"],
            target_instrument=d["target_instrument"],
            signal_type=d.get("signal_type", "unknown"),
            direction=d.get("direction", "long"),
            strength=float(d.get("strength", 0.0)),
            metadata=d.get("metadata"),
            reason=d.get("reason"),
        )


@dataclass
class ForwardReturnResult:
    """Forward return for one signal + one horizon."""
    signal_id: str
    signal_timestamp: float
    source_venue: str
    source_instrument: str
    target_venue: str
    target_instrument: str
    signal_type: str
    direction: str
    strength: float
    horizon: str
    entry_reference_price: Optional[float] = None
    forward_price: Optional[float] = None
    raw_return_bps: Optional[float] = None
    direction_adjusted_return_bps: Optional[float] = None
    fee_bps: Optional[float] = None
    slippage_bps: Optional[float] = None
    quote_mismatch_buffer_bps: Optional[float] = None
    net_return_bps: Optional[float] = None
    max_favorable_excursion_bps: Optional[float] = None
    max_adverse_excursion_bps: Optional[float] = None
    valid: bool = True
    rejection_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class HorizonSummary:
    """Aggregated stats for a single horizon."""
    horizon: str
    event_count: int = 0
    valid_count: int = 0
    rejected_count: int = 0
    mean_raw_return_bps: Optional[float] = None
    median_raw_return_bps: Optional[float] = None
    mean_net_return_bps: Optional[float] = None
    median_net_return_bps: Optional[float] = None
    win_rate_after_fees: Optional[float] = None
    p25: Optional[float] = None
    p50: Optional[float] = None
    p75: Optional[float] = None
    p90: Optional[float] = None
    best_return: Optional[float] = None
    worst_return: Optional[float] = None


@dataclass
class SignalTypeSummary:
    """Aggregated stats for a signal type."""
    signal_type: str
    event_count: int = 0
    valid_count: int = 0
    mean_net_return_bps: Optional[float] = None
    win_rate_after_fees: Optional[float] = None


@dataclass
class SignalEvaluationSummary:
    """Overall summary of an observer run."""
    run_start: float = 0.0
    run_end: float = 0.0
    source_venues: List[str] = field(default_factory=list)
    target_venues: List[str] = field(default_factory=list)
    instruments: List[str] = field(default_factory=list)
    horizons: List[str] = field(default_factory=list)
    total_signals: int = 0
    valid_evaluations: int = 0
    rejected_evaluations: int = 0
    fee_bps: float = 0.0
    slippage_bps: float = 0.0
    quote_mismatch_buffer_bps: float = 0.0
    results_by_horizon: List[dict] = field(default_factory=list)
    results_by_signal_type: List[dict] = field(default_factory=list)
    results_by_venue_pair: List[dict] = field(default_factory=list)
    best_signal_group: Optional[str] = None
    worst_signal_group: Optional[str] = None
    final_recommendation: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
