# Stage 2 Thresholds Rationale — Cross-Asset Event-Window Differential V1

This document explains why each committed value was chosen *before* seeing any
Stage 2 evaluator data.  These are **pre-data operational thresholds** designed
to make the test auditable, not theoretically optimal values fitted to a sample.

## Why Event-Window Differential Is a Sibling Test, Not a Fallback

The event-window differential hypothesis tests a fundamentally different
mechanism from cross-asset beta lag:

| Property | Beta Lag (V1) | Event-Window Differential (this) |
|----------|---------------|----------------------------------|
| Trigger  | Market stress (BTC 150+ bps) | Scheduled macro event |
| Design   | Absolute: does impulse propagate? | Comparative: is event propagation different from baseline? |
| Gate     | Volatility-based | Calendar-based |
| Null     | No propagation | Event and baseline propagation are indistinguishable |
| Windows  | Single continuous capture | Paired (event + offset baseline) |
| Stress   | Required for corpus admission | Not required (events happen regardless) |

The two tests are complementary, not redundant.  Beta lag asks: "during stress,
does BTC/ETH movement drive alt repricing?"  Event-window differential asks:
"around scheduled macro events, does impulse propagation differ from non-event
periods of equal length?"

If beta lag finds strong propagation during stress, event-window differential
may still find that scheduled events produce *faster* propagation even in
moderate market conditions.  If beta lag finds nothing, event-window
differential may still detect a differential pattern because scheduled events
concentrate information arrival in a known time window, reducing the search
space.

They are sibling hypotheses: each can succeed or fail independently.

## Why Scheduled Windows Reduce Post-Hoc Boundary Selection

Scheduled macro events have known timestamps (e.g. FOMC at 14:00 ET, NFP at
08:30 ET).  Defining capture windows as `[−5 min, +15 min]` before seeing any
data means the window boundaries are fixed by the calendar, not by observed
volatility.

This eliminates a large source of p-hacking: the researcher cannot widen the
window to include a spike that happened at +22 min, or narrow it to exclude a
quiet patch at +12 min.  The window is committed before collection.

The baseline window at `+90 min` is chosen to be far enough from the event
(well beyond the typical 30-minute volatility fade) that it represents a
non-event macro period of identical duration.  If the baseline fell at +20 min,
it would still be inside the event's volatility tail and the differential
contrast would be weak.

## Why Liquidation Cascade Is Deferred

A liquidation cascade detector would require a reliable public liquidation feed
with verified timestamps and venue coverage.  Currently:

- No validated public liquidation feed is integrated into the capture
  infrastructure.
- Existing tick capture covers spot and perpetual swaps (order book + trades)
  but not liquidation events.
- A naive "rapid price move = cascade" heuristic would conflate liquidations
  with ordinary volatility and produce a non-falsifiable signal.

Liquidation cascade analysis is deferred until a public liquidation feed
capture pipeline is verified independently.

## Why Baseline-Window Sizing Must Be Reviewed for Event-Window Captures

The precommitment defines explicit, non-inherited baseline parameters:

```
baseline_window_offset_minutes_from_event: 90
baseline_window_duration_seconds: 1200
```

This is a deliberate departure from the older 60-second default baseline that
was used in earlier OHLCV studies.  The 60-second default was designed for
multi-hour bar-level captures where 60s was a small fraction of the total
window.  For event-window captures (20-minute paired windows), a 60-second
baseline would be:

1. **Too short** — the baseline would have 1/20th the observations of the
   event window, making the variance comparison unreliable.
2. **Unmatched duration** — a differential test requires equal-duration windows
   for fair comparison.
3. **Prone to intra-baseline drift** — at 60s, a single burst of orders can
   dominate the baseline statistic.

The 1200-second baseline matches the event window exactly and provides enough
tick volume for stable per-config stats.

These numbers are pre-data operational thresholds chosen to make the test
auditable, not fitted to a sample.

## BH q = 0.10 (Primary FDR)

Standard for screening / discovery-control.  A q-value of 0.10 means we accept
that roughly 10 % of rejected null hypotheses may be false positives during
discovery.  Tightening to 0.05 would increase the false-negative rate and may
discard marginal but reproducible signals too early.  This matches the
cross-asset beta lag precommitment.

## BY q = 0.10 (Sensitivity FDR)

Benjamini–Yekutieli controls the FDR under arbitrary dependence structures
(important for event-window captures where event-window observations are
not independent of baseline-window observations from the same macro event).
It is deliberately more conservative.  Same value as beta lag.

## Temporal 70/30 Split

Same rationale as beta lag: forward-time generalisation is the stronger test.
Event-window captures have an additional reason: economic regimes shift over
time.  A config that works in the first 7 events may fail in the last 3 if
the macro regime has changed.

## Minimum 10 Validated Paired Captures

Ten paired captures (each with event + baseline = 20 windows total) provides
enough mass for meaningful discovery (7 events, ~14 windows) and a
non-trivial holdout (3 events, ~6 windows).  Fewer than 10 would make the
temporal split worthless (fewer than 3 test events).

## +2 bps Aggregate Mean Net Threshold

Same +2 bps as beta lag.  This is the economic bar: a signal that cannot
produce +2 bps per event after all costs is indistinguishable from noise.
The cost model is identical (50 bps all-in), so the same bar applies.

## 70 % Same-Sign Capture Consistency

Same rationale as beta lag.  For 7 discovery event captures (the minimum after
10 paired captures split 70/30), `ceil(0.70 * 7) = 5` must agree in sign.

## −5 bps Worst-Capture Mean Floor

Same tail sanity guard as beta lag.  No single capture may average worse than
−5 bps per event.  This prevents a single disastrous capture from being hidden
by the aggregate.

## Minimum 50 Valid Events per Config

Same pinning as beta lag.  The 60-second baseline error is explicitly avoided:
the baseline window duration of 1200 seconds is more than sufficient to produce
50 valid events even for moderate-liquidity assets like DOGE or AVAX.

## Window Type as a Test Family Dimension

The addition of `window_type` (values: `event`, `baseline`) to the test family
dimensions is the structural distinction from `cross_asset_beta_lag_v1`.  This
allows the FDR correction to treat event-window and baseline-window observations
as distinct entries even when all other dimensions (source, target, signal_type,
lookback, horizon) are identical.

If `window_type` were omitted, a mean return of +3 bps from event windows and
+1 bps from baseline windows would be averaged together (+2 bps) and might pass
the economic bar even though the differential is where the actual signal lives.
Explicitly separating the two types preserves the differential structure for
analysis.

---

*Created before any event-window corpus-admissible capture was attempted.
Numbers are pre-data choices for auditability, not fitted to a sample.*
