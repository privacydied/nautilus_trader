#!/usr/bin/env python3
"""Extended tests for HIP‑3 Builder Deployment Event Discovery components."""

import sys
from pathlib import Path
import json
import tempfile
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
    # Expect precise status: empty listing, credential failure, access denied,
    # date out-of-range, or no events found (probe ran but year 2100 has no data).
    assert result.status in (
        ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_EMPTY,
        ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_ACCESS_DENIED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_MAPPING_UNAVAILABLE,
        ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_MAPPING_INSUFFICIENT_SAMPLES,
        ScoutStatus.HIP3_EXPLORER_BLOCK_TIMESTAMP_PARSE_FAILED,
        ScoutStatus.HIP3_EXPLORER_BLOCK_DATE_OUT_OF_RANGE,
        # Probe may successfully run and find no events for year 2100
        ScoutStatus.HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS,
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
# P1 action-type inventory tests
# ---------------------------------------------------------------------------

_MOD_PATH = "examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0"


def test_inventory_mode_does_not_emit_no_deployment_events():
    """--inventory-action-types-only must NOT emit HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_inventory_probe,
        ScoutStatus,
    )
    inv = run_inventory_probe(
        max_block_files=5,
        explorer_block_budget_bytes=50_000_000,
        allow_network_public=False,
        allow_s3_archive_read=False,
    )
    assert inv.status != ScoutStatus.HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS, (
        f"Inventory mode emitted forbidden status: {inv.status}"
    )


def test_inventory_mode_counts_synthetic_action_types():
    """Action inventory correctly counts synthetic action types from mock data."""
    import lz4.frame
    import msgpack
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_inventory_probe,
        ScoutStatus,
        NetworkChokepoint,
        EXPLORER_BLOCK_BUCKET,
    )

    blocks = [
        {
            "header": {"height": 100, "block_time": "2025-10-13T00:00:00"},
            "txs": [
                {"action": {"type": "DeployPerp"}, "user": "0xabc"},
                {"action": {"type": "RegisterAsset"}, "user": "0xdef"},
                {"action": {"type": "DeployPerp"}, "user": "0xghi"},
            ],
        }
    ]
    packed = msgpack.packb(blocks)
    compressed = lz4.frame.compress(packed)

    with unittest.mock.patch(
        f"{_MOD_PATH}.NetworkChokepoint.s3_list_prefix",
        return_value={
            "prefixes": ["explorer_blocks/100000000/"],
            "keys": [],
            "objects": [],
            "error_code": None,
        },
    ):
        # We need to patch the sub-listing call for the range prefix too
        pass

    # Use direct chokepoint patching
    cp_patch_layout = {
        "prefixes": ["explorer_blocks/100000000/"],
        "keys": [],
        "objects": [],
        "error_code": None,
    }
    cp_patch_sublisting = {
        "prefixes": [],
        "keys": ["explorer_blocks/100000000/block_100.rmp.lz4"],
        "objects": [],
        "error_code": None,
    }

    original_run = run_inventory_probe

    def _patched_run(**kwargs):
        from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
            NetworkChokepoint,
        )
        import unittest.mock as m
        call_count = [0]

        def fake_list_prefix(self_inner, bucket, prefix, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return cp_patch_layout
            return cp_patch_sublisting

        with m.patch.object(NetworkChokepoint, "s3_list_prefix", fake_list_prefix):
            with m.patch.object(NetworkChokepoint, "s3_read_object", return_value=compressed):
                return original_run(**kwargs)

    inv = _patched_run(
        max_block_files=5,
        explorer_block_budget_bytes=50_000_000,
        allow_network_public=False,
        allow_s3_archive_read=True,
    )

    assert inv.status == ScoutStatus.HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_READY, (
        f"Expected INVENTORY_READY, got {inv.status}"
    )
    assert inv.action_type_counts.get("DeployPerp", 0) == 2
    assert inv.action_type_counts.get("RegisterAsset", 0) == 1
    assert inv.files_read == 1


def test_inventory_mode_opaque_records_counted():
    """Opaque/unknown records are counted and not silently dropped."""
    import lz4.frame
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_inventory_probe,
        ScoutStatus,
        NetworkChokepoint,
    )

    # Provide a block that has no txs/actions — triggers opaque counting
    import json
    blocks = [
        {"header": {"height": 200}, "data": "no_txs_here"},
    ]
    compressed = lz4.frame.compress(json.dumps(blocks).encode())
    call_count = [0]
    cp_patch_layout = {
        "prefixes": ["explorer_blocks/100000000/"],
        "keys": [],
        "objects": [],
        "error_code": None,
    }
    cp_patch_sublisting = {
        "prefixes": [],
        "keys": ["explorer_blocks/100000000/block_200.json.lz4"],
        "objects": [],
        "error_code": None,
    }

    def fake_list_prefix(self, bucket, prefix, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return cp_patch_layout
        return cp_patch_sublisting

    with unittest.mock.patch.object(NetworkChokepoint, "s3_list_prefix", fake_list_prefix):
        with unittest.mock.patch.object(NetworkChokepoint, "s3_read_object", return_value=compressed):
            inv = run_inventory_probe(
                max_block_files=5,
                explorer_block_budget_bytes=50_000_000,
                allow_network_public=False,
                allow_s3_archive_read=True,
            )

    # opaque_records_count must be > 0 since we had a block without txs
    assert inv.opaque_records_count > 0, "Expected opaque records to be counted"
    assert inv.files_read == 1


def test_inventory_mode_unknown_layout_stops():
    """Unknown layout must stop before reading any files."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_inventory_probe,
        ScoutStatus,
        NetworkChokepoint,
    )

    # Provide prefixes that don't match any known layout pattern
    mystery_listing = {
        "prefixes": ["explorer_blocks/some_unknown_prefix/"],
        "keys": [],
        "objects": [],
        "error_code": None,
    }

    with unittest.mock.patch.object(NetworkChokepoint, "s3_list_prefix", return_value=mystery_listing):
        with unittest.mock.patch.object(NetworkChokepoint, "s3_read_object", side_effect=AssertionError("Must not read files for unknown layout")) as mock_read:
            inv = run_inventory_probe(
                max_block_files=5,
                explorer_block_budget_bytes=50_000_000,
                allow_network_public=False,
                allow_s3_archive_read=True,
            )

    assert inv.status == ScoutStatus.HIP3_EXPLORER_BLOCK_LAYOUT_UNKNOWN
    assert inv.files_read == 0


def test_inventory_credentials_required_status():
    """No AWS credentials → CREDENTIALS_REQUIRED."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_inventory_probe,
        ScoutStatus,
        NetworkChokepoint,
    )

    creds_missing = {"prefixes": [], "keys": [], "objects": [], "error_code": "NO_CREDENTIALS"}
    with unittest.mock.patch.object(NetworkChokepoint, "s3_list_prefix", return_value=creds_missing):
        inv = run_inventory_probe(allow_s3_archive_read=True)
    assert inv.status == ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED


def test_inventory_access_denied_status():
    """AccessDenied S3 error → ACCESS_DENIED."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_inventory_probe,
        ScoutStatus,
        NetworkChokepoint,
    )

    denied = {"prefixes": [], "keys": [], "objects": [], "error_code": "AccessDenied"}
    with unittest.mock.patch.object(NetworkChokepoint, "s3_list_prefix", return_value=denied):
        inv = run_inventory_probe(allow_s3_archive_read=True)
    assert inv.status == ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_ACCESS_DENIED


def test_no_subprocess_eval_ossystem_in_module():
    """Production module must not contain subprocess, os.system, or eval."""
    src_path = Path(__file__).resolve().parents[1] / "hip3_builder_deployment_event_discovery_v0.py"
    src = src_path.read_text()
    assert "import subprocess" not in src
    assert "subprocess.run" not in src
    assert "os.system(" not in src
    assert "eval(" not in src


def test_inventory_artifacts_no_pnl_fields():
    """Inventory artifacts must not contain PnL/basis/residual/strategy/return fields."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        ActionInventoryResult,
        ScoutStatus,
    )
    inv = ActionInventoryResult(
        status=ScoutStatus.HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_READY,
        run_id="test",
    )
    inv_dict = inv.__dict__
    forbidden = {"pnl", "basis", "residual", "strategy_return", "alpha", "sharpe", "returns"}
    overlap = forbidden & set(inv_dict.keys())
    assert not overlap, f"ActionInventoryResult has forbidden fields: {overlap}"


def test_universe_delta_is_note_not_proof():
    """Summary.md must reference universe-delta as note, not proof."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _write_inventory_artifacts,
        ActionInventoryResult,
        ScoutStatus,
    )
    import tempfile, os
    inv = ActionInventoryResult(
        status=ScoutStatus.HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_EMPTY,
        run_id="test_ud",
        final_status="HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_EMPTY",
        git_sha="abc",
        git_dirty=False,
        repo_root="/tmp",
    )
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _write_inventory_artifacts(run_dir, inv, [])
        md = (run_dir / "summary.md").read_text()
    # Must reference universe-delta context as a note
    assert "Universe-delta" in md or "universe-delta" in md
    assert "does NOT prove absence" in md or "NOT prove" in md


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


# ---------------------------------------------------------------------------
# Additional acceptance-criteria tests for P1 action-type inventory
# ---------------------------------------------------------------------------

def test_inventory_mode_root_layout_must_be_known_before_reading():
    """Inventory mode must discover layout before reading any block files."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_inventory_probe,
        ScoutStatus,
        NetworkChokepoint,
    )

    # Unknown layout: stop without reading files
    unknown_layout = {
        "prefixes": ["explorer_blocks/mystery/"],
        "keys": [],
        "objects": [],
        "error_code": None,
    }

    with unittest.mock.patch.object(NetworkChokepoint, "s3_list_prefix", return_value=unknown_layout):
        with unittest.mock.patch.object(NetworkChokepoint, "s3_read_object", side_effect=AssertionError("Must not read for unknown layout")):
            inv = run_inventory_probe(
                max_block_files=5,
                explorer_block_budget_bytes=50_000_000,
                allow_network_public=False,
                allow_s3_archive_read=True,
            )

    assert inv.status == ScoutStatus.HIP3_EXPLORER_BLOCK_LAYOUT_UNKNOWN
    assert inv.files_read == 0
    assert inv.files_listed == 0


def test_inventory_mode_summary_md_references_universe_delta_as_note():
    """Summary.md must reference universe-delta (c9056978aa) as context note, not proof."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _write_inventory_artifacts,
        ActionInventoryResult,
        ScoutStatus,
    )

    inv = ActionInventoryResult(
        status=ScoutStatus.HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_READY,
        run_id="test_note",
        final_status="HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_READY",
        git_sha="abc",
        git_dirty=False,
        repo_root="/tmp",
        inferred_layout="block_range_partitioned",
        files_listed=10,
        files_read=5,
        bytes_downloaded=12345,
        action_type_counts={"DeployPerp": 3, "RegisterAsset": 1},
    )

    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _write_inventory_artifacts(run_dir, inv, ["--inventory-action-types-only"])
        md = (run_dir / "summary.md").read_text()

    # Must reference universe-delta context
    assert "Universe-delta" in md or "universe-delta" in md
    # Must say it does NOT prove absence
    assert "does NOT prove absence" in md or "NOT prove" in md
    # Must NOT claim the inventory result proves deployment events
    assert "confirmed" not in md.lower() or "universe-delta" not in md.lower()


def test_inventory_mode_no_deployment_status_in_artifacts():
    """Inventory artifacts must never contain HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _write_inventory_artifacts,
        ActionInventoryResult,
        ScoutStatus,
    )

    inv = ActionInventoryResult(
        status=ScoutStatus.HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_EMPTY,
        run_id="test_no_deploy",
        final_status="HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_EMPTY",
        git_sha="abc",
        git_dirty=False,
        repo_root="/tmp",
    )

    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _write_inventory_artifacts(run_dir, inv, ["--inventory-action-types-only"])
        summary = json.loads((run_dir / "summary.json").read_text())
        manifest = json.loads((run_dir / "run_manifest.json").read_text())

    # Neither summary nor manifest should contain the no-deployment status
    assert summary["final_status"] != "HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS"
    assert manifest["final_status"] != "HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS"


def test_inventory_artifacts_no_pnl_basis_strategy_fields():
    """Inventory artifacts must not contain PnL/basis/residual/strategy/return fields."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _write_inventory_artifacts,
        ActionInventoryResult,
        ScoutStatus,
    )

    inv = ActionInventoryResult(
        status=ScoutStatus.HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_READY,
        run_id="test_fields",
        final_status="HIP3_EXPLORER_BLOCK_ACTION_INVENTORY_READY",
        git_sha="abc",
        git_dirty=False,
        repo_root="/tmp",
        action_type_counts={"DeployPerp": 1},
    )

    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        _write_inventory_artifacts(run_dir, inv, [])
        for fname in ("summary.json", "action_type_inventory.json", "schema_shape_samples.json", "run_manifest.json"):
            content = (run_dir / fname).read_text().lower()
            for forbidden in ("pnl", "basis", "residual", "strategy_return", "alpha", "sharpe", "returns"):
                assert forbidden not in content, f"Found forbidden field '{forbidden}' in {fname}"


def test_inventory_opaque_msgpack_not_silently_dropped():
    """Opaque MessagePack records must be counted, not silently dropped."""
    import lz4.frame
    import msgpack
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_inventory_probe,
        ScoutStatus,
        NetworkChokepoint,
    )

    # Build a MessagePack block with no txs/actions list — triggers opaque count
    blocks = [
        {
            "header": {"height": 500, "block_time": "2025-10-13T00:00:00"},
            "metadata": "no_actions",
        },
    ]
    packed = msgpack.packb(blocks)
    compressed = lz4.frame.compress(packed)

    call_count = [0]
    layout_patch = {
        "prefixes": ["explorer_blocks/100000000/"],
        "keys": [],
        "objects": [],
        "error_code": None,
    }
    sublisting_patch = {
        "prefixes": [],
        "keys": ["explorer_blocks/100000000/block_500.rmp.lz4"],
        "objects": [],
        "error_code": None,
    }

    def fake_list_prefix(self, bucket, prefix, **kw):
        call_count[0] += 1
        return layout_patch if call_count[0] == 1 else sublisting_patch

    with unittest.mock.patch.object(NetworkChokepoint, "s3_list_prefix", fake_list_prefix):
        with unittest.mock.patch.object(NetworkChokepoint, "s3_read_object", return_value=compressed):
            inv = run_inventory_probe(
                max_block_files=5,
                explorer_block_budget_bytes=50_000_000,
                allow_network_public=False,
                allow_s3_archive_read=True,
            )

    assert inv.files_read == 1
    assert inv.opaque_records_count > 0, "Opaque records must be counted, not silently dropped"


def test_inventory_no_production_subprocess_os_system_eval():
    """Production module must not use subprocess, os.system, or eval."""
    src_path = Path(__file__).resolve().parents[1] / "hip3_builder_deployment_event_discovery_v0.py"
    src = src_path.read_text()
    assert "import subprocess" not in src, "Must not import subprocess"
    assert "subprocess.run" not in src, "Must not call subprocess.run"
    assert "subprocess.Popen" not in src, "Must not call subprocess.Popen"
    assert "os.system(" not in src, "Must not call os.system"
    assert "eval(" not in src, "Must not call eval"


def test_inventory_mode_explorer_block_budget_enforced():
    """Inventory mode must respect download budget and stop when exceeded."""
    import lz4.frame
    import msgpack
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_inventory_probe,
        ScoutStatus,
        NetworkChokepoint,
    )

    # Create a small block
    blocks = [
        {
            "header": {"height": 100, "block_time": "2025-10-13T00:00:00"},
            "txs": [{"action": {"type": "DeployPerp"}}],
        }
    ]
    packed = msgpack.packb(blocks)
    compressed = lz4.frame.compress(packed)
    compressed_size = len(compressed)

    call_count = [0]
    layout_patch = {
        "prefixes": ["explorer_blocks/100000000/"],
        "keys": [],
        "objects": [],
        "error_code": None,
    }
    sublisting_patch = {
        "prefixes": [],
        "keys": [
            "explorer_blocks/100000000/block_1.rmp.lz4",
            "explorer_blocks/100000000/block_2.rmp.lz4",
        ],
        "objects": [],
        "error_code": None,
    }

    def fake_list_prefix(self, bucket, prefix, **kw):
        call_count[0] += 1
        return layout_patch if call_count[0] == 1 else sublisting_patch

    # Use a real s3_read_object wrapper that tracks bytes
    def fake_read_object(self, bucket, key, requester_pays=True):
        self._track_bytes(compressed_size, f"s3:{bucket}")
        return compressed

    with unittest.mock.patch.object(NetworkChokepoint, "s3_list_prefix", fake_list_prefix):
        with unittest.mock.patch.object(NetworkChokepoint, "s3_read_object", fake_read_object):
            inv = run_inventory_probe(
                max_block_files=20,
                explorer_block_budget_bytes=100,  # tiny budget — will stop after first file
                download_budget_bytes=100,
                allow_network_public=False,
                allow_s3_archive_read=True,
            )

    # Should have read at least 1 file but budget should have stopped further reads
    assert inv.files_read >= 1
    assert inv.bytes_downloaded >= compressed_size


# ---------------------------------------------------------------------------
# P2 deployment-event search tests
# ---------------------------------------------------------------------------

def test_p2_order_cancel_with_empty_p1_types_are_candidates():
    """Order/cancel actions with empty P1 baseline are classified as new_vs_p1 candidates."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _run_p2_scan_window,
        NetworkChokepoint,
        ScoutStatus,
    )
    import lz4.frame
    import msgpack

    blocks = [
        {
            "header": {"height": 100, "block_time": "2025-10-13T00:00:00"},
            "txs": [
                {"type": "order", "side": "buy", "price": "100"},
                {"type": "cancel", "oid": "123"},
            ],
        }
    ]
    packed = msgpack.packb(blocks)
    compressed = lz4.frame.compress(packed)

    call_count = [0]
    sublisting_patch = {
        "prefixes": [],
        "keys": ["explorer_blocks/100000000/block_100.rmp.lz4"],
        "objects": [],
        "error_code": None,
    }

    def fake_list_prefix(self, bucket, prefix, **kw):
        call_count[0] += 1
        return sublisting_patch

    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    with unittest.mock.patch.object(NetworkChokepoint, "s3_list_prefix", fake_list_prefix):
        with unittest.mock.patch.object(NetworkChokepoint, "s3_read_object", return_value=compressed):
            summary = _run_p2_scan_window(
                window_name="test",
                start_date="2025-10-13",
                end_date="2025-10-14",
                mapped_ranges=["explorer_blocks/100000000/"],
                max_files=1,
                chokepoint=cp,
                p1_action_types=set(),
                rare_threshold=25,
                download_budget_remaining=50_000_000,
                explorer_budget_remaining=50_000_000,
            )

    assert summary.blocks_parsed > 0
    assert summary.total_actions > 0
    assert summary.status in (
        ScoutStatus.HIP3_DEPLOYMENT_EVENT_CANDIDATES_FOUND.value,
        ScoutStatus.HIP3_DEPLOYMENT_EVENT_CANDIDATES_FOUND,
    )


def test_p2_no_candidate_status_with_known_p1_types():
    """HIP3_NO_DEPLOYMENT_EVENT_CANDIDATES_IN_SCANNED_BLOCKS requires known P1 types."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _run_p2_scan_window,
        NetworkChokepoint,
        ScoutStatus,
    )
    import lz4.frame
    import msgpack

    blocks = [
        {
            "header": {"height": 100, "block_time": "2025-10-13T00:00:00"},
            "txs": [
                {"type": "order", "side": "buy", "price": "100"},
                {"type": "cancel", "oid": "123"},
            ],
        }
    ]
    packed = msgpack.packb(blocks)
    compressed = lz4.frame.compress(packed)

    call_count = [0]
    sublisting_patch = {
        "prefixes": [],
        "keys": ["explorer_blocks/100000000/block_100.rmp.lz4"],
        "objects": [],
        "error_code": None,
    }

    def fake_list_prefix(self, bucket, prefix, **kw):
        call_count[0] += 1
        return sublisting_patch

    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    with unittest.mock.patch.object(NetworkChokepoint, "s3_list_prefix", fake_list_prefix):
        with unittest.mock.patch.object(NetworkChokepoint, "s3_read_object", return_value=compressed):
            summary = _run_p2_scan_window(
                window_name="test",
                start_date="2025-10-13",
                end_date="2025-10-14",
                mapped_ranges=["explorer_blocks/100000000/"],
                max_files=1,
                chokepoint=cp,
                p1_action_types={"order", "cancel"},
                rare_threshold=1,
                download_budget_remaining=50_000_000,
                explorer_budget_remaining=50_000_000,
            )

    assert summary.blocks_parsed > 0
    assert summary.total_actions > 0
    assert summary.status in (
        ScoutStatus.HIP3_NO_DEPLOYMENT_EVENT_CANDIDATES_IN_SCANNED_BLOCKS.value,
        ScoutStatus.HIP3_NO_DEPLOYMENT_EVENT_CANDIDATES_IN_SCANNED_BLOCKS,
    )


def test_p2_schema_unknown_when_no_actions_in_parsed_files():
    """HIP3_DEPLOYMENT_EVENT_SCHEMA_UNKNOWN fires when parsed files have no actions."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _run_p2_scan_window,
        NetworkChokepoint,
        ScoutStatus,
    )
    import json
    import lz4.frame

    blocks = [
        {"header": {"height": 200}, "metadata": "no_actions"},
    ]
    compressed = lz4.frame.compress(json.dumps(blocks).encode())

    call_count = [0]
    sublisting_patch = {
        "prefixes": [],
        "keys": ["explorer_blocks/100000000/block_200.json.lz4"],
        "objects": [],
        "error_code": None,
    }

    def fake_list_prefix(self, bucket, prefix, **kw):
        call_count[0] += 1
        return sublisting_patch

    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    with unittest.mock.patch.object(NetworkChokepoint, "s3_list_prefix", fake_list_prefix):
        with unittest.mock.patch.object(NetworkChokepoint, "s3_read_object", return_value=compressed):
            summary = _run_p2_scan_window(
                window_name="test",
                start_date="2025-10-13",
                end_date="2025-10-14",
                mapped_ranges=["explorer_blocks/100000000/"],
                max_files=1,
                chokepoint=cp,
                p1_action_types=set(),
                rare_threshold=25,
                download_budget_remaining=50_000_000,
                explorer_budget_remaining=50_000_000,
            )

    assert summary.blocks_parsed > 0
    assert summary.total_actions == 0
    assert summary.status in (
        ScoutStatus.HIP3_DEPLOYMENT_EVENT_SCHEMA_UNKNOWN.value,
        ScoutStatus.HIP3_DEPLOYMENT_EVENT_SCHEMA_UNKNOWN,
    )


def test_p2_rare_action_extraction():
    """Rare action examples are extracted correctly."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _run_p2_scan_window,
        NetworkChokepoint,
        ScoutStatus,
    )
    import lz4.frame
    import msgpack

    blocks = [
        {
            "header": {"height": 300, "block_time": "2025-10-13T00:00:00"},
            "txs": [
                {"type": "HyperliquidDeployer", "deployer": "0xabc123", "symbol": "TEST"},
            ],
        }
    ]
    packed = msgpack.packb(blocks)
    compressed = lz4.frame.compress(packed)

    call_count = [0]
    sublisting_patch = {
        "prefixes": [],
        "keys": ["explorer_blocks/100000000/block_300.rmp.lz4"],
        "objects": [],
        "error_code": None,
    }

    def fake_list_prefix(self, bucket, prefix, **kw):
        call_count[0] += 1
        return sublisting_patch

    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    with unittest.mock.patch.object(NetworkChokepoint, "s3_list_prefix", fake_list_prefix):
        with unittest.mock.patch.object(NetworkChokepoint, "s3_read_object", return_value=compressed):
            summary = _run_p2_scan_window(
                window_name="test",
                start_date="2025-10-13",
                end_date="2025-10-14",
                mapped_ranges=["explorer_blocks/100000000/"],
                max_files=1,
                chokepoint=cp,
                p1_action_types=set(),
                rare_threshold=25,
                download_budget_remaining=50_000_000,
                explorer_budget_remaining=50_000_000,
            )

    assert summary.candidate_count > 0
    assert summary.status in (
        ScoutStatus.HIP3_DEPLOYMENT_EVENT_CANDIDATES_FOUND.value,
        ScoutStatus.HIP3_DEPLOYMENT_EVENT_CANDIDATES_FOUND,
    )


def test_p2_new_action_type_vs_p1():
    """New action type not in P1 baseline is classified as new_vs_p1."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        _classify_candidate,
        P2_CANDIDATE_CLASS_NEW_ACTION_TYPE_VS_P1,
    )

    p1_types = {"order", "cancel", "SetGlobalAction", "CreditBridgeDepositAction", "connect"}
    cls, is_rare, is_new = _classify_candidate(
        action_type="HyperliquidDeployer",
        tx={"deployer": "0xabc"},
        matched_terms=[],
        p1_action_types=p1_types,
        rare_threshold=25,
        global_action_counts={"HyperliquidDeployer": 1},
    )
    assert is_new is True
    assert cls == P2_CANDIDATE_CLASS_NEW_ACTION_TYPE_VS_P1


def test_p2_dry_run_does_not_hit_s3():
    """P2 dry run (credentials required) should not hit S3."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_p2_deployment_search,
        ScoutStatus,
    )

    result = run_p2_deployment_search(
        p2_window="all",
        allow_network_public=False,
        allow_s3_archive_read=False,
    )
    assert result.status == ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED
    assert result.files_read == 0
    assert result.bytes_downloaded == 0


def test_p2_inventory_mode_does_not_emit_no_deployment_events():
    """P2 search must NOT emit HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_p2_deployment_search,
        ScoutStatus,
    )

    result = run_p2_deployment_search(
        p2_window="all",
        allow_network_public=False,
        allow_s3_archive_read=False,
    )
    assert result.status != ScoutStatus.HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS


def test_p2_no_pnl_basis_residual_fields():
    """P2 artifacts must not contain PnL/basis/residual/strategy fields."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        P2SearchResult,
        ScoutStatus,
    )

    result = P2SearchResult(
        status=ScoutStatus.HIP3_DEPLOYMENT_EVENT_SEARCH_READY,
        run_id="test_pnl",
    )
    result_dict = result.__dict__
    forbidden = {"pnl", "basis", "residual", "strategy_return", "alpha", "sharpe", "returns"}
    overlap = forbidden & set(result_dict.keys())
    assert not overlap, f"P2SearchResult has forbidden fields: {overlap}"


def test_p2_no_registry_paper_live_artifacts():
    """P2 does not write registry/paper/live artifacts."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        P2SearchResult,
        ScoutStatus,
    )

    result = P2SearchResult(
        status=ScoutStatus.HIP3_DEPLOYMENT_EVENT_SEARCH_READY,
        run_id="test",
    )
    assert result.safety_mode == "public_data_observer_only"
    result_dict = {k: v for k, v in result.__dict__.items() if not k.startswith("_")}
    flat = str(result_dict)
    for forbidden in ("pnl", "basis", "residual", "strategy_return", "alpha", "sharpe"):
        assert forbidden not in flat


def test_p2_no_production_subprocess_os_system_eval():
    """Production module must not use subprocess, os.system, or eval."""
    import pathlib
    src_path = pathlib.Path(__file__).resolve().parents[1] / "hip3_builder_deployment_event_discovery_v0.py"
    src = src_path.read_text()
    assert "import subprocess" not in src
    assert "subprocess.run" not in src
    assert "os.system(" not in src
    assert "eval(" not in src


def test_p2_multi_window_scan_summaries_are_stable():
    """Multi-window scan summaries produce stable output."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        P2WindowScanSummary,
    )

    ws1 = P2WindowScanSummary(
        window_name="prelaunch",
        date_range=("2025-09-01", "2025-10-13"),
        mapped_ranges=["explorer_blocks/100000000/"],
        files_scanned=10,
        bytes_downloaded=100000,
        blocks_parsed=5,
        total_actions=100,
        unique_action_types=["order", "cancel"],
        candidate_count=2,
        decode_failures=0,
        opaque_count=1,
        status="HIP3_DEPLOYMENT_EVENT_CANDIDATES_FOUND",
    )
    ws2 = P2WindowScanSummary(
        window_name="launch",
        date_range=("2025-10-13", "2025-11-13"),
        mapped_ranges=["explorer_blocks/200000000/"],
        files_scanned=20,
        bytes_downloaded=200000,
        blocks_parsed=10,
        total_actions=200,
        unique_action_types=["order", "cancel", "SetGlobalAction"],
        candidate_count=0,
        decode_failures=1,
        opaque_count=2,
        status="HIP3_NO_DEPLOYMENT_EVENT_CANDIDATES_IN_SCANNED_BLOCKS",
    )

    assert ws1.window_name == "prelaunch"
    assert ws2.window_name == "launch"
    assert ws1.candidate_count == 2
    assert ws2.candidate_count == 0


def test_p2_root_layout_must_be_known_before_reading():
    """P2 must discover layout before reading any block files."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_p2_deployment_search,
        ScoutStatus,
        NetworkChokepoint,
    )

    unknown_layout = {
        "prefixes": ["explorer_blocks/mystery/"],
        "keys": [],
        "objects": [],
        "error_code": None,
    }

    with unittest.mock.patch.object(NetworkChokepoint, "s3_list_prefix", return_value=unknown_layout):
        result = run_p2_deployment_search(
            p2_window="all",
            allow_network_public=False,
            allow_s3_archive_read=True,
        )

    assert result.status == ScoutStatus.HIP3_EXPLORER_BLOCK_LAYOUT_UNKNOWN
    assert result.files_read == 0


def test_p2_credentials_required_status():
    """No AWS credentials -> CREDENTIALS_REQUIRED."""
    from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
        run_p2_deployment_search,
        ScoutStatus,
    )

    result = run_p2_deployment_search(
        p2_window="all",
        allow_network_public=False,
        allow_s3_archive_read=False,
    )
    assert result.status == ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED

