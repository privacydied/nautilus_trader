#!/usr/bin/env python3
"""
Shared test helpers for V3 mean reversion tests.

Provides a deterministic synthetic bar factory and engine-backed backtest
helper specifically tuned for mean-reversion signal profiles.
"""

from decimal import Decimal
from pathlib import Path

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar, BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import AccountType, OmsType, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from examples.strategies.kraken_btcusd_research.config_v3 import (
    INSTRUMENT_ID,
    STARTING_BALANCE_USD,
    TIMEFRAME_BARS,
    MAKER_FEE,
    TAKER_FEE,
    ATR_PERIOD,
    MEAN_WINDOW,
    STD_WINDOW,
    RSI_PERIOD,
    RANGE_WINDOW,
    ZSCORE_ENTRY,
    ENTRY_OFFSET_ATR,
    MIN_RANGE_FEE_MULTIPLE,
)
from examples.strategies.kraken_btcusd_research.strategy_v3 import (
    KrakenBTCUSDMeanReversionStrategy,
    KrakenBTCUSDMeanReversionConfig,
)


def make_v3_synthetic_bars() -> tuple[BarType, InstrumentId, list[Bar]]:
    """
    Build ~300 deterministic 15m bars with a clear mean-reversion profile:

    Bars 0–99:    flat at 50,000 (warm-up for 80-bar mean/std + RSI + ATR)
    Bars 100–119: gradual dip to 48,500 (z-score drops towards -2.0)
    Bars 120:     oversold candle -- close well below mean, RSI < 30
    Bars 121–130: recovery section (price trades back toward mean)
    Bars 131–200: continuation up to 50,500
    Bars 201–250: return to range at 50,000
    Bars 251–299: flat consolidation

    The dip in bars 100–120 should trigger:
    - z-score <= -2.0 at bar ~120
    - RSI oversold
    - Range large enough for fee filter

    Then a pending limit should be created that fills during recovery (bars 121+).
    """
    iid = InstrumentId.from_str(INSTRUMENT_ID)
    spec = BarSpecification(TIMEFRAME_BARS, BarAggregation.MINUTE, PriceType.LAST)
    bt = BarType(iid, spec)

    base_ts = 1_704_067_200_000_000_000  # 2024-01-01 00:00 UTC (ns)
    interval = 900_000_000_000             # 15 minutes (ns)
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

    # Phase 1: flat warmup (bars 0–99)
    # EMA/RSI/ATR converge to 50000 baseline
    for i in range(100):
        _add(i, 50000.0, 50010.0, 49990.0, 50000.0)

    # Phase 2: gradual dip (bars 100–119)
    # Price drops from 50000 to ~48500
    for k in range(20):
        i = 100 + k
        price = 50000.0 - k * 75.0  # drops 1500 over 20 bars
        _add(i, price, price + 5.0, price - 10.0, price - 5.0)

    # Phase 3: THE OVERSOLD CANDLE (bar 120)
    # Deep dip: close = 48500, well below mean
    # This should trigger zscore <= -2 and RSI < 30
    _add(120, 48510.0, 48520.0, 48400.0, 48500.0, vol=500.0)

    # Phase 4: Recovery / bounce (bars 121–130)
    # Price recovers, crosses mean (triggers exit later)
    # Key: bar 121 has low <= pending limit price so it can fill
    recovery_prices = []
    for k in range(10):
        i = 121 + k
        price = 48500.0 + k * 80.0  # recovers 800 over 10 bars
        recovery_prices.append(price)
        _add(i, price, price + 10.0, price - 10.0, price + 5.0, vol=300.0)

    # Phase 5: continuation above mean (bars 131–200)
    # Price goes to ~50500, should trigger mean-reversion exit
    for k in range(70):
        i = 131 + k
        if k < 30:
            price = 49300.0 + k * 40.0  # going up
        elif k < 50:
            price = 50500.0 - (k - 30) * 25.0  # slight pullback
        else:
            price = 50000.0 + (k - 50) * 5.0  # flat around 50000
        _add(i, price, price + 10.0, price - 10.0, price, vol=150.0)

    # Phase 6: flat consolidation (bars 201–299)
    for i in range(201, 300):
        _add(i, 50000.0, 50010.0, 49990.0, 50000.0, vol=80.0)

    return bt, iid, bars


def make_v3_synthetic_bars_no_fill() -> tuple[BarType, InstrumentId, list[Bar]]:
    """
    Build synthetic bars where a pending limit is created but never fills
    (price runs away from limit, never trades through it).

    Bars 0–99:  flat warmup at 50000
    Bars 100–115: gradual dip creating mean-reversion signal
    Bar 116:    oversold candle (triggers pending limit)
    Bars 117–150: price goes UP (away from limit), never trades through it
                  -> pending limit should expire
    """
    iid = InstrumentId.from_str(INSTRUMENT_ID)
    spec = BarSpecification(TIMEFRAME_BARS, BarAggregation.MINUTE, PriceType.LAST)
    bt = BarType(iid, spec)

    base_ts = 1_704_067_200_000_000_000
    interval = 900_000_000_000
    bars: list[Bar] = []

    def _add(i: int, o: float, h: float, l: float, c: float) -> None:
        ts = base_ts + i * interval
        # Clamp to valid bar: low <= min(open, close), high >= max(open, close)
        lo = min(l, o, c)
        hi = max(h, o, c)
        bars.append(Bar(
            bar_type=bt,
            open=Price(o, 2), high=Price(hi, 2),
            low=Price(lo, 2), close=Price(c, 2),
            volume=Quantity(100.0, 8),
            ts_event=ts, ts_init=ts,
        ))

    # Warmup
    for i in range(100):
        _add(i, 50000.0, 50010.0, 49990.0, 50000.0)

    # Dip
    for k in range(16):
        i = 100 + k
        price = 50000.0 - k * 80.0
        _add(i, price, price + 5.0, price - 10.0, price - 5.0)

    # Oversold
    _add(116, 48710.0, 48720.0, 48600.0, 48700.0)

    # Price runs away UP (no fill)
    for k in range(34):
        i = 117 + k
        price = 48700.0 + k * 50.0
        _add(i, price, price + 20.0, price - 5.0, price + 15.0)


    return bt, iid, bars


def make_engine(
    trader_id: str = "V3-SYNTH-TEST-001",
    starting_balance: float = 10000.0,
) -> BacktestEngine:
    """Build a BacktestEngine with Kraken spot venue and BTC/USD instrument."""
    from nautilus_trader.model.identifiers import Symbol
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
        taker_fee=Decimal("0.0040"),
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


def make_v3_strategy_config(bt: BarType, iid: InstrumentId) -> KrakenBTCUSDMeanReversionConfig:
    """Create V3 mean reversion strategy config for tests."""
    return KrakenBTCUSDMeanReversionConfig(
        instrument_id=iid,
        bar_type=bt,
        mean_window=MEAN_WINDOW,
        std_window=STD_WINDOW,
        rsi_period=RSI_PERIOD,
        rsi_oversold=30.0,
        zscore_entry=ZSCORE_ENTRY,
        zscore_exit=-0.2,
        atr_period=ATR_PERIOD,
        range_window=RANGE_WINDOW,
        min_range_fee_multiple=MIN_RANGE_FEE_MULTIPLE,
        entry_offset_atr=ENTRY_OFFSET_ATR,
        maker_order_expiry_bars=6,
        cooldown_bars=12,
        initial_stop_atr_multiplier=2.0,
        trailing_stop_atr_multiplier=1.5,
        time_stop_bars=32,
        maker_fee=MAKER_FEE,
        taker_fee=TAKER_FEE,
        risk_percent=0.0025,
        max_notional_pct=0.30,
        min_position_size_btc=0.001,
    )


def run_v3_synthetic_backtest(
    trader_id: str = "V3-SYNTH-TEST-001",
    bars: list[Bar] | None = None,
) -> dict:
    """
    Run a complete synthetic V3 backtest through the BacktestEngine lifecycle.

    Returns dict:
        engine  -- BacktestEngine (caller must dispose)
        result  -- BacktestResult
        bt      -- BarType
        iid     -- InstrumentId
        bars    -- list[Bar] loaded
    """
    if bars is None:
        bt, iid, bars = make_v3_synthetic_bars()
    else:
        bt = bars[0].bar_type
        iid = bt.instrument_id

    cfg = make_v3_strategy_config(bt, iid)
    engine = make_engine(trader_id=trader_id)
    engine.add_data(bars)
    engine.add_strategy(KrakenBTCUSDMeanReversionStrategy(config=cfg))
    engine.run()
    result = engine.get_result()
    return {"engine": engine, "result": result, "bt": bt, "iid": iid, "bars": bars}
