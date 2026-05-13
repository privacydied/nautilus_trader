# Polymarket BTC/USD Arb — Phase 1 Status

## Scope
Phase 1 observer-only backtest and hypothesis validation. No live trading.

## Git Branch
Current Nautilus branch: polymarket-btcusd-arb-phase1
Base branch/commit: origin/develop / ace1612eb0917a3b691bb0c22dd82788db953ee2
Remote tracking branch: origin/develop
Was implementation done on polymarket-btcusd-arb-phase1: True
Nautilus working tree status before implementation: clean before branch creation
Nautilus working tree status after implementation: see git status
arb-bot working tree status before fixture generation: dirty pre-existing config/strategy_btc_15m.toml observed by read-only inspection
arb-bot working tree status after fixture generation: approved script only if present

## Explicitly Out of Scope
No execution clients, no keys, no live mode, no on-chain, no NO-token strategy analysis.

## Source arb-bot Components Inspected
probability.rs, settlement_predictor.rs, empirical_model.py, CORE_ENGINE_SOURCE_OF_TRUTH.md, docs/bot-report-claude-21032026-v1.md.

## Source arb-bot Components Ported
Pure Python fair probability, settlement predictor shell, empirical model shell.

## Source arb-bot Components Intentionally Not Ported
engine/router/orders/risk/fees/state/wire/events/old TOML config/live execution plumbing.

## Approved arb-bot Fixture Script Status
Probability Rust binding exists. Direct settlement predictor binding was not exposed; documented as blocker.

## Nautilus Components Reused
PolymarketDataLoader.from_market_slug(sanitize_info=True), PolymarketFeeModel availability, BinaryOption metadata.

## Historical Polymarket Market Selected
bitcoin-above-70000-on-april-5

## Polymarket Cache Status
cache; event_count=116

## Binance Data Source
Binance Vision aggTrades unless --binance-data supplied; v1 trade proxy, not book.

## Binance Cache Status
cache; state_count=16672

## Fee Assumption
Maker-first; crypto maker rebates enabled; maker-fee survival gate reported.

## Timestamp Convention
All internal timestamps are integer nanoseconds; CLI dates are UTC.

## Look-ahead Protection
sanitize_info=True required; settlement payoff unavailable before expiry-crossing horizons.

## Assumptions
YES token only in v1. Binance aggTrades are trade-derived reference proxy.

## Deviations From Prompt
Settlement parity fixtures blocked by no direct PySettlementPredictor export. Venue observer files were only available in git history.

## Blockers
Direct standalone Rust settlement predictor binding unavailable.

## Tests Run
Passed: `.venv/bin/python -m pytest examples/strategies/polymarket_btcusd_arb/tests -q` -> 20 passed.
Passed with documented blocker: `.venv/bin/python -m pytest examples/strategies/polymarket_btcusd_arb/parity_tests -q` -> 1 passed, 1 xfailed (direct Rust settlement predictor binding unavailable).

## Backtest Runs
Passed: `.venv/bin/python -m examples.strategies.polymarket_btcusd_arb.run_backtest --market-slug bitcoin-above-70000-on-april-5 --threshold-grid 5,10,20,40`.
Last run report: /mnt/nasirjones/py/nautilus_trader/reports/polymarket_btcusd_arb/20260513T204042Z

## Current Verdict
See summary.json verdicts_by_grid. NEEDS_MORE_DATA is not a win.

## Known Limitations
Binance v1 uses aggTrades/trade-derived proxy, not full book; NO-side economics not analyzed in v1; Direct settlement predictor Rust fixture binding unavailable

## Next Recommendation
Do not proceed to Phase 2 unless a grid cell is CANDIDATE_FOR_LONGER_OBSERVATION.
