# BTC/USD Kraken Spot Technical Research Log

## Final Conclusion

Stop BTC/USD Kraken spot bar-level technical-indicator research under this fee model.

Rejected families:
- V1: 5m Donchian/EMA breakout
- V2: selective breakout
- V3: z-score mean reversion
- V4: 1h trend-following

Shared result:
No tested family produced robust positive gross edge across windows.

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
