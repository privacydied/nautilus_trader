"""
Tests for run_hyperliquid_btc_eth_ml_atr_data_prep_v0 — CLI runner.

NOT live trading. NOT network-connected. Local files only.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_hyperliquid_btc_eth_ml_atr_data_prep_v0 import _parse_args


class TestCLIHelp:
    def test_help_works(self):
        with pytest.raises(SystemExit) as exc:
            _parse_args(["--help"])
        assert exc.value.code == 0


class TestCLIDefaults:
    def test_default_symbols(self):
        args = _parse_args([])
        assert set(args.symbol) == {"BTC", "ETH"}

    def test_custom_symbols(self):
        args = _parse_args(["--symbol", "BTC"])
        assert args.symbol == ["BTC"]


class TestForbiddenStrings:
    def test_no_network_in_runner(self):
        import run_hyperliquid_btc_eth_ml_atr_data_prep_v0 as mod
        source = open(mod.__file__).read()
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for forbidden in ["requests.get", "requests.post", "urllib", "httpx", "aiohttp"]:
                assert forbidden not in stripped
