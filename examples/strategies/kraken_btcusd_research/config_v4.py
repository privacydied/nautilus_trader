#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
V4 1h Trend-Following Configuration.

Final bar-level BTC/USD spot technical-signal test.
V1/V2/V3 are rejected.

Hypothesis: Higher-timeframe BTC/USD trend moves may be large enough to
overcome fee friction because turnover is much lower and average move
per trade is larger.

Final rule: if 1h V4 fails gross, do not test 2h/3h/4h/6h/12h.
Stop BTC/USD spot bar-level technical research under this fee model.
"""

# --- Shared constants ---

KRAKEN_VENUE: str = "KRAKEN"
INSTRUMENT_SYMBOL: str = "BTC/USD"
INSTRUMENT_ID: str = f"{INSTRUMENT_SYMBOL}.{KRAKEN_VENUE}"

STARTING_BALANCE_USD: float = 10000.0
RISK_PER_TRADE: float = 0.0025  # 0.25%
MAX_NOTIONAL_EXPOSURE_PCT: float = 0.30  # 30%
MIN_POSITION_SIZE_BTC: float = 0.001

# Fees
MAKER_FEE: float = 0.0025  # 0.25%
TAKER_FEE: float = 0.0040  # 0.40%

# Timeframe
TIMEFRAME_BARS: int = 60  # 1-hour bars

# --- V4 Trend-following parameters ---

# EMA trend filter
EMA_FAST_PERIODS: int = 50
EMA_SLOW_PERIODS: int = 200

# Donchian breakout
DONCHIAN_WINDOW: int = 100

# ATR filter
ATR_PERIOD: int = 20
ATR_EXPANSION_WINDOW: int = 100

# Exits
TRAILING_STOP_ATR_MULTIPLIER: float = 4.0

# Cooldown
COOLDOWN_BARS: int = 6
