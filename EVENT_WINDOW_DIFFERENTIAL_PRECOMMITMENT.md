# Stage 2 Precommitment — Cross-Asset Event-Window Differential V1

## Signal Family

`cross_asset_event_window_differential_v1`

## Hypothesis

During scheduled high-impact macro events (FOMC rate decisions, NFP/CPI/JOLTS releases,
Fed minutes, ECB/BOE/BOJ rate decisions), BTC/ETH trade-flow impulses propagate to
alt-spot pricing (SOL, LINK, DOGE, AVAX) at a materially different rate and
strength compared to non-event baseline windows of equivalent duration.

This is **not** a stress-contingent test (unlike `cross_asset_beta_lag_v1` which
requires BTC 150+ bps).  Instead it is a **comparative** test: event-window impulse
propagation vs same-duration offset baseline window.  The hypothesis is that
event-zone microstructure amplifies cross-asset propagation — either faster
(shorter latency to alt repricing) or stronger (larger bps per impulse) or both.

If the null holds (no differential), the event-window and baseline-window
distributions of per-impulse net returns will be indistinguishable.

## Source & Target Assets

| Role   | Assets                     |
|--------|----------------------------|
| Source | BTC, ETH                   |
| Target | SOL, LINK, DOGE, AVAX      |

## Primary Signal Config

| Parameter     | Value               |
|---------------|---------------------|
| Signal type   | `signed_imbalance`  |
| Lookback      | 30,000 ms  (30 s)   |
| Horizons      | 10,000 ms (10 s)    |
|               | 30,000 ms (30 s)    |
|               | 60,000 ms (60 s)    |
|               | 300,000 ms (5 min)  |

Only the above signal type, lookback, and horizon set is committed.  No broader
grid is admitted under this precommitment.

## Paired Differential Contrast (Primary Null Test)

The acceptance null hypothesis is tested on a **paired differential contrast**,
not on event and baseline windows independently.

### Definition

For each paired capture instance (one event, one baseline, same event_id):

```
delta_metric = event_window_mean_net_bps_per_config - baseline_window_mean_net_bps_per_config
```

The paired delta is computed per config (source_asset, target_asset, signal_type,
lookback_ms, horizon_ms) within each capture.  Across all discovery captures,
the aggregate mean of `delta_metric` is evaluated against the acceptance criteria.

### Why Paired

Event-window and baseline-window observations from the same macro event are
not independent.  The same base market regime, liquidity conditions, and venue
microstructure affect both windows.  Computing the paired difference removes
this shared variance and isolates the event-specific component.

### window_type as a Dimension

`window_type` remains a required test-family dimension.  The value depends
on the row type:

- **Primary FDR rows** (the `native_paired_permutation` output): the value
  is always `"paired_delta"`.
- **Optional diagnostic rows**: raw event-window and baseline-window rows
  may use `"event"` and `"baseline"` respectively.

Diagnostic rows are **not** the primary FDR family.  They must be tagged
`is_diagnostic: true` and must not affect the primary test family count.

The paired contrast is the gate, not `window_type` separation.
**window_type separation alone is not sufficient** — a config that shows
+3 bps in event windows and +1 bps in baseline windows would pass an
independent "event is positive" test (+3 bps) but the paired contrast
would be only +2 bps.

### Native Paired Permutation

The required p-value source is `native_paired_permutation`: a permutation test
that shuffles the event/baseline label within each paired capture, preserving
the pairing structure.  This produces a null distribution of `delta_metric`
under the hypothesis that event and baseline windows are exchangeable.

Machine-readable fields in `event_window_differential_precommitment.json`:

| JSON Field | Value |
|------------|-------|
| `paired_contrast.contrast_type` | `paired_event_minus_baseline` |
| `paired_contrast.paired_permutation_required` | `true` |
| `paired_contrast.primary_pvalue_source` | `native_paired_permutation` |
| `paired_contrast.window_type_separation_insufficient` | `true` |

These fields mirror those set in `primary_fdr.pvalue_source` (see False
Discovery Rate Control section) and serve as cross-references.

### Paired Permutation Output Schema

Each `native_paired_permutation` evaluation produces one primary p-value row
per **paired delta config**, not separate independent p-values for event and
baseline windows separately.

#### Required identifying dimensions for the paired delta row

Every primary p-value row emitted by the paired permutation must carry these
dimensions:

- `source_asset`
- `target_asset`
- `signal_type`
- `lookback_ms`
- `horizon_ms`
- `window_type`  → always `"paired_delta"` for the primary row

The `window_type` dimension uses the literal value `"paired_delta"` for the
primary p-value row, distinguishing it from raw event/baseline diagnostic rows.

#### Diagnostic rows (optional)

Raw event-window and baseline-window p-value rows may be retained for
diagnostic purposes.  If present, their `window_type` values are `"event"`
and `"baseline"` respectively.  These diagnostic rows:

- Are **not** the primary FDR family.
- Must be prefixed or tagged with `is_diagnostic: true` in their metadata.
- Must not affect the `native_paired_permutation` count tracked by the
  primary test family dimensions.

If no paired permutation evaluator exists yet for this signal family, the
required column schema is defined here as a contract for future
implementation.  The FDR step will fail if the incoming p-value table
does not match these required dimensions.

## Event-Window Family Definition

Each capture produces two time windows per event:

- **Event window:** `[-5 min, +15 min]` relative to the scheduled event timestamp
  (20 minutes total).
- **Baseline window:** `[+90 min, +110 min]` relative to the scheduled event
  timestamp (20 minutes, same duration).
- The baseline offset (+90 min) ensures it falls in a non-event macro period
  (well outside the typical 30-minute volatility fade after a major release).
- The baseline window duration matches the event window duration exactly.
- Each pair (event window, baseline window from the same event) forms one
  **paired capture instance**, identified by `event_id`.

## Capture Geometry: Two Separate Captures, Paired by event_id

The canonical collection geometry is **two separate discrete captures**,
one for the event window and one for the baseline window, paired by `event_id`.

### Why Not One Long Capture

A single continuous capture from T−5 min to T+110 min would produce 110 minutes
of tick data, of which only 40 minutes (the event and baseline windows) would
be admitted.  The remaining 70 minutes of inter-window data would have to be
discarded, which is wasteful and creates a data-selection temptation.  Two
separate discrete captures are simpler to validate, admit, and audit.

### Duration Fields

| Field | Value |
|-------|-------|
| `event_window_duration_seconds` | 1200 (20 min) |
| `baseline_window_duration_seconds` | 1200 (20 min) |
| `paired_admitted_duration_seconds` | 2400 (40 min total) |

The `paired_admitted_duration_seconds` is the sum of both windows: the total
amount of tick data admitted to the corpus for a single paired capture.
Inter-window data is never admitted.

## Capture Window Boundaries

- **Scheduled event time:** sourced from an authoritative macro calendar (Forex
  Factory, Investing.com, or equivalent public source).  See
  `EVENT_WINDOW_EVENT_LIST_TEMPLATE.md` for the event-list artifact convention.
- **Event window:** `T−5 min` to `T+15 min` where T = scheduled release time.
- **Baseline window:** `T+90 min` to `T+110 min`.
- **Minimum capture duration:** 1,200 seconds per window (each window must
  produce at least 1,200 seconds of valid tick data).
- **Minimum global overlap:** 30 seconds of simultaneous tick data across the
  source-target pair within each window.
- **Baseline overlap quarantine:** If another high-impact scheduled macro event
  has its event window overlapping the baseline window of this capture, the
  paired capture is invalid and must be quarantined.  This prevents baseline
  contamination by a different macro event.

## Baseline-Window Rule

The baseline window is **never** inherited from the old 60-second default
used in earlier OHLCV studies.  It is explicitly parameterised in this
precommitment:

```
baseline_window_offset_minutes_from_event: 90
baseline_window_duration_seconds: 1200
```

The baseline must always be defined in terms of wall-clock offset from the
scheduled event, not as a fixed post-capture window.  This guarantees temporal
independence from the event window and prevents post-hoc boundary selection.

No post-hoc event window selection is allowed.  The window boundaries are
committed before any capture begins.  If a capture starts late (missed the
T−5 boundary), it may still proceed from the actual start time, but the
`T+15` end is absolute (not extended).  This ensures fixed-width windows.

## Frozen Event List Convention

An event list artifact must exist before any event-window capture is performed.
The event list is a **frozen, pre-committed** record: events are included or
excluded before their capture window opens, based on the scheduled macro calendar.

See `EVENT_WINDOW_EVENT_LIST_TEMPLATE.md` for the human-readable template and
`event_window_event_list.schema.json` for the machine-readable schema.

Each event in the list includes:
- `event_id` — unique identifier for this paired capture
- `event_name` — human-readable label (e.g. "FOMC Rate Decision 2026-06")
- `scheduled_event_utc` — the scheduled release time
- `calendar_source` — where the event was sourced from
- `calendar_source_snapshot_utc` — when the source was last checked
- `event_window_start_utc` / `event_window_end_utc` — computed from the
  scheduled time ± the committed offsets
- `baseline_window_start_utc` / `baseline_window_end_utc` — computed similarly
- `inclusion_reason` — why this event was selected
- `exclusion_reason` — populated if the event was later excluded (null if
  included)

The event list is not an active collection schedule.  It is an audit artifact
that documents which events were committed to before their window opened,
preventing post-hoc cherry-picking of events that produced interesting
results.  No event may be added to the list retroactively.

## Cost Model

| Component                | Value   |
|--------------------------|---------|
| Taker fee                | 40 bps  |
| Slippage buffer          | 5 bps   |
| Quote mismatch buffer    | 5 bps   |
| **All-in cost per event** | **50 bps** |

The cost model is identical to the cross-asset beta lag precommitment.
No post-hoc cost adjustment.

## Minimum Valid Events per Config

| Parameter                     | Value |
|-------------------------------|-------|
| Minimum valid events per config (per window) | 50 |
| Minimum valid events per config (paired)     | 100 (50 event + 50 baseline) |

## Minimum Validated Captures Before Stage 2 Evaluation

| Parameter                             | Value |
|---------------------------------------|-------|
| Minimum validated paired captures     | 10    |
| Minimum corpus-eligible event windows | 10    |

Each paired capture (event + baseline from the same scheduled event) counts as
one validated capture.  The minimum of 10 applies to fully validated, non-quarantined,
non-burned paired captures.

## False Discovery Rate Control

| Parameter                      | Value                              |
|--------------------------------|------------------------------------|
| Primary FDR method             | Benjamini–Hochberg                 |
| Primary FDR q-value            | 0.10                               |
| Primary p-value source         | `native_paired_permutation`        |
| Sensitivity FDR method         | Benjamini–Yekutieli                |
| Sensitivity FDR q-value        | 0.10                               |
| Sensitivity applies to         | Discovery sensitivity only         |

### Required test-family dimensions

Every p-value in the native paired permutation output must carry these dimensions so
that the FDR script can group and correct correctly:

- `source_asset`
- `target_asset`
- `signal_type`
- `lookback_ms`
- `horizon_ms`
- `window_type` → `"paired_delta"` for the primary row; `"event"`/`"baseline"` for diagnostic rows only

The `window_type` dimension is the structural addition that distinguishes this
precommitment from `cross_asset_beta_lag_v1`.  For the primary FDR family,
the value must always be `"paired_delta"`.

A config with the same source_asset, target_asset, signal_type, lookback,
and horizon is treated as two separate entries in the test family if one is
`"event"` and the other is `"baseline"` (diagnostic rows only).  This allows
FDR-corrected comparison of the two window types for diagnostic purposes,
while the primary FDR family is defined on the `"paired_delta"` rows.

**However**, `window_type` separation is not sufficient by itself.  The
primary null test is the paired contrast `delta = event - baseline`
(see "Paired Differential Contrast" section).  The paired p-value must
come from a `native_paired_permutation` test, not from independent
permutations of event and baseline separately.

## Holdout Split Rule

- **Method:** Temporal (ordered by `capture_start_utc` of the event window)
- **Discovery fraction:** 70 % (earliest captures)
- **Test fraction:** 30 % (latest captures)
- **Minimum validated paired captures:** 10
- **Test set sealed:** Yes — the test set is never inspected during discovery
  mode.

## Capture Admission Rules

- Exclude quarantined runs
- Exclude burned runs
- Exclude `FAST_DIAGNOSTIC` captures
- Require `FULL_ACTIVE` or `EVENT_ACTIVE` capture mode
- Require validation passed
- If another high-impact scheduled macro event overlaps the baseline window,
  the paired capture is quarantined (baseline overlap quarantine).
- Quarantine is run-scoped, not window-scoped: if either the event window or
  the baseline window is invalid, the entire paired capture is quarantined.
- Re-validation after fixing the data issue is permitted.

## Burn Rules

- Burn when the final discovery-criteria survivor set is empty.
- Burn the entire validated corpus for the signal family, not just individual
  runs.
- Do not inspect the sealed test set when discovery fails.
- If BH kills everything, do not relax *q* on the same corpus.
- If the economic bar kills everything, do not lower it on the same corpus.
- If the consistency bar kills everything, do not lower it on the same corpus.
- If the tail bar kills everything, do not relax the tail floor on the same
  corpus.
- If the corpus is below the minimum size, abort and collect more — do not
  burn.
- Criteria changes after discovery invalidate the holdout.
- Failed discovery burns the corpus for the signal family.
- If a final discovery-criteria survivor set is empty, write a burn record for
  the entire validated corpus (both discovery and test run IDs) and do not
  inspect the test set.

## Quarantine Rules

- Quarantined runs are excluded from corpus admission.
- A run may be quarantined if validation fails, manifest is corrupt, or
  the paired baseline window was truncated below the minimum duration.
- If another high-impact scheduled macro event overlaps the baseline window,
  the paired capture is quarantined (baseline overlap quarantine).

## Discovery Acceptance Criteria

| Criterion                                          | Value    | Notes                     |
|----------------------------------------------------|----------|---------------------------|
| Minimum aggregate mean net-of-cost return          | +2 bps per event | Economic bar    |
| Minimum same-sign capture fraction                 | 70 %     | Rounded up with `ceil`    |
| Minimum valid events per config                    | 50       | Per-window, not paired    |
| Worst capture mean net-of-cost floor               | −5 bps   | Tail sanity guard         |
| Requires primary BH survival                       | Yes      | Configs killed by BH excluded |

## Holdout Acceptance Criteria

| Criterion                                          | Value         | Notes                     |
|----------------------------------------------------|---------------|---------------------------|
| Minimum aggregate mean net-of-cost return          | +2 bps per event | Same as discovery     |
| Requires same sign as discovery                    | Yes           | Directional coherence     |
| Minimum valid events per config                    | 50            | Same as discovery         |
| Worst capture mean net-of-cost floor               | −5 bps        | Same as discovery         |
| Frozen config only                                 | Yes           | Only discovery survivors  |
| No threshold/parameter changes                     | Yes           | Locked                    |

## Data Independence

The splitter orders captures by `capture_start_utc` only.  It does not sort by
return, p-value, event count, or any signal-performance metric.  The split is
determined before any evaluator output is inspected.

This file is the human-readable precommitment.  The machine-readable counterpart
is `event_window_differential_precommitment.json`.  Both files must declare the
same values.  A test asserts they match.

FAST_DIAGNOSTIC and arm-only captures are diagnostics only and excluded from
Stage 2 corpus admission.

Any modification to this document or
`event_window_differential_precommitment.json` after the discovery-set
evaluator has been run on the corpus invalidates the holdout test.

## No Post-Hoc Event Window Selection

All event windows are defined relative to the scheduled macro event timestamp
before any capture begins.  No window may be widened, narrowed, or shifted after
the fact based on observed price action.  If a capture is missed (started late),
the window shrinks but does not shift to a different calendar point.

If a scheduled event is cancelled or rescheduled, the capture is discarded
(not re-classified to a different event or used as a test capture).

---

*This file was created before any event-window corpus-admissible capture was
attempted.  The parameters are pre-data operational thresholds chosen to make
the test auditable, not theoretically optimal values fitted to data.*
