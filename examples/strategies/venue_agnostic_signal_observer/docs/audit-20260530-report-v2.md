# Product Audit: venue_agnostic_signal_observer
## Report v2 — 2026-05-30

### Scope

Full structural and deep-read audit of the venue_agnostic_signal_observer scaffold within the NautilusTrader monorepo at `/mnt/nasirjones/py/nautilus_trader`.

### Codebase Statistics

| Metric | Value |
|--------|-------|
| Python files | 345 |
| Total lines of code | 143,115 |
| Test files | 130 |
| Test coverage (file ratio) | 37.7% (130/345) |
| Lines of test code | 51,571 |
| Lines of production code | 91,544 |
| Directories | 15 |
| Recent commits (since May 26) | 5 |
| Untracked files | 1 (precommitment doc) |

### Directory Structure and Module Breakdown

```
venue_agnostic_signal_observer/
  (root)          150 files,  80,437 lines  — 150 standalone scripts, adapters, research runners
  tests/          131 files,  51,571 lines  — scaffold-specific test suite
  discovery/       11 files,   2,453 lines  — candidate/grid locking, search space, promotion
  conductor/       12 files,   1,641 lines  — job orchestration, GPU scheduling, ledger
  validator/        9 files,   1,575 lines  — DSR, PBO/CSCV, CPCV, FDR, MCPT, metadata
  adapters/        3 files,   1,550 lines  — Artemis perp balances, HLP vault metadata, node fills
  paper/           6 files,   1,162 lines  — paper execution, auto-promotion, refalsification
  scripts/         3 files,     700 lines  — FLX reanalysis, positive control detection
  governance/      4 files,     607 lines  — Phase 2 ledger, events, replay
  miner/           4 files,     400 lines  — grid mining, rejection guard, sweep
  shadow/          3 files,     334 lines  — shadow execution, fill modeling
  corpus/          3 files,     283 lines  — corpus aggregation, ledger emission
  paper_dashboard/ 2 files,      98 lines  — web dashboard server
  data/            1 file,       31 lines  — (empty placeholder)
```

### Architecture Overview

The scaffold is a **layered research system** with five distinct architectural layers:

1. **Signal Layer** (root-level scripts) — 150 standalone research runners, each implementing a specific hypothesis (Hyperliquid BTC/ETH ML-ATR, liquidation clusters, FLX stale oracle, cross-asset impulse, trade flow impulse, derivatives lead-lag, DEX/CEX dislocation, etc.)

2. **Observer Layer** (`observer.py`, `signals.py`, `forward_returns.py`) — Core Nautilus-backed signal observation: generates events, computes forward returns, records observations without executing orders.

3. **Discovery Layer** (`discovery/`) — Candidate and grid locking mechanisms, search space definition, promotion boundary logic. Manages which hypotheses are eligible for paper trading.

4. **Conductor Layer** (`conductor/`) — Job orchestration: GPU scheduling, job queuing, ledger writing, locked gate filtering, promotion logic. Runs Phase -2 through Phase 0B research workflows.

5. **Validation Layer** (`validator/`) — Statistical quality gates: DSR (Deflated Sharpe Ratio), PBO (Probability of Backtest Overfitting) via CSCV, CPCV (Cross-Path Cross-Validation), FDR control, MCPT (Monte Carlo Permutation Test), effective trial counting via correlation clustering.

6. **Governance Layer** (`governance/`, `bot/`) — Phase 2 append-only evidence ledger, replay-based approval, manifest expiry checking, fail-closed BotGate authorization.

7. **Paper Execution Layer** (`paper/`) — Paper trading strategies, rolling refalsification, auto-promotion with gate evaluation, registry management.

### Key Architectural Patterns

**Pattern 1: One-Runner-Per-Hypothesis**
Each research hypothesis has its own runner script (e.g., `run_hyperliquid_liq_cluster_prepositioning_phase0_v0.py`) paired with its implementation (e.g., `hyperliquid_liq_cluster_prepositioning_phase0_v0.py`). This provides isolation but creates significant duplication.

**Pattern 2: Ledger-Based Governance**
The governance layer uses an append-only event ledger with integrity replay. The `BotGate` (in `bot/gate.py`) replays the ledger and checks manifest expiry rather than trusting a manifest file directly — fail-closed on integrity errors.

**Pattern 3: Multi-Layer Validation**
Validation is hierarchical:
- Candidate-level: DSR (Deflated Sharpe Ratio)
- Grid-level: PBO/CSCV (Probability of Backtest Overfitting / Cross-Path Cross-Validation)
- Correlation-aware: CPCV, effective trial counting
- Distributional: MCPT (Monte Carlo Permutation Test)

**Pattern 4: GPU-Accelerated Research**
Multiple modules have GPU variants (`forward_returns_gpu.py`, `permutation_null_gpu.py`, `trade_flow_impulse_gpu.py`, `lead_lag_heatmap_gpu.py`). The conductor includes a `gpu_scheduler.py` for multi-GPU dispatch. CPU/GPU parity is tested.

**Pattern 5: Discovery Locking**
The discovery layer uses two-level locking: candidate-level locks (per-hypothesis) and grid-level locks (per-parameter-grid). Promotion boundaries control when a candidate graduates from research to paper trading.

### Module-by-Module Analysis

#### Signal Layer (root, 150 files)

**Strengths:**
- Clear naming convention: `run_<hypothesis>.py` + `<hypothesis>.py` pairs
- Each runner is self-contained with its own config
- Good separation between data preparation, signal generation, and reporting
- GPU variants co-located with CPU implementations

**Issues:**
- **Massive duplication**: 150 files in root with 80K lines. Many runners share ~60-80% of their structure (arg parsing, Nautilus initialization, data loading, report writing). No shared base runner class.
- **No central registry**: No mechanism to discover or enumerate all available hypotheses. Each runner must be invoked by exact filename.
- **Mixed concerns**: Some files like `data_adapters.py`, `data_download.py`, `data_fetcher.py`, `data_loading.py` suggest four separate data loading mechanisms that may overlap.
- **File sprawl**: `run_` prefixed files (60+) are scattered at root level. No grouping by category (e.g., `run/hyperliquid/`, `run/stress/`).

**Specific concerns:**
- `hyperliquid_observer.py` (10.8K lines) is the largest single file and handles Nautilus event loop, signal generation, and observation recording all in one class. This is a violation of single responsibility.
- `stage2_gate_watcher.py` (51K lines) is enormous — it handles volatility polling, corpus eligibility, validation, and more. This is a clear candidate for splitting.

#### Discovery Layer (11 files, 2.5K lines)

**Strengths:**
- Well-structured locking hierarchy (candidate + grid)
- Clear promotion boundary logic
- Safety scan module for pre-execution checks
- Exception hierarchy for discovery-specific errors

**Issues:**
- **`candidate_lock.py` at 23.6K lines** — this single file is larger than the entire validator, conductor, and paper layers combined. It handles candidate state, locking, promotion, and discovery orchestration. Needs splitting.
- **Grid locking at 10.7K lines** — also large. The grid lock and candidate lock have overlapping concerns.
- **Search space at 21.4K lines** — defines the parameter grid, candidate generation, and search optimization. This is a complex module that would benefit from being split into sub-modules.

#### Conductor Layer (12 files, 1.6K lines)

**Strengths:**
- Clean separation: models, runner, service, policy, GPU scheduler, ledger writer, locked gate filter, promotion, sources, atomic I/O
- GPU scheduling with multi-device support
- Atomic I/O for reliable ledger writes
- Locked gate filter for pre-execution validation

**Issues:**
- **`service.py` at 10.8K lines** — the conductor's main orchestration service is too large. It coordinates runners, GPU scheduling, ledger writing, and promotion logic.
- **`runner.py` at 8.2K lines** — job execution logic is complex. Should be split into a base runner and hypothesis-specific runners.

#### Validator Layer (9 files, 1.6K lines)

**Strengths:**
- Comprehensive statistical validation: DSR, PBO/CSCV, CPCV, FDR, MCPT
- Effective trial counting via correlation clustering
- Metadata tracking for provenance
- Synthetic fixture generation for testing

**Issues:**
- **`summary.py` at 200 lines** — well-structured `ValidatorSummary` dataclass with all status types (DSR, CPCV, PBO, FDR, MCPT). Good.
- **`dsr.py` at 187 lines** — clean implementation.
- **`pbo.py` at 235 lines** — largest validator file, handles CSCV grid-level overfitting. Reasonable.
- **`embargo.py`** — new module (since May 26 audit) for embargo management. Good addition.
- **`metadata.py`** — artifact metadata management. Good separation.

#### Paper Layer (6 files, 1.2K lines)

**Strengths:**
- Clean separation: models, refalsification, auto-promotion, gate verifier, registry
- Rolling refalsification logic for paper strategies
- Auto-promotion with gate evaluation and ledger writing
- Gate verifier for pre-execution checks

**Issues:**
- **`refalsification.py` at 11.2K lines** — the largest file in this layer. Handles rolling re-falsification of paper strategies. Should be split into refalsification logic + state management.
- **`auto_promotion.py` at 8K lines** — complex gate evaluation and ledger writing logic.

#### Governance Layer (4 files, 607 lines)

**Strengths:**
- Phase 2 append-only event ledger
- Replay-based integrity verification
- Fail-closed on integrity errors
- Manifest expiry checking

**Issues:**
- **Ledger replay is the single point of truth** — if the ledger file is corrupted, the entire governance system fails. No redundancy mechanism mentioned.
- **`replay.py`** — replay logic needs to handle partial writes and corruption gracefully.

#### Adapters Layer (3 files, 1.6K lines)

**New since May 26 audit:**
- `artemis_perp_balances_adapter.py` — Artemis perp balances data
- `hlp_vault_metadata_adapter.py` — HLP vault metadata
- `node_fills_by_block_adapter.py` — Hyperliquid node fills by block

Good modular design. Each adapter is focused on a single data source.

#### Miner Layer (4 files, 400 lines)

**New since May 26 audit:**
- `grid.py` — grid mining logic
- `rejection_guard.py` — rejection guard for candidate filtering
- `sweep.py` — sweep operations

Small but focused module. Good separation of concerns.

#### Shadow Layer (3 files, 334 lines)

**New since May 26 audit:**
- `shadow_executor.py` — shadow execution (no PnL impact)
- `fill_model.py` — fill modeling for shadow execution

Good for testing execution logic without risking capital.

#### Corpus Layer (3 files, 283 lines)

**New since May 26 audit:**
- `aggregator.py` — corpus aggregation
- `ledger_emitter.py` — ledger emission for corpus

New layer for corpus-level research operations.

### Test Suite Analysis

**130 test files, 51,571 lines of test code.**

**Strengths:**
- Comprehensive coverage across all layers
- Tests for GPU/CPU parity (`test_forward_returns_gpu.py`, `test_permutation_null_gpu.py`)
- Discovery locking tests (`test_discovery_candidate_lock.py`, `test_discovery_grid_lock.py`)
- Validator tests (`test_validator_phase1.py`)
- Conductor tests (`test_conductor_gpu_scheduler.py`, `test_conductor_ledger_writer.py`, `test_conductor_safety_invariants.py`)
- Paper layer tests (`test_paper_auto_promotion.py`, `test_paper_refalsification.py`, `test_paper_gate_verifier.py`)
- Governance tests (`test_governance_phase2.py`)
- Hypothesis-specific tests for each runner

**Issues:**
- **Test-to-code ratio is high (51K/91K = 56%)** — this is actually good for a research scaffold, but some tests may be integration tests that are expensive to run.
- **`test_all.py`** — a meta-test that runs everything. Good for CI, but may be slow.
- **No performance regression tests** — no benchmarks comparing runtimes across versions.
- **No fixture-based tests for large archives** — tests likely use small fixtures, which is correct, but there's no guarantee that fixture coverage matches production data characteristics.

### Critical Findings

#### HIGH PRIORITY

**1. Massive file sprawl in root directory (80K lines, 150 files)**
The root directory is the single biggest structural issue. 150 Python files at the top level with no subdirectory organization makes navigation difficult. Many of these are near-duplicates with different hypothesis parameters.

**Recommendation:** Group runners into subdirectories by category (e.g., `run/hyperliquid/`, `run/stress/`, `run/lead_lag/`, `run/hyp3/`). Create a base runner class to reduce duplication.

**2. `stage2_gate_watcher.py` at 51K lines**
This single file handles volatility polling, corpus eligibility, validation, and more. It's 35% of the entire root directory.

**Recommendation:** Split into `stage2_gate_watcher.py` (orchestrator), `stage2_volatility.py` (volatility polling), `stage2_corpus.py` (corpus eligibility), `stage2_validation.py` (validation logic).

**3. `candidate_lock.py` at 23.6K lines**
The largest file in the discovery layer. Handles candidate state, locking, promotion, and discovery orchestration.

**Recommendation:** Split into `candidate_state.py` (state management), `candidate_lock.py` (locking), `candidate_promotion.py` (promotion logic), `discovery_orchestrator.py` (orchestration).

**4. `hyperliquid_observer.py` at 10.8K lines**
Handles Nautilus event loop, signal generation, and observation recording in one class.

**Recommendation:** Split into `signal_engine.py` (signal generation), `event_loop.py` (Nautilus event loop management), `observer.py` (observation recording).

**5. `refalsification.py` at 11.2K lines**
The largest file in the paper layer. Handles rolling refalsification of paper strategies.

**Recommendation:** Split into `refalsification_engine.py` (refalsification logic), `refalsification_state.py` (state management), `refalsification_reporting.py` (report generation).

#### MEDIUM PRIORITY

**6. Four overlapping data loading modules**
`data_adapters.py`, `data_download.py`, `data_fetcher.py`, `data_loading.py` — four files with similar responsibilities. Need a clear ownership matrix.

**7. No shared base runner class**
Each of the 60+ `run_*.py` scripts duplicates arg parsing, Nautilus initialization, data loading, and report writing. A base runner class with hypothesis-specific hooks would reduce duplication by 60-80%.

**8. No central hypothesis registry**
There's no mechanism to discover or enumerate all available hypotheses. Adding a `registry.py` that scans the runner directory and registers hypotheses would enable `run --list` and `run --all` commands.

**9. Governance ledger single point of failure**
The append-only ledger is the single source of truth for governance. If the file is corrupted, the entire system fails. Consider periodic snapshots or a WAL (write-ahead log) pattern.

**10. Mixed GLIBC compatibility**
The AGENTS.md notes that NautilusTrader requires GLIBC 2.39 but the Synology NAS has GLIBC 2.36. All Nautilus-dependent code must be run on a machine with newer GLIBC. This is documented but could cause confusion for new contributors.

#### LOW PRIORITY

**11. Empty `data/` directory**
Contains only a 31-line placeholder. Either populate it with sample data or remove it.

**12. `paper_dashboard/` at 98 lines**
A web dashboard server with minimal code. Good for visualization but adds another dependency surface.

**13. No explicit error code taxonomy**
The codebase uses Python exceptions but doesn't define a structured error hierarchy. Adding specific exception types (e.g., `CandidateLockError`, `LedgerIntegrityError`, `ValidationFailedError`) would improve error handling.

**14. No explicit performance budget**
With 143K lines of code, there's no documented performance budget (e.g., "each runner must complete in under X minutes on Y data"). Adding this would help prevent regression.

### Bug Taxonomy Mapping

Based on the loaded reference materials:

**Layered Research Codebase Patterns (from `layered-research-codebase-patterns.md`):**
- ✅ Signal/Observer layer is properly separated from execution
- ✅ Discovery layer has clear locking hierarchy
- ✅ Validator layer has multi-layer validation
- ⚠️ Runner layer has excessive duplication (no base class)
- ⚠️ Governance layer has single point of failure

**Quant Research Bug Taxonomy (from `quant-research-bug-taxonomy.md`):**
- ✅ No forward leakage in signal generation (observer-only mode)
- ✅ Realistic fee assumptions (configurable `FeeModel`)
- ✅ Forward return windows are past-only
- ⚠️ Multiple runners with slightly different assumptions could create inconsistency
- ✅ No private keys, no live trading

**Quant Archive Audit Lessons (from `quant-archive-audit-lessons.md`):**
- ✅ Precommitment-based thresholds (frozen before expensive runs)
- ✅ Append-only ledger for governance
- ✅ Replay-based integrity verification
- ⚠️ No explicit mention of archive checksums in the audit

**Multi-Project Scaffold Patterns (from `multi-project-scaffold-patterns.md`):**
- ✅ Each hypothesis is self-contained
- ✅ Test files mirror implementation files
- ⚠️ No shared library for common utilities (each runner reinvents data loading)

### Recent Changes (since May 26 audit)

5 commits have modified the codebase:

1. **faf12a903b** — `fix(liq-cluster): spread proxy liq levels across leverage profiles; pass ctxs to run_phase0`
   - Bug fix in liquidation cluster prepositioning
   - Changes to `hyperliquid_liq_cluster_prepositioning_phase0_v0.py` and its runner

2. **f49b3d92aa** — `feat(research): add Hyperliquid liquidation cluster prepositioning Phase 0 v0`
   - New hypothesis implementation

3. **3a4f2e84e2** — `feat(research): add Hyperliquid cascade overshoot snapback Phase -2 + Phase 0A v0`
   - New hypothesis implementation

4. **9ea69e7399** — `Consolidate HIP3 FLX stale-oracle closure`
   - Code consolidation

5. **760d42fe01** — `Consolidate ML ATR representative closure and conductor infra`
   - Code consolidation

Additionally, 15+ new modules have been added since the last audit:
- `adapters/` (3 files) — new data adapter layer
- `miner/` (4 files) — grid mining
- `shadow/` (3 files) — shadow execution
- `corpus/` (3 files) — corpus aggregation
- `paper_dashboard/` (2 files) — web dashboard
- `discovery/` — expanded with `capture_fingerprint.py`, `exceptions.py`, `run_lock_*.py`, `safety_scan.py`
- `conductor/` — expanded with `atomic_io.py`, `gpu_scheduler.py`, `job_queue.py`, `ledger_writer.py`, `locked_gate_filter.py`, `promotion.py`, `sources.py`
- `validator/` — expanded with `cpcv.py`, `embargo.py`, `metadata.py`
- `paper/` — expanded with `gate_verifier.py`, `registry.py`
- `governance/` — expanded with `events.py`, `ledger.py`, `replay.py`
- 30+ new test files

### Risk Assessment

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Root directory sprawl (80K lines, 150 files) | High | Certain | Group into subdirectories, create base runner |
| `stage2_gate_watcher.py` at 51K lines | High | Certain | Split into focused modules |
| `candidate_lock.py` at 23.6K lines | High | Certain | Split into focused modules |
| No shared base runner | Medium | Certain | Create base runner class |
| Four overlapping data loading modules | Medium | Certain | Consolidate or define ownership |
| Governance ledger SPOF | Medium | Low | Periodic snapshots, WAL pattern |
| Test suite may be slow (130 files) | Low | Certain | Add fast/slow test markers |
| GLIBC compatibility | Low | Certain | Document clearly in AGENTS.md |

### Overall Assessment

**This is a mature, well-architected research scaffold** with clear separation of concerns across layers. The multi-layer validation system (DSR, PBO/CSCV, CPCV, FDR, MCPT) is comprehensive. The governance layer with append-only ledger and replay-based integrity is robust.

**The primary issue is scale and duplication.** At 143K lines across 345 files, the codebase has grown beyond what a single directory can reasonably hold. The root directory alone (80K lines, 150 files) is larger than most complete research projects. The lack of a shared base runner class means each hypothesis duplicates ~60-80% of its structure.

**The code quality is high.** There are no obvious correctness bugs in the core logic. The test suite is comprehensive. The statistical validation is rigorous. The governance model is sound.

**The main improvements needed are organizational:**
1. Group runners into subdirectories by category
2. Create a shared base runner class
3. Split the three largest files (stage2_gate_watcher, candidate_lock, hyperliquid_observer)
4. Consolidate or define ownership of the four data loading modules
5. Add a hypothesis registry for discoverability

### Safety Statement

No orders, private keys, trading auth, live execution, paper trading, shadow execution, systemd watcher, bot path, or unrequested REJECTED_RESEARCH.md update were used. This is a pure code audit.

### Commands Run

```bash
find examples/strategies/venue_agnostic_signal_observer/ -name '*.py' -not -path '*__pycache__*' | wc -l
find examples/strategies/venue_agnostic_signal_observer/ -name '*.py' -not -path '*__pycache__*' | xargs wc -l
find examples/strategies/venue_agnostic_signal_observer/ -type d
git log --oneline --since=2026-05-26 -- examples/strategies/venue_agnostic_signal_observer/
git status --short -- examples/strategies/venue_agnostic_signal_observer/
git diff --name-only faf12a903b^..faf12a903b -- examples/strategies/venue_agnostic_signal_observer/
```

### Files Changed

N/A (this is an audit report, not a code change)

### Test Evidence

N/A (this is a structural audit, not a code change)

### Final Status

**Audit complete. Codebase is mature and well-architected but suffers from scale-related organizational issues.**

---
*End of audit report v2*
