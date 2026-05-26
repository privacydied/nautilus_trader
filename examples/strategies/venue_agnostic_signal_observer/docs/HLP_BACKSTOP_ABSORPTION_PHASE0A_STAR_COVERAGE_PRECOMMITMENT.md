# HLP backstop absorption — Phase 0A* coverage/mechanism diagnostic

Precommitment SHA-256: 72c5d0f21b664f698c4c3dee26abd7e13e18adc2e481a42bbae6d5f1c21d480b

## Status

**Phase 0A* coverage/mechanism diagnostic only.**

This is NOT an economic precommitment.
It does NOT test profitability.
It CANNOT produce a conductor/paper promotion candidate.
It does NOT unlock live, paper, shadow, or conductor promotion.

It determines only whether historical HLP/liquidator-vault backstop inventory is reconstructable and separable enough to justify a future human-authored signal precommitment.

Any future signal precommitment must be written by a human operator in a separate session.

## Kill question

Can Hyperliquid HLP / liquidator-vault per-symbol backstop inventory changes be reconstructed historically, at hourly-or-better cadence, from public archive data, with the backstop component separable from market-making / Earn vault activity, without lookahead, live recording, auth, or private endpoints?

## Terminology

- **HLP backstop absorption**: The subset of liquidator-vault inventory changes attributable to the backstop mechanism, separate from market-making or Earn vault activity.
- **Backstop-attributed inventory delta**: A per-symbol, per-time signed size change where the counterparty on at least one side is a child vault classified as BACKSTOP or BACKSTOP_INFERRED.
- **Liquidator-vault inventory shock**: A large absolute inventory delta whose controlling half-life is short enough to indicate hedging offset.

All naming, verdicts, and code comments use narrow language specific to the HLP backstop path. Do not confuse with generic liquidation absorption.

## Data model

Hyperliquid liquidations primarily route to the open order book. The liquidator vault / HLP path is a **backstop** path, not the primary liquidation route.

### Data sources (priority order)

1. Local Hyperliquid archive/cache under `--data-root`.
2. Existing project-supported Hyperliquid S3 archive paths only if already implemented safely.
3. `hl-mainnet-node-data/node_fills_by_block` local mirror or explicit S3 path.
4. Older `node_fills` / `node_trades` formats as fallback with deterministic schema parsing.
5. Artemis daily Perp/Spot Balances if present locally or explicitly supplied (third-party validation/fallback).
6. Hyperliquid public info API `vaultDetails` only when `--allow-public-metadata-api` is set, for static metadata/address discovery only.
7. **Never** use `userFillsByTime` — it is capped/recent-only and forbidden.

### Network policy

Default: no network.
`--allow-public-metadata-api` may be used only for metadata/address discovery.
No broad archive download by default.
S3 access requires explicit opt-in flag.

### Symbol universe

AAVE, ADA, APT, ARB, ATOM, AVAX, BCH, BNB, BTC, DOGE, DOT, ENA, ETH, FET,
HYPE, INJ, JUP, LINK, LTC, MKR, NEAR, ONDO, OP, PENDLE, SEI, SOL, SUI, TIA,
TON, TRX, UNI, WIF, WLD, XRP

BTC and ETH are included for this coverage diagnostic only. Any downstream signal hypothesis must exclude BTC/ETH unless separately precommitted.
PEPE is excluded.

### Frozen default window

Start: 2025-08-17
End: latest_evaluable_complete_archive_timestamp - 24h

If no latest-evaluable timestamp can be determined, fail closed with HLP_BACKSTOP_COVERAGE_ARCHIVE_INFEASIBLE.

## Address resolution

Resolve:
1. Canonical HLP parent vault address.
2. Active HLP child vault addresses during the diagnostic window.
3. Role label per child: BACKSTOP, BACKSTOP_INFERRED, MM_PARENT, MM_PER_SYMBOL_<symbol>, EARN, UNKNOWN.

Resolution sources (in order):
1. Frozen local fixture / metadata snapshot.
2. Hyperliquid vaultDetails only with --allow-public-metadata-api.
3. Official docs/blog metadata if locally supplied and content-hashed.
4. Heuristic inference from fills.

## Reconstruction paths

### Path A — hourly from node_fills_by_block

1. Stream fills for resolved child vaults and symbols in the window.
2. Filter fills where buyer or seller equals child vault address.
3. Signed size delta: vault buyer = +size, vault seller = -size.
4. Accumulate cumulative per-symbol inventory by child vault.
5. Aggregate to hourly bars with fills strictly before the hour boundary.
6. Missing hours = MISSING, not zero. No interpolation or forward fill.
7. Record block/time provenance for every bar.

### Path B — daily from Artemis-style Perp/Spot Balances

1. Load daily position snapshots by vault address.
2. Compute daily net signed position per symbol.
3. Compute daily delta = today - previous day.
4. Detect missing dates.
5. Write empty artifact with source_unavailable metadata if unavailable.

## Gates

### Hourly coverage gate
HLP_BACKSTOP_COVERAGE_HOURLY_RECONSTRUCTABLE requires:
- Backstop child vault resolved with documented or inferred_high confidence.
- >= 12 symbols with >= 180 consecutive UTC days of hourly coverage.
- No qualifying symbol has gap > 72h.
- Zero lookahead violations.
- Total backstop-attributed fills >= 500 across qualifying symbols.
- Cross-source consistency passes 90% threshold if overlap exists.

### Daily coverage gate
HLP_BACKSTOP_COVERAGE_DAILY_ONLY requires:
- Same child vault resolution.
- >= 12 symbols with >= 180 consecutive calendar days of daily coverage.
- No daily gap > 5 days.
- Total nonzero daily backstop-attributed delta events >= 200.

### Backstop inseparability gate
HLP_BACKSTOP_COVERAGE_BACKSTOP_INSEPARABLE if inventory is reconstructable but no child vault can be confidently classified as backstop.

### External hedging / fast unwind gate
HLP_BACKSTOP_COVERAGE_EXTERNAL_HEDGING_DOMINATES if median backstop half-life < 1h across qualifying symbols.

### Diagnostic error
HLP_BACKSTOP_COVERAGE_DIAGNOSTIC_ERROR for hash mismatch, schema failure, pipeline error, or unsafe path.

## Artifacts

Written under reports/hlp_backstop_absorption_phase0a_star_coverage/<run_id>/

Required: summary.json, summary.md, manifest.json, address_resolution.json,
coverage_per_symbol.csv, backstop_inventory_hourly.parquet,
backstop_inventory_daily.parquet, cross_source_check.csv,
external_hedging_halflife.csv, controls.json, lookahead_audit.json,
source_inventory.json, suggested_registry_snippet.md

## Locked fields

summary.json contains:
- promotion_candidate: false (always)
- paper_promotion_locked: true (always)
- observer_only: true (always)
- no_order_intent: true (always)
- conductor_ready: false (always)
- downstream_unlock: none | hourly_signal_phase0_authorable | daily_signal_phase0_authorable
