# Hyperliquid BTC/ETH ML+ATR Representative Real-Strategy v0 Findings

Status: VALIDATION_CALIBRATION_FAILED_NOT_PROMOTED

## Scope

This document records the outcome of the Hyperliquid BTC/ETH ML+ATR representative real-strategy diagnostic. It is a narrow, scoped negative result for a specific frozen feature set, model, and horizon on representative Hyperliquid data.

This is NOT original v0. This is NOT the full 2025-window diagnostic. This does NOT close the full 2025-window diagnostic because it was not run. This does NOT close original late-2023 v0 because official trade/fill coverage was unavailable.

## What was tested

The actual frozen ML+ATR strategy path was run on representative Hyperliquid BTC/ETH data:

- **Feature set**: ret_1h, ret_4h, ret_24h, realized_vol_24h, atr_norm_14h, funding_current, funding_mean_24h, rsi_14h
- **Model**: Logistic regression (sklearn)
- **Calibrator**: Platt scaling
- **Label horizon**: 24 bars (24 hours)
- **Thresholds**: long >= 0.55, short <= 0.40
- **Cost model**: Hyperliquid taker fees (1 bps per side)
- **Symbols**: BTC, ETH

## Data window

- Train: 2025-08-01 through 2025-08-31
- Validation: 2026-01-01 through 2026-01-31
- Test: 2026-03-01 through 2026-03-31

Total bars: 4464 (2232 per symbol)

No new AWS/S3 downloads were performed during the representative real-strategy diagnostic. The diagnostic used existing representative bar and funding parquet files.

## Split audit

**Verdict**: REPRESENTATIVE_SPLIT_ROW_AUDIT_EXPLAINED

The 721-row train count was explained by a legitimate 24-bar feature warmup drop. The first 24 bars per symbol are dropped because rolling window features (RSI, ATR, realized volatility) require 24 bars of history to compute. This is a legitimate past-only feature construction drop, not row-index partitioning or representative-mode bypass.

- Train rows: 721 (after 24-bar warmup drop)
- Validation rows: 721
- Test rows: 744

No test rows were used for scaler fit, model fit, or Platt calibration.

## Model result

The model trained successfully. The model failed the validation calibration gate because validation AUC < 0.51.

**Diagnostic status**: ML_ATR_REPRESENTATIVE_REAL_STRATEGY_V0_VALIDATION_CALIBRATION_FAILED

**Interpretation**: The frozen feature set + logistic regression + Platt calibration + 24h horizon had no detectable directional predictive power on this representative Hyperliquid BTC/ETH sample. A validation AUC of < 0.51 is approximately coin-flip directional prediction under the frozen gate.

## Interpretation

This is an honest negative result. The gate did exactly what it was supposed to do:

1. Stop promotion before paper/live/shadow
2. Prevent threshold/feature fiddling after looking at results

The frozen v0 representative real-strategy diagnostic failed validation calibration. The pipeline worked. The gate caught lack of edge before promotion.

## What this does not imply

This finding is narrowly scoped and does NOT:

- Reject Hyperliquid as a venue
- Reject all BTC/ETH ML strategies
- Reject all logistic regression uses
- Reject all ATR exits
- Reject volatility/regime prediction
- Reject funding-carry or non-directional hypotheses
- Close the full 2025-window diagnostic because it was not run
- Close original late-2023 v0 because official trade/fill coverage was unavailable

## Promotion decision

No paper/live/shadow/bot/systemd/order-routing eligibility was emitted. The diagnostic status is VALIDATION_CALIBRATION_FAILED_NOT_PROMOTED.

The full 170–220 GiB pull should not be performed solely to rescue this failed representative v0.

## Frozen next-step rule

A future v1 is allowed only with a fresh precommitment written before changing features/model/horizon/splits/thresholds. Do not re-run the same hypothesis without changing something structural.
