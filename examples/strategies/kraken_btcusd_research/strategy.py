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

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Dict, Any
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar, BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import OrderSide, OrderType, PriceType, TimeInForce
from nautilus_trader.model.functions import currency_type_from_str
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.model.orders import Order
from nautilus_trader.model.instruments import CurrencyPair

from nautilus_trader.indicators import DonchianChannel as Donchian, AverageTrueRange as ATR
from nautilus_trader.indicators import ExponentialMovingAverage as EMA

# Import configuration from the same package
from .config import (
    INSTRUMENT_ID,
    STARTING_BALANCE_USD,
    FAST_EMA_PERIODS,
    SLOW_EMA_PERIODS,
    DONCHIAN_WINDOW,
    ATR_PERIOD,
    RISK_PER_TRADE,
    MAX_NOTIONAL_EXPOSURE_PCT,
    MIN_POSITION_SIZE_BTC,
    COOLDOWN_BARS,
    MAKER_FEE,
    TAKER_FEE,
    TIMEFRAME_BARS,
)


class KrakenBTCUSDResearchConfig(StrategyConfig):


    """Configuration for Kraken BTC/USD research strategy."""

    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Quantity
    atr_period: int
    fast_ema_period: int
    slow_ema_period: int
    donchian_window: int
    initial_stop_atr_multiplier: float
    trailing_stop_atr_multiplier: float
    risk_percent: float
    max_notional_pct: float
    cooldown_bars: int
    maker_fee: float
    taker_fee: float


class KrakenBTCUSDResearchStrategy(Strategy):
    """EMA crossover strategy with Donchian breakout and ATR-based risk management."""

    def __init__(self, config: KrakenBTCUSDResearchConfig):
        super().__init__(config)
        
        # Configuration parameters
        self.instrument_id: InstrumentId = config.instrument_id
        self.bar_type: BarType = config.bar_type
        self.trade_size: Quantity = config.trade_size
        self.atr_period: int = config.atr_period
        self.fast_ema_period: int = config.fast_ema_period
        self.slow_ema_period: int = config.slow_ema_period
        self.donchian_window: int = config.donchian_window
        self.initial_stop_atr_multiplier: float = config.initial_stop_atr_multiplier
        self.trailing_stop_atr_multiplier: float = config.trailing_stop_atr_multiplier
        self.risk_percent: float = config.risk_percent
        self.max_notional_pct: float = config.max_notional_pct
        self.cooldown_bars: int = config.cooldown_bars
        self.maker_fee: float = config.maker_fee
        self.taker_fee: float = config.taker_fee
        
        # State variables
        self.fast_ema: Optional[EMA] = None
        self.slow_ema: Optional[EMA] = None
        self.donchian: Optional[Donchian] = None
        self.atr: Optional[ATR] = None
        self.current_position_size: Quantity = Quantity.from_str("0")
        self.entry_price: Optional[float] = None
        self.last_entry_bar: Optional[int] = None
        self.entry_bar: Optional[int] = None
        self.highest_high_since_entry: Optional[float] = None
        self.cooldown_timer: int = -1  # -1 means no cooldown
        
        # Track processed bars
        self.bars_processed: int = 0
        
        # Initialize indicators
        self._init_indicators()
    
    def _init_indicators(self) -> None:
        """Initialize technical indicators."""
        # EMA indicators
        self.fast_ema = EMA(period=self.fast_ema_period)
        self.slow_ema = EMA(period=self.slow_ema_period)
        
        # Donchian channel (for breakout)
        self.donchian = Donchian(period=self.donchian_window)
        
        # ATR (for volatility and stop loss)
        self.atr = ATR(period=self.atr_period)
    
    def on_start(self) -> None:
        """Called when strategy starts."""
        self.subscribe_bars(self.bar_type)
        self.log.info(f"Kraken BTC/USD strategy started. Instrument: {self.instrument_id}")
    
    def on_bar(self, bar: Bar) -> None:
        """Called for each new bar."""
        self.bars_processed += 1

        # Check if we have enough data for indicators (warm-up period)
        if not self._indicators_warmed_up():
            # Update indicators first so they accumulate history before checks
            self._update_indicators(bar)
            self.log.debug(f"Bar {self.bars_processed}: Indicators warming up...")
            return

        # Check for entry/exit BEFORE updating indicators.
        # This uses the prior bar's indicator values, which is the standard
        # Donchian/EMA pattern — you can't check "close > Donchian high" if
        # the Donchian was just updated with this bar's (close <= high) data.
        current_price = float(bar.close)

        exit_order = self._check_exit(current_price, bar)
        if exit_order is not None:
            self.submit_order(exit_order)
            return

        if self.cooldown_timer < 0 and float(self.current_position_size.as_decimal()) <= 0:
            entry_order = self._check_entry(current_price, bar)
            if entry_order is not None:
                self.submit_order(entry_order)

        # Now update indicators with the completed bar
        self._update_indicators(bar)

                # Check cooldown
        if self.cooldown_timer >= 0:
            self.cooldown_timer -= 1
            if self.cooldown_timer < 0:
                self.log.debug("Cooldown finished.")

        # Update trailing stop high if we have a position
        if float(self.current_position_size.as_decimal()) > 0 and self.highest_high_since_entry is not None:
            # Use the current bar's high to update the highest high since entry
            current_high = float(bar.high) if bar.high is not None else 0.0
            if current_high > self.highest_high_since_entry:
                self.highest_high_since_entry = current_high
                self.log.debug(f"New high since entry: {self.highest_high_since_entry}")
    
    def _update_indicators(self, bar: Bar) -> None:
        """Update all technical indicators with new bar data."""
        # Extract values
        high = float(bar.high) if bar.high is not None else 0.0
        low = float(bar.low) if bar.low is not None else 0.0
        close = float(bar.close)
        
        # Update indicators
        if self.fast_ema:
            self.fast_ema.update_raw(float(close))
        if self.slow_ema:
            self.slow_ema.update_raw(float(close))
        if self.donchian:
            self.donchian.update_raw(float(high), float(low))
        if self.atr:
            self.atr.update_raw(float(high), float(low), float(close))
    
    def _indicators_warmed_up(self) -> bool:
        """Check if all indicators have enough data."""
        return (
            self.fast_ema is not None and self.fast_ema.initialized
            and self.slow_ema is not None and self.slow_ema.initialized
            and self.donchian is not None and self.donchian.initialized
            and self.atr is not None and self.atr.initialized
        )
    
    def _check_entry(self, current_price: float, bar: Bar) -> Optional[Order]:
        """
        Check for long entry conditions.
        
        Entry signals:
        - Fast EMA > Slow EMA (bullish trend)
        - Price breaks above Donchian high (breakout)
        - ATR is available and > 0
        """
        # Trend filter: fast EMA above slow EMA
        if self.fast_ema is None or self.slow_ema is None:
            return None
        if self.fast_ema.value <= self.slow_ema.value:
            return None
        
        # Breakout: price above Donchian high
        if self.donchian is None:
            return None
        donchian_high = self.donchian.upper
        if current_price <= donchian_high:
            return None
        
        # ATR must be available and positive
        if self.atr is None or self.atr.value is None or self.atr.value <= 0:
            return None
        
        # Calculate position size based on risk
        account_value = self.get_account_value()
        if account_value is None:
            return None

        # Use ATR for stop distance
        atr_val = self.atr.value if self.atr else 0.0
        if atr_val is None or atr_val <= 0:
            return None
        stop_distance = atr_val * self.initial_stop_atr_multiplier

        # Calculate position size with risk management
        position_size = _calculate_position_size(
            account_value=account_value,
            risk_percent=self.risk_percent,
            entry_price=Decimal(str(current_price)),
            stop_price=Decimal(str(current_price - stop_distance)),
            notional_limit=self.max_notional_pct,
            min_size=MIN_POSITION_SIZE_BTC,
            fee_rate=self.taker_fee,
        )
        
        if position_size is None or float(position_size.as_decimal()) <= 0:
            self.log.warning(f"Position size calculation failed: {position_size}")
            return None
        
        # Set cooldown
        self.cooldown_timer = self.cooldown_bars
        self.last_entry_bar = self.bars_processed
        
        # Create simple market order for entry
        entry_order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=position_size,
            time_in_force=TimeInForce.GTC,
        )
        
        self.log.info(
            f"Long entry signal at {current_price}. "
            f"Position size: {position_size}, "
            f"Stop distance: {stop_distance}"
        )
        
        return entry_order
    
    def _check_exit(self, current_price: float, bar: Bar) -> Optional[Order]:
        """
        Check for exit conditions.

        Exit signals:
        - Price crosses below slow EMA (trend reversal)
        - Stop loss breach (initial ATR-based stop)
        """
        # If we have no position, nothing to exit
        if self.current_position_size.as_decimal() <= 0:
            return None

        # Check for trend reversal: price below slow EMA
        if self.slow_ema and current_price < self.slow_ema.value:
            self.log.info(f"Exit signal: Price {current_price} below slow EMA {self.slow_ema.value}")
            return self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=self.current_position_size,
                time_in_force=TimeInForce.GTC,
            )

        # Check ATR and stop loss
        atr_val = self.atr.value if self.atr else 0.0
        if self.entry_price is not None and self.highest_high_since_entry is not None and atr_val > 0:
            # Calculate initial stop based on entry price
            initial_stop = float(self.entry_price) - (atr_val * self.initial_stop_atr_multiplier)
            # Calculate trailing stop based on highest high since entry
            trailing_stop = self.highest_high_since_entry - (atr_val * self.trailing_stop_atr_multiplier)
            # Use the tighter of the two stops (lower price)
            stop_price = min(initial_stop, trailing_stop)
            if current_price <= stop_price:
                self.log.info(f"Stop loss triggered: Price {current_price} below stop {stop_price}")
                return self.order_factory.market(
                    instrument_id=self.instrument_id,
                    order_side=OrderSide.SELL,
                    quantity=self.current_position_size,
                    time_in_force=TimeInForce.GTC,
                )

        return None
    
    def on_order_filled(self, fill: "OrderFilled") -> None:
        """Update position when order is filled."""
        filled_qty = fill.last_qty
        avg_fill_px = fill.last_px
        if fill.order_side == OrderSide.BUY and float(filled_qty.as_decimal()) > 0:
            # Long entry filled
            self.current_position_size += filled_qty
            # Store the average fill price as entry price for stop loss calculation
            if avg_fill_px is not None and float(avg_fill_px) > 0:
                self.entry_price = Decimal(str(float(avg_fill_px)))
            else:
                self.entry_price = None
            self.log.info(f"Long position opened: {filled_qty} at {self.entry_price}")
            # Record entry bar and initialize trailing stop high
            self.entry_bar = self.bars_processed
            if self.entry_price is not None:
                self.highest_high_since_entry = float(self.entry_price)
        elif fill.order_side == OrderSide.SELL and float(filled_qty.as_decimal()) > 0:
            # Exit filled
            self.current_position_size -= filled_qty
            self.log.info(f"Long position closed: {filled_qty}")
            
            # Reset cooldown after exit
            if self.cooldown_timer < 0:  # Only reset if not already set by entry
                self.cooldown_timer = self.cooldown_bars
                self.log.debug("Cooldown set after exit.")
            
            # Clear entry-related state after exit
            self.entry_price = None
            self.entry_bar = None
            self.highest_high_since_entry = None

    def on_stop(self) -> None:
        """Called when strategy stops."""
        self.log.info("Kraken BTC/USD strategy stopped.")

    def get_account_value(self) -> Optional[Decimal]:
        """Get total account value in USD."""
        try:
            account = self.portfolio.account(self.instrument_id.venue)
            if account is not None:
                return account.balance_total(USD).as_decimal()
        except Exception:
            pass
        # Fallback: starting balance if portfolio not accessible
        return Decimal(str(STARTING_BALANCE_USD))



def _calculate_position_size(
    account_value: Decimal,
    risk_percent: float,
    entry_price: Decimal,
    stop_price: Decimal,
    notional_limit: float,
    min_size: Decimal,
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
    if position_size_btc < min_size:
        return None
    # Round to 8 decimal places
    qty_int = int(position_size_btc * 100_000_000)
    position_size_btc = Decimal(qty_int) / Decimal("100000000")
    return Quantity.from_str(str(position_size_btc))


def create_strategy() -> KrakenBTCUSDResearchStrategy:
    """Create and return a strategy instance."""
    spec = BarSpecification(TIMEFRAME_BARS, BarAggregation.MINUTE, PriceType.LAST)
    config = KrakenBTCUSDResearchConfig(
        instrument_id=InstrumentId.from_str(INSTRUMENT_ID),
        bar_type=BarType(InstrumentId.from_str(INSTRUMENT_ID), spec),
        trade_size=Quantity.from_str(str(MIN_POSITION_SIZE_BTC)),
        atr_period=ATR_PERIOD,
        fast_ema_period=FAST_EMA_PERIODS,
        slow_ema_period=SLOW_EMA_PERIODS,
        donchian_window=DONCHIAN_WINDOW,
        initial_stop_atr_multiplier=2.0,
        trailing_stop_atr_multiplier=1.5,
        risk_percent=RISK_PER_TRADE,
        max_notional_pct=MAX_NOTIONAL_EXPOSURE_PCT,
        cooldown_bars=COOLDOWN_BARS,
        maker_fee=MAKER_FEE,
        taker_fee=TAKER_FEE,
    )
    return KrakenBTCUSDResearchStrategy(config=config)