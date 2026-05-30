"""Tests for the run entry point of the SonarX HIP‑3 L2 Summary Coverage Probe."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_l2_summary_coverage_probe_v0 import (
    STATUSES,
    main as probe_main,
)


# ===================================================================
# 1. Dry‑run performs no S3 calls
# ===================================================================

class TestDryRun:
    def test_dry_run_no_s3_calls(self, tmp_path: Path):
        with patch(
            "examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_l2_summary_coverage_probe_v0.s3_client"
        ) as mock_s3:
            args = [
                "--out-root", str(tmp_path),
                "--markets", "xyz:TSLA",
                "--dry-run",
            ]
            exit_code = probe_main(args)
            assert exit_code == 0
            mock_s3.assert_not_called()
            # dry_run_preview.json should exist
            preview_files = list(tmp_path.rglob("dry_run_preview.json"))
            assert len(preview_files) == 1
            preview = json.loads(preview_files[0].read_text())
            assert preview["status"] == "SONARX_L2_SUMMARY_READY"


# ===================================================================
# 2. CLI args are forwarded correctly
# ===================================================================

class TestCliArgs:
    def test_all_args_accepted(self, tmp_path: Path):
        args = [
            "--out-root", str(tmp_path),
            "--markets", "xyz:TSLA,flx:TSLA,km:TSLA,cash:TSLA,xyz:AAPL,km:AAPL,xyz:MSFT,cash:MSFT,xyz:NVDA,flx:NVDA,km:NVDA,cash:NVDA",
            "--max-markets", "12",
            "--max-partitions-per-market", "3",
            "--max-files-per-partition", "3",
            "--max-sample-files-total", "24",
            "--download-budget-bytes", "500000000",
            "--allow-s3-archive-read",
            "--dry-run",
        ]
        exit_code = probe_main(args)
        assert exit_code == 0
        preview_files = list(tmp_path.rglob("dry_run_preview.json"))
        assert len(preview_files) == 1
        preview = json.loads(preview_files[0].read_text())
        assert preview["command_args"]["max_markets"] == 12
        assert preview["command_args"]["allow_s3_archive_read"] is True


# ===================================================================
# 3. Forbidden statuses absent from all outputs
# ===================================================================

class TestForbiddenStatuses:
    FORBIDDEN = {
        "REJECTED", "PROFITABLE", "ALPHA_FOUND", "TRADE_READY",
        "EXECUTION_READY", "LIVE_READY", "READY_FOR_PHASE_0",
        "CANDIDATE_FOR_LIVE", "PAPER_STRATEGY_PROMOTED",
        "PROMOTION_AUTHORIZED", "EDGE_CONFIRMED",
    }

    def test_no_forbidden_in_statues(self):
        assert not STATUSES.intersection(self.FORBIDDEN)


# ===================================================================
# 4. No PnL / returns / trade fields in dry‑run output
# ===================================================================

class TestNoPnlFields:
    def test_dry_run_has_no_pnl(self, tmp_path: Path):
        args = [
            "--out-root", str(tmp_path),
            "--markets", "xyz:TSLA",
            "--dry-run",
        ]
        probe_main(args)
        preview_files = list(tmp_path.rglob("dry_run_preview.json"))
        data = json.loads(preview_files[0].read_text())
        assert "pnl" not in data
        assert "profit_factor" not in data
        assert "win_rate" not in data
        assert "sharpe" not in data


# ===================================================================
# 5. No subprocess/os.system/eval in production code
# ===================================================================

class TestNoForbiddenImports:
    def test_no_os_system_eval_in_probe(self):
        probe_path = Path(__file__).resolve().parents[1] / "hip3_sonarx_l2_summary_coverage_probe_v0.py"
        src = probe_path.read_text()
        assert "os.system(" not in src
        assert "eval(" not in src


# ===================================================================
# 6. Safety grep
# ===================================================================

class TestSafetyGrep:
    FORBIDDEN_TERMS = [
        "submit_order", "place_order", "cancel_order",
        "private_key", "api_key", "secret_key",
        "wallet", "live_execute",
        "paper_broker", "broker_connect",
        "account_value", "withdraw", "transfer",
    ]

    def test_no_order_terms(self):
        probe_path = Path(__file__).resolve().parents[1] / "hip3_sonarx_l2_summary_coverage_probe_v0.py"
        src = probe_path.read_text()
        for term in self.FORBIDDEN_TERMS:
            assert term not in src, f"Found forbidden term: {term}"
