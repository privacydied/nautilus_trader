# Edge Miner Phase Plan

> **Status disclaimer:** This is a forward-looking architecture plan. Phases 1–6 are not shipped
> capabilities. Nothing in this document changes existing verdicts, existing locked gates, or
> REJECTED_RESEARCH.md.

---

## Overview

`venue_agnostic_signal_observer` is the codebase Phases 1–6 extend. It already contains fragments
of the future Edge Miner and some diagnostic tooling, but it is currently per-hypothesis rather
than frozen-grid and does not yet have the full Validator / Governance / Corpus / Shadow / Bot
promotion pipeline.

**One-liner:**
> "The observer is the codebase Phases 1–6 extend; it contains weak, per-hypothesis precursors to
> Phases 3 and 5, while Phases 1, 2, 4, and 6 are mostly unbuilt."

The six phases are new layers being added *into* the existing `venue_agnostic_signal_observer`
project. They are not six separate projects.

---

## Phases

### Phase 1 — Validator Estimator Layer

**Future implementation branch:** `validator/estimator-layer`

**Phase 1 is the biggest genuinely missing piece.**

#### Purpose

Build the pure-logic oracle *before* building the Miner. The Miner is the easiest place to
accidentally create fake edge through lookahead leakage, off-by-one alignment bugs, bad
entry-delay handling, overlapping-label mistakes, or timestamp leakage. The Validator can be
tested independently on synthetic data before any real edge exists.

#### Includes

- Deflated Sharpe Ratio (DSR)
- CSCV / PBO (Combinatorial Symmetric Cross-Validation / Probability of Backtest Overfitting)
- CPCV (Combinatorial Purged Cross-Validation)
- Purging
- Time-domain embargo
- Synthetic null tests
- Synthetic planted-signal tests
- Synthetic planted-but-untradeable tests
- Synthetic decaying-signal tests

---

#### Estimator Roles

**CPCV**
- Candidate/cell-level honest performance estimation.
- Uses purging and time-domain embargo.
- Applies to timestamped observations with explicit label intervals.
- Should catch non-stationary candidates — signals that are real early and gone later.

**PBO / CSCV**
- Grid-level overfitting diagnostic.
- Measures whether the selection process over the frozen grid is overfit-prone.
- Takes the full grid/cell performance matrix, not a single candidate.
- Must **not** be implemented as `pbo(candidate)`.

**DSR**
- Candidate-level deflated performance diagnostic.
- Consumes effective number of independent trials, not raw grid cell count.
- Must account for correlated grid cells.
- Must account for non-IID event returns or explicitly require de-overlapped returns.
- May return `INSUFFICIENT_DATA` instead of forcing a numeric verdict.
- Grid/corpus artifacts should eventually record `raw_cell_count` and `effective_trial_count`.

**FDR**
- Frozen-family multiple-testing control.
- Uses the grid hash denominator.
- Not the same thing as DSR or PBO.

**Native null / MCPT**
- Timing falsification.
- Tests whether source → target timing beats randomized source timing.
- A null pass does not prove tradeability.

---

#### Design Decisions and Pitfalls

##### 1. Estimator Versioning and Append-Only Evidence

Every estimator output must carry estimator identity and version metadata. Required fields,
conceptually:

```
estimator_name
estimator_version
estimator_config_hash
code_git_sha              (where available)
input_dataset_hash        (where available)
parent_grid_hash          (where available)
candidate_hash            (where available)
generated_at_utc
```

**Invariant:** No estimator output may be replaced in place; a changed estimator version produces
a new appended evidence event.

If a DSR bug is fixed later, the old result remains part of the audit trail and the corrected
result becomes a new event. The evidence ledger must show the change, not erase history.

---

##### 2. DSR Effective Trial Count

**Method:** Cluster grid cells by return-series correlation and use the number of correlation
clusters as the effective number of independent trials.

Required details:

- Compute pairwise correlation between cell return series after aligning observations to the same
  evaluation basis.
- Cluster cells whose return-series correlation exceeds a predeclared threshold.
- Record the threshold in the grid or validator metadata.
- `effective_trial_count` = number of clusters.
- `raw_cell_count` is still recorded but is not the number consumed by DSR.
- DSR consumes `effective_trial_count`.
- The estimator output must echo both `raw_cell_count` and `effective_trial_count`.

**Circularity note:** `effective_trial_count` is per-grid-per-corpus. It is computed from
return-series correlations on a specific corpus of captures. If the corpus changes, the
correlation structure can change, so `effective_trial_count` must be recomputed for corpus
recurrence in Phase 4. It must not be carried forward forever as a constant.

**Why this matters:** Raw grid cell count is not the honest DSR trial count. A frozen grid may
contain 48,000 cells, but many cells are highly correlated. `BTC 30s 30bps → SOL 180s` and
`BTC 30s 50bps → SOL 180s` are not independent experiments.

**Failure modes to avoid:**

| Mistake | Consequence |
|---|---|
| Using `raw_cell_count` | May over-deflate and reject real signals |
| Using N=1 | Under-deflates; turns DSR into theatre |
| Leaving trial count as "estimate somehow" | Makes the verdict arbitrary |
| Carrying `effective_trial_count` across a changed corpus | Makes DSR stale |

---

##### 3. DSR Non-IID / Autocorrelation Adjustment

Event returns are not automatically IID. The project evaluates forward returns at overlapping
horizons such as 60 s, 180 s, and 300 s. Overlapping labels create autocorrelation and distort
Sharpe-like estimates if handled naively.

**Rule:** DSR input returns must either:
- **(A)** be de-overlapped before Sharpe / DSR computation, or
- **(B)** use an autocorrelation-adjusted volatility estimate.

**Default:** Use de-overlapped return series for DSR where possible. If de-overlap would leave too
few observations, use an explicitly documented autocorrelation-adjusted volatility estimate and
mark the result as `lower_confidence`.

**Required third branch:** If there are too few observations even for an autocorrelation-adjusted
estimate, DSR must return an `INSUFFICIENT_DATA` diagnostic status. It must not force a numeric
score or pass/fail verdict.

**DSR result schema (conceptual):**

```
dsr
sharpe
mean_return
volatility
effective_sample_size
skew
kurtosis
raw_trial_count
effective_trial_count
effective_trial_method
effective_trial_correlation_threshold
threshold
diagnostic_status
input_metadata

volatility_adjustment:
  method: deoverlap | autocorrelation_adjusted | none
  autocorrelation_adjustment_used: bool
  deoverlap_method: str | null
  adjusted_sharpe: float | null
  confidence: normal | lower_confidence | insufficient_data
  reason: str | null
```

Adjustment metadata must be nested under a single `volatility_adjustment` object. Do not flatten
all adjustment fields into the top-level DSR schema.

---

##### 4. PBO / CSCV Cost and Scope

PBO is combinatorial and grid-level.

- PBO splits the timeline into S blocks and evaluates combinations C(S, S/2).
- S = 16 means 12,870 partitions.
- Each partition re-ranks every cell in the grid.
- PBO cost scales as `partition_count × n_cells × cost_per_eval`.
- PBO should run **once per frozen grid or grid-family diagnostic**, not per candidate.
- The API shape must reflect this.

**PBO result fields (conceptual):**

```
n_blocks
n_partitions
n_cells
selected_cell_rank_diagnostics
pbo_estimate
grid_level_status
estimator_version_metadata
input_metadata
```

---

##### 5. CSCV and CPCV Partitioners Must Remain Separate

Do not share one generic combinatorial partitioner between CSCV/PBO and CPCV.

- CSCV/PBO uses blocks for overfitting diagnostics.
- CPCV uses purging and embargo for honest performance estimation.
- Sharing partition code invites future bugs where purging is accidentally added to PBO or removed
  from CPCV.

Duplicated combinatorial logic is acceptable here if it preserves statistical semantics and test
clarity.

---

##### 6. Time-Domain Embargo

Embargo must be based on **wall-clock time**, not event count.

Event density varies across stress and quiet windows. A fixed event-count embargo
under-embargoes dense stress windows and over-embargoes sparse quiet windows.

Rules:

- Each observation has a label interval.
- Purging removes train observations whose label interval overlaps the test interval.
- Embargo removes observations for a wall-clock duration *after* the test interval.
- Embargo duration should be derived from label horizon, entry delay, and staleness buffer.
- Event-index embargo is **forbidden**.

**Required test concept:** Construct synthetic events with bursty arrivals — a dense stress
cluster, a sparse quiet tail, and an explicit label horizon. Assert that embargo removes the same
wall-clock duration regardless of density. An index-based embargo would fail this test because it
removes a constant count instead of a constant time span.

---

##### 7. Synthetic Data Populations

Phase 1 synthetic tests need four populations:

| Population | Description | Expected Validator outcome |
|---|---|---|
| **A. Null / no-signal** | No true edge | Reject or refuse promotion |
| **B. Planted signal** | True positive, sufficient effect size | Score more favorably than null |
| **C. Planted-but-untradeable** | Statistically real effect; below cost floor | Statistical diagnostics may detect the effect; verdict logic refuses promotion because net-of-cost edge is dead |
| **D. Decaying signal** | Real signal in early data; weakens or vanishes later | CPCV and purged folds expose non-stationarity; naive in-sample looks better than honest OOS |

The planted-but-untradeable case matters because the project history shows many effects die after
fees, spread, slippage, latency, or fill assumptions.

The decaying-signal case matters because stress-regime behavior is likely non-stationary. A
pattern can be real in one market regime and gone in the next.

The Validator must distinguish:

- statistically real
- economically tradeable
- non-stationary and unreliable

A 0.1 bps signal may be real and still useless. A signal that worked early and vanished later may
be real historically and still unsafe to promote.

---

##### 8. Estimator Output Shape

Each estimator must eventually return structured evidence, not only a boolean, because the Phase 2
evidence ledger will need to record why a verdict happened.

**CPCV result (conceptual):**

```
n_splits
train_test_intervals
purged_count
embargoed_count
per_split_returns
per_split_hit_rate
per_split_sharpe_like
fold_stability_metrics
nonstationarity_diagnostics   (where available)
estimator_version_metadata
input_metadata
```

**Validator summary result (conceptual):**

```
candidate_hash              (if available)
parent_grid_hash            (if available)
estimator_versions
estimator_config_hashes
input_dataset_hash          (if available)
cpcv_result
dsr_result
pbo_result
fdr_result
mcpt_result
cost_floor
economic_viability_status
statistical_validity_status
nonstationarity_status
final_diagnostic_status
```

---

### Phase 2 — Governance Spine

**Future implementation branch:** `governance/grid-candidate-ledger`

#### Purpose

Provide the immutable governance spine and permission system.

#### Includes

- Grid lock
- Candidate lock
- Evidence ledger
- Approval events
- Revocation events
- Demotion events
- Current-state replay

#### Key Rules

- Grid lock freezes the full search universe before discovery.
- Candidate lock freezes the individual surfaced candidate.
- Evidence ledger is append-only.
- Approval is derived from replaying the ledger, not from blindly trusting a manifest file.
- Revocation and demotion must be append-only events.
- Stale approved manifests must not authorize trading by themselves.
- Estimator output updates are appended as new evidence events, never overwritten.

#### Ledger Conflict Resolution

Events are ordered by ledger sequence number, not wall-clock timestamp alone.

Each event contains: `event_time_utc`, `written_at_utc`, `candidate_hash`, `event_type`, and
monotonic `ledger_index`.

Replay rules:

1. Apply events in `ledger_index` order.
2. If two events touch the same candidate, later `ledger_index` wins unless event precedence says
   otherwise.
3. Revocation, kill-switch, corruption, and ledger-integrity events have hard precedence over
   approval, regardless of timestamp.
4. Supersession events replace prior approval state only when they reference the prior
   `candidate_hash`.
5. Unknown event types cause **fail-closed** replay.
6. Changed estimator versions produce new evidence events; they do not replace old evidence events.

`event_time_utc` is metadata. It must not be the sole ordering authority because concurrent
writers and clock skew can make timestamp ordering ambiguous.

#### Corrupt / Truncated Ledger Handling

Replay must fail closed. If the evidence ledger is missing, truncated, corrupt JSONL, contains a
hash mismatch, contains unknown event types, contains invalid sequence numbers, contains invalid
candidate lineage, contains ambiguous estimator replacement, or cannot be fully replayed, then:

- The bot refuses to trade.
- No manifest is approved.
- The failure is logged as a safety event.
- The system never fails open.

The bot must derive permission at load time by replaying current ledger state. It must not say
"approved manifest exists, therefore I can trade." It must say: "Replay the ledger. Confirm
latest candidate state is approved, unexpired, unrecalled, not demoted, not killed, not corrupt,
not superseded, and supported by current estimator evidence. Then load manifest."

---

### Phase 3 — Edge Miner

**Future implementation branch:** `miner/frozen-grid-sweeps`

#### Purpose

Generalize existing observer forward-return evaluation and signal generation into a frozen-grid
scanner.

#### Includes

- Frozen-grid vectorized sweeps
- VectorBT-style execution semantics
- Synthetic no-signal tripwire through Validator

#### What Already Exists

The observer already has chunks of Phase 3:

- Signal generators (`signals.py`)
- Forward-return evaluator (`forward_returns.py`)
- GPU forward returns (`forward_returns_gpu.py`, `lead_lag_heatmap_gpu.py`)
- Derivatives-source to spot-target evaluation (`derivatives_lead_lag.py`)
- Null-worthy group export paths (`mcpt_export.py`)

But the current implementation is **per-hypothesis, not frozen-grid**.

Phase 3 therefore means:

- Generalize what exists.
- Do not build everything from scratch.
- Do not let the Miner mutate the grid.
- Do not give the Miner validation authority.
- Do not give the Miner promotion authority.
- Do not give the Miner execution authority.

#### VectorBT Guidance

- Use VectorBT-style vectorized execution semantics as the reference for sweeps.
- Do not casually fork VectorBT into the project.
- Prefer a thin adapter or minimal local semantics if/when implementation begins.
- Governance artifacts remain native to `venue_agnostic_signal_observer`.

#### Locked Rejection Protection

Cross-reference `REJECTED_RESEARCH.md` and the project's locked gates.

A frozen discovery grid must not silently re-test an already locked-rejected gate as "just another
grid cell" unless the structural-change rule is satisfied and explicitly recorded.

Examples of locked-rejected gates a new grid must not silently include:

- Same-asset cross-venue tick lead-lag
- Previously rejected derivatives-source spot-target lead-lag
- Previously rejected trade-flow impulse variants
- Previously rejected Polymarket BTC Up/Down liquidity/actionability gates
- Any other locked study in `REJECTED_RESEARCH.md`

If a grid cell overlaps a locked-rejected hypothesis, the grid spec must either:
- Exclude it, or
- Mark it as `blocked_by_locked_rejection`, or
- Include a written structural-change rationale explaining why the new test is materially different.

**Structural-change rationale authority:** The rationale must be human-authored, recorded in the
grid lock artifact or a grid-lock-linked rationale artifact, and covered by the grid hash. The
Miner must not self-certify structural-change exemptions.

Examples of structural change:

- Different venue/instrument class
- Different fee tier/cost model with evidence
- Different execution mode with shadow fill model
- Different regime gate precommitted before capture
- Different data type that changes the mechanism (not merely a different threshold)

The Edge Miner must not launder a rejected hypothesis back into the pipeline by renaming it as a
generic grid cell.

---

### Phase 4 — Corpus Recurrence

**Future implementation branch:** `validator/corpus-recurrence`

#### Purpose

Determine whether frozen candidates recur across independent captures and stress windows.

#### Includes

- Repeated capture aggregation
- Current candidate state updates
- Demotion evidence
- Revocation evidence
- Recomputation of `effective_trial_count` for changed corpora

#### Rules

- Repeated evidence matters more than best single capture.
- Sort by recurrence/consistency before best return.
- Single-window artifacts should be demoted or kept diagnostic-only.
- New contradictory corpus evidence can revoke or demote a previously approved candidate through
  Phase 2 ledger events.
- `effective_trial_count` is per-grid-per-corpus and must be recomputed when the corpus changes.

#### State Authority Boundary

Phase 4 does not own a separate state store. Phase 4 emits corpus-evidence, demotion, revocation,
or supersession events into the Phase 2 evidence ledger. Current candidate state is still derived
by Phase 2 ledger replay.

---

### Phase 5 — Shadow Executor

**Future implementation branch:** `shadow/cross-venue-fill-model`

#### Purpose

Test whether statistically validated candidates can actually be captured.

#### Includes

- Extend the L2 maker paper simulator
- Cross-venue trigger → target book fill model
- Queue / fill / staleness / spread / depth simulation
- Fill-model uncertainty reporting

#### Relationship to Existing Work

The existing rejected V7 L2 maker simulator is the starting point, but not a tiny extension. The
old simulator tested one venue's maker book behavior. New candidates may be cross-venue beta-lag:

- Source event on Binance/Bybit perp
- Target entry on Kraken/Coinbase spot
- Entry delay of 0 / 5 / 15 / 30 s
- Target book staleness constraints
- Maker-or-taker decision on target venue
- Exit model after target catches up or signal expires

Phase 5 is therefore partly reuse and partly new cross-venue trigger plumbing.

#### Assumption Boundary

The shadow fill model is an estimate, not ground truth.

- Shadow results must include uncertainty / error bars.
- A shadow result with positive mean but wider uncertainty than the edge is not a pass.
- Example: shadow net +8 bps with ±15 bps fill-model uncertainty is not a valid pass.
- Uncertainty must be reported alongside fill rate, partial fills, forced exits, and capacity
  estimate.
- The Shadow Executor must not become the new place optimism hides.

**Shadow must report:**

```
shadow_fill_rate
missed_fill_rate
partial_fill_rate
forced_exit_rate
entry_spread
exit_spread
queue_penalty
staleness_rejects
shadow_net_bps
fill_model_uncertainty_estimate
lower_confidence_bound
capacity_estimate
```

**Shadow must not place orders.**

---

### Phase 6 — Trading Bot

**Future implementation branch:** `bot/manifest-ledger-gated-nautilus`

#### Purpose

Execute only exact approved frozen rules under exact approved conditions.

#### Includes

- NautilusTrader only
- Manifest + ledger replay
- No discovery
- No tuning
- No promotion

#### Rules

The bot consumes current approved candidate state only. It must derive permission at load time by
replaying current ledger state.

The bot must not:

- Discover signals
- Scan new grids
- Tune parameters
- Reinterpret candidates
- Promote candidates
- Ignore ledger revocations
- Run expired manifests
- Run without hash checks
- Run without kill switches
- Run if ledger replay fails
- Run if manifest exists but current ledger state is not approved

The bot says: "I am allowed to quote this exact frozen rule under these exact conditions, with
these exact limits."

---

## Mapping to Existing `venue_agnostic_signal_observer`

| Existing component | Phase it seeds |
|---|---|
| `forward_returns.py`, `forward_returns_gpu.py` | Phase 3 — proto-Edge-Miner (per-hypothesis, not frozen-grid) |
| `signals.py` | Phase 3 — signal generators |
| `lead_lag_heatmap_gpu.py`, `derivatives_lead_lag.py` | Phase 3 — GPU/derivatives-source sweeps |
| `mcpt_export.py`, `permutation_null*.py` | Phase 1-adjacent — native permutation/null tests |
| `candidate_falsification.py` | Phase 1-adjacent — falsification diagnostics |
| `stage2_fdr.py` | Phase 2 seed — FDR / frozen-family multiple-testing control |
| `stage2_gate_watcher.py`, `stage2_precommitment_utils.py` | Phase 2 seed — precommitment locks |
| `precommitments/` | Phase 2 seed — precommitted locks and locked gates |
| `discovery/` | Phase 2 seed / Phase 3 seed — grid/candidate locking infrastructure |
| Rejected V7 L2 maker simulator (separate sub-project) | Phase 5 — starting point for cross-venue fill model |

**What is genuinely new in Phase 1:**
DSR, PBO/CSCV, CPCV, purged splitting, time-domain embargo, effective trial estimation,
de-overlapped / autocorrelation-adjusted DSR inputs, estimator versioning, and synthetic null /
planted / planted-but-untradeable / decaying-signal validator tests are genuinely new.

**Phase 4** corpus recurrence exists only partially and needs to become candidate-state-aware
while writing back into the Phase 2 ledger.

**Phase 6 Bot** is mostly unbuilt and should remain last.

---

## Correct Build Order

```
1. Validator estimator layer          (Phase 1)
2. Governance Spine                   (Phase 2)
3. Edge Miner                         (Phase 3)
4. Corpus recurrence                  (Phase 4)
5. Shadow Executor                    (Phase 5)
6. Trading Bot                        (Phase 6)
```

### Why Validator Comes First

- It is pure logic — no market connectivity required.
- It can be tested on synthetic null / planted / planted-but-untradeable / decaying-signal data.
- It gives the project an oracle before the Miner exists.
- It can later catch Miner leaks and alignment bugs.

### Why Miner Does Not Come First

- A fast vectorized sweep with a subtle lookahead bug can produce convincing fake candidates.
- The Validator cannot repair biased return series if the Miner generated them incorrectly.
- Miner should be developed with Validator tripwires from day one.

---

## Git Branch Map

### Current documentation branch

| Branch | Purpose |
|---|---|
| `docs/edge-miner-phase-plan` | Documentation-only branch for this six-phase architecture plan |

### Future implementation branches

| Branch | Phase |
|---|---|
| `validator/estimator-layer` | Phase 1 — DSR, CSCV/PBO, CPCV, purging, time-domain embargo, synthetic validator fixtures |
| `governance/grid-candidate-ledger` | Phase 2 — grid locks, candidate locks, evidence ledger, approval/revocation events, current-state replay |
| `miner/frozen-grid-sweeps` | Phase 3 — frozen-grid vectorized sweeps, VectorBT-style execution semantics |
| `validator/corpus-recurrence` | Phase 4 — repeated capture aggregation, corpus-evidence events, demotion/revocation evidence, per-grid-per-corpus effective-trial recomputation |
| `shadow/cross-venue-fill-model` | Phase 5 — L2 maker paper simulator extended into cross-venue trigger → target-book fill modelling |
| `bot/manifest-ledger-gated-nautilus` | Phase 6 — NautilusTrader execution consuming only approved manifest + current ledger replay state |

### Branch Rules

- Each future branch should implement only its named phase.
- Do not mix docs-only planning with implementation branches.
- Do not implement Bot before Validator, Governance, Miner, Corpus, and Shadow evidence exists.
- Do not merge execution-facing work before observer-only safety checks pass.
- Do not reuse a rejected-research branch for new Edge Miner work.
- Do not use a generic branch like `edge-miner-all` or `trading-bot-v1` for multiple phases.

---

## Non-Goals / Out of Scope

- The pipeline does not aim to raise hit rate by widening the grid.
- The pipeline does not aim to rescue already rejected hypotheses without structural-change
  rationale.
- The pipeline does not optimize until something passes.
- The pipeline does not treat more tested cells as progress by itself.
- The pipeline does not promote statistically real but economically dead signals.
- The pipeline does not consider zero surviving candidates to be failure.
- A terminal funnel of zero surviving candidates is an expected and successful outcome if the
  evidence says no edge survived.
- Rejection is a valid product of the pipeline.
- Nothing in this plan authorizes live trading.
- Nothing in this plan changes existing verdicts.
- Nothing in this plan updates `REJECTED_RESEARCH.md`.

> "The goal is not to force a survivor through the funnel. The goal is to make every survivor
> expensive, auditable, and rare."

---

## Non-Negotiable Invariants

- No unlocked discovery scans.
- No mutable grid inside a run family.
- No candidate validation without parent grid hash.
- No candidate mutation after freeze.
- No event-index embargo.
- No PBO per-candidate API.
- No shared CSCV/CPCV partitioner unless tests prove their semantics cannot cross-contaminate.
- No DSR using raw grid cell count as the only trial count.
- No DSR without de-overlapped returns or autocorrelation-adjusted volatility.
- No forced DSR verdict when observations are insufficient.
- No estimator output may be replaced in place; a changed estimator version produces a new
  appended evidence event.
- No diagnostic module can produce `TRADE_READY`.
- No shadow module can place orders.
- No shadow pass without uncertainty reporting.
- No Miner/Validator/Shadow code can import live execution clients.
- No private-key env vars in Miner/Validator/Shadow.
- No approved manifest can authorize trading without current ledger replay.
- No bot discovery, tuning, or promotion.
- No bot fail-open on corrupt, missing, truncated, or ambiguous ledger.
- No frozen grid may silently re-test locked-rejected gates without human-authored, hash-covered
  structural-change rationale.
