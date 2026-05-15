# Stage 2 Precommitment — Derivatives Source → Spot Target Lead-Lag V2

## Purpose

This document records the pre-committed criteria that govern Stage 2
discovery and holdout evaluation for the
`derivatives_source_spot_target_lead_lag_v2` signal family.  The
parameters and rules below were committed **before** any Stage 2
corpus-admissible Full Active capture was collected.

Code reads `stage2_precommitment.json` as the machine‑readable source
of truth.  This Markdown file and the JSON must declare the same
values.  A test asserts they match.

## False Discovery Rate Control

| Parameter | Value |
|-----------|-------|
| Primary FDR method | Benjamini‑Hochberg |
| Primary FDR q‑value | 0.10 |
| Primary p‑value source | `native_permutation` |
| Sensitivity FDR method | Benjamini‑Yekutieli |
| Sensitivity FDR q‑value | 0.10 |
| Sensitivity applies to | Discovery sensitivity only |

### Required test‑family dimensions

Every p‑value in the native permutation output must carry these
dimensions so that the FDR script can group and correct correctly:

- `source_venue`
- `target_venue`
- `symbol`
- `signal_type`
- `lookback_ms`
- `horizon_ms`

If any native permutation p‑value is missing for any config in this
test family, the FDR step **fails**.

## Holdout Split Rule

- **Method:** Temporal (ordered by `capture_start_utc`)
- **Discovery fraction:** 70 % (earliest captures)
- **Test fraction:** 30 % (latest captures)
- **Minimum validated captures:** 10
- **Test set sealed:** Yes — the test set is never inspected during
  discovery mode.

## Minimum Corpus Size

At least **10 validated FULL_ACTIVE captures** (non‑quarantined,
non‑burned) must exist before the splitter runs.  If fewer than 10 are
available, Stage 2 evaluation does not proceed.

## Capture Admission Rules

- Exclude quarantined runs
- Exclude burned runs
- Exclude `FAST_DIAGNOSTIC` captures
- Require `FULL_ACTIVE` capture mode
- Require validation passed

## Discovery Acceptance Criteria

| Criterion | Value | Notes |
|-----------|-------|-------|
| Minimum aggregate mean net‑of‑cost return | **+2 bps per event** | Economic bar |
| Minimum same‑sign capture fraction | **70 %** | Rounded up with `ceil` |
| Minimum valid events per config | **50** | Pinned — not a mutable default |
| Worst capture mean net‑of‑cost floor | **−5 bps** | Tail sanity guard |
| Requires primary BH survival | Yes | Configs killed by BH are excluded |

## Holdout Acceptance Criteria

| Criterion | Value | Notes |
|-----------|-------|-------|
| Minimum aggregate mean net‑of‑cost return | **+2 bps per event** | Same as discovery |
| Requires same sign as discovery | Yes | Directional coherence |
| Minimum valid events per config | **50** | Same as discovery |
| Worst capture mean net‑of‑cost floor | **−5 bps** | Same as discovery |
| Frozen config only | Yes | Only configs that survived discovery |
| No threshold/parameter changes | Yes | Holdout criteria are locked |

## Burn Rules

- Burn when the final discovery‑criteria survivor set is empty.
- Burn the entire validated corpus for the signal family, not just
  individual runs.
- Do not inspect the sealed test set when discovery fails.
- If BH kills everything, do not relax *q* on the same corpus.
- If the economic bar kills everything, do not lower it on the same
  corpus.
- If the consistency bar kills everything, do not lower it on the same
  corpus.
- If the corpus is below the minimum size, abort and collect more —
  do not burn.
- Criteria changes after discovery invalidate the holdout.
- Failed discovery burns the corpus for the signal family.

## Data Independence

The splitter orders captures by `capture_start_utc` only.  It does not
sort by return, p‑value, event count, or any signal‑performance
metric.  The split is determined before any evaluator output is
inspected.

## Audit Statement

Any modification to this document or `stage2_precommitment.json` after
the discovery‑set evaluator has been run on the corpus invalidates the
holdout test.

---

*This file was created before any Stage 2 corpus‑admissible Full Active
capture was attempted.  The parameters are pre‑data operational
thresholds chosen to make the test auditable, not theoretically optimal
values fitted to data.*
