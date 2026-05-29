# Hyperliquid BTC/ETH ML+ATR 2025 Window Smoke v0 Precommitment

## Overview

This precommitment document defines the frozen specification for the ML+ATR strategy diagnostic run on official Hyperliquid BTC/ETH perpetual futures data covering the 2025 window.

**Strategy**: `hyperliquid_btc_eth_ml_atr_2025_window_smoke_v0`

**Purpose**: Diagnostic validation of the full strategy stack (parser, bar builder, funding normalization, feature computation, ML model training, calibration, backtest) on real official Hyperliquid data without claiming trading eligibility.

**Data source**: `s3://hl-mainnet-node-data/node_fills_by_block/hourly/` (official Hyperliquid S3, requester-pays)

**Coverage**: 2025-07-27 through 2026-05-29 (available at time of precommitment)

**Planned window**: 2025-08-01 through latest complete date

**Estimated transfer**: ~170.7 GiB

## Frozen Precommitments

The following parameters are frozen before any expensive diagnostic run:

### Data & Coverage

* Source prefix: `s3://hl-mainnet-node-data/node_fills_by_block/hourly/`
* Symbols: BTC, ETH
* Full window start: 2025-08-01
* Full window end: latest complete date in archive
* Representative windows (Stage A only):
  * Train slice: 2025-08-01 through 2025-08-31
  * Validation slice: 2026-01-01 through 2026-01-31
  * Test slice: 2026-03-01 through 2026-03-31

### Split Configuration (Full Window)

* Train: 2025-08-01 through 2025-12-31
* Validation: 2026-01-01 through 2026-02-28
* Test: 2026-03-01 through latest complete timestamp
* Min train rows per symbol: 3000
* Min validation rows per symbol: 1000
* Min test rows per symbol: 1000

### Representative Window Gates (Stage A Only)

* Min train rows per symbol: 500
* Min validation rows per symbol: 300
* Min test rows per symbol: 300

### Features & Labels

* Label horizon: 24 bars (24 hours for 1h bars)
* Long threshold: 0.55
* Short threshold: 0.40
* Model backend: auto (LightGBM if available, fallback to sklearn)
* Calibrator: Platt
* Seed: 42
* Max gap hours: 24

### Cost & Size Caps

* Previous 25 GiB cap intentionally blocked the full run.
* Official source coverage exists but full pull requires ~170.7 GiB.
* This override is human-authorized and staged.
* Stage A cap: 75 GiB (representative smoke pull)
* Stage B cap: 220 GiB (full-window pull)
* If Stage A planned bytes exceed 75 GiB, stop.
* If Stage B planned bytes exceed 220 GiB, stop.
* If free disk is insufficient, stop.
* Default safety margin: 25 GiB

### Execution Rules

* No orders.
* No private keys.
* No exchange auth.
* No live execution.
* No paper execution.
* No shadow execution unless explicitly requested.
* No systemd watcher changes unless explicitly requested.
* No bot-path coupling.
* No `--loop`.
* No exchange/broker client imports.
* No Nautilus live adapter.

## Staged Cost-Cap Override v0

This override authorizes a two-stage execution:

### Stage A: Representative Smoke Pull

* Purpose: Parser/bar-builder/funding/split/model smoke validation
* Windows: Three one-month slices (train, validation, test representative slices)
* Cap: 75 GiB
* Row gates: Relaxed (500/300/300 per symbol)
* Statuses emitted: Representative-stage only
* Does NOT replace full-window diagnostic
* Does NOT produce edge/promotion claim
* Does NOT unlock paper/live/shadow/bot eligibility

### Stage B: Full-Window Pull

* Only runs if Stage A passes with `ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_PIPELINE_VALIDATED`
* Preserves original 2025-window split and row gates
* Window: 2025-08-01 through latest complete date
* Cap: 220 GiB
* No lowering of gates
* Statuses: Original 2025-window smoke statuses plus explicit cap-override manifest fields

## Status Definitions

### Representative-Stage Statuses

* `ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_PLAN_READY`
* `ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_PIPELINE_VALIDATED`
* `ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_NEEDS_MORE_DATA`
* `ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_BLOCKED_COST_OR_SIZE_CAP`
* `ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_BLOCKED_INSUFFICIENT_DISK`
* `ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_BLOCKED_PARSE_FAILED`
* `ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_DRY_RUN_FAILED`
* `ML_ATR_2025_WINDOW_STAGED_CAP_V0_REPRESENTATIVE_DIAGNOSTIC_FAILED`

### Full-Stage Statuses

* `ML_ATR_2025_WINDOW_SMOKE_V0_BLOCKED_COST_OR_SIZE_CAP`
* `ML_ATR_2025_WINDOW_SMOKE_V0_BLOCKED_INSUFFICIENT_DISK`
* (plus existing 2025-window smoke statuses)

### Forbidden Statuses (Must Never Be Emitted)

* `SHADOW_LOGGING_ELIGIBLE`
* `PAPER_SIM_V0`
* `TRADE_READY`
* `EXECUTION_READY`
* `LIVE_READY`
* `CANDIDATE_FOR_LIVE`
* `PROFITABLE`

## What This Override Does NOT Change

* Features
* Labels
* Model architecture
* Calibration method
* Long/short thresholds (0.55 / 0.40)
* Exit logic
* Cost assumptions
* Funding handling
* Full-window row gates (3000/1000/1000)
* Promotion path requirements
* Raw S3 data commit policy (must not be committed)
* Paper/live/shadow/bot/order routing eligibility (remains forbidden)

## What This Override Does

* Authorizes Stage A representative pull up to 75 GiB
* Authorizes Stage B full pull up to 220 GiB
* Adds explicit disk-space check before execute
* Adds resume-safe download support
* Adds deterministic chunked processing
* Adds manifest fields for cap override tracking
* Adds representative mode to diagnostic runner

## Data Handling

* Raw S3 files must not be committed.
* Prefer resume-safe downloads.
* Prefer deterministic chunked processing.
* Manifest must include:
  * source prefix
  * stage (representative or full)
  * selected windows
  * object count
  * planned bytes
  * downloaded bytes
  * skipped existing bytes
  * max cap GiB
  * required free disk GiB
  * free disk before execute
  * requester_pays true
  * cache root
  * no credentials

## Bar Builder Requirements

* Filter by date windows if cache contains multiple stages.
* Deterministic file ordering.
* Use `orjson` by default where practical.
* Use string keys, not bytes keys.
* Avoid loading all raw records into memory.
* Build hourly bars incrementally by symbol/hour.
* Output exact ML-compatible bars:
  * timestamp
  * symbol
  * open
  * high
  * low
  * close
  * volume
* Write:
  * `hyperliquid_btc_eth_1h_bars.parquet`
  * `hyperliquid_btc_eth_1h_bars.csv`
  * `summary.json`
  * `summary.md`
  * `manifest.json`
  * `gaps.json`
  * `schema_report.json`
  * `dedup_report.json`
* Do not forward-fill.
* Do not synthesize missing hours.
* Do not generate zero-volume missing bars.
* Reject duplicate symbol/timestamp output rows.
* Preserve UTC-aware timestamps.

## Diagnostic Modes

* `--representative-smoke`: Representative mode using three windows and relaxed row gates
* Full mode: Unchanged except for explicit cap-override manifest fields

## Final Eligibility Statement

Final status from this diagnostic **cannot** unlock:
* Paper simulation
* Live trading
* Shadow logging
* Bot deployment
* Order routing

This is a **data-prep and strategy-stack validation only**.

## Precommitment Hash

```
<will be generated after git commit>
```