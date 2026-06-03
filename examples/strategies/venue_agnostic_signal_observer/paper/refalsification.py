"""Rolling re-falsification for paper strategies.

Automatically disables paper strategies when fresh evidence no longer
supports the original promotion decision.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..conductor.atomic_io import (
    append_jsonl_durable,
    read_json,
    sha256_canonical_json,
)
from .models import PaperStrategySpec, PaperStrategyState
from .registry import load_all_strategies, update_strategy_state


@dataclass(frozen=True)
class RefalsificationConfig:
    registry_dir: Path
    ledger_path: Path
    pnl_ledger_path: Path
    artifacts_root: Path
    min_age_hours_before_refalsification: int = 24
    refalsification_interval_hours: int = 168
    disable_on_gate_failure: bool = True
    disable_on_cost_wall: bool = True
    disable_on_negative_cross_capture_median: bool = True
    disable_on_daily_loss_killed: bool = True


@dataclass(frozen=True)
class RefalsificationDecision:
    strategy_id: str
    should_disable: bool
    reason: str
    gate_results: Mapping[str, bool]
    evidence: Mapping[str, Any]
    event_hash: str | None


def _find_latest_artifact(artifacts_root: Path, study_id: str) -> Path | None:
    """Find the latest artifact directory for a given study_id under artifacts_root.

    Matches directories whose name is exactly the study_id (case-insensitive).
    Does not use substring, prefix, or suffix matching.
    """
    if not artifacts_root.is_dir():
        return None
    candidates = []
    study_lower = study_id.lower()
    for child in artifacts_root.iterdir():
        if not child.is_dir():
            continue
        # Exact match only — no substring, prefix, or suffix matching
        if child.name.lower() == study_lower:
            candidates.append(child)
    if not candidates:
        return None
    # Return the most recently modified
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _load_artifact(path: Path | None, filename: str) -> dict[str, Any] | None:
    if path is None:
        return None
    filepath = path / filename
    if not filepath.is_file():
        return None
    try:
        return read_json(filepath)
    except Exception:
        return None


def refalsify_strategy(
    *,
    strategy: PaperStrategySpec,
    config: RefalsificationConfig,
    now_utc: datetime.datetime,
) -> RefalsificationDecision:
    """Re-falsify a single paper strategy against current evidence.

    Fail-closed: if required artifacts cannot be located, strategy is disabled.
    """
    signal_family = strategy.signal_family
    study_id = strategy.study_id
    group_id = strategy.group_id

    artifact_dir = _find_latest_artifact(config.artifacts_root, study_id)

    gate_results: dict[str, bool] = {}
    evidence: dict[str, Any] = {}

    # Fail-closed: missing artifact dir
    if artifact_dir is None:
        return RefalsificationDecision(
            strategy_id=strategy.strategy_id,
            should_disable=True,
            reason="REFALSIFICATION_ARTIFACT_MISSING",
            gate_results={},
            evidence={
                "error": f"No artifact directory found for {study_id}"
            },
            event_hash=None,
        )

    # Load current null results
    null_data = _load_artifact(artifact_dir, "null_test_results.json")
    if null_data is None:
        gate_results["null_test"] = False
        evidence["null_test"] = "MISSING"
    else:
        rejected = null_data.get("null_rejected_groups", [])
        group_rejected = any(
            g.get("group_id") == group_id for g in rejected
        ) if isinstance(rejected, list) else False
        gate_results["null_test"] = not group_rejected
        evidence["null_test"] = null_data

    # Load current FDR/holdout results
    stage2_data = _load_artifact(artifact_dir, "stage2_check_results.json")
    if stage2_data is None:
        gate_results["fdr_test"] = False
        gate_results["holdout_test"] = False
        evidence["fdr_holdout"] = "MISSING"
    else:
        gate_results["fdr_test"] = not stage2_data.get("fdr_blocked", False)
        gate_results["holdout_test"] = not stage2_data.get("holdout_blocked", False)
        evidence["fdr_holdout"] = stage2_data

    # Load cross-capture consistency results
    cross_capture = _load_artifact(artifact_dir, "cross_capture_results.json")
    if cross_capture is None:
        cross_capture = _load_artifact(artifact_dir, "cross_capture_consistency.json")
    if cross_capture is not None:
        groups = cross_capture.get("groups") or cross_capture.get("evaluated_groups") or []
        group_data = None
        for g in groups:
            if isinstance(g, dict) and g.get("group_id") == group_id:
                group_data = g
                break
        if group_data is not None:
            median_net = group_data.get("median_net_bps", group_data.get("mean_net_bps"))
            evidence["cross_capture_median_net_bps"] = median_net
            if isinstance(median_net, (int, float)):
                if config.disable_on_cost_wall and median_net <= strategy.cost_floor_bps:
                    gate_results["cross_capture_median"] = False
                    evidence[
                        "cross_capture_cost_wall"
                    ] = f"median_net_bps={median_net} <= cost_floor_bps={strategy.cost_floor_bps}"
                elif config.disable_on_negative_cross_capture_median and median_net < 0:
                    gate_results["cross_capture_median"] = False
                    evidence[
                        "cross_capture_negative"
                    ] = f"median_net_bps={median_net} < 0"
                else:
                    gate_results["cross_capture_median"] = True
            else:
                gate_results["cross_capture_median"] = False
                evidence["cross_capture_median"] = f"Non-numeric: {median_net!r}"
        else:
            gate_results["cross_capture_median"] = False
            evidence["cross_capture_group_missing"] = f"Group {group_id} not found in cross-capture"
    else:
        gate_results["cross_capture_median"] = False
        evidence["cross_capture_consistency"] = "MISSING"

    # Check daily loss killed
    pnl_path = config.pnl_ledger_path
    daily_loss_killed = False
    if config.disable_on_daily_loss_killed and pnl_path.is_file():
        try:
            with open(pnl_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (
                        entry.get("strategy_id") == strategy.strategy_id
                        and entry.get("event_type") == "DAILY_LOSS_KILLED"
                    ):
                        daily_loss_killed = True
                        evidence["daily_loss_killed"] = True
                        break
        except OSError:
            pass

    should_disable = False
    reasons: list[str] = []

    if config.disable_on_gate_failure:
        for gate_name, passed in gate_results.items():
            if not passed:
                should_disable = True
                reasons.append(f"GATE_FAILED:{gate_name}")

    if daily_loss_killed:
        should_disable = True
        reasons.append("DAILY_LOSS_KILLED")

    reason_str = "; ".join(reasons) if reasons else "ALL_GATES_PASSED"

    # Build event hash
    event_payload: dict[str, Any] = {
        "event_type": "PAPER_STRATEGY_DISABLED_REFALSIFICATION",
        "strategy_id": strategy.strategy_id,
        "paper_precommitment_hash": strategy.precommitment_hash,
        "reason": reason_str,
        "gate_results": gate_results,
        "evidence": evidence,
    }
    event_hash = sha256_canonical_json(event_payload)

    return RefalsificationDecision(
        strategy_id=strategy.strategy_id,
        should_disable=should_disable,
        reason=reason_str,
        gate_results=gate_results,
        evidence=evidence,
        event_hash=event_hash,
    )


def run_refalsification_once(
    config: RefalsificationConfig,
    now_utc: datetime | None = None,
    dry_run: bool = False,
) -> list[RefalsificationDecision]:
    """Run one refalsification pass against all eligible strategies.

    Returns a list of RefalsificationDecision, one per checked strategy.

    If *dry_run* is True, decisions are returned but registry state and
    ledger are not modified.
    """
    if now_utc is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)

    strategies = load_all_strategies(config.registry_dir)
    decisions: list[RefalsificationDecision] = []

    for strategy in strategies:
        # Skip strategies not in ENABLED state
        if strategy.state in (
            PaperStrategyState.DISABLED,
            PaperStrategyState.KILLED,
            PaperStrategyState.EXPIRED,
        ):
            continue

        # Check min age
        if strategy.promoted_at_utc:
            try:
                promoted_dt = datetime.datetime.fromisoformat(strategy.promoted_at_utc)
                if promoted_dt.tzinfo is None:
                    promoted_dt = promoted_dt.replace(tzinfo=datetime.timezone.utc)
                age_hours = (now_utc - promoted_dt).total_seconds() / 3600
                if age_hours < config.min_age_hours_before_refalsification:
                    continue
            except (ValueError, TypeError):
                pass

        # Check interval
        if strategy.last_refalsified_utc:
            try:
                last_dt = datetime.datetime.fromisoformat(
                    strategy.last_refalsified_utc
                )
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=datetime.timezone.utc)
                hours_since = (now_utc - last_dt).total_seconds() / 3600
                if hours_since < config.refalsification_interval_hours:
                    continue
            except (ValueError, TypeError):
                pass

        decision = refalsify_strategy(
            strategy=strategy,
            config=config,
            now_utc=now_utc,
        )

        if decision.should_disable and not dry_run:
            update_strategy_state(
                strategy.strategy_id,
                PaperStrategyState.DISABLED,
                config.registry_dir,
            )
            # Write ledger event
            event_payload: dict[str, Any] = {
                "event_type": "PAPER_STRATEGY_DISABLED_REFALSIFICATION",
                "strategy_id": strategy.strategy_id,
                "paper_precommitment_hash": strategy.precommitment_hash,
                "reason": decision.reason,
                "gate_results": decision.gate_results,
                "evidence": decision.evidence,
                "created_at_utc": now_utc.isoformat(),
            }
            event_hash = sha256_canonical_json(event_payload)
            event_payload["event_hash"] = event_hash
            append_jsonl_durable(config.ledger_path, event_payload)

        decisions.append(decision)

    return decisions