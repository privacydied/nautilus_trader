# Derivatives V2 Capture Runbook

End-to-end workflow for derivatives-source → spot-target lead-lag research.

---

## Preconditions

- Python 3.12+ (`uv sync --all-extras` from repo root)
- GPU paths require PyTorch with CUDA (`torch.cuda.is_available()`)
- Run all commands from the NautilusTrader repo root
- No API keys, no auth tokens, no exchange accounts required
- Output directories are relative to cwd unless absolute paths are given

**Environment check:**

```bash
uv sync --all-extras
# Verify imports
python -c "from examples.strategies.venue_agnostic_signal_observer import *"
# Verify GPU availability (optional)
python -c "import torch; print('CUDA:', torch.cuda.is_available())"
```

---

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

### Output: `capture_manifest.json` key fields

| Field | Purpose |
|---|---|
| `run_id` | Unique capture identifier |
| `requested_duration_seconds` | User-requested capture length |
| `actual_start_time` / `actual_end_time` | Wall-clock start/end (ISO 8601) |
| `streams.{name}.status` | `"ok"`, `"failed"`, or `"missing"` |
| `streams.{name}.tick_count` | Ticks received per stream |
| `streams.{name}.reconnect_count` | WebSocket reconnections |
| `streams.{name}.error_summary` | Last error message (if any) |
| `streams.{name}.diagnostics` | Zero-tick / no-WS-data markers |
| `missing_streams` | Streams that never connected |
| `failed_streams` | Streams that connected but dropped |
| `overlap.global_overlap_start` / `global_overlap_end` | Timestamp range where ALL streams co-existed |
| `overlap.global_overlap_duration_seconds` | Heterogeneous-safe overlap window length |
| `overlap.per_pair.{asset}.overlap_duration_seconds` | Per-asset overlap (derived from streams covering that asset) |
| `total_reconnect_count` | Sum of all stream reconnections |
| `error_summaries` | List of {stream, error} for failed streams |
| `task_exceptions` | Unhandled async exceptions |

Captured tick streams are written as per-stream JSONL files in the output directory.

### Failure / Partial-Capture Handling

- **Stream never connected** — status `"missing"`. Re-run capture; check network and WebSocket endpoint health.
- **Stream dropped mid-capture** — status `"failed"`. Manifest records `error_summary` and `reconnect_count`. Partial data is still usable if global overlap is sufficient.
- **Zero ticks** — manifest diagnostics include `"zero_ticks_no_ws_data"` (no WS frames received) or `"zero_ticks_parser_may_have_dropped"` (WS data arrived but parser discarded all of it). Investigate symbol/endpoint compatibility.
- **Minimum viable overlap:** At least 60 seconds of global overlap across ALL streams for the shortest horizon (1s). For 300s horizons, prefer 600s+ overlap. If `global_overlap_duration_seconds` < 30, the capture is likely unusable — treat as `NEEDS_MORE_DATA`.
- **No SIGTERM/SIGINT-safe manifest finalization exists yet.** If the process is killed, the manifest JSON may be truncated or missing. Capture will restart from scratch.
- **No disk-space cap or rotation.** For long captures (>30 min), ensure at least 1 GB free per stream per hour.

---

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

---

## Step 3: MCPT Export

Export candidate groups for Monte Carlo Permutation Testing.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_mcpt_export \
    --capture-dir data/derivatives_spot_capture_v2 \
    --report-dir reports/derivatives_spot_lead_lag_v2 \
    --out mcpt_export \
    --min-events 50 \
    --cost-floor-bps 50
```

Output: per-group CSV files with event return series.

**Group selection criteria** (from `mcpt_export.py`):
1. At least `min_events` valid events
2. Mean and median net bps are finite
3. Either: candidate flag is True, or mean_net_bps > 0, or near-breakeven (within 10 bps of 0) AND beats baseline by >5 bps
4. Sorted by: candidate status first, then mean_net_bps descending, then win_rate descending, then valid_count descending
5. Deduplicated by (signal_type, lookback_ms) — keeps the best horizon per variant
6. Top `max_groups` (default 3) are exported

---

## Step 4: Permutation/Null Test

**If MCPT export found candidate groups**, run the native permutation null test to check whether the observed edge could have arisen from randomly shifted source timing.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_permutation_null \
    --capture-dir data/derivatives_spot_capture_v2 \
    --report-dir reports/derivatives_spot_lead_lag_v2 \
    --out null_test_results \
    --min-events 50 \
    --cost-floor-bps 50 \
    --iterations 1000 \
    --seed 42 \
    --shift-mode circular_time_shift
```

Two shift modes:
- `circular_time_shift` (default) — rotates source event timestamps by a random offset, preserving inter-event intervals and burst structure.
- `block_time_shift` — shuffles blocks of consecutive events, preserving intra-block clustering. Use `--block-size N` to control block size (default 10).

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

---

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

---

## Step 6: Cost-Sensitivity / Breakeven Diagnostic

Reads an existing evaluation report and computes, for each group, what cost level would turn it from net-negative to breakeven. Diagnostic-only — cannot create candidates or change verdicts.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_cost_sensitivity \
    --report-dir reports/derivatives_spot_lead_lag_v2 \
    --out reports/derivatives_spot_lead_lag_v2/cost_sensitivity \
    --cost-levels-bps 50,10,5,1,0.5 \
    --min-events 50
```

### CLI arguments

| Arg | Default | Description |
|---|---|---|
| `--report-dir` | (required) | Path to existing evaluation report directory |
| `--out` | (required) | Output directory for cost-sensitivity reports |
| `--cost-levels-bps` | `50,10,5,1,0.5` | Comma-separated cost levels for viability margin computation |
| `--min-events` | `0` | Minimum valid_count to include a group (default: 0 = include all) |

### Output

- `cost_sensitivity_summary.json` — per-group breakeven cost, viability at each cost level, verdict
- `cost_sensitivity_report.md` — human-readable markdown

### Verdicts

| Verdict | Meaning |
|---|---|
| `COST_SENSITIVITY_READY` | Analysis completed normally |
| `NO_EVALUATED_GROUPS` | Report had no groups to analyze |
| `GPU_UNAVAILABLE_DIAGNOSTIC` | (N/A — CPU only, no GPU path for cost sensitivity) |

---

## Step 7: Candidate Falsification Summary

Combines optional diagnostic reports (cost sensitivity, null test, lead/lag heatmap, corpus aggregation) into a single survival/failure matrix. Each group gets a `falsification_score` from available evidence sources. Diagnostic-only — cannot create candidates or change verdicts.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_candidate_falsification \
    --evaluated-report-dir reports/derivatives_spot_lead_lag_v2 \
    --cost-sensitivity-dir reports/derivatives_spot_lead_lag_v2/cost_sensitivity \
    --permutation-null-dir reports/derivatives_spot_lead_lag_v2/permutation_null \
    --heatmap-dir reports/derivatives_spot_lead_lag_v2/lead_lag_heatmap \
    --consistency-dir reports/corpus \
    --out reports/derivatives_spot_lead_lag_v2/candidate_falsification \
    --viability-cost-bps 50 \
    --min-events 50
```

All report directories besides `--out` are **optional** — if missing, they contribute a missing-evidence penalty to the score but don't abort.

### CLI arguments

| Arg | Default | Description |
|---|---|---|
| `--evaluated-report-dir` | (optional) | Existing evaluation report directory |
| `--cost-sensitivity-dir` | (optional) | Existing cost-sensitivity report directory |
| `--permutation-null-dir` | (optional) | Existing permutation/null report directory |
| `--heatmap-dir` | (optional) | Existing lead/lag heatmap report directory |
| `--consistency-dir` | (optional) | Existing cross-capture consistency report directory |
| `--out` | (required) | Output directory for falsification reports |
| `--viability-cost-bps` | `50.0` | Cost threshold for cost-sensitivity viability |
| `--min-events` | `50` | Minimum events for score penalty |

### Scoring

Each group accumulates +1 for each available evidence type (raw edge positive, net positive, cost viable, null survived, heatmap supported, 2+ captures) and -1 for missing evidence or single-capture status.

### Verdicts

| Verdict | Meaning |
|---|---|
| `FALSIFICATION_SUMMARY_READY` | Combined analysis completed normally |
| `NO_EVALUATED_GROUPS` | No groups from evaluated report |
| `NO_SURVIVING_GROUPS` | Groups exist but none survive diagnostic filters |
| `INSUFFICIENT_EVIDENCE` | Too few evidence sources available |
| `COST_WALL_BLOCKED` | All groups blocked by configured cost threshold |
| `MISSING_REQUIRED_REPORTS` | Required input directories not found |

**Forbidden verdicts** (module raises `ValueError` if encountered): `REJECTED`, `CANDIDATE`, `CANDIDATE_FOR_LIVE`, `EXECUTION_READY`, `TRADE_READY`.

---

## Step 8: Lead/Lag Heatmap Diagnostics (optional)

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

### CLI arguments

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

### Output

- `lead_lag_heatmap_summary.json` — per-pair correlation/directional-alignment scores
- `lead_lag_heatmap.csv` — flat heatmap table
- `lead_lag_heatmap.md` — markdown report with lag-bucket matrices

---

## Step 9: Latency Diagnostics (optional but recommended)

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

---

## Step 10: Corpus Aggregation (optional)

If you have multiple capture+evaluation reports, aggregate them to see whether a signal config repeats across captures.

```bash
python -m examples.strategies.venue_agnostic_signal_observer.run_report_corpus \
    --report-dirs reports/capture1,reports/capture2,reports/capture3 \
    --out corpus_aggregation
```

Output: per-config aggregation showing:
- Number of captures where this exact config appeared
- Mean and median of mean_net_bps across captures
- Best and worst single capture
- Number of positive-after-cost captures

**Hard rule:** The primary sort is by `num_captures` (consistency), NOT by best return. Cherrypicking the single best window is overfitting.

---

## Run Manifest / Reproducibility

Every run step (capture, evaluation, null test, diagnostics) writes an `_metadata` block into its output JSON. The metadata records:

| Field | Source |
|---|---|
| `git_sha` | `git rev-parse HEAD` at run time |
| `schema_version` | Module-level version constant |
| `capture_mode` | Passed via `--capture-mode` or empty string |
| `run_args` | Full CLI argument snapshot |
| `generated_at` | ISO 8601 timestamp |
| `python_version` | `sys.version` at run time |
| `package_versions` | Key dependency versions (numpy, pandas, nautilus_trader) |
| `data_window` | Actual start/end timestamps from the capture manifest |

**Rules for reproducibility:**
- Always record the `run_id` from the capture manifest when referencing a capture set.
- Store the `git_sha` — results are tied to the code version that produced them.
- Do not overwrite prior runs in-place; use run-specific output directories.
- For published results, record endpoint versions (e.g., Binance streams schema version).

---

## Verdict Reference

### Promotion verdicts (from evaluation)

| Verdict | Meaning |
|---|---|
| `REJECTED` | Enough data, no edge after costs |
| `NEEDS_MORE_DATA` | Insufficient overlap, too quiet, or too few events |
| `CANDIDATE_FOR_LONGER_OBSERVATION` | Passes gates, needs more windows |
| `NULL_REJECTED_DIAGNOSTIC` | Null test only; does NOT promote to a general REJECTED verdict |

### Diagnostic verdicts

| Verdict | Source | Meaning |
|---|---|---|
| `GPU_UNAVAILABLE_DIAGNOSTIC` | Any GPU path | CUDA unavailable — run exited cleanly without fallback to CPU |
| `NO_MCPT_WORTHY_GROUPS` | MCPT export, null test | No groups had sufficient events or positive net bps |
| `COST_SENSITIVITY_READY` | Cost sensitivity | Analysis completed normally |
| `NO_EVALUATED_GROUPS` | Cost sensitivity, falsification | Report had no groups to analyze |
| `FALSIFICATION_SUMMARY_READY` | Falsification | Combined analysis completed normally |
| `NO_SURVIVING_GROUPS` | Falsification | Groups exist but none survive diagnostic filters |
| `INSUFFICIENT_EVIDENCE` | Falsification | Too few evidence sources available |
| `COST_WALL_BLOCKED` | Falsification | All groups blocked by configured cost threshold |
| `MISSING_REQUIRED_REPORTS` | Falsification | Required input directories not found |
| `LEAD_LAG_DIAGNOSTIC_READY` | Heatmap | Heatmap computed normally |
| `INSUFFICIENT_OVERLAP` | Heatmap | Not enough timestamp overlap across streams |
| `INSUFFICIENT_SAMPLES` | Heatmap | Fewer overlapping buckets than `--min-samples` |
| `NO_SIGNAL_SERIES` | Heatmap | No source signal series could be constructed |
| `FAST_DIAGNOSTIC` | (capture mode) | Capture too short for final verdict; evaluation remaps REJECTED to diagnostic only |

---

## Completed Diagnostic Infrastructure

The derivatives-source → spot-target observer now has the following diagnostic-only components:

- GPU forward returns with CPU/GPU parity
- GPU permutation/null tests
- GPU lead/lag heatmap diagnostics
- Cost sensitivity diagnostics
- Cross-capture consistency aggregation
- Candidate falsification summary

These components are public-data research diagnostics only. They do not add execution, orders, private-key handling, live trading paths, threshold changes, verdict-rule changes, or registry updates by themselves.
