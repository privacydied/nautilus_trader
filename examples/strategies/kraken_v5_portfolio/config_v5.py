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

# BarSpecification: 4h bars = step 4 on HOUR aggregation
# Used by strategy bar_type and tests
BAR_SPEC_STEP: int = 4

# === INDICATORS ===

# === PORTFOLIO RULES ===

MAX_POSITIONS: int = 3               # hold top 1-3 assets at a time
MAX_NOTIONAL_PCT: float = 0.50       # max 50% total exposure initially
REBALANCE_BARS: int = 6              # rebalance every 6 bars (~24 hours at 4h)

# === EXIT RULES ===

TRAILING_STOP_ATR_MULT: float = 3.0  # 3x ATR trailing stop

# === FEES ===

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
