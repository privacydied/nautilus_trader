# Polymarket Complement Arb V1 — Precommitment

## Hypothesis

Same-condition binary YES+NO pairs on Polymarket Global occasionally become available below $1 net of costs, and a maker-default hybrid strategy can harvest them without accumulating unmanaged directional exposure.

## Scope

- **Polymarket Global only.**
- **Same condition_id only.**
- **Exactly two outcomes only.**
- **YES + NO complementary pairs only.**
- No negRisk multi-outcome sets.
- No same-question cross-market matching.
- No BTC Chainlink lag.
- No directional prediction.
- No ML.
- No midpoint edge.
- No maker-rebate-dependent acceptance gate.

## Primary Acceptance Gates

1. Observe mode finds same-condition YES+NO opportunities where executable top-of-book edge remains positive after fees, depth/spread, stale-data filters, signing latency buffer, gas/redeem estimate, resolution danger window, and leg-risk buffer.
2. Shadow validation records pessimistic paired fills with positive `paired_fill_realized_net_edge_per_share`.
3. Pessimistic paired fills remain positive after timeout/unwind losses using the precommitted arithmetic below.
4. Results repeat across multiple observer windows and meet the split data sufficiency gates.
5. Backtest/replay validates detector/edge/sizing only.
6. Backtest/replay is not maker-fill proof.
7. One-leg exposure is bounded in the model and timeout/unwind losses are counted against paired gains.
8. Positive results must come from public live/shadow event evidence, not midpoint marks, optimistic-only fills, or replay-only assumptions.
9. Live mode remains guarded, stubbed, and disabled.

## Shadow Validation Definitions

The key metric is `paired_fill_realized_net_edge_per_share`, not whether theoretical complement edges appeared.

Executable complement edge requires all of the following:

- Same `condition_id` binary YES+NO pair only.
- No negRisk, no cross-market matching, no same-question matching, no directional model, no Chainlink/Binance fair value, no ML.
- Edge computed from executable top-of-book prices only; midpoint pricing is invalid.
- YES ask + NO ask + Polymarket fees + leg-risk/signing/redeem buffers remains below $1.00.
- Books are fresh, outside the resolution danger window, and have sufficient depth.
- Max safe size is non-dust and constrained by weaker leg depth, max order USDC, and exposure caps.

Pessimistic queue-fill rule:

- A resting BUY quote fills only after observed sell-side execution or compatible book/trade evidence reaches the bid.
- Cumulative observable traded volume at-or-through the quote price must strictly exceed visible depth resting ahead at quote time.
- Full fill requires cumulative volume to exceed `depth_ahead + our_quote_size`.
- Partial fill requires cumulative volume to exceed `depth_ahead` but not `depth_ahead + our_quote_size`.
- A resting SELL quote mirrors this logic on the ask side.
- Ambiguous trade direction, ambiguous size, ambiguous event order, or missing book/trade evidence defaults to no fill.

Joint two-leg edge-survival window:

- The pair is one joint event: YES becomes fillable at `t1`, NO becomes fillable at `t2`.
- A paired fill counts only if both legs fill within `one_leg_timeout_ms`, both fills belong to the same condition, the edge remains positive through the joint window, realized net edge after fees/buffers is positive, size is non-dust, and the pessimistic model passes.
- The shadow layer must record `first_leg_fill_ts`, `second_leg_fill_ts`, `joint_fill_latency_ms`, `edge_at_detection`, `edge_at_first_fill`, `edge_at_second_fill`, `min_edge_during_joint_window`, and `edge_survived_until_second_leg`.
- Both legs eventually becoming fillable is insufficient if they are not fillable close enough together for the edge to survive.

Timeout/unwind arithmetic:

```text
paired_gain = paired_fill_count * avg_paired_realized_net_edge_per_share * avg_paired_size
unwind_loss = one_leg_fill_count * avg_one_leg_unwind_loss_per_share * avg_one_leg_size
net_shadow_harvest = paired_gain - unwind_loss
expected_edge_per_detected_opportunity = net_shadow_harvest / detected_opportunity_count
```

Reject once sufficient data exists if `unwind_loss >= paired_gain`, `net_shadow_harvest <= 0`, or `expected_edge_per_detected_opportunity <= 0`.

## Data Sufficiency Gates

These are separate config values, not a single arbitrary count:

- `min_observer_windows = 5`
- `min_detected_opportunities = 50`
- `min_pessimistic_paired_fills = 20`
- `min_same_condition_valid_opportunities = 30`
- `min_non_dust_opportunities = 30`

If any sufficiency gate is unmet, verdict is `NEEDS_MORE_DATA`; insufficient data must not reject or promote the strategy.

## Rejection Gates

- No net opportunities after costs.
- Opportunities exist only at midpoint, not executable/quoteable prices.
- Pessimistic paired fill count is zero once sufficient data exists.
- Median pessimistic realized shadow net edge per paired fill is non-positive.
- Pessimistic paired fills are positive only before unwind losses.
- One-leg timeout/unwind losses erase paired-fill gains.
- Only optimistic or neutral fill modes pass.
- Edge does not survive the joint two-leg window.
- Opportunities exist only at dust size.
- One-leg passive fill estimates dominate paired opportunities.
- Fill rate too low to overcome cancels/unwinds.
- Forced unwinds dominate completed pairs.
- Closed/resolved residual events are non-trivial.
- Edge depends on maker rebates being assumed.
- Python signing latency makes taker-close uneconomic.
- Stale data or cancel/fill races make one-leg exposure unacceptable.
- Required adapter data is insufficient to safely quote.
- Live data client lacks required depth and V1 cannot safely degrade.
- Adapter cannot distinguish closure/finality and markets near resolution cannot be safely excluded.

## Verdicts

Allowed verdicts only:

- `NEEDS_MORE_DATA`
- `REJECTED_FOR_CURRENT_LIVE_CONDITIONS`
- `CANDIDATE_FOR_LONGER_OBSERVATION`

Do not add `CANDIDATE_FOR_LIVE`, `TRADE_READY`, or `EXECUTION_READY`.

`CANDIDATE_FOR_LONGER_OBSERVATION` requires pessimistic paired fills with positive realized shadow net edge, edge survival through the joint window, positive unwind-adjusted expected edge, non-dust opportunities, repetition across multiple windows, neutral/optimistic diagnostics that do not contradict pessimistic results, and no weakened safety guard.

Theoretical edge is not tradeable edge. A detected edge is not a candidate. A backtest edge is not a candidate. A replay edge is not a candidate. An optimistic-fill-only edge is not a candidate.

MCPT/null tests are allowed only after positive after-cost event evidence exists. Null tests are falsification tools, not proof of tradeability.

Live execution remains impossible in this phase. `run_live_guarded.py` stays guarded/stubbed and no signing, private-key handling, execution-client import, or order submission belongs in the shadow harness.

## No P-Hacking

Do not tune thresholds after first empirical run without recording a new precommitment version.

## Version Record

- V1: 2026-05-16 — Initial precommitment.

## Key Design Decisions

- **Adapter**: Python `nautilus_trader.adapters.polymarket` (no Rust adapter in this checkout).
- **Depth mode**: `top_of_book_only` in V1 observe runner (REST /book endpoint).
- **Passive fill estimate source**: `book_movement_only` in V1 (no live trade tick subscription in initial observe).
- **Final resolution detection**: `unavailable` in V1 observe (not subscribed to closure/resolution events).
- **Maker rebates**: Excluded from conservative acceptance gate. Recorded for reporting only.
- **Signing latency**: Python py_clob_client_v2 ~1s per order accounted in `signing_latency_buffer_per_share`.
- **Backtest fidelity**: Trade-history replay cannot validate maker fill rate.
