# Research Log — Nautilus Trader Kraken Scaffold

## V1–V5: OHLCV Indicator Strategies — REJECTED

All OHLCV-derived technical indicator strategies (moving averages, RSI, Bollinger Bands, MACD, volatility breakouts) failed to produce statistically significant edges on BTC/USD and ETH/USD Kraken spot data under conservative backtesting.

## V6-A: Cross-Venue Spot Spread Scanner — REJECTED

- **Scope**: Kraken + Coinbase (later Binance) spot for BTC/USD, ETH/USD
- **Method**: Poll public REST tickers, compute gross cross-venue spread, subtract taker fees + latency buffer
- **Results**: Gross spread ~3 bps, net after fees/buffers ~-56 bps
- **Profitable observations**: 0 / 8
- **Conclusion**: No observable cross-venue spread edge at taker fees

## V6-B: Funding/Basis Observer (BTC/ETH) — REJECTED

- **Scope**: Kraken spot USD + Kraken/Binance/Bybit perps for BTC, ETH
- **Method**: Poll funding rates, compute basis bps, annualize funding APR, compute net edge after round-trip taker fees + buffers
- **Observations**: 36 across 120 seconds
- **Candidates**: 0
- **Issue**: USD/USDT quote mismatch filter caught all Binance/Bybit perp observations; Kraken Futures mapping had stale data
- **Conclusion**: Funding income far below conservative costs for BTC/ETH in normal conditions

## V6-C: Altcoin Funding/Basis Anomaly Monitor — REJECTED

- **Scope**: Kraken spot USD + Binance/Bybit USDT perps for SOL, XRP, DOGE, LINK, AVAX, ADA
- **Method**: 600-second scan, 10-second poll interval, 3 cost scenarios (conservative_taker, mixed_maker_taker, optimistic_maker), persistence tracking (min 3 consecutive polls)
- **Observations**: 468 (39 polls x 6 assets x 2 perp venues)
- **Candidates**: 0
- **Durable candidates**: 0
- **Max funding APR**: 10.95% (DOGE, AVAX at frate=0.0001)
- **Best conservative net edge**: -154 bps (DOGE/Binance)
- **Best optimistic net edge**: -67.5 bps
- **Rejection reasons**: 273 USD/USDT mismatch, 195 negative funding rate
- **Conclusion**: Altcoin funding rates are an order of magnitude too small to cover round-trip costs. Even removing USD/USDT mismatch, net edge is -134 bps. The gap is structural.

### V6 Summary Table

| Phase | Thesis | Assets | Observations | Candidates | Best Net Edge | Verdict |
|-------|--------|--------|-------------|------------|---------------|---------|
| V1-V5 | OHLCV technical edge | BTC, ETH | N/A | 0 | N/A | REJECTED |
| V6-A | Cross-venue spread | BTC, ETH | 8 | 0 | -56 bps | REJECTED |
| V6-B | Funding/basis cash-and-carry | BTC, ETH | 36 | 0 | < -100 bps | REJECTED |
| V6-C | Altcoin funding anomalies | SOL, XRP, DOGE, LINK, AVAX, ADA | 468 | 0 | -154 bps | REJECTED |

### Aggregate Conclusion

Public REST spread/funding opportunities do not survive conservative taker-fee + buffer assumptions across:
- BTC/ETH (mainstream, normal conditions)
- Altcoins (SOL, XRP, DOGE, LINK, AVAX, ADA)
- Spot + perpetuals on Kraken, Binance, Bybit
- All three cost scenarios (conservative taker, mixed maker/taker, optimistic maker)

The funding/basis cash-and-carry thesis is rejected for these venues, assets, and fee assumptions. A 120-second scan is too short to rule out dislocation events, but it is sufficient to reject the base case.

### What Was NOT Tested

- Longer windows during market dislocations (flash crashes, exchange outages)
- Order-book depth and maker queue position
- Maker-fill microstructure
- Different fee tiers (VIP/MTI)
- Different venue classes (DEX, options, calendar spreads)
- Non-Kraken/Binance/Bybit venues

## V7: L2 Maker Microstructure Paper Simulator — REJECTED

- **Scope**: Kraken public WebSocket v2, BTC/USD and ETH/USD order book + trades
- **Method**: Subscribe to live L2 book, place paper post-only maker quotes at the touch, model fills pessimistically (book must cross price for pessimistic, touch for neutral), track adverse selection, compute PnL after maker fees + fill penalty
- **Data source**: Kraken WS v2 public endpoint, no private keys, no orders
- **Fill assumptions**: Same-tick fills disallowed. Conservative fee model: maker fee 3.0 bps + fill penalty 2.0 bps = 5.0 bps per round-trip fill
- **Quote lifetime**: 5 seconds, cancel on mid-move, spread collapse, imbalance flip
- **Duration**: 120 seconds per model (pessimistic + neutral)

### Results

| Model | Book Updates | Quotes Placed | Fills | Avg Spread (bps) | Gross PnL (bps) | Fees (bps) | Net PnL (bps) |
|-------|-------------|---------------|-------|---------------------|-----------------|------------|---------------|
| Pessimistic | 15,283 | 28,364 | 0 | 0.2020 | 0.0000 | 0.0000 | 0.0000 |
| Neutral | 14,564 | 26,622 | 3 | 0.1770 | 0.7286 | 15.0000 | -14.2714 |

### Conclusion

Average Kraken BTC/USD spread: ~0.20 bps. Average half-spread captured: ~0.10 bps.
Maker fee + fill penalty cost: ~5.0 bps per fill. The spread is 25-50x smaller than the cost.
No queue priority assumption, no model tweak, no longer runtime can bridge this gap.
This is a **structural fee/spread impossibility** at this venue and fee tier.

---

## Final Verdict

**Kraken trading research is stopped under the current fee/account setup.**

| Phase | Thesis | Result | Verdict |
|-------|--------|--------|---------|
| V1-V5 | OHLCV indicator strategies | No statistically significant edge | REJECTED |
| V6-A | Cross-venue spot spread | 0/8 profitable, net -56 bps | REJECTED |
| V6-B | BTC/ETH funding/basis | 0/36 candidates | REJECTED |
| V6-C | Altcoin funding anomalies | 0/468 candidates, max APR 10.95% | REJECTED |
| V7 | L2 maker microstructure | Spread 0.20 bps << cost 5.0 bps | REJECTED |

**Reason:**
No tested strategy class produced a realistic positive edge after accounting for fees, spreads, buffers, and execution assumptions. V7 confirms that Kraken BTC/USD and ETH/USD top-of-book spreads are far smaller than the maker fee + penalty cost, making microstructure market making structurally uneconomic at this fee tier.

**Action:**
No V8. No live trading. Reuse the scaffold only if the venue, fee tier, instrument class, or capital assumptions change materially.

**Remaining paths (not V8):**
1. Lower-fee venue / rebate venue — needed for market making
2. Different instrument class — options, calendar spreads, prediction markets
3. VIP fee tier — only if expected edge is close (it is not on Kraken BTC/ETH)
4. Non-trading monetizable project — most likely to produce money

---

---

*Log updated May 12, 2026. Tag: kraken-trading-research-rejected-2026-05-12.*
