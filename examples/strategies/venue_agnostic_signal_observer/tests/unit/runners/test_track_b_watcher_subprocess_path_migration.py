from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

from examples.strategies.venue_agnostic_signal_observer.runners.legacy_entrypoints import (
    iter_legacy_entrypoints,
    validate_legacy_entrypoint_catalog,
)
from examples.strategies.venue_agnostic_signal_observer.structure.root_module_inventory import (
    validate_root_module_ledger,
)

_PACKAGE = "examples.strategies.venue_agnostic_signal_observer"
_REPO_ROOT = Path(__file__).resolve().parents[6]
_WATCHER_TARGETS = (
    "run_candidate_falsification",
    "run_cost_sensitivity",
    "run_cross_capture_consistency",
    "run_derivatives_capture_campaign",
    "run_derivatives_spot_capture",
    "run_derivatives_spot_lead_lag",
    "run_hyperliquid_asset_ctxs_archive",
    "run_index",
    "run_lead_lag_heatmap",
    "run_mcpt_export",
    "run_permutation_null",
    "run_report_corpus",
)
_EXPECTED_HELP_LINES = {
    "run_candidate_falsification": 29,
    "run_cost_sensitivity": 18,
    "run_cross_capture_consistency": 19,
    "run_derivatives_capture_campaign": 53,
    "run_derivatives_spot_capture": 34,
    "run_derivatives_spot_lead_lag": 56,
    "run_hyperliquid_asset_ctxs_archive": 19,
    "run_index": 0,
    "run_lead_lag_heatmap": 39,
    "run_mcpt_export": 26,
    "run_permutation_null": 46,
    "run_report_corpus": 42,
}
_EXPECTED_SUBPROCESS_MODULES = {
    # run_stage2_gate_watcher.py was itself migrated under runners/legacy_cli by Track C.
    "examples/strategies/venue_agnostic_signal_observer/runners/legacy_cli/run_stage2_gate_watcher.py": (
        f"{_PACKAGE}.runners.legacy_cli.run_derivatives_spot_capture",
        f"{_PACKAGE}.runners.legacy_cli.run_derivatives_spot_lead_lag",
        f"{_PACKAGE}.runners.legacy_cli.run_cost_sensitivity",
        f"{_PACKAGE}.runners.legacy_cli.run_lead_lag_heatmap",
        f"{_PACKAGE}.runners.legacy_cli.run_permutation_null",
        f"{_PACKAGE}.runners.legacy_cli.run_mcpt_export",
        f"{_PACKAGE}.runners.legacy_cli.run_cross_capture_consistency",
        f"{_PACKAGE}.runners.legacy_cli.run_candidate_falsification",
    ),
    "examples/strategies/venue_agnostic_signal_observer/stage2_gate_watcher.py": (
        f"{_PACKAGE}.runners.legacy_cli.run_derivatives_spot_capture",
    ),
    "examples/strategies/venue_agnostic_signal_observer/stage2_readiness_check.py": (
        f"{_PACKAGE}.runners.legacy_cli.run_derivatives_capture_campaign",
        f"{_PACKAGE}.runners.legacy_cli.run_report_corpus",
        f"{_PACKAGE}.runners.legacy_cli.run_index",
    ),
    "examples/strategies/venue_agnostic_signal_observer/generic_altcoin_stress_extension_present_day_phase0.py": (
        f"{_PACKAGE}.runners.legacy_cli.run_hyperliquid_asset_ctxs_archive",
    ),
    "examples/strategies/venue_agnostic_signal_observer/runners/legacy_cli/run_derivatives_capture_campaign.py": (
        f"{_PACKAGE}.runners.legacy_cli.run_derivatives_spot_capture",
    ),
}


def _run_help(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_track_b_targets_are_moved_and_root_removed() -> None:
    validation = validate_legacy_entrypoint_catalog()
    ledger_validation = validate_root_module_ledger()
    specs = {spec.key: spec for spec in iter_legacy_entrypoints()}
    root_run_files = sorted(
        Path("examples/strategies/venue_agnostic_signal_observer").glob("run_*.py")
    )
    moved_run_files = sorted(
        Path("examples/strategies/venue_agnostic_signal_observer/runners/legacy_cli").glob("run_*.py")
    )

    assert validation.missing == ()
    assert validation.stale == ()
    assert ledger_validation.missing_paths == ()
    assert ledger_validation.stale_paths == ()
    # Global aggregate counts move as later tracks migrate more CLIs
    # (Track C migrated 6 paper/conductor/observer CLIs: root 9->3, moved 56->62).
    assert len(root_run_files) == 6
    assert len(moved_run_files) == 59

    for key in _WATCHER_TARGETS:
        spec = specs[key]
        filename = f"{key}.py"
        assert spec.root_removed is True, key
        assert spec.blocker is None, key
        assert spec.old_root_path == f"examples/strategies/venue_agnostic_signal_observer/{filename}"
        assert spec.path == (
            "examples/strategies/venue_agnostic_signal_observer/"
            f"runners/legacy_cli/{filename}"
        )
        assert spec.old_root_path is not None
        assert spec.path is not None
        assert spec.replacement_module == f"{_PACKAGE}.runners.legacy_cli.{key}"
        assert not (_REPO_ROOT / spec.old_root_path).exists(), spec.old_root_path
        assert (_REPO_ROOT / spec.path).exists(), spec.path


def test_track_b_retained_blocker_counts_are_reduced_to_non_watcher_entries() -> None:
    primary_specs = tuple(
        spec
        for spec in iter_legacy_entrypoints()
        if Path(spec.path).name == f"{spec.key}.py"
    )
    retained_primary = tuple(spec for spec in primary_specs if not spec.root_removed)
    retained_blockers = Counter(spec.blocker for spec in retained_primary if spec.blocker)
    assert dict(retained_blockers) == {
        "forbidden_behavior_area": 1,
        "shared_infrastructure_not_cli": 1,
        "scaffold_forbidden_term_collision": 4,
    }


def test_track_b_subprocess_callers_use_legacy_cli_modules_only() -> None:
    for rel_path, expected_modules in _EXPECTED_SUBPROCESS_MODULES.items():
        text = (_REPO_ROOT / rel_path).read_text(encoding="utf-8")
        for module in expected_modules:
            assert module in text, (rel_path, module)
        for key in _WATCHER_TARGETS:
            old_module = f"{_PACKAGE}.{key}"
            assert old_module not in text, (rel_path, old_module)


def test_track_b_help_smoke_matches_pre_move_line_counts() -> None:
    for key, expected_line_count in _EXPECTED_HELP_LINES.items():
        module = f"{_PACKAGE}.runners.legacy_cli.{key}"
        proc = _run_help(module)
        assert proc.returncode == 0, (key, proc.stderr)
        assert len(proc.stdout.splitlines()) == expected_line_count, key
