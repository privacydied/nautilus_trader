# HIP-3 Builder Discovery Gap Audit v0

**Study ID:** `hip3_builder_discovery_gap_audit_v0`

**Phase:** -1 (Feasibility Gap Audit)

**Date:** 2026-05-27

---

## What This Is NOT

- This is **not** a strategy.
- This is **not** a precommitment.
- This is **not** a PnL evaluator.
- This **cannot** emit candidate/promotion/live/paper verdicts.
- This **cannot** mutate `REJECTED_RESEARCH.md`.
- This **does not** authorize Phase 0 execution.
- This **does not** claim HIP-3 builder-deployed perps don't exist.

## What This Is

A data-plane gap audit to determine whether actual HIP-3 builder-deployed equity/index/commodity perp metadata and archive paths are publicly discoverable from the canonical NautilusTrader repo environment.

## Motivation

The prior scout run (`hip3_offhours_oracle_basis_residual_scout_v0`) executed on 2026-05-27 produced `HIP3_NO_OFFHOURS_RESIDUAL_BASIS_TAIL` but only found **one index-like symbol: SPX**. That SPX symbol has:
- Archive history back to 2023
- No deployer/builder namespace in metadata
- marginTableId=5 (shared with other crypto perps like ATOM, DYDX, APE, OP)
- maxLeverage=5x

This is **incompatible** with the original HIP-3 builder-deployed hypothesis surface, which expects:
- Builder-deployed markets (validator-operated legacy ≠ builder-deployed)
- Deployer namespace identification
- Potentially different fee regimes
- Potentially different archive paths

Therefore, the prior result should be treated as a **narrow SPX-as-discovered / daily-anchor / sample-bounded no-tail diagnostic**, NOT a rejection of the HIP-3 builder-deployed family.

## Audit Questions

1. Does the Hyperliquid public API expose builder/deployer metadata for any perp?
2. Are there builder-specific archive paths on S3?
3. Can we distinguish validator-operated legacy perps from builder-deployed HIP-3 perps via public metadata?
4. Was SPX from the prior scout actually a builder-deployed perp or a legacy validator-operated perp?
5. If actual builder-deployed symbols exist, what are their identifiers?

## Methodology

### 1. Public API Metadata Scan

Query `https://api.hyperliquid.xyz/info` with:
- `{"type": "meta"}` — universe listing
- `{"type": "metaAndAssetCtxs"}` — detailed universe + contexts
- `{"type": "spotMeta"}` — spot universe (for comparison)

Check for fields:
- `deployer`
- `builder`
- `namespace`
- `hip3`
- `marginMode` (builder-deployed might use different margin modes)
- `onlyIsolated` (builder-deployed might be isolated-only)
- Any custom fields beyond standard crypto perp schema

### 2. Archive Path Discovery

Check S3 paths:
- `s3://hyperliquid-archive/asset_ctxs/` — already confirmed working
- `s3://hyperliquid-archive/market_data/` — L2 data
- Potential builder-specific paths (if discoverable)

Note: S3 bucket is Requester-Pays, so anonymous listing is blocked. The scout code uses `aws s3 cp` with requester-pays acknowledgement.

### 3. Symbol Classification

Classify discovered symbols into:
- `index_like` — SPX, NDX, US500, NAS100, etc.
- `single_stock_like` — AAPL, MSFT, NVDA, TSLA, etc.
- `commodity_like` — GOLD, SILVER, OIL, WTI, BRENT
- `crypto_like` — BTC, ETH, SOL, etc.
- `unknown`

Flag any symbol with:
- Deployer/builder namespace
- Non-crypto ticker pattern
- Unusual margin table ID
- Low max leverage (experimental products)

## Required Diagnostic Statuses

| Status | Meaning |
|--------|---------|
| `HIP3_BUILDER_DISCOVERY_READY` | Audit completed, data collected |
| `HIP3_BUILDER_METADATA_FOUND` | Builder/deployer metadata was publicly discoverable |
| `HIP3_BUILDER_ARCHIVE_FOUND` | Builder-specific archive paths found |
| `HIP3_BUILDER_METADATA_NOT_PUBLIC` | No builder/deployer metadata in public API |
| `HIP3_BUILDER_ARCHIVE_NOT_PUBLIC` | No builder-specific archive paths discoverable |
| `HIP3_PRIOR_SPX_NOT_BUILDER_DEPLOYED` | SPX from prior scout is legacy validator-operated |
| `HIP3_DISCOVERY_GAP_UNRESOLVED` | Cannot determine builder vs legacy distinction publicly |
| `HIP3_DISCOVERY_AUDIT_ERROR` | Audit failed due to technical error |

## Forbidden Statuses

Same as the scout:
- `REJECTED`, `CANDIDATE`, `CANDIDATE_FOR_LIVE`, `TRADE_READY`, `EXECUTION_READY`, `LIVE_READY`
- `PAPER_STRATEGY_PROMOTED`, `PROMOTION_AUTHORIZED`, `EDGE_CONFIRMED`, `PROFITABLE`, `ALPHA_FOUND`, `READY_FOR_PHASE_0`

## Safety Constraints

- Public data only
- No private keys
- No API keys
- No auth
- No orders
- No execution
- No paper trading
- No strategy promotion
- No registry mutation

## Expected Outputs

### summary.json

```json
{
  "study_id": "hip3_builder_discovery_gap_audit_v0",
  "run_id": "<timestamp_hash>",
  "status": "<diagnostic status>",
  "symbols_scanned": 230,
  "builder_symbols_found": [],
  "deployer_namespaces_observed": [],
  "archive_paths_checked": ["asset_ctxs", "market_data", ...],
  "builder_archive_paths_found": [],
  "spx_classification": "legacy_validator_operated",
  "hip3_hypothesis_status": "NOT_TESTED_WRONG_INSTRUMENT",
  "next_steps_recommendation": "..."
}
```

### summary.md

Human-readable report with:
- What was checked
- What was found (or not found)
- Whether SPX was the wrong instrument class
- Whether HIP-3 hypothesis remains untested
- Whether future scout on actual builder symbols is warranted
- Recommended registry posture: `NEEDS_MORE_DATA` vs `REJECTED`

## Success Criteria

The audit succeeds if it:
1. Clearly determines whether builder-deployed metadata is publicly accessible
2. Classifies SPX from the prior run as legacy or builder-deployed
3. Provides actionable next steps (e.g., "HIP-3 requires non-public metadata" or "Builder symbols found at X")
4. Does not overclaim — if data is not public, say so clearly

## Failure Modes

- Hyperliquid API changes schema
- Builder metadata exists but under undocumented field names
- S3 archive paths are requester-pays and cannot be enumerated anonymously
- Builder-deployed perps use the same metadata schema as legacy perps

Any failure mode must be reported explicitly, not hidden.

---

## Appendix: Prior Scout Findings

**SPX Characteristics:**
- `szDecimals`: 1
- `maxLeverage`: 5
- `marginTableId`: 5
- `marginMode`: not present
- `onlyIsolated`: not present
- `deployer`: not present
- `builder`: not present
- `namespace`: not present

**Symbols sharing marginTableId=5:**
ATOM, DYDX, APE, OP, INJ, LDO, STX, CFX, COMP, FXS, ... (all crypto perps)

**Conclusion from metadata alone:**
SPX appears in the same margin table as crypto perps, with no distinguishing builder/deployer fields. This suggests SPX is either:
1. A legacy validator-operated perp that happens to have an equity-like ticker, OR
2. A builder-deployed perp that does not expose builder metadata via public API

Either way, the prior scout tested **SPX as discovered**, not **HIP-3 builder-deployed perps as hypothesized**. The HIP-3 family remains untested.