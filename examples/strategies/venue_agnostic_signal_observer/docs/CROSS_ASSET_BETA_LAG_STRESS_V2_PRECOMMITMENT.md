# Cross-Asset Beta-Lag Stress V2 Precommitment

Signal family: `cross_asset_beta_lag_stress_v2`

Created before empirical stress capture for this signal family.

## Hypothesis

When BTC or ETH has a sudden source move of at least 30 bps inside 30 seconds, slower high-beta spot altcoins may reprice 1 to 5 minutes later.

Stress does not bypass costs. Stress is only the regime where this hypothesis can be honestly tested. Quiet-window evaluation is not a valid rejection or acceptance test for this specific hypothesis.

## Primary trigger

The primary trigger is:

- source asset is BTC or ETH
- absolute source move is >= 30 bps
- measured over a rolling 30-second window
- public market data only
- AND crypto-native stress confirmation is active

A 30 bps / 30 second move alone is insufficient when the broader crypto regime is quiet. The trigger is an AND gate, not OR.

No VIX or non-crypto stress proxy is allowed.

## Stress confirmation

Stress confirmation is crypto-native only.

Primary confirmation source for the watcher is the existing volatility gate state where available:

- `MARKET_ACTIVE`
- `ACCELERATING`

If future code adds short-window realized BTC/ETH volatility confirmation, it must be documented as a protocol extension before empirical capture and must remain crypto-native.

## Data-source path decision

Path B is chosen for this initial watcher wiring.

The existing scaffold has reusable public tick capture/evaluation infrastructure (`run_tick_capture.py`, `run_derivatives_spot_capture.py`, `cross_asset_impulse.py`, and `run_cross_asset_impulse.py`), but the existing stage2 gate watcher itself only polls the hourly/1-minute Kraken volatility gate and does not expose a live rolling 30-second BTC/ETH trigger feed inside the watcher loop.

Therefore this work adds the smallest trigger-evaluation plumbing to `stage2_gate_watcher.py`: a pure function that evaluates a supplied tick/sub-minute BTC/ETH series for a rolling 30-second impulse. It does not reimplement capture/evaluation transport. Actual capture continues to use the existing public-data capture runner; evaluation continues to use the existing cross-asset impulse/falsification stack.

## Universe

Sources:

- BTC
- ETH

Targets:

- SOL
- LINK
- DOGE
- AVAX
- ADA

If an existing evaluator omits ADA in a historical v1 config, that is a compatibility constraint to document in the report, not permission to tune the v2 target set after seeing results.

## Horizons

Primary scientific horizons:

- 60 seconds
- 120 seconds
- 300 seconds

Existing evaluators may retain additional horizons for diagnostics, but the hypothesis is 1 to 5 minutes.

## Cost and fill assumptions

Cost model:

- Use the existing pessimistic 50 bps all-in wall unless the existing cross-asset evaluator already has a stricter named model.

Fill assumption:

- Pessimistic observer-only forward-return proxy.
- No optimistic maker fills.
- No order placement.
- No execution client.
- No wallet.
- No private-key requirement.

## Capture duration

Minimum capture duration after trigger: 600 seconds floor.

Longer captures are preferred when practical because 300-second horizons need forward-return room after later signals. The default service capture duration remains 900 seconds unless explicitly changed before empirical capture.

## Cooldown and deduplication

One capture per stress event.

Default cooldown: 3600 seconds unless the existing `CaptureGuard`/watcher state defines a stricter effective guard.

Cooldown prevents duplicate captures from one stress event. Failed/quarantined captures do not become evidence of edge.

## Recurrence requirement

A single stress capture is not proof.

At most, one stress capture can justify `CANDIDATE_FOR_LONGER_OBSERVATION`. It can never justify live trading or trade readiness.

Multiple stress windows with consistent post-cost behavior are required for stronger conclusions.

## Threshold lock

Thresholds are precommitted before the first empirical capture.

Do not tune thresholds after seeing empirical results.

The 30 bps / 30 second source impulse is the only primary trigger threshold in this precommitment.

## 150 bps rule

150 bps is metadata only in this v2 precommitment unless a separate precommitment explicitly promotes it.

If recorded, it must be recorded as descriptive metadata/covariate, for example:

- `contains_150bps_impulse: true`

It is not a separate experiment, primary trigger, acceptance gate, or verdict criterion.

## Verdict authority

The systemd service does not decide candidate/rejected verdicts.

The service may trigger captures and record status only. Post-capture diagnostics decide research status:

- evaluation
- cost sensitivity
- permutation/null tests
- lead/lag heatmaps
- candidate falsification summaries
- cross-capture consistency once multiple captures exist

Diagnostics do not imply live-trading readiness.
