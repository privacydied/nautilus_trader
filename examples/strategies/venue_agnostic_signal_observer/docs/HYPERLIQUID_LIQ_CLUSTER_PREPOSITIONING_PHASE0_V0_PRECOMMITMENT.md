# Hyperliquid Liquidation Cluster Prepositioning - Phase -1 + Phase 0 v0 Precommitment

## Corrective Addendum — Proxy Reconstruction Invalidates Phase 0A Reconstructable Status

**Date**: 2026-05-30 (corrective closure)
**Branch**: `feat/hyperliquid-liq-cluster-prepositioning-phase0-v0` → corrective branch: `fix/liq-cluster-proxy-closure-v0`

The Phase -1 implementation uses **aggregate-OI proxy reconstruction** rather than per-address isolated-margin liquidation-price reconstruction. Specifically:

- It relies on aggregate open-interest snapshots, not per-address position state.
- It assumes a 60/40 long/short split with no evidence for the true margin-mode distribution.
- It uses hardcoded default leverage tiers (e.g., SOL=25x) rather than historical `meta` snapshots or node-fill-derived leverage profiles.
- It cannot distinguish isolated-margin from cross-margin positions, and cross-margin/undetermined positions dominate the altcoin universe.
- Leverage-tier history is unavailable; a single current snapshot is applied across all timestamps.

Because of these constraints, the reconstructed liquidation-price map is a **biased fragment-map**, not the exact mechanism specified in this precommitment. The original run emitted `PHASE0A_MECHANISM_RECONSTRUCTABLE`, which was too strong.

### Corrected Classification

| Field | Value |
|-------|-------|
| Original emitted status | `PHASE0A_MECHANISM_RECONSTRUCTABLE` |
| Corrected status | `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_PROXY_ONLY_RECONSTRUCTION` |
| True hypothesis verdict | **NOT_TESTED** — archive reconstruction blocked |
| Proxy diagnostic verdict | Negative proxy result, **not promotable**, not evidence against the true mechanism |
| Phase 0B validity | Not valid for mechanism evaluation when Phase 0A is proxy-only |
| Registry posture | Do **not** mark `REJECTED` |

### Why aggregate OI is insufficient

1. **Aggregate OI cannot distinguish isolated vs cross margin.** Cross-margin positions have liquidation prices that depend on total account equity, other positions, and unrealized PnL — they are not reconstructable from a single-symbol OI snapshot.
2. **Hardcoded leverage defaults are insufficient.** The precommitment requires per-address leverage or enough position-level data to derive it. A default tier applied across all timestamps cannot capture historical leverage-tier changes.
3. **No node fills / per-address state.** Without node fills archive access, there is no way to recover per-address margin mode, entry price, size, and leverage for the effective altcoin universe.
4. **Mark-only returns are diagnostic only when L2 executable books are absent.** The true mechanism requires executable entries near the cluster; mark-return is secondary.

### What this means

- The negative proxy results (mean net50: -46.3 bps, median net50: -48.5 bps, win rate: 26.1%) are **diagnostic context only**.
- They do **not** reject the true liquidation-cluster hypothesis — the true mechanism was never actually measured.
- No v1, paper, shadow, live, or auto-promotion is unlocked.
- The required future unblocker is: **node fills archive access** (per-address position state) or a public liquidation-price/margin-mode source.

---

## Study ID
`hyperliquid_liq_cluster_prepositioning_phase0_v0`

## Branch
`feat/hyperliquid-liq-cluster-prepositioning-phase0-v0`

## Frozen Question
Can a past-only reconstructed isolated-margin liquidation-cluster map on Hyperliquid non-BTC/ETH altcoin perps predict signed continuation into/through dense liquidation clusters over 15m/30m/60m horizons by more than primary 50 bps round-trip cost, after separating the effect from ordinary short-horizon momentum, and after surviving matched controls, circular-shift null, chronological holdout, and FDR?

## Hypothesis
When current price approaches a dense reconstructed cluster of isolated-margin leveraged liquidation prices, price tends to continue into and through that cluster over the next several minutes to one hour.

Direction is cluster-side continuation:
- Long-liquidation cluster below current price implies downside continuation when price approaches from above.
- Short-liquidation cluster above current price implies upside continuation when price approaches from below.

## Critical Boundary
Do not retest the old venue-age-aware liquidation-flush aftershock reversal. That prior work was reactive after a flush and failed Phase 0C clustering/null/survivorship checks. This study is predictive and map-based: cluster exists before approach, signal fires before cluster contact, and the tested edge is continuation into/through the cluster.

## Data Sources (priority order)
1. Existing local Hyperliquid public archive/cache at `data/hyperliquid_archive/` and `data/hyperliquid_asset_ctxs_staging/`.
2. Hyperliquid official/public archive helpers already implemented in this project (`hyperliquid_s3_archive.py`, `hyperliquid_asset_ctxs_archive.py`).
3. Bounded requester-pays S3 reads only if explicitly enabled by `--allow-s3-archive-read`.

Allowed source classes:
- Public node fills/trades archive (`node_fills_by_block/hourly/`), if locally cached.
- Public asset contexts / mark / OI archive.
- Public L2 book archive, if locally cached.
- Public `meta` snapshots for per-asset max leverage.
- Public candle/price snapshots for auxiliary price alignment.
- Existing project-generated local artifacts, if provenance is recorded.

Forbidden source classes:
- Private endpoints.
- Authenticated endpoints.
- Wallet/account/user endpoints.
- Live order endpoints.
- Any source requiring API keys.
- Any data source where historical liquidation-price reconstruction would require future information.

## Symbol Universe
Frozen non-BTC/ETH altcoin perp universe (33 symbols):

AAVE, ADA, APT, ARB, ATOM, AVAX, BCH, BNB, DOGE, DOT, ENA, FET, HYPE, INJ, JUP, LINK, LTC, MKR, NEAR, ONDO, OP, PENDLE, SEI, SOL, SUI, TIA, TON, TRX, UNI, WIF, WLD, XRP

If a symbol lacks required coverage, exclude it with an explicit coverage reason in `coverage_inventory.json`.

## Start Date
Default start date: `2025-08-17`.

Rationale: Reuses HLP/backstop-era cache whose effective start was `2025-08-17`. Using `2025-08-01` would create artificial coverage gaps against the actual cache. CLI allows override via `--start-date`, but the default run manifest must explain the chosen start date.

## Reconstruction Rules

### Margin Mode Classification
- Isolated-margin: margin mode explicitly `isolated` in fill metadata. Liquidation price is reconstructable from position-level data alone.
- Cross-margin: margin mode explicitly `cross` in fill metadata. Liquidation price depends on total account equity, other positions, spot balances, and unrealized PnL. Not reconstructable from public archive data.
- Undeterminable: margin mode not present or ambiguous. Excluded.

### Isolated-Margin-Only Scope
- Cross-margin positions must be excluded.
- Positions with undeterminable margin mode must be excluded.
- A cross-margin-dominant universe is an expected blocked outcome, not a bug.
- If the remaining isolated-margin reconstructed map covers too little contemporaneous OI, stop with a reconstruction/coverage blocked status.
- Do not produce a biased fragment-map and call it a liquidation-cluster map.

### Leverage/Maintenance-Margin Reconstruction
- Pull per-asset max leverage from public `meta` snapshots.
- Frozen maintenance-margin approximation:
  `maintenance_margin_fraction = 1 / (2 * max_leverage)`
- Record max leverage and maintenance fraction per symbol in `leverage_tier_snapshot.json`.
- If per-asset max leverage is unavailable, stop with `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_LIQ_PRICE_NOT_RECONSTRUCTABLE`.

### Position-State Burn-In
- Default burn-in window: 14 calendar days before the first eligible event timestamp.
- Event generation cannot begin until after burn-in completes.
- For each symbol and map timestamp, compute:
  `reconstructed_open_position_notional / contemporaneous_open_interest_notional`
- Contemporaneous OI uses the last OI observation at-or-before the map timestamp.

### Reconstruction-Completeness/OI Coverage Gates
- Frozen minimum reconstruction-completeness fraction: `0.40`.
- If a symbol's median completeness fraction over eligible map timestamps is < 0.40, drop the symbol with reason `RECONSTRUCTION_COVERAGE_FRACTION_LOW`.
- If fewer than 8 symbols remain after this gate, stop with `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_RECONSTRUCTION_COVERAGE_FRACTION_LOW`.
- If OI is unavailable, emit `OI_NORMALIZATION_UNAVAILABLE_DIAGNOSTIC` and do not pass final review unless the precommitment explicitly permits notional-only reconstruction coverage.

### Leverage-Tier Change Handling
- Record `meta` max leverage per symbol in `leverage_tier_snapshot.json`.
- If historical `meta` snapshots are available, detect tier-change timestamps.
- Refuse to carry reconstructed positions across an unrecorded leverage-tier change.
- If only a current `meta` snapshot exists, classify as `LEVERAGE_TIER_HISTORY_UNAVAILABLE_DIAGNOSTIC`.
- If the unavailable tier history affects too much of the reconstruction window, drop affected symbols or stop with `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_INSUFFICIENT_COVERAGE`.
- Do not use today's leverage tier silently across the entire historical window without a diagnostic.

### Isolated Liquidation Price Formula
For long isolated positions:
`liq_price_long = entry_price * (1 - initial_margin_fraction + maintenance_margin_fraction)`

For short isolated positions:
`liq_price_short = entry_price * (1 + initial_margin_fraction - maintenance_margin_fraction)`

Where:
- `initial_margin_fraction = 1 / leverage`
- `maintenance_margin_fraction = 1 / (2 * max_leverage)`
- `max_leverage` comes from public `meta`
- `leverage` must be reconstructable from isolated-margin position metadata; if unavailable, exclude or block.

### Lookahead Rules
- Approach velocity computed from trailing samples only.
- No centered windows.
- OI normalization uses the last OI observation at-or-before the map timestamp.
- Cluster dominance uses only prior data.
- Rolling percentile warmup is excluded from event generation.
- Forward-window liquidation/flow observations used only for post-hoc diagnostics, never for event selection.

## Cluster Map Rules
- Build liquidation levels from currently open reconstructed isolated-margin positions only.
- Classify long-liquidation levels below mark and short-liquidation levels above mark.
- Bucket liquidation levels by price distance using `cluster_bucket_width_bps = 25`.
- Compute cluster density as estimated isolated notional at risk in bucket.
- Normalize cluster density by contemporaneous symbol OI when OI is available.
- Cluster side:
  - `downside_long_liq_cluster`: long liquidations below current mark, expected continuation direction = short/down.
  - `upside_short_liq_cluster`: short liquidations above current mark, expected continuation direction = long/up.
- Dominant cluster threshold:
  - bucket notional >= max($100,000, 0.50% of contemporaneous OI)
  - AND bucket density >= past-only rolling 90th percentile for that symbol/side over at least 14 prior days
- The 14-day dominance-percentile warmup is excluded from event generation.

## Signal Rules
Generate an approach signal only when all are true:
- Current price is moving toward a dominant cluster.
- Distance to dominant cluster center is <= `approach_distance_bps = 100`.
- Distance is > `touch_distance_bps = 10`, so the signal occurs before contact.
- Trailing approach velocity is in the direction of the cluster.
- Same symbol/side has not fired within `cooldown_minutes = 60`.
- Cluster was known at or before signal timestamp.
- Cluster dominance was calculated from past-only data.
- Symbol has valid forward price coverage for at least 60m.
- Symbol passed the isolated reconstruction-completeness gate.
- BTC and ETH are excluded.

Signal direction:
- Approaching a long-liquidation cluster below price -> direction = short/down.
- Approaching a short-liquidation cluster above price -> direction = long/up.

## Return Horizons
Fixed-horizon returns: 15m, 30m, 60m.
Primary gate uses 60m unless event count is too low; 15m/30m reported as secondary diagnostics.

Path exit diagnostic:
- Breakthrough exit if price reaches cluster center and moves `breakthrough_bps = 25` beyond it in the expected direction.
- Decay exit if price moves away from the cluster by `decay_away_bps = 50` before touch.
- Time exit at 60m.

## Cost Assumptions
- Primary cost gate: net50 (50 bps round-trip taker fee + spread + slippage).
- Also report net10, net25, net75, net100 as diagnostics.
- Costs are round-trip and charged once per event.
- Funding is excluded in v0. Emit `FUNDING_EXCLUDED_DIAGNOSTIC`.
- Do not tune cost after seeing results.

## L2 Executable-vs-Mark-Return Hierarchy
- If executable L2 top-of-book is available, compute executable taker entry/exit and make L2 executable return the primary economic tier.
- Where L2 is available, mark-return is secondary diagnostic only.
- Where L2 is absent, mark-return diagnostics are allowed but cannot produce `LIQ_CLUSTER_PHASE0C_DIAGNOSTIC_SURVIVED_REVIEW_ALLOWED`.
- Do not pool mark-return-only events and L2-executable events into the same primary economic gate.
- Executable realism floor: estimate executable spread+impact at 100 USDC notional. Report median executable spread+impact bps by symbol and side. If median executable spread+impact to enter near the dominant cluster exceeds observed median gross continuation, classify as cost-wall blocked.

## Population Gates (Phase 0A)
Pass Phase 0A only if:
- Isolated liquidation reconstruction is feasible.
- Cross-margin/undetermined positions are excluded.
- Reconstruction-completeness fraction passes frozen 0.40 OI coverage gate.
- At least 8 symbols have usable reconstruction coverage.
- At least 300 accepted approach events total after cooldown.
- At least 50 accepted events in holdout.
- At least 6 distinct calendar weeks.
- Max symbol event share <= 25%.
- Max calendar week event share <= 25%.
- At least 2 sides represented unless one side genuinely has zero cluster tail.
- Dominant cluster density has a real tail: p90/p50 density ratio >= 2.0 and p95 density >= frozen minimum threshold.
- L2 availability is sufficient for primary economic evaluation, or final review is explicitly blocked to diagnostic-only mark-return mode.

## Economic Gates (Phase 0B)
For each cell and the pooled primary group, compute:
- event_count, distinct_symbols
- mean_gross_bps, median_gross_bps
- mean_net50_bps, median_net50_bps
- win_rate_net50
- worst_decile_net50_bps
- baseline_delta_net50_bps
- bootstrap 95% CI for mean_net50
- side breakdown, symbol breakdown, week/month breakdown
- funding-crossing breakdown
- forced-flow-proxy breakdown
- L2 executable spread+impact breakdown
- mark-return secondary diagnostic breakdown

Primary pooled Phase 0B pass requires:
- event_count >= 300
- holdout_event_count >= 50
- mean_net50_bps > 0
- median_net50_bps > 0
- win_rate_net50 >= 0.55
- worst_decile_net50_bps > -75
- baseline_delta_net50_bps >= 25
- mean_net75_bps > 0 as stress-cost diagnostic
- no symbol/week concentration failure
- executable L2 median gross continuation exceeds median executable spread+impact where L2 is primary
- no mark-only event pool used to claim final survival

Cells: side x horizon = 2 sides x 3 horizons = 6 cells.

## Null/Control Gates (Phase 0C)

### Controls
1. Velocity/momentum-matched random-level control:
   - Same symbol/timestamp, random pseudo-cluster at matched distance bucket.
   - Same approach-velocity bucket, same direction balance.
   - Seed: 20260530.
2. Non-dominant cluster control: weaker clusters below dominance threshold.
3. Side-flip control: invert signal direction on accepted events.

### Circular-Shift Null
- For each symbol/side, circular-shift cluster map timestamps by random offsets.
- Preserve event count and temporal structure.
- 1,000 iterations by default.
- Seed: 42.

### Control Gates
- Velocity/matched random-level mean_net50 must be materially below real cluster mean_net50.
- Non-dominant cluster control must be materially below dominant-cluster result.
- Side-flip control should be negative or materially worse.
- Circular-shift null p-value must be <= 0.05 for the primary pooled group.
- If velocity-matched random levels perform as well as real clusters: `LIQ_CLUSTER_PHASE0C_CONTROL_FAILED`.
- If shifted levels perform as well as real clusters: `LIQ_CLUSTER_PHASE0C_NULL_REJECTED`.

## Holdout and FDR
- Split events chronologically 70/30 after all signals are generated.
- Discovery split can be used to report exploratory gate pass.
- Holdout split is the primary confirmation.
- Benjamini-Yekutieli FDR at q=0.10 as the primary family correction across the 6 frozen cells.
- BH q=0.10 may be reported as diagnostic only.
- A pooled result can be reported, but it must not bypass failed cell-level FDR/holdout.
- If pooled passes but all cells fail FDR: `LIQ_CLUSTER_PHASE0C_FDR_BLOCKED`.

## Velocity/Momentum-Matched Controls
Implemented as described above. If velocity-matched random levels perform as well as real clusters, emit `LIQ_CLUSTER_PHASE0C_CONTROL_FAILED`.

## Forced-Flow Absorption Proxy Diagnostic
Post-hoc diagnostic split (not used for event selection):
- Inspect forward window around the predicted cluster level.
- Identify whether liquidation-type/forced-flow-like fills are observed near the cluster level using only public fill fields.
- If no explicit marker, use a conservative proxy labeled `FORCED_FLOW_PROXY_HEURISTIC`.
- Split returns into: `forced_flow_proxy_observed`, `forced_flow_proxy_not_observed`, `forced_flow_proxy_unknown`.
- If continuation is identical with and without observed forced-flow proxy, state plainly that the mechanism is not supported.

## Funding-Epoch Crossing Diagnostic
Hyperliquid funding is hourly. The 60m horizon frequently crosses a funding epoch.
- `funding_epoch_crossed: bool`
- `funding_sign_at_entry`
- `funding_direction_alignment`
- Subgroup metrics for funding-crossing vs non-crossing events.
- Subgroup metrics for direction-pays-funding vs direction-receives-funding.
- Diagnostic only, prominently reported in `summary.md`.

## Status Taxonomy

Allowed statuses:
- `LIQ_CLUSTER_PHASE_MINUS1_READY`
- `LIQ_CLUSTER_PHASE_MINUS1_DRY_RUN_READY`
- `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_NO_INPUT_DATA`
- `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_COST_OR_SIZE_CAP`
- `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_SCHEMA_UNRECOGNIZED`
- `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_LIQ_PRICE_NOT_RECONSTRUCTABLE`
- `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_RECONSTRUCTION_COVERAGE_FRACTION_LOW`
- `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_LOOKAHEAD_RISK`
- `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_INSUFFICIENT_COVERAGE`
- `LIQ_CLUSTER_PHASE0A_CLUSTER_TAIL_ABSENT`
- `LIQ_CLUSTER_PHASE0A_UNDERPOWERED_EVENTS`
- `LIQ_CLUSTER_PHASE0A_TEMPORAL_CONCENTRATION_FAILED`
- `LIQ_CLUSTER_PHASE0A_SYMBOL_CONCENTRATION_FAILED`
- `LIQ_CLUSTER_PHASE0A_MECHANISM_RECONSTRUCTABLE`
- `LIQ_CLUSTER_PHASE0B_RETURN_DIAGNOSTIC_FAIL`
- `LIQ_CLUSTER_PHASE0B_RETURN_DIAGNOSTIC_PASS`
- `LIQ_CLUSTER_PHASE0C_CONTROL_FAILED`
- `LIQ_CLUSTER_PHASE0C_NULL_REJECTED`
- `LIQ_CLUSTER_PHASE0C_HOLDOUT_FAILED`
- `LIQ_CLUSTER_PHASE0C_FDR_BLOCKED`
- `LIQ_CLUSTER_PHASE0C_DIAGNOSTIC_SURVIVED_REVIEW_ALLOWED`
- `LIQ_CLUSTER_PHASE0_ERROR_INVALID_OUTPUT`
- `LIQ_CLUSTER_PHASE0_ERROR_PRECOMMITMENT_MISMATCH`

Forbidden statuses:
- `REJECTED`
- `PROFITABLE`
- `ALPHA_FOUND`
- `TRADE_READY`
- `EXECUTION_READY`
- `LIVE_READY`
- `CANDIDATE_FOR_LIVE`
- `PAPER_STRATEGY_PROMOTED`
- `PROMOTION_AUTHORIZED`
- `EDGE_CONFIRMED`
- `READY_FOR_PHASE_1`
- `READY_FOR_PHASE_0`
- Any status implying trading authorization.

## Safety Constraints
- Public/archive data only.
- No orders.
- No private keys.
- No exchange auth.
- No signing.
- No wallet/account/user endpoints.
- No live execution.
- No paper trading.
- No shadow execution.
- No bot path changes.
- No systemd watcher changes.
- No conductor promotion.
- No `REJECTED_RESEARCH.md` mutation.
- No `TRADE_READY`, `EXECUTION_READY`, `LIVE_READY`, `CANDIDATE_FOR_LIVE`, `PAPER_STRATEGY_PROMOTED`, `PROMOTION_AUTHORIZED`, `EDGE_CONFIRMED`, `PROFITABLE`, or `ALPHA_FOUND` statuses.
- No `subprocess`, `os.system`, shelling to AWS from production code unless an existing project helper already safely wraps requester-pays archive access.
- No Nautilus Rust-backed imports in tests or CLI paths if they fail locally because of GLIBC mismatch.
- Use `orjson` by default for JSON read/write where appropriate, with binary file I/O and deterministic/canonical serialization helpers.
- Canonical JSON used for hashes must round floats to fixed decimal precision before hashing to avoid repr drift.

## Invalidation Conditions
- If isolated-margin liquidation reconstruction is not possible from public archive data without lookahead: stop with `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_LIQ_PRICE_NOT_RECONSTRUCTABLE`.
- If cross-margin/undetermined positions dominate the universe: stop with `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_INSUFFICIENT_COVERAGE`.
- If reconstruction coverage fraction < 0.40 for too many symbols: stop with `LIQ_CLUSTER_PHASE_MINUS1_BLOCKED_RECONSTRUCTION_COVERAGE_FRACTION_LOW`.
- If Phase 0A gates fail: do not proceed to economic claims.
- If Phase 0B fails: emit exact metrics, do not promote.
- If Phase 0C control fails: the result is explained by ordinary short-horizon momentum, not liquidation-cluster information.
- If forced-flow proxy does not support the mechanism: state plainly even if headline metrics are positive.

## No Registry Mutation
This study does not modify the HIP3 builder dex/tradfi registry. A `registry_note_preview.md` may be written but the registry itself is not mutated.

## No Promotion Path Beyond Review
If all diagnostic gates pass, the status is `LIQ_CLUSTER_PHASE0C_DIAGNOSTIC_SURVIVED_REVIEW_ALLOWED`. This does not unlock live, paper, shadow, bot, conductor promotion, or trading. A separate human-reviewed next precommitment is required for any paper/shadow/conductor work.

## CLI Parameters
- `--out-root`: output directory root (default: `reports/hyperliquid_liq_cluster_prepositioning_phase0_v0`)
- `--data-root`: data directory root (default: `data`)
- `--start-date`: start date (default: `2025-08-17`)
- `--end-date`: end date (default: `latest`)
- `--burn-in-days`: burn-in window in days (default: 14)
- `--min-reconstruction-coverage-fraction`: minimum completeness fraction (default: 0.40)
- `--max-download-bytes`: max download bytes (default: 25000000000, ~25 GiB)
- `--allow-s3-archive-read`: enable requester-pays S3 reads
- `--dry-run`: write preview artifacts, no data processing
- `--plan-only`: inventory data, estimate costs, no data processing
- `--skip-null`: skip circular-shift null (for tests/debug)
- `--null-iterations`: number of null iterations (default: 1000)
- `--seed`: random seed (default: 42)
- `--symbols`: comma-separated symbol override

## Precommitment Hash
SHA256 of this document (computed after final write):
`COMPUTED_AFTER_FILE_WRITE`
