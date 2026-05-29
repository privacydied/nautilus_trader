#!/usr/bin/env python3
"""Tests for FLX oracle frequency confirmation mode in hip3_replica_cmds_setoracle_reconstruction_v0."""

import json
import sys
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = str(Path(__file__).resolve().parents[5])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from examples.strategies.venue_agnostic_signal_observer import (
    hip3_replica_cmds_setoracle_reconstruction_v0 as mod,
)
MOD = "examples.strategies.venue_agnostic_signal_observer.hip3_replica_cmds_setoracle_reconstruction_v0"

from examples.strategies.venue_agnostic_signal_observer.hip3_replica_cmds_setoracle_reconstruction_v0 import (
    FullFileHistogramResult,
    ReplicaCmdsProbeConfig,
    ReplicaCmdsSourceConfig,
    RuntimeBudgetState,
    run_flx_frequency_confirmation,
    main,
)
from datetime import datetime, timezone


def _make_source_config(allow_s3=False):
    return ReplicaCmdsSourceConfig(allow_s3_archive_read=allow_s3, requester_pays=True)


def _make_config(
    target_dates=None,
    flx_frequency_confirmation=False,
    max_full_files_per_date=2,
    max_full_files_total=8,
    stop_on_flx_found=True,
    out_root=None,
):
    src = _make_source_config(allow_s3=False)
    return ReplicaCmdsProbeConfig(
        out_root=out_root or Path("/tmp/test_flx_freq"),
        target_dates=target_dates or [],
        flx_frequency_confirmation=flx_frequency_confirmation,
        max_full_files_per_date=max_full_files_per_date,
        max_full_files_total=max_full_files_total,
        stop_on_flx_found=stop_on_flx_found,
        source=src,
    )


def _make_histogram(
    dex_counts=None,
    oracle_pxs_by_dex=None,
    markets_by_dex=None,
    flx_seen=False,
    flx_tsla_found=False,
    flx_nvda_found=False,
    cash_tsla_found=False,
    cash_nvda_found=False,
    km_seen=False,
    perpdeploy_count=100,
    setoracle_count=100,
    oracle_pxs_count=500,
    bytes_downloaded=100_000_000,
    records_decoded=1000,
    status="REPLICA_CMDS_FULL_FILE_HISTOGRAM_COMPLETE",
):
    h = FullFileHistogramResult()
    h.status = status
    h.flx_seen = flx_seen
    h.flx_tsla_found = flx_tsla_found
    h.flx_nvda_found = flx_nvda_found
    h.cash_tsla_found = cash_tsla_found
    h.cash_nvda_found = cash_nvda_found
    h.km_seen = km_seen
    h.perpDeploy_count = perpdeploy_count
    h.setOracle_payload_count = setoracle_count
    h.oraclePxs_pair_count = oracle_pxs_count
    h.bytes_downloaded = bytes_downloaded
    h.records_decoded = records_decoded
    h.setOracle_payloads_by_dex = dex_counts or {}
    h.oraclePxs_pairs_by_dex = oracle_pxs_by_dex or {}
    h.markets_seen_by_dex = markets_by_dex or {}
    h.bounded_candidate_examples_by_dex = defaultdict(list)
    return h


# ---------------------------------------------------------------------------
# 1. Multi-date selection preserves post-HIP3 date ordering
# ---------------------------------------------------------------------------

def test_target_dates_preserved_in_config():
    config = _make_config(
        target_dates=["2026-05-27", "2026-05-28", "2026-03-01"],
        flx_frequency_confirmation=True,
    )
    assert config.target_dates == ["2026-05-27", "2026-05-28", "2026-03-01"]
    assert config.flx_frequency_confirmation is True


# ---------------------------------------------------------------------------
# 2. Max full files per date enforced in config
# ---------------------------------------------------------------------------

def test_max_full_files_per_date_config():
    config = _make_config(
        target_dates=["2026-05-27"],
        flx_frequency_confirmation=True,
        max_full_files_per_date=3,
    )
    assert config.max_full_files_per_date == 3


# ---------------------------------------------------------------------------
# 3. Max full files total enforced in config
# ---------------------------------------------------------------------------

def test_max_full_files_total_config():
    config = _make_config(
        target_dates=["2026-05-27", "2026-05-28"],
        flx_frequency_confirmation=True,
        max_full_files_total=4,
    )
    assert config.max_full_files_total == 4


# ---------------------------------------------------------------------------
# 4. FLX frequency confirmation CLI args parsed
# ---------------------------------------------------------------------------

def test_cli_flx_frequency_confirmation_args():
    with patch(f"{MOD}._get_git_info", return_value=("sha", False, "main")):
        with patch(f"{MOD}._write_json_artifact"):
            ret = main([
                "--out-root", "/tmp/test_flx",
                "--dry-run",
                "--flx-frequency-confirmation",
                "--target-dates", "2026-05-27,2026-05-28",
                "--max-full-files-per-date", "3",
                "--max-full-files-total", "6",
            ])
    assert ret == 0


# ---------------------------------------------------------------------------
# 5. No flx found produces underpowered/no-overclaim status
# ---------------------------------------------------------------------------

def test_no_flx_found_no_overclaim(tmp_path):
    config = _make_config(
        target_dates=["2026-05-27"],
        flx_frequency_confirmation=True,
        max_full_files_total=1,
        out_root=tmp_path,
    )
    budget = RuntimeBudgetState.create(max_minutes=5)
    # Mock inventory to return empty keys
    mock_inv = MagicMock()
    mock_inv.source_accessible = True
    mock_inv.candidate_keys = []

    with patch(f"{MOD}.probe_s3_inventory", return_value=mock_inv):
        with patch(f"{MOD}._write_json_artifact"):
            with patch(f"{MOD}._write_jsonl_rows"):
                with patch(f"{MOD}._write_flx_frequency_report"):
                    status, reason, artifacts = run_flx_frequency_confirmation(
                        MagicMock(), config, budget, "test_run", tmp_path,
                    )
    # No files processed -> underpowered
    assert status == "FLX_SETORACLE_HUNT_UNDERPOWERED"


# ---------------------------------------------------------------------------
# 6. flx:TSLA found triggers early stop
# ---------------------------------------------------------------------------

def test_flx_tsla_found_triggers_early_stop(tmp_path):
    config = _make_config(
        target_dates=["2026-05-27", "2026-05-28"],
        flx_frequency_confirmation=True,
        max_full_files_per_date=1,
        max_full_files_total=4,
        stop_on_flx_found=True,
        out_root=tmp_path,
    )
    budget = RuntimeBudgetState.create(max_minutes=5)

    # First date: no flx, second file would be skipped
    hist_no_flx = _make_histogram(
        dex_counts={"cash": 100, "km": 50},
        oracle_pxs_by_dex={"cash": 100, "km": 50},
        markets_by_dex={"cash": ["cash:TSLA"], "km": ["km:AAPL"]},
        flx_seen=False,
        cash_tsla_found=True,
        km_seen=True,
    )
    hist_flx = _make_histogram(
        dex_counts={"cash": 100, "flx": 10},
        oracle_pxs_by_dex={"cash": 100, "flx": 10},
        markets_by_dex={"cash": ["cash:TSLA"], "flx": ["flx:TSLA"]},
        flx_seen=True,
        flx_tsla_found=True,
        cash_tsla_found=True,
    )

    call_count = [0]
    def mock_inventory(chokepoint, date_config, budget):
        inv = MagicMock()
        inv.source_accessible = True
        inv.candidate_keys = [f"replica_cmds/ts/20260527/100.lz4"]
        return inv

    def mock_histogram(chokepoint, file_config, sk, size, budget):
        idx = call_count[0]
        call_count[0] += 1
        if idx == 0:
            return hist_no_flx
        return hist_flx

    with patch(f"{MOD}.probe_s3_inventory", side_effect=mock_inventory):
        with patch(f"{MOD}.build_full_file_histogram", side_effect=mock_histogram):
            with patch(f"{MOD}._write_json_artifact"):
                with patch(f"{MOD}._write_jsonl_rows"):
                    with patch(f"{MOD}._write_flx_frequency_report"):
                        status, reason, artifacts = run_flx_frequency_confirmation(
                            MagicMock(), config, budget, "test_run", tmp_path,
                        )
    assert status == "FLX_SETORACLE_FOUND_BOUNDED_RECON"


# ---------------------------------------------------------------------------
# 7. flx:NVDA found triggers early stop
# ---------------------------------------------------------------------------

def test_flx_nvda_found_triggers_early_stop(tmp_path):
    config = _make_config(
        target_dates=["2026-05-27"],
        flx_frequency_confirmation=True,
        max_full_files_per_date=1,
        max_full_files_total=2,
        stop_on_flx_found=True,
        out_root=tmp_path,
    )
    budget = RuntimeBudgetState.create(max_minutes=5)

    hist_flx = _make_histogram(
        dex_counts={"cash": 100, "flx": 5},
        oracle_pxs_by_dex={"cash": 100, "flx": 5},
        markets_by_dex={"cash": ["cash:NVDA"], "flx": ["flx:NVDA"]},
        flx_seen=True,
        flx_nvda_found=True,
        cash_nvda_found=True,
    )

    def mock_inventory(chokepoint, date_config, budget):
        inv = MagicMock()
        inv.source_accessible = True
        inv.candidate_keys = ["replica_cmds/ts/20260527/100.lz4"]
        return inv

    with patch(f"{MOD}.probe_s3_inventory", side_effect=mock_inventory):
        with patch(f"{MOD}.build_full_file_histogram", return_value=hist_flx):
            with patch(f"{MOD}._write_json_artifact"):
                with patch(f"{MOD}._write_jsonl_rows"):
                    with patch(f"{MOD}._write_flx_frequency_report"):
                        status, reason, artifacts = run_flx_frequency_confirmation(
                            MagicMock(), config, budget, "test_run", tmp_path,
                        )
    assert status == "FLX_SETORACLE_FOUND_BOUNDED_RECON"


# ---------------------------------------------------------------------------
# 8. Persistent flx absence while cash/km/para active is classified
# ---------------------------------------------------------------------------

def test_persistent_flx_absence_classified(tmp_path):
    config = _make_config(
        target_dates=["2026-05-27", "2026-05-28", "2026-05-26", "2026-05-23"],
        flx_frequency_confirmation=True,
        max_full_files_per_date=1,
        max_full_files_total=4,
        stop_on_flx_found=True,
        out_root=tmp_path,
    )
    budget = RuntimeBudgetState.create(max_minutes=10)

    hist = _make_histogram(
        dex_counts={"cash": 500, "km": 200, "para": 50},
        oracle_pxs_by_dex={"cash": 500, "km": 200, "para": 50},
        markets_by_dex={"cash": ["cash:TSLA", "cash:NVDA"], "km": ["km:AAPL"], "para": ["para:AVGO"]},
        flx_seen=False,
        cash_tsla_found=True,
        cash_nvda_found=True,
        km_seen=True,
        perpdeploy_count=200,
        setoracle_count=200,
        oracle_pxs_count=1500,
        bytes_downloaded=900_000_000,
        records_decoded=10000,
    )

    call_idx = [0]
    def mock_inventory(chokepoint, date_config, budget):
        inv = MagicMock()
        inv.source_accessible = True
        inv.candidate_keys = [f"replica_cmds/ts/{date_config.target_date.replace('-','')}/100.lz4"]
        return inv

    def mock_histogram(chokepoint, file_config, sk, size, budget):
        call_idx[0] += 1
        return hist

    with patch(f"{MOD}.probe_s3_inventory", side_effect=mock_inventory):
        with patch(f"{MOD}.build_full_file_histogram", side_effect=mock_histogram):
            with patch(f"{MOD}._write_json_artifact") as mock_write:
                with patch(f"{MOD}._write_jsonl_rows"):
                    with patch(f"{MOD}._write_flx_frequency_report"):
                        status, reason, artifacts = run_flx_frequency_confirmation(
                            MagicMock(), config, budget, "test_run", tmp_path,
                        )
    # 4 files, all with cash/km/para active, flx absent -> stale oracle artifact
    assert status == "FLX_STALE_ORACLE_ARTIFACT_SUPPORTED"
    assert reason == "flx_persistently_absent_while_cash_km_para_active"


# ---------------------------------------------------------------------------
# 9. DEX histogram aggregates across files
# ---------------------------------------------------------------------------

def test_dex_histogram_aggregates_across_files(tmp_path):
    config = _make_config(
        target_dates=["2026-05-27"],
        flx_frequency_confirmation=True,
        max_full_files_per_date=2,
        max_full_files_total=2,
        stop_on_flx_found=True,
        out_root=tmp_path,
    )
    budget = RuntimeBudgetState.create(max_minutes=10)

    hist1 = _make_histogram(
        dex_counts={"cash": 100},
        oracle_pxs_by_dex={"cash": 100},
        markets_by_dex={"cash": ["cash:TSLA"]},
        cash_tsla_found=True,
    )
    hist2 = _make_histogram(
        dex_counts={"cash": 150, "km": 50},
        oracle_pxs_by_dex={"cash": 150, "km": 50},
        markets_by_dex={"cash": ["cash:TSLA"], "km": ["km:AAPL"]},
        cash_tsla_found=True,
        km_seen=True,
    )

    hists = [hist1, hist2]
    hist_idx = [0]
    def mock_inventory(chokepoint, date_config, budget):
        inv = MagicMock()
        inv.source_accessible = True
        inv.candidate_keys = ["k1.lz4", "k2.lz4"]
        return inv

    def mock_histogram(chokepoint, file_config, sk, size, budget):
        idx = hist_idx[0]
        hist_idx[0] += 1
        return hists[idx]

    summary_written = [None]
    def capture_write(path, obj):
        if str(path).endswith("flx_frequency_confirmation_summary.json"):
            summary_written[0] = obj

    with patch(f"{MOD}.probe_s3_inventory", side_effect=mock_inventory):
        with patch(f"{MOD}.build_full_file_histogram", side_effect=mock_histogram):
            with patch(f"{MOD}._write_json_artifact", side_effect=capture_write):
                with patch(f"{MOD}._write_jsonl_rows"):
                    with patch(f"{MOD}._write_flx_frequency_report"):
                        status, reason, artifacts = run_flx_frequency_confirmation(
                            MagicMock(), config, budget, "test_run", tmp_path,
                        )
    assert summary_written[0] is not None
    agg = summary_written[0]["aggregate_dex_distribution"]
    assert agg["cash"] == 250  # 100 + 150
    assert agg["km"] == 50


# ---------------------------------------------------------------------------
# 10. DEX histogram aggregates across dates
# ---------------------------------------------------------------------------

def test_dex_histogram_aggregates_across_dates(tmp_path):
    config = _make_config(
        target_dates=["2026-05-27", "2026-05-28"],
        flx_frequency_confirmation=True,
        max_full_files_per_date=1,
        max_full_files_total=2,
        stop_on_flx_found=True,
        out_root=tmp_path,
    )
    budget = RuntimeBudgetState.create(max_minutes=10)

    hist = _make_histogram(
        dex_counts={"cash": 100},
        oracle_pxs_by_dex={"cash": 100},
        markets_by_dex={"cash": ["cash:TSLA"]},
        cash_tsla_found=True,
    )

    def mock_inventory(chokepoint, date_config, budget):
        inv = MagicMock()
        inv.source_accessible = True
        inv.candidate_keys = [f"replica_cmds/ts/{date_config.target_date.replace('-','')}/100.lz4"]
        return inv

    summary_written = [None]
    def capture_write(path, obj):
        if str(path).endswith("flx_frequency_confirmation_summary.json"):
            summary_written[0] = obj

    with patch(f"{MOD}.probe_s3_inventory", side_effect=mock_inventory):
        with patch(f"{MOD}.build_full_file_histogram", return_value=hist):
            with patch(f"{MOD}._write_json_artifact", side_effect=capture_write):
                with patch(f"{MOD}._write_jsonl_rows"):
                    with patch(f"{MOD}._write_flx_frequency_report"):
                        status, reason, artifacts = run_flx_frequency_confirmation(
                            MagicMock(), config, budget, "test_run", tmp_path,
                        )
    assert summary_written[0] is not None
    by_date = summary_written[0]["dex_distribution_by_date"]
    assert "2026-05-27" in by_date
    assert "2026-05-28" in by_date
    assert by_date["2026-05-27"]["cash"] == 100
    assert by_date["2026-05-28"]["cash"] == 100


# ---------------------------------------------------------------------------
# 11. Update frequency estimates produced by DEX
# ---------------------------------------------------------------------------

def test_update_frequency_by_dex(tmp_path):
    config = _make_config(
        target_dates=["2026-05-27"],
        flx_frequency_confirmation=True,
        max_full_files_per_date=1,
        max_full_files_total=1,
        out_root=tmp_path,
    )
    budget = RuntimeBudgetState.create(max_minutes=5)

    hist = _make_histogram(
        dex_counts={"cash": 200, "km": 50},
        oracle_pxs_by_dex={"cash": 200, "km": 50},
        markets_by_dex={"cash": ["cash:TSLA"], "km": ["km:AAPL"]},
        cash_tsla_found=True,
        km_seen=True,
    )

    def mock_inventory(chokepoint, date_config, budget):
        inv = MagicMock()
        inv.source_accessible = True
        inv.candidate_keys = ["k1.lz4"]
        return inv

    summary_written = [None]
    def capture_write(path, obj):
        if str(path).endswith("flx_frequency_confirmation_summary.json"):
            summary_written[0] = obj

    with patch(f"{MOD}.probe_s3_inventory", side_effect=mock_inventory):
        with patch(f"{MOD}.build_full_file_histogram", return_value=hist):
            with patch(f"{MOD}._write_json_artifact", side_effect=capture_write):
                with patch(f"{MOD}._write_jsonl_rows"):
                    with patch(f"{MOD}._write_flx_frequency_report"):
                        status, reason, artifacts = run_flx_frequency_confirmation(
                            MagicMock(), config, budget, "test_run", tmp_path,
                        )
    assert summary_written[0] is not None
    freq = summary_written[0]["update_frequency_by_dex"]
    assert freq["cash"]["setoracle_payload_count"] == 200
    assert freq["cash"]["market_count"] == 1
    assert freq["km"]["setoracle_payload_count"] == 50


# ---------------------------------------------------------------------------
# 12. Progress artifact written per file
# ---------------------------------------------------------------------------

def test_progress_artifact_written_per_file(tmp_path):
    config = _make_config(
        target_dates=["2026-05-27"],
        flx_frequency_confirmation=True,
        max_full_files_per_date=1,
        max_full_files_total=1,
        out_root=tmp_path,
    )
    budget = RuntimeBudgetState.create(max_minutes=5)

    hist = _make_histogram(
        dex_counts={"cash": 100},
        oracle_pxs_by_dex={"cash": 100},
        markets_by_dex={"cash": ["cash:TSLA"]},
        cash_tsla_found=True,
    )

    def mock_inventory(chokepoint, date_config, budget):
        inv = MagicMock()
        inv.source_accessible = True
        inv.candidate_keys = ["k1.lz4"]
        return inv

    progress_written = [None]
    def capture_jsonl(path, rows):
        if str(path).endswith("flx_frequency_progress.jsonl"):
            progress_written[0] = rows

    with patch(f"{MOD}.probe_s3_inventory", side_effect=mock_inventory):
        with patch(f"{MOD}.build_full_file_histogram", return_value=hist):
            with patch(f"{MOD}._write_json_artifact"):
                with patch(f"{MOD}._write_jsonl_rows", side_effect=capture_jsonl):
                    with patch(f"{MOD}._write_flx_frequency_report"):
                        status, reason, artifacts = run_flx_frequency_confirmation(
                            MagicMock(), config, budget, "test_run", tmp_path,
                        )
    assert progress_written[0] is not None
    assert len(progress_written[0]) == 1
    row = progress_written[0][0]
    assert row["source_key"] == "k1.lz4"
    assert row["date"] == "2026-05-27"
    assert row["perpdeploy_count"] == 100


# ---------------------------------------------------------------------------
# 13. Closure recommendation written
# ---------------------------------------------------------------------------

def test_closure_recommendation_written(tmp_path):
    config = _make_config(
        target_dates=["2026-05-27"],
        flx_frequency_confirmation=True,
        max_full_files_per_date=1,
        max_full_files_total=1,
        out_root=tmp_path,
    )
    budget = RuntimeBudgetState.create(max_minutes=5)

    hist = _make_histogram(
        dex_counts={"cash": 100},
        oracle_pxs_by_dex={"cash": 100},
        markets_by_dex={"cash": ["cash:TSLA"]},
        cash_tsla_found=True,
    )

    def mock_inventory(chokepoint, date_config, budget):
        inv = MagicMock()
        inv.source_accessible = True
        inv.candidate_keys = ["k1.lz4"]
        return inv

    rec_written = [None]
    def capture_write(path, obj):
        if str(path).endswith("oracle_basis_closure_or_pivot_recommendation.json"):
            rec_written[0] = obj

    with patch(f"{MOD}.probe_s3_inventory", side_effect=mock_inventory):
        with patch(f"{MOD}.build_full_file_histogram", return_value=hist):
            with patch(f"{MOD}._write_json_artifact", side_effect=capture_write):
                with patch(f"{MOD}._write_jsonl_rows"):
                    with patch(f"{MOD}._write_flx_frequency_report"):
                        status, reason, artifacts = run_flx_frequency_confirmation(
                            MagicMock(), config, budget, "test_run", tmp_path,
                        )
    assert rec_written[0] is not None
    assert rec_written[0]["registry_mutation_performed"] is False
    assert "recommended_next_action" in rec_written[0]


# ---------------------------------------------------------------------------
# 14. No production subprocess, os.system, or eval
# ---------------------------------------------------------------------------

def test_no_subprocess_os_system_eval():
    import ast
    src_path = Path(__file__).resolve().parent.parent / "hip3_replica_cmds_setoracle_reconstruction_v0.py"
    src = src_path.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("system", "popen"):
                if isinstance(func.value, ast.Name) and func.value.id == "os":
                    pytest.fail(f"os.system/os.popen found at line {node.lineno}")
            if isinstance(func, ast.Name) and func.id in ("eval", "exec"):
                pytest.fail(f"{func.id}() found at line {node.lineno}")


# ---------------------------------------------------------------------------
# 15. Validation/backfill/SonarX/cross-DEX artifacts not written
# ---------------------------------------------------------------------------

def test_no_validation_backfill_sonarx_artifacts(tmp_path):
    config = _make_config(
        target_dates=["2026-05-27"],
        flx_frequency_confirmation=True,
        max_full_files_per_date=1,
        max_full_files_total=1,
        out_root=tmp_path,
    )
    budget = RuntimeBudgetState.create(max_minutes=5)

    hist = _make_histogram(
        dex_counts={"cash": 100},
        oracle_pxs_by_dex={"cash": 100},
        markets_by_dex={"cash": ["cash:TSLA"]},
        cash_tsla_found=True,
    )

    def mock_inventory(chokepoint, date_config, budget):
        inv = MagicMock()
        inv.source_accessible = True
        inv.candidate_keys = ["k1.lz4"]
        return inv

    written_names = []
    def capture_write(path, obj):
        written_names.append(str(path).split("/")[-1])

    with patch(f"{MOD}.probe_s3_inventory", side_effect=mock_inventory):
        with patch(f"{MOD}.build_full_file_histogram", return_value=hist):
            with patch(f"{MOD}._write_json_artifact", side_effect=capture_write):
                with patch(f"{MOD}._write_jsonl_rows"):
                    with patch(f"{MOD}._write_flx_frequency_report"):
                        status, reason, artifacts = run_flx_frequency_confirmation(
                            MagicMock(), config, budget, "test_run", tmp_path,
                        )
    for name in written_names:
        assert "sonarx" not in name.lower()
        assert "backfill" not in name.lower()
        assert "overlap_validation" not in name.lower()


# ---------------------------------------------------------------------------
# 16. flx frequency confirmation dry-run works
# ---------------------------------------------------------------------------

def test_flx_frequency_confirmation_dry_run(tmp_path):
    with patch(f"{MOD}._get_git_info", return_value=("sha", False, "main")):
        with patch(f"{MOD}._write_json_artifact"):
            ret = main([
                "--out-root", str(tmp_path),
                "--dry-run",
                "--flx-frequency-confirmation",
                "--target-dates", "2026-05-27",
            ])
    assert ret == 0


# ---------------------------------------------------------------------------
# 17. Cross-DEX pivot candidate written when flx absent
# ---------------------------------------------------------------------------

def test_cross_dex_pivot_candidate_when_flx_absent(tmp_path):
    config = _make_config(
        target_dates=["2026-05-27", "2026-05-28"],
        flx_frequency_confirmation=True,
        max_full_files_per_date=1,
        max_full_files_total=2,
        out_root=tmp_path,
    )
    budget = RuntimeBudgetState.create(max_minutes=5)

    hist = _make_histogram(
        dex_counts={"cash": 100, "km": 50},
        oracle_pxs_by_dex={"cash": 100, "km": 50},
        markets_by_dex={"cash": ["cash:TSLA"], "km": ["km:AAPL"]},
        cash_tsla_found=True,
        km_seen=True,
    )

    def mock_inventory(chokepoint, date_config, budget):
        inv = MagicMock()
        inv.source_accessible = True
        inv.candidate_keys = [f"replica_cmds/ts/{date_config.target_date.replace('-','')}/100.lz4"]
        return inv

    rec_written = [None]
    def capture_write(path, obj):
        if str(path).endswith("oracle_basis_closure_or_pivot_recommendation.json"):
            rec_written[0] = obj

    with patch(f"{MOD}.probe_s3_inventory", side_effect=mock_inventory):
        with patch(f"{MOD}.build_full_file_histogram", return_value=hist):
            with patch(f"{MOD}._write_json_artifact", side_effect=capture_write):
                with patch(f"{MOD}._write_jsonl_rows"):
                    with patch(f"{MOD}._write_flx_frequency_report"):
                        status, reason, artifacts = run_flx_frequency_confirmation(
                            MagicMock(), config, budget, "test_run", tmp_path,
                        )
    assert rec_written[0] is not None
    assert rec_written[0]["cross_dex_pivot_candidate"] is not None
    assert "cash:TSLA_vs_km:TSLA" in rec_written[0]["cross_dex_pivot_candidate"]
