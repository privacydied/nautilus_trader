from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
    get_runner_spec,
)

LEGACY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hyperliquid_oi_velocity_compression_phase0"
)
PACKAGE_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.hyperliquid.oi_velocity_compression_phase0"
)
RUNNER_MODULE = f"{PACKAGE_MODULE}.runner"
METADATA_MODULE = f"{PACKAGE_MODULE}.metadata"
COMPAT_MODULE = f"{PACKAGE_MODULE}.compat"
CLI_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "run_hyperliquid_oi_velocity_compression_phase0"
)
REPO_ROOT = Path(__file__).resolve().parents[7]
LEGACY_PATH = (
    REPO_ROOT
    / "examples/strategies/venue_agnostic_signal_observer"
    / "hyperliquid_oi_velocity_compression_phase0.py"
)
PACKAGE_INIT_PATH = (
    REPO_ROOT
    / "examples/strategies/venue_agnostic_signal_observer"
    / "hypotheses/hyperliquid/oi_velocity_compression_phase0/__init__.py"
)
METADATA_PATH = (
    REPO_ROOT
    / "examples/strategies/venue_agnostic_signal_observer"
    / "hypotheses/hyperliquid/oi_velocity_compression_phase0/metadata.py"
)
HELP_BASELINE = REPO_ROOT / "/tmp/hyperliquid_oi_velocity_help_before_unit9.txt"
EXPECTED_LEGACY_SYMBOLS = (
    "FROZEN_SYMBOLS",
    "PHASE0_READY_FOR_V1_PRECOMMITMENT",
    "TERMINAL_VERDICTS",
    "PRICE_FIELD",
    "SECOND_DIRECTION_PROXY",
    "WARMUP_DAYS",
    "HORIZONS_H",
    "Phase0Config",
    "CoverageResult",
    "parse_ts",
    "compute_symbol_list_hash",
    "sha256_file",
    "verify_precommitment_hash",
    "_module_names_from_ast",
    "enforce_funding_quarantine",
    "_read_rows",
    "_symbol_path",
    "_float_field",
    "load_symbol_frame",
    "inspect_symbol_coverage",
    "compute_oi_velocity_bps",
    "compute_realized_vol_bps",
    "compute_past_percentile_ranks",
    "select_first_wins_events",
    "_build_symbol_events",
    "evaluate_phase0b",
    "_median",
    "evaluate_phase0c",
    "_git_metadata",
    "_write_csv",
    "_jsonable",
    "run_phase0_pipeline",
)


def _reload_module(module_name: str):
    for name in (
        LEGACY_MODULE,
        PACKAGE_MODULE,
        COMPAT_MODULE,
        METADATA_MODULE,
        RUNNER_MODULE,
        CLI_MODULE,
    ):
        if module_name == name or module_name in {
            LEGACY_MODULE,
            PACKAGE_MODULE,
            COMPAT_MODULE,
            METADATA_MODULE,
            RUNNER_MODULE,
        }:
            sys.modules.pop(name, None)
    return importlib.import_module(module_name)


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _help_line_count() -> int:
    result = subprocess.run(
        [sys.executable, "-m", CLI_MODULE, "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return len(result.stdout.splitlines())


def test_metadata_imports_without_importing_runner() -> None:
    result = _run_python(
        f"import importlib, sys; importlib.import_module('{METADATA_MODULE}'); "
        f"raise SystemExit(1 if '{RUNNER_MODULE}' in sys.modules else 0)"
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_package_root_imports_without_importing_runner() -> None:
    result = _run_python(
        f"import importlib, sys; importlib.import_module('{PACKAGE_MODULE}'); "
        f"raise SystemExit(1 if '{RUNNER_MODULE}' in sys.modules else 0)"
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_root_wrapper_imports_and_exposes_required_symbols() -> None:
    legacy = _reload_module(LEGACY_MODULE)
    missing = [name for name in EXPECTED_LEGACY_SYMBOLS if not hasattr(legacy, name)]
    assert missing == []


def test_compat_exports_resolve_to_runner_objects() -> None:
    _reload_module(RUNNER_MODULE)
    compat = importlib.import_module(COMPAT_MODULE)
    runner = importlib.import_module(RUNNER_MODULE)
    assert compat.__all__ == EXPECTED_LEGACY_SYMBOLS
    for name in EXPECTED_LEGACY_SYMBOLS:
        assert getattr(compat, name) is getattr(runner, name)


def test_old_cli_import_still_works() -> None:
    _reload_module(CLI_MODULE)
    cli = importlib.import_module(CLI_MODULE)
    legacy = importlib.import_module(LEGACY_MODULE)
    assert cli.Phase0Config is legacy.Phase0Config
    assert cli.run_phase0_pipeline is legacy.run_phase0_pipeline


def test_old_cli_help_subprocess_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", CLI_MODULE, "--help"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == ""
    assert "Observer-only Hyperliquid OI velocity compression Phase 0" in result.stdout


def test_old_cli_help_output_matches_pre_move_baseline_line_count() -> None:
    assert _help_line_count() == 14


def test_runner_module_imports_directly() -> None:
    runner = _reload_module(RUNNER_MODULE)
    assert runner.__name__ == RUNNER_MODULE


def test_root_wrapper_remains_small() -> None:
    assert len(LEGACY_PATH.read_text(encoding="utf-8").splitlines()) <= 6


def test_root_wrapper_has_no_algorithmic_class_or_function_definitions() -> None:
    tree = ast.parse(LEGACY_PATH.read_text(encoding="utf-8"))
    defs = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert defs == []


def test_metadata_has_no_runner_import() -> None:
    tree = ast.parse(METADATA_PATH.read_text(encoding="utf-8"))
    imports = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert not any(name.endswith(".runner") for name in imports)


def test_package_root_has_no_runner_import() -> None:
    tree = ast.parse(PACKAGE_INIT_PATH.read_text(encoding="utf-8"))
    imports = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert not any(name.endswith(".runner") for name in imports)


def test_no_fake_init_py_files_exist() -> None:
    bad = [
        path
        for path in (
            REPO_ROOT / "examples/strategies/venue_agnostic_signal_observer"
        ).rglob("init.py")
    ]
    assert bad == []


def test_metadata_fields_match_expected_contract() -> None:
    metadata_mod = _reload_module(METADATA_MODULE)
    metadata = metadata_mod.METADATA
    assert metadata.key == "hyperliquid_oi_velocity_compression_phase0"
    assert metadata.family == "oi_velocity_compression"
    assert metadata.venue == "hyperliquid"
    assert metadata.cli_module == CLI_MODULE
    assert metadata.implementation_module == RUNNER_MODULE
    assert metadata.package_module == PACKAGE_MODULE
    assert metadata.legacy_module == LEGACY_MODULE


def test_registry_entry_matches_manual_unit9c_admission() -> None:
    spec = get_runner_spec("hyperliquid_oi_velocity_compression_phase0")
    assert spec is not None
    assert spec.key == "hyperliquid_oi_velocity_compression_phase0"
    assert spec.family == "oi_velocity_compression"
    assert spec.venue == "hyperliquid"
