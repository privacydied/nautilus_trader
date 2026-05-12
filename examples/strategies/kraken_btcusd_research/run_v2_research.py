#!/usr/bin/env python3
"""Run V2 backtests across all 4 windows."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from decimal import Decimal
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from examples.strategies.kraken_btcusd_research.strategy_v2 import (
    KrakenBTCUSDV2Strategy, KrakenBTCUSDV2Config
)
from examples.strategies.kraken_btcusd_research.config_v2 import (
    FAST_EMA_PERIODS, SLOW_EMA_PERIODS, DONCHIAN_WINDOW, ATR_PERIOD,
    VOLUME_MEDIAN_PERIOD, INITIAL_STOP_ATR_MULTIPLIER, TRAILING_STOP_ATR_MULTIPLIER,
    MAX_NOTIONAL_EXPOSURE_PCT, COOLDOWN_BARS, MAKER_FEE, TAKER_FEE,
)
from examples.strategies.kraken_btcusd_research.reports import generate_reports


def run_v2(catalog_path, start, end, name):
    catalog = ParquetDataCatalog(path=catalog_path)
    bars = catalog.bars(instrument_ids=["BTC/USD.KRAKEN"])
    bar_type = bars[0].bar_type
    iid = bar_type.instrument_id

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=f"V2-{name}",
        logging=LoggingConfig(log_level="ERROR", bypass_logging=True)))
    engine.add_venue(
        venue=iid.venue, oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=[Money(10000, USD)], base_currency=None
    )
    engine.add_instrument(CurrencyPair(
        instrument_id=iid, raw_symbol=Symbol("BTCUSD"),
        base_currency=BTC, quote_currency=USD,
        price_precision=2, size_precision=8,
        price_increment=Price(0.01, 2), size_increment=Quantity(0.00000001, 8),
        multiplier=Quantity(1, 0),
        maker_fee=Decimal("0.0025"), taker_fee=Decimal("0.004"),
        margin_init=Decimal(0), margin_maint=Decimal(0), ts_event=0, ts_init=0
    ))
    engine.add_data(bars)

    cfg = KrakenBTCUSDV2Config(
        instrument_id=iid, bar_type=bar_type,
        atr_period=ATR_PERIOD,
        fast_ema_period=FAST_EMA_PERIODS,
        slow_ema_period=SLOW_EMA_PERIODS,
        donchian_window=DONCHIAN_WINDOW,
        volume_median_period=VOLUME_MEDIAN_PERIOD,
        initial_stop_atr_multiplier=INITIAL_STOP_ATR_MULTIPLIER,
        trailing_stop_atr_multiplier=TRAILING_STOP_ATR_MULTIPLIER,
        risk_percent=0.0025,
        max_notional_pct=MAX_NOTIONAL_EXPOSURE_PCT,
        cooldown_bars=COOLDOWN_BARS,
        maker_fee=MAKER_FEE,
        taker_fee=TAKER_FEE,
    )
    engine.add_strategy(KrakenBTCUSDV2Strategy(config=cfg))
    engine.run(start=start, end=end)

    result = engine.get_result()
    tmpdir = Path(f"/tmp/v2_{name}")
    tmpdir.mkdir(exist_ok=True)
    summary = generate_reports(result, tmpdir, engine=engine)

    pos_df = engine.trader.generate_positions_report()

    engine.dispose()
    return summary, pos_df


windows = [
    ("2024h1", "2024-01-01", "2024-06-01", "data/kraken/BTCUSD_5m_2024h1.csv", "data/catalog/kraken_btcusd_2024h1"),
    ("2024h2", "2024-06-01", "2025-01-01", "data/kraken/BTCUSD_5m_2024h2.csv", "data/catalog/kraken_btcusd_2024h2"),
    ("2025",   "2025-01-01", "2026-01-01", "data/kraken/BTCUSD_5m_2025.csv",   "data/catalog/kraken_btcusd_2025"),
    ("2026",   "2026-01-01", "2026-05-12", "data/kraken/BTCUSD_5m_2026.csv",   "data/catalog/kraken_btcusd_2026"),
]

print("V2 on 5m Binance data (same data as V1, new strategy)")
print("=" * 80)

results = []
for name, start, end, csv_f, cat_f in windows:
    summary, pos_df = run_v2(cat_f, start, end, name)
    trades = summary.get("total_trades", 0)
    wr = summary.get("win_rate_percent", 0)
    gross = summary.get("total_pnl", 0) or 0
    fees = summary.get("total_fees", 0) or 0
    net = gross - fees
    final_eq = summary.get("final_equity", 10000)
    results.append((name, trades, wr, gross, fees, net, final_eq))
    print(f"\n{name}:")
    print(f"  Trades: {trades}, Win%: {wr:.1f}")
    print(f"  Gross PnL: {gross:.2f}, Fees: {fees:.2f}, Net: {net:.2f}")
    print(f"  Final equity: {final_eq}")
    if len(pos_df) > 0:
        print(f"  Positions: {len(pos_df)}")
        sample = pos_df.head(3)
        print(sample[["avg_px_open", "avg_px_close", "realized_pnl", "commissions"]].to_string())

print("\n" + "=" * 80)
print("V2 SUMMARY vs V1 BASELINE")
print("=" * 80)
h = f"{'Window':<10} {'V1-T':>5} {'V1-W%':>5} {'V1-G':>8} {'V1-N':>8} | {'V2-T':>5} {'V2-W%':>5} {'V2-G':>8} {'V2-N':>8}"
print(h)
print("-" * len(h))

v1_baseline = [
    ("2024h1", 279, 11.5, -4538.61, -8626.49),
    ("2024h2", 397, 11.1, -6132.99, -11912.23),
    ("2025",   669,  8.5, -8033.83, -15739.30),
    ("2026",   223,  8.5, -4274.38, -8362.26),
]

for i, (w, t1, w1, g1, n1) in enumerate(v1_baseline):
    r = results[i]
    v2_str = f"{w:<10} {t1:>5} {w1:>4.1f} {g1:>8.0f} {n1:>8.0f} | {r[1]:>5} {r[2]:>4.0f} {r[3]:>8.0f} {r[5]:>8.0f}"
    print(v2_str)

v1_total = ("TOTAL", 1568, 9.8, -22939.81, -44640.28)
v2_total = ("",
    sum(r[1] for r in results),
    0.0,
    sum(r[3] for r in results),
    sum(r[5] for r in results),
)
print(f"{v1_total[0]:<10} {v1_total[1]:>5} {v1_total[2]:>4.0f} {v1_total[3]:>8.0f} {v1_total[4]:>8.0f} | {v2_total[1]:>5} {v2_total[2]:>4.0f} {v2_total[3]:>8.0f} {v2_total[4]:>8.0f}")
