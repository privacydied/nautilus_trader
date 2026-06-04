from __future__ import annotations

import importlib
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
    ALL_RUNNER_CLI_SPECS,
)
from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli import dispatcher
from examples.strategies.venue_agnostic_signal_observer.runners.legacy_entrypoints import (
    get_legacy_entrypoint,
    iter_legacy_entrypoints,
    iter_root_retained_entrypoints,
    list_actual_run_py_files,
    list_moved_run_py_files,
    validate_legacy_entrypoint_catalog,
)
from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
    get_runner_spec,
    iter_runner_specs,
)

_PACKAGE = "examples.strategies.venue_agnostic_signal_observer"
_REPO_ROOT = Path(__file__).resolve().parents[6]
_LEGACY_CLI_MODULE_PREFIX = f"{_PACKAGE}.runners.legacy_cli."
_ROOT_MODULE_PREFIX = f"{_PACKAGE}."
_ROOT_PREFIX = "examples/strategies/venue_agnostic_signal_observer/"
_EXPECTED_RETAINED_BLOCKERS = {
    "shared_infrastructure_not_cli": 1,
    "forbidden_behavior_area": 1,
    "scaffold_forbidden_term_collision": 4,
}
_METADATA_PINNED_ROOT_FILES = (
    "run_generic_altcoin_stress_regime_ablation_phase0a.py",
    "run_generic_altcoin_stress_regime_ablation_phase0b.py",
    "run_hyperliquid_cost_feasibility.py",
    "run_hyperliquid_funding_divergence_phase0.py",
    "run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0.py",
    "run_hyperliquid_oi_velocity_compression_phase0.py",
)
_RETAINED_HELP_EXPECTATIONS = {
    "generic_altcoin_stress_regime_ablation_phase0a": (18, "Phase0A"),
    "generic_altcoin_stress_regime_ablation_phase0b": (15, "Phase0B"),
    "hyperliquid_oi_velocity_compression_phase0": (14, "OI"),
    "hyperliquid_cost_feasibility": (22, "cost-feasibility"),
    "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0": (84, "node-fills"),
    "hyperliquid_funding_divergence_phase0": (17, "funding divergence"),
}
_MOVED_HELP_SAFE_KEYS = (
    "run_lead_lag",
    "run_tick_lead_lag",
    "run_dex_cex_dislocation",
    "run_liquidation_overshoot_snapback_phase0a",
    "run_hyperliquid_s3_archive",
)
_ALLOWED_RETAINED_ROOT_MODULE_REFERENCES = {
    f"{_PACKAGE}.run_derivatives_spot_capture",
    f"{_PACKAGE}.run_derivatives_spot_lead_lag",
    f"{_PACKAGE}.run_permutation_null",
    f"{_PACKAGE}.run_lead_lag_heatmap",
    f"{_PACKAGE}.run_cost_sensitivity",
    f"{_PACKAGE}.run_report_corpus",
    f"{_PACKAGE}.run_artifacts",
    f"{_PACKAGE}.run_index",
    f"{_PACKAGE}.run_hyperliquid_asset_ctxs_archive",
    f"{_PACKAGE}.run_conductor",
    f"{_PACKAGE}.run_paper_promotion",
    f"{_PACKAGE}.run_paper_refalsification",
    f"{_PACKAGE}.run_hip3_builder_dex_tradfi_forward_recorder_v0",
}
_ALLOWED_REFERENCE_PATH_PREFIXES = (
    "examples/strategies/venue_agnostic_signal_observer/docs/",
    "examples/strategies/venue_agnostic_signal_observer/discovery/",
)
_ALLOWED_REFERENCE_FILES = {
    "examples/strategies/venue_agnostic_signal_observer/structure/root_module_ledger.v0.json",
    "examples/strategies/venue_agnostic_signal_observer/runners/legacy_entrypoints.py",
    "examples/strategies/venue_agnostic_signal_observer/runners/cli_specs.py",
    "examples/strategies/venue_agnostic_signal_observer/tests/unit/runners/test_unit29_legacy_cli_compatibility_sweep.py",
    "examples/strategies/venue_agnostic_signal_observer/tests/test_run_hip3_builder_dex_tradfi_forward_recorder_v0.py",
    "examples/strategies/venue_agnostic_signal_observer/tests/unit/runners/test_runner_registry.py",
    "examples/strategies/venue_agnostic_signal_observer/tests/unit/hypotheses/hyperliquid/test_hyperliquid_cost_feasibility_contract.py",
    "examples/strategies/venue_agnostic_signal_observer/tests/unit/hypotheses/hyperliquid/test_hyperliquid_funding_divergence_phase0_package_contract.py",
    "examples/strategies/venue_agnostic_signal_observer/tests/unit/hypotheses/legacy/test_generic_altcoin_stress_regime_ablation_phase0a_package_contract.py",
    "examples/strategies/venue_agnostic_signal_observer/tests/unit/hypotheses/legacy/test_generic_altcoin_stress_regime_ablation_phase0b_package_contract.py",
    "examples/strategies/venue_agnostic_signal_observer/tests/unit/runners/test_unit10_oi_velocity_callable_cli.py",
}


def _run_help(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run_module(module: str, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *argv],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _moved_primary_specs() -> tuple:
    moved_paths = set(list_moved_run_py_files())
    return tuple(
        spec
        for spec in iter_legacy_entrypoints()
        if spec.path in moved_paths and Path(spec.path).name == f"{spec.key}.py"
    )


def _retained_primary_specs() -> tuple:
    return tuple(iter_root_retained_entrypoints())


def _scan_old_root_references() -> list[tuple[str, str]]:
    allowed_root_modules = tuple(sorted(_ALLOWED_RETAINED_ROOT_MODULE_REFERENCES))
    scanned_roots = [
        _REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer/tests",
        _REPO_ROOT / "tests",
        _REPO_ROOT / ".github",
        _REPO_ROOT / "scripts",
    ]
    explicit_files = [
        _REPO_ROOT / "pyproject.toml",
        _REPO_ROOT / "Makefile",
        _REPO_ROOT / "justfile",
    ]
    violations: list[tuple[str, str]] = []
    for root in scanned_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in {".py", ".md", ".json", ".toml", ".yml", ".yaml", ""}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            rel = path.relative_to(_REPO_ROOT).as_posix()
            for line in text.splitlines():
                if f"{_PACKAGE}.run_" not in line:
                    continue
                if rel in _ALLOWED_REFERENCE_FILES or rel.startswith(_ALLOWED_REFERENCE_PATH_PREFIXES):
                    continue
                modules = [token.rstrip("\"' ,:)\\") for token in line.split() if f"{_PACKAGE}.run_" in token]
                for module in modules:
                    module = module[module.find(_PACKAGE):]
                    if module in allowed_root_modules:
                        continue
                    violations.append((rel, module))
    for path in explicit_files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for line in text.splitlines():
            if f"{_PACKAGE}.run_" not in line:
                continue
            modules = [token.rstrip("\"' ,:)\\") for token in line.split() if f"{_PACKAGE}.run_" in token]
            for module in modules:
                module = module[module.find(_PACKAGE):]
                if module in allowed_root_modules:
                    continue
                violations.append((rel, module))
    return violations


def test_catalog_counts_and_blocker_categories_match_unit28_surface() -> None:
    validation = validate_legacy_entrypoint_catalog()
    retained = _retained_primary_specs()
    moved = _moved_primary_specs()
    blocker_counts = Counter(spec.blocker for spec in retained)

    assert not validation.missing, validation.missing
    assert not validation.stale, validation.stale
    assert len(moved) == 59
    assert len(retained) == 6
    assert dict(blocker_counts) == _EXPECTED_RETAINED_BLOCKERS


def test_track_a_flagship_clis_are_moved_under_legacy_cli() -> None:
    for filename in _METADATA_PINNED_ROOT_FILES:
        key = filename.removesuffix(".py")
        spec = get_legacy_entrypoint(key)
        assert spec is not None, key
        assert spec.root_removed is True, key
        assert spec.blocker is None, key
        assert spec.path == f"{_ROOT_PREFIX}runners/legacy_cli/{filename}"
        assert spec.old_root_path == f"{_ROOT_PREFIX}{filename}"
        assert spec.replacement_module == f"{_PACKAGE}.runners.legacy_cli.{key}"
        assert not (_REPO_ROOT / spec.old_root_path).exists(), spec.old_root_path
        assert (_REPO_ROOT / spec.path).exists(), spec.path


def test_moved_entries_have_replacement_modules_and_old_root_is_gone() -> None:
    help_safe_keys = set(_MOVED_HELP_SAFE_KEYS)
    for spec in _moved_primary_specs():
        assert spec.root_removed is True, spec.key
        assert spec.path.startswith(f"{_ROOT_PREFIX}runners/legacy_cli/run_"), spec.path
        assert spec.module.startswith(_LEGACY_CLI_MODULE_PREFIX + "run_"), spec.module
        assert spec.old_root_path is not None
        assert spec.old_root_path.startswith(f"{_ROOT_PREFIX}run_"), spec.old_root_path
        assert not (_REPO_ROOT / spec.old_root_path).exists(), spec.old_root_path
        assert (_REPO_ROOT / spec.path).exists(), spec.path
        if spec.key in help_safe_keys:
            module = importlib.import_module(spec.module)
            assert module is not None


def test_retained_root_entries_still_exist_with_blockers_and_notes() -> None:
    for spec in _retained_primary_specs():
        assert spec.root_removed is False, spec.key
        assert (_REPO_ROOT / spec.path).exists(), spec.path
        assert spec.old_root_path is None or spec.old_root_path == spec.path
        assert spec.replacement_module is None or spec.replacement_module == ""
        assert spec.blocker
        assert spec.notes is not None


def test_registry_backed_cli_modules_intentionally_use_track_a_legacy_cli_paths() -> None:
    expected = {
        "hyperliquid_cost_feasibility": f"{_PACKAGE}.runners.legacy_cli.run_hyperliquid_cost_feasibility",
        "hyperliquid_oi_velocity_compression_phase0": f"{_PACKAGE}.runners.legacy_cli.run_hyperliquid_oi_velocity_compression_phase0",
        "generic_altcoin_stress_regime_ablation_phase0b": f"{_PACKAGE}.runners.legacy_cli.run_generic_altcoin_stress_regime_ablation_phase0b",
        "generic_altcoin_stress_regime_ablation_phase0a": f"{_PACKAGE}.runners.legacy_cli.run_generic_altcoin_stress_regime_ablation_phase0a",
        "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0": f"{_PACKAGE}.runners.legacy_cli.run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
    }
    for key, cli_module in expected.items():
        spec = get_runner_spec(key)
        assert spec is not None, key
        assert spec.cli_module == cli_module

    funding = get_legacy_entrypoint("run_hyperliquid_funding_divergence_phase0")
    assert funding is not None
    assert funding.root_removed is True
    assert funding.blocker is None
    assert len(iter_runner_specs()) == 5
    assert len(ALL_RUNNER_CLI_SPECS) == 4


def test_track_a_migrated_help_outputs_work_from_legacy_cli() -> None:
    for key, (line_count, _label) in _RETAINED_HELP_EXPECTATIONS.items():
        proc = _run_help(f"{_LEGACY_CLI_MODULE_PREFIX}run_{key}")
        assert proc.returncode == 0, (key, proc.stderr)
        assert len(proc.stdout.splitlines()) == line_count, key


@pytest.mark.parametrize("key", _MOVED_HELP_SAFE_KEYS)
def test_moved_representative_replacement_help_outputs_work(key: str) -> None:
    old_root_module = f"{_ROOT_MODULE_PREFIX}{key}"
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(old_root_module)

    spec = get_legacy_entrypoint(key)
    assert spec is not None
    assert spec.root_removed is True
    assert spec.help_safe is True
    proc = _run_help(spec.module)
    assert proc.returncode == 0, (key, proc.stderr)
    assert "usage" in proc.stdout.lower(), key


def test_dispatcher_remains_help_only_and_import_light() -> None:
    before = {name for name in sys.modules if ".runners.legacy_cli.run_" in name}
    listed = _run_module(f"{_LEGACY_CLI_MODULE_PREFIX}dispatcher", "--list")
    assert listed.returncode == 0
    after = {name for name in sys.modules if ".runners.legacy_cli.run_" in name}
    assert before == after

    with pytest.raises(SystemExit):
        dispatcher.main(["--key", "definitely-not-real"])

    with pytest.raises(SystemExit):
        dispatcher.main([
            "--key",
            "hyperliquid_oi_velocity_compression_phase0",
        ])

    arbitrary = _run_module(
        f"{_LEGACY_CLI_MODULE_PREFIX}dispatcher",
        "--key",
        "hyperliquid_oi_velocity_compression_phase0",
        "--",
        "--seed",
        "1",
    )
    assert arbitrary.returncode != 0 or arbitrary.stdout == ""

    with pytest.raises(SystemExit):
        dispatcher.main(["--key", "hyperliquid_oi_velocity_compression_phase0", "--", "--seed", "1"])


def test_repo_owned_old_root_references_are_controlled() -> None:
    violations = _scan_old_root_references()
    assert not violations, violations


def test_no_root_count_zero_claim_root_count_is_6_by_design() -> None:
    root_files = list_actual_run_py_files()
    moved_files = list_moved_run_py_files()
    assert len(root_files) == 6
    assert len(moved_files) == 59
    assert len(root_files) != 0


def test_no_retained_blocked_root_file_is_duplicated_under_legacy_cli() -> None:
    retained_names = {Path(spec.path).name for spec in _retained_primary_specs()}
    moved_names = {Path(path).name for path in list_moved_run_py_files()}
    assert not retained_names & moved_names
