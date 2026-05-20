# Polymarket BTC Price Target Liquidity Probe V0

Study name: `polymarket_btc_price_target_strike_distance_liquidity_probe_v0`

Phase: 0 — liquidity and observability only.

Status: observer-only public-data experiment. No orders, no wallet, no signing, no auth.

---

## Safety

- Observer-only.
- Public data only.
- No orders.
- No wallet.
- No signing.
- No auth.
- No private keys.
- No API keys.
- No execution path.
- No bot/governance/ledger path.
- No Nautilus LiveNode trading setup.

## Nautilus adapter policy

1. Inspect and prefer safe Nautilus Polymarket data/instrument integration first.
2. Direct public Gamma/CLOB REST is fallback only.
3. Adapter path affects acquisition only, not verdict logic.
4. Execution/auth/wallet/signing paths are forbidden.

## Hypothesis boundary

BTC Price Target markets may have strike-adjacent CLOB liquidity pockets near expiry because they expose numerical strike distance, unlike BTC Up/Down markets.

This Phase 0 does not claim mispricing exists.

## Non-goals

- No fair-value model.
- No probability model.
- No edge test.
- No forward returns.
- No realized PnL.
- No null/MCPT/FDR/holdout.
- No candidate/trade-ready verdict.
- No registry rejection.
- No BTC Up/Down retest.
- No funding task.

## Allowed Phase 0 verdicts

- `GREEN_LIQUIDITY_DIAGNOSTIC`
- `YELLOW_LIQUIDITY_DIAGNOSTIC`
- `RED_LIQUIDITY_DIAGNOSTIC`
- `NEEDS_MORE_DATA`
- `CAPTURE_UNUSABLE`

## Forbidden verdicts

- `REJECTED`
- `CANDIDATE`
- `CANDIDATE_FOR_LONGER_OBSERVATION`
- `EXECUTION_READY`
- `TRADE_READY`
- `CANDIDATE_FOR_LIVE`

## Up/Down exclusion fixtures

The following are Up/Down markets, not Price Target markets:

- `https://polymarket.com/event/btc-updown-5m-1779137400`
- `https://polymarket.com/event/btc-updown-15m-1779137100`
- `https://polymarket.com/event/bitcoin-up-or-down-may-18-2026-4pm-et`
- `https://polymarket.com/event/btc-updown-4h-1779134400`
- `https://polymarket.com/event/bitcoin-up-or-down-on-may-19-2026`

Classification expected:

- Market family: `BTC_UPDOWN`
- Strike extraction status: `NO_STRIKE_FOUND`
- Exclusion reason: `EXCLUDED_BTC_UPDOWN_NO_NUMERICAL_STRIKE`

## Data path

Selected: `PUBLIC_REST_FALLBACK_USED`

Reason: Nautilus Polymarket data-side components (`PolymarketLiveDataClient`, `PolymarketInstrumentProvider`) require `ClobClient` for initialization, which requires API credentials/authentication. The safe Nautilus `gamma_markets` module uses `nautilus_trader.core.nautilus_pyo3.HttpClient` (Rust-backed extension unavailable on the execution platform due to GLIBC constraints). Direct httpx calls to the public Gamma Markets API (`gamma-api.polymarket.com`) and CLOB API (`clob.polymarket.com/book/{asset_id}`) require no auth and are proven by the existing Up/Down probe.

## Market eligibility

A market is eligible for sampling only if:

- Refers to BTC/Bitcoin.
- Classifier returns `BTC_PRICE_TARGET`.
- Strike extraction succeeds.
- Market is active/non-expired.
- Token IDs / YES-NO mapping are available.
- Not Up/Down (all forms).

## TTE buckets

| Bucket | Range |
|---|---|
| `>15m` | tte_seconds > 900 |
| `5-15m` | 300 < tte_seconds <= 900 |
| `2-5m` | 120 < tte_seconds <= 300 |
| `1-2m` | 60 < tte_seconds <= 120 |
| `30s-1m` | 30 < tte_seconds <= 60 |
| `0-30s` | 0 <= tte_seconds <= 30 |
| `expired` | tte_seconds < 0 |
| `unknown` | expiry missing/unparsable |

## Distance buckets

Distance to strike in bps:

`distance_bps = ((btc_proxy_price - strike_price) / strike_price) * 10000`

| Bucket | Range |
|---|---|
| `deep_below_strike` | distance <= -500 |
| `below_strike_100_500bps` | -500 < distance <= -100 |
| `below_strike_25_100bps` | -100 < distance < -25 |
| `near_strike_abs_25bps` | -25 <= distance <= 25 |
| `above_strike_25_100bps` | 25 < distance < 100 |
| `above_strike_100_500bps` | 100 <= distance < 500 |
| `deep_above_strike` | distance >= 500 |
| `unknown` | missing/invalid |

## Convex danger zone

TTE <= 120s AND abs(distance_bps) <= 25.

## Verdict rules

| Condition | Verdict |
|---|---|
| Primary subset >= 5 samples, spread green, depth green, two-sided green | `GREEN_LIQUIDITY_DIAGNOSTIC` |
| Primary subset >= 5 samples, no red, at least one yellow | `YELLOW_LIQUIDITY_DIAGNOSTIC` |
| Primary subset >= 5 samples, any red | `RED_LIQUIDITY_DIAGNOSTIC` |
| Discovery worked but primary subset < 5 samples | `NEEDS_MORE_DATA` |
| Zero active Price Target markets, zero valid samples | `NEEDS_MORE_DATA` |
| Capture failed entirely (API/network) | `CAPTURE_UNUSABLE` |

## V1 recommendation

| Verdict | V1 recommendation |
|---|---|
| `GREEN_LIQUIDITY_DIAGNOSTIC` | `V1_PRECOMMITMENT_JUSTIFIED` |
| `YELLOW_LIQUIDITY_DIAGNOSTIC` | `V1_REQUIRES_HUMAN_REVIEW` |
| `RED_LIQUIDITY_DIAGNOSTIC` | `V1_BLOCKED_BY_PHASE0_LIQUIDITY` |
| `NEEDS_MORE_DATA` | `V1_BLOCKED_BY_INSUFFICIENT_DATA` |
| `CAPTURE_UNUSABLE` | `V1_BLOCKED_BY_INSUFFICIENT_DATA` |

## Component status thresholds

| Component | Green | Yellow | Red |
|---|---|---|---|
| Spread | median <= 3c, p95 <= 10c | median > 3c <= 6c, or p95 > 10c with median <= 6c | median > 6c |
| Depth | median >= $100 | median >= $50 < $100 | median < $50 |
| Two-sided rate | rate >= 0.90 | rate >= 0.70 < 0.90 | rate < 0.70 |

## Output artifacts

```
reports/polymarket_btc_price_target_liquidity_probe_v0/<run_id>/
  adapter_inspection.json
  markets.json
  samples.jsonl
  summary.json
  liquidity_grid.csv
  report.md
  raw_payloads.jsonl (optional, capped)
```
