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
Nautilus working tree status after implementation: see git status
arb-bot working tree: no changes in Phase 2; Phase 2 does not touch arb-bot

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
discover_btc_15m_updown() queries Gamma API for active BTC 15m UpDown markets. Found 2 active at time of testing: btc-updown-15m-1778793300 and btc-updown-15m-1778792400.

## Phase 2 Capture Runs

### Short 60s test capture (v1, before rejection tracking fix)
Run ID: 20260513T213829Z. Market: btc-updown-15m-1778793300. 0 candidates, 0 rejections tracked (pre-fix), deterministic replay.

### 900s full capture (v2, with rejection tracking)
Started after fixing rejection tracking. Pending.

## Phase 2 Replay Checks
Short test: deterministic=True. Full 900s: pending.

## Phase 2 Safety Checks
AST scan passes for entire package including Phase 2 modules. No execution imports, no OrderFactory, no submit_order, no private keys.

## Phase 2 Rejection Tracking Fix
The initial 60s capture had `rejection_counts: {}` and `evaluated_event_count` not tracked. Fixed to properly emit DivergenceSignal rejection records for:
- edge_below_threshold (per threshold evaluated)
- stale_or_missing_binance
- too_close_to_expiry
- market_expired
- spread_too_wide

Now tracks `evaluated_event_count` explicitly. `observer_summary.json` includes `rejection_counts` and `evaluated_event_count`. Report includes rejection reason table.

## Phase 2 Verdict
Pending 900s capture completion.

## Phase 2 Limitations
REST polling at 5s intervals, not real-time WebSocket. Binance bookTicker is point-in-time snapshot. Polymarket CLOB order book polled at 5s. Price-to-beat approximated from Binance current best bid. Staleness checks use max_binance_staleness_ns=2s and max_polymarket_staleness_ns=2s from Phase 1 config.

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
YES/UP token only in v1 and v2. Binance data is trade proxy, not full book. UpDown price-to-beat approximated from Binance current best bid for live observation.

## Deviations From Prompt
Phase 2 live observation uses REST poll for Binance aggTrades and Polymarket CLOB orderbook, not WebSocket streaming. Both are public endpoints. Polling interval is 5 seconds.

## Blockers
No blocker. 900s capture in progress.

## Tests Run
`.venv/bin/python -m pytest examples/strategies/polymarket_btcusd_arb/tests examples/strategies/polymarket_btcusd_arb/parity_tests -q` -> 47 passed (29 Phase 1 + 18 Phase 2).

## Phase 1 Backtest Runs
`.venv/bin/python -m examples.strategies.polymarket_btcusd_arb.run_backtest --market-slug btc-updown-15m-1767107700 --threshold-grid 5,10,20,40`
Last report: /mnt/nasirjones/py/nautilus_trader/reports/polymarket_btcusd_arb/20260513T212019Z

## Current Verdict (Phase 1)
Verdicts by grid: 20 CANDIDATE_FOR_LONGER_OBSERVATION, 60 NEEDS_MORE_DATA. NEEDS_MORE_DATA is not a win.

## Known Limitations
Binance v1 uses aggTrades/trade-derived proxy; NO-side economics not analyzed; UpDown price-to-beat approximated from Binance instead of Chainlink; Phase 1 evidence from one resolved 15m market; Phase 2 uses REST polling not WebSocket; 60s test showed 0 candidates which is expected for an off-peak short window.

## Next Recommendation
Do not recommend Phase 3 unless Phase 2 observer data shows live signal survival. Do not recommend execution. Do not call NEEDS_MORE_DATA a win.