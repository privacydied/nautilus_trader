from __future__ import annotations

import importlib
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import iter_runner_cli_specs
from examples.strategies.venue_agnostic_signal_observer.runners.legacy_entrypoints import (
    get_legacy_entrypoint,
    iter_legacy_entrypoints,
    validate_legacy_entrypoint_catalog,
)
from examples.strategies.venue_agnostic_signal_observer.runners.registry import iter_runner_specs
from examples.strategies.venue_agnostic_signal_observer.structure.root_module_inventory import (
    validate_root_module_ledger,
)

_PACKAGE = "examples.strategies.venue_agnostic_signal_observer"
_REPO_ROOT = Path(__file__).resolve().parents[6]
_ROOT = _REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer"
_LEGACY_CLI_ROOT = _ROOT / "runners/legacy_cli"
_TARGETS = (
    ("run_hyperliquid_cost_feasibility", 22),
    ("run_hyperliquid_funding_divergence_phase0", 17),
    ("run_hyperliquid_oi_velocity_compression_phase0", 14),
    ("run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0", 84),
    ("run_generic_altcoin_stress_regime_ablation_phase0a", 18),
    ("run_generic_altcoin_stress_regime_ablation_phase0b", 15),
)
_TARGET_MODULES = tuple(name for name, _ in _TARGETS)
_EXPECTED_RUNNER_MODULES = {
    "hyperliquid_cost_feasibility": f"{_PACKAGE}.runners.legacy_cli.run_hyperliquid_cost_feasibility",
    "hyperliquid_oi_velocity_compression_phase0": f"{_PACKAGE}.runners.legacy_cli.run_hyperliquid_oi_velocity_compression_phase0",
    "generic_altcoin_stress_regime_ablation_phase0a": f"{_PACKAGE}.runners.legacy_cli.run_generic_altcoin_stress_regime_ablation_phase0a",
    "generic_altcoin_stress_regime_ablation_phase0b": f"{_PACKAGE}.runners.legacy_cli.run_generic_altcoin_stress_regime_ablation_phase0b",
    "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0": (
        f"{_PACKAGE}.runners.legacy_cli.run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
    ),
}
_EXPECTED_CLI_ENTRY_MODULES = {
    "hyperliquid_cost_feasibility": f"{_PACKAGE}.runners.legacy_cli.run_hyperliquid_cost_feasibility",
    "hyperliquid_oi_velocity_compression_phase0": f"{_PACKAGE}.runners.legacy_cli.run_hyperliquid_oi_velocity_compression_phase0",
    "generic_altcoin_stress_regime_ablation_phase0a": f"{_PACKAGE}.runners.legacy_cli.run_generic_altcoin_stress_regime_ablation_phase0a",
    "generic_altcoin_stress_regime_ablation_phase0b": f"{_PACKAGE}.runners.legacy_cli.run_generic_altcoin_stress_regime_ablation_phase0b",
}
_EXPECTED_CLI_MAIN_CALLABLES = {
    "hyperliquid_oi_velocity_compression_phase0": (
        f"{_PACKAGE}.runners.legacy_cli.run_hyperliquid_oi_velocity_compression_phase0:main"
    ),
    "generic_altcoin_stress_regime_ablation_phase0b": (
        f"{_PACKAGE}.runners.legacy_cli.run_generic_altcoin_stress_regime_ablation_phase0b:main"
    ),
}
_EXPECTED_REMAINING_BLOCKERS = {
    "forbidden_behavior_area": 7,
    "shared_infrastructure_not_cli": 1,
    "scaffold_forbidden_term_collision": 1,
}


def _run_help(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize(("module_name", "expected_lines"), _TARGETS)
def test_old_root_files_removed_and_replacements_exist(module_name: str, expected_lines: int) -> None:
    old_path = _ROOT / f"{module_name}.py"
    new_path = _LEGACY_CLI_ROOT / f"{module_name}.py"
    assert not old_path.exists(), str(old_path)
    assert new_path.exists(), str(new_path)
    assert len(new_path.read_text(encoding="utf-8").splitlines()) >= expected_lines


@pytest.mark.parametrize("module_name", _TARGET_MODULES)
def test_old_root_module_imports_fail(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"{_PACKAGE}.{module_name}")


@pytest.mark.parametrize(("module_name", "expected_lines"), _TARGETS)
def test_replacement_module_imports_and_help_match_baseline(module_name: str, expected_lines: int) -> None:
    module = importlib.import_module(f"{_PACKAGE}.runners.legacy_cli.{module_name}")
    assert module is not None
    result = _run_help(f"{_PACKAGE}.runners.legacy_cli.{module_name}")
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == expected_lines
    baseline = Path(f"/tmp/a_{module_name}_old_help.txt").read_text(encoding="utf-8")
    assert result.stdout == baseline
    assert "usage" in result.stdout.lower()


def test_runner_specs_point_to_replacement_modules() -> None:
    specs = {spec.key: spec for spec in iter_runner_specs()}
    assert len(specs) == 5
    for key, cli_module in _EXPECTED_RUNNER_MODULES.items():
        assert specs[key].cli_module == cli_module


def test_cli_specs_point_to_replacement_modules_and_callables() -> None:
    specs = {spec.key: spec for spec in iter_runner_cli_specs()}
    assert len(specs) == 4
    for key, entry_module in _EXPECTED_CLI_ENTRY_MODULES.items():
        assert specs[key].entry_module == entry_module
    for key, main_callable in _EXPECTED_CLI_MAIN_CALLABLES.items():
        assert specs[key].main_callable == main_callable
    assert specs["hyperliquid_cost_feasibility"].main_callable is None
    assert specs["generic_altcoin_stress_regime_ablation_phase0a"].main_callable is None


def test_node_fills_still_has_no_cli_spec() -> None:
    keys = {spec.key for spec in iter_runner_cli_specs()}
    assert "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0" not in keys


def test_counts_and_catalog_state_match_track_a_expectations() -> None:
    root_run_files = sorted(_ROOT.glob("run_*.py"))
    moved_run_files = sorted(_LEGACY_CLI_ROOT.glob("run_*.py"))
    primary_entries = tuple(
        spec for spec in iter_legacy_entrypoints() if Path(spec.path).name == f"{spec.key}.py"
    )
    retained_primary = tuple(spec for spec in primary_entries if not spec.root_removed)
    retained_blockers = Counter(spec.blocker for spec in retained_primary if spec.blocker)

    assert len(root_run_files) == 9
    assert len(moved_run_files) == 56
    assert retained_blockers["metadata_pinned_cli_module"] == 0
    assert retained_blockers["shared_infrastructure_not_cli"] == 1
    assert retained_blockers["watcher_systemd_subprocess_coupling"] == 0
    assert retained_blockers["forbidden_behavior_area"] == 7
    assert retained_blockers["scaffold_forbidden_term_collision"] == 1

def test_legacy_entrypoint_catalog_marks_track_a_targets_as_root_removed() -> None:
    for module_name in _TARGET_MODULES:
        spec = get_legacy_entrypoint(module_name)
        assert spec is not None
        assert spec.root_removed is True
        assert spec.replacement_module == f"{_PACKAGE}.runners.legacy_cli.{module_name}"
        assert spec.path == (
            "examples/strategies/venue_agnostic_signal_observer/runners/legacy_cli/"
            f"{module_name}.py"
        )
        assert spec.old_root_path == (
            "examples/strategies/venue_agnostic_signal_observer/"
            f"{module_name}.py"
        )
        assert spec.blocker is None
