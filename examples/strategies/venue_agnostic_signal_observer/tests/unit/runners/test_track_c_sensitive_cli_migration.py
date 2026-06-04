"""Track C: paper/conductor/observer sensitive CLI migration audit.

Verifies the help-safe paper/conductor/observer CLIs were moved under
runners/legacy_cli without enabling any behavior, that --help output line
counts match the pre-move baseline, that old root module imports fail for the
moved targets while the new replacement modules import, and that the one
unsafe target (run_hyperliquid_observer: psutil import + live systemd unit)
remains retained at root with an explicit blocker.

No non-help execution path is invoked. Paper promotion stays default-deny;
conductor/observer/watcher are never started.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.legacy_entrypoints import (
    get_legacy_entrypoint,
    iter_legacy_entrypoints,
    iter_root_retained_entrypoints,
    list_actual_run_py_files,
    list_moved_run_py_files,
    validate_legacy_entrypoint_catalog,
)
from examples.strategies.venue_agnostic_signal_observer.structure.root_module_inventory import (
    validate_root_module_ledger,
)

_PACKAGE = "examples.strategies.venue_agnostic_signal_observer"
_REPO_ROOT = Path(__file__).resolve().parents[6]

# Targets migrated under runners/legacy_cli by Track C (help-safe, no live coupling).
_MOVED_TARGETS = {
    "run_conductor": 12,
    "run_hyperliquid_btc_eth_ml_atr_paper_v0": 37,
    "run_paper_promotion": 25,
    "run_paper_refalsification": 21,
    "run_signal_observer": 19,
    "run_stage2_gate_watcher": 61,
}
# Retained at root: psutil import (cannot verify --help here) AND wired into a
# live systemd unit (systemd/nautilus-hyperliquid-observer-v0.service).
_RETAINED_TARGET = "run_hyperliquid_observer"


def _run_help(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_track_c_targets_moved_and_root_removed() -> None:
    validation = validate_legacy_entrypoint_catalog()
    ledger_validation = validate_root_module_ledger()
    specs = {spec.key: spec for spec in iter_legacy_entrypoints()}

    assert validation.missing == ()
    assert validation.stale == ()
    assert ledger_validation.missing_paths == ()
    assert ledger_validation.stale_paths == ()

    for key in _MOVED_TARGETS:
        spec = specs[key]
        filename = f"{key}.py"
        assert spec.root_removed is True, key
        assert spec.blocker is None, key
        assert spec.old_root_path == f"{_PACKAGE.replace('.', '/')}/{filename}"
        assert spec.path == f"{_PACKAGE.replace('.', '/')}/runners/legacy_cli/{filename}"
        assert spec.replacement_module == f"{_PACKAGE}.runners.legacy_cli.{key}"
        assert not (_REPO_ROOT / spec.old_root_path).exists(), spec.old_root_path
        assert (_REPO_ROOT / spec.path).exists(), spec.path


def test_track_c_retained_observer_stays_blocked_at_root() -> None:
    spec = get_legacy_entrypoint(_RETAINED_TARGET)
    assert spec is not None
    assert spec.root_removed is False
    assert spec.blocker == "forbidden_behavior_area"
    assert (_REPO_ROOT / spec.path).exists()
    assert spec.path == f"{_PACKAGE.replace('.', '/')}/{_RETAINED_TARGET}.py"


def test_track_c_retained_blocker_distribution() -> None:
    counts = Counter(spec.blocker for spec in iter_root_retained_entrypoints())
    assert dict(counts) == {
        "forbidden_behavior_area": 1,
        "shared_infrastructure_not_cli": 1,
        "scaffold_forbidden_term_collision": 1,
    }


def test_track_c_counts_after_migration() -> None:
    root_run_files = list_actual_run_py_files()
    moved_run_files = list_moved_run_py_files()
    assert len(root_run_files) == 3
    assert len(moved_run_files) == 62


@pytest.mark.parametrize("key", sorted(_MOVED_TARGETS))
def test_track_c_old_root_module_import_fails(key: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"{_PACKAGE}.{key}")


@pytest.mark.parametrize("key", sorted(_MOVED_TARGETS))
def test_track_c_replacement_module_imports(key: str) -> None:
    module = importlib.import_module(f"{_PACKAGE}.runners.legacy_cli.{key}")
    assert module is not None


@pytest.mark.parametrize(("key", "expected_lines"), sorted(_MOVED_TARGETS.items()))
def test_track_c_help_matches_pre_move_line_counts(key: str, expected_lines: int) -> None:
    proc = _run_help(f"{_PACKAGE}.runners.legacy_cli.{key}")
    assert proc.returncode == 0, (key, proc.stderr)
    assert len(proc.stdout.splitlines()) == expected_lines, key
    assert "usage" in proc.stdout.lower()


def test_track_c_paper_promotion_help_is_default_deny_phrasing() -> None:
    # Help-only: must not contain any indication of live/enabled execution.
    proc = _run_help(f"{_PACKAGE}.runners.legacy_cli.run_paper_promotion")
    assert proc.returncode == 0, proc.stderr
    lowered = proc.stdout.lower()
    for forbidden in ("submit_order", "place_order", "private_key", "api_secret"):
        assert forbidden not in lowered
