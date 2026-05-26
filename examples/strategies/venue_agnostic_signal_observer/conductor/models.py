"""Data models for the conductor orchestration layer."""

from __future__ import annotations

import hashlib
import json
import re
import uuid as _uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

from .atomic_io import sha256_canonical_json


class ConductorSourceKind(Enum):
    EXISTING_CAPTURE = auto()
    GATE_WATCHER_TRIGGER = auto()
    ARCHIVE_WINDOW = auto()


class ConductorRunMode(Enum):
    EXPLORATION = auto()
    LOCKED = auto()


class ConductorJobStatus(Enum):
    PENDING = auto()
    SKIPPED_LOCKED_GATE = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    PROMOTED = auto()


_SNAKE_CASE_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class ConductorJobSpec:
    """A frozen specification for one conductor job."""

    job_id: str
    source_kind: ConductorSourceKind
    signal_family: str
    study_id: str
    command: tuple[str, ...]
    capture_dir: str | None
    report_dir: str | None
    output_dir: str
    run_mode: ConductorRunMode
    min_events: int
    cost_floor_bps: float
    structural_change_rationale: str | None
    requested_devices: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.job_id:
            raise ValueError("job_id cannot be empty")
        if not self.signal_family:
            raise ValueError("signal_family cannot be empty")
        if not self.study_id:
            raise ValueError("study_id cannot be empty")
        if not _SNAKE_CASE_RE.match(self.signal_family):
            raise ValueError(
                f"signal_family must be lowercase snake_case: {self.signal_family!r}"
            )
        if not _SNAKE_CASE_RE.match(self.study_id):
            raise ValueError(
                f"study_id must be lowercase snake_case: {self.study_id!r}"
            )
        if not self.command:
            raise ValueError("command cannot be empty")
        if not self.output_dir:
            raise ValueError("output_dir cannot be empty")
        if self.min_events < 1:
            raise ValueError("min_events must be >= 1")
        if self.cost_floor_bps < 0:
            raise ValueError("cost_floor_bps must be >= 0")

        parts = Path(self.output_dir).parts
        if self.run_mode == ConductorRunMode.EXPLORATION:
            if "exploration" not in parts:
                raise ValueError(
                    f"exploration output_dir must contain 'exploration' in Path.parts: "
                    f"{self.output_dir}"
                )
        elif self.run_mode == ConductorRunMode.LOCKED:
            if "exploration" in parts:
                raise ValueError(
                    f"locked output_dir must NOT contain 'exploration' in Path.parts: "
                    f"{self.output_dir}"
                )


@dataclass(frozen=True)
class ConductorJobResult:
    """Result metadata for one executed conductor job."""

    job_id: str
    status: ConductorJobStatus
    returncode: int | None
    started_at_utc: str | None
    finished_at_utc: str | None
    output_dir: str
    summary_path: str | None
    error: str | None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PromotionCandidate:
    """A qualified group that has been frozen into a precommitment."""

    source_job_id: str
    signal_family: str
    study_id: str
    group_id: str
    mean_net_bps: float
    valid_count: int
    win_rate: float | None
    summary_path: str
    precommitment_hash: str
    precommitment_path: str
    locked_job_spec: ConductorJobSpec


def derive_job_id(
    *,
    signal_family: str,
    study_id: str,
    command: Sequence[str],
    capture_dir: str | None,
    run_mode: ConductorRunMode,
) -> str:
    """Deterministic job ID derived from stable job fields.

    Two pollers observing the same capture must produce the same job ID.
    """
    stable_fields: dict[str, Any] = {
        "signal_family": signal_family,
        "study_id": study_id,
        "command": list(command),
        "capture_dir": capture_dir,
        "run_mode": run_mode.name,
    }
    h = sha256_canonical_json(stable_fields)[:12]
    return f"{signal_family}_{study_id}_{h}"