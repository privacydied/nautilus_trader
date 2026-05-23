# Kraken BTC/USD Research Scaffold

A minimal, working research scaffold for backtesting and live trading of BTC/USD on Kraken spot market using Nautilus Trader.

## What This Is

- A clean, surgical implementation of a long-only BTC/USD trend following strategy
- Complete data pipeline: download → import → backtest → reports
- Conservative risk management with position sizing
- Guarded live trading (disabled by default)
- Fee modeling (taker fee = 0.40%, maker fee = 0.25%)

## What This Is NOT

- ❌ Not a profitable strategy guarantee
- ❌ Not a production trading system
- ❌ Not a dashboard or monitoring solution
- ❌ Not a multi-exchange framework
- ❌ Not a machine learning system
- ❌ Not a fully featured backtesting engine with walk-forward optimization

## Strategy Logic

**Entry:**
- Fast EMA (20) > Slow EMA (100) - bullish regime filter
- Price breaks above prior 55-bar Donchian high
- ATR(20) available and > 0
- No open position
- 12-bar cooldown after exits respected

**Exit:**
- Price crosses below Slow EMA (100)
- ATR-based stop loss (2.5 × ATR)
- ATR-based trailing stop (3.0 × ATR)

**Position Sizing:**
- Risk per trade: 0.25% of account equity
- Position size = min(risk_amount / stop_distance, max 30% of equity)
- Rounds down to instrument precision
- Skipped if below minimum size

**Fees:** Conservative taker fee (0.40%) modeled on all trades.

## Quick Start

### 1. Download Historical Data
```bash
python examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py \
  --pair BTC/USD --interval 1 --since 2024-01-01 --out data/kraken/BTCUSD_1m.csv
```

### 2. Import to Nautilus Catalog
```bash
python examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py \
  --csv data/kraken/BTCUSD_1m.csv --catalog data/catalog/kraken_btcusd
```

### 3. Run Backtest
```bash
python examples/strategies/kraken_btcusd_research/run_backtest.py \
  --catalog data/catalog/kraken_btcusd \
  --start 2024-01-01 \
  --end 2024-06-01 \
  --starting-balance 10000
```

## Live Mode (Guarded)

Live trading is **disabled by default**. To enable:

1. Set environment variables:
```bash
export KRAKEN_SPOT_API_KEY="your_key"
export KRAKEN_SPOT_API_SECRET="your_secret"
export I_UNDERSTAND_THIS_CAN_LOSE_MONEY="yes"
```

2. Run with `--live` flag:
```bash
python examples/strategies/kraken_btcusd_research/run_live_kraken_guarded.py --live
```

**Safety Features:**
- Requires explicit `--live` flag
- Requires understanding acknowledgment env var
- Requires valid Kraken API keys
- Max notional limit ($100 default)
- Max daily loss limit ($100 default)
- Stops after first order unless `--allow-multiple-orders` specified
- No leverage, no shorting, spot only

## Files

- `config.py` - Configuration parameters and defaults
- `strategy.py` - EMA crossover strategy with risk management
- `download_kraken_ohlcv.py` - Public Kraken OHLCV downloader
- `import_kraken_ohlcv_to_catalog.py` - CSV to ParquetDataCatalog importer
- `run_backtest.py` - Backtest runner with reporting
- `run_live_kraken_guarded.py` - Guarded live runner
- `reports.py` - Report generation utilities
- `tests/test_kraken_btcusd_research.py` - Unit tests

## Next Steps for Research

- Compare 1-minute vs 5-minute bars
- Test maker-only entries with passive orders
- Implement walk-forward optimization
- Add spread/slippage assumptions
- Test different regime filters
- Add correlation filters
- Test out-of-sample periods (2025/2026)

## Disclaimer

**This is for research purposes only. Trading carries risk of loss. Past performance is not indicative of future results. Use at your own risk.**