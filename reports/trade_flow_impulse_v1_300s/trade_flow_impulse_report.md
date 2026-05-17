## Hypothesis

Trade-flow impulse: abnormal trade count, notional volume, signed imbalance, or large-trade bursts on one venue may predict short-horizon forward returns on another venue.

**This is a research measurement tool only.** No trading or execution.

## Data Coverage

| Venue | Symbol | Ticks | First Timestamp | Last Timestamp |
|---|---|---|---|---|---|
|kraken|BTC-USD|126|2026-05-13T00:22:13.685Z|2026-05-13T00:27:03.477Z|
|kraken|ETH-USD|38|2026-05-13T00:22:18.673Z|2026-05-13T00:27:08.117Z|
|coinbase|BTC-USD|1199|2026-05-13T00:22:12.612Z|2026-05-13T00:27:10.004Z|
|coinbase|ETH-USD|781|2026-05-13T00:22:12.890Z|2026-05-13T00:27:08.684Z|

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
|coinbase|kraken|BTC/USD|notional_burst|1000|25|138|12|-13.1506|0.0|
|coinbase|kraken|BTC/USD|notional_burst|5000|13|68|10|-13.1485|0.0|
|coinbase|kraken|BTC/USD|notional_burst|10000|8|45|3|-12.6224|0.0|
|coinbase|kraken|BTC/USD|notional_burst|30000|1|6|0|-12.6142|0.0|
|coinbase|kraken|BTC/USD|large_trade|1000|25|138|12|-12.6917|0.0|
|coinbase|kraken|BTC/USD|large_trade|5000|25|138|12|-12.6917|0.0|
|coinbase|kraken|BTC/USD|large_trade|10000|25|138|12|-12.6917|0.0|
|coinbase|kraken|BTC/USD|large_trade|30000|25|138|12|-12.6917|0.0|
|coinbase|kraken|BTC/USD|signed_imbalance|1000|27|149|13|-14.8395|0.0|
|coinbase|kraken|BTC/USD|signed_imbalance|5000|28|155|13|-14.7964|0.0|
|coinbase|kraken|BTC/USD|signed_imbalance|10000|27|148|14|-14.6498|0.0|
|coinbase|kraken|BTC/USD|signed_imbalance|30000|21|115|11|-14.8828|0.0|
|coinbase|kraken|ETH/USD|count_burst|1000|0|0|0|None|None|
|coinbase|kraken|ETH/USD|count_burst|5000|0|0|0|None|None|
|coinbase|kraken|ETH/USD|count_burst|10000|0|0|0|None|None|
|coinbase|kraken|ETH/USD|count_burst|30000|0|0|0|None|None|
|coinbase|kraken|ETH/USD|notional_burst|1000|24|123|21|-13.1013|0.0|
|coinbase|kraken|ETH/USD|notional_burst|5000|8|41|7|-12.6691|0.0|
|coinbase|kraken|ETH/USD|notional_burst|10000|3|16|2|-12.9191|0.0|
|coinbase|kraken|ETH/USD|notional_burst|30000|0|0|0|None|None|
|coinbase|kraken|ETH/USD|large_trade|1000|24|129|15|-12.6735|0.0|
|coinbase|kraken|ETH/USD|large_trade|5000|24|129|15|-12.6735|0.0|
|coinbase|kraken|ETH/USD|large_trade|10000|24|129|15|-12.6735|0.0|
|coinbase|kraken|ETH/USD|large_trade|30000|24|129|15|-12.6735|0.0|
|coinbase|kraken|ETH/USD|signed_imbalance|1000|25|123|27|-14.1952|0.0|
|coinbase|kraken|ETH/USD|signed_imbalance|5000|27|135|27|-15.4461|0.0|
|coinbase|kraken|ETH/USD|signed_imbalance|10000|26|135|21|-15.4075|0.0|
|coinbase|kraken|ETH/USD|signed_imbalance|30000|16|94|2|-15.8371|0.0|
|kraken|coinbase|BTC/USD|count_burst|1000|0|0|0|None|None|
|kraken|coinbase|BTC/USD|count_burst|5000|0|0|0|None|None|
|kraken|coinbase|BTC/USD|count_burst|10000|0|0|0|None|None|
|kraken|coinbase|BTC/USD|count_burst|30000|0|0|0|None|None|
|kraken|coinbase|BTC/USD|notional_burst|1000|12|70|2|-13.6956|0.0|
|kraken|coinbase|BTC/USD|notional_burst|5000|12|70|2|-13.2539|0.0|
|kraken|coinbase|BTC/USD|notional_burst|10000|13|76|2|-13.4468|0.0|
|kraken|coinbase|BTC/USD|notional_burst|30000|6|34|2|-12.476|0.0|
|kraken|coinbase|BTC/USD|large_trade|1000|16|91|5|-12.756|0.0|
|kraken|coinbase|BTC/USD|large_trade|5000|16|91|5|-12.756|0.0|
|kraken|coinbase|BTC/USD|large_trade|10000|16|91|5|-12.756|0.0|
|kraken|coinbase|BTC/USD|large_trade|30000|16|91|5|-12.756|0.0|
|kraken|coinbase|BTC/USD|signed_imbalance|1000|14|79|5|-13.6534|0.0|
|kraken|coinbase|BTC/USD|signed_imbalance|5000|15|86|4|-13.5438|0.0|
|kraken|coinbase|BTC/USD|signed_imbalance|10000|15|86|4|-13.7701|0.0|
|kraken|coinbase|BTC/USD|signed_imbalance|30000|16|89|7|-13.769|0.0|
|kraken|coinbase|ETH/USD|count_burst|1000|0|0|0|None|None|
|kraken|coinbase|ETH/USD|count_burst|5000|0|0|0|None|None|
|kraken|coinbase|ETH/USD|count_burst|10000|0|0|0|None|None|
|kraken|coinbase|ETH/USD|count_burst|30000|0|0|0|None|None|
|kraken|coinbase|ETH/USD|notional_burst|1000|10|54|6|-13.2141|0.0|
|kraken|coinbase|ETH/USD|notional_burst|5000|9|48|6|-13.3202|0.0|
|kraken|coinbase|ETH/USD|notional_burst|10000|10|54|6|-13.2062|0.0|
|kraken|coinbase|ETH/USD|notional_burst|30000|4|18|6|-11.9711|0.0|
|kraken|coinbase|ETH/USD|large_trade|1000|7|36|6|-12.8635|0.0|
|kraken|coinbase|ETH/USD|large_trade|5000|7|36|6|-12.8635|0.0|
|kraken|coinbase|ETH/USD|large_trade|10000|7|36|6|-12.8635|0.0|
|kraken|coinbase|ETH/USD|large_trade|30000|7|36|6|-12.8635|0.0|
|kraken|coinbase|ETH/USD|signed_imbalance|1000|3|18|0|-13.5779|0.0|
|kraken|coinbase|ETH/USD|signed_imbalance|5000|3|18|0|-13.5779|0.0|
|kraken|coinbase|ETH/USD|signed_imbalance|10000|5|30|0|-13.0569|0.0|
|kraken|coinbase|ETH/USD|signed_imbalance|30000|10|60|0|-13.0265|0.0|

## Best Groups by Mean Net Return (Top 5)

| Source | Target | Symbol | Type | LB(ms) | Mean Net(bps) | Valid | Win Rate |
|---|---|---|---|---|---|---|---|---|
|kraken|coinbase|ETH/USD|notional_burst|30000|-11.9711|18|0.0|
|kraken|coinbase|BTC/USD|notional_burst|30000|-12.476|34|0.0|
|coinbase|kraken|BTC/USD|notional_burst|30000|-12.6142|6|0.0|
|coinbase|kraken|BTC/USD|notional_burst|10000|-12.6224|45|0.0|
|coinbase|kraken|ETH/USD|notional_burst|5000|-12.6691|41|0.0|

## Baseline Comparison

Baseline mean net: **-13.9795 bps**  (valid events: 478, win rate: 0.0)

## Candidate Groups

No candidate groups passed all gates.

## Signal Count Clarification

| Count | Value | Meaning |
|---|---|---|
| Raw signal events | 724 | Unique (timestamp, venue, symbol, type) combos detected |
| Signal-horizon evaluations | 4,344 | 724 × 6 horizons |
| Valid forward returns | 3,957 | Forward price available |
| Rejected evaluations | 387 | No forward price (end-of-capture) |

count_burst = 0 signals in all pairs. 5-minute captures still too sparse for count-burst detection over a 60s baseline window.

## Final Verdict

**REJECTED.** Trade-flow impulse v1 re-run with a fresh 300s (5-minute) tick capture. Same result as v1: zero candidates across all pairs, all signal types, all lookbacks. Win rate 0.0% everywhere.

- **Capture:** 300s, 4 venue×symbol pairs (Kraken BTC+ETH, Coinbase BTC+ETH)
- **Total ticks:** 2,144 (126 + 38 + 1,199 + 781)
- **Best mean net:** -11.97 bps (KRK→CB ETH notional_burst 30000ms) — still below 19 bps all-in cost
- **count_burst:** 0 signals (too few ticks for count-burst over 60s baseline)

Same structural wall as v1 and tick lead-lag v3: no edge between Coinbase and Kraken for BTC/ETH on these timescales.

## Limitations

- Single 300s capture, single time period
- Only BTC/ETH from two venues
- Fee, slippage, and quote mismatch assumptions are simplified
- Random baseline is a simple sanity check
- Signals from trade ticks only; quote-level dynamics not modelled

## Next Step

Consider longer captures (minutes are better for count_burst), additional venues (Binance), more volatile assets (SOL, XRP, DOGE), or different signal sources (liquidation data, order-book imbalance).
## Next Step

Consider different signal types, longer capture windows, or additional venues/assets. Trade-flow impulse may require larger or more diverse datasets to detect statistically significant patterns.
