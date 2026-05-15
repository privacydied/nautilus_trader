"""GPU-accelerated trade-flow impulse signal generation.

Pure compute. No network. No file writes. No capture logic.
No order/trading imports. No live logic. No auth.

This module provides GPU-accelerated equivalents for the three
heavy signal-generation paths:

- notional_burst
- large_trade
- signed_imbalance

CPU remains default. GPU is explicit via --signal-engine gpu.

Safety:
- safety_mode: public_data_observer_only
- No live trading, no order placement, no auth credentials, no execution.
"""

from __future__ import annotations

import math
import uuid
from typing import Any

from .tick_models import TickSignalEvent, TradeTickLite
from .trade_flow_impulse import (
    TradeFlowImpulseConfig,
    _finite_notional,
    _MS_TO_NS,
)


def _resolve_direction(trades, idx, lookback_ms, signal_type=None):
    """Resolve signal direction from source price move over the lookback.
    CPU-ported from TradeFlowImpulseSignalGenerator._resolve_direction."""
    lookback_ns = lookback_ms * _MS_TO_NS
    if idx > 0:
        ts = trades[idx].ts_event
        ref_idx = idx
        while ref_idx > 0 and ts - trades[ref_idx].ts_event <= lookback_ns:
            ref_idx -= 1
        ref_idx = ref_idx + 1 if ref_idx + 1 <= idx else idx
        ref_price = trades[ref_idx].price
        curr_price = trades[idx].price
        if curr_price >= ref_price:
            return "long"
        return "short"
    if trades[idx].side == "buy":
        return "long"
    if trades[idx].side == "sell":
        return "short"
    return "long"

SAFETY_MODE = "public_data_observer_only"

_MS_TO_NS = 1_000_000


def check_cuda_available(device: str = "cuda:0") -> tuple[bool, str]:
    """Return (available, reason). Never raises."""
    try:
        import torch  # noqa: PLC0415
    except ImportError:
        return False, "torch_not_installed"
    if not torch.cuda.is_available():
        return False, "torch_cuda_unavailable"
    try:
        device_idx = int(device.split(":")[-1]) if ":" in device else 0
        if device_idx >= torch.cuda.device_count():
            return False, f"cuda_device_not_found:{device}"
    except (ValueError, IndexError):
        return False, f"invalid_device_string:{device}"
    return True, "cuda_available"


def _check_all_devices(devices_str: str) -> list[str]:
    """Validate all devices, fail fast on unavailable. Returns device list."""
    import sys as _sys
    parts = [d.strip() for d in devices_str.split(",") if d.strip()]
    if not parts:
        return []
    seen: set[str] = set()
    for d in parts:
        if not d.startswith("cuda:"):
            print(f"ERROR: invalid signal device: {d}", file=_sys.stderr)
            _sys.exit(1)
        if d in seen:
            print(f"ERROR: duplicate signal device: {d}", file=_sys.stderr)
            _sys.exit(1)
        seen.add(d)
        avail, reason = check_cuda_available(d)
        if not avail:
            print(f"ERROR: signal device {d} unavailable: {reason}", file=_sys.stderr)
            _sys.exit(1)
    return parts


def _trades_to_tensors(
    trades: list[TradeTickLite],
    device: str,
) -> tuple["torch.Tensor", "torch.Tensor", "torch.Tensor", "torch.Tensor", "torch.Tensor"]:
    """Convert trade list to GPU tensors.

    Returns (ts, price, size, notional, signed_notional, side_long).
    side_long = 1.0 for buy, -1.0 for sell, 0.0 for unknown.
    """
    import torch  # noqa: PLC0415
    n = len(trades)
    ts = torch.zeros(n, dtype=torch.int64, device=device)
    price = torch.zeros(n, dtype=torch.float64, device=device)
    size = torch.zeros(n, dtype=torch.float64, device=device)
    side_long = torch.zeros(n, dtype=torch.float64, device=device)

    for i, t in enumerate(trades):
        ts[i] = t.ts_event
        price[i] = t.price
        size[i] = t.size
        if t.side == "buy":
            side_long[i] = 1.0
        elif t.side == "sell":
            side_long[i] = -1.0
        else:
            side_long[i] = 0.0

    # Notional = price * size, 0 where non-finite
    notional = price * size
    finite_mask = torch.isfinite(price) & (price > 0) & torch.isfinite(size) & (size > 0)
    notional = torch.where(finite_mask, notional, torch.zeros_like(notional))

    return ts, price, size, notional, side_long


def _apply_cooldown(
    candidate_indices: list[int],
    candidate_timestamps: list[int],
    trades_ts: list[int],
    cooldown_ns: int,
) -> list[int]:
    """Filter candidate indices by cooldown: suppress any candidate
    within cooldown_ns of the previous emitted candidate."""
    if not candidate_indices:
        return []
    result = [candidate_indices[0]]
    last_ts = candidate_timestamps[0]
    for idx, ts in zip(candidate_indices[1:], candidate_timestamps[1:]):
        if ts - last_ts >= cooldown_ns:
            result.append(idx)
            last_ts = ts
    return result


# ---------------------------------------------------------------------------
# notional_burst GPU
# ---------------------------------------------------------------------------


def notional_burst_gpu(
    trades: list[TradeTickLite],
    config: TradeFlowImpulseConfig,
    device: str = "cuda:0",
) -> list[TickSignalEvent]:
    """GPU-accelerated notional_burst signal generation.

    Exact semantic parity with TradeFlowImpulseSignalGenerator._notional_burst.
    CPU remains default. GPU only when explicitly requested.
    """
    import torch  # noqa: PLC0415
    events: list[TickSignalEvent] = []
    cooldown_ns = config.cooldown_ms * _MS_TO_NS
    ts_cpu = [t.ts_event for t in trades]
    ts_gpu, price_gpu, size_gpu, notional_gpu, _ = _trades_to_tensors(trades, device)

    for lookback_ms in config.flow_lookbacks_ms or []:
        param_key = f"notional_burst_{lookback_ms}"
        last_ts = -1
        lookback_ns = lookback_ms * _MS_TO_NS
        bl_ns = config.baseline_window_ms * _MS_TO_NS

        # Prefix sum for O(1) window sums
        prefix = torch.cumsum(notional_gpu, dim=0)

        n = len(trades)
        # Compute lookback window bounds via searchsorted
        lookback_start_ns = ts_gpu - lookback_ns
        # For each trade, find the first index within lookback
        lb_lo = torch.searchsorted(ts_gpu, lookback_start_ns).to(torch.int64)
        # Clip to valid range
        lb_lo = torch.clamp(lb_lo, 0, n - 1)

        # Compute window notionals: prefix[idx+1] - prefix[lb_lo]
        # prefix shifted by 1: prefix_left = prefix[lb_lo - 1] if lb_lo > 0 else 0
        prefix_shifted = torch.zeros(n + 1, dtype=torch.float64, device=device)
        prefix_shifted[1:] = prefix
        window_notionals = prefix_shifted[1:n + 1] - prefix_shifted[lb_lo]

        # Baseline window: for each idx, sample ~10 sub-windows from [ts - bl_ns, ts)
        # Compute baseline_start bounds
        bl_lo = torch.searchsorted(ts_gpu, ts_gpu - bl_ns).to(torch.int64)
        bl_lo = torch.clamp(bl_lo, 0, n - 1)

        baseline_medians = torch.zeros(n, dtype=torch.float64, device=device)

        for idx in range(1, n):
            bl_start = int(bl_lo[idx].item())
            bl_end = idx  # exclusive
            if bl_end - bl_start <= 1:
                continue

            # Sample up to 10 positions
            bl_len = bl_end - bl_start
            step = max(1, bl_len // 10)
            samples: list[float] = []
            for s in range(bl_start, bl_end, step):
                e = min(s + 1, bl_end)
                window_sum = float(prefix_shifted[e].item() - prefix_shifted[s].item())
                if math.isfinite(window_sum) and window_sum > 0:
                    samples.append(window_sum)

            if not samples:
                continue
            samples.sort()
            median_val = samples[len(samples) // 2]
            baseline_medians[idx] = median_val

        # Find candidates
        burst_ratios = window_notionals / torch.clamp(baseline_medians, min=1e-12)
        # Mask: burst_ratio >= multiplier, median > 0, window_notional finite
        valid_mask = (
            (baseline_medians > 0)
            & torch.isfinite(window_notionals)
            & torch.isfinite(burst_ratios)
            & (burst_ratios >= config.notional_burst_multiplier)
        )

        candidate_indices = torch.nonzero(valid_mask).squeeze(-1).tolist()
        if not isinstance(candidate_indices, list):
            candidate_indices = [candidate_indices] if torch.is_tensor(candidate_indices) and candidate_indices.ndim == 0 else []

        # Apply cooldown
        cand_ts = [ts_cpu[i] for i in candidate_indices] if candidate_indices else []
        filtered = _apply_cooldown(candidate_indices, cand_ts, ts_cpu, cooldown_ns)

        for idx in filtered:
            ts = ts_cpu[idx]
            if ts - last_ts < cooldown_ns:
                continue
            wn = float(window_notionals[idx].item())
            mn = float(baseline_medians[idx].item())
            br = float(burst_ratios[idx].item())
            direction = _resolve_direction(trades, idx, lookback_ms)
            evt = TickSignalEvent(
                signal_id=str(uuid.uuid4()),
                ts_event=ts,
                source_venue=config.source_venue,
                source_symbol=config.symbol,
                target_venue=config.target_venue,
                target_symbol=config.symbol,
                asset=config.asset,
                signal_type="trade_flow_impulse",
                direction=direction,
                lookback_ms=lookback_ms,
                threshold_bps=0.0,
                source_move_bps=0.0,
                source_start_price=0.0,
                source_end_price=0.0,
                strength=br,
                metadata={
                    "flow_signal_type": "notional_burst",
                    "lookback_ms": lookback_ms,
                    "baseline_window_ms": config.baseline_window_ms,
                    "notional": round(wn, 2),
                    "median_notional": round(mn, 2),
                    "burst_ratio": round(br, 4),
                    "side_source": "exchange",
                },
            )
            events.append(evt)
            last_ts = ts

    return events


# ---------------------------------------------------------------------------
# large_trade GPU
# ---------------------------------------------------------------------------


def large_trade_gpu(
    trades: list[TradeTickLite],
    config: TradeFlowImpulseConfig,
    device: str = "cuda:0",
) -> list[TickSignalEvent]:
    """GPU-accelerated large_trade signal generation."""
    import torch  # noqa: PLC0415
    events: list[TickSignalEvent] = []
    cooldown_ns = config.cooldown_ms * _MS_TO_NS
    param_key = "large_trade"
    last_ts = -1
    bl_ns = config.baseline_window_ms * _MS_TO_NS

    ts_gpu, price_gpu, size_gpu, notional_gpu, _ = _trades_to_tensors(trades, device)
    n = len(trades)
    ts_cpu = [t.ts_event for t in trades]

    # Track which indices are large trades (exceed min_notional)
    if config.large_trade_min_notional_usd > 0:
        min_notional_mask = notional_gpu >= config.large_trade_min_notional_usd
    else:
        min_notional_mask = notional_gpu > 0

    # For each index, compute rolling median of notionals in baseline window
    candidate_flags = torch.zeros(n, dtype=torch.bool, device=device)

    for idx in range(n):
        if not min_notional_mask[idx].item():
            continue

        ts = ts_cpu[idx]
        notional_val = float(notional_gpu[idx].item())
        if notional_val <= 0 or not math.isfinite(notional_val):
            continue

        # Rolling window: find baseline start
        bl_start = int(torch.searchsorted(ts_gpu, ts - bl_ns).item())
        bl_end = max(0, idx)

        # Collect finite positive notionals in window
        window_notionals = notional_gpu[bl_start:bl_end]
        mask = window_notionals > 0
        finite_wn = window_notionals[mask]
        if finite_wn.numel() < 5:
            candidate_flags[idx] = False
            continue

        # Median via torch.median
        median_n = float(torch.median(finite_wn).item())
        if median_n > 0 and notional_val < median_n * config.large_trade_multiplier:
            continue

        candidate_flags[idx] = True

    # Extract candidate indices, apply cooldown
    cand_indices = torch.nonzero(candidate_flags).squeeze(-1).tolist()
    if not isinstance(cand_indices, list):
        cand_indices = [cand_indices] if torch.is_tensor(cand_indices) and cand_indices.ndim == 0 else []
    cand_ts = [ts_cpu[i] for i in cand_indices] if cand_indices else []
    filtered = _apply_cooldown(cand_indices, cand_ts, ts_cpu, cooldown_ns)

    for idx in filtered:
        ts = ts_cpu[idx]
        if ts - last_ts < cooldown_ns:
            continue
        trade = trades[idx]
        notional_val = float(notional_gpu[idx].item())
        if trade.side in ("buy", "sell"):
            direction = "long" if trade.side == "buy" else "short"
        else:
            direction = _resolve_direction(trades, idx, 1000)

        evt = TickSignalEvent(
            signal_id=str(uuid.uuid4()),
            ts_event=ts,
            source_venue=config.source_venue,
            source_symbol=config.symbol,
            target_venue=config.target_venue,
            target_symbol=config.symbol,
            asset=config.asset,
            signal_type="trade_flow_impulse",
            direction=direction,
            lookback_ms=0,
            threshold_bps=0.0,
            source_move_bps=0.0,
            source_start_price=trade.price,
            source_end_price=trade.price,
            strength=notional_val,
            metadata={
                "flow_signal_type": "large_trade",
                "large_trade_notional": round(notional_val, 2),
                "side_source": "exchange",
            },
        )
        events.append(evt)
        last_ts = ts

    return events


# ---------------------------------------------------------------------------
# signed_imbalance GPU
# ---------------------------------------------------------------------------


def signed_imbalance_gpu(
    trades: list[TradeTickLite],
    config: TradeFlowImpulseConfig,
    device: str = "cuda:0",
) -> list[TickSignalEvent]:
    """GPU-accelerated signed_imbalance signal generation."""
    import torch  # noqa: PLC0415
    events: list[TickSignalEvent] = []
    cooldown_ns = config.cooldown_ms * _MS_TO_NS
    ts_cpu = [t.ts_event for t in trades]
    ts_gpu, price_gpu, size_gpu, notional_gpu, side_gpu = _trades_to_tensors(trades, device)
    n = len(trades)

    # Signed notionals: buy = +notional, sell = -notional, unknown = 0
    signed_notional = notional_gpu * side_gpu

    # Prefix sum of absolute notional (for total_n)
    prefix_abs = torch.zeros(n + 1, dtype=torch.float64, device=device)
    prefix_abs[1:] = torch.cumsum(notional_gpu, dim=0)

    # Prefix sum of signed notional (for buy - sell)
    prefix_signed = torch.zeros(n + 1, dtype=torch.float64, device=device)
    prefix_signed[1:] = torch.cumsum(signed_notional, dim=0)

    for lookback_ms in config.flow_lookbacks_ms or []:
        param_key = f"signed_imbalance_{lookback_ms}"
        last_ts = -1
        lookback_ns = lookback_ms * _MS_TO_NS

        # Lookback window start indices
        lb_lo = torch.searchsorted(ts_gpu, ts_gpu - lookback_ns).to(torch.int64)
        lb_lo = torch.clamp(lb_lo, 0, n - 1)

        # Window totals via prefix diff
        total_n = prefix_abs[1:n + 1] - prefix_abs[lb_lo]
        signed_sum = prefix_signed[1:n + 1] - prefix_signed[lb_lo]

        buy_n = (total_n + signed_sum) / 2.0
        sell_n = (total_n - signed_sum) / 2.0
        imbalance = torch.where(total_n > 0, signed_sum / total_n, torch.zeros_like(total_n))

        # Min trades check
        window_counts = (torch.arange(n, device=device) - lb_lo + 1).to(torch.float64)

        valid_mask = (
            (total_n > 0)
            & torch.isfinite(total_n)
            & torch.isfinite(imbalance)
            & (window_counts >= config.min_trades_in_window)
            & (torch.abs(imbalance) >= config.imbalance_threshold)
        )

        candidate_indices = torch.nonzero(valid_mask).squeeze(-1).tolist()
        if not isinstance(candidate_indices, list):
            candidate_indices = [candidate_indices] if torch.is_tensor(candidate_indices) and candidate_indices.ndim == 0 else []

        # Apply cooldown
        cand_ts = [ts_cpu[i] for i in candidate_indices] if candidate_indices else []
        filtered = _apply_cooldown(candidate_indices, cand_ts, ts_cpu, cooldown_ns)

        for idx in filtered:
            ts = ts_cpu[idx]
            if ts - last_ts < cooldown_ns:
                continue
            imb = float(imbalance[idx].item())
            direction = "long" if imb > 0 else "short"
            bn = float(buy_n[idx].item()) if idx < n else 0.0
            sn = float(sell_n[idx].item()) if idx < n else 0.0
            evt = TickSignalEvent(
                signal_id=str(uuid.uuid4()),
                ts_event=ts,
                source_venue=config.source_venue,
                source_symbol=config.symbol,
                target_venue=config.target_venue,
                target_symbol=config.symbol,
                asset=config.asset,
                signal_type="trade_flow_impulse",
                direction=direction,
                lookback_ms=lookback_ms,
                threshold_bps=0.0,
                source_move_bps=0.0,
                source_start_price=0.0,
                source_end_price=0.0,
                strength=abs(imb),
                metadata={
                    "flow_signal_type": "signed_imbalance",
                    "lookback_ms": lookback_ms,
                    "buy_notional": round(bn, 2),
                    "sell_notional": round(sn, 2),
                    "imbalance": round(imb, 4),
                    "side_source": "exchange",
                },
            )
            events.append(evt)
            last_ts = ts

    return events


# ---------------------------------------------------------------------------
# Combined GPU signal generation
# ---------------------------------------------------------------------------


def generate_signals_gpu(
    trades: list[TradeTickLite],
    config: TradeFlowImpulseConfig,
    device: str = "cuda:0",
) -> list[TickSignalEvent]:
    """Generate all signal types via GPU acceleration.

    Only signed_imbalance is currently GPU-accelerated (fully vectorized
    prefix-sum approach). notional_burst and large_trade use per-index
    baseline median computation that is CPU-competitive — GPU kernel needs
    fully vectorized sliding-window median to beat CPU.

    CPU remains default for all signal types.
    """
    if not trades:
        return []
    events: list[TickSignalEvent] = []
    signal_types = config.validated_signal_types

    if "notional_burst" in signal_types:
        pass  # CPU path only — GPU not faster for per-index baseline median
    if "large_trade" in signal_types:
        pass  # CPU path only — GPU not faster for per-index rolling median
    if "signed_imbalance" in signal_types:
        events.extend(signed_imbalance_gpu(trades, config, device=device))

    return events
