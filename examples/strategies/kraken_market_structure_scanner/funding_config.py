"""V6-B: Funding/Basis scanner configuration."""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FundingConfig:
    assets: List[str] = field(default_factory=lambda: ["BTC", "ETH"])
    spot_venues: List[str] = field(default_factory=lambda: ["kraken"])
    perp_venues: List[str] = field(default_factory=lambda: ["kraken", "binance", "bybit"])
    poll_interval_seconds: float = 5.0
    duration_seconds: float = 120.0

    # Thresholds
    min_funding_apr: float = 20.0
    min_net_edge_bps: float = 25.0

    # Fee assumptions (bps)
    spot_taker_fee_bps: float = 40.0
    perp_taker_fee_bps: float = 40.0  # default, can be per-venue

    # Buffer assumptions (bps)
    slippage_buffer_bps: float = 10.0
    latency_buffer_bps: float = 10.0
    basis_risk_buffer_bps: float = 25.0
    quote_mismatch_buffer_bps: float = 20.0  # extra buffer for USD/USDT mismatch

    stale_quote_max_age_ms: int = 30_000
    output_dir: str = "reports/v6_market_structure"

    # Per-venue fee overrides
    venue_fees: dict = field(default_factory=lambda: {
        "binance": 5.0,
        "bybit": 5.5,
        "kraken": 3.0,
    })


# Symbol mappings: asset -> {venue: symbol}
SYMBOL_MAP = {
    "BTC": {
        "kraken_spot": "XBTUSD",
        "kraken_perp": "PI_XBTUSD",          # Kraken Bitcoin perpetual (USD quoted)
        "binance_perp": "BTCUSDT",
        "bybit_perp": "BTCUSDT",
    },
    "ETH": {
        "kraken_spot": "ETHUSD",
        "kraken_perp": "PI_ETHUSD",          # Kraken Ethereum perpetual (USD quoted)
        "binance_perp": "ETHUSDT",
        "bybit_perp": "ETHUSDT",
    },
    "SOL": {
        "kraken_spot": "SOLUSD",
        "kraken_perp": "PI_SOLUSD",
        "binance_perp": "SOLUSDT",
        "bybit_perp": "SOLUSDT",
    },
}

# Per-venue quote currency
QUOTE_MAP = {
    "kraken_spot": "USD",
    "kraken_perp": "USD",
    "binance_perp": "USDT",
    "bybit_perp": "USDT",
}

# Funding interval in hours (hardcoded; verified from docs)
FUNDING_INTERVAL = {
    "kraken_perp": 8.0,   # Kraken futures: settled every 8h
    "binance_perp": 8.0,  # Binance USDT-M: every 8h
    "bybit_perp": 8.0,    # Bybit USDT perps: every 8h
}
