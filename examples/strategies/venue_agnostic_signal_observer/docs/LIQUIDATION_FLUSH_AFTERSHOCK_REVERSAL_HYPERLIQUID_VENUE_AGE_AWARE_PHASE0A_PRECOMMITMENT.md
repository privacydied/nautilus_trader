Precommitment SHA-256 (self): 3b63380fc85e7c8ccfe3e95d7c11ac3994a120f713240295ad6ce25df66fe96b

# Liquidation Flush Aftershock Reversal – Hyperliquid Venue-Age-Aware Phase 0A Precommitment

## Study id

`liquidation_flush_aftershock_reversal_venue_age_aware_phase0a`

Stage: `venue_age_aware_phase0a_event_population_audit`

Venue: `hyperliquid`

Asset class: Hyperliquid altcoin perpetual asset-context archive, event-population diagnostics only.

## Precommitment SHA rule

The first line must begin exactly with `Precommitment SHA-256 (self):`.
The runner computes the self hash by removing exactly that first self-hash line, preserving all remaining UTF-8 bytes/newlines, and computing SHA-256 over the remaining bytes. The runner must emit the computed value as `precommitment_sha256` in `summary.json`. If the document is missing or the hash does not match the recorded value, status must be `PHASE0A_ERROR_INVALID_PRECOMMITMENT`.

## Scope and discipline

This is venue-age-aware Phase 0A. It is a separate diagnostic precommitment created after the original calendar-year concentration failure was observed. It is not a retroactive replacement for the original calendar-year-gated Phase 0A, and original Phase 0A remains separately interpreted.

If this venue-age-aware Phase 0A reaches `PHASE0A_EVENT_POPULATION_READY`, it can only unlock Phase 0B in principle under this venue-age-aware diagnostic precommitment. It does not authorize Phase 0B implementation by itself.

No returns, PnL, null tests, FDR, execution, live capture, paper trading, shadow execution, orders, private keys, trading auth, bot path, or systemd watcher are evaluated or used in Phase 0A.

## Archive provenance

The normal/default archive path is `data/hyperliquid_oi_velocity_compression_phase0`.
Active archive rows must be real Hyperliquid public `asset_ctxs` rows already present on disk. No archive rows may be synthesized, deleted, redownloaded, or filled by this diagnostic. Coverage caveats are source-availability notes, not synthetic fills.

Known source-availability caveats for the active archive:
- Window: 2024-01-01 through 2025-12-31.
- JSONL file count: 34.
- Total rows: 34,415,499.
- Manifest date count: 731.
- Duplicate timestamp conflicts: 0.
- 2024_q3 final day truncated at 2024-09-30T19:56:00Z.
- 2025_q4 MKR inactive zero-OI placeholder.

The venue-age-aware runner must reject single-symbol archive file input with `PHASE0A_ERROR_INVALID_ARCHIVE_PATH_SINGLE_SYMBOL_FILE`. The valid diagnostic run uses the active archive directory, not `BTC.jsonl` or any other single JSONL file.

## Altcoin-only gate universe

BTC and ETH raw rows may exist in the archive, but the hypothesis is altcoin-only. BTC and ETH must be excluded before all altcoin gates and concentration calculations, including:
- `total_symbols_accepted`
- `symbols_with_at_least_3_events`
- `accepted_event_count_before_cooldown`
- `accepted_event_count_after_cooldown`
- `max_symbol_event_share`
- `max_symbol_event_share_symbol`
- `quarter_distribution`
- `month_distribution`
- all concentration gates

A valid venue-age-aware summary with `max_symbol_event_share_symbol` equal to BTC or ETH is impossible and must hard-fail.

## Frozen Phase 0A event definition

- Event window: 8h.
- Primary price threshold: 8h price return <= -8.0%.
- Primary OI threshold: 8h open-interest change <= -8.0%.
- Event direction: downside liquidation flush only.
- Cooldown: 72h per symbol after each accepted event.
- Minimum accepted event population: at least 100 events after cooldown.
- Minimum accepted symbols: at least 8 symbols with clean usable coverage.
- Minimum symbols with accepted events: at least 8 symbols with at least 3 accepted events each.
- Symbol concentration: no single altcoin symbol may contribute more than 30% of accepted events.

Thresholds, cooldown, and gates must not be changed after seeing results.

## Venue-age-aware temporal diversification gates

The original calendar-year concentration gate is not used by this separate venue-age-aware diagnostic because it is structurally unsatisfiable for a young venue/archive. Instead:

- Minimum distinct calendar months with accepted altcoin events: >= 9.
- Minimum distinct quarters with accepted altcoin events: >= 4.
- Max calendar quarter event share: <= 0.45; no calendar quarter may contribute more than 45% of accepted altcoin events.
- Max calendar month event share: <= 0.20.

Required summary fields for every non-error summary:
- `max_calendar_quarter_event_share`
- `max_calendar_quarter`
- `max_calendar_month_event_share`
- `max_calendar_month`
- `distinct_months_with_events`
- `distinct_quarters_with_events`
- `quarter_distribution`
- `month_distribution`

Required consistency:
- Sum of `quarter_distribution` counts must equal `accepted_event_count_after_cooldown`.
- Sum of `month_distribution` counts must equal `accepted_event_count_after_cooldown`.
- `distinct_quarters_with_events` must equal the number of nonzero quarter buckets.
- `distinct_months_with_events` must equal the number of nonzero month buckets.

If distribution consistency fails, status must not be `PHASE0A_EVENT_POPULATION_READY`; use `PHASE0A_ERROR_INTERNAL_CONSISTENCY`.

## Valid statuses

- `PHASE0A_EVENT_POPULATION_READY`
- `NEEDS_MORE_DATA_UNDERPOWERED_EVENT_POPULATION`
- `PHASE0A_INSUFFICIENT_ARCHIVE_COVERAGE`
- `PHASE0A_SYMBOL_CONCENTRATION_FAILED`
- `PHASE0A_QUARTER_CONCENTRATION_FAILED`
- `PHASE0A_MONTH_CONCENTRATION_FAILED`
- `PHASE0A_TEMPORAL_COVERAGE_BLOCKED`
- `PHASE0A_ERROR_INVALID_PRECOMMITMENT`
- `PHASE0A_ERROR_INVALID_ARCHIVE_PATH_SINGLE_SYMBOL_FILE`
- `PHASE0A_ERROR_INTERNAL_CONSISTENCY`

## Non-promotion

Phase 0A is only an event-population and archive-quality diagnostic. It makes no directional or profitability claim and must not update `REJECTED_RESEARCH.md` unless explicitly requested later.
