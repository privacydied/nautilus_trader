# Discovery Package — Edge Miner Freeze/Precommitment Foundation

## Four-Stage Model

This package implements **Phase 1** of a four-stage research-to-execution pipeline:

```
┌──────────┐     ┌──────────┐     ┌────────────────┐     ┌─────────────┐
│ Edge     │ ──▶ │Validator │ ──▶ │Shadow Executor │ ──▶ │Trading Bot  │
│ Miner    │     │          │     │                │     │             │
└──────────┘     └──────────┘     └────────────────┘     └─────────────┘
  Phase 1          Phase 5           Phase 6                Phase 7
```

1. **Edge Miner** — Finds possible market edges by scanning a frozen, precommitted grid of source → feature → target → lag → regime relationships over public market data.

2. **Validator** — Validates frozen candidate rules using holdout, native permutation/null testing, FDR, cost sensitivity, and cross-capture recurrence.

3. **Shadow Executor** — Tests whether validated candidates still work under realistic queue/fill/spread/staleness assumptions without placing orders.

4. **Trading Bot** — Eventually executes only approved frozen candidate manifests through Nautilus, under strict guard rails.

## The Two-Freeze Rule

1. **Grid freeze** before discovery scan starts. This freezes the full search space and the FDR denominator.

2. **Candidate freeze** after a cluster surfaces. This freezes the exact discovered rule/cluster and the discovery data it came from.

### Data Freeze Addition

Candidate locks must reference **discovery capture manifest hashes**, not just capture IDs. This pins the exact data artifact (including its metadata and generation timestamp) so a later validator can prove holdout captures are distinct from discovery captures.

## Core Invariant

> Discovery may scan exhaustively inside a frozen grid.  
> Discovery may not mutate the grid during the same run family.  
> Changing the grid creates a new grid hash, a new run family, and a new FDR denominator.

## Statistical Invariant

The grid hash alone is not enough. The lock must also record the **enumerated `primary_cell_count`** produced by the current enumeration logic. If the same grid spec later expands to a different `primary_cell_count` (e.g., because enumeration code changed), validation fails loudly. This prevents silent denominator drift.

## Primary Counted Axes

These 10 axes multiply into `primary_cell_count` and define the FDR denominator for discovery:

| Axis | Description |
|------|-------------|
| `source_venues` | Venues providing the signal |
| `source_symbols` | Symbols providing the signal |
| `target_venues` | Venues receiving the prediction |
| `target_symbols` | Symbols receiving the prediction |
| `feature_types` | Signal feature categories |
| `lookbacks_ms` | Lookback windows in milliseconds |
| `thresholds_centibps` | Entry thresholds in centibps |
| `horizons_ms` | Forward prediction horizons in milliseconds |
| `entry_delays_ms` | Entry execution delays in milliseconds |
| `regime_filters` | Market regime filter names |

## Non-Multiplicative Hashed Fields

These fields are part of the grid hash (they change discovery semantics) but do **not** multiply into `primary_cell_count`:

| Field | Rationale |
|-------|-----------|
| `cooldown_ms` | Event de-duplication rule, not an axis |
| `min_events` | Group admissibility rule, not an axis |
| `clustering_keys` | Post-discovery grouping rule, not an axis |
| `fdr_family_dimensions` | Statistical-family declaration, not an axis |
| `cost_models_centibps` | Evaluation lenses, not source→target→feature→horizon relationships |

## Denomination Rules

- **`primary_cell_count`** = product of the 10 primary counted axis cardinalities. This is the FDR denominator.
- **`cost_sensitivity_cell_count`** = `primary_cell_count * len(cost_models_centibps)`. Diagnostic only. NOT the FDR denominator.
- **`fdr_family_dimensions`** is a separate declaration (a subset of the counted axes) consumed by the Phase 5 validator. `primary_cell_count` and `fdr_family_dimensions` are intentionally allowed to differ.

Important: `cooldown_ms` and `min_events` affect neither cell count.

## Schema Versions

| Schema | Version String |
|--------|---------------|
| Grid spec | `discovery-grid-v1` |
| Candidate lock | `discovery-candidate-v1` |

### Compatibility Matrix

- `discovery-candidate-v1` is only compatible with `discovery-grid-v1`.
- A candidate lock whose parent grid schema is not `discovery-grid-v1` must fail validation.
- A candidate lock's own `schema_version` must be `discovery-candidate-v1`.
- A grid lock `schema_version` must match the grid spec `schema_version` exactly.
- Any `schema_version` mismatch is a hard refusal.

## Candidate Lock Structure

A `DiscoveryCandidateLock` always:
- References `parent_grid_id` and `parent_grid_hash`
- References `parent_grid_schema_version`
- References `parent_grid_primary_cell_count` and `parent_grid_cost_sensitivity_cell_count`
- Includes at least one discovery capture manifest ref
- Includes at least one selected cell

Candidate hash includes: `parent_grid_id`, `parent_grid_hash`, `parent_grid_schema_version`, `parent_grid_primary_cell_count`, `parent_grid_cost_sensitivity_cell_count`, `selected_cells` (canonicalized and sorted), discovery capture manifest hashes (sorted), and `schema_version`.

Candidate hash excludes: `candidate_id`, `frozen_at_utc`, `cluster_summary`, `selection_reason`, filesystem output paths, runtime timestamps, and `manifest_path` values.

**Note:** `manifest_path` is convenience metadata only. The trust anchor is `manifest_sha256`.

Although `cluster_summary` and `selection_reason` are excluded from `candidate_hash`, saved candidate lock files are **immutable artifacts**. Do not mutate them in place; write a new artifact if explanatory metadata changes.

## Golden Example Grid

```json
{
  "grid_id": "edge_miner_cross_asset_beta_lag_v1",
  "schema_version": "discovery-grid-v1",
  "signal_family": "cross_asset_beta_lag",
  "source_venues": ["binance_perp"],
  "source_symbols": ["BTC/USDT", "ETH/USDT"],
  "target_venues": ["kraken", "coinbase"],
  "target_symbols": ["SOL/USD", "DOGE/USD", "LINK/USD", "AVAX/USD"],
  "feature_types": ["price_impulse", "signed_imbalance", "notional_burst", "large_trade"],
  "lookbacks_ms": [1000, 5000, 10000, 30000, 60000],
  "thresholds_centibps": [1000, 2000, 3000, 5000],
  "horizons_ms": [10000, 30000, 60000, 180000, 300000],
  "entry_delays_ms": [0, 5000, 15000],
  "cooldown_ms": 60000,
  "regime_filters": ["market_active", "market_stress", "btc_1m_vol_p95"],
  "cost_models_centibps": [1000, 2500, 5000],
  "min_events": 30,
  "clustering_keys": ["feature_types", "lookbacks_ms", "horizons_ms", "target_symbols"],
  "fdr_family_dimensions": ["source_symbols", "target_symbols", "feature_types", "lookbacks_ms", "thresholds_centibps", "horizons_ms", "entry_delays_ms", "regime_filters"],
  "created_at_utc": "2026-05-17T00:00:00Z",
  "notes": "Golden example grid for deterministic freeze tests. created_at_utc and notes must not affect grid_hash."
}
```

- **Expected hash:** `52a34e07c10312b6a49657c49bef541a3b2b55e969773412f6bc9a11f00d8bbb`
- **Expected `primary_cell_count`:** 57600
- **Expected `cost_sensitivity_cell_count`:** 172800

The golden hash must change intentionally if `schema_version` changes in a future v2. This is expected drift, not a bug.

## Capture Manifest Hashes

Raw captures can be reused across grid versions, but statistical claims cannot cross grid families unless explicitly recorded. Manifest hashes pin specific capture artifacts as raw bytes — they do not canonicalize the manifest before hashing. This hash proves "this exact artifact file" was used, not "an equivalent set of data."

## Phase 1 Limitations

1. **Capture-family enforcement**: Capture manifests predate the discovery subsystem. Phase 1 can pin a manifest's bytes via hash, but cannot prove a given capture was produced under a given grid family. "Capture belongs to this grid family" enforcement is a Phase 2 follow-up.

2. **Safety scanner depth**: The scanner is intentionally shallow: it scans the discovery package's own files and forbids first-party imports outside the package, but does not deeply traverse the transitive import graph of stdlib. This is acceptable because the first-party-import ban already blocks the dangerous transitive path.

3. **Promotion boundary wiring**: `promotion_boundary.require_frozen_candidate_for_validation` is provided and tested in Phase 1, but Phase 1 cannot force a future validator to call it. Phase 5 must wire it as the sole entrypoint into validation.

## Package Safety

This package:
- Does **not** execute trades.
- Does **not** create live trading candidates.
- Does **not** place orders.
- Does **not** connect to exchanges.
- Does **not** import `nautilus_trader`.
- **Only** defines and validates frozen discovery contracts.

## CLI Entrypoints

| Command | Purpose |
|---------|---------|
| `run_lock_discovery_grid.py <spec.json> <lock.json>` | Create a grid lock from a grid spec |
| `run_validate_discovery_grid_lock.py <spec.json> <lock.json>` | Validate grid spec against its lock |
| `run_lock_discovery_candidate.py <spec.json> <lock.json> <cells.json> <out.json> [captures...]` | Create a candidate lock |

No CLI accepts `--force`. Existing different locks cannot be overwritten; semantically identical ones are accepted.

## Follow-Up Tasks (Future Phases)

- **Phase 2**: `stream_registry.py` and `capture_supervisor.py` generalization, plus capture-belongs-to-grid-family enforcement.
- **Phase 3**: `feature_factory.py` and `discovery_evaluator.py`.
- **Phase 4**: `candidate_clusterer.py` and `candidate_cards.py`.
- **Phase 5**: Validator integration with holdout/null/FDR/corpus recurrence, wiring `promotion_boundary.require_frozen_candidate_for_validation` as the sole entrypoint.
- **Phase 6**: Shadow Executor queue/fill model.
- **Phase 7**: Strictly gated Nautilus Trading Bot consuming `approved_candidate_manifest.json` only.
