"""
GPU-accelerated forward-return kernel for venue_agnostic_signal_observer.

Pure compute. No network. No file writes. No capture logic.
No order/trading imports. No live logic. No auth.

This module accelerates the inner forward-return evaluation loop over many
events × horizons using chunked CUDA batching with torch.searchsorted.

Safety:
- safety_mode: public_data_observer_only
- No live trading, no order placement, no auth credentials, no execution.

Public API
----------
check_cuda_available(device)       -- (available: bool, reason: str)
gpu_unavailable_diagnostic(...)    -- standard diagnostic dict
batch_evaluate_signals_gpu(...)    -- GPU drop-in for evaluate_tick_signal loop
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .tick_models import QuoteTickLite
from .tick_models import TickForwardReturn
from .tick_models import TickSignalEvent
from .tick_models import TradeTickLite


if TYPE_CHECKING:
    pass  # torch imported lazily at call time

_MS_TO_NS: int = 1_000_000

SAFETY_MODE = "public_data_observer_only"
_MODULE_METADATA = {
    "safety_mode": SAFETY_MODE,
    "live_trading": False,
    "orders": False,
    "auth_credentials": False,
    "derivatives_execution": False,
}

DEFAULT_CHUNK_SIZE: int = 16_384


# ---------------------------------------------------------------------------
# CUDA availability
# ---------------------------------------------------------------------------

def check_cuda_available(device: str = "cuda:0") -> tuple[bool, str]:
    """Return (available, reason). Never raises."""
    try:
        import torch
    except ImportError:
        return False, "torch_not_installed"

    if not hasattr(torch, "cuda") or torch.cuda is None:
        return False, "torch_cuda_unavailable"

    if not torch.cuda.is_available():
        return False, "torch_cuda_unavailable"

    try:
        device_idx = int(device.split(":")[-1]) if ":" in device else 0
        if device_idx >= torch.cuda.device_count():
            return False, f"cuda_device_not_found:{device}"
    except (ValueError, IndexError):
        return False, f"invalid_device_string:{device}"

    return True, "cuda_available"


def gpu_unavailable_diagnostic(device: str, reason: str) -> dict:
    """Standard diagnostic dict when GPU is unavailable or explicitly rejected."""
    return {
        "verdict": "GPU_UNAVAILABLE_DIAGNOSTIC",
        "reason": reason,
        "device": device,
        "safety_mode": SAFETY_MODE,
    }


# ---------------------------------------------------------------------------
# GPU batch evaluator
# ---------------------------------------------------------------------------

def batch_evaluate_signals_gpu(
    signals: list[TickSignalEvent],
    target_ticks: list[TradeTickLite] | list[QuoteTickLite],
    horizons_ms: list[int],
    fee_bps: float,
    slippage_bps: float,
    quote_mismatch_buffer_bps: float = 0.0,
    quote_mismatch: bool = False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    device: str = "cuda:0",
) -> list[TickForwardReturn]:
    """
    GPU-accelerated drop-in replacement for the evaluate_tick_signal loop.

    Evaluates all signals against all horizons in one batched pass using
    torch.searchsorted on the CUDA device. Events are processed in chunks of
    ``chunk_size`` to keep VRAM flat regardless of dataset size.

    Semantics are identical to evaluate_tick_signal:
    - entry price: first target tick at or after signal.ts_event
    - forward price: first target tick at or after entry_ts + horizon_ns
    - direction-adjusted return: long → (fwd/entry - 1)*10000, short → negated
    - net return: dir_adj - (fee_bps + slippage_bps + quote_mismatch if applicable)
    - invalid result for: missing entry, missing forward, zero/non-finite prices

    Parameters
    ----------
    signals:
        Signal events to evaluate.
    target_ticks:
        Sorted target-venue ticks (TradeTickLite or QuoteTickLite).
    horizons_ms:
        Forward-return horizons in milliseconds.
    fee_bps, slippage_bps:
        Per-leg cost assumptions.
    quote_mismatch_buffer_bps:
        Additional cost when quote_mismatch is True.
    quote_mismatch:
        Whether to apply quote_mismatch_buffer_bps.
    chunk_size:
        Events per GPU batch. Default 16384. Affects speed only, not results.
    device:
        CUDA device string. Must be validated by the caller before use.

    Returns
    -------
    list[TickForwardReturn]
        One per (signal, horizon), in signal × horizon order.
        Length = len(signals) * len(horizons_ms).
    """
    import torch

    if not signals or not target_ticks or not horizons_ms:
        return []

    total_cost = fee_bps + slippage_bps + (quote_mismatch_buffer_bps if quote_mismatch else 0.0)

    # Build sorted (ts, price) arrays from target ticks
    if isinstance(target_ticks[0], TradeTickLite):
        raw_ts = [t.ts_event for t in target_ticks]  # type: ignore[union-attr]
        raw_pr = [t.price for t in target_ticks]     # type: ignore[union-attr]
    else:
        raw_ts = [t.ts_event for t in target_ticks]
        raw_pr = [t.mid for t in target_ticks]       # type: ignore[union-attr]

    # Filter non-finite and zero/negative prices from target (match CPU semantics)
    valid_pairs = [
        (ts, pr) for ts, pr in zip(raw_ts, raw_pr, strict=False)
        if math.isfinite(pr) and pr > 0
    ]
    if not valid_pairs:
        # No usable target ticks — every result is invalid
        return _all_invalid(signals, horizons_ms, fee_bps, slippage_bps,
                            quote_mismatch_buffer_bps if quote_mismatch else None,
                            "no_entry_reference_price")

    tgt_ts_list = [p[0] for p in valid_pairs]
    tgt_pr_list = [p[1] for p in valid_pairs]

    dev = torch.device(device)
    tgt_ts_gpu = torch.tensor(tgt_ts_list, dtype=torch.int64, device=dev)
    tgt_pr_gpu = torch.tensor(tgt_pr_list, dtype=torch.float64, device=dev)
    T = tgt_ts_gpu.shape[0]

    horizons_ns = [h * _MS_TO_NS for h in horizons_ms]

    # Pre-extract per-signal fields for fast access during assembly
    sig_ids = [s.signal_id for s in signals]
    sig_ts = [s.ts_event for s in signals]
    sig_dirs = [s.direction for s in signals]
    sig_tvenues = [s.target_venue for s in signals]
    sig_tsyms = [s.target_symbol for s in signals]

    # Direction sign tensor (float64): long=+1, short=-1
    dir_signs = [1.0 if d != "short" else -1.0 for d in sig_dirs]

    E = len(signals)
    H = len(horizons_ms)

    # Pre-allocate in (signal, horizon) order — same as evaluate_tick_signal produces.
    # results[event_idx * H + h_idx] = TickForwardReturn for that (signal, horizon) pair.
    results: list[TickForwardReturn | None] = [None] * (E * H)

    for chunk_start in range(0, E, chunk_size):
        chunk_end = min(chunk_start + chunk_size, E)
        chunk_len = chunk_end - chunk_start

        ev_ts_gpu = torch.tensor(
            sig_ts[chunk_start:chunk_end], dtype=torch.int64, device=dev
        )
        dir_sign_gpu = torch.tensor(
            dir_signs[chunk_start:chunk_end], dtype=torch.float64, device=dev
        )

        # Entry lookup: first target tick at or after event ts
        entry_idx = torch.searchsorted(tgt_ts_gpu, ev_ts_gpu)  # [chunk_len]
        entry_in_bounds = entry_idx < T                         # [chunk_len]

        safe_entry = entry_idx.clamp(0, T - 1)
        entry_ts_gpu = tgt_ts_gpu[safe_entry]                  # [chunk_len]
        entry_pr_gpu = tgt_pr_gpu[safe_entry]                  # [chunk_len]

        # Additional validity: price must be positive and finite (already filtered,
        # but defend against edge cases from clamped out-of-bounds reads)
        entry_pr_valid = entry_in_bounds & (entry_pr_gpu > 0) & torch.isfinite(entry_pr_gpu)

        # Bring to CPU for result assembly
        entry_in_bounds_cpu = entry_in_bounds.cpu().tolist()
        entry_pr_valid_cpu = entry_pr_valid.cpu().tolist()
        entry_pr_cpu = entry_pr_gpu.cpu().tolist()

        for h_idx, (horizon_ms, horizon_ns) in enumerate(zip(horizons_ms, horizons_ns, strict=False)):
            fwd_ts_gpu = entry_ts_gpu + horizon_ns              # [chunk_len]
            fwd_idx = torch.searchsorted(tgt_ts_gpu, fwd_ts_gpu)
            fwd_in_bounds = fwd_idx < T                         # [chunk_len]

            safe_fwd = fwd_idx.clamp(0, T - 1)
            fwd_pr_gpu = tgt_pr_gpu[safe_fwd]                  # [chunk_len]
            fwd_finite = torch.isfinite(fwd_pr_gpu)

            raw_ret = (fwd_pr_gpu / entry_pr_gpu - 1.0) * 10_000.0   # [chunk_len]
            dir_adj = raw_ret * dir_sign_gpu
            net_ret = dir_adj - total_cost

            fwd_in_bounds_cpu = fwd_in_bounds.cpu().tolist()
            fwd_finite_cpu = fwd_finite.cpu().tolist()
            fwd_pr_cpu = fwd_pr_gpu.cpu().tolist()
            raw_ret_cpu = raw_ret.cpu().tolist()
            dir_adj_cpu = dir_adj.cpu().tolist()
            net_ret_cpu = net_ret.cpu().tolist()

            for c in range(chunk_len):
                i = chunk_start + c
                slot = i * H + h_idx  # (signal, horizon) ordering
                sig_id = sig_ids[i]
                s_ts = sig_ts[i]
                t_venue = sig_tvenues[i]
                t_sym = sig_tsyms[i]
                entry_pr = entry_pr_cpu[c]
                fwd_pr = fwd_pr_cpu[c]

                if not entry_in_bounds_cpu[c]:
                    results[slot] = TickForwardReturn(
                        signal_id=sig_id,
                        signal_ts=s_ts,
                        target_venue=t_venue,
                        target_symbol=t_sym,
                        horizon_ms=horizon_ms,
                        entry_reference_price=None,
                        forward_price=None,
                        raw_return_bps=None,
                        direction_adjusted_return_bps=None,
                        fee_bps=fee_bps,
                        slippage_bps=slippage_bps,
                        quote_mismatch_buffer_bps=quote_mismatch_buffer_bps if quote_mismatch else None,
                        net_return_bps=None,
                        valid=False,
                        rejection_reason="no_entry_reference_price",
                    )
                    continue

                if not entry_pr_valid_cpu[c]:
                    results[slot] = TickForwardReturn(
                        signal_id=sig_id,
                        signal_ts=s_ts,
                        target_venue=t_venue,
                        target_symbol=t_sym,
                        horizon_ms=horizon_ms,
                        entry_reference_price=entry_pr,
                        forward_price=None,
                        raw_return_bps=None,
                        direction_adjusted_return_bps=None,
                        fee_bps=fee_bps,
                        slippage_bps=slippage_bps,
                        quote_mismatch_buffer_bps=quote_mismatch_buffer_bps if quote_mismatch else None,
                        net_return_bps=None,
                        valid=False,
                        rejection_reason="zero_or_nonfinite_entry_price",
                    )
                    continue

                if not fwd_in_bounds_cpu[c]:
                    results[slot] = TickForwardReturn(
                        signal_id=sig_id,
                        signal_ts=s_ts,
                        target_venue=t_venue,
                        target_symbol=t_sym,
                        horizon_ms=horizon_ms,
                        entry_reference_price=entry_pr,
                        forward_price=None,
                        raw_return_bps=None,
                        direction_adjusted_return_bps=None,
                        fee_bps=fee_bps,
                        slippage_bps=slippage_bps,
                        quote_mismatch_buffer_bps=quote_mismatch_buffer_bps if quote_mismatch else None,
                        net_return_bps=None,
                        valid=False,
                        rejection_reason=f"no_forward_price_at_{horizon_ms}ms",
                    )
                    continue

                if not fwd_finite_cpu[c]:
                    results[slot] = TickForwardReturn(
                        signal_id=sig_id,
                        signal_ts=s_ts,
                        target_venue=t_venue,
                        target_symbol=t_sym,
                        horizon_ms=horizon_ms,
                        entry_reference_price=entry_pr,
                        forward_price=fwd_pr,
                        raw_return_bps=None,
                        direction_adjusted_return_bps=None,
                        fee_bps=fee_bps,
                        slippage_bps=slippage_bps,
                        quote_mismatch_buffer_bps=quote_mismatch_buffer_bps if quote_mismatch else None,
                        net_return_bps=None,
                        valid=False,
                        rejection_reason="non_finite_forward_price",
                    )
                    continue

                results[slot] = TickForwardReturn(
                    signal_id=sig_id,
                    signal_ts=s_ts,
                    target_venue=t_venue,
                    target_symbol=t_sym,
                    horizon_ms=horizon_ms,
                    entry_reference_price=entry_pr,
                    forward_price=fwd_pr,
                    raw_return_bps=raw_ret_cpu[c],
                    direction_adjusted_return_bps=dir_adj_cpu[c],
                    fee_bps=fee_bps,
                    slippage_bps=slippage_bps,
                    quote_mismatch_buffer_bps=quote_mismatch_buffer_bps if quote_mismatch else None,
                    net_return_bps=net_ret_cpu[c],
                    valid=True,
                    rejection_reason=None,
                )

        # Discard chunk tensors
        del ev_ts_gpu, dir_sign_gpu, entry_idx, entry_in_bounds
        del safe_entry, entry_ts_gpu, entry_pr_gpu, entry_pr_valid

    return results  # type: ignore[return-value]  # all slots filled by construction


# ---------------------------------------------------------------------------
# Multi-GPU wrapper (opt-in, event sharding)
# ---------------------------------------------------------------------------


def batch_evaluate_signals_multi_gpu(
    signals: list[TickSignalEvent],
    target_ticks: list[TradeTickLite] | list[QuoteTickLite],
    horizons_ms: list[int],
    fee_bps: float,
    slippage_bps: float,
    quote_mismatch_buffer_bps: float = 0.0,
    quote_mismatch: bool = False,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    devices: list[str] | None = None,
) -> list[TickForwardReturn]:
    """
    Multi-GPU wrapper around :func:`batch_evaluate_signals_gpu`.

    Coarse-shards the event list contiguously across ``devices`` (preserving
    signal-major ordering), runs the existing per-device kernel on each
    shard, and concatenates the per-device results in shard order.

    Mathematical behaviour, result schema, and ordering are identical to the
    single-device path — when ``len(devices) == 1`` the output matches
    ``batch_evaluate_signals_gpu`` bit-for-bit because the shard equals the
    full event list.

    No silent CPU fallback. Caller must validate device availability
    upfront (see :func:`gpu_devices.validate_cuda_devices`).
    """
    from .gpu_devices import split_work_evenly

    if not signals or not target_ticks or not horizons_ms:
        return []
    if not devices:
        raise ValueError("multi-GPU path requires at least one device")

    if len(devices) == 1:
        return batch_evaluate_signals_gpu(
            signals=signals,
            target_ticks=target_ticks,
            horizons_ms=horizons_ms,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
            quote_mismatch=quote_mismatch,
            chunk_size=chunk_size,
            device=devices[0],
        )

    H = len(horizons_ms)
    shards = split_work_evenly(len(signals), len(devices))
    out: list[TickForwardReturn] = []
    for dev_str, shard in zip(devices, shards, strict=False):
        if len(shard) == 0:
            continue
        shard_signals = signals[shard.start:shard.stop]
        shard_results = batch_evaluate_signals_gpu(
            signals=shard_signals,
            target_ticks=target_ticks,
            horizons_ms=horizons_ms,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
            quote_mismatch=quote_mismatch,
            chunk_size=chunk_size,
            device=dev_str,
        )
        # Length per shard = len(shard_signals) * H — appended in shard order
        # which is the same as original signal order because shards are
        # contiguous and ordered.
        assert len(shard_results) == len(shard_signals) * H
        out.extend(shard_results)
    return out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _all_invalid(
    signals: list[TickSignalEvent],
    horizons_ms: list[int],
    fee_bps: float,
    slippage_bps: float,
    qm_bps: float | None,
    reason: str,
) -> list[TickForwardReturn]:
    """Return an all-invalid result list when target ticks are unusable."""
    out: list[TickForwardReturn] = []
    for sig in signals:
        for h in horizons_ms:
            out.append(TickForwardReturn(
                signal_id=sig.signal_id,
                signal_ts=sig.ts_event,
                target_venue=sig.target_venue,
                target_symbol=sig.target_symbol,
                horizon_ms=h,
                entry_reference_price=None,
                forward_price=None,
                raw_return_bps=None,
                direction_adjusted_return_bps=None,
                fee_bps=fee_bps,
                slippage_bps=slippage_bps,
                quote_mismatch_buffer_bps=qm_bps,
                net_return_bps=None,
                valid=False,
                rejection_reason=reason,
            ))
    return out
