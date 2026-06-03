from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
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
    get_runner_cli_spec,
)
from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
    iter_runner_specs,
)

_REPO_ROOT = Path(__file__).resolve().parents[6]
_OI_RUN_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "run_hyperliquid_oi_velocity_compression_phase0"
)
_OI_RUNNER_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.oi_velocity_compression_phase0.runner"
)
_REGISTRY_CLI_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer.runners.registry_cli"
)
_EXPECTED_RUNNER_KEYS = (
    "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
    "hyperliquid_cost_feasibility",
    "hyperliquid_oi_velocity_compression_phase0",
    "generic_altcoin_stress_regime_ablation_phase0b",
)
_EXPECTED_CLI_SPEC_KEYS = (
    "hyperliquid_cost_feasibility",
    "hyperliquid_oi_velocity_compression_phase0",
    "generic_altcoin_stress_regime_ablation_phase0b",
)
_HELP_DIRS = (
    Path("examples/strategies/venue_agnostic_signal_observer/reports"),
    Path("examples/strategies/venue_agnostic_signal_observer/data"),
    Path("examples/strategies/venue_agnostic_signal_observer/.local_data"),
    Path("reports"),
    Path("data"),
    Path(".local_data"),
)
_IGNORE_SUFFIXES = {".pyc", ".pyo"}
_IGNORE_PARTS = {"__pycache__"}


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _iter_paths(root: Path) -> tuple[str, ...]:
    if not root.exists():
        return ()
    paths: list[str] = []
    for path in root.rglob("*"):
        rel = path.relative_to(_REPO_ROOT)
        if any(part in _IGNORE_PARTS for part in rel.parts):
            continue
        if path.suffix in _IGNORE_SUFFIXES:
            continue
        paths.append(rel.as_posix())
    paths.sort()
    return tuple(paths)


def _snapshot_help_dirs() -> dict[str, tuple[str, ...]]:
    return {
        rel_dir.as_posix(): _iter_paths(_REPO_ROOT / rel_dir)
        for rel_dir in _HELP_DIRS
    }


def _run_help(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestUnit10OiVelocityCallableCli:
    def test_cli_module_exposes_build_parser_and_main(self) -> None:
        cli = importlib.import_module(_OI_RUN_MODULE)
        assert hasattr(cli, "build_parser")
        assert hasattr(cli, "main")
        assert callable(cli.build_parser)
        assert callable(cli.main)

    def test_build_parser_returns_argparse_argument_parser(self) -> None:
        cli = importlib.import_module(_OI_RUN_MODULE)
        parser = cli.build_parser()
        assert isinstance(parser, argparse.ArgumentParser)

    def test_oi_cli_spec_has_callable_metadata(self) -> None:
        spec = HYPERLIQUID_OI_VELOCITY_COMPRESSION_PHASE0_CLI_SPEC
        assert spec.main_callable == (
            "examples.strategies.venue_agnostic_signal_observer."
            "run_hyperliquid_oi_velocity_compression_phase0:main"
        )
        assert spec.build_parser_callable is None
        assert spec.level is RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES
        assert spec.level is not RunnerCliLevel.LEVEL_3_COMMON_CLI_HELPER
        assert spec.registered is True
        assert spec.observer_only is True
        assert spec.paper_or_governance_sensitive is False
        assert spec.live_or_service_sensitive is False

    def test_cli_specs_import_is_lazy_for_oi_callable_metadata(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.cli_specs  # noqa: F401
            after = set(sys.modules)
            new = after - before
            print('oi_runner_imported', {_OI_RUNNER_MODULE!r} in new)
            print('oi_cli_imported', {_OI_RUN_MODULE!r} in new)
            raise SystemExit(1 if {_OI_RUNNER_MODULE!r} in new or {_OI_RUN_MODULE!r} in new else 0)
            """
        )
        result = _run_python(code)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "oi_runner_imported False" in result.stdout
        assert "oi_cli_imported False" in result.stdout

    def test_registry_import_remains_lazy_and_does_not_execute_oi_study(self) -> None:
        code = textwrap.dedent(
            f"""
            import sys
            before = set(sys.modules)
            import examples.strategies.venue_agnostic_signal_observer.runners.registry  # noqa: F401
            after = set(sys.modules)
            new = after - before
            print('oi_runner_imported', {_OI_RUNNER_MODULE!r} in new)
            print('oi_cli_imported', {_OI_RUN_MODULE!r} in new)
            raise SystemExit(1 if {_OI_RUNNER_MODULE!r} in new or {_OI_RUN_MODULE!r} in new else 0)
            """
        )
        result = _run_python(code)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_explicit_main_callable_resolution_imports_cli_only(self) -> None:
        code = textwrap.dedent(
            f"""
            import importlib
            import sys
            from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import get_runner_cli_spec
            spec = get_runner_cli_spec('hyperliquid_oi_velocity_compression_phase0')
            assert spec is not None
            before = set(sys.modules)
            module_name, attr_name = spec.main_callable.split(':', 1)
            module = importlib.import_module(module_name)
            target = getattr(module, attr_name)
            after = set(sys.modules)
            new = after - before
            print('target_callable', callable(target))
            print('oi_cli_imported', {_OI_RUN_MODULE!r} in new or {_OI_RUN_MODULE!r} in after)
            print('registry_cli_imported', {_REGISTRY_CLI_MODULE!r} in new)
            raise SystemExit(1 if {_REGISTRY_CLI_MODULE!r} in new else 0)
            """
        )
        result = _run_python(code)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "target_callable True" in result.stdout
        assert "oi_cli_imported True" in result.stdout
        assert "registry_cli_imported False" in result.stdout

    def test_oi_help_only_subprocess_remains_safe_and_unchanged(self) -> None:
        before = _snapshot_help_dirs()
        result = _run_help(_OI_RUN_MODULE)
        after = _snapshot_help_dirs()
        assert result.returncode == 0, result.stderr
        assert result.stderr.strip() == ""
        assert "usage:" in result.stdout
        assert "run_hyperliquid_oi_velocity_compression_phase0" in result.stdout
        assert len(result.stdout.splitlines()) == 14
        assert before == after

    def test_parser_generated_help_matches_subprocess_help(self) -> None:
        code = textwrap.dedent(
            f"""
            import importlib
            import sys
            mod = importlib.import_module({_OI_RUN_MODULE!r})
            sys.argv = [
                'python -m examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_oi_velocity_compression_phase0'
            ]
            print(mod.build_parser().format_help(), end='')
            """
        )
        expected = _run_python(code)
        result = _run_help(_OI_RUN_MODULE)
        assert expected.returncode == 0, expected.stdout + expected.stderr
        assert result.returncode == 0, result.stderr
        assert result.stdout == expected.stdout

    def test_main_help_is_safe_and_does_not_execute_study(self) -> None:
        cli = importlib.import_module(_OI_RUN_MODULE)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                cli.main(["--help"])
            except SystemExit as exc:
                assert exc.code == 0
            else:
                raise AssertionError("main(['--help']) should raise SystemExit(0)")
        assert "usage:" in stdout.getvalue()
        assert stderr.getvalue() == ""

    def test_cost_feasibility_remains_level1_and_help_22_lines(self) -> None:
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.level is RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA
        assert HYPERLIQUID_COST_FEASIBILITY_CLI_SPEC.main_callable is None
        result = _run_help("examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_cost_feasibility")
        assert result.returncode == 0, result.stderr
        assert len(result.stdout.splitlines()) == 22

    def test_node_fills_remains_without_cli_spec_and_help_84_lines(self) -> None:
        assert get_runner_cli_spec("hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0") is None
        result = _run_help("examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0")
        assert result.returncode == 0, result.stderr
        assert len(result.stdout.splitlines()) == 84

    def test_runner_registry_and_cli_spec_counts_remain_strict(self) -> None:
        assert tuple(spec.key for spec in iter_runner_specs()) == _EXPECTED_RUNNER_KEYS
        assert tuple(spec.key for spec in ALL_RUNNER_CLI_SPECS) == _EXPECTED_CLI_SPEC_KEYS

    def test_registry_cli_json_remains_registry_only(self) -> None:
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
        payload = json.loads(result.stdout)
        rendered = json.dumps(payload, sort_keys=True)
        assert "main_callable" not in rendered
        assert "build_parser_callable" not in rendered
        assert "level_2_exposes_callables" not in rendered

    def test_no_automatic_package_discovery(self) -> None:
        keys = {spec.key for spec in ALL_RUNNER_CLI_SPECS}
        assert keys == set(_EXPECTED_CLI_SPEC_KEYS)
        assert "run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0" not in keys
