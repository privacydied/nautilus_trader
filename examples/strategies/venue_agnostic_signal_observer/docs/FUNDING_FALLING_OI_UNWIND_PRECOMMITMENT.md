# FUNDING_FALLING_OI_UNWIND_PRECOMMITMENT.md

## Study ID
`family3_funding_falling_oi_unwind_v1`

## Label
Family 3 v1 — BTCUSDT Funding Extreme × Falling OI Unwind-Continuation

## Version
v1 — Frozen precommitment. Not yet evaluated. Do not modify after Stage B starts.

---

### Relation to Rejected Family 3 v0 Rising-OI

Family 3 v0 (rising-OI crowding-build) is **rejected** under lock #12. Lock #12 covers the rising-OI mapping only and explicitly carves out the falling-OI unwind mapping as open.

**Structural-change rationale:**
> This study reverses the OI regime filter. Where v0 tested whether *rising* OI at a funding extreme predicts a reversal (crowding build), this study tests whether *falling* OI at a negative funding extreme predicts continuation of the short unwind (unwind already underway). The mechanism is distinct — "the short unwind itself is the move" — and the v0 diagnostic falling-OI data showed positive-mean returns at 6 bps cost, which warrants standalone evaluation at 50 bps.

The v0 diagnostic falling-OI result (+7.0 / +42.1 / +43.8 bps at 8h/24h/48h at 6 bps diagnostic cost) is a **clue, not evidence of edge**. It has not passed any gate, null test, FDR, or holdout. This precommitment starts from zero with no grandfathered results.

**This study does NOT reopen:**
- The rising-OI mapping (lock #12 remains locked).
- Family 2 funding reversal.
- Any positive-funding cell.
- Any 8h primary cell.
- Diagnostic falling-OI v0 numbers are not used as input parameters.

---

### Data Source

**Primary — Funding:** Binance Vision public archive monthly funding rate files.
- Path: `data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-YYYY-MM.zip`
- Content: 8-hour funding settlement records with `calc_time` (ms epoch), `last_funding_rate`.
- Coverage: 2020-09 through present.
- Funding timestamp must be settlement timestamp.
- Funding settlements must align to 00:00 / 08:00 / 16:00 UTC.
- Settlements not on this grid are excluded and reported.

**Primary — OI:** Binance Vision public archive daily metrics files.
- Path: `data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-YYYY-MM-DD.zip`
- Content: 5-minute snapshots of open interest, OI value, long/short ratios, taker volumes.
- OI field: `sum_open_interest` (contracts). Primary.
- Timestamps: UTC, `YYYY-MM-DD HH:MM:SS` format.
- No authenticated APIs.
- No REST `openInterestHist`.

**Primary — Spot:** Binance Vision public archive monthly 1h spot klines.
- Path: `data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-YYYY-MM.zip`
- Content: 1-hour spot OHLCV candles.
- Coverage: 2020-09 through present.
- Price field: close price (column index 4, 0-indexed).

---

### Safety

- Public-data observer only.
- No authentication.
- No API keys.
- No private keys.
- No orders.
- No execution client imports.
- No paper trading.
- No shadow trading.
- No governance approval path.
- No bot path.
- No live capture.
- Archive data only.
- Read-only archive access via HTTPS.

---

### Funding Extreme Rule

- Metric: past-only 180-calendar-day percentile membership.
- Window: exactly 180 calendar days preceding each funding settlement timestamp.
- Threshold: bottom 5% → negative funding extreme only.
- No absolute funding threshold.
- No 2.5%, 1%, or alternate percentile thresholds.
- No positive funding extreme cells in the primary family.
- No threshold tuning based on event counts.

Settlements without a full 180-day past reference window are **excluded from eligibility**, not treated as non-extremes.

---

### OI Alignment Rule

Use only past rows:

```
OI_end   = last metrics row timestamp <= funding_settlement_ts
OI_start = last metrics row timestamp <= funding_settlement_ts - 8h
OI_change_pct = (OI_end - OI_start) / OI_start
falling_oi = OI_change_pct <= 0
```

OI exclusions:
- If either bracket is missing, mark `OI_UNALIGNED` and exclude.
- If `OI_start` is zero, non-positive, missing, NaN, infinite, or otherwise non-finite, mark `OI_UNALIGNED` and exclude.
- No future rows.
- No interpolation using future data.
- No forward-fill across settlement boundaries.

---

### OI Granularity Check

- Explicitly assess whether the available metrics archive granularity can support an 8h falling-OI bracket.
- Report p50/p95/max alignment offset in minutes for:
  - settlement bracket
  - settlement-minus-8h bracket
- Acceptable alignment threshold: `p95_alignment_offset_minutes <= 5` for both brackets.
- If p95 offset exceeds 5 minutes for either bracket, return `OI_ALIGNMENT_FAILED` and STOP.
- If the archive only provides daily-granularity OI and cannot support the 8h bracket, return `OI_GRANULARITY_UNSUPPORTED` and STOP.

---

### Spot Source

- BTCUSDT spot archive only.
- Stage A may check spot availability for 24h and 48h horizons but must not compute returns.
- Stage B may compute forward returns only after Stage A passes.

---

### Primary Family

Exactly 2 cells:

| # | Funding Direction | OI Regime | Horizon |
|---|---|---|---|
| 1 | Negative funding extreme | Falling OI | 24h forward spot return |
| 2 | Negative funding extreme | Falling OI | 48h forward spot return |

**Family size:** exactly 2 primary cells.

**Explicit exclusions:**
- No 8h primary cell.
- No extra horizons.
- No extra assets.
- No extra venues.
- No cross-exchange funding.
- No multi-asset basket.
- No rising-OI cells in primary family, comparison table, diagnostic table, or evaluator row.
- No positive-funding cells in primary family, comparison table, diagnostic table, or evaluator row.
- Rising-OI and positive-funding exclusions may appear only as prose in this precommitment.

---

### Direction Mapping

| Signal | Forward Return Expectation | Score |
|---|---|---|
| negative_funding_extreme + falling_oi | Long BTC (unwind continuation) | score = +forward_return |

---

### Return Leg

- Spot BTC forward return only.
- BTC spot price from Binance Vision monthly 1h klines archive.
- No perp/swap leg.
- No funding-paid-while-held term.
- Return computation: `(price_at_horizon - price_at_entry) / price_at_entry` for raw returns.
- Entry price: first 1h kline close at or after settlement timestamp.
- Forward price: first 1h kline close at or after settlement_ts + horizon.

---

### Cost Assumptions

- **Primary cost:** 50 bps one-way (entry + exit = 100 bps round-trip; net bps = scored_return * 10000 - 100 for round-trip).
- **Diagnostic tier:** Not used. No 6 bps diagnostic edge number is presented as primary evidence.
- Do not compute, present, or report a 6 bps diagnostic edge number.

---

### Minimum Events

- Per-cell requirement: >= 50 valid events overall.
- Chronological holdout requirement: >= 50 valid events in the 30% holdout.
- Population pass rule: each primary cell must have >= 50 valid events overall AND >= 50 in holdout.
- Low event count → `NEEDS_MORE_DATA`.
- Adequate total but insufficient holdout → `UNDERPOWERED_HOLDOUT_FAILURE`.

---

### Chronological Split

- Compute the split timestamp once on the full eligible falling-OI negative-funding event series.
- Use a 70/30 chronological split.
- Per-cell holdout counts are events at or after that fixed split timestamp.
- Do not recompute split timestamps per horizon.

---

### Evaluation Gates (Holdout)

All must pass for the cell to warrant longer observation:

| Gate | Threshold |
|---|---|
| Mean net bps | > 0 |
| Median net bps | > 0 |
| Win rate | >= 0.55 |
| Worst decile net bps | > -50 bps |
| Baseline delta bps | >= 10 bps |
| Timestamp-shuffle null p | <= 0.05 |
| FDR (Benjamini-Yekutieli) | alpha = 0.05 across exactly 2 primary cells |

Gates are evaluated on chronological holdout only. Train metrics are informational.

---

### Null Specification

- **Method:** Timestamp-shuffle null only.
- **Eligible timestamps:** All eligible funding settlement timestamps (post-warmup, on-grid).
- **Preserves:** Event count per cell. 8h settlement-grid structure.
- **Forbidden:** Sign-flip null.
- **Fixed seed:** 42.
- **Purpose:** Generate null distribution of mean net bps for each cell.
- **Usage:** Null output may not be used to tune parameters.
- **Deterministic with fixed seed.**

---

### FDR

- **Method:** Benjamini-Yekutieli.
- **Family size:** exactly 2 primary cells.
- **Alpha:** 0.05.

---

### Per-Cell Verdicts

Allowed:
- `CANDIDATE_FOR_LONGER_OBSERVATION`
- `REJECTED`
- `NULL_REJECTED_DIAGNOSTIC`
- `FDR_BLOCKED_DIAGNOSTIC`
- `NEEDS_MORE_DATA`

Rules:
- `NULL_REJECTED_DIAGNOSTIC` must not collapse into general `REJECTED`.
- A null pass means warrants longer observation, not tradeable.
- No `EXECUTION_READY`.
- No `TRADE_READY`.
- No bot authorization.
- No shadow execution.

---

### Stage B Baseline

- Use eligible settlement timestamps.
- Must be deterministic with fixed seed where randomness is needed.
- Must not tune parameters.

Baseline: mean net bps of all eligible (non-extreme) funding settlements in the same direction, computed on holdout.

---

### Underpowered Semantics

- Low event outcomes → `NEEDS_MORE_DATA`.
- Holdout failure (total >= 50, holdout < 50) → `UNDERPOWERED_HOLDOUT_FAILURE`.
- No p-values computed on cells with zero survivors after cost.
- Underpowered outcomes may suggest expanding the date range or relaxing criteria, but only in a separately precommitted follow-up.

---

### Registry Rule

- Do not update `REJECTED_RESEARCH.md` until Stage B completes cleanly.
- Stage A is allowed: precommitment document, archive feasibility probe, population sizing report.
- Phase 0B (population sizing) must not compute forward returns, edge stats, nulls, FDR, holdout verdicts, or registry updates.
- Phase 0B must be deterministic.
- If Stage A outcome is NOT `POPULATION_SUFFICIENT`, STOP. Do not build evaluator. Do not compute returns. Do not update registry.
- If Stage B does not complete cleanly, do not touch registry.

---

### Content Hashes

- Record content hashes for all archive files used in Phase 0 and Stage B.
- Include hashes in JSON reports and final markdown report.

---

*Frozen precommitment for `family3_funding_falling_oi_unwind_v1`. Created as part of two-stage archive-only experiment. No forward returns, edge stats, null test, FDR, evaluation, registry update, private key, order, execution, or bot path was used during precommitment authoring.*
