#!/usr/bin/env python3
"""Derivatives-source -> spot-target lead-lag evaluation runner.

Reads captured trade-tick JSONL from run_derivatives_spot_capture.py, clips
to true overlap windows, generates trade-flow impulse signals on the source
(derivatives) side, evaluates forward returns on target (spot) venues,
classifies signals into OI buckets, and produces a full report.

Public-data observer only. No auth, no orders, no private keys, no execution.
"""
from __future__ import annotations

import argparse
import itertools
import math
import csv
import json
import statistics
import time
import uuid
from bisect import bisect_left
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .tick_models import TradeTickLite, TickSignalEvent, TickForwardReturn
from .tick_store import load_trades_jsonl
from .symbol_aliases import resolve_symbol, quote_mismatch as sym_quote_mismatch
from .trade_flow_impulse import TradeFlowImpulseConfig, TradeFlowImpulseSignalGenerator
from .event_study import evaluate_tick_signal, generate_random_baseline, evaluate_candidate_group
from .forward_returns_gpu import (
    check_cuda_available as _frgpu_check_cuda,
    batch_evaluate_signals_gpu as _frgpu_batch,
    batch_evaluate_signals_multi_gpu as _frgpu_multi,
)
from .artifact_metadata import build_metadata

_MS_TO_NS = 1_000_000


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _split_ints(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def _split_strings(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _parse_and_validate_devices(devices_str: str, engine: str) -> list[str]:
    """Parse comma-separated CUDA devices. Fail fast if GPU unavailable.

    Returns list of validated device strings. Empty list for CPU engine.
    Duplicate detection, availability check, fast exit on failure.
    """
    import sys as _sys
    if engine != "gpu":
        return []
    parts = [d.strip() for d in devices_str.split(",") if d.strip()]
    if not parts:
        return []
    seen: set[str] = set()
    for d in parts:
        if not d.startswith("cuda:"):
            print(f"ERROR: invalid device string: {d}", file=_sys.stderr)
            _sys.exit(1)
        if d in seen:
            print(f"ERROR: duplicate device: {d}", file=_sys.stderr)
            _sys.exit(1)
        seen.add(d)
        from .forward_returns_gpu import check_cuda_available as _check_dev
        avail, reason = _check_dev(d)
        if not avail:
            print(f"ERROR: device {d} unavailable: {reason}", file=_sys.stderr)
            _sys.exit(1)
    return parts


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Derivatives-source -> spot-target lead-lag evaluator."
    )
    p.add_argument("--capture-dir", type=str, default="data/derivatives_spot_capture_v2")
    p.add_argument("--source-venues", type=str, default="binance_perp")
    p.add_argument("--target-venues", type=str, default="kraken,coinbase")
    p.add_argument("--symbols", type=str, default="BTC/USD,ETH/USD,SOL/USD")
    p.add_argument("--signal-types", type=str, default="notional_burst,large_trade,signed_imbalance")
    p.add_argument("--lookbacks-ms", type=str, default="1000,5000,10000,30000")
    p.add_argument("--baseline-window-ms", type=int, default=60000)
    p.add_argument("--horizons-ms", type=str, default="1000,2000,5000,10000,30000,60000,300000")
    p.add_argument("--cooldown-ms", type=int, default=10000)
    p.add_argument("--fee-bps", type=float, default=40.0)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--quote-mismatch-buffer-bps", type=float, default=5.0)
    p.add_argument("--min-events", type=int, default=50)
    p.add_argument("--out", type=str, default="reports/derivatives_spot_lead_lag_v2")
    p.add_argument("--baseline-seed", type=int, default=42)
    p.add_argument("--capture-mode", type=str, default="",
                   choices=["FULL_ACTIVE", "FAST_DIAGNOSTIC", ""],
                   help="Capture mode from volatility gate. FAST_DIAGNOSTIC prevents final REJECTED verdict.")
    p.add_argument("--forward-engine", type=str, default="cpu", choices=["cpu", "gpu"],
                   help="Forward-return engine: 'cpu' (default) or 'gpu'. GPU requires PyTorch + CUDA.")
    p.add_argument("--forward-device", type=str, default="cuda:0",
                   help="CUDA device for --forward-engine gpu. Default: cuda:0.")
    p.add_argument("--forward-devices", type=str, default="",
                   help="Multi-GPU: comma-separated CUDA devices, e.g. cuda:0,cuda:1. "
                        "Takes precedence over --forward-device. Pairs sharded round-robin.")
    p.add_argument("--forward-batch-size", type=int, default=16384,
                   help="Events per GPU chunk for --forward-engine gpu. Default: 16384.")
    p.add_argument("--signal-engine", type=str, default="cpu", choices=["cpu", "gpu"],
                   help="Signal generation engine: 'cpu' (default) or 'gpu'. GPU requires PyTorch + CUDA.")
    p.add_argument("--signal-devices", type=str, default="cuda:0",
                   help="CUDA device(s) for --signal-engine gpu. Default: cuda:0. "
                        "For multi-GPU: cuda:0,cuda:1 (pairs sharded round-robin).")
    return p


# ---------------------------------------------------------------------------
# Overlap
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OverlapWindow:
    start_ns: int
    end_ns: int

    @property
    def duration_s(self) -> float:
        return (self.end_ns - self.start_ns) / 1e9

    def contains(self, ts_ns: int) -> bool:
        return self.start_ns <= ts_ns <= self.end_ns


def compute_pair_overlap(
    source_ticks: list[TradeTickLite],
    target_ticks: list[TradeTickLite],
) -> OverlapWindow | None:
    if not source_ticks or not target_ticks:
        return None
    o_start = max(source_ticks[0].ts_event, target_ticks[0].ts_event)
    o_end = min(source_ticks[-1].ts_event, target_ticks[-1].ts_event)
    if o_end <= o_start:
        return None
    return OverlapWindow(o_start, o_end)


def clip_ticks(ticks: list[TradeTickLite], overlap: OverlapWindow) -> list[TradeTickLite]:
    lo = bisect_left([t.ts_event for t in ticks], overlap.start_ns)
    result: list[TradeTickLite] = []
    for t in ticks[lo:]:
        if t.ts_event <= overlap.end_ns:
            result.append(t)
        else:
            break
    return result


def price_range_bps(ticks: list[TradeTickLite]) -> float:
    if len(ticks) < 2:
        return 0.0
    prices = [t.price for t in ticks]
    lo_p, hi_p = min(prices), max(prices)
    return (hi_p - lo_p) / lo_p * 10000.0 if lo_p > 0 else 0.0


# ---------------------------------------------------------------------------
# OI
# ---------------------------------------------------------------------------

def _load_oi_snapshots(capture_dir: Path, venue: str, asset: str, quote: str) -> list[dict]:
    snapshots: list[dict] = []
    sym_file = f"{asset}-{quote}"
    for fpath in capture_dir.iterdir():
        if fpath.name.startswith("open_interest") and venue in fpath.name and sym_file in fpath.name:
            try:
                with open(fpath) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            snapshots.append(json.loads(line))
            except Exception:
                continue
    snapshots.sort(key=lambda s: s.get("receive_timestamp_ns", 0))
    return snapshots


def _nearest_oi_at_or_before(snapshots: list[dict], ts_ns: int) -> float | None:
    best_val: float | None = None
    best_ts = -1
    for s in snapshots:
        snap_ts = s.get("receive_timestamp_ns", 0)
        if snap_ts <= ts_ns and snap_ts > best_ts:
            best_val = float(s.get("open_interest", 0))
            best_ts = snap_ts
    return best_val


def classify_oi_bucket(
    source_ticks: list[TradeTickLite],
    lookback_ns: int,
    signal_ts_ns: int,
    oi_snapshots: list[dict],
) -> str:
    if not oi_snapshots:
        return "flat_or_unknown"

    lb_start = signal_ts_ns - lookback_ns

    # Price at lookback start vs price at signal
    ts_list = [t.ts_event for t in source_ticks]
    idx_lb = bisect_left(ts_list, lb_start)
    idx_sig = bisect_left(ts_list, signal_ts_ns)

    if idx_lb >= len(source_ticks) or idx_sig >= len(source_ticks):
        return "flat_or_unknown"

    ref_price = source_ticks[idx_lb].price
    sig_price = source_ticks[min(idx_sig, len(source_ticks) - 1)].price

    if ref_price <= 0:
        return "flat_or_unknown"

    price_up = sig_price >= ref_price

    pre_oi = _nearest_oi_at_or_before(oi_snapshots, lb_start)
    post_oi = _nearest_oi_at_or_before(oi_snapshots, signal_ts_ns)

    if pre_oi is None or post_oi is None or pre_oi <= 0:
        return "flat_or_unknown"

    oi_up = post_oi >= pre_oi

    if price_up and oi_up:
        return "price_up_oi_up"
    if price_up and not oi_up:
        return "price_up_oi_down"
    if not price_up and oi_up:
        return "price_down_oi_up"
    return "price_down_oi_down"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_capture_data(
    capture_dir: Path,
    source_venues: list[str],
    target_venues: list[str],
    symbols: list[str],
) -> dict[tuple[str, str], list[TradeTickLite]]:
    grouped: dict[tuple[str, str], list[TradeTickLite]] = {}
    all_venues = list(dict.fromkeys(source_venues + target_venues))

    for venue in all_venues:
        for symbol in symbols:
            try:
                canon = resolve_symbol(symbol)
            except ValueError:
                continue

            # Build search patterns per venue/quote
            target_quote = canon.quote  # USD from CLI target symbols
            if venue == "binance_perp":
                # Source is always USDT for binance_perp -- search with USDT patterns
                parts = [
                    f"{canon.asset}USDT",
                    f"{canon.asset}-USDT",
                ]
                store_sym = f"{canon.asset}/USDT"
            else:
                parts = [
                    f"{canon.asset}-{target_quote}",
                    f"{canon.asset}/{target_quote}",
                ]
                store_sym = f"{canon.asset}/{target_quote}"

            files: list[Path] = []
            for fpath in sorted(capture_dir.iterdir()):
                if not fpath.name.startswith("trades_") or not fpath.name.endswith(".jsonl"):
                    continue
                if f"_{venue}_" not in fpath.name:
                    continue
                for p in parts:
                    if p in fpath.name:
                        files.append(fpath)
                        break

            files = sorted(set(files))
            if not files:
                print(f"  [INFO] No ticks: venue={venue} symbol={canon.asset}/{canon.quote}")
                continue

            combined: list[TradeTickLite] = []
            for fp in files:
                try:
                    combined.extend(load_trades_jsonl(str(fp)))
                except Exception as e:
                    print(f"  [WARN] {fp}: {e}")

            # Deduplicate, sort, normalize
            seen_set: set[tuple] = set()
            unique: list[TradeTickLite] = []
            for t in combined:
                try:
                    c = resolve_symbol(t.symbol)
                    norm = TradeTickLite(
                        ts_event=t.ts_event, venue=t.venue,
                        symbol=f"{c.asset}/{c.quote}", price=t.price,
                        size=t.size, side=t.side, trade_id=t.trade_id,
                    )
                except ValueError:
                    continue
                key = (norm.ts_event, norm.venue, norm.symbol, norm.price, norm.size)
                if key not in seen_set:
                    seen_set.add(key)
                    unique.append(norm)
            unique.sort(key=lambda t: t.ts_event)

            grouped[(venue, store_sym)] = unique
            print(f"  [LOADED] venue={venue} symbol={store_sym} ticks={len(unique)} files={len(files)}")

    return grouped


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

@dataclass
class EvalSummary:
    capture_dir: str = ""
    capture_mode: str = ""  # "FULL_ACTIVE", "FAST_DIAGNOSTIC", or ""
    total_signals: int = 0
    valid_evaluations: int = 0
    rejected_evaluations: int = 0
    fee_bps: float = 40.0
    slippage_bps: float = 5.0
    quote_mismatch_buffer_bps: float = 5.0
    all_in_cost_bps: float = 50.0
    results_by_group: list[dict] = field(default_factory=list)
    overlap_info: dict = field(default_factory=dict)
    capture_context: dict = field(default_factory=dict)
    oi_bucket_summary: dict = field(default_factory=dict)
    run_start: float = 0.0
    run_end: float = 0.0
    rejections: list[dict] = field(default_factory=list)
    verdict: str = "NEEDS_MORE_DATA"
    best_group: dict | None = None
    best_bucket: str | None = None


# ---------------------------------------------------------------------------
# Helper: nanosecond timestamp formatting
# ---------------------------------------------------------------------------

def _ts_ns(ts_ns: int) -> str:
    dt = datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------------
# Main evaluation
# ---------------------------------------------------------------------------

def run_evaluation(args: argparse.Namespace) -> tuple[EvalSummary, list[TickSignalEvent], list[TickForwardReturn]]:
    from dataclasses import asdict  # noqa

    summary = EvalSummary(
        capture_dir=args.capture_dir,
        capture_mode=getattr(args, "capture_mode", ""),
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps,
        all_in_cost_bps=args.fee_bps + args.slippage_bps + args.quote_mismatch_buffer_bps,
        run_start=time.time(),
    )

    # GPU availability check — fail fast, no silent fallback
    forward_engine = getattr(args, "forward_engine", "cpu")
    forward_device = getattr(args, "forward_device", "cuda:0")
    forward_batch_size = getattr(args, "forward_batch_size", 16384)

    # Multi-GPU device parsing
    forward_devices_arg = getattr(args, "forward_devices", "")
    devices: list[str] = _parse_and_validate_devices(forward_devices_arg, forward_engine)
    if not devices:
        # Single device (or CPU)
        if forward_engine == "gpu":
            import sys as _sys
            cuda_ok, cuda_reason = _frgpu_check_cuda(forward_device)
            if not cuda_ok:
                print(
                    f"ERROR: --forward-engine gpu requested but CUDA unavailable: {cuda_reason}",
                    file=_sys.stderr,
                )
                print(
                    '{"verdict": "GPU_UNAVAILABLE_DIAGNOSTIC", "reason": "'
                    + cuda_reason + '"}',
                    file=_sys.stderr,
                )
                _sys.exit(1)
            devices = [forward_device]
    else:
        # Multi-GPU: override single device info
        forward_device = devices[0]
        print(f"  [GPU] Multi-GPU enabled: {devices}")

    # Device round-robin iterator for pair-level sharding
    if devices:
        _device_cycle = itertools.cycle(devices) if len(devices) > 1 else itertools.repeat(devices[0])
    else:
        _device_cycle = itertools.repeat("cpu")

    # Signal engine setup
    signal_engine = getattr(args, "signal_engine", "cpu")
    signal_device = getattr(args, "signal_devices", "cuda:0")
    if signal_engine == "gpu":
        from .trade_flow_impulse_gpu import generate_signals_gpu as _gpu_signal_check
        if callable(_gpu_signal_check):
            import sys as _sys
            from .trade_flow_impulse_gpu import check_cuda_available as _sig_check
            for _d in _split_strings(signal_device):
                _sig_ok, _sig_reason = _sig_check(_d)
                if not _sig_ok:
                    print(
                        f"ERROR: --signal-engine gpu requested but device {_d} unavailable: {_sig_reason}",
                        file=_sys.stderr,
                    )
                    _sys.exit(1)
        print(f"  [SIGNAL] GPU signal engine enabled, devices={_split_strings(signal_device)}")

    capture_dir = Path(args.capture_dir)
    if not capture_dir.exists():
        summary.verdict = "NEEDS_MORE_DATA"
        summary.run_end = time.time()
        return summary, [], []

    source_venues = _split_strings(args.source_venues)
    target_venues = _split_strings(args.target_venues)
    symbols = _split_strings(args.symbols)
    signal_types = _split_strings(args.signal_types)
    lookbacks_ms = _split_ints(args.lookbacks_ms)
    horizons_ms = _split_ints(args.horizons_ms)

    print("=" * 70)
    print("DERIVATIVES-SOURCE -> SPOT-TARGET LEAD-LAG EVALUATION")
    print("=" * 70)
    print(f"\n[1] Loading from: {capture_dir}")
    grouped = load_capture_data(capture_dir, source_venues, target_venues, symbols)

    if not grouped:
        summary.verdict = "NEEDS_MORE_DATA"
        summary.run_end = time.time()
        return summary, [], []

    # Load OI
    oi_by_asset: dict[str, list[dict]] = {}
    for symbol in symbols:
        try:
            canon = resolve_symbol(symbol)
            snaps = _load_oi_snapshots(capture_dir, "binance_perp", canon.asset, canon.quote)
            if snaps:
                oi_by_asset[canon.asset] = snaps
                print(f"  [OI] {canon.asset}: {len(snaps)} snapshots")
        except ValueError:
            pass

    ALL_SIGNALS: list[TickSignalEvent] = []
    ALL_FWD: list[TickForwardReturn] = []
    ALL_REJECTIONS: list[dict] = []

    # Store target ticks per symbol for baseline evaluation
    target_ticks_by_symbol: dict[str, list] = {}

    max_overlap_price_range = 0.0

    for source_venue in source_venues:
        for target_venue in target_venues:
            if source_venue == target_venue:
                continue

            for symbol in symbols:
                try:
                    canon = resolve_symbol(symbol)
                except ValueError:
                    continue

                asset = canon.asset
                source_key = (source_venue, f"{asset}/USDT")
                target_key = (target_venue, f"{asset}/USD")

                src_all = grouped.get(source_key, [])
                tgt_all = grouped.get(target_key, [])

                pair_label = f"{source_venue}->{target_venue}@{asset}"
                print(f"\n  [PAIR] {pair_label}")
                if forward_engine == "gpu" and len(devices) > 1:
                    _pair_device = next(_device_cycle)
                    print(f"    device={_pair_device}")
                else:
                    _pair_device = forward_device
                print(f"    source_all={len(src_all)} target_all={len(tgt_all)}")

                src_range = price_range_bps(src_all) if src_all else 0.0
                tgt_range = price_range_bps(tgt_all) if tgt_all else 0.0

                # Store target ticks for baseline evaluation
                if tgt_all and asset not in target_ticks_by_symbol:
                    target_ticks_by_symbol[asset] = tgt_all

                # Overlap
                overlap = compute_pair_overlap(src_all, tgt_all)

                overlap_dict = {
                    "source_range": f"{_ts_ns(src_all[0].ts_event)} - {_ts_ns(src_all[-1].ts_event)}" if src_all else "N/A",
                    "target_range": f"{_ts_ns(tgt_all[0].ts_event)} - {_ts_ns(tgt_all[-1].ts_event)}" if tgt_all else "N/A",
                    "source_ticks": len(src_all),
                    "target_ticks": len(tgt_all),
                    "source_price_range_bps": round(src_range, 2),
                    "target_price_range_bps": round(tgt_range, 2),
                }

                if overlap is None:
                    overlap_dict["status"] = "no_overlap"
                    summary.overlap_info[pair_label] = overlap_dict
                    ALL_REJECTIONS.append({"pair": pair_label, "reason": "no_overlap", "verdict": "NEEDS_MORE_DATA"})
                    continue

                overlap_dict["overlap_start"] = _ts_ns(overlap.start_ns)
                overlap_dict["overlap_end"] = _ts_ns(overlap.end_ns)
                overlap_dict["overlap_duration_s"] = round(overlap.duration_s, 2)

                src_clipped = clip_ticks(src_all, overlap)
                tgt_clipped = clip_ticks(tgt_all, overlap)
                overlap_dict["source_in_overlap"] = len(src_clipped)
                overlap_dict["target_in_overlap"] = len(tgt_clipped)

                if src_clipped and tgt_clipped:
                    ol_src_range = price_range_bps(src_clipped)
                    ol_tgt_range = price_range_bps(tgt_clipped)
                    max_overlap_price_range = max(max_overlap_price_range, ol_src_range, ol_tgt_range)
                    overlap_dict["overlap_source_price_range_bps"] = round(ol_src_range, 2)
                    overlap_dict["overlap_target_price_range_bps"] = round(ol_tgt_range, 2)
                else:
                    ol_src_range = 0.0
                    ol_tgt_range = 0.0

                summary.overlap_info[pair_label] = overlap_dict

                print(f"    overlap={overlap.duration_s:.1f}s src_clip={len(src_clipped)} tgt_clip={len(tgt_clipped)}")

                # Volatility sanity: skip evaluation if movement < 20% of cost wall
                if src_clipped and tgt_clipped:
                    max_move = max(ol_src_range, ol_tgt_range)
                    if max_move < summary.all_in_cost_bps * 0.2:
                        print(f"    [VOLATILITY] movement {max_move:.1f} bps < cost wall. NEEDS_MORE_DATA")
                        ALL_REJECTIONS.append({
                            "pair": pair_label, "reason": "quiet_capture",
                            "movement_bps": round(max_move, 2), "cost_bps": summary.all_in_cost_bps,
                            "verdict": "NEEDS_MORE_DATA",
                        })
                        continue

                max_horizon_ns = max(horizons_ms) * _MS_TO_NS
                if overlap.duration_s < max_horizon_ns / 1e9:
                    print(f"    [OVERLAP] too short for horizons. NEEDS_MORE_DATA")
                    ALL_REJECTIONS.append({
                        "pair": pair_label, "reason": "overlap_too_short",
                        "overlap_s": overlap.duration_s,
                        "verdict": "NEEDS_MORE_DATA",
                    })
                    continue

                if len(src_clipped) < 200 or len(tgt_clipped) < 200:
                    print(f"    [OVERLAP] too few ticks. NEEDS_MORE_DATA")
                    ALL_REJECTIONS.append({
                        "pair": pair_label, "reason": "insufficient_ticks_in_overlap",
                        "verdict": "NEEDS_MORE_DATA",
                    })
                    continue

                # Quote mismatch check
                has_qm = False
                adj_slippage = args.slippage_bps
                if src_clipped and tgt_clipped:
                    try:
                        has_qm = sym_quote_mismatch(src_clipped[0].symbol, tgt_clipped[0].symbol)
                    except ValueError:
                        has_qm = False
                    if has_qm:
                        adj_slippage += args.quote_mismatch_buffer_bps

                oi_snaps = oi_by_asset.get(asset, [])

                for sig_type in signal_types:
                    for lb_ms in lookbacks_ms:
                        cfg = TradeFlowImpulseConfig(
                            source_venue=source_venue,
                            target_venue=target_venue,
                            symbol=symbol,
                            asset=asset,
                            flow_lookbacks_ms=[lb_ms],
                            baseline_window_ms=args.baseline_window_ms,
                            signal_types=[sig_type],
                            cooldown_ms=args.cooldown_ms,
                        )

                        if signal_engine == "gpu" and sig_type == "signed_imbalance":
                            # GPU signal generation (signed_imbalance only — confirmed vectorized)
                            from .trade_flow_impulse_gpu import signed_imbalance_gpu as _gpu_signal_fn
                            events = _gpu_signal_fn(
                                src_clipped, cfg,
                                device=_pair_device if len(devices) > 1 else signal_device,
                            )
                        else:
                            gen = TradeFlowImpulseSignalGenerator(cfg)
                            events = gen.generate(src_clipped)

                        # Label OI bucket
                        lb_ns = lb_ms * _MS_TO_NS
                        for evt in events:
                            bucket = classify_oi_bucket(src_clipped, lb_ns, evt.ts_event, oi_snaps)
                            if evt.metadata is None:
                                evt.metadata = {}
                            evt.metadata["oi_bucket"] = bucket
                            # Also store flow_signal_type for grouping
                            evt.metadata["flow_signal_type"] = sig_type

                        ALL_SIGNALS.extend(events)

                        if forward_engine == "gpu" and events:
                            if len(devices) > 1:
                                gpu_frs = _frgpu_multi(
                                    signals=events,
                                    target_ticks=tgt_clipped,
                                    horizons_ms=horizons_ms,
                                    fee_bps=args.fee_bps,
                                    slippage_bps=adj_slippage,
                                    quote_mismatch=has_qm,
                                    quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps if has_qm else 0.0,
                                    chunk_size=forward_batch_size,
                                    devices=devices,
                                )
                            else:
                                gpu_frs = _frgpu_batch(
                                    signals=events,
                                    target_ticks=tgt_clipped,
                                    horizons_ms=horizons_ms,
                                    fee_bps=args.fee_bps,
                                    slippage_bps=adj_slippage,
                                    quote_mismatch=has_qm,
                                    quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps if has_qm else 0.0,
                                    chunk_size=forward_batch_size,
                                    device=_pair_device,
                                )
                            ALL_FWD.extend(gpu_frs)
                        else:
                            for evt in events:
                                frs = evaluate_tick_signal(
                                    signal=evt,
                                    target_ticks=tgt_clipped,
                                    horizons_ms=horizons_ms,
                                    fee_bps=args.fee_bps,
                                    slippage_bps=adj_slippage,
                                    quote_mismatch=has_qm,
                                    quote_mismatch_buffer_bps=args.quote_mismatch_buffer_bps if has_qm else 0.0,
                                )
                                ALL_FWD.extend(frs)

                        print(f"    {sig_type}/{lb_ms}ms: {len(events)} signals, {sum(len(frs) for frs in [[]])} fwd")

    summary.total_signals = len(ALL_SIGNALS)
    valid_fwd = [r for r in ALL_FWD if r.valid]
    summary.valid_evaluations = len(valid_fwd)

    summary.capture_context = {
        "all_in_cost_bps": summary.all_in_cost_bps,
        "forward_engine": forward_engine,
        "forward_devices": devices,
        "signal_engine": signal_engine,
        "signal_devices": _split_strings(signal_device) if signal_engine == "gpu" else [],
        "oi_snapshot_counts": {k: len(v) for k, v in oi_by_asset.items()},
        "max_overlap_price_range_bps": round(max_overlap_price_range, 2),
    }

    # --- OI bucket summary ---
    if oi_by_asset:
        bucket_map: dict[str, list[TickForwardReturn]] = {}
        for fr in ALL_FWD:
            for sig in ALL_SIGNALS:
                if sig.signal_id == fr.signal_id and sig.metadata and "oi_bucket" in sig.metadata:
                    bucket_map.setdefault(sig.metadata["oi_bucket"], []).append(fr)
                    break

        for bucket, frs in bucket_map.items():
            valid = [r for r in frs if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
            if not valid:
                continue
            nets = [r.net_return_bps for r in valid if r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
            if not nets:
                continue
            summary.oi_bucket_summary[bucket] = {
                "event_count": len(frs),
                "valid_count": len(valid),
                "mean_net_bps": round(statistics.mean(nets), 4),
                "median_net_bps": round(statistics.median(nets), 4),
                "win_rate": round(sum(1 for x in nets if x > 0) / len(nets), 4),
            }

    # --- Group results ---
    group_map: dict[tuple, dict] = {}

    for fr in ALL_FWD:
        sig_meta = {}
        for sig in ALL_SIGNALS:
            if sig.signal_id == fr.signal_id:
                sig_meta = sig.metadata or {}
                break

        sig_type = sig_meta.get("flow_signal_type", "unknown")
        lb_ms_val = _infer_lookback(sig_meta, lookbacks_ms)
        key = (sig_type, lb_ms_val, fr.horizon_ms)

        if key not in group_map:
            group_map[key] = {"forward_returns": [], "baseline_events": []}
        group_map[key]["forward_returns"].append(fr)

    best_group = None
    best_mean_net = -1e9

    for gkey, gdata in group_map.items():
        frs = gdata["forward_returns"]
        valid = [r for r in frs if r.valid and r.net_return_bps is not None and math.isfinite(r.net_return_bps)]
        if not valid:
            continue

        nets = [r.net_return_bps for r in valid]
        raws = [r.raw_return_bps for r in valid if r.raw_return_bps is not None and math.isfinite(r.raw_return_bps)]

        mean_raw = statistics.mean(raws) if raws else 0.0
        mean_net = statistics.mean(nets)
        median_net = statistics.median(nets)
        wr = sum(1 for x in nets if x > 0) / len(nets)

        # Baseline
        # Need source ticks for baseline timestamps -- use first available
        any_src = None
        for (sv, _sk), v in grouped.items():
            if v and sv in source_venues:
                any_src = v
                break

        baseline_mean_net = None
        baseline_wr = None

        if any_src and len(any_src) > 1:
            try:
                baseline_events = generate_random_baseline(
                    source_ticks=any_src,
                    signal_count=len(frs) // len(horizons_ms) if len(horizons_ms) > 0 else len(frs),
                    source_venue=source_venues[0] if source_venues else "unknown",
                    target_venue=target_venues[0] if target_venues else "unknown",
                    symbol=symbols[0] if symbols else "BTC/USD",
                    asset="BTC",
                    seed=args.baseline_seed,
                )
                bl_nets_all: list[float] = []
                base_ticks = target_ticks_by_symbol.get(symbols[0] if symbols else "BTC/USD", [])
                if base_ticks:
                    for bev in baseline_events:
                        bl_frs = evaluate_tick_signal(
                            signal=bev,
                            target_ticks=base_ticks,
                            horizons_ms=[gkey[2]],
                            fee_bps=args.fee_bps,
                            slippage_bps=args.slippage_bps,
                        )
                        for bfr in bl_frs:
                            if bfr.valid and bfr.net_return_bps is not None and math.isfinite(bfr.net_return_bps):
                                bl_nets_all.append(bfr.net_return_bps)
                if bl_nets_all:
                    baseline_mean_net = round(statistics.mean(bl_nets_all), 4)
                    baseline_wr = round(sum(1 for x in bl_nets_all if x > 0) / len(bl_nets_all), 4)
            except ValueError:
                pass

        gates = evaluate_candidate_group(
            forward_returns=frs,
            baseline_forward_returns=[],
            min_events=args.min_events,
        )

        grp = {
            "source_venue": source_venues[0] if source_venues else "",
            "target_venue": target_venues[0] if target_venues else "",
            "signal_type": gkey[0],
            "lookback_ms": gkey[1],
            "horizon_ms": gkey[2],
            "valid_count": len(valid),
            "mean_raw_bps": round(mean_raw, 4),
            "mean_net_bps": round(mean_net, 4),
            "median_net_bps": round(median_net, 4),
            "win_rate": round(wr, 4),
            "baseline_mean_net_bps": baseline_mean_net,
            "baseline_win_rate": baseline_wr,
            "candidate": gates.get("candidate", False),
            "rejection_reasons": gates.get("rejection_reasons", []),
        }
        summary.results_by_group.append(grp)

        if mean_net > best_mean_net:
            best_mean_net = mean_net
            best_group = grp

        if gates.get("candidate"):
            pass  # candidate
        else:
            summary.rejected_evaluations += 1

    summary.rejections = ALL_REJECTIONS
    summary.best_group = best_group

    # Best OI bucket
    best_bucket = None
    best_bkt_mean = -1e9
    for bkt, st in summary.oi_bucket_summary.items():
        if st["mean_net_bps"] > best_bkt_mean:
            best_bkt_mean = st["mean_net_bps"]
            best_bucket = bkt
    summary.best_bucket = best_bucket

    # --- Final verdict ---
    if summary.total_signals > 0 and summary.valid_evaluations > 0:
        # Check candidate gate
        any_candidate = any(g.get("candidate") for g in summary.results_by_group)
        if any_candidate:
            summary.verdict = "CANDIDATE_FOR_LONGER_OBSERVATION"
        elif max_overlap_price_range > summary.all_in_cost_bps * 0.5:
            summary.verdict = "REJECTED"
        else:
            summary.verdict = "NEEDS_MORE_DATA"
    else:
        if ALL_REJECTIONS:
            # All pairs had overlap/volatility issues
            if any(r.get("reason") == "quiet_capture" for r in ALL_REJECTIONS):
                summary.verdict = "NEEDS_MORE_DATA"
            elif any(r.get("reason") in ("no_overlap", "overlap_too_short") for r in ALL_REJECTIONS):
                summary.verdict = "NEEDS_MORE_DATA"
            else:
                summary.verdict = "NEEDS_MORE_DATA"
        else:
            summary.verdict = "NEEDS_MORE_DATA"

    # FAST_DIAGNOSTIC capture mode must never produce REJECTED.
    # Remap to MARKET_MODERATE_DIAGNOSTIC (sufficient overlap, no signal
    # passed gates, but single short window is not structurally conclusive).
    if summary.capture_mode == "FAST_DIAGNOSTIC" and summary.verdict == "REJECTED":
        summary.verdict = "MARKET_MODERATE_DIAGNOSTIC"

    summary.run_end = time.time()
    return summary, ALL_SIGNALS, ALL_FWD


def _infer_lookback(sig_meta: dict, lookbacks: list[int]) -> int:
    """Infer lookback from signal metadata or return 0."""
    meta_lb = sig_meta.get("lookback_ms")
    if meta_lb is not None:
        return int(meta_lb)
    return lookbacks[0] if lookbacks else 0


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def write_reports(
    summary: EvalSummary,
    out: Path,
    signals: list[TickSignalEvent],
    fwd: list[TickForwardReturn],
) -> None:
    out.mkdir(parents=True, exist_ok=True)

    # Build provenance metadata
    meta = build_metadata(
        capture_mode=summary.capture_mode,
        run_args=None,  # args not available here; capture_mode is sufficient
    )

    # summary.json
    sd = {
        "_metadata": meta,
        "capture_dir": summary.capture_dir,
        "total_signals": summary.total_signals,
        "valid_evaluations": summary.valid_evaluations,
        "rejected_evaluations": summary.rejected_evaluations,
        "fee_bps": summary.fee_bps,
        "slippage_bps": summary.slippage_bps,
        "quote_mismatch_buffer_bps": summary.quote_mismatch_buffer_bps,
        "all_in_cost_bps": summary.all_in_cost_bps,
        "verdict": summary.verdict,
        "capture_mode": summary.capture_mode,
        "run_duration_s": round(summary.run_end - summary.run_start, 2),
        "capture_context": summary.capture_context,
        "overlap_info": summary.overlap_info,
        "oi_bucket_summary": summary.oi_bucket_summary,
        "results_by_group": summary.results_by_group,
        "best_group": summary.best_group,
        "best_bucket": summary.best_bucket,
        "rejections": summary.rejections,
    }
    with open(out / "summary.json", "w") as f:
        json.dump(sd, f, indent=2, default=str)

    # summary.csv
    with open(out / "summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source_venue", "target_venue", "signal_type", "lookback_ms",
                     "horizon_ms", "valid_count", "mean_raw_bps", "mean_net_bps",
                     "median_net_bps", "win_rate", "baseline_mean_net_bps",
                     "baseline_win_rate", "candidate", "rejection_reasons"])
        for r in summary.results_by_group:
            w.writerow([
                r.get("source_venue", ""), r.get("target_venue", ""),
                r.get("signal_type", ""), r.get("lookback_ms", ""),
                r.get("horizon_ms", ""), r.get("valid_count", 0),
                r.get("mean_raw_bps", ""), r.get("mean_net_bps", ""),
                r.get("median_net_bps", ""), r.get("win_rate", ""),
                r.get("baseline_mean_net_bps", ""), r.get("baseline_win_rate", ""),
                r.get("candidate", False), "; ".join(r.get("rejection_reasons", [])),
            ])

    # signals.jsonl
    with open(out / "signals.jsonl", "w") as f:
        for sig in signals:
            f.write(json.dumps(asdict(sig), default=str) + "\n")

    # forward_returns.jsonl
    with open(out / "forward_returns.jsonl", "w") as f:
        for fr in fwd:
            f.write(json.dumps(asdict(fr), default=str) + "\n")

    # rejections.json
    with open(out / "rejections.json", "w") as f:
        json.dump(summary.rejections, f, indent=2, default=str)

    # oi_bucket_summary
    if summary.oi_bucket_summary:
        with open(out / "oi_bucket_summary.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["bucket", "event_count", "valid_count", "mean_net_bps", "median_net_bps", "win_rate"])
            for bkt, st in summary.oi_bucket_summary.items():
                w.writerow([bkt, st["event_count"], st["valid_count"],
                            st["mean_net_bps"], st["median_net_bps"], st["win_rate"]])
        with open(out / "oi_bucket_summary.json", "w") as f:
            json.dump(summary.oi_bucket_summary, f, indent=2)

    # report.md
    _write_md(summary, out)


def _write_md(summary: EvalSummary, out: Path) -> None:
    cc = summary.capture_context
    lines: list[str] = [
        "# Derivatives-Source Spot Lead-Lag v2 Report",
        "",
        "## Hypothesis",
        "",
        "Derivatives/perp flow as SOURCE predicts spot forward returns as TARGET.",
        "Source: Binance USD-M perpetuals. Target: Kraken/Coinbase spot.",
        "",
        "## Safety",
        "",
        "**Public data only. No auth. No API keys. No orders. No derivatives execution.**",
        "",
        "## Capture Context",
        "",
        f"- Capture method: combined async single-event-loop runner",
        f"- Capture mode: {summary.capture_mode or 'unspecified'}",
        f"- All-in cost: {summary.all_in_cost_bps} bps ({summary.fee_bps} fee + {summary.slippage_bps} slippage + {summary.quote_mismatch_buffer_bps} quote mismatch)",
        "",
    ]

    lines.append("## Overlap Windows\n")
    for pair, info in summary.overlap_info.items():
        lines.append(f"### {pair}")
        for k, v in info.items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    lines.append("## Price Ranges\n")
    lines.append(f"- Max overlap price range: {cc.get('max_overlap_price_range_bps', 0)} bps")
    lines.append(f"- All-in cost: {summary.all_in_cost_bps} bps\n")

    lines.append("## Signal Summary\n")
    lines.append(f"- Total signals: {summary.total_signals}")
    lines.append(f"- Valid forward returns: {summary.valid_evaluations}\n")

    if summary.best_group:
        lines.append("## Best Group by Mean Net Bps\n")
        for k, v in summary.best_group.items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    if summary.oi_bucket_summary:
        lines.append("## OI Bucket Summary\n")
        for bkt, st in summary.oi_bucket_summary.items():
            lines.append(f"### {bkt}")
            for k, v in st.items():
                lines.append(f"- {k}: {v}")
            lines.append("")
        if summary.best_bucket:
            lines.append(f"**Best bucket**: {summary.best_bucket} "
                         f"(mean net = {summary.oi_bucket_summary[summary.best_bucket]['mean_net_bps']} bps)\n")
    else:
        lines.append("## OI Bucket Summary\n")
        lines.append("No OI data available for bucket classification.\n")

    lines.append("## Verdict\n")
    lines.append(f"**{summary.verdict}**\n")

    if summary.capture_mode:
        lines.append(f"Capture mode: {summary.capture_mode}\n")

    if summary.verdict == "NEEDS_MORE_DATA":
        lines.append("Insufficient data, overlap, or price movement for a fair assessment. Not a rejection.\n")
    elif summary.verdict == "REJECTED":
        lines.append("Sufficient overlap and movement observed; no signal group passed candidate gates.\n")
    elif summary.verdict == "CANDIDATE_FOR_LONGER_OBSERVATION":
        lines.append("One or more groups passed all candidate gates. Further observation needed.\n")
    elif summary.verdict == "MARKET_MODERATE_DIAGNOSTIC":
        lines.append("FAST_DIAGNOSTIC capture: sufficient overlap and movement, no signal passed gates, "
                      "but a single short window is not structurally conclusive for REJECTED.\n"
                      "This verdict maps to NEEDS_MORE_DATA for any downstream decision-making.\n")

    with open(out / "report.md", "w") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    summary, signals, fwd = run_evaluation(args)

    write_reports(summary, Path(args.out), signals, fwd)

    print()
    print("=" * 70)
    print(f"VERDICT: {summary.verdict}")
    print("=" * 70)
    print(f"  Signals: {summary.total_signals}")
    print(f"  Valid forward returns: {summary.valid_evaluations}")
    print(f"  Reports: {args.out}/")


if __name__ == "__main__":
    main()
