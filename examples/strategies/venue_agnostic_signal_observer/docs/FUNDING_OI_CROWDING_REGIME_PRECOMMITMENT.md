# FUNDING_OI_CROWDING_REGIME_PRECOMMITMENT.md

## Study ID
`funding_oi_crowding_regime_v0`

## Label
Family 3 — BTCUSDT Funding × Open Interest Crowding Regime

## Version
v0 — Phase 0 precommitment. Not yet evaluated.

---

### Relation to Family 2

Family 2 (funding-crowding reversal v1) is **rejected** and remains rejected. This study does NOT reopen Family 2 by changing thresholds, horizons, costs, seeds, windows, or null iterations.

**Structural-change rationale:**
> This study conditions funding extremes on past-only OI regime. Family 2 did not evaluate open-interest changes near funding extremes.

The previous study tested whether funding extremes alone predict spot reversals. That design assumed the mechanism was funding-rate pressure directly. The new mechanism is that funding extremes may become predictive only when OI confirms leverage crowding. If OI is falling near an extreme, the unwind may already be happening or completed—reversal may not follow. Rising OI near an extreme suggests the crowd is still building, making a later reversal more likely.

---

### Data Source

**Primary:** Binance Vision public archive daily metrics files.
- Path: `data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-YYYY-MM-DD.zip`
- Content: 5-minute snapshots of open interest, OI value, long/short ratios, taker volumes.
- Coverage confirmed: 2020-09-01 through present (5+ years).
- OI field: `sum_open_interest` (contracts). Primary.
- Secondary OI field: `sum_open_interest_value` (USD). Diagnostic only unless this precommitment specifically promotes it.
- Timestamps: UTC, `YYYY-MM-DD HH:MM:SS` format.

**Funding source:** Binance Vision public archive monthly funding rate files.
- Path: `data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-YYYY-MM.zip`
- Content: 8-hour funding settlement records with `calc_time` (ms epoch), `last_funding_rate`.
- Coverage: 2020-09 through present.

**REST openInterestHist is rejected** as a study data source. (See DATA_AVAILABILITY.md for full rationale.)

---

### Safety

- Public-data observer only.
- No authentication.
- No API keys.
- No private keys.
- No orders.
- No execution client imports.
- No bot path.
- No live trading.
- No paper trading.
- No shadow trading.
- Read-only archive access via HTTPS.

---

### OI Alignment Rule (Frozen)

OI change is computed using past-only rows:

```
OI_end   = last metrics row timestamp <= funding_settlement_ts
OI_start = last metrics row timestamp <= funding_settlement_ts - 8h
OI_change_pct = (OI_end - OI_start) / OI_start
```

- If either bracket is missing → OI_UNALIGNED → exclude from evaluation.
- No future rows may be used.
- No interpolation using future data.
- No forward-fill across settlement boundaries.
- Phase 0A confirmation: 5-minute cadence yields 0-minute p95 alignment offset at funding settlement grid points (00:00, 08:00, 16:00 UTC). OI_ALIGNMENT_OK.

---

### OI Regime Classification (Frozen)

```
rising_oi  = True if OI_change_pct > 0
falling_oi = True if OI_change_pct <= 0
```

- No separate threshold for "strongly rising" or "strongly falling" in v0.
- OI_change_pct = 0 → classified as falling_oi.

---

### Funding Threshold (Frozen)

- Metric: past-only 180-day percentile membership.
- Window: exactly 180 calendar days preceding each funding settlement timestamp.
- Thresholds: top 5% → positive funding extreme; bottom 5% → negative funding extreme.
- No absolute threshold values.
- No 2.5%, 1%, or alternative percentile definitions in v0.
- No threshold tuning based on event counts.

---

### Primary Cells

| Funding Direction | OI Regime | Horizon | Count |
|---|---|---|---|
| Positive extreme | Rising OI | 8h forward spot return | 1 |
| Positive extreme | Rising OI | 24h forward spot return | 2 |
| Positive extreme | Rising OI | 48h forward spot return | 3 |
| Negative extreme | Rising OI | 8h forward spot return | 4 |
| Negative extreme | Rising OI | 24h forward spot return | 5 |
| Negative extreme | Rising OI | 48h forward spot return | 6 |

**Family size:** exactly 6 primary cells (2 funding directions × 1 OI regime × 3 horizons).

Multiple-horizon correction: Benjamini-Yekutieli FDR across all 6 primary cells at alpha = 0.05.

---

### Diagnostic Cells

- falling_oi versions of the same 6 cells (2 × 1 × 3 = 6 diagnostic cells).
- Excluded from primary family BY FDR.
- Results reported separately.
- Diagnostic status cannot promote a cell to the primary family.
- 6 bps cost sensitivity may be applied to diagnostic cells, but a positive result at 6 bps cannot override a negative primary result at 50 bps.

---

### Direction Mapping

| Signal | Forward Return Expectation | Score |
|---|---|---|
| positive_funding_extreme + rising_oi | Bearish reversal | score = -forward_return |
| negative_funding_extreme + rising_oi | Bullish squeeze/reversal | score = +forward_return |

Collapsed evaluation: if the directional mapping holds, scores should be positive in aggregate. A negative aggregate score means the expected reversal did not occur (or the opposite move happened).

---

### Return Leg

- Spot BTC forward return only.
- BTC spot price from Binance Vision klines or equivalent public archive.
- No perp/swap leg.
- No funding-paid-while-held term in v0.
- Return computation: (price_at_horizon - price_at_entry) / price_at_entry for raw returns, then sign-flipped per direction mapping above.

---

### Cost Assumptions

- **Primary cost:** 50 bps one-way (entry + exit = 100 bps round-trip for a single horizon, but net bps is computed per event at the cell level).
- **Diagnostic sensitivity:** 6 bps one-way (for diagnostic-only cells).
- Diagnostic cost sensitivity may not be used to promote a rejected primary cell.

---

### Minimum Events

- Primary cell requirement: >= 50 valid events per primary cell overall.
- Holdout requirement: >= 50 valid events per primary cell in the chronological 30% holdout.
- Phase 0B confirmation: both primary rising-OI sides exceed both thresholds:
  - Positive extreme × rising OI: 526 total, 145 holdout.
  - Negative extreme × rising OI: 256 total, 79 holdout.

If any cell has fewer than 50 events total → NEEDS_MORE_DATA.
If any cell has >=50 total but <50 in holdout → UNDERPOWERED_HOLDOUT_FAILURE.
Neither outcome is a "rejection" of the hypothesis.

---

### Evaluation Gates

All must pass for the study to be considered accepted:

| Gate | Threshold |
|---|---|
| Mean net bps | > 0 |
| Median net bps | > 0 |
| Win rate | >= 0.55 |
| Worst decile net bps | > -50 bps |
| Baseline delta bps | >= 10 bps |
| Timestamp-shuffle null p | <= 0.05 |
| FDR (Benjamini-Yekutieli) | alpha = 0.05 across exactly 6 primary cells |
| Chronological holdout | 70/30 train/holdout split |

---

### Null Specification

- **Method:** Timestamp-shuffle null.
- **Eligible timestamps:** All eligible funding settlement timestamps.
- **Preserves:** Event count per cell. Settlement-grid structure (8h periodicity).
- **Forbidden:** Sign-flip null (not applicable to this directional hypothesis).
- **Purpose:** Generate null distribution of mean net bps for each cell.
- **Usage:** Null output may not be used to tune parameters.

---

### Underpowered Semantics

- Low or zero event outcomes → NEEDS_MORE_DATA.
- Tiny holdout failure (total >=50, holdout <50) → UNDERPOWERED_HOLDOUT_FAILURE.
- No p-values computed on cells with zero survivors after cost → not FDR rejection.
- Underpowered outcomes may suggest expanding the date range or relaxing the funding threshold, but only in a separately precommitted follow-up (v1).

---

### Registry Rule

Do not update REJECTED_RESEARCH.md until a real evaluation exists. Phase 0 allowed:
- Precommitment document (this file).
- Data availability reports.
- Population sizing counts.

---

### Phase 0 Completion Status

| Phase | Result |
|---|---|
| 0A: Archive feasibility | PASSED (PHASE0A_ARCHIVE_FEASIBILITY_PASSED) |
| 0B: Population sizing | PASSED (PHASE0B_POPULATION_FEASIBILITY_PASSED) |
| 0C: Full precommitment | Written (this document) |

**Next step:** Implement evaluator, run against Binance Vision archive data, produce results, report to RESEARCH_LOG.md.

---

*Generated by run_funding_oi_archive_probe.py / funding_oi_crowding_regime.py — no forward returns, edge stats, null test, FDR, evaluation, registry update, private-key, order, execution, or bot path was used.*
