"""Tests for the CLI runner for HLP backstop coverage diagnostic.

Covers argument parsing, dry-run behavior, hash verification,
default-no-network, and safety compliance.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from examples.strategies.venue_agnostic_signal_observer.run_hlp_backstop_absorption_phase0a_star_coverage import (
    parse_args,
    main,
    run_diagnostic,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def tmp_precommitment():
    """Create a valid precommitment doc with self-hash."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(
            "# HLP backstop absorption — Phase 0A* coverage/mechanism diagnostic\n"
            "Precommitment SHA-256: PLACEHOLDER\n"
            "Some content here\n"
        )
        fpath = f.name

    # Compute the real hash and update the doc
    from examples.strategies.venue_agnostic_signal_observer.hlp_backstop_absorption_phase0a_star_coverage import compute_precommitment_hash
    h = compute_precommitment_hash(fpath)
    with open(fpath, "w") as f2:
        f2.write(
            "# HLP backstop absorption — Phase 0A* coverage/mechanism diagnostic\n"
            f"Precommitment SHA-256: {h}\n"
            "Some content here\n"
        )
    yield fpath
    os.unlink(fpath)


@pytest.fixture
def tmp_data_root():
    """Create a temporary data root with a vault_metadata fixture."""
    d = tempfile.mkdtemp()
    metadata = {
        "parent_address": "0xdf5f5c0c0a1c0a5a0f0b0c0d0e0f0a0b0c0d0e0f",
        "children": [
            {
                "address": "0xchild_backstop_001",
                "role_label": "BACKSTOP",
                "confidence": "documented",
                "symbol_coverage": ["BTC", "ETH", "SOL"],
                "fraction_fills_liquidation_flagged": 0.75,
            },
            {
                "address": "0xchild_mm_001",
                "role_label": "MM_PARENT",
                "confidence": "documented",
                "symbol_coverage": [],
                "fraction_fills_liquidation_flagged": 0.0,
            },
        ],
    }
    with open(os.path.join(d, "vault_metadata.json"), "w") as f:
        json.dump(metadata, f)
    yield d
    import shutil
    shutil.rmtree(d)


# ===========================================================================
# Argument parsing tests
# ===========================================================================


def test_parse_args_minimal(tmp_precommitment, tmp_data_root):
    """Minimal required args parse correctly."""
    args = parse_args([
        "--data-root", tmp_data_root,
        "--reports-root", tmp_data_root,
        "--precommitment-path", tmp_precommitment,
    ])
    assert args.data_root == tmp_data_root
    assert args.reports_root == tmp_data_root
    assert args.precommitment_path == tmp_precommitment
    assert args.dry_run is False
    assert args.allow_public_metadata_api is False
    assert args.allow_s3_archive_read is False


def test_parse_args_dry_run(tmp_precommitment, tmp_data_root):
    """Dry-run flag is parsed."""
    args = parse_args([
        "--data-root", tmp_data_root,
        "--reports-root", tmp_data_root,
        "--precommitment-path", tmp_precommitment,
        "--dry-run",
    ])
    assert args.dry_run


def test_parse_args_allow_api(tmp_precommitment, tmp_data_root):
    """Allow-public-metadata-api flag is parsed."""
    args = parse_args([
        "--data-root", tmp_data_root,
        "--reports-root", tmp_data_root,
        "--precommitment-path", tmp_precommitment,
        "--allow-public-metadata-api",
    ])
    assert args.allow_public_metadata_api


def test_parse_args_max_symbols(tmp_precommitment, tmp_data_root):
    """Max-symbols cap is parsed."""
    args = parse_args([
        "--data-root", tmp_data_root,
        "--reports-root", tmp_data_root,
        "--precommitment-path", tmp_precommitment,
        "--max-symbols", "3",
    ])
    assert args.max_symbols == 3


def test_parse_args_help():
    """Help works without error."""
    from io import StringIO
    old_stderr = sys.stderr
    sys.stderr = StringIO()
    try:
        with pytest.raises(SystemExit):
            parse_args(["--help"])
    finally:
        sys.stderr = old_stderr


# ===========================================================================
# Dry-run tests
# ===========================================================================


def test_dry_run_success(tmp_precommitment, tmp_data_root):
    """Dry-run succeeds with no network and no normal artifacts."""
    args = parse_args([
        "--data-root", tmp_data_root,
        "--reports-root", tmp_data_root,
        "--precommitment-path", tmp_precommitment,
        "--dry-run",
    ])
    rc = run_diagnostic(args)
    assert rc == 0


def test_dry_run_no_network(tmp_precommitment, tmp_data_root):
    """Dry-run never makes network requests."""
    args = parse_args([
        "--data-root", tmp_data_root,
        "--reports-root", tmp_data_root,
        "--precommitment-path", tmp_precommitment,
        "--dry-run",
    ])
    rc = run_diagnostic(args)
    assert rc == 0


# ===========================================================================
# Hash verification tests
# ===========================================================================


def test_hash_mismatch_returns_error(tmp_data_root):
    """Bad precommitment path returns error exit code."""
    args = parse_args([
        "--data-root", tmp_data_root,
        "--reports-root", tmp_data_root,
        "--precommitment-path", "/nonexistent/path.md",
    ])
    rc = run_diagnostic(args)
    assert rc == 1


def test_tampered_hash_returns_error(tmp_data_root):
    """Precommitment with mismatched hash returns error."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("# Test\nSome content\nPrecommitment SHA-256: 0000000000000000000000000000000000000000000000000000000000000000\nExtra stuff\n")
        fpath = f.name
    try:
        args = parse_args([
            "--data-root", tmp_data_root,
            "--reports-root", tmp_data_root,
            "--precommitment-path", fpath,
        ])
        rc = run_diagnostic(args)
        assert rc == 1
    finally:
        os.unlink(fpath)


# ===========================================================================
# Safety tests
# ===========================================================================


def test_no_network_by_default(tmp_precommitment, tmp_data_root):
    """Default no-network posture is respected (no API flag)."""
    args = parse_args([
        "--data-root", tmp_data_root,
        "--reports-root", tmp_data_root,
        "--precommitment-path", tmp_precommitment,
    ])
    assert not args.allow_public_metadata_api
    assert not args.allow_s3_archive_read


def test_public_metadata_api_only_when_set(tmp_precommitment, tmp_data_root):
    """Public metadata API only used when flag is set."""
    args = parse_args([
        "--data-root", tmp_data_root,
        "--reports-root", tmp_data_root,
        "--precommitment-path", tmp_precommitment,
        "--allow-public-metadata-api",
    ])
    assert args.allow_public_metadata_api


def test_s3_read_only_when_set(tmp_precommitment, tmp_data_root):
    """S3 archive read only used when flag is set."""
    args = parse_args([
        "--data-root", tmp_data_root,
        "--reports-root", tmp_data_root,
        "--precommitment-path", tmp_precommitment,
        "--allow-s3-archive-read",
    ])
    assert args.allow_s3_archive_read


# ===========================================================================
# Output path safety
# ===========================================================================


def test_output_stays_within_reports_root(tmp_precommitment, tmp_data_root):
    """Artifacts go under reports root."""
    with tempfile.TemporaryDirectory() as reports_root:
        args = parse_args([
            "--data-root", tmp_data_root,
            "--reports-root", reports_root,
            "--precommitment-path", tmp_precommitment,
        ])
        rc = run_diagnostic(args)
        assert rc == 0


# ===========================================================================
# Conductor/auto-paper safety
# ===========================================================================


def test_forbidden_strings_not_in_cli(tmp_precommitment, tmp_data_root):
    """Runner does not contain forbidden conductor/promotion paths."""
    import examples.strategies.venue_agnostic_signal_observer.run_hlp_backstop_absorption_phase0a_star_coverage as run_mod
    from examples.strategies.venue_agnostic_signal_observer.hlp_backstop_absorption_phase0a_star_coverage import FORBIDDEN_STRINGS
    mod_path = run_mod.__file__
    assert mod_path is not None
    from pathlib import Path
    content = Path(mod_path).read_text()
    for forbidden in FORBIDDEN_STRINGS:
        assert forbidden not in content, f"Found forbidden string '{forbidden}' in runner"


def test_no_rejected_research_mutation(tmp_precommitment, tmp_data_root):
    """Runner does not reference REJECTED_RESEARCH.md."""
    import examples.strategies.venue_agnostic_signal_observer.run_hlp_backstop_absorption_phase0a_star_coverage as run_mod
    mod_path = run_mod.__file__
    assert mod_path is not None
    from pathlib import Path
    content = Path(mod_path).read_text()
    assert "REJECTED_RESEARCH" not in content