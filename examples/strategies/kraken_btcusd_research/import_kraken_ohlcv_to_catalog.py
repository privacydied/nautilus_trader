#!/usr/bin/env python3
"""
Import historical OHLCV data from CSV to Nautilus ParquetDataCatalog.
"""

import csv
import os
from pathlib import Path
from typing import List

from pyarrow import csv as pa_csv
from pyarrow import dataset as ds
from pyarrow import ipc
from pyarrow import Table
from pyarrow import compute as pc


def csv_to_bars(csv_path: Path) -> List:
    """Convert CSV file to Nautilus Bar objects."""
    import pandas as pd
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.data import Bar, BarType, BarSpecification, BarAggregation
    from nautilus_trader.model.enums import PriceType
    from nautilus_trader.model.objects import Price, Quantity
    from nautilus_trader.model.functions import currency_type_from_str
    from examples.strategies.kraken_btcusd_research.config import (
        INSTRUMENT_ID,
        TIMEFRAME_BARS,
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
    )

    # Read CSV using pandas
    df = pd.read_csv(csv_path)
    
    # Parse timestamps (CSV timestamps are strings)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    
    bars = []
    spec = BarSpecification(TIMEFRAME_BARS, BarAggregation.MINUTE, PriceType.LAST)
    bar_type = BarType(InstrumentId.from_str(INSTRUMENT_ID), spec)

    # Determine price precision from the data (default 2)
    price_precision = 2
    size_precision = 8

    for _, row in df.iterrows():
        ts_event = int(row["timestamp"].timestamp() * 1e9)
        ts_init = ts_event
        open_price = Price(float(row["open"]), price_precision)
        high_price = Price(float(row["high"]), price_precision)
        low_price = Price(float(row["low"]), price_precision)
        close_price = Price(float(row["close"]), price_precision)
        volume_qty = Quantity(float(row["volume"]), size_precision)
        bar = Bar(
            bar_type=bar_type,
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=volume_qty,
            ts_event=ts_event,
            ts_init=ts_init,
        )
        bars.append(bar)
    
    return bars


def write_to_catalog(csv_path: Path, catalog_path: Path) -> None:
    """Import CSV data to ParquetDataCatalog."""
    from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
    
    # Read CSV and convert to Bar objects
    bars = csv_to_bars(csv_path)
    
    if not bars:
        raise ValueError("No bars generated from CSV")
    
    # Create catalog
    catalog = ParquetDataCatalog(path=catalog_path)
    
    # Create instrument (simplified - in practice you'd load from config)
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.instruments import CurrencyPair
    from nautilus_trader.model.objects import Currency
    
    instrument = CurrencyPair(
        instrument_id=InstrumentId.from_str(INSTRUMENT_ID),
        raw_symbol="BTC/USD",
        asset_class=Currency,
        quote_currency=Currency(
            code="USD",
            precision=2,
            iso4217=840,
            name="USD",
            currency_type=currency_type_from_str("fiat"),
        ),
        is_inverse=False,
        price_precision=2,
        size_precision=8,
        price_increment=0.01,
        size_increment=0.00000001,
        multiplier=1.0,
        maker_fee=Decimal("0.0025"),
        taker_fee=Decimal("0.0040"),
        margin_init=0.0,
        margin_maint=0.0,
        ts_event=0,
        ts_init=0,
    )
    
    # Write to catalog
    catalog.write_data([instrument])
    catalog.write_data(bars)
    
    print(f"✅ Imported {len(bars)} bars to catalog at {catalog_path}")