# Polymarket Complement Arb — Precommitment Document

## Hypothesis

The `UP ask + DOWN ask + fees + buffers < 1.00` relationship on same-condition Polymarket binary markets should produce positive edge when both legs fill as maker quotes before a one-leg timeout.

## Assumptions

| Assumption | Value | Source |
|---|---|---|
| Base quote size | 100 shares | Precommitted before campaign |
| Size ladder sizes | 5, 10, 25, 50, 100 | Diagnostic closeout |
| Fill model | Pessimistic (cumulative compatible trade volume > depth ahead + quote size) | Shadow execution model |
| Fee formula | `shares * feeRate * price * (1 - price)` per leg | Polymarket documentation |
| Maker fee | 0 | Standard Polymarket structure, conservative |
| Fee rate source | Condition metadata from Gamma API (`feeSchedule.rate`) | When available |
| Fallback fee rate | 0.03 (assumed_precommitted_default) | Conservative crypto up/down default |
| Leg risk buffer | 0.002 per share | Config |
| Signing latency buffer | 0.001 per share | Config |
| Gas redeem buffer | 0.001 per pair | Config |
| Min net edge | 0.005 per share | Config |
| Max order | 100 USDC | Config |
| One-leg timeout | 30 seconds | Config |
| Resolution danger window | 1 hour before expiry | Config |

## Sufficiency Gates

| Gate | Value | Purpose |
|---|---|---|
| min_repeated_windows_per_market | 3 | Require repeated observation |
| min_total_repeated_windows | 6 | Require sufficient overall data |
| min_trade_evidence_ready_windows | 3 | Require functioning data pipeline |
| min_pessimistic_paired_fills_for_candidate | 20 | Require sufficient fill evidence |
| min_base_size_paired_fills_for_candidate | 10 | Require economic sizing |

## Verdict Rules

### NEEDS_MORE_DATA
- Repeated-window gates not met
- Trade evidence not ready
- Too few non-dust opportunities
- Only one market/window produced evidence

### FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE
- Trade evidence READY
- Repeated-window gates met
- Same market families observed across separated windows
- Opportunities detected
- Non-dust opportunities exist
- Base quote size pessimistic fills = 0 or economically dust
- Net shadow harvest ≤ 0
- Size-ladder replay blocked by schema (REPLAY_SCHEMA_INSUFFICIENT) — cumulative_fillable_volume not stored in artifacts

### CANDIDATE_FOR_LONGER_OBSERVATION
- Base quote size pessimistic paired fills positive
- Median paired_fill_realized_net_edge_per_share positive after fees/buffers
- Net shadow harvest positive after unwind losses
- Edge survives joint two-leg fill window
- Results repeat across required windows
- Non-dust size
- No safety guard weakened
