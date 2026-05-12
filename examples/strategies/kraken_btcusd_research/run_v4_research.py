#!/usr/bin/env python3
"""
Run V4 1h trend-following research on Kraken BTC/USD spot.

Loads 15m catalog bars and aggregates to 1h bars in memory per window.
This is the FINAL bar-level BTC/USD spot technical-signal test.
If V4 fails gross, stop BTC/USD spot bar-level technical research.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path
from collections import defaultdict

# Ensure repo root is on path
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, USD
from nautilus_trader.model.data import Bar, BarType, BarSpecification, BarAggregation
from nautilus_trader.model.enums import PriceType, OmsType, AccountType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from examples.strategies.kraken_btcusd_research.config_v4 import (
    INSTRUMENT_ID, STARTING_BALANCE_USD,
    EMA_FAST_PERIODS, EMA_SLOW_PERIODS, DONCHIAN_WINDOW, ATR_PERIOD,
    TRAILING_STOP_ATR_MULTIPLIER, TAKER_FEE, MAKER_FEE, MIN_POSITION_SIZE_BTC,
)
from examples.strategies.kraken_btcusd_research.strategy_v4 import (
    KrakenBTCUSDV4TrendStrategy, KrakenBTCUSDV4TrendConfig,
)
from examples.strategies.kraken_btcusd_research.reports import (
    generate_reports, parse_pnl, parse_commission,
)

WINDOWS = [
    ("2024h1", "2024-01-01", "2024-06-01"),
    ("2024h2", "2024-07-01", "2025-01-01"),
    ("2025", "2025-01-01", "2026-01-01"),
    ("2026", "2026-01-01", "2026-05-01"),
]


def aggregate_bars_to_1h(bars_15m, iid):
    """Aggregate 15m bars to 1h bars. 4x 15m bars = 1h bar."""
    spec_1h = BarSpecification(1, BarAggregation.HOUR, PriceType.LAST)
    bt_1h = BarType(iid, spec_1h)
    bars_1h = []
    group = []
    for bar in bars_15m:
        group.append(bar)
        if len(group) == 4:
            o = float(group[0].open)
            h = max(float(b.high) for b in group)
            l = min(float(b.low) for b in group)
            c = float(group[-1].close)
            vol = sum(float(b.volume.as_decimal()) for b in group)
            ts = group[0].ts_event
            bars_1h.append(Bar(
                bar_type=bt_1h,
                open=Price(o, 2), high=Price(h, 2),
                low=Price(l, 2), close=Price(c, 2),
                volume=Quantity(vol, 8),
                ts_event=ts, ts_init=ts,
            ))
            group = []
    return bars_1h


def _make_instrument(iid):
    return CurrencyPair(
        instrument_id=iid, raw_symbol=Symbol("BTCUSD"),
        base_currency=BTC, quote_currency=USD,
        price_precision=2, size_precision=8,
        price_increment=Price(0.01, 2),
        size_increment=Quantity(0.00000001, 8),
        multiplier=Quantity(1, 0),
        maker_fee=Decimal("0.0025"), taker_fee=Decimal("0.004"),
        margin_init=Decimal(0), margin_maint=Decimal(0),
        ts_event=0, ts_init=0,
    )


def run_window(catalog_path, label, start, end, reports_dir, starting_balance=STARTING_BALANCE_USD):
    print(f"\n{'='*50}")
    print(f"V4 1h Trend: {label} ({start} -> {end})")
    print(f"{'='*50}")

    catalog = ParquetDataCatalog(path=catalog_path)
    bars_15m = catalog.bars(instrument_ids=[INSTRUMENT_ID])
    if not bars_15m:
        print(f"  No bars found for {label}. Skipping.")
        return None

    iid = bars_15m[0].bar_type.instrument_id
    bars_1h = aggregate_bars_to_1h(bars_15m, iid)
    print(f"  {len(bars_15m)} 15m bars -> {len(bars_1h)} 1h bars | BarType: {bars_1h[0].bar_type}")

    cfg = KrakenBTCUSDV4TrendConfig(
        instrument_id=iid, bar_type=bars_1h[0].bar_type,
        ema_fast_period=EMA_FAST_PERIODS, ema_slow_period=EMA_SLOW_PERIODS,
        donchian_window=DONCHIAN_WINDOW, atr_period=ATR_PERIOD,
        trailing_stop_atr_multiplier=TRAILING_STOP_ATR_MULTIPLIER,
    )
    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id="KRAKEN-V4-1H",
        logging=LoggingConfig(log_level="ERROR", bypass_logging=True),
    ))
    engine.add_venue(iid.venue, OmsType.NETTING, AccountType.CASH,
        starting_balances=[Money(starting_balance, USD)], base_currency=None)
    engine.add_instrument(_make_instrument(iid))
    engine.add_data(bars_1h)
    engine.add_strategy(KrakenBTCUSDV4TrendStrategy(config=cfg))
    engine.run(start=start, end=end)

    result = engine.get_result()

    # Extract PnL from engine reports
    try:
        positions_df = engine.trader.generate_positions_report()
    except Exception:
        positions_df = None

    total_pnl = 0.0
    total_fees = 0.0
    wins = 0
    losses = 0

    if positions_df is not None and len(positions_df) > 0 and "realized_pnl" in positions_df.columns:
        for _, row in positions_df.iterrows():
            pnl = parse_pnl(row.get("realized_pnl", 0))
            comm = parse_commission(row.get("commissions", 0))
            total_pnl += pnl
            total_fees += comm
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1

    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    sharpe = result.stats_returns.get("sharpe_ratio", 0.0) if result.stats_returns else 0.0

    summary = generate_reports(result, reports_dir, trades=[], equity_curve=[])
    summary["total_pnl"] = round(total_pnl, 2)
    summary["total_fees"] = round(total_fees, 2)
    summary["total_trades"] = total_trades
    summary["win_rate"] = round(win_rate, 2)
    summary["sharpe_ratio"] = round(sharpe, 2)
    summary["win_count"] = wins
    summary["loss_count"] = losses

    print(f"  Positions:  {result.total_positions}")
    print(f"  Orders:     {result.total_orders}")
    print(f"  Trades:     {total_trades}")
    print(f"  Win Rate:   {win_rate:.1f}%")
    print(f"  Total PnL:  {total_pnl:.2f}")
    print(f"  Total Fees: {total_fees:.2f}")
    print(f"  Sharpe:     {sharpe:.2f}")

    engine.dispose()
    return summary


def main():
    repo_root = REPO_ROOT
    catalog_base = repo_root / "data" / "catalog"
    reports_base = repo_root / "reports" / "baseline_v4_1h_trend"
    reports_base.mkdir(parents=True, exist_ok=True)

    results = {}
    for label, start, end in WINDOWS:
        p15 = catalog_base / f"kraken_btcusd_15m_{label}"
        p5 = catalog_base / f"kraken_btcusd_{label}"
        if p15.exists():
            catalog_path = str(p15)
            print(f"Using 15m catalog for {label}")
        elif p5.exists():
            catalog_path = str(p5)
            print(f"  WARNING: 5m catalog for {label}")
        else:
            print(f"  ERROR: No catalog for {label}")
            continue
        summary = run_window(
            catalog_path=catalog_path, label=label, start=start, end=end,
            reports_dir=reports_base / label,
        )
        if summary:
            results[label] = summary

    # Print comparison table
    print(f"\n{'='*80}")
    print(f"V4 1h Trend-Following -- Multi-Window Results")
    print(f"{'='*80}")
    header = f"{'Window':<10} {'Trades':>6} {'Win%':>6} {'Gross':>10} {'Fees':>8} {'Net':>10} {'Sharpe':>8}"
    print(header)
    print("-" * 80)
    for label, _, _ in WINDOWS:
        s = results.get(label)
        if s:
            trades = s.get("total_trades", 0)
            pnl = s.get("total_pnl", 0.0)
            fees = s.get("total_fees", 0.0)
            net = pnl - fees
            sharpe = s.get("sharpe_ratio", 0.0)
            wr = s.get("win_rate", 0.0)
            print(f"{label:<10} {trades:>6} {wr:>6.1f} {pnl:>10.2f} {fees:>8.2f} {net:>10.2f} {sharpe:>8.2f}")
        else:
            print(f"{label:<10} {'N/A':>6} {'N/A':>6} {'N/A':>10} {'N/A':>8} {'N/A':>10} {'N/A':>8}")
    print("=" * 80)
    print("\nVerdict:")

    pnl_windows = [results.get(l, {}).get("total_pnl", 0) for l, _, _ in WINDOWS if l in results]
    net_windows = [results.get(l, {}).get("total_pnl", 0) - results.get(l, {}).get("total_fees", 0) for l, _, _ in WINDOWS if l in results]
    pos_gross = sum(1 for p in pnl_windows if p > 0)
    pos_net = sum(1 for n in net_windows if n > 0)
    total = len(pnl_windows)

    print(f"  Gross PnL positive in {pos_gross}/{total} windows")
    print(f"  Net PnL positive in {pos_net}/{total} windows")
    if pos_gross >= total // 2 + 1 and pos_net >= total // 2 + 1:
        print("  PROMISING -- gross and net positive in most windows")
    elif pos_gross >= total // 2 + 1:
        print("  MIXED -- gross positive but fees may erode edge")
    else:
        print("  REJECTED -- negative gross PnL in most windows")
        print("  => Stop BTC/USD spot bar-level technical research under this fee model.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
