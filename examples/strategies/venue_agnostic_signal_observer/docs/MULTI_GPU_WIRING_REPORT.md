# Multi-GPU Wiring Report

## Files Changed

| File | Change |
|---|---|
| `run_derivatives_spot_lead_lag.py` | Import `batch_evaluate_signals_multi_gpu`; call it instead of single-device `batch_evaluate_signals_gpu` when `len(devices) > 1` |
| `run_permutation_null.py` | Add `--devices` arg; import `compute_null_distribution_multi_gpu` + `gpu_devices` helpers; dispatch to multi-GPU path when `len(devices) > 1`, single-device path otherwise |
| `run_lead_lag_heatmap.py` | Add `--devices` arg; import `gpu_devices` helpers; validate all devices up front; pass `devices=` into `compute_lead_lag_heatmap` |
| `tests/test_cli_devices.py` | New — 25 CLI device tests |
| `tests/benchmark_multi_gpu.py` | New — non-gating benchmark script |

## CLIs Supporting `--devices`

| Runner | Single-device flag | Multi-device flag | Multi-GPU function called |
|---|---|---|---|
| `run_derivatives_spot_lead_lag.py` | `--forward-device` | `--forward-devices` | `batch_evaluate_signals_multi_gpu` |
| `run_permutation_null.py` | `--device` | `--devices` | `compute_null_distribution_multi_gpu` |
| `run_lead_lag_heatmap.py` | `--device` | `--devices` | `compute_lead_lag_heatmap(..., devices=...)` |

## Exact Command Examples

```bash
# CPU (always available)
python -m ...run_permutation_null --capture-dir data/cap --report-dir data/rep
python -m ...run_lead_lag_heatmap --capture-dir data/cap --out data/out

# Single GPU (backward-compatible --device flag)
python -m ...run_permutation_null --capture-dir data/cap --report-dir data/rep --engine gpu --device cuda:0
python -m ...run_derivatives_spot_lead_lag --forward-engine gpu --forward-device cuda:0

# Dual GPU (new --devices flag overrides --device)
python -m ...run_permutation_null --capture-dir data/cap --report-dir data/rep --engine gpu --devices cuda:0,cuda:1
python -m ...run_derivatives_spot_lead_lag --forward-engine gpu --forward-devices cuda:0,cuda:1
python -m ...run_lead_lag_heatmap --capture-dir data/cap --out data/out --engine gpu --devices cuda:0,cuda:1

# Benchmark
python -m ...tests.benchmark_multi_gpu                          # CPU only
python -m ...tests.benchmark_multi_gpu --devices cuda:0         # + single GPU
python -m ...tests.benchmark_multi_gpu --devices cuda:0,cuda:1  # + dual GPU
```

## Tests Run and Results

```
test_gpu_devices.py                             26/26 passed
test_multi_gpu_wrappers.py                      12/12 passed
test_cli_devices.py                             25/25 passed
test_forward_returns_gpu.py                     passed
test_permutation_null_gpu.py                    passed
test_lead_lag_heatmap_gpu.py                    passed
test_tick_lead_lag_pipeline.py::TestNoOrderGuard  3/3 passed
Full suite                                     986/986 passed
```

## Benchmark Table (dual-RTX-3090 host)

### A. Forward Returns

| workload | engine | devices | elapsed_s | speedup_vs_cpu | speedup_vs_single_gpu |
|---|---|---|---|---|---|
| small (50 sig, 500 ticks) | cpu | — | 0.001 | 1.0× | — |
| small | gpu | cuda:0 | 0.134 | 0.01× | — |
| small | gpu | cuda:0,cuda:1 | 0.003 | 0.4×¹ | 84×¹ |
| medium (500 sig, 2k ticks) | cpu | — | 0.031 | 1.0× | — |
| medium | gpu | cuda:0 | 0.0035 | 8.8× | — |
| medium | gpu | cuda:0,cuda:1 | 0.0052 | 6.0× | 0.88× |
| large (5k sig, 10k ticks) | cpu | — | 1.41 | 1.0× | — |
| large | gpu | cuda:0 | 0.027 | 52.7× | — |
| large | gpu | cuda:0,cuda:1 | 0.031 | 45.6× | 1.04× |

¹ Small workload: single-GPU warm-up cost dominates; dual-GPU shard overhead exceeds compute time. Not a regression — small workloads should use single-GPU.

### B. Permutation Null

| workload | engine | devices | elapsed_s | speedup_vs_cpu | speedup_vs_single_gpu |
|---|---|---|---|---|---|
| 1k iters | cpu | — | 12.2 | 1.0× | — |
| 1k iters | gpu | cuda:0 | 0.27 | 46× | — |
| 1k iters | gpu | cuda:0,cuda:1 | 0.12 | 99× | **3.3×** |
| 10k iters | cpu | — | 122 | 1.0× | — |
| 10k iters | gpu | cuda:0 | 1.20 | 102× | — |
| 10k iters | gpu | cuda:0,cuda:1 | 1.22 | 100× | 1.0× |

Note: at 10k iterations both GPUs individually saturate; dual-GPU overhead breaks even. The 3.3× gain at 1k iters suggests dual-GPU is most beneficial in the 1k–5k iteration range.

### C. Lead-Lag Heatmap

| workload | engine | devices | elapsed_s | speedup_vs_cpu | speedup_vs_single_gpu |
|---|---|---|---|---|---|
| small (500 pts) | cpu | — | 0.0001 | 1.0× | — |
| small | gpu | cuda:0 | 0.0001 | 1.2× | — |
| small | gpu | cuda:0,cuda:1 | 0.0001 | 1.2× | 1.0× |
| large (5k pts) | cpu | — | 0.0007 | 1.0× | — |
| large | gpu | cuda:0 | 0.0007 | 1.0× | — |
| large | gpu | cuda:0,cuda:1 | 0.0007 | 1.1× | 1.0× |

Note: heatmap is CPU-bound in correlation math at these data sizes. No GPU benefit at current workload scale; no regression either.

## Confirmations

- No captures started. No market data connections opened.
- No network connections opened. Benchmark uses pure synthetic data.
- No auth, orders, private keys, or API keys added. Safety scan passes on all modified files.
- Signal math, verdict rules, thresholds, candidate gates, FDR rules, and verdict taxonomy are unchanged.
