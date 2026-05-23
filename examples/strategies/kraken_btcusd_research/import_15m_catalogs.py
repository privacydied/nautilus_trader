#!/usr/bin/env python3
"""Import 15m resampled BTC/USD bars into Nautilus catalogs for V2 backtesting."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
from decimal import Decimal
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar, BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

PERIODS = [
    ("data/kraken/BTCUSD_15m_2024h1.csv", "data/catalog/kraken_btcusd_15m_2024h1"),
    ("data/kraken/BTCUSD_15m_2024h2.csv", "data/catalog/kraken_btcusd_15m_2024h2"),
    ("data/kraken/BTCUSD_15m_2025.csv",   "data/catalog/kraken_btcusd_15m_2025"),
    ("data/kraken/BTCUSD_15m_2026.csv",   "data/catalog/kraken_btcusd_15m_2026"),
]

iid = InstrumentId.from_str("BTC/USD.KRAKEN")
spec = BarSpecification(15, BarAggregation.MINUTE, PriceType.LAST)
bar_type = BarType(iid, spec)

instr = CurrencyPair(
    instrument_id=iid, raw_symbol=Symbol("BTCUSD"),
    base_currency=BTC, quote_currency=USD,
    price_precision=2, size_precision=8,
    price_increment=Price(0.01, 2), size_increment=Quantity(0.00000001, 8),
    multiplier=Quantity(1, 0),
    maker_fee=Decimal("0.0025"), taker_fee=Decimal("0.004"),
    margin_init=Decimal(0), margin_maint=Decimal(0),
    ts_event=0, ts_init=0,
)

for csv_path, catalog_path in PERIODS:
    csv_p = Path(csv_path)
    cat_p = Path(catalog_path)
    if not csv_p.exists():
        print(f"Missing {csv_path}, skipping")
        continue
    
    df = pd.read_csv(csv_p)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    
    bars = []
    for _, row in df.iterrows():
        ts = int(row["timestamp"].timestamp() * 1e9)
        bars.append(Bar(
            bar_type=bar_type,
            open=Price(float(row["open"]), 2),
            high=Price(float(row["high"]), 2),
            low=Price(float(row["low"]), 2),
            close=Price(float(row["close"]), 2),
            volume=Quantity(float(row["volume"]), 8),
            ts_event=ts,
            ts_init=ts,
        ))
    
    catalog = ParquetDataCatalog(path=str(cat_p))
    catalog.write_data([instr])
    catalog.write_data(bars)
    print(f"Imported {len(bars)} 15m bars -> {catalog_path}")
