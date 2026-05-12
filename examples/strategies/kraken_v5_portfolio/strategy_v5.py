#!/usr/bin/env python3
"""
V5: Multi-Asset Higher-Timeframe Momentum Portfolio Strategy.

Hypothesis: A low-turnover crypto spot portfolio across multiple liquid assets
captures trend moves that single-asset strategies miss, enough to overcome fees.

Rules:
- Universe: liquid Kraken spot USD pairs
- Signal: price above EMA(200) for trend filter
- Portfolio: hold top 1–3 assets by momentum, rebalance weekly or on signal change
- Exit: trailing ATR stop, or trend loss (close below EMA-200)
- No leverage, no shorts, conservative taker fees first
"""

from collections import deque
from decimal import Decimal
from typing import Optional

from nautilus_trader.model.data import Bar, BarSpecification, BarAggregation, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.trading.config import StrategyConfig

from nautilus_trader.indicators import ExponentialMovingAverage as EMA
from nautilus_trader.indicators import AverageTrueRange as ATR

DEFAULT_UNIVERSE: tuple[str, ...] = (
    "BTC/USD.KRAKEN",
    "ETH/USD.KRAKEN",
    "SOL/USD.KRAKEN",
    "XRP/USD.KRAKEN",
    "ADA/USD.KRAKEN",
    "LINK/USD.KRAKEN",
    "DOGE/USD.KRAKEN",
    "AVAX/USD.KRAKEN",
    "LTC/USD.KRAKEN",
    "BCH/USD.KRAKEN",
)

# Per-instrument size precision (quantity decimals)
# Use centralized definition; kept here for backward compat
from examples.strategies.kraken_v5_portfolio.instrument_details import SIZE_PRECISION as SIZE_PRECISION

class _AssetState:
    """Per-asset indicator and position tracking."""

    def __init__(self, instrument_id: InstrumentId, ema_period: int, atr_period: int):
        self.instrument_id = instrument_id
        self.ema_trend = EMA(period=ema_period)
        self.atr = ATR(period=atr_period)
        self.price_history: deque[float] = deque()

        # Position tracking
        self.is_held = False
        self.position_qty: float = 0.0
        self.entry_price: Optional[float] = None
        self.highest_since_entry: float = 0.0
        self.bars_since_entry: int = 0

        # Per-instrument bar counter for rebalance logic
        self.bar_count: int = 0

    @property
    def current_price(self) -> float:
        return self.price_history[-1] if self.price_history else 0.0

    @property
    def momentum(self) -> float:
        """Return absolute momentum: price change over lookback period."""
        lookback = 200  # roughly 200 bars of 4h = ~33 days
        if len(self.price_history) > lookback:
            return self.current_price - self.price_history[-(lookback + 1)]
        return 0.0

    @property
    def in_uptrend(self) -> bool:
        if not self.ema_trend.initialized:
            return False
        return self.current_price > self.ema_trend.value

    def atr_value(self) -> float:
        if self.atr.initialized:
            return float(self.atr.value)
        return 0.0


class KrakenV5PortfolioConfig(StrategyConfig):
    """V5 multi-asset higher-timeframe momentum portfolio config."""
    universe: tuple[str, ...] = DEFAULT_UNIVERSE
    bar_type: Optional[BarType] = None

    ema_trend_period: int = 200
    atr_period: int = 28
    max_positions: int = 3
    max_notional_pct: float = 0.50
    risk_percent: float = 0.01
    taker_fee: float = 0.0040
    min_position_size_usd: float = 25.0
    trailing_stop_atr_mult: float = 3.0
    rebalance_bars: int = 6  # ~24h for 4h bars


class KrakenV5PortfolioStrategy(Strategy):
    """Multi-asset momentum portfolio — long-only, spot, no leverage."""

    def __init__(self, config: KrakenV5PortfolioConfig):
        super().__init__(config)
        self.universe: list[str] = list(config.universe)
        self.assets: dict[str, _AssetState] = {}
        self.bar_spec = config.bar_type.spec if config.bar_type else BarSpecification(
            4, BarAggregation.HOUR, PriceType.LAST
        )
        self.total_bars = 0
        self.bars_per_instrument: list[int] = []  # to count bars per asset
        self.instruments: list[InstrumentId] = []

    # -- Nautilus lifecycle --

    def on_start(self) -> None:
        for id_str in self.universe:
            iid = InstrumentId.from_str(id_str)
            bt = BarType(iid, self.bar_spec)
            self.subscribe_bars(bt)
            self.assets[id_str] = _AssetState(iid, 200, 28)
            self.instruments.append(iid)
            self.bars_per_instrument.append(0)
        self.log.info(f"V5 Portfolio started: {len(self.universe)} assets, {self.bar_spec}")

    def on_bar(self, bar: Bar) -> None:
        iid = bar.bar_type.instrument_id
        state = self._get_state(iid)
        if state is None:
            return

        # Count bars per instrument
        for i, inst_id in enumerate(self.instruments):
            if inst_id == iid:
                self.bars_per_instrument[i] += 1
                break

        self.total_bars += 1

        # Update state
        close = float(bar.close)
        high = float(bar.high)
        low = float(bar.low)
        state.ema_trend.update_raw(close)
        state.atr.update_raw(high, low, close)
        state.price_history.append(close)

        if state.is_held:
            state.bars_since_entry += 1
            if high > state.highest_since_entry:
                state.highest_since_entry = high

        # Per-asset exit checks
        if state.is_held:
            reason = self._check_exit(state)
            if reason:
                id_str = self._state_to_id(state)
                self.log.info(f"EXIT {id_str}: {reason}")
                self._sell_position(id_str, state)
                return  # don't rebalance same bar as exit

        # Rebalance check — uses avg bars across all instruments
        if self._should_rebalance():
            self._rebalance()

    def on_order_filled(self, fill) -> None:
        state = self._get_state(fill.instrument_id)
        if state is None:
            return

        qty = float(fill.last_qty.as_decimal())
        price = float(fill.last_px)

        if fill.order_side == OrderSide.BUY and qty > 0:
            state.is_held = True
            state.position_qty += qty
            state.entry_price = price
            state.highest_since_entry = price
            self.log.info(f"BUY filled: {fill.instrument_id} qty={qty:.8f} px={price:.2f}")
        elif fill.order_side == OrderSide.SELL and qty > 0:
            state.position_qty -= qty
            if state.position_qty <= 1e-12:
                state.is_held = False
                state.position_qty = 0.0
                state.entry_price = None
                self.log.info(f"SELL filled: position closed {fill.instrument_id}")

    def on_stop(self) -> None:
        self.log.info("V5 Portfolio stopped")

    # -- Core logic --

    def _state_to_id(self, state: _AssetState) -> Optional[str]:
        for k, v in self.assets.items():
            if v is state:
                return k
        return None

    def _get_state(self, iid: InstrumentId) -> Optional[_AssetState]:
        for state in self.assets.values():
            if state.instrument_id == iid:
                return state
        return None

    def _check_exit(self, state: _AssetState) -> Optional[str]:
        close = state.current_price
        atr = state.atr_value()
        if atr > 0 and state.highest_since_entry > 0:
            stop = state.highest_since_entry - atr * self.config.trailing_stop_atr_mult
            if close <= stop:
                return f"trail"
        if state.ema_trend.initialized and close < state.ema_trend.value:
            return "trend"
        return None

    def _sell_position(self, id_str: str, state: _AssetState) -> None:
        if state.position_qty <= 1e-12:
            state.is_held = False
            return
        sp = SIZE_PRECISION.get(id_str, 8)
        order = self.order_factory.market(
            instrument_id=InstrumentId.from_str(id_str),
            order_side=OrderSide.SELL,
            quantity=Quantity(state.position_qty, sp),
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)

    def _should_rebalance(self) -> bool:
        """Rebalance when avg bars per instrument crosses the interval."""
        if not self.bars_per_instrument:
            return False
        avg = sum(self.bars_per_instrument) / len(self.bars_per_instrument)
        return avg > 0 and int(avg) % self.config.rebalance_bars == 0

    def _rebalance(self) -> None:
        """Select top assets by momentum, enter/exit accordingly."""
        # 1. Rank assets in uptrend by momentum
        scored = []
        for id_str, state in self.assets.items():
            if state.in_uptrend and state.ema_trend.initialized:
                mom = state.momentum
                scored.append((id_str, mom, state))
        scored.sort(key=lambda x: x[1], reverse=True)
        selected = {s[0] for s in scored[: self.config.max_positions]}

        # 2. Exit dropped assets
        for id_str, state in self.assets.items():
            if state.is_held and id_str not in selected:
                self.log.info(f"Rebalance exit: {id_str} (rank dropped)")
                self._sell_position(id_str, state)

        # 3. Enter new assets
        account = self._account_value()
        if account is None or account <= 0:
            return

        current_usd = self._notional_usd()
        max_usd = account * Decimal(str(self.config.max_notional_pct))

        for id_str, mom, state in scored:
            if id_str not in selected or state.is_held:
                continue
            alloc = account * Decimal(str(self.config.risk_percent)) * Decimal(str(5))
            if current_usd + alloc > max_usd:
                break
            price = state.current_price
            if price <= 0:
                continue
            qty = float(alloc) / price
            sp = SIZE_PRECISION.get(id_str, 8)
            if qty < 10 ** -sp:
                continue
            order = self.order_factory.market(
                instrument_id=InstrumentId.from_str(id_str),
                order_side=OrderSide.BUY,
                quantity=Quantity(qty, sp),
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)
            current_usd += alloc
            self.log.info(f"Rebalance enter: {id_str} qty={qty:.8f} px={price:.2f}")

    def _account_value(self) -> Optional[Decimal]:
        try:
            from nautilus_trader.model.currencies import USD
            account = self.portfolio.account(self.instruments[0].venue if self.instruments else "KRAKEN")
            if account:
                return account.balance_total(USD).as_decimal()
        except Exception:
            pass
        return None

    def _notional_usd(self) -> Optional[Decimal]:
        total = Decimal(0)
        for state in self.assets.values():
            if state.is_held and state.position_qty > 0:
                price = state.current_price
                if price > 0:
                    total += Decimal(str(price)) * Decimal(str(state.position_qty))
        return total


USD_STR = "USD"
