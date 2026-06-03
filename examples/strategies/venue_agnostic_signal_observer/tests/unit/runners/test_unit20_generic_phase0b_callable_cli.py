"""Unit 20 focused tests.

These assert that the packaged Phase 0B legacy CLI is promoted to Level-2 callable
metadata:

* the ``run_*`` module exposes ``build_parser()`` and ``main(argv)``
* the CLI spec records ``LEVEL_2_EXPOSES_CALLABLES`` with a ``main_callable``
* ``--help`` output remains byte-stable at 15 lines and creates no artifacts

No real study execution is performed. Only ``--help`` is exercised. Phase 0A must
remain Level-1, and the other runners must be untouched.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Iterable

from examples.strategies.venue_agnostic_signal_observer.runners.cli_contract import (
    RunnerCliLevel,
)

_REPO_ROOT = Path(__file__).resolve().parents[6]

_PHASE0B_KEY = "generic_altcoin_stress_regime_ablation_phase0b"
_PHASE0A_KEY = "generic_altcoin_stress_regime_ablation_phase0a"
_NODE_FILLS_KEY = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
_COST_KEY = "hyperliquid_cost_feasibility"
_OI_KEY = "hyperliquid_oi_velocity_compression_phase0"

_ENTRY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "runners.legacy_cli.run_generic_altcoin_stress_regime_ablation_phase0b"
)
_RUNNER_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0b.runner"
)
_EXPECTED_MAIN_CALLABLE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "runners.legacy_cli.run_generic_altcoin_stress_regime_ablation_phase0b:main"
)
_EXPECTED_HELP_LINES = 15

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


def _iter_tracked_relative_paths(root: Path) -> Iterable[str]:
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
        rel_dir.as_posix(): tuple(_iter_tracked_relative_paths(_REPO_ROOT / rel_dir))
        for rel_dir in _HELP_DIRS
    }


def test_phase0b_cli_exposes_build_parser() -> None:
    from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli import (
        run_generic_altcoin_stress_regime_ablation_phase0b as cli,
    )

    assert hasattr(cli, "build_parser")
    parser = cli.build_parser()
    assert isinstance(parser, argparse.ArgumentParser)


def test_phase0b_cli_exposes_main() -> None:
    from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli import (
        run_generic_altcoin_stress_regime_ablation_phase0b as cli,
    )

    assert hasattr(cli, "main")
    assert callable(cli.main)


def test_phase0b_main_help_is_help_safe_and_exits_zero() -> None:
    from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli import (
        run_generic_altcoin_stress_regime_ablation_phase0b as cli,
    )

    before = _snapshot_help_dirs()
    try:
        rc = cli.main(["--help"])
    except SystemExit as exc:  # argparse --help raises SystemExit(0)
        assert exc.code in (0, None)
    else:
        assert rc == 0
    after = _snapshot_help_dirs()
    assert before == after


def test_phase0b_help_subprocess_is_stable_and_artifact_free() -> None:
    before = _snapshot_help_dirs()
    result = subprocess.run(
        [sys.executable, "-m", _ENTRY_MODULE, "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    after = _snapshot_help_dirs()
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == ""
    lines = result.stdout.splitlines()
    assert len(lines) == _EXPECTED_HELP_LINES
    assert "usage:" in result.stdout
    assert "run_generic_altcoin_stress_regime_ablation_phase0b" in result.stdout
    assert "--phase0a-report" in result.stdout
    assert before == after


def test_phase0b_cli_spec_is_level2_with_main_callable() -> None:
    from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
        require_runner_cli_spec,
    )

    spec = require_runner_cli_spec(_PHASE0B_KEY)
    assert spec.level is RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES
    assert spec.main_callable == _EXPECTED_MAIN_CALLABLE
    assert spec.build_parser_callable is None
    assert spec.registered is True
    assert spec.observer_only is True
    assert spec.paper_or_governance_sensitive is False
    assert spec.live_or_service_sensitive is False


def test_phase0b_main_callable_target_resolves_explicitly() -> None:
    import importlib

    module_path, _, attr = _EXPECTED_MAIN_CALLABLE.partition(":")
    module = importlib.import_module(module_path)
    target = getattr(module, attr)
    assert callable(target)


def test_phase0a_remains_level1_with_no_callable_metadata() -> None:
    from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
        require_runner_cli_spec,
    )

    spec = require_runner_cli_spec(_PHASE0A_KEY)
    assert spec.level is RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA
    assert spec.main_callable is None
    assert spec.build_parser_callable is None


def test_other_runners_unchanged_and_node_fills_has_no_cli_spec() -> None:
    from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
        get_runner_cli_spec,
        iter_runner_cli_specs,
        require_runner_cli_spec,
    )
    from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
        iter_runner_specs,
    )

    assert len(iter_runner_specs()) == 5
    assert len(iter_runner_cli_specs()) == 4

    cost = require_runner_cli_spec(_COST_KEY)
    assert cost.level is RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA
    assert cost.main_callable is None

    oi = require_runner_cli_spec(_OI_KEY)
    assert oi.level is RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES
    assert oi.main_callable == (
        "examples.strategies.venue_agnostic_signal_observer."
        "runners.legacy_cli.run_hyperliquid_oi_velocity_compression_phase0:main"
    )

    assert get_runner_cli_spec(_NODE_FILLS_KEY) is None


def test_registry_cli_remains_registry_only_for_phase0b() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.strategies.venue_agnostic_signal_observer.runners.registry_cli",
            "--key",
            _PHASE0B_KEY,
            "--json",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    stdout = result.stdout
    assert f'"key": "{_PHASE0B_KEY}"' in stdout
    assert '"level"' not in stdout
    assert '"main_callable"' not in stdout
    assert '"risk"' not in stdout


def test_cli_specs_import_remains_lazy() -> None:
    code = textwrap.dedent(
        f"""
        import sys
        before = set(sys.modules)
        from examples.strategies.venue_agnostic_signal_observer.runners import cli_specs
        after = set(sys.modules)
        new = after - before
        assert {_RUNNER_MODULE!r} not in new, "phase0b runner imported"
        assert {_ENTRY_MODULE!r} not in new, "phase0b old CLI imported"
        cli_specs.require_runner_cli_spec({_PHASE0B_KEY!r})
        after2 = set(sys.modules)
        new2 = after2 - before
        assert {_RUNNER_MODULE!r} not in new2, "phase0b runner imported on lookup"
        assert {_ENTRY_MODULE!r} not in new2, "phase0b old CLI imported on lookup"
        print("OK")
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
    assert "OK" in result.stdout
