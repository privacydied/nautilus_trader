"""
Tests for hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 — S3 bars module.

NOT live trading. NOT exchange-connected. S3 archive only.
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 import (
    S3BarsConfig,
    S3BarsRunSummary,
    check_required_tools,
    discover_s3_layout,
    check_coverage_against_v0_requirements,
    run_s3_bars,
)


# ---------------------------------------------------------------------------
# 1. Module Imports
# ---------------------------------------------------------------------------
class TestModuleImports:
    def test_import_without_nautilus(self):
        import importlib
        mod = importlib.import_module("hyperliquid_btc_eth_ml_atr_official_s3_bars_v0")
        assert hasattr(mod, "run_s3_bars")


# ---------------------------------------------------------------------------
# 2. Tool Checks
# ---------------------------------------------------------------------------
class TestToolChecks:
    def test_aws_cli_check(self):
        tools = check_required_tools()
        assert "aws_cli" in tools

    def test_lz4_check(self):
        tools = check_required_tools()
        assert "lz4" in tools


# ---------------------------------------------------------------------------
# 3. S3 Listing (Mocked)
# ---------------------------------------------------------------------------
class TestS3Listing:
    def test_list_objects_handles_empty(self):
        with patch("hyperliquid_btc_eth_ml_atr_official_s3_bars_v0._aws_cmd") as mock:
            mock.return_value = MagicMock(returncode=1, stderr="AccessDenied")
            from hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 import list_s3_objects
            objects, err = list_s3_objects("s3://test/", request_payer=True)
            assert objects == []
            assert "AccessDenied" in err

    def test_list_objects_parses_output(self):
        mock_output = "2025-07-27 13:02:29   21349239 hourly/20250727/10.lz4\n"
        with patch("hyperliquid_btc_eth_ml_atr_official_s3_bars_v0._aws_cmd") as mock:
            mock.return_value = MagicMock(returncode=0, stdout=mock_output, stderr="")
            from hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 import list_s3_objects
            objects, err = list_s3_objects("s3://test/", request_payer=True)
            assert len(objects) == 1
            assert objects[0].size == 21349239
            assert "10.lz4" in objects[0].key


# ---------------------------------------------------------------------------
# 4. Coverage Check
# ---------------------------------------------------------------------------
class TestCoverageCheck:
    def test_insufficient_coverage_detected(self):
        from hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 import S3DownloadPlan
        plan = S3DownloadPlan(
            status="S3_BARS_V0_PLAN_READY",
            objects=[],
            total_bytes=0,
            total_gb=0.0,
        )
        # Simulate objects from 2025 only
        plan.objects = [
            type("Obj", (), {"key": "s3://test/20250727/10.lz4", "size": 1000})(),
        ]
        config = S3BarsConfig()
        plan = check_coverage_against_v0_requirements(plan, config)
        assert plan.status == "S3_BARS_V0_BLOCKED_INSUFFICIENT_COVERAGE"
        assert "2025" in plan.reason


# ---------------------------------------------------------------------------
# 5. Full Pipeline (Mocked)
# ---------------------------------------------------------------------------
class TestPipeline:
    def test_dry_run_blocks_without_tools(self):
        with patch("hyperliquid_btc_eth_ml_atr_official_s3_bars_v0.check_required_tools") as mock:
            mock.return_value = {"aws_cli": False, "lz4": True}
            cfg = S3BarsConfig(dry_run=True)
            summary = run_s3_bars(cfg)
            assert summary.status == "S3_BARS_V0_BLOCKED_AWS_CLI_MISSING"

    def test_dry_run_blocks_without_lz4(self):
        with patch("hyperliquid_btc_eth_ml_atr_official_s3_bars_v0.check_required_tools") as mock:
            mock.return_value = {"aws_cli": True, "lz4": False}
            cfg = S3BarsConfig(dry_run=True)
            summary = run_s3_bars(cfg)
            assert summary.status == "S3_BARS_V0_BLOCKED_LZ4_MISSING"


# ---------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------
class TestCLI:
    def test_help_works(self):
        from run_hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 import _parse_args
        with pytest.raises(SystemExit) as exc:
            _parse_args(["--help"])
        assert exc.value.code == 0

    def test_requires_mode(self):
        from run_hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 import _parse_args
        with pytest.raises(SystemExit) as exc:
            _parse_args([])
        assert exc.value.code != 0

    def test_unknown_symbol_fails(self):
        from run_hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 import _parse_args
        with pytest.raises(SystemExit) as exc:
            _parse_args(["--symbol", "LINK", "--plan-only"])
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# 7. Forbidden Strings
# ---------------------------------------------------------------------------
class TestForbiddenStrings:
    def test_no_live_order_strings(self):
        import hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 as mod
        source = open(mod.__file__).read()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for forbidden in ["submit_order", "place_order", "cancel_order", "private_key"]:
                assert forbidden not in stripped
