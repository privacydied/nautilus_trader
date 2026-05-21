"""
Shared multi-GPU device helpers for venue_agnostic_signal_observer.

Pure utility module. No network, no file writes, no capture logic, no orders,
no auth, no execution.

These helpers are used by the GPU-accelerated diagnostic modules
(``forward_returns_gpu``, ``permutation_null_gpu``, ``lead_lag_heatmap_gpu``)
to support opt-in multi-GPU sharding without changing the math, verdicts, or
hidden-CPU-fallback policy of those modules.

Public API
----------
parse_cuda_devices(devices_arg, fallback_device, engine)
validate_cuda_devices(devices)
split_work_evenly(n_items, n_devices)
derive_per_device_seeds(base_seed, n_devices)
benchmark_metadata(...)
"""
from __future__ import annotations

import hashlib


SAFETY_MODE = "public_data_observer_only"


def parse_cuda_devices(
    devices_arg: str | None,
    fallback_device: str = "cuda:0",
    engine: str = "gpu",
) -> list[str]:
    """
    Parse a ``--devices`` CSV string into a list of CUDA device strings.

    Behaviour:
    - ``engine != "gpu"`` -> ``[]`` (CPU path keeps single-device semantics
      and never enters GPU code, so we return an empty list).
    - ``devices_arg`` is None or empty -> ``[fallback_device]`` (preserves
      legacy single-``--device`` behaviour).
    - Otherwise splits on commas, strips whitespace, deduplicates while
      preserving order, and returns the list.

    This function does NOT call ``torch`` and does NOT check device
    availability — that's :func:`validate_cuda_devices`. Splitting parsing
    from validation lets callers exit with a clean
    ``GPU_UNAVAILABLE_DIAGNOSTIC`` rather than mixing concerns.
    """
    if engine != "gpu":
        return []
    if devices_arg is None or not devices_arg.strip():
        return [fallback_device]
    parts: list[str] = []
    seen: set[str] = set()
    for raw in devices_arg.split(","):
        d = raw.strip()
        if not d:
            continue
        if d in seen:
            raise ValueError(f"duplicate device in --devices: {d!r}")
        if not d.startswith("cuda:"):
            raise ValueError(
                f"invalid device string {d!r}: must be of the form 'cuda:N'"
            )
        seen.add(d)
        parts.append(d)
    if not parts:
        return [fallback_device]
    return parts


def validate_cuda_devices(devices: list[str]) -> tuple[bool, str]:
    """
    Check that every requested CUDA device exists and is usable.

    Returns ``(True, "cuda_available")`` if all devices pass, otherwise
    ``(False, reason)`` where ``reason`` names the first failing device.

    Never raises. Never falls back silently — the caller decides what to do
    on failure (typical pattern: emit ``GPU_UNAVAILABLE_DIAGNOSTIC`` and
    exit, do NOT downgrade to CPU).
    """
    if not devices:
        return False, "no_devices_requested"
    try:
        import torch
    except ImportError:
        return False, "torch_not_installed"
    if not hasattr(torch, "cuda") or torch.cuda is None:
        return False, "torch_cuda_unavailable"
    if not torch.cuda.is_available():
        return False, "torch_cuda_unavailable"
    n = torch.cuda.device_count()
    for d in devices:
        try:
            idx = int(d.split(":")[-1])
        except (ValueError, IndexError):
            return False, f"invalid_device_string:{d}"
        if idx < 0 or idx >= n:
            return False, f"cuda_device_not_found:{d}"
    return True, "cuda_available"


def split_work_evenly(n_items: int, n_devices: int) -> list[range]:
    """
    Split ``n_items`` of indexed work into ``n_devices`` contiguous ranges.

    Guarantees:
    - The returned ranges cover ``[0, n_items)`` exactly once, with no
      overlap and no gap.
    - Order of ranges matches device order, so gathering results in range
      order reconstructs original-item order.
    - When ``n_items < n_devices`` the first ``n_items`` ranges have length
      one, the rest are empty (preserving the one-range-per-device shape).
    - ``n_items == 0`` -> all empty ranges.

    The remainder (``n_items % n_devices``) is distributed across the
    leading ranges so the load imbalance is at most one item per device.
    """
    if n_devices <= 0:
        raise ValueError(f"n_devices must be >= 1, got {n_devices}")
    if n_items < 0:
        raise ValueError(f"n_items must be >= 0, got {n_items}")
    base, rem = divmod(n_items, n_devices)
    out: list[range] = []
    start = 0
    for i in range(n_devices):
        size = base + (1 if i < rem else 0)
        out.append(range(start, start + size))
        start += size
    assert start == n_items
    return out


def derive_per_device_seeds(base_seed: int, n_devices: int) -> list[int]:
    """
    Derive ``n_devices`` deterministic per-device seeds from ``base_seed``.

    Uses BLAKE2b on ``(base_seed, device_index)`` so the mapping is
    reproducible across runs and machines, and any small change to
    ``base_seed`` or device count produces a fresh seed sequence.

    The returned seeds are 64-bit non-negative integers — within the range
    accepted by numpy/torch generators.
    """
    if n_devices <= 0:
        raise ValueError(f"n_devices must be >= 1, got {n_devices}")
    seeds: list[int] = []
    for i in range(n_devices):
        h = hashlib.blake2b(
            f"vasos:gpu:seed:{base_seed}:{i}".encode(),
            digest_size=8,
        )
        seeds.append(int.from_bytes(h.digest(), "big"))
    return seeds


def benchmark_metadata(
    engine: str,
    devices: list[str],
    workload: dict,
    elapsed_seconds: float,
    per_device_shards: list[int] | None = None,
    batch_size: int | None = None,
) -> dict:
    """
    Build a standard benchmark/timing metadata dict for GPU runs.

    This is diagnostic only — it MUST NOT be consumed by verdict, candidate,
    FDR, or rejection logic. Callers should attach it as a sidecar field on
    reports, not promote any of its values into hypothesis evaluation.
    """
    return {
        "engine": engine,
        "devices": list(devices),
        "num_devices": len(devices),
        "batch_size": batch_size,
        "workload": dict(workload),
        "elapsed_seconds": float(elapsed_seconds),
        "per_device_shards": (
            list(per_device_shards) if per_device_shards is not None else None
        ),
        "safety_mode": SAFETY_MODE,
        "diagnostic_only": True,
    }
