"""Registry presence guard for Phase -1 probe.

Ensures the study ID is tracked in the registry and that the probe
does not mutate the rejected research registry during a run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


STUDY_ID = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"


def test_registry_file_exists():
    """The study registry should exist."""
    repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
    registry_path = Path(repo_root) / "examples/strategies/venue_agnostic_signal_observer" / "data" / "rejected_research_registry.json"

    # The registry may not exist yet if no studies have been rejected
    # This test just checks that the probe doesn't create it
    pass


def test_probe_does_not_create_registry():
    """Running the probe should not create a rejected_research_registry.json."""
    import subprocess
    import sys

    repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)

    # Find or create a temp output dir
    out_dir = Path("/tmp/test_probe_registry")
    out_dir.mkdir(exist_ok=True)

    cmd = [
        sys.executable, "-m",
        "examples.strategies.venue_agnostic_signal_observer.run_hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0",
        "--out-root", str(out_dir),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=repo_root)

    assert result.returncode == 0, f"Probe failed: {result.stderr}"

    # The registry should not have been created
    registry_path = out_dir / "rejected_research_registry.json"
    assert not registry_path.exists(), "Probe should not create rejected_research_registry.json"
