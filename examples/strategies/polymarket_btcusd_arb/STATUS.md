# Polymarket BTC/USD Arb — Phase 1 Status

## Scope
Phase 1 observer-only backtest and hypothesis validation. No live trading.

## Git Branch
Current Nautilus branch: polymarket-btcusd-arb-phase1
Base branch/commit: origin/develop / ace1612eb0917a3b691bb0c22dd82788db953ee2
Remote tracking branch: fork/polymarket-btcusd-arb-phase1
Was implementation done on polymarket-btcusd-arb-phase1: True
Nautilus working tree status before implementation: clean before branch creation; continuation began with only generated cache/ and reports/ untracked
Nautilus working tree status after implementation: see git status
arb-bot working tree status before fixture generation: dirty pre-existing config/strategy_btc_15m.toml observed by read-only inspection
arb-bot working tree status after fixture generation: approved script only; config/strategy_btc_15m.toml remains pre-existing dirty user work

## Explicitly Out of Scope
No execution clients, no keys, no live mode, no on-chain, no NO-token strategy analysis.

## Source arb-bot Components Inspected
probability.rs, settlement_predictor.rs, empirical_model.py, CORE_ENGINE_SOURCE_OF_TRUTH.md, docs/bot-report-claude-21032026-v1.md.

## Source arb-bot Components Ported
Pure Python fair probability, settlement predictor port, empirical model shell.

## Source arb-bot Components Intentionally Not Ported
engine/router/orders/risk/fees/state/wire/events/old TOML config/live execution plumbing.

## Approved arb-bot Fixture Script Status
Probability fixture uses Rust binary_call_probability_py. Settlement fixtures are generated through existing arb-bot PySignalEngine, which internally owns the Rust SettlementPredictor; no Rust source or live bot paths are modified.

## Nautilus Components Reused
PolymarketDataLoader.from_market_slug(sanitize_info=True), PolymarketFeeModel availability, BinaryOption metadata.

## Historical Polymarket Market Selected
btc-updown-15m-1767107700. Candidate slug was accepted by PolymarketDataLoader.from_market_slug and confirmed through Gamma events API as title "Bitcoin Up or Down - December 30, 10:15AM-10:30AM ET", series "BTC Up or Down 15m", closed/resolved, Chainlink BTC/USD resolution source.

## Polymarket Cache Status
cache; event_count=702; sanitize_info=True; no Polymarket pagination/truncation warning observed. YES/Up and NO/Down token IDs were both present; v1 used YES/Up only.

## Binance Data Source
Binance Vision aggTrades unless --binance-data supplied; v1 trade proxy, not book. Binance timestamp normalization now handles microsecond aggTrade timestamps.

## Binance Cache Status
network on latest exact run because the previously cached empty day was invalidated and refreshed; state_count=21640. Subsequent warm-cache runs use canonical row-content hash validation.

## Fee Assumption
Maker-first; crypto maker rebates enabled; maker-fee survival gate reported.

## Timestamp Convention
All internal timestamps are integer nanoseconds; CLI dates are UTC. UpDown slug start timestamp 1767107700 resolved to 2025-12-30T15:15:00Z; expiry/end is 2025-12-30T15:30:00Z.

## Look-ahead Protection
sanitize_info=True required; settlement payoff unavailable before expiry-crossing horizons. Resolved winner metadata is not passed to signal generation.

## Assumptions
YES token only in v1. Binance aggTrades are trade-derived reference proxy, not Chainlink. UpDown price-to-beat was approximated from the first Binance BTCUSDT aggTrade at/after the 15:15:00Z market start: 88349.07.

## Deviations From Prompt
The active-market discovery script still only lists active UpDown markets; it did not discover this resolved historical slug. Direct slug mode works and was used. Venue observer files were only available in git history.

## Blockers
No remaining blocker for the direct-slug Phase 1 run. Scientific limitation remains: the price-to-beat is approximated from Binance aggTrades because Chainlink stream history was not added in Phase 1.

## Tests Run
`.venv/bin/python -m pytest examples/strategies/polymarket_btcusd_arb/tests examples/strategies/polymarket_btcusd_arb/parity_tests -q` -> 29 passed. Plain `python -m pytest ...` fails in the non-venv interpreter because msgspec is unavailable; repo-local `.venv/bin/python` was used because `uv run` is blocked by uv version pin mismatch (`required ==0.11.12`, installed `0.11.14`).

## Backtest Runs
Latest exact required command: `.venv/bin/python -m examples.strategies.polymarket_btcusd_arb.run_backtest --market-slug btc-updown-15m-1767107700 --threshold-grid 5,10,20,40`.
Last run report: /mnt/nasirjones/py/nautilus_trader/reports/polymarket_btcusd_arb/20260513T212019Z
Polymarket rows: 702. Binance rows: 21640. Candidate events: 65. Zero-candidate grid cells: 60.

## Current Verdict
Verdicts by grid: 20 CANDIDATE_FOR_LONGER_OBSERVATION, 60 NEEDS_MORE_DATA. NEEDS_MORE_DATA is not a win. Candidate verdicts are concentrated in the 60s-180s TTE bucket and require longer observer-only validation before any execution work.

## Known Limitations
Binance v1 uses aggTrades/trade-derived proxy, not full book; NO-side economics not analyzed in v1; UpDown price-to-beat is approximated from Binance instead of Chainlink; candidate evidence comes from one resolved 15m market only.

## Next Recommendation
Phase 1 produced CANDIDATE_FOR_LONGER_OBSERVATION grid cells, so the next phase may be considered only as observer-only validation. Do not implement execution, keys, orders, on-chain paths, or NO-token strategy analysis from this result.
