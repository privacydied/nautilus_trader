# Rejected Research Registry

> **This document records studies that have been rejected or frozen.**
> New research may revisit the same idea only with a materially different mechanism, venue, timeframe, or cost model. Do not re-run the same hypothesis without changing something structural.

---

## Mined Status

The full mined status table is at [reports/research_status_table.csv](../reports/research_status_table.csv) with 45 study groups:
- **35 REJECTED**
- **9 NEEDS_MORE_DATA** (insufficient events / zero signals)
- **1 UNKNOWN**

## Status Table

| Study | Signal Family | Venue(s) | Verdict | Key Result | Tag |
|---|---|---|---|---|---|
| V1-V4 Kraken BTC/USD OHLCV | Bar-level indicators (EMA, Donchian, ATR) | Kraken spot | REJECTED | ~80 bps round-trip fees | — |
| V6 cross-venue spread scanner | REST polling spread (2s interval) | Kraken, Coinbase, Binance | REJECTED | No net edge after fees | — |
| V6-B funding/basis scanner | Cash-and-carry spot vs perp | Kraken spot + Binance/Bybit perp | REJECTED | Net edge < all-in cost | — |
| V6-C altcoin funding monitor | Funding anomalies (10 altcoins) | Binance, Bybit perps | REJECTED | No durable candidates | — |
| V7 L2 maker paper | Top-of-book market making | Kraken | REJECTED | Spread < maker fee + fill penalty | — |
| OHLCV lead-lag v1 | 1m bars Binance→Kraken | Binance→Kraken | REJECTED | Bar-level too coarse | — |
| Tick lead-lag v2 | Coinbase/Kraken tick, small capture | Coinbase↔Kraken | REJECTED | Insufficient events | — |
| Tick lead-lag v3 | Coinbase/Kraken BTC/ETH, 600s | Coinbase↔Kraken | REJECTED | Best -12.28 bps, win rate 0% | `lead-lag-v3-coinbase-kraken-rejected` |
| Trade-flow impulse v1 (600s) | 4 signal types, 600s | Coinbase↔Kraken | REJECTED | Best -13.66 bps, win rate 0% | `trade-flow-impulse-v1-rejected` |
| Trade-flow impulse v1 (300s) | 4 signal types, 300s | Coinbase↔Kraken | REJECTED | Best -11.97 bps, win rate 0% | `trade-flow-impulse-v1-300s-rejected` |
| Derivatives lead-lag v1 (smoke) | notional burst, price shock, signed imbalance | Coinbase spot -> Kraken spot BTC | REJECTED_SPOT_SPOT_SMOKE | Best -18.74 bps, win rate 0% | `derivatives-lead-lag-v1-spot-smoke-rejected` |
| DEX-CEX spot dislocation v1 | DEX pool price/volume/liquidity -> CEX forward returns | DEX Screener -> Kraken/Coinbase spot | NEEDS_MORE_DATA | 0 events from 70 snapshots, 2-min window too short, DEX search 5m volumes too stable | `dex-cex-v1-needs-more-data` |

## Locked Gates — Do Not Revisit Without Structural Change

1. **Kraken BTC/USD spot OHLCV indicators** (5m, 1h, Donchian, EMA, ATR). ~80 bps round-trip taker fees. Rejected V1-V4. Stop.
2. **Same-asset same-quote cross-venue tick lead-lag** (CB↔KRK BTC/ETH). HFT-dominated, sub-second decay. Rejected.
3. **Naive top-of-book L2 market making** on BTC/ETH at current fee tier. Spread too small, fills too toxic. Rejected.
4. **Direct cash-and-carry** under Kraken USD spot + Binance/Bybit USDT perp cost model. Net edge below all-in cost. Rejected.
5. **Same-asset spot/spot lead-lag** tested as a smoke run for the derivatives lead-lag v1 observer. REJECTED_SPOT_SPOT_SMOKE. The observer implementation is correct but the run only re-proved an already locked gate.

## Still Open

- **Derivatives flow impulse → spot lead-lag** (perp/futures venue as source, not spot). IMPLEMENTED_OBSERVER_ONLY. The `derivatives_lead_lag_v1` observer is built but has never been tested against an actual derivatives source (Binance/Bybit/Kraken futures perps). The initial v1 smoke run used Coinbase spot as source — a same-asset spot/spot pair already in locked gate #2. Actual derivatives-source thesis: OPEN_UNTESTED.
- OI + price regime classification (filter, not standalone trade)
- Funding as crowding/sentiment feature (not carry)
- L2 adverse selection conditioning on book state
- Options IV/RV regime overlay (filter, not trade)

## Instrument Type Note

The `derivatives_lead_lag_v1` run tagged as `REJECTED_SPOT_SPOT_SMOKE` used:
- source: Coinbase BTC/USD (spot)
- target: Kraken BTC/USD (spot)

This rejected only that specific spot→spot pairing under the configured cost model.
The "derivatives lead-lag" research branch requires a derivatives source (perp or futures)
to be tested. Until then the actual thesis remains OPEN_UNTESTED.

## Known Issues

- config.py Final import: fixed 2026-05-13
- uv version mismatch (0.11.8 pinned vs 0.11.13 runtime)
- No retry logic in REST fetchers
- Pickle coupling in btcusd_research/reports.py
- Baseline window of 60s is too large for 300-600s captures (9 NEEDS_MORE_DATA)
