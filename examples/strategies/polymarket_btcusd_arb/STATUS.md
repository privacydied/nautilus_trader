# Polymarket BTC/USD Arb — Phase 1 + Phase 2 Status

## Scope
Phase 1: observer-only backtest and hypothesis validation. No live trading.
Phase 2: observer-only live data validation. No live trading. No orders. No keys.

## Git Branch
Current Nautilus branch: polymarket-btcusd-arb-phase2-observer
Base branch/commit: polymarket-btcusd-arb-phase1 / 1edc29c5c78b9199276f566c0b8e6258478c443b
Remote tracking branch: fork/polymarket-btcusd-arb-phase2-observer
Was implementation done on polymarket-btcusd-arb-phase2-observer: True
Nautilus working tree status before implementation: clean
Nautilus working tree status after implementation: modified live_capture.py, live_public_observer.py, live_reports.py, test_live_public_observer.py, STATUS.md
arb-bot working tree: no changes in Phase 2

## Explicitly Out of Scope
No execution clients, no keys, no live mode, no on-chain, no NO-token strategy analysis, no paper trading, no simulated orders.

## Source arb-bot Components Inspected
Phase 1: probability.rs, settlement_predictor.rs, empirical_model.py, CORE_ENGINE_SOURCE_OF_TRUTH.md, bot-report.md.

## Source arb-bot Components Ported
Phase 1: Pure Python fair probability, settlement predictor port, empirical model shell.

## Source arb-bot Components Intentionally Not Ported
engine/router/orders/risk/fees/state/wire/events/old TOML config/live execution plumbing.

## Approved arb-bot Fixture Script Status
Phase 1: Probability fixture uses Rust binary_call_probability_py. Settlement fixtures generated through existing arb-bot PySignalEngine. No Rust source or live bot paths modified.

## Nautilus Components Reused
PolymarketDataLoader.from_market_slug(sanitize_info=True), PolymarketFeeModel, BinaryOption metadata, Gamma API for market discovery.

## Historical Polymarket Market Selected (Phase 1)
btc-updown-15m-1767107700 (resolved BTC 15m UpDown Dec 30 2025).

## Phase 2 Branch
Created from Phase 1 HEAD 1edc29c5c.

## Phase 2 Scope
Observer-only live data validation. Public Polymarket CLOB + Binance REST only. No orders. No keys. No execution client.

## Phase 2 Public Data Sources
Binance: https://api.binance.com/api/v3/aggTrades (public), https://api.binance.com/api/v3/ticker/bookTicker (public).
Polymarket: https://clob.polymarket.com/book (public), https://clob.polymarket.com/trades (public).
Gamma API: https://gamma-api.polymarket.com/markets (public).

## Phase 2 Market Discovery
discover_btc_15m_updown() queries Gamma API for active BTC 15m UpDown markets.

## Phase 2 900s Capture Result

### Run ID: 20260513T220101Z
### Selected market slug: btc-updown-15m-1778794200
### Market: "Bitcoin Up or Down - May 14, 5:30PM-5:45PM ET"
### Capture directory: data/polymarket_btcusd_arb/live_observer/20260513T220101Z/
### Report directory: reports/polymarket_btcusd_arb/live_observer/20260513T220101Z/
### Capture start: ~2026-05-13T22:01Z
### Capture end: ~2026-05-13T22:16Z
### Capture duration: 903.16 seconds

### Polymarket event/poll count: 164 polls
### Binance event/poll count: 164 polls (aggTrade snapshots)
### Evaluated events: 145
### Candidate count: 0
### Rejection count: 164
### Rejection counts by reason:
  - spread_too_wide: 145
  - stale_or_missing_binance: 19

### Stale rates:
  - Binance stale rate: 0.0000
  - Polymarket stale rate: 0.0000

### Replay result: deterministic=True (0 candidates = 0 candidates)
### Safety check: passed (no execution imports, no orders, no keys)
### Phase 2 verdict: NEEDS_MORE_DATA (0 candidates, all events rejected for spread or missing binance)

## Phase 2 Rejection Tracking Fix
Initial 60s capture had rejection_counts={} and evaluated_event_count not tracked. Fixed to properly emit DivergenceSignal rejection records for:
- edge_below_threshold (per threshold evaluated)
- stale_or_missing_binance
- too_close_to_expiry
- market_expired
- spread_too_wide
observer_summary.json includes rejection_counts and evaluated_event_count. Report includes rejection reason table.

## Phase 2 Verdict
NEEDS_MORE_DATA. Not a win. The 900s live observation of an active BTC 15m UpDown market found 0 candidates. All 145 evaluated events were rejected: 145 for spread_too_wide, 19 for stale_or_missing_binance. This means the live spread on Polymarket's YES token was consistently too wide relative to the fair probability signal. The signal does not survive live spread conditions in this observation window.

## Phase 2 Limitations
REST polling at 5s intervals, not real-time WebSocket. Binance bookTicker is point-in-time snapshot. Polymarket CLOB order book polled at 5s. Price-to-beat approximated from Binance current best bid. Observation was on one market for one 15-minute window. The spread_too_wide rejection indicates Polymarket live spread economics differ from historical backtest assumptions.

## Phase 1 Backtest Runs
`.venv/bin/python -m examples.strategies.polymarket_btcusd_arb.run_backtest --market-slug btc-updown-15m-1767107700 --threshold-grid 5,10,20,40`
Last Phase 1 report: /mnt/nasirjones/py/nautilus_trader/reports/polymarket_btcusd_arb/20260513T212019Z

## Polymarket Cache Status (Phase 1)
Cached; event_count=702; sanitize_info=True; no pagination/truncation warning; YES/Up + NO/Down tokens present; v1 used YES/Up only.

## Binance Data Source (Phase 1)
Binance Vision aggTrades (historical). Phase 2: Binance live REST.

## Fee Assumption
Maker-first; crypto maker rebates enabled; maker-fee survival gate reported.

## Timestamp Convention
All internal timestamps are integer nanoseconds; CLI dates are UTC.

## Look-ahead Protection
sanitize_info=True required; settlement payoff unavailable before expiry-crossing horizons.

## Assumptions
YES/UP token only in v1 and v2. Phase 2 Binance data is live trade proxy. Phase 1 Binance data was historical aggTrade proxy. UpDown price-to-beat approximated from Binance current best bid for live observation.

## Deviations From Prompt
Phase 2 live observation uses REST poll for Binance aggTrades and Polymarket CLOB orderbook, not WebSocket streaming. Both are public endpoints. Polling interval is 5 seconds. The spread_too_wide rejection uses config.max_spread_bps default of 200 bps.

## Blockers
No blocker.

## Tests Run
`.venv/bin/python -m pytest examples/strategies/polymarket_btcusd_arb/tests examples/strategies/polymarket_btcusd_arb/parity_tests -q` -> 47 passed (29 Phase 1 + 18 Phase 2).

## Current Verdict
Phase 1: 20 CANDIDATE_FOR_LONGER_OBSERVATION (backtest on resolved market, may suffer from spread assumptions).
Phase 2: NEEDS_MORE_DATA (live observation on active market, 0 candidates, all rejected for spread or missing Binance).
Phase 2 verdict is NOT a win. The signal did not survive live spread conditions in this observation window.

## Known Limitations
1. Phase 1 used historical aggTrade data; live spread may differ materially.
2. Phase 2 15-minute observation on one market is insufficient for definitive conclusions.
3. The spread_too_wide filter used 200 bps default; Polymarket live spreads may be structurally wider than the backtest assumed.
4. Binance cheapest-price proxy may not match the Chainlink oracle that Polymarket actually uses for settlement.
5. NO-side economics not analyzed.
6. Phase 2 used REST polling, not WebSocket.

## Next Recommendation
Do not recommend Phase 3 (execution) based on current evidence. The Phase 2 live observation showed 0 candidates due to spread economics. Before Phase 3, conduct longer and broader live observations (multiple markets, different times of day, different volatility regimes) to determine whether the spread issue is structural or episodic. If longer observation still produces 0 candidates, the hypothesis is REJECTED under live conditions regardless of the backtest result.