#!/usr/bin/env python3
"""Multi-GPU benchmark for forward-returns, permutation-null, and lead-lag heatmap.

Non-gating diagnostic tool. Results MUST NOT be used to tune strategy parameters,
thresholds, candidate gates, FDR rules, or verdict taxonomy.

Usage (CPU only, always works):
    python -m examples.strategies.venue_agnostic_signal_observer.tests.benchmark_multi_gpu

Usage (single GPU):
    python -m examples.strategies.venue_agnostic_signal_observer.tests.benchmark_multi_gpu \
        --devices cuda:0

Usage (dual GPU):
    python -m examples.strategies.venue_agnostic_signal_observer.tests.benchmark_multi_gpu \
        --devices cuda:0,cuda:1

Output: JSON table to stdout + optionally a file via --out.

Safety: no network, no auth, no orders, no captures, no live connections.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Safety constants
# ---------------------------------------------------------------------------

SAFETY_MODE = "public_data_observer_only"
_BENCHMARK_DISCLAIMER = (
    "DIAGNOSTIC ONLY — results must not be used to tune strategy parameters, "
    "thresholds, candidate gates, FDR rules, or verdict taxonomy."
)


# ---------------------------------------------------------------------------
# Synthetic data generators
# ---------------------------------------------------------------------------

def _synthetic_signals(n: int, seed: int = 0) -> list:
    from examples.strategies.venue_agnostic_signal_observer.tick_models import TickSignalEvent
    rng = random.Random(seed)
    base_ts = 1_700_000_000_000_000_000
    out = []
    for i in range(n):
        out.append(TickSignalEvent(
            signal_id=f"bench_{i}",
            ts_event=base_ts + i * 1_000_000_000,
            source_venue="okx",
            source_symbol="BTC/USDT",
            target_venue="bybit",
            target_symbol="BTC/USDT",
            asset="BTC",
            signal_type="tick_lead_lag",
            direction="long" if rng.random() > 0.5 else "short",
            lookback_ms=1000,
            threshold_bps=10.0,
            source_move_bps=rng.uniform(10.0, 50.0),
            source_start_price=30000.0,
            source_end_price=30000.0 * (1 + rng.uniform(0.001, 0.002)),
            strength=rng.uniform(10.0, 50.0),
        ))
    return out


def _synthetic_ticks(n: int, seed: int = 1) -> list:
    from examples.strategies.venue_agnostic_signal_observer.tick_models import TradeTickLite
    rng = random.Random(seed)
    base_ts = 1_700_000_000_000_000_000
    price = 30000.0
    out = []
    for i in range(n):
        price += rng.gauss(0, 1.0)
        out.append(TradeTickLite(
            ts_event=base_ts + i * 500_000_000,
            venue="bybit",
            symbol="BTC/USDT",
            price=price,
            size=round(rng.uniform(0.01, 1.0), 4),
            side="buy" if rng.random() > 0.5 else "sell",
        ))
    return out


def _synthetic_timestamps_and_prices(n: int, seed: int = 2):
    rng = random.Random(seed)
    base_ts = 1_700_000_000_000_000_000
    price = 30000.0
    ts, prices = [], []
    for i in range(n):
        price += rng.gauss(0, 1.0)
        ts.append(base_ts + i * 500_000_000)
        prices.append(price)
    return ts, prices


# ---------------------------------------------------------------------------
# Benchmark runners
# ---------------------------------------------------------------------------

def _run_cpu(fn, *args, **kwargs):
    t0 = time.perf_counter()
    fn(*args, **kwargs)
    return time.perf_counter() - t0


def _benchmark_forward_returns(devices_list: list[str]) -> list[dict]:
    """Benchmark forward-returns across CPU / single-GPU / dual-GPU."""
    from examples.strategies.venue_agnostic_signal_observer.forward_returns_gpu import (
        batch_evaluate_signals_gpu,
        batch_evaluate_signals_multi_gpu,
    )
    from examples.strategies.venue_agnostic_signal_observer.event_study import evaluate_tick_signal

    WORKLOADS = [
        ("small", 50, 500),
        ("medium", 500, 2000),
        ("large", 5000, 10000),
    ]
    HORIZONS = [1000, 5000, 30000]
    results = []

    for label, n_sig, n_tick in WORKLOADS:
        sigs = _synthetic_signals(n_sig)
        ticks = _synthetic_ticks(n_tick)

        # CPU
        t0 = time.perf_counter()
        for s in sigs:
            evaluate_tick_signal(s, ticks, horizons_ms=HORIZONS, fee_bps=40.0, slippage_bps=5.0)
        cpu_s = time.perf_counter() - t0

        row_base = {
            "module": "forward_returns",
            "workload": label,
            "n_signals": n_sig,
            "n_ticks": n_tick,
            "n_horizons": len(HORIZONS),
            "disclaimer": _BENCHMARK_DISCLAIMER,
        }
        results.append({**row_base, "engine": "cpu", "devices": [], "num_devices": 0,
                        "elapsed_seconds": round(cpu_s, 4), "speedup_vs_cpu": 1.0,
                        "speedup_vs_single_gpu": None})

        for devs in devices_list:
            devs_l = [d.strip() for d in devs.split(",") if d.strip()]
            n_dev = len(devs_l)
            try:
                if n_dev == 1:
                    t0 = time.perf_counter()
                    batch_evaluate_signals_gpu(sigs, ticks, horizons_ms=HORIZONS,
                                              fee_bps=40.0, slippage_bps=5.0, device=devs_l[0])
                    gpu_s = time.perf_counter() - t0
                    sg_s = gpu_s
                else:
                    t0 = time.perf_counter()
                    batch_evaluate_signals_multi_gpu(sigs, ticks, horizons_ms=HORIZONS,
                                                    fee_bps=40.0, slippage_bps=5.0, devices=devs_l)
                    gpu_s = time.perf_counter() - t0
                    sg_s = None  # filled below from single-GPU row

                speedup_cpu = round(cpu_s / gpu_s, 2) if gpu_s > 0 else None
                results.append({**row_base, "engine": "gpu", "devices": devs_l,
                                "num_devices": n_dev, "elapsed_seconds": round(gpu_s, 4),
                                "speedup_vs_cpu": speedup_cpu, "speedup_vs_single_gpu": None})
            except Exception as exc:
                results.append({**row_base, "engine": "gpu", "devices": devs_l,
                                "num_devices": n_dev, "elapsed_seconds": None,
                                "speedup_vs_cpu": None, "speedup_vs_single_gpu": None,
                                "error": str(exc)})

    # Back-fill speedup_vs_single_gpu for multi-GPU rows
    for i, r in enumerate(results):
        if r["num_devices"] > 1 and r.get("elapsed_seconds"):
            sg = next((x for x in results
                       if x["module"] == r["module"] and x["workload"] == r["workload"]
                       and x["num_devices"] == 1 and x.get("elapsed_seconds")), None)
            if sg:
                r["speedup_vs_single_gpu"] = round(sg["elapsed_seconds"] / r["elapsed_seconds"], 2)

    return results


def _benchmark_permutation_null(devices_list: list[str]) -> list[dict]:
    """Benchmark permutation-null across CPU / single-GPU / dual-GPU."""
    from examples.strategies.venue_agnostic_signal_observer.permutation_null import compute_null_distribution
    from examples.strategies.venue_agnostic_signal_observer.permutation_null_gpu import (
        compute_null_distribution_gpu,
        compute_null_distribution_multi_gpu,
    )

    WORKLOADS = [
        ("iters_1k", 1_000),
        ("iters_10k", 10_000),
    ]
    ts, prices = _synthetic_timestamps_and_prices(2000)
    src_ts, _ = _synthetic_timestamps_and_prices(200, seed=99)
    HORIZONS = [5000]
    results = []

    for label, iters in WORKLOADS:
        # CPU
        t0 = time.perf_counter()
        compute_null_distribution(
            source_event_timestamps=src_ts,
            target_timestamps=ts, target_prices=prices,
            direction="long", horizons_ms=HORIZONS,
            fee_bps=40.0, slippage_bps=5.0,
            iterations=iters, seed=42,
        )
        cpu_s = time.perf_counter() - t0

        row_base = {
            "module": "permutation_null",
            "workload": label,
            "iterations": iters,
            "disclaimer": _BENCHMARK_DISCLAIMER,
        }
        results.append({**row_base, "engine": "cpu", "devices": [], "num_devices": 0,
                        "elapsed_seconds": round(cpu_s, 4), "speedup_vs_cpu": 1.0,
                        "speedup_vs_single_gpu": None})

        for devs in devices_list:
            devs_l = [d.strip() for d in devs.split(",") if d.strip()]
            n_dev = len(devs_l)
            try:
                if n_dev == 1:
                    t0 = time.perf_counter()
                    compute_null_distribution_gpu(
                        source_event_timestamps=src_ts, target_timestamps=ts,
                        target_prices=prices, direction="long", horizons_ms=HORIZONS,
                        fee_bps=40.0, slippage_bps=5.0,
                        iterations=iters, seed=42, device=devs_l[0],
                    )
                    gpu_s = time.perf_counter() - t0
                else:
                    t0 = time.perf_counter()
                    compute_null_distribution_multi_gpu(
                        source_event_timestamps=src_ts, target_timestamps=ts,
                        target_prices=prices, direction="long", horizons_ms=HORIZONS,
                        fee_bps=40.0, slippage_bps=5.0,
                        iterations=iters, seed=42, devices=devs_l,
                    )
                    gpu_s = time.perf_counter() - t0

                speedup_cpu = round(cpu_s / gpu_s, 2) if gpu_s > 0 else None
                results.append({**row_base, "engine": "gpu", "devices": devs_l,
                                "num_devices": n_dev, "elapsed_seconds": round(gpu_s, 4),
                                "speedup_vs_cpu": speedup_cpu, "speedup_vs_single_gpu": None})
            except Exception as exc:
                results.append({**row_base, "engine": "gpu", "devices": devs_l,
                                "num_devices": n_dev, "elapsed_seconds": None,
                                "speedup_vs_cpu": None, "speedup_vs_single_gpu": None,
                                "error": str(exc)})

    for i, r in enumerate(results):
        if r["num_devices"] > 1 and r.get("elapsed_seconds"):
            sg = next((x for x in results
                       if x["module"] == r["module"] and x["workload"] == r["workload"]
                       and x["num_devices"] == 1 and x.get("elapsed_seconds")), None)
            if sg:
                r["speedup_vs_single_gpu"] = round(sg["elapsed_seconds"] / r["elapsed_seconds"], 2)

    return results


def _benchmark_heatmap(devices_list: list[str]) -> list[dict]:
    """Benchmark lead-lag heatmap across CPU / single-GPU / dual-GPU."""
    from examples.strategies.venue_agnostic_signal_observer.lead_lag_heatmap_gpu import (
        compute_lead_lag_heatmap,
    )

    WORKLOADS = [
        ("small", 500),
        ("large", 5000),
    ]
    LAGS = [100, 500, 1000, 5000, 10000]
    results = []

    for label, n in WORKLOADS:
        src_ts, src_vals = _synthetic_timestamps_and_prices(n, seed=10)
        tgt_ts, tgt_vals = _synthetic_timestamps_and_prices(n, seed=11)

        # CPU
        t0 = time.perf_counter()
        compute_lead_lag_heatmap(
            source_timestamps=src_ts, source_values=src_vals,
            target_timestamps=tgt_ts, target_values=tgt_vals,
            lags_ms=LAGS, engine="cpu",
        )
        cpu_s = time.perf_counter() - t0

        row_base = {
            "module": "lead_lag_heatmap",
            "workload": label,
            "n_points": n,
            "n_lags": len(LAGS),
            "disclaimer": _BENCHMARK_DISCLAIMER,
        }
        results.append({**row_base, "engine": "cpu", "devices": [], "num_devices": 0,
                        "elapsed_seconds": round(cpu_s, 4), "speedup_vs_cpu": 1.0,
                        "speedup_vs_single_gpu": None})

        for devs in devices_list:
            devs_l = [d.strip() for d in devs.split(",") if d.strip()]
            n_dev = len(devs_l)
            try:
                t0 = time.perf_counter()
                compute_lead_lag_heatmap(
                    source_timestamps=src_ts, source_values=src_vals,
                    target_timestamps=tgt_ts, target_values=tgt_vals,
                    lags_ms=LAGS, engine="gpu",
                    device=devs_l[0],
                    devices=devs_l if n_dev > 1 else None,
                )
                gpu_s = time.perf_counter() - t0
                speedup_cpu = round(cpu_s / gpu_s, 2) if gpu_s > 0 else None
                results.append({**row_base, "engine": "gpu", "devices": devs_l,
                                "num_devices": n_dev, "elapsed_seconds": round(gpu_s, 4),
                                "speedup_vs_cpu": speedup_cpu, "speedup_vs_single_gpu": None})
            except Exception as exc:
                results.append({**row_base, "engine": "gpu", "devices": devs_l,
                                "num_devices": n_dev, "elapsed_seconds": None,
                                "speedup_vs_cpu": None, "speedup_vs_single_gpu": None,
                                "error": str(exc)})

    for i, r in enumerate(results):
        if r["num_devices"] > 1 and r.get("elapsed_seconds"):
            sg = next((x for x in results
                       if x["module"] == r["module"] and x["workload"] == r["workload"]
                       and x["num_devices"] == 1 and x.get("elapsed_seconds")), None)
            if sg:
                r["speedup_vs_single_gpu"] = round(sg["elapsed_seconds"] / r["elapsed_seconds"], 2)

    return results


# ---------------------------------------------------------------------------
# CLI memory allocation reporting
# ---------------------------------------------------------------------------

def _gpu_memory_snapshot(devices: list[str]) -> dict:
    try:
        import torch
        out = {}
        for d in devices:
            idx = int(d.split(":")[-1])
            out[d] = {
                "allocated_bytes": torch.cuda.memory_allocated(idx),
                "reserved_bytes": torch.cuda.memory_reserved(idx),
            }
        return out
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Pretty-print table
# ---------------------------------------------------------------------------

def _print_table(rows: list[dict]) -> None:
    keys = ["module", "workload", "engine", "num_devices", "elapsed_seconds",
            "speedup_vs_cpu", "speedup_vs_single_gpu"]
    widths = {k: max(len(k), max((len(str(r.get(k, ""))) for r in rows), default=0)) for k in keys}
    sep = "  "
    header = sep.join(k.ljust(widths[k]) for k in keys)
    print(header)
    print("-" * len(header))
    for r in rows:
        err = r.get("error")
        line = sep.join(str(r.get(k, "")).ljust(widths[k]) for k in keys)
        if err:
            line += f"  ERROR: {err}"
        print(line)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--devices",
        type=str,
        default="",
        help="Comma-separated CUDA devices to benchmark, e.g. cuda:0 or cuda:0,cuda:1. "
             "CPU is always benchmarked. Each device group is benchmarked as single or multi-GPU.",
    )
    p.add_argument(
        "--modules",
        type=str,
        default="forward_returns,permutation_null,heatmap",
        help="Comma-separated modules to benchmark. Default: all three.",
    )
    p.add_argument(
        "--out",
        type=str,
        default="",
        help="Optional output JSON path for benchmark results.",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()

    # Validate devices early
    raw = args.devices.strip()
    gpu_device_groups: list[str] = []  # each entry is a comma-separated string of devices

    if raw:
        from examples.strategies.venue_agnostic_signal_observer.gpu_devices import (
            parse_cuda_devices, validate_cuda_devices,
        )
        # Build groups: single-device groups from each unique device, plus the full group if multi.
        all_devs_raw = [d.strip() for d in raw.split(",") if d.strip()]
        unique_devs = list(dict.fromkeys(all_devs_raw))

        # Single-device benchmarks per device
        for d in unique_devs:
            gpu_device_groups.append(d)

        # Multi-device benchmark if more than one device
        if len(unique_devs) > 1:
            gpu_device_groups.append(",".join(unique_devs))

        # Validate all
        ok, reason = validate_cuda_devices(unique_devs)
        if not ok:
            print(f"WARNING: GPU validation failed ({reason}). GPU rows will show errors.", file=sys.stderr)

    modules = [m.strip() for m in args.modules.split(",") if m.strip()]

    print(f"Benchmark: modules={modules}, gpu_groups={gpu_device_groups or ['(none)']}")
    print(f"DISCLAIMER: {_BENCHMARK_DISCLAIMER}")
    print()

    all_results: list[dict] = []

    if "forward_returns" in modules:
        print("[A] Forward Returns")
        rows = _benchmark_forward_returns(gpu_device_groups)
        all_results.extend(rows)
        _print_table(rows)
        print()

    if "permutation_null" in modules:
        print("[B] Permutation Null")
        rows = _benchmark_permutation_null(gpu_device_groups)
        all_results.extend(rows)
        _print_table(rows)
        print()

    if "heatmap" in modules:
        print("[C] Lead-Lag Heatmap")
        rows = _benchmark_heatmap(gpu_device_groups)
        all_results.extend(rows)
        _print_table(rows)
        print()

    # GPU memory snapshot after all benchmarks
    if gpu_device_groups:
        all_devs_flat = list(dict.fromkeys(
            d.strip() for g in gpu_device_groups for d in g.split(",") if d.strip()
        ))
        mem = _gpu_memory_snapshot(all_devs_flat)
        if mem:
            print("GPU memory after benchmarks:")
            for dev, info in mem.items():
                print(f"  {dev}: allocated={info['allocated_bytes']//1024//1024}MB "
                      f"reserved={info['reserved_bytes']//1024//1024}MB")
            print()

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w") as f:
            json.dump({"results": all_results, "safety_mode": SAFETY_MODE,
                       "disclaimer": _BENCHMARK_DISCLAIMER}, f, indent=2, default=str)
        print(f"Results written to: {args.out}")

    print("Done. No network connections opened. No captures started. No auth added.")


if __name__ == "__main__":
    main()
