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

The full mined status table is at [reports/research_status_table.csv](../reports/research_status_table.csv) with 49 study groups:
- **37 REJECTED**
- **11 NEEDS_MORE_DATA** (insufficient events / zero signals / no tail / market availability blocked)
- **1 MARKET_MODERATE_DIAGNOSTIC** (quiet/moderate capture, below volatility gate)
- **1 PHASE0_CLOSED_INDICATOR_FAMILY_DIAGNOSTIC** (scoped Phase 0 diagnostic family closure; not counted as a broad venue or strategy-family rejection)
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
|| Polymarket BTC Up/Down short-expiry liquidity probe v0 | CLOB orderbook liquidity near expiry | Polymarket CLOB (BTC Up/Down binary options) | REJECTED | Near-expiry depth collapses below $100 non-dust floor: $89 at 5-15m → $13 at 0-30s. Spreads tight (1c median) but depth wall blocks Chainlink/CLOB lag hypothesis. Parser-confounded 98c finding corrected and superseded. | `polymarket-btc-updown-liquidity-v0-rejected` |
|| **Polymarket BTC Price Target strike-distance liquidity/mispricing probe v0** | CLOB orderbook liquidity (strike-distance grid) | Polymarket CLOB (BTC Price Target binary options) | **NEEDS_MORE_DATA** | Market availability blocked: 1 active market found, months-to-expiry, deep below strike, empty/unusable CLOB book. 0 valid two-sided samples. Hypothesis untested — not rejected. Reopen requires active strike-bearing markets with observable two-sided books. Tag: `polymarket-btc-price-target-v0-market-availability-blocked` |
| **Family 1 same-venue USD/USDT quote-basis reversion** | Same-venue quote-basis reversion (BTC/USD vs BTC/USDT) | Kraken spot | **REJECTED** | Cached-data diagnostic: observed basis change over 30-120s lookbacks was ~5-30 bps peak; round-trip two-leg cost ~32 bps. Signal is structurally smaller than transaction cost. Cost-wall blocked. Diagnostic confirmed the blocker is signal-vs-cost, not window length or data sparsity. Tag: `family1-kraken-usd-usdt-reversion-cost-wall-rejected` |
| **Family 2 funding crowding reversal** | Binance BTCUSDT funding-rate extremes → spot BTC forward returns | Binance Vision archive (BTCUSDT USDⓈ-M funding + spot) | **REJECTED** | 60 BTC primary cells evaluated Jun 2020–Apr 2026 window (180-day past-only percentile warmup). Approved run (2026-05-18): 25 NEEDS_MORE_DATA, 11 REJECTED, 23 NULL_REJECTED_DIAGNOSTIC, 1 FDR_BLOCKED_DIAGNOSTIC, **0 CANDIDATE_FOR_LONGER_OBSERVATION**. No cell passed all pre-null gates (mean_net > 0, median > 0, win_rate >= 0.55, worst_decile > -50, baseline_delta >= 10). Positive-funding cells uniformly negative. Negative-funding cells had mixed sign but sub-threshold win rates. Full 60-cell evidence: no edge survives after 50 bps cost under frozen precommitted design. | `family2-funding-crowding-reversal-rejected` |
|| **Cross-exchange funding dispersion carry v1** | Binance vs Bybit perp funding spread carry (short high/long low) | Binance Vision + Bybit v5 archive (BTC & ETH) | **NEEDS_MORE_DATA_OR_NO_TAIL** | Gate A (Stage 1): zero dispersion events at all frozen thresholds (5/10/20/40 bps) for both BTC and ETH. Absolute funding spread never exceeded 5 bps across 1,261 aligned settlements (2024-01 to 2025-05). BTC max: 4.14 bps; ETH max: 4.49 bps. Gate B, null, FDR, holdout never reached. | `cross-exchange-funding-dispersion-carry-v1-no-tail` |
|| **Family 3 funding × OI crowding regime v0** | Binance BTCUSDT funding extremes × OI regime (rising/falling) → spot BTC forward returns | Binance Vision archive (BTCUSDT USDⓈ-M metrics + funding + spot) | **SIGNAL_ABSENCE_AT_COST** | 6 primary cells (2 directions × 1 OI regime × 3 horizons). All 6 cells failed evaluation gates (mean net bps < 0). Best cell: negative_extreme × rising_oi 48h = -19.3 bps mean net, 48.4% win rate. All mean net bps negative (range: -19 to -126 bps). 2/6 cells passed null test (negative extremes less bad than random at p<0.05) but still negative. No cell reached FDR or holdout. Blocker: cost wall + signal absence. 456 aligned extreme × rising events (226 neg) adequate for detection but directional mapping does not overcome 50 bps cost. See run `funding_oi_crowding_regime_v0_20260519T010334_54ceff`. Precommitment SHA: `5f911c3f`. Tag: `family3-funding-oi-crowding-regime-signal-absence` |
|| **Family 3 v1 funding × falling-OI unwind mapping** | Binance BTCUSDT negative funding extreme × falling OI → spot BTC forward returns | Binance Vision archive (BTCUSDT USDⓈ-M metrics + funding + spot) | **REJECTED** | 2 primary cells, 24h and 48h. Stage A POPULATION_SUFFICIENT after ms/µs spot-kline parser fix and preflight. Both cells GATES_FAILED at 50 bps primary cost: 24h mean_net -45.14 bps, WR 0.395; 48h mean_net -38.00 bps, WR 0.435. Both beat timestamp-shuffle null (p=0.010 / 0.035), but remained economically negative. "Less bad than random," not tradeable. | `family3-funding-falling-oi-unwind-v1-rejected` |
|| **Hyperliquid BTC->LINK fixed-cell paper replay v0** | Cross-asset beta-lag / BTC stress into LINK perp | Hyperliquid LINK perpetual public S3/archive order-book replay | **PAPER_EXECUTION_DIAGNOSTIC_FAIL_NOT_PROMOTED** | Corrected taker/taker executable ask/bid replay: 348/348 valid round trips, mean net +6.13 bps, median net +2.57 bps, win rate 51.7%, mean lower confidence bound -8.87 bps, pass criterion false. Prior +39.47 bps diagnostic pass invalidated by TIME_RANGE_LOADING_BUG. Not a formal v1 evaluation or null-tested research verdict; exact fixed cell not promoted to v1/shadow. | `hyperliquid-btc-link-fixed-cell-paper-replay-v0-not-promoted` |
|| **Hyperliquid multi-asset funding carry Phase 0** | Single-venue cross-sectional perp funding carry | Hyperliquid perps | **PHASE0_KILLED_NO_CROSS_SECTIONAL_FUNDING_TAIL** | Real public Hyperliquid universe discovery/backfill path built; Phase 0A/0B diagnostics only. Phase 0B spread tail absent: p50 0.035 bps, p90 0.194 bps, p95 0.257 bps vs frozen kill gates p50 >= 3 bps and p90 >= 10 bps. All 18 grid cells underpowered. `v1_unlocked: false`. Not a full `REJECTED` strategy verdict: no v1 evaluator, strategy PnL, null, FDR, holdout, execution, shadow executor, or bot path was used. | `hyperliquid-multi-asset-funding-carry-phase0-killed-no-tail` |
| **Hyperliquid Supertrend 4h/1d altcoin perp v0** | Supertrend Phase 0 indicator diagnostic | Hyperliquid altcoin perpetuals | **PHASE0_CLOSED_INDICATOR_FAMILY_DIAGNOSTIC** | Frozen 20-symbol public-data Hyperliquid altcoin perp universe. 4h failed gross layer: 791 entries, median gross -152.87848344544423 bps, median net primary -173.39852944544424 bps (`PHASE0C_NO_GROSS_EDGE`). 1d was gross-positive but net-negative: 151 entries, median gross +51.65811718806597 bps, median net primary -65.46250328485137 bps (`PHASE0C_GROSS_POSITIVE_NET_NEGATIVE`). Recovered per-entry artifacts reconcile exactly; residual diagnostic emitted regime concentration, Supertrend exit/giveback blocker, and family closure statuses. Exact v0 branch closed; not promoted and not a broad Hyperliquid rejection. | `hyperliquid-supertrend-4h1d-altcoin-perp-v0-closed` |

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

### Cross-Exchange Funding Dispersion Carry v1

**Tag:** `cross-exchange-funding-dispersion-carry-v1-no-tail`

**Verdict:** `NEEDS_MORE_DATA_OR_NO_TAIL`

**Hypothesis:** When the funding rate differential between Bybit and Binance perpetual
BTC/ETH contracts exceeds a threshold, shorting the higher-funding venue and going
long on the lower-funding venue produces positive carry over the subsequent N
settlements (N = 3, 6, 12) after deducting 50 bps round-trip cost.

**Tested conditions (frozen precommitment design):**

| Parameter | Value |
|---|---|
| Study ID | `cross-exchange-funding-dispersion-carry-v1` |
| Assets | BTC, ETH |
| Venues | Binance (long leg), Bybit (short leg) |
| Thresholds | 5, 10, 20, 40 bps (absolute funding spread) |
| Hold lengths | 3, 6, 12 settlements |
| Grid dimensions | 2 assets × 4 thresholds × 3 holds = 24 cells |
| Primary cost | 50 bps per campaign (charged once, not per settlement) |
| Diagnostic cost | 6 bps (changes no verdict) |
| Null | Event-vector circular shift, 1000 iterations, alpha 0.05, seed 42 |
| FDR | Benjamini-Yekutieli, alpha 0.05, family size 24 |
| Split | 70/30 chronological, split at 2024-12-13 |
| Data sources | Binance Vision monthly CSV archives + Bybit v5 public API historical |
| Window | 2024-01-01 00:00 UTC → 2025-05-31 16:00 UTC (1261 aligned settlements) |
| Dropped unaligned | 290 settlements |
| Funding unit | Decimal (auto-detected, confirmed via Binance Vision CSV + Bybit API archive) |
| Safety mode | `public_data_observer_only` |

**Key result:** Gate A (Stage 1 — distribution sizing) found zero dispersion events at
every frozen threshold for both BTC and ETH. The absolute cross-exchange funding
spread between Bybit and Binance never exceeded 5 bps at any settlement in the
entire 17-month window. The pipeline terminated at Gate A; Gate B, null test, FDR,
and holdout were never reached.

| Asset | Mean |disp| | Median |disp| | Max |disp| | p95 |p95| | p99 |p99| |
|---|---|---|---|---|---|
| BTC | 0.44 bps | 0.31 bps | 4.14 bps | 1.43 bps | 2.30 bps |
| ETH | 0.42 bps | 0.27 bps | 4.49 bps | 1.32 bps | 2.41 bps |

Top 10 absolute dispersion observations by asset:

BTC:
1. 2025-02-22 00:00 UTC: 4.14 bps (bybit 5.14, binance 1.00)
2. 2024-12-05 08:00 UTC: 3.61 bps (bybit 10.86, binance 7.24)
3. 2025-03-10 00:00 UTC: 3.33 bps (bybit −2.94, binance 0.40)
4. 2024-03-05 00:00 UTC: 3.11 bps (bybit 8.00, binance 4.90)
5. 2024-03-05 08:00 UTC: 2.98 bps (bybit 11.28, binance 8.30)

ETH:
1. 2024-02-27 08:00 UTC: 4.49 bps (bybit 2.18, binance 6.68)
2. 2024-12-04 16:00 UTC: 3.99 bps (bybit 5.98, binance 1.98)
3. 2024-12-04 08:00 UTC: 3.63 bps (bybit 5.22, binance 1.59)
4. 2024-12-05 08:00 UTC: 3.45 bps (bybit 8.40, binance 4.95)
5. 2024-11-28 00:00 UTC: 3.36 bps (bybit 4.95, binance 1.59)

**Verdict justification:** The 5 bps threshold is the minimum in the frozen grid.
The maximum observed absolute spread was 4.49 bps (ETH), below 5 bps. Neither
asset produced a single event at any threshold. Under the precommitment, Gate A
fires `NEEDS_MORE_DATA_OR_NO_TAIL` when both assets have zero events at every
threshold. This is the correct verdict — the signal is absent from the data, not
merely underpowered.

**What this closes:**
- BTC/ETH perpetual funding dispersion carry between Binance and Bybit only
- Fixed-N settlement holds (3, 6, 12) only
- Frozen thresholds 5, 10, 20, 40 bps only
- Pure funding-accrual carry (no basis/spot-return component) only
- Archive-only v1 design only
- The specific 2024-01 to 2025-05 window only

**What this does NOT close:**
- Cross-exchange funding carry as a broad family (other venue pairs may differ)
- OKX or three-venue dispersion (never tested)
- Altcoin funding dispersion (never tested)
- Stress-only funding dislocations (the 2024-01 to 2025-05 window includes no
  312-like or LUNA-like event; extreme regime behavior was not observed)
- Maker/rebate or institutional-fee cost models (never tested; a sub-5 bps cost
  model could make the observed 1-4 bps spreads partially capturable)
- Convergence-triggered exits (never tested)
- Funding plus basis/mark-to-market spread components (never tested)
- Variable position sizing (never tested)
- Pre-settlement entry timing (never tested)

**Run artifacts:**
- Output: `reports/funding_dispersion_carry/cross_exchange_funding_dispersion_carry_v1_27119f3/fdc_20260518T202319_073672_d67801/`
- Metadata includes: git SHA, seed, window, split date, content hashes, funding-unit detection results, Gate A output, Gate B output (empty)
- Precommitment: `docs/CROSS_EXCHANGE_FUNDING_DISPERSION_PRECOMMITMENT.md` (frozen, with Appendix A resolutions R1/R2/R3)
- Implementation SHA: `4d2a322558ae10e84b8a13e6b03166bfb34c97ff`

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

### Polymarket BTC Price Target Strike-Distance Liquidity/Mispricing Probe v0

**Status:** `NEEDS_MORE_DATA` / `MARKET_AVAILABILITY_BLOCKED`

**Tag:** `polymarket-btc-price-target-v0-market-availability-blocked`

**Verdict classification:** NOT rejected. The hypothesis is untested because Phase 0
did not reach the actual hypothesis test — it was blocked by upstream market availability.

**Hypothesis:** BTC Price Target markets may have strike-adjacent CLOB liquidity pockets
near expiry because they expose numerical strike distance, unlike BTC Up/Down markets.
If two-sided CLOB depth survives near expiry at sufficient levels, a later v1 fair-value /
mispricing study (Chainlink/CLOB lag, probability model, distance decay) could be justified.

**Phase 0 probe result:**
| Metric | Value |
|---|---|
| BTC markets fetched from Gamma API | 100 |
| Active BTC Price Target markets found | 1 |
| Market | "Will bitcoin hit $1m before GTA VI?" |
| Strike | $1,000,000 (extracted via `1m` million pattern) |
| End date | 2026-07-31T12:00:00Z (months to expiry) |
| TTE bucket | >15m |
| Distance bucket | deep_below_strike (BTC ~$67k vs $1M strike) |
| CLOB orderbook status | EMPTY (404 Not Found for both YES and NO tokens) |
| Valid two-sided samples | 0 |
| Primary subset samples | 0 |
| Near-expiry / near-strike samples | 0 |
| Phase 0 verdict | NEEDS_MORE_DATA |

**Key result:** The lone active BTC Price Target market had no CLOB orderbook at all.
Without observable two-sided depth, no liquidity assessment, spread measurement, or
distance-to-strike grid analysis was possible. 48 orderbook polls across 120 seconds
all returned 404 from the CLOB API for both token IDs.

**Critical framing distinction:**

- **Design difference (confirmed):** BTC Price Target markets contain numerical strikes,
  making distance-to-strike buckets possible. This is structurally different from BTC
  Up/Down markets, which have no extractable strike price.
- **Empirical difference (untested):** It is not yet established that Price Target markets
  avoid the near-expiry depth wall that killed BTC Up/Down v0. The numerical strike
  unlocks the measurement grid but does not prove that liquidity survives. The near-expiry
  depth wall has not been ruled out for Price Target markets.

**What this does NOT mean:**
- The hypothesis is not rejected. Nothing about near-expiry strike-distance liquidity or
  mispricing was actually tested.
- Strike availability alone does not make the hypothesis promising.
- Price Target is not automatically rejected by Up/Down's depth wall — each market type
  must be evaluated independently.

**Reopen gate — near-expiry depth first:**
The first gate on reopen is near-expiry two-sided CLOB depth versus the $100 non-dust
floor. This must be checked before any distance-to-strike, fair-value, Chainlink/CLOB lag,
mispricing, or forward-return work. If the same depth wall appears (depth < $100 as
TTE shrinks below 15 minutes), Price Target falls to the same structural blocker as
Up/Down.

**Next actions:**
- Do not spend a full v1 build cycle on Price Target now.
- Park behind a cheap monitor/recheck loop for market availability.
- Reopen only when active BTC Price Target markets exist with observable two-sided
  CLOB books.
- On reopen, first test near-expiry depth against the $100 floor before any
  mispricing/fair-value work.

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

### Family 3 v1 — Funding × Falling-OI Unwind Mapping

**Tag:** `family3-funding-falling-oi-unwind-v1-rejected`

**Verdict:** `REJECTED`

**Hypothesis:** When BTCUSDT funding is in the bottom 5% of its past-only 180-day distribution
and OI is falling over the prior 8h window, the short unwind itself may continue into positive
BTC spot forward returns over 24h and 48h.

**Tested conditions:**
- Asset: BTCUSDT only
- Funding threshold: bottom 5% negative funding, past-only 180-day percentile
- OI regime: falling OI only
- OI change rule: `OI_end` = last row <= settlement_ts; `OI_start` = last row <= settlement_ts − 8h
- Return leg: BTC spot forward return only
- Horizons: 24h and 48h only
- Family size: exactly 2
- Cost: frozen 50 bps one-way / 100 bps round-trip primary cost
- Null: timestamp-shuffle null (1,000 shuffles, fixed seed 42)
- FDR: Benjamini-Yekutieli, alpha = 0.05, across exactly 2 cells
- Split: chronological 70/30 train/holdout
- Data: Binance Vision archive only (funding + OI metrics + spot klines)

**Preflight and bugfix note:**
An earlier Stage A `UNDERPOWERED_HOLDOUT_FAILURE` result was invalidated by a spot-kline
timestamp-unit parser bug. The Binance Vision spot klines archive changed from milliseconds
(13-digit, 2024 and earlier) to microseconds (16-digit, 2025+) at the 2025 year boundary.
The old parser used `datetime.fromtimestamp(ts / 1000)` and silently dropped all 2025+ rows
via `OverflowError`. After `parse_klines_timestamp()` with auto-detection was added, Stage A
became `POPULATION_SUFFICIENT` with 223 total events and 66 holdout events for both horizons.

Preflight confirmed:
- PREFLIGHT_PASSED
- 24h/48h identical spot-availability counts were data-driven, not code-driven
- Last spot timestamp: 2026-01-31 23:00 UTC
- Last event timestamp: 2026-01-09 00:00 UTC
- 0 boundary events (no event where 24h should pass but 48h should fail)
- Funding timestamps: all 5,937 rows use 13-digit ms timestamps (FUNDING_TIMESTAMP_MS_CONFIRMED)
- OI metrics parser: string-based `%Y-%m-%d %H:%M:%S` format
- OI alignment: 1,040 rows exactly at funding grid timestamps (OI_ALIGNMENT_GENUINE_CONFIRMED)

**Stage B results:**

| Cell | N | Holdout | Mean Gross (bps) | Mean Net (bps) | Win Rate | Worst Decile | Baseline Delta | Null p | BY FDR | Cell Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 24h | 223 | 66 | +54.86 | −45.14 | 0.395 | −491.72 | +47.30 | 0.010 | rejected | GATES_FAILED |
| 48h | 223 | 66 | +62.01 | −38.00 | 0.435 | −604.79 | +47.38 | 0.035 | survived | GATES_FAILED |

**Null / FDR interpretation:**
Both cells beat the timestamp-shuffle null (p = 0.010 / 0.035), meaning the falling-OI events
were statistically distinguishable from random eligible settlement timestamps. However,
"distinguishable" does not mean "profitable" — the cell means were still deeply negative
(−45 / −38 bps) after 100 bps round-trip cost. The baseline (all eligible funding settlements)
averaged −92 / −85 bps, so the falling-OI conditioning reduced losses but did not eliminate
them. A null pass cannot override failed economic gates.

**Verdict justification:**
Both cells failed the pre-null economic gates:
- `mean_net_bps < 0` (required > 0)
- `win_rate < 0.55` (required >= 0.55)
- `worst_decile_net_bps` deeply negative (required > −50)

The rejection is due to economic gate failure under the frozen 50 bps primary cost. This is
not a null rejection, not an FDR rejection — it is a GATES_FAILED rejection. The signal
direction is correct (positive gross returns at both horizons) but the magnitude after cost
is negative and inconsistent.

**What this rejects:**
- BTCUSDT negative-funding + falling-OI unwind v1 only
- Binance Vision archive only
- spot BTC return leg only
- 24h / 48h horizons only
- frozen 2-cell family only
- 50 bps primary cost only
- timestamp-shuffle null + BY FDR family size 2 only

**What this does NOT reject:**
- Multi-asset falling-OI unwind portfolio
- Perp return leg with funding-paid-while-held
- Cross-exchange OI divergence
- Tick-level OI or higher-frequency OI
- Lower-cost execution
- Maker/rebate cost model
- Altcoin-specific versions
- Venue-specific OI behavior outside Binance BTCUSDT
- Options IV/RV overlays
- Using falling-OI as a filter rather than a standalone trade

**Run artifacts:**
- Phase 0 / preflight commit: `e5e077cff7a81fcfb8b1bffb4f7040a5860b2463`
- Stage B commit: `1f0668053e82e9b72c9a1c27878cbe5ab2f3a60b`
- Stage B run: `funding_falling_oi_unwind_v1_20260519T020343_a62a61`
- Precommitment SHA: `c26c02281e2b27316b102fead1a09a4d4226af1ff9c150603695de68b41b58b1`
- Seed: 42
- Safety: `public_data_observer_only`

### Hyperliquid Multi-Asset Funding Carry Phase 0

**Tag:** `hyperliquid-multi-asset-funding-carry-phase0-killed-no-tail`

**Verdict:** `PHASE0_KILLED_NO_CROSS_SECTIONAL_FUNDING_TAIL`

**Study:** Hyperliquid multi-asset funding carry Phase 0.

**Signal family:** Single-venue cross-sectional perp funding carry.

**Venue:** Hyperliquid perps.

**Verdict classification:** This is a Phase 0 killed/no-tail entry, not a full
`REJECTED` strategy verdict. No v1 evaluator, strategy PnL, null, FDR, holdout,
execution, shadow executor, or bot path was used.

**Key result:** The real public Hyperliquid universe discovery/backfill path was
built and Phase 0A/0B diagnostics were run only. Phase 0B found the
cross-sectional funding spread tail absent under the frozen kill gates:

| Metric | Observed | Frozen Phase 0 gate |
|---|---:|---:|
| p50 cross-sectional spread | 0.035 bps | >= 3 bps |
| p90 cross-sectional spread | 0.194 bps | >= 10 bps |
| p95 cross-sectional spread | 0.257 bps | diagnostic only |

All 18 frozen grid cells were underpowered. `v1_unlocked: false`.

**What this closes:**
- Hyperliquid single-venue cross-sectional funding carry under the current
  2025-05-23 to 2026-05-22 archive window.
- Frozen Phase 0 grid/gates only.
- Current public-data funding archive and fee/accrual feasibility assumptions.

**What this does NOT close:**
- Different venue.
- Different cost model.
- Maker/rebate model.
- Longer historical archive if materially more symbols obtain longer coverage.
- Liquidation/cascade aftershock hypothesis.
- Cross-asset beta-lag stress hypothesis.
- Directional price-PnL or order-book microstructure hypotheses.

**Safety boundary:** Public data only. No orders, private keys, auth, live
execution, shadow executor, bot path, null/FDR, holdout, strategy PnL, or v1
precommitment was used.

### Hyperliquid BTC->LINK Fixed-Cell Paper Replay v0

**Tag:** `hyperliquid-btc-link-fixed-cell-paper-replay-v0-not-promoted`

**Status:** `PAPER_EXECUTION_DIAGNOSTIC_FAIL_NOT_PROMOTED`

**Study identity:** Hyperliquid BTC->LINK fixed-cell paper replay v0. This is a paper execution diagnostic for one fixed cell, not a formal v1 evaluation, not a null-tested research verdict, and not a broad rejection of Hyperliquid beta-lag.

**Signal family:** Cross-asset beta-lag / BTC stress into LINK perp.

**Venue/instrument:** Hyperliquid LINK perpetual, public S3/archive order-book replay only.

**Fixed cell:**
- Source/target: BTC -> LINK
- Source stress: BTC 60s bullish stress
- Target: LINK perpetual
- Horizon: 300s
- Execution model: taker/taker market entry and market exit
- Entry/exit prices: executable ask/bid order-book walk, first eligible snapshot at or after requested timestamp
- Notional: 100 USDC
- Fees: 4.5 bps taker per side, 9 bps total

**Branch / SHA:**
- Branch: `feat/hyperliquid-btc-link-v1-precommitment`
- Fix commit: `71930d3daed9cc12cbd2fd4424dfad9b1f301c1a`

**Invalidated prior result:** The earlier paper replay appeared to pass with `PAPER_EXECUTION_DIAGNOSTIC_PASS`, mean net about +39.47 bps, median net about +45.57 bps, win rate about 64.2%, and 123 valid round trips. That result is invalidated by `TIME_RANGE_LOADING_BUG`: the replay row-level time-range loader constrained book loading to the requested exit timestamp exactly. The first valid snapshot after the requested exit was 259 ms later and inside stale-book tolerance, but it was filtered out. The old replay skipped to a later snapshot at +6481 ms and used a better exit price. The integrity audit correctly selected the first eligible snapshot at or after the requested exit.

**Corrected paper replay:**
- Report: `reports/hyperliquid_paper_execution_v0/hyperliquid_paper_execution_v0_20260523T041109_616535_d65ce9`
- Total signals: 348
- Eligible signals: 348
- Valid round trips: 348
- Taker fills: 348
- Missed / no_book / insufficient_depth / stale: 0 / 0 / 0 / 0
- Mean raw return: +15.133364119729205 bps
- Median raw return: +11.571267621469698 bps
- Mean net return: +6.133364119729203 bps
- Median net return: +2.5712676214696986 bps
- Win rate: 0.5172413793103449
- Mean lower confidence bound: -8.866635880270797 bps
- Median lower confidence bound: -12.428732378530302 bps
- Pass criterion: false
- Recommendation: `PAPER_EXECUTION_DIAGNOSTIC_FAIL`

**Corrected replay-integrity audit:**
- Report: `reports/hyperliquid_replay_integrity_v0/hyperliquid_replay_integrity_v0_20260523T041155_929018_4887bb`
- Integrity status: `REPLAY_INTEGRITY_WARNINGS`
- Executable price violations: 0
- No-lookahead violations: 0
- Fee math violations: 0
- Timestamp violations: 0
- Unexplained drops: 0
- Count reconciliation: `valid_entry_valid_exit = 348`
- Warning flags: `FUNDING_NOT_INCLUDED_DIAGNOSTIC`, `REGIME_LIMITED_DIAGNOSTIC`, `SHADOW_EXECUTION_REQUIRED`

**Verdict:** Not promoted to v1. Not promoted to shadow. No live execution path. No bot authorization. The corrected executable ask/bid replay has small positive mean/median net returns but fails promotion because win rate is below the fixed gate and the mean lower confidence bound is negative.

**What this rejects:**
- Promotion of the exact BTC->LINK 60s bullish / 300s Hyperliquid taker/taker archive paper replay under current corrected executable-price assumptions.
- Use of the invalidated +39.47 bps paper pass as evidence.
- Maker/post-only rescue for this diagnostic. The prior post-only diagnostic had insufficient fills and cannot override taker/taker failure.

**What this does NOT reject:**
- Broader cross-asset beta-lag research.
- Other precommitted cells.
- Other horizons, if separately precommitted.
- Other target assets, if separately precommitted with family-size/FDR control.
- Live observer data as a separate future diagnostic.
- Improved funding-aware analysis.
- Shadow execution for a future candidate that passes corrected paper replay.
- Hyperliquid as a venue generally.

**Locked gate:** Do not revive this exact fixed-cell result using the invalidated +39.47 bps run. Any future revisit must cite the corrected replay as the baseline, use executable first-eligible snapshot semantics, include funding treatment or explicitly emit `FUNDING_NOT_INCLUDED_DIAGNOSTIC`, and define a fresh precommitment if adding data, assets, horizons, or execution models.

---

### Hyperliquid Supertrend 4h/1d Altcoin Perp v0

**Tag:** `hyperliquid-supertrend-4h1d-altcoin-perp-v0-closed`

**Verdict:** `PHASE0_CLOSED_INDICATOR_FAMILY_DIAGNOSTIC`

**Study identity:** Hyperliquid Supertrend 4h/1d altcoin perp v0 Phase 0. This is a scoped registry closure for the tested v0 indicator-family branch, not a broad Hyperliquid venue rejection, not a new strategy, not a v1 precommitment, and not authorized for execution.

**Original v0 branch / SHA:**
- Branch: `feat/hyperliquid-supertrend-4h1d-altcoin-perp-phase0`
- Commit: `dde42bbae1fff4d54a49d97f9a621abebfdfe4c4`

**Tested conditions:**
- Venue/instruments: Hyperliquid altcoin perpetuals
- Universe: frozen 20-symbol altcoin perp universe
- Timeframes: 4h and 1d
- Signal family: existing v0 Supertrend entry/exit mechanics only
- Cost/funding: fixed v0 cost assumptions and funding treatment
- Data: public data only
- Phase: Phase 0 feasibility framing only; no v1 evaluator, null/FDR/holdout, paper trading, shadow execution, or live execution was unlocked

**Original v0 Phase 0 result:**

| Timeframe | Status | Entries | Median gross bps | Median net primary bps |
|---|---|---:|---:|---:|
| 4h | `PHASE0C_NO_GROSS_EDGE` | 791 | -152.87848344544423 | -173.39852944544424 |
| 1d | `PHASE0C_GROSS_POSITIVE_NET_NEGATIVE` | 151 | +51.65811718806597 | -65.46250328485137 |

Overall v0 was previously reported as `PHASE0C_NO_GROSS_EDGE` because the 4h timeframe failed at the gross-return layer and the 1d timeframe failed net economics.

**Registry closure evidence:**
- Branch: `docs/hyperliquid-supertrend-v0-registry-closure`
- Commit: `c1ca12dd8f2caa0e5cc836a7c4b877dc14205ebb`

**Artifact recovery evidence:**
- Branch: `diag/hyperliquid-supertrend-v0-entry-artifact-recovery`
- Commit: `ed5c35c6a1157da042d835098bafa573dd6ae2de`
- Recovered report directory: `reports/hyperliquid_supertrend_4h1d_phase0/hyperliquid_supertrend_4h1d_phase0_20260524T172027_434119_20efb6`
- Recovered report directory SHA256: `08bd8e008c0780dd320f64f836abf3804f712129d428604c5eb1425f23346e52`
- Full per-entry artifact: `reports/hyperliquid_supertrend_4h1d_phase0/hyperliquid_supertrend_4h1d_phase0_20260524T172027_434119_20efb6/entries_full.csv`
- `entries_full.csv` SHA256: `53aa0230d2e2504b4750c39e6c5b4a2f7b261a94f6e9e8fa3c59e8b93c1cbe41`
- Recovered counts: 791 4h entries and 151 1d entries

Full per-entry artifacts were later recovered and reconciled exactly against the regenerated `summary.json`; no aggregate-only fabrication was used.

**Residual diagnostic evidence:**
- Branch: `diag/hyperliquid-supertrend-v0-1d-residual`
- Commit: `f889ef1d27c8bef99e10b5095383850e80271202`
- Output: `reports/hyperliquid_supertrend_v0_residual_diagnostic/20260524T172206Z`
- Entry input: recovered `entries_full.csv`
- Price archive: `data/hyperliquid_supertrend_4h1d_phase0/hourly_prices.csv`
- Price archive SHA256: `e8d642ba3a11af1935712818beb24be1a5d4c2d5905994c018db87db24cac0ec`

Residual diagnostic statuses emitted:
- `V0_RESIDUAL_DIAGNOSTIC_READY`
- `REGIME_CONCENTRATION_WARNING_DIAGNOSTIC`
- `EXIT_MECHANIC_PRIMARY_BLOCKER_DIAGNOSTIC`
- `FAMILY_CLOSURE_RECOMMENDED_DIAGNOSTIC`

**Closure rationale:**
- 4h fails at the gross-return layer: median gross was negative before net economics.
- 1d has positive median gross but negative median net under the fixed v0 assumptions.
- The 1d residual diagnostic found regime concentration plus Supertrend exit/giveback damage; the gross-positive 1d result is not promoted and must not seed a fitted follow-up.
- The tested v0 indicator-family branch is closed.

**What this closes:**
- This exact Hyperliquid Supertrend 4h/1d altcoin perp v0 design.
- The frozen 20-symbol universe.
- The existing Supertrend entry/exit mechanics.
- The existing v0 cost/funding assumptions.
- The Phase 0 feasibility framing for this branch.

**What this does NOT close:**
- Hyperliquid as a venue.
- All trend-following.
- All altcoin perps.
- Any separately precommitted literature-grounded trend hypothesis.
- Non-Supertrend exits, unless independently justified in a future precommitment.
- Order-book/microstructure hypotheses.
- Cross-asset beta-lag stress hypotheses.
- Liquidation/cascade aftershock hypotheses.
- Maker/rebate or lower-cost execution models, unless separately precommitted and not derived from this diagnostic.

**Locked gate:** Do not reopen this exact v0 by changing Supertrend parameters, ATR parameters, symbols, timeframes, date range, cost assumptions, `max_hold_bars`, or exit rules. Any future Hyperliquid altcoin indicator-family hypothesis must be independently justified from public literature or first principles. The residual diagnostic cannot be used to choose parameters, symbols, regimes, exit rules, or cost models.

**Safety boundary:** Public data only. No orders, private keys, auth, live execution, shadow executor, bot path, paper trading, null/FDR, holdout, strategy PnL, or v1 precommitment was used for this registry closure. No new experiments were run for this registry update.

---

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

11. **Cross-exchange funding dispersion carry v1** (Binance vs Bybit, BTC/ETH, thresholds 5/10/20/40 bps, holds 3/6/12 settlements, 50 bps cost, event-vector shift null, BY FDR). Zero dispersion events at every threshold for both assets. Maximum absolute spread was 4.49 bps — below the 5 bps minimum threshold. The data does not contain a tail to test. Do not reopen by merely lowering thresholds or adding venues within the v1 protocol. Revisiting requires a materially different precommitment — different venue pairs (OKX, three-venue), altcoins, stress-regime conditioning, sub-5 bps cost models, or a convergence-triggered exit mechanism.\n
12. **Family 3 funding × OI crowding regime v0** (Binance BTCUSDT funding extremes × OI change regime → spot BTC forward returns, 6 primary cells, rising OI only, 50 bps cost, failing-oi diagnostic). All 6 primary cells failed evaluation gates — mean net bps negative across all horizons (best: -19 bps for neg_extreme × rising_oi 48h). Signal absence at cost: the OI conditioning does not rescue the funding-extreme reversal hypothesis. Do not reopen without a materially different mechanism: tick-level OI, cross-exchange OI divergence, multi-asset OI conditioning, or a perp return leg with explicit funding-paid-while-held modeling. Do not reopen by merely changing thresholds, horizons, lookbacks, OI regime cutoffs, or cost.

13. **Family 3 v1 funding × falling-OI unwind mapping** — BTCUSDT negative-funding + falling-OI → spot BTC 24h/48h forward returns. Both primary cells GATES_FAILED at frozen 50 bps cost despite passing timestamp-shuffle null. Mean net remained negative (−45.14 / −38.00 bps), win rates were sub-threshold (0.395 / 0.435), and worst decile losses were large (−491 / −605 bps). Do not reopen this exact BTCUSDT 2-cell archive design by changing split, seed, horizons, OI cutoff, funding percentile, null iterations, or cost. Reopening requires a materially different mechanism: multi-asset portfolio, perp return leg with funding-paid-while-held, cross-exchange OI divergence, tick-level OI, or materially different fee/execution model.

14. **Hyperliquid BTC->LINK fixed-cell paper replay v0** — BTC 60s bullish stress → LINK perp, 300s horizon, 100 USDC taker/taker executable ask/bid replay. Corrected paper replay was `PAPER_EXECUTION_DIAGNOSTIC_FAIL`: mean net +6.13 bps, median net +2.57 bps, win rate 0.517, mean lower confidence bound −8.87 bps, pass criterion false. Do not revive this exact fixed-cell result using the invalidated +39.47 bps run. Any future revisit must cite the corrected replay as baseline, use first-eligible executable snapshot semantics, include funding treatment or explicitly mark `FUNDING_NOT_INCLUDED_DIAGNOSTIC`, and define a fresh precommitment if adding data, assets, horizons, or execution models.

15. **Hyperliquid Supertrend 4h/1d altcoin perp v0** — Frozen 20-symbol Hyperliquid altcoin perp Supertrend Phase 0. 4h failed at gross-return layer; 1d was gross-positive but net-negative and residual diagnostics found regime concentration plus Supertrend exit/giveback damage. Do not reopen this exact v0 by changing Supertrend parameters, ATR parameters, symbols, timeframes, date range, cost assumptions, `max_hold_bars`, or exit rules. Any future Hyperliquid altcoin indicator-family hypothesis requires independent public-literature or first-principles justification and cannot use the residual diagnostic to choose parameters, symbols, regimes, exit rules, or cost models.

## Still Open

|- **Cross-asset beta lag under stress** — BTC/ETH shock leads slower repricing in higher-beta assets over 30s to 5m. Distinct from same-asset derivatives-to-spot lead-lag. Only testable during genuine stress windows. See `CROSS_ASSET_BETA_LAG_PRECOMMITMENT.md`.
|- **Cross-asset beta lag archive v0** — BTC/ETH source stress (K=75 bps per-bar prefilter) to spot-target forward returns over the frozen archive window. Final verdict: `SIGNAL_ABSENCE_AT_COST`. 96 powered cells, 0 passed gates; best baseline-delta cell was BTCUSDT→LINKUSDT/60s/bullish/300000ms (+18.97 bps), but its mean net return was still negative (−31.35 bps) at the frozen 50 bps all-in cost. Null/FDR/holdout all failed or were absent because no cell cleared the economic gate. Do not reopen by changing thresholds, horizons, window, seed, null iterations, or cost within this v0 protocol. Reopening requires a materially different precommitment (different universe, different stress definition, different cost model, or a non-spot execution leg if explicitly allowed by jurisdiction and policy). Tag: `cross-asset-beta-lag-archive-v0-signal-absence-at-cost`.
|- **Cross-asset spot impulse v1** — Quiet/moderate-regime diagnostic only. BTC/ETH source movement during the actual capture was ~9-17 bps, below the 30 bps source-range gate required to test stress beta-lag. The 50 pairs with overlap all failed after the 50bps cost wall (best -43.98 bps), but this is diagnostic evidence from a quiet market, not a structural rejection. Open for a genuine volatile/stress-window retest only.
|- **Polymarket BTC Price Target strike-distance liquidity/mispricing** — Phase 0 probe found 1 active market with empty CLOB book. Hypothesis untested. Parked behind cheap market-availability monitor. Reopen gate: active strike-bearing markets with observable two-sided CLOB depth. First test on reopen: near-expiry depth vs $100 floor.
|- OI + price regime classification (filter, not standalone trade)
|- L2 adverse selection conditioning on book state
|- Options IV/RV regime overlay (filter, not trade)
|- **Funding × OI adjacent variants** — Family 3 v0 rejected the BTCUSDT rising-OI crowding-build mapping; Family 3 v1 rejected the BTCUSDT negative-funding + falling-OI unwind mapping at 50 bps on spot returns. Still untested: multi-asset falling-OI portfolios, perp return legs with funding-paid-while-held, cross-exchange OI divergence, tick-level OI, and materially lower-cost maker/rebate execution. These require fresh precommitments and cannot reuse the rejected BTCUSDT v1 grid.

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

### Hyperliquid Supertrend 4h/1d altcoin perp Phase 0

**Study ID:** `hyperliquid_supertrend_4h1d_altcoin_perp_v0`

**Tag:** `hyperliquid-supertrend-4h1d-altcoin-perp-phase0-killed-no-v1`

**Verdict label:** `PHASE0_KILLED_NO_V1_UNLOCKED`

**Branch:** `feat/hyperliquid-supertrend-4h1d-altcoin-perp-phase0`

**Commit SHA:** `dde42bbae1fff4d54a49d97f9a621abebfdfe4c4`

**Report:** `reports/hyperliquid_supertrend_4h1d_phase0/hyperliquid_supertrend_4h1d_phase0_20260524T053716_587572_3ae25f`

**Precommitment hash:** `91460cf160b6df75ed853bcd87642d8cfa8ceda07b5fa48576c5d529bf608bb1`

**Study:** Hyperliquid Supertrend 4h/1d altcoin perp Phase 0.

**Signal family:** Supertrend trend-following, ATR(10), multiplier 3.0.

**Venue:** Hyperliquid perpetuals.

**Universe:** Frozen 20 altcoin perps excluding BTC, ETH, and SOL: HYPE, XRP, DOGE, BNB, ADA, LINK, AVAX, SUI, TRX, LTC, BCH, TON, DOT, AAVE, UNI, APT, ARB, OP, SEI, INJ.

**Data window:** 2025-03-01T00:00:00Z to 2026-04-30T23:00:00Z.

**Rows:** 204,480 hourly price rows; 204,460 funding rows.

**Phase results:**

| Phase | Result |
|---|---|
| Phase 0A | `PHASE0A_PASSED` |
| Phase 0B 4h | `PHASE0B_PASSED` |
| Phase 0B 1d | `PHASE0B_PASSED` |
| Phase 0C 4h | `PHASE0C_NO_GROSS_EDGE` |
| Phase 0C 1d | `PHASE0C_GROSS_POSITIVE_NET_NEGATIVE` |
| Overall | `PHASE0C_NO_GROSS_EDGE` |
| v1 unlocked | false |

**Mechanism diagnostics:**

| Timeframe | Entries | Median gross bps | Median net primary bps | Interpretation |
|---|---:|---:|---:|---|
| 4h | 791 | -152.87848344544423 | -173.39852944544424 | Failed because there was no median gross edge. |
| 1d | 151 | 51.65811718806597 | -65.46250328485137 | Positive median gross, but failed after funding/cost under the frozen primary model. |

**Conclusion:** This frozen Phase 0 Supertrend v0 design does not justify a v1 precommitment. The result is a Phase 0 killed/no-v1-unlocked outcome, not a full strategy `REJECTED` verdict.

**What this closes:**
- The exact frozen Phase 0 Supertrend v0 design above.
- The frozen 4h/1d timeframes, ATR(10), multiplier 3.0, frozen universe, realized-volatility entry regime filter, and primary 10 bps round-trip model.

**What this does NOT globally reject:**
- Hyperliquid as a venue.
- Altcoin perpetuals generally.
- All trend-following mechanisms.
- Maker execution or different execution assumptions.
- Different timeframes.
- Different regime filters.
- Non-Supertrend mechanisms.

**Safety and scope:** No null, FDR, or holdout was run. No orders, private keys, authentication, live execution, shadow execution, or bot path was used. Funding was used only as a held-position return component. OI remained quarantined from signal, filter, and ranking behavior.


---

## CONFLICT_REQUIRES_HUMAN_REVIEW: harvested registry variant lines

These lines were present in at least one branch variant of REJECTED_RESEARCH.md but not exact-line-present in the selected canonical registry. They are preserved append-only for human reconciliation; no existing verdicts were deleted or rewritten during branch flattening.

### Variant `audit__edge-miner-six-phase-verification__examples__strategies__REJECTED_RESEARCH.md.md`

```text
- **35 REJECTED**
- **9 NEEDS_MORE_DATA** (insufficient events / zero signals)
|| DEX-CEX spot dislocation v1 | DEX pool price/volume/liquidity -> CEX forward returns | DEX Screener -> Kraken/Coinbase spot | NEEDS_MORE_DATA | 0 events from 70 snapshots, 2-min window too short, DEX search 5m volumes too stable | `dex-cex-v1-needs-more-data` |
||| Cross-asset spot impulse v1 | BTC/ETH spot impulse -> alt spot forward returns | Coinbase/Kraken/Binance spot | MARKET_MODERATE_DIAGNOSTIC | Best pair -43.98 bps (coinbase:ETH/USDT->coinbase:LINK/USD) in a quiet/moderate capture. BTC/ETH source movement during actual capture was only ~9-17 bps, below the 30 bps source-range gate required to test stress beta-lag. Not a full volatile-window rejection. | 2026-05-13 07:37-07:53 UTC, 906s capture, quiet/moderate market (BTC 12.6 bps, ETH 16.2 bps). 72 pairs, 11,890 signals, 50 pairs with overlap, 0 pairs with sufficient source/target movement. Diagnostic evidence only — open for volatile-window retest. |
|||| Polymarket BTC Up/Down short-expiry liquidity probe v0 | CLOB orderbook liquidity near expiry | Polymarket CLOB (BTC Up/Down binary options) | REJECTED | Near-expiry depth collapses below $100 non-dust floor: $89 at 5-15m → $13 at 0-30s. Spreads tight (1c median) but depth wall blocks Chainlink/CLOB lag hypothesis. Parser-confounded 98c finding corrected and superseded. | `polymarket-btc-updown-liquidity-v0-rejected` |
|||||| Polymarket complement arb | Same-condition YES/NO maker complement on Up/Down binary markets | Polymarket CLOB | FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE | 7 separated windows, 1565 detections, 1155 non-dust, 0 pessimistic paired fills at base 100-share quote. Trade evidence READY. Structural pincer: 5m lacks depth, 15m lacks queue turnover, 4h/daily has edge but zero fills. Queue-position/execution failure, not mathematical rejection. Size-ladder (5/10/25/50/100) inconclusive — replay blocked by missing cumulative_fillable_volume, fresh capture below sufficiency gates. | `polymarket-complement-arb-frozen` |
**Status:** `FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE` — queue-position/execution failure under current public-data, fee, and pessimistic maker-fill assumptions. This is NOT a mathematical rejection of complement arbitrage.
| Duration | 6 separated windows (60-720s each) |
| Poll interval | 2 seconds |
| Markets tested | BTC 5m, BTC 15m (×2 windows), BTC 1h, ETH 5m, ETH 15m, BTC 4h, BTC daily |
| Total detected | 1565 |
| Total non-dust | 1155 |
| Missing trade data events | 0 (all windows READY) |
| Pessimistic paired fills | 0 across all windows |
| Base quote size | 100 shares |
| Duration | Outcome | Explanation |
| BTC / ETH 5m | No economic depth | 0 non-dust opportunities |
| BTC / ETH 15m | Edges detected, zero fills | 27-35 detections, 24-93 non-dust, but queue never clears |
| BTC 1h | Edges detected, zero fills | 24 detections, 57 non-dust, same zero-fill result as 15m |
| BTC 4h / daily | Abundant edge, zero fills | 1473 detections, 978 non-dust, queue never clears |
The initial generic Gamma-discovered market set gave `PUBLIC_TRADE_EVIDENCE_INSUFFICIENT` because the global trade feed (no per-asset filter) did not contain the relevant tokens. After discovering that `market=<conditionId>` is the correct Data API filter parameter (not `conditionId=` or `asset=`), the targeted crypto Up/Down campaign achieved `TRADE_EVIDENCE_READY` in all windows. The earlier "filters silently ignored" conclusion was wrong — only the wrong parameter names were tested.
The `derivatives_lead_lag_v1` run tagged as `REJECTED_SPOT_SPOT_SMOKE` used:
* **Verdict:** `NEEDS_MORE_DATA`
```

### Variant `feat__edge-miner-offline-discovery-runner__examples__strategies__venue_agnostic_signal_observer__REJECTED_RESEARCH.md.md`

```text
- **36 REJECTED**
- **10 NEEDS_MORE_DATA** (insufficient events / zero signals / no tail)
| Polymarket BTC Up/Down short-expiry liquidity probe v0 | CLOB orderbook liquidity near expiry | Polymarket CLOB (BTC Up/Down binary options) | REJECTED | Near-expiry depth collapses below $100 non-dust floor: $89 at 5-15m → $13 at 0-30s. Spreads tight (1c median) but depth wall blocks Chainlink/CLOB lag hypothesis. Parser-confounded 98c finding corrected and superseded. | `polymarket-btc-updown-liquidity-v0-rejected` |
| **Cross-exchange funding dispersion carry v1** | Binance vs Bybit perp funding spread carry (short high/long low) | Binance Vision + Bybit v5 archive (BTC & ETH) | **NEEDS_MORE_DATA_OR_NO_TAIL** | Gate A (Stage 1): zero dispersion events at all frozen thresholds (5/10/20/40 bps) for both BTC and ETH. Absolute funding spread never exceeded 5 bps across 1,261 aligned settlements (2024-01 to 2025-05). BTC max: 4.14 bps; ETH max: 4.49 bps. Gate B, null, FDR, holdout never reached. | `cross-exchange-funding-dispersion-carry-v1-no-tail` |
```

### Variant `feat__hyperliquid-funding-divergence-phase0-rerun__examples__strategies__REJECTED_RESEARCH.md.md`

```text
- **1 PHASE0_KILLED** (`PHASE0_KILLED_NO_FUNDING_DIVERGENCE_TAIL`) — distribution kill before v1 precommitment/evaluator construction; not counted as a full strategy `REJECTED` verdict because no return-side evaluation was run.
||| Polymarket BTC Up/Down short-expiry liquidity probe v0 | CLOB orderbook liquidity near expiry | Polymarket CLOB (BTC Up/Down binary options) | REJECTED | Near-expiry depth collapses below $100 non-dust floor: $89 at 5-15m → $13 at 0-30s. Spreads tight (1c median) but depth wall blocks Chainlink/CLOB lag hypothesis. Parser-confounded 98c finding corrected and superseded. | `polymarket-btc-updown-liquidity-v0-rejected` |
| Hyperliquid BTC/ETH funding divergence Phase 0 | Hyperliquid venue-specific funding divergence / crowding-pressure distribution audit | Hyperliquid perps vs Binance/Bybit reference funding | PHASE0_KILLED_NO_FUNDING_DIVERGENCE_TAIL | BTC max abs divergence 5.86 bps and p99 1.08 bps; ETH max abs divergence 2.23 bps and p99 0.92 bps. Both assets were below the pre-registered kill thresholds of max < 10.0 bps and p99 < 8.0 bps. Distribution tail absent; no v1 precommitment should be written. | `hyperliquid-funding-divergence-phase0-killed-no-tail` |
| Source | Path | Rows | First timestamp | Last timestamp | Notes |
| Hyperliquid BTC | `examples/strategies/venue_agnostic_signal_observer/data/hyperliquid_funding_archive_phase0/hyperliquid_funding_BTC_2024-01-01_to_2025-05-30.jsonl` | 12,383 | 2024-01-01T00:00:00.151000Z | 2025-05-30T23:00:00.180000Z | detected rate basis: native_decimal_hourly_funding_rate |
| Hyperliquid ETH | `examples/strategies/venue_agnostic_signal_observer/data/hyperliquid_funding_archive_phase0/hyperliquid_funding_ETH_2024-01-01_to_2025-05-30.jsonl` | 12,383 | 2024-01-01T00:00:00.151000Z | 2025-05-30T23:00:00.180000Z | detected rate basis: native_decimal_hourly_funding_rate |
| Binance BTC | `examples/strategies/venue_agnostic_signal_observer/data/funding_dispersion_carry_v1_archives/binance_btc_funding.csv` | 1,551 | — | — | reference funding |
| Binance ETH | `examples/strategies/venue_agnostic_signal_observer/data/funding_dispersion_carry_v1_archives/binance_eth_funding.csv` | 1,551 | — | — | reference funding |
| Bybit BTC | `examples/strategies/venue_agnostic_signal_observer/data/funding_dispersion_carry_v1_archives/bybit_btc_funding.csv` | 1,552 | — | — | reference funding |
| Bybit ETH | `examples/strategies/venue_agnostic_signal_observer/data/funding_dispersion_carry_v1_archives/bybit_eth_funding.csv` | 1,552 | — | — | reference funding |
| Asset | Max abs divergence | Max threshold | p99 abs divergence | p99 threshold | Outcome |
| BTC | 5.86 bps | 10.0 bps | 1.08 bps | 8.0 bps | Below kill thresholds |
| ETH | 2.23 bps | 10.0 bps | 0.92 bps | 8.0 bps | Below kill thresholds |
No forward returns, PnL, price-path dependent variables, orders, private keys, auth, execution, shadow, bot, null, FDR, holdout, evaluator, or v1 precommitment were used. This entry is not recorded as a full strategy `REJECTED` verdict.
```

### Variant `fork__feat__hawkes-entropy-btc-link-v0__examples__strategies__venue_agnostic_signal_observer__docs__REJECTED_RESEARCH.md.md`

```text
||| **Family 3 v1 funding × falling-OI unwind mapping** | Binance BTCUSDT funding-rate extremes → spot BTC forward returns | Binance Vision archive (BTCUSDT USDⓈ-M metrics + funding + spot) | **REJECTED** | 2 primary cells, 24h and 48h. Stage A POPULATION_SUFFICIENT after ms/µs spot-kline parser fix and preflight. Both cells GATES_FAILED at 50 bps primary cost: 24h mean_net -45.14 bps, WR 0.395; 48h mean_net -38.00 bps, WR 0.435. Both beat timestamp-shuffle null (p=0.010 / 0.035), but remained economically negative. "Less bad than random," not tradeable. | `family3-funding-falling-oi-unwind-v1-rejected` |
||| **Cross-asset beta-lag archive v0** | BTC/ETH spot stress impulses → alt spot forward returns (SOL, LINK, DOGE, AVAX) | Binance Vision archive (aggTrades parquet) | **SIGNAL_ABSENCE_AT_COST** | 96 powered cells, 10,105 usable windows, 515K events. Best cell BTC→LINK/60s/bullish/300s: +18.34 bps baseline delta, -31.52 bps net at 50 bps cost. All cells fail null/FDR/holdout gates. Raw directional signal beats random entry but dies at the cost wall. Flip-point: ~18-20 bps round-trip. | `cross-asset-beta-lag-archive-v0-signal-absence` |
|- Stage B commit: `1f0668053e82e9b72c9a1c27878cbe5ab2f3a60b`
|- Stage B run: `funding_falling_oi_unwind_v1_20260519T020343_a62a61`
|- Precommitment SHA: `c26c02281e2b27316b102fead1a09a4d4226af1ff9c150603695de68b41b58b1`
|- Seed: 42
|- Safety: `public_data_observer_only`
| Total stress labels | 45,324 |
| Independent windows | 10,792 |
| All-target usable windows | 10,105 |
| Total valid forward-return events | 515,412 |
| Cell | Baseline Delta | Mean Net | N |
| BTCUSDT→LINKUSDT/60s/bullish/300s | **+18.34 bps** | **-31.52 bps** | 809 |
```

### Variant `kraken-v6-market-structure-scanner__examples__strategies__REJECTED_RESEARCH.md.md`

```text
| Derivatives lead-lag v1 | notional burst, price shock, signed imbalance | Coinbase→Kraken BTC | REJECTED | Best -18.74 bps, win rate 0% | — |
```


### hyperliquid-oi-velocity-compression-breakout-phase0

**Verdict:** `REJECTED_PHASE0_MECHANISM_FAILURE`

**Final Phase 0C status:** `PHASE0C_DIRECTION_PROXY_UNSTABLE`

**Report directory:** `reports/hyperliquid_oi_velocity_compression_phase0/20260524T041302Z`

Phase 0A and Phase 0B passed, but Phase 0C failed due to unstable direction proxy agreement and no directional signed edge. The event population exists, but the mechanism is not directionally stable; do not build v1.

Evidence:

| Check | Result |
| --- | ---: |
| Phase 0A usable symbols | 34 |
| Phase 0A missing symbols | PEPE only |
| Phase 0B events | 31,508 |
| Max single-symbol concentration | 0.03199 |
| Phase 0C direction proxy disagreement rate | 0.49943 |
| 1h hit rate | 0.4816 |
| 1h median signed return | 0.0 bps |
| 4h hit rate | 0.4901 |
| 4h median signed return | 0.0 bps |
| 12h hit rate | 0.4901 |
| 12h median signed return | -0.3111 bps |
| 12h median absolute return | 17.1898 bps |

Safety: public-data observer/research only. No orders, auth, private keys, live trading, shadow executor, or bot path were used.
