from __future__ import annotations

from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.hypotheses.base import (
    ArtifactRef,
    HypothesisSpec,
    RunContext,
    RunResult,
)


def test_hypothesis_contract_shape() -> None:
    spec = HypothesisSpec(
        study_id="study.alpha",
        family="family.alpha",
        venue_scope=("hyperliquid", "cex"),
        phase="phase0",
        precommitment_path=Path("precommitments/alpha.md"),
        artifact_schema_version="v0",
    )
    context = RunContext(
        repo_root=Path("/repo"),
        data_root=Path("/repo/data"),
        reports_root=Path("/repo/reports"),
    )
    artifact = ArtifactRef(kind="summary", path=Path("/repo/reports/summary.json"), schema_version="v0")
    result = RunResult(study_id=spec.study_id, status="diagnostic", artifacts=(artifact,))

    assert spec.study_id == "study.alpha"
    assert spec.venue_scope == ("hyperliquid", "cex")
    assert spec.allows_paper_promotion is False
    assert context.allow_network is False
    assert context.allow_s3 is False
    assert result.artifacts[0].schema_version == "v0"
