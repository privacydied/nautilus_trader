#!/usr/bin/env python3
"""
Import multiple Kraken OHLCV CSV files into a ParquetDataCatalog.

Usage:
    python import_multi_catalog.py --csv-dir data/kraken_4h/ \
        --catalog data/catalog/kraken_4h_universe/ --interval 240
"""

import argparse
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent.parent.parent))

from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar, BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

# Instrument ID → (price_precision, size_precision)
INSTRUMENT_PRECISION = {
    "BTC/USD.KRAKEN": (2, 8),
    "ETH/USD.KRAKEN": (2, 8),
    "SOL/USD.KRAKEN": (2, 4),
    "XRP/USD.KRAKEN": (4, 2),
    "ADA/USD.KRAKEN": (5, 0),
    "LINK/USD.KRAKEN": (2, 4),
    "DOGE/USD.KRAKEN": (5, 0),
    "AVAX/USD.KRAKEN": (2, 4),
    "LTC/USD.KRAKEN": (2, 8),
    "BCH/USD.KRAKEN": (2, 8),
}


def make_instrument(id_str: str) -> CurrencyPair:
    iid = InstrumentId.from_str(id_str)
    pp, sp = INSTRUMENT_PRECISION.get(id_str, (2, 8))
    return CurrencyPair(
        instrument_id=iid,
        raw_symbol=Symbol(id_str.replace("/USD.KRAKEN", "").replace("BTC", "XBT")),
        base_currency=BTC,
        quote_currency=USD,
        price_precision=pp,
        size_precision=sp,
        price_increment=Price(10 ** -pp, pp),
        size_increment=Quantity(10 ** -sp, sp),
        maker_fee=Decimal("0.0025"),
        taker_fee=Decimal("0.004"),
        multiplier=Quantity(1, 0),
        margin_init=Decimal(0),
        margin_maint=Decimal(0),
        ts_event=0,
        ts_init=0,
    )


def csv_to_bars(csv_path: Path, instrument_id: str, spec: BarSpecification) -> list[Bar]:
    """Convert a Kraken OHLCV CSV to Nautilus Bar objects."""
    iid = InstrumentId.from_str(instrument_id)
    bt = BarType(iid, spec)
    pp, sp = INSTRUMENT_PRECISION.get(instrument_id, (2, 8))
    df = pd.read_csv(csv_path)
    bars: list[Bar] = []
    for _, row in df.iterrows():
        ts = int(pd.Timestamp(row["timestamp"]).timestamp() * 1_000_000_000)
        bars.append(Bar(
            bar_type=bt,
            open=Price(float(row["open"]), pp),
            high=Price(float(row["high"]), pp),
            low=Price(float(row["low"]), pp),
            close=Price(float(row["close"]), pp),
            volume=Quantity(float(row["volume"]), sp),
            ts_event=ts,
            ts_init=ts,
        ))
    return bars


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv-dir", required=True, help="Dir containing CSV files")
    parser.add_argument("--catalog", required=True, help="Output catalog path")
    parser.add_argument("--interval", type=int, default=240, help="Bar interval in minutes")
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)
    catalog_path = Path(args.catalog)
    spec = BarSpecification(args.interval, BarAggregation.MINUTE, PriceType.LAST)

    if not csv_dir.is_dir():
        print(f"Error: {csv_dir} not found")
        sys.exit(1)

    catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(path=str(catalog_path))

    csv_files = sorted(csv_dir.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files in {csv_dir}")
        sys.exit(1)

    total = 0
    for csv_path in csv_files:
        # Infer instrument ID from filename: XBTUSD_240.csv → BTC/USD.KRAKEN
        stem = csv_path.stem  # e.g. XBTUSD_240
        pair_part = stem.split("_")[0]
        # Map Kraken API pair back to our ID
        api_to_id = {
            "XBTUSD": "BTC/USD.KRAKEN",
            "ETHUSD": "ETH/USD.KRAKEN",
            "SOLUSD": "SOL/USD.KRAKEN",
            "XRPUSD": "XRP/USD.KRAKEN",
            "ADAUSD": "ADA/USD.KRAKEN",
            "LINKUSD": "LINK/USD.KRAKEN",
            "DOGEUSD": "DOGE/USD.KRAKEN",
            "AVAXUSD": "AVAX/USD.KRAKEN",
            "LTCUSD": "LTC/USD.KRAKEN",
            "BCHUSD": "BCH/USD.KRAKEN",
        }
        id_str = api_to_id.get(pair_part)
        if not id_str:
            print(f"  SKIP {csv_path.name}: unknown pair {pair_part}")
            continue

        bars = csv_to_bars(csv_path, id_str, spec)
        if not bars:
            print(f"  EMPTY {id_str}")
            continue

        instrument = make_instrument(id_str)
        catalog.write_data([instrument])
        catalog.write_data(bars)
        print(f"  {id_str}: {len(bars):,} bars")
        total += len(bars)

    print(f"\nTotal: {total:,} bars in {catalog_path}")


if __name__ == "__main__":
    main()
