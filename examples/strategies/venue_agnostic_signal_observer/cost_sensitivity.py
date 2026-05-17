"""Cost-sensitivity / breakeven diagnostic for derivatives spot lead-lag reports.

Reads an existing evaluation report and computes breakeven cost levels for
each evaluated group. This is a diagnostic-only tool — it cannot create
candidates, change verdicts, update registries, or alter evaluator behavior.

Allowed verdicts:
  COST_SENSITIVITY_READY  — groups exist with finite raw bps values
  NO_EVALUATED_GROUPS      — report contains zero groups
  NO_FINITE_GROUPS         — all groups have non-finite raw bps

Forbidden verdicts (raise ValueError):
  REJECTED, CANDIDATE, CANDIDATE_FOR_LONGER_OBSERVATION
"""

from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Sequence

SAFETY_MODE = "public_data_observer_only"

# ---------------------------------------------------------------------------
# Allowed / forbidden verdicts
# ---------------------------------------------------------------------------

_COST_SENSITIVITY_READY = "COST_SENSITIVITY_READY"
_NO_EVALUATED_GROUPS = "NO_EVALUATED_GROUPS"
_NO_FINITE_GROUPS = "NO_FINITE_GROUPS"

_ALLOWED_VERDICTS = {_COST_SENSITIVITY_READY, _NO_EVALUATED_GROUPS, _NO_FINITE_GROUPS}
_FORBIDDEN_VERDICTS = {"REJECTED", "CANDIDATE", "CANDIDATE_FOR_LONGER_OBSERVATION"}

# Public aliases (no leading underscore) for external & test imports
COST_SENSITIVITY_READY = _COST_SENSITIVITY_READY
NO_EVALUATED_GROUPS = _NO_EVALUATED_GROUPS
NO_FINITE_GROUPS = _NO_FINITE_GROUPS

DEFAULT_COST_LEVELS_BPS = [50.0, 10.0, 5.0, 1.0, 0.5]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CostSensitivityRow:
    """One evaluated group annotated with cost-sensitivity metrics."""
    source_venue: str
    target_venue: str
    signal_type: str
    lookback_ms: int
    horizon_ms: int
    mean_raw_bps: float | None
    median_raw_bps: float | None
    mean_net_bps: float | None
    current_all_in_cost_bps: float
    breakeven_cost_bps: float | None
    margin_vs_50bps: float | None
    margin_vs_10bps: float | None
    margin_vs_5bps: float | None
    margin_vs_1bps: float | None
    margin_vs_0_5bps: float | None
    valid_count: int
    win_rate: float | None
    original_candidate: bool
    cost_margins_bps: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validate no forbidden verdicts sneak in via notes etc.
        # This dataclass doesn't carry verdicts itself, but validate
        # any downstream verdict against forbidden labels.
        pass


@dataclass
class CostSensitivitySummary:
    """Top-level summary of the cost-sensitivity diagnostic."""
    report_dir: str
    all_in_cost_bps: float
    fee_bps: float
    slippage_bps: float
    quote_mismatch_buffer_bps: float
    cost_levels_bps: list[float]
    total_groups: int
    finite_groups: int
    verdict: str = _NO_EVALUATED_GROUPS
    reason: str = ""
    rows: list[dict] = field(default_factory=list)
    safety_mode: str = SAFETY_MODE
    notes: str = ""

    def __post_init__(self) -> None:
        self._validate_verdict(self.verdict)

    @staticmethod
    def _validate_verdict(verdict: str) -> None:
        if verdict in _FORBIDDEN_VERDICTS:
            raise ValueError(
                f"Forbidden verdict '{verdict}' in cost-sensitivity module. "
                f"Allowed: {sorted(_ALLOWED_VERDICTS)}"
            )

    def to_dict(self) -> dict:
        d = {
            "report_dir": self.report_dir,
            "all_in_cost_bps": self.all_in_cost_bps,
            "fee_bps": self.fee_bps,
            "slippage_bps": self.slippage_bps,
            "quote_mismatch_buffer_bps": self.quote_mismatch_buffer_bps,
            "cost_levels_bps": self.cost_levels_bps,
            "total_groups": self.total_groups,
            "finite_groups": self.finite_groups,
            "verdict": self.verdict,
            "reason": self.reason,
            "safety_mode": self.safety_mode,
            "notes": self.notes,
            "rows": self.rows,
        }
        return d


# ---------------------------------------------------------------------------
# Finite helpers
# ---------------------------------------------------------------------------

def _is_finite(value: Any) -> bool:
    """Return True if value is a finite number (not NaN, not inf, not None, not empty string)."""
    if value is None:
        return False
    if isinstance(value, str):
        if value.strip() == "":
            return False
        try:
            v = float(value)
        except (ValueError, TypeError):
            return False
        return math.isfinite(v)
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    return False


def _to_float(value: Any) -> float | None:
    """Convert value to float, returning None if non-finite."""
    if not _is_finite(value):
        return None
    return float(value)


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_cost_sensitivity(
    groups: list[dict],
    all_in_cost_bps: float = 50.0,
    fee_bps: float = 40.0,
    slippage_bps: float = 5.0,
    quote_mismatch_buffer_bps: float = 5.0,
    cost_levels_bps: Sequence[float] | None = None,
    min_events: int = 0,
) -> CostSensitivitySummary:
    """Compute cost-sensitivity metrics for a list of evaluated groups.

    groups: list of dicts from summary.json results_by_group or summary.csv rows.
    all_in_cost_bps: total cost wall from the original report.
    cost_levels_bps: cost thresholds for margin computation.
    min_events: minimum valid_count to include a row (0 = include all).
    """
    if cost_levels_bps is None:
        cost_levels_bps = list(DEFAULT_COST_LEVELS_BPS)

    if not groups:
        summary = CostSensitivitySummary(
            report_dir="",
            all_in_cost_bps=all_in_cost_bps,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
            cost_levels_bps=list(cost_levels_bps),
            total_groups=0,
            finite_groups=0,
            verdict=_NO_EVALUATED_GROUPS,
            reason="No evaluated groups in report",
        )
        summary.verdict = _NO_EVALUATED_GROUPS
        return summary

    rows: list[dict] = []
    finite_count = 0

    for g in groups:
        vc = int(g.get("valid_count", 0) or 0)
        if min_events > 0 and vc < min_events:
            continue

        raw_bps = _to_float(g.get("mean_raw_bps"))
        median_raw = _to_float(g.get("median_raw_bps"))
        net_bps = _to_float(g.get("mean_net_bps"))
        wr = _to_float(g.get("win_rate"))

        is_finite_row = raw_bps is not None
        if is_finite_row:
            finite_count += 1

        # Breakeven cost = mean_raw_bps (the cost at which net = 0)
        breakeven = raw_bps  # None if raw_bps is None

        # Margins: mean_raw_bps - cost_level
        margins: dict[str, float | None] = {}
        for level in cost_levels_bps:
            # Format level: drop trailing zeros so 50.0 → "50", 0.5 → "0_5"
            level_str = f"{level:g}".replace(".", "_")
            key = f"margin_vs_{level_str}bps"
            if raw_bps is not None:
                margins[key] = round(raw_bps - level, 6)
            else:
                margins[key] = None

        row = {
            "source_venue": g.get("source_venue", ""),
            "target_venue": g.get("target_venue", ""),
            "signal_type": g.get("signal_type", ""),
            "lookback_ms": int(g.get("lookback_ms", 0) or 0),
            "horizon_ms": int(g.get("horizon_ms", 0) or 0),
            "mean_raw_bps": round(raw_bps, 6) if raw_bps is not None else None,
            "median_raw_bps": round(median_raw, 6) if median_raw is not None else None,
            "mean_net_bps": round(net_bps, 6) if net_bps is not None else None,
            "current_all_in_cost_bps": all_in_cost_bps,
            "breakeven_cost_bps": round(breakeven, 6) if breakeven is not None else None,
            "valid_count": vc,
            "win_rate": round(wr, 6) if wr is not None else None,
            "original_candidate": bool(g.get("candidate", False)),
            "cost_margins_bps": margins,
        }
        # Merge all dynamic margin keys into the row top-level for easy access
        row.update(margins)
        rows.append(row)

    if not rows:
        summary = CostSensitivitySummary(
            report_dir="",
            all_in_cost_bps=all_in_cost_bps,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
            quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
            cost_levels_bps=list(cost_levels_bps),
            total_groups=len(groups),
            finite_groups=0,
            verdict=_NO_EVALUATED_GROUPS,
            reason=f"All {len(groups)} groups filtered by min_events={min_events}",
        )
        summary.verdict = _NO_EVALUATED_GROUPS
        return summary

    # Sort: breakeven descending (highest raw edge first), then valid_count descending
    def sort_key(r: dict) -> tuple:
        be = r.get("breakeven_cost_bps")
        # Non-finite rows sort last
        be_key = be if be is not None else float("-inf")
        return (-be_key, -r.get("valid_count", 0))

    rows_sorted = sorted(rows, key=sort_key)

    # Determine verdict
    total = len(rows_sorted)
    verdict = _NO_FINITE_GROUPS if finite_count == 0 else _COST_SENSITIVITY_READY
    reason = ""
    if verdict == _NO_FINITE_GROUPS:
        reason = f"{total} groups but 0 with finite mean_raw_bps"
    elif verdict == _COST_SENSITIVITY_READY:
        reason = f"{finite_count}/{total} groups with finite mean_raw_bps"

    summary = CostSensitivitySummary(
        report_dir="",
        all_in_cost_bps=all_in_cost_bps,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        quote_mismatch_buffer_bps=quote_mismatch_buffer_bps,
        cost_levels_bps=list(cost_levels_bps),
        total_groups=len(groups),
        finite_groups=finite_count,
        verdict=verdict,
        reason=reason,
        rows=rows_sorted,
    )
    summary.verdict = verdict
    return summary


# ---------------------------------------------------------------------------
# Report loading
# ---------------------------------------------------------------------------

def load_report_groups(report_dir: str | Path) -> tuple[list[dict], dict]:
    """Load evaluated groups from a derivatives spot lead-lag report directory.

    Returns (groups, metadata) where metadata contains cost info from summary.json.
    """
    report_path = Path(report_dir)
    groups: list[dict] = []
    meta: dict = {}

    # Try summary.json first (preferred)
    summary_path = report_path / "summary.json"
    if summary_path.exists():
        with open(summary_path) as f:
            data = json.load(f)
        meta["all_in_cost_bps"] = data.get("all_in_cost_bps", 50.0)
        meta["fee_bps"] = data.get("fee_bps", 40.0)
        meta["slippage_bps"] = data.get("slippage_bps", 5.0)
        meta["quote_mismatch_buffer_bps"] = data.get("quote_mismatch_buffer_bps", 5.0)
        meta["verdict"] = data.get("verdict", "")
        meta["capture_mode"] = data.get("capture_mode", "")
        meta["total_signals"] = data.get("total_signals", 0)
        meta["valid_evaluations"] = data.get("valid_evaluations", 0)
        groups = data.get("results_by_group", [])

    if not groups:
        # Fallback: read summary.csv
        csv_path = report_path / "summary.csv"
        if csv_path.exists():
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                groups = list(reader)
            # CSV rows may have string values — convert numerics
            for row in groups:
                for key in ["valid_count", "lookback_ms", "horizon_ms"]:
                    if key in row and row[key]:
                        try:
                            row[key] = int(row[key])
                        except (ValueError, TypeError):
                            pass
                for key in ["mean_raw_bps", "mean_net_bps", "median_net_bps",
                             "win_rate", "baseline_mean_net_bps", "baseline_win_rate"]:
                    if key in row:
                        row[key] = _to_float(row[key])
                if "candidate" in row:
                    row["candidate"] = row["candidate"] in (True, "True", "true", "1")

    return groups, meta


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------

def write_cost_sensitivity_reports(
    summary: CostSensitivitySummary,
    out_dir: str | Path,
) -> None:
    """Write cost_sensitivity_summary.json, cost_sensitivity.csv, cost_sensitivity.md."""
    out_path = Path(out_dir)
    os.makedirs(out_path, exist_ok=True)
    d = summary.to_dict()

    # JSON
    with open(out_path / "cost_sensitivity_summary.json", "w") as f:
        json.dump(d, f, indent=2, default=str)

    # CSV
    if d["rows"]:
        # Build field names dynamically from cost levels
        base_fields = [
            "source_venue", "target_venue", "signal_type",
            "lookback_ms", "horizon_ms",
            "mean_raw_bps", "median_raw_bps", "mean_net_bps",
            "current_all_in_cost_bps", "breakeven_cost_bps",
        ]
        margin_fields = list(d["rows"][0].get("cost_margins_bps", {}).keys()) if d["rows"] else []
        tail_fields = [
            "valid_count", "win_rate", "original_candidate",
        ]
        fieldnames = base_fields + margin_fields + tail_fields
        with open(out_path / "cost_sensitivity.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in d["rows"]:
                writer.writerow({k: row.get(k, "") for k in fieldnames})

    # Markdown
    lines: list[str] = []
    lines.append("# Cost-Sensitivity / Breakeven Diagnostic\n")
    lines.append(f"**Verdict:** {d['verdict']}\n")
    lines.append(f"**Safety mode:** {d['safety_mode']}\n")
    lines.append(f"**Reason:** {d['reason']}\n")
    lines.append(f"**All-in cost (original report):** {d['all_in_cost_bps']} bps "
                 f"(fee={d['fee_bps']}, slippage={d['slippage_bps']}, "
                 f"quote_mismatch_buffer={d['quote_mismatch_buffer_bps']})\n")
    lines.append(f"**Total groups:** {d['total_groups']}\n")
    lines.append(f"**Finite groups:** {d['finite_groups']}\n")
    lines.append(f"**Cost levels:** {', '.join(str(c) for c in d['cost_levels_bps'])} bps\n")

    if d["rows"]:
        # Build dynamic margin column headers from cost levels
        margin_keys = list(d["rows"][0].get("cost_margins_bps", {}).keys())
        margin_headers = " | ".join(k.replace("margin_vs_", "vs ").replace("bps", " bps") for k in margin_keys)
        lines.append("\n## Top Groups by Breakeven Cost (Descending Raw Edge)\n")
        lines.append("| # | Source→Target | Signal/Lookback | Horizon | Raw bps | Net bps | "
                     f"Breakeven bps | {margin_headers} | "
                     "Events | Win Rate | Candidate |\n")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|\n")
        for i, row in enumerate(d["rows"][:30], 1):
            src_tgt = f"{row['source_venue']}→{row['target_venue']}"
            sig_lb = f"{row['signal_type']}/{row['lookback_ms']}ms"
            hz = f"{row['horizon_ms']}ms"
            raw = f"{row['mean_raw_bps']:.3f}" if row.get("mean_raw_bps") is not None else "N/A"
            net = f"{row['mean_net_bps']:.3f}" if row.get("mean_net_bps") is not None else "N/A"
            be = f"{row['breakeven_cost_bps']:.3f}" if row.get("breakeven_cost_bps") is not None else "N/A"
            margin_vals = []
            for mk in margin_keys:
                v = row.get(mk)
                margin_vals.append(f"{v:.3f}" if v is not None else "N/A")
            margin_str = " | ".join(margin_vals)
            vc = str(row["valid_count"])
            wr = f"{row['win_rate']:.3f}" if row.get("win_rate") is not None else "N/A"
            cand = str(row.get("original_candidate", ""))
            lines.append(f"| {i} | {src_tgt} | {sig_lb} | {hz} | {raw} | {net} | {be} | "
                         f"{margin_str} | {vc} | {wr} | {cand} |\n")

    lines.append("\n## Cost-Wall Analysis\n")
    viable_at = {level: 0 for level in d["cost_levels_bps"]}
    for row in d["rows"]:
        raw = row.get("mean_raw_bps")
        if raw is None:
            continue
        for level in d["cost_levels_bps"]:
            if raw > level:
                viable_at[level] += 1
    lines.append("| Cost Level (bps) | Groups Viable | Verdict |\n")
    lines.append("|---|---|---|\n")
    for level in d["cost_levels_bps"]:
        count = viable_at[level]
        status = "Viable" if count > 0 else "Not viable"
        lines.append(f"| {level} | {count} | {status} |\n")

    lines.append("\n## Diagnostic Assessment\n")
    best_row = d["rows"][0] if d["rows"] else None
    if best_row:
        best_raw = best_row.get("mean_raw_bps")
        best_be = best_row.get("breakeven_cost_bps")
        lines.append(f"- **Best raw edge:** {best_raw:.3f} bps\n" if best_raw is not None else "- **Best raw edge:** N/A\n")
        lines.append(f"- **Breakeven cost required:** {best_be:.3f} bps\n" if best_be is not None else "- **Breakeven cost required:** N/A\n")

        # Determine whether it's a cost-wall or signal-absent problem
        if best_raw is not None and best_raw < 1.0:
            lines.append("- **Diagnosis:** Signal-absent problem — best raw edge is below 1 bps, "
                         "indicating the directional signal magnitude is insufficient even at zero cost.\n")
        elif best_raw is not None and best_raw < 50.0:
            lines.append(f"- **Diagnosis:** Cost-wall problem — raw edge of {best_raw:.1f} bps exists "
                         f"but cannot overcome the {d['all_in_cost_bps']:.0f} bps cost wall. "
                         f"Breakeven cost is {best_be:.1f} bps.\n")
        elif best_raw is not None:
            lines.append(f"- **Diagnosis:** Potential viability — raw edge of {best_raw:.1f} bps "
                         f"exceeds the {d['all_in_cost_bps']:.0f} bps cost wall.\n")
    else:
        lines.append("- No groups to analyze.\n")

    lines.append(f"\n**This is a diagnostic report only.** It does not create candidates, "
                 f"change verdicts, or update registries. "
                 f"Allowed verdicts: {sorted(_ALLOWED_VERDICTS)}.\n")

    with open(out_path / "cost_sensitivity.md", "w") as f:
        f.writelines(lines)