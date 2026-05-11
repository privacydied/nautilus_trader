#!/usr/bin/env python3
"""
Test instrument definition and catalog importer.
"""

import os
import sys
import tempfile
from pathlib import Path
from decimal import Decimal

# Add repo to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.strategies.kraken_btcusd_research import strategy
from examples.strategies.kraken_btcusd_research.config import INSTRUMENT_ID
from examples.strategies.kraken_btcusd_research.import_kraken_ohlcv_to_catalog import csv_to_bars

def test_instrument_id():
    """Verify instrument ID format."""
    from nautilus_trader.model.identifiers import InstrumentId
    from examples.strategies.kraken_btcusd_research.config import KRAKEN_VENUE
    
    instrument_id = InstrumentId.from_str(INSTRUMENT_ID)
    assert instrument_id.venue.value == KRAKEN_VENUE
    assert instrument_id.symbol.value == "BTC/USD"
    print(f"✓ Instrument ID: {instrument_id}")

def test_kraken_spot_instrument():
    """Verify CurrencyPair instrument definition for Kraken spot."""
    from nautilus_trader.model.identifiers import InstrumentId, Symbol
    from nautilus_trader.model.instruments import CurrencyPair
    from nautilus_trader.model.currencies import BTC, USD
    from nautilus_trader.model.objects import Price, Quantity
    
    instrument = CurrencyPair(
        instrument_id=InstrumentId.from_str(INSTRUMENT_ID),
        raw_symbol=Symbol("BTC/USD"),
        base_currency=BTC,
        quote_currency=USD,
        price_precision=2,
        size_precision=8,
        price_increment=Price(0.01, 2),
        size_increment=Quantity(0.00000001, 8),
        multiplier=Quantity(1, 0),
        maker_fee=Decimal("0.0025"),
        taker_fee=Decimal("0.004"),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        ts_event=0,
        ts_init=0,
    )
    assert instrument.id.value == INSTRUMENT_ID
    assert instrument.quote_currency.code == "USD"
    assert instrument.price_precision == 2
    assert instrument.size_precision == 8
    print(f"✓ Instrument created: {instrument}")

def test_csv_to_bars_conversion():
    """Test conversion of CSV to Nautilus Bar objects."""
    import csv
    from datetime import datetime, timezone
    
    # Create synthetic CSV data
    csv_path = Path("test_synthetic.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "vwap", "volume", "count"])
        writer.writeheader()
        # 5 bars, 1 hour apart
        for i in range(5):
            writer.writerow({
                "timestamp": f"2024-01-01T{i:02d}:00:00",
                "open": 40000 + i * 10,
                "high": 40010 + i * 10,
                "low": 39990 + i * 10,
                "close": 40005 + i * 10,
                "vwap": 40005 + i * 10,
                "volume": 100,
                "count": 100,
            })
    
    try:
        bars = csv_to_bars(csv_path)
        assert len(bars) == 5, f"Expected 5 bars, got {len(bars)}"
        assert float(bars[0].open.as_decimal()) == 40000.0
        assert float(bars[-1].close.as_decimal()) == 40045.0
        assert bars[0].ts_event > 0
        print(f"✓ Converted {len(bars)} bars successfully")
        
        # Check BarType
        bar_type = bars[0].bar_type
        assert str(bar_type.instrument_id) == INSTRUMENT_ID
        assert bar_type.spec.step == 5  # 5-minute bars
        print(f"✓ BarType correct: {bar_type}")
    finally:
        if csv_path.exists():
            csv_path.unlink()

if __name__ == "__main__":
    test_instrument_id()
    test_kraken_spot_instrument()
    test_csv_to_bars_conversion()
    print("\n✅ All tests passed!")
