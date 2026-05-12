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
V3 Mean Reversion Strategy for Kraken BTC/USD spot.

Hypothesis: BTC/USD offers better expectancy buying passive pullbacks near
range lows with maker-style entries, not chasing breakouts.

Key differences from V1/V2:
- Mean reversion signal (z-score + RSI) instead of Donchian breakout
- Post-only limit entries with no same-bar fills
- Range regime filter (avoid trending markets)
- Conservative taker exits
"""

from decimal import Decimal
from typing import Deque, Optional, Deque as deque_type

from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide, OrderType, PriceType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.model.orders import Order

from nautilus_trader.indicators import AverageTrueRange as ATR
from nautilus_trader.indicators import RelativeStrengthIndex as RSI


class KrakenBTCUSDMeanReversionConfig(StrategyConfig):
    """Configuration for V3 mean reversion strategy."""
    instrument_id: InstrumentId

    # Timeframe
    bar_type: object  # BarType

    # Rolling statistics
    mean_window: int = 80
    std_window: int = 80
    rsi_period: int = 14
    rsi_oversold: float = 30.0

    # Z-score
    zscore_entry: float = -2.0
    zscore_exit: float = -0.2

    # ATR
    atr_period: int = 20

    # Range filter
    range_window: int = 96
    min_range_fee_multiple: float = 4.0

    # Entry
    entry_offset_atr: float = 0.10

    # Maker order
    maker_order_expiry_bars: int = 6
    cooldown_bars: int = 12

    # Risk / stops
    initial_stop_atr_multiplier: float = 2.0
    trailing_stop_atr_multiplier: float = 1.5
    time_stop_bars: int = 32

    # Fees
    maker_fee: float = 0.0025
    taker_fee: float = 0.0040

    # Limits
    risk_percent: float = 0.0025
    max_notional_pct: float = 0.30
    min_position_size_btc: float = 0.001

    # Trade size (default; actual size computed via risk)
    trade_size: Quantity = None  # type: ignore


class KrakenBTCUSDMeanReversionStrategy(Strategy):
    """
    Mean reversion strategy with maker-first entries and conservative taker exits.

    State machine:
      NO_ORDER -> PENDING_FILL (limit placed, waiting for price)
      PENDING_FILL -> FILLED (limit hit, position opened)
      PENDING_FILL -> EXPIRED (N bars passed without fill)
      FILLED -> EXITED (target / stop / time stop hit)

    No same-bar fills. Limit becomes active next bar after signal.
    """

    def __init__(self, config: KrakenBTCUSDMeanReversionConfig):
        super().__init__(config)

        # Config
        self.instrument_id: InstrumentId = config.instrument_id
        self.bar_type: object = config.bar_type
        self.mean_window: int = config.mean_window
        self.std_window: int = config.std_window
        self.rsi_period: int = config.rsi_period
        self.rsi_oversold: float = config.rsi_oversold
        self.zscore_entry: float = config.zscore_entry
        self.zscore_exit: float = config.zscore_exit
        self.atr_period: int = config.atr_period
        self.range_window: int = config.range_window
        self.min_range_fee_multiple: float = config.min_range_fee_multiple
        self.entry_offset_atr: float = config.entry_offset_atr
        self.maker_order_expiry_bars: int = config.maker_order_expiry_bars
        self.cooldown_bars: int = config.cooldown_bars
        self.initial_stop_atr_multiplier: float = config.initial_stop_atr_multiplier
        self.trailing_stop_atr_multiplier: float = config.trailing_stop_atr_multiplier
        self.time_stop_bars: int = config.time_stop_bars
        self.maker_fee: float = config.maker_fee
        self.taker_fee: float = config.taker_fee
        self.risk_percent: float = config.risk_percent
        self.max_notional_pct: float = config.max_notional_pct
        self.min_position_size_btc: float = config.min_position_size_btc

        # Indicators
        self.atr: Optional[ATR] = None
        self.rsi: Optional[RSI] = None

        # Rolling windows for mean/std calculation
        self._close_history: Deque[float] = deque_type()
        self._high_history: Deque[float] = deque_type()
        self._low_history: Deque[float] = deque_type()

        # Position state
        self.entry_bar: int = 0
        self.entry_price: Optional[float] = None
        self.initial_stop_price: Optional[float] = None
        self.highest_high_since_entry: Optional[float] = None

        # Pending limit order state
        self._pending_limit_price: Optional[float] = None
        self._pending_limit_bar: int = -1
        self._is_pending: bool = False

        # Cooldown
        self.cooldown_timer: int = -1

        # Bar counter
        self.bars_processed: int = 0

        # Init indicators
        self._init_indicators()

    # -- Lifecycle --

    def on_start(self) -> None:
        self.subscribe_bars(self.bar_type)  # type: ignore
        self.log.info(
            f"V3 Mean Reversion started: {self.instrument_id} | "
            f"z_entry={self.zscore_entry} | rsi_oversold={self.rsi_oversold} | "
            f"range_window={self.range_window}"
        )

    def on_bar(self, bar: Bar) -> None:
        self.bars_processed += 1
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)

        # Update histories
        self._close_history.append(close)
        self._high_history.append(high)
        self._low_history.append(low)
        # Prune to max needed
        max_window = max(self.range_window, self.mean_window, self.std_window)
        while len(self._close_history) > max_window + 1:
            self._close_history.popleft()
            self._high_history.popleft()
            self._low_history.popleft()

        # Update indicators
        if self.atr:
            self.atr.update_raw(high, low, close)
        if self.rsi:
            self.rsi.update_raw(close)

        # Cooldown tick
        if self.cooldown_timer > 0:
            self.cooldown_timer -= 1

        # Check warm-up
        if not self._indicators_warmed_up():
            self.log.debug(f"Bar {self.bars_processed}: warming up...")
            return

        # --- Pending order expiry check ---
        if self._is_pending:
            bars_since_signal = self.bars_processed - self._pending_limit_bar
            if bars_since_signal > self.maker_order_expiry_bars:
                self.log.info(
                    f"Bar {self.bars_processed}: pending limit expired after "
                    f"{bars_since_signal} bars (limit={self._pending_limit_price})"
                )
                self._clear_pending()
            elif self._check_pending_fill(bar):
                # Filled on this bar (low <= limit_price, bar index > signal bar)
                self._on_pending_fill(bar)
                return

        # --- Exit checks (position open) ---
        if self.position.is_net_long or self.position.is_flat:
            if self.position.is_net_long:
                exit_order = self._check_exit(bar)
                if exit_order is not None:
                    self.submit_order(exit_order)
                    return

        # --- Entry checks (no position, no pending) ---
        if not self.position.is_net_long and not self._is_pending and self.cooldown_timer <= 0:
            entry_order = self._check_entry(bar)
            if entry_order is not None:
                # Create pending limit order (do NOT submit yet)
                self._create_pending_limit(entry_order, bar)

    def on_order_filled(self, fill: Order) -> None:
        # Note: for maker limit orders, the engine fills them
        # We track position state via the position callback
        pass

    def on_position_changed(self, position) -> None:
        """Track position lifecycle."""
        if position.is_closed:
            # Reset entry tracking
            self.entry_price = None
            self.entry_bar = 0
            self.initial_stop_price = None
            self.highest_high_since_entry = None
            # Set cooldown
            if self.cooldown_timer <= 0:
                self.cooldown_timer = self.cooldown_bars
                self.log.debug(f"Bar {self.bars_processed}: cooldown set after exit")

        elif position.is_open or position.is_net_long:
            if self.entry_price is None:
                self.entry_price = float(position.avg_px_open)
                self.entry_bar = self.bars_processed
                self.log.info(
                    f"Bar {self.bars_processed}: position opened at {self.entry_price}"
                )

    def on_stop(self) -> None:
        self.log.info("V3 Mean Reversion stopped.")

    # -- Helpers --

    def _init_indicators(self) -> None:
        self.atr = ATR(period=self.atr_period)
        self.rsi = RSI(period=self.rsi_period)

    def _indicators_warmed_up(self) -> bool:
        warmup = max(self.mean_window, self.std_window, self.rsi_period, self.atr_period)
        return len(self._close_history) > warmup

    def _rolling_mean(self) -> float:
        n = min(self.mean_window, len(self._close_history))
        history = list(self._close_history)[-n:]
        return sum(history) / len(history) if history else 0.0

    def _rolling_std(self) -> float:
        n = min(self.std_window, len(self._close_history))
        history = list(self._close_history)[-n:]
        if len(history) < 2:
            return 0.0
        m = sum(history) / len(history)
        variance = sum((x - m) ** 2 for x in history) / (len(history) - 1)
        return variance ** 0.5

    def _atr_value(self) -> float:
        if self.atr and self.atr.initialized:
            return float(self.atr.value)
        return 0.0

    def _rsi_value(self) -> float:
        if self.rsi and self.rsi.initialized:
            return float(self.rsi.value)
        return 50.0  # neutral default

    def _recent_range_pct(self) -> float:
        """Calculate recent high-low range as a percentage of mean price."""
        n = min(self.range_window, len(self._high_history))
        if n < 2:
            return 0.0
        highs = list(self._high_history)[-n:]
        lows = list(self._low_history)[-n:]
        range_high = max(highs)
        range_low = min(lows)
        mean = self._rolling_mean()
        if mean <= 0:
            return 0.0
        return (range_high - range_low) / mean

    def _mean_slope(self, lookback: int = 5) -> float:
        """Check if rolling mean is trending up or down."""
        n = min(self.mean_window, len(self._close_history))
        if n < lookback + 1:
            return 0.0
        closes = list(self._close_history)
        # Compare mean of last N closes vs mean of closes before that
        recent = sum(closes[-lookback:]) / lookback
        older = sum(closes[-lookback*2:-lookback]) / lookback if len(closes) >= lookback*2 else closes[0]
        if older <= 0:
            return 0.0
        return (recent - older) / older

    # -- Entry Logic --

    def _check_entry(self, bar: Bar) -> Optional[Order]:
        """Check mean reversion entry conditions."""
        close = float(bar.close)

        # 1. Z-score check
        mean = self._rolling_mean()
        std = self._rolling_std()
        if std <= 0:
            return None
        zscore = (close - mean) / std
        if zscore > self.zscore_entry:
            return None

        # 2. RSI oversold check
        rsi = self._rsi_value()
        if rsi > self.rsi_oversold:
            return None

        # 3. ATR check
        atr = self._atr_value()
        if atr <= 0:
            return None

        # 4. Fee-aware range filter
        estimated_friction = self.maker_fee + self.taker_fee + 0.001  # slippage buffer
        range_pct = self._recent_range_pct()
        if range_pct < self.min_range_fee_multiple * estimated_friction:
            return None

        # 5. Regime filter: avoid strong downtrends
        slope = self._mean_slope()
        if slope < -0.005:  # more than 0.5% downward shift
            return None

        # 6. Calculate position size
        account_value = self.get_account_value()
        if account_value is None or account_value <= 0:
            return None

        stop_distance = atr * self.initial_stop_atr_multiplier
        position_size = _calculate_position_size(
            account_value=account_value,
            risk_percent=self.risk_percent,
            entry_price=Decimal(str(close)),
            stop_price=Decimal(str(close - stop_distance)),
            notional_limit=self.max_notional_pct,
            min_size=self.min_position_size_btc,
            fee_rate=self.maker_fee,
        )
        if position_size is None or float(position_size.as_decimal()) <= 0:
            return None

        order = self.order_factory.limit(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=position_size,
            price=Price(close - 0.01, 2),  # placeholder, actual limit set in _create_pending_limit
            time_in_force=TimeInForce.GTC,
        )
        return order

    def _create_pending_limit(self, order_template: Order, bar: Bar) -> None:
        """Create a pending maker entry (not submitted yet)."""
        close = float(bar.close)
        mean = self._rolling_mean()
        std = self._rolling_std()
        atr = self._atr_value()

        # Limit price: min of close - offset*ATR and mean + zscore*std
        limit_a = close - self.entry_offset_atr * atr
        limit_b = mean + self.zscore_entry * std
        limit_price = min(limit_a, limit_b)

        self._pending_limit_price = limit_price
        self._pending_limit_bar = self.bars_processed
        self._is_pending = True

        self.log.info(
            f"Bar {self.bars_processed}: pending maker limit created | "
            f"limit={limit_price:.2f} | close={close:.2f} | "
            f"z={(close-mean)/std:.2f} | expiry in {self.maker_order_expiry_bars} bars"
        )

    def _check_pending_fill(self, bar: Bar) -> bool:
        """Check if pending limit order should fill on this bar."""
        if not self._is_pending:
            return False
        if bar is None:
            return False

        low = float(bar.low)
        limit_price = self._pending_limit_price
        if limit_price is None:
            return False

        # Fill if bar low trades through limit price
        # (strictly: low <= limit_price means price reached our level)
        return low <= limit_price

    def _on_pending_fill(self, bar: Bar) -> None:
        """Process pending limit fill."""
        limit_price = self._pending_limit_price
        if limit_price is None:
            return

        atr = self._atr_value()
        close = float(bar.close)

        # Create actual limit order at the pre-calculated price
        account_value = self.get_account_value()
        if account_value is None:
            self._clear_pending()
            return

        stop_distance = atr * self.initial_stop_atr_multiplier
        position_size = _calculate_position_size(
            account_value=account_value,
            risk_percent=self.risk_percent,
            entry_price=Decimal(str(limit_price)),
            stop_price=Decimal(str(limit_price - stop_distance)),
            notional_limit=self.max_notional_pct,
            min_size=self.min_position_size_btc,
            fee_rate=self.maker_fee,
        )
        if position_size is None:
            self.log.warning(f"Bar {self.bars_processed}: position size calc failed on fill")
            self._clear_pending()
            return

        # Submit the actual limit order
        order = self.order_factory.limit(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=position_size,
            price=Price(limit_price, 2),
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)
        self.log.info(
            f"Bar {self.bars_processed}: limit order submitted at {limit_price:.2f} | "
            f"bar low={float(bar.low):.2f} | close={close:.2f}"
        )
        self._clear_pending()

    # -- Exit Logic --

    def _check_exit(self, bar: Bar) -> Optional[Order]:
        """Check exit conditions for open position."""
        close = float(bar.close)
        high = float(bar.high)

        if self.entry_price is None:
            return None

        # 1. Mean reversion target: z-score >= zscore_exit or close >= mean
        mean = self._rolling_mean()
        std = self._rolling_std()
        if std > 0:
            zscore = (close - mean) / std
            if zscore >= self.zscore_exit or close >= mean:
                self.log.info(
                    f"Exit: mean reversion target | z={zscore:.2f} | "
                    f"close={close:.2f} vs mean={mean:.2f}"
                )
                return self._create_exit_order(bar)

        # 2. Stop loss
        atr = self._atr_value()
        if atr > 0:
            initial_stop = self.entry_price - (atr * self.initial_stop_atr_multiplier)
            if close <= initial_stop:
                self.log.info(
                    f"Exit: initial stop loss | entry={self.entry_price:.2f} | "
                    f"stop={initial_stop:.2f} | close={close:.2f}"
                )
                return self._create_exit_order(bar)

        # 3. Trailing stop
        if self.highest_high_since_entry is not None and atr > 0:
            trailing_stop = self.highest_high_since_entry - (atr * self.trailing_stop_atr_multiplier)
            if close <= trailing_stop:
                self.log.info(
                    f"Exit: trailing stop | highest={self.highest_high_since_entry:.2f} | "
                    f"stop={trailing_stop:.2f} | close={close:.2f}"
                )
                return self._create_exit_order(bar)

        # 4. Time stop
        bars_in_position = self.bars_processed - self.entry_bar
        if bars_in_position >= self.time_stop_bars:
            self.log.info(
                f"Exit: time stop after {bars_in_position} bars"
            )
            return self._create_exit_order(bar)

        return None

    def _create_exit_order(self, bar: Bar) -> Optional[Order]:
        """Create a market sell order to exit position."""
        pos_qty = self.position.quantity
        if pos_qty is None or float(pos_qty.as_decimal()) <= 0:
            return None

        return self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            quantity=pos_qty,
            time_in_force=TimeInForce.GTC,
        )

    def _update_trailing_stop(self, bar: Bar) -> None:
        """Update highest high since entry for trailing stop."""
        high = float(bar.high)
        if self.highest_high_since_entry is None:
            self.highest_high_since_entry = high
        elif high > self.highest_high_since_entry:
            self.highest_high_since_entry = high

    def _clear_pending(self) -> None:
        self._is_pending = False
        self._pending_limit_price = None
        self._pending_limit_bar = -1

    # -- Utility --

    def get_account_value(self) -> Optional[Decimal]:
        """Get account value in USD."""
        try:
            account = self.portfolio.account(self.instrument_id.venue)
            if account is not None:
                return account.balance_total(USD).as_decimal()
        except Exception:
            pass
        return None

    @property
    def position(self):
        """Get current position for the strategy's instrument venue."""
        try:
            account = self.portfolio.account(self.instrument_id.venue)
            if account is not None:
                positions = account.positions_open(venue=self.instrument_id.venue)
                if positions:
                    return positions[0]
        except Exception:
            pass
        # Return a fake object with known state
        class _FlatPos:
            is_open = False
            is_closed = False
            is_net_long = False
            is_flat = True
            quantity = Quantity(0, 8)
            avg_px_open = 0.0
        return _FlatPos()


def _calculate_position_size(
    account_value: Decimal,
    risk_percent: float,
    entry_price: Decimal,
    stop_price: Decimal,
    notional_limit: float,
    min_size: float,
    fee_rate: float,
) -> Optional[Quantity]:
    """Calculate position size based on risk parameters."""
    if account_value is None or entry_price <= 0:
        return None
    stop_distance = entry_price - stop_price
    if stop_distance <= 0:
        return None
    risk_amount = account_value * Decimal(str(risk_percent))
    position_size_btc = risk_amount / stop_distance
    # Apply notional limit
    max_notional = account_value * Decimal(str(notional_limit))
    max_by_notional = max_notional / entry_price
    if position_size_btc > max_by_notional:
        position_size_btc = max_by_notional
    if position_size_btc < Decimal(str(min_size)):
        return None
    # Round to 8 decimal places
    qty_int = int(position_size_btc * 100_000_000)
    position_size_btc = Decimal(qty_int) / Decimal("100000000")
    return Quantity(position_size_btc, 8)


def create_strategy_v3() -> KrakenBTCUSDMeanReversionStrategy:
    """Create and return a V3 mean reversion strategy instance."""
    from nautilus_trader.model.data import BarSpecification, BarAggregation
    from nautilus_trader.model.identifiers import InstrumentId

    from examples.strategies.kraken_btcusd_research.config_v3 import (
        INSTRUMENT_ID,
        MIN_POSITION_SIZE_BTC,
        MEAN_WINDOW,
        STD_WINDOW,
        RSI_PERIOD,
        RSI_OVERSOLD,
        ZSCORE_ENTRY,
        ZSCORE_EXIT,
        ATR_PERIOD,
        RANGE_WINDOW,
        MIN_RANGE_FEE_MULTIPLE,
        ENTRY_OFFSET_ATR,
        MAKER_ORDER_EXPIRY_BARS,
        COOLDOWN_BARS,
        INITIAL_STOP_ATR_MULTIPLIER,
        TRAILING_STOP_ATR_MULTIPLIER,
        TIME_STOP_BARS,
        MAKER_FEE,
        TAKER_FEE,
        TIMEFRAME_BARS,
    )

    iid = InstrumentId.from_str(INSTRUMENT_ID)
    spec = BarSpecification(TIMEFRAME_BARS, BarAggregation.MINUTE, PriceType.LAST)
    bar_type = BarType(iid, spec)

    cfg = KrakenBTCUSDMeanReversionConfig(
        instrument_id=iid,
        bar_type=bar_type,
        mean_window=MEAN_WINDOW,
        std_window=STD_WINDOW,
        rsi_period=RSI_PERIOD,
        rsi_oversold=RSI_OVERSOLD,
        zscore_entry=ZSCORE_ENTRY,
        zscore_exit=ZSCORE_EXIT,
        atr_period=ATR_PERIOD,
        range_window=RANGE_WINDOW,
        min_range_fee_multiple=MIN_RANGE_FEE_MULTIPLE,
        entry_offset_atr=ENTRY_OFFSET_ATR,
        maker_order_expiry_bars=MAKER_ORDER_EXPIRY_BARS,
        cooldown_bars=COOLDOWN_BARS,
        initial_stop_atr_multiplier=INITIAL_STOP_ATR_MULTIPLIER,
        trailing_stop_atr_multiplier=TRAILING_STOP_ATR_MULTIPLIER,
        time_stop_bars=TIME_STOP_BARS,
        maker_fee=MAKER_FEE,
        taker_fee=TAKER_FEE,
    )
    return KrakenBTCUSDMeanReversionStrategy(config=cfg)
