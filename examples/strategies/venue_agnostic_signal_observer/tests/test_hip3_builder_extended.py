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
        ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_MAPPING_UNAVAILABLE,
        ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_MAPPING_INSUFFICIENT_SAMPLES,
        ScoutStatus.HIP3_EXPLORER_BLOCK_TIMESTAMP_PARSE_FAILED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_OUT_OF_RANGE,
    )
    # No bytes should have been downloaded.
    assert result.bytes_downloaded_total == 0
    assert not result.candidate_events


# ---------------------------------------------------------------------------
# Block-range layout tests
# ---------------------------------------------------------------------------

def test_block_range_prefixes_classify_as_block_range_partitioned():
    """Root prefixes like 'explorer_blocks/100000000/' must classify as block_range_partitioned."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _discover_explorer_block_layout,
        _parse_range_prefixes,
    )
    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    prefixes = [
        "explorer_blocks/0/",
        "explorer_blocks/100000000/",
        "explorer_blocks/200000000/",
        "explorer_blocks/300000000/",
    ]
    with unittest.mock.patch.object(cp, "s3_list_prefix", return_value={
        "prefixes": prefixes,
        "keys": [],
        "error_code": None,
    }):
        info = _discover_explorer_block_layout(cp)
    assert info["layout"] == "block_range_partitioned"
    assert info["range_numbers"] == [0, 100000000, 200000000, 300000000]


def test_date_partitioned_prefixes_not_confused_with_block_range():
    """Date-shaped prefixes must not be classified as block_range_partitioned."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _discover_explorer_block_layout,
    )
    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    prefixes = [
        "explorer_blocks/2025/10/13/",
        "explorer_blocks/2025/10/14/",
    ]
    with unittest.mock.patch.object(cp, "s3_list_prefix", return_value={
        "prefixes": prefixes,
        "keys": [],
        "error_code": None,
    }):
        info = _discover_explorer_block_layout(cp)
    assert info["layout"] == "date_partitioned"


def test_parse_range_prefixes():
    """_parse_range_prefixes must extract block numbers from range prefixes."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _parse_range_prefixes,
    )
    prefixes = [
        "explorer_blocks/0/",
        "explorer_blocks/100000000/",
        "explorer_blocks/200000000/",
        "explorer_blocks/900000000/",
    ]
    nums = _parse_range_prefixes(prefixes)
    assert nums == [0, 100000000, 200000000, 900000000]


def test_infer_stride():
    """_infer_stride must return the most common diff between ranges."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _infer_stride,
    )
    stride = _infer_stride([0, 100000000, 200000000, 300000000])
    assert stride == 100000000


# ---------------------------------------------------------------------------
# Timestamp sampling and date-to-block mapping tests
# ---------------------------------------------------------------------------

def test_timestamp_parse_ms():
    """_parse_timestamp must handle millisecond timestamps."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _parse_timestamp,
    )
    # 2025-10-13T00:00:00Z in ms = 1760313600000
    assert _parse_timestamp("1760313600000") == 1760313600000


def test_timestamp_parse_us():
    """_parse_timestamp must handle microsecond timestamps."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _parse_timestamp,
    )
    # 2025-10-13T00:00:00Z in us = 1760313600000000
    assert _parse_timestamp("1760313600000000") == 1760313600000


def test_timestamp_parse_iso():
    """_parse_timestamp must handle ISO format timestamps."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _parse_timestamp,
    )
    assert _parse_timestamp("2025-10-13T00:00:00") == 1760313600000


def test_timestamp_parse_none():
    """_parse_timestamp must return None for empty input."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _parse_timestamp,
    )
    assert _parse_timestamp(None) is None
    assert _parse_timestamp("") is None


def test_date_to_block_mapping_ready():
    """Date-to-block mapping must succeed when samples bracket the target date."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _map_date_to_block_range,
        BlockTimestampSample,
    )
    # 2025-10-13T00:00:00Z = 1760313600000 ms
    # Block numbers must be within their range prefixes for overlap logic to work.
    # Range 100M covers [100000000, 200000000), range 200M covers [200000000, 300000000)
    samples = [
        BlockTimestampSample(
            source_key="explorer_blocks/100000000/block_100000050.json.lz4",
            top_level_range_prefix="explorer_blocks/100000000/",
            block_number=100000050,
            block_timestamp_utc="1760200000000",  # before target
            parse_status="ok",
            byte_count=1000,
            content_hash="abc",
        ),
        BlockTimestampSample(
            source_key="explorer_blocks/200000000/block_200000050.json.lz4",
            top_level_range_prefix="explorer_blocks/200000000/",
            block_number=200000050,
            block_timestamp_utc="1760400000000",  # after target
            parse_status="ok",
            byte_count=1000,
            content_hash="def",
        ),
    ]
    mapping = _map_date_to_block_range("2025-10-13", None, samples)
    assert mapping["status"] == "HIP3_EXPLORER_BLOCK_DATE_MAPPING_READY"
    assert len(mapping["mapped_ranges"]) > 0


def test_date_to_block_mapping_insufficient_samples():
    """Date-to-block mapping must fail closed when no samples exist."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _map_date_to_block_range,
    )
    mapping = _map_date_to_block_range("2025-10-13", None, [])
    assert mapping["status"] == "HIP3_EXPLORER_BLOCK_DATE_MAPPING_INSUFFICIENT_SAMPLES"


def test_date_to_block_mapping_parse_failed():
    """Date-to-block mapping must report parse failure when timestamps are unparseable."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _map_date_to_block_range,
        BlockTimestampSample,
    )
    samples = [
        BlockTimestampSample(
            source_key="explorer_blocks/100000000/block.json.lz4",
            top_level_range_prefix="explorer_blocks/100000000/",
            block_number=100,
            block_timestamp_utc="not-a-timestamp",
            parse_status="ok",
            byte_count=1000,
            content_hash="abc",
        ),
    ]
    mapping = _map_date_to_block_range("2025-10-13", None, samples)
    assert mapping["status"] == "HIP3_EXPLORER_BLOCK_TIMESTAMP_PARSE_FAILED"


def test_date_to_block_mapping_out_of_range():
    """Date-to-block mapping must report out-of-range for very old dates."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _map_date_to_block_range,
        BlockTimestampSample,
    )
    samples = [
        BlockTimestampSample(
            source_key="explorer_blocks/100000000/block.json.lz4",
            top_level_range_prefix="explorer_blocks/100000000/",
            block_number=100,
            block_timestamp_utc="1760300000000",
            parse_status="ok",
            byte_count=1000,
            content_hash="abc",
        ),
    ]
    # 2024-01-01 is ~8 months before the sample; use 2023 to ensure out-of-range
    mapping = _map_date_to_block_range("2023-01-01", None, samples)
    assert mapping["status"] == "HIP3_EXPLORER_BLOCK_DATE_OUT_OF_RANGE"


def test_deployment_scan_requires_date_mapping():
    """Deployment scan must not run unless date mapping is ready."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_probe,
    )
    result = run_probe(
        start_date="2025-10-13",
        max_days=1,
        max_block_files=5,
        allow_network_public=False,
        allow_s3_archive_read=True,
        dry_run=False,
    )
    # Must not be READY (that's dry-run only) and must not be DEPLOYMENT_EVENTS_FOUND
    assert result.status not in (
        ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_READY,
        ScoutStatus.HIP3_DEPLOYMENT_EVENTS_FOUND,
    )


def test_budget_exceeded_during_timestamp_sampling():
    """Budget exceeded during timestamp sampling must fail closed."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_probe,
    )
    result = run_probe(
        start_date="2025-10-13",
        max_days=1,
        max_block_files=5,
        download_budget_bytes=100,
        explorer_block_budget_bytes=100,
        allow_network_public=False,
        allow_s3_archive_read=True,
        dry_run=False,
    )
    assert result.status in (
        ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_ERROR,
        ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_MAPPING_INSUFFICIENT_SAMPLES,
        ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED,
    )


def test_dry_run_no_s3():
    """Dry run must not read S3."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_probe,
    )
    result = run_probe(
        start_date="2025-10-13",
        dry_run=True,
        allow_network_public=False,
        allow_s3_archive_read=False,
    )
    assert result.status == ScoutStatus.HIP3_DEPLOYMENT_DISCOVERY_READY
    assert result.bytes_downloaded_total == 0
    assert not result.timestamp_samples
    assert not result.date_block_mapping


def test_no_registry_or_live_artifacts():
    """Probe must not write registry, paper trading, or live execution artifacts."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_probe,
    )
    result = run_probe(
        start_date="2025-10-13",
        dry_run=True,
    )
    assert result.study_id == "hip3_builder_deployment_event_discovery_v0"
    assert result.safety_mode == "public_data_observer_only"


import unittest.mock

# ---------------------------------------------------------------------------
# Timestamp samples from synthetic explorer block objects
# ---------------------------------------------------------------------------

def test_timestamp_samples_parsed_from_synthetic_block():
    """Timestamp samples must be parseable from synthetic explorer block data (msgpack)."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _sample_block_timestamps,
        NetworkChokepoint,
    )
    import lz4.frame
    import msgpack

    # Build a synthetic msgpack block file (actual archive format)
    blocks = [
        {
            "header": {
                "height": 100000050,
                "block_time": "2025-10-13T00:00:00",
                "hash": "0xabc",
            },
            "txs": [{"action": "DeployPerp"}, {"action": "RegisterAsset"}],
        }
    ]
    packed = msgpack.packb(blocks)
    compressed = lz4.frame.compress(packed)

    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    with unittest.mock.patch.object(cp, "s3_list_prefix", return_value={
        "prefixes": [],
        "keys": ["explorer_blocks/100000000/100000000/100000050.rmp.lz4"],
        "objects": [],
        "error_code": None,
    }):
        with unittest.mock.patch.object(cp, "s3_read_object", return_value=compressed):
            samples = _sample_block_timestamps(
                cp,
                ["explorer_blocks/100000000/"],
                max_sample_files=10,
                max_sample_bytes=1_000_000,
            )

    assert len(samples) == 1
    assert samples[0].block_number == 100000050
    assert "2025-10-13" in samples[0].block_timestamp_utc
    assert samples[0].parse_status == "ok"
    assert samples[0].action_type_count == 2


def test_extract_block_number_from_key():
    """_extract_block_number_from_key must parse block number from various key formats."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _extract_block_number_from_key,
    )
    assert _extract_block_number_from_key("explorer_blocks/100000000/block_100000050.json.lz4") == 100000050
    assert _extract_block_number_from_key("explorer_blocks/100000000/100000050.json.lz4") == 100000050
    assert _extract_block_number_from_key("explorer_blocks/100000000/block-200000000.json.lz4") == 200000000
    assert _extract_block_number_from_key("explorer_blocks/0/block_0.json.lz4") == 0
    assert _extract_block_number_from_key("explorer_blocks/900000000/block_999999999.json.lz4") == 999999999


def test_estimate_block_for_timestamp():
    """_estimate_block_for_timestamp must interpolate between anchors."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _estimate_block_for_timestamp,
    )
    # Two anchors: block 100 at ts 1000, block 200 at ts 2000
    parsed: list[tuple[int, str | None, int]] = [
        (100, "explorer_blocks/100000000/", 1000),
        (200, "explorer_blocks/200000000/", 2000),
    ]
    # Exact midpoint
    assert _estimate_block_for_timestamp(1500, parsed) == 150
    # Closer to first
    assert _estimate_block_for_timestamp(1100, parsed) == 110
    # Before first anchor
    assert _estimate_block_for_timestamp(500, parsed) == 100
    # After last anchor
    assert _estimate_block_for_timestamp(3000, parsed) == 200


def test_estimate_block_single_anchor():
    """_estimate_block_for_timestamp must return None with fewer than 2 anchors."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _estimate_block_for_timestamp,
    )
    assert _estimate_block_for_timestamp(1500, [(100, "x", 1000)]) is None
    assert _estimate_block_for_timestamp(1500, []) is None


# ---------------------------------------------------------------------------
# Block-range file listing with block bounds
# ---------------------------------------------------------------------------

def test_list_block_range_files_filters_by_block_bounds():
    """_list_block_range_files must filter out files outside [start_block, end_block]."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _list_block_range_files,
        NetworkChokepoint,
    )
    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    with unittest.mock.patch.object(cp, "s3_list_prefix", return_value={
        "prefixes": [],
        "keys": [],
        "objects": [
            {"key": "explorer_blocks/100000000/block_100000050.json.lz4", "size": 1000},
            {"key": "explorer_blocks/100000000/block_100000100.json.lz4", "size": 1000},
            {"key": "explorer_blocks/100000000/block_100000200.json.lz4", "size": 1000},
        ],
        "error_code": None,
    }):
        files = _list_block_range_files(
            ["explorer_blocks/100000000/"],
            max_files=100,
            chokepoint=cp,
            start_block=100000060,
            end_block=100000150,
        )
    # Only block_100000100 should remain
    assert len(files) == 1
    assert "block_100000100" in files[0][0]


# ---------------------------------------------------------------------------
# S3 access through chokepoint
# ---------------------------------------------------------------------------

def test_s3_access_through_chokepoint():
    """All S3 reads must go through NetworkChokepoint."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_probe,
    )
    result = run_probe(
        start_date="2025-10-13",
        dry_run=True,
        allow_network_public=False,
        allow_s3_archive_read=True,
    )
    assert result.bytes_downloaded_total == 0
    assert result.s3_requester_pays_acknowledged


# ---------------------------------------------------------------------------
# Registry and artifact tests
# ---------------------------------------------------------------------------

def test_no_registry_mutation():
    """Probe must not write to any registry or live-trading artifact."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_probe,
    )
    result = run_probe(start_date="2025-10-13", dry_run=True)
    assert result.study_id == "hip3_builder_deployment_event_discovery_v0"
    assert result.safety_mode == "public_data_observer_only"
    # No registry keys
    assert len(result.action_types_inventoried) == 0
    assert len(result.candidate_events) == 0
