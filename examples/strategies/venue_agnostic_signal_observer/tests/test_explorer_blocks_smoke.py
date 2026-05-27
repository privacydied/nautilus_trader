# Smoke test for explorer_blocks availability

import os
import sys
from pathlib import Path
import pytest

repo_root = Path(__file__).resolve().parents[5]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
    _list_explorer_block_files,
    _discover_explorer_block_layout,
    NetworkChokepoint,
    ScoutStatus,
    EXPLORER_BLOCK_BUCKET,
    EXPLORER_BLOCK_PREFIX,
)

_SKIP_NET = os.environ.get("SKIP_S3_SMOKE_TESTS") == "1"


def test_explorer_blocks_bucket_constant():
    """Bucket constant must be hl-mainnet-node-data, not hyperliquid-archive."""
    assert EXPLORER_BLOCK_BUCKET == "hl-mainnet-node-data"
    assert EXPLORER_BLOCK_PREFIX == "explorer_blocks"
    assert "hyperliquid-archive" not in EXPLORER_BLOCK_BUCKET


@pytest.mark.skipif(_SKIP_NET, reason="SKIP_S3_SMOKE_TESTS=1")
def test_explorer_blocks_listing_known_window():
    """Attempt to list explorer_blocks for a date range known to contain data.

    Uses the canonical bucket hl-mainnet-node-data. If S3 returns zero results
    from the correct bucket, that is a data-plane failure — not a silent skip.
    """
    chokepoint = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    block_files = _list_explorer_block_files("2025-10-10", None, max_days=1, max_files=5, chokepoint=chokepoint)
    # Empty result from the correct bucket is a data-plane failure, not a harmless skip.
    assert block_files, (
        f"S3 listing returned zero files from s3://{EXPLORER_BLOCK_BUCKET}/{EXPLORER_BLOCK_PREFIX}/2025/10/10/ "
        "— data-plane failure. The correct bucket may require requester-pays credentials."
    )
    first_key, _size = block_files[0]
    assert first_key.startswith(f"s3://{EXPLORER_BLOCK_BUCKET}/{EXPLORER_BLOCK_PREFIX}/"), (
        f"Unexpected S3 path prefix: {first_key}"
    )
    assert "hyperliquid-archive" not in first_key, (
        f"Explorer block path must not use hyperliquid-archive bucket: {first_key}"
    )
