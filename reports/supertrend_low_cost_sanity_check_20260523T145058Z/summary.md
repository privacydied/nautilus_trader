# Supertrend low-cost sanity check

status: SUPERtrend_LOW_COST_SANITY_CHECK_READY
safety: public_data_observer_only

This diagnostic does not reopen the locked Kraken BTC/USD OHLCV indicator rejection.
This is not CANDIDATE, not REJECTED, not EXECUTION_READY, not TRADE_READY.

## Scope
Fixed TradingView-style Supertrend only: ATR period 10, hl2 source, multiplier 3.0, Wilder RMA ATR. No grid search, no tuning, no optimizer, no null test, no holdout promotion.

## Data
data source path/catalog used: /mnt/nasirjones/py/nautilus_trader_stage2_runtime/data/lead_lag/kraken_btc_usd_1m_20260505_20260512.csv
timeframe(s) actually available/used: 1m
date range: 2026-05-12T07:05:00Z to 2026-05-12T19:05:00Z
bar count: 723

## Low-cost taker result
trade count: 20
win rate: 0.1000
gross PnL: -37.9869 bps
total fees: 180.0000 bps
net PnL: -217.9869 bps
mean gross trade bps: -1.8993
mean net trade bps: -10.8993
median gross trade bps: -5.0137
median net trade bps: -14.0137
best trade bps: 37.3772
worst trade bps: -28.3202
max drawdown: -255.3640 bps
turnover / number of flips: 42

## Comparisons
zero-cost gross net PnL field: -37.9869 bps
old Kraken-like round trip cost: 80.0000 bps
old Kraken-like net PnL: -1637.9869 bps
Low-cost net mean trade -10.90 bps versus the 9.00 bps round-trip cost wall; gross mean trade -1.90 bps.

recommendation: Do not proceed
