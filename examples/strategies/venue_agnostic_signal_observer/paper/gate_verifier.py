"""Gate verifier for paper promotion.

Checks the frozen switch and five promotion gates. Side-effect-light:
does not write ledger events or precommitment files.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..conductor.atomic_io import read_json


@dataclass(frozen=True)
class FrozenGateDecision:
    allowed: bool
    error: str | None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def frozen() -> FrozenGateDecision:
        return FrozenGateDecision(
            allowed=True,
            error=None,
            metadata={"status": "PROMOTION_FROZEN"},
        )

    @staticmethod
    def failed(error: str) -> FrozenGateDecision:
        return FrozenGateDecision(allowed=False, error=error)

    def status(self) -> str:
        if self.allowed:
            return "PROMOTION_FROZEN"
        if self.error:
            return self.error
        return "UNKNOWN"


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    passed: bool
    detail: str
    evidence_path: Path | None


def check_promotion_frozen(
    precommitment_hash: str, precommitment_dir: Path
) -> FrozenGateDecision:
    """Check whether the precommitment has survived a locked run.

    Reads the precommitment file and verifies that both
    ``ledger_write_authorized_before_locked_run`` and
    ``registry_verdict_authorized`` are ``True``.

    If not frozen, returns a ``FAILED_*`` FrozenGateDecision immediately.
    The verifier does not read artifacts, write precommitments, or write
    ledger events.
    """
    precommitment_path = (
        Path(precommitment_dir) / f"{precommitment_hash}.json"
    )
    if not precommitment_path.is_file():
        return FrozenGateDecision.failed("FAILED_MISSING_PRECOMMITMENT")

    try:
        payload = read_json(precommitment_path)
    except (json.JSONDecodeError, OSError):
        return FrozenGateDecision.failed("FAILED_MISSING_PRECOMMITMENT")

    if payload.get("ledger_write_authorized_before_locked_run") is not True:
        return FrozenGateDecision.failed("FAILED_EVIDENCE_GAP")

    if payload.get("registry_verdict_authorized") is not True:
        return FrozenGateDecision.failed("FAILED_VERDICT_NOT_AUTHORIZED")

    return FrozenGateDecision.frozen()


def verify_promotion_gates(
    precommitment_hash: str,
    ledger_path: Path,
    artifacts_dir: Path,
) -> list[GateResult]:
    """Run the five promotion gates against available evidence.

    Returns a list of GateResult, one per gate.  The caller aggregates
    them into a final promotion decision.

    The verifier does not write ledger events or precommitments.
    """
    results: list[GateResult] = []
    artifacts_dir = Path(artifacts_dir)

    # Gate 1: Locked-run ledger event exists
    gate1 = _check_ledger_locked_run(precommitment_hash, ledger_path)
    results.append(gate1)

    # Gate 2: Artifacts exist (summary.json + conductor_result.json)
    gate2 = _check_artifact_exists(artifacts_dir)
    results.append(gate2)

    # Gate 3: Group survives in locked-run summary
    gate3 = _check_group_survives(artifacts_dir, precommitment_hash)
    results.append(gate3)

    # Gate 4: Null test (optional)
    gate4 = _check_null_test(artifacts_dir)
    results.append(gate4)

    # Gate 5: Stage 2 rejection (optional)
    gate5 = _check_stage2_rejection(artifacts_dir)
    results.append(gate5)

    return results


def _check_ledger_locked_run(
    precommitment_hash: str, ledger_path: Path
) -> GateResult:
    if not ledger_path.is_file():
        return GateResult(
            gate_id="ledger_locked_run_completed",
            passed=False,
            detail="LEDGER_FILE_MISSING",
            evidence_path=None,
        )
    try:
        with open(ledger_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    event.get("event_type") == "CONDUCTOR_LOCKED_RUN_COMPLETED"
                    and event.get("precommitment_hash") == precommitment_hash
                ):
                    return GateResult(
                        gate_id="ledger_locked_run_completed",
                        passed=True,
                        detail="LOCKED_RUN_EVENT_FOUND",
                        evidence_path=ledger_path,
                    )
    except OSError:
        pass
    return GateResult(
        gate_id="ledger_locked_run_completed",
        passed=False,
        detail="LOCKED_RUN_EVENT_NOT_FOUND",
        evidence_path=ledger_path,
    )


def _check_artifact_exists(artifacts_dir: Path) -> GateResult:
    summary = artifacts_dir / "summary.json"
    conductor_result = artifacts_dir / "conductor_result.json"
    if summary.is_file() and conductor_result.is_file():
        return GateResult(
            gate_id="artifact_exists",
            passed=True,
            detail="SUMMARY_AND_CONDUCTOR_RESULT_FOUND",
            evidence_path=summary,
        )
    missing = []
    if not summary.is_file():
        missing.append("summary.json")
    if not conductor_result.is_file():
        missing.append("conductor_result.json")
    return GateResult(
        gate_id="artifact_exists",
        passed=False,
        detail=f"ARTIFACTS_MISSING: {', '.join(missing)}",
        evidence_path=artifacts_dir,
    )


def _check_group_survives(
    artifacts_dir: Path, precommitment_hash: str
) -> GateResult:
    summary_path = artifacts_dir / "summary.json"
    if not summary_path.is_file():
        return GateResult(
            gate_id="group_survives_in_locked_run",
            passed=False,
            detail="SUMMARY_FILE_MISSING",
            evidence_path=None,
        )
    try:
        summary = read_json(summary_path)
    except (json.JSONDecodeError, OSError):
        return GateResult(
            gate_id="group_survives_in_locked_run",
            passed=False,
            detail="SUMMARY_PARSE_ERROR",
            evidence_path=summary_path,
        )

    # Try to find the precommitment's group in the summary
    # Look in groups or evaluated_groups
    groups = summary.get("groups") or summary.get("evaluated_groups") or []
    for group in groups:
        if not isinstance(group, dict):
            continue
        mean_net = group.get("mean_net_bps")
        if isinstance(mean_net, (int, float)) and mean_net > 0:
            return GateResult(
                gate_id="group_survives_in_locked_run",
                passed=True,
                detail=f"GROUP_POSITIVE mean_net_bps={mean_net}",
                evidence_path=summary_path,
            )

    return GateResult(
        gate_id="group_survives_in_locked_run",
        passed=False,
        detail="NO_POSITIVE_GROUP_IN_LOCKED_RUN",
        evidence_path=summary_path,
    )


def _check_null_test(artifacts_dir: Path) -> GateResult:
    null_path = artifacts_dir / "null_test_results.json"
    if not null_path.is_file():
        return GateResult(
            gate_id="null_not_candidate_for_rejection",
            passed=True,
            detail="SKIPPED_NO_NULL_DATA",
            evidence_path=None,
        )
    try:
        null_data = read_json(null_path)
    except (json.JSONDecodeError, OSError):
        return GateResult(
            gate_id="null_not_candidate_for_rejection",
            passed=False,
            detail="NULL_PARSE_ERROR",
            evidence_path=null_path,
        )
    # Check if any group was rejected by null
    rejected = null_data.get("null_rejected_groups", [])
    if rejected:
        return GateResult(
            gate_id="null_not_candidate_for_rejection",
            passed=False,
            detail=f"NULL_REJECTED_GROUPS: {len(rejected)}",
            evidence_path=null_path,
        )
    return GateResult(
        gate_id="null_not_candidate_for_rejection",
        passed=True,
        detail="NULL_PASSED",
        evidence_path=null_path,
    )


def _check_stage2_rejection(artifacts_dir: Path) -> GateResult:
    stage2_path = artifacts_dir / "stage2_check_results.json"
    if not stage2_path.is_file():
        return GateResult(
            gate_id="no_stage2_rejection",
            passed=True,
            detail="SKIPPED_NO_STAGE2_DATA",
            evidence_path=None,
        )
    try:
        stage2 = read_json(stage2_path)
    except (json.JSONDecodeError, OSError):
        return GateResult(
            gate_id="no_stage2_rejection",
            passed=False,
            detail="STAGE2_PARSE_ERROR",
            evidence_path=stage2_path,
        )
    # Check if any group was blocked by FDR or holdout
    fdr_blocked = stage2.get("fdr_blocked", False)
    holdout_blocked = stage2.get("holdout_blocked", False)
    if fdr_blocked or holdout_blocked:
        reasons = []
        if fdr_blocked:
            reasons.append("FDR_BLOCKED")
        if holdout_blocked:
            reasons.append("HOLDOUT_BLOCKED")
        return GateResult(
            gate_id="no_stage2_rejection",
            passed=False,
            detail=f"STAGE2_REJECTED: {'; '.join(reasons)}",
            evidence_path=stage2_path,
        )
    return GateResult(
        gate_id="no_stage2_rejection",
        passed=True,
        detail="STAGE2_PASSED",
        evidence_path=stage2_path,
    )