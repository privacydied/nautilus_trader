# HIP-3 FLX Stale-Oracle Funding-Bias Phase -2 Reachability Probe

## Purpose

Observer-only diagnostic: does `flx` HIP-3 builder DEX oracle staleness create a persistent, signed, lag-driven oracle bias versus active same-underlying builder DEX reference oracles, slow enough that a later funding-distortion Phase 0 may be worth drafting?

## What This Is NOT

- NOT a strategy
- NOT a PnL evaluator
- NOT a return backtest
- NOT paper trading
- NOT live trading
- NOT a registry mutation
- NOT a TRADE_READY / EXECUTION_READY / LIVE_READY / PROFITABLE / ALPHA_FOUND determination
- NOT reading or modeling actual funding data

## Hypothesis

`flx` oracle updates are sparse or stale relative to active builder DEX oracles (`cash`, `km`, `xyz`, `para`). When `flx` is stale, the residual `flx_px / ref_px - 1` is:

1. **Signed and persistent** (not symmetric/noisy)
2. **Lag-driven** (correlates with reference price movement during the stale gap)
3. **Slow enough** (persists across a funding-clock proxy) to plausibly distort funding

If all three conditions hold, a future Phase 0 that measures actual funding distortion directly may be worth drafting.

## Data Sources

- Hyperliquid public S3 archive `replica_cmds` (requester-pays)
- LZ4-compressed, newline-delimited JSON records
- Decoded via `abci_block.signed_action_bundles[i][1].signed_actions[j].action`
- Oracle prices nested under: `action.perpDeploy.setOracle.oraclePxs`

## Decoding Path

```
obj -> abci_block -> signed_action_bundles[i] -> [sig_hex, {signed_actions}]
    -> signed_actions[j] -> {action: {type: "perpDeploy", setOracle: {oraclePxs: [...]}}}
```

`oraclePxs` entries are either:
- `[market, price]` tuples (actual S3 format): `["flx:TSLA", "435.07"]`
- `{coin, px}` dicts: `{"coin": "flx:TSLA", "px": 435.07}`

## Target/Reference Symbol Rules

- **Target**: `flx:<SYMBOL>` (always `flx`)
- **References**: `cash:<SYMBOL>`, `km:<SYMBOL>`, `xyz:<SYMBOL>`, `para:<SYMBOL>`
- Primary reference = active DEX with highest update count
- Consensus reference = median price across active references (if >= 2 available)
- Primary symbols: TSLA, NVDA
- Secondary (if discoverable): AAPL, MSFT

## Warning: Reference DEX Oracle Is Not Real-World Truth

`cash:NVDA`, `km:NVDA`, `xyz:NVDA`, etc. are **also** HIP-3 deployer oracles, likely weighted or medianized from one or more CEX references on deployer-specific cadence and methodology.

Therefore:

```
flx_px / cash_px - 1
```

measures **`flx oracle minus cash oracle`**, not **`flx oracle minus real-world NVDA`**.

A persistent signed residual can be a **methodology offset** rather than stale-oracle lag. The probe must explicitly distinguish:

1. **Real stale lag**: reference moves, `flx` does not update, residual grows in direction of reference move, persists long enough to matter on a funding clock
2. **Methodology offset**: residual is signed and persistent but does NOT track reference movement during stale gaps
3. **Symmetric/noisy residual**: no stable signed effect
4. **Real lag but too short-lived**: stale gap exists but reverts before funding-clock proxy

## Frozen Thresholds

| Parameter | Default | Purpose |
|---|---|---|
| `min_target_updates` | 30 | Minimum `flx` oracle updates |
| `min_reference_updates` | 1000 | Minimum reference DEX updates |
| `min_aligned_observations` | 500 | Minimum aligned reference-vs-target pairs |
| `lag_correlation_threshold` | 0.30 | Minimum correlation between residual and reference return |
| `funding_interval_seconds` | 3600 | Funding-clock proxy interval |
| `min_funding_clock_persistence_share` | 0.25 | Min fraction of large residuals persisting across funding clock |
| `bootstrap_iterations` | 1000 | Bootstrap CI iterations |
| `seed` | 20260529 | Random seed for reproducibility |
| `extend_backward_days` | 14 | Extend backward search for target baseline |
| `download_budget_bytes` | 3GB | Default download budget (cheap-probe cap) |
| `max_files` | 8 | Max files per date |

## Naive Signed-Bias Gate

Passes if ALL are true:

```
aligned_observations >= min_aligned_observations
target_update_count >= min_target_updates
reference_update_count >= min_reference_updates
abs(mean_signed_residual_bps) >= 10.0
abs(median_signed_residual_bps) >= 5.0
dominant_sign_share >= 0.65
bootstrap 95% CI for mean excludes 0
stale_age_p90_seconds >= funding_interval_seconds
```

This gate alone does NOT emit `BIASED_REACHABILITY_PASSED`.

## Lag-Mechanism Correlation Gate

Compute Pearson correlation between:

```
signed_residual_bps
reference_return_since_target_update_bps
```

Passes if:

```
lag_mechanism_correlation >= lag_correlation_threshold (default 0.30)
```

Interpretation: if `flx` is genuinely stale, then when the reference has moved far since `flx` last updated, the residual should be large in the same direction.

## Funding-Clock Persistence Proxy Gate

```
funding_clock_persistence_share =
  count(aligned rows with stale_gap >= funding_interval AND abs(residual) >= 10)
  / count(aligned rows with abs(residual) >= 10)
```

Passes if:

```
funding_clock_persistence_share >= min_funding_clock_persistence_share (default 0.25)
```

## Target Update Floor

```
target_update_count >= min_target_updates (default 30)
```

If `target_update_count == 0` and no baseline found via `--extend-backward-days`, emit `HIP3_FLX_ORACLE_BIAS_UNMEASURABLE_NO_FLX_BASELINE`.

If below threshold but > 0, emit `HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_TARGET`.

## Output Files

All artifacts written under:

```
reports/hip3_flx_stale_oracle_funding_bias_phase_minus2_v0/<run_id>/
```

Required files:

- `run_manifest.json` — full run provenance
- `input_plan.json` — CLI parameters used
- `decode_inventory.json` — decode statistics
- `decoded_oracle_updates.jsonl` — extracted oracle updates
- `oracle_update_counts_by_dex_symbol.csv` — per-dex-symbol counts
- `oracle_alignment_rows.jsonl` — alignment rows
- `bias_metrics_by_symbol_reference.csv` — bias metrics per symbol/reference pair
- `gate_decisions.json` — gate pass/fail per symbol/reference pair
- `summary.json` — final summary
- `summary.md` — human-readable summary

## Allowed Statuses

```
HIP3_FLX_ORACLE_BIAS_PHASE_MINUS2_READY
HIP3_FLX_ORACLE_BIAS_PLAN_READY
HIP3_FLX_ORACLE_BIAS_DRY_RUN_READY
HIP3_FLX_ORACLE_BIAS_REFERENCE_ACTIVE_FLX_STALE_CONFIRMED
HIP3_FLX_ORACLE_BIAS_BIASED_REACHABILITY_PASSED
HIP3_FLX_ORACLE_BIAS_PERSISTENT_METHODOLOGY_OFFSET
HIP3_FLX_ORACLE_BIAS_LAG_REAL_BUT_SUB_FUNDING_CLOCK
HIP3_FLX_ORACLE_BIAS_SYMMETRIC_NO_SLOW_EDGE
HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_TARGET
HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_REFERENCE
HIP3_FLX_ORACLE_BIAS_UNDERPOWERED_ALIGNMENT
HIP3_FLX_ORACLE_BIAS_UNMEASURABLE_NO_FLX_BASELINE
HIP3_FLX_ORACLE_BIAS_BLOCKED_REPLICA_CMDS_ACCESS
HIP3_FLX_ORACLE_BIAS_BLOCKED_COST_OR_SIZE_CAP
HIP3_FLX_ORACLE_BIAS_BLOCKED_SCHEMA_UNRECOGNIZED
HIP3_FLX_ORACLE_BIAS_ERROR_INVALID_OUTPUT
```

## Forbidden Statuses

```
REJECTED
CANDIDATE
CANDIDATE_FOR_LIVE
TRADE_READY
EXECUTION_READY
LIVE_READY
PAPER_STRATEGY_PROMOTED
PROMOTION_AUTHORIZED
EDGE_CONFIRMED
PROFITABLE
ALPHA_FOUND
READY_FOR_PHASE_0
```

## Safety Constraints

- No orders
- No private keys
- No signing
- No wallet/account endpoints
- No exchange auth
- No private API calls
- No live execution
- No paper trading
- No shadow executor
- No Nautilus live adapter
- No bot path changes
- No systemd changes
- No conductor promotion
- No PnL/backtest/returns evaluator
- No strategy position sizing
- No registry mutation
- No TRADE_READY / EXECUTION_READY / LIVE_READY
- No PROFITABLE / ALPHA_FOUND / EDGE_CONFIRMED

Allowed public data only:

- Hyperliquid public archive / S3 `replica_cmds` behind explicit `--allow-s3-archive-read`
- Hyperliquid public `/info` endpoint only if needed for symbol discovery behind `--allow-network-public`
- Local prior artifacts only if paths are provided explicitly by CLI

## Interpretation Guide

| Status | Meaning |
|---|---|
| `BIASED_REACHABILITY_PASSED` | Oracle staleness is signed, lag-driven, and slow enough that a future Phase 0 review may be worth drafting. Does NOT authorize Phase 0 execution. |
| `PERSISTENT_METHODOLOGY_OFFSET` | Persistent signed bias exists but does NOT track reference movement. Likely a methodology offset between deployer oracle definitions, not stale lag. |
| `LAG_REAL_BUT_SUB_FUNDING_CLOCK` | Lag mechanism detected but residual episodes revert before the funding-clock proxy. Likely B-fast/latency-only, not B-slow. |
| `SYMMETRIC_NO_SLOW_EDGE` | No stable signed effect. B-slow is not reachable from this evidence. |
| `UNDERPOWERED_TARGET` | Not enough `flx` oracle updates to measure bias. |
| `UNDERPOWERED_REFERENCE` | Not enough reference DEX updates to measure bias. |
| `UNDERPOWERED_ALIGNMENT` | Not enough aligned observations. |
| `UNMEASURABLE_NO_FLX_BASELINE` | No `flx` oracle updates found at all. Cannot measure. |

## Key Statements

- This probe does NOT read or model actual funding data.
- This probe does NOT confirm funding distortion exists.
- This probe only checks whether oracle staleness is signed, lag-driven, and slow enough that actual funding distortion is plausible enough to inspect in a separate Phase 0.
- This gates only whether to draft a later Phase 0 that measures funding directly.
- This does NOT mutate `REJECTED_RESEARCH.md`.
- This does NOT authorize Phase 0, paper, shadow, live, or bot changes.
- `HIP3_FLX_ORACLE_BIAS_BIASED_REACHABILITY_PASSED` means only: "a future Phase 0 review may be worth drafting."

## CLI Usage

```bash
uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_flx_stale_oracle_funding_bias_phase_minus2_v0 \
  --out-root reports/hip3_flx_stale_oracle_funding_bias_phase_minus2_v0 \
  --symbols TSLA,NVDA \
  --reference-dexes cash,km,xyz,para \
  --target-dex flx \
  --sample-dates 2026-05-23,2026-05-26,2026-05-27,2026-05-28 \
  --max-files 8 \
  --download-budget-bytes 3000000000 \
  --allow-s3-archive-read
```

```bash
# Dry run
uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_flx_stale_oracle_funding_bias_phase_minus2_v0 \
  --dry-run

# Plan only
uv run python -m examples.strategies.venue_agnostic_signal_observer.run_hip3_flx_stale_oracle_funding_bias_phase_minus2_v0 \
  --stop-after-plan \
  --allow-s3-archive-read
```
