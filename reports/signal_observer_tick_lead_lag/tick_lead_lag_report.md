## Hypothesis

Tick-level price movements on a "source" venue lead equivalent movements on a "target" venue within a short time window. If this lead-lag relationship is statistically significant and survives cost-adjusted analysis, it may indicate a microstructure signal worth further investigation.

**This is a research measurement tool only.** No trading or execution decisions are made by this report.

## Data Coverage

| Venue | Symbol | Ticks | First Timestamp | Last Timestamp |
|---|---|---|---|---|---|
|coinbase|BTC-USD|113|2026-05-12T20:07:26.015Z|2026-05-12T20:07:53.379Z|

## Venues / Symbols Tested

- **Source venues:** coinbase

- **Target venues:** coinbase

- **Symbols:** BTC-USD

## Signal Parameters Tested

- **Lookbacks (ms):** 1000, 5000, 10000, 30000

- **Thresholds (bps):** 5.0, 10.0, 20.0

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
|coinbase|coinbase|BTC-USD|1000|5.0|0|0|0|None|None|
|coinbase|coinbase|BTC-USD|1000|10.0|0|0|0|None|None|
|coinbase|coinbase|BTC-USD|1000|20.0|0|0|0|None|None|
|coinbase|coinbase|BTC-USD|5000|5.0|0|0|0|None|None|
|coinbase|coinbase|BTC-USD|5000|10.0|0|0|0|None|None|
|coinbase|coinbase|BTC-USD|5000|20.0|0|0|0|None|None|
|coinbase|coinbase|BTC-USD|10000|5.0|0|0|0|None|None|
|coinbase|coinbase|BTC-USD|10000|10.0|0|0|0|None|None|
|coinbase|coinbase|BTC-USD|10000|20.0|0|0|0|None|None|
|coinbase|coinbase|BTC-USD|30000|5.0|0|0|0|None|None|
|coinbase|coinbase|BTC-USD|30000|10.0|0|0|0|None|None|
|coinbase|coinbase|BTC-USD|30000|20.0|0|0|0|None|None|

## Rejected Evaluations

Total rejected evaluations: **0**

No rejections.

## Best Groups by Mean Net Return (Top 5)

No groups with valid mean net returns.

## Best Groups by Median Net Return (Top 5)

No groups with valid median net returns.

## Baseline Comparison

Baseline mean net return: **None bps**  (valid events: 0, win rate: None)

The baseline uses randomly placed timestamps with the same signal count, evaluated on the same target data, to serve as a noise floor.

## Candidate Groups

No candidate groups passed all gates.

## Rejected Groups

12 group(s) were rejected:

- coinbase→coinbase @ BTC-USD lb=1000ms th=5.0bps: no_valid_events

- coinbase→coinbase @ BTC-USD lb=1000ms th=10.0bps: no_valid_events

- coinbase→coinbase @ BTC-USD lb=1000ms th=20.0bps: no_valid_events

- coinbase→coinbase @ BTC-USD lb=5000ms th=5.0bps: no_valid_events

- coinbase→coinbase @ BTC-USD lb=5000ms th=10.0bps: no_valid_events

- coinbase→coinbase @ BTC-USD lb=5000ms th=20.0bps: no_valid_events

- coinbase→coinbase @ BTC-USD lb=10000ms th=5.0bps: no_valid_events

- coinbase→coinbase @ BTC-USD lb=10000ms th=10.0bps: no_valid_events

- coinbase→coinbase @ BTC-USD lb=10000ms th=20.0bps: no_valid_events

- coinbase→coinbase @ BTC-USD lb=30000ms th=5.0bps: no_valid_events

- coinbase→coinbase @ BTC-USD lb=30000ms th=10.0bps: no_valid_events

- coinbase→coinbase @ BTC-USD lb=30000ms th=20.0bps: no_valid_events

## Final Verdict

**NEEDS_MORE_DATA**

No tick data was found for the specified venues and symbols. Collect more data and re-run.

## Limitations

- This analysis uses a single time period. Results may not generalise to other market regimes.

- Fee, slippage, and quote mismatch assumptions are simplified. Real execution costs may be higher, especially for larger sizes.

- The random baseline is a simple sanity check, not a rigorous statistical test. Multiple-comparison bias is not adjusted for.

- Signals are generated from trade ticks only; quote-level dynamics (spread changes, depth shifts) are not modelled.

- Cooldown gating may suppress correlated signals, potentially under-counting the true signal frequency.

## Next Step

Collect tick data for the specified venues and symbols, then re-run with `--ticks <data_dir>`.
