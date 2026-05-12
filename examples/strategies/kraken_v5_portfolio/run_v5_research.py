#!/usr/bin/env python3
"""
V5: Multi-Asset Higher-Timeframe Momentum — Research Runner.

Downloads 4h bars for liquid Kraken USD pairs, builds per-instrument
catalogs, runs the portfolio strategy in BacktestEngine, and produces
a multi-window results table.

Usage:
    python run_v5_research.py          # uses default pairs and windows
    python run_v5_research.py --pair BTC/USD --pair ETH/USD  # subset
"""

import sys
from decimal import Decimal
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import PriceType, OmsType, AccountType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from nautilus_trader.model.data import BarAggregation

from examples.strategies.kraken_v5_portfolio.config_v5 import (
    LIQUID_PAIRS,
    KRAKEN_VENUE,
    BAR_SPEC_STEP,
    EMA_TREND_PERIOD,
    ATR_PERIOD,
    MAX_POSITIONS,
    MAX_NOTIONAL_PCT,
    TRAILING_STOP_ATR_MULT,
    STARTING_BALANCE_USD,
    TAKER_FEE,
    WINDOWS,
)
from examples.strategies.kraken_v5_portfolio.strategy_v5 import (
    KrakenV5PortfolioStrategy,
    KrakenV5PortfolioConfig,
)


def to_instrument_id(pair: str) -> str:
    base, quote = pair.split("/")
    if base == "BTC":
        base = "XBT"
    return f"{base}{quote}.{KRAKEN_VENUE}"


def _make_instrument(iid: InstrumentId) -> CurrencyPair:
    return CurrencyPair(
        instrument_id=iid,
        raw_symbol=Symbol(iid.symbol.value.replace("XBT", "BTC")),
        base_currency=BTC,
        quote_currency=USD,
        price_precision=2,
        size_precision=8,
        price_increment=Price(0.01, 2),
        size_increment=Quantity(0.00000001, 8),
        multiplier=Quantity(1, 0),
        maker_fee=Decimal("0.0025"),
        taker_fee=Decimal("0.004"),
        margin_init=Decimal(0),
        margin_maint=Decimal(0),
        ts_event=0,
        ts_init=0,
    )


def run_window(catalog_path: str, label: str, start: str, end: str, pairs=None):
    print(f"\n{'='*50}")
    print(f"V5 Portfolio: {label} ({start} -> {end})")
    print(f"{'='*50}")

    cat = ParquetDataCatalog(path=catalog_path)
    if pairs is None:
        pairs = LIQUID_PAIRS
    iids = [InstrumentId.from_str(to_instrument_id(p)) for p in pairs]

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id="V5-PORTFOLIO",
        logging=LoggingConfig(bypass_logging=True),
    ))
    engine.add_venue(
        iids[0].venue,
        OmsType.NETTING,
        AccountType.CASH,
        starting_balances=[Money(STARTING_BALANCE_USD, USD)],
    )

    spec = BarSpecification(BAR_SPEC_STEP, BarAggregation.HOUR, PriceType.LAST)

    for iid in iids:
        engine.add_instrument(_make_instrument(iid))
        bars = cat.bars(instrument_ids=[str(iid)])
        if not bars:
            print(f"  WARNING: no bars for {iid}")
            continue
        filter_range = bars.slice(start, end)
        if len(filter_range) > 0:
            engine.add_data(filter_range)
            print(f"  {iid}: {len(filter_range)} bars")

    bt = BarType(iids[0], spec)
    cfg = KrakenV5PortfolioConfig(
        universe=[to_instrument_id(p) for p in pairs],
        bar_type=bt,
        ema_trend_period=EMA_TREND_PERIOD,
        atr_period=ATR_PERIOD,
        max_positions=MAX_POSITIONS,
        max_notional_pct=MAX_NOTIONAL_PCT,
        trailing_stop_atr_mult=TRAILING_STOP_ATR_MULT,
        taker_fee=TAKER_FEE,
    )

    strategy = KrakenV5PortfolioStrategy(config=cfg)
    engine.add_strategy(strategy)
    engine.run(start=start, end=end)
    result = engine.get_result()
    # Extract stats from engine — Nautilus keys stats_pnls by currency
    raw_stats = {}
    for key, val in result.stats_pnls.items():
        if isinstance(val, dict):
            raw_stats.update(val)
    total_pnl = float(raw_stats.get("PnL (total)", 0.0))
    total_fees = float(raw_stats.get("total_commissions", 0.0))

    try:
        pos_df = engine.trader.generate_positions_report()
    except Exception:
        pos_df = None

    wins = 0
    losses = 0
    if pos_df is not None and len(pos_df) > 0 and "realized_pnl" in pos_df.columns:
        for _, row in pos_df.iterrows():
            pnl = float(str(row.get("realized_pnl", 0)).replace("USD", "").replace("'", "").strip())
            comm = float(str(row.get("commissions", 0)).replace("USD", "").replace("'", "").strip())
            total_pnl += pnl
            total_fees += comm
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1

    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    sharpe = result.get("sharpe_ratio", 0.0) if result else 0.0

    print(f"  Trades:   {total_trades}")
    print(f"  Win Rate: {win_rate:.1f}%")
    print(f"  Gross:    {total_pnl:.2f}")
    print(f"  Fees:     {total_fees:.2f}")
    print(f"  Net:      {total_pnl - total_fees:.2f}")
    print(f"  Sharpe:   {sharpe:.2f}")

    engine.dispose()
    return {
        "trades": total_trades,
        "win_rate": win_rate,
        "gross_pnl": total_pnl,
        "fees": total_fees,
        "net_pnl": total_pnl - total_fees,
        "sharpe": sharpe,
        "wins": wins,
        "losses": losses,
    }


def main():
    repo_root = REPO_ROOT
    catalog_base = repo_root / "data" / "catalog"

    results = {}
    for label, start, end in WINDOWS:
        catalog_path = catalog_base / f"kraken_v5_4h_{label}"
        if catalog_path.exists():
            results[label] = run_window(str(catalog_path), label, start, end)
        else:
            print(f"\n  SKIP {label}: no catalog at {catalog_path}")

    print(f"\n{'='*70}")
    print("V5 Portfolio Results")
    print(f"{'='*70}")
    header = f"{'Window':<10} {'Trades':>6} {'Win%':>6} {'Gross':>10} {'Fees':>8} {'Net':>10} {'Sharpe':>8}"
    print(header)
    print("-" * 70)
    for label, start, end in WINDOWS:
        s = results.get(label)
        if s:
            print(f"{label:<10} {s['trades']:>6} {s['win_rate']:>5.1f} {s['gross_pnl']:>10.2f} {s['fees']:>8.2f} {s['net_pnl']:>10.2f} {s['sharpe']:>8.2f}")
        else:
            print(f"{label:<10} {'SKIP':>6} {'N/A':>6} {'N/A':>10} {'N/A':>8} {'N/A':>10} {'N/A':>8}")
    print("=" * 70)

    gross_pos = sum(1 for s in results.values() if s["gross_pnl"] > 0)
    net_pos = sum(1 for s in results.values() if s["net_pnl"] > 0)
    total = len(results)

    print(f"\nGross positive in {gross_pos}/{total} windows")
    print(f"Net positive in {net_pos}/{total} windows")
    if gross_pos >= total // 2 + 1:
        print("PROMISING -- continue research")
    else:
        print("REJECTED -- insufficient gross edge")


if __name__ == "__main__":
    main()
