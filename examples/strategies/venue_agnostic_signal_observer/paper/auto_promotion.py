"""Auto-promotion orchestration — evaluates frozen gate and five gates,
writes ledger events, and saves promoted strategies to the registry.

The auto-promotion module owns audit ledger events.  The gate verifier
is side-effect-light and does not write ledger events.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..conductor.atomic_io import append_jsonl_durable, sha256_canonical_json
from .gate_verifier import (
    FrozenGateDecision,
    GateResult,
    check_promotion_frozen,
    verify_promotion_gates,
)
from .models import PaperExecutionMode, PaperStrategySpec, PaperStrategyState
from .registry import load_strategy, save_strategy


@dataclass(frozen=True)
class PromotionDecision:
    strategy_id: str
    allowed: bool
    reason: str
    frozen_gate: FrozenGateDecision
    gate_results: list[GateResult]
    strategy_spec: PaperStrategySpec | None
    event_hash: str | None


def _write_paper_event(
    paper_events_ledger_path: Path,
    event_type: str,
    strategy_id: str,
    precommitment_hash: str,
    reason: str,
    gate_results: list[GateResult] | None = None,
) -> str:
    """Write a paper events ledger event and return the event_hash."""
    payload: dict[str, Any] = {
        "event_type": event_type,
        "strategy_id": strategy_id,
        "precommitment_hash": precommitment_hash,
        "reason": reason,
        "created_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if gate_results is not None:
        payload["gate_results"] = [
            {"gate_id": g.gate_id, "passed": g.passed, "detail": g.detail}
            for g in gate_results
        ]
    event_hash = sha256_canonical_json(payload)
    payload["event_hash"] = event_hash
    append_jsonl_durable(paper_events_ledger_path, payload)
    return event_hash


def evaluate_promotion(
    precommitment_hash: str,
    precommitment_dir: Path,
    registry_dir: Path,
    paper_events_ledger_path: Path,
    artifacts_base_dir: Path,
    evidence_ledger_path: Path,
) -> PromotionDecision:
    """Evaluate a precommitment for paper promotion.

    Returns a PromotionDecision with the result of the full promotion check.
    """
    strategy_id = f"paper_{precommitment_hash[:12]}"

    # Check if strategy already exists
    existing = load_strategy(strategy_id, registry_dir)
    if existing is not None and existing.state in (
        PaperStrategyState.ENABLED,
        PaperStrategyState.KILLED,
        PaperStrategyState.EXPIRED,
    ):
        return PromotionDecision(
            strategy_id=strategy_id,
            allowed=False,
            reason=f"STRATEGY_ALREADY_{existing.state.name}",
            frozen_gate=FrozenGateDecision.frozen(),
            gate_results=[],
            strategy_spec=existing,
            event_hash=None,
        )

    # Check frozen switch first
    frozen = check_promotion_frozen(precommitment_hash, precommitment_dir)
    if not frozen.allowed:
        event_hash = _write_paper_event(
            paper_events_ledger_path,
            "AUTO_PAPER_PROMOTION_BLOCKED_BY_FREEZE_SWITCH",
            strategy_id,
            precommitment_hash,
            f"Frozen gate blocked: {frozen.status()}",
        )
        return PromotionDecision(
            strategy_id=strategy_id,
            allowed=False,
            reason=f"FROZEN_GATE_BLOCKED: {frozen.status()}",
            frozen_gate=frozen,
            gate_results=[],
            strategy_spec=None,
            event_hash=event_hash,
        )

    # Run five promotion gates
    artifacts_dir = artifacts_base_dir
    gate_results = verify_promotion_gates(
        precommitment_hash=precommitment_hash,
        ledger_path=evidence_ledger_path,
        artifacts_dir=artifacts_dir,
    )

    all_passed = all(g.passed for g in gate_results)

    if all_passed:
        # Build strategy spec from precommitment
        from ..conductor.atomic_io import read_json

        precommitment_path = precommitment_dir / f"{precommitment_hash}.json"
        try:
            precommitment_payload = read_json(precommitment_path)
        except Exception:
            precommitment_payload = {}

        group_payload = precommitment_payload.get("group_payload", {})
        source_job_spec = precommitment_payload.get("source_job_spec", {})

        spec = PaperStrategySpec(
            strategy_id=strategy_id,
            signal_family=source_job_spec.get("signal_family", "unknown"),
            study_id=source_job_spec.get("study_id", "unknown"),
            precommitment_hash=precommitment_hash,
            precommitment_path=precommitment_path,
            promotion_rule_id=precommitment_payload.get(
                "promotion_rule_id", "unknown"
            ),
            execution_mode=PaperExecutionMode.NAUTILUS_BACKTEST_SIMULATED,
            group_id=precommitment_payload.get("group_id", "unknown"),
            mean_net_bps=float(
                group_payload.get("mean_net_bps", precommitment_payload.get("mean_net_bps", 0.0))
            ),
            valid_count=int(
                group_payload.get("valid_count", precommitment_payload.get("valid_count", 0))
            ),
            win_rate=group_payload.get("win_rate", precommitment_payload.get("win_rate")),
            cost_floor_bps=float(
                precommitment_payload.get("cost_floor_bps", 50.0)
            ),
            min_events=int(precommitment_payload.get("min_events", 50)),
            source_venue=source_job_spec.get("source_venue", group_payload.get("source_venue")),
            target_venue=source_job_spec.get("target_venue", group_payload.get("target_venue")),
            source_symbol=source_job_spec.get("source_symbol", group_payload.get("source_symbol")),
            target_symbol=source_job_spec.get("target_symbol", group_payload.get("target_symbol")),
            command=tuple(source_job_spec.get("command", [])),
            capture_dir=source_job_spec.get("capture_dir"),
            artifacts_dir=artifacts_dir,
            output_dir=artifacts_dir,
            promoter_verdict="PAPER_STRATEGY_PROMOTED",
            promoted_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            last_refalsified_utc=None,
            refalsification_status=None,
            state=PaperStrategyState.ENABLED,
            metadata={
                "precommitment_payload": precommitment_payload,
            },
        )

        save_strategy(spec, registry_dir)
        event_hash = _write_paper_event(
            paper_events_ledger_path,
            "PAPER_STRATEGY_PROMOTED",
            strategy_id,
            precommitment_hash,
            "All five promotion gates passed",
            gate_results=gate_results,
        )
        return PromotionDecision(
            strategy_id=strategy_id,
            allowed=True,
            reason="ALL_GATES_PASSED",
            frozen_gate=frozen,
            gate_results=gate_results,
            strategy_spec=spec,
            event_hash=event_hash,
        )
    else:
        event_hash = _write_paper_event(
            paper_events_ledger_path,
            "PAPER_PROMOTION_BLOCKED_BY_GATE",
            strategy_id,
            precommitment_hash,
            "One or more promotion gates failed",
            gate_results=gate_results,
        )
        first_failed = next((g for g in gate_results if not g.passed), None)
        reason = (
            f"GATE_FAILED: {first_failed.gate_id} - {first_failed.detail}"
            if first_failed
            else "MULTIPLE_GATES_FAILED"
        )
        return PromotionDecision(
            strategy_id=strategy_id,
            allowed=False,
            reason=reason,
            frozen_gate=frozen,
            gate_results=gate_results,
            strategy_spec=None,
            event_hash=event_hash,
        )