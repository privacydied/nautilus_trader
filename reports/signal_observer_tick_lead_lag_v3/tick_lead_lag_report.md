## Hypothesis

Tick-level price movements on a "source" venue lead equivalent movements on a "target" venue within a short time window. If this lead-lag relationship is statistically significant and survives cost-adjusted analysis, it may indicate a microstructure signal worth further investigation.

**This is a research measurement tool only.** No trading or execution decisions are made by this report.

## Data Coverage

| Venue | Symbol | Ticks | First Timestamp | Last Timestamp |
|---|---|---|---|---|---|
|coinbase|BTC-USD|2361|2026-05-12T22:26:25.929Z|2026-05-12T22:42:06.938Z|
|coinbase|ETH-USD|1120|2026-05-12T22:26:29.920Z|2026-05-12T22:42:06.855Z|
|kraken|BTC-USD|299|2026-05-12T22:26:35.690Z|2026-05-12T22:42:06.290Z|
|kraken|ETH-USD|116|2026-05-12T22:26:50.092Z|2026-05-12T22:42:06.293Z|

## Venues / Symbols Tested

- **Source venues:** coinbase, kraken

- **Target venues:** kraken, coinbase

- **Symbols:** BTC-USD, ETH-USD

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
|coinbase|kraken|BTC-USD|1000|2.0|6|30|0|-13.1044|0.0|
|coinbase|kraken|BTC-USD|1000|5.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|1000|10.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|1000|20.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|5000|2.0|6|30|0|-13.1044|0.0|
|coinbase|kraken|BTC-USD|5000|5.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|5000|10.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|5000|20.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|10000|2.0|6|29|1|-13.071|0.0|
|coinbase|kraken|BTC-USD|10000|5.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|10000|10.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|10000|20.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|30000|2.0|21|105|0|-13.2433|0.0|
|coinbase|kraken|BTC-USD|30000|5.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|30000|10.0|0|0|0|None|None|
|coinbase|kraken|BTC-USD|30000|20.0|0|0|0|None|None|
|coinbase|kraken|ETH-USD|1000|2.0|5|25|0|-12.7586|0.0|
|coinbase|kraken|ETH-USD|1000|5.0|0|0|0|None|None|
|coinbase|kraken|ETH-USD|1000|10.0|0|0|0|None|None|
|coinbase|kraken|ETH-USD|1000|20.0|0|0|0|None|None|
|coinbase|kraken|ETH-USD|5000|2.0|10|45|5|-13.0955|0.0|
|coinbase|kraken|ETH-USD|5000|5.0|0|0|0|None|None|
|coinbase|kraken|ETH-USD|5000|10.0|0|0|0|None|None|
|coinbase|kraken|ETH-USD|5000|20.0|0|0|0|None|None|
|coinbase|kraken|ETH-USD|10000|2.0|15|70|5|-13.396|0.0|
|coinbase|kraken|ETH-USD|10000|5.0|0|0|0|None|None|
|coinbase|kraken|ETH-USD|10000|10.0|0|0|0|None|None|
|coinbase|kraken|ETH-USD|10000|20.0|0|0|0|None|None|
|coinbase|kraken|ETH-USD|30000|2.0|29|140|5|-13.3641|0.0|
|coinbase|kraken|ETH-USD|30000|5.0|3|15|0|-12.9413|0.0|
|coinbase|kraken|ETH-USD|30000|10.0|0|0|0|None|None|
|coinbase|kraken|ETH-USD|30000|20.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|1000|2.0|2|10|0|-12.2841|0.0|
|kraken|coinbase|BTC-USD|1000|5.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|1000|10.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|1000|20.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|5000|2.0|3|15|0|-12.6901|0.0|
|kraken|coinbase|BTC-USD|5000|5.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|5000|10.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|5000|20.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|10000|2.0|4|20|0|-13.0432|0.0|
|kraken|coinbase|BTC-USD|10000|5.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|10000|10.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|10000|20.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|30000|2.0|8|40|0|-13.2264|0.0|
|kraken|coinbase|BTC-USD|30000|5.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|30000|10.0|0|0|0|None|None|
|kraken|coinbase|BTC-USD|30000|20.0|0|0|0|None|None|
|kraken|coinbase|ETH-USD|1000|2.0|2|10|0|-13.2079|0.0|
|kraken|coinbase|ETH-USD|1000|5.0|0|0|0|None|None|
|kraken|coinbase|ETH-USD|1000|10.0|0|0|0|None|None|
|kraken|coinbase|ETH-USD|1000|20.0|0|0|0|None|None|
|kraken|coinbase|ETH-USD|5000|2.0|2|10|0|-13.2079|0.0|
|kraken|coinbase|ETH-USD|5000|5.0|0|0|0|None|None|
|kraken|coinbase|ETH-USD|5000|10.0|0|0|0|None|None|
|kraken|coinbase|ETH-USD|5000|20.0|0|0|0|None|None|
|kraken|coinbase|ETH-USD|10000|2.0|2|10|0|-13.2079|0.0|
|kraken|coinbase|ETH-USD|10000|5.0|0|0|0|None|None|
|kraken|coinbase|ETH-USD|10000|10.0|0|0|0|None|None|
|kraken|coinbase|ETH-USD|10000|20.0|0|0|0|None|None|
|kraken|coinbase|ETH-USD|30000|2.0|7|35|0|-13.1147|0.0|
|kraken|coinbase|ETH-USD|30000|5.0|0|0|0|None|None|
|kraken|coinbase|ETH-USD|30000|10.0|0|0|0|None|None|
|kraken|coinbase|ETH-USD|30000|20.0|0|0|0|None|None|

## Rejected Evaluations

Total rejected evaluations: **16**

| Rejection Reason | Count |
|---|---|---|
|no_forward_price_at_60000ms|4|
|no_forward_price_at_1000ms|3|
|no_forward_price_at_5000ms|3|
|no_forward_price_at_10000ms|3|
|no_forward_price_at_30000ms|3|

## Best Groups by Mean Net Return (Top 5)

| Source | Target | Symbol | LB (ms) | Th (bps) | Mean Net (bps) | Median Net (bps) | Valid Events | Win Rate |
|---|---|---|---|---|---|---|---|---|---|
|kraken|coinbase|BTC-USD|1000|2.0|-12.2841|-11.965|10|0.0|
|kraken|coinbase|BTC-USD|5000|2.0|-12.6901|-13.7929|15|0.0|
|coinbase|kraken|ETH-USD|1000|2.0|-12.7586|-14.0|25|0.0|
|coinbase|kraken|ETH-USD|30000|5.0|-12.9413|-14.0|15|0.0|
|kraken|coinbase|BTC-USD|10000|2.0|-13.0432|-13.4788|20|0.0|

## Best Groups by Median Net Return (Top 5)

| Source | Target | Symbol | LB (ms) | Th (bps) | Median Net (bps) | Mean Net (bps) | Valid Events | Win Rate |
|---|---|---|---|---|---|---|---|---|---|
|kraken|coinbase|BTC-USD|1000|2.0|-11.965|-12.2841|10|0.0|
|kraken|coinbase|ETH-USD|30000|2.0|-13.3436|-13.1147|35|0.0|
|kraken|coinbase|ETH-USD|1000|2.0|-13.4749|-13.2079|10|0.0|
|kraken|coinbase|ETH-USD|5000|2.0|-13.4749|-13.2079|10|0.0|
|kraken|coinbase|ETH-USD|10000|2.0|-13.4749|-13.2079|10|0.0|

## Baseline Comparison

Baseline mean net return: **-13.8509 bps**  (valid events: 59, win rate: 0.0)

The baseline uses randomly placed timestamps with the same signal count, evaluated on the same target data, to serve as a noise floor.

## Candidate Groups

No candidate groups passed all gates.

## Rejected Groups

64 group(s) were rejected:

- coinbase→kraken @ BTC-USD lb=1000ms th=2.0bps: insufficient_events: 30 < 50; mean_net_return_not_positive: -13.10 bps; median_net_too_negative: -13.99 bps; win_rate_fails: 0.0000 vs baseline 0.0000; does_not_beat_baseline_by_margin: -13.10 <= -13.89 + 1.00; single_event_driven: mean_without_best=-13.24 bps

- coinbase→kraken @ BTC-USD lb=1000ms th=5.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=1000ms th=10.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=1000ms th=20.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=5000ms th=2.0bps: insufficient_events: 30 < 50; mean_net_return_not_positive: -13.10 bps; median_net_too_negative: -13.99 bps; win_rate_fails: 0.0000 vs baseline 0.0000; does_not_beat_baseline_by_margin: -13.10 <= -13.89 + 1.00; single_event_driven: mean_without_best=-13.24 bps

- coinbase→kraken @ BTC-USD lb=5000ms th=5.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=5000ms th=10.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=5000ms th=20.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=10000ms th=2.0bps: insufficient_events: 29 < 50; mean_net_return_not_positive: -13.07 bps; median_net_too_negative: -13.99 bps; win_rate_fails: 0.0000 vs baseline 0.0000; does_not_beat_baseline_by_margin: -13.07 <= -13.89 + 1.00; single_event_driven: mean_without_best=-13.21 bps

- coinbase→kraken @ BTC-USD lb=10000ms th=5.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=10000ms th=10.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=10000ms th=20.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=30000ms th=2.0bps: mean_net_return_not_positive: -13.24 bps; median_net_too_negative: -13.99 bps; win_rate_fails: 0.0000 vs baseline 0.0000; does_not_beat_baseline_by_margin: -13.24 <= -14.15 + 1.00; single_event_driven: mean_without_best=-13.28 bps

- coinbase→kraken @ BTC-USD lb=30000ms th=5.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=30000ms th=10.0bps: no_valid_events

- coinbase→kraken @ BTC-USD lb=30000ms th=20.0bps: no_valid_events

- coinbase→kraken @ ETH-USD lb=1000ms th=2.0bps: insufficient_events: 25 < 50; mean_net_return_not_positive: -12.76 bps; median_net_too_negative: -14.00 bps; win_rate_fails: 0.0000 vs baseline 0.0000; does_not_beat_baseline_by_margin: -12.76 <= -13.55 + 1.00; single_event_driven: mean_without_best=-12.93 bps

- coinbase→kraken @ ETH-USD lb=1000ms th=5.0bps: no_valid_events

- coinbase→kraken @ ETH-USD lb=1000ms th=10.0bps: no_valid_events

- coinbase→kraken @ ETH-USD lb=1000ms th=20.0bps: no_valid_events

… and 44 more (see rejections.json).

## Signal Count Clarification

| Count | Value | Meaning |
|---|---|---|
| **Raw signal events** | 131 | Unique (timestamp, source-venue, symbol) combos that crossed a threshold |
| **Signal-horizon evaluations** | 655 | 131 signals × 5 horizons each (1s, 5s, 10s, 30s, 60s) |
| **Valid forward-return evaluations** | 639 | Evaluations where a forward price was available at the horizon |
| **Rejected evaluations** | 16 | No forward price at that horizon (end-of-capture edge) |

Per-pair breakdown (signals → events): CB→KRK BTC = 39 → 195, CB→KRK ETH = 62 → 310, KRK→CB BTC = 17 → 85, KRK→CB ETH = 13 → 65. Total: 131 → 655 = 639 valid + 16 rejected.

## Final Verdict

**REJECTED.** The tested Coinbase/Kraken BTC/ETH tick lead-lag setup is rejected. Across all cross-venue parameter groups, no group produced positive net forward returns after costs, and no group beat the deterministic random baseline sufficiently to pass the candidate gate.

**Scope of rejection:**
- **Venues:** Coinbase + Kraken (both directions)
- **Assets:** BTC, ETH
- **Data:** 600 seconds of public trade ticks (~10 min captures)
- **Thresholds/lookbacks tested:** 4 lookbacks (1–30s) × 4 thresholds (2–20bps)
- **Horizons:** 1s, 5s, 10s, 30s, 60s
- **Cost model:** ~19 bps all-in (12 fee + 2 slippage + 5 quote mismatch buffer)
- **Result:** No group beat fees or baseline

Same-venue comparisons were excluded. Symbol normalization worked, including Kraken XBT/USD → BTC/USD canonical matching.

## Limitations

- This analysis uses a single time period. Results may not generalise to other market regimes.

- Fee, slippage, and quote mismatch assumptions are simplified. Real execution costs may be higher, especially for larger sizes.

- The random baseline is a simple sanity check, not a rigorous statistical test. Multiple-comparison bias is not adjusted for.

- Signals are generated from trade ticks only; quote-level dynamics (spread changes, depth shifts) are not modelled.

- Cooldown gating may suppress correlated signals, potentially under-counting the true signal frequency.

## Next Step

Consider trying different lookback windows, thresholds, or venue pairs. Alternatively, collect higher-fidelity data (e.g., quotes instead of trades) or a longer time series.
