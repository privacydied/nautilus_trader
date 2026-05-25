# Liquidation Flush Aftershock Reversal – Hyperliquid Venue-Age-Aware Phase 0A Precommitment

## Purpose
Replace the frozen calendar‑year concentration gate with temporal diversification gates that are satisfiable for a venue/archive age of 12‑24 months.

## Archive provenance
- Uses the same `hyperliquid-archive/asset_ctxs` source.
- Safety mode: `public_data_observer_only`.
- No orders, private keys, auth, live execution.

## Temporal diversification gates
- Minimum clean coverage: ≥ 12 months of archive data with at least one accepted event per month.
- Minimum distinct calendar months with accepted events: ≥ 9.
- Minimum distinct quarters with accepted events: ≥ 4.
- Max calendar quarter event share: ≤ 0.45.
- Max calendar month event share: ≤ 0.20 (or ≤ 0.25 for lenient mode).
- Symbol event share gate: retain existing `max_symbol_event_share` from current Phase 0A precommitment.
- BTC/ETH raw rows allowed but excluded from altcoin per‑symbol gates when hypothesis is altcoin‑only.
- Inactive zero‑OI symbols count as unusable per‑symbol coverage, not as fragmented archive corruption.

## Kill/Lock statuses (as in existing Phase 0A)
- `PHASE0A_EVENT_POPULATION_READY`
- `PHASE0A_INSUFFICIENT_ARCHIVE_COVERAGE`
- `PHASE0A_UNDERPOWERED_EVENT_POPULATION`
- `PHASE0A_QUARTER_CONCENTRATION_FAILED`
- `PHASE0A_MONTH_CONCENTRATION_FAILED`
- `PHASE0A_SYMBOL_CONCENTRATION_FAILED`
- `PHASE0A_TEMPORAL_COVERAGE_BLOCKED`
- `PHASE0A_ARCHIVE_PROVENANCE_INVALID`

## Note
The previous year‑gate failure is not evidence of signal quality; it only motivates this venue‑age‑aware redesign.
