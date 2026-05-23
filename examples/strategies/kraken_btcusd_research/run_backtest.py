#!/usr/bin/env python3
"""Run backtest for Kraken BTC/USD strategy (V1 or V2)."""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

# Ensure repo root is on path
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import PriceType, OmsType, AccountType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.model.functions import currency_type_from_str
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from examples.strategies.kraken_btcusd_research.config import (
    INSTRUMENT_ID,
    STARTING_BALANCE_USD,
    TIMEFRAME_BARS,
    MAKER_FEE,
    TAKER_FEE,
)
from examples.strategies.kraken_btcusd_research.strategy import (
    KrakenBTCUSDResearchStrategy,
    KrakenBTCUSDResearchConfig,
)
from examples.strategies.kraken_btcusd_research.reports import generate_reports


def parse_args():
    parser = argparse.ArgumentParser(description="Run backtest for Kraken BTC/USD strategy")
    parser.add_argument("--catalog", type=str, required=True, help="Path to ParquetDataCatalog")
    parser.add_argument("--start", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--starting-balance", type=float, default=STARTING_BALANCE_USD)
    parser.add_argument("--reports-dir", type=str, default=None)
    parser.add_argument("--v2", action="store_true", help="Use V2 maker-aware strategy")
    return parser.parse_args()


def _create_instrument() -> CurrencyPair:
    iid = InstrumentId.from_str(INSTRUMENT_ID)
    return CurrencyPair(
        instrument_id=iid, raw_symbol=Symbol("BTCUSD"),
        base_currency=BTC, quote_currency=USD,
        price_precision=2, size_precision=8,
        price_increment=Price(0.01, 2), size_increment=Quantity(0.00000001, 8),
        multiplier=Quantity(1, 0),
        maker_fee=Decimal("0.0025"), taker_fee=Decimal("0.004"),
        margin_init=Decimal(0), margin_maint=Decimal(0),
        ts_event=0, ts_init=0,
    )


def _get_strategy_v1(iid: InstrumentId, bar_type: BarType):
    cfg = KrakenBTCUSDResearchConfig(
        instrument_id=iid, bar_type=bar_type, trade_size=Quantity(Decimal("0.001"), 8),
        atr_period=20, fast_ema_period=20, slow_ema_period=100, donchian_window=55,
        initial_stop_atr_multiplier=2.0, trailing_stop_atr_multiplier=1.5,
        risk_percent=0.0025, max_notional_pct=0.30, cooldown_bars=12,
        maker_fee=MAKER_FEE, taker_fee=TAKER_FEE,
    )
    return KrakenBTCUSDResearchStrategy(config=cfg)


def _get_strategy_v2(iid: InstrumentId, bar_type: BarType):
    from examples.strategies.kraken_btcusd_research.strategy_v2 import (
        KrakenBTCUSDV2Strategy, KrakenBTCUSDV2Config,
    )
    from examples.strategies.kraken_btcusd_research.config_v2 import (
        FAST_EMA_PERIODS, SLOW_EMA_PERIODS, DONCHIAN_WINDOW, ATR_PERIOD,
        VOLUME_MEDIAN_PERIOD, MAKER_ENTRY_EXPIRY_BARS, SLIPPAGE_BUFFER_PCT,
        EXPECTED_MOVE_MULTIPLIER, INITIAL_STOP_ATR_MULTIPLIER,
        TRAILING_STOP_ATR_MULTIPLIER, MAX_NOTIONAL_EXPOSURE_PCT,
        COOLDOWN_BARS, MIN_POSITION_SIZE_BTC,
    )
    cfg = KrakenBTCUSDV2Config(
        instrument_id=iid, bar_type=bar_type,
        trade_size=Quantity(Decimal(str(MIN_POSITION_SIZE_BTC)), 8),
        atr_period=ATR_PERIOD, fast_ema_period=FAST_EMA_PERIODS,
        slow_ema_period=SLOW_EMA_PERIODS, donchian_window=DONCHIAN_WINDOW,
        volume_median_period=VOLUME_MEDIAN_PERIOD,
        initial_stop_atr_multiplier=INITIAL_STOP_ATR_MULTIPLIER,
        trailing_stop_atr_multiplier=TRAILING_STOP_ATR_MULTIPLIER,
        risk_percent=0.0025, max_notional_pct=MAX_NOTIONAL_EXPOSURE_PCT,
        cooldown_bars=COOLDOWN_BARS, maker_entry_expiry_bars=MAKER_ENTRY_EXPIRY_BARS,
        maker_fee=MAKER_FEE, taker_fee=TAKER_FEE,
        slippage_buffer_pct=SLIPPAGE_BUFFER_PCT,
        expected_move_multiplier=EXPECTED_MOVE_MULTIPLIER,
    )
    return KrakenBTCUSDV2Strategy(config=cfg)


def main():
    args = parse_args()

    try:
        catalog = ParquetDataCatalog(path=args.catalog)
        bars = catalog.bars(instrument_ids=[INSTRUMENT_ID])
        if not bars:
            print("No bars found in catalog")
            return 1
        print(f"Loaded {len(bars)} bars from catalog")

        bar_type = bars[0].bar_type
        iid = bar_type.instrument_id

        strat = _get_strategy_v2(iid, bar_type) if args.v2 else _get_strategy_v1(iid, bar_type)
        strategy_name = "V2-maker" if args.v2 else "V1-taker"
        print(f"Strategy: {strategy_name} | BarType: {bar_type} | Instrument: {iid}")

        engine = BacktestEngine(
            config=BacktestEngineConfig(
                trader_id="KRAKEN-BACKTEST",
                logging=LoggingConfig(log_level="INFO", bypass_logging=False),
            )
        )
        engine.add_venue(
            venue=iid.venue, oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(args.starting_balance, USD)],
            base_currency=None,
        )
        engine.add_instrument(_create_instrument())
        engine.add_data(bars)
        engine.add_strategy(strat)

        print(f"Starting backtest {args.start} -> {args.end}...")
        engine.run(start=args.start, end=args.end)

        result = engine.get_result()
        stats_pnls = result.stats_pnls.get("stats", {}) if result.stats_pnls else {}
        stats_returns = result.stats_returns if result.stats_returns else {}

        print(f"\n{'='*60}")
        print(f"Backtest Results ({strategy_name})")
        print(f"{'='*60}")
        print(f"Period:          {result.backtest_start} -> {result.backtest_end}")
        print(f"Total positions: {result.total_positions}")
        print(f"Total orders:    {result.total_orders}")
        print(f"Total events:    {result.total_events}")
        print(f"Total PnL:       {stats_pnls.get('total_pnl', 'N/A')}")
        print(f"Total fees:      {stats_pnls.get('total_fees', 'N/A')}")
        print(f"Sharpe ratio:    {stats_returns.get('sharpe_ratio', 'N/A')}")
        final_eq = stats_returns.get('final_equity', 'N/A')
        print(f"Final equity:    {final_eq}")
        max_dd = stats_returns.get('max_drawdown', 'N/A')
        print(f"Max drawdown:    {max_dd}")

        fills_df = engine.trader.generate_order_fills_report()
        if len(fills_df) > 0:
            print(f"\n=== Fill Report ({len(fills_df)} fills) ===")
            print(fills_df.head(5).to_string())

        pos_df = engine.trader.generate_positions_report()
        if len(pos_df) > 0:
            print(f"\n=== Positions Report ({len(pos_df)} positions) ===")
            print(pos_df.head(5).to_string())

        if args.reports_dir:
            reports_dir = Path(args.reports_dir)
            summary = generate_reports(result, reports_dir, engine=engine)
            print(f"\n=== Summary JSON ===")
            print(json.dumps(summary, indent=2, default=str))

        engine.dispose()
        print("\nBacktest finished.")
        return 0

    except Exception as e:
        print(f"Backtest failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
