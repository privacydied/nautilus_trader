#!/usr/bin/env python3
"""Tests for the HIP-3 Builder Deployment Event Discovery probe module."""

import sys
from pathlib import Path

# Ensure repo root on path
repo_root = Path(__file__).resolve().parents[5]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
    run_probe,
    ScoutStatus,
    EXPLORER_BLOCK_BUCKET,
    EXPLORER_BLOCK_PREFIX,
    MARKET_DATA_BUCKET,
)

_SCOUT_SRC = Path(__file__).resolve().parents[1] / "hip3_builder_deployment_event_discovery_v0.py"


def test_explorer_blocks_do_not_use_market_data_bucket():
    """Hard regression: production code must not use hyperliquid-archive for explorer blocks."""
    src = _SCOUT_SRC.read_text()
    # Constants must be correct.
    assert EXPLORER_BLOCK_BUCKET == "hl-mainnet-node-data", (
        f"EXPLORER_BLOCK_BUCKET is {EXPLORER_BLOCK_BUCKET!r}, expected 'hl-mainnet-node-data'"
    )
    assert EXPLORER_BLOCK_PREFIX == "explorer_blocks", (
        f"EXPLORER_BLOCK_PREFIX is {EXPLORER_BLOCK_PREFIX!r}, expected 'explorer_blocks'"
    )
    assert MARKET_DATA_BUCKET == "hyperliquid-archive", (
        f"MARKET_DATA_BUCKET is {MARKET_DATA_BUCKET!r}, expected 'hyperliquid-archive'"
    )
    # Source must not construct explorer-block paths using the market-data bucket.
    forbidden = "hyperliquid-archive/explorer_blocks"
    assert forbidden not in src, (
        f"Production source contains forbidden string {forbidden!r}. "
        "Explorer blocks must use hl-mainnet-node-data, not hyperliquid-archive."
    )


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
