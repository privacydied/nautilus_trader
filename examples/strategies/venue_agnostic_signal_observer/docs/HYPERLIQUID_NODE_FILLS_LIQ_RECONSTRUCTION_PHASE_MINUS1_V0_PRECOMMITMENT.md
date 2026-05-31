# Precommitment: Hyperliquid Node Fills Liquidation Reconstruction Phase -1 v0

Study ID: `hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0`

## Purpose

Determine whether Hyperliquid's public requester-pays S3 archive
`node_fills_by_block/hourly/` is sufficient to reconstruct per-address
isolated-margin liquidation prices, enabling a full liquidation-cluster
map for Phase 0 return testing.

This is a data-plane and reconstruction-validity probe only. It does not
test profitability or run returns.

## Acquisition vs Reconstruction-Validity Distinction

Acquisition questions:
1. Does the archive expose `node_fills_by_block/hourly/`?
2. What is the partitioning layout (coin, time/block, other)?
3. What is the true smallest download unit and its size?

Reconstruction-validity questions:
4. Do fill records contain enough fields for per-address position state?
5. Is user leverage available from a public historical source?
6. Can isolated-only liquidation prices be reconstructed exactly?
7. Does a bounded thin slice plausibly cover enough OI to justify Phase 0?

## Archive Partition Discovery Requirement

Before any symbol-based size assumption or download:
- Discover candidate namespaces.
- Determine partitioning type by inspecting listed object keys.
- Measure actual smallest download unit from listed object sizes.
- Write `source_plan.json` before fetching anything.

Partition types to detect:
- `coin_partitioned`: separate files per symbol per time period.
- `time_partitioned_all_coins`: one file per time period, all coins.
- `block_partitioned_all_coins`: one file per block range, all coins.
- `unknown_partitioning`: cannot determine from listing.

## Requester-Pays / Byte-Cap Safety Policy

- No wide S3 sync (`aws s3 sync`).
- Only exact-prefix listing of candidate namespaces.
- Default max download: 100 MB hard cap.
- If estimated bytes exceed cap, stop before downloading.
- Preserve original compressed files under local cache.
- Compute SHA256 for every fetched object.

## S3 Credential Requirements

Requester-pays buckets require AWS credentials / billable identity.
If `--allow-s3-archive-read` is passed without credentials, emit:
`NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_S3_CREDENTIALS_MISSING`.

Do not print secrets. Do not assume default profile exists.

## Default Target Window Selection Rule

Do not hardcode `2024-01-01` as default. First discover archive coverage
start/end. If 2024-01 is unavailable or expensive, use a recent confirmed
covered complete day.

After partition discovery:
- preferred_symbol: SOL
- fallback_symbols: DOGE, LINK, AVAX, ADA
- max_initial_hours: 6
- max_download_bytes: derived from measured single-hour object size, hard cap 100 MB

If all-coin time partitioning is detected, shrink fetch window to one hour
or fewer, since each file contains all coins.

## Schema Sufficiency Fields

Required for position mechanics:
- user / address (per-fill)
- coin / symbol
- dir / side (with frozen mapping tested against startPosition)
- size / sz
- price / px
- timestamp or block time ordering
- startPosition or enough position-before/after information

Required for exact liquidation-price reconstruction:
- isolated/cross margin mode or safe isolated-only classification source
- user leverage at-or-before position state, from public historical leverage source

Inventory but do not require:
- liquidation flag
- fee
- order type
- block number
- transaction hash

## Frozen `dir` to Signed-Size-Delta Mapping

The dir field must be mapped and tested against startPosition. Observed
variants include: "Open Long", "Close Long", "Open Short", "Close Short",
flip variants, liquidation-marked variants.

Mapping rules (to be verified against fixtures):
- "Open Long": signed delta = +sz
- "Close Long": signed delta = -sz
- "Open Short": signed delta = -sz (short position grows negative)
- "Close Short": signed delta = +sz
- Flip variants: determined by startPosition sign

Side mapping from archive (A/B):
- "A" (ask/taker): inventory decreases, signed delta = -sz
- "B" (bid/maker): inventory increases, signed delta = +sz

If dir mapping cannot be verified against startPosition, block with:
`NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_DIR_MAPPING_UNVERIFIED`.

## Liquidation Flag Inventory Requirement

Explicitly inventory whether a liquidation flag exists in fill records.
If absent, later forced-flow mechanism tests remain blocked or heuristic-only.
Absence must not break position mechanics pass.

## Isolated-Only Scope

Only isolated-margin positions are reconstructed for exact liquidation
price. Cross-margin and undetermined margin mode positions are excluded.

Cross-margin: shared pool across all positions per user, so liquidation
depends on aggregate equity. Exact single-position liquidation price is
not independently computable without full portfolio state.

## Margin-Mode Unknown Behavior

If margin mode cannot be determined from fill records or a separate source,
position mechanics may pass but exact liquidation reconstruction must block:
`NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_MARGIN_MODE_UNDETERMINED`.

## Leverage-Source Decision Policy

Primary pass mode: `EXACT_LEVERAGE_SOURCE_REQUIRED`.

A true reconstructability pass requires:
- per-address position state from node fills
- isolated/cross margin mode or safe isolated-only classification
- user leverage history at or before each position state, from public historical action data (e.g. updateLeverage)
- entry/size/side from fill sequence
- maintenance/max leverage information from public meta/tier data

If user leverage history is unavailable, do not claim exact liquidation-price reconstruction.

Optional diagnostic-only mode: `MAX_LEVERAGE_BOUND_DIAGNOSTIC`.
This computes a worst-case liquidation band using max leverage only. It must be labeled:
`liquidation_price_bound_not_actual_level`, `non_promotable`, `not_phase0_ready`, `not_true_cluster_map`.

Bound mode cannot emit an exact reconstruction pass.

## Exact-vs-Bound Liquidation Reconstruction Distinction

Exact: uses per-user leverage at position time + known entry + known side.
Bound: uses max possible leverage as worst case. Bound is always wider than actual.

Thin-slice approximation formula (until exact Hyperliquid tiered formula is available):

Long isolated:
  liq_price_long = entry * (1 - 1/leverage + 1/(2*max_leverage))

Short isolated:
  liq_price_short = entry * (1 + 1/leverage - 1/(2*max_leverage))

Where max_leverage for SOL is typically up to 50x. Record this as approximation.

## Cold-Start / Burn-In Handling

A position is known only if:
- it opens inside the observed window from flat (startPosition == 0); or
- enough prior burn-in data proves its state.

For thin-slice mechanics check: burn_in_days = 0 is acceptable, but OI
completeness must be informational only and expected to be biased low.

For later burn-in completeness probe: burn_in_days >= 14 required.

## OI Completeness Threshold and Gate

Zero-burn-in thin slice:
- compute OI completeness if available
- mark as informational only
- emit `NODE_FILLS_LIQ_PHASE_MINUS1_COMPLETENESS_DIAGNOSTIC_LOW_ZERO_BURNIN` if low
- do not override schema/position/leverage findings

Burn-in inclusive probe (burn_in_days >= 14):
- apply hard coverage threshold: median_coverage_fraction >= 0.40
- emit `NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_RECONSTRUCTION_COVERAGE_LOW_BURNIN` if below

## Maintenance-Margin Tier Caveat

Large positions have higher maintenance tiers, moving liquidation prices
closer to entry. Large positions are precisely the cluster-relevant ones.

If only flat `1/(2*max_leverage)` is available, allow thin-slice diagnostic
but mark eventual Phase 0 as requiring tier schedule.

## Terminal Statuses

See status taxonomy in implementation spec. Key statuses:

Acquisition blocked:
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_S3_CREDENTIALS_MISSING
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_NAMESPACE_NOT_FOUND
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_ARCHIVE_COVERAGE_UNAVAILABLE
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_PARTITIONING_UNKNOWN
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_COST_OR_SIZE_CAP
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_NO_LOCAL_CACHE

Schema blocked:
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_SCHEMA_MISSING_REQUIRED_FIELDS
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_ADDRESS_FIELD_MISSING
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_POSITION_FIELD_MISSING
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_DIR_MAPPING_UNVERIFIED

Position mechanics passed, exact liquidation blocked:
- NODE_FILLS_LIQ_PHASE_MINUS1_THIN_SLICE_SCHEMA_AND_POSITION_MECHANICS_PASSED
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_MARGIN_MODE_UNDETERMINED
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_LEVERAGE_SOURCE_MISSING
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_LEVERAGE_UNDETERMINED
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_LIQ_PRICE_NOT_RECONSTRUCTABLE
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_MARGIN_TIER_SCHEDULE_UNAVAILABLE

Completeness diagnostic/gate:
- NODE_FILLS_LIQ_PHASE_MINUS1_COMPLETENESS_DIAGNOSTIC_LOW_ZERO_BURNIN
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_RECONSTRUCTION_COVERAGE_LOW_BURNIN

Wall 2 source-existence / margin-mode conservative refinements:
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_REQUESTER_PAYS_AUTH_EXPIRED
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_REPLICA_CMDS_NAMESPACE_NOT_FOUND
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_UPDATE_LEVERAGE_DECODER_UNVERIFIED
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_UPDATE_LEVERAGE_NOT_OBSERVED_IN_REPLICA_CMDS_SAMPLE
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_UPDATE_LEVERAGE_SOURCE_TOO_SPARSE_SAMPLE
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_UPDATE_LEVERAGE_SAMPLE_NOT_FOUND_UNDER_CAP
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_MARGIN_MODE_SAMPLE_NOT_INFORMATIVE
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_ISOLATED_MARGIN_COVERAGE_TOO_LOW_SAMPLE
- NODE_FILLS_LIQ_PHASE_MINUS1_LEVERAGE_MARGIN_SAMPLE_PASSED_FULL_BACKFILL_REQUIRED
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_LEVERAGE_IDENTITY_JOIN_UNVERIFIED
- NODE_FILLS_LIQ_PHASE_MINUS1_BLOCKED_ASSET_SYMBOL_MAPPING_UNVERIFIED

These statuses are post-hoc conservative refinements of the Phase -1 feasibility taxonomy for the user-approved bounded targeted backscan. They do not relax gates, authorize Phase 0, imply profitability, change the frozen hypothesis universe, or permit live/paper/shadow execution.

Bound diagnostic:
- NODE_FILLS_LIQ_PHASE_MINUS1_THIN_SLICE_BOUND_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE

Exact thin-slice feasibility passed:
- NODE_FILLS_LIQ_PHASE_MINUS1_THIN_SLICE_EXACT_RECONSTRUCTION_PASSED_REVIEW_ALLOWED

Forbidden statuses: REJECTED, PROFITABLE, ALPHA_FOUND, EDGE_CONFIRMED,
TRADE_READY, EXECUTION_READY, LIVE_READY, PAPER_READY, SHADOW_READY,
CANDIDATE_FOR_LIVE, CANDIDATE_FOR_PAPER, PAPER_STRATEGY_PROMOTED,
PROMOTION_AUTHORIZED, READY_FOR_PHASE_0.

## Artifacts

All artifacts written to:
`reports/hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0/<run_id>/`

Required JSON artifacts include study metadata, safety flags, and precommitment hash.

## No Phase 0 Returns

This probe does not run returns or compute alpha metrics. It only establishes
whether reconstruction is feasible.

## No Registry Rejection

Do not write REJECTED entries. Use NEEDS_MORE_DATA / NODE_FILLS_RECONSTRUCTION_BLOCKED if updating registry.

## No Live/Paper/Shadow/Conductor Unlock

Passing this probe only unblocks writing a separate frozen Phase 0 precommitment.
It does not test profitability or authorize any execution mode.
