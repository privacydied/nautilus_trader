"""Synthetic paper dry-run report generation.

Pure, dependency-light helpers that build a *synthetic* readiness report for
paper-promotion / readiness flows. This module:

* does NOT read real reports, ledgers, or artifacts by default,
* does NOT enable paper promotion or start paper trading,
* does NOT run any strategy,
* writes only to a caller-provided output path.

It exists so callers can render a deterministic "would this be ready for a
paper *simulation*?" summary from already-computed gate/refalsification/policy
booleans, without touching any live or persisted state.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import orjson

#: The only two statuses this module ever emits.
STATUS_READY = "DRY_RUN_READY"
STATUS_BLOCKED = "DRY_RUN_BLOCKED"


@dataclass(frozen=True, slots=True)
class DryRunReportInput:
    strategy_id: str
    registry_key: str
    cli_spec_key: str | None
    gates_passed: bool
    refalsification_passed: bool
    policy_allowed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DryRunReport:
    status: str
    strategy_id: str
    registry_key: str
    cli_spec_key: str | None
    ready_for_paper_simulation: bool
    reasons: tuple[str, ...]


def build_dry_run_report(input: DryRunReportInput) -> DryRunReport:
    """Build a synthetic dry-run report from pre-computed booleans.

    The report is ``DRY_RUN_READY`` only when every gate passed, the
    refalsification pass succeeded, and policy allowed it. In all other cases
    the report is ``DRY_RUN_BLOCKED``. ``ready_for_paper_simulation`` only ever
    signals readiness for a *synthetic simulation*, never live/enabled trading.
    """
    ready = bool(
        input.gates_passed
        and input.refalsification_passed
        and input.policy_allowed
    )
    reasons = tuple(input.reasons)
    if not ready:
        derived: list[str] = []
        if not input.gates_passed:
            derived.append("gates_not_passed")
        if not input.refalsification_passed:
            derived.append("refalsification_not_passed")
        if not input.policy_allowed:
            derived.append("policy_not_allowed")
        # Preserve caller-supplied reasons first, then append derived ones not
        # already present.
        reasons = reasons + tuple(r for r in derived if r not in reasons)

    return DryRunReport(
        status=STATUS_READY if ready else STATUS_BLOCKED,
        strategy_id=input.strategy_id,
        registry_key=input.registry_key,
        cli_spec_key=input.cli_spec_key,
        ready_for_paper_simulation=ready,
        reasons=reasons,
    )


def report_as_dict(report: DryRunReport) -> dict[str, object]:
    """Render the report as a plain JSON-serialisable dict."""
    return {
        "status": report.status,
        "strategy_id": report.strategy_id,
        "registry_key": report.registry_key,
        "cli_spec_key": report.cli_spec_key,
        "ready_for_paper_simulation": report.ready_for_paper_simulation,
        "reasons": list(report.reasons),
    }


def write_dry_run_report(report: DryRunReport, output_path: Path) -> Path:
    """Write the synthetic report as JSON to ``output_path``.

    The parent directory must already exist (the caller owns it — typically a
    pytest ``tmp_path``). This function never creates real ``reports/``,
    ``data/``, or ``.local_data`` locations and fails closed if the parent
    directory is missing.
    """
    output_path = Path(output_path)
    if not output_path.parent.is_dir():
        raise FileNotFoundError(
            f"output parent directory does not exist: {output_path.parent}"
        )
    payload = orjson.dumps(report_as_dict(report), option=orjson.OPT_INDENT_2)
    output_path.write_bytes(payload)
    return output_path
