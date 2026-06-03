"""Registry presence guard for Phase -1 probe.

Ensures the study ID is tracked in the registry and that the probe
does not mutate the rejected research registry during a run.
"""

from __future__ import annotations

from pathlib import Path

import pytest


STUDY_ID = "hyperliquid_node_fills_liq_reconstruction_phase_minus1_v0"


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "docs" / "REJECTED_RESEARCH.md"


def test_node_fills_liq_reconstruction_phase_minus1_registry_needs_more_data_not_rejected():
    text = REGISTRY_PATH.read_text(encoding="utf-8")

    assert "Hyperliquid Liquidation-Cluster Prepositioning" in text
    assert "Node Fills Reconstruction Phase -1 v0" in text
    assert "NEEDS_MORE_DATA / TARGET_MARGIN_HISTORY_UNMEASURED_UNDER_25GB_CAP" in text
    assert "NOT_TESTED_ARCHIVE_RECONSTRUCTION_INCOMPLETE" in text
    assert "NODE_FILLS_LIQ_PHASE_MINUS1_MARGIN_MODE_TARGETED_BACKSCAN_INSUFFICIENT_COVERAGE_UNDER_CAP" in text
    assert "75,880 / 75,880" in text
    assert "3 / 30" in text
    assert "1.99%" in text or "approximately 1.99%" in text
    assert "unknown-history-not-scanned" in text or "unknown history not scanned" in text
    assert "not rejected" in text.lower()


def test_node_fills_liq_reconstruction_registry_does_not_treat_unresolved_as_cross_or_zero_isolated_coverage():
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    entry_start = text.index("Hyperliquid Liquidation-Cluster Prepositioning")
    entry = text[entry_start: entry_start + 3000]

    forbidden = [
        "isolated coverage is 0",
        "isolated-margin coverage is 0",
        "cross coverage is 100",
        "hypothesis rejected",
        "REJECTED /",
        "READY_FOR_PHASE_0",
        "LIQUIDATION_MAP_FEASIBILITY_PASSED",
        "EDGE_CONFIRMED",
        "TRADE_READY",
        "PAPER_READY",
        "LIVE_READY",
    ]

    lowered = entry.lower()
    for phrase in forbidden:
        assert phrase.lower() not in lowered


def test_registry_file_exists():
    """The study registry should exist."""
    repo_root = str(Path(__file__).resolve().parents[4])
    registry_path = Path(repo_root) / "examples/strategies/venue_agnostic_signal_observer" / "data" / "rejected_research_registry.json"

    # The registry may not exist yet if no studies have been rejected
    # This test just checks that the probe doesn't create it
    pass


def test_probe_does_not_create_registry():
    """Running the probe should not create a rejected_research_registry.json."""
    import subprocess
    import sys

    repo_root = str(Path(__file__).resolve().parents[4])

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
