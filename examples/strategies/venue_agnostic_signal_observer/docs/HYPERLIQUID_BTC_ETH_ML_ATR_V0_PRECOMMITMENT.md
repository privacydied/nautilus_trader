# HYPERLIQUID BTC/ETH ML+ATR v0 — Precommitment

> **Project context:** This study sits alongside
> [DERIVATIVES_V2_CAPTURE_RUNBOOK.md](DERIVATIVES_V2_CAPTURE_RUNBOOK.md)
> as broader capture/research pipeline context.
> This study sits alongside
> [audit-20260526-report-v1.md](audit-20260526-report-v1.md)
> as latest project audit context.
> Neither document is modified by this precommitment.

---

## Frozen v0 Design

| Field | Value |
|-------|-------|
| study_id | `hyperliquid_btc_eth_ml_atr_v0` |
| venue | Hyperliquid perps |
| symbols | BTC, ETH |
| timeframe | 1h bars |
| spec_version | v0 |

### Explicitly Out of Scope for v0

- LINK (Hyperliquid liquidity/history insufficient; revisit after BTC/ETH v0 ships)
- Multi-timeframe features
- Ensemble models
- Regime detection
- Position sizing beyond fixed unit
- Intrabar execution simulation
- LightGBM / boosted trees
- Feature expansion beyond the frozen set

### Explicit Non-Goals for v0

- **The goal is not to find a profitable strategy.**
- The goal is to determine whether a simple ML+ATR pipeline produces a calibrated, non-leaking diagnostic baseline on Hyperliquid BTC/ETH data.
- Economic gates passing is informative, not the objective.

### Scope of What Tests Can Prove

- Passing tests proves the pipeline is deterministic, respects frozen alignment rules, and has no detected leakage under the test fixtures.
- Passing tests does **not** prove the strategy has edge.
- Pipeline correctness and strategy edge are separate claims and must never be conflated in writeups.

---

## Data Splits

| Split | Range | Min Rows |
|-------|-------|----------|
| Train | late 2023 through 2024-12-31 23:59:59 UTC | 2000 |
| Validation | 2025-01-01 00:00:00 UTC through 2025-06-30 23:59:59 UTC | 500 |
| Test | 2025-07-01 00:00:00 UTC through latest complete archive timestamp | 500 |

No-overlap audit: `max(train.timestamp) < min(validation.timestamp)` and `max(validation.timestamp) < min(test.timestamp)`.

---

## Model & Label

- **Model:** Logistic regression
- **Label:** `label_up = close_{t + N} > close_t`, default N=24 bars (strict greater-than; ties → 0)
- **Probability target:** Probability of positive forward return
- **Entry/label mismatch (intentional):** Label uses `close_t` to `close_{t+N}`. Entry executes at `open_{t+1}`. This mismatch is documented and frozen.

---

## Calibration

- **Default:** Validation-only Platt scaling (parameters `a`, `b`)
- **Isotonic:** Allowed only behind explicit `--calibrator isotonic` opt-in
- **Thresholds (frozen before seeing validation calibration):**
  - Long if `calibrated_p_up >= 0.55`
  - Short if `calibrated_p_up <= 0.40`
- v0 intentionally freezes 0.55/0.40 before seeing the validation calibration curve. Do not select thresholds from validation in v0. Relaxing this requires a separate precommitment.

---

## Exits

- ATR stop + ATR trailing only. No fixed take profit.
- Default ATR lookback: 14 bars
- Default stop: 2.0 × ATR
- Default trailing: 3.0 × ATR
- ATR at entry = ATR_14h computed at signal bar close `t`, not entry bar `t+1`.
- Trailing stop ratchets only on bar close, not intrabar.
- Trailing stop ATR is frozen at signal bar close and does not update while the trade is open.
- Exit reasons: `initial_stop`, `trailing_stop`, `initial_stop_at_entry`, `end_of_data`

---

## Costs

- `fee_bps_per_side` configurable (default 1.0)
- `slippage_bps_per_side` configurable (default 0.5)
- Funding: actual historical Hyperliquid funding rows
- **Hyperliquid funding sign convention:** Positive `funding_rate` means longs pay shorts.
- Reference rates: Taker ~1 bps/side, Maker ~0 bps/side (as of 2025-01)
- Config values are authoritative; do not silently track exchange fee changes.

---

## Data Provenance

- **Source:** Hyperliquid public archive (S3-backed per [DERIVATIVES_V2_CAPTURE_RUNBOOK.md](DERIVATIVES_V2_CAPTURE_RUNBOOK.md))
- **Tooling:** `hyperliquid_s3_archive.py`, `hyperliquid_funding_archive_backfill.py`
- **Disk location:** User-specified `--bars-path` and `--funding-path`
- **Hash recording:** SHA256 of every input file recorded in `manifest.json` and `INPUTS.md`
- **Assumptions:** Public/archive data only. No private or authenticated data.

---

## What Would Invalidate This Study

1. Funding lookahead discovered
2. Feature lookahead discovered
3. Timestamp misalignment above tolerance (>0.1% of bars)
4. OHLC sanity rejection above tolerance (>0.1% of input rows)
5. Funding unit/sign/cadence failure
6. Calibration failure
7. Validation AUC below threshold (<0.51)
8. Degenerate labels (<30% or >70% positive in any split)
9. Test gates failed
10. Side coverage failed
11. **Any test in the test suite modified to make a failing run pass.**
    Tests are part of the frozen spec. Weakening tests to pass a bad run is a precommitment violation.
12. Response to any of the above: freeze, write up findings, do not iterate on test.

---

## Interpretation Guide

### Statuses Explained

| Status | Meaning |
|--------|---------|
| `ML_ATR_V0_BACKTEST_READY` | **Transient/internal only.** Used during execution before final gate evaluation. Must **not** appear in a completed `summary.json`. |
| `ML_ATR_V0_NEEDS_MORE_DATA` | Insufficient rows in one or more splits after feature/label filtering. |
| `ML_ATR_V0_VALIDATION_CALIBRATION_FAILED` | Validation AUC < 0.51, single-class validation, or calibration error. Pipeline stops before test backtest. |
| `ML_ATR_V0_INSUFFICIENT_TEST_TRADES` | Fewer test trades than `min_test_trades`. |
| `ML_ATR_V0_TEST_ECONOMIC_GATES_FAILED` | Test trades exist but fail economic diagnostics (net bps, win rate, profit factor, side coverage). |
| `ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE` | All v0 gates pass. **Does NOT authorize shadow logging.** It only permits drafting a separate shadow-logging precommitment. |
| `ML_ATR_V0_ERROR_INVALID_INPUT` | Data integrity violation (OHLC, timestamps, gaps, labels, symbols). |
| `ML_ATR_V0_ERROR_LOOKAHEAD_AUDIT_FAILED` | Split boundaries overlap (train/val or val/test). |

### Critical Interpretation Notes

- `ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE` does **NOT** authorize shadow logging. It only permits drafting a separate shadow-logging precommitment.
- `ML_ATR_V0_BACKTEST_READY` is transient/internal only and must **not** appear in completed `summary.json`.
- Passing v0 does not mean the strategy is profitable or tradeable.

---

## Promotion Path

1. Passing v0 gates only permits **writing up findings**.
2. A separate v1 precommitment is required before any v1 work.
3. **No automatic shadow, paper, bot, or live unlock.**
4. The output of v0 is information. Decision to proceed is a separate human step.

---

## Gate Ordering

Evaluate gates top-to-bottom. First failure determines status:

1. Data integrity → `ML_ATR_V0_ERROR_INVALID_INPUT`
2. Lookahead audit → `ML_ATR_V0_ERROR_LOOKAHEAD_AUDIT_FAILED`
3. Row counts → `ML_ATR_V0_NEEDS_MORE_DATA`
4. Validation AUC → `ML_ATR_V0_VALIDATION_CALIBRATION_FAILED`
5. Test trade count → `ML_ATR_V0_INSUFFICIENT_TEST_TRADES`
6. Side coverage → `ML_ATR_V0_TEST_ECONOMIC_GATES_FAILED` (reason: `insufficient_side_coverage`)
7. Economic gates → `ML_ATR_V0_TEST_ECONOMIC_GATES_FAILED`
8. All pass → `ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE`
