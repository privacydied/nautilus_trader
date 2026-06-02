from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.hypotheses.base import (
    ArtifactRef,
    HypothesisRunner,
    HypothesisSpec,
    RunContext,
    RunResult,
)


def _make_spec() -> HypothesisSpec:
    return HypothesisSpec(
        study_id="study.alpha",
        family="family.alpha",
        venue_scope=("hyperliquid",),
        phase="phase0",
        precommitment_path=Path("precommitments/alpha.md"),
        artifact_schema_version="v0",
    )


def test_hypothesis_dataclasses_are_frozen() -> None:
    spec = _make_spec()
    context = RunContext(
        repo_root=Path("/repo"),
        data_root=Path("/repo/data"),
        reports_root=Path("/repo/reports"),
    )
    artifact = ArtifactRef(kind="summary", path=Path("reports/summary.json"), schema_version="v0")
    result = RunResult(study_id=spec.study_id, status="ok", artifacts=(artifact,))

    with pytest.raises(FrozenInstanceError):
        spec.study_id = "study.beta"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        context.allow_network = True  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        artifact.sha256 = "abc"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.status = "changed"  # type: ignore[misc]


def test_allows_paper_promotion_defaults_false() -> None:
    spec = _make_spec()
    assert spec.allows_paper_promotion is False


def test_artifact_ref_requires_schema_version() -> None:
    with pytest.raises(TypeError):
        ArtifactRef(kind="summary", path=Path("reports/summary.json"))  # type: ignore[call-arg]


class FakeHypothesis:
    spec = _make_spec()

    def run(self, context: RunContext) -> RunResult:
        artifact = ArtifactRef(kind="summary", path=context.reports_root / "summary.json", schema_version="v0")
        return RunResult(study_id=self.spec.study_id, status="ok", artifacts=(artifact,))


def test_fake_runner_satisfies_protocol_shape() -> None:
    runner: HypothesisRunner = FakeHypothesis()
    result = runner.run(
        RunContext(
            repo_root=Path("/repo"),
            data_root=Path("/repo/data"),
            reports_root=Path("/repo/reports"),
        )
    )
    assert result.study_id == runner.spec.study_id
    assert result.artifacts[0].schema_version == "v0"


def test_frozen_dataclass_replace_keeps_defaults() -> None:
    updated = replace(_make_spec(), phase="phase1")
    assert updated.phase == "phase1"
    assert updated.allows_paper_promotion is False
