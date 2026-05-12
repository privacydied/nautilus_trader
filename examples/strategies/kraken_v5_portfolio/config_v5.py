#!/usr/bin/env python3
"""
V5 Multi-Asset Higher-Timeframe Momentum Portfolio — Configuration.

Hypothesis: A low-turnover crypto spot portfolio across multiple liquid
assets may produce enough gross edge to overcome fees, unlike single
BTC/USD spot.

Universe: 10 liquid Kraken USD spot pairs.
Timeframe: 4h bars (240-minute intervals).
"""

# === UNIVERSITY ===

LIQUID_PAIRS: list[str] = [
    "BTC/USD",
    "ETH/USD",
    "SOL/USD",
    "XRP/USD",
    "ADA/USD",
    "LINK/USD",
    "DOGE/USD",
    "AVAX/USD",
    "LTC/USD",
    "BCH/USD",
]

KRAKEN_VENUE: str = "KRAKEN"

# === TIMEFRAME ===

# Nautilus BarSpecification: 4h = step 4 on HOUR aggregation
BAR_SPEC_STEP: int = 4
BAR_SPEC_AGG: str = "HOUR"

# Per-asset price precision (price decimals) and size precision (qty decimals)
# Used by import_multi_catalog.py, run_v5_research.py, and strategy_v5.py
INSTRUMENT_DETAIL: dict[str, tuple[int, int]] = {
    "BTC/USD.KRAKEN":  (2, 8),
    "ETH/USD.KRAKEN":  (2, 8),
    "SOL/USD.KRAKEN":  (2, 4),
    "XRP/USD.KRAKEN":  (4, 2),
    "ADA/USD.KRAKEN":  (5, 2),
    "LINK/USD.KRAKEN": (2, 4),
    "DOGE/USD.KRAKEN": (5, 2),
    "AVAX/USD.KRAKEN": (2, 4),
    "LTC/USD.KRAKEN":  (2, 8),
    "BCH/USD.KRAKEN":  (2, 8),
}

EMA_TREND_PERIOD: int = 200          # price above EMA(200) for trend filter
MOMENTUM_LOOKBACK_30D: int = 180     # 30d momentum ~ 180 bars at 4h
MOMENTUM_LOOKBACK_90D: int = 540     # 90d momentum ~ 540 bars at 4h
ATR_PERIOD: int = 28                 # 28-period ATR for volatility filter

# === PORTFOLIO RULES ===

MAX_POSITIONS: int = 3               # hold top 1-3 assets at a time
MAX_NOTIONAL_PCT: float = 0.50       # max 50% total exposure initially
REBALANCE_BARS: int = 6              # rebalance every 6 bars (~24 hours at 4h)

# === EXIT RULES ===

TRAILING_STOP_ATR_MULT: float = 3.0  # 3x ATR trailing stop
TREND_LOSS_EXIT_BARS: float = 1.0    # exit on close below EMA(200) for 1 full bar

# === FEES ===

MAKER_FEE: float = 0.0025            # 0.25%
TAKER_FEE: float = 0.0040            # 0.40%

# === BACKTEST ===

STARTING_BALANCE_USD: float = 100_000.0
RISK_PER_TRADE_PCT: float = 0.01     # 1% equity risk per entry
MIN_POSITION_SIZE_USD: float = 25.0  # skip dust positions

# === TEST WINDOWS ===

# Windows adapted to data availability (daily data starts ~2024-05-22):
WINDOWS = [
    ("2024h2", "2024-07-01", "2025-01-01"),
    ("2025",   "2025-01-01", "2026-01-01"),
    ("2026",   "2026-01-01", "2026-05-01"),
]
