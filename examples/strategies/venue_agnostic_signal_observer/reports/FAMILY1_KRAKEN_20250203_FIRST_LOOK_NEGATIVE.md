# Family 1 Kraken Same-Venue Quote-Basis: First-Look Negative

**Status:** `UNRESOLVED, FIRST-LOOK NEGATIVE`
**Label:** `HOLDOUT_REPLICATION_FAILED_ON_UNDERPOWERED_TICK_SLICE`
**Date:** 2026-05-18
**Branch:** `feat/edge-miner-offline-discovery-runner`
**Commit:** `0e369edd66`
**Precommitment hash:** `b9aba30a3f5830cae924e5383ceb4e75f119bbed442e5bac1b8a624e31eb47ef`

---

## Hypothesis

> On Kraken, when BTC/USD and BTC/USDT diverge (widening the quote basis), the subsequent reversion or continuation can be predicted with enough edge to overcome fees on a same-venue spot basis trade.

## Experiment

| Parameter | Value |
|---|---|
| Family | 1 — same-venue USD/USDT quote-basis |
| Venue | Kraken |
| Date | 2025-02-03 |
| Slice | 2025-02-03 00:00:01 to 03:01:57 UTC (~3 hours overlap) |
| Resolution | trade/tick |
| Streams | XXBTZUSD (BTC/USD), XBTUSDT (BTC/USDT) |
| Rows (USD) | 29,988 |
| Rows (USDT) | 5,377 |
| Lookbacks | 30s, 60s, 120s |
| Horizons | 60s, 300s |
| Cost model | 10 bps fees + 5 bps slippage + 2 bps quote mismatch |
| Train/holdout split | 70/30 temporal |

## Pipeline Results

| Phase | Status |
|---|---|
| Phase 1 prepare | PASSED |
| Phase 2A stress-window index | `OFFLINE_STRESS_INDEX_READY` — 21 promotable windows |
| Phase 2B-1 discovery plan | `OFFLINE_DISCOVERY_PLAN_READY` — 6 Family 1 cells |
| Phase 2B-2A train evaluation | `TRAIN_RAW_EDGE_SCREEN_READY` — 3 cells positive |
| Phase 2B-2B1 survivor freeze | `OFFLINE_TRAIN_SURVIVOR_FREEZE_READY` — 3 survivors |
| Phase 2B-2B2 holdout evaluation | `OFFLINE_HOLDOUT_EVALUATION_READY` — all 3 evaluated, `event_raw_bps` and `event_net_bps` present |
| Phase 2B-2C1 train-holdout comparison | `NO_HOLDOUT_SURVIVORS` — all 3 failed replication |
| Phase 2B-2C3A native null | `NO_NULL_ELIGIBLE_CELLS` — zero cells survived comparison |
| Phase 2B-2C2 FDR correction | Not applicable — no p-values existed to correct |

## Comparison Detail

All 3 survivor cells failed holdout replication with identical metrics:

| Cell | Train net bps | Train win rate | Train events | Holdout net bps | Holdout win rate | Holdout events |
|---|---|---|---|---|---|---|
| 30s×60s | +119.88 | 64.3% | 14 | -157.78 | 42.9% | 7 |
| 60s×60s | +119.88 | 64.3% | 14 | -157.78 | 42.9% | 7 |
| 120s×60s | +119.88 | 64.3% | 14 | -157.78 | 42.9% | 7 |

Failure reasons per cell:
- `below_min_holdout_net_mean_bps` (-157.78 ≤ 0.0)
- `below_min_holdout_net_median_bps` (-41.13 < 0.0)
- `below_min_holdout_win_rate` (42.9% < 50%)

## Caveats

### 1. Underpowered holdout (7 events)

The holdout had only 7 events. This is consistent with overfitting, but the holdout sample is too small to characterize the effect with high confidence. With only 7 events, a single large negative outlier can dominate the mean (-1104 bps worst net). Re-running with more holdout windows would distinguish genuine replication failure from small-sample noise.

### 2. Evaluator/resolution mismatch

The train and holdout evaluators were originally bar-resolution gated (`RESOLUTION_BAR` only). During the run they were widened to accept `RESOLUTION_TRADE` and `RESOLUTION_AGG_TRADE`. This was described as a gate fix, not an algorithmic change, but it still matters for interpretation. This run tested Family 1 through a bar-designed evaluator that was widened to accept trade/tick data. The result is conditioned on that instrumentation choice. A future run should decide explicitly whether Family 1 is a bar hypothesis or a tick hypothesis, then use an evaluator designed for that resolution.

### 3. Lookback parameter appeared inert

All three survivor cells produced bit-identical train and holdout metrics across 30s, 60s, and 120s lookbacks. This suggests either the lookback parameter was not materially affecting the evaluation path, or all three cells collapsed onto the same effective event set. The effective cell count may be closer to one. The lookback wiring or cell deduplication should be investigated before using this grid for a powered verdict run.

## Known Data Limitation

XXBTZUSD was roughly 10x denser than XBTUSDT (7,500 vs 700 trades/hour). This matters for quote-basis interpretation because the basis series is effectively sampled at the slower USDT leg's rate. A null result may be real, but it may also reflect the slower leg blurring a faster effect. Not a blocker, not a proof of overfit — a genuine methodological caveat.

## Verdict

**Unresolved, first-look negative.**

Family 1 Kraken BTC/USD-BTC/USDT quote-basis produced a clean first-look negative on this precommitted 2025-02-03 slice. Train-positive cells failed holdout replication, native null had no eligible cells, and FDR was not applicable. Because the holdout had only 7 events, the evaluator was bar-designed but run on tick data, and the lookback grid appeared inert, this result should be archived as unresolved and mildly discouraging, not as a locked-gate death of the hypothesis.

One properly instrumented, multi-window Family 1 run should decide whether this becomes a locked-gate rejection.

## What This Does Not Prove

- Family 1 is not globally dead or rejected.
- FDR did not reject anything — it was not applicable because zero cells survived the comparison gate.
- The signal is not definitively proven overfit; it is consistent with overfitting on a small holdout.
- The three lookback configurations are not three independent tests; bit-identical metrics suggest effective duplication.

## Recommended Next Experiment

Re-precommit Family 1 with a clear resolution choice. Either make it explicitly bar-based and run on Kraken OHLCVT/bar data, or make it explicitly tick-based with an evaluator designed for tick data. Then run multiple historical windows with enough holdout events to distinguish replication failure from small-sample noise. Confirm that lookback changes alter the event set or metric path before running the verdict.

## Artifacts

- Prepare manifest: `reports/.../offline_historical_prepare/offline_prepare_20260518_015032/offline_prepare_manifest.json`
- Stress window manifest: `reports/.../offline_stress_windows/offline_stress_window_index_20260518_015103/stress_window_manifest.json`
- Discovery plan: `reports/.../offline_discovery_plan/offline_discovery_plan_20260518_015416/offline_discovery_plan.json`
- Train evaluation: `reports/.../offline_train_evaluation/offline_train_evaluation_20260518_020154/offline_train_evaluation.json`
- Survivor freeze: `reports/.../offline_train_survivor_freeze/offline_train_survivor_freeze_20260518_020233/offline_train_survivor_freeze.json`
- Holdout evaluation: `reports/.../offline_holdout_evaluation/offline_holdout_evaluation_20260518_020557/offline_holdout_evaluation.json`
- Train-holdout comparison: `reports/.../offline_train_holdout_comparison/offline_train_holdout_comparison_20260518_020618/offline_train_holdout_comparison.json`
- Native null: `reports/.../offline_native_null/offline_native_null_20260518_020710/offline_native_null.json`
- FDR correction: N/A (no p-values existed)
