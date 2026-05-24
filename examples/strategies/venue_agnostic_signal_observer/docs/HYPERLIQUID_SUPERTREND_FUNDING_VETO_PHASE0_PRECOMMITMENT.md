# Hyperliquid Supertrend Funding Veto Phase 0 Precommitment

**Study ID**: `hyperliquid_supertrend_funding_veto_phase0`

## Relation to v0 Supertrend Phase 0
This study builds directly on the v0 Hyperliquid Supertrend Phase 0 work
(`feat/hyperliquid-supertrend-4h1d-altcoin-perp-phase0`). It reuses the exact
same venue, frozen 20‑symbol altcoin perp universe, archive window
`2025-03-01` → `2026-04-30`, Supertrend parameters, cost model, and funding
source.

## Baseline v0 result being tested
* 1d timeframe: median gross ≈ +51.66 bps, median net ≈ ‑65.46 bps.
* Gross‑vs‑net gap ≈ 117 bps, with an explicit cost model of 10 bps.
* Funding median accrual ≈ ‑10.73 bps (bleed magnitude ~10.73 bps).

## Causal claim
The net‑negative outcome is primarily caused by systematic adverse funding
accrual on entries. Introducing a funding‑aware entry veto should reduce the
funding‑bleed component while preserving most of the gross trend edge.

## Phase A stop rules
1. Verify v0 precommitment hash matches manifest entry.
2. Verify manifest marks `precommitment_hash_verified=true`.
3. Verify report git SHA and branch metadata point to the expected v0 commit.
4. Verify archive window exactly `2025-03-01` → `2026‑04‑30`.

If any check fails, abort with `PHASE0_BASELINE_INTEGRITY_FAILED`.

## Phase B veto rule
* For long entries: veto if the most‑recent settled funding rate exceeds the
  75th percentile of that symbol’s past‑only funding distribution (≥ 30 days).
* For short entries: veto if the most‑recent settled funding rate is below the
  25th percentile.
* Use signed funding values; positive funding is adverse for longs, negative for
  shorts.
* If no sufficient funding history, mark `funding_percentile_unavailable=true`
  and retain the entry.

## Funding lookup rule
Use the most recent settled funding timestamp ≤ the entry bar‑close timestamp.
Do **not** use predicted or future funding rows.

## Signed percentile rule
Percentiles are computed per symbol on signed historical funding values.
A minimum of 30 calendar days of settled observations is required for a valid
percentile; otherwise the entry is retained.

## Unavailable funding‑history handling
Entries with insufficient history are retained, counted in `n_unavailable_funding_history`,
and annotated accordingly.

## Frozen predictions (P1‑P7)
1. Overall veto rate must be between 15 % and 60 %.
2. Median funding bleed must improve by at least max(20 bps, 50 % of baseline bleed).
3. Median gross must remain positive and ≥ max(20 bps, 50 % of baseline median gross).
4. Median net must improve by ≥ 40 bps; if still ≤ 0 bps emit `PHASE0_FUNDING_VETO_CAUSAL_DIAGNOSTIC_PASS`.
5. Retained 1d entries ≥ 50.
6. Each direction must retain ≥ 25 % of its baseline entries.
7. At least 7 symbols must contribute ≥ 3 retained entries.

## Verdict taxonomy & resolution order
(see task description for full list). The highest‑priority applicable verdict will
be recorded.

## Safety boundary
* Public/archive data only.
* No orders, private keys, auth, live execution, shadow executor, or bot paths.
* No parameter tuning after seeing results.
* No universe substitution or timeframe fishing.

## Output schema
Artifacts will be written under `reports/hyperliquid_supertrend_funding_veto_phase0/<run_id>/`:
* `phase_a_attribution.json`
* `funding_decomposition_baseline.json`
* `summary.md`
* (Phase B artifacts if the study proceeds).

## Locked‑gate distinction
This study does **not** reopen any locked gates (9‑13). Funding is used *solely*
as a cost‑control filter on an independent Supertrend signal.
