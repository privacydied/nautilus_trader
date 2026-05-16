# PRODUCT AUDIT — NautilusTrader Strategy Research Scaffold
## Date: 2026-05-16
## Version: v1

---

## 1. Executive Summary

This is a **systematic trading research scaffold** built on top of [NautilusTrader](https://nautilustrader.io) (v1.227.0), a production-grade Rust-native trading engine. The scaffold contains ~71,000 lines of Python across **9 self-contained research sub-projects** under `examples/strategies/`, each exploring a distinct trading hypothesis on crypto markets (Kraken spot, Coinbase spot, Binance perp, Polymarket prediction markets, DEX pools).

The project's primary value proposition is **identifying and rigorously rejecting non-viable trading edges** before any capital is risked. Every hypothesis is tested against realistic fees, spread, slippage, latency, and fill assumptions. The codebase enforces observer-only discipline with AST-level safety checks, explicit guard gates, and strict separation between research and execution code.

**Key finding across all studies: 35 study groups rejected, 9 need more data, 1 diagnostic (moderate market), 1 unknown. Zero surviving tradeable candidates.** The research has systematically closed Kraken spot OHLCV indicators (V1-V4), cross-venue spread scanning (V6), funding/basis (V6-B), altcoin funding (V6-C), L2 maker simulation (V7), same-asset cross-venue tick lead-lag, trade-flow impulse, derivatives-source spot lead-lag, DEX-CEX dislocation, and Polymarket BTC Up/Down liquidity. The only remaining open thread is **cross-asset beta-lag under genuine market stress**, which awaits a volatile market window for testing.

---

## 2. Architecture Overview

### Deployment Model

Self-hosted on a development workstation / Synology NAS NFS mount. Research scripts run locally against real-time public REST/WebSocket APIs. No cloud deployment, no containerization, no production server infrastructure.

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12-3.14 |
| Framework | NautilusTrader 1.227.0 (Rust + Cython extensions) |
| Package manager | `uv` 0.11.8 |
| Build | Poetry 2.3.1 + Cython 3.2.4 + setuptools |
| Data storage | Parquet (Nautilus `ParquetDataCatalog`), JSONL, CSV |
| Data transport | REST (Kraken, Binance, Coinbase, Polymarket, DEX Screener), WebSocket (Kraken v2, Binance FAPI) |
| GPU acceleration | PyTorch CUDA (optional, multi-GPU support) |
| Testing | pytest 7.x, pytest-asyncio |
| Linting | ruff 0.15.12 |
| Type checking | mypy 1.20.2 |
| Async runtime | uvloop 0.22.1 on Linux |
| CI/CD | None discovered (no `.github/workflows/` under the strategies directory; the parent Nautilus repo may have them) |

### Directory Structure

```
examples/strategies/
  indicators/                     # Nautilus custom indicator example (2 files)
    ema_python.py                 #   PyExponentialMovingAverage
  volatility_gate.py              # Kraken OHLCV volatility gate (capture permissions)
  research_report_miner.py        # Walks reports/ directories, mines study results
  test_volatility_gate.py         # Tests for volatility gate helpers
  test_volatility_gate_capture_perm.py  # Tests for capture permission logic
  REJECTED_RESEARCH.md            # Registry of all rejected/frozen studies
  docs/                           # Audit reports
  
  kraken_btcusd_research/         # V1-V4 OHLCV trend strategies (REJECTED)
  kraken_market_structure_scanner/ # V6 cross-venue spread + funding scanners (REJECTED)
  kraken_l2_maker_paper/          # V7 L2 maker-fill simulation (REJECTED)
  pmcpt-main/                     # Monte Carlo Permutation Test framework (external)
  polymarket_btcusd_arb/          # Polymarket BTC Up/Down vs Binance arb (REJECTED)
  polymarket_complement_arb/      # Polymarket YES+NO complement arb (V1 stub)
  venue_agnostic_signal_observer/ # Signal observer framework (active, largest)
```

### Data Flow (General Pattern)

```
Public API (REST/WS)
    ↓
Data Fetching Layer (download_kraken_ohlcv, binance_data, polymarket_data, etc.)
    ↓
Parquet/CSV Cache (ParquetDataCatalog, data_cache)
    ↓
Signal Generation (strategy classes, signal generators, impulse detectors)
    ↓
Forward Return Evaluation (evaluate_signal, evaluate_candidate_group, gates)
    ↓
Report Generation (JSONL, JSON, CSV, markdown summaries)
    ↓
Research Report Mining (research_report_miner.py → REJECTED_RESEARCH.md)
```

### Key Architectural Patterns

1. **Observer-only by default**: Every sub-project enforces no-live-trading discipline. AST-based safety scanners in polymarket_btcusd_arb/safety_checks.py scan for banned imports (execution clients, live nodes, order factory).
2. **Rejection-first research**: Every sub-project has explicit acceptance/rejection gates, precommitment documents, and a central REJECTED_RESEARCH.md registry.
3. **GPU/CPU dual paths**: venue_agnostic_signal_observer has dedicated GPU modules with lazy torch imports and CPU fallback.
4. **Append-only state**: Run index, quarantine, burn — all append-only JSONL. Never mutated in-place.
5. **SHA-256 precommitment locks**: stage2 precommitment pipeline verifies hashes haven't changed during a capture campaign.
6. **No code sharing between sub-projects**: Each sub-project is fully self-contained with its own config, models, and entry points — no shared library layer.
7. **Synthetic data for testing**: Multiple test suites generate synthetic price data for reproducible testing without API access.

---

## 3. Startup & Bootstrap

There is **no single application entry point**. Each sub-project has its own CLI entry points:

- **Root-level utilities**: `volatility_gate.py` is a standalone script (runs as `__main__`)
- **research_report_miner.py**: Standalone CLI with `--reports` argument
- **kraken_btcusd_research**: Multiple `run_*.py` scripts — backtest, live guarded, V4 multi-window research
- **kraken_market_structure_scanner**: 3 entry points for V6 base, V6-B funding, V6-C alt funding
- **kraken_l2_maker_paper**: `run_v7_l2_maker_paper.py` asyncio entry point
- **polymarket_btcusd_arb**: `live_public_observer.py`, `run_duration_spread_probe.py`, `observer_evidence_review.py`
- **polymarket_complement_arb**: `run_observe.py`, `run_backtest.py`, `run_live_guarded.py` (stubbed)
- **venue_agnostic_signal_observer**: `__main__.py` → `run_lead_lag.py`, plus 18 other `run_*.py` scripts

The AGENTS.md file at `examples/strategies/` establishes project conventions:
- Use `uv` for reproducible Python environment
- Rust toolchain required for NautilusTrader's Rust extensions
- Default posture: no live trading, no private-key assumptions, no exchange-account assumptions
- Every trading hypothesis must be tested against realistic fees, spread, slippage, latency, and fill assumptions

There is a **systemd service** for the stage2 gate watcher at `venue_agnostic_signal_observer/systemd/nautilus-stage2-gate-watcher.service`.

---

## 4. Core Flows (Project-Specific)

### 4.1 Volatility Gate (`volatility_gate.py`)

**Purpose**: Gatekeeper for capture decisions. Checks recent Kraken OHLCV volatility and issues capture permissions.

**Flow**:
1. Fetch hourly bars for BTC/USD, ETH/USD, SOL/USD, DOGE/USD from Kraken REST API
2. Compute 3-hour range (bps) and 1-hour range plus acceleration from hourly bars
3. Market classification based on bps thresholds:
   - BTC >= 75 or ETH >= 90 → MARKET_ACTIVE
   - SOL >= 125 or DOGE >= 150 → MARKET_ALT_ACTIVE
   - BTC >= 30 or ETH >= 40 → MARKET_MODERATE
   - Otherwise → MARKET_QUIET
4. Acceleration check: current 1h range / previous 1h range >= 2.0
5. Fast diagnostic using 1-minute bars for 15m/30m/60m ranges
6. Capture permission logic:
   - FULL_ACTIVE_CAPTURE: main gate MARKET_ACTIVE + ACCELERATING
   - FAST_DIAGNOSTIC_CAPTURE_ONLY: main gate not active but fast signals show building activity
   - NO_CAPTURE: neither condition met

**Key thresholds**: BTC 3h >= 75 bps, ETH 3h >= 90 bps, acceleration ratio >= 2.0
**Retry**: 3 attempts with exponential backoff (1s, 2s, 4s)

---

### 4.2 Kraken BTC/USD Research (V1-V4)

**Hypothesis**: BTC/USD trend-following on Kraken spot using EMA/Donchian/ATR indicators.

**Evolution**:
- **V1/V2/V3**: 5-minute bar variants with EMA(20/100) crossover, Donchian(55) breakout, trailing ATR stop
- **V4**: 1-hour bars, EMA(50/200) regime filter, Donchian(100), ATR expansion filter (ATR > median ATR), wider trailing stop (4.0 ATR)

**Bug Fix (V1→V4)**: V1 checked Donchian AFTER indicator update — breakout was impossible to trigger. V4 captures `prior_donch_high` BEFORE `update_raw()`.

**Results (V4 multi-window)**:
| Window | Trades | Win% | Gross PnL | Fees | Net PnL |
|--------|--------|------|-----------|------|---------|
| 2024h1 | 14 | 35.7% | +120.51 | 105.15 | +15.36 |
| 2024h2 | 17 | 52.9% | +48.71 | 113.86 | -65.15 |
| 2025 | 30 | 23.3% | -274.21 | 281.24 | -555.45 |
| 2026 | 9 | 11.1% | -118.95 | 58.41 | -177.36 |
| **Total** | **70** | **30%** | **-$223.94** | **$558.66** | **-$782.60** |

**Verdict**: REJECTED. Gross PnL negative overall so fees aren't the root cause. Fees = 249% of gross loss.

**Files**: strategy.py (V1), strategy_v4.py (V4), config.py, config_v4.py, run_backtest.py, run_v4_research.py, run_live_kraken_guarded.py, download_kraken_ohlcv.py, import_kraken_ohlcv_to_catalog.py, reports.py, debug_v4.py (11 source files + 12 test files)

---

### 4.3 Kraken Market Structure Scanner (V6)

**Three scanners sharing infrastructure**:

**V6 Base — Cross-Venue Spread Scanner**:
- REST polling of Kraken/Binance tickers (2s interval)
- Cross-venue bid/ask spread computed with fee + latency buffers
- Verdict: REJECTED — no net edge after fees

**V6-B — Funding/Basis Cash-and-Carry**:
- Kraken spot + Binance/Bybit perp funding rates
- Computes implied annualized funding APR vs entry/exit costs
- 4 rejection filters: funding rate not positive, USD/USDT mismatch, APR too low (<20%), net edge too low (<25 bps)
- Verdict: REJECTED

**V6-C — Altcoin Funding Anomaly Monitor**:
- Three-tier cost scenarios (conservative/mixed/optimistic)
- Persistence tracking: requires 3 consecutive candidate polls for durability
- 10 altcoins: SOL, XRP, DOGE, LINK, AVAX, ADA, SUI, ARB, OP, APT
- Default 600s duration, 10s poll interval
- Verdict: REJECTED — no durable candidates

**Files**: 16 source files + 3 test files

---

### 4.4 Kraken L2 Maker Paper (V7)

**Hypothesis**: Is there a maker-style microstructure edge at the top of the Kraken L2 book?

**Simulation**:
- Asyncio WebSocket client connecting to `wss://ws.kraken.com/v2` (public book + trade channels)
- `OrderBook` maintains sorted bid/ask levels with snapshot/update application
- `PaperFillModel` with 3 modes: pessimistic (requires book-crossing), neutral, optimistic
- `QuoteEngine` manages up to 4 concurrent quotes with 5 cancellation triggers (max lifetime, stale book, mid move, spread collapse, imbalance flip)
- `MakerPaperSimulator` ties it all together

**Config**: Maker fee 3 bps, fill penalty 2 bps, quote lifetime 5s, cancel on 5 bps mid move

**Verdict**: REJECTED — spread < maker fee + fill penalty. No edge after all costs.

**Files**: 10 source files + 1 test file (552 lines, 36 tests)

---

### 4.5 Polymarket BTC/USD Arbitrage

**Hypothesis**: Exploit divergence between Polymarket BTC Up/Down binary options and Binance spot price.

**Pipeline**:
1. **Data ingestion**: Polymarket trade data via REST API, Binance aggTrades from data.binance.vision
2. **Fair probability**: Black-Scholes binary call formula (sigma=0.70, risk-free=0.05)
3. **Signal generation**: Grid over (threshold, lookback, forward horizon, TTE bucket) — checks spread, staleness, edge threshold
4. **Gate evaluation**: 7 gates per grid cell (event_count, mean_edge_positive, median_edge, win_rate, beats_baseline, not_dominated, maker_fee_survival)
5. **Forward returns**: Measures what happened after signal at configurable horizons
6. **Evidence review**: Phase 2C gate decision engine aggregates 10 valid live windows

**Config**: max_binance_staleness 2s, max_spread 200 bps, latency_buffer 2 bps, threshold_grid (5, 10, 20, 40 bps)

**Results**: 10 valid live windows, 4 distinct markets, ZERO candidates across all windows. Dominant failure: spread_too_wide (72-93%). Secondary: stale_or_missing_binance (7-28%).

**Verdict**: REJECTED_FOR_CURRENT_LIVE_CONDITIONS. Hypothesis not globally disproven but unworkable under observed conditions.

**Duration probe correction**: Prior conclusion that 1h/4h BTC UpDown markets do not exist was SUPERSEDED — they exist (1h: Binance ref, 4h: Chainlink ref). Product existence separate from active market availability.

**Files**: 21 source files + FINAL_RESEARCH_STATUS.md

---

### 4.6 Polymarket Complement Arbitrage (YES+NO)

**Hypothesis**: Same-condition binary YES+NO pairs occasionally sum to < $1, creating a risk-free harvest.

**Architecture**:
- `market_filter.py` → extracts eligible pairs from Gamma API
- `detector.py` → evaluates one complementary pair's book state
- `edge_model.py` → core cost/edge math (Polymarket's concave fee formula: C * feeRate * p * (1-p))
- `sizing.py` → max safe share quantity with exposure constraints
- `state_machine.py` → per-condition lifecycle (13 states from IDLE to SETTLED_UNPAIRED)
- `strategy.py` → Nautilus Strategy subclass (all event handlers are pass-through stubs)
- `passive_fill_estimator.py` → records would-be maker quotes
- `backtest_harness.py` → loads historical trades, runs detection loop
- `ledger.py` → append-only JSONL writer

**V1 Status**: Run_live_guarded.py is STUBBED. No live execution. ComplementArbStrategy has all event handlers as `pass`. This is Phase 7 pending.

**Config**: max_order_usdc=100, min_net_edge_per_share=$0.005, leg_risk_buffer=$0.002, one_leg_timeout=30s, resolution_danger_window=1hr

**Files**: 15 source files + 10 test files + POLYMARKET_COMPLEMENT_ARB_PRECOMMITMENT.md + README.md

---

### 4.7 Venue-Agnostic Signal Observer (Largest Project)

**Purpose**: The signal research framework. ~50+ Python source files, 37 test files, organized in a clean 10-layer architecture.

**The 10 Layers**:

```
1.  MODELS          — Frozen dataclasses (models.py, tick_models.py, dex_models.py, derivatives_models.py)
2.  SIGNALS         — Signal generators (lead_lag.py, event_study.py, trade_flow_impulse.py, etc.)
3.  FORWARD RETURNS — evaluate_signal(), batch_evaluate_signals_gpu()
4.  OBSERVER        — SignalObserver.run() orchestrates signals → returns → summary
5.  DATA ADAPTERS   — load_venue_csv(), fetch_binance_klines(), etc.
6.  DIAGNOSTICS     — lead_lag_heatmap_gpu.py, cost_sensitivity, latency_diagnostics
7.  FALSIFICATION   — permutation_null.py, mcpt_export.py
8.  STAGE2 PIPELINE — precommitment → criteria → FDR → split_corpus → gate_watcher
9.  GPU ACCEL       — 4 dedicated GPU modules (forward_returns_gpu, lead_lag_heatmap_gpu, etc.)
10. REPORTS/CLI     — reports.py, 18 run_*.py entry points
```

**Tick vs Bar Separation**: Two independent tiers with their own models, signal types, and evaluators:
- Bar tier: `SignalEvent`, `ForwardReturnResult` (float timestamps)
- Tick tier: `TickSignalEvent`, `TickForwardReturn` (integer nanosecond timestamps)

**Stage 2 Pipeline** (the most sophisticated component):
```
precommitment.json (SHA-256 locked)
    ↓ readiness_check (imports, git state, gateway)
    ↓ gate_watcher (volatility gate polling with ConcurrencyLock, CaptureGuard)
    ↓ split_corpus (70/30 temporal split, min 10 captures)
    ↓ FDR (BH q=0.10, BY q=0.10 on native permutation p-values)
    ↓ check_criteria (discovery: +2 bps, 70% same-sign, -5 bps worst floor)
    ↓ auto-burn if empty
```

**Signal families studied**:
- OHLCV lead-lag (v1): REJECTED
- Tick lead-lag (v2, v3): REJECTED
- Trade-flow impulse (v1, 300s/600s): REJECTED
- Derivatives lead-lag (v1 spot-spot smoke, v2 perp→spot): REJECTED
- DEX-CEX dislocation (v1): NEEDS_MORE_DATA
- Cross-asset spot impulse (v1): MARKET_MODERATE_DIAGNOSTIC (open for volatile retest)
- Polymarket BTC Up/Down liquidity probe (v0): REJECTED

**GPU modules**: 4 dedicated modules with multi-GPU support, lazy torch imports, CPU fallback, chunked processing:
- `forward_returns_gpu.py`: torch.searchsorted for batch entry/forward price lookups (chunk_size 16,384)
- `lead_lag_heatmap_gpu.py`: GPU correlation batch across lags (diagnostic-only verdicts)
- `permutation_null_gpu.py`: circular time shift on GPU (chunk_size 512)
- `trade_flow_impulse_gpu.py`: prefix sum kernels for notional_burst, large_trade, signed_imbalance

**Config parameters** (observer-wide):
- Fee model: 40 bps fee, 5 bps slippage, 5 bps quote mismatch buffer
- Horizons: 1000, 2000, 5000, 10000, 30000, 60000, 300000 ms
- Lookbacks: 1000, 5000, 10000, 30000 ms
- Signal cooldown: 10,000 ms
- Capture spacing: 3,600 seconds

---

### 4.8 MCPT Framework (mcpt-main/)

**Purpose**: Monte Carlo Permutation Test framework for evaluating statistical significance of trading strategies.

**Methodology**:
1. Convert OHLC to log prices
2. Permute intra-bar components (high-open, low-open, close-open) as a group
3. Permute gap components (open - prev_close) independently
4. Reassemble permuted bars (preserves mean, std, skew, kurtosis, cross-correlation)
5. Run strategy on original and N permuted series
6. p-value = (count_better_or_equal + 1) / N

**Strategies tested**: Donchian breakout, MA crossover, Decision Tree (intentionally overfit joke)

**Two variants**: In-sample (optimize on full set, 1000 permutations) and walkforward (4yr train, 30d retrain, 200 permutations).

**License**: MIT (external project by neurotrader888)

**Files**: 7 source files + LICENSE

---

## 5. Platform/Service Inventory

| Platform | Transport | Auth | Data Contract | Status |
|----------|-----------|------|---------------|--------|
| Kraken REST | HTTPS REST | None (public) | OHLC, Ticker | All sub-projects |
| Kraken WebSocket v2 | WSS | None (public) | Book (depth=10), Trade | V7 L2 maker |
| Binance REST | HTTPS REST | None (public) | bookTicker, aggTrades, klines | Polymarket, V6, observer |
| Binance FAPI WebSocket | WSS | None (public) | Perp tick, funding, OI | Derivatives capture |
| Coinbase REST | HTTPS REST | None (public) | Product ticker | Observer |
| Polymarket Gamma API | HTTPS REST | None (public) | Market metadata, CLOB snapshots | Polymarket arb |
| Polymarket CLOB API | HTTPS REST | None (public) | Order book, trades | Polymarket arb |
| DEX Screener REST | HTTPS REST | None (public) | Pool search, pair details | DEX-CEX dislocation |
| GeckoTerminal REST | HTTPS REST | None (public) | Pool OHLCV | DEX-CEX dislocation |
| Bybit REST | HTTPS REST | None (public) | Perp ticker, funding history | V6-C funding |

**Notable**: No API keys required for any public data source. All authentication-required code paths are behind explicit guard gates (`--live` flag, env var checks, `check_guards()`).

---

## 6. Shared Utilities

### Root-Level

| Module | Purpose | Call Sites |
|--------|---------|------------|
| `volatility_gate.py` | Kraken OHLCV volatility gate | Used by stage2 gate watcher, referenced in cron jobs |
| `research_report_miner.py` | Walks reports/ dirs, produces unified status table | Standalone CLI |

### Per-Project Utilities (not shared)

Each sub-project has its own isolated utility modules. No shared utility library exists.

---

## 7. Persistence & Schema

### Data Storage Formats

| Format | Usage | Reading Pattern |
|--------|-------|-----------------|
| Parquet | Nautilus `ParquetDataCatalog` — primary storage for backtest data | `catalog.bars()` with BarType filter |
| JSONL | Append-only event logs: observations, opportunities, ledgers, rejects | Line-by-line parsing |
| JSON | Structured reports: summaries, gates, verdicts, artifact metadata | `json.loads()` |
| CSV | Intermediate data dumps, trade lists, equity curves | `pd.read_csv()` |
| Pickle | Backtest result serialization (`result.pkl`) | fragile across versions |

### Directory Layout

```
examples/strategies/reports/
  v6_market_structure/          # V6 scanners
  v7_l2_maker_paper/            # V7 L2 maker
  polymarket_complement_arb/    # Complement arb run summaries
  venue_agnostic_signal_observer/ # Observer reports (per capture)

data/ (at repository root)
  catalog/                      # ParquetDataCatalog directories
    kraken_btcusd_1m/
    kraken_btcusd_15m/
  polymarket_btcusd_arb/        # Polymarket cache + live observer data
```

### Schema Notes

- Nautilus `ParquetDataCatalog` schema is managed by NautilusTrader internals
- JSONL schemas vary per project — no shared schema definitions
- `run_index.jsonl`: append-only run index with per-row metadata
- `quarantine.jsonl`, `burn.jsonl`: append-only capture management
- Result.pkl (pickle): no schema enforcement, fragile across Nautilus version upgrades

---

## 8. Frontend Architecture

**Not applicable** — this is a pure research/CLI codebase with no web frontend. All output is file-based (JSONL, JSON, CSV, markdown reports).

---

## 9. Background Jobs & Scheduled Tasks

| Job | Schedule | Mechanism | Description |
|-----|----------|-----------|-------------|
| Stage2 Gate Watcher | Every 30s | systemd service (user-level) | Polls volatility gate, runs captures when gate opens |
| Polymarket Duration Probe | One-shot | `run_duration_spread_probe.py` | Duration discovery + CLOB book polling |
| Observer Campaigns | On-demand | `live_public_observer.py` | 900s observer runs |

---

## 10. Middleware & Cross-Cutting Concerns

| Concern | Implementation | Scope |
|---------|---------------|-------|
| AST-level safety scan | `safety_checks.py` — scans for banned imports, env vars, and function calls | Polymarket BTC arb |
| Live trading guard | `validate_live_guard()` — checks `--live` flag, 5 env vars | Kraken BTC arb, Complement arb |
| Precommitment lock | SHA-256 hash on precommitment JSON files | Stage 2 pipeline |
| Retry logic | 3 attempts with exponential backoff (1s, 2s, 4s) | volatility_gate.py only |
| Rate limiting | 1s delay between DEX Screener calls | dex_adapters.py |
| WS reconnect | Exponential backoff, configurable max retries | KrakenBookWS |

**Notable gap**: Only `volatility_gate.py` has retry/backoff. All other REST clients are single-attempt with no retry logic.

---

## 11. Type System & Contracts

### Project-wide Types

No shared type library exists. Each sub-project defines its own `@dataclass(frozen=True)` models.

### Common Patterns

- **Frozen dataclasses** for all data contracts (immutable, hashable)
- **Nanosecond integer timestamps** (tick_models.py) vs float seconds (models.py)
- **`Optional[Type]` return values** for fetch functions that may fail
- **`@dataclass(frozen=True)` Config classes** with all parameters in one place

### Key Type Contracts (venue_agnostic_signal_observer)

```
SignalEvent → ForwardReturnResult → HorizonSummary → SignalEvaluationSummary
TickSignalEvent → TickForwardReturn → CandidateGroupResult
DerivativeImpulseEvent → impulse_to_tick_signal() → TickSignalEvent
DexDislocationEvent → dex_event_to_tick_signal() → TickSignalEvent
```

---

## 12. Configuration & Environment

### Environment Variables

| Variable | Required By | Purpose |
|----------|-------------|---------|
| `KRAKEN_API_KEY` | `run_live_kraken_guarded.py` | Kraken API key (live mode only) |
| `KRAKEN_API_SECRET` | `run_live_kraken_guarded.py` | Kraken API secret (live mode only) |
| `POLYMARKET_PK` | `run_live_guarded.py` | Polymarket private key (stubbed V1) |
| `POLYMARKET_API_KEY` | `run_live_guarded.py` | Polymarket API key (stubbed V1) |
| `POLYMARKET_API_SECRET` | `run_live_guarded.py` | Polymarket API secret (stubbed V1) |
| `POLYMARKET_PASSPHRASE` | `run_live_guarded.py` | Polymarket passphrase (stubbed V1) |
| `POLYMARKET_FUNDER` | `run_live_guarded.py` | Polymarket funder address (stubbed V1) |

All secrets are [REDACTED] in this report. All environment variables are behind explicit safety gates and never committed to source.

### Python Version

`requires-python = ">=3.12,<3.15"` (from parent pyproject.toml)

### UV Version

`required-version = "==0.11.8"`

---

## 13. CI/CD Pipeline

No CI/CD configuration discovered under `examples/strategies/`. The parent NautilusTrader repository has no `.github/workflows/` visible in this audit's scope.

---

## 14. Security Posture

| Concern | Status |
|---------|--------|
| API keys/secrets committed | No — all behind env vars with explicit guard checks |
| Hardcoded credentials | None found — AST scanner confirms |
| Order placement without guards | Impossible — execution clients behind separate import paths and `--live` flags |
| Prompt injection | Not applicable — no LLM integration in this codebase |
| AST safety scanner | Implemented in polymarket_btcusd_arb/safety_checks.py |
| Test security checks | test_security_no_secrets.py in complement_arb, TestNoLiveTradingCode in l2_maker |

---

## 15. Known Patterns & Conventions

1. **Frozen dataclasses for all models** — immutable, hashable, __slots__-compatible
2. **Observer-only default** — all sub-projects default to observe/backtest mode, never live execution
3. **Precommitment before capture** — SHA-256 locked JSON files declare acceptance gates before empirical testing
4. **No p-hacking rule** — thresholds cannot be tuned after first empirical run without recording a new precommitment version
5. **Rejection registry** — central REJECTED_RESEARCH.md and per-project FINAL_RESEARCH_STATUS.md
6. **Append-only state** — JSONL for all run indices, quarantines, burns
7. **Synthetic data for testing** — each project has helpers to generate reproducible test data
8. **Co-located documentation** — precommitments, research logs, and architecture notes live alongside source code
9. **Sub-project isolation** — no cross-project imports, each is independently runnable
10. **Convention for config**: single frozen dataclass in `config.py` per project
11. **Convention for entry points**: `run_*.py` scripts with argparse
12. **Convention for tests**: per-project `tests/` directory with `test_*.py`

---

## 16. Dependency Map

### Core NautilusTrader (v1.227.0)

- `nautilus_trader.backtest.engine` — BacktestEngine, BacktestEngineConfig
- `nautilus_trader.trading.strategy` — Strategy base class
- `nautilus_trader.model.*` — Instruments, data, identifiers, orders, enums
- `nautilus_trader.indicators` — EMA, DonchianChannel, AverageTrueRange
- `nautilus_trader.persistence.catalog.parquet` — ParquetDataCatalog
- `nautilus_trader.live.*` — LiveNode, TradingNode (guarded behind safety checks)

### External

| Package | Version | Used By |
|---------|---------|---------|
| numpy | >=1.26.4 | Data processing, price math |
| pandas | >=2.3.3 | CSV/Parquet I/O, dataframes |
| pyarrow | >=23.0.1 | Parquet catalog |
| aiohttp | 3.13.5 | WebSocket client (kraken_ws.py) |
| httpx | stdlib | REST calls (dex_adapters.py) |
| torch | (optional) | GPU acceleration (venue_agnostic_signal_observer) |
| pytest | 7.x | Test runner |
| ruff | 0.15.12 | Linter |
| mypy | 1.20.2 | Type checker |

---

## 17. Edge Cases & Operational Notes

### NFS/Network

- The project lives on a Synology NAS NFS mount
- `find` commands without `-maxdepth` can hang on mount boundaries
- EPERM/permission errors on build scripts — user runs build themselves

### NautilusTrader-Specific

- Rust extensions must be compiled via `uv sync --all-extras` (requires Rust toolchain)
- `ParquetDataCatalog` path handling is import-order-sensitive (known issue in `import_kraken_ohlcv_to_catalog.py`)
- Result pickle is fragile across Nautilus version upgrades
- `uv` version mismatch (0.11.8 pinned vs compatible runtime)

### Known Issues (from REJECTED_RESEARCH.md)

- No retry logic in REST fetchers (except volatility_gate.py)
- Pickle coupling in btcusd_research/reports.py
- Baseline window of 60s too large for 300-600s captures (9 NEEDS_MORE_DATA)
- Coinbase fetcher in V6 base scanner is declared in config but NOT implemented in venues.py
- `import_kraken_ohlcv_to_catalog.py` references `ParquetDataCatalog` without importing it (broken import path)
- `run_backtest.py` uses `backtest_result_path` before assignment (variable use-before-assignment)
- Same research_report_miner.py exists in both the root and venue_agnostic_signal_observer directories (duplicate)
- Inconsistent logging: some modules use stdout, some stderr, some Nautilus logging, some Python logger

---

## 18. Testing Posture

### Test Runner

pytest 7.x with pytest-asyncio (asyncio_mode=strict, session-scoped fixtures). Test paths configured for `tests/` at the repository root, but individual sub-projects have their own `tests/` directories.

### Test Coverage (by project)

| Project | Test Files | Tests | Coverage Area |
|---------|-----------|-------|---------------|
| volatility_gate | 2 | ~20 | Range computation, freshness, capture permission logic |
| kraken_btcusd_research | 12 | ~40+ | Import smoke, backtest lifecycle, position sizing, live guards, report generation, V4 config |
| kraken_market_structure_scanner | 3 | 40 | Symbol mapping, spread calculation, funding models, cost scenarios, persistence, alt monitor |
| kraken_l2_maker_paper | 1 | 36 | Book model, quote cancellation, fill model, adverse selection, reports, no-live-trading scan |
| polymarket_complement_arb | 10 | ~40+ | Backtest harness, detector, edge model, ledger, live guards, market filter, passive fill, sizing, state machine, strategy orders, security |
| venue_agnostic_signal_observer | 37 | ~100+ | Lead-lag pipeline, tick pipeline, derivatives, DEX-CEX, trade-flow impulse, cross-asset impulse, permutation null, cost sensitivity, GPU modules, stage2, MCPT, capture, symbols, fixtures |

### Test Quality Observations

- **Strong**: Synthetic data generators in helpers.py (kraken_btcusd) and event_study.py (observer) enable reproducible tests
- **Strong**: Security-focused tests (no secrets, no execution client imports) in complement_arb and l2_maker
- **Good**: Wide coverage of edge cases in L2 book model tests (stale, cross, update, snapshot)
- **Gap**: KrakenBookWS has no tests (requires live WebSocket connection)
- **Gap**: MakerPaperSimulator integration tests (process_book_update) are missing
- **Gap**: `test_backtest_smoke.py` in kraken_btcusd_research is an empty file

---

## 19. Top Priorities / Recommendations

### Critical

1. **Fix broken imports in production paths**: `import_kraken_ohlcv_to_catalog.py` references `ParquetDataCatalog` without importing it. Also `run_backtest.py` uses `backtest_result_path` before assignment. These are structural bugs in existing code.

2. **Replace pickle serialization**: Backtest results stored as `.pkl` are fragile across Nautilus version upgrades. Migrate to Parquet/JSONL with schema-enforced output.

### High

3. **Add retry logic to all REST clients**: Currently only `volatility_gate.py` has retry/backoff. All other REST data fetchers (Polymarket, Binance, DEX Screener, GeckoTerminal) fail on first network error with no recovery.

4. **Implement shared utility library**: The same `research_report_miner.py` exists in two locations. Common patterns (config validation, run artifact management, logging) are duplicated across sub-projects. A shared `strategies/_lib/` module would reduce drift.

5. **Complete Coinbase fetcher**: V6 base `venues.py` declares Coinbase support in config but no fetcher function exists — it silently returns None.

6. **Fill empty test file**: `test_backtest_smoke.py` in kraken_btcusd_research/tests/ is empty.

### Medium

7. **Standardize logging**: Current mix of stdout, stderr, Python logger, and Nautilus logging across projects. Standardize on a project-wide structured logging pattern.

8. **Persist MCPT results**: The MCPT framework (mcpt-main/) is externally sourced and not integrated with the observer pipeline. Export formats and run tracking are manual.

9. **Documentation for stage2 gate watcher**: The systemd service and stage2 pipeline are the most complex parts of the codebase but have no operational runbook (the DERIVATIVES_V2_CAPTURE_RUNBOOK.md covers the OI capture campaign but not the gate watcher lifecycle).

10. **Reduce baseline window**: The 60s baseline window in some evaluations is too large for 300-600s captures, contributing to 9 NEEDS_MORE_DATA verdicts.

### Low

11. **Deduplicate test_all.py files**: Both kraken_btcusd_research and venue_agnostic_signal_observer have `test_all.py` import smoke tests that overlap with per-module tests.

12. **Remove empty directory**: `kraken_v5_portfolio/` has no .py files (just ARCHITECTURE_NOTES.md).

13. **Add .env.example**: No `.env.example` file exists for documenting required environment variables at a glance.

