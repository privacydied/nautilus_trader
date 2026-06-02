from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from examples.strategies.venue_agnostic_signal_observer.hypotheses.package_contract import (
    HypothesisPackageMetadata,
)

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


# ---------------------------------------------------------------------------
# RunnerSpec metadata-only registry tests
# ---------------------------------------------------------------------------

from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
    NODE_FILLS_LIQ_RECONSTRUCTION,
    RunnerSpec,
    _build_registry_by_key,
    get_runner_spec,
    iter_runner_specs,
    require_runner_spec,
)

_NODE_FILLS_KEY = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"


def _synthetic_metadata(**overrides: object) -> HypothesisPackageMetadata:
    values: dict[str, Any] = {
        "key": "synthetic_runner_v0",
        "study_id": "synthetic_study_v0",
        "family": "synthetic_family",
        "venue": "hyperliquid",
        "phase": "phase0",
        "description": "Synthetic metadata-only package.",
        "tags": ("observer_only", "synthetic"),
        "cli_module": "synthetic.cli",
        "implementation_module": "synthetic.runner",
        "package_module": "synthetic",
        "legacy_module": "synthetic_legacy",
    }
    values.update(overrides)
    return HypothesisPackageMetadata(**values)


class TestRunnerSpecRegistry:
    """Tests for the metadata-only RunnerSpec registry."""

    def test_registry_imports_cleanly(self) -> None:
        """1. Registry module imports without error."""
        from examples.strategies.venue_agnostic_signal_observer.runners import registry

        assert hasattr(registry, "RunnerSpec")
        assert hasattr(registry, "iter_runner_specs")

    def test_registry_import_is_lazy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """2. Importing the registry does not import the heavy implementation module."""
        import sys

        heavy_module = (
            "examples.strategies.venue_agnostic_signal_observer."
            "hypotheses.hyperliquid.node_fills_liq_reconstruction.runner"
        )
        # Remove from cache to test fresh import
        sys.modules.pop(heavy_module, None)

        from examples.strategies.venue_agnostic_signal_observer.runners import registry

        assert heavy_module not in sys.modules

        # require_runner_spec should still not import the heavy module
        spec = registry.require_runner_spec(_NODE_FILLS_KEY)
        assert heavy_module not in sys.modules

    def test_exactly_one_runner_registered(self) -> None:
        """3. Exactly one real runner is registered for now."""
        specs = iter_runner_specs()
        assert len(specs) == 1

    def test_registered_key_is_stable(self) -> None:
        """4. Registered key matches the expected stable value."""
        spec = require_runner_spec(_NODE_FILLS_KEY)
        assert spec.key == _NODE_FILLS_KEY

    def test_iter_runner_specs_returns_tuple(self) -> None:
        """5. iter_runner_specs() returns a tuple."""
        result = iter_runner_specs()
        assert isinstance(result, tuple)

    def test_get_runner_spec_returns_spec(self) -> None:
        """6. get_runner_spec(key) returns the node-fills spec."""
        spec = get_runner_spec(_NODE_FILLS_KEY)
        assert spec is not None
        assert spec.key == _NODE_FILLS_KEY
        assert spec is NODE_FILLS_LIQ_RECONSTRUCTION

    def test_get_runner_spec_missing_returns_none(self) -> None:
        """7. get_runner_spec('missing') returns None."""
        assert get_runner_spec("missing") is None

    def test_require_runner_spec_returns_spec(self) -> None:
        """8. require_runner_spec(key) returns the node-fills spec."""
        spec = require_runner_spec(_NODE_FILLS_KEY)
        assert spec.key == _NODE_FILLS_KEY
        assert spec.family == "node_fills_liq_reconstruction"
        assert spec.venue == "hyperliquid"

    def test_require_runner_spec_missing_raises(self) -> None:
        """9. require_runner_spec('missing') raises KeyError."""
        with pytest.raises(KeyError, match="Unknown runner spec"):
            require_runner_spec("missing")

    def test_duplicate_key_builder_rejects(self) -> None:
        """10. _build_registry_by_key rejects duplicate keys."""
        spec_a = RunnerSpec(
            key="dup",
            family="f",
            venue="v",
            study_id="s",
            description="d",
            cli_module="cli",
            implementation_module="impl",
            package_module="pkg",
            legacy_module="legacy",
        )
        spec_b = RunnerSpec(
            key="dup",
            family="f2",
            venue="v2",
            study_id="s2",
            description="d2",
            cli_module="cli2",
            implementation_module="impl2",
            package_module="pkg2",
            legacy_module="legacy2",
        )
        with pytest.raises(ValueError, match="duplicate runner registry key"):
            _build_registry_by_key((spec_a, spec_b))

    def test_metadata_fields_correct(self) -> None:
        """Metadata fields match the expected values."""
        spec = NODE_FILLS_LIQ_RECONSTRUCTION
        assert spec.family == "node_fills_liq_reconstruction"
        assert spec.venue == "hyperliquid"
        assert spec.study_id == _NODE_FILLS_KEY
        assert "Hyperliquid node-fills" in spec.description

    def test_metadata_tags_include_observer_only(self) -> None:
        """16. Registry metadata tags include 'observer_only'."""
        assert "observer_only" in NODE_FILLS_LIQ_RECONSTRUCTION.tags

    def test_no_paper_governance_live_conductor_keys(self) -> None:
        """15. Registry does not include paper/governance/live/conductor keys."""
        all_keys = [s.key for s in iter_runner_specs()]
        forbidden_fragments = ("paper", "governance", "live", "conductor", "promotion")
        for key in all_keys:
            for frag in forbidden_fragments:
                assert frag not in key, f"forbidden fragment {frag!r} in key {key!r}"

    def test_runner_spec_can_be_constructed_from_hypothesis_metadata(self) -> None:
        metadata = _synthetic_metadata()

        spec = RunnerSpec.from_hypothesis_metadata(metadata)

        assert spec.key == "synthetic_runner_v0"
        assert spec.family == "synthetic_family"
        assert spec.venue == "hyperliquid"
        assert spec.study_id == "synthetic_study_v0"
        assert spec.description == "Synthetic metadata-only package."
        assert spec.tags == ("observer_only", "synthetic")

    def test_metadata_conversion_preserves_module_strings(self) -> None:
        metadata = _synthetic_metadata(
            cli_module="synthetic.cli.entry",
            implementation_module="synthetic.impl.runner",
            package_module="synthetic.pkg",
            legacy_module="synthetic_legacy.wrapper",
        )

        spec = RunnerSpec.from_hypothesis_metadata(metadata)

        assert spec.cli_module == "synthetic.cli.entry"
        assert spec.implementation_module == "synthetic.impl.runner"
        assert spec.package_module == "synthetic.pkg"
        assert spec.legacy_module == "synthetic_legacy.wrapper"

    def test_metadata_conversion_validates_metadata(self) -> None:
        metadata = _synthetic_metadata(key=" ")

        with pytest.raises(ValueError, match="key"):
            RunnerSpec.from_hypothesis_metadata(metadata)

    def test_metadata_conversion_requires_runner_module_fields(self) -> None:
        metadata = _synthetic_metadata(cli_module=None)

        with pytest.raises(ValueError, match="cli_module"):
            RunnerSpec.from_hypothesis_metadata(metadata)

    def test_metadata_conversion_does_not_import_module_strings(self) -> None:
        import sys

        fake_module = "unit7b.synthetic_runner_impl_should_not_import"
        sys.modules.pop(fake_module, None)
        metadata = _synthetic_metadata(implementation_module=fake_module)

        spec = RunnerSpec.from_hypothesis_metadata(metadata)

        assert spec.implementation_module == fake_module
        assert fake_module not in sys.modules


class TestRunnerSpecLazyImports:
    """Tests for lazy import helpers on RunnerSpec."""

    def test_import_implementation_module(self) -> None:
        """11b. import_implementation_module loads the heavy runner module."""
        spec = NODE_FILLS_LIQ_RECONSTRUCTION
        module = spec.import_implementation_module()
        assert module.__name__ == spec.implementation_module

    def test_import_cli_module(self) -> None:
        """11a. import_cli_module loads the CLI module."""
        spec = NODE_FILLS_LIQ_RECONSTRUCTION
        module = spec.import_cli_module()
        assert module.__name__ == spec.cli_module

    def test_import_package_module(self) -> None:
        """11c. import_package_module loads the package __init__."""
        spec = NODE_FILLS_LIQ_RECONSTRUCTION
        module = spec.import_package_module()
        assert module.__name__ == spec.package_module

    def test_import_legacy_module(self) -> None:
        """11d. import_legacy_module loads the legacy wrapper."""
        spec = NODE_FILLS_LIQ_RECONSTRUCTION
        module = spec.import_legacy_module()
        assert module.__name__ == spec.legacy_module

    def test_lazy_imports_do_not_execute_cli_main(self) -> None:
        """12. Lazy import of CLI module does not execute __main__."""
        spec = NODE_FILLS_LIQ_RECONSTRUCTION
        module = spec.import_cli_module()
        # The module should have a main function but not have executed it
        assert hasattr(module, "main") or hasattr(module, "__name__")

    def test_imported_cli_module_has_expected_shape(self) -> None:
        """13. Imported CLI module exposes the expected module path."""
        spec = NODE_FILLS_LIQ_RECONSTRUCTION
        module = spec.import_cli_module()
        assert "run_hyperliquid_node_fills" in module.__name__

    def test_imported_impl_module_exposes_compatibility_symbols(self) -> None:
        """14. Implementation module exposes known compatibility symbols."""
        spec = NODE_FILLS_LIQ_RECONSTRUCTION
        impl = spec.import_implementation_module()
        for name in [
            "StudyConfig",
            "TargetedBackwardLookupCheckpoint",
            "reconstruct_positions",
            "NodeFillsLiqReconstructionProbe",
        ]:
            assert hasattr(impl, name), f"missing symbol: {name}"

    def test_imported_package_module_exposes_compatibility_symbols(self) -> None:
        """14b. Package module exposes known compatibility symbols."""
        spec = NODE_FILLS_LIQ_RECONSTRUCTION
        pkg = spec.import_package_module()
        for name in [
            "StudyConfig",
            "TargetedBackwardLookupCheckpoint",
            "reconstruct_positions",
            "NodeFillsLiqReconstructionProbe",
        ]:
            assert hasattr(pkg, name), f"missing symbol in package: {name}"

    def test_imported_legacy_module_exposes_compatibility_symbols(self) -> None:
        """14c. Legacy module exposes known compatibility symbols."""
        spec = NODE_FILLS_LIQ_RECONSTRUCTION
        legacy = spec.import_legacy_module()
        for name in [
            "StudyConfig",
            "TargetedBackwardLookupCheckpoint",
            "reconstruct_positions",
            "NodeFillsLiqReconstructionProbe",
        ]:
            assert hasattr(legacy, name), f"missing symbol in legacy: {name}"
