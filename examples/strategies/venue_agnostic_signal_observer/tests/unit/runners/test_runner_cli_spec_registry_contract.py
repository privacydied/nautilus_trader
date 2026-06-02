from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.cli_contract import (
    RunnerCliLevel,
    RunnerCliRisk,
    validate_runner_cli_spec,
)
from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
    HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC,
    HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC,
    get_runner_cli_spec,
    iter_runner_cli_specs,
    require_runner_cli_spec,
)
from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
    get_runner_spec,
    iter_runner_specs,
    require_runner_spec,
)

_REPO_ROOT = Path(__file__).resolve().parents[6]

EXPECTED_REGISTRY_KEYS = (
    "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
    "hyperliquid_cost_feasibility",
    "hyperliquid_oi_velocity_compression_phase0",
)
EXPECTED_CLI_SPEC_KEYS = (
    "hyperliquid_cost_feasibility",
    "hyperliquid_oi_velocity_compression_phase0",
)
_COST_KEY = "hyperliquid_cost_feasibility"
_OI_KEY = "hyperliquid_oi_velocity_compression_phase0"
_NODE_FILLS_KEY = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"

_COST_RUNNER_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.cost_feasibility.runner"
)
_OI_RUNNER_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.oi_velocity_compression_phase0.runner"
)
_NODE_FILLS_RUNNER_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.node_fills_liq_reconstruction.runner"
)
_NODE_FILLS_LEGACY_ENTRY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
)
_COST_ENTRY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "run_hyperliquid_cost_feasibility"
)
_OI_ENTRY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "run_hyperliquid_oi_velocity_compression_phase0"
)
_REGISTRY_CLI_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer.runners.registry_cli"
)
_COST_METADATA_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.cost_feasibility.metadata"
)
_OI_METADATA_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.oi_velocity_compression_phase0.metadata"
)


def _run_isolated_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestRunnerCliSpecRegistryContract:
    def test_registry_and_cli_specs_modules_import_cleanly(self) -> None:
        import examples.strategies.venue_agnostic_signal_observer.runners.cli_specs as cli_specs_module
        import examples.strategies.venue_agnostic_signal_observer.runners.registry as registry_module

        assert callable(cli_specs_module.iter_runner_cli_specs)
        assert callable(registry_module.iter_runner_specs)

    def test_cross_contract_import_is_dependency_light_in_subprocess(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            from examples.strategies.venue_agnostic_signal_observer.runners import cli_specs
            from examples.strategies.venue_agnostic_signal_observer.runners import registry
            after = set(sys.modules)
            new = sorted(after - before)
            forbidden_exact = {{
                {_NODE_FILLS_RUNNER_MODULE!r},
                {_NODE_FILLS_LEGACY_ENTRY_MODULE!r},
                {_REGISTRY_CLI_MODULE!r},
            }}
            forbidden_fragments = (
                ".paper",
                ".governance",
                ".conductor",
                ".shadow",
                ".bot",
                "boto",
                "requests",
                "websocket",
                "TradingNode",
                "ExecutionClient",
            )
            hits = [
                name
                for name in new
                if name in forbidden_exact or any(fragment in name for fragment in forbidden_fragments)
            ]
            print("cli_specs", cli_specs.__name__)
            print("registry", registry.__name__)
            print("new_count", len(new))
            print("cost_runner_imported", {_COST_RUNNER_MODULE!r} in new)
            print("oi_runner_imported", {_OI_RUNNER_MODULE!r} in new)
            print("cost_entry_imported", {_COST_ENTRY_MODULE!r} in new)
            print("oi_entry_imported", {_OI_ENTRY_MODULE!r} in new)
            print("hits", hits)
            raise SystemExit(1 if hits else 0)
            """
        )
        result = _run_isolated_python(code)
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "hits []" in result.stdout
        assert f"cost_runner_imported {False}" in result.stdout
        assert f"oi_runner_imported {False}" in result.stdout
        assert f"cost_entry_imported {False}" in result.stdout
        assert f"oi_entry_imported {False}" in result.stdout

    def test_registered_runner_spec_keys_are_deterministic(self) -> None:
        assert tuple(spec.key for spec in iter_runner_specs()) == EXPECTED_REGISTRY_KEYS

    def test_cli_spec_catalog_keys_are_deterministic(self) -> None:
        assert tuple(spec.key for spec in iter_runner_cli_specs()) == EXPECTED_CLI_SPEC_KEYS

    def test_cli_spec_catalog_keys_are_subset_of_registered_runner_spec_keys(self) -> None:
        registry_keys = {spec.key for spec in iter_runner_specs()}
        cli_spec_keys = {spec.key for spec in iter_runner_cli_specs()}
        assert cli_spec_keys <= registry_keys

    def test_every_registered_cli_spec_has_matching_runner_spec(self) -> None:
        for spec in iter_runner_cli_specs():
            if spec.registered:
                assert get_runner_spec(spec.key) is not None

    def test_registered_runner_specs_are_not_required_to_have_cli_specs_yet(self) -> None:
        assert get_runner_spec(_NODE_FILLS_KEY) is not None
        assert get_runner_spec(_OI_KEY) is not None
        assert get_runner_cli_spec(_NODE_FILLS_KEY) is None

    def test_cost_feasibility_cli_spec_matches_runner_spec_key(self) -> None:
        runner_spec = require_runner_spec(_COST_KEY)
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.key == runner_spec.key

    def test_cost_feasibility_cli_spec_entry_module_matches_runner_spec(self) -> None:
        runner_spec = require_runner_spec(_COST_KEY)
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.entry_module == runner_spec.cli_module

    def test_cost_feasibility_cli_spec_implementation_module_matches_runner_spec(self) -> None:
        runner_spec = require_runner_spec(_COST_KEY)
        assert (
            HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.implementation_module
            == runner_spec.implementation_module
        )

    def test_cost_feasibility_cli_spec_metadata_module_matches_metadata_expectation(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.metadata_module == _COST_METADATA_MODULE

    def test_cost_feasibility_cli_spec_registered_flag_is_true(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.registered is True

    def test_cost_feasibility_cli_spec_level_remains_help_safe_metadata(self) -> None:
        assert (
            HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.level
            is RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA
        )

    def test_cost_feasibility_cli_spec_risk_remains_pure_wrapper_ready(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.risk is RunnerCliRisk.PURE_WRAPPER_READY

    def test_cost_feasibility_cli_spec_validates_under_runner_cli_contract(self) -> None:
        assert (
            validate_runner_cli_spec(HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC)
            is HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC
        )

    def test_oi_velocity_cli_spec_matches_runner_spec_key(self) -> None:
        runner_spec = require_runner_spec(_OI_KEY)
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.key == runner_spec.key

    def test_oi_velocity_cli_spec_entry_module_matches_runner_spec(self) -> None:
        runner_spec = require_runner_spec(_OI_KEY)
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.entry_module == runner_spec.cli_module

    def test_oi_velocity_cli_spec_implementation_module_matches_runner_spec(self) -> None:
        runner_spec = require_runner_spec(_OI_KEY)
        assert (
            HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.implementation_module
            == runner_spec.implementation_module
        )

    def test_oi_velocity_cli_spec_metadata_module_matches_metadata_expectation(self) -> None:
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.metadata_module == _OI_METADATA_MODULE

    def test_oi_velocity_cli_spec_registered_flag_is_true(self) -> None:
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.registered is True

    def test_oi_velocity_cli_spec_level_promotes_to_level2_exposes_callables(self) -> None:
        assert (
            HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.level
            is RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES
        )

    def test_oi_velocity_cli_spec_main_callable_is_explicit_and_build_parser_remains_none(self) -> None:
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.main_callable == (
            "examples.strategies.venue_agnostic_signal_observer."
            "run_hyperliquid_oi_velocity_compression_phase0:main"
        )
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.build_parser_callable is None

    def test_oi_velocity_cli_spec_risk_is_thin_cli_with_safe_help_only(self) -> None:
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.risk is RunnerCliRisk.THIN_CLI_WITH_SAFE_HELP_ONLY

    def test_oi_velocity_cli_spec_validates_under_runner_cli_contract(self) -> None:
        assert (
            validate_runner_cli_spec(HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC)
            is HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC
        )

    def test_node_fills_runner_spec_intentionally_has_no_cli_spec_in_unit8d(self) -> None:
        assert require_runner_spec(_NODE_FILLS_KEY).key == _NODE_FILLS_KEY
        assert get_runner_cli_spec(_NODE_FILLS_KEY) is None
        with pytest.raises(KeyError, match="Unknown runner CLI spec"):
            require_runner_cli_spec(_NODE_FILLS_KEY)

    def test_registry_get_and_require_functions_still_resolve_registered_keys(self) -> None:
        assert get_runner_spec(_NODE_FILLS_KEY) is require_runner_spec(_NODE_FILLS_KEY)
        assert get_runner_spec(_COST_KEY) is require_runner_spec(_COST_KEY)
        assert get_runner_spec(_OI_KEY) is require_runner_spec(_OI_KEY)

    def test_cli_specs_get_and_require_functions_resolve_registered_cli_specs(self) -> None:
        assert get_runner_cli_spec(_COST_KEY) is HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC
        assert require_runner_cli_spec(_COST_KEY) is HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC
        assert get_runner_cli_spec(_OI_KEY) is HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC
        assert require_runner_cli_spec(_OI_KEY) is HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC

    def test_registry_cli_output_remains_registry_only_not_cli_spec_output(self) -> None:
        code = textwrap.dedent(
            """
            import json
            import subprocess
            import sys

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "examples.strategies.venue_agnostic_signal_observer.runners.registry_cli",
                    "--list",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            payload = json.loads(result.stdout)
            assert list(payload) == ["runners"]
            assert len(payload["runners"]) == 3
            for runner in payload["runners"]:
                assert "key" in runner
                assert "cli_module" in runner
                assert "implementation_module" in runner
                assert "level" not in runner
                assert "risk" not in runner
                assert "supports_help_only" not in runner
            print("OK")
            """
        )
        result = _run_isolated_python(code)
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert result.stdout.strip().endswith("OK")

    def test_importing_cli_specs_and_registry_does_not_import_runner_or_old_cli_modules(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            from examples.strategies.venue_agnostic_signal_observer.runners import cli_specs, registry
            after = set(sys.modules)
            new = after - before
            assert {_COST_RUNNER_MODULE!r} not in new
            assert {_OI_RUNNER_MODULE!r} not in new
            assert {_COST_ENTRY_MODULE!r} not in new
            assert {_OI_ENTRY_MODULE!r} not in new
            print("OK")
            """
        )
        result = _run_isolated_python(code)
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
