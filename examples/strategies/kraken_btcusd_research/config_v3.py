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
V3 Mean Reversion Configuration.

Hypothesis: BTC/USD may offer better expectancy when buying passive pullbacks
near range lows, not chasing breakouts, with maker-style entries and
conservative taker exits.
"""

from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.config import StrategyConfig

# NOTE: The config class itself cannot go here without importing
# nautilus_trader modules (which need the Rust .so). Keep this file
# pure-constant so it can be imported even if nautilus_trader is broken.
# Import KrakenBTCUSDMeanReversionConfig from strategy_v3.py instead.

# --- Shared constants (same as V1/V2) ---

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
SLIPPAGE_BUFFER: float = 0.0010  # 0.10%

# Timeframe
TIMEFRAME_BARS: int = 15  # 15-minute bars (15m first)

# --- Mean reversion parameters ---

# Rolling statistics
MEAN_WINDOW: int = 80
STD_WINDOW: int = 80

# RSI
RSI_PERIOD: int = 14
RSI_OVERSOLD: float = 30.0

# Z-score thresholds
ZSCORE_ENTRY: float = -2.0
ZSCORE_EXIT: float = -0.2

# ATR
ATR_PERIOD: int = 20

# Range / regime filter
RANGE_WINDOW: int = 96  # last 96 bars for range width
MIN_RANGE_FEE_MULTIPLE: float = 4.0  # range must be >= 4x estimated friction

# Entry
ENTRY_OFFSET_ATR: float = 0.10  # how far below close to place limit

# Maker order behaviour
MAKER_ORDER_EXPIRY_BARS: int = 6  # cancel if not filled within N bars
COOLDOWN_BARS: int = 12

# Risk / stops
INITIAL_STOP_ATR_MULTIPLIER: float = 2.0
TIME_STOP_BARS: int = 32
TRAILING_STOP_ATR_MULTIPLIER: float = 1.5
