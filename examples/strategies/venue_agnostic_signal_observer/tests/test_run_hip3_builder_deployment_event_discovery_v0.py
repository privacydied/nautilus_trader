#!/usr/bin/env python3
"""Integration test for the HIP-3 Builder Deployment Event Discovery CLI."""

import subprocess
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[5]
script_path = repo_root / "examples" / "strategies" / "venue_agnostic_signal_observer" / "run_hip3_builder_deployment_event_discovery_v0.py"

def test_cli_dry_run():
    """Run the CLI with --dry-run and ensure it exits cleanly (status 0)."""
    result = subprocess.run([sys.executable, str(script_path), "--dry-run"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"CLI exited with {result.returncode}: {result.stderr}"
    # The output should contain the ready status line.
    assert "HIP3_DEPLOYMENT_DISCOVERY_READY" in result.stdout
