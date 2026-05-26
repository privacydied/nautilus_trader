"""Promotion scanner — identifies promotable groups and generates precommitments.

This module scans evaluation summaries, applies the promotion rule defined in
conductor.policy, freezes precommitment hashes, and returns locked job specs
for any group that qualifies.
"""

from __future__ import annotations

import datetime
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .atomic_io import sha256_canonical_json, write_json_atomic
from .models import (
    ConductorJobSpec,
    ConductorJobStatus,
    ConductorRunMode,
    PromotionCandidate,
)
from .policy import PROMOTION_RULE_ID, is_promotable_group

PROMOTION_SUMMARY_SHAPE_UNKNOWN = "PROMOTION_SUMMARY_SHAPE_UNKNOWN"


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return ""


def _extract_group_id(group: Mapping[str, Any]) -> str:
    group_id = group.get("group_id")
    if group_id and isinstance(group_id, str):
        return group_id
    if group_id is not None:
        return str(group_id)
    # Deterministic hash of the full group payload as fallback
    raw = json.dumps(group, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    import hashlib

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _extract_valid_count(group: Mapping[str, Any]) -> int | None:
    for key in ("valid_count", "n", "events"):
        val = group.get(key)
        if isinstance(val, int):
            return val
    return None


def _find_summary_groups(
    summary: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], str | None] | None:
    """Find groups in the summary.  Returns (groups, shape_marker) or None."""
    if "groups" in summary and isinstance(summary["groups"], list):
        return summary["groups"], "groups"
    if "evaluated_groups" in summary and isinstance(
        summary["evaluated_groups"], list
    ):
        return summary["evaluated_groups"], "evaluated_groups"
    if "results" in summary:
        # Explicitly not supported in v0 — results shape might contain group-like
        # entries in future versions, but v0 should not silently accept them.
        return None
    return None


def _build_locked_output_path(exploration_output_dir: str, job: ConductorJobSpec) -> str:
    """Replace only the first occurrence of 'exploration' in Path.parts with 'locked'.

    Preserves all other path parts exactly, including later parts that may
    contain text like 'exploration_run_2026'.
    """
    parts = list(Path(exploration_output_dir).parts)
    for i, p in enumerate(parts):
        if p == "exploration":
            parts[i] = "locked"
            break
    return str(Path(*parts))


def scan_summary_for_promotions(
    job: ConductorJobSpec,
    summary_path: Path,
    precommitment_out_dir: Path,
) -> list[PromotionCandidate]:
    """Scan an evaluation summary for promotable groups.

    For each group that satisfies is_promotable_group, a precommitment is
    frozen and a locked ConductorJobSpec is created.

    If the summary has an unknown shape, returns [] with an unknown-shape
    marker in the metadata.
    """
    if not summary_path.is_file():
        return []

    with open(summary_path, "r") as f:
        try:
            summary: dict[str, Any] = json.load(f)
        except json.JSONDecodeError:
            return []

    found = _find_summary_groups(summary)
    if found is None:
        return []  # unknown shape (including top-level "results")
    groups, shape_marker = found

    candidates: list[PromotionCandidate] = []
    git_sha = _git_sha()

    for group in groups:
        if not isinstance(group, dict):
            continue

        mean_net_bps = group.get("mean_net_bps")
        if not isinstance(mean_net_bps, (int, float)):
            continue

        valid_count = _extract_valid_count(group)
        if valid_count is None:
            continue

        if not is_promotable_group(
            mean_net_bps=float(mean_net_bps),
            valid_count=valid_count,
            min_events=job.min_events,
        ):
            continue

        group_id = _extract_group_id(group)
        win_rate = group.get("win_rate")
        if win_rate is not None and not isinstance(win_rate, (int, float)):
            win_rate = None

        # Build hash material (no timestamp)
        group_payload_canonical = json.dumps(
            group, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        hash_material: dict[str, Any] = {
            "source_job_spec": {
                "signal_family": job.signal_family,
                "study_id": job.study_id,
                "command": list(job.command),
                "capture_dir": job.capture_dir,
                "run_mode": job.run_mode.name,
            },
            "group_payload": group_payload_canonical,
            "promotion_rule_id": PROMOTION_RULE_ID,
            "cost_floor_bps": job.cost_floor_bps,
            "min_events": job.min_events,
            "git_sha": git_sha,
        }
        precommitment_hash = sha256_canonical_json(hash_material)

        precommitment_payload: dict[str, Any] = {
            "schema_version": "1",
            "promotion_rule_id": PROMOTION_RULE_ID,
            "source_job_spec": {
                "signal_family": job.signal_family,
                "study_id": job.study_id,
                "command": list(job.command),
                "capture_dir": job.capture_dir,
                "run_mode": job.run_mode.name,
            },
            "group_payload": group,
            "group_id": group_id,
            "mean_net_bps": float(mean_net_bps),
            "valid_count": valid_count,
            "win_rate": win_rate,
            "cost_floor_bps": job.cost_floor_bps,
            "min_events": job.min_events,
            "git_sha": git_sha,
            "created_at_utc": datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat(),
            "hash_material": hash_material,
            "precommitment_hash": precommitment_hash,
            "note": "auto-precommit generated before verdict-bearing locked run",
            "registry_verdict_authorized": False,
            "ledger_write_authorized_before_locked_run": False,
        }

        precommitment_out_dir = Path(precommitment_out_dir)
        precommitment_path = precommitment_out_dir / f"{precommitment_hash}.json"

        write_json_atomic(precommitment_path, precommitment_payload)

        # Build locked job spec
        locked_output_dir = _build_locked_output_path(job.output_dir, job)

        locked_job = ConductorJobSpec(
            job_id=job.job_id + "_locked",
            source_kind=job.source_kind,
            signal_family=job.signal_family,
            study_id=job.study_id,
            command=job.command,
            capture_dir=job.capture_dir,
            report_dir=job.report_dir,
            output_dir=locked_output_dir,
            run_mode=ConductorRunMode.LOCKED,
            min_events=job.min_events,
            cost_floor_bps=job.cost_floor_bps,
            structural_change_rationale=job.structural_change_rationale,
            requested_devices=job.requested_devices,
            metadata=dict(job.metadata)
            | {
                "precommitment_hash": precommitment_hash,
                "precommitment_path": str(precommitment_path),
                "promoted_from_job_id": job.job_id,
                "promotion_group_id": group_id,
                "promotion_rule_id": PROMOTION_RULE_ID,
            },
        )

        candidate = PromotionCandidate(
            source_job_id=job.job_id,
            signal_family=job.signal_family,
            study_id=job.study_id,
            group_id=group_id,
            mean_net_bps=float(mean_net_bps),
            valid_count=valid_count,
            win_rate=win_rate,
            summary_path=str(summary_path),
            precommitment_hash=precommitment_hash,
            precommitment_path=str(precommitment_path),
            locked_job_spec=locked_job,
        )
        candidates.append(candidate)

    return candidates