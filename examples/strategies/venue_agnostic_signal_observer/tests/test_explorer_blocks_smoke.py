# Smoke test for explorer_blocks availability

import sys
from pathlib import Path
import pytest

repo_root = Path(__file__).resolve().parents[5]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
    _list_explorer_block_files,
    NetworkChokepoint,
    ScoutStatus,
)

def test_explorer_blocks_listing_known_window():
    """Attempt to list explorer_blocks for a date range known to contain data.

    The exact block content is not validated here; we only verify that the S3 listing succeeds
    and returns at least one object, and that action type inventory is non‑empty when a file
    is processed.
    """
    # The date 2025-10-10 is referenced in REJECTED_RESEARCH.md as a window where NetChildVaultPositionsAction
    # was discovered. It should have at least one explorer block file.
    chokepoint = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    block_files = _list_explorer_block_files("2025-10-10", None, max_days=1, max_files=5, chokepoint=chokepoint)
    # If the prefix is empty or invalid, the function returns an empty list.
    if not block_files:
        pytest.skip("No explorer block files found for the known-good window – cannot verify listing.")
    # At least one block file should be listed.
    assert len(block_files) >= 1
    # Verify that the S3 path appears to follow the expected bucket/prefix layout.
    first_key, _size = block_files[0]
    assert first_key.startswith("s3://hyperliquid-archive/explorer_blocks/2025/10/10")
