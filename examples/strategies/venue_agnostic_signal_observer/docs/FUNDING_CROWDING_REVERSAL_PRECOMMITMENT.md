# Family 2 — Funding Crowding Reversal Precommitment

Signal family: `funding_crowding_reversal_v1`

Phase: 0 — precommitment document

Status: frozen before any empirical evaluation. No data fetched. No results generated.

---

## 1. Hypothesis

Extreme BTCUSDT perpetual funding marks crowded long or crowded short positioning and may predict subsequent BTC price reversal over multi-hour horizons.

The signal is not the funding payment. The signal is positioning pressure.

- **Positive funding extreme** means the market is crowded long and predicts **negative** subsequent BTC return.
- **Negative funding extreme** means the market is crowded short and predicts **positive** subsequent BTC return.

This hypothesis is structurally different from:

- Funding-as-carry (V6-B, rejected). No funding payment is earned or modelled here.
- Same-venue quote-basis reversion (Family 1, rejected under cost wall). The predicted move is across multi-hour horizons where a ~6 bps cost wall is a tax, not an executioner.
- Derivatives-source lead-lag. Information propagation speed is irrelevant to this hypothesis. The feature is sustained positioning pressure, not a news impulse.

---

## 2. Scope

### Primary scope

- **Source:** Binance USDⓈ-M BTCUSDT perpetual funding observations.
- **Forward-return leg:** BTC spot price series.
- **Horizons:** 4h to 48h.

Using spot BTC for the forward-return leg deliberately sidesteps the direction-dependent funding-paid-while-held PnL problem of holding a perpetual position for 4h to 48h.

**Important caveats for this first spot-return study:**

1. The first study evaluates prediction of spot BTC forward returns.
2. It does not model an executed perpetual trade.
3. It does not include funding payments earned or paid during the holding horizon.
4. A future perp-execution variant would need a separate precommitment because funding-paid-while-held is direction-dependent and material.
5. In a perp execution version, shorting crowded-long BTC during positive funding would usually receive funding, while longing crowded-short BTC during negative funding may pay funding. That asymmetry is intentionally out of scope for this first spot-return study.

### Secondary diagnostic scope

- **ETHUSDT** may be evaluated only as diagnostic context.
- ETH must not enter the BTC FDR family.
- ETH can only be described as `consistent with BTC finding`, `not consistent with BTC finding`, or `diagnostic only`.
- ETH must never independently promote a candidate.
- ETH must never expand the BTC FDR denominator.
- ETH must never be used to rescue a weak BTC result.

Do not include alts in the first frozen family.

---

## 3. Frozen signal cells

The BTC primary family consists of exactly **60 frozen cells**:

- 6 threshold definitions
- 5 horizons
- 2 directions

### Threshold definitions (6)

**Absolute funding thresholds:**

| Label | Condition |
|---|---|
| `abs_funding_ge_5bp` | `abs(funding_rate) >= 0.0005` |
| `abs_funding_ge_10bp` | `abs(funding_rate) >= 0.0010` |
| `abs_funding_ge_25bp` | `abs(funding_rate) >= 0.0025` |

**Rolling percentile thresholds:**

| Label | Condition |
|---|---|
| `pct_funding_top_bottom_5pct` | top or bottom 5% |
| `pct_funding_top_bottom_2.5pct` | top or bottom 2.5% |
| `pct_funding_top_bottom_1pct` | top or bottom 1% |

### Forward-return horizons (5)

| Label | Horizon |
|---|---|
| `h4` | 4h |
| `h8` | 8h |
| `h12` | 12h |
| `h24` | 24h |
| `h48` | 48h |

**Primary horizon:** 24h.

**Secondary horizons:** 4h, 8h, 12h, 48h.

### Directions (2)

| Label | Mapping |
|---|---|
| `positive_funding_extreme` | positive funding predicts negative BTC return (reversal down) |
| `negative_funding_extreme` | negative funding predicts positive BTC return (reversal up) |

### Family size invariant

All 60 BTC cells form one precommitted FDR family.

```
BTC_PRIMARY_FDR_FAMILY_SIZE = 60
```

Do not leave family size implicit.

Do not allow the runner to infer the family size dynamically.

Do not allow missing or underpowered cells to shrink the FDR denominator after the fact.

---

## 4. Multiple-comparisons correction

The 60 cells are heavily correlated because:

- absolute thresholds overlap in event sets (same funding observation can pass multiple thresholds)
- percentile thresholds overlap in event sets
- adjacent horizons are highly collinear (4h/8h/12h/24h/48h windows overlap heavily)
- positive and negative funding regimes may cluster in time

Because of correlated cells, use the **Benjamini-Yekutieli (BY) method**, not plain BH, for the primary family-wide correction.

**Mandatory rule:**

A BTC cell can only receive `CANDIDATE_FOR_LONGER_OBSERVATION` if it passes all individual gates and survives BY FDR correction across the frozen 60-cell BTC family.

This is mandatory, not optional. Do not phrase it as optional.

If the BY FDR module is not available on this branch, Phase 0 still completes normally because the precommitment document is the deliverable. However, the final report must state that the next task is `build BY FDR correction`, explicitly not `run funding evaluation`.

---

## 5. Percentile thresholds and lookahead prevention

The rolling percentile thresholds use a **180 calendar day lookback**.

### Past-only percentile invariant

```
PAST_ONLY_PERCENTILE_INVARIANT
```

1. Percentile eligibility at event time `t` is computed using funding observations strictly before `t`.
2. The observation at `t` itself must not be included in the percentile window.
3. Future observations after `t` must not affect the percentile threshold at `t`.
4. Global full-series percentiles are forbidden.
5. Centered rolling windows are forbidden.
6. Expanding windows that include `t` are forbidden.
7. Any percentile event without 180 calendar days of prior funding history is ineligible for percentile cells.

### Sample-size consequence

Percentile cells will have a systematically smaller eligible event pool than absolute-threshold cells because the first 180 days of any data window are warmup-only for percentile cells, while absolute-threshold cells can use those observations. A `NEEDS_MORE_DATA` result on a percentile cell may reflect the 180-day warmup tax rather than a genuine lack of funding extremes.

---

## 6. Funding interval metadata

The evaluation runner must not assume a fixed 8h funding interval.

Binance funding intervals can vary by contract and period. The future runner must read actual funding interval metadata where available and record the funding interval used for each observation or data segment.

### Funding interval metadata requirement

```
FUNDING_INTERVAL_METADATA_REQUIRED
```

The runner must disclose:

- funding interval source
- interval changes, if detected
- number of observations by interval
- whether interval metadata was unavailable
- whether any observation was excluded due to interval ambiguity

Do not fetch or parse interval metadata in Phase 0. This requirement is for the future runner.

---

## 7. Event definition and de-duplication

### Event de-duplication rule

```
EVENT_DEDUP_RULE
```

Each funding timestamp can produce at most one event per frozen cell.

Overlapping forward-return windows are permitted because funding observations are naturally discrete and may occur every 4h or 8h. However, overlapping windows must be disclosed in the report.

**Do not** collapse consecutive funding extremes into one event in the primary evaluation unless a future precommitment explicitly defines a cluster-adjusted variant.

**Do not** allow the runner to choose event cooldown after seeing results.

### Known limitation: funding event clustering

```
FUNDING_EVENT_CLUSTERING_UNADJUSTED
```

Funding extremes cluster hard in time. A euphoric or panic regime can create many correlated extreme events within a few days. Therefore, a cell with 100 events may have far fewer independent regimes. The 60-cell BY FDR correction controls the cell-level multiple-comparisons surface, not per-cell event independence.

Do not attempt to solve clustering in this Phase 0 task. Just disclose it clearly.

---

## 8. Return definition

Use **signed reversal return**.

For positive funding:

```
signal_return_bps = -forward_btc_spot_return_bps
```

For negative funding:

```
signal_return_bps = forward_btc_spot_return_bps
```

Net return:

```
net_signal_return_bps = signal_return_bps - total_cost_bps
```

Do not lower costs based on side, horizon, or observed result.

Do not include funding-paid-while-held in this first spot-return version.

---

## 9. Cost model

Use the conservative round-trip cost assumptions already defined in the existing observer's `config.py` (`FeeModel`) unless a more specific documented BTC spot cost model exists in the repo.

### Cost terms

| Term | Value | Source |
|---|---|---|
| `fee_bps` | 5.0 | Existing `FeeModel.fee_bps` default |
| `slippage_bps` | 1.0 | Existing `FeeModel.slippage_bps` default |
| `quote_mismatch_buffer_bps` | 0.0 | Same-asset BTC spot forward returns; no quote mismatch |
| `total_cost_bps` | 6.0 | `fee_bps + slippage_bps` |

Costs are symmetric for this spot-return study.

### Distinction from a future perp execution study

A future perpetual execution study would need its own precommitment because:

- Held-position funding payments would be direction-dependent (shorting during positive funding receives funding; longing during negative funding pays funding).
- Perp funding rates received/paid during the holding horizon materially change the cost model.
- The cost model would be asymmetric and timing-dependent.

This first spot-return study does not model those effects.

---

## 10. Baseline definition

The baseline-beat gate must be **direction-matched**.

Do not compare reversal signal returns against raw BTC drift.

### Direction-matched baseline rule

```
DIRECTION_MATCHED_BASELINE_REQUIRED
```

- For positive-funding short-reversal cells, the baseline is random-entry negative BTC return over the same horizon.
- For negative-funding long-reversal cells, the baseline is random-entry positive BTC return over the same horizon.

### Gate

The cell must beat the direction-matched unconditional same-horizon baseline by at least 10 bps.

---

## 11. Sample-size policy

### Minimum event counts

| Event count | Action |
|---|---|
| fewer than 50 valid events | `NEEDS_MORE_DATA` |
| at least 50 valid events | diagnostic summary may be reported |
| at least 100 valid events | candidate-style longer-observation label may be considered, subject to all other gates |

Below 100 valid events, do not assign `CANDIDATE_FOR_LONGER_OBSERVATION`.

### Underpowered cell notes

- The strictest cells (especially `abs(funding_rate) >= 0.0025` and top or bottom 1%) are likely to be underpowered and may correctly return `NEEDS_MORE_DATA`.
- Percentile cells are additionally disadvantaged by the 180-day warmup requirement.

---

## 12. Acceptance gates

A BTC cell may receive `CANDIDATE_FOR_LONGER_OBSERVATION` only if **all** of the following are true:

| # | Gate |
|---|---|
| 1 | The cell belongs to the frozen BTC primary 60-cell family. |
| 2 | `valid_count >= 100`. |
| 3 | `mean_net_bps > 0`. |
| 4 | `median_net_bps > 0`. |
| 5 | `win_rate >= 0.55`. |
| 6 | `worst_decile_net_bps > -total_cost_bps`. |
| 7 | The cell beats the direction-matched unconditional same-horizon baseline by at least 10 bps. |
| 8 | The cell survives the mandatory native timestamp-shuffle null diagnostic. |
| 9 | The cell survives BY FDR correction across the frozen 60-cell BTC family. |
| 10 | Chronological train and holdout splits have the same sign. |
| 11 | The holdout result is not underpowered according to the precommitted sample-size policy. |

### Prohibitions

- No individual gate may be enough on its own.
- No raw positive mean may promote a cell.
- No ETH diagnostic result may promote a BTC cell.
- No null result may be used to tune thresholds.
- No FDR result may be used to tune thresholds.

---

## 13. Verdict taxonomy

The following verdicts are defined for this study:

| Verdict | Meaning |
|---|---|
| `CANDIDATE_FOR_LONGER_OBSERVATION` | Only allowed for BTC primary cells that pass all gates, mandatory timestamp-shuffle null, and BY FDR. |
| `REJECTED` | Enough data exists for the frozen BTC cell, but it fails the precommitted gates. |
| `NEEDS_MORE_DATA` | Insufficient valid events, insufficient eligible percentile history, underpowered holdout, or otherwise not enough data to make a rejection or candidate claim. |
| `UNDERPOWERED_HOLDOUT_FAILURE` | Holdout sign or magnitude is not usable because the holdout sample is too small. This is not a global rejection. |
| `NO_MCPT_WORTHY_GROUPS` | No cells warrant external MCPT export. |
| `NULL_REJECTED_DIAGNOSTIC` | The timestamp-shuffle null indicates the observed result could plausibly arise from randomized event timing. This blocks candidate promotion for that cell but does not globally reject the hypothesis. |
| `FDR_BLOCKED_DIAGNOSTIC` | A cell passes individual gates but fails BY FDR correction. This blocks candidate promotion. |
| `FDR_MODULE_MISSING_BLOCKER` | Only allowed in implementation readiness reporting, not as an empirical result. Means candidate promotion is impossible until BY FDR exists. |
| `TIMESTAMP_NULL_MODULE_MISSING_BLOCKER` | Only allowed in implementation readiness reporting, not as an empirical result. Means candidate promotion is impossible until timestamp-shuffle null exists. |

### Forbidden verdicts

- `TRADE_READY` — forbidden for this study.
- `EXECUTION_READY` — forbidden for this study.
- `CANDIDATE_FOR_LIVE` — forbidden for this study.

---

## 14. Null policy

The null is **mandatory** for candidate promotion. Use **timestamp randomization** only.

### Null hypothesis

Funding extremes are unrelated to subsequent returns.

### Native null method

The native null must randomize which timestamps are labeled extreme while preserving funding cadence and the BTC return series.

### Timestamp-shuffle null only

```
TIMESTAMP_SHUFFLE_NULL_ONLY
```

**Sign-flipping is forbidden for this hypothesis.**

Do not randomize funding signs.

Do not include "or funding signs" in the precommitment.

**Reason:** The hypothesis is directional. Positive funding predicts reversal down. Negative funding predicts reversal up. Flipping signs tests a scrambled sign-convention world, not the intended null.

### Implementation note

If the timestamp-shuffle null module is not available on this branch, Phase 0 still completes normally because the precommitment is the deliverable. However, the final report must state that the next task is `build missing falsification module`, explicitly not `run funding evaluation`.

---

## 15. Split policy

Use chronological train/holdout:

- 70% train
- 30% holdout

Do not randomly shuffle time.

The split must be based on event timestamps after eligibility filtering.

Train and holdout results are reported separately.

A candidate label requires same-sign evidence in train and holdout.

A tiny holdout must produce `UNDERPOWERED_HOLDOUT_FAILURE` or `NEEDS_MORE_DATA`, not rejection and not promotion.

---

## 16. Output artifacts for the future runner

```
reports/funding_crowding_reversal_v1/<run_id>/summary.json
reports/funding_crowding_reversal_v1/<run_id>/events.jsonl
reports/funding_crowding_reversal_v1/<run_id>/forward_returns.jsonl
reports/funding_crowding_reversal_v1/<run_id>/fdr_summary.json
reports/funding_crowding_reversal_v1/<run_id>/timestamp_shuffle_null/
reports/funding_crowding_reversal_v1/<run_id>/report.md
```

This Phase 0 task does **not** create report output directories or empirical files. Doc-only means doc-only.

---

## 17. Frozen invariant summary

| Invariant | Value |
|---|---|
| `BTC_PRIMARY_FDR_FAMILY_SIZE` | 60 |
| `PAST_ONLY_PERCENTILE_INVARIANT` | Percentile windows use observations strictly before `t` only; 180 calendar day lookback |
| `FUNDING_INTERVAL_METADATA_REQUIRED` | Runner must report funding interval source, changes, and coverage |
| `EVENT_DEDUP_RULE` | One event per funding timestamp per cell; no discretionary cooldown |
| `FUNDING_EVENT_CLUSTERING_UNADJUSTED` | Clustering is disclosed but not adjusted |
| `DIRECTION_MATCHED_BASELINE_REQUIRED` | Baseline is direction-matched random-entry same-horizon return |
| `TIMESTAMP_SHUFFLE_NULL_ONLY` | Timestamp randomization only; sign-flipping forbidden |
| `FDR_METHOD` | Benjamini-Yekutieli (BY) for arbitrary dependence |

---

## 18. Phase boundary

Phase 0 is successful only if this precommitment document is clear enough that a later evaluation runner cannot improvise research decisions after seeing data.

What Phase 0 does:

- Freezes hypothesis, scope, family structure, thresholds, horizons, directions.
- Freezes correction methods (BY FDR, timestamp-shuffle null).
- Freezes cost model, baseline, sample-size policy, acceptance gates.
- Freezes event de-duplication and split policy.
- Freezes verdict taxonomy.
- Records readiness of falsification modules.

What Phase 0 does not do:

- No empirical evaluation.
- No data fetch.
- No report output directories.
- No execution or order path.
- No live trading.
- No private keys.
- No exchange accounts.
