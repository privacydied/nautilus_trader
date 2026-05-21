"""
Cross-capture consistency aggregation for derivatives spot lead-lag reports.

Reads multiple existing evaluated report directories and aggregates exact
configuration groups across captures. This is diagnostic-only public-data
research infrastructure: it cannot promote candidates, reject research, update
registries, change thresholds, or touch live/execution paths.
"""

from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from statistics import mean
from statistics import median
from typing import Any
from typing import Iterable
from typing import Sequence


SAFETY_MODE = "public_data_observer_only"

CROSS_CAPTURE_CONSISTENCY_READY = "CROSS_CAPTURE_CONSISTENCY_READY"
NO_REPORTS_FOUND = "NO_REPORTS_FOUND"
NO_FINITE_GROUPS = "NO_FINITE_GROUPS"
INSUFFICIENT_CAPTURE_COUNT = "INSUFFICIENT_CAPTURE_COUNT"

ALLOWED_VERDICTS = {
    CROSS_CAPTURE_CONSISTENCY_READY,
    NO_REPORTS_FOUND,
    NO_FINITE_GROUPS,
    INSUFFICIENT_CAPTURE_COUNT,
}
FORBIDDEN_VERDICTS = {
    "REJECTED",
    "CANDIDATE",
    "CANDIDATE_FOR_LIVE",
    "CANDIDATE_FOR_LONGER_OBSERVATION",
    "READY_FOR_LIVE",
    "EXECUTION_READY",
    "LIVE_READY",
}

GROUP_KEY_FIELDS = (
    "source_venue",
    "target_venue",
    "symbol",
    "signal_type",
    "lookback_ms",
    "horizon_ms",
    "oi_bucket",
    "capture_mode",
)


@dataclass(frozen=True)
class GroupKey:
    """Exact config identity for recurring-group aggregation."""

    source_venue: str = ""
    target_venue: str = ""
    symbol: str = ""
    signal_type: str = ""
    lookback_ms: int = 0
    horizon_ms: int = 0
    oi_bucket: str = ""
    capture_mode: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_venue": self.source_venue,
            "target_venue": self.target_venue,
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "lookback_ms": self.lookback_ms,
            "horizon_ms": self.horizon_ms,
            "oi_bucket": self.oi_bucket,
            "capture_mode": self.capture_mode,
        }


@dataclass
class CrossCaptureConsistencySummary:
    """Top-level diagnostic summary."""

    report_dirs: list[str]
    total_reports: int
    loaded_reports: int
    total_group_observations: int
    finite_group_observations: int
    unique_groups: int
    recurring_groups: int
    verdict: str
    reason: str
    min_captures: int = 2
    include_capture_mode: bool = True
    rows: list[dict[str, Any]] = field(default_factory=list)
    report_metadata: list[dict[str, Any]] = field(default_factory=list)
    safety_mode: str = SAFETY_MODE
    notes: str = (
        "Diagnostic-only cross-capture aggregation. This report may describe "
        "recurring near-misses, cost-wall constraints, or the need for more "
        "captures, but it cannot promote a strategy to live execution."
    )

    def __post_init__(self) -> None:
        validate_verdict(self.verdict)

    def to_dict(self) -> dict[str, Any]:
        validate_verdict(self.verdict)
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "safety_mode": self.safety_mode,
            "notes": self.notes,
            "report_dirs": self.report_dirs,
            "total_reports": self.total_reports,
            "loaded_reports": self.loaded_reports,
            "total_group_observations": self.total_group_observations,
            "finite_group_observations": self.finite_group_observations,
            "unique_groups": self.unique_groups,
            "recurring_groups": self.recurring_groups,
            "min_captures": self.min_captures,
            "include_capture_mode": self.include_capture_mode,
            "report_metadata": self.report_metadata,
            "rows": self.rows,
        }


def validate_verdict(verdict: str) -> None:
    """Raise if a forbidden/promotional verdict appears."""
    if verdict in FORBIDDEN_VERDICTS:
        raise ValueError(f"Forbidden verdict '{verdict}' in cross-capture consistency report")
    if verdict not in ALLOWED_VERDICTS:
        raise ValueError(f"Unsupported diagnostic verdict '{verdict}'")


def _is_finite(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        if not value.strip():
            return False
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    return False


def _to_float(value: Any) -> float | None:
    return float(value) if _is_finite(value) else None


def _to_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_mean(values: Sequence[float]) -> float | None:
    return round(mean(values), 6) if values else None


def _safe_median(values: Sequence[float]) -> float | None:
    return round(median(values), 6) if values else None


def _get_capture_mode(summary: dict[str, Any]) -> str:
    meta = summary.get("_metadata") or {}
    return str(meta.get("capture_mode") or summary.get("capture_mode") or "")


def _load_groups_from_csv(csv_path: Path) -> list[dict[str, Any]]:
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for key in ("lookback_ms", "horizon_ms", "valid_count"):
            if key in row:
                row[key] = _to_int(row[key])
        for key in ("mean_raw_bps", "mean_net_bps", "win_rate"):
            if key in row:
                row[key] = _to_float(row[key])
    return rows


def load_report(report_dir: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """
    Load evaluated groups and metadata from one report directory.

    Preferred schema is summary.json with results_by_group. summary.csv is a
    fallback for older reports.
    """
    path = Path(report_dir)
    if not path.exists() or not path.is_dir():
        return None

    summary_path = path / "summary.json"
    summary: dict[str, Any] = {}
    groups: list[dict[str, Any]] = []
    if summary_path.exists():
        with open(summary_path) as f:
            summary = json.load(f)
        groups = list(summary.get("results_by_group") or summary.get("groups") or [])
    if not groups:
        csv_path = path / "summary.csv"
        if csv_path.exists():
            groups = _load_groups_from_csv(csv_path)

    if not summary and not groups:
        return None

    metadata = {
        "report_dir": str(path),
        "report_name": path.name,
        "capture_mode": _get_capture_mode(summary),
        "verdict": str(summary.get("verdict") or ""),
        "total_signals": summary.get("total_signals"),
        "valid_evaluations": summary.get("valid_evaluations"),
        "metadata": summary.get("_metadata") or {},
    }
    return groups, metadata


def _group_key(
    group: dict[str, Any],
    report_meta: dict[str, Any],
    *,
    include_capture_mode: bool = True,
) -> GroupKey:
    capture_mode = str(group.get("capture_mode") or report_meta.get("capture_mode") or "")
    if not include_capture_mode:
        capture_mode = ""
    return GroupKey(
        source_venue=str(group.get("source_venue") or ""),
        target_venue=str(group.get("target_venue") or ""),
        symbol=str(group.get("symbol") or group.get("instrument") or ""),
        signal_type=str(group.get("signal_type") or ""),
        lookback_ms=_to_int(group.get("lookback_ms")),
        horizon_ms=_to_int(group.get("horizon_ms")),
        oi_bucket=str(group.get("oi_bucket") or group.get("open_interest_bucket") or ""),
        capture_mode=capture_mode,
    )


def _consistency_score(
    captures_seen: int,
    finite_raw_count: int,
    positive_raw_count: int,
    positive_net_count: int,
    loaded_reports: int,
) -> float:
    if loaded_reports <= 0:
        return 0.0
    recurrence = captures_seen / loaded_reports
    raw_consistency = positive_raw_count / finite_raw_count if finite_raw_count else 0.0
    net_consistency = positive_net_count / finite_raw_count if finite_raw_count else 0.0
    return round((0.5 * recurrence) + (0.3 * raw_consistency) + (0.2 * net_consistency), 6)


def compute_cross_capture_consistency(
    report_dirs: Iterable[str | Path],
    *,
    min_captures: int = 2,
    include_capture_mode: bool = True,
) -> CrossCaptureConsistencySummary:
    """Aggregate exact config groups across evaluated report directories."""
    dirs = [str(Path(p)) for p in report_dirs]
    if not dirs:
        return CrossCaptureConsistencySummary(
            report_dirs=[],
            total_reports=0,
            loaded_reports=0,
            total_group_observations=0,
            finite_group_observations=0,
            unique_groups=0,
            recurring_groups=0,
            min_captures=min_captures,
            include_capture_mode=include_capture_mode,
            verdict=NO_REPORTS_FOUND,
            reason="No report directories provided",
        )

    loaded: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for report_dir in dirs:
        loaded_report = load_report(report_dir)
        if loaded_report is not None:
            loaded.append(loaded_report)

    if not loaded:
        return CrossCaptureConsistencySummary(
            report_dirs=dirs,
            total_reports=len(dirs),
            loaded_reports=0,
            total_group_observations=0,
            finite_group_observations=0,
            unique_groups=0,
            recurring_groups=0,
            min_captures=min_captures,
            include_capture_mode=include_capture_mode,
            verdict=NO_REPORTS_FOUND,
            reason="No readable evaluated reports found",
        )

    grouped: dict[GroupKey, list[dict[str, Any]]] = {}
    report_metadata: list[dict[str, Any]] = []
    total_observations = 0
    finite_observations = 0

    for groups, meta in loaded:
        report_metadata.append(meta)
        seen_keys_for_report: set[GroupKey] = set()
        for group in groups:
            key = _group_key(group, meta, include_capture_mode=include_capture_mode)
            raw = _to_float(group.get("mean_raw_bps"))
            net = _to_float(group.get("mean_net_bps"))
            win_rate = _to_float(group.get("win_rate"))
            valid_count = _to_int(group.get("valid_count"))
            obs = {
                **key.to_dict(),
                "report_dir": meta["report_dir"],
                "report_name": meta["report_name"],
                "report_verdict": meta.get("verdict", ""),
                "mean_raw_bps": raw,
                "mean_net_bps": net,
                "win_rate": win_rate,
                "valid_count": valid_count,
                "candidate": bool(group.get("candidate", False)),
            }
            grouped.setdefault(key, []).append(obs)
            total_observations += 1
            if raw is not None:
                finite_observations += 1
            seen_keys_for_report.add(key)

    rows: list[dict[str, Any]] = []
    for key, observations in grouped.items():
        report_dirs_seen = sorted({o["report_dir"] for o in observations})
        captures_seen = len(report_dirs_seen)
        raw_values = [o["mean_raw_bps"] for o in observations if o["mean_raw_bps"] is not None]
        net_values = [o["mean_net_bps"] for o in observations if o["mean_net_bps"] is not None]
        win_rates = [o["win_rate"] for o in observations if o["win_rate"] is not None]
        valid_counts = [int(o["valid_count"] or 0) for o in observations]
        positive_raw_count = sum(1 for v in raw_values if v > 0.0)
        positive_net_count = sum(1 for v in net_values if v > 0.0)
        row = {
            **key.to_dict(),
            "captures_seen": captures_seen,
            "finite_mean_raw_captures": len(raw_values),
            "positive_mean_raw_captures": positive_raw_count,
            "positive_mean_net_captures": positive_net_count,
            "mean_of_mean_raw_bps": _safe_mean(raw_values),
            "median_of_mean_raw_bps": _safe_median(raw_values),
            "mean_of_mean_net_bps": _safe_mean(net_values),
            "median_of_mean_net_bps": _safe_median(net_values),
            "best_raw_bps": round(max(raw_values), 6) if raw_values else None,
            "worst_raw_bps": round(min(raw_values), 6) if raw_values else None,
            "best_net_bps": round(max(net_values), 6) if net_values else None,
            "worst_net_bps": round(min(net_values), 6) if net_values else None,
            "mean_win_rate": _safe_mean(win_rates),
            "median_win_rate": _safe_median(win_rates),
            "total_valid_count": sum(valid_counts),
            "min_valid_count": min(valid_counts) if valid_counts else 0,
            "max_valid_count": max(valid_counts) if valid_counts else 0,
            "consistency_score": _consistency_score(
                captures_seen,
                len(raw_values),
                positive_raw_count,
                positive_net_count,
                len(loaded),
            ),
            "report_dirs_seen": report_dirs_seen,
            "capture_modes_seen": sorted({str(o.get("capture_mode") or "") for o in observations}),
            "report_verdicts_seen": sorted({str(o.get("report_verdict") or "") for o in observations}),
            "diagnostic_label": _diagnostic_label(len(raw_values), positive_raw_count, positive_net_count),
        }
        rows.append(row)

    rows.sort(
        key=lambda r: (
            r["captures_seen"],
            r["positive_mean_raw_captures"],
            r["median_of_mean_raw_bps"] if r["median_of_mean_raw_bps"] is not None else float("-inf"),
            r["total_valid_count"],
        ),
        reverse=True,
    )

    finite_rows = [r for r in rows if r["finite_mean_raw_captures"] > 0]
    recurring_rows = [r for r in rows if r["captures_seen"] >= min_captures]
    if len(loaded) < min_captures:
        verdict = INSUFFICIENT_CAPTURE_COUNT
        reason = f"Loaded {len(loaded)} report(s); need at least {min_captures} for consistency aggregation"
    elif not finite_rows:
        verdict = NO_FINITE_GROUPS
        reason = "No groups with finite mean_raw_bps across loaded reports"
    else:
        verdict = CROSS_CAPTURE_CONSISTENCY_READY
        reason = f"Aggregated {len(rows)} unique groups across {len(loaded)} reports"

    return CrossCaptureConsistencySummary(
        report_dirs=dirs,
        total_reports=len(dirs),
        loaded_reports=len(loaded),
        total_group_observations=total_observations,
        finite_group_observations=finite_observations,
        unique_groups=len(rows),
        recurring_groups=len(recurring_rows),
        min_captures=min_captures,
        include_capture_mode=include_capture_mode,
        verdict=verdict,
        reason=reason,
        rows=rows,
        report_metadata=report_metadata,
    )


def _diagnostic_label(finite_raw_count: int, positive_raw_count: int, positive_net_count: int) -> str:
    if finite_raw_count <= 0:
        return "needs more captures"
    if positive_net_count > 0:
        return "recurring near-miss"
    if positive_raw_count > 0:
        return "cost-wall constrained"
    return "needs more captures"


def write_cross_capture_consistency_reports(
    summary: CrossCaptureConsistencySummary,
    out_dir: str | Path,
) -> None:
    """Write JSON, CSV, and Markdown diagnostics."""
    validate_verdict(summary.verdict)
    out = Path(out_dir)
    os.makedirs(out, exist_ok=True)
    data = summary.to_dict()

    with open(out / "cross_capture_consistency_summary.json", "w") as f:
        json.dump(data, f, indent=2, default=str)

    fieldnames = [
        *GROUP_KEY_FIELDS,
        "captures_seen",
        "finite_mean_raw_captures",
        "positive_mean_raw_captures",
        "positive_mean_net_captures",
        "mean_of_mean_raw_bps",
        "median_of_mean_raw_bps",
        "mean_of_mean_net_bps",
        "median_of_mean_net_bps",
        "best_raw_bps",
        "worst_raw_bps",
        "best_net_bps",
        "worst_net_bps",
        "mean_win_rate",
        "median_win_rate",
        "total_valid_count",
        "min_valid_count",
        "max_valid_count",
        "consistency_score",
        "diagnostic_label",
    ]
    with open(out / "cross_capture_consistency.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in data["rows"]:
            writer.writerow(row)

    lines: list[str] = []
    lines.append("# Cross-Capture Consistency Diagnostic\n")
    lines.append(f"**Verdict:** {data['verdict']}\n")
    lines.append(f"**Safety mode:** {data['safety_mode']}\n")
    lines.append(f"**Reason:** {data['reason']}\n")
    lines.append(f"**Loaded reports:** {data['loaded_reports']} / {data['total_reports']}\n")
    lines.append(f"**Unique groups:** {data['unique_groups']}\n")
    lines.append(f"**Recurring groups (captures >= {data['min_captures']}):** {data['recurring_groups']}\n")
    lines.append("\nDiagnostic-only: this does not create candidates, reject research, update registries, or imply execution readiness.\n")

    if data["rows"]:
        lines.append("\n## Top Groups (Sorted by Consistency, Not Best Single Return)\n")
        lines.append("| # | Source→Target | Symbol | Signal/Lookback | Horizon | Mode | Captures | +Raw | +Net | Median Raw | Median Net | Total N | Score | Diagnostic |\n")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for i, row in enumerate(data["rows"][:50], 1):
            src_tgt = f"{row.get('source_venue', '')}→{row.get('target_venue', '')}"
            sig = f"{row.get('signal_type', '')}/{row.get('lookback_ms', 0)}ms"
            median_raw = _fmt(row.get("median_of_mean_raw_bps"))
            median_net = _fmt(row.get("median_of_mean_net_bps"))
            lines.append(
                f"| {i} | {src_tgt} | {row.get('symbol', '')} | {sig} | "
                f"{row.get('horizon_ms', 0)}ms | {row.get('capture_mode', '')} | "
                f"{row.get('captures_seen', 0)} | {row.get('positive_mean_raw_captures', 0)} | "
                f"{row.get('positive_mean_net_captures', 0)} | {median_raw} | {median_net} | "
                f"{row.get('total_valid_count', 0)} | {row.get('consistency_score', 0)} | "
                f"{row.get('diagnostic_label', '')} |\n"
            )

    lines.append("\n## Notes\n")
    lines.append("- Sorting prioritizes recurrence across captures, then positive raw consistency, then median raw return.\n")
    lines.append("- Single-window best returns are intentionally not the primary sort key to avoid cherry-picking.\n")
    lines.append("- Allowed verdicts: " + ", ".join(sorted(ALLOWED_VERDICTS)) + ".\n")
    (out / "cross_capture_consistency.md").write_text("".join(lines))


def _fmt(value: Any) -> str:
    v = _to_float(value)
    return f"{v:.3f}" if v is not None else "N/A"
