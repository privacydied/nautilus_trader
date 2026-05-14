# MCPT Adapter Notes

## What MCPT export does

The MCPT adapter selects candidate or near-candidate groups from an evaluation report and exports their event return series as CSV files suitable for external Monte Carlo Permutation Testing tools.

## When to run MCPT export

Only after evaluation (Step 2). Only when `summary.json` contains groups that are candidates or near-candidates (positive mean_net_bps above cost floor, sufficient event count).

Groups that are cost-floor dust (mean_net_bps well below round-trip costs) are skipped. Groups with insufficient events (< `min_events`) are skipped.

## Relationship to native permutation null test

MCPT export produces data for external permutation tools. The native permutation null test (`permutation_null.py` / `run_permutation_null.py`) is a self-contained alternative that:

1. Reads the same report directory and capture directory
2. Selects null-worthy groups using compatible but slightly broader criteria
3. Runs circular or block time shifts internally
4. Computes empirical p-values and null distribution statistics
5. Produces a verdict without needing external tools

You can use either or both. The native null test is recommended for quick falsification. MCPT export + external tools are recommended for formal publication.

## Key parameters

| Parameter | Default | Description |
|---|---|---|
| `--min-events` | 30 | Minimum event count for a group to be worth testing |
| `--cost-floor-bps` | 50.0 | Minimum cost floor in basis points |
| `--max-groups` | 3 | Maximum groups to export/test per run |

## Output format

### MCPT export (CSV per group)
- Event-level return series with timestamps, directions, forward returns
- Metadata header with group identity and evaluation parameters

### Native null test (JSON + markdown)
- Per-group null distribution statistics (p50, p95, p99 of null mean_net_bps)
- Empirical p-value computation
- Win rate comparison against null median
- `candidate_survives_null` boolean verdict
- `NULL_REJECTED_DIAGNOSTIC` or `NO_MCPT_WORTHY_GROUPS` status when appropriate

## GPU forward-return evaluation (optional, explicit)

The forward-return kernel in `forward_returns_gpu.py` accelerates the inner events × horizons evaluation loop using CUDA batching.

- **CPU is the default.** `--forward-engine cpu` preserves all existing behavior exactly.
- `--forward-engine gpu` uses `batch_evaluate_signals_gpu` via PyTorch CUDA `searchsorted`.
- **No hidden CPU fallback.** If `--forward-engine gpu` is requested and CUDA is unavailable, the run exits with `GPU_UNAVAILABLE_DIAGNOSTIC` and a non-zero exit code.
- GPU forward returns do **not** change signal generation, costs, thresholds, verdict rules, or candidate selection. They are a speed improvement only.
- GPU forward returns do **not** update `REJECTED_RESEARCH.md` and do **not** permit live trading.
- Determinism is trivially guaranteed — there is no randomness in the forward-return kernel.

CLI args added to `run_derivatives_spot_lead_lag.py`:

| Arg | Default | Description |
|---|---|---|
| `--forward-engine` | `cpu` | `cpu` or `gpu` |
| `--forward-device` | `cuda:0` | CUDA device for `--forward-engine gpu` |
| `--forward-batch-size` | `16384` | Events per GPU chunk |

## GPU acceleration (optional, explicit)

The native null test supports an explicit GPU engine via `--engine gpu`.

- **CPU is the default**. `--engine cpu` preserves existing behavior exactly.
- `--engine gpu` uses `permutation_null_gpu.py` with PyTorch CUDA chunked batching.
- There is **no transparent CPU fallback**. If `--engine gpu` is requested and CUDA is unavailable, the run exits cleanly with a `GPU_UNAVAILABLE_DIAGNOSTIC` verdict JSON.
- GPU is faster for large iteration counts; it is **not** a new verdict system and does not change what constitutes a null pass or fail.
- GPU null testing does **not** update `REJECTED_RESEARCH.md`.
- GPU null testing does **not** permit live trading.

CLI args added to `run_permutation_null.py`:

| Arg | Default | Description |
|---|---|---|
| `--engine` | `cpu` | `cpu` or `gpu` |
| `--device` | `cuda:0` | CUDA device for `--engine gpu` |
| `--batch-size` | `512` | Permutation chunk size for GPU batching |

Example (GPU):

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

## Lead/Lag Heatmap Diagnostics (optional, diagnostic-only)

The lead/lag heatmap module (`lead_lag_heatmap_gpu.py`) computes correlation-style diagnostics between source event/flow series and target return series across lag buckets. It is a diagnostic microscope — it cannot create trade candidates, change verdicts, update registries, or alter evaluator behavior.

- **CPU is the default.** `--engine cpu` produces deterministic output.
- `--engine gpu` uses chunked CUDA tensors for the cross-correlation inner loop.
- **No hidden CPU fallback.** If `--engine gpu` is requested and CUDA is unavailable, the run exits with `GPU_UNAVAILABLE_DIAGNOSTIC`.
- CPU/GPU produce matching verdicts and float values within tolerance.
- Allowed diagnostic verdicts: `LEAD_LAG_DIAGNOSTIC_READY`, `INSUFFICIENT_OVERLAP`, `INSUFFICIENT_SAMPLES`, `NO_SIGNAL_SERIES`, `GPU_UNAVAILABLE_DIAGNOSTIC`.
- **Forbidden verdicts**: `REJECTED`, `CANDIDATE`, `CANDIDATE_FOR_LONGER_OBSERVATION`. The module raises `ValueError` on these.
- Heatmaps cannot promote or reject a strategy by themselves.

CLI args added to `run_lead_lag_heatmap.py`:

| Arg | Default | Description |
|---|---|---|
| `--capture-dir` | (required) | Path to capture data directory |
| `--report-dir` | (optional) | Path to evaluated signal groups |
| `--out` | (required) | Output directory |
| `--engine` | `cpu` | `cpu` or `gpu` |
| `--device` | `cuda:0` | CUDA device for `--engine gpu` |
| `--batch-size` | `8192` | Tensor chunk size for GPU processing |
| `--lags-ms` | `100,250,500,1000,2000,5000,10000,30000` | Lag buckets in milliseconds |
| `--bucket-ms` | `250` | Resampling bucket width in milliseconds |
| `--min-samples` | `50` | Minimum overlapping buckets to produce a correlation |

## Important constraints

- MCPT and null tests are **falsification tools only**. They test whether observed results could arise by chance.
- A null pass does NOT prove a signal is tradeable.
- A null failure is **diagnostic evidence**, not a general REJECTED verdict for the hypothesis.
- Never use null test results to optimize signal parameters -- that is p-hacking.
- The null test's `NULL_REJECTED_DIAGNOSTIC` prefix is deliberate: it must not be collapsed into the main `REJECTED` verdict taxonomy.
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

## Completed Derivatives-Spot Diagnostic Infrastructure

The derivatives-source -> spot-target observer now has the following diagnostic-only infrastructure completed:

- GPU permutation/null tests
- GPU forward returns with CPU/GPU parity
- Lead/lag heatmap diagnostics
- Cost sensitivity diagnostics
- Cross-capture consistency aggregation
- Candidate falsification summary

These components are public-data research diagnostics only. They do not add execution, orders, private-key handling, live trading paths, threshold changes, verdict-rule changes, or registry updates by themselves.
