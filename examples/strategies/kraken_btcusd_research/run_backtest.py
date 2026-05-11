#!/usr/bin/env python3
"""
Run backtest for Kraken BTC/USD strategy.

Usage:
    python run_backtest.py --catalog data/catalog/kraken_btcusd --start 2024-01-01 --end 2024-06-01 --starting-balance 10000
"""

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.core.nautilus_pyo3 import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.functions import currency_type_from_str
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from .config import (
    INSTRUMENT_ID,
    STARTING_BALANCE_USD,
    MAKER_FEE,
    TAKER_FEE,
)
from .strategy import create_strategy


def parse_args():
    parser = argparse.ArgumentParser(description="Run backtest for Kraken BTC/USD strategy")
    parser.add_argument(
        "--catalog",
        type=str,
        required=True,
        help="Path to ParquetDataCatalog",
    )
    parser.add_argument(
        "--start",
        type=str,
        required=True,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end",
        type=str,
        required=True,
        help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--starting-balance",
        type=float,
        default=STARTING_BALANCE_USD,
        help="Starting balance in USD",
    )
    return parser.parse_args()


def create_kraken_instrument() -> CurrencyPair:
    """Create Kraken BTC/USD instrument."""
    instrument_id = InstrumentId.from_str(INSTRUMENT_ID)
    return CurrencyPair(
        instrument_id=instrument_id,
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
        maker_fee=MAKER_FEE,
        taker_fee=TAKER_FEE,
        margin_init=0.0,
        margin_maint=0.0,
        ts_event=0,
        ts_init=0,
    )


def generate_reports(backtest_result_path: Path, output_dir: Path) -> None:
    """Generate reports from backtest results.
    
    Args:
        backtest_result_path: Path to backtest results
        output_dir: Output directory for reports
    """
    from .reports import BacktestReportGenerator
    
    generator = BacktestReportGenerator(backtest_result_path, output_dir)
    summary = generator.generate_summary()
    
    print(f"Reports generated in {output_dir}")


def main():
    args = parse_args()
    
    try:
        # Create data catalog
        catalog = ParquetDataCatalog(path=args.catalog)
        
        # Get instrument
        instruments = catalog.instruments()
        if not instruments:
            print("No instruments found in catalog")
            return 1
        instrument = instruments[0]
        print(f"Using instrument: {instrument}")
        
        # Get bars for the instrument
        bar_type = BarType(InstrumentId.from_str(INSTRUMENT_ID), 5)  # 5-minute bars
        bars = catalog.bars(bar_types=[str(bar_type)])
        print(f"Loaded {len(bars)} bars from catalog")
        
        if not bars:
            print("No bars found in catalog")
            return 1
        
        # Create backtest engine
        engine_config = BacktestEngineConfig(
            trader_id="KRAKEN-BTC-USD-BACKTEST",
            logging=LoggingConfig(log_level="INFO"),
        )
        engine = BacktestEngine(config=engine_config)
        
        # Configure trading venue (CASH account for spot)
        engine.add_venue(
            venue=instrument.id.venue,
            oms_type="NETTING",
            account_type="CASH",
            starting_balances=[args.starting_balance],
            base_currency=Currency(code="USD", precision=2, iso4217=840, name="USD", currency_type=currency_type_from_str("fiat")),
            default_leverage=1,  # No leverage
        )
        
        # Add instrument and data
        engine.add_instrument(instrument)
        engine.add_data(bars)
        
        # Create and add strategy
        strategy = create_strategy()
        engine.add_strategy(strategy)
        
        # Execute backtest
        print("Starting backtest...")
        engine.run(
            start=args.start,
            end=args.end,
        )

        # Save the backtest result to a pickle file for report generation
        import pickle
        result_file = backtest_result_path / "result.pkl"
        with open(result_file, "wb") as f:
            pickle.dump(engine.run_result, f)
        print(f"Backtest result saved to {result_file}")
        
        # Get results path
        backtest_result_path = Path(engine.run_result.workdir) / "backtest_result"
        print(f"Backtest completed. Results in: {backtest_result_path}")
        
        # Generate reports
        reports_dir = Path("reports")
        generate_reports(backtest_result_path, reports_dir)
        
        # Clean up
        engine.dispose()
        
        print("Backtest finished successfully")
        return 0
        
    except Exception as e:
        print(f"Backtest failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())