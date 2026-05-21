"""Configuration for the venue-agnostic signal observer."""
from dataclasses import dataclass
from dataclasses import field
from typing import List


@dataclass
class Horizon:
    """A forward-return horizon."""

    name: str          # e.g. "10s", "5m"
    seconds: float     # e.g. 10.0, 300.0


DEFAULT_HORIZONS: List[Horizon] = [
    Horizon("10s", 10.0),
    Horizon("30s", 30.0),
    Horizon("60s", 60.0),
    Horizon("5m", 300.0),
    Horizon("15m", 900.0),
    Horizon("1h", 3600.0),
]


@dataclass
class FeeModel:
    """Simple flat fee + slippage + optional quote-mismatch buffer."""

    fee_bps: float = 5.0
    slippage_bps: float = 1.0
    quote_mismatch_buffer_bps: float = 0.0

    def total_cost_bps(self, quote_mismatch: bool) -> float:
        cost = self.fee_bps + self.slippage_bps
        if quote_mismatch:
            cost += self.quote_mismatch_buffer_bps
        return cost


@dataclass
class SignalSourceConfig:
    """Configuration for a signal source."""

    # CSV signal source
    signals_csv_path: str | None = None
    columns_mapping: dict | None = None  # map CSV column names to SignalEvent fields

    # Cross-market signal source
    cross_market_source_venue: str | None = None
    cross_market_source_instrument: str | None = None
    cross_market_target_venue: str | None = None
    cross_market_target_instrument: str | None = None
    cross_market_move_threshold_bps: float = 20.0
    cross_market_lookback_seconds: float = 60.0
    cross_market_cooldown_seconds: float = 60.0


@dataclass
class ObserverConfig:
    """Top-level configuration for the signal observer."""

    horizons: List[Horizon] = field(default_factory=lambda: list(DEFAULT_HORIZONS))
    fee_model: FeeModel = field(default_factory=FeeModel)
    signal_source: SignalSourceConfig = field(default_factory=SignalSourceConfig)

    # Data input — CSV bars for the target instrument
    bars_csv_path: str | None = None
    bars_csv_columns: dict = field(default_factory=lambda: {
        "timestamp": "timestamp",
        "close": "close",
    })

    # Filtering
    quote_source_currency: str = "USDT"
    quote_target_currency: str = "USD"
    apply_quote_mismatch_buffer: bool = True

    # Output
    output_dir: str = "reports/signal_observer"

    # Synthetic mode (deterministic smoke runs)
    synthetic: bool = False


@dataclass
class LeadLagConfig:
    """Configuration for a cross-venue lead-lag sweep experiment."""

    # Source venue pair
    source_venue: str = "BINANCE"
    source_instrument: str = "BTC/USDT"
    target_venue: str = "KRAKEN"
    target_instrument: str = "BTC/USD"

    # Lookback windows (seconds) — how far back to measure source move
    lookback_windows: list[float] = field(default_factory=lambda: [10.0, 30.0, 60.0])

    # Move thresholds (basis points) — minimum move to fire a signal
    move_thresholds_bps: list[float] = field(default_factory=lambda: [5.0, 10.0, 20.0])

    # Cooldown between signals (seconds)
    cooldown_seconds: float = 60.0

    # Data alignment grid (seconds) — resample period for cross-venue alignment
    grid_seconds: float = 10.0
