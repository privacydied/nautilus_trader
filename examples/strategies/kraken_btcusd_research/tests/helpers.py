#!/usr/bin/env python3
"""
Shared test helpers for Kraken BTC/USD research tests.

Provides a deterministic synthetic backtest helper that runs through
the proper BacktestEngine lifecycle. No direct on_bar() calls anywhere.
"""

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar, BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import AccountType, OmsType, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity

from examples.strategies.kraken_btcusd_research.config import (
    INSTRUMENT_ID,
    STARTING_BALANCE_USD,
    TIMEFRAME_BARS,
    MAKER_FEE,
    TAKER_FEE,
    ATR_PERIOD,
    FAST_EMA_PERIODS,
    SLOW_EMA_PERIODS,
    DONCHIAN_WINDOW,
    RISK_PER_TRADE,
    MAX_NOTIONAL_EXPOSURE_PCT,
    COOLDOWN_BARS,
    MIN_POSITION_SIZE_BTC,
)
from examples.strategies.kraken_btcusd_research.strategy import (
    KrakenBTCUSDResearchStrategy,
    KrakenBTCUSDResearchConfig,
)


def make_synthetic_bars() -> tuple[BarType, InstrumentId, list[Bar]]:
    """
    Build ~200 deterministic 5-min bars with a clear breakout profile:

    Bars 0–110:   flat consolidation at 50 000 (EMA/ATR/Donchian warm-up)
    Bars 111–136: still flat (Donchian(55) upper stabilises at 50 005)
    Bar 137:      small pre-breakout dip
    Bar 138:      BREAKOUT candle — close 50 100 >> Donchian upper 50 005
    Bars 139–151: rally continuation 50 100 → 50 750
    Bars 152–191: sharp drop 50 765 → 48 415 (triggers trailing stop / EMA exit)
    """
    iid = InstrumentId.from_str(INSTRUMENT_ID)
    spec = BarSpecification(TIMEFRAME_BARS, BarAggregation.MINUTE, PriceType.LAST)
    bt = BarType(iid, spec)

    base_ts = 1_704_067_200_000_000_000  # 2024-01-01 00:00 UTC ns
    interval = 300_000_000_000            # 5 minutes
    bars: list[Bar] = []

    def _add(i: int, o: float, h: float, l: float, c: float,
             vol: float = 100.0) -> None:
        ts = base_ts + i * interval
        bars.append(Bar(
            bar_type=bt,
            open=Price(o, 2), high=Price(h, 2),
            low=Price(l, 2), close=Price(c, 2),
            volume=Quantity(vol, 8),
            ts_event=ts, ts_init=ts,
        ))

    # Phase 1: flat consolidation — all EMAs converge to 50000
    for i in range(110):
        _add(i, 50000.0, 50005.0, 49995.0, 50000.0)

    # Phase 2: continue flat (Donchian upper stays 50005)
    for i in range(110, 137):
        _add(i, 50000.0, 50005.0, 49995.0, 50000.0)

    # Phase 3: small dip before breakout
    _add(137, 50000.0, 50003.0, 49980.0, 49985.0)

    # Phase 4: THE BREAKOUT — close 50100 > Donchian upper 50005
    # EMA20 ≈ EMA100 ≈ 50000 (flat warm-up), so EMA20 >= EMA100 ✓
    # Price 49985 → 50100 jump makes fast EMA cross above slow EMA
    _add(138, 50000.0, 50110.0, 49995.0, 50100.0, vol=500.0)

    # Phase 5: rally continuation
    for k in range(13):
        i = 139 + k
        p = 50100.0 + k * 50.0
        _add(i, p, p + 20.0, p - 10.0, p + 15.0, vol=200.0)

    # Phase 6: sharp drop — triggers trailing stop or slow-EMA exit
    for k in range(40):
        i = 152 + k
        p = 50765.0 - k * 60.0
        _add(i, p, p + 10.0, p - 20.0, p - 10.0, vol=200.0)

    return bt, iid, bars


def make_engine(
    trader_id: str = "SYNTH-TEST-001",
    starting_balance: float = 10000.0,
) -> BacktestEngine:
    """Build a BacktestEngine with Kraken spot venue and BTC/USD instrument."""
    iid = InstrumentId.from_str(INSTRUMENT_ID)
    instrument = CurrencyPair(
        instrument_id=iid,
        raw_symbol=Symbol("BTCUSD"),
        base_currency=BTC,
        quote_currency=USD,
        price_precision=2,
        size_precision=8,
        price_increment=Price(0.01, 2),
        size_increment=Quantity(0.00000001, 8),
        multiplier=Quantity(1, 0),
        maker_fee=Decimal("0.0025"),
        taker_fee=Decimal("0.004"),
        margin_init=Decimal(0),
        margin_maint=Decimal(0),
        ts_event=0,
        ts_init=0,
    )
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=trader_id,
            logging=LoggingConfig(log_level="ERROR", bypass_logging=True),
        )
    )
    engine.add_venue(
        venue=iid.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=[Money(starting_balance, USD)],
        base_currency=None,
    )
    engine.add_instrument(instrument)
    return engine


def make_strategy_config(bt: BarType, iid: InstrumentId) -> KrakenBTCUSDResearchConfig:
    """Create standard strategy config for tests."""
    return KrakenBTCUSDResearchConfig(
        instrument_id=iid,
        bar_type=bt,
        trade_size=Quantity(0.001, 8),
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


def run_synthetic_btcusd_backtest(
    trader_id: str = "SYNTH-TEST-001",
) -> dict:
    """
    Run a complete synthetic backtest through the BacktestEngine lifecycle.

    Returns dict:
        engine  — BacktestEngine (caller must dispose)
        result  — BacktestResult from engine.get_result()
        bt      — BarType used
        iid     — InstrumentId (BTC/USD.KRAKEN)
        bars    — list[Bar] loaded
    """
    bt, iid, bars = make_synthetic_bars()
    cfg = make_strategy_config(bt, iid)
    engine = make_engine(trader_id=trader_id)
    engine.add_data(bars)
    engine.add_strategy(KrakenBTCUSDResearchStrategy(config=cfg))
    engine.run()
    result = engine.get_result()
    return {"engine": engine, "result": result, "bt": bt, "iid": iid, "bars": bars}
