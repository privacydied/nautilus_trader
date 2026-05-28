"""Tests for the run entry point of the SonarX HIP-3 TradFi L2 Residual Phase -1 Scout."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from examples.strategies.venue_agnostic_signal_observer.hip3_sonarx_tradfi_l2_residual_phase_minus1_v0 import (
    STATUSES,
    FORBIDDEN_STATUSES,
    main as scout_main,
)


# ===================================================================
# 1. Dry-run performs no S3/API calls
# ===================================================================

class TestDryRun:
    def test_dry_run_no_s3_calls(self, tmp_path: Path):
        with patch(
            "examples.strategies.venue_agnostic_signal_observer"
            ".hip3_sonarx_tradfi_l2_residual_phase_minus1_v0.NetworkChokepoint"
        ) as MockCP:
            mock_cp = MockCP.return_value
            args = [
                "--out-root", str(tmp_path),
                "--markets", "xyz:TSLA",
                "--dry-run",
            ]
            exit_code = scout_main(args)
            assert exit_code == 0
            mock_cp.s3_list_prefix.assert_not_called()
            preview_files = list(tmp_path.rglob("dry_run_preview.json"))
            assert len(preview_files) == 1
            preview = json.loads(preview_files[0].read_text())
            assert preview["status"] == "SONARX_PHASE_MINUS1_READY"


# ===================================================================
# 2. CLI args forwarded correctly
# ===================================================================

class TestCliArgs:
    def test_all_args_accepted(self, tmp_path: Path):
        args = [
            "--out-root", str(tmp_path),
            "--markets", "xyz:TSLA,flx:TSLA,km:TSLA,cash:TSLA,xyz:AAPL,km:AAPL,xyz:MSFT,cash:MSFT,xyz:NVDA,flx:NVDA,km:NVDA,cash:NVDA",
            "--sample-days", "30",
            "--sample-mode", "stratified",
            "--max-markets", "12",
            "--max-files-per-market", "500",
            "--download-budget-bytes", "2000000000",
            "--allow-s3-archive-read",
            "--allow-network-public",
            "--enable-candle-join",
            "--enable-anchors",
            "--anchor-source", "yahoo",
            "--dry-run",
        ]
        exit_code = scout_main(args)
        assert exit_code == 0
        preview_files = list(tmp_path.rglob("dry_run_preview.json"))
        assert len(preview_files) == 1
        preview = json.loads(preview_files[0].read_text())
        assert preview["command_args"]["max_markets"] == 12
        assert preview["command_args"]["allow_s3_archive_read"] is True
        assert preview["command_args"]["allow_network_public"] is True


# ===================================================================
# 3. Forbidden statuses absent from all outputs
# ===================================================================

class TestForbiddenStatuses:
    def test_no_forbidden_in_statuses(self):
        assert not STATUSES.intersection(FORBIDDEN_STATUSES)


# ===================================================================
# 4. No PnL / returns / trade fields in dry-run output
# ===================================================================

class TestNoPnlFields:
    def test_dry_run_has_no_pnl(self, tmp_path: Path):
        args = ["--out-root", str(tmp_path), "--markets", "xyz:TSLA", "--dry-run"]
        scout_main(args)
        preview_files = list(tmp_path.rglob("dry_run_preview.json"))
        data = json.loads(preview_files[0].read_text())
        # Check JSON keys, not stringified path (tmp_path contains test name)
        flat_keys = ",".join(data.keys()).lower()
        for term in ["profit_factor", "win_rate", "sharpe", "entry_signal", "exit_signal"]:
            assert term not in flat_keys, f"Found forbidden key in dry-run: {term}"
        # pnl as a standalone key (not substring of run_id or path)
        assert "pnl" not in data, "Found forbidden 'pnl' key in dry-run"


# ===================================================================
# 5. No subprocess/os.system/eval in production code
# ===================================================================

class TestNoForbiddenImports:
    def test_no_subprocess_in_scout(self):
        probe_path = Path(__file__).resolve().parents[1] / "hip3_sonarx_tradfi_l2_residual_phase_minus1_v0.py"
        src = probe_path.read_text(encoding="utf-8")
        assert "import subprocess" not in src
        assert "subprocess." not in src
        assert "os.system(" not in src
        assert "eval(" not in src


# ===================================================================
# 6. Safety grep
# ===================================================================

class TestSafetyGrep:
    FORBIDDEN_TERMS = [
        "submit_order", "place_order", "cancel_order",
        "private_key", "api_key", "secret_key",
        "wallet", "live_execute", "paper_broker", "broker_connect",
        "account_value", "withdraw", "transfer",
    ]

    def test_no_order_terms(self):
        probe_path = Path(__file__).resolve().parents[1] / "hip3_sonarx_tradfi_l2_residual_phase_minus1_v0.py"
        src = probe_path.read_text(encoding="utf-8")
        for term in self.FORBIDDEN_TERMS:
            assert term not in src, f"Found forbidden term: {term}"
