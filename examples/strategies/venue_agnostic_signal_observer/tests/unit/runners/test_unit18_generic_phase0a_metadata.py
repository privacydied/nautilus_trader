"""Unit 18 focused tests.

These assert that the packaged Phase 0A study is discoverable through the manual
registry and CLI-spec metadata spine only:

* exactly one new metadata-only ``RunnerSpec``
* exactly one Level-1 help-safe ``RunnerCliSpec`` (no callables)

No execution readiness is implied. The legacy ``run_*.py`` CLI is exercised with
``--help`` only. Phase 0A must remain Level-1 (not callable) in this unit.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Iterable

from examples.strategies.venue_agnostic_signal_observer.runners.cli_contract import (
    RunnerCliLevel,
    RunnerCliRisk,
)

_REPO_ROOT = Path(__file__).resolve().parents[6]

_PHASE0A_KEY = "generic_altcoin_stress_regime_ablation_phase0a"
_PHASE0B_KEY = "generic_altcoin_stress_regime_ablation_phase0b"
_ENTRY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "runners.legacy_cli.run_generic_altcoin_stress_regime_ablation_phase0a"
)
_IMPLEMENTATION_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0a.runner"
)
_PACKAGE_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0a"
)
_LEGACY_MODULE = (
    "examples.strategies.venue_agnostic_signal_observer."
    "generic_altcoin_stress_regime_ablation_phase0a"
)
_METADATA_MODULE = _PACKAGE_MODULE + ".metadata"
_NODE_FILLS_KEY = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"

_EXPECTED_REGISTRY_KEYS = (
    "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
    "hyperliquid_cost_feasibility",
    "hyperliquid_oi_velocity_compression_phase0",
    "generic_altcoin_stress_regime_ablation_phase0b",
    "generic_altcoin_stress_regime_ablation_phase0a",
)
_EXPECTED_CLI_SPEC_KEYS = (
    "hyperliquid_cost_feasibility",
    "hyperliquid_oi_velocity_compression_phase0",
    "generic_altcoin_stress_regime_ablation_phase0b",
    "generic_altcoin_stress_regime_ablation_phase0a",
)

_EXPECTED_HELP_LINES = 18

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


def test_phase0a_runner_spec_is_registered_from_metadata() -> None:
    from examples.strategies.venue_agnostic_signal_observer.hypotheses.legacy.generic_altcoin_stress_regime_ablation_phase0a.metadata import (
        METADATA,
    )
    from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
        get_runner_spec,
        iter_runner_specs,
    )

    specs = iter_runner_specs()
    assert len(specs) == 5
    assert tuple(spec.key for spec in specs) == _EXPECTED_REGISTRY_KEYS

    spec = get_runner_spec(_PHASE0A_KEY)
    assert spec is not None
    assert spec.key == METADATA.key == _PHASE0A_KEY
    assert spec.family == METADATA.family
    assert spec.venue == METADATA.venue
    assert spec.study_id == METADATA.study_id
    assert spec.description == METADATA.description
    assert spec.cli_module == METADATA.cli_module == _ENTRY_MODULE
    assert spec.implementation_module == METADATA.implementation_module == _IMPLEMENTATION_MODULE
    assert spec.package_module == METADATA.package_module == _PACKAGE_MODULE
    assert spec.legacy_module == METADATA.legacy_module == _LEGACY_MODULE
    assert spec.tags == METADATA.tags


def test_phase0a_cli_spec_is_level1_help_safe_only() -> None:
    from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
        get_runner_cli_spec,
        iter_runner_cli_specs,
    )

    specs = iter_runner_cli_specs()
    assert len(specs) == 4
    assert tuple(spec.key for spec in specs) == _EXPECTED_CLI_SPEC_KEYS

    spec = get_runner_cli_spec(_PHASE0A_KEY)
    assert spec is not None
    assert spec.level is RunnerCliLevel.LEVEL_1_HELP_SAFE_METADATA
    assert spec.level is not RunnerCliLevel.LEVEL_2_EXPOSES_CALLABLES
    assert spec.risk is RunnerCliRisk.THIN_CLI_WITH_SAFE_HELP_ONLY
    assert spec.main_callable is None
    assert spec.build_parser_callable is None
    assert spec.registered is True
    assert spec.observer_only is True
    assert spec.supports_help_only is True
    assert spec.import_safe is True
    assert spec.writes_artifacts is False
    assert spec.requires_network is False
    assert spec.requires_archive_data is False
    assert spec.paper_or_governance_sensitive is False
    assert spec.live_or_service_sensitive is False
    assert spec.metadata_module == _METADATA_MODULE


def test_phase0a_cli_spec_matches_runner_spec() -> None:
    from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
        require_runner_cli_spec,
    )
    from examples.strategies.venue_agnostic_signal_observer.runners.registry import (
        require_runner_spec,
    )

    runner_spec = require_runner_spec(_PHASE0A_KEY)
    cli_spec = require_runner_cli_spec(_PHASE0A_KEY)
    assert cli_spec.key == runner_spec.key
    assert cli_spec.entry_module == runner_spec.cli_module
    assert cli_spec.implementation_module == runner_spec.implementation_module


def test_phase0a_help_only_subprocess_is_safe() -> None:
    before = _snapshot_help_dirs()
    result = subprocess.run(
        [sys.executable, "-m", _ENTRY_MODULE, "--help"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    after = _snapshot_help_dirs()
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == ""
    assert "usage:" in result.stdout
    assert "run_generic_altcoin_stress_regime_ablation_phase0a" in result.stdout
    assert len(result.stdout.splitlines()) == _EXPECTED_HELP_LINES
    assert before == after


def test_registry_and_cli_specs_imports_are_lazy_in_subprocess() -> None:
    code = textwrap.dedent(
        f"""
        import sys
        before = set(sys.modules)
        from examples.strategies.venue_agnostic_signal_observer.runners import registry
        from examples.strategies.venue_agnostic_signal_observer.runners import cli_specs
        after = set(sys.modules)
        new = after - before
        assert {_IMPLEMENTATION_MODULE!r} not in new, "phase0a runner imported"
        assert {_ENTRY_MODULE!r} not in new, "phase0a old CLI imported"
        registry.require_runner_spec({_PHASE0A_KEY!r})
        cli_specs.require_runner_cli_spec({_PHASE0A_KEY!r})
        after2 = set(sys.modules)
        new2 = after2 - before
        assert {_IMPLEMENTATION_MODULE!r} not in new2, "phase0a runner imported on lookup"
        assert {_ENTRY_MODULE!r} not in new2, "phase0a old CLI imported on lookup"
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_registry_cli_key_phase0a_is_registry_only() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.strategies.venue_agnostic_signal_observer.runners.registry_cli",
            "--key",
            _PHASE0A_KEY,
            "--json",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    stdout = result.stdout
    assert f'"key": "{_PHASE0A_KEY}"' in stdout
    assert '"level"' not in stdout
    assert '"risk"' not in stdout
    assert '"supports_help_only"' not in stdout


def test_node_fills_still_has_no_cli_spec() -> None:
    from examples.strategies.venue_agnostic_signal_observer.runners.cli_specs import (
        get_runner_cli_spec,
    )

    assert get_runner_cli_spec(_NODE_FILLS_KEY) is None
