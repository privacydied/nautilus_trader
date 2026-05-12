#!/usr/bin/env python3
"""V5: Multi-Asset Daily Momentum Portfolio — Research Runner."""

import sys
import json
from datetime import datetime, timezone
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

from examples.strategies.kraken_btcusd_research.reports import parse_pnl, parse_commission
from examples.strategies.kraken_v5_portfolio.config_v5 import (
    LIQUID_PAIRS, KRAKEN_VENUE,
    EMA_TREND_PERIOD, ATR_PERIOD,
    MAX_POSITIONS, MAX_NOTIONAL_PCT, TRAILING_STOP_ATR_MULT,
    STARTING_BALANCE_USD, TAKER_FEE, WINDOWS,
)
from examples.strategies.kraken_v5_portfolio.strategy_v5 import (
    KrakenV5PortfolioStrategy, KrakenV5PortfolioConfig,
)
from examples.strategies.kraken_v5_portfolio.instrument_details import PRICE_SIZE_PRECISION

# Map pairs from config (e.g. "BTC/USD") to catalog IDs (e.g. "BTC/USD.KRAKEN")
def to_catalog_id(pair: str) -> str:
    return f"{pair}.{KRAKEN_VENUE}"

def make_instrument(iid_str: str) -> CurrencyPair:
    iid = InstrumentId.from_str(iid_str)
    pp, sp = PRICE_SIZE_PRECISION.get(iid_str, (2, 8))
    sym = iid_str.split("/")[0].replace("BTC", "XBT")
    return CurrencyPair(
        instrument_id=iid, raw_symbol=Symbol(sym),
        base_currency=BTC, quote_currency=USD,
        price_precision=pp, size_precision=sp,
        price_increment=Price(10**-pp, pp),
        size_increment=Quantity(10**-sp, sp),
        multiplier=Quantity(1, 0),
        maker_fee=Decimal("0.0025"), taker_fee=Decimal("0.004"),
        margin_init=Decimal(0), margin_maint=Decimal(0),
        ts_event=0, ts_init=0,
    )

def run_window(cat, label, start, end, pairs=None):
    print(f"\n{'='*50}")
    print(f"V5 Portfolio: {label} ({start} -> {end})")
    print(f"{'='*50}")

    if pairs is None:
        pairs = LIQUID_PAIRS
    iids = [to_catalog_id(p) for p in pairs]

    start_ns = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1e9)
    end_ns   = int(datetime.strptime(end,   "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1e9)

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id="V5-PORTFOLIO",
        logging=LoggingConfig(bypass_logging=True),
    ))
    engine.add_venue(
        InstrumentId.from_str(iids[0]).venue,
        OmsType.NETTING, AccountType.CASH,
        starting_balances=[Money(STARTING_BALANCE_USD, USD)],
    )

    spec = BarSpecification(1, BarAggregation.DAY, PriceType.LAST)
    total_bars = 0
    for iid_str in iids:
        engine.add_instrument(make_instrument(iid_str))
        bars = cat.bars(instrument_ids=[iid_str])
        if not bars:
            print(f"  SKIP {iid_str}: no bars in catalog")
            continue
        filtered = [b for b in bars if start_ns <= b.ts_event < end_ns]
        if not filtered:
            print(f"  SKIP {iid_str}: no bars in window")
            continue
        engine.add_data(filtered)
        total_bars += len(filtered)
        print(f"  {iid_str}: {len(filtered)} bars")

    if total_bars == 0:
        print("  No data loaded for this window")
        engine.dispose()
        return None

    bt = BarType(InstrumentId.from_str(iids[0]), spec)
    cfg = KrakenV5PortfolioConfig(
        universe=iids, bar_type=bt,
        ema_trend_period=EMA_TREND_PERIOD, atr_period=ATR_PERIOD,
        max_positions=MAX_POSITIONS, max_notional_pct=MAX_NOTIONAL_PCT,
        trailing_stop_atr_mult=TRAILING_STOP_ATR_MULT, taker_fee=TAKER_FEE,
    )
    engine.add_strategy(KrakenV5PortfolioStrategy(config=cfg))
    engine.run(start=start, end=end)
    result = engine.get_result()

    # Extract from position report (reliable source)
    total_pnl = 0.0
    total_fees = 0.0
    wins = losses = 0
    asset_pnl = {}

    try:
        pos_df = engine.trader.generate_positions_report()
    except Exception:
        pos_df = None

    if pos_df is not None and len(pos_df) > 0 and "realized_pnl" in pos_df.columns:
        for _, row in pos_df.iterrows():
            pnl = parse_pnl(row.get("realized_pnl", 0))
            comm = parse_commission(row.get("commissions", 0))
            total_pnl += pnl
            total_fees += comm
            inst = str(row.get("instrument_id", ""))
            asset_pnl[inst] = asset_pnl.get(inst, 0.0) + pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1

    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    net_pnl = total_pnl - total_fees

    print(f"  Trades:   {total_trades}")
    print(f"  Win Rate: {win_rate:.1f}%")
    print(f"  Gross:    {total_pnl:.2f}")
    print(f"  Fees:     {total_fees:.2f}")
    print(f"  Net:      {net_pnl:.2f}")

    engine.dispose()
    return {
        "trades": total_trades, "win_rate": win_rate,
        "gross_pnl": round(total_pnl, 2),
        "fees": round(total_fees, 2),
        "net_pnl": round(net_pnl, 2),
        "wins": wins, "losses": losses,
        "pnl_by_asset": {k: round(v, 2) for k, v in asset_pnl.items()},
    }

def main():
    catalog_path = REPO_ROOT / "data" / "catalog" / "kraken_daily_universe"
    if not catalog_path.exists():
        print(f"ERROR: catalog not found: {catalog_path}")
        sys.exit(1)

    reports_base = REPO_ROOT / "reports" / "baseline_v5_daily_momentum"
    reports_base.mkdir(parents=True, exist_ok=True)

    cat = ParquetDataCatalog(path=str(catalog_path))
    results = {}
    for label, start, end in WINDOWS:
        r = run_window(cat, label, start, end)
        if r is not None:
            results[label] = r
            # Save per-window report
            (reports_base / label).mkdir(parents=True, exist_ok=True)
            with open(reports_base / label / "summary.json", "w") as f:
                json.dump(r, f, indent=2)

    print(f"\n{'='*80}")
    print("V5 Multi-Asset Daily Momentum — Multi-Window Results")
    print(f"{'='*80}")
    hdr = f"{'Window':<10} {'Trades':>6} {'Win%':>6} {'Gross':>10} {'Fees':>8} {'Net':>10}"
    print(hdr)
    print("-" * 80)
    for label, _, _ in WINDOWS:
        s = results.get(label)
        if s:
            print(f"{label:<10} {s['trades']:>6} {s['win_rate']:>5.1f} {s['gross_pnl']:>10.2f} {s['fees']:>8.2f} {s['net_pnl']:>10.2f}")
        else:
            print(f"{label:<10} {'SKIP':>6} {'N/A':>6} {'N/A':>10} {'N/A':>8} {'N/A':>10}")
    print("=" * 80)

    asset_totals = {}
    for s in results.values():
        for a, pnl in s.get("pnl_by_asset", {}).items():
            asset_totals[a] = asset_totals.get(a, 0.0) + pnl

    gross_total = sum(s["gross_pnl"] for s in results.values())
    fees_total  = sum(s["fees"]     for s in results.values())
    net_total   = round(gross_total - fees_total, 2)
    gross_pos   = sum(1 for s in results.values() if s["gross_pnl"] > 0)
    net_pos     = sum(1 for s in results.values() if s["net_pnl"]   > 0)
    n = len(results)

    print(f"\nNet PnL by asset (all windows):")
    for a in sorted(asset_totals, key=asset_totals.__getitem__, reverse=True):
        print(f"  {a:<20} {asset_totals[a]:>10.2f}")

    print(f"\nTotals:  Gross={gross_total:.2f}  Fees={fees_total:.2f}  Net={net_total}")
    print(f"  Gross positive in {gross_pos}/{n} windows")
    print(f"  Net positive in {net_pos}/{n} windows")

    if gross_pos >= 3 and net_pos >= 2 and gross_total > 0 and net_total > 0:
        print("\nPROMISING — continue research")
        verdict = "PROMISING"
    elif gross_total > 0:
        print("\nMIXED — gross positive overall but fees erode edge")
        verdict = "MIXED"
    else:
        print("\nREJECTED — insufficient gross edge")
        verdict = "REJECTED"

    agg = {
        "total_gross_pnl": round(gross_total, 2),
        "total_fees": round(fees_total, 2),
        "total_net_pnl": net_total,
        "windows_with_data": n,
        "gross_positive_windows": gross_pos,
        "net_positive_windows": net_pos,
        "pnl_by_asset": {k: round(v, 2) for k, v in asset_totals.items()},
        "verdict": verdict,
    }
    with open(reports_base / "aggregate_summary.json", "w") as f:
        json.dump(agg, f, indent=2)
    print(f"\nReports saved to {reports_base}/")

if __name__ == "__main__":
    main()
