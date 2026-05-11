#!/usr/bin/env python3
"""
Test basic configuration and imports.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from examples.strategies.kraken_btcusd_research import config
from examples.strategies.kraken_btcusd_research import strategy
from examples.strategies.kraken_btcusd_research import download_kraken_ohlcv
from examples.strategies.kraken_btcusd_research import import_kraken_ohlcv_to_catalog
from examples.strategies.kraken_btcusd_research import run_backtest
from examples.strategies.kraken_btcusd_research import run_live_kraken_guarded
from examples.strategies.kraken_btcusd_research import reports

def test_imports():
    """Test that all modules can be imported."""
    try:
        from examples.strategies.kraken_btcusd_research import config
        from examples.strategies.kraken_btcusd_research import strategy
        from examples.strategies.kraken_btcusd_research import download_kraken_ohlcv
        from examples.strategies.kraken_btcusd_research import import_kraken_ohlcv_to_catalog
        from examples.strategies.kraken_btcusd_research import run_backtest
        from examples.strategies.kraken_btcusd_research import run_live_kraken_guarded
        from examples.strategies.kraken_btcusd_research import reports
        
        print("✓ All modules imported successfully")
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False

def test_config():
    """Test configuration parameters."""
    try:
        from examples.strategies.kraken_btcusd_research.config import (
            INSTRUMENT_ID,
            STARTING_BALANCE_USD,
            RISK_PER_TRADE,
        )
        assert INSTRUMENT_ID == "BTC/USD.KRAKEN"
        assert STARTING_BALANCE_USD == 10000
        assert str(RISK_PER_TRADE) == "0.0025"
        print("✓ Config parameters correct")
        return True
    except Exception as e:
        print(f"✗ Config test failed: {e}")
        return False

if __name__ == "__main__":
    tests = [
        test_imports,
        test_config,
    ]
    
    results = {}
    for test in tests:
        results[test.__name__] = test()
    
    print("\n" + "="*50)
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    all_passed = all(results.values())
    if all_passed:
        print("\n✅ All tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed")
        sys.exit(1)