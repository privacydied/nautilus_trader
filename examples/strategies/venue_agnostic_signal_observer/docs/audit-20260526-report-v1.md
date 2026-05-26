# PRODUCT AUDIT — Venue-Agnostic Signal Observer
## Date: 2026-05-26
## Version: v1

---

## 1. Executive Summary

The venue-agnostic signal observer is a **quantitative trading research pipeline** for evaluating cross-venue, cross-asset tick-level signal hypotheses on public cryptocurrency market data. It is observer-only — no execution, no orders, no private keys, no live trading code is reachable from any research path. The project comprises ~100+ source files, ~74 test files, and operates within the NautilusTrader framework at `/mnt/nasirjones/py/nautilus_trader/examples/strategies/venue_agnostic_signal_observer/`.

**Primary value proposition:** A structured, append-only, precommitted research scaffold that rejects hypotheses with evidence rather than optimizing toward false discovery. The project has evaluated 49+ study groups across 8+ signal families (venue-agnostic lead-lag, derivatives-source→spot, DEX→CEX dislocation, Polymarket binary options, Hyperliquid funding, liquidation flush reversal, cross-asset beta lag, generic altcoin stress) — with 37 REJECTED, 11 NEEDS_MORE_DATA, and 1 MARKET_MODERATE_DIAGNOSTIC. No signal family has been promoted to paper or live execution.

**Key capabilities:**
- Tick-level (nanosecond) and bar-level (second/minute) signal generation
- Forward-return measurement with realistic fee/slippage/latency cost models
- GPU-accelerated evaluation (CUDA via PyTorch) on dual RTX 3090
- Multi-phase falsification: cost sensitivity, cross-capture consistency, permutation null (CPU + GPU), candidate falsification fusion
- Governance infrastructure: append-only JSONL ledger, hash-verified precommitment locks, candidate lock freeze, promotion boundaries
- Stage 2 pipeline: temporal corpus split, BH/BY FDR correction, precommitted gate criteria
- Stage 2 systemd gate watcher for automated stress-window detection and capture orchestration
- Systemd-deployable gate watcher service

---

## 2. Architecture Overview

### Deployment Model
Self-hosted research scaffold on Arch Linux desktop (NAS-mounted NFS source tree). No cloud deployment. No containerization. The NautilusTrader Rust extensions require GLIBC 2.39 but this machine has GLIBC 2.36, so Nautilus imports cannot execute locally — all code is committed and run on a compatible machine.

### Tech Stack
| Component | Technology |
|-----------|-----------|
| Language | Python 3.12+ |
| Framework | NautilusTrader v1.227.0 (Rust-backed) |
| GPU | CUDA via PyTorch (dual RTX 3090) |
| Data serialization | JSONL, Parquet, CSV |
| Config | Frozen dataclasses |
| Testing | pytest 7.x |
| Linting | ruff 0.15.12 |
| Package management | uv |
| Archive storage | Local filesystem + Hyperliquid S3 (requester-pays) |

### Data Flow

```
  PUBLIC APIs / S3
       ↓
  Data Ingestion (data_adapters.py, data_fetcher.py, tick_store.py,
                  csv_normalizer.py, hyperliquid_*.py)
       ↓
  Signal Generation (lead_lag.py, event_study.py, trade_flow_impulse.py,
                     derivatives_lead_lag.py, dex_cex_dislocation.py,
                     cross_asset_impulse.py)
       ↓
  Forward Return Measurement (forward_returns.py, event_study.evaluate_tick_signal(),
                              forward_returns_gpu.py)
       ↓
  Aggregation & Reporting (observer.py, reports.py, reports/cost_sensitivity.py,
                           cross_capture_consistency.py)
       ↓
  Falsification (candidate_falsification.py, permutation_null.py/.gpu)
       ↓
  Stage 2 Pipeline (stage2_split, stage2_fdr, stage2_check_criteria)
       ↓
  Governance (governance/ledger, replay; discovery/locks;
              bot/gate; miner/grid+sweep; validator/*)
```

### 10-Layer Architecture Pattern

```
Layer 1  — Data Models & Config        (models.py, tick_models.py, derivatives_models.py,
                                         dex_models.py, config.py)
Layer 2  — Signal Generators           (signals.py, event_study.TickLeadLagGenerator,
                                         trade_flow_impulse.py, derivatives_lead_lag.py,
                                         dex_cex_dislocation.py, cross_asset_impulse.py)
Layer 3  — Forward Return Evaluation   (forward_returns.py, event_study.evaluate_tick_signal,
                                         forward_returns_gpu.py)
Layer 4  — Observer & Reporting        (observer.py, reports.py, reports/miner.py,
                                         artifact_metadata.py)
Layer 5  — Data Infrastructure         (tick_store.py, data_adapters.py, data_fetcher.py,
                                         data_loading.py, csv_normalizer.py,
                                         data_download.py, burn.py, quarantine.py)
Layer 6  — Domain Adapters             (dex_adapters.py, hyperliquid_observer.py,
                                         hyperliquid_s3_archive.py, symbol_aliases.py)
Layer 7  — Diagnostics                 (cost_sensitivity.py, cross_capture_consistency.py,
                                         latency_diagnostics.py, validate_capture.py,
                                         lead_lag_heatmap_gpu.py, candidate_falsification.py)
Layer 8  — Statistical Falsification   (permutation_null.py, permutation_null_gpu.py,
                                         stage2_fdr.py)
Layer 9  — Governance & Discovery      (discovery/*, governance/*, miner/*, validator/*,
                                         bot/*)
Layer 10 — Runners & Watchers          (run_*.py scripts, stage2_gate_watcher.py,
                                         stage2_split_corpus.py, stage2_check_criteria.py,
                                         stage2_readiness_check.py)
```

Each layer imports only from layers below it. No circular dependencies identified.

### Directory Structure

```
venue_agnostic_signal_observer/
  __init__.py              # Package marker
  __main__.py              # CLI entry point (delegates to run_lead_lag.main)
  config.py                # Frozen dataclass configs
  models.py                # OHLCV-level data models
  tick_models.py           # Tick-level data models (nanosecond precision)
  derivatives_models.py    # Derivatives-market data models
  dex_models.py            # DEX snapshot/dislocation models
  signals.py               # Signal generators (CSV, cross-market)
  observer.py              # Signal observer orchestrator
  forward_returns.py       # OHLCV forward-return computation
  forward_returns_gpu.py   # GPU-accelerated forward returns
  event_study.py           # Tick-level lead-lag event study engine
  lead_lag.py              # OHLCV lead-lag signal generator (superseded)
  tick_store.py            # JSONL tick I/O, sorting, dedup
  trade_flow_impulse.py    # Trade-flow impulse signal generator
  trade_flow_impulse_gpu.py# GPU-accelerated trade-flow impulses
  cross_asset_impulse.py   # Cross-asset impulse evaluator
  derivatives_lead_lag.py  # Derivatives impulse generator
  dex_adapters.py          # DEX Screener / GeckoTerminal API adapters
  dex_cex_dislocation.py   # DEX→CEX dislocation detector
  data_adapters.py         # Multi-venue OHLCV download/alignment
  data_fetcher.py          # Legacy OHLCV fetcher (superseded)
  data_download.py         # CLI download script
  data_loading.py          # CSV loaders + synthetic data
  csv_normalizer.py        # Multi-venue CSV alignment
  latency_diagnostics.py   # Cross-venue tick latency analysis
  reports.py               # OHLCV report writer
  research_report_miner.py # Report directory mining tool
  artifact_metadata.py     # Shared metadata injection
  validate_capture.py      # Capture validator
  burn.py                  # Burn file (JSONL) convention
  quarantine.py            # Quarantine (JSONL) convention
  collect_dex_snapshots.py # DEX snapshot collection
  symbol_aliases.py        # ~100-entry symbol alias registry
  gpu_devices.py           # Multi-GPU utilities
  cost_sensitivity.py      # Breakeven cost diagnostic
  cross_capture_consistency.py  # Cross-capture aggregation
  candidate_falsification.py    # Falsification summary fusion
  permutation_null.py           # CPU permutation null test
  permutation_null_gpu.py       # GPU permutation null test
  lead_lag_heatmap_gpu.py       # Heatmap diagnostic
  generic_altcoin_stress_regime_ablation_phase0a.py  # Altcoin stress detector
  liquidation_flush_aftershock_reversal_phase0a.py   # Liq flush reversal
  liquidation_flush_aftershock_reversal_venue_age_aware_phase0a.py
  liquidation_flush_aftershock_reversal_venue_age_aware_phase0b.py
  liquidation_flush_aftershock_reversal_venue_age_aware_phase0c.py
  hyperliquid_observer.py              # Live HL observer
  hyperliquid_funding_archive_backfill.py  # HL funding backfill
  hyperliquid_funding_divergence_phase0.py # HL funding divergence distribution audit
  hyperliquid_oi_velocity_compression_phase0.py  # HL OI velocity Phase 0
  hyperliquid_asset_ctxs_archive.py       # HL asset_ctxs S3 archive ingestion
  hyperliquid_asset_ctxs_fragment_merge.py   # Fragment merge
  hyperliquid_asset_ctxs_fragment_validation.py  # Fragment validation
  hyperliquid_cost_feasibility.py         # HL cost feasibility
  hyperliquid_s3_archive.py              # HL S3 L2 book archive download
  polymarket_btc_updown_liquidity_probe.py  # Polymarket CLOB probe
  mcpt_export.py              # MCPT export adapter
  stage2_check_criteria.py    # Stage 2 precommitted acceptance criteria
  stage2_fdr.py               # BH/BY FDR correction
  stage2_precommitment_utils.py # Stage 2 shared utilities & collection lock
  stage2_readiness_check.py   # Stage 2 readiness check
  stage2_split_corpus.py      # Temporal discovery/test split
  stage2_gate_watcher.py      # Systemd stress-window gate watcher
  run_*.py (20+ scripts)      # CLI entry points
  discovery/                  # Discovery freeze package (7 files + 3 run scripts)
  governance/                 # Governance ledger package (4 files)
  bot/                        # Bot authorization gate (3 files)
  miner/                      # Edge miner package (4 files)
  validator/                  # Validator package (9 files)
  shadow/                     # Shadow executor package (3 files)
  tests/                      # 74 test files
  docs/                       # AGENTS.md, README.md, REJECTED_RESEARCH.md, precommitments
  reports/                    # Research outputs
  data/                       # Captured data archives
```

---

## 3. Startup & Bootstrap

There is no single application entry point. Each research module has its own CLI runner. The `__main__.py` delegates to `run_lead_lag.main()` for the original OHLCV observer. All other run scripts are standalone entry points:

| Entry Point | Purpose | Dependencies |
|---|---|---|
| `__main__.py` → `run_lead_lag.main()` | OHLCV lead-lag observer | `observer.py`, `forward_returns.py` |
| `run_derivatives_lead_lag.py` | Derivatives impulse evaluation | `derivatives_lead_lag.py` |
| `run_derivatives_spot_capture.py` | Simultaneous same-time capture | Capture infrastructure |
| `run_derivatives_spot_lead_lag.py` | Derivatives→spot lead-lag | `derivatives_lead_lag.py`, `event_study.py` |
| `run_tick_capture.py` | Tick capture | `tick_store.py` |
| `run_tick_lead_lag.py` | Tick lead-lag | `event_study.py` |
| `run_signal_observer.py` | Signal observer | `observer.py` |
| `run_cross_capture_consistency.py` | Cross-capture diag | `cross_capture_consistency.py` |
| `run_cost_sensitivity.py` | Cost sensitivity diag | `cost_sensitivity.py` |
| `run_permutation_null.py` | Null test | `permutation_null.py` |
| `run_candidate_falsification.py` | Falsification fusion | `candidate_falsification.py` |
| `run_index.py` | Run index | N/A |
| `run_stage2_gate_watcher.py` | Gate watcher entry | `stage2_gate_watcher.py` |
| `run_hyperliquid_*.py` | HL-specific runs | Various HL modules |
| `run_polymarket_*.py` | Polymarket probe | `polymarket_btc_updown_liquidity_probe.py` |
| `run_lock_discovery_*.py` | Lock creation/validation | `discovery/*` |
| `run_validate_discovery_grid_lock.py` | Lock validation | `discovery/*` |
| `run_report_corpus.py` | Report corpus | `research_report_miner.py` |

### Startup Sequence (Observer Mode)
1. Load `ObserverConfig` from defaults or CLI override
2. Load signal events (CSV, generator, or pre-loaded list)
3. Load target price data (CSV or synthetic)
4. For each signal × horizon pair, evaluate forward return
5. Aggregate results into `SignalEvaluationSummary`
6. Write outputs (JSONL, JSON, CSV)

### Stage 2 Gate Watcher Startup
1. Readiness check (7 module imports, precommitment files, git SHA, collection lock)
2. Enter polling loop: check volatility gate or stress-v2 trigger every 2s
3. On trigger → check cooldown → subprocess capture → validate → update corpus

---

## 4. Core Flows

### 4.1 OHLCV Lead-Lag (Original Paradigm)

**Hypothesis:** Price moves on source venue (threshold × lookback) predict forward returns on target venue.

**Pipeline:**
1. `data_download.py` or `data_adapters.py` → CSV of OHLCV bars
2. `lead_lag.py:generate_lead_lag_signals()` → scan source prices, emit `SignalEvent` for each threshold-crossing
3. `forward_returns.py:evaluate_signal()` → for each signal × horizon: entry price lookup → horizon price lookup → direction-adjusted return → cost deduction → excursions → `ForwardReturnResult`
4. `observer.py:SignalObserver.run()` → orchestrates 2-3, aggregates into `SignalEvaluationSummary`
5. `reports.py:write_outputs()` → JSONL/CSV/JSON

**Current status:** SUPERSEDED by tick-level event-study paradigm. `lead_lag.py` is effectively dead code.

### 4.2 Tick Lead-Lag (Current Paradigm)

**Hypothesis:** At nanosecond precision, source venue trade ticks crossing a price-move threshold predict target venue forward returns.

**Pipeline:**
1. `run_tick_capture.py` → simultaneous WebSocket capture → JSONL files
2. `tick_store.py:load_trades_jsonl()` → sorted, deduplicated `TradeTickLite` lists
3. `event_study.py:TickLeadLagGenerator.generate()` → sliding-window scanner for each (lookback_ms, threshold_bps) pair → `TickSignalEvent`
4. `event_study.py:evaluate_tick_signal()` → GPU or CPU → `TickForwardReturn`
5. `event_study.py:evaluate_candidate_group()` → 6-gate evaluation vs baseline → gate dict
6. Multi-pass diagnostics: cost_sensitivity → cross_capture_consistency → permutation_null → candidate_falsification

### 4.3 Derivatives-Source → Spot-Target Lead-Lag

**Hypothesis:** Impulse events on derivatives venues (notional burst, price shock, signed imbalance) predict spot forward returns.

**Pipeline:**
1. `run_derivatives_spot_capture.py` → same-time capture of perp + spot × multiple venues
2. `derivatives_lead_lag.py:DerivativesImpulseGenerator.generate()` → 3 signal types across 4 lookback windows
3. `derivatives_lead_lag.py:impulse_to_tick_signal()` → `DerivativeImpulseEvent` → `TickSignalEvent` adapter
4. `event_study.py:evaluate_tick_signal()` → forward returns
5. `cross_asset_impulse.py:generate_source_impulses()` → cross-asset mapping for altcoin targets
6. Stage 2 pipeline: split → FDR → check criteria

**Status:** REJECTED (May 2026) — 63 groups evaluated, best raw edge 0.098 bps vs 50 bps all-in cost. 0 viable at any cost level.

### 4.4 DEX→CEX Dislocation

**Hypothesis:** DEX pool price/volume/liquidity shocks predict CEX forward returns.

**Pipeline:**
1. `dex_adapters.py:search_dexscreener_by_symbols()` or `fetch_dexscreener_pair()` → pool snapshots
2. `dex_cex_dislocation.py:DexCexDislocationDetector.scan()` → 3 signal types (price shock, volume burst, liquidity shock)
3. `dex_cex_dislocation.py:dex_event_to_tick_signal()` → adapter to `TickSignalEvent`
4. Standard forward-return evaluation

**Status:** NEEDS_MORE_DATA — 0 dislocation events from 70 snapshots. DEX Screener search API returns stale/cached data.

### 4.5 Hyperliquid Funding Divergence (Phase 0)

**Hypothesis:** Hyperliquid perp funding divergence from CEX references is a distribution-only diagnostic.

**Pipeline:**
1. Backfill funding history from HL REST API → JSONL
2. Load CEX reference funding (Binance Vision, Bybit v5) → CSV/JSONL
3. Align HL to references (last-observed or median)
4. Compute distribution summary (p50/p75/p90/p95/p99)
5. Phase 0 kill/no-kill decision

**Status:** PHASE0_KILLED_NO_CROSS_SECTIONAL_FUNDING_TAIL — p50 0.035 bps spread, p90 0.194 bps vs frozen kill gates p50 >= 3 bps and p90 >= 10 bps.

### 4.6 Liquidation Flush Aftershock Reversal

**Hypothesis:** Extreme liquidation events (price <= -8%, OI <= -8%) predict aftershock reversal at 6-48h horizons.

**Pipeline:**
1. Load Hyperliquid public archive Parquet/JSONL (OI + price per symbol)
2. Compute 8h rolling points for each symbol
3. Filter candidates: return <= -8% AND OI_change <= -8%
4. Apply 72h per-symbol cooldown
5. Phase 0A: population audit (coverage, concentration gates)
6. Phase 0B: forward-return diagnostic (6/12/24/48h)
7. Phase 0C: falsification (timestamp placebo, month-stratified, circular-shift clustering null)

**Status:** PHASE0C_CLUSTERING_EXPLAINED_WITH_SURVIVORSHIP_AMBIGUITY — Phase 0B positive after 50 bps (mean +125.95 bps, win 56.68%), but Phase 0C circular-shift clustering null failed (p≈0.974), survivorship ambiguity present.

### 4.7 Stage 2 Pipeline (Discovery → Holdout → FDR → Criteria)

Formal multi-capture evaluation pipeline:

1. **Precommitment lock**: `stage2_precommitment_utils.py:CollectionLock` hashes 3 files (markdown, JSON, rationale) + git SHA
2. **Readiness check**: `stage2_readiness_check.py` — 7 module imports, 3 precommitment files, markdown/JSON match, p-value source validation
3. **Temporal split**: `stage2_split_corpus.py` — validates FULL_ACTIVE captures, quarantines/burned exclusion, 70/30 chronological split
4. **Discovery check**: `stage2_check_criteria.py:run_discovery_check()` — min events, mean bps, same-sign fraction, worst floor, BH FDR survival
5. **FDR correction**: `stage2_fdr.py` — BH (primary q=0.10) + BY (sensitivity q=0.10)
6. **Holdout check**: `stage2_check_criteria.py:run_holdout_check()` — frozen configs only

### 4.8 Governance & Discovery Freeze

The discovery freeze package enforces:

- **Grid spec**: 10 primary counted axes + 5 non-multiplicative fields → canonical hash
- **Grid lock**: Freeze at discovery start (grid_id, hash, cell counts, git SHA)
- **Candidate lock**: Freeze selected cells with capture manifest refs → canonical hash
- **Promotion boundary**: Validators must consume candidate lock
- **Safety scan**: AST-level forbidden imports, order terms, env access
- **Evidence ledger**: Append-only JSONL with event_hash integrity check
- **Bot gate**: Derives trading permission exclusively from ledger replay

---

## 5. Platform/Service Inventory

| Platform | Purpose | Transport | Auth | Data Contract |
|---|---|---|---|---|
| Binance USD-M | Perp trade tick + OI | WebSocket | Public | aggTrade, openInterest streams |
| Binance Vision | Historical funding + spot klines + OI metrics | HTTP monthly CSV archives | Public | Monthly zip files |
| Bybit v5 | Historical funding rates | REST API | Public | 8h funding snapshots |
| Kraken spot | Trade ticks + OHLCV | WebSocket + REST | Public | Trade feed, OHLC API |
| Coinbase spot | Trade ticks + candles | WebSocket + REST | Public | match feed, candles (legacy) |
| DEX Screener | DEX pool snapshots | REST API | Public | Search + pair endpoints |
| Polymarket | CLOB orderbook + market data | REST + WebSocket | Public | Gamma API + clob.polymarket.com |
| Hyperliquid | L2 book, trades, funding, asset_ctxs | WebSocket + REST + S3 | Public | Info API, S3 requester-pays |
| GeckoTerminal | DEX pool OHLCV | REST | Public (rate limited) | OHLCV endpoint |

---

## 6. Shared Utilities

| Module | Purpose | Key Functions | Call Sites |
|---|---|---|---|
| `symbol_aliases.py` | 100+ entry symbol resolution | `resolve_symbol()`, `symbols_match()`, `same_asset()`, `quote_mismatch()` | `cross_asset_impulse.py` |
| `artifact_metadata.py` | Metadata injection | `build_metadata()`, `inject_metadata_into_manifest()`, `check_schema_version()` | `latency_diagnostics.py`, `validate_capture.py`, multiple run scripts |
| `burn.py` | Research burn file (JSONL) | `burn_corpus()`, `read_burned()`, `is_burned()` | Standalone |
| `quarantine.py` | Research quarantine (JSONL) | `quarantine_run()`, `read_quarantine()`, `is_quarantined()` | Standalone |
| `gpu_devices.py` | Multi-GPU configuration | `parse_cuda_devices()`, `validate_cuda_devices()`, `split_work_evenly()`, `derive_per_device_seeds()` | All GPU modules |
| `mcpt_export.py` | MCPT candidate export | `is_mcpt_worthy_group()`, `select_mcpt_candidate_groups()`, `export_mcpt_candidate_series()` | `permutation_null.py` |

---

## 7. Persistence & Schema

### Data Files

| Format | Location | Schema |
|---|---|---|
| JSONL (trades) | `data/*/trades_{venue}_{symbol}_{timestamp}.jsonl` | `TradeTickLite` fields per line |
| JSONL (quotes) | `data/*/quotes_{venue}_{symbol}_{timestamp}.jsonl` | `QuoteTickLite` fields per line |
| CSV (OHLCV) | `data/*/*.csv` | timestamp, open, high, low, close, volume (venue-specific) |
| JSONL (signals) | `reports/*/signals_*.jsonl` | `SignalEvent` fields per line |
| JSONL (forward returns) | `reports/*/forward_returns_*.jsonl` | `ForwardReturnResult`/`TickForwardReturn` per line |
| JSON (summary) | `reports/*/summary.json` | `SignalEvaluationSummary` |
| JSONL (burn) | `reports/research_run_burn.jsonl` | `{run_id, signal_family, reason, precommitment, burned_at, ...}` |
| JSONL (quarantine) | `reports/research_run_quarantine.jsonl` | `{run_id, reason, quarantined_at}` |
| JSONL (evidence ledger) | `reports/governance_ledger.jsonl` | `LedgerEvent` with event_hash integrity |
| JSON (grid spec) | `discovery/grid_spec.json` | `DiscoveryGridSpec` frozen dataclass |
| JSON (grid lock) | `discovery/grid_lock.json` | `DiscoveryGridLock` frozen dataclass |
| JSON (candidate lock) | `discovery/candidate_lock.json` | `DiscoveryCandidateLock` frozen dataclass |
| JSONL (governance) | `reports/evidence_ledger.jsonl` | Append-only governance events |
| JSON (Stage 2 manifest) | `reports/stage2_*/*.json` | Split/FDR/check results |
| Parquet (HL L2 book) | `data/hyperliquid/` | Flat 20-level bid/ask schema |
| JSONL (HL funding) | `data/hyperliquid_funding_archive_phase0/` | `FundingRow` per line |
| CSV (Binance Vision) | `data/funding_dispersion_carry_v1_archives/` | Monthly zip archives |
| JSON (manifest) | `data/*/capture_manifest.json` | Capture metadata + stream references |

### State Management

- **Append-only**: Burn, quarantine, evidence ledger, governance events — all append-only JSONL, never mutated in-place
- **SHA-256 hashed**: Precommitments, grid specs, candidate locks, capture manifests, collection locks — deterministic content hashing with canonical JSON serialization
- **Frozen dataclasses**: All configs, data models, and specification types use frozen dataclasses
- **No database**: Pure filesystem-based persistence; no SQL, no ORM, no key-value store

---

## 8. Frontend Architecture

No frontend is present. This is a Python CLI research tool.

---

## 9. Background Jobs & Scheduled Tasks

| Job | System | Trigger | What It Does |
|---|---|---|---|
| Stage 2 Gate Watcher | systemd service | Continuous polling (2s interval) | Polls Binance/USDT ticker for stress trigger (30 bps in 30s) → subprocess capture |

---

## 10. Middleware & Cross-Cutting Concerns

- **No HTTP middleware** — no Express/Koa-style middleware stack
- **Safety assertions**: AST-level safety scan (`discovery/safety_scan.py`) blocks forbidden imports, order terms, env access in discovery package
- **Forbidden verdict guards**: Multiple modules enforce frozenset `FORBIDDEN_VERDICTS` that prevent `TRADE_READY`, `EXECUTION_READY`, `LIVE_READY` from appearing in validator/miner/falsification outputs
- **Precommitment hash verification**: SHA-256 hash lock on precommitment files at collection start; all later stages verify hashes
- **Quarantine/burn convention**: Run artifacts cannot be used once quarantined or burned

---

## 11. Type System & Contracts

### Core Types (Models Layer)

| Type | Purpose | Layers Used |
|---|---|---|
| `SignalEvent` | Bar-level signal | 2-4 |
| `TickSignalEvent` | Tick-level signal (nanosecond) | 2-4, 6 |
| `ForwardReturnResult` | OHLCV forward return | 3-4 |
| `TickForwardReturn` | Tick forward return | 3-4 |
| `DerivativeTradeTick` | Derivative trade tick | 1, 5 |
| `DerivativeImpulseEvent` | Derivative impulse | 2 |
| `DexPoolSnapshot` | DEX pool state | 1, 5 |
| `DexDislocationEvent` | DEX dislocation | 2 |
| `GridCell` | Miner sweep cell | 9 |
| `DiscoveryGridSpec` | Grid specification | 9 |
| `DiscoveryGridLock` | Frozen grid | 9 |
| `DiscoveryCandidateLock` | Frozen candidate | 9 |
| `LedgerEvent` | Governance event | 9 |
| `CandidateState` | Replayed state | 9 |
| `GateResult` | Authorization result | 9 |

### Key Adapter Functions (Layer 6 → Layer 2)

| Adapter | Source Type | Target Type | Location |
|---|---|---|---|
| `impulse_to_tick_signal()` | `DerivativeImpulseEvent` | `TickSignalEvent` | `derivatives_lead_lag.py` |
| `dex_event_to_tick_signal()` | `DexDislocationEvent` | `TickSignalEvent` | `dex_cex_dislocation.py` |

---

## 12. Configuration & Environment

### Frozen Config Dataclasses

| Class | Location | Key Fields |
|---|---|---|
| `ObserverConfig` | `config.py` | horizons, fee_model, signal_source, bars_csv_path, output_dir, synthetic |
| `LeadLagConfig` | `config.py` | source_venue/instrument, target_venue/instrument, lookback_windows, move_thresholds, cooldown, grid |
| `SignalSourceConfig` | `config.py` | CSV path, cross-market venue/instrument/threshold/lookback/cooldown |
| `FeeModel` | `config.py` | fee_bps=5, slippage_bps=1, quote_mismatch_buffer_bps=0 |
| `TickLeadLagConfig` | `event_study.py` | lookbacks_ms, thresholds_bps, cooldown_ms, min_events |
| `TradeFlowImpulseConfig` | `trade_flow_impulse.py` | 4 signal type configs (count/notional burst multipliers, large trade threshold, imbalance threshold) |
| `FillModelConfig` | `shadow/fill_model.py` | entry_delay, maker_probability, fees, spread, queue depth, slippage std |
| `DiscoveryGridSpec` | `discovery/search_space.py` | 10 primary axes, canonical hashing |
| `GridCell` | `miner/grid.py` | cell_id, source/target venue+instrument, signal_type, horizon, entry_delay |

### Environment Variables

No `.env.example` found. The AGENTS.md mentions API keys should never be committed, but no structured environment variable documentation exists beyond inline comments.

All secrets/tokens: **[REDACTED]**

---

## 13. CI/CD Pipeline

No CI/CD pipeline is present. The project is a research scaffold within the NautilusTrader repository — no `.github/workflows/` directory exists specifically for this scaffold.

---

## 14. Security Posture

| Concern | Status |
|---|---|
| **Live trading reachability** | Hard-blocked. No live-execution imports in any research module. AST-level safety scan in discovery package. |
| **Secrets** | No secrets committed. API keys handled via environment vars (undocumented location). |
| **Private endpoints** | All data sources use public REST/WebSocket endpoints. No OAuth, no API keys on any data path. |
| **Subprocess safety** | `stage2_gate_watcher.py` uses subprocess with hardcoded path `[\"git\", \"rev-parse\", \"HEAD\"]` in discovery package. |
| **Safety assertions in tests** | Multiple tests enforce no-order, no-private-key, observer-only modes. |
| **Forbidden verdicts** | Frozenset guards prevent `TRADE_READY`, `EXECUTION_READY`, `LIVE_READY` in all diagnostic modules. |
| **Input validation** | Frozen dataclasses validate on construction. `validate_grid_spec` does full schema validation. |

---

## 15. Known Patterns & Conventions

- **snake_case** throughout
- **Frozen dataclasses** for all data contracts
- **Append-only JSONL** for state
- **SHA-256 hashing** for content integrity
- **Safe observer-only discipline** — no execution code reachable from research paths
- **Hard-fail over silent degradation** — `validate_grid_spec` raises errors, default direction is not assumed
- **GPU/CPU parity** tests where GPU acceleration exists
- **Multiprocessing workers** for independent per-symbol/file work
- **Heartbeat logging** for long-running jobs
- **Forbidden verdict frozensets** in diagnostic modules

---

## 16. Dependency Map

| Dependency | Purpose | Usage |
|---|---|---|
| `nautilus_trader` | Core trading engine (not actually importable on this machine) | Data models, indicators |
| `numpy` | Numerical computation | Vectorized features, rolling windows |
| `pandas` | Data manipulation | DataFrame operations, quantiles |
| `torch` (optional) | GPU acceleration | CUDA-accelerated evaluation/null/heatmap |
| `httpx` (optional) | Async HTTP client | DEX Screener, general REST |
| `urllib` | HTTP requests | Hyperliquid funding backfill |
| `pyarrow` (optional) | Parquet I/O | HL archive reading |
| `lz4` (optional) | LZ4 decompression | HL archive decompression |
| `pytest` | Testing framework | All test files |

---

## 17. Edge Cases & Operational Notes

- **GLIBC 2.36 vs 2.39**: NautilusTrader Rust extensions require GLIBC 2.39. This machine has 2.36. All Nautilus-dependent code must be committed and run elsewhere.
- **NFS filesystem**: Repository lives on Synology NAS NFS mount. `find` without `-maxdepth` can hang. File operations may be slower than local.
- **JSONL parsing**: Archive files use `orjson` when available (stdlib `json` fallback). Opening in binary mode preferred for `orjson`.
- **DEX Screener API**: Search endpoint returns stale/cached prices during quiet market hours. Real prices require direct pair-lookup endpoint.
- **Coinbase legacy API**: Caps at ~300 candles. Advanced Trade API requires authentication.
- **Kraken symbol naming**: `XBT` vs `BTC` aliasing handled in `symbol_aliases.py` and multiple modules.
- **Binance Vision archive**: Spot kline timestamps changed from milliseconds (pre-2025) to microseconds (2025+). Parser auto-detects.
- **Polymarket API**: CLOB orderbook returns 404 for inactive markets. `TTE` computation and `distance_to_strike` bucketing verified.
- **Hyperliquid S3**: Requester-pays. Cost estimated before download. Confirmation token required.
- **Stage 2 gate watcher**: Systemd service with `flock`-based concurrency lock. Default 3600s minimum capture gap.

---

## 18. Testing Posture

### Test Count: 74 test files

| Category | Count | Notes |
|---|---|---|
| Core pipeline | ~15 | `test_all.py`, `test_lead_lag_pipeline.py`, `test_tick_lead_lag_pipeline.py`, etc. |
| GPU | ~5 | `test_forward_returns_gpu.py`, `test_permutation_null_gpu.py`, `test_signal_gpu.py`, etc. |
| Discovery | ~10 | `test_discovery_candidate_lock.py`, `test_discovery_grid_lock.py`, `test_discovery_safety_scan.py`, etc. |
| Governance | ~2 | `test_governance_phase2.py` |
| Miner | ~2 | `test_miner_phase3.py` |
| Shadow | ~2 | `test_shadow_phase5.py` |
| Validator | ~2 | `test_validator_phase1.py` |
| Bot | ~2 | `test_bot_phase6.py` |
| Hyperliquid | ~10 | `test_hyperliquid_funding_*.py`, `test_hyperliquid_asset_ctxs_*.py`, etc. |
| Capture | ~5 | `test_bitfinex_capture.py`, `test_okx_bybit_capture.py`, etc. |
| Cross-asset | ~3 | `test_cross_asset_*.py`, `test_cross_venue_lead_lag.py` |
| Other | ~16 | `test_stage2*.py`, `test_candidate_falsification.py`, `test_cost_sensitivity.py`, `test_corpus_phase4.py`, `test_audit_regression.py`, `test_bug_audit_pass2.py`, etc. |

### Can Tests Run Here?
**No.** NautilusTrader requires GLIBC 2.39 for its Rust extensions. This machine has GLIBC 2.36. All Nautilus-dependent tests (most tests in this directory) will fail at import time with GLIBC errors. Python-stdlib-only tests may run but no test file is pure stdlib — they all import `nautilus_trader` or project modules that do.

### Linter Results

**3150 ruff issues** across the project:

| Issue | Count | Effect | Auto-fixable |
|---|---|---|---|
| `Q000` bad-quotes-inline-string | 586 | Style | Yes |
| `D213` multi-line-summary-second-line | 391 | Style | Yes |
| `F401` unused-import | 320 | Dead code | Yes |
| `I001` unsorted-imports | 313 | Style | Yes |
| `TID252` relative-imports | 288 | Style | No |
| `C901` complex-structure | 111 | Maintainability | No |
| `F841` unused-variable | 67 | Dead code | No |
| `S108` hardcoded-temp-file | 56 | Security | No |
| `S311` suspicious-non-cryptographic-random | 49 | Security | No |
| `E402` import-not-at-top-of-file | 29 | Style | No |
| `F821` undefined-name | 11 | Bug risk | No |
| `S301` suspicious-pickle-usage | 1 | Security | No |

**2027 issues are auto-fixable** (64% of total). 539 hidden with `--unsafe-fixes`.

---

## 19. Top Priorities / Recommendations

### P0: Blocks Trusting Verdicts or Registry Updates

1. **Broken imports in discovery CLI scripts** — `run_lock_discovery_candidate.py`, `run_lock_discovery_grid.py`, and `run_validate_discovery_grid_lock.py` all import functions that don't exist (`candidate_lock_file_is_semantically_identical`, `lock_file_is_semantically_identical`, `validate_grid_lock`). These run scripts are completely broken and will fail at runtime. **Fix:** Align imports with actual function names in `grid_lock.py` and `candidate_lock.py`.

2. **`fetch_dexscreener_pairs_by_address` passes address as chain** — `dex_adapters.py` line 162 calls `fetch_dexscreener_pair(addr, addr, ...)` where the first `addr` should be a chain name. All DEX pair lookups via this function are broken. **Fix:** Accept chain as separate parameter.

### P1: Blocks Promotion to Governance/Bot Boundary

3. **Hardcoded hashes in Phase 0B/0C** — `liquidation_flush_aftershock_reversal_venue_age_aware_phase0b.py` and `phase0c.py` contain hardcoded `EXPECTED_PHASE0A_EVENT_HASH` and `EXPECTED_PHASE0C_PRECOMMITMENT_HASH`. These will break the moment Phase 0A regenerates its event file. **Fix:** Load expected hashes from Phase 0A report artifacts instead of hardcoding.

4. **Hardcoded direction "long" in generic altcoin stress detector** — `generic_altcoin_stress_regime_ablation_phase0a.py` line 726 hardcodes `direction="long"` for what is clearly a downside/crash detector (return <= -300 bps). This contaminates direction-based metrics. **Fix:** Set direction to `"short"` or compute dynamically.

5. **Duplicate utility functions across modules** — `_percentile` (observer.py, latency_diagnostics.py), `align_venues` (data_adapters.py, csv_normalizer.py — different signatures), `_is_finite` (cost_sensitivity.py, cross_capture_consistency.py), `check_cuda_available` (4 GPU modules). Consolidate into shared helpers.

### P2: Research Correctness Hardening

6. **Dead code** — `lead_lag.py` is superseded by `event_study.py`, `data_fetcher.py` is superseded by `data_adapters.py`. `TradeFlowImpulseSignalGenerator._window_indices` and `_rolling_baselines` (dead methods). Remove or mark deprecated.

7. **`is_expired()` uses string comparison** — `bot/manifest.py:is_expired()` compares ISO-8601 datetime strings lexicographically. Works for consistent format but fragile across timezone representations. **Fix:** Parse to `datetime` objects.

8. **`object.__setattr__` on frozen dataclasses** — `tick_store.py:merge_and_sort_ticks()` bypasses `__setattr__` to stamp `tick_type` on frozen instances. Fragile. **Fix:** Use a wrapper dataclass or `__post_init__` pattern.

### P3: Product Hygiene

9. **Reactive `random.seed()` inside functions** — `data_loading.py` calls `random.seed()` inside `generate_synthetic_lead_lag()` and `generate_synthetic_noise()`, which affects module-level `random` state. **Fix:** Use `random.Random()` instance.

10. **Cross-profile imports broken** — `collect_dex_snapshots.py` imports from `dex_adapters`, `dex_models` which are not in this directory. The correct relative paths need verification.

11. **`PROCEED_TO_V1_EVALUATION` never returned** — `hyperliquid_cost_feasibility.py` defines this constant but the decision logic never returns it. Logic gap.

---

## 20. Bug Taxonomy Audit

| Category | Occurrences | Severity |
|---|---|---|
| Key mismatch (producer/consumer) | 0 | — |
| ISO date string comparison | 1 (`manifest.is_expired`) | Medium |
| Hardcoded thresholds | 2 (direction "long", Phase 0B/0C hashes) | High |
| Dead types | 2 (`lead_lag.py`, `data_fetcher.py`) | Low |
| Direction-blind metrics | 1 (generic altcoin stress "long" label) | High |
| Path inconsistency | 1 (cross-profile imports) | Medium |
| Truncated data loss | 0 | — |
| Order-dependent algorithms | 1 (`rejection_guard._overlaps_locked_rejection` substring match) | Low |
| Unused config fields | 0 | — |
| Verdict normalization drift | 0 | — |

---

## Final Response

**Audit scope:** `/mnt/nasirjones/py/nautilus_trader/examples/strategies/venue_agnostic_signal_observer/`

**Files analyzed:** ~100+ source files + 74 test files + reference documents

**Key findings:**
- Clean 10-layer architecture with strong separation of concerns
- Excellent safety discipline: observer-only, append-only state, forbidden verdict guards, precommitment hashing
- 3 P0 bugs in discovery CLI run scripts (broken imports — scripts will not execute)
- 1 P0 bug in DEX adapter (wrong parameter passed)
- 2 P1 bugs with hardcoded values (Phase 0B/0C hashes, direction string)
- 3150 lint issues (2027 auto-fixable)
- Tests blocked by GLIBC mismatch — cannot run on this machine
- 37 of 49 research study groups REJECTED, 11 NEEDS_MORE_DATA — rigorous falsification discipline

**No orders, private keys, trading auth, live execution, paper trading, shadow execution, systemd watcher, bot path, or unrequested REJECTED_RESEARCH.md update were used during this audit.**
