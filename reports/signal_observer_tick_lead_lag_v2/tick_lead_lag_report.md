## Hypothesis

Tick-level price movements on a "source" venue lead equivalent movements on a "target" venue within a short time window. If this lead-lag relationship is statistically significant and survives cost-adjusted analysis, it may indicate a microstructure signal worth further investigation.

**This is a research measurement tool only.** No trading or execution decisions are made by this report.

## Data Coverage

| Venue | Symbol | Ticks | First Timestamp | Last Timestamp |
|---|---|---|---|---|---|
|kraken|BTC-USD|158|2026-05-12T21:23:19.117Z|2026-05-12T21:28:15.665Z|
|coinbase|BTC-USD|1007|2026-05-12T21:23:17.575Z|2026-05-12T21:28:16.229Z|

## Venues / Symbols Tested

- **Source venues:** coinbase, kraken

- **Target venues:** kraken, coinbase

- **Symbols:** BTC-USD

## Signal Parameters Tested

- **Lookbacks (ms):** 1000, 5000, 10000, 30000

- **Thresholds (bps):** 2.0, 5.0, 10.0, 20.0

- **Horizons (ms):** 1000, 5000, 10000, 30000, 60000

- **Cooldown (ms):** 10000

## Fee / Slippage Assumptions

- **Fee:** 12.0 bps

- **Slippage:** 2.0 bps

- **Quote mismatch buffer:** 5.0 bps

- **Total cost per trade:** 19.0 bps

## Total Events by Group

| Source | Target | Symbol | Lookback (ms) | Threshold (bps) | Signals | Valid | Rejected | Mean Net (bps) | Win Rate |
|---|---|---|---|---|---|---|---|---|---|---|
|coinbase|kraken|BTC-USD|1000|2.0|1|5|0|-14.124|0.0|
|coinbase|kraken|BTC-USD|1000|5.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|1000|10.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|1000|20.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|5000|2.0|2|10|0|-14.0558|0.0|
|coinbase|kraken|BTC-USD|5000|5.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|5000|10.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|5000|20.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|10000|2.0|2|10|0|-14.0558|0.0|
|coinbase|kraken|BTC-USD|10000|5.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|10000|10.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|10000|20.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|30000|2.0|4|20|0|-13.7359|0.0|
|coinbase|kraken|BTC-USD|30000|5.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|30000|10.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|30000|20.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|1000|2.0|1|5|0|-13.9578|0.0|
|kraken|coinbase|BTC-USD|1000|5.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|1000|10.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|1000|20.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|5000|2.0|1|5|0|-13.9578|0.0|
|kraken|coinbase|BTC-USD|5000|5.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|5000|10.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|5000|20.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|10000|2.0|1|5|0|-13.9578|0.0|
|kraken|coinbase|BTC-USD|10000|5.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|10000|10.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|10000|20.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|30000|2.0|2|10|0|-14.0743|0.0|
|kraken|coinbase|BTC-USD|30000|5.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|30000|10.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|30000|20.0|0|0|0|None|None|

## Rejected Evaluations

Total rejected evaluations: **0**

No rejections.

## Best Groups by Mean Net Return (Top 5)

| Source | Target | Symbol | LB (ms) | Th (bps) | Mean Net (bps) | Median Net (bps) | Valid Events | Win Rate |
|---|---|---|---|---|---|---|---|---|---|
|coinbase|kraken|BTC-USD|30000|2.0|-13.7359|-13.845|20|0.0|
|kraken|coinbase|BTC-USD|1000|2.0|-13.9578|-13.9988|5|0.0|
|kraken|coinbase|BTC-USD|5000|2.0|-13.9578|-13.9988|5|0.0|
|kraken|coinbase|BTC-USD|10000|2.0|-13.9578|-13.9988|5|0.0|
|coinbase|kraken|BTC-USD|5000|2.0|-14.0558|-13.9876|10|0.0|

## Best Groups by Median Net Return (Top 5)

| Source | Target | Symbol | LB (ms) | Th (bps) | Median Net (bps) | Mean Net (bps) | Valid Events | Win Rate |
|---|---|---|---|---|---|---|---|---|---|
|coinbase|kraken|BTC-USD|30000|2.0|-13.845|-13.7359|20|0.0|
|kraken|coinbase|BTC-USD|30000|2.0|-13.9696|-14.0743|10|0.0|
|coinbase|kraken|BTC-USD|5000|2.0|-13.9876|-14.0558|10|0.0|
|coinbase|kraken|BTC-USD|10000|2.0|-13.9876|-14.0558|10|0.0|
|kraken|coinbase|BTC-USD|1000|2.0|-13.9988|-13.9578|5|0.0|

## Baseline Comparison

Baseline mean net return: **-14.0177 bps**  (valid events: 23, win rate: 0.0)

The baseline uses randomly placed timestamps with the same signal count, evaluated on the same target data, to serve as a noise floor.

## Candidate Groups

No candidate groups passed all gates.

## Rejected Groups

32 group(s) were rejected:

- coinbase→kraken @ BTC-USD lb=1000ms th=2.0bps: insufficient_events: 5 < 50; mean_net_return_not_positive: -14.12 bps; median_net_too_negative: -14.00 bps; win_rate_fails: 0.0000 vs baseline 0.0000; does_not_beat_baseline_by_margin: -14.12 <= -14.00 + 1.00; single_event_driven: mean_without_best=-14.21 bps

- coinbase→kraken @ BTC-USD lb=1000ms th=5.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=1000ms th=10.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=1000ms th=20.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=5000ms th=2.0bps: insufficient_events: 10 < 50; mean_net_return_not_positive: -14.06 bps; median_net_too_negative: -13.99 bps; win_rate_fails: 0.0000 vs baseline 0.0000; does_not_beat_baseline_by_margin: -14.06 <= -14.63 + 1.00; single_event_driven: mean_without_best=-14.09 bps

- coinbase→kraken @ BTC-USD lb=5000ms th=5.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=5000ms th=10.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=5000ms th=20.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=10000ms th=2.0bps: insufficient_events: 10 < 50; mean_net_return_not_positive: -14.06 bps; median_net_too_negative: -13.99 bps; win_rate_fails: 0.0000 vs baseline 0.0000; does_not_beat_baseline_by_margin: -14.06 <= -14.63 + 1.00; single_event_driven: mean_without_best=-14.09 bps

- coinbase→kraken @ BTC-USD lb=10000ms th=5.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=10000ms th=10.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=10000ms th=20.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=30000ms th=2.0bps: insufficient_events: 20 < 50; mean_net_return_not_positive: -13.74 bps; median_net_too_negative: -13.84 bps; win_rate_fails: 0.0000 vs baseline 0.0000; single_event_driven: mean_without_best=-13.77 bps

- coinbase→kraken @ BTC-USD lb=30000ms th=5.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=30000ms th=10.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=30000ms th=20.0bps: no_valid_events

- kraken→coinbase @ BTC-USD lb=1000ms th=2.0bps: insufficient_events: 5 < 50; mean_net_return_not_positive: -13.96 bps; median_net_too_negative: -14.00 bps; win_rate_fails: 0.0000 vs baseline 0.0000; does_not_beat_baseline_by_margin: -13.96 <= -13.78 + 1.00; single_event_driven: mean_without_best=-13.98 bps

- kraken→coinbase @ BTC-USD lb=1000ms th=5.0bps: no_valid_events

- kraken→coinbase @ BTC-USD lb=1000ms th=10.0bps: no_valid_events

- kraken→coinbase @ BTC-USD lb=1000ms th=20.0bps: no_valid_events

… and 12 more (see rejections.json).

## Final Verdict

**REJECTED**

Data was loaded but no signal group passed all candidate gates against the random baseline.

## Limitations

- This analysis uses a single time period. Results may not generalise to other market regimes.

- Fee, slippage, and quote mismatch assumptions are simplified. Real execution costs may be higher, especially for larger sizes.

- The random baseline is a simple sanity check, not a rigorous statistical test. Multiple-comparison bias is not adjusted for.

- Signals are generated from trade ticks only; quote-level dynamics (spread changes, depth shifts) are not modelled.

- Cooldown gating may suppress correlated signals, potentially under-counting the true signal frequency.

## Next Step

Consider trying different lookback windows, thresholds, or venue pairs. Alternatively, collect higher-fidelity data (e.g., quotes instead of trades) or a longer time series.
