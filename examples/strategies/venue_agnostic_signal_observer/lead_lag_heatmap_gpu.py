"""GPU-ready lead/lag heatmap diagnostics for venue_agnostic_signal_observer.

Diagnostic only. No candidate generation. No verdict promotion/rejection.
No network, no orders, no registry updates, no live trading.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

SAFETY_MODE = "public_data_observer_only"
DEFAULT_LAGS_MS = [100, 250, 500, 1000, 2000, 5000, 10000, 30000]


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
    verdict: str = "NO_SIGNAL_SERIES"
    reason_counts: dict[str, int] = field(default_factory=dict)
    safety_mode: str = SAFETY_MODE

    def to_dict(self) -> dict:
        return asdict(self)


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
        chunk = values[idx : idx + bucket_ms]
        if not chunk:
            continue
        finite = [v for v in chunk if math.isfinite(v)]
        if not finite:
            buckets.append(0.0)
        else:
            buckets.append(sum(finite) / len(finite))
    return buckets


def check_cuda_available(device: str = "cuda:0") -> tuple[bool, str]:
    try:
        import torch  # noqa: PLC0415
    except ImportError:
        return False, "torch_not_installed"
    if not torch.cuda.is_available():
        return False, "torch_cuda_unavailable"
    try:
        idx = int(device.split(":")[-1]) if ":" in device else 0
        if idx >= torch.cuda.device_count():
            return False, f"cuda_device_not_found:{device}"
    except (ValueError, IndexError):
        return False, f"invalid_device_string:{device}"
    return True, "cuda_available"


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
) -> LeadLagHeatmapSummary:
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
    src_vals, tgt_vals = _finite_pair_series(source_values, target_values)
    if not src_vals or not tgt_vals:
        summary.verdict = "NO_SIGNAL_SERIES"
        return summary
    src_bucket = _bucketize_series(src_vals, bucket_ms)
    tgt_bucket = _bucketize_series(tgt_vals, bucket_ms)
    if len(src_bucket) < min_samples or len(tgt_bucket) < min_samples:
        summary.verdict = "INSUFFICIENT_SAMPLES"
        return summary
    overlap_seconds = min(len(source_timestamps), len(target_timestamps)) * (bucket_ms / 1000.0)
    if overlap_seconds <= 0:
        summary.verdict = "INSUFFICIENT_OVERLAP"
        return summary
    rows: list[dict] = []
    for lag_ms in lags_ms:
        shift = max(0, int(round(lag_ms / bucket_ms)))
        if shift >= len(src_bucket) or shift >= len(tgt_bucket):
            corr = None
            align = None
            sample_count = 0
        else:
            x = src_bucket[:-shift] if shift else src_bucket
            y = tgt_bucket[shift:] if shift else tgt_bucket
            x, y = _finite_pair_series(x, y)
            sample_count = len(x)
            corr = _pearson(x, y)
            align = _alignment(x, y)
        verdict = "LEAD_LAG_DIAGNOSTIC_READY" if sample_count >= min_samples else "INSUFFICIENT_SAMPLES"
        rows.append(asdict(LeadLagHeatmapRow(
            source_venue=source_venue,
            target_venue=target_venue,
            symbol=symbol,
            signal_type=signal_type,
            lag_ms=lag_ms,
            correlation=corr,
            directional_alignment=align,
            sample_count=sample_count,
            overlap_seconds=overlap_seconds,
            verdict=verdict,
            notes="cpu" if engine == "cpu" else "gpu",
        )))
    summary.rows = rows
    summary.verdict = "LEAD_LAG_DIAGNOSTIC_READY" if any(r["verdict"] == "LEAD_LAG_DIAGNOSTIC_READY" for r in rows) else "INSUFFICIENT_SAMPLES"
    summary.reason_counts = {k: sum(1 for r in rows if r["verdict"] == k) for k in {r["verdict"] for r in rows}}
    return summary


def write_heatmap_reports(summary: LeadLagHeatmapSummary, out_dir: str | Path) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "lead_lag_heatmap_summary.json", "w") as f:
        json.dump(summary.to_dict(), f, indent=2, default=str)
    if summary.rows:
        with open(out / "lead_lag_heatmap.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(summary.rows[0].keys()))
            writer.writeheader()
            writer.writerows(summary.rows)
    with open(out / "lead_lag_heatmap.md", "w") as f:
        f.write("# Lead/Lag Heatmap Diagnostics\n\n")
        f.write(f"Verdict: {summary.verdict}\n\n")
        for row in summary.rows:
            f.write(f"- {row['source_venue']} -> {row['target_venue']} {row['symbol']} {row['signal_type']} lag={row['lag_ms']}ms corr={row['correlation']} align={row['directional_alignment']} samples={row['sample_count']} overlap_s={row['overlap_seconds']} verdict={row['verdict']}\n")
