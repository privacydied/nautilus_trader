from __future__ import annotations

import ast
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.structure.root_module_inventory import (
    iter_entries_by_category,
    iter_entries_by_status,
    load_root_module_ledger,
    validate_root_module_ledger,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[6]


def _source(root_path: str) -> str:
    return (_repo_root() / root_path).read_text(encoding="utf-8")


def test_all_packaged_wrapper_root_files_exist_and_are_small_enough() -> None:
    for entry in iter_entries_by_status("already_packaged_wrapper"):
        source = _source(entry.root_path)
        assert len(source.splitlines()) <= 60, entry.root_path


def test_packaged_wrapper_root_files_contain_no_classes_or_functions() -> None:
    allowlist = {"tests/unit/structure/test_root_wrapper_policy.py"}
    for entry in iter_entries_by_status("already_packaged_wrapper"):
        if entry.root_path in allowlist:
            continue
        tree = ast.parse(_source(entry.root_path))
        assert not [
            node
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]


_ALLOWED_WRAPPER_IMPORT_FRAGMENTS: tuple[str, ...] = (
    ".compat import",
    "compat import",
    "hypotheses.hyperliquid.node_fills_liq_reconstruction import *",
)


def _is_compatibility_import_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    if "import" not in stripped:
        return False
    if stripped.startswith("import ") and stripped[7:].split()[0] == "os":
        return False
    return True


def test_packaged_wrapper_root_files_import_from_compat_or_package() -> None:
    for entry in iter_entries_by_status("already_packaged_wrapper"):
        source = _source(entry.root_path)
        lines = [line for line in source.splitlines() if _is_compatibility_import_line(line)]
        assert lines, f"{entry.root_path} does not contain any import lines"
        assert any(
            any(fragment in line for fragment in _ALLOWED_WRAPPER_IMPORT_FRAGMENTS)
            for line in lines
        ), {
            "root_path": entry.root_path,
            "import_lines": lines,
        }


def test_packaged_wrapper_fields_are_complete() -> None:
    for entry in iter_entries_by_status("already_packaged_wrapper"):
        assert entry.target_package
        assert entry.compatibility_wrapper_required is True


def test_canonical_legacy_cli_files_start_with_run() -> None:
    for entry in iter_entries_by_category("canonical_cli"):
        assert Path(entry.root_path).name.startswith("run_"), entry.root_path


def test_canonical_legacy_cli_is_not_classified_as_hypothesis_module() -> None:
    canonical_paths = {entry.root_path for entry in iter_entries_by_category("canonical_cli")}
    for entry in iter_entries_by_category("hypothesis_module"):
        assert entry.root_path not in canonical_paths


def test_move_to_hypothesis_candidate_entries_have_paired_cli_or_note() -> None:
    for entry in iter_entries_by_status("move_to_hypothesis_candidate"):
        assert entry.paired_cli or entry.notes.strip(), entry.root_path


def test_manual_review_entries_have_non_empty_notes() -> None:
    for entry in iter_entries_by_status("manual_review_required"):
        assert entry.notes.strip(), entry.root_path


def test_quarantine_review_entries_have_non_empty_notes() -> None:
    for entry in iter_entries_by_status("quarantine_review_candidate"):
        assert entry.notes.strip(), entry.root_path


def test_root_marker_files_are_classified_as_package_marker_or_entrypoint_support() -> None:
    marker_names = {"__init__.py", "__main__.py"}
    entries = load_root_module_ledger()
    marker_entries = [entry for entry in entries if Path(entry.root_path).name in marker_names]
    assert marker_entries
    allowed_categories = {"package_marker", "runner_or_entrypoint_support"}
    for entry in marker_entries:
        assert entry.category in allowed_categories, entry.root_path


def test_no_new_root_py_appears_without_ledger_update() -> None:
    validation = validate_root_module_ledger()
    assert not validation.missing_paths, validation.missing_paths


def test_known_packaged_wrappers_pass_policy() -> None:
    wrappers = {entry.module_name: entry for entry in iter_entries_by_status("already_packaged_wrapper")}
    expected_keys = [
        "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
        "hyperliquid_cost_feasibility",
        "hyperliquid_oi_velocity_compression_phase0",
        "hyperliquid_funding_divergence_phase0",
    ]
    for key in expected_keys:
        assert key in wrappers, key
        entry = wrappers[key]
        assert entry.category == "legacy_compat_wrapper"
        assert entry.status == "already_packaged_wrapper"
        assert entry.target_package
