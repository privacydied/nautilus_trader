#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  V2 Selective Breakout Research Strategy
#
#  Hypothesis: Fewer, higher-quality breakouts via stricter filtering can overcome fee drag.
#
#  Key changes from V1:
#  - 15m bars (was 5m) → fewer signals by construction
#  - EMA(50) > EMA(200) regime filter (V1: EMA 20 > EMA 100)
#  - Donchian(80) (V1: Donchian 55)
#  - Volume confirmation: volume > rolling median × 1.25
#  - Cooldown: 48 bars = 12h (V1: 12 bars = 1h)
#  - Market entry (for now, same execution as V1 — the hypothesis is about selectivity, not entry type)
# -------------------------------------------------------------------------------------------------

from decimal import Decimal
from typing import Optional, Deque
from collections import deque
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PriceType, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.model.orders import Order
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.indicators import DonchianChannel as Donchian
from nautilus_trader.indicators import AverageTrueRange as ATR
from nautilus_trader.indicators import ExponentialMovingAverage as EMA

from examples.strategies.kraken_btcusd_research.config_v2 import (
    FAST_EMA_PERIODS as FAST_EMA,
    SLOW_EMA_PERIODS as SLOW_EMA,
    DONCHIAN_WINDOW,
    ATR_PERIOD,
    VOLUME_MEDIAN_PERIOD,
    INITIAL_STOP_ATR_MULTIPLIER,
    TRAILING_STOP_ATR_MULTIPLIER,
    MAX_NOTIONAL_EXPOSURE_PCT,
    COOLDOWN_BARS,
    MAKER_FEE,
    TAKER_FEE,
    TIMEFRAME_BARS,
    MIN_POSITION_SIZE_BTC,
    STARTING_BALANCE_USD,
)


class KrakenBTCUSDV2Config(StrategyConfig):
    instrument_id: InstrumentId
    bar_type: BarType
    atr_period: int
    fast_ema_period: int
    slow_ema_period: int
    donchian_window: int
    volume_median_period: int
    initial_stop_atr_multiplier: float
    trailing_stop_atr_multiplier: float
    risk_percent: float
    max_notional_pct: float
    cooldown_bars: int
    maker_fee: float
    taker_fee: float


class KrakenBTCUSDV2Strategy(Strategy):
    """
    V2 selective breakout — same market execution as V1, but far fewer signals.
    """

    def __init__(self, config: KrakenBTCUSDV2Config):
        super().__init__(config)
        self.instrument_id = config.instrument_id
        self.bar_type = config.bar_type
        self.atr_period = config.atr_period
        self.fast_ema_period = config.fast_ema_period
        self.slow_ema_period = config.slow_ema_period
        self.donchian_window = config.donchian_window
        self.volume_median_period = config.volume_median_period
        self.initial_stop_atr_multiplier = config.initial_stop_atr_multiplier
        self.trailing_stop_atr_multiplier = config.trailing_stop_atr_multiplier
        self.risk_percent = config.risk_percent
        self.max_notional_pct = config.max_notional_pct
        self.cooldown_bars = config.cooldown_bars
        self.maker_fee = config.maker_fee
        self.taker_fee = config.taker_fee

        # Indicators
        self.fast_ema: Optional[EMA] = None
        self.slow_ema: Optional[EMA] = None
        self.donchian: Optional[Donchian] = None
        self.atr: Optional[ATR] = None
        self.volume_history: Deque[float] = deque()

        # Position tracking
        self._pos_size: Quantity = Quantity(Decimal("0"), 8)
        self._entry_price: Optional[float] = None
        self._entry_bar: Optional[int] = None
        self._highest_since_entry: Optional[float] = None
        self._cooldown: int = -1
        self._bars: int = 0

        self._init_indicators()

    def _init_indicators(self):
        self.fast_ema = EMA(period=self.fast_ema_period)
        self.slow_ema = EMA(period=self.slow_ema_period)
        self.donchian = Donchian(period=self.donchian_window)
        self.atr = ATR(period=self.atr_period)

    def _update_indicators(self, bar: Bar):
        h = float(bar.high) if bar.high else 0.0
        l = float(bar.low) if bar.low else 0.0
        c = float(bar.close)
        v = float(bar.volume) if bar.volume else 0.0
        self.fast_ema.update_raw(c)
        self.slow_ema.update_raw(c)
        self.donchian.update_raw(h, l)
        self.atr.update_raw(h, l, c)
        self.volume_history.append(v)
        while len(self.volume_history) > self.volume_median_period:
            self.volume_history.popleft()

    def _warmed_up(self) -> bool:
        return (self.fast_ema.initialized and self.slow_ema.initialized
                and self.donchian.initialized and self.atr.initialized
                and len(self.volume_history) >= self.volume_median_period)

    def _vol_median(self) -> Optional[float]:
        if len(self.volume_history) < self.volume_median_period:
            return None
        v = sorted(self.volume_history)
        m = len(v) // 2
        return (v[m - 1] + v[m]) / 2 if len(v) % 2 == 0 else float(v[m])

    def on_start(self):
        self.subscribe_bars(self.bar_type)
        self.log.info(f"V2 Kraken BTC/USD started. {self.instrument_id}")

    def on_bar(self, bar: Bar):
        self._bars += 1
        if not self._warmed_up():
            self._update_indicators(bar)
            return

        # Exits first
        price = float(bar.close)
        exit_ord = self._exit_check(price, bar)
        if exit_ord is not None:
            self.submit_order(exit_ord)
            return

        # Entry
        if self._cooldown < 0 and self._pos_size.as_decimal() <= 0:
            self._entry_check(price, bar)

        self._update_indicators(bar)

        if self._cooldown >= 0:
            self._cooldown -= 1

        if self._pos_size.as_decimal() > 0 and self._highest_since_entry is not None:
            ch = float(bar.high) if bar.high else 0.0
            if ch > self._highest_since_entry:
                self._highest_since_entry = ch

    def _entry_check(self, price: float, bar: Bar):
        # Regime: EMA50 > EMA200
        if self.fast_ema.value <= self.slow_ema.value:
            return

        # Breakout: close > Donchian(80) high
        dc_high = self.donchian.upper
        if price <= dc_high:
            return

        # ATR
        atr = self.atr.value
        if atr is None or atr <= 0:
            return

        # Volume: > median × 1.25
        vm = self._vol_median()
        if vm is None:
            return
        vol = float(bar.volume) if bar.volume else 0.0
        if vol <= vm * 1.25:
            return

        # Position sizing
        acct = self._get_acct()
        if acct is None or acct <= 0:
            return

        stop_dist = atr * self.initial_stop_atr_multiplier
        entry_d = Decimal(str(price))
        stop_d = Decimal(str(price - stop_dist))

        qty = _pos_size(acct, self.risk_percent, entry_d, stop_d,
                        self.max_notional_pct, Decimal("0.001"), self.taker_fee)
        if qty is None or float(qty.as_decimal()) <= 0:
            return

        self._cooldown = self.cooldown_bars
        ord = self.order_factory.market(
            instrument_id=self.instrument_id, order_side=OrderSide.BUY,
            quantity=qty, time_in_force=TimeInForce.GTC)
        self.log.info(f"V2 entry at {price:.2f}, qty={qty}, stop={stop_dist:.2f}")
        self.submit_order(ord)

    def _exit_check(self, price: float, bar: Bar) -> Optional[Order]:
        if self._pos_size.as_decimal() <= 0:
            return None

        # EMA200 break
        if self.slow_ema and price < self.slow_ema.value:
            self.log.info(f"V2 exit: price {price:.2f} < EMA200 {self.slow_ema.value:.2f}")
            return self._sell()

        # ATR stops
        atr = self.atr.value or 0.0
        if self._entry_price and self._highest_since_entry and atr > 0:
            init = self._entry_price - atr * self.initial_stop_atr_multiplier
            trail = self._highest_since_entry - atr * self.trailing_stop_atr_multiplier
            stop = max(init, trail)
            if price <= stop:
                self.log.info(f"V2 stop: {price:.2f} <= {stop:.2f} (init={init:.2f}, trail={trail:.2f})")
                return self._sell()
        return None

    def _sell(self) -> Order:
        return self.order_factory.market(
            instrument_id=self.instrument_id, order_side=OrderSide.SELL,
            quantity=self._pos_size, time_in_force=TimeInForce.GTC)

    def on_order_filled(self, fill):
        fq = fill.last_qty
        if float(fq.as_decimal()) <= 0:
            return
        if fill.order_side == OrderSide.BUY:
            self._pos_size += fq
            px = float(fill.last_px) if fill.last_px else None
            self._entry_price = px
            self._entry_bar = self._bars
            self._highest_since_entry = px or 0.0
            self.log.info(f"V2 open: {fq} at {px}")
        elif fill.order_side == OrderSide.SELL:
            self._pos_size -= fq
            self.log.info(f"V2 close: {fq}")
            if self._cooldown < 0:
                self._cooldown = self.cooldown_bars
            self._entry_price = None
            self._entry_bar = None
            self._highest_since_entry = None

    def on_stop(self):
        self.log.info("V2 stopped.")

    def get_account_value(self):
        return self._get_acct()

    def _get_acct(self) -> Optional[Decimal]:
        try:
            acct = self.portfolio.account(self.instrument_id.venue)
            if acct:
                return acct.balance_total(USD).as_decimal()
        except:
            pass
        return Decimal(str(STARTING_BALANCE_USD))


def _pos_size(acct, risk_pct, entry_px, stop_px, notional_lim, min_sz, fee):
    if acct is None or entry_px <= 0:
        return None
    d = entry_px - stop_px
    if d <= 0:
        return None
    ra = acct * Decimal(str(risk_pct))
    sz = ra / d
    mx = acct * Decimal(str(notional_lim)) / entry_px
    sz = min(sz, mx)
    if sz < min_sz:
        return None
    qi = int(sz * 100_000_000)
    return Quantity(Decimal(qi) / Decimal("100000000"), 8)


def create_strategy_v2() -> KrakenBTCUSDV2Strategy:
    from nautilus_trader.model.data import BarSpecification, BarAggregation
    from nautilus_trader.model.enums import PriceType
    from nautilus_trader.model.identifiers import InstrumentId
    from examples.strategies.kraken_btcusd_research.config_v2 import TIMEFRAME_BARS as V2_TF, MIN_POSITION_SIZE_BTC as V2_MIN
    spec = BarSpecification(V2_TF, BarAggregation.MINUTE, PriceType.LAST)
    iid = InstrumentId.from_str("BTC/USD.KRAKEN")
    cfg = KrakenBTCUSDV2Config(
        instrument_id=iid, bar_type=BarType(iid, spec),
        atr_period=ATR_PERIOD, fast_ema_period=FAST_EMA,
        slow_ema_period=SLOW_EMA, donchian_window=DONCHIAN_WINDOW,
        volume_median_period=VOLUME_MEDIAN_PERIOD,
        initial_stop_atr_multiplier=INITIAL_STOP_ATR_MULTIPLIER,
        trailing_stop_atr_multiplier=TRAILING_STOP_ATR_MULTIPLIER,
        risk_percent=0.0025, max_notional_pct=MAX_NOTIONAL_EXPOSURE_PCT,
        cooldown_bars=COOLDOWN_BARS, maker_fee=MAKER_FEE, taker_fee=TAKER_FEE,
    )
    return KrakenBTCUSDV2Strategy(config=cfg)
