# Product Audit — venue_agnostic_signal_observer

**Audit date:** 2026-05-20
**Audit version:** v1
**Base directory:** `examples/strategies/venue_agnostic_signal_observer`
**Auditor:** Hermes Agent (automated product-audit skill)

---

## 1. Executive Summary

The `venue_agnostic_signal_observer` codebase is a quantitative crypto research scaffold comprising **169 Python files** (~122,743 lines of code). It implements a rigorous multi-phase research pipeline for discovering, evaluating, and validating cross-venue lead-lag signals and funding-rate arbitrage hypotheses.

**Overall assessment:** The codebase demonstrates strong engineering discipline — observer-only execution semantics, frozen precommitment constants, governance-led state machines, append-only evidence ledgers, and FDR/MCPT/PBO statistical controls. However, the scaffold has grown organically into several architectural layers with inconsistent naming conventions, dead code, path inconsistencies, and a handful of latent bugs that should be addressed before further research runs.

**Key findings:**
- 7 confirmed bugs/defects (3 high, 4 medium)
- 3 architectural inconsistencies
- 5 instances of unused/dead code
- 169 files across 8 research families — well beyond a single strategy scaffold

---

## 2. Scope and Methodology

### Files Audited

All Python source files under:
```
examples/strategies/venue_agnostic_signal_observer/
```

Read in full: `config.py`, `signals.py`, `models.py`, `tick_models.py`, `forward_returns.py`, `event_study.py`, `observer.py`, `funding_dispersion_carry.py`, `research_report_miner.py`, `data_loading.py`, `run_index.py`, `governance/ledger.py`, `governance/replay.py`, `cross_asset_beta_lag_archive.py`, `lead_lag.py`, `csv_normalizer.py`, `data_adapters.py`, and all files in:

- `validator/` (9 files — statistical estimators)
- `bot/` (3 files — execution gate)
- `corpus/` (3 files — recurrence aggregation)
- `shadow/` (3 files — fill simulation)
- `governance/` (3 files — evidence ledger)
- `discovery/` (6 files — grid/candidate locking)
- `systemd/` (3 files — service configuration)

All `run_*.py` CLI entry points (~50 files) were read and catalogued.

### Methodology

Following `layered-research-codebase-patterns.md` and `multi-project-scaffold-patterns.md`:
1. **Structural inventory** — file tree, module counts, dependency mapping
2. **Core logic audit** — signal generation, forward returns, cost modeling, statistical validators
3. **Governance audit** — ledger integrity, replay correctness, gate semantics
4. **Shadow/execution audit** — fill model realism, capacity estimation
5. **CLI entry points** — data flow, argument validation, error paths
6. **Cross-cutting** — naming consistency, dead code, unused imports, path hygiene

---

## 3. Architectural Inventory

### 3.1 File Count by Category

| Category | Files | LOC | Purpose |
|----------|-------|-----|---------|
| Core signal logic | ~15 | ~8,000 | Signal generators, event models, forward returns |
| Data adapters/loaders | ~10 | ~5,000 | CSV normalizers, data fetchers, archive downloaders |
| CLI entry points (run_*.py) | ~50 | ~35,000 | Capture, evaluation, validation, reporting runners |
| Statistical validators | 9 | ~4,500 | DSR, CPCV, PBO, FDR, embargo, effective trials |
| Governance/ledger | 3 | ~2,000 | Evidence ledger, replay, event types |
| Discovery/locking | 6 | ~2,500 | Grid locks, candidate locks, promotion boundary |
| Corpus aggregation | 3 | ~1,500 | Cross-capture aggregation, ledger emission |
| Shadow execution | 3 | ~1,500 | Fill simulation, shadow executor |
| Bot/execution gate | 3 | ~1,000 | Approved manifest, authorization gate |
| Systemd/deployment | 3 | ~500 | Service files, setup scripts |
| Research reports/miner | ~5 | ~2,000 | Report writer, research status miner |
| Stress/corpus labels | ~5 | ~2,500 | Stress window generation, streaming labels |
| Stage 2 gate watcher | ~5 | ~2,000 | Volatility gate, readiness checks, corpus split |
| Funding dispersion | ~5 | ~2,500 | Cross-exchange funding studies |
| Offline discovery | ~12 | ~3,000 | Edge Miner offline pipeline |
| **Total** | **169** | **~122,743** | |

### 3.2 Research Families

The codebase supports at least 8 distinct research families:

1. **Derivatives→Spot Lead-Lag** — Cross-venue impulse detection with forward-return measurement (primary)
2. **Cross-Asset Beta Lag Archive** — Binance Vision archive-based stress study (BTC/ETH → altcoin targets)
3. **Funding Dispersion Carry** — Cross-exchange funding rate arbitrage (Binance vs Bybit)
4. **Funding Crowding Reversal** — Family 2: OI x funding crowding on spot forward returns
5. **Funding Falling OI Unwind** — Family 3: Negative funding + falling OI → positive spot return
6. **Edge Miner Offline Discovery** — Systematic parameter sweep with FDR-controlled discovery
7. **DEX→CEX Dislocation** — DEX pool snapshot → CEX forward return study
8. **Polymarket Liquidity Probes** — Prediction market CLOB liquidity measurement

### 3.3 Pipeline Phases

```
Phase 0: Source Structure Stress Gates
  └── Hawkes intensity + permutation entropy on source ticks

Phase 0A/0B: Archive Probe + Sizing
  └── Binance Vision availability + download volume estimation

Phase 1: Data Capture + Preparation
  └── WebSocket capture, CSV normalization, offline data prep

Phase 2A: Stress Window Indexing
  └── Build stress windows from prepared data

Phase 2B: Offline Discovery Pipeline
  └── Discovery plan → train evaluation → survivor freeze → holdout → FDR → null

Phase 2: Evaluation
  └── Full forward-return evaluation across all families

Phase 3: Edge Miner
  └── End-to-end offline discovery with grid sweeping

Phase 4: Corpus Aggregation
  └── Cross-capture consistency, recurrence detection

Phase 5: Validation
  └── Permutation null, MCPT, cost sensitivity, candidate falsification

Phase 6: Governance → Execution (future)
  └── Ledger → replay → manifest → bot gate → shadow → live
```

---

## 4. Bugs and Defects

### 4.1 HIGH — validator/summary.py: FDR Result Key Mismatch

**File:** `validator/summary.py`, lines 177-178
**Impact:** FDR status always resolves to `"unknown"` in validator summary

```python
# Current code:
fdr_method = fdr_result.get("primary_fdr_method", "unknown")
fdr_q = fdr_result.get("primary_fdr_q", "")
```

`FDRResult.to_dict()` (via `dataclasses.asdict()`) produces keys `method` and `alpha`, not `primary_fdr_method` and `primary_fdr_q`. The lookups silently return defaults, meaning the validator summary never reflects the actual FDR method or q-value used.

**Fix:** Use `fdr_result.get("method", "unknown")` and `fdr_result.get("alpha", "")`, or ensure the caller passes a properly structured dict.

---

### 4.2 HIGH — bot/manifest.py: ISO Date String Comparison

**File:** `bot/manifest.py`, lines 36-37 (`is_expired()`)
**Impact:** Expired manifests may not be detected

```python
def is_expired(self) -> bool:
    return datetime.now(UTC).isoformat() > self.expires_at_utc
```

`datetime.now(UTC).isoformat()` produces `2024-01-15T10:30:00.123456+00:00`, but stored `expires_at_utc` may use `Z` suffix (from `artifact_metadata.py`). Lexicographic comparison of ISO strings with different timezone suffixes is unreliable.

**Fix:** Parse both to `datetime` objects before comparing:
```python
def is_expired(self) -> bool:
    now = datetime.now(UTC)
    expires = datetime.fromisoformat(self.expires_at_utc)
    return now > expires
```

---

### 4.3 HIGH — shadow/fill_model.py: Hardcoded Forced Exit Rate

**File:** `shadow/fill_model.py`, line 115
**Impact:** 20% forced exit rate is not configurable, affecting all shadow results

```python
forced_exit = self._rng.random() > 0.8  # Hardcoded 20%
```

The forced exit probability should be a `FillModelConfig` parameter. Similarly, `queue_depth_levels` in config is declared but never used.

**Fix:** Add `forced_exit_probability: float = 0.2` to `FillModelConfig` and use it.

---

### 4.4 MEDIUM — shadow/shadow_executor.py: Dead Fill Types

**File:** `shadow/shadow_executor.py`, lines 115-165
**Impact:** `missed_fill_rate` and `partial_fill_rate` are always 0.0

- `FillEvent.fill_type` can only be `"maker"`, `"taker"`, or `"staleness_reject"` — never `"missed"` or `"partial"`.
- The executor computes rates for these non-existent types, wasting computation and producing misleading zero values.

**Fix:** Remove `missed` and `partial` fill rate computation, or extend `FillEvent.fill_type` to support them.

---

### 4.5 MEDIUM — systemd/nautilus-stage2-gate-watcher.service: graphical-session.target

**File:** `systemd/nautilus-stage2-gate-watcher.service`
**Impact:** Service won't start on headless deployments

```ini
After=graphical-session.target
Wants=graphical-session.target
```

For a trading research system running on a Synology NAS or headless server, this target will never be reached.

**Fix:** Change to `multi-user.target` for headless compatibility, or make it conditional.

---

### 4.6 MEDIUM — systemd/ Service vs Setup Script Path Inconsistency

**Files:**
- `systemd/nautilus-stage2-gate-watcher.service` → `/mnt/nasirjones/py/nautilus_trader_stage2_cross_asset_stress`
- `systemd/setup_stage2_worktree.sh` → `/mnt/nasirjones/py/nautilus_trader_stage2_runtime`
- `systemd/STAGE2_WORKTREE_SERVICE.md` → `/mnt/nasirjones/py/nautilus_trader_stage2_runtime`

The service file references a different worktree path than the setup script and documentation. If the setup script creates `stage2_runtime` and the service looks for `stage2_cross_asset_stress`, the service will fail.

**Fix:** Standardize all references to one path name (preferably `_stage2_runtime` as documented).

---

### 4.7 MEDIUM — corpus/ledger_emitter.py: Unused Import

**File:** `corpus/ledger_emitter.py`
**Impact:** Dead code

```python
from ..governance.events import make_revocation_event
```

`make_revocation_event` is imported but never called. The emitter produces `corpus-evidence` and `demotion` events but never revocation events.

**Fix:** Remove the import or implement revocation logic if needed.

---

### 4.8 LOW — corpus/aggregator.py: Consistency Score Bug

**File:** `corpus/aggregator.py`, lines 140-143
**Impact:** Negative-performing candidates get same consistency score as positive ones

```python
consistency = max(0.0, 1.0 - abs(ret_std / agg_mr))
```

When `agg_mr` is negative, `abs(ret_std / agg_mr)` produces the same value as for positive means. A candidate that consistently loses money gets the same consistency score as one that consistently wins.

**Fix:** Use signed ratio: `consistency = max(0.0, 1.0 - (ret_std / agg_mr))` so negative means produce low scores.

---

## 5. Architectural Inconsistencies

### 5.1 Scope Creep: Single Scaffold, Multiple Frameworks

The codebase under `venue_agnostic_signal_observer` has grown to include:
- A full statistical validation framework (DSR, CPCV, PBO, FDR, embargo)
- A governance system (append-only ledger, replay, manifest management)
- A bot execution gate (authorization, expiry checking)
- A shadow execution engine (cross-venue fill simulation)
- An offline discovery pipeline (grid sweep, survivor freeze, holdout)
- A corpus aggregation system (recurrence detection)
- A desktop notification system (libnotify, sound alerts)
- A systemd service management framework

This is no longer a "signal observer" — it's a full research-to-execution pipeline. The naming is misleading and the boundary between research and execution logic is porous.

**Recommendation:** Consider splitting into separate packages:
- `venue_agnostic_signal_observer` — signal generation + forward return measurement
- `research_governance` — ledger, replay, manifest, locking
- `research_validation` — DSR, CPCV, PBO, FDR, embargo
- `shadow_execution` — fill modeling
- `research_corpus` — aggregation and recurrence

### 5.2 Inconsistent Cost Models

| Module | Fee | Slippage | Quote Mismatch | Total |
|--------|-----|----------|----------------|-------|
| `event_study.py` | Configurable | Configurable | Configurable | Variable |
| `cross_asset_beta_lag_archive.py` | 40 | 5 | 5 | 50 bps |
| `funding_dispersion_carry.py` | 50 (primary) / 6 (diagnostic) | N/A | N/A | 50 bps |
| `shadow/fill_model.py` | 8.0 (taker) / -2.0 (maker rebate) | Spread-based | N/A | Variable |
| `config.py` | Configurable | Configurable | Configurable | Variable |

The shadow executor uses taker/maker fee asymmetry (8.0 vs -2.0 bps rebate) that doesn't align with the flat 40 bps used elsewhere. This creates incompatible cost assumptions between evaluation and shadow phases.

### 5.3 Mixed Paradigm: Nautilus vs Standalone

Despite living inside a NautilusTrader repository, the vast majority of this codebase uses:
- Standard library (csv, json, urllib, statistics, bisect)
- Custom data models (dataclasses, not Nautilus models)
- Custom data loading (JSONL, CSV, not Nautilus data adapters)
- Custom event systems (not Nautilus message bus)

Only a few files reference Nautilus imports. The scaffold operates as a standalone research framework that happens to share a repo with NautilusTrader.

**Recommendation:** Either integrate with Nautilus primitives (data types, backtest engine, message bus) or move to an independent repository to avoid confusion.

---

## 6. Dead Code and Unused Imports

| File | Issue | Severity |
|------|-------|----------|
| `corpus/ledger_emitter.py` | `make_revocation_event` imported but never used | Low |
| `validator/dsr.py` | `from .metadata import make_metadata` inside function body (already imported at top) | Trivial |
| `shadow/fill_model.py` | `queue_depth_levels` in config but never referenced | Low |
| `shadow/shadow_executor.py` | `partial_fill_rate` always 0.0 (fill model never produces "partial") | Medium |
| `shadow/shadow_executor.py` | `missed_fill_rate` always 0.0 (fill model never produces "missed") | Medium |
| `data_loading.py` | `generate_synthetic_data` is a legacy wrapper that delegates to `generate_synthetic_lead_lag` | Trivial |
| `data_loading.py` | `generate_synthetic_noise` is defined but not imported by any module | Low |
| `research_report_miner.py` | `_VERDICT_NORMALIZE` includes patterns for "PASS" and "VIABLE" that don't match any current verdict system | Trivial |

---

## 7. Security and Safety Assessment

### 7.1 Observer-Only Compliance: PASS

- No live trading code reachable from research scripts
- No API key or credential handling in the codebase
- `SAFETY_MODE = "public_data_observer_only"` in `funding_dispersion_carry.py`
- `FORBIDDEN_VERDICTS` explicitly blocks `CANDIDATE_FOR_LIVE`, `EXECUTION_READY`, `TRADE_READY`
- Governance ledger is fail-closed on integrity failures

### 7.2 Precommitment Enforcement: PASS

- Frozen constants with `assert` guards (e.g., `FROZEN_CELL_COUNT == 24`)
- Invariant labels prevent silent drift
- Grid/candidate locking with SHA-256 hashing
- Promotion boundary as single choke point

### 7.3 Secret Handling: PASS

- No secrets, API keys, or credentials found in any audited file
- All exchange connections use public WebSocket endpoints or public archive URLs
- `.env` patterns not present

### 7.4 Run Index Integrity: PASS

- Append-only JSONL with git SHA tracking (dirty-state aware)
- Schema version validation
- Run type and status enum validation

---

## 8. Data Flow Analysis

### 8.1 Primary Research Flow (Lead-Lag)

```
WebSocket Capture (run_derivatives_spot_capture.py)
    └── JSONL per stream (trade ticks, quote ticks)
        └── Overlap clipping (run_derivatives_spot_lead_lag.py)
            └── Impulse generation (trade_flow_impulse.py)
                └── Forward returns (forward_returns_gpu.py / CPU fallback)
                    └── Evaluation report (summary.json, summary.csv, report.md)
                        └── Permutation null test (run_permutation_null.py)
                        └── Cost sensitivity (run_cost_sensitivity.py)
                        └── Heatmap diagnostics (run_lead_lag_heatmap.py)
```

### 8.2 Archive Research Flow (Beta Lag)

```
Binance Vision Archive (binance_vision_archive.py)
    └── aggTrades CSV download (cached as JSONL/parquet)
        └── Streaming stress labels (streaming_stress_labels.py)
            └── Cross-asset evaluation (cross_asset_beta_lag_archive.py)
                └── Forward returns (CPU/GPU)
                └── Null tests (timestamp shuffle)
                └── Reports (availability, data quality, stress labels)
```

### 8.3 Funding Research Flow

```
Binance/Bybit Funding API → CSV archive
    └── Unit normalization (decimal/percent → bps)
        └── Window resolution (70/30 train/holdout)
            └── Dispersion event detection (threshold-gated)
                └── Non-overlapping campaign construction
                    └── Null test (event-vector circular shift)
                    └── FDR correction (Benjamini-Yekutieli)
                    └── Holdout evaluation
```

---

## 9. Statistical Rigor Assessment

### 9.1 Strengths

- **FDR control:** Benjamini-Yekutieli (arbitrary dependence) used by default — correct for correlated grid cells
- **Multiple validation methods:** DSR, CPCV, PBO provide triangulation
- **Null testing:** Permutation null (time-shift) and sign-flip null both implemented
- **Precommitment:** Frozen cell grid, frozen FDR family size, fixed seeds
- **Embargo/purging:** Wall-clock-based with configurable horizon

### 9.2 Weaknesses

- **PBO/CSCV has no purging/embargo** — per design, but this means overfitting detection is weaker for time-correlated signals
- **PBO block sizing** uses `min(len(r) for r in cell_returns)` — all cells truncated to shortest series, potentially wasting data
- **Single-linkage clustering** in `effective_trials.py` is order-dependent; results change with input order
- **DSR probit approximation** uses Beasley-Springer-Moro rational approximation (~7 decimal precision) — adequate but not exact
- **CPCV non-stationarity detection** uses a heuristic 50% drop threshold — not statistically principled
- **Lower confidence bound** in shadow executor uses 1-sigma (~68% confidence), not 95% or 99%

---

## 10. Recommendations

### 10.1 Immediate Fixes (This Week)

1. Fix `validator/summary.py` FDR key mismatch (Bug 4.1)
2. Fix `bot/manifest.py` ISO date comparison (Bug 4.2)
3. Add `forced_exit_probability` to `FillModelConfig` (Bug 4.3)
4. Remove dead fill type computations from `shadow_executor.py` (Bug 4.4)
5. Remove unused `make_revocation_event` import (Bug 4.7)
6. Standardize systemd worktree path references (Bug 4.6)

### 10.2 Short-Term Improvements (This Month)

7. Change systemd `After=graphical-session.target` to `multi-user.target` (Bug 4.5)
8. Fix `corpus/aggregator.py` consistency score for negative means (Bug 4.8)
9. Align cost models between evaluation (40 bps) and shadow (8.0 bps taker)
10. Clean up dead code: `generate_synthetic_noise`, `generate_synthetic_data` legacy wrapper

### 10.3 Architectural Considerations (Next Quarter)

11. **Rename or split the package** — current name undersells the scope
12. **Consolidate run_*.py entry points** — 50+ runners is hard to navigate; consider a subcommand-based CLI
13. **Add integration tests** for the full capture→evaluate→validate pipeline on synthetic data
14. **Document the dependency graph** — a proper architecture diagram would help onboarding
15. **Evaluate Nautilus integration** — either use Nautilus primitives or move to independent repo

---

## 11. File Inventory

### Core Modules (no prefix)

| File | LOC | Purpose |
|------|-----|---------|
| `config.py` | ~150 | Strategy configuration, horizons, fee models |
| `signals.py` | ~200 | Cross-market signal generator |
| `models.py` | ~200 | SignalEvent, ForwardReturnResult, SignalEvaluationSummary |
| `tick_models.py` | ~350 | QuoteTickLite, TradeTickLite, TickSignalEvent, TickForwardReturn |
| `forward_returns.py` | ~400 | Forward return evaluation (CPU) |
| `event_study.py` | ~710 | TickLeadLagGenerator, evaluate_tick_signal, random baseline, candidate gate |
| `observer.py` | ~225 | SignalObserver — ties signals, forward returns, reporting |
| `reports.py` | ~100 | Report output writers |
| `data_loading.py` | ~165 | CSV loaders, synthetic data generators |
| `csv_normalizer.py` | ~250 | CSV parsing, column mapping, timestamp normalization |
| `data_adapters.py` | ~400 | Venue data ingestion adapters |
| `lead_lag.py` | ~170 | Lead-lag signal generator with bisect windowing |
| `cross_asset_impulse.py` | ~300 | Cross-asset impulse detection |
| `cross_capture_consistency.py` | ~420 | Cross-capture consistency aggregation |
| `candidate_falsification.py` | ~260 | Survival/failure matrix |
| `cost_sensitivity.py` | ~350 | Breakeven cost analysis |
| `research_report_miner.py` | ~555 | Walks reports/ directory, produces unified status table |
| `run_index.py` | ~270 | Append-only JSONL run index |
| `artifact_metadata.py` | ~215 | Metadata injection, schema versioning, git provenance |
| `symbol_aliases.py` | ~180 | Cross-venue symbol → canonical mapping |
| `validate_capture.py` | ~200 | Capture validation smoke tool |
| `funding_dispersion_carry.py` | ~440 | Cross-exchange funding dispersion carry constants + contracts |
| `cross_asset_beta_lag_archive.py` | ~1,540 | Cross-asset stress study engine |
| `binance_vision_archive.py` | ~400 | Binance Vision archive downloader |
| `data_download.py` | ~150 | Public OHLCV data downloader |
| `data_fetcher.py` | ~205 | Binance/Kraken REST API fetcher |

### Subpackages

| Package | Files | Purpose |
|---------|-------|---------|
| `validator/` | 9 | Statistical estimators (DSR, CPCV, PBO, FDR, embargo, effective trials, synthetic) |
| `governance/` | 3 | Evidence ledger, replay, event types |
| `discovery/` | 6 | Grid locks, candidate locks, promotion boundary, safety scan |
| `corpus/` | 3 | Corpus aggregation, ledger emission |
| `shadow/` | 3 | Cross-venue fill simulation, shadow executor |
| `bot/` | 3 | Approved manifest, authorization gate |
| `systemd/` | 3 | Service files, setup scripts, documentation |

---

## 12. Previous Audit Comparison

This audit (2026-05-20) is the first formal product audit on record for this scaffold directory. No prior `docs/audit-*.md` files were found in this directory.

---

## 13. Verdict

**Status:** RESEARCH-GRADE CODEBASE — HIGH POTENTIAL, REQUIRES CLEANUP

The `venue_agnostic_signal_observer` scaffold demonstrates serious quantitative research engineering. The precommitment discipline, governance architecture, and statistical rigor are above average for a research codebase. However, organic growth has created architectural sprawl, dead code, and a handful of latent bugs that should be fixed before further research investment.

The most impactful single improvement would be splitting the codebase into focused packages, allowing each to evolve independently without the risk of breaking unrelated research families.

---

*Audit completed 2026-05-20. Next audit recommended after immediate fixes are applied.*
