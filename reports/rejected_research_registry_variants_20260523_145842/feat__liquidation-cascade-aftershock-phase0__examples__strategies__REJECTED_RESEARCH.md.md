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

Additional registry-only pre-evaluation status:
- **1 PHASE0_KILLED** (`PHASE0_KILLED_NO_FUNDING_DIVERGENCE_TAIL`) — distribution kill before v1 precommitment/evaluator construction; not counted as a full strategy `REJECTED` verdict because no return-side evaluation was run.

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
| Derivatives-source spot lead-lag v2 | notional_burst, large_trade, signed_imbalance | Binance USD-M perp → Kraken/Coinbase spot | REJECTED | May 15 FULL_ACTIVE: 63 groups, best raw edge 0.098 bps vs 50 bps all-in cost. 0 viable at any cost level. 0 recurring groups May 14→May 15. COST_WALL_BLOCKED. Diagnostics complete: cost sensitivity, heatmap, permutation null (skipped), cross-capture consistency, candidate falsification. | `derivatives-v2-binance-perp-to-spot-rejected` |
|| DEX-CEX spot dislocation v1 | DEX pool price/volume/liquidity -> CEX forward returns | DEX Screener -> Kraken/Coinbase spot | NEEDS_MORE_DATA | 0 events from 70 snapshots, 2-min window too short, DEX search 5m volumes too stable | `dex-cex-v1-needs-more-data` |
||| Cross-asset spot impulse v1 | BTC/ETH spot impulse -> alt spot forward returns | Coinbase/Kraken/Binance spot | MARKET_MODERATE_DIAGNOSTIC | Best pair -43.98 bps (coinbase:ETH/USDT->coinbase:LINK/USD) in a quiet/moderate capture. BTC/ETH source movement during actual capture was only ~9-17 bps, below the 30 bps source-range gate required to test stress beta-lag. Not a full volatile-window rejection. | 2026-05-13 07:37-07:53 UTC, 906s capture, quiet/moderate market (BTC 12.6 bps, ETH 16.2 bps). 72 pairs, 11,890 signals, 50 pairs with overlap, 0 pairs with sufficient source/target movement. Diagnostic evidence only — open for volatile-window retest. |
||| Polymarket BTC Up/Down short-expiry liquidity probe v0 | CLOB orderbook liquidity near expiry | Polymarket CLOB (BTC Up/Down binary options) | REJECTED | Near-expiry depth collapses below $100 non-dust floor: $89 at 5-15m → $13 at 0-30s. Spreads tight (1c median) but depth wall blocks Chainlink/CLOB lag hypothesis. Parser-confounded 98c finding corrected and superseded. | `polymarket-btc-updown-liquidity-v0-rejected` |
| Hyperliquid BTC/ETH funding divergence Phase 0 | Hyperliquid venue-specific funding divergence / crowding-pressure distribution audit | Hyperliquid perps vs Binance/Bybit reference funding | PHASE0_KILLED_NO_FUNDING_DIVERGENCE_TAIL | BTC max abs divergence 5.86 bps and p99 1.08 bps; ETH max abs divergence 2.23 bps and p99 0.92 bps. Both assets were below the pre-registered kill thresholds of max < 10.0 bps and p99 < 8.0 bps. Distribution tail absent; no v1 precommitment should be written. | `hyperliquid-funding-divergence-phase0-killed-no-tail` |

## Locked Gates — Do Not Revisit Without Structural Change

1. **Kraken BTC/USD spot OHLCV indicators** (5m, 1h, Donchian, EMA, ATR). ~80 bps round-trip taker fees. Rejected V1-V4. Stop.
2. **Same-asset same-quote cross-venue tick lead-lag** (CB↔KRK BTC/ETH). HFT-dominated, sub-second decay. Rejected.
3. **Naive top-of-book L2 market making** on BTC/ETH at current fee tier. Spread too small, fills too toxic. Rejected.
4. **Direct cash-and-carry** under Kraken USD spot + Binance/Bybit USDT perp cost model. Net edge below all-in cost. Rejected.
5. **Same-asset spot/spot lead-lag** tested as a smoke run for the derivatives lead-lag v1 observer. REJECTED_SPOT_SPOT_SMOKE. The observer implementation is correct but the run only re-proved an already locked gate.
6. **Derivatives flow impulse → spot lead-lag** (Binance USD-M perp → Kraken/Coinbase spot). REJECTED after full-empirical capture (May 15 FULL_ACTIVE). Best raw edge 0.098 bps vs 50 bps all-in cost. 0 viable groups at any cost level. 0 recurring groups across two separate captures. Closed under this execution stack. Do not revisit without materially different execution assumptions: HFT-grade colocated execution, much lower fees, different venue microstructure, or a genuinely different signal family. Do not reopen by merely changing lookbacks, horizons, or adding OI filters.
7. **Polymarket BTC Up/Down short-expiry Chainlink/CLOB lag** — Near-expiry depth collapses below $100 non-dust floor. Parser-confounded 98c spread finding corrected and superseded. Spreads are tight (1c median) but depth wall is real. Distance-to-strike structurally unavailable on directional Up/Down markets (no strike price in question text). Probe infrastructure preserved for Price Target markets, which are a separate hypothesis requiring their own precommitment.
8. **Hyperliquid BTC/ETH funding divergence Phase 0** — HL-vs-Binance/Bybit funding divergence tail absent. BTC max/p99 5.86/1.08 bps; ETH max/p99 2.23/0.92 bps, below pre-registered 10.0 max / 8.0 p99 kill thresholds. No v1 precommitment or evaluator should be written for this exact premise. Reopen only with materially different Phase 0 scope.

## Hyperliquid BTC/ETH Funding Divergence Phase 0 — Distribution Kill Detail

**Tag:** `hyperliquid-funding-divergence-phase0-killed-no-tail`
**Status:** `PHASE0_KILLED_NO_FUNDING_DIVERGENCE_TAIL`

### Study identity

Hyperliquid BTC/ETH funding divergence Phase 0 distribution audit. This was a source/distribution audit only, not a return-side strategy evaluation.

### Mechanism tested

The audit asked whether Hyperliquid BTC/ETH funding diverges from Binance/Bybit reference funding enough to justify a future venue-specific crowding-pressure precommitment.

This is distinct from:
- Family 2 funding crowding reversal, which tested Binance funding extremes.
- Family 3 funding × OI variants, which tested Binance funding/OI conditioning.
- Hyperliquid BTC→LINK fixed-cell replay, which tested cross-asset beta-lag execution feasibility.

### Data window

Audited window: 2024-01-01 to 2025-05-30.

### Inputs

| Source | Path | Rows | First timestamp | Last timestamp | Notes |
|---|---|---:|---|---|---|
| Hyperliquid BTC | `examples/strategies/venue_agnostic_signal_observer/data/hyperliquid_funding_archive_phase0/hyperliquid_funding_BTC_2024-01-01_to_2025-05-30.jsonl` | 12,383 | 2024-01-01T00:00:00.151000Z | 2025-05-30T23:00:00.180000Z | detected rate basis: native_decimal_hourly_funding_rate |
| Hyperliquid ETH | `examples/strategies/venue_agnostic_signal_observer/data/hyperliquid_funding_archive_phase0/hyperliquid_funding_ETH_2024-01-01_to_2025-05-30.jsonl` | 12,383 | 2024-01-01T00:00:00.151000Z | 2025-05-30T23:00:00.180000Z | detected rate basis: native_decimal_hourly_funding_rate |
| Binance BTC | `examples/strategies/venue_agnostic_signal_observer/data/funding_dispersion_carry_v1_archives/binance_btc_funding.csv` | 1,551 | — | — | reference funding |
| Binance ETH | `examples/strategies/venue_agnostic_signal_observer/data/funding_dispersion_carry_v1_archives/binance_eth_funding.csv` | 1,551 | — | — | reference funding |
| Bybit BTC | `examples/strategies/venue_agnostic_signal_observer/data/funding_dispersion_carry_v1_archives/bybit_btc_funding.csv` | 1,552 | — | — | reference funding |
| Bybit ETH | `examples/strategies/venue_agnostic_signal_observer/data/funding_dispersion_carry_v1_archives/bybit_eth_funding.csv` | 1,552 | — | — | reference funding |

### Pre-registered kill criteria

- Kill if both BTC and ETH have max absolute divergence below 10.0 bps.
- Kill if both BTC and ETH have p99 absolute divergence below 8.0 bps.

### Result table

| Asset | Max abs divergence | Max threshold | p99 abs divergence | p99 threshold | Outcome |
|---|---:|---:|---:|---:|---|
| BTC | 5.86 bps | 10.0 bps | 1.08 bps | 8.0 bps | Below kill thresholds |
| ETH | 2.23 bps | 10.0 bps | 0.92 bps | 8.0 bps | Below kill thresholds |

### Interpretation

Hyperliquid did not show a meaningful BTC/ETH funding-divergence tail versus Binance/Bybit over the audited window. The mechanical precondition for a venue-specific funding-crowding hypothesis was absent. The hypothesis is killed before v1 precommitment or evaluator construction.

### What this closes

- BTC/ETH Hyperliquid-vs-Binance/Bybit funding divergence as a Phase 0 motivation for a v1 crowding-pressure study over the audited 2024-01-01 to 2025-05-30 window.
- Writing a v1 precommitment from this distribution audit.
- Building a return evaluator for this exact BTC/ETH funding-divergence premise.

### What this does NOT close

- Other assets on Hyperliquid.
- Later market regimes outside the audited window.
- A materially different venue/reference set.
- A different mechanism that is not dependent on HL-vs-reference funding divergence tail.
- Funding-paid-while-held studies that start from a separate precommitment and different mechanical premise.
- Hyperliquid as a venue generally.
- Cross-asset beta-lag research generally.

### Locked gate

Do not write a v1 precommitment or evaluator for BTC/ETH Hyperliquid-vs-Binance/Bybit funding divergence under this Phase 0 premise. Reopening requires a fresh Phase 0 distribution audit with a materially different scope, such as a different asset universe, different reference venues, or a later non-overlapping market regime. Do not reopen by lowering the 10.0 bps max / 8.0 bps p99 kill thresholds after seeing this result.

### Run artifacts

- Branch: `feat/hyperliquid-funding-divergence-phase0-rerun`
- Starting SHA: `af4fe52086d3d3e617a12e69ff8b225b0ddeb2f3`
- Commit: `e2146f9326a493623679a1b5370fbc31a7d917ba`
- Run directory: `reports/hyperliquid_funding_divergence_phase0_rerun/hyperliquid_funding_divergence_phase0_20260523T135003_945810`
- Completed-run tests:
  - `uv run --no-sync pytest examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_divergence_phase0.py -q` — 11 passed, 0 failed
  - `uv run --no-sync pytest examples/strategies/venue_agnostic_signal_observer/tests/test_hyperliquid_funding_archive_backfill.py -q` — 24 passed, 0 failed

### Safety statement

No forward returns, PnL, price-path dependent variables, orders, private keys, auth, execution, shadow, bot, null, FDR, holdout, evaluator, or v1 precommitment were used. This entry is not recorded as a full strategy `REJECTED` verdict.

## Polymarket BTC Up/Down Liquidity Probe v0 — Rejection Detail

**Date:** 2026-05-16
**Pipeline:** `run_polymarket_btc_updown_liquidity_probe` + direct slug injection
**Probe module:** `polymarket_btc_updown_liquidity_probe.py`
**Status:** REJECTED — blocked by near-expiry depth wall under current observed liquidity

### Correction History

The original probe found a ~98c median spread on some market samples. This was traced to incorrect orderbook parsing (using min bid / max ask instead of max bid / min ask). After the parser fix, TTE bucketing was also verified correct via `_check_tte_sanity()` diagnostic. This rejection supersedes the earlier parser-confounded liquidity read.

### Experiment

| Parameter | Value |
|---|---|
| Duration | 30 minutes (1800s) |
| Poll interval | 5 seconds |
| Markets | 17 BTC Up/Down markets across 5m, 15m, 1h, 4h, 1d durations |
| Total samples | 12,240 |
| Reference proxy | Binance BTC/USDT via REST (30 polls, 60s interval) |

### Near-Expiry Depth Collapse (Primary Rejection Evidence)

| TTE Bucket | Samples | Median Spread | P95 Spread | Top Depth (bid+ask) | $100 Threshold |
|---|---|---|---|---|---|
| 5-15m | 1,554 | 1c | 1c | $89 | BELOW |
| 2-5m | 576 | 1c | 1c | $43 | BELOW |
| 1-2m | 206 | 1c | 2c | $28 | BELOW |
| 30s-1m | 108 | 1c | 4c | $18 | BELOW |
| 0-30s | 108 | 1c | 7c | $13 | BELOW |

Near-expiry rollup (tte<=120s): 422 samples, 9 markets, GREEN on spread (1c median), but depth below $100 threshold.

### Key Findings

1. **Spread was a parser artifact; depth is real.** After correction, spreads are 1c median globally. But near-expiry depth drops monotonically from ~$89 to ~$13 in the final 2 minutes.
2. **Two-sided rate degrades near expiry.** 92.6% at 30s-1m, 75.9% at 0-30s, 0% expired.
3. **P95 spread widens near expiry.** 1-2c for TTE > 1m, rising to 4c at 30s-1m and 7c at 0-30s.
4. **Distance-to-strike structurally unavailable.** BTC Up/Down markets have no numerical strike price. `_extract_price_to_beat()` correctly returns None for directional questions.
5. **Proxy routing infrastructure works.** 30 Binance proxy prices captured with `reference_source: BINANCE`.

### Preserved Infrastructure

The liquidity probe module and CLI runner remain reusable for Price Target markets. TTE computation, orderbook parser, CEX proxy routing, distance-to-strike buckets, two-axis grid, duration coverage, and verification status pipeline are all verified working.

### Price Target Markets — Separate Future Hypothesis

BTC Price Target markets (e.g., "Will BTC be above $X by Y?") embed a numerical strike price enabling distance-to-strike computation. This is a separate hypothesis requiring its own precommitment document, market discovery logic, and independent liquidity thresholds. The Up/Down rejection does not automatically disqualify Price Target markets.

## Still Open

- **Cross-asset beta lag under stress** — BTC/ETH shock leads slower repricing in higher-beta assets over 30s to 5m. Distinct from same-asset derivatives-to-spot lead-lag. Only testable during genuine stress windows. See `CROSS_ASSET_BETA_LAG_PRECOMMITMENT.md`.
- **Cross-asset spot impulse v1** — Quiet/moderate-regime diagnostic only. BTC/ETH source movement during the actual capture was ~9-17 bps, below the 30 bps source-range gate required to test stress beta-lag. The 50 pairs with overlap all failed after the 50bps cost wall (best -43.98 bps), but this is diagnostic evidence from a quiet market, not a structural rejection. Open for a genuine volatile/stress-window retest only.
- OI + price regime classification (filter, not standalone trade)
- Funding as crowding/sentiment feature (not carry), excluding the completed-and-killed BTC/ETH Hyperliquid-vs-Binance/Bybit funding-divergence Phase 0 premise recorded above.
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

## Open Study Infrastructure Notes

The completed diagnostic infrastructure for derivatives-source spot lead-lag v2 (GPU permutation/null, forward returns, heatmap, cost sensitivity, consistency, falsification) remains available for other observer studies that share a compatible report schema. The v2 evaluator, heatmap, cost sensitivity, permutation null, and falsification tools are generic across observer frameworks that produce lead-lag-group output format, not exclusive to the now-rejected derivatives v2 hypothesis.
