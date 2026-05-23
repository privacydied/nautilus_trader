#!/usr/bin/env python3
"""V2 config — fee-aware selective maker breakout for Kraken BTC/USD."""

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.enums import BookType, OmsType, AccountType
from nautilus_trader.model.objects import Currency, Money

from nautilus_trader.model.functions import currency_type_from_str

KRAKEN_VENUE: str = "KRAKEN"
INSTRUMENT_SYMBOL: str = "BTC/USD"
INSTRUMENT_ID: str = f"{INSTRUMENT_SYMBOL}.{KRAKEN_VENUE}"

# Starting balance
STARTING_BALANCE_USD: float = 10000.0

# === V2 Indicators ===
# Higher timeframe regime
FAST_EMA_PERIODS: int = 50     # EMA(50) – bullish regime filter
SLOW_EMA_PERIODS: int = 200    # EMA(200) – slow trend
DONCHIAN_WINDOW: int = 80      # Breakout lookback
ATR_PERIOD: int = 20           # ATR for stops
VOLUME_MEDIAN_PERIOD: int = 80 # Rolling volume median for confirmation

# === Risk ===
RISK_PER_TRADE: float = 0.0025          # 0.25% risk per trade
MAX_NOTIONAL_EXPOSURE_PCT: float = 0.30  # 30% max notional
MIN_POSITION_SIZE_BTC: float = 0.001     # Minimum position size

# === Timing ===
TIMEFRAME_BARS: int = 15  # 15-minute primary
COOLDOWN_BARS: int = 48   # 48 * 15m = 12h cooldown after entry/exit
MAKER_ENTRY_EXPIRY_BARS: int = 6  # Limit expiry in bars (~1.5h on 15m)

# === Fees ===
MAKER_FEE: float = 0.0025  # 0.25% maker
TAKER_FEE: float = 0.0040  # 0.40% taker

# === Fee-aware filter ===
# Total friction = entry maker + exit taker + slippage buffer
SLIPPAGE_BUFFER_PCT: float = 0.0010  # 0.10% extra buffer for slippage
EXPECTED_MOVE_MULTIPLIER: float = 4.0  # require ATR >= 4x round-trip friction cost

# === Exits ===
INITIAL_STOP_ATR_MULTIPLIER: float = 2.0   # Initial ATR stop
TRAILING_STOP_ATR_MULTIPLIER: float = 1.5  # Trailing ATR stop

# === Live guard ===
MAX_ORDER_NOTIONAL_USD: float = 25.0
MAX_DAILY_LOSS_USD: float = 25.0
