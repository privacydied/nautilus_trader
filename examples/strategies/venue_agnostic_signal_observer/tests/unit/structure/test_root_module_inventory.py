from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.structure.root_module_inventory import (
    iter_entries_by_category,
    iter_entries_by_status,
    ledger_path,
    list_actual_root_py_files,
    load_root_module_ledger,
    validate_root_module_ledger,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[6]


def test_ledger_file_loads_with_orjson_and_version_zero() -> None:
    payload = ledger_path().read_bytes()
    code = (
        "import orjson\n"
        "from pathlib import Path\n"
        f"payload = Path({str(ledger_path())!r}).read_bytes()\n"
        "obj = orjson.loads(payload)\n"
        "assert obj['version'] == 0\n"
        "print(obj['version'])\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert proc.stdout.strip() == "0"
    assert payload


def test_all_entries_have_valid_category_status_and_sorted_unique_paths() -> None:
    validation = validate_root_module_ledger()
    entries = load_root_module_ledger()
    root_paths = [entry.root_path for entry in entries]
    assert not validation.invalid_categories
    assert not validation.invalid_statuses
    assert root_paths == sorted(root_paths)
    assert len(root_paths) == len(set(root_paths))


def test_all_root_paths_exist_and_every_actual_root_py_file_is_covered_once() -> None:
    validation = validate_root_module_ledger()
    entries = load_root_module_ledger()
    assert not validation.missing_paths
    assert not validation.stale_paths
    assert not validation.duplicate_paths
    actual = list_actual_root_py_files()
    assert tuple(entry.root_path for entry in entries) == actual
    for root_path in actual:
        assert (_repo_root() / root_path).exists()


def test_every_run_py_root_file_is_classified_as_canonical_cli() -> None:
    entries = load_root_module_ledger()
    run_entries = [entry for entry in entries if Path(entry.root_path).name.startswith("run_")]
    assert run_entries
    assert all(entry.category == "canonical_cli" for entry in run_entries)


def test_known_packaged_wrappers_are_classified_correctly() -> None:
    wrappers = {entry.module_name: entry for entry in iter_entries_by_status("already_packaged_wrapper")}
    node = wrappers["hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"]
    cost = wrappers["hyperliquid_cost_feasibility"]
    oi = wrappers["hyperliquid_oi_velocity_compression_phase0"]
    assert node.category == "legacy_compat_wrapper"
    assert node.registry_key == "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"
    assert node.cli_spec_key is None
    assert cost.registry_key == "hyperliquid_cost_feasibility"
    assert cost.cli_spec_key == "hyperliquid_cost_feasibility"
    assert oi.registry_key == "hyperliquid_oi_velocity_compression_phase0"
    assert oi.cli_spec_key == "hyperliquid_oi_velocity_compression_phase0"


def test_migration_candidates_have_target_package_and_wrapper_flag() -> None:
    candidates = [
        *iter_entries_by_status("move_to_hypothesis_candidate"),
        *iter_entries_by_status("move_to_core_candidate"),
        *iter_entries_by_status("move_to_data_candidate"),
        *iter_entries_by_status("move_to_venues_candidate"),
    ]
    assert candidates
    for entry in candidates:
        assert entry.target_package
        assert entry.compatibility_wrapper_required is True


def test_canonical_clis_do_not_have_target_package() -> None:
    for entry in iter_entries_by_category("canonical_cli"):
        assert entry.target_package is None


def test_manual_review_entries_have_non_empty_notes() -> None:
    manual = iter_entries_by_status("manual_review_required")
    assert manual
    assert all(entry.notes.strip() for entry in manual)


def test_ledger_load_is_dependency_light_and_does_not_import_runners() -> None:
    code = r'''
import sys
before = set(sys.modules)
from examples.strategies.venue_agnostic_signal_observer.structure.root_module_inventory import load_root_module_ledger
load_root_module_ledger()
after = set(sys.modules)
new = sorted(after - before)
bad_fragments = (
    'node_fills_liq_reconstruction.runner',
    'oi_velocity_compression_phase0.runner',
    'cost_feasibility.runner',
    '.paper',
    '.governance',
    '.conductor',
    '.bot',
    '.shadow',
    '.live',
)
hits = [name for name in new if any(fragment in name for fragment in bad_fragments)]
print('\n'.join(hits))
raise SystemExit(1 if hits else 0)
'''
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_no_run_module_imported_during_inventory_load() -> None:
    code = r'''
import sys
before = set(sys.modules)
from examples.strategies.venue_agnostic_signal_observer.structure.root_module_inventory import validate_root_module_ledger
validate_root_module_ledger()
after = set(sys.modules)
new = sorted(after - before)
hits = [name for name in new if '.run_' in name]
print('\n'.join(hits))
raise SystemExit(1 if hits else 0)
'''
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_known_wrapper_files_are_tiny_and_import_only() -> None:
    repo_root = _repo_root()
    for entry in iter_entries_by_status("already_packaged_wrapper"):
        source = (repo_root / entry.root_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert len(source.splitlines()) <= 60
        assert not [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
