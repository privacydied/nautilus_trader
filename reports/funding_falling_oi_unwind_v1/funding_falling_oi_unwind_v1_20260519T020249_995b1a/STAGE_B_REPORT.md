# Stage B Evaluation Report: funding_falling_oi_unwind_v1_20260519T020249_995b1a
## Study: family3_funding_falling_oi_unwind_v1

- Run: funding_falling_oi_unwind_v1_20260519T020249_995b1a
- Preflight verdict: PREFLIGHT_PASSED
- Stage A outcome: POPULATION_SUFFICIENT
- Seed: 42
- Safety: public_data_observer_only

### Cell Results

| Cell | N | Holdout | Mean Gross (bps) | Mean Net (bps) | Median Net (bps) | Win Rate | Worst Decile | Baseline Delta | Null p | FDR | Final Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| negative_funding_extreme_falling_oi_24h | 223 | 66 | 54.8578 | -45.1422 | -61.5648 | 0.3946 | -491.7211 | 47.3035 | 0.00999000999000999 | REJECTED | GATES_FAILED |
| negative_funding_extreme_falling_oi_48h | 223 | 66 | 62.0055 | -37.9945 | -67.2407 | 0.435 | -604.7921 | 47.3834 | 0.03496503496503497 | SURVIVED | GATES_FAILED |

### Data Sources
- Funding: Binance Vision archive
- OI metrics: Binance Vision archive
- Spot klines: Binance Vision archive
- Archive only. No authenticated endpoints.

