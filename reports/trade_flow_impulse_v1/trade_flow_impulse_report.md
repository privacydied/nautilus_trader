## Hypothesis

Trade-flow impulse: abnormal trade count, notional volume, signed imbalance, or large-trade bursts on one venue may predict short-horizon forward returns on another venue.

**This is a research measurement tool only.** No trading or execution.

## Data Coverage

| Venue | Symbol | Ticks | First Timestamp | Last Timestamp |
|---|---|---|---|---|---|
|coinbase|BTC-USD|2361|2026-05-12T22:26:25.929Z|2026-05-12T22:42:06.938Z|
|coinbase|ETH-USD|1120|2026-05-12T22:26:29.920Z|2026-05-12T22:42:06.855Z|
|kraken|BTC-USD|299|2026-05-12T22:26:35.690Z|2026-05-12T22:42:06.290Z|
|kraken|ETH-USD|116|2026-05-12T22:26:50.092Z|2026-05-12T22:42:06.293Z|

## Signal Parameters

- **Signal types:** count_burst, notional_burst, large_trade, signed_imbalance

- **Lookbacks (ms):** 1000, 5000, 10000, 30000

- **Baseline window (ms):** 60000

- **Horizons (ms):** 1000, 2000, 5000, 10000, 30000, 60000

- **Cooldown (ms):** 10000

## Fee / Slippage

- **Fee:** 12.0 bps

- **Slippage:** 2.0 bps

- **Quote mismatch buffer:** 5.0 bps

- **Total cost per trade:** 19.0 bps

## Results by Group

| Source | Target | Symbol | Signal Type | LB(ms) | Signals | Valid | Rejected | Mean Net(bps) | Win Rate |
|---|---|---|---|---|---|---|---|---|---|---|
|coinbase|kraken|BTC/USD|count_burst|1000|0|0|0|None|None|
|coinbase|kraken|BTC/USD|count_burst|5000|0|0|0|None|None|
|coinbase|kraken|BTC/USD|count_burst|10000|0|0|0|None|None|
|coinbase|kraken|BTC/USD|count_burst|30000|0|0|0|None|None|
|coinbase|kraken|BTC/USD|notional_burst|1000|78|456|12|-14.0277|0.0|
|coinbase|kraken|BTC/USD|notional_burst|5000|43|251|7|-13.8608|0.0|
|coinbase|kraken|BTC/USD|notional_burst|10000|30|173|7|-13.8216|0.0|
|coinbase|kraken|BTC/USD|notional_burst|30000|5|27|3|-13.9568|0.0|
|coinbase|kraken|BTC/USD|large_trade|1000|74|435|9|-14.0195|0.0|
|coinbase|kraken|BTC/USD|large_trade|5000|74|435|9|-14.0195|0.0|
|coinbase|kraken|BTC/USD|large_trade|10000|74|435|9|-14.0195|0.0|
|coinbase|kraken|BTC/USD|large_trade|30000|74|435|9|-14.0195|0.0|
|coinbase|kraken|BTC/USD|signed_imbalance|1000|73|429|9|-13.9742|0.0|
|coinbase|kraken|BTC/USD|signed_imbalance|5000|79|465|9|-13.9798|0.0|
|coinbase|kraken|BTC/USD|signed_imbalance|10000|73|428|10|-14.1441|0.0|
|coinbase|kraken|BTC/USD|signed_imbalance|30000|39|228|6|-14.178|0.0|
|coinbase|kraken|ETH/USD|count_burst|1000|0|0|0|None|None|
|coinbase|kraken|ETH/USD|count_burst|5000|0|0|0|None|None|
|coinbase|kraken|ETH/USD|count_burst|10000|0|0|0|None|None|
|coinbase|kraken|ETH/USD|count_burst|30000|0|0|0|None|None|
|coinbase|kraken|ETH/USD|notional_burst|1000|59|344|10|-14.0378|0.0|
|coinbase|kraken|ETH/USD|notional_burst|5000|41|236|10|-13.7534|0.0|
|coinbase|kraken|ETH/USD|notional_burst|10000|18|99|9|-14.4566|0.0|
|coinbase|kraken|ETH/USD|notional_burst|30000|6|36|0|-14.1339|0.0|
|coinbase|kraken|ETH/USD|large_trade|1000|62|361|11|-14.1631|0.0|
|coinbase|kraken|ETH/USD|large_trade|5000|62|361|11|-14.1631|0.0|
|coinbase|kraken|ETH/USD|large_trade|10000|62|361|11|-14.1631|0.0|
|coinbase|kraken|ETH/USD|large_trade|30000|62|361|11|-14.1631|0.0|
|coinbase|kraken|ETH/USD|signed_imbalance|1000|59|348|6|-14.1628|0.0|
|coinbase|kraken|ETH/USD|signed_imbalance|5000|70|409|11|-14.1752|0.0|
|coinbase|kraken|ETH/USD|signed_imbalance|10000|66|386|10|-14.0984|0.0|
|coinbase|kraken|ETH/USD|signed_imbalance|30000|54|313|11|-14.2011|0.0|
|kraken|coinbase|BTC/USD|count_burst|1000|0|0|0|None|None|
|kraken|coinbase|BTC/USD|count_burst|5000|0|0|0|None|None|
|kraken|coinbase|BTC/USD|count_burst|10000|0|0|0|None|None|
|kraken|coinbase|BTC/USD|count_burst|30000|0|0|0|None|None|
|kraken|coinbase|BTC/USD|notional_burst|1000|33|194|4|-13.7168|0.0|
|kraken|coinbase|BTC/USD|notional_burst|5000|32|188|4|-14.0068|0.0|
|kraken|coinbase|BTC/USD|notional_burst|10000|25|145|5|-14.0916|0.0|
|kraken|coinbase|BTC/USD|notional_burst|30000|11|66|0|-13.6927|0.0|
|kraken|coinbase|BTC/USD|large_trade|1000|31|181|5|-13.9175|0.0|
|kraken|coinbase|BTC/USD|large_trade|5000|31|181|5|-13.9175|0.0|
|kraken|coinbase|BTC/USD|large_trade|10000|31|181|5|-13.9175|0.0|
|kraken|coinbase|BTC/USD|large_trade|30000|31|181|5|-13.9175|0.0|
|kraken|coinbase|BTC/USD|signed_imbalance|1000|19|112|2|-13.6595|0.0|
|kraken|coinbase|BTC/USD|signed_imbalance|5000|33|196|2|-13.7346|0.0|
|kraken|coinbase|BTC/USD|signed_imbalance|10000|42|243|9|-13.7347|0.0|
|kraken|coinbase|BTC/USD|signed_imbalance|30000|44|260|4|-13.879|0.0|
|kraken|coinbase|ETH/USD|count_burst|1000|0|0|0|None|None|
|kraken|coinbase|ETH/USD|count_burst|5000|0|0|0|None|None|
|kraken|coinbase|ETH/USD|count_burst|10000|0|0|0|None|None|
|kraken|coinbase|ETH/USD|count_burst|30000|0|0|0|None|None|
|kraken|coinbase|ETH/USD|notional_burst|1000|17|99|3|-14.072|0.0|
|kraken|coinbase|ETH/USD|notional_burst|5000|18|105|3|-13.9817|0.0|
|kraken|coinbase|ETH/USD|notional_burst|10000|17|99|3|-14.0168|0.0|
|kraken|coinbase|ETH/USD|notional_burst|30000|9|54|0|-13.7838|0.0|
|kraken|coinbase|ETH/USD|large_trade|1000|20|118|2|-14.0647|0.0|
|kraken|coinbase|ETH/USD|large_trade|5000|20|118|2|-14.0647|0.0|
|kraken|coinbase|ETH/USD|large_trade|10000|20|118|2|-14.0647|0.0|
|kraken|coinbase|ETH/USD|large_trade|30000|20|118|2|-14.0647|0.0|
|kraken|coinbase|ETH/USD|signed_imbalance|1000|4|24|0|-14.3557|0.0|
|kraken|coinbase|ETH/USD|signed_imbalance|5000|12|71|1|-14.0118|0.0|
|kraken|coinbase|ETH/USD|signed_imbalance|10000|13|77|1|-14.0717|0.0|
|kraken|coinbase|ETH/USD|signed_imbalance|30000|28|162|6|-13.8929|0.0|

## Best Groups by Mean Net Return (Top 5)

| Source | Target | Symbol | Type | LB(ms) | Mean Net(bps) | Valid | Win Rate |
|---|---|---|---|---|---|---|---|---|
|kraken|coinbase|BTC/USD|signed_imbalance|1000|-13.6595|112|0.0|
|kraken|coinbase|BTC/USD|notional_burst|30000|-13.6927|66|0.0|
|kraken|coinbase|BTC/USD|notional_burst|1000|-13.7168|194|0.0|
|kraken|coinbase|BTC/USD|signed_imbalance|5000|-13.7346|196|0.0|
|kraken|coinbase|BTC/USD|signed_imbalance|10000|-13.7347|243|0.0|

## Baseline Comparison

Baseline mean net: **-13.8442 bps**  (valid events: 1167, win rate: 0.0)

## Candidate Groups

No candidate groups passed all gates.

## Signal Count Clarification

| Count | Value | Meaning |
|---|---|---|
| **Raw signal events** | 1898 | Unique (timestamp, source-venue, symbol, signal-type) combos detected |
| **Signal-horizon evaluations** | 11388 | 1898 signals × 6 horizons each |
| **Valid forward-return evaluations** | 11103 | Evaluations with a forward price at the horizon |
| **Rejected evaluations** | 285 | No forward price at that horizon (end-of-capture edge) |

Per-pair breakdown (signals): CB->KRK BTC = 716, CB->KRK ETH = 621, KRK->CB BTC = 363, KRK->CB ETH = 198. Total: 1898. 11388 = 11103 valid + 285 rejected. count_burst produced zero signals across all pairs/lookbacks on this dataset.

## Final Verdict

**REJECTED.** Trade-flow impulse on Coinbase/Kraken BTC/ETH with ~10 min tick captures. Across all cross-venue parameter groups and all 4 signal types, zero produced positive net forward returns after ~19 bps all-in cost, and zero beat the random baseline.

**Scope of rejection:**
- **Venues:** Coinbase + Kraken (both directions)
- **Assets:** BTC, ETH
- **Data:** ~600 seconds of public trade ticks (4 venue pairs)
- **Signal types:** count_burst, notional_burst, large_trade, signed_imbalance
- **Lookbacks tested:** 4 lookbacks (1s, 5s, 10s, 30s)
- **Horizons:** 1s, 2s, 5s, 10s, 30s, 60s
- **Cost model:** ~19 bps all-in (12 fee + 2 slippage + 5 quote mismatch)
- **Result:** Best mean net = -13.66 bps (KRK->CB BTC signed_imbalance 1000ms). Win rate = 0.0% across every group. count_burst = 0 signals.

The -14 bps mean net, 0.0% win rate pattern matches the v3 tick lead-lag rejection. On this data volume, no flow signal shows predictive edge between these venues.

## Limitations

- Single time period — results may not generalise to other regimes.

- Fee, slippage, and quote mismatch assumptions are simplified.

- Random baseline is a simple sanity check, not a rigorous statistical test.

- Signals from trade ticks only; quote-level dynamics not modelled.

## Next Step

Consider different signal types, longer capture windows, or additional venues/assets. Trade-flow impulse may require larger or more diverse datasets to detect statistically significant patterns.
