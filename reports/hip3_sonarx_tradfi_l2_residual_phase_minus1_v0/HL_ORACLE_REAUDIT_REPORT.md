# HL Oracle Anchor Re-audit - Falsification Report

**Study ID**: hip3_sonarx_tradfi_l2_residual_phase_minus1_v0  
**Date**: 2026-05-29  
**Branch**: feat/hip3-sonarx-tradfi-l2-residual-phase-minus1-v0  
**Commit**: 8f469160e7

## Executive Summary

The prior conclusion `HL_ORACLE_SOURCE_UNAVAILABLE` (from run ff5c0627) was a **FALSE NEGATIVE** caused by:

1. Querying the Hyperliquid info endpoint without explicit `dex` parameter
2. Failing to check forward recorder artifacts that already capture oraclePx

**Actual status**: `FORWARD_RECORDER_ORACLE_CONTEXTS_AVAILABLE` and `NEXT_PRECOMMITMENT_REVIEW_ALLOWED`

## Root Cause of Prior False Negative

The prior oracle audit queried:
```json
{"type": "metaAndAssetCtxs"}
```

This returns the **default perp universe only**, which does NOT include HIP-3 builder DEX symbols (xyz:TSLA, flx:TSLA, km:TSLA, cash:TSLA, etc.).

The correct query requires explicit `dex` parameter:
```json
{"type": "metaAndAssetCtxs", "dex": "xyz"}
{"type": "metaAndAssetCtxs", "dex": "flx"}
{"type": "metaAndAssetCtxs", "dex": "km"}
{"type": "metaAndAssetCtxs", "dex": "cash"}
```

Additionally, the audit did not check existing forward recorder artifacts, which have been capturing `oracle_price`, `mark_price`, and `mid_price` for all 12 HIP-3 markets all along.

## Verified Evidence

### 1. Forward Recorder Oracle Contexts

**Location**: `/mnt/nasirjones/py/nautilus_trader/reports/hip3_builder_dex_tradfi_forward_recorder_v0/1aa988da/asset_context_snapshots/20260528.jsonl`

**Schema**:
- `timestamp_utc`: ISO8601 with microseconds
- `api_symbol`: e.g., "xyz:TSLA"
- `dex_name`: e.g., "xyz"
- `oracle_price`: float
- `mark_price`: float
- `mid_price`: float
- `funding`: float
- `open_interest`: float

**Coverage**:
| API Symbol | Samples | Oracle | Mark | Mid |
|------------|---------|--------|------|-----|
| xyz:TSLA   | 250     | 250    | 250  | 250 |
| flx:TSLA   | 250     | 250    | 250  | 250 |
| km:TSLA    | 250     | 250    | 250  | 250 |
| cash:TSLA  | 250     | 250    | 250  | 250 |
| xyz:AAPL   | 250     | 250    | 250  | 250 |
| km:AAPL    | 250     | 250    | 250  | 250 |
| xyz:MSFT   | 250     | 250    | 250  | 250 |
| cash:MSFT  | 250     | 250    | 250  | 250 |
| xyz:NVDA   | 250     | 250    | 250  | 250 |
| flx:NVDA   | 250     | 250    | 250  | 250 |
| km:NVDA    | 250     | 250    | 250  | 250 |
| cash:NVDA  | 250     | 250    | 250  | 250 |

**All 12 markets have 250 aligned oracle samples each.**

### 2. Live API with Explicit DEX

Query: `{"type": "metaAndAssetCtxs", "dex": "xyz"}`

Response shape: `[meta, asset_ctxs]` (index-aligned arrays)

- `meta.universe[i].name`: symbol name (e.g., "xyz:TSLA")
- `asset_ctxs[i].oraclePx`: oracle price
- `asset_ctxs[i].markPx`: mark price
- `asset_ctxs[i].midPx`: mid price

**Result**: All builder DEXes return oraclePx when queried explicitly.

## Forward Residual Diagnostic Results

**Overall Status**: `HL_ORACLE_FORWARD_RESIDUAL_AVAILABLE`

### Key Findings by Symbol

| Symbol | Median (bps) | P90 Abs (bps) | P99 Abs (bps) | Max Abs (bps) | ≥10 bps | ≥25 bps | Oracle Diagnostic |
|--------|--------------|---------------|---------------|---------------|---------|---------|-------------------|
| xyz:TSLA | -8.6 | 9.1 | 11.0 | 12.7 | 11 | 0 | HAS_TAILS |
| **flx:TSLA** | **+47.8** | **101.7** | **124.0** | **127.0** | **230** | **152** | HAS_TAILS |
| km:TSLA | -0.6 | 3.8 | 7.1 | 7.7 | 0 | 0 | HAS_TAILS |
| cash:TSLA | -7.9 | 8.6 | 11.0 | 14.6 | 9 | 0 | HAS_TAILS |
| xyz:AAPL | -0.3 | 6.8 | 9.0 | 10.1 | 1 | 0 | HAS_TAILS |
| km:AAPL | -0.2 | 4.0 | 7.9 | 10.1 | 1 | 0 | HAS_TAILS |
| xyz:MSFT | -3.5 | 5.1 | 7.5 | 10.7 | 1 | 0 | HAS_TAILS |
| cash:MSFT | -2.2 | 6.7 | 10.4 | 11.8 | 3 | 0 | HAS_TAILS |
| xyz:NVDA | +6.4 | 9.2 | 12.5 | 13.0 | 13 | 0 | HAS_TAILS |
| **flx:NVDA** | **+34.7** | **45.3** | **68.0** | **71.0** | **204** | **75** | HAS_TAILS |
| km:NVDA | +0.8 | 4.9 | 9.2 | 10.1 | 2 | 0 | HAS_TAILS |
| cash:NVDA | -0.2 | 3.9 | 6.7 | 8.9 | 0 | 0 | HAS_TAILS |

### Critical Observation: Cross-DEX Dislocation

**flx DEX shows systematically wide oracle basis**:
- flx:TSLA: median +47.8 bps (vs xyz/km/cash at <10 bps)
- flx:NVDA: median +34.7 bps (vs xyz/km/cash at <10 bps)

This is **not noise** - it's a real structural dislocation between builder DEXes. The flx oracle appears to be persistently higher than the mid price, or equivalently, the flx mid is persistently higher than the oracle.

**Interpretation**: This could represent:
1. Arbitrage opportunity between DEXes (if executable)
2. Oracle manipulation/staleness on flx
3. Different oracle sources per DEX
4. Market structure differences (liquidity, leverage limits)

**This is diagnostic-only** - not a trade signal, not PnL, not execution-ready.

## Final Status Determination

### Allowed Status
`NEXT_PRECOMMITMENT_REVIEW_ALLOWED`

### Criteria Met
- ✅ L2 data-bearing preserved (SonarX L2 parse OK)
- ✅ HL oracle proven present (forward recorder + explicit dex query)
- ✅ Residual diagnostic has non-trivial aligned samples (250 per symbol)
- ✅ Sample not trivially underpowered (≥100 threshold met)
- ✅ Multiple session buckets represented (premarket, regular, after hours)
- ✅ No single market/DEX accounts for all evidence (flx-wide, others tight)
- ✅ Safety grep passes (no forbidden implementations)
- ✅ No PnL/returns/signals/trading path exists

### Forbidden Status (NOT used)
- ❌ REJECTED
- ❌ PROFITABLE
- ❌ ALPHA_FOUND
- ❌ TRADE_READY
- ❌ EXECUTION_READY
- ❌ READY_FOR_PHASE_0

## Safety Confirmation

- ✅ No orders submitted
- ✅ No private keys used
- ✅ No exchange auth performed
- ✅ No live execution
- ✅ No paper execution
- ✅ No conductor promotion
- ✅ No registry mutation
- ✅ No PnL calculations
- ✅ No returns analysis
- ✅ No trade signals generated
- ✅ No entry/exit logic
- ✅ No position sizing
- ✅ Forward recorder service untouched/running

## Artifacts Produced

1. `hl_oracle_reaudit_baseline.json` - Reason for re-audit
2. `forward_recorder_oracle_context_audit.json` - Recorder schema verification
3. `hl_oracle_forward_residual_diagnostic.json` - Per-symbol residual statistics
4. `hl_oracle_forward_residual_diagnostic.md` - Human-readable summary
5. `hl_oracle_reatudit_final.json` - Final falsification record
6. `HL_ORACLE_REAUDIT_REPORT.md` - This document

## Next Steps (For Future Prompts)

1. **Do NOT close out Phase -1 yet** - oracle path is now unblocked
2. **Consider extending forward recorder** to capture more oracle context fields if needed
3. **Investigate flx oracle dislocation** - is thisstructural, temporary, or arb opportunity?
4. **Run historical SonarX residual** if timestamp-aligned historical oracle contexts can be sourced
5. **Cross-DEX diagnostic** - anchor-free analysis of L2 dislocations between xyz/flx/km/cash

## Lessons Learned

1. **Always check forward recorder artifacts first** before querying live APIs
2. **Explicit dex parameters required** for builder DEX queries to Hyperliquid
3. **Index-aligned parsing** critical: `meta.universe[i]` ↔ `asset_ctxs[i]`
4. **False negatives from default-universe queries** are a recurring pattern (cf. HIP-3 symbol discovery)
5. **Oracle-basis residuals are feasible** with existing forward recorder data

---

**Registry Status**: UNCHANGED  
**Forward Recorder**: UNTOUCHED  
**Push Status**: SUCCESS (8f469160e7 to fork/feat/hip3-sonarx-tradfi-l2-residual-phase-minus1-v0)