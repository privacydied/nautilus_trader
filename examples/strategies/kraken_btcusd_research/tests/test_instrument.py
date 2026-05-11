#!/usr/bin/env python3
"""
Test instrument definition and catalog importer.
"""

import os
import sys
import tempfile
from pathlib import Path
from decimal import Decimal

from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity

# Add repo to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.strategies.kraken_btcusd_research.config import INSTRUMENT_ID, KRAKEN_VENUE

def test_instrument_id():
    """Verify instrument ID format."""
    instrument_id = InstrumentId.from_str(INSTRUMENT_ID)
    assert instrument_id.venue.value == KRAKEN_VENUE
    assert instrument_id.symbol.value == "BTC/USD"
    print(f"✓ Instrument ID: {instrument_id}")

def test_kraken_spot_instrument():
    """Verify CurrencyPair instrument definition for Kraken spot."""
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

if __name__ == "__main__":
    test_instrument_id()
    test_kraken_spot_instrument()
    print("\n✅ All instrument tests passed!")
