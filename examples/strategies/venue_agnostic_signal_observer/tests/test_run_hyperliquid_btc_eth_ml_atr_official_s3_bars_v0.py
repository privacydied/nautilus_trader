"""
Tests for run_hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 — CLI runner.

NOT live trading. NOT exchange-connected. S3 archive only.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 import _parse_args


class TestCLIHelp:
    def test_help_works(self):
        with pytest.raises(SystemExit) as exc:
            _parse_args(["--help"])
        assert exc.value.code == 0


class TestCLIModeValidation:
    def test_requires_exactly_one_mode(self):
        with pytest.raises(SystemExit) as exc:
            _parse_args([])
        assert exc.value.code != 0

    def test_plan_only_accepted(self):
        args = _parse_args(["--plan-only"])
        assert args.plan_only is True

    def test_dry_run_accepted(self):
        args = _parse_args(["--dry-run"])
        assert args.dry_run is True


class TestForbiddenStrings:
    def test_no_live_in_runner(self):
        import run_hyperliquid_btc_eth_ml_atr_official_s3_bars_v0 as mod
        source = open(mod.__file__).read()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for forbidden in ["submit_order", "place_order", "cancel_order", "private_key"]:
                assert forbidden not in stripped
