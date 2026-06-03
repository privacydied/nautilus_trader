"""Unit 23: final root/refactor spine closure audit (test-only)."""

from __future__ import annotations

import subprocess
import sys

from examples.strategies.venue_agnostic_signal_observer.runners.cli_contract import (
    RunnerCliLevel,
)
from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
    ALL_RUNNER_CLI_SPECS,
)
from examples.strategies.venue_agnostic_signal_observer.runners.legacy_entrypoints import (
    iter_legacy_entrypoints,
    validate_legacy_entrypoint_catalog,
)
from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
    iter_runner_specs,
)
from examples.strategies.venue_agnostic_signal_observer.structure.root_module_inventory import (
    iter_entries_by_status,
    validate_root_module_ledger,
)


def test_root_ledger_has_no_missing_or_stale_paths():
    v = validate_root_module_ledger()
    assert v.missing_paths == ()
    assert v.stale_paths == ()
    assert v.duplicate_paths == ()
    assert v.invalid_categories == ()
    assert v.invalid_statuses == ()


def test_legacy_entrypoint_catalog_has_no_missing_or_stale():
    lv = validate_legacy_entrypoint_catalog()
    assert lv.missing == ()
    assert lv.stale == ()


def test_registered_runner_count_is_five():
    assert len(iter_runner_specs()) == 5


def test_cli_spec_count_is_four():
    assert len(ALL_RUNNER_CLI_SPECS) == 4


def test_level2_keys_exact():
    level2 = {
        spec.key
        for spec in ALL_RUNNER_CLI_SPECS
        if spec.level == RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES
    }
    assert level2 == {
        "hyperliquid_oi_velocity_compression_phase0",
        "generic_altcoin_stress_regime_ablation_phase0b",
    }


def test_level1_keys_exact():
    level1 = {
        spec.key
        for spec in ALL_RUNNER_CLI_SPECS
        if spec.level == RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA
    }
    assert level1 == {
        "hyperliquid_cost_feasibility",
        "generic_altcoin_stress_regime_ablation_phase0a",
    }


def test_node_fills_has_no_cli_spec():
    keys = {spec.key for spec in ALL_RUNNER_CLI_SPECS}
    assert (
        "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0" not in keys
    )


def test_already_packaged_wrapper_count_at_least_six():
    wrappers = iter_entries_by_status("already_packaged_wrapper")
    assert len(wrappers) >= 6


def test_phase0a_and_phase0b_are_already_packaged_wrappers():
    wrapper_modules = {
        e.module_name for e in iter_entries_by_status("already_packaged_wrapper")
    }
    assert any("phase0a" in m for m in wrapper_modules)
    assert any("phase0b" in m for m in wrapper_modules)


def test_run_py_entries_remain_canonical_legacy_entrypoints():
    canonical = iter_entries_by_status("canonical_legacy_cli")
    assert len(canonical) >= 1
    # Legacy run_*.py files remain catalogued as canonical legacy entrypoints, including moved legacy_cli files.
    run_entries = [
        e for e in canonical if e.root_path.rsplit("/", 1)[-1].startswith("run_")
    ]
    assert len(run_entries) >= 4
    assert any("runners/legacy_cli/run_" in e.root_path for e in run_entries)


def test_remaining_migration_candidates_explicit_and_nonempty():
    candidates = iter_entries_by_status("move_to_hypothesis_candidate")
    assert len(candidates) == 3
    for entry in candidates:
        assert entry.notes.strip()


def test_no_automatic_discovery_or_admission_function_names():
    import inspect

    from examples.strategies.venue_agnostic_signal_observer.runners import (
        callable_cli,
        cli_specs,
        registry,
    )

    forbidden = ("discover", "auto_register", "auto_admit", "autoload", "autoscan")
    for mod in (registry, cli_specs, callable_cli):
        for name, _obj in inspect.getmembers(mod, inspect.isfunction):
            assert not any(f in name.lower() for f in forbidden), (
                f"{mod.__name__}.{name}"
            )


def test_common_helper_import_is_dependency_light():
    code = """
import sys
before = set(sys.modules)
import examples.strategies.venue_agnostic_signal_observer.runners.callable_cli  # noqa: F401
after = set(sys.modules)
new = sorted(after - before)
forbidden = (
    ".runners.registry",
    ".runners.cli_specs",
    ".runners.registry_cli",
    "run_hyperliquid_",
    "run_generic_",
    ".hypotheses.",
    "TradingNode",
    "ExecutionClient",
)
hits = [n for n in new if any(f in n for f in forbidden)]
for h in hits:
    print(h)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", result.stdout


def test_spine_imports_have_no_bad_runtime_imports():
    code = """
import sys
before = set(sys.modules)
from examples.strategies.venue_agnostic_signal_observer.structure import root_module_inventory  # noqa
from examples.strategies.venue_agnostic_signal_observer.runners import legacy_entrypoints  # noqa
from examples.strategies.venue_agnostic_signal_observer.runners import registry  # noqa
from examples.strategies.venue_agnostic_signal_observer.runners import cli_specs  # noqa
after = set(sys.modules)
new = sorted(after - before)
forbidden = (
    "run_generic_altcoin_stress_regime_ablation_phase0a",
    "run_generic_altcoin_stress_regime_ablation_phase0b",
    "run_hyperliquid_oi_velocity_compression_phase0",
    "run_hyperliquid_cost_feasibility",
    "TradingNode",
    "ExecutionClient",
    ".registry_cli",
)
hits = [n for n in new if any(f in n for f in forbidden)]
for h in hits:
    print(h)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", result.stdout


def test_legacy_catalog_nonempty():
    assert len(iter_legacy_entrypoints()) >= 1
