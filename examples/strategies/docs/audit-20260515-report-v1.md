# PRODUCT AUDIT — NautilusTrader Strategy Examples
## Date: 2026-05-15
## Version: v1

---

## 1. Executive Summary

This directory contains a suite of algorithmic trading research and simulation projects built on the NautilusTrader v1.227.0 framework (LGPLv3, Rust-native, deterministic event-driven architecture). All projects share a single strict constraint: **observer-only. No live orders. No API keys in source. No trading in tests.**

The collection spans the full research lifecycle: data acquisition (Kraken OHLCV download, DEX Screener polling), multiple strategy hypotheses (directional bar-level trend-following, cross-venue spread/funding scanning, L2 market microstructure simulation, tick-level lead-lag detection, DEX-CEX dislocation), backtesting, paper simulation, forward-return evaluation, diagnostic falsification (cost sensitivity, permutation null, cross-capture consistency, candidate falsification), and a rigorous multi-capture Stage 2 pipeline with precommitment locks, temporal train/test splits, and FDR correction.

Seven distinct strategy projects are hosted here, each representing a rejected or in-progress research thread. The rejection tracker at `REJECTED_RESEARCH.md` documents 6 locked gates — avenues structurally closed under current fee/cost assumptions — and several open diagnostic fronts (cross-asset beta lag under stress, OI regime filtering, funding sentiment features).

---

## 2. Architecture Overview

- **Deployment model**: Local / NAS workstation (Arch Linux + Hyprland, 2x RTX 3090s, NFS mount to Synology)
- **Tech stack**: Python 3.14+, NautilusTrader 1.227.0 (Cython-compiled + optional Rust), pandas, numpy, pyarrow, msgspec, torch (for GPU acceleration paths)
- **Framework**: NautilusTrader — production-grade trading engine with backtesting, paper, and live execution modes; event-driven architecture with pluggable adapters, indicators, order management
- **Build system**: Poetry + poetry-core 2.3.1 + Cython 3.2.4; custom build.py script. uv v0.11.8 pinned (but system has v0.11.13 runtime)
- **Testing**: pytest 7.4.4 (also compiled with 9.0.3), ruff linter, no bandit/semgrep security scanner configured

### Directory Structure

```
examples/strategies/
├── volatility_gate.py                    # Kraken OHLCV volatility gate (capture permission)
├── research_report_miner.py              # Unified report mining & status aggregation
├── test_volatility_gate.py               # Volatility gate tests
├── test_volatility_gate_capture_perm.py  # Capture permission tests
├── REJECTED_RESEARCH.md                  # Locked gates registry
├── indicators/
│   ├── __init__.py
│   └── ema_python.py                     # Pure-Python EMA example
├── kraken_btcusd_research/               # V1-V4 BTC/USD bar-level directional
│   ├── config.py / config_v4.py          # Strategy parameters
│   ├── strategy.py / strategy_v4.py      # Strategy implementations
│   ├── download_kraken_ohlcv.py         # OHLCV downloader
│   ├── import_kraken_ohlcv_to_catalog.py # Catalog importer
│   ├── reports.py                        # Backtest report generation
│   ├── run_backtest.py / run_v4_research.py
│   ├── run_live_kraken_guarded.py        # Multi-layer safety live runner
│   ├── debug_v4.py                       # V4 signal debug script
│   └── tests/ (16 test files)
├── kraken_l2_maker_paper/                # V7 L2 maker paper simulator
│   ├── config.py / book_models.py
│   ├── paper_quote.py / paper_fill_model.py
│   ├── kraken_ws.py / simulator.py
│   ├── symbols.py / reports.py
│   └── run_v7_l2_maker_paper.py
├── kraken_market_structure_scanner/      # V6/V6-B/V6-C live scanners
│   ├── config.py / symbols.py / venues.py
│   ├── scanner.py / opportunity.py
│   ├── funding_*.py (8 files)
│   ├── run_v6_scanner.py / run_v6_funding_basis.py / run_v6_alt_funding_monitor.py
│   └── tests/ (3 test files)
├── kraken_v5_portfolio/                  # (Empty directory — no .py files)
├── mcpt-main/                            # Monte Carlo Permutation Test framework
│   ├── bar_permute.py / donchian.py / moving_average.py / tree_strat.py
│   ├── insample_donchian_mcpt.py / insample_tree_mcpt.py
│   └── walkforward_donchian_mcpt.py
├── venue_agnostic_signal_observer/       # Lead-lag & impulse observer (~68 files)
│   ├── config.py / models.py / tick_models.py
│   ├── signals.py / lead_lag.py / event_study.py
│   ├── observer.py / reports.py
│   ├── trade_flow_impulse.py / cross_asset_impulse.py
│   ├── derivatives_lead_lag.py
│   ├── dex_cex_dislocation.py
│   ├── forward_returns.py / forward_returns_gpu.py
│   ├── permutation_null.py / permutation_null_gpu.py
│   ├── cost_sensitivity.py / cross_capture_consistency.py
│   ├── candidate_falsification.py / lead_lag_heatmap_gpu.py
│   ├── latency_diagnostics.py / tick_store.py
│   ├── stage2_*.py (6 files)
│   ├── 20+ run_*.py entry points
│   └── tests/ (25+ test files)
└── docs/
    └── audit-20260515-report-v1.md       # This file
```

---

## 3. Startup & Bootstrap

There is no single application entry point — each project has its own runner. Common pattern:

1. **CLI argument parsing** via argparse (all runner scripts)
2. **Configuration dataclass** construction from args + defaults
3. **Data loading**: CSV (bars), JSONL (ticks), or live REST polling
4. **Computation/Simulation**: signal generation → forward return evaluation → aggregation
5. **Output**: JSONL observations + summary.json + CSV reports

For live data collection:
- `run_v6_scanner.py`: REST polling loop (Kraken + Binance) with configurable interval
- `run_v6_funding_basis.py`: Spot + perp funding polling, same pattern
- `run_v6_alt_funding_monitor.py`: Extended variant with persistence tracking
- `run_v7_l2_maker_paper.py`: WebSocket connection + event loop (async aiohttp)
- `run_derivatives_spot_capture.py`: Real-time tick capture via exchange WebSocket
- `run_stage2_gate_watcher.py`: Daemon that polls volatility gate and triggers FULL_ACTIVE captures

All runners are observer-only — no API keys are stored in source, no orders are placed.

---

## 4. Core Flows

### 4.1 Volatility Gate (`volatility_gate.py`)

**Purpose**: Determine whether market conditions warrant a derivatives v2 capture campaign.

**Flow**:
1. Fetch 10 hourly OHLCV bars for BTC/USD, ETH/USD, SOL/USD, DOGE/USD from Kraken REST (`/0/public/OHLC`)
2. Compute **3-hour range** (high-low in bps) and **1-hour acceleration** (current vs previous 1h range ratio)
3. Gate logic:
   - **MARKET_ACTIVE** if BTC ≥ 75bps or ETH ≥ 90bps in 3h
   - **MARKET_ALT_ACTIVE** if SOL ≥ 125bps or DOGE ≥ 150bps in 3h
   - **MARKET_MODERATE** if BTC ≥ 30bps or ETH ≥ 40bps
   - **ACCELERATING** if BTC or ETH current 1h ≥ 2x previous 1h
4. Fast diagnostic (1-minute bars): 15m/30m/60m ranges + acceleration for BTC/ETH
5. **Capture permission output**:
   - **FULL_ACTIVE_CAPTURE** — main gate MARKET_ACTIVE + ACCELERATING
   - **FAST_DIAGNOSTIC_CAPTURE_ONLY** — ETH fast active + accelerating (≥50bps 15m or ≥60bps 30m + ≥2x accel) OR BTC fast building/active + accelerating + ETH at least BUILDING
   - **NO_CAPTURE** — neither condition
6. Reports candle freshness metadata so repeated checks know when to next re-check

**Key thresholds**:
- BTC: 3h ≥ 75bps (active), 30bps (moderate). Fast: 15m ≥ 30bps, 30m ≥ 40bps, accel ≥ 1.75x
- ETH: 3h ≥ 90bps (active), 40bps (moderate). Fast: 15m ≥ 50bps, 30m ≥ 60bps, accel ≥ 2.0x
- SOL: 125bps, DOGE: 150bps

**Observations**: Pairs dict uses Kraken API symbols (XXBTZUSD, XETHZUSD, SOLUSD, XDGUSD). Stdout output for JSON consumption. Retry: 3 attempts with exponential backoff.

### 4.2 Research Report Miner (`research_report_miner.py`)

**Purpose**: Walk all `reports/` directories, extract study results from JSON/JSONL/CSV/MD files, produce unified status tables.

**Flow**:
1. Recursively globs `reports/` directories for JSON and JSONL files
2. `_process_json_report()`: extracts summary dict, optional `results_by_group` array, baseline results, verdict
3. `_process_jsonl_file()`: handles line-delimited JSON with per-row net bps values
4. Deduplication key: (study, signal_type, symbol, source_venue, target_venue)
5. Outputs: `research_status_summary.json` + `research_status_summary.csv` in the reports directory
6. Project inference from file paths via regex patterns in `_PATH_PROJECT_MAP`

**Verdict normalization**: REJECTED, CANDIDATE, NEEDS_MORE_DATA, UNKNOWN. If not explicitly set, infers from candidate flag, net bps negativity, rejection reasons, or zero signal count.

### 4.3 Kraken BTC/USD Research (V1-V3 Directional)

**Strategy**: EMA crossover + Donchian breakout + ATR risk management on 5-minute bars.

**Entry conditions** (all must pass):
1. Fast EMA(20) > Slow EMA(100) — bullish trend
2. Close breaks above Donchian(55) high — breakout confirmation
3. ATR(20) > 0 (always true after warmup)

**Exit conditions** (any triggers):
1. Close below Slow EMA(100) — trend reversal
2. Stop loss: tighter of initial stop (entry - 2×ATR) or trailing stop (highest high since entry - 1.5×ATR)

**Position sizing**: Risk-based (`_calculate_position_size()`):
- Risk amount = account_value × 0.25%
- Stop distance in BTC terms → position_size = risk_amount / stop_distance
- Notional cap: 30% of account
- Min size: 0.001 BTC, rounded to 8 decimal places
- Fees: taker 0.40% (market orders only)

**Post-trade**: 12-bar cooldown, highest-high tracking for trailing stop

**Architecture**: Custom `KrakenBTCUSDResearchStrategy` extends Nautilus `Strategy`. Uses `on_bar()` handler with careful indicator-update ordering (check before update — standard Donchian pattern). Module-level `create_strategy()` factory. Standalone position tracking (not using Nautilus `Position` object — uses `current_position_size` Quantity + state flags).

**Known issues** (resolved)
- `run_backtest.py` had `backtest_result_path` used before assignment — fixed 2026-05-15
- `import_kraken_ohlcv_to_catalog.py` had missing `ParquetDataCatalog` import — fixed 2026-05-15

### 4.4 Kraken BTC/USD V4 Research (1h Trend-Following)

**Purpose**: Final bar-level BTC/USD spot test. Hypothesis: higher-timeframe moves may be large enough to overcome fee friction.

**Entry conditions** (all 4 required):
1. EMA(50) > EMA(200) — trend filter
2. Close > prior Donchian(100) high (pre-update value) — breakout
3. ATR(20) > 0
4. ATR(20) > rolling median ATR(100) — volatility expansion filter

**Exit conditions** (either):
1. ATR trailing stop: close < highest_high - 4×ATR (much wider than V1's 1.5×)
2. Regime break: close < EMA(100)

**Key differences from V1-V3**:
- 1h bars (vs 5m)
- 4x ATR multiplier in trailing stop (vs 1.5x)
- ATR median expansion filter
- Prior-bar Donchian capture before indicator update
- EMA(100) regime exit (not EMA(200))
- 6-bar cooldown (vs 12)
- Uses `Quantity(position_qty, 8)` constructor (not `from_str`)

**Multi-window research** (`run_v4_research.py`): Runs across 4 windows (2024h1, 2024h2, 2025, 2026). Aggregates 15m bars to 1h. Verdict logic: gross PnL positive >50% windows + net PnL positive >50% windows = PROMISING; gross positive only = MIXED; otherwise REJECTED. Final rule in config_v4.py docstring: "if 1h V4 fails gross, stop BTC/USD spot bar-level technical research under this fee model."

### 4.5 Live Trading Guard (`run_live_kraken_guarded.py`)

**Multi-layer safety**: Three guard conditions must pass:
1. `--live` CLI flag
2. `I_UNDERSTAND_THIS_CAN_LOSE_MONEY=yes` env variable
3. `KRAKEN_API_KEY` + `KRAKEN_API_SECRET` env variables

Heavy Nautilus imports deferred until live mode is confirmed (import-gating). Currently stubbed — creates instrument and returns without full live setup.

### 4.6 Kraken L2 Maker Paper Simulator (V7)

**Purpose**: Observer-only L2 market-making paper simulator. Models quote placement, fill probability, and adverse selection on Kraken BTC/ETH L2 book + trade data.

**Architecture**:
- `KrakenBookWS`: Async WebSocket v2 client for public `book` (depth=10) and `trade` channels. Auto-reconnect with exponential backoff (5 retries). Handles snapshots vs incremental updates.
- `OrderBook`: Mutable L2 book with best_bid/ask, midprice, spread_bps, imbalance (top 10 levels), stale/crossed detection. `apply_snapshot()` replaces book; `apply_update()` applies deltas.
- `QuoteEngine`: Manages up to 4 concurrent paper quotes. Cancellation triggers: max lifetime (5s default), stale book, mid-move >5bps, spread collapse <1bps, imbalance flip.
- `PaperFillModel`: Three fill models (pessimistic/neutral/optimistic). Default pessimistic: book must cross through quote price (best_ask <= bid or best_bid >= ask). No same-tick fills (<0.5s guard). Fill penalty 2bps (queue uncertainty).
- `MakerPaperSimulator`: Per-symbol book + quote engine + fill model. Processes book updates (checks fills, manages quotes), process_trade (adverse selection at 1/5/30/60s). Returns summary stats: fill rate, avg spread, quote lifetime, gross/net PnL.

**Configuration**:
- Symbols: BTC/USD, ETH/USD
- Duration: 600s default
- Fill model: pessimistic (default)
- Maker fee: 3bps (conservative)
- Quote side: both (bid and ask)
- Offset: 0bps (at touch)
- Quote lifetime: 5s

### 4.7 Kraken Market Structure Scanner (V6/V6-B/V6-C)

**V6 — Cross-Venue Spread Scanner**:
- Polls Kraken + Binance public REST for top-of-book tickers
- Cross-venue ticker comparison with fee math (Kraken 40bps, Binance 10bps, plus latency buffer)
- Tries both directions (buy Kraken sell Binance; buy Binance sell Kraken)
- Logs opportunities to JSONL, writes summary.json

**V6-B — Funding/Basis Scanner**:
- Scans spot vs perp basis + funding rates for cash-and-carry opportunities
- 16 assets: BTC, ETH, SOL, XRP, DOGE, LINK, AVAX, ADA, SUI, ARB, OP, APT, PEPE, WIF, TON
- Kraken spot + Kraken/Binance/Bybit perps
- Cost model: entry fees + exit fees + slippage + latency + basis risk (25bps) + quote mismatch (20bps)
- Minimum gates: funding APR ≥ 20%, net edge ≥ 25bps
- Some assets missing kraken_perp (DOGE, SUI, ARB, OP, APT, WIF, TON) or kraken_spot (PEPE)

**V6-C — Alt Funding Anomaly Monitor**:
- Extends V6-B with 3-tier cost scenarios (conservative/mixed/optimistic)
- Persistence tracking: candidate must survive N consecutive polls to be "durable"
- Dual JSONL output (observations + filtered candidates)
- Higher thresholds: APR 30%, net edge 25bps, longer duration (600s vs 120s)
- Top-N rankings in summary

**Funding venues** (detailed in `funding_venues.py`):
- Kraken: `/0/public/Ticker` (spot), `derivatives/api/v3/tickers` + `historical-funding-rates`
- Binance: `fapi/v1/fundingRate` + `fapi/v1/ticker/bookTicker`
- Bybit: `v5/market/funding/history` + `v5/market/tickers`

### 4.8 Monte Carlo Permutation Tests (mcpt-main)

**Purpose**: Statistical falsification framework. Compares real strategy profit factors against distributions from bar-permuted data.

**Components**:
- `bar_permute.py`: Sophisticated bar shuffler preserving autocorrelation structure. Separately shuffles intra-bar moves (H/L/C relative to open) and gaps (close-to-open). Supports multi-market permutations preserving cross-market correlation.
- `donchian.py`: Rolling Donchian long/short signals, brute-force optimization (lookback 12-168), walk-forward with 4-year training / 30-day step.
- `tree_strat.py`: DecisionTreeClassifier on log-return derivatives (diff6/24/168) predicting 24h direction. Commentary: "This is trash :)" — acknowledged as overfit.
- `moving_average.py`: Simple MA(10) > MA(30) crossover, profit factor + Sharpe.
- In-sample and walk-forward MCPT runners: 1000/200 permutations, count exceedances, report p-value.

### 4.9 Venue-Agnostic Signal Observer (Detailed Architecture)

This is the largest and most sophisticated project (~68 source files, 10 architectural layers).

#### Layer 1 — Core Data Models & Config
- **`config.py`**: Central configuration dataclasses — `Horizon`, `FeeModel`, `SignalSourceConfig`, `ObserverConfig`, `LeadLagConfig`
- **`models.py`**: Bar-level signal observation — `SignalEvent`, `ForwardReturnResult`, `HorizonSummary`, `SignalTypeSummary`, `SignalEvaluationSummary`
- **`tick_models.py`**: Nanosecond-precision tick-level dataclasses — `TradeTickLite`, `QuoteTickLite`, `TickSignalEvent`, `TickForwardReturn`
- **`dex_models.py`**: DEX pool snapshot and dislocation models — `DexPoolSnapshot`, `DexDislocationEvent`, `DexCexForwardResult`
- **`derivatives_models.py`**: Derivative tick and impulse models — `DerivativeTradeTick`, `DerivativeImpulseEvent`, `DerivativeLeadLagResult`

#### Layer 2 — Signal Generators
- **`signals.py`**: Bar-level signal generation (CSV loading + CrossMarketSignalGenerator)
- **`lead_lag.py`**: Cross-venue bar-level lead-lag + random baseline generator
- **`event_study.py` (696 lines)**: Central tick-level signal generator + evaluation engine. `TickLeadLagGenerator.generate()` scans sorted trades with sliding window. `evaluate_tick_signal()` computes forward returns via bisect. `evaluate_candidate_group()` applies six-gate heuristic. **This is the hub module** — used by 5+ other modules.
- **`trade_flow_impulse.py` (566 lines)**: Source-venue trade-flow anomaly detector. Four signal types: `_count_burst`, `_notional_burst`, `_large_trade`, `_signed_imbalance`. Uses rolling median baselines and Lee-Ready aggressor side inference.
- **`cross_asset_impulse.py` (839 lines)**: Cross-asset evaluator. Maps BTC/ETH impulses to altcoin targets. Multi-pair verdict logic with stream health, temporal overlap, and range thresholds.
- **`derivatives_lead_lag.py` (307 lines)**: Derivatives impulse generator. Three types: `notional_burst`, `price_shock`, `signed_imbalance`. Adapter `impulse_to_tick_signal()` converts to shared TickSignalEvent.
- **`dex_cex_dislocation.py` (322 lines)**: DEX-CEX dislocation detector. Three signal types: `dex_price_shock`, `dex_volume_burst`, `dex_liquidity_shock`.

#### Layer 3 — Forward Return Evaluation
- **`forward_returns.py` (252 lines)**: Bar-level evaluation — `get_entry_price`, `find_price_at_or_after`, `compute_forward_return`, `compute_excursions`
- **`forward_returns_gpu.py` (379 lines)**: GPU-accelerated tick-level via torch.searchsorted. Chunked CUDA batching for all signals × horizons in one pass.
- **`event_study.py`** (also Layer 2) — `evaluate_tick_signal` is the primary tick-level forward-return evaluator.

#### Layer 4 — Observer & Reporting
- **`observer.py` (224 lines)**: `SignalObserver.run()` ties signals → prices → evaluation → summary
- **`reports.py` (71 lines)**: Writes 6 output files: signal_events.jsonl, forward_returns.jsonl, summary.json/csv, by_signal_type.csv, rejections.json
- **`research_report_miner.py` (556 lines)**: Unified research status miner (separate copy from root-level)

#### Layer 5 — Data Infrastructure
- **`data_loading.py`**: CSV loading + synthetic data generation (deterministic seed=42)
- **`data_adapters.py` (337 lines)**: Multi-venue OHLCV ingestion, alignment, paginated download (Binance/Kraken/Coinbase)
- **`data_fetcher.py` (224 lines)**: Standalone Binance+Kraken REST fetchers
- **`data_download.py` (152 lines)**: CLI download orchestrator
- **`csv_normalizer.py` (158 lines)**: CSV alignment to common timestamp grid
- **`tick_store.py` (380 lines)**: JSONL tick I/O, dedup, stale tick rejection, stream merge
- **`symbol_aliases.py` (147 lines)**: Cross-venue symbol normalization via explicit alias registry → `CanonicalSymbol`

#### Layer 6 — DEX Adapters
- **`dex_adapters.py` (292 lines)**: DEX Screener REST adapter — search endpoint + pair-lookup
- **`collect_dex_snapshots.py` (157 lines)**: Timed collection loop → filtered/deduped JSONL

#### Layer 7 — Diagnostic Modules
- **`lead_lag_heatmap_gpu.py` (445 lines)**: GPU-ready lead/lag correlation heatmap (Pearson + directional alignment). CPU fallback. Writes JSON, CSV, Markdown reports.
- **`latency_diagnostics.py` (768 lines)**: Tick-stream timing analysis. Per-stream stats, cross-correlation at multiple bucket sizes, pairwise overlap, sub-second horizon confidence.
- **`cost_sensitivity.py` (469 lines)**: Breakeven cost analysis. Reads evaluation reports, computes breakeven cost levels. Diagnostic-only verdicts (COST_SENSITIVITY_READY, NO_EVALUATED_GROUPS, etc.)
- **`cross_capture_consistency.py` (509 lines)**: Multi-capture aggregation. Consistency scores, recurring near-miss detection, cost-wall diagnostics.
- **`candidate_falsification.py` (668 lines)**: Multi-source falsification matrix combining evaluated reports, cost sensitivity, permutation null, heatmaps, cross-capture consistency.
- **`mcpt_export.py` (413 lines)**: MCPT export adapter — selects MCPT-worthy groups, exports event returns as CSV.

#### Layer 8 — Statistical Falsification
- **`permutation_null.py` (743 lines)**: Null/permutation test via circular and block time shift. Preserves inter-event structure. 5-gate survival criteria.
- **`permutation_null_gpu.py`**: GPU-accelerated permutation null distribution.

#### Layer 9 — Stage 2 Pipeline (Precommitment, FDR, Criteria)
- **`stage2_precommitment_utils.py` (337 lines)**: Precommitment loading, `CollectionLock` (SHA-256 hash lock on precommitment files), `validate_markdown_json_match()`
- **`stage2_readiness_check.py` (470 lines)**: Pre-collection checks (imports, precommitment, git state, Hermes gateway)
- **`stage2_split_corpus.py` (426 lines)**: Temporal train/test split (70/30). Requires ≥10 FULL_ACTIVE captures. Excludes quarantined/burned/FAST_DIAGNOSTIC runs.
- **`stage2_fdr.py` (330 lines)**: BH and BY FDR correction on permutation p-values.
- **`stage2_check_criteria.py` (628 lines)**: Two modes: **discovery** (committed criteria + BH survival) and **holdout** (frozen discovery configs vs held-out test data)
- **`stage2_gate_watcher.py` (773 lines)**: Stress-triggered capture daemon. Polls volatility gate. Uses flock concurrency lock, capture spacing cooldown, persistent state.

#### Layer 10 — Runner Scripts

| Runner | Lines | Purpose |
|--------|-------|---------|
| `run_signal_observer.py` | — | Bar-level observer entry point |
| `run_lead_lag.py` | — | Bar-level cross-venue sweep |
| `run_tick_lead_lag.py` | 1425 | Tick-level sweep (discovery, load, evaluate, gate) |
| `run_derivatives_lead_lag.py` | 571 | Derivatives impulse → spot evaluation |
| `run_cross_asset_impulse.py` | 789 | Full cross-asset pipeline (load → generate → evaluate → gate → report) |
| `run_trade_flow_impulse.py` | — | Trade-flow impulse sweep |
| `run_dex_cex_dislocation.py` | — | DEX-CEX dislocation evaluation |
| `run_lead_lag_heatmap.py` | — | Heatmap diagnostics |
| `run_permutation_null.py` | — | Permutation null tests |
| `run_mcpt_export.py` | — | MCPT export |
| `run_cost_sensitivity.py` | — | Cost sensitivity analysis |
| `run_cross_capture_consistency.py` | — | Cross-capture aggregation |
| `run_candidate_falsification.py` | — | Falsification matrix |
| `run_derivatives_capture_campaign.py` | — | Multi-capture campaign |
| `run_derivatives_spot_capture.py` | — | Real-time tick capture |
| `run_derivatives_spot_lead_lag.py` | — | Derivatives→spot evaluation |
| `run_stage2_gate_watcher.py` | — | Stress-poller daemon |
| `run_tick_capture.py` | — | Tick capture daemon |
| `run_report_corpus.py` | — | Corpus-level processing |
| `run_index.py` / `run_artifacts.py` | — | Run management |
| `run_cost_sensitivity.py` | — | Cost sensitivity |

---

## 5. Platform/Service Inventory

| Platform | Transport | Auth | Used By |
|----------|-----------|------|---------|
| Kraken REST (`api.kraken.com`) | HTTP (urlopen, requests) | Public | volatility_gate.py, download_kraken_ohlcv.py, fundng_venues.py, data_fetcher.py |
| Kraken WS v2 (`ws.kraken.com/v2`) | WebSocket (aiohttp) | Public | kraken_ws.py (L2 book + trades) |
| Kraken Futures REST | HTTP | Public | funding_venues.py |
| Binance REST (spot + fapi) | HTTP | Public | venues.py, funding_venues.py, data_fetcher.py |
| Bybit REST (`api.bybit.com`) | HTTP | Public | funding_venues.py |
| Coinbase REST | HTTP | Public | data_adapters.py |
| DEX Screener REST | HTTP | Public | dex_adapters.py, collect_dex_snapshots.py |

**Key observation**: All transport is public (no API keys required). All scanners poll at configurable intervals (2s for V6 spread, 8h implied for funding rates). No WebSocket streaming for scanners (only for L2 maker paper). No authentication secrets stored in source.

---

## 6. Shared Utilities

| Module | Purpose | Inputs | Outputs | Called By |
|--------|---------|--------|---------|-----------|
| `volatility_gate.py` | Kraken OHLCV volatility check | instrument list | JSON verdict dict | run_stage2_gate_watcher.py |
| `research_report_miner.py` | Report mining & aggregation | reports/ JSON/JSONL files | summary.json/csv + md | CLI |
| `symbol_aliases.py` (observer) | Cross-venue symbol normalization | raw ticker string | `CanonicalSymbol` | Multiple observer modules |
| `tick_store.py` | JSONL tick I/O | JSONL files | TradeTickLite / QuoteTickLite lists | All tick-level runners |
| `artifact_metadata.py` | Standardized metadata | schema_version, git SHA, mode | metadata dict | All data-write paths |
| `quarantine.py` | Run quarantine | run ID, reason | append-only JSONL | validate_capture.py |
| `burn.py` | Corpus burn records | signal family name | append-only JSONL | stage2 check criteria |
| `run_index.py` | Run tracking | run metadata | JSONL index | All runners |

---

## 7. Persistence & Schema

No centralized database. All persistence is file-based:

- **Data catalog**: Nautilus `ParquetDataCatalog` for historical OHLCV bars (Parquet files on NAS/Synology)
- **Tick data**: JSONL files (one line per tick) with naming convention: `{symbol}_{venue}_{date}_{capture_id}.jsonl`
- **Observations**: JSONL (append-only, one observation per line)
- **Summaries**: JSON (single file per run, overwritten)
- **Backtest results**: Pickle (single file, loaded by `reports.py`)
- **Reports**: JSON reports per evaluated study group
- **Run tracking**: Append-only JSONL (`run_index.jsonl`)
- **Quarantine/Burn**: Append-only JSONL files

**Schema-free** — no formal migrations. Data contracts are enforced by Python dataclasses (frozen, typed).

---

## 8. Frontend Architecture

Not applicable — this is a CLI-only research toolset. No SPA, no UI, no service worker.

---

## 9. Background Jobs & Scheduled Tasks

- **`run_stage2_gate_watcher.py`**: Daemon (poll loop) that checks volatility gate every few minutes and triggers FULL_ACTIVE captures. Uses `flock` for concurrency lock. Designed to run as systemd service (`systemd/nautilus-stage2-gate-watcher.service`).
- **No cron jobs or external schedulers** configured within this codebase.

---

## 10. Middleware & Cross-Cutting Concerns

- **Safety guards in live runners**: `run_live_kraken_guarded.py` requires `--live` flag + env var + API keys. Strategy has hard limits (MAX_ORDER_NOTIONAL_USD=25, MAX_DAILY_LOSS_USD=25).
- **Safety constraints in all diagnostic modules**: `cost_sensitivity.py`, `cross_capture_consistency.py`, `candidate_falsification.py`, `lead_lag_heatmap.py` enforce allowed/forbidden verdicts via frozensets — preventing accidental promotion of diagnostic-only results to execution-readiness.
- **frozenset verdict guards**: All diagnostic modules restrict what verdicts they can output. `CostSensitivityVerdict.ALLOWED_VERDICTS`, etc.
- **Concurrency locking**: `run_stage2_gate_watcher.py` uses `flock` to prevent concurrent captures.
- **No CSRF, rate limiting, auth middleware**: Not applicable for CLI research tools.

---

## 11. Type System & Contracts

Two-tier data model:

**Bar-level** (`models.py`):
- `SignalEvent`: signal_id, timestamp, source/target venue+instrument, signal_type, direction, strength
- `ForwardReturnResult`: signal + horizon + entry_price + forward_price + raw/adj/net returns + excursions
- `HorizonSummary`: mean/median return, win_rate, p25/p50/p75/p90, best/worst

**Tick-level** (`tick_models.py`):
- `TradeTickLite`: ts_event (int ns), venue, symbol, price, size, side
- `QuoteTickLite`: bid/ask, mid, spread_bps
- `TickSignalEvent`: signal_id, ts_event, source/target venue+symbol, direction, lookback_ms, threshold_bps, source_move_bps
- `TickForwardReturn`: signal_ts, horizon_ms, entry/forward prices, raw/dir_adj/net returns in bps

**Derivatives** (`derivatives_models.py`):
- `DerivativeTradeTick`: ts_event (int ns), venue, symbol, price, size, side, notional property
- `DerivativeImpulseEvent`: ts_event, type (notional_burst/price_shock/signed_imbalance), strength, optional OI/funding context

**DEX** (`dex_models.py`):
- `DexPoolSnapshot`: chain, dex, pair_address, price, liquidity, volumes, txns, imbalances
- `DexDislocationEvent`: type (price_shock/volume_burst/liquidity_shock), direction, strength

**Output artifacts** (`artifact_metadata.py`): Standardized schema_version, git SHA, capture mode, safety mode injected into all JSON outputs.

---

## 12. Configuration & Environment

| Config | Location | Key Parameters |
|--------|----------|----------------|
| Kraken BTCUSD Research | `kraken_btcusd_research/config.py` | EMA(20/100), Donchian(55), ATR(20), 5m bars, 0.4% taker fee, 0.25% risk, $25 safety limits |
| V4 Strategy | `kraken_btcusd_research/config_v4.py` | EMA(50/200), Donchian(100), ATR(20), 1h bars, 4x ATR trailing, 6-bar cooldown |
| V7 L2 Maker | `kraken_l2_maker_paper/config.py` | BTC/ETH, 600s, pessimistic fill, 3bps maker fee, 5s quote lifetime, 0bps offset |
| V6 Scanner | `kraken_market_structure_scanner/config.py` | 5 symbols, Kraken+Binance, 40/10 bps fees, 2s poll interval |
| V6-B Funding | `kraken_market_structure_scanner/funding_config.py` | 16 assets, 20% min APR, 25bps min edge, 20bps mismatch buffer |
| V6-C Alt Funding | Reuses funding_config | 30% min APR, 25bps min edge, 600s duration, 3 min persistence polls |
| Observer config | `*_signal_observer/config.py` | 6 horizons (10s-1h), configurable fee/buffer model |
| Volatility gate | `volatility_gate.py` | BTC 75bps/30bps, ETH 90bps/40bps, 2x accel threshold |

**Environment variables** (live trading only):
- `KRAKEN_API_KEY` — Kraken API key
- `KRAKEN_API_SECRET` — Kraken API secret
- `I_UNDERSTAND_THIS_CAN_LOSE_MONEY` — must be `yes` for live mode

Secrets are [REDACTED] in the report — no credentials stored in source.

---

## 13. CI/CD Pipeline

No `.github/workflows/` directory found in the strategies directory. There is no CI/CD configured for this research codebase.

---

## 14. Security Posture

- **No API keys in source**: All API interactions use public endpoints. If live trading is enabled, credentials come from env variables only.
- **Observer-only constraint enforced by design**: All tests assert no `place_order`, `create_order`, `submit_order`, `market_order`, `limit_order`, or `api_key` tokens appear in source code.
- **Live trading guard**: Multi-layer safety (CLI flag + env var + API key presence) before any live execution.
- **Strategy safety limits**: Hard-coded MAX_ORDER_NOTIONAL_USD=25, MAX_DAILY_LOSS_USD=25 in config.py.
- **No prompt injection surface**: No LLM integration in this codebase.
- **No CSRF, rate limiting, or authentication middleware**: Not applicable for local research tools.
- **Diagnostic verdict frozensets**: Prevent accidental promotion of diagnostic-only results.

---

## 15. Known Patterns & Conventions

1. **Two-tier data model**: Bar-level vs tick-level models with separate schemas but shared evaluation patterns
2. **Adapter pattern**: `dex_event_to_tick_signal()`, `impulse_to_tick_signal()` convert domain events to shared `TickSignalEvent` for unified evaluation
3. **Six-gate candidate evaluation**: Central gate in `event_study.py` — min events, mean > 0, median > -5bps, win rate beats baseline, beats baseline by margin, not single-event-driven
4. **Stage 2 lock-based integrity**: SHA-256 hashed precommitment prevents post-hoc tuning
5. **Append-only state management**: Run index, quarantine, burn — all JSONL, never mutated in-place
6. **GPU acceleration slots**: Separate `*_gpu.py` modules with CPU fallback for CUDA paths
7. **Named research versions**: V1-V4 (BTCUSD directional), V6/V6-B/V6-C (scanner), V7 (L2 maker)
8. **Project rejection tracking**: `REJECTED_RESEARCH.md` with locked gates — do-not-revisit markers

---

## 16. Dependency Map

| Dependency | Version | Purpose |
|-----------|---------|---------|
| nautilus_trader | 1.227.0 | Core framework |
| numpy | ≥1.26.4 | Numerical computing |
| pandas | ≥2.3.3 | Data handling, CSV |
| pyarrow | ≥23.0.1 | Parquet catalog |
| msgspec | ≥0.21.1 | Fast serialization |
| torch | system | GPU acceleration (observer) |
| click | ≥8.0 | CLI commands |
| fsspec | ≥2025.2 | Filesystem abstraction |
| aiohttp | system | WebSocket + async HTTP |
| pytest | 7.4.4+ | Testing |
| ruff | system | Linter |

---

## 17. Edge Cases & Operational Notes

1. **uv version mismatch**: Config pins 0.11.8 but system has 0.11.13 — `uv run` may be blocked. Virtualenv binaries (`.venv/bin/python -m pytest`) work as fallback.
2. **NFS mount**: Code runs on NFS mount (`/mnt/nasirjones/web/`) to Synology NAS. `find . -type f` without `-maxdepth` can hang on mounted filesystems. Use scoped `-maxdepth` or per-subdirectory finds.
3. **`kraken_v5_portfolio/` is empty** — no Python files found. The ARCHITECTURE_NOTES.md in that directory doesn't exist (the ARCHITECTURE_NOTES.md file found was for kraken_market_structure_scanner).
4. **DEX Screener limitations**: Search API returns stale/cached prices in quiet markets. Snapshot windows need 30-60 minutes for useful data.
5. **Backtest baselines**: 60s baseline window too large for 300-600s captures — causes 9 NEEDS_MORE_DATA entries.
6. **Pickle coupling**: `btcusd_research/reports.py` loads pickle files — fragile across Python/Nautilus version changes.
7. **No retry logic**: REST fetchers (except volatility_gate.py) have no retry/backoff.
8. **Cross-asset beta lag**: Only testable during genuine stress windows. Quiet-market captures are diagnostic only — cannot produce structural rejections.
9. **`_PATH_PROJECT_MAP` regex in research_report_miner.py**: Maps file paths to project names via regex. The `kraken_btcusd_research` pattern could potentially match `l2_maker_paper` if naming overlaps.

---

## 18. Testing Posture

- **Test runner**: pytest 7.4.4 (compiled artifacts also show pytest 9.0.3)
- **Total test files**: ~40 across all projects
- **Coverage by project**:
  - `kraken_btcusd_research/tests/`: 16 test files (~2,000+ lines) — backtest smoke, basic, breakout, catalog importer, instrument, live guard, position sizing, real reports, reports smoke, synthetic backtest, v4 trend smoke, helpers
  - `kraken_market_structure_scanner/tests/`: 3 test files (~725 lines total) — v6 scanner, v6b funding, v6c alt funding
  - `kraken_l2_maker_paper/tests/`: 1 test file + __init__.py
  - `venue_agnostic_signal_observer/tests/`: 25+ test files — comprehensive coverage of every module
  - Root-level: 2 test files (volatility gate + capture permission)
- **Key test patterns**:
  - All projects have security tests asserting no order placement code
  - Synthetic test data with deterministic generation (seed=42)
  - Extensive use of dataclass construction for fixtures (no mocking)
- **No linter output available** — ruff is configured but was not run during this audit
- **No CI pipeline** — tests must be run manually or via systemd service

---

## 19. Top Priorities / Recommendations

### Critical
*(None — both bugs found during this audit have been fixed.)*

### High
1. **Define `kraken_v5_portfolio/`** — directory exists but has no Python files. Either populate with the v5 portfolio strategy or remove the empty directory.
2. **Remove pickle coupling** in `kraken_btcusd_research/reports.py` — switch to Parquet or JSON serialization for backtest results to survive version upgrades.

### Medium
3. **Add retry logic to REST fetchers** — currently only `volatility_gate.py` has retry/backoff. All other REST clients are single-attempt.
4. **Add CI pipeline** — at minimum a basic pytest + ruff workflow to catch regressions.
5. **Consolidate `research_report_miner.py`** — there are two copies (root level and inside venue_agnostic_signal_observer). Unify to one canonical copy.
6. **Standardize logging** — some modules print to stdout, some to stderr, some use Nautilus logging, some use Python logger.

### Low
7. **Add type annotations** to standalone functions in `volatility_gate.py` and `run_backtest.py`.
8. **Document reverse proxy/port forwarding requirements** if any runners need live exchange access from behind NAT/NAS.
9. **Add `__init__.py` in `examples/strategies/docs/`** if the `docs/` directory should be a proper package (currently contains only audit reports).
10. **Rename `mcpt-main/`** — the `-main` suffix is redundant since it's already in the strategies directory.

---

## 20. Post-Audit Addition: Event-Window Differential V1 Precommitment

### Date: 2026-05-15
### Commit: `90faf95b4c`

A new sibling Stage 2 precommitment was added for the
`cross_asset_event_window_differential_v1` signal family — a comparative
hypothesis testing whether scheduled macro event windows produce materially
different cross-asset impulse propagation compared to offset baseline windows.

### Files Added

| File | Purpose |
|------|---------|
| `EVENT_WINDOW_DIFFERENTIAL_PRECOMMITMENT.md` | Human-readable precommitment |
| `event_window_differential_precommitment.json` | Machine-readable sidecar |
| `EVENT_WINDOW_THRESHOLDS_RATIONALE.md` | Threshold rationale document |
| `test_event_window_precommitment.py` | 42 tests (8 test classes) |

### Key Design Parameters

| Parameter | Value |
|-----------|-------|
| Signal family | `cross_asset_event_window_differential_v1` |
| Source assets | BTC, ETH |
| Target assets | SOL, LINK, DOGE, AVAX |
| Primary signal type | `signed_imbalance` |
| Lookback | 30,000 ms (30 s) |
| Horizons | 10s, 30s, 60s, 5 min (4 values) |
| Event window | `[-5 min, +15 min]` relative to scheduled event |
| Baseline window | `[+90 min, +110 min]` (offset, same duration) |
| Min capture duration | 1,200 s per window |
| Min global overlap | 30 s |
| Baseline rule | Explicitly parameterised, NOT the old 60s default |
| Cost model | 50 bps all-in (40 fee + 5 slippage + 5 mismatch) |
| FDR | BH q=0.10 primary, BY q=0.10 sensitivity |
| Holdout | Temporal 70/30, min 10 validated paired captures |

### Test family dimensions

The event-window precommitment adds `window_type` (values: `event`, `baseline`)
as a 6th test family dimension — the structural distinction from
`cross_asset_beta_lag_v1` (which has 5 dimensions).  This allows FDR-corrected
comparison of event vs baseline propagation for the same config.

### Separation from Beta Lag

| Check | Passed |
|-------|--------|
| Different signal_family | Yes |
| Beta-lag files not modified | Yes (5 gating assertions) |
| Beta-lag stress gate (150 bps) unchanged | Yes |
| Beta-lag single-horizon config unchanged | Yes |
| Beta-lag lock and watcher untouched | Yes |
| No event-window capture started | Yes |
| No event-window systemd service added | Yes |

### Existing Beta Lag Status

- **Canonical and untouched.**  The cross-asset beta lag watcher, lock, corpus,
  burn/quarantine files, and precommitment artifacts are unchanged.
- **Git SHA:** `90faf95b4c` (same commit).
- **No collection started for event-window either.**

### Stage 2 Utility Multi-Hypothesis Support

All Stage 2 utilities already support multiple precommitment files via explicit
path arguments:

| Module | Path Parameter | Works with |
|--------|---------------|------------|
| `load_precommitment(path)` | Optional `Path` | Event-window JSON via explicit path |
| `CollectionLock(lock_path, precommit_path, md_path, rationale_path)` | All optional | Separate lock file per hypothesis |
| `build_split(precommit_path=...)` | Optional | Event-window JSON via explicit path |
| `run_fdr(precommit_path=...)` | Optional | Event-window JSON via explicit path |
| `run_discovery_check(precommit=...)` | Takes dict directly | Any loaded precommitment dict |

**No code changes were required.**  The optional path parameters on
`CollectionLock.__init__` and all CLI runner `--precommit-path` arguments
provide the extension point.  Multi-hypothesis is architecturally supported
without modifying any Stage 2 utility.

### Test Results

- **Event-window tests:** 42 passed, 0 failed
- **Existing Stage 2 tests:** 52 passed, 0 failed (unchanged)
- **Full observer test suite:** 791 passed, 0 failed
- **All tests run from repo root using `.venv/bin/python -m pytest`**

### Safety Statement

Public data observer only.  No auth.  No orders.  No execution.  No trading.
No API keys.  No private endpoints.  No broker adapters.  No position
management.  No capital deployment.  No scheduler or systemd collector was
created or modified.