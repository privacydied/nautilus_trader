"""Tests for run_hyperliquid_liq_cluster_prepositioning_phase0_v0 CLI runner."""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from examples.strategies.venue_agnostic_signal_observer.runners.legacy_cli.run_hyperliquid_liq_cluster_prepositioning_phase0_v0 import (
    parse_args,
    main,
    get_git_info,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create a temporary data directory with synthetic asset ctxs."""
    staging = tmp_path / "hyperliquid_asset_ctxs_staging"
    staging.mkdir()

    symbols = ["SOL", "LINK", "AVAX"]
    base_price = {"SOL": 100.0, "LINK": 20.0, "AVAX": 30.0}
    base_oi = {"SOL": 1_000_000, "LINK": 500_000, "AVAX": 800_000}

    start = datetime(2025, 8, 17, tzinfo=timezone.utc)
    for sym in symbols:
        q_dir = staging / "test_q"
        q_dir.mkdir(exist_ok=True)
        filepath = q_dir / f"{sym}.jsonl"
        with open(filepath, "w") as f:
            for day in range(30):
                dt = start + __import__("datetime").timedelta(days=day)
                ts_str = dt.strftime("%Y-%m-%dT00:00:00Z")
                price = base_price[sym] * (1 + 0.01 * day * (1 if day % 2 == 0 else -1))
                oi = base_oi[sym] * (1 + 0.001 * day)
                f.write(json.dumps({
                    "index_price": round(price, 4),
                    "open_interest": round(oi, 4),
                    "price": round(price, 4),
                    "price_source": "mark",
                    "symbol": sym,
                    "ts_event": ts_str,
                }) + "\n")

    return str(tmp_path)


# ---------------------------------------------------------------------------
# Test: parse_args defaults
# ---------------------------------------------------------------------------

def test_parse_args_defaults():
    """Test parse_args returns correct defaults."""
    args = parse_args([])
    assert args.out_root == "reports/hyperliquid_liq_cluster_prepositioning_phase0_v0"
    assert args.data_root == "data"
    assert args.start_date == "2025-08-17"
    assert args.end_date == "latest"
    assert args.dry_run is False
    assert args.plan_only is False
    assert args.skip_null is False
    assert args.null_iterations == 1000
    assert args.seed == 42


# ---------------------------------------------------------------------------
# Test: parse_args custom values
# ---------------------------------------------------------------------------

def test_parse_args_custom():
    """Test parse_args with custom values."""
    args = parse_args([
        "--out-root", "/tmp/custom",
        "--data-root", "/tmp/data",
        "--start-date", "2024-01-01",
        "--end-date", "2024-12-31",
        "--dry-run",
        "--plan-only",
        "--skip-null",
        "--null-iterations", "500",
        "--seed", "123",
        "--symbols", "SOL,LINK",
    ])
    assert args.out_root == "/tmp/custom"
    assert args.data_root == "/tmp/data"
    assert args.start_date == "2024-01-01"
    assert args.end_date == "2024-12-31"
    assert args.dry_run is True
    assert args.plan_only is True
    assert args.skip_null is True
    assert args.null_iterations == 500
    assert args.seed == 123


# ---------------------------------------------------------------------------
# Test: CLI dry run
# ---------------------------------------------------------------------------

def test_cli_dry_run(tmp_data_dir, tmp_path):
    """Test CLI dry run writes preview and returns 0."""
    out_dir = str(tmp_path / "dry_run_output")
    rc = main([
        "--out-root", out_dir,
        "--data-root", tmp_data_dir,
        "--start-date", "2025-08-17",
        "--end-date", "2025-09-15",
        "--dry-run",
    ])
    assert rc == 0
    # Summary is under out_dir/<run_id>/summary.json
    import glob
    summaries = glob.glob(os.path.join(out_dir, "**/summary.json"), recursive=True)
    assert len(summaries) >= 1, f"No summary.json found under {out_dir}"
    with open(summaries[0]) as f:
        data = json.load(f)
    assert data["study_id"] == "hyperliquid_liq_cluster_prepositioning_phase0_v0"
    assert data["orders_used"] is False
    assert data["private_keys_used"] is False


# ---------------------------------------------------------------------------
# Test: CLI plan-only
# ---------------------------------------------------------------------------

def test_cli_plan_only(tmp_data_dir, tmp_path):
    """Test CLI plan-only writes inventory and returns 0."""
    out_dir = str(tmp_path / "plan_output")
    rc = main([
        "--out-root", out_dir,
        "--data-root", tmp_data_dir,
        "--start-date", "2025-08-17",
        "--end-date", "2025-09-15",
        "--plan-only",
    ])
    assert rc == 0
    import glob
    summaries = glob.glob(os.path.join(out_dir, "**/summary.json"), recursive=True)
    assert len(summaries) >= 1
    # Plan-only writes summary.json and summary.md but not input_inventory.json
    # (inventories are written after Phase 0). Just verify summary exists.


# ---------------------------------------------------------------------------
# Test: CLI with no data
# ---------------------------------------------------------------------------

def test_cli_no_data(tmp_path):
    """Test CLI with no data returns blocked status."""
    out_dir = str(tmp_path / "no_data_output")
    rc = main([
        "--out-root", out_dir,
        "--data-root", str(tmp_path),
        "--start-date", "2025-08-17",
        "--end-date", "2025-09-15",
    ])
    # Summary.json is written under out_dir/<run_id>/summary.json
    # Check that at least one summary.json exists under out_dir
    import glob
    summaries = glob.glob(os.path.join(out_dir, "**/summary.json"), recursive=True)
    assert len(summaries) >= 1, f"No summary.json found under {out_dir}"
    with open(summaries[0]) as f:
        data = json.load(f)
    assert "BLOCKED" in data["status"]


# ---------------------------------------------------------------------------
# Test: CLI with custom symbols
# ---------------------------------------------------------------------------

def test_cli_custom_symbols(tmp_data_dir, tmp_path):
    """Test CLI with custom symbols."""
    out_dir = str(tmp_path / "custom_symbols_output")
    rc = main([
        "--out-root", out_dir,
        "--data-root", tmp_data_dir,
        "--start-date", "2025-08-17",
        "--end-date", "2025-09-15",
        "--symbols", "SOL",
        "--dry-run",
    ])
    assert rc == 0


# ---------------------------------------------------------------------------
# Test: CLI writes all expected artifacts
# ---------------------------------------------------------------------------

def test_cli_writes_expected_artifacts(tmp_data_dir, tmp_path):
    """Test CLI writes all expected artifacts."""
    out_dir = str(tmp_path / "full_output")
    rc = main([
        "--out-root", out_dir,
        "--data-root", tmp_data_dir,
        "--start-date", "2025-08-17",
        "--end-date", "2025-09-15",
        "--dry-run",
    ])
    assert rc == 0

    import glob
    # Dry-run writes summary.json, summary.md but NOT input_inventory.json etc.
    # (those are written after Phase 0 in non-dry-run mode)
    for fname in ["summary.json", "summary.md"]:
        matches = glob.glob(os.path.join(out_dir, "**", fname), recursive=True)
        assert len(matches) >= 1, f"Missing artifact: {fname}"


# ---------------------------------------------------------------------------
# Test: get_git_info
# ---------------------------------------------------------------------------

def test_get_git_info():
    """Test get_git_info returns valid data."""
    sha, branch, dirty = get_git_info("/mnt/nasirjones/py/nautilus_trader")
    assert isinstance(sha, str)
    assert isinstance(branch, str)
    assert isinstance(dirty, bool)
    assert len(sha) <= 12  # truncated SHA


# ---------------------------------------------------------------------------
# Test: CLI forbidden statuses not in summary
# ---------------------------------------------------------------------------

def test_cli_forbidden_statuses(tmp_data_dir, tmp_path):
    """Test CLI summary does not contain forbidden statuses."""
    out_dir = str(tmp_path / "forbidden_output")
    rc = main([
        "--out-root", out_dir,
        "--data-root", tmp_data_dir,
        "--start-date", "2025-08-17",
        "--end-date", "2025-09-15",
        "--dry-run",
    ])
    assert rc == 0

    import glob
    summaries = glob.glob(os.path.join(out_dir, "**/summary.json"), recursive=True)
    assert len(summaries) >= 1
    with open(summaries[0]) as f:
        data = json.load(f)

    forbidden = {"REJECTED", "PROFITABLE", "ALPHA_FOUND", "TRADE_READY",
                 "EXECUTION_READY", "LIVE_READY", "CANDIDATE_FOR_LIVE",
                 "PAPER_STRATEGY_PROMOTED", "PROMOTION_AUTHORIZED",
                 "EDGE_CONFIRMED", "READY_FOR_PHASE_1", "READY_FOR_PHASE_0"}

    assert data["forbidden_statuses_not_emitted"] is True
    assert data["status"] not in forbidden


# ---------------------------------------------------------------------------
# Test: CLI safety flags
# ---------------------------------------------------------------------------

def test_cli_safety_flags(tmp_data_dir, tmp_path):
    """Test CLI summary safety flags are all false."""
    out_dir = str(tmp_path / "safety_output")
    rc = main([
        "--out-root", out_dir,
        "--data-root", tmp_data_dir,
        "--start-date", "2025-08-17",
        "--end-date", "2025-09-15",
        "--dry-run",
    ])
    assert rc == 0

    import glob
    summaries = glob.glob(os.path.join(out_dir, "**/summary.json"), recursive=True)
    assert len(summaries) >= 1
    with open(summaries[0]) as f:
        data = json.load(f)

    assert data["orders_used"] is False
    assert data["private_keys_used"] is False
    assert data["auth_used"] is False
    assert data["live_execution_used"] is False
    assert data["paper_trading_used"] is False
    assert data["shadow_execution_used"] is False
    assert data["registry_mutated"] is False
    assert data["systemd_mutated"] is False
    assert data["bot_path_mutated"] is False
