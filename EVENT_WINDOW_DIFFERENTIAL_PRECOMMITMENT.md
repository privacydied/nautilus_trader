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

Only the above signal type, lookback, and horizont set is committed.  No broader
grid is admitted under this precommitment.

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
  **paired capture instance**.

## Capture Window Boundaries

- **Scheduled event time:** sourced from an authoritative macro calendar (Forex
  Factory, Investing.com, or equivalent public source).
- **Event window:** `T−5 min` to `T+15 min` where T = scheduled release time.
- **Baseline window:** `T+90 min` to `T+110 min`.
- **Minimum capture duration:** 1,200 seconds (20 min per window, 40 min total
  per paired instance).
- **Minimum global overlap:** 30 seconds of simultanous tick data across the
  source-target pair within each window.

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
| Primary p-value source         | `native_permutation`               |
| Sensitivity FDR method         | Benjamini–Yekutieli                |
| Sensitivity FDR q-value        | 0.10                               |
| Sensitivity applies to         | Discovery sensitivity only         |

### Required test-family dimensions

Every p-value in the native permutation output must carry these dimensions so
that the FDR script can group and correct correctly:

- `source_asset`
- `target_asset`
- `signal_type`
- `lookback_ms`
- `horizon_ms`
- `window_type` (values: `event` or `baseline`)

The `window_type` dimension is the structural addition that distinguishes this
precommitment from `cross_asset_beta_lag_v1`.  A config with the same
source_asset, target_asset, signal_type, lookback, and horizon is treated as
two separate entries in the test family if one is `event` and the other is
`baseline`.  This allows formal FDR-corrected comparison of the two window
types.

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
- Quarantine is run-scoped, not window-scoped: if either the event window or
  the baseline window is invalid, the entire paired capture is quarantined.
- Re-validation after fixing the data issue is permitted.

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
