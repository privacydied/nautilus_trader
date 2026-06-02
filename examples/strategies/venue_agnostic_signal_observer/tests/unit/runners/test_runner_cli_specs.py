from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.cli_contract import (
    RunnerCliLevel,
    RunnerCliRisk,
    validate_runner_cli_spec,
)
from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
    ALL_RUNNER_CLI_SPECS,
    HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC,
    HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC,
    get_runner_cli_spec,
    iter_runner_cli_specs,
    require_runner_cli_spec,
)

_COST_KEY = "hyperliquid_cost_feasibility"
_OI_KEY = "hyperliquid_oi_velocity_compression_phase0"
_ENTRY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "run_hyperliquid_cost_feasibility"
)
_IMPLEMENTATION_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.cost_feasibility.runner"
)
_METADATA_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.cost_feasibility.metadata"
)
_LEGACY_ENTRYPOINT_PATH = (
    "examples/strategies/venue_agnostic_signal_observer/"
    "run_hyperliquid_cost_feasibility.py"
)
_OI_ENTRY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "run_hyperliquid_oi_velocity_compression_phase0"
)
_OI_IMPLEMENTATION_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.oi_velocity_compression_phase0.runner"
)
_OI_METADATA_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.oi_velocity_compression_phase0.metadata"
)
_OI_LEGACY_ENTRYPOINT_PATH = (
    "examples/strategies/venue_agnostic_signal_observer/"
    "run_hyperliquid_oi_velocity_compression_phase0.py"
)


class TestCliSpecsModule:
    def test_cli_specs_module_imports_cleanly(self) -> None:
        import examples.strategies.venue_agnostic_signal_observer.runners.cli_specs as module

        assert module.HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC is HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC
        assert module.HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC is HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC
        assert module.ALL_RUNNER_CLI_SPECS is ALL_RUNNER_CLI_SPECS

    def test_cli_specs_import_is_dependency_light(self) -> None:
        code = textwrap.dedent(
            """
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.cli_specs as module
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
                "oi_velocity_compression_phase0.runner",
                "run_hyperliquid_cost_feasibility",
                "run_hyperliquid_oi_velocity_compression_phase0",
                "hyperliquid_cost_feasibility",
                "hyperliquid_oi_velocity_compression_phase0",
                "registry_cli",
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
            cwd="/mnt/nasirjones/py/nautilus_trader",
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
        assert "hits []" in result.stdout


class TestCliSpecsCatalog:
    def test_all_runner_cli_specs_is_tuple(self) -> None:
        assert isinstance(ALL_RUNNER_CLI_SPECS, tuple)

    def test_catalog_contains_exactly_two_specs_in_unit9c(self) -> None:
        assert len(ALL_RUNNER_CLI_SPECS) == 2

    def test_keys_are_hyperliquid_cost_feasibility_and_oi_velocity(self) -> None:
        assert tuple(spec.key for spec in ALL_RUNNER_CLI_SPECS) == (_COST_KEY, _OI_KEY)

    def test_cost_feasibility_spec_validates_under_validate_runner_cli_spec(self) -> None:
        assert validate_runner_cli_spec(HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC) is HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC

    def test_cost_feasibility_spec_has_level1_help_safe_metadata(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.level is RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA

    def test_cost_feasibility_spec_has_pure_wrapper_ready(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.risk is RunnerCliRisk.PURE_WRAPPER_READY

    def test_cost_feasibility_spec_has_supports_help_only_true(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.supports_help_only is True

    def test_cost_feasibility_spec_has_import_safe_true(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.import_safe is True

    def test_cost_feasibility_spec_has_registered_true(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.registered is True

    def test_cost_feasibility_spec_does_not_claim_build_parser_callable(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.build_parser_callable is None

    def test_cost_feasibility_spec_does_not_claim_main_callable(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.main_callable is None

    def test_cost_feasibility_spec_does_not_require_network(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.requires_network is False

    def test_cost_feasibility_spec_does_not_require_archive_data(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.requires_archive_data is False

    def test_cost_feasibility_spec_does_not_require_reports_dir(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.requires_reports_dir is False

    def test_cost_feasibility_spec_is_not_paper_governance_sensitive(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.paper_or_governance_sensitive is False

    def test_cost_feasibility_spec_is_not_live_service_sensitive(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.live_or_service_sensitive is False

    def test_entry_module_equals_old_run_module_string(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.entry_module == _ENTRY_MODULE

    def test_implementation_module_equals_packaged_runner_module_string(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.implementation_module == _IMPLEMENTATION_MODULE

    def test_metadata_module_equals_packaged_metadata_module_string(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.metadata_module == _METADATA_MODULE

    def test_legacy_entrypoint_path_is_repo_relative_old_cli_string(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.legacy_entrypoint_path == _LEGACY_ENTRYPOINT_PATH
        assert not HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.legacy_entrypoint_path.startswith("/")

    def test_test_modules_tuple_is_deterministic(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.test_modules == (
            "examples.strategies.venue_agnostic_signal_observer.tests.unit.hypotheses.hyperliquid.test_hyperliquid_cost_feasibility_contract",
        )

    def test_oi_velocity_spec_has_level1_help_safe_metadata(self) -> None:
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.level is RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES

    def test_oi_velocity_spec_has_help_safe_risk(self) -> None:
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.risk is RunnerCliRisk.THIN_CLI_WITH_SAFE_HELP_ONLY

    def test_oi_velocity_spec_does_not_claim_build_parser_callable(self) -> None:
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.build_parser_callable is None

    def test_oi_velocity_spec_does_not_claim_main_callable(self) -> None:
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.main_callable == (
            "examples.strategies.venue_agnostic_signal_observer."
            "run_hyperliquid_oi_velocity_compression_phase0:main"
        )

    def test_oi_velocity_spec_still_does_not_claim_build_parser_callable(self) -> None:
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.build_parser_callable is None

    def test_oi_velocity_spec_modules_match_expected_strings(self) -> None:
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.entry_module == _OI_ENTRY_MODULE
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.implementation_module == _OI_IMPLEMENTATION_MODULE
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.metadata_module == _OI_METADATA_MODULE
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.legacy_entrypoint_path == _OI_LEGACY_ENTRYPOINT_PATH

    def test_oi_velocity_spec_test_modules_tuple_is_deterministic(self) -> None:
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.test_modules == (
            "examples.strategies.venue_agnostic_signal_observer.tests.unit.hypotheses.hyperliquid.test_hyperliquid_oi_velocity_compression_package_contract",
        )

    def test_iter_runner_cli_specs_returns_same_or_equal_tuple(self) -> None:
        result = iter_runner_cli_specs()
        assert result is ALL_RUNNER_CLI_SPECS or result == ALL_RUNNER_CLI_SPECS

    def test_get_runner_cli_spec_returns_spec_for_known_key(self) -> None:
        assert get_runner_cli_spec(_COST_KEY) is HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC

    def test_get_runner_cli_spec_returns_oi_velocity_spec_for_known_key(self) -> None:
        assert get_runner_cli_spec(_OI_KEY) is HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC

    def test_get_runner_cli_spec_strips_outer_whitespace(self) -> None:
        assert get_runner_cli_spec(f"  {_COST_KEY}  ") is HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC

    def test_get_runner_cli_spec_returns_none_for_unknown_key(self) -> None:
        assert get_runner_cli_spec("missing") is None

    def test_require_runner_cli_spec_returns_spec_for_known_key(self) -> None:
        assert require_runner_cli_spec(_COST_KEY) is HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC

    def test_require_runner_cli_spec_returns_oi_velocity_spec_for_known_key(self) -> None:
        assert require_runner_cli_spec(_OI_KEY) is HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC

    def test_require_runner_cli_spec_raises_key_error_for_unknown_key(self) -> None:
        with pytest.raises(KeyError, match="Unknown runner CLI spec"):
            require_runner_cli_spec("missing")

    def test_catalog_keys_are_unique(self) -> None:
        keys = [spec.key for spec in ALL_RUNNER_CLI_SPECS]
        assert len(keys) == len(set(keys))

    def test_catalog_order_is_deterministic(self) -> None:
        assert ALL_RUNNER_CLI_SPECS == (
            HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC,
            HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC,
        )

    def test_importing_cli_specs_does_not_import_runner_modules(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.cli_specs  # noqa: F401
            after = set(sys.modules)
            assert {_IMPLEMENTATION_MODULE!r} not in after - before
            assert {_OI_IMPLEMENTATION_MODULE!r} not in after - before
            print('OK')
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd="/mnt/nasirjones/py/nautilus_trader",
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )

    def test_importing_cli_specs_does_not_import_old_run_modules(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.cli_specs  # noqa: F401
            after = set(sys.modules)
            assert {_ENTRY_MODULE!r} not in after - before
            assert {_OI_ENTRY_MODULE!r} not in after - before
            print('OK')
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd="/mnt/nasirjones/py/nautilus_trader",
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )

    def test_importing_cli_specs_does_not_import_registry_cli(self) -> None:
        code = textwrap.dedent(
            """
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.cli_specs  # noqa: F401
            after = set(sys.modules)
            assert (
                "examples.strategies.venue_agnostic_signal_observer.runners.registry_cli"
                not in after - before
            )
            print('OK')
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd="/mnt/nasirjones/py/nautilus_trader",
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
