# Cross-Asset Beta Lag Under Stress — Precommitment

**Status:** OPEN_IMPLEMENTATION

## Hypothesis

BTC/ETH shock leads slower repricing in higher-beta assets (SOL, LINK, AVAX, DOGE, ADA, etc.) over 30s to 5m horizons. This is distinct from same-asset derivatives-to-spot lead-lag (now rejected — see `REJECTED_RESEARCH.md`). The mechanism is cross-asset pricing friction, not venue latency.

## Research Rules

### Allowed

- Public-data observer only: capture ticks from Binance spot, Kraken, Coinbase, OKX, Bybit, and Bitfinex public WebSocket feeds; evaluate forward returns on alt spot.
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

## Venue Expansion Addendum (2026-05-16)

Supported observer venues for this signal are now: **binance, coinbase, kraken, okx, bybit** — all
public, unauthenticated WebSocket trade feeds. This expansion happened before any validated
FULL_ACTIVE capture for `cross_asset_beta_lag_v1` (current state: readiness passed, gate not
passed, 0 validated FULL_ACTIVE captures).

The following are **unchanged** by this venue expansion:
- Hypothesis, mechanism, and rejection condition.
- Signal thresholds and verdict taxonomy.
- Stress gate semantics (`MARKET_ACTIVE` + `ACCELERATING`, BTC/ETH source range ≥ 30 bps, etc.).
- Forward-return horizons (30 s – 5 m).
- Cost model (fee + slippage + quote mismatch).
- Candidate acceptance gates and FDR rules.
- Stage 2 capture count.

OKX and Bybit are observer-only: no auth, no private endpoints, no orders, no execution clients,
no API key environment variables. The expansion is surgical — it only broadens the venue set
from which cross-asset spot ticks can be sourced.

## Bitfinex Venue Expansion Addendum (2026-05-16)

Supported observer venues now also include **bitfinex**, via its public, unauthenticated
WebSocket trade feed (`wss://api-pub.bitfinex.com/ws/2`, `channel: trades`). This second-wave
expansion happened before any validated FULL_ACTIVE capture for `cross_asset_beta_lag_v1`
(current state: still 0 validated FULL_ACTIVE captures).

Bitfinex symbols supported: BTCUSD, BTCUST, ETHUSD, ETHUST, SOLUSD, SOLUST, LINK:USD, LINK:UST,
DOGE:USD, DOGE:UST, AVAX:USD, AVAX:UST (UST = Bitfinex's USDT-tether ticker).

Trade-only — no quote/orderbook channels were added.

The following remain **unchanged** by this Bitfinex expansion:
- 150 bps absolute stress gate.
- `signed_imbalance` definition.
- 30-second lookback.
- 5-minute primary horizon.
- 50 bps cost model.
- Candidate acceptance gates.
- FDR rules.
- 10 FULL_ACTIVE captures before Stage 2 evaluation.

Bitfinex is observer-only: no auth, no private endpoints, no orders, no execution clients, no
API key environment variables.
