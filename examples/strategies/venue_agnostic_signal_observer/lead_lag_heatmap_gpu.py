"""GPU-ready lead/lag heatmap diagnostics for venue_agnostic_signal_observer.

Diagnostic only. No candidate generation. No verdict promotion/rejection.
No network, no orders, no registry updates, no live trading.

Public API
----------
compute_lead_lag_heatmap(...)   -- Compute lead/lag heatmap from raw series
write_heatmap_reports(...)      -- Write JSON, CSV, and Markdown reports
check_cuda_available(...)       -- (available: bool, reason: str)
"""
from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # torch imported lazily at call time

SAFETY_MODE = "public_data_observer_only"
DEFAULT_LAGS_MS = [100, 250, 500, 1000, 2000, 5000, 10000, 30000]

# Allowed diagnostic verdicts — never REJECTED or CANDIDATE
_DIAGNOSTIC_READY = "LEAD_LAG_DIAGNOSTIC_READY"
_INSUFFICIENT_OVERLAP = "INSUFFICIENT_OVERLAP"
_INSUFFICIENT_SAMPLES = "INSUFFICIENT_SAMPLES"
_GPU_UNAVAILABLE = "GPU_UNAVAILABLE_DIAGNOSTIC"
_NO_SIGNAL_SERIES = "NO_SIGNAL_SERIES"

_ALLOWED_VERDICTS = frozenset({
    _DIAGNOSTIC_READY,
    _INSUFFICIENT_OVERLAP,
    _INSUFFICIENT_SAMPLES,
    _GPU_UNAVAILABLE,
    _NO_SIGNAL_SERIES,
})


@dataclass
class LeadLagHeatmapRow:
    source_venue: str
    target_venue: str
    symbol: str
    signal_type: str
    lag_ms: int
    correlation: float | None
    directional_alignment: float | None
    sample_count: int
    overlap_seconds: float
    verdict: str
    notes: str = ""

    def __post_init__(self):
        if self.verdict not in _ALLOWED_VERDICTS:
            raise ValueError(f"Forbidden verdict '{self.verdict}'. Allowed: {sorted(_ALLOWED_VERDICTS)}")


@dataclass
class LeadLagHeatmapSummary:
    capture_dir: str
    report_dir: str = ""
    engine: str = "cpu"
    device: str = "cuda:0"
    batch_size: int = 8192
    lags_ms: list[int] = field(default_factory=lambda: list(DEFAULT_LAGS_MS))
    bucket_ms: int = 250
    min_samples: int = 50
    rows: list[dict] = field(default_factory=list)
    verdict: str = _NO_SIGNAL_SERIES
    reason_counts: dict[str, int] = field(default_factory=dict)
    safety_mode: str = SAFETY_MODE

    def __post_init__(self):
        if self.verdict not in _ALLOWED_VERDICTS:
            raise ValueError(f"Forbidden verdict '{self.verdict}'. Allowed: {sorted(_ALLOWED_VERDICTS)}")

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _split_ints(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def _finite_pair_series(x: list[float], y: list[float]) -> tuple[list[float], list[float]]:
    out_x: list[float] = []
    out_y: list[float] = []
    for a, b in zip(x, y):
        if math.isfinite(a) and math.isfinite(b):
            out_x.append(float(a))
            out_y.append(float(b))
    return out_x, out_y


def _pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) < 2 or len(y) < 2 or len(x) != len(y):
        return None
    mx = sum(x) / len(x)
    my = sum(y) / len(y)
    vx = sum((v - mx) ** 2 for v in x)
    vy = sum((v - my) ** 2 for v in y)
    if vx <= 0 or vy <= 0:
        return None
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    return cov / math.sqrt(vx * vy)


def _alignment(x: list[float], y: list[float]) -> float | None:
    if len(x) < 2 or len(y) < 2 or len(x) != len(y):
        return None
    same = sum(1 for a, b in zip(x, y) if (a >= 0 and b >= 0) or (a < 0 and b < 0))
    return same / len(x)


def _bucketize_series(values: list[float], bucket_ms: int) -> list[float]:
    if bucket_ms <= 0:
        raise ValueError("bucket_ms must be positive")
    if not values:
        return []
    buckets: list[float] = []
    for idx in range(0, len(values), bucket_ms):
        chunk = values[idx: idx + bucket_ms]
        if not chunk:
            continue
        finite = [v for v in chunk if math.isfinite(v)]
        if not finite:
            buckets.append(0.0)
        else:
            buckets.append(sum(finite) / len(finite))
    return buckets


# ---------------------------------------------------------------------------
# CUDA availability
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# GPU-accelerated correlation computation
# ---------------------------------------------------------------------------

def _gpu_correlation_batch(
    source_bucket: list[float],
    target_bucket: list[float],
    lags_ms: list[int],
    bucket_ms: int,
    device: str = "cuda:0",
    batch_size: int = 8192,
) -> list[tuple[float | None, float | None, int]]:
    """Compute correlation and alignment for all lags using GPU batching.

    Returns list of (correlation, directional_alignment, sample_count) per lag.
    """
    import torch  # noqa: PLC0415

    results: list[tuple[float | None, float | None, int]] = []
    n = len(source_bucket)
    m = len(target_bucket)

    src_tensor = torch.tensor(source_bucket, dtype=torch.float64)
    tgt_tensor = torch.tensor(target_bucket, dtype=torch.float64)

    for lag_ms in lags_ms:
        shift = max(0, int(round(lag_ms / bucket_ms))) if bucket_ms > 0 else 0
        if shift >= n or shift >= m or shift >= min(n, m):
            results.append((None, None, 0))
            continue

        x = src_tensor[:-shift] if shift else src_tensor
        y = tgt_tensor[shift:] if shift else tgt_tensor

        min_len = min(len(x), len(y))
        x = x[:min_len].to(device=device)
        y = y[:min_len].to(device=device)

        # Mask non-finite (shouldn't exist after bucketize, but defend)
        valid_mask = torch.isfinite(x) & torch.isfinite(y)
        valid_count = int(valid_mask.sum().item())

        if valid_count < 2:
            results.append((None, None, valid_count))
            del x, y, valid_mask
            continue

        vx = x[valid_mask]
        vy = y[valid_mask]

        mx = vx.mean()
        my = vy.mean()
        vx_c = vx - mx
        vy_c = vy - my
        vx_var = (vx_c ** 2).sum()
        vy_var = (vy_c ** 2).sum()

        if vx_var <= 0 or vy_var <= 0:
            corr = None
        else:
            cov = (vx_c * vy_c).sum()
            corr = float((cov / torch.sqrt(vx_var * vy_var)).item())
            if not math.isfinite(corr):
                corr = None

        # Directional alignment
        same_sign = ((vx >= 0) == (vy >= 0)) | ((vx < 0) == (vy < 0))
        align = float(same_sign.float().mean().item())
        if not math.isfinite(align):
            align = None

        results.append((corr, align, valid_count))
        del x, y, valid_mask

    del src_tensor, tgt_tensor
    return results


# ---------------------------------------------------------------------------
# Main compute function
# ---------------------------------------------------------------------------

def compute_lead_lag_heatmap(
    *,
    source_timestamps: list[int],
    source_values: list[float],
    target_timestamps: list[int],
    target_values: list[float],
    lags_ms: list[int] | None = None,
    bucket_ms: int = 250,
    engine: str = "cpu",
    device: str = "cuda:0",
    batch_size: int = 8192,
    min_samples: int = 50,
    source_venue: str = "unknown",
    target_venue: str = "unknown",
    symbol: str = "unknown",
    signal_type: str = "unknown",
    devices: list[str] | None = None,
) -> LeadLagHeatmapSummary:
    """Compute lead/lag heatmap diagnostics between source and target series.

    Diagnostic only. Does not create trade candidates, change verdicts,
    update registry, or alter evaluator behavior.
    """
    lags_ms = list(lags_ms or DEFAULT_LAGS_MS)
    summary = LeadLagHeatmapSummary(
        capture_dir="",
        report_dir="",
        engine=engine,
        device=device,
        batch_size=batch_size,
        lags_ms=lags_ms,
        bucket_ms=bucket_ms,
        min_samples=min_samples,
    )

    # --- Filter non-finite inputs ---
    src_finite = [v for v in source_values if math.isfinite(v)]
    tgt_finite = [v for v in target_values if math.isfinite(v)]

    if not src_finite or not tgt_finite:
        summary.verdict = _NO_SIGNAL_SERIES
        return summary

    # --- Compute overlap duration from timestamps ---
    if source_timestamps and target_timestamps:
        overlap_start = max(source_timestamps[0], target_timestamps[0])
        overlap_end = min(source_timestamps[-1], target_timestamps[-1])
        overlap_seconds = max(0.0, (overlap_end - overlap_start) / 1e9)
    else:
        overlap_seconds = 0.0

    if overlap_seconds <= 0:
        summary.verdict = _INSUFFICIENT_OVERLAP
        return summary

    # --- Bucketize ---
    src_bucket = _bucketize_series(src_finite, bucket_ms)
    tgt_bucket = _bucketize_series(tgt_finite, bucket_ms)

    if len(src_bucket) < min_samples or len(tgt_bucket) < min_samples:
        summary.verdict = _INSUFFICIENT_SAMPLES
        return summary

    # --- GPU availability check when engine=gpu ---
    use_gpu = engine == "gpu"
    # Resolve effective device list: multi-GPU only when ``devices`` is given
    # AND contains more than one entry. Single-element ``devices`` falls
    # through to the legacy single-device path to keep behaviour identical.
    multi_devices: list[str] = list(devices) if devices else []
    if use_gpu:
        if multi_devices:
            from .gpu_devices import validate_cuda_devices  # noqa: PLC0415
            ok, reason = validate_cuda_devices(multi_devices)
        else:
            ok, reason = check_cuda_available(device)
        if not ok:
            summary.verdict = _GPU_UNAVAILABLE
            summary.reason_counts = {"GPU_UNAVAILABLE_DIAGNOSTIC": 1, "reason": reason}
            return summary

    rows: list[dict] = []

    if use_gpu:
        # GPU path: batch all lags. Multi-GPU shards lag buckets across
        # devices and concatenates in lag order to preserve determinism.
        if len(multi_devices) > 1:
            from .gpu_devices import split_work_evenly  # noqa: PLC0415
            shards = split_work_evenly(len(lags_ms), len(multi_devices))
            gpu_results = []
            for dev_str, shard in zip(multi_devices, shards):
                if len(shard) == 0:
                    continue
                shard_lags = lags_ms[shard.start:shard.stop]
                gpu_results.extend(_gpu_correlation_batch(
                    source_bucket=src_bucket,
                    target_bucket=tgt_bucket,
                    lags_ms=shard_lags,
                    bucket_ms=bucket_ms,
                    device=dev_str,
                    batch_size=batch_size,
                ))
            assert len(gpu_results) == len(lags_ms)
        else:
            effective_device = multi_devices[0] if multi_devices else device
            gpu_results = _gpu_correlation_batch(
                source_bucket=src_bucket,
                target_bucket=tgt_bucket,
                lags_ms=lags_ms,
                bucket_ms=bucket_ms,
                device=effective_device,
                batch_size=batch_size,
            )
        for (corr, align, sample_count), lag_ms in zip(gpu_results, lags_ms):
            verdict = _DIAGNOSTIC_READY if sample_count >= min_samples else _INSUFFICIENT_SAMPLES
            row = asdict(LeadLagHeatmapRow(
                source_venue=source_venue,
                target_venue=target_venue,
                symbol=symbol,
                signal_type=signal_type,
                lag_ms=lag_ms,
                correlation=corr,
                directional_alignment=align,
                sample_count=sample_count,
                overlap_seconds=round(overlap_seconds, 2),
                verdict=verdict,
                notes="gpu",
            ))
            rows.append(row)
    else:
        # CPU path
        for lag_ms in lags_ms:
            shift = max(0, int(round(lag_ms / bucket_ms))) if bucket_ms > 0 else 0
            n_src = len(src_bucket)
            n_tgt = len(tgt_bucket)

            if shift >= n_src or shift >= n_tgt:
                row = asdict(LeadLagHeatmapRow(
                    source_venue=source_venue,
                    target_venue=target_venue,
                    symbol=symbol,
                    signal_type=signal_type,
                    lag_ms=lag_ms,
                    correlation=None,
                    directional_alignment=None,
                    sample_count=0,
                    overlap_seconds=round(overlap_seconds, 2),
                    verdict=_INSUFFICIENT_SAMPLES,
                    notes="cpu",
                ))
                rows.append(row)
                continue

            x = src_bucket[:-shift] if shift else src_bucket
            y = tgt_bucket[shift:] if shift else tgt_bucket
            x, y = _finite_pair_series(x, y)
            sample_count = len(x)
            corr = _pearson(x, y)
            align = _alignment(x, y)

            verdict = _DIAGNOSTIC_READY if sample_count >= min_samples else _INSUFFICIENT_SAMPLES
            row = asdict(LeadLagHeatmapRow(
                source_venue=source_venue,
                target_venue=target_venue,
                symbol=symbol,
                signal_type=signal_type,
                lag_ms=lag_ms,
                correlation=corr,
                directional_alignment=align,
                sample_count=sample_count,
                overlap_seconds=round(overlap_seconds, 2),
                verdict=verdict,
                notes="cpu",
            ))
            rows.append(row)

    summary.rows = rows
    summary.verdict = _DIAGNOSTIC_READY if any(r["verdict"] == _DIAGNOSTIC_READY for r in rows) else _INSUFFICIENT_SAMPLES
    summary.reason_counts = {k: sum(1 for r in rows if r["verdict"] == k) for k in {r["verdict"] for r in rows}}
    return summary


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------

def write_heatmap_reports(summary: LeadLagHeatmapSummary, out_dir: str | Path) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    d = summary.to_dict()

    # NaN/inf sanitization for JSON
    import json

    def _sanitize(obj):
        if isinstance(obj, float):
            if not math.isfinite(obj):
                return None
            return obj
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(v) for v in obj]
        return obj

    with open(out / "lead_lag_heatmap_summary.json", "w") as f:
        json.dump(_sanitize(d), f, indent=2, default=str)

    if summary.rows:
        with open(out / "lead_lag_heatmap.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(summary.rows[0].keys()))
            writer.writeheader()
            for row in summary.rows:
                sanitized = {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in row.items()}
                writer.writerow(sanitized)

    with open(out / "lead_lag_heatmap.md", "w") as f:
        f.write("# Lead/Lag Heatmap Diagnostics\n\n")
        f.write(f"Verdict: {summary.verdict}\n\n")
        f.write(f"Engine: {summary.engine}\n")
        f.write(f"Device: {summary.device}\n")
        f.write(f"Bucket size: {summary.bucket_ms} ms\n")
        f.write(f"Min samples: {summary.min_samples}\n\n")
        f.write("**Diagnostic only — cannot promote or reject a strategy.**\n\n")
        f.write("| source | target | symbol | signal | lag_ms | corr | alignment | samples | overlap_s | verdict |\n")
        f.write("|--------|--------|--------|--------|--------|------|-----------|---------|-----------|--------|\n")
        for r in summary.rows:
            corr_str = f"{r['correlation']:.4f}" if r['correlation'] is not None and math.isfinite(r['correlation']) else "N/A"
            align_str = f"{r['directional_alignment']:.4f}" if r['directional_alignment'] is not None and math.isfinite(r['directional_alignment']) else "N/A"
            f.write(f"| {r['source_venue']} | {r['target_venue']} | {r['symbol']} | {r['signal_type']} | {r['lag_ms']} | {corr_str} | {align_str} | {r['sample_count']} | {r['overlap_seconds']} | {r['verdict']} |\n")