# Venue-Agnostic Signal Observer

## Overview

Public-data observer framework for cross-venue tick-level signal research. Measures forward returns on target venues after signals detected on source venues. No execution, no orders, no private data.

## Running derivatives-source -> spot-target lead-lag

Two-step process: capture data, then evaluate.

### Step 1: Capture

Starts Binance USD-M perp, Kraken spot, Coinbase spot, and optional OI polling in ONE event loop. Writes per-stream JSONL and a capture_manifest.json proving true overlap.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_capture \
    --source-venue binance_perp \
    --source-symbols BTC/USDT,ETH/USDT,SOL/USDT \
    --target-venues kraken,coinbase \
    --target-symbols BTC/USD,ETH/USD,SOL/USD \
    --duration-seconds 600 \
    --capture-open-interest \
    --open-interest-interval-seconds 5 \
    --out data/derivatives_spot_capture_v2
```

Source: Binance USD-M perp aggTrade stream (`wss://fstream.binance.com/stream?streams=...`).
Target: Kraken/Coinbase spot trade WebSockets.
Captures are simultaneous in one event loop.

### Step 2: Evaluate

Reads captured ticks, clips to true overlap windows, generates trade-flow impulse signals, evaluates forward returns, classifies OI buckets.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_lead_lag \
    --capture-dir data/derivatives_spot_capture_v2 \
    --source-venues binance_perp \
    --target-venues kraken,coinbase \
    --symbols BTC/USD,ETH/USD,SOL/USD \
    --signal-types notional_burst,large_trade,signed_imbalance \
    --lookbacks-ms 1000,5000,10000,30000 \
    --baseline-window-ms 60000 \
    --horizons-ms 1000,2000,5000,10000,30000,60000,300000 \
    --cooldown-ms 10000 \
    --fee-bps 40 \
    --slippage-bps 5 \
    --quote-mismatch-buffer-bps 5 \
    --min-events 50 \
    --out reports/derivatives_spot_lead_lag_v2
```

**Multi-GPU evaluation** — shard pairs across multiple GPUs (round-robin):
```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_lead_lag \
    --capture-dir data/derivatives_spot_capture_v2 \
    --source-venues binance_perp \
    --target-venues kraken,coinbase \
    --symbols BTC/USD,ETH/USD,SOL/USD \
    --signal-types notional_burst,large_trade,signed_imbalance \
    --lookbacks-ms 1000,5000,10000,30000 \
    --baseline-window-ms 60000 \
    --horizons-ms 1000,2000,5000,10000,30000,60000,300000 \
    --cooldown-ms 10000 \
    --fee-bps 40 \
    --slippage-bps 5 \
    --quote-mismatch-buffer-bps 5 \
    --min-events 50 \
    --forward-engine gpu \
    --forward-devices cuda:0,cuda:1 \
    --out reports/derivatives_spot_lead_lag_v2
```

Multi-GPU details:
- Pairs are assigned round-robin across listed devices.
- Each pair is processed entirely on its assigned device.
- Results are deterministically merged — same output as single GPU within float tolerance.
- CPU remains default. No hidden fallback: if any listed device is unavailable, the tool fails fast.
- Sequential assignment only (no multiprocessing). The main runtime bottleneck is CPU signal generation, not GPU forward returns; see Phase 4 for GPU signal kernel acceleration.

### Key design rules

- Evaluator clips to overlap windows computed from actual stream timestamps
- OI is a research label only, not a trading rule
- Public data only; no account; no auth; no orders; no derivatives execution
- Missing OI produces `flat_or_unknown` bucket, never a crash
- No overlap or insufficient overlap produces `NEEDS_MORE_DATA`, not `REJECTED`
- Quiet captures (price movement far below the cost wall) produce `NEEDS_MORE_DATA`
- Summary/report aggregation filters non-finite (`NaN`/`inf`) return and bps values before means, medians, rankings, MCPT skip reasons, and candidate gates; malformed market data must not poison a whole group.

### Step 3: MCPT Export

Export candidate groups for Monte Carlo Permutation Testing.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_mcpt_export \
    --capture-dir data/derivatives_spot_capture_v2 \
    --report-dir reports/derivatives_spot_lead_lag_v2 \
    --out mcpt_export \
    --min-events 30 \
    --cost-floor-bps 50
```

### Step 4: Permutation/Null Test

If MCPT export finds candidate groups, test whether the observed edge survives randomized source timing.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_permutation_null \
    --capture-dir data/derivatives_spot_capture_v2 \
    --report-dir reports/derivatives_spot_lead_lag_v2 \
    --out null_test_results \
    --iterations 1000 \
    --seed 42 \
    --shift-mode circular_time_shift
```

Shift modes: `circular_time_shift` (default) preserves inter-event intervals; `block_time_shift` preserves intra-block clustering (use `--block-size N`).

### Step 5: Latency Diagnostics

Check clock alignment and lead/lag between venues.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_latency_diagnostics \
    --capture-dir data/derivatives_spot_capture_v2 \
    --out latency_diagnostics
```

### Step 6: Corpus Aggregation

Aggregate results across multiple capture+evaluation runs.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_report_corpus \
    --report-dirs reports/capture1 reports/capture2 reports/capture3 \
    --out corpus_aggregation
```

### Step 7: Lead/Lag Heatmap Diagnostics (optional)

Compute correlation-style diagnostics between source and target series across lag buckets. This is a diagnostic microscope — it cannot create candidates, change verdicts, or alter evaluator behavior.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_lead_lag_heatmap \
    --capture-dir data/derivatives_spot_capture_v2 \
    --out lead_lag_heatmap \
    --engine cpu \
    --lags-ms 100,250,500,1000,2000,5000,10000,30000 \
    --bucket-ms 250 \
    --min-samples 50
```

Optional GPU acceleration (`--engine gpu --device cuda:0`). No hidden CPU fallback. Diagnostic-only verdicts: `LEAD_LAG_DIAGNOSTIC_READY`, `INSUFFICIENT_OVERLAP`, `INSUFFICIENT_SAMPLES`, `NO_SIGNAL_SERIES`, `GPU_UNAVAILABLE_DIAGNOSTIC`. Forbidden verdicts: `REJECTED`, `CANDIDATE`.

Output: `lead_lag_heatmap_summary.json`, `lead_lag_heatmap.csv`, `lead_lag_heatmap.md`.

### Allowed verdicts

- `REJECTED` -- enough overlap, movement, events; no edge
- `NEEDS_MORE_DATA` -- non-overlapping, too short, too quiet, or too few events
- `CANDIDATE_FOR_LONGER_OBSERVATION` -- gates pass, needs longer-horizon validation
- `NULL_REJECTED_DIAGNOSTIC` -- null test only; does NOT promote to a general REJECTED verdict

### Key design rules (full)

- Never use null test results to tune signal parameters
- Never promote a null-rejected group to candidate status
- Corpus aggregation sorts by consistency (num_captures), NOT by best returns
- A null pass does NOT mean the signal is tradeable
- `NULL_REJECTED_DIAGNOSTIC` is diagnostic evidence only, never a full-hypothesis REJECTED

### Binance USD-M perp WebSocket

Combined stream: `wss://fstream.binance.com/stream?streams=btcusdt@aggTrade/ethusdt@aggTrade/solusdt@aggTrade`

Side inference from `m` field:
- `m=true` -> buyer was maker -> seller aggressor -> `side="sell"`
- `m=false` -> buyer was taker -> `side="buy"`

### Binance OI polling

`GET https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT`

Open interest costs low request weight. At 3 symbols x 5-second polling, this is ~36 requests/minute, well within the 2400 weight/minute limit. Future symbol expansion must keep polling bounded.

## Completed Derivatives-Spot Diagnostic Infrastructure

The derivatives-source -> spot-target observer now has the following diagnostic-only infrastructure completed:

- GPU permutation/null tests
- GPU forward returns with CPU/GPU parity
- Lead/lag heatmap diagnostics
- Cost sensitivity diagnostics
- Cross-capture consistency aggregation
- Candidate falsification summary

These components are public-data research diagnostics only. They do not add execution, orders, private-key handling, live trading paths, threshold changes, verdict-rule changes, or registry updates by themselves.

## Running the Stage 2 gate watcher as a user systemd service

The gate watcher polls the volatility gate every 30 seconds and automatically
starts the derivatives v2 pipeline when capture is permitted.

### Prerequisites

Install `libnotify` for desktop notifications:

```bash
sudo pacman -S libnotify
```

If `notify-send` is not available, the watcher logs a warning and continues
without notifications — notification failure does not block captures.

### Install the service

```bash
mkdir -p ~/.config/systemd/user

cp examples/strategies/venue_agnostic_signal_observer/systemd/nautilus-stage2-gate-watcher.service \
  ~/.config/systemd/user/nautilus-stage2-gate-watcher.service

systemctl --user daemon-reload
```

### Export Wayland environment

```bash
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XDG_CURRENT_DESKTOP DBUS_SESSION_BUS_ADDRESS

dbus-update-activation-environment --systemd DISPLAY WAYLAND_DISPLAY XDG_CURRENT_DESKTOP DBUS_SESSION_BUS_ADDRESS
```

### Start the service

```bash
systemctl --user start nautilus-stage2-gate-watcher.service
```

### Check status

```bash
systemctl --user status nautilus-stage2-gate-watcher.service
```

### View logs

```bash
journalctl --user -u nautilus-stage2-gate-watcher.service -f
```

### Stop the service

```bash
systemctl --user stop nautilus-stage2-gate-watcher.service
```

### Service details

The systemd template uses:

- **WorkingDirectory**: `/mnt/nasirjones/py/nautilus_trader`
- **Python**: `.venv/bin/python` (not bare `python`, no `source activate`)
- **Restart**: `no` — avoids accidental repeated captures
- **Notifications**: enabled via `--notify` (requires `notify-send`)
- **Logs and summaries**: written to `reports/stage2_gate_watcher_logs/`

