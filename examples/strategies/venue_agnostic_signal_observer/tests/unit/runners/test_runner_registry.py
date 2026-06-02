from __future__ import annotations

from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.hypotheses.base import (
    ArtifactRef,
    HypothesisSpec,
    RunContext,
    RunResult,
)
from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
    clear_runner_registry_for_tests,
    get_runner,
    list_runners,
    register_runner,
)


class FakeRunner:
    def __init__(self, study_id: str) -> None:
        self.spec = HypothesisSpec(
            study_id=study_id,
            family="family.alpha",
            venue_scope=("hyperliquid",),
            phase="phase0",
            precommitment_path=None,
            artifact_schema_version="v0",
        )

    def run(self, context: RunContext) -> RunResult:
        artifact = ArtifactRef(kind="summary", path=context.reports_root / "summary.json", schema_version="v0")
        return RunResult(study_id=self.spec.study_id, status="ok", artifacts=(artifact,))


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    clear_runner_registry_for_tests()


def test_runner_registry_rejects_duplicate_names() -> None:
    register_runner("alpha", FakeRunner("study.alpha"))
    with pytest.raises(ValueError, match="Duplicate runner name"):
        register_runner("alpha", FakeRunner("study.beta"))


def test_runner_registry_rejects_duplicate_study_ids() -> None:
    register_runner("alpha", FakeRunner("study.alpha"))
    with pytest.raises(ValueError, match="Duplicate study_id"):
        register_runner("beta", FakeRunner("study.alpha"))


def test_runner_registry_returns_sorted_runners() -> None:
    register_runner("zeta", FakeRunner("study.zeta"))
    register_runner("alpha", FakeRunner("study.alpha"))
    register_runner("middle", FakeRunner("study.middle"))

    assert [runner.name for runner in list_runners()] == ["alpha", "middle", "zeta"]


def test_unknown_runner_lookup_raises_key_error() -> None:
    with pytest.raises(KeyError, match="Unknown runner"):
        get_runner("missing")


def test_registered_runner_has_non_promoting_spec() -> None:
    registered = register_runner("alpha", FakeRunner("study.alpha"))
    assert registered.study_id == "study.alpha"
    assert registered.spec.allows_paper_promotion is False


def test_get_runner_returns_registered_entry() -> None:
    register_runner("alpha", FakeRunner("study.alpha"))
    registered = get_runner("alpha")
    result = registered.runner.run(
        RunContext(
            repo_root=Path("/repo"),
            data_root=Path("/repo/data"),
            reports_root=Path("/repo/reports"),
        )
    )
    assert result.study_id == "study.alpha"
    assert result.artifacts[0].schema_version == "v0"
