from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Iterable

from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
    ALL_RUNNER_CLI_SPECS,
)

_REPO_ROOT = Path(__file__).resolve().parents[6]
_ACCEPTED_HELP_KEYS = (
    "hyperliquid_cost_feasibility",
    "hyperliquid_oi_velocity_compression_phase0",
    "generic_altcoin_stress_regime_ablation_phase0b",
    "generic_altcoin_stress_regime_ablation_phase0a",
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


def _run_help_subprocess(entry_module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", entry_module, "--help"],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )


class TestRunnerCliSpecHelpContract:
    def test_cli_spec_catalog_imports_dependency_light_before_help_subprocesses(self) -> None:
        assert tuple(spec.key for spec in ALL_RUNNER_CLI_SPECS) == _ACCEPTED_HELP_KEYS
        assert all(spec.import_safe for spec in ALL_RUNNER_CLI_SPECS)
        assert all(spec.supports_help_only for spec in ALL_RUNNER_CLI_SPECS)

    def test_only_supports_help_only_specs_are_selected(self) -> None:
        selected = tuple(spec.key for spec in ALL_RUNNER_CLI_SPECS if spec.supports_help_only)
        assert selected == _ACCEPTED_HELP_KEYS

    def test_current_help_contract_spec_keys_are_exact(self) -> None:
        assert tuple(spec.key for spec in ALL_RUNNER_CLI_SPECS) == _ACCEPTED_HELP_KEYS

    def test_help_contract_does_not_include_node_fills_yet(self) -> None:
        assert "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0" not in {
            spec.key for spec in ALL_RUNNER_CLI_SPECS
        }

    def test_unknown_or_non_help_specs_are_ignored(self) -> None:
        selected = [spec.key for spec in ALL_RUNNER_CLI_SPECS if spec.supports_help_only]
        assert "missing" not in selected
        assert "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0" not in selected

    def test_cost_feasibility_help_subprocess_contract(self) -> None:
        spec = ALL_RUNNER_CLI_SPECS[0]
        before = _snapshot_help_dirs()

        result = _run_help_subprocess(spec.entry_module)

        after = _snapshot_help_dirs()
        assert result.returncode == 0, result.stderr
        assert result.stderr.strip() == ""
        assert "usage:" in result.stdout
        assert "run_hyperliquid_cost_feasibility" in result.stdout
        assert "--data-source {live,archive,both}" in result.stdout
        assert "--stress-labels-file STRESS_LABELS_FILE" in result.stdout
        assert len(result.stdout.splitlines()) == 22
        assert before == after
        assert "registry_cli" not in result.stdout
        assert "registry_cli" not in result.stderr
        assert "run_dir" not in result.stdout

    def test_oi_velocity_help_subprocess_contract(self) -> None:
        spec = ALL_RUNNER_CLI_SPECS[1]
        before = _snapshot_help_dirs()

        result = _run_help_subprocess(spec.entry_module)

        after = _snapshot_help_dirs()
        assert result.returncode == 0, result.stderr
        assert result.stderr.strip() == ""
        assert "usage:" in result.stdout
        assert "run_hyperliquid_oi_velocity_compression_phase0" in result.stdout
        assert "--data-dir DATA_DIR" in result.stdout
        assert len(result.stdout.splitlines()) == 14
        assert before == after
        assert "registry_cli" not in result.stdout
        assert "registry_cli" not in result.stderr
        assert "report_dir" not in result.stdout

    def test_generic_phase0b_help_subprocess_contract(self) -> None:
        spec = ALL_RUNNER_CLI_SPECS[2]
        assert spec.key == "generic_altcoin_stress_regime_ablation_phase0b"
        before = _snapshot_help_dirs()

        result = _run_help_subprocess(spec.entry_module)

        after = _snapshot_help_dirs()
        assert result.returncode == 0, result.stderr
        assert result.stderr.strip() == ""
        assert "usage:" in result.stdout
        assert "run_generic_altcoin_stress_regime_ablation_phase0b" in result.stdout
        assert len(result.stdout.splitlines()) == 15
        assert before == after
        assert "registry_cli" not in result.stdout
        assert "registry_cli" not in result.stderr

    def test_every_help_only_spec_runs_cleanly_without_writes(self) -> None:
        for spec in ALL_RUNNER_CLI_SPECS:
            before = _snapshot_help_dirs()
            result = _run_help_subprocess(spec.entry_module)
            after = _snapshot_help_dirs()
            assert result.returncode == 0, (spec.key, result.stderr)
            assert before == after

