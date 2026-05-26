"""Tests for the HLP backstop archive availability probe CLI.

Covers argument parsing, default-no-network behavior, S3 flag requirements,
schema verdicts, and safety compliance.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from examples.strategies.venue_agnostic_signal_observer.run_hlp_backstop_archive_availability_probe import (
    main,
    run_id,
    human_bytes,
    parse_ls_line,
    determine_node_fills_verdict,
    probe_bucket_top,
    probe_generic_prefix,
    probe_date,
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


def test_human_bytes_gib():
    """human_bytes converts GiB."""
    result = human_bytes(1073741824)
    assert "1.0 GiB" in result or "1.0 GiB" in result


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


def test_parse_ls_line_empty():
    """Empty line returns None."""
    assert parse_ls_line("") is None


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
    rc = main(["--probe-date", "2026-05-24", "--skip-node-fills", "--skip-misc-events"])
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
            "--allow-s3",
        ])
    assert rc == 0


# ===========================================================================
# S3 flag requirements
# ===========================================================================


def test_skip_node_fills_creates_report():
    """Skipping node fills still creates report artifacts."""
    with tempfile.TemporaryDirectory() as tmp:
        rc = main([
            "--probe-date", "2026-05-24",
            "--reports-root", tmp,
            "--work-dir", os.path.join(tmp, "work"),
            "--skip-node-fills",
            "--skip-misc-events",
            "--allow-s3",
        ])
        assert rc == 0
        report_dirs = list(Path(tmp).rglob("summary.json"))
        assert len(report_dirs) >= 1


def test_report_artifacts_include_misc_schema():
    """Report artifacts include misc_events_schema.json."""
    with tempfile.TemporaryDirectory() as tmp:
        rc = main([
            "--probe-date", "2026-05-24",
            "--reports-root", tmp,
            "--work-dir", os.path.join(tmp, "work"),
            "--skip-node-fills",
            "--skip-misc-events",
            "--allow-s3",
        ])
        assert rc == 0
        # Should find a misc_events_schema.json artifact
        report_dirs = list(Path(tmp).rglob("misc_events_schema.json"))
        assert len(report_dirs) >= 1


# ===========================================================================
# Safety checks
# ===========================================================================


def test_no_registry_strings():
    """CLI does not contain registry-write or promotion strings."""
    import examples.strategies.venue_agnostic_signal_observer.run_hlp_backstop_archive_availability_probe as mod
    content = open(mod.__file__).read()
    assert "submit_order" not in content
    assert "private_key" not in content
    assert "live_execute" not in content
    assert "paper_broker" not in content
    assert "conductor" not in content.lower() or "not conductor" in content


def test_no_user_fills_by_time():
    """CLI does not contain userFillsByTime."""
    import examples.strategies.venue_agnostic_signal_observer.run_hlp_backstop_archive_availability_probe as mod
    content = open(mod.__file__).read()
    assert "userFillsByTime" not in content


# ===========================================================================
# Mock S3 behavior
# ===========================================================================


def test_probe_bucket_top_no_network():
    """probe_bucket_top without network fails."""
    with pytest.raises(RuntimeError):
        probe_bucket_top(timeout=1)


def test_probe_generic_prefix_no_network():
    """probe_generic_prefix without network fails."""
    with pytest.raises(RuntimeError):
        probe_generic_prefix("nonexistent_prefix", timeout=1)


def test_probe_date_no_network():
    """probe_date without network fails."""
    with pytest.raises(RuntimeError):
        probe_date("20260524", "nonexistent_prefix", timeout=1)


# ===========================================================================
# Max-day / byte threshold (code path)
# ===========================================================================


def test_parse_ls_line_large_size():
    """Parse a large file size correctly."""
    line = "2026-05-24 17:02:40   49.9 MiB 14.lz4"
    parsed = parse_ls_line(line)
    assert parsed is not None
    assert parsed["name"] == "14.lz4"
    assert parsed["size_bytes"] == int(49.9 * 1024 * 1024)
    assert "49.9 MiB" in parsed["size_human"]


def test_parse_ls_line_small():
    """Parse a KiB-sized file."""
    line = "2026-05-24 03:02:16  256.0 KiB test.json"
    parsed = parse_ls_line(line)
    assert parsed is not None
    assert parsed["name"] == "test.json"
    assert parsed["size_bytes"] == 262144
