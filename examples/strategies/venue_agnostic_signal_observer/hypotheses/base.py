from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class HypothesisSpec:
    study_id: str
    family: str
    venue_scope: tuple[str, ...]
    phase: str
    precommitment_path: Path | None
    artifact_schema_version: str
    allows_paper_promotion: bool = False


@dataclass(frozen=True)
class RunContext:
    repo_root: Path
    data_root: Path
    reports_root: Path
    allow_network: bool = False
    allow_s3: bool = False
    devices: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArtifactRef:
    kind: str
    path: Path
    schema_version: str
    sha256: str | None = None


@dataclass(frozen=True)
class RunResult:
    study_id: str
    status: str
    artifacts: tuple[ArtifactRef, ...]


class HypothesisRunner(Protocol):
    spec: HypothesisSpec

    def run(self, context: RunContext) -> RunResult:
        ...
