from __future__ import annotations

from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.core.artifacts import sha256_file
from examples.strategies.venue_agnostic_signal_observer.hypotheses.base import ArtifactRef


def test_artifact_contract_schema_version_and_sha256(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text('{"status":"ok"}', encoding="utf-8")

    artifact = ArtifactRef(
        kind="summary",
        path=path,
        schema_version="v0",
        sha256=sha256_file(path),
    )

    assert artifact.schema_version == "v0"
    assert len(artifact.sha256 or "") == 64
