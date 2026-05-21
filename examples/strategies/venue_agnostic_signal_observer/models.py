"""Data models for the signal observer."""
import json
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List


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
    metadata: Dict[str, Any] | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> "SignalEvent":
        if "direction" not in d or d["direction"] is None:
            raise ValueError("SignalEvent.direction is required; refusing to default to long")
        direction = str(d["direction"])
        if direction not in {"long", "short"}:
            raise ValueError(f"Invalid SignalEvent.direction {direction!r}; expected 'long' or 'short'")
        return cls(
            signal_id=d["signal_id"],
            timestamp=float(d["timestamp"]),
            source_venue=d["source_venue"],
            source_instrument=d["source_instrument"],
            target_venue=d["target_venue"],
            target_instrument=d["target_instrument"],
            signal_type=d.get("signal_type", "unknown"),
            direction=direction,
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
    entry_reference_price: float | None = None
    forward_price: float | None = None
    raw_return_bps: float | None = None
    direction_adjusted_return_bps: float | None = None
    fee_bps: float | None = None
    slippage_bps: float | None = None
    quote_mismatch_buffer_bps: float | None = None
    net_return_bps: float | None = None
    max_favorable_excursion_bps: float | None = None
    max_adverse_excursion_bps: float | None = None
    valid: bool = True
    rejection_reason: str | None = None

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
    mean_raw_return_bps: float | None = None
    median_raw_return_bps: float | None = None
    mean_net_return_bps: float | None = None
    median_net_return_bps: float | None = None
    win_rate_after_fees: float | None = None
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    p90: float | None = None
    best_return: float | None = None
    worst_return: float | None = None


@dataclass
class SignalTypeSummary:
    """Aggregated stats for a signal type."""

    signal_type: str
    event_count: int = 0
    valid_count: int = 0
    mean_net_return_bps: float | None = None
    win_rate_after_fees: float | None = None


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
    best_signal_group: str | None = None
    worst_signal_group: str | None = None
    final_recommendation: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
