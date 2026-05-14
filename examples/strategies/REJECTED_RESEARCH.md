# Rejected Research Registry

> **This document records studies that have been rejected or frozen.**
> New research may revisit the same idea only with a materially different mechanism, venue, timeframe, or cost model. Do not re-run the same hypothesis without changing something structural.

---

## Mined Status

The full mined status table is at [reports/research_status_table.csv](../reports/research_status_table.csv) with 45 study groups:
- **35 REJECTED**
- **9 NEEDS_MORE_DATA** (insufficient events / zero signals)
- **1 MARKET_MODERATE_DIAGNOSTIC** (quiet/moderate capture, below volatility gate)
- **1 UNKNOWN**

## Null Testing Discipline

Null permutation testing is now available via `permutation_null.py` / `run_permutation_null.py`. It is a **falsification step only**, applied after evaluation.

- A group that fails null testing is recorded as `NULL_REJECTED_DIAGNOSTIC` in the group-level verdict reason. This is **diagnostic evidence**; it does NOT become a general `REJECTED` verdict for the entire signal family.
- A group that passes null testing (`candidate_survives_null: true`) is marked as *warranting longer observation* — not as proven tradeable.
- Null test results **must never** be used to optimize signal parameters (p-hacking guard).
- If all candidate groups are `NULL_REJECTED_DIAGNOSTIC` or `NO_MCPT_WORTHY_GROUPS`, update this registry with a brief note that the signal family underwent null falsification and record the aggregate outcome. Do NOT promote the signal family to `REJECTED` solely on null results.

Rationale: Null testing checks whether randomly shifted source timing could produce the observed edge. It guards against false discovery from look-elsewhere / data-mining bias, but it is one diagnostic layer, not a final execution verdict.

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
| Derivatives-source spot lead-lag v2 | notional_burst, large_trade, signed_imbalance | Binance USD-M perp → Kraken/Coinbase spot | OPEN_IMPLEMENTATION | Implementation complete. Needs empirical capture during volatile market window. | — |
|| DEX-CEX spot dislocation v1 | DEX pool price/volume/liquidity -> CEX forward returns | DEX Screener -> Kraken/Coinbase spot | NEEDS_MORE_DATA | 0 events from 70 snapshots, 2-min window too short, DEX search 5m volumes too stable | `dex-cex-v1-needs-more-data` |
|| Cross-asset spot impulse v1 | BTC/ETH spot impulse -> alt spot forward returns | Coinbase/Kraken/Binance spot | MARKET_MODERATE_DIAGNOSTIC | Best pair -43.98 bps (coinbase:ETH/USDT->coinbase:LINK/USD) in a quiet/moderate capture. BTC/ETH source movement during actual capture was only ~9-17 bps, below the 30 bps source-range gate required to test stress beta-lag. Not a full volatile-window rejection. | 2026-05-13 07:37-07:53 UTC, 906s capture, quiet/moderate market (BTC 12.6 bps, ETH 16.2 bps). 72 pairs, 11,890 signals, 50 pairs with overlap, 0 pairs with sufficient source/target movement. Diagnostic evidence only — open for volatile-window retest. |

## Locked Gates — Do Not Revisit Without Structural Change

1. **Kraken BTC/USD spot OHLCV indicators** (5m, 1h, Donchian, EMA, ATR). ~80 bps round-trip taker fees. Rejected V1-V4. Stop.
2. **Same-asset same-quote cross-venue tick lead-lag** (CB↔KRK BTC/ETH). HFT-dominated, sub-second decay. Rejected.
3. **Naive top-of-book L2 market making** on BTC/ETH at current fee tier. Spread too small, fills too toxic. Rejected.
4. **Direct cash-and-carry** under Kraken USD spot + Binance/Bybit USDT perp cost model. Net edge below all-in cost. Rejected.
5. **Same-asset spot/spot lead-lag** tested as a smoke run for the derivatives lead-lag v1 observer. REJECTED_SPOT_SPOT_SMOKE. The observer implementation is correct but the run only re-proved an already locked gate.

## Still Open

- **Cross-asset spot impulse v1** — Quiet/moderate-regime diagnostic only. BTC/ETH source movement during the actual capture was ~9-17 bps, below the 30 bps source-range gate required to test stress beta-lag. The 50 pairs with overlap all failed after the 50bps cost wall (best -43.98 bps), but this is diagnostic evidence from a quiet market, not a structural rejection. Open for a genuine volatile/stress-window retest only.
- **Derivatives flow impulse → spot lead-lag** (perp/futures venue as source, not spot). OPEN_IMPLEMENTATION. The v2 implementation (`run_derivatives_spot_capture.py` + `run_derivatives_spot_lead_lag.py`) uses Binance USD-M perp as source and Kraken/Coinbase spot as target with a single combined async capture runner, overlap-window enforcement, OI bucket labeling, and volatility sanity gates. Needs empirical capture during a volatile market window.
- OI + price regime classification (filter, not standalone trade)
- Funding as crowding/sentiment feature (not carry)
- L2 adverse selection conditioning on book state
- Options IV/RV regime overlay (filter, not trade)

## Instrument Type Note

The `derivatives_lead_lag_v1` run tagged as `REJECTED_SPOT_SPOT_SMOKE` used:
- source: Coinbase BTC/USD (spot)
- target: Kraken BTC/USD (spot)

This rejected only that specific spot->spot pairing under the configured cost model.
The "derivatives-source spot lead-lag v2" implementation uses Binance USD-M perp (fapi.binance.com aggTrade WebSocket) as source with Kraken/Coinbase spot as target. It has a combined async capture runner, overlap enforcement, and OI bucket classification. Awaiting empirical capture during a volatile market window.

## First DEX→CEX Empirical Run Summary

The first real empirical run (2026-05-13 03:17-03:27 UTC) produced:

* **CEX ticks:** 1,344 ticks across Kraken + Coinbase for SOL, LINK, AVAX, DOGE, ADA (10-min capture)
* **DEX snapshots:** 18 snapshots from 2 LINK pools on ethereum/uniswap (2-min capture)
* **Events:** 0 dislocation events fired
* **Verdict:** `NEEDS_MORE_DATA`

**Key findings:**
* LINK/ETH and LINK/WETH prices frozen at $10.44 across all 18 snapshots (0.00 bps movement)
* DEX Screener search API returns stale/cached prices during quiet market hours
* SOL, AVAX, DOGE, ADA pools below $500k liquidity + $100k 1h volume threshold
* Only 2 of 5 target assets had pools passing filters
* The DEX search endpoint's 5m volume data is cumulative, not per-interval, making burst detection at 10s polling ineffective

**What would be needed for a real test:**
* Live DEX pool data from direct DEX Screener `/latest/dex/pairs/{chain}/{address}` endpoint
* 30-60 minute capture during active market hours
* Lower minimum thresholds (e.g. $100k liquidity / $50k vol1h) to include mid-tier pools

## Known Issues

- config.py Final import: fixed 2026-05-13 (no longer applies)
- uv version mismatch (0.11.8 pinned vs 0.11.13 runtime)
- No retry logic in REST fetchers
- Pickle coupling in btcusd_research/reports.py
- Baseline window of 60s is too large for 300-600s captures (9 NEEDS_MORE_DATA)
