# Cross-Asset Beta-Lag Archive v0 — Precommitment

**Study ID:** `cross_asset_beta_lag_archive_v0`

**Date frozen:** 2026-05-19

**Status:** FROZEN — no parameter changes after first data fetch.

---

## Hypothesis

BTC/ETH spot stress impulses lead delayed repricing in higher-beta spot assets
(SOL, LINK, DOGE, AVAX) over 30s to 5m forward horizons. This is an archive
backfill of the live cross-asset beta-lag stress thread, using deterministic
historical archive stress labels instead of waiting for a live volatile window.

## Structural-Change Rationale

Distinct from rejected same-asset cross-venue lead-lag and rejected
derivatives-source spot lead-lag because:
- Source and target assets differ (BTC/ETH → alts)
- Source stress is BTC/ETH spot impulse, not perp flow/OI/funding
- Target effect is beta-lag repricing in alt spot assets
- Stress windows are deterministic archive events, not tuned after outcomes

## Data Source

**Binance Vision public archive only** — no authenticated endpoints, no REST
live ticker fallback, no exchange account assumptions.

Preferred archive source:
- Spot daily `aggTrades` for tick-level evaluation
- Spot daily 1m klines for stress-day prefiltering only (optional)

## Symbols

**Sources:**
- `BTCUSDT`
- `ETHUSDT`

**Targets:**
- `SOLUSDT`
- `LINKUSDT`
- `DOGEUSDT`
- `AVAXUSDT`

**Venue:** `binance_spot_archive`

## Calendar

**Window:** `2024-01-01T00:00:00Z` through `2026-04-30T23:59:59Z`

If archive availability cannot support all six symbols across the full window,
the largest common complete calendar range is used only if determined from
availability metadata before any outcome evaluation.

If common range < 90 calendar days → `NEEDS_MORE_DATA_ARCHIVE_AVAILABILITY`.

## Stress Labels

Use existing rolling-price-move rules with parameters:

| Rule | Lookback | Threshold | Direction |
|------|----------|-----------|-----------|
| 30s absolute source move | 30s | ≥ 30 bps | sign of move |
| 60s absolute source move | 60s | ≥ 50 bps | sign of move |

- Exactly zero source move → ignored
- Dedup cooldown: 30s per source symbol and direction
- Independent window separation: ≥ 30 minutes

## Evaluation Cells

**Primary FDR family dimensions:**
- Source asset: BTC, ETH
- Target asset: SOL, LINK, DOGE, AVAX
- Stress window: 30s, 60s
- Source direction: bullish, bearish
- Forward horizon: 30s, 60s, 300s

**Primary family size:** 2 × 4 × 2 × 2 × 3 = **96 cells**

## Entry Delay

Fixed 1 second after stress label timestamp. No grid-search.

## Cost Model

Observer-standard all-in target-side cost:
- fee_bps = 40
- slippage_bps = 5
- quote_mismatch_buffer_bps = 5
- **Total cost floor = 50 bps**

## Directional Score

- **Bullish source:** `score_bps = target_forward_return_bps - cost_bps`
- **Bearish source:** `score_bps = -target_forward_return_bps - cost_bps`
- Bearish cells are `diagnostic_only_spot_short_not_assumed`

## Minimum Events

- Cell minimum: ≥ 50 valid events
- Holdout minimum: ≥ 20 valid events
- If insufficient → `NEEDS_MORE_DATA_CELL`
- Corpus minimum: ≥ 20 independent all-target stress windows

## Train / Holdout Split

- Chronological 70/30 by stress label timestamp
- No random split, no post-hoc changes

## Baseline

- Matched random baseline within same archive stress-window population
- Same source/target, same count, random eligible timestamps
- Seed 42

## Null Test

- Circular time shift of source event timestamps within same-day complete coverage blocks
- Preserve event count and clustering
- 1000 iterations, seed 42
- p = (count_null_ge_real + 1) / (iterations + 1)

## FDR

- Benjamini-Yekutieli, alpha = 0.05
- Across exactly 96 primary cells with valid null p-values
- Underpowered cells excluded from p-value/FDR denominator

## Economic Gates

All must pass:
1. valid_event_count >= 50
2. mean_net_bps > 0
3. median_net_bps > 0
4. win_rate >= 0.55
5. worst_decile_net_bps > -50
6. baseline_delta_bps >= 10
7. null_p <= 0.05
8. BY FDR survived
9. Holdout mean_net_bps > 0
10. Holdout win_rate >= 0.55 (if n >= 20)

## Family Verdict Taxonomy

| Verdict | Meaning |
|---------|---------|
| `CANDIDATE_FOR_LONGER_OBSERVATION_ARCHIVE_ONLY` | ≥1 cell passes all gates |
| `REJECTED_ARCHIVE_STRESS_BETA_LAG_V0` | Population powered, all cells fail |
| `SIGNAL_ABSENCE_AT_COST` | Powered but mean net ≤ 0 after 50 bps |
| `NEEDS_MORE_DATA_ARCHIVE_STRESS_WINDOWS` | <20 stress windows or insufficient cells |
| `NEEDS_MORE_DATA_ARCHIVE_AVAILABILITY` | Insufficient archive coverage |
| `ARCHIVE_RUN_INVALIDATED_BUG_COMPROMISED` | Bug found post-run |

Never `TRADE_READY`, `EXECUTION_READY`, or `BOT_READY`.

## Event-Vector Reconciliation

Summary stats must reproduce from raw event rows before any null/FDR/holdout.
If reconciliation fails → `ARCHIVE_RUN_INVALIDATED_BUG_COMPROMISED`.
