# Hyperliquid BTC/ETH ML+ATR 2025 Window Smoke Diagnostic — Precommitment

## study_id
`hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0`

## What this is
A separate, explicitly limited **smoke diagnostic** / **pipeline validation** study
using only official Hyperliquid S3 data that actually exists for 2025+.

This is **NOT** original v0.

## Why this exists
Original v0 required late-2023 through 2024-12-31 training data.
Official Hyperliquid trade/fill probes found no original-v0 trade/fill coverage
for 2023/2024. The `node_trades/hourly` path produced only 1541 hourly BTC/ETH
bars from 2025-03-22 to 2025-05-25. The `node_fills` and `node_fills_by_block`
paths have later 2025+ coverage but not the original train window.

Original v0 cannot honestly run as specified. This study uses only the official
2025+ Hyperliquid data that exists.

## Scope
- **Symbols**: BTC and ETH standard perps only.
- **Bars**: 1h bars only.
- **Features**: Same feature set as original ML+ATR v0:
  `ret_1h`, `ret_4h`, `ret_24h`, `realized_vol_24h`, `atr_norm_14h`,
  `funding_current`, `funding_mean_24h`, `rsi_14h`.
- **Model**: Logistic regression first (same as original v0).
- **Calibration**: Platt calibration (same as original v0).
- **Thresholds** (fixed, no search):
  - long threshold: **0.55**
  - short threshold: **0.40**
- **Exit logic**: Same ATR exit logic as original v0
  (stop at 2x ATR, trailing at 3x ATR).
- **Cost/funding**: Same cost and funding modeling as original v0.
- **No retuning**.
- **No feature expansion**.
- **No test-set threshold selection**.
- **No volume-dependent feature** unless it already exists in original v0.

## 2025-Window Split (frozen dates)
- **Train**: 2025-08-01 00:00:00 UTC through 2025-12-31 23:00:00 UTC
- **Validation**: 2026-01-01 00:00:00 UTC through 2026-02-28 23:00:00 UTC
- **Test**: 2026-03-01 00:00:00 UTC through latest complete common BTC/ETH/funding hour

## Regime Caveat
- This covers a **single recent regime** only (mid-2025 through early 2026).
- **Insufficient for robust multi-regime validation**.
- **Not comparable** to the original late-2023 v0.
- Even if metrics are good, this does **not** unlock paper, live, shadow, bot,
  or systemd execution. A separate precommitment is required for any future
  paper/shadow run.

## Data Source
- **Canonical source**: `node_fills_by_block/hourly` from
  `s3://hl-mainnet-node-data/node_fills_by_block/hourly/`
- AWS requester-pays. No credentials stored.
- No SonarX, no Binance/Kraken/Coinbase proxy bars, no third-party paid data.

## Minimum Gates
- BTC and ETH both present.
- Train rows per symbol >= 3000.
- Validation rows per symbol >= 1000.
- Test rows per symbol >= 1000.
- No duplicate symbol/timestamp rows.
- Max gap hours <= 24 unless explicitly blocked and reported.
- Funding overlaps the full selected bar window.
- If these gates fail: **NEEDS_MORE_DATA**. Do not lower gates.

## Allowed Statuses
- `ML_ATR_2025_WINDOW_SMOKE_V0_BACKTEST_COMPLETE_PIPELINE_VALIDATED`
- `ML_ATR_2025_WINDOW_SMOKE_V0_SIGNAL_PRESENT_NOT_PROMOTABLE`
- `ML_ATR_2025_WINDOW_SMOKE_V0_NEEDS_MORE_DATA`
- `ML_ATR_2025_WINDOW_SMOKE_V0_CALIBRATION_FAILED`
- `ML_ATR_2025_WINDOW_SMOKE_V0_INSUFFICIENT_TEST_TRADES`
- `ML_ATR_2025_WINDOW_SMOKE_V0_ECONOMIC_GATES_FAILED`
- `ML_ATR_2025_WINDOW_SMOKE_V0_ERROR_INVALID_INPUT`
- `ML_ATR_2025_WINDOW_SMOKE_V0_ERROR_LOOKAHEAD_AUDIT_FAILED`
- `ML_ATR_2025_WINDOW_SMOKE_V0_BLOCKED_COST_OR_SIZE_CAP`
- `ML_ATR_2025_WINDOW_SMOKE_V0_BLOCKED_SCHEMA_UNRECOGNIZED`
- `ML_ATR_2025_WINDOW_SMOKE_V0_BLOCKED_PARSE_FAILED`

## Forbidden Statuses
- `ML_ATR_V0_TEST_DIAGNOSTIC_PASS_SHADOW_LOGGING_ELIGIBLE`
- `PAPER_SIM_V0_*`
- `TRADE_READY`
- `EXECUTION_READY`
- `LIVE_READY`
- `CANDIDATE_FOR_LIVE`
- `PROFITABLE`

## Promotion Path
Even if metrics pass all gates, this study **does not** unlock:
- Shadow logging
- Exchange paper
- Bot routing
- Live trading
- Order routing
- Systemd

A separate precommitment is required for any future paper/shadow run.

## Max Claim
Pipeline/signal diagnostic only. Evidence from a single recent regime.
