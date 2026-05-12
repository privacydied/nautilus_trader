#!/usr/bin/env python3
"""Debug: print actual columns and first few rows from engine position report."""
import sys
from pathlib import Path
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from decimal import Decimal
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar, BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import AccountType, OmsType, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity, Money
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from examples.strategies.kraken_btcusd_research.config_v3 import INSTRUMENT_ID
from examples.strategies.kraken_btcusd_research.strategy_v3 import (
    KrakenBTCUSDMeanReversionStrategy,
    KrakenBTCUSDMeanReversionConfig,
)

iid = InstrumentId.from_str(INSTRUMENT_ID)
spec = BarSpecification(15, BarAggregation.MINUTE, PriceType.LAST)
bt = BarType(iid, spec)

catalog = ParquetDataCatalog(path="data/catalog/kraken_btcusd_15m_2024h1")
bars = catalog.bars(instrument_ids=[INSTRUMENT_ID])
print(f"Loaded {len(bars)} bars")

cfg = KrakenBTCUSDMeanReversionConfig(
    instrument_id=iid, bar_type=bt,
)
engine = BacktestEngine(
    config=BacktestEngineConfig(
        trader_id="V3-DEBUG",
        logging=LoggingConfig(log_level="ERROR", bypass_logging=True),
    )
)
engine.add_venue(iid.venue, OmsType.NETTING, AccountType.CASH,
    starting_balances=[Money(10000, USD)], base_currency=None)
engine.add_instrument(CurrencyPair(
    instrument_id=iid, raw_symbol=Symbol("BTCUSD"), base_currency=BTC, quote_currency=USD,
    price_precision=2, size_precision=8,
    price_increment=Price(0.01, 2), size_increment=Quantity(0.00000001, 8),
    multiplier=Quantity(1, 0), maker_fee=0.0025, taker_fee=0.004,
    margin_init=0.0, margin_maint=0.0, ts_event=0, ts_init=0,
))
engine.add_data(bars)
engine.add_strategy(KrakenBTCUSDMeanReversionStrategy(config=cfg))
engine.run(start="2024-01-01", end="2024-06-01")

positions_df = engine.trader.generate_positions_report()
print(f"\nPosition report columns: {list(positions_df.columns)}")
print(f"\nFirst 3 rows:")
print(positions_df.head(3).to_string())
print(f"\nShape: {positions_df.shape}")

fills_df = engine.trader.generate_order_fills_report()
print(f"\nFills report shape: {fills_df.shape}")
if len(fills_df) > 0:
    print(f"\nFills report columns: {list(fills_df.columns)}")
    print(f"\nFirst 3 fill rows:")
    print(fills_df.head(3).to_string())

result = engine.get_result()
print(f"\nBacktestResult stats_pnls keys: {list(result.stats_pnls.keys())}")
print(f"stats_pnls[stats]: {result.stats_pnls.get('stats', {})}")
print(f"stats_returns: {result.stats_returns}")
print(f"total_positions: {result.total_positions}")

engine.dispose()
