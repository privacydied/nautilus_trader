"""Diagnostic-only candidate falsification summary.

Combines existing optional report artifacts into one survival/failure matrix.
This module is read-only public-data research infrastructure: it does not run
captures/evaluations, change thresholds, update registries, create strategies,
or imply live execution readiness.
"""

from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SAFETY_MODE = "public_data_observer_only"

FALSIFICATION_SUMMARY_READY = "FALSIFICATION_SUMMARY_READY"
NO_EVALUATED_GROUPS = "NO_EVALUATED_GROUPS"
NO_SURVIVING_GROUPS = "NO_SURVIVING_GROUPS"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
COST_WALL_BLOCKED = "COST_WALL_BLOCKED"
MISSING_REQUIRED_REPORTS = "MISSING_REQUIRED_REPORTS"

ALLOWED_VERDICTS = {
    FALSIFICATION_SUMMARY_READY,
    NO_EVALUATED_GROUPS,
    NO_SURVIVING_GROUPS,
    INSUFFICIENT_EVIDENCE,
    COST_WALL_BLOCKED,
    MISSING_REQUIRED_REPORTS,
}
FORBIDDEN_VERDICTS = {
    "REJECTED",
    "CANDIDATE",
    "CANDIDATE_FOR_LIVE",
    "EXECUTION_READY",
    "TRADE_READY",
    "READY_FOR_LIVE",
    "LIVE_READY",
    "DEPLOY_READY",
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
class FalsificationSummary:
    verdict: str
    reason: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    inputs: dict[str, str | None] = field(default_factory=dict)
    missing_reports: list[str] = field(default_factory=list)
    total_groups: int = 0
    surviving_diagnostic_groups: int = 0
    cost_wall_blocked_groups: int = 0
    safety_mode: str = SAFETY_MODE
    notes: str = (
        "Diagnostic-only falsification summary. Scores are sorting aids only; "
        "they do not promote live trading, execution readiness, or strategy creation."
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
            "inputs": self.inputs,
            "missing_reports": self.missing_reports,
            "total_groups": self.total_groups,
            "surviving_diagnostic_groups": self.surviving_diagnostic_groups,
            "cost_wall_blocked_groups": self.cost_wall_blocked_groups,
            "rows": self.rows,
        }


def validate_verdict(verdict: str) -> None:
    if verdict in FORBIDDEN_VERDICTS:
        raise ValueError(f"Forbidden verdict '{verdict}' in candidate falsification summary")
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


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _capture_mode_from(data: dict[str, Any]) -> str:
    meta = data.get("_metadata") or {}
    return str(meta.get("capture_mode") or data.get("capture_mode") or "")


def _key_from_group(group: dict[str, Any], capture_mode: str = "") -> GroupKey:
    symbol = str(
        group.get("symbol")
        or group.get("target_symbol")
        or group.get("source_symbol")
        or group.get("instrument")
        or ""
    )
    return GroupKey(
        source_venue=str(group.get("source_venue") or ""),
        target_venue=str(group.get("target_venue") or ""),
        symbol=symbol,
        signal_type=str(group.get("signal_type") or ""),
        lookback_ms=_to_int(group.get("lookback_ms")),
        horizon_ms=_to_int(group.get("horizon_ms")),
        oi_bucket=str(group.get("oi_bucket") or group.get("open_interest_bucket") or ""),
        capture_mode=str(group.get("capture_mode") or capture_mode or ""),
    )


def _key_from_null_result(result: dict[str, Any], capture_mode: str = "") -> GroupKey:
    group = result.get("group") or result
    symbol = str(group.get("symbol") or group.get("target_symbol") or group.get("source_symbol") or "")
    return GroupKey(
        source_venue=str(group.get("source_venue") or ""),
        target_venue=str(group.get("target_venue") or ""),
        symbol=symbol,
        signal_type=str(group.get("signal_type") or ""),
        lookback_ms=_to_int(group.get("lookback_ms")),
        horizon_ms=_to_int(group.get("horizon_ms")),
        oi_bucket=str(group.get("oi_bucket") or group.get("open_interest_bucket") or ""),
        capture_mode=str(group.get("capture_mode") or capture_mode or ""),
    )


def _key_from_heatmap_row(row: dict[str, Any], capture_mode: str = "") -> GroupKey:
    return GroupKey(
        source_venue=str(row.get("source_venue") or ""),
        target_venue=str(row.get("target_venue") or ""),
        symbol=str(row.get("symbol") or ""),
        signal_type=str(row.get("signal_type") or ""),
        lookback_ms=0,
        horizon_ms=_to_int(row.get("lag_ms")),
        oi_bucket=str(row.get("oi_bucket") or ""),
        capture_mode=str(row.get("capture_mode") or capture_mode or ""),
    )


def _matching_heatmap_support(key: GroupKey, heatmap_rows: dict[GroupKey, dict[str, Any]]) -> dict[str, Any] | None:
    exact = heatmap_rows.get(key)
    if exact is not None:
        return exact
    for hk, row in heatmap_rows.items():
        if (
            hk.source_venue == key.source_venue
            and hk.target_venue == key.target_venue
            and (not hk.symbol or not key.symbol or hk.symbol == key.symbol)
            and hk.horizon_ms == key.horizon_ms
            and (not hk.capture_mode or not key.capture_mode or hk.capture_mode == key.capture_mode)
        ):
            return row
    return None


def _empty_row(key: GroupKey) -> dict[str, Any]:
    return {
        **key.to_dict(),
        "mean_raw_bps": None,
        "mean_net_bps": None,
        "valid_count": 0,
        "win_rate": None,
        "raw_edge_positive": False,
        "mean_net_positive": False,
        "raw_exceeds_cost_wall": False,
        "current_all_in_cost_bps": None,
        "breakeven_cost_bps": None,
        "cost_viable_below_threshold": False,
        "null_survived": None,
        "heatmap_supported": None,
        "captures_seen": None,
        "positive_mean_raw_captures": None,
        "positive_mean_net_captures": None,
        "evidence_sources_available": 0,
        "falsification_score": 0,
        "diagnostic_status": "needs more evidence",
        "missing_evidence": [],
        "evidence_notes": [],
    }


def _merge_dict_without_none(row: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if value is not None:
            row[key] = value


def load_evaluated_groups(report_dir: str | Path | None) -> tuple[dict[GroupKey, dict[str, Any]], str | None]:
    if not report_dir:
        return {}, "evaluated_report"
    data = _load_json(Path(report_dir) / "summary.json")
    if data is None:
        return {}, "evaluated_report"
    capture_mode = _capture_mode_from(data)
    rows: dict[GroupKey, dict[str, Any]] = {}
    for group in data.get("results_by_group") or data.get("groups") or []:
        key = _key_from_group(group, capture_mode)
        raw = _to_float(group.get("mean_raw_bps"))
        net = _to_float(group.get("mean_net_bps"))
        cost = _to_float(group.get("all_in_cost_bps") or data.get("all_in_cost_bps"))
        rows[key] = {
            "mean_raw_bps": raw,
            "mean_net_bps": net,
            "valid_count": _to_int(group.get("valid_count")),
            "win_rate": _to_float(group.get("win_rate")),
            "raw_edge_positive": raw is not None and raw > 0.0,
            "mean_net_positive": net is not None and net > 0.0,
            "raw_exceeds_cost_wall": raw is not None and cost is not None and raw > cost,
            "current_all_in_cost_bps": cost,
            "evaluated_report_present": True,
        }
    if not rows:
        return {}, None
    return rows, None


def load_cost_sensitivity(cost_dir: str | Path | None, viability_cost_bps: float) -> tuple[dict[GroupKey, dict[str, Any]], str | None]:
    if not cost_dir:
        return {}, "cost_sensitivity_report"
    data = _load_json(Path(cost_dir) / "cost_sensitivity_summary.json")
    if data is None:
        return {}, "cost_sensitivity_report"
    capture_mode = _capture_mode_from(data)
    rows: dict[GroupKey, dict[str, Any]] = {}
    for item in data.get("rows") or []:
        key = _key_from_group(item, capture_mode)
        breakeven = _to_float(item.get("breakeven_cost_bps"))
        rows[key] = {
            "breakeven_cost_bps": breakeven,
            "cost_viable_below_threshold": breakeven is not None and breakeven > viability_cost_bps,
            "current_all_in_cost_bps": _to_float(item.get("current_all_in_cost_bps")),
            "cost_sensitivity_present": True,
        }
    return rows, None


def load_null_results(null_dir: str | Path | None) -> tuple[dict[GroupKey, dict[str, Any]], str | None]:
    if not null_dir:
        return {}, "permutation_null_report"
    summary_path = Path(null_dir) / "null_test_summary.json"
    data = _load_json(summary_path)
    if data is None:
        data = _load_json(Path(null_dir) / "permutation_null_summary.json")
    if data is None:
        return {}, "permutation_null_report"
    capture_mode = _capture_mode_from(data)
    rows: dict[GroupKey, dict[str, Any]] = {}
    for result in data.get("results") or data.get("group_results") or []:
        key = _key_from_null_result(result, capture_mode)
        rows[key] = {
            "null_survived": result.get("candidate_survives_null") is True,
            "null_report_present": True,
            "null_verdict": result.get("verdict") or data.get("overall_verdict") or "",
            "empirical_p_value": _to_float(result.get("empirical_p_value")),
        }
    if not rows and data.get("skipped"):
        return {}, None
    return rows, None


def load_heatmap(heatmap_dir: str | Path | None) -> tuple[dict[GroupKey, dict[str, Any]], str | None]:
    if not heatmap_dir:
        return {}, "lead_lag_heatmap_report"
    data = _load_json(Path(heatmap_dir) / "lead_lag_heatmap_summary.json")
    if data is None:
        return {}, "lead_lag_heatmap_report"
    capture_mode = _capture_mode_from(data)
    rows: dict[GroupKey, dict[str, Any]] = {}
    for item in data.get("rows") or []:
        key = _key_from_heatmap_row(item, capture_mode)
        supported = item.get("verdict") == "LEAD_LAG_DIAGNOSTIC_READY"
        corr = _to_float(item.get("correlation"))
        align = _to_float(item.get("directional_alignment"))
        rows[key] = {
            "heatmap_supported": supported,
            "heatmap_correlation": corr,
            "heatmap_directional_alignment": align,
            "heatmap_sample_count": _to_int(item.get("sample_count")),
            "heatmap_report_present": True,
        }
    return rows, None


def load_consistency(consistency_dir: str | Path | None) -> tuple[dict[GroupKey, dict[str, Any]], str | None]:
    if not consistency_dir:
        return {}, "cross_capture_consistency_report"
    data = _load_json(Path(consistency_dir) / "cross_capture_consistency_summary.json")
    if data is None:
        return {}, "cross_capture_consistency_report"
    rows: dict[GroupKey, dict[str, Any]] = {}
    for item in data.get("rows") or []:
        key = _key_from_group(item, item.get("capture_mode") or "")
        rows[key] = {
            "captures_seen": _to_int(item.get("captures_seen")),
            "positive_mean_raw_captures": _to_int(item.get("positive_mean_raw_captures")),
            "positive_mean_net_captures": _to_int(item.get("positive_mean_net_captures")),
            "consistency_score": _to_float(item.get("consistency_score")),
            "cross_capture_present": True,
        }
    return rows, None


def compute_candidate_falsification_summary(
    *,
    evaluated_report_dir: str | Path | None = None,
    cost_sensitivity_dir: str | Path | None = None,
    permutation_null_dir: str | Path | None = None,
    heatmap_dir: str | Path | None = None,
    consistency_dir: str | Path | None = None,
    viability_cost_bps: float = 50.0,
    min_events: int = 50,
) -> FalsificationSummary:
    inputs = {
        "evaluated_report_dir": str(evaluated_report_dir) if evaluated_report_dir else None,
        "cost_sensitivity_dir": str(cost_sensitivity_dir) if cost_sensitivity_dir else None,
        "permutation_null_dir": str(permutation_null_dir) if permutation_null_dir else None,
        "heatmap_dir": str(heatmap_dir) if heatmap_dir else None,
        "consistency_dir": str(consistency_dir) if consistency_dir else None,
    }
    if not any(inputs.values()):
        return FalsificationSummary(
            verdict=MISSING_REQUIRED_REPORTS,
            reason="No report paths provided",
            inputs=inputs,
            missing_reports=list(inputs.keys()),
        )

    evaluated, missing_eval = load_evaluated_groups(evaluated_report_dir)
    cost_rows, missing_cost = load_cost_sensitivity(cost_sensitivity_dir, viability_cost_bps)
    null_rows, missing_null = load_null_results(permutation_null_dir)
    heat_rows, missing_heat = load_heatmap(heatmap_dir)
    consistency_rows, missing_consistency = load_consistency(consistency_dir)
    missing_reports = [m for m in [missing_eval, missing_cost, missing_null, missing_heat, missing_consistency] if m]

    if not evaluated:
        return FalsificationSummary(
            verdict=NO_EVALUATED_GROUPS if evaluated_report_dir else MISSING_REQUIRED_REPORTS,
            reason="No evaluated groups loaded from evaluated report",
            inputs=inputs,
            missing_reports=missing_reports,
        )

    # Some derived diagnostics (especially older cost-sensitivity outputs) do not
    # carry capture_mode even when the evaluated report does. Treat a blank
    # capture_mode as unavailable metadata and resolve it to a unique evaluated
    # key. Non-blank differing modes remain separate.
    cost_rows = _normalize_to_evaluated_keys(cost_rows, evaluated)
    null_rows = _normalize_to_evaluated_keys(null_rows, evaluated)
    consistency_rows = _normalize_to_evaluated_keys(consistency_rows, evaluated)

    all_keys: set[GroupKey] = set(evaluated)
    all_keys.update(cost_rows)
    all_keys.update(null_rows)
    all_keys.update(consistency_rows)

    rows: list[dict[str, Any]] = []
    for key in all_keys:
        row = _empty_row(key)
        _merge_dict_without_none(row, evaluated.get(key, {}))
        _merge_dict_without_none(row, cost_rows.get(key, {}))
        _merge_dict_without_none(row, null_rows.get(key, {}))
        heat = _matching_heatmap_support(key, heat_rows)
        if heat:
            _merge_dict_without_none(row, heat)
        _merge_dict_without_none(row, consistency_rows.get(key, {}))
        _finalize_row(row, min_events=min_events)
        rows.append(row)

    rows.sort(
        key=lambda r: (
            r["evidence_sources_available"],
            r.get("captures_seen") or 0,
            1 if r.get("mean_net_positive") else 0,
            1 if r.get("null_survived") else 0,
            r.get("breakeven_cost_bps") if r.get("breakeven_cost_bps") is not None else float("-inf"),
            r.get("mean_raw_bps") if r.get("mean_raw_bps") is not None else float("-inf"),
        ),
        reverse=True,
    )

    cost_blocked = sum(1 for r in rows if r["diagnostic_status"] == "blocked by costs")
    surviving = sum(1 for r in rows if r["diagnostic_status"] == "survives diagnostic filter")
    if surviving:
        verdict = FALSIFICATION_SUMMARY_READY
        reason = f"{surviving} group(s) survive the diagnostic filter; no live readiness implied"
    elif cost_blocked:
        verdict = COST_WALL_BLOCKED
        reason = f"{cost_blocked} group(s) have positive raw edge but remain cost-wall blocked"
    elif missing_null or missing_consistency:
        verdict = INSUFFICIENT_EVIDENCE
        reason = "Missing falsification and/or cross-capture evidence"
    else:
        verdict = NO_SURVIVING_GROUPS
        reason = "No group survived the diagnostic falsification matrix"

    return FalsificationSummary(
        verdict=verdict,
        reason=reason,
        rows=rows,
        inputs=inputs,
        missing_reports=missing_reports,
        total_groups=len(rows),
        surviving_diagnostic_groups=surviving,
        cost_wall_blocked_groups=cost_blocked,
    )


def _normalize_to_evaluated_keys(
    rows: dict[GroupKey, dict[str, Any]],
    evaluated: dict[GroupKey, dict[str, Any]],
) -> dict[GroupKey, dict[str, Any]]:
    """Map blank-capture-mode diagnostics onto a unique evaluated key.

    This preserves the capture_mode key when the diagnostic explicitly provides
    one, while letting older derived reports merge with their source evaluated
    report when capture_mode was omitted.
    """
    normalized: dict[GroupKey, dict[str, Any]] = {}
    for key, value in rows.items():
        if key.capture_mode:
            normalized[key] = value
            continue
        matches = [
            eval_key for eval_key in evaluated
            if eval_key.source_venue == key.source_venue
            and eval_key.target_venue == key.target_venue
            and eval_key.symbol == key.symbol
            and eval_key.signal_type == key.signal_type
            and eval_key.lookback_ms == key.lookback_ms
            and eval_key.horizon_ms == key.horizon_ms
            and eval_key.oi_bucket == key.oi_bucket
        ]
        normalized[matches[0] if len(matches) == 1 else key] = value
    return normalized


def _finalize_row(row: dict[str, Any], *, min_events: int) -> None:
    evidence_sources = 0
    for flag in (
        "evaluated_report_present",
        "cost_sensitivity_present",
        "null_report_present",
        "heatmap_report_present",
        "cross_capture_present",
    ):
        if row.get(flag):
            evidence_sources += 1
    row["evidence_sources_available"] = evidence_sources

    missing = []
    if not row.get("evaluated_report_present"):
        missing.append("evaluated_report")
    if not row.get("cost_sensitivity_present"):
        missing.append("cost_sensitivity")
    if not row.get("null_report_present"):
        missing.append("permutation_null")
    if not row.get("heatmap_report_present"):
        missing.append("lead_lag_heatmap")
    if not row.get("cross_capture_present"):
        missing.append("cross_capture_consistency")
    row["missing_evidence"] = missing

    raw_positive = bool(row.get("raw_edge_positive"))
    net_positive = bool(row.get("mean_net_positive"))
    cost_viable = bool(row.get("cost_viable_below_threshold"))
    null_survived = row.get("null_survived") is True
    heat_supported = row.get("heatmap_supported") is True
    captures_seen = row.get("captures_seen")
    enough_events = _to_int(row.get("valid_count")) >= min_events

    score = 0
    if raw_positive:
        score += 1
    if net_positive:
        score += 1
    if cost_viable:
        score += 1
    if null_survived:
        score += 1
    if heat_supported:
        score += 1
    if captures_seen is not None and captures_seen >= 2:
        score += 1
    if captures_seen is None or captures_seen <= 1:
        score -= 1
    if not enough_events:
        score -= 1
    if "permutation_null" in missing:
        score -= 1
    if "cross_capture_consistency" in missing:
        score -= 1
    row["falsification_score"] = score

    notes = []
    if raw_positive and not net_positive:
        notes.append("positive raw edge but not net-positive")
    if raw_positive and not cost_viable:
        notes.append("blocked by configured cost threshold")
    if "permutation_null" in missing:
        notes.append("missing permutation/null evidence")
    if "cross_capture_consistency" in missing:
        notes.append("missing cross-capture recurrence evidence")
    if captures_seen is not None and captures_seen <= 1:
        notes.append("only one capture")
    if not enough_events:
        notes.append(f"below min_events={min_events}")
    row["evidence_notes"] = notes

    if net_positive and cost_viable and null_survived and captures_seen is not None and captures_seen >= 2:
        row["diagnostic_status"] = "survives diagnostic filter"
    elif raw_positive and not net_positive:
        row["diagnostic_status"] = "blocked by costs"
    elif raw_positive:
        row["diagnostic_status"] = "near-miss"
    else:
        row["diagnostic_status"] = "needs more evidence"


def write_candidate_falsification_reports(summary: FalsificationSummary, out_dir: str | Path) -> None:
    validate_verdict(summary.verdict)
    out = Path(out_dir)
    os.makedirs(out, exist_ok=True)
    data = summary.to_dict()

    with open(out / "candidate_falsification_summary.json", "w") as f:
        json.dump(data, f, indent=2, default=str)

    fieldnames = [
        *GROUP_KEY_FIELDS,
        "mean_raw_bps",
        "mean_net_bps",
        "valid_count",
        "win_rate",
        "raw_edge_positive",
        "mean_net_positive",
        "raw_exceeds_cost_wall",
        "current_all_in_cost_bps",
        "breakeven_cost_bps",
        "cost_viable_below_threshold",
        "null_survived",
        "heatmap_supported",
        "captures_seen",
        "positive_mean_raw_captures",
        "positive_mean_net_captures",
        "evidence_sources_available",
        "falsification_score",
        "diagnostic_status",
        "missing_evidence",
        "evidence_notes",
    ]
    with open(out / "candidate_falsification_matrix.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in data["rows"]:
            csv_row = dict(row)
            csv_row["missing_evidence"] = ";".join(row.get("missing_evidence") or [])
            csv_row["evidence_notes"] = ";".join(row.get("evidence_notes") or [])
            writer.writerow(csv_row)

    lines = [
        "# Candidate Falsification Summary\n",
        f"**Verdict:** {data['verdict']}\n",
        f"**Safety mode:** {data['safety_mode']}\n",
        f"**Reason:** {data['reason']}\n",
        f"**Total groups:** {data['total_groups']}\n",
        f"**Surviving diagnostic groups:** {data['surviving_diagnostic_groups']}\n",
        f"**Cost-wall blocked groups:** {data['cost_wall_blocked_groups']}\n",
        "\nDiagnostic-only: this report does not create candidates, reject research, update registries, or imply live execution readiness.\n",
    ]
    if data["missing_reports"]:
        lines.append("\n## Missing Report Types\n")
        for item in data["missing_reports"]:
            lines.append(f"- {item}\n")
    if data["rows"]:
        lines.append("\n## Falsification Matrix\n")
        lines.append("| # | Source→Target | Symbol | Signal/Lookback | Horizon | Mode | Raw | Net | Breakeven | Null | Heatmap | Captures | Score | Status | Missing |\n")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for idx, row in enumerate(data["rows"][:50], 1):
            missing = ", ".join(row.get("missing_evidence") or [])
            lines.append(
                f"| {idx} | {row.get('source_venue', '')}→{row.get('target_venue', '')} | "
                f"{row.get('symbol', '')} | {row.get('signal_type', '')}/{row.get('lookback_ms', 0)}ms | "
                f"{row.get('horizon_ms', 0)}ms | {row.get('capture_mode', '')} | "
                f"{_fmt(row.get('mean_raw_bps'))} | {_fmt(row.get('mean_net_bps'))} | "
                f"{_fmt(row.get('breakeven_cost_bps'))} | {row.get('null_survived')} | "
                f"{row.get('heatmap_supported')} | {row.get('captures_seen')} | "
                f"{row.get('falsification_score')} | {row.get('diagnostic_status')} | {missing} |\n"
            )
    lines.append("\n## Scoring\n")
    lines.append("+1 raw positive; +1 net positive; +1 cost-viable below threshold; +1 null survived; +1 heatmap supported; +1 seen in >=2 captures; -1 only/missing one capture; -1 below min-events; -1 missing null; -1 missing cross-capture. Sorting aid only.\n")
    (out / "candidate_falsification.md").write_text("".join(lines))


def _fmt(value: Any) -> str:
    v = _to_float(value)
    return f"{v:.3f}" if v is not None else "N/A"
