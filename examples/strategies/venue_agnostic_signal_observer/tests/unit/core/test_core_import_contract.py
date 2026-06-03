"""Import-contract tests for shared core utility spines."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.core.direction import (
    normalize_direction,
)
from examples.strategies.venue_agnostic_signal_observer.core.time_utils import (
    parse_iso_datetime,
)
from examples.strategies.venue_agnostic_signal_observer.core.signal_direction import (
    SignalDirection,
    normalize_signal_direction,
)


_REPO_ROOT = Path(__file__).resolve().parents[6]


_HEAVY_MODULES = (
    "examples.strategies.venue_agnostic_signal_observer.paper",
    "examples.strategies.venue_agnostic_signal_observer.bot",
    "examples.strategies.venue_agnostic_signal_observer.conductor",
    "examples.strategies.venue_agnostic_signal_observer.hypotheses.hyperliquid.node_fills_liq_reconstruction.runner",
    "examples.strategies.venue_agnostic_signal_observer.hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
)


def _run_import_probe(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT.parent)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


class TestCoreImportContract:
    def test_existing_core_direction_remains_importable(self) -> None:
        assert normalize_direction("buy") == "long"

    def test_time_utils_import_probe_avoids_heavy_modules(self) -> None:
        result = _run_import_probe(
            "import examples.strategies.venue_agnostic_signal_observer.core.time_utils as m; print('OK')"
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout
        assert not any(module in result.stderr for module in _HEAVY_MODULES)

    def test_signal_direction_import_probe_avoids_heavy_modules(self) -> None:
        result = _run_import_probe(
            "import examples.strategies.venue_agnostic_signal_observer.core.signal_direction as m; print('OK')"
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout
        assert not any(module in result.stderr for module in _HEAVY_MODULES)

    def test_core_package_import_does_not_pull_heavy_modules(self) -> None:
        result = _run_import_probe(
            "import examples.strategies.venue_agnostic_signal_observer.core as m; print('OK')"
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout
        assert not any(module in result.stderr for module in _HEAVY_MODULES)
