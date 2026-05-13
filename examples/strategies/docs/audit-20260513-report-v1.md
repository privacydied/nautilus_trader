# PRODUCT AUDIT — Nautilus Trader Strategies Collection

## Date: 2026-05-13
## Version: v1

---

## 1. Executive Summary

This directory contains **five independent Nautilus Trader research projects**, each representing a different generation of hypothesis testing for crypto trading strategies on Kraken (and multi-venue). They evolved from a single-asset BTC/USD backtest (V4) through multi-asset momentum (V5), cross-venue spread scanning (V6), L2 maker microstructure paper trading (V7), and a venue-agnostic signal evaluator. All are research/backtest or observer-only scaffolds — **none place real orders**. The collective hypothesis history is: every directional OHLCV strategy and cross-venue spread strategy has been rejected under taker fee assumptions; the current live question is whether maker-level microstructure edges exist.

---

## 2. Architecture Overview

### Sub-Project Map

| Project | Version | Purpose | Status | Source Files |
|---------|---------|---------|--------|-------------|
| `kraken_btcusd_research` | V4 | Single BTC/USD trend-following backtest | **REJECTED** — net negative after fees | ~12 |
| `kraken_v5_portfolio` | V5 | Multi-asset (10 pairs) momentum portfolio | Pending verdict | 7 |
| `kraken_market_structure_scanner` | V6 | Cross-venue spread + funding/basis scanner | **REJECTED** — net negative after costs | ~15 |
| `kraken_l2_maker_paper` | V7 | L2 order book paper trading simulator | **IN PROGRESS** | 11 |
| `venue_agnostic_signal_observer` | — | Venue-agnostic multi-horizon signal evaluator | Research tool | 9 |

### Shared Data Pipeline (per-project)

```
Kraken Public API (REST/WS)
       │
       ▼
  CSV files (OHLCV)
       │
       ▼
  ParquetDataCatalog (Nautilus)
       │
       ▼
  BacktestEngine / Paper Simulator
       │
       ▼
  Reports (JSON/CSV/JSONL)
```

### Directory Structure

```
strategies/
├── kraken_btcusd_research/          # V4 — single BTC/USD backtest
│   ├── config.py                    #   V1/V2/V3 params (unused)
│   ├── config_v4.py                 #   V4 params (1h, EMA 50/200, Donchian 100)
│   ├── strategy.py                  #   V1/V2/V3 strategy (has indentation bug)
│   ├── strategy_v4.py               #   V4 1h trend-following strategy
│   ├── run_backtest.py              #   V1/V2/V3 single-window runner
│   ├── run_v4_research.py           #   V4 multi-window runner (agg 15m→1h)
│   ├── download_kraken_ohlcv.py     #   Kraken REST API → CSV downloader
│   ├── import_kraken_ohlcv_to_catalog.py  # CSV → Parquet catalog
│   ├── debug_v4.py                  #   V4 indicator diagnostic tool
│   ├── reports.py                   #   Shared report utilities (parse_pnl, parse_commission)
│   └── tests/                       #   ~15 test files (many overlapping)
│
├── kraken_v5_portfolio/             # V5 — multi-asset momentum
│   ├── config_v5.py                 #   10-pair config, dead constants
│   ├── instrument_details.py        #   Single source of precision tuples
│   ├── strategy_v5.py               #   Multi-asset momentum strategy
│   ├── run_v5_research.py           #   Multi-window runner (daily bars)
│   ├── download_kraken_multi.py     #   Multi-pair Kraken API downloader
│   ├── import_multi_catalog.py      #   CSV → Parquet catalog (multi-pair)
│   └── tests/                       #   2 test files, ~18 tests
│
├── kraken_market_structure_scanner/ # V6 — scanner + funding monitor
│   ├── config.py                    #   V6 scanner config
│   ├── scanner.py                   #   Cross-venue spread scanner
│   ├── symbols.py                   #   16-asset symbol mapping
│   ├── venues.py                    #   Venue definitions (Kraken, Binance, Bybit)
│   ├── opportunity.py               #   Net edge computation
│   ├── funding_config.py            #   V6-B funding scanner config
│   ├── funding_scanner.py           #   V6-B funding/basis scanner
│   ├── funding_models.py            #   V6-B cost model (fees + slippage)
│   ├── funding_venues.py            #   V6-B venue definitions
│   ├── funding_reports.py           #   V6-B report generation
│   ├── funding_models_alt.py        #   V6-C alt: 3 cost scenarios
│   ├── funding_scanner_alt.py       #   V6-C alt: persistence tracker
│   ├── funding_reports_alt.py       #   V6-C alt: JSONL dual output
│   ├── run_v6_scanner.py            #   V6 runner
│   ├── run_v6_funding_basis.py      #   V6-B funding runner
│   ├── run_v6_alt_funding_monitor.py#   V6-C alt runner
│   └── tests/                       #   3 test files, ~50+ tests
│
├── kraken_l2_maker_paper/           # V7 — L2 maker paper simulator
│   ├── config.py                    #   V7Config dataclass
│   ├── symbols.py                   #   BTC/USD, ETH/USD symbol mapping
│   ├── book_models.py               #   OrderBook, BookLevel, BookSnapshot
│   ├── kraken_ws.py                 #   aiohttp WebSocket client (v2)
│   ├── paper_quote.py               #   Quote lifecycle management
│   ├── paper_fill_model.py          #   3 fill models + adverse selection
│   ├── simulator.py                 #   Main async event loop
│   ├── reports.py                   #   JSONL + JSON output
│   ├── run_v7_l2_maker_paper.py     #   CLI entrypoint
│   └── tests/                       #   1 test file, ~30 tests
│
└── venue_agnostic_signal_observer/  # — Multi-horizon signal evaluator
    ├── config.py                    #   ObserverConfig, Horizon, FeeModel
    ├── models.py                    #   SignalEvent, summaries, types
    ├── signals.py                   #   CSV loader, signal generator
    ├── data_loading.py              #   Bar loader, synthetic generator
    ├── forward_returns.py           #   Forward return computation
    ├── observer.py                  #   Main orchestrator
    ├── reports.py                   #   6 output files
    ├── run_signal_observer.py       #   CLI runner
    └── tests/                       #   1 test file, ~20 tests
```

---

## 3. Startup & Bootstrap

Each sub-project is a standalone CLI tool. No shared server, no shared config, no shared state. Data dependencies are resolved per-project through their own download→import→run pipelines.

**Shared infrastructure** (duplicated across projects):
- CSV download from Kraken REST API (each project has its own copy)
- CSV→Parquet catalog import (each project has its own copy)
- Report generation utilities (some cross-importing exists)

**Cross-project coupling:**
- `kraken_btcusd_research/reports.py` is imported by `kraken_v5_portfolio/run_v5_research.py` (parse_pnl, parse_commission)
- `v5_portfolio` references `kraken_btcusd_research` — projects are not truly isolated

---

## 4. Complete Entry-Point Map

### V4: kraken_btcusd_research

| Entry Point | Purpose | Key Arguments |
|------------|---------|---------------|
| `download_kraken_ohlcv.py` | Kraken REST OHLCV → CSV | `--pair`, `--interval` (1/5/15/30/60/240/1440/10080), `--since`, `--until` |
| `import_kraken_ohlcv_to_catalog.py` | CSV → Parquet | `--csv-dir`, `--catalog` |
| `run_backtest.py` | Single-window V1/V2/V3 backtest | `--catalog`, `--start`, `--end`, `--starting-balance` |
| `run_v4_research.py` | V4 multi-window (4 windows) backtest | None — reads WINDOWS from config_v4 |
| `debug_v4.py` | V4 indicator diagnostics (no engine) | None — hardcoded catalog path |

### V5: kraken_v5_portfolio

| Entry Point | Purpose | Key Arguments |
|------------|---------|---------------|
| `download_kraken_multi.py` | Multi-pair Kraken REST → CSV | `--pair`, `--interval` (240/1440), `--since`, `--until` |
| `import_multi_catalog.py` | Multi-pair CSV → Parquet | `--csv-dir`, `--catalog` |
| `run_v5_research.py` | Multi-window (3 windows) daily backtest | None — reads WINDOWS from config_v5 |

### V6: kraken_market_structure_scanner

| Entry Point | Purpose | Key Arguments |
|------------|---------|---------------|
| `run_v6_scanner.py` | Cross-venue spread scanner | `--symbols`, `--interval`, `--duration`, `--output` |
| `run_v6_funding_basis.py` | V6-B funding/basis scanner | `--symbols`, `--interval`, `--duration`, `--output` |
| `run_v6_alt_funding_monitor.py` | V6-C altcoin anomaly monitor | `--symbols`, `--duration`, `--output`, `--min-apr` |

### V7: kraken_l2_maker_paper

| Entry Point | Purpose | Key Arguments |
|------------|---------|---------------|
| `run_v7_l2_maker_paper.py` | Live WebSocket paper trading | `--symbols`, `--duration`, `--quote-lifetime`, `--fill-model`, `--maker-fee-bps` |

### Signal Observer

| Entry Point | Purpose | Key Arguments |
|------------|---------|---------------|
| `run_signal_observer.py` | Multi-horizon signal evaluation | `--synthetic` or `--signals-csv` + `--bars-csv` |

---

## 5. Per-Project Deep Analysis

### 5.1 V4: kraken_btcusd_research (BTC/USD Trend-Following)

**Hypothesis**: Technical trend-following on 1h BTC/USD bars with EMA(50)/EMA(200) crossover, Donchian breakout, ATR volatility filter, and 4x ATR trailing stop can produce positive net PnL after 0.40% taker fees.

**Verdict**: **REJECTED** — net PnL negative across all test windows.

**Strategy logic** (strategy_v4.py):
- Entry requires ALL 4 conditions: trend filter (EMA50>EMA200), breakout (close>Donchian100), volatility (ATR>0), ATR expansion (current ATR > rolling median of last 100).
- Exit: ATR trailing stop (4x) or regime break (close < EMA100).
- Long-only, spot, taker entries/exits, 0.001 BTC minimum.
- 200-bar warmup (EMA_slow period).

**Data flow**: 15-min catalog bars → aggregated to 1h in-memory → BacktestEngine.

**Test coverage**: ~15 test files, many overlapping (test_basic, test_all, test_kraken_btcusd_research all test the same imports + config). ~5 verify actual backtest behavior. No E2E test of run_v4_research.py.py. test_backtest_smoke.py stub.

**Issues**:
1. **Indentation bug** in strategy.py (line 160): cooldown check + trailing update execute outside proper control flow
2. **import_kraken_ohlcv_to_catalog.py** missing top-level imports (ParquetDataCatalog, Decimal, INSTRUMENT_ID imported only inside functions)
3. **reports.py** uses relative import `from .config` — breaks if imported from outside package
4. **Cross-project coupling**: reports.py imported by v5_portfolio

### 5.2 V5: kraken_v5_portfolio (Multi-Asset Momentum)

**Hypothesis**: Diversifying across 10 liquid Kraken USD spot pairs with 4h resolution and strict portfolio rules (max 3 positions, max 50% exposure) provides enough gross edge to overcome 0.40% taker fees.

**Verdict**: **PENDING** — awaits data availability.

**Strategy logic** (strategy_v5.py):
- Per-asset: EMA(200) trend filter, ATR(28) for trailing stops.
- Portfolio: Score all in-uptrend assets by absolute momentum (200-bar lookback), hold top 3.
- Exits: ATR trailing (3x) or trend loss (close < EMA200) or rank drop.
- Rebalance: Every 6 bars on avg (daily bars → every 6 days).
- Position sizing: 5% of account per asset (config.risk_percent 0.01 * 5 hardcoded multiplier), capped at 50% total.

**Issues** (from v3 audit — 8 open issues):
1. Five dead constants in config_v5.py (never consumed)
2. Precision duplication across config_v5.INSTRUMENT_DETAIL and instrument_details.PRICE_SIZE_PRECISION
3. EMA/ATR periods hardcoded — config params ignored
4. Silent exception swallowing in _account_value()
5. O(n) dict scans per bar
6. --api-key arg parsed but never attached
7. bar_count dead field
8. Cross-project coupling to btcusd_research.reports

### 5.3 V6: kraken_market_structure_scanner (Cross-Venue + Funding)

**Three evolutionary phases**:

**V6 (Cross-Venue Spread)**: Polls Kraken+Binance public REST for bid/ask, computes gross/net edge after fees+latency buffer. Outputs opportunities.jsonl + summary.json.

**V6-B (Funding/Basis Scanner)**: Scans Kraken spot vs Kraken/Binance/Bybit perps. 16 assets in SYMBOL_MAP (BTC, ETH, SOL, XRP, DOGE, LINK, AVAX, ADA, SUI, ARB, OP, APT, PEPE, WIF, TON). Computes basis bps, funding APR, net edge vs total cost (fees+slippage+risk buffers). USD/USDT mismatch adds 20bps penalty. Outputs funding_basis_observations.jsonl + summary.json.

**V6-C (Altcoin Anomaly Monitor)**: Reuses V6-B config and venues. Three cost scenarios (conservative_taker, mixed_maker_taker, optimistic_maker). PersistenceTracker: candidate must survive N consecutive polls to be "durable". Dual JSONL output (observations + filtered candidates). Top-10 rankings. 10 altcoins default, 600s duration, 30% min APR.

**Status**: **REJECTED** — net negative across all windows after realistic fee assumptions.

**Test coverage**: ~3 test files, ~50+ tests covering symbol mapping, opportunity math, cost scenarios, persistence, reports, and security (no order placement verification).

### 5.4 V7: kraken_l2_maker_paper (L2 Order Book Paper Trading)

**Hypothesis**: Maker-style microstructure edge exists — placing quotes at the book's best bid/ask and capturing the spread can be profitable even after maker fees and adverse selection.

**Status**: **IN PROGRESS** — live observer, no verdict yet.

**Architecture**:
- WebSocket client (aiohttp) connects to Kraken v2 WS (`wss://ws.kraken.com/v2`)
- Subscribes to book + trade channels for BTC/USD and ETH/USD
- Maintains live OrderBook state from incremental deltas
- QuoteEngine: Places quotes at best bid/ask, max 4 concurrent per symbol, 5s max lifetime
- PaperFillModel: 3 fill models (pessimistic/neutral/optimistic) with 2bps queue penalty
- Cancellation triggers: mid move >5bps, spread collapse <1bps, imbalance flip, stale book (>10s), crossed book
- Same-tick protection: 0.5s minimum before fill
- Adverse selection tracking: midprice movement at 1s, 5s, 30s, 60s windows

**Key design**: Observer-only, no orders, no API keys, no private execution. Conservative defaults (pessimistic fill, 2bps penalty, 3bps maker fee) set a high bar to prove profitability.

**Test coverage**: ~30 tests across OrderBook, PaperQuote, PaperFillModel, reports, and config.

### 5.5 venue_agnostic_signal_observer (Multi-Horizon Signal Evaluator)

**Purpose**: Evaluate trading signals across 6 time horizons (10s, 30s, 1m, 5m, 15m, 1h) with full cost modeling and direction awareness. Venue-agnostic — works with any data source.

**Architecture**:
- Configurable horizons, fee models, signal sources
- Bisect-based price lookup for forward returns
- Excursion tracking (best/worst price between signal and horizon)
- Synthetic data generator (seed=42) for testing
- 6 output files: 2 JSONL, 2 CSV, 1 JSON summary, 1 rejections JSON

**Test coverage**: ~20 tests in single test file.

---

## 6. Shared Pattern Analysis

### Data Sources (per project)

| Project | Data Source | Transport | Auth |
|---------|------------|-----------|------|
| V4 | Kraken REST OHLCV | HTTP GET (requests) | None (key arg unused) |
| V5 | Kraken REST OHLCV | HTTP GET (requests) | None (key arg unused) |
| V6 | Kraken/Binance REST order books | HTTP GET | None |
| V6-B | Kraken/Binance/Bybit REST funding | HTTP GET | None |
| V7 | Kraken WebSocket v2 | ws.kraken.com/v2 (aiohttp) | None — public |
| Signal Observer | CSV or synthetic | N/A | N/A |

### Fee Assumptions (conservative throughout)

| Project | Fee Type | Rate |
|---------|----------|------|
| V4 | Taker | 0.40% |
| V5 | Taker | 0.40% |
| V6 | Maker | 0.016% (tier 1) |
| V6-B | Varies by scenario | 0.04–0.40% |
| V7 | Maker | 0.03% (3bps) |
| Signal Observer | Configurable | Varies |

### Strategy Hypothesis History

1. V4 trend-following → REJECTED (taker fees kill edge)
2. V5 multi-asset momentum → PENDING
3. V6 cross-venue spread → REJECTED (fees + latency)
4. V6-B funding/basis → REJECTED (fees + slippage)
5. V7 maker microstructure → IN PROGRESS

---

## 7. Test Coverage Summary

| Project | Test Files | Tests | Quality |
|---------|-----------|-------|---------|
| V4 btcusd_research | ~15 files | ~30+ | Overlapping, some stubs |
| V5 v5_portfolio | 2 files | ~18 | Synthetic backtests |
| V6 scanner | 3 files | ~50+ | Comprehensive |
| V7 l2_maker_paper | 1 file | ~30 | Good coverage of core |
| Signal Observer | 1 file | ~20 | Basic |

### Gaps
- V4: test_backtest_smoke.py is empty stub; no E2E test of runner; duplicate test files
- V5: No tests for download/import pipeline
- V6: No end-to-end run tests
- V7: No integration test with real WS data
- Signal Observer: Light coverage for complex forward_returns module

---

## 8. Cross-Project Coupling Issues

1. **Shared reports.py**: V5_portfolio imports from btcusd_research.reports (parse_pnl, parse_commission). This couples two otherwise independent projects.

2. **Duplicated infrastructure**: Each project has its own Kraken downloader, catalog importer, and report generator. No shared utilities module exists.

3. **Inconsistent naming**: BTC→XBT aliasing handled differently across projects (some convert on download, some on import, some in instrument creation).

4. **Precision drift risk**: V5 has precision data in 4 places (now being consolidated). Other projects hardcode precision ad-hoc.

---

## 9. Dependency Map

| Dependency | Used By | Purpose |
|-----------|---------|---------|
| nautilus_trader | All except V6, Signal Observer | Backtest engine, models, indicators |
| requests | V4, V5 | Kraken REST API |
| aiohttp | V7 | Kraken WebSocket |
| pandas | V4, V5, Signal Observer | CSV parsing, data manipulation |
| pytest | All | Test framework |

No shared requirements.txt or lockfile across projects.

---

## 10. Known Issues (Across All Projects)

### Critical

1. **V4 strategy.py indentation bug** (line 160): Cooldown/trailing logic executes on every bar including warmup.

2. **V4 import_kraken_ohlcv_to_catalog.py**: Missing top-level imports. Uses relative imports that break outside package.

3. **Cross-project coupling**: V5 imports from V4's reports.py. Projects not truly isolated.

### Structural

4. **Duplicated infrastructure**: Downloaders, importers, report utilities copied across projects. Each has its own Kraken API key arg (all ignored).

5. **BTC aliasing inconsistency**: XBT↔BTC conversion handled differently in each project.

6. **No shared test utilities**: Each project reinvents synthetic bar generation and engine fixtures.

7. **Incomplete consolidation in V5**: Precision data in 4 places; config has dead constants.

### Minor

8. **V7 neutral fill model**: Functionally identical to pessimistic (same-tick protection already limits aggressive fills).

9. **V4 runner loads all catalog bars** before filtering by window — memory inefficiency.

10. **V5 _account_value() silent failure**: Bare exception pass with no logging.

11. **V5 O(n) lookups**: Every bar scans the assets dict.

12. **Multiple dead/unused constants** across V4 and V5 configs.

---

## 11. Operational Notes

- All projects are CLI-only — no web UI, no API server, no daemon
- No live trading in any project (V7 is paper-only, V4/V5 are backtests, V6 is passive scanner)
- WebSocket project (V7) requires network access; backtest projects require local catalog
- Python 3.14 throughout — type hints used consistently
- 4-space indentation standard
- No CI/CD pipeline for any sub-project
- AGENTS.md references btcusd_research specifically — suggests current focus area

---

## 12. Future Architecture Considerations

1. **Shared infrastructure library**: Extract common downloaders, catalog importers, report utilities, test fixtures into a shared `nautilus_utils` package.

2. **Single precision source**: Consolidate all precision/tuple data into one canonical source (instrument_details.py pattern).

3. **Cross-project isolation**: Remove V5's import of V4 reports. Define shared interfaces for common utilities.

4. **E2E test suite**: Add integration tests that run the full data pipeline (download→import→backtest→report) for each project.

5. **CI/CD**: pytest on each sub-project independently.
