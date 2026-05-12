# Venue-Agnostic Signal Observer

## What this is

A research instrument for measuring cross-venue signal expectancy.  It
validates signal hypotheses by computing forward returns, subtracting
realistic costs, and comparing against a deterministic random baseline.

**This observer validates signals. It does not trade.**  Execution code
must not be added until a signal group shows robust positive net forward
expectancy against a random baseline.

This is not a trading system.  There is no order placement, no account
PnL, no live-trading mode, and no private-key handling.

---

## What it refuses to do

- Place or simulate orders
- Calculate account PnL or portfolio balance
- Connect to authenticated exchange APIs
- Run a live trading node
- Claim any signal is "tradable"

---

## Package structure

```
examples/strategies/venue_agnostic_signal_observer/
  __init__.py
  config.py              — FeeModel, Horizon, ObserverConfig, LeadLagConfig
  models.py              — SignalEvent, ForwardReturnResult, HorizonSummary
  signals.py             — CSV loader, CrossMarketSignalGenerator
  forward_returns.py     — Forward-return measurement per horizon
  observer.py            — Top-level orchestration (CSV bars)
  data_loading.py        — Synthetic data generators, CSV loader
  reports.py             — JSONL/CSV/JSON report writing
  data_adapters.py       — Public REST data downloaders (Binance, Kraken, Coinbase)
  csv_normalizer.py      — Multi-venue CSV alignment to common grid
  lead_lag.py            — Cross-market move signal generator + random baseline
  run_lead_lag.py        — CLI runner for OHLCV lead-lag sweep
  data_download.py       — Batch downloader for OHLCV CSVs
  data_fetcher.py        — Legacy REST fetch helpers

  tick_models.py         — TradeTickLite, QuoteTickLite, TickSignalEvent, TickForwardReturn
  tick_store.py          — JSONL loader/saver, sort, dedup, stale rejection, file discovery
  event_study.py         — Tick-level lead-lag generator, forward returns, candidate gate
  ws_collectors.py       — (placeholder) Public WebSocket collectors
  run_tick_capture.py    — CLI for real-time public tick capture
  run_tick_lead_lag.py   — CLI sweep runner for tick-level lead-lag study
  run_signal_observer.py — Legacy CLI for the original bar-based observer

  data/                  — Downloaded CSV tick data lives here
  reports/               — Output reports and rejection logs
  tests/                 — Test suite
```

---

## Running tests

```bash
python -m pytest examples/strategies/venue_agnostic_signal_observer/tests/ -q
```

All tests must pass.  The suite includes:
- Model serialization round-trips
- Signal generation above/below threshold
- Cooldown enforcement
- No-lookahead guarantees
- Forward return computation with cost deduction
- Malformed data rejection
- Deterministic random baseline
- Positive synthetic lead-lag detection
- No-edge synthetic rejection
- Candidate gate logic
- Report output verification
- No-order and no-key code scans

---

## Running the OHLCV lead-lag sweep (1-minute bars)

1. Download public OHLCV data:
   ```bash
   python -m examples.strategies.venue_agnostic_signal_observer.data_download \
       --data-dir data/lead_lag --days 7 --interval 1m
   ```

2. Run the sweep:
   ```bash
   python -m examples.strategies.venue_agnostic_signal_observer.run_lead_lag \
       --data-dir data/lead_lag \
       --source-venues BINANCE \
       --source-assets BTC/USDT,ETH/USDT,SOL/USDT \
       --target-venues KRAKEN \
       --target-assets BTC/USD,ETH/USD,SOL/USD \
       --lookbacks 120,300,600 \
       --thresholds 5,10,20 \
       --horizons 60,300,600,3600 \
       --cooldown 120 \
       --grid-seconds 60 \
       --fee-bps 10 \
       --slippage-bps 2 \
       --output-dir reports/lead_lag_v1
   ```

**Note:** The v1 OHLCV sweep was rejected.  See `reports/lead_lag_v1_rejection.md`.

---

## Running the tick-level lead-lag study (sub-minute resolution)

### Step 1: Capture live ticks (optional)

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_tick_capture \
    --venues kraken,coinbase \
    --symbols BTC/USD,ETH/USD \
    --duration-seconds 300 \
    --out data/signal_observer_ticks
```

This connects to public WebSocket feeds, collects `TradeTickLite` objects,
and writes them incrementally to JSONL files.

### Step 2: Run the sweep on tick data

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_tick_lead_lag \
    --ticks data/signal_observer_ticks \
    --source-venues kraken \
    --target-venues coinbase \
    --symbols BTC-USD ETH-USD \
    --lookbacks-ms 1000 5000 10000 30000 \
    --thresholds-bps 2 5 10 20 \
    --horizons-ms 1000 2000 5000 10000 30000 60000 \
    --cooldown-ms 10000 \
    --fee-bps 12 \
    --slippage-bps 2 \
    --quote-mismatch-buffer-bps 5 \
    --min-events 50 \
    --out reports/signal_observer_tick_lead_lag
```

### Step 3: Read the report

The markdown report at `reports/signal_observer_tick_lead_lag/tick_lead_lag_report.md`
contains:
- Hypothesis
- Data coverage
- Signal parameters tested
- Fee/slippage assumptions
- Results by group
- Baseline comparison
- Candidate groups (if any)
- Final verdict

---

## How to interpret report verdicts

| Verdict | Meaning |
|---|---|
| `REJECTED` | No signal group passed all candidate gates. The hypothesis has no edge under these parameters. |
| `NEEDS_MORE_DATA` | Insufficient tick data was loaded to produce meaningful results. Collect longer captures or more venues. |
| `CANDIDATE_FOR_LONGER_OBSERVATION` | A signal group passed all gates. Worthy of further research with more data, different parameters, or additional venues. NOT tradable. |

The verdict is **never** `ACCEPTED_FOR_TRADING`.

---

## Why positive signal expectancy is required before execution

A signal may look profitable in hindsight if it ignores transaction costs,
slippage, or uses future data.  The observer enforces:
- All measurements use only data available at or before the signal timestamp
- Fees, slippage, and quote-mismatch buffers are subtracted from returns
- Random baselines prevent overinterpreting noise
- The candidate gate requires beating the baseline by a configurable margin
- Reports must be honest: if results are bad, the report says so

Only after a signal group robustly passes all gates — across multiple assets,
time horizons, and parameter variations — should execution code be considered.
Even then, execution is a separate project with its own safety requirements.
