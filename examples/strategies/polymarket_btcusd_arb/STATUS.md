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
Not created. arb-bot Rust bindings not importable from Python. Parity tests use hand-derived fixtures. Documented as blocked.

## Nautilus Components Reused
- PolymarketDataLoader.from_market_slug() for historical data
- PolymarketFeeModel for maker fee calculation (with crypto rebate)
- BinaryOption instrument type
- BacktestEngine for Phase 1
- list_updown_markets.py for market discovery

## Historical Polymarket Market Selected
- Phase 1: btc-updown-15m-1778793300 (resolved, YES won — BTC above strike)
- Phase 2 run 1: btc-updown-15m-1778794200 (legacy event-level accounting capture)
- Phase 2 run 2 (post-fix): btc-updown-15m-1778796900 (grid-level accounting capture)

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
- Phase 1 strike: $104,000 (default sentinel for resolved UpDown markets).
- Phase 1 Binance data: trade-derived reference proxy, not full order book.
- Phase 2 live observer: Binance aggTrade and bookTicker APIs (public, no auth).
- Phase 2 uses same signal pipeline (fair_probability, settlement_predictor, signal_generator, gates) as Phase 1.
- Phase 2 market: active (not resolved) at observation time; strike approximated from Binance bookTicker ($79,263.31).

## Deviations From Prompt
- Phase 2 added live observer/stream modules not in original Phase 1 spec (live_public_observer.py, live_capture.py, etc.) — required for Phase 2 observer validation.
- Parity fixtures not auto-generated from arb-bot Rust (blocked: bindings not importable). Hand-derived fixtures used instead.
- Phase 1 used PolymarketDataLoader; Phase 2 uses live Gamma API + Binance REST for polling.

## Phase 2 Accounting Fix (2026-05-14)
Fixed live/replay rejection accounting contract.

Accounting contract: grid-level (event × lookback × threshold).
Both live capture and replay now use signal_generator.generate_signals() for grid-level evaluation.
Pre-grid rejections (missing_poly, market_expired, too_close_to_expiry) are event-level only and tracked separately.

### Legacy capture (run 20260513T220101Z)
Used event-level rejection accounting:
- spread_too_wide: 145 (event-level)
- stale_or_missing_binance: 19 (event-level)
- grid_rejection_count_match: not_applicable (different accounting contract from replay)

### Post-fix capture (run 20260513T225119Z) — GRID-LEVEL ACCOUNTING VALIDATED
Used grid-level accounting:
- 164 evaluated events × 5 lookbacks × 4 thresholds = 3,280 grid evaluations
- spread_too_wide: 3,060 (grid-level)
- stale_or_missing_binance: 220 (grid-level)
- Replay grid rejection count match: True (3,280 = 3,280)
- Replay rejection counts by reason match: True (spread_too_wide=3060, stale_or_missing_binance=220)
- Replay candidate count match: True (0 = 0)
- Deterministic: True

## Phase 2: Post-Fix Validation Capture (run_id: 20260513T225119Z)
- Market: btc-updown-15m-1778796900
- Strike: $79,263.31 (approximated from Binance bookTicker)
- Capture duration: 902.36 seconds (~15 minutes)
- Accounting contract: grid-level: event × lookback × threshold
- Evaluated event count: 164
- Grid evaluation count: 3,280 (164 events × 5 lookbacks × 4 thresholds)
- Pre-grid rejection count: 0
- Grid candidate count: 0
- Grid rejection count: 3,280
  - spread_too_wide: 3,060
  - stale_or_missing_binance: 220
- Candidate count: 0
- Replay candidate count match: True (0 = 0)
- Replay grid rejection count match: True (3,280 = 3,280)
- Replay rejection counts by reason match: True
  - spread_too_wide: 3,060 replay = 3,060 original
  - stale_or_missing_binance: 220 replay = 220 original
- Replay deterministic: True
- Dominant blocker: spread_too_wide (93.3% of grid-level rejections)
- Phase 2 verdict: NEEDS_MORE_DATA — 0 candidates in this one 900s live window

## Blockers
- Arb-bot Rust bindings not importable for fixture generation (documented, not resolved).

## Tests Run
Phase 1: 29 tests (parity, config, cache, data, signal, forward-returns, gates, baseline, reports, safety, smoke)
Phase 2: 16 tests (live observer modules, accounting contract, rejection tracking)
Total: 45 tests, all passing.

## Backtest Runs
Phase 1: btc-updown-15m-1778793300, warm cache, deterministic.
Phase 2 run 1: btc-updown-15m-1778794200, 900s live observation, legacy event-level accounting, deterministic candidate match, rejection match not_applicable.
Phase 2 run 2: btc-updown-15m-1778796900, 900s live observation, grid-level accounting, deterministic candidate AND rejection match.

## Current Verdict
Phase 1: CANDIDATE_FOR_LONGER_OBSERVATION (on resolved historical data).
Phase 2: NEEDS_MORE_DATA (0 candidates in two 900s live observation windows).
The Phase 1 backtest signal did not produce candidates in live observation because live Polymarket 15m UpDown spreads are consistently wider than the backtest assumed. This is one observation across two 900s windows, not a global rejection of the hypothesis.

## Known Limitations
- Phase 2 observed only two 15-minute windows on one market type.
- Live Polymarket 15m UpDown market spread was consistently wider than backtest assumed (dominant blocker: spread_too_wide).
- 0 candidates does not mean the hypothesis is globally rejected — it means these observation windows did not produce candidates.
- Need broader observations across more markets and volatility regimes before making a global reject/continue decision.
- No parity fixtures auto-generated from arb-bot Rust (blocked).
- Phase 2 uses approximated strike from Binance bookTicker, not exact strike from resolved market metadata.

## Next Recommendation
Do not proceed to Phase 3 (execution).
Do not add execution code.
Phase 2 remains NEEDS_MORE_DATA after two 900s live windows with zero candidates.
Run broader observer-only captures across more markets, longer windows, and different volatility regimes before making a global reject/continue decision.
Do not call NEEDS_MORE_DATA a win.
Do not recommend execution.
Grid-level accounting determinism is now validated: candidate counts and rejection counts match exactly between live capture and replay.