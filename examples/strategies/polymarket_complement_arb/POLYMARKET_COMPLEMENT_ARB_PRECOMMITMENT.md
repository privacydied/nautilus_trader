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

1. Observe mode finds opportunities where net edge remains positive after fees, depth/spread, stale-data filters, signing latency buffer, gas/redeem estimate, resolution danger window, and leg-risk buffer.
2. Observe mode produces passive fill estimates showing quoteable opportunities are not purely theoretical.
3. Backtest/replay validates detector/edge/sizing only.
4. Backtest/replay is not maker-fill proof.
5. One-leg exposure is bounded and unwound, closed, or settled by explicit rule.
6. Positive results must come from real live fills or clearly labelled passive fill estimates, not midpoint marks.
7. Live mode remains guarded and disabled by default.

## Rejection Gates

- No net opportunities after costs.
- Opportunities exist only at midpoint, not executable/quoteable prices.
- Passive fill estimates show quotes are rarely touched while edge remains valid.
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
