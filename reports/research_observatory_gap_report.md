# Research Observatory Gap Report V1

> Generated as part of the Research Observatory Reliability V1 implementation.
> Observer-only. No auth. No orders. No execution. No trading or investing recommendations.
>
> **This report documents both pre-V1 gaps addressed by the Reliability V1
> implementation and gaps intentionally deferred to later methodology or
> diagnostic phases.**  Sections 4 (run IDs, atomic writes), 5 (capture
> validation), 6 (gate caveat), and 15 (quarantine) describe V1 solutions.
> Sections 7-14 and the deferred summary describe gaps that remain open.
>
> **Version**: V1 &mdash; **Generated**: 2026-05-15 &mdash; **Git SHA**: see `reports/research_run_index.jsonl` or `git log` at the commit this report accompanies.

## 1. Current Observatory Gaps

The falsification pipeline (MCPT, permutation tests, cost-sensitivity, heatmap)
is operationally solid, but it produces output for which we cannot certify
data provenance.  The table below distinguishes pre-V1 gaps that V1 addresses
from gaps that remain deferred.

V1 hardens the *negative* case: rejections are now reproducible, provenanced,
and validated.  It does not yet make the *positive* case credible: a candidate
that survives V1's reliability layer still depends on deferred methodology
(FDR correction, OOS/holdout, tail metrics) before it would support a
survival claim.

| Category | Pre-V1 gap / remaining gap | V1 status | Impact |
|---|---|---|---|
| Capture infrastructure | No repeated campaign runner; no run-ID namespacing | Addressed | Previously: runs could silently overwrite each other |
| Artifact discipline | No partial-vs-final manifest; plain `f.write()` can leave half-written JSON | Addressed | Previously: interrupted captures produced unparseable artifacts |
| Validation | No cross-check of manifest counts against raw JSONL | Addressed | Previously: corpus aggregators trusted manifest tick counts blindly |
| Methodology | No multiple-testing correction; no OOS/cross-capture freeze discipline | Deferred | Survivorship claims lack statistical credibility |
| Diagnostics | Latency/regime/OI/funding/L2 context not enforced or integrated | Partially addressed | Signal degradation from drift/regime change goes undetected |

The sections below explain each gap in detail.

---

## 2. Data Coverage Is the Top Blocker

Before we can credibly reject a candidate or promote it for longer
observation, we must
demonstrate that the observed effect generalises:

- **Across time windows.**  One good capture window does not prove persistence.
- **Across market regimes.**  Low-vol and high-vol regimes produce different
  cross-venue dynamics.
- **Across source-target venue pairs.**  A derivatives-source to Kraken
  spot target can behave differently from the same source to Coinbase spot.

A campaign runner (subprocess-based, isolated per capture) is the minimum
infrastructure to produce multi-window corpora.  Anything less invites time-
window overfitting.

---

## 3. Why Campaign Capture Precedes Execution

Execution introduces fill risk, slippage, latency, and adversarial selection.
Before any results are used to justify further investment in a signal,
we must first demonstrate that the *observable* edge
(repeated capture corpus after costs) is:

1. **Reproducible** — appears in more than one capture window.
2. **Consistent** — ranked by capture count, not peak performance.
3. **Statistically defensible** — subject to MCPT and OOS discipline.

Automatic campaign capture is the prerequisite to any of the above.
Execution-only infrastructure without capture validation is research theatre.

---

## 4. Run-ID Output Directories and Atomic Writes

### Problem
The previous capture used `int(time.time())` as a run ID and wrote the
manifest with `open(path, "w")`.  Two captures started in the same second
could collide.  Partial writes during SIGKILL would leave unparseable JSON.

### Solution (V1)
- `create_run_id()` uses UTC timestamps with microseconds plus a 6-hex random
  suffix — unique even under rapid subprocess invocation.
- `safe_output_dir()` refuses to overwrite non-empty dirs unless
  `allow_existing=True` is explicitly set.
- `atomic_write_json()` writes to a temp file in the same directory, then
  `os.replace()` atomically.  No half-written files on crash.
- During active capture, partial state is written to
  `capture_manifest.partial.json`.  The canonical `capture_manifest.json` is
  only finalised on clean exit or controlled interruption.

---

## 5. Capture Validation

### Why It's Required Before Trusting Any Corpus
A corpus aggregation is only as reliable as the least-verified capture it
includes.  Manifest tick counts can diverge from raw JSONL lines for
several reasons:

- **Parser drops.**  A WebSocket parser silently dropping lines produces
  fewer lines than the manifest claims.
- **File truncation.**  A full filesystem leaves a partial JSONL.
- **Overlap miscalculation.**  If the manifest's overlap computation uses
  stream-ended timestamps that differ from actual first/last row timestamps.

The `validate_capture.py` tool cross-checks manifest counts against raw
data *without modifying the capture files*.  V1 introduces the validator
so that future corpus aggregation can require validation before inclusion;
its verdict should be treated as the entry condition for trusting a
capture in any corpus-level analysis.

---

## 6. Volatility-Gated Capture Bias

Volatility-gated captures (relying on the Kraken hourly OHLC gate) have a
structural limitation:

> **The gate uses recent-past information, not a forward-looking oracle.**
> A capture triggered by a volatility spike may catch the *tail* of the move
> rather than its core — or may fire into the beginning of a regime shift
> that reverses before the capture ends.

This means:

- **Gated captures are biased toward volatile periods.**  The corpus will
  over-represent high-vol regimes and under-represent quiet regimes.
- **TAIL BIAS**: A gate triggered on a 75bps 3h range + acceleration may
  catch the last 30 minutes of a move that exhausts itself during the
  capture window.
- **MISSED OPPORTUNITY**: A consolidation that resolves violently in the
  first minute of a capture window is captured; one that resolves in the
  last minute is missed until the next window.

**Mitigation**: Record the gate snapshot at capture time.  During evaluation,
label each capture with its gate context so corpus-level results can be
conditioned on volatility regime.  The campaign runner also implements a
`--max-skip-streak` guard: if consecutive captures are skipped for low
volatility, the campaign aborts rather than spinning indefinitely in calm
markets.

---

## 7. Multiple-Testing Correction Gap

The current pipeline evaluates many (`source_venue`, `target_venue`,
`signal_type`, `lookback_ms`, `horizon_ms`) combinations per capture.
With the runbook defaults of 3 signal types × 4 lookbacks × 7 horizons ×
~3 symbols × 2 target venues, approximately 500 configurations are tested
per window.  More aggressive sweeps can push this into the thousands.
Across N capture windows, the total number of tests scales multiplicatively.

**We do not currently apply any multiple-testing correction.**

This is a deliberate deferral:

- **No specific FDR method is chosen in V1.**
  Choosing Bonferroni, Benjamini-Hochberg, BY, or a Bayesian alternative
  requires a methodology decision that should consider:
  - Dependence structure between configurations (nested lookbacks).
  - Expected proportion of true effects (likely very low).
  - Tolerance for false discoveries (depends on use case).

- **Deferred**: The correction method choice is explicitly deferred to a
  later research-methodology decision.  V1 does not implement Bonferroni,
  BH, or any holdout / walk-forward / cross-capture split mechanic.

**What V1 does instead**: The corpus aggregation ranks by *num_captures*
(consistency), not by peak performance.  This is weaker than statistical
correction but is a necessary first step.

---

## 8. Out-of-Sample / Cross-Capture Frozen-Config Gap

The current pipeline allows the researcher to cherry-pick the best
configuration *after* seeing all results.  Without OOS discipline (the
property of testing on unseen data) and its mechanism (frozen-config /
holdout):

- Lookback/horizon pairs can be overfit to a specific capture window.
- The "best" configuration may be the one that happened to line up with
  a transient market microstructure artefact.

**Deferred in V1**: A frozen-config / holdout mechanism where the
corpus is split into a discovery-set and a test-set, and the final
evaluation only runs on the held-out captures.  V1 only provides the
infrastructure to *produce* multi-capture corpora.

The choice of holdout dimension is itself a methodology decision with
different statistical properties:

- **Random capture holdout** (split corpus 70/30 by run_id) tests
  average generalisability across any capture window.
- **Temporal holdout** (first N captures discover, last M test) tests
  forward-predictive generalisability — harder, and closer to ongoing
  data-collection conditions.
- **Regime-stratified holdout** (high-vol captures discover, low-vol
  test, or vice versa) tests cross-regime robustness — different
  from both of the above.

This choice should be made jointly with the FDR method selection (see
section 7).  V1 does not choose one; it documents the options so the
next phase can make an informed decision.

---

## 9. MCPT Limitation

The Monte Carlo Permutation Test (MCPT) / native permutation-null tooling
asks whether the observed event-return relationship is stronger than a
randomized or shifted timing baseline.

**This is not a test of regime robustness, tradeability, or statistical
significance in the traditional sense.**  Specifically:

- **Null vs permutation**: MCPT compares against a permutation/shift null
  that randomises or shifts event timing.  This tests whether the signal
  contains information beyond random timing — it does NOT test whether the
  signal survives transaction costs, latency, or adversarial selection.

- **Not regime robustness**: MCPT does not condition on volatility regime,
  spread regime, or venue drift.  A signal that passes MCPT in a
  high-volatility window may fail completely in a low-volatility one.

- **Not proof of tradeability**: Passing MCPT suggests the observed
  event-return relationship is stronger than the chosen null baseline.
  It does not mean the signal can be captured
  by a trading system after spread, slippage, latency, and fill uncertainty.

**MCPT is a useful filter — it is not a green light for execution.**

---

## 10. Tail / Drawdown Metric Gap

All current evaluation metrics are point-estimate averages:

- Mean/median net bps.
- Win rate.
- Positive-after-cost fraction.

**Missing**: Any metric describing the *distribution tail* — worst 5% of
events, consecutive-loss runs, or maximum adverse excursion.  A positive
mean driven by a fat right tail is much less reproducible than one driven
by a tight distribution — the research-side consequence of ignoring tails
is that a candidate appearing viable from averages alone may evaporate on
re-examination.

**V1 does not add tail metrics.**  The gap is documented for a future
phase that adds quantile-based risk metrics.  The motivation here is
research reproducibility, not execution-risk management.

---

## 11. Baseline-Window and Capture Duration Weakness

Two distinct window parameters cause different problems:

**Short captures** (600s default).  Short capture windows mean:
- **Fewer events**, which inflates the variance of any mean estimate.
- **Regime sampling bias** — a 10-minute capture samples at most one
  intraday regime.
- **No overnight / session-transition sampling**.

The campaign runner defaults to 1800s (30 min) to mitigate this.
Short captures can still be useful for FAST_DIAGNOSTIC mode, but
should not be used for corpus-level conclusions.

**Baseline window too large relative to capture** (60 000 ms evaluator
default).  The evaluator's baseline window defines the lookback used to
classify each tick's local regime.  A 60-second baseline within a
10-minute capture leaves only a handful of independent baseline periods,
making the baseline comparison itself unstable.  This parameter should be
reviewed when capture duration defaults change.

---

## 12. Cost Calibration Split

### Observable Pre-Trade Cost Calibration (V1)
V1 can only perform observable/pre-trade cost estimation from public
data and assumed fee tiers:
- Venue fee schedules.
- Observed bid-ask spreads from tick data.
- Quote-mismatch buffer from cross-venue timestamp alignment.

These are static / observable estimates.  They are conservative but
not conservative enough to account for real fill conditions.

### Real-Fill Calibration Gap
Real-fill calibration (fill probability, partial fills, queue position)
cannot be estimated from public trade data alone.  It requires either:

- Paper trading with a simulated matching engine, or
- Live execution with fill feedback.

**This is explicitly deferred until the paper/live execution pipeline
exists.**  This report does not request paper trading.  Until then,
cost assumptions are documented caveats, not calibrated parameters.

---

## 13. Diagnostic Gaps

Several diagnostic dimensions are not yet fully integrated into the
derivatives-v2 evaluation pipeline and its corpus quality gate:

| Diagnostic | Status | Impact |
|---|---|---|
| **Latency diagnostics** | Existing but never executed on a volatile-window capture | Venue-relative timestamp drift can correlate to zero; missing from standard capture/corpus quality gate |
| **Venue timestamp drift** | Not computed | A drifting comparison is indistinguishable from a signal |
| **Realised-vol regime labels** | Not emitted | Same signal may be regime-dependent |
| **OI/funding context** | OI capture optional, funding absent | Structural shifts without position context |
| **L2 book context** | Not integrated into derivatives-v2 evaluator | Spread/book-state conditioning is unavailable for this signal family |
| **DEX / CEX dislocation** | Separate tool exists but not integrated | May explain apparent CEX-only signals |

**V1 does not add these.**  They are listed to prevent the observatory
from claiming completeness where it does broader diagnostics.

---

## 14. Discovery Gap

The derivatives-v2 pipeline currently uses simple trade-flow impulse
features: notional burst, large trade, and signed imbalance.
The wider observatory also contains other research families (tick
lead-lag, cross-asset impulse, DEX/CEX dislocation, L2 maker-paper)
but those are separate studies or scaffolds that do not feed the
derivatives-v2 evaluator.

**Next discovery features for derivatives-v2 should be conditioning
variables first** — spread regime, volatility regime, time-of-day,
OI-change regime, funding sentiment, IV/RV — before adding exotic
signal families.  A signal that works only in
 tight-spread + high-vol conditions is very different from one that
 works in all conditions, and we currently don't condition on either.

---

## 15. Quarantine Gap

When a capture is known to be bad (corrupted JSONL, partial filesystem,
timing failure), the current options are:

1. **Delete the evidence** — destroys forensic traceability.
2. **Leave it in the corpus** — contaminates aggregation.

**The quarantine mechanism (V1)** adds a third path:
Mark the run as quarantined (append to `research_run_quarantine.jsonl`)
without deleting the original artifacts.  Corpus aggregation skips
quarantined runs by default, but the underlying data remains available
for forensic inspection.

The quarantine file is append-only JSONL.  No delete command exists in
V1.  No automatic removal of old artifacts.  The methodology report
must explain that quarantine exists to preserve evidence without
contaminating aggregation.

---

## 16. Recommended V1 Workflow

The V1 deliverables are designed to be used in a specific sequence.
This is the canonical operational flow:

1. **Run ID & output dir** — Call `create_run_id()` before any capture.
   Use `safe_output_dir()` with the generated ID to ensure unique,
   non-overwriting output directories.  Never reuse `int(time.time())`
   or any short timestamp truncated to seconds.

2. **Capture** — Use the campaign runner (`run_derivatives_capture_campaign`)
   for repeated captures, or `run_derivatives_spot_capture` directly for
   single-shot captures.  The capture script now writes to a run-ID-named
   directory, writes tick data to stream files, records progress via a
   partial manifest during active capture (canonical after finalisation),
   and appends a run-index row.

3. **Validate** — Run `validate_capture` on every capture directory before
   using its data.  The validator cross-checks manifest counts against
   raw JSONL, detecting tick-count mismatches, malformed lines, and
   overlap discrepancies.  Do not include a capture in any corpus
   aggregation unless it passes validation (or has explicit non-fatal
   warnings recorded).

4. **Quarantine if bad** — If validation fails, append the run ID to
   `research_run_quarantine.jsonl`.  Do not delete the
   capture files.  The quarantine file preserves forensic evidence while
   preventing the run from contaminating future aggregates.

5. **Run the evaluator** — Run the derivatives-spot lead-lag evaluator
   on the validated capture.  This produces `summary.json` containing
   `results_by_group` with per-configuration net-of-cost returns, and
   is the input for all post-analysis steps.

6. **Run post-analysis** — Over the evaluator's output, run cost
   sensitivity, permutation/null test, lead/lag heatmap, and
   candidate falsification in that order.

7. **Aggregate with quarantine awareness** — Use `run_report_corpus`
   with `--glob` and `--quarantine-file` to discover and filter report
   directories automatically.  The corpus aggregator skips quarantined
   runs by default, respecting `--include-quarantined` when needed.

8. **Reject or promote for observation** — A candidate that survives
   V1's reliability layer (provenanced capture, validated, passes
   evaluation filters across multiple windows) is a candidate for
   *longer observation*, not for execution.  See section 1's asymmetry
   note: V1 hardens rejection, not survival.

---

## Summary of Deferred Items

| Item | Deferred To | Reason |
|---|---|---|
| Specific FDR method | Research-methodology decision | Method choice depends on dependence structure |
| OOS/cross-capture split | V2 | Requires campaign corpus to exist first |
| Tail / drawdown metrics | V2 | Requires quantile infrastructure |
| Real-fill cost calibration | Paper/live execution pipeline | Cannot calibrate without fill feedback |
| Latency diagnostic enforcement | V2 | Existing but never executed on a volatile-window capture; V2 should make it part of the standard capture/corpus quality gate |
| Venue timestamp drift | V2 | Requires cross-venue clock sync analysis |
| Regime conditioning labels | V2 | Requires regime detection subsystem |
| Discovery conditioning variables | V2 | Requires data catalogue expansion |

### Deferred-item triggers

Deferred items in this report are revisited when at least one candidate
group survives V1-level falsification across three or more independent
capture windows with consistent positive net-of-cost return.  Until that
condition is met, claims about candidate survival are upper-bounded by
the V1 reliability layer's guarantees (provenanced data, capture validation,
quarantine discipline) and not by the methodology layer's deferred defences
(FDR correction, OOS holdout, tail metrics).

The precise trigger threshold may be refined as experience with the V1
pipeline accumulates.  The key property is that deferred items do not
become permanent — their status changes when the empirical evidence
justifies the methodology investment.

## Safety

**Public data observer only. No auth. No orders. No execution.**
**Research only. No trading or investing recommendations.**
