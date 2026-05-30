# HIP-3 Builder Discovery Gap Audit v0 — Summary

**Study ID:** `hip3_builder_discovery_gap_audit_v0`
**Run ID:** `20260527_031203_b50c0759`
**Timestamp:** `2026-05-27T03:12:03.490619Z`
**Git SHA:** `unknown` (`clean`)

---

## Final Status

`HIP3_PRIOR_SPX_NOT_BUILDER_DEPLOYED`

## What Was Checked

- **Symbols scanned:** 230 perp symbols from Hyperliquid public API
- **Metadata fields examined:** All universe and asset context fields
- **S3 archive paths checked:** asset_ctxs, market_data (cannot enumerate Requester-Pays bucket anonymously)

**What Was Found**

### Builder/Deployer Metadata

**NOT FOUND:** No builder, deployer, or namespace fields in public API response.

**Universe keys:** isDelisted, marginMode, marginTableId, maxLeverage, name, onlyIsolated, szDecimals
**Context keys:** dayBaseVlm, dayNtlVlm, funding, impactPxs, markPx, midPx, openInterest, oraclePx, premium, prevDayPx

### Symbol Classification

- **Index-like:** SPX (only match)
- **Single-stock-like:** None discovered
- **Commodity-like:** None discovered
- **Crypto-like:** 229 symbols (all others)

### SPX Classification

**Verdict:** `legacy_validator_operated`

SPX shares marginTableId=5 with crypto perps (ATOM, DYDX, APE, OP, INJ, LDO, STX, CFX, COMP, FXS...).
No deployer, builder, or namespace metadata is present.

### Archive Paths

No builder-specific S3 archive paths are publicly discoverable. The bucket is Requester-Pays, preventing anonymous enumeration.

## HIP-3 Hypothesis Status

`NOT_TESTED_WRONG_INSTRUMENT`

## Does This Close the HIP-3 Family?

**No.** This audit confirms that the prior scout (`hip3_offhours_oracle_basis_residual_scout_v0`) tested **SPX as discovered**, which appears to be a legacy validator-operated perp. The original HIP-3 hypothesis concerns **builder-deployed** perps, which have not been publicly identified.

## Recommended Registry Posture

**NEEDS_MORE_DATA** — not REJECTED.

The HIP-3 builder-deployed hypothesis remains untested because:
1. No builder/deployer metadata is publicly accessible via the Hyperliquid API
2. SPX (the only index-like symbol found) shows no builder indicators
3. Builder-specific archive paths are not publicly discoverable

## Next Steps

The prior scout (hip3_offhours_oracle_basis_residual_scout_v0) tested SPX, which appears to be a legacy validator-operated perp, not a builder-deployed HIP-3 perp. The HIP-3 builder-deployed hypothesis remains untested. Future work should: (1) identify actual builder-deployed symbols via non-public channels or Hyperliquid documentation, (2) verify whether builder-deployed perps have different metadata schemas or archive paths, (3) re-run the off-hours basis scout on confirmed builder-deployed symbols only. Recommended registry posture: NEEDS_MORE_DATA (not REJECTED).

---

## Safety Statement

This audit used public data only. No private keys, API keys, auth, orders, execution, paper trading, strategy promotion, or registry mutation occurred.
