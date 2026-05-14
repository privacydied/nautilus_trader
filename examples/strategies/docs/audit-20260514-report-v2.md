# PRODUCT AUDIT — NautilusTrader `examples/strategies`
## Date: 2026-05-14
## Version: v2

---

## Source-of-truth and scope

This audit covers `/mnt/nasirjones/py/nautilus_trader/examples/strategies` from source code only. Per the audit rule, existing documentation, README files, architecture notes, and prior markdown reports were not used as sources of truth. Existing audit filenames were only listed to choose this output filename.

Audited source population:
- 126 Python source files under `examples/strategies`.
- 35 pytest-style test files under the same tree.
- Root package/config files read directly: `/mnt/nasirjones/py/nautilus_trader/pyproject.toml`, `/mnt/nasirjones/py/nautilus_trader/python/pyproject.toml`, and `.pre-commit-config.yaml`.
- GitHub Actions workflow filenames were inventoried from `.github/workflows`; selected workflow source was inspected for CI/CD posture.

Important caveat: `examples/strategies/polymarket_btcusd_arb` currently has no visible `.py` source files in the worktree; only bytecode/cache artifacts were discoverable by the auditing subagent. That package is therefore an operational integrity risk even though bytecode metadata/disassembly exposes much of the intended design.

// its in a different branch
---

## 1. Executive Summary

`examples/strategies` is not a single deployable product. It is a collection of research scaffolds, observer-only market microstructure tools, Kraken strategy experiments, Polymarket/Binance hypothesis-validation code, Monte Carlo permutation testing examples, volatility gates, and report-mining utilities inside the NautilusTrader repository.

The strongest and most actively maintained slice is `venue_agnostic_signal_observer`: a public-data, no-orders, no-credentials research suite that captures or loads cross-venue crypto market data, generates signal events, evaluates forward returns net of explicit cost assumptions, writes JSON/CSV/Markdown artifacts, and includes a sizeable regression test suite. It is designed to decide whether a signal hypothesis deserves longer observation, not to execute trades.

The older Kraken BTC/USD and Kraken market-structure scaffolds are useful prototypes but show clear bitrot: missing imports, wrong Nautilus API shapes in some runner paths, many lint failures, and research assumptions that need tightening before they can support claims. The Polymarket package is scientifically careful in intent (observer-only gates, replay, liquidity-spread studies, cache hashes), but the missing source files make it non-promotable until the source is restored and tests run from source.

---

## 2. Architecture Overview

### Deployment model

- Local Python research/examples tree inside a larger Rust/Python NautilusTrader monorepo.
- No web server, no HTTP routes, no database schema, no frontend/SPA.
- Execution is by CLI scripts and pytest tests.
- Runtime artifacts are file-based: JSONL tick streams, JSON summaries, CSV exports, parquet/cache files in some historical loaders, and markdown reports written by code paths.

### Tech stack

- Python 3.12-3.14 supported by root project config; current local venv used Python 3.14.5.
- NautilusTrader 1.227.0 root package, with Rust/Cython extension expectations in the parent repo.
- pandas/numpy/pyarrow/msgspec as core dependencies from root config.
- Optional/runtime network libraries include requests, urllib, aiohttp, websockets, and pandas IO.
- No ORM, no persistent DB tables in this subtree.

### Directory map

- `kraken_btcusd_research/`: bar-based Kraken BTC/USD spot research strategies, OHLCV downloader, catalog importer, backtest/live-guard runners, reports, tests.
- `kraken_l2_maker_paper/`: Kraken L2 websocket/paper maker quoting simulation with book models, paper fill model, simulator, reports, tests.
- `kraken_market_structure_scanner/`: spot spread/depth scanners and funding/basis monitor variants across Kraken/Binance/Bybit/OKX-style endpoints, with tests.
- `polymarket_btcusd_arb/`: bytecode-only Polymarket BTC Up/Down observer/replay/spread-regime research package; source missing.
- `venue_agnostic_signal_observer/`: current observer-only venue-agnostic market-microstructure research package with bar, tick, DEX/CEX, derivatives/spot, cross-asset, MCPT export, and report mining flows.
- `mcpt-main/`: standalone Monte Carlo permutation testing scripts for Donchian/moving-average/tree examples.
- Top-level `volatility_gate.py`: Kraken public OHLC volatility diagnostic/gating helper.
- Top-level `research_report_miner.py`: report artifact miner/summarizer.

### Data flow diagram

```text
Public market data APIs / local CSV / local JSONL / parquet cache
        |
        v
Adapters and normalizers
  - OHLCV loaders, tick JSONL stores, DEX snapshots, Polymarket loaders
        |
        v
Signal generators / scanners
  - lead-lag, trade-flow impulse, derivatives impulse, DEX/CEX dislocation,
    Kraken spread/depth scanners, Polymarket divergence generator
        |
        v
Forward-return and gate evaluators
  - horizons, cost floors, stale data filters, quote mismatch buffers,
    random baseline comparisons, candidate/rejection accounting
        |
        v
File artifacts
  - JSON/CSV/JSONL/Markdown reports, manifests, MCPT CSV exports, cache metadata
```

### Key architectural patterns

- Observer-only public data pipelines; the newer modules explicitly ban credentials/orders.
- Typed dataclasses for event/result contracts rather than database schemas.
- File manifests and JSON summaries for reproducibility.
- Regression tests encode previously discovered microstructure and numeric pitfalls.
- Multiple research generations coexist, so duplicate concepts exist (`LeadLagConfig`, report miners, CSV loaders) and should be consolidated.

---

## 3. Startup & Bootstrap

There is no long-lived application bootstrap. Each CLI is its own startup sequence:

1. Parse CLI args with `argparse` or use module constants.
2. Load config dataclasses or hard-coded study windows/grids.
3. Load local data or connect to public market endpoints.
4. Generate events/signals.
5. Evaluate forward outcomes with horizons and cost assumptions.
6. Write artifacts to `data/...`, `reports/...`, or package-specific output directories.
7. Exit.

Representative entry points:

- `python -m examples.strategies.venue_agnostic_signal_observer` forwards to `run_lead_lag.main`.
- `venue_agnostic_signal_observer/run_tick_capture.py` captures public websocket tick data.
- `venue_agnostic_signal_observer/run_derivatives_spot_capture.py` starts combined Binance perp/Kraken/Coinbase/OI capture tasks and writes a capture manifest.
- `venue_agnostic_signal_observer/run_derivatives_spot_lead_lag.py` evaluates captured derivative-source to spot-target lead-lag signals.
- `venue_agnostic_signal_observer/run_mcpt_export.py` exports candidate event-return series for MCPT.
- `kraken_btcusd_research/run_v4_research.py` runs multi-window V4 bar backtests.
- `kraken_btcusd_research/run_live_kraken_guarded.py` is a live guard/stub, not a full live deployment.
- `volatility_gate.py` fetches public Kraken OHLC and prints JSON diagnostics.

---

## 4. Core Flows

### 4.1 Kraken BTC/USD bar strategy research

Flow:
1. `download_kraken_ohlcv.py` fetches Kraken public OHLC batches and writes CSV.
2. `import_kraken_ohlcv_to_catalog.py` is intended to convert CSV rows into Nautilus `Bar` objects and write a parquet catalog, but currently has missing imports and likely stale instrument construction.
3. `strategy.py` runs a long-only EMA/Donchian/ATR breakout strategy on bars.
4. `strategy_v4.py` runs a 1h trend-following variant with EMA50/EMA200, Donchian(100), ATR, cooldown, and trailing stop logic.
5. `run_v4_research.py` loads catalog windows, aggregates bars, runs BacktestEngine, and writes reports.
6. Tests verify basic construction, position sizing, smoke backtests, live guard behavior, and report generation.

Main risks:
- Several runner/importer paths are nonfunctional or stale against current Nautilus APIs.
- Strategy state is manually tracked from fills instead of using portfolio as source of truth.
- Some fee arguments are accepted but unused in sizing.
- One stop calculation in the original strategy appears directionally wrong for a long trailing stop if “tighter” was intended.

### 4.2 Kraken L2 maker paper simulation

Flow:
1. `kraken_ws.py` connects to Kraken public websocket L2/trade channels.
2. Book update models maintain top-of-book/depth snapshots.
3. `paper_quote.py` creates maker quote decisions from spread/depth/edge signals.
4. `paper_fill_model.py` simulates passive fill probability/adverse selection.
5. `simulator.py` runs paper state and report accumulation.
6. `run_v7_l2_maker_paper.py` is the CLI runner.

Posture:
- Public data only.
- No real order submission path in audited modules.
- Good separation between market data ingestion and paper fill model, but network reconnect/rate/error handling should remain a priority.

### 4.3 Kraken market-structure scanner and funding/basis monitors

Flow:
1. Venue adapters fetch public spread/depth/funding data.
2. Symbol normalization maps venue-specific products.
3. Scanner modules compute opportunity-like objects from spread/depth/basis/funding conditions.
4. Runner scripts execute scans or monitors and produce report outputs.

Posture:
- Public data; no credentials observed.
- Research/scanner, not execution.

Main risks:
- Public REST schemas are brittle and should be validated with explicit schemas and timeouts.
- Cross-venue funding/futures data must stay observer-only unless the legal/execution boundary is explicitly redesigned.

### 4.4 Polymarket BTC Up/Down observer research

Because source files are missing, this audit relies on bytecode-discovered module contracts. The package appears to implement:

- Historical Polymarket/Binance data loading and deterministic cache hashing.
- Fair probability/settlement prediction for BTC Up/Down outcomes.
- Divergence signal generation with staleness, spread, TTE, fee/buffer, and rejection reasons.
- Forward outcome measurement and random baseline comparison.
- Live public observer capture and deterministic replay.
- Multi-window observation campaign aggregation.
- Spread regime studies and duration/lifecycle quote probes.
- Evidence review and observer-only gate decisions.
- Safety checks banning live nodes, order factories, execution clients, and Polymarket credential env vars.

Critical risk:
- Missing `.py` source and test source means this package cannot be safely reviewed, modified, or promoted. Restore source before any further research claims or CI reliance.

### 4.5 Venue-agnostic signal observer

This is the most complete subsystem.

Bar-level flow:
1. Load/generate source and target bars from CSV or synthetic fixtures.
2. Generate `SignalEvent` objects from source moves.
3. Evaluate target forward returns over configured horizons with fees/slippage/quote mismatch buffers.
4. Summarize by signal type/horizon and write reports.

Tick-level flow:
1. Capture or load `TradeTickLite`/`QuoteTickLite` JSONL.
2. Generate tick signals via lead-lag, trade-flow impulse, cross-asset impulse, DEX/CEX dislocation, or derivatives impulse logic.
3. Evaluate forward returns with strict finite-price and no-lookahead checks.
4. Apply candidate gates and baseline comparisons.
5. Export reports and optional MCPT CSV series.

Derivatives-source to spot-target flow:
1. `run_derivatives_spot_capture.py` captures Binance USD-M perp trades, Kraken/Coinbase spot trades, and optional Binance futures OI snapshots using public endpoints.
2. It writes JSONL per stream plus a capture manifest proving overlap.
3. `run_derivatives_spot_lead_lag.py` clips to overlap windows, generates derivative-source trade-flow impulse signals, evaluates spot forward returns, classifies OI buckets, accounts for cost floor, and writes verdict artifacts.
4. `run_mcpt_export.py` selects candidate groups and exports event-return CSVs if a group is worth MCPT falsification.

Completed derivatives-source spot-target diagnostics now include:
- GPU permutation/null tests
- GPU forward returns with CPU/GPU parity
- Lead/lag heatmap diagnostics
- Cost sensitivity diagnostics
- Cross-capture consistency aggregation
- Candidate falsification summary

These are diagnostic-only research tools and do not create execution readiness.

Important rules encoded in source/tests:
- No auth, no orders, no private keys, no execution.
- FAST_DIAGNOSTIC capture mode cannot produce a final REJECTED verdict.
- Public websocket URLs are extracted to constants and tested to avoid silent “connected but no ticks” regressions.
- Non-finite numbers are filtered/rejected before statistics and bps computations.

### 4.6 MCPT standalone examples

`mcpt-main` contains standalone research scripts for bar permutation and simple strategy examples. It is not Nautilus-integrated and should not be interpreted as execution-grade strategy performance. It is useful as a statistical falsification reference/pattern.

---

## 5. Platform / Service Inventory

| Service | Used by | Transport | Auth | Purpose | Notes |
|---|---|---:|---:|---|---|
| Kraken public OHLC | volatility gate, Kraken BTC downloader, data fetchers | REST | None | OHLC bars and diagnostics | Needs retries/timeouts and schema checks. |
| Kraken websocket v2 | L2 maker paper, derivatives/spot capture | WS | None | Spot trades/order-book updates | v2 endpoint matters for v2 subscription envelopes. |
| Binance spot REST | data fetchers, Polymarket ref, derivative evaluator | REST | None | klines, aggTrades, bookTicker | User-Agent needed in some urllib paths. |
| Binance Vision | Polymarket historical Binance loader | HTTP ZIP | None | daily aggTrades | Cache and failure modes matter. |
| Binance USD-M futures WS | derivatives/spot capture | WS | None | perp trade source | Must use `/market/stream`, not bare `/stream`. |
| Binance futures OI REST | derivatives/spot capture | REST | None | open-interest snapshots | Polling interval is bounded and public. |
| Coinbase Exchange REST/WS | data fetchers/capture | REST/WS | None | spot candles/trades | Public only. |
| DEX Screener | DEX adapters | REST | None | pool snapshots | Schema/staleness flags recommended. |
| GeckoTerminal | DEX adapters | REST | None | pool snapshots | Schema/staleness flags recommended. |
| Polymarket Gamma/CLOB | Polymarket observer | REST | None in audited posture | market discovery and public order book | Execution credentials are explicitly banned by safety checks. |

---

## 6. Shared Utilities

Key shared utilities in this subtree:

- `symbol_aliases.py`: canonical asset/quote parsing, same-asset checks, quote mismatch detection.
- `tick_store.py`: JSONL tick load/write helpers and sorted tick handling.
- `csv_normalizer.py`: OHLC CSV normalization, resampling, venue alignment.
- `data_loading.py`: bar/signal CSV loading and synthetic fixture generation.
- `data_adapters.py` / `data_fetcher.py` / `data_download.py`: public data fetch and local data adapter paths.
- `event_study.py`: tick signal forward-return evaluation, baseline generation, candidate gating.
- `forward_returns.py`: bar signal forward-return evaluation.
- `reports.py` modules: per-package JSON/CSV/Markdown artifact writers.
- `mcpt_export.py`: pure MCPT candidate selection/export adapter.
- `research_report_miner.py`: report artifact mining/summarization.

The utility layer is file-oriented and dataclass-oriented; there is no central service container or database.

---

## 7. Persistence & Schema

There is no database schema under the audited subtree.

Persistence surfaces:
- CSV OHLC and normalized series files.
- JSONL tick/trade/quote/event streams.
- JSON capture manifests, summaries, rejection tables, metadata files.
- Markdown generated reports/status files.
- Parquet cache files plus JSON metadata sidecars in Polymarket/Binance historical cache paths.
- Pickle appears in older Kraken report code; this is flagged by ruff/bandit rule S301 and should only be used for trusted local data.

Primary contracts are Python dataclasses:
- Bar-level: `SignalEvent`, `ForwardReturnResult`, `HorizonSummary`, `SignalTypeSummary`, `SignalEvaluationSummary`.
- Tick-level: `TradeTickLite`, `QuoteTickLite`, `TickSignalEvent`, `TickForwardReturn`.
- Derivatives: `DerivativeTradeTick`, `OpenInterestSnapshot`, `FundingSnapshot`, `DerivativeImpulseEvent`, `DerivativeLeadLagResult`.
- DEX: `DexPoolSnapshot`, `DexDislocationEvent`, `DexCexForwardResult`.
- Polymarket (bytecode-discovered): Binance state, Polymarket quote/metadata/snapshot, fair probability, settlement prediction, divergence signal, forward outcome, candidate group, baseline, cache metadata, backtest result.

Recommendation: add explicit `schema_version` fields to all persisted JSON/JSONL contracts and include source-code git SHA/run arguments in every run manifest.

---

## 8. Frontend Architecture

Not applicable. No frontend, SPA, browser routes, service worker, or CSS exist in the audited target.

---

## 9. Background Jobs & Scheduled Tasks

No daemonized scheduler or cron job exists inside the target. Long-running behavior is CLI-driven:

- Websocket capture scripts run until duration or interrupt.
- Campaign/observer scripts orchestrate repeated windows when invoked.
- GitHub scheduled workflows exist at repository level, notably nightly tests and security audit, but they are CI tasks rather than app background jobs.

---

## 10. Middleware & Cross-Cutting Concerns

No HTTP middleware exists. Cross-cutting concerns are implemented as code conventions and checks:

- Observer-only safety banners and tests scanning for order/credential patterns.
- Branch guards in some Polymarket research scripts.
- Finite-number guards around bps/statistics computations in newer observer code.
- Capture manifests for overlap/freshness proof.
- Explicit fee/slippage/quote mismatch buffers in signal evaluation.
- Deterministic cache hashes for Polymarket historical data.
- Pre-commit hooks at repo level for formatting, linting, secrets, gitleaks, actionlint/zizmor, and custom policy checks.

---

## 11. Type System & Contracts

The code relies on Python dataclasses and typed function signatures. Contracts are mostly informal but test-backed in newer modules.

Strong contracts:
- Tick JSONL serialization/deserialization helpers.
- Candidate group evaluation and rejection-reason accounting.
- Symbol canonicalization and quote mismatch handling.
- Derivatives capture manifests and overlap windows.
- MCPT export CSV schema.

Weak contracts:
- Older Kraken catalog/import/backtest paths use stale Nautilus API shapes and missing imports.
- Report file schemas vary between packages and generations.
- Duplicate report miners and duplicate config concepts increase ambiguity.
- Polymarket contracts cannot be source-reviewed until `.py` files are restored.

---

## 12. Configuration & Environment

Root project config:
- Package: `nautilus_trader` version `1.227.0` at repo root; Python `>=3.12,<3.15`.
- Root build backend: poetry-core with custom `build.py`; Rust/Cython extensions included in wheels.
- V2 package under `python/` uses maturin with module `nautilus_trader._libnautilus`.
- uv required version: `==0.11.8` in both root and `python/pyproject.toml`.
- Ruff target: Python 3.12, line length 100, broad lint selection including security (`S`) and complexity.
- Pytest root config: testpaths `tests`; addopts `-ra --new-first --failed-first`; asyncio strict.

Target env vars:
- Most audited examples require no env vars and no secrets.
- `kraken_btcusd_research/run_live_kraken_guarded.py` requires live gate env vars only when `--live` is passed:
  - `I_UNDERSTAND_THIS_CAN_LOSE_MONEY=yes`
  - `KRAKEN_API_KEY=[REDACTED]`
  - `KRAKEN_API_SECRET=[REDACTED]`
- Polymarket bytecode safety checks ban credential env vars such as `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE`, `POLYMARKET_PK`, and `POLYMARKET_FUNDER`.

---

## 13. CI/CD Pipeline

Workflow inventory in `.github/workflows`:
- build-docs.yml
- build-v2.yml
- build.yml
- cli-binaries.yml
- codeql-analysis.yml
- copilot-setup-steps.yml
- coverage.yml
- docker.yml
- dst.yml
- nightly-docs-features-check.yml
- nightly-merge.yml
- nightly-miri.yml
- nightly-tests.yml
- performance.yml
- security-audit.yml

Selected CI posture:
- `build.yml`: main monorepo build/pre-commit pipeline; uses hardened runner, pinned checkout, common setup, pre-commit, capnp schema checks, Rust/Python build/test jobs, and master-only cargo-deny release gate.
- `build-v2.yml`: v2 Python package build/publish path under `python/`; triggers on pushes to `test-ci`, `test-ci-v2`, `develop`, `nightly`; tests Python 3.12/3.13/3.14 on self-hosted runners.
- `nightly-tests.yml`: scheduled/manual extended tests, including turmoil network tests and macOS matrix builds/tests.
- `security-audit.yml`: scheduled/manual supply-chain audit with cargo-audit, cargo-deny, cargo-vet, and pip-audit jobs under restricted egress.
- `coverage.yml`: manual-only; coverage test step currently commented due runner OOM/shutdown instability.
- `dst.yml`: deterministic simulation testing smoke gate for `nightly` and `test-ci`, with madsim seed controls.

The examples/strategies subtree has local pytest coverage, but root CI is much broader and may not always gate every experimental example path with strict ruff cleanliness.

---

## 14. Security Posture

Positive findings:
- Newer observer modules are public-data only and explicitly state no auth, no orders, no private keys, no execution.
- Live Kraken path is guarded by CLI and env acknowledgement and does not print secrets.
- Polymarket bytecode-discovered safety checks ban live execution imports and credential env vars.
- Repo pre-commit includes detect-private-key and gitleaks.
- GitHub Actions use pinned action SHAs and harden-runner egress controls in inspected workflows.

Risks:
- Bytecode-only Polymarket package is not reviewable and is therefore unsafe to promote.
- Older code uses pickle report loading, which is unsafe for untrusted input.
- Several public URL fetchers use `urllib`/network calls without robust scheme validation, retries, and error schemas; ruff flags `S310` for URL open in `volatility_gate.py`.
- Live-capable Strategy classes can submit orders if mounted in a live engine; safety boundaries should stay in runners and tests.
- Report miners read local artifact trees; malformed or adversarial local JSON/JSONL should be handled defensively.

---

## 15. Known Patterns & Conventions

- Treat research code as observer-only until a separate explicit execution phase is designed.
- Always account for round-trip costs: fees, slippage, quote mismatch buffers, stale data buffers.
- Do not infer global structural conclusions from a single live observation window.
- Keep FAST_DIAGNOSTIC verdicts from producing final rejection decisions.
- Use module-level websocket URL constants and tests for brittle exchange endpoint paths.
- Persist data immediately during live capture; do not buffer until the end.
- Emit rejection reasons, not just zero-candidate summaries.
- Use deterministic hashes for cached research data.
- Prefer public data and no credentials for research scaffolds.

---

## 16. Dependency Map

Key dependencies and uses:
- `nautilus_trader`: BacktestEngine, Strategy, market data/instrument models, catalogs.
- `pandas`: CSV/parquet IO, time-series transforms, report aggregation.
- `numpy`/`statistics`/`math`: metrics, percentiles, bps calculations.
- `pyarrow`: parquet/catalog/cache IO in some paths.
- `requests`/`urllib`: public REST fetches.
- `aiohttp`/`websockets`: websocket capture fallback/primary transports.
- `pytest`: test suite.
- `ruff`: lint/security/static checks.
- `plotly`/visualization optional dependencies are root-level, not central to this subtree.

---

## 17. Edge Cases & Operational Notes

- Binance futures combined streams require `wss://fstream.binance.com/market/stream`; the wrong route can connect but produce no frames.
- Kraken websocket v2 subscription format requires `wss://ws.kraken.com/v2` and dict-style message parsing.
- FAST_DIAGNOSTIC runs are deliberately insufficient for structural rejection.
- Non-finite numeric values can silently poison statistics and comparisons; current tests cover several guards.
- OI polling count should scale with capture duration; too-few snapshots indicate a capture loop bug.
- Polymarket boundary quotes at 0.01/0.99 must be classified as real exchange-bound liquidity if observed from the CLOB, not as synthetic fallback.
- Catalog and bar aggregation paths in older Kraken research can silently mislabel timeframe if fallback catalog assumptions are wrong.
- Public APIs may rate-limit or change schemas; all capture scripts should write explicit error summaries and partial manifests.

---

## 18. Testing Posture

Targeted verification run:

```text
........................................................................ [ 20%]
........................................................................ [ 40%]
........................................................................ [ 60%]
........................................................................ [ 80%]
........................................................................ [100%]
360 passed in 0.87s
```

Ruff targeted run:

```text
examples/strategies/kraken_btcusd_research/__init__.py:4:22: W292 [*] No newline at end of file
examples/strategies/kraken_btcusd_research/config.py:5:2: W291 [*] Trailing whitespace
examples/strategies/kraken_btcusd_research/config.py:17:1: I001 [*] Import block is un-sorted or un-formatted
examples/strategies/kraken_btcusd_research/config.py:17:47: F401 [*] `nautilus_trader.model.identifiers.InstrumentId` imported but unused
examples/strategies/kraken_btcusd_research/config.py:18:41: F401 [*] `nautilus_trader.model.enums.BookType` imported but unused
examples/strategies/kraken_btcusd_research/config.py:18:51: F401 [*] `nautilus_trader.model.enums.OmsType` imported but unused
examples/strategies/kraken_btcusd_research/config.py:18:60: F401 [*] `nautilus_trader.model.enums.AccountType` imported but unused
examples/strategies/kraken_btcusd_research/config.py:19:43: F401 [*] `nautilus_trader.model.objects.Currency` imported but unused
examples/strategies/kraken_btcusd_research/config.py:19:53: F401 [*] `nautilus_trader.model.objects.Money` imported but unused
examples/strategies/kraken_btcusd_research/config.py:26:1: E402 Module level import not at top of file
examples/strategies/kraken_btcusd_research/config.py:26:1: I001 [*] Import block is un-sorted or un-formatted
examples/strategies/kraken_btcusd_research/config.py:26:45: F401 [*] `nautilus_trader.model.functions.currency_type_from_str` imported but unused
examples/strategies/kraken_btcusd_research/debug_v4.py:3:1: I001 [*] Import block is un-sorted or un-formatted
examples/strategies/kraken_btcusd_research/debug_v4.py:8:1: E402 Module level import not at top of file
examples/strategies/kraken_btcusd_research/debug_v4.py:8:1: I001 [*] Import block is un-sorted or un-formatted
examples/strategies/kraken_btcusd_research/debug_v4.py:9:1: E402 Module level import not at top of file
examples/strategies/kraken_btcusd_research/debug_v4.py:10:1: E402 Module level import not at top of file
examples/strategies/kraken_btcusd_research/debug_v4.py:11:1: E402 Module level import not at top of file
examples/strategies/kraken_btcusd_research/debug_v4.py:12:1: E402 Module level import not at top of file
examples/strategies/kraken_btcusd_research/debug_v4.py:13:1: E402 Module level import not at top of file
examples/strategies/kraken_btcusd_research/debug_v4.py:44:25: E702 Multiple statements on one line (semicolon)
examples/strategies/kraken_btcusd_research/debug_v4.py:44:46: E702 Multiple statements on one line (semicolon)
examples/strategies/kraken_btcusd_research/debug_v4.py:48:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/debug_v4.py:68:13: E701 Multiple statements on one line (colon)
examples/strategies/kraken_btcusd_research/debug_v4.py:69:13: E701 Multiple statements on one line (colon)
examples/strategies/kraken_btcusd_research/debug_v4.py:70:23: E701 Multiple statements on one line (colon)
examples/strategies/kraken_btcusd_research/debug_v4.py:76:7: F541 [*] f-string without any placeholders
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:12:1: I001 [*] Import block is un-sorted or un-formatted
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:14:8: F401 [*] `json` imported but unused
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:16:8: F401 [*] `os` imported but unused
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:21:1: UP035 `typing.Dict` is deprecated, use `dict` instead
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:21:1: UP035 `typing.List` is deprecated, use `list` instead
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:78:1: W293 Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:81:1: W293 Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:88:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:90:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:94:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:107:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:113:42: UP017 [*] Use `datetime.UTC` alias
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:118:30: UP017 [*] Use `datetime.UTC` alias
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:121:55: UP045 [*] Use `X | None` for type annotations
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:121:75: UP006 Use `dict` instead of `Dict` for type annotation
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:124:1: W293 Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:129:1: W293 Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:139:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:148:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:151:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:154:52: RUF010 [*] Use explicit conversion flag
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:158:58: UP045 [*] Use `X | None` for type annotations
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:158:92: UP045 [*] Use `X | None` for type annotations
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:158:122: UP006 Use `list` instead of `List` for type annotation
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:158:127: UP006 Use `dict` instead of `Dict` for type annotation
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:161:1: W293 Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:167:1: W293 Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:173:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:177:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:182:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:187:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:193:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:197:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:210:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:212:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:216:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:219:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:226:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:230:23: UP006 Use `list` instead of `List` for type annotation
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:230:28: UP006 Use `dict` instead of `Dict` for type annotation
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:233:1: W293 Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:241:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:244:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:250:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:256:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:262:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:267:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:272:74: UP017 [*] Use `datetime.UTC` alias
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:274:74: UP017 [*] Use `datetime.UTC` alias
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:275:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:283:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:286:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:288:32: RUF010 [*] Use explicit conversion flag
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py:293:11: W292 [*] No newline at end of file
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:6:1: I001 [*] Import block is un-sorted or un-formatted
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:6:8: F401 [*] `csv` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:7:8: F401 [*] `os` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:9:1: UP035 `typing.List` is deprecated, use `list` instead
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:11:28: F401 [*] `pyarrow.csv` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:12:32: F401 [*] `pyarrow.dataset` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:13:21: F401 [*] `pyarrow.ipc` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:14:21: F401 [*] `pyarrow.Table` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:15:32: F401 [*] `pyarrow.compute` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:18:36: UP006 Use `list` instead of `List` for type annotation
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:20:5: I001 [*] Import block is un-sorted or un-formatted
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:25:49: F401 [*] `nautilus_trader.model.functions.currency_type_from_str` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:29:9: F401 [*] `examples.strategies.kraken_btcusd_research.config.FAST_EMA_PERIODS` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:30:9: F401 [*] `examples.strategies.kraken_btcusd_research.config.SLOW_EMA_PERIODS` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:31:9: F401 [*] `examples.strategies.kraken_btcusd_research.config.DONCHIAN_WINDOW` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:32:9: F401 [*] `examples.strategies.kraken_btcusd_research.config.ATR_PERIOD` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:33:9: F401 [*] `examples.strategies.kraken_btcusd_research.config.RISK_PER_TRADE` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:34:9: F401 [*] `examples.strategies.kraken_btcusd_research.config.MAX_NOTIONAL_EXPOSURE_PCT` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:35:9: F401 [*] `examples.strategies.kraken_btcusd_research.config.MIN_POSITION_SIZE_BTC` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:36:9: F401 [*] `examples.strategies.kraken_btcusd_research.config.COOLDOWN_BARS` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:37:9: F401 [*] `examples.strategies.kraken_btcusd_research.config.MAKER_FEE` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:38:9: F401 [*] `examples.strategies.kraken_btcusd_research.config.TAKER_FEE` imported but unused
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:43:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:46:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:74:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:82:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:85:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:87:15: F821 Undefined name `ParquetDataCatalog`
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:88:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:93:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:95:45: F821 Undefined name `INSTRUMENT_ID`
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:103:27: F821 Undefined name `currency_type_from_str`
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:111:19: F821 Undefined name `Decimal`
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:112:19: F821 Undefined name `Decimal`
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:118:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:122:1: W293 [*] Blank line contains whitespace
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py:123:71: W292 [*] No newline at end of file
examples/strategies/kraken_btcusd_research/reports.py:6:1: I001 [*] Import block is un-sorted or un-formatted
examples/strategies/kraken_btcusd_research/reports.py:10:1: UP035 `typing.Dict` is deprecated, use `dict` instead
...
examples/strategies/venue_agnostic_signal_observer/tick_store.py:271:5: D213 [*] Multi-line docstring summary should start at the second line
examples/strategies/venue_agnostic_signal_observer/tick_store.py:327:5: D213 [*] Multi-line docstring summary should start at the second line
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:1:1: D213 [*] Multi-line docstring summary should start at the second line
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:11:75: RUF002 Docstring contains ambiguous `×` (MULTIPLICATION SIGN). Did you mean `x` (LATIN SMALL LETTER X)?
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:12:85: RUF002 Docstring contains ambiguous `×` (MULTIPLICATION SIGN). Did you mean `x` (LATIN SMALL LETTER X)?
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:13:84: RUF002 Docstring contains ambiguous `×` (MULTIPLICATION SIGN). Did you mean `x` (LATIN SMALL LETTER X)?
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:17:1: I001 [*] Import block is un-sorted or un-formatted
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:22:33: F401 [*] `bisect.bisect_right` imported but unused
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:87:5: D213 [*] Multi-line docstring summary should start at the second line
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:109:5: D213 [*] Multi-line docstring summary should start at the second line
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:127:9: D213 [*] Multi-line docstring summary should start at the second line
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:158:9: D213 [*] Multi-line docstring summary should start at the second line
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:236:17: F841 Local variable `bl_count` is assigned to but never used
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:363:9: C901 `_large_trade` is too complex (11 > 10)
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:372:28: F401 [*] `bisect.insort` imported but unused
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:385:17: F841 Local variable `old_n` is assigned to but never used
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:395:13: SIM102 Use a single `if` statement instead of nested `if` statements
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:446:9: C901 `_signed_imbalance` is too complex (14 > 10)
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:538:9: D213 [*] Multi-line docstring summary should start at the second line
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py:552:23: FURB136 [*] Replace `ref_idx + 1 if ref_idx + 1 <= idx else idx` with `min(ref_idx + 1, idx)`
examples/strategies/volatility_gate.py:1:1: D213 [*] Multi-line docstring summary should start at the second line
examples/strategies/volatility_gate.py:8:1: I001 [*] Import block is un-sorted or un-formatted
examples/strategies/volatility_gate.py:26:11: S310 Audit URL open for permitted schemes. Allowing use of `file:` or custom schemes is often unexpected.
examples/strategies/volatility_gate.py:29:18: S310 Audit URL open for permitted schemes. Allowing use of `file:` or custom schemes is often unexpected.
examples/strategies/volatility_gate.py:84:55: UP017 [*] Use `datetime.UTC` alias
examples/strategies/volatility_gate.py:88:71: UP017 [*] Use `datetime.UTC` alias
examples/strategies/volatility_gate.py:96:5: D213 [*] Multi-line docstring summary should start at the second line
examples/strategies/volatility_gate.py:185:81: UP017 [*] Use `datetime.UTC` alias
Found 1838 errors.
[*] 1273 fixable with the `--fix` option (170 hidden fixes can be enabled with the `--unsafe-fixes` option).
```

Interpretation:
- The active top-level volatility tests and `venue_agnostic_signal_observer/tests` suite passed: 360 passed in 0.87s.
- The full `examples/strategies` ruff check failed with 1838 reported issues, 1273 auto-fixable. This is partly style/import/docstring debt, but also includes real correctness findings such as undefined names in older Kraken modules.
- The successful pytest subset does not cover bytecode-only Polymarket source or every older Kraken runner path.

---

## 19. Top Priorities / Recommendations

Critical:
1. Restore all missing `.py` source and test source under `polymarket_btcusd_arb`; add CI/source-integrity check that fails if bytecode exists without matching source.
2. Fix nonfunctional Kraken BTC/USD importer/backtest/report paths: missing imports (`ParquetDataCatalog`, `INSTRUMENT_ID`, `Decimal`, `currency_type_from_str`, `sys`, `logger`), stale BacktestEngine venue/account objects, and result path bugs.
3. Keep all current research observer-only. Do not add execution until source integrity, statistical gates, liquidity gates, and legal constraints are explicitly redesigned.

High:
4. Consolidate duplicate modules/concepts: duplicate report miners, duplicate `LeadLagConfig`, overlapping CSV/signal loaders.
5. Add manifest/schema versions to every JSON/JSONL/CSV/Markdown-producing runner.
6. Harden all public network fetchers with timeout, retry/backoff, explicit status/error schemas, and endpoint-specific schema validation.
7. Enforce non-finite numeric rejection consistently at every ingestion/evaluation/statistics boundary.
8. Tighten source-derived tests for older Kraken modules and runner CLIs, not just helper functions.

Medium:
9. Reduce ruff debt selectively: first fix undefined names/security warnings/complexity in active paths, then style/import/docstring debt.
10. Replace pickle report loading or clearly isolate it as trusted-local-only.
11. Make bar aggregation timestamp-aware and validate input timeframe/gaps before labeling output bars.
12. Add explicit run metadata: command args, git SHA, data window, source endpoints, cost model, capture mode, and random seeds.

Low:
13. Improve CLI ergonomics and help text across older scripts.
14. Move standalone MCPT examples into a clearer reference/examples namespace or wrap them with reproducible CLIs.
15. Improve malformed artifact reporting in report miners.

---

## Appendix A — Python source inventory

```text
examples/strategies/kraken_btcusd_research/config.py
examples/strategies/kraken_btcusd_research/config_v4.py
examples/strategies/kraken_btcusd_research/debug_v4.py
examples/strategies/kraken_btcusd_research/download_kraken_ohlcv.py
examples/strategies/kraken_btcusd_research/import_kraken_ohlcv_to_catalog.py
examples/strategies/kraken_btcusd_research/__init__.py
examples/strategies/kraken_btcusd_research/reports.py
examples/strategies/kraken_btcusd_research/run_backtest.py
examples/strategies/kraken_btcusd_research/run_live_kraken_guarded.py
examples/strategies/kraken_btcusd_research/run_v4_research.py
examples/strategies/kraken_btcusd_research/strategy.py
examples/strategies/kraken_btcusd_research/strategy_v4.py
examples/strategies/kraken_btcusd_research/tests/helpers.py
examples/strategies/kraken_btcusd_research/tests/__init__.py
examples/strategies/kraken_btcusd_research/tests/test_all.py
examples/strategies/kraken_btcusd_research/tests/test_backtest_smoke.py
examples/strategies/kraken_btcusd_research/tests/test_basic.py
examples/strategies/kraken_btcusd_research/tests/test_breakout_smoke.py
examples/strategies/kraken_btcusd_research/tests/test_catalog_importer.py
examples/strategies/kraken_btcusd_research/tests/test_instrument.py
examples/strategies/kraken_btcusd_research/tests/test_kraken_btcusd_research.py
examples/strategies/kraken_btcusd_research/tests/test_live_guard.py
examples/strategies/kraken_btcusd_research/tests/test_position_sizing.py
examples/strategies/kraken_btcusd_research/tests/test_real_reports.py
examples/strategies/kraken_btcusd_research/tests/test_reports_smoke.py
examples/strategies/kraken_btcusd_research/tests/test_synthetic_backtest.py
examples/strategies/kraken_btcusd_research/tests/test_v4_trend_smoke.py
examples/strategies/kraken_l2_maker_paper/book_models.py
examples/strategies/kraken_l2_maker_paper/config.py
examples/strategies/kraken_l2_maker_paper/__init__.py
examples/strategies/kraken_l2_maker_paper/kraken_ws.py
examples/strategies/kraken_l2_maker_paper/paper_fill_model.py
examples/strategies/kraken_l2_maker_paper/paper_quote.py
examples/strategies/kraken_l2_maker_paper/reports.py
examples/strategies/kraken_l2_maker_paper/run_v7_l2_maker_paper.py
examples/strategies/kraken_l2_maker_paper/simulator.py
examples/strategies/kraken_l2_maker_paper/symbols.py
examples/strategies/kraken_l2_maker_paper/tests/__init__.py
examples/strategies/kraken_l2_maker_paper/tests/test_v7_l2_maker_paper.py
examples/strategies/kraken_market_structure_scanner/config.py
examples/strategies/kraken_market_structure_scanner/funding_config.py
examples/strategies/kraken_market_structure_scanner/funding_models_alt.py
examples/strategies/kraken_market_structure_scanner/funding_models.py
examples/strategies/kraken_market_structure_scanner/funding_reports_alt.py
examples/strategies/kraken_market_structure_scanner/funding_reports.py
examples/strategies/kraken_market_structure_scanner/funding_scanner_alt.py
examples/strategies/kraken_market_structure_scanner/funding_scanner.py
examples/strategies/kraken_market_structure_scanner/funding_venues.py
examples/strategies/kraken_market_structure_scanner/__init__.py
examples/strategies/kraken_market_structure_scanner/opportunity.py
examples/strategies/kraken_market_structure_scanner/run_v6_alt_funding_monitor.py
examples/strategies/kraken_market_structure_scanner/run_v6_funding_basis.py
examples/strategies/kraken_market_structure_scanner/run_v6_scanner.py
examples/strategies/kraken_market_structure_scanner/scanner.py
examples/strategies/kraken_market_structure_scanner/symbols.py
examples/strategies/kraken_market_structure_scanner/tests/test_v6b_funding.py
examples/strategies/kraken_market_structure_scanner/tests/test_v6c_alt_funding.py
examples/strategies/kraken_market_structure_scanner/tests/test_v6_scanner.py
examples/strategies/kraken_market_structure_scanner/venues.py
examples/strategies/mcpt-main/bar_permute.py
examples/strategies/mcpt-main/donchian.py
examples/strategies/mcpt-main/insample_donchian_mcpt.py
examples/strategies/mcpt-main/insample_tree_mcpt.py
examples/strategies/mcpt-main/moving_average.py
examples/strategies/mcpt-main/tree_strat.py
examples/strategies/mcpt-main/walkforward_donchian_mcpt.py
examples/strategies/research_report_miner.py
examples/strategies/test_volatility_gate_capture_perm.py
examples/strategies/test_volatility_gate.py
examples/strategies/venue_agnostic_signal_observer/collect_dex_snapshots.py
examples/strategies/venue_agnostic_signal_observer/config.py
examples/strategies/venue_agnostic_signal_observer/cross_asset_impulse.py
examples/strategies/venue_agnostic_signal_observer/csv_normalizer.py
examples/strategies/venue_agnostic_signal_observer/data_adapters.py
examples/strategies/venue_agnostic_signal_observer/data_download.py
examples/strategies/venue_agnostic_signal_observer/data_fetcher.py
examples/strategies/venue_agnostic_signal_observer/data_loading.py
examples/strategies/venue_agnostic_signal_observer/derivatives_lead_lag.py
examples/strategies/venue_agnostic_signal_observer/derivatives_models.py
examples/strategies/venue_agnostic_signal_observer/dex_adapters.py
examples/strategies/venue_agnostic_signal_observer/dex_cex_dislocation.py
examples/strategies/venue_agnostic_signal_observer/dex_models.py
examples/strategies/venue_agnostic_signal_observer/event_study.py
examples/strategies/venue_agnostic_signal_observer/latency_diagnostics.py
forward_returns.py
examples/strategies/venue_agnostic_signal_observer/__init__.py
examples/strategies/venue_agnostic_signal_observer/lead_lag.py
examples/strategies/venue_agnostic_signal_observer/__main__.py
examples/strategies/venue_agnostic_signal_observer/permutation_null.py
examples/strategies/venue_agnostic_signal_observer/permutation_null_gpu.py
mcpt_export.py
examples/strategies/venue_agnostic_signal_observer/models.py
examples/strategies/venue_agnostic_signal_observer/observer.py
examples/strategies/venue_agnostic_signal_observer/reports.py
examples/strategies/venue_agnostic_signal_observer/research_report_miner.py
examples/strategies/venue_agnostic_signal_observer/run_cross_asset_impulse.py
examples/strategies/venue_agnostic_signal_observer/run_derivatives_lead_lag.py
examples/strategies/venue_agnostic_signal_observer/run_derivatives_spot_capture.py
examples/strategies/venue_agnostic_signal_observer/run_derivatives_spot_lead_lag.py
examples/strategies/venue_agnostic_signal_observer/run_dex_cex_dislocation.py
examples/strategies/venue_agnostic_signal_observer/run_lead_lag.py
examples/strategies/venue_agnostic_signal_observer/run_permutation_null.py
run_mcpt_export.py
examples/strategies/venue_agnostic_signal_observer/run_signal_observer.py
examples/strategies/venue_agnostic_signal_observer/run_tick_capture.py
examples/strategies/venue_agnostic_signal_observer/run_tick_lead_lag.py
examples/strategies/venue_agnostic_signal_observer/run_trade_flow_impulse.py
examples/strategies/venue_agnostic_signal_observer/run_report_corpus.py
examples/strategies/venue_agnostic_signal_observer/signals.py
examples/strategies/venue_agnostic_signal_observer/symbol_aliases.py
examples/strategies/venue_agnostic_signal_observer/tests/__init__.py
examples/strategies/venue_agnostic_signal_observer/tests/test_all.py
examples/strategies/venue_agnostic_signal_observer/tests/test_audit_regression.py
examples/strategies/venue_agnostic_signal_observer/tests/test_bug_audit_pass2.py
examples/strategies/venue_agnostic_signal_observer/tests/test_capture_ws_urls.py
examples/strategies/venue_agnostic_signal_observer/tests/test_cross_asset_impulse.py
examples/strategies/venue_agnostic_signal_observer/tests/test_cross_venue_lead_lag.py
examples/strategies/venue_agnostic_signal_observer/tests/test_derivatives_lead_lag.py
examples/strategies/venue_agnostic_signal_observer/tests/test_derivatives_spot_lead_lag.py
examples/strategies/venue_agnostic_signal_observer/tests/test_dex_cex_dislocation.py
examples/strategies/venue_agnostic_signal_observer/tests/test_lead_lag_pipeline.py
examples/strategies/venue_agnostic_signal_observer/tests/test_latency_diagnostics.py
examples/strategies/venue_agnostic_signal_observer/tests/test_mcpt_export.py
examples/strategies/venue_agnostic_signal_observer/tests/test_permutation_null.py
examples/strategies/venue_agnostic_signal_observer/tests/test_permutation_null_gpu.py
examples/strategies/venue_agnostic_signal_observer/tests/test_report_corpus.py
examples/strategies/venue_agnostic_signal_observer/tests/test_research_report_miner.py
examples/strategies/venue_agnostic_signal_observer/tests/test_symbol_aliases.py
examples/strategies/venue_agnostic_signal_observer/tests/test_synthetic_fixtures.py
examples/strategies/venue_agnostic_signal_observer/tests/test_tick_lead_lag_pipeline.py
examples/strategies/venue_agnostic_signal_observer/tests/test_trade_flow_impulse.py
examples/strategies/venue_agnostic_signal_observer/tick_models.py
examples/strategies/venue_agnostic_signal_observer/tick_store.py
examples/strategies/venue_agnostic_signal_observer/trade_flow_impulse.py
examples/strategies/volatility_gate.py
```

---

## Appendix B — Detailed source-derived sub-audit: Kraken packages

```text
What I did:
- Audited Python source only under:
  - examples/strategies/kraken_btcusd_research
  - examples/strategies/kraken_l2_maker_paper
  - examples/strategies/kraken_market_structure_scanner
- Skipped README/docs/report markdown. I did not write or modify files.
- Read implementation modules, entrypoint scripts, and tests.

Files created/modified:
- None.

Issues encountered:
- Some source modules contain clear runtime/import bugs and stale/incomplete paths.
- Some source search output included markdown filenames and repository agent guidance automatically, but I did not inspect strategy README/report markdown content.

============================================================
1. examples/strategies/kraken_btcusd_research
============================================================

Purpose:
- BTC/USD Kraken spot research scaffold for bar-based technical strategies.
- Includes:
  - OHLCV downloader from Kraken public REST.
  - CSV-to-Nautilus Bar conversion/catalog import helper.
  - 5-minute EMA/Donchian/ATR long-only spot strategy.
  - V4 1-hour trend-following variant.
  - Backtest runners/report generation.
  - Guarded live-run stub that blocks live trading unless explicit conditions pass.

Source modules:

1. __init__.py
- Package marker only.

2. config.py
Purpose:
- Central constants for original BTC/USD 5-minute strategy.
Config:
- KRAKEN_VENUE = "KRAKEN"
- INSTRUMENT_SYMBOL = "BTC/USD"
- INSTRUMENT_ID = "BTC/USD.KRAKEN"
- STARTING_BALANCE_USD = 10000.0
- FAST_EMA_PERIODS = 20
- SLOW_EMA_PERIODS = 100
- DONCHIAN_WINDOW = 55
- ATR_PERIOD = 20
- RISK_PER_TRADE = 0.0025
- MAX_NOTIONAL_EXPOSURE_PCT = 0.30
- MIN_POSITION_SIZE_BTC = 0.001
- COOLDOWN_BARS = 12
- MAKER_FEE = 0.0025
- TAKER_FEE = 0.004
- TIMEFRAME_BARS = 5
- Live safety constants:
  - MAX_ORDER_NOTIONAL_USD = 25.0
  - MAX_DAILY_LOSS_USD = 25.0
Notes:
- Imports several Nautilus classes not used in module.
- Fee constants are decimal rates, not bps.

3. config_v4.py
Purpose:
- Constants for final V4 1-hour trend-following test.
Config:
- INSTRUMENT_ID = "BTC/USD.KRAKEN"
- STARTING_BALANCE_USD = 10000.0
- RISK_PER_TRADE = 0.0025
- MAX_NOTIONAL_EXPOSURE_PCT = 0.30
- MIN_POSITION_SIZE_BTC = 0.001
- MAKER_FEE = 0.0025
- TAKER_FEE = 0.0040
- TIMEFRAME_BARS = 60
- EMA_FAST_PERIODS = 50
- EMA_SLOW_PERIODS = 200
- DONCHIAN_WINDOW = 100
- ATR_PERIOD = 20
- ATR_EXPANSION_WINDOW = 100
- TRAILING_STOP_ATR_MULTIPLIER = 4.0
- COOLDOWN_BARS = 6
Notes:
- Docstring states V1/V2/V3 rejected and V4 is final bar-level test.

4. strategy.py
Purpose:
- Original Nautilus Strategy implementation for BTC/USD Kraken spot.
Entry points:
- create_strategy()
- KrakenBTCUSDResearchStrategy
- KrakenBTCUSDResearchConfig
Data flow:
- on_start subscribes to configured BarType.
- on_bar:
  - increments bar counter.
  - warms indicators.
  - checks exit before entry.
  - checks entry only if no position/cooldown.
  - updates indicators after signal checks.
  - updates cooldown and trailing high.
- Entry:
  - long-only market BUY.
  - Fast EMA > Slow EMA.
  - Close > previous Donchian upper.
  - ATR positive.
  - Position size based on account value, risk %, ATR stop distance, max notional, min size.
- Exit:
  - market SELL when close < slow EMA.
  - market SELL on ATR stop condition.
- on_order_filled maintains internal position state.
External services:
- None directly.
Operational/security:
- Strategy can submit orders if attached to live engine; no internal live guard.
- Long-only, spot-like behavior, no short logic.
Findings/issues:
- _calculate_position_size accepts fee_rate but does not use it.
- imports unused classes/types.
- current_position_size is manually tracked rather than relying on portfolio/positions; risk of desync on partial fills/rejections.
- Stop logic says use “tighter” stop but uses min(initial_stop, trailing_stop); for long positions tighter/higher stop should generally be max().
- get_account_value swallows all exceptions and falls back to STARTING_BALANCE_USD, which may hide live/account issues.
- on_bar exits immediately after submitting exit before updating indicators/cooldown/trailing state for that bar.
Recommendations:
- Use portfolio/position state as source of truth, or reconcile manual state robustly.
- Fix long stop to max(initial_stop, trailing_stop) if intended.
- Incorporate fees into sizing or remove parameter.
- Avoid fallback account value in live-capable context.
- Separate research strategy from any order-capable live wiring.

5. strategy_v4.py
Purpose:
- V4 1-hour trend-following Nautilus Strategy.
Entry points:
- KrakenBTCUSDV4TrendStrategy
- KrakenBTCUSDV4TrendConfig
- _calculate_position_size()
Data flow:
- on_start subscribes to 1h bars.
- on_bar:
  - captures prior Donchian upper.
  - updates EMA50, EMA200, EMA100, Donchian, ATR.
  - appends ATR history.
  - ticks cooldown.
  - updates trailing high.
  - skips trading until warmup > max(EMA/Donchian).
  - exits if open position.
  - enters if no open position and cooldown expired.
- Entry:
  - EMA50 > EMA200.
  - Close > prior Donchian(100) high.
  - ATR > 0.
  - ATR > rolling median ATR when median available.
  - market BUY sized by risk and 4x ATR stop distance.
- Exit:
  - ATR trailing stop: close <= highest_high - 4*ATR.
  - regime break: close < EMA100.
  - market SELL.
External services:
- None directly.
Operational/security:
- Order-capable Strategy; safe only if backtest/paper engine used.
Findings/issues:
- Uses internal position state from fills, not portfolio.
- get_account_value returns None on portfolio/account access failure; strategy silently skips entries.
- fee_rate passed to _calculate_position_size but unused.
- import BTC, Price unused.
- _indicators_warmed_up uses bar count only, not indicator.initialized flags for ATR; although ATR checked separately.
- Optimistic/pessimistic assumptions not explicit in Strategy, only in research runner fees.
Recommendations:
- Align sizing with instrument increments/limits rather than hard-coded 8 decimals only.
- Make fee/slippage treatment explicit in reports and backtest fills.
- Consider using position events or portfolio for canonical state.

6. download_kraken_ohlcv.py
Purpose:
- CLI downloader for Kraken public OHLC endpoint.
Entry point:
- main()
CLI/config:
- --pair required, e.g. BTC/USD
- --interval choices: 1, 5, 15, 30, 60, 240, 1440, 10080
- --since optional ISO datetime
- --until optional ISO datetime
- --out required CSV path
External services:
- Kraken REST public OHLC:
  - https://api.kraken.com/0/public/OHLC
Data flow:
- Convert human pair to Kraken pair, e.g. BTC/USD -> XBTUSD.
- Fetch paginated OHLC batches.
- Transform rows to timestamp/open/high/low/close/vwap/volume/count.
- Save CSV.
Operational/security:
- No credentials.
- Uses requests timeout 30s.
- Sleeps REQUEST_DELAY = 2s between batches.
Findings/issues:
- Pagination uses since_ts = last_timestamp, which may duplicate the last row on next request depending Kraken semantics.
- until filtering uses continue rather than breaking when timestamp > until_ts, causing unnecessary iteration.
- datetime_to_unix_timestamp forcibly replaces tzinfo with UTC, which can misinterpret timezone-aware inputs.
- MAX_RESULTS_PER_REQUEST unused.
Recommendations:
- Deduplicate by timestamp.
- Break on until exceeded after sorted rows.
- Respect timezone-aware datetimes via astimezone(timezone.utc).

7. import_kraken_ohlcv_to_catalog.py
Purpose:
- Convert downloaded CSV to Nautilus Bar objects and write to ParquetDataCatalog.
Entry points:
- csv_to_bars(csv_path)
- write_to_catalog(csv_path, catalog_path)
Data flow:
- pandas reads CSV.
- timestamp parsed to pandas datetime.
- constructs BarType using config TIMEFRAME_BARS.
- creates Bar per CSV row.
- write_to_catalog creates CurrencyPair instrument and writes instrument + bars.
External services:
- None.
Findings/issues:
- Missing imports in write_to_catalog:
  - ParquetDataCatalog
  - INSTRUMENT_ID
  - Decimal
  - currency_type_from_str is imported only inside csv_to_bars, not module/global for write_to_catalog.
- CurrencyPair construction appears inconsistent with newer Nautilus usage elsewhere; uses asset_class=Currency and quote_currency=Currency(...) but no base_currency.
- Many imported config values unused.
- No CLI main, despite being an importer script.
- Uses float conversions for price/volume.
Recommendations:
- Fix missing imports and instrument construction.
- Add CLI parse_args or keep as library and rename.
- Validate CSV schema and sort timestamps.
- Use Decimal/string for exact Price/Quantity construction where possible.

8. run_backtest.py
Purpose:
- Intended CLI backtest runner for original 5-minute strategy.
Entry point:
- main()
CLI/config:
- --catalog required
- --start required YYYY-MM-DD
- --end required YYYY-MM-DD
- --starting-balance default from config
External services:
- None, local ParquetDataCatalog.
Data flow:
- Load catalog.
- Read first instrument.
- Load bars.
- Add BacktestEngine venue/instrument/data.
- Add create_strategy().
- Run backtest.
- Save result pickle and generate reports.
Findings/issues:
- Missing import sys, yet uses sys.exit(main()).
- backtest_result_path used before assignment when creating result_file.
- BarType construction likely wrong: BarType(InstrumentId.from_str(INSTRUMENT_ID), 5).
- engine.add_venue parameters use string "NETTING"/"CASH" and starting_balances=[args.starting_balance], unlike other modules using OmsType/AccountType/Money.
- create_kraken_instrument uses CurrencyPair signature inconsistent with tests/helpers.
- generate_reports expects result.pkl under backtest_result_path, but save path is broken.
Recommendations:
- Treat as nonfunctional until fixed.
- Reuse tested helper patterns from tests/helpers.py or run_v4_research.py.
- Add smoke test that imports and executes minimal synthetic backtest through main path.

9. run_v4_research.py
Purpose:
- Multi-window V4 1-hour trend research runner.
Entry point:
- main()
Config:
- Hard-coded WINDOWS:
  - 2024h1: 2024-01-01 to 2024-06-01
  - 2024h2: 2024-07-01 to 2025-01-01
  - 2025: 2025-01-01 to 2026-01-01
  - 2026: 2026-01-01 to 2026-05-01
- Catalog base: repo_root/data/catalog
- Report output: repo_root/reports/baseline_v4_1h_trend
Data flow:
- Chooses 15m catalog per window, falls back to 5m catalog path.
- Loads bars by instrument id.
- Aggregates every 4 bars to 1h bars.
- Builds BacktestEngine with CASH venue.
- Runs V4 strategy.
- Reads result stats and position report.
- Calls reports.generate_reports and prints comparison/verdict.
External services:
- None.
Findings/issues:
- If fallback to 5m catalog, aggregation still groups every 4 bars but labels as 1h; 4x5m = 20m, incorrect.
- aggregate_bars_to_1h assumes contiguous 15m bars and groups sequentially without checking time boundaries.
- gross/net terminology is confused: total_pnl may already include fees depending Nautilus stats, then subtracts fees again for “net”.
- summary generated before overriding metrics may write stale JSON to disk.
- Uses stats_returns key "sharpe_ratio"; may vary by Nautilus version.
Recommendations:
- Enforce 15m input or dynamically aggregate by timestamps.
- Ensure fee treatment is not double-counted.
- Write final summary after overrides.
- Add tests for aggregation boundaries/gaps.

10. run_live_kraken_guarded.py
Purpose:
- Guarded live-trading stub for Kraken BTC/USD.
Entry points:
- validate_live_guard(args, environ)
- main()
CLI/env:
- --live required to enter live branch.
- Env required for live:
  - I_UNDERSTAND_THIS_CAN_LOSE_MONEY=yes
  - KRAKEN_API_KEY
  - KRAKEN_API_SECRET
- --catalog optional unused.
- --starting-balance accepted but unused in live branch.
Data flow:
- If no --live: prints paper mode and exits 0.
- If --live: validates env gates, constructs CurrencyPair, prints “Full Nautilus live setup required.”
External services:
- None actually connected.
Security/operational:
- Good: no secret printing, live disabled unless --live and env ack and keys.
- Heavy imports deferred until after guard.
Findings/issues:
- The live branch does not actually configure Kraken live adapter or submit orders; it is a stub.
- CurrencyPair constructor likely mismatched with current Nautilus style: price_increment and size_increment are strings, multiplier int.
- Imports unused Path, several config values.
Recommendations:
- Keep named as guarded stub unless real live wiring is implemented.
- If implementing live, add explicit kill switch, dry-run mode, max notional checks using config constants, and no fallback account values.

11. reports.py
Purpose:
- Backtest report utilities.
Entry points:
- BacktestReportGenerator
- parse_pnl()
- parse_commission()
- generate_reports()
- generate_performance_report()
Data flow:
- Can load pickled result.pkl from a directory.
- Extracts stats from BacktestResult.
- Writes trades.csv, equity_curve.csv, backtest_summary.json.
External services:
- None.
Findings/issues:
- save_trades_to_csv uses logger.warning but logger is not defined.
- stats extraction mixes old/new Nautilus keys; may produce zeros silently.
- generate_reports writes placeholder trade/equity data when actual lists absent.
- parse_pnl Money repr parsing is brittle.
- generate_performance_report with trades=[] calls save_trades_to_csv and triggers undefined logger if no trades.
Recommendations:
- Define logger.
- Avoid placeholder trade rows; explicitly mark unavailable.
- Use Nautilus report DataFrames directly where possible.
- Unit-test against actual BacktestResult schema for current version.

12. debug_v4.py
Purpose:
- Ad hoc diagnostic script to count which V4 entry conditions pass.
Entry point:
- top-level execution, no main guard.
Config/data:
- Hard-coded catalog path data/catalog/kraken_btcusd_15m_2024h1
- Hard-coded InstrumentId BTC/USD.KRAKEN
Data flow:
- Loads 15m bars, aggregates every 4 to 1h.
- Manually updates EMA/Donchian/ATR and counts condition hits.
External services:
- None.
Findings/issues:
- Executes on import because no if __name__ == "__main__".
- Hard-coded local path.
- Uses ATR.update_raw return value, but elsewhere strategy uses atr.value; possible mismatch.
Recommendations:
- Add main guard and CLI args.
- Reuse aggregation from run_v4_research or extract helper.

Tests under kraken_btcusd_research:
- helpers.py:
  - Creates deterministic synthetic bars.
  - Builds BacktestEngine and CurrencyPair.
  - Runs synthetic lifecycle with strategy.
- test_basic.py:
  - Imports main modules and create_strategy.
  - Currently imports run_backtest, which may fail due source bugs.
- test_backtest_smoke.py:
  - Only contains “from decimal import Decimal”; effectively empty/no assertions.
- test_live_guard.py:
  - Tests live guard env/flag behavior.
- test_v4_trend_smoke.py:
  - Tests V4 constants, imports, position sizing.
- test_catalog_importer.py:
  - Tests instrument ID and csv_to_bars conversion.
  - Writes temporary test_synthetic.csv in current directory rather than tempfile directory.
- Additional tests listed but not all read due scope/time: synthetic backtest, reports, real reports, position sizing, etc.
Overall test findings:
- Good coverage around guard and core sizing/import smoke.
- Several tests are smoke-only and may not catch broken CLI paths.
- Need direct tests for run_backtest main, import catalog write_to_catalog, report no-trade path, and V4 aggregation.

============================================================
2. examples/strategies/kraken_l2_maker_paper
============================================================

Purpose:
- V7 observer-only L2 maker-paper simulator for Kraken public WebSocket.
- Simulates hypothetical post-only quotes at top of book; no real orders or private keys.
- Tracks paper quote lifecycle, pessimistic fills, cancellation, adverse selection, and outputs JSONL/summary.

Source modules:

1. __init__.py
Purpose:
- Package docstring states observer-only, no orders/private keys/live execution.

2. config.py
Purpose:
- Dataclass config for V7 simulator.
Config:
- symbols default ["BTC/USD", "ETH/USD"]
- duration_seconds = 600
- max_reconnect_attempts = 5
- reconnect_delay_seconds = 2
- quote_side = "both"
- quote_offset_bps = 0.0
- quote_lifetime_seconds = 5.0
- max_cancel_rate = 0.95
- cancel_on_mid_move_bps = 5.0
- cancel_on_spread_collapse_bps = 1.0
- cancel_on_imbalance_flip = True
- fill_model = "pessimistic"
- fill_penalty_bps = 2.0
- adverse_selection_windows = [1,5,30,60]
- stale_book_max_age_seconds = 10
- maker_fee_bps = 3
- min_spread_bps = 0.01
- output_dir = reports/v7_l2_maker_paper
- ws_url = wss://ws.kraken.com/v2
- rest_url = https://api.kraken.com
External services:
- Public Kraken WS by default.
Findings/issues:
- rest_url unused.
- reconnect_delay_seconds and max_reconnect_attempts mostly not wired through simulator entrypoint.
- maker_fee comment says Kraken maker fee ~0.16% but default is 3 bps; comment/config mismatch.
- min_spread_bps and max_cancel_rate are defined but not enforced in simulator.
Recommendations:
- Remove unused knobs or implement them.
- Clarify fee assumption.

3. symbols.py
Purpose:
- Kraken symbol mapping.
Data:
- SYMBOL_MAP:
  - BTC/USD -> BTC/USD
  - ETH/USD -> ETH/USD
- REST_PAIR_MAP:
  - BTC/USD -> XBTUSD
  - ETH/USD -> ETHUSD
Entry points:
- to_ws_symbol()
- to_rest_pair()
Findings:
- Minimal, tested.
- Only supports BTC/USD and ETH/USD.

4. book_models.py
Purpose:
- In-memory L2 book model and snapshots.
Entry points:
- BookLevel
- OrderBook
- BookSnapshot.from_book()
Data flow:
- apply_snapshot replaces book.
- apply_update modifies/removes levels and sorts.
- Computes best bid/ask, mid, spread bps, top-10 imbalance, stale/crossed flags.
Findings/issues:
- apply_update crossed-book cleanup is flawed:
  - If best_bid >= best_ask, it filters bids less than best_ask, then filters asks greater than self.best_bid after bids changed. self.best_bid may have changed or be None.
- apply_snapshot allows crossed books intentionally/tested.
- sequence field unused.
- Uses float price matching; may be okay for JSON feed but exact price levels better as Decimal/string.
Recommendations:
- Improve crossed cleanup using captured crossed bid/ask before mutation.
- Use Decimal or normalized integer ticks for production-grade book.
- Enforce max depth trimming if needed.

5. kraken_ws.py
Purpose:
- aiohttp Kraken public WebSocket v2 client for book and trade channels.
Entry points:
- KrakenBookWS.connect_and_subscribe()
- KrakenBookWS._handle_message()
- WSEvent
External services:
- wss://ws.kraken.com/v2
No credentials.
Data flow:
- Subscribe book depth=10 and trade per symbol.
- Parse messages into WSEvent.
- Maintain self.books as OrderBook per symbol.
- Invoke callback for parsed events.
Findings/issues:
- Snapshot/update distinction uses len(bids) >= 10; brittle. Kraken messages include "type": "snapshot"/"update", which should be used.
- WSEvent for book updates does not carry bids/asks despite fields existing.
- register_callback is deprecated no-op.
- reconnect_delay_seconds config not used; internal delay = 2.0 * retry_count.
- connect_and_subscribe duration timer starts before reconnect loop and is not reset after reconnect.
- SYMBOL_MAP fallback silently allows unknown symbols.
- Does not handle subscription status/acks beyond ignoring.
Security/operational:
- Public only; no auth or private data.
- Uses heartbeat and receive timeout.
Recommendations:
- Use msg type for snapshot/update.
- Wire config reconnect delay.
- Validate symbols.
- Include book update details in events or remove fields.

6. paper_quote.py
Purpose:
- Paper quote model/lifecycle.
Entry points:
- PaperQuote
- PaperQuoteState
- QuoteEngine
Data flow:
- place_quote creates PaperQuoteState and appends active quote.
- check_quotes cancels by lifetime, stale book, mid move, spread collapse, imbalance flip.
- close_all cancels all.
Config used:
- quote_lifetime_seconds
- cancel_on_mid_move_bps
- cancel_on_spread_collapse_bps
- cancel_on_imbalance_flip
Findings/issues:
- quote_offset_bps and min_spread_bps not used here; placement price supplied externally.
- Active quote cap hard-coded at 4.
- Filled quotes are only moved to history when check_quotes later runs, not immediately on fill.
- Cancellation conditions do not incorporate quote side for mid move direction; absolute move used.
Recommendations:
- Make max active quotes configurable.
- Move filled quotes to history after fill or add cleanup method.
- Apply quote offset/min spread at placement layer.

7. paper_fill_model.py
Purpose:
- Paper fill model for hypothetical maker quotes.
Entry points:
- PaperFill
- PaperFillModel.check_fills()
- PaperFillModel.check_adverse_selection()
Data flow:
- For each active quote:
  - Require quote age >= 0.5 sec.
  - Pessimistic/neutral:
    - bid fills if best ask <= quote price.
    - ask fills if best bid >= quote price.
  - optimistic currently pass/no additional behavior.
  - Apply fill_penalty_bps:
    - bid fill price reduced by penalty.
    - ask fill price increased by penalty.
  - Record PaperFill.
- Adverse selection:
  - For configured windows, compares current mid/trade price against mid at fill.
Findings/issues:
- Neutral identical to pessimistic.
- Optimistic unimplemented.
- Trade data does not feed fill decisions despite docstring stating trade confirmation triggers fill.
- For a bid, reducing fill price by penalty is favorable, not adverse/costly. Queue penalty should worsen price: bid buys higher or PnL adjusted downward; ask sells lower. Current sign is likely inverted.
- check_adverse_selection can return repeated checks for same fill/window on every trade after window.
- _adverse_checks storage unused.
Recommendations:
- Fix fill penalty sign or account as explicit cost separately.
- Implement distinct neutral/optimistic or remove choices.
- Track emitted adverse windows per fill to avoid duplicates.
- Include trade-price touches if intended.

8. simulator.py
Purpose:
- Main V7 simulator loop/class.
Entry points:
- MakerPaperSimulator.run()
- process_book_update()
- process_trade()
- get_summary_stats()
Data flow:
- Maintains books, QuoteEngine, PaperFillModel per symbol.
- On book update:
  - Builds snapshot.
  - Closes quotes on stale/crossed.
  - Checks fills.
  - Places initial quotes once book initialized.
  - Later cancels/replaces quotes when cancellation occurs.
- On trade:
  - Runs adverse selection checks for last 20 fills.
- run:
  - Opens events.jsonl.
  - Connects KrakenBookWS.
  - Logs book summaries every ~50 updates and trade events.
  - On completion, cancels active quotes and writes summary.json.
External services:
- Public Kraken WebSocket via KrakenBookWS unless injected ws_client.
Operational/security:
- Explicit observer-only, no orders.
Findings/issues:
- In __init__, self.books initialized for cfg.symbols; in run_v7_l2_maker_paper.py, sim.books = ws.books replaces it with empty dict, causing potential mismatch with quote_engines/fill_models keyed by original symbols until WS populates.
- process_book_update ignores quote_offset_bps, min_spread_bps, max_cancel_rate.
- get_summary_stats uses only QuoteEngine.history; filled active quotes may not be in history, so fill_rate/total_quoted can undercount.
- gross_pnl_bps calculation sums half-spread per fill and fees per fill but ignores adverse selection, inventory, markout, quote side, and actual exit.
- adverse selection checks increment stats but are not written to summary details.
- run returns None on WS failure and may leave partial files.
Recommendations:
- Keep a single source of book dict without replacing initialized keys unsafely.
- Add min spread and quote offset handling.
- Add explicit quote/fill event logs, not just periodic summaries/trades.
- Incorporate adverse selection into net edge or report separately.
- Treat output metrics as observation diagnostics, not PnL.

9. reports.py
Purpose:
- Write events JSONL and summary JSON.
Entry points:
- write_event(event, fh)
- write_summary(summary, output_dir)
Findings:
- Simple and tested.
- write_summary does not create output_dir itself; caller must create it.
Recommendation:
- Make write_summary robust by mkdir(parents=True, exist_ok=True).

10. run_v7_l2_maker_paper.py
Purpose:
- CLI entrypoint for V7 simulator.
CLI/config:
- --symbols default BTC/USD ETH/USD
- --duration-seconds default 120
- --quote-lifetime default 5
- --fill-model choices pessimistic/neutral/optimistic
- --maker-fee-bps default 3
- --out default reports/v7_l2_maker_paper
- --verbose
Data flow:
- Builds V7Config.
- Creates out dir.
- Creates KrakenBookWS and MakerPaperSimulator.
- Replaces sim.books with ws.books.
- Logs book summaries every 200 updates and summary at end.
External services:
- Kraken WS public.
Security:
- Prints “No orders. No private keys. Observer-only.”
Findings/issues:
- Duplicates substantial logic from MakerPaperSimulator.run instead of calling it.
- Replacement sim.books = ws.books can cause initial-key issue as noted.
- Imports unused BookSnapshot/OrderBook/write_summary at top partly used.
Recommendations:
- Make entrypoint call MakerPaperSimulator.run(ws_client=ws, out_dir=out_dir).
- Add validation for supported symbols.
- Expose fill_penalty_bps, quote_side, min_spread_bps, quote_offset_bps.

Tests under kraken_l2_maker_paper:
- test_v7_l2_maker_paper.py covers:
  - OrderBook best bid/ask, mid, spread, imbalance, staleness, updates.
  - Quote placement/cancellation/close_all.
  - No same-tick fill.
  - Pessimistic bid/ask fill.
  - Adverse selection direction.
  - reports writing.
  - config defaults/custom fee.
  - source scan ensuring no order/API key terms.
  - symbol mapping.
Findings:
- Good unit coverage for local models.
- Tests codify apply_snapshot crossed-book behavior.
- Missing tests for Kraken WS parsing with actual v2 snapshot/update type and for run_v7_l2_maker_paper book dict replacement.
- No tests for min_spread_bps, quote_offset_bps, max_cancel_rate because unimplemented.

============================================================
3. examples/strategies/kraken_market_structure_scanner
============================================================

Purpose:
- V6 public-data market structure scanners:
  - Cross-venue spot spread scanner.
  - Funding/basis scanner comparing Kraken spot with Kraken/Binance/Bybit perps.
  - V6-C altcoin funding anomaly monitor with cost scenarios and persistence.
- Observer-only: REST polling, no orders/private keys.

Source modules:

1. __init__.py
- Package marker only.

Core spot spread scanner:

2. config.py
Purpose:
- Scanner configs and fee defaults.
Config:
- FeeConfig:
  - kraken 40 bps
  - coinbase 40 bps
  - binance 10 bps
- ScannerConfig:
  - symbols default BTC/USD ETH/USD
  - venues default kraken coinbase
  - poll_interval_seconds 2
  - min_net_edge_bps 0
  - latency_buffer_bps 10
  - max_runtime_seconds optional
  - output_dir reports/v6_market_structure
Findings/issues:
- Duplicate dataclass import.
- Defaults include coinbase, but venues.py does not implement coinbase fetcher; default scanner will log coinbase as unsupported/no data silently.
- min_net_edge_bps is not used to filter logging.
Recommendations:
- Implement Coinbase or remove from default venues.
- Use min_net_edge_bps in Scanner._check_opportunities/run.

3. symbols.py
Purpose:
- Cross-venue symbol normalization.
Data:
- STANDARD_SYMBOLS includes BTC/USD, BTC/USDT, ETH/USD, ETH/USDT, SOL/USD.
- SymbolSpec has base, quote, kraken, coinbase, binance.
Important:
- USD and USDT intentionally not treated as identical.
Findings/issues:
- For BTC/USD and ETH/USD, Binance mapping points to BTCUSDT/ETHUSDT although quote is USD. calculate_opportunity prevents cross if Ticker.quote differs, but fetch_binance is passed spec.quote, so Binance BTCUSDT ticker can be mislabeled as USD for BTC/USD scans.
Recommendations:
- Do not map USD symbols to USDT venue symbols unless explicitly modeling conversion and quote mismatch.
- Split venue support by exact quote.

4. venues.py
Purpose:
- Public REST ticker adapters.
Entry points:
- Ticker dataclass.
- fetch_kraken()
- fetch_binance()
- FETCHERS = {"kraken", "binance"}
External services:
- Kraken public Ticker: https://api.kraken.com/0/public/Ticker
- Binance spot bookTicker: https://api.binance.com/api/v3/ticker/bookTicker
Findings/issues:
- Coinbase missing despite config/default symbols containing coinbase mapping.
- fetch_binance labels quote using passed quote even if Binance symbol is USDT.
- Broad except returns None, losing error detail.
- No rate limit/backoff beyond scanner sleep.
Recommendations:
- Add structured error logging.
- Correct quote labeling.
- Implement Coinbase adapter or remove config default.

5. opportunity.py
Purpose:
- Cross-venue opportunity calculation for exact matching quotes.
Entry points:
- Opportunity dataclass.
- calculate_opportunity()
Data flow:
- Rejects quote mismatch, same venue, missing/nonpositive bid/ask.
- Computes both directions:
  - buy t1 ask/sell t2 bid
  - buy t2 ask/sell t1 bid
- Selects better gross edge.
- Net = gross bps - taker fees - latency buffer.
Findings:
- Sound simple calculation, tested.
- Does not include transfer, inventory, withdrawal, settlement, stablecoin conversion, or size/depth.
Recommendations:
- Keep as observation only.
- Add size/depth and quote-age if using operationally.

6. scanner.py
Purpose:
- Polling loop for cross-venue spot opportunities.
Entry point:
- Scanner.run()
Data flow:
- For each symbol/venue, get spec and call fetcher.
- Check all venue pairs.
- Write opportunities.jsonl and summary.json.
External services:
- Via venues FETCHERS.
Findings/issues:
- Unsupported venues are silently skipped, not counted as errors.
- min_net_edge_bps not used.
- Logs all calculated opportunities, including very negative, regardless min_net_edge_bps.
- max_is_profitable = best_net and best_net > 0 returns None/false-ish inconsistently if best_net=0.
- Sleeps fixed interval; no graceful interrupt handling.
Recommendations:
- Count unsupported venues distinctly.
- Honor min_net_edge_bps or rename to documentation-only.
- Add timestamps in summary as ISO too.

7. run_v6_scanner.py
Purpose:
- CLI entrypoint for V6 cross-venue scanner.
CLI/config:
- --symbols default BTC/USD ETH/USD
- --venues default kraken coinbase
- --poll-interval-seconds default 2
- --duration-seconds default 120
- --min-net-edge-bps default 0
- --latency-buffer-bps default 10
- --kraken-fee-bps default 40
- --coinbase-fee-bps default 40
- --binance-fee-bps default 10
- --out default reports/v6_market_structure
- --log-all
Findings/issues:
- Default coinbase venue unsupported.
- Imports get_spec but unused.
Recommendations:
- Default to implemented venues only or add Coinbase.

Funding/basis scanner V6-B:

8. funding_config.py
Purpose:
- Funding scanner config and symbol/quote/funding interval maps.
Config:
- FundingConfig:
  - assets default BTC ETH
  - spot_venues default kraken
  - perp_venues default kraken binance bybit
  - poll_interval_seconds 5
  - duration_seconds 120
  - min_funding_apr 20
  - min_net_edge_bps 25
  - spot_taker_fee_bps 40
  - perp_taker_fee_bps 40
  - slippage_buffer_bps 10
  - latency_buffer_bps 10
  - basis_risk_buffer_bps 25
  - quote_mismatch_buffer_bps 20
  - stale_quote_max_age_ms 30000
  - output_dir reports/v6_market_structure
  - min_persistence_polls 3
  - venue_fees binance 5, bybit 5.5, kraken 3
- SYMBOL_MAP covers BTC, ETH, SOL, XRP, DOGE, LINK, AVAX, ADA, SUI, ARB, OP, APT, PEPE, WIF, TON.
- QUOTE_MAP distinguishes Kraken USD vs Binance/Bybit USDT.
- FUNDING_INTERVAL all 8h.
Findings:
- Good explicit quote mismatch modeling.
- spot_venues currently only Kraken supported downstream.

9. funding_models.py
Purpose:
- Dataclasses for funding scanner.
Entry points:
- PriceLevel
- FundingObservation
Data:
- PriceLevel computes mid from bid/ask else mark.
- is_stale compares exchange vs receive ms if exchange timestamp exists.
Findings:
- PriceLevel lacks bid_size/ask_size, used conditionally by alt code via hasattr.

10. funding_venues.py
Purpose:
- Public REST adapters for spot/perp tickers and funding.
External services:
- Kraken spot Ticker.
- Kraken Futures tickers:
  - https://futures.kraken.com/derivatives/api/v3/tickers
- Kraken historical funding:
  - https://futures.kraken.com/derivatives/api/v3/historical-funding-rates
- Binance USD-M funding:
  - https://fapi.binance.com/fapi/v1/fundingRate
- Binance USD-M bookTicker:
  - https://fapi.binance.com/fapi/v1/ticker/bookTicker
- Bybit V5 funding:
  - https://api.bybit.com/v5/market/funding/history
- Bybit V5 ticker:
  - https://api.bybit.com/v5/market/tickers
Findings/issues:
- kraken_funding_rates timestamp parsing is broken:
  - int(r.get("timestamp", ...).replace(...)) attempts int on date string like "2024-..." and will raise, causing [].
  - Thus Kraken funding latest may always fail unless timestamp format is numeric.
- kraken_futures_tickers(symbols) ignores symbols parameter.
- Bybit funding function returns nextFundingTime from funding history item, but funding history normally has fundingRateTimestamp; semantics likely wrong.
- Broad except returns None/empty without diagnostics.
- No session pooling/retries.
Recommendations:
- Fix Kraken timestamp parsing with datetime.fromisoformat.
- Use correct Bybit timestamp field.
- Return structured errors or log.
- Cache all-Kraken futures tickers once per poll rather than refetch per asset/venue.

11. funding_scanner.py
Purpose:
- V6-B funding/basis polling loop.
Entry points:
- run_funding_scan(cfg)
- make_observation()
- fetch_spot(), fetch_kraken_perp(), fetch_binance_perp(), fetch_bybit_perp()
Data flow:
- For each poll:
  - Fetch Kraken spot per asset.
  - For each perp venue:
    - fetch perp ticker and latest funding.
    - make FundingObservation.
    - write JSONL.
    - count candidates/rejection reasons.
- Candidate logic:
  - reject nonpositive funding.
  - reject USD/USDT mismatch.
  - reject funding APR below threshold.
  - reject estimated net edge below threshold.
  - else candidate.
- Cost:
  - entry fees = spot fee + perp venue fee.
  - exit fees same.
  - buffers = slippage + latency + basis risk + mismatch buffer if mismatch.
  - expected funding bps = funding rate * 10000.
External services:
- Via funding_venues plus direct Kraken futures tickers request.
Findings/issues:
- Imports json unused.
- cfg.spot_venues ignored; always Kraken.
- fetch_spot defaults to XBTUSD if asset not mapped, potentially wrong.
- fetch_kraken_perp refetches all futures tickers per asset, inefficient.
- stale quote tracking is initialized but not used.
- Candidate logic hard-rejects quote mismatch even though it also models quote_mismatch_buffer_bps.
- expected funding only one interval; does not model holding period or repeated funding.
- For Kraken funding, due timestamp parsing bug, frate may be None.
Recommendations:
- Respect spot_venues or remove config.
- Make quote mismatch policy configurable.
- Add funding timestamp/freshness validation.
- Avoid direct requests in scanner when adapter already exists.
- Distinguish “not candidate due mismatch” from “edge after mismatch buffer negative.”

12. funding_reports.py
Purpose:
- Write funding observation JSONL and summary.
Entry points:
- write_observation()
- write_summary()
Data flow:
- Summary computes max/median funding APR, basis, net edge from JSONL.
Findings:
- Median is upper median for even-length lists, tested.
- No duration/start/end in summary unless stats provided.
Recommendations:
- Add scan timing from scanner stats.
- Include candidate top-N rows.

13. run_v6_funding_basis.py
Purpose:
- CLI for V6-B funding/basis scanner.
CLI/config:
- --assets default BTC ETH
- --spot-venues default kraken
- --perp-venues default kraken binance bybit
- --poll-interval-seconds default 5
- --duration-seconds default 120
- --min-funding-apr default 20
- --min-net-edge-bps default 25
- --spot-fee-bps default 40
- --perp-fee-bps default 40
- --out default reports/v6_market_structure
Findings:
- Imports STANDARD_SYMBOLS unused.
- Per-venue fee overrides in FundingConfig still apply via cfg.venue_fees, so --perp-fee-bps is fallback only; CLI output may imply uniform fee.
Recommendations:
- Print effective per-venue fee overrides.

Alt funding scanner V6-C:

14. funding_models_alt.py
Purpose:
- Altcoin funding/basis observation with cost scenarios and persistence.
Entry points:
- FundingObservationAlt
- CostScenario
- PersistenceTracker
- make_cost_scenarios()
Data flow:
- CostScenario.total_cost_bps = entry + optional exit + buffers + mismatch.
- make_cost_scenarios creates:
  - conservative_taker
  - mixed_maker_taker
  - optimistic_maker
Findings:
- Good scenario separation.
- DepthLevel defined but not used.
- PersistenceTracker and CandidateState in reports_alt overlap.

15. funding_reports_alt.py
Purpose:
- V6-C JSONL and summary writing plus CandidateState persistence helper.
Entry points:
- write_observation_alt()
- write_candidate_alt()
- write_summary_alt()
- CandidateState
Data flow:
- Writes full observations and compact candidate rows.
- Summary top-10 observations by conservative net, durable candidates, rejection counts.
Findings/issues:
- CandidateState duplicates PersistenceTracker and is not used by AltFundingScanner except imported/unused.
- write_summary_alt uses set order for assets/venues, nondeterministic.
- rejected_reason_counts uses stats rejection_reasons if present, otherwise missing_counts; if stats has empty {}, it ignores computed missing_counts.
Recommendations:
- Remove CandidateState or use it consistently.
- Sort assets/venues in summary.
- Merge stats rejection counts with computed counts.

16. funding_scanner_alt.py
Purpose:
- V6-C altcoin funding anomaly scanner with scenarios and persistence.
Entry points:
- AltFundingScanner.run()
- make_observation_alt()
- fetch_kraken_spot()
- fetch_perp_funding_and_ticker()
Data flow:
- Filters configured assets to those with Kraken spot and at least one mapped perp venue.
- For each poll:
  - Fetch Kraken spot.
  - Fetch perp ticker/funding for each perp venue.
  - Build FundingObservationAlt with cost scenarios.
  - Track consecutive candidate polls.
  - Write observations/candidates.
- Candidate logic:
  - reject nonpositive funding.
  - reject quote mismatch.
  - reject low APR.
  - reject conservative net edge below threshold.
  - else candidate.
External services:
- Kraken spot REST.
- Binance/Bybit/Kraken perps/funding REST via funding_venues or direct Kraken futures request.
Findings/issues:
- For kraken perps, fetch_perp_funding_and_ticker returns funding_rate None; Kraken funding not implemented here, so Kraken candidates impossible on funding criteria.
- make_observation_alt has confusing branch: if spot_pl.mid is None or perp_pl is None, it may call _perp_mid(perp_pl) but then still later uses spot_pl.mid; however normal callers skip missing spot.
- self.scenarios = make_cost_scenarios() ignores cfg venue_fees and CLI fee overrides.
- candidate_state imported but unused.
- durable_candidates increments every durable candidate observation, not unique durable asset/venue pair.
- stats["scan_end"] in run_v6_alt_funding_monitor set before run and rewritten after run, but scanner.run’s internal summary first lacks proper end until rewritten.
- quote_stale rarely true because exchange timestamps mostly None.
- Direct requests to Kraken futures repeated per asset.
Recommendations:
- Wire cost scenarios from FundingConfig/CLI.
- Implement Kraken funding in alt scanner or exclude Kraken perps by default.
- Count unique durable candidates separately from durable observations.
- Clean missing data branches.
- Use exchange timestamps where available.

17. run_v6_alt_funding_monitor.py
Purpose:
- CLI for V6-C altcoin funding monitor.
CLI/config:
- default assets SOL XRP DOGE LINK AVAX ADA SUI ARB OP APT
- default perp venues binance bybit
- default spot venue kraken
- duration 600
- poll interval 10
- min funding APR 30
- min conservative net edge 25
- min persistence polls 3
- out reports/v6_market_structure
Operational/security:
- Docstring: observer-only, no orders, no API keys, no live execution.
Findings/issues:
- cfg.output_dir is not set from args.out. FundingConfig constructed without output_dir=args.out, so scanner writes default reports/v6_market_structure regardless --out.
- min_persistence_polls mutated after scanner construction; scanner uses cfg reference, so it works, but comment says config doesn’t have it natively although FundingConfig does define it.
- scan_start and scan_end both set before scanner.run, then scan_end updated after; summary rewritten manually.
Recommendations:
- Pass output_dir=args.out into FundingConfig.
- Remove post-construction mutation.
- Set scan_start before and scan_end after only.

Tests under kraken_market_structure_scanner:
- test_v6_scanner.py:
  - Symbol parsing/specs.
  - Cross-venue opportunity calculation.
  - Quote mismatch rejection.
  - Fees/latency buffer behavior.
- test_v6b_funding.py:
  - Funding symbol maps, quote maps, intervals.
  - PriceLevel mid/stale.
  - make_observation candidate/rejection/fee calculations.
  - report JSONL/summary.
- test_v6c_alt_funding.py:
  - Alt mappings.
  - Cost scenarios.
  - make_observation_alt quote mismatch/candidate logic.
  - _perp_mid.
  - _compute_net_edges.
  - PersistenceTracker/CandidateState.
  - report writing.
  - source scan for no order placement.
Findings:
- Tests strongly cover local pure functions.
- Tests do not catch:
  - default unsupported Coinbase venue.
  - Binance USDT mislabeled as USD in spot scanner.
  - --out bug in V6-C entrypoint.
  - Kraken funding timestamp parsing bug.
  - direct live REST adapter behavior.
  - stale quote fields mostly absent.

============================================================
External services summary
============================================================

Kraken BTC/USD research:
- Kraken public REST OHLC:
  - https://api.kraken.com/0/public/OHLC
- Guarded live stub requires env keys but does not call Kraken private APIs.

L2 maker paper:
- Kraken public WebSocket v2:
  - wss://ws.kraken.com/v2
- No authentication/private API.

Market structure scanner:
- Kraken public spot REST:
  - https://api.kraken.com/0/public/Ticker
- Kraken futures public REST:
  - https://futures.kraken.com/derivatives/api/v3/tickers
  - https://futures.kraken.com/derivatives/api/v3/historical-funding-rates
- Binance public REST:
  - https://api.binance.com/api/v3/ticker/bookTicker
  - https://fapi.binance.com/fapi/v1/fundingRate
  - https://fapi.binance.com/fapi/v1/ticker/bookTicker
- Bybit public REST:
  - https://api.bybit.com/v5/market/funding/history
  - https://api.bybit.com/v5/market/tickers

============================================================
Environment variables
============================================================

Only explicit env vars found:
- KRAKEN_API_KEY
- KRAKEN_API_SECRET
- I_UNDERSTAND_THIS_CAN_LOSE_MONEY=yes

Used only in kraken_btcusd_research/run_live_kraken_guarded.py.
No private keys are required by L2 maker paper or market-structure scanner.

============================================================
Security and operational notes
============================================================

Positive:
- Most scanner/simulator code is observer-only and public-data-only.
- L2 maker paper tests explicitly assert no place_order/create_order/submit_order/API key terms in key modules.
- Live BTC/USD path is guarded and currently only a stub.
- No secrets are printed.

Concerns:
- Strategy classes are order-capable if connected to a live engine.
- Original strategy get_account_value fallback can hide account access failures.
- Funding scanners examine perps/funding; product context says derivative markets may be used as signal sources, but UK retail execution should not rely on perps.
- Several scripts write to reports/ or local CSV/catalog paths without overwrite protection.
- Broad exception swallowing across REST adapters hides operational failures/rate limits/schema changes.
- No centralized rate limiter, retries with backoff, or HTTP session pooling for REST scanners.
- Some default configs reference unsupported venues or mismatched quote instruments.

============================================================
Highest-priority recommendations
============================================================

1. Fix correctness/runtime blockers:
- kraken_btcusd_research/run_backtest.py:
  - import sys
  - assign backtest_result_path before use
  - fix BarType and add_venue usage
- import_kraken_ohlcv_to_catalog.py:
  - add missing imports and valid CurrencyPair construction
- reports.py:
  - define logger or avoid logger.warning in no-trades path
- run_v6_alt_funding_monitor.py:
  - pass output_dir=args.out into FundingConfig
- funding_venues.py:
  - fix Kraken funding timestamp parsing
- market_structure config/default:
  - remove coinbase default or implement Coinbase fetcher.

2. Fix financial/model assumptions:
- strategy.py:
  - long trailing stop should likely use max(initial_stop, trailing_stop), not min.
- paper_fill_model.py:
  - fill penalty sign likely inverted; make it adverse/costly.
- run_v4_research.py:
  - do not aggregate 5m fallback bars as if they were 15m.
  - avoid fee double-counting.
- spot scanner:
  - do not label Binance USDT quotes as USD opportunities.

3. Improve observability:
- Replace broad silent excepts in venue adapters with structured error counters/log messages.
- Record request failures by endpoint/status/schema.
- Include quote timestamps/freshness where venues provide them.
- Add summary metadata: start/end ISO, config used, exact assets/venues scanned.

4. Tighten tests:
- Add CLI smoke tests for all run_*.py entrypoints with monkeypatched network/data.
- Add tests for:
  - V6-C --out behavior.
  - Kraken funding parser.
  - unsupported venue defaults.
  - quote mismatch handling for Binance USDT vs USD.
  - V4 aggregation with time gaps and wrong source timeframe.
  - L2 WS snapshot/update parsing using actual Kraken v2 message shapes.

5. Keep execution boundaries explicit:
- Continue treating L2 and market-structure scanners as observer-only.
- If any live execution is added, isolate it from research scripts and require explicit dry-run/live mode, kill switch, max notional, daily loss, and credential validation.
```

---

## Appendix C — Detailed source-derived sub-audit: Polymarket and MCPT packages

```text
What I did
Audited code-only source artifacts under:
- /mnt/nasirjones/py/nautilus_trader/examples/strategies/polymarket_btcusd_arb
- /mnt/nasirjones/py/nautilus_trader/examples/strategies/mcpt-main

I did not read README/docs/report markdown. For polymarket_btcusd_arb, no .py source files are present in the working tree; only __pycache__ bytecode and test bytecode are present. I inspected available bytecode metadata/disassembly and module signatures. For mcpt-main, I read all Python source files.

Files created or modified
- None.

Issues encountered
- examples/strategies/polymarket_btcusd_arb has no tracked or visible .py source files; git only tracks mcpt-main files in these two targets.
- polymarket_btcusd_arb analysis is therefore source-derived from Python bytecode artifacts, not original .py text.
- Some bytecode imports failed due to missing source package modules or stale/inconsistent bytecode import order, but signatures, constants, strings, code object names, and disassembly provided enough module-level audit coverage.
- Tests under polymarket_btcusd_arb exist only as pytest .pyc names; no test source was readable.

============================================================
AUDIT: examples/strategies/polymarket_btcusd_arb
============================================================

Overall purpose
- Observer/research scaffold for BTC Up/Down Polymarket vs Binance BTCUSDT reference-price divergence.
- Emphasis is explicitly observer-only: no orders, no keys, no execution clients, no on-chain calls.
- Main research questions:
  - Can Polymarket BTC Up/Down quoted probability diverge enough from a Binance-derived fair/settlement probability after fees, spread, staleness, and buffers?
  - Are Polymarket BTC Up/Down order books ever tight/actionable enough to trade?
  - Which durations, quote-quality regimes, and lifecycle windows have usable liquidity?

Important operational caveat
- Original .py source files are missing from the target tree. Only .pyc bytecode remains. This is operationally risky: code cannot be reviewed/modified normally, git does not track the implementation, and tests/source parity cannot be guaranteed.

Entry points discovered
- run_backtest
- live_public_observer
- live_observation_campaign
- live_replay_runner
- run_spread_regime_study
- run_duration_spread_probe
- run_1h_quote_lifecycle_observer

External services
- Binance public REST:
  - https://api.binance.com/api/v3/aggTrades
  - https://api.binance.com/api/v3/ticker/bookTicker
  - https://data.binance.vision/data/spot/daily/aggTrades/...
- Polymarket Gamma API:
  - https://gamma-api.polymarket.com
- Polymarket CLOB public book endpoint:
  - https://clob.polymarket.com/book?token_id=...
- NautilusTrader Polymarket data adapter/loader appears used in historical Polymarket data loading.

Persistence
- Cache root default:
  - /mnt/nasirjones/py/nautilus_trader/cache/polymarket_btcusd_arb
- Live observer data root:
  - /mnt/nasirjones/py/nautilus_trader/data/polymarket_btcusd_arb/live_observer
- Default reports root seen in CLI constants:
  - /mnt/nasirjones/py/nautilus_trader/reports/polymarket_btcusd_arb
  - reports/polymarket_btcusd_arb/spread_regime for spread study
- Writes parquet cache files plus .metadata.json hash manifests.
- Writes JSON/JSONL/CSV/Markdown reports/status outputs.

Security/operational posture
- safety_checks bans live/order-capable imports and env vars:
  - Banned imports: TradingNode, LiveNode, OrderFactory, PolymarketExecutionClient, PolymarketLiveExecClientFactory
  - Banned env vars: POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_PASSPHRASE, POLYMARKET_PK, POLYMARKET_FUNDER
- Entry points print or encode NO_ORDERS=1, NO_KEYS=1, OBSERVER_ONLY=1, SANITIZE_INFO=1.
- Branch guards exist for several scripts, e.g. polymarket-btcusd-arb-phase1, polymarket-btc-updown-spread-regime-v1, polymarket-btc-updown-duration-discovery-fix-v2.

------------------------------------------------------------
Module: config
------------------------------------------------------------
Purpose
- Defines core configuration and time/TTE helpers for the Polymarket BTC/USD arb observer/backtest.

Key types/functions
- PolymarketArbConfig dataclass:
  - market_slug default: btc-updown-development-fixture
  - binance_symbol default: BTCUSDT
  - threshold_bps_grid default: (5, 10, 20, 40)
  - lookback_ns_grid default: 1s, 5s, 15s, 30s, 60s
  - forward_horizon_ns_grid default: 1s, 5s, 15s, 30s, 60s
  - tte_bucket_edges_ns default: 0s, 60s, 180s, 480s, large terminal bucket
  - max_binance_staleness_ns: 2s
  - max_polymarket_staleness_ns: 2s
  - min_tte_ns: 30s
  - max_spread_bps: 200
  - min_depth: 0
  - latency_buffer_bps: 2
  - settlement_buffer_bps: 2
  - stale_buffer_bps: 1
  - min_events: 5
  - baseline_sample_count: 100
  - random_seed: 42
  - sanitize_info: True
  - maker_rebates_enabled: True
  - cache_dir / polymarket_cache_dir / binance_cache_dir
  - use_warm_cache: True
  - refresh_cache: False
  - fail_on_lookahead: True
- parse_utc_ns(value)
- tte_bucket_name(tte_ns, edges)

Config/env vars
- No env var reads found here.
- Enforces sanitize_info=True and validates positive grids and cache path containment.

Data flow
- Config object feeds data loading, signal generation, forward-return measurement, gates, reports.

Operational notes
- Good explicit config. The default market_slug looks fixture-like; production/campaign scripts should override it.
- Cache path validation helps avoid accidental writes outside cache root.

Recommendations
- Recreate source .py and ensure config is git-tracked.
- Consider exposing config via typed CLI/YAML but preserve dataclass validation.

------------------------------------------------------------
Module: models
------------------------------------------------------------
Purpose
- Shared dataclasses for Binance state, Polymarket quote/contract state, signals, outcomes, group verdicts, cache/report metadata.

Key types
- BinanceReferenceState:
  - symbol, price, ts_event_ns, optional ts_recv_ns, best_bid, best_ask, last_trade, spread_bps, source.
- PolymarketQuoteState:
  - market_slug, token_id, outcome, quoted_probability, ts_event_ns, optional book/trade/depth fields.
- PolymarketContractMetadata:
  - market_slug, strike, expiry_ns, condition_id, yes/no token IDs, outcome labels, has_no_token, sanitized_info, resolution_metadata.
- PolymarketMarketSnapshot:
  - metadata plus tuple of quote states.
- FairProbabilityResult:
  - fair_probability, sigma_used, time_to_expiry_years, settlement basis/staleness, expiry_configured.
- SettlementPredictionResult:
  - probability_yes, confidence, direction, score, in_no_trade_band, contributing_signals.
- DivergenceSignal:
  - market_slug, side=YES only, threshold_bps, lookback_ns, tte_bucket, fair/quoted probability, raw/net divergence, fee/spread/buffer costs, ts_event_ns, expiry_ns, direction LONG_YES/NO_SIGNAL, rejection_reason.
- ForwardOutcome:
  - signal, horizon_ns, forward probability/move/edge, settlement_payoff, favorable/adverse flags, rejection_reason.
- CandidateGroupResult:
  - grid cell verdict, reason, counts, edge stats, baseline comparison, gates.
- BaselineResult
- ReportPaths
- CacheMetadata
- BacktestRunResult

Data flow
- These are the central schema objects passed across loaders, signal generation, forward outcomes, gates, and reports.

Operational notes
- v1 semantics appear YES-token only; NO-side economics explicitly not analyzed in summary strings.

Recommendations
- Recreate source and document schema stability.
- Add serialization helpers instead of relying on ad hoc asdict everywhere.

------------------------------------------------------------
Module: data_cache
------------------------------------------------------------
Purpose
- Deterministic parquet cache IO with metadata and hash verification.

Key functions
- safe_name(v)
- canonical_rows_hash(rows)
- write_cache(df, data_path, metadata_path, source, symbol_or_slug, sanitize_info, loader_version_or_module)
- read_cache(data_path, metadata_path)

Data flow
- DataFrames are sorted/canonicalized by ts_event_ns, persisted as parquet.
- Metadata includes source, symbol/slug, start/end ns, row count, created_at, data_hash, sanitize_info, loader module/version, path.
- read_cache recomputes hash and raises RuntimeError on mismatch.

Persistence
- Parquet + JSON metadata sidecar.

Security/operational notes
- Hash verification is good.
- Uses safe_name for file-safe slug/symbol names.

Recommendations
- Keep hash stable across pandas versions; canonical rounding could still create subtle reproducibility issues.
- Add cache schema version in metadata.

------------------------------------------------------------
Module: binance_data
------------------------------------------------------------
Purpose
- Load/normalize Binance BTCUSDT reference states from local files, cache, or Binance Vision daily aggTrade downloads; align Binance state to Polymarket event timestamps.

Key functions/classes
- normalize_binance_df(df, symbol, source)
- states_from_df(df)
- load_binance_data(config, start_ns, end_ns, local_path)
- align_state(states, ts_event_ns, max_staleness_ns)
- _download(symbol, date)

External services
- Binance Vision daily aggregate trades ZIP endpoint:
  - data/spot/daily/aggTrades/{symbol}/{symbol}-aggTrades-{YYYY-MM-DD}.zip

Inputs/outputs
- Input DataFrame requires price and timestamp columns.
- Output normalized columns include ts_event_ns, price, last_trade, symbol, source, optional best_bid, best_ask, spread_bps.
- align_state uses bisect over ts_event_ns and rejects stale states.

Persistence
- Reads local parquet/csv.
- Reads/writes cache through data_cache.

Operational notes
- Historical Binance data source is aggTrades/trade proxy, not full order book; summary strings explicitly state this limitation.
- Live capture does poll bookTicker, so historical/live assumptions differ.

Recommendations
- Separate trade-proxy vs book-mid reference in schema/report names.
- Store Binance API/vision download failures distinctly from empty data.
- Rate-limit and retry downloads if source restored.

------------------------------------------------------------
Module: polymarket_data
------------------------------------------------------------
Purpose
- Fetch/load Polymarket historical market/trade data through Nautilus PolymarketDataLoader; normalize into contract metadata and quote DataFrame/states.

Key functions inferred
- _extract_strike(info)
- _extract_updown_expiry_ns(info, fallback_expiry_ns)
- _extract_updown_tokens(tokens)
- _row(trade, slug)
- async fetch_polymarket_trades(market_slug, start, end, sanitize_info)
- load_polymarket_data(...)
- load_polymarket_quotes_from_df(...)

External dependencies/services
- nautilus_trader.adapters.polymarket.PolymarketDataLoader
- Polymarket market data through Nautilus adapter.

Data flow
- Creates loader from market slug with token_index=0 and sanitize_info=True.
- Loads trades for [start, end].
- Extracts instrument info, token IDs, expiry, strike/price_to_beat.
- Converts trades to rows:
  - market_slug, token_id, outcome=YES, price, size, ts_event_ns, ts_recv_ns.
- Sorts and returns metadata + normalized DataFrame/states.

Security/operational notes
- Refuses sanitize_info=False.
- Catches RuntimeWarning messages matching partial/truncated data and raises RuntimeError.
- Parses expiry from slug pattern updown-{minutes}m-{epoch}; fallback to instrument expiration.
- Strike extraction uses regex from question/description/title and fallback 100000.0; fallback is potentially dangerous if not surfaced.

Recommendations
- Make fallback strike explicit fatal unless marked fixture.
- Persist original sanitized market metadata hash.
- Ensure pagination truncation is fatal in all paths.
- Restore source file and add tests around edge-case slug/token parsing.

------------------------------------------------------------
Module: fair_probability
------------------------------------------------------------
Purpose
- Compute fair YES probability for a binary up/down payout using a Black-Scholes-like binary call probability.

Key functions/constants
- SECONDS_PER_YEAR
- binary_call_probability(spot, strike, time_to_expiry_years, sigma, risk_free_rate)
- fair_probability(binance_state, time_to_expiry_ns, strike, config)

Data flow
- Uses BinanceReferenceState price, strike, and TTE.
- Returns FairProbabilityResult with probability, sigma, TTE years, basis/staleness fields.

Operational notes
- Validates inputs and clamps probability.
- Sigma source/default was not fully reconstructable from bytecode; likely configured/static.

Recommendations
- Make volatility/sigma estimation explicit and report it per signal.
- Avoid presenting Black-Scholes assumptions as settlement truth for very short-dated binary events without calibration.

------------------------------------------------------------
Module: settlement_predictor
------------------------------------------------------------
Purpose
- Pure-Python mirror/port of an arb-bot settlement predictor; derives probability_yes/confidence/direction from spot momentum, CVD, funding, perp basis, liquidation/flush signals.

Key types/functions/constants
- SettlementPredictorConfig:
  - min_history_ms
  - momentum_30s_scale_bps=12
  - momentum_3m_scale_bps=30
  - cvd_min_volume_btc=5
  - cvd_strong_tfi_threshold=0.15
  - cvd_30s_scale_usd=25000
  - cvd_3m_scale_usd=120000
  - funding_neutral_band_bps=3
  - funding_strong_bps=8
  - funding_scale_bps=1
  - perp_basis_neutral_band_bps=10
  - basis_scale_bps=25
  - flush_threshold_usd=1,000,000
  - flush_cooling_usd=150,000
  - flip_hysteresis_ms=45,000
  - no_trade_band_low/high=0.45/0.55
- SettlementView:
  - spot_mid, cvd_30s/3m, CVD notionals, liquidation buy/sell notionals, funding_rate_bps, perp_spot_basis_bps.
- SettlementPredictor:
  - reset_for_rollover(now_ms)
  - predict(view, now_ms)
- predict_settlement(state, strike, expiry_ns, now_ns=None)
- Constants:
  - MIN_SAMPLES_FOR_PREDICTION=5
  - signal opposition/flip thresholds.

Data flow
- Stateful predictor tracks recent spot observations and momentum windows.
- Stateless helper predict_settlement uses BinanceReferenceState/strike/expiry to emit a basic probability/direction.

Operational notes
- Uses derivative-market inputs (funding/perp basis/liquidations) as signals, not necessarily as execution instruments.
- For UK retail constraints, derivative signals are acceptable as observer inputs; execution should remain spot/non-derivative.

Recommendations
- Clearly separate predictor inputs available in current capture from unavailable/zero-filled fields.
- Add calibration validation against resolved Polymarket events before use in candidate gates.

------------------------------------------------------------
Module: signal_generator
------------------------------------------------------------
Purpose
- Generate YES-side DivergenceSignal candidates/rejections over grid of lookback and threshold parameters.

Key functions
- maker_fee_bps(price, maker_rebates_enabled)
  - imports PolymarketFeeModel from nautilus_trader.adapters.polymarket.fee_model.
- generate_signals(snapshot/quotes, binance_states, metadata, config)

Data flow
- For each Polymarket quote and grid cell:
  - Computes time-to-expiry bucket.
  - Rejects too_close_to_expiry.
  - Aligns Binance state within max staleness.
  - Computes fair_probability.
  - Computes raw_divergence_bps and subtracts maker fee, Polymarket spread, latency buffer, settlement buffer, stale buffer.
  - Emits LONG_YES only if net divergence >= threshold.
  - Otherwise emits rejection with reasons such as stale_or_missing_binance, spread_too_wide, edge_below_threshold.
- Rejection counters are collected.

Operational notes
- YES-only strategy: no NO-side economics.
- Looks conservative due to explicit fee/spread/stale/latency/settlement buffers.
- maker_fee_bps depends on Nautilus Polymarket fee model; if model changes, historical reports can change.

Recommendations
- Version and snapshot fee-model assumptions in every report.
- Add explicit “not order intent” field to signal objects to prevent downstream misuse.
- Expand to NO side only after separate economics and safety checks.

------------------------------------------------------------
Module: forward_returns
------------------------------------------------------------
Purpose
- Measure forward outcomes for generated divergence signals across horizons.

Key functions
- measure_forward_outcomes(signals, quotes, horizons, ...)
- assert_no_settlement_lookahead(outcomes)

Data flow
- For each signal/horizon:
  - Finds forward quoted probability at signal timestamp + horizon.
  - Computes probability move bps, direction-adjusted move, maker-fee-adjusted edge, spread-adjusted edge.
  - Handles missing forward data as no_forward_probability.
  - Handles settlement_payoff only when horizon crosses expiry.
- assert_no_settlement_lookahead raises if settlement payoff is populated before expiry-crossing horizon.

Operational notes
- Good explicit lookahead guard.
- Outcomes are signal-observer metrics, not executed PnL.

Recommendations
- Include quote staleness at forward horizon.
- Add adverse selection metrics around bid/ask, not only quoted probability.

------------------------------------------------------------
Module: baseline
------------------------------------------------------------
Purpose
- Generate random baseline forward outcomes for comparison against candidate signals.

Key function
- generate_random_baseline(...)

Data flow
- Uses random.Random(config.random_seed), samples quote/event timestamps, assigns TTE buckets and horizons, returns BaselineResult objects.

Operational notes
- Random baseline is useful but depends heavily on sampling universe and quote availability.

Recommendations
- Add stratified baseline by TTE bucket, market, and liquidity regime.
- Ensure random baseline never samples future relative to signal groups.

------------------------------------------------------------
Module: gates
------------------------------------------------------------
Purpose
- Evaluate whether candidate groups pass research gates.

Key functions
- evaluate_candidate_group(...)
- evaluate_grid(...)

Gates inferred
- min_events / insufficient_events
- mean_edge_positive
- median_edge
- win_rate
- beats_random_baseline
- not_single_event_dominated
- maker_fee_survival_gate
- settlement_leakage_guard

Verdicts
- NEEDS_MORE_DATA
- REJECTED
- CANDIDATE
- CANDIDATE_FOR_LONGER_OBSERVATION

Data flow
- Groups ForwardOutcome by lookback, threshold, TTE bucket.
- Computes mean/median/win/baseline comparisons.
- Returns CandidateGroupResult per grid cell.

Operational notes
- Good rejection-first posture.
- min_events default 5 is low; enough for smoke but not statistical confidence.

Recommendations
- Raise min_events for any promotion beyond observer.
- Add confidence intervals/permutation tests for edge metrics.
- Enforce multiple-comparison correction over grid.

------------------------------------------------------------
Module: reports
------------------------------------------------------------
Purpose
- Generate Phase 1 backtest report artifacts.

Key functions
- make_report_paths(root, run_id)
- write_reports(paths, summary, candidates, rejections, baseline, groups, safety, parity, cache_metadata)
- render_markdown(summary, groups)
- section(n, summary, groups)

Persistence
- summary_json
- candidates_jsonl
- rejections_jsonl
- baseline_jsonl
- candidate_groups_csv
- report_md
- safety_check_json
- parity_check_json
- cache_metadata_json

Operational notes
- Writes markdown reports, but I did not read any markdown output.
- Summary contains strong limitation strings:
  - Binance v1 uses aggTrades/trade-derived proxy, not full book.
  - NO-side economics not analyzed in v1.
  - Maker fill / crypto maker rebate enabled / PolymarketFeeModel required.
  - Pagination truncation fatal if warning emitted.

Recommendations
- Add machine-readable schema version to each report artifact.
- Separate executable report generation from research verdict logic.

------------------------------------------------------------
Module: run_backtest
------------------------------------------------------------
Purpose
- Phase 1 observer-only backtest / hypothesis validation CLI.

Entry point
- main(argv=None)

CLI/configs inferred
- --market-slug
- --threshold-grid default 5,10,20,40
- --start
- --end
- --binance-data
- --lookback-grid
- --forward-horizons
- --report-dir default /mnt/nasirjones/py/nautilus_trader/reports/polymarket_btcusd_arb
- --random-seed
- --baseline-samples
- --max-events
- --refresh-cache
- --fail-on-lookahead
- --skip-branch-check

Branch/safety
- REQUIRED_BRANCH: polymarket-btcusd-arb-phase1
- Emits/records PHASE=1_BACKTEST_HYPOTHESIS_VALIDATION, OBSERVER_ONLY=1, NO_ORDERS=1, NO_KEYS=1, SANITIZE_INFO=1, MAKER_REBATES_ENABLED=1.

Data flow
1. Validate branch unless skipped.
2. Build PolymarketArbConfig from CLI.
3. Load Polymarket data.
4. Load Binance data.
5. Generate signals/rejections.
6. Measure forward outcomes.
7. Assert no settlement lookahead.
8. Generate random baseline.
9. Evaluate gates/grid.
10. Run safety/parity checks.
11. Write report artifacts and STATUS.md.

Persistence
- Writes reports and STATUS.md near module directory.

Operational notes
- Strong observer-only framing.
- Uses branch checks to avoid accidental execution on wrong research branch.
- Potential risk: writes STATUS.md into source package directory; since source missing, status/source relationship is unclear.

Recommendations
- Restore .py source and make CLI runnable/testable from git.
- Do not update source-tree STATUS.md from runs; write all run state under reports/.
- Make --skip-branch-check visibly unsafe or test-only.

------------------------------------------------------------
Module: live_market_discovery
------------------------------------------------------------
Purpose
- Discover active BTC 15m UpDown markets from Polymarket Gamma API.

Key types/functions
- UpDownMarketInfo dataclass:
  - slug, question, active, closed, condition_id, yes/no token IDs, start_ns, end_ns, series_slug, resolution_source, price_to_beat, price_to_beat_source.
- discover_btc_15m_updown()
- fetch_market_detail(slug)
- _gamma_get(path, params, timeout=30)
- _parse_tokens(market)
- _ts(v)

External service
- https://gamma-api.polymarket.com

Data flow
- Queries Gamma markets active=true closed=false limit=200, ordered by startDate.
- Filters BTC/bitcoin updown/up-down/up-or-down and -15m- slug markers.
- Parses token IDs and timestamps.

Operational notes
- Public API only.
- Token parsing handles JSON strings and lists.

Recommendations
- Add backoff/retry and response schema validation.
- Log all skipped markets with reasons during discovery to audit missed opportunities.

------------------------------------------------------------
Module: duration_market_discovery
------------------------------------------------------------
Purpose
- Generalize BTC UpDown discovery across 5m, 15m, 1h, 4h and reference source classifications.

Key constants
- Durations: 5m, 15m, 1h, 4h, unknown.
- Reference sources:
  - BINANCE_BTCUSDT
  - CHAINLINK_BTCUSD
  - UNKNOWN
- Duration verdict constants:
  - DURATION_MARKET_EXISTS_NEEDS_QUOTE_OBSERVATION
  - DURATION_ACTIVE_MARKET_NO_USABLE_BOOK
  - DURATION_ACTIVE_MARKET_HAS_ACTIONABLE_TWO_SIDED_BOOK
  - DURATION_NO_ACTIVE_MARKET_NOW
  - DURATION_DISCOVERY_FAILED
  - DURATION_UNSUPPORTED_REFERENCE_SOURCE
- Probe verdict constants:
  - DURATION_PROBE_SUPERSEDES_PRIOR_NONEXISTENCE_FINDING
  - DURATION_PROBE_NEEDS_MORE_LIVE_DATA
  - DURATION_PROBE_NO_USABLE_BOOKS_IN_OBSERVED_ACTIVE_MARKETS
  - DURATION_PROBE_FOUND_ACTIONABLE_BOOKS_REQUIRES_PHASE1_BACKTEST

Key type/functions
- DurationMarketInfo extends/wraps UpDownMarketInfo with duration label/seconds, classification source, reference source kind, is_known_slug.
- classify_duration_from_slug(slug)
- classify_duration_from_start_end(start_ns, end_ns)
- classify_duration_from_title(question)
- classify_reference_source(market)
- classify_duration(market)
- discover_updown_markets(...)
- discover_duration_markets(...)
- validate_known_slug(slug)
- poll_quote_for_market(market)

External services
- Polymarket Gamma API via live_market_discovery._gamma_get.
- Polymarket CLOB book endpoint via poll_quote_for_market.

Data flow
- Discovers markets broadly, classifies duration from slug > start/end delta > title.
- Classifies reference source from question/resolution_source/slug.
- Polls book for yes_token_id to determine quote availability/actionability.

Operational notes
- 4h Chainlink products are identified and treated separately/unsupported for Binance reference source.
- Helps distinguish “product does not exist” from “no active market now” from “active but no usable book.”

Recommendations
- Persist raw discovery payloads for reproducibility.
- Keep Chainlink-vs-Binance separation strict; do not compare Chainlink settlement products against Binance without basis analysis.

------------------------------------------------------------
Module: live_capture
------------------------------------------------------------
Purpose
- Phase 2 live public capture of Binance and Polymarket data, with same grid-level rejection accounting contract as replay.

Key classes/functions
- BinancePublicStream:
  - poll_aggtrades(start_ns, end_ns)
  - poll_bookticker()
- PolymarketPublicStream:
  - poll_orderbook()
  - poll_trades()
- run_live_capture(...)
- write_capture(...)

External services
- Binance aggTrades REST
- Binance bookTicker REST
- Polymarket CLOB book REST

Data flow
- Poll loop until duration end:
  - Fetch Binance aggTrades and bookTicker.
  - Fetch Polymarket order book/trades for market token.
  - Build BinanceReferenceState and PolymarketQuoteState events.
  - Generate observer signals/rejections using signal_generator.
  - Sleep between polls.
- write_capture writes metadata and event JSONL files under live observer data root.

Persistence
- metadata.json
- Binance events JSONL
- Polymarket events JSONL
- signals/rejections likely JSONL.

Operational notes
- REST polling, not websocket; latency/staleness need to be considered.
- Uses public APIs only, User-Agent “NautilusTrader/Phase2Observer”.
- No order path observed.

Recommendations
- Add monotonic poll timestamps and latency measurement per request.
- Use bounded retries/backoff and rate-limit handling.
- Consider websocket capture if latency-sensitive, but keep observer-only.

------------------------------------------------------------
Module: live_replay
------------------------------------------------------------
Purpose
- Replay captured live data and verify accounting parity / regenerate signals from captures.

Key functions inferred
- Replay loaders and signal/gate comparison functions; exact names not fully importable due missing dependencies.

Data flow
- Reads live observer capture artifacts.
- Reconstructs Binance/Polymarket states.
- Re-runs signal generation/rejection logic.
- Compares replay results with live summary.

Operational notes
- Replay parity is important to ensure live observer accounting is deterministic.

Recommendations
- Treat replay mismatch as fatal for any research claim.
- Store config snapshot in capture metadata to guarantee replay.

------------------------------------------------------------
Module: live_replay_runner
------------------------------------------------------------
Purpose
- CLI wrapper for replaying Phase 2 captured data.

Entry point
- main()

Data flow
- Parses CLI, invokes live_replay, writes/prints replay status.

Recommendations
- Ensure CLI exits nonzero on replay mismatch.
- Add --strict default true.

------------------------------------------------------------
Module: live_reports
------------------------------------------------------------
Purpose
- Generate Phase 2 live-data reports with explicit accounting contract and separated pre-grid vs grid-level rejection counts.

Key function
- write_live_report(report_dir, summary, signals, rejections, groups, safety, replay, cache_meta=None)

Persistence
- Writes live summary/report files, likely JSON and markdown.

Operational notes
- Clear separation of rejection classes is good for diagnosing whether no signals are caused by pre-grid data quality or actual threshold failure.

Recommendations
- Keep report machine-readable first; markdown derived second.

------------------------------------------------------------
Module: live_public_observer
------------------------------------------------------------
Purpose
- Phase 2 live public observer CLI.

Key functions
- _branch_ok()
- main()

Config/entry behavior inferred
- Enforces branch guard.
- Uses live_market_discovery/live_capture/signal/reports.
- Likely accepts duration, poll interval, market slug, report/data dirs, threshold/lookback grids.

Security/operational notes
- Public observer only.
- No keys/orders/execution.

Recommendations
- Make safety_checks mandatory at startup.
- Explicitly refuse to run if any banned env var is present, even if unused.

------------------------------------------------------------
Module: live_observation_campaign
------------------------------------------------------------
Purpose
- Phase 2B campaign runner to orchestrate multiple observer windows.

Key functions
- discover_markets()
- run_single_window(capture_dir, report_dir, duration_seconds, threshold_grid, lookback_grid=None, market_slug=None, max_markets=1)
- build_campaign_summary(campaign_id, windows, markets_observed, start_time, end_time, params, safety)
- write_campaign_report(report_dir, summary, windows)
- main()

Data flow
- Discovers active markets.
- Uses subprocess to run:
  - python -m examples.strategies.polymarket_btcusd_arb.live_public_observer
- Aggregates individual window results into campaign summary/report.

Persistence
- Campaign report directory with per-window results and summary.

Operational notes
- Subprocess orchestration isolates individual windows but requires robust error capture.
- Observer-only docstring explicitly says no orders, keys, execution clients, or on-chain calls.

Recommendations
- Capture subprocess stdout/stderr into per-window logs.
- Include exact command and git commit in campaign summary.
- Do not continue campaign silently after failed windows unless marked partial.

------------------------------------------------------------
Module: observer_evidence_review
------------------------------------------------------------
Purpose
- Phase 2C evidence review and gate decision across Phase 1, Phase 2, Phase 2B outputs.

Key types/functions
- WindowRollup:
  - run_id, source, market_slug, evaluated_events, candidate_count, grid_rejection_count, rejection counts, spread/staleness rates, replay_passed, window_verdict.
- RejectionRollup:
  - totals and rejection counts by reason.
- GateDecision:
  - gate, reason, evidence_summary, windows_analyzed, candidates_total, spread_dominated, replay_all_passed, limitations.
- aggregate_evidence(report_dir, data_dir)
- compute_gate_decision(valid_live_rollups, agg)
- write_evidence_report(...)
- run_evidence_review(report_dir=None, data_dir=None)

Allowed gate decisions
- ARCHIVE_REJECTED_FOR_CURRENT_LIVE_CONDITIONS
- CONTINUE_OBSERVER_ONLY
- ALLOW_PHASE_3_RUST_HOTPATH

Data flow
- Reads existing JSON summaries from backtest/live/campaign report/data dirs.
- Aggregates rejection rates and replay statuses.
- Computes gate decision.

Security/operational notes
- Docstring explicitly says no orders, keys, execution, on-chain.
- Decision gate could allow “Phase 3 Rust hotpath” but not actual trading by itself.

Recommendations
- Require minimum live windows and replay_all_passed before any promotion.
- Make “ALLOW_PHASE_3_RUST_HOTPATH” require human approval and separate implementation review.

------------------------------------------------------------
Module: safety_checks
------------------------------------------------------------
Purpose
- Static observer-only guard over Python source tree.

Key functions
- check_path(path)
- assert_observer_only(path)

Checks
- Parses .py files excluding __pycache__.
- Flags imports from nautilus_trader.live.
- Flags banned imports/constructors/calls.
- Flags banned env reads via os.environ/getenv-like patterns.

Operational notes
- In current directory, source .py files are missing, so check_path over this path would not inspect the implementation represented by .pyc. This materially weakens the safety guarantee.

Recommendations
- Restore source files immediately; do not rely on bytecode-only modules for safety-critical observer assertions.
- Extend checker to fail if expected source modules are absent but bytecode exists.

------------------------------------------------------------
Module: spread_regime
------------------------------------------------------------
Purpose
- Market-structure study: determine whether Polymarket BTC UpDown books are ever tight/actionable enough for a fair-probability strategy.

Key constants
- Spread thresholds bps: 20, 40, 80, 100, 200, 500.
- TTE buckets: 0-30s, 30-60s, 60-180s, 180-300s, 300-600s, 600s+.
- Volatility buckets: 0-5bps, 5-20bps, 20-50bps, 50-100bps, 100+bps.
- Time-of-day UTC buckets: 00-06, 06-12, 12-18, 18-24.
- Polymarket token bounds: min 0.01, max 0.99.
- Boundary definitions: bid <=0.02, ask >=0.98 treated as exchange-bound.

Key types/functions
- QuoteQuality constants:
  - TWO_SIDED_BOOK
  - EXCHANGE_BOUND_TWO_SIDED_BOOK
  - ONE_SIDED_BOOK
  - EMPTY_BOOK
  - FALLBACK_MIN_MAX
  - MISSING_BOOK
  - INVALID_BOOK
- classify_quote_quality(...)
- is_actionable_two_sided(quote_quality)
- SpreadEvent
- compute_spread_bps(best_bid, best_ask)
- compute_spread_abs(best_bid, best_ask)
- assign_tte_bucket(tte_ns)
- assign_time_of_day_bucket(ts_event_ns)
- assign_volatility_bucket(vol_bps)
- compute_percentile(sorted_values, percentile)
- compute_spread_bucket_counts(spreads)
- SpreadRegimeSummary
- classify_verdict(summary)
- classify_verdict_reason(summary)
- compute_summary_from_events(events, run_id, input_capture_dirs)

Data flow
- Converts quote events to SpreadEvent.
- Computes spread distribution and quality counts.
- Separates real but non-actionable exchange-bound 0.01/0.99 books from synthetic fallback.
- Classifies verdicts:
  - needs more data
  - structurally too wide
  - has tight windows
  - etc.

Operational notes
- Strong diagnosis discipline: “real CLOB orders at exchange bounds” are not treated as actionable liquidity.
- Actionable two-sided means only real TWO_SIDED_BOOK away from min/max bounds.

Recommendations
- Make exchange-bound classification central in signal generator too, not only reports.
- Add depth-weighted actionable thresholds, not just bid/ask spread.

------------------------------------------------------------
Module: spread_regime_reports
------------------------------------------------------------
Purpose
- Generate CSV/Markdown/JSON reports from spread_regime analysis.

Key functions
- write_all_reports(summary, events, output_dir)
- _write_aggregate_csvs(events, output_dir)
- _write_markdown_report(summary, events, verdict, reason, output_dir)
- _quote_quality_audit_text(summary)

Persistence
- summary.json
- spread_events.csv
- aggregate CSVs by TTE/time/volatility
- report.md

Security/operational notes
- Report safety fields include no_orders, no_keys, no_execution_client_imports, no_on_chain_calls.

Recommendations
- Keep quote-quality audit in JSON too, not just markdown text.
- Add raw counts and denominators for every percentage field.

------------------------------------------------------------
Module: run_spread_regime_study
------------------------------------------------------------
Purpose
- CLI for spread regime study over captured live observer data.

Entry point
- main()

Key function
- load_observer_captures(data_dir)

CLI/configs
- --data-dir default data/polymarket_btcusd_arb/live_observer
- --report-dir default reports/polymarket_btcusd_arb/spread_regime
- --skip-branch-check

Branch
- Expected branch constant:
  - polymarket-btc-updown-spread-regime-v1 / branch check string includes polymarket-btc-updown-spread-regime.

Data flow
1. Load metadata.json and polymarket_events.jsonl from capture dirs.
2. Filter poly_quote events.
3. Build SpreadEvent with TTE, best bid/ask, mid, spread, depth, Binance fields, stale flags.
4. compute_summary_from_events.
5. classify_verdict.
6. write_all_reports.

Operational notes
- Exits if no spread events found.
- Observer-only status printed.

Recommendations
- Treat missing metadata per capture as explicit warning artifact.
- Include number of skipped malformed rows.

------------------------------------------------------------
Module: run_duration_spread_probe
------------------------------------------------------------
Purpose
- Corrected discovery/probe for BTC UpDown products across 5m, 15m, 1h, 4h.

Entry point
- main()

Branch
- polymarket-btc-updown-duration-discovery-fix-v2

Key functions
- classify_duration_verdict_v2(...)
- classify_overall_verdict_v2(...)
- group_events_by_duration(events, duration_map)
- compute_duration_summary(groups)
- write_reports_v2(...)
- _write_report_md_v2(...)

CLI/configs inferred
- --durations
- --poll seconds / observation controls
- --report-dir
- --skip-branch-check
- Known slug validation options likely exist.

Data flow
1. Discover/validate duration markets.
2. Poll quotes from active market tokens.
3. Classify quote quality via spread_regime.
4. Group SpreadEvents by duration.
5. Compute per-duration summary.
6. Classify duration and overall probe verdict.
7. Write JSON/CSV/Markdown reports.

Operational notes
- Correct taxonomy separates product existence, active market availability, book poll success, and actionable two-sided liquidity.
- Treats Chainlink reference source as unsupported for this Binance-referenced phase.

Recommendations
- Keep known-slug validation results separate from active-live observations.
- Require multiple markets per duration before generalizing.

------------------------------------------------------------
Module: run_1h_quote_lifecycle_observer
------------------------------------------------------------
Purpose
- Specialized observer for 1h BTC UpDown quote lifecycle and actionability across pre-start/in-lifecycle/expired states.

Key inferred behavior
- Discovers/validates 1h markets.
- Waits/polls around lifecycle windows.
- Polls Polymarket CLOB book.
- Classifies quote quality.
- Computes actionable stats:
  - actionable_two_sided_book_count
  - actionable_two_sided_book_rate
  - first/last actionable timestamps
  - max_contiguous_actionable_seconds
- Tracks lifecycle buckets/states:
  - pre-start snapshots
  - in-lifecycle snapshots
  - expired snapshots
  - lifecycle coverage seconds/rate
- Classifies verdict with forbidden verdict guard.

Persistence
- Writes summary, snapshots, market list, book poll failures, markdown/CSV/JSON reports.

Operational notes
- Explicit limitations include:
  - Observer-only, no orders/keys.
  - Public API only.
  - Product existence ≠ active market availability.
  - Active market availability ≠ book poll success.
  - Book poll success ≠ actionable liquidity.
  - Active/open trading ≠ event lifecycle started.
  - 4h Chainlink hypothesis parked.
  - Single 1h market observed is not global sample.
  - Observation window may not cover full lifecycle.

Recommendations
- Keep this as market-structure monitoring, not strategy validation.
- Promote only after multiple full lifecycle windows with actionable liquidity.

============================================================
polymarket_btcusd_arb tests
============================================================

Only bytecode test artifacts are present. Test source is missing. Test names indicate coverage for:
- test_backtest_smoke
- test_baseline
- test_binance_data
- test_config
- test_data_cache
- test_forward_returns
- test_gates
- test_live_observation_campaign
- test_live_public_observer
- test_observer_evidence_review
- test_polymarket_data
- test_reports
- test_safety_checks
- test_signal_generator
- test_spread_regime
- test_duration_spread_probe
- test_1h_quote_lifecycle_observer
- parity_tests/test_probability_parity
- parity_tests/test_settlement_parity

Test audit notes
- The breadth is good, but bytecode-only tests are not maintainable or reviewable.
- Safety tests cannot meaningfully prove the implementation without source.

Recommendations
- Restore all test .py files.
- Add CI check that fails if .pyc exists without corresponding .py for strategy modules.
- Add tests for missing-source safety failure.

============================================================
AUDIT: examples/strategies/mcpt-main
============================================================

Overall purpose
- Standalone Monte Carlo Permutation Test examples for simple crypto trading strategies on hourly BTCUSD parquet data.
- Not integrated with NautilusTrader strategy/runtime.
- Research scripts assess whether in-sample/walk-forward strategy performance beats permuted OHLC paths.

External services
- None.
- Local data dependency: BTCUSD3600.pq and, in bar_permute demo, ETHUSD3600.pq.

Persistence
- Reads local parquet files.
- Does not write persistent files.
- Displays matplotlib charts.

Security/operational posture
- No keys, no orders, no exchange clients, no network.
- Scripts are research-only.
- However, most scripts execute immediately at import because they lack main guards.

Tests
- No test files found under mcpt-main.

------------------------------------------------------------
Module: bar_permute.py
------------------------------------------------------------
Purpose
- Generate permutation-resampled OHLC bars preserving selected distributional properties and multi-market cross-asset alignment.

Key function
- get_permutation(ohlc, start_index=0, seed=None)

Data flow
- Accepts either a single OHLC DataFrame or list of aligned OHLC DataFrames.
- Requires columns: open, high, low, close.
- Converts OHLC to logs.
- Keeps bars before start_index and the start bar fixed.
- Computes:
  - open relative to previous close
  - high/low/close relative to current open
- Shuffles intrabar relative high/low/close together with one permutation.
- Shuffles overnight/interbar open gaps separately.
- Reconstructs OHLC path from permuted relative components.
- For multiple markets, uses same permutations across markets to preserve correlation structure approximately.
- Returns a DataFrame or list of DataFrames.

Entry point
- __main__ demo:
  - Reads BTCUSD3600.pq and ETHUSD3600.pq.
  - Filters 2018-2019.
  - Prints return stats and BTC/ETH correlations.
  - Plots cumulative log returns.

Operational notes
- Uses np.random.seed(seed), mutating global RNG.
- Does not validate high >= open/close >= low after reconstruction; relative component permutation can produce odd candles if original components are inconsistent.
- Uses assert for validation; asserts can be disabled with -O.

Recommendations
- Use numpy Generator instead of global seed.
- Replace asserts with explicit ValueError.
- Validate OHLC positivity and required columns.
- Add tests for single/multi-market shape, index preservation, reproducibility, and no NaNs after start.

------------------------------------------------------------
Module: donchian.py
------------------------------------------------------------
Purpose
- Define and optimize a Donchian breakout signal, plus walk-forward optimization.

Key functions
- donchian_breakout(ohlc, lookback)
  - Uses rolling max/min of close over lookback-1 shifted by 1.
  - Signal 1 when close > prior upper, -1 when close < prior lower.
  - Forward-fills signal.
- optimize_donchian(ohlc)
  - Computes next-bar log return.
  - Tests lookback 12 through 168.
  - Maximizes profit factor = positive returns sum / abs(negative returns sum).
  - Returns best_lookback, best_pf.
- walkforward_donch(ohlc, train_lookback=24*365*4, train_step=24*30)
  - Re-optimizes on trailing train_lookback window every train_step bars.
  - Applies chosen Donchian signal out-of-sample until next retrain.

Entry point
- __main__:
  - Reads BTCUSD3600.pq.
  - Filters 2016-2019.
  - Optimizes lookback in-sample.
  - Plots cumulative strategy log return.

Data flow
- close -> breakout signal -> next-bar returns -> PF.

Operational notes
- No transaction costs, slippage, spread, fees, leverage constraints, or position sizing.
- Profit factor division can divide by zero if no losses.
- Initial signal NaNs are forward-filled but can remain NaN before first breakout.
- Lookback optimization is in-sample and multiple-comparison prone.

Recommendations
- Add costs and realistic fill assumptions.
- Add safe PF calculation.
- Return pandas Series from walkforward with index instead of raw ndarray.
- Add tests for no lookahead: shifted rolling bounds are correct, but tests should prove it.

------------------------------------------------------------
Module: moving_average.py
------------------------------------------------------------
Purpose
- Minimal moving-average crossover example on BTCUSD hourly data.

Behavior
- Executes at import:
  - Reads BTCUSD3600.pq.
  - Computes fast 10-bar and slow 30-bar moving averages of close.
  - Signal = 1 when fast > slow, else 0.
  - Computes next-bar log return and strategy_return.
  - Computes profit_factor and sharpe_ratio.

Entry points
- None guarded; top-level script only.

Data flow
- local parquet -> rolling averages -> long/flat signal -> next-bar returns -> PF/Sharpe variables.

Operational notes
- No plotting despite importing matplotlib.
- No print/output.
- Importing module requires local parquet and performs computation immediately.
- No fees/spread/slippage.
- Sharpe is unannualized and can divide by zero.

Recommendations
- Add main guard and functions.
- Remove unused matplotlib import or plot/report intentionally.
- Add costs and annualization if metrics are used.

------------------------------------------------------------
Module: tree_strat.py
------------------------------------------------------------
Purpose
- Decision-tree classifier toy strategy predicting 24-hour BTC direction from lagged log-return features.

Key functions
- train_tree(ohlc)
  - Features:
    - diff6 = log close diff over 6 bars
    - diff24
    - diff168
  - Target:
    - sign of 24-hour forward log return, transformed from -1/1 to 0/1.
  - Uses DecisionTreeClassifier(min_samples_leaf=5, random_state=69).
- tree_strategy(ohlc, model)
  - Computes same features.
  - Predicts class.
  - Reindexes predictions to full OHLC index.
  - Signal = 1 if prediction > 0 else -1.
  - Computes next-bar log-return PF.

Entry point
- __main__:
  - Reads BTCUSD3600.pq.
  - Trains on 2016-2019.
  - Prints in-sample PF.
  - Plots cumulative returns.

Data flow
- close -> lag features -> tree classifier -> long/short signal -> next-bar returns -> PF.

Operational notes
- Comment says “This is trash :)” and code is toy/research.
- Serious lookahead/label mismatch risk:
  - Target is 24-hour forward direction, but strategy performance uses next-bar return.
  - In tree_strategy, dropped-feature NaNs are reindexed, then np.where(NaN > 0, 1, -1), making missing early predictions short.
- No train/test split inside helper; main is in-sample.
- No costs, slippage, position sizing.
- Decision tree can overfit heavily.

Recommendations
- Fix NaN signal handling.
- Align prediction horizon and return horizon.
- Add walk-forward or purged split.
- Add costs and permutation testing only after horizon consistency is fixed.

------------------------------------------------------------
Module: insample_donchian_mcpt.py
------------------------------------------------------------
Purpose
- In-sample Monte Carlo Permutation Test for optimized Donchian strategy.

Behavior
- Executes at import.
- Reads BTCUSD3600.pq.
- Filters 2016-2019.
- Computes real best Donchian lookback/PF.
- Runs n_permutations=1000:
  - Permutes training OHLC using get_permutation.
  - Re-optimizes Donchian on permuted path.
  - Counts permuted PF >= real PF.
- Computes p-value = perm_better_count / n_permutations.
- Plots histogram of permuted PFs with real PF vertical line.

External dependencies
- tqdm, matplotlib, pandas, numpy.

Operational notes
- No seed passed to get_permutation, so results are non-reproducible.
- Top-level execution makes import expensive and side-effectful.
- p-value count starts at 1 and loop runs range(1, n_permutations), giving standard plus-one-ish behavior but denominator 1000 while only 999 permutations are appended.
- No costs.

Recommendations
- Add CLI/main and seed.
- Persist p-value inputs/results if used in reports.
- Add costs before MCPT conclusion.

------------------------------------------------------------
Module: insample_tree_mcpt.py
------------------------------------------------------------
Purpose
- In-sample MCPT for toy decision-tree strategy.

Behavior
- Executes at import.
- Reads BTCUSD3600.pq.
- Filters 2016-2019.
- Trains real tree and computes real in-sample PF.
- Runs n_permutations=1000:
  - Permutes OHLC.
  - Trains tree on permuted OHLC.
  - Computes permuted in-sample PF.
  - Counts permuted PF >= real PF.
- Plots permuted PF histogram.

Operational notes
- Inherits tree_strat horizon/NaN/cost issues.
- Non-reproducible due no seed.
- Top-level execution expensive.
- MCPT of an already flawed metric/strategy can provide misleading confidence.

Recommendations
- Fix tree strategy first.
- Add seed and main guard.
- Use out-of-sample/walk-forward evaluation.

------------------------------------------------------------
Module: walkforward_donchian_mcpt.py
------------------------------------------------------------
Purpose
- Walk-forward MCPT for Donchian strategy.

Behavior
- Executes at import.
- Reads BTCUSD3600.pq.
- Filters 2016-2020.
- Computes next-bar returns.
- train_window = 4 years of hourly bars.
- Computes real walk-forward Donchian signal.
- Computes real walk-forward PF.
- Runs n_permutations=200:
  - get_permutation(df, start_index=train_window), preserving training window and permuting after it.
  - Computes permuted walk-forward signal/PF.
  - Counts permuted PF >= real_wf_pf.
- Plots histogram.

Operational notes
- Better than in-sample scripts because it attempts walk-forward evaluation.
- But permutation starts after training window while walkforward_donch also retrains on windows containing permuted data after the first window; concept is plausible but should be documented/tested.
- No costs.
- Non-reproducible due no seed.
- Top-level expensive execution.

Recommendations
- Add reproducible seeds and CLI.
- Use costs.
- Add benchmark random/long-only baselines.
- Add tests for train window boundaries and no lookahead.

============================================================
Cross-cutting recommendations
============================================================

Polymarket BTC/USD arb
1. Restore all .py source files and test source files. Bytecode-only research code is not reviewable or safe enough.
2. Add a CI/source-integrity check: fail if __pycache__ module exists without matching .py.
3. Treat current polymarket_btcusd_arb as non-production/non-promotable until source is restored and tests run from source.
4. Preserve observer-only posture. No order path should be added until:
   - Source restored.
   - Safety checker passes over source.
   - Replay parity passes.
   - Multiple live windows show actionable non-boundary two-sided books.
   - Human approval for any Phase 3/Rust hotpath.
5. Strengthen liquidity gating:
   - Use QuoteQuality.TWO_SIDED_BOOK only.
   - Exclude exchange-bound 0.01/0.99 books from signals, not only reports.
   - Add depth and duration-contiguity requirements.
6. Strengthen statistical gating:
   - Raise min_events.
   - Add confidence intervals/permutation correction over grid.
   - Require out-of-window validation.
7. Make all external API requests robust:
   - retries/backoff
   - request latency capture
   - rate-limit handling
   - raw payload audit hashes.
8. Keep derivative/perp/funding/liquidation signals observer-only and non-execution, consistent with UK retail constraints.

mcpt-main
1. Add main guards to all scripts; avoid expensive import side effects.
2. Add reproducible seeds.
3. Add tests.
4. Add fees/spread/slippage before interpreting PF/p-values.
5. Fix tree strategy horizon/NaN issues before any MCPT claims.
6. Convert scripts into functions/CLIs that accept data path/date windows/configs.
7. Do not treat MCPT outputs as NautilusTrader strategy performance; these are standalone research examples.
```

---

## Appendix D — Detailed source-derived sub-audit: venue_agnostic_signal_observer and top-level strategy tools

```text
Source-only audit completed for:
- /mnt/nasirjones/py/nautilus_trader/examples/strategies/venue_agnostic_signal_observer/**/*.py
- top-level /mnt/nasirjones/py/nautilus_trader/examples/strategies/*.py

I did not read README/docs/report markdown. I used code discovery/AST/source inspection only. No files were created or modified.

High-level finding:
This tree is a research/observer-only signal-measurement suite for venue-agnostic crypto market microstructure studies. It is not integrated with Nautilus execution components in these modules and repeatedly asserts no orders/no credentials/no live trading. It ingests public CEX/DEX market data, stores lightweight JSONL/CSV artifacts, generates signal events, evaluates forward returns after fee/slippage/buffers, applies candidate gates, and writes reports.

External services:
- Binance spot REST: https://api.binance.com/api/v3/klines
- Kraken REST OHLC: https://api.kraken.com/0/public/OHLC
- Coinbase Exchange REST candles: https://api.exchange.coinbase.com/products/{symbol}/candles
- Binance spot WS: wss://stream.binance.com:9443/ws/{streams}
- Kraken spot WS: wss://ws.kraken.com and wss://ws.kraken.com/v2
- Coinbase WS: wss://ws-feed.exchange.coinbase.com
- Binance USD-M futures WS: wss://fstream.binance.com/market/stream
- Binance futures open interest REST: https://fapi.binance.com/fapi/v1/openInterest
- DEX Screener REST: https://api.dexscreener.com/latest
- GeckoTerminal REST: https://api.geckoterminal.com/api/v2

Environment variables:
- No meaningful env-var driven configuration found in the audited source. run_trade_flow_impulse imports os but no os.getenv/os.environ usage was detected in source summaries.
- No API key or secret env vars are required by the audited modules.

Persistence:
- JSONL tick/event/result files.
- CSV summaries/export series.
- JSON summary/rejection/manifest files.
- Markdown reports are written by several code paths, but I did not read any existing markdown reports.
- Default output paths are mostly relative: data/... and reports/...
- No database persistence found.
- No order/account persistence found.

Top-level examples/strategies/*.py

1. volatility_gate.py
Purpose:
- Public Kraken OHLC volatility diagnostic/gate.
- Computes BTC/ETH recent range/freshness/acceleration and a capture-permission style diagnostic.

Entry points:
- Executable module with main guard.
- compute_hourly_gate() fetches and prints JSON diagnostics.

Configs/env:
- Hardcoded Kraken OHLC REST URL.
- fetch_ohlc(pair, interval, count).
- No env vars.

Data flow:
- Fetches BTC/USD and ETH/USD OHLC via urllib.
- Parses Kraken response.
- Computes:
  - simple range
  - 3h range
  - 1h ranges
  - freshness
  - fast diagnostic from 1m bars
- Emits JSON to stdout.

External services:
- Kraken REST public OHLC.

Persistence:
- None except stdout.

Tests:
- test_volatility_gate.py covers range calculations, freshness, fast diagnostic.
- test_volatility_gate_capture_perm.py mocks fetch paths and validates capture permission serialization/safety.

Security/ops:
- Public endpoint only, no credentials/orders.
- Uses urllib without broader retry/backoff; network errors should be expected.
- Operationally suitable as diagnostic gate, not execution.

Recommendations:
- Add timeout/backoff/error schema to fetch_ohlc.
- Make pair/interval/count CLI-configurable instead of fixed compute_hourly_gate.
- Keep this as gating/diagnostic only unless wired into a larger observer with explicit safety boundaries.

2. research_report_miner.py
Purpose:
- Top-level report miner for report artifacts under reports/ directories, producing unified research status summaries.

Entry points:
- main() CLI via argparse.
- mine_reports(), format_rejected_md(), format_bps_gate_table().

Configs/env:
- CLI expects reports_dir.
- No env vars.

Data flow:
- Walks report directories.
- Parses JSON/JSONL files.
- Infers project/study/verdict/metrics.
- Writes research_status_summary.json/csv/md.

External services:
- None.

Persistence:
- Writes summary JSON/CSV/MD in reports_dir.

Tests:
- Similar functionality tested inside venue_agnostic_signal_observer/tests/test_research_report_miner.py, though that test imports names from another miner module and appears partially stale/mismatched against current package-local miner names.

Security/ops:
- Reads arbitrary local report files; safe from network/order standpoint.
- Writes markdown; output overwrite behavior should be explicit if important.

Recommendations:
- Consolidate with package-local research_report_miner.py to avoid duplicate miners.
- Add schema/version field to output.
- Add robust malformed-file reporting.

Top-level tests:
7. test_volatility_gate.py
Purpose:
- Unit tests for volatility_gate range/freshness/diagnostic calculations.
Entry points:
- pytest functions.
Persistence/external:
- None.
Notes:
- Good deterministic coverage for core computations.

8. test_volatility_gate_capture_perm.py
Purpose:
- Tests capture permission logic and mocked Kraken fetch scenarios.
Entry points:
- pytest functions.
External:
- Uses unittest.mock.patch, no real network expected.
Security:
- Includes safety test scanning no trading patterns.
Recommendations:
- Keep safety scan aligned with future names; string scans can produce false positives/negatives.

venue_agnostic_signal_observer package modules

5. __init__.py
Purpose:
- Empty/minimal package marker.
Entry points/config/data:
- None.

6. __main__.py
Purpose:
- Package entrypoint forwarding to run_lead_lag.main.
Entry points:
- python -m examples.strategies.venue_agnostic_signal_observer
Configs:
- Same as run_lead_lag.
Recommendation:
- Document/consider whether default package invocation should still run bar lead-lag rather than the newer tick workflows.

7. config.py
Purpose:
- Dataclass config objects for bar/signal observer.
Key classes:
- Horizon(name, seconds)
- FeeModel(fee_bps, slippage_bps, quote_mismatch_buffer_bps), total_cost_bps()
- SignalSourceConfig, ObserverConfig, LeadLagConfig
Entry points:
- Imported by observer/runners/tests.
Configs:
- Defaults include output_dir reports/signal_observer.
- No env vars.
Data flow:
- Pure configuration.
Persistence/external:
- None.
Tests:
- test_all.py, test_synthetic_fixtures.py, test_lead_lag_pipeline.py.
Recommendations:
- Use a single LeadLagConfig definition; there is also LeadLagConfig in lead_lag.py.
- Add validation methods for nonnegative fees/horizons/thresholds.

8. models.py
Purpose:
- Dataclasses for bar-level signal observation and summaries.
Key classes:
- SignalEvent
- ForwardReturnResult
- HorizonSummary
- SignalTypeSummary
- SignalEvaluationSummary
Entry points:
- to_dict/to_json/from_dict helpers.
Data flow:
- SignalEvent -> ForwardReturnResult -> SignalEvaluationSummary.
Persistence:
- JSON serializable output models.
Security/ops:
- No execution.
Recommendations:
- Add schema version fields.
- Validate finite numeric values on construction or serialization.

9. tick_models.py
Purpose:
- Lightweight tick, signal, and forward-return dataclasses independent of Nautilus core models.
Key classes:
- TradeTickLite
- QuoteTickLite
- TickSignalEvent
- TickForwardReturn
Entry points:
- to_dict/to_json/from_dict methods, QuoteTickLite.mid/spread_bps.
Data flow:
- Used by capture, tick store, event studies, impulse detectors.
Persistence:
- JSONL-friendly dicts.
Tests:
- test_tick_lead_lag_pipeline.py and many strategy tests.
Recommendations:
- Add explicit schema_version and normalize side enum.
- Guard against non-finite price/size in from_dict.

10. derivatives_models.py
Purpose:
- Lightweight derivatives-source data/event/result models.
Key classes:
- DerivativeTradeTick
- OpenInterestSnapshot
- FundingSnapshot
- DerivativeImpulseEvent
- DerivativeLeadLagResult
Data flow:
- Derivative ticks/OI/funding -> impulse events -> forward result.
Persistence:
- JSON serializable.
Tests:
- test_derivatives_lead_lag.py, test_derivatives_spot_lead_lag.py.
Security/ops:
- Research source only; no derivatives execution in code audited.
Recommendations:
- Preserve UK-retail boundary: derivatives as signal source only.
- Add instrument_type/source_market metadata consistently to outputs.

11. dex_models.py
Purpose:
- DEX pool snapshot, DEX dislocation event, and DEX/CEX forward result models.
Key classes:
- DexPoolSnapshot
- DexDislocationEvent
- DexCexForwardResult
Helpers:
- _maybe_float, _maybe_int.
Data flow:
- DEX API payloads -> DexPoolSnapshot -> DexDislocationEvent -> CEX forward result.
Tests:
- test_dex_cex_dislocation.py.
Recommendations:
- Add chain/address validation and source timestamp quality flags.
- Make stale-data buffers explicit in all summaries.

12. symbol_aliases.py
Purpose:
- Canonical symbol resolution/comparison.
Key class:
- CanonicalSymbol
Functions:
- resolve_symbol, symbols_match, same_asset, same_quote, quote_mismatch.
Data flow:
- Used to prevent same-asset mistakes, quote mismatch detection, CEX/DEX mapping.
Tests:
- test_symbol_aliases.py.
Recommendations:
- Expand alias table carefully; unknown symbol behavior should remain conservative.
- Add quote mismatch severity metadata to evaluation outputs.

13. csv_normalizer.py
Purpose:
- Load OHLC CSV, resample to grid, align venues.
Functions:
- load_ohlc_csv
- resample_to_grid
- align_venues
Data flow:
- Raw CSV -> normalized timestamp/price rows -> aligned source/target rows.
Persistence:
- Reads CSV only.
Tests:
- No direct test noted; align behavior also covered in data_adapters regression.
Recommendations:
- Add tests for malformed CSV, duplicate timestamps, time zones.
- Avoid forward/backfill before first observation; ensure same invariant as data_adapters.

14. data_loading.py
Purpose:
- Load bar/signals CSV and generate synthetic datasets.
Functions:
- load_bars_from_csv
- load_signals_from_csv
- generate_synthetic_lead_lag
- generate_synthetic_noise
- generate_synthetic_data
Persistence:
- Reads local CSV.
Tests:
- test_all.py, test_synthetic_fixtures.py, test_lead_lag_pipeline.py.
Recommendations:
- Consolidate duplicate load_signals_from_csv with signals.py or clarify different schemas.
- Add explicit CSV schema errors.

15. signals.py
Purpose:
- CSV signal loading plus simple cross-market signal generation from bar data.
Key class:
- CrossMarketSignalGenerator
Functions:
- load_signals_from_csv
Data flow:
- Input signals CSV or source price moves -> SignalEvent objects.
Persistence:
- Reads CSV.
Tests:
- test_all.py, test_synthetic_fixtures.py.
Operational notes:
- Handles bad strength/empty metadata per regression test.
Recommendations:
- Add stronger metadata JSON validation and source column schema versioning.

16. forward_returns.py
Purpose:
- Bar-level forward-return evaluator.
Functions:
- get_entry_price
- find_price_at_or_after
- compute_forward_return
- compute_excursions
- evaluate_signal
Data flow:
- SignalEvent + target bars + Horizon/FeeModel -> ForwardReturnResult.
Persistence/external:
- None.
Tests:
- test_all.py, test_lead_lag_pipeline.py, test_audit_regression.py.
Security/ops:
- Correctness-sensitive: avoids zero/non-finite entry prices per tests.
Recommendations:
- Continue enforcing no lookahead and non-finite rejection.
- Add latency parameter equivalent to tick-level studies for consistency.

17. lead_lag.py
Purpose:
- Bar-level lead-lag signal generation and random baseline.
Key class:
- LeadLagConfig
Functions:
- generate_lead_lag_signals
- generate_random_baseline
Data flow:
- Source timestamps/prices -> SignalEvent list.
Tests:
- test_lead_lag_pipeline.py, audit regression.
Recommendations:
- Remove duplicate/conflicting LeadLagConfig with config.py or rename one.
- Add explicit cooldown/window boundary docs in code comments/tests.

18. observer.py
Purpose:
- Orchestrates bar-level signal observer.
Key class:
- SignalObserver
Functions:
- _build_summary and percentile helpers.
Data flow:
- Config -> load/generate signals and bars -> evaluate_signal for horizons -> build summary -> write_outputs.
Persistence:
- Uses reports.write_outputs to write signal_events.jsonl, forward_returns.jsonl, summary.json/csv, rejections.
Tests:
- test_all.py, test_synthetic_fixtures.py, test_bug_audit_pass2.py.
Recommendations:
- Add run manifest with exact config and input file hashes.
- Ensure output dirs are clearly separated by run ID to avoid accidental overwrites.

19. reports.py
Purpose:
- Bar observer report writers.
Functions:
- write_outputs, _write_jsonl, _write_json, _write_horizon_csv, _write_signal_type_csv.
Persistence:
- Writes:
  - signal_events.jsonl
  - forward_returns.jsonl
  - summary.json
  - summary.csv
  - by_signal_type.csv
  - rejections.json
Tests:
- test_all.py, test_lead_lag_pipeline.py.
Recommendations:
- Atomic writes would reduce partial report risk.
- Include schema_version and generated_at.

20. data_fetcher.py
Purpose:
- Public OHLC fetch helpers for Binance/Kraken and CSV writer.
Functions:
- fetch_binance_klines
- fetch_kraken_ohlc
- write_ohlc_csv
External:
- Binance spot klines REST.
- Kraken OHLC REST.
Persistence:
- Writes OHLC CSV.
Tests:
- Regression test for empty CSV header.
Security/ops:
- Public unauthenticated only.
- Uses httpx and time pagination; rate-limit sleeps present.
Recommendations:
- Add client timeout config, retry/backoff, and API error classification.
- Do not silently trust exchange response timestamps; validate monotonicity.

21. data_adapters.py
Purpose:
- Venue CSV loader/alignment plus public kline/candle downloader for Binance, Kraken, Coinbase.
Functions:
- load_venue_csv
- align_venues
- download_public_klines
- _fetch_binance
- _fetch_kraken
- _fetch_coinbase
- save_venue_csv
External:
- Binance, Kraken, Coinbase public REST.
Persistence:
- Reads/writes CSV.
Tests:
- test_bug_audit_pass2.py covers no pre-first-data backfill.
Recommendations:
- Consolidate overlap with data_fetcher.py.
- Add bounded retries/timeouts and per-venue rate-limit config.
- Normalize all timestamps to a declared unit.

22. data_download.py
Purpose:
- CLI downloader for lead-lag OHLCV data.
Entry points:
- main() with argparse and main guard.
Configs:
- --data-dir default data/lead_lag
- --days, --interval, venues/symbols inferred in code.
Data flow:
- Calls download_public_klines then save_venue_csv.
Persistence:
- Writes CSV files named with venue/symbol/interval/window.
External:
- Same as data_adapters.
Recommendations:
- Add manifest of downloaded ranges and API errors.
- Make venue/symbol lists fully CLI configurable if not already.

23. tick_store.py
Purpose:
- JSONL read/write and discovery utilities for lightweight ticks.
Functions:
- load_trades_jsonl, load_quotes_jsonl
- save_trades_jsonl, save_quotes_jsonl
- sort_by_ts, reject_stale_ticks, merge_and_sort_ticks
- tick_file_discovery
Persistence:
- Reads/writes JSONL.
Tests:
- test_tick_lead_lag_pipeline.py and cross-venue tests.
Operational:
- Discovery depends on filename pattern <tick_type>_<venue>_<symbol>_<timestamp>.jsonl.
Recommendations:
- Write atomically and fsync for capture durability.
- Capture malformed-line counts as diagnostics.
- Make filename parsing robust to symbols with underscores if needed.

24. event_study.py
Purpose:
- Tick lead-lag generation, forward-return evaluation, baseline generation, candidate gating, synthetic fixtures.
Key classes:
- TickLeadLagConfig
- TickLeadLagGenerator
Functions:
- evaluate_tick_signal
- generate_random_baseline
- evaluate_candidate_group
- generate_synthetic_positive_lead_lag_ticks
- generate_synthetic_no_edge_ticks
Data flow:
- Source ticks -> TickSignalEvent -> target ticks -> TickForwardReturn -> candidate gate.
Tests:
- test_tick_lead_lag_pipeline.py, test_cross_venue_lead_lag.py, test_cross_asset_impulse.py, audit regression.
Security/ops:
- Pure research computation, no I/O/network.
Recommendations:
- Keep no-lookahead invariants heavily tested.
- Add deterministic random baseline seed to all runner summaries.
- Separate baseline-window sampling assumptions in report outputs.

25. trade_flow_impulse.py
Purpose:
- Generates trade-flow impulse signals from trade ticks.
Key class:
- TradeFlowImpulseConfig
- TradeFlowImpulseSignalGenerator
Signal types:
- count_burst
- notional_burst
- large_trade
- signed_imbalance
Helpers:
- _infer_tick_rule_side, _finite_positive, _finite_notional.
Data flow:
- Source TradeTickLite stream -> TickSignalEvent list.
Tests:
- test_trade_flow_impulse.py, cross-asset tests, audit regression.
Security/ops:
- Direction may use tick-rule proxy if side missing; this is a research assumption.
Recommendations:
- Always surface side_source/proxy metadata in reports.
- Add venue-specific trade side normalization where raw side semantics differ.

26. derivatives_lead_lag.py
Purpose:
- Derivatives-source impulse generation and conversion to tick signal.
Key class:
- DerivativesImpulseGenerator
Function:
- impulse_to_tick_signal
Signal types:
- notional_burst
- price_shock
- signed_imbalance
Data flow:
- DerivativeTradeTick stream -> DerivativeImpulseEvent -> TickSignalEvent for spot-target forward evaluation.
Tests:
- test_derivatives_lead_lag.py and audit regression.
Security/ops:
- Research-only; derivatives used as signal sources.
Recommendations:
- Keep strong guardrails against interpreting this as derivatives execution.
- Add funding/OI integration tests if those fields become active inputs.

27. dex_adapters.py
Purpose:
- DEX Screener/GeckoTerminal API adapters and JSONL loader.
Functions:
- parse_dexscreener_pair
- fetch_dexscreener_pair
- fetch_dexscreener_pairs_by_address
- search_dexscreener_by_symbols
- fetch_geckoterminal_pool_ohlcv
- load_dex_snapshots_from_jsonl
External:
- DEX Screener, GeckoTerminal.
Persistence:
- Reads DEX snapshots JSONL.
Tests:
- test_dex_cex_dislocation.py.
Security/ops:
- Public APIs only.
- Rate-limit delay constant exists.
Recommendations:
- Add timeout/retry/backoff and source-specific stale timestamp handling.
- Treat DEX Screener search results as untrusted: validate pair/address/quote/liquidity.

28. dex_cex_dislocation.py
Purpose:
- Detects DEX/CEX dislocation-style events from DEX pool snapshots and maps them to tick-style signals.
Key class:
- DexCexDislocationDetector
Function:
- dex_event_to_tick_signal
Signal types:
- price shock, volume burst, liquidity shock style checks.
Data flow:
- DexPoolSnapshot sequence -> DexDislocationEvent -> target CEX forward return.
Tests:
- test_dex_cex_dislocation.py.
Recommendations:
- Include DEX data latency/staleness in every decision.
- Add chain-specific liquidity/volume outlier controls.

29. cross_asset_impulse.py
Purpose:
- Cross-asset spot impulse lead-lag evaluation primitives and report generation.
Key classes:
- StreamHealth
- PairKey
- PairResult
- CrossAssetVerdict
Functions:
- generate_source_impulses
- compute_overlap
- compute_verdict
- generate_markdown_report
Data flow:
- Source BTC/ETH-like trade-flow impulses -> remap target alt symbols -> target forward returns -> per-pair verdict.
Persistence:
- Writes markdown report via generate_markdown_report.
Tests:
- extensive test_cross_asset_impulse.py.
Security/ops:
- Separates long-executable vs diagnostic-only signals.
- Same-symbol skips and stream health checks are explicit.
Recommendations:
- Make all report writes optional or atomic.
- Include raw stream-health thresholds in JSON outputs, not just markdown.

30. mcpt_export.py
Purpose:
- Selects candidate groups from report outputs and exports event return series for MCPT/permutation testing.
Key class:
- GroupKey
Functions:
- is_mcpt_worthy_group
- select_mcpt_candidate_groups
- export_mcpt_candidate_series
- export_mcpt_summary
Persistence:
- Reads summary.json, signals.jsonl, forward_returns.jsonl.
- Writes candidate CSV files and mcpt_export_summary.json.
Tests:
- test_mcpt_export.py and run_mcpt_export regression.
Security/ops:
- Offline local file processing only.
Recommendations:
- Include provenance fields: source report dir, input hashes, selected gate values.
- Reject non-finite values aggressively, which tests already cover.

31. research_report_miner.py inside package
Purpose:
- Package-local report miner extracting ResearchRecord rows from JSON/JSONL/CSV/MD.
Key class:
- ResearchRecord
Functions:
- mine_reports
- output writers
- main CLI
Persistence:
- Reads report artifacts, including .md parser capability.
- Writes research_status_summary.json/csv/md.
Tests:
- test_research_report_miner.py appears to expect older/different symbols in places; verify compatibility.
Important note:
- I did not read existing markdown reports, but source contains markdown parsing/writing code.
Recommendations:
- Reconcile with top-level research_report_miner.py and tests.
- If docs/reports are stale, consider disabling MD ingestion by default or flagging it as lower-trust.

Runner/CLI modules

32. run_signal_observer.py
Purpose:
- CLI wrapper for SignalObserver.
Entry points:
- main() with argparse and main guard.
Configs:
- --synthetic
- --signals-csv
- --bars-csv
- --fee-bps default 5.0
- --slippage-bps default 1.0
- --quote-mismatch-buffer-bps default 0.0
- --out default reports/signal_observer
Data flow:
- Builds ObserverConfig and runs SignalObserver.
Persistence:
- Writes observer reports through reports.py.
External:
- None unless input paths are user-provided local files.
Recommendations:
- Print/write resolved config manifest.
- Fail fast when neither synthetic nor required CSV paths are supplied.

33. run_lead_lag.py
Purpose:
- Bar-level lead-lag sweep runner.
Entry points:
- parse_args, run_sweep, main.
Defaults discovered:
- data dir data/lead_lag
- output reports/lead_lag_v1
- source venue BINANCE, target venue KRAKEN
- source symbols BTC/USDT,ETH/USDT,SOL/USDT
- target instruments BTC/USD,ETH/USD,SOL/USD
- windows/thresholds/horizons like 5,10,20 and 10,30,60,300.
Data flow:
- Finds CSVs, aligns venue bars, generates lead-lag/random baseline, evaluates forward returns, summarizes.
Persistence:
- Writes reports via local logic/reports.
External:
- None at run time if CSVs exist.
Recommendations:
- Make filename discovery explicit and fail loudly on ambiguous files.
- Include data coverage/overlap diagnostics in summary.

34. run_tick_capture.py
Purpose:
- Async spot tick capture from Binance/Kraken/Coinbase public websockets.
Entry points:
- main -> asyncio _main.
Key functions:
- run_binance_feed
- run_kraken_feed
- run_coinbase_feed
- _write_manifest
- _print_diagnostics
Key class:
- IncrementalJSONLWriter
- _CaptureStats
Configs/defaults:
- venues default kraken,coinbase
- symbols default BTC/USD,ETH/USD
- out data/signal_observer_ticks
Data flow:
- Subscribe to trade feeds, normalize raw messages to TradeTickLite JSONL, write manifest/diagnostics.
External:
- Binance spot WS, Kraken WS, Coinbase WS.
Persistence:
- JSONL tick files and capture manifest.
Tests:
- URL/safety behaviors indirectly via tick pipeline/capture URL tests.
Security/ops:
- Public data only, no auth/orders.
- Long-running network process; reconnect/flush behavior is operationally important.
Recommendations:
- Ensure all WS connections have bounded reconnect/backoff and failure visibility.
- Add disk-space and file rotation handling for long captures.
- Add capture manifest hashes/counts per file.

35. run_tick_lead_lag.py
Purpose:
- Tick cross-venue lead-lag sweep runner and report generator.
Entry points:
- build_parser, run_sweep, _write_outputs, generate_markdown_report, main.
Defaults:
- ticks data/signal_observer_ticks
- out reports/signal_observer_tick_lead_lag
- source coinbase, target kraken
- symbol BTC/USD
- lookbacks 1000,5000,10000,30000 ms
- thresholds 2,5,10,20 bps
- horizons 1000,2000,5000,10000,30000,60000 ms
Data flow:
- Discover/load source/target ticks -> generate TickLeadLag signals -> evaluate target forward returns -> random baseline -> candidate gate -> write JSON/CSV/MD outputs.
Persistence:
- tick_summary.json/csv, by_horizon.csv, by_asset.csv, by_venue_pair.csv, random_baseline.csv, rejections.json, tick_lead_lag_report.md.
Tests:
- test_tick_lead_lag_pipeline.py, test_cross_venue_lead_lag.py.
Security/ops:
- No order paths; tests scan for order guard.
Recommendations:
- Keep source/target overlap diagnostics mandatory.
- Add input file hashes and exact discovered filenames to summary.

36. run_trade_flow_impulse.py
Purpose:
- CLI runner for trade-flow impulse studies.
Entry points:
- build_parser, load_tick_data, run_sweep, _write_outputs, generate_markdown_report, main.
Defaults:
- ticks data/signal_observer_ticks
- out reports/trade_flow_impulse_v1
- common source/target venues and BTC/ETH symbols in parser doc/defaults.
Configs:
- signal types, lookbacks, baseline window, horizons, cooldown, fee/slippage/quote mismatch, min-events.
Data flow:
- Load source/target trade ticks -> TradeFlowImpulseSignalGenerator -> evaluate forward returns -> baseline -> candidate gate -> report.
Persistence:
- tick_summary.json/csv, by_signal_type.csv, by_venue_pair.csv, random_baseline.csv, rejections.json, trade_flow_impulse_report.md.
Tests:
- test_trade_flow_impulse.py.
Security/ops:
- No execution/order logic.
Recommendations:
- Remove unused os import if truly unused.
- Include side inference quality statistics in summary.

37. run_cross_asset_impulse.py
Purpose:
- Cross-asset spot impulse lead-lag runner.
Entry points:
- build_parser, _run, main.
Defaults:
- ticks data/cross_asset_spot_capture_v1 shown in usage.
- out reports/cross_asset_impulse_v1.
- Source BTC/ETH, targets alt spot assets, configurable venues/signal types/lookbacks/horizons/gates.
Data flow:
- Load tick data -> build stream health -> pair source/target excluding same asset -> generate source impulses -> remap to target -> evaluate forward returns -> random baseline/candidate gate -> verdict/report files.
Persistence:
- cross_asset_summary.json/csv
- cross_asset_impulse_events.jsonl
- cross_asset_forward_returns.jsonl
- cross_asset_rejections.json
- cross_asset_report.md
Tests:
- extensive test_cross_asset_impulse.py.
Security/ops:
- Public spot data only; no orders.
- Explicit diagnostic-only vs long-executable distinction.
Recommendations:
- Persist same-symbol skips and stream-health warnings in machine-readable summary.
- Add max pair/run limit safeguards for large universes.

38. collect_dex_snapshots.py
Purpose:
- Polls DEX Screener search for assets and writes DEX pool snapshots.
Entry points:
- collect_snapshots, main with argparse.
Configs:
- --symbols
- --duration-seconds
- --poll-interval-seconds
- --min-liquidity-usd
- --min-volume-1h-usd
- --out
Data flow:
- DEX Screener search -> parse DexPoolSnapshot -> append JSONL.
External:
- DEX Screener REST via dex_adapters/httpx.
Persistence:
- DEX snapshots JSONL.
Security/ops:
- Public data; no keys/orders.
Recommendations:
- Enforce minimum poll interval/rate-limit safety in parser.
- Add graceful SIGINT manifest write.
- Track duplicate pair snapshots and dropped malformed responses.

39. run_dex_cex_dislocation.py
Purpose:
- DEX/CEX dislocation research runner.
Entry points:
- _build_parser, run_sweep, evaluate_dex_events, _write_outputs, main.
Defaults:
- assets SOL,LINK,AVAX,DOGE,ADA
- target venues kraken,coinbase
- duration 600, poll interval 10
- min liquidity 500,000 USD
- min 1h volume 100,000 USD
- horizons 30000,60000,300000,900000,3600000 ms
- out reports/dex_cex_spot_dislocation_v1
Data flow:
- Collect/load DEX snapshots/events -> load target CEX ticks -> evaluate forward returns -> optional baseline -> summary/reports.
External:
- DEX APIs if collecting; otherwise local tick/snapshot files.
Persistence:
- summary.json/csv, report.md and likely events/returns/rejections.
Tests:
- test_dex_cex_dislocation.py.
Security/ops:
- No order logic; public data.
Recommendations:
- Separate collection and evaluation modes clearly to avoid network during analysis.
- Make stale DEX/CEX timestamp tolerance explicit in outputs.

40. run_derivatives_lead_lag.py
Purpose:
- CLI runner for derivatives-source lead-lag signal research.
Key class:
- DerivativesLeadLagSummary
Entry points:
- _build_parser, run_sweep, _write_outputs, main.
Defaults:
- source/target tick files required in usage.
- out reports/derivatives_lead_lag_v1.
Configs:
- source/target venue, symbol/asset, signal types, lookbacks, horizons, cooldown, fee/slippage, latency/quote buffers, min-events.
Data flow:
- Load DerivativeTradeTick source and TradeTickLite target -> generate derivative impulses -> convert to TickSignalEvent -> evaluate spot target returns -> baseline/gate -> write reports.
Persistence:
- summary.json/csv, report.md, events/returns outputs.
Tests:
- test_derivatives_lead_lag.py.
Security/ops:
- Source can be perps/futures; target should be spot. Tests include spot-spot guard and no forbidden imports.
Recommendations:
- Keep target instrument validation strict.
- Include instrument_type metadata in every result/report.
- Make UK-retail no-derivatives-execution statement machine-readable in summary.

41. run_derivatives_spot_capture.py
Purpose:
- Combined async capture for Binance perp source plus Kraken/Coinbase spot targets and optional Binance OI polling.
Entry points:
- build_parser, main.
Key functions:
- capture_binance_perp
- capture_kraken_spot
- capture_coinbase_spot
- poll_binance_oi
- compute_overlap_windows
- _with_reconnect_loop
Key class:
- StreamStats
Defaults:
- out data/derivatives_spot_capture_v2
- output filenames trades_binance_perp_..., trades_kraken_..., trades_coinbase_..., open_interest_binance_perp_...
Data flow:
- Public WS/REST streams -> normalized JSONL files -> capture_manifest with overlap proof.
External:
- Binance futures WS and openInterest REST, Kraken WS v2, Coinbase WS.
Persistence:
- JSONL trades/OI and capture_manifest.json.
Tests:
- test_capture_ws_urls.py, test_audit_regression.py, test_derivatives_spot_lead_lag.py.
Security/ops:
- Public data only, no auth/orders.
- OI polling rate-limit comments indicate conservative bounds.
- Tests check reconnect loop, session cleanup, exception logging/task surfacing.
Recommendations:
- Add hard cap on symbols * OI poll frequency.
- Add disk-space/file rotation and SIGTERM-safe manifest finalization.
- Treat Binance derivatives source as signal-only; no execution.

42. run_derivatives_spot_lead_lag.py
Purpose:
- Evaluation runner for captured derivatives-source to spot-target lead-lag.
Key classes:
- OverlapWindow
- EvalSummary
Entry points:
- build_parser, run_evaluation, write_reports, main.
Defaults:
- capture-dir data/derivatives_spot_capture_v2
- source-venues binance_perp
- target-venues kraken,coinbase
- symbols BTC/USD,ETH/USD,SOL/USD
- signal-types notional_burst,large_trade,signed_imbalance
- lookbacks 1000,5000,10000,30000 ms
- horizons 1000,2000,5000,10000,30000,60000,300000 ms
- fees 40 bps, slippage 5 bps, quote mismatch 5 bps
- out reports/derivatives_spot_lead_lag_v2
Data flow:
- Load captured JSONL -> compute overlap -> clip ticks -> classify OI buckets -> generate/evaluate signals -> summarize/write reports.
Persistence:
- summary.json/csv/report.md and supporting outputs.
Tests:
- test_derivatives_spot_lead_lag.py and audit regression for non-finite top groups.
Security/ops:
- Evaluation-only, no network.
Recommendations:
- Persist overlap windows and clipped counts in summary.
- Keep non-finite filtering in report ranking, already regression-tested.

43. run_mcpt_export.py
Purpose:
- CLI wrapper for mcpt_export candidate selection/export.
Entry points:
- build_parser, main.
Configs:
- report dir/output dir/max groups/min events/cost floor style parameters inferred from mcpt_export defaults.
Data flow:
- Read report summary/signals/forward_returns -> select groups -> write candidate CSVs and summary.
Persistence:
- Writes MCPT export summary and candidate CSVs.
Tests:
|- test_mcpt_export.py, test_bug_audit_pass2.py.
Recommendations:
|- Add dry-run mode and no-overwrite option.
|- Include explicit skip reasons for NaN/non-finite candidate stats, already covered by regression.

44. permutation_null.py
Purpose:
|- Native permutation/null test core module. Pure functions only.
Key functions:
|- is_null_worthy_group — gate: should a group be null-tested?
|- select_null_candidate_groups — pick top N candidate/near-candidate groups
|- circular_time_shift — shift timestamps preserving inter-event spacing
|- block_time_shift — shift in blocks to preserve clustering
|- compute_null_distribution — build null distribution via repeated shifts
|- run_null_test_for_group — high-level full null test for one group
Persistence:
|- No direct I/O; functions operate in-memory.
Tests:
|- test_permutation_null.py (25 tests).
Security/ops:
|- Observer-only; no network; no execution.
Recommendations:
|- Keep shift modes simple; avoid over-engineering.

45. permutation_null_gpu.py
Purpose:
|- GPU-accelerated permutation null engine using PyTorch CUDA.
Key functions:
|- check_cuda_available — verifies CUDA device availability
|- compute_null_distribution_gpu — batched null distribution kernel
|- gpu_unavailable_diagnostic — emits diagnostic verdict JSON on CUDA failure
Notes:
|- Explicit --engine gpu selection; fail-fast if CUDA unavailable (no silent fallback).
|- Chunked batching with per-chunk seeding ensures determinism.
Persistence:
|- In-memory compute only; no direct I/O.
Tests:
|- test_permutation_null_gpu.py — determinism, chunk-size invariance, CUDA-unavailable path.
Security/ops:
|- Same observer-only constraints as CPU path; no network; no orders.
Recommendations:
|- Keep batch-size configurable; lazy torch import to avoid hard dependency for CPU runs.

46. run_permutation_null.py
Purpose:
|- CLI entry point for permutation null testing.
Entry points:
|- build_parser, main.
Configs:
|- --capture-dir, --report-dir, --out, --max-groups (default 3), --iterations (default 1000), --seed (default 42), --min-events (default 30), --cost-floor-bps (default 50), --shift-mode (circular_time_shift|block_time_shift), --block-size (default 10), --engine (cpu|gpu, default cpu), --device (cuda:0), --batch-size (default 512).
Data flow:
|- Loads evaluation report → selects null-worthy groups → loads capture data → runs null distribution per group → writes JSON + markdown report.
Persistence:
|- Outputs under <report-dir>/null_test/ including null_test_summary.json, null_test_report.md, and per-group distribution JSONs.
Tests:
|- Indirect via test_permutation_null.py (core logic) and integration via CLI.
Security/ops:
|- Pure local file I/O; no network; no orders.
Recommendations:
|- Keep deterministic seeds across runs.

47. latency_diagnostics.py
Purpose:
|- Cross-venue tick-stream latency and clock-skew diagnostics.
Dataclasses:
|- StreamStats — per-(venue, symbol) tick timing
|- OverlapResult — pairwise overlap + lead-lag summary
|- LatencyDiagnosticReport — top-level report
Key functions:
|- load_stream_stats — load trades_*.jsonl and compute tick-gap stats
|- compute_cross_correlation — bucket-price cross-correlation at 250/1000/5000ms
|- compute_overlap_stats — compute overlap duration and warnings
|- compute_latency_diagnostics — assemble full report
|- format_latency_report — markdown output
|- write_latency_diagnostics — JSON + MD writer
Persistence:
|- Reads trades JSONL from capture dir; writes latency_diagnostics.json + latency_diagnostics_report.md to out_dir.
Tests:
|- test_latency_diagnostics.py (12 tests).
Security/ops:
|- Observer-only; only timing analysis; no execution gating.
Recommendations:
|- Keep bucket sizes configurable if new venues differ.

48. run_report_corpus.py
Purpose:
|- Corpus aggregation CLI — aggregate evaluation reports across multiple captures.
Entry points:
|- build_parser, main.
Configs:
|- --report-dirs (comma-separated), --out (default reports/corpus/).
Data flow:
|- Reads summary.json from each report dir → groups by (source_venue, target_venue, signal_type, lookback_ms, horizon_ms) → computes consistency statistics → writes JSON + markdown.
Hard rule:
|- Primary sort is by number of captures (consistency), NOT best-ever performance.
Persistence:
|- Outputs: corpus_aggregation.json, corpus_report.md.
Tests:
|- test_report_corpus.py (13 tests).
Security/ops:
|- Local files only; no network.
Recommendations:
|- Add per-config raw result samples for auditability.

Tests under venue_agnostic_signal_observer/tests

49. test_all.py
Purpose:
- Broad legacy/unit coverage for configs, fee model, quote mismatch, SignalEvent/ForwardReturnResult, CSV parser, CrossMarketSignalGenerator, forward returns, summary aggregation, reports, synthetic observer, no-orders.
Notes:
- Good safety baseline.
Recommendation:
- Split by module if test runtime/maintenance becomes problematic.

50. test_audit_regression.py
Purpose:
- Regression tests for previously found bugs:
  - lead-lag window reference
  - derivatives direction off-by-one
  - baseline uses real ticks
  - raw vs direction-adjusted returns
  - capture exception logging
  - large-trade direction mapping
  - reconnect/session/task exception handling
  - overlap preservation
Recommendation:
- Preserve as high-value audit suite; add issue IDs/comments if available.

51. test_bug_audit_pass2.py
Purpose:
- Second pass regression suite:
  - reject zero/non-finite entry prices
  - summary filters NaN/interpolates percentiles
  - bad strength/empty metadata CSV handling
  - align_venues no pre-first backfill
  - zero stream-health min not treated falsy
  - None gate values in cross-asset verdict/report
  - empty CSV header
  - non-finite group filtering
  - MCPT NaN skip reason
Recommendation:
- High-value data-quality correctness tests; keep mandatory.

52. test_capture_ws_urls.py
Purpose:
- Tests Binance perp URL construction, Kraken URL constants, StreamStats diagnostics.
Recommendation:
- Add Coinbase/Binance spot URL tests if capture code changes.

53. test_cross_asset_impulse.py
Purpose:
- Extensive cross-asset logic tests:
  - same-symbol skip
  - source-target mapping/direction propagation
  - no short execution distinction
  - quiet source/target gates
  - insufficient events/zero streams/subscription failures
  - rejected/candidate verdicts
  - overlap/stream health
  - parser defaults
  - no forbidden imports
Recommendation:
- Strong suite; keep safety and diagnostic tests.

54. test_cross_venue_lead_lag.py
Purpose:
- Cross-venue pair and synthetic real-like lead-lag tests using tick files.
Recommendation:
- Add multi-symbol ambiguous file discovery cases.

55. test_derivatives_lead_lag.py
Purpose:
- Derivatives models, impulse generator, no-lookahead, conservative verdict, malformed data tolerance, forbidden imports, instrument metadata, spot-spot guard, synthetic perp-to-spot.
Recommendation:
- Keep UK-regulatory constraint reflected in test names/metadata.

56. test_derivatives_spot_lead_lag.py
Purpose:
- Symbol normalization, Binance side inference, overlap windows, price range, OI bucket classification, capture data loading, safety scan, verdict logic.
Recommendation:
- Add malformed OI JSONL and missing manifest cases.

57. test_dex_cex_dislocation.py
Purpose:
- DEX models, DEX Screener parsing, detector, forward net bps, no-lookahead, outputs, candidate gate, malformed payload, report guardrails.
Recommendation:
- Add source timestamp stale/latency tests.

58. test_lead_lag_pipeline.py
Purpose:
- Bar-level lead-lag signal generation, random baseline, end-to-end report writing.
Recommendation:
- Include timezone/duplicate timestamp CSV cases.

59. test_latency_diagnostics.py
Purpose:
|- Latency and clock-skew diagnostics across venues.
Key tests:
|- StreamStats, OverlapResult, compute_cross_correlation, compute_overlap_stats
|- Format/write helpers
Persistence:
|- Reads trades JSONL; writes latency_diagnostics.json + .md.
Tests cover:
|- Overlap detection, gap statistics, cross-correlation buckets, missing data handling.
Security/ops:
|- Pure observer; no network or execution.
Recommendations:
|- Keep bucket sizes configurable if new venues differ.

60. test_permutation_null.py
Purpose:
|- Unit and integration tests for permutation_null core logic.
Coverage:
|- circular_time_shift and block_time_shift preserve event counts
|- p-value determinism with fixed seed
|- selection gates: cost-floor skip, candidate/near-candidate pick
|- null distribution shape and survival criteria
|- FAST_DIAGNOSTIC metadata preservation
|- no REJECTED verdict produced from null diagnostics
Security/ops:
|- Fast, deterministic, pure functions.

61. test_permutation_null_gpu.py
Purpose:
|- Verifies GPU engine correctness and fail-fast behavior.
Coverage:
|- Determinism with fixed seeds across chunk sizes
|- Chunk-size invariance of distribution statistics
|- CUDA-unavailable path returns GPU_UNAVAILABLE_DIAGNOSTIC
|- Engine selection routing (gpu vs cpu)
Security/ops:
|- Pure unit tests; no external I/O; mocks torch as needed.
Recommendations:
|- Mock torch.cuda.is_available in CI; test both engines.

62. test_report_corpus.py
Purpose:
|- Corpus aggregation tests.
Coverage:
|- Exact config key grouping (venue/symbol/lookback/horizon)
|- NaN/inf JSON sanitization
|- Deduplication and consistency metrics
|- Cherry-pick guard (primary sort by capture count)
|- Handling empty/missing report directories
Security/ops:
|- Local file I/O only.

63. test_mcpt_export.py
Purpose:
- MCPT skip/candidate selection, near-breakeven baseline, min events, NaN/Inf safety, CSV export, max groups, duplicate variant suppression, slugify.
Recommendation:
- Add no-overwrite behavior if implemented.

64. test_research_report_miner.py
Purpose:
- Tests report miner parsing/verdict/status formatting and full mine output.
Issue:
- The imports shown in source summary reference names like StudyResult/_parse_val/_make_result/format_bps_gate_table that align with top-level research_report_miner.py more than package-local ResearchRecord miner. This may be stale or path-dependent.
Recommendation:
- Verify test imports pass under intended PYTHONPATH; consolidate duplicate miner modules.

65. test_symbol_aliases.py
Purpose:
- Canonical symbol resolution, unknown symbols, asset/quote comparison.
Recommendation:
- Add venue-specific aliases as real captures demand.

66. test_synthetic_fixtures.py
Purpose:
- Synthetic positive/noise observer runs.
Recommendation:
- Keep deterministic seeds/fixtures.

67. test_tick_lead_lag_pipeline.py
Purpose:
- Tick models/store, signal generation, forward returns, random baseline, candidate gate, synthetic end-to-end, report output, no-order guard.
Recommendation:
- Add large JSONL streaming/performance test if capture files grow.

68. test_trade_flow_impulse.py
Purpose:
- TradeFlowImpulseConfig, tick-rule side inference, count/notional/large trade/signed imbalance, no-lookahead, no-order guard, metadata.
Recommendation:
- Add venue-specific side semantics tests for real raw payloads.

### Modules added after original audit date

69. cost_sensitivity.py
Purpose:
- Diagnostic-only cost-sensitivity / breakeven analysis for evaluated signal groups.
Key functions:
- load_report_groups — reads summary.json from an evaluated report
- compute_cost_sensitivity — per-group breakeven cost computation at multiple cost levels
- write_cost_sensitivity_reports — writes JSON + markdown
Verdicts:
- COST_SENSITIVITY_READY, NO_EVALUATED_GROUPS
Forbidden verdicts:
- REJECTED, CANDIDATE, CANDIDATE_FOR_LONGER_OBSERVATION (module raises ValueError)
Persistence:
- No network; reads report summary.json, writes cost_sensitivity_summary.json and .md.
Tests:
- test_cost_sensitivity.py.
Security/ops:
- Observer-only diagnostic; no execution gating.
Recommendations:
- Add capture metadata to cost-level tables.

70. run_cost_sensitivity.py
Purpose:
- CLI runner for cost-sensitivity diagnostic.
Entry points:
- build_parser, main.
Configs:
- --report-dir (required), --out (required), --cost-levels-bps (default 50,10,5,1,0.5), --min-events (default 0).
Data flow:
- Loads evaluated report → computes cost sensitivity per group → writes reports.
Persistence:
- Writes cost_sensitivity_summary.json, cost_sensitivity_report.md.
Security/ops:
- Local file I/O only; no network; no orders.

71. candidate_falsification.py
Purpose:
- Diagnostic-only falsification summary combining multiple optional report artifacts (evaluated, cost sensitivity, null test, heatmap, corpus consistency) into a survival/failure matrix.
Key classes:
- FalsificationSummary — verdict, rows with per-group scores
- GroupKey — source/target venue, symbol, signal_type, lookback/horizon, OI bucket, capture_mode
Key functions:
- compute_candidate_falsification_summary — loads optional reports and produces scored matrix
- write_candidate_falsification_reports — writes JSON + markdown
Scoring:
- +1 per available evidence type; -1 for missing evidence or single-capture status.
Verdicts:
- FALSIFICATION_SUMMARY_READY, NO_EVALUATED_GROUPS, NO_SURVIVING_GROUPS, INSUFFICIENT_EVIDENCE, COST_WALL_BLOCKED, MISSING_REQUIRED_REPORTS
Forbidden verdicts:
- REJECTED, CANDIDATE, CANDIDATE_FOR_LIVE, EXECUTION_READY, TRADE_READY, READY_FOR_LIVE, LIVE_READY, DEPLOY_READY
Persistence:
- Reads optional report dirs; writes candidate_falsification_summary.json + .md.
Tests:
- test_candidate_falsification.py.
Security/ops:
- Observer-only; all inputs are optional (missing inputs produce a MISSING_REQUIRED_REPORTS verdict rather than aborting).
Recommendations:
- Add CLI flags for score-weight overrides.

72. run_candidate_falsification.py
Purpose:
- CLI runner for candidate falsification summary.
Entry points:
- build_parser, main.
Configs:
- --evaluated-report-dir (optional), --cost-sensitivity-dir (optional), --permutation-null-dir (optional), --heatmap-dir (optional), --consistency-dir (optional), --out (required), --viability-cost-bps (default 50.0), --min-events (default 50).
Data flow:
- Reads optional report directories → loads per-group evidence → computes falsification scores → writes summary.
Persistence:
- Writes candidate_falsification_summary.json, candidate_falsification_report.md.
Security/ops:
- Local file I/O only; all report dirs optional.

73. test_cost_sensitivity.py
Purpose:
- Unit tests for cost_sensitivity module.
Coverage:
- Load/filter groups from JSON
- Breakeven cost computation
- Viability at multiple cost levels
- Empty/no-groups handling
- NaN/inf filtering
- Report writing schema
- Forbidden verdict enforcement
Security/ops:
- Pure unit tests; mock I/O.

74. test_candidate_falsification.py
Purpose:
- Unit tests for candidate_falsification module.
Coverage:
- FalsificationSummary construction and verdict validation
- Forbidden verdict enforcement
- Group key construction
- Evidence loading from optional reports
- Score computation with various evidence combinations
- Missing report paths produce MISSING_REQUIRED_REPORTS
- Cost-wall blocked detection
- Empty/no-groups handling
Security/ops:
- Pure unit tests; mock I/O.

Cross-cutting security/operational notes:
- No credentials, private endpoints, or order-submission paths found in the audited source summaries.
- Most network calls use public endpoints but should have explicit timeouts/retries/backoff everywhere.
- Long-running capture scripts need disk-space, rotation, graceful shutdown, and manifest-finalization hardening.
- Relative output paths can overwrite prior reports; run IDs/atomic writes/input hashes are recommended.
- Derivatives modules use perps/futures only as public signal sources; maintain explicit guardrails against derivative execution.
- DEX data should be treated as lower-trust/stale-prone and validated heavily.
- Report miners parse local reports and write summaries; duplicate miner modules should be consolidated.

Most important recommendations:
1. Consolidate duplicated modules/concepts:
   - top-level vs package research_report_miner.py
   - data_fetcher.py vs data_adapters.py
   - LeadLagConfig in config.py vs lead_lag.py
   - signal CSV loading in data_loading.py vs signals.py

2. Add run manifests everywhere:
   - config
   - input paths
   - input file hashes
   - capture overlap
   - event counts
   - software/schema version
   - generated_at
   - public endpoint versions if known

3. Harden I/O:
   - atomic writes for reports/JSONL where feasible
   - no-overwrite or run-id directories
   - malformed-line counters
   - disk-space/rotation for capture

4. Harden network operations:
   - explicit timeouts
   - bounded retries/backoff
   - rate-limit configuration
   - clear partial-capture failure reporting

5. Preserve and expand safety tests:
   - no orders/no credentials/no private endpoints
   - no derivatives execution
   - no lookahead
   - non-finite/zero-price rejection
   - same-symbol and quote-mismatch guards

Files created/modified:
- None.

Issues encountered:
- Tool output for the first broad AST scan was truncated due to repository size, so I ran focused follow-up source inspections for omitted runner details.
- I intentionally avoided reading existing README/docs/report markdown per instructions, though source code that parses/writes markdown was audited.
- Potential stale/mismatched test imports around research_report_miner should be verified by running tests in the parent workflow.
```
