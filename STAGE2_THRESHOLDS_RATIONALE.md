# Stage 2 Thresholds Rationale — Cross-Asset Beta Lag V1

This document explains why each committed value was chosen *before* seeing any
Stage 2 evaluator data.  These are **pre-data operational thresholds** designed
to make the test auditable, not theoretically optimal values fitted to a sample.

## BH q = 0.10 (Primary FDR)

This is a screening / discovery-control problem, not a capital-deployment
decision.  A q-value of 0.10 means we accept that roughly 10 % of rejected null
hypotheses may be false positives during discovery.  That is standard for
early-stage signal exploration where the cost of a false discovery is a wasted
evaluation cycle (not lost capital).  A tighter q (e.g. 0.05) would increase
the false-negative rate and may discard marginal but reproducible signals too
early.

## BY q = 0.10 (Sensitivity FDR)

Benjamini–Yekutieli controls the FDR under arbitrary dependence structures,
which makes it more conservative than BH.  We keep q = 0.10 so that the
*effective* stringency is higher (fewer discoveries), providing a sensitivity
check: if a config survives BH but is killed by BY, the signal is not robust
under the dependence-agnostic model and should be treated with extra caution
during discovery.  The BY step exists only for diagnostics — it does not gate
final acceptance.

## Native Permutation as Primary p-value Source

The Stage 2 machinery must use **one** required p-value source consistently.
`native_permutation` is the primary source built into the evaluator pipeline.
If it is missing for any declared config, the FDR step fails with a structured
error rather than falling back to an alternate estimator (e.g. MCPT).  This
ensures that missing data is always surfaced as a hard failure, never silently
substituted.

## Temporal 70/30 Split

Forward-time generalisation matters more than random-window mixing.  Ordering
captures by `capture_start_utc` and assigning the earliest 70 % to discovery
and the latest 30 % to holdout tests whether signals found in past data persist
in unseen future data — a stronger test than a shuffled split.  The 70/30 ratio
provides enough mass for discovery while keeping a meaningful holdout block.

## Minimum 10 Validated Captures

Smaller corpora make holdout claims weak.  With fewer than 10 validated
full-active captures, the temporal split would yield fewer than 3 test captures,
which is insufficient to draw any conclusion about forward-time generalisation.
Ten is an operational minimum that produces at least 3 test captures (30 % of 10)
and at least 7 discovery captures — enough for a meaningful same-sign consistency
check.

## +2 bps Aggregate Mean Net Threshold

A threshold of "positive" ( > 0 bps) is too close to noise — on any given run
the mean may be slightly positive by chance.  +2 bps per event requires a
non-trivial observable edge that survives aggregation across multiple
validation-status events.  This is a modest bar (most institutional equity
strategies target > 1 bp per trade), but it ensures that the discovery process
does not waste holdout capacity on configs whose net edge is indistinguishable
from measurement noise.

## 70 % Same-Sign Capture Consistency

If a config truly has directional edge, more than half of its individual
captures should show the same sign.  70 % represents a majority-plus-robustness
bar: it requires that at least 70 % of captures (rounded up via `ceil`) produce
a mean net return with the same sign as the aggregate.  For 7 discovery
captures, `ceil(0.70 * 7) = 5` captures must agree — a clear majority that is
not met by a single outlier.  With 7 discovery captures and threshold 0.70,
4 same-sign captures fail and 5 same-sign captures pass.

## −5 bps Worst-Capture Mean Floor

The aggregate mean can hide one ugly capture that is heavily negative.  A floor
of −5 bps per event means that no single capture's mean net return may be worse
than −5 bps.  This is a tail sanity guard: if any capture loses −6 bps per
event on average, the config fails even if the aggregate is positive, because
the loss path would be unacceptable in a real trading context.  The −5 bps
level is symmetric with the +2 threshold — it allows a single bad capture to
be 2.5× worse than the discovery bar, but not more.

## Minimum 50 Valid Events per Config

The current evaluator / runbook already uses a `--min-events 50` default.
Pinning this in the precommitment prevents future evaluator-default drift from
silently changing Stage 2 criteria.  If someone changes the evaluator's default
to 30 events, the Stage 2 criteria checker still enforces 50.  50 events per
config provides enough observations for a meaningful mean and standard error
without being so large that most configs are excluded for low liquidity periods.

## Cross-Asset Beta Lag: Test Family Dimensions

The test-family dimensions differ from the derivatives-source-to-spot-target
precommitment because cross-asset beta lag tests a different mechanism:

- **source_asset** and **target_asset** replace `source_venue` / `target_venue` /
  `symbol` because cross-asset propagation depends on asset identity, not venue.
  A BTC impulse propagates to SOL regardless of whether both trade on the same
  exchange.
- **signal_type**, **lookback_ms**, **horizon_ms** remain as dimensions because
  beta-lag may depend on how quickly the source impulse is measured and how
  far forward the alt repricing takes to materialise.
- **venue** is not a dimension because the existing cross_asset_impulse
  evaluator pairs source ticks (from capture streams) with target ticks (from
  the same capture directory) without distinguishing venue pairs — it aggregates
  all available source/target streams.

This change is intentional and documented.  The FDR script will fail if the
dimensions in `stage2_precommitment.json` do not match the incoming p-value
table.

## Narrow Config Grid (signed_imbalance, 30s, 5min)

The May 13 diagnostic identified `signed_imbalance` with a 30-second lookback
and 5-minute horizon as the most promising combination for cross-asset beta
lag.  Committing a single config avoids garden-of-forking-paths inflation of
the test family.  Additional signal types, lookbacks, or horizons require a new
precommitment before collection.

## Event Gate: BTC 150+ bps, Acceleration, FULL_ACTIVE Only

Cross-asset beta lag is a stress-contingent phenomenon.  In quiet markets, BTC
and alt prices move together in a slow drift where lead-lag is unmeasurable.
Committing a 150 bps BTC 1h move with confirmed acceleration before seeing any
corpus data ensures that the corpus contains only genuine stress events.  This
prevents the quiet-window diagnostic data from inflating the false-negative
rate during discovery.

## Quiet-Window Diagnostics Excluded from Corpus

Quiet-window captures are useful for plumbing verification but must not enter
the Stage 2 corpus.  If BTC stress < 150 bps or acceleration is not confirmed,
the run reports `INSUFFICIENT_STRESS_FOR_HYPOTHESIS_TEST`.  No `REJECTED`
verdict is produced from quiet diagnostics because the hypothesis is not being
tested — only the infrastructure is being verified.

## Thresholds Intentionally Not Committed in V1

The following thresholds are deliberately absent from the V1 precommitment:

- **No regime-conditioning threshold yet.**  The volatility gate already
  filters out quiet markets at capture time.  Adding a second regime filter at
  the evaluation stage would create an untested interaction.  Deferred to V2.

- **No minimum capture-duration threshold beyond validated FULL_ACTIVE
  admission.**  A FULL_ACTIVE capture is expected to run near its full
  configured duration; if captures systematically truncate early, a minimum
  duration bar may be needed.  Deferred until early Stage 2 captures reveal
  typical duration distributions.

- **No extra lookback / horizon families beyond the declared single config.**
  Adding families would change the test-family dimensions and invalidate the
  FDR correction.  Deferred to V2 after V1 discovery characterises whether the
  30s/5min config survives BH.

- **No execution / fill-quality threshold.**  This is an observer-only system.
  Fill-quality metrics from live trading are not available.  A threshold that
  cannot be measured would be non-operational.

- **No venue-distinction within source/target assets.**  Cross-asset beta lag
  is an asset-level phenomenon.  Venue-level granularity would multiply the
  test family 6× (2 source venues × 3 target venues) without a theoretical
  justification for venue-specific beta lags at multi-second horizons.

These omissions are **deliberate deferrals, not oversights**.  Each will be
revisited in V2 after the V1 pipeline has produced its first discovery results.

These numbers are pre-data operational thresholds chosen to make the test
auditable, not fitted to a sample.

---

*Created before any Stage 2 corpus-admissible Full Active capture was attempted.
Numbers are pre-data choices for auditability, not fitted to a sample.*
