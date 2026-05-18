# PRODUCT AUDIT — Venue-Agnostic Signal Observer
## Date: 2026-05-18
## Version: v1

---

## 1. Executive Summary

The **Venue-Agnostic Signal Observer** is a sophisticated quantitative research pipeline for crypto cross-venue and cross-asset lead-lag signal evaluation. It detects price impulses on one market (source) and measures forward returns on another (target), testing whether cross-venue or cross-asset lead-lag effects exist and whether they survive transaction costs.

The project has grown from a straightforward OHLCV-based signal observer into a **multi-phase, multi-pipeline research architecture** spanning ~75 source Python files across 10 packages and ~40 CLI runner entry points. It implements a complete hypothesis-testing workflow: market data capture → signal generation → forward-return evaluation → statistical falsification → FDR correction → precommitment-gated replication → corpus aggregation → execution shadow simulation → ledger-governed approval → bot-gated execution.

**Key architectural insight:** The codebase is organized as a **10-layer research pipeline** with clear separation of concerns: data models (Layer 1) → signal generators (Layer 2) → forward-return evaluation (Layer 3) → observer/reporting (Layer 4) → data infrastructure (Layer 5) → domain adapters (Layer 6) → diagnostics (Layer 7) → statistical falsification (Layer 8) → multi-capture pipeline (Layer 9) → runners (Layer 10).

The project is explicitly research-only: every module carries a `SAFETY_MODE = "public_data_observer_only"` declaration or equivalent docstring. No module imports live execution clients. The bot module (Phase 6) implements a ledger-replay authorization gate that requires explicit governance approval before any order-capable code could run.

**Current research status:** The REJECTED_RESEARCH.md registry shows 36 rejected study groups, 9 needs-more-data groups, and a handful of open questions. The cross-asset beta-lag stress hypothesis remains the primary open thread, awaiting volatile-market capture windows.

---

## 2. Architecture Overview

### Deployment Model
Self-hosted research environment running on a Synology NAS. Code authored on the NAS, but NautilusTrader's Rust extensions require GLIBC 2.39 which the NAS lacks — all Nautilus-dependent code must be committed from the NAS and run on a desktop/cloud machine.

### Tech Stack
- **Language:** Python 3.12+ (project pyproject.toml requires >=3.12, <3.15)
- **Framework:** NautilusTrader v1.227.0 (Rust-native trading engine)
- **Data models:** Pure Python frozen dataclasses (no Pydantic)
- **Data storage:** JSONL (append-only), CSV, JSON manifests
- **GPU acceleration:** PyTorch (optional, CUDA)
- **HTTP client:** httpx (for public exchange REST APIs)
- **Linting:** ruff 0.15.12
- **Testing:** pytest 7.x
- **Python environment:** uv
- **Wiki:** Obsidian-compatible markdown wiki at `~/wiki/`

### Directory Structure

```
venue_agnostic_signal_observer/
  AGENTS.md                          # Agent operating rules
  REJECTED_RESEARCH.md               # Living rejection registry (locked gates)
  config.py                          # Frozen dataclass configs (Horizon, FeeModel, etc.)
  models.py                          # Bar-level data models (SignalEvent, ForwardReturnResult)
  tick_models.py                     # Tick-level models (TradeTickLite, TickSignalEvent, etc.)
  derivatives_models.py              # Derivatives-specific models
  dex_models.py                      # DEX pool models
  signals.py                         # CSV + cross-market signal generators
  event_study.py                     # Core event-study engine (711 lines)
  data_adapters.py                   # Venue CSV I/O, alignment, kline downloads
  data_loading.py                    # Data loading utilities
  data_fetcher.py                    # HTTP data fetchers
  data_download.py                   # Bulk data downloads
  csv_normalizer.py                  # OHLCV grid alignment
  symbol_aliases.py                  # Canonical symbol resolution
  artifact_metadata.py               # SHA/git metadata injection
  burn.py                            # Burn-file convention (JSONL)
  validate_capture.py                # Capture integrity checker
  tick_store.py                      # Tick data store
  reports.py                         # Bar-level report writing
  research_report_miner.py           # Cross-report data mining (556 lines)
  
  # Signal generators
  trade_flow_impulse.py              # 4-type trade-flow impulse signals (566 lines)
  trade_flow_impulse_gpu.py          # GPU-accelerated signal inference
  derivatives_lead_lag.py            # Derivatives impulse signals
  cross_asset_impulse.py             # Cross-asset impulse evaluator (839 lines, most complex)
  dex_cex_dislocation.py             # DEX-CEX dislocation detector
  lead_lag.py                        # Bar-level lead-lag (legacy)
  tick_lead_lag.py                   # Tick-level lead-lag
  
  # Diagnostics & falsification
  candidate_falsification.py         # Multi-evidence falsification matrix
  cost_sensitivity.py                # Breakeven cost analysis
  cross_capture_consistency.py       # Recurrence across captures
  stress_corpus.py                   # Stress window corpus builder
  stress_corpus_accumulator.py       # Incremental corpus accumulator
  stress_labels.py                   # Deterministic BTC/ETH stress labeling
  
  # Stage 2 pipeline (precommitment-gated replication)
  stage2_gate_watcher.py             # Main stress-poller daemon (1,598 lines)
  stage2_check_criteria.py           # Precommitted acceptance criteria
  stage2_fdr.py                      # BH/BY FDR correction
  stage2_precommitment_utils.py      # Precommitment lock & hash utilities
  stage2_readiness_check.py          # Environment readiness checks
  stage2_split_corpus.py             # Temporal 70/30 discovery/holdout split
  
  # Packages
  bot/                               # Phase 6: Trading bot gate
  corpus/                            # Phase 4: Corpus recurrence aggregation
  discovery/                         # Discovery grid lock & search
  governance/                        # Phase 2: Evidence ledger & replay
  miner/                             # Phase 3: Frozen-grid vectorized sweep
  shadow/                            # Phase 5: Fill-model simulation
  validator/                         # Phase 1: Statistical estimators (DSR, CPCV, PBO, FDR)
  
  # Runners (40+ CLI entry points)
  run_*.py                           # One runner per experiment type
  
  tests/                             # 50+ test files
  data/                              # Captured trade data (JSONL)
  corpora/                           # Stress-test corpora
  reports/                           # Research outputs
  systemd/                           # Systemd service files for gate watcher
  precommitments/                    # Precommitment JSON files
  docs/                              # Documentation (not source of truth per audit rules)
```

### Data Flow Diagram

```
Live Exchange APIs (Binance, Kraken, Coinbase, DEX Screener)
  │
  ▼
┌─────────────────────┐
│  Data Capture (run_*) │  ← Volatility gate gating
│  JSONL trade files    │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  validate_capture.py │  ← Integrity check
└─────────┬───────────┘
          │
          ▼
┌─────────────────────────────────────────────┐
│  Signal Generators                          │
│  ┌──────────┬──────────┬─────────┬────────┐ │
│  │ TickLL   │ TradFlow │ Deriv   │ DEX    │ │
│  │ LeadLag  │ Impulse  │ Impulse │ Disloc │ │
│  └──────────┴──────────┴─────────┴────────┘ │
│         │         │         │         │      │
│         └─────────┴┬────────┴─────────┘      │
│                   │                          │
│                   ▼                          │
│         TickSignalEvent (unified)            │
└─────────────────────┬───────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────┐
│  event_study.evaluate_tick_signal()           │
│  → TickForwardReturn per horizon              │
│  → generate_random_baseline()                 │
│  → evaluate_candidate_group() (6-gate check)   │
└─────────────────────┬────────────────────────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
┌────────────┐ ┌──────────┐ ┌──────────────┐
│ cost_sens  │ │ consis-  │ │ candidate_   │
│ itivity.py │ │ tency.py │ │ falsific.py  │
└─────┬──────┘ └────┬─────┘ └──────┬───────┘
      │             │              │
      ▼             ▼              ▼
┌──────────────────────────────────────────────┐
│  Stage 2 Pipeline (precommitment-gated)       │
│  split_corpus → fdr → check_criteria          │
│  → discovery survivors → frozen configs       │
│  → holdout evaluation                         │
└─────────────────────┬────────────────────────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
┌────────────┐ ┌──────────┐ ┌──────────────┐
│  Miner     │ │Validator │ │  Corpus      │
│  (grid     │ │(DSR,CPCV │ │  Aggregation │
│   sweep)   │ │ PBO,FDR) │ │              │
└─────┬──────┘ └────┬─────┘ └──────┬───────┘
      │             │              │
      ▼             ▼              ▼
┌──────────────────────────────────────────────┐
│  Governance Ledger (append-only JSONL)        │
│  Events: grid_lock, candidate_lock,           │
│  estimator_evidence, approval, revocation,    │
│  demotion, supersession, kill_switch          │
└─────────────────────┬────────────────────────┘
                      │
                      ▼
┌──────────────────────────┐
│  Bot Gate (ledger replay) │
│  → authorize || fail-closed
└──────────────────────────┘
```

### Key Architectural Patterns

1. **Two-tier data model**: Bar-level (SignalEvent, ForwardReturnResult) for OHLCV work; tick-level (TickSignalEvent, TickForwardReturn) with nanosecond integer timestamps. Never mixed.

2. **Adapter pattern for signal unification**: `impulse_to_tick_signal()`, `dex_event_to_tick_signal()` convert domain-specific events → shared `TickSignalEvent`. All evaluation goes through `event_study.evaluate_tick_signal()` with zero domain knowledge.

3. **Frozenset verdict guards**: Diagnostic modules constrain output verdicts via frozenset ALLOWED_VERDICTS/FORBIDDEN_VERDICTS — preventing accidental promotion of diagnostic results to execution-readiness.

4. **Append-only state**: Run index, quarantine, burn — all append-only JSONL. Never mutated in-place.

5. **Precommitment locks**: SHA-256 hash lock on precommitment files at collection start. Every later stage verifies hashes haven't changed — prevents post-hoc threshold tuning.

6. **Frozen grids**: Hash-identified grid definitions that cannot be mutated during a run family. Grid identity is established by content hash.

7. **Ledger-derived authorization**: Approval derived by event-log replay, not from trusting a manifest file. Revocation/kill events have hard precedence.

8. **GPU-acceleration slots**: Separate `*_gpu.py` modules with CPU fallback. Currently only `signed_imbalance` is fully vectorized on GPU.

---

## 3. Startup & Bootstrap

There is no single "application startup." The codebase is a library of research scripts, each run independently:

### CLI Runner Pattern
Each `run_*.py` file follows a consistent pattern:
1. Parse CLI args with argparse
2. Load/browse for data files (trades, CSVs, manifests)
3. Instantiate generator/evaluator
4. Run computation
5. Write outputs (JSONL/CSV/JSON/MD)

### Stage 2 Gate Watcher Daemon
The one persistent daemon is `stage2_gate_watcher.py`. Its lifecycle:
1. **Startup reconciliation**: Detects stale flags/partial captures from previous crashes
2. **Concurrency lock**: Acquires `flock()`-based lock preventing duplicate instances
3. **Readiness check**: Runs `stage2_readiness_check.py` — checks module imports, precommitment file integrity, git tracking, Hermes gateway status, quarantine/burn directory conventions
4. **Main loop**: Polls volatility gate (BTC 1h >= 150 bps with acceleration) + stress-v2 trigger (30 bps/30s impulse on BTC/ETH). When triggered: cooldown check → collection lock → spawn capture subprocess → validate → count corpus windows
5. **Corpus readiness**: Reports diagnostic status at 10 windows, rerun-ready at 20 windows

### Systemd Service
- Service file: `systemd/nautilus-stage2-gate-watcher.service`
- Setup script: `systemd/setup_stage2_worktree_service.sh`
- Runs from a git worktree at `/mnt/nasirjones/py/nautilus_trader_stage2_runtime` for clean git SHA tracking

---

## 4. Core Flows

### 4.1 Signal Generation Pipeline

The project implements four signal generator families, all producing a unified `TickSignalEvent`:

#### a) Tick Lead-Lag (`event_study.py`)
- **Class:** `TickLeadLagGenerator`
- **Mechanism:** Walks sorted source-venue trade ticks with sliding window. Emits signal when price moves >= threshold_bps over lookback_ms window.
- **Parameters:** Lookback (ms) × threshold (bps) cartesian product
- **Cooldown:** Per (lookback, threshold) pair, default 10s

#### b) Trade-Flow Impulse (`trade_flow_impulse.py`)
- **Class:** `TradeFlowImpulseSignalGenerator`
- **4 signal types:**
  - `count_burst`: Trade count in lookback > rolling median × multiplier (3x)
  - `notional_burst`: Notional sum in lookback > rolling median × 3x
  - `large_trade`: Single trade > min_notional OR > rolling median × 5x
  - `signed_imbalance`: (buy - sell) / total >= 0.65 threshold
- **GPU variant** (`trade_flow_impulse_gpu.py`): Only `signed_imbalance_gpu` fully vectorized; others still CPU-bound

#### c) Derivatives Impulse (`derivatives_lead_lag.py`)
- **Class:** `DerivativesImpulseGenerator`
- **3 signal types:** notional_burst, price_shock, signed_imbalance
- **Conversion:** `impulse_to_tick_signal()` maps `DerivativeImpulseEvent` → `TickSignalEvent`

#### d) DEX-CEX Dislocation (`dex_cex_dislocation.py`)
- **Class:** `DexCexDislocationDetector`
- **3 signal types:** price_shock (>=50 bps), volume_burst (rolling × 3), liquidity_shock (>=200 bps)
- **Hard filters:** $500K minimum liquidity, $100K 1h volume
- **Conversion:** `dex_event_to_tick_signal()` maps → `TickSignalEvent`

#### e) Cross-Asset Impulse (`cross_asset_impulse.py`)
- **Purpose:** Cross-asset evaluator (BTC/ETH source → alt targets)
- **Orchestrator pattern:** Uses `TradeFlowImpulseSignalGenerator` for source signals, then remaps signals to each target pair
- **Complex verdict logic:** `compute_verdict()` returns `NEEDS_MORE_DATA`, `SINGLE_PAIR_CANDIDATE_DIAGNOSTIC`, `CANDIDATE_FOR_LONGER_OBSERVATION`, or `REJECTED`
- **Key metric:** Positive-beta propagation — only bullish source signals are `long_executable`; bearish signals are `diagnostic_only`

### 4.2 Forward-Return Evaluation

**Core evaluator:** `event_study.evaluate_tick_signal()`
- Uses `bisect.bisect_left` for O(log n) timestamp lookup on target ticks
- Computes raw return bps, direction-adjusted return, fee/slippage/quote-mismatch costs
- Returns `TickForwardReturn` per horizon
- Rejects: no entry price, no forward price, non-finite prices, zero-division

**Random baseline:** `generate_random_baseline()`
- Uniform random timestamps across source tick range
- Directions, lookbacks, thresholds chosen at random
- Seed-controlled reproducibility

**Candidate gate:** `evaluate_candidate_group()`
- 6-gate check against baseline: min events (50), mean net > baseline + margin, win rate, distribution stats

### 4.3 Diagnostics Pipeline

**Cost sensitivity** (`cost_sensitivity.py`):
- Computes breakeven cost per evaluated group
- Default cost levels: 50, 10, 5, 1, 0.5 bps
- Verdicts: `COST_SENSITIVITY_READY`, `NO_EVALUATED_GROUPS`, `NO_FINITE_GROUPS`
- Forbidden: any "promotion" verdict

**Cross-capture consistency** (`cross_capture_consistency.py`):
- Aggregates multiple evaluated report directories by exact `GroupKey` (8 fields)
- Computes consistency scores: 0.5*recurrence + 0.3*raw_consistency + 0.2*net_consistency
- Diagnostic labels: `recurring near-miss`, `cost-wall constrained`, `needs more captures`
- Verdicts: `CROSS_CAPTURE_CONSISTENCY_READY`, `NO_REPORTS_FOUND`, etc.

**Candidate falsification** (`candidate_falsification.py`):
- Merges all evidence: evaluated groups + cost sensitivity + null test + heatmap + consistency
- Weighted score per group: +1 per positive signal, -1 per missing/null flag
- Statuses: `survives diagnostic filter`, `blocked by costs`, `near-miss`, `needs more evidence`
- 6 allowed verdicts, 8 forbidden verdicts (execution/promotion)

**Stress labeling** (`stress_labels.py`, `stress_corpus.py`, `stress_corpus_accumulator.py`):
- Deterministic BTC/ETH stress window rules: 30s move >= 30 bps, 60s move >= 50 bps, rolling range >= 75 bps
- 30-second cooldown between labels
- Corpus builder checks target asset coverage (SOL, LINK, DOGE, AVAX)
- Accumulator variant merges overlapping labels with 30-minute cooldown, requires ALL 4 targets
- Target: 20 usable stress windows for rerun trigger

### 4.4 Statistical Validator (Phase 1)

The `validator/` package provides a suite of statistical estimators:

| Module | Class | Purpose | Verdicts |
|--------|-------|---------|----------|
| `dsr.py` | Deflated Sharpe Ratio | Candidate-level deflated performance (Bailey & Lopez de Prado 2014) | PASS/FAIL/INSUFFICIENT_DATA/ERROR |
| `cpcv.py` | Combinatorial Purged CV | Honest performance with time-domain embargo | Aggregate metrics + non-stationarity detection |
| `pbo.py` | Probability of Backtest Overfitting | Grid-level CSCV (Bailey et al 2016) | OVERFIT_SUSPECTED if PBO > 0.5 |
| `fdr.py` | FDR Control | BY (default) or BH correction | Rejection decisions + adjusted p-values |
| `effective_trials.py` | Effective trial count | Correlation clustering of return series | cluster_assignments |
| `embargo.py` | Time-domain embargo | Purging + embargo for CPCV | — |
| `metadata.py` | Estimator metadata | Identity/version tracking | — |
| `synthetic.py` | Synthetic data generators | 4 populations for testing | — |
| `summary.py` | ValidatorSummary | Aggregates all estimators | DIAGNOSTIC_PASS/FAIL |

### 4.5 Edge Miner (Phase 3)

The `miner/` package implements frozen-grid vectorized sweeps:

**Grid definition** (`grid.py`):
- `GridSpec` → `FrozenGrid` with content hash
- `GridCell`: source_venue, source_instrument, target_venue, target_instrument, entry_delay, forward_horizon, signal_type
- Cells can be `blocked_by_locked_rejection` with `structural_change_rationale`

**Rejection guard** (`rejection_guard.py`):
- Prevents laundering of locked-rejected hypotheses as "just another grid cell"
- `KNOWN_LOCKED_REJECTION_KEYS`: 4 keys for previously rejected families
- Cells need human-authored `structural_change_rationale` to bypass

**Sweep** (`sweep.py`):
- `run_sweep(grid, observation_fn)` — calls `observation_fn` per active cell
- Returns `SweepResult` with per-cell metrics (mean_return, hit_rate, sharpe_like, return_series)
- Invalid cells are skipped with reason codes, not failed

### 4.6 Governance & Bot

**Evidence ledger** (`governance/ledger.py`):
- Append-only JSONL-backed event store
- Events: GRID_LOCK, CANDIDATE_LOCK, ESTIMATOR_EVIDENCE, APPROVAL, REVOCATION, DEMOTION, SUPERSESSION, KILL_SWITCH, LEDGER_INTEGRITY
- Monotone `ledger_index`, SHA-256 per-event hash
- `LedgerIntegrityError` on any anomaly

**Ledger replay** (`governance/replay.py`):
- Derives current `CandidateState` by replaying events in order
- Rules: later ledger_index wins (except REVOCATION/KILL_SWITCH/LEDGER_INTEGRITY have hard precedence)
- Unknown event types cause fail-closed replay
- Missing/corrupt ledger → no approval

**Bot gate** (`bot/gate.py`):
- `BotGate.authorize(candidate_hash)` — 4-step check:
  1. Replay ledger (fail closed)
  2. Confirm candidate state is tradeable (approved, not revoked/killed/demoted/superseded)
  3. Load manifest (must exist + contain candidate)
  4. Check manifest expiry

**Manifest** (`bot/manifest.py`):
- `ApprovedManifest`: list of `ManifestRecord` with conditions and limits
- Loaded from JSON, hash-verified
- Manifest alone is NOT sufficient — current ledger replay is required

### 4.7 Shadow Execution (Phase 5)

The `shadow/` package simulates fill models for signal-triggered cross-venue entry:

**Fill model** (`fill_model.py`):
- `CrossVenueFillModel`: configurable entry delay, staleness, maker/taker probability, queue depth
- Simulates: trigger → entry (maker/taker/missed/staleness_reject) → exit
- All results carry uncertainty (±15 bps fill_model_uncertainty_bps)

**Shadow executor** (`shadow_executor.py`):
- `run_shadow()`: runs fill model over signal event sequence
- Pass requires: `shadow_net_bps > 0` AND `lower_confidence_bound > 0`
- Positive mean with wider uncertainty than edge is NOT a pass

### 4.8 Research Report Miner

`research_report_miner.py` (556 lines) walks all report directories, parses JSON/JSONL/CSV/MD files, and produces a unified bps-gated research-status table. Key features:
- Recursive JSON extraction with max depth 6
- Verdict normalization (REGEX-based)
- De-duplication by (study, symbol, venue, signal_type, net_bps)
- Output: JSON, CSV, Markdown

---

## 5. Platform/Service Inventory

| Platform | Transport | Auth | Purpose | Quirks |
|----------|-----------|------|---------|--------|
| Binance | REST (httpx, public) | None | OHLCV klines, ticker prices | 1000 bars/request, 250ms rate limit |
| Kraken | REST (httpx, public) | None | OHLCV klines | 720 bars/request, returns "last" key separately |
| Coinbase | REST (httpx, public) | None | OHLCV candles | No start/end filters — client-side filter applied. ~300 bar max |
| DEX Screener | REST (httpx) | None | DEX pool snapshots | Search endpoint returns stale/cached prices; 5m volume is cumulative |
| Binance Vision | Archive files | None | Historical funding + spot | Explicitly archived; don't assume calendar availability |
| Polymarket CLOB | REST | None | Orderbook liquidity probe | CLOB orderbook; near-expiry depth collapses |

---

## 6. Shared Utilities

| Module | Purpose | Consumes | Produces |
|--------|---------|----------|----------|
| `artifact_metadata.py` | Git SHA, schema version, safety mode injection | git commands, CLI args | Metadata dict for manifests |
| `burn.py` | Burn-file JSONL convention | signal_family, run_ids, precommitment SHA | Append-only burn records |
| `csv_normalizer.py` | OHLCV grid alignment | CSV files | Aligned (ts, source, target) arrays |
| `symbol_aliases.py` | Canonical symbol resolution | Raw symbol strings | CanonicalSymbol(asset, quote) |
| `tick_store.py` | Tick data storage | TradeTickLite lists | JSONL files |
| `data_loading.py` | Data loading | file paths | Trade ticks / OHLCV data |
| `data_fetcher.py` | HTTP data fetch | venue, symbol, interval | Raw exchange data |
| `data_download.py` | Bulk download | config | Local CSV/JSONL files |
| `validate_capture.py` | Capture integrity check | Capture directories | Validation verdicts |

---

## 7. Persistence & Schema

### Data Files
- **Trade data:** JSONL files at `data/derivatives_spot_capture_v2_*/*.jsonl` — one file per (venue, symbol) pair per capture
- **Artifact manifests:** `capture_manifest.json` in each capture directory — includes `_metadata` with schema version, git SHA, capture mode, volatility gate snapshot, safety mode
- **Reports:** JSONL + JSON + CSV + MD files under `reports/`
- **Corpora:** JSON manifests + label JSONL under `corpora/`

### Ledger
- **Format:** Append-only JSONL
- **Schema:** Each event: `ledger_index`, `event_type`, `event_time_utc`, `written_at_utc`, `candidate_hash`, `grid_hash`, `payload`, `event_hash`
- **Integrity:** Monotone index, SHA-256 per-event hash, hash verification on every read

### Burn File
- **Format:** Append-only JSONL at `reports/research_run_burned.jsonl`
- **Schema:** `burn_id`, `signal_family`, `reason`, `precommitment_git_sha`, `precommitment_json_sha256`, `run_ids`, `discovery_run_ids`, `test_run_ids`
- **Conventions:** `burn_corpus()` writes one row; `get_burned_run_ids()` aggregates across all records

### Quarantine
- Referenced in tests and validation code; JSONL file at `reports/research_run_quarantine.jsonl`

### Schema Versions
- Current: `1.0.0`
- Supported: `{1.0.0, v0}` (backward compat with v0 artifacts)
- Version compatibility checking via `check_schema_version()` in `artifact_metadata.py`

---

## 8. Frontend Architecture

**Not applicable.** This is a pure CLI/daemon research pipeline with no web frontend, no SPA, no service worker, no browser-based UI. Output is via JSONL/JSON/CSV/Markdown files.

---

## 9. Background Jobs & Scheduled Tasks

### Stage 2 Gate Watcher
- **Service:** `systemd/nautilus-stage2-gate-watcher.service`
- **Runtime:** Runs from git worktree at `/mnt/nasirjones/py/nautilus_trader_stage2_runtime`
- **Frequency:** Continuous polling (every 2s for price feed)
- **Volatility gate params:** BTC 1h >= 150 bps with acceleration
- **Stress trigger params:** 30 bps / 30s impulse on BTC/ETH ANDed with crypto-native stress confirmation
- **Systemd env vars:** `NAUTILUS_STAGE2_*` point reports/data to main repo
- **Concurrency:** `flock()`-based lock prevents duplicate instances; process-local `CaptureGuard` flag file

### Hermes Scheduled Jobs
- The Stage 2 readiness check references a Hermes scheduled job (ID: `85a4d56145fe`) for the stress trigger feed
- When the gate watcher detects a newer git SHA in main repo code paths (precommitment, watcher, config, capture, evaluation), it deploys it

---

## 10. Middleware & Cross-Cutting Concerns

| Concern | Mechanism | Scope |
|---------|-----------|-------|
| Observer-only safety | `SAFETY_MODE = "public_data_observer_only"` in every module | All research code |
| Verdict guards | Frozenset `ALLOWED_VERDICTS` / `FORBIDDEN_VERDICTS` | Diagnostic modules |
| Precommitment integrity | SHA-256 hash lock at collection start | Stage 2 pipeline |
| Rejection guard | `check_rejection_guard()` against `KNOWN_LOCKED_REJECTION_KEYS` | Miner sweeps |
| Ledger-derived auth | Event-log replay, not manifest trust | Bot gate |
| Append-only state | JSONL files, never in-place mutation | Burn, quarantine, ledger |
| Schema versioning | `check_schema_version()` with backward compat | All artifact readers |
| Git SHA tracking | `_get_git_sha()`, `_get_git_sha_dirty()`, `_get_git_branch()` | artifact_metadata |
| Redacted secrets | Pattern-based redaction in `_args_to_dict()` | artifact_metadata |
| Cooldown enforcement | Per (signal_type, lookback) cooldown tracking | All signal generators |
| Stress cooldown | 3600s minimum between corpus-eligible captures | Stage 2 watcher |

---

## 11. Type System & Contracts

### Bar-Level Models (models.py)
- `SignalEvent`: signal_id, timestamp, source/target venue/instrument, signal_type, direction, strength
- `ForwardReturnResult`: signal_id + horizon + entry/forward prices + fee/slippage costs + net return + MAE/MFE
- `HorizonSummary`: aggregated stats per horizon
- `SignalEvaluationSummary`: overall run summary with by-horizon, by-type, by-venue-pair breakdowns

### Tick-Level Models (tick_models.py)
- `TradeTickLite`: ts_event (int ns), venue, symbol, price, size, side
- `QuoteTickLite`: ts_event, venue, symbol, bid, ask, bid_size, ask_size (+ mid/spread_bps properties)
- `TickSignalEvent`: signal_id, ts_event, source/target, asset, signal_type, direction, lookback_ms, threshold_bps, source_move_bps, strength
- `TickForwardReturn`: signal_id, signal_ts, target, horizon_ms, entry/forward prices, returns, costs, valid flag

### Derivatives Models (derivatives_models.py)
- `DerivativeTradeTick`: extends TradeTickLite semantic with .notional property
- `OpenInterestSnapshot`: ts_event, venue, symbol, open_interest
- `FundingSnapshot`: ts_event, venue, symbol, funding_rate, funding_apr, mark/index prices
- `DerivativeImpulseEvent`: signal_id, ts_event, source/target, signal_type, direction, strength, lookback_ms, OI/funding context
- `DerivativeLeadLagResult`: similar to TickForwardReturn with latency_buffer_bps

### DEX Models (dex_models.py)
- `DexPoolSnapshot`: chain, dex, pair_address, base/quote, price, liquidity, volumes, txns, price changes
- `DexDislocationEvent`: event_id, chain, dex, pair, signal_type, direction, strength fields
- `DexCexForwardResult`: event_id + asset + source/target + horizon + costs + net

### Config Models (config.py)
- `Horizon`: name + seconds
- `FeeModel`: fee_bps + slippage_bps + quote_mismatch_buffer_bps
- `SignalSourceConfig`: CSV path + mapping (OR) cross-market signal params
- `ObserverConfig`: horizons + fee model + signal source + data paths + filtering
- `LeadLagConfig`: venue pair + lookback windows + thresholds + cooldown + grid

---

## 12. Configuration & Environment

### CLI Args
Every `run_*.py` has its own argparse. Common patterns:
- `--data-dir`, `--output-dir` for paths
- `--source-venue`, `--target-venue`, `--symbol`
- `--capture-mode` (FULL_ACTIVE | FAST_DIAGNOSTIC)
- `--fee-bps`, `--slippage-bps` for cost assumptions
- `--seed` for reproducibility

### Environment Variables
- `NAUTILUS_STAGE2_*` — Stage 2 worktree environment (path overrides)
- No API keys required for research (public endpoints only)

### Filesystem Layout
- Repo root: `/mnt/nasirjones/py/nautilus_trader/`
- Klients worktree: `/mnt/nasirjones/py/nautilus_trader_stage2_runtime/`
- Wiki: `/home/pry/wiki/` (Obsidian-compatible)
- All secrets/tokens are [REDACTED] in this report

### NFS Caveat
All files live on Synology NFS mount at `/mnt/nasirjones/web/taybi.uk/`. Build scripts must not be run from this machine — the user runs them themselves.

---

## 13. CI/CD Pipeline

**No CI/CD pipeline exists.** The `.github/workflows/` directory was checked in the parent NautilusTrader repo (not this scaffold). This research scaffold has no automated testing pipeline. The AGENTS.md notes that NautilusTrader's full test suite is available but cannot be run on the NAS (GLIBC limitation).

---

## 14. Security Posture

### Research-Only Design
- Every module explicitly declares observer-only semantics
- No live execution clients imported
- No API keys/tokens in source code
- CLI args optionally redact sensitive fields via `_args_to_dict()`

### Bot Gate Security
- `BotGate.authorize()` implements 4 independent checks before allowing execution
- Fail-closed on any integrity error, corrupt ledger, missing manifest, or expired record
- Ledger replay is the source of authorization truth — manifests alone are insufficient

### Ledger Integrity
- SHA-256 per-event hash linked to all fields
- Monotone `ledger_index` enforced on append
- Hash verification on every `read_all()` call
- Unknown event types cause fail-closed replay
- `LEDGER_INTEGRITY` event type allows external integrity signals

### Rejection Guard
- Prevents locked-rejected hypotheses from being re-tested without structural-change rationale
- `KNOWN_LOCKED_REJECTION_KEYS` maintained as a living list
- Structural-change rationale must be human-authored and hash-covered

### No Secrets in Source
- All tokens/passwords are referenced only via environment variables or secret managers
- `_args_to_dict()` redacts keys containing: secret, key, password, token, auth

---

## 15. Known Patterns & Conventions

### Naming Conventions
- **Files/Directories:** `snake_case`
- **Dataclasses:** PascalCase
- **Functions/methods:** `snake_case`
- **Constants:** UPPER_SNAKE_CASE
- **Private methods:** `_leading_underscore`
- **Signal types:** lowercase_with_underscores (e.g., `count_burst`, `notional_burst`)
- **Verdict strings:** UPPER_SNAKE_CASE (e.g., `REJECTED`, `NEEDS_MORE_DATA`)

### Code Organization
- One class per file for signal generators (single responsibility)
- Dataclasses in dedicated files by domain (tick_models, derivatives_models, dex_models)
- Config frozen dataclasses in `config.py`
- Runner files named `run_<purpose>.py` — each is a CLI entry point
- Cross-cutting utilities in `symbol_aliases.py`, `artifact_metadata.py`, `csv_normalizer.py`

### Error Handling
- `ValueError` for invalid input/state (not custom exceptions)
- `LedgerIntegrityError` specific to governance ledger operations
- All HTTP calls use `raise_for_status()` or explicit error checking
- Validation functions return (ok, reason) tuples, not exceptions
- Null/missing values represented as `None`, not sentinel values

### Safety Patterns
- Frozenset verdict guards in every diagnostic module
- Forbidden verdict sets that cannot be reached by accident
- `SAFETY_MODE` constant at module level
- `capture_mode` metadata tracks FULL_ACTIVE vs FAST_DIAGNOSTIC vs empty
- `validate_verdict()` function raises on forbidden verdicts

### Performance Patterns
- `bisect.bisect_left` for O(log n) window lookups
- Pre-allocated lists, not repeated appends in tight loops
- Rolling window baselines with sampling (not full computation)
- GPU fallback paths in separate modules (not interleaved)
- Prefix sums (`torch.cumsum`) for O(1) window aggregation

---

## 16. Dependency Map

| Dependency | Version | Purpose |
|------------|---------|---------|
| Python | >=3.12, <3.15 | Runtime |
| NautilusTrader | 1.227.0 | Core trading engine (Rust extensions) |
| Click | >=8.0.0 | CLI arg parsing |
| httpx | (via Nautilus) | HTTP client for public exchange APIs |
| numpy | >=1.26.4 | Numerical operations |
| pandas | >=2.3.3 | Data manipulation (limited use) |
| PyTorch | (optional) | GPU-accelerated signal generation |
| ruff | 0.15.12 | Linting |
| pytest | 7.x | Testing |
| uv | 0.11.12 | Python environment management |

---

## 17. Edge Cases & Operational Notes

### Platform-Specific
- **Synology NAS:** NFS mount at `/mnt/nasirjones/web/taybi.uk/`. GLIBC 2.36 prevents running NautilusTrader Rust extensions locally.
- **Coinbase API:** No start/end filters on candles endpoint — returns most recent ~300 bars only. Client-side filtering applied.
- **DEX Screener:** Search API returns stale/cached prices during quiet hours. 5m volume data is cumulative, not per-interval.
- **Binance Vision:** Archive-based data; calendar availability must be explicitly checked (never assumed).
- **Spot kline timestamps:** millisecond precision pre-2025, microsecond 2025+. Parser detects dynamically.

### Known Failure Modes
- **uv version mismatch:** pyproject.toml requires 0.11.12, runtime may differ.
- **No retry logic in REST fetchers:** Single-attempt HTTP calls in data_adapters.py — no retry/backoff.
- **Pickle coupling:** The parent research scaffold uses pickle for backtest results (fragile across Python versions).
- **Baseline window too large:** 60s baseline window is too large for 300-600s captures (causes 9 NEEDS_MORE_DATA in rejection registry).
- **GPU path not always faster:** `notional_burst_gpu` and `large_trade_gpu` are not faster than CPU for per-index baseline median computation — fully vectorized sliding-window median kernel would be needed.

### Blocked Hypotheses
- **10 locked gates** documented in REJECTED_RESEARCH.md — cannot be re-tested without structural change
- **Reopening requirements:** Different execution model (maker/rebate), different venue microstructure, different data type, or genuinely different signal family
- **Derivatives v2 lead-lag:** Best raw edge 0.098 bps vs 50 bps all-in cost. 0 recurring groups across captures. Closed unless HFT-grade colocation or much lower fees.
- **Family 2 funding reversal:** 0/60 cells survived. Signal absent, not cost-walled. 6 bps diagnostic tier changed no verdicts.

### Open Questions
- Cross-asset beta lag under stress — only testable during genuine volatility windows
- OI + price regime classification (filter, not standalone trade)
- Options IV/RV regime overlay

---

## 18. Testing Posture

### Test Infrastructure
- **Framework:** pytest 7.x (intentionally held at 7.x by NautilusTrader)
- **Runtime:** Strict `asyncio_mode`, session-scoped default fixture loop
- **Runner:** `uv run -m pytest ...`
- **50+ test files** covering all major modules

### Notable Test Files

| File | Focus | Notes |
|------|-------|-------|
| `test_all.py` | Catch-all / integration | |
| `test_stage2.py` | Stage 2 pipeline | References quarantine |
| `test_stage2_gate_watcher.py` | Gate watcher daemon | References quarantine |
| `test_cross_asset_beta_lag_stress_v2.py` | Cross-asset stress v2 | References quarantine |
| `test_event_window_precommitment.py` | Event window precommitment | References quarantine |
| `test_cross_venue_lead_lag.py` | Lead-lag pipeline integration | References event_study |
| `test_tick_lead_lag_pipeline.py` | Tick lead-lag pipeline | References event_study |
| `test_forward_returns_gpu.py` | GPU-accelerated returns | References event_study + forward_returns_gpu |
| `test_cross_asset_impulse.py` | Cross-asset estimator | References event_study |
| `test_dex_cex_dislocation.py` | DEX-CEX dislocation | References event_study |
| `test_candidate_falsification.py` | Falsification matrix | |
| `test_cost_sensitivity.py` | Cost sensitivity | |
| `test_cross_capture_consistency.py` | Cross-capture consistency | |
| `test_discovery_*.py` | Discovery module (7 files) | candidate_lock, grid_lock, promotion_boundary, safety_scan, search_space |
| `test_offline_*.py` | Offline pipeline (10+ files) | | 
| `test_validator_*.py` | Validator package | phasing |
| `test_gpu_devices.py` | GPU device detection | |
| `test_multi_gpu_wrappers.py` | Multi-GPU interop | |
| `benchmark_multi_gpu.py` | GPU benchmark | |
| `test_audit_regression.py` | Regression against prior audits | References event_study |

### Environment Limitation
NautilusTrader tests cannot run on the Synology NAS (GLIBC 2.36 < 2.39 required by Rust extensions). Tests were not executed during this audit. All analysis is based on source code reading.

---

## 19. Top Priorities / Recommendations

### Critical
1. **Run the test suite on a compatible machine** — the 50+ test files have never been executed in this session. Some may have regressed as the codebase evolved rapidly.
2. **Add retry/backoff to REST fetchers** — `data_adapters.py` has single-attempt HTTP calls. For a 24/7 gate watcher, transient network failures will cause unnecessary capture gaps.

### High
3. **Decompose `stage2_gate_watcher.py`** (1,598 lines) — This is the largest file by far and handles daemon lifecycle, trigger evaluation, capture orchestration, validation, and corpus counting. Split into smaller modules with single responsibility.
4. **Decompose `cross_asset_impulse.py`** (839 lines) — The most complex orchestrator. Stream health tracking, overlap computation, verdict logic, and report generation should be separated.
5. **Fix the GPU path performance note** — Document why `notional_burst_gpu` and `large_trade_gpu` aren't faster than CPU, and whether a fully vectorized sliding-window median kernel is worth the effort.
6. **Unify runner pattern** — 40+ `run_*.py` files each have their own argparse. Consider a shared CLI framework (click-based or argparse with shared base parser).

### Medium
7. **Add missing `__init__.py` docstrings** — The packages mostly have them, but the root-level modules lack package-level documentation.
8. **Standardize logging** — Some modules use stdout, some stderr, some Python logger. The gate watcher uses structured JSON logging, but signal generators don't log at all.
9. **Document the `OI_SHIFT` signal type** — The `DerivativeImpulseEvent` model defines `oi_shift` as a signal type, but no generator produces it yet.
10. **Remove duplicate utility code** — `_mean()` and `_std()` are defined in `sweep.py`, `shadow_executor.py`, `corpus/aggregator.py`, and probably others. Extract to a shared `utils.py`.

### Low
11. **Add `py.typed` markers** — Only present in NautilusTrader core, not in the research scaffold.
12. **Empty project directories** — Several subdirectories may exist without source files (common scaffold accretion).
13. **Consider dropping the `quarantine.py` reference** — The file is referenced in AGENTS.md and test files but wasn't found in the source tree (may have been renamed/removed).
14. **Document the 10-layer architecture explicitly** — The layered pattern is emergent and clean; an explicit LAYERS.md would help newcomers understand the import hierarchy.
