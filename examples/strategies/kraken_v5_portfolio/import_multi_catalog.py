#!/usr/bin/env python3
"""
Import multiple Kraken OHLCV CSV files into a Nautilus ParquetDataCatalog.

Usage:
    python import_multi_catalog.py --csv-dir data/kraken_daily/ \
        --catalog data/catalog/kraken_daily_universe/
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

from examples.strategies.kraken_v5_portfolio.instrument_details import PRICE_SIZE_PRECISION

# Aliased as INSTRUMENT_PRECISION for backward compat with existing code
INSTRUMENT_PRECISION = {}
for k, (pp, sp) in PRICE_SIZE_PRECISION.items():
    INSTRUMENT_PRECISION[k] = (pp, sp)

PREFIX_TO_ID = {
    "BTC_USD": "BTC/USD.KRAKEN",
    "ETH_USD": "ETH/USD.KRAKEN",
    "SOL_USD": "SOL/USD.KRAKEN",
    "XRP_USD": "XRP/USD.KRAKEN",
    "ADA_USD": "ADA/USD.KRAKEN",
    "LINK_USD": "LINK/USD.KRAKEN",
    "DOGE_USD": "DOGE/USD.KRAKEN",
    "AVAX_USD": "AVAX/USD.KRAKEN",
    "LTC_USD": "LTC/USD.KRAKEN",
    "BCH_USD": "BCH/USD.KRAKEN",
    "XBT_USD": "BTC/USD.KRAKEN",
}


def make_instrument(id_str: str) -> CurrencyPair:
    iid = InstrumentId.from_str(id_str)
    pp, sp = INSTRUMENT_PRECISION.get(id_str, (2, 8))
    sym = id_str.split("/USD")[0].replace("BTC", "XBT")
    return CurrencyPair(
        instrument_id=iid,
        raw_symbol=Symbol(sym),
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
    parser.add_argument("--csv-dir", required=True)
    parser.add_argument("--catalog", required=True)
    args = parser.parse_args()

    csv_dir = Path(args.csv_dir)
    catalog_path = Path(args.catalog)

    # Daily bars
    spec = BarSpecification(1, BarAggregation.DAY, PriceType.LAST)

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
        stem = csv_path.stem
        parts = stem.rsplit("_", 1)
        prefix = parts[0]
        id_str = PREFIX_TO_ID.get(prefix)
        if not id_str:
            print(f"  SKIP {csv_path.name}: unknown prefix {prefix}")
            continue

        bars = csv_to_bars(csv_path, id_str, spec)
        if not bars:
            print(f"  EMPTY {id_str}")
            continue

        instrument = make_instrument(id_str)
        catalog.write_data([instrument])
        catalog.write_data(bars)
        print(f"  {id_str}: {len(bars):,} bars ({bars[0].ts_event} -> {bars[-1].ts_event})")
        total += len(bars)

    print(f"\nTotal: {total:,} bars in {catalog_path}")

    seen = set()
    for prefix, id_str in PREFIX_TO_ID.items():
        if id_str in seen:
            continue
        seen.add(id_str)
        try:
            count = catalog.bars(instrument_ids=[id_str])
            if count:
                print(f"  VERIFY {id_str}: {len(count):,} bars")
            else:
                print(f"  VERIFY {id_str}: MISSING")
        except Exception:
            pass


if __name__ == "__main__":
    main()
