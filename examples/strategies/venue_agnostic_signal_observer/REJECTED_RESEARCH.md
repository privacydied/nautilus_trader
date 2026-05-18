# Rejected Research Registry

## Purpose

This file records research hypotheses that were tested, evaluated, and
rejected. A rejection means the hypothesis was tested under specific
conditions (venue, signal design, cost model, date windows) and did not
produce evidence of a tradable edge under those conditions.

Adjacent hypotheses that were NOT tested are listed alongside each rejection,
not as open promises but as precise boundaries of what was not evaluated.

## Status Table

| Study | Signal Family | Venue | Verdict | Key Result | Tag |
|---|---|---|---|---|---|
| Derivatives v2 Binance Perp-to-Spot | Perp-spot basis beta lag | Binance (perp + spot) | `REJECTED` | Cost-wall blocked: perp-spot basis change within 5-minute horizon was structurally smaller than perp funding + spot execution cost | `derivatives-v2-binance-perp-to-spot-rejected` |
| **Family 1 same-venue USD/USDT quote-basis reversion** | Same-venue quote-basis reversion (BTC/USD vs BTC/USDT) | Kraken spot | `REJECTED` | Observed basis change over 30-120s lookbacks was ~5-30 bps peak; round-trip two-leg cost ~32 bps. Signal is structurally smaller than transaction cost. Cost-wall blocked. Diagnostic confirmed the blocker is signal-vs-cost, not window length or data sparsity. | `family1-kraken-usd-usdt-reversion-cost-wall-rejected` |

## Rejection Details

### Family 1 — Same-Venue USD/USDT Quote-Basis Reversion

**Tag:** `family1-kraken-usd-usdt-reversion-cost-wall-rejected`

**Verdict:** `REJECTED`

**Hypothesis:** When Kraken BTC/USD and BTC/USDT quote-basis diverges, fading
the divergence (buying the cheap leg, selling the expensive leg) produces a
positive expected forward return over 1-5 minute horizons.

**Tested conditions:**

- Venue: Kraken spot
- Instrument pair: XXBTZUSD vs XBTUSDT
- Signal: Reversion framing — `basis_change_bps < 0 => long, > 0 => short`
- Lookback windows: 30s, 60s, 120s
- Forward horizons: 60s, 300s
- Cost model: ~16 bps per leg, ~32 bps round trip (retail taker)
- Date windows: 7-date calendar-spread (Feb-Aug 2025, Mon-Sun), 13:00-16:00 UTC
- Data: Public Kraken trade history (no quote/order-book data)

**Key result:**

Cached-data diagnostic measured peak USD/USDT basis change over the 30-120s
lookback range at roughly 5-30 bps, with a maximum within-window basis range
of ~55 bps. Round-trip execution cost on Kraken spot for the two-leg trade is
approximately 32 bps (~16 bps per leg). The observed signal magnitude is
structurally smaller than its own round-trip transaction cost, in the best
window observed.

**Prior attempts (non-tests):**

Two earlier verdict attempts did not produce a clean powered test:

1. **Instrumentation bug (first run):** The Family 1 evaluator in
   `offline_train_evaluation.py` and `offline_holdout_evaluation.py` read
   `cell.lookback_ms` from the plan but never used it in the basis computation.
   All lookback variants produced identical metrics. This was fixed by
   `family1_tick_basis.py` (the committed tick-basis implementation), but the
   pipeline never reached the evaluation phases in the multi-date run.

2. **Zero stress windows (second run):** The 7-date 13:00-16:00 UTC
   calendar-spread run produced zero promotable stress windows under the
   existing stress-window rules. This was initially suspected to be a window
   length issue, but the subsequent diagnostic showed the structural blocker
   is signal-vs-cost, not window length or data sparsity.

**The rejection rests on the cost-wall diagnostic (cached-data analysis of the
same 7 windows), not on the two earlier non-test runs.** No FDR, native null,
or holdout stage was reached, and this is correct because the cost-wall blocker
is structural and upstream of those stages.

**What this rejects:**

- **Family 1 reversion specifically** — the direction mapping `basis_change_bps
  < 0 => long, > 0 => short` on Kraken spot at the current retail fee tier.

**What this does NOT reject (untested adjacent hypotheses):**

- **Momentum framing** of the same basis (never tested) — `basis_change_bps < 0
  => short, > 0 => long` requires a separate precommitment and FDR family-size
  decision.
- **The same hypothesis under a maker-only/rebate cost model** (never tested) —
  a sub-10 bps round trip could change the cost-wall calculation.
- **Quote-basis behavior on lower-liquidity / weaker-peg stablecoins** (never
  tested) — USDC/USDT, DAI/USD, or FXS/FRAX pairs may exhibit larger basis
  dislocations than Kraken BTC/USD vs BTC/USDT.
- **Order-book level analysis** (never tested) — quote ticks may reveal
  sub-second basis divergence that trade prints miss.

## Locked Gates — Do Not Revisit Without Structural Change

The following hypotheses are locked. Do not reopen by changing lookbacks,
horizons, window length, or date set.

1. **Derivatives v2 Binance Perp-to-Spot** — Perp-spot basis beta lag. Signal
   magnitude structurally below perp funding + execution cost. Do not revisit
   without a materially different cost model or venue.

2. **Family 1 same-venue USD/USDT quote-basis reversion on Kraken spot.**
   Signal magnitude (5-30 bps basis change) is below round-trip cost (~32 bps).
   Do not revisit without a materially different cost model (maker-only/rebate
   tier), a materially different instrument (weaker-peg stablecoin with larger
   basis dislocations), or a different venue microstructure. Do not reopen by
   changing lookbacks, horizons, window length, or date set.
