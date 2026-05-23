#!/usr/bin/env python3
"""
Import historical OHLCV data from CSV to Nautilus ParquetDataCatalog.

Usage:
    python import_kraken_ohlcv_to_catalog.py \
      --csv data/kraken/BTCUSD_5m_2024h1.csv \
      --catalog data/catalog/kraken_btcusd_2024h1
"""

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

# Import config using direct path manipulation
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from examples.strategies.kraken_btcusd_research.config import (
    INSTRUMENT_ID,
    TIMEFRAME_BARS,
    MAKER_FEE,
    TAKER_FEE,
)

import pandas as pd
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar, BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog


def csv_to_bars(csv_path: Path) -> list[Bar]:
    iid = InstrumentId.from_str(INSTRUMENT_ID)
    spec = BarSpecification(TIMEFRAME_BARS, BarAggregation.MINUTE, PriceType.LAST)
    bar_type = BarType(iid, spec)

    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    bars: list[Bar] = []
    for _, row in df.iterrows():
        ts_event = int(row["timestamp"].timestamp() * 1e9)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price(float(row["open"]), 2),
                high=Price(float(row["high"]), 2),
                low=Price(float(row["low"]), 2),
                close=Price(float(row["close"]), 2),
                volume=Quantity(float(row["volume"]), 8),
                ts_event=ts_event,
                ts_init=ts_event,
            )
        )
    return bars


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=str, required=True, help="Path to CSV file")
    parser.add_argument("--catalog", type=str, required=True, help="Path to catalog directory")
    args = parser.parse_args()

    bars = csv_to_bars(Path(args.csv))
    if not bars:
        raise ValueError("No bars generated from CSV")

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

    catalog = ParquetDataCatalog(path=str(args.catalog))
    catalog.write_data([instrument])
    catalog.write_data(bars)
    print(f"Imported {len(bars)} bars to catalog at {args.catalog}")


if __name__ == "__main__":
    main()
