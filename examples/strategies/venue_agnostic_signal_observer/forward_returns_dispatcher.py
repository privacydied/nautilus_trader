"""
Forward-return engine dispatcher for cross-asset beta-lag archive runner.

Dispatches between CPU (bisect-based) and GPU (torch.searchsorted) forward-return
computation. Both paths produce identical TickForwardReturn outputs.

Public API
----------
compute_forward_returns_batch(
    labels, ts_arr, pr_arr, target_symbol, horizons_ms,
    *, engine, device, batch_size, VENUE, ENTRY_DELAY_NS,
    MS_TO_NS, FEE_BPS, SLIPPAGE_BPS, QUOTE_MISMATCH_BUFFER_BPS,
) -> list[TickForwardReturn]

compute_forward_returns_single(
    label, ts_arr, pr_arr, target_symbol, horizons_ms,
    *, engine, device, batch_size, VENUE, ENTRY_DELAY_NS,
    MS_TO_NS, FEE_BPS, SLIPPAGE_BPS, QUOTE_MISMATCH_BUFFER_BPS,
) -> list[TickForwardReturn]

Safety:
- observer-only data math, no execution, no orders, no auth.
"""

from __future__ import annotations

from typing import Any
from typing import List
from typing import Sequence

from .tick_models import TickForwardReturn
from .tick_models import TickSignalEvent
from .tick_models import TradeTickLite


# ---------------------------------------------------------------------------
# Direction mapping: StressLabel uses "bullish"/"bearish",
# TickSignalEvent uses "long"/"short".
# ---------------------------------------------------------------------------

_DIRECTION_MAP: dict[str, str] = {
    "bullish": "long",
    "bearish": "short",
}


def _stress_label_to_signal_event(
    label: Any,
    target_symbol: str,
    target_venue: str,
    entry_delay_ns: int,
) -> TickSignalEvent:
    """Convert a StressLabel into a TickSignalEvent for the GPU path.

    The GPU kernel uses signal.ts_event as the entry lookup timestamp directly.
    The CPU path adds ENTRY_DELAY_NS to stress_end_ns before looking up entry
    price. To match semantics, we pre-add ENTRY_DELAY_NS into ts_event.
    """
    direction = _DIRECTION_MAP.get(label.direction, label.direction)
    if direction not in ("long", "short"):
        direction = "long" if label.direction == "bullish" else "short"

    # GPU kernel uses ts_event as the entry lookup time directly,
    # so we must add the entry delay here to match CPU semantics.
    ts_event = label.stress_end_ns + entry_delay_ns

    return TickSignalEvent(
        signal_id=label.label_id,
        ts_event=ts_event,
        source_venue=target_venue,
        source_symbol=label.source_symbol,
        target_venue=target_venue,
        target_symbol=target_symbol,
        asset=target_symbol.replace("USDT", ""),
        signal_type="cross_asset_beta_lag",
        direction=direction,
        lookback_ms=label.stress_window_seconds * 1_000,
        threshold_bps=0.0,
        source_move_bps=label.source_move_bps,
        source_start_price=label.source_start_price,
        source_end_price=label.source_end_price,
        strength=abs(label.source_move_bps),
    )


def _ts_prices_to_trade_ticks(
    ts_arr: Sequence[int],
    pr_arr: Sequence[float],
    target_symbol: str,
    target_venue: str,
) -> List[TradeTickLite]:
    """Convert (timestamps, prices) arrays into TradeTickLite objects."""
    ticks: List[TradeTickLite] = []
    for ts, pr in zip(ts_arr, pr_arr, strict=False):
        ticks.append(TradeTickLite(
            ts_event=int(ts),
            venue=target_venue,
            symbol=target_symbol,
            price=float(pr),
            size=1.0,
            side="unknown",
        ))
    return ticks


def compute_forward_returns_batch(
    labels: List[Any],
    ts_arr: Sequence[int],
    pr_arr: Sequence[float],
    target_symbol: str,
    horizons_ms: List[int],
    *,
    engine: str = "cpu",
    device: str = "cuda:0",
    batch_size: int = 16384,
    VENUE: str,
    ENTRY_DELAY_NS: int,
    MS_TO_NS: int,
    FEE_BPS: float,
    SLIPPAGE_BPS: float,
    QUOTE_MISMATCH_BUFFER_BPS: float,
) -> List[TickForwardReturn]:
    """
    Compute forward returns for a batch of stress labels on one target symbol.

    Parameters
    ----------
    labels : list of StressLabel
    ts_arr : timestamps (ns) array
    pr_arr : prices array
    target_symbol : target symbol string
    horizons_ms : forward-return horizons in ms
    engine : "cpu" or "gpu"
    device : CUDA device string (only for GPU engine)
    batch_size : GPU chunk size (only for GPU engine)
    VENUE, ENTRY_DELAY_NS, MS_TO_NS, FEE_BPS, SLIPPAGE_BPS, QUOTE_MISMATCH_BUFFER_BPS :
        forwarded to respective engines

    Returns
    -------
    list[TickForwardReturn]
        One per (label, horizon), in label-major order.
    """
    if engine == "gpu":
        return _compute_gpu_batch(
            labels, ts_arr, pr_arr, target_symbol, horizons_ms,
            device=device, batch_size=batch_size,
            VENUE=VENUE, ENTRY_DELAY_NS=ENTRY_DELAY_NS,
            FEE_BPS=FEE_BPS, SLIPPAGE_BPS=SLIPPAGE_BPS,
            QUOTE_MISMATCH_BUFFER_BPS=QUOTE_MISMATCH_BUFFER_BPS,
        )
    else:
        # CPU path -- default
        return _compute_cpu_batch(
            labels, ts_arr, pr_arr, target_symbol, horizons_ms,
            VENUE=VENUE, ENTRY_DELAY_NS=ENTRY_DELAY_NS,
            MS_TO_NS=MS_TO_NS, FEE_BPS=FEE_BPS,
            SLIPPAGE_BPS=SLIPPAGE_BPS,
            QUOTE_MISMATCH_BUFFER_BPS=QUOTE_MISMATCH_BUFFER_BPS,
        )


def compute_forward_returns_single(
    label: Any,
    ts_arr: Sequence[int],
    pr_arr: Sequence[float],
    target_symbol: str,
    horizons_ms: List[int],
    *,
    engine: str = "cpu",
    device: str = "cuda:0",
    batch_size: int = 16384,
    VENUE: str,
    ENTRY_DELAY_NS: int,
    MS_TO_NS: int,
    FEE_BPS: float,
    SLIPPAGE_BPS: float,
    QUOTE_MISMATCH_BUFFER_BPS: float,
) -> List[TickForwardReturn]:
    """
    Compute forward returns for a single stress label on one target symbol.

    Convenience wrapper around compute_forward_returns_batch for single-label
    calls. Falls through to the same dispatch logic.
    """
    return compute_forward_returns_batch(
        [label], ts_arr, pr_arr, target_symbol, horizons_ms,
        engine=engine, device=device, batch_size=batch_size,
        VENUE=VENUE, ENTRY_DELAY_NS=ENTRY_DELAY_NS,
        MS_TO_NS=MS_TO_NS, FEE_BPS=FEE_BPS,
        SLIPPAGE_BPS=SLIPPAGE_BPS,
        QUOTE_MISMATCH_BUFFER_BPS=QUOTE_MISMATCH_BUFFER_BPS,
    )


# ---------------------------------------------------------------------------
# Internal: CPU batch
# ---------------------------------------------------------------------------

def _compute_cpu_batch(
    labels: List[Any],
    ts_arr: Sequence[int],
    pr_arr: Sequence[float],
    target_symbol: str,
    horizons_ms: List[int],
    *,
    VENUE: str,
    ENTRY_DELAY_NS: int,
    MS_TO_NS: int,
    FEE_BPS: float,
    SLIPPAGE_BPS: float,
    QUOTE_MISMATCH_BUFFER_BPS: float,
) -> List[TickForwardReturn]:
    """CPU forward returns: per-label loop using existing bisect path."""
    from .streaming_stress_labels import compute_forward_returns_from_ts_prices

    all_results: List[TickForwardReturn] = []
    for lbl in labels:
        frs = compute_forward_returns_from_ts_prices(
            lbl, ts_arr, pr_arr, target_symbol, horizons_ms,
            TickForwardReturn=TickForwardReturn,
            VENUE=VENUE,
            ENTRY_DELAY_NS=ENTRY_DELAY_NS,
            MS_TO_NS=MS_TO_NS,
            FEE_BPS=FEE_BPS,
            SLIPPAGE_BPS=SLIPPAGE_BPS,
            QUOTE_MISMATCH_BUFFER_BPS=QUOTE_MISMATCH_BUFFER_BPS,
        )
        all_results.extend(frs)
    return all_results


# ---------------------------------------------------------------------------
# Internal: GPU batch
# ---------------------------------------------------------------------------

def _compute_gpu_batch(
    labels: List[Any],
    ts_arr: Sequence[int],
    pr_arr: Sequence[float],
    target_symbol: str,
    horizons_ms: List[int],
    *,
    device: str,
    batch_size: int,
    VENUE: str,
    ENTRY_DELAY_NS: int,
    FEE_BPS: float,
    SLIPPAGE_BPS: float,
    QUOTE_MISMATCH_BUFFER_BPS: float,
) -> List[TickForwardReturn]:
    """GPU forward returns: batch evaluation via forward_returns_gpu."""
    from .forward_returns_gpu import batch_evaluate_signals_gpu
    from .forward_returns_gpu import check_cuda_available

    cuda_ok, reason = check_cuda_available(device)
    if not cuda_ok:
        raise RuntimeError(
            f"GPU_UNAVAILABLE_DIAGNOSTIC: forward-engine=gpu but CUDA "
            f"unavailable on {device}: {reason}"
        )

    # Convert labels to TickSignalEvents
    signals = [
        _stress_label_to_signal_event(lbl, target_symbol, VENUE, ENTRY_DELAY_NS)
        for lbl in labels
    ]

    # Convert ts/pr arrays to TradeTickLite
    target_ticks = _ts_prices_to_trade_ticks(ts_arr, pr_arr, target_symbol, VENUE)

    return batch_evaluate_signals_gpu(
        signals=signals,
        target_ticks=target_ticks,
        horizons_ms=horizons_ms,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        quote_mismatch_buffer_bps=QUOTE_MISMATCH_BUFFER_BPS,
        quote_mismatch=True,
        chunk_size=batch_size,
        device=device,
    )
