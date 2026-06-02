from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
    ALL_RUNNER_CLI_SPECS,
)
from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
    iter_runner_specs,
)

_REPO_ROOT = Path(__file__).resolve().parents[6]
_NODE_FILLS_KEY = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
_COST_KEY = "hyperliquid_cost_feasibility"
_COST_PACKAGE_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.cost_feasibility"
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


class TestUnit8RunnerCliSpineClosure:
    def test_cli_contract_and_cli_specs_catalog_remain_dependency_light(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.cli_contract  # noqa: F401
            import examples.strategies.venue_agnostic_signal_observer.runners.cli_specs  # noqa: F401
            after = set(sys.modules)
            new = after - before
            forbidden = [
                {_NODE_RUNNER_MODULE!r},
                {_COST_RUNNER_MODULE!r},
                {_COST_RUN_MODULE!r},
                {_COST_LEGACY_MODULE!r},
                {_REGISTRY_CLI_MODULE!r},
            ]
            hits = [name for name in new if name in forbidden]
            print('hits', hits)
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
        assert result.returncode == 0, result.stdout + result.stderr
        assert "hits []" in result.stdout

    def test_registry_import_remains_lazy_for_runner_implementations(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.registry as registry
            after = set(sys.modules)
            new = after - before
            forbidden = [{_NODE_RUNNER_MODULE!r}, {_COST_RUNNER_MODULE!r}, {_COST_RUN_MODULE!r}]
            hits = [name for name in new if name in forbidden]
            print('hits', hits)
            print('count', len(registry.iter_runner_specs()))
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
        assert result.returncode == 0, result.stdout + result.stderr
        assert "hits []" in result.stdout
        assert "count 2" in result.stdout

    def test_registered_runner_spec_count_and_keys_remain_stable(self) -> None:
        specs = iter_runner_specs()
        assert len(specs) == 2
        assert tuple(spec.key for spec in specs) == (_NODE_FILLS_KEY, _COST_KEY)

    def test_cli_spec_catalog_count_and_keys_remain_stable(self) -> None:
        assert len(ALL_RUNNER_CLI_SPECS) == 1
        assert tuple(spec.key for spec in ALL_RUNNER_CLI_SPECS) == (_COST_KEY,)

    def test_node_fills_intentionally_has_no_cli_spec(self) -> None:
        assert _NODE_FILLS_KEY not in {spec.key for spec in ALL_RUNNER_CLI_SPECS}

    def test_help_contract_covers_every_supports_help_only_cli_spec(self) -> None:
        help_keys = tuple(spec.key for spec in ALL_RUNNER_CLI_SPECS if spec.supports_help_only)
        assert help_keys == (_COST_KEY,)

    def test_registry_cli_json_output_remains_registry_only(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "examples.strategies.venue_agnostic_signal_observer.runners.registry_cli",
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
        assert '"key": "hyperliquid_cost_feasibility"' in stdout
        assert '"key": "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"' in stdout
        assert '"level"' not in stdout
        assert '"risk"' not in stdout
        assert '"supports_help_only"' not in stdout

    def test_cost_feasibility_package_root_import_remains_metadata_light(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import {_COST_PACKAGE_MODULE} as package
            after = set(sys.modules)
            new = after - before
            hits = [name for name in new if name == {_COST_RUNNER_MODULE!r}]
            print('hits', hits)
            print('has_metadata', hasattr(package, 'METADATA'))
            print('has_compute', hasattr(package, 'compute_cost_feasibility'))
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
        assert result.returncode == 0, result.stdout + result.stderr
        assert "hits []" in result.stdout
        assert "has_metadata True" in result.stdout
        assert "has_compute False" in result.stdout

    def test_cost_feasibility_legacy_wrapper_still_exposes_compat_symbols_when_explicitly_imported(self) -> None:
        code = textwrap.dedent(
            f"""
            import {_COST_LEGACY_MODULE} as legacy
            required = [
                'PROCEED_TO_V1_EVALUATION',
                'DO_NOT_PROCEED_COST_WALL_PERSISTS',
                'INSUFFICIENT_LIVE_CAPTURE',
                'AWAITING_STRESS_WINDOWS',
                'DataSource',
                'CoinCostSummary',
                'CostFeasibilityResult',
                'compute_cost_feasibility',
                'write_cost_outputs',
            ]
            missing = [name for name in required if not hasattr(legacy, name)]
            print('missing', missing)
            raise SystemExit(1 if missing else 0)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "missing []" in result.stdout

    def test_no_automatic_discovery_or_admission_apis_were_added(self) -> None:
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

    def test_catalog_and_registry_imports_do_not_pull_old_run_py_modules(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.cli_specs  # noqa: F401
            import examples.strategies.venue_agnostic_signal_observer.runners.registry  # noqa: F401
            after = set(sys.modules)
            new = after - before
            forbidden = [{_COST_RUN_MODULE!r}, {_REGISTRY_CLI_MODULE!r}]
            hits = [name for name in new if name in forbidden]
            print('hits', hits)
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
        assert result.returncode == 0, result.stdout + result.stderr
        assert "hits []" in result.stdout
