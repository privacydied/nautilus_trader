# Polymarket BTC 15m UpDown Arb — Final Research Status

## Final Decision

```
ARCHIVED_REJECTED_FOR_CURRENT_LIVE_CONDITIONS
```

The hypothesis is not globally mathematically disproven.
The current BTC 15m UpDown version is rejected under observed live Polymarket spread/staleness conditions.
No Phase 3 Rust hotpath work is justified.
No paper execution is justified.
No live trading is justified.
The scaffold remains useful as an observer/research lab.

## What Was Tested

- **Hypothesis**: The fair-probability model from arb-bot's Rust core identifies real Polymarket BTC 15-minute UpDown mispricing that survives maker economics after fees, spread, staleness, latency, and settlement buffers.
- **Thresholds tested**: 5, 10, 20, 40 bps
- **Lookbacks tested**: 1s, 5s, 15s, 30s, 60s (nanosecond equivalents)
- **Time-to-expiry buckets**: Multiple
- **Mode**: Observer-only. Maker economics. YES/UP token only.
- **Execution**: None. Zero orders.

## What Was Not Tested

- NO token economics (v1 evaluates YES/UP only)
- NO different thresholds (80, 100, 200 bps)
- NO different market durations (1h, 4h)
- NO different volatility regimes
- NO different reference sources (Chainlink, full order book)
- NO taker economics as primary verdict
- NO execution simulation
- NO live or paper trading

## Branches Used

- `polymarket-btcusd-arb-phase1` — Phase 1 backtest, parity port, hypothesis validation
- `polymarket-btcusd-arb-phase2-observer` — Phase 2 live observer
- `polymarket-btcusd-arb-phase2b-observer-campaign` — Phase 2B campaign runner
- `polymarket-btcusd-arb-phase2c-observer-analysis` — Phase 2C evidence review and gate decision

## Key Commits

All commits on branch `polymarket-btcusd-arb-phase2c-observer-analysis` and its ancestors.

## Phase 1 Backtest Evidence

Initial scaffold/backtest path was first exercised on `bitcoin-above-70000-on-april-5`, a resolved BTC binary fixture market (5 runs, 0 candidates each).

The accepted Phase 1 direct-slug BTC 15m UpDown validation market was:
`btc-updown-15m-1767107700`

Phase 1 direct-slug result:
- Resolved BTC 15m UpDown market
- 65 candidates
- 20 grid cells with CANDIDATE_FOR_LONGER_OBSERVATION
- 60 grid cells with NEEDS_MORE_DATA
- Limitations: one resolved market, Binance aggTrade proxy, Binance-derived price-to-beat approximation, not Chainlink historical stream replay

Parity with Rust fixtures was confirmed within 1e-9 tolerance.

## Phase 2 Live Observer Evidence

Two direct 900s observer windows were executed against live Polymarket markets. Both produced zero candidates. Grid rejections were dominated by `spread_too_wide` (84–93%) and `stale_or_missing_binance` (7–16%). Replay determinism was confirmed for all windows.

## Phase 2B Campaign Evidence

Four additional 900s campaign windows were executed across four distinct BTC 15m UpDown market periods. All produced zero candidates. Grid rejection profiles were consistent with Phase 2: `spread_too_wide` dominant, `stale_or_missing_binance` secondary.

## Phase 2C Evidence Review

Aggregated evidence from Phase 1, Phase 2, and Phase 2B. Evidence review produced:

```
Gate Decision: REJECTED_FOR_CURRENT_LIVE_CONDITIONS
```

## Valid Live Window Count

10 valid live observation windows (6 Phase 2 direct observer + 4 Phase 2B campaign)
with grid_rejection_count > 0, after filtering out failed/incomplete/test runs.
Total artifact rows discovered: 36 (including Phase 1 backtest runs, failed dry-discover attempts, and test runs excluded from gate decisions).

## Distinct Market Count

4 distinct BTC 15m UpDown market periods identified from campaign data:
- `btc-updown-15m-1778801400`
- `btc-updown-15m-1778802300`
- `btc-updown-15m-1778803200`
- `btc-updown-15m-1778805000`

Phase 2 direct observer runs did not record market slugs ("unknown"), but grid rejection counts and time windows are consistent with overlap with campaign data.

## Candidate Counts

Total live candidates across all windows: **0**

## Grid Rejection Counts

| Window | Market | Grid Rejections | spread_too_wide | stale_or_missing_binance |
|--------|--------|----------------|-----------------|--------------------------|
| Phase 2 #1 | unknown | 3280 | ~93.3% | ~6.7% |
| Phase 2 #2 | unknown | 3160 | ~88.6% | ~11.4% |
| 2B #1 | btc-updown-15m-1778801400 | 3140 | 84.1% | 15.9% |
| 2B #2 | btc-updown-15m-1778802300 | 3160 | 72.2% | 27.8% |
| 2B #3 | btc-updown-15m-1778803200 | 3140 | 80.3% | 19.7% |
| 2B #4 | btc-updown-15m-1778805000 | 3160 | 86.7% | 13.3% |

Additional windows from duplicate observation angles show consistent profiles.

## Rejection Counts By Reason

Across all valid live windows:
- `spread_too_wide`: dominant blocker (72–93% per window)
- `stale_or_missing_binance`: secondary blocker (7–28% per window)
- No other rejection reasons were observed at significant levels

## Replay Determinism

All windows with grid_rejection_count > 0 have `replay_deterministic = True`.
Confidence: deterministic replay at grid level confirmed.

## Parity Status

- Probability parity with Rust fixtures: **TRUE** (within 1e-9)
- Settlement predictor parity: **PASSING** on 2 synthetic cases
- Empirical model port: deterministic on fixture inputs, no execution dependency

## Safety Status

- No orders submitted
- No private keys loaded
- No execution client imports
- No on-chain calls
- No OrderFactory instantiation
- No submit_order calls
- No cancel_order calls
- No Polymarket trading credential access
- No live WebSocket captures in Phase 1
- No branch contamination (all work on dedicated phase branches)
- AST-based safety checks passing
- `--fail-on-lookahead` enforced
- `sanitize_info=True` enforced

## Why Phase 3 Is Blocked

Phase 3 (Rust hotpath) requires the Phase 2C gate decision to be `ALLOW_PHASE_3_RUST_HOTPATH`.
The actual gate decision is `REJECTED_FOR_CURRENT_LIVE_CONDITIONS`.
Zero candidates across 10 valid live observation windows and 4 distinct markets.
The fair-probability signal does not survive live Polymarket BTC 15m UpDown maker economics.

## Why Execution Is Blocked

Execution requires a signal that survives live conditions.
No such signal exists in the tested configuration.
The dominant failure mode is `spread_too_wide`: Polymarket quoted spreads exceed the fair-probability edge at all tested thresholds (5, 10, 20, 40 bps).

## Dominant Failure Mode

```
spread_too_wide (72–93% of all grid rejections per window)
```

The Polymarket BTC 15m UpDown market's quoted spread consistently exceeds the fair-probability edge after maker fee adjustment. The Binance reference state is available and not stale for most evaluated events, but the Polymarket mid-quote gap is too wide relative to the computed fair probability to produce tradeable divergence at any tested threshold.

## Scientific Interpretation

- The Phase 1 historical backtest produced candidate grid cells on one resolved market.
- The live observer campaign did not produce candidates.
- The live rejection profile was dominated by `spread_too_wide` and `stale_or_missing_binance`.
- The tested BTC 15m UpDown hypothesis is rejected under current observed live conditions.
- The result does not globally disprove all Polymarket/Binance arbitrage hypotheses.
- This is a rejection under observed live Polymarket BTC 15m UpDown market conditions for thresholds 5, 10, 20, 40 bps.
- Future work must be treated as a new hypothesis, not Phase 3 of this track.

## What Would Need To Change To Resume

Any of the following would constitute a new hypothesis, not a continuation:

1. Wider thresholds (80, 100, 200 bps) — the current signal may survive higher spread barriers
2. Different market durations (1h, 4h BTC UpDown) — spread profiles may differ
3. Different volatility regimes — checked during high-volatility scheduled events
4. Better reference sources (Chainlink stream replay, closer settlement-source proxy)
5. Full order book reference instead of Binance aggTrade/bookTicker proxy

None of these justifies Phase 3 or execution under the current hypothesis.

## Scaffold Reuse Notes

The scaffold in `examples/strategies/polymarket_btcusd_arb/` remains usable as:

- An observer/research lab for new hypotheses
- A backtest harness for Polymarket binary options
- A fair-probability calculator independent of the old arb-bot Rust core
- A replay-deterministic historical analysis tool

To reuse for a new hypothesis:
1. Create a new branch from `polymarket-btcusd-arb-phase2c-observer-analysis`
2. Modify `config.py` with new thresholds, markets, or data sources
3. Define a new hypothesis document
4. Run Phase 1-style backtest first

## Possible New Hypotheses

These are not continuations of the current accepted phase path.
Each requires a new Phase 1-style hypothesis definition and new branch.
None justifies execution.

1. **Wider threshold observer**: 80, 100, 200 bps spread thresholds. Current signal may survive higher spread barriers.
2. **Different market duration**: 1h or 4h BTC UpDown markets. Spread profiles and maker economics may differ significantly.
3. **Different volatility regime**: High-volatility scheduled events (FOMC, CPI, ETF decisions). Spread and signal profiles may shift.
4. **Better reference source**: Chainlink stream replay or closer settlement-source proxy. Binance aggTrade/bookTicker may be too stale or insufficiently granular.
5. **Full order book reference**: Instead of mid-price proxy, use full Binance order book depth for reference probability computation.

## Final Recommendation

```
Do not proceed to Phase 3.
Do not add execution.
Do not add keys.
Do not add orders.
Do not add on-chain paths.
Archive this BTC 15m UpDown research track as REJECTED_FOR_CURRENT_LIVE_CONDITIONS.
The scaffold may be reused only for a separately scoped new hypothesis.
```