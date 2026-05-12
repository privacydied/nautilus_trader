# Rejection Report: Lead-Lag Cross-Venue Signal (Binance→Kraken)

**Date:** 2026-05-12
**Observer:** venue_agnostic_signal_observer
**Pipeline:** run_lead_lag.py (real-data sweep)
**Status:** REJECTED

---

## Hypothesis

> When Binance (source venue) moves sharply in BTC/ETH/SOL, Kraken (target venue) follows with enough latency for a cross-venue taker strategy to capture positive expected value after fees.

## Experiment

| Parameter | Value |
|---|---|
| Source venue | Binance (BTC/USDT, ETH/USDT, SOL/USDT) |
| Target venue | Kraken (BTC/USD, ETH/USD, SOL/USD) |
| Data window | 2026-05-12 07:05 to 2026-05-12 19:05 UTC (~12h overlap) |
| Candle interval | 1-minute bars |
| Alignment grid | 60-second forward-fill |
| Shared bars | BTC: 718, ETH: 720, SOL: 720 |
| Lookback windows | 120s, 300s, 600s |
| Move thresholds | 5, 10, 20 bps |
| Cooldown | 120s |
| Horizons | 60s, 300s, 600s, 3600s |
| Fee model | 10 bps taker + 2 bps slippage = 12 bps total |

## Results Summary

| Pair | Total Signals | Best Mean Net | Best Win Rate | Verdict |
|---|---|---|---|---|
| BTC: BIN→KRK | 1,127 | -12.09 bps | 17.3% | REJECTED |
| ETH: BIN→KRK | 1,430 | -12.24 bps | 23.9% | REJECTED |
| SOL: BIN→KRK | 1,783 | -12.58 bps | 24.8% | REJECTED |

### Parameter Sweep

All 9 lookback×threshold combinations per asset were tested.
Zero out of 27 configurations passed the acceptance gate.

Worse signals fired at higher thresholds (fewer events, same negative expectancy),
consistent with fees eating every trade regardless of how large the move was.

### Random Baseline

| Pair | Baseline Mean Net | Baseline Win Rate |
|---|---|---|
| BTC: BIN→KRK | -11.67 bps | 14.7% |
| ETH: BIN→KRK | -11.58 bps | 19.1% |
| SOL: BIN→KRK | -11.99 bps | 22.9% |

The random baseline (same number of events, random timestamps, random direction)
produces mean net returns nearly identical to the signal.  The lead-lag signals
do **not** meaningfully outperform random chance at this timescale.

## Observations

1. **Mean net ≈ -cost**: Across all configurations, mean net returns hover
   around -12 bps, which is exactly the total cost (10 fee + 2 slippage).
   This is the signature of raw returns near zero being eaten by fees.

2. **Median = -12 bps** for many configs: This means the majority of events
   produced near-zero raw returns, so after fee deduction they sit at -12.

3. **Win rate < fee breakeven**: At 12 bps cost, you need ~50%+ win rate
   with favorable average wins to be profitable.  Observed win rates are
   12-25%, far below breakeven.

4. **Higher thresholds reduce event count but don't improve edge**: This
   suggests the moves themselves are not predictive — just noisy.

5. **1-minute resolution**: As anticipated, 1m candles are too coarse to
   capture sub-minute lead-lag.  Any real edge at this timescale would be
   diluted or invisible at this resolution.

## Acceptance Gate

| Gate | Result |
|---|---|
| Mean net return positive after fees | FAIL (all negative) |
| Median not deeply negative | FAIL (median ≈ -12) |
| Event count meaningful | PASS (3,340 total) |
| Result across >1 window/asset | N/A (no positive any) |
| Not one-event driven | N/A (no positive any) |

## Verdict

**REJECTED.** Binance→Kraken lead-lag at 1-minute resolution shows no
predictive edge. Results are indistinguishable from random events at the
same frequency, consistent with efficient price discovery between these
two major venues at this timescale.

## What Would Change This Verdict

1. **Finer resolution**: 1-second bars or tick data may reveal sub-minute
   lead-lag that 1m candles smooth over entirely.
2. **Different venue pairs**: Less liquid pairs (e.g., Coinbase→smaller venue)
   may have wider latency gaps.
3. **Different signal**: Threshold-based moves may not be the right trigger.
   Order book imbalance, trade flow, or volume-spike signals could differ.
4. **Longer window**: 12 hours of overlap is thin. A full 7+ day Kraken
   window (requires paid endpoint) would give more statistical power.

## Artifacts

- Reports: `reports/lead_lag_v1/`
- Summary: `reports/lead_lag_v1/summary.json`
- Per-pair: `reports/lead_lag_v1/pair_binance_{btc,eth,sol}_usd.json`
- Data: `data/lead_lag/`
