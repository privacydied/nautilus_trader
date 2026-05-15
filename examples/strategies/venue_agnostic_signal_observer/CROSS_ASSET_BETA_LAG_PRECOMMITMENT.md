# Cross-Asset Beta Lag Under Stress — Precommitment

**Status:** OPEN_IMPLEMENTATION

## Hypothesis

BTC/ETH shock leads slower repricing in higher-beta assets (SOL, LINK, AVAX, DOGE, ADA, etc.) over 30s to 5m horizons. This is distinct from same-asset derivatives-to-spot lead-lag (now rejected — see `REJECTED_RESEARCH.md`). The mechanism is cross-asset pricing friction, not venue latency.

## Research Rules

### Allowed

- Public-data observer only: capture ticks from Binance spot/Kraken/Coinbase, evaluate forward returns on alt spot.
- Run the existing cross-asset impulse observer (`cross_asset_impulse.py`) during genuine market stress.
- Use existing infrastructure: the lead-lag evaluator, heatmap, cost sensitivity, permutation null, falsification tools.
- Run diagnostic filters: OI, funding, heatmap, cost sensitivity — as evidence layers, not standalone edges.

### Not Allowed

- No private keys, no orders, no live trading, no execution code.
- No capture during quiet or moderate market windows — only genuine stress.
- No adding filters after the fact to rescue a failed result.

## Required Market State

A genuine stress window, meaning:
- The existing volatility gate returns `MARKET_ACTIVE` + `ACCELERATING`, OR
- The fast diagnostic indicates ACCELERATING with 15m/30m ranges above threshold, OR
- BTC/ETH source range ≥ 30 bps over 15-60 minutes.

Do not test during `MARKET_QUIET`, `MARKET_MODERATE`, or `MARKET_ACTIVE` + `NOT_ACCELERATING`.

## Candidate Acceptance Gates

A candidate group must survive ALL of the following to be considered non-rejected:

1. Positive raw edge after full all-in costs (fee + slippage + quote mismatch).
2. Cost sensitivity shows viability below a realistic cost floor.
3. Permutation/null test passes (if candidate-worthy — i.e., sufficient events).
4. Cross-capture consistency across at least 2 separate stress captures.
5. Not driven by a single symbol, a single cluster, or a single horizon.

## Rejection Condition

If the hypothesis fails under a genuine stress capture with sufficient movement and events — mark as REJECTED in `REJECTED_RESEARCH.md`. Do not add post-hoc filters to salvage the result.

## Infrastructure

No new infrastructure should be added before the next empirical test. The existing `cross_asset_impulse.py` and lead-lag evaluator are sufficient for a stress-window test. If the hypothesis shows promise after stress testing, infrastructure improvements may be considered at that point.

---

*Created: 2026-05-15. Status is OPEN_IMPLEMENTATION until tested under genuine stress.*
