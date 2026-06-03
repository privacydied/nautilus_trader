from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.runners.cli_contract import (
    RunnerCliLevel,
)
from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
    ALL_RUNNER_CLI_SPECS,
    HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC,
    HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC,
)
from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
    iter_runner_specs,
)

_REPO_ROOT = Path(__file__).resolve().parents[6]
_NODE_FILLS_KEY = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
_COST_KEY = "hyperliquid_cost_feasibility"
_OI_KEY = "hyperliquid_oi_velocity_compression_phase0"
_PHASE0B_KEY = "generic_altcoin_stress_regime_ablation_phase0b"
_PHASE0A_KEY = "generic_altcoin_stress_regime_ablation_phase0a"
_OI_PACKAGE_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.oi_velocity_compression_phase0"
)
_OI_METADATA_MODULE = _OI_PACKAGE_MODULE + ".metadata"
_OI_RUNNER_MODULE = _OI_PACKAGE_MODULE + ".runner"
_OI_COMPAT_MODULE = _OI_PACKAGE_MODULE + ".compat"
_OI_LEGACY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hyperliquid_oi_velocity_compression_phase0"
)
_OI_RUN_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "run_hyperliquid_oi_velocity_compression_phase0"
)
_COST_RUNNER_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.cost_feasibility.runner"
)
_COST_LEGACY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hyperliquid_cost_feasibility"
)
_COST_RUN_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "run_hyperliquid_cost_feasibility"
)
_NODE_RUNNER_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.node_fills_liq_reconstruction.runner"
)
_REGISTRY_CLI_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer.runners.registry_cli"
)


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestUnit9SecondPackagedRunnerClosure:
    def test_registered_runner_spec_count_and_keys_are_exact(self) -> None:
        specs = iter_runner_specs()
        assert len(specs) == 5
        assert tuple(spec.key for spec in specs) == (
            _NODE_FILLS_KEY,
            _COST_KEY,
            _OI_KEY,
            _PHASE0B_KEY,
            _PHASE0A_KEY,
        )

    def test_cli_spec_catalog_count_and_keys_are_exact(self) -> None:
        assert len(ALL_RUNNER_CLI_SPECS) == 4
        assert tuple(spec.key for spec in ALL_RUNNER_CLI_SPECS) == (
            _COST_KEY,
            _OI_KEY,
            _PHASE0B_KEY,
            _PHASE0A_KEY,
        )

    def test_node_fills_still_intentionally_has_no_cli_spec(self) -> None:
        assert _NODE_FILLS_KEY not in {spec.key for spec in ALL_RUNNER_CLI_SPECS}

    def test_cost_feasibility_remains_level1_with_no_callable_strings(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.level is RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.build_parser_callable is None
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.main_callable is None

    def test_oi_velocity_is_level2_with_main_callable_only(self) -> None:
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.level is RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.build_parser_callable is None
        assert HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC.main_callable == (
            "examples.strategies.venue_agnostic_signal_observer."
            "run_hyperliquid_oi_velocity_compression_phase0:main"
        )

    def test_registry_import_does_not_import_oi_runner(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.registry  # noqa: F401
            after = set(sys.modules)
            new = after - before
            assert {_OI_RUNNER_MODULE!r} not in new
            print('OK')
            """
        )
        result = _run_python(code)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_cli_specs_import_does_not_import_oi_runner(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.cli_specs  # noqa: F401
            after = set(sys.modules)
            new = after - before
            assert {_OI_RUNNER_MODULE!r} not in new
            print('OK')
            """
        )
        result = _run_python(code)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_registry_and_cli_specs_combined_import_remains_lazy(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.registry  # noqa: F401
            import examples.strategies.venue_agnostic_signal_observer.runners.cli_specs  # noqa: F401
            after = set(sys.modules)
            new = after - before
            forbidden = [
                {_OI_RUNNER_MODULE!r},
                {_OI_RUN_MODULE!r},
                {_COST_RUNNER_MODULE!r},
                {_COST_RUN_MODULE!r},
                {_NODE_RUNNER_MODULE!r},
                {_REGISTRY_CLI_MODULE!r},
            ]
            hits = [name for name in new if name in forbidden]
            print('hits', hits)
            raise SystemExit(1 if hits else 0)
            """
        )
        result = _run_python(code)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "hits []" in result.stdout

    def test_oi_package_metadata_imports_without_runner(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import {_OI_METADATA_MODULE} as metadata
            after = set(sys.modules)
            new = after - before
            assert {_OI_RUNNER_MODULE!r} not in new
            print('has_metadata', hasattr(metadata, 'METADATA'))
            """
        )
        result = _run_python(code)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "has_metadata True" in result.stdout

    def test_oi_package_root_imports_without_runner(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import {_OI_PACKAGE_MODULE} as package
            after = set(sys.modules)
            new = after - before
            assert {_OI_RUNNER_MODULE!r} not in new
            print('has_metadata', hasattr(package, 'METADATA'))
            print('has_runner_symbol', hasattr(package, 'run_phase0_pipeline'))
            """
        )
        result = _run_python(code)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "has_metadata True" in result.stdout
        assert "has_runner_symbol False" in result.stdout

    def test_oi_legacy_wrapper_imports_runner_only_when_explicitly_imported(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import {_OI_LEGACY_MODULE} as legacy
            after = set(sys.modules)
            new = after - before
            print('runner_imported', {_OI_RUNNER_MODULE!r} in new)
            print('has_run_phase0_pipeline', hasattr(legacy, 'run_phase0_pipeline'))
            raise SystemExit(0 if {_OI_RUNNER_MODULE!r} in new else 1)
            """
        )
        result = _run_python(code)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "runner_imported True" in result.stdout
        assert "has_run_phase0_pipeline True" in result.stdout

    def test_old_oi_cli_help_remains_unchanged(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                _OI_RUN_MODULE,
                "--help",
            ],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        assert len(result.stdout.splitlines()) == 14
        assert result.stderr.strip() == ""

    def test_registry_cli_json_remains_registry_only(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                _REGISTRY_CLI_MODULE,
                "--list",
                "--json",
            ],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        stdout = result.stdout
        assert '"key": "hyperliquid_oi_velocity_compression_phase0"' in stdout
        assert '"level"' not in stdout
        assert '"risk"' not in stdout
        assert '"supports_help_only"' not in stdout

    def test_manual_opt_in_only_no_auto_discovery_admission_apis_added(self) -> None:
        import examples.strategies.venue_agnostic_signal_observer.runners.cli_specs as cli_specs
        import examples.strategies.venue_agnostic_signal_observer.runners.registry as registry

        forbidden = {
            "discover_runner_cli_specs",
            "auto_discover_runner_cli_specs",
            "auto_register_runner_specs",
            "discover_runner_specs",
        }
        assert forbidden.isdisjoint(set(dir(cli_specs)))
        assert forbidden.isdisjoint(set(dir(registry)))
