# BTC/USD Kraken Spot Technical Research Log

## Final Conclusion

Do not continue BTC/USD Kraken spot OHLCV indicator variants under this fee model.
This is a hard stop, not a soft suggestion.

Rejected families:
- V1: 5m Donchian/EMA breakout
- V2: selective breakout
- V3: z-score mean reversion
- V4: 1h trend-following

Shared result:
No tested family produced robust positive gross edge across windows.

Requires new hypothesis: venue, instrument universe, execution model, or data type must change.

---

## V4 1h Trend-Following -- Final Reject (2026-05-12)

### Hypothesis
> Maybe V1-V3 failed because 5m/15m signals are noise. V4 tests whether 1h trend moves are large enough to produce positive gross edge before fees.

### Bugs Fixed During V4
1. **Donchian breakout impossible** - Entry checked `close > self.donchian.upper` AFTER `donchian.update_raw(high, low)` had already included the current bar's high. Since `close <= high` by OHLC geometry, the breakout could never fire. Fix: capture `prior_donchian_high` BEFORE calling `update_raw()`.
2. **ATR expansion filter silently inert** - `ATR.update_raw()` returns `None`, not the calculated value. The strategy checked `if atr_val is not None:` and never populated `_atr_history`, so `_atr_median()` always returned 0.0 and the expansion filter was a no-op. Fix: read `self.atr.value` with `self.atr.initialized` after calling `update_raw()`.
3. **Fee extraction** - Nautilus now keys `stats_pnls` by currency (`'USD': {'PnL (total)': ...}`) instead of `{'stats': ...}`. Commissions column contains list objects like `['3.58 USD']`. Both the reports parser and `run_v4_research.py` extraction were updated.

### V4 Multi-Window Results

| Window | Trades | Win% | Gross PnL | Fees | Net PnL |
|--------|--------|------|-----------|------|---------|
| 2024h1 | 14 | 35.7% | +120.51 | 105.15 | +15.36 |
| 2024h2 | 17 | 52.9% | +48.71 | 113.86 | -65.15 |
| 2025 | 30 | 23.3% | -274.21 | 281.24 | -555.45 |
| 2026 | 9 | 11.1% | -118.95 | 58.41 | -177.36 |
| **Total** | **70** | **30%** | **-$223.94** | **$558.66** | **-$782.60** |

Starting balance: $10,000. Period: 2024-01-01 to 2026-05-01.

### Key Interpretation

Because gross PnL is negative overall (-$223.94), this is **not primarily a fee problem**. Fees worsen it, but even with zero fees the strategy still loses across the full test. That means maker execution alone should not be treated as the obvious next fix. Maker execution can reduce drag, but it does not create signal edge unless the fill model materially changes trade selection.

### Strategy Rules Implemented
- Long-only, no leverage, no margin, no pyramiding
- EMA(50) > EMA(200) trend filter
- Close breaks above prior Donchian(100) high
- ATR(20) must be initialized and > 0
- ATR expansion: current ATR > rolling median of last 100 ATR values
- Exit: ATR trailing stop (4x ATR) or close below EMA(100)
- 0.25% equity risk per trade, 30% max notional
- Taker entry/exit at 0.40% fee

### Acceptance Criteria vs Reality
- Gross positive in 2/4 windows: BARELY MET (2024h1 +$120, 2024h2 +$49; both weak)
- Trade count in 10-80 range: PASSED (70 total, 9-30 per window)
- No single trade explains result: PASSED
- Fees do not erase edge: FAILED (fees = 249% of gross PnL)
- Gross PnL positive overall: FAILED (-$223.94 total)

### Verdict: REJECTED

No timeframe fishing to 2h/3h/4h/6h/12h.

### Future Work

A maker-first strategy is only worth testing if it changes the signal/execution logic materially, not merely as a fee patch. Since V4 total gross PnL is negative, lower fees alone are not enough.

Structurally different research directions:
1. Order-book / microstructure signals
2. Cross-exchange spread or latency-aware arbitrage
3. Funding/basis strategies (if using derivatives elsewhere)
4. Different instrument with better volatility-to-fee profile
5. Different venue/fee tier
6. Longer-horizon portfolio/trend system across multiple assets, not single BTC/USD spot

### Next Steps
- [x] V4 rejection logged
- [ ] Commit V4 rejection and research log
- [ ] Archive V4 reports under reports/baseline_v4_1h_trend/
- [ ] Tag commit as final bar-level BTC/USD spot reject
- [ ] Stop adding variants to this family
- [ ] Start a new hypothesis only if it changes venue, instrument, data type, or strategy class

The scaffold prevented live deployment of four losing ideas. That is the point of the research system.

---

## V5 Daily Multi-Asset Momentum — Final Reject (2026-05-12)

### Hypothesis
> A low-turnover multi-asset crypto spot momentum system may outperform single BTC/USD because it can rotate into the strongest liquid assets and avoid dead/choppy regimes.

### What Changed from V4
- 10 liquid Kraken spot USD pairs instead of BTC/USD alone
- Daily bars (1-day) instead of 1h (2024h2–2026 windows)
- Portfolio holds top 2–3 assets by momentum
- EMA(200) trend filter per asset
- Trailing ATR stop, weekly rebalance

### Data Coverage
Daily data from Kraken public API covers 2024-05-22 to 2026-05-01 (710 bars per asset).
- 2024h2: 184 bars per asset (Jul–Dec 2024). EMA(200) warmup blocks all trading.
- 2025: 365 bars per asset. Only window with enough data for trading.
- 2026: 120 bars per asset. EMA(200) warmup blocks all trading.

### V5 Multi-Window Results

| Window | Trades | Win% | Gross PnL | Fees | Net PnL |
|--------|--------|------|-----------|------|---------|
| 2024h2 | 0 | 0.0% | $0.00 | $0.00 | $0.00 |
| 2025 | 21 | 14.3% | -$4,837.21 | $746.54 | -$5,583.75 |
| 2026 | 0 | 0.0% | $0.00 | $0.00 | $0.00 |

### Net PnL by Asset
- SOL/USD:   -$240.87
- LTC/USD:   -$394.75
- BTC/USD:  -$1,239.01
- ETH/USD:  -$1,376.69
- BCH/USD:  -$1,585.89

Only 5 of 10 assets held any positions in the 2025 window.

### Verdict: REJECTED

V5 gross PnL is deeply negative in the only tradable window (-$4,837). Win rate 14.3% with 21 trades.
Gross positive in 0/3 windows. Net positive in 0/3 windows.

Multi-asset rotation did not rescue the strategy. If anything, the broader universe diluted already weak signals — the portfolio rotated into losing assets and bled across five instruments.

### Acceptance Criteria vs Reality
- Gross PnL positive overall: FAILED (-$4,837 in 2025)
- Gross PnL positive in 3/4 windows: FAILED (0/3 had data)
- Not dominated by one asset: PASSED (losses spread across 5)
- Positive win rate: FAILED (14.3%)
- Fees do not dominate: N/A (gross already deeply negative)

---

## All OHLCV Indicator Research — Final Stop

Five families tested across Kraken spot USD pairs under 0.40% taker fees:

| Family | Timeframe | Assets | Gross PnL (best window) | Status |
|--------|-----------|--------|------------------------|--------|
| V1: Donchian breakout | 5m/15m | 1 (BTC) | -$X in all windows | REJECTED |
| V2: Selective breakout | 5m/15m | 1 (BTC) | -$X in all windows | REJECTED |
| V3: Z-score mean rev | 5m/15m | 1 (BTC) | -$X in all windows | REJECTED |
| V4: 1h trend-following | 1h | 1 (BTC) | +$120 in 2/4 windows | REJECTED |
| V5: Daily momentum portfolio | 1d | 10 | -$4,837 in 1 tradable window | REJECTED |

Shared result:
No tested OHLCV indicator family produced robust positive gross edge across windows.

Conclusion:
Stop all Kraken spot OHLCV indicator research under this fee model.
The next viable path requires a fundamentally different approach:
1. Order-book / microstructure signals
2. Cross-exchange spread or latency-aware arbitrage
3. Funding/basis strategies (derivatives)
4. Different venue with lower fees
5. Different data type

No more BTC/USD single-asset EMA/Donchian/z-score/timeframe variants.
No live trading.
