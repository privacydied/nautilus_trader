#!/usr/bin/env python3
"""
Comprehensive test for Kraken BTC/USD research scaffold.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.strategies.kraken_btcusd_research.config import (
    INSTRUMENT_ID,
    STARTING_BALANCE_USD,
    RISK_PER_TRADE,
)


def test_imports():
    """Test that all modules can be imported."""
    from examples.strategies.kraken_btcusd_research import config
    from examples.strategies.kraken_btcusd_research import strategy
    from examples.strategies.kraken_btcusd_research import run_live_kraken_guarded
    from examples.strategies.kraken_btcusd_research import reports
    print("OK")


def test_config_values():
    """Test configuration constants."""
    assert INSTRUMENT_ID == "BTC/USD.KRAKEN", f"Got {INSTRUMENT_ID}"
    assert STARTING_BALANCE_USD == 10000.0
    assert RISK_PER_TRADE == 0.0025
    print("OK")


def test_instrument_id():
    """Test instrument identity — must be BTC/USD.KRAKEN, never XBT."""
    from nautilus_trader.model.identifiers import InstrumentId
    iid = InstrumentId.from_str(INSTRUMENT_ID)
    assert iid.venue.value == "KRAKEN"
    assert iid.symbol.value == "BTC/USD"
    assert "XBT" not in INSTRUMENT_ID
    assert "PI_XBT" not in INSTRUMENT_ID
    assert "PF_XBT" not in INSTRUMENT_ID
    print("OK")
