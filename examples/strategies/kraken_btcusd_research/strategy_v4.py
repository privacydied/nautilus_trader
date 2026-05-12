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
V4 1h Trend-Following Strategy.

Final bar-level BTC/USD spot technical-signal test.
V1/V2/V3 are rejected. If this fails, stop BTC/USD spot technical research.

Hypothesis: Higher-timeframe BTC/USD trend moves may be large enough to
overcome fee friction because turnover is much lower and average move
per trade is larger.

Rules:
- Long-only, spot, no leverage, no margin, no short
- EMA(50) > EMA(200) for trend filter
- Close breaks above Donchian(100) high for breakout
- ATR expansion filter: current ATR > rolling median ATR(100)
- ATR trailing stop: 4x ATR from highest high since entry
- Regime break exit: close below EMA(100)
- Taker entry/exit (market orders, conservative)
- 0.25% equity risk per trade, 30% max notional
"""

from decimal import Decimal
from typing import Deque, Optional, Deque as deque_type

from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.model.orders import Order

from nautilus_trader.indicators import DonchianChannel as Donchian
from nautilus_trader.indicators import AverageTrueRange as ATR
from nautilus_trader.indicators import ExponentialMovingAverage as EMA


class KrakenBTCUSDV4TrendConfig(StrategyConfig):
    """Configuration for V4 1h trend-following strategy."""
    instrument_id: InstrumentId
    bar_type: object  # BarType

    # EMA
    ema_fast_period: int = 50
    ema_slow_period: int = 200
    ema_regime_period: int = 100  # for regime-break exit

    # Donchian breakout
    donchian_window: int = 100

    # ATR
    atr_period: int = 20
    atr_expansion_window: int = 100

    # Exits
    trailing_stop_atr_multiplier: float = 4.0

    # Cooldown
    cooldown_bars: int = 6

    # Risk
    risk_percent: float = 0.0025
    max_notional_pct: float = 0.30
    min_position_size_btc: float = 0.001
    maker_fee: float = 0.0025
    taker_fee: float = 0.0040


class KrakenBTCUSDV4TrendStrategy(Strategy):
    """
    V4 1h trend-following strategy.

    Final bar-level BTC/USD spot technical-signal test.
    Entry: EMA trend + Donchian breakout + ATR expansion.
    Exit: ATR trailing stop or close < EMA(100).
    """

    def __init__(self, config: KrakenBTCUSDV4TrendConfig):
        super().__init__(config)

        # Config
        self.instrument_id: InstrumentId = config.instrument_id
        self.bar_type: object = config.bar_type
        self.ema_fast_period: int = config.ema_fast_period
        self.ema_slow_period: int = config.ema_slow_period
        self.ema_regime_period: int = config.ema_regime_period
        self.donchian_window: int = config.donchian_window
        self.atr_period: int = config.atr_period
        self.atr_expansion_window: int = config.atr_expansion_window
        self.trailing_stop_atr_multiplier: float = config.trailing_stop_atr_multiplier
        self.cooldown_bars: int = config.cooldown_bars
        self.risk_percent: float = config.risk_percent
        self.max_notional_pct: float = config.max_notional_pct
        self.min_position_size_btc: float = config.min_position_size_btc
        self.maker_fee: float = config.maker_fee
        self.taker_fee: float = config.taker_fee

        # Indicators
        self.ema_fast: Optional[EMA] = None
        self.ema_slow: Optional[EMA] = None
        self.ema_regime: Optional[EMA] = None
        self.donchian: Optional[Donchian] = None
        self.atr: Optional[ATR] = None

        # ATR history for expansion filter
        self._atr_history: Deque[float] = deque_type()

        # Position state (tracked via on_order_filled, not on_position_changed)
        self._has_open_position: bool = False
        self._position_qty: float = 0.0
        self.entry_price: Optional[float] = None
        self.entry_bar: int = 0
        self.highest_high_since_entry: Optional[float] = None

        # Cooldown
        self.cooldown_timer: int = -1

        # Bar counter
        self.bars_processed: int = 0

        # Init indicators
        self._init_indicators()

    def on_start(self) -> None:
        self.subscribe_bars(self.bar_type)  # type: ignore
        self.log.info(
            f"V4 1h Trend-Following started: {self.instrument_id} | "
            f"EMA({self.ema_fast_period}/{self.ema_slow_period}) | "
            f"Donchian({self.donchian_window}) | "
            f"ATR trailing={self.trailing_stop_atr_multiplier}x"
        )

    def on_bar(self, bar: Bar) -> None:
        self.bars_processed += 1
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)

        # Update indicators
        if self.ema_fast:
            self.ema_fast.update_raw(close)
        if self.ema_slow:
            self.ema_slow.update_raw(close)
        if self.ema_regime:
            self.ema_regime.update_raw(close)
        if self.donchian:
            self.donchian.update_raw(high, low)
        if self.atr:
            atr_val = self.atr.update_raw(high, low, close)
            if atr_val is not None:
                a = float(atr_val) if not isinstance(atr_val, float) else atr_val
                self._atr_history.append(a)
                while len(self._atr_history) > self.atr_expansion_window + 1:
                    self._atr_history.popleft()

        # Cooldown tick
        if self.cooldown_timer > 0:
            self.cooldown_timer -= 1

        # Update trailing stop high
        if self._has_open_position and self.highest_high_since_entry is not None:
            if high > self.highest_high_since_entry:
                self.highest_high_since_entry = high

        # Check warm-up
        if not self._indicators_warmed_up():
            self.log.debug(f"Bar {self.bars_processed}: warming up...")
            return

        # --- Exit checks ---
        if self._has_open_position:
            exit_order = self._check_exit(bar)
            if exit_order is not None:
                self.submit_order(exit_order)
                return

        # --- Entry checks ---
        if not self._has_open_position and self.cooldown_timer <= 0:
            entry_order = self._check_entry(bar)
            if entry_order is not None:
                self.submit_order(entry_order)

    def on_order_filled(self, fill: "OrderFilled") -> None:
        """Track position state from fill events."""
        qty = float(fill.last_qty.as_decimal())
        if fill.order_side == OrderSide.BUY and qty > 0:
            self._has_open_position = True
            self._position_qty += qty
            self.entry_price = float(fill.last_px)
            self.entry_bar = self.bars_processed
            self.highest_high_since_entry = float(fill.last_px)
            self.log.info(
                f"Bar {self.bars_processed}: BUY filled at {self.entry_price:.2f}, "
                f"qty={self._position_qty}"
            )
        elif fill.order_side == OrderSide.SELL and qty > 0:
            self._position_qty -= qty
            if self._position_qty <= 1e-10:
                self._has_open_position = False
                self.entry_price = None
                self.entry_bar = 0
                self.highest_high_since_entry = None
                if self.cooldown_timer <= 0:
                    self.cooldown_timer = self.cooldown_bars
                self.log.info(
                    f"Bar {self.bars_processed}: SELL filled, position closed"
                )

    def on_position_changed(self, position) -> None:
        pass  # Position state tracked via on_order_filled

    def on_stop(self) -> None:
        self.log.info("V4 1h Trend-Following stopped.")

    def _init_indicators(self) -> None:
        self.ema_fast = EMA(period=self.ema_fast_period)
        self.ema_slow = EMA(period=self.ema_slow_period)
        self.ema_regime = EMA(period=self.ema_regime_period)
        self.donchian = Donchian(period=self.donchian_window)
        self.atr = ATR(period=self.atr_period)

    def _indicators_warmed_up(self) -> bool:
        warmup = max(
            self.ema_fast_period, self.ema_slow_period,
            self.ema_regime_period, self.donchian_window,
        )
        return self.bars_processed > warmup

    def _atr_value(self) -> float:
        if self.atr and self.atr.initialized:
            v = self.atr.value
            return float(v) if not isinstance(v, float) else v
        return 0.0

    def _atr_median(self) -> float:
        """Rolling median of ATR values."""
        if len(self._atr_history) < 10:
            return 0.0
        h = sorted(self._atr_history)
        n = len(h)
        if n % 2 == 0:
            return (h[n // 2 - 1] + h[n // 2]) / 2.0
        return float(h[n // 2])

    def _check_entry(self, bar: Bar) -> Optional[Order]:
        """
        Entry conditions:
        1. EMA(50) > EMA(200) -- trend filter
        2. Close > Donchian(100) high -- breakout
        3. ATR(20) > 0
        4. ATR(20) > rolling median ATR(100) -- ATR expansion
        """
        close = float(bar.close)

        if self.ema_fast is None or self.ema_slow is None:
            return None
        if self.ema_fast.value <= self.ema_slow.value:
            return None

        if self.donchian is None:
            return None
        if close <= self.donchian.upper:
            return None

        atr = self._atr_value()
        if atr <= 0:
            return None

        atr_median = self._atr_median()
        if atr_median > 0 and atr <= atr_median:
            return None

        account_value = self.get_account_value()
        if account_value is None or account_value <= 0:
            return None

        stop_distance = atr * self.trailing_stop_atr_multiplier
        position_size = _calculate_position_size(
            account_value=account_value,
            risk_percent=self.risk_percent,
            entry_price=Decimal(str(close)),
            stop_price=Decimal(str(close - stop_distance)),
            notional_limit=self.max_notional_pct,
            min_size=self.min_position_size_btc,
            fee_rate=self.taker_fee,
        )
        if position_size is None or float(position_size.as_decimal()) <= 0:
            return None

        self.cooldown_timer = self.cooldown_bars

        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=position_size,
            time_in_force=TimeInForce.GTC,
        )

        self.log.info(
            f"Bar {self.bars_processed}: ENTRY {close:.2f} | "
            f"Donchian high={self.donchian.upper:.2f} | "
            f"ATR={atr:.2f} median={atr_median:.2f} | "
            f"EMA50={self.ema_fast.value:.2f} EMA200={self.ema_slow.value:.2f}"
        )
        return order

    def _check_exit(self, bar: Bar) -> Optional[Order]:
        """
        Exit conditions:
        1. ATR trailing stop: close < highest_high - 4*ATR
        2. Regime break: close < EMA(100)
        """
        if self.entry_price is None:
            return None

        close = float(bar.close)
        atr = self._atr_value()

        # ATR trailing stop
        if atr > 0 and self.highest_high_since_entry is not None:
            trailing_stop = self.highest_high_since_entry - (
                atr * self.trailing_stop_atr_multiplier
            )
            if close <= trailing_stop:
                self.log.info(
                    f"EXIT: ATR trailing stop | highest={self.highest_high_since_entry:.2f} "
                    f"stop={trailing_stop:.2f} close={close:.2f}"
                )
                return self._create_exit_order()

        # EMA(100) regime break
        if self.ema_regime and close < self.ema_regime.value:
            self.log.info(
                f"EXIT: EMA(100) regime break | EMA100={self.ema_regime.value:.2f} "
                f"close={close:.2f}"
            )
            return self._create_exit_order()

        return None

    def _create_exit_order(self) -> Optional[Order]:
        """Market sell to exit position."""
        if self._position_qty <= 1e-10:
            return None
        return self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            quantity=Quantity(self._position_qty, 8),
            time_in_force=TimeInForce.GTC,
        )

    def get_account_value(self) -> Optional[Decimal]:
        try:
            account = self.portfolio.account(self.instrument_id.venue)
            if account is not None:
                return account.balance_total(USD).as_decimal()
        except Exception:
            pass
        return None


def _calculate_position_size(
    account_value: Decimal,
    risk_percent: float,
    entry_price: Decimal,
    stop_price: Decimal,
    notional_limit: float,
    min_size: float,
    fee_rate: float,
) -> Optional[Quantity]:
    if account_value is None or entry_price <= 0:
        return None
    stop_distance = entry_price - stop_price
    if stop_distance <= 0:
        return None
    risk_amount = account_value * Decimal(str(risk_percent))
    position_size_btc = risk_amount / stop_distance
    max_notional = account_value * Decimal(str(notional_limit))
    max_by_notional = max_notional / entry_price
    if position_size_btc > max_by_notional:
        position_size_btc = max_by_notional
    if position_size_btc < Decimal(str(min_size)):
        return None
    qty_int = int(position_size_btc * 100_000_000)
    position_size_btc = Decimal(qty_int) / Decimal("100000000")
    return Quantity(position_size_btc, 8)
