#!/usr/bin/env python3
"""Extended tests for HIP‑3 Builder Deployment Event Discovery components."""

import sys
from pathlib import Path
import json
import pytest

# Ensure repo root on path for imports
repo_root = Path(__file__).resolve().parents[5]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
    _search_json_nested,
    _extract_candidate_from_match,
    NetworkChokepoint,
    BudgetExceededError,
    ScoutStatus,
    ProbeResult,
)


def test_search_json_nested_finds_terms():
    data = {
        "level1": {
            "deployInfo": {
                "builder": "builder123",
                "symbol": "ABC",
            },
            "list": [
                {"action": "register"},
                {"notRelevant": 1},
            ],
        }
    }
    matches = _search_json_nested(data, ["deploy", "builder", "symbol", "register"])
    # Expect matches for keys containing the terms
    keys = {m["key"].lower() for m in matches}
    # The traversal should capture any key containing a search term.
    # In our fixture we have keys: deployInfo, builder, symbol and a nested "action" key (value contains "register").
    # Only keys that *contain* the term are recorded, so we expect the three direct keys.
    assert "deployinfo" in keys
    assert "builder" in keys
    assert "symbol" in keys


def test_extract_candidate_missing_symbol():
    match = {"key": "deployInfo", "value": {"builder": "builderXYZ"}, "parent": {"some": "data"}}
    block_data = {"block_time": "2025-01-01T00:00:00Z", "block_number": 123456}
    cand = _extract_candidate_from_match(match, block_data, "/tmp/fake.json", "hash123")
    assert cand.symbol is None
    assert cand.symbol_extractable is False
    # The builder field is interpreted as a deployer address by the extraction logic.
    assert cand.deployer_address == "builderXYZ"
    # No explicit namespace was present.
    assert cand.builder_namespace is None
    # action_type should be the key from match
    assert cand.action_type == "deployInfo"


def test_network_chokepoint_blocks_without_permission():
    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=False)
    with pytest.raises(PermissionError):
        cp.http_get("https://example.com")
    with pytest.raises(PermissionError):
        cp.s3_download("s3://bucket/key", Path("/tmp/dummy"))
    with pytest.raises(PermissionError):
        cp.s3_list_prefix("bucket", "prefix/")
    with pytest.raises(PermissionError):
        cp.s3_read_object("bucket", "key")


def test_budget_exceeded_error():
    cp = NetworkChokepoint(allow_network_public=True, allow_s3_archive_read=True)
    # Simulate downloading large amount
    cp._track_bytes(5_000_000_001, "http:example.com")
    with pytest.raises(BudgetExceededError):
        cp.check_budget(total_cap=5_000_000_000)

def test_probe_result_status_ready_on_dry_run():
    # Directly invoke run_probe with dry_run flag to ensure status handling
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import run_probe
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
    assert isinstance(result, ProbeResult)
    assert result.status == ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_READY
    assert result.bytes_downloaded_total == 0
    assert not result.candidate_events


def test_probe_status_no_block_files():
    # Use a date where public explorer blocks are highly unlikely to exist.
    # The probe should return the new distinct status rather than HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS.
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import run_probe
    result = run_probe(
        start_date="2100-01-01",
        end_date=None,
        max_days=1,
        max_block_files=5,
        download_budget_bytes=1_000_000,
        explorer_block_budget_bytes=1_000_000,
        allow_network_public=False,
        allow_s3_archive_read=True,
        dry_run=False,
    )
    # Expect precise status: empty listing, credential failure, or access denied.
    assert result.status in (
        ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_EMPTY,
        ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_ACCESS_DENIED,
    )
    # No bytes should have been downloaded.
    assert result.bytes_downloaded_total == 0
    assert not result.candidate_events
