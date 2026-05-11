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

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.enums import BookType, OmsType, AccountType
from nautilus_trader.model.objects import Currency, Money

# Stop loss multipliers (ATR-based)
INITIAL_STOP_ATR_MULTIPLIER: Final[float] = 2.0
TRAILING_STOP_ATR_MULTIPLIER: Final[float] = 1.5

from nautilus_trader.model.functions import currency_type_from_str

KRAKEN_VENUE: Final[str] = "KRAKEN"
INSTRUMENT_SYMBOL: Final[str] = "BTC/USD"  # Nautilus normalizes to BTC, not XBT
INSTRUMENT_ID: Final[str] = f"{INSTRUMENT_SYMBOL}.{KRAKEN_VENUE}"

# Trading parameters
STARTING_BALANCE_USD: Final[float] = 10000.0

# Technical indicators
FAST_EMA_PERIODS: Final[int] = 20
SLOW_EMA_PERIODS: Final[int] = 100
DONCHIAN_WINDOW: Final[int] = 55
ATR_PERIOD: Final[int] = 20

# Risk management
RISK_PER_TRADE: Final[float] = 0.0025  # 0.25%
MAX_NOTIONAL_EXPOSURE_PCT: Final[float] = 0.30  # 30%
MIN_POSITION_SIZE_BTC: Final[float] = 0.001  # Minimum position size in BTC
COOLDOWN_BARS: Final[int] = 12

# Fees (maker/taker)
MAKER_FEE: Final[float] = 0.0025  # 0.25%
TAKER_FEE: Final[float] = 0.004  # 0.40%

# Timeframe
TIMEFRAME_BARS: Final[int] = 5  # 5-minute bars
# Live trading safety limits
MAX_ORDER_NOTIONAL_USD: Final[float] = 25.0
MAX_DAILY_LOSS_USD: Final[float] = 25.0
