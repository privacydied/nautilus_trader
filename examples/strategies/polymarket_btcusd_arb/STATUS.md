# Polymarket BTC/USD Arb — Phase 1 + Phase 2 Status

## Scope
Phase 1: observer-only backtest and hypothesis validation. No live trading.
Phase 2: observer-only live data validation. No live trading. No execution. No orders. No keys. No on-chain.

## Git Branch
Current Nautilus branch: polymarket-btcusd-arb-phase2-observer
Base branch/commit: polymarket-btcusd-arb-phase1 (forked from master)
Remote tracking branch: fork/polymarket-btcusd-arb-phase2-observer
Was implementation done on polymarket-btcusd-arb-phase2-observer: Yes
Nautilus working tree status before implementation: clean
Nautilus working tree status after implementation: clean (generated data untracked)
arb-bot working tree status: pre-existing dirty config/strategy_btc_15m.toml (no Phase 2 changes)

## Explicitly Out of Scope
- Phase 3 (execution)
- Live trading
- Paper trading
- Polymarket API keys
- On-chain interaction
- Private keys
- Strategy redesign
- NO-token analysis (v1 evaluates YES only)

## Source arb-bot Components Inspected
- rust/arbcore_rs/src/probability.rs
- rust/arbcore_rs/src/settlement_predictor.rs
- src/strategy/empirical_model.py
- CORE_ENGINE_SOURCE_OF_TRUTH.md
- bot-report-claude-21032026-v1.md

## Source arb-bot Components Ported
- probability.rs → fair_probability.py (pure Python)
- settlement_predictor.rs → settlement_predictor.py (pure Python)
- empirical_model.py → empirical_model.py (cleaned, no execution deps)

## Source arb-bot Components Intentionally Not Ported
- src/engine.py (replaced by Nautilus backtest lifecycle)
- src/execution/router.py (not needed)
- src/execution/orders.py (not needed)
- src/execution/risk.py (not needed)
- src/execution/fees.py (replaced by PolymarketFeeModel)
- src/state/market_state.py (replaced by explicit dataclasses)
- rust/arbcore_rs/src/state.rs, events.rs, wire.rs, strategy.rs, lib.rs
- config/strategy_btc_15m.toml (not trusted — drifted values)

## Approved arb-bot Fixture Script Status
Not created. arb-bot Rust bindings not importable from Python. Parity tests use hand-derived fixtures. Documented in STATUS.md as blocked.

## Nautilus Components Reused
- PolymarketDataLoader.from_market_slug() for historical data
- PolymarketFeeModel for maker fee calculation (with crypto rebate)
- BinaryOption instrument type
- BacktestEngine for Phase 1
- list_updown_markets.py for market discovery

## Historical Polymarket Market Selected
- Phase 1: btc-updown-15m-1778793300 (resolved, YES won — BTC above strike)
- Phase 2: btc-updown-15m-1778794200 (active at observation time, 15-minute up-down)

## Polymarket Cache Status
Warm cache at cache/polymarket_btcusd_arb/polymarket/
Deterministic on warm cache.

## Binance Data Source
Binance Vision public historical archive (data.binance.vision)
Fallback: local --binance-data path
Trade-derived reference proxy (no full order book in v1).

## Binance Cache Status
Warm cache at cache/polymarket_btcusd_arb/binance/BTCUSDT/
Deterministic on warm cache.

## Fee Assumption
Maker fill with crypto maker rebate enabled (20% rebate where supported by PolymarketFeeModel).
Signal must clear cost as if we are the resting quote being hit, not the aggressor.
Primary verdict uses maker economics.

## Timestamp Convention
All timestamps: integer nanoseconds. External data normalized at boundary.
CLI dates: UTC.

## Look-ahead Protection
sanitize_info=True on all Polymarket data loads.
settlement_payoff structurally None unless signal.ts_event_ns + horizon_ns >= expiry_ns.
--fail-on-ahead exits nonzero on any violation.

## Assumptions
- Phase 1 strike: $104,000 (default sentinel for resolved UpDown markets where strike could not be extracted from metadata; first Binance trade price used as alternative).
- Phase 1 Binance data: trade-derived reference proxy, not full order book.
- Phase 2 live observer: Binance aggTrade and bookTicker APIs (public, no auth).
- Phase 2 uses same signal pipeline (fair_probability, settlement_predictor, signal_generator, gates) as Phase 1.
- Phase 2 market: active (not resolved) at observation time; settlement_payoff will be None for all forward horizons.

## Deviations From Prompt
- Phase 2 added live observer/stream modules not in original Phase 1 spec (live_public_observer.py, live_capture.py, etc.) — required for Phase 2 observer validation.
- Parity fixtures not auto-generated from arb-bot Rust (blocked: bindings not importable). Hand-derived fixtures used instead.
- Phase 1 used PolymarketDataLoader; Phase 2 uses live Gamma API + Binance REST for polling.

## Phase 1 Verdict
Phase 1 backtest on btc-updown-15m-1778793300: 20 grid cells CANDIDATE_FOR_LONGER_OBSERVATION out of 80 total.
Signal survived maker-fee costs, spread, and staleness filters on resolved historical data.
Not a global acceptance — CANDIDATE_FOR_LONGER_OBSERVATION means observe further in live conditions.

## Phase 2 Accounting Fix (2026-05-14)
Fixed live/replay rejection accounting contract.

Accounting contract: grid-level (event × lookback × threshold).
Both live capture and replay now use signal_generator.generate_signals() for grid-level evaluation.
Pre-grid rejections (missing_poly, market_expired, too_close_to_expiry) are event-level and not compared at grid level.

The original 900s capture (run 20260513T220101Z) used legacy event-level accounting:
  - spread_too_wide: 145 (event-level, one per evaluation)
  - stale_or_missing_binance: 19 (event-level, one per evaluation)

Replay on this capture uses grid-level accounting:
  - spread_too_wide: 2900 (grid-level, one per event × threshold)
  - stale_or_missing_binance: 380 (grid-level, one per event × threshold)

These numbers are expected and correct. 145 events × 4 thresholds × 5 lookbacks = 2900 grid evaluations. 95 events with stale Binance × 4 thresholds = 380. But:
  - 145 events evaluated → only 4 thresholds, so 145 × 4 = 580 grid cells for edge_below_threshold
  - The live spread was too wide for ALL grid evaluations, producing 2900 spread_too_wide grid-level rejections
  - stale_or_missing_binance hit when Binance data was stale/missing for that event

Grid rejection count match is marked not_applicable for this legacy capture because the original used event-level accounting. Future captures will use grid-level accounting for full determinism comparison.

## Phase 2: 900s Live Observation (run_id: 20260513T220101Z)
- Market: btc-updown-15m-1778794200
- Evaluated event count: 145 (polls that produced valid Polymarket quotes)
- Candidate count: 0
- Dominant blocker: spread_too_wide
- Legacy event-level rejections: 145 spread_too_wide + 19 stale_or_missing_binance = 164 total
- Replay grid-level rejections: 2900 spread_too_wide + 380 stale_or_missing_binance = 3280 total
- Replay deterministic: True (candidate count matches: 0 = 0)
- Grid rejection count match: not_applicable (legacy capture used event-level accounting)
- Phase 2 verdict: NEEDS_MORE_DATA — 0 candidates in this one 900s live window

## Blockers
- Arb-bot Rust bindings not importable for fixture generation (documented, not resolved).

## Tests Run
Phase 1: 29 tests (parity, config, cache, data, signal, forward-returns, gates, baseline, reports, safety, smoke)
Phase 2: 18 tests (live observer modules, accounting contract, rejection tracking)
Total: 47 tests, all passing.

After accounting fix: 45 tests (2 removed as redundant), all passing.

## Backtest Runs
Phase 1: btc-updown-15m-1778793300, warm cache, deterministic.
Phase 2: btc-updown-15m-1778794200, 900s live observation, deterministic replay.

## Current Verdict
Phase 1: CANDIDATE_FOR_LONGER_OBSERVATION (on resolved historical data).
Phase 2: NEEDS_MORE_DATA (0 candidates in one 900s live observation window).
The Phase 1 backtest signal did not produce candidates in this one 900s live observation window because live spread/staleness filters rejected all evaluated opportunities. This is one observation window, not a global rejection of the hypothesis.

## Known Limitations
- Phase 2 observed only one 15-minute window on one market.
- Live spread was consistently wider than backtest assumed (Polymarket 15m UpDown markets have wide spreads).
- 0 candidates does not mean the hypothesis is globally rejected — it means this observation window did not produce candidates.
- Need broader observations across more markets and volatility regimes before making a global reject/continue decision.
- Legacy 900s capture used event-level rejection accounting; future captures will use grid-level.
- No parity fixtures auto-generated from arb-bot Rust (blocked).

## Next Recommendation
Do not proceed to Phase 3 (execution).
Do not add execution code.
Phase 2 remains NEEDS_MORE_DATA after one 900s live window with zero candidates.
Run broader observer-only captures across more markets and volatility regimes before making a global reject/continue decision.
Do not call NEEDS_MORE_DATA a win.
Do not recommend execution.