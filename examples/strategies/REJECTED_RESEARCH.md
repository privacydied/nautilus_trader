# Rejected Research Registry

> **This document records studies that have been rejected or frozen.**
> New research may revisit the same idea only with a materially different mechanism, venue, timeframe, or cost model. Do not re-run the same hypothesis without changing something structural.

---

## Status Table

| Study | Symbol(s) | Venues | Signal Family | Verdict | Date | Tag |
|---|---|---|---|---|---|---|
| V1 BTC/USD 5m Donchian/EMA breakout | BTC/USD | Kraken | Bar-level indicator | REJECTED | 2024 | — |
| V2 BTC/USD selective EMA/Donchian | BTC/USD | Kraken | Bar-level indicator | REJECTED | 2024 | — |
| V3 BTC/USD mean reversion | BTC/USD | Kraken | Bar-level indicator | REJECTED | 2024 | — |
| V4 BTC/USD 1h trend-following | BTC/USD | Kraken | Bar-level indicator | REJECTED/FINAL | 2025-05 | — |
| V5 daily multi-asset momentum | Multiple | Kraken | Bar-level indicator | UNKNOWN_NEEDS_REPORT_MINING | — | — |
| V6 cross-venue spread scanner | BTC/USD, ETH/USD | Kraken, Coinbase, Binance | REST polling spread | REJECTED | 2026-05-13 | — |
| V6-B funding/basis scanner | BTC, ETH | Kraken spot + Binance/Bybit perps | Cash-and-carry | REJECTED (net edge < cost) | 2026-05-13 | — |
| V6-C altcoin funding monitor | SOL, XRP, DOGE, etc. | Binance, Bybit perps | Funding anomalies | REJECTED (no durable candidates) | 2026-05-13 | — |
| V7 L2 maker paper BTC/ETH | BTC/USD, ETH/USD | Kraken | Market making paper sim | REJECTED (spread too small vs fee) | 2026-05-13 | — |
| OHLCV lead-lag v1 (1m bars) | BTC, ETH, SOL | Binance→Kraken | Bar-level lead-lag | REJECTED | 2026-05-12 | — |
| Tick lead-lag v3 (600s) | BTC-USD, ETH-USD | Coinbase↔Kraken | Tick-level lead-lag | REJECTED | 2026-05-13 | `lead-lag-v3-coinbase-kraken-rejected` |
| Trade-flow impulse v1 (4 types, 600s) | BTC-USD, ETH-USD | Coinbase↔Kraken | Trade flow burst | REJECTED | 2026-05-13 | `trade-flow-impulse-v1-rejected` |

## Locked Gates

1. **Kraken BTC/USD spot OHLCV indicators** (5m, 1h, Donchian, EMA, ATR). ~80 bps round-trip taker fees. Rejected V1-V4. Stop.
2. **Same-asset same-quote cross-venue tick lead-lag** (CB↔KRK BTC/ETH, ~600s captures). HFT-dominated, sub-second decay. Rejected.
3. **Naive top-of-book L2 maker** on BTC/ETH at current fee tier. Spread too small, fills too toxic. Rejected.
4. **Direct cash-and-carry** under Kraken USD spot + Binance/Bybit USDT perp cost model. Net edge below all-in cost. Rejected.

## Still Open

- Derivatives flow impulse → spot lead-lag
- OI + price regime classification (filter, not trade)
- Funding as crowding/sentiment feature (not carry)
- L2 adverse selection conditioning
- Options IV/RV regime overlay (filter)

## Known Issues

- config.py Final import: fixed 2026-05-13
- uv version mismatch (0.11.8 pinned vs 0.11.13 runtime)
