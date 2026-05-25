Precommitment SHA-256 (self): dbe02660f57fae5684290529ad8545fce5dadd93e9c01267f91bd0179bc46ae7

# Liquidation Flush Aftershock Reversal – Hyperliquid Venue-Age-Aware Phase 0B Precommitment

## Scope

This is Phase 0B for the venue-age-aware diagnostic precommitment only. It is not validation of the original calendar-year-gated Phase 0A; that original path remains locked by year concentration.

## Frozen Phase 0A source

Phase 0A source report path: `reports/liquidation_flush_aftershock_reversal_venue_age_aware_phase0a/liquidation_flush_aftershock_reversal_venue_age_aware_phase0a_20260525T061624_145154_5680d5`

Phase 0A precommitment SHA-256: `3b63380fc85e7c8ccfe3e95d7c11ac3994a120f713240295ad6ce25df66fe96b`

Phase 0A accepted event count: `955`

Phase 0A accepted event artifact hash: `c2ef21042e7b22e58e60dd96d3b65e211a4f853f5ca6b43bb79a8614d4734f85`

Active archive path: `data/hyperliquid_oi_velocity_compression_phase0`

## Frozen event universe

Phase 0B must use exactly the deterministic Phase 0A accepted event artifact. It must not change Phase 0A event thresholds, cooldown, symbol gates, quarter/month gates, archive rows, or accepted-event selection. It must not relabel events or run a grid search.

BTC/ETH exclusion: BTC and ETH raw rows may exist in the archive but are excluded from the altcoin event universe and all Phase 0B evaluation counts. Any BTC or ETH event in the Phase 0A accepted-event artifact is a hard event-reproduction failure.

## Economic model

Primary outcome: directional post-event reversal return in bps from event price to future mark price.

Horizons: `6h`, `12h`, `24h`, `48h`.

Primary horizon: `24h`.

Direction:
- downside liquidation flush / long wipe: reversal direction is long; return = future_price / event_price - 1.
- upside liquidation flush / short squeeze: reversal direction is short; return = event_price / future_price - 1.
- ambiguous direction: exclude before evaluation and report count.

Costs:
- primary cost haircut: 50 bps round-trip.
- secondary diagnostic: 25 bps round-trip.
- report gross and net.
- primary verdict uses 50 bps net.

## Primary pass gate

All must pass at the 24h primary horizon:
- 24h mean net bps after 50 bps cost > +10 bps.
- 24h median net bps after 50 bps cost > 0 bps.
- 24h win rate after 50 bps cost > 0.53.
- lower confidence bound for 24h mean net bps > 0.
- effective event count after de-overlap/cooldown remains >= 200.

## Hard fail gates

- 24h mean net bps <= 0 after 50 bps cost.
- or 24h median net bps <= 0.
- or win rate <= 0.50.
- or event reproduction mismatch.
- or BTC/ETH included.
- or precommitment hash mismatch.

## Null

No null engine is implemented in this Phase 0B task. If the primary gate passes without null, status is `PHASE0B_RETURN_DIAGNOSTIC_NO_NULL` and the next phase is not automatically authorized as trading.

## No execution / safety

No live trading, no order placement, no private keys, no auth, no paper trading, no shadow execution, no systemd watcher, no bot path, and no `REJECTED_RESEARCH.md` update are allowed during Phase 0B execution.
