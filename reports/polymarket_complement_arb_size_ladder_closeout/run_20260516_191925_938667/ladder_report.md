# Size Ladder Diagnostic Closeout — Complement Arb

## Configuration

- dust threshold notional: min_order_usdc = 10.0
- min net edge per share: 0.005
- min economic net harvest USDC: 0.1

A quote size is NOT economically valid unless it passes ALL three thresholds:
1. Notional >= min_order_usdc
2. Net edge per share >= diagnostic_min_net_edge_per_share
3. Net harvest USDC >= diagnostic_min_economic_net_harvest_usdc

## Per-Size Results

| Quote Size | Opportunities | Non-Dust | Pess Paired | One-Leg | Median Paired Edge/Shr | Paired Gain | Unwind Loss | Net Harvest | Exp Edge/Opp | Dust Count |
|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 6 | 0 | 0 | 0 | N/A | 0.0000 | 0.0000 | 0.0000 | 0.000000 | 6 |
| 10 | 6 | 0 | 0 | 0 | N/A | 0.0000 | 0.0000 | 0.0000 | 0.000000 | 6 |
| 25 | 6 | 0 | 0 | 0 | N/A | 0.0000 | 0.0000 | 0.0000 | 0.000000 | 6 |
| 50 | 6 | 0 | 0 | 0 | N/A | 0.0000 | 0.0000 | 0.0000 | 0.000000 | 6 |
| 100 | 6 | 0 | 0 | 0 | N/A | 0.0000 | 0.0000 | 0.0000 | 0.000000 | 6 |

## Interpretation

The base frozen status FROZEN_NO_PESSIMISTIC_PAIRED_FILL_EDGE applies to
base_quote_size = 100. The size ladder above adds resolution at smaller
quote sizes without altering the base verdict.

If any smaller quote size shows positive pessimistic paired fills:
- It does NOT overturn the 100-share freeze
- It reveals a possible FROZEN_EDGE_ONLY_AT_DUST_SIZE outcome
- It does NOT create CANDIDATE_FOR_LONGER_OBSERVATION

If ALL sizes show zero fills: the freeze deepens — no quote size produces
executable pessimistic queue turnover on these markets.

## Note

This diagnostic was run after the original campaign and the hourly addendum.
Total opportunities observed in this diagnostic run: 6.
