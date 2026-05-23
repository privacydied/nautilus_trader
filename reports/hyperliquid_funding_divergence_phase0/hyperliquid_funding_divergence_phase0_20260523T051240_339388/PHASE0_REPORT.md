# Hyperliquid funding divergence Phase 0 audit

## 1. Study identity
Study ID: hyperliquid_funding_divergence_phase0. Distribution-only funding-divergence audit.

## 2. Not a precommitment
This is not a precommitment, not an evaluator, not a return study, not a PnL study, not shadow execution, and not a bot path.

## 3. Kill criteria written before histogram
The runner records pre_data_kill_criteria before loading any data. Module section constants define: `KILL_CRITERION_MAX_ABS_BPS = 10.0` and `KILL_CRITERION_P99_ABS_BPS = 8.0`.

## 4. Data sources and coverage
[]

## 5. Data-source provenance table
See manifest.json data_source_provenance for first row timestamp, last row timestamp, row count, source sha256, detected timestamp unit, and detected native funding interval.

## 6. Alignment method and no-lookahead proof
Each Hyperliquid timestamp aligns to the latest reference timestamp where reference_timestamp <= hyperliquid_timestamp. Future rows are never eligible; no interpolation or covering-interval logic is used.

## 7. Funding normalization method
hourly_funding_bps = native_funding_rate * 10000 / native_interval_hours. projected_8h_funding_bps = hourly_funding_bps * 8.

## 8. Distribution table
[]

## 9. Kill decision table
BTC p99 vs 8 bps: None
BTC max vs 10 bps: None
ETH p99 vs 8 bps: None
ETH max vs 10 bps: None

## 10. Single-asset distribution-ready interpretation
If exactly one asset clears, future v1 must predeclare one-asset vs two-asset scope and count family size accordingly. Do not silently drop the failing asset later.

## 11. Persistence / half-life
Reported in persistence_half_life.csv with n_observations, n_censored, and percentile suppression where n < 30.

## 12. Calendar stratification
Descriptive only. Calendar conditioning cannot create evaluation cells in Phase 0. If used later, it must be frozen in a separate v1 precommitment and counted in family size / FDR.

## 13. Non-coverage
This is separate from Family 2 funding crowding reversal, Family 3 funding x OI variants, and the Hyperliquid BTC->LINK fixed-cell paper replay.
The invalidated +39.47 bps BTC->LINK replay result is not evidence for this funding-divergence hypothesis.
The corrected +6.13 bps BTC->LINK replay result is not evidence against this funding-divergence hypothesis.
BTC->LINK replay involved cross-asset beta-lag. This audit involves same-asset funding divergence and venue-specific crowding pressure.

## 14. Self-test paragraph
If this hypothesis advances to v1 and the v1 return leg is BTC spot, the separation risks laundering Family 2 / Family 3 unless the load-bearing mechanism is explicitly the venue-specific crowding pressure on Hyperliquid identified via HL-vs-reference funding divergence, not BTC spot funding extremes alone. Prefer a Hyperliquid perp return leg, Hyperliquid venue crowding, explicit funding-paid-while-held treatment, or another clearly distinct mechanism.

## 15. Final Phase 0 status
PHASE0_SOURCE_UNAVAILABLE

## 16. Next-step rule
No evaluator may be written from this Phase 0 report alone.

## Appendix: branch handling
remote branch feat/hyperliquid-funding-divergence-phase0 absent at start; created from develop

Kill decision: source unavailable
