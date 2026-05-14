# Derivatives V2 Capture Runbook

End-to-end workflow for derivatives-source → spot-target lead-lag research.

## Step 1: Capture

Collect simultaneous tick data from Binance perp, Kraken spot, and Coinbase spot.

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

Output: per-stream JSONL tick files + `capture_manifest.json` with true overlap windows.

## Step 2: Evaluate

Generate signals and evaluate forward returns on target venues.

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

Output: `summary.json`, `signals.jsonl`, `forward_returns.jsonl`, plus per-group report files.

### Optional: GPU-accelerated evaluation

CPU is the default. For large signal sets and many horizons, an explicit GPU engine is available. Requires PyTorch + CUDA.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_derivatives_spot_lead_lag \
    --capture-dir data/derivatives_spot_capture_v2 \
    --source-venues binance_perp \
    --target-venues kraken,coinbase \
    --symbols BTC/USD,ETH/USD,SOL/USD \
    --forward-engine gpu \
    --forward-device cuda:0 \
    --forward-batch-size 16384 \
    ... (same remaining args as CPU run)
```

Important rules:
- `--forward-engine cpu` is the default and produces identical output.
- `--forward-engine gpu` is explicit. If CUDA is unavailable the run exits with `GPU_UNAVAILABLE_DIAGNOSTIC` — it does **not** silently fall back to CPU.
- GPU forward returns accelerate the events × horizons inner loop only. All signal generation, OI bucketing, baseline evaluation, grouping, verdict rules, and report writing are unchanged.
- GPU forward returns do **not** update `REJECTED_RESEARCH.md`.
- GPU forward returns do **not** permit live trading or execution.
- Lead/lag heatmaps are diagnostics only; they do not promote or reject a strategy by themselves.

## Step 3: MCPT Export

Export candidate groups for Monte Carlo Permutation Testing.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_mcpt_export \
    --capture-dir data/derivatives_spot_capture_v2 \
    --report-dir reports/derivatives_spot_lead_lag_v2 \
    --out mcpt_export \
    --min-events 30 \
    --cost-floor-bps 50
```

Output: per-group CSV files with event return series.

## Step 4: Permutation/Null Test

**If MCPT export found candidate groups**, run the native permutation null test to check whether the observed edge could have arisen from randomly shifted source timing.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_permutation_null \
    --capture-dir data/derivatives_spot_capture_v2 \
    --report-dir reports/derivatives_spot_lead_lag_v2 \
    --out null_test_results \
    --min-events 30 \
    --cost-floor-bps 50 \
    --iterations 1000 \
    --seed 42 \
    --shift-mode circular_time_shift
```

Two shift modes:
- `circular_time_shift` (default) -- rotates source event timestamps by a random offset, preserving inter-event intervals and burst structure.
- `block_time_shift` -- shuffles blocks of consecutive events, preserving intra-block clustering. Use `--block-size N` to control block size (default 10).

Output: per-group null test JSON + markdown summary.

### Optional: GPU-accelerated null test

CPU is the default. For large iteration counts, an explicit GPU engine is available. Requires PyTorch + CUDA.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_permutation_null \
  --capture-dir data/derivatives_spot_capture_v2_ACTIVE_YYYYMMDD_HHMMSS \
  --report-dir reports/derivatives_spot_lead_lag_v2_ACTIVE_YYYYMMDD_HHMMSS \
  --out reports/derivatives_spot_lead_lag_v2_ACTIVE_YYYYMMDD_HHMMSS/permutation_null_gpu \
  --iterations 10000 \
  --seed 42 \
  --shift-mode circular_time_shift \
  --engine gpu \
  --device cuda:0 \
  --batch-size 512 \
  --min-events 50 \
  --cost-floor-bps 50
```

Important GPU rules:
- `--engine cpu` is the default and preserves existing behavior.
- `--engine gpu` is explicit. If CUDA is unavailable, the run exits with a `GPU_UNAVAILABLE_DIAGNOSTIC` JSON verdict — it does **not** silently fall back to CPU.
- GPU is a faster microscope, not a new verdict system. Same candidate selection, same survival criteria, same output schema.
- GPU null testing does **not** update `REJECTED_RESEARCH.md`.
- GPU null testing does **not** permit live trading or execution.

## Step 5: Interpret Null Results

| Null result | Meaning |
|---|---|
| `candidate_survives_null: true` | Real edge beats null p95, p-value ≤ 0.05, win rate beats null median. Mark for longer observation. |
| `NULL_REJECTED_DIAGNOSTIC` | Real result could be produced by random timing. Record as diagnostic evidence only. Do NOT convert to a general `REJECTED` verdict. |
| `NO_MCPT_WORTHY_GROUPS` | No groups had enough events and positive net bps to warrant testing. |

**Hard rules:**
- Never use null test results to tune signal parameters.
- Never promote a null-rejected group to candidate status.
- A null pass does NOT mean the signal is tradeable; it means it survives a falsification check and warrants further observation.

## Step 6: Latency Diagnostics (optional but recommended)

Check clock alignment and lead-lag between venues before trusting sub-second results.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_latency_diagnostics \
    --capture-dir data/derivatives_spot_capture_v2 \
    --out latency_diagnostics
```

Output: per-symbol JSON + markdown with:
- Tick counts, first/last timestamps, overlap seconds per stream
- Median and p95 inter-tick gaps
- Cross-venue lead/lag estimates at 250ms, 1s, 5s buckets
- Whether Binance perp leads spot
- Whether sub-second horizons are trustworthy for this capture

This is a **quality diagnostic**, not a trading signal. Use it to calibrate trust in capture data, not to create candidates.

## Step 7: Corpus Aggregation (optional)

If you have multiple capture+evaluation reports, aggregate them to see whether a signal config repeats across captures.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_report_corpus \
    --report-dirs reports/capture1 reports/capture2 reports/capture3 \
    --out corpus_aggregation
```

Output: per-config aggregation showing:
- Number of captures where this exact config appeared
- Mean and median of mean_net_bps across captures
- Best and worst single capture
- Number of positive-after-cost captures

**Hard rule:** The primary sort is by `num_captures` (consistency), NOT by best return. Cherrypicking the single best window is overfitting.

## Allowed verdicts (unchanged)

- `REJECTED` -- enough data, no edge after costs
- `NEEDS_MORE_DATA` -- insufficient overlap, too quiet, or too few events
- `CANDIDATE_FOR_LONGER_OBSERVATION` -- passes gates, needs more windows
- `NULL_REJECTED_DIAGNOSTIC` -- null test only; does NOT promote to a general REJECTED verdict
## Files Changed

┌─────────────────────────────────────┬──────────────────────────────────────────┐
  │               File                  │               Change                     │
  ├─────────────────────────────────────┼──────────────────────────────────────────┤
  │ forward_returns_gpu.py              │ New — GPU forward-return kernel          │
  ├─────────────────────────────────────┼──────────────────────────────────────────┤
  │ lead_lag_heatmap_gpu.py             │ New — GPU lead/lag heatmap diagnostics  │
  ├─────────────────────────────────────┼──────────────────────────────────────────┤
  │ run_lead_lag_heatmap.py             │ New — CLI runner for heatmap            │
  ├─────────────────────────────────────┼──────────────────────────────────────────┤
  │ tests/test_forward_returns_gpu.py   │ New — 16 focused tests                  │
  ├─────────────────────────────────────┼──────────────────────────────────────────┤
  │ tests/test_lead_lag_heatmap_gpu.py  │ New — 22 focused tests                  │
  ├─────────────────────────────────────┼──────────────────────────────────────────┤
  │ run_derivatives_spot_lead_lag.py    │ Modified — 3 CLI args + GPU routing    │
  ├─────────────────────────────────────┼──────────────────────────────────────────┤
  │ MCPT_ADAPTER_NOTES.md               │ Updated                                 │
  ├─────────────────────────────────────┼──────────────────────────────────────────┤
  │ DERIVATIVES_V2_CAPTURE_RUNBOOK.md   │ Updated                                 │
  └─────────────────────────────────────┴──────────────────────────────────────────┘


## Step 7b: Lead/Lag Heatmap Diagnostics (optional)

Compute correlation-style lead/lag diagnostics between source event/flow series and target return series across lag buckets. This is a **diagnostic microscope** — it cannot create trade candidates, change verdicts, update registries, or alter evaluator behavior.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_lead_lag_heatmap \
    --capture-dir data/derivatives_spot_capture_v2 \
    --out lead_lag_heatmap \
    --engine cpu \
    --lags-ms 100,250,500,1000,2000,5000,10000,30000 \
    --bucket-ms 250 \
    --min-samples 50
```

### Optional: GPU-accelerated heatmap

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_lead_lag_heatmap \
    --capture-dir data/derivatives_spot_capture_v2 \
    --out lead_lag_heatmap_gpu \
    --engine gpu \
    --device cuda:0 \
    --batch-size 8192 \
    --lags-ms 100,250,500,1000,2000,5000,10000,30000 \
    --bucket-ms 250 \
    --min-samples 50
```

CLI args:

| Arg | Default | Description |
|---|---|---|
| `--capture-dir` | (required) | Path to capture data directory |
| `--report-dir` | (optional) | Path to evaluated signal groups (for metadata enrichment) |
| `--out` | (required) | Output directory |
| `--engine` | `cpu` | `cpu` or `gpu` |
| `--device` | `cuda:0` | CUDA device for `--engine gpu` |
| `--batch-size` | `8192` | Tensor chunk size for GPU processing |
| `--lags-ms` | `100,250,500,1000,2000,5000,10000,30000` | Lag buckets in milliseconds |
| `--bucket-ms` | `250` | Resampling bucket width in milliseconds |
| `--min-samples` | `50` | Minimum overlapping buckets to produce a correlation |

Important rules:
- `--engine cpu` is the default and produces deterministic output.
- `--engine gpu` is explicit. If CUDA is unavailable, the run exits with `GPU_UNAVAILABLE_DIAGNOSTIC` — it does **not** silently fall back to CPU.
- CPU/GPU produce bit-identical verdicts and matching float values within tolerance.
- Heatmap diagnostics are **diagnostic only** — they cannot promote or reject a strategy by themselves.
- Allowed diagnostic verdicts: `LEAD_LAG_DIAGNOSTIC_READY`, `INSUFFICIENT_OVERLAP`, `INSUFFICIENT_SAMPLES`, `NO_SIGNAL_SERIES`, `GPU_UNAVAILABLE_DIAGNOSTIC`.
- Forbidden verdicts: `REJECTED`, `CANDIDATE`, `CANDIDATE_FOR_LONGER_OBSERVATION`.

Output: `lead_lag_heatmap_summary.json`, `lead_lag_heatmap.csv`, `lead_lag_heatmap.md`.
