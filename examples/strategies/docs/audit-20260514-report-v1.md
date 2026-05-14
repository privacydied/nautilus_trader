# PRODUCT AUDIT — NautilusTrader examples/strategies
## Date: 2026-05-14
## Version: v1

---

## 1. Executive Summary

This audit covers `/mnt/nasirjones/py/nautilus_trader/examples/strategies`, a self-contained strategy-research and market-data observation area inside the NautilusTrader repository. The code is not a single product in the web-app sense; it is a collection of research scaffolds for spot crypto strategy evaluation, cross-venue signal observation, public market-data capture, paper L2 maker modelling, and volatility/capture gating.

The core value proposition is disciplined trading-signal research without prematurely building live execution. Most subpackages are deliberately observer-only: they fetch public data, write local CSV/JSON/JSONL artifacts, compute forward returns after realistic cost assumptions, and generate candidate/rejection summaries. The only true Nautilus `Strategy` implementations are in `kraken_btcusd_research`, and even there the default posture is research/backtest; the live runner is intentionally guarded and incomplete rather than a hidden trading path.

The subtree is unusually test-rich for an examples area. The targeted test suite ran cleanly with 466 passing tests. Lint is not clean: the targeted ruff check reported 1,616 issues, mostly import order, docstring style, trailing whitespace, unused imports/variables, and a few complexity/security-audit findings. The more important product risks are not formatting: broken or stale backtest/import scripts in the original Kraken BTC/USD scaffold, unused config controls, simplistic paper-fill/PnL models, silent network exception handling, and several research-validity traps around quote mismatches, timestamp overlap, and public-feed schema drift.

---

## 2. Architecture Overview

### Deployment model

This is a local research scaffold, not a deployed service. The code is meant to be run from the NautilusTrader repository with Python 3.12-3.14, the local `.venv`, and NautilusTrader’s Rust/Cython extensions available. It interacts with public exchange REST/WebSocket APIs and writes local artifacts under `data/` and `reports/` style directories.

No server process, HTTP routing layer, database daemon, or SPA frontend exists in this subtree.

### Tech stack

- Language: Python.
- Parent project: NautilusTrader 1.227.0.
- Core runtime dependencies from `pyproject.toml`: click, fsspec, msgspec, numpy, pandas, portion, pyarrow, pytz, tqdm, uvloop.
- Test dependencies: pytest 7.4.x, pytest-asyncio, pytest-cov, pytest-xdist, aiohttp.
- Lint/type tools: ruff 0.15.12, mypy 1.20.2.
- Network clients used in the examples: `requests`, `urllib`, `httpx`, `aiohttp`, `websockets`.
- Persistence: local files only — CSV, JSON, JSONL, ParquetDataCatalog for Nautilus backtests.

### Directory structure

- `kraken_btcusd_research/`: Original Nautilus spot BTC/USD EMA/Donchian/ATR research strategy, data downloader/importer, backtest scripts, V4 1h trend variant, guarded live stub, and tests.
- `kraken_market_structure_scanner/`: V6 public REST scanners for spot cross-venue market structure and spot/perp funding-basis observations.
- `kraken_l2_maker_paper/`: V7 public Kraken L2 WebSocket capture and in-memory maker quote/fill paper simulator.
- `venue_agnostic_signal_observer/`: Largest subsystem; venue-agnostic bar/tick signal observation, cross-venue lead/lag, trade-flow impulse, DEX/CEX dislocation, derivatives-to-spot capture/evaluation, cross-asset impulse, report writing, tick storage, data downloaders, and regression tests.
- `volatility_gate.py`: Top-level public Kraken OHLC volatility/capture-permission gate.
- `research_report_miner.py`: Top-level report miner source utility.
- `test_volatility_gate*.py`: Top-level tests for the volatility gate.
- `kraken_v5_portfolio/`, `polymarket_btcusd_arb/`: Present in the directory inventory but not deeply audited here because the current source inventory focus and tests centred on the active Python strategy/observer subtrees listed above. Existing documentation was not used as source of truth.
- `docs/`: Destination for this generated audit report only.

### Data-flow diagram

```text
Public exchange REST/WS APIs
    |
    |-- OHLC/candle fetchers --> normalized CSV --> bar-level observers/backtests
    |-- trade WS captures ----> trades_*.jsonl --> tick lead/lag, trade-flow, derivatives studies
    |-- L2 book WS -----------> in-memory OrderBook --> paper QuoteEngine/FillModel --> JSON reports
    |-- DEX snapshots --------> dex snapshots JSONL --> DEX/CEX dislocation evaluator

Nautilus ParquetDataCatalog
    |-- bars + instruments --> BacktestEngine --> Strategy --> reports JSON/CSV

Observer/evaluator modules
    |-- SignalEvent / TickSignalEvent
    |-- forward-return measurement
    |-- fee/slippage/quote mismatch costs
    |-- rejection accounting
    |-- summary JSON/CSV/MD outputs
```

### Architectural patterns

- Research-first: observe, backtest, replay, and paper simulate before any execution.
- Public-data-only in most modules.
- Local artifact persistence instead of databases.
- Dataclasses for typed event/result contracts.
- CLI entrypoints with bounded runtime/duration flags.
- Tests include explicit no-order/no-secret scans in observer modules.
- Failure mode is often “record rejection reason” rather than throw.

---

## 3. Startup & Bootstrap

There is no global startup sequence. Each script is a separate CLI entrypoint.

Common bootstrap patterns:

1. Parse CLI args with `argparse`.
2. Build a config dataclass or config object.
3. Load data from CSV/JSONL/ParquetDataCatalog or connect to public REST/WS endpoints.
4. Run one bounded scan/evaluation/backtest/capture loop.
5. Write local artifacts.
6. Print a concise terminal summary/verdict.

Nautilus backtest scripts additionally:

1. Construct `BacktestEngine` with `BacktestEngineConfig` and `LoggingConfig`.
2. Add a venue as `NETTING`/`CASH` for spot-style assumptions.
3. Add instrument and bars.
4. Add a strategy object.
5. Run the engine over a date range.
6. Generate result/report artifacts.

Important bootstrap caveat: the repository `pyproject.toml` pins `uv` to `==0.11.8`. The environment had `uv 0.11.14`, so direct `uv run ...` failed. Tests/lint were run via `.venv/bin/python` and `.venv/bin/ruff`, which matched the installed environment.

---

## 4. Core Flows

### 4.1 `kraken_btcusd_research`: original Nautilus BTC/USD strategy research

#### Config modules

`config.py` defines the original 5-minute research constants:

- Venue/instrument: `KRAKEN`, `BTC/USD.KRAKEN`.
- Starting balance: 10,000 USD.
- Indicators: EMA fast 20, EMA slow 100, Donchian 55, ATR 20.
- Risk: 0.25% per trade, 30% max notional, 0.001 BTC min position, 12-bar cooldown.
- Fees: maker 0.25%, taker 0.40%.
- Timeframe: 5-minute bars.
- Live-limit constants exist (`MAX_ORDER_NOTIONAL_USD`, `MAX_DAILY_LOSS_USD`) but are not enforced by the strategy or live stub.

`config_v4.py` defines the V4 1-hour trend variant:

- EMA 50/200 trend, regime EMA 100, Donchian 100, ATR 20.
- ATR expansion window 100.
- Trailing stop 4x ATR.
- 6-bar cooldown.
- Same conservative spot-risk posture.

#### Strategy flow: `strategy.py`

Important classes/functions:

- `KrakenBTCUSDResearchConfig`
- `KrakenBTCUSDResearchStrategy`
- `_calculate_position_size(...)`
- `create_strategy()`

Runtime behavior:

1. `on_start()` subscribes to the configured `BarType`.
2. `on_bar()` increments `bars_processed`.
3. During warmup it updates EMA/Donchian/ATR and returns.
4. After warmup, it checks exits and entries before updating indicators, which avoids the classic Donchian bug where updating with the current bar makes `close > current Donchian high` impossible.
5. Entry requires:
   - fast EMA > slow EMA;
   - current close > prior Donchian upper;
   - ATR > 0;
   - account value available;
   - `_calculate_position_size` returns at least min size under risk/notional limits.
6. Entry submits a market BUY with GTC time-in-force.
7. Exit requires either:
   - current price below slow EMA; or
   - current price below computed ATR stop.
8. Exit submits a market SELL for tracked current position quantity.
9. `on_order_filled()` manually updates `current_position_size`, `entry_price`, `entry_bar`, and `highest_high_since_entry`.

Risk and correctness notes:

- `_calculate_position_size` takes `fee_rate` but never uses it.
- Stop logic comment says “tighter” stop, but implementation uses `min(initial_stop, trailing_stop)`. For a long position, the tighter protective stop is usually the higher stop (`max`), so current behavior can be looser than intended.
- Strategy state is manually tracked from fills rather than authoritative portfolio position state. That is fragile for partial fills, rejected orders, restarts, or live reconciliation.
- `get_account_value()` silently falls back to `STARTING_BALANCE_USD` if account lookup fails; useful for tests, risky for live-like operation.

#### Strategy flow: `strategy_v4.py`

Important classes/functions:

- `KrakenBTCUSDV4TrendConfig`
- `KrakenBTCUSDV4TrendStrategy`
- `_calculate_position_size(...)`

Runtime behavior:

1. Subscribes to configured bars.
2. Maintains EMA50, EMA200, EMA100 regime filter, Donchian100, ATR20, and rolling ATR history.
3. Uses prior Donchian high before indicator update for breakout logic.
4. Entry requires EMA50 > EMA200, close > prior Donchian high, ATR expansion, risk/notional/min-size constraints.
5. Exit uses 4x ATR trailing stop or close below EMA100.
6. Uses market orders and manual fill bookkeeping.

Risks:

- Fee rate accepted but unused in sizing.
- Float state and `Quantity(self._position_qty, 8)` precision can drift.
- Manual fill-derived state can desync.
- ATR median can become active after as few as 10 values even though config names a 100-window expansion regime.

#### Backtest/data scripts

`download_kraken_ohlcv.py`:

- Fetches Kraken public REST OHLC data.
- CLI args: pair, interval, since, until, output.
- Writes CSV with timestamp/open/high/low/close/vwap/volume/count.
- Sleeps between paginated requests.
- Risks: possible duplicate last candle pagination; no dedup/sort before write.

`import_kraken_ohlcv_to_catalog.py`:

- Converts OHLC CSV into Nautilus `Bar` objects and writes a `ParquetDataCatalog`.
- Appears broken/stale in source: missing `ParquetDataCatalog` import, missing `Decimal` import, and names imported inside `csv_to_bars()` are referenced in `write_to_catalog()` module scope.

`run_backtest.py`:

- Intended to load a ParquetDataCatalog, create a cash BacktestEngine, add the strategy, run, pickle result, and generate reports.
- Appears broken/stale:
  - calls `sys.exit(main())` without importing `sys`;
  - uses `backtest_result_path` before assignment;
  - passes `starting_balances=[args.starting_balance]` instead of `Money(...)`;
  - uses `BarType(InstrumentId, 5)` shape inconsistent with current Nautilus BarSpecification usage;
  - uses `engine.run_result` assumptions that may not match current API.

`run_v4_research.py`:

- Runs V4 1h trend research across named windows.
- Loads catalogs from `data/catalog/kraken_btcusd_15m_*`, falling back to `kraken_btcusd_*`.
- Aggregates every four bars into a 1h bar, runs BacktestEngine, extracts stats/reports, writes under `reports/baseline_v4_1h_trend`.
- Risk: if fallback data is 5-minute bars, grouping four bars produces 20-minute bars mislabeled as 1h. Aggregation is sequential, not wall-clock aligned.

`run_live_kraken_guarded.py`:

- Guarded live stub, not a complete live runner.
- Requires `--live`, `I_UNDERSTAND_THIS_CAN_LOSE_MONEY=yes`, `KRAKEN_API_KEY`, and `KRAKEN_API_SECRET`.
- Defers heavy Nautilus imports until after guard checks.
- Does not print secrets.
- It stops at instrument construction and says full Nautilus live setup is required.
- Parsed `--catalog` and `--starting-balance` are effectively unused.

#### Report generation

`reports.py` writes:

- `backtest_summary.json`
- `trades.csv`
- `equity_curve.csv`

Risks:

- `logger.warning` is used in one path without a defined logger.
- Empty equity curve handling can index `[0]` without guard.
- If real trades/equity are not supplied, placeholder/minimal outputs can look more complete than they are.
- PnL/commission string parsing is brittle.

---

### 4.2 `kraken_market_structure_scanner`: V6 public REST market/funding scanners

#### Spot market structure scanner

Key files:

- `config.py`
- `symbols.py`
- `venues.py`
- `opportunity.py`
- `scanner.py`
- `run_v6_scanner.py`

Flow:

1. `ScannerConfig` defines symbols, venues, poll interval, min edge, latency buffer, and output dir.
2. `symbols.py` maps human symbols to venue API symbols.
3. `venues.py` fetches public top-of-book tickers from Kraken and Binance.
4. `calculate_opportunity()` compares venue pairs, computes gross edge, fees, latency buffer, net edge, and quote mismatch.
5. `Scanner.run()` polls tickers until deadline, writes each computed opportunity row to `opportunities.jsonl`, then writes `summary.json`.

Important issues:

- Default config/CLI mentions Coinbase, but `FETCHERS` has only Kraken and Binance.
- `ScannerConfig.min_net_edge_bps` is not used to filter opportunities.
- BTC/USD and ETH/USD mappings can use Binance USDT symbols while preserving quote `USD`, creating implicit USD/USDT equivalence.
- All REST fetch failures become `None` and are mostly counted, not diagnosed.
- Output files are opened with `w`, overwriting previous runs.

#### Funding/basis scanners

Key files:

- `funding_config.py`
- `funding_models.py`
- `funding_venues.py`
- `funding_scanner.py`
- `funding_reports.py`
- `funding_models_alt.py`
- `funding_scanner_alt.py`
- `funding_reports_alt.py`
- `run_v6_funding_basis.py`
- `run_v6_alt_funding_monitor.py`

Flow:

1. Fetch Kraken spot top-of-book.
2. Fetch Binance/Bybit/Kraken perp ticker and funding where configured.
3. Compute spot/perp basis, expected funding bps, APR, fees/buffers, quote mismatch, and net edge.
4. Reject candidates when funding is non-positive, quote mismatch exists, APR is too low, or conservative net edge is too low.
5. Alt scanner computes three cost scenarios: conservative taker, mixed maker/taker, optimistic maker.
6. Alt scanner tracks persistence across consecutive polls with `PersistenceTracker` and writes durable candidates separately.

External endpoints:

- Kraken spot ticker: `https://api.kraken.com/0/public/Ticker`
- Kraken futures tickers/funding: `https://futures.kraken.com/derivatives/api/v3/...`
- Binance futures funding/ticker: `https://fapi.binance.com/fapi/v1/...`
- Bybit V5 market endpoints: `https://api.bybit.com/v5/market/...`

Risks:

- Kraken funding timestamp parsing likely fails and returns an empty list due swallowed exception.
- USD/USDT mismatch blocks Binance/Bybit candidates when spot is Kraken USD, which may be intentional conservative behavior but sharply limits candidate discovery.
- Staleness fields exist but exchange timestamps are often `None`, making freshness less binding.
- Alt scanner imports `CandidateState` but does not use it in scanner logic.
- `max_conservative_net` summary field is not populated in stats.

Safety posture: observer only; public REST only; no credentials or order paths.

---

### 4.3 `kraken_l2_maker_paper`: V7 Kraken public L2 maker paper simulator

Key files:

- `config.py`
- `symbols.py`
- `book_models.py`
- `kraken_ws.py`
- `paper_quote.py`
- `paper_fill_model.py`
- `simulator.py`
- `reports.py`
- `run_v7_l2_maker_paper.py`

Flow:

1. `V7Config` sets symbols, duration, Kraken WS URL, quote side, quote lifetime, cancellation thresholds, fill model, maker fee, and output dir.
2. `KrakenBookWS` connects to `wss://ws.kraken.com/v2` using public book/trade subscriptions.
3. Book messages update local `OrderBook` objects.
4. `MakerPaperSimulator.process_book_update()` creates a `BookSnapshot`, rejects stale/crossed books, checks paper fills, places initial quotes, checks cancellations, and replaces cancelled quotes.
5. `QuoteEngine` places in-memory paper quotes and cancels for lifetime, stale book, mid move, spread collapse, or imbalance flip.
6. `PaperFillModel` simulates fills. It disallows same-tick fills by requiring at least 0.5s after quote timestamp. Pessimistic/neutral fill on touch/cross. Optimistic currently does not really fill.
7. Reports are written to `events.jsonl` and `summary.json`.

Important risks:

- `MakerPaperSimulator.run()` creates a `KrakenBookWS` but does not share `ws_client.books` with `self.books`; the wrapper `run_v7_l2_maker_paper.py` manually sets `sim.books = ws.books`, avoiding this only in that path.
- No Kraken checksum or sequence validation.
- Snapshot/update detection uses `len(bids) >= 10` heuristic rather than explicit message type.
- `quote_offset_bps`, `max_cancel_rate`, and `min_spread_bps` are configured but effectively unused in quote placement/cancellation.
- Neutral and pessimistic fill modes are effectively identical; optimistic is disabled/empty.
- PnL model is simplified: half-spread gross per fill minus maker fee/penalty, no inventory pairing or mark-to-market.

Safety posture: public WebSocket only, no auth, no order submission.

---

### 4.4 `venue_agnostic_signal_observer`: bar/tick research platform

This is the most product-like subsystem in the target directory. It provides reusable event/result contracts, data loaders, public capture scripts, multiple signal generators, forward-return evaluation, rejection accounting, and report writers.

#### Core dataclasses/contracts

`models.py`:

- `SignalEvent`: bar-level signal with source/target venue/instrument, direction, strength, metadata, reason.
- `ForwardReturnResult`: per-signal/per-horizon measurement, raw/direction-adjusted/net returns, fees, slippage, quote mismatch, excursions, valid/rejection reason.
- `HorizonSummary`, `SignalTypeSummary`, `SignalEvaluationSummary`.

`tick_models.py`:

- `TradeTickLite`: normalized trade tick with nanosecond timestamp, venue, symbol, price, size, side, trade id, raw payload.
- `QuoteTickLite`: normalized quote tick with bid/ask, mid and spread properties; rejects invalid bid/ask.
- `TickSignalEvent`: tick-level signal record.
- `TickForwardReturn`: tick-level forward-return measurement.

`config.py`:

- `Horizon`
- `FeeModel`
- `SignalSourceConfig`
- `ObserverConfig`
- `LeadLagConfig`

`FeeModel.total_cost_bps()` combines fee, slippage, and optional quote mismatch buffer.

#### Bar-level observer flow

Key files:

- `observer.py`
- `signals.py`
- `forward_returns.py`
- `reports.py`
- `run_signal_observer.py`
- `run_lead_lag.py`

Flow:

1. Load signals from direct objects, CSV, or cross-market generator.
2. Load target prices from direct arrays or OHLC CSV.
3. Determine whether quote mismatch buffer applies.
4. For every signal/horizon, find entry price at or after signal timestamp.
5. Find forward price at or after horizon timestamp.
6. Reject missing/zero/non-finite prices with explicit reasons.
7. Compute raw, direction-adjusted, and net returns after costs.
8. Compute favorable/adverse excursions.
9. Aggregate by horizon and signal type.
10. Write `signal_events.jsonl`, `forward_returns.jsonl`, `summary.json`, `summary.csv`, `by_signal_type.csv`, and `rejections.json`.

Notable correctness points:

- Non-finite and zero prices are aggressively rejected.
- Summary aggregation filters non-finite values before statistics.
- Forward return records distinguish raw price move from direction-adjusted return.

#### Public data adapters

`data_adapters.py`:

- Binance spot klines.
- Kraken OHLC.
- Coinbase candles.
- CSV load/save and venue alignment.

`data_fetcher.py`:

- Older/parallel public OHLC fetchers using `httpx`.

`data_loading.py`, `csv_normalizer.py`:

- CSV bar/signal loading, synthetic data generation, resampling/alignment.

`tick_store.py`:

- JSONL trade/quote load/save, dedup, sorting, stale tick rejection, merge, and filename discovery.

#### Tick capture and tick-level lead/lag

`run_tick_capture.py`:

- Public unauthenticated WebSocket trade capture.
- Venues: Binance spot, Kraken, Coinbase.
- Writes per-stream `trades_*.jsonl` incrementally and a manifest.
- Uses no credentials and no execution calls.

`run_tick_lead_lag.py`, `event_study.py`:

- Generate cross-venue tick lead/lag signals.
- Evaluate forward returns across millisecond horizons.
- Support random baselines and minimum-event gates.

Risks:

- Binance spot stream symbol normalization can map `BTC/USD` to `btcusd@trade`; Binance spot commonly uses `BTCUSDT` for liquid crypto pairs, so user-supplied quote handling matters.
- Public WebSocket feeds are not exchange-time synchronized; receive latency and timestamp semantics can bias lead/lag results.

#### Trade-flow impulse

`trade_flow_impulse.py` and `run_trade_flow_impulse.py`:

- Signal types: count burst, notional burst, large trade, signed imbalance.
- Optional tick-rule aggressor-side proxy.
- Emits observational tick signal events, never orders.
- Risk: trade side inference can be wrong when venue side is unknown; some generator functions are complex and lint flags unused locals/imports.

#### DEX/CEX dislocation

`dex_adapters.py`, `dex_models.py`, `dex_cex_dislocation.py`, `collect_dex_snapshots.py`, `run_dex_cex_dislocation.py`:

- Fetch or load DEX Screener / GeckoTerminal style pool snapshots.
- Detect DEX price shock, volume burst, liquidity shock events.
- Convert DEX events into generic tick signals and evaluate CEX forward returns.
- Risks: DEX APIs and liquidity snapshots can be noisy; candidate status is observational only.

#### Derivatives-to-spot research

`derivatives_models.py`, `derivatives_lead_lag.py`, `run_derivatives_spot_capture.py`, `run_derivatives_spot_lead_lag.py`, `run_derivatives_lead_lag.py`:

- Captures Binance USD-M perp trades plus Kraken/Coinbase spot trades in one event loop.
- Optional Binance open-interest polling.
- Important endpoint constants:
  - `BINANCE_PERP_WS_BASE = wss://fstream.binance.com/market/stream`
  - `KRAKEN_WS_URL = wss://ws.kraken.com/v2`
- Uses bounded reconnect loops, diagnostics, and session cleanup.
- Evaluates derivatives-source impulses into spot-target forward returns.

Risk posture:

- Derivatives are used as signal sources, not execution targets.
- This is important for UK retail constraints: execution research should not assume crypto perps/futures/options as primary PnL instruments.

#### Cross-asset impulse

`cross_asset_impulse.py`, `run_cross_asset_impulse.py`:

- Generates source impulses from one asset and evaluates target assets.
- Tracks stream health, overlap, price range sufficiency, same-symbol skips, candidate/rejected verdicts.
- Writes JSONL/JSON/CSV/markdown report bundle.

---

### 4.5 Top-level `volatility_gate.py`

Purpose: decide whether market conditions justify running more expensive live capture windows.

Flow:

1. Fetch Kraken OHLC for BTC/USD, ETH/USD, SOL/USD, DOGE/USD.
2. Compute 3h range and current/previous 1h range.
3. Classify market as quiet/moderate/active/alt-active.
4. Compute BTC/ETH 1-minute fast diagnostics over 15m/30m/60m.
5. Compute candle freshness and next useful re-check time.
6. Emit JSON with:
   - values by symbol;
   - market verdict;
   - acceleration verdict;
   - freshness;
   - fast diagnostic;
   - `fast_capture_eligible`;
   - `capture_permission` (`FULL_ACTIVE_CAPTURE`, `FAST_DIAGNOSTIC_CAPTURE_ONLY`, or `NO_CAPTURE`).

Safety: public Kraken OHLC only; tests assert no order/api-key patterns.

---

## 5. Platform / Service Inventory

| Service | Used by | Transport | Auth | Purpose | Notes |
|---|---|---|---|---|---|
| Kraken Spot REST | OHLC downloaders, volatility gate, scanners | HTTPS REST | None | OHLC and ticker data | Public endpoint; failures often handled conservatively. |
| Kraken WS v1 | `run_tick_capture.py` | WebSocket | None | Spot trade ticks | Older endpoint in generic tick capture. |
| Kraken WS v2 | L2 paper simulator, derivatives spot capture | WebSocket | None | Book/trade stream | Correct v2 endpoint needed for dict-format subscriptions. |
| Kraken Futures REST | funding scanners | HTTPS REST | None | Futures ticker/funding | Timestamp parsing risk in funding helper. |
| Binance Spot REST | data adapters/scanner | HTTPS REST | None | Klines/book ticker | USD/USDT symbol mapping must be explicit. |
| Binance Spot WS | `run_tick_capture.py` | WebSocket | None | Spot trade ticks | Symbol normalization can be a pitfall. |
| Binance USD-M Futures REST | derivatives capture/funding scanner | HTTPS REST | None | Open interest, funding, book ticker | Derivatives are signal sources only. |
| Binance USD-M Futures WS | derivatives capture | WebSocket | None | Perp trade stream | Uses `/market/stream`, not bare `/stream`. |
| Coinbase Exchange REST | data adapters | HTTPS REST | None | Candles | Public endpoint. |
| Coinbase Exchange WS | tick capture | WebSocket | None | Spot trades | Public matches feed. |
| Bybit V5 REST | funding scanners | HTTPS REST | None | Funding/tickers | Public endpoint. |
| DEX Screener | DEX adapters | HTTPS REST | None | DEX pool snapshots | Search/snapshot data quality varies. |
| GeckoTerminal | DEX adapters | HTTPS REST | None | Pool OHLCV | Public endpoint. |

No private authenticated exchange APIs are used by the observer/scanner subtrees. The guarded Kraken live stub checks for API key/secret but does not actually create a live trading node.

---

## 6. Shared Utilities

- `symbol_aliases.py`: canonical symbol resolution, same-asset/same-quote checks, quote mismatch detection. Critical for avoiding USD/USDT conflation.
- `tick_store.py`: JSONL loading/saving, deduplication, stale tick rejection, merge/sort, file discovery.
- `reports.py` modules: write local JSON/CSV/JSONL outputs in each subsystem.
- `forward_returns.py`: bar-level entry/forward price lookup, direction-adjusted returns, excursion calculations, rejection records.
- `event_study.py`: tick-level signal evaluation and random baseline generation.
- `data_loading.py` and `data_adapters.py`: CSV/OHLC public data normalization.
- `book_models.py`: L2 order book, book snapshot, spread/mid/imbalance helpers.
- `paper_quote.py`: in-memory quote lifecycle and cancellation rules.
- `paper_fill_model.py`: in-memory paper fill and adverse-selection checks.
- `funding_models_alt.py`: funding cost scenarios and persistence tracker.

---

## 7. Persistence & Schema

No database schema exists in this target subtree. Persistence is local files.

Common artifact types:

- OHLC CSV: timestamp/open/high/low/close plus optional volume/vwap/count.
- Nautilus `ParquetDataCatalog`: instruments and bars for backtests.
- JSONL tick files: one normalized trade/quote/event per line.
- JSON summaries: aggregate run metadata, candidate counts, rejection reasons, verdicts.
- CSV summaries: horizon/signal/group tabular reports.
- Markdown reports: some runner scripts generate human-readable summaries, but existing markdown reports were not used as audit sources.

Overwrite behavior:

- Several scanners open output logs with `w`, overwriting previous files.
- Tick capture generally uses timestamped filenames and append-per-tick writing.
- Report writers use fixed filenames under a run output directory; caller must choose unique output dirs to preserve old results.

---

## 8. Frontend Architecture

No frontend exists. This target is CLI/research tooling only.

---

## 9. Background Jobs & Scheduled Tasks

No cron/background scheduler is registered in code. Long-running behavior is explicit CLI loops:

- REST scanner poll loops with `max_runtime`.
- WebSocket capture loops with `duration_seconds` and reconnect budgets.
- L2 maker paper simulator with bounded duration.
- Optional Binance open-interest polling inside derivatives spot capture.

These are foreground research processes, not daemonized application jobs.

---

## 10. Middleware & Cross-Cutting Concerns

No web middleware exists. Cross-cutting concerns are implemented as utility/config patterns:

- Fee/slippage/quote-mismatch costs via `FeeModel` and scanner cost scenarios.
- Explicit rejection reasons in forward-return and candidate evaluation paths.
- Public-data-only safety checks and tests scanning for forbidden order/auth patterns.
- Bounded reconnect loops in WebSocket capture.
- Incremental JSONL writes for crash-tolerant captures.
- Conservative quote mismatch handling for USD vs USDT.
- Nautilus strategy risk constraints: risk per trade, notional cap, min size, cooldown, ATR stop.

---

## 11. Type System & Contracts

Primary contracts:

- Bar-level: `SignalEvent` -> `ForwardReturnResult` -> `SignalEvaluationSummary`.
- Tick-level: `TradeTickLite`/`QuoteTickLite` -> `TickSignalEvent` -> `TickForwardReturn`.
- Funding: `FundingObservation`, `FundingObservationAlt`, `CostScenario`, persistence tracker state.
- L2 maker paper: `OrderBook`, `BookSnapshot`, `PaperQuote`, `PaperQuoteState`, `PaperFill`, `WSEvent`.
- Nautilus strategy: `StrategyConfig`, `BarType`, `Bar`, `Quantity`, `Price`, `OrderFilled`, `BacktestEngine` objects.

Contract risks:

- Several older scripts use Nautilus constructor signatures that appear stale relative to the verified v1.227 patterns.
- Some configs expose fields that are not consumed by implementation, creating a false contract with CLI users.
- Raw exchange payloads in tick records are useful for traceability but can change shape without warning.

---

## 12. Configuration & Environment

Parent project config:

- `pyproject.toml` requires Python `>=3.12,<3.15` and `uv ==0.11.8`.
- Local environment used Python 3.14.5 and `.venv` pytest/ruff.
- Ruff target version is py312 and line length 100.
- Pytest config in `pyproject.toml` sets `testpaths = ["tests"]`, but targeted tests were run directly by path.

Runtime config examples:

- Strategy constants live in Python config modules, not env files.
- Guarded live stub env vars:
  - `I_UNDERSTAND_THIS_CAN_LOSE_MONEY` — must equal `yes`.
  - `KRAKEN_API_KEY` — required for guarded live path, value treated as secret.
  - `KRAKEN_API_SECRET` — required for guarded live path, value treated as secret.
- Observer/scanner CLIs configure paths, symbols, venues, thresholds, horizons, costs, and durations via CLI flags.

Secrets posture:

- Observer/scanner modules require no secrets.
- No secrets were printed or included in this report.

---

## 13. CI/CD Pipeline

GitHub Actions workflows present at repository root:

### `build-docs.yml`

Observed workflow source cues:

- `7|on:`
- `8|  push:`
- `11|jobs:`
- `12|  build-docs:`
- `14|    steps:`
- `17|        with:`

### `build-v2.yml`

Observed workflow source cues:

- `5|# v2 package index at:`
- `8|permissions:`
- `13|on:`
- `14|  push:`
- `15|    branches:`
- `21|env:`
- `24|jobs:`
- `25|  plan:`
- `27|    outputs:`
- `30|    steps:`
- `33|        with:`
- `41|        with:`
- `48|        env:`
- `54|  pre-commit:`
- `60|    env:`
- `71|    steps:`
- `73|        with:`
- `81|        with:`

### `build.yml`

Observed workflow source cues:

- `7|on:`
- `8|  push:`
- `10|  pull_request:`
- `13|concurrency:`
- `18|env:`
- `25|jobs:`
- `26|  plan:`
- `28|    outputs:`
- `31|    steps:`
- `34|        with:`
- `42|        with:`
- `49|        env:`
- `55|  pre-commit:`
- `61|    env:`
- `72|    steps:`
- `75|        with:`
- `84|        with:`
- `95|        env:`

### `cli-binaries.yml`

Observed workflow source cues:

- `3|permissions:`
- `7|on:`
- `8|  workflow_dispatch:`
- `9|  push:`
- `10|    branches:`
- `13|env:`
- `17|jobs:`
- `18|  build-linux-x86:`
- `21|    defaults:`
- `22|      run:`
- `24|    steps:`
- `26|        with:`
- `34|        with:`
- `39|        with:`
- `69|        with:`
- `73|  build-linux-arm64:`
- `76|    defaults:`
- `77|      run:`

### `codeql-analysis.yml`

Observed workflow source cues:

- `8|on:`
- `9|  workflow_dispatch:`
- `10|  pull_request:`
- `12|  schedule:`
- `15|jobs:`
- `16|  analyze:`
- `19|    strategy:`
- `21|      matrix:`
- `23|    steps:`
- `26|        with:`
- `35|        with:`
- `55|        with:`
- `62|        with:`

### `coverage.yml`

Observed workflow source cues:

- `10|on:`
- `11|  workflow_dispatch:`
- `13|jobs:`
- `14|  build:`
- `16|    env:`
- `21|    services:`
- `22|      redis:`
- `24|        ports:`
- `31|      postgres:`
- `33|        env:`
- `37|        ports:`
- `41|    steps:`
- `44|        with:`
- `53|        with:`
- `58|        with:`
- `66|        env:`
- `72|        env:`
- `88|      #   with:`

### `docker.yml`

Observed workflow source cues:

- `7|on:`
- `8|  push:`
- `10|  workflow_dispatch:`
- `12|env:`
- `19|jobs:`
- `21|  test-docker-build:`
- `25|    permissions:`
- `27|    strategy:`
- `29|      matrix:`
- `30|        include:`
- `35|    env:`
- `37|    steps:`
- `40|        with:`
- `67|        with:`
- `79|        with:`
- `89|        with:`
- `95|        with:`

### `dst.yml`

Observed workflow source cues:

- `12|permissions:`
- `16|on:`
- `17|  push:`
- `19|  workflow_dispatch:`
- `21|concurrency:`
- `25|env:`
- `28|jobs:`
- `29|  dst-smoke:`
- `32|    env:`
- `41|      # A failure here is replayable locally with:`
- `44|    steps:`
- `46|        with:`
- `53|        with:`
- `58|        with:`

### `nightly-docs-features-check.yml`

Observed workflow source cues:

- `7|permissions:`
- `11|on:`
- `12|  push:`
- `14|  schedule:`
- `18|jobs:`
- `19|  docsrs-check:`
- `22|    env:`
- `26|    steps:`
- `28|        with:`
- `36|        with:`
- `41|        with:`
- `54|        with:`
- `60|  check-features:`
- `63|    env:`
- `67|    steps:`
- `69|        with:`
- `77|        with:`
- `82|        with:`

### `nightly-merge.yml`

Observed workflow source cues:

- `7|on:`
- `8|  push:`
- `10|  schedule:`
- `14|jobs:`
- `15|  check-develop-status:`
- `17|    permissions:`
- `19|    outputs:`
- `22|    steps:`
- `25|        with:`
- `34|        with:`

### `nightly-miri.yml`

Observed workflow source cues:

- `13|permissions:`
- `17|on:`
- `18|  push:`
- `20|  schedule:`
- `24|jobs:`
- `25|  miri:`
- `28|    strategy:`
- `30|      matrix:`
- `31|        crate:`
- `34|    env:`
- `42|    steps:`
- `45|        with:`
- `54|        with:`
- `59|        with:`

### `nightly-tests.yml`

Observed workflow source cues:

- `5|permissions:`
- `9|on:`
- `10|  push:`
- `12|  schedule:`
- `16|jobs:`
- `17|  turmoil:`
- `20|    env:`
- `24|    steps:`
- `27|        with:`
- `36|        with:`
- `41|        with:`
- `49|        env:`
- `54|        env:`
- `62|  build-macos:`
- `65|    strategy:`
- `67|      matrix:`
- `68|        python-version:`
- `72|    defaults:`

### `performance.yml`

Observed workflow source cues:

- `7|on:`
- `8|  push:`
- `11|jobs:`
- `12|  performance-benchmarks:`
- `14|    env:`
- `22|    services:`
- `23|      redis:`
- `25|        ports:`
- `32|      postgres:`
- `34|        env:`
- `38|        ports:`
- `42|    steps:`
- `45|        with:`
- `57|        with:`
- `92|        with:`

### `security-audit.yml`

Observed workflow source cues:

- `7|permissions:`
- `11|on:`
- `12|  push:`
- `14|  schedule:`
- `18|jobs:`
- `19|  cargo-audit:`
- `22|    environment:`
- `30|    steps:`
- `32|        with:`
- `40|        with:`
- `45|        with:`
- `51|  cargo-deny:`
- `54|    environment:`
- `62|    steps:`
- `64|        with:`
- `72|        with:`
- `77|        with:`
- `83|  cargo-vet:`


CI relevance to this target subtree:

- The parent project CI is broad NautilusTrader CI, not strategy-subtree-specific CI.
- The examples/strategies tests can be run directly and passed in this audit.
- Lint applies to examples, but examples have per-file ignores for some security/mock-credential patterns. The targeted examples lint is still noisy.

---

## 14. Security Posture

Positive findings:

- Most modules are public-data-only and do not accept API keys.
- Observer modules include explicit no-order/no-private-key/no-execution wording and tests.
- Tests scan for forbidden order/auth patterns in several observer/gate files.
- The guarded live stub requires explicit CLI and env acknowledgements before even importing live-oriented Nautilus components.
- No database, web server, auth session, cookie, CSRF, or user-submitted web content exists.

Security/research risks:

- `volatility_gate.py` uses `urllib.request.urlopen`; ruff flags S310 because arbitrary URL schemes can be risky in general. In this source, URL is constructed from a hardcoded Kraken HTTPS base and fixed pair map.
- User-provided output paths are written directly; this is normal CLI behavior but not sandboxed.
- Raw exchange payloads may be persisted in JSONL. They are public market data but can be large/noisy.
- Silent exception swallowing in REST adapters can hide outages or schema breaks.
- Any future live execution must remain hard-separated from observer/research paths.

---

## 15. Known Patterns & Conventions

- Research scripts prefer explicit verdicts and rejection reasons.
- Use public market data as observation input; do not assume execution from observer evidence.
- Treat derivatives as signal sources, not primary execution instruments for UK retail assumptions.
- Prefer cost-adjusted net returns over raw returns.
- Require overlap/range/sample gates before promoting a signal family.
- For Nautilus strategies, use prior-bar indicator values for breakout checks.
- Keep secrets out of logs and reports.

---

## 16. Dependency Map

- NautilusTrader: strategy, backtest engine, instruments, bars, quantities, money, orders.
- pandas/numpy: CSV and data manipulation in loaders/backtests.
- pyarrow/ParquetDataCatalog: Nautilus catalog storage.
- requests/httpx/urllib: public REST calls.
- aiohttp/websockets: public WebSocket capture.
- pytest: regression and smoke tests.
- ruff: lint/security/style checks.

---

## 17. Edge Cases & Operational Notes

- `uv run` currently fails in this environment because repo requires `uv ==0.11.8` but installed `uv` is 0.11.14. Use `.venv/bin/python` or install matching uv.
- L2 maker paper direct simulator path has a book-sharing issue; wrapper path works around it.
- Binance futures WebSocket must use `/market/stream` for this code’s combined stream path.
- Kraken v2 subscriptions require the v2 endpoint and dict-format parsing.
- USD/USDT quote mismatch is a first-class research concern; conflating them can fabricate edge.
- Single observation windows must not be overgeneralized into structural claims.
- Several scripts write fixed filenames and can overwrite prior results.
- Public API schema/rate-limit drift can bias captures and should be diagnosed explicitly.

---

## 18. Testing Posture

### Commands run

Initial mandated commands using `uv run` failed due uv version pin mismatch:

```text
error: Required uv version `==0.11.8` does not match the running version `0.11.14`
```

Fallback test command:

```bash
.venv/bin/python -m pytest -q --tb=line   examples/strategies/kraken_btcusd_research/tests   examples/strategies/kraken_market_structure_scanner/tests   examples/strategies/kraken_l2_maker_paper/tests   examples/strategies/venue_agnostic_signal_observer/tests   examples/strategies/test_volatility_gate.py   examples/strategies/test_volatility_gate_capture_perm.py
```

Result:

```text
466 passed in 13.48s
```

Lint command:

```bash
.venv/bin/ruff check examples/strategies/kraken_btcusd_research   examples/strategies/kraken_market_structure_scanner   examples/strategies/kraken_l2_maker_paper   examples/strategies/venue_agnostic_signal_observer   examples/strategies/volatility_gate.py   examples/strategies/test_volatility_gate.py   examples/strategies/test_volatility_gate_capture_perm.py
```

Result:

```text
Found 1616 errors.
1084 fixable with --fix; 162 additional hidden fixes with --unsafe-fixes.
```

Representative lint categories:

- Import sorting (`I001`) and module-level import placement (`E402`).
- Trailing whitespace / missing final newlines (`W291`, `W292`, `W293`).
- Unused imports/variables (`F401`, `F841`).
- Deprecated typing aliases (`UP035`, `UP006`, `UP045`).
- Docstring style (`D213`, `D401`).
- Complexity (`C901`) in trade-flow functions.
- Security audit warnings (`S310`) around `urllib` usage.
- Misc modernization/refurb suggestions.

Coverage posture:

- Strong regression coverage for observer safety/no-order posture, non-finite price guards, symbol aliases, WebSocket URL constants, reconnect/session cleanup, forward-return semantics, DEX/derivatives/cross-asset gates, L2 paper components, and Kraken BTC strategy smoke paths.
- Gaps include real WebSocket integration tests, stale Nautilus scripts, config-field consumption tests, and checks for several known broken script paths.

---

## 19. Top Priorities / Recommendations

### Critical

1. Fix or retire broken `kraken_btcusd_research` scripts.
   - `run_backtest.py` and `import_kraken_ohlcv_to_catalog.py` appear stale/broken. They undermine confidence because users will naturally try them first.

2. Keep observer-only and execution-capable paths physically separate.
   - The subtree mostly does this well. Preserve it. Do not add order submission to venue-agnostic observers or scanners.

### High

3. Fix long-stop logic in `strategy.py`.
   - Use the correct protective stop semantics for long positions; current `min(initial_stop, trailing_stop)` likely makes stops looser than intended.

4. Make config fields executable or remove them.
   - Examples: scanner `min_net_edge_bps`, L2 `quote_offset_bps`, `max_cancel_rate`, `min_spread_bps`, reconnect delay, live max order/daily loss constants.

5. Add regression tests for known stale/broken entrypoints.
   - Importer CLI/backtest script smoke tests would catch missing imports, wrong Nautilus signatures, and use-before-assignment bugs.

6. Fix L2 simulator book ownership.
   - `MakerPaperSimulator.run()` should share the WebSocket client’s live book dict or process event payloads into its own book dict deterministically.

### Medium

7. Improve REST/WS diagnostics.
   - Stop swallowing all exceptions without structured reason fields. Distinguish API error, timeout, schema mismatch, no data, and parser drop.

8. Stop overwriting reports by default.
   - Timestamp run directories or require explicit `--overwrite` for scanner/report outputs.

9. Enforce quote-currency semantics consistently.
   - Avoid implicit USD/USDT equivalence in symbol maps unless the specific study intentionally models that basis.

10. Add checksum/sequence validation or resync strategy for L2 book capture.
    - Without it, local books can drift while paper fill/PnL remains confidently wrong.

11. Make paper PnL labels more conservative.
    - Current L2 PnL is a fill/spread heuristic, not inventory-aware market-making PnL.

### Low

12. Run ruff auto-fix in focused passes.
    - Do not mix style cleanup with research logic changes. The lint surface is large but mostly mechanical.

13. Consolidate duplicate data fetcher/normalizer utilities.
    - `data_adapters.py`, `data_fetcher.py`, `data_loading.py`, and `csv_normalizer.py` overlap.

14. Add a strategy-subtree-specific test command in project tooling.
    - A named script/Make target would make the passing 466-test subset easier to reproduce.

---

## 20. Audit Methodology

- Loaded and followed the `product-audit` skill.
- Loaded Nautilus-specific development guidance.
- Did not use existing README/docs/reports markdown as source of truth.
- Read source/config/test files and delegated three parallel source-reading slices.
- Manually verified key files and suspicious claims directly.
- Ran targeted tests and lint checks.
- Wrote this report as a source-derived snapshot for 2026-05-14.
