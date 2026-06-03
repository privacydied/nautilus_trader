"""Unit 28: root run_* entrypoint removal audit.

Verifies the 44 safely-migrated run_*.py files now live under
runners/legacy_cli/, that the legacy entrypoint catalog records the migration
(replacement module + old root path), that root-retained files carry explicit
safety blockers, and that old root module paths are intentionally gone.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.legacy_entrypoints import (
    get_legacy_entrypoint,
    iter_legacy_entrypoints,
    iter_root_retained_entrypoints,
    list_actual_run_py_files,
    list_moved_run_py_files,
)

_PKG = "examples.strategies.venue_agnostic_signal_observer"
_LEGACY_CLI_PREFIX = "examples/strategies/venue_agnostic_signal_observer/runners/legacy_cli/run_"
_LEGACY_CLI_MODULE_PREFIX = f"{_PKG}.runners.legacy_cli.run_"

# Files intentionally retained at root with their blocker class.
_EXPECTED_RETAINED_BLOCKERS = {
    "shared_infrastructure_not_cli": 1,
    "forbidden_behavior_area": 7,
    "scaffold_forbidden_term_collision": 1,
}
_MOVED_HELP_SAFE_SAMPLE = ("run_lead_lag", "run_tick_lead_lag", "run_dex_cex_dislocation")
_REMOVED_OLD_ROOT_MODULES = (
    f"{_PKG}.run_lead_lag",
    f"{_PKG}.run_dex_cex_dislocation",
)


def test_moved_files_live_only_under_legacy_cli() -> None:
    moved = list_moved_run_py_files()
    assert len(moved) == 56, moved
    for path in moved:
        assert path.startswith(_LEGACY_CLI_PREFIX), path


def test_root_run_files_are_all_safety_blocked() -> None:
    root = list_actual_run_py_files()
    # Root removal did NOT go to zero by design: every remaining file is blocked.
    assert root  # non-empty by design
    retained = iter_root_retained_entrypoints()
    assert len(retained) == len(root) == 9, (len(retained), len(root))
    for spec in retained:
        assert spec.root_removed is False, spec.key
        assert spec.blocker is not None, spec.key


def test_retained_blocker_distribution_matches_expected() -> None:
    from collections import Counter

    counts = Counter(spec.blocker for spec in iter_root_retained_entrypoints())
    assert dict(counts) == _EXPECTED_RETAINED_BLOCKERS, dict(counts)


def test_moved_catalog_entries_point_to_legacy_cli_with_provenance() -> None:
    moved_paths = set(list_moved_run_py_files())
    for spec in iter_legacy_entrypoints():
        if spec.path not in moved_paths:
            continue
        assert spec.root_removed is True, spec.key
        assert spec.module.startswith(_LEGACY_CLI_MODULE_PREFIX), spec.module
        assert spec.path.startswith(_LEGACY_CLI_PREFIX), spec.path
        assert spec.replacement_module == spec.module, spec.key
        assert spec.old_root_path is not None
        assert spec.old_root_path.startswith(
            "examples/strategies/venue_agnostic_signal_observer/run_"
        ), spec.old_root_path
        assert spec.blocker is None, spec.key


def test_registry_and_spec_backed_clis_remain_at_root() -> None:
    # Track A intentionally migrated the six metadata-pinned flagship CLIs.
    for key in (
        "run_hyperliquid_cost_feasibility",
        "run_hyperliquid_oi_velocity_compression_phase0",
        "run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
        "run_generic_altcoin_stress_regime_ablation_phase0a",
        "run_generic_altcoin_stress_regime_ablation_phase0b",
        "run_hyperliquid_funding_divergence_phase0",
    ):
        spec = get_legacy_entrypoint(key)
        assert spec is not None, key
        assert spec.root_removed is True, key
        assert spec.blocker is None, key
        assert spec.path.startswith(_LEGACY_CLI_PREFIX), key
        assert spec.module.startswith(_LEGACY_CLI_MODULE_PREFIX), key
        assert spec.old_root_path is not None, key


def test_cost_feasibility_keeps_registry_and_spec_keys() -> None:
    spec = get_legacy_entrypoint("run_hyperliquid_cost_feasibility")
    assert spec is not None
    assert spec.registry_key == "hyperliquid_cost_feasibility"
    assert spec.cli_spec_key == "hyperliquid_cost_feasibility"


@pytest.mark.parametrize("module_name", _REMOVED_OLD_ROOT_MODULES)
def test_old_root_module_paths_are_gone(module_name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


@pytest.mark.parametrize("run_name", _MOVED_HELP_SAFE_SAMPLE)
def test_moved_replacement_modules_import(run_name: str) -> None:
    module = importlib.import_module(f"{_LEGACY_CLI_MODULE_PREFIX[:-4]}{run_name}")
    assert module is not None


def test_legacy_catalog_import_does_not_import_moved_cli_modules() -> None:
    import sys

    before = {n for n in sys.modules if ".runners.legacy_cli.run_" in n}
    importlib.import_module(f"{_PKG}.runners.legacy_entrypoints")
    iter_legacy_entrypoints()
    after = {n for n in sys.modules if ".runners.legacy_cli.run_" in n}
    assert before == after
