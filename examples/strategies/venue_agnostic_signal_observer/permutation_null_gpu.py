"""GPU-accelerated permutation null engine for venue_agnostic_signal_observer.

Pure functions only. No network, no capture, no live imports, no orders, no auth.

Uses PyTorch CUDA for chunked circular-time-shift null testing.
Engine selection is EXPLICIT: this module is only called when --engine gpu is passed.
There is NO transparent CPU fallback here.

Safety:
- safety_mode: public_data_observer_only
- No live trading, no order placement, no private keys, no derivatives execution.

Public API
----------
check_cuda_available()       -- returns (available: bool, reason: str)
compute_null_distribution_gpu(...)  -- GPU version of compute_null_distribution
"""
from __future__ import annotations

import math
import random
from typing import Any


# ---------------------------------------------------------------------------
# Safety metadata
# ---------------------------------------------------------------------------

SAFETY_MODE = "public_data_observer_only"
_MODULE_METADATA = {
    "safety_mode": SAFETY_MODE,
    "live_trading": False,
    "orders": False,
    "auth_credentials": False,
    "derivatives_execution": False,
}

DEFAULT_CHUNK_SIZE: int = 512


# ---------------------------------------------------------------------------
# CUDA availability detection
# ---------------------------------------------------------------------------

def check_cuda_available(device: str = "cuda:0") -> tuple[bool, str]:
    """Return (available, reason) without raising.

    If torch is not installed, or CUDA is not available, returns False with
    a diagnostic reason string.
    """
    try:
        import torch  # noqa: PLC0415
    except ImportError:
        return False, "torch_not_installed"

    if not torch.cuda.is_available():
        return False, "torch_cuda_unavailable"

    # Validate device string
    try:
        device_idx = int(device.split(":")[-1]) if ":" in device else 0
        if device_idx >= torch.cuda.device_count():
            return False, f"cuda_device_not_found:{device}"
    except (ValueError, IndexError):
        return False, f"invalid_device_string:{device}"

    return True, "cuda_available"


def gpu_unavailable_diagnostic(device: str, reason: str) -> dict[str, Any]:
    """Build the standard diagnostic dict when GPU is unavailable."""
    return {
        "verdict": "GPU_UNAVAILABLE_DIAGNOSTIC",
        "reason": reason,
        "device": device,
        "safety_mode": SAFETY_MODE,
    }


# ---------------------------------------------------------------------------
# Core GPU null distribution
# ---------------------------------------------------------------------------

def compute_null_distribution_gpu(
    source_event_timestamps: list[int],
    target_timestamps: list[int],
    target_prices: list[float],
    direction: str,
    horizons_ms: list[int],
    fee_bps: float,
    slippage_bps: float,
    quote_mismatch_buffer_bps: float = 0.0,
    quote_mismatch: bool = False,
    iterations: int = 1000,
    seed: int = 42,
    shift_mode: str = "circular_time_shift",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    device: str = "cuda:0",
) -> dict[str, Any]:
    """GPU version of compute_null_distribution.

    Output schema is compatible with the CPU version in permutation_null.py.

    Parameters
    ----------
    source_event_timestamps:
        Nanosecond timestamps of source signal events.
    target_timestamps:
        Nanosecond timestamps of target price ticks (sorted).
    target_prices:
        Prices at each target tick.
    direction:
        'long' or 'short'.
    horizons_ms:
        List of forward return horizons in milliseconds.
    fee_bps, slippage_bps:
        Cost parameters.
    quote_mismatch_buffer_bps:
        Additional cost buffer when symbols have different quote currencies.
    quote_mismatch:
        Whether to apply quote_mismatch_buffer_bps.
    iterations:
        Total number of null permutations.
    seed:
        Base seed. All offsets are precomputed from a single CPU generator seeded once,
        so --batch-size only affects speed, never which offsets are used.
    shift_mode:
        Must be 'circular_time_shift'. 'block_time_shift' is not supported on GPU.
    chunk_size:
        Number of permutations per GPU batch. Default: 512.
    device:
        CUDA device string, e.g. 'cuda:0' or 'cuda:1'.

    Returns
    -------
    dict matching compute_null_distribution output schema:
        null_mean_net_bps, null_win_rates, null_median_net_bps,
        percentiles, iterations, seed, shift_mode, engine, device, chunk_size
    """
    import torch  # noqa: PLC0415

    if shift_mode != "circular_time_shift":
        raise ValueError(
            f"GPU engine only supports shift_mode='circular_time_shift', got '{shift_mode}'. "
            "Use --engine cpu for block_time_shift."
        )

    # Validate inputs
    if len(source_event_timestamps) < 2:
        return _empty_null_result(iterations, seed, shift_mode, device, chunk_size)

    # Filter zero/negative prices from target
    valid_pairs = [
        (ts, pr)
        for ts, pr in zip(target_timestamps, target_prices)
        if math.isfinite(pr) and pr > 0
    ]
    if not valid_pairs:
        return _empty_null_result(iterations, seed, shift_mode, device, chunk_size)

    target_timestamps = [p[0] for p in valid_pairs]
    target_prices = [p[1] for p in valid_pairs]

    # Build tensors on device
    dev = torch.device(device)
    tgt_ts = torch.tensor(target_timestamps, dtype=torch.int64, device=dev)
    tgt_pr = torch.tensor(target_prices, dtype=torch.float64, device=dev)

    src_ts = torch.tensor(source_event_timestamps, dtype=torch.int64, device=dev)
    ts_min = int(src_ts.min().item())
    ts_max = int(src_ts.max().item())
    duration = ts_max - ts_min

    if duration <= 0:
        return _empty_null_result(iterations, seed, shift_mode, device, chunk_size)

    total_cost = fee_bps + slippage_bps + (quote_mismatch_buffer_bps if quote_mismatch else 0.0)
    direction_sign = 1.0 if direction != "short" else -1.0

    # Horizon in nanoseconds
    horizon_ns_list = [int(h * 1_000_000) for h in horizons_ms]

    null_means: list[float] = []
    null_medians: list[float] = []
    null_win_rates: list[float] = []

    n_events = len(source_event_timestamps)

    # src_ts offsets relative to ts_min for circular shift
    src_offsets = src_ts - ts_min  # shape [N]

    # Precompute all permutation offsets using Python's random.Random (stdlib,
    # Mersenne Twister). Stable across PyTorch versions, OS, and hardware.
    # Makes --batch-size a pure speed knob: same (seed, iterations, duration)
    # always produces the same null universe.
    rng = random.Random(seed)
    all_offsets_cpu = torch.tensor(
        [rng.randrange(duration) for _ in range(iterations)],
        dtype=torch.int64,
    )

    for chunk_start in range(0, iterations, chunk_size):
        this_chunk = min(chunk_size, iterations - chunk_start)

        # Slice the precomputed schedule and move to device
        rand_offsets = all_offsets_cpu[chunk_start : chunk_start + this_chunk].to(dev)

        # shifted_ts[perm, event] = (src_offsets[event] + rand_offset[perm]) % duration + ts_min
        # Shape: [this_chunk, n_events]
        shifted = (
            src_offsets.unsqueeze(0) + rand_offsets.unsqueeze(1)
        ) % duration + ts_min

        # For each (perm, event), find entry price and forward price using searchsorted
        shifted_flat = shifted.reshape(-1)  # [this_chunk * n_events]

        entry_idx = torch.searchsorted(tgt_ts, shifted_flat)  # [this_chunk * n_events]

        # Accumulate returns for each perm in this chunk
        for horizon_ns in horizon_ns_list:
            fwd_ts_flat = shifted_flat + horizon_ns
            fwd_idx = torch.searchsorted(tgt_ts, fwd_ts_flat)  # [this_chunk * n_events]

            # Build valid mask: entry_idx < T and fwd_idx < T
            T = tgt_ts.shape[0]
            valid_mask = (entry_idx < T) & (fwd_idx < T)

            # Safe gather (clamp out-of-bounds indices so gather doesn't crash)
            safe_entry = entry_idx.clamp(0, T - 1)
            safe_fwd = fwd_idx.clamp(0, T - 1)

            entry_pr = tgt_pr[safe_entry]  # [this_chunk * n_events]
            fwd_pr = tgt_pr[safe_fwd]      # [this_chunk * n_events]

            # Filter zero/negative entry prices
            valid_mask = valid_mask & (entry_pr > 0)

            # net return in bps
            raw_ret = (fwd_pr / entry_pr - 1.0) * 10_000.0 * direction_sign - total_cost
            # Invalidate NaN/inf
            finite_mask = torch.isfinite(raw_ret)
            valid_mask = valid_mask & finite_mask

            # Reshape to [this_chunk, n_events]
            raw_ret_2d = raw_ret.reshape(this_chunk, n_events)
            valid_2d = valid_mask.reshape(this_chunk, n_events)

            for i in range(this_chunk):
                valid_row = valid_2d[i]
                if not valid_row.any():
                    null_means.append(float("nan"))
                    null_medians.append(float("nan"))
                    null_win_rates.append(float("nan"))
                    continue

                rets = raw_ret_2d[i][valid_row]
                rets_cpu = rets.cpu().tolist()
                iter_mean = sum(rets_cpu) / len(rets_cpu)
                sorted_rets = sorted(rets_cpu)
                n = len(sorted_rets)
                if n % 2 == 1:
                    iter_median = sorted_rets[n // 2]
                else:
                    iter_median = (sorted_rets[n // 2 - 1] + sorted_rets[n // 2]) / 2.0
                iter_win_rate = sum(1 for x in rets_cpu if x > 0) / n

                null_means.append(iter_mean)
                null_medians.append(iter_median)
                null_win_rates.append(iter_win_rate)

        # Discard chunk tensors to keep VRAM flat
        del shifted, shifted_flat, entry_idx

    # Compute percentiles
    percentiles = _compute_percentiles(null_means, null_win_rates)

    return {
        "null_mean_net_bps": null_means,
        "null_win_rates": null_win_rates,
        "null_median_net_bps": null_medians,
        "percentiles": percentiles,
        "iterations": iterations,
        "seed": seed,
        "shift_mode": shift_mode,
        "engine": "gpu",
        "device": device,
        "chunk_size": chunk_size,
        "safety_mode": SAFETY_MODE,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return float("nan")
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    rank = p / 100.0 * (n - 1)
    lower = int(math.floor(rank))
    upper = lower + 1
    if upper >= n:
        return sorted_values[-1]
    frac = rank - lower
    return sorted_values[lower] + frac * (sorted_values[upper] - sorted_values[lower])


def _compute_percentiles(
    null_means: list[float], null_win_rates: list[float]
) -> dict[str, float]:
    valid_means = sorted(x for x in null_means if math.isfinite(x))
    valid_wrs = sorted(x for x in null_win_rates if math.isfinite(x))

    out: dict[str, float] = {}
    if valid_means:
        out["mean_net_bps_p50"] = _percentile(valid_means, 50)
        out["mean_net_bps_p95"] = _percentile(valid_means, 95)
        out["mean_net_bps_p99"] = _percentile(valid_means, 99)
    else:
        out["mean_net_bps_p50"] = float("nan")
        out["mean_net_bps_p95"] = float("nan")
        out["mean_net_bps_p99"] = float("nan")

    if valid_wrs:
        out["win_rate_p50"] = _percentile(valid_wrs, 50)
        out["win_rate_p95"] = _percentile(valid_wrs, 95)
        out["win_rate_p99"] = _percentile(valid_wrs, 99)
    else:
        out["win_rate_p50"] = float("nan")
        out["win_rate_p95"] = float("nan")
        out["win_rate_p99"] = float("nan")

    return out


def _empty_null_result(
    iterations: int, seed: int, shift_mode: str, device: str, chunk_size: int
) -> dict[str, Any]:
    nan_list: list[float] = [float("nan")] * iterations
    return {
        "null_mean_net_bps": nan_list,
        "null_win_rates": nan_list,
        "null_median_net_bps": nan_list,
        "percentiles": {
            "mean_net_bps_p50": float("nan"),
            "mean_net_bps_p95": float("nan"),
            "mean_net_bps_p99": float("nan"),
            "win_rate_p50": float("nan"),
            "win_rate_p95": float("nan"),
            "win_rate_p99": float("nan"),
        },
        "iterations": iterations,
        "seed": seed,
        "shift_mode": shift_mode,
        "engine": "gpu",
        "device": device,
        "chunk_size": chunk_size,
        "safety_mode": SAFETY_MODE,
    }
