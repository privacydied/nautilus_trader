#!/usr/bin/env python3
"""
Basic test to verify the scaffold package can be imported.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_imports():
    """Test that main modules can be imported."""
    try:
        import examples.strategies.kraken_btcusd_research.config as config_mod
        import examples.strategies.kraken_btcusd_research.strategy as strategy_mod
        import examples.strategies.kraken_btcusd_research.download_kraken_ohlcv as download_mod
        import examples.strategies.kraken_btcusd_research.import_kraken_ohlcv_to_catalog as import_mod
        import examples.strategies.kraken_btcusd_research.run_backtest as run_backtest_mod
        import examples.strategies.kraken_btcusd_research.run_live_kraken_guarded as live_mod
        import examples.strategies.kraken_btcusd_research.reports as reports_mod
        
        print(f"✓ config imported: {config_mod}")
        print(f"✓ strategy imported: {strategy_mod}")
        print(f"✓ download imported: {download_mod}")
        print(f"✓ import module imported: {import_mod}")
        print(f"✓ run_backtest imported: {run_backtest_mod}")
        print(f"✓ run_live_kraken_guarded imported: {live_mod}")
        print(f"✓ reports imported: {reports_mod}")
    except ImportError as e:
        print(f"✗ Import failed: {e}")
        return False
    return True

def test_strategy_creation():
    """Test that strategy can be created."""
    from examples.strategies.kraken_btcusd_research.strategy import create_strategy
    strategy = create_strategy()
    assert strategy is not None
    print("✓ Strategy created successfully")

def test_config():
    """Test configuration parameters."""
    from examples.strategies.kraken_btcusd_research.config import (
        INSTRUMENT_ID,
        STARTING_BALANCE_USD,
        RISK_PER_TRADE,
    )
    assert INSTRUMENT_ID == "BTC/USD.KRAKEN"
    assert STARTING_BALANCE_USD == 10000
    assert str(RISK_PER_TRADE) == "0.0025"
    print("✓ Config parameters correct")

if __name__ == "__main__":
    test_imports()
    test_strategy_creation()
    test_config()
    print("\n✅ All basic tests passed!")