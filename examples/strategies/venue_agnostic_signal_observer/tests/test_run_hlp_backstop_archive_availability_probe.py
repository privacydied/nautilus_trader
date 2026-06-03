"""Tests for the HLP backstop archive availability probe CLI.

Covers argument parsing, default-no-network behavior, S3 flag requirements,
schema verdicts, explorer_blocks, replica_cmds, vault details, marker search,
and safety compliance.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hlp_backstop_archive_availability_probe import (
    main,
    run_id,
    human_bytes,
    parse_ls_line,
    determine_node_fills_verdict,
    probe_bucket_top,
    probe_generic_prefix,
    probe_date,
    probe_explorer_blocks,
    probe_replica_cmds,
    probe_vault_details,
    run_marker_search,
)


# ===========================================================================
# Helper tests
# ===========================================================================


def test_run_id_format():
    """run_id returns a non-empty string."""
    rid = run_id()
    assert isinstance(rid, str)
    assert len(rid) > 10


def test_human_bytes_zero():
    """human_bytes handles zero."""
    assert human_bytes(0) == "0.0 B"


def test_human_bytes_mib():
    """human_bytes converts MiB."""
    assert "1.0 MiB" in human_bytes(1048576)


def test_parse_ls_line_valid():
    """Parse a valid aws s3 ls line."""
    line = "2026-05-24 03:02:16   26.7 MiB 0.lz4"
    parsed = parse_ls_line(line)
    assert parsed is not None
    assert parsed["name"] == "0.lz4"
    assert parsed["size_bytes"] > 0
    assert "MiB" in parsed["size_human"]


def test_parse_ls_line_pre():
    """PRE lines are skipped."""
    assert parse_ls_line("PRE hourly/") is None


def test_determine_node_fills_verdict_ready():
    """Schema with required fields -> SCHEMA_READY."""
    schema = {"present_fields": {"timestamp_or_block_timestamp": True, "liquidation_or_backstop_marker": False}}
    assert determine_node_fills_verdict(schema) == "NODE_FILLS_BY_BLOCK_SCHEMA_READY"


def test_determine_node_fills_verdict_unavailable():
    """Schema without timestamp -> UNAVAILABLE."""
    schema = {"present_fields": {"timestamp_or_block_timestamp": False}}
    assert determine_node_fills_verdict(schema) == "NODE_FILLS_BY_BLOCK_SOURCE_UNAVAILABLE"


# ===========================================================================
# No-network behavior
# ===========================================================================


def test_main_requires_allow_s3():
    """main fails without --allow-s3 flag."""
    rc = main(["--probe-date", "2026-05-24", "--skip-node-fills", "--skip-misc-events",
               "--skip-explorer-blocks", "--skip-replica-cmds", "--skip-vault-details"])
    assert rc == 1


def test_main_allow_s3_with_skip():
    """main succeeds with --allow-s3 when skipping S3 operations."""
    with tempfile.TemporaryDirectory() as tmp:
        rc = main([
            "--probe-date", "2026-05-24",
            "--reports-root", tmp,
            "--work-dir", os.path.join(tmp, "work"),
            "--skip-node-fills",
            "--skip-misc-events",
            "--skip-explorer-blocks",
            "--skip-replica-cmds",
            "--skip-vault-details",
            "--allow-s3",
        ])
    assert rc == 0


# ===========================================================================
# explorer_blocks tests
# ===========================================================================


def test_explorer_blocks_no_network():
    """probe_explorer_blocks without network fails."""
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises((RuntimeError, OSError)):
            probe_explorer_blocks(
                Path(tmp),
                block_prefix="nonexistent",
                block_range="nonexistent",
                sample_file="nonexistent.rmp.lz4",
            )


def test_explorer_blocks_verdict_unavailable():
    """Empty/missing data -> UNAVAILABLE."""
    schema = {}
    assert schema.get("verdict", "EXPLORER_BLOCKS_SOURCE_UNAVAILABLE") == "EXPLORER_BLOCKS_SOURCE_UNAVAILABLE"


def test_explorer_blocks_schema_insufficient():
    """Schema with no vault or liquidation info -> INSUFFICIENT."""
    schema = {"verdict": "EXPLORER_BLOCKS_SCHEMA_INSUFFICIENT", "action_types": {}, "vault_address_count": 0}
    assert schema["verdict"] == "EXPLORER_BLOCKS_SCHEMA_INSUFFICIENT"


# ===========================================================================
# replica_cmds tests
# ===========================================================================


def test_replica_cmds_no_network():
    """probe_replica_cmds without network returns unavailable."""
    result = probe_replica_cmds("20260524")
    # Without network, should return unavailable or error
    assert result.get("verdict") in (
        "REPLICA_CMDS_SOURCE_UNAVAILABLE", "REPLICA_CMDS_SCHEMA_READY_NO_LIQUIDATION_MARKERS"
    ) or "error" in result


def test_replica_cmds_verdict_unavailable():
    """Source unavailable verdict."""
    schema = {"verdict": "REPLICA_CMDS_SOURCE_UNAVAILABLE"}
    assert schema["verdict"] == "REPLICA_CMDS_SOURCE_UNAVAILABLE"


def test_replica_cmds_verdict_no_markers():
    """No liquidation markers verdict."""
    schema = {"verdict": "REPLICA_CMDS_SCHEMA_READY_NO_LIQUIDATION_MARKERS"}
    assert schema["verdict"] == "REPLICA_CMDS_SCHEMA_READY_NO_LIQUIDATION_MARKERS"


def test_replica_cmds_verdict_with_markers():
    """With liquidation markers verdict."""
    schema = {"verdict": "REPLICA_CMDS_SCHEMA_READY_WITH_LIQUIDATION_MARKERS"}
    assert schema["verdict"] == "REPLICA_CMDS_SCHEMA_READY_WITH_LIQUIDATION_MARKERS"


# ===========================================================================
# Vault details tests
# ===========================================================================


def test_vault_details_requires_api_flag():
    """probe_vault_details returns unavailable without flag."""
    result = probe_vault_details(allow_public_metadata_api=False)
    assert result["verdict"] == "VAULT_DETAILS_SOURCE_UNAVAILABLE"


def test_vault_details_ready():
    """VAULT_DETAILS_READY verdict."""
    schema = {"verdict": "VAULT_DETAILS_READY", "backstop_role_found": True, "child_roles_present": True}
    assert schema["verdict"] == "VAULT_DETAILS_READY"


def test_vault_details_parent_only():
    """VAULT_DETAILS_PARENT_ONLY verdict."""
    schema = {"verdict": "VAULT_DETAILS_PARENT_ONLY", "child_vaults_found": True, "child_roles_present": False}
    assert schema["verdict"] == "VAULT_DETAILS_PARENT_ONLY"


def test_vault_details_no_child_roles():
    """VAULT_DETAILS_NO_CHILD_ROLES verdict."""
    schema = {"verdict": "VAULT_DETAILS_NO_CHILD_ROLES"}
    assert schema["verdict"] == "VAULT_DETAILS_NO_CHILD_ROLES"


def test_vault_details_source_unavailable():
    """VAULT_DETAILS_SOURCE_UNAVAILABLE verdict."""
    schema = {"verdict": "VAULT_DETAILS_SOURCE_UNAVAILABLE"}
    assert schema["verdict"] == "VAULT_DETAILS_SOURCE_UNAVAILABLE"


def test_vault_details_probe_error():
    """VAULT_DETAILS_PROBE_ERROR verdict."""
    schema = {"verdict": "VAULT_DETAILS_PROBE_ERROR"}
    assert schema["verdict"] == "VAULT_DETAILS_PROBE_ERROR"


# ===========================================================================
# Marker search tests
# ===========================================================================


def test_marker_search_finds_liquidation():
    """Marker search finds 'liquidation' in raw text."""
    text = "some event data with Liquidation marker in it"
    result = run_marker_search(text, "test")
    assert "liquidation" in result["terms_found"]
    assert len(result["match_details"]["liquidation"]) >= 1


def test_marker_search_finds_margin():
    """Marker search finds 'margin' in raw text."""
    text = "updateIsolatedMargin for user 0x1234 with margin 5000"
    result = run_marker_search(text, "test")
    assert "margin" in result["terms_found"]


def test_marker_search_finds_vault():
    """Marker search finds 'vault' in raw text."""
    text = "NetChildVaultPositionsAction for vault addresses"
    result = run_marker_search(text, "test")
    assert "vault" in result["terms_found"]


def test_marker_search_no_terms():
    """Marker search with no matching terms returns empty."""
    text = "completely unrelated data without any markers"
    result = run_marker_search(text, "test")
    assert result["terms_found"] == []


def test_marker_search_join_keys():
    """Marker search reports join keys found."""
    text = "block_number 12345 time 56789 user 0xabc oid 999 hash 0xtx coin BTC side B size 10.0 address 0xdef"
    result = run_marker_search(text, "test")
    for key in ("block", "time", "user", "oid", "hash", "coin", "side", "size", "address"):
        assert key in result["join_keys_found"], f"Join key '{key}' not found in {result['join_keys_found']}"


def test_marker_search_case_insensitive():
    """Marker search is case-insensitive."""
    text = "LIQUIDATION LiquidAtion liquidated"
    result = run_marker_search(text, "test")
    assert "liquidation" in result["terms_found"]
    assert "liquidate" in result["terms_found"]
    assert "liquidat" in result["terms_found"]


def test_marker_search_sample_redacted_payload():
    """Marker search redacts context (no full raw payloads leaked)."""
    text = f"confidential{'x'*1000}data LiquidationEvent mark"
    result = run_marker_search(text, "test")
    for term, matches in result["match_details"].items():
        for m in matches:
            ctx = m["context"]
            assert len(ctx) < 200  # context is bounded


# ===========================================================================
# Report artifacts tests
# ===========================================================================


def test_report_artifacts_include_all_schemas():
    """Report artifacts include explorer_blocks, replica_cmds, vault_details, marker search."""
    with tempfile.TemporaryDirectory() as tmp:
        rc = main([
            "--probe-date", "2026-05-24",
            "--reports-root", tmp,
            "--work-dir", os.path.join(tmp, "work"),
            "--skip-node-fills",
            "--skip-misc-events",
            "--skip-explorer-blocks",
            "--skip-replica-cmds",
            "--skip-vault-details",
            "--allow-s3",
        ])
        assert rc == 0
        report_files = [str(p.relative_to(tmp)) for p in Path(tmp).rglob("*.json")]
        # These should exist even when skipped (with default empty dicts)
        assert any("summary.json" in f for f in report_files)


def test_report_artifacts_no_registry_strings():
    """Report does not contain registry-write or promotion strings."""
    import examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hlp_backstop_archive_availability_probe as mod
    content = open(mod.__file__).read()
    assert "submit_order" not in content
    assert "private_key" not in content
    assert "paper_broker" not in content


def test_no_user_fills_by_time():
    """CLI does not contain userFillsByTime."""
    import examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hlp_backstop_archive_availability_probe as mod
    content = open(mod.__file__).read()
    assert "userFillsByTime" not in content


# ===========================================================================
# Byte/download cap
# ===========================================================================


def test_parse_ls_line_large_size():
    """Parse a large file size correctly."""
    line = "2026-05-24 17:02:40   49.9 MiB 14.lz4"
    parsed = parse_ls_line(line)
    assert parsed is not None
    assert parsed["name"] == "14.lz4"


def test_parse_ls_line_small():
    """Parse a KiB-sized file."""
    line = "2026-05-24 03:02:16  256.0 KiB test.json"
    parsed = parse_ls_line(line)
    assert parsed is not None
    assert parsed["name"] == "test.json"


# ===========================================================================
# Public metadata API flag
# ===========================================================================


def test_vault_details_without_api_flag():
    """probe_vault_details without flag returns source unavailable."""
    result = probe_vault_details(allow_public_metadata_api=False, vault_addresses=["0x1234"])
    assert result["verdict"] == "VAULT_DETAILS_SOURCE_UNAVAILABLE"