# PRODUCT AUDIT — examples/strategies (NautilusTrader)

## Date: 2026-05-13
## Version: v2

---

## 1. Executive Summary

This directory (`examples/strategies/`) contains four independent Python research projects built on the NautilusTrader framework. All are trading research tools — only one project (`kraken_btcusd_research`) has live-trading code, and that code is heavily guarded behind environment variables and CLI flags. The projects are:

1. **kraken_btcusd_research** — A Kraken BTC/USD backtest/live strategy research scaffold. Includes backtest runners, OHLCV downloaders, and a guarded live-trading entry point. Multiple iteration versions (V1–V3 on 5min bars, V4 on 1h bars) are all documented as rejected within the code.

2. **venue_agnostic_signal_observer** — A standalone, venue-agnostic signal research framework. Tests cross-venue signal hypotheses using forward-return measurement, random baselines, and candidate gating. Supports OHLCV lead-lag, tick-level lead-lag, and trade-flow impulse signals. Observer-only — no orders, no keys, no execution.

3. **kraken_l2_maker_paper** — An L2 order book paper simulator. Connects to Kraken's public WebSocket, maintains order book state, places hypothetical maker quotes, and checks fill eligibility based on pessimistic/neutral/optimistic fill models. Observer-only — no orders, no keys.

4. **kraken_market_structure_scanner** — A cross-venue market structure scanner. Polls REST tickers from Kraken, Coinbase, Binance, computes cross-venue arbitrage opportunities and funding/basis spreads between spot and perpetual venues. Includes a main scanner (V6), a funding/basis scanner (V6-B), and an altcoin funding monitor (V6-C). Observer-only — no orders, no keys.

All four projects share a common philosophy: research first, execute never unless a signal survives forward-return analysis against a random baseline with realistic fee/slippage assumptions. No project holds API credentials. No project runs trading by default.

---

## 2. Architecture Overview

### Deployment Model
Self-contained Python packages living inside the NautilusTrader repository at `examples/strategies/`. Each project is a Python package (has `__init__.py`). Entry points are either CLI scripts (`run_*.py`) or `__main__.py` modules.

### Tech Stack
- **Language:** Python 3.11+
- **Framework:** NautilusTrader (Rust-backed Python package)
- **HTTP client:** `requests` for REST polling
- **WebSocket client:** `aiohttp` for Kraken WS v2
- **Test runner:** pytest
- **Environment:** `uv` for dependency management

### Directory Structure

```
examples/strategies/
├── kraken_btcusd_research/          # Nautilus backtest + live strategy scaffold
│   ├── config.py, config_v4.py      # Strategy parameters (EMA, Donchian, ATR, fees)
│   ├── strategy.py, strategy_v4.py   # Nautilus Strategy subclasses
│   ├── run_backtest.py               # ParquetDataCatalog backtest runner
│   ├── run_v4_research.py           # 1h trend-following research (FINAL attempt)
│   ├── run_live_kraken_guarded.py   # Heavily guarded live mode
│   ├── download_kraken_ohlcv.py     # REST OHLCV downloader from Kraken
│   ├── debug_v4.py                  # Debug: count which entry conditions pass/fail
│   ├── reports.py                   # BacktestBack report generation (pickle-based)
│   └── tests/                       # 22 test files covering strategy, backtest, guards
│
├── venue_agnostic_signal_observer/  # Cross-venue signal research framework (17 modules)
│   ├── config.py, models.py         # Observer/v1 config, SignalEvent, ForwardReturnResult
│   ├── observer.py                  # Top-level bar-based orchestration
│   ├── signals.py                   # CSV signal loading, CrossMarketSignalGenerator
│   ├── data_loading.py              # Synthetic data generators, CSV loader
│   ├── forward_returns.py           # Per-horizon forward return calculation
│   ├── reports.py                   # JSONL/CSV/JSON report writing
│   ├── data_adapters.py             # Public REST downloaders (Binance, Kraken, Coinbase)
│   ├── csv_normalizer.py            # Multi-venue CSV alignment to common grid
│   ├── data_download.py             # Batch downloader for OHLCV CSVs
│   ├── data_fetcher.py              # Legacy REST fetch helpers
│   ├── lead_lag.py                  # Cross-market move signal generator + random baseline
│   ├── run_lead_lag.py              # CLI for OHLCV lead-lag sweep
│   ├── run_signal_observer.py       # Legacy CLI for bar-based observer
│   ├── tick_models.py               # TradeTickLite, QuoteTickLite, TickSignalEvent, TickForwardReturn
│   ├── tick_store.py                # JSONL loader/saver, sort, dedup, file discovery
│   ├── event_study.py               # Tick-level lead-lag generator, forward returns, gate
│   ├── trade_flow_impulse.py        # Trade-flow burst signals (count, notional, large, imbalance)
│   ├── run_trade_flow_impulse.py    # CLI runner for trade-flow impulse study
│   ├── run_tick_capture.py          # CLI for real-time public WS tick capture
│   ├── run_tick_lead_lag.py         # CLI for tick-level lead-lag sweep
│   └── tests/                       # 8 test files (132 tests)
│
├── kraken_l2_maker_paper/           # L2 maker-style paper simulator (9 modules)
│   ├── config.py                     # V7Config (symbols, duration, quote params, fees)
│   ├── book_models.py                # OrderBook with midprice, spread, imbalance, staleness
│   ├── kraken_ws.py                  # Kraken WS v2 L2 book subscriber (aiohttp)
│   ├── paper_quote.py                # PaperQuote lifecycle (place, cancel, fill)
│   ├── paper_fill_model.py           # Pessimistic/Neutral/Optimistic fill models
│   ├── simulator.py                  # Main loop: process books → check fills → place quotes
│   ├── symbols.py                    # BTC/USD, ETH/USD mappings (WS + REST)
│   ├── reports.py                    # JSONL event + summary.json writing
│   ├── run_v7_l2_maker_paper.py     # Entry point
│   └── tests/                        # 1 test file
│
├── kraken_market_structure_scanner/ # Cross-venue scanner + funding basis (17 modules)
│   ├── config.py                     # ScannerConfig, FeeConfig
│   ├── symbols.py                    # Symbol normalization (BTC/USD → XBTUSD, BTC-USD, etc.)
│   ├── venues.py                     # REST fetchers for Kraken, Binance (FETCHERS dict)
│   ├── scanner.py                    # Polling loop: fetch → normalize → check opportunities
│   ├── run_v6_scanner.py            # Entry point: cross-venue spread scanner
│   ├── opportunity.py                # Spread/opportunity calculator (buy venue A, sell venue B)
│   ├── funding_config.py             # FundingConfig + SYMBOL_MAP for BTC/ETH/SOL/XRP/etc.
│   ├── funding_models.py             # FundingObservation with basis, funding APR, net edge
│   ├── funding_reports.py            # JSONL observation writing + summary computation
│   ├── funding_scanner.py            # V6-B: spot+perp fetcher + observation builder
│   ├── funding_models_alt.py         # V6-C: FundingObservationAlt with 3 cost scenarios
│   ├── funding_reports_alt.py        # V6-C: candidate persistence + summary writing
│   ├── funding_scanner_alt.py        # V6-C: altcoin scanner with depth + persistence
│   ├── funding_venues.py             # REST adapters: Kraken spot/futures, Binance, Bybit
│   ├── run_v6_funding_basis.py      # V6-B entry: funding/basis scanner
│   ├── run_v6_alt_funding_monitor.py # V6-C entry: altcoin funding anomaly monitor
│   └── tests/                        # 2 test files
```

### Data Flow Diagram (Text-Based)

```
┌─────────────────────────────────────────────────────────┐
│  VENUE DATA SOURCES (Public)                            │
│  Kraken REST/WS, Coinbase REST, Binance REST, Bybit     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  [kraken_market_structure_scanner]                      │
│    ├── scanner.py polls REST tickers every N seconds    │
│    ├── venues.py normalizes Tickers across venues       │
│    ├── opportunity.py computes cross-venue edge         │
│    └── funding_scanner.py computes spot/perp basis      │
│        └── JSONL → reports/v6_market_structure/         │
│                                                         │
│  [kraken_l2_maker_paper]                                │
│    ├── KrakenBookWS subscribes to WS v2 book+trade      │
│    ├── OrderBook maintains bids/asks, mid, spread       │
│    ├── QuoteEngine places/retires paper quotes          │
│    ├── PaperFillModel checks fill eligibility           │
│    └── JSONL → reports/v7_l2_maker_paper/              │
│                                                         │
│  [venue_agnostic_signal_observer]                       │
│    ├── run_tick_capture.py collects WS TradeTicks       │
│    ├── tick_store.py loads/sorts/dedupes JSONL          │
│    ├── event_study.py generates lead-lag signals        │
│    ├── trade_flow_impulse.py generates flow bursts      │
│    ├── evaluate_tick_signal measures forward returns    │
│    ├── generate_random_baseline for noise floor         │
│    ├── evaluate_candidate_group gates against baseline  │
│    └── JSON/CSV/JSONL/MD → reports/trade_flow_impulse/  │
│                                                         │
│  [kraken_btcusd_research]                               │
│    ├── download_kraken_ohlcv.py → CSV files             │
│    ├── OHLCV CSV → ParquetDataCatalog                   │
│    ├── Strategy subclass processes Bars                 │
│    ├── BacktestEngine replays catalog                   │
│    ├── run_live_kraken_guarded.py (guarded: env+flag)   │
│    └── BacktestResult → reports/*.json                  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Key Architectural Patterns
- **Observer-first:** Three of four projects are purely observational. They measure markets, place hypotheses, collect data — but never submit orders.
- **Guarded live mode:** Only `kraken_btcusd_research/live_kraken_guarded.py` can trade, and it requires three independent gates: `--live` flag, `I_UNDERSTAND_THIS_CAN_LOSE_MONEY=yes` env var, and API key env vars.
- **Rejected iterations as code comments:** Each project documents its rejected versions inline (V1-V3 rejected, V4 is final, trade-flow v1 rejected, etc.).
- **Per-venue symbol normalization:** Every project handles venue-specific naming conventions (XBTUSD vs BTC-USD vs BTCUSDT).
- **Reporter pattern:** Each project writes JSONL event streams and summary.json into its own `reports/` directory.

---

## 3. Startup & Bootstrap

### kraken_btcusd_research

**No global startup.** Each entry point is a standalone script run from CLI:

- `run_backtest.py`: Loads `ParquetDataCatalog` from disk, creates `KrakenBTCUSDResearchConfig`, wires a `BacktestEngine` with Kraken venue config (BookType.L2_MBP, OmsType.NETTING, FeeModel models), adds the strategy, runs, dumps `BacktestResult` to pickle.
- `run_v4_research.py`: Same pattern but loads 15m catalog bars and aggregates them to 1h bars in memory (`aggregate_bars_to_1h`). Loops across date windows (2024h1 through 2026). Uses `KrakenBTCUSDV4TrendStrategy` with EMA(50/200), Donchian(100), ATR(20) trailing stop.
- `run_live_kraken_guarded.py`: Validates live guard conditions first. If paper mode, runs backtest. If live mode, defers heavy Nautilus/Kraken live imports until guard passes. Requires `KRAKEN_API_KEY` and `KRAKEN_API_SECRET`.

### venue_agnostic_signal_observer

**Three independent entry points:**

- `run_signal_observer.py`: Legacy bar-based observer. Loads OHLCV CSVs, runs `CrossMarketSignalGenerator`, measures forward returns.
- `run_tick_lead_lag.py`: Tick-level sweep. Loads JSONL trade ticks via `tick_store.py`, runs `TickLeadLagGenerator`, measures forward returns via `evaluate_tick_signal`, gates via `evaluate_candidate_group`.
- `run_trade_flow_impulse.py`: Trade-flow impulse sweep. Loads JSONL trade ticks, runs `TradeFlowImpulseSignalGenerator` (4 signal types), reuses the same `evaluate_tick_signal` and `evaluate_candidate_group` from `event_study.py`.
- `run_tick_capture.py`: Real-time WS collector. Connects to public Kraken/Coinbase trade feeds, writes `TradeTickLite` objects to JSONL files incrementally.

### kraken_l2_maker_paper

**Single entry point:** `run_v7_l2_maker_paper.py`

1. Parse CLI args (symbols, duration, fill-model, etc.)
2. Build `V7Config`
3. Create `KrakenBookWS` client connecting to `wss://ws.kraken.com/v2`
4. Create `MakerPaperSimulator` with shared book dict
5. `asyncio.run()` main loop:
   - Subscribe to book channels
   - On each WS message: parse → update `OrderBook` → `process_book_update` → check fills → place new quotes
   - Run until `duration_seconds` expires

### kraken_market_structure_scanner

**Three independent entry points:**

- `run_v6_scanner.py`: Creates `Scanner` + `FeeConfig`, enters polling loop (fetch tickers → compute opportunities → write JSONL) for `duration_seconds`.
- `run_v6_funding_basis.py`: Creates `FundingConfig`, enters `run_funding_scan` loop: fetch spot + perp tickers → compute funding rate → build `FundingObservation` → write JSONL.
- `run_v6_alt_funding_monitor.py`: Creates `FundingConfig`, uses `AltFundingScanner` for 10 altcoins across Binance/Bybit perps. Adds persistence tracking (`CandidateState` — requires N consecutive candidate polls to promote to durable).

---

## 4. Core Flows — Project-Specific

### 4.1 Route/Endpoint Inventory

These are not web applications with HTTP routes. Instead, each project consumes external REST/WS endpoints and emits local files. Below is the external API surface each project touches:

**kraken_market_structure_scanner (venues.py + funding_venues.py):**

| External Endpoint | Method | Purpose | Auth |
|---|---|---|---|
| `https://api.kraken.com/0/public/OHLC` | GET | Kraken OHLCV | None |
| `https://api.kraken.com/0/public/Ticker` | GET | Kraken spot ticker | None |
| `https://futures.kraken.com/derivatives/api/v3/tickers` | GET | Kraken perps tickers | None |
| `https://futures.kraken.com/derivatives/api/v3/historical-funding-rates` | GET | Kraken settled funding | None |
| `https://api.binance.com/api/v3/ticker/bookTicker` | GET | Binance best bid/ask | None |
| `https://fapi.binance.com/fapi/v1/premiumIndex` | GET | Binance perp mark+index+rate | None |
| `https://fapi.binance.com/fapi/v1/fundingRate` | GET | Binance funding history | None |
| `https://api.bybit.com/v5/market/tickers` | GET | Bybit spot+perp tickers | None |
| `https://api.bybit.com/v5/market/funding/history` | GET | Bybit funding history | None |
| `wss://ws.kraken.com/v2` | WS | Kraken L2 book stream | None |

**venue_agnostic_signal_observer (data_adapters.py + run_tick_capture.py):**

| External Endpoint | Method/Protocol | Purpose | Auth |
|---|---|---|---|
| `https://api.kraken.com/0/public/Trades` | GET | Kraken historical trades | None |
| `https://api.coinbase.com/api/v3/brokerage/products` | GET | Coinbase product info | None |
| `wss://ws-feed.exchange.coinbase.com` | WS | Coinbase trade stream | None |
| `wss://ws.kraken.com/v2` | WS | Kraken trade stream | None |

**kraken_btcusd_research (download_kraken_ohlcv.py):**

| External Endpoint | Method | Purpose | Auth |
|---|---|---|---|
| `https://api.kraken.com/0/public/OHLC` | GET | Historical OHLCV download | None |

### 4.2 Signal/Strategy Execution Flow

**kraken_btcusd_research — strategy.py (V1-V3, 5min bars):**
1. On bar received: update EMA(20), EMA(100), Donchian(55), ATR(20)
2. Check entry: price breaks Donchian high, EMA cross confirmation, ATR filter
3. Calculate position size based on ATR-based stop distance + risk per trade (0.25%)
4. Submit market order (taker) with calculated quantity
5. On fill: set trailing stop at 1.5x ATR from highest high
6. On stop hit: close position
7. Cooldown: skip entry for 12 bars after a signal

**kraken_btcusd_research — strategy_v4.py (1h bars, FINAL attempt):**
1. On bar received: update EMA(50), EMA(200), Donchian(100), ATR(20)
2. Entry conditions (ALL must pass):
   - EMA(50) > EMA(200) — trend filter
   - Close > Donchian(100) high — breakout
   - ATR > rolling median ATR(100) — expansion filter
3. Long-only, spot, no leverage, no short
4. 0.25% equity risk per trade, 30% max notional
5. Exit: trailing stop at 4x ATR, or close below EMA(100)
6. Taker entry/exit

**venue_agnostic_signal_observer — signal flow:**
1. Load sorted `TradeTickLite` list from JSONL
2. `TickLeadLagGenerator`: slide lookback window across source venue ticks, compute price move (first-to-last), emit `TickSignalEvent` when `|move_bps| >= threshold` (with cooldown)
3. `TradeFlowImpulseSignalGenerator`: walk source venue ticks evaluating 4 signal types per window:
   - `count_burst`: trade count in lookback > rolling median × multiplier
   - `notional_burst`: notional sum in lookback > rolling median × multiplier
   - `large_trade`: single trade notional >= minimum or > rolling median × multiplier
   - `signed_imbalance`: (buy − sell notional) / total exceeds threshold
4. For each signal, `evaluate_tick_signal` measures target venue forward returns at multiple horizons using bisect timestamp lookup
5. Random baseline generated by placing random timestamps in the same source-data temporal range
6. `evaluate_candidate_group` applies 6 gates (event count, mean positive, median, win rate vs baseline, beats baseline margin, not single-event driven)
7. Verdict: NEEDS_MORE_DATA | REJECTED | CANDIDATE_FOR_LONGER_OBSERVATION

**kraken_l2_maker_paper — quote simulation flow:**
1. Connect to Kraken WS v2, subscribe to book channels
2. Parse messages: snapshots build fresh `OrderBook`, updates patch bids/asks
3. On each book update:
   - Check staleness and crossed books
   - Check existing paper quotes for fill eligibility via `PaperFillModel`
   - If fills or cancellations: retire quotes, record events
   - Place new quotes if none active (limit: 4 concurrent)
4. Quote placement rules: offset from best bid/ask, spread check, book imbalance check
5. Adverse selection tracking: measure mid-price movement after fill at 1s, 5s, 30s, 60s windows

**kraken_market_structure_scanner — polling loop:**
1. On each poll (every N seconds):
   - Fetch tickers from all configured venues for all symbols
   - Normalize to `Ticker` dataclass (venue, symbol, quote, bid, ask, ts_recv_ms)
   - For each symbol, cross-compare all venue pairs
   - Calculate arbitrage: `buy_ask` on venue A, `sell_bid` on venue B
   - Subtract fees (fee_buy + fee_sell + latency_buffer) → `net_edge_bps`
   - If net > threshold: log as opportunity
2. Funding scanner (V6-B): fetch spot bid/ask + perp bid/ask + funding rate → compute basis bps, funding APR, estimated costs, net edge
3. Altcoin monitor (V6-C): same as V6-B but for 10 altcoins, with 3 cost scenarios (conservative/mixed/optimistic) and persistence tracking

---

## 5. Platform/Service Inventory

| Platform | Projects Using It | Transport | Auth | Notes |
|---|---|---|---|---|
| Kraken (spot) | btcusd_research, l2_maker_paper, signal_observer, scanner, funding | REST + WS v2 | None for public; keys for live | REST ticker rate-limited; OHLC `since` param unreliable |
| Kraken (futures/perps) | scanner (funding), funding_scanner, funding_scanner_alt | REST | None | Historical funding endpoint available |
| Coinbase (spot) | signal_observer, scanner | REST + WS | None for public | WebSocket product_id uses hyphens (BTC-USD) |
| Binance (spot + perps) | scanner, funding_scanner, funding_scanner_alt | REST | None for public | FAPI endpoint for perp data; bookTicker for spot |
| Bybit (perps only) | funding_scanner_alt, alt_funding_monitor | REST v5 | None for public | Public funding history endpoint |

Known limitations:
- Kraken REST `/public/OHLC` does not respect `since` parameter consistently — memory notes this
- Venue symbol conventions differ widely: Kraken uses XBT for BTC, `XBTUSD` for REST; Coinbase uses `BTC-USD`; Binance uses `BTCUSDT`
- USD and USDT are NOT interchangeable — the scanner enforces quote currency matching and adds a mismatch buffer when they differ
- All external calls use `requests` with `timeout=5`, no retry logic in most scanners (funding_venues has no retry)

---

## 6. Shared Utilities

### kraken_btcusd_research
- `config.py` / `config_v4.py` — Constant-only (no runtime logic). Fee models, indicator periods, risk parameters, safety limits. Note: `config.py` has a `Final` import without importing `typing.Final` — this is a latent bug (the file references `Final[float]` without importing it from `typing`).
- `reports.py` — Loads pickled `BacktestResult`, extracts PnL/stats, generates CSV summaries and JSON outputs. Uses `pickle` which couples it to the specific Nautilus version that produced the pickle.
- `download_kraken_ohlcv.py` — Simple REST paginated downloader. Respects `MAX_RESULTS_PER_REQUEST=1000`, `REQUEST_DELAY=2s`, outputs CSV.
- `debug_v4.py` — Debug utility: runs bar-by-bar through historical 1h bars, counts how many bars pass each entry condition. Not a test, not a runner.

### venue_agnostic_signal_observer
- `symbol_aliases.py` — `resolve_symbol(symbol) -> SymbolSpec` with `asset`, `quote`, `canonical` fields. Canonicalizes `XBT/USD` → `BTC/USD`. No silent substitution — raises `ValueError` on unknown symbols.
- `tick_store.py` — `load_trades_jsonl`, `save_trades_jsonl`, `sort_ticks`, `dedup_ticks`, `tick_file_discovery`. Handles nanosecond timestamps, string-to-int safety on ts_event.
- `lead_lag.py` — `LeadLagSignalGenerator` (bar-level), `generate_random_baseline` (bar-level). Earlier version; superseded by `event_study.py` for tick-level.
- `event_study.py` — `TickLeadLagGenerator`, `evaluate_tick_signal`, `generate_random_baseline`, `evaluate_candidate_group`, `generate_random_baseline`, `evaluate_candidate_group`. The core event study engine reused by `run_tick_lead_lag.py` and `run_trade_flow_impulse.py`.
- `trade_flow_impulse.py` — `TradeFlowImpulseSignalGenerator` with 4 signal types. Uses `_infer_tick_rule_side` (Lee-Ready proxy) for trades without side data.
- `run_tick_capture.py` — Async WS collector. Connects to Kraken/Coinbase public trade feeds, writes JSONL incrementally. Bounded reconnect loop.

### kraken_l2_maker_paper
- `book_models.py` — `OrderBook` with computed properties: `best_bid`, `best_ask`, `midprice`, `spread_bps`, `imbalance` (top-10 volume), `is_crossed`, `is_stale`.
- `paper_quote.py` — `QuoteEngine` manages quote lifecycle. Max 4 concurrent quotes. Cancellation triggers: mid-move, spread-collapse, imbalance-flip, lifetime-exceeded.
- `paper_fill_model.py` — Three fill models: pessimistic requires book-crossing (not touch), neutral allows price-touch, optimistic fills on trade-price match. Min 0.5s between placement and fill check.
- `kraken_ws.py` — `KrakenBookWS` handles WS connect, subscribe, parse, reconnect. Uses `aiohttp`. Parses both v2 JSON structure with dict-based book data and legacy array-based format.

### kraken_market_structure_scanner
- `venues.py` — `Ticker` dataclass + fetchers for Kraken and Binance REST. `FETCHERS` dict maps venue name to function.
- `opportunity.py` — `calculate_opportunity(t1, t2, fees)` returns `Opportunity` dataclass or `None`. Rejects if quote currencies differ or if any bid/ask is None/zero.
- `funding_venues.py` — REST adapters for all 5 venue endpoints. No retry logic. Returns `None` on any error (swallows exceptions).
- `funding_scanner.py` / `funding_scanner_alt.py` — Polling loops for V6-B and V6-C. Alt version adds `PersistenceTracker` and `CandidateState` for consecutive-poll validation.

---

## 7. Persistence & Schema

**No database.** All persistence is file-based:

| Project | Storage Type | Path Pattern |
|---|---|---|
| btcusd_research | ParquetDataCatalog | `data/catalog/kraken_btcusd*/` |
| btcusd_research | Pickled BacktestResult | `data/results_backtest_v*/` |
| btcusd_research | CSV (downloaded OHLCV) | `data/kraken/BTCUSD_*.csv` |
| signal_observer | JSONL (tick captures) | `data/signal_observer_ticks_v*/trades_*.jsonl` |
| signal_observer | JSONL (historical trades) | `data/signal_observer_ticks/trades_*.jsonl` |
| signal_observer | JSON output | `reports/signal_observer_*/tick_summary.json` |
| signal_observer | CSV output | `reports/signal_observer_*/tick_summary.csv` |
| signal_observer | Markdown report | `reports/signal_observer_*/tick_lead_lag_report.md` |
| l2_maker_paper | JSONL events | `reports/v7_l2_maker_paper/events.jsonl` |
| l2_maker_paper | JSON summary | `reports/v7_l2_maker_paper/summary.json` |
| scanner | JSONL observations | `reports/v6_market_structure/observations_*.jsonl` |
| scanner | JSON summary | `reports/v6_market_structure/funding_basis_summary.json` |

No migrations, no schemas, no DB bootstrap. All file formats are self-describing (JSONL with known keys).

---

## 8. Frontend Architecture

None of the projects have a frontend. All are CLI tools or background observers.

The scanner outputs are JSONL/JSON files meant for post-hoc analysis (likely by the `ai` agent or human reading files).

---

## 9. Background Jobs & Scheduled Tasks

No cron jobs, no schedulers. All projects use bounded-duration loops:

| Project | Loop Type | Duration | Trigger |
|---|---|---|---|
| scanner | Polling (2s interval) | `duration_seconds` (default 120s) | CLI start |
| funding_scanner | Polling (5s interval) | `duration_seconds` (default 120s) | CLI start |
| alt_funding_monitor | Polling (10s interval) | `duration_seconds` (default 600s) | CLI start |
| l2_maker_paper | WS event-driven | `duration_seconds` (default 120s) | CLI start |
| tick_capture | WS event-driven | `duration_seconds` (default 300s) | CLI start |

All loops are externally cancellable (time-based or interrupt-based). No infinite loops.

---

## 10. Middleware & Cross-Cutting Concerns

**Security posture:**
- No project hard-codes credentials
- The only project with API key support (`run_live_kraken_guarded.py`) has triple-gate validation: explicit `--live` flag, environment variable acknowledgment, and API key presence check
- All REST calls are to public endpoints (no auth required)
- `run_live_kraken_guarded.py` never prints secret values (keys are checked for emptiness only)
- Heavy Nautilus/Kraken live imports are deferred until live mode is actually entered (lazy import pattern)

**Fee/cost models:**
- Every project uses bps-based cost accounting
- Conservative defaults: 40bps taker fee on retail venues (Kraken, Coinbase without volume), 10bps on Binance
- Latency buffer: 10bps standard across projects
- Quote mismatch buffer: 5-20bps depending on project (USD vs USDT mismatch)
- Signal observer: 12 fee + 2 slippage + 5 quote mismatch = 19bps all-in
- Fill model (l2_maker_paper): additional fill penalty (2bps) for queue uncertainty

**No-order enforcement:**
- `venue_agnostic_signal_observer` and `l2_maker_paper` have zero imports from Nautilus trading/order modules
- Both projects explicitly document "no orders" in their docstrings
- Test suites include forbidden-code scanners that grep all `.py` files for `order_submit`, `place_order`, `api_key`, `secret_key` patterns

---

## 11. Type System & Contracts

These are Python projects with runtime dataclasses — no static type checking layer, no shared type definitions across projects.

**Key shared patterns across projects:**
- All use `dataclass` for models (Ticker, OrderBook, PaperQuote, FundingObservation, etc.)
- All use `Optional[float]` for nullable numeric fields
- All timestamps are milliseconds or nanoseconds (signal_observer uses ns, everything else uses ms)
- No formal interface/protocol definitions — duck typing

**Signal event contracts (signal_observer):**
- `TickSignalEvent` has 15 standard fields + `metadata` dict for extensibility
- `TickForwardReturn` has 14 fields including `valid`, `rejection_reason` for filtering
- `TradeTickLite` stores `side` as string (`"buy"`, `"sell"`, or `"unknown"`) — not an enum

**Funding observation contracts (scanner):**
- `FundingObservation` has 27 fields covering prices, basis, funding APR, cost estimates, net edge
- `FundingObservationAlt` (V6-C) adds three cost scenarios and persistence tracking

---

## 12. Configuration & Environment

**kraken_btcusd_research:**
| Variable | Purpose | Default |
|---|---|---|
| `--live` | Enable live trading | False (disabled) |
| `I_UNDERSTAND_THIS_CAN_LOSE_MONEY` | Safety acknowledgment env var | Not set |
| `KRAKEN_API_KEY` | Kraken API key for live mode | Not set |
| `KRAKEN_API_SECRET` | Kraken API secret for live mode | Not set |
| `--catalog` | Path to ParquetDataCatalog | Required |
| `--start` / `--end` | Backtest date window | Required |
| `--starting-balance` | Starting balance USD | 10,000 |

**venue_agnostic_signal_observer:**
| Variable (CLI) | Purpose | Default |
|---|---|---|
| `--ticks` | JSONL tick data directory | `data/signal_observer_ticks` |
| `--source-venues` | Source venue names | `kraken` |
| `--target-venues` | Target venue names | `coinbase` |
| `--symbols` | Trading symbols | `BTC/USD` |
| `--signal-types` | Signal types (trade_flow) | all 4 types |
| `--lookbacks-ms` | Lookback windows | 1s, 5s, 10s, 30s |
| `--horizons-ms` | Forward horizons | 1s-60s |
| `--fee-bps` | Fee cost | 12 |
| `--slippage-bps` | Slippage cost | 2 |
| `--min-events` | Gate threshold | 50 |

**kraken_l2_maker_paper:**
| Variable (CLI) | Purpose | Default |
|---|---|---|
| `--symbols` | Symbols to simulate | BTC/USD, ETH/USD |
| `--duration-seconds` | Observation duration | 120s |
| `--fill-model` | Fill strictness | pessimistic |
| `--maker-fee-bps` | Maker fee | 3.0 bps |
| `--quote-lifetime` | Max quote age | 5.0s |

No `.env` files, no `.env.example` templates. No secret file references.

---

## 13. CI/CD Pipeline

No CI/CD pipelines (no `.github/workflows/` found at the repository level). All testing is run locally via `uv run -m pytest`.

---

## 14. Security Posture

**Auth:** No project uses authentication except `run_live_kraken_guarded.py` which requires Kraken API credentials (passed via environment, not stored in code).

**Input validation:** Symbol normalization raises `ValueError` / `KeyError` on unknown symbols. No silent fallback to wrong symbols.

**Secret handling:** API keys are only ever read from environment variables. None are printed, logged, or stored.

**Prompt injection:** Not applicable — no LLM integration in the current codebase.

**Network safety:** All external API calls use `timeout=5` seconds. No infinite retry loops. WS clients have bounded reconnect attempts (`max_reconnect_attempts=5`, delay 2s).

**Live trading guard:** Triple-gate in `run_live_kraken_guarded.py` — the strongest safety pattern in the repo. Heavy trading imports are deferred to prevent accidental import-time side effects.

---

## 15. Known Patterns & Conventions

**Coding style:**
- Snake case for all identifiers
- Dataclasses for all models (no Pydantic)
- Type hints on all public functions (though not enforced at runtime)
- Docstrings on every module and public function
- Module-level constants in UPPER_CASE
- `from __future__ import annotations` in most files

**File organization:**
- Each project is self-contained under its own directory
- No cross-project imports (projects do not share code)
- Tests live in `tests/` subdirectory per project
- Reports go in `reports/` at repository level (not per-project)
- Data files live in `data/` at repository level

**Error taxonomy:**
- REST errors: return `None` (silent failure in scanners)
- WS errors: reconnect with bounded attempts
- Data errors: rejected evaluations with `rejection_reason` field
- Config errors: `ValueError` / `KeyError` with descriptive messages

**Naming conventions:**
- Strategy classes: `KrakenBTCUSDV4TrendStrategy`, `MakerPaperSimulator`, `Scanner`, `AltFundingScanner`
- Config classes: `StrategyConfig`, `V7Config`, `ScannerConfig`, `FundingConfig`
- Runner scripts: `run_<version>_<purpose>.py`

---

## 16. Dependency Map

| Dependency | Used By | Purpose | Notes |
|---|---|---|---|
| `nautilus_trader` | btcusd_research, signal_observer | Backtest engine, Strategy base, indicators, data models, ParquetDataCatalog | Rust-backed, requires Cargo |
| `requests` | scanner, funding_scanner, download_kraken_ohlcv | HTTP REST calls to public endpoints | No retries, timeout=5 |
| `aiohttp` | l2_maker_paper | WebSocket client for Kraken WS v2 | Optional import — graceful degradation |
| `uv` | all | Virtual environment + dependency management | Required version pin: ==0.11.8 (runtime is 0.11.13 — mismatch) |
| `pytest` | all | Test runner | 264 total tests, all passing |
| `pathlib` | all | File path handling | Used for all file I/O |
| `json` | all | Report serialization | JSONL line-per-object pattern |
| `dataclasses` | all | Model definitions | No Pydantic, no attrs |
| `bisect` | signal_observer | Efficient timestamp lookup in forward return evaluation | Critical for performance |
| `pickle` | btcusd_research/reports.py | Loading BacktestResult | Version-coupled — pickle of Nautilus internals |

---

## 17. Edge Cases & Operational Notes

1. **uv version mismatch:** `pyproject.toml` requires `uv==0.11.8` but the runtime is `0.11.13`. This causes `error: Required uv version ... does not match` on `uv run` commands. Workaround: use `source .venv/bin/activate && python -m pytest ...` directly.

2. **Kraken symbol naming:** Kraken uses `XBT` internally but the REST API returns `BTC` in some responses. The `symbol_aliases.py` module normalizes this, but the scanner's `venues.py` handles it differently (hard-coded `XBTUSD` mapping). Inconsistency risk if new symbols are added.

3. **Pickle coupling:** `reports.py` in btcusd_research loads `BacktestResult` via `pickle`. If the Nautilus version changes and the pickle format changes, loading will fail silently or crash.

4. **No retry logic:** Most REST fetchers (`kraken_spot_ticker`, `binance_perp_ticker`, etc.) catch exceptions and return `None`. No rate-limit handling, no exponential backoff. During market volatility or API outages, scanners may silently drop data.

5. **`finalize` in btcusd_research strategy.py:** The strategy has a `finalize()` method but the actual implementation is incomplete/inconsistent between `strategy.py` and `strategy_v4.py`. V4 has position closing in `finalize`, V1 has logging.

6. **`final` import bug in config.py:** `config.py` references `Final[float]` but only imports from `nautilus_trader.model.identifiers` etc. — `Final` from `typing` is not imported. This would cause a `NameError` if the constants were actually evaluated at module load time (they are, but only at import, and the actual usage may not trigger the error if the `Final` annotations are not runtime-evaluated... actually they are, since Python 3.11 evaluates them).

7. **Cross-project data sharing:** The `venue_agnostic_signal_observer` uses `data/signal_observer_ticks_v3/` for its trade-flow impulse study, but this was generated by the tick capture system. The l2_maker_paper uses completely different WS format. No shared tick data between projects.

8. **`__main__.py` entry point:** The `venue_agnostic_signal_observer` has a `__main__.py` that makes it runnable as `python -m examples.strategies.venue_agnostic_signal_observer`, but the other projects do not. Only `run_*.py` scripts for them.

---

## 18. Testing Posture

**Runner:** `pytest`
**Total tests:** 264 across 4 projects
**Status:** All passing (as of 2026-05-13)

### Test Breakdown

| Project | Test Files | Tests | Coverage Areas |
|---|---|---|---|
| kraken_btcusd_research | 12 test files | ~80 | Strategy logic, backtest smoke, instrument setup, live guard, position sizing, synthetic backtest, catalog import, reports smoke |
| venue_agnostic_signal_observer | 8 test files | 132 | Model serialization, fee/dir-adjusted returns, signal generation above/below threshold, cooldown, no-lookahead, random baseline, candidate gate, forward returns, synthetic fixtures, trade-flow config, tick-rule proxy, burst detection, signed imbalance, no-order guards |
| kraken_l2_maker_paper | 1 test file | ~4 | V7 strategy smoke test |
| kraken_market_structure_scanner | 2 test files | ~48 | Funding observations, scanner opportunity calculation, alt funding scanner, symbol mappings |

### Notable Gaps
- **kraken_l2_maker_paper:** Only 1 test file with smoke tests. No test coverage for `paper_fill_model.py` fill models, `QuoteEngine` lifecycle, `OrderBook` boundary conditions, or WS message parsing.
- **kraken_market_structure_scanner:** No test for `venues.py` REST adapters, no test for `opportunity.py` edge cases (zero prices, crossed bid/ask, missing ticker data), no test for `funding_scanner.py` error paths.
- **No integration tests:** No test runs a full observer → signal → forward-return → gate pipeline against real data.
- **No linter in CI:** No automated flake8/ruff checks on commit. Tests run manually.
- **No performance tests:** No test for large tick datasets (millions of ticks) to verify bisect-based lookups scale properly.

### Test Failures Discovered During Audit
None. All 264 tests pass cleanly.

---

## 19. Top Priorities / Recommendations

### HIGH

1. **Fix `config.py` import bug** in `kraken_btcusd_research`: `Final` is referenced but not imported from `typing`. This is a latent NameError that could surface in any future Python version change. Lines 22-23 of `config.py` use `Final[float]` without importing it.

2. **Fix uv version mismatch:** `pyproject.toml` pins `uv==0.11.8` but the environment has `0.11.13`. This breaks `uv run -m pytest` with an error message. Either update the pin in `pyproject.toml` or pin the uv version in the environment.

3. **Strengthen l2_maker_paper test coverage:** Only 1 test file for a complex async WS simulator. Add tests for `PaperFillModel` (pessimistic/neutral/optimistic logic), `QuoteEngine` (max concurrent quotes, cancellation triggers), and `OrderBook` (staleness detection, crossed book detection).

### MEDIUM

4. **Add retry logic to REST fetchers:** `venues.py` and `funding_venues.py` silently return `None` on any error (timeout, rate-limit, connection reset). Add bounded retries with exponential backoff for reliability. At minimum, distinguish between "no data" (return None) and "transient error" (raise/log).

5. **Decouple `pickle` usage in btcusd_research/reports.py:** Loading `BacktestResult` via pickle ties the report generator to a specific Nautilus version. Consider exporting summary stats to JSON during the backtest run, then reading JSON for report generation.

6. **Add `__main__.py` entry points to all projects:** `venue_agnostic_signal_observer` has one; the other three do not. Consistency would improve discoverability.

### LOW

7. **Consolidate symbol normalization:** Each project handles venue-specific symbol mapping differently (`symbol_aliases.py`, `venues.py` FETCHERS, `funding_venues.py`, `symbols.py`). A single shared normalization utility would reduce duplication and inconsistency risk.

8. **Document rejected strategies in a central table:** Currently, rejection verdicts are scattered across code comments (`config_v4.py` docstrings, `event_study.py` docstrings). A single STATUS.md listing all iterations (V1-V7 Kraken, lead-lag v1/v3, trade-flow v1) with verdicts would improve traceability.

9. **Add timing metrics to scanner polling:** The scanner logs opportunities but doesn't log per-poll latency or fetch duration. Adding timing info would help identify slow REST endpoints under load.
