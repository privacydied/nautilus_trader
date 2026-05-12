#!/usr/bin/env python3
"""
Run V3 mean reversion research backtest across four windows.

V3 hypothesis: BTC/USD may offer better expectancy when buying passive
pullbacks near range lows with maker-style entries, not chasing breakouts.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

# Ensure repo root is on sys.path
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

from examples.strategies.kraken_btcusd_research.config_v3 import (
    INSTRUMENT_ID,
    STARTING_BALANCE_USD,
    TIMEFRAME_BARS,
    MAKER_FEE,
    TAKER_FEE,
    MEAN_WINDOW,
    STD_WINDOW,
    RSI_PERIOD,
    RSI_OVERSOLD,
    ZSCORE_ENTRY,
    ZSCORE_EXIT,
    ATR_PERIOD,
    RANGE_WINDOW,
    MIN_RANGE_FEE_MULTIPLE,
    ENTRY_OFFSET_ATR,
    MAKER_ORDER_EXPIRY_BARS,
    COOLDOWN_BARS,
    INITIAL_STOP_ATR_MULTIPLIER,
    TRAILING_STOP_ATR_MULTIPLIER,
    TIME_STOP_BARS,
    MIN_POSITION_SIZE_BTC,
)
from examples.strategies.kraken_btcusd_research.strategy_v3 import (
    KrakenBTCUSDMeanReversionStrategy,
    KrakenBTCUSDMeanReversionConfig,
)
from examples.strategies.kraken_btcusd_research.reports import generate_reports

# Research windows: (label, start, end)
WINDOWS = [
    ("2024h1", "2024-01-01", "2024-06-01"),
    ("2024h2", "2024-07-01", "2025-01-01"),
    ("2025", "2025-01-01", "2026-01-01"),
    ("2026", "2026-01-01", "2026-05-01"),
]


def _make_instrument() -> CurrencyPair:
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


def run_window(
    catalog_path: str,
    label: str,
    start: str,
    end: str,
    reports_dir: Path,
    starting_balance: float = STARTING_BALANCE_USD,
) -> dict:
    """Run a single backtest window and return summary stats."""
    print(f"\n{'=' * 50}")
    print(f"V3 Research: {label} ({start} -> {end})")
    print(f"{'=' * 50}")

    catalog = ParquetDataCatalog(path=catalog_path)

    # Try to discover the correct bar type for 15m bars
    bars = catalog.bars(instrument_ids=[INSTRUMENT_ID])
    if not bars:
        print(f"  No bars found for {label}. Skipping.")
        return None

    bar_type = bars[0].bar_type
    iid = bar_type.instrument_id
    print(f"  Loaded {len(bars)} bars | BarType: {bar_type}")

    strategy_config = KrakenBTCUSDMeanReversionConfig(
        instrument_id=iid,
        bar_type=bar_type,
        mean_window=MEAN_WINDOW,
        std_window=STD_WINDOW,
        rsi_period=RSI_PERIOD,
        rsi_oversold=RSI_OVERSOLD,
        zscore_entry=ZSCORE_ENTRY,
        zscore_exit=ZSCORE_EXIT,
        atr_period=ATR_PERIOD,
        range_window=RANGE_WINDOW,
        min_range_fee_multiple=MIN_RANGE_FEE_MULTIPLE,
        entry_offset_atr=ENTRY_OFFSET_ATR,
        maker_order_expiry_bars=MAKER_ORDER_EXPIRY_BARS,
        cooldown_bars=COOLDOWN_BARS,
        initial_stop_atr_multiplier=INITIAL_STOP_ATR_MULTIPLIER,
        trailing_stop_atr_multiplier=TRAILING_STOP_ATR_MULTIPLIER,
        time_stop_bars=TIME_STOP_BARS,
        maker_fee=MAKER_FEE,
        taker_fee=TAKER_FEE,
        risk_percent=0.0025,
        max_notional_pct=0.30,
        min_position_size_btc=MIN_POSITION_SIZE_BTC,
    )
    strategy = KrakenBTCUSDMeanReversionStrategy(config=strategy_config)

    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id="KRAKEN-V3-BACKTEST",
            logging=LoggingConfig(log_level="ERROR", bypass_logging=True),
        )
    )
    engine.add_venue(
        venue=iid.venue, oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=[Money(starting_balance, USD)],
        base_currency=None,
    )
    engine.add_instrument(_make_instrument())
    engine.add_data(bars)
    engine.add_strategy(strategy)

    engine.run(start=start, end=end)
    result = engine.get_result()

    stats_pnls = result.stats_pnls.get("stats", {}) if result.stats_pnls else {}
    stats_returns = result.stats_returns if result.stats_returns else {}

    total_pnl = stats_pnls.get("total_pnl", 0.0)
    total_fees = stats_pnls.get("total_fees", 0.0)
    final_equity = stats_returns.get("final_equity", starting_balance)
    sharpe = stats_returns.get("sharpe_ratio", 0.0)

    # Generate reports from BacktestResult
    summary = generate_reports(
        result, reports_dir, trades=[], equity_curve=[]
    )

    print(f"  Positions:  {result.total_positions}")
    print(f"  Orders:     {result.total_orders}")
    print(f"  Total PnL:  {total_pnl}")
    print(f"  Total Fees: {total_fees}")
    print(f"  Sharpe:     {sharpe}")
    print(f"  Final Eq:   {final_equity}")

    engine.dispose()
    return summary


def main():
    """Run all four windows and produce a comparison table."""
    repo_root = REPO_ROOT
    catalog_base = repo_root / "data" / "catalog"

    # Resolve per-window catalogs (15m preferred, fall back to 5m)
    catalog_paths = {}
    for label, start, end in WINDOWS:
        p15 = catalog_base / f"kraken_btcusd_15m_{label}"
        p5 = catalog_base / f"kraken_btcusd_{label}"
        if p15.exists():
            catalog_paths[label] = str(p15)
        elif p5.exists():
            catalog_paths[label] = str(p5)
        else:
            print(f"ERROR: No catalog for {label}")
            return 1

    reports_base = repo_root / "reports" / "baseline_v3"
    reports_base.mkdir(parents=True, exist_ok=True)

    results = {}
    for label, start, end in WINDOWS:
        window_reports = reports_base / label
        summary = run_window(
            catalog_path=catalog_paths[label],
            label=label,
            start=start,
            end=end,
            reports_dir=window_reports,
        )
        if summary:
            results[label] = summary

    # Print comparison table
    print(f"\n{'=' * 70}")
    print(f"V3 Mean Reversion -- Multi-Window Results")
    print(f"{'=' * 70}")
    header = f"{'Window':<10} {'Trades':>6} {'PnL':>10} {'Fees':>8} {'Net':>10} {'Sharpe':>8}"
    print(header)
    print("-" * 70)
    for label, _, _ in WINDOWS:
        s = results.get(label)
        if s:
            trades = s.get("total_trades", 0)
            pnl = s.get("total_pnl", 0.0)
            fees = s.get("total_fees", 0.0)
            net = pnl - fees
            sharpe = s.get("sharpe_ratio", 0.0)
            print(f"{label:<10} {trades:>6} {pnl:>10.2f} {fees:>8.2f} {net:>10.2f} {sharpe:>8.2f}")
        else:
            print(f"{label:<10} {'N/A':>6} {'N/A':>10} {'N/A':>8} {'N/A':>10} {'N/A':>8}")
    print("=" * 70)
    print("\nVerdict:")
    pnl_windows = [results.get(l, {}).get("total_pnl", 0) for l, _, _ in WINDOWS if l in results]
    fees_windows = [results.get(l, {}).get("total_fees", 0) for l, _, _ in WINDOWS if l in results]
    net_windows = [p - f for p, f in zip(pnl_windows, fees_windows)]
    pos_gross = sum(1 for p in pnl_windows if p > 0)
    pos_net = sum(1 for n in net_windows if n > 0)
    total = len(pnl_windows)
    print(f"  Gross PnL positive in {pos_gross}/{total} windows")
    print(f"  Net PnL positive in {pos_net}/{total} windows")
    if pos_gross >= total // 2 + 1 and pos_net >= total // 2 + 1:
        print("  Status: PROMISING -- continues to warrant more research")
    elif pos_gross >= total // 2 + 1:
        print("  Status: MIXED -- gross positive but fees consuming edge")
    else:
        print("  Status: REJECTED -- negative expectancy")

    return 0


if __name__ == "__main__":
    sys.exit(main())
