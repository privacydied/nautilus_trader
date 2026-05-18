# Rejected Research Registry

## Purpose

This file records research hypotheses that were tested, evaluated, and
rejected. A rejection means the hypothesis was tested under specific
conditions (venue, signal design, cost model, date windows) and did not
produce evidence of a tradable edge under those conditions.

Adjacent hypotheses that were NOT tested are listed alongside each rejection,
not as open promises but as precise boundaries of what was not evaluated.

New research may revisit the same idea only with a materially different mechanism,
venue, timeframe, or cost model. Do not re-run the same hypothesis without
changing something structural.

---

## Mined Status

The full mined status table is at [reports/research_status_table.csv](../reports/research_status_table.csv) with 45 study groups:
- **36 REJECTED**
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
| Derivatives-source spot lead-lag v2 | notional_burst, large_trade, signed_imbalance | Binance USD-M perp → Kraken/Coinbase spot | REJECTED | May 15 FULL_ACTIVE: 63 groups, best raw edge 0.098 bps vs 50 bps all-in cost. 0 viable at any cost level. 0 recurring groups May 14→May 15. COST_WALL_BLOCKED. Diagnostics complete: cost sensitivity, heatmap, permutation null (skipped), cross-capture consistency, candidate falsification. | `derivatives-v2-binance-perp-to-spot-rejected` |
| DEX-CEX spot dislocation v1 | DEX pool price/volume/liquidity -> CEX forward returns | DEX Screener -> Kraken/Coinbase spot | NEEDS_MORE_DATA | 0 events from 70 snapshots, 2-min window too short, DEX search 5m volumes too stable | `dex-cex-v1-needs-more-data` |
| Cross-asset spot impulse v1 | BTC/ETH spot impulse -> alt spot forward returns | Coinbase/Kraken/Binance spot | MARKET_MODERATE_DIAGNOSTIC | Best pair -43.98 bps (coinbase:ETH/USDT->coinbase:LINK/USD) in a quiet/moderate capture. BTC/ETH source movement during actual capture was only ~9-17 bps, below the 30 bps source-range gate required to test stress beta-lag. Not a full volatile-window rejection. | 2026-05-13 07:37-07:53 UTC, 906s capture, quiet/moderate market (BTC 12.6 bps, ETH 16.2 bps). 72 pairs, 11,890 signals, 50 pairs with overlap, 0 pairs with sufficient source/target movement. Diagnostic evidence only — open for volatile-window retest. |
| Polymarket BTC Up/Down short-expiry liquidity probe v0 | CLOB orderbook liquidity near expiry | Polymarket CLOB (BTC Up/Down binary options) | REJECTED | Near-expiry depth collapses below $100 non-dust floor: $89 at 5-15m → $13 at 0-30s. Spreads tight (1c median) but depth wall blocks Chainlink/CLOB lag hypothesis. Parser-confounded 98c finding corrected and superseded. | `polymarket-btc-updown-liquidity-v0-rejected` |
| **Family 1 same-venue USD/USDT quote-basis reversion** | Same-venue quote-basis reversion (BTC/USD vs BTC/USDT) | Kraken spot | **REJECTED** | Cached-data diagnostic: observed basis change over 30-120s lookbacks was ~5-30 bps peak; round-trip two-leg cost ~32 bps. Signal is structurally smaller than transaction cost. Cost-wall blocked. Diagnostic confirmed the blocker is signal-vs-cost, not window length or data sparsity. Tag: `family1-kraken-usd-usdt-reversion-cost-wall-rejected` |
| **Family 2 funding crowding reversal** | Binance BTCUSDT funding-rate extremes → spot BTC forward returns | Binance Vision archive (BTCUSDT USDⓈ-M funding + spot) | **REJECTED** | 60 BTC primary cells evaluated Jun 2020–Apr 2026 window (180-day past-only percentile warmup). Approved run (2026-05-18): 25 NEEDS_MORE_DATA, 11 REJECTED, 23 NULL_REJECTED_DIAGNOSTIC, 1 FDR_BLOCKED_DIAGNOSTIC, **0 CANDIDATE_FOR_LONGER_OBSERVATION**. No cell passed all pre-null gates (mean_net > 0, median > 0, win_rate >= 0.55, worst_decile > -50, baseline_delta >= 10). Positive-funding cells uniformly negative. Negative-funding cells had mixed sign but sub-threshold win rates. Full 60-cell evidence: no edge survives after 50 bps cost under frozen precommitted design. | `family2-funding-crowding-reversal-rejected` |

## Rejection Details

### Family 1 — Same-Venue USD/USDT Quote-Basis Reversion

**Tag:** `family1-kraken-usd-usdt-reversion-cost-wall-rejected`

**Verdict:** `REJECTED`

**Hypothesis:** When Kraken BTC/USD and BTC/USDT quote-basis diverges, fading
the divergence (buying the cheap leg, selling the expensive leg) produces a
positive expected forward return over 1-5 minute horizons.

**Tested conditions:**
- Venue: Kraken spot
- Instrument pair: XXBTZUSD vs XBTUSDT
- Signal: Reversion framing — `basis_change_bps < 0 => long, > 0 => short`
- Lookback windows: 30s, 60s, 120s
- Forward horizons: 60s, 300s
- Cost model: ~16 bps per leg, ~32 bps round trip (retail taker)
- Date windows: 7-date calendar-spread (Feb-Aug 2025, Mon-Sun), 13:00-16:00 UTC
- Data: Public Kraken trade history (no quote/order-book data)

**Key result:** Cached-data diagnostic measured peak USD/USDT basis change over
the 30-120s lookback range at roughly 5-30 bps, with a maximum within-window
basis range of ~55 bps. Round-trip execution cost on Kraken spot for the two-leg
trade is approximately 32 bps (~16 bps per leg). The observed signal magnitude is
structurally smaller than its own round-trip transaction cost, in the best
window observed.

**Prior attempts (non-tests):**
Two earlier verdict attempts did not produce a clean powered test:
1. **Instrumentation bug (first run):** The Family 1 evaluator in
   `offline_train_evaluation.py` and `offline_holdout_evaluation.py` read
   `cell.lookback_ms` from the plan but never used it in the basis computation.
   All lookback variants produced identical metrics. This was fixed by
   `family1_tick_basis.py` (the committed tick-basis implementation), but the
   pipeline never reached the evaluation phases in the multi-date run.
2. **Zero stress windows (second run):** The 7-date 13:00-16:00 UTC
   calendar-spread run produced zero promotable stress windows under the
   existing stress-window rules. This was initially suspected to be a window
   length issue, but the subsequent diagnostic showed the structural blocker
   is signal-vs-cost, not window length or data sparsity.

The rejection rests on the cost-wall diagnostic (cached-data analysis of the
same 7 windows), not on the two earlier non-test runs. No FDR, native null,
or holdout stage was reached, and this is correct because the cost-wall blocker
is structural and upstream of those stages.

**What this rejects:**
- Family 1 reversion specifically — the direction mapping
  `basis_change_bps < 0 => long, > 0 => short` on Kraken spot at the current
  retail fee tier.

**What this does NOT reject (untested adjacent hypotheses):**
- Momentum framing of the same basis (never tested) — `basis_change_bps < 0
  => short, > 0 => long` requires a separate precommitment and FDR family-size
  decision.
- The same hypothesis under a maker-only/rebate cost model (never tested) —
  a sub-10 bps round trip could change the cost-wall calculation.
- Quote-basis behavior on lower-liquidity / weaker-peg stablecoins (never
  tested) — USDC/USDT, DAI/USD, or FXS/FRAX pairs may exhibit larger basis
  dislocations than Kraken BTC/USD vs BTC/USDT.
- Order-book level analysis (never tested) — quote ticks may reveal
  sub-second basis divergence that trade prints miss.

### Family 2 — Funding Crowding Reversal

**Tag:** `family2-funding-crowding-reversal-rejected`

**Verdict:** `REJECTED`

**Hypothesis:** When BTCUSDT funding rates are extreme (high positive or negative),
the crowded-side unwind produces a predictable spot BTC reversal over 4–48 hour
horizons. Positive funding extremes (crowded longs) predict downward reversal;
negative funding extremes (crowded shorts) predict upward reversal.

**Tested conditions (frozen precommitment design):**
- Family size: 60 BTC primary cells (6 thresholds × 5 horizons × 2 directions)
- Thresholds: absolute (5bp, 10bp, 25bp) + percentile (5%, 2.5%, 1%) with 180-day past-only percentile lookback
- Horizons: 4h, 8h, 12h, 24h, 48h
- Direction mapping: positive funding → -forward_return; negative funding → +forward_return
- Return leg: spot BTC forward returns only (no perp leg, no funding-paid-while-held term)
- Cost model: 50 bps primary (gates promotion); 6 bps diagnostic sensitivity tier only
- Data: Binance Vision archive, BTCUSDT USDⓈ-M funding rate + BTCUSDT 1h spot klines
- Window: 2020-06-29 00:00:00 UTC → 2026-04-30 16:00:00 UTC (earliest available + 180-day warmup through latest common)
- Null: Timestamp-shuffle null (1000 iterations, alpha=0.05), testing if extreme-funding timestamps' returns are distinguishable from random eligible-calendar timestamps
- Split: Chronological 70/30 train/holdout
- FDR: Benjamini-Yekutieli across all 60 cells (alpha=0.05)

**Key result:** 0 of 60 cells earned CANDIDATE_FOR_LONGER_OBSERVATION. Full verdict breakdown:

| Verdict | Count | Basis |
|---|---|---|
| NEEDS_MORE_DATA | 25 | <50 valid events (insufficient sample) |
| REJECTED | 11 | Failed pre-null gates (mean_net <= 0, median <= 0, win_rate < 0.55) |
| NULL_REJECTED_DIAGNOSTIC | 23 | Passed pre-null gates but null distribution was not exceeded |
| FDR_BLOCKED_DIAGNOSTIC | 1 | Survived null but failed family-wide BY FDR |
| CANDIDATE_FOR_LONGER_OBSERVATION | 0 | — |

Positive-funding cells uniformly showed negative mean net returns (strongly negative
across all thresholds and horizons), meaning upward BTC price moves after positive
funding extremes do not occur at sufficient magnitude or consistency. Negative-funding
cells had mixed sign but consistently sub-0.55 win rates, meaning the reversal after
negative funding extremes is not reliably directional. The 6 bps diagnostic tier did
not change any verdict — the cost wall is not the primary blocker; the signal itself
is absent.

**Prior bug-compromised run (2026-05-18, SHA `ad55c351f0`):** An initial run produced
35 NO_NULL_WORTHY_CELLS due to `CellResult.to_dict()` omitting the `events` field, which
silently dropped event data through the gate pipeline. This was fixed (SHA `54d3f52572`),
verified with 6 regression tests, and the approved rerun produced the verdicts above.
The invalidated run artifacts are marked with `INVALIDATED.txt` markers under
`reports/funding_crowding_reversal_v1/`.

**What this rejects:**
- Family 2 funding crowding reversal under the frozen 60-cell BTC design, at 50 bps
  primary cost, using Binance Vision archive data from 2020-2026, with the specific
  precommitted threshold definitions, horizons, direction mapping, null methodology,
  and train/holdout/FDR gates.

**What this does NOT reject (untested adjacent hypotheses):**
- **Higher-frequency funding signals** (e.g., tick-level funding rate from a live
  WebSocket feed rather than 8-hour archive snapshots) — the archive data's 8h
  cadence may mask intra-period extremes.
- **Cross-exchange funding** (e.g., Bybit or OKX funding rates) — only Binance was
  tested.
- **Funding + OI regime conditioning** — open-interest changes near funding extremes
  were not evaluated as a conditioning filter.
- **Multi-asset funding portfolios** (e.g., altcoin funding baskets) — only BTC was
  tested.
- **Funding accrual capture** (funding-paid-while-held as part of return) — the
  frozen design explicitly excluded this term.
- **Perp basis / roll-yield strategies** — separate from the crowded-trade reversal
  hypothesis.
- **Maker-only / rebate cost tier** (never tested) — a sub-10 bps round trip
  could change which cells clear the primary cost gate.

**Archival Addendum (validated run `funding_crowding_reversal_20260518T180157_168230_8d622b`):**

The approved bugfix rerun (commit `54d3f5257292efb4270fbfe4f3773fbb53cd5220`, seed 42, window `2020-06-29 00:00:00 UTC → 2026-04-30 16:00:00 UTC`) confirmed the verdicts above. All details were verified from run artifacts: SHA matches; window matches; seed matches; 60 BTC primary cells; verdict counts reconcile with the gate funnel; data source is Binance Vision archive only (content hashes recorded, no REST/live/authenticated endpoint); null p-values are finite for every cell that reached the null gate (the events-dropped bug is fixed).

Two prior runs (SHA `ad55c351f0`) were invalidated as `INVALID_RUN_BUG_COMPROMISED_EVENTS_DROPPED` (`CellResult.to_dict()` omitted `events`). Both are quarantined with `INVALIDATED.txt` markers under `reports/funding_crowding_reversal_v1/` and are not used as evidence. The 25 `NEEDS_MORE_DATA` cells — the strictest thresholds (`abs ≥ 0.0025`, top/bottom 1%) at longer horizons — are underpowered because extreme funding is rare; Phase 0 explicitly predicted this, so it is expected behavior, not a bug. One cell (`BTC/pct_funding_top_bottom_5pct/h8/negative_funding_extreme`) survived the timestamp-shuffle null and was then blocked by family-wide BY FDR — the multiple-comparisons correction working as designed; no single cell's isolated edge survived correction across 60 correlated tests. The 6 bps diagnostic sensitivity tier changed no verdicts.

### Polymarket BTC Up/Down Liquidity Probe v0 — Rejection Detail

**Date:** 2026-05-16
**Pipeline:** `run_polymarket_btc_updown_liquidity_probe` + direct slug injection
**Probe module:** `polymarket_btc_updown_liquidity_probe.py`
**Status:** REJECTED — blocked by near-expiry depth wall under current observed liquidity

**Correction History:** The original probe found a ~98c median spread on some market
samples. This was traced to incorrect orderbook parsing (using min bid / max ask instead
of max bid / min ask). After the parser fix, TTE bucketing was also verified correct via
`_check_tte_sanity()` diagnostic. This rejection supersedes the earlier parser-confounded
liquidity read.

| Parameter | Value |
|---|---|
| Duration | 30 minutes (1800s) |
| Poll interval | 5 seconds |
| Markets | 17 BTC Up/Down markets across 5m, 15m, 1h, 4h, 1d durations |
| Total samples | 12,240 |
| Reference proxy | Binance BTC/USDT via REST (30 polls, 60s interval) |

**Near-Expiry Depth Collapse (Primary Rejection Evidence):**

| TTE Bucket | Samples | Median Spread | P95 Spread | Top Depth (bid+ask) | $100 Threshold |
|---|---|---|---|---|---|
| 5-15m | 1,554 | 1c | 1c | $89 | BELOW |
| 2-5m | 576 | 1c | 1c | $43 | BELOW |
| 1-2m | 206 | 1c | 2c | $28 | BELOW |
| 30s-1m | 108 | 1c | 4c | $18 | BELOW |
| 0-30s | 108 | 1c | 7c | $13 | BELOW |

Near-expiry rollup (tte<=120s): 422 samples, 9 markets, GREEN on spread (1c median),
but depth below $100 threshold.

**Preserved Infrastructure:** The liquidity probe module and CLI runner remain reusable
for Price Target markets. TTE computation, orderbook parser, CEX proxy routing,
distance-to-strike buckets, two-axis grid, duration coverage, and verification status
pipeline are all verified working.

### First DEX→CEX Empirical Run Summary

The first real empirical run (2026-05-13 03:17-03:27 UTC) produced:
- CEX ticks: 1,344 ticks across Kraken + Coinbase for SOL, LINK, AVAX, DOGE, ADA (10-min capture)
- DEX snapshots: 18 snapshots from 2 LINK pools on ethereum/uniswap (2-min capture)
- Events: 0 dislocation events fired
- Verdict: `NEEDS_MORE_DATA`

**Key findings:**
- LINK/ETH and LINK/WETH prices frozen at $10.44 across all 18 snapshots (0.00 bps movement)
- DEX Screener search API returns stale/cached prices during quiet market hours
- SOL, AVAX, DOGE, ADA pools below $500k liquidity + $100k 1h volume threshold
- Only 2 of 5 target assets had pools passing filters
- The DEX search endpoint's 5m volume data is cumulative, not per-interval, making burst detection at 10s polling ineffective

**What would be needed for a real test:**
- Live DEX pool data from direct DEX Screener `/latest/dex/pairs/{chain}/{address}` endpoint
- 30-60 minute capture during active market hours
- Lower minimum thresholds (e.g. $100k liquidity / $50k vol1h) to include mid-tier pools

## Locked Gates — Do Not Revisit Without Structural Change

1. **Kraken BTC/USD spot OHLCV indicators** (5m, 1h, Donchian, EMA, ATR). ~80 bps round-trip taker fees. Rejected V1-V4. Stop.

2. **Same-asset same-quote cross-venue tick lead-lag** (CB↔KRK BTC/ETH). HFT-dominated, sub-second decay. Rejected.

3. **Naive top-of-book L2 market making** on BTC/ETH at current fee tier. Spread too small, fills too toxic. Rejected.

4. **Direct cash-and-carry** under Kraken USD spot + Binance/Bybit USDT perp cost model. Net edge below all-in cost. Rejected.

5. **Same-asset spot/spot lead-lag** tested as a smoke run for the derivatives lead-lag v1 observer. REJECTED_SPOT_SPOT_SMOKE. The observer implementation is correct but the run only re-proved an already locked gate.

6. **Derivatives flow impulse → spot lead-lag** (Binance USD-M perp → Kraken/Coinbase spot). REJECTED after full-empirical capture (May 15 FULL_ACTIVE). Best raw edge 0.098 bps vs 50 bps all-in cost. 0 viable groups at any cost level. 0 recurring groups across two separate captures. Closed under this execution stack. Do not revisit without materially different execution assumptions: HFT-grade colocated execution, much lower fees, different venue microstructure, or a genuinely different signal family. Do not reopen by merely changing lookbacks, horizons, or adding OI filters.

7. **Polymarket BTC Up/Down short-expiry Chainlink/CLOB lag** — Near-expiry depth collapses below $100 non-dust floor. Parser-confounded 98c spread finding corrected and superseded. Spreads are tight (1c median) but depth wall is real. Distance-to-strike structurally unavailable on directional Up/Down markets (no strike price in question text). Probe infrastructure preserved for Price Target markets, which are a separate hypothesis requiring their own precommitment.

8. **Family 1 same-venue USD/USDT quote-basis reversion on Kraken spot.** Signal magnitude (5-30 bps basis change) is below round-trip cost (~32 bps). Do not revisit without a materially different cost model (maker-only/rebate tier), a materially different instrument (weaker-peg stablecoin with larger basis dislocations), or a different venue microstructure. Do not reopen by changing lookbacks, horizons, window length, or date set.

9. **Family 2 funding crowding reversal** (Binance BTCUSDT funding extremes → spot BTC forward returns). 0/60 cells survived the frozen design. Signal absent at 50 bps cost; 6 bps diagnostic also shows no consistent edge. Do not revisit without a materially different signal definition (tick-level funding, cross-exchange, multi-asset, or funding+OI conditioning). Do not reopen by merely changing thresholds, horizons, windows, or null iterations.

10. **Family 2 funding crowding reversal v1** (frozen design: 60 BTC cells, spot return leg, 50 bps primary cost, Binance Vision archive, timestamp-shuffle null, BY FDR). 0 candidates; 1 FDR-blocked cell confirmed. Do not reopen by changing thresholds, horizons, window, seed, or cost within the v1 protocol. Revisiting requires a materially different precommitment — a perp return leg with explicit funding-paid-while-held modeling, a maker/rebate cost tier, a different instrument universe, or a different crowding feature.

## Still Open

- **Cross-asset beta lag under stress** — BTC/ETH shock leads slower repricing in higher-beta assets over 30s to 5m. Distinct from same-asset derivatives-to-spot lead-lag. Only testable during genuine stress windows. See `CROSS_ASSET_BETA_LAG_PRECOMMITMENT.md`.
- **Cross-asset spot impulse v1** — Quiet/moderate-regime diagnostic only. BTC/ETH source movement during the actual capture was ~9-17 bps, below the 30 bps source-range gate required to test stress beta-lag. The 50 pairs with overlap all failed after the 50bps cost wall (best -43.98 bps), but this is diagnostic evidence from a quiet market, not a structural rejection. Open for a genuine volatile/stress-window retest only.
- OI + price regime classification (filter, not standalone trade)
- L2 adverse selection conditioning on book state
- Options IV/RV regime overlay (filter, not trade)

## Known Issues

- config.py Final import: fixed 2026-05-13 (no longer applies)
- uv version mismatch (0.11.8 pinned vs 0.11.13 runtime)
- No retry logic in REST fetchers
- Pickle coupling in btcusd_research/reports.py
- Baseline window of 60s is too large for 300-600s captures (9 NEEDS_MORE_DATA)

## Open Study Infrastructure Notes

The completed diagnostic infrastructure for derivatives-source spot lead-lag v2
(GPU permutation/null, forward returns, heatmap, cost sensitivity, consistency,
falsification) remains available for other observer studies that share a compatible
report schema. The v2 evaluator, heatmap, cost sensitivity, permutation null, and
falsification tools are generic across observer frameworks that produce lead-lag-group
output format, not exclusive to the now-rejected derivatives v2 hypothesis.
