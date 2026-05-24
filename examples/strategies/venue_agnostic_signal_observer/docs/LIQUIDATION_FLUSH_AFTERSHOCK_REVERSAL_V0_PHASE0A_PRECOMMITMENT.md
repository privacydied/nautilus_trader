Precommitment SHA-256 (self): ed2361e3d5a08766c857ad812119fabfee6ceccafc8c612240eed71ef4c1262e

# Liquidation flush aftershock reversal v0 Phase 0A precommitment

## Study id

`liquidation_flush_aftershock_reversal_v0`

Stage: `phase0a_event_population_audit`

Venue: `hyperliquid`

Asset class: altcoin perpetuals.

## Precommitment SHA rule

The line beginning exactly with `Precommitment SHA-256 (self):` is excluded from the self hash.
The hash rule is:

1. Remove exactly one line beginning with `Precommitment SHA-256 (self):`.
2. Preserve all other UTF-8 bytes and newlines.
3. Compute SHA-256 over the remaining UTF-8 bytes.

A startup self-check must verify the document hash equals the hash recorded in the document. A real or synthetic run summary must record the same hash as `precommitment_sha256`.

## Scope

Phase 0A is an archive-only event-population audit. The only question it may answer is:

Does the selected archive contain enough historical joint liquidation-flush events, across enough symbols and years, to justify a later Phase 0B return evaluation?

The audit counts historical joint downside price-shock plus open-interest-collapse events. It records population breadth, archive coverage, concentration, duplicate-timestamp diagnostics, and archive freshness.

## Non-scope

Phase 0A does not evaluate profitability. It does not evaluate forward returns, PnL, null tests, FDR, execution, live capture, paper trading, or any trading authorization. Phase 0A does not authorize v1. Phase 0A does not authorize trading. Phase 0A does not update `REJECTED_RESEARCH.md` unless the user explicitly requests it later.

## Venue lock

Hyperliquid only. The implementation must not switch venues if Hyperliquid coverage is poor. Binance USD-M or any other venue would be a separate study.

## Restricted archive discovery paths

Archive discovery is restricted to these candidate path families, in order:

1. `examples/strategies/venue_agnostic_signal_observer/data/hyperliquid/`
2. `data/hyperliquid_archive/`
3. `reports/hyperliquid_oi_velocity_compression_phase0/*/`

The third path is read-only reuse of prior archive artifacts. No output may be written into prior report directories.

## Archive-only safety

Public/archive data only. No orders. No private keys. No exchange-account assumptions. No live execution. No paper trading. No shadow executor. No bot path. No systemd watcher changes. No live capture jobs. No wallet or signing code. No execution clients.

## Relationship to rejected research

This study remains structurally distinct from prior rejected or closed work and does not reopen or modify it.

## Structurally distinct from

- Family 2 funding crowding reversal.
- Family 3 funding x OI BTCUSDT spot-return grids.
- Hyperliquid Supertrend 4h/1d altcoin perp v0.
- Hyperliquid OI velocity compression v0.
- Hyperliquid multi-asset funding carry Phase 0.
- Cross-exchange funding dispersion carry.
- Derivatives-source spot lead-lag.
- Tick lead-lag.
- Kraken OHLCV indicator research.

The structural difference is: multi-asset altcoin perps, same-venue Hyperliquid perp archive, joint price-shock plus OI-collapse event population, event-tail audit only, and population-existence audit only. Unlike the prior Hyperliquid OI velocity compression direction-proxy failure, this Phase 0A makes no directional claim and does not test direction proxy stability.

## Frozen Phase 0A event definition

- Event window: 8h.
- Primary price threshold: 8h price return <= -8.0%.
- Primary OI threshold: 8h open-interest change <= -8.0%.
- Event direction: downside liquidation flush only.
- Cooldown: 72h per symbol after each accepted event.
- Minimum coverage per accepted symbol: at least 9 months of usable price plus OI history.
- Minimum accepted symbols: at least 8 symbols with clean coverage.
- Minimum accepted event population: at least 100 accepted events after cooldown.
- Minimum symbol breadth: at least 8 symbols must contribute at least 3 accepted events each.
- Maximum symbol concentration: no single symbol may contribute more than 30% of accepted events.
- Maximum year concentration: no single calendar year may contribute more than 45% of accepted events.

For timestamp `t`:

`price_return_8h_pct = 100 * (price_t / price_t_minus_8h - 1)`

`oi_change_8h_pct = 100 * (oi_t / oi_t_minus_8h - 1)`

All event windows are past-only. No future data may be used in event construction. The prior row for `t - 8h` may be the nearest row at or before the target only if it is within the maximum alignment tolerance. Default maximum alignment tolerance is 65 minutes.

## Deterministic duplicate-timestamp rule

For duplicate `symbol + timestamp` rows, keep the last occurrence by file order. Drop earlier duplicate rows and count them in `duplicate_timestamp_rows`. If duplicate price values for the same key differ by more than 5 bps, emit a diagnostic warning in `summary.md`. If duplicate OI values differ by more than 1%, emit a diagnostic warning in `summary.md`. If either warning occurs for a symbol, mark that symbol's `non_monotonic_detected` field true. Duplicate drift alone does not reject the symbol in Phase 0A.

## Deterministic cooldown ordering rule

Candidate-event processing order within each symbol is strict ascending `event_timestamp_utc`. Cooldown is greedy from earliest:

1. Accept the earliest eligible candidate.
2. Exclude all candidates with timestamp <= accepted_timestamp + 72h.
3. Resume scanning after the cooldown boundary.

## Frozen population gates

Only the frozen -8% price / -8% OI primary gate controls `unlocks_phase0b`. Diagnostic thresholds never unlock Phase 0B.

## Threshold discipline

Report distribution diagnostics for:

- 8h return percentiles by symbol and all-symbol pooled.
- 8h OI-change percentiles by symbol and all-symbol pooled.
- Joint-event counts at -5% / -5%, -8% / -8%, -10% / -10%, per-symbol bottom 5% / bottom 5%, and per-symbol bottom 2.5% / bottom 2.5%.

Distribution diagnostics may explain sparsity or abundance. They must not promote any threshold other than the frozen primary threshold.

## Archive freshness diagnostic

Compute:

`archive_end_age_days = (generated_at_utc - archive_end_utc).days`

Report it in `summary.json` and `summary.md`. This is not a Phase 0A gate. If Phase 0B is later precommitted, that precommitment must decide whether `archive_end_age_days` warrants a fresh public archive backfill before return evaluation.

## Forbidden filters

Do not include funding, RSI, Supertrend, ATR, moving averages, long/short ratio, volume, liquidation prints, volatility filters, market regime filters, BTC trend filters, or exchange-wide risk filters as Phase 0A filters.

## Allowed statuses

- `PHASE0A_EVENT_POPULATION_READY`
- `PHASE0A_INSUFFICIENT_ARCHIVE_COVERAGE`
- `NEEDS_MORE_DATA_UNDERPOWERED_EVENT_POPULATION`
- `PHASE0A_SYMBOL_CONCENTRATION_FAILED`
- `PHASE0A_YEAR_CONCENTRATION_FAILED`
- `PHASE0A_ERROR_INVALID_INPUT`
- `PHASE0A_ERROR_INVALID_PRECOMMITMENT`

## Invalid-input status rules

Return `PHASE0A_ERROR_INVALID_INPUT` if the archive contains zero rows after loading, if more than 50% of rows across all symbols fail field validation due to missing timestamp, symbol, price/OI fields, or if archive timestamps are entirely non-UTC-parseable.

## Invalid-precommitment status rules

Return `PHASE0A_ERROR_INVALID_PRECOMMITMENT` if any frozen constants in the implementation module disagree with this document for event window, price threshold, OI threshold, cooldown, minimum events, minimum symbols, maximum symbol share, or maximum year share; or if the precommitment SHA self-check fails.

## Runner exit-code rules

Exit 0 when Phase 0A completes and summary is written, regardless of normal gate status. Exit 2 for invalid input or invalid precommitment. Exit 3 for unhandled exception after best-effort traceback dump to `summary.md`. Do not intentionally exit 1.

## Phase 0B unlock rule

`unlocks_phase0b` is true only when status is exactly `PHASE0A_EVENT_POPULATION_READY`. Every other status keeps Phase 0B locked.

## Phase 0B requirement if unlocked

If Phase 0B is later implemented, it must account for directional funding-paid-while-held using archive funding rates across the hold window. For long perp aftershock tests, funding impact must be computed from actual historical funding settlements between entry and exit, not assumed constants. Positive funding paid by longs must reduce net return; negative funding received by longs must increase net return. This funding logic is not implemented in Phase 0A.
