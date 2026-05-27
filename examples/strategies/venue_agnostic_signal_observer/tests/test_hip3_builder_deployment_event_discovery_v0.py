#!/usr/bin/env python3
"""Tests for the HIP-3 Builder Deployment Event Discovery probe module."""

import sys
from pathlib import Path

# Ensure repo root on path
repo_root = Path(__file__).resolve().parents[5]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import unittest.mock as mock

from examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 import (
    run_probe,
    ScoutStatus,
    EXPLORER_BLOCK_BUCKET,
    EXPLORER_BLOCK_PREFIX,
    MARKET_DATA_BUCKET,
    NetworkChokepoint,
    _discover_explorer_block_layout,
)

_SCOUT_SRC = Path(__file__).resolve().parents[1] / "hip3_builder_deployment_event_discovery_v0.py"


def test_no_subprocess_in_production_source():
    """Production scout source must not import or use subprocess."""
    src = _SCOUT_SRC.read_text()
    assert "import subprocess" not in src, "Production source must not import subprocess"
    assert "subprocess.run" not in src, "Production source must not call subprocess.run"
    assert "subprocess.Popen" not in src, "Production source must not call subprocess.Popen"


def test_no_os_system_in_production_source():
    """Production scout source must not use os.system."""
    src = _SCOUT_SRC.read_text()
    assert "os.system(" not in src, "Production source must not call os.system"


def test_no_eval_in_production_source():
    """Production scout source must not use eval."""
    src = _SCOUT_SRC.read_text()
    assert "eval(" not in src, "Production source must not call eval"


def test_s3_listing_only_through_chokepoint():
    """Production source must not construct boto3 S3 clients outside NetworkChokepoint methods."""
    src = _SCOUT_SRC.read_text()
    import re
    # boto3.client calls outside of class NetworkChokepoint or _list_explorer_block_files helper
    # should not appear — i.e. no raw boto3.client("s3") outside the chokepoint class.
    # We check that direct boto3 S3 client construction only appears inside known helpers.
    lines = src.splitlines()
    boto3_lines = [i for i, l in enumerate(lines, 1) if "boto3.client(" in l]
    # All boto3.client calls should be within methods of NetworkChokepoint or known helpers.
    # Simple check: none should be at module level (indentation of 0).
    for lineno in boto3_lines:
        line = lines[lineno - 1]
        assert not line.startswith("boto3.client("), (
            f"Line {lineno}: boto3.client at module level — must be inside NetworkChokepoint or helper"
        )


_MOD = "examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0"


def test_missing_credentials_maps_to_credentials_required():
    """No AWS credentials → HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED."""
    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    with mock.patch(f"{_MOD}._BOTO3_AVAILABLE", True):
        with mock.patch.object(cp, "s3_list_prefix", return_value={"prefixes": [], "keys": [], "error_code": "NO_CREDENTIALS"}):
            info = _discover_explorer_block_layout(cp)
    assert info["status"] == ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_CREDENTIALS_REQUIRED


def test_requester_pays_access_denied_maps_to_access_denied():
    """AccessDenied S3 error → HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_ACCESS_DENIED."""
    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    with mock.patch(f"{_MOD}._BOTO3_AVAILABLE", True):
        with mock.patch.object(cp, "s3_list_prefix", return_value={"prefixes": [], "keys": [], "error_code": "AccessDenied"}):
            info = _discover_explorer_block_layout(cp)
    assert info["status"] == ScoutStatus.HIP3_EXPLORER_BLOCK_REQUESTER_PAYS_ACCESS_DENIED


def test_generic_s3_error_maps_to_root_listing_failed():
    """Unrelated S3 error → HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED."""
    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    with mock.patch(f"{_MOD}._BOTO3_AVAILABLE", True):
        with mock.patch.object(cp, "s3_list_prefix", return_value={"prefixes": [], "keys": [], "error_code": "InternalError"}):
            info = _discover_explorer_block_layout(cp)
    assert info["status"] == ScoutStatus.HIP3_EXPLORER_BLOCK_ROOT_LISTING_FAILED


def test_s3_list_prefix_passes_requester_payer():
    """s3_list_prefix must include RequestPayer='requester' in the boto3 call."""
    import types
    import importlib

    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)
    captured_kwargs: dict = {}

    fake_s3 = mock.MagicMock()
    fake_s3.list_objects_v2.side_effect = lambda **kw: (captured_kwargs.update(kw) or {"CommonPrefixes": [], "Contents": []})

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = mock.MagicMock(return_value=fake_s3)  # type: ignore

    # Patch into module namespace so s3_list_prefix picks it up.
    import examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 as _mod_ref
    with mock.patch(f"{_MOD}._BOTO3_AVAILABLE", True):
        with mock.patch.object(_mod_ref, "boto3", fake_boto3, create=True):
            cp.s3_list_prefix("hl-mainnet-node-data", "explorer_blocks/", requester_pays=True)

    assert captured_kwargs.get("RequestPayer") == "requester"


def test_aws_account_suffix_at_most_4_chars():
    """aws_account_suffix must never exceed 4 characters."""
    result = run_probe(
        start_date="2025-10-13",
        dry_run=True,
        allow_s3_archive_read=True,
    )
    assert len(result.aws_account_suffix) <= 4, (
        f"aws_account_suffix is {result.aws_account_suffix!r} — must be at most 4 chars"
    )


def test_full_account_id_not_in_probe_result():
    """Full AWS account ID (12+ digits) must never appear in ProbeResult fields."""
    fake_account = "123456789012"
    fake_suffix = fake_account[-4:]

    with mock.patch(
        "examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0._aws_identity_preflight",
        return_value=(True, fake_suffix),
    ):
        result = run_probe(start_date="2025-10-13", dry_run=True, allow_s3_archive_read=True)

    import dataclasses
    result_dict = dataclasses.asdict(result)
    flat = str(result_dict)
    assert fake_account not in flat, "Full AWS account ID must not appear in ProbeResult artifacts"


def test_no_deployment_events_requires_parsed_blocks():
    """HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS requires at least one block was parsed."""
    cp = NetworkChokepoint(allow_network_public=False, allow_s3_archive_read=True)

    # Simulate layout discovery succeeding with date_partitioned layout
    layout_result = {
        "status": ScoutStatus.HIP3_EXPLORER_BLOCK_LAYOUT_DISCOVERED,
        "layout": "date_partitioned",
        "prefixes": ["explorer_blocks/2025/10/13/"],
        "keys": [],
    }

    # One block file returned, zero bytes (simulate download succeeding but empty content)
    import examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0 as mod

    with mock.patch.object(cp, "s3_list_prefix", return_value={"prefixes": ["explorer_blocks/2025/10/13/"], "keys": [], "error_code": None}):
        with mock.patch(
            "examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0._discover_explorer_block_layout",
            return_value=layout_result,
        ):
            with mock.patch(
                "examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0._validate_explorer_block_source",
                return_value=(True, ["explorer_blocks/2025/10/13/fake.json.lz4"]),
            ):
                with mock.patch(
                    "examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0._list_explorer_block_files",
                    return_value=[("s3://hl-mainnet-node-data/explorer_blocks/2025/10/13/fake.json.lz4", 100)],
                ):
                    with mock.patch(
                        "examples.strategies.venue_agnostic_signal_observer.hip3_builder_deployment_event_discovery_v0._download_and_process_block",
                        return_value=[],  # block parsed, zero candidates
                    ) as mock_download:
                        result = run_probe(
                            start_date="2025-10-13",
                            max_days=1,
                            max_block_files=1,
                            allow_network_public=False,
                            allow_s3_archive_read=True,
                            dry_run=False,
                        )

    assert result.status == ScoutStatus.HIP3_NO_DEPLOYMENT_EVENTS_IN_PUBLIC_BLOCKS
    assert mock_download.called, "At least one block must have been processed"


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
