from __future__ import annotations

import dataclasses
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.cli_contract import (
    RunnerCliLevel,
    RunnerCliRisk,
    RunnerCliSpec,
    normalize_test_modules,
    validate_runner_cli_spec,
)

_REPO_ROOT = Path(__file__).resolve().parents[6]

_COST_FEASIBILITY_RUNNER_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.cost_feasibility.runner"
)
_NODE_FILLS_RUNNER_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.node_fills_liq_reconstruction.runner"
)


def _minimal_spec(**overrides: object) -> RunnerCliSpec:
    values: dict[str, object] = {
        "key": "synthetic_runner",
        "description": "Synthetic metadata-only runner.",
        "entry_module": "examples.synthetic.run_runner",
    }
    values.update(overrides)
    return RunnerCliSpec(**values)


def _cost_feasibility_spec() -> RunnerCliSpec:
    return RunnerCliSpec(
        key="hyperliquid_cost_feasibility",
        description="Hyperliquid cost feasibility compatibility CLI.",
        entry_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "run_hyperliquid_cost_feasibility"
        ),
        implementation_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "hypotheses.hyperliquid.cost_feasibility.runner"
        ),
        metadata_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "hypotheses.hyperliquid.cost_feasibility.metadata"
        ),
        level=RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA,
        risk=RunnerCliRisk.PURE_WRAPPER_READY,
        supports_help_only=True,
        import_safe=True,
        writes_artifacts=False,
        requires_network=False,
        requires_archive_data=False,
        registered=True,
        test_modules=(
            "examples.strategies.venue_agnostic_signal_observer.tests.test_hyperliquid_cost_feasibility",
            "examples.strategies.venue_agnostic_signal_observer.tests.unit.hypotheses.hyperliquid.test_hyperliquid_cost_feasibility_contract",
        ),
    )


def _node_fills_spec() -> RunnerCliSpec:
    return RunnerCliSpec(
        key="hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
        description="Hyperliquid node-fills liquidation reconstruction compatibility CLI.",
        entry_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
        ),
        implementation_module=(
            "examples.strategies.venue_agnostic_signal_observer."
            "hypotheses.hyperliquid.node_fills_liq_reconstruction.runner"
        ),
        level=RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA,
        risk=RunnerCliRisk.THIN_CLI_WITH_SAFE_HELP_ONLY,
        supports_help_only=True,
        import_safe=True,
        registered=True,
    )


def _live_observer_spec(**overrides: object) -> RunnerCliSpec:
    values: dict[str, object] = {
        "key": "hyperliquid_observer",
        "description": "Hyperliquid observer service entrypoint.",
        "entry_module": (
            "examples.strategies.venue_agnostic_signal_observer."
            "run_hyperliquid_observer"
        ),
        "level": RunnerCliLevel.LEVEL_0_METADATA_ONLY,
        "risk": RunnerCliRisk.RUNNER_TOUCHES_LIVE_OR_OBSERVER_SERVICE,
        "supports_help_only": True,
        "import_safe": True,
        "live_or_service_sensitive": True,
    }
    values.update(overrides)
    return RunnerCliSpec(**values)


class TestCliContractModule:
    def test_module_imports_cleanly(self) -> None:
        import examples.strategies.venue_agnostic_signal_observer.runners.cli_contract as module

        assert module.RunnerCliSpec is RunnerCliSpec
        assert module.validate_runner_cli_spec is validate_runner_cli_spec

    def test_cli_contract_import_is_dependency_light(self) -> None:
        code = textwrap.dedent(
            """
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.cli_contract as module
            after = set(sys.modules)
            new = sorted(after - before)
            forbidden = [
                "paper",
                "governance",
                "conductor",
                "bot",
                "shadow",
                "node_fills_liq_reconstruction.runner",
                "cost_feasibility.runner",
                "boto",
                "pandas",
                "numpy",
                "requests",
                "websocket",
            ]
            hits = [name for name in new if any(token in name for token in forbidden)]
            print("module", module.__name__)
            print("new_count", len(new))
            print("hits", hits)
            raise SystemExit(1 if hits else 0)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "hits []" in result.stdout


class TestRunnerCliEnums:
    def test_runner_cli_level_values_are_stable(self) -> None:
        assert RunnerCliLevel.LEVEL_0_METADATA_ONLY.value == "level_0_metadata_only"
        assert RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA.value == "level_1_help_safe_metadata"
        assert RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES.value == "level_2_exposes_callables"
        assert RunnerCliLevel.LEVEL_3_COMMON_CLI_HELPER.value == "level_3_common_cli_helper"

    def test_runner_cli_risk_values_are_stable(self) -> None:
        assert RunnerCliRisk.PURE_WRAPPER_READY.value == "pure_wrapper_ready"
        assert (
            RunnerCliRisk.THIN_CLI_WITH_SAFE_HELP_ONLY.value
            == "thin_cli_with_safe_help_only"
        )
        assert (
            RunnerCliRisk.ARGPARSE_BUT_HAS_IO_OR_ARTIFACT_WRITES.value
            == "argparse_but_has_io_or_artifact_writes"
        )
        assert (
            RunnerCliRisk.RUNNER_CONTAINS_RESEARCH_LOGIC.value
            == "runner_contains_research_logic"
        )
        assert (
            RunnerCliRisk.RUNNER_CONTAINS_DATA_FETCH_OR_ARCHIVE_IO.value
            == "runner_contains_data_fetch_or_archive_io"
        )
        assert (
            RunnerCliRisk.RUNNER_TOUCHES_PAPER_GOVERNANCE_CONDUCTOR.value
            == "runner_touches_paper_governance_conductor"
        )
        assert (
            RunnerCliRisk.RUNNER_TOUCHES_LIVE_OR_OBSERVER_SERVICE.value
            == "runner_touches_live_or_observer_service"
        )
        assert RunnerCliRisk.KEEP_LEGACY_FOR_NOW.value == "keep_legacy_for_now"
        assert RunnerCliRisk.NEEDS_DEEPER_REVIEW.value == "needs_deeper_review"


class TestRunnerCliValidation:
    def test_valid_minimal_level0_spec_validates(self) -> None:
        spec = _minimal_spec()

        validated = validate_runner_cli_spec(spec)

        assert validated is spec
        assert validated.level is RunnerCliLevel.LEVEL_0_METADATA_ONLY
        assert validated.risk is RunnerCliRisk.NEEDS_DEEPER_REVIEW

    def test_valid_help_safe_level1_spec_validates(self) -> None:
        spec = _cost_feasibility_spec()

        validated = validate_runner_cli_spec(spec)

        assert validated is spec
        assert validated.level is RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA
        assert validated.risk is RunnerCliRisk.PURE_WRAPPER_READY
        assert validated.supports_help_only is True
        assert validated.import_safe is True

    def test_valid_level2_spec_requires_main_callable(self) -> None:
        spec = _minimal_spec(level=RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES)

        with pytest.raises(ValueError, match="main_callable"):
            validate_runner_cli_spec(spec)

        valid = _minimal_spec(
            level=RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES,
            main_callable="examples.synthetic.run_runner:main",
        )
        assert validate_runner_cli_spec(valid) is valid

    def test_valid_level3_spec_requires_main_callable_and_build_parser_callable(
        self,
    ) -> None:
        missing_main = _minimal_spec(
            level=RunnerCliLevel.LEVEL_3_COMMON_CLI_HELPER,
            build_parser_callable="examples.synthetic.run_runner:build_parser",
        )
        with pytest.raises(ValueError, match="main_callable"):
            validate_runner_cli_spec(missing_main)

        missing_parser = _minimal_spec(
            level=RunnerCliLevel.LEVEL_3_COMMON_CLI_HELPER,
            main_callable="examples.synthetic.run_runner:main",
        )
        with pytest.raises(ValueError, match="build_parser_callable"):
            validate_runner_cli_spec(missing_parser)

        valid = _minimal_spec(
            level=RunnerCliLevel.LEVEL_3_COMMON_CLI_HELPER,
            risk=RunnerCliRisk.THIN_CLI_WITH_SAFE_HELP_ONLY,
            main_callable="examples.synthetic.run_runner:main",
            build_parser_callable="examples.synthetic.run_runner:build_parser",
        )
        assert validate_runner_cli_spec(valid) is valid

    @pytest.mark.parametrize(
        ("field_name", "value"),
        [
            ("key", ""),
            ("key", "   "),
            ("description", ""),
            ("description", "  \n"),
            ("entry_module", ""),
            ("entry_module", "   "),
        ],
    )
    def test_required_strings_reject_empty_values(
        self,
        field_name: str,
        value: str,
    ) -> None:
        spec = _minimal_spec(**{field_name: value})

        with pytest.raises(ValueError, match=field_name):
            validate_runner_cli_spec(spec)

    @pytest.mark.parametrize(
        "field_name",
        [
            "implementation_module",
            "metadata_module",
            "build_parser_callable",
            "main_callable",
            "legacy_entrypoint_path",
        ],
    )
    def test_optional_strings_reject_empty_values_when_provided(
        self,
        field_name: str,
    ) -> None:
        spec = _minimal_spec(**{field_name: "  "})

        with pytest.raises(ValueError, match=field_name):
            validate_runner_cli_spec(spec)

    def test_legacy_entrypoint_path_rejects_absolute_path(self) -> None:
        spec = _minimal_spec(legacy_entrypoint_path="/tmp/run_example.py")

        with pytest.raises(ValueError, match="legacy_entrypoint_path"):
            validate_runner_cli_spec(spec)

    def test_legacy_entrypoint_path_rejects_parent_traversal(self) -> None:
        spec = _minimal_spec(legacy_entrypoint_path="../run_example.py")

        with pytest.raises(ValueError, match="legacy_entrypoint_path"):
            validate_runner_cli_spec(spec)

    def test_test_modules_normalize_to_tuple(self) -> None:
        normalized = normalize_test_modules([" a.test ", "b.test", "c.test  "])

        assert normalized == ("a.test", "b.test", "c.test")
        assert isinstance(normalized, tuple)

    def test_test_modules_reject_empty_entries(self) -> None:
        with pytest.raises(ValueError, match="test_modules"):
            normalize_test_modules(["ok.test", "  "])

    def test_test_modules_reject_duplicates(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            normalize_test_modules(["dup.test", " dup.test "])

    def test_supports_help_only_requires_import_safe(self) -> None:
        spec = _minimal_spec(supports_help_only=True)

        with pytest.raises(ValueError, match="supports_help_only"):
            validate_runner_cli_spec(spec)

    def test_requires_network_prevents_pure_wrapper_ready(self) -> None:
        spec = _minimal_spec(
            risk=RunnerCliRisk.PURE_WRAPPER_READY,
            requires_network=True,
        )

        with pytest.raises(ValueError, match="requires_network"):
            validate_runner_cli_spec(spec)

    def test_requires_archive_data_prevents_pure_wrapper_ready(self) -> None:
        spec = _minimal_spec(
            risk=RunnerCliRisk.PURE_WRAPPER_READY,
            requires_archive_data=True,
        )

        with pytest.raises(ValueError, match="requires_archive_data"):
            validate_runner_cli_spec(spec)

    def test_paper_governance_sensitive_prevents_pure_wrapper_ready(self) -> None:
        spec = _minimal_spec(
            risk=RunnerCliRisk.PURE_WRAPPER_READY,
            paper_or_governance_sensitive=True,
        )

        with pytest.raises(ValueError, match="paper_or_governance_sensitive"):
            validate_runner_cli_spec(spec)

    def test_live_service_sensitive_prevents_level3_common_cli_helper(self) -> None:
        spec = _live_observer_spec(
            level=RunnerCliLevel.LEVEL_3_COMMON_CLI_HELPER,
            main_callable="examples.synthetic.run_live:main",
            build_parser_callable="examples.synthetic.run_live:build_parser",
        )

        with pytest.raises(ValueError, match="live_or_service_sensitive"):
            validate_runner_cli_spec(spec)

    def test_validation_does_not_import_dotted_modules(self) -> None:
        sys.modules.pop(_COST_FEASIBILITY_RUNNER_MODULE, None)
        sys.modules.pop(_NODE_FILLS_RUNNER_MODULE, None)
        spec = _cost_feasibility_spec()

        validated = validate_runner_cli_spec(spec)

        assert validated is spec
        assert _COST_FEASIBILITY_RUNNER_MODULE not in sys.modules
        assert _NODE_FILLS_RUNNER_MODULE not in sys.modules

    def test_dataclass_is_frozen(self) -> None:
        spec = _minimal_spec()

        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.key = "mutated"

    def test_dataclass_uses_slots(self) -> None:
        spec = _minimal_spec()

        assert hasattr(RunnerCliSpec, "__slots__")
        assert not hasattr(spec, "__dict__")

    def test_cost_feasibility_sample_spec_validates(self) -> None:
        spec = _cost_feasibility_spec()

        validated = validate_runner_cli_spec(spec)

        assert validated.registered is True
        assert validated.metadata_module is not None
        assert validated.test_modules[0].endswith("test_hyperliquid_cost_feasibility")

    def test_node_fills_sample_spec_validates(self) -> None:
        spec = _node_fills_spec()

        validated = validate_runner_cli_spec(spec)

        assert validated.registered is True
        assert validated.risk is RunnerCliRisk.THIN_CLI_WITH_SAFE_HELP_ONLY
        assert validated.supports_help_only is True

    def test_live_observer_deferred_sample_validates(self) -> None:
        spec = _live_observer_spec()

        validated = validate_runner_cli_spec(spec)

        assert validated.level is RunnerCliLevel.LEVEL_0_METADATA_ONLY
        assert validated.live_or_service_sensitive is True
