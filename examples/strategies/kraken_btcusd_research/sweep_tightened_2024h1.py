#!/usr/bin/env python3
"""Quick tightened V3 parameter sweep on 2024h1 only."""
import sys
from pathlib import Path
from decimal import Decimal
from copy import deepcopy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import AccountType, OmsType, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity, Money
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from examples.strategies.kraken_btcusd_research.config_v3 import INSTRUMENT_ID
from examples.strategies.kraken_btcusd_research.strategy_v3 import (
    KrakenBTCUSDMeanReversionStrategy,
    KrakenBTCUSDMeanReversionConfig,
)
from examples.strategies.kraken_btcusd_research.reports import parse_pnl

from datacatalog import ParquetDataCatalog

CATALOG = "data/catalog/kraken_btcusd_15m_2024h1"
LABEL = "2024h1"
START = "2024-01-01"
END = "2024-06-01"

PARAMS = [
    # (zscore_entry, zscore_exit, range_fee_mult, cooldown)
    ("tight-1",  -3.0, 0.5,  8.0, 24),
    ("tight-2",  -3.5, 1.0,  8.0, 48),
]

iid = InstrumentId.from_str(INSTRUMENT_ID)
spec = BarSpecification(15, BarAggregation.MINUTE, PriceType.LAST)
bt = BarType(iid, spec)

catalog = ParquetDataCatalog(path=CATALOG)
bars = catalog.bars(instrument_ids=[INSTRUMENT_ID])
print(f"Loaded {len(bars)} bars for {LABEL}")

results = []
for name, z_ent, z_exit, r_fee, cd in PARAMS:
    cfg = KrakenBTCUSDMeanReversionConfig(
        instrument_id=iid,
        bar_type=bt,
        zscore_entry=z_ent,
        zscore_exit=z_exit,
        min_range_fee_multiple=r_fee,
        cooldown_bars=cd,
    )
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=f"V3-{name}",
            logging=LoggingConfig(log_level="ERROR", bypass_logging=True),
        )
    )
    engine.add_venue(iid.venue, OmsType.NETTING, AccountType.CASH,
        starting_balances=[Money(10000, USD)], base_currency=None)
    engine.add_instrument(CurrencyPair(
        instrument_id=iid, raw_symbol=Symbol("BTCUSD"),
        base_currency=BTC, quote_currency=USD,
        price_precision=2, size_precision=8,
        price_increment=Price(0.01, 2), size_increment=Quantity(0.00000001, 8),
        multiplier=Quantity(1, 0), maker_fee=Decimal("0.0025"),
        taker_fee=Decimal("0.0040"), margin_init=0.0, margin_maint=0.0,
        ts_event=0, ts_init=0,
    ))
    engine.add_data(bars)
    engine.add_strategy(KrakenBTCUSDMeanReversionStrategy(config=cfg))
    engine.run(start=START, end=END)

    pos_df = engine.trader.generate_positions_report()
    total_pnl = 0.0
    total_fees = 0.0
    wins = 0
    losses = 0
    if pos_df is not None and not pos_df.empty and "realized_pnl" in pos_df.columns:
        for _, row in pos_df.iterrows():
            pnl = parse_pnl(row.get("realized_pnl", 0))
            comm = parse_pnl(row.get("commissions", 0))
            total_pnl += pnl
            total_fees += comm
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
    net = total_pnl - total_fees
    n_trades = wins + losses
    avg_win = total_pnl / wins if wins > 0 else 0
    avg_loss = total_pnl / losses if losses > 0 else 0

    print(f"\n{name:10s} | z_in={z_ent:5.1f} z_out={z_exit:5.2f} "
          f"rfm={r_fee:3.1f} cd={cd:3d} | trades={n_trades:3d} "
          f"wins={wins:3d} losses={losses:3d}")
    print(f"           | gross_pnl={total_pnl:10.2f} fees={total_fees:8.2f} "
          f"net={net:10.2f} avg_win={avg_win:8.2f} avg_loss={avg_loss:8.2f}")

    results.append((name, z_ent, z_exit, r_fee, cd, n_trades, total_pnl, total_fees, net))
    engine.dispose()

print(f"\n{'=' * 90}")
print(f"{'Name':<10} {'Trades':>6} {'Gross':>10} {'Fees':>8} {'Net':>10} {'Wins':>4} {'Losses':>6}")
print("-" * 90)
for name, _, _, _, _, n, gp, f, net in results:
    wins = sum(1 for r in results if r[0] == name)  # placeholder
    print(f"{name:<10} {n:>6} {gp:>10.2f} {f:>8.2f} {net:>10.2f}")
print("=" * 90)
