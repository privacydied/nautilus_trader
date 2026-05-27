#!/usr/bin/env python3
"""Tests for the HIP-3 Builder Deployment Event Discovery probe module."""

import sys
from pathlib import Path

# Ensure repo root on path
repo_root = Path(__file__).resolve().parents[5]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import run_probe, ScoutStatus

def test_probe_dry_run():
    """Dry run should not require network and should return READY status."""
    result = run_probe(
        start_date="2025-10-13",
        end_date=None,
        max_days=1,
        max_block_files=1,
        download_budget_bytes=1_000_000,
        explorer_block_budget_bytes=1_000_000,
        allow_network_public=False,
        allow_s3_archive_read=False,
        dry_run=True,
    )
    assert result.status == ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_READY
    assert result.bytes_downloaded_total == 0
    assert result.candidate_events == []
