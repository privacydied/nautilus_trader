# HIP-3 Oracle Residual Validation Audit - Phase -1

**Study ID:** `hip3_sonarx_tradfi_l2_residual_phase_minus1_v0`  
**Audit Date:** 2026-05-29  
**Branch:** `feat/hip3-sonarx-tradfi-l2-residual-phase-minus1-v0`  
**Commit:** `8f469160e7`

## Executive Summary

**Final Status:** `HL_ORACLE_CROSS_DEX_DISLOCATION_UNCONFIRMED`

**Precommitment Review Allowed:** NO

**Key Finding:** The observed `flx:TSLA` and `flx:NVDA` residuals are driven by **oracle feed discrepancies**, not mid-price dislocations. The flx builder DEX oracle shows a level shift relative to xyz/km/cash oracles that converges over the session. This is an oracle artifact, NOT a tradeable basis reversion.

## Critical Findings

### 1. Oracle Feed Displacement (NOT Mid-Price Dislocation)

| DEX | Oracle Deviation vs xyz | Interpretation |
|-----|------------------------|----------------|
| xyz | 0 bps (reference) | Reference oracle |
| km | ±2 bps | Tight tracking |
| cash | +15-18 bps | Systematic methodology offset? |
| **flx** | **+133bps → -15bps** | **Converging oracle feed discrepancy** |

**Evidence:**
- At session start (03:47): flx oracle = 435.73, xyz oracle = 430.00 (+133bps deviation)
- At session end (08:37): flx oracle = 434.25, xyz oracle = 434.89 (-15bps deviation)
- flx mid price tracks xyz oracle reasonably throughout the session
- The apparent "residual" (mid - flx_oracle) is large because flx_oracle itself is displaced

### 2. Reversion Classification

| Symbol | Classification | Median (bps) | Positive Share |
|--------|---------------|--------------|----------------|
| flx:TSLA | PERSISTENT_ORACLE_LEVEL_SHIFT_NOT_REVERSION | +14.2 | 63.2% |
| flx:NVDA | PERSISTENT_ORACLE_LEVEL_SHIFT_NOT_REVERSION | +10.7 | 62.0% |
| xyz:TSLA | UNDERPOWERED | -5.1 | 0.8% |
| km:TSLA | UNDERPOWERED | -0.6 | 38.0% |
| cash:TSLA | UNDERPOWERED | -5.4 | 0.4% |

**Interpretation:** The 2 detected "excursions" from >25bps to <10bps represent the **transition period** as the flx oracle converges from +133bps to near-zero. This is NOT mean reversion around a stable zero baseline.

### 3. Single-Day Limitation

- Oracle-bearing days: **1** (2026-05-28 only)
- Status: **FORWARD_RESIDUAL_SINGLE_DAY_DIAGNOSTIC**
- Cannot determine if oracle displacement is persistent cross-day or single-day artifact
- Multi-day validation required before mature conclusions

## Validation Results

| Check | Result | Details |
|-------|--------|---------|
| Raw data integrity | PASSED | 250 rows/symbol, no duplicates, all fields present |
| Symbol mapping | PASSED | All 12 target symbols present and correctly keyed |
| Residual recompute | MATCHED | Independent recompute confirms diagnostic values |
| Reversion pattern | NOT CONFIRMED | Pattern reflects oracle level shift, not tradeable reversion |
| Cross-DEX comparison | COMPLETED | flx oracle displacement confirmed |
| Liquidity validation | NOT PERFORMED | Requires SonarX L2 join (blocked by oracle finding) |

## Data Sources

- Primary file: `/mnt/nasirjones/py/nautilus_trader/reports/hip3_builder_dex_tradfi_forward_recorder_v0/9b41ea53/asset_context_snapshots/20260528.jsonl`
- Date range: 2026-05-28T03:47:24Z to 2026-05-28T08:37:52Z
- Total rows: 3000 (250 per symbol × 12 symbols)
- Oracle-bearing days: 1

## Recommended Next Steps

1. **Verify flx oracle feed source and methodology** vs xyz/km/cash
2. **Check multi-day oracle behavior** - does flx displacement persist?
3. **Understand cash:TSLA +15bps systematic oracle offset**
4. **Only after oracle behavior is understood**, proceed to L2 liquidity join (Step 10)
5. **Do NOT treat oracle artifact as tradeable basis** without oracle methodology reconciliation

## Safety Declaration

- [x] No orders submitted
- [x] No private keys used
- [x] No exchange authentication
- [x] No live/paper/shadow execution
- [x] No PnL or returns computed
- [x] No trade signals generated
- [x] Registry unchanged
- [x] Forward recorder untouched
- [x] Public data only
- [x] No subprocess/eval/os.system calls

## Artifacts Generated

- `hl_oracle_validation_artifact_inventory.json`
- `hl_oracle_forward_recorder_file_inventory.json`
- `hl_oracle_raw_context_integrity_audit.json`
- `hl_oracle_independent_residual_recompute.json`
- `hl_oracle_residual_reversion_vs_level_offset_audit.json`
- `hl_oracle_residual_timeseries.jsonl`
- `HL_ORACLE_REVERSION_LIQUIDITY_VALIDATION_AUDIT.json`
- `HL_ORACLE_REVERSION_LIQUIDITY_VALIDATION_AUDIT.md` (this file)

## Appendix: Raw Evidence

### flx:TSLA Oracle vs Mid Price Time Series (Sampled)

| idx | timestamp | oracle | mid | mid-oracle (bps) | regime |
|-----|-----------|--------|-----|------------------|--------|
| 0 | 03:47:25 | 435.73 | 430.19 | -127.0 | Q1: negative |
| 50 | 04:45:34 | 431.92 | 429.50 | -56.1 | Q2: transition |
| 100 | 05:43:37 | 431.05 | 432.56 | +35.1 | Q3: positive |
| 150 | 06:42:12 | 432.81 | 433.44 | +14.6 | Q4: settled |
| 200 | 07:40:19 | 433.61 | 434.27 | +15.3 | Q5: settled |
| 249 | 08:37:45 | 434.25 | 435.00 | +17.4 | Q5: settled |

### Cross-DEX Oracle Comparison (Session Start)

| DEX | Oracle Price | Deviation from xyz (bps) |
|-----|--------------|-------------------------|
| xyz | 430.00 | 0.0 |
| km | 430.06 | +1.4 |
| cash | 430.80 | +18.7 |
| **flx** | **435.73** | **+133.3** |

### Cross-DEX Oracle Comparison (Session End)

| DEX | Oracle Price | Deviation from xyz (bps) |
|-----|--------------|-------------------------|
| xyz | 434.89 | 0.0 |
| km | 435.05 | +3.7 |
| cash | 435.56 | +15.4 |
| **flx** | **434.25** | **-14.7** |

---

**Conclusion:** The flx oracle residual is an **oracle feed artifact**, not a tradeable dislocation. Precommitment review is NOT allowed until oracle methodology is reconciled and multi-day behavior is validated.